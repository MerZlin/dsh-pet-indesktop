"""Cross-process version leases for installed official Feature packages.

The install-state document selects a version; this module owns the stronger
lifetime guarantee that prevents deletion while Core, Settings, or a Worker
still uses that version. JSON records are bounded diagnostics and hand-off
metadata. OS locks, not PID/TTL/heartbeat fields, are the liveness authority.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from . import feature_state_io as io
from .feature_install_state import FeatureInstallStateStore
from .feature_state_io import StateError

if TYPE_CHECKING:
    pass

__all__ = [
    "CrossProcessLease",
    "FeatureVersionLeaseCoordinator",
    "FeatureVersionSelection",
    "LeaseError",
    "LeaseInfo",
    "LeaseOccupancy",
    "WorkerReservation",
    "retain_process_lease",
    "release_process_leases",
]

_SCHEMA_VERSION = 1
_MAX_RECORD_BYTES = 16 * 1024
_MAX_RECORDS = 4096
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_LEASE_ID = re.compile(r"[a-f0-9]{32}\Z")
_KINDS = frozenset({"host", "settings", "worker_reservation", "worker"})
_PHASES = frozenset({"active", "reserved", "claimed"})
_FIELDS = frozenset(
    {
        "schema_version",
        "lease_id",
        "feature_id",
        "version",
        "revision",
        "manifest_digest",
        "kind",
        "phase",
        "owner_pid",
        "owner_identity",
        "created_at_ns",
        "token_digest",
        "parent_lease_id",
        "child_lease_id",
    }
)


class LeaseError(StateError):
    """Safe lease reason code without local paths or untrusted contents."""


@dataclass(frozen=True, slots=True)
class FeatureVersionSelection:
    """Revision-bound, already verified package selection."""

    feature_id: str
    version: str
    revision: int
    manifest_digest: str
    descriptor: Any
    purpose: str = "execution"
    permit: Any = None

    @classmethod
    def from_resolution(cls, resolution: Any) -> "FeatureVersionSelection":
        if getattr(resolution, "status", None) != "resolved" or resolution.descriptor is None or resolution.revision is None:
            raise LeaseError("selection_unresolved")
        descriptor = resolution.descriptor
        raw_manifest = getattr(descriptor, "raw_manifest", None)
        if not isinstance(raw_manifest, bytes):
            raise LeaseError("selection_invalid")
        return cls(
            str(getattr(descriptor, "id", "")),
            str(getattr(descriptor, "version", "")),
            int(resolution.revision),
            hashlib.sha256(raw_manifest).hexdigest(),
            descriptor,
            getattr(resolution, "purpose", "execution"),
            getattr(resolution, "permit", None),
        )


@dataclass(frozen=True, slots=True)
class LeaseInfo:
    lease_id: str
    feature_id: str
    version: str
    revision: int
    manifest_digest: str
    kind: str
    phase: str
    owner_pid: int
    owner_identity: str
    child_lease_id: str | None = None


@dataclass(frozen=True, slots=True)
class LeaseOccupancy:
    status: str
    version: str
    revision: int | None
    leases: tuple[LeaseInfo, ...] = ()
    reason: str | None = None
    cleaned_records: int = 0

    @property
    def can_remove(self) -> bool:
        return self.status == "free"


_OWNER_IDENTITY = uuid.uuid4().hex
_PROCESS_LEASES: list["CrossProcessLease"] = []
_PROCESS_LEASES_LOCK = threading.RLock()


def process_owner_identity() -> str:
    """Endpoint binding only; kernel lease locks remain liveness authority."""
    return _OWNER_IDENTITY


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LeaseError("record_corrupt")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise LeaseError("record_corrupt")


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise LeaseError("record_corrupt") from None
    if not isinstance(value, dict):
        raise LeaseError("record_corrupt")
    return value


def _record_from_document(value: dict[str, object]) -> dict[str, object]:
    if set(value) != _FIELDS or value.get("schema_version") != _SCHEMA_VERSION:
        raise LeaseError("record_corrupt")
    lease_id = value.get("lease_id")
    feature_id = value.get("feature_id")
    version = value.get("version")
    revision = value.get("revision")
    digest = value.get("manifest_digest")
    kind = value.get("kind")
    phase = value.get("phase")
    pid = value.get("owner_pid")
    identity = value.get("owner_identity")
    created = value.get("created_at_ns")
    token_digest = value.get("token_digest")
    parent_id = value.get("parent_lease_id")
    child_id = value.get("child_lease_id")
    if (
        not isinstance(lease_id, str)
        or not _LEASE_ID.fullmatch(lease_id)
        or not isinstance(feature_id, str)
        or not feature_id
        or not isinstance(version, str)
        or not _VERSION.fullmatch(version)
        or type(revision) is not int
        or revision < 0
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
        or kind not in _KINDS
        or phase not in _PHASES
        or type(pid) is not int
        or pid <= 0
        or not isinstance(identity, str)
        or not 1 <= len(identity) <= 128
        or type(created) is not int
        or created <= 0
        or (token_digest is not None and (not isinstance(token_digest, str) or not _DIGEST.fullmatch(token_digest)))
        or (parent_id is not None and (not isinstance(parent_id, str) or not _LEASE_ID.fullmatch(parent_id)))
        or (child_id is not None and (not isinstance(child_id, str) or not _LEASE_ID.fullmatch(child_id)))
    ):
        raise LeaseError("record_corrupt")
    if kind == "worker_reservation" and (phase not in {"reserved", "claimed"} or token_digest is None):
        raise LeaseError("record_corrupt")
    if kind != "worker_reservation" and token_digest is not None:
        raise LeaseError("record_corrupt")
    if kind == "worker" and parent_id is None:
        raise LeaseError("record_corrupt")
    if phase == "claimed" and kind == "worker_reservation" and child_id is None:
        raise LeaseError("record_corrupt")
    return value


def _record_info(value: dict[str, object]) -> LeaseInfo:
    return LeaseInfo(
        str(value["lease_id"]),
        str(value["feature_id"]),
        str(value["version"]),
        cast(int, value["revision"]),
        str(value["manifest_digest"]),
        str(value["kind"]),
        str(value["phase"]),
        cast(int, value["owner_pid"]),
        str(value["owner_identity"]),
        value["child_lease_id"] if isinstance(value["child_lease_id"], str) else None,
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unlink(path: Path) -> None:
    try:
        io.safe_path(path)
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        return


class CrossProcessLease:
    """A lease backed by an OS lock and a bounded diagnostic record."""

    def __init__(self, coordinator: "FeatureVersionLeaseCoordinator", record: dict[str, object], lock: io.KernelLock):
        self._coordinator = coordinator
        self._record = dict(record)
        self._lock = lock
        self._closed = False

    @property
    def lease_id(self) -> str:
        return str(self._record["lease_id"])

    @property
    def feature_id(self) -> str:
        return str(self._record["feature_id"])

    @property
    def version(self) -> str:
        return str(self._record["version"])

    @property
    def revision(self) -> int:
        return cast(int, self._record["revision"])

    @property
    def kind(self) -> str:
        return str(self._record["kind"])

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> bool:
        """Release the kernel lock; return whether metadata was also removed."""
        if self._closed:
            return True
        removed = False
        try:
            with io.state_lock(self._coordinator.leases_lock_path):
                self._lock.close()
                current = self._coordinator._read_record_file(self._coordinator._record_path(self.lease_id))
                if current["lease_id"] != self.lease_id:
                    raise LeaseError("record_identity_conflict")
                _unlink(self._coordinator._record_path(self.lease_id))
                _unlink(self._coordinator._lock_path(self.lease_id))
                removed = True
        except (LeaseError, StateError, OSError):
            self._lock.close()
        finally:
            self._closed = True
        return removed

    def __enter__(self) -> "CrossProcessLease":
        if self.closed:
            raise LeaseError("lease_closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class WorkerReservation(CrossProcessLease):
    @property
    def handoff_token(self) -> str:
        token = self._handoff_token
        if token is None:
            raise LeaseError("handoff_unavailable")
        return token

    def __init__(self, coordinator: "FeatureVersionLeaseCoordinator", record: dict[str, object], lock: io.KernelLock, token: str):
        super().__init__(coordinator, record, lock)
        self._handoff_token = token

    def claim_child(self, *, child_pid: int | None = None, child_identity: str | None = None) -> bool:
        return self._coordinator._reservation_claimed(self.lease_id, child_pid=child_pid, child_identity=child_identity)

    def abort_after_confirmed_failure(self) -> bool:
        return self.close()


class FeatureVersionLeaseCoordinator:
    """Cross-process lease authority for one Feature install-state root."""

    def __init__(self, store: FeatureInstallStateStore | Path):
        self.store = store if isinstance(store, FeatureInstallStateStore) else FeatureInstallStateStore(Path(store))
        self.root = self.store.root
        self.leases_dir = self.root / "leases"
        self.leases_lock_path = self.root / "locks/leases.lock"

    @property
    def data_root(self) -> Path:
        return self.root.parent.parent

    def _record_path(self, lease_id: str) -> Path:
        return self.leases_dir / f"{lease_id}.json"

    def _lock_path(self, lease_id: str) -> Path:
        return self.leases_dir / f"{lease_id}.lock"

    def _read_record_file(self, path: Path) -> dict[str, object]:
        return _record_from_document(_decode(io.read_bytes(path, _MAX_RECORD_BYTES)))

    def _validate_selection_shape(self, selection: FeatureVersionSelection) -> None:
        if not isinstance(selection, FeatureVersionSelection):
            raise LeaseError("selection_invalid")
        if (
            selection.feature_id != self.store.feature_id
            or not _VERSION.fullmatch(selection.version)
            or selection.revision < 0
            or not _DIGEST.fullmatch(selection.manifest_digest)
        ):
            raise LeaseError("selection_invalid")
        descriptor = selection.descriptor
        raw_manifest = getattr(descriptor, "raw_manifest", None)
        if (
            getattr(descriptor, "id", None) != selection.feature_id
            or getattr(descriptor, "version", None) != selection.version
            or getattr(descriptor, "trust_status", None) not in {"trusted_official", "local_user"}
            or not isinstance(raw_manifest, bytes)
            or hashlib.sha256(raw_manifest).hexdigest() != selection.manifest_digest
        ):
            raise LeaseError("selection_invalid")
        expected_root = (self.root / "versions" / selection.version).resolve()
        try:
            actual_root = Path(getattr(descriptor, "root")).resolve()
        except (TypeError, ValueError, OSError):
            raise LeaseError("selection_invalid") from None
        if actual_root != expected_root:
            raise LeaseError("selection_invalid")

    def _validate_current_state(self, selection: FeatureVersionSelection) -> None:
        result = self.store.read()
        state = result.state
        from .feature_startup_contract import StartupLoadPermit

        if selection.purpose not in ("execution", "configuration", "startup"):
            raise LeaseError("selection_invalid")
        statuses = ("enabled",) if selection.purpose == "execution" else ("enabled", "disabled")
        if result.status not in statuses or state is None:
            raise LeaseError("feature_not_enabled")
        if selection.purpose == "startup":
            if not isinstance(selection.permit, StartupLoadPermit) or not selection.permit.matches(self.root, state):
                raise LeaseError("startup_permit_required")
        elif state.pending_transaction is not None:
            raise LeaseError("recovery_required")
        if state.revision != selection.revision or state.active != selection.version:
            raise LeaseError("revision_conflict")
        if state.versions.get(selection.version) != selection.manifest_digest:
            raise LeaseError("digest_conflict")

    def _new_record(self, selection: FeatureVersionSelection, kind: str, *, token_digest: str | None = None, parent_id: str | None = None) -> dict[str, object]:
        if kind not in _KINDS:
            raise LeaseError("kind_invalid")
        phase = "reserved" if kind == "worker_reservation" else "active"
        lease_id = uuid.uuid4().hex
        return {
            "schema_version": _SCHEMA_VERSION,
            "lease_id": lease_id,
            "feature_id": selection.feature_id,
            "version": selection.version,
            "revision": selection.revision,
            "manifest_digest": selection.manifest_digest,
            "kind": kind,
            "phase": phase,
            "owner_pid": os.getpid(),
            "owner_identity": _OWNER_IDENTITY,
            "created_at_ns": time.time_ns(),
            "token_digest": token_digest,
            "parent_lease_id": parent_id,
            "child_lease_id": None,
        }

    def _acquire(self, selection: FeatureVersionSelection, kind: str) -> CrossProcessLease:
        self._validate_selection_shape(selection)
        if kind == "worker_reservation" and selection.purpose != "execution":
            raise LeaseError("execution_purpose_required")
        token = secrets.token_urlsafe(32) if kind == "worker_reservation" else None
        with io.state_lock(self.leases_lock_path):
            self._validate_current_state(selection)
            record = self._new_record(selection, kind, token_digest=_token_digest(token) if token else None)
            lease_id = str(record["lease_id"])
            lock = io.open_kernel_lock(self._lock_path(lease_id))
            try:
                io.atomic_write(self._record_path(lease_id), _json(record))
            except BaseException:
                lock.close()
                _unlink(self._lock_path(lease_id))
                raise
        if kind == "worker_reservation":
            assert token is not None
            return WorkerReservation(self, record, lock, token)
        return CrossProcessLease(self, record, lock)

    def acquire_host(self, selection: FeatureVersionSelection) -> CrossProcessLease:
        return self._acquire(selection, "host")

    def acquire_settings(self, selection: FeatureVersionSelection) -> CrossProcessLease:
        return self._acquire(selection, "settings")

    def reserve_worker(self, selection: FeatureVersionSelection) -> WorkerReservation:
        if getattr(selection.descriptor, "execution_kind", None) != "host-worker":
            raise LeaseError("host_only_has_no_worker")
        result = self._acquire(selection, "worker_reservation")
        assert isinstance(result, WorkerReservation)
        return result

    def verify_current_selection(self, selection: FeatureVersionSelection) -> bool:
        """Re-check a revision-bound request immediately before execution.

        The returned boolean is intentionally boring: callers should treat any
        exception as a hard authorization failure and must not silently select a
        different version.  Keeping this seam on the coordinator makes legacy
        QAction/request/result paths able to revalidate the same identity that
        lease acquisition uses.
        """
        if selection.purpose != "execution":
            raise LeaseError("execution_purpose_required")
        self._validate_selection_shape(selection)
        self._validate_current_state(selection)
        return True

    def is_current_selection(self, selection: FeatureVersionSelection) -> bool:
        """Return ``False`` for every validation failure without fallback."""
        try:
            return self.verify_current_selection(selection)
        except (LeaseError, StateError, OSError, TypeError, ValueError):
            return False

    def claim_worker(self, token: str) -> CrossProcessLease:
        if not isinstance(token, str) or not 32 <= len(token) <= 256:
            raise LeaseError("handoff_invalid")
        digest = _token_digest(token)
        with io.state_lock(self.leases_lock_path):
            io.safe_path(self.leases_dir)
            matches: list[dict[str, object]] = []
            paths = sorted(self.leases_dir.glob("*.json")) if self.leases_dir.exists() else ()
            for path in paths:
                try:
                    record = self._read_record_file(path)
                except (LeaseError, StateError, OSError):
                    raise LeaseError("pending_confirmation") from None
                if record["kind"] == "worker_reservation" and record["token_digest"] == digest:
                    matches.append(record)
            if len(matches) != 1:
                raise LeaseError("handoff_unavailable")
            reservation = matches[0]
            if reservation["phase"] != "reserved":
                raise LeaseError("handoff_already_claimed")
            selection = FeatureVersionSelection.__new__(FeatureVersionSelection)
            object.__setattr__(selection, "feature_id", reservation["feature_id"])
            object.__setattr__(selection, "version", reservation["version"])
            object.__setattr__(selection, "revision", reservation["revision"])
            object.__setattr__(selection, "manifest_digest", reservation["manifest_digest"])
            object.__setattr__(selection, "descriptor", None)
            self._validate_current_identity(selection)
            child = self._new_record(selection, "worker", parent_id=str(reservation["lease_id"]))
            child_id = str(child["lease_id"])
            child_lock = io.open_kernel_lock(self._lock_path(child_id))
            try:
                io.atomic_write(self._record_path(child_id), _json(child))
                reservation["phase"] = "claimed"
                reservation["child_lease_id"] = child_id
                io.atomic_write(self._record_path(str(reservation["lease_id"])), _json(reservation))
            except BaseException:
                child_lock.close()
                _unlink(self._record_path(child_id))
                _unlink(self._lock_path(child_id))
                raise
            return CrossProcessLease(self, child, child_lock)

    def _validate_current_identity(self, selection: FeatureVersionSelection) -> None:
        result = self.store.read()
        state = result.state
        if result.status != "enabled" or state is None or state.pending_transaction is not None:
            raise LeaseError("feature_not_enabled")
        if state.revision != selection.revision or state.active != selection.version:
            raise LeaseError("revision_conflict")
        if state.versions.get(selection.version) != selection.manifest_digest:
            raise LeaseError("digest_conflict")

    def _reservation_claimed(self, lease_id: str, *, child_pid: int | None, child_identity: str | None) -> bool:
        with io.state_lock(self.leases_lock_path):
            try:
                record = self._read_record_file(self._record_path(lease_id))
            except (LeaseError, StateError, OSError):
                return False
            if record["kind"] != "worker_reservation" or record["phase"] != "claimed" or not record["child_lease_id"]:
                return False
            child_path = self._record_path(str(record["child_lease_id"]))
            try:
                child = self._read_record_file(child_path)
            except (LeaseError, StateError, OSError):
                return False
            if child["kind"] != "worker" or child["parent_lease_id"] != lease_id:
                return False
            if child_pid is not None and child["owner_pid"] != child_pid:
                return False
            if child_identity is not None and child["owner_identity"] != child_identity:
                return False
            return True

    def inspect_occupancy(self, version: str, revision: int | None = None) -> LeaseOccupancy:
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            return LeaseOccupancy("pending_confirmation", version, revision, reason="version_invalid")
        try:
            with io.state_lock(self.leases_lock_path):
                return self._inspect_occupancy_locked(version, revision)
        except (StateError, OSError) as exc:
            reason = exc.code if isinstance(exc, StateError) else "io_error"
            return LeaseOccupancy("pending_confirmation", version, revision, reason=reason)

    @contextmanager
    def management_guard(self, versions):
        """Freeze lease admission while a manager rechecks and commits/removes.

        Lock ordering is management -> leases -> state, matching acquisition's
        leases -> state ordering. The yielded occupancy includes every revision.
        """
        with io.state_lock(self.leases_lock_path):
            occupancy = {}
            for version in versions:
                if not isinstance(version, str) or not _VERSION.fullmatch(version):
                    raise StateError("version_invalid")
                occupancy[version] = self._inspect_occupancy_locked(version, None)
            yield occupancy

    def _inspect_occupancy_locked(self, version: str, revision: int | None) -> LeaseOccupancy:
        io.safe_path(self.leases_dir)
        if not self.leases_dir.exists():
            return LeaseOccupancy("free", version, revision)
        records: list[dict[str, object]] = []
        for path in sorted(self.leases_dir.glob("*.json")):
            try:
                record = self._read_record_file(path)
            except (LeaseError, StateError, OSError):
                return LeaseOccupancy("pending_confirmation", version, revision, reason="record_corrupt")
            if path.stem != record["lease_id"]:
                return LeaseOccupancy("pending_confirmation", version, revision, reason="record_identity_conflict")
            records.append(record)
        json_ids = {str(record["lease_id"]) for record in records}
        orphan_locks = [path for path in self.leases_dir.glob("*.lock") if path.stem not in json_ids]
        if orphan_locks:
            return LeaseOccupancy("pending_confirmation", version, revision, reason="orphan_lock")
        active: list[LeaseInfo] = []
        cleaned = 0
        uncertain = False
        uncertainty_reason: str | None = None
        for record in records:
            if record["feature_id"] != self.store.feature_id or record["version"] != version or (revision is not None and record["revision"] != revision):
                continue
            lease_id = str(record["lease_id"])
            try:
                probe = io.open_kernel_lock(self._lock_path(lease_id), create=False)
            except StateError as exc:
                if exc.code == "lock_busy":
                    active.append(_record_info(record))
                    continue
                return LeaseOccupancy("pending_confirmation", version, revision, tuple(active), reason=exc.code, cleaned_records=cleaned)
            except OSError:
                return LeaseOccupancy("pending_confirmation", version, revision, tuple(active), reason="io_error", cleaned_records=cleaned)
            probe.close()
            # A released, still-reserved parent may be between native
            # process creation and child bootstrap.  Its record is not
            # evidence of liveness, but removing it would race a child
            # that still holds the one-time token.  Only an explicit
            # owner-side abort may clear this phase.
            if record["kind"] == "worker_reservation" and record["phase"] == "reserved":
                uncertain = True
                uncertainty_reason = "unconfirmed_reservation"
                continue
            _unlink(self._record_path(lease_id))
            _unlink(self._lock_path(lease_id))
            cleaned += 1
        if active:
            return LeaseOccupancy("occupied", version, revision, tuple(active), cleaned_records=cleaned)
        if uncertain:
            return LeaseOccupancy("pending_confirmation", version, revision, reason=uncertainty_reason, cleaned_records=cleaned)
        return LeaseOccupancy("free", version, revision, cleaned_records=cleaned)

    def can_remove(self, version: str, revision: int | None = None) -> bool:
        return self.inspect_occupancy(version, revision).can_remove


def retain_process_lease(lease: CrossProcessLease) -> CrossProcessLease:
    """Pin a host lease until explicit process shutdown/atexit."""
    if not isinstance(lease, CrossProcessLease):
        raise TypeError("a CrossProcessLease is required")
    with _PROCESS_LEASES_LOCK:
        if lease not in _PROCESS_LEASES:
            _PROCESS_LEASES.append(lease)
    return lease


def release_process_leases() -> None:
    with _PROCESS_LEASES_LOCK:
        leases = list(reversed(_PROCESS_LEASES))
        _PROCESS_LEASES.clear()
    for lease in leases:
        lease.close()


atexit.register(release_process_leases)
