"""Core-owned balance survives absent AI; generated secrets only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pet.config import Config
from pet.credentials import CredentialError, CredentialVaultPort


class MemorySecrets:
    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


def config(tmp_path, monkeypatch):
    from pet import feature_distribution

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    cfg = Config(base=tmp_path)
    cfg.runtime_layout = SimpleNamespace(data_root_id="generated-stable-root-id")
    assert cfg.save()
    return cfg


def test_balance_has_independent_secret_scope_and_never_adopts_chat_key(tmp_path, monkeypatch):
    from pet.balance_config import BalanceConfiguration, resolve_balance_request

    cfg = config(tmp_path, monkeypatch)
    cfg.data["chat"] = {"providers": {"p": {"api_key": "unapproved-generated-legacy"}}}
    backend = MemorySecrets()
    balance = BalanceConfiguration(cfg, backend=backend)
    assert balance.request().api_key == ""
    balance.save("https://api.deepseek.com", "generated-balance-only", expected_revision=balance.revision)
    # Old storage remains readable only by its owner; requests require the
    # user-confirmed Core API migration, not an implicit credential fallback.
    with pytest.raises(PermissionError, match="api_use_not_authorized"):
        resolve_balance_request(cfg, backend=backend)
    from pet.api_config import CoreApiConfiguration
    from pet.api_migration import ApiMigration

    migration = ApiMigration(cfg, backend=backend)
    preview = next(p for p in migration.preview() if p.source_id == "balance")
    api = CoreApiConfiguration(cfg, backend=backend)
    migration.confirm(preview, "balance-imported", grants=preview.purposes, expected_revision=api.revision())
    request = resolve_balance_request(cfg, backend=backend)
    assert request.api_key == "generated-balance-only" and request.verify_ssl is True
    assert "generated-balance-only" not in cfg.path.read_text(encoding="utf-8")
    ai = CredentialVaultPort("official.ai-chat", "generated-stable-root-id", backend=backend)
    with pytest.raises(CredentialError, match="scope_denied"):
        ai.acquire(balance.value["credential_ref"], "balance", request.base_url, "chat.send")


def test_balance_move_preserves_root_identity_not_old_absolute_path(tmp_path, monkeypatch):
    from pet.balance_config import BalanceConfiguration

    cfg = config(tmp_path / "first", monkeypatch)
    backend = MemorySecrets()
    first = BalanceConfiguration(cfg, backend=backend)
    first.save("https://api.deepseek.com", "generated-only", expected_revision=first.revision)
    cfg.dir.rename(tmp_path / "moved")
    cfg.dir = tmp_path / "moved"
    cfg.path = cfg.dir / "config.json"
    assert BalanceConfiguration(cfg, backend=backend).request().api_key == "generated-only"
    cfg.runtime_layout = SimpleNamespace(data_root_id="different-generated-root")
    with pytest.raises(CredentialError, match="scope_denied"):
        BalanceConfiguration(cfg, backend=backend).request()


def test_balance_cas_preserves_other_owner_and_no_orphan_secret_on_conflict(tmp_path, monkeypatch):
    from pet.balance_config import BalanceConfiguration

    cfg = config(tmp_path, monkeypatch)
    backend = MemorySecrets()
    first, stale = BalanceConfiguration(cfg, backend=backend), BalanceConfiguration(cfg, backend=backend)
    first.save("https://api.deepseek.com", "generated-first", expected_revision=first.revision)
    before = dict(backend.items)
    with pytest.raises(ValueError, match="configuration_changed"):
        stale.save("https://api.deepseek.com", "generated-stale", expected_revision=stale.revision)
    assert backend.items == before
    assert BalanceConfiguration(cfg, backend=backend).request().api_key == "generated-first"


@pytest.mark.parametrize(
    "endpoint", ["file:///C:/private", "https://user:password@example.com", "https://example.com/?token=x", "http://example.com", "https://example.com/#secret"]
)
def test_balance_rejects_unsafe_endpoint_before_credential_write(tmp_path, monkeypatch, endpoint):
    from pet.balance_config import BalanceConfiguration

    cfg = config(tmp_path, monkeypatch)
    backend = MemorySecrets()
    balance = BalanceConfiguration(cfg, backend=backend)
    with pytest.raises(ValueError, match="balance_endpoint_invalid"):
        balance.save(endpoint, "generated", expected_revision=balance.revision)
    assert not backend.items


def test_balance_without_layout_cannot_guess_secret_scope(tmp_path, monkeypatch):
    from pet.balance_config import BalanceConfiguration

    cfg = config(tmp_path, monkeypatch)
    cfg.runtime_layout = None
    with pytest.raises(ValueError, match="balance_layout_required"):
        BalanceConfiguration(cfg)
