"""Executable-scoped new-product startup cleanup, not legacy global hook removal.

Only the current-user Run value for dsh-pet-core-webm is inspected. Generated
OS-boundary tests can supply a registry adapter; production never redirects it
or enumerates other values. A second installed Core's registration is retained.
"""

from __future__ import annotations

import ntpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import feature_state_io as io

VALUE_NAME = "dsh-pet-core-webm"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_COMMAND = re.compile(r'^cmd /c start "" /D "([^"\r\n]+)" "([^"\r\n]+)" --slot 0$', re.IGNORECASE)


@dataclass(frozen=True)
class RegistrationCleanupResult:
    status: str
    reason: str | None = None


def _identity(path):
    return ntpath.normcase(ntpath.normpath(str(path)))


def remove_owned_autostart(executable: Path, *, registry=None) -> RegistrationCleanupResult:
    executable = Path(executable).absolute()
    try:
        io.safe_path(executable)
        if executable.name.casefold() != "dsh-pet-core-webm.exe" or not executable.is_file():
            return RegistrationCleanupResult("recovery_required", "core_executable_boundary_invalid")
        if registry is None:
            if sys.platform != "win32":
                return RegistrationCleanupResult("recovery_required", "platform_not_verified")
            import winreg

            registry = winreg
        try:
            handle = registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_QUERY_VALUE | registry.KEY_SET_VALUE)
        except FileNotFoundError:
            return RegistrationCleanupResult("idempotent")
        with handle as key:
            try:
                value, kind = registry.QueryValueEx(key, VALUE_NAME)
            except FileNotFoundError:
                return RegistrationCleanupResult("idempotent")
            match = _COMMAND.fullmatch(value) if isinstance(value, str) and kind == registry.REG_SZ else None
            if match is None:
                return RegistrationCleanupResult("recovery_required", "autostart_registration_ambiguous")
            directory, target = match.groups()
            if not ntpath.isabs(directory) or not ntpath.isabs(target) or _identity(directory) != _identity(ntpath.dirname(target)):
                return RegistrationCleanupResult("recovery_required", "autostart_registration_ambiguous")
            if _identity(target) != _identity(executable):
                return RegistrationCleanupResult("idempotent", "other_core_registration_preserved")
            # The Core code/removal barriers exclude legitimate app instances.
            # Registry read/delete is NOT an OS CAS against arbitrary external
            # editors; recheck once and never overwrite a changed/new value.
            if registry.QueryValueEx(key, VALUE_NAME) != (value, kind):
                return RegistrationCleanupResult("recovery_required", "autostart_registration_changed")
            registry.DeleteValue(key, VALUE_NAME)
            try:
                registry.QueryValueEx(key, VALUE_NAME)
            except FileNotFoundError:
                return RegistrationCleanupResult("completed")
            return RegistrationCleanupResult("recovery_required", "autostart_registration_changed")
    except (io.StateError, OSError):
        return RegistrationCleanupResult("recovery_required", "autostart_registry_unavailable")
