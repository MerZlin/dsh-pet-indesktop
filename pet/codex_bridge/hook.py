"""Record lifecycle state only; never retain prompts, tool inputs, or replies."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

STATES = {
    "UserPromptSubmit": "thinking",
    "PreToolUse": "coding",
    "PermissionRequest": "attention",
    "PostToolUse": "coding",
    "Stop": "completed",
    "Interrupt": "interrupted",
    "SessionEnd": "completed",
}


def record(payload, directory, now=None):
    event, session = payload.get("hook_event_name"), payload.get("session_id")
    if event not in STATES or not isinstance(session, str) or not session:
        return False
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    value = {"state": STATES[event], "ts": time.time() if now is None else now, "event": event, "sessionId": session}
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as stream:
        json.dump(value, stream)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, directory / (key + ".json"))
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    args = parser.parse_args()
    try:
        record(json.load(sys.stdin), args.events)
    except (OSError, ValueError, TypeError):
        pass  # Never prevent Codex from continuing because a pet is unavailable.


if __name__ == "__main__":
    main()
