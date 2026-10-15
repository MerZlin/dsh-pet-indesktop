# -*- coding: utf-8 -*-
"""V-5~V-15 overlay/sprite 硬化回归（QT_QPA_PLATFORM=offscreen 可跑）。

覆盖（REVIEW_VERDICT.md 小修批）：
- V-6  tick 间隔公式（90Hz 边界）与刷新率重读入口；
- V-7  _press_global / click-only grab 丢失 release 的看门狗（全屏吞点击兜底）；
- V-8  remove_sprite 释放 clip（release_clip=False 保留）+ 移除通知；
- V-9  _cats_cache 弱键（库销毁自动回收）；
- V-10 scale property（置脏/重钳/上报）；
- V-12 closeEvent 停 tick timer；
- V-13 拖拽中被非左键打断 → 合成 release 收尾。**弹弓刀调和**：右键在
  ``slingshot_enabled`` 为真时改为「进蓄力瞄准」（不再合成 release），
  其余非左键（中键等）与弹弓关闭时维持原语义——瞄准有明确退出路径
  （松左键发射 / Esc / 再点右键取消 / 左键丢失看门狗），不会卡 drag 态。

纪律（AGENTS.md 时序测试）：同步直调 handler，不 sleep 赌时序；
素材用纯 QImage 假 clip。
"""
from __future__ import annotations

import gc
import os
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QMouseEvent, QRegion
from PySide6.QtWidgets import QApplication

import pet.overlay_window as overlay_window
from pet.overlay_window import OverlayWindow
from pet.pet_sprite import INTERACTION_DRAG, INTERACTION_NORMAL, PetSprite
from pet.sprite_behavior import BehaviorController

app = QApplication.instance() or QApplication([])


class FakeClip(QObject):
    frameChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)
        self.started = False

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return 1

    def start(self):
        self.started = True
        return True

    def stop(self):
        self.started = False


class FakeLibrary:
    def __init__(self, clip=None):
        self._clip = clip or FakeClip()
        self.no_mirror: set[str] = set()
        self.idles = ["idle"]
        self.turns: list = []
        self.moves: list = []
        self.clicks: list = []

    def movie(self, name):
        return self._clip


def _make_sprite(pos=QPointF(100, 100), scale=0.5):
    clip = FakeClip()
    sprite = PetSprite(FakeLibrary(clip), pos=pos, scale=scale)
    sprite.bind_clip("idle")
    return sprite, clip


# ---------------------------------------------------------------- V-6
def test_tick_interval_boundaries():
    assert OverlayWindow._tick_interval_ms(170.0) == 6
    assert OverlayWindow._tick_interval_ms(144.0) == 7
    assert OverlayWindow._tick_interval_ms(90.0) == 11   # 旧边界错打成 16
    assert OverlayWindow._tick_interval_ms(75.0) == 13
    assert OverlayWindow._tick_interval_ms(60.0) == 16
    assert OverlayWindow._tick_interval_ms(0.0) == 16


def test_show_event_rereads_refresh_rate():
    from PySide6.QtGui import QShowEvent

    overlay = OverlayWindow()
    overlay._timer.setInterval(999)
    overlay.showEvent(QShowEvent())
    assert overlay._timer.interval() != 999


# ---------------------------------------------------------------- V-7
class FakeGrab:
    def __init__(self):
        self.released = False

    def on_release(self, _pos):
        self.released = True


def test_stale_press_watchdog_clears_dead_grab():
    overlay = OverlayWindow()
    grab = FakeGrab()
    overlay._mouse_grab = grab
    overlay._press_global = QPoint(5, 5)
    # 测试环境无真实按键：mouseButtons() 必为 NoButton → 看门狗应收尾
    assert not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
    overlay._check_stale_press()
    assert overlay._press_global is None
    assert overlay._mouse_grab is None
    assert grab.released is True


def test_stale_press_watchdog_noop_when_idle():
    overlay = OverlayWindow()
    overlay._check_stale_press()  # 无 press：不得抛异常
    assert overlay._press_global is None


