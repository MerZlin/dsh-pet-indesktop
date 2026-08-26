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


def test_auto_hide_keeps_fullscreen_watcher_alive(app, tmp_path, monkeypatch):
    """全屏自动隐藏时 watcher 必须保持运行——它是退出全屏后 show() 回来的唯一路径。"""
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.auto_hide_fullscreen = True
    win.show()
    app.processEvents()
    win._fullscreen_timer.start()  # 非 Windows 平台初始不启动，测试里强制模拟

    # 模拟全屏检测命中：_check_fullscreen 先置 _auto_hidden 再 hide()
    win._auto_hidden = True
    win.hide()
    app.processEvents()
    assert win._hidden_paused is True
    assert win._fullscreen_timer.isActive()  # 关键：watcher 不能被暂停

    # 模拟退出全屏：else 分支应把桌宠 show 回来并恢复活动
    monkeypatch.setattr(win, '_foreground_covers_fullscreen', lambda: False)
    win._check_fullscreen()
    app.processEvents()
    assert win._auto_hidden is False
    assert win._hidden_paused is False
    assert win.movie is not None and win.movie._running is True

    win.close()
    app.processEvents()


def test_disable_auto_hide_restores_pet(app, tmp_path):
    """自动隐藏状态下关闭开关：桌宠应立即恢复显示（玩游戏也想看到桌宠的场景）。"""
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.auto_hide_fullscreen = True
    win.show()
    app.processEvents()

    # 模拟已被全屏自动隐藏
    win._auto_hidden = True
    win.hide()
    app.processEvents()
    assert not win.isVisible()

    # 用户通过菜单关闭「全屏时自动隐藏」
    win.set_auto_hide_fullscreen(False)
    app.processEvents()
    assert win._auto_hidden is False
    assert win._hidden_paused is False
    assert win.isVisible()
    assert win.cfg.get('auto_hide_fullscreen') is False

    win.close()
    app.processEvents()
