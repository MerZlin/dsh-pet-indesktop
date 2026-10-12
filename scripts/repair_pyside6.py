"""Rebuild affected Qt-for-Python runtimes before tests/packaging (Linux 6.12.0).

Uses the active Python and official Qt sources/SDK. No Qt modules are loaded in
this process and no application-time reference-count monkey patch is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "f50facb83fc3dc5f3240c854668c7a6dd93871b2"  # Qt v6.12.0
SOURCE = "https://github.com/pyside/pyside-setup.git"
PATCH = ROOT / "packaging/patches/qt-for-python-6.12-singleton-refs.patch"
PROBE = """
import gc, json, sys, warnings
from PySide6.QtCore import QCoreApplication, QTimer, QObject, Signal
warnings.simplefilter('ignore')
app = QCoreApplication([]); timer = QTimer()
class Emitter(QObject): event = Signal()
emitter = Emitter()
def callback(): pass
def disconnect():
    emitter.event.connect(callback)
    emitter.event.disconnect(callback)
gc.collect(); gc.disable(); deltas = []
for method, value in ((timer.stop, None), (lambda: timer.setInterval(100), None),
                      (lambda: timer.start(100), None), (emitter.event.emit, True),
                      (disconnect, True), (lambda: emitter.event.disconnect(callback), False)):
    before = sys.getrefcount(value)
    for _ in range(100): method()
    deltas.append(sys.getrefcount(value) - before)
