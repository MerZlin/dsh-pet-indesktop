"""键盘控制移动的轮询表：只在用户显式开启时运行。"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Qt

from ..platform import keyboard_state


class KeyboardDriveController(QObject):
    """轮询按住方向并交给目标 PetWindow；关闭即停表。

    自适应节奏：按住时 16ms，连续空拍后降到 50ms。空闲时 60Hz 的精确计时器
    会持续唤醒事件循环，却什么也不做；降频后方向键检测的最大延迟仍有 50ms。
    """

    INTERVAL_MS = 16
    IDLE_INTERVAL_MS = 50
    IDLE_AFTER_TICKS = 6

    def __init__(self, parent=None, *, reader=None) -> None:
        super().__init__(parent)
        self._reader = reader or keyboard_state.pressed_directions
        self._target = None
        self._quiet_ticks = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    @property
    def target(self):
        return self._target

    def set_target(self, window) -> None:
        self.stop()
        if window is None:
            return
        self._target = window
        self._quiet_ticks = 0
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        target = self._target
        self._target = None
        self._quiet_ticks = 0
        self._timer.setInterval(self.INTERVAL_MS)
        if target is not None:
            try:
                target.drive_keyboard(set())
            except Exception:  # noqa: BLE001 - 目标销毁时静默收口
                pass

    def _tick(self) -> None:
        target = self._target
        if target is None:
            self._timer.stop()
            return
        directions: set[str] = set()
        try:
            visible = bool(target.isVisible())
        except AttributeError:
            visible = True
        except Exception:  # noqa: BLE001 - 目标销毁：走下面的 stop
            visible = False
        if visible:
            try:
                directions = set(self._reader())
            except Exception:  # noqa: BLE001 - 读键失败按未按处理
                directions = set()
        if directions:
            self._quiet_ticks = 0
            self._set_interval(self.INTERVAL_MS)
        else:
            self._quiet_ticks += 1
            if self._quiet_ticks >= self.IDLE_AFTER_TICKS:
                self._set_interval(self.IDLE_INTERVAL_MS)
        try:
            target.drive_keyboard(directions)
        except Exception:  # noqa: BLE001 - 窗口销毁/隐藏：停表，别每帧抛
            self.stop()

    def _set_interval(self, interval_ms: int) -> None:
        if self._timer.interval() != int(interval_ms):
            self._timer.setInterval(int(interval_ms))


__all__ = ["KeyboardDriveController"]
