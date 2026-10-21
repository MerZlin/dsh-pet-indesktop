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
import logging

import pytest
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite

app = QApplication.instance() or QApplication([])

pytestmark = pytest.mark.usefixtures("non_macos_overlay_topology")


@pytest.mark.parametrize("cross_screen,count,expected", [
    (True, 2, 2), (False, 2, 1), (True, 1, 1),
])
def test_macos_unit_dpr_screens_use_separate_windows(
        tmp_path, monkeypatch, cross_screen, count, expected):
    import pet.overlay_shell as overlay_mod

    monkeypatch.setattr(overlay_mod, "_IS_MACOS", True, raising=False)
    main = FakeScreen((0, 0, 1920, 1080), (0, 30, 1920, 1010), name="MAIN")
    left = FakeScreen((-1920, 0, 1920, 1080), (-1920, 30, 1920, 1010), name="LEFT")
    _install_screens(monkeypatch, *(main, left)[:count])
    shell = _make_shell(tmp_path, main, {"allow_cross_screen": cross_screen})
    try:
        assert len(shell.hosts()) == expected
        if expected == 2:
            for screen in (main, left):
                assert shell.host_for_screen(screen).overlay.geometry() == screen.availableGeometry()
            for screen in (left, main):
                assert shell.move_sprite_to_screen(shell.sprite, screen)
                host = shell.host_for_sprite(shell.sprite)
                assert host.screen is screen
                body = shell.sprite.body_rect().translated(host.overlay.geometry().topLeft())
                assert QRectF(screen.availableGeometry()).contains(body)
            shell.sprite.set_pos(QPointF(150, 500))
            for screen, step in ((left, -40), (main, 40)):
                overlay = shell.host_for_sprite(shell.sprite).overlay
                cursor, local = _drag_across(shell, shell.sprite, step_px=step)
                overlay.mouseReleaseEvent(_release(local, cursor))
                assert shell.host_for_sprite(shell.sprite).screen is screen
                assert not shell.sprite.dragging
                assert all(shell.sprite not in h.overlay._projections for h in shell.hosts())
    finally:
        _cleanup(shell)


class FakeScreen(QObject):
    """带名字的假屏：宿主按 ``name()`` 命中位置记忆，故必须实现它。"""

    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)
    refreshRateChanged = Signal(float)

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

    def set_refresh(self, rate):
        """同分辨率下切刷新率（60↔180）：几何不变，只发 refreshRateChanged。"""
        self._refresh = float(rate)
        self.refreshRateChanged.emit(self._refresh)

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
        # 松手初速估算的 Δt 来自 time.monotonic（pet_sprite._clock 的注释就写明
        # 「测试替换为假钟即可确定性构造轨迹」）：负载机器上事件间隔被拉长，
        # 同一个 40px/步手势会被估成暴速（本机与 CI 都实测抖出 >3000）。
        # 假钟每读一次走 1/60s → 速度 = 40px/(1/60s) = 2400px/s，确定且符合意图。
        _t = [1000.0]

        def _fake_clock() -> float:
            _t[0] += 1 / 60.0
            return _t[0]

        monkeypatch.setattr(main, "_clock", _fake_clock)
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


def test_mixed_dpr_uses_one_window_per_screen(tmp_path, monkeypatch):
    """混合缩放 + 允许跨屏：**每块屏一张窗**，跨屏交给目标窗（#307 的正解）。

    一张窗只能有一个 ``devicePixelRatio``：横跨两种缩放时必有一侧按错的倍率渲染
    （实机：整只画到可见区之外）。每屏一窗后每张窗的 DPR 天然正确，跨屏 = 把这只
    连同位置交给目标窗（局部坐标经全局中转）。
    """
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        assert len(shell.hosts()) == 2, "混合缩放必须每屏一窗"
        assert shell.overlay.geometry() == QRect(a.availableGeometry())
        b_host = shell.host_for_screen(b)
        assert b_host is not None
        assert b_host.overlay.geometry() == QRect(b.availableGeometry())
        assert b_host.overlay is not shell.overlay

        child = shell._spawned[0] if shell._spawned else None
        if child is None:
            shell.spawn_pet()
            child = shell._spawned[0]
        assert shell.move_sprite_to_screen(child, b) is True
        assert shell.host_for_sprite(child) is b_host, "跨屏后归目标宿主"
        assert shell._screen_of_sprite(child) is b
        assert child.dpr == 2.0, "目标窗的 DPR 天然正确"
    finally:
        _cleanup(shell)


def test_mixed_dpr_without_cross_screen_stays_single_window(tmp_path, monkeypatch):
    """混合缩放但**关闭跨屏**：不必多开窗，仍单窗退化为家屏。"""
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": False})
    try:
        assert len(shell.hosts()) == 1
        assert shell.overlay.geometry() == QRect(a.availableGeometry()), (
            "不出家屏就只开一张窗，且退化为家屏可用区")
    finally:
        _cleanup(shell)


def test_mixed_dpr_multi_window_log_does_not_claim_cross_screen_unavailable(
        tmp_path, monkeypatch, caplog):
    """多窗真的生效时，日志不许再喊「跨屏本次不可用」。

    实机证据（125% 副屏 + 100% 主屏）：进程确实开了两张 overlay ——
    ``(-1920,0,1920,1020)`` 与 ``(0,0,2560,1392)``，但启动日志仍打
    「窗口退化为启动屏，跨屏本次不可用」：那条文案只判了 ``_mixed_dpr()``，
    没判是否真的走了多窗（``_multi_window()``）。用户看日志会以为功能没生效。
    """
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    with caplog.at_level(logging.INFO):
        shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "跨屏本次不可用" not in text, "多窗已生效，这句是假警报"
        assert "退化为启动屏" not in text
        assert "每块屏一张合成窗" in text, "要说清实际走的是哪种"
    finally:
        _cleanup(shell)


def test_mixed_dpr_single_window_log_names_the_degradation(
        tmp_path, monkeypatch, caplog):
    """混合缩放但没开跨屏：退化成一张窗，日志要如实说「退化为启动屏」。"""
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    with caplog.at_level(logging.INFO):
        shell = _make_shell(tmp_path, a, {"allow_cross_screen": False})
    try:
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "退化为启动屏" in text
        assert "每块屏一张合成窗" not in text, "没走多窗就不许说走了多窗"
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


# ---------------------------------------------------------------- 屏事件：刷新率
def _refresh_calls(monkeypatch, shell):
    """把"重算 tick 间隔"换成本地计数：接/摘线直接看调用次数，不看间隔推。"""
    calls = []
    monkeypatch.setattr(shell, "_refresh_tick_interval",
                        lambda: calls.append(1))
    return calls


