# -*- coding: utf-8 -*-
"""Verify the Qt runtime DLL chain in a PyInstaller onedir bundle."""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys


def load_pyd(name: str, pyd_path: str) -> None:
    spec = importlib.util.spec_from_file_location(name, pyd_path)
    if spec is None:
        raise RuntimeError(f"cannot create spec for {pyd_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--internal", required=True)
    args = ap.parse_args()

    internal = os.path.abspath(args.internal)
    for directory in (
        internal,
        os.path.join(internal, "shiboken6"),
        os.path.join(internal, "PySide6"),
    ):
        if os.path.isdir(directory) and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(directory)

    for name, pyd_names in (
        ("Shiboken", ("Shiboken.cp310-win_amd64.pyd", "Shiboken.pyd")),
        ("QtCore", ("QtCore.cp310-win_amd64.pyd", "QtCore.pyd")),
        ("QtGui", ("QtGui.cp310-win_amd64.pyd", "QtGui.pyd")),
        ("QtWidgets", ("QtWidgets.cp310-win_amd64.pyd", "QtWidgets.pyd")),
    ):
        package_dir = "shiboken6" if name == "Shiboken" else "PySide6"
        pyd = None
        for candidate in pyd_names:
            probe = os.path.join(internal, package_dir, candidate)
            if os.path.isfile(probe):
                pyd = probe
                break
        if pyd is None:
            print(f"FAIL: {os.path.join(internal, package_dir, pyd_names[0])} not found", file=sys.stderr)
            sys.exit(1)
        try:
            load_pyd(name, pyd)
        except Exception as exc:
            print(f"FAIL: {name} ({exc.__class__.__name__}: {exc})", file=sys.stderr)
            sys.exit(1)
        print(f"OK: {name}")

    print("ALL OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
