"""重置设置（纯逻辑）：按键作用域、保留项、落盘与失败回滚。

petcore 与主分支的存储布局不同：主宠的全局设置与自己的偏好都在 ``config.json``，
子宠偏好另存 ``config-pet-N.json``。所以「重置本宠」与「重置全局」是**两组键**，
这里钉住的就是这条边界：谁都不许把对方那一半顺手清掉。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from petcore import settings_reset
from petcore.config import DEFAULTS, Config, ScopedConfig


def _config(
    tmp_path: Path, data: dict | None = None, *, filename: str | None = None
) -> Config:
    return Config(tmp_path, dict(data or {}), filename=filename)


def test_global_scope_is_global_keys_minus_preserved() -> None:
    keys = set(settings_reset.reset_keys(settings_reset.SCOPE_GLOBAL))
    assert "ui_font_size" in keys
    assert "self_talk_enabled" not in keys
    assert keys.isdisjoint(settings_reset.PRESERVED_ALWAYS)


def test_pet_scope_is_pet_keys_minus_identity() -> None:
    keys = set(settings_reset.reset_keys(settings_reset.SCOPE_PET))
    assert "self_talk_enabled" in keys
    assert "ui_font_size" not in keys
    assert keys.isdisjoint(
        settings_reset.PRESERVED_ALWAYS | settings_reset.PET_IDENTITY_KEYS
    )


def test_global_reset_keeps_placement_identity_and_pet_preferences(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.set("ui_font_size", 17)
    config.set("active_pets", [0, 2])
    config.set("positions", {"0": {"rx": 0.5, "ry": 0.5}, "2": {"rx": 0.2, "ry": 0.8}})
    config.set("character", "fallback")
    config.set("self_talk_enabled", False)
    positions = config.get("positions")

    changed = settings_reset.apply_reset(config, settings_reset.SCOPE_GLOBAL)

    assert "ui_font_size" in changed
    assert config.get("ui_font_size") == DEFAULTS["ui_font_size"]
    assert config.get("active_pets") == [0, 2]
    assert config.get("positions") == positions
    assert config.get("character") == "fallback"
    assert config.get("self_talk_enabled") is False


def test_pet_reset_keeps_identity_placement_and_global_keys(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.set("character", "fallback")
    config.set("rx", 0.25)
    config.set("ry", 0.75)
    config.set("screen_name", "DISPLAY1")
    config.set("scale", 1.4)
    config.set("self_talk_enabled", True)
    config.set("click_talk_texts", ["自定义台词"])
    config.set("ui_font_size", 17)
    config.set("active_pets", [0, 1])

    changed = settings_reset.apply_reset(config, settings_reset.SCOPE_PET)

    assert {"scale", "self_talk_enabled", "click_talk_texts"} <= set(changed)
    assert config.get("scale") == DEFAULTS["scale"]
    assert config.get("self_talk_enabled") == DEFAULTS["self_talk_enabled"]
    assert config.get("click_talk_texts") == DEFAULTS["click_talk_texts"]
    # 身份与位置：重置不碰。
    assert config.get("character") == "fallback"
    assert config.get("rx") == 0.25
    assert config.get("ry") == 0.75
    assert config.get("screen_name") == "DISPLAY1"
    # 全局键：本宠重置不碰。
    assert config.get("ui_font_size") == 17
    assert config.get("active_pets") == [0, 1]


def test_reset_writes_defaults_to_disk(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.set("ui_font_size", 17)
    settings_reset.apply_reset(config, settings_reset.SCOPE_GLOBAL)
    raw = json.loads(config.path.read_text(encoding="utf-8"))
    assert raw["ui_font_size"] == DEFAULTS["ui_font_size"]


def test_scoped_reset_on_the_main_pet_splits_scopes(tmp_path: Path) -> None:
    """主宠：全局与自己的偏好同在一个文件，两个作用域仍各管一半。"""
    global_config = _config(tmp_path, filename="config.json")
    global_config.set("ui_font_size", 17)
    global_config.set("self_talk_enabled", False)
    scoped = ScopedConfig(global_config)

    settings_reset.apply_reset(scoped, settings_reset.SCOPE_PET)
    assert global_config.get("self_talk_enabled") == DEFAULTS["self_talk_enabled"]
    assert global_config.get("ui_font_size") == 17

    global_config.set("self_talk_enabled", False)
    settings_reset.apply_reset(scoped, settings_reset.SCOPE_GLOBAL)
    assert global_config.get("ui_font_size") == DEFAULTS["ui_font_size"]
    assert global_config.get("self_talk_enabled") is False


def test_child_pet_reset_writes_only_its_own_file(tmp_path: Path) -> None:
    global_config = _config(tmp_path, filename="config.json")
    pet_config = _config(tmp_path, filename="config-pet-1.json")
    global_config.set("ui_font_size", 17)
    pet_config.set("scale", 1.4)
    pet_config.set("character", "fallback")
    scoped = ScopedConfig(global_config, pet_config)

    settings_reset.apply_reset(scoped, settings_reset.SCOPE_PET)

    raw = json.loads((tmp_path / "config-pet-1.json").read_text(encoding="utf-8"))
    assert raw["scale"] == DEFAULTS["scale"]
    assert raw["character"] == "fallback"
    # 全局键（ui_font_size）写的是全局文件，子宠文件里仍是默认值。
    assert raw["ui_font_size"] == DEFAULTS["ui_font_size"]
    assert global_config.get("ui_font_size") == 17


def test_save_failure_rolls_back_memory_and_raises(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    config.set("ui_font_size", 17)

    def boom() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(config, "save", boom)
    with pytest.raises(settings_reset.ResetError):
        settings_reset.apply_reset(config, settings_reset.SCOPE_GLOBAL)
    assert config.get("ui_font_size") == 17
