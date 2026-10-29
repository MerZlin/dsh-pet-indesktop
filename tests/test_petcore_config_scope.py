from __future__ import annotations

from pathlib import Path

from petcore.config import Config, ScopedConfig


def test_scoped_config_routes_pet_and_global_keys(tmp_path: Path) -> None:
    global_config = Config(tmp_path)
    pet_config = Config(tmp_path, {"character": "child", "scale": 0.5}, filename="config-pet-1.json")
    scoped = ScopedConfig(global_config, pet_config)

    assert scoped.get("character") == "child"
    assert scoped.get("scale") == 0.5
    assert scoped.get("ui_font_size") == global_config.get("ui_font_size")

    scoped.set("character", "other-child")
    scoped.set("ui_font_size", 16)

    assert pet_config.get("character") == "other-child"
    assert global_config.get("character") == "shenshen"
    assert global_config.get("ui_font_size") == 16
    assert pet_config.get("ui_font_size") != 16


def test_scoped_config_snapshot_restore_covers_both_files(tmp_path: Path) -> None:
    global_config = Config(tmp_path)
    pet_config = Config(tmp_path, {"character": "child"}, filename="config-pet-1.json")
    scoped = ScopedConfig(global_config, pet_config)
    snapshot = scoped.snapshot()

    scoped.set("character", "changed")
    scoped.set("ui_font_size", 18)
    scoped.restore(snapshot)

    assert pet_config.get("character") == "child"
    assert global_config.get("ui_font_size") == snapshot["ui_font_size"]


def test_scoped_config_save_persists_global_and_pet(tmp_path: Path) -> None:
    global_config = Config(tmp_path)
    pet_config = Config(tmp_path, {"character": "child"}, filename="config-pet-1.json")
    scoped = ScopedConfig(global_config, pet_config)
    scoped.set("character", "saved-child")
    scoped.set("menu_layout", {"schema_version": 1, "nodes": []})
    scoped.save()

    assert Config.load(tmp_path).get("menu_layout") == {"schema_version": 1, "nodes": []}
    assert Config.load(tmp_path, filename="config-pet-1.json").get("character") == "saved-child"


def test_legacy_config_and_slot_profiles_migrate_with_backup(tmp_path: Path, monkeypatch) -> None:
    old_root = tmp_path / "old-standalone"
    new_root = tmp_path / "new-core"
    old_root.mkdir()
    (old_root / "config.json").write_text(
        '{"character":"old-main","scale":0.8,"mouse_through":true,"ui_font_size":15}',
        encoding="utf-8",
    )
    (old_root / "config-slot-1.json").write_text(
        '{"character":"old-child","scale":0.55,"self_talk_enabled":true}',
        encoding="utf-8",
    )
    monkeypatch.setattr("petcore.paths.legacy_data_roots", lambda: (old_root,))

    config = Config.load(new_root)
    child = Config.load(new_root, filename="config-pet-1.json")

    assert config.get("character") == "old-main"
    assert config.get("click_through") is True
    assert config.get("ui_font_size") == 15
    assert child.get("character") == "old-child"
    assert child.get("scale") == 0.55
    assert child.get("self_talk_enabled") is True
    assert (new_root / "migration-backup" / "old-standalone-config.json").exists()
    assert (old_root / "config.json").exists()


def test_proactive_keys_are_global_and_normalized(tmp_path: Path) -> None:
    global_config = Config(tmp_path)
    pet_config = Config(tmp_path, {"character": "child"}, filename="config-pet-1.json")
    scoped = ScopedConfig(global_config, pet_config)

    assert scoped.get("ai_proactive_enabled") is False
    assert scoped.get("ai_proactive_whitelist") == []
    assert scoped.get("ai_proactive_dwell_seconds") == 45
    assert scoped.get("ai_proactive_change_threshold") == 8
    assert scoped.get("ai_proactive_daily_cap") == 15
    assert scoped.get("ai_proactive_cooldown_seconds") == 300
    assert scoped.get("ai_proactive_min_interval_seconds") == 60
    assert scoped.get("ai_proactive_require_idle") is False
    assert scoped.get("ai_proactive_min_idle_seconds") == 30
    assert scoped.get("ai_proactive_dry_run") is False
    assert scoped.get("ai_proactive_pre_cue") is True
    assert scoped.get("ai_proactive_sync_chat") is True

    scoped.set("ai_proactive_enabled", True)
    scoped.set("ai_proactive_whitelist", [" chrome.exe ", "", "title:*报告*", "x" * 300])
    scoped.set("ai_proactive_dwell_seconds", 1)
    scoped.set("ai_proactive_change_threshold", 99)
    scoped.set("ai_proactive_daily_cap", 0)
    scoped.set("ai_proactive_cooldown_seconds", 5)
    scoped.set("ai_proactive_min_interval_seconds", 99999)

    assert pet_config.get("ai_proactive_enabled") is False, "全局面不写子宠配置"
    assert global_config.get("ai_proactive_enabled") is True
    rules = global_config.get("ai_proactive_whitelist")
    assert rules[0] == "chrome.exe"
    assert rules[1] == "title:*报告*"
    assert len(rules) == 3
    assert all(len(rule) <= 120 for rule in rules)
    assert global_config.get("ai_proactive_dwell_seconds") == 15
    assert global_config.get("ai_proactive_change_threshold") == 32
    assert global_config.get("ai_proactive_daily_cap") == 1
    assert global_config.get("ai_proactive_cooldown_seconds") == 30
    assert global_config.get("ai_proactive_min_interval_seconds") == 3600


def test_proactive_whitelist_caps_at_fifty_rules(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("ai_proactive_whitelist", [f"app{index}.exe" for index in range(80)])
    assert len(config.get("ai_proactive_whitelist")) == 50


def test_squash_elasticity_keys_are_per_pet_and_clamped(tmp_path: Path) -> None:
    global_config = Config(tmp_path)
    pet_config = Config(tmp_path, {"character": "child"}, filename="config-pet-1.json")
    scoped = ScopedConfig(global_config, pet_config)

    assert scoped.get("click_squash_bounces") == 1
    assert scoped.get("click_squash_strength") == 100
    assert scoped.get("click_squash_ms") == 220

    scoped.set("click_squash_bounces", 99)
    scoped.set("click_squash_strength", 5)
    scoped.set("click_squash_ms", 5000)

    assert global_config.get("click_squash_bounces") == 1, "本宠键不写全局面"
    assert pet_config.get("click_squash_bounces") == 5
    assert pet_config.get("click_squash_strength") == 30
    assert pet_config.get("click_squash_ms") == 1000
