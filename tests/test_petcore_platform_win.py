"""petcore Windows 输入层：左键转发 + 逐像素轮询的判定。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from petcore.platform import win as win_mod
from petcore.platform.win import PerPixelInputController, forward_left_click


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeUser32:
    def __init__(self) -> None:
        self.style = 0x0008
        self.writes: list[int] = []

    def GetWindowLongW(self, _hwnd, _index):  # noqa: N802 - Win32 名字
        return self.style

    def SetWindowLongW(self, _hwnd, _index, value):  # noqa: N802 - Win32 名字
        self.writes.append(int(value))
        self.style = int(value)
        return 0


class _Win(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.dragging = False
        self._left_press_active = False
        self._right_press_active = False
        self._through_hold_until = 0.0
        self.click_through = True
        self.transparent = False

    def is_transparent_at(self, _local) -> bool:
        return self.transparent

    def mapFromGlobal(self, point):  # noqa: N802 - Qt 名字
        return QPoint(10, 10)


def test_forward_left_click_sets_transparency_and_restores(app) -> None:
    window = _Win()
    user32 = _FakeUser32()
    sent: list[object] = []

    def fake_send(_user32) -> bool:
        sent.append(_user32)
        return True

    assert forward_left_click(
        window, hold_ms=30, user32=user32, send_input=fake_send
    ) is True
    assert sent and sent[0] is user32
    assert user32.writes[0] & win_mod._WS_EX_TRANSPARENT
    assert window._through_hold_until > 0.0

    deadline = QTimer()
    deadline.setSingleShot(True)
    done: list[bool] = []
    deadline.timeout.connect(lambda: done.append(True))
    deadline.start(120)
    while not done:
        app.processEvents()
    assert user32.writes[-1] == 0x0008
    assert window._through_hold_until == 0.0
    window.deleteLater()


def test_forward_left_click_restores_immediately_when_sendinput_fails(app) -> None:
    window = _Win()
    user32 = _FakeUser32()
    assert forward_left_click(
        window, hold_ms=30, user32=user32, send_input=lambda _u: False
    ) is False
    assert user32.writes[-1] == 0x0008
    assert window._through_hold_until == 0.0
    window.deleteLater()


def test_controller_does_not_force_transparency_for_click_through(app, monkeypatch) -> None:
    applied: list[bool] = []
    monkeypatch.setattr(win_mod, "set_click_through", lambda _hwnd, on: applied.append(bool(on)))
    window = _Win()
    window.resize(200, 120)
    window.show()
    app.processEvents()
    window.transparent = False
    controller = PerPixelInputController(window, window.is_transparent_at)
    controller.stop()
    controller.refresh()
    assert applied[-1] is False, "不透明像素在穿透模式下也不能整窗放走右键"

    window.transparent = True
    controller.refresh()
    assert applied[-1] is True

    window.transparent = False
    window._left_press_active = True
    controller.refresh()
    assert applied[-1] is False, "左键按住期间不许把窗口变透明（否则丢 release）"
    window.deleteLater()
