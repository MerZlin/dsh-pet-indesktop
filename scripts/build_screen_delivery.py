"""Explicit, isolated Phase 4A validation builds; never changes default releases.

Test trust is generated in memory, embedded only in these labelled validation
executables, and discarded. No private key, installer or installed-state file.
Outputs must be new directories; failures preserve evidence for inspection.
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
from typing import Iterable, Mapping

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


def prepare_core(root: Path, output: Path, *, chat: bool, public_key: str) -> dict:
    root, output = root.resolve(strict=True), output.absolute()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if len(bytes.fromhex(public_key)) != 32:
        raise ValueError("test public key must be Ed25519")
    snapshots = {}
    for path in (root / "pet").rglob("*.py"):
        relative = path.relative_to(root)
        module = ".".join(relative.with_suffix("").parts)
        if "__pycache__" not in path.parts and not _banned(module, core_excludes(chat=chat)):
            snapshots[relative.as_posix()] = _source_bytes(root, path)
    snapshots["validation_entry.py"] = _source_bytes(root, root / "packaging/phase4a_validation_entry.py")
    snapshots["pet/feature_distribution.py"] = b'"""Independent validation variant; not installed state."""\nBUILTIN_SCREEN = False\n'
    snapshots["build_variant.py"] = (f"VARIANT = {('webm-chat' if chat else 'webm')!r}\n").encode()
    snapshots["validation_config.py"] = (f"VALIDATION_ONLY = True\nENABLE_CHAT = {chat!r}\nTEST_PUBLIC_KEY = {public_key!r}\n").encode()
    hidden = host_dependencies(root)
    output.mkdir(parents=True, exist_ok=False)
    (output / "evidence").mkdir()
    for relative, data in snapshots.items():
        path = output / "source" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "scope": "no-screen-core-validation",
        "chat": chat,
        "python": sys.version,
        "sources": {n: digest(d) for n, d in snapshots.items()},
        "hidden_imports": hidden,
        "excluded_modules": list(core_excludes(chat=chat)),
        "tools": {n: importlib.metadata.version(n) for n in ("PyInstaller", "PySide6", "cryptography", "keyring")},
    }
    write_json(output / "evidence/build-input.json", manifest)
    return manifest


def verify_worker_inputs(root: Path, build: Path, *, synthetic: bool = False) -> Path:
    inputs = json.loads((build / "evidence/build-input.json").read_text(encoding="utf-8"))
    if inputs.get("scope") != "standalone-screen-worker-validation":
        raise ValueError("not an independent Worker build")
    if bool(inputs.get("synthetic_boundary", False)) != synthetic:
        raise ValueError("Worker validation boundary does not match selected mode")
    if __package__:
        from .build_screen_worker import ENTRY_SOURCE, WORKER_SOURCES
    else:
        from build_screen_worker import ENTRY_SOURCE, WORKER_SOURCES

    expected_sources = set(WORKER_SOURCES)
    if synthetic:
        expected_sources.add("packaging/phase4a_synthetic_worker.py")
    if set(inputs["sources"]) != expected_sources:
        raise ValueError("Worker source inventory differs from closed build inputs")
    expected_entry = (root / "packaging/phase4a_synthetic_worker.py").read_bytes() if synthetic else ENTRY_SOURCE.encode("utf-8")
    if inputs.get("entry_sha256") != digest(expected_entry):
        raise ValueError("Worker entry changed; rebuild first")
    for relative, checksum in inputs["sources"].items():
        if digest(_source_bytes(root.resolve(), root / relative)) != checksum:
            raise ValueError(f"Worker input changed; rebuild first: {relative}")
    artifact = json.loads((build / "evidence/artifact.json").read_text(encoding="utf-8"))
    bundle = build / "dist/proactive-screen-worker"
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    if actual != set(artifact["files"]):
        raise ValueError("Worker artifact inventory changed")
    for relative, entry in artifact["files"].items():
        data = _source_bytes(bundle.resolve(), bundle / relative)
        if len(data) != entry["size"] or digest(data) != entry["sha256"]:
            raise ValueError(f"Worker artifact changed: {relative}")
    return bundle


def assemble_package(root: Path, target: Path, worker_bundle: Path, key, *, synthetic: bool = False) -> Path:
    """Single version, signed inventory. The private key is never serialized."""
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
        version="1.0.0",
        api_version="1",
        core_requires=">=4.2.1,<6.0.0",
        platforms=[sys.platform],
        capabilities=["screen.capture"],
        factory="screen-understanding/v1",
        worker={"path": "worker/proactive-screen-worker" + (".exe" if os.name == "nt" else ""), "args": []},
        files=files,
    )
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (target / "manifest.json").write_bytes(raw)
    (target / "manifest.sig").write_bytes(key.sign(raw))
    return target


def build_core(root: Path, output: Path, *, chat: bool, public_key: str) -> Path:
    manifest = prepare_core(root, output, chat=chat, public_key=public_key)
    source = output / "source"
    datas = []
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
    name = "core-webm-chat-no-screen" if chat else "core-webm-no-chat-no-screen"
    spec = output / "validation.spec"
    spec.write_text(
        "from PyInstaller.utils.hooks import collect_all\n"
        + f"datas={datas!r}\nbinaries=[]\nhiddenimports={manifest['hidden_imports']!r}\n"
        + f"for module in {COLLECT!r}:\n    d,b,h=collect_all(module)\n    datas+=d; binaries+=b; hiddenimports+=h\n"
        + f"a=Analysis([{str(source / 'validation_entry.py')!r}],pathex=[{str(source)!r}],datas=datas,binaries=binaries,hiddenimports=hiddenimports,excludes={manifest['excluded_modules']!r})\n"
        + "pyz=PYZ(a.pure)\n"
        + f"exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name={name!r},console=True,upx=False)\n"
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
    return verify_core_bundle(output, chat=chat, build_seconds=time.perf_counter() - started)


def verify_core_bundle(output: Path, *, chat: bool, build_seconds: float) -> Path:
    """Audit an actual artifact; rerunnable without changing build inputs."""
    name = "core-webm-chat-no-screen" if chat else "core-webm-no-chat-no-screen"
    bundle = output / "dist" / name
    exe = bundle / (name + (".exe" if os.name == "nt" else ""))
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
            "size_bytes": sum(v["size"] for v in files.values()),
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
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    package = assemble_package(ROOT, output / "official.screen-understanding/1.0.0", worker, key)
    synthetic_package = None
    if args.synthetic_worker_build:
        fixture = verify_worker_inputs(ROOT, args.synthetic_worker_build.resolve(), synthetic=True)
        synthetic_package = assemble_package(ROOT, output / "synthetic-test-only/official.screen-understanding/1.0.0", fixture, key, synthetic=True)
    del key
    write_json(
        output / "validation-trust.json",
        {"validation_only": True, "public_key": public, "package": str(package), "synthetic_package": str(synthetic_package) if synthetic_package else None},
    )
    for chat in (False, True):
        build_core(ROOT, output / ("core-chat" if chat else "core-no-chat"), chat=chat, public_key=public)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
