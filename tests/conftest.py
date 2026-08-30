# -*- coding: utf-8 -*-
"""全局测试静音：跑测试时不许真实发声。

play_sound 的取证日志照常记录（测试仍可断言"调用了播放"），但
QSoundEffect/QMediaPlayer 的 play 被替换为空操作——测试套件在任何
机器上跑都不应该让喇叭出声。
"""

import pytest


@pytest.fixture(autouse=True)
def _mute_qt_audio(monkeypatch):
    try:
        from PySide6.QtMultimedia import QMediaPlayer, QSoundEffect
    except Exception:
        return
    monkeypatch.setattr(QSoundEffect, "play", lambda self: None)
    monkeypatch.setattr(QMediaPlayer, "play", lambda self: None)
