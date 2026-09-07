# -*- coding: utf-8 -*-
"""直播捕获兼容模式下气泡作为主窗子内容的回归测试（issue #62）。

开启「直播捕获兼容模式」后，气泡 PetSpeechBubble 不再作为独立 Tool 窗口，
而是临时变成 PetWindow 的子控件：OBS/直播姬只需捕获主窗一个源即可看到气泡。
本文件锁定父/子切换、窗口类型、标题与主窗内放置约束。
"""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from pet.config import Config
from pet.speech_bubble import PetSpeechBubble
from pet.window import STREAM_CAPTURE_TITLE, PetWindow
from tests.test_window_pause import FakeLibrary


def _qapp():
    return QApplication.instance() or QApplication([])


def _make_pet(tmp_path, *, capture_on: bool = False) -> PetWindow:
    config = Config(base=tmp_path)
    config.set("stream_capture_mode", bool(capture_on))
    return PetWindow(FakeLibrary(), config)


def _window_type_mask(flags):
    return flags & Qt.WindowType.WindowType_Mask


def test_speech_bubble_default_is_independent_tool_window():
    _qapp()
    bubble = PetSpeechBubble()
    try:
        assert bubble.parentWidget() is None
        assert _window_type_mask(bubble.windowFlags()) == Qt.WindowType.Tool
        assert bubble.windowTitle() == ""
    finally:
        bubble.close()
        bubble.deleteLater()
        _qapp().processEvents()


def test_speech_bubble_capture_compat_becomes_child_and_restores():
    app = _qapp()
    host = QWidget()
    host.setGeometry(0, 0, 640, 390)
    bubble = PetSpeechBubble()
    try:
        bubble.set_capture_compat(True, host)
        assert bubble.parentWidget() is host
        assert _window_type_mask(bubble.windowFlags()) == Qt.WindowType.Widget
        assert bubble.windowTitle() == ""

        bubble.set_capture_compat(False)
        assert bubble.parentWidget() is None
        assert _window_type_mask(bubble.windowFlags()) == Qt.WindowType.Tool
        assert bubble.windowTitle() == ""
    finally:
        bubble.close()
        host.close()
        bubble.deleteLater()
        host.deleteLater()
        app.processEvents()


def test_capture_compat_places_bubble_inside_host_bounds():
    app = _qapp()
    host = QWidget()
    host.setGeometry(0, 0, 640, 390)
    bubble = PetSpeechBubble()
    try:
        bubble.set_capture_compat(True, host)
        bubble.show_text(
            "直播测试气泡内容",
            host.geometry(),
            duration_ms=60000,
            pet_scale=1.0,
        )
        # 子模式可用区应为主窗矩形，气泡不能越出主窗客户区边界。
        # 子控件 geometry() 是相对父控件的坐标，因此用 host.rect() 判定。
        assert host.rect().contains(bubble.geometry())
    finally:
        bubble.close()
        host.close()
        bubble.deleteLater()
        host.deleteLater()
        app.processEvents()


class _FakeBubble:
    def __init__(self, parent, geometry):
        self._parent = parent
        self._geometry = geometry
        self._interactive = True

    def isVisible(self):
        return True

    def parentWidget(self):
        return self._parent

    def geometry(self):
        return self._geometry


class _FakeHitPet:
    _frame_draw_rect = PetWindow._frame_draw_rect
    _is_transparent_at = PetWindow._is_transparent_at

    def __init__(self, bubble):
        self._speech_bubble = bubble
        self.scale = 0.1
        self._w = 64
        self._h = 39
        self._squash_active = False
        self._frame_pixmap = QPixmap(64, 36)
        self._frame_pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(self._frame_pixmap)
        p.fillRect(0, 0, 64, 36, QColor(255, 255, 255, 255))
        p.end()
        self._hit_alpha_image = None


def test_capture_child_interactive_bubble_is_non_transparent_hit_target():
    """子模式气泡在 Windows 逐像素穿透判定中必须是可点击命中区。"""
    _qapp()
    fake = _FakeHitPet(None)
    bubble = _FakeBubble(fake, QRect(0, 0, 100, 50))
    fake._speech_bubble = bubble
    # 该点位于帧绘制矩形上方，未加气泡守卫时 _is_transparent_at 返回 True。
    assert PetWindow._is_transparent_at(fake, QPoint(10, 2)) is False


class _FakeQuickBubble:
    """子模式气泡：geometry 是父坐标，mapToGlobal 模拟父窗口偏移。"""

    def __init__(self):
        self._origin = QPoint(100, 20)

    def isVisible(self):
        return True

    def mapToGlobal(self, point):
        return self._origin + point

    def size(self):
        return QRect(0, 0, 120, 50).size()


def test_try_open_quick_chat_from_bubble_works_in_child_mode():
    """子模式气泡 geometry 是父坐标；快速对话命中判断必须换算回全局坐标。"""
    _qapp()
    opened = []
    bubble = _FakeQuickBubble()
    pet = SimpleNamespace(on_open_quick_chat=lambda: opened.append(True), _speech_bubble=bubble)
    # 子模式全局命中：气泡左上角在 (100,20)，点击内部 (110,30) 应命中。
    assert PetWindow._try_open_quick_chat_from_bubble(pet, QPoint(110, 30)) is True
    assert opened == [True]
    # 气泡外点击不触发快速对话。
    assert PetWindow._try_open_quick_chat_from_bubble(pet, QPoint(300, 300)) is False
    assert opened == [True]


def test_pet_window_runtime_capture_mode_syncs_bubble(tmp_path):
    app = _qapp()
    win = _make_pet(tmp_path, capture_on=False)
    try:
        assert win._speech_bubble.parentWidget() is None
        win.set_stream_capture_mode(True)
        assert win.windowTitle() == STREAM_CAPTURE_TITLE
        assert win._speech_bubble.parentWidget() is win
        assert _window_type_mask(win._speech_bubble.windowFlags()) == Qt.WindowType.Widget

        win.set_stream_capture_mode(False)
        assert win.windowTitle() == ""
        assert win._speech_bubble.parentWidget() is None
        assert _window_type_mask(win._speech_bubble.windowFlags()) == Qt.WindowType.Tool
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


def test_pet_window_starts_capture_mode_with_child_bubble(tmp_path):
    app = _qapp()
    win = _make_pet(tmp_path, capture_on=True)
    try:
        assert win.windowTitle() == STREAM_CAPTURE_TITLE
        assert win._speech_bubble.parentWidget() is win
        assert _window_type_mask(win._speech_bubble.windowFlags()) == Qt.WindowType.Widget
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()
