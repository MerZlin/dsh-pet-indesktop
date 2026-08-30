# -*- coding: utf-8 -*-
"""第一批功能：灵动岛配置/点击台词绑定/聊天窗置顶的基础测试。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.config import Config


def _config(tmp_path: Path) -> Config:
    return Config(tmp_path)


def test_config_click_talk_bindings_roundtrip(tmp_path: Path):
    cfg = _config(tmp_path)
    cfg.set_click_talk_bindings("shenshen", {
        "点击回应 - 开心跃动": ["好耶！", "今天也要开心～"],
        "点击回应 - 傲娇生气": ["哼！"],
    })

    reloaded = Config(tmp_path)
    assert reloaded.click_talk_texts_for("shenshen", "点击回应 - 开心跃动") == ["好耶！", "今天也要开心～"]
    assert reloaded.click_talk_texts_for("shenshen", "点击回应 - 傲娇生气") == ["哼！"]
    assert reloaded.click_talk_texts_for("shenshen", "未绑定动画") == []


def test_config_dynamic_island_keeps_at_least_one_component(tmp_path: Path):
    cfg = _config(tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True,
        "show_icon": False,
        "show_name": False,
        "show_info": False,
        "info_mode": "time",
        "custom_text": "",
        "show_status": False,
        "x": None,
        "y": None,
    })

    island = cfg.get("dynamic_island")
    assert island["enabled"] is True
    assert island["show_info"] is True  # 至少保留一个组件


def test_modern_chat_window_pin_toggle_persists(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    from pet.chat.widgets import ChatWindow

    cfg = _config(tmp_path)
    win = ChatWindow(cfg, "shenshen")
    try:
        assert hasattr(win, "pin_button")
        win.pin_button.setChecked(True)
        win.toggle_always_on_top()
        assert cfg.get("chat_always_on_top") is True
        assert bool(win.windowFlags() & __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.WindowType.WindowStaysOnTopHint)
        win.pin_button.setChecked(False)
        win.toggle_always_on_top()
        assert cfg.get("chat_always_on_top") is False
    finally:
        win.close()
        app.processEvents()


def test_dynamic_island_widget_import_and_signal(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    from pet.dynamic_island import DynamicIsland

    cfg = _config(tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True,
        "show_icon": True,
        "show_name": True,
        "show_info": True,
        "info_mode": "time",
        "custom_text": "",
        "show_status": True,
        "style": "glass",
        "x": 100,
        "y": 100,
    })
    island = DynamicIsland(cfg)
    clicks = []
    island.clicked.connect(lambda: clicks.append(1))
    island.show()
    app.processEvents()
    island.clicked.emit()
    assert clicks == [1]
    assert island.width() > 0
    island.hide()
    island.deleteLater()
    app.processEvents()


def test_dynamic_island_resizes_when_info_changes(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    from pet.dynamic_island import DynamicIsland

    cfg = _config(tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True,
        "show_icon": True,
        "show_name": True,
        "show_info": True,
        "info_mode": "balance",
        "custom_text": "",
        "show_status": True,
        "style": "dark",
        "x": 100,
        "y": 100,
    })
    island = DynamicIsland(cfg)
    width_before = island.width()
    island.set_balance_info(
        "当前高峰 · 下周一 09:00 切换",
        "余额 ¥123.45（充值 ¥200.00 / 赠送 ¥0.00）",
    )
    app.processEvents()
    assert island.width() > width_before
    island.hide()
    island.deleteLater()
    app.processEvents()
