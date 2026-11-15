# -*- coding: utf-8 -*-
"""工作状态动画锁：working 只播 work 组，无素材回退待机，结束恢复行为链。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.engine.enums import AnimatType, GraphType, ModeType
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


def _spec(name: str, graph: GraphType) -> AnimationSpec:
    return AnimationSpec(
        name=name,
        graph=graph,
        animat=AnimatType.B_LOOP,
        mode=ModeType.NOMAL,
        path=None,
        locate="ground",
        loop=True,
    )


def _character(*, with_work: bool) -> CharacterSpec:
    animations = [_spec("idle-loop", GraphType.IDEL)]
    if with_work:
        animations.append(_spec("work-loop", GraphType.WORK))
    return CharacterSpec(
        id="work-test",
        name="Work Test",
        root=Path("."),
        media_kind="none",
        animations=tuple(animations),
    )


def _window(tmp_path: Path, *, with_work: bool) -> PetWindow:
    character = _character(with_work=with_work)
    config = Config(tmp_path)
    config.set("character", character.id)
    window = PetWindow(config, tmp_path, _Host([character]))
    window.start(character)
    window._idle_fps_timer.stop()  # noqa: SLF001 - 测试不跑后台节拍
    window._stop_clip()  # noqa: SLF001
    window.dragging = False
    return window


def test_working_locks_to_the_work_clip(app, tmp_path: Path) -> None:
    window = _window(tmp_path, with_work=True)
    calls: list[tuple[str, str]] = []
    window._play_spec = lambda spec, kind: calls.append((spec.name, kind))  # type: ignore[method-assign]
    try:
        assert window._work_lock_active() is False  # noqa: SLF001
        window.set_work_state("working")
        assert window._work_state == "working"  # noqa: SLF001
        assert window._work_lock_active() is True  # noqa: SLF001
        assert calls == [("work-loop", "work")]

        calls.clear()
        window._roll_next()  # noqa: SLF001
        assert calls == [("work-loop", "work")], "工作期间随机动作必须被 work 组替换"
    finally:
        window.shutdown()


def test_working_without_work_clips_keeps_idle(app, tmp_path: Path) -> None:
    window = _window(tmp_path, with_work=False)
    idle_calls: list[bool] = []
    window._play_idle = lambda: idle_calls.append(True)  # type: ignore[method-assign]
    try:
        window._action_kind = "move"  # noqa: SLF001
        window.set_work_state("working")
        assert idle_calls == [True], "没有 work 素材时不能伪造工作动画"
        assert window._work_lock_active() is True  # noqa: SLF001

        idle_calls.clear()
        window._roll_next()  # noqa: SLF001
        assert idle_calls == [True]
    finally:
        window.shutdown()


def test_work_lock_can_be_disabled_and_released(app, tmp_path: Path) -> None:
    window = _window(tmp_path, with_work=True)
    window.config.set("work_animations_only", False)
    calls: list[tuple[str, str]] = []
    window._play_spec = lambda spec, kind: calls.append((spec.name, kind))  # type: ignore[method-assign]
    try:
        window.set_work_state("working")
        assert window._work_lock_active() is False  # noqa: SLF001
        assert calls == [], "关掉开关后 working 不改变当前动画"

        window.config.set("work_animations_only", True)
        window.set_work_state("done")
        assert window._work_state == "done"  # noqa: SLF001
        assert window._work_lock_active() is False  # noqa: SLF001
    finally:
        window.shutdown()


def test_work_lock_does_not_override_drag_or_probe(app, tmp_path: Path) -> None:
    window = _window(tmp_path, with_work=True)
    try:
        window.set_work_state("working")
        window.dragging = True
        assert window._work_lock_active() is False  # noqa: SLF001
        window.dragging = False
        window._edge_probe._mode = "PEEKING"  # noqa: SLF001 - 进入探头接管态
        assert window._work_lock_active() is False  # noqa: SLF001
    finally:
        window.shutdown()
