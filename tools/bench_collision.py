# -*- coding: utf-8 -*-
"""撞岛碰撞 tick 微基准（纯数学层，无需 Qt）。

用法::

    python tools/bench_collision.py [ticks]

输出每个场景的 ``us/tick``，并按 60Hz 折算成"占一个核的百分比"。
优化前后对照请用同一台机器、同一负载连续跑 3 轮取中位数（本机实测见
``docs`` 外的工作报告：far 40µs → 3.9µs，touch/thrown 约 -30%）。

三种场景：
  far      —— 桌宠离岛很远、每 tick 都在走动（签名变化，不允许静止豁免）
  touch    —— 桌宠身体停在岛区附近（本帧未重叠 → 扫掠仍要跑）
  thrown   —— 抛掷态贴岛（FLAG_THROWN 分支 + 反弹恢复系数）
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pet.sprite_collision import SpriteCollisionWorld, capsule_circles

ISLAND = ("island", 0.0, 555.0, 192.0, 44.0)
DT = 1.0 / 60.0


class Rect:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = float(x), float(y), float(w), float(h)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._w

    def height(self):
        return self._h


class Vec:
    def __init__(self, x=0.0, y=0.0):
        self._x, self._y = float(x), float(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def setX(self, v):
        self._x = float(v)

    def setY(self, v):
        self._y = float(v)


class FakeSprite:
    visible = True
    collision_enabled = True
    interaction_state = "normal"
    scale = 0.5

    def __init__(self, x, y, vx=0.0, vy=0.0):
        # pos 是 canvas 左上角，rect() 是整 canvas，body_rect() 是身体框
        self.pos = Vec(x, y)
        self.velocity = Vec(vx, vy)
        self._canvas = Rect(x, y, 320, 180)
        self._body = Rect(x + 70, y + 20, 108, 135)
        self.interaction_state = "normal"

    def rect(self):
        return self._canvas

    def body_rect(self):
        return self._body

    def set_pos(self, p):
        dx = float(p.x()) - self.pos.x()
        dy = float(p.y()) - self.pos.y()
        self.pos = Vec(p.x(), p.y())
        self._canvas = Rect(self._canvas.x() + dx, self._canvas.y() + dy,
                            self._canvas.width(), self._canvas.height())
        self._body = Rect(self._body.x() + dx, self._body.y() + dy,
                          self._body.width(), self._body.height())

    def set_velocity(self, v):
        self.velocity = Vec(v.x(), v.y())

    def _sync_rects(self):
        x, y = self.pos.x(), self.pos.y()
        self._canvas = Rect(x, y, 320, 180)
        self._body = Rect(x + 70, y + 20, 108, 135)


def make_world():
    w = SpriteCollisionWorld()
    w.add_static_member(
        ISLAND[0], ISLAND[1], ISLAND[2], ISLAND[3], ISLAND[4],
        circles=capsule_circles(ISLAND[1], ISLAND[2], ISLAND[3], ISLAND[4]),
    )
    return w


def bench(scenario: str, ticks: int) -> float:
    world = make_world()
    if scenario == "far":
        sprite = FakeSprite(1300.0, 400.0, -20.0, 0.0)
    elif scenario == "touch":
        sprite = FakeSprite(120.0, 560.0, 240.0, -60.0)
    elif scenario == "thrown":
        sprite = FakeSprite(160.0, 560.0, 300.0, 0.0)
        sprite.interaction_state = "thrown"
    else:
        raise SystemExit(scenario)
    sprites = [sprite]

    # 预热（首批 tick 会建缓存 / 扫掠 prev）
    for _ in range(200):
        world.tick(sprites, DT)

    t0 = time.perf_counter()
    for i in range(ticks):
        # 行为控制器：每 tick 写一点位置/速度（"正常走动"），保证签名变化
        if scenario == "far":
            sprite.pos = Vec(1300.0 - (i % 200) * 0.5, 400.0)
            sprite._sync_rects()
        else:
            # 贴岛场景：位置由求解器冲量弹出后继续朝岛压回去（模拟持续顶撞）
            vx = max(60.0, min(320.0, 240.0 + 120.0 * math.sin(i * 0.1)))
            if sprite.velocity.x() < 0:
                vx = -vx
            sprite.velocity = Vec(vx, sprite.velocity.y())
            sprite.pos = Vec(120.0 + (i % 7), 560.0)
            sprite._sync_rects()
        world.tick(sprites, DT)
    dt = time.perf_counter() - t0
    return dt / ticks * 1e6


def main():
    ticks = 20000
    print(f"ticks={ticks}  (sprite 320x180 @scale .5, island {ISLAND[3]:.0f}x{ISLAND[4]:.0f})")
    for scenario in ("far", "touch", "thrown"):
        us = bench(scenario, ticks)
        print(f"  {scenario:7s}  {us:8.2f} us/tick   "
              f"{us * 60 / 1e3:6.2f} ms/s @60Hz  "
              f"({us * 60 / 1e4:5.2f}% of one core)")


if __name__ == "__main__":
    main()