# -*- coding: utf-8 -*-
"""Phase 1b 抛掷/拖拽物理 offscreen 单测（QT_QPA_PLATFORM=offscreen 可跑）。

覆盖 ThrowPhysicsController（重力下坠/边界反弹衰减/静止回 normal/子步
不穿透地板/只处理 thrown）与 PetSprite 拖拽轨迹协议（高速松手进
thrown/低速或停顿松手回 normal/初速方向/软上限/轨迹修剪）。

纪律（AGENTS.md 时序测试）：drag 轨迹时间戳用注入的假钟（sprite._clock）
确定性构造，控制器 tick 直接同步调用，不启动真实 QTimer、不固定 sleep。
素材用纯 QImage 假 clip（同 tests/test_overlay_window.py 的造假方式）。
"""
from __future__ import annotations

import math
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet import physics as physics_mod
from pet.pet_sprite import (
    INTERACTION_DRAG,
    INTERACTION_NORMAL,
    INTERACTION_THROWN,
    PetSprite,
)
from pet.sprite_physics import ThrowPhysicsController

app = QApplication.instance() or QApplication([])

# scale=0.5 时 sprite 外接矩形 320x180；bounds (0,0,800,600) 下
# 左上角可活动范围 x∈[0,480]、y∈[0,420]（地板 floor_y=420）
BOUNDS = QRectF(0, 0, 800, 600)
FLOOR_Y = 600 - 180
RIGHT_X = 800 - 320


class FakeClip(QObject):
    """接口对齐 WebMClip 的假 clip：frameChanged 信号 + 固定 QImage 帧。"""

    frameChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)
        self.started = False
        self.playback_speed = 1.0
        self.speed_calls: list[float] = []

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

    def set_playback_speed(self, speed):
        self.playback_speed = float(speed)
        self.speed_calls.append(float(speed))


class FakeLibrary:
    def __init__(self, clip):
        self._clip = clip
        self.no_mirror: set[str] = set()

    def movie(self, name, **_kw):
        return self._clip


class FakeClock:
    """单调假钟：advance(dt) 推进，调用返回当前值。"""

    def __init__(self, t: float = 1000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_sprite(pos=(100.0, 100.0), *, clock: FakeClock | None = None) -> PetSprite:
    sprite = PetSprite(FakeLibrary(FakeClip()), pos=QPointF(*pos), scale=0.5)
    sprite.bind_clip("fake")
    sprite.advance(0.016)  # 吞掉 bind 后的首帧脏标记
    if clock is not None:
        sprite._clock = clock
    return sprite


def _drag_steps(sprite: PetSprite, clock: FakeClock, start: tuple[float, float],
                steps: list[tuple[float, float, float]]) -> QPointF:
    """press 后按 (dx, dy, dt) 序列拖动，返回松手位置（不 release）。

    真拖拽序列 = on_press（点击候选）→ begin_drag（过阈值升级）→ on_move…
    每步拖动后推一帧（真实拖拽中移动事件与帧交错；弹簧档靠 advance 追踪光标）。
    """
    sprite.on_press(QPointF(*start))
    sprite.begin_drag()
    x, y = start
    for dx, dy, dt in steps:
        clock.advance(dt)
        x, y = x + dx, y + dy
        sprite.on_move(QPointF(x, y))
        sprite.advance(dt)
    return QPointF(x, y)


# ---------------------------------------------------------------- 拖拽跟随弹簧
def test_drag_follow_springs_when_drag_physics_on():
    """拖拽弹簧（对齐 legacy window.py _tick_drag_physics 的 K=200/C=30 手感）：
    drag_physics 开时 on_move 只写目标点不直写位置，advance 用 spring_velocity
    阻尼收敛。v4.3.0 回归：overlay 拓扑从未实现这段，拖拽变 1:1 硬跟手。"""
    clock = FakeClock()
    sprite = _make_sprite((100.0, 100.0), clock=clock)
    sprite.drag_physics = True
    sprite.on_press(QPointF(110.0, 110.0))
    sprite.begin_drag()
    sprite.on_move(QPointF(410.0, 110.0))     # 拖拽目标 x=400（grab 偏移 10）

    assert sprite.pos.x() == 100.0            # 不直写
    for _ in range(12):                        # 0.1s：在半路（滞后），且没到
        clock.advance(1 / 120)
        sprite.advance(1 / 120)
    assert 100.0 < sprite.pos.x() < 390.0
    for _ in range(120):                       # 再 1s：收敛到目标
        clock.advance(1 / 120)
        sprite.advance(1 / 120)
    assert abs(sprite.pos.x() - 400.0) < 2.0


def test_drag_follow_stays_direct_when_drag_physics_off():
    """drag_physics 关 = 今天的 1:1 直跟，逐字不变。"""
    sprite = _make_sprite((100.0, 100.0))
    sprite.drag_physics = False
    sprite.on_press(QPointF(110.0, 110.0))
    sprite.begin_drag()
    sprite.on_move(QPointF(410.0, 110.0))
    assert sprite.pos.x() == 400.0


# ---------------------------------------------------------------- 飞行段（controller）
def test_thrown_falls_under_gravity():
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, 50.0))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(0, 0))

    for _ in range(10):
        controller.tick([sprite], 0.016)

    assert sprite.interaction_state == INTERACTION_THROWN  # 半空中不回 normal
    assert sprite.pos.y() > 50.0                           # 下坠
    assert sprite.velocity.y() > 0.0                       # 重力累积出向下速度


