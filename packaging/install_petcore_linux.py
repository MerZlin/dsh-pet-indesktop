#!/usr/bin/env python3
"""Install a user-level launcher for this extracted candidate (no sudo/copy).

Place the extracted directory at its final location before running this file.
--remove deletes only this launcher's .desktop file, preserving app and data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def quote(value):
    # Desktop Entry string escaping is applied after Exec argument escaping.
    value = str(value).replace("%", "%%")
    value = "".join("\\" + ch if ch in '\\"`$' else ch for ch in value)
    return ('"' + value + '"').replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def install(*, remove=False):
    bundle = Path(__file__).resolve().parent
    executable = bundle / "dsh-pet-core"
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    desktop = data_home / "applications/io.dsh.petcore.desktop"
    if remove:
        desktop.unlink(missing_ok=True)
        return desktop
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"Missing executable: {executable}")
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nType=Application\nName=DSH Pet Core\n"
                       f"Exec={quote(executable)}\nTerminal=false\nCategories=Game;\n", encoding="utf-8")
    desktop.chmod(0o644)
    return desktop


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true")
    print(install(remove=parser.parse_args().remove))