def test_stale_press_watchdog_clears_click_only_grab():
    """click-only 按下（锁定位 / 未按 SHIFT）不记 ``_press_global``，丢 release
    时同样必须被看门狗收尾。

    卡住的 click-only grab 在生产里等价于「拖拽在途」：壳层
    ``_cursor_transition_blocked()`` 恒真 → 光标恢复的 SHOWING 只被滞留
    （``_cursor_restore_pending``）→ 光标隐藏态触发的整窗穿透再也退不出去，
    窗口可见却点不到，而点击本身又被穿透吃掉（无法自愈）。
    """
    overlay = OverlayWindow()
    grab = FakeGrab()
    overlay._mouse_grab = grab
    overlay._press_click_only = True      # 点击候选：不留 _press_global
    assert overlay._press_global is None
    # 测试环境无真实按键：mouseButtons() 必为 NoButton → 看门狗应收尾
    assert not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
    overlay._check_stale_press()
    assert overlay._mouse_grab is None
    assert overlay._press_click_only is False
    assert grab.released is True


class _HeldLeftButton:
    """替身：只提供 ``mouseButtons()``（测试环境造不出真实的按住状态）。"""

    @staticmethod
    def mouseButtons():
        return Qt.MouseButton.LeftButton


def test_stale_press_watchdog_keeps_held_click_only_grab(monkeypatch):
    """左键仍按下时不得收尾：click-only 的按住是合法的长按（锁定位置下
    用户可能一直按着没松）。判据与拖拽分支同源（``mouseButtons()``）。"""
    overlay = OverlayWindow()
    grab = FakeGrab()
    overlay._mouse_grab = grab
    overlay._press_click_only = True
    monkeypatch.setattr(overlay_window, "QApplication", _HeldLeftButton)
    overlay._check_stale_press()
    assert overlay._mouse_grab is grab
    assert overlay._press_click_only is True
    assert grab.released is False


def test_stale_press_watchdog_clears_orphan_grab_without_press():
    """孤儿 grab（``_press_global`` 与 click-only 旗标都空）同样收尾。

    grab 是"必须有按压源、必须松得开"的状态——判据只看 grab 本身，不要求
    它长得像 click-only：任何把 ``_mouse_grab`` 留在窗口上的路径都不该让
    整窗交互被钉死。
    """
    overlay = OverlayWindow()
    grab = FakeGrab()
    overlay._mouse_grab = grab
    assert overlay._press_global is None
    assert overlay._press_click_only is False
    overlay._check_stale_press()
    assert overlay._mouse_grab is None
    assert grab.released is True


# ---------------------------------------------------------------- V-8
def test_remove_sprite_releases_clip_and_notifies():
    overlay = OverlayWindow()
    sprite, clip = _make_sprite()
    overlay.add_sprite(sprite)
    assert clip.started is True
    removed = []
    overlay.add_sprite_removed_listener(removed.append)

    overlay.remove_sprite(sprite)

    assert clip.started is False      # 移除即停解码
    assert removed == [sprite]        # V-9 移除通知
    assert sprite._dirty_cb is None


def test_remove_sprite_keep_clip_for_migration():
    overlay = OverlayWindow()
    sprite, clip = _make_sprite()
    overlay.add_sprite(sprite)

    overlay.remove_sprite(sprite, release_clip=False)

    assert clip.started is True       # 屏迁移：clip 保留
    assert sprite._clip is not None


# ---------------------------------------------------------------- V-9
def test_cats_cache_weak_key_evicts_on_lib_gc():
    c = BehaviorController(QRect(0, 0, 800, 600))
    lib = FakeLibrary()
    cats = c._categories(lib)
    assert cats["idles"] == ["idle"]
    assert len(c._cats_cache) == 1

    ref = weakref.ref(lib)
    del lib
    gc.collect()

    assert ref() is None
    assert len(c._cats_cache) == 0    # 库销毁 → 缓存自动回收（无地址复用误判）


# ---------------------------------------------------------------- V-10
def test_scale_setter_marks_dirty_and_reclamps():
    overlay = OverlayWindow()
    sprite, _clip = _make_sprite()
    sprite.set_bounds(QRect(0, 0, 500, 400))
    overlay.add_sprite(sprite)
    overlay._on_tick(dt=1 / 60)       # 消费首帧脏
    sprite._frame_dirty = False

    old_rect = sprite.rect()
    sprite.scale = 1.0                # 画布 320x180 → 640x360

    assert sprite._frame_dirty is True
    assert sprite.rect().width() == 640
    assert sprite.rect() != old_rect
    # 补钳：身体框（无 body_box → 全画布）不得出界
    assert sprite.pos.x() <= 500 - 640 or sprite.rect().right() <= 500 or sprite.pos.x() == 0


def test_scale_setter_rejects_non_positive():
    sprite, _clip = _make_sprite()
    try:
        sprite.scale = 0
    except ValueError:
        pass
    else:
        raise AssertionError("scale=0 必须抛 ValueError")
    assert sprite.scale == 0.5


