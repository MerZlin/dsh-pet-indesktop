# -*- coding: utf-8 -*-
"""Phase 3 worker JSONL protocol tests."""

from __future__ import annotations

import json

import pytest

from pet.workers.protocol import (
    MAX_MESSAGE_BYTES,
    WorkerMessage,
    WorkerProtocolError,
    build_message,
    decode_message,
    encode_message,
)


def test_round_trip_preserves_request_id_timestamp_and_payload() -> None:
    message = build_message(
        "agent-link-events",
        "event",
        {"generation": 3, "record": {"event": "task/started", "ok": True}},
        request_id="req-7",
        timestamp="2026-09-25T00:00:00Z",
    )

    decoded = decode_message(encode_message(message))

    assert decoded == message
    assert decoded.as_dict()["request_id"] == "req-7"


def test_encode_rejects_unknown_message_type() -> None:
    with pytest.raises(WorkerProtocolError, match="unknown message type"):
        build_message("agent-link-events", "not-supported", {})


def test_payload_must_be_json_serializable() -> None:
    with pytest.raises(WorkerProtocolError, match="not JSON serializable"):
        build_message("agent-link-events", "config_push", {"bad": object()})


def test_decode_rejects_invalid_json_protocol_and_message_type() -> None:
    cases = [
        (b"not-json\n", "invalid JSON"),
        (
            json.dumps(
                {
                    "protocol": "pet-worker/v0",
                    "worker_id": "agent-link-events",
                    "type": "hello",
                    "timestamp": "2026-09-25T00:00:00Z",
                    "payload": {},
                }
            ).encode()
            + b"\n",
            "unsupported protocol",
        ),
        (
            json.dumps(
                {
                    "protocol": "pet-worker/v1",
                    "worker_id": "agent-link-events",
                    "type": "unknown",
                    "timestamp": "2026-09-25T00:00:00Z",
                    "payload": {},
                }
            ).encode()
            + b"\n",
            "unknown message type",
        ),
    ]

    for raw, expected in cases:
        with pytest.raises(WorkerProtocolError, match=expected):
            decode_message(raw)


def test_decode_rejects_multiline_and_oversized_messages() -> None:
    with pytest.raises(WorkerProtocolError, match="exactly one line"):
        decode_message(b'{"protocol":"pet-worker/v1"}\nextra\n')

    oversized = b"{" + b"x" * (MAX_MESSAGE_BYTES + 1) + b"}\n"
    with pytest.raises(WorkerProtocolError, match="exceeds"):
        decode_message(oversized)

    large = WorkerMessage(
        protocol="pet-worker/v1",
        worker_id="agent-link-events",
        type="hello",
        payload={"blob": "x" * MAX_MESSAGE_BYTES},
    )
    with pytest.raises(WorkerProtocolError, match="exceeds"):
        encode_message(large)


def test_decode_requires_object_payload_and_required_fields() -> None:
    base = {
        "protocol": "pet-worker/v1",
        "worker_id": "agent-link-events",
        "type": "hello",
        "timestamp": "2026-09-25T00:00:00Z",
    }

    missing_timestamp = dict(base)
    missing_timestamp.pop("timestamp")
    with pytest.raises(WorkerProtocolError, match="timestamp"):
        decode_message(json.dumps(missing_timestamp).encode())

    bad_payload = dict(base, payload=[])
    with pytest.raises(WorkerProtocolError, match="payload must be an object"):
        decode_message(json.dumps(bad_payload).encode())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("worker_id", "   ", "worker_id"),
        ("timestamp", "   ", "timestamp"),
        ("request_id", 123, "request_id"),
    ],
)
def test_protocol_rejects_invalid_scalar_fields(field: str, value: object, message: str) -> None:
    data = build_message("worker", "hello").as_dict()
    data[field] = value

    with pytest.raises(WorkerProtocolError, match=message):
        decode_message(json.dumps(data))


def test_encode_revalidates_directly_constructed_messages() -> None:
    invalid = WorkerMessage("pet-worker/v1", "worker", "hello", timestamp="   ")

    with pytest.raises(WorkerProtocolError, match="timestamp"):
        encode_message(invalid)


@pytest.mark.parametrize("message_type", ["request", "response"])
def test_request_and_response_require_request_id(message_type: str) -> None:
    message = build_message("proactive-screen", message_type, {}, request_id="req-1")
    assert decode_message(encode_message(message)) == message

    with pytest.raises(WorkerProtocolError, match="request_id"):
        build_message("proactive-screen", message_type, {})

    raw = message.as_dict()
    raw.pop("request_id")
    with pytest.raises(WorkerProtocolError, match="request_id"):
        decode_message(json.dumps(raw))


def test_request_response_message_types_remain_jsonl_compatible() -> None:
    request = build_message(
        "proactive-screen",
        "request",
        {"operation": "observe_foreground", "arguments": {"generation": 2}},
        request_id="req-observe",
    )
    response = build_message(
        "proactive-screen",
        "response",
        {"operation": "observe_foreground", "status": "ok", "result": {}},
        request_id="req-observe",
    )

    assert decode_message(encode_message(request)).type == "request"
    assert decode_message(encode_message(response)).type == "response"
