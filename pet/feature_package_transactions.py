"""Qt-free, journaled local transactions; state.json is the only authority."""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol

from . import feature_package_files as package_files
from . import feature_state_io as io
from .feature_install_state import FeatureInstallStateStore, InstallState, StateChange
from .feature_lifecycle_contract import LifecyclePrepareRequest, authorize_request, request_path
from .feature_startup_contract import StartupLoadPermit
from .feature_state_io import StateError
from .feature_version_lease import FeatureVersionLeaseCoordinator
from .official_features import SCREEN_FEATURE_ID, official_feature
from .plugins.package_trust import FeaturePackageVerifier, PackageVerificationError, VerifiedFeatureDescriptor

# Transient lock contention is retryable, never evidence of a corrupt package.
LOCK_BUSY_REASONS = frozenset({"management_lock_busy", "leases_lock_busy", "state_lock_busy", "lock_busy"})


@contextmanager
def _named_lock(acquire: Callable[[], Any], resource: str) -> Iterator[Any]:
    """Name acquisition failure only; never relabel an exception from its body."""
    with ExitStack() as stack:
        try:
            acquired = stack.enter_context(acquire())
        except StateError as exc:
            if exc.code == "lock_busy":
                raise StateError(resource + "_lock_busy") from exc
            raise
        yield acquired


