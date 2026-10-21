# -*- coding: utf-8 -*-
"""astra 盲审 P1 发布阻断回归（2026-10-21）。

P1-1：停用模块的行「claimed 未挂载」，域重建后旧页 deleteLater 连坐销毁控件，
而保存链/搜索索引仍引用它们 → RuntimeError；纯核心用户甚至无法在设置里保存
（重新启用模块的写回在崩溃点之后）。修法：判活先于控件读取 + 索引随重建刷新
并只收挂载行。
P1-2：include_ai（模块可用性）被同时喂给更新器选安装包变体——chat 模块关 =
更新到纯桌宠安装包。修法：chat_build_capable（构建变体真值）独立传参。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from pet import feature_activation as fa
from pet.features import MIGRATION_BASELINE


@pytest.fixture(autouse=True)
def _reset_snapshot():
    fa.reset_snapshot_for_tests()
    yield
    fa.reset_snapshot_for_tests()


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _exec_pump(app, ms: int = 150):
    """deleteLater 的 DeferredDelete 只在 exec() 循环投递（PySide6 6.11.2 实测）。"""
    from PySide6.QtCore import QTimer

    QTimer.singleShot(ms, app.quit)
    app.exec()


def _freeze_all_but(*excluded: str):
    fa.freeze_snapshot_for_tests([fid for fid in MIGRATION_BASELINE if fid not in excluded])


def _build_dialog(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dlg = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    return app, dlg


def test_pure_core_save_and_reenable_chat_works(tmp_path, monkeypatch):
    """纯核心（全模块关）：设置能保存，且能在功能页启用 chat 后再次保存。"""
    from pet.config import Config

    seeded = Config(tmp_path)
    assert fa.write_enabled(seeded, [])  # 已判定的纯核心（盘上 enabled=[]）
    fa.freeze_snapshot_for_tests([])
    app, dlg = _build_dialog(tmp_path, monkeypatch)
    try:
        _exec_pump(app)  # 让任何 DeferredDelete 落地
        assert dlg._write_config() is True  # P1-1 修复前：岛控件读取先炸
        page = dlg._features_page
        chat_card = page._cards["chat"]
        chat_card.toggle.setChecked(True)
        assert "chat" in page.pending()
        assert dlg._write_config() is True
        import json

        # feature_activation 是全局键：真值在主 config.json（dlg.config.path
        # 是 slot0 档案 config-slot-0.json，不存它）
        disk = json.loads((dlg.config.dir / "config.json").read_text(encoding="utf-8"))
        assert "chat" in disk["feature_activation"]["enabled"]
    finally:
        dlg.close()
        app.processEvents()


def test_rebuild_after_external_disable_then_save_and_search(tmp_path, monkeypatch):
    """开着对话框时外部停用岛：域重建 → DeferredDelete → 保存与搜索不崩。"""
    from PySide6.QtWidgets import QApplication

    fa.freeze_snapshot_for_tests(MIGRATION_BASELINE)  # 岛开着建窗（行已挂载）
    app, dlg = _build_dialog(tmp_path, monkeypatch)
    try:
        # 外部变化：模块被停用（另一进程/另一窗改了激活集），本窗刷新重建
        _freeze_all_but("dynamic_island")
        dlg._rebuild_domain_navigation()
        _exec_pump(app)  # 旧页 deleteLater 真正落地（行控件连坐销毁）
        assert dlg._write_config() is True  # 修复前：island_* 死控件 RuntimeError
        QApplication.processEvents()
        dlg._search_settings("灵动岛")  # 修复前：搜索遍历死行同样炸
        dlg._search_settings("")
    finally:
        dlg.close()
        app.processEvents()


def test_search_index_excludes_unmounted_rows(tmp_path, monkeypatch):
    """未激活模块的行不进搜索索引（挂载行才入）。"""
    from pet.modern_settings_dialog import SettingRow

    _freeze_all_but("dynamic_island")
    app, dlg = _build_dialog(tmp_path, monkeypatch)
    try:
        names = {row.objectName() for row in dlg._search_rows}
        assert "settingRow_island_enabled" not in names
        assert "settingRow_scale" in names  # 核心行照常在索引里
    finally:
        dlg.close()
        app.processEvents()


def test_updater_uses_build_capability_not_module_gate(tmp_path, monkeypatch):
    """更新器吃构建变体真值：chat 模块关（include_ai=False）但完整版构建时，
    include_chat 仍是 True；缺省不传时回落 include_ai（旧语义）。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dlg = settings_mod.ModernSettingsDialog(
        Config(tmp_path), include_ai=False, chat_build_capable=True)
    try:
        assert dlg.update_page.include_chat is True  # 模块关不送纯桌宠安装包
    finally:
        dlg.close()
        app.processEvents()
    dlg2 = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    try:
        assert dlg2.chat_build_capable is False  # 缺省回落旧语义
    finally:
        dlg2.close()
        app.processEvents()
