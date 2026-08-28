# -*- coding: utf-8 -*-
"""多开配置隔离：--instance 使用独立 config 文件，单开行为不变。"""
from __future__ import annotations

import json

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


def test_save_redacts_api_keys_from_disk(tmp_path):
    """keyring 不可用时 key 只保留内存；写盘副本必须剔除明文 api_key/vision_api_key。"""
    config = Config(base=tmp_path)
    settings = config.chat_settings()
    provider = settings.active_config
    provider.api_key = "sk-plaintext"
    provider.vision_api_key = "vk-plaintext"
    config.set_chat_settings(settings)
    assert config.save() is True

    raw = json.loads(config.path.read_text(encoding="utf-8"))
    disk_provider = raw["chat"]["providers"]["openai-main"]
    assert "api_key" not in disk_provider
    assert "vision_api_key" not in disk_provider
    # 内存中保留，本次运行仍可用
    assert config.chat_settings().active_config.api_key == "sk-plaintext"
    assert config.chat_settings().active_config.vision_api_key == "vk-plaintext"


def test_save_returns_false_on_write_failure(tmp_path):
    """写盘失败（此处置目标为目录迫使 os.replace 失败）时 save 返回 False。"""
    config = Config(base=tmp_path)
    config.path.mkdir(parents=True, exist_ok=True)
    assert config.save() is False
