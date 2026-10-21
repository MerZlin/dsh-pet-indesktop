# -*- coding: utf-8 -*-
"""岛速通道回归：拖拽灵动岛撞桌宠要「有冲量弹开」，不是只平推。

根因：``sprite_collision._static_member_state`` 把岛（静态成员）速度写死
``vx=vy=0``，``add_static_member`` 也不收速度；求解器只认相对速度 → vn≈0 →
冲量 j=0 → 只剩位置分离 = 平推。旧架构的岛速估计在
``island_collision._update_motion``（overlay 拓扑下该 body 被旁路，app.py:2244）。

本文件覆盖：
- 岛桥把采样出的岛速随 update_geometry → _sync → add_static_member 传进世界；
- 岛以 > ``STATIC_HIT_MIN_DV`` 的速度撞静止 sprite → 冲量 + THROWN + 事件 + bump；
- 守卫逐条（旧代码注释记载的实机教训）：几何动画期间不采样（展开动画峰值
  ~2600px/s 会把旁边的鱼凭空拍飞）、尺寸变化只重置采样点、dt<0.01s 跳过样本、
  瞬移跳变清零、仅拖拽中允许钳到上限、岛停下 rect 未变速度也归零；
- **拖拽中的重复上报**（用户反馈「拖灵动岛快速撞鱼没反馈」的根因）：一次
  ``mouseMoveEvent`` 会经 ``move()`` 的 Move 事件与随后的
  ``_emit_geometry_changed()`` 把**同一个 rect** 喂桥两遍，第二遍落进
  < ``_MOTION_MIN_DT`` 死区——死区判据必须看岛侧 ``_dragging``（松手才清零），
  不能只看几何是否重复，否则拖拽全程岛速恒 0、撞击 j=0；
- **松手闸（拖拽 → 松手的状态切换）**：松手回调距上次采样 <10ms 或 ≥10ms、
  rect 不变或已变（夹回屏幕/落位）都必须清零**并作废采样基线**——只清零不作废
  基线时，紧随其后的第一份非拖拽几何上报会拿拖拽期的旧采样点算出「跨松手的
  过期位移 / 间隔」当岛速（幽灵撞击，实测 150~500px/s）。判据是状态切换，
  不是几何去重；常态非拖拽运动（自动动画/落位/设置改位）语义不变。
- **岛拖拽 → overlay 穿透轮询降档**：真实的岛是小窗，overlay 收不到它的鼠标
  事件（``_press_global`` 恒 None），拖岛全程 overlay 保持 10ms 全量逐像素判定；
  桥把 ``_update_motion`` 观测到的 ``_dragging`` 翻转转达给壳，与拖鱼同款降档
  （真实 ``DynamicIsland`` mouse 事件端到端覆盖起手/重复上报/松手/隐藏复位）。

纪律（AGENTS.md）：注入假钟 + 同步直调 update_geometry/tick，禁固定 sleep；
假件复用 ``tests/test_island_bridge.py`` 的零 Qt 协议替身。真实 Qt 用例是
``test_real_island_drag_into_pet_throws_with_paired_reports`` 与
``test_real_island_drag_slows_overlay_pixel_polling``（合成 QMouseEvent 走真实
``DynamicIsland`` 的 mouse 事件，按真实 125Hz 鼠标节奏 8ms 连喂；Qt 只在这两个
用例内局部 import）。
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from pet import collision as collision_mod
from pet.island.bridge import IslandCollisionBridge
from pet.sprite_collision import (
    STATIC_HIT_MIN_DV,
    SpriteCollisionWorld,
    capsule_circles,
)
from tests.test_island_bridge import FakeSprite

ISLAND = collision_mod.ISLAND_MEMBER_ID
# 拖拽中的岛速上限（口径沿用 island_collision._MAX_ISLAND_SPEED=1500）
MAX_ISLAND_SPEED = 1500.0

# 岛几何（碰撞世界局部坐标）：宽 200、高 44 的胶囊；sprite 60×60 贴其右端，
# 第二次采样（left=340）时胶囊右端圆心 (518,122) 与 sprite 圆心 (550,122)
# 距离 32 < 22+30 → 法线恰好为 (+1, 0)，岛向右拖即朝 sprite 接近。
ISLAND_LEFT = 300.0
ISLAND_TOP = 100.0
ISLAND_W = 200.0
ISLAND_H = 44.0
SPRITE_POS = (520.0, 92.0)


class FakeClock:
    """可注入假钟（岛速采样用；不 sleep 赌时序）。"""

    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class IslandStub:
    """核心桥速度守卫读的岛侧状态（零 Qt 鸭子类型）。

    ``_geo_to`` 非 None = 展开/停靠/归位几何动画中；``_dragging`` = 用户拖拽中
    （测试可直接改这个属性模拟松手——真实岛 ``mouseReleaseEvent`` 就是先翻它
    再发几何回调）。
    """

    def __init__(self, *, dragging=True, geo_to=None):
        self._dragging = bool(dragging)
        self._geo_to = geo_to


class BareIsland:
    """既无 ``_dragging`` 也无 ``_geo_to`` 的岛（非 QWidget / 直接喂几何的假对象）。"""


def _bridge_with(*, dragging=True, geo_to=None, clock=None, island=None):
    """桥 + bump 记录表 + 岛替身（要改岛侧状态时用三返回值版本）。"""
    stub = island if island is not None else IslandStub(dragging=dragging, geo_to=geo_to)
    bumps: list = []
    bridge = IslandCollisionBridge(
        island=stub,
        clock=clock if clock is not None else FakeClock(),
        bump=lambda s, dx, dy: bumps.append((s, dx, dy)))
    return bridge, bumps, stub


def _bridge(*, dragging=True, geo_to=None, clock=None):
    bridge, bumps, _stub = _bridge_with(
        dragging=dragging, geo_to=geo_to, clock=clock)
    return bridge, bumps


def _moving_island(bridge, clock, *, dx=40.0, dt=0.05):
    """先采样基线、再位移 dx 采样一次 → 岛速 ≈ dx/dt。"""
    bridge.update_geometry(ISLAND_LEFT, ISLAND_TOP, ISLAND_W, ISLAND_H)
    clock.advance(dt)
    bridge.update_geometry(ISLAND_LEFT + dx, ISLAND_TOP, ISLAND_W, ISLAND_H)


# ---------------------------------------------------------------- 主回归（弹开）
def test_moving_island_impulses_stationary_sprite():
    """岛以 800px/s 拖向静止 sprite → 冲量切 THROWN + CollisionEvent + bump。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, bumps = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)

        _moving_island(bridge, clock)  # dx=40 / 0.05s = 800px/s
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        world.tick([sprite], 1 / 60)

        assert [e for e in events if ISLAND in (e.a, e.b)], "岛的真撞击未 fire 事件"
        assert sprite.interaction_state == "thrown"
        assert math.hypot(sprite.velocity.x(), sprite.velocity.y()) >= STATIC_HIT_MIN_DV
        assert bumps, "岛 bump 反馈未触发"
    finally:
        bridge.detach()


