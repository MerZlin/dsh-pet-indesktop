# -*- coding: utf-8 -*-
"""Linux/X11（含 XWayland）逐像素输入区域控制。

问题背景（v4.3.4 实机回归）
--------------------------
v4.3.4 的 Linux 穿透实现见 ``pet/platform_passthrough.py``：
一个 Qt 定时器每 10/50ms 读 ``QCursor.pos()``，据此决定把整窗设成
``Qt::WindowTransparentForInput``（空输入区域）还是恢复输入区域。

这在 **XWayland** 下不成立：XWayland 只在指针位于 XWayland surface 上时
才更新自己的指针位置；当指针位于 GNOME 桌面、Wayland 原生应用等非
XWayland surface 上时，``XQueryPointer``（``QCursor.pos()`` 的底层）会
停在最后一次已知位置。桌宠 overlay 的输入区域平时是空的（点击穿透），
所以指针一离开 XWayland 窗口，轮询读到的位置就是陈旧的：

- 用户把鼠标移到桌宠身上，轮询却仍读到旧坐标（例如屏幕另一角）；
- 判定「光标不在宠物上」→ 输入区域继续保持空；
- 左右键点击全部穿透到下一层窗口，桌宠永远点不到。

也就是说「用轮询决定输入区域」和「输入区域本身会让 XWayland 丢失指针
位置」互相锁死。**输入区域必须由服务端根据桌宠的绘制范围直接判定**，
不能依赖全局光标位置。

修复方式
--------
不再轮询光标，而是把 window 的 **XFixes ShapeInput 输入区域**交给 X server
判定——区域是 sprite 当前帧**画出来的不透明像素**（逐行矩形并集）：

- 指针落在宠物身体像素上 → 合成器把输入交给 overlay，Qt 照常收到
  enter / move / press；
- 指针落在**包围盒内的透明区域** → 该点不在输入区域内，事件照常交给下层
  应用（不会出现「点了个寂寞」的死区）；
- 区域每帧随动画/移动/旋转重算，只看 20ms 定时拍里是否去了新帧，静止画面
  零渲染、零 X 请求；
- 不再读取全局光标，也就不存在 XWayland 位置陈旧的问题。

逐像素区域的来源：`paintEvent` 的同一条入口 `sprite.paint()` 渲染到离屏
QImage → alpha 阈值（与 `sprite_at` 的 `ALPHA_HIT_THRESHOLD` 同口径）→ 逐行
压成矩形 → `XFixesCreateRegion`/`XFixesSetWindowShapeRegion`。探头/抛掷旋转、
镜像、Q 弹压缩都由 `paint()` 自己施加，本模块不复制任何变换逻辑；区域粒度
在大于 ~15 万像素的离屏图上按 2px 采样（自适应），矩形数再垂直合并一次。
拿不到逐像素轮廓时（假 sprite / 渲染失败 / numpy 不可用）退回绘制包围盒，
保证「至少点得到」。

多屏：每块屏一张 overlay，各自只计算自己的 ``sprites``；跨屏投影
（``_projections``）不参与输入区域，与旧 ``sprite_at`` 口径一致。

平台边界：仅在 Qt 平台插件为 ``xcb`` 时启用（Linux 默认如此，Wayland
会话下 ``pet/app.py`` 会强制切到 xcb）。原生 Wayland（用户显式设置
``QT_QPA_PLATFORM=wayland``）没有 XFixes，回退旧控制器。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
import threading

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter

# X11 Shape 常量：ShapeInput（XFixes 用同一个 kind 值）
_SHAPE_INPUT = 2

#: 像素级输入轮廓的 alpha 阈值：与 ``OverlayWindow.ALPHA_HIT_THRESHOLD`` /
#: ``PetSprite.alpha_at`` 的命中判据保持一致——「能点到哪」必须等于
#: 「``sprite_at`` 认哪」，否则会出现「区域接住了、Qt 却判透明」的 1px 死区。
_ALPHA_HIT_THRESHOLD = 16

#: numpy 惰性导入缓存（首次用到时才 import，~100ms 一次性成本不压启动路径）。
_np = None


def _numpy():
    """懒加载 numpy（运行时依赖；失败时返回 None，调用方退回包围盒）。"""
    global _np
    if _np is None:
        try:
            import numpy as np  # noqa: PLC0415 (刻意惰性)
        except Exception:
            return None
        _np = np
    return _np


def _image_alpha_rects(image: QImage, offset_x: int = 0,
                       offset_y: int = 0,
                       rect_scale: int = 1) -> list[tuple[int, int, int, int]] | None:
    """把 ARGB32 离屏图里 alpha >= 阈值的像素压成矩形列表。

    每行按连续不透明段切一条矩形，再把「上下相邻、x/宽完全相同」的行合并成
    更高的一条——鱼这类横向连续的身体能把高度合并掉一大半。

    **自适应采样**：图大于 ~15 万像素时每 2 像素取一个采样点（区域粒度 2px）。
    像素级精度对「点得到/点不到」的差别只在 1-2px 边缘，而 2× 降采样把像素
    扫描与矩形数都减半（真机实测 574µs→约 250µs）；小图/高分屏像素仍走 1:1。

    用 numpy 一次算完全部行的起止（``diff`` 找 0→1 / 1→0 跳变），避免逐像素
    Python 循环。numpy 不可用时返回 None（调用方退回包围盒）。
    """
    np = _numpy()
    if np is None:
        return None
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        return None
    bytes_per_line = image.bytesPerLine()
    if bytes_per_line < width * 4:
        return None
    raw = np.frombuffer(image.constBits(), dtype=np.uint8,
                        count=image.sizeInBytes())
    if raw.size < bytes_per_line * height:
        return None
    # ARGB32 在内存里是小端 BGRA：alpha 在每 4 字节的第 4 个
    alpha = raw.reshape(height, bytes_per_line)[:, :width * 4]
    alpha = alpha.reshape(height, width, 4)[:, :, 3]
    # 采样步长（图像像素）：超大图再额外隔行采样，控制 Python/请求规模。
    # ``rect_scale`` = 每个图像像素对应多少窗口物理像素（渲染下采样倍率）。
    step = 2 if width * height > 400000 else 1
    if step > 1:
        alpha = alpha[::step, ::step]
    sh, sw = alpha.shape
    mask = alpha >= _ALPHA_HIT_THRESHOLD
    # 两侧各补一列 0，让「行首/行尾的段」也有配对的跳变
    padded = np.zeros((sh, sw + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    diff = np.diff(padded, axis=1).ravel()
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    if starts.size != ends.size or starts.size == 0:
        return []
    # diff 每行长度 = sw+1（padded 是 sw+2 列，diff 少一列）；
    # 原始 x = padded 列 k-1，而 0→1 跳变恰好发生在 k-1 处 → xs = k
    stride = sw + 1
    cell = step * rect_scale  # 每个采样格覆盖的窗口物理像素
    xs = (starts % stride) * cell + offset_x
    ys = (starts // stride) * cell + offset_y
    widths = (ends - starts) * cell
    rects = list(zip(xs.tolist(), ys.tolist(), widths.tolist()))
    if cell <= 1:
        return [(x, y, w, 1) for (x, y, w) in rects]
    # 垂直合并：同一 x/宽、上下相邻的采样行并成一条（高度按 cell 累加）
    merged: list[list[int]] = []
    for (x, y, w) in rects:
        if merged and merged[-1][0] == x and merged[-1][2] == w \
                and merged[-1][1] + merged[-1][3] == y:
            merged[-1][3] += cell
        else:
            merged.append([x, y, w, cell])
    return [tuple(item) for item in merged]


class XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    ]


class XErrorEvent(ctypes.Structure):
    """Xlib ``XErrorEvent``（只取判据要用的字段）。"""

    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


#: X11 协议错误码：BadWindow（窗口在「读 winId → 发请求」之间被销毁时会命中）。
_BAD_WINDOW = 3

#: Xlib 错误处理器：**必须**吞掉 BadWindow，否则默认处理器会直接终止进程。
#:
#: 这是一个真实的时序窗口：``closeEvent`` 前窗口正常销毁，但屏迁移/测试收尾
#: 等路径可能绕过 closeEvent 用 ``deleteLater`` 销毁——我们在主线程读到 winId
#: 后、X server 处理请求前，窗口可能已经没了。Xlib 默认错误处理器打印一条
#: ``BadWindow`` 后 ``exit(1)``，桌宠会当场退出；Qt 自己走 xcb、有独立错误处理，
#: 管不到我们这条 Xlib 连接。所以自己装一个：BadWindow 静默忽略（下一次
#: ``refresh`` 会因为窗口不存在/已重建而自然收敛），其余协议错误记 WARNING
#: 但不终止进程（桌宠没有「宁可崩掉」的语义需求）。
_X_ERROR_HANDLER_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))
_X_ERROR_HANDLER = None


def _handle_x_error(display, event):
    """Xlib XErrorHandler：只记日志、不终止进程；返回 0 = 已处理。

    整个体包 try：Xlib 可能在解释器/日志系统收尾阶段回调（进程退出时的
    ``XCloseDisplay`` 也能触发排队错误），此时任何 Python 异常都只能静默。
    """
    try:
        code = int(event.contents.error_code) if event else 0
        if code != _BAD_WINDOW:
            logging.warning("X11 请求失败（error_code=%s），已忽略", code)
    except Exception:
        pass
    return 0


def _install_error_handler(x11) -> None:
    """给本进程的 Xlib 连接装错误处理器（进程级、只装一次）。"""
    global _X_ERROR_HANDLER
    if _X_ERROR_HANDLER is not None:
        return
    try:
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        x11.XSetErrorHandler.argtypes = [_X_ERROR_HANDLER_TYPE]
        _X_ERROR_HANDLER = _X_ERROR_HANDLER_TYPE(_handle_x_error)
        x11.XSetErrorHandler(_X_ERROR_HANDLER)
    except Exception:
        # 装不上就退回默认处理器；正常路径按「只对活窗口写」避免触发。
        logging.debug("安装 X11 错误处理器失败", exc_info=True)
        _X_ERROR_HANDLER = None


def _load_x11():
    """加载 libX11 / libXfixes 并声明用到的函数签名（进程内只做一次）。"""
    global _LIBS
    if _LIBS is not None:
        return _LIBS
    libx11_name = ctypes.util.find_library("X11") or "libX11.so.6"
    libxfixes_name = ctypes.util.find_library("Xfixes") or "libXfixes.so.3"
    x11 = ctypes.CDLL(libx11_name)
    xfixes = ctypes.CDLL(libxfixes_name)

    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

    xfixes.XFixesCreateRegion.restype = ctypes.c_ulong
    xfixes.XFixesCreateRegion.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(XRectangle), ctypes.c_int]
    xfixes.XFixesSetWindowShapeRegion.restype = None
    xfixes.XFixesSetWindowShapeRegion.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xfixes.XFixesDestroyRegion.restype = None
    xfixes.XFixesDestroyRegion.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    _install_error_handler(x11)
    _LIBS = (x11, xfixes)
    return _LIBS


#: 进程级共享的 X Display（多屏时每块屏一张 overlay，共用一条连接）。
#:
#: 每条 ``XOpenDisplay`` 都是一个独立的 X 连接（socket + 服务端会话资源）。
#: 多屏各开一条既浪费、也让「谁在什么时候关连接」变成多点责任；XFixes 请求
#: 只是往同一条连接里排队，共享连接没有并发问题——所有 overlay 都在 Qt 主线程
#: 创建/销毁/刷新。因此这里做引用计数：第一个控制器打开，最后一个关掉。
_DISPLAY_LOCK = threading.Lock()
_DISPLAY: ctypes.c_void_p | None = None
_DISPLAY_REFS = 0
_LIBS = None


def _acquire_display():
    """取一条可用的 X Display（引用计数 +1）；失败返回 None。"""
    global _DISPLAY, _DISPLAY_REFS
    x11, _ = _load_x11()
    with _DISPLAY_LOCK:
        if _DISPLAY is None:
            display = x11.XOpenDisplay(None)
            if not display:
                return None
            _DISPLAY = ctypes.c_void_p(display)
        _DISPLAY_REFS += 1
        return _DISPLAY


def _release_display() -> None:
    """释放一次引用；归零时真正关连接。"""
    global _DISPLAY, _DISPLAY_REFS
    x11, _ = _load_x11()
    with _DISPLAY_LOCK:
        if _DISPLAY_REFS <= 0:
            return
        _DISPLAY_REFS -= 1
        if _DISPLAY_REFS == 0 and _DISPLAY is not None:
            try:
                x11.XCloseDisplay(_DISPLAY)
            except Exception:
                logging.debug("关闭 X11 Display 失败", exc_info=True)
            _DISPLAY = None


def platform_uses_x11_input_region() -> bool:
    """当前 Qt 平台插件是否走 X11（xcb / XWayland）。

    只在 xcb 上启用本控制器：Windows/macOS 有各自实现；原生 Wayland 无
    XFixes 输入区域可用，必须回退 ``QtPerPixelInputController``。
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        from PySide6.QtGui import QGuiApplication
        # 没有 QApplication 时 platformName() 在部分 Qt 版本会返回构建默认值，
        # 不能据此判平台；调用点是 showEvent，此时 app 必然已存在。
        if QGuiApplication.instance() is None:
            return False
        return QGuiApplication.platformName() == "xcb"
    except Exception:
        return False


