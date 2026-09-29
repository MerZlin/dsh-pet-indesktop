"""GUI-thread, owner-bound routing for optional official feature services.

The host grants routes and service names. Receivers own business validation and
persistence; this module never imports feature or UI implementations.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceRoute:
    window_id: str
    instance_id: str
    character_id: str


@dataclass(frozen=True)
class ServiceRequest:
    owner: str
    route: ServiceRoute
    payload: dict[str, object]


@dataclass(frozen=True)
class ServiceResult:
    status: str


class ServiceHandle:
    def __init__(self, dispose: Callable[[], None]) -> None:
        self._dispose = dispose

    def dispose(self) -> None:
        self._dispose()


class ServiceClient:
    def __init__(self, call: Callable[[str, Mapping[str, object]], ServiceResult]) -> None:
        self._call = call
        self._disposed = False

    def call(self, service: str, payload: Mapping[str, object]) -> ServiceResult:
        if self._disposed:
            return ServiceResult("denied")
        return self._call(service, payload)

    def dispose(self) -> None:
        self._disposed = True


class ServiceRegistry:
    MAX_PAYLOAD_BYTES = 256 * 1024

    def __init__(self) -> None:
        self._thread = threading.get_ident()
        self._receivers: dict[tuple[str, ServiceRoute], tuple[str, str, Callable[[ServiceRequest], ServiceResult]]] = {}

    def _check_thread(self) -> None:
        if threading.get_ident() != self._thread:
            raise RuntimeError("service_thread_mismatch")

    def register(self, owner: str, service: str, route: ServiceRoute, handler: Callable[[ServiceRequest], ServiceResult]) -> ServiceHandle:
        self._check_thread()
        key = (service, route)
        if not owner or not service or key in self._receivers:
            raise ValueError("invalid_or_duplicate_service")
        identity = uuid.uuid4().hex
        self._receivers[key] = (owner, identity, handler)

        def dispose() -> None:
            self._check_thread()
            current = self._receivers.get(key)
            if current and current[1] == identity:
                del self._receivers[key]

        return ServiceHandle(dispose)

    def dispose_owner(self, owner: str) -> None:
        self._check_thread()
        for key, current in tuple(self._receivers.items()):
            if current[0] == owner:
                del self._receivers[key]

    def bind(self, owner: str, route: ServiceRoute, *, allowed: set[str], authorized: Callable[[], bool]) -> ServiceClient:
        self._check_thread()
        grants = frozenset(allowed)

        def call(service: str, payload: Mapping[str, object]) -> ServiceResult:
            self._check_thread()
            try:
                if not owner or service not in grants or not authorized():
                    return ServiceResult("denied")
                # Detach caller-owned data. Reject NaN, non-JSON objects and
                # oversized messages without logging possibly sensitive values.
                encoded = json.dumps(dict(payload), ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
                    return ServiceResult("invalid")
                detached = json.loads(encoded)
            except (TypeError, ValueError, OverflowError, RecursionError):
                return ServiceResult("invalid")
            except Exception:
                return ServiceResult("denied")
            receiver = self._receivers.get((service, route))
            if receiver is None:
                return ServiceResult("unavailable")
            try:
                result = receiver[2](ServiceRequest(owner, route, detached))
                return result if isinstance(result, ServiceResult) else ServiceResult("fault")
            except Exception:
                return ServiceResult("fault")

        return ServiceClient(call)
