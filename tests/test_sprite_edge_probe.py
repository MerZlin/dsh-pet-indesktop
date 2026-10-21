# -*- coding: utf-8 -*-
"""边缘探头 sprite 版 offscreen 单测（overlay/sprite 架构移植）。

语义蓝本是旧窗口机 pet/edge_probe.py（只读参考，不 import 其控制器；只
import 纯几何函数 probe_body_bounds 做口径对拍）。本文件覆盖：

- 进入条件：使能（config 每次判定前热读）/贴边/静止三条件齐备才进；
- 过渡动画时间基：注入时钟，一个 tick 跳 150ms 必须推进到 OutCubic(0.5)
  （tick 计数口径的实现会失败在这条）；
- 点击真实角色 → 拉直（角度 0、曝光 engage）→ EDGE_IDLE_SECONDS 后自动
  退回探头姿态；
- cancel 三触发源：拖拽开始 / 碰撞真撞击 / 抛掷态；碰撞取消后按
  EDGE_REENTRY_SECONDS 延迟重进；
- 多 sprite 各自独立；
- 钳制放宽（与旧 probe_body_bounds 逐值对拍）与取消后恢复，且不影响
  其它 sprite；
- 渲染：角度绕帧绘制矩形中心旋转（paint 变换挂载点，角度 0 零额外成本、
  squash 路径复用同一挂载点）；角度/曝光变化走既有脏矩形上报；DPR 不
  影响逻辑几何。

纪律（AGENTS.md 时序测试）：全部注入 FakeClock + 同步 tick，禁固定 sleep；
素材用纯 QImage 假 clip，不依赖 webm/ffmpeg。
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from pet.edge_probe import probe_body_bounds
from pet.overlay_window import OverlayWindow
from pet.pet_sprite import INTERACTION_DRAG, INTERACTION_NORMAL, INTERACTION_THROWN, PetSprite
from pet.sprite_behavior import STATE_MOVE, BehaviorController
from pet.sprite_edge_probe import (
    EDGE_ENGAGE_EXPOSURE,
    EDGE_ENTER_MS,
    EDGE_IDLE_SECONDS,
    EDGE_PEEK_EXPOSURE,
    EDGE_PROBE_ANGLE,
    EDGE_REENTRY_SECONDS,
    EDGE_REST_SECONDS,
    EDGE_RETURN_MS,
    EDGE_STRAIGHTEN_MS,
    ENTERING,
    OFF,
    PEEKING,
    RETURNING,
    STRAIGHTENED,
    STRAIGHTENING,
    SpriteEdgeProbeWorld,
    create_edge_probe_world,
)

app = QApplication.instance() or QApplication([])

BOUNDS = QRect(0, 0, 1280, 720)
SPRITE_SCALE = 0.5
LOGICAL_W, LOGICAL_H = 320, 180  # CANVAS(640x360) * scale：假库未声明 body_box


# ---------------------------------------------------------------- 测试替身
class FakeConfig:
    """config.get 协议最小替身（设置页热切换 = 测试中途 set）。"""

    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeClock:
    """可注入时钟：世界时间只由测试推进，禁固定 sleep 赌时序。"""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class FakeClip(QObject):
    frameChanged = Signal(int)

    def __init__(self, frames=2):
        super().__init__()
        self.frame = 0
        self._frames = frames
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return self._frames

    def duration(self):
        return self._frames * 42 / 1000.0

    def start(self):
        return True

    def stop(self):
        pass


class FakeLibrary:
    """同时满足 PetSprite（movie/no_mirror/character_id）与
    BehaviorController（idles/turns/moves/clicks 池 + duration）协议。"""

    def __init__(self):
        self.idles = ["idle1"]
        self.turns = ["turn1"]
        self.moves = ["walk"]
        self.clicks = ["click1"]
        self.acts: list[str] = []
        self.move_strides = {"walk": 120}
        self.no_mirror: set[str] = set()
        self.character_id = "fake_char"  # 无 manifest → body_box None → 画布即身体
        self._clips = {n: FakeClip() for n in self.idles + self.turns + self.moves + self.clicks}

    def movie(self, name, **_kw):
        return self._clips[name]

    def duration(self, name):
        return self._clips[name].duration()


class ScriptedRng:
    """确定性随机源：全部掷中移动桶（0.99 ≥ P_ACTS）。"""

    def __init__(self, roll=0.99):
        self._roll = roll

    def random(self):
        return self._roll

    def randint(self, a, b):
        return a

    def choice(self, seq):
        return seq[0]


class RecordingOverlay(OverlayWindow):
    """把 update() 的累加区域记下来，不做真实绘制调度（同 test_sprite_dirty_rect）。"""

    def __init__(self):
        super().__init__()
        self.updated = QRegion()

    def update(self, *args):  # noqa: D102 - 测试替身
        for a in args:
            if isinstance(a, QRegion):
                self.updated |= a
            elif isinstance(a, QRect):
                self.updated |= QRegion(a)


class RecordingPainter:
    """记录绘制变换调用序列的假 painter（验证变换挂载点与零额外成本）。"""

    def __init__(self):
        self.calls = []

    def save(self):
        self.calls.append(("save",))

    def restore(self):
        self.calls.append(("restore",))

    def translate(self, point):
        self.calls.append(("translate", float(point.x()), float(point.y())))

    def rotate(self, degrees):
        self.calls.append(("rotate", float(degrees)))

    def drawPixmap(self, *args):
        self.calls.append(("drawPixmap", type(args[0]).__name__))

    def names(self):
        return [c[0] for c in self.calls]


# ---------------------------------------------------------------- 夹具与工具
def _sprite(pos=(0.0, 300.0), *, lib=None, bounds=True):
    sprite = PetSprite(lib or FakeLibrary(), pos=QPointF(*pos), scale=SPRITE_SCALE)
    if bounds:
        sprite.set_bounds(QRect(BOUNDS))
    sprite.bind_clip("idle1")
    return sprite


def _world(enabled=True, *, clock=None, config=None):
    clock = clock or FakeClock()
    cfg = config if config is not None else FakeConfig(edge_probe_enabled=enabled)
    return SpriteEdgeProbeWorld(cfg, BOUNDS, clock=clock), clock


def _run(world, clock, sprites, seconds, *, step=1 / 60, advance=True):
    """模拟 overlay tick 循环：世界在前，sprite.advance 积分在后。"""
    remaining = float(seconds)
    while remaining > 1e-9:
        dt = min(step, remaining)
        clock.advance(dt)
        world.tick(sprites, dt)
        if advance:
            for sprite in sprites:
                sprite.advance(dt)
        remaining -= dt


def _drive_to_peeking(world, clock, sprites, *, step=0.02, limit=600):
    """静止贴边推进到 PEEKING（静止阈值 + 进入过渡）。

    limit 上限 = 12 秒仿真时间，远大于 EDGE_REST_SECONDS + EDGE_ENTER_MS；
    超限说明状态机没进入，直接失败而不是死循环。
    """
    for _ in range(limit):
        _run(world, clock, sprites, step, step=step)
        if all(world.mode_of(s) == PEEKING for s in sprites):
            return
    states = {s: world.mode_of(s) for s in sprites}
    raise AssertionError(f"未能推进到 PEEKING: {states}")


def _out_cubic(progress: float) -> float:
    return 1.0 - (1.0 - progress) ** 3


def _forward_rotate(point: QPointF, rect: QRect, angle_deg: float) -> QPointF:
    """把局部点绕 rect.center() 正向旋转（与 QPainter.rotate 同口径）。"""
    center = rect.center()
    rad = math.radians(angle_deg)
    dx, dy = point.x() - center.x(), point.y() - center.y()
    return QPointF(center.x() + dx * math.cos(rad) - dy * math.sin(rad),
                   center.y() + dx * math.sin(rad) + dy * math.cos(rad))


def _projected_body_span(sprite) -> tuple[float, float]:
    """sprite 身体框绕帧绘制矩形中心旋转后的投影 x 区间（测试侧独立算法）。"""
    body = sprite.body_rect()
    rect = sprite.rect()
    cx = rect.x() + rect.width() / 2.0
    cy = rect.y() + rect.height() / 2.0
    rad = math.radians(sprite.probe_angle)
    xs = []
    for px, py in (
        (body.left(), body.top()),
        (body.left() + body.width(), body.top()),
        (body.left(), body.top() + body.height()),
        (body.left() + body.width(), body.top() + body.height()),
    ):
        dx, dy = px - cx, py - cy
        xs.append(cx + dx * math.cos(rad) - dy * math.sin(rad))
    return min(xs), max(xs)


def _visible_ratio(sprite, bounds=QRect(BOUNDS)) -> float:
    left, right = _projected_body_span(sprite)
    visible = min(right, bounds.right() + 1.0) - max(left, float(bounds.left()))
    return visible / (right - left)


# ---------------------------------------------------------------- 使能与进入条件
def test_disabled_is_zero_cost_and_hot_switch_applies():
    """config 每次判定前热读：关时全路径早退，开时下个周期进入，关时立即退回。"""
    cfg = FakeConfig(edge_probe_enabled=False)
    world, clock = _world(config=cfg)
    sprite = _sprite((0.0, 300.0))

    _run(world, clock, [sprite], 1.0)
    assert world.mode_of(sprite) == OFF
    assert world.active is False
    assert sprite.probe_angle == 0.0
    assert sprite.probe_exposure == 1.0
    assert sprite.pos == QPointF(0, 300)  # 关时不碰位置

    cfg.set("edge_probe_enabled", True)  # 设置页热切换
    _run(world, clock, [sprite], EDGE_REST_SECONDS + 0.05, step=0.02)
    assert world.mode_of(sprite) == ENTERING
    _run(world, clock, [sprite], EDGE_ENTER_MS / 1000.0 + 0.05, step=0.02)
    assert world.mode_of(sprite) == PEEKING
    assert sprite.body_rect().left() < BOUNDS.left()  # 身体已藏出左缘

    cfg.set("edge_probe_enabled", False)
    world.tick([sprite], 1 / 60)
    assert world.mode_of(sprite) == OFF
    assert world.active is False
    assert sprite.probe_angle == 0.0
    assert sprite.probe_exposure == 1.0
    assert sprite.body_rect().left() >= BOUNDS.left()  # 常规钳制已恢复


def test_entry_requires_edge_and_sustained_rest():
    """进入条件：使能 + 贴边 + 持续静止；拖着经过/屏幕中间/拖拽中都不进。"""
    world, clock = _world()
    sprite = _sprite((500.0, 300.0))

    # 屏幕中间静止：不进入
    _run(world, clock, [sprite], 1.5)
    assert world.mode_of(sprite) == OFF

    # 贴边但仍在移动（拖着经过边缘）：静止累计被打断
    sprite.set_pos(QPointF(0, 300))
    sprite.set_velocity(QPointF(80, 0))
    _run(world, clock, [sprite], EDGE_REST_SECONDS * 0.5, step=0.02)
    assert world.mode_of(sprite) == OFF

    # 拖拽中（即使贴边不动）：不进入
    sprite.set_velocity(QPointF(0, 0))
    sprite.set_pos(QPointF(0, 300))
    sprite.interaction_state = INTERACTION_DRAG
    _run(world, clock, [sprite], 1.0)
    assert world.mode_of(sprite) == OFF

    # 贴边静止达到阈值：进入
    sprite.interaction_state = INTERACTION_NORMAL
    _run(world, clock, [sprite], EDGE_REST_SECONDS + 0.05, step=0.02)
    assert world.mode_of(sprite) == ENTERING


def test_entry_transition_is_wall_clock_based_not_tick_counts():
    """过渡动画时间基：单 tick 跳 150ms 必须到 OutCubic(0.5)，tick 计数口径会失败。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))

    for _ in range(200):
        _run(world, clock, [sprite], 0.02, step=0.02)
        if world.mode_of(sprite) == ENTERING:
            break
    assert world.mode_of(sprite) == ENTERING
    assert sprite.probe_angle == 0.0  # 过渡刚起步

    clock.advance(0.150)
    world.tick([sprite], 0.150)
    assert sprite.probe_angle == pytest.approx(
        EDGE_PROBE_ANGLE * _out_cubic(0.5), abs=0.2)

    clock.advance(0.200)
    world.tick([sprite], 0.200)
    assert world.mode_of(sprite) == PEEKING
    assert sprite.probe_angle == pytest.approx(EDGE_PROBE_ANGLE)
    assert sprite.probe_exposure == pytest.approx(EDGE_PEEK_EXPOSURE)


