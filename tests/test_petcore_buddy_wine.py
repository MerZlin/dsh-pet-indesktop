"""「朋友的酒模式」：音乐播放时持续 Q 弹，接管态不叠加形变。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_petcore_music_mod import (  # noqa: E402
    _Harness,
    _playback,
    runtime_mod,
)

from petcore.ui.pet_window import PetWindow  # noqa: E402


def _harness(enabled: bool, bpm: int | None = None) -> _Harness:
    config = {
        "music_buddy_wine_mode": enabled,
        "music_lyric_enabled": False,
        "music_sing_enabled": False,
    }
    if bpm is not None:
        config["music_buddy_wine_bpm"] = bpm
    return _Harness(config, playback=_playback(position=0.0))


def test_buddy_wine_squashes_on_the_default_132_bpm() -> None:
    harness = _harness(True)
    harness.poll()
    assert len(harness.presentation.squashes) == 1
    assert harness.scheduler.every[-1][1] == 455, "132 BPM 必须是独立 455ms 节拍"

    harness.now += 0.4  # 132 BPM ≈ 0.455s，还不到下一拍
    harness.scheduler.fire()
    assert len(harness.presentation.squashes) == 1

    harness.now += 0.1  # 累计 0.5s，超过 0.455s
    harness.scheduler.fire()
    assert len(harness.presentation.squashes) == 2

    harness.sample = _playback(playing=False)
    harness.now += 1.0
    harness.poll()
    assert not any(
        handle not in harness.scheduler.cancelled
        for handle, _interval, _callback in harness.scheduler.every
    ), "暂停必须取消 BPM 节拍器"
    harness.scheduler.fire()
    assert len(harness.presentation.squashes) == 2


def test_buddy_wine_bpm_controls_the_interval() -> None:
    slow = _harness(True, bpm=60)  # 1 秒一拍
    slow.poll()
    assert slow.scheduler.every[-1][1] == 1000
    slow.now += 0.5
    slow.scheduler.fire()
    assert len(slow.presentation.squashes) == 1
    slow.now += 0.6
    slow.scheduler.fire()
    assert len(slow.presentation.squashes) == 2

    fast = _harness(True, bpm=200)  # 0.3 秒一拍
    fast.poll()
    assert fast.scheduler.every[-1][1] == 300
    fast.now += 0.15
    fast.scheduler.fire()
    assert len(fast.presentation.squashes) == 1, "200 BPM 也不许被 1Hz 采样放大"
    fast.now += 0.16
    fast.scheduler.fire()
    assert len(fast.presentation.squashes) == 2


def test_buddy_wine_interval_helper_clamps_and_defaults() -> None:
    interval = runtime_mod.buddy_wine_interval_seconds

    assert interval(60) == 1.0
    assert interval(132) == pytest.approx(60 / 132)
    assert interval(200) == 0.3
    assert interval(0) == 1.0
    assert interval(999) == 0.3
    assert interval("bad") == pytest.approx(60 / 132)


def test_buddy_wine_off_sends_no_squash() -> None:
    harness = _harness(False)
    for _ in range(3):
        harness.poll()
        harness.now += 1.0
    assert harness.presentation.squashes == []


def test_pet_window_squash_skips_takeover_states() -> None:
    calls: list[tuple] = []
    effects = SimpleNamespace(
        throw_egg_active=False,
        start_squash=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    window = SimpleNamespace(
        dragging=False,
        probe_active=False,
        motion=SimpleNamespace(thrown=True),
        effects=effects,
    )
    assert PetWindow.squash(window) is False
    assert calls == []

    window.motion.thrown = False
    assert PetWindow.squash(window, 260, 2, 1.4) is True
    assert calls and calls[0][0] == (260,) and calls[0][1]["force"] is True


def test_buddy_wine_keys_are_public_in_settings_schema() -> None:
    from petcore.settings_schema import spec_for

    mode = spec_for("music_buddy_wine_mode")
    assert mode is not None
    assert mode.scope == "global"
    assert mode.type == "bool"
    assert mode.default is False

    bpm = spec_for("music_buddy_wine_bpm")
    assert bpm is not None
    assert bpm.scope == "global"
    assert bpm.type == "int"
    assert bpm.default == 132
    assert bpm.min == 60
    assert bpm.max == 200
