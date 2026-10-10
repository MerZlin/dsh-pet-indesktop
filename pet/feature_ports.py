"""Small internal official host contracts; no Qt, application or feature imports.

Ports express ownership, not a Python sandbox. Official host code is trusted only
following package verification; callers cannot select another owner's namespace.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Protocol

from .api_ports import FeatureApiPort

if TYPE_CHECKING:
    from .credentials import CredentialVaultPort
    from .desktop_query import DesktopQueryPort
    from .workers.launch import WorkerLaunch


class FeatureConfigurationPort(Protocol):
    def read_namespace(self) -> dict: ...
    def revision(self) -> str: ...
    def migration_source(self) -> dict: ...
    def commit_namespace(self, value: dict, *, expected_revision: str, source_guard: Callable[[dict], None] | None = None) -> None: ...
    def operation(self) -> AbstractContextManager[None]: ...
    def read_journal(self) -> dict | None: ...
    def write_journal(self, value: dict) -> None: ...


class FeaturePreferencesPort(Protocol):
    """Compatibility preferences already scoped by the host; stage != flush."""

    def read(self) -> dict: ...
    def stage(self, values: dict) -> None: ...
    def flush(self) -> bool: ...


class FeatureDocumentPort(Protocol):
    """One host-selected data document, not general filesystem access."""

    def read(self) -> dict: ...
    def write(self, value: dict) -> None: ...
    def clear(self) -> None: ...


class FeatureStateDocumentPort(FeatureDocumentPort, Protocol):
    """A bound state file and its pre-existing cross-instance lock policy."""

    def locked(self) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class FeatureWindowState:
    """A value snapshot, not a window handle or an application object."""

    window_id: str
    instance_id: str
    character_id: str
    display_name: str
    visible: bool
    interacting: bool
    mouse_through: bool


@dataclass(frozen=True, slots=True)
class FeatureWindowPorts:
    """Core-bound operations. Only snapshots cross the application boundary."""

    snapshot: Callable[[], FeatureWindowState]
    agent_busy: Callable[[str, str], bool]
    show_bubble: Callable[[str, int], None]
    hold_bubble: Callable[[float], None]
    sync_text: Callable[[str, str], None]
    external_text: Callable[[str, str, str, str], None]
    enabled: Callable[[], bool]
    bind_lifecycle: Callable[[Callable[[], None], Callable[[], None], Callable[[], None]], Callable[[], None]]


@dataclass(frozen=True, slots=True)
class FeatureUserDataPort:
    """One Core-selected owner directory and read-only presentation snapshots.

    This does not grant the installation ledger, another owner's data or a
    global configuration object. Secrets remain in the separate vault port.
    """

    root: Path
    instance_id: str
    read_display: Callable[[], Mapping]
    alias_for: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class FeatureHostContext:
    """Internal official host ABI, supplied after host-owned authorization.

    Not a sandbox: trusted Python callbacks are opaque ports, not object secrecy.
    No application, window, global config or unrestricted filesystem is supplied.
    """

    owner: str
    configuration: FeatureConfigurationPort
    credentials: CredentialVaultPort
    legacy_secret_reader: Callable[[str], str]
    preferences: FeaturePreferencesPort
    documents: Mapping[str, FeatureDocumentPort]
    state_documents: Mapping[str, FeatureStateDocumentPort]
    desktop: DesktopQueryPort | None
    window: FeatureWindowPorts | None = None
    worker_launch_factory: Callable[[], WorkerLaunch] | None = None
    allow_in_process: bool = True
    execution_authorized: Callable[[], bool] | None = None
    bind_execution: Callable[[Callable, Callable], Callable[[], None]] | None = None
    user_data: FeatureUserDataPort | None = None
    api: FeatureApiPort | None = None
