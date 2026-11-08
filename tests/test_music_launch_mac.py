# -*- coding: utf-8 -*-
"""右键「打开某播放器给主人放歌」的 macOS 点击路径（``shared._launch_player_and_play``）。

只换操作系统边界：播放器路径解析（``find_player``，其 mac 分支由
``tests/test_music_players_mac.py`` 覆盖）、``open`` 启动（LaunchServices）、
「正在播放」查询（``play_session_for``）。worker 线程、信号桥与轮询节奏走产品代码。
"""
from __future__ import annotations

import plistlib
import threading
import time
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from pet.context_menus import shared
from pet.music import now_playing, players as music_players


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, *, timeout: float = 15.0, pump=None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pump is not None:
            pump()
        if predicate():
            return True
        time.sleep(0.01)
    if pump is not None:
        pump()
    return bool(predicate())


def _release(pet) -> None:
    """先等 worker 线程收工、处理完 queued 信号，再销毁宿主。

    宿主是 QObject 时信号桥是它的子对象（不进 ``_LAUNCH_BRIDGES``）：worker 还在
    ``_worker_done.emit()`` 时就 ``shiboken6.delete(pet)``，是「销毁与 emit 并发」的
    竞态——``test_music_player_cache.py`` 里同模式的既有用例在满载下会因此段错误
    （基线 34d810b 上 14 次复现 4 次，见 docs/PR-REPORT-MAC-MUSIC-2026-11-08.md §六）。
    """
    for thread in threading.enumerate():
        if thread.name == "music-launch":
            thread.join(15.0)
            assert not thread.is_alive(), "music-launch worker 没有收工"
    app = _qapp()
    for _ in range(5):
        app.processEvents()
    shiboken6.delete(pet)


class _Cfg:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class _QtPet(QObject):
    def __init__(self, values=None):
        super().__init__()
        self.cfg = _Cfg(values)
        self.bubbles: list = []

    def show_bubble(self, text: str, duration_ms: int | None = None) -> None:
        self.bubbles.append((text, duration_ms))


def _make_app(root: Path, name: str, bundle_id: str) -> Path:
    app = root / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": bundle_id}))
    return app


@pytest.fixture
def mac_launch(monkeypatch, tmp_path):
    _qapp()
    monkeypatch.setattr(shared.sys, "platform", "darwin")
    monkeypatch.setattr(shared, "_LAUNCH_POLL_SECONDS", 0.01)
    app = _make_app(tmp_path, "NeteaseMusic.app", "com.netease.163music")
    box = {"app": app, "opened": [], "played": [], "play_results": [False, False, True],
           "open_ok": True, "done": threading.Event()}
    monkeypatch.setattr(music_players, "find_player", lambda key, manual="": str(app))

    def fake_open(path):
        box["opened"].append(path)
        return box["open_ok"]

    def fake_play(identity):
        box["played"].append(identity)
        result = box["play_results"].pop(0) if box["play_results"] else False
        if result or not box["play_results"]:
            box["done"].set()
        return result

    monkeypatch.setattr(shared, "_open_mac_app", fake_open)
    monkeypatch.setattr(now_playing, "play_session_for", fake_play)
    return box


def test_mac_launch_opens_the_app_bundle_and_plays_by_bundle_id(mac_launch):
    pet = _QtPet()
    try:
        shared._launch_player_and_play("netease", pet)
        assert mac_launch["done"].wait(15.0), "worker 没有跑完轮询"
    finally:
        _release(pet)

    assert mac_launch["opened"] == [str(mac_launch["app"])], "mac 上要经 open 启动应用包"
    assert mac_launch["played"] == ["com.netease.163music"] * 3, (
        "会话身份要用 Info.plist 的 bundle id，并在启动后轮询到它出现在「正在播放」"
    )


def test_mac_launch_skips_open_when_player_is_already_now_playing(mac_launch):
    mac_launch["play_results"] = [True]
    pet = _QtPet()
    try:
        shared._launch_player_and_play("netease", pet)
        assert mac_launch["done"].wait(15.0)
    finally:
        _release(pet)

    assert mac_launch["opened"] == []
    assert mac_launch["played"] == ["com.netease.163music"]


def test_mac_launch_reports_when_open_fails(mac_launch):
    """open 失败（应用被移走 / 损坏）：气泡说明，而不是静默等 10 秒。"""
    mac_launch["play_results"] = [False]
    mac_launch["open_ok"] = False
    pet = _QtPet()
    app = _qapp()
    try:
        shared._launch_player_and_play("netease", pet)
        assert _wait_until(lambda: bool(pet.bubbles), pump=app.processEvents)
    finally:
        _release(pet)

    assert "打不开网易云音乐" in pet.bubbles[0][0]
    assert mac_launch["played"] == ["com.netease.163music"], "启动失败后不该再轮询"


def test_open_mac_app_runs_launchservices_with_a_timeout(monkeypatch):
    seen: list = []

    class _Done:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return _Done()

    monkeypatch.setattr(shared.subprocess, "run", fake_run)
    assert shared._open_mac_app("/Applications/QQMusic.app/Contents/MacOS/QQMusic") is True
    argv, kwargs = seen[0]
    assert argv == ["/usr/bin/open", "/Applications/QQMusic.app"], "要 open 应用包，不执行包内二进制"
    assert kwargs.get("timeout")

    class _Failed:
        returncode = 1

    monkeypatch.setattr(shared.subprocess, "run", lambda argv, **kwargs: _Failed())
    assert shared._open_mac_app("/Applications/QQMusic.app") is False
