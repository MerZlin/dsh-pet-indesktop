# -*- coding: utf-8 -*-
"""网易云悬浮控制器：灯效取色、形态几何、悬停开合、外框刷、平台分支。

不建真实网络/SMTC 会话：播放状态与进程探测都是 OS 边界，用替身断言。
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.music import floatbar as fb
from pet.music import spectrum


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


# ---------------------------------------------------------------- 彩虹接缝

def test_rainbow_angle_advances_without_seam(bar):
    """彩虹旋转角按固定步长累加：跨过相位 2π 回绕点时不允许跳变。

    旧实现用 ``degrees(breathe_phase * 0.9) % 360``，相位回绕会让角度一次
    跳 36°（360 × 0.9 = 324 的补角），圆盘环带上就出现一道明显分界。
    """
    bar._playing = True
    bar._spectrum_failed = True   # 别让心跳里的 _sync_spectrum 真去起采集线程
    step = math.degrees(bar._breathe_step_rad * 0.9)
    prev = bar._rainbow_angle
    for _ in range(80):   # 0.12 × 80 ≈ 9.6 rad，覆盖两次 breathe_phase 回绕
        bar._breathe_tick()
        delta = (bar._rainbow_angle - prev) % 360.0
        assert delta == pytest.approx(step, abs=1e-6)
        assert 0.0 <= bar._rainbow_angle < 360.0
        prev = bar._rainbow_angle


# ---------------------------------------------------------------- 曲名标题

def test_track_title_shows_full_song_and_falls_back(bar):
    """有曲目名就显示完整曲名（不再截断），没有会话才回落「网易云音乐」。"""
    bar._set_track_title("晴天")
    assert bar._title.text() == "晴天"
    assert bar._title.toolTip() == "晴天"

    bar._set_track_title("   ")
    assert bar._title.text() == "网易云音乐"

    long = "一首名字长到根本放不进九十六像素的测试曲目名称"
    bar._set_track_title(long)
    assert bar._title.text() == long   # 交给滚动，而不是省略号
    assert bar._title.toolTip() == long


def test_track_title_scrolls_in_a_loop(bar):
    """放不下的曲名循环滚动：走到「文本宽 + 间距」后回到起点，不回弹反方向。"""
    bar._apply_form(fb._FORM_EXPANDED, move=False)
    bar._title.resize(96, 30)
    long = "一首名字长到根本放不进九十六像素的测试曲目名称"
    bar._set_track_title(long)
    span = bar._title.fontMetrics().horizontalAdvance(long)
    cycle = span + fb._TITLE_SCROLL_GAP
    assert span > bar._title.width()

    bar._title._pause = 0
    for _ in range(20):
        bar._title.advance()
    assert bar._title._offset == pytest.approx(20.0)

    for _ in range(int(cycle)):
        bar._title._pause = 0
        bar._title.advance()
        if bar._title._pause == fb._TITLE_SCROLL_PAUSE:
            break
    assert bar._title._offset == pytest.approx(0.0)   # 回到开头，而不是反向滚回去
    assert bar._title._pause == fb._TITLE_SCROLL_PAUSE


def test_short_title_does_not_scroll(bar):
    bar._apply_form(fb._FORM_EXPANDED, move=False)
    bar._title.resize(96, 30)
    bar._set_track_title("晴天")
    for _ in range(10):
        bar._title._pause = 0
        assert bar._title.advance() is False
    assert bar._title._offset == 0.0


# ---------------------------------------------------------------- 音乐可视化

def test_viz_above_grows_window_and_shifts_children(bar):
    """「胶囊条上方」：窗口高 38 + 26，按钮/标题整体下移一条频谱带的高度。"""
    bar._viz_pos = fb._VIZ_POS_ABOVE
    bar._apply_form(fb._FORM_EXPANDED)
    assert bar.height() == fb._EXPANDED_H + fb._VIZ_H
    assert bar.width() == fb._EXPANDED_W
    assert bar._btn_prev.y() == 6 + fb._VIZ_H
    assert bar._title.y() == 4 + fb._VIZ_H

    bar.set_viz_pos(fb._VIZ_POS_INSIDE)
    assert bar.height() == fb._EXPANDED_H
    assert bar._btn_prev.y() == 6
    assert bar._title.y() == 4


def test_viz_band_progress_follows_animation(bar):
    """频谱带高度跟着几何动画的进度一起长 / 收，不能中途突然跳满。"""
    bar._viz_pos = fb._VIZ_POS_ABOVE
    bar._apply_form(fb._FORM_EXPANDED, move=False)
    bar._animating = True
    bar._anim_target = fb._FORM_DISC
    for p, expected in ((0.0, 1.0), (0.5, 0.5), (1.0, 0.0)):
        bar._anim_progress = p
        assert bar._viz_band_progress() == pytest.approx(expected)

    bar._form = fb._FORM_DISC
    bar._anim_target = fb._FORM_EXPANDED
    for p, expected in ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0)):
        bar._anim_progress = p
        assert bar._viz_band_progress() == pytest.approx(expected)

    bar._animating = False
    bar._form = fb._FORM_EXPANDED
    assert bar._viz_band_progress() == 1.0
    bar._form = fb._FORM_DISC
    assert bar._viz_band_progress() == 0.0


def test_viz_band_progress_written_by_real_animation(bar):
    """真实动画的中间帧也会写进度，窗口高度处在 38 → 64 之间。"""
    bar._viz_pos = fb._VIZ_POS_ABOVE
    bar._apply_form(fb._FORM_DISC, move=False)
    bar.set_expanded()
    anim = bar.findChild(QVariantAnimation)
    assert anim is not None
    anim.setCurrentTime(150)
    assert 0.2 < bar._anim_progress < 0.8
    assert fb._EXPANDED_H < bar.height() < fb._EXPANDED_H + fb._VIZ_H
    anim.stop()


def test_viz_levels_bounded_and_animated(bar):
    """频谱条高度恒在 0.10~1.0 之间，并且随相位推进而变形。"""
    bar._viz_phase = 0.0
    first = bar._viz_levels()
    assert len(first) == fb._VIZ_BARS
    assert all(0.10 <= level <= 1.0 for level in first)

    bar._viz_phase = 1.7
    assert bar._viz_levels() != first


def test_viz_bar_color_modes_and_alpha(bar):
    """单色叠加半透明度；呼吸在暗态更淡；彩虹每条颜色不同。"""
    bar._viz_alpha = 45
    bar._viz_color = QColor("#de3434")
    bar._viz_mode = fb._VIZ_MODE_SOLID
    solid = bar._viz_bar_color(0)
    assert (solid.red(), solid.green(), solid.blue()) == (222, 52, 52)
    assert solid.alpha() == int(round(255 * 45 / 100.0))

    bar._viz_mode = fb._VIZ_MODE_BREATHE
    bar._breathe_phase = 0.0
    dim = bar._viz_bar_color(0).alpha()
    bar._breathe_phase = math.pi
    bright = bar._viz_bar_color(0).alpha()
    assert 0 < dim < bright == int(round(255 * 45 / 100.0))

    bar._viz_mode = fb._VIZ_MODE_RAINBOW
    hues = {bar._viz_bar_color(i).hue() for i in range(4)}
    assert len(hues) > 1

    bar._viz_mode = fb._VIZ_MODE_GRADIENT
    bar._viz_color = QColor("#de3434")
    bar._viz_color2 = QColor("#ff9a3c")
    start = bar._viz_bar_color(0)
    end = bar._viz_bar_color(fb._VIZ_BARS - 1)
    assert (start.red(), start.green(), start.blue()) == (222, 52, 52)
    assert (end.red(), end.green(), end.blue()) == (255, 154, 60)
    assert start.alpha() == end.alpha() == int(round(255 * 45 / 100.0))


def test_viz_state_readers_clamp_and_fall_back(bar):
    """存档里的可视化设置只认已知值；透明度钳到 0~100，颜色非法回落默认。"""
    bar._state = {}
    assert bar._state_viz_pos() == fb._VIZ_POS_ABOVE
    assert bar._state_viz_mode() == fb._VIZ_MODE_DEFAULT
    assert bar._state_viz_alpha() == fb._VIZ_ALPHA_DEFAULT
    assert bar._state_viz_color().name() == fb._VIZ_COLOR_DEFAULT

    bar._state = {
        "viz_pos": "middle",
        "viz_mode": "neon",
        "viz_alpha": 300,
        "viz_color": "not-a-color",
    }
    assert bar._state_viz_pos() == fb._VIZ_POS_ABOVE
    assert bar._state_viz_mode() == fb._VIZ_MODE_DEFAULT
    assert bar._state_viz_alpha() == 100
    assert bar._state_viz_color().name() == fb._VIZ_COLOR_DEFAULT
    assert bar._state_viz_color2().name() == fb._VIZ_COLOR2_DEFAULT


def test_viz_levels_prefer_real_spectrum(bar):
    """有真实频谱就直接用它的 22 段电平；采集线程报 failed 时回落到程序化波形。"""

    class _Fake:
        def __init__(self, levels, failed=False):
            self._levels = levels
            self.failed = failed

        @property
        def levels(self):
            return list(self._levels)

        def stop(self):
            self._levels = [0.0] * len(self._levels)

    real = [0.05 * i for i in range(fb._VIZ_BARS)]
    bar._spectrum = _Fake(real)
    assert bar._viz_levels() == real

    bar._spectrum = _Fake([0.0] * fb._VIZ_BARS, failed=True)
    assert bar._viz_levels() != [0.0] * fb._VIZ_BARS   # 回落波形仍会动
    assert all(0.10 <= v <= 1.0 for v in bar._viz_levels())


def test_sync_spectrum_only_while_playing_and_expanded(bar, monkeypatch):
    """采集线程只在「正在播放 + 胶囊条形态」时存在，收起 / 暂停要立刻停。"""
    calls = []

    class _FakeSpectrum:
        failed = False
        levels = [0.0] * fb._VIZ_BARS

        def __init__(self, bars):
            calls.append(("init", bars))

        def start(self):
            calls.append(("start",))

        def stop(self):
            calls.append(("stop",))

    monkeypatch.setattr(fb, "_spectrum_class", lambda: _FakeSpectrum)
    bar._form = fb._FORM_EXPANDED
    bar._playing = True
    bar._sync_spectrum()
    assert calls == [("init", fb._VIZ_BARS), ("start",)]

    bar._playing = False
    bar._sync_spectrum()
    assert calls[-1] == ("stop",)
    assert bar._spectrum is None

    bar._playing = True
    bar._form = fb._FORM_DISC
    bar._sync_spectrum()
    assert ("start",) not in calls[2:]


def test_spectrum_import_failure_falls_back(bar, monkeypatch):
    """numpy / spectrum 模块不可用时只记一次失败，之后不再尝试起线程。"""
    def _boom():
        raise ImportError("spectrum unavailable")

    monkeypatch.setattr(fb, "_spectrum_class", _boom)
    bar._form = fb._FORM_EXPANDED
    bar._playing = True
    bar._sync_spectrum()
    assert bar._spectrum is None
    assert bar._spectrum_failed is True
    bar._sync_spectrum()
    assert bar._spectrum_failed is True


def test_spectrum_band_edges_cover_log_range():
    """对数分频边界：60 Hz 起步、8000 Hz 收尾、23 个严格递增的边界。"""
    edges = spectrum._band_edges(fb._VIZ_BARS)
    assert len(edges) == fb._VIZ_BARS + 1
    assert edges[0] == pytest.approx(60.0)
    assert edges[-1] == pytest.approx(8000.0)
    assert edges == sorted(edges)


def test_band_db_has_no_dead_low_bands():
    """48 段里有若干低频段窄到一个 bin 都落不进；这些段必须借邻近 bin，不能变成死条。"""
    np = __import__("numpy")
    freqs = np.fft.rfftfreq(4096, 1.0 / 48000)
    edges = spectrum._band_edges(fb._VIZ_BARS)
    centers = spectrum._band_centers(fb._VIZ_BARS)
    masks = [(freqs >= edges[i]) & (freqs < edges[i + 1]) for i in range(fb._VIZ_BARS)]
    empty = [i for i, mask in enumerate(masks) if not mask.any()]
    assert empty, "48 段下应当存在没有 bin 的空段（否则这条用例失去意义）"

    spec = np.zeros_like(freqs)
    spec[3:20] = 0.3      # 低频有真实能量
    db = spectrum._band_db(spec, freqs, centers, masks)
    for index in empty:
        assert db[index] > -60   # 不再是 -140 的死条

    quiet = np.zeros_like(freqs)
    quiet[3:20] = 0.03
    db_quiet = spectrum._band_db(quiet, freqs, centers, masks)
    assert db[empty[0]] > db_quiet[empty[0]]   # 能量变化时会跟着动


def test_viz_width_levels_fit_panel(bar):
    """5 档粗细：条数就是档位定义值；极细至少 40 条，最粗一档在 214 px 面板上仍有 10 条。"""
    for index, (_label, count) in enumerate(fb._VIZ_WIDTHS):
        bar._viz_width = index
        assert bar._viz_bar_count(214.0) == count
    assert fb._VIZ_WIDTHS[0][1] >= 40
    assert fb._VIZ_WIDTHS[-1][1] >= 10
    # 面板再窄也不能少于 10 条（最粗档），只是每条更窄
    bar._viz_width = len(fb._VIZ_WIDTHS) - 1
    assert bar._viz_bar_count(30.0) == 10


def test_viz_resample_keeps_peaks(bar):
    """48 段压成 10 条时取每组最大值，峰值不会丢；条数不少于源长度时原样返回。"""
    levels = [i / float(fb._VIZ_BARS) for i in range(fb._VIZ_BARS)]
    n = len(levels)
    squeezed = bar._viz_resample(levels, 10)
    assert len(squeezed) == 10
    assert squeezed[0] == max(levels[0:max(1, int(n / 10))])
    assert squeezed[-1] == max(levels[int(9 * n / 10):n])
    assert max(squeezed) == max(levels)
    assert bar._viz_resample(levels, n + 12) == levels


def test_rainbow_hue_loops_without_jump(bar):
    """彩虹色相独立累加并模 1：跨过 1.0 的回绕点上增量仍然恒定。"""
    bar._playing = True
    bar._spectrum_failed = True
    prev = bar._viz_hue
    for _ in range(120):   # 0.01 × 120 = 1.2，必然跨过一次回绕
        bar._breathe_tick()
        delta = (bar._viz_hue - prev) % 1.0
        assert delta == pytest.approx(fb._VIZ_HUE_STEP, abs=1e-9)
        assert 0.0 <= bar._viz_hue < 1.0
        prev = bar._viz_hue


def test_viz_switch_closes_band_and_capture(bar, monkeypatch):
    """关闭「音乐可视化」：窗口收回 38 高、不画频谱、采集线程不再启动。"""
    started = []

    class _FakeSpectrum:
        failed = False
        levels = [0.0] * fb._VIZ_BARS

        def __init__(self, bars):
            pass

        def start(self):
            started.append(1)

        def stop(self):
            pass

    monkeypatch.setattr(fb, "_spectrum_class", lambda: _FakeSpectrum)
    bar._viz_pos = fb._VIZ_POS_ABOVE
    bar._apply_form(fb._FORM_EXPANDED, move=False)
    assert bar.height() == fb._EXPANDED_H + fb._VIZ_H

    bar.set_viz_enabled(False)
    assert bar._viz_on is False
    assert bar._viz_band_h() == 0
    assert bar.height() == fb._EXPANDED_H

    bar._playing = True
    bar._sync_spectrum()
    assert started == []


def test_title_switch_falls_back_to_fixed_label(bar):
    """关闭「显示当前曲名」：标题固定「网易云音乐」，后续曲名不再覆盖它。"""
    bar._set_track_title("晴天")
    assert bar._title.text() == "晴天"

    bar.set_title_enabled(False)
    assert bar._title_on is False
    assert bar._title.text() == "网易云音乐"

    bar._set_track_title("七里香")
    assert bar._title.text() == "网易云音乐"


def test_viz_width_setting_persists_and_ignores_bad_values(bar, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))

    bar.set_viz_width(4)
    assert bar._viz_width == 4
    assert saved.get("viz_width") == 4

    bar.set_viz_width(9)   # 越界：不改、不写
    assert bar._viz_width == 4


def test_viz_switches_persist(bar, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))

    bar.set_viz_enabled(False)
    bar.set_title_enabled(False)
    assert saved.get("viz_enabled") is False
    assert saved.get("title_enabled") is False


def test_viz_switch_state_reader_is_strict(bar):
    """存档里的开关只认布尔；缺键 / 字符串一律按「开」。"""
    bar._state = {}
    assert bar._state_flag("viz_enabled") is True
    bar._state = {"viz_enabled": False, "title_enabled": "off"}
    assert bar._state_flag("viz_enabled") is False
    assert bar._state_flag("title_enabled") is True
    bar._state = {"viz_width": 9}
    assert bar._state_viz_width() == fb._VIZ_WIDTH_DEFAULT


def test_ring_style_state_and_setter(bar, monkeypatch):
    """圆环样式只认 dots/wave/bars；切换写入存档并重置时间平滑。"""
    bar._state = {}
    assert bar._state_ring_style() == fb._RING_STYLE_DEFAULT
    bar._state = {"ring_style": "wave"}
    assert bar._state_ring_style() == "wave"
    bar._state = {"ring_style": "bokeh"}
    assert bar._state_ring_style() == fb._RING_STYLE_DEFAULT

    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))
    bar._ring_prev = [0.5] * fb._RING_SEGMENTS
    bar.set_ring_style("bars")
    assert bar._ring_style == "bars"
    assert saved.get("ring_style") == "bars"
    assert bar._ring_prev == []
    bar.set_ring_style("nonsense")
    assert bar._ring_style == "bars"


def test_ring_gain_state_and_setter(bar, monkeypatch):
    """幅度档位只认 small/mid/large，逐档 ×3；切换写入存档，非法值回落默认。"""
    bar._state = {}
    assert bar._state_ring_gain() == fb._RING_GAIN_DEFAULT
    bar._state = {"ring_gain": "mid"}
    assert bar._state_ring_gain() == "mid"
    bar._state = {"ring_gain": "huge"}
    assert bar._state_ring_gain() == fb._RING_GAIN_DEFAULT

    factors = {key: factor for key, _label, factor in fb._RING_GAINS}
    assert factors == {"small": 1.0, "mid": 3.0, "large": 9.0}

    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))
    assert bar._ring_gain_factor() == 1.0
    bar.set_ring_gain("large")
    assert bar._ring_gain == "large"
    assert bar._ring_gain_factor() == 9.0
    assert saved.get("ring_gain") == "large"
    bar.set_ring_gain("nonsense")
    assert bar._ring_gain == "large"


def test_ring_all_gains_paint_without_error(bar):
    """三种风格 × 三档幅度都要能完整画一遍（防增益接线断裂）。"""
    from PySide6.QtGui import QImage, QPainter

    bar._playing = True
    bar._spectrum_failed = True
    bar._edge = "left"
    bar._skin = fb._SKIN_VINYL
    bar._apply_form(fb._FORM_EDGE_PANEL, move=False)
    image = QImage(bar.size(), QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    for key in ("dots", "wave", "bars"):
        bar.set_ring_style(key)
        for gain in ("small", "mid", "large"):
            bar.set_ring_gain(gain)
            bar._paint_ring_spectrum(painter, 128.0, 32.0, 31.0)
    painter.end()


def test_ring_all_styles_paint_without_error(bar):
    """三种风格在黑胶面板上都要能完整画一遍（防重构后分派断裂）。"""
    from PySide6.QtGui import QImage, QPainter

    bar._playing = True
    bar._spectrum_failed = True
    bar._edge = "left"
    bar._skin = fb._SKIN_VINYL
    bar._apply_form(fb._FORM_EDGE_PANEL, move=False)
    image = QImage(bar.size(), QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    for key in ("dots", "wave", "bars"):
        bar.set_ring_style(key)
        bar._paint_ring_spectrum(painter, 128.0, 32.0, 31.0)
    painter.end()


def _paint_ink(paint_fn):
    """把一段绘制搬进透明 QImage，返回可按像素取 alpha 的图。"""
    from PySide6.QtGui import QImage, QPainter

    image = QImage(200, 200, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    paint_fn(painter)
    painter.end()
    return image


def test_ring_wave_layers_are_sparse_and_inside_label():
    """声波层间距要达到「几个像素」量级；5 层基准都落在标签区之外，小档波峰不出盘。"""
    disc_r = 31.0
    assert fb._RING_WAVE_GAP * disc_r >= 2.0
    assert fb._RING_WAVE_TOP - fb._RING_WAVE_GAP * 4 >= fb._RING_WAVE_FLOOR
    assert fb._RING_WAVE_TOP + fb._RING_WAVE_AMP <= 0.84


def test_ring_wave_density_places_layers_at_sparse_radii(bar):
    """恒定 0.5 电平下各层基准 = TOP - GAP×n：1 层时内层位置必须空着，2 层时要有墨。"""
    shape = [0.5] * fb._RING_SEGMENTS
    color = QColor(222, 52, 52)

    one = _paint_ink(lambda p: bar._paint_ring_wave(p, 100.0, 100.0, 100.0, shape, color, 1))
    assert one.pixelColor(174, 100).alpha() > 0    # 最外层 0.74r
    assert one.pixelColor(166, 100).alpha() == 0   # 0.66r 是第二层的位子，1 层时必须是空的

    two = _paint_ink(lambda p: bar._paint_ring_wave(p, 100.0, 100.0, 100.0, shape, color, 2))
    assert two.pixelColor(166, 100).alpha() > 0

    five = _paint_ink(lambda p: bar._paint_ring_wave(p, 100.0, 100.0, 100.0, shape, color, 5))
    assert five.pixelColor(142, 100).alpha() > 0   # 第 5 层恰好落在 floor 0.42r


def test_ring_dots_density_is_concentric_layers(bar):
    """圆点密度 = 同心环层数：1 档只有最外一圈，5 档时 0.46r 内圈也有墨。"""
    shape = [1.0] * fb._RING_SEGMENTS
    color = QColor(222, 52, 52)

    one = _paint_ink(lambda p: bar._paint_ring_dots(p, 100.0, 100.0, 100.0, shape, color, 1))
    assert one.pixelColor(186, 100).alpha() > 0    # 最外圈 0.86r
    assert one.pixelColor(146, 100).alpha() == 0   # 0.46r 处没有内圈

    five = _paint_ink(lambda p: bar._paint_ring_dots(p, 100.0, 100.0, 100.0, shape, color, 5))
    assert five.pixelColor(146, 100).alpha() > 0   # 最内一圈 0.46r，仍避开 0.34r 标签
    assert five.pixelColor(136, 100).alpha() == 0  # 标签区之外不进墨


def test_ring_bars_density_changes_count_and_width(bar):
    """射线密度同时改条数与线宽：5 档比 1 档条更密、线更粗。"""
    shape = [1.0] * fb._RING_SEGMENTS
    color = QColor(222, 52, 52)

    one = _paint_ink(lambda p: bar._paint_ring_bars(p, 100.0, 100.0, 100.0, shape, color, 1))
    five = _paint_ink(lambda p: bar._paint_ring_bars(p, 100.0, 100.0, 100.0, shape, color, 5))

    # 两档在正右方向（θ=0）都有一条
    assert one.pixelColor(170, 100).alpha() > 0
    assert five.pixelColor(170, 100).alpha() > 0
    # 条数：5 档 56 条时 θ≈6.4° 处有墨，1 档 28 条时该位置是空档
    assert five.pixelColor(170, 108).alpha() > 0
    assert one.pixelColor(170, 108).alpha() == 0
    # 线宽：5 档半宽 4 px，离条中线 3 px 仍有墨；1 档半宽 1.4 px 则没有
    assert five.pixelColor(170, 103).alpha() > 0
    assert one.pixelColor(170, 103).alpha() == 0


def test_ring_menu_integrated_into_viz_menu(bar):
    """「黑胶圆环频谱」收进「音乐可视化」菜单；密度档菜单名随风格变化；颜色项叫「频谱颜色…」。

    直接调 _build_context_menu 检查菜单树——PySide6/Shiboken 类的方法
    （QMenu.exec）在 Python 层 patch 了也拦不住 C++ 调用，patch 了等于没 patch。
    """

    def submenu(menu, title):
        for act in menu.actions():
            if act.menu() is not None and act.text() == title:
                return act.menu()
        return None

    menu = bar._build_context_menu()
    viz = submenu(menu, "音乐可视化")
    extra = submenu(menu, "附加功能")
    assert viz is not None and extra is not None
    ring = submenu(viz, "黑胶圆环频谱")
    assert ring is not None                       # 整合进音乐可视化
    assert submenu(extra, "黑胶圆环频谱") is None  # 附加功能里不再出现
    labels = [act.text() for act in ring.actions()]
    assert "频谱颜色…" in labels
    assert "圆环颜色…" not in labels
    assert "圆点层数" in labels                    # 默认风格 dots

    bar.set_ring_style("wave")
    ring = submenu(submenu(bar._build_context_menu(), "音乐可视化"), "黑胶圆环频谱")
    assert any(act.text() == "声波层数" for act in ring.actions())
    bar.set_ring_style("bars")
    ring = submenu(submenu(bar._build_context_menu(), "音乐可视化"), "黑胶圆环频谱")
    assert any(act.text() == "密度" for act in ring.actions())


def test_ring_shape_is_smooth(bar):
    """锯齿状的输入经过 Catmull-Rom + 圆形滑动平均后，相邻点半径不能突变。"""
    levels = [1.0 if i % 2 else 0.0 for i in range(fb._VIZ_BARS)]
    shape = bar._ring_shape(levels)
    assert len(shape) == fb._RING_SEGMENTS
    steps = [abs(shape[i] - shape[(i + 1) % len(shape)]) for i in range(len(shape))]
    assert max(steps) < 0.25


def test_ring_state_readers_and_clamps(bar):
    """圆环数量只认 1~5，颜色非法回落默认。"""
    bar._state = {}
    assert bar._state_ring_count() == fb._RING_COUNT_DEFAULT
    assert bar._state_ring_color().name() == fb._RING_COLOR_DEFAULT
    bar._state = {"ring_count": 5}
    assert bar._state_ring_count() == 5
    bar._state = {"ring_count": 9, "ring_color": "not-a-color"}
    assert bar._state_ring_count() == fb._RING_COUNT_DEFAULT
    assert bar._state_ring_color().name() == fb._RING_COLOR_DEFAULT


def test_ring_count_setting_persists(bar, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))
    bar.set_ring_count(5)
    assert bar._ring_count == 5
    assert saved.get("ring_count") == 5
    bar.set_ring_count(0)   # 越界：不改、不写
    assert bar._ring_count == 5


def test_ring_spectrum_uses_capture_in_vinyl_panel(bar, monkeypatch):
    """黑胶面板 + 圆环开关打开时，即使胶囊频谱关掉也要起采集线程。"""
    started = []

    class _FakeSpectrum:
        failed = False
        levels = [0.0] * fb._VIZ_BARS

        def __init__(self, bars):
            pass

        def start(self):
            started.append(1)

        def stop(self):
            pass

    monkeypatch.setattr(fb, "_spectrum_class", lambda: _FakeSpectrum)
    bar._viz_on = False
    bar._ring_on = True
    bar._skin = fb._SKIN_VINYL
    bar._edge = "left"
    bar._form = fb._FORM_EDGE_PANEL
    bar._playing = True
    bar._sync_spectrum()
    assert started == [1]

    bar._ring_on = False
    bar._sync_spectrum()
    assert bar._spectrum is None


def test_ring_switch_persists(bar, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))
    bar.set_ring_enabled(False)
    assert saved.get("ring_enabled") is False


def test_viz_settings_persist(bar, monkeypatch):
    """配色 / 透明度改动写入存档；非法模式直接忽略，超范围透明度钳位后写入。"""
    saved: dict = {}
    monkeypatch.setattr(fb, "_save_state", lambda state: saved.update(state))

    bar.set_viz_mode(fb._VIZ_MODE_RAINBOW)
    assert bar._viz_mode == fb._VIZ_MODE_RAINBOW
    assert saved.get("viz_mode") == fb._VIZ_MODE_RAINBOW

    bar.set_viz_mode("neon")   # 未知模式：不改、不写
    assert bar._viz_mode == fb._VIZ_MODE_RAINBOW

    bar.set_viz_alpha(180)
    assert bar._viz_alpha == 100
    assert saved.get("viz_alpha") == 100
