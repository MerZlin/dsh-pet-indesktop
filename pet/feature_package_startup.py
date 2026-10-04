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
            and not self.context.allow_in_process
            and callable(self.context.worker_launch_factory)
            and pin is not None
            and not pin.closed
            and pin.kind == "host"
            and pin.lease_id == self.lease_id
            and pin.revision == self.permit.revision
            and pin.version == self.permit.version
            and self.binding.host.state(self.context.owner) == "disabled"
        )


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
        from .feature_package_transactions import OperationResult

        if self.binding is not None:
            return self.refresh_authorization()
        try:
            state = self.service.store.read().state
            if state is None:
                return OperationResult("recovery_required", reason="install_state_unavailable")
            if state.pending_transaction:
                return self.load_pending(state.pending_transaction)
            if state.active is None:
                return OperationResult("idempotent", revision=state.revision, reason="not_installed")
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
            self._attach_authority()
            return self.refresh_authorization()
        except Exception:
            self.host.fault("official.screen-understanding", "startup_load_failed")
            return OperationResult("recovery_required", reason="startup_load_failed")

    def refresh_authorization(self) -> OperationResult:
        from .feature_package_transactions import OperationResult

        if self.binding is None:
            return OperationResult("awaiting_release", reason="startup_required")
        owner = self.binding.handle.descriptor.id
        state = self.service.store.read().state
        if state is None or state.pending_transaction or state.active != self.binding.handle.descriptor.version:
            self.host.disable(owner)
            return OperationResult("awaiting_release", reason="version_change_requires_restart")
        try:
            purpose = "execution" if state.enabled else "configuration"
            selected = FeatureVersionSelection.from_resolution(self.service.store.resolve_verified(self.service.verifier, purpose=purpose))
            self.binding.refresh_selection(selected)
            if state.enabled:
                self.host.enable(owner)
            else:
                self.host.disable(owner)
            return OperationResult("completed", revision=selected.revision)
        except Exception:
            self.host.disable(owner)
            return OperationResult("recovery_required", reason="authorization_refresh_failed")

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
            self._attach_authority()
            pin = self.binding.handle.process_pin
            if pin is None:
                raise PackageVerificationError("production process pin is missing")
            receipt = StartupLoadReceipt(permit, self.binding, self.context, os.getpid(), pin.lease_id, _RECEIPT_SEAL)
            result = self.service.confirm_startup(operation_id, receipt=receipt)
            if result.status in ("completed", "idempotent"):
                state = self.service.store.read().state
                purpose = "execution" if state is not None and state.enabled else "configuration"
                normal = FeatureVersionSelection.from_resolution(self.service.store.resolve_verified(self.service.verifier, purpose=purpose))
                self.binding.refresh_selection(normal)
                if state is not None and state.enabled:
                    self.host.enable(selection.feature_id)
            return result
        except Exception:
            # Do not serialize candidate exception text or clear an imported pin.
            if self.binding is not None:
                self.host.fault(self.binding.handle.descriptor.id, "startup_load_failed")
            return self.service._startup_failed(permit)
