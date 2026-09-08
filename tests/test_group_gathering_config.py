# -*- coding: utf-8 -*-
"""聚集互动配置键：默认关闭、规范化、持久化与子槽落种。"""
from __future__ import annotations

import json

from pet.config import Config


def _main_config(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", {"enabled": True, "auto_enabled": True})
    cfg.save()
    return cfg


def test_default_disabled_and_auto_disabled(tmp_path):
    cfg = Config(base=tmp_path)
    value = cfg.get("group_gathering", {})
    assert value == {"enabled": False, "auto_enabled": False}


def test_auto_requires_enabled_even_with_dirty_input(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", {"enabled": False, "auto_enabled": True})
    assert cfg.get("group_gathering") == {"enabled": False, "auto_enabled": False}


def test_invalid_value_returns_defaults(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("group_gathering", None)
    assert cfg.get("group_gathering") == {"enabled": False, "auto_enabled": False}
    cfg.set("group_gathering", "bad")
    assert cfg.get("group_gathering") == {"enabled": False, "auto_enabled": False}


def test_roundtrip_persists(tmp_path):
    cfg = _main_config(tmp_path)
    reloaded = Config(base=tmp_path)
    assert reloaded.get("group_gathering") == {"enabled": True, "auto_enabled": True}


def test_new_slot_seeds_from_main(tmp_path):
    main = Config(base=tmp_path)
    main.set("group_gathering", {"enabled": True, "auto_enabled": False})
    main.save()

    slot = Config(base=tmp_path, instance_id="slot-1")
    assert slot.get("group_gathering") == {"enabled": True, "auto_enabled": False}


def test_disk_dirty_bool_strings_normalized(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    cfg.path.write_text(json.dumps({
        "version": 4,
        "group_gathering": {"enabled": "true", "auto_enabled": "false"},
    }), encoding="utf-8")
    reloaded = Config(base=tmp_path)
    assert reloaded.get("group_gathering") == {"enabled": True, "auto_enabled": False}
