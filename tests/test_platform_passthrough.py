# -*- coding: utf-8 -*-
"""非 Windows 逐像素输入穿透（``QtPerPixelInputController``）回归。

背景（发布阻断级）：新架构默认拓扑是全屏 overlay——一个全屏透明窗承载全部
sprite（``pet/overlay_window.py``）。逐像素穿透此前只有 Windows 实现，Linux/
macOS 上整屏 overlay 就是一个整屏输入区，桌面点击被整块吞掉。本文件钉三件事：

1. **判定层**（纯函数 ``passthrough_decision``）：手动穿透恒穿透 / 拖拽中恒不
   穿透 / 不可见恒不穿透 / 命中透明像素穿透 / 命中 sprite 不穿透 / 盒外不穿透；
2. **落点层**：``WA_TransparentForMouseEvents`` 与原生输入穿透标志
   （``Qt::WindowTransparentForInput``）按目标态切换，状态已是目标态时不重写，
   原生窗口重建（句柄变化）后必须重放；
3. **接线层**：``showEvent`` 在非 win32 平台创建 Qt 控制器（win32 仍是原
   控制器）；``hide``/``show`` 的 stop/resume 与 Windows 路径对称；拖拽降频与
   松手重新定档。

纪律：判定层用纯函数直测，不依赖事件循环；落点/接线的真窗口用例一律 offscreen
真 QWidget / 真 OverlayWindow 走真事件（show/hide/close、press/release），只把
「操作系统边界」（``QCursor`` 与 ``sys.platform``）换成假件；等真定时器处用宽
预算 ``_pump`` 推事件循环，不做单点 sleep 赌时序。

未在本机验证的部分（诚实登记）：X11/macOS 上 ``WA_TransparentForMouseEvents`` +
``WindowTransparentForInput`` 的实际跨应用穿透行为本机（Windows）无法执行，靠
CI ubuntu/macOS 与实机验证；见交付报告「未实测项」。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

from pet import platform_passthrough as pp
from pet.overlay_window import OverlayWindow
from tests.test_overlay_window import FakeSprite, _mouse_event

app = QApplication.instance() or QApplication([])

ATTR = Qt.WidgetAttribute.WA_TransparentForMouseEvents
FLAG = Qt.WindowType.WindowTransparentForInput
WIN_W, WIN_H = 400, 300

#: 盒内 / 盒外取样点（假窗口 mapFromGlobal 是恒等映射：局部 == 全局）
INSIDE = QPoint(10, 10)
OUTSIDE = QPoint(WIN_W + 50, WIN_H + 50)


def _pump(ms: int) -> None:
    """推进真事件循环 ms 毫秒（用于「必须什么都没发生」的否定断言）。

    注意：单次 ``processEvents()`` 在有遗留事件积压时会阻塞很久（全量套件里实测
    单次 139ms），所以本函数**只**用于「这段时间内不得有 X 次调用」这类方向安全的
    断言（多推一会儿只会让断言更强）；「等到某个状态出现」的用例一律走
    ``_wait_until``（事件同步 + 宽预算）。
    """
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)


def _wait_until(condition, *, timeout_ms: int = 5000) -> bool:
    """推事件循环直到条件成立（宽预算，CI 慢 runner 余量按数倍取）。

    用例必须「等真定时器」时用这个而不是固定时长 pump：真定时器的首拍在有事件
    积压的上下文里可能被推迟上百毫秒（实测首发 169ms），固定小预算会假红。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if condition():
            return True
        app.processEvents()
        time.sleep(0.005)
    return condition()


class _FixedCursor:
    """假光标（``platform_passthrough.QCursor`` 替身）：钉死在给定屏幕坐标。"""

    def __init__(self, pos: QPoint):
        self._pos = QPoint(pos)

    def pos(self) -> QPoint:
        return QPoint(self._pos)


class _FakeHandle:
    """假 QWindow：记录 ``setFlag`` 调用，``flags()`` 回报当前标志位。

    ``win_id`` 可改，用来模拟「平台重建原生窗口」（``QWindow::winId()`` 是活读，
    X11 上是 XID，重建即变）。
    """

    def __init__(self, win_id: int = 1) -> None:
        self.calls: list[tuple] = []
        self.win_id = win_id
        self._flags = Qt.WindowType(0)

    def winId(self) -> int:
        return self.win_id

    def setFlag(self, flag, on: bool) -> None:
        self.calls.append((flag, bool(on)))
        if on:
            self._flags |= flag
        else:
            self._flags &= ~flag

    def flags(self):
        return self._flags


