"""petcore 绘制路径：镜像在 painter 上做，Q 弹矩形仍从底边绘制。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
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


def _fallback() -> CharacterSpec:
    return CharacterSpec(
        id="fallback", name="Fallback", root=Path("."),
        media_kind="none", animations=(),
    )


def _window(tmp_path):
    config = Config(tmp_path)
    window = PetWindow(config, tmp_path, _Host([_fallback()]))
    window.start(_fallback())
    return window


def _red_block(window, width: int | None = None) -> QPixmap:
    pixmap = QPixmap(window.width(), window.height())
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.fillRect(
        QRect(0, 0, width or window.width(), window.height()),
        QColor(255, 0, 0, 255),
    )
    painter.end()
    window._pixmap = pixmap
    window._image = pixmap.toImage()
    return pixmap


def test_facing_right_mirrors_at_paint_time(app, tmp_path) -> None:
    window = _window(tmp_path)
    try:
        _red_block(window, width=8)
        window.state.facing = "right"
        shot = window.grab().toImage()
        assert shot.pixelColor(window.width() - 4, window.height() // 2).red() > 200
        assert shot.pixelColor(4, window.height() // 2).alpha() == 0
    finally:
        window.shutdown()


def test_squash_rect_is_drawn_from_the_bottom(app, tmp_path) -> None:
    window = _window(tmp_path)
    try:
        _red_block(window)
        window.state.facing = "left"
        window.effects._squash_active = True
        window.effects._squash_started = window.effects._clock() - 25.0
        window.effects._squash_duration_ms = 100000.0
        window.effects._squash_bounces = 1
        window.effects._squash_strength = 1.0
        shot = window.grab().toImage()
        assert shot.pixelColor(window.width() // 2, window.height() - 2).red() > 200
        assert shot.pixelColor(window.width() // 2, 0).alpha() == 0
    finally:
        window.effects.reset()
        window.shutdown()
