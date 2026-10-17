# -*- coding: utf-8 -*-
"""一张合成窗铺满整个虚拟桌面（issue #302 / #303）。

旧拓扑：每块有宠的屏各一张合成窗，窗口矩形取 ``screen.geometry()``（**含任务栏**），
跨界靠"缝上通道"移交 sprite、再让邻窗画只读投影。两个用户实测缺陷：

- #302：窗口铺满屏几何 ⇒ Wallpaper Engine / TrafficMonitor 这类按窗口矩形自判全屏
  的软件把桌宠当全屏应用（壁纸暂停、悬浮窗隐藏）；
- #303：跨屏开着也拖不过去——副屏宿主窗从不创建，身体被钳在 x=0。

本文件钉住新契约（真实 QScreen 鸭子替身 + 真 OverlayShell + 真 PetSprite）：

1. 单屏：窗口高度 = **可用工作区**高度（不是屏几何高度）——#302 的修法；
2. 多屏：``hosts()`` 恒为 1，窗口 = 各屏可用区的并集包围矩形；
3. 活动区（可站立域）= 每屏可用区一块矩形；关跨屏时只剩家那块；
4. 错位拼接的空洞挡得住（宠不会站进没有显示器的地方）；
5. 副屏的宠能拖到主屏、主屏的宠能拖到副屏（不再被钳在缝上）——#303；
6. tick 刷新率取全部屏里最高的一档（本机主屏 170Hz，不是副屏 60Hz）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF

import tests.test_overlay_hosts_multiscreen as hosts_mod
from tests.test_overlay_hosts_multiscreen import (
    _global_of, _hittable, _install_screens, _make_shell, _move, _press, _release)

TICK_S = 1 / 60.0


def _tick(shell):
    shell.driver.tick_sim(TICK_S)
    shell.driver.advance_overlays(TICK_S)


def _drag(shell, sprite, dx_steps):
    """在唯一合成窗里按住 sprite 逐拍拖 ``dx_steps``（每步 40px），返回光标局部坐标。"""
    overlay = shell.overlay
    _hittable(sprite)
    cursor = QPointF(sprite.rect().center())
    overlay.mousePressEvent(_press(cursor, QPointF(
        overlay.geometry().topLeft()) + cursor))
    for _ in range(dx_steps):
        cursor = QPointF(cursor.x() + 40.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor, QPointF(
            overlay.geometry().topLeft()) + cursor))
        _tick(shell)
    overlay.mouseReleaseEvent(_release(cursor, QPointF(
        overlay.geometry().topLeft()) + cursor))
    return cursor


def _body_global(shell, sprite) -> QRectF:
    return QRectF(sprite.body_rect()).translated(
        shell.overlay.geometry().topLeft())


# ---------------------------------------------------------------- #302：任务栏不进窗口
def test_single_screen_window_stops_above_the_taskbar(tmp_path, monkeypatch):
    """单屏：窗口 = 可用工作区（1440 高的屏、任务栏 48 → 窗口 1392 高）。

    旧实现在这里给的是 ``screen.geometry()``（1440）——那正是 WE/TM 把桌宠判成
    全屏应用的原因。
    """
    screen = hosts_mod.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    _install_screens(monkeypatch, screen)
    shell = _make_shell(tmp_path, screen, {"allow_cross_screen": False})
    try:
        geometry = screen.geometry()
        available = screen.availableGeometry()
        assert shell.overlay.geometry() == QRect(available)
        assert shell.overlay.geometry().height() != geometry.height(), (
            "窗口高度必须小于屏高（任务栏那条不在窗内）")
        assert shell._bounds == QRect(0, 0, available.width(), available.height())
        assert len(shell.hosts()) == 1
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 多屏：单窗 + 并集
def test_multi_screen_uses_one_window_sized_to_the_available_union(tmp_path, monkeypatch):
    a, b = hosts_mod._two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        assert len(shell.hosts()) == 1, "全进程只有一张合成窗"
        assert shell.overlay.geometry() == QRect(0, 0, 3840, 1040)
        assert shell._bounds == QRect(0, 0, 3840, 1040)
        assert [screen for screen, _rect in shell._main_host.activity] == [a, b]
        assert shell._main_host.activity[0][1] == QRect(0, 0, 1920, 1040)
        assert shell._main_host.activity[1][1] == QRect(1920, 0, 1920, 1040)
    finally:
        hosts_mod._cleanup(shell)


def test_activity_rects_shrink_to_the_home_screen_when_the_switch_is_off(
        tmp_path, monkeypatch):
    a, b = hosts_mod._two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": False})
    try:
        assert shell.overlay.geometry() == QRect(0, 0, 3840, 1040), (
            "窗仍铺满整个虚拟桌面（不因为开关关着就缩回一块屏）")
        assert [screen for screen, _rect in shell._main_host.activity] == [a], (
            "关跨屏 = 活动区只剩家那块屏")
        assert shell._main_host.activity[0][1] == QRect(0, 0, 1920, 1040)
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 空洞不是可站立区
def test_staggered_layout_never_lets_a_pet_stand_in_the_gap(tmp_path, monkeypatch):
    a = hosts_mod.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="A")
    # 右侧屏往下错开 500px：包围矩形里 x∈(1920,3840), y∈(0,500) 一带没有显示器
    b = hosts_mod.FakeScreen((1920, 500, 1920, 1080), (1920, 500, 1920, 1040),
                             name="B")
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        assert shell.overlay.geometry() == QRect(0, 0, 3840, 1540), "并集包围矩形"
        pet = shell.sprite
        body_rect = pet.body_rect()
        requested = QRectF(2400.0, 100.0, body_rect.width(), body_rect.height())
        assert not (QRectF(a.availableGeometry()).contains(requested)
                    or QRectF(b.availableGeometry()).contains(requested)), (
            "夹具前提：请求的位置确实落在空洞里")

        pet.set_pos(QPointF(2400.0, 100.0))       # 直接点进空洞

        body = _body_global(shell, pet)
        assert any(QRectF(screen.availableGeometry()).contains(body)
                   for screen in (a, b)), (
            "空洞里的位置必须被挡回某块屏的可用区（不是没有显示器的地方）")
        assert body != requested, "位置确实被挡回（不是原样接受）"
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- #303：真的能拖过去
def test_drag_from_the_main_screen_reaches_the_left_secondary(tmp_path, monkeypatch):
    """主屏上的宠往左拖过缝：落到副屏上（旧实现里它被钳在 x=0）。"""
    right = hosts_mod.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040),
                                 name="MAIN")
    left = hosts_mod.FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1040),
                                name="LEFT")
    _install_screens(monkeypatch, right, left)
    shell = _make_shell(tmp_path, right)
    try:
        pet = shell.sprite
        pet.set_pos(QPointF(1000.0, 600.0))       # 主屏（局部 1920 起是主屏）
        _drag(shell, pet, dx_steps=-24)           # 往左拖 960px，越过缝

        assert shell._screen_of_sprite(pet) is left, "身体过缝就算在副屏上"
        body = _body_global(shell, pet)
        assert QRectF(left.availableGeometry()).contains(body), (
            "整只落在副屏可用区内（不是被钳在缝上）")
        assert body.center().x() < 0, "身体中心已在副屏（全局负坐标）"
        assert len(shell.hosts()) == 1, "拖过去不新建窗口"
    finally:
        hosts_mod._cleanup(shell)


def test_drag_from_the_secondary_reaches_the_main_screen(tmp_path, monkeypatch):
    """副屏上的宠往右拖过缝：回到主屏上（双向都通）。"""
    right = hosts_mod.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040),
                                 name="MAIN")
    left = hosts_mod.FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1040),
                                name="LEFT")
    _install_screens(monkeypatch, right, left)
    shell = _make_shell(tmp_path, right)
    try:
        pet = shell.sprite
        assert shell.move_sprite_to_screen(pet, left) is True
        assert shell._screen_of_sprite(pet) is left
        _drag(shell, pet, dx_steps=24)

        assert shell._screen_of_sprite(pet) is right, "身体过缝就算回主屏"
        assert QRectF(right.availableGeometry()).contains(_body_global(shell, pet))
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 刷新率取全屏最大
def test_tick_interval_follows_the_fastest_screen(tmp_path, monkeypatch):
    """主屏 170Hz + 副屏 60Hz：tick 间隔按最快那块算（6ms，不是 16ms）。"""
    fast = hosts_mod.FakeScreen((0, 0, 2560, 1440), (0, 0, 2560, 1392),
                                refresh=170.0, name="FAST")
    slow = hosts_mod.FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1032),
                                refresh=60.0, name="SLOW")
    _install_screens(monkeypatch, fast, slow)
    shell = _make_shell(tmp_path, fast)
    try:
        rate = shell.driver._refresh_rate()
        assert rate == pytest.approx(170.0)
        assert shell.driver.tick_interval_ms(rate) == 6
    finally:
        hosts_mod._cleanup(shell)


# ---------------------------------------------------------------- 拖拽域 / 探头域
def _stacked_screens():
    """主屏 2560×1440(avail 1392) + 左侧副屏 1920×1080(avail 1032)。

    副屏比主屏矮：``x∈[-1920,0], y∈[1032,1392)`` 是**没有显示器**的空洞。
    """
    main = hosts_mod.FakeScreen((0, 0, 2560, 1440), (0, 0, 2560, 1392), name="MAIN")
    left = hosts_mod.FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1032), name="LEFT")
    return main, left


def test_drag_relaxes_the_clamp_domain_to_the_whole_window(tmp_path, monkeypatch):
    """跨缝拖拽期间放宽成整窗，松手恢复多矩形活动区。

    严域（多矩形）下身体框跨到错位屏的空洞时会被逐点判定钳回最近那块屏——实测
    表现是先像撞墙、再突然瞬移过去，而不是顺着光标穿过去。
    """
    main, left = _stacked_screens()
    _install_screens(monkeypatch, main, left)
    shell = _make_shell(tmp_path, main)
    try:
        sprite = shell.sprite
        _hittable(sprite)
        overlay = shell.overlay
        assert sprite._clamp_rects is not None and len(sprite._clamp_rects) == 2

        origin = QPointF(overlay.geometry().topLeft())
        cursor = QPointF(sprite.rect().center())
        overlay.mousePressEvent(_press(cursor, origin + cursor))
        cursor = QPointF(cursor.x() - 40.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor, origin + cursor))
        _tick(shell)

        assert len(sprite._clamp_rects) == 1, "拖动中应放宽成单个整窗矩形"

        overlay.mouseReleaseEvent(_release(cursor, origin + cursor))
        assert len(sprite._clamp_rects) == 2, "松手后恢复多矩形活动区"
    finally:
        hosts_mod._cleanup(shell)


def test_release_inside_the_dead_zone_lands_back_on_a_real_screen(tmp_path, monkeypatch):
    """松手点落在屏间空洞里时，收尾必须把身体钳回真正有显示器的屏。"""
    main, left = _stacked_screens()
    _install_screens(monkeypatch, main, left)
    shell = _make_shell(tmp_path, main)
    try:
        host = shell._main_host
        sprite = shell.sprite
        # 拖拽中的宽松域 + 落点：局部分量都在副屏带内，y 却超过副屏可用高度
        sprite.set_clamp_rects((QRectF(host.bounds),))
        sprite.set_pos(QPointF(600.0, 1200.0))
        assert sprite.pos.y() > left.availableGeometry().height(), "前提：此刻确实在空洞里"

        shell._drag_relaxed_sprite = sprite
        shell._restore_drag_domain()

        body = QRectF(sprite.body_rect())
        assert any(QRectF(rect).contains(body) for _screen, rect in host.activity), (
            "松手后身体必须完整落在某块真实屏上")
    finally:
        hosts_mod._cleanup(shell)


def test_probe_pose_keeps_its_relaxed_domain_under_multi_rects(tmp_path, monkeypatch):
    """探头姿态优先用放宽域：多矩形活动区不许把探头身体钳回屏内。

    实机表现：姿态照画、位置却在屏里，看起来像身子露在屏幕外/姿态错位。
    """
    main, left = _stacked_screens()
    _install_screens(monkeypatch, main, left)
    shell = _make_shell(tmp_path, main)
    try:
        sprite = shell.sprite
        assert sprite._clamp_rects is not None and len(sprite._clamp_rects) == 2

        sprite.set_probe_pose(0.0, 0.4)
        relaxed = sprite.probe_clamp_bounds()
        assert relaxed is not None, "前提：曝光 0.4 应进入放宽域"

        targets = sprite._clamp_targets()
        assert len(targets) == 1, "探头期间只该有一个（放宽后的）钳制矩形"
        assert targets[0] == relaxed

        # 放宽域真的允许身体站到窗口右缘之外（原 bounds 之内做不到）
        edge = QPointF(shell.overlay.geometry().width() - sprite.rect().width() / 2.0, 400.0)
        sprite.set_pos(edge)
        assert sprite.pos.x() > shell.overlay.geometry().width() - sprite.rect().width()
    finally:
        hosts_mod._cleanup(shell)



def test_probe_pose_target_keeps_part_of_the_body_on_screen(tmp_path, monkeypatch):
    """探头目标位置：露出约一半，不许整只身体出屏。

    露出量由 ``(1 - exposure) * 投影宽度`` 算，而投影（±45° 姿态）比身体宽——
    两处口径任一错位都会把整只推到没有显示器的地方（实机反馈：整只露在屏幕外）。
    """
    from pet.sprite_edge_probe import probe_target_x

    main, left = _stacked_screens()
    _install_screens(monkeypatch, main, left)
    shell = _make_shell(tmp_path, main, {"edge_probe_enabled": True})
    try:
        sprite = shell.sprite
        main_rect = hosts_mod._activity_rect(shell, main)
        # 贴主屏右缘（body_rect() 已是局部绝对矩形，按差量对齐一次）
        sprite.set_pos(QPointF(4200.0, 400.0))
        body = QRectF(sprite.body_rect())
        sprite.set_pos(QPointF(sprite.pos.x() + (main_rect.right() - body.right()), 400.0))

        sprite.set_probe_pose(45.0, 0.55)
        # probe_target_x 吃的是**相对 sprite 原点**的投影矩形（body_rect() 已含 pos）
        projected = QRectF(sprite.body_rect()).translated(-sprite.pos)
        target = probe_target_x("right", 0.55, projected, sprite.bounds)
        sprite.set_pos(QPointF(target, sprite.pos.y()))

        window = QRectF(shell.overlay.rect())
        out = QRectF(sprite.body_rect())
        assert out.intersects(window), "探头把整只身体推出了窗口（整只出屏）"
        assert out.right() > window.right(), "探头应当露出一部分身体"
        assert out.left() < window.right() - sprite.rect().width() / 4.0, (
            "露出量过大：留在屏内的部分不足一刻")
    finally:
        hosts_mod._cleanup(shell)
