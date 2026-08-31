# -*- coding: utf-8 -*-
"""B7 审查修复回归：动画启动被拒（start() 返回 False）时窗口层的可观测降级。

锁定 P1-1 窗口层行为：
1. _switch 拿到 start() 失败必须回退到上一个可播放动画（或 idle），
   绝不留下 "anim 已切换但 movie 未在播" 的停滞态；
2. 失败后安排稍后重试被拒动画，reader 可回收后重试成功恢复；
3. 上一动画与 idle 都被拒（极端）时保留最后渲染帧、释放 click/interaction
   hold，并仍安排重试（用户可见的恢复路径，而非静默死停）。

以及 P1-2 库层：pause_warm（隐藏/切角色）必须取消在飞首帧预热。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.window import PetWindow

NAMES = [
    catalog.IDLE,
    catalog.TURN,
    catalog.MOVES[0],
    catalog.CLICKS[0],
    catalog.DRAG,
    "写代码",
]


class FakeClip(QObject):
    """与 WebMClip 形状一致的假 clip：start() 可配置为失败（返回 False）。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, fail: bool = False, parent=None):
        super().__init__(parent)
        self.fail = fail
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        if self.fail:
            return False
        self._running = True
        return True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self, failing: set[str] | None = None):
        self._failing = set(failing or ())
        self._clips = {name: FakeClip(fail=name in self._failing) for name in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0

    def set_failing(self, name: str, failing: bool) -> None:
        self._clips[name].fail = failing


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_win(tmp_path, lib):
    cfg = Config(base=tmp_path)
    return PetWindow(lib, cfg)


def test_switch_rejected_start_restores_previous_animation_and_retries(app, tmp_path):
    """P1-1：目标动画 start() 被拒时回退上一动画（仍在播），并安排稍后重试。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    # 初始 idle 播放成功
    assert win.anim == catalog.IDLE
    assert win.movie is lib.movie(catalog.IDLE)
    assert win.movie._running is True

    win._switch("写代码")  # 目标动画 start 被拒

    # 回退：anim 仍是 idle 且其 clip 在播——绝无"anim 已切但 movie 未播"
    assert win.anim == catalog.IDLE
    assert win.movie is lib.movie(catalog.IDLE)
    assert win.movie._running is True
    assert win._click_hold is False, "回退后不得残留点击 hold"
    assert win._pending_switch == "写代码", "被拒动画必须登记待重试"
    assert win._switch_retry_timer.isActive(), "必须安排稍后重试"

    # reader 可回收后重试成功：切到目标动画并清除待重试状态
    lib.set_failing("写代码", False)
    win._on_switch_retry_timeout()
    assert win.anim == "写代码"
    assert win.movie is lib.movie("写代码")
    assert win.movie._running is True
    assert win._pending_switch is None
    assert win._switch_retry_timer.isActive() is False

    win.close()
    app.processEvents()


def test_switch_rejected_start_no_previous_falls_back_to_idle(app, tmp_path):
    """P1-1：无上一动画可回退（含上一动画同样被拒）时回退到可播放 idle。"""
    # 让 idle 与目标动画都失败：初始 _switch(idle) 即失败（prev_movie 为 None）
    lib = FakeLibrary(failing={catalog.IDLE, "写代码"})
    win = _make_win(tmp_path, lib)

    # 初始 idle 被拒：不得停滞——保留最后渲染帧、释放 hold、安排重试
    assert win.anim == catalog.IDLE
    assert win._click_hold is False
    assert win._pending_switch == catalog.IDLE
    assert win._switch_retry_timer.isActive()

    # 恢复后重试成功：idle 开始播放
    lib.set_failing(catalog.IDLE, False)
    win._on_switch_retry_timeout()
    assert win.anim == catalog.IDLE
    assert win.movie._running is True
    assert win._pending_switch is None

    # 再切目标动画：此时上一动画（idle）可回退
    lib.set_failing("写代码", True)
    win._switch("写代码")
    assert win.anim == catalog.IDLE, "目标被拒必须回退上一动画"
    assert win.movie._running is True
    assert win._pending_switch == "写代码"
    assert win._switch_retry_timer.isActive()

    win.close()
    app.processEvents()


def test_pause_activity_drops_pending_switch_retry(app, tmp_path):
    """P1-1：窗口隐藏（pause_activity）时停止待重试，避免隐藏期间反复重试。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    win._switch("写代码")  # 被拒 → 待重试
    assert win._pending_switch == "写代码"
    assert win._switch_retry_timer.isActive()

    win._pause_activity()
    assert win._pending_switch is None
    assert win._switch_retry_timer.isActive() is False
    assert win._switch_retry_count == 0

    win.close()
    app.processEvents()


def test_library_pause_warm_cancels_inflight_first_frame_warm(app, tmp_path, monkeypatch):
    """P1-2：pause_warm（隐藏/切角色）必须取消在飞的首帧预热。"""
    import pet.library as library_mod

    class CancelTrackingClip:
        def __init__(self, path, parent=None):
            self.path = Path(path)
            self.cancel_calls = 0

        def warm_meta(self):
            pass

        def warm_first_frame(self):
            pass

        def cancel_first_frame_warm(self):
            self.cancel_calls += 1

    monkeypatch.setattr(library_mod, "WebMClip", CancelTrackingClip)
    videos = tmp_path / "videos"
    folders = {
        "idle": ["待机呼吸休闲.webm"],
        "turn": ["东张西望.webm"],
        "move": ["螃蟹走路.webm"],
        "click": ["点击回应 - 开心跃动.webm"],
        "drag": ["被鼠标拖拽悬空反馈.webm"],
        "random": ["写代码.webm"],
    }
    for folder, files in folders.items():
        directory = videos / folder
        directory.mkdir(parents=True)
        for name in files:
            (directory / name).write_bytes(b"fake")
    lib = library_mod.MovieLibrary(asset_dir=videos)

    clips = list(lib._movies.values())
    assert clips, "库构造后应有已创建的 clip"
    assert all(c.cancel_calls == 0 for c in clips)

    lib.pause_warm()
    assert all(c.cancel_calls == 1 for c in clips), \
        "pause_warm 必须取消每个已创建 clip 的在飞首帧预热"
    app.processEvents()
