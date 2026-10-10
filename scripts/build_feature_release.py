"""Closed production builders for the unified Core and independent screen Worker.

No private keys, validation entrypoints, source runtime fallback or synthetic
boundaries. Preparation alone is not proof of a frozen or signed delivery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from pet import feature_state_io as io
from pet.feature_probe_windows import TrustedProbeBundle
from pet.official_features import OFFICIAL_FEATURES
from pet.plugins.package_trust import VerificationLimits
from scripts import feature_release_materials as materials
from scripts.build_screen_delivery import COLLECT, DATA_ROOTS, HEAVY_MODULES, native_module_inventory, sanitized_build_environment
from scripts.build_screen_worker import WORKER_NAME, WORKER_SOURCES, compiler_command, inspect_archive
from scripts.release_distribution import PRODUCT, read_public_policy

MAX_GENERATED_BYTES = materials.MAX_GENERATED_BYTES
CORE_BUILD_RESERVE = 2 * 1024**3
WORKER_BUILD_RESERVE = 768 * 1024**2
CORE_EXCLUDES = (*materials.CORE_EXCLUDES, *HEAVY_MODULES)
CORE_ENTRY = 'from pet.__main__ import _main\n\nif __name__ == "__main__":\n    raise SystemExit(_main())\n'
WORKER_ENTRY = 'from pet.workers.screen_entry import run_screen_worker_entry\n\nif __name__ == "__main__":\n    raise SystemExit(run_screen_worker_entry())\n'


class BuildError(ValueError):
    """Safe reason code; build logs are local owned evidence, never public secrets."""


def _target(repository_root: Path, output: Path, owned_root: Path, budget: int):
    root, output, owned = Path(repository_root).absolute(), Path(output).absolute(), Path(owned_root).absolute()
    if type(budget) is not int or not 0 < budget <= MAX_GENERATED_BYTES:
        raise BuildError("generation_budget_exceeded")
    for path in (root, output, owned):
        io.safe_path(path)
    if not root.is_dir() or not owned.is_dir():
        raise BuildError("invalid_build_material")
    if output == owned or not output.resolve().is_relative_to(owned.resolve()):
        raise BuildError("output_outside_owned_root")
    if output.exists():
        raise BuildError("output_exists")
    if root.resolve().is_relative_to(output.resolve()) or output.resolve().is_relative_to(root.resolve() / "pet"):
        raise BuildError("unsafe_snapshot_target")
    return root, output, owned


def _encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write(path: Path, raw: bytes):
    io.safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    io.safe_path(path)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _banned(module):
    return any(module == name or module.startswith(name + ".") for name in CORE_EXCLUDES)


def _trust(policy: Path, approved: dict):
    records = read_public_policy(policy)
    for key, record in records.items():
        if approved.get(key) != record.fingerprint:
            raise BuildError("public_identity_not_approved")
        if any(
            part in {"test", "testing", "dev", "validation", "synthetic", "manual", "acceptance", "generated", "integration"}
            for part in re.split(r"[-_.]", key.lower())
        ):
            raise BuildError("production_trust_policy")
    for owner, feature in OFFICIAL_FEATURES.items():
        if not any(not record.revoked and owner in record.feature_ids and feature.capabilities <= record.capabilities for record in records.values()):
            raise BuildError("production_trust_policy")
    anchors = tuple((key, record.public_key_hex) for key, record in sorted(records.items()))
    scopes = tuple(
        (
            key,
            dict(feature_ids=tuple(sorted(record.feature_ids)), capabilities=tuple(sorted(record.capabilities)), revoked=record.revoked, allow_legacy_v1=False),
        )
        for key, record in sorted(records.items())
    )
    return records, anchors, scopes


def _policy(anchors, scopes, digest):
    capabilities = frozenset(cap for feature in OFFICIAL_FEATURES.values() for cap in feature.capabilities)
    return (
        '"""Frozen release policy, never inferred from candidate files or environment."""\n'
        f"OFFICIAL_FEATURE_TRUST_ANCHORS = {anchors!r}\n"
        f"OFFICIAL_FEATURE_KEY_POLICIES = {scopes!r}\n"
        'FEATURE_API_VERSION = "1"\n'
        "ALLOW_LOCAL_PACKAGE_ACTIVATION = True\n"
        f"FEATURE_CAPABILITIES = frozenset({sorted(capabilities)!r})\n"
        'PROBE_BUNDLE_DIRECTORY = "feature-probe"\n'
        f"PROBE_BUNDLE_MANIFEST_SHA256 = {digest!r}\n"
        "VALIDATION_BUILD = False\n"
    ).encode()


def _table(root: Path, prefix: str):
    return materials._tree(root, prefix, VerificationLimits())


# The trusted repository has pnpm hardlinks/reparse nodes under node_modules.
# They are developer dependencies, NOT release input. This fixed publication
# list does not relax any candidate-package or published-file link boundary.
BRIDGE_FILES = ("index.js", "package.json", "cordis.patch.yml")


def _published_integrations(root: Path):
    table = {}
    io.safe_path(root)
    for name in BRIDGE_FILES:
        path = root / "dsh-pet-bridge" / name
        io.safe_path(path)
        stamp = path.lstat()
        if not stat.S_ISREG(stamp.st_mode):
            raise BuildError("invalid_build_material")
        digest, size = materials._digest(path, VerificationLimits().max_file_bytes)
        table["integrations/dsh-pet-bridge/" + name] = (path, digest, size, materials._identity(stamp))
    return table


def _copy(table, source: Path):
    for name, (origin, digest, size, stamp) in sorted(table.items()):
        target = source / name
        io.safe_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        io.safe_path(target)
        with target.open("xb") as stream:
            actual = materials._digest(origin, VerificationLimits().max_file_bytes, stream)
        if actual != (digest, size) or materials._identity(origin.stat()) != stamp:
            raise BuildError("source_changed")


def _snapshot(root: Path):
    return {name: {"sha256": value[1], "size": value[2]} for name, value in _table(root, "source").items()}


def _budget(owned, cost, budget):
    try:
        used = materials._owned_bytes(owned, budget)
    except materials.MaterialError as exc:
        raise BuildError(str(exc)) from None
    if used + cost > budget:
        raise BuildError("generation_budget_exceeded")


def prepare_core(
    repository_root: Path,
    output: Path,
    *,
    owned_root: Path,
    version: str,
    public_policy: Path,
    approved_fingerprints: dict,
    probe_bundle: Path,
    probe_manifest_sha256: str,
    budget_bytes: int = MAX_GENERATED_BYTES,
) -> dict:
    """Exclusive Core snapshot with independently approved public identities only."""
    try:
        root, output, owned = _target(repository_root, output, owned_root, budget_bytes)
        if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})", version):
            raise BuildError("invalid_core_version")
        records, anchors, scopes = _trust(Path(public_policy), approved_fingerprints)
        try:
            TrustedProbeBundle(Path(probe_bundle), probe_manifest_sha256).verify()
        except (OSError, ValueError, RuntimeError):
            raise BuildError("trusted_probe_unavailable") from None
        roots = [(root / "pet", "pet"), (Path(probe_bundle).absolute(), "feature-probe")]
        roots.extend((root / name, name) for name in DATA_ROOTS if (root / name).is_dir() and not name.startswith("pet/") and name != "integrations")
        tables = [(origin, prefix, _table(origin, prefix)) for origin, prefix in roots]
        if (root / "integrations").is_dir():
            tables.append((root / "integrations", "integrations", _published_integrations(root / "integrations")))
        table = {}
        for _, prefix, inventory in tables:
            for name, item in inventory.items():
                module = ".".join(Path(name).with_suffix("").parts)
                if prefix == "pet" and _banned(module):
                    continue
                if name in table:
                    raise BuildError("overlapping_source_material")
                table[name] = item
        generated = {
            "assets/icon.ico": io.read_bytes(root / "assets/icon.ico", 1024 * 1024),
            "core_entry.py": CORE_ENTRY.encode(),
            "build_variant.py": b'VARIANT = "core-webm"\n',
            "pet/feature_distribution.py": b'"""Build-owned availability, not installed state."""\nBUILTIN_AI = False\nBUILTIN_SCREEN = False\n',
            "pet/feature_build_policy.py": _policy(anchors, scopes, probe_manifest_sha256),
        }
        original = io.read_bytes(root / "pet/__init__.py", VerificationLimits().max_host_bytes).decode("utf-8")
        adjusted, count = re.subn(r'(?m)^__version__\s*=\s*[\'"][^\'"\r\n]+[\'"]\s*$', f'__version__ = "{version}"', original)
        if count != 1:
            raise BuildError("invalid_core_version_source")
        generated["pet/__init__.py"] = adjusted.encode()
        _budget(owned, sum(item[2] for item in table.values()) + sum(map(len, generated.values())) + 128 * 1024, budget_bytes)
        output.mkdir(parents=True, exist_ok=False)
        source = output / "source"
        _copy({name: item for name, item in table.items() if name not in generated}, source)
        for name, raw in generated.items():
            _write(source / name, raw)
        for origin, prefix, expected in tables:
            if (_published_integrations(origin) if prefix == "integrations" else _table(origin, prefix)) != expected:
                raise BuildError("source_changed")
        TrustedProbeBundle(source / "feature-probe", probe_manifest_sha256).verify()
        hidden = materials.host_core_dependencies(root)
        # Registering host factories is dynamic; only generic ports enter Core.
        hidden = sorted(set(hidden) | {"keyring.backends.Windows", "PySide6.QtWidgets", "pet.__main__"})
        datas = [(str(source / prefix), prefix) for _, prefix, _ in tables if prefix != "pet"]
        datas.append((str(source / "assets/icon.ico"), "assets"))
        datas.extend((str(source / name), name) for name in DATA_ROOTS if name.startswith("pet/") and (source / name).is_dir())
        spec = (
            "from PyInstaller.utils.hooks import collect_all\n"
            f"datas={datas!r}\nbinaries=[]\nhiddenimports={hidden!r}\n"
            f"for module in {COLLECT!r}:\n    d,b,h=collect_all(module)\n    datas+=d; binaries+=b; hiddenimports+=h\n"
            f"a=Analysis([{str(source / 'core_entry.py')!r}],pathex=[{str(source)!r}],datas=datas,binaries=binaries,hiddenimports=hiddenimports,excludes={CORE_EXCLUDES!r})\n"
            "pyz=PYZ(a.pure)\n"
            f"exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name={PRODUCT!r},console=False,upx=False,icon={str(source / 'assets/icon.ico')!r})\n"
            f"coll=COLLECT(exe,a.binaries,a.datas,name={PRODUCT!r},upx=False)\n"
        )
        _write(output / "core.spec", spec.encode())
        metadata = dict(
            schema=1,
            scope="production-source",
            product=PRODUCT,
            version=version,
            public_fingerprints={key: record.fingerprint for key, record in records.items()},
            probe_manifest_sha256=probe_manifest_sha256,
            hidden_imports=hidden,
            excluded_modules=list(CORE_EXCLUDES),
            source_files=_snapshot(source),
            spec_sha256=hashlib.sha256(spec.encode()).hexdigest(),
        )
        _write(output / "evidence/build-input.json", _encoded(metadata))
        _budget(owned, 0, budget_bytes)
        return metadata
    except BuildError:
        raise
    except (io.StateError, OSError, ValueError, TypeError):
        raise BuildError("invalid_build_material") from None


def prepare_worker(repository_root: Path, output: Path, *, owned_root: Path, budget_bytes: int = MAX_GENERATED_BYTES) -> dict:
    """Capture production-only sources; frozen execution must still pass LPAC."""
    try:
        root, output, owned = _target(repository_root, output, owned_root, budget_bytes)
        table = {}
        for name in WORKER_SOURCES:
            path = root / name
            digest, size = materials._digest(path, VerificationLimits().max_file_bytes)
            table[name] = (path, digest, size, materials._identity(path.stat()))
        _budget(owned, sum(item[2] for item in table.values()) + len(WORKER_ENTRY) + 16 * 1024, budget_bytes)
        output.mkdir(parents=True, exist_ok=False)
        source = output / "source"
        _copy(table, source)
        _write(source / "worker_entry.py", WORKER_ENTRY.encode())
        metadata = dict(
            schema=1,
            scope="production-worker-source",
            product=WORKER_NAME,
            source_files={name.removeprefix("source/"): value for name, value in _snapshot(source).items()},
        )
        _write(output / "evidence/build-input.json", _encoded(metadata))
        return metadata
    except BuildError:
        raise
    except (io.StateError, OSError, ValueError, TypeError):
        raise BuildError("invalid_build_material") from None


def _environment():
    import PySide6

    return sanitized_build_environment(
        os.environ, trusted_directories=(Path(PySide6.__file__).parent, Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
    )


def _run(output: Path, command: list[str], owned: Path, reserve: int, budget: int):
    _budget(owned, reserve, budget)
    environment, removed = _environment()
    _write(output / "evidence/build-command.json", _encoded(dict(arguments=command, cwd=str(output / "source"), excluded_path_directories=removed)))
    start = time.perf_counter()
    with (output / "evidence/pyinstaller.log").open("xb") as log:
        result = subprocess.run(command, cwd=output / "source", env=environment, stdout=log, stderr=subprocess.STDOUT, check=False, timeout=1800)
    _budget(owned, 0, budget)
    if result.returncode:
        raise BuildError("compiler_failed")
    return time.perf_counter() - start


def _artifact(bundle: Path, output: Path, modules, seconds: float):
    table = _table(bundle, "bundle")
    files = {name.removeprefix("bundle/"): {"sha256": item[1], "size": item[2]} for name, item in table.items()}
    _write(
        output / "evidence/artifact.json",
        _encoded(
            dict(
                schema=1,
                scope="production-frozen-unsigned",
                build_seconds=round(seconds, 3),
                files=files,
                size_bytes=sum(item["size"] for item in files.values()),
                pyz_modules=sorted(modules),
            )
        ),
    )


def inspect_core_bundle(bundle: Path, *, probe_manifest_sha256: str) -> tuple[Path, list[str]]:
    """Read-only actual PYZ/native/resource audit shared by build and archive."""
    from PyInstaller.archive.readers import CArchiveReader

    bundle = Path(bundle)
    executable = bundle / (PRODUCT + ".exe")
    io.safe_path(executable)
    archive = CArchiveReader(str(executable))
    pyz = [name for name, item in archive.toc.items() if item[-1] == "z"]
    if len(pyz) != 1 or any("validation" in name.lower() for name in archive.toc):
        raise BuildError("invalid_core_archive")
    native = native_module_inventory(bundle / "_internal")
    modules = materials.audit_core_inventory([*archive.open_embedded_archive(pyz[0]).toc, *native])
    for name in ("assets/chat", "pet/chat", "features"):
        if (bundle / "_internal" / name).exists():
            raise BuildError("forbidden_core_resource")
    TrustedProbeBundle(bundle / "_internal/feature-probe", probe_manifest_sha256).verify()
    if list((bundle / "_internal").glob("icu*.dll")):
        raise BuildError("external_icu_in_core")
    return executable, sorted(modules)


def verify_core_bundle(output: Path, *, probe_manifest_sha256: str, build_seconds: float = 0) -> Path:
    """Seal an exclusive build receipt only after the read-only compiled audit."""
    bundle = Path(output) / "dist" / PRODUCT
    executable, modules = inspect_core_bundle(bundle, probe_manifest_sha256=probe_manifest_sha256)
    _artifact(bundle, Path(output), modules, build_seconds)
    return executable


def build_core(repository_root: Path, output: Path, **kwargs) -> Path:
    metadata = prepare_core(repository_root, output, **kwargs)
    output = Path(output).absolute()
    if _snapshot(output / "source") != metadata["source_files"]:
        raise BuildError("source_changed")
    command = [sys.executable, "-m", "PyInstaller", "--distpath", str(output / "dist"), "--workpath", str(output / "build"), str(output / "core.spec")]
    seconds = _run(output, command, Path(kwargs["owned_root"]).absolute(), CORE_BUILD_RESERVE, kwargs.get("budget_bytes", MAX_GENERATED_BYTES))
    if _snapshot(output / "source") != metadata["source_files"] or materials._digest(output / "core.spec", 1024**2)[0] != metadata["spec_sha256"]:
        raise BuildError("source_changed")
    return verify_core_bundle(output, probe_manifest_sha256=metadata["probe_manifest_sha256"], build_seconds=seconds)


def build_worker(
    repository_root: Path, output: Path, *, owned_root: Path, probe_bootloader: Path, probe_native_extension: Path, budget_bytes: int = MAX_GENERATED_BYTES
) -> Path:
    # Production refuses the optional/ordinary loader route of old validation builds.
    from scripts.build_feature_probe import finalize_owned_headless_runtime

    verify_native_inputs(Path(probe_bootloader), Path(probe_native_extension))
    io.safe_path(Path(probe_native_extension))
    if Path(probe_native_extension).name != "_dsh_probe_native.pyd":
        raise BuildError("invalid_native_leaf")
    metadata = prepare_worker(repository_root, output, owned_root=owned_root, budget_bytes=budget_bytes)
    output = Path(output).absolute()
    if {name.removeprefix("source/"): value for name, value in _snapshot(output / "source").items()} != metadata["source_files"]:
        raise BuildError("source_changed")
    raw = io.read_bytes(Path(probe_native_extension), 16 * 1024**2)
    _write(output / "source/_dsh_probe_native.pyd", raw)
    _write(
        output / "evidence/headless-input.json",
        _encoded(dict(bootloader_sha256=materials._digest(Path(probe_bootloader), 16 * 1024**2)[0], native_leaf_sha256=hashlib.sha256(raw).hexdigest())),
    )
    command = compiler_command(output)
    command[-1:-1] = ["--hidden-import", "_dsh_probe_native"]
    seconds = _run(output, command, Path(owned_root).absolute(), WORKER_BUILD_RESERVE, budget_bytes)
    bundle = output / "dist" / WORKER_NAME
    executable = bundle / (WORKER_NAME + ".exe")
    expected = dict(metadata["source_files"])
    expected["_dsh_probe_native.pyd"] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    if {name.removeprefix("source/"): value for name, value in _snapshot(output / "source").items()} != expected:
        raise BuildError("source_changed")
    verify_native_inputs(Path(probe_bootloader), Path(probe_native_extension))
    finalize_owned_headless_runtime(bundle, executable, Path(probe_bootloader))
    modules = inspect_archive(executable)
    if "pet.official_features" not in modules:
        raise BuildError("worker_owner_registry_missing")
    from scripts.validate_screen_worker_startup import validate_worker_startup

    validate_worker_startup(executable, output / "evidence/normal-startup")
    _artifact(bundle, output, modules, seconds)
    _budget(Path(owned_root).absolute(), 0, budget_bytes)
    return executable


PROBE_BUILD_RESERVE = 384 * 1024**2
PROBE_SOURCES = (
    "pet/__init__.py",
    "pet/frozen_runtime_paths.py",
    "pet/official_features.py",
    "pet/feature_package_probe.py",
    "pet/feature_ports.py",
    "pet/api_ports.py",
    "pet/feature_probe_crypto.py",
    "pet/plugins/__init__.py",
    "pet/plugins/package_trust.py",
    "pet/plugins/feature_packages.py",
    "pet/plugins/feature_host.py",
    "pet/plugins/capabilities.py",
    "pet/plugins/contributions.py",
    "pet/plugins/ports.py",
    "pet/plugins/config.py",
    "pet/plugins/events.py",
    "pet/plugins/manifest.py",
    "pet/plugins/runtime.py",
    "pet/plugins/builtin/__init__.py",
    "pet/plugins/builtin/festival_reminder/__init__.py",
    "pet/festival_service.py",
    "pet/festival.py",
    "pet/festival_calendar.py",
    "pet/festival_data.py",
    "pet/festival_quotes_cn.py",
    "pet/festival_quotes_west.py",
    "pet/voice_chime.py",
    "pet/voice_chime_quotes.py",
    "scripts/feature_probe_entry.py",
    "scripts/feature_probe_canary.py",
)
PROBE_EXCLUDES = (
    "PySide6",
    "PySide2",
    "PyQt5",
    "PyQt6",
    "shiboken6",
    "numpy",
    "PIL",
    "keyring",
    "ctypes",
    "_ctypes",
    "cryptography",
    "nacl",
    "_cffi_backend",
    "cffi",
    "pet.app",
    "pet.config",
    "pet.feature_install_state",
    "pet.feature_version_lease",
    "pet.feature_package_transactions",
    *materials.CORE_EXCLUDES,
)


def verify_native_inputs(bootloader: Path, native_extension: Path):
    """Require the pinned native build receipt, then audit both real PE inputs."""
    import pefile

    from scripts.build_feature_probe_native import PYINSTALLER_VERSION, SOURCE_SHA256

    try:
        bootloader, native_extension = bootloader.absolute(), native_extension.absolute()
        if bootloader.name != "probe-run.exe" or native_extension.name != "_dsh_probe_native.pyd" or bootloader.parent != native_extension.parent:
            raise BuildError("native_provenance_required")
        receipt = json.loads(io.read_bytes(bootloader.parent / "native-build.json", 64 * 1024))
        if receipt.get("pyinstaller") != PYINSTALLER_VERSION or receipt.get("source_sha256") != SOURCE_SHA256 or receipt.get("schema") != 1:
            raise BuildError("native_provenance_required")
        for path in (bootloader, native_extension):
            if receipt.get("files", {}).get(path.name) != materials._digest(path, 16 * 1024**2)[0]:
                raise BuildError("native_provenance_required")
            with pefile.PE(str(path)) as pe:
                imports = {entry.dll.decode().casefold() for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", ())}
                if pe.FILE_HEADER.Machine != 0x8664 or imports & {"user32.dll", "gdi32.dll", "comctl32.dll", "ole32.dll"}:
                    raise BuildError("native_gui_dependency_forbidden")
                if path == bootloader and (pe.OPTIONAL_HEADER.Subsystem != 2 or not pe.OPTIONAL_HEADER.DATA_DIRECTORY[5].Size):
                    raise BuildError("native_loader_contract")
                if path == native_extension:
                    exports = {entry.name for entry in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", ())}
                    if b"PyInit__dsh_probe_native" not in exports:
                        raise BuildError("native_leaf_contract")
    except BuildError:
        raise
    except (io.StateError, OSError, ValueError, TypeError, KeyError, pefile.PEFormatError):
        raise BuildError("native_provenance_required") from None


def prepare_probe(repository_root: Path, output: Path, *, owned_root: Path, native_extension: Path, budget_bytes: int = MAX_GENERATED_BYTES) -> dict:
    """Bounded headless helper source; never borrows the repository at runtime."""
    try:
        root, output, owned = _target(repository_root, output, owned_root, budget_bytes)
        table = {}
        for name in PROBE_SOURCES:
            path = root / name
            digest, size = materials._digest(path, VerificationLimits().max_file_bytes)
            table[name] = (path, digest, size, materials._identity(path.stat()))
        native_extension = Path(native_extension).absolute()
        if native_extension.name != "_dsh_probe_native.pyd":
            raise BuildError("invalid_native_leaf")
        digest, size = materials._digest(native_extension, 16 * 1024**2)
        table[native_extension.name] = (native_extension, digest, size, materials._identity(native_extension.stat()))
        _budget(owned, sum(item[2] for item in table.values()) + 16 * 1024, budget_bytes)
        output.mkdir(parents=True, exist_ok=False)
        _copy(table, output / "source")
        _write(output / "source/scripts/__init__.py", b'"""Only trusted headless probe entry and generated canary."""\n')
        metadata = dict(schema=1, scope="production-probe-source", source_files=_snapshot(output / "source"), excluded_modules=list(PROBE_EXCLUDES))
        _write(output / "evidence/build-input.json", _encoded(metadata))
        return metadata
    except BuildError:
        raise
    except (io.StateError, OSError, ValueError, TypeError):
        raise BuildError("invalid_build_material") from None


def build_probe(
    repository_root: Path,
    output: Path,
    *,
    owned_root: Path,
    probe_bootloader: Path,
    native_extension: Path,
    crypto_library: Path,
    budget_bytes: int = MAX_GENERATED_BYTES,
) -> tuple[Path, str]:
    from scripts.build_feature_probe import finalize_owned_headless_runtime, headless_crypto_arguments, stage_headless_crypto

    verify_native_inputs(Path(probe_bootloader), Path(native_extension))
    metadata = prepare_probe(repository_root, output, owned_root=owned_root, native_extension=native_extension, budget_bytes=budget_bytes)
    output = Path(output).absolute()
    source = output / "source"
    if _snapshot(source) != metadata["source_files"]:
        raise BuildError("source_changed")
    io.safe_path(Path(crypto_library))
    crypto = stage_headless_crypto(Path(crypto_library), output / "crypto-source")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--console",
        "--noupx",
        "--name",
        "dsh-feature-probe",
        "--distpath",
        str(output / "dist"),
        "--workpath",
        str(output / "build"),
        "--specpath",
        str(output / "spec"),
        "--paths",
        str(source),
        "--paths",
        str(crypto),
    ]
    for name in PROBE_EXCLUDES:
        command.extend(("--exclude-module", name))
    command.extend(headless_crypto_arguments())
    command.extend(("--add-binary", str(crypto / "libsodium.dll") + ";.", str(source / "scripts/feature_probe_entry.py")))
    seconds = _run(output, command, Path(owned_root).absolute(), PROBE_BUILD_RESERVE, budget_bytes)
    bundle = output / "dist/dsh-feature-probe"
    exe = bundle / "dsh-feature-probe.exe"
    if _snapshot(source) != metadata["source_files"]:
        raise BuildError("source_changed")
    verify_native_inputs(Path(probe_bootloader), Path(native_extension))
    finalize_owned_headless_runtime(bundle, exe, Path(probe_bootloader))
    for path in crypto.glob("LICENSE-*.txt"):
        _write(bundle / "LIBSODIUM-LICENSES" / path.name, io.read_bytes(path, 64 * 1024))
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    pyz = [name for name, item in archive.toc.items() if item[-1] == "z"]
    if len(pyz) != 1:
        raise BuildError("invalid_probe_archive")
    modules = set(archive.open_embedded_archive(pyz[0]).toc) | set(native_module_inventory(bundle / "_internal"))
    if any(name == banned or name.startswith(banned + ".") for name in modules for banned in PROBE_EXCLUDES):
        raise BuildError("forbidden_probe_module")
    required = {
        "pet.official_features",
        "pet.plugins.package_trust",
        "pet.feature_probe_crypto",
        "pet.feature_package_probe",
        "pet.feature_ports",
        "pet.api_ports",
        "_dsh_probe_native",
    }
    if not required <= modules:
        raise BuildError("required_probe_module_missing")
    table = _table(bundle, "bundle")
    raw = _encoded(dict(schema=1, entry="dsh-feature-probe.exe", files={name.removeprefix("bundle/"): item[1] for name, item in table.items()}))
    _write(bundle / "bundle.json", raw)
    digest = hashlib.sha256(raw).hexdigest()
    TrustedProbeBundle(bundle, digest).verify()
    _artifact(bundle, output, modules, seconds)
    _budget(Path(owned_root).absolute(), 0, budget_bytes)
    return bundle, digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    core = commands.add_parser("core")
    core.add_argument("--version", required=True)
    core.add_argument("--public-policy", type=Path, required=True)
    core.add_argument("--approved-key", action="append", required=True, metavar="KEY_ID=SHA256")
    core.add_argument("--probe-bundle", type=Path, required=True)
    core.add_argument("--probe-digest", required=True)
    worker = commands.add_parser("worker")
    probe = commands.add_parser("probe")
    probe.add_argument("--crypto-library", type=Path, required=True)
    for command in (worker, probe):
        command.add_argument("--probe-bootloader", type=Path, required=True)
        command.add_argument("--native-extension", type=Path, required=True)
    for command in (core, worker, probe):
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--owned-root", type=Path, required=True)
        command.add_argument("--budget-bytes", type=int, default=MAX_GENERATED_BYTES)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    common = dict(owned_root=args.owned_root, budget_bytes=args.budget_bytes)
    result: Path | tuple[Path, str]
    try:
        if args.command == "core":
            approved = {}
            for text in args.approved_key:
                key, separator, fingerprint = text.partition("=")
                if not separator or key in approved or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                    raise BuildError("invalid_approved_identity")
                approved[key] = fingerprint
            result = build_core(
                root,
                args.output,
                **common,
                version=args.version,
                public_policy=args.public_policy,
                approved_fingerprints=approved,
                probe_bundle=args.probe_bundle,
                probe_manifest_sha256=args.probe_digest,
            )
        elif args.command == "worker":
            result = build_worker(root, args.output, **common, probe_bootloader=args.probe_bootloader, probe_native_extension=args.native_extension)
        else:
            result = build_probe(
                root, args.output, **common, probe_bootloader=args.probe_bootloader, native_extension=args.native_extension, crypto_library=args.crypto_library
            )
        print(json.dumps({"status": "built_unsigned", "result": str(result)}, ensure_ascii=True))
        return 0
    except (BuildError, OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"status": "failed", "reason": "production_build_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