def test_refresh_rate_change_on_a_non_home_screen_retargets_tick(
        tmp_path, monkeypatch):
    """非窗口归属屏改刷新率：统一 tick 跟着改。

    单合成窗铺满整个虚拟桌面，``_refresh_rate`` 取**全部**屏的最高档（v4.3.3
    口径）；失效通知若仍只挂在窗口自己那块屏上，改副屏刷新率就永远收不到通知，
    宠物按旧频率跑（升刷不动、降刷继续空转唤醒）。
    """
    a, b = _two_screens()  # 两块 60Hz
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        driver = shell.driver
        assert driver.timer.interval() == 16, "60Hz 起步"

        b.set_refresh(180.0)  # 副屏（非窗口归属屏）

        assert driver.timer.interval() == 6

        b.set_refresh(60.0)

        assert driver.timer.interval() == 16, "降回 60Hz 也要跟上"
    finally:
        _cleanup(shell)


def test_screen_removal_detaches_the_refresh_rate_signal(tmp_path, monkeypatch):
    """拔屏后刷新率信号必须摘掉；插回来只接一次（不泄漏、不重复接线）。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a)
    try:
        _install_screens(monkeypatch, a)
        shell.handle_screen_removed(b)
        calls = _refresh_calls(monkeypatch, shell)

        b.set_refresh(180.0)
        assert calls == [], "拔掉的屏不再触发重算"

        _install_screens(monkeypatch, a, b)
        shell.handle_screen_added(b)
        assert len(calls) == 1, "插屏后明确刷新一次（取数范围变了）"
        b.set_refresh(170.0)
        assert len(calls) == 2, "插回来只接一次（重复接线会翻倍）"
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


# ---------------------------------------------------------------- 多窗弹弓的坐标所有者
def _mixed_dpr_shell(tmp_path, monkeypatch):
    """混合缩放的双屏壳（副屏 DPR=2 → 每屏一窗）与其两块屏。"""
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    return _make_shell(tmp_path, a, {"allow_cross_screen": True}), a, b


def _aim_on_host(shell, sprite) -> None:
    """在 sprite **当前宿主窗**上按下左键、再点右键进瞄准（真鼠标事件）。"""
    host = shell.host_for_sprite(sprite)
    overlay = host.overlay
    _hittable(sprite)
    origin = QPointF(overlay.geometry().topLeft())
    hit = QPointF(sprite.rect().center())
    overlay.mousePressEvent(_press(hit, origin + hit))
    cursor = origin + hit + QPointF(10.0, 0.0)
    overlay.mousePressEvent(_right_press(cursor - origin, cursor))


def test_slingshot_ui_only_paints_on_the_owning_window(tmp_path, monkeypatch):
    """多窗：弹弓瞄准 UI **只由源窗**画，别家窗一笔不画（不再画幽灵弹弓）。

    会话是壳内共享的一份（``shell._slingshot``），但每张窗有自己的局部坐标系：
    副屏瞄准时按"本地坐标相同位置"重画，主窗上就会出现一条错位的幽灵橡皮带。
    改为会话显式记录 owner，非 owner 窗直接跳过这一段绘制。
    """
    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        assert shell.move_sprite_to_screen(main, b) is True
        host_b = shell.host_for_sprite(main)
        assert host_b.screen is b, "前提：这只在副屏的窗里"
        overlay_b = host_b.overlay
        overlay_a = shell.host_for_screen(a).overlay
        assert overlay_a is not overlay_b

        _aim_on_host(shell, main)

        slingshot = shell._slingshot
        assert slingshot.aiming is True
        canvas = QPixmap(16, 16)     # 画布要活到 painter 结束（临时对象会崩）
        assert _painted(overlay_b, canvas) is True, "源窗照常画橡皮带 + 轨迹"
        assert _painted(overlay_a, canvas) is False, "非源窗不得画弹弓（幽灵）"
        assert slingshot.owner is overlay_b, "会话必须记下接输入的那张窗"
    finally:
        _cleanup(shell)


def test_global_escape_cancels_the_owning_window_session(tmp_path, monkeypatch):
    """多窗：全局 Esc 必须路由到 owner——源窗的 grab/press 状态同时被收掉。

    共享会话却固定调主窗 ``_cancel_slingshot`` 时，副屏瞄准按下 Esc 只会取消会话
    本身：副屏的 ``_mouse_grab``/``_press_global`` 仍在，随后松手会走普通点击分支。
    """
    shell, _a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        assert shell.move_sprite_to_screen(main, b) is True
        overlay_b = shell.host_for_sprite(main).overlay
        _aim_on_host(shell, main)
        assert shell._slingshot.aiming is True
        assert overlay_b._mouse_grab is main

        shell.eventFilter(shell.overlay, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier))

        assert shell._slingshot.aiming is False
        assert overlay_b._mouse_grab is None, "源窗的 grab 必须一起收"
        assert overlay_b._press_global is None
        assert overlay_b._press_click_only is False
        assert main.interaction_state == "normal"
        assert shell._slingshot.owner is None
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 多窗几何变化
def test_multi_window_geometry_change_keeps_ratio_and_bubble_origin(
        tmp_path, monkeypatch):
    """多窗：某屏可用区变化后宠按比例落位，气泡跟随器的原点跟着换。

    多窗分支此前只 setGeometry/set_bounds/set_activity 就返回：宠留在旧绝对位置
    （分辨率一变比例就走样），气泡跟随器仍缓存在旧原点（屏一平移气泡就留在旧
    全局坐标上）。这条用例钉住"每张窗都走完整事务"。
    """
    shell, a, _b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        sprite = shell.sprite
        host = shell.host_for_sprite(sprite)
        assert host.screen is a
        old_bounds = QRect(host.bounds)
        rect = sprite.rect()
        sprite.set_pos(QPointF(old_bounds.x() + 0.3 * old_bounds.width()
                               - rect.width() / 2.0,
                               old_bounds.y() + 0.4 * old_bounds.height()
                               - rect.height() / 2.0))
        body = sprite.body_rect()
        ratio = ((body.x() + body.width() / 2.0 - old_bounds.x()) / old_bounds.width(),
                 (body.y() + body.height() / 2.0 - old_bounds.y()) / old_bounds.height())
        follower = shell._bubble_follower
        assert follower._origin == QPoint(0, 0)
        dpr_refreshes = []
        monkeypatch.setattr(shell, "_refresh_self_talk_image_cache_for_dpr",
                            lambda: dpr_refreshes.append(1))

        # 屏整体平移 + 可用区变窄变矮（分辨率/任务栏同时变）
        a.set_rects((120, 80, 1920, 1080), (120, 80, 1600, 900))
        a.availableGeometryChanged.emit(a.availableGeometry())

        new_bounds = QRect(shell.host_for_sprite(sprite).bounds)
        assert new_bounds == QRect(0, 0, 1600, 900)
        body = sprite.body_rect()
        got = ((body.x() + body.width() / 2.0 - new_bounds.x()) / new_bounds.width(),
               (body.y() + body.height() / 2.0 - new_bounds.y()) / new_bounds.height())
        assert got[0] == pytest.approx(ratio[0], abs=0.02), "身体中心比例不保"
        assert got[1] == pytest.approx(ratio[1], abs=0.02)
        assert follower._origin == QPoint(120, 80), "气泡跟随器原点必须跟着窗换"
        assert dpr_refreshes, "配图 DPR 缓存同一次事务里刷新"
    finally:
        _cleanup(shell)


def test_multi_window_geometry_change_finishes_inflight_drag(tmp_path, monkeypatch):
    """多窗：拖拽中发生几何变化 → 先把在途会话收尾，再按比例重新落位。

    拖拽期间这只的钳制域被放宽到整张虚拟桌面、别的窗还挂着带缩放的只读投影；
    换域之后这套东西全是按旧几何算的（指向窗外的空洞），所以几何变化必须先走
    ``_end_drag`` 收尾，再按比例落进新域。
    """
    shell, a, _b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        overlay = shell.host_for_sprite(main).overlay
        _hittable(main)
        origin = QPointF(overlay.geometry().topLeft())
        hit = QPointF(main.rect().center())
        overlay.mousePressEvent(_press(hit, origin + hit))
        cursor = origin + hit + QPointF(120.0, 0.0)     # 过 DRAG_THRESHOLD
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        assert overlay._drag_committed is True, "前提：已升级为真拖拽"
        assert shell._drag_relaxed_sprite is main, "前提：拖拽域已放宽"

        a.set_rects((0, 0, 1920, 1080), (0, 0, 1600, 900))
        a.availableGeometryChanged.emit(a.availableGeometry())

        assert overlay._mouse_grab is None, "几何变化必须先收尾在途拖拽"
        assert overlay._press_global is None
        assert overlay._press_click_only is False
        assert shell._drag_relaxed_sprite is None, "放宽域必须收回"
        new_bounds = QRect(shell.host_for_sprite(main).bounds)
        assert new_bounds == QRect(0, 0, 1600, 900)
        body = main.body_rect()
        assert new_bounds.contains(QRect(body)), "收尾后必须落在新可用区内"
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


# ---------------------------------------------------------------- 交付前审查的三条（4.3.4 收尾）
class _RecordingFollower:
    """气泡跟随器替身：只记 rebind / set_origin 调用。"""

    def __init__(self):
        self.rebinds = []
        self.origins = []

    def rebind(self, overlay, origin=None):
        self.rebinds.append((overlay, origin))

    def set_origin(self, origin):
        self.origins.append(origin)


def test_main_pet_bubble_follower_rebinds_to_the_target_window(tmp_path, monkeypatch):
    """主宠跨屏后，它的气泡跟随器必须改挂到**目标窗**。

    ``remove_sprite`` 会摘掉该 sprite 的全部位置监听，跟随器又缓存着旧窗原点：
    只调 ``set_origin`` 不会重新注册，气泡从此不跟宠走。主宠的跟随器不在子宠表
    ``_bubble_followers`` 里，取错表就等于整段静默跳过（实机"气泡漂到另一块屏"）。
    """
    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        follower = _RecordingFollower()
        shell._bubble_follower = follower
        assert shell._bubble_for(main) is follower, "前提：主宠取的是壳的主跟随器"

        assert shell.move_sprite_to_screen(main, b) is True

        host_b = shell.host_for_screen(b)
        assert follower.rebinds, "换窗必须重绑，不能只 update 原点"
        overlay, _origin = follower.rebinds[-1]
        assert overlay is host_b.overlay, "要挂到目标窗，不是原来那张"
    finally:
        _cleanup(shell)


def test_main_pet_runtime_listener_follows_the_target_window(tmp_path, monkeypatch):
    """主宠跨屏后 runtime 标记的位置监听要挂在**它现在所在**那张窗上。"""
    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        assert shell.move_sprite_to_screen(main, b) is True
        host_b = shell.host_for_screen(b)
        host_a = shell.host_for_screen(a)
        assert main in host_b.overlay._position_listeners
        assert main not in host_a.overlay._position_listeners
    finally:
        _cleanup(shell)


def test_runtime_dpr_change_flips_topology_and_reconciles_hosts(tmp_path, monkeypatch):
    """运行中改缩放会翻转「单窗 ↔ 每屏一窗」，宿主集合必须跟着重排。

    同 DPI 双屏启动只有一张窗；把副屏改成 200% 后 ``_multi_window()`` 变真，若只
    喂 DPR 不重排宿主，副屏没有窗、那边的宠被按主宿主重新落位。反向（改回同 DPI）
    则必须拆掉多出来的宿主，否则它挂着成员而主窗又按整桌面铺开。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        assert len(shell.hosts()) == 1, "前提：同 DPI 只有一张窗"

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
        shell._feed_screen_dpr()
        assert len(shell.hosts()) == 2, "缩放变成混合 DPI 后必须补出副屏的窗"
        assert shell.host_for_screen(b) is not None

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)
        shell._feed_screen_dpr()
        assert len(shell.hosts()) == 1, "缩放改回一致后必须拆掉多出来的宿主"
        assert shell.host_for_screen(b) is shell._main_host, (
            "单窗下任何在连的屏都由唯一那张窗承载")
    finally:
        _cleanup(shell)


