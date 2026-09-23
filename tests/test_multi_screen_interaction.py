# -*- coding: utf-8 -*-
"""多显示器拖拽/抛掷（issue #186）：一次交互一个「多屏桌面区域」快照。

背景：4.2.0 的拖拽/抛掷是裸 ``self.move(...)``，宠物能被拖到副屏；4.2.1 的
#137 把所有落位统一进 ``move_window_towards``，把身体框与窗口钳进
``host.screen().availableGeometry()``（``throw_bounds`` 同源）——于是宠物被钉在
本屏，拖不过去也丢不过去。

方案：拖拽/抛掷开始时取**一次**多屏桌面快照（各屏可用区的包围矩形 + 逐屏可用
区），物理 tick 只读这份快照；活动范围再按「宠物当前所在的屏幕带」收窄
（``band_bounds``），错位拼接（异分辨率/异缩放/上下错开）时不会落进没有显示器的
空洞。单屏、几何异常、没有快照时逐位退回本屏语义——漫游、落位、边缘探头不受影响。

本机只有一块屏（DISPLAY1），所以这里用假双屏驱动**真实**的钳制与边界
代码；真实跨屏由用户在双屏机器上验收，见
``docs/PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md``。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect

import pet.catalog as catalog
from pet import window_placement
from pet.window import PetWindow

# shenshen manifest 声明的身体框（源像素，640×360 画布，已镜像对称化）
BOX = (212, 60, 428, 330)

TILED_PRIMARY = QRect(0, 0, 1920, 1040)
TILED_SECONDARY = QRect(1920, 0, 1920, 1040)
STACKED_LOWER = QRect(0, 1040, 1920, 1040)
# 错位拼接：主屏 1536×816（125% 缩放 + 任务栏），副屏 1920×1040，副屏更高
RAGGED_PRIMARY = QRect(0, 0, 1536, 816)
RAGGED_SECONDARY = QRect(1536, 0, 1920, 1040)


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


class _Screen:
    """QScreen 的最小替身：只要 availableGeometry（desktop_area 只用它）。"""

    def __init__(self, avail, name="screen"):
        self._avail = avail
        self._name = name

    def name(self):
        return self._name

    def availableGeometry(self):
        return self._avail

    def devicePixelRatio(self):
        return 1.0


class FakePet:
    """_move_window_towards / _throw_bounds 的最小桩：记录 move，桩掉 mask/重绘。"""

    _stable_body_local_rect = PetWindow._stable_body_local_rect
    _virtual_pos = PetWindow._virtual_pos
    _move_window_towards = PetWindow._move_window_towards
    _throw_bounds = PetWindow._throw_bounds

    def __init__(self, *, scale=1.0, avail=TILED_PRIMARY):
        self.scale = scale
        self._w = int(round(catalog.CANVAS_W * scale))
        self._h = int(round((catalog.CANVAS_H + catalog.PAD) * scale))
        self._capture_headroom = 0
        self._draw_delta = QPoint(0, 0)
        self._collision_local_bounds = None
        self.cfg = _Config(character="shenshen")
        self._screen = _Screen(avail)
        self._interaction_area = None
        self._physics_mode = None
        self._phys_pos = [0.0, 0.0]
        self._phys_vel = [0.0, 0.0]
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


def _install_screens(monkeypatch, *screens):
    monkeypatch.setattr(window_placement.QGuiApplication, "screens", lambda: list(screens))


def _sbr(pet):
    return PetWindow._stable_body_local_rect(pet)


def _body_rect(pet):
    """角色身体框的当前屏幕 rect（窗口 + 自然偏移 + delta）。"""
    sbr = _sbr(pet)
    return QRect(pet.pos() + sbr.topLeft() + pet._draw_delta, sbr.size())


def _tiles(body_rect, *screens):
    return any(rect.intersects(body_rect) for rect in screens)


def _snapshot(monkeypatch, *screens):
    _install_screens(monkeypatch, *screens)
    area = window_placement.desktop_area()
    assert area is not None
    return area


# ---------------------------------------------------------------- 快照本身的判定


def test_single_screen_has_no_desktop_area(body_box, monkeypatch):
    """单屏用户不产生快照——后面所有钳制逐位退回本屏语义（改造前行为）。"""
    _install_screens(monkeypatch, _Screen(TILED_PRIMARY))
    assert window_placement.desktop_area() is None


def test_two_tiled_screens_snapshot_is_union(body_box, monkeypatch):
    area = _snapshot(monkeypatch, _Screen(TILED_PRIMARY), _Screen(TILED_SECONDARY))
    assert area.bounds == QRect(0, 0, 3840, 1040)
    assert area.screens == (TILED_PRIMARY, TILED_SECONDARY)


def test_geometry_anomaly_falls_back_to_single_screen(body_box, monkeypatch):
    """任一屏几何非法就不赌多屏布局：返回 None ≈ 今天的行为。"""
    _install_screens(monkeypatch, _Screen(TILED_PRIMARY), _Screen(QRect(0, 0, 0, 0)))
    assert window_placement.desktop_area() is None
    _install_screens(monkeypatch, _Screen(TILED_PRIMARY))
    assert window_placement.desktop_area() is None


# ---------------------------------------------------------------- 拖拽（#186 主症）


def test_drag_reaches_secondary_screen(body_box, monkeypatch):
    """拖拽越屏：请求落在副屏上就直接落在副屏，不再被钉在本屏右缘。"""
    primary = _Screen(TILED_PRIMARY)
    pet = FakePet(avail=TILED_PRIMARY)
    pet._interaction_area = _snapshot(monkeypatch, primary, _Screen(TILED_SECONDARY))

    pet._move_window_towards(2500, 300)

    assert pet.x() == 2500
    assert pet.y() == 300
    assert pet._draw_delta == QPoint(0, 0)


def test_without_snapshot_drag_stays_on_current_screen(body_box, monkeypatch):
    """对照：没有快照（= 4.2.1 现状）时同一个请求被钳在本屏右缘——#186 的红。"""
    _install_screens(monkeypatch, _Screen(TILED_PRIMARY), _Screen(TILED_SECONDARY))
    pet = FakePet(avail=TILED_PRIMARY)

    pet._move_window_towards(2500, 300)

    assert pet.x() == TILED_PRIMARY.right() - pet._w + 1


