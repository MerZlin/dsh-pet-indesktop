# -*- coding: utf-8 -*-
"""SMTC 媒体类型（音乐 / 视频）过滤的行为测试。

工单背景（2026-10「SMTC 视频被当歌曲」）：``now_playing`` 只读
title / artist / album_title，只要有标题且 ``playback_status == PLAYING``
就当歌曲，于是 Edge 播放 B 站视频时桌宠会显示「我在唱《<视频标题>》」、去
歌词接口取词、并开唱歌动画（唱歌动画只看系统音量峰值，视频自然也算）。

本文件钉住修复后的口径（两条腿）：**A. 明确 VIDEO(2) 的会话不进唱歌链路**；
**B. 关掉「浏览器媒体会话参与歌词」时（默认关）浏览器会话同样不进**。
Unknown 照常放行（第三方播放器不上报类型，一刀切会误伤）；多会话同播优先
音乐，其次 Unknown，最后才视频。四处接口约定（实现位置可自选，行为必须如此）：

1. 采样层 ``now_playing.get_now_playing()``：唯一在播的会话是视频（或开关关时
   的浏览器会话）时，不得把它当歌曲返回（返回 ``None``，与"无标题/无会话"
   同一口径）；同播时视频不得压住后来开始播放的音乐会话——**包括视频先被跟踪
   的情形**。
2. 歌词链路 ``MusicLyricController``：被否决的会话不产「我在唱《…》」、不取词；
   采样线程要把开关（cfg）如实带给 ``now_playing``。
3. 唱歌动画门 ``music_detect.is_music_playing``：当前**在播**的会话被否决时
   音量峰值不得触发唱歌动画。判定由 ``now_playing`` 的常驻探针在非主线程产出，
   门只读缓存（主线程不许同步查 SMTC）；没有判定数据、没有会话、或只有暂停的
   会话时一律放行（维持旧行为，不因门故障或"挂着暂停页签"误伤）。
4. 浏览器否决表**不是白名单**：表外应用（VLC/PotPlayer/LX Music…）退回旧行为。

真实的 SMTC 枚举是 ``Windows.Media.MediaPlaybackType``：UNKNOWN=0 /
MUSIC=1 / VIDEO=2 / IMAGE=3。这里只用整数、不引用枚举类：本机实测（2026-10，
winrt-Windows.Media.Control 3.2.1）读 ``playback_type`` 时若未安装
``winrt-Windows.Media``（该枚举的宿主包）会抛
``AttributeError: module 'winrt.windows.media' has no attribute
'MediaPlaybackType'``，所以"读不到就按 Unknown 处理"是底线，见
``test_missing_media_type_degrades_to_unknown``。

全部用例不联网、不碰真实 WinRT / COM 音频设备：只替换 OS 边界（假 SMTC 会话
管理器 + 假音频峰值表），产品代码全走真路径。
"""
from __future__ import annotations

import asyncio
import sys
import time

import pytest
from PySide6.QtCore import QObject

from pet import window_alerts
from pet.music import detect as music_detect, now_playing

# import 期有平台分支，必须在下面 _win32 夹具改 sys.platform 之前完成导入。
from pet.window import SING_ANIM

# Windows.Media.MediaPlaybackType
UNKNOWN = 0
MUSIC = 1
VIDEO = 2

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
PLAYING = 4
PAUSED = 5

# 标题形状照抄 2026-10 本机实测的 Edge 会话（B 站视频页标题 + 空 artist）。
VIDEO_TITLE = "什么？deepseek v4.1，kimi k3 免费了？-某UP主-哔哩哔哩视频"
MUSIC_TITLE = "夜曲"
MUSIC_ARTIST = "周杰伦"


@pytest.fixture(autouse=True)
def _win32(monkeypatch):
    """SMTC 采样与音频峰值检测都只在 Windows 分支生效，非 Windows 上会短路。"""
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture(autouse=True)
def _clean_session_probe():
    """每个用例前后清掉探针缓存与推送的浏览器开关，避免跨用例串味。"""
    now_playing.reset_session_probe()
    music_detect.set_browser_media_enabled(False)
    yield
    now_playing.reset_session_probe()
    music_detect.set_browser_media_enabled(False)


