# -*- coding: utf-8 -*-
"""直播捕获兼容模式下气泡作为主窗子内容的回归测试（issue #62）。

开启「直播捕获兼容模式」后，气泡 PetSpeechBubble 不再作为独立 Tool 窗口，
而是临时变成 PetWindow 的子控件：OBS/直播姬只需捕获主窗一个源即可看到气泡。
本文件锁定父/子切换、窗口类型、标题与主窗内放置约束。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
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


def test_speech_bubble_capture_compat_becomes_child_and_restores():
    app = _qapp()
    host = QWidget()
    host.setGeometry(0, 0, 640, 390)
    host.show()
    app.processEvents()
    bubble = PetSpeechBubble()
    bubble.show()
    app.processEvents()
    try:
        assert bubble.isVisible()
        bubble.set_capture_compat(True, host)
        app.processEvents()
        assert bubble.parentWidget() is host
        assert _window_type_mask(bubble.windowFlags()) == Qt.WindowType.Widget
        assert bubble.windowTitle() == ""
        assert bubble.isVisible(), "切换为子控件后应保持原可见状态"

        bubble.set_capture_compat(False)
        app.processEvents()
        assert bubble.parentWidget() is None
        assert _window_type_mask(bubble.windowFlags()) == Qt.WindowType.Tool
        assert bubble.windowTitle() == ""
        assert bubble.isVisible(), "切回独立 Tool 窗口后应保持原可见状态"
    finally:
        bubble.close()
        host.close()


def test_capture_compat_places_bubble_inside_host_bounds():
    app = _qapp()
    host = QWidget()
    host.setGeometry(0, 0, 640, 390)
    host.show()
    app.processEvents()
    bubble = PetSpeechBubble()
    try:
        bubble.set_capture_compat(True, host)
        bubble.show_text(
            "直播测试气泡内容",
            host.geometry(),
            duration_ms=60000,
            pet_scale=1.0,
        )
        app.processEvents()
        assert bubble.isVisible()
        # 子模式可用区应为主窗矩形，气泡不能越出主窗客户区边界。
        # 子控件 geometry() 是相对父控件的坐标，因此用 host.rect() 判定。
        assert host.rect().contains(bubble.geometry())
    finally:
        bubble.close()
        host.close()


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
        app.processEvents()