def test_static_member_velocity_participates_in_solver():
    """世界层直喂：静态成员速度产生相对接近速度 → 冲量（非仅位置分离）。"""
    world = SpriteCollisionWorld()
    try:
        world.add_static_member(ISLAND, ISLAND_LEFT + 40.0, ISLAND_TOP,
                                ISLAND_W, ISLAND_H, vx=800.0, vy=0.0)
        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)

        world.tick([sprite], 1 / 60)

        assert [e for e in events if ISLAND in (e.a, e.b)]
        assert sprite.interaction_state == "thrown"
    finally:
        world.remove_static_member(ISLAND)


# ---------------------------------------------------------------- 撞击门口径
def test_light_fish_island_hit_past_dv_gate_still_bumps():
    """轻量小鱼的岛击必须照样反馈：桥侧门要与世界侧的 **dv** 门同口径。

    世界判真撞击用的是相对速度 dv（``static_hit_min_dv=60``，
    ``sprite_collision._apply_results``），而 ``CollisionEvent`` 只带冲量
    ``j = dv × mass``（岛无限质量 ⇒ j = dv × 鱼质量）。拿 j 直接比 60 时，
    轻量小鱼（scale 小 → mass 钳到 0.5）的岛击 j = dv×0.5 < 60 会被桥吞掉：
    bump 不触发、音效不响，而世界侧明明已按 dv 放行（撞击事件都发了）。

    本用例：mass 0.5 的鱼 + 岛以 65px/s 撞上（|vn| < IMPULSE_MIN_APPROACH_SPEED
    → e=0 → dv = 65，刚过 60 的门）→ j = 32.5。
    """
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, bumps = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        sprite = FakeSprite(*SPRITE_POS)
        sprite.scale = 0.5  # calculate_mass(…, scale=0.5) → 钳到质量下界 0.5
        events: list = []
        world.add_collision_listener(events.append)

        bridge.update_geometry(ISLAND_LEFT + 36.75, ISLAND_TOP, ISLAND_W, ISLAND_H)
        clock.advance(0.05)
        bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)  # 3.25px / 0.05s = 65px/s
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(65.0)

        world.tick([sprite], 1 / 60)

        assert [e for e in events if ISLAND in (e.a, e.b)], "世界侧未按 dv 放行"
        assert sprite.interaction_state == "thrown"
        assert bumps, f"轻量小鱼（j={events[0].j:.1f}）的岛击被桥吞掉：无 bump"
    finally:
        bridge.detach()


