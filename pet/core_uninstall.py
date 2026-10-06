"""Qt-free two-package uninstall precondition; never deletes Core or user data.

The installer separately retains its Core file-replacement barrier. This
coordinator confirms immutable per-owner plans and invokes only the real local
transactions. It is deliberately NOT a cross-package atomic transaction:
accepted removals remain accepted if another owner is occupied or fails.
A UI can display removal evidence, but cannot submit startup-load receipts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from . import feature_state_io as io
from .feature_install_state import InstallState
from .feature_package_transactions import FeaturePackageTransactionService, OperationPlan, OperationResult
from .official_features import OFFICIAL_FEATURES


def _digest(document) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class PackageRemovalPlan:
    feature_id: str
    revision: int
    state_digest: str
    plan: OperationPlan | None


@dataclass(frozen=True)
class CoreUninstallPlan:
    operation_id: str
    data_path_identity: str
    packages: tuple[PackageRemovalPlan, ...]
    summary: str
    confirmation_token: str


@dataclass(frozen=True)
class CoreRemovalEvidence:
    """Fresh all-clear observation, not permission to bypass the installer lock."""

    data_path_identity: str
    revisions: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CoreUninstallResult:
    status: str
    results: tuple[OperationResult, ...] = ()
    plan: CoreUninstallPlan | None = None
    evidence: CoreRemovalEvidence | None = None
    reason: str | None = None


class CoreUninstallCoordinator:
    def __init__(self, services: Mapping[str, FeaturePackageTransactionService]):
        if set(services) != set(OFFICIAL_FEATURES) or any(
            not isinstance(service, FeaturePackageTransactionService) or service.store.feature_id != owner for owner, service in services.items()
        ):
            raise ValueError("official_owner_set_required")
        roots = {service.store.root.parent.parent.resolve() for service in services.values()}
        if len(roots) != 1:
            raise ValueError("shared_data_root_required")
        self.services = MappingProxyType(dict(services))
        self.data_path_identity = _digest(str(next(iter(roots))))
        self._issued: dict[str, CoreUninstallPlan] = {}
        self._closed = False

    def _state(self, owner: str) -> InstallState:
        result = self.services[owner].store.read()
        if result.state is None:
            raise io.StateError(result.reason or result.status)
        return result.state

    def _evidence(self) -> CoreRemovalEvidence:
        revisions = []
        for owner, service in sorted(self.services.items()):
            state = self._state(owner)
            if state.versions or state.enabled or state.active or state.previous or state.pending_transaction:
                raise io.StateError("package_removal_incomplete")
            # Unknown directories are diagnostics, never adopted as installed or
            # recursively removed to manufacture a successful Core uninstall.
            for name, reason in (
                ("versions", "orphan_version_requires_recovery"),
                ("staging", "staging_cleanup_required"),
                ("leases", "lease_cleanup_required"),
            ):
                path = service.store.root / name
                io.safe_path(path)
                if path.exists() and any(path.iterdir()):
                    raise io.StateError(reason)
            revisions.append((owner, state.revision))
        return CoreRemovalEvidence(self.data_path_identity, tuple(revisions))

    def prepare(self) -> CoreUninstallResult:
        if self._closed:
            return CoreUninstallResult("rejected", reason="coordinator_closed")
        results = []
        entries = []
        try:
            for owner, service in sorted(self.services.items()):
                before = self._state(owner)
                result = service.preflight_uninstall()
                results.append(result)
                if result.status not in ("awaiting_confirmation", "idempotent"):
                    return CoreUninstallResult(result.status, tuple(results), reason=result.reason)
                after = self._state(owner)
                # The empty-ledger bootstrap may add a metadata revision. No
                # execution state may change during a preview.
                if result.plan is not None and result.plan.revision != after.revision:
                    raise io.StateError("revision_conflict")
                if before.versions != after.versions or before.enabled != after.enabled or before.pending_transaction != after.pending_transaction:
                    raise io.StateError("revision_conflict")
                entries.append(PackageRemovalPlan(owner, after.revision, _digest(after.document()), result.plan))
            if all(entry.plan is None for entry in entries):
                return CoreUninstallResult("idempotent", tuple(results), evidence=self._evidence())
            summary = "卸载 Core 前分别卸载 AI 与屏幕理解代码；不承诺跨包回滚。已接受的卸载不会因取消而复活。保留全部个人数据、设置、profile、凭据、记忆、额度和聊天历史。"
            operation_id = "core-remove-" + uuid.uuid4().hex
            document = dict(
                operation_id=operation_id,
                data_path_identity=self.data_path_identity,
                packages=[
                    dict(
                        feature_id=entry.feature_id,
                        revision=entry.revision,
                        state_digest=entry.state_digest,
                        plan=entry.plan.document() if entry.plan else None,
                    )
                    for entry in entries
                ],
                summary=summary,
            )
            plan = CoreUninstallPlan(operation_id, self.data_path_identity, tuple(entries), summary, _digest(document))
            self._issued[plan.operation_id] = plan
            return CoreUninstallResult("awaiting_confirmation", tuple(results), plan)
        except (io.StateError, OSError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else "io_error"
            return CoreUninstallResult("recovery_required", tuple(results), reason=reason)

    def apply(self, plan: CoreUninstallPlan, *, confirmation_token: str | None = None) -> CoreUninstallResult:
        if self._closed:
            return CoreUninstallResult("rejected", reason="coordinator_closed")
        if not isinstance(plan, CoreUninstallPlan) or self._issued.get(plan.operation_id) is not plan:
            return CoreUninstallResult("rejected", reason="confirmation_plan_invalid")
        if confirmation_token is None:
            return CoreUninstallResult("awaiting_confirmation", plan=plan)
        if not isinstance(confirmation_token, str) or not hmac.compare_digest(confirmation_token, plan.confirmation_token):
            return CoreUninstallResult("rejected", plan=plan, reason="confirmation_token_invalid")
        results = []
        try:
            # Best-effort before-image check for both owners BEFORE any removal.
            # Each independent service still performs its own revision CAS;
            # this check does not pretend to make the two commits atomic.
            for entry in plan.packages:
                state = self._state(entry.feature_id)
                if state.revision != entry.revision or _digest(state.document()) != entry.state_digest:
                    return CoreUninstallResult("rejected", plan=plan, reason="revision_conflict")
            for entry in plan.packages:
                if entry.plan is not None:
                    result = self.services[entry.feature_id].apply(entry.plan, confirmation_token=entry.plan.confirmation_token)
                    results.append(result)
            return self._aggregate(tuple(results), plan)
        except (io.StateError, OSError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else "io_error"
            return CoreUninstallResult("recovery_required", tuple(results), plan, reason=reason)

    def _aggregate(self, results: tuple[OperationResult, ...], plan: CoreUninstallPlan | None = None) -> CoreUninstallResult:
        for status in ("recovery_required", "failed", "rejected", "awaiting_release", "awaiting_startup_confirmation", "awaiting_confirmation"):
            match = next((item for item in results if item.status == status), None)
            if match is not None:
                return CoreUninstallResult(status, results, plan, reason=match.reason)
        try:
            return CoreUninstallResult("completed", results, plan, self._evidence())
        except (io.StateError, OSError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else "io_error"
            return CoreUninstallResult("recovery_required", results, plan, reason=reason)

    def recover(self) -> CoreUninstallResult:
        if self._closed:
            return CoreUninstallResult("rejected", reason="coordinator_closed")
        # Recovery never accepts a new removal, saves drafts, closes another
        # process, loads a factory, or changes an accepted removal back to enabled.
        results = tuple(service.recover_pending() for _, service in sorted(self.services.items()))
        return self._aggregate(results)

    def close(self) -> None:
        # GUI disposal has no authority to cancel already-accepted journals.
        self._closed = True
        self._issued.clear()
