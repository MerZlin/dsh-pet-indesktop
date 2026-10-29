# -*- coding: utf-8 -*-
"""pet/platform_x11_input.py 的纯逻辑回归（不建立真实 X 连接）。

Linux/XWayland 的输入区域现在由 sprite 绘制包围盒直接决定，不再依赖
``QCursor.pos()``。这里钉住区域计算的三条语义：

- 只收可见 sprite，且按 ``paint_bounds()``（而非 ``rect()``）取范围；
- 区域裁剪到窗口矩形；
- ``mouse_through`` / 隐藏窗口 → 空区域（点击穿透）。
"""

import ctypes
import logging

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor

from pet.platform_x11_input import (
    X11InputRegionController,
    platform_uses_x11_input_region,
)


class _FakeSprite:
    def __init__(self, rect, *, visible=True):
        self._rect = QRect(*rect)
        self.visible = visible

    def rect(self):
        return QRect(self._rect)

    def paint_bounds(self):
        return QRect(self._rect)


class _FakeWindow:
    def __init__(self, width=200, height=100, win_id=7):
        self.sprites = []
        self.mouse_through = False
        self.visible = True
        self._width = width
        self._height = height
        self._win_id = win_id

    def isVisible(self):
        return self.visible

    def width(self):
        return self._width

    def height(self):
        return self._height

    def winId(self):
        return self._win_id

    def devicePixelRatioF(self):
        return 1.0


def _controller(window):
    """绕过 __init__：测试只覆盖区域计算/收敛逻辑，不碰 X11。"""
    controller = object.__new__(X11InputRegionController)
    controller._window = window
    controller._display = 1  # 哨兵；_apply_region 被替换
    controller._closed = False
    controller._last_key = None
    controller._mask_cache = {}
    controller._region_key = None
    controller._region_rects = []
    controller._applied = []
    controller._apply_region = lambda win_id, rects: controller._applied.append(
        (win_id, tuple(rects)))
    return controller


def test_platform_probe_false_outside_xcb():
    # CI（offscreen）与 Windows/macOS 上都不应启用 X11 输入区域
    assert platform_uses_x11_input_region() is False


def test_collect_rects_uses_paint_bounds_and_clips_to_window():
    window = _FakeWindow()
    window.sprites = [
        _FakeSprite((10, 20, 30, 40)),
        _FakeSprite((-10, -5, 30, 20)),   # 越界左上：裁剪到 (0,0,20,15)
        _FakeSprite((190, 90, 50, 50)),   # 越界右下：裁剪到 (190,90,10,10)
        _FakeSprite((5, 5, 10, 10), visible=False),
    ]
    controller = _controller(window)
    # QRegion 会按扫描带切分/合并矩形，所以按「覆盖面积 + 关键点命中」判定
    rects = controller._collect_rects()
    covered = sum(w * h for _x, _y, w, h in rects)

    def covers(x, y):
        return any(rx <= x < rx + rw and ry <= y < ry + rh
                   for rx, ry, rw, rh in rects)

    assert covered == 30 * 40 + 20 * 15 + 10 * 10
    assert covers(20, 40) and covers(5, 5) and covers(195, 95)
    assert not covers(150, 50)  # 空白处不能接输入


def test_collect_rects_empty_when_hidden_or_mouse_through():
    window = _FakeWindow()
    window.sprites = [_FakeSprite((10, 20, 30, 40))]
    controller = _controller(window)
    assert controller._collect_rects() == [(10, 20, 30, 40)]
    window.mouse_through = True
    assert controller._collect_rects() == []
    window.mouse_through = False
    window.visible = False
    assert controller._collect_rects() == []


def test_refresh_only_writes_when_state_changed():
    window = _FakeWindow()
    window.sprites = [_FakeSprite((10, 20, 30, 40))]
    controller = _controller(window)
    controller.refresh()
    controller.refresh()
    assert controller._applied == [(7, ((10, 20, 30, 40),))]
    window.sprites.append(_FakeSprite((50, 50, 10, 10)))
    controller.refresh()
    rects = controller._applied[-1][1]
    assert sum(w * h for _x, _y, w, h in rects) == 30 * 40 + 10 * 10
    assert any(rx <= 20 < rx + rw and ry <= 30 < ry + rh
               for rx, ry, rw, rh in rects)
    assert any(rx <= 55 < rx + rw and ry <= 55 < ry + rh
               for rx, ry, rw, rh in rects)


def test_refresh_reapplies_after_native_window_recreation():
    window = _FakeWindow()
    window.sprites = [_FakeSprite((10, 20, 30, 40))]
    controller = _controller(window)
    controller.refresh()
    window._win_id = 8  # on_top 开关等导致原生窗口重建
    controller.refresh()
    assert controller._applied == [
        (7, ((10, 20, 30, 40),)),
        (8, ((10, 20, 30, 40),)),
    ]


class _FakeTimer:
    def stop(self):
        pass


