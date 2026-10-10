"""Core API authority: generated credentials, no network or real secret store."""

import json
from dataclasses import replace

import pytest

from pet.config import Config
from tests.screen_fakes import MemoryVault


def provision(tmp_path):
    from pet.api_config import ApiService, CoreApiConfiguration

    cfg = Config(base=tmp_path)
    assert cfg.save()
    backend = MemoryVault()
    core = CoreApiConfiguration(cfg, backend=backend)
    service = ApiService("shared", "共享测试服务", "https://api.deepseek.com", model="text-model", vision_model="vision-model", balance_protocol="deepseek")
    core.save_service(
        service,
        secret="GENERATED-CENTRAL-KEY",
        grants=[("example.ai", "chat.send", "text-model"), ("example.screen", "manual_look", "vision-model"), ("core.balance", "balance.query", "")],
        expected_revision=core.revision(),
    )
    return cfg, core, backend


def test_generic_purpose_grants_share_key_without_enumeration(tmp_path):
    cfg, core, backend = provision(tmp_path)
    ai = core.bind("example.ai", authorized=lambda: True)
    vision = core.bind("example.screen", authorized=lambda: True)
    assert ai.resolve("chat.send").api_key == vision.resolve("manual_look").api_key
    assert ai.resolve("chat.send").model == "text-model"
    assert vision.resolve("manual_look").model == "vision-model"
    with pytest.raises(PermissionError, match="api_use_not_authorized"):
        ai.resolve("manual_look")
    with pytest.raises(PermissionError, match="execution_not_authorized"):
        core.bind("example.ai", authorized=lambda: False).resolve("chat.send")
    assert "GENERATED-CENTRAL-KEY" not in cfg.path.read_text("utf-8")
    assert "GENERATED-CENTRAL-KEY" not in repr(ai.resolve("chat.send"))
    assert not hasattr(ai, "configuration")
    assert not hasattr(ai, "vault")


def test_purpose_versions_do_not_change_for_unrelated_binding(tmp_path):
    cfg, core, backend = provision(tmp_path)
    api = core.bind("example.screen", authorized=lambda: True)
    before = api.effective_version("manual_look")
    snapshot = api.resolve("manual_look")
    core.revoke("example.ai", "chat.send", expected_revision=core.revision())
    assert api.effective_version("manual_look") == before
    assert snapshot.model == "vision-model"
    core.revoke("example.screen", "manual_look", expected_revision=core.revision())
    with pytest.raises(PermissionError):
        api.resolve("manual_look")


def test_cas_and_address_change_do_not_reuse_credentials(tmp_path):
    cfg, core, backend = provision(tmp_path)
    old = core.revision()
    profile = core.services()["shared"]
    core.save_service(replace(profile, base_url="https://other.invalid"), expected_revision=old)
    with pytest.raises(PermissionError):
        core.bind("example.ai", authorized=lambda: True).resolve("chat.send")
    with pytest.raises(ValueError, match="configuration_changed"):
        core.save_service(profile, secret="NEW-FAKE", expected_revision=old)
    assert len(backend.items) == 1


def test_failed_secure_save_preserves_atomic_config_and_draft(tmp_path):
    cfg, core, backend = provision(tmp_path)
    before = cfg.path.read_bytes()
    backend.fail = True
    with pytest.raises(Exception, match="credential_write_failed"):
        core.save_service(core.services()["shared"], secret="FAILING-FAKE", expected_revision=core.revision())
    assert cfg.path.read_bytes() == before
    journal = core.port.read_journal()
    assert "FAILING-FAKE" not in json.dumps(journal)


def test_ordinary_core_save_preserves_authoritative_api_namespace(tmp_path):
    cfg, core, backend = provision(tmp_path)
    stale = Config(base=tmp_path)
    core.revoke("example.ai", "chat.send", expected_revision=core.revision())
    stale.set("generated_unrelated", 4)
    assert stale.save()
    with pytest.raises(PermissionError):
        core.bind("example.ai", authorized=lambda: True).resolve("chat.send")


