#!/usr/bin/env python3
"""Build isolated candidates on Ubuntu 22.04 x86_64 or macOS >=14.4 arm64.

Never deletes/reuses an existing output and never publishes a release.
Run with the pinned build venv: python scripts/build_petcore_posix.py --output …
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "dsh-pet-core"
QT_VERSIONS = {"linux": "6.11.3", "darwin": "6.12.0"}


def version(value):
    return tuple(int(n) for n in value.split(".") if n.isdigit())


def host_problems(system, machine, qt_version, release):
    problems = []
    if system not in QT_VERSIONS:
        return ["Build on Linux or macOS, not a cross compiler."]
    if qt_version != QT_VERSIONS[system]:
        problems.append(f"PySide6/Qt must equal {QT_VERSIONS[system]} (got {qt_version})")
    if system == "linux" and (machine != "x86_64" or release != "22.04"):
        problems.append("Linux candidates require Ubuntu 22.04 x86_64 (glibc <=2.35)")
    if system == "darwin" and (machine != "arm64" or version(release) < (14, 4)):
        problems.append("macOS candidates require native arm64 and macOS >=14.4")
    return problems


def elf_problems(header: bytes, versions: str):
    problems = []
    if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 62:
        problems.append("ELF must be little-endian x86_64")
    required = [tuple(map(int, item.split("."))) for item in re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", versions)]
    if required and max(required) > (2, 35):
        problems.append(f"glibc requirement {max(required)} exceeds Ubuntu 22.04 / 2.35")
    if "GLIBC_ABI_DT_RELR" in versions:
        problems.append("DT_RELR requires glibc >=2.36")
    return problems


def prepare_output(output):
    output = Path(output).resolve()
    if output == ROOT or output in ROOT.parents or (output.exists() and any(output.iterdir())):
        raise ValueError("Choose a fresh, empty output directory outside existing build outputs")
    output.mkdir(parents=True, exist_ok=True)
    return output


def run(*argv, **kwargs):
    print("+", " ".join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), check=True, **kwargs)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_snapshot(root, inputs):
    """Identify actual build inputs, including exported or dirty source trees."""
    root = Path(root).resolve()
    paths = set()
    for name in inputs:
        path = root / name
        if not path.exists():
            raise FileNotFoundError(path)
        paths.update(item for item in (path.rglob("*") if path.is_dir() else (path,))
                     if item.is_file() and "__pycache__" not in item.parts)
    files = [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
              "sha256": file_sha256(path)}
             for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix())]
    encoded = json.dumps(files, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sha, dirty = None, None
    # Do not accidentally attribute an exported tree to an enclosing repository.
    if (root / ".git").exists():
        sha = run("git", "rev-parse", "HEAD", cwd=root, capture_output=True, text=True).stdout.strip()
        dirty = bool(run("git", "status", "--porcelain", cwd=root, capture_output=True, text=True).stdout)
    return {"sha": sha, "dirty": dirty, "source_sha256": hashlib.sha256(encoded).hexdigest(), "files": files}


def audit_native(bundle, system):
    records, problems = [], []
    seen = set()
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        with path.open("rb") as stream:
            header = stream.read(64)
        name = str(path.relative_to(bundle))
        if header[:2] == b"MZ":
            problems.append(f"{name}: Windows binary in POSIX artifact")
        if system == "linux" and header[:4] == b"\x7fELF":
            versions = run("readelf", "--version-info", path, capture_output=True, text=True).stdout
            errors = elf_problems(header, versions)
            dependencies = subprocess.run(["ldd", str(path)], capture_output=True, text=True)
            if "not found" in dependencies.stdout + dependencies.stderr:
                errors.append("unresolved dependencies: " + dependencies.stdout)
            records.append({"file": name, "dependencies": dependencies.stdout, "errors": errors})
            problems.extend(f"{name}: {error}" for error in errors)
        elif system == "darwin" and header[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            arch = run("lipo", "-archs", path, capture_output=True, text=True).stdout.strip()
            load = run("otool", "-l", path, capture_output=True, text=True).stdout
            minimums = re.findall(r"(?:minos|version)\s+(\d+\.\d+(?:\.\d+)?)", "\n".join(
                block for block in load.split("Load command")
                if "LC_BUILD_VERSION" in block or "LC_VERSION_MIN_MACOSX" in block))
            deps = run("otool", "-L", path, capture_output=True, text=True).stdout
            errors = []
            if "arm64" not in arch.split():
                errors.append(f"no arm64 slice: {arch}")
            if any(version(value)[:2] > (14, 4) for value in minimums):
                errors.append(f"minimum macOS exceeds 14.4: {minimums}")
            for dep in deps.splitlines()[1:]:
                value = dep.strip().split(" (", 1)[0]
                if value.startswith("/") and not value.startswith(("/System/Library/", "/usr/lib/")):
                    errors.append(f"external dependency: {value}")
            records.append({"file": name, "arch": arch, "minimums": minimums, "dependencies": deps, "errors": errors})
            problems.extend(f"{name}: {error}" for error in errors)
    return {"binaries": records, "problems": problems}


def build(output, fcitx_build_dir=None):
    import PySide6
    from PySide6.QtCore import QLibraryInfo, qVersion

    sys.path.insert(0, str(ROOT))
    from petcore.platform.bundle import DATA_DIRS, DATA_FILES
    release = platform.mac_ver()[0] if sys.platform == "darwin" else platform.freedesktop_os_release().get("VERSION_ID", "")
    problems = host_problems(sys.platform, platform.machine(), PySide6.__version__, release)
    if sys.platform == "linux" and platform.freedesktop_os_release().get("ID") != "ubuntu":
        problems.append("Linux build distribution must be Ubuntu")
    if qVersion() != PySide6.__version__:
        problems.append("PySide6 and Qt versions differ")
    if problems:
        raise RuntimeError("; ".join(problems))
    inputs = (*DATA_DIRS, *DATA_FILES, "petcore", "requirements.txt",
              "packaging/petcore_entry.py", "packaging/petcore-posix-constraints.txt",
              "packaging/install_petcore_linux.py", "scripts/build_petcore_posix.py",
              "scripts/build_petcore_fcitx.sh",
              *(str(path.relative_to(ROOT)) for path in (ROOT / "pet").rglob("*.py")))
    source = source_snapshot(ROOT, inputs)
    output = prepare_output(output)
    (output / "source-files.json").write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chdir(ROOT)
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET="14.4")
    args = [sys.executable, "-m", "PyInstaller", "--onedir", "--name", NAME,
            "--distpath", str(output), "--workpath", str(output / ".work"),
            "--specpath", str(output), "--paths", str(ROOT)]
    for resource in (*DATA_DIRS, *DATA_FILES):
        destination = resource if resource in DATA_DIRS else "."
        args += ["--add-data", f"{ROOT / resource}:{destination}"]
    for module in ("petcore", "edge_tts", "keyring", "lunar_python", "rapidocr"):
        args += ["--collect-all", module]
    for module in ("pet.webm_clip", "pet.library", "pet.frameseq_clip", "pet.frameseq_provision",
                   "PIL", "psutil", "PySide6.QtTextToSpeech", "onnxruntime", "cv2", "numpy", "omegaconf"):
        args += ["--hidden-import", module]
    for module in ("edge_tts", "keyring", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
        args += ["--copy-metadata", module]
    for module in ("winrt", "win32ctypes", "pycaw", "comtypes"):
        args += ["--exclude-module", module]
    if sys.platform == "linux":
        plugin = output / ".fcitx/libfcitx5platforminputcontextplugin.so"
        run("bash", ROOT / "scripts/build_petcore_fcitx.sh", sys.executable, plugin,
            *([fcitx_build_dir.resolve()] if fcitx_build_dir else []))
        args += ["--add-binary", f"{plugin}:PySide6/Qt/plugins/platforminputcontexts"]
        args += ["--add-data", f"{plugin.parent / 'licenses'}:licenses"]
        environment["LD_LIBRARY_PATH"] = QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath)
    else:
        args += ["--windowed", "--target-architecture", "arm64", "--codesign-identity", "-",
                 "--osx-bundle-identifier", "io.dsh.petcore", "--icon", str(ROOT / "assets/icon.ico")]
        # QtWebView has no PyInstaller Qt hook. Its native Cocoa plugin is explicit.
        args += ["--hidden-import", "PySide6.QtWebView"]
        libraries = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))
        plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
        framework = libraries / "QtWebView.framework"
        if framework.exists():
            args += ["--add-data", f"{framework}:PySide6/Qt/lib/QtWebView.framework"]
        for plugin in (plugins / "webview").glob("*.dylib"):
            args += ["--add-binary", f"{plugin}:PySide6/Qt/plugins/webview"]
    run(*args, ROOT / "packaging/petcore_entry.py", env=environment)
    bundle = output / (NAME + ".app" if sys.platform == "darwin" else NAME)
    if sys.platform == "darwin":
        info = bundle / "Contents/Info.plist"
        data = plistlib.loads(info.read_bytes())
        data.update(LSUIElement=True, LSMinimumSystemVersion="14.4", NSHighResolutionCapable=True)
        info.write_bytes(plistlib.dumps(data))
        run("codesign", "--force", "--deep", "--sign", "-", bundle)
        run("codesign", "--verify", "--deep", "--strict", "--verbose=2", bundle)
        executable = bundle / "Contents/MacOS" / NAME
    else:
        executable = bundle / NAME
        (bundle / "_internal/gio-modules").mkdir(exist_ok=True)
        shutil.copy2(ROOT / "packaging/install_petcore_linux.py", bundle / "install.py")
    if sys.platform == "linux":
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(map(str, (bundle / "_internal", bundle / "_internal/PySide6/Qt/lib")))
    audit = audit_native(bundle, sys.platform)
    (output / "native-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    if audit["problems"]:
        raise RuntimeError("Native artifact audit failed; see native-audit.json")
    check = output / "check.json"
    run(executable, "--root", output / ".smoke-data", "--check", "--strict", "--out", check,
        env=dict(environment, QT_QPA_PLATFORM="offscreen"))
    report = json.loads(check.read_text(encoding="utf-8"))
    if report["strict"]["problems"]:
        raise RuntimeError("Frozen strict check failed")
    if source_snapshot(ROOT, inputs)["source_sha256"] != source["source_sha256"]:
        raise RuntimeError("Source inputs changed during the build; use a fresh snapshot")
    if sys.platform == "linux":
        artifact = output / f"{NAME}-linux-x86_64.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            archive.add(bundle, arcname=NAME)
    else:
        artifact = output / f"{NAME}-macos-arm64.app.zip"
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, artifact)
    digest = file_sha256(artifact)
    evidence = {"sha": source["sha"], "dirty": source["dirty"],
                "source_sha256": source["source_sha256"], "source_manifest": "source-files.json",
                "platform": platform.platform(), "python": sys.version, "qt": qVersion(),
                "artifact": artifact.name, "sha256": digest}
    (output / "build.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fcitx-build-dir", type=Path,
                        help="Optional Qt/Fcitx compilation cache; pinned clean sources are rechecked and rebuilt")
    options = parser.parse_args()
    build(options.output.resolve(), options.fcitx_build_dir)
