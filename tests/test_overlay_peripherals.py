# -*- coding: utf-8 -*-
"""Phase 3b/3c offscreen 单测（QT_QPA_PLATFORM=offscreen 可跑）。

覆盖：
- OverlayWindow 位置监听（add/remove_position_listener）：rect 变化触发 /
  视觉无变化（advance 返回 None）不触发 / 仅帧变化（rect 未动）不触发 /
  remove 后不再触发 / remove_sprite 连带清理；
- 气泡锚点换算纯函数 sprite_anchor_rect_global（overlay 局部 → 全局）；
- 真实 PetSpeechBubble 经 SpriteBubbleFollower 跟随 sprite 位移；
- IslandWindowBridge：静态成员注册/注销/几何更新（假岛喂几何）、
  撞岛事件（pair 含 ISLAND_MEMBER_ID 且超阈值 → bump；未超不触发；音效由
  世界监听侧单入口派发，桥内不再重复调用）、
  真实 DynamicIsland 构造接线；
- 可见性闸门 OverlayVisibilityGate（O2）：光标探测表只在有可见 overlay 时跑，
  全屏探测表不受影响（隐藏后它是恢复显示的唯一路径）。

纪律：同步直调 _on_tick(dt=...) / 事件处理器，不启动真实 QTimer、不固定
sleep 赌时序（AGENTS.md 时序测试纪律）。
"""
from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from pet import collision as collision_mod
from pet import overlay_peripherals
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.bridge import IslandWindowBridge
from pet.overlay_peripherals import FullscreenCursorWatcher, OverlayVisibilityGate
from pet.overlay_window import OverlayWindow
from pet.sprite_bubble import SpriteBubbleFollower, sprite_anchor_rect_global
from pet.sprite_collision import CollisionEvent, SpriteCollisionWorld

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 假件
class FakeSprite:
    """纯 QImage 假 sprite：与 PetSprite 同一组协议方法（同 test_overlay_window）。"""

    def __init__(self, pos, size=(40, 30), *, movable=False):
        self.pos = QPointF(*pos)
        self._size = size
        self.image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
        self.image.fill(0xFF336699)
        self.velocity = QPointF(0, 0)
        self.movable = movable
        self.frame_dirty = False
        self.scale = 1.0

    def rect(self):
        return QRect(int(self.pos.x()), int(self.pos.y()), self._size[0], self._size[1])

    def alpha_at(self, local):
        return 255

    def advance(self, dt):
        old = self.rect()
        if self.movable:
            self.pos += self.velocity * dt
        new = self.rect()
        if new != old or self.frame_dirty:
            self.frame_dirty = False
            return (old, new)
        return None

    def paint(self, painter):
        pass


class FakeIsland(QWidget):
    """几何/显隐可控的假岛：QWidget 提供 geometry/isVisible，bump 记录调用。"""

    def __init__(self):
        super().__init__()
        self.on_geometry_changed = None
        self.bump_calls: list[tuple[float, float, float]] = []

    def bump(self, strength=1.0, dir_x=0.0, dir_y=0.0):
        self.bump_calls.append((strength, dir_x, dir_y))

    def emit_geometry_changed(self):
        """模拟 DynamicIsland._emit_geometry_changed 的无参回调。"""
        if callable(self.on_geometry_changed):
            self.on_geometry_changed()


class StubSound:
    def __init__(self):
        self.events = []

    def on_collision(self, event):
        self.events.append(event)


def _overlay() -> OverlayWindow:
    return OverlayWindow()


def _island_event(j, *, a="sprite-1", b=collision_mod.ISLAND_MEMBER_ID,
                  nx=0.6, ny=0.8) -> CollisionEvent:
    return CollisionEvent(tick=1, pair="|".join(sorted([a, b])), a=a, b=b,
                          j=j, nx=nx, ny=ny, contact_x=0.0, contact_y=0.0)


# ---------------------------------------------------------------- 位置监听
def test_position_listener_fires_on_rect_change():
    overlay = _overlay()
    sprite = FakeSprite((10, 10), movable=True)
    sprite.velocity = QPointF(100, 0)
    overlay.add_sprite(sprite)
    seen = []
    overlay.add_position_listener(sprite, seen.append)
    overlay._on_tick(dt=0.1)
    assert seen == [sprite]


def test_position_listener_not_fired_without_visual_change():
    overlay = _overlay()
    sprite = FakeSprite((10, 10), movable=True)  # velocity 为 0 → advance 返回 None
    overlay.add_sprite(sprite)
    seen = []
    overlay.add_position_listener(sprite, seen.append)
    overlay._on_tick(dt=0.1)
    assert seen == []