def test_drag_domain_covers_the_physical_extent_of_a_high_dpi_screen(
        tmp_path, monkeypatch):
    """拖拽放宽域按**物理**外接矩形算，别按逻辑桌面（高 DPI 屏会被算小）。

    副屏 1920 物理 @200% 在 Qt 里只有 960 逻辑宽。按逻辑求并集时源窗局部右缘只到
    1920+960=2880，而那块屏的物理右缘是 1920+1920=3840 —— 宠拖到中段就跟不上
    鼠标（鼠标还在动，位置被域钳住）。
    """
    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        host_a = shell.host_for_screen(a)
        domain = shell._drag_domain_local(host_a)
        assert domain is not None
        assert domain.right() >= 1920 + 1920 - 1, (
            "放宽域必须覆盖副屏的物理右缘（3840），而不是逻辑右缘（2880）")
        assert domain.left() <= 0
    finally:
        _cleanup(shell)


def test_drag_projection_keeps_logical_size_across_mixed_dpi(tmp_path, monkeypatch):
    """跨混合 DPI 拖拽时投影按**逻辑尺寸**画：松手前后宠一样大。

    按物理像素对齐（``scale = d_src/d_dst``）设备采样比更接近 1:1，但会让这只在
    目标屏上先大 1.5 倍、松手瞬间缩回原生尺寸——4K@150% → 1080p@100% 的用户直接
    报成「拖过去缩放不对/很怪」。宠的逻辑尺寸跨屏不变，投影照逻辑尺寸画。
    """
    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        host_a = shell.host_for_screen(a)
        host_b = shell.host_for_screen(b)
        assert host_a is not host_b
        monkeypatch.setattr(host_a.overlay, "devicePixelRatioF", lambda: 1.5)
        monkeypatch.setattr(host_b.overlay, "devicePixelRatioF", lambda: 1.0)
        monkeypatch.setattr(shell, "_window_physical_origin", lambda overlay: QPoint(0, 0))

        shell._sync_drag_projection(main)

        entry = host_b.overlay._projections.get(main)
        assert entry is not None, "目标窗必须挂上这只的投影"
        _offset, scale, _last = entry
        assert scale == pytest.approx(1.0), "投影按逻辑尺寸画，不按物理像素放大"

        before = main.rect().width()
        projected = host_b.overlay.projection_rect(main).width()
        assert projected == before, "投影宽度 = 宠的逻辑宽度（松手后一样大）"
    finally:
        _cleanup(shell)


