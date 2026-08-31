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

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap

from . import catalog

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
# stop() 后安排一次 sweep 的延迟（毫秒）。
_SWEEP_DELAY_MS = 2000


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

        self._current_image: QImage | None = None
        self._current_pixmap: QPixmap | None = None
        self._first_image: QImage | None = None
        self._first_pixmap: QPixmap | None = None
        # 首帧解码原子认领（N4）：warm_first_frame（后台）与 _decode_first_frame_sync
        # （前台）同一时间只有一个执行者。_first_frame_done 在 _first_image 写入后
        # set，供前台有界等待复用后台解码结果（零重复解码）。
        self._first_frame_lock = threading.Lock()
        self._first_frame_done = threading.Event()
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

        对仍存活的退役 reader 保留追踪（绝不静默丢弃），等待后续 sweep 或
        下次 cleanup/start 回收；其 ffmpeg 进程句柄已被 terminate，线程会在
        管道 EOF 后自行退出。
        """
        try:
            self.stop()
        except RuntimeError:
            pass  # QTimer 等 Qt 子对象已随 C++ 侧销毁
        self._reap_retired(join_timeout=_RECLAIM_JOIN_TIMEOUT)

    def _sweep_retired(self) -> None:
        """定时回收退役池：清理已退出的；对仍存活者二次 terminate + 有界 join，
        仍不退出则保留追踪并稍后再试。"""
        self._reap_retired(join_timeout=_SWEEP_JOIN_TIMEOUT)
        if any(r.thread.is_alive() for r in self._retired):
            QTimer.singleShot(_SWEEP_DELAY_MS, self._sweep_retired)

    def _reap_retired(self, join_timeout: float) -> None:
        """回收退役池：丢弃已退出线程；对仍存活者二次 terminate 其 ffmpeg 并
        有界 join；仍不退出则保留在池中（绝不静默丢弃追踪）。"""
        survivors = []
        for r in self._retired:
            if not r.thread.is_alive():
                try:
                    r.join(timeout=0)
                except Exception:
                    pass
                continue
            if r.proc is not None:
                self._terminate_proc(r.proc)
            r.thread.join(timeout=join_timeout)
            if r.thread.is_alive():
                survivors.append(r)
        self._retired = survivors

    def _drain_retired(self) -> bool:
        """start() 前清空退役池；返回 True 表示可安全启动新 reader。"""
        self._reap_retired(join_timeout=_RECLAIM_JOIN_TIMEOUT)
        return not self._retired

    def _enforce_retired_cap(self) -> None:
        """退役池超过上限时强制回收最旧的 reader（其 ffmpeg 已 terminate，join
        快速返回）；病态场景（join 超时仍存活）停止回收并保留追踪。"""
        while len(self._retired) > _MAX_RETIRED_READERS:
            oldest = self._retired[0]
            if oldest.proc is not None:
                self._terminate_proc(oldest.proc)
            oldest.thread.join(timeout=_RECLAIM_JOIN_TIMEOUT)
            if oldest.thread.is_alive():
                return  # 仍存活：保留追踪，池短暂超限（真实 reader 不会出现）
            self._retired.pop(0)

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen | None) -> None:
        """主动终止 ffmpeg 子进程（已退出则无操作）。"""
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
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

    def start(self) -> None:
        if self._running:
            return
        if imageio_ffmpeg is None:
            self.errorOccurred.emit(str(_IMPORT_ERROR or 'imageio_ffmpeg 不可用'))
            return

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
            return

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
            QTimer.singleShot(_SWEEP_DELAY_MS, self._sweep_retired)

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

    def _decode_first_qimage(self):
        """解码首帧为 QImage（线程安全：不触碰 QPixmap/QTimer）。

        返回 None 表示失败或依赖缺失；调用方负责填入 _first_image 等缓存。
        """
        if imageio_ffmpeg is None:
            return None
        gen = None
        try:
            gen = imageio_ffmpeg.read_frames(
                str(self.path),
                pix_fmt='rgba',
                bits_per_pixel=self._bpp * 8,
                input_params=['-c:v', 'libvpx-vp9'],
            )
            meta = next(gen)
            frame = next(gen)
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
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass

    def _decode_first_qimage_and_cache(self) -> None:
        """解码首帧并写入 _first_image 缓存（调用方须已持有 _first_frame_lock）。

        幂等：缓存已存在时直接返回，避免重复拉起 ffmpeg。
        """
        if self._first_image is not None:
            return
        img = self._decode_first_qimage()
        if img is not None:
            self._first_image = img
            self._first_frame_done.set()

    def _apply_first_frame(self) -> None:
        """把已缓存的 _first_image 应用到当前播放帧（仅主线程调用）。"""
        if self._first_image is None:
            return
        self._current_image = self._first_image
        self._current_pixmap = QPixmap.fromImage(self._first_image)
        self._first_pixmap = self._current_pixmap

    def _decode_first_frame_sync(self) -> None:
        """同步解码首帧（主线程），保证 jumpToFrame(0)/currentPixmap 在 start() 前有画面。

        与后台 warm_first_frame 原子互斥：同一时间只有一个首帧解码执行者。
        认领失败说明后台预热正在解码：最多等待 _FIRST_FRAME_SYNC_WAIT_MS
        （后台完成则直接用其缓存，零重复解码）；超时则放弃等待直接自行解码，
        前台播放绝不被后台预热长时间卡住（代价是极端情况下短暂双解码）。
        """
        if self._first_frame_lock.acquire(blocking=False):
            try:
                self._decode_first_qimage_and_cache()
            finally:
                self._first_frame_lock.release()
        else:
            if not self._first_frame_done.wait(timeout=_FIRST_FRAME_SYNC_WAIT_MS / 1000.0):
                self._decode_first_qimage_and_cache()
        self._apply_first_frame()

    def warm_first_frame(self) -> None:
        """后台线程预解码首帧缓存（仅 QImage，线程安全）。

        首次播放某动画时 jumpToFrame(0) 需要首帧：有缓存则主线程零阻塞，
        避免点击瞬间同步 ffmpeg 解码造成卡顿，以及 Q 弹期间残留旧动画帧。

        原子认领（N4）：同一时间只有一个首帧解码执行者。认领失败（前台
        同步解码或并发预热正在进行）直接放弃——预热是尽力而为，不排队等待。
        """
        if self._first_image is not None or imageio_ffmpeg is None:
            return
        if not self._first_frame_lock.acquire(blocking=False):
            return
        try:
            self._decode_first_qimage_and_cache()
        finally:
            self._first_frame_lock.release()

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
