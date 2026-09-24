# -*- coding: utf-8 -*-
"""Phase 2 Core 插件运行时和官方节日提醒插件契约测试。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from datetime import datetime, timezone

import pytest

from pet.config import Config
from pet.plugins import (
    CapabilityDenied,
    CommandRegistry,
    ContentProviderRegistry,
    CoreEvent,
    CoreEventBus,
    FESTIVAL_MANIFEST,
    FestivalReminderPlugin,
    PluginConfigStore,
    PluginManifest,
    PluginRegistry,
    PresentationPort,
    SchedulerPort,
)


class _Config:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.saved = 0

    def save(self):
        self.saved += 1
        return True


class _FakeHandle:
    def __init__(self):
        self.active = True

    def cancel(self):
        self.active = False

    def is_active(self):
        return self.active


class _FakeScheduler:
    def __init__(self):
        self.handles = {}
        self._on_error = None
        self._next = 1

    def set_error_handler(self, handler):
        self._on_error = handler

    def call_later(self, callback, delay_ms, *, owner="core"):
        del callback, delay_ms
        handle = _FakeHandle()
        self.handles[(owner, self._next)] = handle
        self._next += 1
        return handle

    def call_repeating(self, callback, interval_ms, *, owner="core"):
        return self.call_later(callback, interval_ms, owner=owner)

    def cancel_owner(self, owner):
        for (handle_owner, _), handle in self.handles.items():
            if handle_owner == owner:
                handle.cancel()

    def timer_count(self, owner=None):
        return sum(
            1
            for (handle_owner, _), handle in self.handles.items()
            if handle.active and (owner is None or handle_owner == owner)
        )


def _manifest(plugin_id, *, dependencies=(), capabilities=(), default_enabled=True):
    return PluginManifest(
        id=plugin_id,
        name=plugin_id,
        version="1.0.0",
        kind="in_process",
        api_version="1",
        core_requires=">=5.0.0,<6.0.0",
        dependencies=tuple(dependencies),
        capabilities=tuple(capabilities),
        default_enabled=default_enabled,
    )


def _registry(config=None, *, scheduler=None, presentation=None):
    return PluginRegistry(
        config=config or _Config(),
        scheduler=scheduler or _FakeScheduler(),
        presentation=presentation or PresentationPort(),
        commands=CommandRegistry(),
        content=ContentProviderRegistry(),
    )


def test_event_payload_is_json_serializable():
    with pytest.raises(TypeError):
        CoreEvent.now("test", "unit", {"value": object()})


def test_event_callback_fault_isolated_and_subscription_removed():
    registry = _registry()
    events = []

    class BadPlugin:
        def __init__(self, context):
            self.context = context

        def start(self):
            self.context.events.subscribe("test.event", self._on_event)

        def _on_event(self, event):
            del event
            raise RuntimeError("callback failed")

    registry.register_builtin(_manifest("test.bad", capabilities=()), BadPlugin, enabled=True)
    registry.start_all()
    assert registry.get("test.bad").state == "running"

    registry.events.subscribe("test.event", lambda event: events.append(event.type), owner="core.test")
    registry.publish_event("test.event", source="unit", payload={})

    assert events == ["test.event"]
    record = registry.get("test.bad")
    assert record.state == "fault"
    assert registry.events.subscriptions(owner="test.bad") == ()
    assert any(item.plugin_id == "test.bad" and item.stage == "event" for item in registry.diagnostics())


def test_registry_can_discover_a_factory_registered_after_initial_scan():
    registry = _registry()
    registry.discover()

    class LatePlugin:
        pass

    registry.register_builtin(_manifest("test.late"), LatePlugin, enabled=True)

    assert registry.get("test.late").manifest.id == "test.late"
    assert not any(item.plugin_id == "test.late" and item.stage == "discovery" for item in registry.diagnostics())


def test_plugin_namespace_survives_real_config_reload(tmp_path):
    config = Config(base=Path(tmp_path))
    store = PluginConfigStore(
        config,
        FESTIVAL_MANIFEST.id,
        legacy_map=FESTIVAL_MANIFEST.legacy_config_map,
    )
    store.set("enabled", True)
    assert config.save() is True

    reloaded = Config(base=Path(tmp_path))
    reloaded_store = PluginConfigStore(
        reloaded,
        FESTIVAL_MANIFEST.id,
        legacy_map=FESTIVAL_MANIFEST.legacy_config_map,
    )

    assert reloaded_store.get("enabled") is True
    assert reloaded.data["plugins"][FESTIVAL_MANIFEST.id]["settings"]["enabled"] is True


def test_registry_starts_dependencies_topologically_and_stops_in_reverse():
    order = []
    registry = _registry()

    class Plugin:
        def __init__(self, context, name):
            self.name = name

        def start(self):
            order.append(f"start:{self.name}")

        def stop(self):
            order.append(f"stop:{self.name}")

    registry.register_builtin(
        _manifest("test.child", dependencies=("test.base",)),
        lambda context: Plugin(context, "child"),
        enabled=True,
    )
    registry.register_builtin(
        _manifest("test.base"),
        lambda context: Plugin(context, "base"),
        enabled=True,
    )

    registry.start_all()
    assert order[:2] == ["start:base", "start:child"]
    registry.stop_all()
    assert order[-2:] == ["stop:child", "stop:base"]


def test_dependency_failure_does_not_start_dependent():
    started = []
    registry = _registry()

    class Broken:
        def start(self):
            raise RuntimeError("base unavailable")

    registry.register_builtin(_manifest("test.base"), lambda context: Broken(), enabled=True)
    class Child:
        def start(self):
            started.append("child")

    registry.register_builtin(
        _manifest("test.child", dependencies=("test.base",)),
        lambda context: Child(),
        enabled=True,
    )

    registry.start_all()

    assert started == []
    assert registry.get("test.base").state == "fault"
    assert registry.get("test.child").state == "fault"
    assert any(item.stage == "dependency" and item.plugin_id == "test.child" for item in registry.diagnostics())


def test_plugin_config_namespace_isolated_and_legacy_mapping_round_trips():
    config = _Config({"festival_reminder_enabled": True, "unrelated": "keep"})
    store = PluginConfigStore(
        config,
        FESTIVAL_MANIFEST.id,
        legacy_map=FESTIVAL_MANIFEST.legacy_config_map,
    )

    assert store.get("enabled") is True
    assert config.data["plugins"][FESTIVAL_MANIFEST.id]["settings"]["enabled"] is True
    assert config.data["plugins"][FESTIVAL_MANIFEST.id]["legacy"]["festival_reminder_enabled"] is True

    store.set("enabled", False)
    assert config.data["festival_reminder_enabled"] is False
    assert store.as_legacy_config()["festival_reminder_enabled"] is False
    assert config.data["unrelated"] == "keep"


def test_plugin_config_capabilities_are_enforced():
    registry = _registry()
    captured = {}

    class ReadOnlyPlugin:
        def __init__(self, context):
            captured["context"] = context

    registry.register_builtin(_manifest("test.read-only"), ReadOnlyPlugin, enabled=True)
    # The manifest has no settings.read/settings.write capability.
    registry.start("test.read-only")

    with pytest.raises(CapabilityDenied):
        captured["context"].config.get("value")
    with pytest.raises(CapabilityDenied):
        captured["context"].config.set("value", 1)


def test_capability_denied_for_presentation_and_command_ports():
    registry = _registry()
    captured = {}

    class RestrictedPlugin:
        def __init__(self, context):
            captured["context"] = context

    registry.register_builtin(_manifest("test.restricted"), RestrictedPlugin, enabled=True)
    registry.start("test.restricted")
    context = captured["context"]

    with pytest.raises(CapabilityDenied):
        context.presentation.show_bubble("blocked")
    with pytest.raises(CapabilityDenied):
        context.commands.register("blocked", lambda: None)


def test_official_festival_plugin_uses_ports_and_cleans_up_on_stop():
    config = _Config({"festival_reminder_enabled": False, "festival_reminder_speak": False})
    scheduler = _FakeScheduler()
    bubbles = []
    notifications = []
    presentation = PresentationPort(
        show_bubble=lambda text, **kwargs: bubbles.append((text, kwargs)) or True,
        notify=lambda title, message, **kwargs: notifications.append((title, message, kwargs)) or True,
        speak=lambda text, **kwargs: True,
    )
    registry = _registry(config, scheduler=scheduler, presentation=presentation)
    registry.register_builtin(FESTIVAL_MANIFEST, FestivalReminderPlugin, enabled=True)

    registry.start(FESTIVAL_MANIFEST.id)
    instance = registry.get_instance(FESTIVAL_MANIFEST.id)
    assert isinstance(instance, FestivalReminderPlugin)
    assert scheduler.timer_count(FESTIVAL_MANIFEST.id) == 1
    assert "official.festival-reminder.remind_now" in registry.commands.list()

    registry.commands.invoke("official.festival-reminder.remind_now")
    assert bubbles or notifications

    registry.stop_all()
    assert scheduler.timer_count(FESTIVAL_MANIFEST.id) == 0
    assert registry.commands.list(owner=FESTIVAL_MANIFEST.id) == ()
    assert registry.events.subscriptions(owner=FESTIVAL_MANIFEST.id) == ()


def test_core_event_now_uses_timezone_aware_timestamp():
    event = CoreEvent.now("core.app.started", "core")
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))


def test_invalid_manifest_compatibility_and_plugin_kind_are_diagnostic():
    invalid = _registry()
    invalid.register_builtin(
        {
            "id": "test.invalid",
            "name": "Invalid",
            "version": "not-a-version",
            "kind": "in_process",
            "api_version": "1",
        },
        lambda context: object(),
        enabled=True,
    )
    invalid.discover()
    assert any(item.plugin_id == "test.invalid" and item.stage == "manifest" for item in invalid.diagnostics())

    api_mismatch = _registry()
    api_mismatch.register_builtin(
        replace(_manifest("test.api-mismatch"), api_version="2"),
        lambda context: object(),
        enabled=True,
    )
    api_mismatch.discover()
    assert any(item.plugin_id == "test.api-mismatch" and item.stage == "compatibility" for item in api_mismatch.diagnostics())

    core_mismatch = PluginRegistry(
        core_version="4.0.0",
        config=_Config(),
        scheduler=_FakeScheduler(),
        presentation=PresentationPort(),
        commands=CommandRegistry(),
        content=ContentProviderRegistry(),
    )
    core_mismatch.register_builtin(
        _manifest("test.core-mismatch"),
        lambda context: object(),
        enabled=True,
    )
    core_mismatch.discover()
    assert any(item.plugin_id == "test.core-mismatch" and item.stage == "compatibility" for item in core_mismatch.diagnostics())


def test_duplicate_plugin_id_is_rejected_without_blocking_first_definition():
    registry = _registry()
    registry.register_builtin(_manifest("test.duplicate"), lambda context: object(), enabled=True)
    registry.register_builtin(_manifest("test.duplicate"), lambda context: object(), enabled=True)

    records = registry.discover()

    assert [record.manifest.id for record in records] == ["test.duplicate"]
    assert any(item.plugin_id == "test.duplicate" and item.stage == "discovery" for item in registry.diagnostics())


def test_dependency_cycle_is_faulted_without_blocking_registry():
    registry = _registry()
    registry.register_builtin(
        _manifest("test.cycle-a", dependencies=("test.cycle-b",)),
        lambda context: object(),
        enabled=True,
    )
    registry.register_builtin(
        _manifest("test.cycle-b", dependencies=("test.cycle-a",)),
        lambda context: object(),
        enabled=True,
    )

    registry.start_all()

    assert registry.get("test.cycle-a").state == "fault"
    assert registry.get("test.cycle-b").state == "fault"
    assert any(item.stage == "dependency" and "cycle" in item.reason for item in registry.diagnostics())


def test_plugin_start_exception_is_isolated():
    registry = _registry()

    class Broken:
        def start(self):
            raise RuntimeError("start failed")

    registry.register_builtin(_manifest("test.start-error"), lambda context: Broken(), enabled=True)
    registry.start_all()

    record = registry.get("test.start-error")
    assert record.state == "fault"
    assert any(item.plugin_id == "test.start-error" and item.stage == "start" for item in registry.diagnostics())


def test_plugin_stop_exception_does_not_block_other_plugins():
    registry = _registry()
    stopped = []

    class BrokenStop:
        def stop(self):
            raise RuntimeError("stop failed")

    class Healthy:
        def stop(self):
            stopped.append("healthy")

    registry.register_builtin(_manifest("test.bad-stop"), lambda context: BrokenStop(), enabled=True)
    registry.register_builtin(_manifest("test.healthy-stop"), lambda context: Healthy(), enabled=True)
    registry.start_all()
    registry.stop_all()

    assert stopped == ["healthy"]
    assert registry.get("test.bad-stop").state == "stopped"
    assert any(item.plugin_id == "test.bad-stop" and item.stage == "stop" for item in registry.diagnostics())
