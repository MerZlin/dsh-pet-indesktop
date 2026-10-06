"""Generated data and memory keyring only; real source/secret access requires consent."""

from __future__ import annotations

import json

import pytest

from pet.credentials import CredentialVaultPort
from pet.official_features import AI_OWNER, SCREEN_OWNER
from tests.test_feature_config_ports import MemoryVault
from tests.test_runtime_data_import import fixture_import


def authorized_fixture(tmp_path, monkeypatch):
    from pet.runtime_credential_import import RuntimeCredentialImporter
    from pet.runtime_data_import import RuntimeDataImporter

    layout, source, _ = fixture_import(tmp_path, monkeypatch)
    backend = MemoryVault()
    cfg = source / "config-instance-A.json"
    chat = {
        "active_provider": "generated-provider",
        "providers": {
            "generated-provider": {
                "base_url": "https://generated.invalid",
                "model": "generated-model",
                "api_key_ref": "provider/generated-provider",
                "vision_api_key_ref": "",
            }
        },
    }
    screen = CredentialVaultPort(SCREEN_OWNER, str(cfg.resolve()), backend=backend)
    screen_ref = screen.save("manual", "https://generated.invalid/v1/chat/completions", "GENERATED-SCREEN-SECRET")
    cfg.write_text(
        json.dumps(
            {
                "instance_id": "instance-A",
                "character": "dsh",
                "chat": chat,
                "plugins": {
                    SCREEN_OWNER: {
                        "schema_version": 1,
                        "settings": {
                            "schema_version": 1,
                            "profiles": {
                                "manual": {
                                    "profile_id": "manual",
                                    "base_url": "https://generated.invalid",
                                    "model": "generated-model",
                                    "chat_path": "/v1/chat/completions",
                                    "credential_ref": screen_ref,
                                }
                            },
                            "bindings": {"manual": "manual"},
                            "migration_state": "configured",
                        },
                    }
                },
            }
        )
    )
    backend.set_password("dsh-pet-standalone", "provider/generated-provider", "GENERATED-AI-SECRET")
    adapter = RuntimeCredentialImporter(layout, backend=backend)
    importer = RuntimeDataImporter(layout, credentials=adapter)
    return layout, source, backend, importer


def test_preflight_does_not_read_keyring_and_confirmation_binds_scopes(tmp_path, monkeypatch):
    layout, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    reads = []
    monkeypatch.setattr(backend, "get_password", lambda *args: reads.append(args) or pytest.fail("preflight must not read secret"))
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation", preview
    mappings = preview.plan.credential_mappings
    assert {m.owner for m in mappings} == {AI_OWNER, SCREEN_OWNER}
    assert all(m.source_file == "config-instance-A.json" and m.instance == "instance-A" for m in mappings)
    assert importer.apply(preview.plan, confirmation_token="unconfirmed").status == "rejected"
    assert not reads
    assert all(
        "GENERATED-AI-SECRET" not in p.read_text(encoding="utf-8") and "GENERATED-SCREEN-SECRET" not in p.read_text(encoding="utf-8")
        for p in layout.data_root.rglob("*.json")
    )


def test_authorized_import_preserves_provider_and_transfers_only_bound_references(tmp_path, monkeypatch):
    from features.ai_chat.host.config import AiConfiguration
    from pet import credentials
    from pet.config import Config
    from pet.feature_host_bindings import bind_ai_context

    layout, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    original = (source / "config-instance-A.json").read_bytes()
    preview = importer.preflight(source)
    result = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert result.status == "completed", result
    monkeypatch.setattr(credentials, "secure_backend", lambda: backend)
    cfg = Config(layout=layout, instance_id="instance-A")
    ai = AiConfiguration(bind_ai_context(cfg))
    provider = ai.chat_settings().active_config
    assert provider.provider_id == "generated-provider" and provider.model == "generated-model"
    assert ai.context.credentials.acquire(provider.api_key_ref, provider.provider_id, provider.base_url, "chat.send") == "GENERATED-AI-SECRET"
    screen = CredentialVaultPort(SCREEN_OWNER, layout.credential_namespace(SCREEN_OWNER, "instance-A"), backend=backend)
    doc = json.loads(cfg.path.read_bytes())
    profile = doc["plugins"][SCREEN_OWNER]["settings"]["profiles"]["manual"]
    assert screen.acquire(profile["credential_ref"], "manual", "https://generated.invalid/v1/chat/completions", "manual_look") == "GENERATED-SCREEN-SECRET"
    assert (source / "config-instance-A.json").read_bytes() == original
    assert importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token).status == "idempotent"
    assert all(
        "GENERATED-AI-SECRET" not in p.read_text(encoding="utf-8") and "GENERATED-SCREEN-SECRET" not in p.read_text(encoding="utf-8")
        for p in layout.data_root.rglob("*.json")
    )


@pytest.mark.parametrize("step", ["before_credential", "after_credential", "after_file"])
def test_credential_migration_recovers_without_plaintext_journal(tmp_path, monkeypatch, step):
    layout, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    fired = False

    def crash(point):
        nonlocal fired
        if point == step and not fired:
            fired = True
            raise OSError("generated interruption")

    monkeypatch.setattr(importer, "_checkpoint", crash)
    interrupted = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert interrupted.status == "recovery_required" and fired
    recovered = importer.recover_pending()
    assert recovered.status == "completed", recovered
    assert importer.recover_pending().status == "idempotent"
    assert all("GENERATED-AI-SECRET" not in p.read_text(encoding="utf-8") for p in layout.data_root.rglob("*.json"))


