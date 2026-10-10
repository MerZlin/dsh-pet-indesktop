"""All real chat windows consume committed Core API, with fake network boundary."""

import time

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from tests.screen_fakes import MemoryVault


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def new_runtime(tmp_path, monkeypatch):
    from features.ai_chat.host.factory import create_host
    from pet.api_config import CoreApiConfiguration
    from pet.config import Config
    from pet.feature_host_bindings import bind_ai_context
    from pet.plugins.feature_host import FeatureHost

    monkeypatch.setattr("pet.feature_distribution.BUILTIN_AI", False)
    store = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: store)
    cfg = Config(base=tmp_path)
    assert cfg.save()
    host = FeatureHost()
    host.provide(create_host(), enabled=True)
    return cfg, host.runtime(bind_ai_context(cfg)), CoreApiConfiguration(cfg, backend=store)


@pytest.mark.parametrize("kind", ["modern", "classic", "quick", "island"])
def test_unconfigured_request_retains_input_and_session(tmp_path, monkeypatch, app, kind):
    cfg, runtime, central = new_runtime(tmp_path, monkeypatch)
    window = runtime.create_window(kind)
    text = "保留这条尚未发送的输入"
    before = list(window.session.messages)
    if kind in {"modern", "classic"}:
        window.input.setPlainText(text)
        send = window.send_message
    else:
        window.input.setText(text)
        send = window._send
    try:
        send()
        actual = window.input.toPlainText() if kind in {"modern", "classic"} else window.input.text()
        assert actual == text
        assert window.session.messages == before
        assert not window.service.busy
    finally:
        window.close()
        runtime.close()
        app.processEvents()


@pytest.mark.parametrize("kind", ["modern", "classic", "quick", "island"])
@pytest.mark.parametrize("simple_mode", [False, True])
def test_existing_window_sends_with_fresh_core_profile(tmp_path, monkeypatch, app, kind, simple_mode):
    from pet.api_config import ApiService

    cfg, runtime, central = new_runtime(tmp_path, monkeypatch)
    window = runtime.create_window(kind)
    captured = []

    class Provider:
        def stream(self, messages, config, cancel, **kwargs):
            captured.append((config.model, config.api_key, config.base_url))
            yield "generated reply"

    window.service.provider = Provider()
    if simple_mode:
        from pet.simple_api import SimpleApiConfiguration, SimpleApiProfile

        simple = SimpleApiConfiguration(cfg)
        profile = SimpleApiProfile(ApiService("shared", "主 API", "https://api.deepseek.com", model="fresh-core-model"))
        simple.save([profile], "shared", expected_revision=simple.revision(), keys={"shared": "FAKE-CENTRAL"})
    else:
        central.save_service(
            ApiService("shared", "Core 服务", "https://api.deepseek.com", model="fresh-core-model"),
            secret="FAKE-CENTRAL",
            grants=[("official.ai-chat", "chat.send", "fresh-core-model")],
            expected_revision=central.revision(),
        )
    try:
        if kind in {"modern", "classic"}:
            window.input.setPlainText("test")
            window.send_message()
        else:
            window.input.setText("test")
            window._send()
        deadline = time.monotonic() + 15
        while (not captured or window.service.busy) and time.monotonic() < deadline:
            loop = QEventLoop()
            QTimer.singleShot(10, loop.quit)
            loop.exec()
        assert captured == [("fresh-core-model", "FAKE-CENTRAL", "https://api.deepseek.com")]
    finally:
        window.close()
        runtime.close()
        deadline = time.monotonic() + 15
        while runtime.drain_status == "awaiting_release" and time.monotonic() < deadline:
            app.processEvents()
        assert runtime.drain_status == "completed"


