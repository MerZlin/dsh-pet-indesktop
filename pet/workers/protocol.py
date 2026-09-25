# -*- coding: utf-8 -*-
"""Common JSON Lines protocol for isolated dsh-pet workers.

The protocol deliberately stays independent from Qt and the desktop-pet UI so the
same implementation can be used by the frozen executable and by source runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

PROTOCOL = "pet-worker/v1"
MAX_MESSAGE_BYTES = 64 * 1024
MESSAGE_TYPES = frozenset({"hello", "ready", "config_push", "event", "error", "heartbeat", "shutdown"})


class WorkerProtocolError(ValueError):
    """Raised when a JSONL worker message violates the wire contract."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_json(value: Any) -> Any:
    """Validate and return *value* without allowing custom encoders."""
    try:
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkerProtocolError(f"payload is not JSON serializable: {exc}") from exc
    return value


@dataclass(frozen=True)
class WorkerMessage:
    protocol: str
    worker_id: str
    type: str
    request_id: str | None = None
    timestamp: str = field(default_factory=utc_timestamp)
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        _validate_message_fields(self)
        _ensure_json(self.payload)
        data: dict[str, Any] = {
            "protocol": self.protocol,
            "worker_id": self.worker_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
        if self.request_id:
            data["request_id"] = self.request_id
        return data


def _validate_message_fields(message: WorkerMessage) -> None:
    """Validate the non-payload portion of a wire message.

    ``WorkerMessage`` can be constructed directly, bypassing
    :func:`build_message`.  Encoding must therefore enforce the same contract
    for both construction paths before writing to a worker pipe.
    """
    if message.protocol != PROTOCOL:
        raise WorkerProtocolError(f"unsupported protocol: {message.protocol!r}")
    if not isinstance(message.worker_id, str) or not message.worker_id.strip():
        raise WorkerProtocolError("worker_id must be a non-empty string")
    if message.type not in MESSAGE_TYPES:
        raise WorkerProtocolError(f"unknown message type: {message.type!r}")
    if not isinstance(message.timestamp, str) or not message.timestamp.strip():
        raise WorkerProtocolError("timestamp must be a non-empty string")
    if message.request_id is not None and not isinstance(message.request_id, str):
        raise WorkerProtocolError("request_id must be a string when present")
    if not isinstance(message.payload, dict):
        raise WorkerProtocolError("payload must be an object")


def build_message(
    worker_id: str,
    message_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    request_id: str | None = None,
    timestamp: str | None = None,
) -> WorkerMessage:
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise WorkerProtocolError("worker_id must be a non-empty string")
    if message_type not in MESSAGE_TYPES:
        raise WorkerProtocolError(f"unknown message type: {message_type!r}")
    if request_id is not None and not isinstance(request_id, str):
        raise WorkerProtocolError("request_id must be a string when present")
    if timestamp is not None and (not isinstance(timestamp, str) or not timestamp.strip()):
        raise WorkerProtocolError("timestamp must be a non-empty string")
    body = dict(payload or {})
    _ensure_json(body)
    return WorkerMessage(
        protocol=PROTOCOL,
        worker_id=worker_id,
        type=message_type,
        request_id=request_id,
        timestamp=timestamp or utc_timestamp(),
        payload=body,
    )


def encode_message(message: WorkerMessage, *, newline: bool = True) -> bytes:
    if not isinstance(message, WorkerMessage):
        raise WorkerProtocolError("encode_message expects WorkerMessage")
    _validate_message_fields(message)
    raw = json.dumps(message.as_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise WorkerProtocolError(f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    return raw + (b"\n" if newline else b"")


def decode_message(raw: bytes | str) -> WorkerMessage:
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_BYTES + 1:
            raise WorkerProtocolError(f"message exceeds {MAX_MESSAGE_BYTES} bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkerProtocolError("message is not valid UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise WorkerProtocolError("message must be bytes or str")

    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    if "\n" in text or "\r" in text:
        raise WorkerProtocolError("JSONL message must contain exactly one line")
    if not text.strip():
        raise WorkerProtocolError("empty worker message")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise WorkerProtocolError(f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkerProtocolError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise WorkerProtocolError("message root must be an object")

    protocol = data.get("protocol")
    worker_id = data.get("worker_id")
    message_type = data.get("type")
    timestamp = data.get("timestamp")
    payload = data.get("payload", {})
    request_id = data.get("request_id")
    if protocol != PROTOCOL:
        raise WorkerProtocolError(f"unsupported protocol: {protocol!r}")
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise WorkerProtocolError("worker_id must be a non-empty string")
    if message_type not in MESSAGE_TYPES:
        raise WorkerProtocolError(f"unknown message type: {message_type!r}")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise WorkerProtocolError("timestamp must be a non-empty string")
    if request_id is not None and not isinstance(request_id, str):
        raise WorkerProtocolError("request_id must be a string when present")
    if not isinstance(payload, dict):
        raise WorkerProtocolError("payload must be an object")
    _ensure_json(payload)
    return WorkerMessage(protocol, worker_id, message_type, request_id, timestamp, payload)