def test_pause_freezes_transition_progress_and_resume_continues():
    """隐藏冻结：pause 期间时钟跳 10 秒不吞掉过渡进度，resume 从原处继续。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))

    for _ in range(200):
        _run(world, clock, [sprite], 0.02, step=0.02)
        if world.mode_of(sprite) == ENTERING:
            break
    assert world.mode_of(sprite) == ENTERING

    world.pause()
    clock.advance(10.0)
    world.tick([sprite], 10.0)
    assert sprite.probe_angle == 0.0  # 冻结在起步处，未被 10 秒直接推完

    world.resume()
    clock.advance(0.150)
    world.tick([sprite], 0.150)
    assert sprite.probe_angle == pytest.approx(
        EDGE_PROBE_ANGLE * _out_cubic(0.5), abs=0.2)


# ---------------------------------------------------------------- 点击拉直 + 自动退回
def test_click_straightens_then_auto_returns_to_peek():
    """点击真实角色 → 拉直（0°/engage）→ 5 秒后自动退回探头姿态。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])

    assert world.on_sprite_clicked(sprite) is True
    assert world.mode_of(sprite) == STRAIGHTENING

    _run(world, clock, [sprite], EDGE_STRAIGHTEN_MS / 1000.0 + 0.05, step=0.02)
    assert world.mode_of(sprite) == STRAIGHTENED
    assert sprite.probe_angle == pytest.approx(0.0)
    assert sprite.probe_exposure == pytest.approx(EDGE_ENGAGE_EXPOSURE)
    ratio = _visible_ratio(sprite)
    assert ratio == pytest.approx(EDGE_ENGAGE_EXPOSURE, abs=0.02)

    # 倒计时未到：保持拉直
    _run(world, clock, [sprite], EDGE_IDLE_SECONDS - 0.5, step=0.1)
    assert world.mode_of(sprite) == STRAIGHTENED

    _run(world, clock, [sprite], 0.6, step=0.1)
    assert world.mode_of(sprite) == RETURNING
    _run(world, clock, [sprite], EDGE_RETURN_MS / 1000.0 + 0.05, step=0.02)
    assert world.mode_of(sprite) == PEEKING
    assert sprite.probe_angle == pytest.approx(EDGE_PROBE_ANGLE)
    assert sprite.probe_exposure == pytest.approx(EDGE_PEEK_EXPOSURE)


