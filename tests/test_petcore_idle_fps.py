# -*- coding: utf-8 -*-
"""闲置降帧门控（2026-11-01 静息 CPU）。

现场：16 逻辑核机器上静息 2%（≈单核 32%），主要是 `playback_speed=2.0` 下
48fps 的待机帧解码 + 整窗缩放绘制。这里钉住门控：开关开、可见、idle 动作、
30 秒无交互才降帧；任何交互立即恢复；关闭开关不降。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
from petcore.ui.menu_model import MenuModel
from petcore.ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Host:
    def __init__(self, characters) -> None:
        self._characters = characters
        self.menu_model = MenuModel()

    def characters(self):
        return list(self._characters)

    def build_menu(self, parent=None):
        return self.menu_model.build(parent)

    def open_settings(self):
        return None

    def reload_mods(self):
        return None

    def open_data_dir(self):
        return None

    def show_about(self):
        return None

    def quit_app(self):
        return None


def _fallback() -> CharacterSpec:
    return CharacterSpec(
        id="fallback",
        name="Fallback",
        root=Path("."),
        media_kind="none",
        animations=(),
    )


class _SpyClip:
    def __init__(self) -> None:
        self.divisors: list[int] = []

    def set_frame_divisor(self, divisor: int) -> None:
        self.divisors.append(int(divisor))

    def stop(self) -> None:
        return None

    def clear_callbacks(self) -> None:
        return None


def _window(tmp_path: Path) -> PetWindow:
    character = _fallback()
    config = Config(tmp_path)
    config.set("character", character.id)
    host = _Host([character])
    window = PetWindow(config, tmp_path, host)
    window.start(character)
    window._idle_fps_timer.stop()
    window._stop_clip()
    return window


def test_idle_low_fps_toggle_defaults_to_on(tmp_path) -> None:
    assert Config(tmp_path).get("idle_low_fps_enabled") is True


def test_idle_low_fps_applies_after_threshold_and_resets_on_interaction(
    app, tmp_path
) -> None:
    window = _window(tmp_path)
    clip = _SpyClip()
    window._clip = clip
    window._action_kind = "idle"
    window.dragging = False
    window._idle_low_fps_active = False
    try:
        window._last_activity = time.monotonic()
        assert window._idle_low_fps_allowed() is False
        window._last_activity -= 31.0
        assert window._idle_low_fps_allowed() is True
        window._tick_idle_fps()
        assert clip.divisors == [2]
        window._note_activity()
        assert clip.divisors == [2, 1], "任何交互都必须立刻回全速"
    finally:
        window.shutdown()


def test_idle_low_fps_switch_off_disables_throttling(app, tmp_path) -> None:
    window = _window(tmp_path)
    window._clip = _SpyClip()
    window._action_kind = "idle"
    window._last_activity -= 31.0
    try:
        window.config.set("idle_low_fps_enabled", False)
        assert window._idle_low_fps_allowed() is False
        window.config.set("idle_low_fps_enabled", True)
        assert window._idle_low_fps_allowed() is True
    finally:
        window.shutdown()


def test_idle_low_fps_applies_to_self_play_but_not_dragging(app, tmp_path) -> None:
    """静息 = 用户无交互；随机自播动作也应降帧（同主分支），拖拽/锁屏除外。"""
    window = _window(tmp_path)
    window._clip = _SpyClip()
    window._last_activity -= 31.0
    try:
        window._action_kind = "act"
        assert window._idle_low_fps_allowed() is True
        window.dragging = True
        assert window._idle_low_fps_allowed() is False
        window.dragging = False
        window._paused_for_session = True
        assert window._idle_low_fps_allowed() is False
    finally:
        window.shutdown()


def test_idle_low_fps_still_applies_while_peeking_at_the_edge(app, tmp_path) -> None:
    """贴边探头是静息态：它只锁动画选择，不许把「闲置降帧」永久挡在门外。

    现场（2026-11-13，0.5.0 安装版）：宠物停在左缘 PEEKING，单核 40%+ 不下降；
    根因是 `_idle_low_fps_allowed()` 把 `_effects_lock_active()`（含探头）当成
    满速条件，探头一停几小时就一直是 48fps 解码。
    """
    window = _window(tmp_path)
    window._clip = _SpyClip()
    window._action_kind = "idle"
    window._last_activity -= 31.0
    window._edge_probe._mode = "PEEKING"  # noqa: SLF001 - 探头会话的静息态
    try:
        assert window.probe_active is True
        assert window._idle_low_fps_allowed() is True
        window.dragging = True
        assert window._idle_low_fps_allowed() is False, "拖拽仍然必须满速"
    finally:
        window._edge_probe._mode = "OFF"  # noqa: SLF001
        window.shutdown()



class _ThrottleOnlyClip:
    """WebM 式后端：只有 set_decode_throttle，没有真正的隔帧接口。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def set_decode_throttle(self, divisor: int) -> None:
        self.calls.append(int(divisor))

    def stop(self) -> None:
        return None

    def clear_callbacks(self) -> None:
        return None


class _SpeedClip:
    def __init__(self) -> None:
        self.speeds: list[float] = []

    def set_playback_speed(self, speed: float) -> None:
        self.speeds.append(float(speed))

    def stop(self) -> None:
        return None

    def clear_callbacks(self) -> None:
        return None


def test_webm_like_clip_is_not_treated_as_frame_throttling(app, tmp_path) -> None:
    window = _window(tmp_path)
    clip = _ThrottleOnlyClip()
    window._clip = clip
    window._action_kind = "idle"
    window._last_activity -= 31.0
    try:
        window._tick_idle_fps()
        assert clip.calls == [], "decode_throttle 会把时长拉长 2 倍，不许用于闲置降帧"
        assert window._idle_low_fps_active is False
    finally:
        window.shutdown()


def test_active_drive_loop_and_move_are_never_throttled(app, tmp_path) -> None:
    window = _window(tmp_path)
    window._clip = _SpyClip()
    window._last_activity -= 31.0
    try:
        window._drive_active = True
        assert window._idle_low_fps_allowed() is False
        window._drive_active = False
        window._loop_active = True
        assert window._idle_low_fps_allowed() is False
        window._loop_active = False
        window._move_plan = {"loops": 1}
        assert window._idle_low_fps_allowed() is False
        window._move_plan = None
        assert window._idle_low_fps_allowed() is True
    finally:
        window.shutdown()


def test_stopping_a_clip_resets_the_divisor_before_cache_reuse(app, tmp_path) -> None:
    window = _window(tmp_path)
    clip = _SpyClip()
    window._clip = clip
    window._action_kind = "idle"
    window._last_activity -= 31.0
    try:
        window._tick_idle_fps()
        assert clip.divisors == [2]
        window._stop_clip()
        assert clip.divisors == [2, 1], "LRU 复用的 clip 不许带着 divisor=2 离场"
    finally:
        window.shutdown()


def test_apply_playback_speed_reaches_the_current_clip(app, tmp_path) -> None:
    window = _window(tmp_path)
    clip = _SpeedClip()
    window._clip = clip
    try:
        window.apply_playback_speed(2.0)
        assert clip.speeds == [2.0]
    finally:
        window.shutdown()
