"""WebP/PNG 精灵表：一次性解码整张图，按网格切帧，QTimer 推进。

WebP 的 Qt handler 不支持随机跳帧（`QImageReader.jumpToImage` 返回 False），
所以必须整张解码；超过像素预算时在解码阶段等比缩小，避免 120 帧大表
（约 110MB/张）把内存打爆。切出的帧只在播放时按需 copy，缓存上限 4 帧。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

from ..contracts.content import SheetSpec

#: 单张精灵表解码后的像素预算；aboutme-pets 的 8×15×640×360 会缩小到约 54%（单张约 32MB）。
MAX_SHEET_PIXELS = 8_000_000
_FRAME_CACHE = 4


class SheetClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, spec: SheetSpec, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._image = QImage()
        self.error = ""
        self._frame_w = max(1, int(spec.frame_width))
        self._frame_h = max(1, int(spec.frame_height))
        self._columns = max(1, int(spec.columns))
        self._rows = max(1, int(spec.rows))
        self._count = max(1, min(int(spec.frame_count), self._columns * self._rows))
        self._fps = max(1.0, float(spec.fps or 24.0))
        self._index = 0
        self._divisor = 1
        self._speed = 1.0
        self._cache: dict[int, QImage] = {}
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._load()

    def _load(self) -> None:
        reader = QImageReader(str(self.spec.path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() * size.height() > MAX_SHEET_PIXELS:
            scale = math.sqrt(MAX_SHEET_PIXELS / float(size.width() * size.height()))
            reader.setScaledSize(
                QSize(max(1, int(size.width() * scale)), max(1, int(size.height() * scale)))
            )
        image = reader.read()
        if image.isNull():
            self.error = reader.errorString() or "sprite sheet decode failed"
            return
        self._image = image
        self._frame_w = max(1, self._image.width() // self._columns)
        self._frame_h = max(1, self._image.height() // self._rows)

    def estimated_bytes(self) -> int:
        try:
            return int(self._image.sizeInBytes()) if not self._image.isNull() else 0
        except Exception:  # noqa: BLE001
            return 0

    def frameCount(self) -> int:
        return max(1, self._count)

    def duration(self) -> float:
        # 降帧是隔帧采样（每拍前进 divisor 帧），时长不随 divisor 变。
        return self.frameCount() / self._fps / max(0.1, self._speed)

    def currentFrameNumber(self) -> int:
        return int(self._index)

    def currentImage(self):
        return self._frame(self._index)

    def _frame(self, index: int) -> QImage:
        if self._image.isNull():
            return QImage()
        index = max(0, min(self.frameCount() - 1, int(index)))
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        column = index % self._columns
        row = index // self._columns
        frame = self._image.copy(
            column * self._frame_w,
            row * self._frame_h,
            self._frame_w,
            self._frame_h,
        )
        if len(self._cache) >= _FRAME_CACHE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[index] = frame
        return frame

    def _interval_ms(self) -> int:
        return max(1, int(round(1000.0 / self._fps / max(0.1, self._speed) * self._divisor)))

    def start(self) -> None:
        self._index = 0
        self._timer.start(self._interval_ms())
        self.frameChanged.emit(0)

    def stop(self) -> None:
        self._timer.stop()

    def pause(self) -> None:
        self._timer.stop()

    def resume(self) -> None:
        self._timer.start(self._interval_ms())

    def set_playback_speed(self, speed: float) -> None:
        self._speed = max(0.1, float(speed))
        if self._timer.isActive():
            self._timer.start(self._interval_ms())

    def set_frame_divisor(self, divisor: int) -> None:
        self._divisor = max(1, int(divisor))
        if self._timer.isActive():
            self._timer.start(self._interval_ms())

    def jumpToFrame(self, frame_index: int) -> bool:
        if self._image.isNull():
            return False
        self._index = max(0, min(self.frameCount() - 1, int(frame_index)))
        self.frameChanged.emit(self._index)
        return True

    def warm_first_frame(self) -> None:
        self._frame(0)

    def _tick(self) -> None:
        if self._image.isNull():
            self._timer.stop()
            return
        last = max(0, self.frameCount() - 1)
        if self._index >= last:
            self._timer.stop()
            self.finished.emit()
            return
        # 降帧是「隔帧采样」，不是把时长拉长：每拍前进 divisor 帧，时长不变。
        step = max(1, int(self._divisor))
        self._index = min(last, self._index + step)
        self.frameChanged.emit(self._index)

    def close(self) -> None:
        self._timer.stop()
        self._cache.clear()
        self._image = QImage()


def create_sheet_clip(spec: SheetSpec, name: str = "") -> QObject:
    clip = SheetClip(spec)
    if name:
        clip.setObjectName(str(name))
    return clip


__all__ = ["MAX_SHEET_PIXELS", "SheetClip", "create_sheet_clip"]
