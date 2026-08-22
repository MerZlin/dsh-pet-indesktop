from __future__ import annotations

import os
import time
from pathlib import Path

from pet.runtime_cleanup import (
    cleanup_stale_runtime_dirs,
    find_stale_runtime_dirs,
)


def _touch_dir(path: Path, age_seconds: float) -> None:
    path.mkdir()
    timestamp = time.time() - age_seconds
    os.utime(path, (timestamp, timestamp))


def test_find_stale_runtime_dirs_only_returns_old_mei_directories(tmp_path: Path) -> None:
    old_dir = tmp_path / "_MEI12345"
    young_dir = tmp_path / "_MEI67890"
    wrong_name = tmp_path / "_MEI-not-a-number"
    _touch_dir(old_dir, 48 * 3600)
    _touch_dir(young_dir, 30 * 60)
    _touch_dir(wrong_name, 48 * 3600)

    result = find_stale_runtime_dirs(
        temp_dir=tmp_path,
        min_age_seconds=24 * 3600,
        now=time.time(),
    )

    assert result == [old_dir]


def test_find_stale_runtime_dirs_skips_current_runtime_directory(tmp_path: Path) -> None:
    current_dir = tmp_path / "_MEI12345"
    old_other_dir = tmp_path / "_MEI67890"
    _touch_dir(current_dir, 48 * 3600)
    _touch_dir(old_other_dir, 48 * 3600)

    result = find_stale_runtime_dirs(
        temp_dir=tmp_path,
        current_dir=current_dir,
        min_age_seconds=24 * 3600,
        now=time.time(),
    )

    assert result == [old_other_dir]


def test_cleanup_stale_runtime_dirs_reports_removed_and_failed(tmp_path: Path) -> None:
    old_dir = tmp_path / "_MEI12345"
    _touch_dir(old_dir, 48 * 3600)
    (old_dir / "readme.txt").write_text("stale", encoding="utf-8")
    old_timestamp = time.time() - 48 * 3600
    os.utime(old_dir, (old_timestamp, old_timestamp))

    result = cleanup_stale_runtime_dirs(
        temp_dir=tmp_path,
        min_age_seconds=24 * 3600,
        now=time.time(),
    )

    assert result.removed == [old_dir]
    assert result.failed == {}
    assert not old_dir.exists()


def test_cleanup_dry_run_does_not_delete_candidates(tmp_path: Path) -> None:
    old_dir = tmp_path / "_MEI12345"
    _touch_dir(old_dir, 48 * 3600)

    result = cleanup_stale_runtime_dirs(
        temp_dir=tmp_path,
        min_age_seconds=24 * 3600,
        now=time.time(),
        dry_run=True,
    )

    assert result.candidates == [old_dir]
    assert result.removed == []
    assert result.failed == {}
    assert old_dir.exists()
