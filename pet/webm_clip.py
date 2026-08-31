# -*- coding: utf-8 -*-
"""
WebM-backed clip library（webm 主路线）。

使用 imageio-ffmpeg 自带的静态 ffmpeg 解码 640×360 透明 webm：
- read_frames(..., pix_fmt='rgba', bits_per_pixel=32, input_params=['-c:v','libvpx-vp9'])
  可正确保留 VP9 alpha，输出 RGBA 原始帧。
- imageio_ffmpeg 内部在 Windows 上使用 STARTUPINFO 隐藏控制台窗口，
  避免旧 ffmpeg 子进程方案导致的“窗口反复出现/消失”。

线程模型（B7 生命周期受控）：
- 后台 reader 线程只负责把 RGBA 字节放入有界队列；
- 主线程 QTimer 按视频 fps 从队列取帧，构造 QImage/QPixmap 并发出 frameChanged；
- 所有 Qt GUI 操作只发生在主线程。
- 同一 clip 最多 1 个 active reader + 有上限的退役 reader（_MAX_RETIRED_READERS）：
  stop() 主动 terminate 底层 ffmpeg 进程（_PopenCapture 捕获句柄），退役池超上限时
  强制回收最旧的；start() 前清空退役池，池内仍有存活 reader 时拒绝启动（防无上限累积）。
- cleanup() 对仍存活的退役 reader 保留追踪（绝不静默丢弃），等待后续 sweep 回收。
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import types
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from . import catalog
from . import bounds_precompute as bounds_mod

logger = logging.getLogger(__name__)

# 进程内元数据缓存：避免反复切换角色时重复调用 count_frames_and_secs
_META_CACHE: dict[str, tuple[int, float]] = {}

# 跨进程共享的元数据缓存文件：多开实例共享同一份，避免每个实例都拉起
# ffmpeg 探测 91 段动画。缓存以（文件 mtime + size）为失效依据。
_META_FILE_CACHE_PATH = Path(tempfile.gettempdir()) / "dsh-pet-media-meta-cache.json"
_META_FILE_CACHE: dict | None = None
_META_CACHE_LOCK = threading.Lock()

# 前台同步解码等待后台预热完成的有限时长（毫秒）：超过此时长前台放弃等待、
# 直接自行解码，保证 GUI 线程不被后台首帧预热长时间卡住（N4）。
_FIRST_FRAME_SYNC_WAIT_MS = 120

# ------------------------------------------------------------ reader 生命周期（B7）
# 同一 clip 允许的退役 reader 上限：1 个 active reader + 1 个 retiring reader。
_MAX_RETIRED_READERS = 1
# 强制回收最旧退役 reader 时的有界 join 时长（秒）；真实 reader 的 ffmpeg 已被
# terminate，join 通常在毫秒级返回，此值只作为病态场景（卡死）的上限。
_RECLAIM_JOIN_TIMEOUT = 0.5
# 定时 sweep 对仍存活退役 reader 的 join 时长（秒）。
_SWEEP_JOIN_TIMEOUT = 0.2
# terminate 后等待进程退出的有界时长（秒）；超时则 kill 强杀（P2）。
# 在 GUI 线程调用时（stop/_reap_retired）该值同时是 GUI 阻塞上限，
# 正常进程 terminate 后毫秒级退出，此值只作为病态场景的兜底。
_PROC_TERMINATE_TIMEOUT = 0.5

# ------------------------------------------------------------ 孤儿 sweep 生命周期管理器（B7 审查 P2）
# 退役 reader 的回收由「独立生命周期管理器」持有：模块级注册表记录所有
# 退役池非空的 clip，模块级 lazy QTimer 周期回收。
#
# 为什么必须模块级持有：若 sweep timer 是 clip 的成员 QTimer，会形成
# 引用环（clip → timer → 连接 → clip），循环 GC 可能在 reader 线程仍在
# 收尾（finally/gen.close/进程 teardown）时回收整个 clip——clip 的属性
# （锁/队列/进程句柄）在 reader 线程使用中被释放，Windows 上原生崩溃。
# 模块级注册表强引用持有 clip，直到其退役池清空，杜绝该竞态；同时满足
# 「cleanup 后不残留调度」（cleanup 后 clip 自身不再安排任何 timer，
# 由管理器统一回收）与「cleanup 不丢追踪」（存活 reader 的记录被持有
# 到线程退出）。
_ORPHANED_CLIPS: "set[WebMClip]" = set()
_ORPHAN_LOCK = threading.Lock()
_orphan_timer: "QTimer | None" = None
_ORPHAN_SWEEP_DELAY_MS = 500
# 病态 reader 永不退出时的泄漏告警阈值（连续回收次数）。
_ORPHAN_LEAK_ATTEMPTS = 6


def _ensure_orphan_timer() -> "QTimer | None":
    """惰性创建模块级 sweep timer（须在 GUI 线程；无 QApplication 时返回 None）。"""
    global _orphan_timer
    if _orphan_timer is None:
        app = QApplication.instance()
        if app is None:
            return None
        _orphan_timer = QTimer()
        _orphan_timer.setSingleShot(True)
        _orphan_timer.setInterval(_ORPHAN_SWEEP_DELAY_MS)
        _orphan_timer.timeout.connect(_reap_orphaned_clips)
    return _orphan_timer


def _register_orphan(clip: "WebMClip") -> None:
    """把退役池非空的 clip 挂到模块级注册表（强引用持有，防 GC 竞态）。"""
    with _ORPHAN_LOCK:
        _ORPHANED_CLIPS.add(clip)
    timer = _ensure_orphan_timer()
    if timer is not None:
        timer.start()


def _unregister_orphan(clip: "WebMClip") -> None:
    with _ORPHAN_LOCK:
        _ORPHANED_CLIPS.discard(clip)


def _reap_orphaned_clips() -> None:
    """模块级回收：对注册的 clip 做有界回收；退役池清空者移出注册表。

    病态 reader 多次回收仍不退出时记录泄漏告警（而不是无限静默重试）。
    """
    with _ORPHAN_LOCK:
        holders = list(_ORPHANED_CLIPS)
        for clip in holders:
            try:
                clip._reap_retired(join_timeout=_SWEEP_JOIN_TIMEOUT)
            except Exception:
                pass  # clip 已销毁等：交由 GC 兜底
            if not clip._retired:
                _ORPHANED_CLIPS.discard(clip)
        if _ORPHANED_CLIPS:
            for clip in _ORPHANED_CLIPS:
                clip._orphan_reap_count = getattr(clip, '_orphan_reap_count', 0) + 1
                if clip._orphan_reap_count >= _ORPHAN_LEAK_ATTEMPTS:
                    logger.warning(
                        'webm 退役 reader 多次回收仍存活（疑似泄漏，进程已 terminate）: %s',
                        clip.path,
                    )
            timer = _ensure_orphan_timer()
            if timer is not None:
                timer.start()


class _Reader:
    """一个 reader 线程 + 其持有的底层 ffmpeg 进程句柄。"""

    __slots__ = ("thread", "proc")

    def __init__(self, thread: threading.Thread, proc: subprocess.Popen | None) -> None:
        self.thread = thread
        self.proc = proc

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)


class _PopenCapture:
    """跨线程透明的 subprocess.Popen 包装，用于捕获 reader 线程拉起的 ffmpeg 进程句柄。

    - 安装一次（幂等、加锁防并发）：把 imageio_ffmpeg._io 模块内的 `subprocess`
      引用替换为一个代理模块（其余属性原样透传、仅 Popen 换成 _wrapped），
      只影响 imageio_ffmpeg 的 Popen 调用；进程内全局 subprocess.Popen 保持原样
      ——不能全局替换，否则会破坏 `class X(subprocess.Popen)` 这类继承用法
      （asyncio.windows_utils 就是这样，会导致 unittest.mock 导入失败）。
    - 仅「进入 capture 上下文」的线程（reader 线程）会把新建进程记入自己的
      _procs 列表并可即时回调（on_process）；其余线程完全无感。
    - 即时回调让 stop() 在 reader 尚处于 ffmpeg 头部解析（可能卡住）时也能
      拿到进程句柄并主动 terminate，而不是等 reader 自己退。
    """

    _install_lock = threading.Lock()
    _installed = False
    _real_popen = subprocess.Popen
    _local = threading.local()

    def __init__(self, on_process=None) -> None:
        self._on_process = on_process
        self._procs: list[subprocess.Popen] = []
        self._prev: _PopenCapture | None = None

    def __enter__(self) -> "_PopenCapture":
        self._install()
        self._prev = getattr(self._local, "capture", None)
        self._local.capture = self
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._prev is None:
            try:
                del self._local.capture
            except AttributeError:
                pass
        else:
            self._local.capture = self._prev
        return False

    @property
    def process(self) -> subprocess.Popen | None:
        """最近拉起的进程（解码进程；ffmpeg exe 探测进程在其之前）。"""
        return self._procs[-1] if self._procs else None

    @classmethod
    def _install(cls) -> None:
        if cls._installed:
            return
        with cls._install_lock:
            if cls._installed:
                return
            cls._real_popen = subprocess.Popen
            io_mod = sys.modules.get("imageio_ffmpeg._io")
            if io_mod is not None and getattr(io_mod, "subprocess", None) is subprocess:
                proxy = types.ModuleType("subprocess")
                proxy.__dict__.update(subprocess.__dict__)
                proxy.Popen = cls._wrapped  # type: ignore[assignment]
                io_mod.subprocess = proxy
            cls._installed = True

    @classmethod
    def _wrapped(cls, *args, **kwargs):
        proc = cls._real_popen(*args, **kwargs)
        state = getattr(cls._local, "capture", None)
        if state is not None:
            state._procs.append(proc)
            if state._on_process is not None:
                try:
                    state._on_process(proc, args[0] if args else None)
                except Exception:
                    pass
        return proc


def _load_meta_file_cache() -> dict:
    try:
        raw = json.loads(_META_FILE_CACHE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _get_meta_file_cache() -> dict:
    global _META_FILE_CACHE
    if _META_FILE_CACHE is None:
        _META_FILE_CACHE = _load_meta_file_cache()
    return _META_FILE_CACHE


def _save_meta_file_cache_entry(key: str, frames: int, duration: float) -> None:
    global _META_FILE_CACHE
    try:
        with _META_CACHE_LOCK:
            cache = _get_meta_file_cache()
            cache[key] = {
                "frames": frames,
                "duration": duration,
            }
            # tmp 名带 PID：共享临时目录下防符号链接预占攻击与多实例互抢
            tmp = _META_FILE_CACHE_PATH.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(_META_FILE_CACHE_PATH)
    except OSError:
        pass

try:
    import imageio_ffmpeg
except Exception as exc:  # pragma: no cover - 依赖缺失时无法使用 webm 路线
    imageio_ffmpeg = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class WebMClip(QObject):
    """与窗口层期望的媒体播放器接口兼容。"""

    available = imageio_ffmpeg is not None

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._w = catalog.CANVAS_W
        self._h = catalog.CANVAS_H
        self._bpp = 4  # RGBA

        # 元数据（惰性填充；由 MovieLibrary 并行 warm 或首次使用时读取）
        self._frame_count = 0
        self._duration = 0.0
        self._fps = 24.0
        self.playback_speed = 1.0

        # 播放状态
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        # 当前 active reader 持有的 ffmpeg 进程句柄（_reader_lock 保护，reader 线程
        # 注册、GUI 线程 stop 时读取/清空并 terminate）。
        self._reader_proc: subprocess.Popen | None = None
        self._reader_lock = threading.Lock()
        # reader 已拉起并登记 ffmpeg 进程（或确定无进程）的信号；每轮 start() 重建。
        self._reader_ready = threading.Event()
        # 退役 reader 池（有硬上限）：thread + 其 ffmpeg 进程句柄的记录列表。
        self._retired: list[_Reader] = []
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._timer_interval())
        self._timer.timeout.connect(self._poll)
        # cleanup 后 clip 终结：不再启动新 reader；退役池回收由模块级
        # 生命周期管理器持有（见模块头注释，P2）。
        self._cleaned = False

        self._current_image: QImage | None = None
        self._current_pixmap: QPixmap | None = None
        self._first_image: QImage | None = None
        self._first_pixmap: QPixmap | None = None
        # 首帧解码原子认领（N4）：warm_first_frame（后台）与 _decode_first_frame_sync
        # （前台）同一时间只有一个执行者。_first_frame_done 在 _first_image 写入后
        # set，供前台有界等待复用后台解码结果（零重复解码）。
        self._first_frame_lock = threading.Lock()
        self._first_frame_done = threading.Event()
        # 首帧解码生命周期（P1-2）：与播放 reader 同一回收体系。
        # _first_frame_gen 是首帧解码代次：cancel_first_frame_warm/cleanup 时自增，
        # 使在飞解码的结果作废（不写入缓存）；_first_frame_procs 登记在飞首帧
        # 解码拉起的 ffmpeg 进程句柄（_reader_lock 保护），取消/cleanup 时主动
        # terminate，隐藏/切角色后不再有不受控的后台 ffmpeg 存活。
        self._first_frame_gen = 0
        self._first_frame_procs: set = set()
        # 动画 bounds 预计算（B14）：与首帧解码同款的 锁 + 代次 + 进程登记
        # 生命周期（互不复用同一把锁/代次，两条解码任务互不干扰；取消/回收
        # 均走 _reader_lock 保护登记集合）。结果按 (mirrored, scale, dpr) 键
        # 整包原子提交——绝不提交半成品，运行时才可安全地把缓存当完整 union。
        self._bounds_lock = threading.Lock()
        self._bounds_gen = 0
        self._bounds_procs: set = set()
        self._bounds_cache: dict[tuple, bounds_mod.AnimBounds] = {}
        self._frame_index = 0
        self._ended_fired = False
        self._running = False
        self._generation = 0

        self.destroyed.connect(self._on_destroyed)

    def _on_destroyed(self) -> None:
        self.cleanup()

    def __del__(self) -> None:
        try:
            self.cleanup()
        except RuntimeError:
            pass  # 解释器退出期 Qt C++ 对象可能已先销毁，此时无需清理

    def cleanup(self) -> None:
        """销毁/清理：terminate active reader 的 ffmpeg，退役并回收。

        对仍存活的退役 reader 保留追踪（绝不静默丢弃）：clip 及其 _Reader
        记录交由模块级生命周期管理器持有（_ORPHANED_CLIPS），持续回收直到
        线程退出——绝不随 clip GC 丢弃追踪，也不与 reader 线程的收尾竞态
        （Windows 原生崩溃的根因，见模块头注释）。cleanup 后 clip 终结
        （_cleaned），自身不再安排任何 sweep/timer。
        """
        self._cleaned = True
        self.cancel_first_frame_warm()
        self.cancel_bounds_warm()
        try:
            self.stop()
        except RuntimeError:
            pass  # QTimer 等 Qt 子对象已随 C++ 侧销毁
        self._reap_retired(join_timeout=_RECLAIM_JOIN_TIMEOUT)
        if self._retired:
            # 仍存活 reader：保持模块级持有，由管理器继续回收
            _register_orphan(self)
        else:
            _unregister_orphan(self)

    def _sweep_retired(self) -> None:
        """兼容别名：由模块级生命周期管理器驱动（_reap_orphaned_clips）。"""
        self._reap_retired(join_timeout=_SWEEP_JOIN_TIMEOUT)

    def _reap_retired(self, join_timeout: float) -> None:
        """回收退役池：丢弃已退出线程；对仍存活者有界 join；仍不退出则保留
        在池中（绝不静默丢弃追踪），由模块级管理器持续重试。

        注意：不在此处二次 terminate / poll 退役 reader 的进程句柄——进程
        已由 stop()（terminate + kill 兜底）或 reader 自身 finally 终止；
        此处再触碰 proc 会与 reader 线程的 finally 收尾并发操作同一 Popen
        （Windows 上原生竞态崩溃）。
        """
        survivors = []
        for r in self._retired:
            if not r.thread.is_alive():
                try:
                    r.join(timeout=0)
                except Exception:
                    pass
                continue
            r.thread.join(timeout=join_timeout)
            if r.thread.is_alive():
                survivors.append(r)
        self._retired = survivors

    def _drain_retired(self) -> bool:
        """start() 前清空退役池；返回 True 表示可安全启动新 reader。"""
        self._reap_retired(join_timeout=_RECLAIM_JOIN_TIMEOUT)
        return not self._retired

    def _enforce_retired_cap(self) -> None:
        """退役池超过上限时强制回收最旧的 reader（join 快速返回）；病态场景
        （join 超时仍存活）停止回收并保留追踪（进程已终止，线程退出由管道
        EOF 驱动；不触碰 proc，理由同 _reap_retired）。"""
        while len(self._retired) > _MAX_RETIRED_READERS:
            oldest = self._retired[0]
            oldest.thread.join(timeout=_RECLAIM_JOIN_TIMEOUT)
            if oldest.thread.is_alive():
                return  # 仍存活：保留追踪，池短暂超限（真实 reader 不会出现）
            self._retired.pop(0)

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen | None,
                        timeout: float = _PROC_TERMINATE_TIMEOUT) -> None:
        """主动终止 ffmpeg 子进程并确认退出（P2）。

        顺序：terminate() → 有界 wait() → 超时 kill() → 再次 wait()。
        返回时进程要么已确认退出，要么（极端病态，kill 后仍存活）记录警告
        并保留句柄供后续 sweep 重试——绝不静默假设 terminate 后进程已退出。
        已退出或 None 进程为无操作。

        已知局限：只保证单个 Popen 句柄（父进程）的退出，不覆盖其派生的
        进程树。当前 ffmpeg 为单进程解码器、不派生子进程；若未来引入会
        派生工作子进程的解码器，需在此升级为进程树终止（Windows job
        object / 进程组 kill），本实现不负责该场景。
        """
        if proc is None:
            return
        try:
            if proc.poll() is not None:
                return
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        'ffmpeg 进程 terminate+kill 后仍存活（保留句柄供 sweep 重试）: pid=%s',
                        getattr(proc, 'pid', '?'),
                    )
        except Exception:
            pass

    # ------------------------------------------------------------ metadata
    def _ensure_meta(self) -> None:
        if self._duration > 0 or imageio_ffmpeg is None:
            return
        key = str(self.path)
        try:
            st = Path(key).stat()
            cache_key = f"{key}|{st.st_mtime_ns}|{st.st_size}"
        except OSError:
            cache_key = key
        cached = _META_CACHE.get(cache_key)
        if cached is not None:
            self._frame_count, self._duration = cached
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            return
        entry = _get_meta_file_cache().get(cache_key)
        if isinstance(entry, dict) and entry.get('frames') and entry.get('duration'):
            self._frame_count = int(entry['frames'])
            self._duration = float(entry['duration'])
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            _META_CACHE[cache_key] = (self._frame_count, self._duration)
            return
        try:
            frames, secs = imageio_ffmpeg.count_frames_and_secs(key)
            if frames and frames > 0:
                self._frame_count = int(frames)
            if secs and secs > 0:
                self._duration = float(secs)
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            _META_CACHE[cache_key] = (self._frame_count, self._duration)
            _save_meta_file_cache_entry(cache_key, self._frame_count, self._duration)
        except Exception as exc:
            logger.warning('webm 元数据读取失败 %s: %s', self.path, exc)
            # 保留默认值，后续 reader 会尝试从 read_frames 的 meta 补充

    def warm_meta(self) -> None:
        """预取元数据（可被线程池并行调用）。"""
        self._ensure_meta()

    def _timer_interval(self) -> int:
        if self._fps > 0:
            return max(1, int(round(1000 / (self._fps * self.playback_speed))))
        return max(1, int(round(catalog.FRAME_MS / self.playback_speed)))

    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._ensure_meta()
        return max(1, self._frame_count)

    def duration(self) -> float:
        if self._duration <= 0:
            self._ensure_meta()
        return self._duration / self.playback_speed if self._duration > 0 else 0.0

    def currentFrameNumber(self) -> int:
        return self._frame_index

    def currentTimeSeconds(self) -> float:
        if self._fps <= 0:
            return 0.0
        return self._frame_index / (self._fps * self.playback_speed)

    def currentPixmap(self):
        return self._current_pixmap

    # ------------------------------------------------------------ lifecycle
    def set_playback_speed(self, speed: float) -> None:
        self.playback_speed = max(0.1, float(speed))
        # _switch() 在 movie.start() 之前设置速率，不能只在 QTimer 已启动时更新。
        # 否则每个新 WebM 动画都会继续使用默认的 1x interval。
        self._timer.setInterval(self._timer_interval())

    def start(self) -> bool:
        """启动播放；返回 True 表示已启动（或已在播），False 表示本次启动被拒绝。

        拒绝原因：imageio_ffmpeg 不可用、clip 已 cleanup（终结），或退役
        reader 池未清空（存在存活 reader，B7 硬上限）。

        调用方（窗口层）必须把 False 当作「动画没有在播」并执行明确降级
        （回退上一动画/待机、安排重试），绝不能把动画状态切换与播放启动
        当成同一件事（B7 审查 P1-1）。
        """
        if self._running:
            return True
        if self._cleaned:
            logger.warning('clip 已 cleanup，拒绝启动 reader: %s', self.path)
            return False
        if imageio_ffmpeg is None:
            self.errorOccurred.emit(str(_IMPORT_ERROR or 'imageio_ffmpeg 不可用'))
            return False

        # 上一轮 natural end 残留的 active 线程先退役（其 ffmpeg 已自行退出）
        with self._reader_lock:
            stale_thread = self._thread
            stale_proc = self._reader_proc
            self._thread = None
            self._reader_proc = None
        if stale_thread is not None:
            self._retired.append(_Reader(thread=stale_thread, proc=stale_proc))
        # 清空退役池（丢弃已退出者；对存活者二次 terminate + 有界 join）。
        # 池内仍有存活 reader（进程已终止仍不退出/卡死）时拒绝启动新 reader，
        # 防止快速切换/损坏素材场景下线程与 ffmpeg 子进程无上限累积（B7）。
        if not self._drain_retired():
            logger.warning(
                'webm reader 退役池未清空（存在存活 reader），拒绝启动新 reader: %s',
                self.path,
            )
            return False

        # 在 GUI 线程读取真实 fps 后再启动 QTimer，保证新动画的实际帧率
        # 与播放速率计算一致；reader 线程只负责解码和入队。
        self._ensure_meta()
        self._timer.setInterval(self._timer_interval())
        stop_evt = threading.Event()
        ready_evt = threading.Event()
        self._stop_evt = stop_evt
        self._reader_ready = ready_evt
        self._queue = queue.Queue(maxsize=8)
        self._frame_index = 0
        self._ended_fired = False
        self._running = True
        self._generation += 1
        gen_id = self._generation

        thread = threading.Thread(
            target=self._reader,
            args=(stop_evt, gen_id, ready_evt),
            daemon=True,
            name=f'webm-reader-{gen_id}',
        )
        with self._reader_lock:
            self._thread = thread
        thread.start()
        self._timer.start()
        return True

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        stop_evt = self._stop_evt
        if stop_evt is not None:
            stop_evt.set()
        # 主动 terminate 底层 ffmpeg：不能只是 set 事件等 reader 自己退（B7）。
        # 正在阻塞读管道/解析头部的 reader 会因进程终止而立即解除阻塞退出。
        thread = None
        proc = None
        with self._reader_lock:
            thread = self._thread
            proc = self._reader_proc
            self._thread = None
            self._reader_proc = None
        if proc is not None:
            self._terminate_proc(proc)
        if thread is not None:
            self._retired.append(_Reader(thread=thread, proc=proc))
            self._enforce_retired_cap()
            # 退役池回收交给模块级生命周期管理器（P2）：持有 clip 直到退役
            # reader 全部退出，既避免 GC 与 reader 收尾竞态，也保证 cleanup
            # 后不残留本 clip 的 timer 调度。
            if not self._cleaned and self._retired:
                _register_orphan(self)

    def jumpToFrame(self, frame_index: int) -> bool:
        # 本项目只需要回到首帧；完整 seek 通过重启 reader + 丢弃帧实现。
        if frame_index <= 0:
            self.stop()
            self._frame_index = 0
            if self._first_image is not None:
                # 首帧已缓存（后台 warm_first_frame 或上次同步解码）：
                # 主线程直接转 QPixmap，零阻塞、无旧帧残留窗口。
                self._current_image = self._first_image
                self._current_pixmap = QPixmap.fromImage(self._first_image)
            else:
                self._current_image = None
                self._current_pixmap = None
                self._decode_first_frame_sync()
            return True
        return False

    def _decode_first_qimage(self, gen: int | None = None):
        """解码首帧为 QImage（线程安全：不触碰 QPixmap/QTimer）。

        返回 None 表示失败、依赖缺失，或解码期间已被取消（换代）；调用方
        负责经 _store_first_frame（持锁提交）写入 _first_image 缓存。

        P1-2 生命周期：解码拉起的 ffmpeg 进程经 _PopenCapture 登记到
        _first_frame_procs（_reader_lock 保护），cancel_first_frame_warm/
        cleanup 可主动 terminate；gen 是本次解码认领的首帧代次，解码期间
        代次被换代则结果作废返回 None。未显式传入 gen 时以解码开始时的
        代次为准（所有真实调用路径均显式传入，此为防御默认）。
        """
        if imageio_ffmpeg is None:
            return None
        if gen is None:
            gen = self._first_frame_gen
        proc = None
        g = None
        try:
            def _register(p: subprocess.Popen, argv) -> None:
                """解码进程 Popen 一拉起即登记（可被取消/cleanup 主动 terminate）。

                登记在 _reader_lock 内复查代次与 cleanup 状态（B7 复审 R2）：
                取消（cancel_first_frame_warm 换代/清集合）与登记之间存在
                竞态窗口——「Popen 已创建、登记尚未完成」时取消会读到空集合；
                迟到的登记必须自检 stale，已取消的进程绝不漏进集合，而是
                立即自终止，保证取消后不再有不受控的 ffmpeg 存活。
                """
                nonlocal proc
                if not (isinstance(argv, list) and "-i" in argv):
                    return  # ffmpeg exe 探测等非解码进程：忽略
                proc = p
                with self._reader_lock:
                    stale = self._cleaned or gen != self._first_frame_gen
                    if not stale:
                        self._first_frame_procs.add(p)
                if stale:
                    self._terminate_proc(p)

            with _PopenCapture(on_process=_register):
                g = imageio_ffmpeg.read_frames(
                    str(self.path),
                    pix_fmt='rgba',
                    bits_per_pixel=self._bpp * 8,
                    input_params=['-c:v', 'libvpx-vp9'],
                )
                meta = next(g)  # ffmpeg 进程在此拉起；capture 即时登记句柄
                frame = next(g)
            if gen is not None and gen != self._first_frame_gen:
                return None  # 解码期间被取消/换代：结果作废
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))
            expect = self._w * self._h * self._bpp
            if len(frame) == expect:
                img = QImage(frame, self._w, self._h, self._w * self._bpp,
                             QImage.Format.Format_RGBA8888)
                if not img.isNull():
                    return img.copy()
            return None
        except Exception as exc:
            logger.warning('webm 首帧解码失败 %s: %s', self.path, exc)
            return None
        finally:
            if proc is not None:
                with self._reader_lock:
                    self._first_frame_procs.discard(proc)
            if g is not None:
                try:
                    g.close()
                except Exception:
                    pass

    def _store_first_frame(self, img) -> None:
        """把解码结果写入 _first_image 缓存（调用方须已持有 _first_frame_lock）。

        幂等：缓存已存在则跳过；写入后 set _first_frame_done。
        """
        if img is None:
            return
        if self._first_image is None:
            self._first_image = img
            self._first_frame_done.set()

    def _decode_first_qimage_and_cache(self, gen: int | None = None) -> None:
        """解码首帧并写入 _first_image 缓存（调用方须已持有 _first_frame_lock）。

        幂等：缓存已存在时直接返回，避免重复拉起 ffmpeg。
        gen：warm 传入的首帧代次；解码期间被换代（cancel_first_frame_warm/
        cleanup）则丢弃结果，不污染缓存（P1-2）。
        """
        if self._first_image is not None:
            return
        img = self._decode_first_qimage(gen=gen)
        if gen is not None and gen != self._first_frame_gen:
            return  # 已被取消/换代：结果作废，不提交
        self._store_first_frame(img)

    def _apply_first_frame(self) -> None:
        """把已缓存的 _first_image 应用到当前播放帧（仅主线程调用）。"""
        if self._first_image is None:
            return
        self._current_image = self._first_image
        self._current_pixmap = QPixmap.fromImage(self._first_image)
        self._first_pixmap = self._current_pixmap

    def _apply_first_frame_image(self, img) -> None:
        """把解码得到的首帧图像直接应用到当前播放帧（仅主线程调用）。

        不写入 _first_image 缓存：用于逃生口拿不到锁、无法经锁提交缓存的
        场景（P1-3 / B7 复审 R2——绝不做无锁 check-then-store，缓存提交
        只可能由持锁方完成）。
        """
        if img is None:
            return
        self._current_image = img
        self._current_pixmap = QPixmap.fromImage(img)
        self._first_pixmap = self._current_pixmap

    def _decode_first_frame_sync(self) -> None:
        """同步解码首帧（主线程），保证 jumpToFrame(0)/currentPixmap 在 start() 前有画面。

        与后台 warm_first_frame 原子互斥：同一时间只有一个首帧解码执行者。
        认领失败说明后台预热正在解码：最多等待 _FIRST_FRAME_SYNC_WAIT_MS
        （后台完成则直接用其缓存，零重复解码）；超时则放弃等待直接自行解码，
        前台播放绝不被后台预热长时间卡住（代价是极端情况下短暂双解码）。

        逃生口（超时自行解码）允许与后台双解码，但缓存提交单胜者化
        （P1-3 / B7 复审 R2）：始终经锁提交，拿不到锁就放弃写缓存、只把
        图像直接应用到当前画面——绝不无锁 check-then-store（两个逃生提交者
        并发时缓存胜者取决于调度顺序，且后台完成后的幂等检查会让先写入者
        永久决定缓存）。

        P1-2 / B7 复审 R2：同步路径在进入时捕获首帧代次并贯穿解码与提交，
        在飞解码被取消（换代）后结果作废，不污染缓存——与后台 warm 同等
        的代次取消语义。
        """
        gen = self._first_frame_gen
        if self._first_frame_lock.acquire(blocking=False):
            try:
                self._decode_first_qimage_and_cache(gen=gen)
            finally:
                self._first_frame_lock.release()
        else:
            if not self._first_frame_done.wait(timeout=_FIRST_FRAME_SYNC_WAIT_MS / 1000.0):
                img = self._decode_first_qimage(gen=gen)
                self._commit_first_frame_escape(img, gen=gen)
        self._apply_first_frame()

    def _commit_first_frame_escape(self, img, gen: int | None = None) -> None:
        """逃生口（未持锁）的首帧缓存提交（P1-3 / B7 复审 R2）。

        只允许两种结果：
        1. 拿到锁：经 _store_first_frame 幂等提交（与后台写真正互斥）；
        2. 拿不到锁（后台仍持锁卡住）：放弃写缓存，把本帧直接应用到当前
           画面（主线程已拿到可显示首帧）——缓存提交只可能由持锁方完成，
           明确单胜者，绝不在锁外触碰 _first_image。

        绝不在逃生路径上阻塞等待锁：后台真卡死时持锁不释放，等待会把
        「前台绝不被后台预热长时间卡住」的承诺重新变成 GUI 冻结。
        gen：本次解码认领的首帧代次；解码期间被取消（换代）则结果作废。
        """
        if img is None:
            return
        if gen is not None and gen != self._first_frame_gen:
            return  # 解码期间被取消/换代：结果作废，不提交（P1-2）
        if self._first_frame_lock.acquire(blocking=False):
            try:
                self._store_first_frame(img)
            finally:
                self._first_frame_lock.release()
        else:
            self._apply_first_frame_image(img)

    def warm_first_frame(self) -> None:
        """后台线程预解码首帧缓存（仅 QImage，线程安全）。

        首次播放某动画时 jumpToFrame(0) 需要首帧：有缓存则主线程零阻塞，
        避免点击瞬间同步 ffmpeg 解码造成卡顿，以及 Q 弹期间残留旧动画帧。

        原子认领（N4）：同一时间只有一个首帧解码执行者。认领失败（前台
        同步解码或并发预热正在进行）直接放弃——预热是尽力而为，不排队等待。

        P1-2：预热解码纳入生命周期回收——解码拉起的 ffmpeg 登记到
        _first_frame_procs，cancel_first_frame_warm/cleanup 可主动 terminate；
        解码代次在认领时捕获，取消后结果作废不写入缓存。cleanup 后不再预热。
        """
        if self._first_image is not None or imageio_ffmpeg is None or self._cleaned:
            return
        if not self._first_frame_lock.acquire(blocking=False):
            return
        try:
            gen = self._first_frame_gen
            self._decode_first_qimage_and_cache(gen=gen)
        finally:
            self._first_frame_lock.release()

    def cancel_first_frame_warm(self) -> None:
        """取消在飞的首帧预热（P1-2）：换代使在飞解码结果作废，并主动
        terminate 其 ffmpeg 进程。

        由 cleanup()（clip 销毁）与 MovieLibrary.pause_warm()（隐藏/切角色）
        调用。之后新的 warm_first_frame 仍可重新预热（代次自增，非终态）。

        换代与集合读取/清空在 _reader_lock 内原子完成（B7 复审 R2）：登记
        回调也在该锁内复查代次——「Popen 已创建、登记尚未完成」窗口内取消
        时，迟到的登记会看到换代并自终止，已取消进程绝不漏进集合。
        """
        with self._reader_lock:
            self._first_frame_gen += 1
            procs = list(self._first_frame_procs)
            self._first_frame_procs.clear()
        for p in procs:
            self._terminate_proc(p)

    # ------------------------------------------------------------ bounds 预计算（B14）
    def warm_bounds(self, mirrored: bool, scale: float, dpr: float,
                    has_text: bool = False) -> None:
        """后台线程预计算整个动画的可见 bounds（锁 + 代次，与 warm_first_frame
        同款原子认领）。

        - 同一时间只有一个执行者：认领失败（并发 warm 或解码进行中）直接放弃，
          不排队等待——预热是尽力而为；
        - 解码拉起的 ffmpeg 进程登记进 _bounds_procs（_reader_lock 保护，
          登记回调在锁内复查代次，B7 复审 R2 同款），cancel_bounds_warm /
          cleanup 可主动 terminate；
        - 结果按 (mirrored, scale, dpr) 键整包原子提交（全部帧算完才写入
          _bounds_cache），被取消/换代则作废不提交——运行时把缓存当完整
          union 使用才是安全的。

        mirrored：是否按朝向镜像（facing=right 且非 no_mirror）；scale/dpr
        与窗口渲染参数一致；has_text：文字动画标记（text_clips.json）。
        """
        if self._cleaned or imageio_ffmpeg is None:
            return
        key = (bool(mirrored), float(scale), float(dpr))
        if key in self._bounds_cache:
            return
        if not self._bounds_lock.acquire(blocking=False):
            return
        try:
            if key in self._bounds_cache:
                return
            gen = self._bounds_gen
            data = self._compute_bounds(gen, key, bool(has_text))
            if data is not None and gen == self._bounds_gen:
                self._bounds_cache[key] = data
        finally:
            self._bounds_lock.release()

    def _compute_bounds(self, gen: int, key: tuple, has_text: bool):
        """解码全部帧并逐帧计算窗口局部 bounds（线程安全：只用 QImage）。

        管线（bounds_mod.frame_window_bounds）与运行时 _rebuild_frame →
        _sync_mask 完全同源，结果逐位一致（差分测试锁定）。解码期间被
        换代/cleanup 则返回 None（结果作废，不污染缓存）。
        """
        mirrored, scale, dpr = key
        proc = None
        g = None
        flat = bounds_mod.empty_flat(0)
        union = None
        try:
            def _register(p: subprocess.Popen, argv) -> None:
                """解码进程 Popen 一拉起即回调：登记句柄供取消/cleanup 主动
                terminate；在锁内复查代次——取消后迟到的登记必须自终止，
                已取消进程绝不漏进集合（B7 复审 R2 同款）。"""
                nonlocal proc
                if not (isinstance(argv, list) and "-i" in argv):
                    return  # ffmpeg exe 探测等非解码进程：忽略
                proc = p
                with self._reader_lock:
                    stale = self._cleaned or gen != self._bounds_gen
                    if not stale:
                        self._bounds_procs.add(p)
                if stale:
                    self._terminate_proc(p)

            with _PopenCapture(on_process=_register):
                g = imageio_ffmpeg.read_frames(
                    str(self.path),
                    pix_fmt='rgba',
                    bits_per_pixel=self._bpp * 8,
                    input_params=['-c:v', 'libvpx-vp9'],
                )
                meta = next(g)  # ffmpeg 进程在此拉起；capture 即时登记句柄
                w, h = self._w, self._h
                expect = w * h * self._bpp
                for frame_data in g:
                    if self._cleaned or gen != self._bounds_gen:
                        return None  # 解码期间被取消/换代：结果作废
                    if len(frame_data) != expect:
                        continue
                    img = QImage(frame_data, w, h, w * self._bpp,
                                 QImage.Format.Format_RGBA8888)
                    if img.isNull():
                        continue
                    rect = bounds_mod.frame_window_bounds(
                        img, mirrored=mirrored, scale=scale, dpr=dpr
                    )
                    if rect.isEmpty():
                        flat.extend((-1, -1, -1, -1))
                    else:
                        flat.extend((rect.x(), rect.y(), rect.right(), rect.bottom()))
                        union = rect if union is None else union.united(rect)
            if gen != self._bounds_gen:
                return None
            frame_count = len(flat) // 4
            if frame_count == 0:
                return None
            u = union if union is not None else QRect()
            feet = QPoint(u.center().x(), u.bottom()) if not u.isEmpty() else QPoint(0, 0)
            return bounds_mod.AnimBounds(frame_count, flat, u, feet, has_text)
        except Exception as exc:
            logger.warning('webm bounds 预计算失败 %s: %s', self.path, exc)
            return None
        finally:
            if proc is not None:
                with self._reader_lock:
                    self._bounds_procs.discard(proc)
            if g is not None:
                try:
                    g.close()
                except Exception:
                    pass

    def bounds_data(self, mirrored: bool, scale: float, dpr: float):
        """指定 (mirrored, scale, dpr) 键的完整 bounds 数据；未预计算返回 None。"""
        return self._bounds_cache.get((bool(mirrored), float(scale), float(dpr)))

    def bounds_rect(self, mirrored: bool, scale: float, dpr: float,
                    frame_n: int | None) -> QRect | None:
        """运行时逐帧查询：当前帧的窗口局部可见 bounds。

        命中返回 QRect（空帧为 QRect()，与画布扫描一致）；未预计算或帧号
        越界返回 None——调用方（_sync_mask）回落到现有逐帧扫描，行为不变。
        """
        data = self.bounds_data(mirrored, scale, dpr)
        if data is None or frame_n is None:
            return None
        return data.frame_rect(int(frame_n))

    def bounds_union(self, mirrored: bool, scale: float, dpr: float) -> QRect | None:
        """预计算的整段动画 union 可见 bounds（窗口局部坐标）；未预计算返回 None。

        仅当该键整包预计算完成后存在（原子提交），因此可安全地直接作为
        碰撞稳定体边界使用，不会出现半成品下界（漏判）。
        """
        data = self.bounds_data(mirrored, scale, dpr)
        if data is None:
            return None
        return QRect(data.union)

    def cancel_bounds_warm(self) -> None:
        """取消在飞 bounds 预计算：换代使在飞解码结果作废，并主动 terminate
        其 ffmpeg 进程。

        由 cleanup()（clip 销毁）与 MovieLibrary.pause_warm()（隐藏/切角色）
        调用。之后新的 warm_bounds 仍可重新预计算（代次自增，非终态）。
        换代与集合读取/清空在 _reader_lock 内原子完成（与首帧同款 R2 语义）。
        """
        with self._reader_lock:
            self._bounds_gen += 1
            procs = list(self._bounds_procs)
            self._bounds_procs.clear()
        for p in procs:
            self._terminate_proc(p)

    # ------------------------------------------------------------ reader
    def _reader(self, stop_evt: threading.Event, generation: int,
                ready_evt: threading.Event | None = None) -> None:
        gen = None
        proc = None
        try:
            q = self._queue

            def _register(p: subprocess.Popen, argv) -> None:
                """解码进程 Popen 一拉起即回调（可能发生在头部解析阻塞期间）：
                登记进程句柄，让 stop() 能立即 terminate；若本轮已被 stop/换代，
                则立即自终止。

                只认解码进程（argv 含 '-i <path>'）：imageio 的 ffmpeg exe 探测
                （ffmpeg -version）也在 reader 线程上拉起，不能登记/终止，否则
                探测会被误杀导致 get_ffmpeg_exe 失败。
                """
                nonlocal proc
                if not (isinstance(argv, list) and "-i" in argv):
                    return  # ffmpeg exe 探测等非解码进程：忽略
                proc = p
                stale = stop_evt.is_set() or self._generation != generation
                if not stale:
                    with self._reader_lock:
                        if not stop_evt.is_set() and self._generation == generation:
                            self._reader_proc = p
                if stale:
                    self._terminate_proc(p)

            with _PopenCapture(on_process=_register) as capture:
                gen = imageio_ffmpeg.read_frames(
                    str(self.path),
                    pix_fmt='rgba',
                    bits_per_pixel=self._bpp * 8,
                    input_params=['-c:v', 'libvpx-vp9'],
                )
                meta = next(gen)  # ffmpeg 进程在此拉起；capture 即时登记句柄
                if proc is None:
                    proc = capture.process
            if proc is not None:
                # 兜底登记：capture 即时回调已覆盖，此处仅防异常路径遗漏
                with self._reader_lock:
                    if not stop_evt.is_set() and self._generation == generation:
                        self._reader_proc = proc
            if ready_evt is not None:
                ready_evt.set()
            if self._generation != generation:
                return
            # 用实际流信息修正元数据
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))

            for frame in gen:
                if stop_evt.is_set() or self._generation != generation:
                    break
                try:
                    q.put(frame, timeout=0.2)
                except queue.Full:
                    # 队列满说明 UI 消费不过来；丢弃这一帧，保持实时性
                    pass
            # 正常播完时放入结束标记。主线程可能正忙（队列满、帧被丢弃），
            # 必须循环重试直到放入或收到停止信号；否则“最后一帧被丢弃且
            # 结束标记也丢失”会让上层永远等不到播完，动画链卡死在最后一帧。
            while not stop_evt.is_set() and self._generation == generation:
                try:
                    q.put(None, timeout=0.5)
                    break
                except queue.Full:
                    continue
        except Exception as exc:
            if self._generation != generation or stop_evt.is_set():
                return
            logger.exception('webm 解码失败: %s', self.path)
            self.errorOccurred.emit(str(exc))
            # 异常中断也要放入结束标记，避免动画链卡在最后一帧
            while not stop_evt.is_set() and self._generation == generation:
                try:
                    q.put(None, timeout=0.5)
                    break
                except queue.Full:
                    continue
        finally:
            with self._reader_lock:
                if proc is not None and self._reader_proc is proc:
                    self._reader_proc = None
            if proc is not None:
                # 兜底 terminate：正常情况下 stop() 已终止；自然播完/解码失败时
                # 进程已自行退出（poll()!=None），此处为无操作。
                self._terminate_proc(proc)
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass

    def _poll(self) -> None:
        """主线程按视频帧率逐帧取帧，不跳帧、不积压追帧。

        注意：不能一次清空队列只处理最新帧，否则会把中间帧丢弃，
        导致动画视觉上“快进”。这里每次只取最早的一帧。
        """
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            return

        if item is None:
            # 正常播完；若在处理最后一帧时已经由窗口层启动了下一个动画，
            # self._queue 已被替换，不会走到这里。
            if not self._ended_fired:
                self._ended_fired = True
                self._running = False
                self._timer.stop()
                self.finished.emit()
            return

        self._process_frame(item)

    def _process_frame(self, data: bytes) -> None:
        expect = self._w * self._h * self._bpp
        if len(data) != expect:
            logger.warning('webm 帧长度异常: got=%d expect=%d', len(data), expect)
            return
        img = QImage(data, self._w, self._h, self._w * self._bpp,
                     QImage.Format.Format_RGBA8888)
        if img.isNull():
            return
        self._current_image = img.copy()
        self._current_pixmap = QPixmap.fromImage(self._current_image)
        self._frame_index += 1
        self.frameChanged.emit(self._frame_index)
