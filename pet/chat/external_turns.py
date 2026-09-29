"""Chat-owned receiver for text-only, optional external analysis results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Protocol

from ..plugins.services import ServiceRequest, ServiceResult, ServiceRoute
from .models import ChatMessage, ChatSession
from .session_store import SessionStore

SERVICE_ID = "chat.external-turn/v1"
CHAT_OWNER = "official.ai-chat"
SCREEN_OWNER = "official.screen-understanding"
_FIELDS = frozenset({"result_id", "kind", "user_text", "reply"})


class ChatTarget(Protocol):
    store: SessionStore
    session: ChatSession
    character_id: str

    @property
    def service(self): ...

    def apply_external_turn(self, session: ChatSession) -> None: ...


class ExternalTurnReceiver:
    def __init__(self, config, route: ServiceRoute, target: Callable[[], ChatTarget | None]) -> None:
        self._config = config
        self.route = route
        self._target = target

    def receive(self, request: ServiceRequest) -> ServiceResult:
        if request.owner != SCREEN_OWNER or request.route != self.route:
            return ServiceResult("denied")
        payload = request.payload
        if (
            set(payload) != _FIELDS
            or any(not isinstance(value, str) for value in payload.values())
            or not 0 < len(str(payload["result_id"])) <= 160
            or payload["kind"] not in {"manual", "automatic"}
            or not str(payload["reply"]).strip()
            or len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 256 * 1024
        ):
            return ServiceResult("invalid")
        if not self._config.get("chat", {}).get("enabled", False):
            return ServiceResult("unavailable")
        if str(self._config.instance_id or "primary") != self.route.instance_id or str(self._config.get("character", "shenshen")) != self.route.character_id:
            return ServiceResult("stale")
        window = self._target()
        if window is not None:
            if window.character_id != self.route.character_id:
                return ServiceResult("stale")
            if window.service.busy:
                return ServiceResult("busy")
        settings = self._config.chat_settings()
        store = window.store if window is not None else SessionStore(self._config.dir, self._config.instance_id)
        identity = json.dumps([request.owner, self.route.instance_id, self.route.character_id, payload["result_id"]], ensure_ascii=False)
        receipt = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        messages = [
            ChatMessage("user", str(payload["user_text"]), message_id=f"external-{receipt}-user"),
            ChatMessage("assistant", str(payload["reply"]), message_id=f"external-{receipt}-assistant"),
        ]
        session, status = store.append_external_turn(
            self.route.character_id,
            settings.active_provider,
            settings.default_system_prompt,
            messages,
            window.session if window is not None else None,
        )
        if status == "accepted" and window is not None:
            window.apply_external_turn(session)
        return ServiceResult(status)