class _HandleRecorder:
    """假 QWindow：初值可设（模拟「平台缓存说已经穿透」），记录 setFlag 序列。"""

    def __init__(self, win_id: int, *, flags=Qt.WindowType(0)) -> None:
        self.win_id = win_id
        self.calls: list[tuple] = []
        self._flags = flags

    def winId(self) -> int:
        return self.win_id

    def flags(self):
        return self._flags

    def setFlag(self, flag, on: bool) -> None:
        self.calls.append((flag, bool(on)))
        if on:
            self._flags |= flag
        else:
            self._flags &= ~flag


class _FakeWindow(QObject):
    """逐像素协议替身（与 platform_win 测试同口径）：只实现控制器读到的窗口事实。

    继承 ``QObject`` 是因为控制器要把 ``QTimer`` 挂到窗口上（真控制器同款）。
    """

    def __init__(self, *, transparent: bool = True, visible: bool = True) -> None:
        super().__init__()
        self.mouse_through = False
        self._press_global: QPoint | None = None
        self.transparent = transparent
        self.visible = visible
        self.win_id = 4242
        self.handle: _FakeHandle | None = None
        self.attributes: dict = {}
        self.attribute_writes: list[tuple] = []
        self.hits: list[QPoint] = []

    # ---- 控制器读到的窗口事实
    def winId(self):
        return self.win_id

    def mapFromGlobal(self, point):
        return QPoint(point)          # 恒等：局部 == 全局

    def _is_transparent_at(self, local):
        self.hits.append(QPoint(local))
        return self.transparent

    def isVisible(self):
        return self.visible

    def width(self):
        return WIN_W

    def height(self):
        return WIN_H

    # ---- 落点层读到的窗口接口
    def windowHandle(self):
        return self.handle

    def testAttribute(self, attribute):
        return bool(self.attributes.get(attribute, False))

    def setAttribute(self, attribute, on):
        self.attributes[attribute] = bool(on)
        self.attribute_writes.append((attribute, bool(on)))


def _controller(window) -> pp.QtPerPixelInputController:
    return pp.QtPerPixelInputController(window)


# ---------------------------------------------------------------- 判定层（纯函数）
#: (mouse_through, press_active, visible, transparent_pixel, 取样点, 期望)
CASES = [
    (True, False, True, False, INSIDE, True),     # 手动穿透：命中 sprite 也穿透
    (True, False, True, True, INSIDE, True),
    (False, True, True, True, INSIDE, False),     # 拖拽中：恒不穿透（事件要送达）
    (False, False, False, True, INSIDE, False),   # 不可见：恒不穿透
    (False, False, True, True, INSIDE, True),     # 命中透明像素：穿透
    (False, False, True, False, INSIDE, False),   # 命中 sprite：不穿透
    (False, False, True, True, OUTSIDE, False),   # 盒外：不穿透（样式在那里无意义）
]


@pytest.mark.parametrize(
    "mouse_through,press_active,visible,transparent,point,expected", CASES)
def test_pure_decision_truth_table(
        mouse_through, press_active, visible, transparent, point, expected):
    """纯判定层：该不该穿透与窗口无关，只看窗口事实（可离线穷举）。"""
    assert pp.passthrough_decision(
        mouse_through=mouse_through, press_active=press_active, visible=visible,
        inside_bounds=point == INSIDE, transparent_pixel=transparent,
    ) is expected


@pytest.mark.parametrize(
    "mouse_through,press_active,visible,transparent,point,expected", CASES)
def test_should_click_through_truth_table(
        mouse_through, press_active, visible, transparent, point, expected):
    """协议入口：控制器把窗口事实摊平成判定，真值表逐条一致。"""
    window = _FakeWindow(transparent=transparent, visible=visible)
    window.mouse_through = mouse_through
    window._press_global = QPoint(1, 1) if press_active else None
    controller = _controller(window)
    try:
        assert controller.should_click_through(point) is expected
    finally:
        controller.stop()