def test_ground_bounce_reverses_and_damps():
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, FLOOR_Y - 5.0))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(0, 2000))                  # 高速砸向地板

    controller.tick([sprite], 0.016)

    assert sprite.pos.y() <= FLOOR_Y                       # 不穿透地板
    assert sprite.velocity.y() < 0.0                       # 反弹向上
    assert abs(sprite.velocity.y()) < 2000.0               # 恢复系数衰减


def test_wall_bounce_reverses_horizontal():
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((RIGHT_X - 10.0, 100.0))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(5000, -800))               # 斜向撞右墙

    controller.tick([sprite], 0.016)

    assert sprite.pos.x() <= RIGHT_X
    assert sprite.velocity.x() < 0.0                       # 水平反弹
    assert sprite.interaction_state == INTERACTION_THROWN  # 仍有速度，不收尾


def test_rest_on_ground_returns_to_normal():
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, FLOOR_Y))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(5, 0))                     # 贴地低速滑动

    controller.tick([sprite], 0.016)

    assert sprite.interaction_state == INTERACTION_NORMAL  # 交还行为控制器
    assert sprite.velocity == QPointF(0, 0)                # 停住
    assert sprite.pos.y() == FLOOR_Y                       # 停在地板上


def test_substep_integration_does_not_tunnel_floor():
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, FLOOR_Y - 50.0))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(0, 8000))                  # 50ms 单步会飞出 400px

    controller.tick([sprite], 0.05)                        # 大 dt：靠 ≤8ms 子步切分

    # 单步积分的末尾位置会被钳在地板上；子步积分在 tick 中途反弹、剩余
    # 时间按反弹后速度继续上升——pos 严格高于地板即证明子步语义生效
    assert 0.0 <= sprite.pos.y() < FLOOR_Y
    assert sprite.velocity.y() < 0.0
    assert sprite.interaction_state == INTERACTION_THROWN


def test_controller_ignores_non_thrown_sprites():
    controller = ThrowPhysicsController(BOUNDS)
    normal = _make_sprite((50.0, 50.0))
    normal.set_velocity(QPointF(300, 0))                   # normal：advance 管
    dragged = _make_sprite((300.0, 50.0))
    dragged.on_press(QPointF(310.0, 60.0))
    dragged.begin_drag()                                     # drag：光标管

    controller.tick([normal, dragged], 0.016)

    assert normal.pos == QPointF(50.0, 50.0)               # 控制器不碰
    assert normal.velocity == QPointF(300, 0)
    assert dragged.pos == QPointF(300.0, 50.0)


def test_advance_skips_integration_when_thrown():
    sprite = _make_sprite((100.0, 100.0))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(300, 0))

    sprite.advance(0.016)                                  # thrown：控制器积分
    assert sprite.pos == QPointF(100.0, 100.0)

    sprite.interaction_state = INTERACTION_NORMAL
    sprite.advance(0.016)                                  # normal：advance 积分
    assert sprite.pos == QPointF(104.8, 100.0)


