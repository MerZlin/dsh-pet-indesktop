# -*- coding: utf-8 -*-
"""多屏独立落位：一屏一个宿主（一张 overlay + 一份屏局部仿真控制器）。

契约（2026-10-15 用户口径）：每只宠可**独立**放到任意显示器；仍然只有一个进程、
一个 tick 时钟、一套共享服务；被占用的每块屏各有一张 overlay。sprite 坐标是屏
局部坐标（= 全局 - 屏原点），故行为/碰撞/物理/探头逐屏一份——两块屏上局部坐标
相同的两只宠在桌面几何上相距上千像素，混进同一个世界就会被判成接触。

本机通常只有一块屏，故这里用**假双屏**驱动真实的宿主创建、跨屏移交与拖拽链路；
真实跨缝手感（半只宠被缝裁掉的短暂瞬间、异 DPI 观感）留多显示器实机验收。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite

app = QApplication.instance() or QApplication([])


class FakeScreen(QObject):
    """带名字的假屏：宿主按 ``name()`` 命中位置记忆，故必须实现它。"""

    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, geo, avail, *, dpr=1.0, refresh=60.0, name="A"):
        super().__init__()
        self._geo = QRect(*geo)
        self._avail = QRect(*avail)
        self._dpr = float(dpr)
        self._refresh = float(refresh)
        self._name = name

    def set_rects(self, geo, avail, *, dpr=None):
        self._geo = QRect(*geo)
        self._avail = QRect(*avail)
        if dpr is not None:
            self._dpr = float(dpr)

    def geometry(self):
        return QRect(self._geo)

    def availableGeometry(self):
        return QRect(self._avail)

    def devicePixelRatio(self):
        return self._dpr

    def refreshRate(self):
        return self._refresh

    def name(self):
        return self._name


def _two_screens():
    """左右并排的两块屏：A 原点 (0,0)，B 原点 (1920,0)。"""
    a = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="A")
    b = FakeScreen((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), name="B")
    return a, b


def _three_screens():
    """再并一块 C（原点 3840,0）：回收类用例需要"还有别的宿主"才走得下去。"""
    a, b = _two_screens()
    c = FakeScreen((3840, 0, 1920, 1080), (3840, 0, 1920, 1040), name="C")
    return a, b, c


def _global_of(host, sprite):
    """sprite 的全局左上角（窗口原点 + 局部坐标）。"""
    return QPointF(host.overlay.geometry().topLeft()) + QPointF(sprite.pos)


def _screen_of(shell, sprite):
    """这只宠当前所在的屏（产品按身体中心判定）。"""
    return shell._screen_of_sprite(sprite)


def _activity_rect(shell, screen) -> QRect:
    """该屏在活动区里的矩形（窗口局部坐标）。"""
    for candidate, rect in shell._main_host.activity:
        if candidate is screen:
            return QRect(rect)
    raise AssertionError("该屏不在活动区里")


def _ratio_in(shell, sprite, screen) -> tuple:
    """这只宠身体中心相对该屏可用区的比例（= 落盘口径）。"""
    body = sprite.body_rect()
    rect = _activity_rect(shell, screen)
    return ((body.x() + body.width() / 2.0 - rect.left()) / rect.width(),
            (body.y() + body.height() / 2.0 - rect.top()) / rect.height())


def _install_screens(monkeypatch, *screens):
    """屏枚举喂假屏：跨屏路由（``QGuiApplication.screens``）与位置记忆都读它。

    只换**类级** Qt API：屏枚举在产品里走 ``QGuiApplication.screens()``。不去动
    ``QApplication`` 实例上的同名属性——那个实例是全会话共用的，monkeypatch 对
    PySide6 实例的还原只会把记录到的值（此时已是类级补丁）重新塞成实例属性，
    泄漏到下一条用例。
    """
    monkeypatch.setattr(QGuiApplication, "screens",
                        staticmethod(lambda: list(screens)))
    monkeypatch.setattr(QGuiApplication, "primaryScreen",
                        staticmethod(lambda: screens[0]))


def _make_shell(tmp_path, screen, values=None):
    # 本文件测的就是"允许跨屏"下的多屏合同：开关是全局键、重启级（进程内快照），
    # 不在这台"机器"上显式开启的话，活动区只剩家那块屏，全部多屏断言都无从成立
    # （默认关 = 单显示器口径）。显式值优先，便于单条用例反向覆盖。
    values = {"allow_cross_screen": True, **(values or {})}
    config = cap.CapConfig(tmp_path, values)
    from pet import pet_profiles

    values_pet = {key: value for key, value in (values or {}).items()
                  if key in pet_profiles.PER_PET_KEYS}
    pet_profiles.write_profile_settings(tmp_path, 0, values_pet or {"scale": 0.5})
    shell = OverlayShell(
        app, cap.CapInstance(config), screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    lib = fac.RichLibrary()
    shell.lib = lib
    shell.sprite.library = lib
    shell._create_main_library = fac.RichLibrary
    shell.overlay.click_feedback = None  # 点击音效与多屏语义无关，先摘掉
    return shell


def _hittable(sprite):
    """让 sprite 的命中图就位（``sprite_at`` 逐像素判据要能命中）。"""
    sprite.bind_clip("idle1")
    sprite._rebuild_pixmap()


def _cleanup(shell):
    shell.stop()
    shell.overlay.close()
    shell._delete_runtime_marker()


def _press(local, global_pos):
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(local), QPointF(global_pos),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


def _move(local, global_pos):
    return QMouseEvent(
        QEvent.Type.MouseMove, QPointF(local), QPointF(global_pos),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


def _release(local, global_pos):
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(local), QPointF(global_pos),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier)


def _painted(overlay, canvas) -> bool:
    """在该 overlay 上真跑一次瞄准 UI 绘制，返回"是否画了内容"。

    画布由调用方持有：``QPainter`` 活过临时 ``QPixmap`` 会访问违例。
    """
    painter = QPainter(canvas)
    try:
        return bool(overlay._paint_slingshot(painter))
    finally:
        painter.end()


def _tick(shell, dt=1 / 60.0):
    """一个真 tick 的两段（推进段收尾才跑跨屏路由）。"""
    shell.driver.tick_sim(dt)
    shell.driver.advance_overlays(dt)


def _right_press(local, global_pos):
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(local), QPointF(global_pos),
        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier)


def _drag_across(shell, sprite, *, steps=16, step_px=40):
    """把 sprite 从 A 拖到 B（真鼠标事件序列 + 每步一个 tick）。

    跨屏移交由"全部 overlay advance 之后"的钩子按**这一拍的实际身体位置**判，
    所以每一步都要走一个真 tick（仿真段 + 推进段）——这正是窗口在拖拽期间一直在
    跑的那个时钟。

    返回 (落点全局坐标, A 窗局部坐标)；光标从按下点起每步向右 ``step_px``，默认
    共 640px——落点深入 B，抓取偏移不会被左缘钳制掩盖。
    """
    overlay_a = shell.host_for_sprite(sprite).overlay
    origin_a = QPointF(overlay_a.geometry().topLeft())
    _hittable(sprite)
    hit = QPointF(sprite.rect().center())
    overlay_a.mousePressEvent(_press(hit, origin_a + hit))
    cursor = origin_a + hit
    for _ in range(steps):
        cursor = QPointF(cursor.x() + step_px, cursor.y())
        overlay_a.mouseMoveEvent(_move(cursor - origin_a, cursor))
        shell.driver.tick_sim(1 / 60.0)
        shell.driver.advance_overlays(1 / 60.0)
    return cursor, cursor - origin_a


# ---------------------------------------------------------------- 宿主（公开面）
def test_host_for_sprite_exposes_screen_overlay_and_bounds(tmp_path, monkeypatch):
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        host = shell.host_for_sprite(shell.sprite)
        assert host.screen is a
        assert host.overlay is shell.overlay
        # 单窗：窗口 = 各屏**可用工作区**并集，局部坐标从 (0,0) 起
        assert host.bounds == shell._bounds == QRect(0, 0, 3840, 1040)
        assert [screen for screen, _rect in host.activity] == [a, b]
        assert shell.hosts() == [host]
    finally:
        _cleanup(shell)


def test_move_sprite_to_screen_moves_only_that_pet(tmp_path, monkeypatch):
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        main = shell.sprite
        _hittable(main)
        _hittable(child)
        main_pos = QPointF(main.pos)

        assert shell.move_sprite_to_screen(
            child, b, global_pos=QPointF(2100, 300)) is True

        assert _screen_of(shell, child) is b
        assert child in shell.overlay.sprites
        assert child.home_screen is b
        # 另一只一分不动、仍在它自己那块屏上
        assert main.pos == main_pos
        assert _screen_of(shell, main) is a
        # 给了 global_pos：落在该全局位置（局部坐标 = 全局 - 窗口原点）
        assert _global_of(shell.host_for_sprite(child), child) == QPointF(2100, 300)
        assert child.pos.x() == pytest.approx(2100.0, abs=1.0)
        # 不给 global_pos：按"当前屏内的相对位置"落到目标屏同一相对位置
        ratio_in_b = _ratio_in(shell, child, b)
        assert shell.move_sprite_to_screen(child, a) is True
        assert _screen_of(shell, child) is a
        assert _ratio_in(shell, child, a) == pytest.approx(ratio_in_b, abs=0.01)
        assert shell._screen is a
    finally:
        _cleanup(shell)


def test_two_pets_a_screen_apart_never_collide(tmp_path, monkeypatch):
    """相隔一整块屏的两只不撞；摆到同一处仍要撞（有牙对照）。

    单窗把全部宠放进同一个世界，局部坐标就是窗口全局坐标——要紧的是它们在同一套
    坐标里算距离，而不是像旧多窗拓扑那样按屏各算一份。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.move_sprite_to_screen(child, b)
        main = shell.sprite

        # 两只各自站在自己那块屏的同一屏内坐标上（真机相距 1920px）
        main.set_pos(QPointF(400, 300))
        child.set_pos(QPointF(400 + 1920, 300))
        assert shell.hosts() == [shell._main_host], "单窗：一个世界"

        events = []
        shell.collision.add_collision_listener(events.append)
        shell.collision._prev_circles = {}
        shell.driver.tick_sim(1 / 60.0)
        assert events == [], "相隔一整块屏不是接触"

        # 对照（有牙）：摆到同一处就必须结算（先清扫掠快照，别把瞬移当掠过）
        child.set_pos(QPointF(main.pos))
        events.clear()
        shell.collision._prev_circles = {}
        shell.collision.tick([main, child], 1 / 60.0)
        assert main.pos != QPointF(400, 300) or child.pos != QPointF(400, 300)
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 跨屏拖拽
def test_drag_across_screens_moves_only_the_dragged_pet(tmp_path, monkeypatch):
    """拖过缝：被拖的那只走到副屏，别的宠与窗口几何一分不动（#303 主症）。

    跨屏不再换窗口/换宿主：同一张合成窗里自由移动，没有"移交"这一拍，也就没有
    跨屏大跳与"身体被钳在 x=0"。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        main = shell.sprite
        _hittable(child)
        child.set_pos(QPointF(200, 700))
        assert shell.move_sprite_to_screen(
            child, b, global_pos=QPointF(3400, 200)) is True
        child_pos = QPointF(child.pos)
        overlay = shell.overlay
        geometry = overlay.geometry()

        cursor, _ = _drag_across(shell, main)

        assert _screen_of(shell, main) is b, "身体中心过缝即算在副屏上"
        assert main.home_screen is b
        assert child.pos == child_pos, "别的宠不被拖拽带动"
        assert _screen_of(shell, child) is b
        assert overlay.geometry() == geometry, "拖动的是绘制，不是窗口"
        assert overlay._mouse_grab is main, "会话握在同一张窗里"

        overlay.mouseReleaseEvent(_release(cursor, cursor))

        # 松手落在光标处（窗口局部坐标），且抓取偏移不变（宠没从光标下错位）
        expected_local = cursor - QPointF(overlay.geometry().topLeft())
        assert main.rect().center().x() == pytest.approx(
            expected_local.x(), abs=4.0)
        assert shell.hosts() == [shell._main_host]
    finally:
        _cleanup(shell)


def test_cross_screen_drag_keeps_release_velocity_sane(tmp_path, monkeypatch):
    """换屏不改手感：松手初速按真实手势估（跨屏这一跳不得被当成甩动）。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"drag_physics": True})
    try:
        main = shell.sprite
        overlay = shell.overlay
        origin = QPointF(overlay.geometry().topLeft())
        _hittable(main)
        hit = QPointF(main.rect().center())
        overlay.mousePressEvent(_press(hit, origin + hit))
        for step in range(1, 26):
            cursor = QPointF(1700 + 40 * step, 400)
            overlay.mouseMoveEvent(_move(cursor - origin, cursor))
            shell.driver.tick_sim(1 / 60.0)
            shell.driver.advance_overlays(1 / 60.0)
        assert _screen_of(shell, main) is b, "身体整块进入邻屏后就算在副屏上"
        # 轨迹样本同一套坐标：相邻样本差仍是每步 40px（没有被拼成跨屏的一跳）
        trail = main.drag_trail
        assert len(trail) >= 2
        deltas = [abs(trail[i + 1][1] - trail[i][1]) for i in range(len(trail) - 1)]
        # 首个差值含"按下点 → 第一帧光标"的固有位移，只看其后：每步都是 40px
        assert deltas[1:] == pytest.approx([40.0] * (len(deltas) - 1)), (
            f"轨迹样本帧内一致，实测 {deltas}")
        cursor = QPointF(1700 + 40 * 25, 400)
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        # 拖动物理开着：手没在收尾瞬间暴冲 → 松手不是"甩出去"的暴速
        speed = (main.velocity.x() ** 2 + main.velocity.y() ** 2) ** 0.5
        assert speed < 3000.0
    finally:
        _cleanup(shell)


