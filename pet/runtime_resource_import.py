"""Bounded data-only character import; executable DLC is never adopted.

Source/target traversal and aggregate bounds are enforced by the data importer
before calling the existing content verifier. Version hashes and authoritative
active/previous pointers are preserved, not reconstructed from directories.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import __version__
from . import feature_state_io as io
from .content.manifest import current_platform, validate_package_root

PREFIX = "content/characters/"
MAX_FILE_BYTES = 128 * 1024**2
MAX_TOTAL_BYTES = 512 * 1024**2
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
_PAYLOAD_SUFFIXES = frozenset({".json", ".webm", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".ico", ".svg", ".txt", ".md", ".wav", ".mp3", ".ogg"})


def resource_name(name: str, *, directory=False) -> bool:
    if not name.startswith(PREFIX):
        return False
    parts = name[len(PREFIX) :].split("/")
    if not _ID.fullmatch(parts[0]):
        return False
    if directory and len(parts) == 1:
        return True
    if not directory and len(parts) == 2 and parts[1] in {"active.json", "previous.json"}:
        return True
    if len(parts) < 2 or parts[1] != "versions":
        return False
    if directory and len(parts) == 2:
        return True
    if len(parts) < 3 or not _VERSION.fullmatch(parts[2]):
        return False
    return directory or (len(parts) >= 4 and Path(parts[-1]).suffix.lower() in _PAYLOAD_SUFFIXES)


def file_limit(name: str) -> int:
    # Metadata remains JSON-bounded; only recognized opaque media is larger.
    return MAX_FILE_BYTES if resource_name(name) and not name.endswith(".json") else 16 * 1024**2


def validate_resources(root: Path) -> None:
    characters = root / "content/characters"
    io.safe_path(characters)
    if not characters.exists():
        return
    for character in characters.iterdir():
        io.safe_path(character)
        if not character.is_dir() or not _ID.fullmatch(character.name):
            raise io.StateError("import_resource_layout_invalid")
        versions = character / "versions"
        io.safe_path(versions)
        if not versions.is_dir():
            raise io.StateError("import_resource_layout_invalid")
        verified = {}
        for version in versions.iterdir():
            io.safe_path(version)
            if not version.is_dir() or not _VERSION.fullmatch(version.name):
                raise io.StateError("import_resource_layout_invalid")
            manifest, errors, _ = validate_package_root(
                version, core_version=__version__, platform_name=current_platform(), allow_unsigned=False, verify_hash=True
            )
            if errors or manifest is None or manifest.version != version.name or character.name not in manifest.characters:
                raise io.StateError("import_resource_verification_failed")
            verified[version.name] = manifest.plugin_id
        for pointer_name in ("active.json", "previous.json"):
            pointer = character / pointer_name
            io.safe_path(pointer)
            if not pointer.exists():
                if pointer_name == "active.json":
                    raise io.StateError("import_resource_pointer_invalid")
                continue
            try:
                value = json.loads(io.read_bytes(pointer, 65536))
                if not isinstance(value, dict) or set(value) != {"plugin_id", "version"} or not isinstance(value["version"], str):
                    raise ValueError
                if verified.get(value["version"]) != value["plugin_id"]:
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                raise io.StateError("import_resource_pointer_invalid") from None
