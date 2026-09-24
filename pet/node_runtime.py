# -*- coding: utf-8 -*-
"""Resolve Node.js tools consistently for terminal and desktop-app launches.

桌宠是可能由 Finder / Explorer / 开机自启拉起的 GUI 进程，继承到的 PATH 往往
不完整：

- macOS：.app 的环境 PATH 极简（Issue #67）；
- Windows：进程环境块是**登录时缓存**的，用户刚装好的 nvm-windows / pnpm /
  Volta 目录不在里面（issue：桌宠找不到 pnpm → 桥接安装失败）。

因此这里给出统一的「增强 PATH」：把已知的 Node 包管理器目录补齐；Windows 还额外
读注册表里**最新**的 PATH（进程环境块的旧值靠它补全），再交给 ``shutil.which``。

顺序语义：POSIX 保持历史行为（额外目录**前置**，Finder 极简 PATH 场景需要它）；
Windows 是进程 PATH **优先**、额外目录追加在后面——Windows 上用户可能用 nvm 明确
钉了某个 node 版本，前置公共目录会把用户的选择劫持掉。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Iterable, Mapping

# POSIX：常见的包管理器 bin 目录（存在即前置）
_POSIX_ABS_BIN_DIRS = (
    "/opt/homebrew/bin",  # Apple Silicon Homebrew
    "/usr/local/bin",  # Intel Homebrew / 官方 pkg
    "/usr/bin",
    "/home/linuxbrew/.linuxbrew/bin",
)
_POSIX_HOME_BIN_DIRS = (
    ".npm-global/bin",
    ".local/bin",
    ".volta/bin",
    ".bun/bin",
    ".yarn/bin",
    "Library/pnpm",  # macOS pnpm 独立安装
    ".local/share/pnpm",
    ".asdf/shims",
)

# Windows：包管理器默认落点（env 变量名, 相对子路径）
_WINDOWS_ENV_BIN_DIRS = (
    ("ProgramFiles", "nodejs"),
    ("ProgramFiles(x86)", "nodejs"),
    ("APPDATA", "npm"),
    ("LOCALAPPDATA", "pnpm"),
    ("LOCALAPPDATA", "Volta/bin"),
    ("LOCALAPPDATA", "Yarn/bin"),
    ("USERPROFILE", ".bun/bin"),
    ("USERPROFILE", "scoop/shims"),
    ("ProgramData", "scoop/shims"),
    ("ProgramData", "chocolatey/bin"),
)

# POSIX：npm / yarn / bun 等全局包根（静态候选，存在与否由调用方决定；
# `home` 相对项按调用方给的家目录展开，便于用临时家目录覆盖 Linux/macOS 布局）
_POSIX_ABS_NODE_MODULES = (
    "/usr/local/lib/node_modules",
    "/opt/homebrew/lib/node_modules",
    "/usr/lib/node_modules",
    "/home/linuxbrew/.linuxbrew/lib/node_modules",
)
_POSIX_HOME_NODE_MODULES = (
    ".local/lib/node_modules",
    ".npm-global/lib/node_modules",
    ".config/yarn/global/node_modules",
    ".bun/install/global/node_modules",
)

# Windows：同上
_WINDOWS_NODE_MODULES = (
    ("APPDATA", "npm/node_modules"),
    ("LOCALAPPDATA", "pnpm/global/5/node_modules"),
    ("ProgramFiles", "nodejs/node_modules"),
    ("ProgramFiles(x86)", "nodejs/node_modules"),
    ("LOCALAPPDATA", "Yarn/Data/global/node_modules"),
    ("USERPROFILE", ".bun/install/global/node_modules"),
)

_WIN_VAR_PATTERN = re.compile(r"%([^%]+)%")


def _is_windows() -> bool:
    """平台判定（独立成函数，测试可替换）。"""
    return os.name == "nt"


def _home() -> Path:
    """家目录（独立成函数，测试可替换成临时家目录以覆盖 POSIX 布局）。"""
    return Path.home()


def _env_get(env: Mapping[str, str], name: str) -> str:
    """读环境变量：Windows 变量名大小写不敏感，普通 dict 也要按此语义取。"""
    direct = env.get(name)
    if direct is not None:
        return str(direct)
    lowered = name.lower()
    for key, value in env.items():
        if str(key).lower() == lowered:
            return str(value)
    return ""


def _env_root_or_default(env: Mapping[str, str], name: str, fallback: Path) -> Path:
    """环境变量重定向的**替代根**：指向不存在的目录时回退默认布局。

    只用于「替代根」语义的变量（NVM_DIR / FNM_DIR）：设置了就替换默认探测根。
    用户的 NVM_DIR/FNM_DIR 可能指向已移除/失效的目录（升级、换机器、配置残留），
    此时若按变量直接探测会**整个丢掉**默认家目录下真实存在的 nvm/fnm 布局——
    桌面 Linux/macOS 场景即「找不到全局 pnpm/npm → 需要 pnpm，自动安装失败」。
    追加型变量（VOLTA_HOME / BUN_INSTALL / PNPM_HOME）只是额外候选、不替换
    默认，不经过本函数（_existing_dirs 会滤掉不存在的）。
    """
    value = _env_get(env, name).strip()
    if value:
        candidate = Path(value)
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            pass
    return fallback


# ----------------------------------------------------------------------
# PATH 拼接工具
# ----------------------------------------------------------------------
def _expand_windows_vars(value: str, env: Mapping[str, str]) -> str:
    """展开 ``%VAR%``（变量名大小写不敏感，值里可能还嵌变量，有界多轮）。"""
    current = value
    for _ in range(3):
        if "%" not in current:
            break
        lookup = {str(key).lower(): str(val) for key, val in env.items()}

        def _replace(match: re.Match) -> str:
            return lookup.get(match.group(1).lower(), match.group(0))

        expanded = _WIN_VAR_PATTERN.sub(_replace, current)
        if expanded == current:
            break
        current = expanded
    return current


def _split_path_entries(value: str, env: Mapping[str, str], sep: str | None = None) -> list[str]:
    """按分隔符切分 PATH，展开 ``%VAR%``，丢掉空项与首尾空白。"""
    separator = os.pathsep if sep is None else sep
    entries: list[str] = []
    for raw in str(value or "").split(separator):
        entry = _expand_windows_vars(raw, env).strip()
        if entry:
            entries.append(entry)
    return entries


def _dedupe_path_entries(entries: Iterable[str], *, case_insensitive: bool) -> list[str]:
    """去重并保持顺序；Windows 路径大小写不敏感。"""
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        key = text.lower() if case_insensitive else text
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _joined_path(*groups: Iterable[str], case_insensitive: bool) -> str:
    entries: list[str] = []
    for group in groups:
        entries.extend(group)
    return os.pathsep.join(_dedupe_path_entries(entries, case_insensitive=case_insensitive))


def _existing_dirs(candidates: Iterable[Path]) -> list[Path]:
    """只保留真实存在的目录（PATH 里塞不存在的路径只会拖慢 which）。"""
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _safe_glob(parent: Path, pattern: str) -> list[Path]:
    try:
        return sorted(parent.glob(pattern))
    except OSError:
        return []


# ----------------------------------------------------------------------
# 各平台的额外目录
# ----------------------------------------------------------------------
def _nvm_roots(env: Mapping[str, str], home: Path, *, windows: bool) -> list[Path]:
    """nvm 候选根目录：环境变量优先，再补常见默认布局。

    为什么不止一个：GUI 进程（macOS .app / 桌面启动器）拿不到 shell 里设的
    ``NVM_DIR``，只认单个默认根就会漏——reporter 的 nvm 装在 ``~/nvm``（无点号），
    官方默认却是 ``~/.nvm``；nvm-windows 默认在 ``%APPDATA%\\nvm``。
    """
    roots: list[Path] = []
    names = ("NVM_HOME", "NVM_DIR") if windows else ("NVM_DIR",)
    for name in names:
        value = _env_get(env, name).strip()
        if value:
            roots.append(Path(value))
    if windows:
        appdata = _env_get(env, "APPDATA").strip()
        if appdata:
            roots.append(Path(appdata) / "nvm")
    else:
        roots.extend([home / ".nvm", home / "nvm"])
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _version_manager_bin_dirs(env: Mapping[str, str], home: Path, *, windows: bool) -> list[Path]:
    """版本管理器（nvm / nvm-windows / fnm / volta / bun / pnpm）的 bin 目录。"""
    dirs: list[Path] = []

    nvm_symlink = _env_get(env, "NVM_SYMLINK").strip()
    if nvm_symlink:
        dirs.append(Path(nvm_symlink))

    if windows:
        # nvm-windows：%NVM_HOME%\v<版本>\ 里同时有 node.exe 与全局包 shim
        for root in _nvm_roots(env, home, windows=True):
            dirs.append(root)
            dirs.extend(_safe_glob(root, "v*"))
    else:
        for nvm_root in _nvm_roots(env, home, windows=False):
            for version in _safe_glob(nvm_root / "versions" / "node", "*"):
                dirs.append(version / "bin")

    fnm_dir = _env_get(env, "FNM_DIR").strip()
    if windows:
        fnm_root = Path(fnm_dir) if fnm_dir else (home / "AppData" / "Roaming" / "fnm")
    else:
        fnm_root = _env_root_or_default(env, "FNM_DIR", home / ".local" / "share" / "fnm")
    for version in _safe_glob(fnm_root / "node-versions", "*"):
        dirs.append(version / "installation" / "bin")
        if windows:
            dirs.append(version / "installation")

    volta_home = _env_get(env, "VOLTA_HOME").strip()
    if volta_home:
        dirs.append(Path(volta_home) / "bin")

    bun_install = _env_get(env, "BUN_INSTALL").strip()
    if bun_install:
        dirs.append(Path(bun_install) / "bin")

    pnpm_home = _env_get(env, "PNPM_HOME").strip()
    if pnpm_home:
        dirs.append(Path(pnpm_home))
    return dirs


def _windows_extra_bin_dirs(env: Mapping[str, str], home: Path) -> list[Path]:
    """Windows 上应当补进 PATH 且真实存在的目录。"""
    candidates: list[Path] = list(_version_manager_bin_dirs(env, home, windows=True))
    for name, suffix in _WINDOWS_ENV_BIN_DIRS:
        base = _env_get(env, name).strip()
        if base:
            candidates.append(Path(base) / Path(suffix))
    return _existing_dirs(candidates)


def _posix_extra_bin_dirs(env: Mapping[str, str], home: Path) -> list[Path]:
    """POSIX 上应当补进 PATH 且真实存在的目录。"""
    candidates: list[Path] = [Path(directory) for directory in _POSIX_ABS_BIN_DIRS]
    candidates.extend(home / suffix for suffix in _POSIX_HOME_BIN_DIRS)
    candidates.extend(_version_manager_bin_dirs(env, home, windows=False))
    return _existing_dirs(candidates)


# ----------------------------------------------------------------------
# Windows 注册表（进程环境块是登录时的旧值，注册表里才是最新配置）
# ----------------------------------------------------------------------
_WINDOWS_REGISTRY_KEYS = (
    ("HKEY_CURRENT_USER", "Environment"),
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
)

_MANAGER_ENV_NAMES = (
    "Path",
    "NVM_HOME",
    "NVM_SYMLINK",
    "NVM_DIR",
    "PNPM_HOME",
    "VOLTA_HOME",
    "FNM_DIR",
    "BUN_INSTALL",
    "NODE_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramData",
)


def _windows_registry_env(names: Iterable[str] = _MANAGER_ENV_NAMES) -> dict[str, str]:
    """从注册表读最新环境变量（进程环境块可能停留在登录时刻）。"""
    if not _is_windows():
        return {}
    try:
        import winreg
    except Exception:
        return {}
    wanted = {str(name).lower() for name in names}
    values: dict[str, str] = {}
    for hive_name, subkey in _WINDOWS_REGISTRY_KEYS:
        hive = getattr(winreg, hive_name, None)
        if hive is None:
            continue
        try:
            with winreg.OpenKey(hive, subkey) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if str(name).lower() in wanted and str(value).strip():
                        values.setdefault(str(name), _expand_windows_vars(str(value), {**values, **os.environ}))
        except OSError:
            continue
    return values


def _windows_registry_path_entries() -> list[str]:
    """注册表里配置的 PATH 条目（含 ``%VAR%`` 展开）。"""
    env = _windows_registry_env()
    return _split_path_entries(_env_get(env, "Path"), {**env, **os.environ})


# ----------------------------------------------------------------------
# 公开 API
# ----------------------------------------------------------------------
def augmented_path() -> str:
    """Return PATH plus common Node package-manager locations."""
    home = _home()
    process_entries = _split_path_entries(os.environ.get("PATH", ""), os.environ)
    if _is_windows():
        registry_env = _windows_registry_env()
        env = {**registry_env, **os.environ}
        registry_entries = _windows_registry_path_entries()
        manager_entries = [str(path) for path in _windows_extra_bin_dirs(env, home)]
        return _joined_path(
            process_entries,
            _existing_dirs(Path(entry) for entry in registry_entries),
            manager_entries,
            case_insensitive=True,
        )
    extra = [str(path) for path in _posix_extra_bin_dirs(os.environ, home)]
    return _joined_path(extra, process_entries, case_insensitive=False)


def which(name: str) -> str | None:
    """Resolve an executable using the desktop-safe augmented PATH."""
    return shutil.which(name, path=augmented_path())


# ----------------------------------------------------------------------
# 全局 node_modules 根目录（pnpm / npm / dsh 等全局包的实际落点）
# ----------------------------------------------------------------------
def static_node_modules_roots() -> list[Path]:
    """静态候选全局 node_modules 根（不要求存在，调用方自行判定）。"""
    if _is_windows():
        roots: list[Path] = []
        for name, suffix in _WINDOWS_NODE_MODULES:
            base = _env_get(os.environ, name).strip()
            if base:
                roots.append(Path(base) / Path(suffix))
        return roots
    home = _home()
    return [
        *(Path(directory) for directory in _POSIX_ABS_NODE_MODULES),
        *(home / suffix for suffix in _POSIX_HOME_NODE_MODULES),
    ]


def global_node_modules_roots() -> list[Path]:
    """真实存在的全局 node_modules 根（各版本管理器 / 包管理器）。

    覆盖桌面端最容易漏的 nvm / nvm-windows / volta / fnm / pnpm 全局目录：
    ``dsh`` 的 ``@deepseek-ai/dsh/lib/bin.js``、``pnpm`` 的 ``bin/pnpm.mjs``
    都在这些根下面。
    """
    env = os.environ
    home = _home()
    roots: list[Path] = list(static_node_modules_roots())
    if _is_windows():
        for root in _nvm_roots(env, home, windows=True):
            for version in _safe_glob(root, "v*"):
                roots.append(version / "node_modules")
        nvm_symlink = _env_get(env, "NVM_SYMLINK").strip()
        if nvm_symlink:
            roots.append(Path(nvm_symlink) / "node_modules")
        volta_root = _env_root_or_default(env, "VOLTA_HOME", home / ".volta")
        for image in _safe_glob(volta_root / "tools" / "image" / "node", "*"):
            roots.append(image / "node_modules")
        fnm_dir = _env_get(env, "FNM_DIR").strip()
        fnm_root = Path(fnm_dir) if fnm_dir else home / "AppData" / "Roaming" / "fnm"
        for version in _safe_glob(fnm_root / "node-versions", "*"):
            roots.append(version / "installation" / "node_modules")
    else:
        for nvm_root in _nvm_roots(env, home, windows=False):
            for version in _safe_glob(nvm_root / "versions" / "node", "*"):
                roots.append(version / "lib" / "node_modules")
        volta_root = _env_root_or_default(env, "VOLTA_HOME", home / ".volta")
        for image in _safe_glob(volta_root / "tools" / "image" / "node", "*"):
            roots.append(image / "lib" / "node_modules")
        fnm_root = _env_root_or_default(env, "FNM_DIR", home / ".local" / "share" / "fnm")
        for version in _safe_glob(fnm_root / "node-versions", "*"):
            roots.append(version / "installation" / "lib" / "node_modules")
        bun_root = _env_root_or_default(env, "BUN_INSTALL", home / ".bun")
        roots.append(bun_root / "install" / "global" / "node_modules")
        for store in _safe_glob(home / ".local" / "share" / "pnpm" / "global", "*"):
            roots.append(store / "node_modules")
    return _existing_dirs(roots)
