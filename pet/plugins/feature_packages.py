"""Controlled official host loading and process-local, generation-scoped leases.

Public seam::

    verifier = FeaturePackageVerifier(core_version="5.0.0", api_version="1",
        trust_anchors=core_public_keys, allowed_capabilities={"screen.capture"})
    descriptor = verifier.verify(absolute_version_directory)
    loader = FeaturePackageLoader(verifier)
    host = loader.load_host(descriptor)       # imports; does NOT call create_host
    factory = host.factory                   # rechecks; parent supplies host ports
    settings = loader.acquire_settings(descriptor)
    worker = loader.acquire_worker(descriptor)
    program, arguments = worker.command()    # rechecks just before parent's launch

Keep each handle until the corresponding host/settings/process is actually
stopped, then close it. Counts span loader instances within this process only;
Phase 4B must coordinate other processes and installation state. Imported code
and its namespace intentionally survive zero leases. There is no hot unload,
version deletion, subprocess, GUI import, sys.path change or source fallback.

This is publisher authentication, NOT a Python sandbox. Captured, verified
source bytes close the check-to-import reopen window. A path-returning worker
contract cannot atomically bind QProcess/CreateProcess to a verified inode;
the parent must launch immediately and protect installed directories against
concurrent writers. Reverification is not a filesystem permission boundary.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from types import ModuleType
from typing import Callable

from ..feature_version_lease import CrossProcessLease, FeatureVersionLeaseCoordinator, FeatureVersionSelection, retain_process_lease
from .package_trust import (
    CompatibilityEvidence,
    FeaturePackageVerifier,
    PackageVerificationError,
    VerificationLimits,
    VerifiedFeatureDescriptor,
    VerifiedFile,
)

__all__ = [
    "CompatibilityEvidence",
    "FeaturePackageLoader",
    "FeaturePackageVerifier",
    "HostHandle",
    "LeaseCounts",
    "PackageVerificationError",
    "VerificationLimits",
    "VerifiedFeatureDescriptor",
    "VerifiedFile",
    "VersionLease",
    "WorkerHandle",
]


@dataclass(frozen=True)
class LeaseCounts:
    host: int = 0
    settings: int = 0
    worker: int = 0
    imported: bool = False

    @property
    def can_remove(self) -> bool:
        """Only this process's answer; attempted imports remain pinned until exit."""
        return not (self.host or self.settings or self.worker or self.imported)


@dataclass
class _Generation:
    descriptor: VerifiedFeatureDescriptor
    namespace: str = field(default_factory=lambda: "_pet_official_screen_" + uuid.uuid4().hex)
    leases: dict[str, set[object]] = field(default_factory=lambda: {"host": set(), "settings": set(), "worker": set()})
    imported: bool = False
    failed: bool = False
    factory: Callable | None = None
    verifier: FeaturePackageVerifier | None = None
    modules: dict[str, tuple[bytes | None, str | None, bool]] = field(default_factory=dict)
    load_lock: threading.RLock = field(default_factory=threading.RLock)
    loading_thread: int | None = None


_LOCK = threading.RLock()
_GENERATIONS: dict[tuple, _Generation] = {}


def _key(descriptor: VerifiedFeatureDescriptor) -> tuple:
    if not isinstance(descriptor, VerifiedFeatureDescriptor):
        raise PackageVerificationError("a verified descriptor is required")
    return (
        os.path.normcase(str(descriptor.root)),
        hashlib.sha256(descriptor.raw_manifest).digest(),
        descriptor.signature,
        descriptor.tree,
        descriptor.ancestors,
        descriptor.trust_status,
        descriptor.trust_anchor,
        descriptor.evidence,
    )


def _generation(descriptor: VerifiedFeatureDescriptor) -> _Generation:
    key = _key(descriptor)
    with _LOCK:
        if key not in _GENERATIONS:
            _GENERATIONS[key] = _Generation(descriptor)
        return _GENERATIONS[key]


