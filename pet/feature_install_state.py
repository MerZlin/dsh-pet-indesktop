"""Single install-state authority. No installation, loading or execution here.

Receipts recover metadata commits only. They are not a second active-version
selector, a version lease, or authentication against an attacker who can replace
all local state/evidence. Package trust is always checked separately.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Protocol

from . import feature_state_io as io
from .feature_state_io import StateError

if TYPE_CHECKING:
    from .plugins.package_trust import VerifiedFeatureDescriptor

FEATURE_ID = "official.screen-understanding"
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_RECORDS = 4096  # No automatic evidence pruning; maintenance is a later design.
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_OPERATION = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_STATE_FIELDS = {"schema_version", "feature_id", "revision", "versions", "active", "previous", "enabled", "pending_transaction"}
_RECORD_FIELDS = {"schema_version", "operation_id", "request_digest", "before", "after", "before_digest", "after_digest", "phase"}


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StateError("corrupt")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StateError("corrupt")


def _decode(data: bytes) -> dict:
    try:
        obj = json.loads(data, object_pairs_hook=_unique, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise StateError("corrupt") from None
    if not isinstance(obj, dict):
        raise StateError("corrupt")
    return obj


def _operation(value: object) -> bool:
    return isinstance(value, str) and bool(_OPERATION.fullmatch(value))


@dataclass(frozen=True)
class StateChange:
    versions: Mapping[str, str]
    active: str | None = None
    previous: str | None = None
    enabled: bool = False
    pending_transaction: str | None = None


@dataclass(frozen=True)
class InstallState:
    revision: int
    versions: Mapping[str, str]
    active: str | None
    previous: str | None
    enabled: bool
    pending_transaction: str | None

    def document(self) -> dict:
        return dict(
            schema_version=1,
            feature_id=FEATURE_ID,
            revision=self.revision,
            versions=dict(self.versions),
            active=self.active,
            previous=self.previous,
            enabled=self.enabled,
            pending_transaction=self.pending_transaction,
        )


def _state(obj: dict) -> InstallState:
    schema = obj.get("schema_version")
    if type(schema) is int and schema > 1:
        raise StateError("unsupported_schema")
    if set(obj) != _STATE_FIELDS or type(schema) is not int or schema != 1 or obj["feature_id"] != FEATURE_ID:
        raise StateError("corrupt")
    revision, versions = obj["revision"], obj["versions"]
    if type(revision) is not int or not 0 <= revision < 2**63 or not isinstance(versions, dict) or len(versions) > 256:
        raise StateError("corrupt")
    for version, digest in versions.items():
        if not isinstance(version, str) or not _VERSION.fullmatch(version) or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise StateError("corrupt")
    for key in ("active", "previous"):
        if obj[key] is not None and (not isinstance(obj[key], str) or obj[key] not in versions):
            raise StateError("corrupt")
    if type(obj["enabled"]) is not bool or (obj["enabled"] and obj["active"] is None):
        raise StateError("corrupt")
    if obj["pending_transaction"] is not None and not _operation(obj["pending_transaction"]):
        raise StateError("corrupt")
    return InstallState(revision, MappingProxyType(dict(versions)), obj["active"], obj["previous"], obj["enabled"], obj["pending_transaction"])


def _fresh() -> InstallState:
    return InstallState(0, MappingProxyType({}), None, None, False, None)


@dataclass(frozen=True)
class StateResult:
    status: str
    state: InstallState | None = None
    reason: str | None = None


def _result(state: InstallState) -> StateResult:
    return StateResult("uninstalled" if not state.versions else "enabled" if state.enabled else "disabled", state)


@dataclass(frozen=True)
class CommitReceipt:
    operation_id: str
    previous_revision: int
    revision: int
    request_digest: str
    state_digest: str


@dataclass(frozen=True)
class VerifiedResolution:
    status: str
    revision: int | None = None
    descriptor: VerifiedFeatureDescriptor | None = None
    reason: str | None = None
    purpose: str = "execution"
    permit: object | None = None


class PackageVerifier(Protocol):
    def verify(self, root: Path) -> VerifiedFeatureDescriptor: ...


@dataclass(frozen=True)
class _Record:
    operation_id: str
    request_digest: str
    before: InstallState
    after: InstallState
    phase: str

    def document(self, phase: str | None = None) -> dict:
        return dict(
            schema_version=1,
            operation_id=self.operation_id,
            request_digest=self.request_digest,
            before=self.before.document(),
            after=self.after.document(),
            before_digest=_digest(_json(self.before.document())),
            after_digest=_digest(_json(self.after.document())),
            phase=phase or self.phase,
        )

    def receipt(self) -> CommitReceipt:
        return CommitReceipt(self.operation_id, self.before.revision, self.after.revision, self.request_digest, _digest(_json(self.after.document())))


def _request(state: InstallState, expected_revision: int) -> str:
    doc = state.document()
    doc.pop("revision")
    return _digest(_json({"expected_revision": expected_revision, "change": doc}))


def _record(obj: dict) -> _Record:
    if type(obj.get("schema_version")) is int and obj["schema_version"] > 1:
        raise StateError("unsupported_schema")
    if set(obj) != _RECORD_FIELDS or type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        raise StateError("corrupt")
    if not _operation(obj["operation_id"]) or obj["phase"] not in ("prepared", "committed", "aborted"):
        raise StateError("corrupt")
    if not isinstance(obj["before"], dict) or not isinstance(obj["after"], dict):
        raise StateError("corrupt")
    before, after = _state(obj["before"]), _state(obj["after"])
    if (
        after.revision != before.revision + 1
        or _digest(_json(before.document())) != obj["before_digest"]
        or _digest(_json(after.document())) != obj["after_digest"]
        or _request(after, before.revision) != obj["request_digest"]
    ):
        raise StateError("corrupt")
    return _Record(obj["operation_id"], obj["request_digest"], before, after, obj["phase"])


def _latest_known(records: list[_Record]) -> InstallState | None:
    """Before snapshots constrain recovery, but never authorize restoring state."""
    known: dict[int, InstallState] = {}
    for record in records:
        snapshots = (record.before, record.after) if record.phase == "committed" else (record.before,)
        for snapshot in snapshots:
            if snapshot.revision in known and known[snapshot.revision] != snapshot:
                raise StateError("evidence_conflict")
            known[snapshot.revision] = snapshot
    return known[max(known)] if known else None


class FeatureInstallStateStore:
    """Synchronous internal API: callers schedule filesystem work off the GUI.

    read/recover classify failures; commit raises StateError with a safe code.
    A historical receipt is not a statement that its version is still active.
    resolve_verified returns a revision-bound snapshot, NOT a lease or permission
    to import code later without rechecking with the future lifecycle coordinator.
    """

    def __init__(self, data_root: Path):
        self.root = Path(data_root).absolute() / "plugins" / FEATURE_ID
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / "locks/state.lock"
        self.transactions = self.root / "transactions"
        self.frontier_path = self.transactions / "commit-frontier.json"

    def _record_path(self, operation: str) -> Path:
        return self.transactions / f"op-{operation}.json"

    def _records(self) -> list[_Record]:
        io.safe_path(self.transactions)
        if not self.transactions.exists():
            return []
        records: list[_Record] = []
        for path in self.transactions.iterdir():
            if path == self.frontier_path or re.fullmatch(r"tx-[a-f0-9]{32}\.json", path.name):
                # Feature-package journals share this directory but are not state receipts.
                continue
            # Interrupted exclusive temporary writes cannot be commit proof.
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            if path.name.startswith("corrupt-") and path.suffix == ".bin":
                continue
            if len(records) >= MAX_RECORDS:
                raise StateError("metadata_limit")
            record = _record(_decode(io.read_bytes(path, MAX_DOCUMENT_BYTES)))
            if path != self._record_path(record.operation_id):
                raise StateError("corrupt")
            records.append(record)
        committed = sorted((r for r in records if r.phase == "committed"), key=lambda r: r.after.revision)
        for left, right in zip(committed, committed[1:]):
            if left.after != right.before:
                raise StateError("evidence_conflict")
        _latest_known(records)  # Even an aborted operation proves its before revision existed.
        return records

    def _frontier(self, records: list[_Record]) -> _Record | None:
        """Evidence of the last attempt, NOT another active-version authority.

        Persisted before replacing state. A missing final receipt must not make
        an older committed snapshot look like the newest recoverable state.
        """
        try:
            obj = _decode(io.read_bytes(self.frontier_path, MAX_DOCUMENT_BYTES))
        except FileNotFoundError:
            if any(r.phase == "committed" for r in records):
                raise StateError("recovery_required") from None
            return None
        if type(obj.get("schema_version")) is int and obj["schema_version"] > 1:
            raise StateError("unsupported_schema")
        if set(obj) != {"schema_version", "operation_id", "request_digest"} or type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise StateError("corrupt")
        record = next((r for r in records if r.operation_id == obj["operation_id"]), None)
        if record is None:
            raise StateError("recovery_required")
        if record.request_digest != obj["request_digest"] or any(r.phase == "committed" and r.after.revision > record.after.revision for r in records):
            raise StateError("evidence_conflict")
        return record

    def _current(self) -> StateResult:
        io.safe_path(self.root)
        io.safe_path(self.state_path)
        try:
            raw = io.read_bytes(self.state_path, MAX_DOCUMENT_BYTES)
        except FileNotFoundError:
            # locks alone are not evidence of an installation (failed first CAS).
            if self.root.exists() and any(p.name != "locks" for p in self.root.iterdir()):
                return StateResult("recovery_required", reason="missing_state_with_traces")
            return _result(_fresh())
        return _result(_state(_decode(raw)))

    def _read(self) -> StateResult:
        current = self._current()
        if current.state is None:
            return current
        records = self._records()
        self._frontier(records)
        if any(r.phase == "prepared" for r in records):
            return StateResult("recovery_required", reason="unfinished_commit")
        latest = _latest_known(records)
        if latest is not None:
            if latest.revision > current.state.revision:
                return StateResult("recovery_required", reason="state_behind_receipt")
            if latest.revision == current.state.revision and latest != current.state:
                return StateResult("corrupt", reason="evidence_conflict")
        return current

    def read(self) -> StateResult:
        try:
            with io.state_lock(self.lock_path, create=False, exclusive=False):
                return self._read()
        except StateError as exc:
            return StateResult(exc.code)
        except OSError:
            return StateResult("io_error")

    def commit(self, change: StateChange, *, expected_revision: int, operation_id: str) -> CommitReceipt:
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**63 - 1 or not _operation(operation_id):
            raise StateError("invalid_request")
        try:
            target = _state(
                dict(
                    schema_version=1,
                    feature_id=FEATURE_ID,
                    revision=expected_revision + 1,
                    versions=dict(change.versions),
                    active=change.active,
                    previous=change.previous,
                    enabled=change.enabled,
                    pending_transaction=change.pending_transaction,
                )
            )
        except (StateError, ValueError, TypeError, AttributeError):
            raise StateError("invalid_request") from None
        request = _request(target, expected_revision)
        try:
            with io.state_lock(self.lock_path, timeout=1.0):
                records = self._records()
                for record in records:
                    if record.operation_id == operation_id:
                        if record.request_digest != request:
                            raise StateError("operation_conflict")
                        if record.phase == "committed":
                            return record.receipt()
                        raise StateError("recovery_required" if record.phase == "prepared" else "operation_aborted")
                current = self._read()
                if current.state is None:
                    raise StateError(current.status)
                if current.state.revision != expected_revision:
                    raise StateError("revision_conflict")
                committed = [r for r in records if r.phase == "committed"]
                if committed and max(committed, key=lambda r: r.after.revision).after != current.state:
                    # Preserve a newer valid authority, but do not extend an evidence
                    # chain with an unexplained gap and then claim it is recoverable.
                    raise StateError("recovery_required")
                if len(records) >= MAX_RECORDS:
                    raise StateError("metadata_limit")
                record = _Record(operation_id, request, current.state, target, "prepared")
                io.atomic_write(self._record_path(operation_id), _json(record.document()))
                io.atomic_write(self.frontier_path, _json(dict(schema_version=1, operation_id=operation_id, request_digest=request)))
                io.atomic_write(self.state_path, _json(target.document()))
                io.atomic_write(self._record_path(operation_id), _json(record.document("committed")))
                return record.receipt()
        except OSError:
            raise StateError("io_error") from None

    def recover(self) -> StateResult:
        # A truly fresh read or future schema must not create repair artifacts.
        original = self.read()
        if original.status in ("unsupported_schema", "io_error", "lock_busy", "unsafe_path") or (original.state and original.state.revision == 0):
            return original
        try:
            with io.state_lock(self.lock_path):
                try:
                    current = self._current()
                except StateError as exc:
                    if exc.code == "unsupported_schema":
                        return StateResult(exc.code)
                    current = StateResult(exc.code)
                records = self._records()
                frontier = self._frontier(records)
                prepared = [r for r in records if r.phase == "prepared"]
                if len(prepared) > 1:
                    return StateResult("recovery_required", reason="ambiguous_commits")
                for record in prepared:
                    if current.state == record.after:
                        if frontier != record:
                            return StateResult("recovery_required", reason="missing_commit_frontier")
                        phase = "committed"
                    elif current.state == record.before or (not self.state_path.exists() and record.before == _fresh()):
                        phase = "aborted"
                    else:
                        return StateResult("recovery_required", reason="ambiguous_commit")
                    io.atomic_write(self._record_path(record.operation_id), _json(record.document(phase)))
                records = self._records()
                committed = [r for r in records if r.phase == "committed"]
                known = _latest_known(records)
                if current.state is not None and known is not None:
                    if current.state.revision > known.revision:
                        return current
                    if current.state.revision == known.revision:
                        return current if current.state == known else StateResult("corrupt", reason="evidence_conflict")
                if not committed:
                    # A failed first commit must not leave the service permanently
                    # stuck. This only materializes the proven empty predecessor,
                    # never the uncommitted candidate. Repeatable after another crash.
                    if records and not self.state_path.exists() and all(r.phase == "aborted" and r.before == _fresh() for r in records):
                        io.atomic_write(self.state_path, _json(_fresh().document()))
                        return _result(_fresh())
                    return current
                latest = max(committed, key=lambda r: r.after.revision).after
                if known is not None and known.revision > latest.revision:
                    return StateResult("recovery_required", reason="missing_later_commit")
                # Proven latest commit only; a backup or directory is never enough.
                io.safe_path(self.state_path)
                if self.state_path.exists():
                    raw = io.read_bytes(self.state_path, MAX_DOCUMENT_BYTES)
                    io.atomic_write(self.transactions / f"corrupt-{uuid.uuid4().hex}.bin", raw)
                io.atomic_write(self.state_path, _json(latest.document()))
                return _result(latest)
        except StateError as exc:
            return StateResult(exc.code)
        except OSError:
            return StateResult("io_error")

    def resolve_verified(self, verifier: PackageVerifier, *, purpose: str = "execution", permit=None) -> VerifiedResolution:
        initial = self.read()
        state = initial.state
        from .feature_startup_contract import StartupLoadPermit

        if purpose not in ("execution", "configuration", "startup"):
            return VerifiedResolution("rejected", reason="resolution_purpose_invalid")
        allowed = ("enabled",) if purpose == "execution" else ("enabled", "disabled")
        if initial.status not in allowed or state is None or state.active is None:
            return VerifiedResolution(initial.status, state.revision if state else None, reason=initial.reason)
        if purpose == "startup":
            if not isinstance(permit, StartupLoadPermit) or not permit.matches(self.root, state):
                return VerifiedResolution("recovery_required", state.revision, reason="startup_permit_required")
        elif state.pending_transaction is not None:
            return VerifiedResolution("recovery_required", state.revision, reason="pending_transaction")
        version_root = self.root / "versions" / state.active
        try:
            io.safe_path(version_root)
            descriptor = verifier.verify(version_root)
            if (
                descriptor.id != FEATURE_ID
                or descriptor.version != state.active
                or descriptor.root != version_root
                or descriptor.trust_status != "trusted_official"
                or _digest(descriptor.raw_manifest) != state.versions[state.active]
            ):
                return VerifiedResolution("verification_failed", state.revision)
        except Exception:
            # Verifier exceptions may carry raw paths or package-supplied text.
            return VerifiedResolution("verification_failed", state.revision)
        try:
            with io.state_lock(self.lock_path, exclusive=False):
                final = self._read()
                if final.state != state:
                    return VerifiedResolution("revision_conflict", state.revision)
                return VerifiedResolution("resolved", state.revision, descriptor, purpose=purpose, permit=permit)
        except StateError as exc:
            return VerifiedResolution(exc.code, state.revision)
