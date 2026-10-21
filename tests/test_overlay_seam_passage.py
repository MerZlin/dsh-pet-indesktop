# -*- coding: utf-8 -*-
"""跨屏拖拽的缝上语义：一张合成窗、活动区是矩形集合，缝对行为不可见。

旧拓扑把每块屏各开一张窗，于是"过缝"需要临时开合法域、按实际身体位置移交、
再靠邻窗画只读投影把跨缝的那一半补上——那套机制带来三处几何缺陷（提前移交导致
跨屏大跳、单矩形钳制域在缝上钳住身体、换帧顺序错导致两次钳制叠加）。

单合成窗铺满整个虚拟桌面之后，缝只是活动区里两块矩形的公共边，不再有任何移交
动作。本文件因此改钉这几条**结果**合同（全部只看全局坐标/像素，不看实现）：

- 缝对轨迹不可见：跨两块屏的逐拍轨迹与同一块大屏上的参考轨迹逐拍一致；
- 没有任何一拍出现跳变（每拍位移不超过弹簧自己的剩余滞后）；
- 身体跨在缝上时松手不跳、不掉进屏间空洞、不被控制器拽回；
- 错位拼接的空洞挡得住：拖进空洞的宠被挡在离它最近的那块屏里；
- 上下左右的邻屏都算通道；
- 开关关着时活动区只剩启动那块屏，拖不过去；
- 跨缝的身体在唯一合成窗上完整可见（不再需要邻窗投影）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.usefixtures("non_macos_overlay_topology")
from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
import tests.test_overlay_hosts_multiscreen as hosts_mod
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite
from tests.test_overlay_hosts_multiscreen import (
    FakeScreen, _hittable, _move, _press, _release)

app = QApplication.instance() or QApplication([])

TICK_S = 1 / 60.0


def _shell(tmp_path, screen, values=None, *, switch=True):
    # hosts 的 _make_shell 会把 allow_cross_screen 默认成 True（那个文件整体测"允许
    # 跨屏"），这里要能显式关掉，故先放进 values 再传。
    values = {"allow_cross_screen": switch, **dict(values or {})}
    return hosts_mod._make_shell(tmp_path, screen, values)


def _install(monkeypatch, screens):
    """屏枚举喂假屏（只补**类级** Qt API，不碰实例属性——实例 patch 会泄漏）。"""
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: list(screens)))
    monkeypatch.setattr(QGuiApplication, "primaryScreen",
                        staticmethod(lambda: screens[0]))


def _tick(shell, dt=TICK_S):
    """一个真 tick 的两段：仿真段 + 推进段（推进段收尾才跑跨屏路由）。"""
    shell.driver.tick_sim(dt)
    shell.driver.advance_overlays(dt)


def _global(shell, sprite):
    host = shell.host_for_sprite(sprite)
    return QPointF(host.overlay.geometry().topLeft()) + QPointF(sprite.pos)


def _global_target(shell, sprite):
    """弹簧目标的**全局等价位置**（换帧后局部值必须变，全局值必须不变）。"""
    target = getattr(sprite, "_drag_spring_target", None)
    if target is None:
        return None
    host = shell.host_for_sprite(sprite)
    return QPointF(host.overlay.geometry().topLeft()) + QPointF(target)


def _global_trail(shell, sprite):
    """拖拽轨迹样本的**全局等价** (x, y) 列表（换帧后全局值必须不变）。"""
    host = shell.host_for_sprite(sprite)
    origin = QPointF(host.overlay.geometry().topLeft())
    return [(origin.x() + row[1], origin.y() + row[2]) for row in sprite.drag_trail]


def _two_screens(height=1080, avail_height=1040):
    a = FakeScreen((0, 0, 1920, height), (0, 0, 1920, avail_height), name="DISPLAY1")
    b = FakeScreen((1920, 0, 1920, height), (1920, 0, 1920, avail_height), name="DISPLAY2")
    return a, b


def _grab(shell, sprite, at=None):
    """在 sprite 当前宿主窗里按下（返回 (overlay, origin, cursor)）。"""
    overlay = shell.host_for_sprite(sprite).overlay
    origin = QPointF(overlay.geometry().topLeft())
    _hittable(sprite)
    hit = QPointF(sprite.rect().center()) if at is None else QPointF(at)
    overlay.mousePressEvent(_press(hit, origin + hit))
    return overlay, origin, origin + hit


# ---------------------------------------------------------------- 1. 无 tick 不提前移交
def test_cursor_jump_without_tick_neither_moves_the_pet(
        tmp_path, monkeypatch):
    """光标一步跨到另一块屏、但**一个 tick 都没跑**：弹簧档位置不动。

    弹簧档的实际位移只在 advance 里发生——没有 tick 就没有位移。这条同时挡住
    "跨屏那一帧身体瞬移"的复发（旧实现正是在这一帧换了宿主）。
    """
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a, {"drag_physics": True})
    try:
        main = shell.sprite
        main.set_pos(QPointF(300.0, 600.0))
        overlay, origin, cursor = _grab(shell, main)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))   # 过阈值：真拖拽
        _tick(shell)                                             # 弹簧开始跟
        before_global = _global(shell, main)

        cursor = QPointF(origin.x() + 2600.0, cursor.y())        # 一步跨到副屏深处
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))

        assert _global(shell, main) == before_global, (
            "弹簧档的实际位置只在 advance 里变；事件本身不得移动它")
        assert main._drag_spring_target is not None, "前提：弹簧目标已更新（这才是事件的作用）"

        # 弹簧要好几拍才把身体推进邻屏：每一拍的位移都不许超过"到目标的剩余距离"
        spring_before = _global_target(shell, main)
        velocity_before = QPointF(main.velocity)
        crossed = None
        for tick in range(1, 120):
            prev_global = _global(shell, main)
            lag = abs(QPointF(main._drag_spring_target).x() - main.pos.x())
            _tick(shell)
            step = abs(_global(shell, main).x() - prev_global.x())
            assert step <= lag + 4.0, (
                f"第 {tick} 拍位移 {step} 明显超过剩余滞后 {lag}（瞬移，不是弹簧）")
            if shell._screen_of_sprite(main) is b:
                crossed = tick
                break
        assert crossed is not None and crossed > 1, (
            "弹簧档按自己的节奏推进（不是光标一过缝就瞬移）")
        assert _global_target(shell, main) == spring_before, (
            "弹簧目标的**全局**位置不许被改写")
        assert QPointF(main.velocity) == velocity_before, "速度不许被改写"
    finally:
        hosts_mod._cleanup(shell)


def test_seam_trajectory_matches_a_seamless_screen(tmp_path, monkeypatch):
    """逐 tick 全局轨迹 == 同一块大屏（没有内缝）上的参考轨迹。

    缝对轨迹必须是**不可见**的：同样的光标路径、同样的 tick 节奏，跨两块屏的宠与
    在一块 3840 宽屏上的宠每一拍都应落在同一个全局位置（1:1 档两块的可用区并集与
    那块大屏逐位相同，弹簧档的目标点也相同）。
    """
    samples_seam = []
    samples_ref = []

    def run(screens, out, tmp, physics):
        _install(monkeypatch, screens)
        shell = _shell(tmp, screens[0], {"drag_physics": physics})
        try:
            main = shell.sprite
            main.set_pos(QPointF(300.0, 600.0))
            overlay, origin, cursor = _grab(shell, main)
            overlay.mouseMoveEvent(_move(cursor - origin, cursor))
            _tick(shell)
            for step in range(90):
                cursor = QPointF(cursor.x() + 32.0, cursor.y())
                overlay.mouseMoveEvent(_move(cursor - origin, cursor))
                _tick(shell)
                out.append(round(_global(shell, main).x(), 3))
            overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
            return shell
        finally:
            pass

    a, b = _two_screens()
    big = FakeScreen((0, 0, 3840, 1080), (0, 0, 3840, 1040), name="BIG")
    seam_shell = run([a, b], samples_seam, tmp_path / "seam", physics=False)
    ref_shell = run([big], samples_ref, tmp_path / "ref", physics=False)
    try:
        assert len(samples_seam) == len(samples_ref) == 90
        assert samples_seam == pytest.approx(samples_ref, abs=0.001), (
            "跨缝轨迹必须与无内缝参考逐拍一致（差一点点就是钳制/提前移交留下的位移）")
    finally:
        hosts_mod._cleanup(seam_shell)
        hosts_mod._cleanup(ref_shell)


def test_crossing_the_seam_never_teleports(tmp_path, monkeypatch):
    """跨过屏边界那一拍：全局位置零跳变（没有"移交"这回事）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a, {"drag_physics": True})
    try:
        main = shell.sprite
        main.set_pos(QPointF(300.0, 600.0))
        overlay, origin, cursor = _grab(shell, main)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)

        cursor = QPointF(origin.x() + 2400.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))

        spring_before = _global_target(shell, main)
        velocity_before = QPointF(main.velocity)
        trail_before = _global_trail(shell, main)
        first_in_b = False
        prev = _global(shell, main)
        for _ in range(200):
            lag = abs(QPointF(main._drag_spring_target).x() - main.pos.x())
            _tick(shell)
            now = _global(shell, main)
            step = abs(now.x() - prev.x())
            assert step <= lag + 4.0, (
                f"跨屏那拍位移 {step} 超过剩余滞后 {lag}（那是瞬移）")
            prev = now
            if shell._screen_of_sprite(main) is b:
                first_in_b = True
                break
        assert first_in_b, "弹簧跑满 200 拍身体仍未进副屏"
        assert _global_target(shell, main) == spring_before, (
            "弹簧目标的全局位置不许被改写")
        assert QPointF(main.velocity) == velocity_before, "速度不许被改写"
        assert len(_global_trail(shell, main)) >= len(trail_before), "轨迹样本照旧累积"
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 4. 只有真相接的屏相连
def _drag_to(shell, sprite, target_global):
    overlay, origin, cursor = _grab(shell, sprite)
    overlay.mouseMoveEvent(_move(cursor - origin, cursor))
    _tick(shell)
    overlay.mouseMoveEvent(_move(target_global - origin, target_global))
    _tick(shell)
    return overlay, origin


