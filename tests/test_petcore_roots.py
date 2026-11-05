"""数据根 / 缓存根：引导指针、搬迁校验、复制与校验、旧目录清理、设置页入口。

搬迁是「复制 → 校验 → 写指针 → 重启 → 下次启动删旧目录」，所以这里的重点不是
「搬得快」，而是**任何一步失败都不许写指针**——指针一旦指到半成品目录，
用户的会话与配置就等于消失。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore import paths
from petcore.storage import roots as storage_roots


@pytest.fixture(autouse=True)
def _reset_cache_root():
    paths.set_cache_root(None)
    yield
    paths.set_cache_root(None)


def _tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text('{"character": "shenshen"}', encoding="utf-8")
    (root / "sessions").mkdir(exist_ok=True)
    (root / "sessions" / "a.json").write_text("{}", encoding="utf-8")
    (root / "cache" / "tts").mkdir(parents=True, exist_ok=True)
    (root / "cache" / "tts" / "x.mp3").write_bytes(b"ID3" + b"0" * 100)
    (root / storage_roots.LOCK_FILENAME).write_bytes(b"")
    return root


# ------------------------------------------------------------------ 指针
def test_pointer_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "location.json"
    pointer = storage_roots.Pointer(data_root=tmp_path / "data", cache_root=tmp_path / "cache")
    storage_roots.write_pointer(path, pointer)
    assert storage_roots.read_pointer(path) == pointer


def test_missing_pointer_is_not_an_error(tmp_path: Path) -> None:
    assert storage_roots.read_pointer(tmp_path / "nope.json") is None


def test_broken_pointer_falls_back_to_default(tmp_path: Path) -> None:
    path = tmp_path / "location.json"
    path.write_text("{not json", encoding="utf-8")
    default = tmp_path / "default"
    resolved = storage_roots.resolve_roots(default, pointer_path=path)
    assert resolved.data_root == default
    assert resolved.cache_root == default
    assert resolved.source == "default"
    assert resolved.note
    # 坏文件不许被偷偷覆盖：用户还得能看到自己写了什么。
    assert path.read_text(encoding="utf-8") == "{not json"


def test_explicit_root_wins_over_pointer(tmp_path: Path) -> None:
    path = tmp_path / "location.json"
    storage_roots.write_pointer(path, storage_roots.Pointer(data_root=tmp_path / "pointed"))
    explicit = tmp_path / "explicit"
    resolved = storage_roots.resolve_roots(
        tmp_path / "default", pointer_path=path, explicit=explicit
    )
    assert resolved.data_root == explicit
    assert resolved.cache_root == explicit
    assert resolved.source == "cli"


def test_pointer_cache_root_defaults_to_data_root(tmp_path: Path) -> None:
    path = tmp_path / "location.json"
    storage_roots.write_pointer(path, storage_roots.Pointer(data_root=tmp_path / "data"))
    resolved = storage_roots.resolve_roots(tmp_path / "default", pointer_path=path)
    assert resolved.data_root == tmp_path / "data"
    assert resolved.cache_root == tmp_path / "data"
    assert resolved.source == "pointer"


def test_instance_ignores_the_pointer(tmp_path: Path, monkeypatch) -> None:
    pointer = tmp_path / "location.json"
    storage_roots.write_pointer(pointer, storage_roots.Pointer(data_root=tmp_path / "other"))
    monkeypatch.setattr(paths, "location_path", lambda portable: pointer)
    monkeypatch.setattr(paths, "default_data_root", lambda portable, instance: tmp_path / f"inst-{instance}")
    resolved = paths.resolve_roots(instance="two")
    assert resolved.data_root == tmp_path / "inst-two"
    assert resolved.source == "default"


def test_absolute_paths_survive_relative_entries(tmp_path: Path) -> None:
    payload = {"schema": 1, "data_root": "relative-data"}
    pointer = storage_roots.parse_pointer(payload, base_dir=tmp_path)
    assert pointer.data_root == tmp_path / "relative-data"


# ------------------------------------------------------------------ 缓存根
def test_cache_dir_follows_the_data_root_by_default(tmp_path: Path) -> None:
    assert paths.cache_dir(tmp_path, "cache", "tts") == tmp_path / "cache" / "tts"


def test_cache_dir_switches_to_the_cache_root(tmp_path: Path) -> None:
    paths.set_cache_root(tmp_path / "cachedisk")
    assert paths.cache_dir(tmp_path, "cache", "tts") == tmp_path / "cachedisk" / "cache" / "tts"


def test_a_bound_cache_root_does_not_leak_to_other_roots(tmp_path: Path) -> None:
    """按 A 根设置的缓存根不许被 B 根取走（--check 指向别处时尤其致命）。"""
    mine = tmp_path / "mine"
    other = tmp_path / "other"
    paths.set_cache_root(tmp_path / "cachedisk", data_root=mine)
    assert paths.cache_dir(mine, "cache", "tts") == tmp_path / "cachedisk" / "cache" / "tts"
    assert paths.cache_dir(other, "cache", "tts") == other / "cache" / "tts"


def test_tts_cache_and_market_downloads_follow_the_cache_root(tmp_path: Path) -> None:
    from petcore.market.download import download_dir
    from petcore.speech.cache import SpeechCache
    from petcore.update.service import UpdateService

    paths.set_cache_root(tmp_path / "cachedisk")
    assert SpeechCache(tmp_path).directory() == tmp_path / "cachedisk" / "cache" / "tts"
    assert download_dir(tmp_path) == tmp_path / "cachedisk" / "market" / "downloads"
    assert UpdateService(tmp_path).update_dir == tmp_path / "cachedisk" / "updates"


def test_everything_stays_in_the_data_root_when_split_is_off(tmp_path: Path) -> None:
    from petcore.market.download import download_dir
    from petcore.speech.cache import SpeechCache
    from petcore.update.service import UpdateService

    assert SpeechCache(tmp_path).directory() == tmp_path / "cache" / "tts"
    assert download_dir(tmp_path) == tmp_path / "market" / "downloads"
    assert UpdateService(tmp_path).update_dir == tmp_path / "updates"


# ------------------------------------------------------------------ 校验
def test_validate_target_accepts_an_empty_directory(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    assert storage_roots.validate_target(source, target) is None


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda src, tmp: src, id="same-directory"),
        pytest.param(lambda src, tmp: src / "inside", id="inside-source"),
        pytest.param(lambda src, tmp: tmp, id="parent-of-source"),
    ],
)
def test_validate_target_rejects_bad_locations(tmp_path: Path, build) -> None:
    source = _tree(tmp_path / "src")
    problem = storage_roots.validate_target(source, build(source, tmp_path))
    assert problem
    assert isinstance(problem, str)


def test_validate_target_rejects_a_non_empty_directory(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    assert "空目录" in str(storage_roots.validate_target(source, target))


def test_validate_target_rejects_a_missing_source(tmp_path: Path) -> None:
    assert storage_roots.validate_target(tmp_path / "gone", tmp_path / "dst")


@pytest.mark.skipif(sys.platform != "win32", reason="盘符根只在 Windows 上有意义")
def test_validate_target_rejects_a_drive_root(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    assert storage_roots.validate_target(source, Path("C:/"))


# ------------------------------------------------------------------ 复制
def test_plan_and_copy_move_everything_except_the_lock(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    plan = storage_roots.with_target(storage_roots.plan_move(source), target)
    assert plan.files == 3  # config.json / sessions/a.json / cache/tts/x.mp3
    assert all(item.relative.name != storage_roots.LOCK_FILENAME for item in plan.items)
    seen: list[tuple[int, int]] = []
    result = storage_roots.copy_tree(plan, progress=lambda done, total: seen.append((done, total)))
    assert result.entries == 3
    assert seen and seen[-1] == (plan.total_bytes, plan.total_bytes)
    ok, detail = storage_roots.verify_copy(plan)
    assert ok, detail
    assert (target / "sessions" / "a.json").is_file()


def test_plan_with_include_only_takes_the_cache_parts(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    (source / "updates").mkdir()
    (source / "updates" / "setup.exe").write_bytes(b"x" * 10)
    plan = storage_roots.plan_move(source, include=storage_roots.CACHE_PARTS)
    relatives = {item.relative.as_posix() for item in plan.items}
    assert relatives == {"cache/tts/x.mp3", "updates/setup.exe"}


def test_verify_detects_a_missing_target_file(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    plan = storage_roots.with_target(storage_roots.plan_move(source), target)
    storage_roots.copy_tree(plan)
    (target / "config.json").unlink()
    ok, detail = storage_roots.verify_copy(plan)
    assert ok is False
    assert "缺少" in detail


def test_cancel_keeps_the_partial_copy(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    plan = storage_roots.with_target(storage_roots.plan_move(source), target)
    with pytest.raises(storage_roots.MoveCancelled):
        storage_roots.copy_tree(plan, cancel=lambda: True)
    assert target.is_dir()


# ------------------------------------------------------------------ 任务
def test_move_job_writes_the_pointer_and_the_journal(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.roots_job import RootsMoveJob

    instance = QApplication.instance() or QApplication([])
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    pointer_path = tmp_path / "location.json"
    outcomes: list[object] = []
    job = RootsMoveJob(
        source=source,
        target=target,
        pointer_path=pointer_path,
        pointer_after=storage_roots.Pointer(data_root=target, cache_root=target),
        kind="data",
    )
    job.done.connect(outcomes.append)
    job._run()  # 直接跑，避开线程与事件循环
    assert outcomes and outcomes[0].files == 3
    assert storage_roots.read_pointer(pointer_path) == storage_roots.Pointer(
        data_root=target, cache_root=target
    )
    journal = storage_roots.read_journal(target)
    assert journal is not None
    assert journal["old_root"] == str(source)
    assert job.is_running() is False
    assert instance is not None


def test_move_job_does_not_write_the_pointer_when_verify_fails(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings import roots_job

    QApplication.instance() or QApplication([])
    source = _tree(tmp_path / "src")
    target = tmp_path / "dst"
    target.mkdir()
    pointer_path = tmp_path / "location.json"
    failures: list[str] = []
    job = roots_job.RootsMoveJob(
        source=source,
        target=target,
        pointer_path=pointer_path,
        pointer_after=storage_roots.Pointer(data_root=target),
        kind="data",
    )
    job.failed.connect(failures.append)
    original = roots_job.storage_roots.verify_copy
    roots_job.storage_roots.verify_copy = lambda plan: (False, "缺少 1 个文件")  # type: ignore[assignment]
    try:
        job._run()
    finally:
        roots_job.storage_roots.verify_copy = original  # type: ignore[assignment]
    assert failures and "校验" in failures[0]
    assert storage_roots.read_pointer(pointer_path) is None


# ------------------------------------------------------------------ 清理
def test_cleanup_removes_the_old_root_and_keeps_the_pointer(tmp_path: Path) -> None:
    old = _tree(tmp_path / "old")
    new = tmp_path / "new"
    new.mkdir()
    pointer_path = old / storage_roots.LOCATION_FILENAME
    storage_roots.write_pointer(
        pointer_path, storage_roots.Pointer(data_root=new)
    )
    storage_roots.write_journal(new, old_root=old, pointer_path=pointer_path, kind="data")
    assert storage_roots.cleanup_moved_source(new) is True
    # 旧根里只会剩下指针文件（删树后原地写回），其余内容必须消失。
    assert pointer_path.is_file()
    assert [item.name for item in old.iterdir()] == [storage_roots.LOCATION_FILENAME]
    assert not (old / "config.json").exists()
    # 指针原来躺在旧根里：删树后必须原地写回来，否则下次启动退回空默认根。
    assert storage_roots.read_pointer(pointer_path) == storage_roots.Pointer(data_root=new)
    assert storage_roots.read_journal(new) is None


def test_cleanup_is_a_noop_without_a_journal(tmp_path: Path) -> None:
    assert storage_roots.cleanup_moved_source(tmp_path) is False


def test_cleanup_ignores_a_journal_pointing_at_the_current_root(tmp_path: Path) -> None:
    root = _tree(tmp_path / "root")
    storage_roots.write_journal(root, old_root=root, pointer_path=tmp_path / "p.json", kind="data")
    assert storage_roots.cleanup_moved_source(root) is False
    assert root.is_dir()


# ------------------------------------------------------------------ 设置页
def test_settings_page_shows_both_paths_and_stays_clean(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.pages_core import GeneralPage
    from tests.test_petcore_settings import _host

    QApplication.instance() or QApplication([])
    page = GeneralPage(_host(tmp_path))
    try:
        assert page.data_dir_label.text() == str(tmp_path)
        assert page.cache_dir_label.text() == str(tmp_path)
        assert page.roots_panel.isHidden() is True
        assert page.dirty() is False
        haystack = " ".join(page.keywords())
        for needle in ("数据目录", "缓存目录", "清空缓存", "搬迁"):
            assert needle in haystack, needle
    finally:
        page.dispose()
        page.deleteLater()


def test_pack_download_dir_prefers_the_app_directory(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.pages_core import GeneralPage
    from tests.test_petcore_settings import _host

    QApplication.instance() or QApplication([])
    page = GeneralPage(_host(tmp_path))
    try:
        resolved = page.pack_download_dir()
        assert resolved.is_dir()
        assert resolved.name == "tmp"
    finally:
        page.dispose()
        page.deleteLater()


def test_pointer_schema_that_is_not_a_number_is_a_roots_error() -> None:
    with pytest.raises(storage_roots.RootsError):
        storage_roots.parse_pointer({"schema": "v1"}, base_dir=Path("."))


def test_cleanup_refuses_a_relative_old_root(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    victim = work / "victim"
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    root = tmp_path / "new"
    root.mkdir()
    storage_roots.write_journal(
        root, old_root="victim", pointer_path=tmp_path / "p.json", kind="data"
    )
    monkeypatch.chdir(work)
    assert storage_roots.cleanup_moved_source(root) is False
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "x"
    assert storage_roots.read_journal(root) is None


def test_cleanup_refuses_a_drive_root(tmp_path: Path) -> None:
    root = tmp_path / "new"
    root.mkdir()
    storage_roots.write_journal(
        root, old_root=Path(tmp_path.anchor), pointer_path=tmp_path / "p.json", kind="data"
    )
    assert storage_roots.cleanup_moved_source(root) is False
    assert root.is_dir()
    assert storage_roots.read_journal(root) is None
