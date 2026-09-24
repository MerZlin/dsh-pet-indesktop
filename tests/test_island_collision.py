# -*- coding: utf-8 -*-
"""灵动岛碰撞（本进程直连版，同步硬墙）。

30Hz 检测/结算已整体移除（issue #146 实机教训）：岛改为屏幕边界式位置硬墙，
在统一位置出口 move_window_towwards 里同步钳制，杜绝采样间隙导致的抽搐。
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from pet import collision, window_placement
from pet import physics as physics_mod
from pet.collision_ipc import _KNOWN_FLAGS_MASK
from pet.config import Config
from pet.dynamic_island import DynamicIsland
from pet.island_collision import IslandCollisionBody


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _pet(runtime_id="pet", x=100.0, y=100.0, vx=-600.0, flags=0) -> collision.MemberState:
    return collision.MemberState(
        runtime_id=runtime_id,
        x=x,
        y=y,
        radius_x=50.0,
        radius_y=50.0,
        vx=vx,
        vy=0.0,
        flags=collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED | flags,
    )


def _island_state(flags=collision.FLAG_STATIC) -> collision.MemberState:
    return collision.MemberState(
        runtime_id="island",
        x=200.0,
        y=100.0,
        radius_x=100.0,
        radius_y=22.0,
        vx=0.0,
        vy=0.0,
        is_infinite_mass=True,
        flags=collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED | flags,
    )


# ------------------------------------------------------------ 弹性规则（通用求解器）
def test_static_wall_boosts_restitution_pet_bounces_off_faster():
    """FLAG_STATIC 静态岛加速弹开（e=1.3）：撞岛比撞墙弹得更快，像撞弹床。"""
    pet = _pet(vx=600.0)  # 桌宠在岛左侧，向右（+x）撞岛
    island = _island_state()
    # nx=-1：法线从 A(岛) 指向 B(桌宠)，接近速度 vn = 600*(-1) < 0
    jn, dvx_a, _dvy_a, dvx_b, _dvy_b = collision.solve_collision_impulse(island, pet, -1.0, 0.0, restitution=0.82, friction=0.08, impulse_cap=9000.0)
    assert jn > 0
    assert dvx_a == 0.0  # 岛无限质量，不动
    # e=1.3 加速反弹：桌宠末速 = 600 * (-1.3) = -780（比入射更快地弹回）
    assert pet.vx + dvx_b < -600.0


def test_static_wall_low_speed_contact_stays_dead():
    """低速贴上岛不抖动：接近速度低于阈值仍 e=0（只挡不弹）。"""
    pet = _pet(vx=50.0)  # 低于 IMPULSE_MIN_APPROACH_SPEED(80)
    island = _island_state()
    _jn, _dva, _dva2, dvx_b, _dvb = collision.solve_collision_impulse(island, pet, -1.0, 0.0, restitution=0.82, friction=0.08, impulse_cap=9000.0)
    # e=0：仅消除接近速度，不反转、不加速
    assert abs(pet.vx + dvx_b) < 1e-6


def test_static_flag_in_known_mask():
    assert _KNOWN_FLAGS_MASK & collision.FLAG_STATIC == collision.FLAG_STATIC


# ------------------------------------------------------------ 同步硬墙（屏幕边界式位置钳制）
class _ClampScreen:
    def __init__(self, w: int = 3840, h: int = 2160):
        self._w, self._h = w, h

    def name(self):
        return "big"

    def availableGeometry(self):
        return QRect(0, 0, self._w, self._h)

    def devicePixelRatio(self):
        return 1.0


class FakePhysicsTimer:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True


class WallWin:
    """同步墙的窗口桩：全窗口即身体（无 body_box），带虚拟坐标/物理状态。

    _move_window_towwards 走真实统一出口（window_placement.move_window_towards），
    供"落窗即钳制 / 抛掷反射 / submit 推挤 / 撞岛业务链"端到端断言使用。
    """

    def __init__(
        self,
        x: float,
        y: float,
        w: int,
        h: int,
        *,
        physics_mode: str = "",
        vx: float = 0.0,
        vy: float = 0.0,
        visible: bool = True,
        screen_w: int = 3840,
        screen_h: int = 2160,
    ):
        self._x, self._y = float(x), float(y)
        self._w, self._h = w, h
        self.scale = 1.0
        self._capture_headroom = 0
        self._draw_delta = QPoint(0, 0)
        self._collision_local_bounds = None
        self._visible = visible
        self.cfg = SimpleNamespace(get=lambda k, d=None: d)
        self._screen = _ClampScreen(screen_w, screen_h)
        self._physics_mode = physics_mode
        self._phys_vel = [vx, vy]
        self._phys_pos = [float(x), float(y)]
        self._hidden_paused = False
        self._interaction_state = "IDLE"
        self._throw_speed_cap = 4800.0
        self._throw_egg = None
        self._edge_probe = None
        self._squash_active = False
        self._just_dragged = False
        self._last_physics_tick_time = None
        self._physics_timer = FakePhysicsTimer()
        self.sounds = 0
        self.squashes = 0
        self.entered_modes = []

    def _screen_available(self, *_a, **_k):
        return self._screen

    def pos(self):
        return QPoint(int(self._x), int(self._y))

    def move(self, x, y):
        self._x, self._y = float(x), float(y)

    def x(self):
        return int(self._x)

    def y(self):
        return int(self._y)

    def isVisible(self):
        return self._visible

    def _stable_body_local_rect(self):
        return QRect(0, 0, self._w, self._h)

    def _virtual_pos(self):
        return QPoint(int(self._x) + self._draw_delta.x(), int(self._y) + self._draw_delta.y())

    def _move_window_towards(self, x, y, body_bounds=None):
        window_placement.move_window_towards(self, x, y, body_bounds=body_bounds)

    def _cancel_move(self):
        pass

    def _cancel_animation_gap(self):
        pass

    def _play_collision_sound(self):
        self.sounds += 1

    def _enter_physics_mode(self, mode):
        self._physics_mode = mode
        self.entered_modes.append(mode)

    def _start_squash(self):
        self.squashes += 1

    def _sync_mask(self):
        pass

    def update(self):
        pass

    def _schedule_position_sync(self):
        pass

    def _submit_collision_state(self, *_a, **_k):
        pass


def _make_body(tmp_path: Path, pets=()):
    cfg = Config(base=tmp_path)
    cfg.set("dynamic_island", {"enabled": True, "x": 400, "y": 300})
    island = DynamicIsland(cfg)
    body = IslandCollisionBody(island, cfg, pets_provider=lambda: list(pets))
    return island, body


def _assert_body_out_of_stadium(win, body, msg=""):
    """断言身体框（全窗口）中心到岛轴的距离 >= 矩形径向半径 + 岛半径 + 1。"""
    stadium = body._island_stadium()
    ax0, ax1, ay, rr, _h = stadium
    center_x = win.x() + win._draw_delta.x() + win._w / 2.0
    center_y = win.y() + win._draw_delta.y() + win._h / 2.0
    closest_x = min(max(center_x, ax0), ax1)
    dx, dy = center_x - closest_x, center_y - ay
    dist = math.hypot(dx, dy)
    assert dist > 1e-9, f"身体中心不应恰好落在岛轴上：{msg}"
    nx, ny = dx / dist, dy / dist
    radial = min((win._w / 2) / max(abs(nx), 1e-9), (win._h / 2) / max(abs(ny), 1e-9))
    assert dist >= radial + rr + 1.0 - 1e-6, f"身体框未被钳出岛区：中心距轴 {dist:.1f} < 径向 {radial:.1f} + 岛半径 {rr} + 1（{msg}）"


def test_body_start_stop_lifecycle(tmp_path):
    """start 给桌宠挂同步硬墙 hook 并置 running；stop 清除（岛碰撞关闭后不设墙）。"""
    _qapp()
    win = WallWin(1000, 1000, 200, 100)
    island, body = _make_body(tmp_path, pets=[win])
    try:
        island.show()
        assert getattr(win, "_island_clamp_body", None) is None
        body.start()
        assert body._running
        # 绑定方法每次访问是新对象，比 __func__
        assert win._island_clamp_body.__func__ is body._clamp_body.__func__
        body.stop()
        assert not body._running
        assert win._island_clamp_body is None
    finally:
        island.hide()
        island.deleteLater()


def test_synchronous_clamp_keeps_body_out_of_island(tmp_path):
    """同步硬墙：统一位置出口在每次落窗前把身体框钳出岛碰撞区（像屏幕边界墙）。

    30Hz 判定有采样间隙（帧间可钻进区→被弹→速度不足离区→再判定→抽搐）；
    同步钳制在 move_window_towwards 里逐次落窗，身体框根本不进入岛区，从源头
    杜绝穿透与抽搐。这里目标位置让身体中心落在岛轴上，应被钳到岛外。
    """
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        win = WallWin(1000, 1000, 200, 100)
        win._island_clamp_body = body._clamp_body
        stadium = body._island_stadium()
        ax0, ax1, ay, rr, _h = stadium
        target_cx = (ax0 + ax1) / 2.0
        window_placement.move_window_towards(win, target_cx - win._w / 2.0, ay - win._h / 2.0)
        _assert_body_out_of_stadium(win, body, "落点恰在岛轴")
    finally:
        island.hide()
        island.deleteLater()


def test_no_island_clamp_without_hook(tmp_path):
    """无 hook（岛碰撞关闭）时 move_window_towards 不钳制——行为与改造前一致。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        win = WallWin(1000, 1000, 200, 100)
        stadium = body._island_stadium()
        ax0, ax1, ay, _rr, _h = stadium
        target_cx = (ax0 + ax1) / 2.0
        window_placement.move_window_towards(win, target_cx - win._w / 2.0, ay - win._h / 2.0)
        center_x = win.x() + win._draw_delta.x() + win._w / 2.0
        center_y = win.y() + win._draw_delta.y() + win._h / 2.0
        # 统一出口对坐标取整（int(round)），容差放 1px
        assert abs(center_x - target_cx) < 1.0
        assert abs(center_y - ay) < 1.0
    finally:
        island.hide()
        island.deleteLater()