def _module_table(namespace: str, sources: dict[str, bytes]) -> dict[str, tuple[bytes | None, str | None, bool]]:
    modules: dict[str, tuple[bytes | None, str | None, bool]] = {namespace: (None, None, True)}
    for path, source in sources.items():
        parts = path.split("/")
        for depth in range(1, len(parts)):
            parent = namespace + "." + ".".join(parts[:depth])
            prior = modules.get(parent)
            if prior is not None and not prior[2]:
                raise PackageVerificationError(f"ambiguous Python package/module: {path}")
            modules.setdefault(parent, (None, None, True))
        is_package = parts[-1] == "__init__.py"
        module_parts = parts[:-1] if is_package else [*parts[:-1], parts[-1][:-3]]
        if not all(part.isidentifier() for part in module_parts):
            raise PackageVerificationError(f"invalid host Python module path: {path}")
        name = namespace + "." + ".".join(module_parts)
        prior = modules.get(name)
        if prior is not None and (prior[0] is not None or prior[2] != is_package):
            raise PackageVerificationError(f"ambiguous Python package/module: {path}")
        modules[name] = (source, path, is_package)
    return modules


class _VerifiedSourceLoader(importlib.abc.Loader):
    def __init__(self, generation: _Generation, fullname: str):
        self.generation = generation
        self.fullname = fullname

    def create_module(self, spec):
        return None

    def exec_module(self, module: ModuleType):
        generation = self.generation
        if generation.failed:
            raise PackageVerificationError("failed host generation is quarantined until process exit")
        # Initial imports share one fully verified immutable source snapshot.
        # Deferred imports must recheck the directory too; never read fresh code.
        if generation.loading_thread != threading.get_ident():
            verifier = generation.verifier
            if verifier is None:
                raise PackageVerificationError("host generation has no verifier")
            verifier.reverify(generation.descriptor)
        source, path, is_package = generation.modules[self.fullname]
        if is_package:
            module.__path__ = []  # only the closed finder can resolve relative imports
        if source is not None:
            if path is None:
                raise PackageVerificationError("verified source has no relative path")
            filename = str(generation.descriptor.root / path)
            module.__file__ = filename
            exec(compile(source, filename, "exec", dont_inherit=True), module.__dict__)