# ---------------------------------------------------------------- 守卫逐条
def test_geometry_animation_does_not_sample_island_speed():
    """几何动画期间（_geo_to 非 None）不采样：不得把旁边的鱼凭空拍飞。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, geo_to=object(), clock=clock)
    try:
        bridge.attach(world)
        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)

        _moving_island(bridge, clock)  # 同样的位移：动画期间必须不采样
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)

        world.tick([sprite], 1 / 60)
        assert events == []
        assert sprite.interaction_state == "normal"
    finally:
        bridge.detach()


def test_size_change_resets_sample_point_without_estimate():
    """尺寸变化（展开/收起）只重置采样点：中心平移不当速度。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(0.05)
        bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W + 60.0,
                               ISLAND_H)  # size 变了：清零 + 重置采样点
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
    finally:
        bridge.detach()


def test_dense_callbacks_skip_sample_without_zeroing():
    """dt<0.01s 的高频回调「跳过」而不是清零：拖拽全程岛速不能恒为 0。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(0.001)  # 样本太密：跳过（保留上次速度、不刷新采样点）
        bridge.update_geometry(ISLAND_LEFT + 80.0, ISLAND_TOP, ISLAND_W, ISLAND_H)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)
    finally:
        bridge.detach()


def test_teleport_jump_is_not_sampled():
    """瞬移跳变守卫：换屏/配置夹回造成的跳变不参与估计。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        bridge.update_geometry(ISLAND_LEFT, ISLAND_TOP, ISLAND_W, ISLAND_H)
        clock.advance(0.02)
        bridge.update_geometry(5000.0, ISLAND_TOP, ISLAND_W, ISLAND_H)  # 瞬移
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
    finally:
        bridge.detach()


def test_overspeed_is_clamped_only_while_dragging():
    """仅拖拽中允许把超速钳到 _MAX_ISLAND_SPEED；非拖拽的极速位移清零。"""
    # 非拖拽：真实拖拽之外的岛移动没有合法高速来源 → 清零
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=False, clock=clock)
    try:
        bridge.attach(world)
        bridge.update_geometry(ISLAND_LEFT, ISLAND_TOP, ISLAND_W, ISLAND_H)
        clock.advance(0.02)
        bridge.update_geometry(ISLAND_LEFT + 60.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)  # 3000px/s
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
    finally:
        bridge.detach()

    # 拖拽中：合法甩动钳到上限
    world2 = SpriteCollisionWorld()
    clock2 = FakeClock()
    bridge2, _ = _bridge(dragging=True, clock=clock2)
    try:
        bridge2.attach(world2)
        bridge2.update_geometry(ISLAND_LEFT, ISLAND_TOP, ISLAND_W, ISLAND_H)
        clock2.advance(0.02)
        bridge2.update_geometry(ISLAND_LEFT + 60.0, ISLAND_TOP, ISLAND_W, ISLAND_H)
        assert world2._static_member_velocity[ISLAND][:2] == (
            pytest.approx(MAX_ISLAND_SPEED), 0.0)
    finally:
        bridge2.detach()