def test_classic_settings_uses_core_jump_and_never_opens_legacy_secret_store(tmp_path, monkeypatch, app):
    from features.ai_chat.host.chat.settings_dialog import ChatSettingsDialog

    cfg, runtime, central = new_runtime(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("features.ai_chat.host.chat.settings_dialog.SecretStore.get", lambda *args: pytest.fail("central UI must not read a legacy Key"))
    from dataclasses import replace

    runtime.config.context = replace(runtime.config.context, api=replace(runtime.config.context.api, open_settings=lambda: calls.append("core")))
    dialog = ChatSettingsDialog(runtime.config)
    try:
        dialog.show()
        app.processEvents()
        for field in (dialog.url, dialog.key, dialog.model, dialog.timeout, dialog.skip_ssl, dialog.vkey, dialog.provider_combo, dialog.add_provider_btn):
            assert not field.isVisible()
        assert dialog.prompt.isEnabled() and dialog.temp.isEnabled()
        dialog.test.click()
        assert calls == ["core"]
        assert dialog._test_thread is None
    finally:
        dialog.close()
        runtime.close()
        app.processEvents()


@pytest.mark.parametrize("change", ["ordinary_save", "revoke", "revoke_regrant"])
def test_file_interpret_requires_own_grant_and_preserves_inflight_snapshot(tmp_path, monkeypatch, app, change):
    from threading import Event

    from pet.api_config import ApiService
    from tests.test_file_interpret import _FakeWin

    cfg, runtime, central = new_runtime(tmp_path, monkeypatch)
    runtime.config.set("file_interpret", {"enabled": True})
    runtime.config.save()
    service = ApiService("shared", "Core 文件解读", "https://api.deepseek.com", model="file-model")
    central.save_service(service, secret="GENERATED-FILE-ONE", grants=[("official.ai-chat", "chat.send", "chat-model")], expected_revision=central.revision())
    entered, release = Event(), Event()
    captured = []

    class Provider:
        def stream(self, messages, config, cancel, **kwargs):
            captured.append((config.model, config.api_key, cancel))
            entered.set()
            assert release.wait(20)
            yield "GENERATED-FILE-REPLY"

    surface = _FakeWin(tmp_path)
    controller = runtime.create_file_interpreter(surface, provider=Provider())
    document = tmp_path / "generated.txt"
    document.write_text("generated file content only")
    try:
        controller.offer([document])
        controller._on_confirm()
        assert controller._state == "awaiting"
        assert controller._pending == [document]
        assert controller._service is None and controller._session is None
        assert entered.is_set() is False
        central.save_service(
            service,
            secret="GENERATED-FILE-TWO",
            grants=[("official.ai-chat", "chat.send", "chat-model"), ("official.ai-chat", "files.interpret", "file-model")],
            expected_revision=central.revision(),
        )
        controller._on_confirm()
        assert entered.wait(15)
        assert captured[0][:2] == ("file-model", "GENERATED-FILE-TWO")
        if change != "ordinary_save":
            central.revoke("official.ai-chat", "files.interpret", expected_revision=central.revision())
            if change == "revoke_regrant":
                # Simulate another settings process: no same-process publication.
                monkeypatch.setattr(central, "_publish", lambda: None)
                central.save_service(
                    service,
                    grants=[("official.ai-chat", "chat.send", "chat-model"), ("official.ai-chat", "files.interpret", "file-model")],
                    expected_revision=central.revision(),
                )
        else:
            from dataclasses import replace

            central.save_service(
                replace(service, model="new-file-model"),
                secret="GENERATED-FILE-THREE",
                grants=[("official.ai-chat", "chat.send", "chat-model"), ("official.ai-chat", "files.interpret", "new-file-model")],
                expected_revision=central.revision(),
            )
        deadline = time.monotonic() + 15
        while change != "ordinary_save" and not captured[0][2].is_set() and time.monotonic() < deadline:
            app.processEvents()
        assert captured[0][2].is_set() == (change != "ordinary_save")
        release.set()
        deadline = time.monotonic() + 15
        while (not controller._service.is_drained or controller._state == "running") and time.monotonic() < deadline:
            loop = QEventLoop()
            QTimer.singleShot(10, loop.quit)
            loop.exec()
        assert controller._service.is_drained
        assert controller._state == "idle"
        replies = [message.content for message in controller._session.messages if message.role == "assistant"]
        assert replies == (["GENERATED-FILE-REPLY"] if change == "ordinary_save" else [])
    finally:
        release.set()
        runtime.close()
        deadline = time.monotonic() + 15
        while runtime.drain_status == "awaiting_release" and time.monotonic() < deadline:
            app.processEvents()
        assert runtime.drain_status == "completed"
        surface.close()
        app.processEvents()


@pytest.mark.parametrize("status", [401, None])
def test_central_chat_error_signal_never_reflects_remote_credentials(tmp_path, monkeypatch, app, status):
    from features.ai_chat.host.chat.providers import ProviderError
    from pet.api_config import ApiService

    cfg, runtime, central = new_runtime(tmp_path, monkeypatch)
    central.save_service(
        ApiService("shared", "Core 测试", "https://api.deepseek.com", model="generated-model"),
        secret="GENERATED-KEY-ECHO",
        grants=[("official.ai-chat", "chat.send", "generated-model")],
        expected_revision=central.revision(),
    )

    class Provider:
        def stream(self, *args, **kwargs):
            raise ProviderError("GENERATED-KEY-ECHO https://private.example/secret?token=GENERATED-TOKEN", status=status)
            yield  # keep this boundary a streaming provider

    service = runtime.create_service(provider=Provider())
    errors = []
    service.error.connect(lambda rid, message: errors.append(message))
    try:
        service.send([], runtime.config.request_config(runtime.config.chat_settings().active_config, operation="chat.send"))
        deadline = time.monotonic() + 15
        while (not errors or not service.is_drained) and time.monotonic() < deadline:
            loop = QEventLoop()
            QTimer.singleShot(10, loop.quit)
            loop.exec()
        assert errors
        assert "GENERATED" not in errors[0] and "private.example" not in errors[0]
        if status == 401:
            assert "认证" in errors[0] or "Key" in errors[0]
    finally:
        runtime.close()
        app.processEvents()


@pytest.mark.parametrize("change", ["revoke_regrant", "endpoint_change"])
def test_prepared_request_cannot_adopt_a_replacement_grant(tmp_path, monkeypatch, app, change):
    from dataclasses import replace

    from pet.api_config import ApiService

    cfg, runtime, central = new_runtime(tmp_path, monkeypatch)
    profile = ApiService("shared", "Core snapshot", "https://api.deepseek.com", model="test-model")
    grants = [("official.ai-chat", "chat.send", "test-model")]
    central.save_service(profile, secret="GENERATED-SNAPSHOT-OLD", grants=grants, expected_revision=central.revision())
    prepared = runtime.config.request_config(runtime.config.chat_settings().active_config)
    assert "authorization_version" not in prepared.to_dict()
    calls = []

    class Provider:
        def stream(self, *args, **kwargs):
            calls.append(True)
            yield "must-not-send"

    service = runtime.create_service(provider=Provider())
    if change == "revoke_regrant":
        central.revoke("official.ai-chat", "chat.send", expected_revision=central.revision())
    else:
        profile = replace(profile, base_url="https://replacement.example")
    central.save_service(profile, secret="GENERATED-SNAPSHOT-NEW", grants=grants, expected_revision=central.revision())
    try:
        with pytest.raises(PermissionError, match="api_configuration_changed"):
            service.send([], prepared)
        assert calls == [] and service.is_drained
    finally:
        runtime.close()
        deadline = time.monotonic() + 15
        while runtime.drain_status == "awaiting_release" and time.monotonic() < deadline:
            app.processEvents()
        assert runtime.drain_status == "completed"
