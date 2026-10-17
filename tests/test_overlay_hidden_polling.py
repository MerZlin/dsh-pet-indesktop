# -*- coding: utf-8 -*-
"""隐藏期不轮询（O2）：overlay 隐藏后停穿透轮询与光标可见性探测，显示即恢复。

背景（审计 O2）：隐藏的 overlay 不接收任何输入，两条探测链路却照常空转——
``WindowsPerPixelInputController`` 10/50ms 轮询（QCursor.pos + GetWindowLongW）、
``FullscreenCursorWatcher`` 光标可见性 50ms 探测。全屏探测 1Hz 表**不**停：
它是全屏避让后恢复显示的唯一路径，停掉就再也回不来。

三条隐藏路径（全屏避让 / 托盘隐藏 / 屏迁移重建）都必须 hide/show 成对，
显示侧立即刷新一次穿透态（显示后不能有一段时间吞点击）。

纪律：offscreen 真 ``OverlayWindow`` + 真壳；隐藏/显示走真事件
（hideEvent/showEvent/closeEvent），只在「等待真定时器」处用短 sleep 推进事件
循环（不是赌时序：断言是「隐藏期 0 次探测 / 可见期 ≥N 次」的宽松边界）。
"""
from __future__ import annotations

import pytest

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pet import platform_win
from pet import overlay_peripherals
from pet.overlay_peripherals import FullscreenCursorWatcher
from pet.overlay_window import OverlayWindow
from tests.test_overlay_window_capabilities import _make_shell

app = QApplication.instance() or QApplication([])


class _CountingCursor:
    """假光标（``platform_win.QCursor`` 替身）：数 refresh 的入口调用。"""

    def __init__(self, pos: QPoint):
        self._pos = QPoint(pos)
        self.calls = 0

    def pos(self):
        self.calls += 1
        return QPoint(self._pos)


class _CountingClickThrough:
    """``_set_windows_click_through`` 替身：数应用次数，不碰真实 Win32。"""

    def __init__(self):
        self.calls: list[bool] = []

    def __call__(self, hwnd, enabled):
        self.calls.append(bool(enabled))
        return True


def _pump(ms: int) -> None:
    """推进真事件循环 ms 毫秒（等真定时器跑，不做单点 sleep 等待）。"""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)


def _overlay() -> OverlayWindow:
    overlay = OverlayWindow()
    overlay.show()
    return overlay



@pytest.fixture(autouse=True)
def _isolate_visibility_gate():
    """可见性登记是进程级的：同进程先跑的用例若残留未关的可见 overlay，
    会让本文件"隐藏后光标表必须停"的断言被别人的窗口顶住。每例从空登记起步，
    结束后还原——只隔离测试间的残留，不改产品语义。"""
    gate = overlay_peripherals._overlay_visibility
    saved = list(gate._visible)
    gate._visible.clear()
    yield
    gate._visible.clear()
    for widget in saved:
        gate._visible.add(widget)

# ---------------------------------------------------------------- 穿透轮询表
# 逐像素穿透控制器由 showEvent 按平台创建（Windows → platform_win 的
# WindowsPerPixelInputController，非 Windows → platform_passthrough 的
# QtPerPixelInputController）。下面三条穿透轮询用例的替身打的是 Windows 控制器
# 的模块级入口（platform_win.QCursor / _set_windows_click_through），故仅 Windows
# 可跑；非 Windows 的同形用例见 tests/test_platform_passthrough.py。
@pytest.mark.skipif(os.name != "nt", reason="本组用例的替身打的是 Windows 控制器入口")
def test_hide_stops_through_polling_and_show_resumes():
    overlay = _overlay()
    try:
        ctl = overlay._input_controller
        assert ctl is not None and ctl._timer.isActive()
        overlay.hide()
        assert not ctl._timer.isActive(), "隐藏期穿透轮询必须停表"
        overlay.show()
        assert overlay._input_controller is not None
        assert overlay._input_controller._timer.isActive(), "显示必须恢复轮询"
    finally:
        overlay.close()


@pytest.mark.skipif(os.name != "nt", reason="本组用例的替身打的是 Windows 控制器入口")
def test_hidden_overlay_polls_nothing(monkeypatch):
    """隐藏 0.4s 内 0 次 refresh；可见窗口跑同一时长必须真在轮询（对照）。"""
    cursor = _CountingCursor(QPoint(-500, -500))
    monkeypatch.setattr(platform_win, "QCursor", cursor)
    clicks = _CountingClickThrough()
    monkeypatch.setattr(platform_win, "_set_windows_click_through", clicks)
    overlay = _overlay()
    try:
        _pump(400)
        visible_calls = cursor.calls
        assert visible_calls >= 2, "前提：可见期轮询在跑"
        overlay.hide()
        before = cursor.calls
        _pump(400)
        assert cursor.calls == before, "隐藏期不得再轮询光标"
    finally:
        overlay.close()