def test_switching_screen_keeps_clip_and_driver_running(tmp_path, monkeypatch):
    """换屏不是退出：clip 不释放、成员不注销、共享驱动器照旧驱动这张窗。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        _hittable(child)
        shell.move_sprite_to_screen(child, b)
        clip = child._clip
        assert clip is not None
        closed: list = []
        child.close = lambda *a, **k: closed.append(1)
        removed: list = []
        shell.overlay.add_sprite_removed_listener(
            lambda sprite: removed.append(sprite))

        shell.move_sprite_to_screen(child, a)

        assert closed == [] and removed == []
        assert child._clip is clip
        assert shell.overlay in shell.driver._overlays
        assert _screen_of(shell, child) is a
    finally:
        _cleanup(shell)


def test_dpr_follows_the_target_screen(tmp_path, monkeypatch):
    """一张窗只有一个 DPR，但逐只按**它自己那块屏**重建 pixmap/命中图。"""
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        assert child.dpr == 1.0
        shell.move_sprite_to_screen(child, b)
        assert child.dpr == 2.0
        assert shell.sprite.dpr == 1.0, "另一只在主屏上，仍是 1.0"
    finally:
        _cleanup(shell)


def test_single_window_driver_membership_and_stop(tmp_path, monkeypatch):
    """单窗：驱动器成员恒为一个；停表/起表只经壳的显隐与收口入口。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        shell.start()  # 起表（进程级一个 QTimer）
        assert shell.driver.timer.isActive() is True
        assert list(shell.driver.overlays) == [shell.overlay]

        shell.stop()

        assert shell.driver.timer.isActive() is False, "整壳停表即停表"
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 屏事件
def test_primary_screen_change_does_not_move_pets(tmp_path, monkeypatch):
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.move_sprite_to_screen(child, b)
        main_pos, child_pos = QPointF(shell.sprite.pos), QPointF(child.pos)
        home = shell._screen

        shell.handle_primary_screen_changed(b)

        assert shell._screen is home, "主屏换人不换家"
        assert _screen_of(shell, shell.sprite) is a
        assert _screen_of(shell, child) is b
        assert shell.sprite.pos == main_pos
        assert child.pos == child_pos
    finally:
        _cleanup(shell)