def test_clamp_noop_when_body_already_outside(tmp_path):
    """远处落点不动墙：身体框未越界时 _clamp_body 原样返回（逐像素落窗）。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        win = WallWin(100, 100, 120, 120)
        win._island_clamp_body = body._clamp_body
        window_placement.move_window_towards(win, 100.0, 100.0)
        assert win.x() == 100 and win.y() == 100
    finally:
        island.hide()
        island.deleteLater()


def test_wall_inactive_when_stopped_hidden_or_docked(tmp_path):
    """墙在 未运行/岛隐藏/细条态/几何动画 时不钳制（原样落窗，不设幻影墙）。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        win = WallWin(1000, 1000, 200, 100)
        win._island_clamp_body = body._clamp_body
        stadium = body._island_stadium()
        ax0, ax1, ay, _rr, _h = stadium
        tx = (ax0 + ax1) / 2.0 - win._w / 2.0
        ty = ay - win._h / 2.0

        def center_on_axis():
            window_placement.move_window_towards(win, tx, ty)
            center_x = win.x() + win._w / 2.0
            return abs(center_x - (ax0 + ax1) / 2.0) < 1.0

        assert center_on_axis(), "未运行时墙应失效"
        body._running = True
        island.hide()
        assert center_on_axis(), "岛隐藏时墙应失效"
        island.show()
        island._mode = "docked"
        island._hover_peek = False
        assert center_on_axis(), "细条态墙应失效"
        island._mode = "normal"
        island._geo_to = QRect(400, 300, 340, 44)
        assert center_on_axis(), "几何动画中墙应失效"
    finally:
        island.hide()
        island.deleteLater()


