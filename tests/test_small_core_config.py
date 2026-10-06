"""Small Core preserves opaque AI data without policy imports or credential reads."""

from __future__ import annotations

import builtins
import json

import pytest

from pet.config import APP_DIR_NAME, Config


def test_small_core_does_not_supply_provider_or_file_policy_defaults(tmp_path, monkeypatch):
    from pet import feature_distribution

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    cfg = Config(base=tmp_path)
    assert cfg.get("chat") == {}
    assert cfg.get("file_interpret") == {}


def test_retained_ai_data_is_not_normalized_and_never_reads_legacy_credentials(tmp_path, monkeypatch):
    from pet import feature_distribution

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    path = tmp_path / APP_DIR_NAME / "config.json"
    path.parent.mkdir(parents=True)
    retained = {
        "active_provider": "future",
        "providers": {"future": {"vendor_option": [1, 2], "api_key": "generated-legacy-only"}},
        "future_policy": {"keep": True},
    }
    path.write_text(json.dumps({"version": 4, "chat": retained, "file_interpret": {"future": "opaque"}}), encoding="utf-8")
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        if name.startswith(("features.ai_chat", "pet.chat")) or name == "chat.models":
            raise AssertionError("small Core imported AI policy/credential implementation")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)
    cfg = Config(base=tmp_path)
    assert cfg.get("chat") == retained
    assert cfg.get("file_interpret") == {"future": "opaque"}
    assert cfg.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(retained))
    expected["providers"]["future"].pop("api_key")
    assert saved["chat"] == expected
    assert "generated-legacy-only" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("method", ["chat_settings", "chat_config", "resolve_api_key", "set_chat_settings"])
def test_legacy_ai_facades_cannot_bypass_owner_in_small_core(tmp_path, monkeypatch, method):
    from pet import feature_distribution

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    cfg = Config(base=tmp_path)
    args = (object(),) if method in {"resolve_api_key", "set_chat_settings"} else ()
    with pytest.raises(PermissionError, match="ai_requires_owner_context"):
        getattr(cfg, method)(*args)


def test_new_product_never_implicitly_copies_legacy_data(tmp_path, monkeypatch):
    from pet import config as config_module
    from pet import feature_distribution

    monkeypatch.setenv("APPDATA", str(tmp_path / "isolated-appdata"))
    monkeypatch.setattr(config_module, "APP_DIR_NAME", "dsh-pet-core-webm")
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    legacy = tmp_path / "dsh-pet-standalone"
    sessions = legacy / "sessions" / "generated-owner"
    sessions.mkdir(parents=True)
    config_text = json.dumps({"version": 4, "scale": 1.8, "chat": {"future": "generated-only"}})
    (legacy / "config.json").write_text(config_text, encoding="utf-8")
    (sessions / "generated-session.json").write_text('{"generated": true}', encoding="utf-8")
    cfg = config_module.Config(base=tmp_path)
    assert cfg.get("scale") != 1.8
    assert cfg.get("chat") == {}
    assert not cfg.path.exists()
    assert not (cfg.dir / "sessions").exists()
    assert (legacy / "config.json").read_text(encoding="utf-8") == config_text
