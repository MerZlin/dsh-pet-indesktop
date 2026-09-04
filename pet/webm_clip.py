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
import os
import queue
import threading
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
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._timer_interval())
        self._timer.timeout.connect(self._poll)

        self._current_image: QImage | None = None
        self._current_pixmap: QPixmap | None = None
        self._first_image: QImage | None = None
        self._first_pixmap: QPixmap | None = None
        self._frame_index = 0
        self._ended_fired = False
        self._running = False
        self._generation = 0
        self._retired: set[threading.Thread] = set()

        self.destroyed.connect(self._on_destroyed)

    def _on_destroyed(self) -> None:
        self.cleanup()

    def __del__(self) -> None:
        try:
            self.cleanup()
        except RuntimeError:
            pass  # 解释器退出期 Qt C++ 对象可能已先销毁，此时无需清理

    def cleanup(self) -> None:
        """停止播放并等待所有 reader 线程退出。"""
        try:
            self.stop()
        except RuntimeError:
            pass  # QTimer 等 Qt 子对象已随 C++ 侧销毁
        # stop() 会把当前 reader 放入 _retired。这里必须等待它们真正退出；
        # imageio-ffmpeg 关闭生成器时可能需要等待 ffmpeg 的收尾线程，短暂
        # 的 500ms 等待会把 LogCatcher 留到测试/进程退出之后。
        retired = list(self._retired)
        for t in retired:
            if t.is_alive():
                try:
                    t.join(timeout=5.0)
                except Exception:
                    pass
        self._retired.difference_update(retired)
        self._sweep_retired()

    def _sweep_retired(self) -> None:
        """探活回收已退出的线程（只清理已死的，活的不动）。"""
        alive = set()
        for t in self._retired:
            if t.is_alive():
                alive.add(t)
            else:
                try:
                    t.join(timeout=0)
                except Exception:
                    pass
        self._retired = alive

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

        # 在 GUI 线程读取真实 fps 后再启动 QTimer，保证新动画的实际帧率
        # 与播放速率计算一致；reader 线程只负责解码和入队。
        self._ensure_meta()
        self._timer.setInterval(self._timer_interval())
        self._stop_evt = threading.Event()
        self._queue = queue.Queue(maxsize=8)
        self._frame_index = 0
        self._ended_fired = False
        self._running = True
        self._generation += 1
        gen_id = self._generation

        self._thread = threading.Thread(target=self._reader, args=(self._stop_evt, gen_id), daemon=True)
        self._thread.start()
        self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        if self._stop_evt is not None:
            self._stop_evt.set()
        thread = self._thread
        if thread is not None:
            self._retired.add(thread)
            self._thread = None
            QTimer.singleShot(5000, self._sweep_retired)

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

    def _decode_first_frame_sync(self) -> None:
        """同步解码首帧（主线程），保证 jumpToFrame(0)/currentPixmap 在 start() 前有画面。"""
        img = self._decode_first_qimage()
        if img is not None:
            self._current_image = img
            self._current_pixmap = QPixmap.fromImage(img)
            self._first_image = img
            self._first_pixmap = self._current_pixmap

    def warm_first_frame(self) -> None:
        """后台线程预解码首帧缓存（仅 QImage，线程安全）。

        首次播放某动画时 jumpToFrame(0) 需要首帧：有缓存则主线程零阻塞，
        避免点击瞬间同步 ffmpeg 解码造成卡顿，以及 Q 弹期间残留旧动画帧。
        """
        if self._first_image is not None or imageio_ffmpeg is None:
            return
        img = self._decode_first_qimage()
        if img is not None:
            self._first_image = img

    # ------------------------------------------------------------ reader
    def _reader(self, stop_evt: threading.Event, generation: int) -> None:
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
                    # 队列满说明 UI 消费不过来；等待主线程消费，保持
                    # 视频时间轴，不让后台解码速度决定动画播放速度。
                    continue
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
