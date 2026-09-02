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


def test_quick_chat_closes_when_the_window_loses_focus(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    from pet.quick_chat import QuickChatBubble

    bubble = QuickChatBubble(_config(tmp_path))
    bubble.show()
    app.processEvents()
    assert bubble.isVisible()

    QApplication.sendEvent(bubble, QEvent(QEvent.Type.WindowDeactivate))
    app.processEvents()

    assert not bubble.isVisible()
    bubble.close()
    app.processEvents()