def test_same_nonunit_dpr_still_uses_one_window_per_screen(tmp_path, monkeypatch):
    """两块屏缩放**相同但不是 1:1**（双 4K@150% 就是这么配的）：同样每屏一窗。

    Qt 6.12 在 Windows 上对负原点屏报「原生原点 + 逻辑尺寸」的混合坐标系。
    ``_mixed_dpr()`` 在两块屏 DPR 相同时为假，于是单窗把两块屏的矩形放进同一
    坐标空间 → 屏间多出一段无显示器的空洞、屏外侧反而没边界，宠被钳在半块屏里
    或走出可见区（issue #310）。每屏一窗时那个坏坐标根本不参与运算。
    """
    a, b = _two_screens()
    a.set_rects((0, 0, 2560, 1440), (0, 0, 2560, 1392), dpr=1.5)
    b.set_rects((2560, 0, 2560, 1440), (2560, 0, 2560, 1392), dpr=1.5)
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        assert shell._mixed_dpr() is False, "前提：两屏 DPR 相同"
        assert shell._scaled_multi_screen() is True
        assert len(shell.hosts()) == 2, "非 1:1 缩放的机器必须每屏一窗"
        assert shell.host_for_screen(b) is not None
    finally:
        _cleanup(shell)


def test_same_nonunit_dpr_without_cross_screen_stays_on_home_screen(
        tmp_path, monkeypatch):
    """关闭跨屏时退回**启动那块屏**的单窗，绝不铺满那张坏坐标系的虚拟桌面。"""
    a, b = _two_screens()
    a.set_rects((0, 0, 2560, 1440), (0, 0, 2560, 1392), dpr=1.5)
    b.set_rects((-2560, 0, 2560, 1440), (-2560, 0, 2560, 1392), dpr=1.5)
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": False})
    try:
        assert len(shell.hosts()) == 1
        assert shell.overlay.geometry() == QRect(a.availableGeometry()), (
            "退化到启动屏，而不是两屏并集")
    finally:
        _cleanup(shell)


def test_unit_dpr_multi_screen_keeps_the_single_window(tmp_path, monkeypatch):
    """全 1:1 的多屏仍是单窗：Qt 的原点就是原生原点，没有那个坐标系问题。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        assert shell._scaled_multi_screen() is False
        assert len(shell.hosts()) == 1, "1:1 的多屏零变化"
    finally:
        _cleanup(shell)


def test_runtime_host_creation_inherits_window_caps(tmp_path, monkeypatch):
    """运行期补出来的宿主也要吃到窗口级配置（置顶/锁位/SHIFT 门/不透明度）。

    漏下发的话那张窗停在出厂默认：用户关了置顶它照样盖在最上面，锁定位在副屏
    不生效——这正是 `_apply_host_window_caps` docstring 里写的"运行期新建的屏也走
    这一处"，而实际只有整组刷新会调它。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True,
                                      "lock_position": True,
                                      "shift_drag": True})
    try:
        assert len(shell.hosts()) == 1, "前提：同 DPI 只有一张窗"

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
        shell._feed_screen_dpr()

        created = shell.host_for_screen(b)
        assert created is not None
        assert created.overlay.lock_position is True, "锁定位要下发到新窗"
        assert created.overlay.shift_drag_required is True, "SHIFT 门要下发到新窗"
    finally:
        _cleanup(shell)


def test_topology_flip_keeps_the_group_hidden(tmp_path, monkeypatch):
    """整组隐藏中翻出新的屏，不能顺手把宠全放出来（全屏避让会被打穿）。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        shell._hide_all_hosts()

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
        shell._feed_screen_dpr()

        created = shell.host_for_screen(b)
        assert created is not None
        assert created.overlay.isVisible() is False, "隐藏状态必须被继承"
    finally:
        _cleanup(shell)


def test_collapsing_back_to_single_window_keeps_pets_on_their_screen(
        tmp_path, monkeypatch):
    """缩放改回一致后合窗，副屏的宠要留在副屏（不能被并到主屏）。

    `_dismantle_host` 是拔屏用的"临时并入主屏"算法；拿它当合窗用会把宠永久搬到
    主屏——可那块屏还在线，用户只是改了缩放。
    """
    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        main = shell.sprite
        assert shell.move_sprite_to_screen(main, b) is True
        assert shell._screen_of_sprite(main) is b

        # 两侧缩放改成一致 → 拓扑翻回单窗
        a.set_rects((0, 0, 1920, 1080), (0, 0, 1920, 1040), dpr=1.0)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)
        shell._feed_screen_dpr()

        assert len(shell.hosts()) == 1, "前提：已合回单窗"
        body = main.body_rect()
        center = QPointF(body.x() + body.width() / 2.0, body.y() + body.height() / 2.0)
        assert shell._screen_at_global(QPointF(
            shell.overlay.geometry().topLeft()) + center) is b, (
            "合窗后这只必须还在副屏那块屏的范围内")
    finally:
        _cleanup(shell)


def test_topology_flip_rebinds_the_island_wall(tmp_path, monkeypatch):
    """宿主集合重排后岛墙要重绑（岛自己没有几何事件，不会自己发现宿主变了）。"""
    from tests.test_island_multiscreen_hosts import ISLAND_MEMBER, _Island

    shell, a, b = _mixed_dpr_shell(tmp_path, monkeypatch)
    try:
        host_b = shell.host_for_screen(b)
        island = _Island(b, QRect(2300, 100, 400, 80))
        shell.attach_island(island)
        assert shell._island_host is host_b, "前提：墙绑在副屏宿主"

        a.set_rects((0, 0, 1920, 1080), (0, 0, 1920, 1040), dpr=1.0)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)
        shell._feed_screen_dpr()

        assert shell._island_host is shell._main_host, "重排后墙必须重绑到存活宿主"
        assert ISLAND_MEMBER in shell.collision._static_members  # noqa: SLF001
    finally:
        shell.attach_island(None)
        _cleanup(shell)


def test_split_into_per_screen_windows_keeps_pets_in_place(tmp_path, monkeypatch):
    """单窗拆成每屏一窗：**留在主窗的那只不许被按整桌面比例缩着挪**。

    拆窗时只回位"被搬走的"那几只是不够的：留在主宿主的宠会被几何事务用**旧**主宿主
    的整桌面 bounds 量比例，再落进新主屏的单屏 bounds。两块 1920 屏、宠在主屏正中
    （整桌面 x=960）时，翻转后会落到 x≈480。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        desktop = QRect(shell.overlay.geometry())
        assert desktop.width() == 3840, "前提：单窗铺满两张 1920 屏"
        # 摆到主屏正中（= 整桌面的 25%）
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, desktop.height()))
        body = main.body_rect()
        before = body.x() + body.width() / 2.0
        assert before == pytest.approx(960.0, abs=2.0), "前提：身体中心在主屏正中"

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
        shell._feed_screen_dpr()

        assert len(shell.hosts()) == 2, "前提：已拆成每屏一窗"
        assert shell._screen_of_sprite(main) is a
        body = main.body_rect()
        after = body.x() + body.width() / 2.0
        assert after == pytest.approx(960.0, abs=3.0), (
            f"主屏那只必须留在主屏正中（原 {before:.0f} → 现 {after:.0f}）")
    finally:
        _cleanup(shell)