def test_staggered_screens_keep_the_pet_out_of_the_gap(tmp_path, monkeypatch):
    """错位拼接的屏间**空洞不是可站立区**：松手后必须落在真实屏上。

    活动区是"每屏可用区一块矩形"的集合，不是它们的并集包围矩形——没有显示器的
    地方不能站。拖动**过程中**允许经过空洞（严域会在身体框跨缝时把它钳回最近那块
    屏，实测表现是撞墙 + 瞬移），钳制在松手收尾时补一次。
    """
    a = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="A")
    # 左下错位一块副屏：两块屏之间在 x∈(1920, 2000), y∈(0,1000) 一带是空洞
    diagonal = FakeScreen((2000, 1000, 1920, 1080), (2000, 1000, 1920, 1040),
                          name="D")
    _install(monkeypatch, [a, diagonal])
    shell = _shell(tmp_path, a)
    try:
        assert len(shell._main_host.activity) == 2
        main = shell.sprite
        main.set_pos(QPointF(300.0, 600.0))
        overlay, origin = _drag_to(shell, main, QPointF(2600.0, 600.0))
        # 前提：拖拽目标确实落在空洞里（没有任何屏覆盖它）
        assert not any(screen.availableGeometry().contains(2600, 600)
                       for screen in (a, diagonal)), "夹具前提：目标点是屏间空洞"
        target = QPointF(2600.0, 600.0)
        overlay.mouseReleaseEvent(_release(target - origin, target))
        _tick(shell)
        body = QRectF(main.body_rect()).translated(
            shell.overlay.geometry().topLeft())
        assert any(QRectF(screen.availableGeometry()).contains(body)
                   for screen in (a, diagonal)), (
            "松手后身体必须完整落在某块屏的可用区里——空洞不是可站立区")
        assert len(shell.hosts()) == 1
    finally:
        hosts_mod._cleanup(shell)


