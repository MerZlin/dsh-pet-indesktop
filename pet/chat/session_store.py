# -*- coding: utf-8 -*-
"""聊天会话持久化：读穿 pending 的异步写盘（B8 进程内版）。

设计要点（复审档案 _plan/REVIEW_B8*.md 的全部教训）：
- 写盘（json 落盘 + fsync + atomic replace）在串行后台 worker 线程执行，
  GUI 线程只负责把 session 序列化成字节快照并提交——worker 绝不触碰可变 session 对象；
- 同一路径的连续写合并（流式 delta 只落最终快照）；
- **读穿 pending**：load()/list() 先看未落盘的快照，read-your-writes 语义与旧同步版一致，
  既有调用方（保存后立刻 load/list）无需改动；
- flush() 诚实：有未上报的写失败/超时一律返回 False（失败被下一次 flush 上报后清零，
  保证每个失败至少被上报一次）；save()/delete() 在 writer 关闭后返回 False（拒绝可观测）；
- close() 幂等且粘滞：先 flush 再停线程，重复 close 不覆盖第一次的结果；
- 崩溃安全与旧版一致（tmp + fsync + os.replace）；进程被 kill -9 时未落盘快照丢失
  （窗口 ≈毫秒级，worker 连续消费无人工延迟）——已知权衡，写在这里；
- 不做任何跨进程协调（多实例靠 instance_id 分目录隔离，那是既有机制）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from .models import ChatSession, utc_now

log = logging.getLogger("dsh-pet-standalone")


class _AsyncWriter:
    """每个会话目录一个串行写盘 worker（同目录的多个 SessionStore 共享）。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cond = threading.Condition()
        self._pending: OrderedDict[Path, bytes | None] = OrderedDict()  # None = 删除
        self._submitted = 0
        self._processed = 0
        self._unreported_failures = 0
        self._closing = False
        self._close_result: bool | None = None  # 粘滞：第一次 close 的真实结果
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"session-writer-{root.name}",
        )
        self._thread.start()

    # ---------------- 提交侧（任意线程，主要是 GUI） ----------------

    def submit(self, path: Path, payload: bytes | None) -> bool:
        """提交写/删除。返回 False = writer 已关闭，拒绝被调用方可观测。"""
        with self._cond:
            if self._closing:
                log.warning("会话写盘 worker 已关闭，拒绝提交: %s", path.name)
                return False
            # 同路径合并：替换 payload；已合并的提交不产生新的待处理操作，
            # 否则 flush 的目标计数会把合并掉的提交也算进去（永远等不到）。
            if path not in self._pending:
                self._submitted += 1
            self._pending[path] = payload
            self._cond.notify_all()
            return True

    def pending_for_dir(self, folder: Path) -> dict[Path, bytes | None]:
        with self._cond:
            return {p: v for p, v in self._pending.items() if p.parent == folder}

    # ---------------- 等待/关闭 ----------------

    def flush(self, timeout: float = 10.0) -> bool:
        """等待所有已提交操作落盘。任一失败或超时返回 False（绝不虚假成功）。

        失败上报语义：_unreported_failures 被本次 flush 清零——每个失败
        至少被一次 flush 观测到，不会被静默吞掉。
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            target = self._submitted
            while self._processed < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning("会话写盘 flush 超时（%s/%s 已处理）", self._processed, target)
                    self._unreported_failures = 0
                    return False
                self._cond.wait(remaining)
            ok = self._unreported_failures == 0
            if not ok:
                log.warning("会话写盘存在失败操作（flush 返回 False）")
            self._unreported_failures = 0
            return ok

    def close(self, timeout: float = 10.0) -> bool:
        """幂等关闭：先 flush 再停线程。返回第一次 close 的真实结果（粘滞）。"""
        with self._cond:
            if self._close_result is not None:
                return self._close_result
        ok = self.flush(timeout=timeout)
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            # daemon 线程会随进程退出；标记结果但绝不无限等待（B9 的教训：
            # 无界 join 会冻结 GUI/退出路径）
            log.warning("会话写盘 worker 退出超时（仍有未落盘数据风险）")
            ok = False
        with self._cond:
            if self._close_result is None:
                self._close_result = ok
            return self._close_result

    # ---------------- worker 线程 ----------------

    def _loop(self) -> None:
        # peek 而非 pop：写入期间条目保留在 _pending 里，读侧（load/list）
        # 全程可见最新已提交快照——否则"正在写"的窗口期里 load 会穿透到
        # 磁盘旧文件甚至报 FileNotFoundError，把会话误判成不存在。
        while True:
            with self._cond:
                while not self._pending and not self._closing:
                    self._cond.wait()
                if not self._pending:
                    return  # closing 且无积压
                path, payload = next(iter(self._pending.items()))
            try:
                if payload is None:
                    path.unlink(missing_ok=True)
                    _fsync_dir(path.parent)
                else:
                    _atomic_write(path, payload)
            except Exception:
                log.exception("会话写盘失败: %s", path)
                with self._cond:
                    self._unreported_failures += 1
            with self._cond:
                # 写完后只有内容没被更新过才移除；写入期间来了新快照则
                # 保留条目，下一轮用最新 payload 重写（最终一致）。
                current = self._pending.get(path)
                if current is payload or path not in self._pending:
                    self._pending.pop(path, None)
                    self._processed += 1
                self._cond.notify_all()


def _atomic_write(path: Path, payload: bytes) -> None:
    """与旧同步版一致的崩溃安全写：tmp + fsync + os.replace + 父目录 fsync。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(payload.decode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    _fsync_dir(path.parent)


def _fsync_dir(folder: Path) -> None:
    """保证 rename/unlink 的目录项持久化。失败必须让调用方知道（往上抛）。"""
    if os.name == "nt":
        return  # Windows 无目录 fsync 语义
    fd = os.open(str(folder), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# 同目录共享 writer：app/现代窗/经典窗/快速对话各建各的 SessionStore，
# 但同一 root 只有一个写盘线程（串行即一致）。
_writers: dict[Path, _AsyncWriter] = {}
_writers_lock = threading.Lock()


def _writer_for(root: Path) -> _AsyncWriter:
    with _writers_lock:
        w = _writers.get(root)
        # 不复活正在关闭的 writer：让 submit 走「拒绝可观测」路径；
        # 测试/重开场景先 close_all_writers() 清注册表，下一次提交自然建新实例。
        if w is None:
            w = _AsyncWriter(root)
            _writers[root] = w
        return w


def close_all_writers(timeout: float = 10.0) -> bool:
    """应用退出时调用：落盘并关闭全部 writer。返回是否全部干净关闭。"""
    with _writers_lock:
        writers = list(_writers.values())
        _writers.clear()
    ok = True
    for w in writers:
        if not w.close(timeout=timeout):
            ok = False
    return ok


class SessionStore:
    def __init__(self, config_dir, instance_id=''):
        # 多开隔离：带实例 ID 时使用独立会话目录，避免多实例互覆同一会话；
        # 不传实例 ID 时保持原目录，历史会话无缝沿用。
        suffix = f'-{instance_id}' if str(instance_id or '').strip() else ''
        self.root = Path(config_dir) / f'sessions{suffix}'

    def _path(self, character_id, session_id):
        return self.root / character_id / f'{session_id}.json'

    def create(self, character_id, provider_id, system_prompt):
        return ChatSession.create(character_id, provider_id, system_prompt)

    def save(self, session) -> bool:
        """序列化（调用线程）+ 提交异步落盘。返回 False = writer 已关闭被拒绝。"""
        session.updated_at = utc_now()
        payload = json.dumps(session.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        return _writer_for(self.root).submit(
            self._path(session.character_id, session.session_id), payload)

    def flush(self, timeout: float = 10.0) -> bool:
        """等待本目录所有已提交写盘完成；有失败/超时返回 False。"""
        with _writers_lock:
            w = _writers.get(self.root)
        if w is None:
            return True  # 从未写过，无事可等
        return w.flush(timeout=timeout)

    def _parse(self, raw: bytes):
        try:
            return ChatSession.from_dict(json.loads(raw.decode("utf-8")))
        except (ValueError, KeyError, TypeError):
            return None

    def load(self, session_id, character_id=None):
        # pending 优先：未落盘快照/删除立即生效（read-your-writes）。
        # character_id 缺省时磁盘 glob 找不到未落盘的新会话，也必须查 pending。
        found, payload = _pending_for_session(self.root, session_id, character_id)
        if found:
            return None if payload is None else self._parse(payload)
        paths = [self._path(character_id, session_id)] if character_id \
            else list(self.root.glob(f'*/{session_id}.json'))
        if not paths:
            return None
        path = paths[0]
        try:
            return ChatSession.from_dict(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, KeyError, TypeError):
            try:
                os.replace(path, path.with_name(f'{path.stem}.corrupt-{int(time.time())}{path.suffix}'))
            except OSError:
                pass
            return None

    def list(self, character_id):
        folder = self.root / character_id
        pending = _pending_for_dir(self.root, folder)
        result = {}
        if folder.is_dir():
            for path in folder.glob('*.json'):
                result[path.stem] = path
        for path, payload in pending.items():
            if payload is None:
                result.pop(path.stem, None)  # 未落盘的删除：列表里直接移除
            else:
                x = self._parse(payload)
                if x:
                    result[path.stem] = x
                continue
        sessions = []
        for key, item in result.items():
            if isinstance(item, Path):
                x = self.load(item.stem, character_id)
                if x:
                    sessions.append(x)
            else:
                sessions.append(item)
        return sorted(sessions, key=lambda x: x.updated_at, reverse=True)

    def delete(self, session) -> bool:
        return _writer_for(self.root).submit(
            self._path(session.character_id, session.session_id), None)

    def clear(self, session):
        session.messages.clear()
        self.save(session)
        return session


def _pending_for_session(root: Path, session_id: str, character_id) -> tuple[bool, bytes | None]:
    """在共享 writer 的 pending 里按 session_id（可选限定角色目录）找未落盘操作。"""
    with _writers_lock:
        w = _writers.get(root)
    if w is None:
        return False, None
    with w._cond:
        for path, payload in w._pending.items():
            if path.stem != session_id:
                continue
            if character_id and path.parent.name != str(character_id):
                continue
            return True, payload
    return False, None


def _pending_for_dir(root: Path, folder: Path) -> dict[Path, bytes | None]:
    with _writers_lock:
        w = _writers.get(root)
    if w is None:
        return {}
    return w.pending_for_dir(folder)
