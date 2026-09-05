# -*- coding: utf-8 -*-
"""测试设置界面控件状态、保存/读取 round-trip 以及内置 Agent 音效资源存在与格式有效性。"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.click_sound import resolve_builtin_sound
from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_builtin_agent_sounds_exist_and_valid_wav():
    """验证三个内置 Agent 音效文件存在，且是合法 16-bit 22050Hz 单声道 WAV。"""
    for sound_id, key in [
        ("builtin:agent-start", "agent-start"),
        ("builtin:agent-done", "agent-done"),
        ("builtin:agent-error", "agent-error"),
    ]:
        sound_path = resolve_builtin_sound(sound_id)
        assert sound_path is not None, f"无法解析内置音效: {sound_id}"
        assert sound_path.is_file(), f"音效文件不存在: {sound_path}"
        assert sound_path.suffix.lower() == ".wav"
        assert sound_path.stat().st_size > 1000

        with wave.open(str(sound_path), "rb") as wf:
            assert wf.getnchannels() == 1  # 单声道
            assert wf.getsampwidth() == 2  # 16-bit = 2 bytes
            assert wf.getframerate() == 22050
            num_frames = wf.getnframes()
            assert num_frames > 0
            duration = num_frames / wf.getframerate()
            assert 0.1 <= duration <= 1.0


def test_modern_settings_dialog_round_trip(qapp, tmp_path: Path):
    """验证现代设置面板：初始化正确读取 Config，修改后 _write_config 写入 Config 并能准确读回。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root)

    # 1. 初始状态断言
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        assert dialog.slingshot_check.isChecked() is True
        assert dialog.throw_strength_select.currentData() == "standard"
        assert dialog.click_sound_check.isChecked() is True
        assert dialog.click_sound_volume_spin.value() == 70
        assert dialog.agent_sound_check.isChecked() is False
        assert dialog.agent_sound_volume_spin.value() == 65
        assert dialog.agent_sound_cooldown_spin.value() == 2.0
        assert dialog.spawn_inherit_size_check.isChecked() is True
        assert dialog.spawn_inherit_dynamic_island_check.isChecked() is False

        # 2. 模拟用户修改各个设置项
        dialog.slingshot_check.setChecked(False)
        dialog.throw_strength_select.setCurrentData("crazy")
        dialog.click_sound_volume_spin.setValue(85)
        dialog.click_sound_picker.set_pack({"kind": "builtin", "id": "duck", "path": ""})
        dialog.spawn_inherit_size_check.setChecked(False)
        dialog.spawn_scale_combo.setCurrentData(0.5)
        dialog.spawn_inherit_dynamic_island_check.setChecked(True)

        dialog.agent_sound_check.setChecked(True)
        dialog.agent_sound_start_check.setChecked(True)
        dialog.agent_sound_start_picker.setText("C:/custom/start.wav")
        dialog.agent_sound_done_check.setChecked(False)
        dialog.agent_sound_error_check.setChecked(True)
        dialog.agent_sound_volume_spin.setValue(90)
        dialog.agent_sound_cooldown_spin.setValue(3.5)

        # 3. 触发写入
        ok = dialog._write_config()
        assert ok is True
    finally:
        dialog.deleteLater()

    # 4. 新建 Config 实例重载验证持久化 round-trip
    reloaded_cfg = Config(cfg_root)
    assert reloaded_cfg.get("slingshot_enabled") is False
    assert reloaded_cfg.get("throw_strength") == "crazy"
    assert reloaded_cfg.get("throw_max_speed") == 9000.0
    assert reloaded_cfg.get("click_sound_volume") == 0.85
    assert reloaded_cfg.get("click_sound_pack") == {"kind": "builtin", "id": "duck", "path": ""}
    assert reloaded_cfg.get("spawn_inherit_size") is False
    assert abs(reloaded_cfg.get("spawn_scale") - 0.5) < 1e-6
    assert reloaded_cfg.get("spawn_inherit_dynamic_island") is True

    agent_cfg = reloaded_cfg.get("agent_link")
    assert agent_cfg["sound_enabled"] is True
    assert agent_cfg["sound_start_enabled"] is True
    assert agent_cfg["sound_start_path"] == "C:/custom/start.wav"
    assert agent_cfg["sound_done_enabled"] is False
    assert agent_cfg["sound_error_enabled"] is True
    assert abs(agent_cfg["sound_volume"] - 0.90) < 1e-4
    assert abs(agent_cfg["sound_cooldown_seconds"] - 3.5) < 1e-4


