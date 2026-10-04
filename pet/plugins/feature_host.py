"""Explicit official feature contributions; in-memory authority, not an installer.

The host owns state and leases, never feature UI objects or persisted settings.
A settings-only process can register factories without attaching runtime commands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Callable

from .capabilities import CapabilitySet
from .contributions import Contribution, ContributionBatch, ContributionHandle, ContributionRegistry
from .ports import CommandRegistry

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureDefinition:
    owner: str
    menus: tuple[Contribution, ...]
    settings_factory: Callable[..., Any]
    runtime_factory: Callable[..., Any] | None = None
    manual_factory: Callable[..., Any] | None = None
    policy_factory: Callable[..., dict] | None = None
    worker_launch_factory: Callable | None = None
    allow_in_process: bool = True


class FeatureHost:
    def __init__(self, registry: ContributionRegistry | None = None):
        self.registry = registry or ContributionRegistry(CommandRegistry())
        self._definitions: dict[str, FeatureDefinition] = {}
        self._states: dict[str, str] = {}
        self._bindings: dict[tuple[str, str], tuple[dict[str, Callable], Callable]] = {}
        self._menus: dict[tuple[str, str], ContributionBatch] = {}
        self._settings: dict[tuple[str, str], ContributionHandle] = {}
        self._listeners: list[Callable[[str, str], None]] = []
        self._prepare: dict[str, list[Callable[[], bool]]] = {}
        self._diagnostics: dict[str, str] = {}
        self._executions: dict[object, tuple[str, Callable, Callable]] = {}
        self._authorities: dict[str, Callable[[], bool]] = {}

    def state(self, owner: str) -> str:
        return self._states.get(owner, "absent")

    def enabled(self, owner: str) -> bool:
        if self.state(owner) != "enabled":
            return False
        check = self._authorities.get(owner)
        try:
            return bool(check()) if check is not None else True
        except Exception:
            return False

    def bind_authority(self, owner: str, check: Callable[[], bool]) -> None:
        """Fail closed at every execution seam, before watcher delivery."""
        self.registry.check_thread()
        self._authorities[owner] = check

    def configurable(self, owner: str) -> bool:
        return self.state(owner) in ("enabled", "disabled")

    def owners(self) -> tuple[str, ...]:
        return tuple(o for o in self._definitions if self.configurable(o))

    def diagnostics(self) -> dict[str, str]:
        return dict(self._diagnostics)

    def provide(self, definition: FeatureDefinition, *, enabled: bool = True) -> None:
        self.registry.check_thread()
        if definition.owner in self._definitions:
            raise ValueError("feature already provided")
        self._definitions[definition.owner] = definition
        self._states[definition.owner] = "enabled" if enabled else "disabled"

    def bind_context(self, context):
        """Only a provided owner's immutable ports can reach its factories."""
        from ..feature_ports import FeatureHostContext

        self.registry.check_thread()
        if not isinstance(context, FeatureHostContext) or not self.configurable(context.owner):
            raise PermissionError("feature is not available")
        definition = self._definitions[context.owner]
        return replace(context, worker_launch_factory=definition.worker_launch_factory, allow_in_process=definition.allow_in_process)

    def runtime(self, context, *, worker_mode="auto"):
        context = self.bind_context(context)
        if not self.enabled(context.owner):
            return None
        factory = self._definitions[context.owner].runtime_factory
        return factory(context, worker_mode=worker_mode) if factory else None

    def manual(self, context, request, cancel):
        context = self.bind_context(context)
        if not self.enabled(context.owner):
            return None
        factory = self._definitions[context.owner].manual_factory
        return factory(context, request, cancel) if factory else None

    def policy(self, owner, values):
        if not self.configurable(owner):
            return {}
        factory = self._definitions[owner].policy_factory
        return factory(values) if factory else {}

    def subscribe(self, callback: Callable[[str, str], None]) -> Callable[[], None]:
        self.registry.check_thread()
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def before_remove(self, owner: str, callback: Callable[[], bool]) -> Callable[[], None]:
        self.registry.check_thread()
        callbacks = self._prepare.setdefault(owner, [])
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    def _notify(self, owner: str) -> None:
        for callback in tuple(self._listeners):
            try:
                callback(owner, self.state(owner))
            except Exception:
                _LOG.exception("feature contribution observer failed")

    def bind_execution(self, owner: str, stop: Callable, resume: Callable) -> Callable[[], None]:
        """Host-owned runtime, independent of the number of window menus."""
        self.registry.check_thread()
        token = object()
        self._executions[token] = owner, stop, resume

        def release() -> None:
            self.registry.check_thread()
            self._executions.pop(token, None)

        return release

    def _execution_transition(self, owner: str, *, running: bool) -> None:
        for o, stop, resume in tuple(self._executions.values()):
            if o == owner:
                try:
                    (resume if running else stop)()
                except Exception:
                    _LOG.exception("feature execution transition failed")
                    self._diagnostics[owner] = "execution_transition_failed"

    def attach(self, owner: str, scope: str, commands: dict[str, Callable], stop: Callable[[], None]) -> None:
        self.registry.check_thread()
        key = owner, scope
        if key in self._bindings:
            return
        self._bindings[key] = commands, stop
        if self.enabled(owner):
            try:
                self._register_menu(owner, scope)
            except Exception:
                self._bindings.pop(key, None)
                raise

    def _register_menu(self, owner: str, scope: str) -> None:
        port = self.registry.bind(owner, scope, CapabilitySet(["menu.contribute"]))
        self._menus[owner, scope] = port.register(list(self._definitions[owner].menus), commands=self._bindings[owner, scope][0])

    def detach(self, owner: str, scope: str) -> None:
        self.registry.check_thread()
        batch = self._menus.pop((owner, scope), None)
        if batch:
            batch.dispose()
        self._bindings.pop((owner, scope), None)
        handle = self._settings.pop((owner, scope), None)
        if handle:
            handle.revoke()

    def menu(self, owner: str, scope: str, contribution_id: str) -> ContributionHandle | None:
        batch = self._menus.get((owner, scope))
        if not self.enabled(owner) or not batch:
            return None
        return next((h for h in batch.handles if h.descriptor.id == contribution_id and h.active), None)

    def settings(self, owner: str, scope: str) -> ContributionHandle | None:
        self.registry.check_thread()
        if not self.configurable(owner):
            return None
        key = owner, scope
        old = self._settings.get(key)
        if old and old.active:
            return old
        port = self.registry.bind(owner, scope, CapabilitySet(["settings.contribute"]))
        batch = port.register([Contribution("settings", "settings", group="automation", factory=self._definitions[owner].settings_factory)])
        self._settings[key] = batch.handles[0]
        return batch.handles[0]

    def disable(self, owner: str) -> None:
        self.registry.check_thread()
        if self.state(owner) != "enabled":
            return
        # Authority changes before callbacks; stale actions cannot race cleanup.
        self._states[owner] = "disabled"
        self._execution_transition(owner, running=False)
        for (o, scope), (_, stop) in tuple(self._bindings.items()):
            if o != owner:
                continue
            batch = self._menus.pop((o, scope), None)
            if batch:
                batch.dispose()
            try:
                stop()
            except Exception:
                _LOG.exception("feature stop failed")
        self._notify(owner)

    def enable(self, owner: str) -> None:
        self.registry.check_thread()
        if self.state(owner) != "disabled":
            return
        created = []
        try:
            for o, scope in self._bindings:
                if o == owner:
                    self._register_menu(o, scope)
                    created.append((o, scope))
        except Exception:
            for key in created:
                self._menus.pop(key).dispose()
            raise
        self._states[owner] = "enabled"
        self._execution_transition(owner, running=True)
        self._notify(owner)

    def remove(self, owner: str) -> bool:
        self.registry.check_thread()
        for prepare in tuple(self._prepare.get(owner, ())):
            if not prepare():
                return False
        self.disable(owner)
        self._states[owner] = "absent"
        self.registry.revoke_owner(owner)
        self._notify(owner)
        return True

    def fault(self, owner: str, reason: str) -> None:
        self.disable(owner)
        self._states[owner] = "fault"
        self._diagnostics[owner] = reason
        self.registry.revoke_owner(owner)
        self._notify(owner)
