# -*- coding: utf-8 -*-
"""死窗口/死消费方的帧路径回调防护（H1，offscreen）。

背景（全量套件实锤）：某个更早的用例泄漏了一只活着的 PetSprite + FrameSeqClip，
它继续收帧 → ``PetSprite._on_frame_changed`` → ``_notify_dirty`` → 回调落到
**已销毁**的 ShellOverlayWindow：``OverlayWindow._on_sprite_dirty`` 里的
``self.update`` / ``tick_advance`` 里的 ``self.update`` 抛
``RuntimeError: libshiboken: Internal C++ object (ShellOverlayWindow) already
deleted``，pytest 按 unraisable 判败（单独跑绿、全量红）。同一竞态在生产上也
成立：``overlay_shell`` 屏热插拔重建 overlay 窗时，在途帧交付打到旧窗。

手法：``shiboken6.delete(obj)`` 同步销毁 C++ 侧、Python 包装器留下 —— 等价于
「窗口已销毁但回调仍被持有」（tests/test_menu_layout.py、test_music_player_cache.py
同款），不需要赌时序，也不启动真实 QTimer。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import shiboken6
from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication

from pet.overlay_window import OverlayWindow
from pet.pet_sprite import PetSprite
from pet.tick_driver import TickDriver

app = QApplication.instance() or QApplication([])


class _Library:
    """PetSprite 取 clip 的最小库替身（本文件不播放动画，只需要协议面）。"""

    no_mirror: frozenset[str] = frozenset()

    def movie(self, _name, **_kw):
        return None


class _CountingOverlay(OverlayWindow):
    """记录 update 调用的 overlay：用于确认活窗口的正常路径没被守卫改掉。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.update_calls = 0
        self.update_regions: list[QRegion] = []

    def update(self, *args):
        self.update_calls += 1
        for arg in args:
            if isinstance(arg, QRegion):
                self.update_regions.append(QRegion(arg))
        super().update(*args)


def _sprite() -> PetSprite:
    return PetSprite(_Library(), pos=QPointF(10, 10), scale=0.5)


@pytest.fixture
def shared_driver():
    """进程级共享驱动器（产品壳形态：driver 不随窗口销毁）。"""
    driver = TickDriver()
    try:
        yield driver
    finally:
        driver.stop()


def test_sprite_frame_delivery_to_deleted_overlay_is_silent(shared_driver):
    """帧到达/位移回调打到已销毁的 overlay：不抛 RuntimeError（红的原始报错）。"""
    overlay = OverlayWindow(driver=shared_driver)
    sprite = _sprite()
    overlay.add_sprite(sprite)
    assert sprite._dirty_cb is not None       # 窗口确实挂上了回调
    assert shared_driver.overlays == [overlay]

    shiboken6.delete(overlay)                 # 窗口销毁，sprite 仍活着并收帧
    assert not shiboken6.isValid(overlay)

    # 真实回调链：帧到达直驱 → _notify_dirty → overlay._dirty_cb(self.update)
    sprite._on_frame_changed(0)
    # 位移通道（old != new 时先走驱动器档位、再 update）
    sprite.set_pos(QPointF(40, 40))
    # 直接调回调：绕开 sprite 侧的最后一道收窄（见
    # test_dirty_callback_runtime_error_is_narrowed），确保这里考的是 overlay
    # 自己的守卫——没有它这个调用点会抛 RuntimeError。
    rect = sprite.paint_bounds()
    sprite._dirty_cb(rect, rect)


def test_driver_tick_on_deleted_overlay_is_silent(shared_driver):
    """驱动器仍持有已销毁 overlay 成员时的每 tick 路径（tick_advance / 档位评估）。"""
    overlay = OverlayWindow(driver=shared_driver)
    sprite = _sprite()
    overlay.add_sprite(sprite)
    sprite._frame_dirty = True                # 让 tick 产生非空脏区（走到 update）

    shiboken6.delete(overlay)

    shared_driver.on_tick(0.016)              # 仿真段 → tick_advance → 档位评估
    overlay.tick_advance(0.016)
    overlay._note_kinetic()                   # sprite._kinetic_cb 通道
    shared_driver.on_tick(0.016)
    # 死窗口在档位评估里报「不可见」（而不是抛）：可见性访问器是死安全的
    assert overlay.isVisible() is False


def test_live_window_path_is_unchanged():
    """守卫不得改动活窗口语义：脏上报照旧 update，可见性照旧转发。"""
    overlay = _CountingOverlay()
    try:
        sprite = _sprite()
        overlay.add_sprite(sprite)
        overlay.update_calls = 0
        overlay.update_regions.clear()

        rect = sprite.paint_bounds()
        sprite._notify_dirty(rect, rect)
        assert overlay.update_calls == 1
        assert overlay.update_regions == [QRegion(rect)]

        sprite._frame_dirty = True
        overlay.tick_advance(0.016)
        assert overlay.update_calls == 2

        overlay._note_kinetic()               # 活窗口照旧进驱动器
        assert overlay.isVisible() is False   # 未 show 的窗口本就是 False
        overlay.show()
        assert overlay.isVisible() is True    # 可见性逐位转发 super()
    finally:
        overlay.close()


def test_dirty_callback_runtime_error_is_narrowed():
    """sprite 侧最后一道防线：消费方已死（RuntimeError）静默，其它异常照抛。"""
    sprite = _sprite()

    def _dead_consumer(_old, _new):
        raise RuntimeError("libshiboken: Internal C++ object already deleted.")

    sprite._dirty_cb = _dead_consumer
    sprite._notify_dirty(QRect(0, 0, 4, 4), QRect(0, 0, 4, 4))   # 不抛

    calls = []

    def _buggy_consumer(old, new):
        calls.append((old, new))
        raise ValueError("消费方自己的缺陷：必须外抛，不许被吞")

    sprite._dirty_cb = _buggy_consumer
    with pytest.raises(ValueError):
        sprite._notify_dirty(QRect(0, 0, 4, 4), QRect(0, 0, 4, 4))
    assert len(calls) == 1