# ---------------------------------------------------------------- F4：飞行期动画匹配
def test_flight_anim_speed_follows_velocity_and_landing_resets():
    """F4：飞行每 tick 按速度设 clip 速率；落地复位回用户速率。

    不复位会让 duration()（除以 playback_speed，webm_clip.py:1387-1390）
    在下一次 _plan_move 里按加速后的时长量化位移。
    """
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, 100.0))
    clip = sprite._clip
    sprite.playback_speed = 1.0
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(1400, 0))                  # 高速飞行

    controller.tick([sprite], 0.016)

    assert sprite.interaction_state == INTERACTION_THROWN
    assert clip.playback_speed > 1.0                       # 频闪修法：加速播放
    assert sprite.velocity.x() > 0.0                       # 位置积分未被影响

    sprite.set_pos(QPointF(100.0, FLOOR_Y))                # 贴地低速 → 落地
    sprite.set_velocity(QPointF(5, 0))
    controller.tick([sprite], 0.016)

    assert sprite.interaction_state == INTERACTION_NORMAL
    assert clip.playback_speed == 1.0                      # 速率复位


def test_flight_anim_speed_stacks_on_user_rate():
    """倍率叠加在用户播放速率之上（window.py:4328-4340），不是硬写成 1.75×。"""
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, 100.0))
    clip = sprite._clip
    sprite.playback_speed = 1.5
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(700, 0))

    controller.tick([sprite], 0.016)

    speed = math.hypot(sprite.velocity.x(), sprite.velocity.y())
    assert clip.playback_speed > 1.5
    assert clip.playback_speed == pytest.approx(
        1.5 * physics_mod.flight_anim_speed(speed))


def test_flight_anim_speed_untouched_for_non_thrown():
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, 100.0))
    clip = sprite._clip
    sprite.set_velocity(QPointF(900, 0))                   # normal：advance 管

    controller.tick([sprite], 0.016)

    assert clip.playback_speed == 1.0                      # 控制器不碰非 thrown


# ---------------------------------------------------------------- 松手抛掷判定（sprite 侧）
def test_high_speed_release_enters_thrown():
    clock = FakeClock()
    sprite = _make_sprite((100.0, 100.0), clock=clock)
    sprite.drag_physics = True  # 抛掷接管只在物理拖拽开时生效（缺省 False）
    # 400px / 0.1s ≈ 4000px/s，远超 DEAD_ZONE_SPEED
    release = _drag_steps(sprite, clock, (110.0, 110.0),
                          [(40.0, 0.0, 0.01)] * 10)
    sprite.on_release(release)

    assert not sprite.dragging
    assert sprite.interaction_state == INTERACTION_THROWN
    assert sprite.velocity.x() > physics_mod.DEAD_ZONE_SPEED
    assert sprite.velocity.y() == 0.0                      # 纯水平甩动
    # 弹簧档松手不 snap：宠从滞后位置起抛（legacy 手感），不是瞬移到光标
    assert 100.0 < sprite.pos.x() < release.x() - 20.0


def test_slow_release_returns_normal():
    clock = FakeClock()
    sprite = _make_sprite((100.0, 100.0), clock=clock)
    sprite.set_velocity(QPointF(120, 0))                   # 拖拽前在游荡
    # 6px / 0.03s ≈ 200px/s，低于死区 = 原地放下
    release = _drag_steps(sprite, clock, (110.0, 110.0),
                          [(2.0, 0.0, 0.01)] * 3)
    sprite.on_release(release)

    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)                # 停住，不恢复拖拽前速度
    # 慢拖弹簧贴得很紧：落在拖拽位置附近（不 snap，允许弹簧残差；
    # 本例拖拽全程仅 6px，跟不上的残差 ~5.7px 是弹簧还没走完的物理事实）
    assert abs(sprite.pos.x() - (release.x() - 10.0)) < 8.0