def test_hotplugged_screen_host_inherits_caps_and_visibility(tmp_path, monkeypatch):
    """热插一块屏补出来的宿主：caps 与显隐同样要继承（与缩放翻转同一口径）。"""
    a, b = _two_screens()
    b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=2.0)
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True,
                                      "lock_position": True,
                                      "shift_drag": True})
    try:
        c = FakeScreen((3840, 0, 1920, 1080), (3840, 0, 1920, 1040), name="C")
        _install_screens(monkeypatch, a, b, c)
        shell._hide_all_hosts()

        shell.handle_screen_added(c)

        created = shell.host_for_screen(c)
        assert created is not None, "前提：新屏补出了宿主"
        assert created.overlay.lock_position is True, "锁定位要下发到热插的窗"
        assert created.overlay.shift_drag_required is True, "SHIFT 门要下发"
        assert created.overlay.isVisible() is False, "隐藏状态必须被继承"
    finally:
        _cleanup(shell)


def test_scale_change_that_also_shrinks_the_work_area_keeps_the_screen_ratio(
        tmp_path, monkeypatch):
    """缩放变化**同时把逻辑工作区变小**时，比例要按旧尺寸定标。

    缩放信号到达那一刻 ``availableGeometry()`` 已经是新尺寸（1920 → 960），而宠的
    坐标还在旧窗里。拿新宽高除旧坐标会把"副屏正中"量成 1.0，回位时把它扔到屏边。
    所以比例定标必须读"上次已应用"的那份矩形，不是现算。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        # 把它摆到副屏正中并记下归属（等价于"用户把它拖过去、松手已完成"）
        shell._place_body_ratio(main, 0.75, 0.5, QRect(shell.overlay.geometry()))
        shell._remember_sprite_screen(main, b)
        body = main.body_rect()
        assert body.x() + body.width() / 2.0 == pytest.approx(2880.0, abs=3.0), (
            "前提：身体中心在副屏正中（整桌面 3840 的 75%）")

        # 副屏改成 200%：逻辑工作区同时减半，且与主屏不再一致 → 拆窗
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        host_b = shell.host_for_screen(b)
        assert host_b is not None and len(shell.hosts()) == 2, "前提：已拆成每屏一窗"
        rect = shell._screen_rect_in_host(b, host_b)
        body = main.body_rect()
        ratio = (body.x() + body.width() / 2.0 - rect.x()) / rect.width()
        assert ratio == pytest.approx(0.5, abs=0.06), (
            f"必须留在副屏正中（比例 0.5），实际 {ratio:.2f}")
    finally:
        _cleanup(shell)


def test_geometry_signal_before_the_dpi_flip_keeps_positions(tmp_path, monkeypatch):
    """**几何信号先到、DPI 信号后到**时位置同样要保住（真实缩放的常见次序）。

    `availableGeometryChanged` 直接调 `_sync_host_geometry()`，而它原先按
    `_multi_window()` 选分支——此时判定已翻转、宿主集合还是旧的一张窗，于是整桌面
    bounds 被当成单屏比例分母（主屏正中 960 → 480），并把活动区一起覆盖；随后到达
    的 DPI 重排只能读到这个已损坏的状态，救不回原比例。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, 1040))
        body = main.body_rect()
        assert body.x() + body.width() / 2.0 == pytest.approx(960.0, abs=2.0), (
            "前提：身体中心在主屏正中")

        # 副屏改 200%：工作区同时减半。几何信号先到，DPI 信号后到
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell.handle_geometry_changed()
        shell._feed_screen_dpr()

        assert len(shell.hosts()) == 2, "前提：最终拆成每屏一窗"
        assert shell._screen_of_sprite(main) is a
        body = main.body_rect()
        assert body.x() + body.width() / 2.0 == pytest.approx(960.0, abs=3.0), (
            "主屏那只必须仍在主屏正中")
    finally:
        _cleanup(shell)


def test_three_screens_flip_both_ways_and_keep_every_pet_in_place(tmp_path, monkeypatch):
    """三屏、只改其中一块的缩放：两个方向都要保住每只的屏归属与屏内比例。"""
    a, b, c = _three_screens()
    _install_screens(monkeypatch, a, b, c)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        desktop = QRect(shell.overlay.geometry())
        assert desktop.width() == 5760, "前提：三块 1920 屏共用一张窗"
        main = shell.sprite
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.spawn_pet()
        third = shell._spawned[1]
        # 三只分别摆在 A / B / C 的中间（整桌面 5760 的 1/6、1/2、5/6）
        shell._place_body_ratio(main, 1 / 6, 0.5, desktop)
        shell._place_body_ratio(child, 0.5, 0.5, desktop)
        shell._place_body_ratio(third, 5 / 6, 0.5, desktop)
        shell._remember_sprite_screen(main, a)
        shell._remember_sprite_screen(child, b)
        shell._remember_sprite_screen(third, c)
        wants = ((main, a), (child, b), (third, c))

        def ratio_in_screen(sprite, screen) -> float:
            host = shell.host_for_screen(screen)
            assert host is not None
            rect = shell._screen_rect_in_host(screen, host)
            body = sprite.body_rect()
            return (body.x() + body.width() / 2.0 - rect.x()) / rect.width()

        # 只有 B 改成 200%（工作区同时减半）→ 每屏一窗
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()
        assert len(shell.hosts()) == 3, "三块屏都该有自己的窗"
        for sprite, screen in wants:
            assert shell._screen_of_sprite(sprite) is screen, "屏归属不许变"
            assert ratio_in_screen(sprite, screen) == pytest.approx(0.5, abs=0.08), (
                f"{screen.name()} 上的这只必须仍在屏内中央附近")

        # B 改回 100% → 合回单窗：三只各自回屏，**比例也要回来**
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)
        shell._feed_screen_dpr()
        assert len(shell.hosts()) == 1, "回到单窗"
        for sprite, screen in wants:
            assert shell._screen_of_sprite(sprite) is screen
            assert ratio_in_screen(sprite, screen) == pytest.approx(0.5, abs=0.08), (
                f"合窗后 {screen.name()} 上这只的比例也要保住")
    finally:
        _cleanup(shell)


