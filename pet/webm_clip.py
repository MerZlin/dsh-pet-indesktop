# -*- coding: utf-8 -*-
"""
WebM-backed clip library（webm 主路线）。

使用 imageio-ffmpeg 自带的静态 ffmpeg 解码 640×360 透明 webm：
- read_frames(..., pix_fmt='rgba', bits_per_pixel=32, input_params=['-c:v','libvpx-vp9'])
  可正确保留 VP9 alpha，输出 RGBA 原始帧。
- imageio_ffmpeg 内部在 Windows 上使用 STARTUPINFO 隐藏控制台窗口，
  避免旧 ffmpeg 子进程方案导致的“窗口反复出现/消失”。

线程模型：
- 后台 reader 线程只负责把 RGBA 字节放入有界队列；
- 主线程 QTimer 按视频 fps 从队列取帧，构造 QImage/QPixmap 并发出 frameChanged；
- 所有 Qt GUI 操作只发生在主线程。
"""

from __future__ import annotations

import logging
import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap

from . import catalog

logger = logging.getLogger(__name__)

# 进程内元数据缓存：避免反复切换角色时重复调用 count_frames_and_secs
_META_CACHE: dict[str, tuple[int, float]] = {}

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

        # 播放状态
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(self._timer_interval())
        self._timer.timeout.connect(self._poll)

        self._current_image: QImage | None = None
        self._current_pixmap: QPixmap | None = None
        self._first_image: QImage | None = None
        self._first_pixmap: QPixmap | None = None
        self._frame_index = 0
        self._ended_fired = False
        self._running = False

    # ------------------------------------------------------------ metadata
    def _ensure_meta(self) -> None:
        if self._duration > 0 or imageio_ffmpeg is None:
            return
        key = str(self.path)
        cached = _META_CACHE.get(key)
        if cached is not None:
            self._frame_count, self._duration = cached
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            return
        try:
            frames, secs = imageio_ffmpeg.count_frames_and_secs(key)
            if frames and frames > 0:
                self._frame_count = int(frames)
            if secs and secs > 0:
                self._duration = float(secs)
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            _META_CACHE[key] = (self._frame_count, self._duration)
        except Exception as exc:
            logger.warning('webm 元数据读取失败 %s: %s', self.path, exc)
            # 保留默认值，后续 reader 会尝试从 read_frames 的 meta 补充

    def warm_meta(self) -> None:
        """预取元数据（可被线程池并行调用）。"""
        self._ensure_meta()

    def _timer_interval(self) -> int:
        if self._fps > 0:
            return max(1, int(round(1000 / self._fps)))
        return catalog.FRAME_MS

    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._ensure_meta()
        return max(1, self._frame_count)

    def duration(self) -> float:
        if self._duration <= 0:
            self._ensure_meta()
        return self._duration

    def currentFrameNumber(self) -> int:
        return self._frame_index

    def currentTimeSeconds(self) -> float:
        if self._fps <= 0:
            return 0.0
        return self._frame_index / self._fps

    def currentPixmap(self):
        return self._current_pixmap

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._running:
            return
        if imageio_ffmpeg is None:
            self.errorOccurred.emit(str(_IMPORT_ERROR or 'imageio_ffmpeg 不可用'))
            return

        self._stop_evt.clear()
        self._queue = queue.Queue(maxsize=8)
        self._frame_index = 0
        self._ended_fired = False
        self._running = True

        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self._stop_evt.set()
        # 不 join：reader 是 daemon 线程，避免切换动画时阻塞 UI 造成卡顿
        self._thread = None

    def jumpToFrame(self, frame_index: int) -> bool:
        # 本项目只需要回到首帧；完整 seek 通过重启 reader + 丢弃帧实现。
        if frame_index <= 0:
            self.stop()
            self._frame_index = 0
            if self._first_image is not None:
                self._current_image = self._first_image
                self._current_pixmap = self._first_pixmap
            else:
                self._current_image = None
                self._current_pixmap = None
                self._decode_first_frame_sync()
            return True
        return False

    def _decode_first_frame_sync(self) -> None:
        """同步解码首帧，保证 jumpToFrame(0)/currentPixmap 在 start() 前有画面。"""
        if imageio_ffmpeg is None:
            return
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
                    self._current_image = img.copy()
                    self._current_pixmap = QPixmap.fromImage(self._current_image)
                    self._first_image = self._current_image
                    self._first_pixmap = self._current_pixmap
        except Exception as exc:
            logger.warning('webm 首帧预解码失败 %s: %s', self.path, exc)
        finally:
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass

    # ------------------------------------------------------------ reader
    def _reader(self) -> None:
        gen = None
        try:
            q = self._queue
            gen = imageio_ffmpeg.read_frames(
                str(self.path),
                pix_fmt='rgba',
                bits_per_pixel=self._bpp * 8,
                input_params=['-c:v', 'libvpx-vp9'],
            )
            meta = next(gen)
            # 用实际流信息修正元数据
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))

            for frame in gen:
                if self._stop_evt.is_set():
                    break
                try:
                    q.put(frame, timeout=0.2)
                except queue.Full:
                    # 队列满说明 UI 消费不过来；丢弃这一帧，保持实时性
                    pass
            # 正常播完时放入结束标记
            if not self._stop_evt.is_set():
                try:
                    q.put(None, timeout=0.2)
                except queue.Full:
                    pass
        except Exception as exc:
            logger.exception('webm 解码失败: %s', self.path)
            self.errorOccurred.emit(str(exc))
            # 异常中断也要放入结束标记，避免动画链卡在最后一帧
            try:
                q.put(None, timeout=0.2)
            except Exception:
                pass
        finally:
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
