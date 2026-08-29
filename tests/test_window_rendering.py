# -*- coding: utf-8 -*-
"""窗口渲染 / 角色区域 / Windows 逐像素命中测试。"""
from __future__ import annotations

import os
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication

from pet import window as window_mod
from pet import catalog


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _alpha_pixmap(width: int = 64, height: int = 36) -> QPixmap:
    """左半不透明、右半透明的合成帧。"""
    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(0, 0, width // 2, height, QColor(255, 255, 255, 255))
    p.end()
    return pm


class _FakePet:
    """最小 fake 对象，只包含渲染相关方法所需的属性。"""

    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect
    _sync_mask = window_mod.PetWindow._sync_mask
    _is_transparent_at = window_mod.PetWindow._is_transparent_at
    character_local_region = window_mod.PetWindow.character_local_region

    def __init__(self, scale: float = 0.1) -> None:
        self.scale = scale
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._squash_active = False
        self._squash_progress = 1.0
        self._frame_pixmap = None
        self._mask_bounds = None
        self._hit_alpha_image = None


def _fake_pet(scale: float = 0.1) -> _FakePet:
    return _FakePet(scale)


def test_sync_mask_updates_bounds_and_respects_platform(monkeypatch):
    _qapp()
    fake = _fake_pet()
    fake._frame_pixmap = _alpha_pixmap()
    mask_sets = []

    def set_mask(mask):
        mask_sets.append(mask)

    fake.setMask = set_mask

    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="posix"))
    window_mod.PetWindow._sync_mask(fake)
    assert fake._mask_bounds is not None
    assert not fake._mask_bounds.isEmpty()
    assert mask_sets, "非 Windows 仍应使用 setMask"

    # Windows 路径：不再 setMask；已有旧 mask 时清理一次
    mask_sets.clear()
    clear_calls = []
    fake.clearMask = lambda: clear_calls.append(True)
    fake.mask = lambda: QRegion(0, 0, 10, 10)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    window_mod.PetWindow._sync_mask(fake)
    assert not mask_sets, "Windows 不应再调用 setMask"
    assert clear_calls, "Windows 上存在旧 mask 时应 clearMask"


def test_is_transparent_at_uses_frame_alpha():
    _qapp()
    fake = _fake_pet(scale=0.1)
    fake._frame_pixmap = _alpha_pixmap()
    fake._hit_alpha_image = None

    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(10, 10)) is False
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(50, 10)) is True
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(10, 2)) is True


def test_visible_content_rect_uses_character_region():
    _qapp()

    class FakeWithGeometry(_FakePet):
        def frameGeometry(self):
            return QRect(100, 100, self._w, self._h)

        def mask(self):
            return QRegion()

    obj = FakeWithGeometry()
    obj._mask_bounds = QRect(4, 3, 20, 20)

    rect = window_mod.PetWindow.visible_content_rect(obj)
    assert rect == QRect(104, 103, 20, 20)


class _FakeBubble:
    def __init__(self) -> None:
        self.hidden = False
        self.shown_text = []

    def hide(self) -> None:
        self.hidden = True

    def show_text(self, text, *args, **kwargs) -> None:
        self.shown_text.append(text)

    def show_image(self, *args, **kwargs) -> None:
        self.shown_text.append("<image>")


class _FakeBubblePet:
    def __init__(self) -> None:
        self._bubble_suppressed = False
        self._speech_bubble = _FakeBubble()
        self.scale = 1.0
        self._self_talk_texts = ["你好"]
        self._self_talk_images = []

    def isVisible(self) -> bool:  # noqa: N802 - Qt 命名
        return True

    def hold_bubble(self, seconds: float) -> None:
        pass

    def visible_content_rect(self) -> QRect:
        return QRect(0, 0, 100, 100)


def test_bubble_suppression_skips_bubbles_while_settings_open():
    _qapp()
    pet = _FakeBubblePet()

    window_mod.PetWindow.set_bubble_suppressed(pet, True)
    assert pet._bubble_suppressed is True
    assert pet._speech_bubble.hidden is True

    window_mod.PetWindow.show_bubble(pet, "不应显示")
    assert pet._speech_bubble.shown_text == []

    assert window_mod.PetWindow._show_random_self_talk(pet) is False
    assert pet._speech_bubble.shown_text == []

    window_mod.PetWindow.set_bubble_suppressed(pet, False)
    window_mod.PetWindow.show_bubble(pet, "恢复显示")
    assert pet._speech_bubble.shown_text == ["恢复显示"]