def test_repeated_topology_notifications_are_idempotent(tmp_path, monkeypatch):
    """重复/交错通知不许反复拆建宿主，也不许把位置越推越远。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, 1040))

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        for _ in range(3):
            shell.handle_geometry_changed()
            shell._feed_screen_dpr()
            shell.handle_geometry_changed()

        assert len(shell.hosts()) == 2, "重复通知不许越拆越多"
        body = main.body_rect()
        center = body.x() + body.width() / 2.0
        assert center == pytest.approx(960.0, abs=3.0), "位置也不许被推走"
    finally:
        _cleanup(shell)


def test_signal_arriving_during_the_topology_transaction_is_reapplied(
        tmp_path, monkeypatch):
    """事务进行中又收到一次缩放信号：那个请求不许被闸门静默吞掉。

    真实机器上几何/DPI 信号可以交错到达；事务期间的重入会被闸门挡下（否则会在一半
    状态上再开一层）。挡下之后必须在收口时补做，否则最终拓扑和判定不一致——例如
    "拆到一半时缩放又改回去了"，宿主就永远多留一张。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        original = shell._adopt_new_host
        state = {"armed": True}

        def adopt(screen, *, visible):
            original(screen, visible=visible)
            if state["armed"]:
                state["armed"] = False
                # 事务进行中：副屏缩放又改回去了（信号交错）
                b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)
                shell._feed_screen_dpr()

        monkeypatch.setattr(shell, "_adopt_new_host", adopt)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        assert shell._topology_txn is False, "事务收口必须复位闸门"
        assert shell._topology_needs_reconcile() is False, "最终拓扑必须与判定一致"
        assert len(shell.hosts()) == 1, "缩放改回去了，多出来的宿主必须被拆掉"
    finally:
        _cleanup(shell)


def test_duplicate_screen_names_prefer_the_screen_the_pet_stands_on(
        tmp_path, monkeypatch):
    """两块屏**同名**时，归属判定要选"这只脚下那块"，不是宿主那屏。

    同名屏在实践中很少但可能（驱动重装、虚拟显示器）：单窗下宿主屏是"家那块"，
    若一律用宿主屏作为优先项，站在同名另一块屏上的宠会被算成在主屏，
    拓扑翻转时比例按错的那块量、位置跳到别的屏。
    """
    from tests.test_overlay_hosts_multiscreen import FakeScreen

    a = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="SAME")
    b = FakeScreen((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), name="SAME")
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        # 站到"同名的那块"（B）上，并让归属也写成同名
        shell._place_body_ratio(main, 0.75, 0.5, QRect(shell.overlay.geometry()))
        shell._remember_sprite_screen(main, b)

        assert shell._assigned_screen(main) is b, "同名时要选脚下那块，而不是宿主那屏"

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()
        assert shell._screen_of_sprite(main) is b, "翻转后仍在该屏"
    finally:
        _cleanup(shell)


def test_three_screens_converge_when_one_host_cannot_be_created(
        tmp_path, monkeypatch, caplog):
    """某块屏的宿主**建不起来**时：不许空转、不许吞掉，留日志等下次信号。

    上一版这里有两个缺陷：`_reconcile_hosts_for_dpi` 的早退还用"数量 > 1"判据
    （三屏只有两张宿主时直接 return，连几何同步都被跳过）；补偿用递归，每层都
    重新拿到预算，遇到持续翻转能一路递归下去。现在是有界循环 + 集合判据 + 日志。
    """
    import logging as _logging

    a, b, c = _three_screens()
    _install_screens(monkeypatch, a, b, c)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        original = shell._create_host

        def flaky(screen):
            if screen is c:
                raise RuntimeError("这张宿主演不出来")
            return original(screen)

        monkeypatch.setattr(shell, "_create_host", flaky)
        c.set_rects((3840, 0, 1920, 1080), (3840, 0, 960, 520), dpr=2.0)
        with caplog.at_level(_logging.WARNING):
            for _ in range(3):        # 连续信号：不许递归、不许越拆越多
                shell._feed_screen_dpr()

        assert shell._topology_txn is False, "闸门必须复位"
        assert len(shell.hosts()) <= 3
        assert any("屏拓扑未能对齐" in record.getMessage() for record in caplog.records), (
            "收敛不了要留日志，不能静默")
    finally:
        _cleanup(shell)