def test_changed_source_is_rejected_before_any_credential_read(tmp_path, monkeypatch):
    _, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    (source / "config-instance-A.json").write_text('{"instance_id":"changed"}')
    monkeypatch.setattr(backend, "get_password", lambda *args: pytest.fail("changed source must not read credential"))
    result = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert result.status == "rejected" and result.reason == "source_changed"


def test_plaintext_or_unregistered_reference_is_not_snapshotted(tmp_path, monkeypatch):
    layout, source, _, importer = authorized_fixture(tmp_path, monkeypatch)
    cfg = source / "config-instance-A.json"
    cfg.write_text('{"chat":{"providers":{"generated":{"api_key":"GENERATED-PLAINTEXT"}}}}')
    result = importer.preflight(source)
    assert result.status == "rejected" and result.reason == "credential_migration_required"
    assert not importer.root.exists()
    assert not (layout.data_root / "config-instance-A.json").exists()


def test_source_root_identity_change_is_rejected_before_acceptance(tmp_path, monkeypatch):
    import uuid

    from pet.runtime_credential_import import RuntimeCredentialImporter
    from pet.runtime_data_import import RuntimeDataImporter
    from pet.runtime_layout import PRODUCT_ID, RuntimeLayout

    layout, source, backend, _ = authorized_fixture(tmp_path, monkeypatch)
    root_id = uuid.uuid4().hex
    identity = source / "data-root.json"
    identity.write_text(json.dumps({"format_version": 1, "product_id": PRODUCT_ID, "data_root_id": root_id}))
    cfg = source / "config-instance-A.json"
    doc = json.loads(cfg.read_bytes())
    old = RuntimeLayout(layout.executable, source, "installed", root_id)
    vault = CredentialVaultPort(SCREEN_OWNER, old.credential_namespace(SCREEN_OWNER, "instance-A"), backend=backend)
    doc["plugins"][SCREEN_OWNER]["settings"]["profiles"]["manual"]["credential_ref"] = vault.save(
        "manual", "https://generated.invalid/v1/chat/completions", "GENERATED-SCREEN-SECRET"
    )
    cfg.write_text(json.dumps(doc))
    importer = RuntimeDataImporter(layout, credentials=RuntimeCredentialImporter(layout, backend=backend))
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation", preview
    identity.write_text(json.dumps({"format_version": 1, "product_id": PRODUCT_ID, "data_root_id": uuid.uuid4().hex}))
    monkeypatch.setattr(backend, "get_password", lambda *args: pytest.fail("identity conflict must not read secrets"))
    result = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert result.status == "rejected" and result.reason == "source_identity_changed"
    assert not importer.pending.exists()


def test_backend_error_does_not_escape_or_disclose_secret(tmp_path, monkeypatch):
    _, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    preview = importer.preflight(source)

    def broken(*args):
        raise RuntimeError("GENERATED-SENSITIVE-BACKEND-DETAIL")

    monkeypatch.setattr(backend, "get_password", broken)
    result = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert result.status == "recovery_required" and result.reason == "credential_transfer_failed"
    assert "GENERATED-SENSITIVE-BACKEND-DETAIL" not in repr(result)


def test_changed_destination_vault_is_not_overwritten(tmp_path, monkeypatch):
    _, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    row = next(row for row in preview.plan.credential_mappings if row.owner == AI_OWNER)
    vault = importer.credentials._target_vault(row.owner, row.instance)
    vault.save(row.profile, row.endpoint, "GENERATED-NEW-EDIT", ref=row.target_ref)
    result = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert result.status == "recovery_required" and result.reason == "credential_target_changed"
    assert vault.acquire(row.target_ref, row.profile, row.endpoint, "chat.send") == "GENERATED-NEW-EDIT"


def test_balance_reference_is_independent_of_ai_import(tmp_path, monkeypatch):
    import uuid

    from pet import credentials
    from pet.balance_config import BalanceConfiguration
    from pet.config import Config
    from pet.credentials import CoreBalanceVault
    from pet.runtime_layout import PRODUCT_ID

    layout, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    root_id = uuid.uuid4().hex
    (source / "data-root.json").write_text(json.dumps({"format_version": 1, "product_id": PRODUCT_ID, "data_root_id": root_id}))
    cfg = source / "config-instance-A.json"
    doc = json.loads(cfg.read_bytes())
    # Keep the fixture's legacy chat only; screen scope was created against an
    # older path-layout and cannot masquerade as this modern root's scope.
    doc["plugins"].pop(SCREEN_OWNER)
    balance = CoreBalanceVault(root_id, "instance-A", backend=backend)
    ref = balance.save("balance", "https://balance.invalid", "GENERATED-BALANCE-SECRET")
    doc["plugins"]["core.balance"] = {"format_version": 1, "endpoint": "https://balance.invalid", "credential_ref": ref}
    cfg.write_text(json.dumps(doc))
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation", preview
    assert {m.owner for m in preview.plan.credential_mappings} == {AI_OWNER, "core.balance"}
    result = importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)
    assert result.status == "completed", result
    monkeypatch.setattr(credentials, "secure_backend", lambda: backend)
    request = BalanceConfiguration(Config(layout=layout, instance_id="instance-A")).request()
    assert request.api_key == "GENERATED-BALANCE-SECRET"