@pytest.mark.skipif(os.name != "nt", reason="本组用例的替身打的是 Windows 控制器入口")
def test_show_refreshes_through_state_immediately(monkeypatch):
    """显示后立即刷新一次：穿透态与当前命中口径一致，不留吞点击窗口。"""
    clicks = _CountingClickThrough()
    monkeypatch.setattr(platform_win, "_set_windows_click_through", clicks)
    overlay = _overlay()
    try:
        overlay.hide()
        clicks.calls.clear()
        overlay.show()
        assert clicks.calls, "showEvent 必须立即刷新一次穿透态"
    finally:
        overlay.close()


# ---------------------------------------------------------------- 光标可见性探测
def test_cursor_visibility_probe_stops_while_overlay_hidden():
    probes: list = []
    watcher = FullscreenCursorWatcher(
        fullscreen_probe=lambda: False,
        cursor_probe=lambda: probes.append(1) or "UNKNOWN")
    try:
        watcher.set_cursor_enabled(True)
        overlay = _overlay()
        try:
            _pump(400)
            assert probes, "前提：可见期光标探测在跑"
            seen = len(probes)
            overlay.hide()
            _pump(400)
            assert len(probes) == seen, "隐藏期不得再探测光标可见性"
            overlay.show()
            _pump(400)
            assert len(probes) > seen, "显示后光标探测必须恢复"
        finally:
            overlay.close()
    finally:
        watcher.stop()


def test_fullscreen_probe_timer_keeps_running_while_hidden(tmp_path):
    """全屏探测 1Hz 表不停：隐藏后它是恢复显示的唯一路径。"""
    shell = _make_shell(tmp_path, {"auto_hide_fullscreen": True})
    try:
        shell.overlay.show()
        assert shell._watcher._cursor_timer.isActive()
        shell._on_fullscreen_changed(True)
        assert not shell.overlay.isVisible()
        assert shell._watcher._fs_timer.isActive(), "隐藏期全屏探测必须继续跑"
        assert not shell._watcher._cursor_timer.isActive()
        shell._on_fullscreen_changed(False)
        assert shell.overlay.isVisible()
        assert shell._watcher._cursor_timer.isActive()
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 三条隐藏路径成对
def test_visible_overlay_gate_counts_real_widget_visibility():
    # 计数是进程级登记：同进程先跑的用例可能残留可见 overlay，只断言本例增量
    base = overlay_peripherals.visible_overlay_count()
    overlay = _overlay()
    try:
        assert overlay_peripherals.visible_overlay_count() == base + 1
        overlay.hide()
        assert overlay_peripherals.visible_overlay_count() == base
        overlay.show()
        assert overlay_peripherals.visible_overlay_count() == base + 1
    finally:
        overlay.close()


def test_tray_hide_and_screen_migration_stay_paired(tmp_path):
    """托盘隐藏 + 换家：闸门计数与轮询表在每一步都成对（单窗不重建窗口）。"""
    base = overlay_peripherals.visible_overlay_count()
    shell = _make_shell(tmp_path)
    shell.start()
    try:
        assert overlay_peripherals.visible_overlay_count() == base + 1
        shell.set_pet_visible(False)
        assert overlay_peripherals.visible_overlay_count() == base
        assert not shell._watcher._cursor_timer.isActive()
        shell.set_pet_visible(True)
        assert overlay_peripherals.visible_overlay_count() == base + 1
        assert shell._watcher._cursor_timer.isActive()

        old = shell.overlay
        old_ctl = old._input_controller          # 换家前捕获：这张窗自己的控制器
        shell._migrate_to_screen(QGuiApplication.primaryScreen())
        assert shell.overlay is old, "前提：单窗拓扑下换家不重建窗口"
        assert overlay_peripherals.visible_overlay_count() == base + 1, (
            "窗口没变，闸门计数不变")
        assert shell._watcher._cursor_timer.isActive()
        # 逐像素穿透轮询由 showEvent 按平台创建（见文件头注释），两个平台都要钉：
        # 换家不重建窗口 ⇒ 控制器还是同一个、仍在轮询（不产生新的空转/重复表）。
        ctl = shell.overlay._input_controller
        assert ctl is old_ctl and ctl is not None and ctl._timer.isActive()
        # 平台判据用 ``sys.platform``——与产品分派（overlay_window.showEvent 的
        # win32 门）同一个键，避免测试用了另一个键而给出错误期望。
        if sys.platform == "win32":
            from pet.platform_win import WindowsPerPixelInputController
            assert isinstance(ctl, WindowsPerPixelInputController)
        else:
            from pet.platform_passthrough import QtPerPixelInputController
            assert isinstance(ctl, QtPerPixelInputController), (
                "posix 由 Qt 控制器接管穿透（此前 posix 无任何穿透落点）")
    finally:
        shell.stop()
        shell._delete_runtime_marker()