def test_vertical_stack_crosses_in_y(body_box, monkeypatch):
    """上下堆叠的双屏：纵向拖拽同样越屏。"""
    pet = FakePet(avail=TILED_PRIMARY)
    pet._interaction_area = _snapshot(
        monkeypatch, _Screen(TILED_PRIMARY), _Screen(STACKED_LOWER))

    pet._move_window_towards(400, 1600)

    assert pet.y() == 1600


# ---------------------------------------------------------------- 抛掷物理边界


def test_throw_bounds_span_both_screens(body_box, monkeypatch):
    """抛掷边界换成并集：飞行不再在本屏右缘反弹（跨屏来回丢）。"""
    pet = FakePet(avail=TILED_PRIMARY)
    pet._interaction_area = _snapshot(
        monkeypatch, _Screen(TILED_PRIMARY), _Screen(TILED_SECONDARY))
    pet._phys_pos = [400.0, 300.0]

    sbr = _sbr(pet)
    left, top, right, bottom = pet._throw_bounds()
    union = QRect(0, 0, 3840, 1040)

    assert right == pytest.approx(union.right() + 1 - sbr.x() - sbr.width())
    assert right > TILED_PRIMARY.right()
    assert bottom == pytest.approx(union.bottom() + 1 - sbr.y() - sbr.height())


