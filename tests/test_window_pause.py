# -*- coding: utf-8 -*-
"""窗口隐藏/显示时暂停与恢复动画解码的回归测试。

背景：桌宠隐藏（托盘隐藏 / 全屏自动隐藏）后仍会继续 24fps 解码与重建
mask，多开时属于纯白烧。此测试锁定 hideEvent 暂停、showEvent 恢复的行为。
"""
from __future__ import annotations

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
    """与 WebMClip 接口兼容的极简假播放器，只记录启停状态。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.stop_count = 0
        self.start_count = 0
        self.speed = 1.0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False
        self.stop_count += 1

    def start(self):
        self._running = True
        self.start_count += 1

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
    """只包含核心动画名的假素材库，避免测试拉真 ffmpeg。"""

    def __init__(self):
        self._clips = {name: FakeClip() for name in NAMES}
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


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_hide_pauses_and_show_resumes_activity(app, tmp_path):
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.show()
    app.processEvents()

    idle = win.movie
    assert idle is not None
    assert idle._running is True
    assert win._hidden_paused is False

    win.hide()
    app.processEvents()
    assert win._hidden_paused is True
    assert idle._running is False
    assert not win._move_timer.isActive()
    assert not win._physics_timer.isActive()
    assert not win._topmost_watchdog.isActive()
    assert not win._self_talk_timer.isActive()
    assert not win._fullscreen_timer.isActive()
    # 隐藏时不产生任何可见气泡
    win.show_bubble("隐藏时不应显示")
    win.set_chat_status("thinking", "隐藏时不应显示")
    assert not win._speech_bubble.isVisible()

    win.show()
    app.processEvents()
    assert win._hidden_paused is False
    assert win.movie is not None
    assert win.movie._running is True
    assert win._topmost_watchdog.isActive()

    win.close()
    app.processEvents()
