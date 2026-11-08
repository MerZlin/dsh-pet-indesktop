# -*- coding: utf-8 -*-
"""首跑供给的空闲闸门（2026-11-01 启动 CPU 尖峰）。

现场：每次启动约 60 秒后 `首跑供给完成 converted=2 ... deferred=26`，ffmpeg
VP9/WebP 转换让任务管理器看到 9% CPU、持续约一分钟。这些 clip 有等价旧产物，
只是画质档位待升级，不该和用户开机抢 CPU。这里钉住「等系统连续空闲 60 秒
才起手」，并允许测试注入 idle_provider / sleep，不用真的等。
"""

from __future__ import annotations

import threading
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from petcore import paths
from petcore.media import provision


def _host(tmp_path):
    root = tmp_path / "shenshen"
    (root / "videos").mkdir(parents=True, exist_ok=True)
    character = SimpleNamespace(
        root=str(root),
        animations=[SimpleNamespace(path=str(root / "videos" / "idle.webm"))],
    )
    return SimpleNamespace(window=SimpleNamespace(character=character))


def _report():
    return SimpleNamespace(
        converted=2,
        retired=0,
        deferred_budget=0,
        deferred_backoff=0,
        deferred_disk=0,
        locked=False,
        idle=False,
    )


def test_provision_waits_for_system_idle(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(provision, "_started", False)
    values = [0.0, 0.0, 120.0]
    sleeps: list[float] = []
    calls: list = []
    done = threading.Event()

    def idle() -> float:
        return values.pop(0) if values else 120.0

    def fake_provision(character):
        calls.append(character)
        done.set()
        return _report()

    monkeypatch.setattr(provision, "provision_character", fake_provision)
    # worker 在 `done.set()` 之后还会写一条完成日志；测试可能先返回、pytest
    # 已拆掉 logging capture，线程再写就会在日志里留一段 closed-file traceback。
    monkeypatch.setattr(provision.logger, "info", lambda *args, **kwargs: None)
    assert provision.start_background_provision(
        _host(tmp_path),
        idle_wait=60.0,
        poll_seconds=1.0,
        initial_delay=0.0,
        idle_provider=idle,
        sleep=sleeps.append,
    ) is True
    assert done.wait(5.0), "空闲后应起手一轮供给"
    # 两次 < 60s 的检查各睡 1 秒；第三次 120s 满足条件直接起手。
    assert sleeps == [1.0, 1.0]
    assert len(calls) == 1
    # 第二个进程级请求直接拒绝（每个进程最多一轮）。
    assert provision.start_background_provision(
        _host(tmp_path),
        idle_wait=60.0,
        poll_seconds=1.0,
        initial_delay=0.0,
        idle_provider=lambda: 120.0,
        sleep=sleeps.append,
    ) is False


def test_provision_requires_a_writable_character_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(provision, "_started", False)
    host = SimpleNamespace(window=SimpleNamespace(character=None))
    assert provision.start_background_provision(host) is False
    assert provision._started is False


def _frozen_resources(monkeypatch, tmp_path, platform):
    if platform == "darwin":
        package = tmp_path / "Pet 中文.app"
        executable = package / "Contents/MacOS/dsh-pet-core"
        resources = package / "Contents/Resources"
        internal = package / "Contents/Frameworks"
    else:
        package = tmp_path / "package"
        executable = package / "dsh-pet-core"
        resources = internal = package / "_internal"
    monkeypatch.setattr(paths, "sys", SimpleNamespace(
        frozen=True, platform=platform, executable=str(executable), _MEIPASS=str(internal),
    ))
    return resources


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_frozen_posix_provision_never_modifies_writable_bundle(monkeypatch, tmp_path, platform):
    from pet import frameseq_provision

    resources = _frozen_resources(monkeypatch, tmp_path, platform)
    character = _host(resources / "assets").window.character
    root = Path(character.root)
    assert os.access(root, os.W_OK)
    before = set(root.rglob("*"))

    def encode(_videos, output, **_kwargs):
        output.mkdir()
        return _report()

    # OS media boundary: a converter invocation would publish files in this root.
    monkeypatch.setattr(frameseq_provision, "provision_once", encode)
    assert provision.provision_character(character) is None
    assert set(root.rglob("*")) == before


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_frozen_posix_external_character_can_still_be_provisioned(monkeypatch, tmp_path, platform):
    from pet import frameseq_provision

    _frozen_resources(monkeypatch, tmp_path, platform)
    character = _host(tmp_path / "user-data/mods").window.character
    calls = []
    report = _report()
    monkeypatch.setattr(frameseq_provision, "provision_once", lambda *a, **k: calls.append(a) or report)
    assert provision.provision_character(character) is report
    assert calls[0][1] == Path(character.root) / "frameseq"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_frozen_posix_bundle_does_not_start_a_provision_thread(monkeypatch, tmp_path, platform):
    resources = _frozen_resources(monkeypatch, tmp_path, platform)
    monkeypatch.setattr(provision, "_started", False)
    done = threading.Event()
    monkeypatch.setattr(provision, "provision_character", lambda _character: done.set())
    monkeypatch.setattr(provision.logger, "info", lambda *a, **k: None)
    started = provision.start_background_provision(
        _host(resources / "assets"), initial_delay=0, idle_wait=0, idle_provider=lambda: 0,
    )
    if started:
        assert done.wait(5), "Unexpected worker must finish before fixture cleanup"
    assert started is False
    assert provision._started is False
    assert not done.is_set()
