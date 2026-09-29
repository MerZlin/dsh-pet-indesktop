"""Connect a verified official package to the generic, child-only launcher.

This is not an installation resolver: the host explicitly supplies a verified
version and a writable runtime directory it owns. Every restart acquires a new
lease and revalidates the signed files; a validation failure has no fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence

from ..workers.launch import WorkerLaunch, isolated_worker_environment
from .feature_packages import FeaturePackageLoader, VerifiedFeatureDescriptor


def verified_worker_launch(
    loader: FeaturePackageLoader,
    descriptor: VerifiedFeatureDescriptor,
    *,
    runtime_directory: Path,
    core_roots: Sequence[Path] = (),
    environment: Mapping[str, str] | None = None,
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
    handle = loader.acquire_worker(descriptor)
    try:
        program, arguments = handle.command()
        child_environment = isolated_worker_environment(os.environ if environment is None else environment, core_roots=core_roots)
        return WorkerLaunch(str(program), tuple(arguments), str(runtime), child_environment, handle.close)
    except BaseException:
        handle.close()
        raise
