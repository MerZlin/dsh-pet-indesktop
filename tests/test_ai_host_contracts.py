"""Public owner-bound AI factory/config/lifecycle seams with generated data."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.credentials import CredentialError, CredentialVaultPort
from pet.feature_host_bindings import bind_ai_context
from pet.plugins.feature_host import FeatureHost


class MemorySecrets:
    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[service, username] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


def binding(tmp_path, *, enabled=True):
    from features.ai_chat.host.factory import create_host

    cfg = Config(base=tmp_path)
    cfg.set("unrelated_canary", {"keep": 1})
    assert cfg.save()
    host = FeatureHost()
    host.provide(create_host(), enabled=enabled)
    vault = CredentialVaultPort("official.ai-chat", "generated-data-root", backend=MemorySecrets())
    ctx = host.bind_context(replace(bind_ai_context(cfg), credentials=vault, api=None))
    return cfg, host, ctx


def test_ai_settings_namespace_cas_and_no_plaintext_secret(tmp_path):
    from features.ai_chat.host.config import AiConfiguration

    cfg, host, ctx = binding(tmp_path)
    ai = AiConfiguration(ctx)
    settings = ai.chat_settings()
    provider = settings.active_config
    provider.base_url = "http://127.0.0.1:34999"
    provider.api_key_ref = ai.save_provider_secret(provider, "generated-only-secret")
    ai.set_chat_settings(settings)
    ai.set("modern_chat_card_opacity", 76)
    assert ai.save()
    raw = json.loads(cfg.path.read_text("utf-8"))
    assert raw["unrelated_canary"] == {"keep": 1}
    assert "generated-only-secret" not in cfg.path.read_text("utf-8")
    assert raw["plugins"][ctx.owner]["ui"]["modern_chat_card_opacity"] == 76
    assert ai.resolve_provider_secret(provider) == "generated-only-secret"
    host.disable(ctx.owner)
    with pytest.raises(CredentialError, match="execution_not_authorized"):
        ai.resolve_provider_secret(provider)
    with pytest.raises(PermissionError, match="preference_not_granted"):
        ai.set("autostart", True)
    old = AiConfiguration(ctx)
    ai.set("chat_ui_style", "classic")
    assert ai.save()
    old.set("chat_ui_style", "modern")
    with pytest.raises(ValueError, match="configuration_changed"):
        old.save()


def test_ai_configuration_rejects_raw_secret_and_other_owner(tmp_path):
    from features.ai_chat.host.config import AiConfiguration

    cfg, host, ctx = binding(tmp_path)
    ai = AiConfiguration(ctx)
    settings = ai.chat_settings()
    settings.active_config.api_key = "never-persist-this"
    with pytest.raises(CredentialError, match="plaintext_secret_not_allowed"):
        ai.set_chat_settings(settings)
    assert "never-persist-this" not in cfg.path.read_text("utf-8")
    with pytest.raises(TypeError, match="owner-bound"):
        AiConfiguration(replace(ctx, owner="official.screen-understanding"))


def wait(app, predicate, budget=15):
    end = time.monotonic() + budget
    while not predicate() and time.monotonic() < end:
        app.processEvents()
    assert predicate()


def test_real_ai_runtime_lifecycle_fences_session_and_requests(tmp_path, monkeypatch):
    from features.ai_chat.host.chat import session_store as ss
    from features.ai_chat.host.chat.session_store import SessionStore, close_all_writers
    from features.ai_chat.host.runtime import AiRuntime

    monkeypatch.setattr(ss, "_registry", ss._WriterRegistry())
    app = QApplication.instance() or QApplication([])
    cfg, host, ctx = binding(tmp_path)
    runtime = host.runtime(ctx)
    assert isinstance(runtime, AiRuntime)
    assert runtime.accepting
    store = SessionStore(runtime.config.dir, runtime.config.instance_id)
    session = store.create("generated", "p1", "sys")
    assert store.save(session)
    host.disable(ctx.owner)
    assert not runtime.accepting
    assert not store.save(session)
    wait(app, lambda: runtime.drain_status != "awaiting_release")
    assert runtime.drain_status == "completed"
    host.enable(ctx.owner)
    assert runtime.accepting
    assert store.save(session)
    runtime.close()
    wait(app, lambda: runtime.drain_status != "awaiting_release")
    assert runtime.drain_status == "completed"
    assert not runtime.resume()
    close_all_writers()


def test_signed_ai_settings_factory_uses_scoped_vault_and_retains_dirty_draft(tmp_path, monkeypatch):
    from features.ai_chat.host.chat.models import SecretStore
    from features.ai_chat.host.factory import create_settings

    app = QApplication.instance() or QApplication([])
    cfg, host, ctx = binding(tmp_path)

    def deny_legacy(*args):
        raise AssertionError("signed AI must not access legacy unscoped keyring")

    monkeypatch.setattr(SecretStore, "set", deny_legacy)
    monkeypatch.setattr(SecretStore, "get", deny_legacy)
    component = create_settings(ctx)
    assert not component.dirty()
    component.page.key.setText("generated-page-secret")
    assert component.dirty()
    assert "generated-page-secret" not in json.dumps(component.draft())
    assert component.confirm_save()
    assert not component.dirty()
    assert "generated-page-secret" not in cfg.path.read_text("utf-8")
    assert component.page.provisional_config().api_key == "generated-page-secret"
    component.dispose()
    app.processEvents()


def test_ai_settings_discard_preserves_reparented_rows_and_file_draft(tmp_path):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from features.ai_chat.host.factory import create_settings

    app = QApplication.instance() or QApplication([])
    cfg, host, ctx = binding(tmp_path)
    component = create_settings(ctx)
    container = QWidget()
    layout = QVBoxLayout(container)
    original_page = component.page
    original_rows = tuple(component.rows)
    for row in original_rows:
        layout.addWidget(row)
    original_prompt = component.page.prompt.toPlainText()
    original_enabled = component.file_controls.file_interpret_enabled_check.isChecked()
    component.page.prompt.setPlainText("generated discard draft")
    component.page._add_provider()
    component.file_controls.file_interpret_enabled_check.setChecked(not original_enabled)
    assert component.dirty()
    assert component.discard_changes()
    app.processEvents()
    assert component.page is original_page
    assert tuple(component.rows) == original_rows
    assert all(row.parentWidget() is container for row in original_rows)
    assert component.page.prompt.toPlainText() == original_prompt
    assert component.file_controls.file_interpret_enabled_check.isChecked() == original_enabled
    assert not component.dirty()
    component.dispose()
    container.deleteLater()
    app.processEvents()


def test_ai_settings_save_is_one_cas_for_chat_and_file(tmp_path, monkeypatch):
    from features.ai_chat.host.factory import create_settings

    app = QApplication.instance() or QApplication([])
    cfg, host, ctx = binding(tmp_path)
    component = create_settings(ctx)
    calls = []
    original = component.config.save

    def save():
        calls.append(1)
        return original()

    monkeypatch.setattr(component.config, "save", save)
    component.page.prompt.setPlainText("generated combined save")
    component.file_controls.file_interpret_enabled_check.setChecked(False)
    assert component.confirm_save()
    assert len(calls) == 1
    component.dispose()
    app.processEvents()


def test_disabled_ai_cannot_start_connection_test_with_fresh_key(tmp_path):
    from features.ai_chat.host.factory import create_settings

    app = QApplication.instance() or QApplication([])
    cfg, host, ctx = binding(tmp_path, enabled=False)
    component = create_settings(ctx)
    component.page.key.setText("generated denied secret")
    with pytest.raises(CredentialError, match="execution_not_authorized"):
        component.page.provisional_config()
    component.page._run_test()
    assert component.page._test_thread is None
    assert component.page.key.text() == "generated denied secret"
    component.dispose()
    app.processEvents()


def test_ai_reenable_during_writer_drain_resumes_without_second_toggle(tmp_path, monkeypatch):
    import threading

    from features.ai_chat.host.chat import session_store as ss

    entered, release = threading.Event(), threading.Event()

    def blocked_write(path, payload):
        entered.set()
        assert release.wait(15)
        ss._atomic_write(path, payload)

    registry = ss._WriterRegistry(writer_factory=lambda root: ss._AsyncWriter(root, write=blocked_write))
    monkeypatch.setattr(ss, "_registry", registry)
    app = QApplication.instance() or QApplication([])
    cfg, host, ctx = binding(tmp_path)
    runtime = host.runtime(ctx)
    store = ss.SessionStore(runtime.config.dir, runtime.config.instance_id)
    session = store.create("generated", "p1", "sys")
    assert store.save(session)
    assert entered.wait(10)
    try:
        host.disable(ctx.owner)
        host.enable(ctx.owner)
        assert not runtime.accepting
        release.set()
        wait(app, lambda: runtime.accepting)
        assert store.save(session)
    finally:
        release.set()
        runtime.close()
        wait(app, lambda: runtime.drain_status != "awaiting_release")
        ss.close_all_writers()


def test_shared_ai_session_drain_can_be_observed_and_resumed_by_two_hosts(tmp_path, monkeypatch):
    from features.ai_chat.host.chat import session_store as ss

    monkeypatch.setattr(ss, "_registry", ss._WriterRegistry())
    app = QApplication.instance() or QApplication([])
    cfg1, host1, ctx1 = binding(tmp_path / "one")
    cfg2, host2, ctx2 = binding(tmp_path / "two")
    runtimes = [host1.runtime(ctx1), host2.runtime(ctx2)]
    try:
        host1.disable(ctx1.owner)
        host2.disable(ctx2.owner)
        wait(app, lambda: all(r.drain_status == "completed" for r in runtimes))
        host1.enable(ctx1.owner)
        host2.enable(ctx2.owner)
        wait(app, lambda: all(r.accepting for r in runtimes))
    finally:
        for runtime in runtimes:
            runtime.close()
        wait(app, lambda: all(r.drain_status != "awaiting_release" for r in runtimes))
        ss.close_all_writers()
