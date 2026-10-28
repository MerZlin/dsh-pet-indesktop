"""展示会话闸门：歌词这类连续展示占着气泡时，别人的自动气泡必须让路（离线）。

三块：
- 闸门本身（占用/重入/续期/过期/释放）；
- ``_PresentationAdapter`` 的接线（别人的 ``say/say_sequence`` 返回 False 且不弹，
  持有者带 ``owner`` 能显示，``force=True`` 仍能显示）；
- ``animation_state()`` 把"核心把循环清掉了/正被探头击飞接管"如实报给 mod，
  以及 ``media.music`` 运行时经**真实适配器**跑通的端到端链路。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from petcore.contracts.host import AnimationState, HostPorts
from petcore.ui import presentation_gate
from petcore.ui.app import _PresentationAdapter
from petcore.ui.presentation_gate import STALE_SECONDS, PresentationGate

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_gate_host"


def _mod_host():
    """按包加载 media.music 的 host（与 loader/生产同一套绑定方式）。"""
    if HOST_PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            HOST_PKG,
            MOD_DIR / "host" / "__init__.py",
            submodule_search_locations=[str(MOD_DIR / "host")],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[HOST_PKG] = module
        spec.loader.exec_module(module)
    return sys.modules[HOST_PKG]


@pytest.fixture(autouse=True)
def _clean_gate():
    presentation_gate.reset()
    yield
    presentation_gate.reset()


# ---------------------------------------------------------------- 闸门


def test_gate_allows_exactly_one_owner():
    gate = PresentationGate(clock=lambda: 100.0)
    assert gate.owner() == ""
    assert gate.allows("") is True
    assert gate.begin_scene("media.music") is True
    assert gate.owner() == "media.music"
    assert gate.allows("media.music") is True
    assert gate.allows("") is False
    assert gate.allows("fun.self-talk") is False
    # 被拒的一方抢不走会话，也不能把持有者挤掉。
    assert gate.begin_scene("fun.self-talk") is False
    assert gate.owner() == "media.music"
    gate.end_scene("media.music")
    assert gate.allows("fun.self-talk") is True


def test_gate_reentry_needs_matching_release():
    gate = PresentationGate(clock=lambda: 100.0)
    assert gate.begin_scene("media.music") is True
    assert gate.begin_scene("media.music") is True
    gate.end_scene("media.music")
    assert gate.owner() == "media.music"
    gate.end_scene("media.music")
    assert gate.owner() == ""


def test_gate_ignores_foreign_release_and_empty_owner():
    gate = PresentationGate(clock=lambda: 100.0)
    assert gate.begin_scene("") is False
    assert gate.begin_scene("media.music") is True
    gate.end_scene("fun.self-talk")
    assert gate.owner() == "media.music"
    gate.end_scene("")
    assert gate.owner() == "media.music"


def test_gate_expires_a_hold_that_stops_refreshing():
    now = [100.0]
    gate = PresentationGate(clock=lambda: now[0], stale_seconds=5.0)
    assert gate.begin_scene("media.music") is True
    now[0] += 5.0
    assert gate.refresh_scene("media.music") is True
    now[0] += 6.0
    # 超过 5 秒没续期：坏掉的 owner 不许永久静音桌宠。
    assert gate.owner() == ""
    assert gate.allows("fun.self-talk") is True
    assert STALE_SECONDS >= 30.0


def test_gate_refresh_reports_who_holds():
    gate = PresentationGate(clock=lambda: 100.0)
    assert gate.refresh_scene("media.music") is False
    assert gate.begin_scene("media.music") is True
    assert gate.refresh_scene("media.music") is True
    assert gate.refresh_scene("fun.self-talk") is False


def test_module_level_gate_is_process_wide():
    assert presentation_gate.begin_scene("media.music") is True
    assert presentation_gate.owner() == "media.music"
    assert presentation_gate.allows("media.music") is True
    assert presentation_gate.allows("") is False
    presentation_gate.end_scene("media.music")
    assert presentation_gate.owner() == ""


# ---------------------------------------------------------------- 适配器


class _Window:
    """足够真实的替身窗口：气泡记录 + 动画状态（循环标志/接管态）。"""

    def __init__(self):
        self.calls: list[str] = []
        self.sequences: list[list] = []
        self.images: list[str] = []
        self.animated: list[tuple[str, bool]] = []
        self.released = 0
        self._loop_active = False
        self.probe_active = False
        self.dragging = False
        self.state = SimpleNamespace(current=SimpleNamespace(name="悠闲哼歌"))
        self.motion = SimpleNamespace(thrown=False)
        self.effects = SimpleNamespace(throw_egg_active=False)

    def isVisible(self) -> bool:
        return True

    def say(self, text, duration_ms=4000, *, subtitle=""):
        self.calls.append(text)
        return True

    def say_sequence(self, lines, *, on_show=None):
        self.sequences.append(list(lines))
        for text, _subtitle, _duration in lines:
            if on_show is not None:
                on_show(text)
        return True

    # 与 PetWindow 同款语义：`_play_spec()` 是所有动画的入口，它先清循环标志。
    def play_animation(self, name, *, loop=False):
        self.animated.append((name, loop))
        self.state = SimpleNamespace(current=SimpleNamespace(name=name))
        self._loop_active = bool(loop)
        return True

    def release_loop(self):
        self.released += 1
        was = self._loop_active
        self._loop_active = False
        return was

    def play_event(self, group, variant=None):
        return False

    def show_image(self, path, caption="", duration_ms=4000, *, image_scale=1.0):
        self.images.append(str(path))
        return True


def _adapter(window):
    host = SimpleNamespace(
        _active_window=window,
        window=window,
        pets=None,
        quiet=None,
        settings_dialog=None,
        island=None,
    )
    return _PresentationAdapter(host)


def test_adapter_blocks_foreign_bubbles_during_a_scene():
    window = _Window()
    adapter = _adapter(window)

    assert adapter.begin_scene("media.music") is True
    # 自言自语：匿名调用，被拒且真的没弹（气泡内容不变）。
    assert adapter.say("自言自语一句") is False
    assert window.calls == []
    # 持有者带 owner 才能显示。
    assert adapter.say("第一句歌词", owner="media.music") is True
    assert window.calls == ["第一句歌词"]
    # 用户动作（点桌宠/菜单反馈）仍然可以显示。
    assert adapter.say("点击台词", force=True) is True
    assert window.calls == ["第一句歌词", "点击台词"]

    adapter.end_scene("media.music")
    assert adapter.say("自言自语一句") is True
    assert window.calls[-1] == "自言自语一句"


def test_adapter_blocks_sequences_from_other_owners():
    window = _Window()
    adapter = _adapter(window)

    assert adapter.begin_scene("media.music") is True
    assert adapter.say_sequence([("champion", "冠军", 1200)]) is False
    assert window.sequences == []
    assert adapter.say_sequence([("第一句", "", 1200)], owner="media.music") is True
    assert len(window.sequences) == 1
    assert adapter.say_sequence([("champion", "冠军", 1200)], force=True) is True
    assert len(window.sequences) == 2


def test_adapter_blocks_foreign_images_during_a_scene():
    window = _Window()
    adapter = _adapter(window)

    assert adapter.begin_scene("media.music") is True
    # 自言自语的配图同样要让路：图片气泡也会盖掉歌词。
    assert adapter.show_image("a.gif", "配图") is False
    assert window.images == []
    # 用户动作（点击台词的配图）用 force 压过去。
    assert adapter.show_image("b.gif", "点击配图", force=True) is True
    assert window.images == ["b.gif"]

    adapter.end_scene("media.music")
    assert adapter.show_image("c.gif") is True
    assert window.images == ["b.gif", "c.gif"]


def test_adapter_scene_is_shared_by_slot_views():
    window = _Window()
    adapter = _adapter(window)
    assert adapter.begin_scene("media.music") is True
    bound = adapter.for_window(window)
    assert bound.say("歌词", owner="media.music") is True
    assert bound.say("自语") is False
    adapter.end_scene("media.music")


def test_adapter_animation_state_reports_loop_and_lock():
    window = _Window()
    adapter = _adapter(window)

    assert adapter.animation_state() == AnimationState(name="悠闲哼歌", loop=False, locked=False)
    window._loop_active = True
    assert adapter.animation_state() == AnimationState(name="悠闲哼歌", loop=True, locked=False)

    window.probe_active = True
    assert adapter.animation_state().locked is True
    window.probe_active = False
    window.motion = SimpleNamespace(thrown=True)
    assert adapter.animation_state().locked is True
    window.motion = SimpleNamespace(thrown=False)
    window.dragging = True
    assert adapter.animation_state().locked is True
    window.dragging = False
    window.effects = SimpleNamespace(throw_egg_active=True)
    assert adapter.animation_state().locked is True


def test_adapter_animation_state_is_none_when_unreadable():
    class Bare:
        def isVisible(self) -> bool:
            return True

        def say(self, text, duration_ms=4000, *, subtitle=""):
            return True

    assert _adapter(Bare()).animation_state() is None


# ---------------------------------------------------------------- 端到端（mod × 真实适配器）


class _Config(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def _run_music(window, *, config=None, position=0.0, duration=180.0):
    """把 media.music 运行时接到**真实适配器 + 真实闸门**上跑一拍。"""
    host_mod = _mod_host()
    runtime_mod = importlib.import_module(f"{HOST_PKG}.runtime")
    now_playing = importlib.import_module(f"{HOST_PKG}.now_playing")
    lyrics_mod = importlib.import_module(f"{HOST_PKG}.lyrics")

    adapter = _adapter(window)
    ports = HostPorts(
        presentation=adapter,
        config=_Config(config or {"music_lyric_enabled": True, "music_sing_enabled": True}),
        http=SimpleNamespace(),
    )
    runtime = runtime_mod.MusicRuntime(SimpleNamespace(ports=ports))
    clock = [100.0]
    runtime.clock = lambda: clock[0]
    runtime.sample_smtc = lambda **_kwargs: now_playing.SmtcSample(
        playback=now_playing.Playback(
            title="蝴蝶",
            artist="陶喆",
            app_id="cloudmusic.exe",
            position=position,
            duration=duration,
            playing=True,
        )
    )
    runtime.sample_audio = lambda: ()
    runtime.sample_windows = lambda _targets: ()
    runtime.background = lambda work: work()
    runtime.fetch_lyrics = lambda *_args, **_kwargs: lyrics_mod.Lyrics(
        lines=(lyrics_mod.LyricLine(0.0, "第一句"),), instrumental=False
    )
    return runtime, adapter, clock


def test_music_lyrics_block_self_talk_through_the_real_adapter():
    window = _Window()
    runtime, adapter, _clock = _run_music(window)

    runtime.poll()
    assert window.calls == ["第一句"]
    assert presentation_gate.owner() == "media.music"

    # 自言自语走的正是这条路：匿名 + 非 force → 被拒，且歌词没被盖掉。
    assert adapter.say("自言自语一句") is False
    assert window.calls == ["第一句"]
    # 点击台词/命令反馈是 force=True，仍然可以显示。
    assert adapter.say("点击台词", force=True) is True

    runtime.stop()
    assert presentation_gate.owner() == ""
    assert adapter.say("自言自语一句") is True


def test_music_singing_rearms_through_the_real_adapter():
    window = _Window()
    runtime, adapter, clock = _run_music(
        window, config={"music_lyric_enabled": False, "music_sing_enabled": True}
    )

    runtime.poll()
    assert window.animated == [("悠闲哼歌", True)]
    assert adapter.animation_state().loop is True

    # 用户点了桌宠：核心改播点击动作并清掉循环标志。
    window.state = SimpleNamespace(current=SimpleNamespace(name="点击"))
    window._loop_active = False
    clock[0] += 1
    runtime.poll()
    assert window.animated == [("悠闲哼歌", True), ("悠闲哼歌", True)]

    # 探头接管期间不抢（此刻请求会被核心降级成待机）。
    window.probe_active = True
    window.state = SimpleNamespace(current=SimpleNamespace(name="待机"))
    window._loop_active = False
    for _ in range(4):
        clock[0] += 1
        runtime.poll()
    assert len(window.animated) == 2

    window.probe_active = False
    clock[0] += 1
    runtime.poll()
    assert window.animated[-1] == ("悠闲哼歌", True)
    runtime.stop()
    assert window.released == 1
