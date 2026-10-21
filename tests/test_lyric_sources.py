# -*- coding: utf-8 -*-
"""歌词来源接线：浏览器标题清洗 + foobar2000 补充源（issue #228 两件）。

本文件钉的是**接线与优先级**，规则本体的真值表在 ``tests/test_music_title.py``：

1. **SMTC 有在播会话时绝不问 foobar**（foobar 是补充源，不抢跟踪位）；
2. foobar 只在没有在播会话时接管，且返回的 ``app_id`` 不许覆盖 SMTC 的
   ``tracked_app_id``；
3. **只有浏览器媒体会话**才做标题清洗，原生播放器（SMTC 结构化字段）走原样；
4. 两个开关都从 cfg 每拍读，采样线程如实把它们带给 ``now_playing``。

全部用例不联网、不碰真实 WinRT / Win32：只替换 OS 边界（假 SMTC 会话管理器、
假 foobar 标题读取），产品代码全走真路径。
"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QObject

from pet.music import foobar as foobar_playing, now_playing, title as music_title

MUSIC = 1
PLAYING = 4
PAUSED = 5

MUSIC_TITLE = "夜曲"
MUSIC_ARTIST = "周杰伦"
BILI_TITLE = "【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili"
FOOBAR_WINDOW_TITLE = "周杰伦 - 晴天"


@pytest.fixture(autouse=True)
def _win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    foobar_playing.reset_pid_cache()


# ------------------------------------------------------------ 假 SMTC / 假 foobar


class _Sec:
    def __init__(self, value: float):
        self._value = value

    def total_seconds(self) -> float:
        return self._value


class _Timeline:
    def __init__(self, position: float, end: float):
        self.position = _Sec(position)
        self.end_time = _Sec(end)
        self.last_updated_time = None


class _Controls:
    is_next_enabled = True
    is_previous_enabled = True


class _Info:
    def __init__(self, status: int, media_type: int):
        self.playback_status = status
        self.playback_type = media_type
        self.controls = _Controls()


class _Props:
    def __init__(self, title: str, artist: str):
        self.title = title
        self.artist = artist
        self.album_title = ""


class _Session:
    def __init__(self, app_id: str, *, title="t", artist="a",
                 status: int = PLAYING, media_type: int = MUSIC):
        self.source_app_user_model_id = app_id
        self._title = title
        self._artist = artist
        self._status = status
        self._media_type = media_type

    def get_playback_info(self):
        return _Info(self._status, self._media_type)

    def get_timeline_properties(self):
        return _Timeline(0.0, 254.0)

    async def try_get_media_properties_async(self):
        return _Props(self._title, self._artist)


class _Manager:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return list(self._sessions)


def _install_winrt(monkeypatch, sessions) -> None:
    class _Cls:
        @staticmethod
        async def request_async():
            return _Manager(sessions)

    monkeypatch.setattr(now_playing, "_import_winrt", lambda: _Cls)


def _install_foobar(monkeypatch, parsed) -> list:
    """装一个假 foobar 标题读取；返回调用记录（用于断言"根本没问它"）。"""
    calls: list = []

    def _read(title_format=""):
        calls.append(title_format)
        return parsed

    monkeypatch.setattr(foobar_playing, "read_track", _read)
    return calls


# ---------------------------------------------------- 源优先级（SMTC 优先）


def test_foobar_supplies_track_when_no_smtc_session(monkeypatch):
    """没有 SMTC 会话：foobar 接管，按"在播 + 无进度"包装。

    进度必须是 ``None``（窗口标题里没有时间轴），否则控制器会拿 0 当真实位置，
    手动对齐入口也会被误判为"播放器会自己跟快进"而整组置灰。
    """
    _install_winrt(monkeypatch, [])
    _install_foobar(monkeypatch, ("晴天", "周杰伦"))

    playback = now_playing.get_now_playing(allow_foobar=True)

    assert playback is not None
    assert (playback.track.title, playback.track.artist) == ("晴天", "周杰伦")
    assert playback.track.playing is True
    assert playback.position is None
    assert playback.app_id == foobar_playing.FOOBAR_APP_ID
    assert now_playing.is_foobar_source(playback.app_id) is True
    assert now_playing.is_browser_session(playback.app_id) is False, (
        "foobar 源不得被当成浏览器会话，否则会被标题清洗规则再洗一遍"
    )


def test_foobar_is_not_consulted_when_switch_off(monkeypatch):
    """开关关（默认）：根本不问 foobar，行为与本次改动前完全一致。"""
    _install_winrt(monkeypatch, [])
    calls = _install_foobar(monkeypatch, ("晴天", "周杰伦"))

    assert now_playing.get_now_playing() is None
    assert calls == [], "开关关着还去读窗口标题 = 开关是摆设"


def test_playing_smtc_session_beats_foobar(monkeypatch):
    """SMTC 有在播会话：用它，且**一次都不问** foobar（不抢跟踪位）。"""
    _install_winrt(
        monkeypatch, [_Session("cloudmusic.exe", title=MUSIC_TITLE, artist=MUSIC_ARTIST)]
    )
    calls = _install_foobar(monkeypatch, ("晴天", "周杰伦"))

    playback = now_playing.get_now_playing(allow_foobar=True)

    assert playback is not None and playback.track.title == MUSIC_TITLE
    assert playback.app_id == "cloudmusic.exe"
    assert calls == [], "SMTC 在播时读 foobar 窗口标题 = 让补充源抢了主源"


def test_foobar_takes_over_when_smtc_session_is_paused(monkeypatch):
    """SMTC 只有暂停会话：foobar 在播时接管（暂停的会话不算"在播"）。

    否则用户暂停网易云、切到 foobar 播放时，歌词会一直挂在暂停的那首上。
    """
    _install_winrt(
        monkeypatch,
        [_Session("cloudmusic.exe", title=MUSIC_TITLE, artist=MUSIC_ARTIST, status=PAUSED)],
    )
    _install_foobar(monkeypatch, ("晴天", "周杰伦"))

    playback = now_playing.get_now_playing(allow_foobar=True)

    assert playback is not None
    assert playback.app_id == foobar_playing.FOOBAR_APP_ID
    assert playback.track.title == "晴天"


def test_paused_smtc_session_survives_when_foobar_has_nothing(monkeypatch):
    """SMTC 暂停 + foobar 读不到曲目：回到暂停的那首，不得整条链路空掉。"""
    _install_winrt(
        monkeypatch,
        [_Session("cloudmusic.exe", title=MUSIC_TITLE, artist=MUSIC_ARTIST, status=PAUSED)],
    )
    _install_foobar(monkeypatch, None)

    playback = now_playing.get_now_playing(allow_foobar=True)

    assert playback is not None
    assert playback.app_id == "cloudmusic.exe"
    assert playback.track.playing is False


def test_foobar_sampling_survives_missing_winrt(monkeypatch):
    """没装 winrt 时 SMTC 无从查起，foobar 的窗口标题源仍要能用。"""
    monkeypatch.setattr(now_playing, "_import_winrt", lambda: None)
    _install_foobar(monkeypatch, ("晴天", "周杰伦"))

    playback = now_playing.get_now_playing(allow_foobar=True)

    assert playback is not None and playback.track.title == "晴天"
    assert now_playing.get_now_playing() is None


def test_read_track_prefers_longest_window_title(monkeypatch):
    """窗口标题里挑最长的一条：同一进程的皮肤/独立窗口标题通常更短。"""
    monkeypatch.setattr(foobar_playing, "foobar_pids", lambda **kw: (4242,))
    monkeypatch.setattr(
        foobar_playing,
        "_window_titles_for_pids",
        lambda pids: ["foobar2000", "周杰伦 - 晴天"],
    )
    assert foobar_playing.read_track("") == ("周杰伦 - 晴天", "周杰伦 - 晴天")


def test_read_track_returns_none_when_process_is_not_running(monkeypatch):
    """进程不在就不枚举窗口（开销最低的那条短路）。"""
    monkeypatch.setattr(foobar_playing, "foobar_pids", lambda **kw: ())
    monkeypatch.setattr(
        foobar_playing,
        "_window_titles_for_pids",
        lambda pids: pytest.fail("进程不在时不该枚举窗口"),
    )
    assert foobar_playing.read_track("") is None


# ------------------------------------------------------- 控制器：跟踪位与取词词


class _SongWindow(QObject):
    _alert_current = None
    _sticky_bubble_active = False
    _speech_bubble = None

    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = dict(cfg or {})
        self.shown: list[tuple[str, str]] = []

    def show_bubble(self, text, duration_ms=3200, subtitle=None, **kw):
        self.shown.append((subtitle or "", text))

    def hold_bubble(self, seconds=0.0):
        pass

    def isVisible(self):
        return True


def _controller(monkeypatch, cfg=None):
    from pet.music.lyric_controller import MusicLyricController

    win = _SongWindow(cfg)
    ctrl = MusicLyricController(win)
    fetched: list[tuple] = []
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **kw: fetched.append(a))
    return win, ctrl, fetched


def _playback(app_id: str, *, title="t", artist="a"):
    from pet.music.now_playing import Playback, Track

    return Playback(
        track=Track(title=title, artist=artist, playing=True),
        position=None,
        updated_at=0.0,
        app_id=app_id,
    )


def test_foobar_playback_does_not_take_the_smtc_tracking_slot(monkeypatch):
    """foobar 源不得覆盖 ``_tracked_app_id``；SMTC 源照常覆盖。"""
    _win, ctrl, _fetched = _controller(monkeypatch)
    ctrl._tracked_app_id = "cloudmusic.exe"

    ctrl._on_playback_ready(_playback(foobar_playing.FOOBAR_APP_ID))
    assert ctrl._tracked_app_id == "cloudmusic.exe", "foobar 抢了 SMTC 的跟踪位"

    ctrl._on_playback_ready(_playback("QQMusic.exe"))
    assert ctrl._tracked_app_id == "QQMusic.exe", "SMTC 源必须照常更新跟踪位"


def _wait_until(predicate, *, timeout=5.0, interval=0.02):
    """宽预算轮询：CI 慢 runner 是本地数倍慢，禁止固定 sleep 赌时序。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_sample_loop_passes_foobar_settings_from_config(monkeypatch):
    """采样线程必须把两个 foobar 键带给 now_playing（否则开关是摆设）。"""
    calls: list[dict] = []

    def fake_get_now_playing(tracked_app_id=None, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(now_playing, "get_now_playing", fake_get_now_playing)
    _win, ctrl, _fetched = _controller(
        monkeypatch,
        {
            "foobar_lyrics_enabled": True,
            "foobar_title_format": "%artist% - %title%",
        },
    )
    try:
        ctrl.sync_enabled(True)
        assert _wait_until(lambda: calls), "开启后应采样"
        assert calls[0]["allow_foobar"] is True
        assert calls[0]["foobar_title_format"] == "%artist% - %title%"
    finally:
        ctrl.sync_enabled(False)


def test_browser_title_is_cleaned_into_the_fetch_query(monkeypatch):
    """浏览器会话：取词用清洗后的查询词，**气泡标题仍是原始网页标题**。"""
    win, ctrl, fetched = _controller(monkeypatch, {"music_browser_media_enabled": True})

    ctrl._on_playback_ready(_playback("msedge", title=BILI_TITLE, artist=""))

    # 取词在真线程（``music-lyric-fetch``）里跑，断言必须等它落地而不是立刻读。
    assert _wait_until(lambda: fetched), "浏览器会话开着开关就该取词"
    _key, query_title, query_artist = fetched[0]
    assert (query_title, query_artist) == ("晴天", "周杰伦")
    assert any(BILI_TITLE in sub or BILI_TITLE in text for sub, text in win.shown), (
        f"气泡标题应保持原始网页标题，实际 {win.shown}"
    )


def test_native_player_title_is_never_cleaned(monkeypatch):
    """原生播放器（SMTC 结构化字段）走原样——清洗只会误伤。"""
    _win, ctrl, fetched = _controller(monkeypatch, {"music_browser_media_enabled": True})

    ctrl._on_playback_ready(
        _playback("cloudmusic.exe", title=BILI_TITLE, artist=MUSIC_ARTIST)
    )

    assert _wait_until(lambda: fetched), "原生播放器同样该取词（只是不清洗）"
    _key, query_title, query_artist = fetched[0]
    assert (query_title, query_artist) == (BILI_TITLE, MUSIC_ARTIST)


def test_browser_title_is_not_cleaned_when_switch_off(monkeypatch):
    """开关关：连清洗都不做（虽然浏览器会话本就被否决，门要单独成立）。"""
    _win, ctrl, _fetched = _controller(monkeypatch, {"music_browser_media_enabled": False})

    got = ctrl._lyric_query(BILI_TITLE, "", _playback("msedge", title=BILI_TITLE))

    assert got == (BILI_TITLE, "")


def test_lyric_query_never_raises_on_a_foreign_playback(monkeypatch):
    """采样替身/异常对象进到取词路径：清洗门必须兜住，不能炸掉主线程。"""
    _win, ctrl, _fetched = _controller(monkeypatch, {"music_browser_media_enabled": True})
    assert ctrl._lyric_query("晴天", "周杰伦", object()) == ("晴天", "周杰伦")


def test_foobar_config_readers_tolerate_dirty_values(monkeypatch):
    """脏 cfg（非布尔/非字符串）：一律按关与"自动"处理。"""
    _win, ctrl, _fetched = _controller(monkeypatch, {})
    assert ctrl._foobar_lyrics_enabled() is False
    assert ctrl._foobar_title_format() == ""

    class _Boom:
        @property
        def cfg(self):
            raise RuntimeError("cfg 不可用")

    ctrl.win = _Boom()
    assert ctrl._foobar_lyrics_enabled() is False
    assert ctrl._foobar_title_format() == ""


# ------------------------------------------------------------------- 配置键


def test_foobar_config_keys_default_off_and_round_trip(tmp_path):
    from pet.config import Config

    cfg = Config(tmp_path)
    assert cfg.get("foobar_lyrics_enabled") is False, "窗口标题源默认必须关"
    assert cfg.get("foobar_title_format") == ""

    cfg.set("foobar_lyrics_enabled", True)
    cfg.set("foobar_title_format", "  %artist% - %title%  ")
    cfg.save()

    again = Config(tmp_path)
    assert again.get("foobar_lyrics_enabled") is True
    assert again.get("foobar_title_format") == "%artist% - %title%", "存储时已 strip"


def test_dirty_foobar_config_is_normalized(tmp_path):
    """手改脏值：字符串 "false" 不得被 bool() 判真；超长格式串要截断。"""
    import json

    from pet import config as config_mod
    from pet.config import Config

    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 4,
                "foobar_lyrics_enabled": "false",
                "foobar_title_format": "x" * 500,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = Config(base=tmp_path)

    assert cfg.data["foobar_lyrics_enabled"] is False, "'false' 不得被 bool() 误开"
    assert len(cfg.data["foobar_title_format"]) == music_title.FOOBAR_FORMAT_MAX_CHARS

    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, "foobar_title_format": ["not", "a", "string"]}),
        encoding="utf-8",
    )
    assert Config(base=tmp_path).data["foobar_title_format"] == ""
