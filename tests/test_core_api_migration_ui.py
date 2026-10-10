"""Central UI/migration/balance public seams; generated credentials only."""

import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from pet.api_config import ApiService, CoreApiConfiguration
from pet.config import Config
from pet.credentials import CredentialVaultPort
from tests.screen_fakes import MemoryVault


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    cfg = Config(tmp_path / "config.json")
    cfg.save()
    return app, cfg, backend


def legacy_ai(cfg, backend):
    scope = cfg.runtime_layout.credential_namespace("official.ai-chat", str(cfg.instance_id or "primary")) if cfg.runtime_layout else str(cfg.path.resolve())
    vault = CredentialVaultPort("official.ai-chat", scope, backend=backend)
    ref = vault.save("old", "https://api.example", "GENERATED-OLD-KEY")
    cfg.data.setdefault("plugins", {})["official.ai-chat"] = {
        "chat": {
            "active_provider": "old",
            "providers": {"old": {"provider_id": "old", "name": "旧文字服务", "base_url": "https://api.example", "model": "old-text", "api_key_ref": ref}},
        }
    }
    # Independently owned namespace must use its CAS seam, not ordinary Core save.
    from pet.feature_config import bind_feature_configuration

    p = bind_feature_configuration(cfg, "official.ai-chat", journal_path=cfg.path.with_suffix(".ai.json"))
    p.commit_namespace(cfg.data["plugins"]["official.ai-chat"], expected_revision=p.revision())
    return ref


def test_migration_preview_no_keys_explicit_import_replay_and_old_key_retained(setup):
    from pet.api_migration import ApiMigration

    _, cfg, backend = setup
    old_ref = legacy_ai(cfg, backend)
    migration = ApiMigration(cfg, backend=backend)
    previews = migration.preview()
    selected = next(p for p in previews if p.source_id == "ai:old:text")
    assert "GENERATED-OLD-KEY" not in repr(previews)
    api = CoreApiConfiguration(cfg, backend=backend)
    assert api.services() == {}
    migration.confirm(selected, "imported", grants=[("official.ai-chat", "chat.send", "")], expected_revision=api.revision())
    request = api.bind("official.ai-chat", authorized=lambda: True).resolve("chat.send")
    assert request.api_key == "GENERATED-OLD-KEY"
    count = len(backend.items)
    migration.confirm(selected, "imported", grants=[("official.ai-chat", "chat.send", "")], expected_revision=api.revision())
    assert len(backend.items) == count
    assert any(old_ref == ref for _, ref in backend.items)
    for filename in cfg.path.parent.glob("*.json"):
        assert "GENERATED-OLD-KEY" not in filename.read_text(encoding="utf-8")


def test_migration_source_change_is_conflict_without_new_keys(setup):
    from pet.api_migration import ApiMigration

    _, cfg, backend = setup
    legacy_ai(cfg, backend)
    migration = ApiMigration(cfg, backend=backend)
    selected = next(p for p in migration.preview() if p.source_id == "ai:old:text")
    document = json.loads(cfg.path.read_text(encoding="utf-8"))
    document["plugins"]["official.ai-chat"]["chat"]["providers"]["old"]["model"] = "other"
    cfg.path.write_text(json.dumps(document), encoding="utf-8")
    count = len(backend.items)
    with pytest.raises(ValueError, match="migration_source_changed"):
        migration.confirm(selected, "imported", grants=[("official.ai-chat", "chat.send", "")], expected_revision=CoreApiConfiguration(cfg).revision())
    assert len(backend.items) == count
    assert CoreApiConfiguration(cfg).services() == {}


def test_api_widget_is_core_owned_and_keeps_unsubmitted_draft(setup):
    from pet.settings_api import ApiSettingsWidget

    _, cfg, backend = setup
    widget = ApiSettingsWidget(cfg, backend=backend)
    try:
        widget.name_edit.setText("尚未保存")
        widget.secret_edit.setText("GENERATED-DRAFT")
        widget.refresh_committed()
        assert widget.name_edit.text() == "尚未保存"
        assert widget.secret_edit.text() == "GENERATED-DRAFT"
        assert not CoreApiConfiguration(cfg).services()
    finally:
        widget.close()


def test_balance_consumes_shared_core_key_without_ai_dlc(setup, monkeypatch):
    from pet import feature_distribution
    from pet.balance_config import resolve_balance_request

    _, cfg, backend = setup
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    api = CoreApiConfiguration(cfg, backend=backend)
    api.save_service(
        ApiService("shared", "共享", "https://api.deepseek.com", model="text", balance_protocol="deepseek"),
        secret="GENERATED-SHARED",
        grants=[("core.balance", "balance.query", "")],
        expected_revision=api.revision(),
    )
    request = resolve_balance_request(cfg, backend=backend)
    assert request.api_key == "GENERATED-SHARED" and request.id == "shared"


def test_balance_unsupported_protocol_is_not_missing_key(setup, monkeypatch):
    from pet import feature_distribution
    from pet.balance_config import resolve_balance_request

    _, cfg, backend = setup
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    api = CoreApiConfiguration(cfg, backend=backend)
    api.save_service(
        ApiService("other", "其他服务", "https://api.example"),
        secret="GENERATED",
        grants=[("core.balance", "balance.query", "")],
        expected_revision=api.revision(),
    )
    with pytest.raises(ValueError, match="balance_protocol_unsupported"):
        resolve_balance_request(cfg, backend=backend)


