# -*- coding: utf-8 -*-
"""灵动岛余额峰谷：按系统时间自动显示/刷新，不依赖余额查询。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtWidgets import QApplication

from pet import balance as balance_mod
from pet.config import Config
from pet.dynamic_island import DynamicIsland


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island_cfg(tmp_path, info_mode: str = "balance_tier") -> Config:
    cfg = Config(base=tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True,
        "show_icon": True,
        "show_name": False,
        "show_info": True,
        "info_mode": info_mode,
        "custom_text": "",
        "show_status": False,
        "style": "dark",
        "x": 100,
        "y": 100,
    })
    return cfg


def test_balance_tier_text_uses_time_and_custom_labels(tmp_path, monkeypatch):
    app = _qapp()
    cfg = _island_cfg(tmp_path)
    monkeypatch.setattr(balance_mod, "deepseek_pricing_tier", lambda: "peak")
    monkeypatch.setattr(balance_mod, "next_pricing_switch", lambda _now: ("idle", None))
    monkeypatch.setattr(balance_mod, "format_switch_time", lambda _now, _next: "12:00")
    island = DynamicIsland(cfg)
    try:
        assert island._balance_tier_display_text() == "高峰 → 12:00"
        assert island._info_text() == "高峰 → 12:00"

        # 不需要 set_balance_info 也会显示，而不是“余额峰谷 --”
        assert "余额峰谷 --" not in island._info_text()

        cfg.set("balance_tier_labels_mode", "liangwen")
        island.refresh_from_config()
        assert island._balance_tier_display_text() == "梁文峰 → 12:00"
    finally:
        island.hide()
        island.deleteLater()
        app.processEvents()


def test_balance_tier_idle_uses_idle_label(tmp_path, monkeypatch):
    app = _qapp()
    cfg = _island_cfg(tmp_path)
    monkeypatch.setattr(balance_mod, "deepseek_pricing_tier", lambda: "idle")
    monkeypatch.setattr(balance_mod, "next_pricing_switch", lambda _now: ("peak", None))
    monkeypatch.setattr(balance_mod, "format_switch_time", lambda _now, _next: "09:00")
    island = DynamicIsland(cfg)
    try:
        assert island._balance_tier_display_text() == "空闲 → 09:00"
    finally:
        island.hide()
        island.deleteLater()
        app.processEvents()


def test_balance_tier_schedules_next_switch_tick(tmp_path, monkeypatch):
    app = _qapp()
    cfg = _island_cfg(tmp_path)
    now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(balance_mod, "beijing_now", lambda: now)
    monkeypatch.setattr(
        balance_mod, "next_pricing_switch",
        lambda: ("idle", now + timedelta(seconds=60)),
    )
    island = DynamicIsland(cfg)
    try:
        assert island._tier_tick_timer.isActive() is True
        assert island._tier_tick_timer.interval() >= 60_000
    finally:
        island.hide()
        island.deleteLater()
        app.processEvents()


def test_non_balance_tier_mode_does_not_schedule_tick(tmp_path, monkeypatch):
    app = _qapp()
    cfg = _island_cfg(tmp_path, info_mode="time")
    now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(balance_mod, "beijing_now", lambda: now)
    monkeypatch.setattr(
        balance_mod, "next_pricing_switch",
        lambda: ("idle", now + timedelta(seconds=60)),
    )
    island = DynamicIsland(cfg)
    try:
        assert island._tier_tick_timer.isActive() is False
    finally:
        island.hide()
        island.deleteLater()
        app.processEvents()
