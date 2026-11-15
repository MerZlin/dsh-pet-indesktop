# -*- coding: utf-8 -*-
"""灵动岛全屏隐藏：探测器信号、宿主状态机与「只恢复自己藏的那次」。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.ui.app import CoreHost
from petcore.ui.fullscreen_hide import FullscreenHideController
from petcore.ui.pet_manager import PetManager


class FakePet:
    def __init__(self, slot: int) -> None:
        self.slot = slot
        self.visible = True
        self.calls: list[bool] = []

    def isVisible(self) -> bool:
        return self.visible

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)
        self.calls.append(bool(visible))


class FakeProbe:
    def __init__(self, hit: bool) -> None:
        self.hit = hit
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return (self.hit, "test")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _controller(pets, *, confirm_polls: int = 1) -> FullscreenHideController:
    return FullscreenHideController(
        lambda: pets,
        probe=FakeProbe(False),
        supported=True,
        interval_ms=60_000,
        confirm_polls=confirm_polls,
    )


def test_signal_marks_enter_and_leave(app) -> None:
    pet = FakePet(0)
    ctrl = _controller([pet])
    seen: list[bool] = []
    ctrl.fullscreen_changed.connect(seen.append)
    try:
        ctrl.set_enabled(True)
        ctrl._probe = FakeProbe(True)  # noqa: SLF001
        ctrl.poll()
        assert pet.visible is False
        ctrl._probe = FakeProbe(False)  # noqa: SLF001
        ctrl.poll()
        assert pet.visible is True
        assert seen == [True, False]
    finally:
        ctrl.stop()


def test_hide_pets_off_restores_without_claiming_fullscreen_ended(app) -> None:
    pet = FakePet(0)
    ctrl = _controller([pet])
    seen: list[bool] = []
    ctrl.fullscreen_changed.connect(seen.append)
    try:
        ctrl.set_enabled(True)
        ctrl._probe = FakeProbe(True)  # noqa: SLF001
        ctrl.poll()
        assert pet.visible is False and seen == [True]
        ctrl.set_hide_pets(False)
        assert pet.visible is True, "关掉桌宠隐藏要立刻把宠放出来"
        assert seen == [True], "仍然在全屏里，不许对岛谎报「全屏结束」"
    finally:
        ctrl.stop()


def test_disabling_the_detector_reports_leave(app) -> None:
    pet = FakePet(0)
    ctrl = _controller([pet])
    seen: list[bool] = []
    ctrl.fullscreen_changed.connect(seen.append)
    try:
        ctrl.set_enabled(True)
        ctrl._probe = FakeProbe(True)  # noqa: SLF001
        ctrl.poll()
        ctrl.set_enabled(False)
        assert seen == [True, False]
        assert pet.visible is True
    finally:
        ctrl.stop()


# ------------------------------------------------------------------ 宿主接线


def _host(app, tmp_path: Path, **settings):
    from petcore.content import collect

    original = collect.load_all
    collect.load_all = lambda **_kw: []  # type: ignore[assignment]
    try:
        config = Config(tmp_path)
        config.set("island_enabled", True)
        config.set("auto_hide_fullscreen", False)
        for key, value in settings.items():
            config.set(key, value)
        host = CoreHost(app, config, tmp_path, ())
        manager = PetManager(host, tmp_path, config)
        host.pets = manager
        character = host.character_for("fallback")
        host.window = manager.create_primary(character)
        host.attach_window(host.window)
        host.apply_island_settings()
        host.start_fullscreen_hide()
        return host, manager
    finally:
        collect.load_all = original  # type: ignore[assignment]


def _poll(host, hit: bool, times: int = 2) -> None:
    controller = host.fullscreen_hide
    controller._probe = FakeProbe(hit)  # noqa: SLF001
    controller._confirm_polls = 1  # noqa: SLF001
    for _ in range(times):
        controller.poll()


def test_island_hides_in_fullscreen_and_comes_back(app, tmp_path: Path) -> None:
    host, manager = _host(app, tmp_path, island_hide_fullscreen=True)
    island = host.island
    try:
        assert island.isVisible() is True
        _poll(host, True)
        assert island.isVisible() is False
        assert host._island_hidden_by_fullscreen is True  # noqa: SLF001
        # 全屏期间设置页重放：不许把岛拉回画面
        host.apply_island_settings()
        assert island.isVisible() is False
        _poll(host, False)
        assert island.isVisible() is True
        assert host._island_hidden_by_fullscreen is False  # noqa: SLF001
    finally:
        island.stop()
        island.deleteLater()
        host.stop_fullscreen_hide()
        manager.shutdown()


def test_island_stays_visible_when_the_setting_is_off(app, tmp_path: Path) -> None:
    host, manager = _host(app, tmp_path, island_hide_fullscreen=False)
    island = host.island
    try:
        _poll(host, True)
        assert island.isVisible() is True
    finally:
        island.stop()
        island.deleteLater()
        host.stop_fullscreen_hide()
        manager.shutdown()


def test_island_setting_alone_keeps_the_detector_running(app, tmp_path: Path) -> None:
    host, manager = _host(
        app, tmp_path, auto_hide_fullscreen=False, island_hide_fullscreen=True
    )
    try:
        assert host.fullscreen_hide.timer_active() is True, (
            "关掉桌宠隐藏但勾上岛隐藏时，探测器仍要为岛开着"
        )
    finally:
        island = host.island
        if island is not None:
            island.stop()
            island.deleteLater()
        host.stop_fullscreen_hide()
        manager.shutdown()
