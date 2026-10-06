"""Build the self-contained screen Worker validation artifact, not a default release.

Uses a new explicit output directory and a closed source snapshot. There is no
cleanup, repo variant-file rewrite, package installation, signing or user-process
termination. Build failures keep their logs; reruns need a new output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, cast

ROOT = Path(__file__).resolve().parents[1]
WORKER_NAME = "proactive-screen-worker"
WORKER_SOURCES = (
    "features/__init__.py",
    "features/screen_understanding/__init__.py",
    "features/screen_understanding/common/__init__.py",
    "features/screen_understanding/common/hashing.py",
    "features/screen_understanding/common/models.py",
    "features/screen_understanding/worker/__init__.py",
    "features/screen_understanding/worker/runtime.py",
    "features/screen_understanding/worker/vision.py",
    "pet/__init__.py",
    "pet/desktop_query.py",
    "pet/http_compat.py",
    "pet/workers/__init__.py",
    "pet/workers/protocol.py",
    "pet/workers/screen_entry.py",
    "pet/frozen_runtime_paths.py",
    "pet/workers/lease_bootstrap.py",
    "pet/feature_state_io.py",
    "pet/feature_install_state.py",
    "pet/feature_startup_contract.py",
    "pet/feature_version_lease.py",
)
EXCLUDED_MODULES = (
    "PySide6",
    "PySide2",
    "PyQt6",
    "PyQt5",
    "shiboken6",
    "tkinter",
    "multiprocessing",
    "keyring",
    "pet.chat",
    "pet.app",
    "pet.window",
    "pet.proactive",
    "pet.vision",
    "pet.config",
    "pet.workers.agent_link_worker",
    "features.screen_understanding.host",
)
REQUIRED_MODULES = (
    "pet.workers.screen_entry",
    "pet.workers.lease_bootstrap",
    "pet.feature_version_lease",
    "pet.feature_startup_contract",
    "features.screen_understanding.worker.runtime",
    "features.screen_understanding.worker.vision",
    "features.screen_understanding.common.models",
    "features.screen_understanding.common.hashing",
    "pet.desktop_query",
    "pet.http_compat",
    "pet.workers.protocol",
    "PIL.Image",
    "PIL.ImageGrab",
    "certifi",
)
ENTRY_SOURCE = """from pet.workers.screen_entry import run_screen_worker_entry

if __name__ == "__main__":
    raise SystemExit(run_screen_worker_entry())
"""
SYNTHETIC_ENTRY_SOURCE = """from pet.workers.screen_entry import run_screen_worker_entry

def run_synthetic():
    from validation_screen_worker import install_synthetic_boundary
    install_synthetic_boundary()
    from features.screen_understanding.worker.runtime import run_proactive_screen_worker
    return run_proactive_screen_worker()

if __name__ == "__main__":
    raise SystemExit(run_screen_worker_entry(run_runtime=run_synthetic))
"""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare_build(root: Path, output: Path, *, synthetic: bool = False) -> dict:
    """Capture actual inputs before creating output; never follow source symlinks."""
    root = root.resolve(strict=True)
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"build output already exists: {output}")
    snapshots = {}
    for relative in WORKER_SOURCES:
        source = root / relative
        if source.is_symlink() or not source.resolve(strict=True).is_relative_to(root):
            raise ValueError(f"source leaves repository: {relative}")
        snapshots[relative] = source.read_bytes()
    entry = ENTRY_SOURCE.encode("utf-8")
    if synthetic:
        name = "packaging/phase4a_synthetic_worker.py"
        path = root / name
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("synthetic validation entry leaves source root")
        snapshots[name] = path.read_bytes()
        snapshots["validation_screen_worker.py"] = snapshots[name]
        entry = SYNTHETIC_ENTRY_SOURCE.encode("utf-8")
    output.mkdir(parents=True, exist_ok=False)
    (output / "evidence").mkdir()
    for relative, data in snapshots.items():
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / "source/worker_entry.py").write_bytes(entry)
    manifest = {
        "scope": "standalone-screen-worker-validation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "sources": {name: _digest(data) for name, data in snapshots.items()},
        "entry_sha256": _digest(entry),
        "synthetic_boundary": synthetic,
        "tools": {name: importlib.metadata.version(name) for name in ("pyinstaller", "pyinstaller-hooks-contrib", "pillow", "certifi")},
        "excluded_modules": list(EXCLUDED_MODULES),
    }
    _write_json(output / "evidence/build-input.json", manifest)
    return manifest


def build_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = {name: value for name, value in source.items() if not name.upper().startswith(("PYTHON", "_PYI", "_MEIPASS"))}
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return environment


def compiler_command(output: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--console",
        "--noupx",
        "--name",
        WORKER_NAME,
        "--distpath",
        str(output / "dist"),
        "--workpath",
        str(output / "build"),
        "--specpath",
        str(output / "spec"),
        "--collect-data",
        "certifi",
    ]
    for name in EXCLUDED_MODULES:
        command.extend(("--exclude-module", name))
    command.append(str(output / "source/worker_entry.py"))
    return command


def check_module_inventory(modules: Iterable[str]) -> list[str]:
    names = set(modules)
    forbidden = sorted(name for name in names if any(name == banned or name.startswith(banned + ".") for banned in EXCLUDED_MODULES))
    if forbidden:
        raise ValueError(f"forbidden Worker modules: {forbidden}")
    missing = sorted(set(REQUIRED_MODULES) - names)
    if missing:
        raise ValueError(f"missing Worker modules: {missing}")
    return sorted(names)


def inspect_archive(executable: Path) -> list[str]:
    """Inspect the actual executable's PYZ, not an inferred import list."""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    names = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
    if len(names) != 1:
        raise ValueError("expected exactly one embedded PYZ")
    return check_module_inventory(archive.open_embedded_archive(names[0]).toc)