class _VerifiedPackageFinder(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.generations: dict[str, _Generation] = {}

    def find_spec(self, fullname, path=None, target=None):
        namespace = fullname.split(".", 1)[0]
        with _LOCK:
            generation = self.generations.get(namespace)
        if generation is None:
            return None
        if fullname not in generation.modules:
            # A miss in our namespace must not fall through to filesystem importers.
            raise ModuleNotFoundError(f"not in verified host inventory: {fullname}", name=fullname)
        _, relative, is_package = generation.modules[fullname]
        origin = str(generation.descriptor.root / relative) if relative else "verified-official-namespace"
        return importlib.machinery.ModuleSpec(fullname, _VerifiedSourceLoader(generation, fullname), origin=origin, is_package=is_package)


_FINDER = _VerifiedPackageFinder()


class VersionLease:
    """Explicit release; dropping a Python reference does NOT prove a process/UI stopped."""

    def __init__(
        self,
        generation: _Generation,
        verifier: FeaturePackageVerifier,
        kind: str,
        *,
        process_lease: CrossProcessLease | None = None,
    ):
        self._generation = generation
        self._verifier = verifier
        self._kind = kind
        self._process_lease = process_lease
        self._token = object()
        with _LOCK:
            generation.leases[kind].add(self._token)

    @property
    def descriptor(self) -> VerifiedFeatureDescriptor:
        return self._generation.descriptor

    @property
    def closed(self) -> bool:
        with _LOCK:
            return self._token not in self._generation.leases[self._kind]

    def _check(self):
        if self.closed:
            raise PackageVerificationError("version lease is closed")
        self._verifier.reverify(self.descriptor)

    def close(self):
        # Token ownership, not a decrement against a mutable current-version slot.
        # Even copied/stale handles cannot decrement somebody else's generation.
        with _LOCK:
            self._generation.leases[self._kind].discard(self._token)
        if self._process_lease is not None:
            self._process_lease.close()
            self._process_lease = None

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class HostHandle(VersionLease):
    @property
    def namespace(self) -> str:
        return self._generation.namespace

    @property
    def factory(self) -> Callable:
        """Return, never invoke, the official factory; parent controls injection."""
        self._check()
        factory = self._generation.factory
        if factory is None:
            raise PackageVerificationError("host factory is unavailable")
        return factory


class WorkerHandle(VersionLease):
    def command(self) -> tuple[str, tuple[str, ...]]:
        """Call immediately before launch without a shell. Close only after process exit."""
        self._check()
        return str(self.descriptor.worker_path), self.descriptor.worker_args


class FeaturePackageLoader:
    def __init__(self, verifier: FeaturePackageVerifier):
        if not isinstance(verifier, FeaturePackageVerifier):
            raise PackageVerificationError("a Core package verifier is required")
        self.verifier = verifier

    def load_host(
        self,
        descriptor: VerifiedFeatureDescriptor,
        *,
        process_lease: CrossProcessLease | None = None,
    ) -> HostHandle:
        # Full inventory, digest and signature verification precedes *all* imports,
        # including package __init__.py, and also applies to cached generations.
        # When an installed-process lease is supplied, it is pinned immediately
        # before entering the verified interpreter and remains process-owned.
        lease_retained = False
        try:
            sources = self.verifier._snapshot(descriptor)
            generation = _generation(descriptor)
            with generation.load_lock:
                if generation.failed:
                    raise PackageVerificationError("failed host generation is quarantined until process exit")
                if generation.factory is None:
                    modules = _module_table(generation.namespace, sources)
                    generation.modules = modules
                    generation.verifier = self.verifier
                    with _LOCK:
                        if process_lease is not None:
                            retain_process_lease(process_lease)
                            lease_retained = True
                        generation.imported = True  # pin even partially executed failures
                        _FINDER.generations[generation.namespace] = generation
                        if _FINDER not in sys.meta_path:
                            sys.meta_path.insert(0, _FINDER)
                    generation.loading_thread = threading.get_ident()
                    try:
                        module = importlib.import_module(generation.namespace + ".host.factory")
                        factory = getattr(module, "create_host", None)
                        if not callable(factory):
                            raise PackageVerificationError("official create_host is not callable")
                        generation.factory = factory
                    except BaseException as exc:
                        generation.failed = True
                        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                            raise
                        raise PackageVerificationError(f"official host import failed: {exc}") from exc
                    finally:
                        generation.loading_thread = None
            if process_lease is not None and not lease_retained:
                retain_process_lease(process_lease)
                lease_retained = True
            return HostHandle(generation, self.verifier, "host")
        except BaseException:
            if process_lease is not None and not lease_retained:
                process_lease.close()
            raise

    def load_installed_host(
        self,
        selection: FeatureVersionSelection,
        coordinator: FeatureVersionLeaseCoordinator,
    ) -> HostHandle:
        """Acquire the cross-process host lease before verified import.

        This is the production installed-package path.  Validation-only callers
        continue to use :meth:`load_host` directly and remain process-local.
        """
        descriptor = selection.descriptor
        if not isinstance(descriptor, VerifiedFeatureDescriptor):
            raise PackageVerificationError("an installed verified descriptor is required")
        process_lease = coordinator.acquire_host(selection)
        return self.load_host(descriptor, process_lease=process_lease)

    def acquire_settings(self, descriptor: VerifiedFeatureDescriptor) -> VersionLease:
        self.verifier.reverify(descriptor)
        return VersionLease(_generation(descriptor), self.verifier, "settings")

    def acquire_installed_settings(
        self,
        selection: FeatureVersionSelection,
        coordinator: FeatureVersionLeaseCoordinator,
    ) -> VersionLease:
        descriptor = selection.descriptor
        if not isinstance(descriptor, VerifiedFeatureDescriptor):
            raise PackageVerificationError("an installed verified descriptor is required")
        process_lease = coordinator.acquire_settings(selection)
        try:
            self.verifier.reverify(descriptor)
            return VersionLease(_generation(descriptor), self.verifier, "settings", process_lease=process_lease)
        except BaseException:
            process_lease.close()
            raise

    def acquire_worker(self, descriptor: VerifiedFeatureDescriptor) -> WorkerHandle:
        self.verifier.reverify(descriptor)
        return WorkerHandle(_generation(descriptor), self.verifier, "worker")

    def lease_counts(self, descriptor: VerifiedFeatureDescriptor) -> LeaseCounts:
        with _LOCK:
            generation = _GENERATIONS.get(_key(descriptor))
            if generation is None:
                return LeaseCounts()
            return LeaseCounts(
                host=len(generation.leases["host"]),
                settings=len(generation.leases["settings"]),
                worker=len(generation.leases["worker"]),
                imported=generation.imported,
            )