def test_throw_bounds_without_snapshot_use_current_screen(body_box, monkeypatch):
    _install_screens(monkeypatch, _Screen(TILED_PRIMARY), _Screen(TILED_SECONDARY))
    pet = FakePet(avail=TILED_PRIMARY)
    pet._phys_pos = [400.0, 300.0]
    sbr = _sbr(pet)

    left, top, right, bottom = pet._throw_bounds()

    assert right == pytest.approx(TILED_PRIMARY.right() + 1 - sbr.x() - sbr.width())
    assert bottom == pytest.approx(TILED_PRIMARY.bottom() + 1 - sbr.y() - sbr.height())


def test_throw_bounds_without_screen_freeze_in_place(body_box):
    """连屏幕对象都拿不到（无显示器）时冻结在原位，不抛异常。"""
    pet = FakePet(avail=TILED_PRIMARY)
    pet._screen = None
    pet._phys_pos = [12.0, 34.0]

    assert pet._throw_bounds() == (12.0, 34.0, 12.0, 34.0)


# ---------------------------------------------------------------- 错位拼接的空洞防护


def test_ragged_layout_keeps_body_on_a_screen(body_box, monkeypatch):
    """错位拼接：往主屏正下方的空洞里拖，会被收回主屏可用区内。"""
    primary = _Screen(RAGGED_PRIMARY, name="primary")
    pet = FakePet(avail=RAGGED_PRIMARY)
    pet._interaction_area = _snapshot(
        monkeypatch, primary, _Screen(RAGGED_SECONDARY, name="secondary"))

    pet._move_window_towards(200, 1200)

    body = _body_rect(pet)
    assert body.bottom() <= RAGGED_PRIMARY.bottom()
    assert _tiles(body, RAGGED_PRIMARY, RAGGED_SECONDARY)


def test_ragged_layout_lets_secondary_use_its_full_height(body_box, monkeypatch):
    """同一布局里副屏自己的高度仍然可用：越屏不是"一刀切回到矮屏"。"""
    primary = _Screen(RAGGED_PRIMARY, name="primary")
    pet = FakePet(avail=RAGGED_PRIMARY)
    pet._interaction_area = _snapshot(
        monkeypatch, primary, _Screen(RAGGED_SECONDARY, name="secondary"))

    pet._move_window_towards(2400, 1000)

    body = _body_rect(pet)
    assert pet.x() == 2400
    assert body.bottom() > RAGGED_PRIMARY.bottom()
    assert RAGGED_SECONDARY.contains(body.topLeft())


def test_ragged_throw_bounds_follow_current_screen_band(body_box, monkeypatch):
    """错位拼接的抛掷下界跟着"宠物在哪块屏"走：主屏上仍是主屏底部。"""
    primary = _Screen(RAGGED_PRIMARY, name="primary")
    pet = FakePet(avail=RAGGED_PRIMARY)
    pet._interaction_area = _snapshot(
        monkeypatch, primary, _Screen(RAGGED_SECONDARY, name="secondary"))
    sbr = _sbr(pet)

    pet._phys_pos = [200.0, 400.0]  # 主屏上
    bottom_primary = pet._throw_bounds()[3]
    pet._phys_pos = [2000.0, 400.0]  # 副屏上
    bottom_secondary = pet._throw_bounds()[3]

    assert bottom_primary == pytest.approx(
        RAGGED_PRIMARY.bottom() + 1 - sbr.y() - sbr.height())
    assert bottom_secondary == pytest.approx(
        RAGGED_SECONDARY.bottom() + 1 - sbr.y() - sbr.height())


def test_body_always_tiles_a_screen_across_requests(body_box, monkeypatch):
    """不变量：多屏快照生效时，任意落点后身体框都至少盖到一块屏的可用区。"""
    primary = _Screen(RAGGED_PRIMARY, name="primary")
    secondary = _Screen(RAGGED_SECONDARY, name="secondary")
    pet = FakePet(avail=RAGGED_PRIMARY)
    pet._interaction_area = _snapshot(monkeypatch, primary, secondary)

    for x in (-500, 0, 700, 1500, 2400, 3300, 4200):
        for y in (-300, 0, 500, 900, 1200, 2000):
            pet._move_window_towards(x, y)
            assert _tiles(_body_rect(pet), RAGGED_PRIMARY, RAGGED_SECONDARY), (x, y)


