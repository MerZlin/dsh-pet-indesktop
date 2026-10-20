# -*- coding: utf-8 -*-
"""V-3/V-4 脏矩形契约回归（QT_QPA_PLATFORM=offscreen 可跑）。

背景（REVIEW_VERDICT.md V-3/V-4）：物理控制器在 before_sprites_advance
阶段、拖拽在鼠标事件里移动 sprite，而 advance() 的 old 取自自身入口——
old==new → 旧位置永不上报脏矩形 → 静态素材冻结 / 24fps 素材拖尾；且
重绘完全以 tick 为闸门，任何闲置降档都会把动画帧率打到 tick 频率。

契约：
1. 无论谁在何时移动 sprite（advance 内外），overlay 收到的脏区域必须
   同时覆盖移动前的旧 rect 与移动后的新 rect；
2. clip 帧到达（frameChanged）不经过 tick 也必须触发重绘。

纪律（AGENTS.md 时序测试）：不启动真实 QTimer，直接同步调 _on_tick；
素材用纯 QImage 假 clip（frameCount=1，永不自发 frameChanged）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QImage, QRegion
from PySide6.QtWidgets import QApplication

from pet.overlay_window import OverlayWindow
from pet.pet_sprite import INTERACTION_THROWN, PetSprite

app = QApplication.instance() or QApplication([])


class FakeClip(QObject):
    """静态假 clip：frameCount=1，测试内手动 emit frameChanged。"""

    frameChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return 1

    def start(self):
        return True

    def stop(self):
        pass


class FakeLibrary:
    def __init__(self, clip):
        self._clip = clip
        self.no_mirror: set[str] = set()

    def movie(self, name, **_kw):
        return self._clip


class RecordingOverlay(OverlayWindow):
    """把 update() 的累加区域记下来，不做真实绘制调度。"""

    def __init__(self):
        super().__init__()
        self.updated = QRegion()

    def update(self, *args):  # noqa: D102 - 测试替身
        for a in args:
            if isinstance(a, QRegion):
                self.updated |= a
            elif isinstance(a, QRect):
                self.updated |= QRegion(a)

    def reset(self):
        self.updated = QRegion()


def _make_sprite(pos=QPointF(100, 100), scale=0.5):
    clip = FakeClip()
    sprite = PetSprite(FakeLibrary(clip), pos=pos, scale=scale)
    sprite.bind_clip("idle")
    return sprite, clip


def _assert_region_covers(overlay: RecordingOverlay, rect: QRect):
    assert QRegion(rect).subtracted(overlay.updated).isEmpty(), (
        f"脏区域未覆盖 {rect}；已记录区域 bounds={overlay.updated.boundingRect()}"
    )


def test_physics_movement_reports_old_and_new_rect():
    """V-3：before_sprites_advance 阶段（抛掷物理）的移动必须上报旧+新 rect。"""
    overlay = RecordingOverlay()
    sprite, _clip = _make_sprite()
    overlay.add_sprite(sprite)
    sprite.interaction_state = INTERACTION_THROWN
    # 首 tick 消费 bind_clip 的首帧 _frame_dirty，之后素材无帧变化
    overlay._on_tick(dt=1 / 60)
    overlay.reset()

    old_rect = sprite.rect()

    def physics(dt):
        sprite.set_pos(sprite.pos + QPointF(80, 0))

    overlay.before_sprites_advance = physics
    overlay._on_tick(dt=1 / 60)

    new_rect = sprite.rect()
    assert old_rect != new_rect, "前提：物理控制器确实移动了 sprite"
    _assert_region_covers(overlay, old_rect)
    _assert_region_covers(overlay, new_rect)


def test_drag_movement_between_ticks_reports_old_and_new_rect():
    """V-3：tick 之间（拖拽鼠标事件）的移动同样必须上报旧+新 rect。"""
    overlay = RecordingOverlay()
    sprite, _clip = _make_sprite()
    overlay.add_sprite(sprite)
    overlay._on_tick(dt=1 / 60)
    overlay.reset()

    old_rect = sprite.rect()
    sprite.set_pos(sprite.pos + QPointF(60, 40))  # 事件侧移动（on_move 同路径）
    overlay._on_tick(dt=1 / 60)

    new_rect = sprite.rect()
    assert old_rect != new_rect
    _assert_region_covers(overlay, old_rect)
    _assert_region_covers(overlay, new_rect)


def test_frame_changed_repaints_without_tick():
    """V-4：帧到达直驱重绘，不经过 tick（闲置降档的前置）。"""
    overlay = RecordingOverlay()
    sprite, clip = _make_sprite()
    overlay.add_sprite(sprite)
    overlay._on_tick(dt=1 / 60)
    overlay.reset()

    clip.frame = 1
    clip.frameChanged.emit(1)

    _assert_region_covers(overlay, sprite.rect())
