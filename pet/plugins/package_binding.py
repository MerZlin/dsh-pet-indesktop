"""Explicit verified package attachment to the existing official feature host.

No scanning, installation state or source fallback. A loaded host stays pinned
until process exit; settings and native child leases describe additional users.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from ..feature_ports import FeatureHostContext
from ..feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection
from .feature_host import FeatureDefinition, FeatureHost
from .feature_packages import FeaturePackageLoader, HostHandle, PackageVerificationError, VerifiedFeatureDescriptor
from .worker_launch import verified_worker_launch


class FeaturePackageBinding:
    def __init__(self, host: FeatureHost, handle: HostHandle):
        self.host = host
        self.handle = handle
        self.closed = False
        self.selection: FeatureVersionSelection | None = None
        self.context: FeatureHostContext | None = None

    def refresh_selection(self, selection: FeatureVersionSelection) -> None:
        self.host.registry.check_thread()
        descriptor = self.handle.descriptor
        if (
            self.selection is None
            or selection.version != descriptor.version
            or selection.manifest_digest != self.selection.manifest_digest
            or selection.descriptor.raw_manifest != descriptor.raw_manifest
        ):
            raise PackageVerificationError("version replacement requires restart")
        self.selection = replace(selection, descriptor=descriptor)

    def close(self) -> bool:
        """Honor settings drafts. Native process leases end only on native exit."""
        self.host.registry.check_thread()
        if self.closed:
            return True
        if not self.host.remove(self.handle.descriptor.id):
            return False
        self.closed = True
        self.handle.close()
        return True


def bind_verified_feature(
    host: FeatureHost,
    loader: FeaturePackageLoader,
    descriptor: VerifiedFeatureDescriptor,
    *,
    runtime_directory: Path,
    core_roots: Sequence[Path] = (),
    environment: Mapping[str, str] | None = None,
    selection: FeatureVersionSelection | None = None,
    lease_coordinator: FeatureVersionLeaseCoordinator | None = None,
    enabled: bool = True,
) -> FeaturePackageBinding:
    """Use one explicitly selected, verified version for host/settings/Worker.

    The caller owns the writable runtime directory and the binding. Settings
    leases follow QObject destruction, not a best-effort Python finalizer.
    """
    host.registry.check_thread()
    if (selection is None) != (lease_coordinator is None):
        raise PackageVerificationError("installed selection and lease coordinator must be supplied together")
    if selection is not None and selection.descriptor is not descriptor:
        raise PackageVerificationError("installed selection does not match descriptor")
    if host.state(descriptor.id) != "absent" or descriptor.id in host._definitions:
        raise PackageVerificationError("feature already provided; version replacement requires restart")
    handle = (
        loader.load_installed_host(selection, lease_coordinator) if selection is not None and lease_coordinator is not None else loader.load_host(descriptor)
    )
    binding = FeaturePackageBinding(host, handle)
    binding.selection = selection
    try:
        definition = handle.factory()
        if not isinstance(definition, FeatureDefinition) or definition.owner != descriptor.id or not callable(definition.settings_factory):
            raise PackageVerificationError("official factory returned an invalid definition")

        def settings_factory(*args, **kwargs):
            if binding.closed or not host.configurable(descriptor.id):
                raise PackageVerificationError("feature settings are unavailable")
            current = binding.selection
            if current is not None and lease_coordinator is not None and current.purpose != "startup":
                from ..feature_version_lease import FeatureVersionSelection

                resolution = lease_coordinator.store.resolve_verified(loader.verifier, purpose="configuration")
                resolved = FeatureVersionSelection.from_resolution(resolution)
                binding.refresh_selection(resolved)
                current = resolved
            lease = (
                loader.acquire_installed_settings(current, lease_coordinator)
                if current is not None and lease_coordinator is not None
                else loader.acquire_settings(descriptor)
            )
            try:
                widget = definition.settings_factory(*args, **kwargs)
                from PySide6.QtCore import QObject

                if not isinstance(widget, QObject):
                    raise PackageVerificationError("official settings must own a QObject lifecycle")
                # Qt keeps this closure alive; a bound method alone weakly owns the lease.
                widget.destroyed.connect(lambda *_: lease.close())
                return widget
            except BaseException:
                lease.close()
                raise

        def launch_factory():
            if binding.closed or not host.enabled(descriptor.id):
                raise PackageVerificationError("feature execution is unavailable")
            return verified_worker_launch(
                loader,
                descriptor,
                runtime_directory=runtime_directory,
                core_roots=core_roots,
                environment=environment,
                selection=binding.selection,
                lease_coordinator=lease_coordinator,
            )

        host.provide(replace(definition, settings_factory=settings_factory, worker_launch_factory=launch_factory, allow_in_process=False), enabled=enabled)
    except BaseException:
        handle.close()
        raise
    return binding