@pytest.mark.parametrize("mouse_through,press_active,visible", [
    (True, False, True),   # 手动穿透
    (False, True, True),   # 拖拽中
    (False, False, False),  # 不可见
])
def test_short_circuit_skips_pixel_hit_test(
        mouse_through, press_active, visible):
    """短路条件成立时不查任何 sprite 的 alpha（全屏 overlay 下命中判定不便宜）。"""
    window = _FakeWindow(transparent=True, visible=visible)
    window.mouse_through = mouse_through
    window._press_global = QPoint(1, 1) if press_active else None
    controller = _controller(window)
    try:
        controller.should_click_through(INSIDE)
        assert window.hits == []
    finally:
        controller.stop()


def test_outside_bounding_box_skips_pixel_hit_test():
    """盒外同样短路：判定必然「不穿透」，没必要走命中链。"""
    window = _FakeWindow(transparent=True)
    controller = _controller(window)
    try:
        assert controller.should_click_through(OUTSIDE) is False
        assert window.hits == []
    finally:
        controller.stop()


# ---------------------------------------------------------------- 落点层（属性 + 原生标志）
def _shown_widget() -> QWidget:
    widget = QWidget()
    widget.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.resize(WIN_W, WIN_H)
    widget.show()
    app.processEvents()
    return widget


def test_apply_through_toggles_attribute_and_window_flag():
    """真 QWidget：穿透态同时落到 Qt 属性与原生输入穿透标志。"""
    widget = _shown_widget()
    controller = _controller(widget)
    try:
        assert controller._apply_through(True) is True
        assert widget.testAttribute(ATTR) is True
        assert bool(widget.windowHandle().flags() & FLAG)

        assert controller._apply_through(False) is True
        assert widget.testAttribute(ATTR) is False
        assert not bool(widget.windowHandle().flags() & FLAG)
    finally:
        controller.stop()
        widget.close()


def test_apply_through_is_noop_when_state_matches():
    """已是目标态：不重写（每 10ms 一次的轮询不得变成每 10ms 一次窗口写）。"""
    widget = _shown_widget()
    controller = _controller(widget)
    try:
        assert controller._apply_through(True) is True
        assert controller._apply_through(True) is False
        assert controller._apply_through(True) is False
        assert widget.testAttribute(ATTR) is True
    finally:
        controller.stop()
        widget.close()


def test_external_attribute_write_is_converged_back():
    """未知的外部属性改写：下一个 tick 收敛回目标态（读属性是纯内存读，无系统调用）。"""
    widget = _shown_widget()
    controller = _controller(widget)
    try:
        controller._apply_through(True)
        widget.setAttribute(ATTR, False)          # 模拟外部改写
        assert controller._apply_through(True) is True
        assert widget.testAttribute(ATTR) is True
    finally:
        controller.stop()
        widget.close()


def test_external_flag_write_is_converged_back():
    """外部把原生输入穿透标志翻掉（同句柄、属性仍对）：轮询必须把它收敛回来。

    属性与标志是两个独立的判据，任一侧被外部改写都要能在下一 tick 自愈——只钉属性
    侧会漏掉「标志被平台/第三方清掉」这一类（穿透静默失效的另一种形态）。

    平台例外（issue #314）：macOS 的 QCocoaWindow 读不回
    ``WindowTransparentForInput``（``flags()`` 恒不含该位），标志侧没有读回
    通道，收敛只能靠控制器自记状态——外部翻标志在 macOS 上**不可检测**（已登记
    的平台限制，属性侧收敛仍由上一用例钉住）。macOS 下本用例改钉「不重写」
    语义：自记状态一致时每 tick 不做任何窗口写。
    """
    window = _FakeWindow(transparent=True)
    window.handle = _FakeHandle(0xA1)
    controller = _controller(window)
    try:
        assert controller._apply_through(True) is True
        assert window.handle.flags() & FLAG
        assert window.testAttribute(ATTR) is True

        window.handle.setFlag(FLAG, False)        # 外部改写：标志被翻掉，句柄未变
        if sys.platform == "darwin":
            # 读不回 → 自记状态短路，不重写也不收敛（见 docstring 的平台例外）
            assert controller._apply_through(True) is False
        else:
            assert controller._apply_through(True) is True
            assert window.handle.flags() & FLAG, "标志侧外部改写必须被收敛回目标态"
            assert window.testAttribute(ATTR) is True
    finally:
        controller.stop()