def test_async_settings_job_freezes_draft_until_completion(setup):
    import threading

    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    widget = ApiSettingsWidget(cfg, backend=backend)
    started, release = threading.Event(), threading.Event()

    def action(cfg):
        started.set()
        assert release.wait(10)
        return "api_saved"

    try:
        widget._run(action, publish=True)
        assert started.wait(5)
        assert not widget.name_edit.isEnabled()
        assert not widget.secret_edit.isEnabled()
        assert not widget.services_select.isEnabled()
    finally:
        release.set()
        widget.pending_job.thread.join(10)
        app.processEvents()
        widget.close()
        widget.deleteLater()


def test_minimal_probe_is_explicit_and_does_not_publish(setup, monkeypatch):
    from pet.api_probe import ProbeResult, probe_text

    app, cfg, backend = setup
    calls = []

    def post(request, **kw):
        calls.append((request, kw))
        from unittest.mock import MagicMock

        response = MagicMock()
        response.__enter__.return_value.status = 200
        return response

    monkeypatch.setattr("urllib.request.urlopen", post)
    before = cfg.path.read_bytes()
    api = CoreApiConfiguration(cfg, backend=backend)
    service = ApiService("probe", "仅测试", "https://api.example", model="test-text")
    assert probe_text(cfg, service, "GENERATED-PROBE", backend=backend) == ProbeResult("probe_text_ok", 200)
    assert cfg.path.read_bytes() == before
    assert not api.services()
    assert len(calls) == 1
    payload = json.loads(calls[0][0].data)
    assert payload["max_tokens"] == 1
    assert "image" not in str(payload)
    assert calls[0][1]["timeout"] <= 15
    assert not backend.items


def test_edited_endpoint_cannot_silently_reuse_old_secret(setup):
    from dataclasses import replace

    from pet.credentials import CredentialError
    from pet.simple_api import SimpleApiConfiguration, SimpleApiProfile

    _, cfg, backend = setup
    api = CoreApiConfiguration(cfg, backend=backend)
    original = ApiService("old", "原服务", "https://original.example", model="text")
    api.save_service(original, secret="GENERATED-OLD", grants=[("official.ai-chat", "chat.send", "")], expected_revision=api.revision())
    before = dict(backend.items)
    simple = SimpleApiConfiguration(cfg, backend=backend)
    with pytest.raises(CredentialError, match="credential_missing"):
        simple.save([SimpleApiProfile(replace(original, base_url="https://other.example"))], "old", expected_revision=simple.revision())
    assert backend.items == before
    assert api.services()["old"].base_url == original.base_url


def test_core_api_entry_is_available_without_any_dlc(setup, monkeypatch):
    from pet import feature_distribution
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app, cfg, backend = setup
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        assert dialog.api_settings_widget is not None
        assert dialog.findChild(SettingRow, "settingRow_api_profile") is not None
        assert dialog.findChild(SettingRow, "settingRow_balance_credentials") is not None
    finally:
        for manager in dialog.feature_managers.values():
            manager.close()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_api_job_start_failure_is_safe_and_keeps_editable_draft(setup, monkeypatch):
    import threading

    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    widget = ApiSettingsWidget(cfg, backend=backend)
    widget.name_edit.setText("未保存草稿")
    widget.secret_edit.setText("GENERATED-DRAFT")

    def unavailable(_):
        raise RuntimeError("GENERATED-SENSITIVE-START-ERROR")

    monkeypatch.setattr(threading.Thread, "start", unavailable)
    try:
        widget._run(lambda _: "api_saved", publish=True)
        assert not widget.busy
        assert widget.secret_edit.isEnabled()
        assert widget.secret_edit.text() == "GENERATED-DRAFT"
        assert "GENERATED-SENSITIVE-START-ERROR" not in widget.status.text()
        assert "未" in widget.status.text()
        assert not CoreApiConfiguration(cfg).services()
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def test_api_widget_opens_safely_with_corrupt_config_and_does_not_overwrite(setup):
    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    document = json.loads(cfg.path.read_text("utf-8"))
    document.setdefault("plugins", {})["core.api"] = {"schema_version": 1, "services": {}, "bindings": {"example.ai": []}}
    cfg.path.write_text(json.dumps(document), encoding="utf-8")
    before = cfg.path.read_bytes()
    widget = ApiSettingsWidget(cfg, backend=backend)
    try:
        assert "无法读取" in widget.status.text()
        assert not widget.save_button.isEnabled()
        assert cfg.path.read_bytes() == before
        assert not backend.items
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def test_api_widget_reload_after_repair_restores_editing(setup):
    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    original = cfg.path.read_bytes()
    document = json.loads(original)
    document.setdefault("plugins", {})["core.api"] = {"schema_version": 1, "services": {}, "bindings": {"example.ai": []}}
    cfg.path.write_text(json.dumps(document), encoding="utf-8")
    widget = ApiSettingsWidget(cfg, backend=backend)
    try:
        assert not widget.save_button.isEnabled()
        cfg.path.write_bytes(original)
        widget.reload_button.click()
        assert widget.save_button.isEnabled()
        assert widget.secret_edit.isEnabled()
        assert not widget.dirty
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()