# ------------------------------------------------------------ 假 SMTC 会话


class _Sec:
    """timeline 的时间字段（真实实现是 datetime.timedelta）。"""

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
    def __init__(self, title: str, artist: str, media_type: int):
        self.title = title
        self.artist = artist
        self.album_title = ""
        self.playback_type = media_type


class _InfoNoType:
    """``playback_type`` 读不到时的形状（枚举宿主包缺失，见模块 docstring）。"""

    def __init__(self, status: int):
        self.playback_status = status
        self.controls = _Controls()

    @property
    def playback_type(self):
        raise AttributeError(
            "module 'winrt.windows.media' has no attribute 'MediaPlaybackType'"
        )


class _PropsNoType:
    def __init__(self, title: str, artist: str):
        self.title = title
        self.artist = artist
        self.album_title = ""

    @property
    def playback_type(self):
        raise AttributeError(
            "module 'winrt.windows.media' has no attribute 'MediaPlaybackType'"
        )


class _Session:
    """一个 SMTC 会话。``playback_type`` 同时挂在 PlaybackInfo 与
    MediaProperties 上——真实 API 两处都有（已核对 winrt 的 .pyi）。"""

    def __init__(
        self,
        app_id: str,
        media_type: int,
        *,
        title: str = "t",
        artist: str = "a",
        status: int = PLAYING,
        position: float = 0.0,
        end: float = 0.0,
        with_media_type: bool = True,
    ):
        self.source_app_user_model_id = app_id
        self._media_type = media_type
        self._with_media_type = with_media_type
        self._title = title
        self._artist = artist
        self._status = status
        self._position = position
        self._end = end
        # 读取计数：WinRT 往返次数的回归断言用（见 test_session_read_count_is_not_inflated）
        self.playback_info_reads = 0

    def get_playback_info(self):
        self.playback_info_reads += 1
        if self._with_media_type:
            return _Info(self._status, self._media_type)
        return _InfoNoType(self._status)

    def get_timeline_properties(self):
        return _Timeline(self._position, self._end)

    async def try_get_media_properties_async(self):
        if self._with_media_type:
            return _Props(self._title, self._artist, self._media_type)
        return _PropsNoType(self._title, self._artist)


def _video_session(app_id: str = "MSEdge", **kw) -> _Session:
    kw.setdefault("title", VIDEO_TITLE)
    kw.setdefault("artist", "")
    kw.setdefault("end", 188.0)
    return _Session(app_id, VIDEO, **kw)


def _music_session(app_id: str = "cloudmusic.exe", **kw) -> _Session:
    kw.setdefault("title", MUSIC_TITLE)
    kw.setdefault("artist", MUSIC_ARTIST)
    kw.setdefault("end", 254.0)
    return _Session(app_id, MUSIC, **kw)


def _unknown_session(app_id: str = "LXMusic.exe", **kw) -> _Session:
    kw.setdefault("title", "没上报类型的歌")
    kw.setdefault("artist", "")
    return _Session(app_id, UNKNOWN, **kw)


def _browser_session(app_id: str = "MSEdge", **kw) -> _Session:
    """浏览器里的**视频**（实测形状）：类型仍报 MUSIC、元数据全空。

    这正是"只能按会话来源否决"的由来——类型与元数据都区分不出来。
    """
    kw.setdefault("title", VIDEO_TITLE)
    kw.setdefault("artist", "")
    kw.setdefault("end", 188.0)
    return _Session(app_id, MUSIC, **kw)


class _Manager:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return list(self._sessions)


def _install_winrt(monkeypatch, sessions) -> None:
    """把假 manager 装成 WinRT 的等价物（只替换 OS 边界）。"""

    class _Cls:
        @staticmethod
        async def request_async():
            return _Manager(sessions)

    monkeypatch.setattr(now_playing, "_import_winrt", lambda: _Cls)


