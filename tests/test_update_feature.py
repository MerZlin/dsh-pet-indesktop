# -*- coding: utf-8 -*-
"""自动更新功能的 UI/进程接线回归。"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_settings_has_version_footer_and_update_page(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    try:
        labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
        assert labels[-1] == "更新"
        assert dialog.version_footer.objectName() == "settingsVersion"
        assert dialog.version_footer.text().startswith("版本 v")
        assert dialog.update_page.findChild(settings_mod.QPushButton, "checkUpdateButton") is not None
        assert dialog.update_page.findChild(settings_mod.QPushButton, "installUpdateButton") is not None
        assert dialog.select_page("更新") is True
        assert dialog.sidebar.currentItem().text() == "更新"
    finally:
        dialog.close()


def test_settings_update_page_does_not_write_config(tmp_path, monkeypatch):
    from pet import updater
    from pet.config import Config
    from pet.update_settings import UpdatePage

    _qapp()
    config = Config(tmp_path)
    before = config.path.read_bytes() if config.path.exists() else None
    page = UpdatePage(config, include_chat=False)
    release = {
        "version": "4.3.0",
        "notes": "notes",
        "assets": {},
        "asset_details": {},
        "html_url": "https://github.com/MerZlin/dsh-pet-indesktop/releases",
    }
    page._on_check_finished(release)
    assert page.status_label.text().startswith("发现 v4.3.0")
    after = config.path.read_bytes() if config.path.exists() else None
    assert after == before
    page.close()


def test_settings_process_page_deep_link_is_passed(tmp_path, monkeypatch):
    from pet.app import AppShell
    from pet.config import Config

    shell = AppShell.__new__(AppShell)
    shell.config = Config(tmp_path)
    shell._settings_process_running = lambda: False
    shell._settings_launch_pending = lambda: False
    shell._install_config_watcher = lambda: None
    shell._mark_settings_child = lambda active: None
    calls = []
    monkeypatch.setattr(
        shell,
        "_launch_settings_process",
        lambda instance=None, *, page=None: calls.append((instance, page)) or True,
    )
    instance = object()
    assert shell.open_settings_process(instance, page="更新") is True
    assert calls == [(instance, "更新")]