def test_position_listener_not_fired_when_only_frame_changes():
    overlay = _overlay()
    sprite = FakeSprite((10, 10), movable=False)
    sprite.frame_dirty = True  # advance 返回 (old, new) 但 old == new
    overlay.add_sprite(sprite)
    seen = []
    overlay.add_position_listener(sprite, seen.append)
    overlay._on_tick(dt=0.1)
    assert seen == []


def test_remove_position_listener_stops_callbacks():
    overlay = _overlay()
    sprite = FakeSprite((10, 10), movable=True)
    sprite.velocity = QPointF(100, 0)
    overlay.add_sprite(sprite)
    seen = []
    overlay.add_position_listener(sprite, seen.append)
    overlay.remove_position_listener(sprite, seen.append)
    overlay.remove_position_listener(sprite, seen.append)  # 重复注销是 no-op
    overlay._on_tick(dt=0.1)
    assert seen == []


def test_remove_sprite_drops_position_listeners():
    overlay = _overlay()
    sprite = FakeSprite((10, 10), movable=True)
    overlay.add_sprite(sprite)
    overlay.add_position_listener(sprite, lambda s: None)
    overlay.remove_sprite(sprite)
    assert sprite not in overlay._position_listeners


# ---------------------------------------------------------------- 气泡锚点换算（纯函数）
def test_sprite_anchor_rect_global_converts_origin():
    sprite = FakeSprite((100, 50), size=(80, 60))
    anchor = sprite_anchor_rect_global(sprite, QPoint(10, 20))
    assert anchor == QRect(110, 70, 80, 60)


def test_sprite_anchor_rect_global_zero_origin():
    sprite = FakeSprite((7, 9), size=(40, 30))
    anchor = sprite_anchor_rect_global(sprite, QPoint(0, 0))
    assert anchor == QRect(7, 9, 40, 30)


# ---------------------------------------------------------------- 真实气泡跟随
def test_bubble_follower_real_bubble_follows_sprite():
    overlay = _overlay()
    sprite = FakeSprite((100, 200), movable=True)
    overlay.add_sprite(sprite)
    follower = SpriteBubbleFollower(overlay, sprite)
    try:
        assert follower.bubble is not None
        assert follower.say("测试一句") is True
        assert follower.bubble.isVisible()
        before = follower.bubble.pos()
        sprite.velocity = QPointF(100, 0)
        overlay._on_tick(dt=0.2)  # sprite 右移 20px → 位置监听 → 气泡直移跟随
        after = follower.bubble.pos()
        assert after.x() - before.x() == 20
        assert after.y() == before.y()
        assert follower.say("") is False  # 空文案静默降级
    finally:
        follower.close()
    assert sprite not in overlay._position_listeners


# ---------------------------------------------------------------- 岛静态成员注册/注销/几何更新
def test_island_static_member_registered_and_updated():
    overlay = _overlay()
    origin = overlay.geometry().topLeft()
    world = SpriteCollisionWorld()
    island = FakeIsland()
    island.setGeometry(500, 300, 200, 44)
    island.show()
    bridge = IslandWindowBridge(island, world, overlay, sound=StubSound())
    try:
        member = world._static_members.get(collision_mod.ISLAND_MEMBER_ID)
        assert member == (500 - origin.x(), 300 - origin.y(), 200, 44)
        # 几何变化：岛自己的无参回调通道（接线口径同 app.py on_geometry_changed）
        island.setGeometry(620, 340, 200, 44)
        island.emit_geometry_changed()
        member = world._static_members.get(collision_mod.ISLAND_MEMBER_ID)
        assert member == (620 - origin.x(), 340 - origin.y(), 200, 44)
        # 岛隐藏 → 注销
        island.hide()
        assert collision_mod.ISLAND_MEMBER_ID not in world._static_members
        # 再显示 → 恢复注册
        island.show()
        assert collision_mod.ISLAND_MEMBER_ID in world._static_members
    finally:
        bridge.close()
        island.close()
    assert collision_mod.ISLAND_MEMBER_ID not in world._static_members


# ---------------------------------------------------------------- 撞岛反馈
def test_island_hit_above_threshold_bumps_and_defers_sound_to_world_listener():
    overlay = _overlay()
    world = SpriteCollisionWorld()
    island = FakeIsland()
    island.setGeometry(500, 300, 200, 44)
    island.show()
    sound = StubSound()
    bridge = IslandWindowBridge(island, world, overlay, sound=sound)
    try:
        bridge.on_collision(_island_event(100.0))
        assert island.bump_calls == [(0.25, 0.6, 0.8)]  # min(3, 100/400)；b=岛 → +n
        # a=岛：方向取反（岛被顶的方向 = 从对方指向岛）
        bridge.on_collision(_island_event(900.0, a=collision_mod.ISLAND_MEMBER_ID, b="sprite-9"))
        assert island.bump_calls[-1] == (2.25, -0.6, -0.8)
        # 音效不再由桥派发：同一个 SpriteSoundPlayer 已注册在世界监听侧
        # （overlay_shell._build），桥内再调一次 = 每个岛击事件两遍 on_collision
        assert sound.events == []
    finally:
        bridge.close()
        island.close()