# ------------------------------------------------------------ 1. 采样层


def test_video_session_is_not_reported_as_song(monkeypatch):
    """唯一在播的会话是视频：采样不得把它当歌曲交出去。"""
    _install_winrt(monkeypatch, [_video_session()])

    assert now_playing.get_now_playing() is None


def test_music_wins_over_video_when_both_play(monkeypatch):
    """视频与音乐同播：枚举顺序里视频在前（实测 sessions[0] 常是浏览器）
    也必须选音乐。"""
    _install_winrt(monkeypatch, [_video_session(), _music_session()])

    playback = now_playing.get_now_playing()

    assert playback is not None, "同播时音乐会话必须被选中"
    assert playback.track.title == MUSIC_TITLE


@pytest.mark.parametrize(
    "challenger", [MUSIC, UNKNOWN], ids=["music", "unknown"]
)
def test_tracked_video_does_not_hold_the_slot(monkeypatch, challenger):
    """视频先被跟踪时，后开始的音乐/未上报类型会话必须抢回跟踪位。

    这是工单里"同播行为取决于枚举顺序"的根因：``_pick_playing_session`` 的
    粘住规则（"跟踪中的还在播就返回它"）对视频会话同样生效，于是视频一旦
    先入选，音乐永远抢不回来。
    """
    _install_winrt(
        monkeypatch,
        [_video_session("MSEdge"), _Session("later.exe", challenger,
                                            title="稍后开始的歌", artist="")],
    )

    playback = now_playing.get_now_playing("MSEdge")

    assert playback is not None, "视频会话不能占着跟踪位挡住音乐"
    assert playback.track.title == "稍后开始的歌"


@pytest.mark.parametrize(
    "media_type", [UNKNOWN, MUSIC], ids=["unknown", "music"]
)
def test_non_video_session_still_counts_as_song(monkeypatch, media_type):
    """防误伤回归：Unknown（第三方播放器不上报类型）与音乐照常当歌曲。

    口径明确要求 Unknown **不挡**——用白名单式过滤会把 LX Music 这类播放器
    静默挡掉。
    """
    _install_winrt(
        monkeypatch, [_Session("player.exe", media_type, title="某首歌")]
    )

    playback = now_playing.get_now_playing()

    assert playback is not None
    assert playback.track.title == "某首歌"


def test_missing_media_type_degrades_to_unknown(monkeypatch):
    """读不到 ``playback_type`` 时按 Unknown 放行，不能整条采样挂掉。

    这不是假想：本机实测未安装 ``winrt-Windows.Media`` 时访问该属性会抛
    AttributeError（见模块 docstring），而 ``_read_async`` 的字段异常兜底是
    "整次采样返回 None"——若不单独兜住，桌宠会在这种环境里彻底不显示歌词。
    """
    _install_winrt(
        monkeypatch,
        [_Session("player.exe", UNKNOWN, title="没有类型的歌",
                  with_media_type=False)],
    )

    playback = now_playing.get_now_playing()

    assert playback is not None
    assert playback.track.title == "没有类型的歌"


# ------------------------------------------------------------ 2. 歌词链路


class _SongWindow(QObject):
    """最小窗口替身：只提供 MusicLyricController 会用的 seam。

    必须是 QObject——控制器构造时把它当 parent。
    """

    _alert_current = None
    _sticky_bubble_active = False
    _speech_bubble = None

    def __init__(self):
        super().__init__()
        self.cfg: dict = {}
        self.shown: list[tuple[str, str]] = []

    def show_bubble(self, text, duration_ms=3200, subtitle=None, **kw):
        self.shown.append((subtitle or "", text))

    def hold_bubble(self, seconds=0.0):
        pass

    def isVisible(self):
        return True


