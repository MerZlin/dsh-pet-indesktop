"""Host-owned I/O ports preserve data while withholding the global configuration."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from pet.config import Config
from pet.credentials import CredentialVaultPort
from tests.screen_fakes import MemoryVault

OWNER = "official.screen-understanding"


@pytest.fixture
def bound(tmp_path):
    from pet.feature_config import bind_feature_configuration

    cfg = Config(tmp_path)
    cfg.dir, cfg.path = tmp_path, tmp_path / "config.json"
    cfg.data["chat"] = {"unrelated": "must-not-leak"}
    cfg.data["private_core"] = "must-not-leak"
    cfg.data["legacy_source"] = {"model": "first"}
    assert cfg.save()
    port = bind_feature_configuration(
        cfg,
        OWNER,
        journal_path=cfg.path.with_suffix(".json.vision-migration.json"),
        migration_source=lambda doc: {"model": doc["legacy_source"]["model"]},
    )
    return cfg, port


def test_port_exposes_only_owned_copies(bound):
    cfg, port = bound
    assert not hasattr(port, "cfg") and not hasattr(port, "data") and not hasattr(port, "path")
    assert port.read_namespace() == {}
    assert port.migration_source() == {"model": "first"}
    snapshot = port.migration_source()
    snapshot["model"] = "tampered"
    assert port.migration_source() == {"model": "first"}
    port.commit_namespace({"settings": {"value": 1}}, expected_revision=port.revision())
    snapshot = port.read_namespace()
    snapshot["settings"]["value"] = 999
    assert port.read_namespace()["settings"]["value"] == 1
    assert cfg.data["private_core"] == "must-not-leak"


def test_commit_reloads_other_fields_and_rejects_stale_writes(bound):
    cfg, port = bound
    revision = port.revision()
    latest = json.loads(cfg.path.read_text(encoding="utf-8"))
    latest["parallel_edit"] = "preserved"
    cfg.path.write_text(json.dumps(latest), encoding="utf-8")
    port.commit_namespace({"settings": {"value": 1}}, expected_revision=revision)
    assert json.loads(cfg.path.read_text(encoding="utf-8"))["parallel_edit"] == "preserved"
    with pytest.raises(ValueError, match="configuration_changed"):
        port.commit_namespace({"settings": {"value": 2}}, expected_revision=revision)
    assert port.read_namespace()["settings"]["value"] == 1


def test_migration_guard_sees_only_authorized_snapshot_and_rechecks_under_lock(bound):
    cfg, port = bound
    seen = []

    def guard(source):
        seen.append(source)
        if source["model"] != "first":
            raise ValueError("source_changed")

    revision = port.revision()
    latest = json.loads(cfg.path.read_text(encoding="utf-8"))
    latest["legacy_source"]["model"] = "second"
    cfg.path.write_text(json.dumps(latest), encoding="utf-8")
    with pytest.raises(ValueError, match="source_changed"):
        port.commit_namespace({"settings": {}}, expected_revision=revision, source_guard=guard)
    assert seen == [{"model": "second"}]
    assert port.read_namespace() == {}


def test_operation_lock_is_nonblocking_and_journal_path_is_host_owned(bound):
    cfg, port = bound
    assert port.read_journal() is None
    with port.operation():
        with pytest.raises(OSError, match="configuration_busy"):
            with port.operation():
                pass
        port.write_journal({"phase": "prepared", "created_refs": []})
    assert port.read_journal() == {"phase": "prepared", "created_refs": []}
    assert cfg.path.with_suffix(".json.vision-migration.json").exists()


def test_feature_service_accepts_ports_not_core_config(bound):
    from features.screen_understanding.common.models import VisionProfile
    from features.screen_understanding.host.config import VisionConfigService

    cfg, port = bound
    backend = MemoryVault()
    vault = CredentialVaultPort(OWNER, str(cfg.path.resolve()), backend=backend)
    service = VisionConfigService(port, vault=vault)
    assert not hasattr(service, "cfg") and not hasattr(service, "path")
    service.save_profile(
        VisionProfile("manual", "https://vision.invalid", "model"), modes=["manual"], expected_revision=service.revision(), secret="TEST-SECRET"
    )
    assert service.resolve("manual").request.api_key == "TEST-SECRET"
    assert service.resolve("automatic").reason == "not_configured"
    assert "TEST-SECRET" not in cfg.path.read_text(encoding="utf-8")
    assert "TEST-SECRET" not in json.dumps(port.read_journal())


def test_feature_keyring_never_holds_config_write_lock(bound, monkeypatch):
    from features.screen_understanding.common.models import VisionProfile
    from features.screen_understanding.host.config import VisionConfigService
    from pet import feature_config

    cfg, port = bound
    original = feature_config.file_transaction
    inside = []

    @contextmanager
    def tracked(path):
        with original(path):
            inside.append(path)
            try:
                yield
            finally:
                inside.remove(path)

    monkeypatch.setattr(feature_config, "file_transaction", tracked)
    backend = MemoryVault()
    save = backend.set_password

    def checked(*args):
        assert cfg.path.with_suffix(".json.write.lock") not in inside
        return save(*args)

    backend.set_password = checked
    service = VisionConfigService(port, vault=CredentialVaultPort(OWNER, str(cfg.path.resolve()), backend=backend))
    service.save_profile(VisionProfile("p", "https://vision.invalid", "model"), modes=["manual"], expected_revision=service.revision(), secret="TEST-SECRET")
    assert service.resolve("manual").ready
