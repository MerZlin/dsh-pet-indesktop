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
