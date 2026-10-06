"""Local onedir native lookup, without source/PYTHONPATH fallback.

CPython's FileFinder may fail to discover a long .pyd pathname even with a
long-path-aware frozen executable. Keep PYZ precedence and add only the
extended-length spelling of the executable's own sealed dependency directory.
The parent still verifies the bundle and sandbox before any candidate executes.
"""

from __future__ import annotations

import ntpath
import sys


def activate_frozen_dependency_path() -> bool:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    root = getattr(sys, "_MEIPASS", None)
    executable = sys.executable
    if not isinstance(root, str) or not isinstance(executable, str):
        raise ValueError("frozen_runtime_layout")
    # Win32 extended spelling must designate the same local drive, never UNC,
    # device namespaces or a different dependency root.
    root = root[4:] if root.startswith("\\\\?\\") else root
    executable = executable[4:] if executable.startswith("\\\\?\\") else executable
    for path in (root, executable):
        drive, tail = ntpath.splitdrive(path)
        if len(drive) != 2 or drive[1] != ":" or not tail.startswith("\\") or any(part in (".", "..") for part in tail.split("\\")):
            raise ValueError("frozen_runtime_layout")
    expected = ntpath.join(ntpath.dirname(executable), "_internal")
    if ntpath.normcase(ntpath.normpath(root)) != ntpath.normcase(expected):
        raise ValueError("frozen_runtime_layout")
    extended = "\\\\?\\" + ntpath.normpath(root)
    if extended in sys.path:
        return False
    sys.path.append(extended)
    return True