def test_stale_release_returns_normal():
    clock = FakeClock()
    sprite = _make_sprite((100.0, 100.0), clock=clock)
    release = _drag_steps(sprite, clock, (110.0, 110.0),
                          [(40.0, 0.0, 0.01)] * 10)        # 快甩
    clock.advance(0.5)                                     # 但松手前停顿 > STALE
    sprite.on_release(release)

    assert sprite.interaction_state == INTERACTION_NORMAL  # 停顿 = 静止放下
    assert sprite.velocity == QPointF(0, 0)


# ---------------------------------------------------------------- #219 静止放手下落开关
# 现状（issue #219）：低速松手 = 原地放下，半空松手就停在半空。drop_gravity
# 打开后改为以**零初速**进 throw 物理模式，由重力自然落地（链路由
# ThrowPhysicsController 现成提供）；已经贴地时不能多绕一次抛掷接管——物理
# 下一 tick 就判回静止，白绑一次悬空 clip 还要提交一次落地预热。
def _bounds_sprite(pos, clock):
    """挂上 bounds 的 sprite（真机由 overlay_shell 逐只喂，`_at_ground` 依赖它）。"""
    sprite = _make_sprite(pos, clock=clock)
    sprite.set_bounds(QRect(0, 0, 800, 600))
    return sprite


def _slow_drag_release(sprite, clock, start):
    """低速拖拽（2px / 0.01s = 200px/s < DEAD_ZONE_SPEED）后松手。"""
    release = _drag_steps(sprite, clock, start, [(2.0, 0.0, 0.01)] * 3)
    sprite.on_release(release)
    return release


def test_drop_gravity_slow_release_falls_to_ground():
    """#219：开关开 → 低速松手以零初速进抛掷链，由重力落地后静止。"""
    controller = ThrowPhysicsController(BOUNDS)
    clock = FakeClock()
    sprite = _bounds_sprite((100.0, 100.0), clock)
    sprite.drag_physics = True
    sprite.drop_gravity = True

    _slow_drag_release(sprite, clock, (110.0, 110.0))

    assert sprite.interaction_state == INTERACTION_THROWN   # 进抛掷物理模式
    assert sprite.velocity == QPointF(0, 0)                 # 零初速（不是甩出速度）
    assert sprite.pos.y() == 100.0                          # 本 tick 位置未变

    ticks = 0
    while sprite.interaction_state == INTERACTION_THROWN and ticks < 1200:
        controller.tick([sprite], 1 / 60)
        ticks += 1
    assert sprite.interaction_state == INTERACTION_NORMAL   # 落地静止，交还行为层
    assert sprite.pos.y() == FLOOR_Y                        # 落在地板上
    assert sprite.velocity == QPointF(0, 0)


def test_drop_gravity_off_slow_release_stays_in_the_air():
    """#219 默认路径：开关关（默认）→ 维持现状，原地停在半空，不进抛掷链。"""
    controller = ThrowPhysicsController(BOUNDS)
    clock = FakeClock()
    sprite = _bounds_sprite((100.0, 100.0), clock)
    sprite.drag_physics = True
    assert sprite.drop_gravity is False                     # 默认关 = 现状

    _slow_drag_release(sprite, clock, (110.0, 110.0))

    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)
    for _ in range(30):
        controller.tick([sprite], 1 / 60)
    assert sprite.pos.y() == 100.0                          # 半空不动（零变化）


def test_drop_gravity_grounded_slow_release_rests_without_bounce():
    """#219：开关开 + 已贴地松手 → 立即静止，不弹跳。

    贴地这一路不进抛掷接管：一次 THROWN 会白绑悬空 clip（落地回待机再绑一次
    idle）+ 提交一次整池落地预热，而物理下一 tick 就把零初速的贴地 sprite 判
    回 normal——实测 GUI 线程 3 tick 从 0.48ms 涨到 2.00ms，还多两次后台
    ffmpeg 预热（见交付报告探针）。
    """
    controller = ThrowPhysicsController(BOUNDS)
    clock = FakeClock()
    # 抓取偏移 (10,10)：松手落点 y = FLOOR_Y 恰好贴地
    sprite = _bounds_sprite((100.0, FLOOR_Y), clock)
    sprite.drag_physics = True
    sprite.drop_gravity = True

    _slow_drag_release(sprite, clock, (110.0, FLOOR_Y + 10.0))

    assert sprite.pos.y() == FLOOR_Y                        # 贴地落点
    assert sprite.interaction_state == INTERACTION_NORMAL   # 立即静止
    assert sprite.velocity == QPointF(0, 0)                 # 无速度可弹

    for _ in range(30):
        controller.tick([sprite], 1 / 60)
        assert sprite.pos.y() == FLOOR_Y                    # 从未离开地面（不弹跳）
    assert sprite.velocity == QPointF(0, 0)
    assert sprite.interaction_state == INTERACTION_NORMAL


