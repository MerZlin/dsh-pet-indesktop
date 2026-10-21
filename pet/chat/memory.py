# -*- coding: utf-8 -*-
"""AI 对话全局记忆（档 A：手动钉记忆）— ChatMemory。

设计稿：`.scratch/feature-modules/CHAT-GLOBAL-MEMORY.md`（档 A 先行，零 LLM 成本）。

- 存储文件：`<config_dir>/sessions[-实例]/<character_id>/memory.json`（**按角色**——
  不同角色记不同人格事实；多实例经 SessionStore 的实例后缀目录天然隔离）；
- 结构：`{version, entries: [{id, text, source, created_at, updated_at, hits}]}`，
  新条目在头部；上限 100 条 / 4000 字符，超限按 hits 少 + 最老淘汰；
- 两个入口：对话里「记住：……」（指令解析，`parse_remember_command`）与
  消息气泡「钉为记忆」；重复钉同一文本按文本去重（updated_at 刷新 + hits+1）；
- 注入：`render_injection` 生成「关于用户的记忆」段（硬字符上限，超出按
  hits/时间截），由 PromptBuilder.build_messages 追加到 system 末尾；
  `chat_memory_enabled` 关 = 注入段为空，零成本。

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
import time
import uuid
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
from .session_store import SessionStore

log = logging.getLogger(__name__)

#: 条目数 / 总字符 / 单条字符上限（设计稿 §3：超限按 hits 少 + 最老淘汰）。
MAX_ENTRIES = 100
MAX_TOTAL_CHARS = 4000
MAX_ENTRY_CHARS = 500
#: 注入段的硬字符上限（设计稿 §4：条列，带字符上限，超出按 hits/时间截）。
INJECT_CHAR_LIMIT = 1200

#: 「记住：……」指令前缀（中英文冒号都认）。
REMEMBER_PREFIXES = ("记住：", "记住:")

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


def memory_path(config_dir, instance_id: str, character_id: str) -> Path:
    """记忆文件路径：`<config_dir>/sessions[-实例]/<character_id>/memory.json`。

    与会话文件同目录（SessionStore 的实例后缀规则原样继承），不同角色、不同
    实例各记各的。
    """
    root = SessionStore(config_dir, instance_id).root
    return root / str(character_id) / "memory.json"


def parse_remember_command(text: str) -> str | None:
    """「记住：…」「记住:…」开头 → 指令正文（strip 后，可能是空串）；非指令 → None。

    空串与 None 必须区分：空串是"指令到了但没说要记什么"（调用方给提示气泡），
    None 是"这根本不是记忆指令"（走正常发送）。
    """
    stripped = str(text or "").strip()
    for prefix in REMEMBER_PREFIXES:
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None


def render_injection(entries: list[dict[str, Any]], char_limit: int = INJECT_CHAR_LIMIT) -> str:
    """把记忆条目渲染成 system 末尾的「关于用户的记忆」段；无条目 → 空串。

    选取优先级：hits 高 → 最近更新；段长有硬上限（`char_limit`），第一条放不下的
    条目处截断（"超出按 hits/时间截"，不跳着拣短条目——顺序就是优先级）。
    """
    if not entries:
        return ""
    limit = max(200, int(char_limit))

    def _key(entry: dict[str, Any]) -> tuple:
        return (-int(entry.get("hits", 0) or 0), -_iso_sort_key(entry.get("updated_at")))

    section = _INJECT_HEADER
    for entry in sorted(entries, key=_key):
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


def injection_text(enabled: bool, config_dir, instance_id: str, character_id: str) -> str:
    """生成注入段的总入口：`chat_memory_enabled` 关 / 无记忆 / 读不到 → 空串。

    纯读（只拿本地 I/O 窄锁，不碰写者的宽锁与跨进程锁）：读者要么看到旧快照、
    要么看到新快照，永远不会看到半截文件（写入走原子替换）。
    """
    if not enabled:
        return ""
    memory = ChatMemory(memory_path(config_dir, instance_id, character_id))
    return render_injection(memory.load())


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
        """读取记忆列表（新条目在前）。纯读：只与"本地正在写盘"互斥一瞬间。"""
        entries = self._read_entries_strict()
        return [] if entries is None else entries

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
    def add(self, text: str, source: str = "manual") -> tuple[dict[str, Any], bool] | None:
        """新增/刷新一条记忆（读→改→写整体跨进程互斥）。

        返回 ``(entry, created)``：``created=False`` 表示同一文本已在记忆里
        （去重：updated_at 刷新 + hits+1，不新增条目）。返回 None：文本为空、
        读失败或拿不到跨进程锁——后两种情况盘上**一个字节都没动**（绝不把只含
        新条目的快照盖到旧记忆头上）。
        """
        text = str(text or "").strip()[:MAX_ENTRY_CHARS]
        if not text:
            return None
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
                    if str(entry.get("text", "")) == text:
                        entry["updated_at"] = now
                        entry["hits"] = int(entry.get("hits", 0) or 0) + 1
                        entries.remove(entry)
                        entries.insert(0, entry)
                        self._write(entries)
                        return entry, False
                entry = {
                    "id": uuid.uuid4().hex,
                    "text": text,
                    "source": str(source or "manual"),
                    "created_at": now,
                    "updated_at": now,
                    "hits": 1,
                }
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

        返回本次有没有真的落盘（空操作 / 读失败 / 拿不到跨进程锁 = False，
        后两种情况盘上一个字节都没动）。
        """
        normalized = []
        for item in ops or []:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", "")).strip().upper()
            text = str(item.get("text", "")).strip()[:MAX_ENTRY_CHARS]
            if op in ("ADD", "UPDATE", "FORGET") and text:
                normalized.append((op, text))
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
                now = self._clock().isoformat()
                added: list[dict[str, Any]] = []
                for op, text in normalized:
                    found = next((e for e in entries if str(e.get("text", "")) == text), None)
                    if op == "FORGET":
                        if found is not None:
                            entries.remove(found)
                            if found in added:
                                added.remove(found)
                        continue
                    # ADD / UPDATE 未命中按 ADD；UPDATE 命中 = 重申
                    if found is not None:
                        found["updated_at"] = now
                        found["hits"] = int(found.get("hits", 0) or 0) + 1
                        entries.remove(found)
                        entries.insert(0, found)
                        continue
                    entry = {
                        "id": uuid.uuid4().hex,
                        "text": text,
                        "source": str(source or "auto"),
                        "created_at": now,
                        "updated_at": now,
                        "hits": 1,
                    }
                    entries.insert(0, entry)
                    added.append(entry)
                self._enforce_limits(entries, protected=tuple(added))
                return self._write(entries)
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