def test_tall_pet_side_wall_uses_half_width(tmp_path):
    """高瘦桌宠贴岛侧壁：同步墙按横向半宽推出（矩形口径），不再按身高的一半。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        win = WallWin(1000, 1000, 156, 194)  # 高瘦：横向半宽 78、纵向半高 97
        win._island_clamp_body = body._clamp_body
        stadium = body._island_stadium()
        ax0, _ax1, ay, rr, _h = stadium
        # 目标：身体中心与岛轴同高、位于左端帽左侧 90px（90 < 78+22 会越界），
        # 纯横向分离，按横向半宽推出
        window_placement.move_window_towards(win, ax0 - 90.0 - 156.0 / 2.0, ay - 194.0 / 2.0)
        center_x = win.x() + win._w / 2.0
        assert abs(center_x - (ax0 - (78.0 + rr + 1.0))) < 2.0
    finally:
        island.hide()
        island.deleteLater()


def test_wall_geometry_independent_of_screen_size(tmp_path):
    """不同分辨率屏幕：同步墙只依赖岛几何 + 身体矩形（与屏幕尺寸无关）。"""
    _qapp()
    for screen_w, screen_h in ((1024, 768), (1920, 1080), (3840, 2160)):
        island, body = _make_body(tmp_path)
        try:
            island.show()
            body._running = True
            win = WallWin(1000, 1000, 200, 100, screen_w=screen_w, screen_h=screen_h)
            win._island_clamp_body = body._clamp_body
            stadium = body._island_stadium()
            ax0, ax1, ay, rr, _h = stadium
            window_placement.move_window_towards(win, (ax0 + ax1) / 2.0 - win._w / 2.0, ay - win._h / 2.0)
            _assert_body_out_of_stadium(win, body, f"屏幕 {screen_w}x{screen_h}")
        finally:
            island.hide()
            island.deleteLater()


def test_throw_mode_pet_reflects_off_island_wall(tmp_path):
    """抛掷中撞岛：速度沿墙法线反射（e=RESTITUTION）+ 物理位置钉在钳制点。

    像撞屏幕边缘一样弹开；纯钳制只挡位置会让物理空间穿过岛、视觉被钉在墙上
    直到落体结束——反射后物理/视觉一致。真撞附带命中反馈（音效/挤压/岛弹跳）。
    """
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        bumps = []
        island.bump = lambda *a: bumps.append(a)
        # 岛左端帽 (ax0, ay)；身体 120×120 中心放 ax0-80（距轴 80 < 60+22），
        # 向右 600px/s 高速接近 → 应反射成 -600*RESTITUTION 向左弹开
        stadium = body._island_stadium()
        ax0, _ax1, ay, rr, _h = stadium
        win = WallWin(ax0 - 140.0, ay - 60.0, 120, 120, physics_mode="throw", vx=600.0, vy=0.0)
        win._island_clamp_body = body._clamp_body
        win._move_window_towards(win._virtual_pos().x(), win._virtual_pos().y())
        assert win._phys_vel[0] < 0.0  # 弹回来路
        assert abs(win._phys_vel[0] - (-600.0 * physics_mod.RESTITUTION)) < 1e-6
        assert win._phys_vel[1] == 0.0
        # 物理位置钉在虚拟钳制点（= 落窗后的虚拟坐标），下一帧从此起跳
        vp = win._virtual_pos()
        assert abs(win._phys_pos[0] - vp.x()) < 1e-6
        assert abs(win._phys_pos[1] - vp.y()) < 1e-6
        _assert_body_out_of_stadium(win, body, "抛掷反射")
        # 命中反馈：音效/挤压/岛弹跳（不重进 throw——已处于抛掷中）
        assert win.sounds == 1
        assert win.squashes == 1
        assert len(bumps) == 1
        assert win._physics_mode == "throw"
    finally:
        island.hide()
        island.deleteLater()


def test_fast_roaming_pet_flings_off_island_with_feedback(tmp_path):
    """漫游高速撞岛（原有业务）：冲量 + 进抛掷物理 + 音效/挤压/岛弹跳，事件驱动。

    与 30Hz 检测无关：墙在落窗时按接触跟踪测得的接近速度判真撞，走 _apply_hit
    业务链（撞岛像撞弹床 e=STATIC_RESTITUTION）。
    """
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        bumps = []
        island.bump = lambda *a: bumps.append(a)
        # 身体 120×120 中心在岛左端帽左侧 80px（越界 80 < 60+22）
        stadium = body._island_stadium()
        ax0, _ax1, ay, rr, _h = stadium
        win = WallWin(ax0 - 140.0, ay - 60.0, 120, 120, vx=0.0)
        win._island_clamp_body = body._clamp_body
        now = time.monotonic()
        # 模拟漫游接近：上一帧在更左 12px（30ms 前）→ 接触跟踪测得 400px/s
        body._contact[id(win)] = (ax0 - 152.0, ay - 60.0, now - 0.03)
        win._move_window_towards(win._virtual_pos().x(), win._virtual_pos().y())
        assert win._physics_mode == "throw"  # 进抛掷物理（被拍飞）
        assert win._interaction_state == "THROWN"
        assert win._phys_vel[0] < 0.0  # 弹回左侧
        assert abs(win._phys_vel[0]) > 400.0 * 1.3  # e=1.3 加速反弹
        assert win.sounds == 1
        assert win.squashes == 1
        assert len(bumps) == 1
        _assert_body_out_of_stadium(win, body, "漫游真撞")
    finally:
        island.hide()
        island.deleteLater()


def test_slow_roaming_pet_is_pushed_without_feedback(tmp_path):
    """慢速贴岛（轻贴）：只推出不撞——不响、不挤、不弹、不进抛掷物理。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        bumps = []
        island.bump = lambda *a: bumps.append(a)
        stadium = body._island_stadium()
        ax0, _ax1, ay, rr, _h = stadium
        win = WallWin(ax0 - 140.0, ay - 60.0, 120, 120, vx=0.0)
        win._island_clamp_body = body._clamp_body
        now = time.monotonic()
        # 30ms 前同位 → 接触测得接近速度 ~0（轻贴）
        body._contact[id(win)] = (ax0 - 140.0, ay - 60.0, now - 0.03)
        win._move_window_towards(win._virtual_pos().x(), win._virtual_pos().y())
        assert win._physics_mode != "throw"
        assert win._interaction_state == "IDLE"
        assert win.sounds == 0
        assert win.squashes == 0
        assert bumps == []
        _assert_body_out_of_stadium(win, body, "轻贴推出")
    finally:
        island.hide()
        island.deleteLater()


