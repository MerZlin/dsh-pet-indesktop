# -*- coding: utf-8 -*-
"""Verify the Qt runtime DLL chain in a PyInstaller onedir bundle."""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import sys


def load_pyd(name: str, pyd_path: str) -> None:
    spec = importlib.util.spec_from_file_location(name, pyd_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot create loader for {pyd_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


def loaded_module_path(name: str) -> str | None:
    """Return the path of a loaded Windows DLL, when the platform exposes it."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    handle = kernel32.GetModuleHandleW(name)
    if not handle:
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    kernel32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel32.GetModuleFileNameW.restype = ctypes.c_uint32
    length = kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
    return buffer.value if length else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--internal", required=True)
    args = ap.parse_args()

    internal = os.path.abspath(args.internal)
    # Keep the handles alive until every extension module has been loaded.
    # Closing them early can make a direct probe depend on unrelated process state.
    dll_directory_handles = []
    for directory in (
        internal,
        os.path.join(internal, "shiboken6"),
        os.path.join(internal, "PySide6"),
    ):
        if os.path.isdir(directory) and hasattr(os, "add_dll_directory"):
            dll_directory_handles.append(os.add_dll_directory(directory))

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

    if os.name == "nt":
        icu_path = loaded_module_path("icuuc.dll")
        if icu_path:
            print(f"OK: icuuc.dll -> {icu_path}")
        else:
            print("WARN: icuuc.dll was not present in the loaded module list")

    print("ALL OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
