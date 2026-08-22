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


def cleanup_stale_runtime_dirs(
    temp_dir: Path | str | None = None,
    *,
    current_dir: Path | str | None = None,
    min_age_seconds: float = DEFAULT_STALE_AGE_SECONDS,
    now: float | None = None,
    dry_run: bool = False,
) -> CleanupResult:
    """清理过期 `_MEI`目录；默认调用方应先确保没有活动实例。"""
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
        try:
            shutil.rmtree(directory, onerror=_remove_readonly)
        except OSError as exc:
            result.failed[directory] = f"{type(exc).__name__}: {exc}"
        else:
            result.removed.append(directory)
    return result