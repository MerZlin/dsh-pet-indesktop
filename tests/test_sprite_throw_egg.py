# -*- coding: utf-8 -*-
"""throw_egg 彩蛋 sprite 版 offscreen 单测（overlay/sprite 架构移植）。

语义蓝本是旧窗口机 pet/throw_egg.py（只读参考，不 import 其控制器；只
import 两个阈值常量做「单一来源」对拍）。本文件覆盖：

- arm 条件严格对齐旧机：只有「探头会话存在且被真撞击 cancel」才 arm，
  普通抛掷（非探头状态被击飞）不 arm（批 A 现状）；
- 角度跟随：屏幕坐标 y 朝下口径（右 90/下 180/上 0/左 270），触界 780 /
  空中 150 双阈值分档，低速冻结角度但会话继续（不设飞行时间硬上限）；
- 回正三触发源：低速触界、飞行中低速撞其它桌宠、落地停稳（physics 把
  interaction_state 切回 normal）兜底——含真实 ThrowPhysicsController
  跑完整飞行并落地；
- 多 sprite 各自独立；
- 渲染：抛掷旋转走既有 begin_rotation/end_rotation 管线（与探头同管线、
  同旋转中心），角度 0 零成本；与探头姿态同轴合成不互踩；脏矩形经既有
  _notify_dirty 通道、覆盖旋转投影；alpha 命中跟随抛掷/合成旋转。

纪律（AGENTS.md 时序测试）：全部同步 tick + 注入 FakeClock（探头世界），
固定 dt 直调，禁固定 sleep；素材用纯 QImage 假 clip，不依赖 webm/ffmpeg。
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from pet.overlay_window import OverlayWindow
from pet.pet_sprite import (
    INTERACTION_DRAG,
    INTERACTION_NORMAL,
    INTERACTION_THROWN,
    PetSprite,
)
from pet.sprite_edge_probe import PEEKING, SpriteEdgeProbeWorld
from pet.sprite_physics import ThrowPhysicsController
from pet.sprite_throw_egg import (
    SpriteThrowEggWorld,
    create_throw_egg_world,
    touching_boundary,
)
from pet.throw_egg import THROW_EGG_AIR_FOLLOW_SPEED, THROW_EGG_RECOVER_SPEED
from pet.window_effects import rotated_region_bounds

app = QApplication.instance() or QApplication([])

BOUNDS = QRect(0, 0, 1280, 720)
SPRITE_SCALE = 0.5
LOGICAL_W, LOGICAL_H = 320, 180  # CANVAS(640x360) * scale：假库未声明 body_box
RIGHT_X = BOUNDS.width() - LOGICAL_W      # 贴右缘的 sprite 左上角 x
BOTTOM_Y = BOUNDS.height() - LOGICAL_H    # 贴地 sprite 左上角 y


# ---------------------------------------------------------------- 测试替身
class FakeConfig:
    """config.get 协议最小替身（探头世界使能键）。"""

    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeClock:
    """可注入时钟（探头世界）。"""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class FakeProbe:
    """探头世界最小替身：公开状态即 arm 条件复核的输入。"""

    def __init__(self, *, probing=False, reentry=0.0):
        self.probing = bool(probing)
        self.reentry = float(reentry)

    def is_probing(self, sprite):
        return self.probing

    def reentry_remaining_of(self, sprite):
        return self.reentry


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
    """满足 PetSprite 协议的最小白板（character_id 无 manifest → 画布即身体）。"""

    def __init__(self):
        self.no_mirror: set[str] = set()
        self.character_id = "fake_char"
        self._clip = FakeClip()

    def movie(self, name, **_kw):
        return self._clip


class RecordingOverlay(OverlayWindow):
    """记录 update() 区域，不做真实绘制调度（同 test_sprite_edge_probe）。"""

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
    """记录绘制变换调用序列的假 painter（验证挂载点与零额外成本）。"""

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

    def rotations(self):
        return [c[1] for c in self.calls if c[0] == "rotate"]


# ---------------------------------------------------------------- 夹具与工具
def _sprite(pos=(400.0, 200.0)) -> PetSprite:
    sprite = PetSprite(FakeLibrary(), pos=QPointF(*pos), scale=SPRITE_SCALE)
    sprite.set_bounds(QRect(BOUNDS))
    sprite.bind_clip("idle1")
    return sprite


def _thrown(sprite, vx, vy):
    """置抛掷态 + 速度（碰撞世界在触发 listener 前就已完成这两件事）。"""
    sprite.set_velocity(QPointF(vx, vy))
    sprite.interaction_state = INTERACTION_THROWN


def _world(**kwargs) -> SpriteThrowEggWorld:
    return SpriteThrowEggWorld(BOUNDS, **kwargs)


def _drive_to_peeking(probe, clock, sprite, *, step=0.02, limit=600):
    """同步推进真实探头世界到 PEEKING（贴边静止 → 进入过渡）。"""
    for _ in range(limit):
        clock.advance(step)
        probe.tick([sprite], step)
        if probe.mode_of(sprite) == PEEKING:
            return
    raise AssertionError(f"未能推进到 PEEKING: {probe.mode_of(sprite)}")


def _forward_rotate(point: QPointF, rect: QRect, angle_deg: float) -> QPointF:
    """把局部点绕 rect.center() 正向旋转（与 QPainter.rotate 同口径）。"""
    center = rect.center()
    rad = math.radians(angle_deg)
    dx, dy = point.x() - center.x(), point.y() - center.y()
    return QPointF(center.x() + dx * math.cos(rad) - dy * math.sin(rad),
                   center.y() + dx * math.sin(rad) + dy * math.cos(rad))


# ---------------------------------------------------------------- 阈值单一来源
def test_thresholds_come_from_the_old_controller_not_copied():
    """阈值常量必须是 pet.throw_egg 的同一对象（禁止复制数值）。"""
    from pet import sprite_throw_egg, throw_egg

    assert sprite_throw_egg.THROW_EGG_RECOVER_SPEED is throw_egg.THROW_EGG_RECOVER_SPEED
    assert sprite_throw_egg.THROW_EGG_AIR_FOLLOW_SPEED is throw_egg.THROW_EGG_AIR_FOLLOW_SPEED


# ---------------------------------------------------------------- arm 条件
def test_arm_requires_probe_session_cancelled_by_real_collision():
    """只有「本次真取消了活跃探头会话」才 arm；残留倒计时不作证据。"""
    probe = FakeProbe()
    world = _world(probe=probe)
    sprite = _sprite()
    _thrown(sprite, 900.0, 0.0)

    world.tick([sprite], 1 / 60)          # thrown 本身不是 arm 条件
    world.on_probe_collision_throw(sprite)
    assert world.is_active(sprite) is False
    assert world.active is False
    assert sprite.throw_rotation == 0.0

    probe.probing = True                  # 反向接线顺序（先 egg 后 probe）：
    world.on_probe_collision_throw(sprite)  # 缺省 None → 回退 is_probing
    assert world.is_active(sprite) is True
    assert world.active is True
    assert world.angle_of(sprite) == 0.0
    assert sprite.throw_rotation == 0.0

    # 显式事实：本次真取消了探头会话（会话已不在也照样 arm）
    world.forget(sprite)
    probe.probing = False
    world.on_probe_collision_throw(sprite, probe_cancelled=True)
    assert world.is_active(sprite) is True

    # 残留倒计时（旧泄漏通道）不再是证据：会话已不在 → 不 arm
    world.forget(sprite)
    probe.reentry = 5.0
    world.on_probe_collision_throw(sprite)
    assert world.is_active(sprite) is False

    # 显式事实：本次没有取消任何活跃会话 → 绝不 arm
    world.forget(sprite)
    world.on_probe_collision_throw(sprite, probe_cancelled=False)
    assert world.is_active(sprite) is False


def test_residual_reentry_countdown_never_rearms_after_egg_end():
    """彩蛋 end() 后探头重进倒计时仍残留，5s 窗口内再被撞不得误 arm。

    实机日志 arm 30 次 vs 探头真实退出仅 11 次：`_probe_hit_confirmed` 把
    探头世界的 ``reentry_remaining_of > 0``（cancel 时 arm 的 5 秒倒计时，
    彩蛋 end() 不会清）当成「本次撞击真取消了活跃会话」的证明，5s 内再被
    撞就误 re-arm。修法 = 显式事实传递（on_sprite_collision_hit 的返回值），
    残留态不再参与判定。
    """
    clock = FakeClock()
    probe = SpriteEdgeProbeWorld(FakeConfig(edge_probe_enabled=True), BOUNDS,
                                 clock=clock)
    world = create_throw_egg_world(BOUNDS, probe=probe)
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(probe, clock, sprite)

    hit = probe.on_sprite_collision_hit(sprite)      # 第一次：真取消会话
    assert hit is True
    _thrown(sprite, 900.0, 0.0)
    world.on_probe_collision_throw(sprite, probe_cancelled=hit)
    assert world.is_active(sprite) is True

    world.end(sprite, "settled")                     # 彩蛋结束（落地回正）
    assert world.is_active(sprite) is False
    assert probe.reentry_remaining_of(sprite) > 0.0  # 倒计时残留仍在窗口内

    clock.advance(1.0)                               # 走 1s：仍在 5s 窗口内
    probe.tick([sprite], 1 / 60)
    assert probe.reentry_remaining_of(sprite) > 0.0

    hit2 = probe.on_sprite_collision_hit(sprite)     # 第二次：探头无活跃会话
    assert hit2 is False
    world.on_probe_collision_throw(sprite, probe_cancelled=hit2)
    assert world.is_active(sprite) is False          # 不得误 re-arm

    # 缺省口径（旧调用/反向接线）同样不得靠残留倒计时 arm
    world.on_probe_collision_throw(sprite)
    assert world.is_active(sprite) is False


def test_arm_falls_back_to_probe_pose_without_probe_world():
    """未注入探头世界：退化为 sprite 姿态口径（曝光<1 或已有探头角）。"""
    world = _world()
    plain = _sprite()
    _thrown(plain, 900.0, 0.0)
    world.on_probe_collision_throw(plain)
    assert world.is_active(plain) is False

    peeking = _sprite()
    peeking.set_probe_pose(45.0, 0.55)
    _thrown(peeking, 900.0, 0.0)
    world.on_probe_collision_throw(peeking)
    assert world.is_active(peeking) is True

    # 显式事实优先：本次未取消会话 → 姿态兜底也不 arm
    explicit = _sprite()
    explicit.set_probe_pose(45.0, 0.55)
    _thrown(explicit, 900.0, 0.0)
    world.on_probe_collision_throw(explicit, probe_cancelled=False)
    assert world.is_active(explicit) is False


def test_real_probe_collision_arms_after_pose_cleared():
    """真接线语义：探头会话被真撞击取消 → 姿态已清 → 彩蛋仍能 arm 并跟随。"""
    clock = FakeClock()
    probe = SpriteEdgeProbeWorld(FakeConfig(edge_probe_enabled=True), BOUNDS, clock=clock)
    world = create_throw_egg_world(BOUNDS, probe=probe)
    sprite = _sprite((0.0, 300.0))
    _drive_to_peeking(probe, clock, sprite)
    assert sprite.probe_angle != 0.0
    assert sprite.probe_active is True

    # 壳层碰撞 listener 的顺序：先探头 cancel（真撞击），再彩蛋入口；
    # cancel 的返回值就是「本次真取消了会话」的显式事实
    hit = probe.on_sprite_collision_hit(sprite)
    assert hit is True
    _thrown(sprite, 900.0, 0.0)
    world.on_probe_collision_throw(sprite, probe_cancelled=hit)

    assert world.is_active(sprite) is True
    assert sprite.probe_exposure == 1.0     # 探头姿态已清
    assert sprite.probe_angle == 0.0
    assert sprite.throw_rotation == 0.0

    world.tick([sprite], 1 / 60)
    assert sprite.throw_rotation == pytest.approx(90.0)


# ---------------------------------------------------------------- 角度跟随与分档
@pytest.mark.parametrize(("vx", "vy", "expected"), [
    (400.0, 0.0, 90.0),    # 向右飞
    (0.0, 400.0, 180.0),   # 向下（屏幕坐标 y 朝下）
    (-400.0, 0.0, 270.0),  # 向左飞
    (0.0, -400.0, 0.0),    # 向上
])
def test_angle_follows_velocity_direction_in_screen_coordinates(vx, vy, expected):
    world = _world()
    sprite = _sprite((400.0, 200.0))  # 屏幕中间：不触界 → 空中分档
    world.arm(sprite)
    _thrown(sprite, vx, vy)

    world.tick([sprite], 1 / 60)

    assert sprite.throw_rotation == pytest.approx(expected)
    assert world.angle_of(sprite) == pytest.approx(expected)


def test_air_threshold_freezes_angle_below_follow_speed_without_ending():
    """空中 150 分档：低于它冻结角度但会话继续；不设飞行时间硬上限。"""
    world = _world()
    sprite = _sprite((400.0, 200.0))
    world.arm(sprite)
    _thrown(sprite, 400.0, 0.0)
    world.tick([sprite], 1 / 60)
    assert sprite.throw_rotation == pytest.approx(90.0)

    _thrown(sprite, THROW_EGG_AIR_FOLLOW_SPEED - 1.0, 0.0)
    world.tick([sprite], 1 / 60)
    assert world.is_active(sprite) is True
    assert sprite.throw_rotation == pytest.approx(90.0)  # 冻结而非回正

    for _ in range(600):  # 空转 10 秒仿真时间：不触界就一直是会话
        world.tick([sprite], 1 / 60)
    assert world.is_active(sprite) is True
    assert sprite.throw_rotation == pytest.approx(90.0)


def test_touching_boundary_uses_recover_threshold_then_recovers():
    """触界 780 分档：高于它继续跟随，低于它立刻回正。"""
    world = _world()
    ground = _sprite((400.0, float(BOTTOM_Y)))
    assert touching_boundary(ground, BOUNDS) is True

    world.arm(ground)
    _thrown(ground, THROW_EGG_RECOVER_SPEED + 100.0, 0.0)
    world.tick([ground], 1 / 60)
    assert world.is_active(ground) is True
    assert ground.throw_rotation == pytest.approx(90.0)

    _thrown(ground, THROW_EGG_RECOVER_SPEED - 1.0, 0.0)
    world.tick([ground], 1 / 60)
    assert world.is_active(ground) is False
    assert ground.throw_rotation == 0.0


def test_touching_boundary_matches_physics_bounce_geometry():
    """触界口径 = 身体框贴 bounds 四边（与抛掷物理反弹/落地同源）。"""
    assert touching_boundary(_sprite((0.0, 200.0)), BOUNDS) is True           # 左缘
    assert touching_boundary(_sprite((float(RIGHT_X), 200.0)), BOUNDS) is True  # 右缘
    assert touching_boundary(_sprite((400.0, float(BOTTOM_Y))), BOUNDS) is True  # 地面
    # 顶边（天花板）刻意不算触界：旧机判定只有左右缘与地面
    assert touching_boundary(_sprite((400.0, 0.0)), BOUNDS) is False
    assert touching_boundary(_sprite((400.0, 200.0)), BOUNDS) is False


# ---------------------------------------------------------------- 回正三触发源
def test_settled_and_dragged_sprites_recover_angle():
    """落地停稳（physics 回 normal）与拖拽打断：无条件兜底回正。"""
    world = _world()
    settled = _sprite((400.0, 200.0))
    world.arm(settled)
    _thrown(settled, 400.0, 0.0)
    world.tick([settled], 1 / 60)
    assert settled.throw_rotation != 0.0

    settled.interaction_state = INTERACTION_NORMAL  # 抛掷物理的静止收尾
    settled.velocity = QPointF(0, 0)
    world.tick([settled], 1 / 60)
    assert world.is_active(settled) is False
    assert world.angle_of(settled) == 0.0
    assert settled.throw_rotation == 0.0

    dragged = _sprite((400.0, 200.0))
    world.arm(dragged)
    _thrown(dragged, 400.0, 0.0)
    world.tick([dragged], 1 / 60)
    assert dragged.throw_rotation != 0.0

    dragged.interaction_state = INTERACTION_DRAG  # 空中被抓住
    world.tick([dragged], 1 / 60)
    assert world.is_active(dragged) is False
    assert dragged.throw_rotation == 0.0


def test_low_speed_pet_contact_recovers_but_fast_contact_keeps_following():
    """飞行中再次撞其它桌宠：低速回正，高速继续跟随（旧 on_pet_contact）。"""
    world = _world()
    slow = _sprite((400.0, 200.0))
    world.arm(slow)
    _thrown(slow, 200.0, 0.0)
    world.tick([slow], 1 / 60)
    assert slow.throw_rotation == pytest.approx(90.0)

    world.on_probe_collision_throw(slow)  # 已在会话中：走撞宠分支
    assert world.is_active(slow) is False
    assert slow.throw_rotation == 0.0

    fast = _sprite((400.0, 200.0))
    world.arm(fast)
    _thrown(fast, 1500.0, 0.0)
    world.tick([fast], 1 / 60)
    world.on_probe_collision_throw(fast)
    assert world.is_active(fast) is True
    assert fast.throw_rotation == pytest.approx(90.0)

    world.on_pet_contact(fast, speed=100.0)  # 显式速度入口
    assert world.is_active(fast) is False
    assert fast.throw_rotation == 0.0


def test_real_throw_physics_flight_lands_and_recovers():
    """真实抛掷物理跑完整飞行：角度跟随过速度方向，落地停稳后回正。"""
    world = _world()
    physics = ThrowPhysicsController(BOUNDS)
    sprite = _sprite((400.0, 40.0))
    world.arm(sprite)
    sprite.set_velocity(QPointF(320.0, -220.0))
    sprite.interaction_state = INTERACTION_THROWN

    seen = []
    for _ in range(3000):  # 50 秒仿真预算，远大于实际落地时间
        physics.tick([sprite], 1 / 60)
        world.tick([sprite], 1 / 60)
        if world.is_active(sprite):
            seen.append(sprite.throw_rotation)
        if sprite.interaction_state == INTERACTION_NORMAL:
            break

    assert sprite.interaction_state == INTERACTION_NORMAL, "抛掷物理未在预算内落地"
    assert world.is_active(sprite) is False
    assert sprite.throw_rotation == 0.0
    assert seen, "飞行中从未记录到角度"
    assert any(abs(a) > 1.0 for a in seen), "飞行中未跟随过速度方向"


# ---------------------------------------------------------------- 多 sprite 独立
def test_multiple_sprites_are_independent():
    world = _world()
    left = _sprite((300.0, 200.0))
    right = _sprite((600.0, 200.0))
    world.arm(left)
    world.arm(right)
    _thrown(left, 400.0, 0.0)
    _thrown(right, -400.0, 0.0)

    world.tick([left, right], 1 / 60)
    assert left.throw_rotation == pytest.approx(90.0)
    assert right.throw_rotation == pytest.approx(270.0)

    # 只让 left 触界回正：right 的角度与角度记录都不受影响
    left.set_pos(QPointF(300.0, float(BOTTOM_Y)))
    world.tick([left, right], 1 / 60)
    assert world.is_active(left) is False
    assert left.throw_rotation == 0.0
    assert world.is_active(right) is True
    assert right.throw_rotation == pytest.approx(270.0)
    assert world.active is True


# ---------------------------------------------------------------- 会话收口
def test_end_forget_cancel_all_are_idempotent():
    world = _world()
    sprite = _sprite((400.0, 200.0))
    world.arm(sprite)
    _thrown(sprite, 500.0, 0.0)
    world.tick([sprite], 1 / 60)
    assert sprite.throw_rotation != 0.0

    world.forget(sprite)
    assert world.is_active(sprite) is False
    assert sprite.throw_rotation == 0.0
    world.end(sprite)        # 幂等
    world.forget(sprite)     # 幂等
    assert sprite.throw_rotation == 0.0

    a = _sprite((400.0, 200.0))
    b = _sprite((600.0, 200.0))
    world.arm(a)
    world.arm(b)
    world.cancel_all("character_switch")
    assert world.active is False
    assert a.throw_rotation == 0.0
    assert b.throw_rotation == 0.0


def test_factory_queries_bounds_update_and_empty_table_tick():
    world = create_throw_egg_world(BOUNDS)
    assert isinstance(world, SpriteThrowEggWorld)
    assert world.bounds == QRectF(BOUNDS)

    unknown = _sprite((400.0, 200.0))
    assert world.is_active(unknown) is False
    assert world.angle_of(unknown) == 0.0
    world.tick([unknown], 1 / 60)   # 空会话表：正常/抛掷态都不碰
    assert unknown.throw_rotation == 0.0

    world.set_bounds(QRect(0, 0, 640, 360))
    assert touching_boundary(unknown, world.bounds) is True  # 新右缘之外的 x


# ---------------------------------------------------------------- 渲染通道
def test_paint_zero_cost_without_throw_rotation():
    sprite = _sprite((400.0, 200.0))
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["drawPixmap"]  # 角度 0：不 save/translate/rotate
    assert sprite.throw_rotation == 0.0


def test_paint_mounts_throw_rotation_at_frame_center():
    sprite = _sprite((400.0, 200.0))
    sprite.set_throw_rotation(90.0)

    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["save", "translate", "rotate", "translate",
                               "drawPixmap", "restore"]
    center = sprite.rect().center()
    cx, cy = float(center.x()), float(center.y())
    assert painter.calls[1] == ("translate", cx, cy)
    assert painter.calls[2] == ("rotate", 90.0)
    assert painter.calls[3] == ("translate", -cx, -cy)

    sprite.clear_throw_rotation()
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["drawPixmap"]


def test_paint_composes_probe_and_throw_on_same_pipeline():
    """两条通道同管线同轴合成：抛掷外层 + 探头内层 = 角度相加；清一条不动另一条。"""
    sprite = _sprite((400.0, 200.0))
    sprite.set_probe_pose(45.0, 0.55)
    probe_only = sprite.paint_bounds()
    assert probe_only != sprite.rect()

    sprite.set_throw_rotation(90.0)
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.names() == ["save", "translate", "rotate", "translate",
                               "save", "translate", "rotate", "translate",
                               "drawPixmap", "restore", "restore"]
    assert painter.rotations() == [90.0, 45.0]  # 抛掷先入栈（外层）

    rect = sprite.rect()
    assert sprite.paint_bounds() == rotated_region_bounds(rect, rect, 135.0).united(rect)
    assert sprite.probe_angle == 45.0           # 探头姿态未被抛掷通道改动
    assert sprite.probe_exposure == pytest.approx(0.55)

    sprite.clear_throw_rotation()
    assert sprite.probe_angle == 45.0           # 清抛掷角不碰探头姿态
    assert sprite.probe_exposure == pytest.approx(0.55)
    assert sprite.paint_bounds() == probe_only  # 回到纯探头投影
    painter = RecordingPainter()
    sprite.paint(painter)
    assert painter.rotations() == [45.0]


def test_throw_rotation_reports_dirty_region_through_overlay():
    """脏矩形经既有 _notify_dirty 通道：覆盖旋转后的绘制外接矩形，同值 no-op。"""
    overlay = RecordingOverlay()
    sprite = _sprite((400.0, 200.0))
    overlay.add_sprite(sprite)

    overlay.updated = QRegion()
    sprite.set_throw_rotation(135.0)
    paint = sprite.paint_bounds()
    assert paint != sprite.rect()
    assert QRegion(paint).subtracted(overlay.updated).isEmpty()
    assert QRegion(sprite.rect()).subtracted(overlay.updated).isEmpty()

    overlay.updated = QRegion()
    sprite.set_throw_rotation(135.0)   # 同值：零成本 no-op
    assert overlay.updated.isEmpty()

    overlay.updated = QRegion()
    sprite.clear_throw_rotation()
    assert QRegion(sprite.rect()).subtracted(overlay.updated).isEmpty()


def test_alpha_hit_testing_follows_throw_and_composed_rotation():
    """命中逆变换跟随抛掷角；与探头角合成时逆变换顺序正确（画哪=点哪）。"""
    clip = FakeClip()
    painter = QPainter(clip.image)
    painter.fillRect(QRect(0, 0, 40, 40), Qt.GlobalColor.red)  # 左上角不透明块
    painter.end()
    lib = FakeLibrary()
    lib._clip = clip
    sprite = PetSprite(lib, pos=QPointF(0, 0), scale=1.0)
    sprite.set_bounds(QRect(BOUNDS))
    sprite.bind_clip("idle1")
    sprite.paint(RecordingPainter())  # 建首帧（含命中图）

    assert sprite.alpha_at(QPointF(20, 20)) == 255

    sprite.set_throw_rotation(180.0)
    assert sprite.alpha_at(_forward_rotate(QPointF(20, 20), sprite.rect(), 180.0)) == 255
    assert sprite.alpha_at(QPointF(20, 20)) == 0

    # 探头 45° + 抛掷 135° = 合成 180°（同一旋转中心 → 角度相加）
    sprite.clear_throw_rotation()
    sprite.set_probe_pose(45.0, 0.55)
    sprite.set_throw_rotation(135.0)
    assert sprite.alpha_at(_forward_rotate(QPointF(20, 20), sprite.rect(), 180.0)) == 255
    assert sprite.alpha_at(QPointF(20, 20)) == 0