def _controller(monkeypatch):
    """真实控制器 + 被记录的取词线程入口（取词会联网，必须打桩）。"""
    from pet.music.lyric_controller import MusicLyricController

    win = _SongWindow()
    ctrl = MusicLyricController(win)
    fetched: list[tuple] = []
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **kw: fetched.append(a))
    return win, ctrl, fetched


def _captions(win) -> list[str]:
    return [text for _sub, text in win.shown] + [sub for sub, _text in win.shown]


def test_video_session_does_not_produce_singing_caption(monkeypatch):
    """视频会话：不显示「我在唱」、不取词。"""
    win, ctrl, fetched = _controller(monkeypatch)
    _install_winrt(monkeypatch, [_video_session()])

    ctrl._on_playback_ready(now_playing.get_now_playing())

    assert not any("我在唱" in line for line in _captions(win)), win.shown
    assert not ctrl._title_line
    assert fetched == [], "视频会话不该去歌词接口取词"


def test_music_caption_when_video_and_music_play_together(monkeypatch):
    """视频 + 音乐同播：气泡必须唱音乐那首。"""
    win, ctrl, _fetched = _controller(monkeypatch)
    _install_winrt(monkeypatch, [_video_session(), _music_session()])

    ctrl._on_playback_ready(now_playing.get_now_playing())

    assert ("", f"我在唱《{MUSIC_TITLE}》") in win.shown, win.shown


# ------------------------------------------------------------ 3. 唱歌动画门
#
# 门只读 ``now_playing`` 后台探针写下的判定缓存（主线程不许同步查 SMTC），
# 所以这些用例一律先用 ``_prime_verdict`` 喂一拍判定，再驱动 check_music_sing：
# 不 sleep、不赌时序。


class _LoudMeter:
    """系统正在出声：峰值远高于 MUSIC_PEAK_THRESHOLD。"""

    def GetPeakValue(self) -> float:
        return 0.5


class _SingHost:
    """最小宿主：``check_music_sing`` 会碰到的 seam。"""

    _music_sing_enabled = True
    _dragging = False

    def __init__(self, cfg: dict | None = None):
        self.cfg: dict = dict(cfg or {})
        self._music_sing_active = False
        self.switches: list[str] = []

    def isVisible(self):
        return True

    def _is_one_shot_playing(self):
        return False

    def _switch(self, name):
        self.switches.append(name)


def _loud_system(monkeypatch, sessions) -> None:
    """系统在出声 + 给定的 SMTC 会话在播（只替换 OS 边界）。"""
    _install_winrt(monkeypatch, sessions)
    monkeypatch.setattr(music_detect, "_get_meter", lambda: _LoudMeter())


def _prime_verdict() -> tuple:
    """同步喂一拍会话事实表，等价于探针跑过一轮。

    返回逐会话事实表 ``((媒体类型, 是否浏览器, 是否在播), ...)``。调的是探针
    自己的公开入口（不是测试专用后门），因此不会把实现钉死。门读的是缓存事实、
    开关在读取时才参与判定，所以喂一拍就够，不必等线程。
    """
    now_playing.reset_session_probe()
    return now_playing.refresh_session_probe_once()


def test_video_session_does_not_trigger_sing_animation(monkeypatch):
    """视频在播：即使系统在出声，也不能开唱歌动画。

    门必须落在 music_detect / window_alerts 这条链上——不依赖歌词控制器是否
    在运行（歌词功能关着时"我在唱"本来就不显示，动画更不该唱）。
    """
    _loud_system(monkeypatch, [_video_session()])
    _prime_verdict()
    host = _SingHost()

    window_alerts.check_music_sing(host)

    assert host._music_sing_active is False
    assert host.switches == []


@pytest.mark.parametrize(
    "factory", [_music_session, _unknown_session], ids=["music", "unknown"]
)
def test_non_video_session_still_triggers_sing_animation(monkeypatch, factory):
    """防误伤回归：音乐 / Unknown 会话在播时照常唱歌。"""
    _loud_system(monkeypatch, [factory()])
    _prime_verdict()
    host = _SingHost()

    window_alerts.check_music_sing(host)

    assert host._music_sing_active is True
    assert host.switches == [SING_ANIM]