def test_neighbour_screens_on_all_four_sides_are_walkable(tmp_path, monkeypatch):
    """左右上下（含负坐标）的邻屏都在活动区里：身体整块过去就算在那块屏上。"""
    cases = [
        ("right", FakeScreen((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), name="R")),
        ("left", FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1040), name="L")),
        ("below", FakeScreen((0, 1080, 1920, 1080), (0, 1080, 1920, 1040), name="B")),
        ("above", FakeScreen((0, -1080, 1920, 1080), (0, -1080, 1920, 1040), name="T")),
    ]
    for name, neighbour in cases:
        a = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="A")
        _install(monkeypatch, [a, neighbour])
        shell = _shell(tmp_path / name, a)
        try:
            assert len(shell._main_host.activity) == 2, f"{name}: 两块屏都在活动区"
            main = shell.sprite
            main.set_pos(QPointF(600.0, 500.0))
            target = {
                "right": QPointF(2600.0, 500.0),
                "left": QPointF(-1300.0, 500.0),
                "below": QPointF(600.0, 1600.0),
                "above": QPointF(600.0, -600.0),
            }[name]
            overlay, origin, cursor = _grab(shell, main)
            overlay.mouseMoveEvent(_move(cursor - origin, cursor))
            _tick(shell)
            start = QPointF(cursor)
            for step in range(1, 61):
                cursor = start + (target - start) * (step / 60.0)
                overlay.mouseMoveEvent(_move(cursor - origin, cursor))
                _tick(shell)
            assert shell._screen_of_sprite(main) is neighbour, (
                f"{name}: 身体整块进入邻屏后就算在那块屏上")
            assert main.home_screen is neighbour
            overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        finally:
            hosts_mod._cleanup(shell)