# ---------------------------------------------------------------- 其余路径不受影响


def test_explicit_body_bounds_ignores_snapshot(body_box, monkeypatch):
    """边缘探头传显式 body_bounds：即便正处于拖拽快照里，也只受本屏约束。"""
    primary = _Screen(TILED_PRIMARY)
    pet = FakePet(avail=TILED_PRIMARY)
    pet._interaction_area = _snapshot(monkeypatch, primary, _Screen(TILED_SECONDARY))
    probe_bounds = QRect(TILED_PRIMARY.left() - 400, TILED_PRIMARY.top(),
                         TILED_PRIMARY.width() + 400, TILED_PRIMARY.height())

    pet._move_window_towards(2500, 300, body_bounds=probe_bounds)

    assert pet.x() == TILED_PRIMARY.right() - pet._w + 1
    assert pet.x() < TILED_SECONDARY.left()


def test_default_corner_and_walk_keep_current_screen_after_snapshot_cleared(
        body_box, monkeypatch):
    """快照清掉后（松手/落地）本屏语义回归：并集区域不再影响后续落位。"""
    primary = _Screen(TILED_PRIMARY)
    pet = FakePet(avail=TILED_PRIMARY)
    pet._interaction_area = _snapshot(monkeypatch, primary, _Screen(TILED_SECONDARY))
    pet._move_window_towards(2500, 300)
    assert pet.x() > TILED_PRIMARY.right()

    pet._interaction_area = None
    pet._move_window_towards(2500, 300)

    assert pet.x() == TILED_PRIMARY.right() - pet._w + 1


# ---------------------------------------------------------------- 快照生命周期与开销


class _PhysicsStub:
    """只带 _enter_physics_mode / _stop_physics 依赖的最小桩。"""

    _enter_physics_mode = PetWindow._enter_physics_mode
    _stop_physics = PetWindow._stop_physics

    def __init__(self):
        self._physics_mode = None
        self._interaction_area = None
        self._throw_slow_switched = False
        self._phys_vel = [0.0, 0.0]
        self._interaction_state = "IDLE"
        self.idles = ()
        self.movie = None
        self.drag = None
        self.anim = None
        self._physics_timer = self

    def stop(self):
        pass

    def _cancel_move(self):
        pass

    def _cancel_animation_gap(self):
        pass

    def _warm_landing_idles(self):
        pass

    def _unpin_landing_idles(self):
        pass

    def _submit_collision_state(self, **kwargs):
        pass


def test_snapshot_lifecycle_across_physics_modes(body_box, monkeypatch):
    """拖拽/抛掷进入时取快照，物理结束（落地、锁定位置打断）时释放。"""
    _install_screens(monkeypatch, _Screen(TILED_PRIMARY), _Screen(TILED_SECONDARY))
    pet = _PhysicsStub()

    pet._enter_physics_mode('throw')
    assert pet._interaction_area is not None

    pet._stop_physics()
    assert pet._interaction_area is None

    pet._enter_physics_mode('drag')
    assert pet._interaction_area is not None

    pet._stop_physics()
    assert pet._interaction_area is None


def test_hot_paths_never_enumerate_screens(body_box, monkeypatch):
    """热路径只读快照：连续 N 轮钳制/边界计算都不会再调用 desktop_area()。"""
    calls = []
    monkeypatch.setattr(window_placement, "desktop_area", lambda: calls.append(1))
    pet = FakePet(avail=TILED_PRIMARY)
    pet._interaction_area = window_placement.DesktopArea(
        bounds=QRect(0, 0, 3840, 1040),
        screens=(TILED_PRIMARY, TILED_SECONDARY),
    )

    for _ in range(60):
        pet._move_window_towards(2500, 300)
        pet._throw_bounds()

    assert calls == []
    assert pet.x() == 2500
