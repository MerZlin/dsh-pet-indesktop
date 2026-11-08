"""Resource manifest shared by POSIX builds and the frozen --check gate."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import sys
from pathlib import Path

DATA_DIRS = (
    "assets", "integrations", "petcore/menu_templates", "petcore/community/data",
    "petcore/builtin", "resources/rapidocr_models",
)
DATA_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")
REQUIRED_FILES = DATA_FILES + (
    "assets/icon.ico",
    "integrations/dsh-pet-bridge/index.js",
    "integrations/dsh-pet-bridge/channel.js",
    "integrations/dsh-pet-bridge/package.json",
    "integrations/dsh-pet-bridge/cordis.patch.yml",
    "integrations/hermes-pet-bridge/__init__.py",
    "integrations/hermes-pet-bridge/plugin.yaml",
    "petcore/builtin/features/fun.self-talk/daily_lines.json",
)


def resource_problems(base: Path) -> list[str]:
    missing = [f"missing resource: {name}" for name in REQUIRED_FILES
               if not (base / name).is_file() or not (base / name).stat().st_size]
    for directory, pattern in (("assets/sounds", "*.wav"), ("resources/rapidocr_models", "*.onnx")):
        if not any((base / directory).rglob(pattern)):
            missing.append(f"missing resource: {directory}/{pattern}")
    return missing


def fcitx_problems(plugin: Path, metadata: dict, qt_version: str, provenance: dict) -> list[str]:
    """Qt metadata encodes major/minor only; provenance verifies the exact build."""
    problems = []
    major, minor, _patch = map(int, qt_version.split("."))
    if metadata.get("version") != (major << 16 | minor << 8):
        problems.append("Fcitx Qt major/minor ABI mismatch")
    if "fcitx" not in metadata.get("MetaData", {}).get("Keys", []):
        problems.append("Fcitx input-context key missing")
    if not isinstance(provenance, dict):
        provenance = {}
    if (provenance.get("schema") != 1 or provenance.get("qt_version") != qt_version
            or provenance.get("qt_source_tag") != f"v{qt_version}"
            or provenance.get("fcitx_source_tag") != "5.1.14"):
        problems.append("Fcitx exact Qt/source provenance mismatch or missing")
    for key in ("qt_source_commit", "fcitx_source_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get(key, ""))):
            problems.append(f"Fcitx invalid source identity: {key}")
    try:
        with plugin.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != provenance.get("plugin_sha256"):
            problems.append("Fcitx plugin digest mismatch")
    except OSError:
        problems.append("Fcitx plugin missing or unreadable")
    return problems


def report(base: Path) -> dict:
    modules = {}
    for name in ("keyring", "edge_tts", "imageio_ffmpeg", "rapidocr", "onnxruntime"):
        try:
            modules[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            modules[name] = False
    problems = resource_problems(base)
    plugins = {}
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        for name in ("licenses/fcitx5-qt/BSD-3-Clause.txt", "licenses/fcitx5-qt-5.1.14-source.tar.gz",
                     "licenses/qtbase/LGPL-3.0-only.txt"):
            if not (base / name).is_file():
                problems.append(f"missing license/source: {name}")
        from PySide6.QtCore import QLibraryInfo, QPluginLoader, qVersion
        directory = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
        plugin = directory / "platforminputcontexts/libfcitx5platforminputcontextplugin.so"
        loader = QPluginLoader(str(plugin))
        metadata = loader.metaData()
        try:
            provenance = json.loads((base / "licenses/fcitx5-qt-build.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            provenance = {}
        errors = fcitx_problems(plugin, metadata, qVersion(), provenance)
        matched = not errors
        loaded = loader.load() if matched else False
        plugins["fcitx"] = {"qt": qVersion(), "build_matches": matched, "loaded": loaded,
                            "problems": errors, "provenance": provenance}
        problems.extend(errors)
        if not matched or not loaded:
            problems.append("Fcitx Qt ABI/load check failed: " + loader.errorString())
    return {"resource_problems": problems, "modules": modules, "plugins": plugins}
