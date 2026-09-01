"""Opt-in diagnostics for the multi-process collision path."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path


ENABLED = os.environ.get("DSH_PET_COLLISION_DEBUG") == "1"
MAX_BYTES = 2 * 1024 * 1024
_lock = threading.Lock()


def log(instance: str, event: str, **fields) -> None:
    if not ENABLED:
        return
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    path = Path(appdata) / "dsh-pet-standalone-webm-chat" / "collision-debug.log"
    line = "{} [{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), instance or "?", event)
    if fields:
        line += " " + " ".join(f"{key}={value!r}" for key, value in fields.items())
    line += "\n"
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except OSError:
        pass
