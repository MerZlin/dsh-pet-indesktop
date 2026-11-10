# -*- coding: utf-8 -*-
"""AI 对话全局记忆（档 A 手动钉 + v2a 四层）— ChatMemory。

设计稿：`.scratch/feature-modules/CHAT-GLOBAL-MEMORY.md`（档 A 先行，零 LLM 成本）
与 `.scratch/memory-v2/SPEC-v2a.md`（四层 / 按需命中）。

- 存储文件：`<config_dir>/sessions[-实例]/<character_id>/memory.json`（**按角色**——
  不同角色记不同人格事实；多实例经 SessionStore 的实例后缀目录天然隔离）；
- 结构：`{version, entries: [{id, text, source, layer, keywords, created_at,
  updated_at, hits}]}`；老条目缺 `layer` 读取即 `fact`、缺 `keywords` 现场 bigram
  兜底（**不迁移、不改写老文件**）；新条目在头部；上限 100 条 / 4000 字符，
  超限按 hits 少 + 最老淘汰；
- 四层：`user` 恒定注入；`fact/lesson/topic` 第二轮起按需命中；topic 连续 14 天
  未命中自动归档（`archived`，不参与命中，UI 可恢复）；
- 两个入口：对话里「记住[偏好/教训/话题]：……」（指令解析，
  `parse_remember_command`）与消息气泡「钉为记忆」；重复钉同一文本按文本去重
  （updated_at 刷新 + hits+1）；
- 注入：`injection_text` 只返回 user 快照（硬字符上限，不再暂存条目）；
  聊天窗改用 `memory_payload` 生成的显式 `MemoryPayload`（user 快照 + 全量
  条目 + 会话命名空间），`PromptBuilder.build_messages` 用真实用户消息补上
  fact/lesson/topic 命中段。旧调用方仍可用 `injection_text` / `memory_text`。
  `chat_memory_enabled` 关 = 注入段为空，零成本。
- 命中记录：`note_recalled` 只更新进程内 `last_hit_at` 缓存并入队，磁盘写
  由单飞 daemon 线程完成（同一 topic ≥60s 去抖），GUI 线程零磁盘写。

存储纪律照抄 `pet/proactive_memory.py`（三层锁，直接复用它的进程内锁表与
锁文件约定，不新造轮子）：
  ① 跨进程：旁路 `<name>.lock` 的 QLockFile，包住 add/remove/clear 的 读→改→写；
  ② 同进程写者：按路径共享的可重入宽锁（`proactive_memory._lock_for`）；
  ③ 本地文件 I/O：按路径共享的窄锁（`proactive_memory._io_lock_for`），只在真正
     碰文件的那一瞬间持有，退避睡眠不占锁；读路径只拿 ③。

「读不到」与「没有条目」同样是两个事实（`_read_entries_strict`）：纯读（load）
可降级空列表展示，但降级结果绝不进读改写链——add/remove 在"读失败"时放弃本次
修改，而不是把只含新条目的快照盖到盘上旧记忆头上。

为什么需要跨进程锁（与 proactive_memory 同款的真实产品路径）：默认「独立设置页」
是另一个进程（`python -m pet --settings`），其「记忆管理」的清空/删除与主进程
聊天窗的「记住：……」/「钉为记忆」写同一个 memory.json；没有跨进程互斥时两边
各自 读→改→写，后写的把前一条挤掉，clear 也可能被 in-flight 的旧快照复活。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QLockFile

from ..config import _is_replace_conflict, atomic_replace_with_retry
from ..proactive.memory import (
    DEFAULT_LOCK_TIMEOUT_MS,
    _io_lock_for,
    _lock_for,
    _resolved,
    lock_path_for,
)
from .memory_recall import (
    DEFAULT_LAYER,
    LAYERS,
    MAX_KEYWORDS,
    USER_CHAR_LIMIT,
    archive_stale_topics,
    extract_keywords,
    is_archived,
    layer_of,
    normalize_layer,
    recall,
    render_user_snapshot,
)
from .session_store import SessionStore

log = logging.getLogger(__name__)

#: 条目数 / 总字符 / 单条字符上限（设计稿 §3：超限按 hits 少 + 最老淘汰）。
MAX_ENTRIES = 100
MAX_TOTAL_CHARS = 4000
MAX_ENTRY_CHARS = 500
#: 注入段的硬字符上限（设计稿 §4：条列，带字符上限，超出按 hits/时间截）。
INJECT_CHAR_LIMIT = 1200

#: 手动钉四前缀（中英文冒号都认；长前缀在前，避免被「记住：」抢匹配）。
_REMEMBER_LAYERS = (
    ("记住偏好：", "user"),
    ("记住偏好:", "user"),
    ("记住教训：", "lesson"),
    ("记住教训:", "lesson"),
    ("记住话题：", "topic"),
    ("记住话题:", "topic"),
    ("记住：", "fact"),
    ("记住:", "fact"),
)
REMEMBER_PREFIXES = tuple(prefix for prefix, _layer in _REMEMBER_LAYERS)


class RememberCommand(str):
    """「记住…：」解析结果：字符串本体 + ``layer``，兼容只读正文的老调用方。"""

    layer: str

    def __new__(cls, value: str, layer: str) -> RememberCommand:
        instance = super().__new__(cls, value)
        instance.layer = normalize_layer(layer)
        return instance

#: 注入段头：说明这是用户事实清单，防止模型逐字复述。
_INJECT_HEADER = (
    "【关于用户的记忆】以下是你记住的关于用户的事，回答时自然地参考这些偏好与事实；"
    "不要在回复里逐字复述这份清单。"
)

#: 本地 I/O 重试口径同 proactive_memory：只重试瞬时共享冲突（WinError 5/32、
#: EACCES、EPERM），其它 OSError 立即如实上报。读重试少（GUI 线程上的注入读），
#: 删文件重试多（撞上读者手柄只能等它关掉）。
_RETRY_BASE_DELAY = 0.02
_RETRY_MAX_DELAY = 0.2
_READ_ATTEMPTS = 3
_UNLINK_ATTEMPTS = 5
_REPLACE_ATTEMPTS = 5

#: 命中记录落盘去抖：同一 topic 两次写盘至少隔 60s（内存缓存立即可见）。
HIT_WRITE_DEBOUNCE_SECONDS = 60.0
#: 命中写盘失败后的退避：1s→2s→4s…上限 30s（锁超时 / 磁盘错误时立即重试会
#: 烧满 CPU）；连续失败达到 MAX_HIT_WRITE_ATTEMPTS 次后放弃本批并只记一条 warning。
_FAIL_RETRY_SECONDS = 1.0
_FAIL_RETRY_MAX_SECONDS = 30.0
MAX_HIT_WRITE_ATTEMPTS = 5
#: v2f N3：自动来源要新建条目时，与已归档条目的关键词 Jaccard 达到该比例，
#: 视为同义改写（换说法绕过归档），丢弃这次新增。
ARCHIVE_DUP_RATIO = 0.6
#: 归档巡检最小间隔：无 topic 命中的轮次只做内存判断，不每轮读盘。
ARCHIVE_SWEEP_SECONDS = 60.0
#: 进程内 last_hit_at 缓存上限（按条目 id，超限丢最老的写入记录）。
_HIT_CACHE_LIMIT = 512


def memory_path(config_dir, instance_id: str, character_id: str) -> Path:
    """记忆文件路径：`<config_dir>/sessions[-实例]/<character_id>/memory.json`。

    与会话文件同目录（SessionStore 的实例后缀规则原样继承），不同角色、不同
    实例各记各的。
    """
    root = SessionStore(config_dir, instance_id).root
    return root / str(character_id) / "memory.json"


def parse_remember_command(text: str) -> str | None:
    """「记住[偏好/教训/话题]：…」开头 → 指令正文；非指令 → None。

    返回的是 ``RememberCommand``（str 子类，额外带 ``layer``）：老调用方继续把
    它当正文传给 ``ChatMemory.add``，层信息不会在调用链上丢失。空串与 None 必须
    区分：空串是"指令到了但没说要记什么"，None 是"这根本不是记忆指令"。
    """
    stripped = str(text or "").strip()
    for prefix, layer in _REMEMBER_LAYERS:
        if stripped.startswith(prefix):
            return RememberCommand(stripped[len(prefix):].strip(), layer)
    return None


def render_injection(
    entries: list[dict[str, Any]],
    char_limit: int = INJECT_CHAR_LIMIT,
    *,
    include_archived: bool = False,
) -> str:
    """把记忆条目渲染成 system 末尾的「关于用户的记忆」段；无条目 → 空串。

    选取优先级：hits 高 → 最近更新；段长有硬上限（`char_limit`），第一条放不下的
    条目处截断（"超出按 hits/时间截"，不跳着拣短条目——顺序就是优先级）。
    v2b：默认**排除归档 topic**（后台提炼不再把归档条目喂回模型而静默复活）；
    显式 ``include_archived=True`` 才包含。
    """
    if not entries:
        return ""
    limit = max(200, int(char_limit))
    moment = datetime.now().astimezone()

    def _key(entry: dict[str, Any]) -> tuple:
        return (-int(entry.get("hits", 0) or 0), -_iso_sort_key(entry.get("updated_at")))

    section = _INJECT_HEADER
    for entry in sorted(entries, key=_key):
        if not include_archived and is_archived(entry, moment):
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        candidate = f"{section}\n- {text}"
        if len(candidate) > limit:
            break
        section = candidate
    return section


def _iso_sort_key(value) -> float:
    """ISO 时间戳 → 可比较的时间值；解析失败按 0（最老）。"""
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _clean_keyword_list(value: Any) -> list[str]:
    """ops / 条目里的 keywords → 去重、小写、≤MAX_KEYWORDS 的字符串列表。"""
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        token = str(item or "").strip().lower()
        if token and token not in cleaned:
            cleaned.append(token)
        if len(cleaned) >= MAX_KEYWORDS:
            break
    return cleaned


def _keyword_jaccard(left: str, right: str) -> float:
    """两条文本的关键词 Jaccard（与 ``extract_keywords`` 同一口径）。"""
    left_keywords = set(extract_keywords(left))
    right_keywords = set(extract_keywords(right))
    if not left_keywords or not right_keywords:
        return 0.0
    return len(left_keywords & right_keywords) / len(left_keywords | right_keywords)


def _duplicates_archived(
    text: str,
    entries: list[dict[str, Any]],
    moment: datetime,
) -> bool:
    """自动来源新增前检查：是否与某条已归档记忆是同义改写（v2f N3）。"""
    return any(
        is_archived(entry, moment)
        and _keyword_jaccard(text, str(entry.get("text", ""))) >= ARCHIVE_DUP_RATIO
        for entry in entries
    )


def _normalized_entry(
    entry: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """纯读副本：补 layer / keywords，并把超期 topic 标成 archived（不写盘）。"""
    item = dict(entry)
    item["layer"] = layer_of(item)
    keywords = _clean_keyword_list(item.get("keywords"))
    item["keywords"] = keywords or extract_keywords(item.get("text", ""))
    if item["layer"] == "topic" and not item.get("archived") and is_archived(item, now):
        item["archived"] = True
    return item


def injection_text(
    enabled: bool,
    config_dir,
    instance_id: str,
    character_id: str,
    *,
    user_text: str | None = None,
    turn_index: int = 2,
    seen: frozenset[str] | set[str] | None = None,
    now: datetime | None = None,
) -> str:
    """生成注入段的总入口：`chat_memory_enabled` 关 / 无记忆 / 读不到 → 空串。

    默认只返回 user 快照；**不再暂存条目**（v2b：暂存会把 A 会话的记忆串进
    同一线程上 B 会话的注入）。需要按需命中的调用方改用
    ``memory_payload`` + ``build_messages(memory_payload=...)``。
    显式传 ``user_text`` 时直接返回「user 快照 + 命中」两段，便于测试/其它
    调用方一次拿全（旧签名继续可用）。

    纯读（只拿本地 I/O 窄锁，不碰写者的宽锁与跨进程锁）：读者要么看到旧快照、
    要么看到新快照，永远不会看到半截文件（写入走原子替换）。
    """
    if not enabled:
        return ""
    memory = ChatMemory(memory_path(config_dir, instance_id, character_id))
    entries = memory.load()
    user_section = render_user_snapshot(entries, now=now)
    if user_text is None:
        return user_section
    result = recall(entries, str(user_text), seen=seen, now=now, turn_index=turn_index)
    return "\n\n".join(part for part in (user_section, result.text) if part)


@dataclass
class MemoryPayload:
    """一轮对话要注入的记忆快照（显式传参，替代已删除的线程暂存）。

    ``text`` 是 system 末尾的 user 层快照；``entries`` 是全量条目（按需命中用）；
    ``session_key`` 是 ``_SEEN`` 的命名空间（记忆文件路径）；``note_hits`` 是
    命中回写回调（通常是 ``ChatMemory.note_recalled``）。
    """

    text: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)
    session_key: str = ""
    note_hits: Callable[[list[str]], Any] | None = None


def memory_payload(
    memory: "ChatMemory",
    enabled: bool,
    *,
    char_limit: int = USER_CHAR_LIMIT,
) -> MemoryPayload:
    """纯读地生成一轮注入快照：不暂存、不写盘。

    ``enabled=False`` 或没有条目时返回空 payload；命中回写回调只在本进程的
    内存里记录（真正的落盘由 ``note_recalled`` 的 daemon 线程去做）。
    """
    if not enabled:
        return MemoryPayload()
    entries = memory.load()
    if not entries:
        return MemoryPayload(session_key=str(memory.path))
    return MemoryPayload(
        text=render_user_snapshot(entries, char_limit=char_limit),
        entries=entries,
        session_key=str(memory.path),
        note_hits=memory.note_recalled,
    )


def _fail_retry_delay(failures: int) -> float:
    """连续失败次数 → 退避秒数（1s→2s→4s…，上限 30s）。"""
    exponent = max(1, int(failures)) - 1
    return min(_FAIL_RETRY_SECONDS * (2 ** exponent), _FAIL_RETRY_MAX_SECONDS)


class _HitWriteQueue:
    """按记忆文件路径共享的命中写盘队列：GUI 线程只入队，daemon 线程单飞落盘。

    合并去重：同一 topic 在排队期间被多次命中只保留最新时间；同一 topic 两次
    落盘至少间隔 ``HIT_WRITE_DEBOUNCE_SECONDS``（内存缓存立即更新）。
    归档巡检（超期 topic 写 ``archived=True``）由 ``sweep_archive`` / 低频巡检
    触发，是一次性变更，不受命中去抖限制。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cond = threading.Condition()
        self._pending: dict[str, datetime] = {}
        self._cache: dict[str, datetime] = {}
        self._last_write: dict[str, float] = {}
        self._last_sweep: float | None = None
        self._sweep_moment: datetime | None = None
        self._force = False
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._clock: Callable[[], datetime] = lambda: datetime.now().astimezone()

    # ------------------------------------------------------------ 入队 / 查询
    def record(
        self,
        ids: set[str],
        moment: datetime,
        clock: Callable[[], datetime],
    ) -> None:
        with self._cond:
            self._clock = clock
            for entry_id in ids:
                self._pending[entry_id] = moment
                self._cache.pop(entry_id, None)
                self._cache[entry_id] = moment
            while len(self._cache) > _HIT_CACHE_LIMIT:
                self._cache.pop(next(iter(self._cache)))
            # 首轮巡检立即到期（不依赖进程 uptime 是否已过 60s）；之后低频。
            if (self._last_sweep is None
                    or time.monotonic() - self._last_sweep >= ARCHIVE_SWEEP_SECONDS):
                self._sweep_moment = moment
            if not self._pending and self._sweep_moment is None:
                return  # 空命中且不在巡检窗口：不启动线程
            self._ensure_thread_locked()
            self._cond.notify_all()

    def request_sweep(self, moment: datetime) -> None:
        with self._cond:
            self._sweep_moment = moment
            self._ensure_thread_locked()
            self._cond.notify_all()

    def cached_hits(self) -> dict[str, datetime]:
        with self._cond:
            return dict(self._cache)

    def flush(self, timeout: float = 5.0) -> bool:
        """强制忽略去抖把排队命中/巡检写盘，并等到 worker 退出（测试用）。"""
        with self._cond:
            self._force = True
            self._ensure_thread_locked()
            self._cond.notify_all()
            deadline = time.monotonic() + max(0.0, float(timeout))
            while self._thread is not None and self._thread.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    # ------------------------------------------------------------ worker
    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="chat-memory-hit-write")
        self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                work = self._collect_locked()
                if work is None:
                    self._thread = None
                    self._cond.notify_all()
                    return
            succeeded = None
            try:
                succeeded = self._write_batch(*work)
            except Exception:  # noqa: BLE001 - 命中记录失败只记日志
                # v2f L1：失败不在这里打完整 traceback；退避重试，连续失败到
                # MAX_HIT_WRITE_ATTEMPTS 次后由下面统一记一条 warning 并放弃本批。
                succeeded = None
            with self._cond:
                now = time.monotonic()
                if succeeded is True:
                    # L1：只有真正落盘才进入 60s 去抖窗口。
                    for entry_id in work[0]:
                        self._last_write[entry_id] = now
                if succeeded is not None:
                    # 巡检已处理（含"无变化"）：同样进入 60s 低频窗口。
                    if work[1] is not None:
                        self._last_sweep = now
                    self._failures = 0
                else:
                    # 失败不置 _last_write（否则该命中 60s 内不再重试）；
                    # ``False``（条目已不存在/巡检无变化）是"无需写盘"，不是失败。
                    self._failures += 1
                    if self._failures >= MAX_HIT_WRITE_ATTEMPTS:
                        log.warning(
                            "对话记忆命中记录连续 %d 次写盘失败，放弃本批: %s",
                            self._failures, self.path)
                        self._failures = 0
                    else:
                        for entry_id, moment in work[0].items():
                            self._pending.setdefault(entry_id, moment)
                        if work[1] is not None and (
                                self._sweep_moment is None
                                or work[1] > self._sweep_moment):
                            self._sweep_moment = work[1]
                        self._cond.wait(
                            timeout=_fail_retry_delay(self._failures))
                self._cond.notify_all()

    def _collect_locked(self) -> tuple[dict[str, datetime], datetime | None] | None:
        while True:
            now = time.monotonic()
            if self._force:
                ids = dict(self._pending)
                self._pending.clear()
                sweep = self._sweep_moment
                self._sweep_moment = None
                self._force = False
                return (ids, sweep) if (ids or sweep is not None) else None
            ready: dict[str, datetime] = {}
            wait_for: float | None = None
            for entry_id, moment in self._pending.items():
                last = self._last_write.get(entry_id)
                if last is None or now - last >= HIT_WRITE_DEBOUNCE_SECONDS:
                    ready[entry_id] = moment
                else:
                    remain = HIT_WRITE_DEBOUNCE_SECONDS - (now - last)
                    wait_for = remain if wait_for is None else min(wait_for, remain)
            if ready or self._sweep_moment is not None:
                for entry_id in ready:
                    self._pending.pop(entry_id, None)
                sweep = self._sweep_moment
                self._sweep_moment = None
                return ready, sweep
            if not self._pending:
                return None
            self._cond.wait(timeout=wait_for if wait_for is not None else 1.0)

    def _write_batch(
        self,
        ids: dict[str, datetime],
        sweep_moment: datetime | None,
    ) -> bool | None:
        memory = ChatMemory(self.path, clock=self._clock)
        return memory._apply_hit_batch(ids, sweep_moment)


