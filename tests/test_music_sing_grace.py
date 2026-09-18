# -*- coding: utf-8 -*-
"""后台音乐检测 → 唱歌动画的宽限期行为测试。

回归点：``is_music_playing`` 看的是音频峰值，歌曲的前奏/间奏/轻声段会让
峰值瞬时跌破阈值。此前一检测到静音就退出唱歌，表现为「唱着唱着主动退出」。
现要求：只有**持续静音**超过宽限期才真的退出。
"""
from __future__ import annotations

from pet import music_detect, window_alerts


class _FakeCfg:
    def __init__(self, grace=None):
        self._grace = grace

    def get(self, key, default=None):
        if key == "music_sing_grace_seconds":
            return self._grace if self._grace is not None else default
        return default


class _FakeHost:
    """最小宿主：只提供 check_music_sing 会碰到的属性。"""

    _music_sing_enabled = True
    _dragging = False

    def __init__(self, grace=None):
        self.cfg = _FakeCfg(grace)
        self._music_sing_active = False
        self.switches: list[str] = []

    def isVisible(self):
        return True

    def _is_one_shot_playing(self):
        return False

    def _switch(self, name):
        self.switches.append(name)


def test_starts_singing_when_music_plays(monkeypatch):
    monkeypatch.setattr(music_detect, "is_music_playing", lambda: True)
    host = _FakeHost()
    window_alerts.check_music_sing(host)

    from pet.window import SING_ANIM

    assert host._music_sing_active is True
    assert host.switches == [SING_ANIM]


def test_brief_silence_does_not_stop_singing(monkeypatch):
    """瞬时静音（间奏/轻声段）不应打断唱歌。"""
    state = {"playing": True}
    monkeypatch.setattr(music_detect, "is_music_playing", lambda: state["playing"])
    host = _FakeHost()
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is True

    state["playing"] = False
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is True, "瞬时静音不该退出唱歌"
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is True


def test_silence_timer_resets_when_music_resumes(monkeypatch):
    """静音后又恢复出声：静音计时清零，仍在唱歌。"""
    state = {"playing": True}
    monkeypatch.setattr(music_detect, "is_music_playing", lambda: state["playing"])
    host = _FakeHost()
    window_alerts.check_music_sing(host)

    state["playing"] = False
    window_alerts.check_music_sing(host)
    assert getattr(host, "_music_sing_silent_since", None) is not None

    state["playing"] = True
    window_alerts.check_music_sing(host)
    assert host._music_sing_silent_since is None
    assert host._music_sing_active is True


def test_long_silence_does_stop_singing(monkeypatch):
    """持续静音超过宽限期后才退出。"""
    state = {"playing": True}
    monkeypatch.setattr(music_detect, "is_music_playing", lambda: state["playing"])
    host = _FakeHost(grace=1.0)
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is True

    state["playing"] = False
    window_alerts.check_music_sing(host)          # 起算静音
    # 把起点往前拨，模拟已静音超过宽限期（不依赖真实等待）
    host._music_sing_silent_since -= 5.0
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is False


def test_grace_seconds_defaults_and_clamps():
    assert (
        window_alerts.music_sing_grace_seconds(_FakeHost())
        == window_alerts.MUSIC_SING_GRACE_SECONDS
    )
    # 非法值回退默认
    assert (
        window_alerts.music_sing_grace_seconds(_FakeHost(grace="abc"))
        == window_alerts.MUSIC_SING_GRACE_SECONDS
    )
    # 低于下限会被抬到 1 秒，避免退化成"瞬时静音即退出"
    assert window_alerts.music_sing_grace_seconds(_FakeHost(grace=0.1)) == 1.0
    # 正常值原样返回
    assert window_alerts.music_sing_grace_seconds(_FakeHost(grace=8)) == 8.0


def test_disabled_switch_stops_immediately(monkeypatch):
    """关掉开关要立刻停，不受宽限期影响。"""
    monkeypatch.setattr(music_detect, "is_music_playing", lambda: True)
    host = _FakeHost()
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is True

    host._music_sing_enabled = False
    window_alerts.check_music_sing(host)
    assert host._music_sing_active is False