def test_import_dialogue_template_reads_entries_and_top_level_phrases(tmp_path, monkeypatch):
    """导入模板必须同时读取顶层 phrases 与 entries[].phrases（既有 bug 回归）。"""
    import json

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    cfg = Config(tmp_path / "appdata")
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        # 1) 只改了 entries[].phrases（顶层缺失/为空）的模板也要生效
        entries_only = {
            "template": "persona-phrases/v1",
            "mode": "custom",
            "phrases": {},
            "entries": [
                {"key": "start", "description": "start", "sources": [], "parameters": [],
                 "displayHint": "", "phrases": ["entries 里的台词 {name}"]},
                {"key": "done.success", "description": "done", "sources": [], "parameters": [],
                 "displayHint": "", "phrases": []},
            ],
        }
        dialog.dialogue_template_import_edit.setPlainText(json.dumps(entries_only, ensure_ascii=False))
        dialog._import_dialogue_template_json()
        assert dialog.dialogue_phrase_edits["start"].toPlainText() == "entries 里的台词 {name}"
        assert dialog.dialogue_mode_select.currentData() == "custom"

        # 2) 顶层 phrases 有内容时以顶层为准，不被 entries 覆盖
        both = {
            "template": "persona-phrases/v1",
            "mode": "custom",
            "phrases": {"start": ["顶层台词 {name}"]},
            "entries": [
                {"key": "start", "description": "start", "sources": [], "parameters": [],
                 "displayHint": "", "phrases": ["entries 不应覆盖"]},
            ],
        }
        dialog.dialogue_template_import_edit.setPlainText(json.dumps(both, ensure_ascii=False))
        dialog._import_dialogue_template_json()
        assert dialog.dialogue_phrase_edits["start"].toPlainText() == "顶层台词 {name}"


    finally:
        dialog.deleteLater()


def test_spawn_size_controls_visibility(qapp, tmp_path: Path):
    """生小肥鱼继承大小开启时隐藏自定义大小；关闭后显示。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        row = dialog.findChild(SettingRow, "settingRow_spawn_scale")
        assert row is not None
        assert row.isHidden() is True, "默认继承大小，不应显示自定义小肥鱼大小"

        dialog.spawn_inherit_size_check.setChecked(False)
        assert row.isHidden() is False

        dialog.spawn_inherit_size_check.setChecked(True)
        assert row.isHidden() is True
    finally:
        dialog.deleteLater()


def test_agent_sound_controls_visibility_and_subcontrols(qapp, tmp_path: Path):
    """总开关隐藏子项；单事件开关隐藏路径和试听，但保留自身以便恢复。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        # 初始 sound_enabled=False
        dialog._update_agent_sound_controls(False)
        start_row = dialog.findChild(SettingRow, "settingRow_agent_sound_start")
        assert start_row is not None
        assert start_row.isHidden() is True

        # 开启总开关
        dialog.agent_sound_check.setChecked(True)
        dialog._update_agent_sound_controls(True)
        assert start_row.isHidden() is False

        # 单独关闭 start 事件
        dialog.agent_sound_start_check.setChecked(False)
        assert dialog.agent_sound_start_picker.isHidden() is True
        assert dialog.agent_sound_start_preview.isHidden() is True
        assert dialog.agent_sound_start_check.isHidden() is False

        # 打开 start 事件
        dialog.agent_sound_start_check.setChecked(True)
        assert dialog.agent_sound_start_picker.isHidden() is False
        assert dialog.agent_sound_start_preview.isHidden() is False
    finally:
        dialog.deleteLater()

def test_dialogue_key_params_match_runtime_call_sites():
    """设置页每 key 的“可用参数”提示直接派生自 PARAMETERS（单一真相源）。

    上游重构曾让设置页提示与运行时注入漂移；现在 DIALOGUE_KEY_PARAMS 由
    persona_template.PARAMETERS 派生，这里验证派生关系与代表性条目。"""
    from pet.modern_settings_dialog import DIALOGUE_KEY_PARAMS, DIALOGUE_PARAMS
    from pet.persona_template import PARAMETERS

    assert DIALOGUE_KEY_PARAMS == dict(PARAMETERS)
    # 展示名必须覆盖全部宣称的参数（对话框 hint 渲染用 DIALOGUE_PARAMS[item]）
    advertised = {f for fields in PARAMETERS.values() for f in fields}
    assert advertised <= set(DIALOGUE_PARAMS), sorted(advertised - set(DIALOGUE_PARAMS))
    # activity 组把上游 target 等字段送到表现层后的代表条目
    assert "target" in DIALOGUE_KEY_PARAMS["activity.read"]
    assert DIALOGUE_KEY_PARAMS["rate_limit.one"] == ("count",)
    assert DIALOGUE_KEY_PARAMS["dsh.writeback.failed"] == ()
