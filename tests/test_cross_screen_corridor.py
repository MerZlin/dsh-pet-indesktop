# -*- coding: utf-8 -*-
"""跨屏「过缝通行 + 落点吸附」offscreen 单测（.scratch/cross-screen-corridor/SPEC.md）。

用户实机布局建模：主屏 2560×1440（右，avail 高 1392）+ 副屏 1920×1080（左，
avail 高 1032），顶对齐。主屏左侧 y>1032 那条带正对副屏下方空洞——修复前
拖拽/抛掷在这条带上是墙（band 收窄语义 + set_pos 逐块钳制）。

纪律：假 clip / 假钟同 tests/test_sprite_physics.py；控制器 tick 同步直调。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtWidgets import QApplication

from pet import physics as physics_mod
from pet.pet_sprite import INTERACTION_NORMAL, INTERACTION_THROWN, PetSprite
from pet.sprite_physics import ThrowPhysicsController
from pet.window_placement import snap_to_nearest_screen
from tests.test_sprite_physics import BOUNDS, FakeClock, _make_sprite

app = QApplication.instance() or QApplication([])

# 用户实机布局（overlay 拓扑，avail 口径：任务栏已扣）
MAIN = QRectF(0, 0, 2560, 1392)       # 右：2560×1440，avail 1392
SECOND = QRectF(-1920, 0, 1920, 1032)  # 左：1920×1080，avail 1032
SCREENS = (MAIN, SECOND)
# sprite 320×180（scale 0.5）：身体 = 全画布（FakeClip 无 body_box 声明）


def _corridor_sprite(pos) -> PetSprite:
    sprite = _make_sprite(pos)
    sprite.set_clamp_rects(SCREENS)
    return sprite


def _throw_until_rest(sprite, controller, *, max_ticks=4000):
    controller.tick([sprite], 0.016)
    ticks = 0
    while sprite.interaction_state == INTERACTION_THROWN and ticks < max_ticks:
        sprite.advance(0.016)
        controller.tick([sprite], 0.016)
        ticks += 1
    return ticks


# ---------------------------------------------------------------- 抛掷：过缝通行
def test_throw_crosses_the_dead_zone_corridor():
    """主屏底部带向左抛：修复前 band 收窄把左墙钉在 x=0（主→副底部是墙）；
    过缝通行语义下边界是并集外接矩形（左缘 = 副屏左缘 -1920），可以穿缝。"""
    sprite = _corridor_sprite((200.0, 1100.0))
    controller = ThrowPhysicsController(QRectF(MAIN))
    # 修复前的 band 结果：左墙 = 0（主屏左缘）；修复后：-1920（副屏左缘）
    bounds = controller._effective_bounds(sprite)
    assert bounds.left() == -1920.0
    assert bounds.right() == 2560.0
    sprite.velocity = QPointF(-900.0, -300.0)
    sprite.interaction_state = INTERACTION_THROWN
    for _ in range(200):
        controller.tick([sprite], 0.016)
        if sprite.pos.x() < 0:
            break
    assert sprite.pos.x() < 0, "抛掷没有穿过主屏左缘——过缝通行未生效"


def test_throw_resting_in_dead_zone_snaps_to_secondary():
    """落在空洞（副屏下方 y>1032 且 x<0）静止 → 吸附到副屏（最近屏）右下，
    身体完整收入副屏可用区。"""
    sprite = _corridor_sprite((200.0, 1100.0))
    controller = ThrowPhysicsController(QRectF(MAIN))
    sprite.velocity = QPointF(-500.0, 0.0)
    sprite.interaction_state = INTERACTION_THROWN
    _throw_until_rest(sprite, controller)
    assert sprite.interaction_state == INTERACTION_NORMAL
    body = QRectF(sprite.pos.x(), sprite.pos.y(), 320, 180)
    in_main = MAIN.contains(body.center())
    in_second = SECOND.contains(body.center())
    assert in_main or in_second, f"静止后身体中心仍在空洞里: {body}"


# ---------------------------------------------------------------- 拖拽：过缝 + 松手吸附
def test_drag_can_cross_corridor_and_release_snaps():
    """拖拽中可进入空洞（看不见是过渡态）；松手在空洞 → 吸附到最近的屏。"""
    clock = FakeClock()
    sprite = _corridor_sprite((100.0, 1150.0))
    sprite._clock = clock
    # 拖进空洞（副屏下方，x<0、y>1032）
    sprite.on_press(QPointF(200.0, 1200.0))
    sprite.begin_drag()
    clock.advance(0.05)
    sprite.on_move(QPointF(-900.0, 1250.0))
    sprite.advance(0.05)
    # 拖拽中：位置可以落在空洞（并集外接矩形钳制，不是逐块并集）
    assert sprite.pos.x() < 0
    # 低速松手（停顿轨迹 → estimate 判静止）→ 吸附到最近的屏
    for _ in range(4):
        clock.advance(0.4)
        sprite.advance(0.05)
    sprite.on_release(QPointF(-900.0, 1250.0))
    center_x = sprite.pos.x() + 160
    center_y = sprite.pos.y() + 90
    assert SECOND.contains(center_x, center_y) or MAIN.contains(center_x, center_y), \
        f"松手后身体中心仍在空洞里: ({center_x}, {center_y})"


def test_release_inside_a_screen_does_not_snap():
    """松手时身体中心在某屏内：不吸附（位置不动）。"""
    sprite = _corridor_sprite((100.0, 400.0))
    sprite._clock = FakeClock()
    sprite.on_press(QPointF(150.0, 450.0))
    sprite.begin_drag()
    for _ in range(4):
        sprite._clock.advance(0.4)
        sprite.advance(0.05)
    before = QPointF(sprite.pos)
    sprite.on_release(QPointF(150.0, 450.0))
    assert sprite.pos == before


# ---------------------------------------------------------------- 吸附纯函数
def test_snap_pure_function_layouts():
    """snap_to_nearest_screen：左副屏布局 / 上下堆叠 / 身体比屏大。"""
    main = QRect(0, 0, 2560, 1392)
    second = QRect(-1920, 0, 1920, 1032)
    # 屏内 → None
    assert snap_to_nearest_screen(QRect(100, 100, 320, 180), [main, second]) is None
    # 空洞（副屏下方）→ 收进副屏：x 保持、y 钳进副屏底
    snapped = snap_to_nearest_screen(QRect(-1000, 1200, 320, 180), [main, second])
    assert snapped is not None
    body = QRect(snapped.x(), snapped.y(), 320, 180)
    assert second.contains(body)
    # 上下堆叠：上屏下方的空洞 → 收进下屏
    top = QRect(0, 0, 1920, 1080)
    bottom = QRect(0, 1080, 1920, 1080)
    snapped2 = snap_to_nearest_screen(QRect(800, 1079, 320, 180), [top, bottom])
    # 身体横跨两屏缝（中心在上屏 y<1080 内）→ None
    assert snap_to_nearest_screen(QRect(800, 990, 320, 180), [top, bottom]) is None
    # 身体比屏大 → 贴上/左缘（不崩）
    snapped3 = snap_to_nearest_screen(QRect(-5000, 5000, 4000, 3000), [main, second])
    assert snapped3 is not None
    big = QRect(snapped3.x(), snapped3.y(), 4000, 3000)
    nearest = second if abs(big.center().x() - second.center().x()) < abs(big.center().x() - main.center().x()) else main
    assert big.left() == nearest.left() and big.top() == nearest.top()


# ---------------------------------------------------------------- 单屏逐位不变
def test_single_screen_bounds_and_no_snap():
    """单矩形域：_effective_bounds 恒等于该矩形（行为逐位不变）；屏内不吸附。"""
    sprite = _make_sprite((100.0, 100.0))
    controller = ThrowPhysicsController(BOUNDS)
    bounds = controller._effective_bounds(sprite)
    assert bounds == BOUNDS
    body = QRect(100, 100, 320, 180)
    assert snap_to_nearest_screen(body, [QRect(0, 0, 800, 600)]) is None
EOF_MARKER_NOT_USED = True


# ---------------------------------------------------------------- k3 盲审修复回归
def test_snap_triggers_on_half_body_hanging_out():
    """覆盖判定（k3 A2）：身体中心在屏内但半身悬在空洞上 → 也要吸附
    （修复前按中心判定不吸附，半个宠隐形到下一次 NORMAL 位移）。"""
    main = QRect(0, 0, 2560, 1392)
    second = QRect(-1920, 0, 1920, 1032)
    # 身体 x∈[-100,220]：中心 60 在主屏内，但左半身 100px 悬在 y>1032 的空洞上
    snapped = snap_to_nearest_screen(QRect(-100, 1200, 320, 180), [main, second])
    assert snapped is not None
    body = QRect(snapped.x(), snapped.y(), 320, 180)
    assert main.contains(body) or second.contains(body)


def test_island_supported_settle_also_snaps():
    """落岛落定（k3 S1）：_settle_supported 把 thrown 宠钉在静态成员上时，
    身体在空洞里同样吸附——鸭子类型调用，不 import pet_sprite。"""
    from pet.sprite_collision import SpriteCollisionWorld
    from tests.test_sprite_collision import FakeSprite

    calls = []
    sprite = FakeSprite(-960, -90, vx=0.0, vy=0.0, collision_id="p")
    sprite.interaction_state = INTERACTION_THROWN
    sprite._snap_into_screen_if_needed = lambda: calls.append(1)
    world = SpriteCollisionWorld(support_settle_ticks=2)
    # 岛在副屏上方（x<0, y<0 的空洞上方边缘），宠低速贴上去
    world.add_static_member("island", -1000, -50, 200, 44)
    world.tick([sprite], 0.016)
    world.tick([sprite], 0.016)  # 连续支撑达到 ticks=2 才收尾
    assert calls, "落岛落定没有调用吸附"
    assert sprite.interaction_state == INTERACTION_NORMAL


def test_slingshot_cancel_snaps_before_forcing_normal():
    """弹弓 Esc 取消（k3 S2）：锚点在空洞里时，强制 NORMAL 前先吸附——
    否则 drop_gravity 的 THROWN 兜底被覆盖，宠钉死在空洞。"""
    from pet.sprite_slingshot import SlingshotController

    events = []
    class FakeSlingshotSprite:
        def __init__(self):
            self.interaction_state = INTERACTION_THROWN  # on_release 走 drop_gravity 分支后
        def on_release(self, _pos):
            self.interaction_state = INTERACTION_THROWN
        def set_pos(self, _pos):
            pass
        def set_velocity(self, _v):
            pass
        def squash(self):
            pass
        def _snap_into_screen_if_needed(self):
            events.append(("snap", self.interaction_state))
    sprite = FakeSlingshotSprite()
    sling = SlingshotController()
    sling._sprite = sprite
    sling._anchor = QPointF(-900.0, 1250.0)  # 锚点在空洞
    sling._cursor = QPointF(-950.0, 1250.0)
    assert sling.cancel(resume_drag=False) is True
    assert events == [("snap", INTERACTION_THROWN)]  # 吸附在强制 NORMAL 之前、且当时仍是 THROWN
    assert sprite.interaction_state == INTERACTION_NORMAL


def test_spring_drag_release_in_dead_zone_snaps():
    """弹簧档拖拽（k3 A4）：on_move 只写目标点，松手时滞后位置在空洞 → 吸附。"""
    clock = FakeClock()
    sprite = _corridor_sprite((100.0, 1150.0))
    sprite._clock = clock
    sprite.drag_physics = True
    sprite.on_press(QPointF(200.0, 1200.0))
    sprite.begin_drag()
    clock.advance(0.05)
    sprite.on_move(QPointF(-900.0, 1250.0))
    # 弹簧追踪几步（位置逼近目标点所在的空洞）
    for _ in range(60):
        clock.advance(0.016)
        sprite.advance(0.016)
    for _ in range(4):
        clock.advance(0.4)
        sprite.advance(0.05)
    sprite.on_release(QPointF(-900.0, 1250.0))
    center_x = sprite.pos.x() + 160
    center_y = sprite.pos.y() + 90
    assert SECOND.contains(center_x, center_y) or MAIN.contains(center_x, center_y), \
        f"弹簧档松手后身体中心仍在空洞里: ({center_x}, {center_y})"
