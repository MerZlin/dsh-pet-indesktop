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


def test_balance_tier_and_music_settings_persist_through_reload(tmp_path):
    """新增设置项必须能从磁盘重载，否则重启后峰谷文案/颜色/音乐开关会丢。"""
    config = Config(base=tmp_path)
    config.set("balance_tier_labels_mode", "liangwen")
    config.set("balance_tier_label_peak", "梁文峰")
    config.set("balance_tier_label_idle", "梁文谷")
    config.set("balance_tier_color_enabled", False)
    config.set("music_sing_enabled", True)
    config.save()

    reloaded = Config(base=tmp_path)
    assert reloaded.get("balance_tier_labels_mode") == "liangwen"
    assert reloaded.get("balance_tier_label_peak") == "梁文峰"
    assert reloaded.get("balance_tier_label_idle") == "梁文谷"
    assert reloaded.get("balance_tier_color_enabled") is False
    assert reloaded.get("music_sing_enabled") is True


def test_save_returns_false_on_write_failure(tmp_path):
    """写盘失败（此处置目标为目录迫使 os.replace 失败）时 save 返回 False。"""
    config = Config(base=tmp_path)
    config.path.mkdir(parents=True, exist_ok=True)
    assert config.save() is False


def test_reload_preserves_memory_api_key_when_keyring_unavailable(tmp_path, monkeypatch):
    """keyring 不可用时 key 只存内存：磁盘重载（config._load()）不得冲掉内存 key。

    回归背景：设置对话框保存前会调用 config._load() 从磁盘重读以吸收外部改动，
    而 _redacted_data() 写盘时剔除了明文 api_key/vision_api_key —— 磁盘文件里没有
    key，_load() 于是把内存中的 key 覆盖成空，用户没重启就丢了 key。
    """
    # 模拟 keyring 不可用：set 恒失败、get 恒空；否则加载时明文迁移会把内存 key
    # 搬进真实 keyring（见 Config._migrate_plaintext_keys_to_keyring），本测试
    # 要固定的正是「不可用兜底」这条路径。
    class _UnavailableStore:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, ref):
            return ""
        def set(self, ref, value):
            return False
    monkeypatch.setattr("pet.chat.models.SecretStore", _UnavailableStore)

    config = Config(base=tmp_path)
    settings = config.chat_settings()
    provider = settings.active_config
    provider.api_key = "sk-plaintext"
    provider.vision_api_key = "vk-plaintext"
    config.set_chat_settings(settings)
    assert config.save() is True

    # 磁盘文件确实不含明文 key（防回归）
    raw = json.loads(config.path.read_text(encoding="utf-8"))
    disk_provider = raw["chat"]["providers"]["openai-main"]
    assert "api_key" not in disk_provider
    assert "vision_api_key" not in disk_provider

    # 模拟设置对话框保存前从磁盘重读：内存 key 必须仍在
    config._load()
    reloaded = config.chat_settings().active_config
    assert reloaded.api_key == "sk-plaintext"
    assert reloaded.vision_api_key == "vk-plaintext"

    # 磁盘依然没有明文（防回归：_load 不得把内存 key 写回磁盘）
    raw_after = json.loads(config.path.read_text(encoding="utf-8"))
    disk_after = raw_after["chat"]["providers"]["openai-main"]
    assert "api_key" not in disk_after
    assert "vision_api_key" not in disk_after
