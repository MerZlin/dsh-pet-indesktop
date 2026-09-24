"""Core 进程内插件运行时。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .capabilities import CapabilitySet
from .config import PluginConfigStore
from .events import CoreEvent, CoreEventBus, ScopedEventBus, Subscription
from .manifest import PluginDiagnostic, PluginManifest, PluginManifestError, version_satisfies
from .ports import (
    CommandRegistry,
    ContentProviderRegistry,
    PresentationPort,
    SchedulerPort,
    ScopedCommandRegistry,
    ScopedContentProviderRegistry,
    ScopedPresentationPort,
    ScopedSchedulerPort,
    StructuredLogger,
)

_LOG = logging.getLogger(__name__)
CORE_PLUGIN_VERSION = "5.0.0"
CORE_PLUGIN_API_VERSION = "1"


class PluginLifecycle(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class PluginContext:
    """插件可见的受限 Core 服务集合。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        core_version: str,
        api_version: str,
        config: PluginConfigStore,
        events: CoreEventBus,
        presentation: PresentationPort,
        scheduler: SchedulerPort,
        commands: CommandRegistry,
        content: ContentProviderRegistry,
        capabilities: CapabilitySet,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.core_version = core_version
        self.api_version = api_version
        self.config = config
        self.capabilities = capabilities
        self.logger = logger or StructuredLogger(plugin_id)
        self._events = events
        self._commands = commands
        self.events: ScopedEventBus = ScopedEventBus(events, plugin_id)
        self.presentation = ScopedPresentationPort(presentation, capabilities)
        self.scheduler = ScopedSchedulerPort(scheduler, plugin_id, capabilities)
        self.commands = ScopedCommandRegistry(commands, plugin_id, capabilities)
        self.content = ScopedContentProviderRegistry(content)
        self._disposed = False

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.scheduler.cancel_all()
        self.events.clear()
        self._commands.unregister_owner(self.plugin_id)


@dataclass
class PluginRecord:
    manifest: PluginManifest
    factory: Callable[[PluginContext], Any]
    enabled: bool = True
    state: str = "discovered"
    instance: Any | None = None
    context: PluginContext | None = None
    diagnostics: list[PluginDiagnostic] = field(default_factory=list)