def test_click_on_non_probing_sprite_is_not_consumed():
    """未在探头：点击不由探头消费（壳层据此放行点击音效/clip）。"""
    world, clock = _world()
    sprite = _sprite((500.0, 300.0))
    assert world.on_sprite_clicked(sprite) is False
    assert world.mode_of(sprite) == OFF


def test_peek_exposure_is_ratio_of_projected_body_width():
    """曝光语义：左右缘的可见比例都等于 EDGE_PEEK_EXPOSURE（分母是投影 bbox）。"""
    world, clock = _world()
    left = _sprite((0.0, 300.0))
    right = _sprite((BOUNDS.width() - LOGICAL_W, 300.0))
    _drive_to_peeking(world, clock, [left, right])

    assert left.probe_angle == pytest.approx(EDGE_PROBE_ANGLE)
    assert right.probe_angle == pytest.approx(-EDGE_PROBE_ANGLE)
    assert _visible_ratio(left) == pytest.approx(EDGE_PEEK_EXPOSURE, abs=0.02)
    assert _visible_ratio(right) == pytest.approx(EDGE_PEEK_EXPOSURE, abs=0.02)


# ---------------------------------------------------------------- cancel 三触发源
def test_drag_started_cancels_and_restores_normal_clamp():
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])
    assert sprite.body_rect().left() < BOUNDS.left()

    world.on_sprite_drag_started(sprite)

    assert world.mode_of(sprite) == OFF
    assert world.active is False
    assert sprite.probe_angle == 0.0
    assert sprite.probe_exposure == 1.0
    assert sprite.body_rect().left() >= BOUNDS.left()


