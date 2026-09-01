from __future__ import annotations
import atexit, json, logging, os, threading, time
from pathlib import Path
from .models import ChatSession, utc_now
from .. import slot_manager as slot_manager_mod

# ============ 历史上限（数值锚定现有代码量级，非拍脑袋） ============
# 参考：prompt.py 的上下文裁剪 history_message_limit=40 / history_char_limit=24000；
# widgets.py 的附件上限 MAX_TEXT_TOTAL_CHARS=200_000、MAX_IMAGE_TOTAL_BYTES=20MB。
MAX_MESSAGES_PER_SESSION = 2000           # 50× 上下文窗口(40)：单会话消息数上限，超出裁剪最旧
MAX_MESSAGE_CHARS = 200_000               # 与 MAX_TEXT_TOTAL_CHARS 一致：单条消息字符数上限
MAX_SESSION_FILE_BYTES = 16 * 1024 * 1024 # 与 20MB 图片总量同量级：单文件大小上限（保存裁剪/加载拒绝）
MAX_SESSION_LIST = 200                    # 列表加载数量上限（防全会话解析爆炸）
MAX_WRITE_ATTEMPTS = 2                    # 单快照写失败自动重试次数（仍失败则内存保留+日志）
WRITE_LOCK_TIMEOUT = 2.0                  # 跨进程会话写锁最长等待（秒）

_logger = logging.getLogger(__name__)


def _serialize_snapshot(snapshot):
    """序列化快照；超出单文件上限时从快照中裁剪最旧消息（内存对象不受影响）。

    非消息字段（system_prompt/custom_title 等）单独超限时抛出 ValueError，
    让写入失败可观测（flush 返回 False），而不是静默产出超限文件。
    """
    data = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if len(data.encode('utf-8')) <= MAX_SESSION_FILE_BYTES:
        return data
    _logger.warning('会话文件超过 %d 字节上限，裁剪最旧消息: %s',
                    MAX_SESSION_FILE_BYTES, snapshot.get('session_id'))
    messages = list(snapshot.get('messages') or [])
    while messages and len(data.encode('utf-8')) > MAX_SESSION_FILE_BYTES:
        messages = messages[1:]
        data = json.dumps({**snapshot, 'messages': messages}, ensure_ascii=False, indent=2)
    if len(data.encode('utf-8')) > MAX_SESSION_FILE_BYTES:
        raise ValueError(
            f'会话元数据（非消息字段）超过 {MAX_SESSION_FILE_BYTES} 字节上限，'
            f'无法安全保存: {snapshot.get("session_id")}')
    return data


