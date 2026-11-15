# -*- coding: utf-8 -*-
"""动画朝向：每组素材原生朝向可配置（自定义角色左右反了的修复口）。

素材约定默认原生朝左（``facing == "right"`` 才镜像）；生成出来就朝右的角色把
``anim_facing_default`` 改成 ``right``，个别组不一样时写 ``anim_facing_groups``。
这里钉住判定、组映射、文字素材例外，以及绘制/命中的一致性。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.engine.enums import AnimatType, GraphType
from petcore.ui.pet_window import PetWindow


class _Host:
    def __init__(self, characters):
        self._characters = characters

    def characters(self):
        return list(self._characters)

    def build_menu(self, parent=None):
        return None

    def open_settings(self, initial_page=None):
        return None

    def reload_mods(self):
        return None

    def open_data_dir(self):
        return None

    def quit_app(self):
        return None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _character() -> CharacterSpec:
    return CharacterSpec(
        id="facing",
        name="Facing",
        root=Path("."),
        media_kind="none",
        animations=(
            AnimationSpec("idle", GraphType.IDEL, AnimatType.B_LOOP),
            AnimationSpec("walk", GraphType.MOVE, AnimatType.B_LOOP),
            AnimationSpec("click", GraphType.TOUCH_BODY, AnimatType.SINGLE),
            AnimationSpec("drag", GraphType.RAISED_STATIC, AnimatType.SINGLE),
            AnimationSpec("laugh", GraphType.EVENT, AnimatType.SINGLE),
            AnimationSpec("caption", GraphType.EVENT, AnimatType.SINGLE),
        ),
        text_clips=frozenset({"caption"}),
    )


def _window(tmp_path, **config_values) -> PetWindow:
    config = Config(tmp_path)
    for key, value in config_values.items():
        config.set(key, value)
    character = _character()
    window = PetWindow(config, tmp_path, _Host([character]))
    window.start(character)
    window.character = character
    return window


def _use(window, name: str, kind: str) -> None:
    spec = next(item for item in window.character.animations if item.name == name)
    window.state.current = spec
    window._action_kind = kind


def _red_block(window) -> None:
    pixmap = QPixmap(window.width(), window.height())
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.fillRect(QRect(0, 0, 8, window.height()), QColor(255, 0, 0, 255))
    painter.end()
    window._pixmap = pixmap
    window._image = pixmap.toImage()


# ------------------------------------------------------------------ 判定


def test_native_left_mirrors_when_facing_right(app, tmp_path) -> None:
    window = _window(tmp_path)
    try:
        _use(window, "idle", "idle")
        window.state.facing = "left"
        assert window._mirrored() is False
        window.state.facing = "right"
        assert window._mirrored() is True, "默认素材朝左：朝右必须镜像"
    finally:
        window.shutdown()


def test_master_switch_flips_a_natively_right_character(app, tmp_path) -> None:
    window = _window(tmp_path, anim_facing_default="right")
    try:
        _use(window, "idle", "idle")
        window.state.facing = "right"
        assert window._mirrored() is False, "原生朝右的角色朝右时不该镜像"
        window.state.facing = "left"
        assert window._mirrored() is True
    finally:
        window.shutdown()


def test_group_override_only_affects_its_own_group(app, tmp_path) -> None:
    window = _window(tmp_path, anim_facing_groups={"drag": "right"})
    try:
        _use(window, "drag", "drag")
        window.state.facing = "right"
        assert window._mirrored() is False, "拖拽组已声明原生朝右"
        window.state.facing = "left"
        assert window._mirrored() is True
        _use(window, "idle", "idle")
        window.state.facing = "right"
        assert window._mirrored() is True, "其它组仍跟随默认（朝左）"
    finally:
        window.shutdown()


def test_head_pat_and_throw_map_onto_click_and_drag(app, tmp_path) -> None:
    window = _window(tmp_path, anim_facing_groups={"click": "right", "drag": "right"})
    try:
        _use(window, "click", "head_pat")
        window.state.facing = "right"
        assert window._mirrored() is False
        _use(window, "drag", "throw")
        window.state.facing = "right"
        assert window._mirrored() is False
        _use(window, "laugh", "act")
        window.state.facing = "right"
        assert window._mirrored() is True
    finally:
        window.shutdown()


def test_group_falls_back_to_graph_when_kind_is_unknown(app, tmp_path) -> None:
    window = _window(tmp_path, anim_facing_groups={"move": "right"})
    try:
        _use(window, "walk", None)
        window.state.facing = "right"
        assert window._mirrored() is False
    finally:
        window.shutdown()


def test_text_clips_never_mirror(app, tmp_path) -> None:
    window = _window(tmp_path, anim_facing_default="right")
    try:
        _use(window, "caption", "act")
        window.state.facing = "left"
        assert window._mirrored() is False, "含可读文字的素材永不镜像"
    finally:
        window.shutdown()


# ------------------------------------------------------------------ 绘制 / 命中


def test_paint_and_hit_test_follow_the_native_facing(app, tmp_path) -> None:
    window = _window(tmp_path, anim_facing_default="right")
    try:
        _red_block(window)
        _use(window, "idle", "idle")
        window.state.facing = "left"  # 原生朝右 → 镜像，红色块应翻到右侧
        shot = window.grab().toImage()
        assert shot.pixelColor(window.width() - 4, window.height() // 2).red() > 200
        assert shot.pixelColor(4, window.height() // 2).alpha() == 0
        assert window.is_transparent_at(QPoint(4, window.height() // 2)) is True
        assert (
            window.is_transparent_at(QPoint(window.width() - 4, window.height() // 2))
            is False
        )
    finally:
        window.shutdown()


# ------------------------------------------------------------------ 配置


def test_facing_config_defaults_and_normalization(tmp_path) -> None:
    config = Config(tmp_path)
    assert config.get("anim_facing_default") == "left"
    assert config.get("anim_facing_groups") == {}
    config.set("anim_facing_default", "RIGHT")
    config.set(
        "anim_facing_groups",
        {"idle": "right", "drag": "left", "bogus": "right", "move": "sideways"},
    )
    assert config.get("anim_facing_default") == "right"
    assert config.get("anim_facing_groups") == {"idle": "right", "drag": "left"}, (
        "未知组与非法值必须被归一化丢掉，不留死项"
    )