def test_zero_velocity_drop_on_ground_rests_without_bounce():
    """兜底口径：零初速 + 贴地进抛掷链也首个子步就静止（不弹跳）。

    与上一条相对：上一条锁「贴地不进链」，本条锁「就算进了链也不弹」——
    落地竖直速度只到 g·8ms=11.2px/s，远低于 GROUND_BOUNCE_STOP_VY=120，
    throw_step 直接归零（改动前即成立，作为复用链路的性质固定下来）。
    """
    controller = ThrowPhysicsController(BOUNDS)
    sprite = _make_sprite((100.0, FLOOR_Y))
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(0, 0))

    for _ in range(5):
        controller.tick([sprite], 1 / 60)
        assert sprite.pos.y() == FLOOR_Y
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)


def test_grounded_drop_skips_thrown_capture_and_landing_warm(monkeypatch):
    """#219：贴地松手连抛掷接管都不进——不提交整池落地预热、不打飞行期 pin。

    进一次 THROWN 不是零成本：``_enter_thrown`` 会绑悬空 clip 并调
    ``_warm_landing_idles``（后台逐条拉 ffmpeg 预热 idle 首帧 + 打
    ``_ffr_landing_pinned``），而贴地这一路物理下一 tick 就判回静止——预热
    当即被 ``_unpin_landing_idles`` 摘掉，纯浪费（实测 GUI 线程 3 tick
    0.48→2.00ms、后台多两次预热，见交付报告探针）。

    「提交」面用 ``_WARM_EXECUTOR.submit`` 记录（同步、确定性）而不是数
    clip 预热次数：预热跑在共享单 worker 上，全量套件里队列常排着别的用例的
    预热，数次数会把「用例顺序」写进断言（本地单跑绿、全量跑红）。半空对照
    证明同装配下这条链路确实会提交，贴地那半不是空过。
    """
    from pet import sprite_behavior
    from pet.sprite_behavior import BehaviorController, STATE_THROWN

    submitted: list = []
    monkeypatch.setattr(sprite_behavior._WARM_EXECUTOR, "submit",
                        lambda fn, *args: submitted.append((fn, args)))

    def scene(y: float):
        clip = WarmClip()
        lib = FakeLibrary(clip)
        lib.idles = ["fake"]
        clock = FakeClock()
        sprite = PetSprite(lib, pos=QPointF(100.0, y), scale=0.5)
        sprite.set_bounds(QRect(0, 0, 800, 600))
        sprite.bind_clip("fake")
        sprite._clock = clock
        sprite.drag_physics = True
        sprite.drop_gravity = True
        behavior = BehaviorController(BOUNDS_INT)
        behavior.predict_enabled = False
        return clip, sprite, clock, behavior

    grounded_clip, grounded, clock, behavior = scene(FLOOR_Y)
    _slow_drag_release(grounded, clock, (110.0, FLOOR_Y + 10.0))
    behavior.tick([grounded], 0.016)
    ThrowPhysicsController(BOUNDS).tick([grounded], 0.016)

    assert behavior.state_of(grounded) != STATE_THROWN    # 没进飞行接管
    assert grounded_clip._ffr_landing_pinned is False     # 没打飞行期 pin
    assert submitted == []                                # 没提交落地预热

    airborne_clip, airborne, clock, behavior = scene(50.0)
    _slow_drag_release(airborne, clock, (110.0, 60.0))
    behavior.tick([airborne], 0.016)

    assert behavior.state_of(airborne) == STATE_THROWN    # 半空：真进飞行
    assert airborne_clip._ffr_landing_pinned is True      # 起飞打 pin
    assert len(submitted) == 1                            # 起飞边沿提交一次
    assert airborne_clip in submitted[0][1][0]            # 提交的就是它的 idle 池