_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\Z")
_OPERATION = re.compile(r"tx-[a-f0-9]{32}\Z")
_STATUSES = frozenset(
    {"completed", "idempotent", "awaiting_confirmation", "awaiting_release", "awaiting_startup_confirmation", "rejected", "recovery_required", "failed"}
)
_PHASES = frozenset(
    {
        "staged",
        "preflighted",
        "awaiting_confirmation",
        "pending_runtime_release",
        "state_pending",
        "version_persisted",
        "active_committed",
        "awaiting_startup_confirmation",
        "rolling_back",
        "gc_pending",
        "completed",
        "recovery_required",
        "rejected",
    }
)


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json(value) -> bytes:
    return json.dumps(_plain(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decode(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StateError("journal_corrupt")
            result[key] = value
        return result

    def invalid(_value):
        raise StateError("journal_corrupt")

    value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(value, dict):
        raise StateError("journal_corrupt")
    return value


def _error(exc: BaseException) -> str:
    if isinstance(exc, StateError):
        return exc.code
    if isinstance(exc, PackageVerificationError):
        return "package_verification_failed: " + str(exc)
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in (32, 33):
            return "file_in_use"
        if winerror in (225, 226):
            return "antivirus_blocked"
        if winerror in (39, 112):
            return "disk_full"
        if winerror == 5 or exc.errno in (errno.EACCES, errno.EPERM):
            return "permission_denied"
        if exc.errno == errno.ENOSPC:
            return "disk_full"
        return "filesystem_error"
    return "invalid_package: " + type(exc).__name__


@dataclass(frozen=True)
class RuntimePreparation:
    status: str = "ready"
    reason: str | None = None
    blocked_versions: tuple[str, ...] = ()
    details: Mapping[str, object] = field(default_factory=dict)


class RuntimeLifecycle(Protocol):
    def prepare(self, request: LifecyclePrepareRequest) -> RuntimePreparation: ...


class SelfChecker(Protocol):
    def check(self, descriptor: VerifiedFeatureDescriptor) -> RuntimePreparation: ...


@dataclass(frozen=True)
class OperationPlan:
    operation_id: str
    kind: str
    revision: int
    source_type: str | None
    source_path: Path | None
    source_digest: str | None
    staged_root: Path | None
    staged_digest: str | None
    target_version: str | None
    manifest_digest: str | None
    active: str | None
    previous: str | None
    enabled: bool
    versions: Mapping[str, str]
    estimated_bytes: int
    occupancy: Mapping[str, object]
    action_summary: tuple[str, ...]
    rollback_allowed: bool
    delete_versions: tuple[str, ...]
    confirmation_digest: str
    confirmation_token: str = field(repr=False)
    feature_id: str = SCREEN_FEATURE_ID

    def __post_init__(self):
        official_feature(self.feature_id)
        object.__setattr__(self, "versions", _freeze(self.versions))
        object.__setattr__(self, "occupancy", _freeze(self.occupancy))
        object.__setattr__(self, "action_summary", tuple(self.action_summary))
        object.__setattr__(self, "delete_versions", tuple(self.delete_versions))

    def document(self, *, include_token: bool = False) -> dict:
        return {f.name: _plain(getattr(self, f.name)) for f in fields(self) if include_token or f.name != "confirmation_token"}


@dataclass(frozen=True)
class OperationResult:
    status: str
    operation_id: str | None = None
    phase: str | None = None
    plan: OperationPlan | None = None
    revision: int | None = None
    reason: str | None = None
    blocked_versions: tuple[str, ...] = ()
    details: Mapping[str, object] = field(default_factory=dict)
    feature_id: str = SCREEN_FEATURE_ID

    def __post_init__(self):
        official_feature(self.feature_id)
        if self.status not in _STATUSES or self.phase is not None and self.phase not in _PHASES:
            raise ValueError("invalid transaction result")


@dataclass(frozen=True)
class Inspection:
    status: str
    revision: int | None
    active: str | None
    previous: str | None
    enabled: bool
    pending_transaction: str | None
    transactions: tuple[str, ...]
    garbage: tuple[str, ...]
    reason: str | None = None
    feature_id: str = SCREEN_FEATURE_ID


class FeaturePackageTransactionService:
    def _result(self, *args, **kwargs) -> OperationResult:
        if "feature_id" in kwargs:
            raise TypeError("result identity is owned by the service")
        kwargs["feature_id"] = self.store.feature_id
        return OperationResult(*args, **kwargs)

    def __init__(
        self, data_root: Path | str, verifier: FeaturePackageVerifier, *, self_checker: SelfChecker | None = None, runtime: RuntimeLifecycle | None = None
    ):
        self.store = FeatureInstallStateStore(Path(data_root), feature_id=verifier.feature_id)
        self.verifier = verifier
        if self_checker is None:
            from .feature_package_probe import SubprocessFeatureSelfChecker

            self_checker = SubprocessFeatureSelfChecker(verifier)
        self.self_checker = self_checker
        self.runtime = runtime
        self.versions_root = self.store.root / "versions"
        self.staging_root = self.store.root / "staging"
        self.journal_root = self.store.transactions
        self.management_lock_path = self.store.root / "locks" / "management.lock"
        self.leases = FeatureVersionLeaseCoordinator(self.store)
        self._startup_permits: dict[str, StartupLoadPermit] = {}

    def _commit(self, *args, **kwargs):
        try:
            return self.store.commit(*args, **kwargs)
        except StateError as exc:
            if exc.code == "lock_busy":
                raise StateError("state_lock_busy") from exc
            raise

    def _state(self, *, initialize: bool = False) -> InstallState:
        result = self.store.read()
        if result.state is None:
            raise StateError("state_lock_busy" if result.status == "lock_busy" else result.reason or result.status)
        if initialize and not self.store.state_path.exists():
            # The public ledger commit creates both state and predecessor evidence
            # before staging/journals can turn a missing state into corruption.
            self._commit(StateChange({}), expected_revision=result.state.revision, operation_id="bootstrap-" + uuid.uuid4().hex)
            return self._state()
        return result.state

    @staticmethod
    def _live_owners(occupancy) -> tuple[tuple[int, str], ...]:
        return tuple(sorted({(lease.owner_pid, lease.owner_identity) for item in occupancy.values() for lease in item.leases}))

    def _prepare_runtime(self, operation_id, operation, versions, state) -> RuntimePreparation:
        assert self.runtime is not None
        occupancy = {version: self.leases.inspect_occupancy(version) for version in versions}
        if any(item.status not in ("free", "occupied") for item in occupancy.values()):
            return RuntimePreparation("awaiting_release", "lease_evidence_uncertain")
        owners = self._live_owners(occupancy)
        request = LifecyclePrepareRequest(operation_id, state.revision, operation, versions, owners, feature_id=self.store.feature_id)
        authorize_request(self.store, request)
        try:
            prepared = self.runtime.prepare(request)
            return RuntimePreparation(prepared.status, prepared.reason, prepared.blocked_versions, {**dict(prepared.details), "prepared_owners": owners})
        finally:
            path = request_path(self.store, request)
            io.safe_path(path)
            path.unlink(missing_ok=True)

    def _path(self, operation_id: str) -> Path:
        if not isinstance(operation_id, str) or not _OPERATION.fullmatch(operation_id):
            raise StateError("invalid_operation_id")
        return self.journal_root / (operation_id + ".json")

    def _load(self, operation_id: str) -> dict:
        journal = _decode(io.read_bytes(self._path(operation_id), 2 * 1024 * 1024))
        if journal.get("schema_version") != 1 or journal.get("phase") not in _PHASES or type(journal.get("accepted")) is not bool:
            raise StateError("journal_corrupt")
        plan = self._plan_from(journal["plan"])
        if plan.feature_id != self.store.feature_id:
            raise StateError("feature_identity_conflict")
        document = plan.document()
        if "feature_id" not in journal["plan"]:
            document.pop("feature_id")  # explicitly scoped legacy screen journal
        body = dict(document)
        body.pop("confirmation_digest")
        if _digest(_json(body)) != plan.confirmation_digest or journal.get("plan_digest") != _digest(_json(document)):
            raise StateError("journal_plan_conflict")
        if plan.operation_id != operation_id or plan.kind not in ("install", "upgrade", "rollback", "uninstall"):
            raise StateError("journal_identity_conflict")
        if plan.target_version is not None and not _VERSION.fullmatch(plan.target_version):
            raise StateError("journal_corrupt")
        if plan.staged_root != (None if plan.kind == "uninstall" else self.staging_root / operation_id / "package"):
            raise StateError("journal_path_conflict")
        if any(not isinstance(v, str) or not _VERSION.fullmatch(v) for v in (*journal.get("garbage_versions", []), *plan.delete_versions, *plan.versions)):
            raise StateError("journal_corrupt")
        if len(plan.versions) > 256 or len(plan.delete_versions) > 256 or type(plan.revision) is not int or plan.revision < 0 or type(plan.enabled) is not bool:
            raise StateError("journal_corrupt")
        eligible_gc = {plan.target_version} if journal.get("rollback_pending") else set(plan.versions) - {plan.active, plan.target_version}
        if not set(journal.get("garbage_versions", [])) <= eligible_gc:
            raise StateError("journal_gc_conflict")
        return journal

    @staticmethod
    def _plan_from(document: dict) -> OperationPlan:
        values = dict(document)
        for key in ("source_path", "staged_root"):
            values[key] = Path(values[key]) if values[key] is not None else None
        values["confirmation_token"] = ""
        return OperationPlan(**values)

    def _save(self, journal: dict) -> None:
        io.atomic_write(self._path(journal["plan"]["operation_id"]), _json(journal))

    def _phase(self, journal: dict, phase: str, **details) -> None:
        journal.update(details)
        journal["phase"] = phase
        self._save(journal)

    def inspect(self) -> Inspection:
        result = self.store.read()
        state = result.state
        transactions = []
        garbage: set[str] = set()
        try:
            for path in self._journals():
                journal = self._load(path.stem)
                transactions.append(path.stem)
                garbage.update(journal.get("garbage_versions", []))
        except (StateError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return Inspection(
                "recovery_required", None, None, None, False, None, tuple(transactions), tuple(sorted(garbage)), _error(exc), self.store.feature_id
            )
        return Inspection(
            result.status,
            state.revision if state else None,
            state.active if state else None,
            state.previous if state else None,
            state.enabled if state else False,
            state.pending_transaction if state else None,
            tuple(transactions),
            tuple(sorted(garbage)),
            result.reason,
            self.store.feature_id,
        )

    def _journals(self) -> list[Path]:
        io.safe_path(self.journal_root)
        paths = []
        if self.journal_root.exists():
            for path in self.journal_root.iterdir():
                if path.name.startswith("tx-") and path.suffix == ".json":
                    paths.append(path)
                    if len(paths) > 4096:
                        raise StateError("metadata_limit")
        return sorted(paths)

    def _occupancy(self, versions) -> dict:
        return {version: _plain(self.leases.inspect_occupancy(version)) for version in versions}

    def _make_plan(self, operation_id: str, kind: str, state: InstallState, **source) -> tuple[OperationPlan, dict]:
        values: dict[str, Any] = dict(
            operation_id=operation_id,
            feature_id=self.store.feature_id,
            kind=kind,
            revision=state.revision,
            source_type=None,
            source_path=None,
            source_digest=None,
            staged_root=None,
            staged_digest=None,
            target_version=None,
            manifest_digest=None,
            active=state.active,
            previous=state.previous,
            enabled=state.enabled,
            versions=dict(state.versions),
            estimated_bytes=0,
            occupancy=self._occupancy(state.versions),
            action_summary=(
                "protect drafts and user data",
                "release runtime and version leases",
                "commit immutable state",
                "confirm load or complete safe deletion",
            ),
            rollback_allowed=kind in ("upgrade", "rollback"),
            delete_versions=tuple(state.versions),
        )
        values.update(source)
        digest = _digest(_json(values))
        token = secrets.token_urlsafe(32)
        plan = OperationPlan(**values, confirmation_digest=digest, confirmation_token=token)
        journal = dict(
            schema_version=1,
            plan=plan.document(),
            plan_digest=_digest(_json(plan.document())),
            confirmation_token_hash=_digest(token.encode()),
            phase="preflighted",
            accepted=False,
            expected_state=state.document(),
            intent=None,
            garbage_versions=[],
            deleted_versions=[],
            self_check_passed=False,
            persist_intent=False,
        )
        self._save(journal)
        return plan, journal

    def preflight_install(self, source: Path | str) -> OperationResult:
        return self._preflight("install", Path(source))

    def preflight_upgrade(self, source: Path | str) -> OperationResult:
        return self._preflight("upgrade", Path(source))

    def _preflight(self, kind: str, source: Path) -> OperationResult:
        operation_id = "tx-" + uuid.uuid4().hex
        try:
            state = self._state(initialize=True)
            if state.pending_transaction:
                raise StateError("pending_transaction")
            if kind == "upgrade" and state.active is None or kind == "install" and state.active is not None:
                # Same-version retry remains legal, all other replacements must
                # use the explicit upgrade operation and its retention policy.
                require_same = kind == "install"
                if not require_same:
                    raise StateError("not_installed")
            source = source.absolute()
            io.safe_path(source)
            source = source.resolve(strict=True)
            if source == self.store.root or self.store.root in source.parents:
                if kind != "rollback" or state.previous is None or source != self.versions_root / state.previous:
                    raise StateError("source_inside_installation")
            source_type, fingerprint = package_files.source_fingerprint(source, self.verifier.limits)
            staged_root = self.staging_root / operation_id / "package"
            size = package_files.stage(source, source_type, staged_root, self.verifier.limits)
            if package_files.source_fingerprint(source, self.verifier.limits) != (source_type, fingerprint):
                raise StateError("source_changed")
            descriptor = self.verifier.verify(staged_root)
            if descriptor.trust_status != "trusted_official":
                raise StateError("official_signature_required")
            manifest_digest = _digest(descriptor.raw_manifest)
            old_digest = state.versions.get(descriptor.version)
            if old_digest is not None:
                if old_digest != manifest_digest:
                    raise StateError("same_version_different_digest")
                if kind != "rollback":
                    self._cleanup_stage(operation_id)
                    return self._result("idempotent", operation_id, "completed", revision=state.revision, reason="same_version_same_digest")
            if kind == "install" and state.active is not None:
                raise StateError("already_installed_use_upgrade")
            if kind == "rollback" and (descriptor.version != state.previous or old_digest != manifest_digest):
                raise StateError("previous_unavailable")
            if kind != "rollback" and state.active is not None and tuple(map(int, descriptor.version.split("."))) <= tuple(map(int, state.active.split("."))):
                raise StateError("downgrade_not_proven_compatible")
            plan, journal = self._make_plan(
                operation_id,
                kind,
                state,
                source_type=source_type,
                source_path=source,
                source_digest=fingerprint,
                staged_root=staged_root,
                staged_digest=self._package_digest(descriptor),
                target_version=descriptor.version,
                manifest_digest=manifest_digest,
                estimated_bytes=size,
            )
            self._phase(journal, "awaiting_confirmation")
            return self._result("awaiting_confirmation", operation_id, "awaiting_confirmation", plan, state.revision)
        except (StateError, PackageVerificationError, OSError, ValueError, TypeError, zipfile.BadZipFile) as exc:
            reason = _error(exc)
            try:
                self._cleanup_stage(operation_id)
            except (StateError, OSError):
                pass  # Never widen the cleanup boundary to make rejection succeed.
            return self._result("rejected", operation_id, "rejected", reason=reason)

    def preflight_rollback(self) -> OperationResult:
        try:
            state = self._state()
            if state.pending_transaction:
                raise StateError("pending_transaction")
            if state.previous is None or state.previous not in state.versions:
                raise StateError("previous_unavailable")
            return self._preflight("rollback", self.versions_root / state.previous)
        except (StateError, OSError) as exc:
            return self._result("rejected", reason=_error(exc))

    def set_enabled(self, enabled: bool, *, expected_revision: int) -> OperationResult:
        try:
            if type(enabled) is not bool or type(expected_revision) is not int:
                raise StateError("invalid_enable_request")
            state = self._state()
            if state.pending_transaction:
                raise StateError("pending_transaction")
            if state.revision != expected_revision:
                raise StateError("revision_conflict")
            if state.active is None:
                raise StateError("not_installed")
            if enabled:
                descriptor = self.verifier.verify(self.versions_root / state.active)
                if descriptor.trust_status != "trusted_official" or _digest(descriptor.raw_manifest) != state.versions[state.active]:
                    raise StateError("active_verification_failed")
            elif self.runtime is not None:
                prepared = self._prepare_runtime("lc-" + uuid.uuid4().hex, "disable", tuple(state.versions), state)
                if prepared.status != "ready":
                    return self._result(
                        "awaiting_confirmation" if prepared.status == "draft_blocked" else "awaiting_release", reason=prepared.reason, details=prepared.details
                    )
            with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                with _named_lock(lambda: self.leases.management_guard(tuple(state.versions)), "leases"):
                    current = self._state()
                    if current.document() != state.document():
                        raise StateError("revision_conflict")
                    if any((journal := self._load(path.stem))["accepted"] and journal["phase"] != "completed" for path in self._journals()):
                        raise StateError("accepted_transaction_pending")
                    if current.enabled == enabled:
                        return self._result("idempotent", revision=current.revision)
                    self._commit(
                        StateChange(dict(current.versions), current.active, current.previous, enabled),
                        expected_revision=current.revision,
                        operation_id="enable-" + uuid.uuid4().hex,
                    )
                    return self._result("completed", revision=current.revision + 1)
        except (StateError, PackageVerificationError, OSError) as exc:
            return self._result("rejected", reason=_error(exc))

    def cancel_preflight(self, plan: OperationPlan) -> OperationResult:
        if not isinstance(plan, OperationPlan):
            return self._result("rejected", reason="invalid_plan")
        if plan.feature_id != self.store.feature_id:
            return self._result("rejected", reason="feature_identity_conflict")
        try:
            with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                journal = self._load(plan.operation_id)
                if journal["plan_digest"] != _digest(_json(plan.document())):
                    raise StateError("confirmation_plan_changed")
                if journal["accepted"]:
                    raise StateError("accepted_transaction_cannot_cancel")
                self._phase(journal, "rejected", reason="preflight_cancelled")
            self._cleanup_stage(plan.operation_id)
            return self._result("completed", plan.operation_id, "completed", reason="preflight_cancelled")
        except (StateError, OSError) as exc:
            return self._result("rejected", plan.operation_id, reason=_error(exc))

    def preflight_uninstall(self) -> OperationResult:
        operation_id = "tx-" + uuid.uuid4().hex
        try:
            state = self._state(initialize=True)
            if state.pending_transaction:
                raise StateError("pending_transaction")
            delete_versions = set(state.versions)
            for path in self._journals():
                old = self._load(path.stem)
                if old["phase"] == "completed":
                    delete_versions.update(old["garbage_versions"])
            io.safe_path(self.versions_root)
            if self.versions_root.exists() and any(path.name not in delete_versions for path in self.versions_root.iterdir()):
                raise StateError("orphan_version_requires_recovery")
            if not delete_versions:
                return self._result("idempotent", operation_id, "completed", revision=state.revision)
            plan, journal = self._make_plan(operation_id, "uninstall", state, delete_versions=tuple(sorted(delete_versions)))
            self._phase(journal, "awaiting_confirmation")
            return self._result("awaiting_confirmation", operation_id, "awaiting_confirmation", plan, state.revision)
        except (StateError, OSError) as exc:
            return self._result("recovery_required", operation_id, "recovery_required", reason=_error(exc))

    @staticmethod
    def _package_digest(descriptor: VerifiedFeatureDescriptor) -> str:
        return _digest(descriptor.raw_manifest + (descriptor.signature or b""))

    def _cleanup_stage(self, operation_id: str) -> None:
        self._path(operation_id)  # validate ownership identity before any removal
        package_files.remove_owned_tree(self.staging_root / operation_id, self.staging_root, self.verifier.limits)

    def _change(self, plan: OperationPlan, state: InstallState, step: str) -> StateChange:
        versions = dict(state.versions)
        if step == "pending":
            return StateChange(versions, state.active, state.previous, False if plan.kind == "uninstall" else state.enabled, plan.operation_id)
        if step == "activate":
            assert plan.target_version is not None and plan.manifest_digest is not None
            retained = {plan.target_version: plan.manifest_digest}
            if plan.active is not None:
                retained[plan.active] = plan.versions[plan.active]
            return StateChange(retained, plan.target_version, plan.active, True if plan.kind == "install" else plan.enabled, plan.operation_id)
        if step == "confirmed":
            return StateChange(versions, state.active, state.previous, state.enabled, None)
        if step == "uninstalled":
            return StateChange({}, None, None, False, None)
        if step == "rollback":
            assert plan.active is not None
            return StateChange({plan.active: plan.versions[plan.active]}, plan.active, None, False, plan.operation_id)
        if step == "rollback_confirmed":
            return StateChange(versions, state.active, state.previous, plan.enabled, None)
        if step == "disabled":
            # Evidence conflict never clears pending or authorizes a bad candidate.
            return StateChange(versions, state.active, state.previous, False, plan.operation_id)
        raise StateError("journal_intent_invalid")

    def _reconcile(self, journal: dict) -> InstallState:
        state = self._state()
        intent = journal.get("intent")
        if intent is not None:
            plan = self._plan_from(journal["plan"])
            before = journal["expected_state"]
            if state.document() == intent["after"]:
                # The after image is derived here, not trusted from journal input.
                predecessor = InstallState(
                    before["revision"],
                    before["versions"],
                    before["active"],
                    before["previous"],
                    before["enabled"],
                    before["pending_transaction"],
                    self.store.feature_id,
                )
                change = self._change(plan, predecessor, intent["step"])
                after = InstallState(
                    predecessor.revision + 1, change.versions, change.active, change.previous, change.enabled, change.pending_transaction, self.store.feature_id
                ).document()
                if after != intent["after"]:
                    raise StateError("journal_intent_invalid")
                journal["expected_state"] = after
                journal["intent"] = None
                self._save(journal)
            elif state.document() != before:
                raise StateError("revision_conflict")
            else:
                # A crash before commit is retried at its normal state-machine
                # step, never by executing an arbitrary journal after image.
                # Keep the exact child identity for a write-ahead replay.
                self._validate_intent(plan, journal, state)
        if state.document() != journal["expected_state"]:
            raise StateError("revision_conflict")
        return state

    def _validate_intent(self, plan: OperationPlan, journal: dict, state: InstallState) -> None:
        intent = journal["intent"]
        step = intent["step"]
        child = intent.get("operation_id")
        # Legacy drafts used a fixed child ID. Replay it only for that exact
        # already-persisted intent; new commits always receive a unique child.
        if child is None:
            child = plan.operation_id + "." + step
            intent["operation_id"] = child
        if not isinstance(child, str) or not re.fullmatch(r"(?:txc-[a-f0-9]{32}|" + re.escape(plan.operation_id) + r")\." + re.escape(step), child):
            raise StateError("journal_intent_invalid")
        change = self._change(plan, state, step)
        if (
            intent["after"]
            != InstallState(
                state.revision + 1, change.versions, change.active, change.previous, change.enabled, change.pending_transaction, self.store.feature_id
            ).document()
        ):
            raise StateError("journal_intent_invalid")

    def _move(self, plan: OperationPlan, journal: dict, state: InstallState, step: str) -> InstallState:
        if state.document() != journal["expected_state"] or self._state().document() != state.document():
            raise StateError("revision_conflict")
        change = self._change(plan, state, step)
        after = InstallState(
            state.revision + 1, change.versions, change.active, change.previous, change.enabled, change.pending_transaction, self.store.feature_id
        )
        intent = journal.get("intent")
        if intent is not None:
            self._validate_intent(plan, journal, state)
            if intent["step"] != step or intent["after"] != after.document():
                raise StateError("journal_intent_invalid")
        else:
            intent = dict(step=step, after=after.document(), operation_id="txc-" + uuid.uuid4().hex + "." + step)
            journal["intent"] = intent
            self._save(journal)
        self._commit(change, expected_revision=state.revision, operation_id=intent["operation_id"])
        journal["expected_state"] = after.document()
        journal["intent"] = None
        self._save(journal)
        return after

    def apply(self, plan: OperationPlan, *, confirmation_token: str | None = None) -> OperationResult:
        if not isinstance(plan, OperationPlan):
            return self._result("rejected", reason="invalid_plan")
        if plan.feature_id != self.store.feature_id:
            return self._result("rejected", reason="feature_identity_conflict")
        try:
            journal = self._load(plan.operation_id)
            if journal["plan_digest"] != _digest(_json(plan.document())):
                raise StateError("confirmation_plan_changed")
            if confirmation_token is None:
                return self._result("awaiting_confirmation", plan.operation_id, "awaiting_confirmation", plan, plan.revision)
            if not hmac.compare_digest(_digest(confirmation_token.encode()), journal["confirmation_token_hash"]):
                raise StateError("confirmation_token_invalid")
            return self._continue(plan, journal)
        except (StateError, PackageVerificationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return self._result("rejected", plan.operation_id, "rejected", plan, reason=_error(exc))

    def _continue(self, plan: OperationPlan, journal: dict) -> OperationResult:
        try:
            prepared_owners = None
            with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                journal = self._load(plan.operation_id)
                state = self._reconcile(journal)
            if journal.get("rollback_load_failed"):
                return self._result("recovery_required", plan.operation_id, "recovery_required", revision=state.revision, reason="rollback_load_failed")
            if journal.get("rollback_pending"):
                if state.active == plan.active and journal.get("intent") is None:
                    if journal["phase"] == "completed":
                        return self._result("idempotent", plan.operation_id, "completed", revision=state.revision)
                    self._phase(journal, "awaiting_startup_confirmation")
                    return self._result("awaiting_startup_confirmation", plan.operation_id, journal["phase"], revision=state.revision)
                return self._rollback(plan, journal, reason="rolled_back")
            if journal["phase"] == "rolling_back":
                return self._rollback(plan, journal, reason=journal.get("reason", "startup_load_failed"))
            if journal.get("reason") == "preflight_cancelled":
                raise StateError("preflight_cancelled")
            if journal["phase"] == "completed":
                return self._result("idempotent", plan.operation_id, "completed", plan, state.revision)
            if state.pending_transaction not in (None, plan.operation_id):
                raise StateError("pending_transaction")
            if not journal["accepted"]:
                if plan.kind != "uninstall" and not getattr(self.self_checker, "available", True):
                    raise StateError("self_check_sandbox_unavailable")
                if state.revision != plan.revision:
                    raise StateError("revision_conflict")
                if plan.kind != "uninstall":
                    assert plan.source_path is not None and plan.staged_root is not None
                    if package_files.source_fingerprint(plan.source_path, self.verifier.limits) != (plan.source_type, plan.source_digest):
                        raise StateError("source_changed")
                    descriptor = self.verifier.verify(plan.staged_root)
                    self._validate_candidate(plan, descriptor)
                    # Isolation completes BEFORE freezing the working runtime.
                    # A rejected candidate must not disable the existing feature.
                    checked = self.self_checker.check(descriptor)
                    if checked.status != "ready":
                        return self._result("rejected", plan.operation_id, "rejected", plan, state.revision, checked.reason or "self_check_failed")
                    self.verifier.reverify(descriptor)
                    journal["self_check_passed"] = True
                if self.runtime is not None:
                    prepared = self._prepare_runtime(plan.operation_id, plan.kind, tuple(plan.versions), state)
                    prepared_owners = prepared.details.get("prepared_owners")
                    if prepared.status != "ready":
                        status = "awaiting_confirmation" if prepared.status == "draft_blocked" else "awaiting_release"
                        return self._result(
                            status,
                            plan.operation_id,
                            "awaiting_confirmation" if status == "awaiting_confirmation" else "pending_runtime_release",
                            plan,
                            state.revision,
                            prepared.reason,
                            prepared.blocked_versions,
                            prepared.details,
                        )
            # Freeze execution and new lease admission before waiting for users
            # to naturally exit. No lock is retained during that wait.
            if state.pending_transaction is None:
                with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                    with _named_lock(lambda: self.leases.management_guard(tuple(state.versions)), "leases") as final_occupancy:
                        if prepared_owners is not None and self._live_owners(final_occupancy) != prepared_owners:
                            return self._result("awaiting_release", plan.operation_id, reason="lifecycle_owners_changed", plan=plan)
                        current = self._load(plan.operation_id)
                        state = self._reconcile(current)
                        # The acceptance record and first pending CAS are in one
                        # short critical section. Its write-before-commit gap is
                        # recoverable from the unique matching before-image.
                        if not current["accepted"]:
                            current["self_check_passed"] = journal["self_check_passed"]
                            current["accepted"] = True
                            journal = current  # preserve uncertainty if save fails after write
                            self._save(current)
                        journal = current
                        state = self._move(plan, journal, state, "pending")
                        self._phase(journal, "state_pending")
            if state.active == plan.target_version and plan.kind != "uninstall":
                self._phase(journal, "awaiting_startup_confirmation")
                return self._result("awaiting_startup_confirmation", plan.operation_id, "awaiting_startup_confirmation", plan, state.revision)
            if self.runtime is not None:
                prepared = self._prepare_runtime(plan.operation_id, plan.kind, tuple(plan.versions), state)
                if prepared.status != "ready":
                    self._phase(journal, "pending_runtime_release")
                    return self._result(
                        "awaiting_release",
                        plan.operation_id,
                        "pending_runtime_release",
                        plan,
                        state.revision,
                        prepared.reason,
                        prepared.blocked_versions,
                        prepared.details,
                    )
            occupancy = self._occupancy(plan.delete_versions)
            blocked = tuple(version for version, item in occupancy.items() if item["status"] != "free")
            if blocked:
                self._phase(journal, "pending_runtime_release")
                return self._result(
                    "awaiting_release", plan.operation_id, "pending_runtime_release", plan, state.revision, "version_in_use", blocked, {"occupancy": occupancy}
                )
            if plan.kind == "uninstall":
                return self._uninstall(plan, journal)
            assert plan.target_version is not None and plan.staged_root is not None
            candidate = self.versions_root / plan.target_version
            if candidate.exists() and plan.kind != "rollback" and (not journal["persist_intent"] or plan.staged_root.exists()):
                raise StateError("version_directory_exists")
            root = candidate if candidate.exists() else plan.staged_root
            descriptor = self.verifier.verify(root)
            self._validate_candidate(plan, descriptor)
            if not journal["self_check_passed"]:
                checked = self.self_checker.check(descriptor)
                if checked.status != "ready":
                    return self._rollback(plan, journal, reason=checked.reason or "self_check_failed")
                journal["self_check_passed"] = True
                self._save(journal)
            with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                with _named_lock(lambda: self.leases.management_guard(tuple(plan.versions)), "leases") as occupancy_guard:
                    state = self._reconcile(journal)
                    if any(item.status != "free" for item in occupancy_guard.values()):
                        self._phase(journal, "pending_runtime_release")
                        return self._result("awaiting_release", plan.operation_id, "pending_runtime_release", plan, state.revision, "version_in_use")
                    self.verifier.check_snapshot_identity(descriptor)
                    self._validate_candidate(plan, descriptor)
                    io.safe_path(self.versions_root)
                    self.versions_root.mkdir(parents=True, exist_ok=True)
                    io.safe_path(candidate)
                    if not candidate.exists():
                        if plan.staged_root.stat().st_dev != self.versions_root.stat().st_dev:
                            raise StateError("staging_volume_mismatch")
                        # rename (not replace); an existing target is never removed.
                        journal["persist_intent"] = True
                        self._save(journal)
                        os.rename(plan.staged_root, candidate)
                    elif root != candidate:
                        raise StateError("version_directory_exists")
                    if plan.kind == "rollback" and state.previous != plan.target_version:
                        raise StateError("previous_unavailable")
                    self._phase(journal, "version_persisted")
                    journal["garbage_versions"] = sorted(set(plan.versions) - {plan.active, plan.target_version})
                    self._save(journal)
                    state = self._move(plan, journal, state, "activate")
                    self._phase(journal, "active_committed")
                    self._phase(journal, "awaiting_startup_confirmation")
            self._cleanup_stage(plan.operation_id)
            return self._result("awaiting_startup_confirmation", plan.operation_id, "awaiting_startup_confirmation", plan, state.revision)
        except (StateError, PackageVerificationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            reason = _error(exc)
            if reason in LOCK_BUSY_REASONS:
                return self._result(
                    "failed",
                    plan.operation_id,
                    reason=reason,
                    plan=plan,
                    details={"lock_resource": reason.removesuffix("_lock_busy") if reason != "lock_busy" else "unknown", "safe_retry": True},
                )
            status = "recovery_required" if journal["accepted"] else "rejected"
            if status == "recovery_required":
                try:
                    # A nested state-machine step may have persisted a newer
                    # intent before raising. Never write this caller's stale copy.
                    with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                        latest = self._load(plan.operation_id)
                        if latest.get("rollback_pending") or latest["phase"] == "rolling_back":
                            latest["last_error"] = reason
                            self._save(latest)
                        else:
                            self._phase(latest, "recovery_required", reason=reason)
                except (StateError, OSError):
                    pass  # Persisted ledger/intent still drives recovery; no false success.
            return self._result(status, plan.operation_id, "recovery_required" if status == "recovery_required" else "rejected", plan, reason=reason)

    def _validate_candidate(self, plan: OperationPlan, descriptor: VerifiedFeatureDescriptor) -> None:
        if (
            descriptor.trust_status != "trusted_official"
            or descriptor.version != plan.target_version
            or _digest(descriptor.raw_manifest) != plan.manifest_digest
            or self._package_digest(descriptor) != plan.staged_digest
        ):
            raise StateError("staged_package_changed")

    def _uninstall(self, plan: OperationPlan, journal: dict) -> OperationResult:
        deleted = set()
        # Acquire in management -> leases -> state order. Release management
        # before recursive deletion, retaining only the kernel admission guard.
        # An accepted uninstall is already pending and can never be re-enabled.
        with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management") as management:
            with _named_lock(lambda: self.leases.management_guard(plan.delete_versions), "leases") as occupancy:
                journal = self._load(plan.operation_id)
                state = self._reconcile(journal)
                blocked = tuple(v for v, item in occupancy.items() if item.status != "free")
                if blocked:
                    return self._result("awaiting_release", plan.operation_id, "pending_runtime_release", plan, state.revision, "version_in_use", blocked)
                if state.pending_transaction != plan.operation_id or state.enabled:
                    raise StateError("uninstall_evidence_conflict")
                management.close()
                for version in plan.delete_versions:
                    if self._state().document() != state.document():
                        raise StateError("revision_conflict")
                    package_files.remove_owned_tree(self.versions_root / version, self.versions_root, self.verifier.limits)
                    deleted.add(version)
        # Never reacquire management while holding leases (no inverted order).
        with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
            with _named_lock(lambda: self.leases.management_guard(plan.delete_versions), "leases") as occupancy:
                journal = self._load(plan.operation_id)
                state = self._reconcile(journal)
                if journal["phase"] == "completed":
                    return self._result("idempotent", plan.operation_id, "completed", revision=state.revision)
                if any(item.status != "free" for item in occupancy.values()):
                    raise StateError("lease_evidence_changed")
                # Partial deletion/restart is safe without a progress ack: missing
                # named versions remain an accepted pending deletion, never active.
                if any((self.versions_root / v).exists() for v in plan.delete_versions):
                    raise StateError("incomplete_deletion")
                io.safe_path(self.versions_root)
                if self.versions_root.exists() and any(self.versions_root.iterdir()):
                    raise StateError("orphan_version_requires_recovery")
                journal["deleted_versions"] = sorted(set(journal["deleted_versions"]) | deleted)
                self._save(journal)
                state = self._move(plan, journal, state, "uninstalled")
                self._phase(journal, "completed")
        return self._result("completed", plan.operation_id, "completed", plan, state.revision)

    def startup_permit(self, operation_id: str, *, role: str):
        from .feature_startup_contract import StartupLoadPermit

        with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
            journal = self._load(operation_id)
            state = self._reconcile(journal)
            target = journal["plan"]["active"] if journal.get("rollback_pending") else journal["plan"]["target_version"]
            if (
                not journal["accepted"]
                or journal["plan"]["kind"] == "uninstall"
                or journal["phase"] != "awaiting_startup_confirmation"
                or state.active != target
                or state.pending_transaction != operation_id
            ):
                raise StateError("not_awaiting_startup")
            permit = StartupLoadPermit._issue(operation_id, state, self.store.root, role)
            self._startup_permits[permit.nonce] = permit
            return permit

    def confirm_startup(self, operation_id: str, *, receipt=None, loaded_manifest_digest=None, success=None) -> OperationResult:
        # Legacy digest/boolean arguments are deliberately rejected, not trusted.
        from .feature_package_startup import StartupLoadReceipt

        if not isinstance(receipt, StartupLoadReceipt) or loaded_manifest_digest is not None or success is not None:
            return self._result("recovery_required", operation_id, "recovery_required", reason="startup_load_receipt_required")
        try:
            journal = self._load(operation_id)
            plan = self._plan_from(journal["plan"])
            descriptor = self.verifier.verify(self.versions_root / receipt.permit.version)
            if _digest(descriptor.raw_manifest) != receipt.permit.manifest_digest:
                raise StateError("startup_load_receipt_invalid")
            with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                with _named_lock(lambda: self.leases.management_guard((receipt.permit.version,)), "leases") as occupancy:
                    journal = self._load(operation_id)
                    state = self._reconcile(journal)
                    if journal["phase"] == "completed" and receipt.permit.nonce in self._startup_permits:
                        return self._result("idempotent", operation_id, "completed", revision=state.revision)
                    if receipt.permit.operation_id != operation_id or not receipt.validate(self, state):
                        raise StateError("startup_load_receipt_invalid")
                    live = occupancy[receipt.permit.version].leases
                    if not any(
                        info.lease_id == receipt.lease_id
                        and info.kind == "host"
                        and info.owner_pid == receipt.process_id
                        and info.manifest_digest == receipt.permit.manifest_digest
                        and info.revision == receipt.permit.revision
                        for info in live
                    ):
                        raise StateError("startup_load_lease_missing")
                    step = "rollback_confirmed" if journal.get("rollback_pending") else "confirmed"
                    state = self._move(plan, journal, state, step)
                    self._phase(journal, "completed")
                    return self._result(
                        "completed", operation_id, "completed", revision=state.revision, reason="rolled_back" if journal.get("rollback_pending") else None
                    )
        except (StateError, PackageVerificationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return self._result("recovery_required", operation_id, "recovery_required", reason=_error(exc))

    def _startup_failed(self, permit: StartupLoadPermit) -> OperationResult:
        try:
            journal = self._load(permit.operation_id)
            plan = self._plan_from(journal["plan"])
            with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                state = self._reconcile(journal)
                issued = self._startup_permits.get(permit.nonce)
                if issued is None or issued is not permit or not issued.matches(self.store.root, state):
                    raise StateError("startup_permit_required")
                if journal.get("rollback_pending"):
                    journal["rollback_load_failed"] = True
                self._phase(journal, "rolling_back", reason="rolled_back" if journal.get("rollback_pending") else "startup_load_failed")
                if state.enabled:
                    self._move(plan, journal, state, "disabled")
            return self._rollback(plan, journal, reason="startup_load_failed")
        except (StateError, PackageVerificationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return self._result("recovery_required", phase="recovery_required", reason=_error(exc))

    def _rollback(self, plan: OperationPlan, journal: dict, *, reason: str) -> OperationResult:
        previous = plan.active
        descriptor = None
        if previous is not None:
            try:
                descriptor = self.verifier.verify(self.versions_root / previous)
                if (
                    descriptor.version != previous
                    or _digest(descriptor.raw_manifest) != plan.versions[previous]
                    or descriptor.trust_status != "trusted_official"
                ):
                    descriptor = None
            except (PackageVerificationError, StateError, OSError):
                descriptor = None
        with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
            with _named_lock(
                lambda: self.leases.management_guard(tuple(set(plan.versions) | ({plan.target_version} if plan.target_version else set()))), "leases"
            ) as occupancy:
                journal = self._load(plan.operation_id)
                state = self._reconcile(journal)
                if journal.get("rollback_pending") and journal["phase"] == "awaiting_startup_confirmation":
                    return self._result("awaiting_startup_confirmation", plan.operation_id, journal["phase"], revision=state.revision)
                self._phase(journal, "rolling_back", reason="rolled_back" if journal.get("rollback_pending") else reason)
                if state.enabled:
                    state = self._move(plan, journal, state, "disabled")
                if journal.get("rollback_load_failed") or descriptor is None or (previous is not None and occupancy[previous].status != "free"):
                    self._phase(journal, "recovery_required", reason="previous_unavailable_or_in_use")
                    return self._result(
                        "recovery_required", plan.operation_id, "recovery_required", revision=state.revision, reason="previous_unavailable_or_in_use"
                    )
                if plan.target_version is not None and occupancy[plan.target_version].status != "free":
                    return self._result(
                        "awaiting_release",
                        plan.operation_id,
                        "rolling_back",
                        revision=state.revision,
                        reason="failed_host_requires_exit",
                        blocked_versions=(plan.target_version,),
                    )
                # Write-ahead rollback mode must survive an interrupted state CAS.
                journal["rollback_pending"] = True
                journal["garbage_versions"] = [plan.target_version] if plan.target_version else []
                journal["reason"] = "rolled_back"
                self._save(journal)
                state = self._move(plan, journal, state, "rollback")
                self._phase(journal, "awaiting_startup_confirmation")
        return self._result(
            "awaiting_startup_confirmation",
            plan.operation_id,
            "awaiting_startup_confirmation",
            revision=state.revision,
            reason="rollback_load_confirmation_required",
        )

    def recover_pending(self) -> OperationResult:
        try:
            recovered = self.store.recover()
            if recovered.state is None:
                raise StateError(recovered.reason or recovered.status)
            state = recovered.state
            if state.pending_transaction is None:
                accepted = []
                with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                    state = self._state()
                    if state.pending_transaction is not None:
                        journal = self._load(state.pending_transaction)
                        if not journal["accepted"]:
                            raise StateError("pending_without_acceptance")
                        plan = self._plan_from(journal["plan"])
                    else:
                        plan = None
                    for path in () if plan is not None else self._journals():
                        journal = self._load(path.stem)
                        intent = journal.get("intent")
                        if intent is not None and intent.get("after") == state.document():
                            self._reconcile(journal)
                            self._phase(journal, "completed")
                            return self._result("completed", path.stem, "completed", revision=state.revision)
                        if journal["accepted"] and journal["phase"] != "completed":
                            accepted.append(journal)
                    if plan is not None:
                        accepted = [journal]
                    elif not accepted:
                        return self._result("idempotent", phase="completed", revision=state.revision)
                    if len(accepted) != 1:
                        # Only an exact current before-image permits a safety
                        # freeze; stale transactions never mutate higher revisions.
                        if all(j["expected_state"] == state.document() for j in accepted) and state.enabled:
                            with _named_lock(lambda: self.leases.management_guard(tuple(state.versions)), "leases"):
                                self._commit(
                                    StateChange(dict(state.versions), state.active, state.previous, False),
                                    expected_revision=state.revision,
                                    operation_id="conflict-" + uuid.uuid4().hex,
                                )
                        raise StateError("accepted_intent_conflict")
                    journal = accepted[0]
                    if plan is None and journal["expected_state"] != state.document():
                        raise StateError("revision_conflict")
                    plan = self._plan_from(journal["plan"])
                return self._continue(plan, journal)
            journal = self._load(state.pending_transaction)
            if not journal["accepted"]:
                raise StateError("pending_without_acceptance")
            plan = self._plan_from(journal["plan"])
            return self._continue(plan, journal)
        except (StateError, PackageVerificationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return self._result("recovery_required", phase="recovery_required", reason=_error(exc))

    def collect_garbage(self) -> OperationResult:
        removed = []
        failures = []
        try:
            for path in self._journals():
                journal = self._load(path.stem)
                if journal["phase"] != "completed":
                    continue
                retired = set()
                for version in journal["garbage_versions"]:
                    with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management") as management:
                        with _named_lock(lambda: self.leases.management_guard((version,)), "leases") as occupancy:
                            state = self._state()
                            if state.pending_transaction:
                                return self._result("awaiting_release", phase="gc_pending", reason="pending_transaction")
                            if version in state.versions:
                                # Old GC evidence cannot delete a reinstalled version.
                                retired.add(version)
                                continue
                            if occupancy[version].status != "free":
                                failures.append(version)
                                continue
                            management.close()
                            # Admission stays frozen, so install cannot accept or
                            # persist the same version while this path is deleted.
                            try:
                                package_files.remove_owned_tree(self.versions_root / version, self.versions_root, self.verifier.limits)
                                removed.append(version)
                                retired.add(version)
                            except (StateError, OSError) as exc:
                                failures.append(_error(exc))
                with _named_lock(lambda: io.open_kernel_lock(self.management_lock_path), "management"):
                    latest = self._load(path.stem)
                    latest["garbage_versions"] = sorted(set(latest["garbage_versions"]) - retired)
                    self._save(latest)
            return self._result(
                "failed" if failures else "completed",
                phase="gc_pending" if failures else "completed",
                reason="gc_pending" if failures else None,
                details={"removed": tuple(removed), "failures": tuple(failures)},
            )
        except (StateError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return self._result("failed", phase="gc_pending", reason=_error(exc))
