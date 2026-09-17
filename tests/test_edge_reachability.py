# -*- coding: utf-8 -*-
"""Linux 贴边（issue #103 / linux-edge-clamp）：绘制补偿（视口模型）回归。

背景：GNOME/mutter 把 X11 窗口整体钳在 work area 内（已实测 raw X11
XMoveWindow 同样被钳），"让窗口悬出屏幕、身体贴边"在 Linux 上走不通。
方案 D：窗口永远停在 work area 内（应用内主动 clamp，位置确定性），
角色身体框位置成为权威语义——窗口位置 = clamp(身体位置 − 自然偏移)，
绘制偏移 delta = 虚拟窗口位置 − 实际窗口位置，帧按 delta 在窗口内平移。

这些测试锁定：
- 屏幕中央请求时 delta == (0,0)（Windows/macOS/任意平台回归保护）；
- 身体框四边都能贴到 work area 边缘（间距 0），窗口本身不出 work area；
- 无 body_box 声明的角色包回退到"窗口即身体"（delta 恒 0，行为=现状+主动钳位）；
- delta 变化时 mask 与碰撞局部包围盒同步失效重算（画面/mask/碰撞逐像素一致）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect

import pet.catalog as catalog
from pet import window_placement
from pet.window import PetWindow, _content_frame_rect

# shenshen manifest 声明的身体框（源像素，640×360 画布，已镜像对称化；
# alpha≥128 紧致口径，与用户视觉感知的"角色边缘"一致）
BOX = (212, 60, 428, 330)
AVAIL = QRect(70, 27, 1850, 1053)  # 本机 GNOME 工作区（左 dock 70、顶栏 27）


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


class _Screen:
    def __init__(self, avail):
        self._avail = avail

    def name(self):
        return "primary"

    def availableGeometry(self):
        return self._avail

    def devicePixelRatio(self):
        return 1.0


class FakePet:
    """_move_window_towards 的最小桩：记录 move，桩掉 mask/重绘。"""

    _stable_body_local_rect = PetWindow._stable_body_local_rect
    _virtual_pos = PetWindow._virtual_pos
    _throw_bounds = PetWindow._throw_bounds

    def __init__(self, *, scale=1.0, avail=AVAIL):
        self.scale = scale
        self._w = int(round(catalog.CANVAS_W * scale))
        self._h = int(round((catalog.CANVAS_H + catalog.PAD) * scale))
        self._capture_headroom = 0
        self._draw_delta = QPoint(0, 0)
        self._collision_local_bounds = None
        self.cfg = _Config(character="shenshen")
        self._screen = _Screen(avail)
        self._x, self._y = 0, 0
        self.mask_syncs = 0
        self.updates = 0

    def _screen_available(self, screen_name=None):
        return self._screen

    def move(self, x, y):
        self._x, self._y = int(x), int(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def pos(self):
        return QPoint(self._x, self._y)

    def frameGeometry(self):
        return QRect(self._x, self._y, self._w, self._h)

    def _sync_mask(self):
        self.mask_syncs += 1

    def update(self):
        self.updates += 1


@pytest.fixture
def body_box(monkeypatch):
    monkeypatch.setattr(catalog, "character_body_box", lambda cid: BOX)
    return BOX


@pytest.fixture
def no_body_box(monkeypatch):
    monkeypatch.setattr(catalog, "character_body_box", lambda cid: None)
    return None


def _sbr(pet):
    return PetWindow._stable_body_local_rect(pet)


def _body_screen_rect(pet):
    """角色身体框的当前屏幕 rect（窗口 + 自然偏移 + delta）。"""
    sbr = _sbr(pet)
    tl = pet.pos() + sbr.topLeft() + pet._draw_delta
    return QRect(tl, sbr.size())


def test_stable_body_rect_from_manifest_scale_1(body_box):
    pet = FakePet(scale=1.0)
    sbr = _sbr(pet)
    assert sbr == QRect(BOX[0], catalog.PAD + BOX[1], BOX[2] - BOX[0], BOX[3] - BOX[1])


def test_stable_body_rect_scales(body_box):
    pet = FakePet(scale=0.5)
    sbr = _sbr(pet)
    assert sbr.x() == round(BOX[0] * 0.5)
    assert sbr.y() == round(catalog.PAD * 0.5) + round(BOX[1] * 0.5)
    assert sbr.width() == round((BOX[2] - BOX[0]) * 0.5)
    assert sbr.height() == round((BOX[3] - BOX[1]) * 0.5)


def test_stable_body_rect_fallback_full_window(no_body_box):
    pet = FakePet()
    assert _sbr(pet) == QRect(0, 0, pet._w, pet._h)


def test_middle_of_screen_keeps_zero_delta(body_box):
    pet = FakePet()
    PetWindow._move_window_towards(pet, 500, 500)
    assert pet.pos() == QPoint(500, 500)
    assert pet._draw_delta == QPoint(0, 0)


def test_body_reaches_left_edge(body_box):
    pet = FakePet()
    sbr = _sbr(pet)
    # 请求虚拟窗口位置使角色框目标 = 可用区左缘
    PetWindow._move_window_towards(pet, AVAIL.left() - sbr.x(), 500)
    assert pet.x() == AVAIL.left()  # 窗口不出 work area
    assert _body_screen_rect(pet).left() == AVAIL.left()
    # 继续往屏外拖：身体被钳在边缘（不会被拖丢），窗口仍贴边
    PetWindow._move_window_towards(pet, AVAIL.left() - sbr.x() - 500, 500)
    assert pet.x() == AVAIL.left()
    assert _body_screen_rect(pet).left() == AVAIL.left()


def test_body_reaches_right_edge(body_box):
    pet = FakePet()
    sbr = _sbr(pet)
    PetWindow._move_window_towards(pet, AVAIL.right() + 1 - sbr.x() - sbr.width(), 500)
    assert pet.x() + pet._w - 1 <= AVAIL.right()
    assert _body_screen_rect(pet).right() == AVAIL.right()


def test_body_reaches_top_edge(body_box):
    pet = FakePet()
    sbr = _sbr(pet)
    PetWindow._move_window_towards(pet, 500, AVAIL.top() - sbr.y())
    assert pet.y() == AVAIL.top()
    assert _body_screen_rect(pet).top() == AVAIL.top()


def test_body_reaches_bottom_edge(body_box):
    pet = FakePet()
    sbr = _sbr(pet)
    PetWindow._move_window_towards(pet, 500, AVAIL.bottom() + 1 - sbr.y() - sbr.height())
    assert pet.y() + pet._h - 1 <= AVAIL.bottom()
    assert _body_screen_rect(pet).bottom() == AVAIL.bottom()


def test_virtual_pos_roundtrip(body_box):
    pet = FakePet()
    sbr = _sbr(pet)
    PetWindow._move_window_towards(pet, AVAIL.left() - sbr.x() - 500, 500)
    assert PetWindow._virtual_pos(pet) == QPoint(
        AVAIL.left() - sbr.x(), 500
    ) or PetWindow._virtual_pos(pet) == pet.pos() + pet._draw_delta


def test_delta_change_invalidates_mask_and_collision_bounds(body_box):
    pet = FakePet()
    pet._collision_local_bounds = QRect(10, 10, 50, 50)
    PetWindow._move_window_towards(pet, -10000, -10000)
    assert pet._draw_delta != QPoint(0, 0)
    assert pet.mask_syncs >= 1  # delta 变化 → mask 必须重算（画面/mask 逐像素一致）
    # 碰撞局部并集是窗口局部坐标：随偏移整体平移（旧积累继续有效），
    # 不能置空——否则贴边期间碰撞体突然收紧成单帧包围盒。
    assert pet._collision_local_bounds == QRect(10, 10, 50, 50).translated(pet._draw_delta)


def test_fallback_character_behaves_like_full_canvas(no_body_box):
    """未声明 body_box 的角色包：delta 恒 0，窗口被主动钳进 work area（= 现状语义）。"""
    pet = FakePet()
    PetWindow._move_window_towards(pet, -10000, -10000)
    assert pet._draw_delta == QPoint(0, 0)
    assert pet.pos() == QPoint(AVAIL.left(), AVAIL.top())
    PetWindow._move_window_towards(pet, 100000, 100000)
    assert pet._draw_delta == QPoint(0, 0)
    assert pet.pos() == QPoint(AVAIL.right() - pet._w + 1, AVAIL.bottom() - pet._h + 1)


def test_edge_side_at_rest_uses_body_rect(body_box):
    """贴边判定用稳定身体框：mask 像素口径会被素材羽化边缘（alpha 16..128
    不进 mask）留出十几像素间隙，导致探头在 GNOME 上永远判不上。"""
    from pet.edge_probe import edge_side_at_rest

    pet = FakePet()
    # 模拟羽化：mask 可见区左缘离窗口左缘还有 22px（alpha≥128 口径）
    pet.character_local_region = lambda: QRect(22, 5, 150, 190)
    PetWindow._move_window_towards(pet, AVAIL.left() - _sbr(pet).x(), 300)
    assert edge_side_at_rest(pet, AVAIL) == "left"
    PetWindow._move_window_towards(pet, AVAIL.right() + 1 - _sbr(pet).x() - _sbr(pet).width(), 300)
    assert edge_side_at_rest(pet, AVAIL) == "right"
    PetWindow._move_window_towards(pet, 500, 300)
    assert edge_side_at_rest(pet, AVAIL) is None


def test_edge_side_at_rest_legacy_stub_falls_back_to_mask(no_body_box):
    """无身体框接口的轻量桩：回退 mask 像素口径（旧行为不变）。"""
    from pet.edge_probe import edge_side_at_rest

    class StubWin:
        def character_local_region(self):
            return QRect(100, 0, 200, 200)

        def frameGeometry(self):
            return QRect(-100, 100, 400, 300)

    assert edge_side_at_rest(StubWin(), QRect(0, 0, 1000, 800)) == "left"


def test_content_frame_rect_follows_delta(body_box):
    pet = FakePet()
    PetWindow._move_window_towards(pet, -10000, -10000)
    rect = _content_frame_rect(pet)
    assert rect.x() == pet._draw_delta.x()
    assert rect.y() == pet._draw_delta.y() + int(round(catalog.PAD * pet.scale))
    assert rect.width() == int(round(catalog.CANVAS_W * pet.scale))
    assert rect.height() == int(round(catalog.CANVAS_H * pet.scale))


def test_collision_clamp_uses_body_edges(body_box):
    """碰撞夹取的语义 = 角色身体贴 work area 边缘（不再是 _w/3 经验值）。"""
    pet = FakePet()
    sbr = _sbr(pet)
    x, y = PetWindow._collision_clamp_pos(pet, -100000.0, -100000.0)
    assert x == pytest.approx(AVAIL.left() - sbr.x())
    assert y == pytest.approx(AVAIL.top() - sbr.y())
    x, y = PetWindow._collision_clamp_pos(pet, 100000.0, 100000.0)
    assert x == pytest.approx(AVAIL.right() + 1 - sbr.x() - sbr.width())
    assert y == pytest.approx(AVAIL.bottom() + 1 - sbr.y() - sbr.height())


def test_throw_physics_bounds_use_body_edges(body_box):
    """抛掷物理边界 = 角色贴边才反弹（兑现原 _w/3 注释的意图）。"""
    pet = FakePet()
    sbr = _sbr(pet)
    bounds = PetWindow._throw_bounds(pet)
    assert bounds[0] == pytest.approx(AVAIL.left() - sbr.x())
    assert bounds[1] == pytest.approx(AVAIL.top() - sbr.y())
    assert bounds[2] == pytest.approx(AVAIL.right() + 1 - sbr.x() - sbr.width())
    assert bounds[3] == pytest.approx(AVAIL.bottom() + 1 - sbr.y() - sbr.height())


def test_go_default_corner_places_body_at_corner(body_box):
    """回到右下角：身体右缘距可用区 CORNER_MARGIN，脚底贴可用区底。"""
    pet = FakePet()
    pet._cancel_move = lambda: None
    pet._stop_physics = lambda: None
    pet._drag_target = None
    pet._save_position = lambda: None
    pet._disarm_screen_restore_retry = lambda: None
    window_placement.go_default_corner(pet)
    body = _body_screen_rect(pet)
    assert body.right() == AVAIL.right() - catalog.CORNER_MARGIN
    assert body.bottom() == AVAIL.bottom()