def test_release_velocity_direction_matches_drag():
    clock = FakeClock()
    sprite = _make_sprite((300.0, 300.0), clock=clock)
    sprite.drag_physics = True  # 抛掷接管只在物理拖拽开时生效（缺省 False）
    # 向右上甩：dx:dy = 3:-2
    release = _drag_steps(sprite, clock, (310.0, 310.0),
                          [(30.0, -20.0, 0.01)] * 8)
    sprite.on_release(release)

    assert sprite.interaction_state == INTERACTION_THROWN
    vx, vy = sprite.velocity.x(), sprite.velocity.y()
    assert vx > 0.0 and vy < 0.0                           # 方向：右上
    assert math.isclose(vx / -vy, 1.5, rel_tol=0.2)        # 位移方向比例保持


def test_release_speed_soft_clamped_below_cap():
    clock = FakeClock()
    sprite = _make_sprite((100.0, 100.0), clock=clock)
    sprite.drag_physics = True  # 抛掷接管只在物理拖拽开时生效（缺省 False）
    # 3000px / 0.05s ≈ 60000px/s：十倍于 cap 的暴力甩
    release = _drag_steps(sprite, clock, (110.0, 110.0),
                          [(300.0, 0.0, 0.005)] * 10)
    sprite.on_release(release)

    speed = math.hypot(sprite.velocity.x(), sprite.velocity.y())
    assert sprite.interaction_state == INTERACTION_THROWN
    assert physics_mod.DEAD_ZONE_SPEED < speed < sprite.throw_speed_cap


def test_drag_trail_sampling_and_pruning():
    clock = FakeClock()
    sprite = _make_sprite((100.0, 100.0), clock=clock)
    sprite.on_press(QPointF(110.0, 110.0))
    sprite.begin_drag()
    assert len(sprite.drag_trail) == 1                     # press 即首个样本

    for i in range(5):
        clock.advance(0.01)
        sprite.on_move(QPointF(120.0 + i * 10, 110.0))
    trail = sprite.drag_trail
    assert len(trail) == 6
    assert all(len(s) == 3 for s in trail)                 # (ts, x, y) 三元组
    assert trail[-1] == (clock.t, 160.0, 110.0)

    clock.advance(0.3)                                     # 远超 TRAIL_KEEP_SEC
    sprite.on_move(QPointF(170.0, 110.0))
    trail = sprite.drag_trail
    assert trail == [(clock.t, 170.0, 110.0)]              # 旧样本全部修剪

    clock.advance(0.01)
    sprite.on_release(QPointF(170.0, 110.0))
    assert sprite.drag_trail == []                         # 松手后清空


def test_press_is_click_candidate_until_begin_drag():
    """M3 语义：按下只是点击候选（不置拖拽态、不动画、探头不取消），
    过 DRAG_THRESHOLD 的 begin_drag 才升级真拖拽（旧 window.py:3169-3171）。"""
    sprite = _make_sprite((100.0, 100.0))
    sprite.set_velocity(QPointF(200, 50))

    sprite.on_press(QPointF(110.0, 120.0))

    assert not sprite.dragging
    assert sprite.interaction_state != INTERACTION_DRAG
    assert sprite.velocity == QPointF(0, 0)          # 按下即刹停（点击候选也停）

    sprite.begin_drag()
    assert sprite.dragging
    assert sprite.interaction_state == INTERACTION_DRAG

    sprite.begin_drag()                              # 幂等：重复升级不二次置态
    assert sprite.interaction_state == INTERACTION_DRAG


