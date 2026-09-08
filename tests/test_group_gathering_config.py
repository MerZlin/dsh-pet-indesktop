# -*- coding: utf-8 -*-
"""聚集互动配置键：默认关闭、规范化、持久化与子槽落种。"""
from __future__ import annotations

import json

from pet.config import Config

DEFAULT_GROUP = {"enabled": False, "auto_enabled": False, "preset": ""}


def _main_config(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", {"enabled": True, "auto_enabled": True, "preset": "breakfast"})
    cfg.save()
    return cfg


def test_default_disabled_and_auto_disabled(tmp_path):
    cfg = Config(base=tmp_path)
    value = cfg.get("group_gathering", {})
    assert value == DEFAULT_GROUP


def test_auto_requires_enabled_even_with_dirty_input(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", {"enabled": False, "auto_enabled": True})
    assert cfg.get("group_gathering") == DEFAULT_GROUP


def test_invalid_value_returns_defaults(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", None)
    assert cfg.get("group_gathering") == DEFAULT_GROUP
    cfg.set("group_gathering", "bad")
    assert cfg.get("group_gathering") == DEFAULT_GROUP


def test_roundtrip_persists_preset(tmp_path):
    cfg = _main_config(tmp_path)
    reloaded = Config(base=tmp_path)
    assert reloaded.get("group_gathering") == {
        "enabled": True, "auto_enabled": True, "preset": "breakfast",
    }


def test_invalid_preset_is_cleaned_to_random(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", {"enabled": True, "auto_enabled": False, "preset": "not-a-preset"})
    assert cfg.get("group_gathering") == {
        "enabled": True, "auto_enabled": False, "preset": "",
    }


def test_new_slot_seeds_from_main_with_preset(tmp_path):
    main = Config(base=tmp_path)
    main.set("group_gathering", {"enabled": True, "auto_enabled": False, "preset": "hotpot"})
    main.save()

    slot = Config(base=tmp_path, instance_id="slot-1")
    assert slot.get("group_gathering") == {
        "enabled": True, "auto_enabled": False, "preset": "hotpot",
    }


def test_disk_dirty_bool_strings_normalized(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    cfg.path.write_text(json.dumps({
        "version": 4,
        "group_gathering": {
            "enabled": "true",
            "auto_enabled": "false",
            "preset": "rice",
        },
    }), encoding="utf-8")
    reloaded = Config(base=tmp_path)
    assert reloaded.get("group_gathering") == {
        "enabled": True, "auto_enabled": False, "preset": "rice",
    }