print(json.dumps(deltas), flush=True)
"""


def probe():
    result = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=30)
    values = json.loads(result.stdout)
    if result.returncode and min(values) >= -5:
        raise RuntimeError(f"Qt ownership probe crashed unexpectedly: {result.stderr}")
    return values  # A negative result may also abort during Python finalization.


def run(*args, env=None, cwd=None):
    subprocess.run([str(arg) for arg in args], check=True, env=env, cwd=cwd)


def export_source(source: Path, work: Path):
    archive = work / "qt-for-python-6.12.0-pet-source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("sources/shiboken6", "sources/pyside6", "build_scripts", "LICENSES"):
            tar.add(source / name, arcname=name, filter=lambda info: None if "__pycache__" in Path(info.name).parts else info)
        tar.add(PATCH, arcname="desktop-pet/packaging/patches/" + PATCH.name)
        tar.add(Path(__file__), arcname="desktop-pet/scripts/repair_pyside6.py")
    return archive


def replace_library(source: Path, target: Path):
    fd, name = tempfile.mkstemp(prefix=".qt-runtime-", dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(source, name)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def repair(work: Path):
    if sys.platform != "linux" or importlib.metadata.version("shiboken6") != "6.12.0":
        print("Qt runtime repair: not applicable")
        return
    sites = {name: Path(importlib.metadata.distribution(name).locate_file(name)) for name in ("shiboken6", "PySide6")}
    for site in sites.values():
        if not site.resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise RuntimeError("Use the Python environment that owns these Qt packages")
    before = probe()
    print(f"Qt singleton ownership probe: {before}", flush=True)
    if min(before) >= -5:
        print("Qt runtime ownership is already correct")
        return
    if platform.machine() != "x86_64":
        raise RuntimeError("This repair recipe currently targets Linux x86_64")
    marker = work / ".desktop-pet-shiboken-repair"
    if work.exists() and any(work.iterdir()) and not marker.exists():
        raise RuntimeError("Repair work directory is not owned by this tool; choose an empty directory")
    work.mkdir(parents=True, exist_ok=True)
    marker.touch()
    source = work / "source"
    if not (source / ".git").is_dir():
        run("git", "init", source)
        run("git", "-C", source, "remote", "add", "origin", SOURCE)
    run("git", "-C", source, "fetch", "--depth", "1", "origin", REVISION)
    run("git", "-C", source, "checkout", "--force", "--detach", REVISION)
    run("git", "-C", source, "apply", "--check", PATCH)
    run("git", "-C", source, "apply", PATCH)
    sdk = work / "qt" / "6.12.0/gcc_64"
    if not (sdk / "lib/cmake/Qt6Core/Qt6CoreConfig.cmake").exists():
        run(sys.executable, "-m", "aqt", "install-qt", "linux", "desktop", "6.12.0", "linux_gcc_64", "--archives", "qtbase", "-O", work / "qt", cwd=work)
    common = [
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTS=OFF",
        "-DPET_RUNTIME_ONLY=ON",
        "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON",
        f"-DPython_EXECUTABLE={sys.executable}",
    ]
    # Both CMake configure (qmake) and build (rcc) execute SDK tools needing ICU.
    build_env = {**os.environ, "LD_LIBRARY_PATH": str(sites["PySide6"] / "Qt/lib")}
    shiboken_build = work / "shiboken-build"
    run("cmake", "-S", source / "sources/shiboken6", "-B", shiboken_build, *common, "-DFORCE_LIMITED_API=ON", f"-DCMAKE_PREFIX_PATH={sdk}", env=build_env)
    run("cmake", "--build", shiboken_build, "--target", "libshiboken", "--parallel", "4", env=build_env)
    pyside_build = work / "pyside-build"
    run(
        "cmake",
        "-S",
        source / "sources/pyside6",
        "-B",
        pyside_build,
        *common,
        "-DSTANDALONE=ON",
        "-DMODULES=Core",
        f"-DCMAKE_PREFIX_PATH={sdk};{sites['shiboken6'] / 'lib/cmake'}",
        f"-DCMAKE_CXX_FLAGS=-I{source / 'sources/shiboken6/libshiboken'} -I{shiboken_build / 'libshiboken'}",
        env=build_env,
    )
    run("cmake", "--build", pyside_build, "--target", "pyside6", "--parallel", "4", env=build_env)
    libraries = [
        (shiboken_build / "libshiboken/libshiboken6.abi3.so.6.12", sites["shiboken6"] / "libshiboken6.abi3.so.6.12"),
        (pyside_build / "libpyside/libpyside6.abi3.so.6.12", sites["PySide6"] / "libpyside6.abi3.so.6.12"),
    ]
    backups = []
    try:
        for built, target in libraries:
            backup = work / (target.name + ".original")
            shutil.copy2(target, backup)
            backups.append((backup, target))
            replace_library(built, target)
        after = probe()
        if min(after) < -5:
            raise RuntimeError(f"Rebuilt Qt runtime still consumes singleton references: {after}")
    except BaseException:
        for backup, target in backups:
            replace_library(backup, target)
        raise
    archive = export_source(source, work)
    shutil.copy2(archive, sites["shiboken6"] / archive.name)
    (sites["shiboken6"] / "PET-REPAIR.txt").write_text(
        f"Qt-for-Python 6.12.0 stable-ABI ownership repair\nSource: {SOURCE}\nRevision: {REVISION}\n"
        f"Patch SHA256: {hashlib.sha256(PATCH.read_bytes()).hexdigest()}\n"
        "License: LGPL-3.0-only (see LICENSES in the accompanying patched source archive).\n"
        "Rebuild instructions: scripts/repair_pyside6.py in the desktop-pet source release.\n"
        "Qt SDK: python -m aqt install-qt linux desktop 6.12.0 linux_gcc_64 --archives qtbase -O qt\n"
        "Build sources/shiboken6 target libshiboken and sources/pyside6 target pyside6 with "
        "PET_RUNTIME_ONLY=ON and the active Python; the repair script gives exact CMake options.\n"
        "Replace _internal/shiboken6/libshiboken6.abi3.so.6.12 and "
        "_internal/PySide6/libpyside6.abi3.so.6.12 in the packaged application.\n",
        encoding="utf-8",
    )
    print(f"Qt runtime repaired: {before} -> {after}; source retained in {sites['shiboken6'] / archive.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build/shiboken-repair")
    args = parser.parse_args()
    repair(args.work_dir.resolve())