def test_recreated_native_window_replays_through_state():
    """原生窗口重建（on_top / 捕获模式改 flags）→ 句柄变化：必须重放穿透态。

    判据取 **QWindow 的 winId**（活读；X11 上重建后是新 XID），不用 QWidget 的
    winId 缓存——平台在 ``QXcbWindow::show()`` 里重建原生窗口时 widget 缓存不更新，
    用它判「句柄是否变化」会漏检。重放要真写两次：``QWindow::setFlag`` 对相同标志
    早退，单写 True 落不到新的原生窗口（与 platform_win 的「句柄变化必须重读样式」
    同款防御）。
    """
    window = _FakeWindow(transparent=True)
    window.handle = _FakeHandle(0xA1)
    controller = _controller(window)
    try:
        assert controller._apply_through(True) is True
        assert window.handle.calls == [(FLAG, True)]
        assert controller._apply_through(True) is False      # 句柄未变：跳过

        window.handle.win_id = 0xB2                          # 平台重建：新原生窗口
        assert controller._apply_through(True) is True
        assert window.handle.calls[-2:] == [(FLAG, False), (FLAG, True)]
    finally:
        controller.stop()


def test_stop_keeps_manual_mouse_through_attribute():
    """手动穿透（mouse_through）不受 stop 影响：与 Windows stop() 同口径。"""
    window = _FakeWindow(transparent=True)
    window.mouse_through = True
    controller = _controller(window)
    try:
        controller._apply_through(True)
        controller.stop()
        assert window.testAttribute(ATTR) is True
    finally:
        controller.stop()


# ---------------------------------------------------------------- 轮询档位（与 Windows 同协议）
class _CountingCursor(_FixedCursor):
    def __init__(self, pos: QPoint):
        super().__init__(pos)
        self.calls = 0

    def pos(self) -> QPoint:
        self.calls += 1
        return QPoint(self._pos)


def test_poll_tiers_match_windows_protocol(monkeypatch):
    """档位常量与 Windows 控制器**逐值同源**，三平台都断言（不再只 gate 到 Windows）。

    硬编码字面量在 posix 上钉不住任何东西：Windows 侧改了档位这里照样绿。改从
    ``platform_win`` 直接取常量——该模块 posix 可 import（``tests/test_overlay_hidden_polling.py``
    等文件本就在 Linux/macOS CI 上模块级 import 它）。
    """
    from pet.platform_win import WindowsPerPixelInputController as WinCtl

    normal = pp.QtPerPixelInputController.NORMAL_POLL_INTERVAL_MS
    idle = pp.QtPerPixelInputController.IDLE_POLL_INTERVAL_MS
    drag = pp.QtPerPixelInputController.DRAG_POLL_INTERVAL_MS
    assert normal == WinCtl.NORMAL_POLL_INTERVAL_MS
    assert idle == WinCtl.IDLE_POLL_INTERVAL_MS
    assert drag == WinCtl.DRAG_POLL_INTERVAL_MS
    assert idle > normal > 0                     # 两段式定档的前提
    assert drag > idle                           # 拖拽档最省

    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    window = _FakeWindow(transparent=True)
    controller = _controller(window)
    try:
        controller.refresh()
        assert controller._timer.interval() == normal     # 盒内：逐像素跟手
        monkeypatch.setattr(pp, "QCursor", _FixedCursor(OUTSIDE))
        controller.refresh()
        assert controller._timer.interval() == idle      # 盒外：低频空转
        assert window.attribute_writes[-1] == (ATTR, False)
    finally:
        controller.stop()


