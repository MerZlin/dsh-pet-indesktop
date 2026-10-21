# -*- coding: utf-8 -*-
"""灵动岛「靠边半隐藏」hover 振荡回归（左右停靠细条的死区）。

**根因**：左右停靠时细条是 ``_STRIP_THICKNESS × _STRIP_SIDE`` = 16×64，滑出胶囊高
``_CAPSULE_HEIGHT`` = 44，且以细条中心居中 → 细条上下各露 (64-44)/2 = **10px 死区**。
光标停在死区里时：

1. 细条盖着光标 → ``enterEvent`` → 滑出胶囊；
2. 胶囊比细条矮，上/下缘**越过光标**（光标此刻在窗口外）→ Qt 合成 ``leaveEvent``；
3. 800ms 后 ``_dock_back`` 收回细条 → 细条重新盖住同一像素 → 回到第 1 步。

第 3 步只看 ``_mode`` / ``_hover_peek``，不看光标在哪，于是回路自持，周期 ≈1s。
上下停靠没有这个问题（``strip_rect_for`` 的上下形态宽度不变、高度 16 < 44，滑出矩
形完全覆盖细条，"光标在细条内"必然"光标在滑出矩形内"，不会有死区）。

**判据修正**：可 hover 区 = 当前窗口矩形 ∪ 细条矩形（docked 态由 ``strip_rect_for``
按当前窗口推导，非 docked 就是窗口矩形）。收回的唯一入口 ``_dock_back`` 进入前复
检：光标仍在区内 → 不收回，只把收回表延后一拍再判（光标真走了仍会在 800ms 内收
口）。细条单击的滑出预览共用这个入口，不另写一份判据。

**实机对照**（真实 Windows 屏幕、真实鼠标停在死区，探针 ``.scratch/island_hover_
probe.py``）：修复前 2.5s 内 49 次几何翻转、周期 ≈1.02s；修复后 0 次。数字与原始
输出见 ``docs/PR-REPORT-ISLAND-HOVER-OSCILLATION-2026-10-13.md``。

光标位置在 offscreen 平台用 ``QCursor.setPos`` 直接驱动（实测该平台 setPos/pos 可
回读一致），这是**操作系统边界**——真实鼠标位置没有别的确定性注入方式，所以在这里
动它不算替身。用完复位，别污染别的用例。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.island.dynamic_island import (
    DynamicIsland,
    _CAPSULE_HEIGHT,
    _STRIP_SIDE,
    _STRIP_THICKNESS,
)

app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_cursor():
    """用例结束后把全局光标放回原处（offscreen 下 setPos 是真生效的全局状态）。"""
    home = QCursor.pos()
    try:
        yield
    finally:
        QCursor.setPos(home)


def _island(tmp_path, **overrides) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True, "show_info": True,
        "info_mode": "time", "custom_text": "", "show_status": True, "style": "dark",
        "x": 200, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


def _put_cursor(point: QPoint) -> None:
    """把光标放到全局坐标 point（并回读校验，setPos 无效的平台直接红）。"""
    QCursor.setPos(point)
    assert QCursor.pos() == point, \
        f"本平台 QCursor.setPos 无法驱动光标（读到 {QCursor.pos()}），用例会假绿"


def _mouse(kind: QEvent.Type, widget, global_pos: QPoint) -> QMouseEvent:
    local = widget.mapFromGlobal(global_pos)
    return QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def _click(widget, global_pos: QPoint) -> None:
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, widget, global_pos))
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(widget.mapFromGlobal(global_pos)),
        QPointF(global_pos), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier)
    widget.mouseReleaseEvent(release)


def _os_hover(island: DynamicIsland, point: QPoint) -> bool:
    """按 OS/Qt 语义重判光标与窗口的关系：出窗合成 leave，回窗合成 enter。

    窗口在光标**停着**的时候缩小/移走时 Qt 会合成一次 leave（正是本 bug 的第 2 步），
    只在"圈内 → 圈外"的沿上发一次，所以这里也按沿触发。
    """
    inside = island.geometry().contains(point)
    if inside and not island._cursor_inside:
        island.enterEvent(None)
    elif not inside and island._cursor_inside:
        island.leaveEvent(None)
    return inside


def _geo_tuple(island: DynamicIsland) -> tuple[int, int, int, int]:
    rect = island.geometry()
    return (rect.x(), rect.y(), rect.width(), rect.height())


def _in_hover_zone(island: DynamicIsland, point: QPoint) -> bool:
    """光标点是否落在可 hover 区内（与产品判据同源：逐矩形判包含）。"""
    return any(rect.contains(point) for rect in island._hover_rects())


def _zone_covers(island: DynamicIsland, rect: QRect) -> bool:
    """可 hover 区是否覆盖整个 rect（逐像素判，不看包围盒）。"""
    return all(_in_hover_zone(island, QPoint(x, y))
               for x in range(rect.left(), rect.right() + 1)
               for y in range(rect.top(), rect.bottom() + 1))


def _drive_anim(island: DynamicIsland, holding: QPoint | None = None,
                max_ticks: int = 60) -> list[tuple[bool, tuple[int, int, int, int]]]:
    """逐 tick 推进几何动画（含收尾重算），每帧后按 OS 语义重判一次光标。

    返回每帧之后的 ``(hover_peek, 几何)`` 采样。**振荡是一个循环**——只看回合结
    束时的状态抓不到它（收回+再滑出之后末态和出口态一模一样），必须逐帧看这一轮
    里出现过什么。
    """
    samples: list[tuple[bool, tuple[int, int, int, int]]] = []
    for _ in range(max_ticks):
        if not island._anim_timer.isActive():
            return samples
        island._anim_last = time.monotonic() - 0.05
        island._on_anim_tick()
        if holding is not None:
            _os_hover(island, holding)
        samples.append((island._hover_peek, _geo_tuple(island)))
    return samples


def _assert_round_stable(samples, peek_tuple, round_no: int, why: str) -> None:
    """这一轮里只允许出现"停在滑出态"的帧，任何一帧往细条方向退都是振荡。"""
    bad = [sample for sample in samples if sample != (True, peek_tuple)]
    assert not bad, f"第 {round_no} 轮：{why}——出现收回/振荡帧 {bad[:3]}（共 {len(bad)} 帧）"


def _docked_island(tmp_path, edge: str):
    """建一个停靠在 edge 的岛，返回 (island, strip 矩形)。"""
    island = _island(tmp_path, dock_edge=edge)
    island.show()
    app.processEvents()
    island._finish_animations()
    app.processEvents()
    strip = island.geometry()
    assert island._mode == "docked" and island._hover_peek is False
    if edge in ("left", "right"):
        assert (strip.width(), strip.height()) == (_STRIP_THICKNESS, _STRIP_SIDE)
    else:
        assert strip.height() == _STRIP_THICKNESS
    return island, strip


@pytest.mark.parametrize("edge", ["left", "right"])
def test_side_dock_dead_zone_hover_does_not_oscillate(tmp_path, edge):
    """光标停在细条死区：滑出之后不许被收回（收回会让细条再盖住光标 → 死循环）。"""
    island, strip = _docked_island(tmp_path, edge)
    try:
        # 死区采样点：细条内、滑出胶囊（高 44，以细条中心居中）之外的第一个像素
        dead = QPoint(strip.center().x(), strip.top() + 2)
        assert strip.contains(dead)
        _put_cursor(dead)

        _os_hover(island, dead)                  # 光标落在细条上 → enterEvent 滑出
        _drive_anim(island, holding=dead)
        peek = island.geometry()
        assert island._hover_peek is True
        assert peek.height() == _CAPSULE_HEIGHT
        assert not peek.contains(dead), "本用例的前提：死区（滑出胶囊盖不到这个像素）"
        assert island._cursor_inside is False, "胶囊滑出后光标已在窗外（Qt 合成 leave）"

        # 不变量：可 hover 区覆盖整个细条 —— 死区像素也在区内（逐像素判，非包围盒），
        # 并且滑出时推导出的细条矩形就是收回后窗口会落到的那个矩形
        assert _zone_covers(island, strip)

        # 收回表到点（直接驱动回调，与既有测试同一手法）：光标还在死区 → 不许收回
        peek_tuple = _geo_tuple(island)
        for round_no in range(1, 7):
            island._dock_back()
            samples = _drive_anim(island, holding=dead)
            assert island._hover_peek is True, \
                f"第 {round_no} 轮：光标仍停在死区，滑出却被收回 → 回路开始"
            assert island.geometry() == peek, \
                f"第 {round_no} 轮：死区悬停下几何发生了振荡（{island.geometry()}）"
            _assert_round_stable(samples, peek_tuple, round_no, "光标停在死区")
    finally:
        island.hide()
        island.deleteLater()


@pytest.mark.parametrize("edge", ["left", "right"])
def test_dead_zone_peek_retracts_once_the_cursor_leaves(tmp_path, edge):
    """反向护栏：光标真的离开可 hover 区之后，必须在下一拍收口（不许永久钉住）。"""
    island, strip = _docked_island(tmp_path, edge)
    try:
        dead = QPoint(strip.center().x(), strip.top() + 2)
        _put_cursor(dead)
        _os_hover(island, dead)
        _drive_anim(island, holding=dead)
        peek = island.geometry()
        assert island._hover_peek is True

        # 光标离开死区（细条下缘再往下 60px）：窗口与细条都在外面
        away = QPoint(strip.center().x(), strip.bottom() + 60)
        assert not _in_hover_zone(island, away)
        _put_cursor(away)

        island._dock_back()
        _drive_anim(island, holding=away)
        assert island._hover_peek is False
        assert island.geometry() == strip, "光标真走了却不收回，滑出就被永久钉住"

        # 收口之后同一轮悬停还能再走一遍（判据没有把滑出一起锁死）
        _put_cursor(dead)
        _os_hover(island, dead)
        _drive_anim(island, holding=dead)
        assert island._hover_peek is True
        assert island.geometry() == peek
    finally:
        island.hide()
        island.deleteLater()


def test_side_dock_peek_union_covers_the_strip(tmp_path):
    """不变量：可 hover 区覆盖整个细条（滑出矩形 ∪ 细条矩形）。"""
    island, strip = _docked_island(tmp_path, "right")
    try:
        dead_top = QPoint(strip.center().x(), strip.top() + 2)
        dead_bottom = QPoint(strip.center().x(), strip.bottom() - 2)
        _put_cursor(dead_top)
        _os_hover(island, dead_top)
        _drive_anim(island, holding=dead_top)
        peek = island.geometry()
        assert not peek.contains(dead_top), "细条上端死区：胶囊盖不到"
        assert not peek.contains(dead_bottom), "细条下端死区：胶囊盖不到"
        assert _zone_covers(island, strip)
        assert _in_hover_zone(island, dead_top)
        assert _in_hover_zone(island, dead_bottom)
    finally:
        island.hide()
        island.deleteLater()


@pytest.mark.parametrize("edge", ["top", "bottom"])
def test_flat_dock_has_no_dead_zone_and_keeps_retracting(tmp_path, edge):
    """上下停靠本来就没有死区：光标停在细条内时滑出胶囊完全覆盖它，不进回路。"""
    island, strip = _docked_island(tmp_path, edge)
    try:
        inside = QPoint(strip.center().x(), strip.top() + 2)
        assert strip.contains(inside)
        _put_cursor(inside)
        _os_hover(island, inside)
        _drive_anim(island, holding=inside)
        peek = island.geometry()
        assert island._hover_peek is True
        assert peek.contains(inside), "上下停靠的滑出矩形必须完全覆盖细条"
        assert island._cursor_inside is True, "光标仍在窗内：不该有合成 leave"

        # 光标移出滑出矩形 → 正常收回
        away = QPoint(peek.center().x(), peek.bottom() + 40)
        _put_cursor(away)
        island.leaveEvent(None)
        island._dock_back()
        _drive_anim(island, holding=away)
        assert island._hover_peek is False
        assert island.geometry() == strip
    finally:
        island.hide()
        island.deleteLater()


def test_clicking_the_strip_from_the_dead_zone_does_not_oscillate(tmp_path):
    """细条单击的滑出预览共用同一判据：单击点落在死区时同样不许收回→再滑出。"""
    island, strip = _docked_island(tmp_path, "left")
    try:
        dead = QPoint(strip.center().x(), strip.top() + 2)
        _put_cursor(dead)
        _click(island, dead)                     # 细条单击 = 滑出预览 + 起收回表
        _drive_anim(island, holding=dead)
        peek = island.geometry()
        peek_tuple = _geo_tuple(island)
        assert island._hover_peek is True
        assert not peek.contains(dead)

        for round_no in range(1, 5):
            island._dock_back()                  # 800ms 到点
            samples = _drive_anim(island, holding=dead)
            assert island._hover_peek is True, f"第 {round_no} 轮：死区单击后被收回"
            assert island.geometry() == peek, f"第 {round_no} 轮：几何振荡"
            _assert_round_stable(samples, peek_tuple, round_no, "死区单击的滑出预览")

        # 光标离开 → 下一拍收口
        away = QPoint(strip.center().x(), strip.bottom() + 60)
        _put_cursor(away)
        island._dock_back()
        _drive_anim(island, holding=away)
        assert island._hover_peek is False
        assert island.geometry() == strip
    finally:
        island.hide()
        island.deleteLater()


def test_hover_zone_is_only_the_window_when_not_docked(tmp_path):
    """非停靠态可 hover 区就是窗口矩形（细条矩形不参与）。"""
    island = _island(tmp_path)
    island.show()
    app.processEvents()
    island._finish_animations()
    try:
        assert island._mode == "normal"
        assert island._hover_rects() == (island.geometry(),)
    finally:
        island.hide()
        island.deleteLater()


def test_hover_zone_excludes_the_band_below_the_capsule(tmp_path):
    """判据是**逐矩形并集**，不是包围盒：胶囊下缘外那 10px 横带不在区内。

    那里收回完全安全（收回后细条在左边 16px 宽的一条里，光标既不在胶囊也不在细
    条），把它算进区内只会让收回被无谓拖后。
    """
    island, strip = _docked_island(tmp_path, "left")
    try:
        dead = QPoint(strip.center().x(), strip.top() + 2)
        _put_cursor(dead)
        _os_hover(island, dead)
        _drive_anim(island, holding=dead)
        peek = island.geometry()
        assert island._hover_peek is True
        corner = QPoint(peek.right() - 2, peek.bottom() + 3)
        assert not peek.contains(corner)
        assert not strip.contains(corner)
        assert not _in_hover_zone(island, corner)
        assert peek.united(strip).contains(corner),             "包围盒会把它算进区内——所以要逐矩形判"

        # 光标移到这条横带里 → 收回照常发生（不被包围盒判据拖住）
        _put_cursor(corner)
        island._dock_back()
        _drive_anim(island, holding=corner)
        assert island._hover_peek is False
        assert island.geometry() == strip
    finally:
        island.hide()
        island.deleteLater()


@pytest.mark.parametrize("edge", ["left", "right"])
def test_derived_strip_rect_is_the_retract_target(tmp_path, edge):
    """判据的等价前提：滑出时推导出的细条矩形 == 收回后窗口落到的矩形。

    等价成立，才有"光标在推导矩形里 → 收回后细条立刻盖住它 → 必然再滑出"这条推
    论；等价一旦被改坏（比如换成按别的锚点推导），判据就成了猜。
    """
    island, strip = _docked_island(tmp_path, edge)
    try:
        dead = QPoint(strip.center().x(), strip.top() + 2)
        _put_cursor(dead)
        _os_hover(island, dead)
        _drive_anim(island, holding=dead)
        assert island._hover_peek is True
        derived = island._hover_rects()[1]
        assert derived == strip, "滑出时推导出的必须仍是最初那条细条（同心）"

        away = QPoint(strip.center().x(), strip.bottom() + 60)
        _put_cursor(away)
        island._dock_back()
        _drive_anim(island, holding=away)
        assert island.geometry() == derived
    finally:
        island.hide()
        island.deleteLater()