# ---------------------------------------------------------------- V-2：body_box 口径
def test_sprite_bounds_inset_by_body_box(monkeypatch):
    """V-2：抛掷反弹边界按身体框内缩（旧口径按整 canvas，反弹发生在透明边上）。

    body_box=(100,60,400,330) × scale 0.5 → 局部身体框 QRect(50,30,150,135)；
    bounds (0,0,800,600) → 左上角活动范围 (-50,-30,600,435)。
    """
    from pet import catalog

    monkeypatch.setattr(catalog, "character_body_box", lambda _cid, *a, **k: (100, 60, 400, 330))
    sprite = _make_sprite()
    c = ThrowPhysicsController(BOUNDS)
    assert c._sprite_bounds(sprite) == (-50.0, -30.0, 600.0, 435.0)


# ---------------------------------------------------------------- 落地首帧：预热 + 飞行期 pin
# 旧架构口径：起飞 ``_warm_landing_idles`` 后台预热全部 idle 首帧并给 clip 打
# ``_ffr_landing_pinned``（window.py:4356-4404，pin 只覆盖「起飞→落地」窗口——
# 飞行期 8MB 首帧预算的逐出会把刚暖好的落地首帧挤掉，实测定案 105~399ms 落地
# 冷解码卡顿）；落地（``_stop_physics``，:4289）与被拖拽打断
# （``_enter_physics_mode('drag')``，:4318）调 ``_unpin_landing_idles`` 摘 pin。
# sprite 世界没有等价物（sprite_physics 全文无 warm/pin 调用），落地回待机因此
# 命中冷首帧（GUI 线程同步拉 ffmpeg ~100ms），飞行期 idle 首帧还会被预算逐出。
#
# 落点在行为控制器的 THROWN 进入/退出边沿（位置积分仍归本控制器）：起飞 =
# ``_enter_thrown``，落地/被拖拽打断 = 退出该状态的那一 tick。
BOUNDS_INT = QRect(0, 0, 800, 600)


class WarmClip(FakeClip):
    """带首帧预热接口的假 clip（记录调用；可选阻塞，验证预热绝不在 GUI 线程跑）。"""

    def __init__(self, block: "threading.Event | None" = None):
        super().__init__()
        self._ffr_landing_pinned = False
        self.warm_calls = 0
        self.entered = threading.Event()
        self._block = block

    def warm_first_frame(self):
        self.warm_calls += 1
        self.entered.set()
        if self._block is not None:
            self._block.wait(5.0)


def _throw_scene(clip, *, idles=("fake",)):
    """起飞现场：sprite 已甩出（interaction_state = thrown），行为控制器待接管。"""
    from pet.sprite_behavior import BehaviorController

    lib = FakeLibrary(clip)
    lib.idles = list(idles)
    sprite = PetSprite(lib, pos=QPointF(100.0, 50.0), scale=0.5)
    sprite.bind_clip("fake")
    behavior = BehaviorController(BOUNDS_INT)
    behavior.predict_enabled = False
    sprite.interaction_state = INTERACTION_THROWN
    return sprite, behavior


def test_throw_entry_warms_and_pins_landing_idles_off_gui_thread():
    """起飞首帧预热 + 飞行期 pin：后台进行，GUI 线程零阻塞。"""
    import time

    release = threading.Event()
    clip = WarmClip(block=release)
    sprite, behavior = _throw_scene(clip)

    t0 = time.monotonic()
    behavior.tick([sprite], 0.016)                 # 起飞边沿
    elapsed = time.monotonic() - t0
    try:
        assert clip.entered.wait(5.0)              # 后台线程确实开始预热（事件同步）
        assert clip._ffr_landing_pinned is True    # 飞行期首帧绝不逐出
    finally:
        release.set()
    # warm 阻塞上限 5s：同步预热会在此吃到那 5s（旧机教训：GUI 同步预热在碰撞
    # 风暴下每只 ~100ms，多鱼互撞连续 200ms+ 卡顿）
    assert elapsed < 2.0
    assert clip.warm_calls == 1