def test_no_smtc_session_keeps_volume_trigger(monkeypatch):
    """没有任何 SMTC 会话（例如只在游戏里出声）：维持旧行为——只看峰值就唱歌。

    否决制只否决明确的视频与（开关关时的）浏览器会话；没有会话就无从否决，
    所以游戏/无会话的本地声音照常触发，不做一刀切误伤。
    """
    _loud_system(monkeypatch, [])
    _prime_verdict()
    host = _SingHost()

    window_alerts.check_music_sing(host)

    assert host._music_sing_active is True
    assert host.switches == [SING_ANIM]


def test_gate_allows_while_verdict_missing(monkeypatch):
    """判定还没产出（探针首拍之前）：按放行处理，不因门故障误伤。

    真实进程里是"刚开启功能的前一两拍"，此时维持旧行为（只看峰值）。
    """
    _loud_system(monkeypatch, [_video_session()])
    now_playing.reset_session_probe()  # 清空缓存且不喂数据
    host = _SingHost()

    window_alerts.check_music_sing(host)

    assert host._music_sing_active is True
    assert host.switches == [SING_ANIM]


def test_video_session_ends_singing_already_started(monkeypatch):
    """唱歌进行中切到视频：唱歌必须停下来，且不会重新开唱。

    不钉"立刻停"还是"走完 6s 宽限期"——两种实现都接受（宽限期是给"同一首歌
    里峰值瞬间跌破阈值"用的，视频会话属于确定的状态切换，走不走宽限期是产品
    取舍）。钉住的是：最终必须停，而且不能因为"还有声音"被重新拉起。
    """
    _loud_system(monkeypatch, [_video_session()])
    _prime_verdict()
    host = _SingHost()
    host._music_sing_active = True
    host._music_sing_silent_since = None

    window_alerts.check_music_sing(host)

    # 走宽限期的实现：把起点向前拨，等价于"已静音超过宽限期"，不依赖真实等待。
    silent_since = getattr(host, "_music_sing_silent_since", None)
    if silent_since is not None:
        host._music_sing_silent_since = silent_since - 10.0
        window_alerts.check_music_sing(host)

    assert host._music_sing_active is False
    assert host.switches == [], "视频会话不许重新开唱"


def test_paused_video_session_does_not_veto_volume_trigger(monkeypatch):
    """暂停的视频会话在场、但并没有在播：不否决（维持旧行为，照常唱歌）。

    否决判定要求"当前会话在播"：暂停的会话说明不了声音来自哪里（可能是游戏、
    或不上报 SMTC 的播放器）。常见的"后台挂着一个暂停的 B 站页签"就属于这种
    情况，按会话存在与否决会让桌宠对任何声音都不唱歌。
    """
    _loud_system(monkeypatch, [_video_session(status=PAUSED)])
    assert _prime_verdict() == ((VIDEO, True, False),)

    host = _SingHost()
    window_alerts.check_music_sing(host)

    assert host._music_sing_active is True
    assert host.switches == [SING_ANIM]


# ------------------------------------------------- 4. 浏览器会话开关（否决制）
#
# 为什么需要这个开关：探针实测（2026-10，本机）Chromium 族对网页**视频**恒报
# MUSIC(1)，元数据里 artist/album/subtitle 全空、也没有任何字段能与网页音乐
# 分开——类型过滤只能覆盖上报 VIDEO 的原生播放器。所以要挡住"对着 B 站视频
# 唱《视频标题》"，唯一可靠的判据是会话来源（app_id 是不是浏览器）。
#
# 默认关：不区分内容的浏览器会话不进唱歌链路。打开后恢复旧行为——网页版音乐
# 平台靠它才能显示歌词。表外应用（VLC/PotPlayer/LX Music/网易云…）不受影响。