def build_worker(
    root: Path, output: Path, *, synthetic: bool = False, probe_bootloader: Path | None = None, probe_native_extension: Path | None = None
) -> Path:
    if (probe_bootloader is None) != (probe_native_extension is None):
        raise ValueError("headless probe build requires both trusted native inputs")
    if probe_bootloader is not None:
        import pefile

        pe = pefile.PE(str(probe_bootloader))
        imports = {item.dll.decode().casefold() for item in pe.DIRECTORY_ENTRY_IMPORT}
        if pe.OPTIONAL_HEADER.Subsystem != 2 or imports & {"user32.dll", "gdi32.dll", "ole32.dll"} or not pe.OPTIONAL_HEADER.DATA_DIRECTORY[5].Size:
            raise ValueError("headless Worker bootloader contract")
        pe.close()
    output = output.absolute()
    prepare_build(root, output, synthetic=synthetic)
    command = compiler_command(output)
    if probe_native_extension is not None:
        assert probe_bootloader is not None  # Paired inputs were validated above.
        if probe_native_extension.name != "_dsh_probe_native.pyd" or probe_native_extension.is_symlink():
            raise ValueError("headless Worker native leaf contract")
        native = output / "source/_dsh_probe_native.pyd"
        native.write_bytes(probe_native_extension.read_bytes())
        command[-1:-1] = ["--hidden-import", "_dsh_probe_native"]
        _write_json(
            output / "evidence/headless-input.json",
            {"bootloader_sha256": _digest(probe_bootloader.read_bytes()), "native_leaf_sha256": _digest(native.read_bytes())},
        )
    _write_json(output / "evidence/build-command.json", {"arguments": command, "cwd": str(output / "source")})
    started = time.perf_counter()
    with (output / "evidence/pyinstaller.log").open("wb") as log:
        result = subprocess.run(command, cwd=output / "source", env=build_environment(os.environ), stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"PyInstaller failed ({result.returncode}); see {output / 'evidence/pyinstaller.log'}")
    bundle = output / "dist" / WORKER_NAME
    executable = bundle / (WORKER_NAME + (".exe" if os.name == "nt" else ""))
    if probe_bootloader is not None:
        if __package__:
            from .build_feature_probe import finalize_owned_headless_runtime
        else:
            from build_feature_probe import finalize_owned_headless_runtime
        finalize_owned_headless_runtime(bundle, executable, probe_bootloader)
    modules = inspect_archive(executable)
    files = {}
    for file in sorted(bundle.rglob("*")):
        if file.is_symlink():
            raise ValueError(f"validation bundle contains a link: {file}")
        if file.is_file():
            files[file.relative_to(bundle).as_posix()] = {"size": file.stat().st_size, "sha256": _digest(file.read_bytes())}
    _write_json(
        output / "evidence/artifact.json",
        {
            "build_seconds": round(time.perf_counter() - started, 3),
            "executable": str(executable),
            "files": files,
            "size_bytes": sum(cast(int, item["size"]) for item in files.values()),
            "pyz_modules": modules,
        },
    )
    print(f"SCREEN_WORKER_BUILD_OK {executable}")
    return executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new validation build directory; must not exist")
    parser.add_argument("--synthetic", action="store_true", help="VALIDATION ONLY: fixed image/foreground, loopback HTTP only")
    parser.add_argument("--probe-bootloader", type=Path, help="Core-owned headless onedir bootloader")
    parser.add_argument("--probe-native-extension", type=Path, help="Core-owned native isolation verification leaf")
    args = parser.parse_args(argv)
    try:
        build_worker(ROOT, args.output, synthetic=args.synthetic, probe_bootloader=args.probe_bootloader, probe_native_extension=args.probe_native_extension)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"SCREEN_WORKER_BUILD_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
