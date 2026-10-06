"""Core-owned file-replacement barrier, separate from DLC and data-root leases.

All normal frozen Core/settings processes take a shared kernel lock before
RuntimeLayout or Config. An installer must retain the exclusive lock through
file replacement/deletion. No PID killing, window-presence heuristic, or
portable/user-data cleanup is provided here. A lock file is not an installation
ledger and is never interpreted as evidence of installed feature packages.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import feature_state_io as io

CORE_EXECUTABLE = "dsh-pet-core-webm.exe"
CODE_LOCK_FILE = ".core-files.lock"
_runtime_code_lock: io.KernelLock | None = None


class CoreCodeGateError(ValueError):
    """Displayable reason code without user paths or OS error contents."""


class CoreCodeGate:
    def __init__(self, core_directory: Path):
        self.root = Path(core_directory).absolute()
        self.lock_path = self.root / CODE_LOCK_FILE

    def _check_boundary(self) -> None:
        io.safe_path(self.root)
        io.safe_path(self.lock_path)
        io.safe_path(self.root / CORE_EXECUTABLE)

    def _acquire(self, *, exclusive: bool) -> io.KernelLock:
        handle = None
        try:
            self._check_boundary()
            if not exclusive and not (self.root / CORE_EXECUTABLE).is_file():
                raise CoreCodeGateError("core_executable_missing")
            handle = io.open_kernel_lock(self.lock_path, exclusive=exclusive)
            self._check_boundary()
            if not exclusive and not (self.root / CORE_EXECUTABLE).is_file():
                raise CoreCodeGateError("core_executable_missing")
            return handle
        except (io.StateError, OSError, CoreCodeGateError) as exc:
            if handle is not None:
                handle.close()
            if isinstance(exc, CoreCodeGateError):
                raise
            code = exc.code if isinstance(exc, io.StateError) else "io_error"
            if code == "unsafe_path":
                raise CoreCodeGateError("code_boundary_invalid") from None
            if code == "lock_busy":
                reason = "core_in_use" if exclusive else "core_replacement_in_progress"
                raise CoreCodeGateError(reason) from None
            raise CoreCodeGateError("code_root_unavailable") from None

    def acquire_runtime(self) -> io.KernelLock:
        return self._acquire(exclusive=False)

    def acquire_replacement(self) -> io.KernelLock:
        """Nonblocking: never close an existing process to obtain the lock."""
        return self._acquire(exclusive=True)


def hold_current_core_code() -> None:
    """Normal product entry only. Never lock Python/source or legacy builds."""
    global _runtime_code_lock
    if _runtime_code_lock is not None:
        return
    if not getattr(sys, "frozen", False):
        return
    from build_variant import VARIANT

    if VARIANT != "core-webm":
        return
    executable = Path(sys.executable).absolute()
    if executable.name.casefold() != CORE_EXECUTABLE.casefold():
        raise CoreCodeGateError("core_executable_identity_invalid")
    # Non-inheritable handle remains alive even if every visible window closes;
    # the OS releases it when the process actually exits.
    _runtime_code_lock = CoreCodeGate(executable.parent).acquire_runtime()
