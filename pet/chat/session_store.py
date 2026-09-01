from __future__ import annotations
import atexit, json, logging, os, threading, time
from pathlib import Path
from .models import ChatSession, utc_now

# ============ 历史上限（数值锚定现有代码量级，非拍脑袋） ============
# 参考：prompt.py 的上下文裁剪 history_message_limit=40 / history_char_limit=24000；
# widgets.py 的附件上限 MAX_TEXT_TOTAL_CHARS=200_000、MAX_IMAGE_TOTAL_BYTES=20MB。
MAX_MESSAGES_PER_SESSION = 2000           # 50× 上下文窗口(40)：单会话消息数上限，超出裁剪最旧
MAX_MESSAGE_CHARS = 200_000               # 与 MAX_TEXT_TOTAL_CHARS 一致：单条消息字符数上限
MAX_SESSION_FILE_BYTES = 16 * 1024 * 1024 # 与 20MB 图片总量同量级：单文件大小上限（保存裁剪/加载拒绝）
MAX_SESSION_LIST = 200                    # 列表加载数量上限（防全会话解析爆炸）
MAX_WRITE_ATTEMPTS = 2                    # 单快照写失败自动重试次数（仍失败则内存保留+日志）

_logger = logging.getLogger(__name__)


def _serialize_snapshot(snapshot):
    """序列化快照；超出单文件上限时从快照中裁剪最旧消息（内存对象不受影响）。"""
    data = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if len(data.encode('utf-8')) <= MAX_SESSION_FILE_BYTES:
        return data
    _logger.warning('会话文件超过 %d 字节上限，裁剪最旧消息: %s',
                    MAX_SESSION_FILE_BYTES, snapshot.get('session_id'))
    messages = list(snapshot.get('messages') or [])
    while messages and len(data.encode('utf-8')) > MAX_SESSION_FILE_BYTES:
        messages = messages[1:]
        data = json.dumps({**snapshot, 'messages': messages}, ensure_ascii=False, indent=2)
    return data