_HIT_QUEUES: dict[Path, _HitWriteQueue] = {}
_HIT_QUEUES_LOCK = threading.Lock()


def _hit_queue_for(path: Path | str) -> _HitWriteQueue:
    key = _resolved(path)
    with _HIT_QUEUES_LOCK:
        queue = _HIT_QUEUES.get(key)
        if queue is None:
            queue = _HitWriteQueue(key)
            _HIT_QUEUES[key] = queue
        return queue


def _cached_hits(path: Path) -> dict[str, datetime]:
    key = _resolved(path)
    with _HIT_QUEUES_LOCK:
        queue = _HIT_QUEUES.get(key)
    return queue.cached_hits() if queue is not None else {}


def flush_pending_hits(timeout: float = 2.0) -> int:
    """退出收口：冲刷本进程所有记忆文件的命中/归档队列。

    ``timeout`` 是全部队列共享的总预算；超时/单队列异常只记日志、绝不抛出
    （退出收尾必须尽力而为）。返回干净收口的队列数。生产调用点是
    ``pet/app.py`` 的 aboutToQuit 收口（L2：否则去抖窗口内的命中随 daemon
    线程一起丢弃）。
    """
    with _HIT_QUEUES_LOCK:
        queues = list(_HIT_QUEUES.values())
    if not queues:
        return 0
    deadline = time.monotonic() + max(0.0, float(timeout))
    clean = 0
    for queue in queues:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0.0:
            log.warning("对话记忆命中队列退出前预算耗尽: %s", queue.path)
            continue
        try:
            if queue.flush(remaining):
                clean += 1
            else:
                log.warning("对话记忆命中队列退出前未写完: %s", queue.path)
        except Exception:  # noqa: BLE001 - 退出路径只记日志
            log.exception("对话记忆命中队列退出前冲刷失败: %s", queue.path)
    return clean


