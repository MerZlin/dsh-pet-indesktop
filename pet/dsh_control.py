# -*- coding: utf-8 -*-
"""Client for the in-process DSH bridge watchdog control queue."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid

TIMEOUT_S = 30.0
POLL_S = 0.08


def _bridge_dir() -> str:
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, "dsh-pet-bridge")
    if os.sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "dsh-pet-bridge")
    return os.path.join(os.path.expanduser("~"), ".config", "dsh-pet-bridge")


def _atomic_json(path: str, value: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="watchdog-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _log_event(directory: str, event: str, **fields) -> None:
    try:
        path = os.path.join(directory, f"dsh-pet-control-{os.getpid()}.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), "agent": "pet", "event": event, **fields}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def request(operation: str, session_id: str, text: str = "", ports: list[int] | None = None,
            *, goal: str = "", context: str = "", provider: str = "", model: str = "",
            timeout: float = TIMEOUT_S, alert_id: str = "") -> tuple[bool, str]:
    del ports  # retained for compatibility; bridge discovery is file based
    if not session_id or session_id.startswith("turn:"):
        return False, "missing-session-id"
    if operation not in {"interrupt", "replan"}:
        return False, "unsupported-operation"
    request_id = uuid.uuid4().hex
    directory = _bridge_dir()
    request_path = os.path.join(directory, f"watchdog-request-{request_id}.json")
    response_path = os.path.join(directory, f"watchdog-response-{request_id}.json")
    payload = {
        "id": request_id,
        "ts": int(time.time() * 1000),
        "operation": operation,
        "sessionId": session_id,
        "text": text[:12000],
        "goal": goal[:2000],
        "context": context[:12000],
        "provider": provider[:120],
        "model": model[:240],
        "timeoutMs": max(1000, int(float(timeout) * 1000)),
    }
    try:
        _atomic_json(request_path, payload)
        _log_event(directory, "pet/control-clicked", requestId=request_id, sessionId=session_id,
                   operation=operation, alertId=alert_id)
        _log_event(directory, "pet/control-queued", requestId=request_id, sessionId=session_id,
                   operation=operation, requestPath=request_path, directory=directory, written=True)
    except Exception as exc:
        _log_event(directory, "pet/control-queued", requestId=request_id, sessionId=session_id,
                   operation=operation, requestPath=request_path, directory=directory, written=False,
                   error=str(exc))
        return False, f"queue-write-failed:{exc}"

    deadline = time.monotonic() + max(1.0, float(timeout))
    try:
        while time.monotonic() < deadline:
            try:
                with open(response_path, "r", encoding="utf-8") as handle:
                    result = json.load(handle)
                if result.get("ok") is True:
                    return True, json.dumps(result, ensure_ascii=False)
                return False, str(result.get("error") or "bridge-control-rejected")
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                time.sleep(POLL_S)
    finally:
        for path in (request_path, response_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    return False, "bridge-control-timeout"
