"""Closed production-source assembly, not signing or proof of a frozen build.

Only caller-owned, exclusive output is written. No code in a candidate is run;
full signature/compatibility verification remains the signer's responsibility.
Failed outputs are retained as unsigned diagnostic evidence, never overwritten.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from pet import feature_package_files as files
from pet import feature_state_io as io
from pet.official_features import AI_OWNER, SCREEN_OWNER, official_feature
from pet.plugins.package_trust import FeaturePackageVerifier, PackageVerificationError, VerificationLimits

MAX_GENERATED_BYTES = 6 * 1024**3
CORE_REQUIRED = frozenset(
    {
        "pet.app",
        "pet.mod_api.v1",
        "pet.mod_api.worker_client",
        "pet.runtime_layout",
        "pet.official_features",
        "pet.feature_package_transactions",
        "pet.feature_package_startup",
        "pet.plugins.feature_packages",
        "pet.feature_ports",
        "pet.api_ports",
        "pet.api_config",
        "pet.api_migration",
        "pet.settings_api",
        "pet.credentials",
        "pet.async_exit",
        "pet.ai_bindings",
        "pet.runtime_data_import",
        "pet.runtime_data_import_entry",
        "pet.runtime_data_import_ui",
        "pet.runtime_credential_import",
        "pet.runtime_resource_import",
        "pet.core_maintenance",
        "pet.core_registration_cleanup",
        "pet.core_uninstall",
        "pet.core_uninstall_ui",
        "pet.local_package_intents",
    }
)
CORE_EXCLUDES = (
    "features",
    "pet.chat",
    "pet.quick_chat",
    "pet.island_chat",
    "pet.file_interpret",
    "pet.settings_file_interpret",
    "pet.screen_understanding",
    "pet.vision",
    "pet.proactive",
    "pet.proactive_limiter",
    "pet.proactive_memory",
    "pet.workers.proactive_screen_adapter",
    "pet.workers.proactive_screen_worker",
)
_SOURCE_SUFFIXES = frozenset({".py", ".qss", ".json"})
_RESOURCE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".svg"})


class MaterialError(ValueError):
    """Safe reason code only; do not log source contents or secret paths."""


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_nlink


def _digest(path: Path, limit: int, output=None) -> tuple[str, int]:
    io.safe_path(path)
    before = _identity(path.stat())
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(min(1024 * 1024, limit - size + 1)):
            size += len(block)
            if size > limit:
                raise MaterialError("payload_size_limit")
            digest.update(block)
            if output is not None:
                output.write(block)
        # Windows path stat synthesizes executable mode bits from .exe; fstat
        # has no filename and does not. Compare object identity/file type here,
        # then all path mode bits again after the read. Do not drop inode/link checks.
        opened = _identity(os.fstat(stream.fileno()))
        if opened[:4] != before[:4] or stat.S_IFMT(opened[4]) != stat.S_IFMT(before[4]) or opened[5] != before[5]:
            raise MaterialError("source_changed")
    io.safe_path(path)
    if _identity(path.stat()) != before:
        raise MaterialError("source_changed")
    return digest.hexdigest(), size


def _tree(root: Path, prefix: str, limits: VerificationLimits, suffixes=None):
    result = {}
    for name, path, stamp in files.inventory(root, limits):
        # inventory checks every node, including caches, before they are omitted.
        if stat.S_ISDIR(stamp.st_mode) or "__pycache__" in Path(name).parts or path.suffix == ".pyc":
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            raise MaterialError("unexpected_source_material")
        target = files.canonical_name(prefix + "/" + name, limits)
        if any(part.startswith("validation_") or part == "validation" for part in Path(name).parts):
            raise MaterialError("validation_material_forbidden")
        digest, size = _digest(path, limits.max_file_bytes)
        result[target] = (path, digest, size, _identity(stamp))
    return result


def _owned_bytes(root: Path, budget: int) -> int:
    total, count = 0, 0
    if not root.exists():
        return 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            io.safe_path(path)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                raise MaterialError("unsafe_owned_tree")
            count += 1
            total += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if count > 200000 or total > budget:
                raise MaterialError("generation_budget_exceeded")
    return total


def prepare_unsigned_feature(
    repository_root: Path,
    destination: Path,
    feature_id: str,
    *,
    key_id: str,
    version: str,
    owned_root: Path,
    worker_bundle: Path | None = None,
    budget_bytes: int = MAX_GENERATED_BYTES,
) -> dict:
    """Create one complete v2 snapshot from the two explicitly registered owners.

    worker_bundle must be an independently built onedir tree; accepting its
    bytes here is not evidence that its protocol, lease or OS sandbox passed.
    """
    try:
        feature = official_feature(feature_id)
        repository_root, destination, owned_root = (Path(p).absolute() for p in (repository_root, destination, owned_root))
        for path in (repository_root, destination, owned_root):
            io.safe_path(path)
        if destination == owned_root or not destination.resolve().is_relative_to(owned_root.resolve()):
            raise MaterialError("output_outside_owned_root")
        if destination.exists():
            raise MaterialError("output_exists")
        if type(budget_bytes) is not int or not 0 < budget_bytes <= MAX_GENERATED_BYTES:
            raise MaterialError("invalid_generation_budget")
        if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key_id):
            raise MaterialError("invalid_key_id")
        limits = VerificationLimits()
        selected = "ai_chat" if feature_id == AI_OWNER else "screen_understanding"
        roots: list[tuple[Path, str, frozenset[str] | None]] = [(repository_root / "features" / selected / "host", "host", _SOURCE_SUFFIXES)]
        common = repository_root / "features" / selected / "common"
        if common.exists():
            roots.append((common, "common", _SOURCE_SUFFIXES))
        if feature_id == SCREEN_OWNER:
            if worker_bundle is None:
                raise MaterialError("worker_bundle_required")
            worker_bundle = Path(worker_bundle).absolute()
            io.safe_path(worker_bundle)
            if not (worker_bundle / "proactive-screen-worker.exe").is_file():
                raise MaterialError("worker_bundle_required")
            roots.append((worker_bundle, "worker", None))
        elif worker_bundle is not None:
            raise MaterialError("host_only_worker_forbidden")
        resources = repository_root / "assets" / "chat"
        if feature_id == AI_OWNER and resources.exists():
            roots.append((resources, "resources/chat", _RESOURCE_SUFFIXES))
        inventory = {}
        for root, prefix, suffixes in roots:
            if destination.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination.resolve()):
                raise MaterialError("unsafe_snapshot_target")
            inventory.update(_tree(root, prefix, limits, suffixes))
        manifest = {
            "format_version": 2,
            "id": feature.id,
            "key_id": key_id,
            "version": version,
            "api_version": "1",
            "core_requires": ">=4.2.4,<6.0.0" if feature_id == SCREEN_OWNER else ">=4.2.3,<6.0.0",
            "platforms": ["win32"],
            "capabilities": sorted(feature.capabilities),
            "factory": feature.factory,
            "execution_kind": feature.execution_kind,
            "worker": {"path": "worker/proactive-screen-worker.exe", "args": []} if feature_id == SCREEN_OWNER else None,
            "files": {name: {"sha256": item[1], "size": item[2]} for name, item in sorted(inventory.items())},
        }
        raw = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        verifier = FeaturePackageVerifier(core_version="4.2.4", api_version="1", feature_id=feature.id, allowed_capabilities=feature.capabilities)
        verifier._schema(raw)  # One Core schema, not a weaker assembler-only format.
        if len(raw) > limits.max_manifest_bytes:
            raise MaterialError("manifest_size_limit")
        cost = len(raw) + sum(item[2] for item in inventory.values())
        if _owned_bytes(owned_root, budget_bytes) + cost > budget_bytes:
            raise MaterialError("generation_budget_exceeded")
        destination.mkdir(parents=True, exist_ok=False)
        for name, (source, digest, size, stamp) in sorted(inventory.items()):
            target = destination / name
            io.safe_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            io.safe_path(target)
            with target.open("xb") as output:
                actual = _digest(source, limits.max_file_bytes, output)
            if actual != (digest, size) or _identity(source.stat()) != stamp:
                raise MaterialError("source_changed")
        for root, prefix, suffixes in roots:
            current = _tree(root, prefix, limits, suffixes)
            expected = {name: item for name, item in inventory.items() if name.startswith(prefix + "/")}
            if current != expected:
                raise MaterialError("source_changed")
        io.safe_path(destination / "manifest.json")
        with (destination / "manifest.json").open("xb") as stream:
            stream.write(raw)
        return manifest
    except MaterialError:
        raise
    except (io.StateError, PackageVerificationError, OSError, ValueError, TypeError):
        raise MaterialError("invalid_release_material") from None


def audit_core_inventory(modules) -> list[str]:
    names = set(modules)
    if any(not isinstance(name, str) or not name for name in names):
        raise MaterialError("invalid_core_inventory")
    for name in names:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in CORE_EXCLUDES) or any(
            part.startswith("validation_") or part == "validation" for part in name.split(".")
        ):
            raise MaterialError("forbidden_core_module")
    if not CORE_REQUIRED <= names:
        raise MaterialError("required_core_module_missing")
    return sorted(names)


def host_core_dependencies(repository_root: Path) -> list[str]:
    """AST dependency hints only; the final built PYZ must be audited separately."""
    result = set(CORE_REQUIRED)
    for selected in ("ai_chat", "screen_understanding"):
        for component in ("host", "common"):
            root = Path(repository_root) / "features" / selected / component
            if not root.exists():
                continue
            for _, source, stamp in files.inventory(root, VerificationLimits()):
                if not stat.S_ISREG(stamp.st_mode) or source.suffix != ".py" or "__pycache__" in source.parts:
                    continue
                tree = ast.parse(io.read_bytes(source, VerificationLimits().max_host_bytes), filename=source.name)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        result.update(alias.name for alias in node.names if alias.name.startswith("pet."))
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        if node.module == "pet":
                            result.update("pet." + alias.name for alias in node.names)
                        elif node.module and node.module.startswith("pet."):
                            result.add(node.module)
    return audit_core_inventory(result)