class PluginRegistry:
    """官方 in-process 插件 Registry。

    Phase 2 只接受显式 ``register_builtin`` factory，不扫描或执行未知来源的
    Python entrypoint。所有异常都停留在单个 PluginRecord 和诊断列表内。
    """

    def __init__(
        self,
        *,
        core_version: str = CORE_PLUGIN_VERSION,
        api_version: str = CORE_PLUGIN_API_VERSION,
        config: Any | None = None,
        events: CoreEventBus | None = None,
        presentation: PresentationPort | None = None,
        scheduler: SchedulerPort | None = None,
        commands: CommandRegistry | None = None,
        content: ContentProviderRegistry | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.core_version = str(core_version)
        self.api_version = str(api_version)
        self.config = config
        self.events = events or CoreEventBus()
        self.presentation = presentation or PresentationPort()
        self.scheduler = scheduler or SchedulerPort()
        self.commands = commands or CommandRegistry()
        self.content = content or ContentProviderRegistry()
        self.logger = logger or StructuredLogger("core")
        self._definitions: list[tuple[Any, Callable[[PluginContext], Any], bool | None]] = []
        self._records: dict[str, PluginRecord] = {}
        self._diagnostics: list[PluginDiagnostic] = []
        self._diagnostic_keys: set[tuple[str, str, str]] = set()
        self._discovered = False
        self._processed_definition_count = 0
        self._publishing_error = False
        self.events.set_error_handler(self._handle_event_error)
        self.scheduler.set_error_handler(self._handle_scheduler_error)

    def register_builtin(
        self,
        manifest: PluginManifest | dict[str, Any],
        factory: Callable[[PluginContext], Any],
        *,
        enabled: bool | None = None,
    ) -> None:
        if not callable(factory):
            raise TypeError("plugin factory must be callable")
        self._definitions.append((manifest, factory, enabled))
        self._discovered = False

    # ``register`` 是给官方内置测试和未来内置 provider 的简短别名。
    register = register_builtin

    def _add_diagnostic(
        self,
        plugin_id: str,
        stage: str,
        reason: str,
        *,
        severity: str = "error",
        status: str = "fault",
        details: dict[str, Any] | None = None,
    ) -> PluginDiagnostic:
        key = (str(plugin_id), str(stage), str(reason))
        if key in self._diagnostic_keys:
            for item in self._diagnostics:
                if (item.plugin_id, item.stage, item.reason) == key:
                    return item
        diagnostic = PluginDiagnostic(
            plugin_id=str(plugin_id),
            stage=str(stage),
            reason=str(reason),
            severity=severity,
            status=status,
            details=dict(details or {}),
        )
        self._diagnostic_keys.add(key)
        self._diagnostics.append(diagnostic)
        record = self._records.get(str(plugin_id))
        if record is not None:
            record.diagnostics.append(diagnostic)
        return diagnostic

    def discover(self) -> list[PluginRecord]:
        if self._discovered:
            return list(self._records.values())
        # 允许在首次 discover() 后继续注册官方 factory，再次 discover() 时只处理
        # 尚未处理的定义；已运行的 PluginRecord 不会被重建或丢失。
        seen: set[str] = set(self._records)
        pending = self._definitions[self._processed_definition_count :]
        for raw_manifest, factory, enabled_override in pending:
            plugin_id = str(raw_manifest.get("id", "<unknown>")) if isinstance(raw_manifest, dict) else getattr(raw_manifest, "id", "<unknown>")
            if plugin_id in seen or plugin_id in self._records:
                self._add_diagnostic(plugin_id, "discovery", "duplicate plugin id")
                continue
            seen.add(plugin_id)
            try:
                manifest = raw_manifest if isinstance(raw_manifest, PluginManifest) else PluginManifest.from_dict(raw_manifest)
                manifest.validate()
            except (PluginManifestError, TypeError, ValueError) as exc:
                self._add_diagnostic(plugin_id, "manifest", str(exc))
                continue
            if manifest.api_version != self.api_version:
                self._add_diagnostic(
                    manifest.id,
                    "compatibility",
                    f"plugin API {manifest.api_version!r} is incompatible with Core API {self.api_version!r}",
                )
                continue
            if not version_satisfies(self.core_version, manifest.core_requires):
                self._add_diagnostic(
                    manifest.id,
                    "compatibility",
                    f"Core {self.core_version} does not satisfy {manifest.core_requires}",
                )
                continue
            enabled = manifest.default_enabled if enabled_override is None else bool(enabled_override)
            self._records[manifest.id] = PluginRecord(manifest=manifest, factory=factory, enabled=enabled)
        self._processed_definition_count = len(self._definitions)
        self._discovered = True
        return list(self._records.values())

    def diagnostics(self) -> list[PluginDiagnostic]:
        return list(self._diagnostics)

    def get(self, plugin_id: str) -> PluginRecord | None:
        self.discover()
        return self._records.get(plugin_id)

    def get_instance(self, plugin_id: str) -> Any | None:
        record = self.get(plugin_id)
        return record.instance if record is not None else None

    def _enable_recursive(self, plugin_id: str, stack: tuple[str, ...]) -> bool:
        record = self._records.get(plugin_id)
        if record is None:
            self._add_diagnostic(plugin_id, "dependency", "plugin dependency is missing")
            return False
        if plugin_id in stack:
            cycle = " -> ".join((*stack, plugin_id))
            self._add_diagnostic(plugin_id, "dependency", f"dependency cycle: {cycle}")
            record.state = "fault"
            return False
        if record.state == "fault" and record.instance is None:
            return False
        record.enabled = True
        ok = True
        for dependency in record.manifest.dependencies:
            ok = self._enable_recursive(dependency, (*stack, plugin_id)) and ok
        if not ok:
            record.state = "fault"
        return ok

    def enable(self, plugin_id: str) -> None:
        self.discover()
        self._enable_recursive(plugin_id, ())
        record = self._records.get(plugin_id)
        if record is not None and record.state != "fault" and record.instance is None:
            record.state = "enabled"

    def disable(self, plugin_id: str) -> None:
        self.discover()
        record = self._records.get(plugin_id)
        if record is None:
            return
        if record.instance is not None:
            self._stop_record(record)
        record.enabled = False
        if record.state != "fault":
            record.state = "disabled"

    def ensure_instance(self, plugin_id: str) -> Any | None:
        self.discover()
        record = self._records.get(plugin_id)
        if record is None or record.state == "fault":
            return None
        if record.instance is not None:
            return record.instance
        context = None
        try:
            capabilities = CapabilitySet(record.manifest.capabilities)
            context = PluginContext(
                plugin_id=record.manifest.id,
                core_version=self.core_version,
                api_version=self.api_version,
                config=PluginConfigStore(
                    self.config,
                    record.manifest.id,
                    legacy_map=record.manifest.legacy_config_map,
                    capabilities=capabilities,
                )
                if self.config is not None
                else PluginConfigStore(
                    _MemoryConfig(),
                    record.manifest.id,
                    legacy_map=record.manifest.legacy_config_map,
                    capabilities=capabilities,
                ),
                events=self.events,
                presentation=self.presentation,
                scheduler=self.scheduler,
                commands=self.commands,
                content=self.content,
                capabilities=capabilities,
                logger=StructuredLogger(record.manifest.id),
            )
            instance = record.factory(context)
            if instance is None:
                raise RuntimeError("plugin factory returned None")
            record.context = context
            record.instance = instance
            if record.state not in {"running", "fault"}:
                record.state = "prepared"
            return instance
        except Exception as exc:
            self._add_diagnostic(plugin_id, "factory", str(exc))
            record.state = "fault"
            if context is not None:
                context.dispose()
            record.context = None
            record.instance = None
            return None

    def _dependency_order(self, *, include_disabled: bool = False) -> list[PluginRecord]:
        self.discover()
        records = {plugin_id: record for plugin_id, record in self._records.items() if (include_disabled or record.enabled) and record.state != "fault"}
        visiting: set[str] = set()
        visited: set[str] = set()
        result: list[PluginRecord] = []

        def visit(plugin_id: str, path: tuple[str, ...]) -> bool:
            if plugin_id in visited:
                return True
            if plugin_id in visiting:
                cycle = " -> ".join((*path, plugin_id))
                self._add_diagnostic(plugin_id, "dependency", f"dependency cycle: {cycle}")
                record = self._records.get(plugin_id)
                if record is not None:
                    record.state = "fault"
                return False
            record = records.get(plugin_id)
            if record is None:
                self._add_diagnostic(plugin_id, "dependency", "enabled dependency is missing")
                return False
            visiting.add(plugin_id)
            valid = True
            for dependency in record.manifest.dependencies:
                if dependency not in self._records:
                    self._add_diagnostic(plugin_id, "dependency", f"missing dependency: {dependency}")
                    valid = False
                    continue
                dependency_record = self._records[dependency]
                if not dependency_record.enabled and not include_disabled:
                    dependency_record.enabled = True
                    records[dependency] = dependency_record
                if not visit(dependency, (*path, plugin_id)):
                    valid = False
            visiting.discard(plugin_id)
            if valid:
                visited.add(plugin_id)
                result.append(record)
            else:
                record.state = "fault"
            return valid

        for plugin_id in tuple(records):
            visit(plugin_id, ())
        return result

    def _publish_lifecycle(self, record: PluginRecord, state: str) -> None:
        try:
            self.events.publish(
                CoreEvent.now(
                    "plugin.lifecycle.changed",
                    "core.plugins",
                    {"plugin_id": record.manifest.id, "state": state, "version": record.manifest.version},
                )
            )
        except Exception:
            _LOG.exception("publishing plugin lifecycle event failed plugin_id=%s", record.manifest.id)

    def _start_record(self, record: PluginRecord) -> None:
        if record.state == "running":
            return
        for dependency_id in record.manifest.dependencies:
            dependency = self._records.get(dependency_id)
            if dependency is None or dependency.state != "running":
                state = dependency.state if dependency is not None else "missing"
                self._add_diagnostic(
                    record.manifest.id,
                    "dependency",
                    f"dependency {dependency_id!r} is not running ({state})",
                    details={"dependency": dependency_id, "dependency_state": state},
                )
                self._fault_record(record)
                return
        instance = self.ensure_instance(record.manifest.id)
        if instance is None:
            return
        try:
            starter = getattr(instance, "start", None)
            if callable(starter):
                starter()
            record.state = "running"
            self._publish_lifecycle(record, "running")
        except Exception as exc:
            self._add_diagnostic(record.manifest.id, "start", str(exc))
            self._fault_record(record)

    def _dependency_ids(self, plugin_id: str) -> set[str]:
        """返回目标插件及其传递依赖，不包含反向依赖。"""
        required: set[str] = set()
        visiting: set[str] = set()

        def visit(current: str) -> None:
            if current in required or current in visiting:
                return
            record = self._records.get(current)
            if record is None:
                return
            visiting.add(current)
            required.add(current)
            for dependency in record.manifest.dependencies:
                visit(dependency)
            visiting.discard(current)

        visit(plugin_id)
        return required

    def start(self, plugin_id: str) -> None:
        self.enable(plugin_id)
        required = self._dependency_ids(plugin_id)
        for record in self._dependency_order():
            if record.manifest.id in required:
                self._start_record(record)

    def start_all(self) -> None:
        for record in self._dependency_order():
            self._start_record(record)

    def _stop_record(self, record: PluginRecord) -> None:
        instance = record.instance
        context = record.context
        if instance is None and context is None:
            return
        try:
            stopper = getattr(instance, "stop", None) if instance is not None else None
            if callable(stopper):
                stopper()
        except Exception as exc:
            self._add_diagnostic(record.manifest.id, "stop", str(exc), severity="warn")
        finally:
            if context is not None:
                context.dispose()
            record.instance = None
            record.context = None
            if record.state != "fault":
                record.state = "stopped" if record.enabled else "disabled"
            self._publish_lifecycle(record, record.state)

    def stop(self, plugin_id: str) -> None:
        self.discover()
        record = self._records.get(plugin_id)
        if record is not None:
            self._stop_record(record)

    def stop_all(self) -> None:
        records = self._dependency_order(include_disabled=True)
        known = {record.manifest.id for record in records}
        records.extend(record for record in self._records.values() if record.instance is not None and record.manifest.id not in known)
        for record in reversed(records):
            if record.instance is not None or record.context is not None:
                self._stop_record(record)

    def apply_config(self, plugin_id: str) -> None:
        instance = self.get_instance(plugin_id)
        if instance is None:
            return
        method = getattr(instance, "apply_config", None)
        if callable(method):
            try:
                method()
            except Exception as exc:
                record = self._records.get(plugin_id)
                if record is not None:
                    self._add_diagnostic(plugin_id, "config", str(exc))
                    self._fault_record(record)

    def publish_event(self, event_type: str, *, source: str = "core", payload: dict[str, Any] | None = None) -> None:
        self.events.publish(CoreEvent.now(event_type, source, payload))

    def _fault_record(self, record: PluginRecord) -> None:
        record.state = "fault"
        instance = record.instance
        context = record.context
        try:
            stopper = getattr(instance, "stop", None) if instance is not None else None
            if callable(stopper):
                stopper()
        except Exception:
            _LOG.exception("stopping faulted plugin failed plugin_id=%s", record.manifest.id)
        finally:
            if context is not None:
                context.dispose()
            record.instance = None
            record.context = None
            self._publish_lifecycle(record, "fault")

    def _handle_event_error(self, subscription: Subscription, event: CoreEvent, exc: Exception) -> None:
        self._add_diagnostic(
            subscription.owner,
            "event",
            f"callback for {event.type} failed: {exc}",
            details={"event_type": event.type},
        )
        record = self._records.get(subscription.owner)
        if record is not None and not self._publishing_error:
            self._fault_record(record)

    def _handle_scheduler_error(self, owner: str, exc: Exception) -> None:
        self._add_diagnostic(owner, "scheduler", str(exc))
        record = self._records.get(owner)
        if record is not None:
            self._fault_record(record)


class _MemoryConfig:
    """没有宿主 Config 时给独立 runtime 单测使用的最小适配器。"""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def save(self) -> bool:
        return True
