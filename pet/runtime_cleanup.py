# -*- coding: utf-8 -*-
"""安全清理 PyInstaller onefile 遗留的 ``_MEI*`` 临时目录。"""
from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

_MEI_NAME = re.compile(r"^_MEI\d+$")
DEFAULT_STALE_AGE_SECONDS = 24 * 60 * 60


@dataclass
class CleanupResult:
    """一次清理操作的候选、成功和失败结果。"""

    candidates: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    failed: dict[Path, str] = field(default_factory=dict)


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_mei_directory(path: Path) -> bool:
    try:
        return path.is_dir() and _MEI_NAME.fullmatch(path.name) is not None
    except OSError:
        return False


def find_stale_runtime_dirs(
    temp_dir: Path | str | None = None,
    *,
    current_dir: Path | str | None = None,
    min_age_seconds: float = DEFAULT_STALE_AGE_SECONDS,
    now: float | None = None,
) -> list[Path]:
    """返回系统临时目录中明确过期的 PyInstaller `_MEI数字`目录。

    只扫描临时目录的直接子目录，不跟随链接；`current_dir` 始终跳过，
    用于保护当前 onefile 进程正在使用的运行目录。
    """
    root = _resolved(temp_dir or tempfile.gettempdir())
    current = _resolved(current_dir) if current_dir is not None else None
    timestamp = time.time() if now is None else float(now)
    result: list[Path] = []

    try:
        children = list(root.iterdir())
    except OSError:
        return result

    for child in children:
        if not _is_mei_directory(child):
            continue
        try:
            if current is not None and child.resolve(strict=False) == current:
                continue
            age = timestamp - child.stat().st_mtime
        except OSError:
            continue
        if age >= max(0.0, float(min_age_seconds)):
            result.append(child)

    return sorted(result, key=lambda item: item.name.lower())


def _remove_readonly(func, path: str, _exc_info) -> None:
    """为 shutil.rmtree 的只读文件重试删除；不修改 ACL 或所有者。"""
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def _dir_in_use(directory: Path) -> bool:
    """探活：目录被存活实例占用时不可删除。

    Windows 上运行中的 onefile 实例锁定其 _MEI 目录内的 DLL/pyd，
    对目录做原地改名会失败（WinError 32/5）——改名探测比按文件名猜 PID 可靠。
    探测成功会立刻改回原名；仅在 Windows 启用（POSIX 上 rename 总能成功，
    无法作为占用信号，回退为纯年龄判定）。
    """
    if os.name != "nt":
        return False
    probe = directory.with_name(directory.name + ".probe")
    try:
        os.rename(directory, probe)
        os.rename(probe, directory)
    except OSError:
        # 改名失败：可能是占用（跳过），也可能是权限问题（rmtree 同样会失败）
        try:
            if probe.exists() and not directory.exists():
                os.rename(probe, directory)  # 尽力恢复原名
        except OSError:
            pass
        return True
    return False


def cleanup_stale_runtime_dirs(
    temp_dir: Path | str | None = None,
    *,
    current_dir: Path | str | None = None,
    min_age_seconds: float = DEFAULT_STALE_AGE_SECONDS,
    now: float | None = None,
    dry_run: bool = False,
) -> CleanupResult:
    """清理过期 `_MEI`目录；删除前会先做占用探活，跳过仍被存活实例使用的目录。"""
    candidates = find_stale_runtime_dirs(
        temp_dir,
        current_dir=current_dir,
        min_age_seconds=min_age_seconds,
        now=now,
    )
    result = CleanupResult(candidates=list(candidates))
    if dry_run:
        return result

    for directory in candidates:
        if _dir_in_use(directory):
            continue  # 另一个仍在运行的实例：跳过，等它退出后下次再清
        try:
            shutil.rmtree(directory, onerror=_remove_readonly)
        except OSError as exc:
            result.failed[directory] = f"{type(exc).__name__}: {exc}"
        else:
            result.removed.append(directory)
    return result