class X11InputRegionController:
    """把 overlay 的 XFixes 输入区域限定为 sprite 当前帧的**不透明像素**。

    接口与 ``platform_passthrough.QtPerPixelInputController`` /
    ``platform_win.WindowsPerPixelInputController`` 保持一致
    （``resume`` / ``stop`` / ``refresh`` / ``set_drag_active`` / ``close``），
    由 ``OverlayWindow.showEvent`` 按平台分派创建。
    """

    #: 区域刷新频率：50Hz。桌宠走动时区域会跟着移动；50Hz（≤20ms 滞后）
    #: 对「移过去再点」的交互足够，又不会让每个 10ms tick 都发一次
    #: XFixes + compositor commit。
    UPDATE_INTERVAL_MS = 20

    def __init__(self, window) -> None:
        self._window = window
        self._timer = QTimer(window)
        self._timer.setInterval(self.UPDATE_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._x11 = None
        self._xfixes = None
        self._display = None
        self._closed = False
        self._last_key = None
        #: sprite -> (cache_key, [(x, y, w, h)])：逐像素输入轮廓缓存（窗口物理坐标）。
        self._mask_cache = {}
        #: 合成后的区域缓存：``_region_key`` 未变时 ``_collect_rects`` 直接返回
        #: 上一次的列表（20ms 一拍的空转成本压到「一次签名比较」）。
        self._region_key = None
        self._region_rects: list[tuple[int, int, int, int]] = []
        self._x11, self._xfixes = _load_x11()
        # 多屏时每块屏一张 overlay：共用一条 X 连接（引用计数，最后一个关）
        self._display = _acquire_display()
        if not self._display:
            raise OSError("无法打开 X11 display（XOpenDisplay 返回 NULL）")

    # ---------------------------------------------------------------- 生命周期
    def resume(self) -> None:
        """显示/恢复：立即收敛一次并开表（show() 返回时输入区域已正确）。"""
        self._last_key = None
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        """隐藏/关闭：停表并把输入区域清空（隐藏窗口不吞点击）。

        显示侧 ``resume`` 会重新计算；不关闭 Display，避免显隐循环反复
        建立 X 连接。
        """
        self._timer.stop()
        self._last_key = None
        # 只能对真实窗口 ID 写区域：XID 0 不是合法窗口，XFixes 会以 BadWindow
        # 报错（Xlib 默认处理器会打日志/终止进程）。窗口已销毁时宁可不写。
        try:
            win_id = int(self._window.winId())
        except (RuntimeError, AttributeError):
            win_id = 0
        if win_id:
            self._apply_region(win_id, [])

    def close(self) -> None:
        """进程级销毁（closeEvent）：停表 + 归还共享 X 连接。"""
        if self._closed:
            return
        self.stop()
        self._closed = True
        if self._display:
            _release_display()
            self._display = None

    def set_drag_active(self, active: bool) -> None:  # noqa: ARG002 (协议占位)
        """拖拽期无需降频：输入区域由 sprite 包围盒驱动，与光标轮询无关。"""
        # 拖拽中 sprite 跟随光标移动，区域每 20ms 跟着走；Qt 已建立隐式
        # grab，指针短暂离开区域也不会丢 move/release 事件。
        return

    # ---------------------------------------------------------------- 区域计算
    def _window_physical_rect(self) -> tuple[QRect, float]:
        """窗口矩形与 DPR（X 窗口坐标是设备像素，Qt 几何是逻辑像素）。"""
        win = self._window
        dpr = float(win.devicePixelRatioF()) or 1.0
        width = max(1, int(round(win.width() * dpr)))
        height = max(1, int(round(win.height() * dpr)))
        return QRect(0, 0, width, height), dpr

    def _sprite_region(self, sprite, dpr: float):
        """该 sprite 当前帧「不透明像素」的输入矩形（窗口物理坐标）。

        把 sprite 按与 `paintEvent` 相同的入口（``sprite.paint``）渲染到离屏
        QImage，再按 alpha 压成逐行矩形：探头/抛掷旋转、镜像、Q 弹压缩都由
        ``paint`` 自己施加，这里不碰任何变换细节。

        返回 ``None`` 表示这条路走不通（假 sprite 没有 paint、离屏渲染失败、
        numpy 不可用等），调用方退回绘制包围盒（至少「点得到」，不会吞桌面）。

        缓存键 = ``sprite.input_revision()`` + DPR + 包围盒：帧/位置/旋转/缩放
        不变时直接复用矩形表，静止画面零渲染、零像素扫描。
        """
        paint = getattr(sprite, "paint", None)
        bounds_getter = getattr(sprite, "paint_bounds", None)
        if not callable(paint) or not callable(bounds_getter):
            return None
        try:
            bounds = bounds_getter()
        except Exception:
            return None
        if bounds is None or bounds.isEmpty():
            return None
        revision_getter = getattr(sprite, "input_revision", None)
        try:
            revision = revision_getter() if callable(revision_getter) else None
        except Exception:
            revision = None
        cache_key = (revision, round(dpr, 6), bounds.x(), bounds.y(),
                     bounds.width(), bounds.height())
        cached = self._mask_cache.get(sprite)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        try:
            # 大图按 2 倍下采样渲染（区域粒度 2 逻辑像素，点感无差别，
            # 渲染与像素扫描都省一半以上）；小图 1:1 保精度。
            render_scale = 1
            if bounds.width() * bounds.height() * dpr * dpr > 60000:
                render_scale = 2
            pixel_ratio = dpr / render_scale
            image_w = max(1, int(round(bounds.width() * pixel_ratio)))
            image_h = max(1, int(round(bounds.height() * pixel_ratio)))
            image = QImage(image_w, image_h,
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                # 与 paintEvent 同一坐标系：逻辑坐标 → 包围盒局部 → 物理像素
                painter.scale(pixel_ratio, pixel_ratio)
                painter.translate(-bounds.x(), -bounds.y())
                paint(painter)
            finally:
                painter.end()
            rects = _image_alpha_rects(
                image,
                int(round(bounds.x() * dpr)),
                int(round(bounds.y() * dpr)),
                rect_scale=render_scale)
        except Exception:
            logging.debug("渲染 sprite 输入轮廓失败", exc_info=True)
            return None
        if rects is None:
            return None
        self._mask_cache[sprite] = (cache_key, rects)
        return rects

    def _sprite_bounds_rects(self, sprite, dpr: float):
        """兜底：绘制包围盒（拿不到逐像素轮廓时用）。"""
        getter = getattr(sprite, "paint_bounds", None) or getattr(sprite, "rect", None)
        if not callable(getter):
            return None
        try:
            bounds = getter()
        except Exception:
            return None
        if bounds is None or bounds.isEmpty():
            return None
        return [(int(round(bounds.x() * dpr)), int(round(bounds.y() * dpr)),
                 max(1, int(round(bounds.width() * dpr))),
                 max(1, int(round(bounds.height() * dpr))))]

    @staticmethod
    def _sprite_revision(sprite):
        """sprite 的输入轮廓代次（假 sprite / 未来后端没有该方法时返回 None）。"""
        getter = getattr(sprite, "input_revision", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _region_signature(self, sprites, dpr: float, window_rect: QRect):
        """本拍输入区域的「廉价签名」：只读 sprite 代次，不做任何像素工作。"""
        return (
            round(dpr, 6),
            window_rect.width(),
            window_rect.height(),
            tuple((id(sprite), self._sprite_revision(sprite))
                  for sprite in sprites),
        )

    def _collect_rects(self) -> list[tuple[int, int, int, int]]:
        """当前应接受输入的矩形（窗口物理坐标）。

        **逐像素轮廓**（不是包围盒）：桌宠周围的大片透明区域不再吞点击，
        点击直接落到下层应用；被宠物身体覆盖的像素照常由 overlay 接住。

        20ms 一拍的常态成本 = 一次签名比较（帧/位置/旋转都没变就直接返回
        上次的列表）；只有宠物动画换帧、移动、旋转或显隐变化时才重新渲染。
        """
        win = self._window
        if not win.isVisible() or bool(getattr(win, "mouse_through", False)):
            self._mask_cache.clear()
            self._region_key = None
            self._region_rects = []
            return []
        window_rect, dpr = self._window_physical_rect()
        sprites = [sprite for sprite in getattr(win, "sprites", ())
                   if getattr(sprite, "visible", True)]
        key = self._region_signature(sprites, dpr, window_rect)
        if key == self._region_key:
            return self._region_rects
        rects: list[tuple[int, int, int, int]] = []
        used = set()
        for sprite in sprites:
            sprite_rects = self._sprite_region(sprite, dpr)
            if sprite_rects is not None:
                used.add(sprite)
            else:
                sprite_rects = self._sprite_bounds_rects(sprite, dpr)
            if not sprite_rects:
                continue
            window_w, window_h = window_rect.width(), window_rect.height()
            for (x, y, width, height) in sprite_rects:
                # 手写裁剪：逐矩形建 QRect/intersected 在 100+ 矩形时是主要成本
                left = x if x > 0 else 0
                top = y if y > 0 else 0
                right = x + width
                if right > window_w:
                    right = window_w
                bottom = y + height
                if bottom > window_h:
                    bottom = window_h
                if right <= left or bottom <= top:
                    continue
                rects.append((left, top, right - left, bottom - top))
        # 已移除/隐藏 sprite 的缓存顺手清掉（多宠增删不留死角）
        if len(self._mask_cache) > len(used):
            self._mask_cache = {sp: value for sp, value in self._mask_cache.items()
                                if sp in used}
        self._region_key = key
        self._region_rects = rects
        return rects

    def refresh(self) -> None:
        """收敛一次输入区域；状态未变时零 X 请求。"""
        if self._closed or not self._display:
            return
        win = self._window
        try:
            win_id = int(win.winId())
        except (RuntimeError, AttributeError):
            return
        if win_id == 0:
            return
        rects = self._collect_rects()
        key = (win_id, tuple(rects))
        if key == self._last_key:
            return
        self._apply_region(win_id, rects)
        self._last_key = key

    def _apply_region(self, win_id: int, rects) -> None:
        if not self._display or not win_id:
            return
        count = len(rects)
        if count:
            array = (XRectangle * count)(
                *[XRectangle(x, y, w, h) for (x, y, w, h) in rects])
            region = self._xfixes.XFixesCreateRegion(
                self._display, array, count)
        else:
            region = self._xfixes.XFixesCreateRegion(
                self._display, None, 0)
        if not region:
            return
        try:
            self._xfixes.XFixesSetWindowShapeRegion(
                self._display, ctypes.c_ulong(win_id),
                _SHAPE_INPUT, 0, 0, ctypes.c_ulong(region))
            self._x11.XFlush(self._display)
        except Exception:
            logging.debug("设置 X11 输入区域失败", exc_info=True)
        finally:
            self._xfixes.XFixesDestroyRegion(
                self._display, ctypes.c_ulong(region))
