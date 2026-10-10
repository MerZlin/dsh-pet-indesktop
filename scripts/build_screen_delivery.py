"""Explicit, isolated Phase 4A validation builds; never changes default releases.

Build a Core plus a user-trusted local package fixture. Phase5A local
activation deliberately has no publisher key or signature dependency; the
package still carries a bounded manifest/file inventory for compatibility and
corruption detection. Outputs must be new directories; failures preserve
 evidence for inspection.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Mapping, cast

ROOT = Path(__file__).resolve().parents[1]
SCREEN_MODULES = (
    "features",
    "pet.screen_understanding",
    "pet.vision",
    "pet.proactive",
    "pet.proactive_limiter",
    "pet.proactive_memory",
    "pet.workers.proactive_screen_adapter",
    "pet.workers.proactive_screen_worker",
)
HEAVY_MODULES = (
    "OpenGL",
    "torch",
    "transformers",
    "datasets",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langsmith",
    "langgraph",
    "wandb",
    "sentry_sdk",
    "pandas",
    "numba",
    "llvmlite",
    "pyarrow",
    "polars",
    "_polars_runtime_32",
    "spacy",
    "cv2",
    "playwright",
    "narwhals",
    "sympy",
    "fsspec",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "matplotlib",
    "matplotlib_inline",
    "seaborn",
    "IPython",
    "ipykernel",
    "jupyter_client",
    "jupyter_core",
    "nbformat",
    "zmq",
)
CORE_REQUIRED = {
    "pet.app",
    "pet.window",
    "pet.desktop_query",
    "pet.feature_host_bindings",
    "pet.plugins.package_binding",
    "pet.plugins.feature_packages",
    "pet.credentials",
    "keyring",
    "cryptography",
    "PySide6.QtWidgets",
}
DATA_ROOTS = ("assets/characters", "content", "assets/big_blue_fat_fish", "assets/sounds", "pet/persona_presets", "pet/menu_templates", "integrations")
COLLECT = ("imageio_ffmpeg", "certifi", "PySide6.QtMultimedia", "edge_tts", "aiofiles", "tzdata", "psutil", "keyring")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


IMAGE_SUBSYSTEM_WINDOWS_GUI = 2


def read_pe_subsystem(executable: Path) -> int:
    """Read the PE OptionalHeader subsystem without executing the artifact."""
    path = Path(executable).resolve(strict=True)
    raw = path.read_bytes()
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise ValueError(f"invalid PE DOS header: {path}")
    pe_offset = int.from_bytes(raw[0x3C:0x40], "little")
    signature_end = pe_offset + 4
    if pe_offset < 0x40 or signature_end > len(raw) or raw[pe_offset:signature_end] != b"PE\x00\x00":
        raise ValueError(f"invalid PE signature: {path}")
    coff_start = signature_end
    if coff_start + 20 > len(raw):
        raise ValueError(f"truncated PE COFF header: {path}")
    optional_size = int.from_bytes(raw[coff_start + 16 : coff_start + 18], "little")
    optional_start = coff_start + 20
    subsystem_offset = optional_start + 68
    if optional_size < 70 or subsystem_offset + 2 > len(raw):
        raise ValueError(f"truncated PE optional header: {path}")
    magic = int.from_bytes(raw[optional_start : optional_start + 2], "little")
    if magic not in {0x10B, 0x20B}:
        raise ValueError(f"unsupported PE optional header: {path}")
    return int.from_bytes(raw[subsystem_offset : subsystem_offset + 2], "little")


def require_gui_pe_subsystem(executable: Path) -> None:
    subsystem = read_pe_subsystem(executable)
    if subsystem != IMAGE_SUBSYSTEM_WINDOWS_GUI:
        raise ValueError(f"Core PE subsystem must be GUI (2), got {subsystem}")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def core_excludes(*, chat: bool) -> tuple[str, ...]:
    return SCREEN_MODULES + HEAVY_MODULES + (() if chat else ("pet.chat",))


def _banned(name: str, banned: Iterable[str]) -> bool:
    return any(name == item or name.startswith(item + ".") for item in banned)


def native_module_inventory(internal: Path) -> list[str]:
    """Native extensions are on disk, not in the pure-Python PYZ archive."""
    names = set()
    for path in internal.rglob("*"):
        if path.is_file() and path.suffix in {".pyd", ".so"}:
            relative = path.relative_to(internal)
            stem = relative.name.split(".", 1)[0]
            names.add(".".join((*relative.parts[:-1], stem)))
    return sorted(names)


def core_module_inventory(modules: Iterable[str], *, chat: bool, native_modules: Iterable[str] = ()) -> list[str]:
    pure = set(modules)
    names = pure | set(native_modules)
    screen = sorted(n for n in names if _banned(n, SCREEN_MODULES))
    if screen:
        raise ValueError(f"screen implementation in Core PYZ: {screen}")
    if not chat and any(_banned(n, ("pet.chat",)) for n in names):
        raise ValueError("chat implementation in no-chat Core")
    missing = CORE_REQUIRED - names
    if missing:
        raise ValueError(f"missing required Core modules: {sorted(missing)}")
    return sorted(pure)


def host_dependencies(root: Path) -> list[str]:
    """Find Core-side imports hidden from PyInstaller by versioned host loading."""
    result = set(CORE_REQUIRED)
    for area in ("host", "common"):
        for source in (root / "features/screen_understanding" / area).rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    result.update(alias.name for alias in node.names if alias.name.startswith("pet."))
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    if node.module.startswith("pet."):
                        result.add(node.module)
                    elif node.module == "pet":
                        result.update("pet." + alias.name for alias in node.names if (root / "pet" / (alias.name + ".py")).exists())
    forbidden = [name for name in result if _banned(name, SCREEN_MODULES + ("pet.chat",))]
    if forbidden:
        raise ValueError(f"feature host still directly imports feature/chat implementation: {forbidden}")
    return sorted(result)


def sanitized_build_environment(
    source: Mapping[str, str], *, path_separator: str = os.pathsep, trusted_directories: Iterable[Path] = ()
) -> tuple[dict[str, str], list[str]]:
    result = {k: v for k, v in source.items() if not k.upper().startswith(("PYTHON", "_PYI", "_MEIPASS", "QT_", "QML"))}
    trusted = {p.resolve() for p in trusted_directories}
    removed, retained = [], []
    path_key = next((k for k in result if k.upper() == "PATH"), "PATH")
    for text in result.get(path_key, "").split(path_separator):
        if not text:
            continue
        directory = Path(text.strip('"'))
        untrusted_icu = directory.resolve() not in trusted and directory.is_dir() and any(directory.glob("icu*.dll"))
        known_conflict = any(word in text.lower() for word in ("anaconda", "miniconda", "miktex"))
        if untrusted_icu or known_conflict:
            removed.append(text)
        else:
            retained.append(text)
    result[path_key] = path_separator.join(retained)
    result.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return result, removed


def _source_bytes(root: Path, path: Path) -> bytes:
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"input leaves source root: {path}")
    return path.read_bytes()


def prepare_core(
    root: Path,
    output: Path,
    *,
    chat: bool,
    public_key: str | None = None,
    entrypoint: Path | None = None,
    probe_bundle: Path | None = None,
    probe_manifest_sha256: str | None = None,
) -> dict:
    root, output = root.resolve(strict=True), output.absolute()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    management = entrypoint is not None
    manual = False
    if entrypoint is not None:
        if probe_bundle is None or probe_manifest_sha256 is None:
            raise ValueError("production management validation requires trusted probe")
        selected_entry = entrypoint.resolve(strict=True)
        allowed_entries = (root / "packaging/phase4b_validation_entry.py", root / "packaging/phase4b_manual_entry.py")
        if selected_entry not in allowed_entries:
            raise ValueError("unexpected management entry")
        manual = selected_entry == allowed_entries[1]
        from pet.feature_probe_windows import TrustedProbeBundle

        TrustedProbeBundle(probe_bundle, probe_manifest_sha256).verify()
    elif probe_bundle is not None or probe_manifest_sha256 is not None:
        raise ValueError("probe requires production management entry")
    snapshots = {"assets/icon.ico": _source_bytes(root, root / "assets/icon.ico")}
    for path in (root / "pet").rglob("*.py"):
        relative = path.relative_to(root)
        module = ".".join(relative.with_suffix("").parts)
        if "__pycache__" not in path.parts and not _banned(module, core_excludes(chat=chat)):
            snapshots[relative.as_posix()] = _source_bytes(root, path)
    snapshots["validation_entry.py"] = _source_bytes(root, entrypoint if entrypoint is not None else root / "packaging/phase4a_validation_entry.py")
    if management:
        policy = _source_bytes(root, root / "pet/feature_build_policy.py").decode("utf-8")
        # This replaces only the closed validation source tree, never repository
        # policy, installed Core, environment trust or a candidate's input.
        if public_key is not None:
            # Compatibility only for historical signed-fixture tests. No
            # Phase5A builder supplies this argument anymore.
            anchor_name = "manual-acceptance-only" if manual else "validation-only"
            policy += f"\nOFFICIAL_FEATURE_TRUST_ANCHORS = (({anchor_name!r}, {public_key!r}),)\nALLOW_LOCAL_PACKAGE_ACTIVATION = False\n"
        else:
            policy += "\nOFFICIAL_FEATURE_TRUST_ANCHORS = ()\nOFFICIAL_FEATURE_KEY_POLICIES = ()\nALLOW_LOCAL_PACKAGE_ACTIVATION = True\n"
        policy += f"PROBE_BUNDLE_MANIFEST_SHA256 = {probe_manifest_sha256!r}\nVALIDATION_BUILD = True\n"
        if manual:
            policy += "MANUAL_ACCEPTANCE_BUILD = True\n"
        snapshots["pet/feature_build_policy.py"] = policy.encode("utf-8")
        if not manual:
            snapshots["validation_boundaries.py"] = _source_bytes(root, root / "packaging/phase4b_validation_boundaries.py")
    snapshots["pet/feature_distribution.py"] = b'"""Independent validation variant; not installed state."""\nBUILTIN_AI = False\nBUILTIN_SCREEN = False\n'
    variant = "core-webm" if manual else ("webm-chat" if chat else "webm")
    snapshots["build_variant.py"] = (f"VARIANT = {variant!r}\n").encode()
    snapshots["validation_config.py"] = (
        f"VALIDATION_ONLY = True\nMANUAL_ACCEPTANCE_ONLY = {manual!r}\nENABLE_CHAT = {chat!r}\nLOCAL_PACKAGE_ACTIVATION = {public_key is None!r}\n"
        + (f"TEST_PUBLIC_KEY = {public_key!r}\n" if public_key is not None else "")
    ).encode()
    hidden = host_dependencies(root)
    output.mkdir(parents=True, exist_ok=False)
    (output / "evidence").mkdir()
    for relative_name, data in snapshots.items():
        path = output / "source" / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "scope": "no-screen-core-manual-acceptance" if manual else "no-screen-core-validation",
        "manual_acceptance_only": manual,
        "production_management": management,
        "probe_manifest_sha256": probe_manifest_sha256,
        "chat": chat,
        "python": sys.version,
        "sources": {n: digest(d) for n, d in snapshots.items()},
        "hidden_imports": hidden,
        "excluded_modules": list(core_excludes(chat=chat)),
        "tools": {n: importlib.metadata.version(n) for n in ("PyInstaller", "PySide6", "cryptography", "keyring")},
    }
    write_json(output / "evidence/build-input.json", manifest)
    return manifest


def _verify_production_worker_inputs(root: Path, build: Path) -> Path:
    """Verify the closed production-worker preparation emitted by build_feature_release."""
    inputs = json.loads((build / "evidence/build-input.json").read_text(encoding="utf-8"))
    if inputs.get("scope") != "production-worker-source":
        raise ValueError("not a production Worker source build")
    if bool(inputs.get("synthetic_boundary", False)):
        raise ValueError("production Worker cannot use a synthetic boundary")
    if __package__:
        from .build_feature_release import WORKER_ENTRY
        from .build_screen_worker import WORKER_SOURCES
    else:
        from build_feature_release import WORKER_ENTRY
        from build_screen_worker import WORKER_SOURCES
    expected_sources = set(WORKER_SOURCES) | {"pet/official_features.py", "worker_entry.py"}
    source_files = inputs.get("source_files")
    if not isinstance(source_files, dict) or set(source_files) != expected_sources:
        raise ValueError("production Worker source inventory differs from closed build inputs")
    source_root = build / "source"
    for relative, entry in source_files.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("size"), int):
            raise ValueError("production Worker source evidence is invalid")
        path = source_root / relative
        if relative == "worker_entry.py":
            data = WORKER_ENTRY.encode("utf-8")
        else:
            data = _source_bytes(root.resolve(), root / relative)
        if path.read_bytes() != data or len(data) != entry["size"] or digest(data) != entry["sha256"]:
            raise ValueError(f"production Worker input changed; rebuild first: {relative}")
    return _verify_worker_artifact(build, require_native=True)


def _verify_worker_artifact(build: Path, *, require_native: bool) -> Path:
    artifact = json.loads((build / "evidence/artifact.json").read_text(encoding="utf-8"))
    bundle = build / "dist/proactive-screen-worker"
    if not bundle.is_dir():
        raise ValueError("Worker frozen bundle is missing")
    if require_native:
        # A real Windows probe must carry the Core-owned native isolation leaf.
        # The Worker must fail closed when this leaf is absent; accepting an
        # ordinary PyInstaller build here only defers that failure to install.
        evidence_path = build / "evidence/headless-input.json"
        if not evidence_path.is_file():
            raise ValueError("non-synthetic Worker missing headless probe evidence")
        headless = json.loads(evidence_path.read_text(encoding="utf-8"))
        native_name = "_internal/_dsh_probe_native.pyd"
        native_path = bundle / native_name
        if not native_path.is_file():
            raise ValueError("non-synthetic Worker missing native isolation leaf")
        native_digest = headless.get("native_leaf_sha256")
        if not isinstance(native_digest, str) or len(native_digest) != 64:
            raise ValueError("non-synthetic Worker native leaf evidence is invalid")
        if digest(native_path.read_bytes()) != native_digest:
            raise ValueError("non-synthetic Worker native isolation leaf changed")
        artifact_native = artifact.get("files", {}).get(native_name)
        if not isinstance(artifact_native, dict) or artifact_native.get("sha256") != native_digest:
            raise ValueError("non-synthetic Worker artifact omits native isolation leaf evidence")
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    if actual != set(artifact["files"]):
        raise ValueError("Worker artifact inventory changed")
    for relative, entry in artifact["files"].items():
        data = _source_bytes(bundle.resolve(), bundle / relative)
        if len(data) != entry["size"] or digest(data) != entry["sha256"]:
            raise ValueError(f"Worker artifact changed: {relative}")
    return bundle


def verify_worker_inputs(root: Path, build: Path, *, synthetic: bool = False) -> Path:
    inputs = json.loads((build / "evidence/build-input.json").read_text(encoding="utf-8"))
    if inputs.get("scope") == "production-worker-source":
        if synthetic:
            raise ValueError("Worker validation boundary does not match selected mode")
        return _verify_production_worker_inputs(root, build)
    if inputs.get("scope") != "standalone-screen-worker-validation":
        raise ValueError("not an independent Worker build")
    if bool(inputs.get("synthetic_boundary", False)) != synthetic:
        raise ValueError("Worker validation boundary does not match selected mode")
    if __package__:
        from .build_screen_worker import ENTRY_SOURCE, SYNTHETIC_ENTRY_SOURCE, WORKER_SOURCES
    else:
        from build_screen_worker import ENTRY_SOURCE, SYNTHETIC_ENTRY_SOURCE, WORKER_SOURCES

    expected_sources = set(WORKER_SOURCES)
    if synthetic:
        expected_sources.update(("packaging/phase4a_synthetic_worker.py", "validation_screen_worker.py"))
    if set(inputs["sources"]) != expected_sources:
        raise ValueError("Worker source inventory differs from closed build inputs")
    expected_entry = (SYNTHETIC_ENTRY_SOURCE if synthetic else ENTRY_SOURCE).encode("utf-8")
    if inputs.get("entry_sha256") != digest(expected_entry):
        raise ValueError("Worker entry changed; rebuild first")
    for relative, checksum in inputs["sources"].items():
        origin = "packaging/phase4a_synthetic_worker.py" if synthetic and relative == "validation_screen_worker.py" else relative
        if digest(_source_bytes(root.resolve(), root / origin)) != checksum:
            raise ValueError(f"Worker input changed; rebuild first: {relative}")
    return _verify_worker_artifact(build, require_native=not synthetic)


def assemble_package(root: Path, target: Path, worker_bundle: Path, legacy_key=None, *, synthetic: bool = False) -> Path:
    """Create a local package; ``legacy_key`` is test-only compatibility.

    New Phase5A callers leave it unset. An explicitly supplied ephemeral key is
    retained solely for historical signed-fixture tests and is never generated
    by a delivery/manual build.
    """
    if target.exists():
        raise FileExistsError(f"package output exists: {target}")
    target.mkdir(parents=True)
    for area in ("host", "common"):
        origin = root / "features/screen_understanding" / area
        for path in origin.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            dest = target / area / path.relative_to(origin)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_source_bytes(root.resolve(), path))
    for source in sorted(worker_bundle.rglob("*")):
        if source.is_file():
            dest = target / "worker" / source.relative_to(worker_bundle)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_source_bytes(worker_bundle.resolve(), source))
    if synthetic:
        marker = target / "resources/VALIDATION-SYNTHETIC.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("VALIDATION ONLY: fixed image and loopback HTTP; not a distributable feature.\n", encoding="utf-8")
    files = {p.relative_to(target).as_posix(): {"sha256": digest(p.read_bytes()), "size": p.stat().st_size} for p in sorted(target.rglob("*")) if p.is_file()}
    manifest = dict(
        id="official.screen-understanding",
        version="1.0.3",
        api_version="1",
        core_requires=">=4.2.4,<6.0.0",
        platforms=[sys.platform],
        capabilities=["screen.capture"],
        factory="screen-understanding/v1",
        worker={"path": "worker/proactive-screen-worker" + (".exe" if os.name == "nt" else ""), "args": []},
        files=files,
    )
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (target / "manifest.json").write_bytes(raw)
    if legacy_key is not None:
        (target / "manifest.sig").write_bytes(legacy_key.sign(raw))
    return target


def assemble_ai_package(root: Path, target: Path, *, version: str = "1.0.3") -> Path:
    """Create the ordinary, unsigned local AI package used by Setup/manual acceptance.

    ``key_id`` is a non-cryptographic manifest discriminator required by the
    existing v2 host-only schema.  It does not identify a public key and is
    ignored by local activation; no ``manifest.sig`` is emitted.
    """
    if target.exists():
        raise FileExistsError(f"package output exists: {target}")
    origin = (root / "features/ai_chat/host").resolve(strict=True)
    target.mkdir(parents=True)
    for path in origin.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        destination = target / "host" / path.relative_to(origin)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_source_bytes(root.resolve(strict=True), path))
    files = {
        path.relative_to(target).as_posix(): {"sha256": digest(path.read_bytes()), "size": path.stat().st_size}
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "format_version": 2,
        "execution_kind": "host-only",
        "key_id": "local-user",
        "id": "official.ai-chat",
        "version": version,
        "api_version": "1",
        "core_requires": ">=4.2.3,<6.0.0",
        "platforms": [sys.platform],
        "capabilities": [
            "network.http",
            "settings.contribute",
            "menu.contribute",
            "chat.contribute",
            "files.user-selected.read",
        ],
        "factory": "ai-chat/v1",
        "worker": None,
        "files": files,
    }
    (target / "manifest.json").write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return target


def build_core(
    root: Path,
    output: Path,
    *,
    chat: bool,
    public_key: str | None = None,
    entrypoint: Path | None = None,
    probe_bundle: Path | None = None,
    probe_manifest_sha256: str | None = None,
) -> Path:
    manifest = prepare_core(
        root, output, chat=chat, public_key=public_key, entrypoint=entrypoint, probe_bundle=probe_bundle, probe_manifest_sha256=probe_manifest_sha256
    )
    source = output / "source"
    datas = [(str(source / "assets/icon.ico"), "assets")]
    if probe_bundle is not None:
        assert probe_manifest_sha256 is not None  # prepare_core validated the pair.
        from pet.feature_probe_windows import TrustedProbeBundle

        TrustedProbeBundle(probe_bundle, probe_manifest_sha256).verify()
        destination = source / "feature-probe"
        destination.mkdir()
        for file in sorted(probe_bundle.rglob("*")):
            if file.is_file():
                data = _source_bytes(probe_bundle.resolve(), file)
                path = destination / file.relative_to(probe_bundle)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                manifest.setdefault("resources", {})[path.relative_to(source).as_posix()] = {"size": len(data), "sha256": digest(data)}
        TrustedProbeBundle(destination, probe_manifest_sha256).verify()
        datas.append((str(destination), "feature-probe"))
    for name in DATA_ROOTS + (("assets/chat",) if chat else ()):
        origin = root / name
        if not origin.is_dir():
            continue
        destination = source / name
        # Record the resources too; no caches, symlinks or runtime content roots.
        for file in origin.rglob("*"):
            if not file.is_file() or "__pycache__" in file.parts or ".git" in file.parts:
                continue
            data = _source_bytes(root, file)
            path = destination / file.relative_to(origin)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest.setdefault("resources", {})[path.relative_to(source).as_posix()] = {"size": len(data), "sha256": digest(data)}
        datas.append((str(destination), name))
    if chat:
        for file in (root / "pet/chat").glob("*.qss"):
            destination = source / "pet/chat" / file.name
            shutil.copyfile(file, destination)
            datas.append((str(destination), "pet/chat"))
            manifest.setdefault("resources", {})["pet/chat/" + file.name] = {"size": file.stat().st_size, "sha256": digest(file.read_bytes())}
    write_json(output / "evidence/build-input.json", manifest)
    name = "dsh-pet-core-webm" if manifest["manual_acceptance_only"] else ("core-webm-chat-no-screen" if chat else "core-webm-no-chat-no-screen")
    spec = output / "validation.spec"
    spec.write_text(
        "from PyInstaller.utils.hooks import collect_all\n"
        + f"datas={datas!r}\nbinaries=[]\nhiddenimports={manifest['hidden_imports']!r}\n"
        + f"for module in {COLLECT!r}:\n    d,b,h=collect_all(module)\n    datas+=d; binaries+=b; hiddenimports+=h\n"
        + f"a=Analysis([{str(source / 'validation_entry.py')!r}],pathex=[{str(source)!r}],datas=datas,binaries=binaries,hiddenimports=hiddenimports,excludes={manifest['excluded_modules']!r})\n"
        + "pyz=PYZ(a.pure)\n"
        + f"exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name={name!r},console=False,upx=False,icon={str(source / 'assets/icon.ico')!r})\n"
        + f"coll=COLLECT(exe,a.binaries,a.datas,name={name!r},upx=False)\n",
        encoding="utf-8",
    )
    import PySide6

    trusted = [Path(PySide6.__file__).parent, Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"]
    env, removed = sanitized_build_environment(os.environ, trusted_directories=trusted)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(output / "dist"), "--workpath", str(output / "build"), str(spec)]
    write_json(output / "evidence/build-command.json", {"arguments": command, "cwd": str(source), "excluded_path_directories": removed})
    started = time.perf_counter()
    with (output / "evidence/pyinstaller.log").open("wb") as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"Core build failed ({result.returncode}), see {output / 'evidence/pyinstaller.log'}")
    executable = verify_core_bundle(output, chat=chat, manual=manifest["manual_acceptance_only"], build_seconds=time.perf_counter() - started)
    if probe_bundle is not None:
        assert probe_manifest_sha256 is not None
        TrustedProbeBundle(executable.parent / "_internal/feature-probe", probe_manifest_sha256).verify()
    return executable


def verify_core_bundle(output: Path, *, chat: bool, manual: bool = False, build_seconds: float) -> Path:
    """Audit an actual artifact; rerunnable without changing build inputs."""
    name = "dsh-pet-core-webm" if manual else ("core-webm-chat-no-screen" if chat else "core-webm-no-chat-no-screen")
    bundle = output / "dist" / name
    exe = bundle / (name + (".exe" if os.name == "nt" else ""))
    if os.name == "nt":
        require_gui_pe_subsystem(exe)
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    pyz = [n for n, item in archive.toc.items() if item[-1] == "z"]
    if len(pyz) != 1:
        raise ValueError("expected one embedded PYZ")
    native = native_module_inventory(bundle / "_internal")
    modules = core_module_inventory(archive.open_embedded_archive(pyz[0]).toc, chat=chat, native_modules=native)
    if os.name == "nt" and list((bundle / "_internal").glob("icu*.dll")):
        raise ValueError("external ICU at Core runtime root")
    files = {p.relative_to(bundle).as_posix(): {"size": p.stat().st_size, "sha256": digest(p.read_bytes())} for p in bundle.rglob("*") if p.is_file()}
    write_json(
        output / "evidence/artifact.json",
        {
            "executable": str(exe),
            "build_seconds": build_seconds,
            "pyz_modules": modules,
            "native_modules": native,
            "files": files,
            "size_bytes": sum(cast(int, v["size"]) for v in files.values()),
        },
    )
    print(f"CORE_VALIDATION_BUILD_OK {exe}", flush=True)
    return exe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-build", type=Path, required=True)
    parser.add_argument("--synthetic-worker-build", type=Path, help="optional separate synthetic validation artifact")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        parser.error("output directory must not exist")
    worker = verify_worker_inputs(ROOT, args.worker_build.resolve())
    package = assemble_package(ROOT, output / "official.screen-understanding/1.0.3", worker)
    synthetic_package = None
    if args.synthetic_worker_build:
        fixture = verify_worker_inputs(ROOT, args.synthetic_worker_build.resolve(), synthetic=True)
        synthetic_package = assemble_package(ROOT, output / "synthetic-test-only/official.screen-understanding/1.0.3", fixture, synthetic=True)
    write_json(
        output / "validation-trust.json",
        {
            "validation_only": True,
            "package_activation": "local-structure",
            "signature_required": False,
            "package": str(package),
            "synthetic_package": str(synthetic_package) if synthetic_package else None,
        },
    )
    for chat in (False, True):
        build_core(ROOT, output / ("core-chat" if chat else "core-no-chat"), chat=chat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
