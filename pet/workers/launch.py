"""Explicit child-only launch settings; no Qt or feature implementation imports.

Environment cleanup is necessary but is NOT proof of independent DLL loading.
Frozen artifact validation must additionally inspect actual loaded module paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence


@dataclass
class WorkerLaunch:
    """One acquired launch lease. Release only after the native child has exited."""

    program: str
    arguments: tuple[str, ...]
    working_directory: str
    environment: Mapping[str, str]
    on_release: Callable[[], None]
    _released: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not Path(self.program).is_absolute() or not Path(self.working_directory).is_absolute():
            raise ValueError("external Worker paths must be absolute")
        self.arguments = tuple(self.arguments)
        self.environment = MappingProxyType(dict(self.environment))

    def close(self) -> None:
        if not self._released:
            self._released = True
            self.on_release()


def isolated_worker_environment(
    source: Mapping[str, str],
    *,
    core_roots: Sequence[Path] = (),
) -> dict[str, str]:
    """Copy network/system settings but discard inherited Python/Qt/Core paths.

    Never mutate os.environ, the parent's DLL directories, or proxy/TLS policy.
    PATH relative and empty entries are discarded (they depend on Core's cwd).
    Caller supplies Core application/runtime roots, NOT the shared data root.
    """
    roots = tuple(Path(root).resolve() for root in core_roots)
    drop = {"QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML_IMPORT_PATH", "QML2_IMPORT_PATH"}
    result: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper.startswith(("PYTHON", "_PYI", "_MEIPASS")) or upper in drop:
            continue
        if upper == "PATH":
            kept = []
            for entry in value.split(os.pathsep):
                candidate = Path(entry.strip('"'))
                if not entry or not candidate.is_absolute():
                    continue
                resolved = candidate.resolve()
                if not any(resolved == root or root in resolved.parents for root in roots):
                    kept.append(entry)
            result[key] = os.pathsep.join(kept)
        else:
            result[key] = value
    return result
