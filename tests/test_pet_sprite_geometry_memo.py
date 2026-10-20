# -*- coding: utf-8 -*-
"""PetSprite 几何记忆化回归（offscreen）：rect()/_logical_size() 的缓存与作废。

背景（实机 py-spy，120s 窗口、3 sprite 持续运动）：``rect()`` 占 2.1%、
``_logical_size()`` 占 1.3% GUI 线程时间。``rect()`` 每 tick 每 sprite 被
碰撞/绘制/区域判断/气泡锚点各调一次，每次都重算 ``_logical_size()``（两次
``int(round)`` + 两次 catalog 属性查找）并新建 QRect；而 ``_logical_size``
只依赖 scale、rect 只依赖 ``(int(pos.x), int(pos.y), 逻辑 w, h)``。

本文件锁三件事：
1. **缓存真的命中**：pos/scale 未变时 ``rect()`` 返回同一对象、
   ``_logical_size()`` 不再读 ``catalog.CANVAS_*``（改前红、改后绿）；
2. **作废不漏**：pos（含亚像素）或 scale 一变，值按未缓存的算式逐位更新；
3. **共享对象不引入别名事故**：调用点拿到的 QRect 是「取值当时的快照」，
   移动后旧对象的值不变——脏矩形上报的 old/new 仍是两个不同的矩形
   （调用点原地改 rect() 的地方已 grep 确认不存在：产品代码只有
   ``translated()``（const，返回新对象），tests 里零处改动动词）。

纪律：offscreen 真 QApplication；纯假库（rect/几何不依赖素材），不碰
webm/ffmpeg；不 sleep 赌时序（AGENTS.md）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRect
from PySide6.QtWidgets import QApplication

import pet.pet_sprite as pet_sprite_mod
from pet import catalog
from pet.pet_sprite import PetSprite

app = QApplication.instance() or QApplication([])

_BS = 0.5


class _Lib:
    """rect/几何路径只依赖 scale/pos——库侧给最小协议面即可。"""

    no_mirror: tuple = ()
    character_id = None

    def movie(self, name, **_kw):
        return None


def _sprite(pos=(100, 100), scale=_BS) -> PetSprite:
    return PetSprite(_Lib(), pos=QPointF(float(pos[0]), float(pos[1])),
                     scale=scale)


def _expected_size(scale: float) -> tuple[int, int]:
    """未缓存时的原算式（值断言的金标准，直读模块常量不抄数字）。"""
    return (max(1, int(round(catalog.CANVAS_W * scale))),
            max(1, int(round(catalog.CANVAS_H * scale))))


def _expected_rect(pos, scale: float) -> QRect:
    w, h = _expected_size(scale)
    return QRect(int(pos[0]), int(pos[1]), w, h)


class _CountingCatalog:
    """``pet_sprite.catalog`` 的代理：只统计 CANVAS_W/CANVAS_H 读取次数。"""

    def __init__(self, real) -> None:
        self._real = real
        self.reads: list[str] = []

    def __getattr__(self, name):
        if name in ("CANVAS_W", "CANVAS_H"):
            self.reads.append(name)
        return getattr(self._real, name)


# ---------------------------------------------------------------- 缓存命中
def test_logical_size_reads_canvas_constants_once(monkeypatch):
    """逻辑尺寸只依赖 scale：重复调用不得再重算（catalog 读取零增长）。"""
    counter = _CountingCatalog(catalog)
    monkeypatch.setattr(pet_sprite_mod, "catalog", counter)
    sprite = _sprite()
    counter.reads.clear()          # 构造期（钳制域）自会读一次，不计入本断言

    first = sprite._logical_size()
    reads_after_first = list(counter.reads)
    assert len(reads_after_first) <= 2      # 首次至多 W/H 两次
    for _ in range(20):
        assert sprite._logical_size() == first
    assert counter.reads == reads_after_first, "重复调用仍在重算 / 重读常量"
    assert first == _expected_size(_BS)


def test_rect_reuses_same_object_when_inputs_unchanged():
    """pos/scale 未变时 rect() 直接返回缓存对象（不再每调新建 QRect）。"""
    sprite = _sprite()
    first = sprite.rect()
    assert sprite.rect() is first
    assert sprite.rect() is first


def test_rect_cache_hit_does_not_recompute_logical_size(monkeypatch):
    """rect() 连续调用：整条链路（含 _logical_size）读取零增长。"""
    counter = _CountingCatalog(catalog)
    monkeypatch.setattr(pet_sprite_mod, "catalog", counter)
    sprite = _sprite()
    counter.reads.clear()

    for _ in range(10):
        sprite.rect()
    assert len(counter.reads) <= 2, f"缓存未命中：{len(counter.reads)} 次常量读取"


# ---------------------------------------------------------------- 作废正确性
@pytest.mark.parametrize("pos", [(0, 0), (13, 7), (10.9, 20.1), (-40.5, 30.75)])
@pytest.mark.parametrize("scale", [0.25, 0.5, 1.0, 1.75])
def test_rect_matches_recomputation_after_fresh_creation(pos, scale):
    sprite = _sprite(pos=pos, scale=scale)
    assert sprite.rect() == _expected_rect(pos, scale)


def test_rect_updates_after_pos_change():
    sprite = _sprite(pos=(100, 100))
    before = sprite.rect()
    sprite.set_pos(QPointF(400.5, 260.25))
    after = sprite.rect()

    assert after == _expected_rect((400.5, 260.25), _BS)
    assert after != before
    # 亚像素内移动：整数化结果不变 → 仍复用同一对象且值正确
    stable = sprite.rect()
    sprite.set_pos(QPointF(400.9, 260.9))
    assert sprite.rect() is stable
    assert sprite.rect() == _expected_rect((400.5, 260.25), _BS)
    # 跨整数边界：必须换新矩形
    sprite.set_pos(QPointF(401.0, 261.0))
    assert sprite.rect() != stable
    assert sprite.rect() == _expected_rect((401.0, 261.0), _BS)


@pytest.mark.parametrize("scale", [0.25, 0.75, 1.0, 1.5])
def test_logical_size_and_rect_invalidated_by_scale_change(scale):
    sprite = _sprite(pos=(200, 150), scale=1.0)
    assert sprite.rect() == _expected_rect((200, 150), 1.0)

    sprite.scale = scale
    assert sprite._logical_size() == _expected_size(scale)
    assert sprite.rect() == _expected_rect((200, 150), scale)


def test_scale_change_invalidates_logical_size_memo(monkeypatch):
    """scale 一变，逻辑尺寸缓存必须失效（不得把旧尺寸当命中）。"""
    counter = _CountingCatalog(catalog)
    monkeypatch.setattr(pet_sprite_mod, "catalog", counter)
    sprite = _sprite(scale=1.0)
    counter.reads.clear()
    before = sprite._logical_size()
    assert before == _expected_size(1.0)

    sprite.scale = 0.5
    assert sprite._logical_size() == _expected_size(0.5) != before
    assert counter.reads, "scale 变化后仍吃旧缓存（常量一次都没重读）"
    assert sprite.rect() == _expected_rect((100, 100), 0.5)


# ---------------------------------------------------------------- 共享对象不产生别名事故
def test_returned_rect_is_a_stable_snapshot():
    """移动后，先前拿到的 QRect 对象必须保持旧值（缓存不得原地改写）。"""
    sprite = _sprite(pos=(100, 100))
    before = sprite.rect()
    snapshot = QRect(before)
    sprite.set_pos(QPointF(500, 400))
    assert before == snapshot, "旧的 rect() 返回值被原地改写"
    assert sprite.rect() != before


def test_move_dirty_report_keeps_old_and_new_distinct():
    """脏矩形上报：old/new 值不同、对象不同（缓存命中不得让两者同一化）。"""
    sprite = _sprite(pos=(100, 100))
    sprite._frame_dirty = False
    reports: list[tuple[QRect, QRect]] = []
    sprite._dirty_cb = lambda old, new: reports.append((QRect(old), QRect(new)))

    sprite.set_pos(QPointF(140, 130))
    assert len(reports) == 1
    old, new = reports[0]
    assert old == _expected_rect((100, 100), _BS)
    assert new == _expected_rect((140, 130), _BS)
    assert old != new


def test_derived_geometry_matches_recomputation_after_moves_and_scales():
    """center/body_rect/radius/paint_bounds 经 rect() 复用缓存后取值不变。"""
    sprite = _sprite(pos=(100, 100), scale=1.0)
    for pos, scale in [((100, 100), 1.0), ((321.4, 77.6), 0.5),
                       ((0, 0), 1.25), ((-12.5, 40.25), 0.75)]:
        sprite.scale = scale
        sprite.set_pos(QPointF(float(pos[0]), float(pos[1])))
        r = _expected_rect(pos, scale)
        assert sprite.rect() == r
        assert sprite.center() == QPointF(r.x() + r.width() / 2,
                                          r.y() + r.height() / 2)
        assert sprite.body_rect() == r          # 无 body_box 声明 → 回退整矩形
        assert sprite.radius() == 0.45 * min(r.width(), r.height())
        assert sprite.paint_bounds() == r       # 无探头角 → 原样返回
