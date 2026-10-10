"""Local package routing and the legacy closed Setup adapter.

A user-selected directory or ZIP is routed only from bounded manifest metadata.
The target manager performs the full verifier, transaction, self-check and
startup work; routing never imports package code or creates a Worker. Local
activation is explicit user trust, not publisher authentication or sandboxing.
"""

from __future__ import annotations

import hashlib
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Sequence

from PySide6.QtCore import QObject, Slot

from .feature_management import FeatureManagementRuntime
from .feature_package_transactions import Inspection
from .official_features import OFFICIAL_FEATURES
from .plugins.package_trust import PackageVerificationError, read_manifest_registration

if TYPE_CHECKING:
    from PySide6.QtWidgets import QDialog


_ROUTE_MANIFEST_LIMIT = 2 * 1024 * 1024
_ROUTE_ENTRY_LIMIT = 16384
_ROUTE_PLATFORMS = frozenset({"win32", "linux", "darwin"})


@dataclass(frozen=True)
class LocalPackageRoute:
    """Bounded, non-executable routing evidence for a user-selected package.

    The route is deliberately weaker than :class:`VerifiedFeatureDescriptor`:
    it only tells the caller which generic feature registration should receive
    the package.  The selected source must still pass the full verifier before
    it can be staged, installed, imported, or started.
    """

    source: Path
    feature_id: str
    factory: str
    execution_kind: str
    format_version: int
    version: str
    api_version: str
    core_requires: str
    platforms: tuple[str, ...]
    capabilities: tuple[str, ...]
    manifest_digest: str
    manifest: Mapping

    @classmethod
    def from_manifest(cls, source: Path, raw: bytes) -> "LocalPackageRoute":
        try:
            registration, payload = read_manifest_registration(raw, max_bytes=_ROUTE_MANIFEST_LIMIT)
            version = payload["version"]
            api_version = payload["api_version"]
            core_requires = payload["core_requires"]
            platforms = payload["platforms"]
            capabilities = payload["capabilities"]
            if not isinstance(version, str) or not version:
                raise PackageVerificationError("invalid package version")
            if not isinstance(api_version, str) or not api_version or len(api_version) > 128:
                raise PackageVerificationError("invalid package API version")
            if not isinstance(core_requires, str) or not core_requires or len(core_requires) > 1024:
                raise PackageVerificationError("invalid Core version requirement")
            if (
                not isinstance(platforms, list)
                or not platforms
                or len(platforms) > len(_ROUTE_PLATFORMS)
                or len(platforms) != len(set(platforms))
                or any(platform not in _ROUTE_PLATFORMS for platform in platforms)
            ):
                raise PackageVerificationError("invalid package platforms")
            if not isinstance(capabilities, list) or len(capabilities) != len(set(capabilities)):
                raise PackageVerificationError("invalid package capabilities")
            frozen_manifest = _freeze_manifest(payload)
            return cls(
                source=source,
                feature_id=registration.id,
                factory=registration.factory,
                execution_kind=registration.execution_kind,
                format_version=2 if "format_version" in payload else 1,
                version=version,
                api_version=api_version,
                core_requires=core_requires,
                platforms=tuple(platforms),
                capabilities=tuple(capabilities),
                manifest_digest=hashlib.sha256(raw).hexdigest(),
                manifest=frozen_manifest,
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, PackageVerificationError):
                raise
            raise PackageVerificationError("local package route invalid") from exc