def test_switch_off_drag_stays_inside_the_started_screen(tmp_path, monkeypatch):
    """开关关着：拖到缝另一侧也过不去（活动区只剩启动那块屏，且不多建窗口）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a, switch=False)
    try:
        main = shell.sprite
        main.set_pos(QPointF(300.0, 600.0))
        overlay, origin, cursor = _grab(shell, main)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        cursor = QPointF(origin.x() + 2600.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)

        assert len(shell.hosts()) == 1, "关着时不许因为拖拽多建窗"
        assert [screen for screen, _r in shell._main_host.activity] == [a], (
            "关闭跨屏 = 活动区只剩启动那块屏")
        assert shell._screen_of_sprite(main) is a
        body = main.body_rect()
        assert QRect(0, 0, 1920, 1040).contains(body), "身体必须仍完整留在启动那块屏里"
    finally:
        hosts_mod._cleanup(shell)


def test_drag_relaxes_the_clamp_domain_then_release_restores_the_activity_set(
        tmp_path, monkeypatch):
    """拖拽全程放宽成**整窗**，松手恢复活动区集合。

    严域（多矩形）在身体框跨缝时会被逐点判定钳回最近那块屏——实机表现是先撞墙、
    再突然瞬移过去，而不是顺着光标穿过去。放宽只对开关打开时生效（关闭时活动区本
    来就只剩家那块屏，整窗宽松域会把宠放出去）。
    """
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 500.0))
        expected = tuple(QRectF(rect) for _screen, rect in shell._main_host.activity)
        assert tuple(QRectF(r) for r in main._clamp_rects) == expected
        overlay, origin, cursor = _grab(shell, main)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        cursor = QPointF(origin.x() + 2200.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        assert shell._screen_of_sprite(main) is b, "身体整块进副屏"
        assert tuple(QRectF(r) for r in main._clamp_rects) == (
            QRectF(shell._main_host.bounds),), "拖动中放宽成整窗"
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        _tick(shell)
        assert tuple(QRectF(r) for r in main._clamp_rects) == expected, (
            "松手恢复多矩形活动区")
        assert shell._screen_of_sprite(main) is b
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 5. 松手 / 弹弓跨缝
def _drag_to_straddle(shell, sprite, *, extra=0.0):
    """拖到"身体正跨在缝上"的位置（左缘在 A、右缘在 B），返回 (overlay, origin, cursor)。

    ``extra`` = 在跨缝位置基础上再往右多拖的像素（0 = 正好卡在缝上）。
    """
    overlay, origin, cursor = _grab(shell, sprite)
    overlay.mouseMoveEvent(_move(cursor - origin, cursor))
    _tick(shell)
    body = sprite.body_rect()
    # 目标：身体左缘刚好落在缝左边半个身体宽的位置（身体横跨 1920）
    want_body_left = 1920.0 - body.width() / 2.0 + extra
    want_pos_x = want_body_left - (body.x() - sprite.rect().x())
    want_cursor_x = want_pos_x + sprite._drag_offset.x()
    cursor = QPointF(origin.x() + want_cursor_x, cursor.y())
    overlay.mouseMoveEvent(_move(cursor - origin, cursor))
    _tick(shell)
    return overlay, origin, cursor


def test_release_straddling_the_seam_neither_snaps_nor_closes_the_domain(
        tmp_path, monkeypatch):
    """松手时身体正跨在缝上：位置不许跳，域也不许当场收回（否则就是第二次 snap）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 500.0))
        overlay, origin, cursor = _drag_to_straddle(shell, main, extra=-20.0)
        host = shell.host_for_sprite(main)
        body = QRectF(main.body_rect()).translated(host.overlay.geometry().topLeft())
        assert body.left() < 1920.0 < body.left() + body.width(), (
            "前提：身体正跨在缝上（两边各有一部分）")
        assert getattr(main, "_clamp_rects", None) is not None, "拖拽中域是开的"

        before = _global(shell, main)
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        assert _global(shell, main) == before, "松手本身不许移动这只"
        assert getattr(main, "_clamp_rects", None) is not None, (
            "身体还在缝上 → 域必须留着（当场收回就是把跨缝的身体钳进一块屏 = 第二次 snap）")

        # 松手后跑几拍：这只仍完整可见/合法（不落在屏间空洞里），位置连续
        prev = _global(shell, main)
        for _ in range(10):
            _tick(shell)
            host = shell.host_for_sprite(main)
            body = QRectF(main.body_rect()).translated(host.overlay.geometry().topLeft())
            covered = any(
                QRectF(s.availableGeometry()).intersects(body)
                for s in (a, b))
            assert covered, "身体必须仍落在有显示器的地方"
            step = abs(_global(shell, main).x() - prev.x())
            assert step <= 40.0, f"松手后每拍位移 {step} 过大（有人把它钳回去了）"
            prev = _global(shell, main)
    finally:
        hosts_mod._cleanup(shell)


