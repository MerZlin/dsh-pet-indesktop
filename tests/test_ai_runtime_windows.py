"""Real owner windows through Core ports, no legacy AI fallback or model access."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from pet import feature_distribution
from tests.test_ai_host_contracts import binding, wait


@pytest.fixture
def app_and_sessions(monkeypatch):
    from features.ai_chat.host.chat import session_store as ss

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ss, "_registry", ss._WriterRegistry())
    yield app
    ss.close_all_writers()
    app.processEvents()


def test_ai_actual_windows_use_owner_data_and_runtime_services(tmp_path, app_and_sessions):
    from features.ai_chat.host.runtime import AiRuntime

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    try:
        windows = [runtime.create_window(kind) for kind in ("modern", "classic", "quick", "island")]
        assert all(window.__class__.__module__.startswith("features.ai_chat.host") for window in windows)
        assert all(window.config is runtime.config for window in windows)
        assert all(window.service in runtime._services for window in windows)
        assert runtime.config.dir == cfg.dir / "feature-data" / "official.ai-chat"
        assert runtime.config.instance_id == cfg.instance_id
        for window in windows:
            assert window.store.root.is_relative_to(runtime.config.dir)
            window.show()
        host.disable(context.owner)
        assert all(not window.isVisible() and not window.isEnabled() for window in windows)
        wait(app_and_sessions, lambda: runtime.drain_status == "completed")
        host.enable(context.owner)
        assert all(window.isEnabled() and not window.isVisible() for window in windows)
        assert isinstance(runtime, AiRuntime)
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")


def test_ai_display_ports_do_not_grant_core_config_or_installation_paths(tmp_path, app_and_sessions):
    from features.ai_chat.host.config import AiConfiguration

    cfg, host, context = binding(tmp_path)
    config = AiConfiguration(context)
    assert config.get("character") == cfg.get("character")
    assert config.character_alias(cfg.get("character")) == cfg.character_alias(cfg.get("character"))
    assert config.dir != cfg.dir
    for field in ("plugins", "memory", "quota", "autostart_wanted", "credentials"):
        with pytest.raises(PermissionError, match="preference_not_granted"):
            config.get(field)
    with pytest.raises(PermissionError, match="preference_not_granted"):
        config.set("character", "another")


def test_ai_service_rechecks_authority_before_thread_creation(tmp_path, app_and_sessions):
    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)

    class GeneratedProvider:
        def stream(self, *args, **kwargs):
            raise AssertionError("thread must not reach generated provider")
            yield ""

    service = runtime.create_service(provider=GeneratedProvider())
    try:
        host.bind_authority(context.owner, lambda: False)
        with pytest.raises(RuntimeError, match="ai_requests_not_accepting"):
            service.send([], runtime.config.chat_settings().active_config)
        assert service._worker is None and service.is_drained
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")


def test_small_core_pet_instance_opens_actual_verified_owner_window(tmp_path, app_and_sessions, monkeypatch):
    from pet.app import PetInstance

    cfg, host, context = binding(tmp_path)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    pet = QWidget()
    instance = PetInstance.__new__(PetInstance)
    instance.config = cfg
    instance.shell = SimpleNamespace(feature_host=host, enable_chat=True, system_notify=None)
    instance.win = pet
    instance.modern_chat_window = None
    instance.legacy_chat_window = None
    instance.chat_window = None
    instance._pending_dialog_opens = set()
    try:
        instance.open_modern_chat()
        window = instance.modern_chat_window
        assert window.__class__.__module__ == "features.ai_chat.host.chat.widgets"
        assert window.config.dir != cfg.dir
        assert window.service in instance._ai_runtime._services
        assert window.isVisible()
        host.disable("official.ai-chat")
        assert not window.isVisible()
        instance.open_modern_chat()
        assert not window.isVisible()
    finally:
        runtime = getattr(instance, "_ai_runtime", None)
        if runtime:
            runtime.close()
            wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        pet.close()


def test_closing_one_ai_instance_does_not_freeze_another_instances_writer(tmp_path, app_and_sessions):
    from features.ai_chat.host.chat.session_store import SessionStore

    cfg_a, host_a, context_a = binding(tmp_path / "a")
    cfg_b, host_b, context_b = binding(tmp_path / "b")
    a, b = host_a.runtime(context_a), host_b.runtime(context_b)
    store_a = SessionStore(a.config.dir, a.config.instance_id)
    store_b = SessionStore(b.config.dir, b.config.instance_id)
    message_a = store_a.create("generated", "p1", "a")
    message_b = store_b.create("generated", "p1", "b")
    try:
        assert store_a.save(message_a) and store_b.save(message_b)
        a.close()
        wait(app_and_sessions, lambda: a.drain_status != "awaiting_release")
        assert a.drain_status == "completed"
        assert b.accepting and store_b.save(message_b)
    finally:
        b.close()
        wait(app_and_sessions, lambda: b.drain_status != "awaiting_release")


@pytest.mark.parametrize("kind", ("modern", "classic", "quick", "island"))
def test_revoked_authority_does_not_consume_input_or_append_session(kind, tmp_path, app_and_sessions):
    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    window = runtime.create_window(kind)
    before = list(window.session.messages)
    host.bind_authority(context.owner, lambda: False)
    if kind in {"modern", "classic"}:
        window.input.setPlainText("generated unsent text")
        send = window.send_message
        text = window.input.toPlainText
    else:
        window.input.setText("generated unsent text")
        send = window._send
        text = window.input.text
    try:
        send()
        assert text() == "generated unsent text"
        assert window.session.messages == before
        assert window.service._worker is None
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")


def test_new_runtime_reopens_completed_closed_instance_without_reviving_old_runtime(tmp_path, app_and_sessions):
    from features.ai_chat.host.chat.session_store import SessionStore

    cfg, host, context = binding(tmp_path)
    old = host.runtime(context)
    old.close()
    wait(app_and_sessions, lambda: old.drain_status != "awaiting_release")
    new = host.runtime(context)
    store = SessionStore(new.config.dir, new.config.instance_id)
    try:
        assert old is not new and not old.accepting and new.accepting
        assert store.save(store.create("generated", "p1", "reopened"))
    finally:
        new.close()
        wait(app_and_sessions, lambda: new.drain_status != "awaiting_release")


def test_serial_test_reset_clears_finished_root_and_global_drain_fences(tmp_path, app_and_sessions):
    from features.ai_chat.host.chat import session_store as ss

    a = ss.SessionStore(tmp_path, "a")
    b = ss.SessionStore(tmp_path, "b")
    assert a.save(a.create("generated", "p1", "a"))
    assert b.save(b.create("generated", "p1", "b"))
    root_drain = ss.begin_session_drain(root=a.root)
    global_drain = ss.begin_session_drain()
    wait(app_and_sessions, lambda: root_drain.completed and global_drain.completed)
    ss.reset_writers_for_tests()
    assert a.save(a.create("generated", "p1", "new a"))
    assert b.save(b.create("generated", "p1", "new b"))


def test_paused_empty_instance_cannot_create_new_session_writer(tmp_path, app_and_sessions):
    from features.ai_chat.host.chat.session_store import SessionStore

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    store = SessionStore(runtime.config.dir, runtime.config.instance_id)
    try:
        host.disable(context.owner)
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        assert not store.save(store.create("generated", "p1", "blocked"))
        host.enable(context.owner)
        assert store.save(store.create("generated", "p1", "resumed"))
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")


def test_ai_owned_file_interpretation_is_runtime_bound_and_revocable(tmp_path, app_and_sessions):
    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)

    class FileSurface(QWidget):
        def __init__(self):
            super().__init__()
            self.alerts = []
            self.bubbles = []

        def show_alert(self, text, **kwargs):
            self.alerts.append((text, kwargs))

        def resolve_alert(self, identity):
            self.alerts.clear()

        def show_bubble(self, text, **kwargs):
            self.bubbles.append(text)

    surface = FileSurface()
    source = tmp_path / "generated-file.txt"
    source.write_text("generated canary text", encoding="utf-8")
    try:
        controller = runtime.create_file_interpreter(surface)
        assert controller.__class__.__module__ == "features.ai_chat.host.file_interpret"
        assert controller._win.cfg is runtime.config
        assert not hasattr(controller._win, "grab")
        controller.offer([source])
        assert controller._state == "awaiting" and surface.alerts
        host.disable(context.owner)
        assert controller._state == "idle" and not surface.alerts
        controller.offer([source])
        assert not surface.alerts and controller._service is None
        wait(app_and_sessions, lambda: runtime.drain_status == "completed")
        host.enable(context.owner)
        controller.offer([source])
        assert controller._state == "awaiting"
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        surface.close()


def test_ai_owned_external_turn_receiver_uses_scoped_sessions_and_execution_authority(tmp_path, app_and_sessions):
    from pet.plugins.services import ServiceRequest, ServiceRoute

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    route = ServiceRoute("generated-window", str(cfg.instance_id or "primary"), str(cfg.get("character")))
    payload = {"result_id": "generated-1", "kind": "manual", "user_text": "generated question", "reply": "generated reply"}
    try:
        receiver = runtime.create_external_receiver(route, lambda: None)
        request = ServiceRequest("official.screen-understanding", route, payload)
        assert receiver.receive(request).status == "accepted"
        wait(app_and_sessions, lambda: len(list(runtime.config.dir.rglob("*.json"))) == 1)
        files = list(runtime.config.dir.rglob("*.json"))
        assert len(files) == 1
        before = files[0].read_bytes()
        host.bind_authority(context.owner, lambda: False)
        request = ServiceRequest("official.screen-understanding", route, {**payload, "result_id": "generated-2"})
        assert receiver.receive(request).status in {"denied", "unavailable"}
        assert files[0].read_bytes() == before
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")


def test_small_core_island_routes_verified_ai_runtime_without_legacy_import(tmp_path, app_and_sessions, monkeypatch):
    from pet.app import AppShell, PetInstance

    cfg, host, context = binding(tmp_path)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    shell = AppShell.__new__(AppShell)
    shell.config = cfg
    shell.feature_host = host
    shell.system_notify = None
    shell.island = QWidget()
    shell.island.resize(180, 40)
    shell.island_chat = None
    instance = PetInstance.__new__(PetInstance)
    instance.config = cfg
    instance.shell = shell
    instance.win = QWidget()
    instance.chat_window = None
    shell.instance = instance
    shell._instances = [instance]
    try:
        shell._show_island_chat(activate=False, reply_text="generated display-only reply")
        assert shell.island_chat.__class__.__module__ == "features.ai_chat.host.island_chat"
        assert shell.island_chat.config is instance._ai_runtime.config
        assert shell.island_chat.isVisible()
        host.disable(context.owner)
        assert not shell.island_chat.isVisible()
        shell._show_island_chat(activate=False)
        assert not shell.island_chat.isVisible()
    finally:
        runtime = getattr(instance, "_ai_runtime", None)
        if runtime:
            runtime.close()
            wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        shell.island.close()
        instance.win.close()


def test_small_core_optional_service_route_uses_owner_receiver(tmp_path, app_and_sessions, monkeypatch):
    from pet.app import PetInstance

    cfg, host, context = binding(tmp_path)
    from features.screen_understanding.host.factory import create_host as create_screen_host

    host.provide(create_screen_host())
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    instance = PetInstance.__new__(PetInstance)
    instance.config = cfg
    instance.shell = SimpleNamespace(feature_host=host)
    instance.win = QWidget()
    instance.chat_window = None
    try:
        instance._bind_optional_services(instance.win)
        result = instance.win.on_external_text("generated-result", "manual", "question", "reply")
        assert result.status == "accepted"
        host.disable(context.owner)
        assert instance.win.on_external_text("generated-next", "manual", "question", "reply").status == "denied"
    finally:
        cleanup = getattr(instance, "_optional_service_cleanup", None)
        if cleanup:
            cleanup()
        runtime = getattr(instance, "_ai_runtime", None)
        if runtime:
            runtime.close()
            wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        instance.win.close()


def test_small_core_no_ai_file_interpreter_does_not_import_legacy_code(tmp_path, app_and_sessions, monkeypatch):
    from pet.window_optional_services import WindowFeatureGateMixin

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)

    class FileSurface(QWidget, WindowFeatureGateMixin):
        def __init__(self):
            super().__init__()
            self._file_interpret = None
            self._file_eater = SimpleNamespace(interpret_offer="old-placeholder")

        def install_file_eater(self):
            return self._file_eater

    surface = FileSurface()
    try:
        assert surface.install_file_interpreter() is None
        assert surface._file_eater.interpret_offer is None
    finally:
        surface.close()


def test_small_core_owner_menu_and_callbacks_revoke_and_rebind(tmp_path, app_and_sessions, monkeypatch):
    from PySide6.QtWidgets import QMenu

    from pet.ai_bindings import bind_ai_window, build_ai_menu

    cfg, host, context = binding(tmp_path)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    calls = []
    surface = QWidget()
    surface.cfg = cfg
    surface.feature_host = host
    instance = SimpleNamespace(
        shell=SimpleNamespace(feature_host=host),
        win=surface,
        config=cfg,
        open_chat=lambda: calls.append("chat"),
        open_quick_chat=lambda: calls.append("quick"),
        open_chat_settings=lambda: calls.append("settings"),
        _bind_optional_services=lambda target: None,
    )
    menu = QMenu()
    try:
        bind_ai_window(instance, surface)
        assert callable(surface.on_open_chat)
        action = build_ai_menu(menu, surface, "chat")
        action.trigger()
        assert calls == ["chat"]
        host.disable(context.owner)
        assert surface.on_open_chat is None and not action.isVisible()
        action.trigger()
        assert calls == ["chat"]
        host.enable(context.owner)
        assert callable(surface.on_open_chat)
        action.trigger()
        assert calls == ["chat"]
        current = build_ai_menu(menu, surface, "chat")
        current.trigger()
        assert calls == ["chat", "chat"]
    finally:
        menu.close()
        surface.close()


def test_file_interpretation_uses_real_runtime_thread_and_scoped_secret(tmp_path, app_and_sessions):
    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)

    class GeneratedProvider:
        def __init__(self):
            self.keys = []

        def stream(self, messages, provider, cancel):
            self.keys.append(provider.api_key)
            yield "generated file response"

    class FileSurface(QWidget):
        def show_alert(self, text, **kwargs):
            self.confirm = kwargs["buttons"][0][1]

        def resolve_alert(self, identity):
            pass

        def show_bubble(self, text, **kwargs):
            pass

    settings = runtime.config.chat_settings()
    provider = settings.active_config
    provider.api_key_ref = runtime.config.save_provider_secret(provider, "generated-file-secret")
    runtime.config.set_chat_settings(settings)
    runtime.config.save()
    generated = GeneratedProvider()
    surface = FileSurface()
    source = tmp_path / "generated-code.py"
    source.write_text("print('generated')", encoding="utf-8")
    try:
        controller = runtime.create_file_interpreter(surface, provider=generated)
        controller.offer([source])
        surface.confirm()
        wait(app_and_sessions, lambda: controller._state == "idle" and controller._service.is_drained)
        assert generated.keys == ["generated-file-secret"]
        assert controller._service.parent() is runtime
        assert controller._service in runtime._services
        assert controller._store.root.is_relative_to(runtime.config.dir)
        assert any(message.role == "assistant" for message in controller._session.messages)
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        surface.close()


@pytest.mark.parametrize("kind", ["modern", "classic", "quick", "island"])
def test_deleted_ai_window_does_not_break_owner_drain(kind, tmp_path, app_and_sessions):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    window = runtime.create_window(kind)
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(window)
    try:
        runtime.pause()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        assert runtime.drain_status == "completed"
        assert runtime.resume()
    finally:
        runtime.close()


def test_core_finished_subscription_is_runtime_scoped_and_revocable(tmp_path, app_and_sessions):
    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)

    class GeneratedProvider:
        def stream(self, messages, provider, cancel):
            yield "generated complete reply"

    service = runtime.create_service(provider=GeneratedProvider())
    received = []
    try:
        unsubscribe = runtime.subscribe_finished(received.append)
        service.send([], runtime.config.chat_settings().active_config)
        wait(app_and_sessions, lambda: service.is_drained)
        assert received == ["generated complete reply"]
        unsubscribe()
        service.finished.emit("late", "must not arrive")
        assert received == ["generated complete reply"]
        runtime.subscribe_finished(received.append)
        host.disable(context.owner)
        service.finished.emit("revoked", "must not arrive")
        assert received == ["generated complete reply"]
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")


def test_small_core_instance_close_uses_retained_runtime_not_legacy_writer(tmp_path, app_and_sessions, monkeypatch):
    from pet.app import AppShell

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    instance = SimpleNamespace(config=cfg, _ai_runtime=runtime)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    try:
        AppShell._close_instance_session_writer(SimpleNamespace(), instance)
        assert not runtime.accepting
        assert runtime._closing
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        assert runtime.drain_status == "completed"
    finally:
        runtime.close()


def test_file_offer_observes_latest_owner_policy(tmp_path, app_and_sessions):
    from features.ai_chat.host.config import AiConfiguration

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)

    class FileSurface(QWidget):
        def show_alert(self, text, **options):
            self.alerts.append(text)

        def resolve_alert(self, identity):
            pass

    surface = FileSurface()
    surface.alerts = []
    source = tmp_path / "generated.txt"
    source.write_text("generated", encoding="utf-8")
    controller = runtime.create_file_interpreter(surface)
    edited = AiConfiguration(context)
    edited.set("file_interpret", {"enabled": False})
    edited.save()
    try:
        controller.offer([source])
        assert surface.alerts == []
    finally:
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
        surface.close()


def test_ai_classic_settings_preserves_native_dialog_result(tmp_path, app_and_sessions):
    from PySide6.QtWidgets import QDialog

    from features.ai_chat.host.chat.settings_dialog import ChatSettingsDialog

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    dialog = ChatSettingsDialog(runtime.config)
    try:
        # A connection-status label must never shadow Qt's native result().
        assert dialog.result() == QDialog.DialogCode.Rejected
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()
        dialog.deleteLater()
        runtime.close()
        wait(app_and_sessions, lambda: runtime.drain_status != "awaiting_release")