def _fsync_dir(path):
    """fsync 父目录，保证 os.replace 的目录项在掉电/系统崩溃后持久（POSIX；Windows 跳过）。"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class SessionDeletedError(Exception):
    """跨进程墓碑拒绝：会话已被其他进程显式删除，迟到的保存被拒绝（防复活）。"""


def _tombstone_path(path):
    """跨进程删除墓碑：与锁文件同目录、不参与 *.json 会话 glob 的隐藏侧车文件。"""
    return path.with_name(f'.{path.name}.deleted')


def _read_tombstone(path):
    """读跨进程删除墓碑的删除版本；无墓碑/损坏返回 0。"""
    try:
        with _tombstone_path(path).open('r', encoding='utf-8') as f:
            raw = json.load(f)
        return int(raw.get('rev', 0) or 0) if isinstance(raw, dict) else 0
    except (OSError, ValueError, TypeError):
        return 0


def _write_tombstone(path, rev):
    """写跨进程删除墓碑（tmp+fsync+replace 原子落盘），供其他进程拒绝迟到保存。"""
    tomb = _tombstone_path(path)
    tmp = path.with_name(f'.{path.name}.deleted.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as f:
        json.dump({'rev': int(rev), 'deleted_at': utc_now()}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, tomb)


def _clear_tombstone(path):
    """删除后合法重建（保存基线不早于删除版本）时清除墓碑。"""
    try:
        _tombstone_path(path).unlink()
    except FileNotFoundError:
        pass


def _acquire_write_lock(path, timeout=WRITE_LOCK_TIMEOUT):
    """跨进程互斥：写会话文件前持同路径 .lock 文件的内核排他锁（阻塞等待至超时）。"""
    lock_path = path.with_name(f'{path.name}.lock')
    deadline = time.monotonic() + timeout
    while True:
        handle = slot_manager_mod.acquire_file_lock(lock_path)
        if handle is not None:
            return handle
        if time.monotonic() >= deadline:
            raise OSError(f'无法获取会话写锁（超时 {timeout:.1f}s）: {lock_path}')
        time.sleep(0.05)


def _merge_snapshots(base_snapshot, incoming):
    """按 message_id 合并两个会话快照（聊天消息追加语义）。

    base_snapshot 是较新（rev 更高）的一份；incoming 是基于旧版本的编辑。
    返回新快照：消息按 base 顺序并附上 incoming 独有的消息；标量字段以 base 为准。
    用于修复「双窗口/双进程基于旧版本保存 → 整会话静默覆盖」的数据丢失。
    """
    merged = dict(base_snapshot)
    seen = {}
    for message in base_snapshot.get('messages') or []:
        if isinstance(message, dict):
            seen[str(message.get('message_id') or id(message))] = message
    for message in incoming.get('messages') or []:
        if isinstance(message, dict):
            key = str(message.get('message_id') or id(message))
            if key not in seen:
                seen[key] = message
    merged['messages'] = list(seen.values())
    return merged


def _read_snapshot(path):
    """读磁盘上的会话快照；不存在/损坏/超限返回 None（不抛异常）。"""
    try:
        if path.stat().st_size > MAX_SESSION_FILE_BYTES:
            return None
        with path.open('r', encoding='utf-8') as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _missing_disk_messages(existing, incoming):
    """磁盘快照存在、但 incoming 缺失的消息 id（rev 数字跨 writer 碰撞时的内容级 CAS 判定）。

    两个独立 writer 各自维护本地 rev 序列，同一路径上两者都可能认为自己是
    "rev N"，单靠 `base < disk_rev` 会漏掉 rev 相等但内容分叉的情况，导致
    后写者静默覆盖先写者的消息。以 message_id 集合做内容级判定兜底。
    """
    if not existing:
        return False
    incoming_ids = {
        str(m.get('message_id')) for m in (incoming.get('messages') or [])
        if isinstance(m, dict)
    }
    for m in existing.get('messages') or []:
        if isinstance(m, dict) and str(m.get('message_id')) not in incoming_ids:
            return True
    return False


def _atomic_write(path, snapshot):
    """tmp + flush + fsync + os.replace 原子替换，并同步父目录。

    写前持跨进程锁并按磁盘 rev 做 CAS：旧版本快照按 message_id 合并后再写，
    绝不静默覆盖其他进程已落盘的新数据；删除墓碑（跨进程）拒绝更旧的迟到保存，
    防止已删除会话复活。返回实际写入的快照（可能为合并结果）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    base = int(snapshot.get('base_rev', snapshot.get('rev', 0)) or 0)
    lock = None
    temp = None
    try:
        lock = _acquire_write_lock(path)
        existing = _read_snapshot(path)
        disk_rev = int((existing or {}).get('rev', 0) or 0)
        tomb_rev = _read_tombstone(path)
        if tomb_rev > base:
            raise SessionDeletedError(
                f'会话已被其他进程删除（墓碑 rev {tomb_rev} > 保存基线 {base}），'
                f'拒绝迟到保存防复活: {path}')
        if tomb_rev > disk_rev:
            disk_rev = tomb_rev  # 删除后重建：基线不低于删除版本
        if base < disk_rev or _missing_disk_messages(existing, snapshot):
            stale_base = base
            snapshot = _merge_snapshots(existing, snapshot)
            base = disk_rev
            snapshot['base_rev'] = base
            snapshot['rev'] = disk_rev + 1
            _logger.warning('跨进程/跨 writer 快照冲突（base %d < 磁盘 %d），已合并消息: %s',
                            stale_base, disk_rev, path)
        # 内部 CAS 基线不写入持久化格式
        snapshot = dict(snapshot)
        snapshot.pop('base_rev', None)
        temp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        with temp.open('w', encoding='utf-8', newline='\n') as f:
            f.write(_serialize_snapshot(snapshot))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        temp = None
        _clear_tombstone(path)
        _fsync_dir(path.parent)
        return snapshot
    finally:
        if lock is not None:
            slot_manager_mod.release_file_lock(lock)
        if temp is not None:
            try:
                temp.unlink()
            except OSError:
                pass


