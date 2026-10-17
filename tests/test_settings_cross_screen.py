# -*- coding: utf-8 -*-
"""「允许桌宠跨屏」设置行：全局键、默认关、重启级、保存 round trip。

行本身是常规域「窗口与系统」的一行（位置与分组由 ``test_settings_ia_first_cut``
与 ``test_settings_compat_layering`` 钉住），本文件只管这一行的**行为契约**：

1. 默认关（出厂默认 = 单显示器口径）；
2. 保存 round trip：勾选 → ``_write_config`` 落盘 → 重开对话框仍是勾上的；
3. 作用域是**全局键**：行内带「（影响所有桌宠）」标注，且说明里那句被削到标注里
   （文案只有一份来源，见 ``settings_pet_controls.GLOBAL_SCOPE_ROWS``）；
4. 重启级：勾选只改磁盘，运行中的壳不重读（进程快照，见
   ``tests/test_overlay_cross_screen_switch.py``）；
5. 非 overlay 拓扑（legacy 单窗）下如实置灰 + 说明，不留"改了没反应"的活开关；
6. 默认值标记：它登记在 ``settings_default_marks`` 的映射表里（出厂默认 False）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pet.modern_settings_dialog as settings_mod  # noqa: E402
import pet.settings_pet_controls as pet_controls  # noqa: E402
from pet import settings_default_marks  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.modern_settings_dialog import SettingRow  # noqa: E402
from pet.settings_pet_controls import GLOBAL_SCOPE_NOTE  # noqa: E402

app = QApplication.instance() or QApplication([])

ROW_KEY = "cross_screen"
CONFIG_KEY = "allow_cross_screen"


def _dialog(tmp_path, monkeypatch, *, overlay=True):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(pet_controls, "_is_overlay_topology", lambda: overlay)
    return settings_mod.ModernSettingsDialog(Config(base=tmp_path), include_ai=True)


def _row(dialog) -> SettingRow:
    row = dialog.findChild(SettingRow, f"settingRow_{ROW_KEY}")
    assert row is not None, "常规域「窗口与系统」必须有这一行"
    return row


def test_row_defaults_to_off_and_saves_round_trip(tmp_path, monkeypatch):
    cfg = Config(base=tmp_path)
    assert cfg.get(CONFIG_KEY) is False, "出厂默认必须是关"
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog)
        assert row.control.isChecked() is False, "行初值跟着配置（默认关）"
        row.control.setChecked(True)
        assert dialog._write_config() is True
    finally:
        dialog.close()

    assert Config(base=tmp_path).get(CONFIG_KEY) is True, "勾选必须落盘"
    reopened = _dialog(tmp_path, monkeypatch)
    try:
        assert _row(reopened).control.isChecked() is True, "重开对话框读回落盘值"
        _row(reopened).control.setChecked(False)
        assert reopened._write_config() is True
    finally:
        reopened.close()
    assert Config(base=tmp_path).get(CONFIG_KEY) is False


def test_row_declares_the_global_scope_note(tmp_path, monkeypatch):
    """全局键：说明末尾那句被拆成行内标注（文案仍是同一句、同一处来源）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog)
        assert row.scope_note.text() == GLOBAL_SCOPE_NOTE
        assert GLOBAL_SCOPE_NOTE not in row.hint_label.text(), "同一句不许出现两遍"
        assert "重启后生效" in row.hint_label.text(), "重启级必须写在行内"
        assert (ROW_KEY, GLOBAL_SCOPE_NOTE) in pet_controls.GLOBAL_SCOPE_ROWS
        # 可访问说明挂在控件上，且是"说明 + 标注"全文（拆位置不等于访问性丢掉）
        assert GLOBAL_SCOPE_NOTE in row.control.accessibleDescription()
    finally:
        dialog.close()


def test_row_is_greyed_out_in_the_legacy_topology(tmp_path, monkeypatch):
    """非 overlay 拓扑：legacy 单窗自己就能跨显示器，本开关没有第二个读点。"""
    dialog = _dialog(tmp_path, monkeypatch, overlay=False)
    try:
        row = _row(dialog)
        assert row.control.isEnabled() is False
        assert row.control.toolTip() == "仅单合成窗（overlay）拓扑生效"
        # 置灰不等于隐藏：行与说明照旧在（用户看得到"为什么用不了"）
        assert row.hint_label.isVisible() or row.hint_label.text()
    finally:
        dialog.close()


def test_row_has_a_comparable_default(tmp_path, monkeypatch):
    """默认值标记：行登记在映射表里，且出厂默认 False 真的可比。"""
    assert settings_default_marks._ROW_DEFAULT_PATHS[ROW_KEY] == CONFIG_KEY
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog)
        assert row.default_reset_button.isHidden(), "默认态不出「已修改」入口"
        row.control.setChecked(True)
        app.processEvents()
        assert not row.default_reset_button.isHidden(), (
            "勾上之后要出「已修改」标记与单项重置入口")
    finally:
        dialog.close()


def test_row_is_reachable_by_search(tmp_path, monkeypatch):
    """搜索：搜「跨屏 / 显示器 / 桌宠跨屏 / 影响所有桌宠」都能命中这一行。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        for query in ("跨屏", "显示器", "桌宠跨屏", "影响所有桌宠"):
            dialog.search_edit.setText("")
            app.processEvents()
            dialog.search_edit.setText(query)
            app.processEvents()
            matched = [row.objectName() for row in dialog._search_matches]
            assert f"settingRow_{ROW_KEY}" in matched, f"搜「{query}」应当命中这一行"
    finally:
        dialog.close()


def test_hint_describes_one_window_over_the_desktop():
    """说明文案必须写新架构（一张合成窗铺满桌面），不能再写"每块屏各开一张窗口"。

    旧文案描述的是已删除的"每屏一窗"拓扑；用户照着它理解，会以为跨屏就是多开窗口。
    """
    from pet.settings_pet_controls import CROSS_SCREEN_HINT

    assert "一张合成窗" in CROSS_SCREEN_HINT
    assert "各开一张窗口" not in CROSS_SCREEN_HINT
