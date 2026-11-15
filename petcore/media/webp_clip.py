"""单文件 WebP 动画：QMovie 播放，按真实帧延时算时长。

Qt 的 WebP 插件支持动图（`QMovie` 可播），但不支持随机跳帧；控制移动的
「跳过前摇」只在支持 `jumpToFrame` 的后端上生效，这里如实返回 False。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QMovie


class WebPClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self._movie = QMovie(str(self.path))
        self._movie.setCacheMode(QMovie.CacheMode.CacheNone)
        self._movie.frameChanged.connect(self.frameChanged)
        self._movie.finished.connect(self.finished)
        self._movie.error.connect(lambda err: self.errorOccurred.emit(str(err)))
        self._frame_count = max(0, int(self._movie.frameCount()))
        self._delays: list[int] = []
        self.playback_speed = 1.0
        self._measure()

    def _measure(self) -> None:
        count = self._frame_count
        if count <= 0:
            return
        fallback = 42
        total = 0
        for index in range(count):
            try:
                self._movie.jumpToFrame(index)
                delay = int(self._movie.nextFrameDelay())
            except Exception:  # noqa: BLE001 - 读不到就按 24fps 兜底
                delay = 0
            if delay <= 0:
                delay = fallback
            self._delays.append(delay)
            total += delay
        self._total_ms = total
        try:
            self._movie.jumpToFrame(0)
        except Exception:  # noqa: BLE001
            pass

    def frameCount(self) -> int:
        return max(1, self._frame_count)

    def duration(self) -> float:
        total = float(getattr(self, "_total_ms", 0) or 0)
        if total <= 0.0:
            total = self.frameCount() * 42.0
        return total / 1000.0 / max(0.1, self.playback_speed)

    def currentFrameNumber(self) -> int:
        return int(self._movie.currentFrameNumber())

    def currentImage(self):
        return self._movie.currentImage()

    def currentPixmap(self):
        return self._movie.currentPixmap()

    def set_playback_speed(self, speed: float) -> None:
        self.playback_speed = max(0.1, float(speed))
        self._movie.setSpeed(int(round(100 * self.playback_speed)))

    def start(self) -> None:
        self._movie.start()

    def stop(self) -> None:
        self._movie.stop()

    def pause(self) -> None:
        self._movie.setPaused(True)

    def resume(self) -> None:
        self._movie.setPaused(False)

    def jumpToFrame(self, frame_index: int) -> bool:
        total = self._movie.frameCount()
        if total <= 0:
            return False
        index = max(0, min(total - 1, int(frame_index)))
        return bool(self._movie.jumpToFrame(index))

    def warm_first_frame(self) -> None:
        try:
            self._movie.jumpToFrame(0)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        self._movie.stop()