def test_stop_never_targets_invalid_window_zero():
    """hideEvent/closeEvent 走 stop：必须对真实 XID 清区域，不能传 0。"""
    window = _FakeWindow(win_id=11)
    window.sprites = [_FakeSprite((10, 20, 30, 40))]
    controller = _controller(window)
    controller._timer = _FakeTimer()
    controller.stop()
    assert controller._applied == [(11, ())]


def test_stop_skips_x11_when_window_already_gone():
    class _DeadWindow(_FakeWindow):
        def winId(self):
            raise RuntimeError("already deleted")

    controller = _controller(_DeadWindow())
    controller._timer = _FakeTimer()
    controller.stop()
    assert controller._applied == []


def test_shared_display_is_refcounted(monkeypatch):
    """多屏每屏一张 overlay：共用一条 X 连接，最后一个控制器关掉它。"""
    from pet import platform_x11_input as module

    class _FakeX11:
        opened = 0
        closed = 0

        def XOpenDisplay(self, _name):
            _FakeX11.opened += 1
            return 0x12345678

        def XCloseDisplay(self, _handle):
            _FakeX11.closed += 1

    monkeypatch.setattr(module, "_LIBS", (_FakeX11(), object()))
    monkeypatch.setattr(module, "_DISPLAY", None)
    monkeypatch.setattr(module, "_DISPLAY_REFS", 0)

    first = module._acquire_display()
    second = module._acquire_display()
    assert first is second
    assert module._DISPLAY_REFS == 2
    assert _FakeX11.opened == 1  # 只开一次

    module._release_display()
    assert module._DISPLAY_REFS == 1
    assert module._DISPLAY is not None
    assert _FakeX11.closed == 0  # 还有人在用，不许关

    module._release_display()
    assert module._DISPLAY_REFS == 0
    assert module._DISPLAY is None
    assert _FakeX11.closed == 1

    module._release_display()  # 幂等：多释放一次不会关第二次
    assert _FakeX11.closed == 1


def test_bad_window_is_ignored_but_other_x_errors_are_logged(caplog):
    """Xlib 默认错误处理器遇 BadWindow 会 exit(1)：本模块必须自己吞掉它。

    真实触发路径：``deleteLater`` 等绕过 closeEvent 的窗口销毁与「读 winId →
    X server 处理请求」之间的时序窗口。其余协议错误记 WARNING 但不终止进程。
    """
    from pet import platform_x11_input as module

    bad_window = module.XErrorEvent()
    bad_window.error_code = module._BAD_WINDOW
    with caplog.at_level(logging.WARNING):
        assert module._handle_x_error(None, ctypes.pointer(bad_window)) == 0
    assert not caplog.records  # BadWindow 静默

    other = module.XErrorEvent()
    other.error_code = 9  # BadDrawable
    with caplog.at_level(logging.WARNING):
        assert module._handle_x_error(None, ctypes.pointer(other)) == 0
    assert any("error_code=9" in record.getMessage() for record in caplog.records)


class _PaintedSprite:
    """能真画东西的假 sprite：包围盒 100×80，只有中间 30×30 不透明。"""

    def __init__(self, bounds=(0, 0, 100, 80), opaque=(20, 20, 30, 30)):
        self._bounds = QRect(*bounds)
        self._opaque = QRect(*opaque)
        self.visible = True
        self.paints = 0
        self.revision = 1

    def rect(self):
        return QRect(self._bounds)

    def paint_bounds(self):
        return QRect(self._bounds)

    def input_revision(self):
        return (self.revision,)

    def paint(self, painter):
        self.paints += 1
        painter.fillRect(self._opaque, QColor(255, 255, 255, 255))


def test_collect_rects_is_pixel_accurate_not_just_bounding_box():
    """包围盒内透明区域必须让出输入（点击要穿透到下层应用）。"""
    window = _FakeWindow()
    sprite = _PaintedSprite()
    window.sprites = [sprite]
    controller = _controller(window)
    rects = controller._collect_rects()

    def covers(x, y):
        return any(rx <= x < rx + rw and ry <= y < ry + rh
                   for rx, ry, rw, rh in rects)

    assert covers(30, 30), "不透明像素必须接输入"
    assert not covers(5, 5), "左上角透明处必须让出输入"
    assert not covers(90, 70), "右下角透明处必须让出输入"
    # 覆盖面积应接近 30×30，而不是包围盒 100×80
    area = sum(w * h for _x, _y, w, h in rects)
    assert 30 * 30 <= area <= 30 * 30 + 64, f"输入区域面积异常: {area}"


def test_pixel_region_is_cached_until_sprite_revision_changes():
    window = _FakeWindow()
    sprite = _PaintedSprite()
    window.sprites = [sprite]
    controller = _controller(window)

    controller._collect_rects()
    controller._collect_rects()
    assert sprite.paints == 1, "帧/位置/旋转未变时不应重复离屏渲染"

    sprite.revision = 2
    controller._collect_rects()
    assert sprite.paints == 2, "代次变化后必须重算轮廓"
