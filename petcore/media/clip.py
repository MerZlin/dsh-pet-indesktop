from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

_BACKENDS: dict[str, Callable[[Path], Any]] = {}


def register_backend(suffix: str, factory: Callable[[Path], Any]) -> None:
    _BACKENDS[suffix.lower().lstrip(".")] = factory


def has_backend(suffix: str) -> bool:
    return suffix.lower().lstrip(".") in _BACKENDS


class PetClip:
    def __init__(self, impl: Any, name: str):
        self._impl = impl
        self.name = name
        self._frame_callbacks: list[Callable[[int], None]] = []
        self._finished_callbacks: list[Callable[[], None]] = []
        self._error_callbacks: list[Callable[[str], None]] = []
        impl.frameChanged.connect(self._on_frame)
        impl.finished.connect(self._on_finished)
        impl.errorOccurred.connect(self._on_error)

    def on_frame(self, callback: Callable[[int], None]) -> None:
        self._frame_callbacks.append(callback)

    def on_finished(self, callback: Callable[[], None]) -> None:
        self._finished_callbacks.append(callback)

    def on_error(self, callback: Callable[[str], None]) -> None:
        self._error_callbacks.append(callback)

    def clear_callbacks(self) -> None:
        self._frame_callbacks.clear()
        self._finished_callbacks.clear()
        self._error_callbacks.clear()

    def _on_frame(self, number: int) -> None:
        for callback in tuple(self._frame_callbacks):
            callback(number)

    def _on_finished(self) -> None:
        for callback in tuple(self._finished_callbacks):
            callback()

    def _on_error(self, message: str) -> None:
        for callback in tuple(self._error_callbacks):
            callback(message)

    @property
    def frame_count(self) -> int:
        try:
            return max(0, int(self._impl.frameCount()))
        except Exception:
            return 0

    @property
    def duration_seconds(self) -> float:
        try:
            return max(0.0, float(self._impl.duration()))
        except Exception:
            return 0.0

    @property
    def fps(self) -> float:
        count = self.frame_count
        duration = self.duration_seconds
        if count > 0 and duration > 0:
            return count / duration
        return float(getattr(self._impl, "_fps", 24.0) or 24.0)

    def start(self) -> bool:
        result = self._impl.start()
        return result is not False

    def rearm_same(self) -> None:
        """同一段 clip 的续圈：帧序列在跑时不要拉回第 0 帧。

        WebM 的 ``start()`` 本身是圈末 re-arm 的入口（``_running`` 为真时也
        需要它唤醒驻留的 reader），所以只让显式提供 ``rearm_same`` 的实现
        走短路；其余仍按旧语义 ``start()``。
        """
        rearm = getattr(self._impl, "rearm_same", None)
        if callable(rearm):
            rearm()
            return
        self.start()

    def stop(self) -> None:
        self._impl.stop()

    def pause(self) -> None:
        pause = getattr(self._impl, "pause", None)
        if callable(pause):
            try:
                pause()
            except Exception:
                pass

    def resume(self) -> None:
        resume = getattr(self._impl, "resume", None)
        if callable(resume):
            try:
                resume()
            except Exception:
                pass

    def jump_to_frame(self, index: int) -> bool:
        try:
            return bool(self._impl.jumpToFrame(index))
        except Exception:
            return False

    def current_frame_number(self) -> int:
        try:
            return int(self._impl.currentFrameNumber())
        except Exception:
            return 0

    def current_image(self) -> Any:
        getter = getattr(self._impl, "currentImage", None)
        if callable(getter):
            return getter()
        getter = getattr(self._impl, "currentPixmap", None)
        if callable(getter):
            return getter()
        return None

    def warm_first_frame(self) -> None:
        warmer = getattr(self._impl, "warm_first_frame", None)
        if callable(warmer):
            try:
                warmer()
            except Exception:
                pass

    def set_decode_throttle(self, divisor: int) -> None:
        setter = getattr(self._impl, "set_decode_throttle", None)
        if callable(setter):
            try:
                setter(divisor)
            except Exception:
                pass

    def set_playback_speed(self, speed: float) -> None:
        setter = getattr(self._impl, "set_playback_speed", None)
        if callable(setter):
            try:
                setter(max(0.1, float(speed)))
            except Exception:
                pass

    def close(self) -> None:
        self.stop()
        closer = getattr(self._impl, "close", None)
        if callable(closer):
            try:
                closer()
                return
            except Exception:
                pass
        cleaner = getattr(self._impl, "cleanup", None)
        if callable(cleaner):
            try:
                cleaner()
                return
            except Exception:
                pass
        self._impl.deleteLater()


def create_clip(path: Path, name: str) -> PetClip | None:
    factory = _BACKENDS.get(path.suffix.lower().lstrip("."))
    if factory is None:
        return None
    impl = factory(path)
    return PetClip(impl, name)


def create_frame_clip(frames_dir: Path, name: str) -> PetClip | None:
    try:
        from pet.frameseq_clip import FrameSeqClip
    except Exception:
        return None
    # 定时器必须是 PreciseTimer（FrameSeqClip 的默认值）：Windows 的计时器颗粒是
    # 15.625ms，CoarseTimer 会把间隔量化到那个边界——24fps 的 42ms 变 47ms（1x 只有
    # 21.3fps）、2x 的 21ms 变 31ms（2x 只有 32fps，观感"开了 2 倍速仍偏慢"）。
    # Precise 的均值与目标一致（实测 1x 23.8fps / 2x 47.6fps）；它在这条链路上没有
    # 观察到额外的计时器分辨率副作用（旧版主分支的满速档也一直是 Precise）。
    return PetClip(FrameSeqClip(frames_dir), name)


def create_fallback_clip(name: str = "fallback") -> PetClip:
    from .fallback import FallbackClip

    return PetClip(FallbackClip(), name)