def test_consecutive_reentries_do_not_recurse(tmp_path, monkeypatch):
    """事务里**连续多次**翻转：补偿循环有界，不会层层递归。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        original = shell._adopt_new_host
        flips = {"left": 6}

        def adopt(screen, *, visible):
            original(screen, visible=visible)
            if flips["left"] > 0:
                flips["left"] -= 1
                # 反复在"多窗/单窗"之间横跳，逼补偿循环
                dpr = 2.0 if flips["left"] % 2 == 0 else 1.0
                b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=dpr)
                shell._feed_screen_dpr()

        monkeypatch.setattr(shell, "_adopt_new_host", adopt)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()      # 不许 RecursionError

        assert shell._topology_txn is False
        assert isinstance(shell.hosts(), list)
    finally:
        _cleanup(shell)


def test_failed_host_creation_is_rolled_back_and_retried(tmp_path, monkeypatch, caplog):
    """建窗**登记之后**失败：半成品宿主必须撤干净，不能被当成"该屏已补齐"。

    `_create_host` 先把宿主登记进 `_hosts`，之后接线/能力下发/显示任一步失败都会留下
    "登记了但没初始化好"的窗；完整性判据只认"该屏有没有宿主"，不清掉就再也不重试，
    回位时还会把宠搬进一张没 show 过的窗。
    """
    import logging as _logging

    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        original_caps = shell._apply_host_window_caps
        state = {"fail": True}

        def flaky_caps(host):
            if state["fail"] and host.screen is b:
                raise RuntimeError("能力下发失败")
            return original_caps(host)

        monkeypatch.setattr(shell, "_apply_host_window_caps", flaky_caps)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        with caplog.at_level(_logging.WARNING):
            shell._feed_screen_dpr()

        assert shell._topology_txn is False, "闸门必须复位"
        # 单窗形态下 host_for_screen 对任何在线屏都返回主宿主（那是既有语义），
        # 所以这里看的是"有没有一张**属于 b** 的宿主"。
        assert not any(host.screen is b for host in shell.hosts()), "半成品宿主不许留在表里"
        assert shell._active_multi() is False, "降级后已应用形态必须回到单窗"
        assert any("建宿主窗失败" in record.getMessage() or
                   "屏拓扑未能对齐" in record.getMessage()
                   for record in caplog.records), "失败要留日志"

        # 恢复后只发一次普通信号就要能补齐
        state["fail"] = False
        shell._feed_screen_dpr()
        assert shell.host_for_screen(b) is not None, "恢复后必须能补上"
    finally:
        _cleanup(shell)


def test_negative_origin_screen_survives_a_failed_flip(tmp_path, monkeypatch):
    """家屏在右、副屏在左侧负原点：翻转失败时几何与活动区不能混用两套坐标系。"""
    from tests.test_overlay_hosts_multiscreen import FakeScreen

    right = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="RIGHT")
    left = FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1040), name="LEFT")
    _install_screens(monkeypatch, right, left)
    shell = _make_shell(tmp_path, right, {"allow_cross_screen": True})
    try:
        original = shell._apply_host_window_caps
        monkeypatch.setattr(
            shell, "_apply_host_window_caps",
            lambda host: (_ for _ in ()).throw(RuntimeError("失败")) if host.screen is left
            else original(host))
        main = shell.sprite
        desktop = QRect(shell.overlay.geometry())
        # 家屏在右：整桌面 3840 宽里它占 1920..3840，所以 0.75 才是它自身的中线
        shell._place_body_ratio(main, 0.75, 0.5, desktop)
        before = main.body_rect().center()

        left.set_rects((-1920, 0, 1920, 1080), (-1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        assert len(shell.hosts()) == 1, "补齐失败就还只有一张窗"
        host = shell._main_host
        # 活动区必须是**单窗口径**：家屏(右)在窗局部坐标里应当右移，而不是 (0,0)
        rects = dict(host.activity)
        home_rect = rects.get(right)
        assert home_rect is not None
        assert home_rect.left() > 0, "家屏在右，活动区必须按单窗口径右移（不能混用多窗口径）"
        # 这里是**降级态**：判定要多窗但补窗失败，实际只有一张窗。要断言的是
        # "坐标口径一致、宠没被丢出去"，不是精确比例——这台机器上 LEFT 的逻辑
        # 原点本身是 Qt 的已知问题（#310），单窗降级态下比例本就会偏。
        window = QRect(shell.overlay.geometry())
        local = QRect(0, 0, window.width(), window.height())   # 精灵坐标是窗口局部
        body = main.body_rect()
        assert local.contains(body.center()), "宠必须还在窗里（没被丢到屏外）"
        # 活动区必须整体落在窗口内：混用两套坐标系时会出现 (0,0) 起的矩形
        for _screen, rect in host.activity:
            assert local.contains(QRect(rect)), (
                "活动区必须按单窗口径平移，不许出现多窗口径的 (0,0)")
    finally:
        _cleanup(shell)


def test_pass_budget_exhausts_without_recursing_then_recovers(tmp_path, monkeypatch,
                                                              caplog):
    """每一轮都真有推进但永远收敛不了：有界循环必须停下并留 pending/日志，
    之后**只发几何信号**也要能接住。
    """
    import logging as _logging

    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        original = shell._adopt_new_host
        flips = {"left": 12}

        def adopt(screen, *, visible):
            original(screen, visible=visible)
            if flips["left"] > 0:
                flips["left"] -= 1
                # 每轮都翻一次：一定还有"工作"要做
                dpr = 2.0 if flips["left"] % 2 == 0 else 1.0
                b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920 if dpr == 1.0 else 960,
                                                   1040 if dpr == 1.0 else 520), dpr=dpr)
                shell._feed_screen_dpr()

        monkeypatch.setattr(shell, "_adopt_new_host", adopt)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        with caplog.at_level(_logging.WARNING):
            shell._feed_screen_dpr()

        assert shell._topology_txn is False
        assert flips["left"] > 0, "有界循环应当提前停下（不是把 12 轮全跑完）"

        # 停掉翻转后只发几何信号：补偿入口也要接住
        flips["left"] = 0
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)
        shell.handle_geometry_changed()
        shell.handle_geometry_changed()
        assert shell._topology_needs_reconcile() is False or len(shell.hosts()) == 1
    finally:
        _cleanup(shell)


def test_degraded_state_survives_the_window_own_geometry_callback(
        tmp_path, monkeypatch):
    """降级态下窗口**自己订阅的**屏事件不许再把窗口缩回家屏。

    `overlay._desktop_rect()` 走注入的 `_host_window_rect(screen)`，而它原先只看
    `_multi_window()`（目标形态）；补窗失败时目标是多窗、实际一张窗，于是一次刷新率/
    几何事件就会把窗口从整桌面缩回家屏，而活动区仍是整桌面窗口的局部坐标。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        original = shell._apply_host_window_caps
        monkeypatch.setattr(
            shell, "_apply_host_window_caps",
            lambda host: (_ for _ in ()).throw(RuntimeError("失败")) if host.screen is b
            else original(host))
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()
        assert shell._active_multi() is False, "前提：已降级为单窗"
        union = QRect(shell.overlay.geometry())

        # 窗口自己订阅的路径：屏刷新率/几何变化
        shell.overlay._on_screen_geometry_changed()

        assert QRect(shell.overlay.geometry()) == union, (
            "降级态下窗口必须仍是整桌面（不能被自己缩回家屏）")
        host = shell._main_host
        local = QRect(0, 0, union.width(), union.height())
        for _screen, rect in host.activity:
            assert local.contains(QRect(rect)), "活动区必须仍是单窗口径"
    finally:
        _cleanup(shell)


