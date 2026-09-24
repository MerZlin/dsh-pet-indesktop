"""Core 插件事件总线。

Phase 2 的事件总线刻意保持为纯 Python 同步实现：事件只在 GUI 线程内派发，
不暴露 Qt signal、QApplication 或 AppShell。跨线程/跨进程事件留给后续 worker
阶段。
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoreEvent:
    """Core 与官方插件之间传递的稳定事件对象。"""

    type: str
    source: str
    timestamp: datetime
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type.strip():
            raise ValueError("event type must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("event source must be a non-empty string")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("event timestamp must be datetime")
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=timezone.utc))
        if not isinstance(self.payload, dict):
            raise TypeError("event payload must be a dict")
        try:
            json.dumps(self.payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError("event payload must be JSON serializable") from exc

    @classmethod
    def now(cls, event_type: str, source: str, payload: dict[str, Any] | None = None) -> "CoreEvent":
        return cls(
            type=event_type,
            source=source,
            timestamp=datetime.now(timezone.utc),
            payload=dict(payload or {}),
        )


@dataclass(frozen=True)
class Subscription:
    token: int
    event_type: str
    owner: str
    callback: Callable[[CoreEvent], Any]


class CoreEventBus:
    """同步、可诊断、按 owner 清理的 GUI 线程事件总线。"""

    def __init__(self) -> None:
        self._tokens = itertools.count(1)
        self._subscriptions: dict[int, Subscription] = {}
        self._error_handler: Callable[[Subscription, CoreEvent, Exception], Any] | None = None

    def set_error_handler(
        self,
        handler: Callable[[Subscription, CoreEvent, Exception], Any] | None,
    ) -> None:
        self._error_handler = handler

    def subscribe(
        self,
        event_type: str,
        callback: Callable[[CoreEvent], Any],
        *,
        owner: str,
    ) -> Subscription:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event type must be a non-empty string")
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("event owner must be a non-empty string")
        if not callable(callback):
            raise TypeError("event callback must be callable")
        subscription = Subscription(next(self._tokens), event_type, owner, callback)
        self._subscriptions[subscription.token] = subscription
        return subscription

    def unsubscribe(self, subscription: Subscription | int) -> None:
        token = subscription if isinstance(subscription, int) else subscription.token
        self._subscriptions.pop(token, None)

    def clear_owner(self, owner: str) -> None:
        for subscription in tuple(self._subscriptions.values()):
            if subscription.owner == owner:
                self._subscriptions.pop(subscription.token, None)

    def subscriptions(self, *, owner: str | None = None) -> tuple[Subscription, ...]:
        values = tuple(self._subscriptions.values())
        if owner is None:
            return values
        return tuple(item for item in values if item.owner == owner)

    def publish(self, event: CoreEvent) -> None:
        if not isinstance(event, CoreEvent):
            raise TypeError("publish expects CoreEvent")
        # 快照保证回调中订阅/取消订阅不会改变本次派发顺序。
        targets = tuple(self._subscriptions.values())
        for subscription in targets:
            if subscription.event_type not in {event.type, "*"}:
                continue
            if self._subscriptions.get(subscription.token) is None:
                continue
            try:
                subscription.callback(event)
            except Exception as exc:  # 插件回调不能冒泡到 Qt 主循环
                self._subscriptions.pop(subscription.token, None)
                handler = self._error_handler
                if handler is None:
                    _LOG.exception(
                        "插件事件回调失败 owner=%s event=%s",
                        subscription.owner,
                        event.type,
                    )
                    continue
                try:
                    handler(subscription, event, exc)
                except Exception:
                    _LOG.exception("处理插件事件回调错误失败 owner=%s", subscription.owner)


class ScopedEventBus:
    """绑定单个插件 owner 的事件门面。"""

    def __init__(self, bus: CoreEventBus, owner: str) -> None:
        self._bus = bus
        self._owner = owner

    def subscribe(self, event_type: str, callback: Callable[[CoreEvent], Any]) -> Subscription:
        return self._bus.subscribe(event_type, callback, owner=self._owner)

    def unsubscribe(self, subscription: Subscription | int) -> None:
        self._bus.unsubscribe(subscription)

    def publish(self, event: CoreEvent) -> None:
        self._bus.publish(event)

    def clear(self) -> None:
        self._bus.clear_owner(self._owner)
