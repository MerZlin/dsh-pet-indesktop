# -*- coding: utf-8 -*-
"""桌宠交互锁定与不透明度（用户反馈批次）：锁定位置、SHIFT+左键拖动、
窗口不透明度，以及托盘菜单复选状态与设置同步。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from pet import catalog
from pet.config import Config
from pet.window import PetWindow

NAMES = [
    catalog.IDLE,
    catalog.TURN,
    catalog.MOVES[0],
    catalog.CLICKS[0],
    catalog.DRAG,
    "写代码",
]


class FakeClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        self._running = True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self):
        self._clips = {name: FakeClip() for name in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _press(pos: QPointF, global_pos: QPointF, modifiers=Qt.KeyboardModifier.NoModifier) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers,
    )


def _move(pos: QPointF, global_pos: QPointF, buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseMove, pos, global_pos,
        Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier,
    )


def _release(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _make_win(app, tmp_path, **overrides):
    cfg = Config(base=tmp_path)
    for key, value in overrides.items():
        cfg.set(key, value)
    win = PetWindow(FakeLibrary(), cfg)
    win._is_in_interactive_area = lambda pos: True  # 测试聚焦拖拽判定
    return win


def test_lock_position_blocks_drag_but_keeps_click(app, tmp_path):
    win = _make_win(app, tmp_path, lock_position=True)
    assert win.lock_position is True
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    assert win.pos() == start, "锁定位置时拖动不应移动窗口"
    clicks = []
    win._on_click = lambda: clicks.append(1)
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
    assert clicks == [1], "锁定位置时点击互动仍应生效"
    win.close()
    app.processEvents()


def test_shift_drag_requires_shift(app, tmp_path):
    win = _make_win(app, tmp_path, shift_drag=True)
    assert win.shift_drag is True
    # 未按 SHIFT：按下+移动不拖动，松手为点击
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    assert win.pos() == start, "未按 SHIFT 时不应拖动"
    clicks = []
    win._on_click = lambda: clicks.append(1)
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
    assert clicks == [1]
    # 按住 SHIFT：可拖动
    win.mousePressEvent(_press(
        QPointF(10, 10), QPointF(100, 100), Qt.KeyboardModifier.ShiftModifier
    ))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    assert win.pos() != start, "按住 SHIFT 应可拖动"
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
    win.close()
    app.processEvents()


def test_pet_opacity_applied_and_synced(app, tmp_path):
    win = _make_win(app, tmp_path, pet_opacity=50)
    win.show()
    app.processEvents()
    assert abs(win.windowOpacity() - 0.5) < 0.01, "show 后应应用 50% 不透明度"
    win.set_pet_opacity(80)
    assert abs(win.windowOpacity() - 0.8) < 0.01, "set_pet_opacity 应立即生效"
    assert win.cfg.get("pet_opacity") == 80
    # 设置对话框关闭路径：refresh_pet_settings 同步
    win.cfg.set("pet_opacity", 60)
    win.cfg.set("lock_position", True)
    win.cfg.set("shift_drag", True)
    win.refresh_pet_settings()
    assert win.lock_position is True
    assert win.shift_drag is True
    assert abs(win.windowOpacity() - 0.6) < 0.01
    win.close()
    app.processEvents()


def test_tray_menu_syncs_mouse_through_from_config(tmp_path):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QWidget

    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    config = Config(tmp_path)
    config.set("mouse_through", False)

    class Win:
        def __init__(self, config):
            self.config = config

        def icon_pixmap(self, _size=None):
            return QPixmap(2, 2)

        def set_mouse_through(self, on):
            self.config.set("mouse_through", bool(on))
            self.config.save()

        def _speech_bubble(self):  # pragma: no cover - 占位
            raise NotImplementedError

    win = Win(config)
    win._speech_bubble = QWidget()
    manager = PetApp(app, config, enable_chat=True)
    tray = manager._build_tray(win)
    menu = tray.contextMenu()
    action = next(a for a in menu.actions() if a.text() == "鼠标穿透")
    assert action.isChecked() is False
    # 设置对话框里改了鼠标穿透 → config 变化 → 托盘菜单弹出前同步
    config.set("mouse_through", True)
    menu.aboutToShow.emit()
    assert action.isChecked() is True
    # 反向：托盘里点掉 → config 落盘
    action.setChecked(False)
    assert config.get("mouse_through") is False
    tray.hide()
    app.processEvents()