def test_collision_hit_cancels_and_reenters_after_delay():
    """碰撞真撞击：取消会话并按 EDGE_REENTRY_SECONDS 延迟重进（旧批 A 语义）。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])

    assert world.on_sprite_collision_hit(sprite) is True
    assert world.mode_of(sprite) == OFF
    assert world.reentry_remaining_of(sprite) == pytest.approx(EDGE_REENTRY_SECONDS)

    _run(world, clock, [sprite], EDGE_REENTRY_SECONDS - 0.5, step=0.1)
    assert world.mode_of(sprite) == OFF  # 倒计时内不重进

    _run(world, clock, [sprite], 0.5 + EDGE_ENTER_MS / 1000.0 + 0.1, step=0.05)
    assert world.mode_of(sprite) == PEEKING


def test_collision_on_non_probing_sprite_is_ignored():
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    # 未在探头：普通碰撞不取消任何会话 → 返回 False（调用方据此不 arm 彩蛋）
    assert world.on_sprite_collision_hit(sprite) is False
    assert world.mode_of(sprite) == OFF
    assert world.reentry_remaining_of(sprite) == 0.0


def test_drag_during_reentry_countdown_voids_it():
    """重进倒计时期间拖拽 → 作废倒计时，改走常规静止阈值（不等剩余 5 秒）。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])
    assert world.on_sprite_collision_hit(sprite) is True
    assert world.reentry_remaining_of(sprite) > 0.0

    sprite.interaction_state = INTERACTION_DRAG
    world.on_sprite_drag_started(sprite)
    assert world.reentry_remaining_of(sprite) == 0.0

    _run(world, clock, [sprite], 2.0, step=0.05)
    assert world.mode_of(sprite) == OFF  # 拖拽期间不进入

    sprite.interaction_state = INTERACTION_NORMAL
    world.on_sprite_drag_released(sprite)
    _run(world, clock, [sprite], EDGE_REST_SECONDS + 0.05, step=0.02)
    assert world.mode_of(sprite) == ENTERING  # 只等静止阈值，不是剩余 5 秒