def test_unplugging_one_screen_moves_only_its_pets(tmp_path, monkeypatch):
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        shell.spawn_pet()
        first, second = shell._spawned
        shell.move_sprite_to_screen(second, b)
        main = shell.sprite
        main_pos = QPointF(main.pos)
        first_pos = QPointF(first.pos)

        _install_screens(monkeypatch, a)
        shell.handle_screen_removed(b)

        assert shell.host_for_screen(b) is None, "拔掉的屏不再被窗覆盖"
        assert _screen_of(shell, second) is a
        assert second in shell.overlay.sprites
        # 别的屏上的宠一分不动
        assert main.pos == main_pos and first.pos == first_pos
    finally:
        _cleanup(shell)


def test_unplugging_the_home_screen_moves_home_to_a_survivor(tmp_path, monkeypatch):
    """拔掉家那块屏：家搬到存活屏，窗口仍是同一张（不重建），宠跟着走。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.move_sprite_to_screen(child, b)
        overlay = shell.overlay

        _install_screens(monkeypatch, b)
        shell.handle_screen_removed(a)

        assert shell.overlay is overlay, "单窗：拔屏不重建窗口"
        assert shell._screen is b
        assert shell.sprite in shell.overlay.sprites
        assert _screen_of(shell, shell.sprite) is b
        assert shell.sprite.home_screen is b
        assert shell.hosts() == [shell._main_host]
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 位置记忆（逐宠）
def test_profile_records_the_pets_own_screen(tmp_path, monkeypatch):
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        slot = shell._spawned_slots[child]
        shell.move_sprite_to_screen(child, b)
        child.set_pos(QPointF(2500, 500))     # 落在 B 里（归属与承载一致才写盘）

        shell.save_spawned_positions()

        from pet import pet_profiles
        geometry = dict(pet_profiles.read_slot_snapshot(tmp_path, slot).profile.data)
        assert geometry["screen_name"] == "B"
        rx, ry = _ratio_in(shell, child, b)
        assert geometry["rx"] == pytest.approx(rx, abs=0.01)
        assert geometry["ry"] == pytest.approx(ry, abs=0.01)
    finally:
        _cleanup(shell)


def test_restore_puts_each_pet_back_on_its_own_screen(tmp_path, monkeypatch):
    """重启复活：按各宠档案里的 ``screen_name`` 回到各自那块屏。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    from pet import pet_profiles

    # 复活路径同样只在"允许跨屏"下按各宠记忆的屏落位
    config = cap.CapConfig(tmp_path, {"allow_cross_screen": True})
    pet_profiles.write_profile_settings(tmp_path, 0, {"scale": 0.5})
    pet_profiles.write_profile_settings(tmp_path, 1, {"scale": 0.5})
    pet_profiles.save_active_set(tmp_path, [0, 1], on_broken="backup-overwrite")
    pet_profiles.write_profile_geometry(
        tmp_path, 1, {"rx": 0.25, "ry": 0.75, "screen_name": "B"})
    shell = OverlayShell(
        app, cap.CapInstance(config), screen=a,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    try:
        shell._create_main_library = fac.RichLibrary
        assert shell.sprite is not None
        child = shell._spawned[0]
        assert _screen_of(shell, child) is b
        rect = _activity_rect(shell, b)
        body = child.body_rect()
        cx = body.x() + body.width() / 2.0
        cy = body.y() + body.height() / 2.0
        assert cx == pytest.approx(rect.left() + 0.25 * rect.width(), abs=2.0)
        assert cy == pytest.approx(rect.top() + 0.75 * rect.height(), abs=2.0)
    finally:
        _cleanup(shell)


def test_main_pet_restored_to_its_saved_screen_re_sites_the_home_screen(
        tmp_path, monkeypatch):
    """主宠记着副屏：家（关闭跨屏时唯一活动区、拔屏兜底）就落在副屏。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    from pet import pet_profiles

    config = cap.CapConfig(tmp_path, {"allow_cross_screen": True})
    pet_profiles.write_profile_settings(tmp_path, 0, {"scale": 0.5})
    pet_profiles.write_profile_geometry(
        tmp_path, 0, {"rx": 0.5, "ry": 0.5, "screen_name": "B"})
    shell = OverlayShell(
        app, cap.CapInstance(config), screen=a,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    try:
        shell._create_main_library = fac.RichLibrary
        assert len(shell.hosts()) == 1, "单窗：主宠那块屏只是家，不多建窗口"
        host = shell.host_for_sprite(shell.sprite)
        assert host.screen is b
        assert shell.overlay is host.overlay
        assert shell._screen is b
        rect = _activity_rect(shell, b)
        body = shell.sprite.body_rect()
        assert body.x() + body.width() / 2.0 == pytest.approx(
            rect.left() + 0.5 * rect.width(), abs=2.0)
        assert body.y() + body.height() / 2.0 == pytest.approx(
            rect.top() + 0.5 * rect.height(), abs=2.0)
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 逐屏外围
def test_bubble_anchor_uses_the_window_origin(tmp_path, monkeypatch):
    """气泡锚点 = 身体框 + **窗口**原点（单窗一套坐标，副屏那只同样对）。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.move_sprite_to_screen(child, b)
        child.set_pos(QPointF(300, 400))

        follower = shell._bubble_for(child)
        assert follower is not None
        body = child.body_rect()
        origin = shell.overlay.geometry().topLeft()
        expected = QRect(origin + body.topLeft(), body.size())
        assert follower.anchor() == expected
        assert shell.bubble_anchor_rect_for(getattr(follower, "bubble", None)) == expected
    finally:
        _cleanup(shell)


def test_island_wall_binds_to_the_single_world(tmp_path, monkeypatch):
    """岛只有一座：墙挂在唯一合成窗的世界里（岛的局部坐标与全部宠同源）。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.move_sprite_to_screen(child, b)

        class _Island:
            def __init__(self, screen):
                self._screen = screen

            def _current_screen(self):
                return self._screen

        shell.attach_island(_Island(b))

        assert shell.island_bridge is not None
        assert shell._island_host is shell._main_host
        assert shell.island_bridge.core._world is shell.collision  # noqa: SLF001
        # 岛换到另一块屏：仍是同一个世界（不复制墙、不换宿主）
        bridge = shell.island_bridge
        shell.attach_island(_Island(a))
        assert shell._island_host is shell._main_host
        assert shell.island_bridge is not bridge
        assert shell.island_bridge.core._world is shell.collision  # noqa: SLF001
    finally:
        shell.attach_island(None)
        _cleanup(shell)


def test_island_wall_has_a_landing_on_every_screen(tmp_path, monkeypatch):
    """岛在没有宠的屏上也有落点：单窗铺满整个虚拟桌面，墙立刻挂上。

    旧拓扑要"等那块屏有宠（建出宿主）再挂墙"，因为局部坐标不同源、挂错等于在
    另一块屏上凭空多一堵墙；单窗下只有一套坐标，这条等待条件消失。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        class _Island:
            def __init__(self, screen):
                self._screen = screen

            def _current_screen(self):
                return self._screen

        shell.attach_island(_Island(b))

        assert shell.island_bridge is not None
        assert shell.island_bridge.attached is True
        assert shell._island_host is shell._main_host
    finally:
        shell.attach_island(None)
        _cleanup(shell)


# ---------------------------------------------------------------- 跨屏组合手势
def test_slingshot_gesture_after_crossing_uses_one_coordinate_system(
        tmp_path, monkeypatch):
    """跨屏拖到副屏后、**同一次 grab** 里点右键进弹弓：输入与预览同一坐标系。

    单窗下"跨屏"只是同一张窗内的位移，锚点、拉拽矢量、瞄准 UI 全部在同一套局部
    坐标里——这条用例钉住的就是"没有两套坐标可混"：拖过一整块屏之后手势仍然跟着
    光标走，不会因为跨屏甩出去。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        main = shell.sprite
        main.set_pos(QPointF(200.0, 700.0))
        overlay = shell.overlay
        origin = QPointF(overlay.geometry().topLeft())
        cursor, local = _drag_across(shell, main, steps=30, step_px=60)
        assert _screen_of(shell, main) is b, "前提：这只已经拖到副屏上"
        assert b.geometry().contains(cursor.toPoint()), "前提：光标确实落在副屏上"
        assert overlay._mouse_grab is main

        # 同一次 grab 里点右键 = 进瞄准（会话活在唯一控制器上）
        overlay.mousePressEvent(_right_press(local, cursor))
        slingshot = shell._slingshot
        assert slingshot.aiming is True
        assert slingshot.sprite is main
        assert slingshot.anchor == QPointF(main.pos), "锚点就是这只在窗口坐标里的位置"
        assert (slingshot.anchor_cursor - slingshot.anchor).manhattanLength() < (
            main.rect().width() + main.rect().height()), "锚点光标与锚点同系"

        # 光标向左挪 60px：拉拽矢量 = 60px（60 在弹弓拉拽上限之内）
        pull_cursor = cursor - QPointF(60.0, 0.0)
        overlay.mouseMoveEvent(_move(pull_cursor - origin, pull_cursor))
        assert slingshot.pull.x() == pytest.approx(60.0, abs=1.0)
        assert slingshot.pull.y() == pytest.approx(0.0, abs=1.0)

        # 瞄准 UI 由这张窗画，脏区也归它
        canvas = QPixmap(16, 16)   # 画布要活到 painter 结束（临时对象会崩）
        assert _painted(overlay, canvas) is True
        assert overlay._slingshot_dirty.isEmpty() is False

        # 再点右键 = 取消并就地恢复拖拽：抓取偏移按当前光标重建
        overlay.mousePressEvent(_right_press(pull_cursor - origin, pull_cursor))
        assert slingshot.aiming is False

        before = QPointF(main.pos)
        next_cursor = pull_cursor + QPointF(40.0, 0.0)
        overlay.mouseMoveEvent(_move(next_cursor - origin, next_cursor))
        assert (QPointF(main.pos) - before).x() == pytest.approx(40.0, abs=2.0), (
            "恢复拖拽后跟着光标走")

        overlay.mouseReleaseEvent(_release(next_cursor - origin, next_cursor))
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 跨屏与弹簧拖拽
def test_crossing_with_drag_physics_keeps_the_spring_lag(tmp_path, monkeypatch):
    """拖动物理开着时跨屏：逐拍连续、滞后保留，**不**瞬移到光标处。

    跨屏不产生任何"移交拍"（同一张窗），所以这条用例钉的是更基本的一件事：跨过
    屏边界的那些 tick 里，位移只能是弹簧自己的那一份，不能出现整块屏的跳变。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"drag_physics": True})
    try:
        main = shell.sprite
        main.set_pos(QPointF(600.0, 600.0))
        overlay = shell.overlay
        origin = QPointF(overlay.geometry().topLeft())
        _hittable(main)
        hit = QPointF(main.rect().center())
        overlay.mousePressEvent(_press(hit, origin + hit))
        cursor = origin + hit
        for _ in range(5):
            cursor = QPointF(cursor.x() + 40.0, cursor.y())
            overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        assert _screen_of(shell, main) is a
        assert (QPointF(main._drag_spring_target) - QPointF(main.pos)).x() > 100.0, (
            "前提：弹簧滞后已经攒出来（拖动确实落后于目标）")

        # 一大步跨过屏边界，然后逐拍推进：每一拍的位移都不许超过当时的剩余滞后
        cursor = QPointF(cursor.x() + 1400.0, cursor.y())
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        crossed = False
        for tick in range(1, 120):
            prev = QPointF(main.pos)
            lag = abs(QPointF(main._drag_spring_target).x() - main.pos.x())
            _tick(shell)
            step = abs(QPointF(main.pos).x() - prev.x())
            assert step <= lag + 4.0, (
                f"第 {tick} 拍位移 {step} 明显超过剩余滞后 {lag}（那是瞬移不是弹簧）")
            if _screen_of(shell, main) is b:
                crossed = True
                break
        assert crossed, "身体整块进副屏后就算在副屏上（同一张窗，无移交拍）"

        before_release = QPointF(main.pos)
        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        assert (QPointF(main.pos) - before_release).manhattanLength() < 2.0, (
            "松手不得把这只从当前位置挪到光标处")
        assert main.interaction_state in ("normal", "thrown")
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- runtime 标记监听
def test_main_pet_position_listener_tracks_it_across_screens(tmp_path, monkeypatch):
    """主宠移到副屏后：runtime 标记（设置进程避让用）照旧随位置更新。

    监听挂在唯一合成窗上，而这正是不再需要"逐屏重挂注册"的原因：宠在哪块屏上，
    窗口都是同一张。
    """
    from pet import slot_manager

    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        main = shell.sprite
        overlay = shell.overlay
        assert shell._on_main_sprite_moved in overlay._position_listeners.get(main, [])

        assert shell.move_sprite_to_screen(main, b) is True
        assert shell._on_main_sprite_moved in overlay._position_listeners.get(main, [])

        def marker_x():
            path = slot_manager.runtime_marker_path(
                shell._config.dir, shell._config.instance_id, versioned=True)
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload["x"]

        main.set_pos(QPointF(2100.0, 500.0))

        def drift_and_fanout(dx):
            """让这只真动起来并跑一次推进段：位置通知就在这一段扇出。"""
            shell._last_marker_write = 0.0
            main.set_velocity(QPointF(dx * 60.0, 0.0))
            shell.driver.advance_overlays(1 / 60.0)
            main.set_velocity(QPointF(0.0, 0.0))

        drift_and_fanout(200.0)
        first = marker_x()
        drift_and_fanout(400.0)
        second = marker_x()
        assert second - first == pytest.approx(400.0, abs=8.0), (
            "标记跟着这只在副屏上的位置走")
        assert _screen_of(shell, main) is b
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 拔屏时的在途会话
def test_screen_removed_without_any_fallback_screen_finishes_the_grab(
        tmp_path, monkeypatch):
    """拔屏且**没有**兜底屏可迁：在途会话仍要走完 grab 收尾协议。

    ``_fallback_screen`` 返回 None（这是唯一的活动屏）时换家是空操作——这条路径
    **没有**随后的 show() 收敛兜底（穿透修复报告 §三 记的正是它），grab 收尾的壳层
    通知因此是冲刷穿透待办的唯一时机：漏掉就 ``_converge_pending`` 与
    ``_auto_cursor_hidden`` 一起永久滞留（看得见点不到）。
    """
    a = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="only")
    _install_screens(monkeypatch, a)            # 只有这一块屏：拔了就没兜底
    shell = _make_shell(tmp_path, a, {"mouse_through": False})
    try:
        overlay = shell.overlay
        released: list = []
        shell.sprite.on_release = released.append
        shell._on_cursor_visibility_changed("HIDDEN")     # 自动穿透（生产置位路径）
        assert overlay.mouse_through is True
        overlay._mouse_grab = shell.sprite
        overlay._press_click_only = True
        shell._converge_auto_cursor_hidden()              # 收敛撞上 grab → 记待办
        assert shell._converge_pending is True

        shell.handle_screen_removed(a)

        assert shell.overlay is overlay                   # 没兜底屏 → 窗口不动
        assert released == [QPointF(shell.sprite.pos)]    # 会话只松一次手
        assert overlay._mouse_grab is None
        assert overlay._press_click_only is False
        assert shell._converge_pending is False           # 待办被补做
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
    finally:
        _cleanup(shell)