def test_island_hit_below_threshold_no_feedback():
    overlay = _overlay()
    world = SpriteCollisionWorld()  # static_hit_min_dv = 60
    island = FakeIsland()
    island.show()
    sound = StubSound()
    bridge = IslandWindowBridge(island, world, overlay, sound=sound)
    try:
        bridge.on_collision(_island_event(30.0))
        bridge.on_collision(_island_event(500.0, a="x", b="y"))  # pair 不含岛
        assert island.bump_calls == []
        assert sound.events == []
    finally:
        bridge.close()
        island.close()


# ---------------------------------------------------------------- 真实灵动岛构造接线
def test_real_dynamic_island_registers_in_collision_world(tmp_path):
    overlay = _overlay()
    origin = overlay.geometry().topLeft()
    world = SpriteCollisionWorld()
    cfg = Config(base=tmp_path)
    island = DynamicIsland(cfg)
    bridge = IslandWindowBridge(island, world, overlay, sound=StubSound())
    try:
        island.show()
        g = island.geometry()
        member = world._static_members.get(collision_mod.ISLAND_MEMBER_ID)
        assert member == (g.x() - origin.x(), g.y() - origin.y(), g.width(), g.height())
        assert g.width() > 0 and g.height() > 0
        # 真实岛的 bump 方法存在且调用不炸（event_effects 默认开）
        bridge.on_collision(_island_event(200.0))
    finally:
        bridge.close()
        island.close()
    assert collision_mod.ISLAND_MEMBER_ID not in world._static_members


# ---------------------------------------------------------------- 可见性闸门（O2）
def _watcher(gate, probes: list) -> FullscreenCursorWatcher:
    return FullscreenCursorWatcher(
        fullscreen_probe=lambda: False,
        cursor_probe=lambda: probes.append(1) or "UNKNOWN",
        overlay_visibility=gate)


def test_cursor_timer_follows_overlay_visibility():
    """没有可见 overlay 就不跑光标探测；有则跑；全屏表始终不受影响。"""
    gate = OverlayVisibilityGate()
    probes: list = []
    w = _watcher(gate, probes)
    widget = QWidget()
    try:
        w.set_fullscreen_enabled(True)
        w.set_cursor_enabled(True)
        assert not w._cursor_timer.isActive(), "无可见 overlay：不空转"
        assert w._fs_timer.isActive()

        gate.note(widget, True)
        assert w._cursor_timer.isActive()
        probes.clear()
        gate.note(widget, False)
        assert not w._cursor_timer.isActive()
        assert probes == [], "隐藏后不得再探测光标"
        assert w._fs_timer.isActive(), "全屏探测表不受显隐影响"

        gate.note(widget, True)
        assert w._cursor_timer.isActive()
        assert probes, "恢复显示必须立即补一次探测（停表对去抖状态机透明）"
    finally:
        w.stop()
        widget.close()


def test_cursor_gate_off_wins_over_visibility():
    """配置门关：即使有可见 overlay 也不跑；再开时按当前可见性决定。"""
    gate = OverlayVisibilityGate()
    probes: list = []
    w = _watcher(gate, probes)
    widget = QWidget()
    w.set_cursor_enabled(True)
    try:
        gate.note(widget, True)
        assert w._cursor_timer.isActive()
        w.set_cursor_enabled(False)
        assert not w._cursor_timer.isActive()
        gate.note(widget, False)
        w.set_cursor_enabled(True)
        assert not w._cursor_timer.isActive(), "门开了但窗口仍隐藏：不跑"
        gate.note(widget, True)
        assert w._cursor_timer.isActive()
    finally:
        w.stop()
        widget.close()


def test_gate_drops_destroyed_widgets():
    """弱引用登记：窗口被回收即自动摘除，不会把闸门永久顶开。"""
    gate = OverlayVisibilityGate()
    widget = QWidget()
    gate.note(widget, True)
    assert gate.any_visible() is True
    assert gate.count() == 1
    widget.close()
    del widget
    gc.collect()
    assert gate.any_visible() is False


def test_module_gate_reports_real_overlay_visibility():
    """真 OverlayWindow 的 show/hide 走模块级闸门（生产路径）。"""
    overlay = OverlayWindow()
    base = overlay_peripherals.visible_overlay_count()
    try:
        overlay.show()
        assert overlay_peripherals.visible_overlay_count() == base + 1
        overlay.hide()
        assert overlay_peripherals.visible_overlay_count() == base
    finally:
        overlay.close()
    assert overlay_peripherals.visible_overlay_count() == base