def test_submit_flings_pet_when_island_swept_onto_it(tmp_path):
    """拖岛拍鱼（原有业务）：岛被快速拖向静止桌宠，桌宠沿相对运动方向被拍飞
    + 反馈（岛速参与结算，submit 事件驱动，无定时器）。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        bumps = []
        island.bump = lambda *a: bumps.append(a)
        # 让岛速采样基线暗示"正以 800px/s 向右拖"
        rect = island.geometry()
        body._last_size = (rect.width(), rect.height())
        body._last_center = (float(rect.center().x()) - 40.0, float(rect.center().y()))
        body._last_motion_ts = time.monotonic() - 0.05
        # 静止桌宠中心在岛右端帽外侧 20px（身体已越界，会被推出并判真撞）
        stadium = body._island_stadium()
        ax0, ax1, ay, rr, _h = stadium
        win = WallWin(ax1 + 20.0 - 60.0, ay - 60.0, 120, 120, vx=0.0)
        win._island_clamp_body = body._clamp_body
        body._pets_provider = lambda: [win]
        island.on_geometry_changed = body.submit
        island.on_geometry_changed()
        assert win._physics_mode == "throw"  # 被拍飞进抛掷物理
        assert win._phys_vel[0] > 0.0  # 沿岛运动方向（向右）飞出
        assert win.sounds == 1
        assert win.squashes == 1
        assert len(bumps) == 1
        _assert_body_out_of_stadium(win, body, "拖岛拍鱼")
    finally:
        island.hide()
        island.deleteLater()


def test_light_contact_cancels_pet_move_plan(tmp_path):
    """轻贴推出取消桌宠自主移动计划（与旧分离同口径）——避免漫游原地踏步顶墙。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        stadium = body._island_stadium()
        ax0, _ax1, ay, rr, _h = stadium
        win = WallWin(ax0 - 140.0, ay - 60.0, 120, 120, vx=0.0)
        win._island_clamp_body = body._clamp_body
        cancels = {"move": 0, "gap": 0}
        win._cancel_move = lambda: cancels.__setitem__("move", cancels["move"] + 1)
        win._cancel_animation_gap = lambda: cancels.__setitem__("gap", cancels["gap"] + 1)
        win._move_window_towards(win._virtual_pos().x(), win._virtual_pos().y())
        assert cancels["move"] >= 1 and cancels["gap"] >= 1
        _assert_body_out_of_stadium(win, body, "轻贴取消移动计划")
    finally:
        island.hide()
        island.deleteLater()