class ChatMemory:
    """按角色的对话全局记忆管理器（存储纪律见模块 docstring，照抄 proactive_memory）。"""

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
        max_entries: int = MAX_ENTRIES,
        max_total_chars: int = MAX_TOTAL_CHARS,
        lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    ) -> None:
        self.path = _resolved(path)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.max_entries = max(1, int(max_entries))
        self.max_total_chars = max(MAX_ENTRY_CHARS, int(max_total_chars))
        self._lock_timeout_ms = max(1, int(lock_timeout_ms))
        self._lock = _lock_for(self.path)
        self._io_lock = _io_lock_for(self.path)
        self._lock_file = lock_path_for(self.path)

    # ------------------------------------------------------------ 读
    def _retry_with_narrow_lock(self, step: Callable[[], Any], *, attempts: int) -> Any:
        """窄锁内只做一次 ``step``；瞬时共享冲突在**锁外**退避重试（口径与
        proactive_memory._retry_with_narrow_lock 相同：退避期间没有文件操作，
        把锁一起睡掉只是让 GUI 读者陪睡）。"""
        delay = _RETRY_BASE_DELAY
        for attempt in range(1, attempts + 1):
            with self._io_lock:
                try:
                    return step()
                except OSError as exc:
                    if attempt >= attempts or not _is_replace_conflict(exc):
                        raise
            time.sleep(delay)
            delay = min(delay * 2, _RETRY_MAX_DELAY)
        raise AssertionError(f"重试循环不可达（attempts={attempts}）")

    def _read_entries_strict(self) -> list[dict[str, Any]] | None:
        """读当前记忆列表；**"读失败"与"不存在/合法空/损坏"严格区分**。

        - 返回 list：文件不存在、内容合法（含空 entries）、或内容损坏——损坏是
          "盘上就是这个内容"，按既有口径回退空列表、不重试；
        - 返回 None：**读取失败**（瞬时共享冲突重试耗尽 / 真 OSError）——读改写链
          （add/remove）据此知道"盘上有没有旧记忆"是未知的，必须放弃本次修改；
          纯读路径（load）把 None 降级成空列表展示，降级结果绝不进读改写链。
        """
        try:
            if not self.path.is_file():
                return []
        except OSError as exc:
            # 探测失败（stat 报 EACCES/EIO/WinError 5/32…）不是"没有记忆"而是"读不到"。
            log.warning("对话记忆存在性探测失败: %s (%s)", self.path, exc)
            return None
        try:
            text = self._retry_with_narrow_lock(
                lambda: self.path.read_text(encoding="utf-8"), attempts=_READ_ATTEMPTS)
        except FileNotFoundError:
            return []  # 读之前被清空：目标状态就是"没有记忆"
        except OSError as exc:
            log.warning("对话记忆读取失败: %s (%s)", self.path, exc)
            return None
        except ValueError:
            return []  # UnicodeDecodeError ⊂ ValueError：文件在但不是合法 utf-8
        try:
            raw = json.loads(text)
        except ValueError:
            return []
        if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            return [item for item in raw["entries"] if isinstance(item, dict)]
        return []

    def load(self) -> list[dict[str, Any]]:
        """读取记忆列表（新条目在前）。纯读，只与"本地正在写盘"互斥一瞬间。

        老条目在返回的副本上补 ``layer=fact`` 与现场 bigram ``keywords``；
        **不写盘**，盘上文件保持原样（无迁移）。进程内已记录、尚未落盘的命中
        （``note_recalled`` 去抖窗口内的 ``last_hit_at``）在副本上先行生效。
        """
        entries = self._read_entries_strict()
        # 超期 topic 的隐式归档判定必须走注入时钟：绕过它会让测试里钉死的时钟
        # 失效——固定 NOW 的用例在真实时间越过归档阈值后悄然转红（2026-11 实发）。
        moment = self._clock()
        cached = _cached_hits(self.path)
        loaded: list[dict[str, Any]] = []
        for entry in entries or []:
            item = dict(entry)
            hit = cached.get(str(item.get("id", "")))
            if hit is not None and layer_of(item) == "topic":
                item["last_hit_at"] = hit.isoformat()
            loaded.append(_normalized_entry(item, now=moment))
        return loaded

    def load_grouped(self) -> dict[str, list[dict[str, Any]]]:
        """按四层分组读取；组内按 updated_at 倒序，四个键恒存在。"""
        groups: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}
        for entry in self.load():
            groups[layer_of(entry)].append(entry)
        for items in groups.values():
            items.sort(
                key=lambda entry: (
                    _iso_sort_key(entry.get("updated_at")),
                    str(entry.get("id", "")),
                ),
                reverse=True,
            )
        return groups

    # ------------------------------------------------------------ 跨进程锁
    def _try_file_lock(self, qlock: QLockFile) -> bool:
        """尝试取跨进程锁；取不到只告警并返回 False，调用方必须放弃本次修改。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("对话记忆锁目录创建失败: %s (%s)", self.path.parent, exc)
            return False
        if qlock.tryLock(self._lock_timeout_ms):
            return True
        log.warning("对话记忆跨进程锁获取超时（%dms），本次不修改记忆: %s",
                    self._lock_timeout_ms, self.path)
        return False

    # ------------------------------------------------------------ 写
    def add(
        self,
        text: str,
        source: str = "manual",
        *,
        layer: str | None = None,
    ) -> tuple[dict[str, Any], bool] | None:
        """新增/刷新一条记忆（读→改→写整体跨进程互斥）。

        ``layer`` 缺省时从 ``RememberCommand`` 或完整「记住…：」文本里取；再缺省
        按 ``fact``。返回 ``(entry, created)``：``created=False`` 表示同一文本已
        在记忆里（去重：updated_at 刷新 + hits+1，不新增条目）。返回 None：文本
        为空、读失败或拿不到跨进程锁——后两种情况盘上**一个字节都没动**。
        """
        layer_hint = layer or getattr(text, "layer", None)
        raw = str(text or "").strip()
        if not layer_hint:
            parsed = parse_remember_command(raw)
            if parsed is not None:
                layer_hint = getattr(parsed, "layer", None)
                raw = str(parsed).strip()
        raw = raw[:MAX_ENTRY_CHARS]
        if not raw:
            return None
        explicit_layer = normalize_layer(layer_hint) if layer_hint else None
        keywords = extract_keywords(raw)
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return None
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("对话记忆读取失败，本次不修改记忆: %s", self.path)
                    return None
                now = self._clock().isoformat()
                for entry in entries:
                    if str(entry.get("text", "")) == raw:
                        entry["updated_at"] = now
                        entry["hits"] = int(entry.get("hits", 0) or 0) + 1
                        if explicit_layer is not None:
                            entry["layer"] = explicit_layer
                            entry.pop("archived", None)
                        else:
                            entry["layer"] = layer_of(entry)
                        entry["keywords"] = (
                            _clean_keyword_list(entry.get("keywords")) or keywords
                        )
                        if layer_of(entry) == "topic":
                            entry["last_hit_at"] = now
                            # L4：手动钉 = 恢复。``add`` 是唯一手动通道，所以这里
                            # 无条件丢掉归档标记；自动通道必须走 ``apply_ops``，
                            # 不得改走 ``add()``（否则自动提炼会借手动通道复活归档）。
                            entry.pop("archived", None)
                        entries.remove(entry)
                        entries.insert(0, entry)
                        # v2f L5：去重刷新也要走限额，否则盘上超预算的老条目
                        # 会一直停在超限状态（旧实现直接 return 跳过了淘汰）。
                        self._enforce_limits(entries, protected=(entry,))
                        self._write(entries)
                        return entry, False
                entry_layer = explicit_layer or DEFAULT_LAYER
                entry = {
                    "id": uuid.uuid4().hex,
                    "text": raw,
                    "source": str(source or "manual"),
                    "layer": entry_layer,
                    "keywords": keywords,
                    "created_at": now,
                    "updated_at": now,
                    "hits": 1,
                }
                if entry_layer == "topic":
                    entry["last_hit_at"] = now
                entries.insert(0, entry)
                self._enforce_limits(entries, protected=(entry,))
                self._write(entries)
                return entry, True
            finally:
                qlock.unlock()

    def apply_ops(self, ops: list[dict[str, Any]], source: str = "auto") -> bool:
        """合并一批记忆操作（档 B 自动提炼；读→改→写整体跨进程互斥，一次落盘）。

        逐条按序应用：
        - ADD：按文本归一去重（与 add() 同源）——命中 hits+1 + updated_at 刷新
          （source 保持原值：手动钉过的条目被自动提炼再提到，仍是 manual）；
        - UPDATE：命中 = 同 ADD 命中（重申）；未命中按 ADD；
        - FORGET：按文本命中删除；未命中忽略。

        v2a：ops 可带 ``layer``（fact/lesson/topic，缺省 fact）与 ``keywords``
        （≤6，缺省现场 bigram 兜底）。老格式无这两个字段，按 fact + 现场提取兼容；
        自动提炼重申已有条目时不覆盖原层（发版终审起对自动来源恒成立，避免把
        user 偏好降级成 fact）。

        v2c：``source != "manual"`` 时，已归档或隐式超期的条目只 +hits，不改层、
        不刷 updated_at/last_hit_at、不改关键词（FORGET 仍可删除）；手动重申
        保留「改层 + 恢复」语义。自动通道一律走本方法，不得改走 ``add()``。

        v2f N1：自动来源对**所有**条目都不写 ``last_hit_at`` / ``updated_at``，
        只 +hits——否则后台提炼每 13 天重申一次就让 topic 永不到期；活跃时间
        只由真实召回（``note_recalled``）与手动钉刷新。
        v2f N3：自动来源要新建条目时，先与已归档条目算关键词 Jaccard；达到
        ``ARCHIVE_DUP_RATIO`` 视为换说法绕过归档，丢弃这次新增（手动来源不受限）。
        v2f L4：整批没有任何实际变更（如 FORGET 未命中）时不写盘并返回 False。

        发版终审：自动来源对已有条目**永不改层**（ops 显式带 layer 也不生效）——
        提炼按内容分类，会把手动钉的 user 偏好降级成 fact 而丢失恒定注入。

        返回本次有没有真的落盘（空操作 / 读失败 / 拿不到跨进程锁 = False，
        后两种情况盘上一个字节都没动）。
        """
        normalized = []
        for item in ops or []:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", "")).strip().upper()
            text = str(item.get("text", "")).strip()[:MAX_ENTRY_CHARS]
            if op not in ("ADD", "UPDATE", "FORGET") or not text:
                continue
            explicit_layer = (
                normalize_layer(item.get("layer")) if item.get("layer") else None
            )
            keywords = _clean_keyword_list(item.get("keywords"))
            normalized.append((op, text, explicit_layer, keywords))
        if not normalized:
            return False
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return False
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("对话记忆读取失败，本次不合并提炼操作: %s", self.path)
                    return False
                moment = self._clock()
                now = moment.isoformat()
                added: list[dict[str, Any]] = []
                manual = str(source or "").strip().lower() == "manual"
                changed = False
                for op, text, explicit_layer, keywords in normalized:
                    found = next((e for e in entries if str(e.get("text", "")) == text), None)
                    if op == "FORGET":
                        if found is not None:
                            entries.remove(found)
                            if found in added:
                                added.remove(found)
                            changed = True
                        continue
                    # ADD / UPDATE 未命中按 ADD；UPDATE 命中 = 重申
                    if found is not None:
                        # v2c M1/M3：自动来源对「已归档 / 隐式超期」条目只允许
                        # 重申计数（+hits），不改层、不刷 updated_at/last_hit_at、
                        # 不改关键词——否则后台提炼一重申就把归档 topic 静默复活。
                        # 显式归档与 is_archived 的隐式口径必须同源（M3）。
                        if not manual and is_archived(found, moment):
                            found["hits"] = int(found.get("hits", 0) or 0) + 1
                            changed = True
                            continue
                        found["hits"] = int(found.get("hits", 0) or 0) + 1
                        # v2f N1：只有手动钉能刷新活跃/更新时间；自动重申只累计
                        # hits（否则提炼的周期性复述会让 topic 的 14 天归档永不触发）。
                        if manual:
                            found["updated_at"] = now
                        # v2b：手动来源（手动钉）可以改层并解除归档；自动来源
                        # 永不改已有条目的层（发版终审：提炼提示词按内容分类，
                        # 带 layer 的自动重申会把手动钉的 user 偏好降级成 fact，
                        # 此后不再恒定注入）。
                        restored = manual and explicit_layer is not None
                        if explicit_layer is not None and manual:
                            found["layer"] = explicit_layer
                            found.pop("archived", None)
                        else:
                            found["layer"] = layer_of(found)
                        found["keywords"] = (
                            keywords
                            or _clean_keyword_list(found.get("keywords"))
                            or extract_keywords(text)
                        )
                        if manual and layer_of(found) == "topic" and (
                                restored or not is_archived(found, moment)):
                            found["last_hit_at"] = now
                        entries.remove(found)
                        entries.insert(0, found)
                        changed = True
                        continue
                    # v2f N3：自动新增前先查已归档条目的同义改写；命中则丢弃。
                    if not manual and _duplicates_archived(text, entries, moment):
                        continue
                    entry_layer = explicit_layer or DEFAULT_LAYER
                    entry = {
                        "id": uuid.uuid4().hex,
                        "text": text,
                        "source": str(source or "auto"),
                        "layer": entry_layer,
                        "keywords": keywords or extract_keywords(text),
                        "created_at": now,
                        "updated_at": now,
                        "hits": 1,
                    }
                    if entry_layer == "topic":
                        entry["last_hit_at"] = now
                    entries.insert(0, entry)
                    added.append(entry)
                    changed = True
                # v2f L4：空操作（FORGET 未命中 / 全部被归档去重丢弃）不写盘。
                if not changed:
                    return False
                self._enforce_limits(entries, protected=tuple(added))
                return self._write(entries)
            finally:
                qlock.unlock()

    def update_layer(self, entry_id: str, layer: str) -> bool:
        """单条改层（UI 下拉）；目标不存在 = 已达成 → True。"""
        target = normalize_layer(layer)
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return False
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("对话记忆读取失败，本次不改层: %s", self.path)
                    return False
                found = next(
                    (e for e in entries if str(e.get("id")) == str(entry_id)), None)
                if found is None:
                    return True
                now = self._clock().isoformat()
                found["layer"] = target
                found["updated_at"] = now
                # 只在「改到别的层」时丢掉归档标记（话题已不是话题，标记无意义）；
                # 仍在 topic 层就把归档态留着，避免这里成为第二条静默恢复通道。
                if target != "topic":
                    found.pop("archived", None)
                if target == "topic":
                    found["last_hit_at"] = now
                return self._write(entries)
            finally:
                qlock.unlock()

    def set_archived(self, entry_id: str, archived: bool) -> bool:
        """归档 / 恢复一条 topic；恢复时刷新 last_hit_at，避免立刻又被自动归档。"""
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return False
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("对话记忆读取失败，本次不改归档态: %s", self.path)
                    return False
                found = next(
                    (e for e in entries if str(e.get("id")) == str(entry_id)), None)
                if found is None:
                    return True
                found["archived"] = bool(archived)
                if not archived:
                    now = self._clock().isoformat()
                    found["last_hit_at"] = now
                    found["updated_at"] = now
                return self._write(entries)
            finally:
                qlock.unlock()

    def restore_topic(self, entry_id: str) -> bool:
        """恢复归档 topic（UI「恢复」按钮）。"""
        return self.set_archived(entry_id, False)

    def note_recalled(
        self,
        entry_ids: list[str],
        *,
        now: datetime | None = None,
    ) -> None:
        """记录一次命中（v2b：GUI 线程只更新内存缓存并入队，不碰磁盘）。

        真正的 ``last_hit_at`` 落盘与超期 topic 归档在按路径单飞的 daemon 线程
        里完成；同一 topic 两次落盘 ≥60s 去抖，排队期间合并去重。任何写盘异常
        只写日志。空 ``entry_ids`` 也会触发一次低频归档巡检（≥60s 一次）。
        """
        ids = {str(item) for item in (entry_ids or [])}
        moment = now or self._clock()
        _hit_queue_for(self.path).record(ids, moment, self._clock)

    def sweep_archive(self, *, now: datetime | None = None) -> None:
        """主动安排一次超期 topic 归档巡检（异步；测试可随后 ``flush_hits``）。"""
        _hit_queue_for(self.path).request_sweep(now or self._clock())

    def flush_hits(self, timeout: float = 5.0) -> bool:
        """等本路径所有排队中的命中/归档写盘完成（测试与退出前的等待点）。"""
        return _hit_queue_for(self.path).flush(timeout)

    def _apply_hit_batch(
        self,
        hits: dict[str, datetime],
        sweep_moment: datetime | None,
    ) -> bool | None:
        """worker 专用：一次读改写落盘命中时间 + 超期 topic 归档。

        ``True`` = 已写盘；``False`` = 无需写盘（条目已不存在/巡检无变化）；
        ``None`` = 写盘失败（拿不到锁 / 读失败 / 写失败），调用方保留批次重试。
        """
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return None
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("对话记忆读取失败，本次不记录命中: %s", self.path)
                    return None
                changed = False
                for entry in entries:
                    entry_id = str(entry.get("id"))
                    moment = hits.get(entry_id)
                    if moment is None:
                        continue
                    # v2f L6：命中不只刷新 last_hit_at，还要累加 hits——淘汰与
                    # 注入排序都看 hits，旧实现让真实命中永远停在 1。
                    entry["hits"] = int(entry.get("hits", 0) or 0) + 1
                    if layer_of(entry) == "topic":
                        entry["last_hit_at"] = moment.isoformat()
                    changed = True
                if sweep_moment is not None:
                    changed = archive_stale_topics(entries, sweep_moment) or changed
                if not changed:
                    return False
                return True if self._write(entries) else None
            finally:
                qlock.unlock()

    def _enforce_limits(self, entries: list[dict[str, Any]], *, protected: tuple = ()) -> None:
        """条目数 / 总字符超限：按 hits 少 + 最老淘汰；刚加入的条目受保护
        （单条已截到 MAX_ENTRY_CHARS，必然能在预算内存活，循环必然终止）。"""
        protected_ids = {id(e) for e in protected}

        def over() -> bool:
            return (len(entries) > self.max_entries
                    or sum(len(str(e.get("text", ""))) for e in entries) > self.max_total_chars)

        while over():
            candidates = [e for e in entries if id(e) not in protected_ids]
            if not candidates:
                break
            victim = min(
                candidates,
                key=lambda e: (int(e.get("hits", 0) or 0), _iso_sort_key(e.get("created_at"))),
            )
            entries.remove(victim)

    def remove(self, entry_id: str) -> bool:
        """按 id 删除一条（跨进程互斥）；目标不存在 = 已达成 → True。"""
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return False
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("对话记忆读取失败，本次不删除: %s", self.path)
                    return False
                kept = [e for e in entries if str(e.get("id")) != str(entry_id)]
                if len(kept) == len(entries):
                    return True
                if not kept:
                    return self._unlink()
                return self._write(kept)
            finally:
                qlock.unlock()

    def _write(self, entries: list[dict[str, Any]]) -> bool:
        """同目录唯一临时文件 + fsync + 原子替换落盘（纪律同 proactive_memory._write）。"""
        payload = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp")
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                # 只有碰数据文件的那一下才拿窄锁；替换的退避重试在锁外。
                self._retry_with_narrow_lock(
                    lambda: atomic_replace_with_retry(tmp_path, self.path, attempts=1),
                    attempts=_REPLACE_ATTEMPTS)
            finally:
                tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("对话记忆写入失败: %s (%s)", self.path, exc)
            return False
        return True

    def _unlink(self) -> bool:
        """窄锁内、有界重试地删除数据文件；返回"文件是否已不存在"。"""
        try:
            if not self.path.is_file():
                return True
        except OSError as exc:
            log.warning("对话记忆清空失败（存在性探测失败）: %s (%s)", self.path, exc)
            return False

        def remove_file() -> None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # 另一个进程刚删完：目标状态已达成

        try:
            self._retry_with_narrow_lock(remove_file, attempts=_UNLINK_ATTEMPTS)
        except OSError as exc:
            log.warning("对话记忆清空失败（重试 %d 次后仍失败）: %s (%s)",
                        _UNLINK_ATTEMPTS, self.path, exc)
            return False
        return True

    def clear(self) -> bool:
        """清空记忆（跨进程互斥，等 in-flight 的写入落盘后再删），返回这次是否真的清空。"""
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return False
            try:
                return self._unlink()
            finally:
                qlock.unlock()
