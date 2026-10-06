"""Stream immutable local release ZIPs, never installing or executing payloads.

An approved verifier owns DLC trust. Core archives repeat the actual compiled
and helper audit and compare the trusted build receipt. The sole omitted
runtime artifact is the regular, empty Core code-lock file. No user data,
portable markers, unknown files or changed payloads are silently included.
Failed writes remain owned, unsigned partial evidence; retries need a new name.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import zipfile
from pathlib import Path

from pet import feature_state_io as io
from pet.plugins.package_trust import FeaturePackageVerifier, PackageVerificationError, VerificationLimits
from scripts import build_feature_release as builder
from scripts import feature_release_materials as materials
from scripts.feature_release_signing import ReleaseSigningError
from scripts.release_distribution import PRODUCT, _json


class ArchiveBuildError(ValueError):
    """Bounded displayable release reason, not a success for partial output."""


def _destination(source: Path, destination: Path, owned_root: Path, budget_bytes: int) -> tuple[Path, Path, Path]:
    source, destination, owned = Path(source).absolute(), Path(destination).absolute(), Path(owned_root).absolute()
    for path in (source, destination, owned):
        io.safe_path(path)
    if type(budget_bytes) is not int or not 0 < budget_bytes <= materials.MAX_GENERATED_BYTES:
        raise ArchiveBuildError("generation_budget_exceeded")
    if not source.is_dir() or not owned.is_dir() or not destination.parent.is_dir():
        raise ArchiveBuildError("invalid_archive_material")
    if destination == owned or not destination.resolve().is_relative_to(owned.resolve()):
        raise ArchiveBuildError("output_outside_owned_root")
    if destination.resolve().is_relative_to(source.resolve()) or destination.suffix.casefold() != ".zip":
        raise ArchiveBuildError("invalid_archive_target")
    if destination.exists():
        raise ArchiveBuildError("output_exists")
    return source, destination, owned


def _stream_zip(table, destination: Path, owned: Path, budget: int, *, marker: bytes | None = None):
    total = sum(item[2] for item in table.values()) + (len(marker) if marker else 0)
    reserve = total + total // 50 + (len(table) + 1) * 4096
    if materials._owned_bytes(owned, budget) + reserve > budget:
        raise ArchiveBuildError("generation_budget_exceeded")
    # ZipFile.open streams in bounded blocks; no complete DLL/media allocation.
    with destination.open("xb") as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for name, (origin, digest, size, stamp) in sorted(table.items()):
                io.safe_path(origin)
                if materials._identity(origin.stat()) != stamp:
                    raise ArchiveBuildError("source_changed")
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                with archive.open(entry, "w", force_zip64=True) as target:
                    measured, count = materials._digest(origin, VerificationLimits().max_file_bytes, target)
                if measured != digest or count != size:
                    raise ArchiveBuildError("source_changed")
            if marker is not None:
                archive.writestr("portable.json", marker)
        output.flush()
        os.fsync(output.fileno())
    materials._owned_bytes(owned, budget)
    return materials._digest(destination, VerificationLimits().max_total_bytes)


def _table(root: Path):
    return {key.removeprefix("bundle/"): value for key, value in builder._table(root, "bundle").items()}


def _reason(exc):
    return exc.code if isinstance(exc, io.StateError) else str(exc) if isinstance(exc, ValueError) else "archive_io_error"


def build_feature_zip(
    source: Path, destination: Path, verifier: FeaturePackageVerifier, *, owned_root: Path, budget_bytes: int = materials.MAX_GENERATED_BYTES
):
    start = time.perf_counter()
    try:
        source, destination, owned = _destination(source, destination, owned_root, budget_bytes)
        descriptor = verifier.verify(source)
        table = _table(source)
        digest, size = _stream_zip(table, destination, owned, budget_bytes)
        if _table(source) != table:
            raise ArchiveBuildError("source_changed")
        verifier.reverify(descriptor)
        return dict(
            kind="ai-dlc" if descriptor.id == "official.ai-chat" else "screen-dlc",
            feature_id=descriptor.id,
            version=descriptor.version,
            manifest_sha256=hashlib.sha256(descriptor.raw_manifest).hexdigest(),
            path=str(destination),
            sha256=digest,
            size_bytes=size,
            elapsed_seconds=time.perf_counter() - start,
        )
    except (io.StateError, OSError, PackageVerificationError, materials.MaterialError, builder.BuildError, ReleaseSigningError) as exc:
        raise ArchiveBuildError(_reason(exc)) from None


def _core_table(bundle: Path):
    table = _table(bundle)
    lock = table.pop(".core-files.lock", None)
    if lock is not None and lock[2] != 0:
        raise ArchiveBuildError("unexpected_core_runtime_material")
    return table


def build_core_zip(
    build_root: Path,
    destination: Path,
    *,
    probe_manifest_sha256: str,
    owned_root: Path,
    portable: bool = False,
    budget_bytes: int = materials.MAX_GENERATED_BYTES,
):
    start = time.perf_counter()
    try:
        if type(portable) is not bool:
            raise ArchiveBuildError("invalid_portable_selection")
        root = Path(build_root).absolute()
        bundle, destination, owned = _destination(root / "dist" / PRODUCT, destination, owned_root, budget_bytes)
        raw = io.read_bytes(root / "evidence/artifact.json", 8 * 1024**2)
        receipt = _json(raw)
        if (
            not isinstance(receipt, dict)
            or type(receipt.get("schema")) is not int
            or receipt.get("schema") != 1
            or receipt.get("scope") != "production-frozen-unsigned"
        ):
            raise ArchiveBuildError("invalid_build_receipt")
        _, modules = builder.inspect_core_bundle(bundle, probe_manifest_sha256=probe_manifest_sha256)
        table = _core_table(bundle)
        expected = {name: dict(sha256=item[1], size=item[2]) for name, item in table.items()}
        if (
            not table
            or receipt.get("files") != expected
            or receipt.get("size_bytes") != sum(item[2] for item in table.values())
            or receipt.get("pyz_modules") != sorted(modules)
        ):
            raise ArchiveBuildError("source_changed")
        marker = (json.dumps(dict(format_version=1, product_id=PRODUCT, data="data"), sort_keys=True) + "\n").encode() if portable else None
        digest, size = _stream_zip(table, destination, owned, budget_bytes, marker=marker)
        if io.read_bytes(root / "evidence/artifact.json", 8 * 1024**2) != raw or _core_table(bundle) != table:
            raise ArchiveBuildError("source_changed")
        return dict(
            kind="portable-zip" if portable else "core-zip",
            path=str(destination),
            sha256=digest,
            size_bytes=size,
            unpacked_bytes=sum(item[2] for item in table.values()) + (len(marker) if marker else 0),
            elapsed_seconds=time.perf_counter() - start,
        )
    except (io.StateError, OSError, PackageVerificationError, materials.MaterialError, builder.BuildError, ReleaseSigningError) as exc:
        raise ArchiveBuildError(_reason(exc)) from None