def test_submit_pushes_out_resting_pet_when_island_moved_onto_it(tmp_path):
    """岛被拖到静止桌宠身上：submit 事件驱动把桌宠推出（岛动桌宠没动也挡）。"""
    _qapp()
    island, body = _make_body(tmp_path)
    try:
        island.show()
        body._running = True
        stadium = body._island_stadium()
        ax0, ax1, ay, _rr, _h = stadium
        win = WallWin(1000, 1000, 200, 100)
        # 让身体中心恰在岛轴上（模拟岛拖过来盖住静止桌宠）
        win._x, win._y = (ax0 + ax1) / 2.0 - win._w / 2.0, ay - win._h / 2.0
        win._island_clamp_body = body._clamp_body
        body._pets_provider = lambda: [win]
        island.on_geometry_changed = body.submit  # 接线与 app.py 一致
        island.on_geometry_changed()
        _assert_body_out_of_stadium(win, body, "岛拖到静止桌宠身上")
    finally:
        island.hide()
        island.deleteLater()


def test_wall_hook_covers_pet_window_created_after_start(tmp_path):
    """启动之后新生的桌宠（生小肥鱼）也必须受硬墙约束。

    回归背景：硬墙 hook 只在 start() 挂到当时已存在的窗口上；本进程 spawn
    出来的新窗不在那一刻的列表里，且 start() 二次调用直接 return——新鱼于是
    可以整个走进岛里（30Hz 时代由每帧遍历 pets_provider 自动覆盖，无此缺口）。
    管线：spawn → body.refresh_hooks() → 新窗同样被钳出岛区。
    """
    _qapp()
    pets = [WallWin(1000, 900, 200, 300)]
    island, body = _make_body(tmp_path, pets)
    try:
        island.show()
        body.start()
        assert callable(getattr(pets[0], "_island_clamp_body", None)), "启动时已存在的窗应有钩子"

        late = WallWin(1000, 1500, 200, 300)  # 启动之后才入列（spawn_in_process_window）
        pets.append(late)
        body.refresh_hooks()

        assert callable(getattr(late, "_island_clamp_body", None)), "启动后新生的桌宠未挂上硬墙钩子——它会直接穿过灵动岛"
        stadium = body._island_stadium()
        ax0, ax1, ay, _rr, _h = stadium
        late._move_window_towards((ax0 + ax1) / 2.0 - late._w / 2.0, ay - late._h / 2.0)
        _assert_body_out_of_stadium(late, body, "启动后新生的桌宠")
    finally:
        island.hide()
        island.deleteLater()


def test_refresh_hooks_is_noop_when_stopped(tmp_path):
    """碰撞体已停（果冻墙关掉）时刷新钩子不得把墙偷偷挂回来。"""
    _qapp()
    pets = [WallWin(1000, 900, 200, 300)]
    island, body = _make_body(tmp_path, pets)
    try:
        island.show()
        body.start()
        body.stop()
        late = WallWin(1000, 1500, 200, 300)
        pets.append(late)
        body.refresh_hooks()
        assert getattr(late, "_island_clamp_body", None) is None, "停用状态下不该挂墙"
        assert getattr(pets[0], "_island_clamp_body", None) is None, "停用状态下旧的钩子应已清掉"
    finally:
        island.hide()
        island.deleteLater()
