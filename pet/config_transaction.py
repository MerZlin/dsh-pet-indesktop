"""Nonblocking, locked JSON writes for independently owned configuration namespaces."""

from __future__ import annotations

import copy
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

SCREEN_NAMESPACE = "official.screen-understanding"


@contextmanager
def file_transaction(path: Path):
    # Late import avoids the existing slot_manager -> config helper dependency.
    from .slot_manager import acquire_file_lock, release_file_lock

    handle = acquire_file_lock(path)
    if handle is None:
        raise OSError("configuration_busy")
    try:
        yield
    finally:
        release_file_lock(handle)


def read_document(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError):
        raise OSError("configuration_invalid") from None


def atomic_document(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def namespace(data: dict) -> dict:
    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("configuration_invalid")
    value = plugins.get(SCREEN_NAMESPACE, {})
    if not isinstance(value, dict):
        raise ValueError("configuration_invalid")
    return copy.deepcopy(value)


def save_core_document(path: Path, data: dict) -> dict:
    """Preserve all CAS-owned plugin namespaces against stale ordinary Core saves."""
    with file_transaction(path.with_suffix(path.suffix + ".write.lock")):
        latest = read_document(path)
        plugins = latest.get("plugins", {})
        if isinstance(plugins, dict):
            # CAS owner namespaces beat a stale ordinary Core save snapshot.
            data.setdefault("plugins", {}).update(copy.deepcopy(plugins))
        independent = namespace(data)
        atomic_document(path, data)
        return independent
