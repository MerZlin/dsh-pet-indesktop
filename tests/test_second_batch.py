# -*- coding: utf-8 -*-
"""第二批功能：快速对话气泡基础测试。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from pet.config import Config


def _config(tmp_path: Path) -> Config:
    return Config(tmp_path)


def test_quick_chat_bubble_instantiates(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    from pet.quick_chat import QuickChatBubble

    cfg = _config(tmp_path)
    bubble = QuickChatBubble(cfg)
    try:
        assert bubble.input is not None
        assert bubble.send_btn is not None
        assert bubble.close_btn is not None
    finally:
        bubble.close()
        app.processEvents()


def test_quick_chat_long_reply_uses_pagination(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    from pet.quick_chat import QuickChatBubble

    cfg = _config(tmp_path)
    bubble = QuickChatBubble(cfg)
    try:
        bubble._reply_text = "长" * 1200
        bubble._render_reply()
        assert bubble.page_widget.isVisibleTo(bubble) or bubble.page_widget.isVisible()
        assert len(bubble._pages) == 3
        assert bubble.page_label.text() == "1/3"
    finally:
        bubble.close()
        app.processEvents()


def test_quick_chat_closes_when_the_window_loses_focus(tmp_path: Path, monkeypatch):
    import pet.quick_chat as quick_chat_mod

    app = QApplication.instance() or QApplication([])
    from pet.quick_chat import QuickChatBubble

    bubble = QuickChatBubble(_config(tmp_path))
    bubble.show()
    app.processEvents()
    assert bubble.isVisible()

    class InactiveQApplication:
        @staticmethod
        def activeWindow():
            return object()

    monkeypatch.setattr(quick_chat_mod, "QApplication", InactiveQApplication)
    QApplication.sendEvent(bubble, QEvent(QEvent.Type.WindowDeactivate))
    app.processEvents()

    assert not bubble.isVisible()
    bubble.close()
    app.processEvents()


def test_quick_chat_waits_for_popup_menu_to_close(tmp_path: Path, monkeypatch):
    """菜单收尾的失活事件不能把刚打开的快速对话立即关掉。"""
    import time

    import pet.app as app_mod
    from pet.app import PetApp

    app = QApplication.instance() or QApplication([])
    state = {"popup": True}

    class FakeQApp:
        @staticmethod
        def activePopupWidget():
            return object() if state["popup"] else None

    monkeypatch.setattr(app_mod, "QApplication", FakeQApp)
    owner = PetApp.__new__(PetApp)
    owner.config = _config(tmp_path)
    owner.enable_chat = True
    owner.win = object()
    owner.quick_chat = None
    owner._pending_dialog_opens = set()

    owner.open_quick_chat()
    app.processEvents()
    assert owner.quick_chat is None, "菜单仍活动时不应创建或显示快速对话"

    state["popup"] = False
    deadline = time.time() + 1
    while owner.quick_chat is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert owner.quick_chat is not None
    assert owner.quick_chat.isVisible(), "菜单关闭后快速对话应自动显示"
    owner.quick_chat.close()
    app.processEvents()


def test_quick_chat_open_is_deferred_to_next_event_turn(tmp_path: Path):
    """Cocoa 原生菜单不可被 activePopupWidget 发现，打开动作仍须异步派发。"""
    from pet.app import PetApp

    app = QApplication.instance() or QApplication([])
    owner = PetApp.__new__(PetApp)
    owner.config = _config(tmp_path)
    owner.enable_chat = True
    owner.win = object()
    owner.quick_chat = None
    owner._pending_dialog_opens = set()

    owner.open_quick_chat()
    assert owner.quick_chat is None, "快速对话不得在原生菜单动作回调内同步创建"

    app.processEvents()
    assert owner.quick_chat is not None
    assert owner.quick_chat.isVisible()
    owner.quick_chat.close()
    app.processEvents()


def test_quick_chat_survives_initial_cocoa_activation_handoff(tmp_path: Path):
    """已有活动窗口时，首次激活不能被 Cocoa 的过渡失活事件立即关闭。"""
    import pytest
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    app = QApplication.instance() or QApplication([])
    if app.platformName() != "cocoa":
        pytest.skip("requires the real Cocoa Qt platform plugin")

    from pet.quick_chat import QuickChatBubble

    settings = QDialog()
    bubble = QuickChatBubble(_config(tmp_path))
    try:
        settings.show()
        settings.raise_()
        settings.activateWindow()
        QTest.qWait(100)

        bubble.show_for_pet()
        QTest.qWait(100)

        assert bubble.isVisible(), "首次激活过渡不能留下空白的已关闭原生窗口"
        assert bubble.isActiveWindow()
    finally:
        bubble.close()
        settings.close()
        app.processEvents()
