# -*- coding: utf-8 -*-
"""SMTC 会话选取与进度读取的离线单元测试。

背景（2026-09-21 本机实测）：同时存在多个媒体会话时，旧实现"没有正在播放的
会话就退回 sessions[0]"。实测本机 sessions[0] 是 Chrome（浏览器标签页），而
用户真正在听的是 cloudmusic.exe，于是桌宠会显示浏览器里那首歌。

另一条实测事实（本文件用来钉死它）：网易云音乐（cloudmusic.exe）**完全不上报
播放进度**——position / end_time 恒为 0.0、last_updated_time 是 1601-01-01，
播放中、暂停、切歌、快进四种状态采样结果一致。因此 `Playback.position` 必须
是 None，由调用方回退到本地时钟推算。

全部用例都不联网、不碰真实 WinRT：`_import_winrt` 被替换成假实现。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from pet import now_playing

# MediaPlaybackStatus: 4 = PLAYING, 5 = Paused
PLAYING = 4
PAUSED = 5

_EPOCH_ZERO = datetime(1601, 1, 1, tzinfo=timezone.utc)


class _Sec:
    """timeline 的时间字段（真实实现是 datetime.timedelta）。"""

    def __init__(self, value: float):
        self._value = value

    def total_seconds(self) -> float:
        return self._value


class _Timeline:
    def __init__(self, position: float, end: float, stamp):
        self.position = _Sec(position)
        self.end_time = _Sec(end)
        self.last_updated_time = stamp


class _Info:
    def __init__(self, status: int):
        self.playback_status = status
        self.controls = type(
            "C", (), {"is_next_enabled": True, "is_previous_enabled": True},
        )()


class _Props:
    def __init__(self, title: str, artist: str):
        self.title = title
        self.artist = artist
        self.album_title = ""


class _Session:
    def __init__(
        self, app_id: str, *, status: int = PLAYING, title: str = "t",
        artist: str = "a", position: float = 0.0, end: float = 0.0,
        stamp=_EPOCH_ZERO,
    ):
        self.source_app_user_model_id = app_id
        self._status = status
        self._title = title
        self._artist = artist
        self._position = position
        self._end = end
        self._stamp = stamp

    def get_playback_info(self):
        return _Info(self._status)

    def get_timeline_properties(self):
        return _Timeline(self._position, self._end, self._stamp)

    async def try_get_media_properties_async(self):
        return _Props(self._title, self._artist)


class _Manager:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return list(self._sessions)


def _install(monkeypatch, sessions):
    """把假 manager 装成 winrt 的等价物，并返回 manager 实例。"""

    class _Cls:
        @staticmethod
        async def request_async():
            return _Manager(sessions)

    monkeypatch.setattr(now_playing, "_import_winrt", lambda: _Cls)
    return _Manager(sessions)


def _pick(sessions, tracked=None):
    return asyncio.run(now_playing._pick_playing_session(_Manager(sessions), tracked))


def _read(monkeypatch, sessions, tracked=None):
    _install(monkeypatch, sessions)
    return asyncio.run(now_playing._read_async(tracked))


# ------------------------------------------------------------ 进度读取


def test_session_without_timeline_yields_none_position(monkeypatch):
    """网易云实测形状：position/end 恒 0、时间戳是 1601 —— 必须判为"无进度"。"""
    netease = _Session("cloudmusic.exe", position=0.0, end=0.0, stamp=_EPOCH_ZERO)
    playback = _read(monkeypatch, [netease])

    assert playback is not None
    assert playback.position is None, "无时间轴时必须回退本地时钟，而不是报 0 秒"
    assert playback.track.duration == 0.0
    assert playback.track.playing is True


def test_session_with_timeline_reports_real_position(monkeypatch):
    """有真实时间轴（QQ 音乐 / Chrome）：直接用真实值。"""
    chrome = _Session("Chrome", position=42.0, end=200.0, stamp=None)
    playback = _read(monkeypatch, [chrome])

    assert playback is not None
    assert playback.position == 42.0
    assert playback.track.duration == 200.0


def test_playback_carries_source_app_id(monkeypatch):
    """采样结果必须带上是哪个播放器，供下一次选取"粘住"当前跟踪对象。"""
    netease = _Session("cloudmusic.exe", title="发如雪", artist="周杰伦")
    playback = _read(monkeypatch, [netease])

    assert playback is not None
    assert playback.app_id == "cloudmusic.exe"


def test_read_returns_none_without_sessions(monkeypatch):
    assert _read(monkeypatch, []) is None


# ------------------------------------------------------------ 会话选取


def test_pick_prefers_playing_over_paused():
    paused = _Session("cloudmusic.exe", status=PAUSED, title="发如雪")
    playing = _Session("Chrome", status=PLAYING, title="正在播的视频")
    assert _pick([paused, playing]).source_app_user_model_id == "Chrome"


def test_pick_keeps_tracked_session_while_it_is_playing():
    """多个会话同时在播时，不能被别的会话抢走。"""
    tracked = _Session("cloudmusic.exe", status=PLAYING, title="发如雪")
    other = _Session("Chrome", status=PLAYING, title="视频")
    picked = _pick([other, tracked], tracked="cloudmusic.exe")
    assert picked.source_app_user_model_id == "cloudmusic.exe"


def test_pick_keeps_tracked_paused_session_instead_of_first():
    """实测缺陷回归：全部暂停时必须留在当前跟踪对象上。

    旧实现退回 sessions[0]，实测 sessions[0] 是 Chrome，于是桌宠会跳到浏览器
    里那首歌上（用户的网易云只是暂停了一下）。
    """
    chrome_paused = _Session("Chrome", status=PAUSED, title="浏览器里的歌")
    netease_paused = _Session("cloudmusic.exe", status=PAUSED, title="发如雪")
    picked = _pick([chrome_paused, netease_paused], tracked="cloudmusic.exe")
    assert picked.source_app_user_model_id == "cloudmusic.exe"


def test_pick_switches_when_tracked_is_paused_and_another_plays():
    """当前跟踪对象暂停、另一个真的在播 —— 应该让位给正在播的那个。"""
    tracked = _Session("cloudmusic.exe", status=PAUSED, title="发如雪")
    playing = _Session("Chrome", status=PLAYING, title="视频")
    picked = _pick([tracked, playing], tracked="cloudmusic.exe")
    assert picked.source_app_user_model_id == "Chrome"


def test_pick_falls_back_to_first_when_nothing_tracked_or_playing():
    """没有任何线索时保持旧兼容行为（第一个）。"""
    a = _Session("cloudmusic.exe", status=PAUSED, title="发如雪")
    b = _Session("Chrome", status=PAUSED, title="视频")
    assert _pick([a, b]).source_app_user_model_id == "cloudmusic.exe"


def test_pick_ignores_stale_tracked_id():
    """跟踪对象已经退出：退回正常规则，不能返回 None。"""
    a = _Session("Chrome", status=PLAYING, title="视频")
    picked = _pick([a], tracked="cloudmusic.exe")
    assert picked is not None
    assert picked.source_app_user_model_id == "Chrome"


def test_pick_returns_none_without_sessions():
    assert _pick([]) is None


def test_get_now_playing_accepts_tracked_id(monkeypatch):
    """公开入口要能把跟踪对象透传下去（控制器每拍带上去）。"""
    tracked = _Session("cloudmusic.exe", status=PAUSED, title="发如雪")
    other = _Session("Chrome", status=PAUSED, title="视频")
    _install(monkeypatch, [other, tracked])
    monkeypatch.setattr(now_playing.sys, "platform", "win32")

    playback = now_playing.get_now_playing("cloudmusic.exe")
    assert playback is not None
    assert playback.app_id == "cloudmusic.exe"


@pytest.mark.parametrize("tracked", [None, "cloudmusic.exe"])
def test_read_async_accepts_optional_tracked_id(monkeypatch, tracked):
    _install(monkeypatch, [_Session("cloudmusic.exe")])
    assert asyncio.run(now_playing._read_async(tracked)) is not None