def test_island_stop_with_unchanged_rect_zeroes_velocity():
    """岛停下时 rect 没变，速度也必须归零（否则旧速度残留继续拍鱼）。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(0.05)
        bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)  # rect 不变 = 岛停了
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)

        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)
        assert events == []
        assert sprite.interaction_state == "normal"
    finally:
        bridge.detach()


# ---------------------------------------------------------------- 拖拽中的重复上报
def test_paired_same_rect_reports_keep_valid_island_speed():
    """① 同一次 move 的两路上报（``move()`` 的 Move 事件 + ``_emit_geometry_changed``）
    喂来的是**同一个 rect**：死区里的第二遍必须当重复上报，不能当「岛停了」。

    失效模式（用户反馈「拖灵动岛快速撞鱼没反馈」）：每步拖拽都把刚估出的岛速
    清掉 → 世界里的岛速恒 0 → 撞击冲量 j=0 → 只剩位置分离（平推）。
    """
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        # dt=0、同 rect 的死区重复上报（真实拖拽每步都有这两路）
        for _ in range(2):
            bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                                   ISLAND_H)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        # 保留下来的岛速照样把贴上的桌宠弹开（不是「只剩平推」）
        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)
        assert [e for e in events if ISLAND in (e.a, e.b)]
        assert sprite.interaction_state == "thrown"
    finally:
        bridge.detach()


def test_repeated_same_rect_samples_still_zero_speed():
    """③ 岛在拖拽中真停下（指针按住不动时仍有同 rect 回调）：跨过死区窗口后归零。

    「同 rect = 停」这条不能因为 ① 被整条删掉——松手/停顿仍必须让岛速归零，
    否则静止的岛继续拿旧速度拍鱼。
    """
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        for _ in range(3):
            clock.advance(0.05)
            bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                                   ISLAND_H)
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)

        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)
        assert events == []
        assert sprite.interaction_state == "normal"
    finally:
        bridge.detach()


def test_long_pause_then_report_does_not_resurrect_speed():
    """③ 长停顿（间隔 > _MOTION_MAX_DT）后的第一份位置上报不估速度，紧随其后的
    死区重复上报也不得把已清零的岛速复活。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _ = _bridge(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(2.0)  # 远超 _MOTION_MAX_DT 的长停顿
        bridge.update_geometry(ISLAND_LEFT + 45.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
        bridge.update_geometry(ISLAND_LEFT + 45.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)  # 死区重复上报
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
    finally:
        bridge.detach()


def test_island_without_dragging_flag_keeps_same_rect_stop():
    """④ 岛对象没有 ``_dragging``（非 QWidget 岛 / 直接喂几何的假对象）：
    取不到拖拽状态 → 沿用「死区同 rect = 停」的既有判据，直接喂几何路径不后退。"""
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _, _stub = _bridge_with(island=BareIsland(), clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(0.005)
        bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
    finally:
        bridge.detach()


# ---------------------------------------------------------------- B1 幽灵速度
def test_deadzone_release_sample_zeroes_velocity():
    """松手样本落进 <10ms 死区也必须清零（桥侧闸，不再手动补喂 0.05s 停止样本）。

    真实顺序（``DynamicIsland.mouseReleaseEvent`` 在 ``_emit_geometry_changed()``
    之前先 ``_dragging = False``）——松手样本带「未拖拽」标志落进死区，桥侧据此
    立即清零。失效模式（DS 全量审查 B1）：松手瞬间的 geometry 回调距上次接受
    样本 < _MOTION_MIN_DT，死区早退不清零 → 静止的岛持残留速度把贴上来的桌宠
    拍进 THROWN。本用例不注入「停止时刻的 dt≥0.01 样本」——产品里停止根本不
    会产生那种回调。
    """
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _, stub = _bridge_with(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(0.005)  # < _MOTION_MIN_DT 的死区样本
        stub._dragging = False  # 松手：先翻标志，再发几何回调（真实顺序）
        bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)

        # 幽灵速度闸：清零后贴上来的桌宠只被位置分离，不进 THROWN
        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)
        assert events == []
        assert sprite.interaction_state == "normal"
    finally:
        bridge.detach()


@pytest.mark.parametrize("gap", [0.005, 0.1], ids=["deadzone", "over-min-dt"])
def test_release_sample_with_moved_rect_zeroes_velocity(gap):
    """松手样本带「未拖拽」标志但 rect 已经变了（松手时被夹回屏幕/落位到停靠位）：
    必须立即清零——否则旧岛速挂在新 rect 上、带新鲜时间戳，继续把贴上的桌宠拍飞。

    ``gap`` = 松手回调距上次采样的间隔，两种都要覆盖：<10ms（死区，几何被当成
    重复上报）与 ≥10ms（走正常采样路径，位移会被估成「新岛速」= 幽灵撞击）。
    """
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _, stub = _bridge_with(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(gap)
        stub._dragging = False
        bridge.update_geometry(ISLAND_LEFT + 90.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)  # 夹回落点
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)

        sprite = FakeSprite(SPRITE_POS[0] + 50.0, SPRITE_POS[1])
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)
        assert events == []
        assert sprite.interaction_state == "normal"
    finally:
        bridge.detach()


@pytest.mark.parametrize("gap", [0.005, 0.1], ids=["deadzone", "over-min-dt"])
def test_release_resets_sampling_baseline_so_next_report_is_not_estimated(gap):
    """松手必须作废采样基线（过期延迟闸）。

    松手样本要么落进死区（不刷新采样点）、要么走正常路径顺便把采样点挪到松手
    位置；两种情况下若基线仍指向**拖拽期**的采样点，紧随其后的第一份非拖拽几何
    上报都会按「跨松手的过期位移 / 间隔」算出岛速 → 松手后岛自己动一下就把
    贴上的桌宠拍飞。判据是「拖拽 → 松手」的状态切换，不是几何是否重复。
    """
    world = SpriteCollisionWorld()
    clock = FakeClock()
    bridge, _, stub = _bridge_with(dragging=True, clock=clock)
    try:
        bridge.attach(world)
        _moving_island(bridge, clock)  # 拖拽中 800px/s，采样点=left+40
        assert world._static_member_velocity[ISLAND][0] == pytest.approx(800.0)

        clock.advance(gap)
        stub._dragging = False
        bridge.update_geometry(ISLAND_LEFT + 40.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)  # 松手样本（rect 不变）
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)

        clock.advance(0.1)  # 松手后岛自己动（落位/自动动画，非拖拽）
        bridge.update_geometry(ISLAND_LEFT + 70.0, ISLAND_TOP, ISLAND_W,
                               ISLAND_H)
        # 基线若未作废：这 30px 会按「跨松手的过期间隔」算成 150~300px/s 幽灵岛速
        # （都超过 STATIC_HIT_MIN_DV=60 → 静止的岛把贴上的桌宠拍飞）
        assert world._static_member_velocity[ISLAND][:2] == (0.0, 0.0)
    finally:
        bridge.detach()


