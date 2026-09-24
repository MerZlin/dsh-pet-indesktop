"""Phase 2 Core 插件服务端口。

这些对象是插件可以看到的最小能力面。Qt 只在 SchedulerPort 实际创建定时器时
惰性导入，便于无 GUI 的 runtime 单元测试。
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any, Callable

from .capabilities import CapabilitySet


class CommandConflict(RuntimeError):
    """命令 ID 已被其他 owner 注册。"""


class CommandNotFound(KeyError):
    """找不到命令。"""


class StructuredLogger:
    """带 plugin_id 字段的轻量日志门面。"""

    def __init__(self, plugin_id: str = "core", logger: logging.Logger | None = None) -> None:
        self.plugin_id = plugin_id
        self._logger = logger or logging.getLogger(f"dsh-pet.plugin.{plugin_id}")

    def _extra(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {"plugin_id": self.plugin_id}
        if extra:
            result.update(extra)
        return result

    def debug(self, message: str, *args, extra: dict[str, Any] | None = None, **kwargs) -> None:
        self._logger.debug(message, *args, extra=self._extra(extra), **kwargs)

    def info(self, message: str, *args, extra: dict[str, Any] | None = None, **kwargs) -> None:
        self._logger.info(message, *args, extra=self._extra(extra), **kwargs)

    def warning(self, message: str, *args, extra: dict[str, Any] | None = None, **kwargs) -> None:
        self._logger.warning(message, *args, extra=self._extra(extra), **kwargs)

    def error(self, message: str, *args, extra: dict[str, Any] | None = None, **kwargs) -> None:
        self._logger.error(message, *args, extra=self._extra(extra), **kwargs)

    def exception(self, message: str, *args, extra: dict[str, Any] | None = None, **kwargs) -> None:
        self._logger.exception(message, *args, extra=self._extra(extra), **kwargs)


class PresentationPort:
    """Core 提供的展示端口；实现由 AppShell 注入。"""

    def __init__(
        self,
        *,
        show_bubble: Callable[..., Any] | None = None,
        notify: Callable[..., Any] | None = None,
        speak: Callable[..., Any] | None = None,
    ) -> None:
        self._show_bubble = show_bubble
        self._notify = notify
        self._speak = speak

    def show_bubble(
        self,
        text: str,
        *,
        duration_ms: int = 12_000,
        subtitle: str = "",
        pet_scale: float | None = None,
    ) -> bool:
        if self._show_bubble is None:
            return False
        return bool(
            self._show_bubble(
                str(text),
                duration_ms=int(duration_ms),
                subtitle=str(subtitle or ""),
                pet_scale=pet_scale,
            )
        )

    def notify(
        self,
        title: str,
        message: str,
        *,
        on_click=None,
        duration_ms: int = 5000,
    ) -> bool:
        if self._notify is None:
            return False
        return bool(
            self._notify(
                str(title),
                str(message),
                on_click=on_click,
                duration_ms=int(duration_ms),
            )
        )

    def speak(self, text: str, *, log_tag: str = "插件") -> bool:
        if self._speak is None:
            return False
        return bool(self._speak(str(text), log_tag=str(log_tag)))


class ScopedPresentationPort:
    def __init__(self, port: PresentationPort, capabilities: CapabilitySet) -> None:
        self._port = port
        self._capabilities = capabilities

    def show_bubble(self, text: str, **kwargs) -> bool:
        self._capabilities.require("notification.present")
        return self._port.show_bubble(text, **kwargs)

    def notify(self, title: str, message: str, **kwargs) -> bool:
        self._capabilities.require("notification.present")
        return self._port.notify(title, message, **kwargs)

    def speak(self, text: str, **kwargs) -> bool:
        self._capabilities.require("speech.present")
        return self._port.speak(text, **kwargs)


@dataclass
class TimerHandle:
    token: int
    owner: str
    timer: Any
    repeating: bool
    _scheduler: "SchedulerPort"
    active: bool = True

    def cancel(self) -> None:
        self._scheduler._cancel(self)

    def is_active(self) -> bool:
        if not self.active:
            return False
        try:
            return bool(self.timer.isActive())
        except RuntimeError:
            return False


class SchedulerPort:
    """GUI 线程 QTimer 适配器，所有任务都带 owner 且可批量清理。"""

    def __init__(self, *, on_error: Callable[[str, Exception], Any] | None = None) -> None:
        self._tokens = itertools.count(1)
        self._handles: dict[int, TimerHandle] = {}
        self._on_error = on_error

    def set_error_handler(self, handler: Callable[[str, Exception], Any] | None) -> None:
        self._on_error = handler

    def _create(self, callback: Callable[[], Any], delay_ms: int, owner: str, repeating: bool) -> TimerHandle:
        if not callable(callback):
            raise TypeError("scheduled callback must be callable")
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("scheduler owner must be a non-empty string")
        from PySide6.QtCore import QTimer

        timer = QTimer()
        timer.setInterval(max(0, int(delay_ms)))
        timer.setSingleShot(not repeating)
        token = next(self._tokens)
        handle = TimerHandle(token, owner, timer, repeating, self)
        self._handles[token] = handle

        def fire() -> None:
            if not handle.active:
                return
            try:
                callback()
            except Exception as exc:
                handler = self._on_error
                if handler is not None:
                    try:
                        handler(owner, exc)
                    except Exception:
                        logging.getLogger(__name__).exception("scheduler error handler failed owner=%s", owner)
                else:
                    logging.getLogger(__name__).exception("scheduled plugin callback failed owner=%s", owner)
                if repeating:
                    handle.cancel()
            finally:
                if not repeating:
                    self._remove(handle)

        timer.timeout.connect(fire)
        timer.start()
        return handle

    def call_later(self, callback: Callable[[], Any], delay_ms: int, *, owner: str = "core") -> TimerHandle:
        return self._create(callback, delay_ms, owner, False)

    def call_repeating(self, callback: Callable[[], Any], interval_ms: int, *, owner: str = "core") -> TimerHandle:
        return self._create(callback, interval_ms, owner, True)

    def _remove(self, handle: TimerHandle) -> None:
        self._handles.pop(handle.token, None)
        handle.active = False
        try:
            handle.timer.stop()
            handle.timer.deleteLater()
        except RuntimeError:
            pass

    def _cancel(self, handle: TimerHandle) -> None:
        if handle.token in self._handles:
            self._remove(handle)

    def cancel_owner(self, owner: str) -> None:
        for handle in tuple(self._handles.values()):
            if handle.owner == owner:
                handle.cancel()

    def cancel_all(self) -> None:
        for handle in tuple(self._handles.values()):
            handle.cancel()

    def timer_count(self, owner: str | None = None) -> int:
        if owner is None:
            return sum(1 for handle in self._handles.values() if handle.active)
        return sum(1 for handle in self._handles.values() if handle.active and handle.owner == owner)


class ScopedSchedulerPort:
    def __init__(self, scheduler: SchedulerPort, owner: str, capabilities: CapabilitySet) -> None:
        self._scheduler = scheduler
        self._owner = owner
        self._capabilities = capabilities

    def call_later(self, callback: Callable[[], Any], delay_ms: int) -> TimerHandle:
        self._capabilities.require("scheduler.timer")
        return self._scheduler.call_later(callback, delay_ms, owner=self._owner)

    def call_repeating(self, callback: Callable[[], Any], interval_ms: int) -> TimerHandle:
        self._capabilities.require("scheduler.timer")
        return self._scheduler.call_repeating(callback, interval_ms, owner=self._owner)

    def cancel_all(self) -> None:
        self._scheduler.cancel_owner(self._owner)

    def timer_count(self) -> int:
        return self._scheduler.timer_count(self._owner)


@dataclass(frozen=True)
class CommandHandle:
    name: str
    owner: str
    registry: "CommandRegistry"

    def unregister(self) -> None:
        self.registry.unregister(self.name, owner=self.owner)


class CommandRegistry:
    """Core 命令表；命令属于 owner，停止插件时统一注销。"""

    def __init__(self) -> None:
        self._commands: dict[str, tuple[str, Callable[..., Any]]] = {}

    def register(self, name: str, callback: Callable[..., Any], *, owner: str, replace: bool = False) -> CommandHandle:
        if not name or not isinstance(name, str):
            raise ValueError("command name must be a non-empty string")
        if not callable(callback):
            raise TypeError("command callback must be callable")
        if name in self._commands and not replace:
            raise CommandConflict(f"command already registered: {name}")
        self._commands[name] = (owner, callback)
        return CommandHandle(name, owner, self)

    def unregister(self, name: str, *, owner: str | None = None) -> None:
        current = self._commands.get(name)
        if current is None:
            return
        if owner is not None and current[0] != owner:
            return
        self._commands.pop(name, None)

    def unregister_owner(self, owner: str) -> None:
        for name, (current_owner, _) in tuple(self._commands.items()):
            if current_owner == owner:
                self._commands.pop(name, None)

    def invoke(self, name: str, *args, **kwargs) -> Any:
        current = self._commands.get(name)
        if current is None:
            raise CommandNotFound(name)
        return current[1](*args, **kwargs)

    def list(self, *, owner: str | None = None) -> tuple[str, ...]:
        if owner is None:
            return tuple(sorted(self._commands))
        return tuple(sorted(name for name, (current_owner, _) in self._commands.items() if current_owner == owner))


class ScopedCommandRegistry:
    def __init__(self, registry: CommandRegistry, owner: str, capabilities: CapabilitySet) -> None:
        self._registry = registry
        self._owner = owner
        self._capabilities = capabilities

    def _name(self, name: str) -> str:
        return f"{self._owner}.{name.lstrip('.')}"

    def register(self, name: str, callback: Callable[..., Any]) -> CommandHandle:
        self._capabilities.require("menu.contribute")
        return self._registry.register(self._name(name), callback, owner=self._owner)

    def unregister(self, name: str) -> None:
        self._registry.unregister(self._name(name), owner=self._owner)

    def invoke(self, name: str, *args, **kwargs) -> Any:
        return self._registry.invoke(self._name(name), *args, **kwargs)


class ContentProviderRegistry:
    """Phase 1 CharacterRegistry 的只读 provider 门面。"""

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider
        self._providers: dict[str, Any] = {}
        if provider is not None:
            self._providers["core.content"] = provider

    def register(self, provider: Any, *, owner: str = "core") -> None:
        self._providers[str(owner)] = provider
        if self._provider is None:
            self._provider = provider

    def list(self):
        provider = self._provider
        if provider is None:
            return []
        method = getattr(provider, "list_available", None) or getattr(provider, "scan", None)
        return list(method() if callable(method) else ())

    def list_available(self):
        return self.list()

    def resolve(self, character_id: str):
        provider = self._provider
        method = getattr(provider, "resolve", None) if provider is not None else None
        return method(character_id) if callable(method) else None


class ScopedContentProviderRegistry:
    def __init__(self, registry: ContentProviderRegistry) -> None:
        self._registry = registry

    def list(self):
        return self._registry.list()

    def list_available(self):
        return self._registry.list_available()

    def resolve(self, character_id: str):
        return self._registry.resolve(character_id)