def test_drag_tier_wins_and_release_retiers(monkeypatch):
    """拖拽 100ms 档优先级最高；松手按当前位置重新定档并强制刷新一次。"""
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    window = _FakeWindow(transparent=True)
    controller = _controller(window)
    try:
        window._press_global = QPoint(INSIDE)
        controller.set_drag_active(True)
        assert controller._timer.interval() == 100
        monkeypatch.setattr(pp, "QCursor", _FixedCursor(OUTSIDE))
        controller.refresh()
        assert controller._timer.interval() == 100, "拖拽中不得被位置定档打回"

        window._press_global = None
        controller.set_drag_active(False)
        assert controller._timer.interval() == 50, "松手按当前位置定档"
    finally:
        controller.stop()


# ---------------------------------------------------------------- 接线层（showEvent 平台分派）
def test_show_event_creates_qt_controller_off_windows(monkeypatch):
    """非 win32：showEvent 创建 Qt 控制器（此前整屏 overlay 没有任何穿透落点）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    overlay = OverlayWindow()
    try:
        assert overlay._input_controller is None
        overlay.show()
        assert isinstance(overlay._input_controller, pp.QtPerPixelInputController)
        assert overlay._input_controller._timer.isActive()
    finally:
        overlay.close()


@pytest.mark.skipif(os.name != "nt", reason="win32 分派要真 user32，仅 Windows 可跑")
def test_show_event_keeps_windows_controller_on_win32():
    """win32：分配的仍是原 Windows 控制器（Windows 路径一行未改）。"""
    from pet.platform_win import WindowsPerPixelInputController

    overlay = OverlayWindow()
    try:
        overlay.show()
        assert isinstance(overlay._input_controller, WindowsPerPixelInputController)
    finally:
        overlay.close()


def test_hide_stops_and_show_resumes_qt_controller(monkeypatch):
    """hide→show 对称：隐藏期停表且不留穿透态；显示后立即收敛回穿透态。

    ``stop()`` 清穿透态是硬要求：隐藏窗口留着穿透态，下次显示到第一次 refresh
    之间会是一个「整屏吞点击」的窗口（Windows 侧同款语义）。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    overlay = OverlayWindow()
    try:
        overlay.show()
        controller = overlay._input_controller
        assert controller._timer.isActive()
        # 首次显示也走 resume 的同步收敛：show() 返回即穿透态正确
        assert overlay.testAttribute(ATTR) is True, "盒内无 sprite：必须穿透"

        overlay.hide()
        assert not controller._timer.isActive(), "隐藏期不得再轮询"
        assert overlay.testAttribute(ATTR) is False, "隐藏期不得留穿透态"

        overlay.show()
        assert overlay._input_controller is controller, "复用同一控制器（resume 路径）"
        assert controller._timer.isActive()
        # resume() 同步 refresh 一次：显示后立即就是正确穿透态，不必等定时器
        assert overlay.testAttribute(ATTR) is True, "显示后必须立即收敛回穿透态"
    finally:
        overlay.close()


def test_hidden_overlay_polls_nothing(monkeypatch):
    """隐藏后 0 次光标读取；可见期必须真在轮询（对照）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    cursor = _CountingCursor(INSIDE)
    monkeypatch.setattr(pp, "QCursor", cursor)
    overlay = OverlayWindow()
    try:
        overlay.show()
        controller = overlay._input_controller
        assert _wait_until(lambda: cursor.calls >= 2), "前提：可见期轮询真的在跑"
        overlay.hide()
        assert not controller._timer.isActive(), "隐藏即停表（确定性判据）"
        before = cursor.calls
        _pump(400)
        assert cursor.calls == before, "隐藏期不得再轮询光标"
    finally:
        overlay.close()


def test_close_releases_qt_controller(monkeypatch):
    """closeEvent：停表并复位控制器引用（与 Windows 路径同形）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    overlay = OverlayWindow()
    try:
        overlay.show()
        controller = overlay._input_controller
        overlay.close()
        assert not controller._timer.isActive()
        assert overlay._input_controller is None
        assert overlay.testAttribute(ATTR) is False
    finally:
        overlay.close()