def _atomic_write(path, snapshot):
    """tmp + flush + fsync + os.replace 原子替换；tmp 名含 pid+线程 id，跨进程不撞名。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as f:
            f.write(_serialize_snapshot(snapshot))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _atomic_delete(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class _SessionWriter:
    """进程内串行 I/O worker（默认所有 SessionStore 共享同一条队列）。

    - submit 只做内存合并入队：同一路径只保留最新操作（save 互相合并、delete 覆盖
      save、save 覆盖 delete），I/O 全部在唯一 worker 线程串行执行；
    - 读穿透：peek() 返回最新未落盘操作，GUI 线程 load/list 无需等磁盘；
    - 写失败：记日志 + 自动重试一次；仍失败则内存保留最新快照（可观测：
      failure_count / last_error / failed_count），等待下次 save/flush 重试，
      绝不静默丢数据、绝不留半成品文件；
    - flush() 是排空屏障：等待队列与在飞写全部落盘（含失败快照重试）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._ops: dict[Path, tuple[str, object]] = {}      # path -> ('save', snapshot) | ('delete', None)
        self._inflight: dict[Path, tuple[str, object]] = {}  # 正在写盘的操作（读穿透仍可见）
        self._failed: dict[Path, object] = {}                # 写失败保留的最新快照
        self._write_attempts: dict[Path, int] = {}
        self._failures = 0
        self._last_error: BaseException | None = None
        self._stop = False
        self._closed = False
        # 测试/替换 seam：worker 通过实例属性调用，隔离测试可注入计数/失败包装
        self.atomic_write = _atomic_write
        self.atomic_delete = _atomic_delete
        self._thread = threading.Thread(
            target=self._run, name='session-save-worker', daemon=True)
        self._thread.start()

    # ---- 可观测状态 ----
    @property
    def failure_count(self):
        with self._lock:
            return self._failures

    @property
    def last_error(self):
        with self._lock:
            return self._last_error

    @property
    def failed_count(self):
        with self._lock:
            return len(self._failed)

    # ---- 提交（GUI 线程调用，不阻塞） ----
    def submit_save(self, path, snapshot):
        with self._cond:
            if self._closed:
                _logger.warning('会话写入已关闭，丢弃保存: %s', path)
                return
            self._ops[path] = ('save', snapshot)
            self._cond.notify()

    def submit_delete(self, path):
        with self._cond:
            if self._closed:
                _logger.warning('会话写入已关闭，丢弃删除: %s', path)
                return
            self._ops[path] = ('delete', None)
            self._cond.notify()

    def peek(self, path):
        """返回该路径最新未落盘操作 ('save', snapshot) / ('delete', None)，无则 None。"""
        with self._cond:
            op = self._ops.get(path)
            if op is None:
                op = self._inflight.get(path)
            return op

    def pending_paths(self):
        """返回所有未落盘路径（供无 character_id 的 load 扫描待写快照）。"""
        with self._cond:
            return list(self._ops) + list(self._inflight)

    # ---- 屏障（GUI 线程调用，等待落盘） ----
    def flush(self, timeout=10.0):
        """同步等待所有已提交操作（含失败快照的一次重试）落盘。返回是否排空。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            retry = [p for p in self._failed
                     if p not in self._ops and p not in self._inflight]
            for p in retry:
                self._ops[p] = ('save', self._failed[p])
            self._cond.notify()
        with self._cond:
            while self._ops or self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
        return True

    def close(self, timeout=10.0):
        """排空队列后停 worker 线程（退出路径；atexit 兜底注册）。"""
        with self._cond:
            if self._closed:
                return
            self._stop = True
        self.flush(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(5.0, max(0.1, timeout)))

    # ---- worker 线程 ----
    def _run(self):
        while True:
            with self._cond:
                while not self._ops and not self._stop:
                    self._cond.wait()
                if not self._ops and self._stop:
                    break
                batch = list(self._ops.items())
                self._ops.clear()
                self._inflight.update(batch)
            try:
                for path, (kind, payload) in batch:
                    try:
                        self._apply(path, kind, payload)
                    except Exception as exc:  # noqa: BLE001 —— worker 永不因单条失败退出
                        self._record_failure(path, kind, payload, exc)
            finally:
                with self._cond:
                    for path, _ in batch:
                        self._inflight.pop(path, None)
                    self._cond.notify_all()

    def _apply(self, path, kind, payload):
        if kind == 'save':
            self.atomic_write(path, payload)
        else:
            self.atomic_delete(path)
        with self._cond:
            self._failed.pop(path, None)
            self._write_attempts.pop(path, None)

    def _record_failure(self, path, kind, payload, exc):
        with self._cond:
            self._failures += 1
            self._last_error = exc
            if kind != 'save':
                _logger.error('会话删除失败 path=%s: %s', path, exc, exc_info=exc)
                return
            self._failed[path] = payload
            attempts = self._write_attempts.get(path, 0)
            self._write_attempts[path] = attempts + 1
            retry = attempts < MAX_WRITE_ATTEMPTS - 1 and path not in self._ops
            if retry:
                self._ops[path] = ('save', payload)
                self._cond.notify()
        _logger.error('会话保存失败 path=%s: %s', path, exc, exc_info=exc)


# 进程内单例：同进程多窗口（modern+classic）/ 多角色共用一条串行 I/O 队列，
# 同一会话只保留最新快照，天然规避双窗口写同一会话的竞态。
_shared_writer = _SessionWriter()
atexit.register(_shared_writer.close)


def _enforce_save_caps(session):
    """保存前裁剪：单会话消息数 / 单条消息字符数（内存与磁盘保持一致）。"""
    messages = session.messages
    if len(messages) > MAX_MESSAGES_PER_SESSION:
        dropped = len(messages) - MAX_MESSAGES_PER_SESSION
        del messages[:dropped]
        _logger.warning('会话 %s 消息数超限（%d），裁剪最旧 %d 条',
                        session.session_id, MAX_MESSAGES_PER_SESSION, dropped)
    for message in messages:
        if len(message.content) > MAX_MESSAGE_CHARS:
            _logger.warning('会话 %s 单条消息超长（%d），截断到 %d 字符',
                            session.session_id, len(message.content), MAX_MESSAGE_CHARS)
            message.content = message.content[:MAX_MESSAGE_CHARS]


class SessionStore:
    def __init__(self, config_dir, instance_id='', writer=None):
        # 多开隔离：带实例 ID 时使用独立会话目录，避免多实例互覆同一会话；
        # 不传实例 ID 时保持原目录，历史会话无缝沿用。
        suffix = f'-{instance_id}' if str(instance_id or '').strip() else ''
        self.root = Path(config_dir) / f'sessions{suffix}'
        # 默认共享进程内单例 writer；测试可注入独立 writer 隔离验证。
        self._writer = writer if writer is not None else _shared_writer

    def _path(self, character_id, session_id):
        return self.root / character_id / f'{session_id}.json'

    def create(self, character_id, provider_id, system_prompt):
        return ChatSession.create(character_id, provider_id, system_prompt)

    def save(self, session):
        """GUI 线程只做内存快照与入队（不落盘、不阻塞）；同一会话队列合并。"""
        session.updated_at = utc_now()
        _enforce_save_caps(session)
        self._writer.submit_save(
            self._path(session.character_id, session.session_id), session.to_dict())
        return session

    def flush(self, timeout=10.0):
        """强制 flush：等待所有已提交操作落盘（退出/停止/失败时调用）。"""
        return self._writer.flush(timeout)

    @property
    def failure_count(self):
        return self._writer.failure_count

    @property
    def last_error(self):
        return self._writer.last_error

    def load(self, session_id, character_id=None):
        # 无 character_id 时：磁盘 glob + 尚未落盘的待写快照（异步期间 load 不丢会话）
        if character_id:
            paths = [self._path(character_id, session_id)]
        else:
            paths = list(self.root.glob(f'*/{session_id}.json'))
            seen = set(paths)
            for path in self._writer.pending_paths():
                if path.stem == session_id and self.root in path.parents and path not in seen:
                    paths.append(path)
                    seen.add(path)
        if not paths:
            return None
        path = paths[0]
        # 读穿透：最新未落盘操作优先（save → 快照；delete → 视为已删除）
        pending = self._writer.peek(path)
        if pending is not None:
            kind, payload = pending
            if kind == 'save':
                return ChatSession.from_dict(payload)
            return None
        try:
            if path.stat().st_size > MAX_SESSION_FILE_BYTES:
                _logger.error('会话文件超过上限，跳过解析（保留原文件）: %s', path)
                return None
            return ChatSession.from_dict(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, KeyError, TypeError):
            try:
                os.replace(path, path.with_name(f'{path.stem}.corrupt-{int(time.time())}{path.suffix}'))
            except OSError:
                pass
            return None

    def list(self, character_id):
        folder = self.root / character_id
        # 磁盘文件 + 尚未落盘的待写/待删路径（异步保存期间列表不丢会话）
        paths = set(folder.glob('*.json')) if folder.is_dir() else set()
        paths.update(p for p in self._writer.pending_paths() if p.parent == folder)
        result = []
        for path in paths:
            session = self.load(path.stem, character_id)
            if session is not None:
                result.append(session)
        result.sort(key=lambda x: x.updated_at, reverse=True)
        # 列表加载数量上限：置顶会话优先保留，再取最近的
        if len(result) > MAX_SESSION_LIST:
            pinned = [s for s in result if s.pinned]
            result = (pinned + [s for s in result if not s.pinned])[:MAX_SESSION_LIST]
        return result

    def delete(self, session):
        self._writer.submit_delete(self._path(session.character_id, session.session_id))

    def clear(self, session):
        session.messages.clear()
        self.save(session)
        return session
