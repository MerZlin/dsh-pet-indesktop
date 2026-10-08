# -*- coding: utf-8 -*-
"""双击桌宠打开快速对话气泡（事件过滤器实现）回归测试。

设计要点（为什么不是覆写 mouseDoubleClickEvent）：
PetWindow 的 MRO 是 [PetWindow, QWidget, QObject, QPaintDevice, Object,
WindowFeatureGateMixin, object] —— QWidget 排在 WindowFeatureGateMixin 之前，
混入类里定义 Qt 虚函数会被 QWidget 的实现静默遮蔽；而 window.py 行数预算
已顶满（4616/4616），因此双击走「事件过滤器」：零侵入主窗口。

本文件的真实事件序取 Windows 的真实消息映射：
Press → Release → DblClick → Release。
其中第二段 Release 必须被吞掉，否则会再触发一次点击回应（#实测见文件末）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.double_click_chat import DoubleClickChatFilter, install_double_click_chat
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


class _BigScreen:
    def name(self):
        return "big"

    def availableGeometry(self):
        return QRect(0, 0, 1920, 1200)

    def geometry(self):
        return QRect(0, 0, 1920, 1200)

    def devicePixelRatio(self):
        return 1.0


_BIG_SCREEN = _BigScreen()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_win(app, tmp_path, **overrides):
    cfg = Config(base=tmp_path)
    for key, value in overrides.items():
        cfg.set(key, value)
    win = PetWindow(FakeLibrary(), cfg)
    win._screen_available = lambda *a, **k: _BIG_SCREEN
    # 夹具不建立真实窗口几何/角色 body_box，稳定身体框为空会让命中判定恒 False；
    # 本文件聚焦双击路由与「尾随 Release 抑制」，故按既有约定放开该判定，
    # 留白区域的语义由 test_double_click_outside_interactive_area_does_not_open 单独锁定。
    win._is_in_interactive_area = lambda pos: True
    return win


def _ev(kind, buttons=Qt.MouseButton.LeftButton, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(
        kind, QPointF(10, 10), QPointF(100, 100), button, buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _double_click_sequence(win, app):
    """Windows 真实双击消息序：Press → Release → DblClick → Release。"""
    for event in (
        _ev(QEvent.Type.MouseButtonPress),
        _ev(QEvent.Type.MouseButtonRelease, buttons=Qt.MouseButton.NoButton),
        _ev(QEvent.Type.MouseButtonDblClick),
        _ev(QEvent.Type.MouseButtonRelease, buttons=Qt.MouseButton.NoButton),
    ):
        app.sendEvent(win, event)
        app.processEvents()


@pytest.fixture
def harness(app, tmp_path):
    """返回 (win, clicks, opened)：clicks 记录点击回应次数，opened 记录开栏次数。"""
    win = _make_win(app, tmp_path)
    clicks: list[int] = []
    opened: list[int] = []
    win._on_click = lambda: clicks.append(1)
    win.on_open_quick_chat = lambda: opened.append(1)
    yield win, clicks, opened
    win.close()
    app.processEvents()


def test_double_click_opens_quick_chat_once(harness, app):
    win, clicks, opened = harness
    install_double_click_chat(win)
    _double_click_sequence(win, app)
    assert opened == [1], "双击应打开一次快速对话栏"
    assert clicks == [1], "首击仍给一次点击回应；尾随 Release 不得再补一次"


def test_trailing_release_is_swallowed_only_once(harness, app):
    win, clicks, opened = harness
    install_double_click_chat(win)
    _double_click_sequence(win, app)
    assert len(clicks) == 1
    # 双击之后的一次普通单击必须恢复为「一次点击回应」——抑制不得泄漏到后续交互
    app.sendEvent(win, _ev(QEvent.Type.MouseButtonPress))
    app.processEvents()
    app.sendEvent(win, _ev(QEvent.Type.MouseButtonRelease, buttons=Qt.MouseButton.NoButton))
    app.processEvents()
    assert len(clicks) == 2, "尾随 Release 抑制泄漏到了下一次正常单击"
    assert opened == [1], "后续单击不应再次开栏"


def test_disabled_config_keeps_legacy_two_click_behavior(harness, app):
    win, clicks, opened = harness
    win.cfg.set("double_click_chat", False)
    install_double_click_chat(win)
    _double_click_sequence(win, app)
    assert opened == [], "开关关闭时双击不应开栏"
    assert len(clicks) == 2, "开关关闭时必须保持原有「双击=两次单击回应」行为"


def test_without_chat_callback_double_click_stays_legacy(harness, app):
    win, clicks, opened = harness
    win.on_open_quick_chat = None  # 无 Chat 的打包变体
    install_double_click_chat(win)
    _double_click_sequence(win, app)
    assert opened == []
    assert len(clicks) == 2, "没有对话入口时应回落到原有双击行为，而不是被吞掉"


def test_double_click_outside_interactive_area_does_not_open(harness, app):
    win, clicks, opened = harness
    win._is_in_interactive_area = lambda pos: False  # 左右留白区域
    install_double_click_chat(win)
    _double_click_sequence(win, app)
    assert opened == [], "留白区域的双击不应开栏"
    assert len(clicks) == 2


def test_right_button_double_click_is_ignored(harness, app):
    win, clicks, opened = harness
    install_double_click_chat(win)
    app.sendEvent(win, _ev(
        QEvent.Type.MouseButtonDblClick,
        buttons=Qt.MouseButton.RightButton,
        button=Qt.MouseButton.RightButton,
    ))
    app.processEvents()
    assert opened == [], "右键双击不应开栏"


def test_install_is_idempotent(harness, app):
    win, clicks, opened = harness
    install_double_click_chat(win)
    install_double_click_chat(win)
    _double_click_sequence(win, app)
    assert opened == [1], "重复安装不应导致开栏两次"


def test_filter_forwards_other_events_untouched(harness, app):
    win, clicks, opened = harness
    flt = DoubleClickChatFilter(win)
    win.installEventFilter(flt)
    # 非鼠标事件必须原样放行（不吞事件、不改行为）
    assert flt.eventFilter(win, QEvent(QEvent.Type.KeyPress)) is False
    assert flt.eventFilter(object(), QEvent(QEvent.Type.MouseButtonDblClick)) is False


def test_install_tolerates_host_without_event_filter_support():
    """_wire_window 也会喂非 QWidget 的测试替身：没有 children()/installEventFilter
    时不得抛异常（历史回归：_FakeWindow 上 AttributeError 打挂整个接线路径）。"""

    class _FakeHost:
        cfg = {"double_click_chat": True}

    assert install_double_click_chat(_FakeHost()) is None


def test_install_tolerates_host_without_config():
    class _NoConfig:
        pass

    assert install_double_click_chat(_NoConfig()) is None
