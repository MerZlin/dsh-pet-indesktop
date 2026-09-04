# -*- coding: utf-8 -*-
"""加载时把磁盘明文 API Key 迁移进 keyring 的回归测试。

回归背景：v4.0.4/4.0.5 起 _redacted_data() 写盘时剔除 chat.providers 下的明文
api_key/vision_api_key，但 SecretStore.set 只在设置对话框保存时调用——老版本
(≤v4.0.0) 用户磁盘上的明文 key 从未进过 keyring，升级后首次写盘即被剔除，
重启后 resolve_api_key 拿不到任何值，聊天/视觉 401 静默失效。
"""
from __future__ import annotations

import json

import pytest

import pet.chat.models as chat_models
from pet.config import APP_DIR_NAME, Config


class FakeStore:
    """模拟 SecretStore：实例共享一个 dict；set 可控成败。"""

    shared: dict = {}
    set_ok: bool = True

    def __init__(self, service_name="dsh-pet-standalone"):
        self.service_name = service_name

    @property
    def available(self):
        return True

    def get(self, ref):
        if not ref:
            return ""
        return self.shared.get(ref, "")

    def set(self, ref, value):
        if not self.set_ok or not ref:
            return False
        self.shared[ref] = value
        return True


@pytest.fixture(autouse=True)
def _fake_secret_store(monkeypatch):
    FakeStore.shared = {}
    FakeStore.set_ok = True
    monkeypatch.setattr(chat_models, "SecretStore", FakeStore)


def _write_disk_config(tmp_path, providers):
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"version": 4, "chat": {"providers": providers}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_plaintext_api_key_migrated_to_keyring_on_load(tmp_path):
    """磁盘 config.json 含明文 api_key：加载后迁移进 keyring，内存/磁盘均无明文。"""
    _write_disk_config(tmp_path, {
        "openai-main": {"name": "DeepSeek", "api_key": "sk-legacy-plaintext"},
    })

    config = Config(base=tmp_path)

    # keyring 收到默认 ref 的 key
    assert FakeStore.shared["provider/openai-main"] == "sk-legacy-plaintext"
    # 内存中明文已被移除
    provider = config.data["chat"]["providers"]["openai-main"]
    assert not provider.get("api_key")
    # 幂等：重复 reload 无副作用
    config.reload()
    assert FakeStore.shared["provider/openai-main"] == "sk-legacy-plaintext"
    # save() 后磁盘也无明文
    assert config.save() is True
    raw = json.loads(config.path.read_text(encoding="utf-8"))
    disk_provider = raw["chat"]["providers"]["openai-main"]
    assert "api_key" not in disk_provider


def test_plaintext_kept_in_memory_when_keyring_unavailable(tmp_path):
    """keyring 不可用（set 返回 False）→ 内存保留明文，维持原兜底行为。"""
    _write_disk_config(tmp_path, {
        "openai-main": {"name": "DeepSeek", "api_key": "sk-legacy-plaintext"},
    })
    FakeStore.set_ok = False

    config = Config(base=tmp_path)

    assert FakeStore.shared == {}
    provider = config.data["chat"]["providers"]["openai-main"]
    assert provider["api_key"] == "sk-legacy-plaintext"


def test_existing_keyring_value_not_overwritten(tmp_path):
    """keyring 已有该 ref 的值 → 不覆盖，仅丢弃明文。"""
    _write_disk_config(tmp_path, {
        "openai-main": {"name": "DeepSeek", "api_key": "sk-legacy-plaintext"},
    })
    FakeStore.shared["provider/openai-main"] = "sk-already-in-keyring"

    config = Config(base=tmp_path)

    assert FakeStore.shared["provider/openai-main"] == "sk-already-in-keyring"
    provider = config.data["chat"]["providers"]["openai-main"]
    assert not provider.get("api_key")


def test_vision_api_key_migrated_with_default_ref(tmp_path):
    """vision_api_key 迁移：ref 为空时用默认 ref provider/<pid>/vision 并回填。"""
    _write_disk_config(tmp_path, {
        "openai-main": {"name": "DeepSeek", "vision_api_key": "vk-legacy-plaintext"},
    })

    config = Config(base=tmp_path)

    assert FakeStore.shared["provider/openai-main/vision"] == "vk-legacy-plaintext"
    provider = config.data["chat"]["providers"]["openai-main"]
    assert not provider.get("vision_api_key")
    assert provider["vision_api_key_ref"] == "provider/openai-main/vision"
