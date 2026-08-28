# -*- coding: utf-8 -*-
"""多开配置隔离：--instance 使用独立 config 文件，单开行为不变。"""
from __future__ import annotations

from pet.config import Config


def test_instance_config_is_isolated_from_default(tmp_path):
    instance = Config(base=tmp_path, instance_id="pet2")
    instance.set("rx", 0.5)
    instance.set("ry", 0.6)
    instance.save()

    default = Config(base=tmp_path)
    assert default.get("rx") is None
    assert default.get("ry") is None

    reloaded = Config(base=tmp_path, instance_id="pet2")
    assert reloaded.get("rx") == 0.5
    assert reloaded.get("ry") == 0.6


def test_default_config_still_uses_plain_file(tmp_path):
    default = Config(base=tmp_path)
    assert default.path.name == "config.json"

    instance = Config(base=tmp_path, instance_id="pet3")
    assert instance.path.name == "config-pet3.json"
