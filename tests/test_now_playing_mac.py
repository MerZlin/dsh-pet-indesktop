# -*- coding: utf-8 -*-
"""macOS 分支：``now_playing`` / ``detect`` 经 ``mac_media`` 读「正在播放」与发命令。

替身只换操作系统边界——``mac_media.read_now_playing``（背后是 osascript 助手进程，
协议本身由 ``tests/test_mac_media.py`` 用真实子进程覆盖）与
``mac_media.send_command``（背后是 MediaRemote 的 ctypes 调用）。其余（会话否决规则、
播放位置外推、Playback 形状、探针缓存与唱歌门）全部走产品代码。
"""
from __future__ import annotations

import time

import pytest

from pet.music import detect as music_detect, mac_media, now_playing


def _state(**overrides) -> mac_media.MacNowPlaying:
    fields = dict(
        bundle_id="com.netease.163music",
        parent_bundle_id="",
        display_name="网易云音乐",
        title="晴天",
        artist="周杰伦",
        album="叶惠美",
        duration=269.0,
        elapsed=30.0,
        timestamp=None,
        rate=1.0,
        playing=True,
        media_type="",
    )
    fields.update(overrides)
    return mac_media.MacNowPlaying(**fields)


@pytest.fixture
def mac(monkeypatch):
    """切到 darwin，并把「正在播放」读数与命令通道换成可控替身。"""
    monkeypatch.setattr(now_playing.sys, "platform", "darwin")
    box = {"state": _state(), "reads": [], "commands": [], "accept": True}

    def fake_read(max_age=None):
        box["reads"].append(max_age)
        return box["state"]

    def fake_send(command):
        box["commands"].append(command)
        return box["accept"]

    monkeypatch.setattr(mac_media, "read_now_playing", fake_read)
    monkeypatch.setattr(mac_media, "send_command", fake_send)
    now_playing.reset_session_probe()
    yield box
    now_playing.reset_session_probe()


# ------------------------------------------------------------------ 读曲目（歌词链路）


def test_get_now_playing_maps_mac_state_to_playback(mac):
    playback = now_playing.get_now_playing()

    assert playback is not None
    assert playback.track.title == "晴天"
    assert playback.track.artist == "周杰伦"
    assert playback.track.album == "叶惠美"
    assert playback.track.playing is True
    assert playback.track.duration == pytest.approx(269.0)
    assert playback.app_id == "com.netease.163music"


def test_position_is_extrapolated_from_the_mediaremote_timestamp(mac):
    mac["state"] = _state(elapsed=30.0, timestamp=time.time() - 10.0)
    playback = now_playing.get_now_playing()
    assert playback.position == pytest.approx(40.0, abs=1.0)


def test_position_is_none_without_a_timeline(mac):
    """不上报时间轴的播放器：position=None，控制器走本地计时 + 手动对齐（与 Windows 同口径）。"""
    mac["state"] = _state(duration=None, elapsed=None)
    playback = now_playing.get_now_playing()
    assert playback.position is None
    assert playback.track.duration == 0.0


def test_paused_state_is_reported_as_not_playing(mac):
    mac["state"] = _state(playing=False, elapsed=30.0, timestamp=time.time() - 100)
    playback = now_playing.get_now_playing()
    assert playback.track.playing is False
    assert playback.position == pytest.approx(30.0)


@pytest.mark.parametrize("state", [None, "untitled"])
def test_nothing_to_show_is_none(mac, state):
    mac["state"] = None if state is None else _state(title="", artist="")
    assert now_playing.get_now_playing() is None


def test_video_is_vetoed(mac):
    mac["state"] = _state(media_type="MRMediaRemoteMediaTypeVideo")
    assert now_playing.get_now_playing() is None


@pytest.mark.parametrize("bundle, parent", [
    ("com.google.Chrome", ""),
    ("com.apple.WebKit.GPU", "com.apple.Safari"),
    ("org.mozilla.firefox", ""),
    ("com.microsoft.edgemac", ""),
])
def test_browser_sessions_follow_the_browser_switch(mac, bundle, parent):
    """浏览器网页媒体：开关关时按"不是歌"处理，开着才进歌词链路（与 Windows 同规）。"""
    mac["state"] = _state(bundle_id=bundle, parent_bundle_id=parent)
    assert now_playing.get_now_playing(allow_browser=False) is None
    playback = now_playing.get_now_playing(allow_browser=True)
    assert playback is not None
    assert now_playing.is_browser_session(playback.app_id) is True


def test_native_mac_players_are_not_browsers():
    for bundle in ("com.netease.163music", "com.tencent.QQMusicMac", "com.kugou.mac.Music",
                   "com.soda.music", "com.apple.Music", "com.spotify.client"):
        assert now_playing.is_browser_session(bundle) is False, bundle


def test_foobar_window_title_source_is_not_consulted_on_mac(mac, monkeypatch):
    """foobar 窗口标题源走 Win32 窗口枚举：macOS 上不问它（也不扫进程表）。"""
    called: list = []
    monkeypatch.setattr(now_playing.foobar_playing, "read_track",
                        lambda fmt="": called.append(fmt))
    mac["state"] = None
    assert now_playing.get_now_playing(allow_foobar=True) is None
    assert called == []