def test_browser_session_is_not_a_song_when_switch_off(monkeypatch):
    """开关关（默认）：浏览器会话不当歌——采样层直接不返回它。"""
    _install_winrt(monkeypatch, [_browser_session()])

    assert now_playing.get_now_playing(allow_browser=False) is None


def test_browser_session_is_song_when_switch_on(monkeypatch):
    """开关开：恢复现状行为（浏览器会话照常当歌）。"""
    _install_winrt(monkeypatch, [_browser_session()])

    playback = now_playing.get_now_playing(allow_browser=True)

    assert playback is not None
    assert playback.app_id == "MSEdge"


@pytest.mark.parametrize(
    "app_id",
    ["Chrome", "chrome", "chrome.exe", "CHROME", " MSEdge ", "Firefox.exe",
     "brave.exe", "Vivaldi", "opera.exe", "Arc.exe"],
)
def test_browser_app_ids_are_recognized_regardless_of_shape(app_id):
    """app_id 形状不统一（大小写 / .exe / 空白），比较前必须归一。"""
    assert now_playing.is_browser_session(app_id) is True


@pytest.mark.parametrize(
    "app_id",
    ["cloudmusic.exe", "QQMusic.exe", "PotPlayerMini64.exe", "vlc.exe",
     "mpv.exe", "LXMusic.exe", "Microsoft.ZuneVideo", "MSEdgeWebView2.exe", ""],
)
def test_table_external_app_is_not_treated_as_browser(app_id):
    """否决表不是白名单：表外应用一律放行（含"名字像浏览器"的 WebView 壳）。

    这条是防误伤的核心——把某个播放器漏在表外只意味着退回旧行为，绝不会
    因为它没被登记就被静默挡掉。
    """
    assert now_playing.is_browser_session(app_id) is False


def test_browser_video_does_not_shadow_paused_music(monkeypatch):
    """浏览器视频在播 + 网易云只是暂停：开关关时选中网易云那条会话，而不是浏览器。

    这是 P1 的原始形态：类型分层先把唯一在播的浏览器会话选出来（它报 MUSIC，
    优先级高于暂停会话），再否决 → 整条链路空掉、歌词消失。正确口径是**在可接受
    候选里选**：候选只有网易云，于是停在它上面（暂停态；视频标题绝不出现）。
    """
    _install_winrt(
        monkeypatch,
        [_browser_session(), _music_session(status=PAUSED)],
    )

    playback = now_playing.get_now_playing(allow_browser=False)

    assert playback is not None, "候选里还有网易云，不该整条链路空掉"
    assert playback.app_id == "cloudmusic.exe"
    assert playback.track.playing is False


def test_switch_flip_takes_effect_without_waiting_for_probe(monkeypatch):
    """开关一改就生效：缓存的是"会话事实"，判定在读取时按当前开关算。

    否则用户改完设置要等下一拍采样（TTL 2s）才生效，且旧判定会在切换瞬间
    被当成新配置的结果。
    """
    _install_winrt(monkeypatch, [_browser_session()])
    assert now_playing.refresh_session_probe_once() == ((MUSIC, True, True),)

    assert now_playing.song_session_accepted(allow_browser=False) is False
    assert now_playing.song_session_accepted(allow_browser=True) is True


def test_browser_session_produces_no_caption_when_switch_off(monkeypatch):
    """歌词链路：开关关时浏览器会话不产「我在唱」、不取词。"""
    win, ctrl, fetched = _controller(monkeypatch)
    win.cfg["music_browser_media_enabled"] = False
    _install_winrt(monkeypatch, [_browser_session()])

    ctrl._on_playback_ready(
        now_playing.get_now_playing(allow_browser=ctrl._browser_media_enabled())
    )

    assert win.shown == [], win.shown
    assert not ctrl._title_line
    assert fetched == [], "浏览器会话不该去歌词接口取词"


