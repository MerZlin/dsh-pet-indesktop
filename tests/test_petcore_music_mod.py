"""media.music：当前播放 + 歌词 + 唱歌动画（P4 重写）。

全部离线：网络走替身 ``HttpPort``，SMTC 走替身采样函数，音频/动画走替身
``presentation``，没有固定 sleep（时钟由测试推进）。设置页用 offscreen QApplication。
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from petcore.contracts.host import AnimationState, HostPorts
from petcore.contracts.http import HttpError, HttpResponse
from petcore.mods import feature as feature_mod
from petcore.mods.manifest import load_manifest
from petcore.ui import presentation_gate

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_test_host"


@pytest.fixture(autouse=True)
def _clean_presentation_gate():
    """进程级闸门是全局态：每个用例前后都清空，免得把别的测试的气泡挡掉。"""
    presentation_gate.reset()
    yield
    presentation_gate.reset()


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
now_playing = importlib.import_module(f"{HOST_PKG}.now_playing")
lyrics_mod = importlib.import_module(f"{HOST_PKG}.lyrics")
players_mod = importlib.import_module(f"{HOST_PKG}.players")
runtime_mod = importlib.import_module(f"{HOST_PKG}.runtime")


# ---------------------------------------------------------------- 替身


class FakeHttpPort:
    """记录每个请求（含 prefer_direct / URL / 上限），按 handler 回应或抛错。"""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        outcome = self.handler(request)
        if isinstance(outcome, BaseException):
            raise outcome
        status, body, headers = outcome
        return HttpResponse(status=status, url=request.url, headers=tuple(headers), body=body)


def _json(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


NETEASE_LYRIC = "[00:00.00]第一句\n[00:03.00]第二句"


def _netease_handler(request):
    if "cloudsearch" in request.url:
        return (200, _json({"result": {"songs": [{"id": 42, "ar": [{"name": "陶喆"}]}]}}), ())
    if "/api/song/lyric" in request.url:
        return (200, _json({"lrc": {"lyric": NETEASE_LYRIC}}), ())
    return HttpError("protocol", status=404)


KUGOU_SEARCH = _json({"candidates": [{"id": "abc", "accesskey": "key", "singer": "陶喆", "song": "蝴蝶"}]})
KUGOU_LYRIC = _json(
    {"content": base64.b64encode("[00:01.00]酷狗第一句\n[00:02.00]酷狗第二句".encode("utf-8")).decode("ascii")}
)


def _kugou_handler(request):
    if "krcs.kugou.com" in request.url:
        return (200, KUGOU_SEARCH, ())
    if "lyrics.kugou.com" in request.url:
        return (200, KUGOU_LYRIC, ())
    return HttpError("protocol", status=404)


class _Presentation:
    def __init__(self, start: bool = True):
        self.said: list[str] = []
        self.animations: list[tuple[str, bool]] = []
        self.released = 0
        self.start = start

    def say(self, text, *, duration_ms=4000, force=False):
        self.said.append(text)

    def play_animation(self, name, *, loop=False):
        self.animations.append((name, loop))
        return self.start

    def release_loop(self):
        self.released += 1
        return True

    def play_event(self, group, variant=None):
        return False

    def show_image(self, path, caption="", *, duration_ms=4000, scale=1.0):
        return False

    def notify(self, title, message, *, force=False):
        return None


class _Config(dict):
    def set(self, key, value):
        self[key] = value

    def get(self, key, default=None):
        return dict.get(self, key, default)


def _playback(
    title: str = "蝴蝶",
    artist: str = "陶喆",
    *,
    position: float | None = 0.0,
    playing: bool = True,
    app_id: str = "cloudmusic.exe",
    duration: float = 180.0,
):
    return now_playing.Playback(
        title=title,
        artist=artist,
        app_id=app_id,
        position=position,
        duration=duration,
        playing=playing,
    )


class _Harness:
    """运行时 + 可推进的时钟 + 可替换的采样/网络。"""

    def __init__(self, config=None, *, presentation=None, http=None, playback=None):
        self.presentation = presentation or _Presentation()
        self.ports = HostPorts(
            presentation=self.presentation,
            config=_Config(config or {}),
            http=http,
        )
        self.runtime = runtime_mod.MusicRuntime(SimpleNamespace(ports=self.ports))
        self.now = 100.0
        self.sample = playback
        self.reads = 0
        self.runtime.clock = lambda: self.now
        # 采样接缝：SMTC 走替身，音频/窗口源显式空（本套用例只验收状态机，
        # 三源合并的判定表在 tests/test_petcore_music_sources.py）。
        self.runtime.sample_smtc = self._read
        self.runtime.sample_audio = lambda: ()
        self.runtime.sample_windows = lambda _targets: ()
        # 后台取词同步执行：结果在 poll() 里就位，测试不必等线程。
        self.runtime.background = lambda work: work()

    def _read(self, **_kwargs):
        self.reads += 1
        if self.sample is None:
            return now_playing.SmtcSample(reason="系统里没有正在播放的媒体会话。")
        return now_playing.SmtcSample(playback=self.sample)

    def poll(self) -> None:
        self.runtime.poll()


# ---------------------------------------------------------------- LRC


def test_parse_lrc_handles_offsets_multi_timestamps_and_bad_lines():
    text = (
        "[ti:测试]\n"
        "[offset:500]\n"
        "[00:01.00]第一句\n"
        "[00:02.00][00:04.50]重复句\n"
        "坏行\n"
        "[abc]无效\n"
        "[00:03:20]冒号毫秒\n"
    )
    lines = lyrics_mod.parse_lrc(text)
    assert [(round(line.at, 2), line.text) for line in lines] == [
        (1.5, "第一句"),
        (2.5, "重复句"),
        (3.7, "冒号毫秒"),
        (5.0, "重复句"),
    ]
    assert lyrics_mod.parse_lrc("") == ()


def test_line_at_applies_lead_and_offset():
    lines = lyrics_mod.parse_lrc("[00:10.00]A\n[00:20.00]B")
    assert lyrics_mod.line_at(lines, 9.0) == -1
    assert lyrics_mod.line_at(lines, 9.8) == -1
    assert lyrics_mod.line_at(lines, 9.8, lead=0.3) == 0
    assert lyrics_mod.line_at(lines, 19.8) == 0
    assert lyrics_mod.line_at(lines, 19.8, lead=0.3) == 1
    assert lyrics_mod.line_at(lines, 19.8, offset=0.5) == 1
    assert lyrics_mod.line_at(lines, None) == -1
    assert lyrics_mod.line_at((), 10.0) == -1
    assert lyrics_mod.line_duration_ms(lines, 0) == 6000
    assert lyrics_mod.line_duration_ms(lines, 1) == 3000


def test_instrumental_placeholder_is_recognised():
    instrumental = lyrics_mod.parse_lrc("[00:00.00]纯音乐，请欣赏")
    assert lyrics_mod.looks_instrumental(instrumental) is True
    assert lyrics_mod.looks_instrumental(lyrics_mod.parse_lrc("[00:00.00]Instrumental")) is True
    real = lyrics_mod.parse_lrc("[00:00.00]这是一首纯音乐\n[00:02.00]但这两句是歌词\n[00:03.00]还有第三句\n[00:04.00]第四句")
    assert lyrics_mod.looks_instrumental(real) is False


# ---------------------------------------------------------------- 取词


def test_fetch_lyrics_uses_first_source_directly():
    port = FakeHttpPort(_netease_handler)
    result = lyrics_mod.fetch_lyrics(port, "蝴蝶", "陶喆")

    assert [line.text for line in result.lines] == ["第一句", "第二句"]
    assert result.source == "网易云"
    assert len(port.requests) == 2
    assert all(request.prefer_direct is True for request in port.requests)
    assert all(request.max_bytes <= lyrics_mod.MAX_BYTES for request in port.requests)
    assert port.requests[0].params[:1] == (("s", "蝴蝶 陶喆"),)


def test_fetch_lyrics_falls_back_to_second_source():
    def handler(request):
        if "music.163.com" in request.url:
            return HttpError("network")
        return _kugou_handler(request)

    port = FakeHttpPort(handler)
    result = lyrics_mod.fetch_lyrics(port, "蝴蝶", "陶喆")

    assert result.source == "酷狗"
    assert [line.text for line in result.lines] == ["酷狗第一句", "酷狗第二句"]
    assert any("krcs.kugou.com" in request.url for request in port.requests)


def test_fetch_lyrics_failure_names_every_source():
    port = FakeHttpPort(lambda request: HttpError("timeout"))
    with pytest.raises(lyrics_mod.LyricsError) as caught:
        lyrics_mod.fetch_lyrics(port, "蝴蝶", "陶喆")

    text = str(caught.value)
    assert "网易云" in text and "酷狗" in text and "lrclib" in text
    assert "请求超时" in text
    # 一首歌只试一轮：三个源各一次请求，失败后不再重发。
    assert len(port.requests) == 3


def test_fetch_lyrics_marks_instrumental():
    def handler(request):
        if "cloudsearch" in request.url:
            return (200, _json({"result": {"songs": [{"id": 7, "ar": [{"name": "陶喆"}]}]}}), ())
        return (200, _json({"lrc": {"lyric": "[00:00.00]纯音乐，请欣赏"}}), ())

    result = lyrics_mod.fetch_lyrics(FakeHttpPort(handler), "飞机场的10:30", "陶喆")
    assert result.instrumental is True
    assert result.lines == ()


def test_cache_hit_skips_network_and_cap_is_enforced(tmp_path):
    cache = lyrics_mod.LyricsCache(tmp_path / "lyrics", limit=2)
    port = FakeHttpPort(_netease_handler)

    first = lyrics_mod.fetch_lyrics(port, "蝴蝶", "陶喆", cache=cache)
    assert len(port.requests) == 2
    again = lyrics_mod.fetch_lyrics(port, "蝴蝶", "陶喆", cache=cache)
    assert len(port.requests) == 2
    assert again.lines == first.lines

    for title in ("甲", "乙", "丙"):
        lyrics_mod.fetch_lyrics(port, title, "陶喆", cache=cache)
    files = list((tmp_path / "lyrics").glob("*.json"))
    assert len(files) == 2
    assert cache.path_for("蝴蝶", "陶喆").name.endswith(".json")


def test_cache_key_is_sha1_of_title_and_artist():
    assert lyrics_mod.cache_key("蝴蝶", "陶喆") == lyrics_mod.cache_key(" 蝴蝶 ", "陶喆")
    assert len(lyrics_mod.cache_key("蝴蝶", "陶喆")) == 40


# ---------------------------------------------------------------- 会话选择


def _session(app_id, *, media_type=now_playing.MEDIA_TYPE_MUSIC, playing=True, index=0):
    return now_playing.Session(
        app_id=app_id,
        media_type=media_type,
        playing=playing,
        browser=now_playing.is_browser_session(app_id),
        vetoed=now_playing.is_vetoed_session(app_id),
        index=index,
    )


def test_pick_singing_session_veto_rules():
    browser = _session("chrome.exe")
    music = _session("cloudmusic.exe")
    video = _session("cloudmusic.exe", media_type=now_playing.MEDIA_TYPE_VIDEO)
    system = _session("SystemSettings.exe")

    assert now_playing.pick_singing_session([browser], browser_media_enabled=False) is None
    assert now_playing.pick_singing_session([browser], browser_media_enabled=True) is browser
    assert now_playing.pick_singing_session([video, system]) is None
    assert now_playing.pick_singing_session([video, music], browser_media_enabled=False) is music
    assert now_playing.pick_singing_session([]) is None


def test_pick_singing_session_prefers_tracked_player_in_best_tier():
    browser = _session("chrome", index=0)
    music = _session("cloudmusic.exe", index=1)
    picked = now_playing.pick_singing_session(
        [browser, music], browser_media_enabled=True, tracked_app_id="cloudmusic.exe"
    )
    assert picked is music
    paused = _session("cloudmusic.exe", playing=False, index=0)
    assert (
        now_playing.pick_singing_session([paused], browser_media_enabled=False) is paused
    )


def test_allowed_app_ids_from_paths_and_extras():
    assert now_playing.allowed_app_ids({}) == frozenset()
    ids = now_playing.allowed_app_ids(
        {"netease": r"C:\Program Files\Netease\cloudmusic.exe"},
        extras="汽水音乐，QQMusic.exe",
    )
    assert ids == frozenset({"cloudmusic", "汽水音乐", "qqmusic"})


def test_pick_singing_session_respects_allowlist():
    netease = _session("cloudmusic.exe", index=0)
    kugou = _session("kugou.exe", index=1)
    allowed = frozenset({"cloudmusic"})

    assert now_playing.pick_singing_session([kugou], allowed_ids=allowed) is None
    assert (
        now_playing.pick_singing_session(
            [kugou, netease], allowed_ids=allowed
        )
        is netease
    )
    assert now_playing.accepts_singing(kugou, allowed_ids=allowed) is False
    assert now_playing.accepts_singing(netease, allowed_ids=allowed) is True


def test_player_registry_only_launches_configured_paths(tmp_path, monkeypatch):
    assert players_mod.player_keys() == ("netease", "qqmusic", "kugou", "soda")
    ok, message = players_mod.launch({}, "netease")
    assert ok is False and "还没有配置" in message
    ok, message = players_mod.launch({"netease": str(tmp_path / "missing.exe")}, "netease")
    assert ok is False and "路径不可用" in message

    launched: list[str] = []
    monkeypatch.setattr(players_mod.os, "startfile", launched.append, raising=False)
    exe = tmp_path / "cloudmusic.exe"
    exe.write_bytes(b"MZ")
    ok, message = players_mod.launch({"netease": str(exe)}, "netease")
    assert ok is True and "网易云音乐" in message
    assert launched == [str(exe)]


# ---------------------------------------------------------------- 唱歌状态机


def test_playing_track_starts_singing_once():
    harness = _Harness({"music_lyric_enabled": False}, playback=_playback())
    harness.poll()
    harness.poll()

    assert harness.presentation.animations == [(runtime_mod.SING_ANIM, True)]
    assert harness.presentation.released == 0


def test_failed_animation_backs_off_then_recovers():
    """唱歌失败按 2^n 拍退避重试，**永不永久放弃**（角色包补上素材后能自己唱起来）。"""
    presentation = _Presentation(start=False)
    harness = _Harness(
        {"music_lyric_enabled": False},
        presentation=presentation,
        playback=_playback(),
    )
    for _ in range(5):
        harness.poll()
        harness.now += 1
    # 1 + 2 + 4 ... 的退避里，5 拍只应尝试 1 次（第一次失败后等 2 秒）
    attempts = len(presentation.animations)
    assert 1 <= attempts <= 3, attempts

    # 素材变得可用（角色包补上「悠闲哼歌」）后，退避到期就会唱
    presentation.start = True
    for _ in range(40):
        harness.poll()
        harness.now += 1
    assert presentation.animations[-1] == (runtime_mod.SING_ANIM, True)
    assert harness.runtime.singing is True
    assert presentation.released == 0


def test_silence_beyond_grace_releases_loop():
    harness = _Harness(
        {"music_lyric_enabled": False, "music_sing_grace_seconds": 5},
        playback=_playback(),
    )
    harness.poll()
    assert harness.presentation.released == 0

    harness.sample = None
    harness.now += 1
    harness.poll()
    harness.now += 3.5
    harness.poll()
    assert harness.presentation.released == 0

    harness.now += 1.5
    harness.poll()
    assert harness.presentation.released == 1
    assert harness.presentation.animations == [(runtime_mod.SING_ANIM, True)]


def test_pause_releases_the_loop_on_the_next_tick():
    """明确暂停**立刻**停唱：宽限只用于 absent（用户按了暂停就是不想听了）。"""
    harness = _Harness(
        {"music_lyric_enabled": False, "music_sing_grace_seconds": 2},
        playback=_playback(),
    )
    harness.poll()
    assert harness.presentation.animations == [(runtime_mod.SING_ANIM, True)]

    harness.sample = _playback(playing=False)
    harness.now += 1
    harness.poll()
    assert harness.presentation.released == 1
    assert harness.runtime.singing is False


def test_instrumental_track_does_not_sing():
    def handler(request):
        if "cloudsearch" in request.url:
            return (200, _json({"result": {"songs": [{"id": 7, "ar": [{"name": "陶喆"}]}]}}), ())
        return (200, _json({"lrc": {"lyric": "[00:00.00]纯音乐，请欣赏"}}), ())

    harness = _Harness(
        {"music_sing_enabled": True, "music_lyric_enabled": True},
        http=FakeHttpPort(handler),
        playback=_playback(title="飞机场的10:30"),
    )
    harness.poll()
    harness.now += 1
    harness.poll()

    assert harness.presentation.animations == []
    assert harness.runtime.instrumental is True


def test_singing_disabled_does_nothing():
    harness = _Harness(
        {"music_sing_enabled": False, "music_lyric_enabled": False},
        playback=_playback(),
    )
    harness.poll()
    harness.poll()

    assert harness.presentation.animations == []
    assert harness.presentation.released == 0


def test_music_disabled_does_not_poll():
    harness = _Harness({"music_enabled": False}, playback=_playback())
    harness.poll()
    assert harness.reads == 0
    assert harness.presentation.animations == []


def test_stop_releases_loop():
    harness = _Harness({"music_lyric_enabled": False}, playback=_playback())
    harness.poll()
    harness.runtime.stop()
    assert harness.presentation.released == 1


# ---------------------------------------------------------------- 歌词节流


def test_lyric_bubble_has_lead_and_throttle():
    harness = _Harness(
        {
            "music_lyric_enabled": True,
            "music_lyric_lead_seconds": 0.3,
            "music_sing_enabled": False,
        },
        http=FakeHttpPort(_netease_handler),
        playback=_playback(position=0.0),
    )
    harness.poll()
    assert harness.presentation.said == ["第一句"]

    harness.now += 1.0
    harness.sample = _playback(position=20.0)
    harness.poll()
    assert harness.presentation.said == ["第一句"]

    harness.now += 1.0
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句"]


def test_pause_freezes_the_local_clock():
    def handler(request):
        if "cloudsearch" in request.url:
            return (200, _json({"result": {"songs": [{"id": 1, "ar": [{"name": "陶喆"}]}]}}), ())
        return (200, _json({"lrc": {"lyric": "[00:00.00]第一句\n[00:20.00]第二句"}}), ())

    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=FakeHttpPort(handler),
        playback=_playback(position=None),
    )
    harness.poll()
    assert harness.presentation.said == ["第一句"]

    harness.now += 5
    harness.sample = _playback(position=None, playing=False)
    harness.poll()
    harness.now += 60
    harness.sample = _playback(position=None)
    harness.poll()

    # 暂停的 60 秒不算进本地时钟：位置仍是 5 秒，不该跳到 20 秒那句。
    assert harness.presentation.said == ["第一句"]


def test_lyric_fetch_runs_once_per_track():
    port = FakeHttpPort(_netease_handler)
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=port,
        playback=_playback(position=0.0),
    )
    harness.poll()
    requests = len(port.requests)
    harness.now += 1
    harness.poll()
    assert len(port.requests) == requests

    harness.sample = _playback(title="普通朋友", position=0.0)
    harness.now += 1
    harness.poll()
    assert len(port.requests) > requests


def test_lyric_align_cycles_offset_and_bubbles():
    harness = _Harness({"music_lyric_offset_seconds": 2.5, "music_lyric_enabled": False})
    runtime = harness.runtime
    runtime.lyric_align()
    assert runtime.lyric_offset() == 3.0
    runtime.lyric_align()
    assert runtime.lyric_offset() == runtime_mod.OFFSET_MIN
    assert "+3.0" in harness.presentation.said[0]
    assert "-3.0" in harness.presentation.said[1]


# ---------------------------------------------------------------- 命令反馈


def test_transport_without_session_reports_reason():
    harness = _Harness({"music_enabled": True})
    harness.runtime.control_playback = lambda action, *, tracked_app_id="": (
        False,
        "当前没有可控制的播放器（请先在播放器里播放一首歌）。",
    )
    harness.runtime.commands["pause"]()
    assert "没有可控制的播放器" in harness.presentation.said[-1]


def test_runtime_passes_allowlist_only_when_filter_enabled():
    harness = _Harness(
        {
            "music_enabled": True,
            "music_player_paths": {"netease": r"C:\Netease\cloudmusic.exe"},
            "music_filter_enabled": False,
        }
    )
    seen: list[dict] = []

    def read(**kwargs):
        seen.append(kwargs)
        return now_playing.SmtcSample(reason="系统里没有正在播放的媒体会话。")

    harness.runtime.sample_smtc = read
    harness.poll()
    assert "allowed_ids" not in seen[-1]

    harness.ports.config.set("music_filter_enabled", True)
    harness.poll()
    assert seen[-1]["allowed_ids"] == frozenset({"cloudmusic"})


def test_sung_bubble_carries_subtitle_when_port_supports_it():
    class SubtitlePresentation(_Presentation):
        def __init__(self):
            super().__init__()
            self.calls = []

        def say(self, text, *, duration_ms=4000, force=False, subtitle=""):
            self.calls.append((text, subtitle))

    presentation = SubtitlePresentation()
    harness = _Harness({}, presentation=presentation)
    harness.runtime._title = "蝴蝶"

    assert harness.runtime._say_sung("第一句", subtitle=harness.runtime._subtitle())

    assert presentation.calls == [("第一句", "我在唱《蝴蝶》")]


def test_transport_success_reports_action():
    harness = _Harness({"music_enabled": True})
    harness.runtime.control_playback = lambda action, *, tracked_app_id="": (True, "")
    harness.runtime.commands["next"]()
    assert "下一首" in harness.presentation.said[-1]


def test_open_player_without_path_bubbles_reason():
    harness = _Harness({"music_enabled": True, "music_player_paths": {}})
    harness.runtime.commands["open_netease"]()
    assert "还没有配置" in harness.presentation.said[-1]


# ---------------------------------------------------------------- 清单与设置页


class _Host:
    def __init__(self, config):
        self.config = _Config(config)
        self.ports = SimpleNamespace(http=None)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_manifest_and_contributions_load():
    manifest, problems = load_manifest(MOD_DIR)
    assert problems == []
    assert manifest.id == "media.music"
    assert manifest.kind == "feature"
    assert manifest.execution == "host-only"
    assert manifest.version == "1.2.0"

    manager = feature_mod.FeatureManager(HostPorts())
    diagnostics = manager.load(
        pack=MOD_DIR, owner=manifest.id, entrypoint=manifest.entrypoint, config={}
    )
    assert diagnostics == []

    menu_ids = {contribution.id for _, contribution in manager.menu_contributions()}
    assert {
        "pause",
        "next",
        "prev",
        "stop",
        "lyric_align",
        "open_netease",
        "open_qqmusic",
        "open_kugou",
        "open_soda",
    } <= menu_ids
    pages = manager.settings_pages()
    assert pages and pages[0].owner == "media.music"
    assert pages[0].id == "music"
    owners_with_menu = {feature.owner for feature, _ in manager.menu_contributions()}
    owners_with_settings = {
        feature.owner
        for feature in manager.features.values()
        for contribution in feature.definition.contributions
        if contribution.kind == "settings_page"
    }
    assert owners_with_menu <= owners_with_settings

    commands = manager.features["media.music"].commands
    for command in ("pause", "next", "prev", "stop", "lyric_align", "open_soda"):
        assert callable(commands[command])


def test_settings_page_round_trip(app):
    manager = feature_mod.FeatureManager(HostPorts())
    manifest, _ = load_manifest(MOD_DIR)
    manager.load(pack=MOD_DIR, owner=manifest.id, entrypoint=manifest.entrypoint, config={})
    page_class = manager.settings_pages()[0].factory

    host = _Host(
        {
            "music_enabled": True,
            "music_sing_enabled": True,
            "music_sing_grace_seconds": 5,
            "music_lyric_enabled": True,
            "music_lyric_lead_seconds": 0.3,
            "music_lyric_cache_limit": 80,
            "music_browser_media_enabled": False,
            "music_player": "",
            "music_player_paths": {},
        }
    )
    page = page_class(host)
    assert page.dirty() is False

    page.sing.setChecked(False)
    assert host.config["music_sing_enabled"] is False
    page.grace.setValue(9)
    assert host.config["music_sing_grace_seconds"] == 9
    page.lead.setValue(1.2)
    assert host.config["music_lyric_lead_seconds"] == 1.2
    page.browser.setChecked(True)
    assert host.config["music_browser_media_enabled"] is True
    page.player.setCurrentIndex(page.player.findData("netease"))
    assert host.config["music_player"] == "netease"

    page.path_edits["netease"].setText("C:/CloudMusic/cloudmusic.exe")
    page._on_path_changed("netease")
    assert host.config["music_player_paths"] == {"netease": "C:/CloudMusic/cloudmusic.exe"}
    assert page.dirty() is True

    page.confirm_save()
    assert page.dirty() is False

    page.sing.setChecked(True)
    page.discard_changes()
    assert page.sing.isChecked() is False
    assert host.config["music_sing_enabled"] is False
    assert host.config["music_player_paths"] == {"netease": "C:/CloudMusic/cloudmusic.exe"}


def test_settings_page_has_h2_and_h3_groups(app):
    from PySide6.QtWidgets import QLabel

    page = sys.modules[HOST_PKG].MusicPage(_Host({"music_enabled": True}))
    headings = [
        (label.objectName(), label.text())
        for label in page.findChildren(QLabel)
        if label.objectName() in {"groupTitle", "subsectionTitle"}
    ]
    assert headings[0] == ("groupTitle", "音乐")
    assert [text for name, text in headings if name == "subsectionTitle"] == [
        "播放与唱歌",
        "识别诊断",
        "歌词显示",
        "识别范围",
        "切歌与播放控制",
        "播放器",
    ]


def test_settings_page_detect_result_is_shown(app):
    page = sys.modules[HOST_PKG].MusicPage(_Host({"music_enabled": True}))
    page._on_detect_done("陶喆 - 蝴蝶（在播）")
    assert "蝴蝶" in page.detect_status.text()
    assert page.detect_button.isEnabled() is True

    # 详细诊断进多行视图，并允许复制（用户报障时贴这段）
    page._on_diagnose_done(
        "SMTC：1 个会话\n判定：在播 · 来源=系统媒体会话\n理由：\n  · …"
    )
    assert "SMTC" in page.diagnose_view.toPlainText()
    assert "在播" in page.detect_status.text()
    assert page.copy_button.isEnabled() is True


def test_settings_page_title_format_round_trips(app):
    page = sys.modules[HOST_PKG].MusicPage(_Host({"music_enabled": True}))
    page.title_format.setText("%artist% - %title%")
    page._write("music_title_format", page.title_format.text().strip())
    assert page.dirty() is True
    page.confirm_save()
    assert page.dirty() is False

    page.title_format.setText("改坏了")
    page.discard_changes()
    assert page.title_format.text() == "%artist% - %title%"
    assert page.player.findData("netease") >= 0
    assert set(page.path_edits) == set(players_mod.player_keys())


def test_blocked_smtc_stop_retains_worker_and_cancel_generation():
    import threading

    entered, release = threading.Event(), threading.Event()
    harness = _Harness({"music_lyric_enabled": False}, playback=_playback())
    runtime = harness.runtime
    reads = []

    def read(**kwargs):
        reads.append(threading.get_ident())
        entered.set()
        assert release.wait(10)
        return now_playing.SmtcSample(playback=_playback())

    runtime.sample_smtc = read
    runtime.start()
    old = runtime._worker
    cancel = runtime._closing
    try:
        runtime.poll()
        assert entered.wait(10)
        runtime.stop()
        assert runtime._worker is old and old.is_alive()
        runtime.start()
        runtime.poll()
        assert runtime._worker is old
        assert cancel.is_set()
        assert len(reads) == 1
    finally:
        runtime.stop()
        release.set()
        old.join(10)
        current = runtime._worker
        if current is not None and current is not old:
            current.join(10)
    assert not old.is_alive()
    assert harness.presentation.animations == []
    runtime.start()
    try:
        assert runtime._worker is not old
        assert runtime._closing is not cancel
    finally:
        runtime.stop()
        runtime._worker.join(10)


def test_old_lyrics_cannot_reappear_after_stop_and_restart():
    harness = _Harness(http=FakeHttpPort(lambda request: (200, b"", ())), playback=_playback())
    pending = []
    harness.runtime.background = pending.append
    harness.runtime.fetch_lyrics = lambda *args, **kwargs: lyrics_mod.Lyrics(
        lines=(lyrics_mod.LyricLine(0, "old"),), instrumental=False,
    )
    harness.poll()
    assert pending
    harness.runtime.stop()
    # 同一首歌重新出现也不能接受前一轮取词结果。
    harness.runtime._track_key = harness.sample.key()
    pending[0]()
    assert harness.runtime._lines == ()
# ---------------------------------------------------------------- 唱到一半被打断
#
# 三个用户报的现象各有一组用例：
#
# 1. **唱歌动作被打断后不再回来**：核心 ``_play_spec()`` 是循环的唯一清除点（点击、
#    探头、击飞、拖拽都会清掉），mod 以前只认自己的 ``_leases``，于是"以为还在唱"。
#    现在每拍用 ``animation_state()`` 复核，丢了就补，并且有 2^n 退避、接管态不抢。
# 2. **歌词期间自言自语打断**：歌词持有展示会话（``begin_scene``），别人的自动气泡
#    返回 False。
# 3. **单曲循环第二遍没有歌词**：曲目身份不变（``Playback.key``），进度回到开头要按
#    新一轮重放**已缓存**的歌词；不上报进度的播放器用估算曲长 + 暂停→播放边沿判定。


class _LoopPresentation(_Presentation):
    """带动画状态的替身：核心清掉循环/探头接管都能被如实模拟。"""

    def __init__(self, start: bool = True):
        super().__init__(start=start)
        self.name = ""
        self.loop = False
        self.locked = False

    def play_animation(self, name, *, loop=False):
        ok = super().play_animation(name, loop=loop)
        if ok:
            self.name, self.loop = name, loop
        return ok

    def release_loop(self):
        self.loop = False
        return super().release_loop()

    def animation_state(self):
        if self.locked:
            return AnimationState(name=self.name, loop=False, locked=True)
        return AnimationState(name=self.name, loop=self.loop)

    def external_clear(self, name: str = "点头") -> None:
        """模拟核心改播别的素材：``_play_spec()`` 清掉循环标志。"""
        self.name, self.loop = name, False


def test_singing_resumes_after_core_clears_the_loop():
    presentation = _LoopPresentation()
    harness = _Harness(
        {"music_lyric_enabled": False}, presentation=presentation, playback=_playback()
    )
    harness.poll()
    assert presentation.animations == [(runtime_mod.SING_ANIM, True)]
    assert presentation.loop is True

    # 用户点了桌宠：核心改播点击动作，循环标志被清掉，mod 的 _leases 还留着。
    presentation.external_clear("点击")
    harness.now += 1
    harness.poll()

    assert presentation.animations == [(runtime_mod.SING_ANIM, True)] * 2
    assert presentation.loop is True
    assert harness.runtime.singing is True
    # 循环标志本来就不在我们手里了：补播前不许再发一次 release_loop()
    # （那会误伤别人持着的循环）。
    assert presentation.released == 0


def test_lost_loop_retries_with_backoff_and_never_gives_up():
    presentation = _LoopPresentation()
    harness = _Harness(
        {"music_lyric_enabled": False}, presentation=presentation, playback=_playback()
    )
    harness.poll()
    first = len(presentation.animations)

    # 每一拍都被别人接管：必须恢复，但不许每拍都发请求。
    for _ in range(20):
        presentation.external_clear()
        harness.now += 1
        harness.poll()
    recovered = len(presentation.animations) - first
    assert 2 <= recovered <= 8, recovered

    # 不再被清掉之后稳住，一拍请求都不再发。
    for _ in range(60):
        harness.now += 1
        harness.poll()
        if harness.runtime.singing:
            break
    assert harness.runtime.singing is True
    assert presentation.loop is True
    settled = len(presentation.animations)
    for _ in range(5):
        harness.now += 1
        harness.poll()
    assert len(presentation.animations) == settled
    assert harness.runtime.singing is True


def test_locked_states_are_not_stolen():
    presentation = _LoopPresentation()
    harness = _Harness(
        {"music_lyric_enabled": False}, presentation=presentation, playback=_playback()
    )
    harness.poll()
    assert len(presentation.animations) == 1

    # 探头/击飞/拖拽接管：核心会拒绝或立刻清掉，mod 不许抢。
    presentation.locked = True
    for _ in range(5):
        presentation.external_clear("待机")
        harness.now += 1
        harness.poll()
    assert len(presentation.animations) == 1

    presentation.locked = False
    harness.now += 1
    harness.poll()
    assert presentation.animations[-1] == (runtime_mod.SING_ANIM, True)


def test_track_change_rearms_a_backed_off_loop():
    presentation = _LoopPresentation()
    harness = _Harness(
        {"music_lyric_enabled": False}, presentation=presentation, playback=_playback()
    )
    harness.poll()
    for _ in range(8):
        presentation.external_clear()
        harness.now += 1
        harness.poll()
    before = len(presentation.animations)

    harness.sample = _playback(title="普通朋友")
    harness.now += 1
    presentation.external_clear()
    harness.poll()
    assert len(presentation.animations) == before + 1
    assert presentation.loop is True


# ---------------------------------------------------------------- 单曲循环


POSITION_LYRIC = "[00:00.00]第一句\n[00:15.00]第二句"
CLOCK_LYRIC = "[00:00.00]第一句\n[00:03.00]第二句"


def _lyric_handler(text):
    def handler(request):
        if "cloudsearch" in request.url:
            return (200, _json({"result": {"songs": [{"id": 42, "ar": [{"name": "陶喆"}]}]}}), ())
        if "/api/song/lyric" in request.url:
            return (200, _json({"lrc": {"lyric": text}}), ())
        return HttpError("protocol", status=404)

    return handler


def test_single_track_loop_replays_lyrics_from_reported_position():
    port = FakeHttpPort(_lyric_handler(POSITION_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=port,
        playback=_playback(position=0.0),
    )
    harness.poll()
    assert harness.presentation.said == ["第一句"]
    requests = len(port.requests)

    harness.now += 2.0
    harness.sample = _playback(position=20.0)
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句"]

    # 播到尾部（0.9 × 180s）：没有新的一句。
    harness.now += 2.0
    harness.sample = _playback(position=162.0)
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句"]

    # 单曲循环第二遍：进度回到 0.05 × 180s = 9s，用缓存歌词重新逐行显示。
    harness.now += 2.0
    harness.sample = _playback(position=9.0)
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句", "第一句"]
    assert harness.runtime._lyric_index == 0
    # 不许重新联网取词。
    assert len(port.requests) == requests

    harness.now += 2.0
    harness.sample = _playback(position=20.0)
    harness.poll()
    assert harness.presentation.said[-1] == "第二句"


def test_single_track_loop_replays_lyrics_without_reported_position():
    """网易云/酷狗不上报进度：本地时钟越过估算曲长就按新一轮处理。"""
    port = FakeHttpPort(_lyric_handler(CLOCK_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=port,
        playback=_playback(position=None, duration=0.0),
    )
    harness.poll()
    assert harness.presentation.said == ["第一句"]
    requests = len(port.requests)

    harness.now += 3.0
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句"]

    # 估算曲长 = 末句 3s + 6s 尾巴 = 9s：越过它就是新一圈。
    harness.now += 7.5
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句", "第一句"]
    assert len(port.requests) == requests

    harness.now += 3.0
    harness.poll()
    assert harness.presentation.said[-1] == "第二句"


def test_pause_play_edge_starts_a_new_round_when_position_is_unknown():
    port = FakeHttpPort(_lyric_handler(CLOCK_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=port,
        playback=_playback(position=None, duration=0.0),
    )
    harness.poll()
    harness.now += 3.0
    harness.poll()
    assert harness.presentation.said == ["第一句", "第二句"]

    # 尾部暂停（本地时钟停在 7s，已过"估算曲长 - 容差"），再播放就是新一圈。
    harness.now += 4.0
    harness.poll()
    harness.now += 1.0
    harness.sample = _playback(position=None, playing=False, duration=0.0)
    harness.poll()
    harness.now += 30.0
    harness.sample = _playback(position=None, duration=0.0)
    harness.poll()
    assert harness.presentation.said[-1] == "第一句"
    assert harness.runtime._lyric_index == 0


# ---------------------------------------------------------------- 暂停
#
# 用户报的两个现象同源：播放器**明确暂停**时运行时还在等 ``music_sing_grace_seconds``
# （唱歌动画多演 5 秒），并且把暂停期间的身份降级（SMTC 会话掉线 → 窗口标题兜底，
# 艺人/专辑/时长都丢了）当成换歌 → ``_begin_track`` 重建歌词状态 → 恢复播放时重新
# 联网取词 → 取不到就冒「歌词获取失败」。
#
# 现在：暂停下一拍就停唱；暂停里不动曲目/歌词/取词配额；取词失败只在**播放中**冒泡。


def _failing_fetch(calls: list[str]):
    """取词替身：每次都失败（模拟三个源都没有这首）。"""

    def fetch(_http, title, _artist, **_kwargs):
        calls.append(title)
        raise lyrics_mod.LyricsError("歌词获取失败：网易云：没有找到匹配的歌词")

    return fetch


def _paused(harness, **overrides) -> None:
    """推进一拍并且换成暂停的采样（默认保持曲目身份不变）。"""
    harness.sample = _playback(playing=False, **overrides)
    harness.now += 1
    harness.poll()


def test_paused_singing_stops_and_lyrics_are_kept():
    port = FakeHttpPort(_lyric_handler(POSITION_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": True, "music_sing_grace_seconds": 30},
        http=port,
        playback=_playback(position=0.0),
    )
    harness.poll()  # 第一拍：取词 + 等纯音乐探测
    harness.now += 1
    harness.poll()  # 第二拍：唱起来
    assert harness.presentation.animations == [(runtime_mod.SING_ANIM, True)]
    assert harness.presentation.said == ["第一句"]
    requests = len(port.requests)
    lines, index = harness.runtime._lines, harness.runtime._lyric_index

    # 宽限设成 30 秒也不许再唱（旧实现要等满宽限才解除）。
    _paused(harness, position=1.0)
    assert harness.presentation.released == 1
    assert harness.runtime.singing is False
    assert harness.runtime._lines == lines
    assert harness.runtime._lyric_index == index
    assert len(port.requests) == requests
    assert not [text for text in harness.presentation.said if "歌词获取失败" in text]

    # 继续暂停：不重复释放、不取词、不再唱。
    for _ in range(3):
        harness.now += 1
        harness.poll()
    assert harness.presentation.released == 1
    assert harness.presentation.animations == [(runtime_mod.SING_ANIM, True)]
    assert len(port.requests) == requests


def test_paused_identity_degradation_keeps_the_track_and_its_lyrics():
    """暂停期间 SMTC 掉线（只剩窗口标题、没有时长）不是换歌。"""
    port = FakeHttpPort(_lyric_handler(POSITION_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": True, "music_sing_grace_seconds": 30},
        http=port,
        playback=_playback(position=0.0),
    )
    harness.poll()
    harness.now += 1
    harness.poll()
    key = harness.runtime._track_key
    assert key == ("蝴蝶", "陶喆")
    requests = len(port.requests)
    lines, index = harness.runtime._lines, harness.runtime._lyric_index

    # 暂停：艺人丢了、duration 也没了（窗口标题源给的曲目就是这个形状）。
    degraded = {"artist": "", "duration": 0.0, "position": None}
    _paused(harness, **degraded)
    for _ in range(2):
        harness.now += 1
        harness.poll()

    assert harness.runtime._track_key == key
    assert harness.runtime._lines == lines
    assert harness.runtime._lyric_index == index
    assert len(port.requests) == requests

    # 恢复播放，身份仍是降级的那一版：不重建、不取词、不跳句、不冒错。
    # 多跑几拍——降级身份是**一直**这样的，第二拍才算真正守住（别名不能只用一次）。
    harness.sample = _playback(**degraded)
    for _ in range(3):
        harness.now += 1
        harness.poll()
        assert harness.runtime._track_key == key
        assert harness.runtime._lines == lines
        assert len(port.requests) == requests

    assert harness.runtime._lyric_index == index
    assert harness.runtime.singing is True
    assert not [text for text in harness.presentation.said if "歌词获取失败" in text]


def test_real_track_change_while_paused_is_adopted_on_resume():
    """暂停里真的换了歌：暂停中不重建，恢复播放时才接管（并正常取词）。"""
    port = FakeHttpPort(_lyric_handler(POSITION_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=port,
        playback=_playback(position=0.0),
    )
    harness.poll()
    requests = len(port.requests)

    harness.sample = _playback(title="普通朋友", playing=False)
    harness.now += 1
    harness.poll()
    assert harness.runtime._track_key == ("蝴蝶", "陶喆")
    assert len(port.requests) == requests

    harness.sample = _playback(title="普通朋友")
    harness.now += 1
    harness.poll()
    assert harness.runtime._track_key == ("普通朋友", "陶喆")
    assert len(port.requests) > requests


def test_absent_still_waits_for_the_grace():
    """absent（切歌抖动 / 播放器被关掉）不许跟暂停一样立刻停：宽限 + 确认拍照旧。"""
    harness = _Harness(
        {"music_lyric_enabled": False, "music_sing_grace_seconds": 6},
        playback=_playback(),
    )
    harness.poll()
    assert harness.presentation.animations == [(runtime_mod.SING_ANIM, True)]

    harness.sample = None
    harness.now += 1
    harness.poll()
    assert harness.presentation.released == 0
    harness.now += 3
    harness.poll()
    assert harness.presentation.released == 0
    harness.now += 3
    harness.poll()
    assert harness.presentation.released == 1


def test_lyric_failure_while_paused_is_silent_and_does_not_use_the_quota():
    port = FakeHttpPort(_lyric_handler(POSITION_LYRIC))
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        http=port,
        playback=_playback(),
    )
    calls: list[str] = []
    harness.runtime.fetch_lyrics = _failing_fetch(calls)
    pending: list = []
    harness.runtime.background = pending.append  # 取词停在"在飞"

    harness.poll()
    assert harness.runtime._fetch_attempts == 1
    assert calls == []  # 取词还在飞（work 还没跑）

    # 暂停之后失败结果才回来：静默，并且把配额退回去（暂停不是"这首取不到词"）。
    _paused(harness)
    pending[0]()
    assert calls == ["蝴蝶"]
    assert not [text for text in harness.presentation.said if "歌词获取失败" in text]
    assert harness.runtime._fetch_attempts == 0
    assert harness.runtime._lyric_retry_at is None

    # 恢复播放：照常再试一次；这一回在播，失败要冒泡，而且只冒一次。
    harness.runtime.background = lambda work: work()
    harness.sample = _playback(position=0.0)
    harness.now += 1
    harness.poll()
    assert harness.runtime._fetch_attempts == 1
    assert len([text for text in harness.presentation.said if "歌词获取失败" in text]) == 1

    # 冷却与上限照旧：冷却期内不重试，最多 LYRIC_MAX_ATTEMPTS 次。
    harness.now += 1
    harness.poll()
    assert harness.runtime._fetch_attempts == 1
    for _ in range(5):
        harness.now += runtime_mod.LYRIC_RETRY_SECONDS
        harness.poll()
    assert harness.runtime._fetch_attempts == runtime_mod.LYRIC_MAX_ATTEMPTS
    assert len([text for text in harness.presentation.said if "歌词获取失败" in text]) == 1


# ---------------------------------------------------------------- 展示会话


class _ScenePresentation(_LoopPresentation):
    """带展示会话闸门的替身：闸门语义与 Core 的 ``_PresentationAdapter`` 同款。"""

    def __init__(self, start: bool = True):
        super().__init__(start=start)
        self.scene = ""
        self.scene_calls: list[tuple[str, str]] = []
        self.owners: list[str] = []

    def begin_scene(self, owner):
        self.scene_calls.append(("begin", owner))
        self.scene = owner
        return True

    def end_scene(self, owner):
        self.scene_calls.append(("end", owner))
        if self.scene == owner:
            self.scene = ""

    def refresh_scene(self, owner):
        return self.scene == owner

    def say(self, text, *, duration_ms=4000, force=False, subtitle="", owner=""):
        if self.scene and not force and owner != self.scene:
            return False
        self.owners.append(owner)
        return super().say(text, duration_ms=duration_ms, force=force)


def test_lyrics_hold_the_scene_and_foreign_bubbles_do_not_interrupt():
    presentation = _ScenePresentation()
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        presentation=presentation,
        http=FakeHttpPort(_lyric_handler(POSITION_LYRIC)),
        playback=_playback(position=0.0),
    )
    harness.poll()
    assert presentation.said == ["第一句"]
    assert presentation.scene == runtime_mod.SCENE_OWNER == "media.music"
    assert presentation.owners[-1] == "media.music"

    # 歌词期间别人的自动气泡被拒，且歌词还在。
    assert presentation.say("自言自语一句") is False
    assert presentation.said == ["第一句"]

    harness.now += 2.0
    harness.sample = _playback(position=20.0)
    harness.poll()
    assert presentation.said == ["第一句", "第二句"]
    # 会话只申请一次（不每拍重入）。
    assert presentation.scene_calls.count(("begin", "media.music")) == 1

    harness.runtime.stop()
    assert presentation.scene == ""
    assert ("end", "media.music") in presentation.scene_calls


def test_scene_is_released_when_lyrics_stop_being_shown():
    presentation = _ScenePresentation()
    harness = _Harness(
        {"music_lyric_enabled": True, "music_sing_enabled": False},
        presentation=presentation,
        http=FakeHttpPort(_lyric_handler(POSITION_LYRIC)),
        playback=_playback(position=0.0),
    )
    harness.poll()
    assert presentation.scene == "media.music"

    # 关掉歌词显示：下一拍就要放开气泡（不能把自言自语永久锁死）。
    harness.ports.config.set("music_lyric_enabled", False)
    harness.now += 1
    harness.poll()
    assert presentation.scene == ""
    assert ("end", "media.music") in presentation.scene_calls


def test_scene_stays_held_while_a_rearm_is_pending():
    """唱歌的补播空档里不发第二次 begin：会话要一路连贯，别两拍一抖。"""
    presentation = _ScenePresentation()
    harness = _Harness(
        {"music_lyric_enabled": False, "music_sing_enabled": True},
        presentation=presentation,
        playback=_playback(),
    )
    harness.poll()
    assert presentation.scene == "media.music"
    assert presentation.loop is True

    # 循环被清掉、补播暂时失败（素材一时读不到）：会话不许放掉。
    presentation.start = False
    presentation.external_clear()
    harness.now += 1
    harness.poll()
    assert presentation.loop is False
    assert presentation.scene == "media.music"
    assert presentation.scene_calls.count(("begin", "media.music")) == 1

    # 素材回来：退避到期就重新唱，还是同一个会话。
    presentation.start = True
    for _ in range(5):
        harness.now += 1
        harness.poll()
    assert presentation.loop is True
    assert presentation.scene_calls.count(("begin", "media.music")) == 1
    assert ("end", "media.music") not in presentation.scene_calls
