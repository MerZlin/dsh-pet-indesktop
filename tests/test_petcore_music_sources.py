"""音乐识别判定层：多来源证据合并（纯函数，离线）。

判定表（写死在用例里就是契约）：SMTC 是主源，音频会话是"在不在出声"的裁判，
播放器窗口标题只在 SMTC 没有在播会话时兜底。任何"猜"都要有证据。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_sources_test_host"


def _host_package():
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


_host_package()
recognizer = importlib.import_module(f"{HOST_PKG}.recognizer")
now_playing = importlib.import_module(f"{HOST_PKG}.now_playing")
players_mod = importlib.import_module(f"{HOST_PKG}.players")
sampling = importlib.import_module(f"{HOST_PKG}.sampling")
audio_sessions = importlib.import_module(f"{HOST_PKG}.audio_sessions")
window_source = importlib.import_module(f"{HOST_PKG}.window_source")


# ---------------------------------------------------------------- 替身


def audio(exe: str, state: str = "active", *, muted: bool = False, peak: float = 0.4):
    return SimpleNamespace(pid=1, exe=exe, state=state, muted=muted, peak=peak)


def window(exe: str, title: str, pid: int = 1):
    return SimpleNamespace(pid=pid, exe=exe, title=title)


def sample(
    *,
    playback=None,
    audio_rows=(),
    windows=(),
    smtc_reason: str = "",
    smtc_error: bool = False,
    smtc_degraded: bool = False,
    audio_available: bool = True,
    window_available: bool = True,
):
    return SimpleNamespace(
        sessions=(),
        playback=playback,
        smtc_reason=smtc_reason,
        smtc_error=smtc_error,
        smtc_degraded=smtc_degraded,
        audio=tuple(audio_rows),
        audio_available=audio_available,
        windows=tuple(windows),
        window_available=window_available,
    )


def playback(
    title: str = "晴天",
    artist: str = "周杰伦",
    *,
    app_id: str = "cloudmusic.exe",
    playing: bool = True,
    position: float | None = None,
    duration: float = 0.0,
):
    return now_playing.Playback(
        title=title,
        artist=artist,
        app_id=app_id,
        position=position,
        duration=duration,
        playing=playing,
    )


def parse_ok(title, exe, fmt=""):
    return ("晴天", "周杰伦")


# ---------------------------------------------------------------- SMTC 主源


def test_smtc_playing_with_active_audio_is_confirmed() -> None:
    decision = recognizer.decide(
        sample(playback=playback(), audio_rows=[audio("cloudmusic.exe")])
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.source == recognizer.SOURCE_SMTC
    assert decision.confidence == recognizer.CONFIDENCE_CONFIRMED
    assert decision.track is not None and decision.track.title == "晴天"
    assert any("state=active" in line for line in decision.evidence)


def test_smtc_playing_but_audio_inactive_counts_as_paused() -> None:
    """播放器还挂着"在播"、音频会话已经 Inactive：按暂停处理（不唱、歌词冻结）。"""
    decision = recognizer.decide(
        sample(playback=playback(), audio_rows=[audio("cloudmusic.exe", "inactive")])
    )
    assert decision.status == recognizer.STATUS_PAUSED
    assert decision.track is not None  # 曲目/歌词要留着
    assert any("inactive" in reason for reason in decision.reasons)


def test_smtc_playing_but_process_muted_counts_as_paused() -> None:
    decision = recognizer.decide(
        sample(playback=playback(), audio_rows=[audio("cloudmusic.exe", muted=True)])
    )
    assert decision.status == recognizer.STATUS_PAUSED
    assert any("静音" in reason for reason in decision.reasons)


def test_missing_audio_session_falls_back_to_smtc_status() -> None:
    """蓝牙 / 独占模式 / 虚拟声卡：会话表里没有它，按 SMTC 判，不许误判暂停。"""
    decision = recognizer.decide(
        sample(playback=playback(), audio_rows=[audio("steam.exe", "inactive")])
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.confidence == recognizer.CONFIDENCE_ASSUMED
    assert any("不在音频会话表" in line for line in decision.evidence)


def test_smtc_paused_stays_paused_even_if_audio_is_active() -> None:
    """SMTC 说暂停就暂停：音频活跃可能是提示音/切换动画，不能反推成在播。"""
    decision = recognizer.decide(
        sample(playback=playback(playing=False), audio_rows=[audio("cloudmusic.exe")])
    )
    assert decision.status == recognizer.STATUS_PAUSED


def test_smtc_paused_ignores_the_same_players_silent_leftover_session() -> None:
    """同一台播放器暂停后 WASAPI 会话常仍挂 active，但**没有声音**（peak=0）：
    不能拿它把暂停翻成在播。"""
    decision = recognizer.decide(
        sample(
            playback=playback(playing=False),
            audio_rows=[audio("cloudmusic.exe", peak=0.0)],
            windows=[window("cloudmusic.exe", "晴天 - 周杰伦")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PAUSED
    assert any("同一台播放器" in reason for reason in decision.reasons)


def test_smtc_paused_yields_to_the_same_player_when_audio_is_audible() -> None:
    """SMTC 状态停在暂停、同一台播放器却真在出声（peak>0）：以窗口源为准。

    网易云的 SMTC 状态会落后于实际播放；只看 ``state`` 会把"在播"永久判成暂停，
    这正是"音乐识别不到"的一种现场。peak 是第三条证据，能分开两种"Active 残留"。
    """
    decision = recognizer.decide(
        sample(
            playback=playback(playing=False),
            audio_rows=[audio("cloudmusic.exe", peak=0.4)],
            windows=[window("cloudmusic.exe", "晴天 - 周杰伦")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.source == recognizer.SOURCE_WINDOW
    assert decision.track is not None and decision.track.title == "晴天"


def test_degraded_smtc_falls_back_to_the_window_source() -> None:
    """SMTC 读取超时（系统媒体服务卡死）：不许整拍判失败，窗口源能救就救。"""
    decision = recognizer.decide(
        sample(
            smtc_degraded=True,
            smtc_reason="读取系统媒体会话超时。",
            audio_rows=[audio("cloudmusic.exe", peak=0.4)],
            windows=[window("cloudmusic.exe", "晴天 - 周杰伦")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.source == recognizer.SOURCE_WINDOW
    assert any("超时" in line for line in (*decision.reasons, *decision.evidence))


def test_degraded_smtc_without_any_source_is_absent_not_error() -> None:
    """彻底没有证据时按"没在放"处理（歌词冻结、宽限后停），不是永久 ERROR。

    ``error`` 的语义是"这一拍读失败、保留上一拍"；SMTC 卡死是**持续**状态，
    按它处理就等于永远停在最后一首歌上。
    """
    decision = recognizer.decide(
        sample(smtc_degraded=True, smtc_reason="读取系统媒体会话超时。")
    )
    assert decision.status == recognizer.STATUS_ABSENT
    assert any("超时" in reason for reason in decision.reasons)


def test_smtc_paused_yields_to_another_player_that_is_audible() -> None:
    """挂着的客户端 + 另一台不注册 SMTC 的播放器在放：仍以窗口源为准。"""
    decision = recognizer.decide(
        sample(
            playback=playback(playing=False),
            audio_rows=[audio("cloudmusic.exe"), audio("foobar2000.exe")],
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.source == recognizer.SOURCE_WINDOW
    assert any("以窗口标题源为准" in reason for reason in decision.reasons)


def test_uwp_app_id_without_exe_name_falls_back_to_smtc() -> None:
    decision = recognizer.decide(
        sample(
            playback=playback(app_id="SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"),
            audio_rows=[audio("spotify.exe")],
        )
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.exe == ""
    assert decision.confidence == recognizer.CONFIDENCE_ASSUMED


def test_smtc_read_failure_is_error_not_absence() -> None:
    decision = recognizer.decide(
        sample(smtc_reason="读取系统媒体信息超时。", smtc_error=True)
    )
    assert decision.status == recognizer.STATUS_ERROR
    assert decision.readable is False
    assert decision.reasons == ("读取系统媒体信息超时。",)


# ---------------------------------------------------------------- 窗口标题兜底


def test_window_source_supplies_track_when_smtc_has_nothing() -> None:
    decision = recognizer.decide(
        sample(
            smtc_reason="系统里没有正在播放的媒体会话。",
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
            audio_rows=[audio("foobar2000.exe")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.source == recognizer.SOURCE_WINDOW
    assert decision.track is not None and decision.track.artist == "周杰伦"


def test_window_source_without_audio_evidence_is_not_playing() -> None:
    """开着播放器没在放（标题停在上一首）：没有音频会话就不认。"""
    decision = recognizer.decide(
        sample(
            smtc_reason="系统里没有正在播放的媒体会话。",
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
            audio_rows=[],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_ABSENT
    assert any("没有音频会话" in reason for reason in decision.reasons)


def test_window_source_with_inactive_audio_is_paused() -> None:
    decision = recognizer.decide(
        sample(
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
            audio_rows=[audio("foobar2000.exe", "inactive")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PAUSED
    assert decision.source == recognizer.SOURCE_WINDOW


def test_window_source_with_unparsed_title_sings_without_track() -> None:
    """认不出歌名可以只唱歌：track 为空，但状态是在播（不取词）。"""
    decision = recognizer.decide(
        sample(
            windows=[window("aimp.exe", "某个看不懂的标题")],
            audio_rows=[audio("aimp.exe")],
        ),
        parse_window=lambda *_args: None,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.track is None
    assert decision.source == recognizer.SOURCE_WINDOW


def test_window_source_falls_back_to_title_only_when_audio_metering_is_unavailable() -> None:
    """没有音频证据可用时（例如非 Windows 探针缺失）退回"按标题认"，置信度 assumed。"""
    decision = recognizer.decide(
        sample(
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
            audio_rows=[],
            audio_available=False,
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.confidence == recognizer.CONFIDENCE_ASSUMED


def test_unknown_process_audio_only_is_not_music() -> None:
    """游戏/语音/未知应用在出声不算音乐（对误检的收紧，写进判定表）。"""
    decision = recognizer.decide(
        sample(smtc_reason="系统里没有正在播放的媒体会话。", audio_rows=[audio("game.exe")])
    )
    assert decision.status == recognizer.STATUS_ABSENT
    assert "系统里没有正在播放的媒体会话。" in decision.reasons


def test_allowlist_applies_to_the_window_source_too() -> None:
    """识别范围对三个来源统一生效，被挡下要点名播放器。"""
    decision = recognizer.decide(
        sample(
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
            audio_rows=[audio("foobar2000.exe")],
        ),
        allowed_ids=frozenset({"cloudmusic"}),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_ABSENT
    assert any("不在识别范围" in reason for reason in decision.reasons)


# ---------------------------------------------------------------- 小工具


@pytest.mark.parametrize(
    ("app_id", "expected"),
    [
        ("cloudmusic.exe", "cloudmusic.exe"),
        ("Chrome", "chrome.exe"),
        ("MSEdge", "msedge.exe"),
        ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", ""),
        ("", ""),
    ],
)
def test_exe_name_normalization(app_id: str, expected: str) -> None:
    assert recognizer.exe_name(app_id) == expected


def test_track_key_and_label() -> None:
    track = recognizer.Track(title=" 晴天 ", artist="周杰伦")
    assert track.key() == ("晴天", "周杰伦")
    assert track.label() == "周杰伦 - 晴天"
    assert recognizer.Track(title="").label() == "未知曲目"


def test_recognition_targets_union_known_configured_and_extras() -> None:
    targets = players_mod.recognition_targets(
        {"netease": r"D:\CloudMusic\cloudmusic.exe", "x": r"D:\Other\MyPlayer.exe"},
        "汽水音乐, foo",
    )
    assert "cloudmusic.exe" in targets
    assert "myplayer.exe" in targets
    assert "foobar2000.exe" in targets  # 已知表
    assert "foo.exe" in targets  # 额外识别名补 .exe
    assert "汽水音乐.exe" in targets


def test_window_source_does_not_repeat_the_title_as_artist() -> None:
    """顺序不可知（无格式串）时只显示一次整条标题，不把同一串贴两遍。"""
    decision = recognizer.decide(
        sample(
            windows=[window("foobar2000.exe", "开不了口 - 周杰伦")],
            audio_rows=[audio("foobar2000.exe")],
        ),
        parse_window=lambda title, _exe, _fmt: (title, title),
    )
    assert decision.track is not None
    assert decision.track.title == "开不了口 - 周杰伦"
    assert decision.track.artist == ""
    assert decision.track.label() == "开不了口 - 周杰伦"


def test_paused_smtc_session_does_not_hide_another_playing_player() -> None:
    """SMTC 里挂着暂停会话、另一台不注册 SMTC 的播放器在出声：判定要认后者。

    「挂着的客户端 + foobar 在放」是最常见的使用姿势；旧逻辑在这种情况下窗口源
    白采一遍、诊断里证据齐全却判"暂停"，也没有任何理由说明。
    """
    decision = recognizer.decide(
        sample(
            playback=playback(title="上一首", playing=False),
            audio_rows=[audio("cloudmusic.exe", "inactive"), audio("foobar2000.exe")],
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PLAYING
    assert decision.source == recognizer.SOURCE_WINDOW
    assert decision.track is not None and decision.track.title == "晴天"
    assert any("暂停" in reason and "以窗口标题源为准" in reason for reason in decision.reasons)


def test_paused_smtc_session_still_wins_when_no_other_player_sounds() -> None:
    decision = recognizer.decide(
        sample(
            playback=playback(title="上一首", playing=False),
            audio_rows=[audio("cloudmusic.exe", "inactive")],
            windows=[window("foobar2000.exe", "晴天 - 周杰伦")],
        ),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_PAUSED
    assert decision.source == recognizer.SOURCE_SMTC


def test_path_shaped_app_id_is_reduced_to_its_executable_name() -> None:
    """有些播放器把 app_id 报成完整安装路径：必须取 basename，否则音频证据全失配、路径还会进诊断。"""
    full_path = "C:" + chr(92) + "Program Files" + chr(92) + "Netease" + chr(92) + "cloudmusic.exe"
    decision = recognizer.decide(
        sample(playback=playback(app_id=full_path), audio_rows=[audio("cloudmusic.exe", muted=True)])
    )
    assert decision.exe == "cloudmusic.exe"
    assert decision.status == recognizer.STATUS_PAUSED  # 静音否决必须生效
    assert "Program Files" not in decision.exe


def test_window_allowlist_reasons_are_deduplicated() -> None:
    decision = recognizer.decide(
        sample(
            windows=[
                window("foobar2000.exe", "A - B", pid=1),
                window("foobar2000.exe", "A - C", pid=1),
            ],
            audio_rows=[audio("foobar2000.exe")],
        ),
        allowed_ids=frozenset({"cloudmusic"}),
        parse_window=parse_ok,
    )
    assert decision.status == recognizer.STATUS_ABSENT
    hits = [reason for reason in decision.reasons if "不在识别范围" in reason]
    assert len(hits) == 1


# ---------------------------------------------------------------- 采样开销
#
# 每秒一拍的系统调用是这条链路唯一的稳态成本，两条实测口径必须钉住：
#
# 1. **暂停时整桌窗口枚举降到 2 秒一次**（SMTC / WASAPI 仍是 1 秒）：暂停期间没有
#    "在播会话"要发现，而 ``EnumWindows`` + 逐 pid 查进程名是这里最贵的一次调用。
#    ``absent``（什么都看不到）不节流——那时窗口源是唯一能发现新播放器的来源。
# 2. **SMTC 管理器与会话表按线程缓存**：``request_async()`` 每次都走一次 WinRT 激活；
#    会话集合变化远慢于 1Hz。但被选中会话的标题/进度**每拍现读**（暂停边沿一秒都不能漏）。


def _window_info(exe: str = "foobar2000.exe", title: str = "晴天 - 周杰伦"):
    return SimpleNamespace(pid=1, exe=exe, title=title)


def _smtc_paused(*, title: str = "晴天", artist: str = "周杰伦"):
    return now_playing.SmtcSample(
        playback=now_playing.Playback(
            title=title, artist=artist, app_id="cloudmusic.exe", playing=False
        )
    )


def _smtc_absent():
    return now_playing.SmtcSample(reason="系统里没有正在播放的媒体会话。")


def _collect_ticks(counter: dict, *, smtc_factory, ticks: int = 5, monkeypatch=None):
    """连采 ``ticks`` 拍（每拍推进 1 秒假时钟），返回 WindowCache 与三源调用计数。"""
    if monkeypatch is not None:
        monkeypatch.setattr(window_source, "available", lambda: True)
        monkeypatch.setattr(audio_sessions, "available", lambda: True)
    now = [100.0]
    windows = counter.setdefault("windows", [])
    audio = counter.setdefault("audio", [])
    smtc = counter.setdefault("smtc", [])
    cache = sampling.WindowCache()
    samples = []
    for _ in range(ticks):
        samples.append(
            sampling.collect(
                tracked_app_id="cloudmusic.exe",
                targets=frozenset({"foobar2000.exe"}),
                smtc=lambda **_kwargs: (smtc.append(1), smtc_factory())[1],
                audio=lambda: (audio.append(1), ())[1],
                windows=lambda _targets: (
                    windows.append(1),
                    (_window_info(),),
                )[1],
                window_cache=cache,
                clock=lambda: now[0],
            )
        )
        now[0] += 1.0
    return cache, samples


def test_paused_window_enumeration_is_throttled_while_smtc_and_audio_keep_ticking(monkeypatch):
    seen: dict = {}
    _cache, samples = _collect_ticks(seen, smtc_factory=_smtc_paused, monkeypatch=monkeypatch)

    assert all(sample.playback is not None for sample in samples)
    # SMTC / WASAPI 仍然每秒一次（暂停/恢复的边沿一秒都不能漏）……
    assert len(seen["smtc"]) == 5
    assert len(seen["audio"]) == 5
    # ……整桌窗口枚举降到 2 秒一次：100、102、104 三趟。
    assert len(seen["windows"]) == 3


def test_paused_window_enumeration_refreshes_on_the_two_second_boundary(monkeypatch):
    seen: dict = {}
    _cache, _samples = _collect_ticks(
        seen, smtc_factory=_smtc_paused, ticks=3, monkeypatch=monkeypatch
    )
    # 秒数走到 100/101/102：第三拍刚好跨过 2 秒窗口。
    assert len(seen["windows"]) == 2


def test_absent_still_enumerates_windows_every_tick(monkeypatch):
    """什么都看不到时窗口源是唯一能发现"新开了播放器"的来源，不许节流。"""
    seen: dict = {}
    _collect_ticks(seen, smtc_factory=_smtc_absent, monkeypatch=monkeypatch)
    assert len(seen["windows"]) == 5


def test_playing_smtc_never_enumerates_windows(monkeypatch):
    """SMTC 明确在播时窗口源一条都不问（既有口径，节流不许把它变成"偶尔问"）。"""
    seen: dict = {}

    def playing():
        return now_playing.SmtcSample(
            playback=now_playing.Playback(
                title="晴天", artist="周杰伦", app_id="cloudmusic.exe", playing=True
            )
        )

    _collect_ticks(seen, smtc_factory=playing, monkeypatch=monkeypatch)
    assert seen["windows"] == []


def test_window_cache_is_invalidated_when_the_target_set_changes(monkeypatch):
    monkeypatch.setattr(window_source, "available", lambda: True)
    monkeypatch.setattr(audio_sessions, "available", lambda: True)
    cache = sampling.WindowCache()
    now = [100.0]
    calls: list[frozenset] = []

    def read_windows(targets):
        calls.append(targets)
        return (_window_info(),)

    def tick(targets):
        return sampling.collect(
            targets=targets,
            smtc=lambda **_kwargs: _smtc_paused(),
            audio=lambda: (),
            windows=read_windows,
            window_cache=cache,
            clock=lambda: now[0],
        )

    tick(frozenset({"foobar2000.exe"}))
    now[0] += 0.5
    tick(frozenset({"foobar2000.exe"}))  # 缓存有效：不重扫
    assert len(calls) == 1

    tick(frozenset({"foobar2000.exe", "aimp.exe"}))  # 换了识别目标：立刻重扫
    assert len(calls) == 2
    assert calls[-1] == frozenset({"foobar2000.exe", "aimp.exe"})


# ---------------------------------------------------------------- SMTC 缓存（按线程）

_MEDIA_MUSIC = now_playing.MEDIA_TYPE_MUSIC
_STATUS_PLAYING = now_playing.PLAYBACK_STATUS_PLAYING
_STATUS_PAUSED = 5


class _Seconds:
    """timeline 时间字段（真实实现是 ``datetime.timedelta``）。"""

    def __init__(self, value: float):
        self._value = value

    def total_seconds(self) -> float:
        return self._value


class _FakeSmtc:
    """假的 WinRT 侧：统计"建管理器 / 列会话 / 读属性"各被调用了多少次。"""

    def __init__(self) -> None:
        self.sessions: list = []
        self.manager_requests = 0
        self.list_calls = 0
        self.props_calls = 0
        self.info_calls = 0

    def manager_class(self):
        outer = self

        class _ManagerClass:
            @staticmethod
            async def request_async():
                outer.manager_requests += 1
                return _FakeManager(outer)

        return _ManagerClass


class _FakeManager:
    def __init__(self, parent: _FakeSmtc) -> None:
        self.parent = parent

    def get_sessions(self):
        self.parent.list_calls += 1
        return list(self.parent.sessions)


class _FakeSession:
    def __init__(self, parent: _FakeSmtc, *, title: str = "晴天", playing: bool = True) -> None:
        self.parent = parent
        self.source_app_user_model_id = "cloudmusic.exe"
        self.title = title
        self.playing = playing

    def get_playback_info(self):
        self.parent.info_calls += 1
        status = _STATUS_PLAYING if self.playing else _STATUS_PAUSED
        return SimpleNamespace(playback_type=_MEDIA_MUSIC, playback_status=status)

    def get_timeline_properties(self):
        return SimpleNamespace(
            position=_Seconds(30.0),
            end_time=_Seconds(180.0),
            last_updated_time=SimpleNamespace(year=1601),
        )

    async def try_get_media_properties_async(self):
        self.parent.props_calls += 1
        return SimpleNamespace(title=self.title, artist="周杰伦")


def _install_fake_smtc(monkeypatch):
    fake = _FakeSmtc()
    fake.sessions = [_FakeSession(fake)]
    monkeypatch.setattr(now_playing, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(now_playing, "_manager_class", fake.manager_class)
    return fake


def test_smtc_manager_and_session_list_are_cached_per_thread(monkeypatch):
    fake = _install_fake_smtc(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(now_playing, "_now", lambda: clock[0])
    try:
        first = now_playing.read_sample()
        assert first.playback is not None and first.playback.title == "晴天"
        assert (fake.manager_requests, fake.list_calls) == (1, 1)
        assert (fake.props_calls, fake.info_calls) == (1, 1)

        # 1 秒后：管理器与会话表都不重建；标题/进度仍然现读（每拍一次）。
        clock[0] += 1.0
        second = now_playing.read_sample()
        assert second.playback is not None and second.playback.title == "晴天"
        assert (fake.manager_requests, fake.list_calls) == (1, 1)
        assert (fake.props_calls, fake.info_calls) == (2, 2)

        # 跨过一个刷新窗口：会话表重列一次，管理器继续复用。
        clock[0] += now_playing.SESSION_REFRESH_SECONDS
        now_playing.read_sample()
        assert fake.manager_requests == 1
        assert fake.list_calls == 2
        assert fake.props_calls == 3
    finally:
        now_playing.close_thread_loop()



def test_smtc_cache_is_per_thread(monkeypatch):
    """WinRT 对象不能跨线程用：另一条采样线程必须有自己的管理器。"""
    fake = _install_fake_smtc(monkeypatch)
    monkeypatch.setattr(now_playing, "_now", lambda: 500.0)
    box: dict = {}

    def worker() -> None:
        try:
            box["sample"] = now_playing.read_sample()
        finally:
            now_playing.close_thread_loop()

    try:
        thread = threading.Thread(target=worker, name="smtc-cache-probe")
        thread.start()
        thread.join(10.0)
        assert thread.is_alive() is False
        assert box["sample"].playback is not None
        assert fake.manager_requests == 1

        # 主线程自己的缓存没被那条线程建立的管理器顶掉。
        assert now_playing.read_sample().playback is not None
        assert fake.manager_requests == 2
    finally:
        now_playing.close_thread_loop()
