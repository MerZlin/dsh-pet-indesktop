# -*- coding: utf-8 -*-
"""手机版对话窗：窄屏骨架、抽屉、视图 chips、更多菜单与对比度。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from petcore.config import Config
from petcore.ui.chat.contrast import (
    audit_pairs,
    contrast_ratio,
    ensure_contrast,
    readable_ink,
)
from petcore.ui.chat.controller import ChatController
from petcore.ui.chat.phone_window import PhoneChatWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Host:
    def __init__(self, root: Path) -> None:
        self.config = Config(root)


def _controller(root: Path) -> ChatController:
    return ChatController(_Host(root), root)


def test_chip_palette_meets_contrast() -> None:
    assert contrast_ratio("#2a3442", "#eef1f5") >= 4.5
    assert contrast_ratio("#d7dce6", "#262b36") >= 4.5
    assert readable_ink("#eef1f5") == "#16181d"
    assert readable_ink("#16181d") == "#ffffff"
    assert audit_pairs(
        [
            ("#2a3442", "#eef1f5", 4.5),
            ("#d7dce6", "#262b36", 4.5),
            ("#ffffff", ensure_contrast("#4d6bfe", "#ffffff", 4.5), 4.5),
        ]
    ) == []


def test_phone_window_is_narrow_and_has_phone_chrome(app, tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "phone")
    control = ChatController(host, tmp_path)
    try:
        assert control.open("ai.chat", pet_slot=0) is True
        window = control.window_for("ai.chat", 0)
        assert isinstance(window, PhoneChatWindow)
        assert window.minimumWidth() == 440
        assert window.maximumWidth() == 560
        assert 440 <= window.width() <= 560
        assert window.phone_back_button.isVisible() is True
        assert window.phone_new_button.isVisible() is True
        assert window.phone_more_button.isVisible() is True
        assert {"ai", "dsh"} <= set(window._phone_chip_buttons)
        assert window.phone_view_bar.parent() is window.chat_main
        assert window.title_bar.height() > 0
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_phone_back_button_opens_the_session_drawer(app, tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "phone")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        window = control.window_for("ai.chat", 0)
        window.show()
        app.processEvents()
        assert window._compact_layout is True
        window.phone_back_button.click()
        app.processEvents()
        assert window.sidebar.isVisible() is True
        window.phone_back_button.click()
        app.processEvents()
        assert window.sidebar.isVisible() is False
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_phone_more_menu_covers_desktop_actions(app, tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "phone")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        window = control.window_for("ai.chat", 0)
        window._build_phone_menu()
        texts = [action.text() for action in window._phone_menu.actions() if action.text()]
        for expected in ("设置", "新建会话", "回到对话", "桌面版", "置顶", "深色模式", "回收站", "最小化", "关闭"):
            assert expected in texts
        window.set_trash_count(2)
        assert "2" in window.trash_button.text()
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_layout_switch_is_blocked_while_streaming(app, tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "desktop")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        state = control.states()[("ai.chat", 0)]
        state.active = True
        errors: list[tuple[str, str]] = []
        window = control.window_for("ai.chat", 0)
        window.show_error = lambda reason, message="": errors.append((reason, message))
        window.layout_requested.emit("phone")
        app.processEvents()
        assert host.config.get("chat_layout") == "desktop"
        assert errors and errors[0][0] == "busy"
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_layout_toggle_rebuilds_the_window(app, tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "desktop")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        window = control.window_for("ai.chat", 0)
        from petcore.ui.chat.window import ChatWindow

        assert isinstance(window, ChatWindow) and not isinstance(window, PhoneChatWindow)
        window.layout_requested.emit("phone")
        app.processEvents()
        assert host.config.get("chat_layout") == "phone"
        rebuilt = control.window_for("ai.chat", 0)
        assert isinstance(rebuilt, PhoneChatWindow)
        rebuilt.layout_requested.emit("desktop")
        app.processEvents()
        assert host.config.get("chat_layout") == "desktop"
        assert not isinstance(control.window_for("ai.chat", 0), PhoneChatWindow)
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_phone_chip_bar_stays_visible_in_extension_views(app) -> None:
    spec = SimpleNamespace(
        id="mem",
        label="记忆",
        factory=lambda: QLabel("mem"),
    )
    window = PhoneChatWindow(views=[spec], character_id="shenshen")
    window.show()
    app.processEvents()
    try:
        assert "mem" in window._phone_chip_buttons
        window.set_view("mem")
        app.processEvents()
        assert window.current_view() == "mem"
        assert window.phone_view_bar.isVisible() is True
        assert window.phone_view_bar.parent() is window.chat_main
        window._phone_chip_buttons["ai"].click()
        app.processEvents()
        assert window.current_view() == "ai"
    finally:
        window.deleteLater()


def _settle(app) -> None:
    # 折行/滚动条宽度依赖布局落定后才算得出的视口宽：多跑几轮事件循环再断言。
    for _ in range(4):
        app.processEvents()


def test_phone_assistant_bubble_fits_the_narrow_viewport(app, tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "phone")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        window = control.window_for("ai.chat", 0)
        window.resize(440, 760)
        window.show()
        _settle(app)
        long_text = "这是一段很长的回复，" * 80
        control.append("ai.chat", "assistant", long_text)
        _settle(app)
        bubble = window.bubbles()[-1]
        available = max(
            160,
            window.scroll.viewport().width() - 2 * window.message_horizontal_margin,
        )
        assert bubble.role == "assistant"
        # 气泡外框占满可用宽（不多出边界），正文只让出右侧 12px padding（左边靠左）。
        assert bubble.width() <= available + 2
        assert bubble.width() - bubble.body.width() == 12
        assert bubble.body.minimumWidth() <= bubble.width() - 12
        # 长段必须真的折成多行：正文高度不少于 5 行，说明宽度没有被硬撑成一行。
        assert bubble.body.height() >= 5 * bubble.body.fontMetrics().lineSpacing()
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_phone_new_button_sits_at_the_view_bar_end_and_close_in_the_header(
    app, tmp_path: Path
) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "phone")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        window = control.window_for("ai.chat", 0)
        row = window.phone_view_bar.layout()
        header = window.title_bar.layout()
        assert window.phone_new_button.parent() is window.phone_view_bar
        assert row.itemAt(row.count() - 1).widget() is window.phone_new_button
        assert header.itemAt(header.count() - 2).widget() is window.phone_more_button
        assert header.itemAt(header.count() - 1).widget() is window.phone_close_button
        assert window.phone_new_button.objectName() == "phone-new-button"
        assert window.phone_close_button.objectName() == "phone-close-button"
        assert window.phone_close_button.toolTip() == "关闭聊天窗口"
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()


def test_phone_close_button_hides_the_window_and_follows_the_theme(
    app, tmp_path: Path
) -> None:
    host = _Host(tmp_path)
    host.config.set("chat_layout", "phone")
    control = ChatController(host, tmp_path)
    try:
        control.open("ai.chat", pet_slot=0)
        window = control.window_for("ai.chat", 0)
        window.show()
        app.processEvents()
        light_key = window.phone_close_button.icon().cacheKey()
        window.set_dark(True)
        app.processEvents()
        assert window.phone_close_button.icon().cacheKey() != light_key
        window.phone_close_button.click()
        app.processEvents()
        assert window.isVisible() is False
    finally:
        for state in control.states().values():
            if state.window is not None:
                state.window.deleteLater()