# ---------------------------------------------------------------- V-12
class _TickSprite:
    """够 add_sprite/advance 的最小 sprite（M12c 起空 overlay 不起表）。"""

    visible = True

    def rect(self):
        return QRect(0, 0, 10, 10)

    def advance(self, dt):
        return None


def test_close_event_stops_tick_timer():
    overlay = OverlayWindow()
    overlay.add_sprite(_TickSprite())
    overlay.start()
    assert overlay._timer.isActive()
    overlay.closeEvent(QCloseEvent())
    assert not overlay._timer.isActive()


# ---------------------------------------------------------------- V-13（弹弓刀调和）
class FakeSlingshotConfig:
    """最小 config 替身：controller 只依赖 get(key, default)。"""

    def __init__(self, enabled):
        self.enabled = bool(enabled)

    def get(self, key, default=None):
        if key == "slingshot_enabled":
            return self.enabled
        return default


def _make_hittable_sprite(pos=QPointF(100, 100), scale=0.5):
    """整幅不透明的 sprite（sprite_at 逐像素命中需要 alpha >= 阈值）。"""
    clip = FakeClip()
    clip.image.fill(0xFF336699)
    sprite = PetSprite(FakeLibrary(clip), pos=pos, scale=scale)
    sprite.bind_clip("idle")
    sprite._rebuild_pixmap()
    return sprite


def _press(overlay, pos, button, buttons):
    point = QPointF(pos) if isinstance(pos, QPoint) else QPointF(float(pos[0]), float(pos[1]))
    overlay.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, point, point,
        button, buttons, Qt.KeyboardModifier.NoModifier))


def _drag_once(overlay, sprite):
    """左键按下并移动一次（真实拖拽态），返回移动后的光标点。"""
    hit = QPoint(sprite.rect().center())
    _press(overlay, hit, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert overlay._mouse_grab is sprite
    moved = QPoint(hit.x() + 20, hit.y())
    overlay.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(moved), QPointF(moved),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    return moved


def test_non_left_press_during_grab_synthesizes_release():
    """中键等非左键（非右键）：V-13 原语义不变——合成 release 收尾。"""
    overlay = OverlayWindow()
    grab = FakeGrab()
    overlay._mouse_grab = grab
    overlay._press_global = QPoint(5, 5)
    _press(overlay, (10, 10), Qt.MouseButton.MiddleButton,
           Qt.MouseButton.MiddleButton)

    assert overlay._mouse_grab is None
    assert overlay._press_global is None
    assert grab.released is True
    assert overlay.slingshot.aiming is False


def test_right_press_during_grab_enters_slingshot_instead_of_release():
    """右键在拖拽中 = 进蓄力瞄准（slingshot_enabled 热读为真），不合成 release。

    为什么允许偏离 V-13：右键若继续走合成 release，弹弓就没有入口。瞄准
    本身有明确退出路径（松左键发射 / Esc / 再点右键取消 / 左键丢失看门狗），
    sprite 全程留在 drag 等价态（免积分/无限质量），不会卡在无人收尾的态上。
    """
    overlay = OverlayWindow()
    sprite = _make_hittable_sprite()
    overlay.add_sprite(sprite)
    moved = _drag_once(overlay, sprite)
    anchored = QPointF(sprite.pos)

    _press(overlay, moved, Qt.MouseButton.RightButton,
           Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)

    assert overlay.slingshot.aiming is True
    assert overlay._mouse_grab is sprite                 # 没被 V-13 收尾放下
    assert overlay._press_global is not None             # 左键仍按住
    assert sprite.pos == anchored                        # sprite 定锚不动
    assert sprite.interaction_state == INTERACTION_DRAG   # drag 等价态（免推进）
    assert sprite.velocity == QPointF(0, 0)


def test_right_press_keeps_v13_release_when_slingshot_disabled():
    """config 关闭弹弓：右键回到 V-13 合成 release（热读，无需重建 controller）。"""
    overlay = OverlayWindow()
    overlay.slingshot.config = FakeSlingshotConfig(False)
    sprite = _make_hittable_sprite()
    overlay.add_sprite(sprite)
    moved = _drag_once(overlay, sprite)

    _press(overlay, moved, Qt.MouseButton.RightButton,
           Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)

    assert overlay.slingshot.aiming is False
    assert overlay._mouse_grab is None                   # V-13 合成 release 收尾
    assert overlay._press_global is None
    assert sprite.interaction_state == INTERACTION_NORMAL
