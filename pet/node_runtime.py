# -*- coding: utf-8 -*-
"""Resolve Node.js tools consistently for terminal and desktop-app launches."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


_POSIX_BIN_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "~/.npm-global/bin",
    "~/.local/bin",
    "~/.volta/bin",
    "~/.bun/bin",
    "~/.yarn/bin",
    "~/Library/pnpm",
    "~/.local/share/pnpm",
)


def augmented_path() -> str:
    """Return PATH plus common Node package-manager locations on POSIX."""
    if os.name == "nt":
        return os.environ.get("PATH", "")
    extra: list[str] = []
    for directory in _POSIX_BIN_DIRS:
        path = Path(directory).expanduser()
        if path.is_dir():
            extra.append(str(path))
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        extra.extend(str(path) for path in sorted(nvm.glob("*/bin")) if path.is_dir())
    return os.pathsep.join([*extra, os.environ.get("PATH", "")])


def which(name: str) -> str | None:
    """Resolve an executable using the desktop-safe augmented PATH."""
    return shutil.which(name, path=augmented_path())