def test_first_show_converges_through_state_synchronously(monkeypatch):
    """Minor：首次显示 show() 返回时穿透态已收敛，不等 10ms 首拍定时器。

    首拍之前整屏 overlay 仍是一个输入区（事件循环繁忙时首拍可能被推迟上百毫秒），
    所以首次创建也必须走 ``resume()`` 的同款同步收敛，与 hide→show 恢复路径同契约。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    overlay = OverlayWindow()
    try:
        overlay.show()
        assert overlay.testAttribute(ATTR) is True, "show() 返回即为穿透态"
        assert overlay._input_controller._timer.isActive()
    finally:
        overlay.close()


def test_show_reconcile_replays_through_on_rebuilt_native_window(monkeypatch):
    """Major：show 之后的延迟对账必须在**当前**原生窗口上真重放 False→True。

    接缝：X11 上置顶标志触发的原生重建发生在 ``QXcbWindow::show()``，**晚于**
    ``QWidget::showEvent`` 里第一次收敛；重建后的新窗口沿用旧的 ``m_transparent``
    缓存，``setTransparentForMouseEvents(True)`` 于是早退、空输入区域不重建，穿透
    静默失效。

    这里把平台句柄换成「新窗口 + 缓存自称已穿透」的假件，并把 ``_applied_win_id``
    设成新句柄（最坏情形：属性、标志、句柄三条判据读起来全都「已一致」，正常
    轮询会永久跳过），断言延迟对账仍然写出 False→True，且属性同样先清再立。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    overlay = OverlayWindow()
    handle = _HandleRecorder(0xB2, flags=FLAG)   # 新原生窗口：平台缓存说「已穿透」
    try:
        overlay.show()
        assert overlay.testAttribute(ATTR) is True
        controller = overlay._input_controller

        writes: list[tuple] = []
        real_set_attribute = overlay.setAttribute

        def _record_set_attribute(attribute, on):
            writes.append((attribute, bool(on)))
            real_set_attribute(attribute, on)

        monkeypatch.setattr(overlay, "setAttribute", _record_set_attribute,
                            raising=False)
        monkeypatch.setattr(overlay, "windowHandle", lambda: handle, raising=False)
        monkeypatch.setattr(overlay, "winId", lambda: 0xB2, raising=False)
        controller._applied_win_id = 0xB2        # 最坏情形：句柄判据也读不出变化

        assert _wait_until(lambda: bool(handle.calls)), "延迟对账必须自己跑起来"
        assert handle.calls == [(FLAG, False), (FLAG, True)], "必须真重放（先清再立）"
        assert writes[-2:] == [(ATTR, False), (ATTR, True)], "属性同理先清再立"
        assert overlay.testAttribute(ATTR) is True
    finally:
        overlay.close()


def test_pending_reconcile_does_not_reapply_after_hide(monkeypatch):
    """隐藏/关闭后才到达的延迟对账必须早退：不得把已清掉的穿透态写回去。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    overlay = OverlayWindow()
    try:
        overlay.show()
        overlay.hide()                    # 对账（singleShot(0)）还没跑就隐藏了
        assert overlay.testAttribute(ATTR) is False
        _pump(400)                        # 宽预算：让挂起的对账真跑起来
        assert overlay.testAttribute(ATTR) is False, "隐藏后到达的对账必须早退"
        assert not overlay._input_controller._timer.isActive()
    finally:
        overlay.close()


def test_on_top_toggle_keeps_passthrough(monkeypatch):
    """on_top 开关（``setWindowFlag`` + ``show``：原生窗口重建）后穿透态不丢。

    这是 X11 上置顶重建的真实触发路径（``overlay_shell`` 的 on_top 开关就是这么
    做的）：重建走的是「改 flags → 重新 show」，因此必须再次经过 showEvent 的
    ``resume`` 同步收敛与延迟对账，而不是靠 10ms 轮询慢慢补。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))
    overlay = OverlayWindow()
    try:
        overlay.show()
        assert overlay.testAttribute(ATTR) is True

        overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        overlay.show()
        assert overlay.testAttribute(ATTR) is True, "关置顶重建后穿透态必须仍在"

        overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        overlay.show()
        assert overlay.testAttribute(ATTR) is True, "开置顶重建后穿透态必须仍在"
    finally:
        overlay.close()