def _atomic_delete(path, rev=None):
    """跨进程删除：与写入共用同一把锁；写持久墓碑（含删除版本）防其他进程迟到保存复活。

    与 _atomic_write 同待遇：unlink 后同步父目录，删除的目录项在掉电后持久。
    """
    lock = None
    try:
        lock = _acquire_write_lock(path)
        if rev is None:
            existing = _read_snapshot(path)
            rev = int((existing or {}).get('rev', 0) or 0) + 1
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        _write_tombstone(path, rev)
        _fsync_dir(path.parent)
    finally:
        if lock is not None:
            slot_manager_mod.release_file_lock(lock)


class _SessionWriter:
    """进程内串行 I/O worker（默认所有 SessionStore 共享同一条队列）。

    - submit 只做内存合并入队：同一路径只保留最新操作（save 互相合并、delete 覆盖
      save、save 覆盖 delete），I/O 全部在唯一 worker 线程串行执行；
    - 版本控制：每个会话快照携带单调递增 rev；基于旧 rev 的保存按 message_id 合并
      （双窗口/双进程并发编辑不静默覆盖）；显式删除后的迟到保存被墓碑拒绝（防复活）；
    - 读穿透：peek() 返回最新未落盘操作（含失败快照），GUI 线程 load/list 不丢会话；
    - 写失败：记日志 + 自动重试一次；仍失败则内存保留（可观测：failure_count /
      last_error / failed_count），flush() 会再重试并如实返回失败；
    - flush() 是排空+真实落盘屏障：返回 True 当且仅当所有已提交操作全部成功持久化；
    - close() 原子关闭提交入口后排空并停 worker；关闭后新提交自动重开 worker（不丢数据）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._ops: dict[Path, tuple[str, object]] = {}       # path -> ('save', snapshot) | ('delete', None)
        self._inflight: dict[Path, tuple[str, object]] = {}  # 正在写盘的操作（读穿透仍可见）
        self._failed: dict[Path, tuple[str, object]] = {}    # 写失败保留的操作（save/delete 均计入）
        self._write_attempts: dict[Path, int] = {}
        self._last_rev: dict[Path, int] = {}                 # 每路径已接受的最新 rev
        self._last_snapshot: dict[Path, dict] = {}           # 每路径最后成功写盘的快照（合并来源）
        self._tombstones: set[Path] = set()                  # 显式删除的路径（防迟到保存复活）
        self._failures = 0
        self._last_error: BaseException | None = None
        self._conflicts = 0
        self._stop = False
        self._closed = False
        self._closing = False
        self._close_failed = False  # close() 首次失败/超时状态粘滞：重复 close 不得谎报 True
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

    @property
    def conflict_count(self):
        """旧版本保存冲突 / 删除后复活被拒的次数（可观测，供排查双窗口覆盖）。"""
        with self._lock:
            return self._conflicts

    # ---- 提交（GUI 线程调用，不阻塞） ----
    def submit_save(self, path, snapshot):
        with self._cond:
            if not self._ensure_open_locked(path, '保存'):
                return None
            if path in self._tombstones:
                self._conflicts += 1
                _logger.error('会话已删除，拒绝迟到的保存（防复活）: %s', path)
                return None
            base = int(snapshot.get('base_rev', snapshot.get('rev', 0)) or 0)
            last = self._last_rev.get(path, 0)
            if base < last:
                latest = self._latest_snapshot_locked(path)
                if latest is None:
                    self._conflicts += 1
                    _logger.error('会话保存基于旧版本且无最新快照可合并，拒绝: %s', path)
                    return None
                snapshot = _merge_snapshots(latest, snapshot)
                base = last  # 合并后内容已含到 last 为止的全部消息
                self._conflicts += 1
                _logger.warning('会话保存基于旧版本（base %d < %d），已合并消息防覆盖: %s',
                                base, last, path)
            rev = max(last, int(snapshot.get('rev', 0) or 0)) + 1
            self._last_rev[path] = rev
            snapshot = dict(snapshot)
            snapshot['base_rev'] = base  # 携带调用方基线，供写盘时跨进程 CAS 使用
            snapshot['rev'] = rev
            self._ops[path] = ('save', snapshot)
            self._cond.notify()
            return rev

    def submit_delete(self, path):
        with self._cond:
            if not self._ensure_open_locked(path, '删除'):
                return False
            self._tombstones.add(path)
            self._last_rev[path] = self._last_rev.get(path, 0) + 1
            # payload 携带删除版本：写盘时作为跨进程墓碑 rev（防其他进程迟到保存复活）
            self._ops[path] = ('delete', self._last_rev[path])
            self._cond.notify()
            return True

    def _ensure_open_locked(self, path, action):
        """提交入口：关闭中拒绝并记录；已完全关闭则重开 worker（绝不静默丢数据）。

        close(timeout) 超时后旧 worker 若仍存活，直接复用（唤醒继续消费），
        绝不另起线程与旧 worker 并存争抢同一队列。
        """
        if not self._closed:
            return True
        if self._closing:
            _logger.warning('会话写入正在关闭，丢弃%s: %s', action, path)
            return False
        _logger.debug('会话写入已关闭，重新拉起 worker 接受%s: %s', action, path)
        self._closed = False
        self._stop = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            # close 超时遗留的旧 worker 仍存活：唤醒继续消费，不再新建线程
            self._cond.notify()
        else:
            self._thread = threading.Thread(
                target=self._run, name='session-save-worker', daemon=True)
            self._thread.start()
        return True

    def _latest_snapshot_locked(self, path):
        op = self._ops.get(path)
        if op is not None and op[0] == 'save':
            return op[1]
        op = self._inflight.get(path)
        if op is not None and op[0] == 'save':
            return op[1]
        failed = self._failed.get(path)
        if failed is not None and failed[0] == 'save':
            return failed[1]
        return self._last_snapshot.get(path)

    def peek(self, path):
        """返回该路径最新未落盘操作 ('save', snapshot) / ('delete', rev)，无则 None。"""
        with self._cond:
            op = self._ops.get(path)
            if op is None:
                op = self._inflight.get(path)
            if op is None:
                op = self._failed.get(path)
            return op

    def pending_paths(self):
        """返回所有未落盘路径（含失败快照；供无 character_id 的 load 扫描）。"""
        with self._cond:
            return list(self._ops) + list(self._inflight) + list(self._failed)

    # ---- 屏障（GUI 线程调用，等待真实落盘） ----
    def flush(self, timeout=10.0):
        """同步等待所有已提交操作落盘；返回是否全部成功持久化。

        契约：队列排空后只要仍有失败操作（save/delete 均计入 _failed）或超时，
        就返回 False；只有全部操作真实落盘才返回 True。上层"强制 flush"路径
        必须检查返回值并至少记录日志。
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            retry = [p for p, _kind in self._failed.items()
                     if p not in self._ops and p not in self._inflight]
            for p in retry:
                self._ops[p] = self._failed[p]
            self._cond.notify()
        with self._cond:
            while self._ops or self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return not self._failed

    def close(self, timeout=10.0):
        """排空队列并停 worker 线程（应用退出路径；atexit 兜底注册）。

        - 先原子关闭提交入口再排空：关闭窗口内到达的提交被拒绝（save()/delete()
          通过公开 API 返回 False 明确失败，不再只记内部日志）且不留无人消费的队列；
        - 幂等且诚实：重复调用返回第一次调用的结果；首次排空/落盘失败或超时的状态
          粘滞，后续 close() 不得谎报 True（不丢第一次的失败信息）；
        - close(timeout) 超时后旧 worker 仍存活时，后续提交复用该 worker
          （绝不另起线程与旧 worker 并存争抢队列）。
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            if self._closed:
                thread = self._thread
                ok = not self._close_failed
                closing_now = False
            else:
                thread = self._thread
                self._closing = True
                self._closed = True
                closing_now = True
        if closing_now:
            # flush 会自行加 _cond，不能在持锁状态下调用
            ok = self.flush(timeout)
            with self._cond:
                if not ok:
                    self._close_failed = True
                self._stop = True
                self._closing = False
                self._cond.notify_all()
        remaining = deadline - time.monotonic()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, remaining))
        return ok

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
                    except SessionDeletedError:
                        # 跨进程墓碑拒绝：不是 I/O 失败（不入 _failed、不重试），
                        # 而是可观测的冲突——丢弃迟到保存，防复活。
                        with self._cond:
                            self._conflicts += 1
                        _logger.error('会话已被其他进程删除，拒绝迟到的保存（防复活）: %s', path)
                    except Exception as exc:  # noqa: BLE001 —— worker 永不因单条失败退出
                        self._record_failure(path, kind, payload, exc)
            finally:
                with self._cond:
                    for path, _ in batch:
                        self._inflight.pop(path, None)
                    self._cond.notify_all()

    def _apply(self, path, kind, payload):
        if kind == 'save':
            written = self.atomic_write(path, payload)
        else:
            written = None
            self.atomic_delete(path, payload)  # payload 为删除版本（跨进程墓碑 rev）
        with self._cond:
            if kind == 'save':
                if written is not None:
                    self._last_snapshot[path] = written
                    written_rev = int(written.get('rev', 0) or 0)
                    if written_rev > self._last_rev.get(path, 0):
                        self._last_rev[path] = written_rev
            else:
                self._last_snapshot.pop(path, None)
            self._failed.pop(path, None)
            self._write_attempts.pop(path, None)

    def _record_failure(self, path, kind, payload, exc):
        with self._cond:
            self._failures += 1
            self._last_error = exc
            self._failed[path] = (kind, payload)
            attempts = self._write_attempts.get(path, 0)
            self._write_attempts[path] = attempts + 1
            retry = attempts < MAX_WRITE_ATTEMPTS - 1 and path not in self._ops
            if retry:
                self._ops[path] = (kind, payload)
                self._cond.notify()
        _logger.error('会话%s失败 path=%s: %s',
                      '删除' if kind != 'save' else '保存', path, exc, exc_info=exc)


# 进程内单例：同进程多窗口（modern+classic）/ 多角色共用一条串行 I/O 队列；
# 同一会话按 rev 合并（不互覆）；显式删除以墓碑防复活。
_shared_writer = _SessionWriter()
atexit.register(_shared_writer.close)


def flush_shared_writer(timeout=10.0) -> bool:
    """应用退出路径：同步排空共享会话 writer。返回是否全部成功落盘。"""
    return _shared_writer.flush(timeout)


def close_shared_writer(timeout=10.0) -> bool:
    """应用退出路径：排空并停掉共享会话 writer（atexit 兜底）。返回是否全部成功落盘。"""
    return _shared_writer.close(timeout)


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
        """GUI 线程只做内存快照与入队（不落盘、不阻塞）；同一会话队列合并。

        返回是否成功入队：True 表示已接受（可随后 flush() 确认真实落盘）；
        False 表示被拒绝——会话已删除（防复活）、基于旧版本且无最新快照可合并、
        或写入器正在关闭。拒绝时 session.rev 不更新；拒绝原因通过 writer 的
        日志与 conflict_count 可观测。调用方（关窗/退出路径）必须感知 False 并
        记录，不能只依赖内部日志。
        """
        session.updated_at = utc_now()
        _enforce_save_caps(session)
        rev = self._writer.submit_save(
            self._path(session.character_id, session.session_id), session.to_dict())
        if rev is not None:
            session.rev = rev
            return True
        return False

    def flush(self, timeout=10.0):
        """强制 flush：等待所有已提交操作真实落盘；失败/超时返回 False。"""
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
        """删除会话（异步入队）；返回是否被接受。False 表示写入器正在关闭被拒绝。"""
        return self._writer.submit_delete(self._path(session.character_id, session.session_id))

    def clear(self, session):
        session.messages.clear()
        self.save(session)
        return session