def test_degraded_state_saves_the_home_screen_ratio(tmp_path, monkeypatch):
    """降级态落盘要用**单窗**的屏矩形：家屏正中不许被写成 1.5 这种越界比例。"""
    from pet import pet_profiles
    from tests.test_overlay_hosts_multiscreen import FakeScreen

    right = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), name="RIGHT")
    left = FakeScreen((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1040), name="LEFT")
    _install_screens(monkeypatch, right, left)
    shell = _make_shell(tmp_path, right, {"allow_cross_screen": True})
    try:
        original = shell._apply_host_window_caps
        monkeypatch.setattr(
            shell, "_apply_host_window_caps",
            lambda host: (_ for _ in ()).throw(RuntimeError("失败")) if host.screen is left
            else original(host))
        main = shell.sprite
        # 家屏（右）的中线：整桌面 3840 里的 0.75
        shell._place_body_ratio(main, 0.75, 0.5, QRect(shell.overlay.geometry()))

        left.set_rects((-1920, 0, 1920, 1080), (-1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()
        assert shell._active_multi() is False, "前提：已降级为单窗"

        saved = shell._save_child_geometry(main, slot=0, geometry=None)

        # 降级态下"窗口按单窗、屏按目标"两套口径并存，比例用哪套都可能是错的
        # （实测会写出 1.5 / 0.08）。这里的正确行为是**不写**，等拓扑恢复再存。
        assert saved is False, "拓扑未对齐时不许落盘几何"
        profile = pet_profiles.read_slot_profile(tmp_path, 0)
        rx = profile.data.get("rx")
        assert rx is None or 0.0 <= float(rx) <= 1.0, (
            f"绝不许把越界比例写进档案，实际 {rx}")
    finally:
        _cleanup(shell)


def test_save_is_refused_while_the_topology_transaction_is_running(
        tmp_path, monkeypatch):
    """**事务进行中**（宿主已补齐、几何还没应用）拖拽收尾不许落盘。

    这段时间 `_active_multi()` 已经翻成多窗、精灵坐标还是旧单窗的，量出来的比例是
    越界值（实测 1.5）。落盘后补窗成功或重启就会把宠推到屏边。
    """
    from pet import pet_profiles

    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, 1040))
        # 建宠时已经存过一次默认几何，这里记下"翻转前"的快照，比对用
        before = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)

        original = shell._adopt_new_host
        results = {}

        def adopt(screen, *, visible):
            original(screen, visible=visible)
            # 宿主已补齐、`_sync_host_geometry` 还没跑：模拟拖拽在这一刻收尾
            results["saved"] = shell._save_child_geometry(main, slot=0, geometry=None)
            results["in_txn"] = shell._topology_txn
            results["active_multi"] = shell._active_multi()

        monkeypatch.setattr(shell, "_adopt_new_host", adopt)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        assert results["in_txn"] is True, "前提：那一刻确实在事务里"
        assert results["active_multi"] is True, "前提：形态标志已翻成多窗"
        assert results["saved"] is False, "事务进行中不许落盘"
        assert dict(pet_profiles.read_slot_profile(tmp_path, 0).data) == before, (
            "事务进行中那次保存不许改动档案")

        # 事务收口后再存：这次要成功，而且比例正确
        shell._place_body_ratio(main, 0.5, 0.5, shell._screen_rect(a))
        assert shell._save_child_geometry(main, slot=0, geometry=None) is True
        rx = float(pet_profiles.read_slot_profile(tmp_path, 0).data["rx"])
        assert rx == pytest.approx(0.5, abs=0.05)
    finally:
        _cleanup(shell)


def test_rollback_puts_session_notifications_back_on_the_survivor(
        tmp_path, monkeypatch):
    """撤掉半成品宿主后，会话通知要**显式**登记回存活宿主。

    通知是"按 HWND 注册、新的顶掉旧的"；用户设了 on_top=False 时再下发一遍窗口能力
    会早退（同值），早退路径不碰通知注册——只调能力下发是不够的。
    """
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True, "on_top": False})
    try:
        registered: list = []
        monkeypatch.setattr(shell, "_register_session_notifications",
                            lambda host=None: registered.append(host))
        original = shell._apply_host_window_caps
        monkeypatch.setattr(
            shell, "_apply_host_window_caps",
            lambda host: (_ for _ in ()).throw(RuntimeError("失败")) if host.screen is b
            else original(host))

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        assert shell._main_host in registered, (
            "撤宿主后必须把会话通知登记回存活宿主（不能只靠能力下发，那里会早退）")
    finally:
        _cleanup(shell)


def test_interrupted_drag_is_saved_after_the_topology_transaction(
        tmp_path, monkeypatch):
    """事务打断的拖拽：当场拒写（防错误比例），**收口后要补存**。

    原先的取舍是"这次不写"——安全但不完整：拓扑切换打断拖拽后用户重启/被强杀，
    位置就丢了。收口后拓扑已对齐，这时再存一次才是正确的口径。
    """
    from pet import pet_profiles

    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, 1040))
        # 模拟"正在拖这只"：事务里的 `_end_drag` 会走一次收尾并尝试保存
        shell._drag_relaxed_sprite = main
        before = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)

        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        after = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)
        assert after != before, "事务收口后必须把这次位置补存下来"
        rx = float(after["rx"])
        assert 0.0 <= rx <= 1.0, f"补存的比例也必须合法，实际 {rx}"
        assert rx == pytest.approx(0.5, abs=0.1)
    finally:
        _cleanup(shell)


def test_deferred_drag_save_survives_a_multi_pass_transaction(tmp_path, monkeypatch):
    """一次重排需要**多轮**时，第一轮记下的补存记录不许被后面的轮次吃掉。

    每轮开始都清记录的话：第一轮记下被拖那只 → 第二轮 `_drag_relaxed_sprite` 已被
    几何事务清空 → 记不上 → 收敛成功也补不了存。这条路径是"最终成功却漏存"。
    """
    from pet import pet_profiles

    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, 1040))
        shell._drag_relaxed_sprite = main
        before = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)

        original = shell._adopt_new_host
        state = {"once": True}

        def adopt(screen, *, visible):
            original(screen, visible=visible)
            if state["once"]:
                state["once"] = False
                # 事务内把缩放改回去：这一轮结束后判定又变回单窗 → 必须**再跑一轮**
                # 把多出来的宿主拆掉。第二轮时 `_drag_relaxed_sprite` 已被几何事务
                # 清空，靠的就是第一轮记下的那条待补存记录。
                b.set_rects((1920, 0, 1920, 1080), (1920, 0, 1920, 1040), dpr=1.0)

        monkeypatch.setattr(shell, "_adopt_new_host", adopt)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        assert shell._topology_needs_reconcile() is False, "先确认最终是收敛的"
        assert len(shell.hosts()) == 1, "前提：确实跑了第二轮把宿主拆回去"
        after = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)
        assert after != before, "收敛后必须补存（多轮也要）"
        rx = float(after["rx"])
        assert rx == pytest.approx(0.5, abs=0.1)
    finally:
        _cleanup(shell)


def test_deferred_drag_save_is_kept_when_the_topology_is_still_broken(
        tmp_path, monkeypatch):
    """未收敛时补存记录**不许被消费**：先清记录再判条件，等于把"这次没对齐"变成
    "这次没存过"，补窗成功后也没人再补。"""
    a, b = _two_screens()
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True})
    try:
        main = shell.sprite
        shell._place_body_ratio(main, 0.25, 0.5, QRect(0, 0, 3840, 1040))
        shell._drag_relaxed_sprite = main
        original_caps = shell._apply_host_window_caps
        state = {"fail": True}

        def flaky(host):
            if state["fail"] and host.screen is b:
                raise RuntimeError("建窗失败")
            return original_caps(host)

        monkeypatch.setattr(shell, "_apply_host_window_caps", flaky)
        b.set_rects((1920, 0, 1920, 1080), (1920, 0, 960, 520), dpr=2.0)
        shell._feed_screen_dpr()

        assert shell._topology_needs_reconcile() is True, "前提：确实没对齐"
        assert shell._geometry_save_after_txn is main, "未对齐时必须留着这条待补存记录"

        # 恢复后下一次信号应当把这次位置补存掉
        state["fail"] = False
        from pet import pet_profiles
        before = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)
        shell._feed_screen_dpr()
        after = dict(pet_profiles.read_slot_profile(tmp_path, 0).data)
        assert after != before, "对齐后要补存那次被打断的拖拽"
        assert shell._geometry_save_after_txn is None, "存上了才消费记录"
    finally:
        _cleanup(shell)
