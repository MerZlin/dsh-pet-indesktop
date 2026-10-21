# -*- coding: utf-8 -*-
"""GLM Q6 / M-2：sprite collision_id 稳定身份回归（offscreen 可跑）。

背景（REVIEW_VERDICT.md §4 GLM C4 + M-2 条款）：碰撞世界的成员身份此前是
``f"sprite-{id(sprite)}"``——对象销毁后 CPython 复用地址，新 sprite 拿到老
runtime_id，世界侧 ``_prev_circles`` / 去抖表把新成员当成老成员，产生"幽灵
扫掠"（对早已不存在的圆链做扫掠碰撞）。Q6 要求 sprite 暴露稳定的
``collision_id`` 字段，随 M-2 落地。

契约：
1. ``PetSprite.collision_id`` 非空、对象生命周期内稳定、进程内**永不复用**；
2. 地址复用（``id()`` 相同）时两个 sprite 的 collision_id 必须不同；
3. ``SpriteCollisionWorld._member_id`` 优先取它；无该字段的鸭式 sprite 仍回退
   ``sprite-<id>``（既有兼容行为不破）。

纪律（AGENTS.md 时序测试）：纯对象生命周期断言，无 Qt 事件循环、无 sleep；
素材用纯 QImage 假 clip。
"""
from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet.pet_sprite import PetSprite
from pet.sprite_collision import SpriteCollisionWorld

app = QApplication.instance() or QApplication([])


class FakeClip(QObject):
    frameChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return 1

    def start(self):
        return True

    def stop(self):
        pass


class FakeLibrary:
    def __init__(self):
        self._clip = FakeClip()
        self.no_mirror: set[str] = set()

    def movie(self, name, **_kw):
        return self._clip


def _make_sprite(pos=QPointF(100, 100), scale=0.5) -> PetSprite:
    return PetSprite(FakeLibrary(), pos=pos, scale=scale)


# ---------------------------------------------------------------- collision_id 契约
def test_collision_id_is_stable_within_object_lifetime():
    sprite = _make_sprite()
    first = sprite.collision_id
    assert first and isinstance(first, str)
    assert sprite.collision_id == first          # 生命周期内稳定
    sprite.set_pos(QPointF(300, 200))
    sprite.set_velocity(QPointF(50, -20))
    assert sprite.collision_id == first          # 位置/速度变化不影响身份


def test_collision_id_unique_across_live_sprites():
    sprites = [_make_sprite() for _ in range(5)]
    ids = [s.collision_id for s in sprites]
    assert len(set(ids)) == len(ids)             # 同进程并存成员两两不同
    assert all(cid.startswith("pet-sprite-") for cid in ids)


def test_collision_id_never_reused_across_object_lifetimes():
    """幽灵扫掠根因回归：身份源单调发号，对象生灭不复用身份。

    两段断言：① 单调递增 + 互异（确定性硬断言，等价于"进程内永不复用"）；
    ② 若本次运行恰好发生了地址复用（CPython 分配器行为，不作为前提），
    复用同一地址的两个 sprite 必须拿到不同身份——旧 ``id()`` 口径下这里
    正是幽灵成员的产生点。
    """
    pairs: list[tuple[int, str]] = []
    for _ in range(8):
        sprite = _make_sprite()
        pairs.append((id(sprite), sprite.collision_id))
        del sprite
        gc.collect()

    serials = [int(cid.rsplit("-", 1)[1]) for _addr, cid in pairs]
    assert serials == sorted(set(serials)), "身份源必须单调递增且永不复用"
    assert len({cid for _addr, cid in pairs}) == len(pairs)

    for i, (addr, cid) in enumerate(pairs):
        for j in range(i):
            if pairs[j][0] == addr:            # 地址复用：必须换新身份
                assert cid != pairs[j][1]


# ---------------------------------------------------------------- 世界侧取用
def test_world_member_id_prefers_collision_id():
    sprite = _make_sprite()
    assert SpriteCollisionWorld._member_id(sprite) == sprite.collision_id
    # 运动签名（静止豁免判据）同样以稳定身份为键：成员换人即换键，
    # 不会把新 sprite 认成老成员
    signature = SpriteCollisionWorld._motion_signature([sprite])
    assert signature[0][0] == sprite.collision_id


def test_world_member_id_falls_back_for_duck_sprites():
    """无 collision_id 的鸭式 sprite（测试假对象/旧调用方）保持既有回退。"""

    class Duck:
        pass

    duck = Duck()
    assert SpriteCollisionWorld._member_id(duck) == f"sprite-{id(duck)}"