def _freeze_manifest(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_manifest(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_manifest(item) for item in value)
    return value


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise PackageVerificationError("manifest size limit")
    return raw


def _safe_archive_name(name: str) -> str:
    if not isinstance(name, str) or "\\" in name or "\x00" in name or len(name) > 512:
        raise PackageVerificationError("unsafe archive path")
    if PurePosixPath(name).is_absolute():
        raise PackageVerificationError("unsafe archive path")
    parts = name.rstrip("/").split("/")
    if not name or not parts or any(not part or part in {".", ".."} for part in parts):
        raise PackageVerificationError("unsafe archive path")
    return "/".join(parts)


def _read_zip_manifest(source: Path) -> bytes:
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= _ROUTE_ENTRY_LIMIT:
                raise PackageVerificationError("archive entry limit")
            names: dict[str, tuple[str, bool]] = {}
            manifest = None
            for member in members:
                name = _safe_archive_name(member.filename)
                mode = member.external_attr >> 16
                if member.flag_bits & 1 or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or member.external_attr & 0x400:
                    raise PackageVerificationError("unsafe archive member")
                folded = name.casefold()
                if folded in names:
                    raise PackageVerificationError("duplicate archive entry")
                is_dir = member.is_dir()
                names[folded] = name, is_dir
                if name == "manifest.json":
                    if is_dir or manifest is not None or member.file_size > _ROUTE_MANIFEST_LIMIT:
                        raise PackageVerificationError("invalid archive manifest")
                    manifest = member
            if manifest is None:
                raise PackageVerificationError("manifest missing")
            for name, _is_dir in names.values():
                parts = name.split("/")
                for index in range(1, len(parts)):
                    prefix = "/".join(parts[:index])
                    entry = names.get(prefix.casefold())
                    if entry is not None and (entry[0] != prefix or not entry[1]):
                        raise PackageVerificationError("archive path collision")
            raw = archive.read(manifest)
            if len(raw) > _ROUTE_MANIFEST_LIMIT:
                raise PackageVerificationError("manifest size limit")
            return raw
    except zipfile.BadZipFile as exc:
        raise PackageVerificationError("invalid local package archive") from exc


def route_local_package(source: Path | str) -> LocalPackageRoute:
    """Read a selected directory/ZIP manifest without importing or executing it."""

    try:
        path = Path(source).expanduser().resolve(strict=True)
        if path.is_dir():
            manifest = path / "manifest.json"
            if manifest.is_symlink() or not manifest.is_file():
                raise PackageVerificationError("manifest missing")
            raw = _read_bounded(manifest, _ROUTE_MANIFEST_LIMIT)
        elif path.is_file():
            raw = _read_zip_manifest(path)
        else:
            raise PackageVerificationError("local package source invalid")
        return LocalPackageRoute.from_manifest(path, raw)
    except PackageVerificationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PackageVerificationError("local package route invalid") from exc


def attach_local_package_manager(
    config,
    host,
    source_or_route: LocalPackageRoute | Path | str,
    *,
    role: str = "core",
    management_only: bool = False,
) -> FeatureManagementRuntime:
    """Attach the generic manager selected by a bounded local manifest."""
    route = source_or_route if isinstance(source_or_route, LocalPackageRoute) else route_local_package(source_or_route)
    from .feature_management import attach_feature_management

    return attach_feature_management(
        config,
        host,
        feature_id=route.feature_id,
        role=role,
        management_only=management_only,
    )


def parse_intents(args: Sequence[str]) -> tuple[Path, tuple[str, ...]]:
    if not 2 <= len(args) <= 3 or any(not isinstance(value, str) or "\0" in value for value in args):
        raise ValueError("local_package_intent_invalid")
    root, owners = Path(args[0]), tuple(args[1:])
    if not root.is_absolute() or len(set(owners)) != len(owners) or not set(owners) <= set(OFFICIAL_FEATURES):
        raise ValueError("local_package_intent_invalid")
    return root, owners


class LocalPackageIntent(QObject):
    def __init__(self, manager: FeatureManagementRuntime, packages_root: Path):
        super().__init__(manager)
        if manager.feature_id not in OFFICIAL_FEATURES or not Path(packages_root).is_absolute():
            raise ValueError("local_package_intent_invalid")
        self.manager = manager
        self.packages_root = Path(packages_root)
        self.waiting = False
        self.closed = False
        manager.result_ready.connect(self._result)

    def begin(self) -> bool:
        if self.closed or self.waiting or self.manager.builtin:
            return False
        self.waiting = True
        if not self.manager.submit("inspect"):
            self.waiting = False
            return False
        return True

    @Slot(object)
    def _result(self, result) -> None:
        if self.closed or not self.waiting or not isinstance(result, Inspection) or result.feature_id != self.manager.feature_id:
            return
        self.waiting = False
        if result.pending_transaction is not None or result.status == "recovery_required":
            return
        command = "upgrade" if result.active is not None else "install"
        self.manager.submit(command, self.packages_root / (self.manager.feature_id + ".zip"))

    def close(self) -> None:
        # No authority to cancel accepted journals or re-enable removed code.
        self.closed = True
        self.waiting = False


def create_local_package_dialog(managers: dict[str, FeatureManagementRuntime], owners: tuple[str, ...]) -> QDialog:
    """Build the actual local-install UI without starting any package operation."""
    from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

    from .feature_management_ui import FeatureManagementWidget

    dialog = QDialog()
    dialog.setWindowTitle("安装本地扩展：分别预检和确认")
    dialog.setAccessibleName("本地功能包确认")
    dialog.resize(720, 650)
    layout = QVBoxLayout(dialog)
    hint = QLabel(
        "Core 已安装。各包独立确认；缺包或失败不会阻止 Core，也不会联网下载。选择 ZIP 或目录后，Core 只检查结构、兼容性与文件完整性；等待启动确认不代表功能已可用。",
        dialog,
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)
    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    contents = QWidget(scroll)
    body = QVBoxLayout(contents)
    for owner in owners:
        manager = managers[owner]
        widget = FeatureManagementWidget(manager, contents)
        heading = QLabel(widget.feature_title, contents)
        heading.setObjectName("localPackageOwnerHeading")
        heading.setAccessibleName(widget.feature_title)
        heading.setWordWrap(True)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        body.addWidget(heading)
        body.addWidget(widget)
    scroll.setWidget(contents)
    layout.addWidget(scroll)
    return dialog


def run_local_packages(packages_root: Path, owners: tuple[str, ...]) -> int:
    """Normal code-lock/data-root bootstrap has already run in _main."""
    import sys

    from PySide6.QtWidgets import QApplication

    from .async_exit import application_exit_gate
    from .config import Config
    from .feature_management import attach_official_management, close_official_management
    from .plugins.feature_host import FeatureHost

    # Defence in depth for callers other than the closed production entry.
    parse_intents([str(packages_root), *owners])
    app = QApplication([sys.argv[0]])
    host = FeatureHost()
    managers = attach_official_management(Config(), host, role="local-install", management_only=True)
    dialog = create_local_package_dialog(managers, owners)
    intents = [LocalPackageIntent(managers[owner], packages_root) for owner in owners]
    # Closing the management window leaves lifecycle endpoints/event loop alive
    # through accepted writes, deletes and queued GUI preparation.
    gate = application_exit_gate(app)
    token = gate.register("local-package-intents", lambda: None, lambda: all(not manager.busy for manager in managers.values()))
    dialog.show()
    for intent in intents:
        intent.begin()
    try:
        app.exec()
        return (
            0
            if all(managers[owner].last_operation is not None and managers[owner].last_operation.status in ("completed", "idempotent") for owner in owners)
            else 3
        )
    finally:
        for intent in intents:
            intent.close()
        gate.unregister(token)
        close_official_management(host)
