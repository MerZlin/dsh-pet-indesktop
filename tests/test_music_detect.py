# -*- coding: utf-8 -*-
"""后台音乐检测模块测试。"""

from pet import music_detect


def test_non_windows_returns_false(monkeypatch):
    monkeypatch.setattr(music_detect.sys, "platform", "linux")
    assert music_detect.is_music_playing() is False
