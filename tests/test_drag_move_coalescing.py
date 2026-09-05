# -*- coding: utf-8 -*-
"""普通拖拽合帧（B1）与 moveEvent 同帧合并（B3）回归测试。

B1：高回报率鼠标（125-1000Hz）会在一个显示帧内触发多次 mouseMoveEvent。
普通拖拽不再逐事件 self.move，而是只记录最新目标位置，由 ~120Hz timer
消费最新目标（永远只消费最新、丢弃中间过期位置）；松手/进入弹弓等拖拽
结束路径必须强制处理最后一次位置并停止 timer。物理拖拽（16ms physics
timer 驱动）不参与合帧，Shift/锁位/弹弓变体语义不变。

B3：moveEvent 中气泡重定位与 position listeners 通知做同帧合并——同一
GUI 帧内多次 moveEvent 只处理最后一次（0ms 去抖）；拖拽开始的第一帧与
松手后的位置立即同步处理，不能等。碰撞提交保持原样（20Hz 节流由
test_collision_window 锁定，本文件不改动它）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QCloseEvent, QHideEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.window import DRAG_MOVE_COALESCE_MS, PetWindow
from tests.test_window_pause import FakeLibrary


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _press(pos: QPointF, global_pos: QPointF,
           modifiers=Qt.KeyboardModifier.NoModifier) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers,
    )


def _move(pos: QPointF, global_pos: QPointF,
          buttons=Qt.MouseButton.LeftButton,
          modifiers=Qt.KeyboardModifier.NoModifier) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseMove, pos, global_pos,
        Qt.MouseButton.NoButton, buttons, modifiers,
    )


def _release(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _right_press(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, global_pos,
        Qt.MouseButton.RightButton,
        Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _make_win(app, tmp_path, **overrides):
    cfg = Config(base=tmp_path)
    for key, value in overrides.items():
        cfg.set(key, value)
    win = PetWindow(FakeLibrary(), cfg)
    win._is_in_interactive_area = lambda pos: True  # 测试聚焦拖拽判定
    return win


def _drag_to(win, *globals_):
    """从按下到拖动：首帧跨阈值（立即跟手），随后只记录最新目标。"""
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    for gx, gy in globals_:
        win.mouseMoveEvent(_move(QPointF(gx - 300, gy - 200), QPointF(gx, gy)))


# ================================================================ B1 合帧

def test_drag_coalesce_timer_is_about_120hz(app, tmp_path):
    win = _make_win(app, tmp_path)
    # 8ms ≈ 125Hz：每显示帧至多消费一次最新目标
    assert 1000 / win._drag_move_timer.interval() >= 120.0
    assert win._drag_move_timer.interval() == DRAG_MOVE_COALESCE_MS
    assert win._drag_move_pending is None
    win.close()
    app.processEvents()


def test_normal_drag_records_latest_target_and_drops_intermediate(app, tmp_path):
    win = _make_win(app, tmp_path)
    start = win.pos()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    # 越过阈值：拖拽开始的第一帧仍立即跟手（既有交互语义，测试锁定）
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    assert win._interaction_state == "DRAGGING"
    first_target = QPoint(400, 300) - win._grab_offset
    assert win.pos() == first_target
    # 同一显示帧内连续多次移动：只记录最新目标，不逐事件 move
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    win.mouseMoveEvent(_move(QPointF(80, 80), QPointF(600, 380)))
    win.mouseMoveEvent(_move(QPointF(90, 90), QPointF(700, 420)))
    assert win.pos() == first_target, "中间移动不应在消费前生效"
    assert win._drag_move_pending == QPoint(700, 420) - win._grab_offset
    assert win._drag_move_timer.isActive()
    # 一次消费：只应用最新目标，中间位置全部丢弃
    win._consume_drag_move()
    assert win.pos() == QPoint(700, 420) - win._grab_offset
    assert win._drag_move_pending is None
    win.close()
    app.processEvents()


def test_drag_timer_wired_to_consume_slot(app, tmp_path):
    """合帧 timer 的 timeout 已连接到消费槽：发射 timeout 即消费最新目标。

    不用 QTest.qWait：pytest 共享 QApplication 环境下事件循环可能不按时
    派发 8ms tick（实测偶发），用信号发射做确定性验证，避免墙钟抖动。"""
    win = _make_win(app, tmp_path)
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))  # 拖拽开始（立即跟手）
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    win.mouseMoveEvent(_move(QPointF(80, 80), QPointF(600, 380)))
    assert win._drag_move_timer.isActive()
    win._drag_move_timer.timeout.emit()  # 等价于一次真实 tick
    assert win.pos() == QPoint(600, 380) - win._grab_offset
    assert win._drag_move_pending is None
    win.close()
    app.processEvents()


def test_release_flushes_last_target_and_stops_timer(app, tmp_path):
    win = _make_win(app, tmp_path)
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    win.mouseMoveEvent(_move(QPointF(80, 80), QPointF(600, 380)))
    offset = win._grab_offset
    assert win._drag_move_timer.isActive()
    win.mouseReleaseEvent(_release(QPointF(80, 80), QPointF(620, 400)))
    # 松手位置（而非最后一次 move 的过期目标）为最终位置
    assert win.pos() == QPoint(620, 400) - offset
    assert win._dragging is False
    assert win._press_global is None
    assert win._drag_move_pending is None
    assert not win._drag_move_timer.isActive(), "松手后必须停止合帧 timer"
    win.close()
    app.processEvents()


def test_slingshot_enter_flushes_last_target_before_anchor(app, tmp_path):
    win = _make_win(app, tmp_path)
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    win.mouseMoveEvent(_move(QPointF(80, 80), QPointF(600, 380)))
    assert win._drag_move_timer.isActive()
    win.mousePressEvent(_right_press(QPointF(80, 80), QPointF(600, 380)))
    assert win._interaction_state == "SLINGSHOT_AIMING"
    # 进入瞄准前最后一次跟手位置已强制应用：锚点 = 当前窗口位置
    assert win.pos() == QPoint(600, 380) - win._grab_offset
    assert win._slingshot_anchor_pos == win.pos()
    assert win._drag_move_pending is None
    assert not win._drag_move_timer.isActive(), "进入弹弓后必须停止合帧 timer"
    win.close()
    app.processEvents()


def test_physics_drag_path_untouched(app, tmp_path):
    """物理拖拽仍由 16ms physics timer 驱动：不记录合帧目标、不启动合帧 timer。"""
    win = _make_win(app, tmp_path, drag_physics=True)
    assert win.drag_physics is True
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    assert win._interaction_state == "DRAGGING"
    assert win._physics_mode == "drag"
    assert win._physics_timer.isActive()
    assert win._drag_move_pending is None
    assert not win._drag_move_timer.isActive()
    # 拖拽中继续移动：只更新物理目标，合帧 timer 保持不参与
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    assert win._drag_target == QPoint(500, 350) - win._grab_offset
    assert win._drag_move_pending is None
    assert not win._drag_move_timer.isActive()
    win.close()
    app.processEvents()


# ================================================================ B3 同帧合并

def test_move_event_coalesces_bubble_and_listener_per_frame(app, tmp_path, monkeypatch):
    win = _make_win(app, tmp_path)
    win.show()
    app.processEvents()
    seen = []
    win.add_position_listener(lambda w: seen.append((w.x(), w.y())))
    repositioned = []
    monkeypatch.setattr(
        win._speech_bubble, "reposition",
        lambda rect: repositioned.append((rect.x(), rect.y())),
    )
    base = win.pos()
    win.move(base.x() + 10, base.y() + 10)
    win.move(base.x() + 20, base.y() + 20)
    win.move(base.x() + 30, base.y() + 30)
    assert seen == [], "同帧多次 moveEvent 不得逐事件通知监听器"
    assert repositioned == []
    assert win._position_sync_pending is True
    final = win.pos()
    app.processEvents()
    assert seen == [(final.x(), final.y())], "去抖后只处理最后一次位置"
    assert len(repositioned) == 1
    app.processEvents()
    assert seen == [(final.x(), final.y())], "无新移动不得重复通知"
    win.close()
    app.processEvents()


def test_drag_start_frame_syncs_position_immediately(app, tmp_path, monkeypatch):
    win = _make_win(app, tmp_path)
    win.show()
    app.processEvents()
    seen = []
    win.add_position_listener(lambda w: seen.append((w.x(), w.y())))
    repositioned = []
    monkeypatch.setattr(
        win._speech_bubble, "reposition",
        lambda rect: repositioned.append(rect),
    )
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    # 拖拽开始的第一帧立即同步（不等 0ms 去抖）
    assert seen == [(win.pos().x(), win.pos().y())]
    assert len(repositioned) == 1
    # 已立即同步，去抖回调随后触发时被丢弃，不重复通知
    app.processEvents()
    assert len(seen) == 1
    win.close()
    app.processEvents()


def test_release_frame_syncs_position_immediately(app, tmp_path, monkeypatch):
    win = _make_win(app, tmp_path)
    win.show()
    app.processEvents()
    seen = []
    win.add_position_listener(lambda w: seen.append((w.x(), w.y())))
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    win.mouseMoveEvent(_move(QPointF(80, 80), QPointF(600, 380)))
    offset = win._grab_offset
    win.mouseReleaseEvent(_release(QPointF(80, 80), QPointF(620, 400)))
    # 松手后的最终位置立即同步（不等去抖）
    assert seen[-1] == (win.pos().x(), win.pos().y())
    assert win.pos() == QPoint(620, 400) - offset
    win.close()
    app.processEvents()


# ================================================ 边界漏洞回归（锁定位/生命周期）

def test_lock_position_during_drag_flushes_last_target_and_stops_timer(app, tmp_path):
    """问题1：拖拽中途 set_lock_position(True) 必须等同松手——最后一次跟手位置
    立即应用、合帧 timer 停止、pending 清空，下一次 tick 不得再把窗口 move 到旧目标。"""
    win = _make_win(app, tmp_path)
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))  # 拖拽开始（立即跟手）
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    win.mouseMoveEvent(_move(QPointF(80, 80), QPointF(600, 380)))
    offset = win._grab_offset
    last_target = QPoint(600, 380) - offset
    assert win._dragging is True
    assert win._drag_move_timer.isActive()
    assert win._drag_move_pending == last_target

    win.set_lock_position(True)  # 拖拽中途锁定

    assert win.lock_position is True
    assert win._dragging is False
    assert win._press_global is None
    # 最后一次跟手位置已应用（松手语义）：窗口停在最后目标，而非过期中间位置
    assert win.pos() == last_target
    assert win._drag_move_pending is None
    assert not win._drag_move_timer.isActive(), "锁定位后合帧 timer 必须停止"
    # 即便旧 timer 幽灵触发，也不得再把窗口 move 到任何新位置
    win._consume_drag_move()
    assert win.pos() == last_target
    win.close()
    app.processEvents()


def test_hide_event_clears_drag_coalesce_and_position_sync_state(app, tmp_path):
    """问题2：平台原生 hide（hideEvent 直进路径，不经自定义 hide()/_pause_activity）
    也必须清理拖拽合帧 timer/pending 与位置同步去抖状态——hideEvent 是完整生命周期兜底。"""
    win = _make_win(app, tmp_path)
    win.show()
    app.processEvents()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    assert win._drag_move_timer.isActive()
    assert win._drag_move_pending is not None
    win._position_sync_pending = True  # 模拟存在未消费的同帧去抖
    win.hideEvent(QHideEvent())  # 平台原生 hide 的事件派发路径
    assert not win._drag_move_timer.isActive(), "hideEvent 后合帧 timer 必须停止"
    assert win._drag_move_pending is None
    assert win._position_sync_pending is False, "hideEvent 必须丢弃位置同步去抖"
    win.close()
    app.processEvents()


def test_native_set_visible_false_stops_drag_coalesce_timer(app, tmp_path):
    """问题2（接线验证）：QWidget.setVisible(False)（不经自定义 hide()）必须触发
    hideEvent 兜底——合帧 timer 停止，隐藏期间不得再把窗口 move 到旧目标。"""
    win = _make_win(app, tmp_path)
    win.show()
    app.processEvents()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    assert win._drag_move_timer.isActive()
    win.setVisible(False)  # 原生隐藏直进路径
    app.processEvents()
    assert not win.isVisible()
    assert not win._drag_move_timer.isActive(), "原生 hide 后合帧 timer 必须停止"
    assert win._drag_move_pending is None
    win.close()
    app.processEvents()


def test_close_event_clears_drag_coalesce_and_position_sync_state(app, tmp_path):
    """问题2：closeEvent 直进路径同样兜底清理拖拽合帧与位置同步去抖状态。"""
    win = _make_win(app, tmp_path)
    win.show()
    app.processEvents()
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseMoveEvent(_move(QPointF(60, 60), QPointF(400, 300)))
    win.mouseMoveEvent(_move(QPointF(70, 70), QPointF(500, 350)))
    assert win._drag_move_timer.isActive()
    win._position_sync_pending = True  # 模拟存在未消费的同帧去抖
    win.closeEvent(QCloseEvent())  # 关闭直进路径
    assert not win._drag_move_timer.isActive(), "closeEvent 后合帧 timer 必须停止"
    assert win._drag_move_pending is None
    assert win._position_sync_pending is False, "closeEvent 必须丢弃位置同步去抖"
    win.close()
    app.processEvents()
