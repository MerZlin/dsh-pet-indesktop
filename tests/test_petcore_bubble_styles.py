# -*- coding: utf-8 -*-
"""气泡风格：原分支 5 种预设的配色/圆角/尾巴/水泡轮廓与热切换。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtWidgets import QApplication

from petcore.contracts.bubble_styles import (
    BUBBLE_STYLE_PRESETS,
    DEFAULT_BUBBLE_STYLE,
    STYLE_IDS,
    style_label,
)
from petcore.ui import bubble_style
from petcore.ui.bubble import SpeechBubble


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _sample(image, x: int, y: int):
    return image.pixelColor(max(0, x), max(0, y))


def test_presets_cover_the_five_original_styles() -> None:
    assert STYLE_IDS == (
        "classic_top",
        "paper_left",
        "glass_right",
        "soft_blue_top",
        "breath_bubble",
    )
    assert DEFAULT_BUBBLE_STYLE == "classic_top"
    assert style_label("glass_right") == "深色玻璃 · 右上方"
    assert {item["placement"] for item in BUBBLE_STYLE_PRESETS.values()} == {
        "top",
        "top_left",
        "top_right",
    }


def test_tail_side_points_back_at_the_anchor() -> None:
    anchor = QRect(400, 400, 100, 100)
    assert bubble_style.tail_side_for(anchor, QRect(380, 300, 140, 80)) == "bottom"
    assert bubble_style.tail_side_for(anchor, QRect(380, 520, 140, 80)) == "top"
    assert bubble_style.tail_side_for(anchor, QRect(250, 380, 120, 80)) == "right"
    assert bubble_style.tail_side_for(anchor, QRect(520, 380, 120, 80)) == "left"
    assert bubble_style.tail_side_for(anchor, QRect(410, 410, 60, 40)) == "none"


def test_surface_path_adds_the_tail_outside_the_body() -> None:
    body = QRectF(10, 10, 100, 60)
    plain = bubble_style.surface_path(body, 12)
    assert plain.boundingRect().height() == pytest.approx(60, abs=1.0)
    tail = bubble_style.surface_path(body, 12, tail_side="bottom", tail_depth=9)
    assert tail.boundingRect().bottom() > plain.boundingRect().bottom() + 8
    assert tail.contains(body.center())


def test_breath_bubble_uses_an_organic_outline() -> None:
    path = bubble_style.breath_main_bubble_path(QSize(240, 195))
    assert not path.isEmpty()
    bounds = path.boundingRect()
    assert bounds.width() > 180 and bounds.height() > 150
    assert bubble_style.is_breath("breath_bubble") is True
    assert bubble_style.is_breath("classic_top") is False


def test_default_bubble_uses_the_plain_rewrite_surface(app) -> None:
    """默认风格回到重写版：QSS 圆角矩形、无三角、窗口=可见卡片。"""
    bubble = SpeechBubble()
    try:
        bubble.set_style("classic_top")
        bubble.show_text("你好呀，今天也一起慢慢来吧。", QRect(400, 400, 120, 120))
        app.processEvents()
        assert "background:transparent" in bubble.text_panel.styleSheet()
        assert bubble.style_radius() == 12
        panel = bubble.text_panel.geometry()
        assert panel.left() == 0 and panel.top() == 0, "不再预留 10px 尾巴透明边"
        image = bubble.grab().toImage()
        color = _sample(image, panel.center().x(), panel.top() + 3)
        assert color.alpha() > 200
        assert (color.red(), color.green(), color.blue()) == (0xFF, 0xFF, 0xFF)
    finally:
        bubble.hide()
        bubble.deleteLater()


def test_plain_bubble_window_equals_the_visible_card_near_the_edge(app) -> None:
    screen = QApplication.primaryScreen().availableGeometry()
    anchor = QRect(screen.right() - 80, screen.top() + 120, 60, 80)
    bubble = SpeechBubble()
    try:
        bubble.set_style("classic_top")
        bubble.show_text(
            "这是一句会靠近屏幕右边缘的长气泡文本，用来复现贴边偏移。",
            anchor,
        )
        app.processEvents()
        panel = bubble.text_panel.geometry()
        assert panel.left() == 0 and panel.top() == 0
        assert bubble.width() == panel.width()
        assert bubble.height() == panel.height()
        assert bubble.geometry().right() <= screen.right()
        assert bubble.geometry().left() >= screen.left()
    finally:
        bubble.hide()
        bubble.deleteLater()


def test_other_styles_still_paint_their_own_surface(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.set_style("soft_blue_top")
        bubble.show_text("柔蓝", QRect(400, 400, 120, 120))
        app.processEvents()
        panel = bubble.text_panel.geometry()
        color = _sample(bubble.grab().toImage(), panel.center().x(), panel.top() + 3)
        assert (color.red(), color.green(), color.blue()) == (0xEE, 0xF6, 0xFF)
        assert "background:transparent" in bubble.text_panel.styleSheet()
    finally:
        bubble.hide()
        bubble.deleteLater()


def test_dark_preset_switches_text_and_surface_colors(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.set_style("glass_right")
        assert bubble.style_id() == "glass_right"
        assert "#f7f8fb" in bubble.label.styleSheet()
        bubble.show_text("深色玻璃", QRect(400, 400, 120, 120))
        app.processEvents()
        panel = bubble.text_panel.geometry()
        color = _sample(
            bubble.grab().toImage(), panel.center().x(), panel.top() + 3
        )
        assert (color.red(), color.green(), color.blue()) == (0x29, 0x2D, 0x36)
    finally:
        bubble.hide()
        bubble.deleteLater()


def test_scale_and_style_do_not_reset_each_other(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.set_style("soft_blue_top")
        bubble.set_text_scale(2.0)
        assert bubble.style_id() == "soft_blue_top"
        assert bubble.style_radius() == 32  # 16 × 2
        assert "font-size:26px" in bubble.label.styleSheet()
        bubble.set_style("paper_left")
        assert bubble.text_scale() == 2.0
        assert bubble.style_radius() == 22  # 11 × 2
    finally:
        bubble.deleteLater()


def test_unknown_style_falls_back_to_default(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.set_style("no-such-style")
        assert bubble.style_id() == DEFAULT_BUBBLE_STYLE
    finally:
        bubble.deleteLater()


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


def _fallback_character():
    from pathlib import Path

    from petcore.contracts.content import CharacterSpec

    return CharacterSpec(
        id="fallback",
        name="Fallback",
        root=Path("."),
        media_kind="none",
        animations=(),
    )


def test_pet_window_syncs_the_bubble_style_before_each_say(app, tmp_path) -> None:
    from petcore.config import Config
    from petcore.ui.pet_window import PetWindow

    character = _fallback_character()
    config = Config(tmp_path)
    config.set("self_talk_bubble_style", "glass_right")
    window = PetWindow(config, tmp_path, _Host([character]))
    window.start(character)
    try:
        window.set_visible(True)
        window.say("换风格", 800)
        assert window.bubble.style_id() == "glass_right"
        config.set("self_talk_bubble_style", "breath_bubble")
        window.say("再换一次", 800)
        assert window.bubble.style_id() == "breath_bubble"
    finally:
        window.shutdown()


def test_long_bubble_tail_points_at_the_anchor_and_stays_inside() -> None:
    """长气泡靠边：尾巴沿边投影到角色中心，并钳进圆角内（实机偏移修复）。"""
    anchor = QRect(300, 400, 120, 120)
    bubble = QRect(100, 300, 400, 80)
    side, at = bubble_style.tail_origin(anchor, bubble, radius=14, tail_half=7)
    assert side == "bottom"
    assert at == pytest.approx(anchor.center().x())
    assert 100 + 14 + 7 <= at <= 500 - 14 - 7

    # 角色在气泡右下角：尾巴钳到气泡右端，仍然朝角色而不是边中点。
    far = QRect(700, 400, 120, 120)
    side, at = bubble_style.tail_origin(far, bubble, radius=14, tail_half=7)
    assert side == "bottom"
    assert at == pytest.approx(bubble.right() - 14 - 7)
