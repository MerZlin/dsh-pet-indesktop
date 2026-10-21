# -*- coding: utf-8 -*-
"""抛掷物理控制器（单合成窗架构 Phase 1b）："thrown" sprite 的子步积分。

直接复用 pet/physics.py 的纯函数（throw_step / is_at_rest /
soft_clamp_speed，见 .scratch/single-overlay-window/spec.md「直接复用」
表），语义对齐旧窗口路径 window.py 的 _tick_throw_physics（:4414-4461）：
≤8ms 子步积分 + 四边反弹 + 地面摩擦，落地静止（is_at_rest）后停住并把
interaction_state 切回 "normal"（行为控制器随后接管）。

位置积分归属：本控制器在 overlay 的 before_sprites_advance 阶段运行，
直接写 sprite 的 pos/velocity；PetSprite.advance 对 "thrown" 状态不再
积分（否则同一 tick 双重积分）。拖拽轨迹采样与松手抛掷判定在
PetSprite 侧（on_press/on_move/on_release），本模块只管飞行段。
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from PySide6.QtCore import QPointF, QRect, QRectF

from . import physics as physics_mod
from .pet_sprite import INTERACTION_NORMAL, INTERACTION_THROWN


def _united(rects) -> QRect:
    """多个 QRectF 的包围矩形（QRect）。"""
    united = None
    for r in rects:
        qr = QRect(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
        united = qr if united is None else united.united(qr)
    return united if united is not None else QRect()


class ThrowPhysicsController:
    """"thrown" sprite 的抛掷物理：重力 + 边界反弹 + 地面摩擦 + 静止收尾。

    bounds 为 overlay 局部坐标下的可活动区域（QRect/QRectF，由调用方
    传入；本模块不直接查 QScreen，保持 offscreen 可测）。sprite 身体框
    贴到 bounds 四边即反弹——语义同旧架构 _throw_bounds（身体框贴边，
    画布透明边允许越界），每个 sprite 的有效边界按其身体框偏移与
    尺寸内缩（V-2）。
    """

    def __init__(
        self,
        bounds: QRect | QRectF,
        *,
        max_sub_dt: float = 0.008,
        gravity: float = physics_mod.GRAVITY,
        bounce_stop_vy: float = physics_mod.GROUND_BOUNCE_STOP_VY,
    ) -> None:
        self._bounds = QRectF(bounds)
        self._max_sub_dt = float(max_sub_dt)
        self._gravity = float(gravity)
        self._bounce_stop_vy = float(bounce_stop_vy)

    @property
    def bounds(self) -> QRectF:
        return QRectF(self._bounds)

    def set_bounds(self, bounds: QRect | QRectF) -> None:
        """更新可活动区域（屏幕/工作区变化时由集成层调用）。"""
        self._bounds = QRectF(bounds)

    def tick(self, sprites: Iterable, dt: float) -> None:
        """推进一个 tick：只处理 interaction_state == "thrown" 的 sprite。

        "normal"（行为控制器写 velocity，advance 积分）与 "drag"
        （光标驱动位置）的 sprite 原样跳过。
        """
        if dt <= 0.0:
            return
        for sprite in list(sprites):
            if getattr(sprite, "interaction_state", INTERACTION_NORMAL) != INTERACTION_THROWN:
                continue
            self._advance_thrown(sprite, float(dt))

    def _effective_bounds(self, sprite) -> QRectF:
        """这次积分该用的边界：多矩形跨屏域按**并集外接矩形**（过缝通行语义）。

        跨屏时 sprite 的合法域是多块屏的并集（``_clamp_rects``）。旧实现取
        「离身体最近的那一块屏」做反弹边界：墙挡着身体、身体中心过不去、
        墙就永远不收——并列双屏下抛掷被永久锁在本屏（issue #319）。
        band 收窄语义（4.2.1 legacy）修好了 #319 但在**异高屏**上制造新墙：
        主屏正对副屏下方空洞的那条带永远过不去（用户实机，2026-10-22）。
        定稿语义改为「通行域 = 并集外接矩形（含空洞），静止域 = 各屏本身」：
        穿缝任意过，落地时身体不被并集覆盖则由落点吸附收进最近的屏
        （``_snap_into_screen_if_needed`` 鸭子类型调用 pet_sprite 同名方法）。
        单矩形域（单屏/未开跨屏）并集恒等于该矩形，行为逐位不变。
        """
        clamp_rects = getattr(sprite, "_clamp_rects", None)
        if clamp_rects:
            return QRectF(_united(clamp_rects))
        return QRectF(self._bounds)

    def _snap_into_screen_if_needed(self, sprite) -> None:
        """落点吸附：静止时身体不被各屏并集覆盖 → 收进最近的屏（SPEC §语义）。

        实现唯一来源是 pet_sprite 的同名方法（鸭子类型调用，防两份拷贝漂移
        ——k3 盲审 A1）；fake sprite 没有该方法时静默 no-op。
        """
        snap = getattr(sprite, "_snap_into_screen_if_needed", None)
        if callable(snap):
            snap()

    def _sprite_bounds(self, sprite) -> tuple[float, float, float, float]:
        """sprite 左上角的可活动范围：身体框贴 bounds 四边（V-2）。

        语义对齐旧架构 window_placement.throw_bounds（身体框贴工作区，
        画布透明边允许越界）——此前按整 canvas 矩形内缩，反弹发生在
        透明画布边上，与注释声称的口径不符。
        """
        bounds = self._effective_bounds(sprite)
        rect = sprite.rect()
        body = sprite.body_rect() if hasattr(sprite, "body_rect") else rect
        off_x, off_y = body.x() - rect.x(), body.y() - rect.y()
        left = bounds.left() - off_x
        top = bounds.top() - off_y
        # sprite 比区域还大时 right/bottom 会小于 left/top：钳回，防死循环
        right = max(left, bounds.right() - off_x - body.width())
        bottom = max(top, bounds.bottom() - off_y - body.height())
        return left, top, right, bottom

    def _advance_thrown(self, sprite, dt: float) -> None:
        left, top, right, bottom = self._sprite_bounds(sprite)
        px, py = sprite.pos.x(), sprite.pos.y()
        vx, vy = sprite.velocity.x(), sprite.velocity.y()
        remaining = dt
        bounced_any = False
        speed = math.hypot(vx, vy)

        # 子步积分（≤8ms/步，对齐 window.py:4419-4435）：单步积分整个
        # dt 会把反弹延迟到 tick 末尾才结算（穿透后的位置被钳回、剩余
        # 飞行时间丢失）；子步保证反弹发生在正确时刻，剩余时间按反弹后
        # 速度继续积分。
        while remaining > 1e-6:
            step_dt = min(self._max_sub_dt, remaining)
            px, py, vx, vy, bounced = physics_mod.throw_step(
                px, py, vx, vy, step_dt, left, top, right, bottom,
                gravity=self._gravity, bounce_stop_vy=self._bounce_stop_vy)
            bounced_any = bounced_any or bounced
            remaining -= step_dt
            speed = math.hypot(vx, vy)
            if physics_mod.is_at_rest(py, vx, vy, bottom, bounced_any, speed):
                break

        sprite.set_pos(QPointF(px, py))
        speed = math.hypot(vx, vy)
        if physics_mod.is_at_rest(py, vx, vy, bottom, bounced_any, speed):
            # 落地静止：velocity 归零停住（advance 只对 "normal" 积分，
            # 归零后原地不动），交还行为控制器。交还前先落点吸附——过缝
            # 通行语义下飞行可穿空洞，但静止不许落在里面（SPEC §语义）
            self._snap_into_screen_if_needed(sprite)
            sprite.set_velocity(QPointF(0, 0))
            # F4：速率必须先复位——duration() 除以 playback_speed
            # （webm_clip.py:1387-1390），留着飞行倍率会让落地后的第一次
            # _plan_move 按加速后的时长量化位移（步态与墙钟失配）
            self._reset_flight_speed(sprite)
            sprite.interaction_state = INTERACTION_NORMAL
        else:
            sprite.set_velocity(QPointF(vx, vy))
            self._apply_flight_speed(sprite, speed)

    @staticmethod
    def _apply_flight_speed(sprite, speed: float) -> None:
        """飞行期动画随速度加速（F4，window.py:4328-4340）。

        倍率叠加在用户播放速率之上（叠加在 PetSprite 侧完成，倍率口径唯一
        来源 = 纯函数 physics.flight_anim_speed）。sprite 缺该接口时静默
        跳过：位置积分不因动画能力缺失而中断。
        """
        setter = getattr(sprite, "set_flight_anim_speed", None)
        if callable(setter):
            setter(physics_mod.flight_anim_speed(speed))

    @staticmethod
    def _reset_flight_speed(sprite) -> None:
        """落地复位用户播放速率（F4）：与 _apply_flight_speed 成对，唯一出口。"""
        reset = getattr(sprite, "reset_playback_speed", None)
        if callable(reset):
            reset()
