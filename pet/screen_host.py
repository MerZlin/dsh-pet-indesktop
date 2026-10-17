# -*- coding: utf-8 -*-
"""合成窗承载：**一张** overlay 覆盖整个虚拟桌面 + 一份仿真控制器组。

契约（2026-10-17 用户口径）：全进程只有一张合成窗，窗口矩形 = 全部屏可用工作区
的并集包围矩形；精灵在同一张窗里自由跨越显示器，没有任何"换宿主"。

sprite 坐标是 **窗口局部坐标**（= 全局 - 窗口原点，见
``OverlayShell._desktop_bounds``）。活动区（精灵可站立域）是**矩形集合**：各屏
可用工作区平移到窗口局部坐标后的那些矩形——错位拼接的屏间空洞不是可站立区，
所以：

- ``bounds`` 是并集包围矩形，喂行为/抛掷物理/边缘探头这些"单矩形"控制器；
- ``activity`` 是矩形集合，喂 ``PetSprite.set_clamp_rects``（逐点落在并集内才
  合法，空洞里没有合法位置）；两者必须一起换，只换前者会让第一次几何变化把多
  矩形语义吃掉（``set_bounds`` 会就地按单矩形钳一次）；
- 关闭跨屏（``allow_cross_screen``）时集合只留家那块屏——窗仍覆盖全桌面，宠出
  不去。

窗口能力、气泡、菜单、托盘、音效、联动、监视器、素材库等进程级面仍归
``OverlayShell``。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF

from .sprite_behavior import BehaviorController
from .sprite_collision import SpriteCollisionWorld
from .sprite_physics import ThrowPhysicsController


class ScreenHost:
    """整桌面合成窗的宿主：overlay、单矩形边界、活动区矩形集合与控制器组。"""

    def __init__(self, screen, overlay, bounds: QRect) -> None:
        # ``screen`` = 家那块屏（关闭跨屏时唯一活动区、屏拔出时的兜底判据）。
        self.screen = screen
        self.overlay = overlay
        self.bounds = QRect(bounds)
        # ((screen, QRect) ...)：活动区矩形集合 + 它属于哪块屏，几何同步按屏对齐
        self.activity: tuple = ()
        # 仿真：行为（漫游/落位边界）→ 碰撞（全桌成员）→ 抛掷物理（窗口边界）
        self.behavior = BehaviorController(self.bounds)
        self.collision = SpriteCollisionWorld()
        self.physics = ThrowPhysicsController(self.bounds)
        # 附加仿真控制器（tick 尾段，顺序即列表序：边缘探头先、抛掷彩蛋后）
        self.extras: list = []
        self.probe = None
        self.throw_egg = None
        self.feeding = None
        # overlay 反查宿主：驱动器的分组仿真与鼠标路由都从这里取
        overlay.host = self
        overlay.behavior = self.behavior  # contextMenuEvent 查表点

    @property
    def sprites(self) -> list:
        """当前承载的 sprite（overlay 有序表即 z-order）。"""
        return list(self.overlay.sprites)

    def tick(self, dt: float) -> None:
        """一轮仿真：行为 → 碰撞 → 物理 → 附加控制器（顺序协议同 M-2）。"""
        sprites = list(self.overlay.sprites)
        self.behavior.tick(sprites, dt)
        self.collision.tick(sprites, dt)
        self.physics.tick(sprites, dt)
        for ctrl in list(self.extras):
            ctrl.tick(sprites, dt)

    def set_bounds(self, bounds: QRect) -> None:
        """换窗口边界（并集包围矩形变化）：控制器与全部成员一起换域。

        单矩形边界只服务于控制器；成员的可站立域由 ``set_activity`` 的矩形集合
        决定，故这里换完必须把集合重挂回每只成员——``sprite.set_bounds`` 会就地
        按单矩形钳一次，不重挂的话第一次几何变化就把多矩形语义吃掉了。
        """
        self.bounds = QRect(bounds)
        self.behavior.bounds = QRect(bounds)
        self.physics.set_bounds(bounds)
        for sprite in self.overlay.sprites:
            sprite.set_bounds(QRect(bounds))
            self.apply_activity(sprite)
        for ctrl in (self.probe, self.throw_egg):
            if ctrl is not None:
                ctrl.set_bounds(QRect(bounds))

    def set_activity(self, rects) -> None:
        """换活动区矩形集合（``((screen, QRect) ...)``，窗口局部坐标）。"""
        self.activity = tuple((screen, QRect(rect)) for screen, rect in rects)
        for sprite in self.overlay.sprites:
            self.apply_activity(sprite)

    def apply_activity(self, sprite) -> None:
        """把活动区集合挂到一只成员身上（``None`` = 没有活动区时退回单矩形）。"""
        if not self.activity:
            sprite.set_clamp_rects(None)
            return
        sprite.set_clamp_rects(tuple(QRectF(rect) for _screen, rect in self.activity))

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        try:
            name = str(self.screen.name())
        except Exception:
            name = "?"
        return (f"<ScreenHost {name} bounds={self.bounds.x()},{self.bounds.y()}"
                f",{self.bounds.width()}x{self.bounds.height()}"
                f" activity={len(self.activity)}"
                f" sprites={len(self.overlay.sprites)}>")