def test_release_inside_one_screen_does_not_move(tmp_path, monkeypatch):
    """身体整块落在某块屏内时松手：一个像素都不移动。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 500.0))
        overlay, origin, cursor = _grab(shell, main)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        cursor = QPointF(origin.x() + 1000.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        before = _global(shell, main)
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        _tick(shell)
        assert _global(shell, main) == before, "松手不许带来任何位移"
        assert shell._screen_of_sprite(main) is a
    finally:
        hosts_mod._cleanup(shell)


def test_slingshot_over_the_seam_keeps_the_open_domain(tmp_path, monkeypatch):
    """跨缝时进瞄准 / 发射 / 取消：域保持开着，位移只来自弹弓自己的物理。"""
    from tests.test_overlay_hosts_multiscreen import _right_press

    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a, {"drag_physics": True})
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 500.0))
        assert shell.move_sprite_to_screen(main, b, global_pos=QPointF(2000.0, 500.0)) is True
        overlay_b = shell.host_for_sprite(main).overlay
        origin_b = QPointF(overlay_b.geometry().topLeft())
        _hittable(main)
        hit = QPointF(main.rect().center())
        overlay_b.mousePressEvent(_press(hit, origin_b + hit))
        _tick(shell)
        cursor = origin_b + hit + QPointF(30.0, 0.0)
        overlay_b.mouseMoveEvent(_move(cursor - origin_b, cursor))
        _tick(shell)
        assert getattr(main, "_clamp_rects", None) is not None, "拖拽中域是开的"

        # 进瞄准：位置定锚不动、域保持开着
        anchor = _global(shell, main)
        overlay_b.mousePressEvent(_right_press(cursor - origin_b, cursor))
        assert shell._slingshot.aiming is True
        assert _global(shell, main) == anchor, "进瞄准不许移动这只"
        assert getattr(main, "_clamp_rects", None) is not None, "瞄准期间域不许被收"

        # 再点右键取消（就地恢复拖拽）：位置不跳，域仍开着
        overlay_b.mousePressEvent(_right_press(cursor - origin_b, cursor))
        assert shell._slingshot.aiming is False
        assert _global(shell, main) == anchor, "取消瞄准不许移动这只"
        assert getattr(main, "_clamp_rects", None) is not None

        # 松手：位置不跳，域按"身体是否已合法"决定去留
        before = _global(shell, main)
        overlay_b.mouseReleaseEvent(_release(cursor - origin_b, cursor))
        assert _global(shell, main) == before, "松手不许移动这只"
    finally:
        hosts_mod._cleanup(shell)


def test_straddling_pet_is_not_hauled_back_by_the_host_controllers(tmp_path, monkeypatch):
    """跨缝松手后，本屏的行为/物理控制器不许把它硬拽回本屏（第二次 snap 的另一半）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 500.0))
        overlay, origin, cursor = _drag_to_straddle(shell, main, extra=-20.0)
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        host = shell.host_for_sprite(main)
        body = QRectF(main.body_rect()).translated(host.overlay.geometry().topLeft())
        assert body.left() < 1920.0, "前提：身体仍有一半在原屏"
        # 行为控制器（每 tick 都对正常态 sprite 调 _clamp_into_bounds）不许在这里钳
        for _ in range(20):
            _tick(shell)
        host = shell.host_for_sprite(main)
        body = QRectF(main.body_rect()).translated(host.overlay.geometry().topLeft())
        assert min(abs(body.left() - 1920.0), abs(body.left() + body.width() - 1920.0)) < (
            body.width()), "这只仍应停在缝附近（被拽回本屏就是硬钳）"
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 6. 跨缝只读投影
def _composite(shell, screens):
    """把各窗的内容拼成一张"全局桌面"图（每窗按自己原点贴到对应位置）。

    投影正确时，拼出来的图应当与"同一块大屏、同一位置"的参考图**逐像素一致**
    （同 DPR、同素材、同位置；差异只可能来自拼接本身）。
    """
    from PySide6.QtGui import QImage, QPainter

    left = min(s.geometry().x() for s in screens)
    top = min(s.geometry().y() for s in screens)
    right = max(s.geometry().x() + s.geometry().width() for s in screens)
    bottom = max(s.geometry().y() + s.geometry().height() for s in screens)
    canvas = QImage(right - left, bottom - top, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    try:
        for host in shell.hosts():
            geo = host.overlay.geometry()
            pix = host.overlay.grab()
            painter.drawImage(geo.x() - left, geo.y() - top, pix.toImage())
    finally:
        painter.end()
    return canvas


def _body_pixels(canvas, body_global, alpha_min=128):
    """全局身体框内 alpha≥阈值 的像素占比（判断"这只在图上画出来了多少"）。"""
    hits = 0
    total = 0
    for x in range(int(body_global.left()), int(body_global.left() + body_global.width()), 4):
        for y in range(int(body_global.top()), int(body_global.top() + body_global.height()), 4):
            total += 1
            if canvas.pixelColor(x, y).alpha() >= alpha_min:
                hits += 1
    return hits / max(1, total)


def test_straddling_pet_is_fully_visible_in_the_single_window(tmp_path, monkeypatch):
    """身体跨缝时：唯一合成窗上这只**完整可见**（不再需要邻窗投影）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 400.0))
        overlay, origin, cursor = _drag_to_straddle(shell, main)
        body = QRectF(main.body_rect()).translated(overlay.geometry().topLeft())
        assert body.left() < 1920.0 < body.left() + body.width(), "前提：身体跨在缝上"

        overlay.grab()   # offscreen 下 grab 触发一次 paintEvent
        canvas = _composite(shell, [a, b])
        covered = _body_pixels(canvas, body)
        assert covered > 0.6, (
            f"跨缝的身体在合成图上只画出了 {covered:.0%}——单窗应当整只画出来")
        assert shell.hosts() == [shell._main_host], "单窗：没有第二个窗口要投影"
    finally:
        hosts_mod._cleanup(shell)


def test_crossing_leaves_no_extra_window_or_projection_state(tmp_path, monkeypatch):
    """跨屏全程只有一张窗、没有投影簿记（旧机制整体消失）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 400.0))
        overlay, origin, cursor = _drag_to_straddle(shell, main)
        assert shell.hosts() == [shell._main_host]
        # 投影机制回来了（混合缩放每屏一窗时给相邻窗画另一半），但**单窗模式**
        # 一张窗就能画整只，不该登记任何投影。
        assert overlay._projections == {}, "单窗模式不产生跨窗投影"
        assert shell.host_for_screen(b) is shell._main_host, "副屏也在同一张窗里"

        cursor = QPointF(origin.x() + 2400.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        assert shell._screen_of_sprite(main) is b, "身体整块进 B"
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        _tick(shell)
        assert shell.hosts() == [shell._main_host], "过渡结束也不回收/新建窗口"
    finally:
        hosts_mod._cleanup(shell)


def test_switch_off_never_creates_a_window(tmp_path, monkeypatch):
    """关闭跨屏：拖拽全程只有一张窗（默认态与旧行为逐位相同）。"""
    a, b = _two_screens()
    _install(monkeypatch, [a, b])
    shell = _shell(tmp_path, a, switch=False)
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 400.0))
        overlay, origin, cursor = _grab(shell, main)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        cursor = QPointF(origin.x() + 2600.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
        assert len(shell.hosts()) == 1, "关闭态不许建窗"
        assert shell.hosts() == [shell._main_host]
    finally:
        hosts_mod._cleanup(shell)