@pytest.mark.parametrize("url", ["https://api.example:bad", "https://api .example", "https://api.example/\npath", "https://api.example:99999"])
def test_endpoint_rejects_invalid_authority_before_key_write(tmp_path, url):
    cfg, core, backend = provision(tmp_path)
    before = cfg.path.read_bytes()
    with pytest.raises(ValueError, match="api_endpoint"):
        core.save_service(replace(core.services()["shared"], base_url=url), secret="GENERATED-UNSAVED", expected_revision=core.revision())
    assert cfg.path.read_bytes() == before
    assert len(backend.items) == 1


def test_request_fences_revocation_during_secret_resolution(tmp_path, monkeypatch):
    cfg, core, backend = provision(tmp_path)
    api = core.bind("example.ai", authorized=lambda: True)
    get = backend.get_password

    def revoke(service, ref):
        core.revoke("example.ai", "chat.send", expected_revision=core.revision())
        return get(service, ref)

    monkeypatch.setattr(backend, "get_password", revoke)
    with pytest.raises(PermissionError, match="api_configuration_changed"):
        api.resolve("chat.send")


def test_display_and_unrelated_model_changes_keep_manual_effective_version(tmp_path):
    cfg, core, backend = provision(tmp_path)
    api = core.bind("example.screen", authorized=lambda: True)
    before = api.effective_version("manual_look")
    core.save_service(replace(core.services()["shared"], name="新名称", model="new-text", balance_protocol="none"), expected_revision=core.revision())
    assert api.effective_version("manual_look") == before


def test_recovery_never_deletes_committed_or_legacy_references(tmp_path):
    cfg, core, backend = provision(tmp_path)
    live = core.services()["shared"].credential_ref
    dangling = core.vault.reserve()
    core.vault.save("draft", "https://api.example", "GENERATED-ORPHAN", ref=dangling)
    core.port.write_journal({"phase": "prepared", "created_refs": [live, dangling]})
    core.recover()
    assert len(backend.items) == 1
    assert core.bind("example.ai", authorized=lambda: True).resolve("chat.send").api_key == "GENERATED-CENTRAL-KEY"
    core.recover()
    assert len(backend.items) == 1


@pytest.mark.parametrize("field,value", [("name", 12), ("chat_path", None), ("model", []), ("vision_model", None), ("credential_ref", 10)])
def test_invalid_profile_types_fail_before_any_secret_write(tmp_path, field, value):
    cfg, core, backend = provision(tmp_path)
    before = cfg.path.read_bytes()
    with pytest.raises(ValueError, match="api_"):
        core.save_service(replace(core.services()["shared"], **{field: value}), secret="GENERATED-NEW", expected_revision=core.revision())
    assert cfg.path.read_bytes() == before
    assert len(backend.items) == 1


@pytest.mark.parametrize(
    "binding",
    [
        [],
        {"chat.send": []},
        {"chat.send": {"service_id": [], "model": "", "authorized_endpoint": "https://api.deepseek.com"}},
        {"chat.send": {"service_id": "shared", "model": [], "authorized_endpoint": "https://api.deepseek.com"}},
    ],
)
def test_corrupt_binding_fails_closed_before_secret_lookup(tmp_path, monkeypatch, binding):
    cfg, core, backend = provision(tmp_path)
    document = json.loads(cfg.path.read_text("utf-8"))
    document["plugins"]["core.api"]["bindings"]["example.ai"] = binding
    cfg.path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(backend, "get_password", lambda *_: pytest.fail("corrupt grant must not open the secret store"))
    port = core.bind("example.ai", authorized=lambda: True)
    assert port.effective_version("chat.send") == "unavailable"
    with pytest.raises(ValueError, match="api_configuration_invalid"):
        port.resolve("chat.send")


def test_authorization_identity_survives_edits_but_not_regrant(tmp_path):
    cfg, core, backend = provision(tmp_path)
    port = core.bind("example.ai", authorized=lambda: True)
    original = port.resolve("chat.send").metadata.authorization_version
    assert original
    service = core.services()["shared"]
    core.save_service(replace(service, model="new-model"), grants=[("example.ai", "chat.send", "new-model")], expected_revision=core.revision())
    assert port.resolve("chat.send").metadata.authorization_version == original
    core.revoke("example.ai", "chat.send", expected_revision=core.revision())
    core.save_service(service, grants=[("example.ai", "chat.send", "text-model")], expected_revision=core.revision())
    assert port.resolve("chat.send").metadata.authorization_version != original
