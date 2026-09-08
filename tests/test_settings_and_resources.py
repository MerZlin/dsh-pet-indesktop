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


def test_subfish_settings_save_sets_user_customized(qapp, tmp_path: Path):
    """批 C：子肥鱼自己的设置界面保存会置位 user_customized=True。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root, instance_id="slot-1")
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        ok = dialog._write_config()
        assert ok is True
    finally:
        dialog.deleteLater()
    assert cfg.get("user_customized") is True
    reloaded = Config(cfg_root, instance_id="slot-1")
    assert reloaded.get("user_customized") is True


def test_main_settings_save_does_not_set_user_customized(qapp, tmp_path: Path):
    """批 C：主配置（slot 0/主肥鱼）保存不置位 user_customized（保持默认假）。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        ok = dialog._write_config()
        assert ok is True
    finally:
        dialog.deleteLater()
    assert cfg.get("user_customized") is False
    reloaded = Config(cfg_root)
    assert reloaded.get("user_customized") is False


def test_position_autosave_does_not_set_user_customized(tmp_path):
    """批 C：位置自动保存等后台写盘不得置位 user_customized（保留默认假）。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root, instance_id="slot-2")
    # 模拟窗口 _save_position：写位置键 + save()，不经过设置界面。
    cfg.set("rx", 0.5)
    cfg.set("ry", 0.5)
    cfg.set("screen_name", "X")
    cfg.set("facing", "right")
    cfg.save()
    assert cfg.get("user_customized") is False
    reloaded = Config(cfg_root, instance_id="slot-2")
    assert reloaded.get("user_customized") is False


def test_clear_spawned_pets_button_routes_through_shell_callback(
        qapp, tmp_path: Path, monkeypatch):
    """批 E：设置界面「一键清除」优先调 PetWindow 已接线的 on_clear_spawned_pets
    （= AppShell 路径，自带确认框与进程内子窗前置于关闭）——对话框不再二次确认，
    也不直接走文件级清理。"""
    from PySide6.QtWidgets import QMessageBox, QWidget

    import pet.child_pet_cleanup as cleanup_mod

    calls = []
    parent = QWidget()
    parent.on_clear_spawned_pets = lambda: calls.append("shell")
    cfg = Config(tmp_path / "appdata")
    questions = []
    cleanup_calls = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: (questions.append(a),
                          QMessageBox.StandardButton.Cancel)[1])
    monkeypatch.setattr(
        cleanup_mod, "clear_spawned_pets",
        lambda *a, **kw: cleanup_calls.append(a) or
        {"killed_pids": [], "deleted": []})
    dialog = ModernSettingsDialog(cfg, parent, include_ai=False)
    try:
        dialog.clear_spawned_pets_btn.click()
        assert calls == ["shell"], "应调用 win.on_clear_spawned_pets 回调"
        assert questions == [], "走 shell 回调时对话框不应二次确认"
        assert cleanup_calls == [], "走 shell 回调时不应直接文件级清理"
    finally:
        dialog.deleteLater()
        parent.close()
        qapp.processEvents()


def test_clear_spawned_pets_button_falls_back_without_callback(
        qapp, tmp_path: Path, monkeypatch):
    """批 E：拿不到 win.on_clear_spawned_pets（无父窗/旧接线）时回退原有
    「确认 + 直接文件级清理」路径。"""
    from PySide6.QtWidgets import QMessageBox

    import pet.child_pet_cleanup as cleanup_mod

    cfg = Config(tmp_path / "appdata")
    cleaned = []
    monkeypatch.setattr(
        cleanup_mod, "clear_spawned_pets",
        lambda cfg_dir: (cleaned.append(cfg_dir),
                         {"killed_pids": [1], "deleted": ["a", "b"]})[1])
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    infos = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **kw: infos.append(a))
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        dialog._on_clear_spawned_pets()
        assert cleaned == [cfg.dir]
        assert infos == [], "批 I：结果弹窗已移除（结果写日志）"
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_clear_spawned_pets_button_fallback_runs_without_dialogs(
        qapp, tmp_path: Path, monkeypatch):
    """批 I：回退路径无确认框无结果框，一键直接静默执行。"""
    from PySide6.QtWidgets import QMessageBox

    import pet.child_pet_cleanup as cleanup_mod

    cfg = Config(tmp_path / "appdata")
    cleanup_calls = []
    monkeypatch.setattr(
        cleanup_mod, "clear_spawned_pets",
        lambda *a, **kw: cleanup_calls.append(a) or
        {"killed_pids": [], "deleted": []})
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: (questions.append(1),
                          QMessageBox.StandardButton.Cancel)[1])
    infos = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **kw: infos.append(a))
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        dialog._on_clear_spawned_pets()
        assert questions == [], "批 I：无确认框"
        assert cleanup_calls != [], "回退路径直接执行清理"
        assert infos == [], "批 I：无结果框"
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_clear_spawned_pets_button_disabled_for_child_config(qapp, tmp_path):
    """批 G：子肥鱼（slot-N）的设置对话框禁用「一键退出」按钮——该操作只对
    主肥鱼开放（子鱼进程执行会把主鱼当子鱼杀掉）。"""
    cfg = Config(tmp_path / "appdata", instance_id="slot-1")
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        assert not dialog.clear_spawned_pets_btn.isEnabled()
        assert dialog.clear_spawned_pets_btn.toolTip()
    finally:
        dialog.deleteLater()
        qapp.processEvents()
