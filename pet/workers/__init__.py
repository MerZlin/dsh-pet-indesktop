# -*- coding: utf-8 -*-
"""Built-in worker registry and Qt-free worker entry exports."""

from .protocol import (
    MAX_MESSAGE_BYTES,
    MESSAGE_TYPES,
    PROTOCOL,
    WorkerMessage,
    WorkerProtocolError,
    build_message,
    decode_message,
    encode_message,
)

__all__ = [
    "AgentLinkWorker",
    "MAX_MESSAGE_BYTES",
    "MESSAGE_TYPES",
    "PROTOCOL",
    "WorkerMessage",
    "WorkerProtocolError",
    "build_message",
    "decode_message",
    "encode_message",
    "run_agent_link_worker",
]


def __getattr__(name):
    if name in {"AgentLinkWorker", "run_agent_link_worker"}:
        from . import agent_link_worker

        return getattr(agent_link_worker, name)
    raise AttributeError(name)