def test_get_now_playing_never_raises(mac, monkeypatch):
    def boom(max_age=None):
        raise RuntimeError("helper exploded")

    monkeypatch.setattr(mac_media, "read_now_playing", boom)
    assert now_playing.get_now_playing() is None


# ------------------------------------------------------------------ 传输控制


def test_skip_and_toggle_send_mediaremote_commands(mac):
    assert now_playing.skip_track("next") is True
    assert now_playing.skip_track("previous") is True
    assert now_playing.toggle_play_pause() is True
    assert mac["commands"] == [
        mac_media.COMMAND_NEXT_TRACK,
        mac_media.COMMAND_PREVIOUS_TRACK,
        mac_media.COMMAND_TOGGLE_PLAY_PAUSE,
    ]


def test_controls_do_nothing_without_a_now_playing_app(mac):
    """没有任何「正在播放」应用：不发命令（与 Windows "没有会话 → False" 同口径）。"""
    mac["state"] = None
    assert now_playing.skip_track("next") is False
    assert now_playing.toggle_play_pause() is False
    assert mac["commands"] == []


def test_controls_report_rejected_commands(mac):
    mac["accept"] = False
    assert now_playing.skip_track("next") is False


def test_play_session_for_only_targets_the_named_player(mac):
    """「打开某播放器并播放」：只在它就是「正在播放」应用时发播放，不去恢复别的应用。"""
    mac["state"] = _state(bundle_id="com.apple.Music", playing=False)
    assert now_playing.play_session_for("com.netease.163music") is False
    assert mac["commands"] == []

    mac["state"] = _state(bundle_id="com.netease.163music", playing=False)
    assert now_playing.play_session_for("COM.NETEASE.163MUSIC") is True
    assert mac["commands"] == [mac_media.COMMAND_PLAY]
    assert mac["reads"][-1] == 0, "发命令前要读此刻的状态，不用复用窗口里的旧值"


def test_play_session_for_already_playing_sends_nothing(mac):
    mac["state"] = _state(bundle_id="com.netease.163music", playing=True)
    assert now_playing.play_session_for("com.netease.163music") is True
    assert mac["commands"] == []


def test_play_session_for_without_identity_is_false(mac):
    assert now_playing.play_session_for("") is False
    assert mac["commands"] == []


# ------------------------------------------------------------------ 唱歌门（探针 + detect）


def test_session_probe_facts_come_from_mediaremote_on_mac(mac):
    facts = now_playing.refresh_session_probe_once()
    assert facts == ((now_playing.MEDIA_TYPE_UNKNOWN, False, True),)

    mac["state"] = _state(media_type="MRMediaRemoteMediaTypeMusic", playing=False)
    assert now_playing.refresh_session_probe_once() == ((now_playing.MEDIA_TYPE_MUSIC, False, False),)

    mac["state"] = None
    assert now_playing.refresh_session_probe_once() == ()


def test_song_session_playing_reads_only_the_cache(mac):
    """主线程安全：判定只读探针缓存，调用线程自己一次 MediaRemote 都不读。"""
    now_playing.refresh_session_probe_once()
    reads_before = len(mac["reads"])

    assert now_playing.song_session_playing(allow_browser=False) is True
    assert len(mac["reads"]) == reads_before


def test_song_session_playing_applies_the_same_vetoes(mac):
    mac["state"] = _state(bundle_id="com.google.Chrome")
    now_playing.refresh_session_probe_once()
    assert now_playing.song_session_playing(allow_browser=False) is False
    assert now_playing.song_session_playing(allow_browser=True) is True

    mac["state"] = _state(media_type="MRMediaRemoteMediaTypeVideo")
    now_playing.refresh_session_probe_once()
    assert now_playing.song_session_playing(allow_browser=True) is False

    mac["state"] = _state(playing=False)
    now_playing.refresh_session_probe_once()
    assert now_playing.song_session_playing(allow_browser=True) is False


def test_song_session_playing_is_unknown_before_first_sample(mac):
    assert now_playing.song_session_playing() is None


def test_detect_on_mac_sings_only_for_a_playing_song(mac, monkeypatch):
    """macOS 没有 pycaw 峰值表：「在放歌」以系统「正在播放」状态为准。"""
    monkeypatch.setattr(music_detect.sys, "platform", "darwin")
    music_detect.set_browser_media_enabled(False)
    try:
        assert music_detect.is_music_playing() is False, "第一拍还没有探针数据：不唱"

        now_playing.refresh_session_probe_once()
        assert music_detect.is_music_playing() is True

        mac["state"] = _state(playing=False)
        now_playing.refresh_session_probe_once()
        assert music_detect.is_music_playing() is False

        mac["state"] = _state(bundle_id="com.google.Chrome")
        now_playing.refresh_session_probe_once()
        assert music_detect.is_music_playing() is False, "浏览器开关关：网页视频不唱"
        music_detect.set_browser_media_enabled(True)
        assert music_detect.is_music_playing() is True
    finally:
        music_detect.set_browser_media_enabled(False)


def test_media_session_supported_platforms(monkeypatch):
    for platform, expected in (("win32", True), ("darwin", True), ("linux", False)):
        monkeypatch.setattr(now_playing.sys, "platform", platform)
        assert now_playing.media_session_supported() is expected, platform
