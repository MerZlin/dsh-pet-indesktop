# -*- coding: utf-8 -*-
"""灵动岛卡片的显隐桌宠按钮：宿主接线（按钮 → 桌宠真的隐藏/显示）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.ui.app import CoreHost
from petcore.ui.pet_manager import PetManager


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _host(app, tmp_path: Path):
    from petcore.content import collect

    original = collect.load_all
    collect.load_all = lambda **_kw: []  # type: ignore[assignment]
    try:
        config = Config(tmp_path)
        config.set("island_enabled", True)
        host = CoreHost(app, config, tmp_path, ())
        manager = PetManager(host, tmp_path, config)
        host.pets = manager
        character = host.character_for("fallback")
        host.window = manager.create_primary(character)
        host.attach_window(host.window)
        return host, manager
    finally:
        collect.load_all = original  # type: ignore[assignment]


def test_card_toggle_button_hides_and_shows_the_pet(app, tmp_path: Path) -> None:
    host, manager = _host(app, tmp_path)
    island = host.ensure_island()
    try:
        assert host.window.isVisible() is True
        island.card._toggle_button.click()
        app.processEvents()
        assert host.window.isVisible() is False
        assert island.card._toggle_button.text() == "显示桌宠"
        island.card._toggle_button.click()
        app.processEvents()
        assert host.window.isVisible() is True
        assert island.card._toggle_button.text() == "隐藏桌宠"
    finally:
        island.stop()
        island.deleteLater()
        manager.shutdown()


def test_island_click_action_toggle_pet_is_wired(app, tmp_path: Path) -> None:
    """`island_click_action=toggle_pet` 以前只发信号没人接，现在必须真的生效。"""
    host, manager = _host(app, tmp_path)
    island = host.ensure_island()
    try:
        assert host.window.isVisible() is True
        island.clicked.emit()
        app.processEvents()
        assert host.window.isVisible() is False
        island.clicked.emit()
        app.processEvents()
        assert host.window.isVisible() is True
    finally:
        island.stop()
        island.deleteLater()
        manager.shutdown()
