"""Connect a verified official package to the generic, child-only launcher.

This is not an installation resolver: the host explicitly supplies a verified
version and a writable runtime directory it owns. Every restart acquires a new
lease and revalidates the signed files; a validation failure has no fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence

from ..feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection, WorkerReservation
from ..workers.launch import WorkerLaunch, isolated_worker_environment
from .feature_packages import FeaturePackageLoader, VerifiedFeatureDescriptor


def verified_worker_launch(
    loader: FeaturePackageLoader,
    descriptor: VerifiedFeatureDescriptor,
    *,
    runtime_directory: Path,
    core_roots: Sequence[Path] = (),
    environment: Mapping[str, str] | None = None,
    selection: FeatureVersionSelection | None = None,
    lease_coordinator: FeatureVersionLeaseCoordinator | None = None,
) -> WorkerLaunch:
    """Hold the selected version until the supervisor confirms native exit.

    Runtime/log files may not be written into either installation. The caller
    creates the per-instance runtime directory; this function never creates or
    removes directories and cannot change the signed program or its arguments.
    """
    runtime = Path(runtime_directory)
    if not runtime.is_absolute():
        raise ValueError("runtime directory must be absolute")
    runtime = runtime.resolve(strict=True)
    roots = (descriptor.root.resolve(strict=True), *(Path(root).resolve() for root in core_roots))
    if not runtime.is_dir() or any(runtime == root or root in runtime.parents for root in roots):
        raise ValueError("runtime directory must be outside package and Core installations")
    if (selection is None) != (lease_coordinator is None):
        raise ValueError("installed selection and lease coordinator must be supplied together")
    if selection is not None and selection.descriptor is not descriptor:
        raise ValueError("installed selection does not match descriptor")
    reservation: WorkerReservation | None = None
    if selection is not None and lease_coordinator is not None:
        reservation = lease_coordinator.reserve_worker(selection)
    handle = loader.acquire_worker(descriptor)
    try:
        program, arguments = handle.command()
        child_environment = isolated_worker_environment(os.environ if environment is None else environment, core_roots=core_roots)
        if reservation is None:
            return WorkerLaunch(str(program), tuple(arguments), str(runtime), child_environment, handle.close)
        assert lease_coordinator is not None
        token = reservation.handoff_token
        child_environment["DSH_PET_FEATURE_LEASE_ROOT"] = str(lease_coordinator.data_root)
        child_environment["DSH_PET_FEATURE_LEASE_HANDOFF_TOKEN"] = token

        def confirm(child_pid: int | None) -> bool:
            confirmed = reservation.claim_child(child_pid=child_pid)
            if confirmed:
                reservation.close()
            return confirmed

        def abort() -> None:
            if not reservation.closed:
                reservation.abort_after_confirmed_failure()

        def release() -> None:
            handle.close()
            if not reservation.closed:
                reservation.abort_after_confirmed_failure()

        return WorkerLaunch(
            str(program),
            tuple(arguments),
            str(runtime),
            child_environment,
            release,
            on_handoff_confirmed=confirm,
            on_handoff_abort=abort,
            handoff_token=token,
        )
    except BaseException:
        handle.close()
        if reservation is not None and not reservation.closed:
            reservation.abort_after_confirmed_failure()
        raise
