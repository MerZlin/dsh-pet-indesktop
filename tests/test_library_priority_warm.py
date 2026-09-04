# -*- coding: utf-8 -*-
"""素材库懒加载 + 默认优先级预热行为的回归测试。

锁定两点：
1. MovieLibrary 构造时只创建高优先级动画（idle/turn/click/drag/move），
   随机动作池按需创建，不一次性 new 出全部 91 个播放器；
2. 随机动作池预热由应用层 schedule_low_priority_warm() 延迟触发，
   不在库构造时自动启动（避免测试/非事件循环环境凭空拉线程）。
"""
from __future__ import annotations

from pathlib import Path

from pet import catalog
import pet.library as library_mod


class FakeClip:
    """极简假 WebMClip：记录预热调用，不碰 ffmpeg/Qt。"""

    def __init__(self, path, parent=None):
        self.path = Path(path)
        self.warmed_meta = False
        self.warmed_frame = False

    def warm_meta(self):
        self.warmed_meta = True

    def warm_first_frame(self):
        self.warmed_frame = True


def _make_lib(tmp_path, monkeypatch, prewarm_policy="balanced"):
    monkeypatch.setattr(library_mod, "WebMClip", FakeClip)
    videos = tmp_path / "videos"
    folders = {
        "idle": ["待机呼吸休闲.webm"],
        "turn": ["东张西望.webm"],
        "move": ["螃蟹走路.webm"],
        "click": ["点击回应 - 开心跃动.webm"],
        "drag": ["被鼠标拖拽悬空反馈.webm"],
        "random": ["写代码.webm", "吃白饭.webm"],
    }
    for folder, files in folders.items():
        directory = videos / folder
        directory.mkdir(parents=True, exist_ok=True)
        for name in files:
            (directory / name).write_bytes(b"fake")
    return library_mod.MovieLibrary(asset_dir=videos, prewarm_policy=prewarm_policy)


def test_lazy_creates_only_high_priority_at_start(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)

    high_expected = {
        catalog.IDLE,
        catalog.TURN,
        catalog.MOVES[0],
        catalog.CLICKS[0],
        catalog.DRAG,
    }
    assert set(lib._movies.keys()) == high_expected
    assert {"写代码", "吃白饭"} <= set(lib.names())
    assert "写代码" not in lib._movies

    clip = lib.movie("写代码")
    assert "写代码" in lib._movies
    assert clip.path.name == "写代码.webm"


def test_priority_names_split(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    high, low = lib._priority_names()

    assert catalog.IDLE in high
    assert catalog.DRAG in high
    assert "写代码" in low
    assert "吃白饭" in low
    assert set(high).isdisjoint(low)


def test_priority_names_click_before_idle(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    high, _ = lib._priority_names()

    # 点击回应必须优先于 idle/turn/move 预热：首次点击最怕同步 ffmpeg 解码卡顿。
    assert high.index(catalog.CLICKS[0]) < high.index(catalog.IDLE)
    assert high.index(catalog.CLICKS[0]) < high.index(catalog.TURN)
    assert high.index(catalog.CLICKS[0]) < high.index(catalog.MOVES[0])


def test_low_priority_warm_not_auto_started_then_scheduled(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    lib = _make_lib(tmp_path, monkeypatch)
    assert lib._low_warm_timer.isActive() is False

    lib.schedule_low_priority_warm()
    assert lib._low_warm_timer.isActive() is True
    app.processEvents()


def test_prewarm_policy_default_balanced(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    assert lib._prewarm_policy == "balanced"


def test_prewarm_invalid_policy_falls_back_to_balanced(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch, prewarm_policy="magic")
    assert lib._prewarm_policy == "balanced"


def test_warm_objects_skips_frames_when_include_frames_false(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    clip = lib.movie("写代码")
    lib._warm_objects([clip], 1, include_frames=False)
    assert clip.warmed_meta is True
    assert clip.warmed_frame is False


def test_warm_objects_decodes_frames_when_include_frames_true(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    clip = lib.movie("写代码")
    lib._warm_objects([clip], 1, include_frames=True)
    assert clip.warmed_meta is True
    assert clip.warmed_frame is True


def test_low_warm_include_frames_by_policy(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch, prewarm_policy="balanced")
    captured = {}
    lib._warm_objects = lambda clips, workers, **kw: captured.update(kw)

    class SyncThread:
        def __init__(self, target, *a, **k):
            self.target = target
            self.args = a

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(library_mod.threading, "Thread", SyncThread)
    lib._warm_low_priority_background()
    assert captured["include_frames"] is False

    lib = _make_lib(tmp_path, monkeypatch, prewarm_policy="full")
    captured = {}
    lib._warm_objects = lambda clips, workers, **kw: captured.update(kw)
    lib._warm_low_priority_background()
    assert captured["include_frames"] is True


def test_app_create_library_prewarm_derivation(tmp_path, monkeypatch):
    """预热策略推导：默认 balanced；省电模式（idle_low_fps）强制 minimal；
    手改 media_prewarm=full 的高级配置在非省电模式下生效、被省电模式覆盖。"""
    from pet import app as app_mod
    from pet.config import Config

    captured = {}

    class FakeLib:
        def __init__(self, **kw):
            captured.update(kw)

        def schedule_high_priority_warm(self):
            pass

        def schedule_low_priority_warm(self):
            pass

        def names(self):
            return []

    monkeypatch.setattr(app_mod, "MovieLibrary", FakeLib)

    app = app_mod.AppShell(object(), Config(tmp_path / "a"))
    app.instance._create_library("shenshen")
    assert captured["prewarm_policy"] == "balanced"

    cfg = Config(tmp_path / "b")
    cfg.set("media_prewarm", "full")
    app = app_mod.AppShell(object(), cfg)
    app.instance._create_library("shenshen")
    assert captured["prewarm_policy"] == "full"

    cfg = Config(tmp_path / "c")
    cfg.set("media_prewarm", "full")
    cfg.set("idle_low_fps_enabled", True)
    app = app_mod.AppShell(object(), cfg)
    app.instance._create_library("shenshen")
    assert captured["prewarm_policy"] == "minimal"