def test_browser_session_shows_caption_when_switch_on(monkeypatch):
    """开关打开 = 恢复现状：网页版音乐平台靠这条才能显示歌词。

    代价同样钉在这里——浏览器里的视频也会照旧被当成歌（这正是默认关闭的原因）。
    """
    win, ctrl, _fetched = _controller(monkeypatch)
    win.cfg["music_browser_media_enabled"] = True
    _install_winrt(monkeypatch, [_browser_session()])

    ctrl._on_playback_ready(
        now_playing.get_now_playing(allow_browser=ctrl._browser_media_enabled())
    )

    assert ("", f"我在唱《{VIDEO_TITLE}》") in win.shown, win.shown


def _wait_until(predicate, *, timeout=5.0, interval=0.02):
    """宽预算轮询：CI 慢 runner 是本地数倍慢，禁止固定 sleep 赌时序。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_sample_loop_passes_switch_from_config(monkeypatch):
    """真实采样线程必须把开关带给 now_playing（否则开关是个摆设）。

    桩掉 now_playing.get_now_playing，只检查采样线程实际传了什么参数——这是
    "配置 → 采样"这段接线唯一会被漏掉的地方。
    """
    from pet.music.lyric_controller import MusicLyricController

    calls: list = []

    def fake_get_now_playing(tracked_app_id=None, **kwargs):
        calls.append(kwargs.get("allow_browser"))
        return None

    monkeypatch.setattr(now_playing, "get_now_playing", fake_get_now_playing)

    for enabled in (False, True):
        calls.clear()
        win = _SongWindow()
        win.cfg = {"music_browser_media_enabled": enabled}
        ctrl = MusicLyricController(win)
        try:
            ctrl.sync_enabled(True)
            assert _wait_until(lambda: calls), "开启后应采样"
            assert calls[0] is enabled, calls
        finally:
            ctrl.sync_enabled(False)


def test_browser_session_does_not_trigger_sing_when_switch_off(monkeypatch):
    """动画门：开关关时浏览器会话不触发唱歌（默认关，cfg 里没有这个键）。"""
    _loud_system(monkeypatch, [_browser_session()])
    _prime_verdict()
    host = _SingHost()  # cfg 为空 = 取默认值（关）

    window_alerts.check_music_sing(host)

    assert host._music_sing_active is False
    assert host.switches == []
    assert music_detect.browser_media_enabled() is False, "宿主应把默认值推给门"


def test_browser_session_triggers_sing_when_switch_on(monkeypatch):
    """动画门：开关打开后恢复现状行为（浏览器会话照常唱歌）。"""
    _loud_system(monkeypatch, [_browser_session()])
    _prime_verdict()
    host = _SingHost({"music_browser_media_enabled": True})

    window_alerts.check_music_sing(host)

    assert host._music_sing_active is True
    assert host.switches == [SING_ANIM]
    assert music_detect.browser_media_enabled() is True


def test_browser_session_ends_singing_already_started(monkeypatch):
    """唱歌进行中切到浏览器内容（开关关）：必须停，且不重开。

    与视频那条同形（否定规则只有一份实现），单独钉一遍是避免"浏览器分支只在
    冷启动路径验证过"——它走的是同一个 6s 静音宽限期出口。
    """
    _loud_system(monkeypatch, [_browser_session()])
    _prime_verdict()
    host = _SingHost()  # cfg 为空 = 浏览器开关默认关
    host._music_sing_active = True
    host._music_sing_silent_since = None

    window_alerts.check_music_sing(host)

    silent_since = getattr(host, "_music_sing_silent_since", None)
    if silent_since is not None:
        host._music_sing_silent_since = silent_since - 10.0
        window_alerts.check_music_sing(host)

    assert host._music_sing_active is False
    assert host.switches == [], "浏览器会话（开关关）不许重新开唱"


# ------------------------------------------------- 5. 6.1sol 审查修复（P1 / P2）
#
# 三条都是"先在可接受候选里选，再谈否决"的推论：被否决的会话必须在**选择阶段**
# 出局，而不是先选出赢家再否决——只否决赢家会让报 MUSIC 的浏览器压住同播的表外
# 音乐播放器，也让结果随枚举顺序摇摆。


def test_rejected_browser_does_not_shadow_unknown_type_player(monkeypatch):
    """P1：浏览器(MUSIC,在播) + LX Music(Unknown,在播)，开关关 → 选 LX Music。

    修前实测：两种枚举顺序都返回 ``None``（浏览器按类型分层胜出 → 被否决 →
    整条链路空掉），音乐明明在播却"没有歌"。
    """
    browser = _browser_session("MSEdge")
    lx = _Session("LXMusic.exe", UNKNOWN, title="LX 里的歌", artist="")

    for order in ([browser, lx], [lx, browser]):
        _install_winrt(monkeypatch, order)
        playback = now_playing.get_now_playing(allow_browser=False)
        assert playback is not None, f"枚举顺序 {[s.source_app_user_model_id for s in order]}"
        assert playback.app_id == "LXMusic.exe"
        assert playback.track.title == "LX 里的歌"


def test_rejected_browser_does_not_shadow_music_type_player(monkeypatch):
    """P1-b：两个都报 MUSIC（浏览器 + 原生播放器）时，结果不许随枚举顺序变。

    修前实测：浏览器在前 → ``None``；QQ 音乐在前 → 选中 QQ 音乐。
    """
    browser = _browser_session("MSEdge")
    qq = _music_session("QQMusic.exe")

    for order in ([browser, qq], [qq, browser]):
        _install_winrt(monkeypatch, order)
        playback = now_playing.get_now_playing(allow_browser=False)
        assert playback is not None, f"枚举顺序 {[s.source_app_user_model_id for s in order]}"
        assert playback.app_id == "QQMusic.exe"


def test_transport_control_keeps_head_semantics(monkeypatch):
    """P2：传输控制（暂停/切歌）不受唱歌链路的否决与分层影响。

    用户看视频时点「暂停」就该暂停那个视频。HEAD 语义 = 枚举里第一个在播的
    （tracked 在播时粘住 tracked）。修前实测：分层把 MUSIC 顶到前面，两种顺序
    都变成"控制网易云"。
    """
    vlc = _Session("vlc.exe", VIDEO, title="本地视频", artist="")
    netease = _music_session("cloudmusic.exe")

    _install_winrt(monkeypatch, [vlc, netease])
    picked = asyncio.run(now_playing._pick_playback_session())
    assert picked.source_app_user_model_id == "vlc.exe", "枚举里第一个在播的是 VLC"

    _install_winrt(monkeypatch, [netease, vlc])
    picked = asyncio.run(now_playing._pick_playback_session())
    assert picked.source_app_user_model_id == "cloudmusic.exe"

    # tracked 在播 → 粘住 tracked（HEAD 规则 1），哪怕它是视频
    _install_winrt(monkeypatch, [netease, vlc])
    picked = asyncio.run(now_playing._pick_playback_session("vlc.exe"))
    assert picked.source_app_user_model_id == "vlc.exe"


def test_singing_picker_ignores_tracked_rejected_session(monkeypatch):
    """被跟踪的是浏览器（开关关）时，唱歌链路要跳到可接受候选上。

    否则用户从"网易云在播"切到"浏览器视频在播、音乐暂停"后，跟踪位一直钉在
    浏览器上，音乐重新开始播也抢不回来。
    """
    _install_winrt(monkeypatch, [_browser_session("MSEdge"), _music_session()])

    playback = now_playing.get_now_playing("MSEdge", allow_browser=False)

    assert playback is not None
    assert playback.app_id == "cloudmusic.exe"


def test_session_read_count_is_not_inflated(monkeypatch):
    """P2：每个会话每次采样只读一次 ``PlaybackInfo``（HEAD 基线 2 次）。

    修前实测 5 次（选会话读状态、算类型排名再读、否决时又读）；同一份数据反复
    取是白开销，也说明类型判定散在多处。
    """
    session = _music_session()
    _install_winrt(monkeypatch, [session])

    now_playing.get_now_playing(allow_browser=False)

    assert session.playback_info_reads == 1
