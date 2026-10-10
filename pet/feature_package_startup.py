"""Actual production startup: verified import, process pin, ports, then receipt.

This path never starts a Worker and never asks UI to certify a boolean result.
All FeatureHost operations run on its owning thread.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .feature_ports import FeatureHostContext
from .feature_startup_contract import StartupLoadPermit
from .feature_version_lease import FeatureVersionSelection
from .plugins.feature_packages import FeaturePackageLoader
from .plugins.package_binding import FeaturePackageBinding, bind_verified_feature
from .plugins.package_trust import PackageVerificationError

if TYPE_CHECKING:
    from .feature_package_transactions import FeaturePackageTransactionService, OperationResult
    from .plugins.feature_host import FeatureHost

_RECEIPT_SEAL = object()


@dataclass(frozen=True)
class StartupLoadReceipt:
    permit: StartupLoadPermit
    binding: FeaturePackageBinding
    context: FeatureHostContext
    process_id: int
    lease_id: str
    _seal: object = field(repr=False)

    def validate(self, service: FeaturePackageTransactionService, state) -> bool:
        pin = self.binding.handle.process_pin
        return (
            self._seal is _RECEIPT_SEAL
            and self.process_id == os.getpid()
            and service._startup_permits.get(self.permit.nonce) is self.permit
            and self.permit.matches(service.store.root, state)
            and not self.binding.closed
            and not self.binding.handle.closed
            and self.binding.context is not None
            and self.binding.context is self.context
            and self.context.owner == self.permit.version_owner
            and _valid_execution_ports(self.context, self.binding.handle.descriptor.execution_kind)
            and pin is not None
            and not pin.closed
            and pin.kind == "host"
            and pin.lease_id == self.lease_id
            and pin.revision == self.permit.revision
            and pin.version == self.permit.version
            and self.binding.host.state(self.context.owner) == "disabled"
        )


def _valid_execution_ports(context: FeatureHostContext, execution_kind: str) -> bool:
    kind = execution_kind
    if kind == "host-only":
        return context.allow_in_process is True and context.worker_launch_factory is None and context.desktop is None
    return context.allow_in_process is False and callable(context.worker_launch_factory)


class ProductionFeatureStartup:
    def __init__(
        self,
        service: FeaturePackageTransactionService,
        host: FeatureHost,
        *,
        runtime_directory: Path,
        context_factory: Callable[[], FeatureHostContext],
        role: str = "core",
    ):
        self.service = service
        self.host = host
        self.runtime_directory = runtime_directory
        self.context_factory = context_factory
        self.role = role
        self.binding: FeaturePackageBinding | None = None
        self.context: FeatureHostContext | None = None
        self._pending_receipt: StartupLoadReceipt | None = None

    def _prepare_runtime(self) -> None:
        from .feature_state_io import StateError, safe_path

        runtime = self.runtime_directory
        if not runtime.is_absolute() or not runtime.is_relative_to(self.service.leases.data_root) or runtime.is_relative_to(self.service.store.root):
            raise StateError("runtime_boundary")
        safe_path(runtime)
        runtime.mkdir(parents=True, exist_ok=True)
        safe_path(runtime)

    def _core_roots(self) -> tuple[Path, ...]:
        if not getattr(sys, "frozen", False):
            return ()
        return (Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))

    def _attach_authority(self) -> None:
        assert self.binding is not None
        binding = self.binding

        def allowed():
            selection = binding.selection
            state = self.service.store.read().state
            return (
                not binding.closed
                and selection is not None
                and state is not None
                and state.enabled
                and state.pending_transaction is None
                and state.revision == selection.revision
                and state.active == selection.version
                and state.versions.get(selection.version) == selection.manifest_digest
            )

        self.host.bind_authority(binding.handle.descriptor.id, allowed)

    def load_current(self) -> OperationResult:
        if self._pending_receipt is not None:
            return self._confirm_receipt()
        if self.binding is not None:
            return self.refresh_authorization()
        try:
            state = self.service.store.read().state
            if state is None:
                return self.service._result("recovery_required", reason="install_state_unavailable")
            if state.pending_transaction:
                return self.load_pending(state.pending_transaction)
            if state.active is None:
                return self.service._result("idempotent", revision=state.revision, reason="not_installed")
            selection = FeatureVersionSelection.from_resolution(self.service.store.resolve_verified(self.service.verifier, purpose="configuration"))
            self._prepare_runtime()
            self.binding = bind_verified_feature(
                self.host,
                FeaturePackageLoader(self.service.verifier),
                selection.descriptor,
                runtime_directory=self.runtime_directory,
                core_roots=self._core_roots(),
                selection=selection,
                lease_coordinator=self.service.leases,
                enabled=False,
            )
            raw = self.context_factory()
            if not isinstance(raw, FeatureHostContext) or raw.owner != selection.feature_id:
                raise PackageVerificationError("startup ports are invalid")
            self.context = self.host.bind_context(raw)
            self.binding.context = self.context
            if not _valid_execution_ports(self.context, selection.descriptor.execution_kind):
                raise PackageVerificationError("startup execution ports are invalid")
            self._attach_authority()
            return self.refresh_authorization()
        except Exception as exc:
            from .feature_package_transactions import LOCK_BUSY_REASONS
            from .feature_state_io import StateError

            if self.binding is None and isinstance(exc, StateError) and exc.code in LOCK_BUSY_REASONS:
                return self.service._result("awaiting_release", reason=exc.code)
            self.host.fault(self.service.store.feature_id, "startup_load_failed")
            return self.service._result("recovery_required", reason="startup_load_failed")

    def refresh_authorization(self) -> OperationResult:
        if self.binding is None:
            return self.service._result("awaiting_release", reason="startup_required")
        owner = self.binding.handle.descriptor.id
        from .feature_package_transactions import LOCK_BUSY_REASONS
        from .feature_state_io import StateError

        state_result = self.service.store.read()
        state = state_result.state
        if state is None:
            self.host.disable(owner)
            reason = "state_lock_busy" if state_result.status == "lock_busy" else "install_state_unavailable"
            return self.service._result("awaiting_release" if reason in LOCK_BUSY_REASONS else "recovery_required", reason=reason)
        if state.pending_transaction or state.active != self.binding.handle.descriptor.version:
            self.host.disable(owner)
            return self.service._result("awaiting_release", reason="version_change_requires_restart")
        try:
            purpose = "execution" if state.enabled else "configuration"
            selected = FeatureVersionSelection.from_resolution(self.service.store.resolve_verified(self.service.verifier, purpose=purpose))
            self.binding.refresh_selection(selected)
            if state.enabled:
                self.host.enable(owner)
            else:
                self.host.disable(owner)
            return self.service._result("completed", revision=selected.revision)
        except Exception as exc:
            self.host.disable(owner)
            if isinstance(exc, StateError) and exc.code in LOCK_BUSY_REASONS:
                return self.service._result("awaiting_release", reason=exc.code)
            return self.service._result("recovery_required", reason="authorization_refresh_failed")

    def load_pending(self, operation_id: str) -> OperationResult:
        """Only startup/bootstrap calls this before any feature execution."""
        permit = self.service.startup_permit(operation_id, role=self.role)
        try:
            resolution = self.service.store.resolve_verified(self.service.verifier, purpose="startup", permit=permit)
            selection = FeatureVersionSelection.from_resolution(resolution)
            loader = FeaturePackageLoader(self.service.verifier)
            self._prepare_runtime()
            self.binding = bind_verified_feature(
                self.host,
                loader,
                selection.descriptor,
                runtime_directory=self.runtime_directory,
                core_roots=self._core_roots(),
                selection=selection,
                lease_coordinator=self.service.leases,
                enabled=False,
            )
            raw_context = self.context_factory()
            if not isinstance(raw_context, FeatureHostContext) or raw_context.owner != selection.feature_id:
                raise PackageVerificationError("startup ports are invalid")
            self.context = self.host.bind_context(raw_context)
            self.binding.context = self.context
            if not _valid_execution_ports(self.context, selection.descriptor.execution_kind):
                raise PackageVerificationError("startup execution ports are invalid")
            self._attach_authority()
            pin = self.binding.handle.process_pin
            if pin is None:
                raise PackageVerificationError("production process pin is missing")
            self._pending_receipt = StartupLoadReceipt(permit, self.binding, self.context, os.getpid(), pin.lease_id, _RECEIPT_SEAL)
            return self._confirm_receipt()
        except Exception:
            # Do not serialize candidate exception text or clear an imported pin.
            if self.binding is not None:
                self.host.fault(self.binding.handle.descriptor.id, "startup_load_failed")
            return self.service._startup_failed(permit)

    def _confirm_receipt(self) -> OperationResult:
        receipt = self._pending_receipt
        assert receipt is not None and self.binding is not None
        result = self.service.confirm_startup(receipt.permit.operation_id, receipt=receipt)
        if result.status in ("completed", "idempotent"):
            # The commit can be durable while a subsequent authority read is
            # contended. Keep the real receipt/pin disabled for a retry rather
            # than treating a transient read failure as candidate load failure.
            authorized = self.refresh_authorization()
            if authorized.status != "completed":
                return authorized
            self._pending_receipt = None
        return result
