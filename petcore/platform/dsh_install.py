"""把随包 DSH 插件安装到 ``~/.dsh``（仅用户显式点击时执行）。

只做三件事：拷插件、改 ``profiles/desktop/package.json`` 的 dependencies/bundles、
在 profile 的 ``node_modules`` 建目录联接。``cordis.patch.yml`` 由插件自己的
``dsh.bundle.patch`` 提供，不手改 profile 的 cordis 配置。

所有写操作先备份 ``package.json``；失败原因用稳定字符串返回，不抛到 UI 外。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PLUGINS: tuple[tuple[str, str], ...] = (
    ("dsh-pet-bridge", "@dsh-pet/bridge"),
    ("dsh-pet-channel", "dsh-pet-channel"),
)
PLUGIN_NAMES = tuple(directory for directory, _package in PLUGINS)


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    reason: str = ""
    profile: str = ""
    plugins: tuple[str, ...] = ()
    backup: str = ""


def plugin_root() -> Path | None:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", ".")) / "integrations"
    else:
        base = Path(__file__).resolve().parents[2] / "integrations"
    return base if base.is_dir() else None


def profile_dir(home: Path | None = None) -> Path:
    root = Path(home) if home is not None else Path.home()
    return root / ".dsh" / "profiles" / "desktop"


def status(home: Path | None = None) -> dict:
    profile = profile_dir(home)
    package = profile / "package.json"
    result = {
        "profile": str(profile),
        "profile_exists": package.is_file(),
        "installed": False,
        "bundles": [],
        "plugins": [],
    }
    if not package.is_file():
        return result
    try:
        doc = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(doc, dict):
        return result
    bundles = (doc.get("dsh") or {}).get("profile", {}).get("bundles")
    if isinstance(bundles, list):
        result["bundles"] = [str(item) for item in bundles]
    deps = doc.get("dependencies")
    if isinstance(deps, dict):
        result["plugins"] = [
            package for _directory, package in PLUGINS if package in deps
        ]
    result["installed"] = all(
        package in result["bundles"] for _directory, package in PLUGINS
    )
    return result


def _write_package(path: Path, doc: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def _link_plugin(link: Path, target: Path, runner) -> bool:
    if link.exists():
        return True
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            completed = runner(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if int(getattr(completed, "returncode", 1) or 0) == 0:
                return True
        except Exception:  # noqa: BLE001 - 退回 symlink
            pass
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except OSError:
        return False


def install(
    home: Path | None = None,
    *,
    plugin_root_dir: Path | None = None,
    runner=None,
) -> InstallResult:
    root = plugin_root_dir or plugin_root()
    if root is None:
        return InstallResult(False, "plugins_missing")
    profile = profile_dir(home)
    package = profile / "package.json"
    if not package.is_file():
        return InstallResult(False, "no_profile", str(profile))
    try:
        doc = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return InstallResult(False, "bad_profile", str(profile))
    if not isinstance(doc, dict):
        return InstallResult(False, "bad_profile", str(profile))
    local_root = (Path(home) if home is not None else Path.home()) / ".dsh" / "local-plugins"
    try:
        local_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return InstallResult(False, "write_failed", str(profile))
    copied: list[str] = []
    for directory, _package in PLUGINS:
        source = root / directory
        if not source.is_dir():
            return InstallResult(False, f"missing_{directory}", str(profile))
        target = local_root / directory
        try:
            shutil.copytree(
                source,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "node_modules", "*.pyc"),
            )
        except OSError:
            return InstallResult(False, f"copy_{directory}", str(profile))
        copied.append(directory)
    backup = package.with_name(f"package.json.dsh-pet-backup-{int(time.time())}")
    try:
        shutil.copy2(package, backup)
    except OSError:
        return InstallResult(False, "backup_failed", str(profile))
    dependencies = doc.get("dependencies")
    if not isinstance(dependencies, dict):
        dependencies = {}
        doc["dependencies"] = dependencies
    bundles = ((doc.get("dsh") or {}).get("profile") or {}).get("bundles")
    if not isinstance(bundles, list):
        dsh = doc.setdefault("dsh", {})
        profile_doc = dsh.setdefault("profile", {})
        bundles = []
        profile_doc["bundles"] = bundles
    for directory, package_name in PLUGINS:
        target = local_root / directory
        dependencies[package_name] = f"file:{target.as_posix()}"
        if package_name not in bundles:
            bundles.append(package_name)
    try:
        _write_package(package, doc)
    except OSError:
        return InstallResult(False, "write_failed", str(profile))
    linked: list[str] = []
    run = runner or subprocess.run
    for directory, package_name in PLUGINS:
        if _link_plugin(
            profile / "node_modules" / package_name, local_root / directory, run
        ):
            linked.append(directory)
    if len(linked) != len(PLUGINS):
        # 目录联接失败时把 profile 恢复到安装前；否则 DSH 下次启动会找
        # 一个 node_modules 里并不存在的包。本地插件目录保留（可手动清理）。
        try:
            shutil.copy2(backup, package)
        except OSError:
            pass
        return InstallResult(
            False, "link_failed", str(profile), tuple(copied), str(backup)
        )
    return InstallResult(True, "", str(profile), tuple(copied), str(backup))
