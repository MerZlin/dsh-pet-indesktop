"""Explicit verified package attachment to the existing official feature host.

No scanning, installation state or source fallback. A loaded host stays pinned
until process exit; settings and native child leases describe additional users.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from .feature_host import FeatureDefinition, FeatureHost
from .feature_packages import FeaturePackageLoader, HostHandle, PackageVerificationError, VerifiedFeatureDescriptor
from .worker_launch import verified_worker_launch


class FeaturePackageBinding:
    def __init__(self, host: FeatureHost, handle: HostHandle):
        self.host = host
        self.handle = handle
        self.closed = False

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
) -> FeaturePackageBinding:
    """Use one explicitly selected, verified version for host/settings/Worker.

    The caller owns the writable runtime directory and the binding. Settings
    leases follow QObject destruction, not a best-effort Python finalizer.
    """
    host.registry.check_thread()
    if host.state(descriptor.id) != "absent" or descriptor.id in host._definitions:
        raise PackageVerificationError("feature already provided; version replacement requires restart")
    handle = loader.load_host(descriptor)
    binding = FeaturePackageBinding(host, handle)
    try:
        definition = handle.factory()
        if not isinstance(definition, FeatureDefinition) or definition.owner != descriptor.id:
            raise PackageVerificationError("official factory returned an invalid definition")

        def settings_factory(*args, **kwargs):
            if binding.closed or not host.configurable(descriptor.id):
                raise PackageVerificationError("feature settings are unavailable")
            lease = loader.acquire_settings(descriptor)
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
            )

        host.provide(replace(definition, settings_factory=settings_factory, worker_launch_factory=launch_factory, allow_in_process=False))
    except BaseException:
        handle.close()
        raise
    return binding