def test_screen_removed_finishes_the_inflight_session_once(tmp_path, monkeypatch):
    """拔屏只收一次会话：在途 grab 按协议收尾（恰好一次），别的宠一分不动。"""
    a, b, c = _three_screens()
    _install_screens(monkeypatch, a, b, c)
    shell = _make_shell(tmp_path, a)
    try:
        shell.spawn_pet()
        shell.spawn_pet()
        first, second = shell._spawned
        _hittable(first)
        _hittable(second)
        shell.move_sprite_to_screen(first, b)
        shell.move_sprite_to_screen(second, c)
        main = shell.sprite
        _drag_across(shell, main)                 # 按住 → 身体过缝（同一张窗）
        assert _screen_of(shell, main) is b
        overlay = shell.overlay
        assert overlay._mouse_grab is main
        released: list = []
        main.on_release = released.append
        pos_at_release = QPointF(main.pos)

        shell.handle_screen_removed(a)

        # 会话收尾一次（不重复 release）；收尾用的是松开那一刻的当前位置
        assert released == [pos_at_release]
        assert overlay._mouse_grab is None
        assert overlay._press_click_only is False
        assert overlay._drag_committed is False
        assert overlay._press_global is None
        assert shell.hosts() == [shell._main_host], "单窗：拔屏不换宿主"
        assert shell.host_for_screen(a) is None
        assert _screen_of(shell, first) is b
        assert _screen_of(shell, second) is c
    finally:
        _cleanup(shell)


