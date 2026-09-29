"""Owner-bound UI contributions. Descriptors are metadata; handles are revocable leases.

Only explicit official factories may reach this registry (no entrypoint imports).
All writes, factory calls and actions execute on the creating/GUI thread.
"""

from __future__ import annotations

import itertools
import logging
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, Literal

from .capabilities import CapabilitySet
from .ports import CommandHandle, CommandNotFound, CommandRegistry

_LOG = logging.getLogger(__name__)
Kind = Literal["menu", "settings", "shortcut"]


@dataclass(frozen=True)
class Contribution:
    id: str
    kind: Kind
    command: str = ""
    group: str = ""
    order: int = 0
    platforms: tuple[str, ...] = ()
    factory: Callable[..., Any] | None = None


@dataclass(frozen=True)
class ContributionHandle:
    owner: str
    scope: str
    descriptor: Contribution
    registration: int
    registry: ContributionRegistry
    command: CommandHandle | None = None

    @property
    def active(self) -> bool:
        return self.registry.current(self)

    def revoke(self) -> None:
        self.registry.revoke(self)

    def invoke(self, *args, **kwargs) -> Any:
        self.registry.check_thread()
        if not self.active or self.command is None:
            raise CommandNotFound(self.descriptor.id)
        return self.command.invoke(*args, **kwargs)

    def create(self, *args, **kwargs) -> Any:
        self.registry.check_thread()
        if not self.active or self.descriptor.factory is None:
            raise CommandNotFound(self.descriptor.id)
        try:
            return self.descriptor.factory(*args, **kwargs)
        except Exception:
            # A failed factory invalidates its batch, never another owner's UI.
            self.registry.reject_batch(self)
            raise


@dataclass
class ContributionBatch:
    handles: tuple[ContributionHandle, ...]
    commands: tuple[CommandHandle, ...]
    registry: ContributionRegistry
    owner: str
    scope: str

    def dispose(self) -> None:
        self.registry.dispose_batch(self)


class ContributionRegistry:
    def __init__(self, commands: CommandRegistry) -> None:
        self.commands = commands
        self._thread = threading.get_ident()
        self._serial = itertools.count(1)
        self._entries: dict[tuple[str, str, str], ContributionHandle] = {}
        self._batches: dict[int, ContributionBatch] = {}
        self._live_batches: dict[int, ContributionBatch] = {}
        self._listeners: list[Callable[[], None]] = []

    def check_thread(self) -> None:
        if threading.get_ident() != self._thread:
            raise RuntimeError("contribution operation must run on its owning GUI thread")

    def bind(self, owner: str, scope: str, capabilities: CapabilitySet) -> ContributionPort:
        self.check_thread()
        if not owner or not scope:
            raise ValueError("owner and scope are required")
        return ContributionPort(self, owner, scope, capabilities)

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        self.check_thread()
        self._listeners.append(callback)

        def unsubscribe() -> None:
            self.check_thread()
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _changed(self) -> None:
        for callback in tuple(self._listeners):
            try:
                callback()
            except Exception:
                _LOG.exception("contribution observer failed")

    def current(self, handle: ContributionHandle) -> bool:
        return self._entries.get((handle.owner, handle.scope, handle.descriptor.id)) is handle

    def list(self, *, owner: str | None = None, scope: str | None = None, kind: Kind | None = None) -> tuple[ContributionHandle, ...]:
        return tuple(
            sorted(
                (
                    h
                    for h in self._entries.values()
                    if (owner is None or h.owner == owner) and (scope is None or h.scope == scope) and (kind is None or h.descriptor.kind == kind)
                ),
                key=lambda h: (h.descriptor.order, h.descriptor.id),
            )
        )

    def revoke(self, handle: ContributionHandle) -> None:
        self.check_thread()
        if self.current(handle):
            self.reject_batch(handle)

    def dispose_batch(self, batch: ContributionBatch) -> None:
        self.check_thread()
        if self._live_batches.pop(id(batch), None) is None:
            return
        for handle in batch.handles:
            if self.current(handle):
                del self._entries[(handle.owner, handle.scope, handle.descriptor.id)]
            self._batches.pop(handle.registration, None)
        for command in batch.commands:
            command.unregister()
        self._changed()

    def reject_batch(self, handle: ContributionHandle) -> None:
        self.check_thread()
        batch = self._batches.get(handle.registration)
        if batch is not None:
            batch.dispose()

    def revoke_owner(self, owner: str, scope: str | None = None) -> None:
        self.check_thread()
        for batch in tuple(self._live_batches.values()):
            if batch.owner == owner and (scope is None or batch.scope == scope):
                batch.dispose()


class ContributionPort:
    """Owner cannot be supplied by the feature in a descriptor or register call."""

    def __init__(self, registry: ContributionRegistry, owner: str, scope: str, capabilities: CapabilitySet) -> None:
        self._registry, self._owner, self._scope, self._capabilities = registry, owner, scope, capabilities

    def register(self, descriptors: list[Contribution], *, commands: dict[str, Callable[..., Any]] | None = None) -> ContributionBatch:
        registry = self._registry
        registry.check_thread()
        commands = commands or {}
        if commands:
            self._capabilities.require("menu.contribute")
        if any(not isinstance(name, str) or not name or not callable(callback) for name, callback in commands.items()):
            raise ValueError("invalid command")
        names: set[str] = set()
        for item in descriptors:
            if item.kind not in ("menu", "settings", "shortcut") or not item.id or item.id in names:
                raise ValueError("invalid or duplicate contribution")
            self._capabilities.require("settings.contribute" if item.kind == "settings" else "menu.contribute")
            names.add(item.id)
            if (self._owner, self._scope, item.id) in registry._entries:
                raise ValueError(f"contribution already registered: {item.id}")
            if item.kind == "settings" and not callable(item.factory):
                raise ValueError("settings contribution requires an official factory")
            if item.kind != "settings" and item.command not in commands:
                raise ValueError("menu/shortcut requires a command in this batch")
        active = [item for item in descriptors if not item.platforms or sys.platform in item.platforms]
        used_commands = {item.command for item in active}
        registration = next(registry._serial)
        registered: dict[str, CommandHandle] = {}
        handles: list[ContributionHandle] = []
        try:
            for name, callback in commands.items():
                if descriptors and name not in used_commands:
                    continue
                registered[name] = registry.commands.register(f"{self._owner}:{self._scope}:{registration}:{name}", callback, owner=self._owner)
            for item in descriptors:
                if item.platforms and sys.platform not in item.platforms:
                    continue
                handles.append(ContributionHandle(self._owner, self._scope, item, next(registry._serial), registry, registered.get(item.command)))
        except Exception:
            for command in registered.values():
                command.unregister()
            raise
        batch = ContributionBatch(tuple(handles), tuple(registered.values()), registry, self._owner, self._scope)
        registry._live_batches[id(batch)] = batch
        for handle in handles:
            registry._entries[(handle.owner, handle.scope, handle.descriptor.id)] = handle
            registry._batches[handle.registration] = batch
        registry._changed()
        return batch