def test_thrown_state_cancels_and_arms_reentry():
    """抛掷：tick 检测到 thrown 即取消并arm 重进倒计时（物理落地后 tick 自行重进）。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])

    sprite.interaction_state = INTERACTION_THROWN
    world.tick([sprite], 1 / 60)

    assert world.mode_of(sprite) == OFF
    assert sprite.probe_exposure == 1.0
    assert world.reentry_remaining_of(sprite) > 0.0


def test_cancel_is_idempotent_and_forget_clears_state():
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])

    world.cancel(sprite, "drag_away")
    world.cancel(sprite, "drag_away")  # 幂等
    assert world.mode_of(sprite) == OFF

    world.forget(sprite)
    assert world.mode_of(sprite) == OFF
    assert world.reentry_remaining_of(sprite) == 0.0


# ---------------------------------------------------------------- 多 sprite 独立
def test_multiple_sprites_are_independent():
    world, clock = _world()
    left = _sprite((0.0, 300.0))
    right = _sprite((BOUNDS.width() - LOGICAL_W, 300.0))
    _drive_to_peeking(world, clock, [left, right])

    assert world.side_of(left) == "left"
    assert world.side_of(right) == "right"
    assert world.angle_of(left) == pytest.approx(EDGE_PROBE_ANGLE)
    assert world.angle_of(right) == pytest.approx(-EDGE_PROBE_ANGLE)

    # 点击左：右的姿态不受影响
    world.on_sprite_clicked(left)
    _run(world, clock, [left, right], EDGE_STRAIGHTEN_MS / 1000.0 + 0.05, step=0.02)
    assert world.mode_of(left) == STRAIGHTENED
    assert world.mode_of(right) == PEEKING
    assert world.angle_of(right) == pytest.approx(-EDGE_PROBE_ANGLE)
    assert right.probe_exposure == pytest.approx(EDGE_PEEK_EXPOSURE)

    # 取消左：右仍在探头
    world.on_sprite_drag_started(left)
    assert world.mode_of(left) == OFF
    assert world.mode_of(right) == PEEKING
    assert world.active is True


# ---------------------------------------------------------------- 钳制放宽与恢复
def test_probe_clamp_matches_old_probe_body_bounds_and_restores():
    """探头放宽域与旧 probe_body_bounds 逐值一致；取消恢复常规钳制。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(world, clock, [sprite])

    body = sprite.body_rect()
    assert body.left() < BOUNDS.left()  # 身体已藏出左缘
    body_local = body.translated(-sprite.rect().topLeft())
    assert sprite.probe_clamp_bounds().toAlignedRect() == probe_body_bounds(BOUNDS, body_local)

    # 放宽域内可继续把身体整体推出（旧语义：左右各放宽一个身体宽）
    target = QPointF(BOUNDS.left() - body.width() + 4, sprite.pos.y())
    sprite.set_pos(target)
    assert sprite.pos.x() == pytest.approx(target.x())
    assert sprite.pos.x() < BOUNDS.left() - body.width() + 5

    # 取消：清姿态即恢复常规钳制（身体钳回界内）
    world.cancel(sprite, "drag_away")
    assert sprite.probe_clamp_bounds() is None
    assert sprite.body_rect().left() >= BOUNDS.left()
    assert sprite.pos.x() == pytest.approx(BOUNDS.left())


