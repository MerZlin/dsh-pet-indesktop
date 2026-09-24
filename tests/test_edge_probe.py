# -*- coding: utf-8 -*-
"""边缘探头控制器/几何测试。"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect
from PySide6.QtWidgets import QApplication

from pet.edge_probe import (
    EDGE_ENGAGE_EXPOSURE,
    EDGE_ENTER_MS,
    EDGE_IDLE_SECONDS,
    EDGE_PEEK_EXPOSURE,
    EDGE_PROBE_ANGLE,
    EDGE_REENTRY_SECONDS,
    EDGE_RETURN_MS,
    EDGE_STRAIGHTEN_MS,
    OFF,
    PEEKING,
    STRAIGHTENED,
    EdgeProbeController,
    edge_side_at_rest,
    probe_body_bounds,
    probe_window_x,
)


def _qapp():
    return QApplication.instance() or QApplication([])


class Avail:
    def availableGeometry(self):
        return QRect(0, 0, 1000, 800)


class FakeCfg:
    def get(self, key, default=None):
        return default


class FakeWin(QObject):
    def __init__(self):
        super().__init__()
        self._x = 0
        self._y = 100
        self._w = 400
        self._h = 300
        self.cfg = FakeCfg()
        self.anim = "drag"
        self.idles = ["idle"]
        self.turns = ["turn"]
        self.switched = []

    def frameGeometry(self):
        return QRect(self._x, self._y, self._w, self._h)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def move(self, x, y):
        self._x = int(x)
        self._y = int(y)

    def update(self):
        pass

    def screen_available(self, *_args):
        return Avail()

    def character_local_region(self):
        return QRect(100, 0, 200, 200)

    def _frame_draw_rect(self):
        return QRect(0, 0, self._w, self._h)

    def _switch(self, name):
        self.switched.append(name)
        self.anim = name
        return True

    def _pick(self, pool):
        return pool[0]

    def _cancel_move(self):
        pass

    def _stop_physics(self):
        pass


def test_probe_window_x_left_and_right():
    local = QRect(100, 0, 200, 200)
    avail = QRect(0, 0, 1000, 800)
    # left：露出 55%，左侧 off-screen = 90px；窗口 x = 0 - 90 - 100 = -190
    assert probe_window_x("left", EDGE_PEEK_EXPOSURE, local, avail) == -190
    # left：露出 82%，off-screen = 36px；x = -136
    assert probe_window_x("left", EDGE_ENGAGE_EXPOSURE, local, avail) == -136
    # right：露出 55%，右侧 off-screen = 90px；x = 999 + 90 - 299 = 790
    assert probe_window_x("right", EDGE_PEEK_EXPOSURE, local, avail) == 790


def test_edge_side_at_rest_detects_left_and_right():
    win = FakeWin()
    avail = Avail().availableGeometry()
    win.move(-100, 100)
    assert edge_side_at_rest(win, avail) == "left"
    win.move(700, 100)
    assert edge_side_at_rest(win, avail) == "right"
    win.move(0, 100)
    assert edge_side_at_rest(win, avail) is None


def _controller_and_clock():
    times = [0.0]

    def clock():
        return times[0]

    ctrl = EdgeProbeController(FakeWin(), clock=clock)
    ctrl.enabled = True
    return ctrl, times


def test_release_at_left_edge_enters_peek_pose():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "drag"
    ctrl.on_release(was_dragging=True)
    assert ctrl.active
    assert ctrl.side == "left"
    assert ctrl.win.anim == "idle"
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.win.x() == -220
    assert ctrl.current_angle_deg() == EDGE_PROBE_ANGLE
    assert abs(ctrl.current_exposure() - EDGE_PEEK_EXPOSURE) < 1e-6
    ctrl.cancel(restore=True)


def test_release_at_right_edge_enters_peek_pose():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(700, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    assert ctrl.active
    assert ctrl.side == "right"
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.win.x() == 821
    assert ctrl.current_angle_deg() == -EDGE_PROBE_ANGLE
    assert abs(ctrl.current_exposure() - EDGE_PEEK_EXPOSURE) < 1e-6
    ctrl.cancel(restore=True)


def test_click_straightens_then_returns_after_idle():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()

    ctrl.on_clicked()
    assert ctrl.mode == "STRAIGHTENING"
    times[0] += EDGE_STRAIGHTEN_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == STRAIGHTENED
    assert ctrl.current_angle_deg() == 0.0
    assert abs(ctrl.current_exposure() - EDGE_ENGAGE_EXPOSURE) < 1e-6
    assert abs(ctrl._idle_remaining - EDGE_IDLE_SECONDS) < 1e-6
    assert ctrl.win.x() == -136

    times[0] += EDGE_IDLE_SECONDS + 0.1
    ctrl._on_timer()
    assert ctrl.mode == "RETURNING"
    times[0] += EDGE_RETURN_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.current_angle_deg() == EDGE_PROBE_ANGLE
    ctrl.cancel(restore=True)


def test_hidden_pause_freezes_state_and_countdown():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    ctrl.on_clicked()
    times[0] += EDGE_STRAIGHTEN_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == STRAIGHTENED
    remaining_before = ctrl._idle_remaining
    ctrl.pause()
    times[0] += 10.0
    ctrl._on_timer()  # hidden 状态应直接忽略，不消耗倒计时
    assert ctrl.mode == STRAIGHTENED
    assert abs(ctrl._idle_remaining - remaining_before) < 1e-6
    ctrl.resume()
    assert ctrl.active
    assert ctrl.mode == STRAIGHTENED
    ctrl.cancel(restore=True)


def test_drag_away_cancels_without_snap_back():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.win.x() == -220
    ctrl.on_drag_started()
    assert not ctrl.active
    assert ctrl.win.x() == -220  # 用户拖离时位置由用户接管，不 snap
    ctrl.cancel(restore=True)


def test_feature_off_cancels_and_restores_position():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.win.x() == -220
    ctrl.set_enabled(False)
    assert not ctrl.active
    assert ctrl.win.x() == -100


def _activate_probe_at_left_edge(ctrl, times):
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING


def test_collision_throw_cancels_and_settle_reenters_probe():
    """批 A：碰撞撞飞取消探头会话→落地停稳后 5 秒到期重新进入探头吸附。"""
    _qapp()
    ctrl, times = _controller_and_clock()
    _activate_probe_at_left_edge(ctrl, times)
    # 真实撞击：取消会话（物理引擎接管位置，不回拉），并标记落地后可重进。
    ctrl.cancel("collision_throw", restore=False)
    assert not ctrl.active
    assert ctrl.mode == OFF
    assert ctrl._reentry_armed
    # 落地停稳（仍静止于左边缘）→ 开始 5s 重进倒计时。
    ctrl.on_throw_settled()
    assert ctrl._reentry_active
    assert abs(ctrl._reentry_remaining - EDGE_REENTRY_SECONDS) < 1e-6
    # 倒计时内未拖拽 → 到期重新进入探头。
    times[0] += EDGE_REENTRY_SECONDS + 0.1
    ctrl._on_timer()
    assert not ctrl._reentry_active
    assert ctrl.active
    assert ctrl.mode == "ENTERING"
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.win.x() == -220
    ctrl.cancel(restore=True)


def test_collision_throw_reentry_countdown_cancelled_by_drag():
    """批 A：重进倒计时期间发生拖拽 → 作废本次倒计时，不再重新进入探头。"""
    _qapp()
    ctrl, times = _controller_and_clock()
    _activate_probe_at_left_edge(ctrl, times)
    ctrl.cancel("collision_throw", restore=False)
    ctrl.on_throw_settled()
    assert ctrl._reentry_active
    ctrl.on_drag_started()
    assert not ctrl._reentry_active
    # 倒计时已被作废：即便时间走到 5 秒也不再重新进入探头。
    times[0] += EDGE_REENTRY_SECONDS + 0.1
    ctrl._on_timer()
    assert not ctrl.active
    assert ctrl.mode == OFF
    ctrl.cancel(restore=True)


def test_non_collision_cancel_does_not_arm_reentry():
    """批 A：非碰撞导致的取消（如拖离）不标记落地后重进。"""
    _qapp()
    ctrl, times = _controller_and_clock()
    _activate_probe_at_left_edge(ctrl, times)
    ctrl.cancel("drag_away", restore=False)
    assert not ctrl._reentry_armed
    ctrl.on_throw_settled()
    assert not ctrl._reentry_active
    assert not ctrl.active
    ctrl.cancel(restore=True)


def test_collision_throw_settle_off_edge_does_not_start_countdown():
    """批 A：撞飞落地后静止于屏幕中央（非边缘）时不开始重进倒计时。"""
    _qapp()
    ctrl, times = _controller_and_clock()
    _activate_probe_at_left_edge(ctrl, times)
    ctrl.cancel("collision_throw", restore=False)
    ctrl.win.move(400, 300)  # 中央，不在左/右边缘
    ctrl.on_throw_settled()
    assert not ctrl._reentry_active
    assert not ctrl.active


# ------------------------------------------------------------ 贴边绘制偏移（#137 之后）
class DeltaWin(FakeWin):
    """带「稳定身体框 + 贴边绘制偏移」的窗口桩，复现 #137 之后的真实坐标关系。

    - character_local_region() / _frame_draw_rect() 返回**含绘制偏移**的窗口局部
      矩形（与真实 _sync_mask / _content_frame_rect 一致：mask 由按偏移绘制的帧
      生成，贴边时把身体在窗口内整体平移了一个 delta）；
    - 虚拟位置 = 实际位置 + 绘制偏移；移动时按身体框钳进可用区（同
      window_placement.move_window_towards 的口径）。
    """

    def __init__(self):
        super().__init__()
        self._w = 640
        self._h = 390
        self._body = QRect(212, 90, 216, 270)  # 画布留白：左 212 / 右 212
        self._vis = QRect(264, 124, 212, 266)  # 可见像素（窗口内容坐标，不含偏移）
        self._draw_delta = QPoint(0, 0)

    def _stable_body_local_rect(self):
        return QRect(self._body)

    def character_local_region(self):
        # 真实 _mask_bounds 是「帧按含偏移的绘制矩形渲染」后的可见像素包围盒：
        # 贴边时整体平移了一个 delta（issue #146 根因口径）。
        return QRect(self._vis).translated(self._draw_delta)

    def _frame_draw_rect(self):
        # 真实 _content_frame_rect 的 x/y 起点就是 delta（与 mask 同系，含偏移）。
        return QRect(self._draw_delta.x(), self._draw_delta.y(), self._w, self._h)

    def _virtual_pos(self):
        return QPoint(self._x + self._draw_delta.x(), self._y + self._draw_delta.y())

    def _move_window_towards(self, x, y, body_bounds=None):
        avail = self.screen_available().availableGeometry()
        bounds = avail if body_bounds is None else body_bounds
        xi = x + self._body.x()
        xi = min(max(xi, bounds.left()), bounds.right() - self._body.width() + 1) - self._body.x()
        wx = min(max(xi, avail.left()), avail.right() - self._w + 1)
        self._draw_delta = QPoint(xi - wx, 0)
        self.move(wx, y)


def _delta_controller():
    times = [0.0]
    ctrl = EdgeProbeController(DeltaWin(), clock=lambda: times[0])
    ctrl.enabled = True
    return ctrl, times


def test_vis_local_is_delta_free_even_right_after_a_big_draw_offset():
    """入场时可见区必须换算到虚拟窗口坐标（反平移 delta），不得把偏移带进分母框。

    回归背景（issue #146「回弹」）：character_local_region() 返回的 _mask_bounds
    由「按含偏移的绘制矩形渲染的帧」生成，贴边时把身体在窗口内整体平移了一个
    delta（实测 scale=1.0 贴左缘 delta=-212，mask 左缘从 264 变成 52）；直接当
    分母框用，入场第一帧就把 delta 重算平、角色跳回修复前位置。旧实现「不平移」
    与「再平移 delta」都错：前者把 delta 带进分母框，后者多减一整个画布留白。
    """
    _qapp()
    ctrl, times = _delta_controller()
    win = ctrl.win
    win.move(0, 100)
    win._draw_delta = QPoint(-212, 0)  # 贴边后的真实偏移
    ctrl.on_release(was_dragging=True)
    assert ctrl.side == "left"
    # 分母框必须等于可见区的虚拟窗口坐标（= 含偏移的 mask 反平移 delta）
    assert ctrl._vis_local == win.character_local_region().translated(-win._draw_delta)
    assert ctrl._vis_local == win._vis
    assert ctrl._vis_local.left() == win._vis.left()


def test_entry_pose_does_not_jump_back_from_the_edge():
    """松手后探头入场第一帧不得把角色推回屏幕内侧（issue #146「回弹」）。

    贴左缘（delta=-212）松手触发探头：入场前角色身体左缘贴住可用区左缘（0）。
    旧实现把含 delta 的 mask 当分母框时，入场第一帧 x≈0-(1-exposure)*w-mask.left()
    把 delta 重算成约 -62，身体左缘跳到 +150（= #137 修复前位置）；换算到虚拟
    坐标后入场第一帧身体左缘应留在边缘一侧（≤ 小正数），不得回弹进屏。
    """
    _qapp()
    ctrl, times = _delta_controller()
    win = ctrl.win

    def body_left():
        return win.x() + win._draw_delta.x() + win._stable_body_local_rect().left()

    # 贴左缘：身体框左缘 = 可用区左缘（0），delta=-212
    win._move_window_towards(-win._stable_body_local_rect().left(), 100)
    assert win._draw_delta == QPoint(-212, 0)
    assert body_left() == 0  # 贴边静止

    ctrl.on_release(was_dragging=True)
    assert ctrl.mode == "ENTERING"
    times[0] += 0.016  # 第一个过渡帧
    ctrl._on_timer()
    # 不得跳回屏幕内侧：修复前该值 ≈ +150（回弹到画布留白处）
    assert body_left() <= 5, f"入场第一帧身体左缘={body_left()}，跳回屏幕内侧（修复前 ≈ +150）"
    ctrl.cancel(restore=True)


def test_probe_body_bounds_allow_the_body_to_leave_the_screen_on_both_sides():
    """放宽区间的契约：身体框必须能整体推到屏幕外（左右对称都要够）。

    探头要把身体"藏一半出屏"，所以身体钳位区间必须比可用区宽出「一个完整身体」。
    旧算式两侧只加了一个 sbr.width() 却没有减去 sbr.x()：身体框在画布里右偏
    （shenshen 局部 x=212）时左向只放宽到 -4px，物理上不允许身体离屏超过 4px
    ——那不是"藏半边"，是把身体钉在边缘。这里直接断言区间的契约，不编造症状。
    """
    _qapp()
    ctrl, _times = _delta_controller()
    win = ctrl.win
    avail = win.screen_available().availableGeometry()
    sbr = win._stable_body_local_rect()
    bounds = probe_body_bounds(avail, sbr)
    # 身体左边界（= bounds.left() + sbr.x()）必须能到 avail.left() - sbr.width()
    assert bounds.left() + sbr.x() <= avail.left() - sbr.width(), f"左向放宽不足：身体左边界最远只能到 {bounds.left() + sbr.x()}"
    right_edge = bounds.left() + bounds.width() - 1
    assert right_edge + sbr.x() >= avail.right() + sbr.width(), f"右向放宽不足：身体右边界最远只能到 {right_edge + sbr.x()}"


def test_peek_divides_by_the_rotated_visible_box_in_the_same_frame():
    """分母框必须由**不含偏移**的帧矩形算出：含偏移会平白多出一个 delta。

    口径：可见 212×266 的框绕帧矩形中心转 45° 后，投影 bbox 宽 = (212+266)/√2
    ≈ 339；露出 55% 时虚拟窗口 x ≈ -(339×0.45 + 偏移) ≈ -133。
    若 pivot 仍带 delta（-212），bbox 左边界被推到屏幕外约 -467，x 只算到 -20
    ——角色几乎整只留在屏幕内。
    """
    _qapp()
    ctrl, times = _delta_controller()
    win = ctrl.win
    win.move(-win._stable_body_local_rect().left(), 100)
    win._draw_delta = QPoint(-212, 0)
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    vx = win._virtual_pos().x()
    assert vx <= -270, f"虚拟窗口 x={vx} 偏内（阈值 -270）：分母框仍被绘制偏移污染——旧口径下该值只有 -232，角色几乎整只留在屏幕内"
    assert vx >= -420, f"虚拟窗口 x={vx} 偏外：角色会被整只推出屏幕"
    ctrl.cancel(restore=True)

    ctrl.cancel(restore=True)