def test_landing_returns_to_idle_and_unpins_landing_idles():
    """落地（物理切回 normal）→ 收口回待机并摘 pin（window.py:4289）。"""
    from pet.sprite_behavior import STATE_IDLE

    clip = WarmClip()
    sprite, behavior = _throw_scene(clip)
    physics = ThrowPhysicsController(BOUNDS)

    behavior.tick([sprite], 0.016)
    assert clip._ffr_landing_pinned is True

    sprite.set_pos(QPointF(100.0, FLOOR_Y))        # 贴地低速 → 落地静止
    sprite.set_velocity(QPointF(5, 0))
    physics.tick([sprite], 0.016)
    assert sprite.interaction_state == INTERACTION_NORMAL

    behavior.tick([sprite], 0.016)                 # 收口 tick：回待机
    assert behavior.state_of(sprite) == STATE_IDLE
    assert clip._ffr_landing_pinned is False


def test_flight_interrupted_by_drag_unpins_landing_idles():
    """飞行被拖拽打断（空中抓住）→ 摘 pin（window.py:4318 同款语义）。"""
    from pet.sprite_behavior import STATE_DRAG

    clip = WarmClip()
    sprite, behavior = _throw_scene(clip)

    behavior.tick([sprite], 0.016)
    assert clip._ffr_landing_pinned is True

    sprite.on_press(QPointF(110.0, 60.0))
    sprite.begin_drag()                            # 过阈值升级真拖拽
    behavior.tick([sprite], 0.016)

    assert behavior.state_of(sprite) == STATE_DRAG
    assert clip._ffr_landing_pinned is False


def test_landing_warm_skips_clip_without_warm_api():
    """缺 ``warm_first_frame`` 的播放器（旧替身/帧序列降级）→ getattr 跳过，不炸。"""
    clip = FakeClip()                              # 无首帧预热接口
    sprite, behavior = _throw_scene(clip)

    behavior.tick([sprite], 0.016)

    assert sprite.interaction_state == INTERACTION_THROWN   # 飞行不受影响
    assert clip._ffr_landing_pinned is True                 # pin 与预热独立


# ---------------------------------------------------------------- 跨屏抛掷（issue #319）
def test_thrown_crosses_screens_in_multi_rect_domain():
    """跨屏活动区（常态多矩形域）：抛掷边界按身体带取并集——并列双屏的
    右墙在副屏右缘，飞行直接穿缝（overlay 拓扑恢复 legacy #186 的能力）。"""
    screens = [QRectF(0, 0, 1920, 1040), QRectF(1920, 0, 1920, 1040)]
    controller = ThrowPhysicsController(QRectF(0, 0, 3840, 1040))
    sprite = _make_sprite((1500.0, 100.0))
    sprite.set_clamp_rects(screens)
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(5000, -800))  # 高速向右

    for _ in range(10):
        controller.tick([sprite], 0.016)

    assert sprite.pos.x() > 1920 - 320  # 穿过主屏右缘（320 = sprite 外接宽）
    assert sprite.velocity.x() > 0.0    # 没有在主屏右墙反弹回头


def test_thrown_crosses_staggered_hole_and_settles_on_screen():
    """错位拼接：正对空洞的那条带不再是墙——band 语义已退役为过缝通行
    （cross-screen-corridor SPEC，2026-10-22）：抛掷穿缝，落地时身体在空洞
    里则吸附到最近的屏。"""
    # 副屏整体下移 520px：y=100 处 x>1920 是空洞——旧语义在这里挡墙
    screens = [QRectF(0, 0, 1920, 1040), QRectF(1920, 520, 1920, 1040)]
    controller = ThrowPhysicsController(QRectF(0, 0, 3840, 1560))
    sprite = _make_sprite((1500.0, 100.0))
    sprite.set_clamp_rects(screens)
    sprite.interaction_state = INTERACTION_THROWN
    sprite.set_velocity(QPointF(5000, -800))

    for _ in range(10):
        controller.tick([sprite], 0.016)
    assert sprite.pos.x() > 1920.0  # 穿缝而过，没有在主屏右缘被挡回

    ticks = 0
    while sprite.interaction_state == INTERACTION_THROWN and ticks < 4000:
        sprite.advance(0.016)
        controller.tick([sprite], 0.016)
        ticks += 1
    body_center_x = sprite.pos.x() + 160
    body_center_y = sprite.pos.y() + 90
    assert any(r.contains(body_center_x, body_center_y) for r in screens), \
        f"落地后身体中心仍在空洞里: ({body_center_x}, {body_center_y})"
