# -*- coding: utf-8 -*-
"""灵动岛跨屏松手选屏（issue #154）：岛落在**光标**所在屏，不再弹回原屏。

根因：``mouseReleaseEvent`` 的夹取/停靠判定用 ``_current_screen()`` 选屏，而它
是 ``screenAt(self.pos())``——窗口**左上角**所在屏。跨屏拖拽的触发带恰等于
抓取偏移量：光标刚过屏缝松手时左上角仍在原屏，于是 ``available`` 还是原屏，
整窗被夹回原屏边缘（距离 0）并就近判定停靠——用户看到的"弹回主屏右缘细条"。
修法：release 路径改按松手**光标全局位置**选屏（``event.globalPosition()``），
窗口左上角落回该屏。心跳 ``_clamp_to_screen`` 仍按窗口左上角（跨缝状态夹回
所在屏是保守合理行为，不动）。

本机只有一块屏，这里用假屏驱动**真实**的选屏/夹取/停靠代码（同
``tests/test_multi_screen_interaction.py`` 的假屏思路）；真实双屏由用户在双屏
机器上验收，见报告里的"实机未验"声明。断言只依赖假屏几何与窗口宽高，不赌
字体度量，全程不用 sleep 猜时序。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.island.dynamic_island import DynamicIsland

# 主屏 1920×1040（左上角原点），副屏在右 / 在左 / 错位拼接三种摆法
PRIMARY = QRect(0, 0, 1920, 1040)
RIGHT_SECOND = QRect(1920, 0, 1920, 1040)
LEFT_SECOND = QRect(-1920, 0, 1920, 1040)
# 错位拼接：副屏右下错开，左上角留出 (x≥1920, y<200) 的空洞
RAGGED_SECOND = QRect(1920, 200, 1920, 840)

# 抓取点（相对窗口左上角）：距左 30px、纵向居中——落在胶囊内，且 x 偏移让
# 「光标已过缝、左上角还在原屏」这个触发带出现（这正是缺陷的形态）
GRAB = QPoint(30, 22)


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeScreen:
    """QScreen 的最小替身：选屏/夹取/停靠只读 ``availableGeometry``。"""

    def __init__(self, available: QRect, name: str):
        self._available = QRect(available)
        self._name = name

    def availableGeometry(self) -> QRect:
        return QRect(self._available)

    def name(self) -> str:
        return self._name


class _FakeDesktop:
    """假多屏桌面：接管 ``QGuiApplication.screenAt`` / ``primaryScreen``。

    落在假桌面包围盒内、却没有任何屏覆盖的点返回 ``None``（错位拼接的空洞，
    与真实 ``screenAt`` 在空洞里的行为一致）——产品代码对 ``None`` 回退主屏；
    包围盒外的点交回真实实现，避免误伤 Qt 内部对这两个静态方法的调用。
    """

    def __init__(self, primary: QRect, *others: QRect):
        self.primary = _FakeScreen(primary, "primary")
        self.screens = [self.primary]
        box = QRect(primary)
        for index, rect in enumerate(others):
            self.screens.append(_FakeScreen(rect, f"screen-{index + 1}"))
            box = box.united(QRect(rect))
        self._box = box
        self._real_screen_at = QGuiApplication.screenAt

    @property
    def secondary(self) -> _FakeScreen:
        return self.screens[1]

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(
            QGuiApplication, "screenAt", staticmethod(self._screen_at))
        monkeypatch.setattr(
            QGuiApplication, "primaryScreen", staticmethod(lambda: self.primary))

    def _screen_at(self, point):
        point = QPoint(point)
        for screen in self.screens:
            if screen.availableGeometry().contains(point):
                return screen
        if self._box.contains(point):
            return None
        return self._real_screen_at(point)


def _mouse(kind: QEvent.Type, widget, global_pos: QPoint,
           buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    return QMouseEvent(kind, QPointF(widget.mapFromGlobal(global_pos)),
                       QPointF(global_pos), Qt.MouseButton.LeftButton,
                       buttons, Qt.KeyboardModifier.NoModifier)


def _drag_to(widget, cursor_global: QPoint) -> QPoint:
    """按 ``GRAB`` 抓取拖到 ``cursor_global``（不松手），返回松手前的左上角。

    起手点 = 当前窗口左上角 + GRAB，于是拖到位时窗口左上角必然等于
    ``cursor_global - GRAB``——"光标过缝、左上角未过缝"的触发带由此可控复现。
    """
    press = widget.pos() + GRAB
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, widget, press))
    mid = QPoint((press.x() + cursor_global.x()) // 2,
                 (press.y() + cursor_global.y()) // 2)
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, widget, mid))
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, widget, cursor_global))
    assert widget._dragging, "拖拽未起手（位移没过阈值）"
    return widget.pos()


def _release_at(widget, cursor_global: QPoint) -> None:
    widget.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, widget, cursor_global,
               buttons=Qt.MouseButton.NoButton))


def _island(tmp_path: Path, **overrides) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 1400, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


def _settled_geometry(island: DynamicIsland) -> QRect:
    island._finish_animations()
    return island.geometry()


def _precondition(island: DynamicIsland) -> None:
    assert island.width() > GRAB.x(), "抓取点必须落在胶囊内才是真实拖拽"
    assert island.height() > GRAB.y()


# ------------------------------------------------------------ 跨屏松手选屏
def test_release_past_seam_lands_on_cursor_screen(tmp_path, monkeypatch):
    """右副屏：光标刚过缝 5px 松手，岛必须落到副屏（修复前弹回主屏右缘）。"""
    _qapp()
    desktop = _FakeDesktop(PRIMARY, RIGHT_SECOND)
    desktop.install(monkeypatch)
    island = _island(tmp_path)
    try:
        island.show()
        _precondition(island)
        cursor = QPoint(RIGHT_SECOND.left() + 5, 500)  # 过缝 5px
        left_top = _drag_to(island, cursor)
        # 触发带成立：光标在副屏、窗口左上角仍在主屏（缺陷先决条件）
        assert desktop._screen_at(cursor) is desktop.secondary
        assert left_top.x() < RIGHT_SECOND.left()
        _release_at(island, cursor)
        geo = _settled_geometry(island)
        assert RIGHT_SECOND.contains(geo), f"岛被弹回主屏：{geo}"
        assert island._debug_state()["mode"] == "docked"
        assert island._debug_state()["dock_edge"] == "left"
        saved = island.config.get("dynamic_island")
        assert saved["dock_edge"] == "left"
        assert saved["x"] >= RIGHT_SECOND.left()
    finally:
        island.hide()
        island.deleteLater()


def test_release_deep_inside_secondary_stays_normal(tmp_path, monkeypatch):
    """右副屏：光标深入副屏内部松手 → 留副屏、不触发停靠（防回归对照）。"""
    _qapp()
    desktop = _FakeDesktop(PRIMARY, RIGHT_SECOND)
    desktop.install(monkeypatch)
    island = _island(tmp_path)
    try:
        island.show()
        _precondition(island)
        cursor = QPoint(RIGHT_SECOND.left() + 600, 500)
        _drag_to(island, cursor)
        _release_at(island, cursor)
        geo = _settled_geometry(island)
        assert RIGHT_SECOND.contains(geo), f"岛跑出副屏：{geo}"
        assert island.geometry().topLeft() == cursor - GRAB  # 未被夹取挪动
        assert island._debug_state()["dock_edge"] == "none"
        assert island._debug_state()["mode"] == "normal"
    finally:
        island.hide()
        island.deleteLater()


def test_release_into_left_mirror_screen(tmp_path, monkeypatch):
    """左副屏（镜像摆法）：光标越过屏缝回到主屏松手，岛必须跟着落到主屏。"""
    _qapp()
    desktop = _FakeDesktop(PRIMARY, LEFT_SECOND)
    desktop.install(monkeypatch)
    island = _island(tmp_path, x=-400, y=300)  # 岛起始在左副屏
    try:
        island.show()
        _precondition(island)
        assert LEFT_SECOND.contains(island.geometry())
        cursor = QPoint(5, 500)  # 光标刚进主屏 5px
        left_top = _drag_to(island, cursor)
        assert desktop._screen_at(cursor) is desktop.primary
        assert left_top.x() < PRIMARY.left()
        _release_at(island, cursor)
        geo = _settled_geometry(island)
        assert PRIMARY.contains(geo), f"岛被弹回左副屏：{geo}"
        assert island._debug_state()["dock_edge"] == "left"
    finally:
        island.hide()
        island.deleteLater()


def test_release_in_layout_gap_falls_back_to_primary(tmp_path, monkeypatch):
    """错位拼接的空洞：``screenAt(光标) is None`` → 仍回退主屏（行为不变）。"""
    _qapp()
    desktop = _FakeDesktop(PRIMARY, RAGGED_SECOND)
    desktop.install(monkeypatch)
    island = _island(tmp_path)
    try:
        island.show()
        _precondition(island)
        cursor = QPoint(RAGGED_SECOND.left() + 10, RAGGED_SECOND.top() - 100)
        assert desktop._screen_at(cursor) is None, "该点应落在空洞里"
        _drag_to(island, cursor)
        _release_at(island, cursor)
        geo = _settled_geometry(island)
        assert PRIMARY.contains(geo), f"空洞松手没回退主屏：{geo}"
        assert island._debug_state()["dock_edge"] == "right"
    finally:
        island.hide()
        island.deleteLater()