# ---------------------------------------------------------------- 真实岛端到端
def _mouse_event(kind, global_pos, buttons=None):
    """合成鼠标事件（Qt 只在本用例族内局部 import，模块其余部分是零 Qt 通路）。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    if buttons is None:
        buttons = Qt.MouseButton.LeftButton
    return QMouseEvent(kind, QPointF(global_pos), QPointF(global_pos),
                       Qt.MouseButton.LeftButton, buttons,
                       Qt.KeyboardModifier.NoModifier)


def test_real_island_drag_into_pet_throws_with_paired_reports(tmp_path):
    """真实 ``DynamicIsland`` 拖拽端到端（用户反馈的那条路径，125Hz 鼠标节奏）：

    ``mouseMoveEvent`` 里 ``move()`` 触发 Move 事件 → 桥喂一遍几何，
    紧接着 ``_emit_geometry_changed()`` 再喂一遍同一个 rect；两遍之后世界里岛速
    必须非零 → 拖岛撞桌宠产生冲量（事件 + THROWN + 岛 bump 反馈）；松手后岛速
    立即归零，不留幽灵速度。

    采样节奏取 8ms（真实 125Hz 鼠标）：一次 ``mouseMoveEvent`` 的**两路**喂入
    都落在 < ``_MOTION_MIN_DT``(10ms) 的死区里，岛速只能靠「拖拽中的同 rect
    上报不算停」活下来——按 50ms 喂（旧写法）只覆盖死区外的估算那一遍，测不出
    这条失效路径（拖拽全程岛速被第二遍抹 0 → 撞击 j=0 → 只剩平推）。
    """
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.island.dynamic_island import DynamicIsland
    from pet.island.bridge import IslandWindowBridge

    clock = FakeClock()
    world = SpriteCollisionWorld(clock=clock)
    island = DynamicIsland(Config(base=tmp_path))
    island.show()
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    bumps: list = []
    real_bump = island.bump
    island.bump = lambda s, dx, dy: (bumps.append((s, dx, dy)), real_bump(s, dx, dy))
    bridge = IslandWindowBridge(island, world, None, clock=clock)
    try:
        rect = island.geometry()
        screen = island.screen() or app.primaryScreen()
        avail = screen.availableGeometry()
        # 朝屏幕内侧拖，避开松手夹回/边缘停靠（那些会叠加几何动画通路）
        step = 40 if (avail.right() - rect.right()) >= (rect.x() - avail.left()) else -40
        press = island.pos() + QPoint(60, 20)

        island.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, press))
        island.mouseMoveEvent(_mouse_event(
            QEvent.Type.MouseMove, press + QPoint(30, 0)))
        assert island._dragging, "拖拽未起手"
        for i in range(4):
            clock.advance(0.008)  # 真实 125Hz 鼠标：8ms 一步（两路喂入均在死区内）
            island.mouseMoveEvent(_mouse_event(
                QEvent.Type.MouseMove, press + QPoint(70 + step * i, 0)))

        vx = world._static_member_velocity[ISLAND][0]
        assert abs(vx) > STATIC_HIT_MIN_DV, f"拖拽中岛速被重复上报清掉：{vx}"

        # 岛前进方向的胶囊端点圆正前方 32px 处放一只静止桌宠 → 岛撞上去
        rect = island.geometry()
        chain = capsule_circles(rect.x(), rect.y(), rect.width(), rect.height())
        cx, cy, _r = chain[-1] if step > 0 else chain[0]
        lead = 32.0 if step > 0 else -32.0
        sprite = FakeSprite(cx + lead - 30.0, cy - 30.0)
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)

        assert [e for e in events if ISLAND in (e.a, e.b)], "岛撞桌宠未产生撞击事件"
        assert sprite.interaction_state == "thrown"
        assert bumps, "岛 bump 反馈未触发"

        # 松手（同 rect、同时刻 → 死区）：立即清零，不留幽灵速度
        island.mouseReleaseEvent(_mouse_event(
            QEvent.Type.MouseButtonRelease, press + QPoint(70 + step * 3, 0),
            buttons=Qt.MouseButton.NoButton))
        assert island._dragging is False
        # 松手后岛进入夹回/归位动画（_geo_to 非 None）：按旧 _wall_active 口径
        # 动画期整条墙撤下（成员条目随之删除），比清零更强；若动画已结束则
        # 成员仍在但速度必须归零。两种形态都满足「不留幽灵速度」。
        assert world._static_member_velocity.get(ISLAND, (0.0, 0.0))[:2] == (0.0, 0.0)
    finally:
        bridge.close()
        island.close()


def test_stale_velocity_expires_without_any_callback():
    """按住不动（零回调）场景的世界侧兜底：速度样本超 TTL 未刷新按 0 处理。

    拖拽中按住不动 = 不再有几何回调，桥侧无从纠正；世界在读速度时按
    写入时刻过期（STATIC_VELOCITY_TTL_SECS），静止岛不得再拍飞桌宠。
    """
    world = SpriteCollisionWorld()
    try:
        world.add_static_member(ISLAND, ISLAND_LEFT + 40.0, ISLAND_TOP,
                                ISLAND_W, ISLAND_H, vx=800.0, vy=0.0)
        # 把速度样本的写入时刻拨到 TTL 之前（等价于按住不动远超 0.15s）
        vx, vy, ts = world._static_member_velocity[ISLAND]
        world._static_member_velocity[ISLAND] = (vx, vy, ts - 1.0)

        sprite = FakeSprite(*SPRITE_POS)
        events: list = []
        world.add_collision_listener(events.append)
        world.tick([sprite], 1 / 60)

        assert [e for e in events if ISLAND in (e.a, e.b)] == []
        assert sprite.interaction_state == "normal"
    finally:
        world.remove_static_member(ISLAND)


def test_real_island_drag_slows_overlay_pixel_polling(tmp_path):
    """真实 ``DynamicIsland`` 拖拽 → overlay 逐像素穿透轮询降档（与拖鱼同款）。

    岛是独立顶层窗：overlay 的 ``_press_global`` 永远不会因拖岛而置位——没有这条
    通道时，拖岛全程 overlay 保持 10ms 全量逐像素判定（拖鱼早已降到 100ms）。
    真实岛 mouse 事件链覆盖起手（未过拖拽阈值不置位）→ 拖拽（重复几何上报不
    重复置位）→ 松手复位 → 拖拽中隐藏（中断路径复位）。
    """
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.island.dynamic_island import DynamicIsland
    from pet.island.bridge import IslandWindowBridge
    from pet.overlay_window import OverlayWindow
    from tests.test_island_bridge import FakeInputController

    clock = FakeClock()
    world = SpriteCollisionWorld(clock=clock)
    overlay = OverlayWindow()
    controller = FakeInputController()
    overlay._input_controller = controller  # 真控制器要写 Win32 样式，此处假件
    island = DynamicIsland(Config(base=tmp_path))
    island.show()
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    bridge = IslandWindowBridge(island, world, overlay, clock=clock)
    try:
        press = island.pos() + QPoint(60, 20)
        island.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, press))
        assert controller.drag_states == [], "按下未过拖拽阈值：不得置降频档"

        island.mouseMoveEvent(_mouse_event(
            QEvent.Type.MouseMove, press + QPoint(60, 0)))
        assert island._dragging, "拖拽未起手"
        assert controller.drag_states == [True], "拖岛未把 overlay 轮询降到拖拽档"

        for i in range(4):  # 真实 125Hz 鼠标节奏（一次 move 喂两遍几何）
            clock.advance(0.008)
            island.mouseMoveEvent(_mouse_event(
                QEvent.Type.MouseMove, press + QPoint(70 + 8 * i, 0)))
        assert controller.drag_states == [True], "重复几何上报重复置位了降频档"

        island.mouseReleaseEvent(_mouse_event(
            QEvent.Type.MouseButtonRelease, press + QPoint(100, 0),
            buttons=Qt.MouseButton.NoButton))
        assert island._dragging is False
        assert controller.drag_states == [True, False], "松手未复位穿透轮询档"

        # 拖拽中被隐藏（托盘隐藏/全屏避让/换屏重建）：中断路径同样复位
        island.mousePressEvent(_mouse_event(
            QEvent.Type.MouseButtonPress, press + QPoint(100, 0)))
        island.mouseMoveEvent(_mouse_event(
            QEvent.Type.MouseMove, press + QPoint(170, 0)))
        assert controller.drag_states == [True, False, True]
        island.hide()
        assert controller.drag_states == [True, False, True, False], \
            "拖拽中隐藏未复位穿透轮询档（overlay 会永久停在 100ms 档）"
    finally:
        bridge.close()
        island.close()
        overlay.close()