def test_probe_clamp_does_not_affect_other_sprites():
    world, clock = _world()
    peeking = _sprite((0.0, 300.0))
    other = _sprite((400.0, 300.0))
    _drive_to_peeking(world, clock, [peeking])

    other.set_pos(QPointF(-200.0, 300.0))
    assert other.pos.x() == pytest.approx(BOUNDS.left())  # 常规钳制到贴边
    assert other.body_rect().left() >= BOUNDS.left()
    assert other.probe_clamp_bounds() is None


# ---------------------------------------------------------------- 渲染
def test_paint_transform_mounts_at_frame_center_with_zero_cost_at_zero_angle():
    sprite = _sprite((0.0, 300.0))

    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["drawPixmap"]  # 角度 0：不 save/translate/rotate

    sprite.set_probe_pose(EDGE_PROBE_ANGLE, EDGE_PEEK_EXPOSURE)
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["save", "translate", "rotate", "translate",
                               "drawPixmap", "restore"]
    rect = sprite.rect()
    center = rect.center()  # QRect.center() 口径：与渲染侧 begin_rotation 同一取数
    cx, cy = float(center.x()), float(center.y())
    assert painter.calls[1] == ("translate", cx, cy)
    assert painter.calls[2] == ("rotate", EDGE_PROBE_ANGLE)
    assert painter.calls[3] == ("translate", -cx, -cy)
    assert painter.calls[4][1] == "QPointF"  # 常规路径：按 pos 画

    sprite.clear_probe_pose()
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["drawPixmap"]


def test_paint_squash_path_reuses_the_same_transform_mount():
    sprite = _sprite((0.0, 300.0))
    sprite.set_probe_pose(EDGE_PROBE_ANGLE, EDGE_PEEK_EXPOSURE)
    sprite.squash()

    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["save", "translate", "rotate", "translate",
                               "drawPixmap", "restore"]
    assert painter.calls[4][1] == "QRect"  # squash 路径：按压缩矩形画


def test_probe_paint_bounds_contains_rect_and_grows_with_rotation():
    sprite = _sprite((0.0, 300.0))
    assert sprite.paint_bounds() == sprite.rect()  # 角度 0：等值，无额外区域
    assert sprite.probe_active is False

    sprite.set_probe_pose(EDGE_PROBE_ANGLE, EDGE_PEEK_EXPOSURE)
    bounds = sprite.paint_bounds()
    assert sprite.probe_active is True
    assert bounds != sprite.rect()
    assert bounds.contains(sprite.rect())
    assert bounds.width() > sprite.rect().width()


def test_probe_pose_changes_report_dirty_region_through_overlay():
    """角度/曝光变化走既有脏矩形上报：update 区域覆盖旋转后的绘制外接矩形。"""
    clock = FakeClock()
    world = SpriteEdgeProbeWorld(FakeConfig(edge_probe_enabled=True), BOUNDS, clock=clock)
    overlay = RecordingOverlay()
    sprite = _sprite((0.0, 300.0))
    overlay.add_sprite(sprite)
    overlay.before_sprites_advance = lambda dt: world.tick(overlay.sprites, dt)

    overlay._on_tick(dt=1 / 60)
    for _ in range(200):
        if world.mode_of(sprite) == PEEKING:
            break
        clock.advance(0.02)
        overlay._on_tick(dt=0.02)

    assert world.mode_of(sprite) == PEEKING
    paint = sprite.paint_bounds()
    assert paint != sprite.rect()
    assert QRegion(paint).subtracted(overlay.updated).isEmpty(), (
        f"脏区域未覆盖旋转外接矩形 {paint}；已记录={overlay.updated.boundingRect()}")
    assert QRegion(sprite.rect()).subtracted(overlay.updated).isEmpty()


