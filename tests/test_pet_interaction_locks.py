# -*- coding: utf-8 -*-
"""桌宠交互锁定与不透明度（用户反馈批次）：锁定位置、SHIFT+左键拖动、
窗口不透明度，以及托盘菜单复选状态与设置同步。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QPixmap
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


def test_slingshot_geometry_stays_inside_window_for_all_pull_directions():
    base = QRect(0, 8, 100, 100)
    bounds = QRect(0, 0, 100, 108)
    for pull in (QPoint(-160, 0), QPoint(160, 0), QPoint(0, -160), QPoint(0, 160),
                 QPoint(-113, -113), QPoint(113, 113)):
        x, y, width, height = PetWindow._slingshot_geometry(base, pull, 1.0, bounds)
        assert bounds.contains(QRect(x, y, width, height))


def test_slingshot_trajectory_anchor_starts_at_character_edge():
    character = QRect(10, 8, 100, 100)
    anchor = PetWindow._slingshot_trajectory_anchor(character, QPoint(1, 1))
    # QRect's right/bottom are inclusive, so the ray exits at (109, 107).
    assert anchor == QPointF(109.0, 107.0)


def test_slingshot_trajectory_preview_preserves_arc_scale_and_allows_clipping():
    bounds = QRect(0, 0, 120, 108)
    anchor = QPointF(110, 108)
    trajectory = [(0.0, 0.0), (90.0, -30.0), (180.0, 90.0)]
    preview = PetWindow._slingshot_trajectory_preview(trajectory, anchor, bounds, 1.0)
    assert len(preview) == len(trajectory)
    assert preview[0] == (110.0, 108.0)
    assert preview[1] == (200.0, 78.0)
    assert preview[-1] == (290.0, 198.0)


def test_slingshot_geometry_uses_smooth_directional_deformation():
    base = QRect(0, 8, 100, 100)
    horizontal = PetWindow._slingshot_geometry(base, QPoint(160, 0), 1.0)
    vertical = PetWindow._slingshot_geometry(base, QPoint(0, -160), 1.0)
    assert horizontal[2] == round(base.width() * 1.3)
    assert horizontal[3] == round(base.height() / 1.3)
    assert vertical[2] == round(base.width() / 1.3)
    assert vertical[3] == round(base.height() * 1.3)


def test_slingshot_band_points_use_visible_edge_and_mouse_endpoint():
    start, end = PetWindow._slingshot_band_points(
        QRect(10, 8, 100, 100), QPoint(4, 57), QPoint(20, 0),
    )
    assert start == QPointF(9.0, 57.0)
    assert end == QPointF(4.0, 57.0)


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


def _move(pos: QPointF, global_pos: QPointF, buttons=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseMove, pos, global_pos,
        Qt.MouseButton.NoButton, buttons, modifiers,
    )


def _release(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _right_press(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, global_pos,
        Qt.MouseButton.RightButton,
        Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _right_release(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, global_pos,
        Qt.MouseButton.RightButton, Qt.MouseButton.LeftButton,
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
    win.mouseMoveEvent(_move(
        QPointF(60, 60), QPointF(400, 300), modifiers=Qt.KeyboardModifier.ShiftModifier
    ))
    assert win.pos() != start, "按住 SHIFT 应可拖动"
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
    # 先按下（未按 SHIFT），越过阈值前补按 SHIFT → 仍可拖动
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(11, 11), QPointF(102, 102)))  # 未超阈值
    win.mouseMoveEvent(_move(
        QPointF(60, 60), QPointF(400, 300),
        modifiers=Qt.KeyboardModifier.ShiftModifier,
    ))
    assert win.pos() != start, "越过阈值时按住 SHIFT 应开始拖动"
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
    win.close()
    app.processEvents()


def test_slingshot_sequence_launches_with_reverse_pull(app, tmp_path):
    win = _make_win(app, tmp_path)
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(140, 100)))
    assert win._interaction_state == "DRAGGING"
    win.mousePressEvent(_right_press(QPointF(60, 60), QPointF(140, 100)))
    assert win._interaction_state == "SLINGSHOT_AIMING"
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(60, 100),
                             buttons=Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton))
    assert win._slingshot_pull.x() > 0
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(60, 100)))
    assert win._interaction_state == "THROWN"
    assert win._physics_mode == "throw"
    assert win._phys_vel[0] > 0, "向左拉时 pull 应指向右侧"
    win.close()
    app.processEvents()


def test_slingshot_right_release_and_escape_cancel_to_anchor(app, tmp_path):
    win = _make_win(app, tmp_path)
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(140, 100)))
    win.mousePressEvent(_right_press(QPointF(60, 60), QPointF(140, 100)))
    anchor = win.pos()
    win.mouseReleaseEvent(_right_release(QPointF(60, 60), QPointF(140, 100)))
    assert win._interaction_state == "DRAGGING"
    win.mousePressEvent(_right_press(QPointF(60, 60), QPointF(140, 100)))
    win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                Qt.KeyboardModifier.NoModifier))
    assert win._interaction_state == "IDLE"
    assert win.pos() == anchor
    win.close()
    app.processEvents()


def test_slingshot_minimum_and_lock_position_do_not_launch(app, tmp_path):
    win = _make_win(app, tmp_path)
    starts = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(140, 100)))
    win.mousePressEvent(_right_press(QPointF(60, 60), QPointF(140, 100)))
    anchor = win.pos()
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(130, 100)))
    assert win._interaction_state == "IDLE"
    assert win.pos() == anchor
    win.set_lock_position(True)
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(140, 100)))
    win.mousePressEvent(_right_press(QPointF(60, 60), QPointF(140, 100)))
    assert win._interaction_state == "IDLE"
    win.close()
    app.processEvents()


def test_slingshot_focus_out_cancels_to_anchor(app, tmp_path):
    win = _make_win(app, tmp_path)
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(140, 100)))
    win.mousePressEvent(_right_press(QPointF(60, 60), QPointF(140, 100)))
    anchor = win.pos()
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(40, 100),
                             buttons=Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton))
    win.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert win._interaction_state == "IDLE"
    assert win.pos() == anchor
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

    from pet.app import AppShell
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
    manager = AppShell(app, config, enable_chat=True)
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

def test_drag_does_not_play_click_sound_but_click_does(app, tmp_path, monkeypatch):
    from pathlib import Path
    win = _make_win(app, tmp_path)
    press_calls = []
    release_calls = []
    monkeypatch.setattr("pet.window.resolve_click_sound_pair", lambda pack, data_dir=None: (Path("press.wav"), Path("release.wav")))
    monkeypatch.setattr("pet.window.play_press_sound", lambda pair, volume: press_calls.append((pair, volume)))
    monkeypatch.setattr("pet.window.play_release_sound", lambda pair, volume: release_calls.append((pair, volume)))

    # 拖动：按下 -> 超过阈值移动 -> 松手，不应触发任何点击音效
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseReleaseEvent(_release(QPointF(60, 60), QPointF(400, 300)))
    assert win.pos() != start
    assert press_calls == []
    assert release_calls == []

    # 点击：按下 -> 原位松手，应播放完整 press+release
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(200, 200)))
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(200, 200)))
    assert len(press_calls) == 1
    assert len(release_calls) == 1

    win.close()
    app.processEvents()

def test_click_sound_pair_is_cleared_when_resolution_fails(app, tmp_path, monkeypatch):
    from pathlib import Path
    win = _make_win(app, tmp_path)
    press_calls = []
    release_calls = []
    pair_a = (Path("press-a.wav"), Path("release-a.wav"))
    current_pair = [pair_a]
    monkeypatch.setattr("pet.window.resolve_click_sound_pair", lambda pack, data_dir=None: current_pair[0])
    monkeypatch.setattr("pet.window.play_press_sound", lambda pair, volume: press_calls.append(pair))
    monkeypatch.setattr("pet.window.play_release_sound", lambda pair, volume: release_calls.append(pair))

    # 第一次点击：解析成功，播放 pair_a
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(200, 200)))
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(200, 200)))
    assert press_calls == [pair_a]
    assert release_calls == [pair_a]

    # 解析失败（如切换音效包后文件缺失）：不应复用旧 pair
    current_pair[0] = None
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(300, 300)))
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(300, 300)))
    assert press_calls == [pair_a], "解析失败时不应播放旧按下音"
    assert release_calls == [pair_a], "解析失败时不应播放旧释放音"

    # 再次解析成功且换成 pair_b：应播放新 pair
    pair_b = (Path("press-b.wav"), Path("release-b.wav"))
    current_pair[0] = pair_b
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(400, 400)))
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(400, 400)))
    assert press_calls == [pair_a, pair_b]
    assert release_calls == [pair_a, pair_b]

    win.close()
    app.processEvents()