def test_press_release_symmetric_drag_tier(monkeypatch):
    """真 overlay 事件链：按下降频且强制不穿透；松手恢复档位并重新收敛穿透态。

    判定与落点全部用显式 ``refresh()`` 驱动（与定时器无关的确定性断言）；「10ms
    档在真定时器上跑」由 ``test_hidden_overlay_polls_nothing`` 覆盖。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pp, "QCursor", _FixedCursor(INSIDE))   # 透明区：本该穿透
    overlay = OverlayWindow()
    sprite = FakeSprite((200, 200), (100, 100))   # 避开钉死的光标点（10,10）
    overlay.add_sprite(sprite)
    try:
        overlay.show()
        controller = overlay._input_controller
        controller.refresh()
        assert controller._timer.interval() == 10
        assert overlay.testAttribute(ATTR) is True

        overlay.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, (230, 230)))
        assert overlay._press_global is not None
        assert controller._timer.interval() == 100, "拖拽期降频"
        controller.refresh()
        assert overlay.testAttribute(ATTR) is False, "拖拽中恒不穿透（事件必须送达）"

        overlay.mouseReleaseEvent(_mouse_event(
            QEvent.Type.MouseButtonRelease, (230, 230)))
        assert overlay._press_global is None
        assert controller._timer.interval() == 10, "松手按光标位置重新定档"
        assert overlay.testAttribute(ATTR) is True, "松手必须重新收敛穿透态"
    finally:
        overlay.close()


# ---------------------------------------------------------------- 与 Windows 控制器同为一份真值表
def test_matches_windows_controller_decisions():
    """逐条对齐：同一窗口事实下两个控制器的判定必须完全一致。

    三平台都跑：这里只读 Windows 控制器的判定（``object.__new__`` 绕过 ``__init__``，
    不碰 user32/扩展样式位），所以 posix 上同样可执行。档位常量对齐在
    ``test_poll_tiers_match_windows_protocol`` 里断言。
    """
    from pet.platform_win import WindowsPerPixelInputController as WinCtl

    for mouse_through, press_active, visible, transparent, point, expected in CASES:
        window = _FakeWindow(transparent=transparent, visible=visible)
        window.mouse_through = mouse_through
        window._press_global = QPoint(1, 1) if press_active else None

        win_ctl = object.__new__(WinCtl)          # 绕过 __init__：不碰真 user32
        win_ctl._window = window
        win_ctl._timer = QTimer()
        assert win_ctl.should_click_through(point) is expected

        qt_ctl = _controller(window)
        try:
            assert qt_ctl.should_click_through(point) is expected
        finally:
            qt_ctl.stop()


def test_macos_same_state_never_resets_window_flags(monkeypatch):
    """issue #314：macOS 读不回穿透标志（flag_ok 恒 False）时，以控制器自记
    落窗状态为准——同目标态的轮询 tick 不许再碰窗口 flags（每碰一次
    QCocoaWindow::setWindowFlags 都会伴随窗口重建并把 App 激活）。"""
    from pet import platform_passthrough as pp

    monkeypatch.setattr(pp, "_IS_MACOS", True)
    widget = _shown_widget()
    controller = _controller(widget)
    try:
        assert controller._apply_through(True) is True    # 首次落窗 True
        # 模拟 macOS 读回不可信：原生标志位直接清掉，读回自然「不是目标态」
        widget.windowHandle().setFlag(FLAG, False)
        assert controller._apply_through(True) is False,             "读回不可信时必须以自记状态为准，同状态不许再写窗口"
        assert controller._apply_through(True) is False
        assert controller._apply_through(False) is True   # 状态真变了才写
        assert controller._apply_through(False) is False
    finally:
        controller.stop()
        widget.close()


def test_non_macos_keeps_readback_convergence(monkeypatch):
    """非 macOS 平台保留「读回收敛」：外部把属性写走后，下一拍纠回来。"""
    from pet import platform_passthrough as pp

    monkeypatch.setattr(pp, "_IS_MACOS", False)
    widget = _shown_widget()
    controller = _controller(widget)
    try:
        controller._apply_through(True)
        widget.setAttribute(ATTR, False)      # 模拟外部改写
        assert controller._apply_through(True) is True, "读回发现不一致必须收敛"
        widget.windowHandle().setFlag(FLAG, False)  # 标志也被写走 → 再收敛一次
        assert controller._apply_through(True) is True
    finally:
        controller.stop()
        widget.close()
