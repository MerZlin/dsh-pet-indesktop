# -*- coding: utf-8 -*-
"""彩蛋：边缘探头状态的桌宠被击飞时，飞行中整帧旋转使头部跟随速度方向。

> 批 D / 通用约束：非探头状态被击飞维持批 A 现状；不加配置开关，彩蛋常开。
> 控制器只维护激活状态与当前角度；整帧旋转由 PetWindow 经 pet/window_effects.py
> 应用（与 edge_probe / golden_spin 同一绘制管线，begin_rotation/end_rotation）。
> 落地停稳（_stop_physics 兜底）、低速触碰屏幕边界或其它桌宠后恢复正常
> （角度归零）。用户明确要求不设飞行时间硬上限。
"""
from __future__ import annotations

import math
from typing import Any

# 速度低于该阈值（并触碰边界/桌宠）时结束彩蛋、恢复正常姿态。
# 量级参照 physics.is_at_rest 的静止判定（REST_VY / REST_VX 为几十 px/s 量级）。
# 120/240/400 都太低：低速贴地时会快速连续碰撞，速度方向反复翻转导致鱼头
# 快速来回变向（用户实机反馈"很奇怪"），故拉到 780——低速弹跳段直接不再
# 更新角度且一碰就回正（用户实机要求，明确不要时间硬上限）。
THROW_EGG_RECOVER_SPEED = 780.0  # px/s


class ThrowEggController:
    """管理“被击飞时头部跟随速度方向”的旋转会话。

    每个 PetWindow 持有同一实例；仅在边缘探头激活被撞时由 arm() 开启。
    飞行中每 tick 以 update() 跟随速度方向更新角度；落地停稳（_stop_physics
    兜底）、低速触碰边界或桌宠时 end() 结束并恢复正常姿态。不依赖素材。
    """

    def __init__(self, win: Any) -> None:
        self.win = win
        self._active = False
        self._angle_deg = 0.0

    # ------------------------------------------------------------ 查询
    @property
    def active(self) -> bool:
        return self._active

    def current_angle_deg(self) -> float:
        return self._angle_deg if self._active else 0.0

    # ------------------------------------------------------------ 开关
    def arm(self) -> None:
        """仅探头激活被撞时由 edge_probe.cancel 调用 → 开始头部跟随速度。"""
        self._active = True
        self._angle_deg = 0.0
        self._notify()

    def update(self, vx: float, vy: float, touching_boundary: bool) -> None:
        """每 tick 由 _tick_throw_physics 调用；低速贴界则恢复正常姿态。

        速度高于阈值时才更新角度（低于阈值保持当前角，防抖）；
        速度低于阈值且触碰边界 → end()。不设飞行时间上限（用户明确要求）。
        """
        if not self._active:
            return
        speed = math.hypot(vx, vy)
        if speed >= THROW_EGG_RECOVER_SPEED:
            # 屏幕坐标 y 朝下：向右飞=90°、向下=180°、向上=0°、向左=270°。
            self._angle_deg = 90.0 + math.degrees(math.atan2(vy, vx))
        if speed < THROW_EGG_RECOVER_SPEED and touching_boundary:
            self.end()
            return
        self._notify()

    def on_pet_contact(self, speed: float) -> None:
        """飞行中再次撞到其它桌宠：低速则立刻恢复正常姿态。"""
        if not self._active:
            return
        if speed < THROW_EGG_RECOVER_SPEED:
            self.end()

    def end(self) -> None:
        """恢复正常姿态；幂等。落地停稳（_stop_physics）无条件调用兜底。"""
        if not self._active:
            return
        self._active = False
        self._angle_deg = 0.0
        self._notify()

    # ------------------------------------------------------------ 内部
    def _notify(self) -> None:
        try:
            self.win.update()
        except Exception:
            pass
