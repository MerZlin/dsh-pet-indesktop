# -*- coding: utf-8 -*-
"""网易云悬浮控制器：灯效取色、形态几何、悬停开合、外框刷、平台分支。

不建真实网络/SMTC 会话：播放状态与进程探测都是 OS 边界，用替身断言。
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.music import floatbar as fb


@pytest.fixture()
def bar(monkeypatch):
    monkeypatch.setattr(fb, "_load_state", lambda: {})
    monkeypatch.setattr(fb, "_save_state", lambda _state: None)
    monkeypatch.setattr(fb, "_is_cloudmusic_running", lambda: True)
    app = QApplication.instance() or QApplication([])
    widget = fb.MusicFloatBar()
    widget._refresh_timer.stop()
    widget._vis_timer.stop()
    widget._breathe_timer.stop()
    widget._hover_timer.stop()
    yield widget
    widget.shutdown()
    widget.deleteLater()
    app.processEvents()


def test_lamp_off_when_not_playing(bar):
    """没有播放中的会话时，镂空处必须完全不亮（返回 None 让绘制层跳过）。"""
    bar._playing = False
    assert bar._lamp_brush(0, 0, 0, 10) is None


def test_solid_lamp_alpha_cycles(bar):
    """单色灯效：相位 0 全灭，相位 π 全亮。"""
    bar._playing = True
    bar._effect = "red"
    bar._breathe_phase = 0.0
    assert bar._solid_alpha_color(QColor(222, 52, 52)) is None

    bar._breathe_phase = math.pi
    lit = bar._solid_alpha_color(QColor(222, 52, 52))
    assert lit is not None
    assert lit.alpha() == fb._BREATHE_MAX_ALPHA
    assert (lit.red(), lit.green(), lit.blue()) == (222, 52, 52)


def test_rainbow_flow_keeps_constant_lum(bar):
    """彩虹流动（不呼吸）亮度恒定；彩虹呼吸有明暗两态。"""
    bar._playing = True
    bar._effect = "rainbow"
    assert bar._lamp_brush(0, 0, 0, 10) is not None
    assert bar._rainbow_lum(False) == 1.0

    bar._effect = "rainbow_breath"
    assert bar._rainbow_lum(True) < 0.2   # 暗态
    bar._glow_phase = math.pi
    assert bar._rainbow_lum(True) > 0.9   # 亮态


def test_edge_bar_layout_flush_with_edge(bar):
    """贴左边：变成 12×96 竖条，贴边侧严丝合缝压在屏幕可用区左缘。"""
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_BAR)
    avail = bar._available_geometry()
    assert (bar.width(), bar.height()) == (fb._EDGE_THICK, fb._EDGE_LONG)
    assert bar.x() == avail.left()
    assert bar._edge_fade == 0.0


def test_edge_panel_anchored_and_shows_only_controls(bar):
    """贴边面板：贴屏侧不动，只露三个控制键（标题与收起键隐藏）。"""
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_BAR)
    bar._apply_form(fb._FORM_EDGE_PANEL)
    avail = bar._available_geometry()
    assert (bar.width(), bar.height()) == (fb._VINYL_PERP, fb._VINYL_ALONG)
    assert bar.x() == avail.left()
    assert not bar._btn_prev.isHidden()
    assert not bar._btn_toggle.isHidden()
    assert not bar._btn_next.isHidden()
    assert bar._title.isHidden()
    assert bar._btn_collapse.isHidden()


def test_rounded_skin_is_portrait_on_vertical_edges(bar):
    """圆角矩形皮肤：竖边上高窄（64×120），横边上宽扁（120×64）。"""
    bar._skin = fb._SKIN_ROUNDED
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    assert (bar.width(), bar.height()) == (fb._PANEL_THICK, fb._PANEL_LONG)

    bar._edge = "bottom"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    assert (bar.width(), bar.height()) == (fb._PANEL_LONG, fb._PANEL_THICK)


def test_edge_panel_buttons_clear_of_vinyl_disc(bar):
    """三个键必须落在贴屏侧的矩形段里，不能被内端黑胶（半径 = 短边/2）压住。"""
    bar._skin = fb._SKIN_VINYL
    for edge in ("left", "right", "top", "bottom"):
        bar._edge = edge
        bar._apply_form(fb._FORM_EDGE_PANEL)
        vertical = edge in ("left", "right")
        cross = bar.width() if vertical else bar.height()
        span = bar.height() if vertical else bar.width()
        disc_near, disc_far = span, cross - span   # 内端唱片占掉 cross 轴最后 span
        for btn in (bar._btn_prev, bar._btn_toggle, bar._btn_next):
            geo = btn.geometry()
            if edge == "left":
                assert geo.right() < disc_far
            elif edge == "right":
                assert geo.left() >= disc_near
            elif edge == "top":
                assert geo.bottom() < disc_far
            else:
                assert geo.top() >= disc_near


def test_vinyl_buttons_are_horizontal_row(bar):
    """黑胶皮肤（竖边）：三个键横排——同一行，x 递增。"""
    bar._skin = fb._SKIN_VINYL
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    prev, toggle, nxt = (b.geometry() for b in (bar._btn_prev, bar._btn_toggle, bar._btn_next))
    assert prev.y() == toggle.y() == nxt.y()
    assert prev.x() < toggle.x() < nxt.x()


def test_rounded_buttons_are_vertical_column(bar):
    """圆角矩形皮肤（竖边）：三个键竖排——同一列，y 递增。"""
    bar._skin = fb._SKIN_ROUNDED
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    prev, toggle, nxt = (b.geometry() for b in (bar._btn_prev, bar._btn_toggle, bar._btn_next))
    assert prev.x() == toggle.x() == nxt.x()
    assert prev.y() < toggle.y() < nxt.y()


def test_state_skin_reads_only_known_values(bar):
    """存档里的面板主题只认两套皮肤，其余值一律回落默认（老存档不带这个键）。"""
    bar._state = {}
    assert bar._state_skin() == fb._SKIN_DEFAULT
    bar._state = {"panel_skin": "rounded"}
    assert bar._state_skin() == fb._SKIN_ROUNDED
    bar._state = {"panel_skin": "glass"}
    assert bar._state_skin() == fb._SKIN_DEFAULT


def test_set_skin_switches_geometry_and_persists(bar, monkeypatch):
    """切换皮肤：立即换成新皮肤的目标尺寸，并把选择写进存档。"""
    saved = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    assert (bar.width(), bar.height()) == (fb._VINYL_PERP, fb._VINYL_ALONG)

    bar._set_skin(fb._SKIN_ROUNDED)
    assert bar._skin == fb._SKIN_ROUNDED
    assert saved.get("panel_skin") == fb._SKIN_ROUNDED
    assert (bar.width(), bar.height()) == (fb._PANEL_THICK, fb._PANEL_LONG)


def test_rounded_body_has_square_corners_on_screen_side():
    """圆角只给朝屏内一侧：贴屏侧两角是直角，观感是「从屏幕边框里伸出来的板子」。"""
    path = fb.MusicFloatBar._rounded_body_path(48.0, 120.0, 1.0)
    assert path.contains(QPointF(0.5, 0.5))
    assert path.contains(QPointF(0.5, 119.5))
    assert not path.contains(QPointF(47.5, 0.5))
    assert not path.contains(QPointF(47.5, 119.5))


def test_vinyl_body_has_flat_screen_side():
    """黑胶皮肤：贴屏侧同样直角，半圆只长在朝屏内那一端。"""
    path = fb.MusicFloatBar._vinyl_body_path(160.0, 64.0, 1.0)
    assert path.contains(QPointF(0.5, 0.5))
    assert path.contains(QPointF(0.5, 63.5))
    assert path.contains(QPointF(159.0, 32.0))     # 半圆顶点
    assert not path.contains(QPointF(159.0, 1.0))  # 半圆之外


def test_pull_distance_depends_on_edge(bar):
    """「往外拉」的朝向随贴的边翻转，四条边都要能拉出去。"""
    bar._press_pos = QPoint(1000, 500)
    for edge, target in (
        ("left", QPoint(1040, 500)),
        ("right", QPoint(960, 500)),
        ("top", QPoint(1000, 540)),
        ("bottom", QPoint(1000, 460)),
    ):
        bar._edge = edge
        assert bar._pull_distance(target) == 40


def test_panel_press_starts_drag(bar):
    """面板空白处按下也要进入拖动（之前面板上的左键被直接吞掉，等于不能拖）。"""
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    local = QPointF(20.0, 30.0)
    press = QMouseEvent(
        QEvent.MouseButtonPress,
        local,
        QPointF(bar.x() + 20.0, bar.y() + 30.0),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    bar.mousePressEvent(press)
    assert bar._drag_pos is not None
    assert bar._drag_edge == "left"


def test_drag_away_from_edge_detaches_to_disc(bar):
    """贴边态往外拉过阈值 → 当场脱离贴边变圆盘（后续拖动跟普通窗口一致）。"""
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    bar._press_pos = QPoint(bar.x() + 20, bar.y() + 30)
    bar._drag_edge = "left"
    bar._detach_from_edge(QPoint(bar.x() + 200, bar.y() + 30))
    assert bar._edge is None
    assert bar._drag_edge is None
    assert bar._form == fb._FORM_DISC
    assert (bar.width(), bar.height()) == (fb._COLLAPSED_W, fb._COLLAPSED_H)


def test_drag_along_edge_keeps_side_pinned(bar):
    """贴边态沿边拖：只动沿边方向，垂直屏边方向仍钉在屏幕边缘上。"""
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    press = QPoint(bar.x() + 20, bar.y() + 30)
    bar._press_pos = press
    bar._drag_along = press.y() - bar.y()
    bar._slide_along_edge(QPoint(press.x() + 3, press.y() + 120))
    avail = bar._available_geometry()
    assert bar.x() == avail.left()
    assert bar.y() == press.y() + 120 - bar._drag_along


def test_button_drag_forwards_to_panel_and_detaches(bar):
    """按在控制键上再拖动 → 整段序列转给面板，越阈值当场脱离贴边变圆盘。

    这是实机上最容易被按到的位置：三个键盖住了面板中间，若按钮独吞按下事件，
    用户「抓住面板往出拉」就完全没有反应。
    """
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_PANEL)
    btn = bar._btn_toggle
    gx = bar.x() + btn.x() + 13
    gy = bar.y() + btn.y() + 13
    local = QPointF(13.0, 13.0)
    btn.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            local,
            QPointF(gx, gy),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    assert btn._press_global is not None
    assert bar._drag_pos is None   # 按下本身不算拖动，避免把点击吃掉
    # 朝屏幕内侧（左侧屏 → 向右）拉 60 px，超过 _EDGE_DETACH_PX
    btn.mouseMoveEvent(
        QMouseEvent(
            QEvent.MouseMove,
            local,
            QPointF(gx + 60.0, gy),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    assert bar._drag_pos is not None
    assert bar._drag_edge is None
    assert bar._edge is None
    assert bar._form == fb._FORM_DISC


def test_button_press_without_move_still_clicks(bar):
    """没移动就松手 → 仍然是一次正常点击（拖动接管不能把点击吃掉）。"""
    fired = []
    bar._btn_toggle.clicked.connect(lambda: fired.append(1))
    btn = bar._btn_toggle
    local = QPointF(13.0, 13.0)
    gx = bar.x() + btn.x() + 13
    gy = bar.y() + btn.y() + 13
    btn.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            local,
            QPointF(gx, gy),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    btn.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease,
            local,
            QPointF(gx, gy),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
    )
    assert fired == [1]
    assert bar._drag_pos is None


def test_detect_edge_requires_proximity(bar):
    """离边缘远于阈值时不吸附，靠近时命中同一条边。"""
    avail = bar._available_geometry()
    bar.move(avail.left() + 200, avail.top() + 200)
    assert bar._detect_edge() is None

    bar.move(avail.left() + 5, avail.top() + 200)
    assert bar._detect_edge() == "left"


def test_frame_brush_solid_and_gradient(bar):
    """外框刷：单色返回纯色，两色返回线性渐变。"""
    bar._frame = {"mode": "solid", "c1": "#102030", "c2": "#405060"}
    solid = bar._frame_brush(QRect(0, 0, 100, 40))
    assert solid.style() == Qt.BrushStyle.SolidPattern
    assert solid.color().name() == "#102030"

    bar._frame = {"mode": "gradient", "c1": "#102030", "c2": "#405060"}
    grad = bar._frame_brush(QRect(0, 0, 100, 40))
    assert isinstance(grad.gradient(), QLinearGradient)


def test_hover_opens_bar_and_panel_auto_collapses(bar, monkeypatch):
    """光标靠近长条 → 自动展开；光标移开面板 → 自动收贴。"""
    bar._netcloud_running = True
    bar._edge = "left"
    bar._apply_form(fb._FORM_EDGE_BAR)
    center = bar.frameGeometry().center()

    class _NearCursor:
        @staticmethod
        def pos():
            return center

    class _FarCursor:
        @staticmethod
        def pos():
            return QPoint(center.x() + 4000, center.y() + 4000)

    opened: list[str] = []
    monkeypatch.setattr(bar, "expand_edge_panel", lambda: opened.append("open"))
    monkeypatch.setattr(fb, "QCursor", _NearCursor)
    bar._hover_tick()
    assert opened

    collapsed: list[str] = []
    monkeypatch.setattr(bar, "collapse_to_edge_bar", lambda: collapsed.append("close"))
    bar._apply_form(fb._FORM_EDGE_PANEL)
    monkeypatch.setattr(fb, "QCursor", _FarCursor)
    for _ in range(8):
        bar._hover_tick()
    assert collapsed


def test_probe_is_noop_off_windows(monkeypatch):
    """非 Windows 平台不做进程枚举（避免 NameError / 无意义的系统调用）。"""
    monkeypatch.setattr(fb.sys, "platform", "linux")
    assert fb._is_cloudmusic_running() is False