def test_dpr_change_keeps_probe_geometry_logical():
    """DPR 兼容：旋转/曝光都留在逻辑坐标，DPR 只影响渲染像素。"""
    sprite = _sprite((0.0, 300.0))
    sprite.set_probe_pose(EDGE_PROBE_ANGLE, EDGE_PEEK_EXPOSURE)
    logical = sprite.rect()
    bounds = sprite.paint_bounds()

    sprite.set_dpr(1.5)

    assert sprite.rect() == logical
    assert sprite.paint_bounds() == bounds
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["save", "translate", "rotate", "translate",
                               "drawPixmap", "restore"]


def test_alpha_hit_testing_follows_probe_rotation():
    """命中跟随旋转：旋转后身体画在哪，alpha 命中就在哪（点击拉直的入口）。"""
    clip = FakeClip()
    clip.image = QImage(640, 360, QImage.Format.Format_ARGB32)
    clip.image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(clip.image)
    painter.fillRect(QRect(0, 0, 40, 40), Qt.GlobalColor.red)  # 左上角一块不透明
    painter.end()
    lib = FakeLibrary()
    lib._clips["idle1"] = clip
    sprite = PetSprite(lib, pos=QPointF(0, 0), scale=1.0)
    sprite.set_bounds(QRect(BOUNDS))
    sprite.bind_clip("idle1")
    sprite.paint(RecordingPainter())  # 建首帧（含命中图）

    assert sprite.alpha_at(QPointF(20, 20)) == 255
    assert sprite.alpha_at(QPointF(320, 180)) == 0

    sprite.set_probe_pose(180.0, EDGE_PEEK_EXPOSURE)  # 180°：块转到右下
    rotated = _forward_rotate(QPointF(20, 20), sprite.rect(), 180.0)
    assert sprite.alpha_at(rotated) == 255
    assert sprite.alpha_at(QPointF(20, 20)) == 0  # 原位置已空


# ---------------------------------------------------------------- 与行为控制器共存
def test_behavior_does_not_clamp_or_walk_a_probing_sprite():
    """探头会话期间行为控制器不钳制、不游荡（只允许待机/转向语义）。"""
    world, clock = _world()
    sprite = _sprite((0.0, 300.0))
    behavior = BehaviorController(BOUNDS, rng=ScriptedRng())
    _drive_to_peeking(world, clock, [sprite])
    assert sprite.probe_active is True
    pos_before = QPointF(sprite.pos)
    states = set()

    for _ in range(60):
        clock.advance(0.05)
        behavior.tick([sprite], 0.05)
        world.tick([sprite], 0.05)
        sprite.advance(0.05)
        states.add(behavior.state_of(sprite))

    assert sprite.pos == pos_before  # 未被钳回屏内、未被游荡带走
    assert sprite.probe_angle == pytest.approx(EDGE_PROBE_ANGLE)
    assert STATE_MOVE not in states


def test_behavior_still_clamps_a_normal_sprite():
    """对照组：非探头 sprite 的越界位置仍由行为控制器钳回（闸门是探头专属）。"""
    sprite = _sprite((0.0, 300.0))
    behavior = BehaviorController(BOUNDS, rng=ScriptedRng(roll=0.0))
    sprite.pos = QPointF(-200.0, 300.0)  # 绕开 set_pos 直接置越界，模拟外部写入

    behavior.tick([sprite], 0.05)

    assert sprite.pos.x() == pytest.approx(BOUNDS.left())


# ---------------------------------------------------------------- 工厂与查询
def test_factory_builds_world_and_tolerates_missing_config():
    world = create_edge_probe_world(FakeConfig(edge_probe_enabled=True), BOUNDS)
    assert isinstance(world, SpriteEdgeProbeWorld)
    assert world.bounds == QRect(BOUNDS)

    no_cfg = create_edge_probe_world(None, BOUNDS)
    sprite = _sprite((0.0, 300.0))
    no_cfg.tick([sprite], 0.1)
    assert no_cfg.mode_of(sprite) == OFF
    assert no_cfg.side_of(sprite) is None
    assert no_cfg.angle_of(sprite) == 0.0
    assert no_cfg.exposure_of(sprite) == 1.0


def test_unknown_sprite_queries_are_safe():
    world, _clock = _world()
    sprite = _sprite((500.0, 300.0))
    assert world.mode_of(sprite) == OFF
    assert world.is_probing(sprite) is False
    assert world.angle_of(sprite) == 0.0
    assert world.exposure_of(sprite) == 1.0
    assert world.reentry_remaining_of(sprite) == 0.0


