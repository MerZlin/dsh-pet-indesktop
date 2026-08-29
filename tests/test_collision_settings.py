# -*- coding: utf-8 -*-
"""Phase 4 验收测试：碰撞设置项配置、UI 控件、round-trip、非法值归一化与运行时即时生效。"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog
from pet.settings_dialog import PetSettingsDialog
from pet.window import PetWindow
from tests.test_collision_window import FakeLibrary, FakeCollisionSession


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_collision_config_defaults_and_normalization(tmp_path: Path):
    """测试碰撞参数默认值与非法值边界归一化。"""
    cfg_dir = tmp_path
    
    # 默认值
    cfg = Config(cfg_dir)
    assert cfg.get("collision_enabled") is True
    assert cfg.get("collision_restitution") == pytest.approx(0.82)
    assert cfg.get("collision_friction") == pytest.approx(0.08)
    assert cfg.get("collision_mass_scale") == pytest.approx(1.0)
    assert cfg.get("collision_impulse_cap") == pytest.approx(9000.0)

    # 写入越界/非法值，验证 _load 自动归一化
    raw_data = {
        "collision_enabled": "not_bool",
        "collision_restitution": 99.0,   # max 1.0
        "collision_friction": -5.0,      # min 0.0
        "collision_mass_scale": 10.0,    # max 2.0
        "collision_impulse_cap": 500.0,  # min 1000.0
    }
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f)

    reloaded = Config(cfg_dir)
    assert reloaded.get("collision_enabled") is True
    assert reloaded.get("collision_restitution") == pytest.approx(1.0)
    assert reloaded.get("collision_friction") == pytest.approx(0.0)
    assert reloaded.get("collision_mass_scale") == pytest.approx(2.0)
    assert reloaded.get("collision_impulse_cap") == pytest.approx(1000.0)


def test_modern_settings_dialog_collision_ui_and_round_trip(qapp, tmp_path: Path):
    """验证 ModernSettingsDialog 中的碰撞控件存在、修改并持久化到磁盘。"""
    cfg_file = tmp_path / "appdata"
    cfg = Config(cfg_file)

    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        # 1. 验证控件初始值
        assert hasattr(dialog, "collision_enabled_check")
        assert hasattr(dialog, "collision_restitution_spin")
        assert hasattr(dialog, "collision_friction_spin")
        assert hasattr(dialog, "collision_mass_scale_spin")
        assert hasattr(dialog, "collision_impulse_cap_spin")

        assert dialog.collision_enabled_check.isChecked() is True
        assert dialog.collision_restitution_spin.value() == pytest.approx(0.82)
        assert dialog.collision_friction_spin.value() == pytest.approx(0.08)
        assert dialog.collision_mass_scale_spin.value() == pytest.approx(1.0)
        assert dialog.collision_impulse_cap_spin.value() == pytest.approx(9000.0)

        # 2. 修改碰撞设置
        dialog.collision_enabled_check.setChecked(False)
        dialog.collision_restitution_spin.setValue(0.50)
        dialog.collision_friction_spin.setValue(0.15)
        dialog.collision_mass_scale_spin.setValue(1.50)
        dialog.collision_impulse_cap_spin.setValue(6000.0)

        # 3. 保存落盘
        ok = dialog._write_config()
        assert ok is True
    finally:
        dialog.deleteLater()

    # 4. 重新加载 Config 验证持久化
    reloaded_cfg = Config(cfg_file)
    assert reloaded_cfg.get("collision_enabled") is False
    assert reloaded_cfg.get("collision_restitution") == pytest.approx(0.50)
    assert reloaded_cfg.get("collision_friction") == pytest.approx(0.15)
    assert reloaded_cfg.get("collision_mass_scale") == pytest.approx(1.50)
    assert reloaded_cfg.get("collision_impulse_cap") == pytest.approx(6000.0)


def test_legacy_settings_dialog_collision_toggle_round_trip(qapp, tmp_path: Path):
    """验证旧版 PetSettingsDialog 总开关存在并能正确保存。"""
    cfg_file = tmp_path / "appdata"
    cfg = Config(cfg_file)

    dialog = PetSettingsDialog(cfg, enable_chat=False)
    try:
        assert hasattr(dialog, "collision_enabled_check")
        assert dialog.collision_enabled_check.isChecked() is True

        dialog.collision_enabled_check.setChecked(False)
        dialog._save()
    finally:
        dialog.deleteLater()

    reloaded_cfg = Config(cfg_file)
    assert reloaded_cfg.get("collision_enabled") is False


def test_refresh_pet_settings_live_applies_collision_switch(qapp, tmp_path: Path):
    """验证运行中 refresh_pet_settings() 开启/关闭碰撞即时生效（attach/detach session）。"""
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg.set("collision_enabled", True)
    lib = FakeLibrary()
    session = FakeCollisionSession("pet_live_test")
    win = PetWindow(lib, cfg, collision_session=session)
    try:
        win.resize(100, 100)
        win.show()
        assert win._collision_session is session

        # 运行时关闭
        cfg.set("collision_enabled", False)
        win.refresh_pet_settings()
        assert win._collision_session is None

        # 运行时重新开启
        cfg.set("collision_enabled", True)
        win.refresh_pet_settings()
        assert win._collision_session is session
    finally:
        win.close()


def test_modern_settings_dialog_collision_policy_note(qapp, tmp_path: Path):
    """「多开碰撞」区有一行协调者配置优先的说明文案。"""
    dialog = ModernSettingsDialog(Config(tmp_path / "appdata"), include_ai=False)
    try:
        assert hasattr(dialog, "collision_policy_note")
        assert "碰撞参数由当前协调者桌宠的设置决定" in dialog.collision_policy_note.text()
    finally:
        dialog.deleteLater()


def test_refresh_pet_settings_syncs_collision_policy(qapp, tmp_path: Path):
    """运行中改碰撞参数：refresh_pet_settings 后 session policy 更新（协调者配置优先）。"""
    cfg = Config(str(tmp_path / "cfg_policy.json"))
    cfg.set("collision_enabled", True)
    lib = FakeLibrary()
    session = FakeCollisionSession("pet_policy")
    win = PetWindow(lib, cfg, collision_session=session)
    try:
        win.resize(100, 100)
        win.show()
        # 初始 attach 已同步一次默认策略
        assert len(session.policy_updates) >= 1
        assert session.policy_updates[-1]["collision_restitution"] == pytest.approx(0.82)

        # 运行中修改参数 → 重新同步
        cfg.set("collision_restitution", 0.5)
        cfg.set("collision_friction", 0.15)
        cfg.set("collision_mass_scale", 1.5)
        cfg.set("collision_impulse_cap", 6000.0)
        win.refresh_pet_settings()
        policy = session.policy_updates[-1]
        assert policy["collision_restitution"] == pytest.approx(0.5)
        assert policy["collision_friction"] == pytest.approx(0.15)
        assert policy["collision_mass_scale"] == pytest.approx(1.5)
        assert policy["collision_impulse_cap"] == pytest.approx(6000.0)

        # 参数未变时不重复推送
        before = len(session.policy_updates)
        win.refresh_pet_settings()
        assert len(session.policy_updates) == before
    finally:
        win.close()
