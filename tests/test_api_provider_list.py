# -*- coding: utf-8 -*-
"""API/Provider 列表：设置页可添加、切换、删除多套 API 配置。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pet.config import Config


def _app():
    return QApplication.instance() or QApplication([])


def test_chat_settings_provider_list_add_switch_delete(tmp_path):
    """AI 设置对话框：API 列表添加后能切换并保留编辑，删除后回到默认项。"""
    from pet.chat.settings_dialog import ChatSettingsDialog

    _app()
    cfg = Config(tmp_path)
    dlg = ChatSettingsDialog(cfg)
    try:
        assert dlg.provider_combo.count() == 1
        assert dlg.settings.active_provider == "openai-main"

        dlg._add_provider()
        new_pid = dlg.settings.active_provider
        assert dlg.provider_combo.count() == 2
        assert new_pid != "openai-main"
        assert new_pid in dlg.settings.providers

        # 修改新 API 后切到默认项再切回，草稿应保留
        dlg.name.setText("Second API")
        dlg.provider_combo.setCurrentIndex(dlg.provider_combo.findData("openai-main"))
        assert dlg.settings.active_provider == "openai-main"
        dlg.provider_combo.setCurrentIndex(dlg.provider_combo.findData(new_pid))
        assert dlg.name.text() == "Second API"

        # 删除新 API 后回到唯一默认项
        dlg._delete_provider()
        assert dlg.provider_combo.count() == 1
        assert dlg.settings.active_provider == "openai-main"
        assert dlg.settings.providers.keys() == {"openai-main"}
    finally:
        dlg.close()
        _app().processEvents()


def test_chat_settings_provider_list_save_persists_active_provider(tmp_path):
    """保存后 active_provider 与新增 Provider 应写入配置。"""
    from pet.chat.settings_dialog import ChatSettingsDialog

    _app()
    cfg = Config(tmp_path)
    dlg = ChatSettingsDialog(cfg)
    try:
        dlg._add_provider()
        new_pid = dlg.settings.active_provider
        dlg.name.setText("Second API")
        dlg.save()

        reloaded = cfg.chat_settings()
        assert reloaded.active_provider == new_pid
        assert new_pid in reloaded.providers
        assert reloaded.providers[new_pid].name == "Second API"
    finally:
        dlg.close()
        _app().processEvents()


def test_modern_ai_page_provider_list_add_switch_delete(tmp_path, monkeypatch):
    """现代设置 AI 页：API 列表同样支持添加/切换/删除。"""
    import pet.modern_settings_dialog as settings_mod

    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    _app()
    cfg = Config(tmp_path)
    dlg = settings_mod.ModernSettingsDialog(cfg, include_ai=True)
    try:
        page = dlg.ai_page
        assert page.provider_combo.count() == 1
        assert page.settings.active_provider == "openai-main"

        page._add_provider()
        new_pid = page.settings.active_provider
        assert page.provider_combo.count() == 2
        assert new_pid != "openai-main"

        page.name.setText("Second API")
        page.provider_combo.setCurrentData("openai-main")
        assert page.settings.active_provider == "openai-main"
        page.provider_combo.setCurrentData(new_pid)
        assert page.name.text() == "Second API"

        page._delete_provider()
        assert page.provider_combo.count() == 1
        assert page.settings.providers.keys() == {"openai-main"}
    finally:
        dlg.close()
        _app().processEvents()
