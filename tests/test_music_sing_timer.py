# -*- coding: utf-8 -*-
"""音乐自动唱歌 4s 检测定时器按需启停的回归测试（B4）。

背景：`_music_sing_timer` 构造时无条件 start()，且隐藏/设置切换都不停，
功能关闭时仍每 4s 唤醒一次 `_check_music_sing` 空转。
要求：`_music_sing_enabled` 为 False 时定时器必须停止，True 才启动；
设置切换即时生效；隐藏停止、恢复显示按当前开关状态恢复。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.window import SING_ANIM, PetWindow
from tests.test_window_pause import FakeLibrary


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_real_music_detection(monkeypatch):
    """测试环境不读真实音频峰值；默认按“没有在放音乐”处理。"""
    import pet.music_detect as music_detect
    monkeypatch.setattr(music_detect, "is_music_playing", lambda: False)


def _make_win(tmp_path, enabled: bool = False) -> PetWindow:
    cfg = Config(base=tmp_path)
    cfg.set("music_sing_enabled", enabled)
    return PetWindow(FakeLibrary(), cfg)


class SingingLibrary(FakeLibrary):
    """在核心动画基础上补唱歌动画，供“检测到音乐立即唱歌”测试使用。"""

    def __init__(self):
        super().__init__()
        from tests.test_window_pause import FakeClip
        self._clips[SING_ANIM] = FakeClip()


def test_music_sing_starts_immediately_when_visible(app, tmp_path, monkeypatch):
    """回归 issue #69-1：窗口可见且检测到音乐时应尽快进入唱歌，而不是等 4s 轮询。"""
    import pet.music_detect as music_detect

    cfg = Config(base=tmp_path)
    cfg.set("music_sing_enabled", True)
    win = PetWindow(SingingLibrary(), cfg)
    try:
        monkeypatch.setattr(music_detect, "is_music_playing", lambda: True)
        win.show()
        app.processEvents()
        assert win.anim == SING_ANIM
    finally:
        win.close()
        app.processEvents()


def test_music_sing_timer_stopped_when_disabled(app, tmp_path):
    """功能关闭（默认）：定时器必须处于停止状态。"""
    win = _make_win(tmp_path, enabled=False)
    try:
        assert not win._music_sing_timer.isActive()
    finally:
        win.close()
        app.processEvents()


def test_music_sing_timer_started_when_enabled(app, tmp_path):
    """功能开启：定时器启动。"""
    win = _make_win(tmp_path, enabled=True)
    try:
        assert win._music_sing_timer.isActive()
    finally:
        win.close()
        app.processEvents()


def test_music_sing_timer_toggle_via_refresh_pet_settings(app, tmp_path):
    """设置切换即时生效：可见状态下开→启、关→停。"""
    win = _make_win(tmp_path, enabled=False)
    try:
        win.show()
        app.processEvents()
        assert not win._music_sing_timer.isActive()

        win.cfg.set("music_sing_enabled", True)
        win.refresh_pet_settings()
        assert win._music_sing_timer.isActive()

        win.cfg.set("music_sing_enabled", False)
        win.refresh_pet_settings()
        assert not win._music_sing_timer.isActive()
    finally:
        win.close()
        app.processEvents()


def test_music_sing_timer_stops_on_hide_resumes_on_show(app, tmp_path):
    """隐藏停止定时器；恢复显示后按当前开关状态重新启动。"""
    win = _make_win(tmp_path, enabled=True)
    try:
        win.show()
        app.processEvents()
        assert win._music_sing_timer.isActive()

        win.hide()
        app.processEvents()
        assert not win._music_sing_timer.isActive()

        win.show()
        app.processEvents()
        assert win._music_sing_timer.isActive()
    finally:
        win.close()
        app.processEvents()


def test_music_sing_timer_toggled_while_hidden_stays_off_until_show(app, tmp_path):
    """隐藏期间把开关打开：定时器保持停止，恢复显示后才启动。"""
    win = _make_win(tmp_path, enabled=False)
    try:
        win.hide()
        app.processEvents()
        win.cfg.set("music_sing_enabled", True)
        win.refresh_pet_settings()
        assert not win._music_sing_timer.isActive(), "隐藏期间不应启动"

        win.show()
        app.processEvents()
        assert win._music_sing_timer.isActive(), "恢复显示后按开关状态启动"
    finally:
        win.close()
        app.processEvents()
