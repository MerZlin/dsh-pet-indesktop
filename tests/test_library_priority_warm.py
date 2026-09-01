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

    def __init__(self, path, parent=None, first_frame_cache=None):
        self.path = Path(path)
        self.warmed_meta = False
        self.warmed_frame = False

    def warm_meta(self):
        self.warmed_meta = True

    def warm_first_frame(self):
        self.warmed_frame = True


def _make_lib(tmp_path, monkeypatch):
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
        directory.mkdir(parents=True)
        for name in files:
            (directory / name).write_bytes(b"fake")
    return library_mod.MovieLibrary(asset_dir=videos)


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
