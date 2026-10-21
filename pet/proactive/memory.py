# -*- coding: utf-8 -*-
"""主动识屏短期陪伴记忆 — ProactiveMemory。

批6-1 从 proactive.py 整体迁出（纯搬移，逻辑/默认值/时序零改动）：
- 存储文件：<config.dir>/proactive_screen_memory.json；
- 仅记录元数据（时间戳、进程名、活动分类），绝不保存截图；
- 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
- 持久化：同目录唯一临时文件（mkstemp）+ fsync + 原子替换；损坏回退空列表；
- 并发（三层，缺一层都有实测后果，见下）：
  ① **跨进程**：旁路 `<name>.lock` 的 QLockFile，包住 record 的 读→改→写 与 clear；
  ② **同进程写者**：一把按路径共享的可重入宽锁，序列化 ① 的整个窗口；
  ③ **本地文件 I/O**：一把按路径共享的窄锁，只在真正碰文件的那一瞬间持有
     （读 / 原子替换 / unlink）——重试之间的**退避睡眠也不占锁**：锁要挡的是
     "读句柄与本地替换重叠"（Windows 上会 WinError 5），退避期间没有任何文件
     操作，把锁一起睡掉只是让 GUI 读者陪睡（实测 310ms）。**读路径只拿 ③**
     ——宽锁在 record 手里要等最多 DEFAULT_LOCK_TIMEOUT_MS 的跨进程锁，读者拿
     宽锁就会被拖住。

「读不到」与「没有条目」是两个事实（`_read_entries_strict`）：
    纯读（load/latest）可以降级成空列表展示，但**降级结果绝不进读改写链**——
    record 拿降级后的空列表写下去，落盘的就是只含新条目的快照，旧记忆被整体
    覆写。QLockFile 挡的是别的进程，挡不了自己丢数据；所以 record 在"读失败"
    时放弃本次修改而不是照写。

为什么需要跨进程锁（真实产品路径，不是理论场景）：
    默认「独立设置页」是另一个进程（`python -m pet --settings`，
    app.py:1704），其"清空记忆"入口 `modern_settings_dialog.py:1174`
    与主进程后台主动识屏 `proactive.py:631` 的 record 写同一个文件。
    没有跨进程互斥时两边各自 读→改→写，后写的会把前一条挤掉；clear 也可能
    先删完返回，随后 in-flight 的旧快照把它复活（实测两进程各 25 条只剩
    1~28 条）。

为什么读不能"干脆不加锁"（本机实测，2 写者 × 250 条 + 2 个无锁读者）：
    Windows 的读句柄（MSVCRT 共享模式不含 FILE_SHARE_DELETE）与并发的原子替换直接
    冲突：读句柄还开着时 os.replace 覆盖同一文件报 WinError 5，1297 次替换里 890
    次冲突，atomic_replace_with_retry 的重试预算耗尽后只落盘 407/500 条；读侧同口径
    拿到 275 次空快照。所以读要的是"不与本地写重叠"（窄锁），而不是"等写者排空"
    （宽锁）——产品调用点 `proactive.py:550` 的 latest() 在 GUI 主线程上，实测被
    宽锁拖到 965ms。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QLockFile

from ..config import _is_replace_conflict, atomic_replace_with_retry

log = logging.getLogger(__name__)

#: 跨进程锁等待预算（ms）。真实竞争只有"设置页清空 vs 后台记录"这一类毫秒级
#: 操作，所以这是安全阀而不是常规路径：超时即告警并**放弃本次修改**——
#: 宁可不记这一条短期陪伴记忆，也不做无锁写入把别人的记录覆盖掉。
DEFAULT_LOCK_TIMEOUT_MS = 1000

#: 本地 I/O 重试口径：判定复用 config._is_replace_conflict（WinError 5/32、
#: EACCES、EPERM 这些"马上重试就过"的瞬时共享冲突），延迟取同一量级
#: （config._REPLACE_RETRY_BASE_DELAY/_MAX_DELAY）。只重试瞬时冲突，其它
#: OSError（ENOSPC、ENOENT……）重试一万次也一样，立即如实上报。
_RETRY_BASE_DELAY = 0.02
_RETRY_MAX_DELAY = 0.2
#: 读重试少：读在 GUI 线程上（`proactive.py:550` 的 latest()），3 次 × 20/40ms
#: ≈ 60ms 上限，只用来骑过"另一个进程正在原子替换/删除"的瞬时窗口。
_READ_ATTEMPTS = 3
#: 删文件重试多：unlink 撞上"别的读者还开着这个文件"（Windows WinError 32）时只能
#: 等它关掉——读句柄只在一次 read_text 期间存在，5 次 × 20/40/80/160ms ≈ 300ms
#: 交给用户点击"清空记忆"后的等待预算。
_UNLINK_ATTEMPTS = 5
#: 原子替换的尝试次数（口径同 config.atomic_replace_with_retry 的默认 5 次）。
#: 每次尝试单独拿窄锁、退避在锁外（见 _retry_with_narrow_lock）。
_REPLACE_ATTEMPTS = 5

#: 同进程内按存储路径共享的锁表（**宽锁**）。用弱引用：注册表不随"用过的路径数"
#: 无界增长（最后一个 ProactiveMemory 实例消失后条目自动回收）。
_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()

#: 同进程内按存储路径共享的**本地 I/O 窄锁**：只在真正碰文件的那一瞬间持有。
#:
#: 它存在的理由是两个方向都堵住了：
#: - 再往上一层就是宽锁，而宽锁在 record 手里要等最多 DEFAULT_LOCK_TIMEOUT_MS 的
#:   跨进程锁——读如果拿宽锁，GUI 主线程的 latest() 就被拖住（实测 965ms）；
#: - 干脆不加锁也不行：实测 2 写者 × 250 条 + 2 个无锁读者，Windows 上读句柄让
#:   并发的原子替换报 WinError 5，重试耗尽后只落盘 407/500 条，读侧另有 275 次空
#:   快照（推演与数字见模块 docstring）。
#: 边界同理往回收：**重试之间的退避睡眠不占这把锁**（`_retry_with_narrow_lock`）。
#: 锁要挡的只是"读句柄与本地替换重叠"，退避期间没有文件操作；旧口径把锁连同
#: 20/40/80/160ms 的睡眠一起拿在手里，GUI 主线程的 latest() 实测被按到 310ms。
#: 非重入（普通 Lock）：三条路径各自只拿一次，绝不嵌套；宽锁 → 窄锁是唯一顺序。
_IO_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_IO_LOCKS_GUARD = threading.Lock()


def _resolved(path: Path | str) -> Path:
    """归一化存储路径：锁键与锁文件路径都按它算。

    `sub/../x.json` 与 `x.json` 是同一个文件，但按原样拼锁文件路径会得到两个
    不同字符串——`sub` 不存在时那个路径连创建都失败（锁永远拿不到，写入全被
    自己挡死）。resolved 之后两者指向同一把锁、同一个锁文件、同一个目录。
    """
    return Path(os.path.realpath(str(path)))


def _lock_key(path: Path | str) -> str:
    """进程内锁表键：再叠一层 normcase，保证同一文件只有一把进程内锁。"""
    return os.path.normcase(str(_resolved(path)))


def _lock_for(path: Path | str) -> threading.RLock:
    """返回该存储路径在本进程内共享的可重入锁。

    可重入的意义：`record()` 持锁期间需要读当前内容，读用同一把锁不会自锁
    （QLockFile 不保证可重入，所以跨进程锁只在公开操作入口拿一次）。
    """
    key = _lock_key(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _io_lock_for(path: Path | str) -> threading.Lock:
    """返回该存储路径在本进程内共享的本地 I/O 窄锁（非重入，见 _IO_LOCKS）。"""
    key = _lock_key(path)
    with _IO_LOCKS_GUARD:
        lock = _IO_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _IO_LOCKS[key] = lock
        return lock


def lock_path_for(path: Path | str) -> Path:
    """跨进程锁文件路径：数据文件的旁路文件（与数据文件、临时文件都不同名）。"""
    resolved = _resolved(path)
    return resolved.with_name(resolved.name + ".lock")


class ProactiveMemory:
    """主动识屏短期陪伴记忆管理器。

    存储文件：<config.dir>/proactive_screen_memory.json
    - 仅记录元数据（时间戳、进程名、活动分类），绝不保存截图；
    - 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
    - 采用 同目录唯一临时文件 + fsync + 原子替换 持久化；损坏回退空列表。
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        max_entries: int = 20,
        lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    ) -> None:
        self.path = _resolved(path)
        self._clock = clock
        self.max_entries = max(1, max_entries)
        self._lock_timeout_ms = max(1, int(lock_timeout_ms))
        self._lock = _lock_for(self.path)
        self._io_lock = _io_lock_for(self.path)
        self._lock_file = lock_path_for(self.path)

    # ------------------------------------------------------------ 读
    def _retry_with_narrow_lock(self, step: Callable[[], Any], *, attempts: int) -> Any:
        """窄锁内只做一次 ``step``；瞬时共享冲突在**锁外**退避重试。

        为什么退避必须离开窄锁：这把锁要挡的只是"读句柄与本地读/替换/删除重叠"
        （Windows 上替换撞上读句柄会报 WinError 5）。退避期间没有任何文件操作，
        把锁一起睡掉就只是让 GUI 读者陪睡——产品调用点 `proactive.py:550` 的
        latest() 实测被 20/40/80/160ms 的退避按在 310ms 上。

        重试口径复用 config 的冲突判定（``_is_replace_conflict``：WinError 5/32、
        EACCES、EPERM 这些"马上重试就过"的瞬时冲突）与同一量级延迟；其它 OSError
        是真错误，立即上抛交给调用方如实处理。``step`` 必须在窄锁内原子完成
        （一次 read_text / 一次 os.replace / 一次 unlink），它自己不做重试。
        """
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
        """读当前记忆列表；**"读失败"与"不存在/合法空"严格区分**。

        - 返回 list：文件不存在、内容合法（含空 entries）、或内容损坏——损坏是
          "盘上就是这个内容"，按既有口径回退空列表、不重试（重试读到的还是同一份
          坏内容）；
        - 返回 None：**读取失败**（瞬时共享冲突重试耗尽 / 真 OSError）——调用方由
          此知道"盘上有没有旧记忆"是未知的。

        存在性探测本身也是会失败的调用，失败同样归入 None：``Path.is_file()``
        走 ``os.stat``，pathlib 只吞 ENOENT/ENOTDIR/EBADF/ELOOP 这类"本来就没有"
        的错误，EACCES/EIO/WinError 5/32 一律**原样上抛**（本机 CPython 实测）。
        它不是"没有记忆"，而是"读不到"；放任它抛出去的代价落在产品调用点
        （``proactive.py:550`` 的 latest() 在 GUI 主线程上、且调用前已盖频控章）
        ——异常穿出去，这次主动识屏就死在构造记忆上下文那一步，视觉请求不再派发。

        读改写链（record）必须把 None 当"未知"并放弃修改；纯读路径（load/latest）
        把 None 降级成空列表展示可以接受，但降级结果绝不进读改写链。

        只拿本地 I/O 窄锁，**不碰** record 的宽锁：宽锁在 record 手里要等最多
        lock_timeout_ms 的跨进程锁，读若拿宽锁，GUI 主线程的 latest() 就被拖住。
        """
        try:
            if not self.path.is_file():
                return []
        except OSError as exc:
            # 探测失败（stat 报 EACCES/EIO/WinError 5/32…）不是"没有记忆"，
            # 而是"读不到"：归 None，让 record 放弃本次修改、纯读降级。
            log.warning("主动陪伴记忆存在性探测失败: %s (%s)", self.path, exc)
            return None
        try:
            text = self._retry_with_narrow_lock(
                lambda: self.path.read_text(encoding="utf-8"), attempts=_READ_ATTEMPTS)
        except FileNotFoundError:
            # 文件在这次读之前被删除（clear）= 目标状态就是"没有记忆"
            return []
        except OSError as exc:
            log.warning("主动陪伴记忆读取失败: %s (%s)", self.path, exc)
            return None
        except ValueError:
            # UnicodeDecodeError ⊂ ValueError：文件在，但内容不是合法 utf-8
            return []
        try:
            raw = json.loads(text)
        except ValueError:
            return []
        if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            return raw["entries"]
        return []

    def _read_entries(self) -> list[dict[str, Any]]:
        """读当前记忆列表（**降级口径**）：读失败按"当前没有记忆"返回空列表。

        只给纯读路径用（load/latest 是 GUI 展示口径，无记忆与读不到在界面上是
        同一件事）。需要区分"读失败"的读改写链必须用 `_read_entries_strict`。
        """
        entries = self._read_entries_strict()
        return [] if entries is None else entries

    def load(self) -> list[dict[str, Any]]:
        """读取记忆列表（按时间倒序，最新在最前）。

        纯读：不加跨进程锁、也不等 record 的宽锁，只与"本地正在写盘"互斥一瞬间
        （见 `_read_entries`）。写入走原子替换，读者要么看到旧快照、要么看到新快照，
        永远不会看到半截文件。
        """
        return self._read_entries()

    def latest(self) -> dict[str, Any] | None:
        """获取最近一条记忆项。"""
        entries = self.load()
        return entries[0] if entries else None

    # ------------------------------------------------------------ 跨进程锁
    def _try_file_lock(self, qlock: QLockFile) -> bool:
        """尝试取跨进程锁；取不到只告警并返回 False，调用方必须放弃本次修改。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("主动陪伴记忆锁目录创建失败: %s (%s)", self.path.parent, exc)
            return False
        if qlock.tryLock(self._lock_timeout_ms):
            return True
        # 持锁方进程消失时 QLockFile 会按 PID 判定陈旧并回收，所以这里只可能是
        # 真的有人在写（或对方卡住）——不做无锁写，也不无限等。
        log.warning("主动陪伴记忆跨进程锁获取超时（%dms），本次不修改记忆: %s",
                    self._lock_timeout_ms, self.path)
        return False

    # ------------------------------------------------------------ 写
    def record(self, process: str, title: str, activity: str) -> None:
        """记录一条新的陪伴活动记忆（读→改→写整体跨进程互斥）。

        注意：title 参数仅用于保持调用签名兼容，**不会落盘**——窗口标题可能含
        文档名/网页标题等敏感信息，记忆只保留进程名与活动分类。

        **读失败则不修改**：读改写链拿不到当前条目（"读不到"）与"文件里没有条目"
        是两个事实。把前者当后者写下去，落盘的 payload 只含这一条新记录，盘上的
        旧记忆被整体覆写——这次丢的不是一条记忆，是全部。"""
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return
            try:
                entries = self._read_entries_strict()
                if entries is None:
                    log.warning("主动陪伴记忆读取失败，本次不修改记忆: %s", self.path)
                    return
                new_item = {
                    "ts": self._clock(),
                    "process": str(process or "").strip(),
                    "activity": str(activity or "").strip(),
                }
                entries.insert(0, new_item)
                entries = entries[: self.max_entries]
                self._write(entries)
            finally:
                qlock.unlock()

    def _write(self, entries: list[dict[str, Any]]) -> bool:
        """同目录唯一临时文件 + fsync + 原子替换落盘；返回这次是否真的落盘。

        - 临时文件与替换各自在**本地 I/O 窄锁**内：同一进程的读者不会一边握着
          读句柄一边让这次替换去抢同一个文件（Windows 上那会让 os.replace 报
          WinError 5，实测见模块 docstring 与 _IO_LOCKS 注释）；窄锁只包住
          **碰数据文件**的那一下——写/fsync 临时文件不碰数据文件，不拿锁；替换的
          退避重试也在锁外（`_retry_with_narrow_lock`），否则 GUI 读者的
          latest() 会被 20/40/80/160ms 的退避一起按住（实测 310ms）；
        - 临时文件用 ``tempfile.mkstemp``（O_EXCL）：名带随机段，同一进程的多个
          实例/线程、以及不同进程都不会写串同一个临时文件（旧的固定名
          `.json.tmp` 是所有写者共用一个文件）；
        - 替换前 fsync 文件：否则 rename 可能先于内容持久化，掉电后留下空档；
          父目录 fsync 不做——记忆是"最多 20 条的短期陪伴"，丢最后一条可接受，
          不值得每次记录多一次 open+fsync（口径同 proactive_limiter/slot_manager）；
        - 替换走 ``config.atomic_replace_with_retry``（每次尝试 attempts=1，重试
          节奏由本模块按同一口径排在锁外）：骑过 Windows 读句柄造成的瞬时共享
          冲突，与 ``Config.save`` 同一冲突判定；
        - 只动数据文件与自己的临时文件，绝不触碰旁路 `.lock`；
        - 任何出口都清掉临时文件（失败残留会污染配置目录）；
        - 失败记 warning（不像旧实现那样 ``except OSError: pass`` 把丢写藏起来）
          并返回 False，调用方据此判断"这次到底改没改到盘上"。"""
        payload = json.dumps({"entries": entries}, ensure_ascii=False, indent=2)
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
                # 只有**碰数据文件**的那一下才拿窄锁：写临时文件与它无关，
                # 没必要把 fsync 的时间转嫁给正在读的 GUI 线程。
                self._retry_with_narrow_lock(
                    lambda: atomic_replace_with_retry(tmp_path, self.path, attempts=1),
                    attempts=_REPLACE_ATTEMPTS)
            finally:
                tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            # 跨进程写入冲突（另一个实例/杀软占用）只能在这里被发现：记日志
            # 而不是像旧实现那样 except OSError: pass 把丢写彻底藏起来。
            log.warning("主动陪伴记忆写入失败: %s (%s)", self.path, exc)
            return False
        return True

    def _unlink(self) -> bool:
        """窄锁内、有界重试地删除数据文件；返回"文件是否已不存在"。

        为什么要重试：Windows 上 unlink 撞到"别的读者还开着这个文件"会报
        WinError 32（MSVCRT 共享模式不含 FILE_SHARE_DELETE）。实测：读者手柄开着
        时，修复前 clear 一次失败就返回 False，设置页随即弹"清空失败"——用户看到
        的是"没人写却清不掉"。读句柄只在一次 read_text 期间存在，重试即过。
        退避同样在锁外（见 `_retry_with_narrow_lock`）：设置页的 clear 也在 GUI
        线程上，"清空记忆"不该把 20/40/80/160ms 的等待转嫁给读者。

        只删自己的数据文件：绝不触碰旁路 `.lock`（那是别的进程正在用的跨进程锁）。
        文件本来就不存在（含"别的进程刚删完"）= 目标状态已达成 → True。

        存在性探测（`Path.is_file()` → `os.stat`）失败时返回 **False**：探测失败
        与"文件不存在"是两件事，此时"文件还在不在"是未知的。clear() 的返回值是
        设置页唯一能分辨"清了"与"没清"的依据，所以这里只可能是 False——降级成
        True 就是"报了已清空却还在"（用户看到文件/记忆原样躺着），上抛则从设置页
        的命令处理函数直接穿出去（`_on_pro_clear_memory` 里没有 try），连失败提示
        都没有。stat 会原样上抛的错误：EACCES/EIO/WinError 5/32 这类（pathlib 只吞
        ENOENT/ENOTDIR/EBADF/ELOOP）。
        """
        try:
            if not self.path.is_file():
                return True
        except OSError as exc:
            log.warning("主动陪伴记忆清空失败（存在性探测失败）: %s (%s)", self.path, exc)
            return False

        def remove() -> None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # 另一个进程刚删完：目标状态已达成

        try:
            self._retry_with_narrow_lock(remove, attempts=_UNLINK_ATTEMPTS)
        except OSError as exc:
            log.warning("主动陪伴记忆清空失败（重试 %d 次后仍失败）: %s (%s)",
                        _UNLINK_ATTEMPTS, self.path, exc)
            return False
        return True

    def clear(self) -> bool:
        """清空陪伴记忆（同样跨进程互斥），返回**这次是否真的清空了**。

        必须等 in-flight 的 record 落盘后再删：否则"清空"先返回、对方的旧快照
        随后落盘，记忆被复活（用户看到"清了还在"）。

        返回值是调用方（设置页）唯一能分辨"清了"与"没清"的依据：拿不到跨进程锁
        （别的进程正在写/卡住）、删除失败、或存在性探测失败（"文件还在不在"未知，
        见 `_unlink`）时一律返回 False，绝不静默假装成功。文件本来就不存在时返回
        True：对用户而言"没有记忆"与"已清空"是同一状态。

        实现选择：仍然删文件（而不是"原子替换成空 entries"）——产品侧已有断言要求
        "报了已清空就必须真的删掉文件"（tests/test_desktop_pet_features.py 的
        `test_clear_proactive_memory_reports_success_and_removes_file`）。删除放在
        本地 I/O 窄锁内并对瞬时共享冲突做有界重试（见 `_unlink`），既保住原子读
        （读者只会看到"旧快照"或"没有文件"），又不会被读者手柄误判成失败。
        """
        with self._lock:
            qlock = QLockFile(str(self._lock_file))
            if not self._try_file_lock(qlock):
                return False
            try:
                return self._unlink()
            finally:
                qlock.unlock()
