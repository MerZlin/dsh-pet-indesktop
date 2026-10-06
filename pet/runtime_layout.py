"""Early, Qt-free product layout. A broken portable contract never falls back.

Only the new Core selects this layout; older/source distributions keep their
existing data roots. Identities are non-secret. OS credentials are never stored
in this directory or migrated automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import feature_state_io as io
from .official_features import official_feature

PRODUCT_ID = "dsh-pet-core-webm"
_runtime_session: RuntimeSession | None = None
_runtime_removal_mode = False
_current_layout: RuntimeLayout | None = None


class RuntimeLayoutError(ValueError):
    """Fixed, displayable failure reason; no silent relocation."""


@dataclass
class RuntimeSession:
    """Both roots are pinned until natural process exit; closing a UI is not enough."""

    data_access: io.KernelLock
    removal_gate: io.KernelLock | None

    @property
    def closed(self) -> bool:
        return self.data_access.closed

    def close(self) -> None:
        self.data_access.close()
        if self.removal_gate is not None:
            self.removal_gate.close()

    def __enter__(self) -> RuntimeSession:
        if self.closed:
            raise RuntimeLayoutError("data_session_closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _removal_gate(root: Path) -> io.KernelLock:
    try:
        io.safe_path(root)
        return io.open_kernel_lock(root / "core-removal.lock", exclusive=False)
    except io.StateError as exc:
        reason = {"lock_busy": "core_removal_in_progress", "unsafe_path": "data_root_boundary_invalid"}.get(exc.code, "data_root_unavailable")
        raise RuntimeLayoutError(reason) from None


def _closed_removal_entry() -> bool:
    try:
        from build_variant import VARIANT
    except ImportError:
        return False
    return bool(
        getattr(sys, "frozen", False)
        and VARIANT == "core-webm"
        and Path(sys.executable).name.casefold() == "dsh-pet-core-webm.exe"
        and sys.argv[1:] == ["--core-maintenance", "uninstall"]
    )


def shell_appdata() -> Path:
    """Match Inno {userappdata}, not a child-process APPDATA override."""
    if os.name != "nt":
        raise RuntimeLayoutError("core_removal_platform_unverified")
    import ctypes
    from ctypes import wintypes as w

    class Guid(ctypes.Structure):
        _fields_ = [("data1", w.DWORD), ("data2", w.WORD), ("data3", w.WORD), ("data4", ctypes.c_ubyte * 8)]

    folder_id = Guid.from_buffer_copy(uuid.UUID("3eb685db-65f9-4cf6-a03a-e3ef65729f3d").bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    get_folder = shell.SHGetKnownFolderPath
    get_folder.argtypes = [ctypes.POINTER(Guid), w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    get_folder.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    result = ctypes.c_wchar_p()
    if get_folder(ctypes.byref(folder_id), 0, None, ctypes.byref(result)) != 0:
        raise RuntimeLayoutError("shell_appdata_unavailable")
    try:
        if not result.value:
            raise RuntimeLayoutError("shell_appdata_unavailable")
        return Path(result.value)
    finally:
        free(ctypes.cast(result, ctypes.c_void_p))


def _validate_installed_removal_parent() -> Path:
    # The native parent fences Shell's fixed root through Core deletion. Before
    # allowing its maintenance exemption, prove that the child uses that SAME
    # root and that the parent's exclusive kernel gate is actually held. A
    # different APPDATA or portable marker must fail before ordinary data I/O.
    expected = shell_appdata() / PRODUCT_ID
    declared = os.environ.get("APPDATA")
    if not declared or os.path.normcase(str((Path(declared) / PRODUCT_ID).resolve())) != os.path.normcase(str(expected.resolve())):
        raise RuntimeLayoutError("core_removal_root_mismatch")
    marker = Path(sys.executable).parent / "portable.json"
    try:
        io.safe_path(marker)
        if marker.exists():
            raise RuntimeLayoutError("core_removal_layout_unsupported")
        io.safe_path(expected)
        gate = expected / "core-removal.lock"
        io.safe_path(gate)
        if not expected.is_dir() or not gate.is_file():
            raise RuntimeLayoutError("core_removal_parent_missing")
        with io.open_kernel_lock(gate, exclusive=False):
            pass
    except io.StateError as exc:
        if exc.code == "lock_busy":
            return expected
        raise RuntimeLayoutError("core_removal_boundary_invalid") from None
    except OSError:
        raise RuntimeLayoutError("core_removal_boundary_invalid") from None
    raise RuntimeLayoutError("core_removal_parent_missing")


def _json_document(path: Path, limit: int, reason: str) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value

    try:
        value = json.loads(io.read_bytes(path, limit), object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, OSError, io.StateError, RecursionError):
        raise RuntimeLayoutError(reason) from None


def filesystem_name(path: Path) -> str:
    """Query the actual Windows volume, not drive-letter or path heuristics."""
    if os.name != "nt":
        raise RuntimeLayoutError("portable_platform_unverified")
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    volume_path = kernel.GetVolumePathNameW
    volume_path.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
    volume_path.restype = w.BOOL
    volume_info = kernel.GetVolumeInformationW
    volume_info.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
    volume_info.restype = w.BOOL
    root = ctypes.create_unicode_buffer(32768)
    filesystem = ctypes.create_unicode_buffer(256)
    if not volume_path(str(path), root, len(root)) or not volume_info(root.value, None, 0, None, None, None, filesystem, len(filesystem)):
        raise RuntimeLayoutError("volume_information_unavailable")
    return filesystem.value


def _root_identity(root: Path) -> str:
    try:
        io.safe_path(root)
        root.mkdir(parents=True, exist_ok=True)
        io.safe_path(root)
        with io.open_kernel_lock(root / "runtime-layout.lock"):
            identity = root / "data-root.json"
            if identity.exists():
                doc = _json_document(identity, 4096, "data_root_identity_invalid")
                if (
                    set(doc) != {"format_version", "product_id", "data_root_id"}
                    or type(doc["format_version"]) is not int
                    or doc["format_version"] != 1
                    or doc["product_id"] != PRODUCT_ID
                    or not isinstance(doc["data_root_id"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", doc["data_root_id"])
                ):
                    raise RuntimeLayoutError("data_root_identity_invalid")
            else:
                # safe_path also rejects a dangling link/reparse point here.
                io.safe_path(identity)
                doc = {"format_version": 1, "product_id": PRODUCT_ID, "data_root_id": uuid.uuid4().hex}
                io.atomic_write(identity, json.dumps(doc, sort_keys=True).encode())
            # Validate write permission on every startup, including moves/updates.
            probe = root / (".layout-write-" + uuid.uuid4().hex)
            with probe.open("xb") as stream:
                stream.write(b"generated layout write probe")
            probe.unlink()
            return doc["data_root_id"]
    except RuntimeLayoutError:
        raise
    except io.StateError as exc:
        if exc.code == "unsafe_path":
            raise RuntimeLayoutError("data_root_boundary_invalid") from None
        raise RuntimeLayoutError("data_root_unavailable") from None
    except OSError:
        raise RuntimeLayoutError("data_root_not_writable") from None


@dataclass(frozen=True)
class RuntimeLayout:
    executable: Path
    data_root: Path
    mode: str
    data_root_id: str
    product_id: str = PRODUCT_ID

    @classmethod
    def discover(cls, executable: Path, *, appdata: Path | None = None, for_core_removal: bool = False) -> RuntimeLayout:
        if for_core_removal and not _closed_removal_entry():
            raise RuntimeLayoutError("core_removal_entry_required")
        exe = Path(executable).absolute()
        try:
            io.safe_path(exe)
            io.safe_path(exe.parent / "portable.json")
        except (io.StateError, OSError):
            raise RuntimeLayoutError("portable_boundary_invalid") from None
        marker = exe.parent / "portable.json"
        if marker.exists():
            doc = _json_document(marker, 4096, "portable_marker_invalid")
            expected = {"format_version": 1, "product_id": PRODUCT_ID, "data": "data"}
            if type(doc.get("format_version")) is not int or doc != expected:
                raise RuntimeLayoutError("portable_marker_invalid")
            if filesystem_name(exe.parent) != "NTFS":
                raise RuntimeLayoutError("portable_requires_ntfs")
            root, mode = exe.parent / "data", "portable"
        else:
            base = appdata if appdata is not None else os.environ.get("APPDATA")
            if base is None or not Path(base).is_absolute():
                raise RuntimeLayoutError("appdata_unavailable")
            root, mode = Path(base) / PRODUCT_ID, "installed"
        if for_core_removal:
            identity = _root_identity(root)
        else:
            # Fail before identity/probe/config I/O while another installed Core
            # is being removed, including when this executable is another copy.
            with _removal_gate(root):
                identity = _root_identity(root)
        return cls(exe, root, mode, identity)

    def acquire_import_gate(self) -> io.KernelLock:
        """Fence Core deletion, without self-pinning ordinary data during import.

        Only the closed import process uses this gate; it never loads Config,
        factories, settings drafts or Workers. Actual writes/recovery still
        acquire the importer's exclusive data-access lock after confirmation.
        """
        return _removal_gate(self.data_root)

    def acquire_session(self, *, for_core_removal: bool = False) -> RuntimeSession:
        """Pin ordinary data access; exclusive import waits for natural exit."""
        if for_core_removal and not _closed_removal_entry():
            raise RuntimeLayoutError("core_removal_entry_required")
        removal = None if for_core_removal else _removal_gate(self.data_root)
        try:
            lock = io.open_kernel_lock(self.data_root / "data-access.lock", exclusive=False)
            try:
                pending = self.data_root / "data-import/pending.json"
                io.safe_path(pending)
                if pending.exists():
                    raise RuntimeLayoutError("data_import_recovery_required")
            except BaseException:
                lock.close()
                raise
            return RuntimeSession(lock, removal)
        except BaseException as exc:
            if removal is not None:
                removal.close()
            if not isinstance(exc, io.StateError):
                raise
            reason = "data_root_in_use" if exc.code == "lock_busy" else "data_root_unavailable"
            raise RuntimeLayoutError(reason) from None

    @property
    def path_identity(self) -> str:
        canonical = str(self.data_root.resolve())
        if os.name == "nt":
            canonical = canonical.casefold()
        return hashlib.sha256(canonical.encode()).hexdigest()

    def credential_namespace(self, owner: str, instance: str) -> str:
        official_feature(owner)
        return "dsh-pet/root-v2/" + self.data_root_id + "/" + hashlib.sha256((owner + "\0" + instance).encode()).hexdigest()


def current_layout() -> RuntimeLayout | None:
    return _current_layout


def initialize_for_current_build(*, for_core_removal: bool = False) -> RuntimeLayout | None:
    """Run before Config, resource discovery, locks, IPC or settings imports."""
    global _current_layout, _runtime_session, _runtime_removal_mode
    if for_core_removal and not _closed_removal_entry():
        raise RuntimeLayoutError("core_removal_entry_required")
    if _current_layout is not None:
        if _runtime_removal_mode != for_core_removal:
            raise RuntimeLayoutError("core_removal_entry_required")
        return _current_layout
    try:
        from build_variant import VARIANT
    except ImportError:
        return None  # legacy/source builds have their own established layout
    if VARIANT != "core-webm":
        return None
    expected = _validate_installed_removal_parent() if for_core_removal else None
    layout = RuntimeLayout.discover(Path(sys.executable), for_core_removal=for_core_removal)
    if expected is not None and (layout.mode != "installed" or layout.data_root != expected):
        raise RuntimeLayoutError("core_removal_root_mismatch")
    _runtime_session = layout.acquire_session(for_core_removal=for_core_removal)
    _runtime_removal_mode = for_core_removal
    # Keep the non-inheritable kernel handle until natural process death; a UI
    # close does not prove that all session/config writes have stopped.
    _current_layout = layout
    return _current_layout