# ---------------------------------------------------------------- overlay 壳接线
def _shell_with_probe(tmp_path, clock):
    """真实 OverlayShell + 真实 SpriteEdgeProbeWorld（只注入世界时钟）。

    ``edge_probe_enabled`` 是默认关的（默认 False），overlay 却是默认拓扑，
    故显式打开；时钟用 ``SpriteEdgeProbeWorld`` 明确支持的注入口，避免固定
    sleep 赌时序（AGENTS.md 时序测试纪律）。
    """
    import tests.test_sprite_menu_facade as fac

    shell, _lib = fac._make_shell(tmp_path, {"edge_probe_enabled": True})
    shell._probe._clock = clock
    return shell


def _menu_action(container, label):
    """递归找 label 对应的 QAction（现代模板把「回到右下角」收在子菜单里）。"""
    for action in container.actions():
        if action.isSeparator():
            continue
        if action.text() == label:
            return action
        submenu = action.menu()
        if submenu is not None:
            found = _menu_action(submenu, label)
            if found is not None:
                return found
    return None


def _probe_ticks(world, clock, sprite, seconds, *, step=1 / 60):
    """只推进探头世界（不驱动 sprite.advance：本用例断言姿态与位置改写）。"""
    remaining = float(seconds)
    while remaining > 1e-9:
        dt = min(step, remaining)
        clock.advance(dt)
        world.tick([sprite], dt)
        remaining -= dt


@pytest.mark.parametrize("entry", ["shell", "menu"])
def test_overlay_return_corner_cancels_active_probe_session(tmp_path, entry):
    """overlay「回右下角」必须先取消探头会话（legacy 窗侧 window.py 同语义）。

    复现（修复前）：PEEKING 稳态角色（±45°、曝光 0.55）回角后会话仍在——角色
    斜着停在右下角，且下一次点击触发的拉直过渡会把 x 写回**旧屏缘**（
    ``_apply_pose`` 的 ``probe_target_x``），表现为「点一下瞬移回原来贴的那条边」。

    两个入口都验：``shell`` = 托盘主宠/子宠与设置重置走的那条
    （``OverlayShell._go_default_corner``）；``menu`` = 右键菜单「回到右下角」
    （建造器经 facade 回调落回同一方法）。第二入口此前在 facade 里另写了一份
    「set_pos 落角」，所以它单独构成一条回归面。
    """
    from pet import catalog
    from pet.sprite_menu_facade import build_sprite_full_menu

    clock = FakeClock()
    shell = _shell_with_probe(tmp_path, clock)
    sprite = shell.sprite
    bounds = QRect(shell._bounds)
    menu = None
    try:
        # 贴左缘静止 → 推进到 PEEKING 稳态（左侧会话，姿态 ±45°）
        sprite.set_pos(QPointF(0.0, bounds.y() + 400.0))
        _probe_ticks(shell._probe, clock, sprite,
                     EDGE_REST_SECONDS + EDGE_ENTER_MS / 1000.0 + 0.2)
        assert shell._probe.mode_of(sprite) == PEEKING, "前置：必须已进入探头稳态"
        assert shell._probe.side_of(sprite) == "left"
        assert abs(sprite.probe_angle) == pytest.approx(EDGE_PROBE_ANGLE)

        if entry == "menu":
            menu = build_sprite_full_menu(shell)
            _menu_action(menu, "回到右下角").trigger()
        else:
            shell._go_default_corner(sprite)

        corner = QPointF(bounds.x() + bounds.width() - 1 - sprite.rect().width()
                         - catalog.CORNER_MARGIN,
                         bounds.y() + bounds.height() - 1 - sprite.rect().height())
        assert sprite.pos == corner, "回角落位"
        assert shell._probe.mode_of(sprite) == OFF, "回角必须取消探头会话"
        assert sprite.probe_angle == 0.0 and sprite.probe_exposure == 1.0, \
            "姿态必须复位（否则角色斜着停在右下角）"

        # 点击不再被探头消费（会话已退出），随后的 tick 也不再改写 x
        assert shell._probe.on_sprite_clicked(sprite) is False
        _probe_ticks(shell._probe, clock, sprite, 0.5)
        assert sprite.pos == corner, "探头残留会把角色瞬移回旧屏缘"
    finally:
        if menu is not None:
            menu.close()
        shell._delete_runtime_marker()
