# -*- coding: utf-8 -*-
"""Windows 逐像素穿透轮询拖拽降频回归测试（B2）。

背景：WindowsPerPixelInputController 每 10ms 轮询 QCursor.pos() 更新穿透
样式，但拖拽（`_press_global` 非 None）期间 `should_click_through` 恒返回
False，轮询纯属空转。要求：拖拽期间降频（100ms），松手立即恢复原频率并
强制刷新一次穿透状态；非拖拽时行为不变；仅 Windows 路径受影响。
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from pet import window as window_mod
from pet.config import Config
from pet.window import PetWindow
from tests.test_window_pause import FakeLibrary


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _press(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _move(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseMove, pos, global_pos,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _release(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


class _FakeWindow:
    def winId(self):
        return 123

    def mapFromGlobal(self, point):
        return point

    def _is_transparent_at(self, point):
        return point.x() >= 50


def test_controller_drag_slows_polling_and_restores_on_release():
    """控制器自身：拖拽降频；松手恢复原频率并强制刷新一次穿透状态。"""
    _qapp()
    fake = _FakeWindow()
    fake.mouse_through = False
    fake._press_global = None
    fake.isVisible = lambda: True
    fake.width = lambda: 100
    fake.height = lambda: 80

    controller = object.__new__(window_mod.WindowsPerPixelInputController)
    controller._window = fake
    controller._timer = QTimer()
    controller._timer.setInterval(
        window_mod.WindowsPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    )
    refreshes = []
    controller.refresh = lambda: refreshes.append(1)

    assert controller._timer.interval() == (
        window_mod.WindowsPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    )
    controller.set_drag_active(True)
    assert controller._timer.interval() == (
        window_mod.WindowsPerPixelInputController.DRAG_POLL_INTERVAL_MS
    )
    assert refreshes == [], "降频本身不应触发额外刷新"

    controller.set_drag_active(False)
    assert controller._timer.interval() == (
        window_mod.WindowsPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    )
    assert refreshes == [1], "松手恢复频率时应强制刷新一次穿透状态"

    # 非拖拽状态重复恢复是 no-op：不重复刷新、不改变频率
    controller.set_drag_active(False)
    assert controller._timer.interval() == (
        window_mod.WindowsPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    )
    assert refreshes == [1]

    controller.set_drag_active(True)
    assert controller._timer.interval() == (
        window_mod.WindowsPerPixelInputController.DRAG_POLL_INTERVAL_MS
    )


@pytest.mark.skipif(os.name != "nt", reason="逐像素穿透控制器仅 Windows 创建")
def test_window_press_drag_release_slows_and_restores_polling(app, tmp_path):
    """真实窗口事件链：按下降频、拖拽保持降频、松手立即恢复原频率。"""
    win = PetWindow(FakeLibrary(), Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True
    ctrl = win._input_controller
    assert ctrl is not None
    normal = window_mod.WindowsPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    slow = window_mod.WindowsPerPixelInputController.DRAG_POLL_INTERVAL_MS
    try:
        assert ctrl._timer.interval() == normal

        win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
        assert ctrl._timer.interval() == slow

        win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
        assert win._dragging is True
        assert ctrl._timer.interval() == slow

        win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
        assert ctrl._timer.interval() == normal, "松手后应立即恢复原频率"
    finally:
        win.close()
        app.processEvents()


@pytest.mark.skipif(os.name != "nt", reason="逐像素穿透控制器仅 Windows 创建")
def test_hide_during_drag_restores_polling(app, tmp_path):
    """全审 P2-3：拖拽中隐藏打断 → _reset_press_hold_state 必须对称恢复
    穿透轮询原频率（否则滞留 100ms 拖拽节奏直到下一次完整按-放循环，
    re-show 后穿透状态更新延迟 10 倍且缺强制刷新）。"""
    win = PetWindow(FakeLibrary(), Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True
    ctrl = win._input_controller
    assert ctrl is not None
    normal = window_mod.WindowsPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    slow = window_mod.WindowsPerPixelInputController.DRAG_POLL_INTERVAL_MS
    try:
        assert ctrl._timer.interval() == normal

        # 拖拽中（按下 → 拖拽 → 未松手）被隐藏
        win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
        assert ctrl._timer.interval() == slow
        win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
        assert win._dragging is True
        assert ctrl._timer.interval() == slow

        win.hide()  # 全屏自动隐藏/托盘隐藏路径（自定义 hide → _pause_activity）
        app.processEvents()
        assert win._dragging is False
        assert ctrl._timer.interval() == normal, "隐藏打断拖拽后穿透轮询应立即恢复原频率"

        # 重新显示 → 再拖拽 → 原生 hide（setVisible(False) 直进 hideEvent）
        win.show()
        app.processEvents()
        win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
        assert ctrl._timer.interval() == slow
        win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
        assert win._dragging is True
        win.setVisible(False)
        app.processEvents()
        assert win._dragging is False
        assert ctrl._timer.interval() == normal, "原生隐藏打断拖拽后穿透轮询应立即恢复原频率"
    finally:
        win.close()
        app.processEvents()
