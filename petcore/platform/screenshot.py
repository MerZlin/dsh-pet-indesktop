"""截屏：抓一块屏幕到内存 PNG（不落盘、不写日志正文）。

两条取画面路径：

- 整屏/框选走 Qt 的 ``QScreen.grabWindow(0)``，跨平台可用、无需额外依赖；
- 窗口采样走 Win32 GDI 的 ``BitBlt``，按**物理像素矩形**直接抓虚拟桌面的一块，
  因此 150%/200% 缩放、负坐标副屏都不需要逻辑/物理换算；非 Windows 退回 Qt
  抓「交集最大的屏再裁剪」。

权限由调用侧（``ScreenPort`` 的提供者）按能力声明控制。
"""

from __future__ import annotations

import base64
import ctypes
import logging
import sys

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QScreen

from ..contracts.screen import ScreenCapture

logger = logging.getLogger("petcore.platform.screenshot")

#: ``GetSystemMetrics`` 的虚拟桌面指标（物理像素，含负坐标副屏）。
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000
_HALFTONE = 4
#: 送模型前的常规降采样上限（主分支同为 768）；哈希也在这张缩略图上算。
WINDOW_MAX_EDGE = 768


def data_url(capture: ScreenCapture) -> str:
    """把 PNG 转成多模态请求可用的内联 data URL（只存在于内存）。"""
    encoded = base64.b64encode(capture.png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class ScreenshotError(RuntimeError):
    reason = "capture_failed"

    def __init__(self, reason: str = "", message: str = ""):
        if reason:
            self.reason = reason
        super().__init__(message or self.reason)


def ensure_capture_permission(*, request: bool = False) -> None:
    from .macos import capture_allowed
    if not capture_allowed(request=request):
        raise ScreenshotError("permission_denied", "请在系统设置 → 隐私与安全性 → 屏幕录制中允许桌宠（源码运行时允许终端/Python），然后重启应用。")


def _target_screen(window=None) -> QScreen | None:
    if window is not None:
        try:
            screen = window.screen()
        except Exception:
            screen = None
        if screen is not None:
            return screen
    return QGuiApplication.primaryScreen()


def encode_png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ScreenshotError("capture_failed", "屏幕内容编码失败")
    data = bytes(buffer.data())
    buffer.close()
    if not data:
        raise ScreenshotError("capture_failed", "屏幕内容为空")
    return data


_encode_png = encode_png


def grab_screen_image(screen: QScreen | None = None, window=None) -> tuple[QImage, QScreen]:
    """抓整屏并返回 ``(QImage, QScreen)``；框选覆盖层复用它冻结画面。

    ``QScreen.grabWindow`` 在 Qt6 返回 **QPixmap**（真机实测），这里统一拆成
    ``QImage`` 并复位 ``devicePixelRatio`` 为 1：冻结帧是「裸设备像素」，DPR 只在
    裁切时按屏幕换算。留着 QPixmap 会让覆盖层的 ``QPainter.drawImage`` 抛
    TypeError——整层画不出来，看起来就是一整屏灰色。
    """
    ensure_capture_permission()
    target = screen or _target_screen(window)
    if target is None:
        raise ScreenshotError("no_screen", "没有可用的显示器")
    try:
        grabbed = target.grabWindow(0)
    except Exception as exc:  # noqa: BLE001 - 平台插件失败统一成稳定 reason
        logger.debug("grabWindow failed", exc_info=True)
        raise ScreenshotError(
            "capture_failed", f"无法读取屏幕内容：{type(exc).__name__}"
        ) from exc
    if isinstance(grabbed, QPixmap):
        image = grabbed.toImage()
    elif isinstance(grabbed, QImage):
        image = grabbed
    else:
        image = QImage()
    if image.isNull():
        raise ScreenshotError("capture_failed", "无法读取屏幕内容")
    image.setDevicePixelRatio(1.0)
    return image, target


def capture_from_image(image: QImage, *, screen_name: str = "") -> ScreenCapture:
    """把已在内存里的画面编码成 ``ScreenCapture``（框选裁切后使用）。"""
    if image is None or image.isNull():
        raise ScreenshotError("capture_failed", "截图为空")
    from .foreground import foreground_app_info

    return ScreenCapture(
        png=encode_png(image),
        width=int(image.width()),
        height=int(image.height()),
        screen_name=str(screen_name or ""),
        app_info=foreground_app_info(),
    )


def capture_screen(window=None, *, screen: QScreen | None = None) -> ScreenCapture:
    """抓 ``screen``（缺省为窗口所在屏，再退主屏）的整屏内容。"""
    image, target = grab_screen_image(screen, window)
    return capture_from_image(image, screen_name=str(target.name() or ""))


def _virtual_screen_rect() -> tuple[int, int, int, int] | None:
    """虚拟桌面（所有显示器并集）的物理像素矩形；非 Windows 返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        x = int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
        y = int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
    except (AttributeError, OSError, TypeError, ValueError):
        logger.debug("virtual screen metrics failed", exc_info=True)
        return None
    if width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", ctypes.c_uint32 * 3)]


def _grab_desktop_rect(
    rect: tuple[int, int, int, int], *, max_edge: int = 0
) -> QImage:
    """用 GDI 按物理像素抓虚拟桌面的一块（Windows）。

    不经过 Qt 的逻辑坐标，所以 150%/200% 缩放、负坐标副屏都能直接对上
    ``foreground_window_info()`` 给的矩形。``max_edge`` > 0 时用 ``StretchBlt``
    等比缩到该边长以内（送模型前的常规降采样，同时让稳态采样更便宜）。
    """
    x, y, width, height = (int(value) for value in rect)
    target_w, target_h = width, height
    if max_edge and max(width, height) > int(max_edge):
        scale = float(max_edge) / float(max(width, height))
        target_w = max(1, round(width * scale))
        target_h = max(1, round(height * scale))
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    handle = ctypes.c_void_p
    user32.GetDC.restype = handle
    user32.GetDC.argtypes = [handle]
    user32.ReleaseDC.argtypes = [handle, handle]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.SetStretchBltMode.argtypes = [handle, ctypes.c_int]
    gdi32.SetBrushOrgEx.argtypes = [handle, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    gdi32.StretchBlt.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    gdi32.BitBlt.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    gdi32.GetDIBits.argtypes = [
        handle,
        handle,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(_BitmapInfo),
        ctypes.c_uint32,
    ]
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteDC.argtypes = [handle]

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise ScreenshotError("capture_failed", "无法获取屏幕设备上下文")
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, target_w, target_h)
    if not memory_dc or not bitmap:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)
        raise ScreenshotError("capture_failed", "无法创建离屏位图")
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if (target_w, target_h) != (width, height):
            gdi32.SetStretchBltMode(memory_dc, _HALFTONE)
            gdi32.SetBrushOrgEx(memory_dc, 0, 0, None)
            copied = gdi32.StretchBlt(
                memory_dc,
                0,
                0,
                target_w,
                target_h,
                screen_dc,
                x,
                y,
                width,
                height,
                _SRCCOPY | _CAPTUREBLT,
            )
        else:
            copied = gdi32.BitBlt(
                memory_dc, 0, 0, width, height, screen_dc, x, y, _SRCCOPY | _CAPTUREBLT
            )
        if not copied:
            raise ScreenshotError("capture_failed", "位图拷贝失败")
        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth = target_w
        header.biHeight = -target_h  # 负高度 = 自上而下，正好是 QImage 的行序
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB：32 位 BGRA，匹配 Format_RGB32
        info = _BitmapInfo()
        info.bmiHeader = header
        buffer = ctypes.create_string_buffer(target_w * target_h * 4)
        if not gdi32.GetDIBits(
            memory_dc, bitmap, 0, target_h, buffer, ctypes.byref(info), 0
        ):
            raise ScreenshotError("capture_failed", "像素读取失败")
        return QImage(
            buffer, target_w, target_h, target_w * 4, QImage.Format.Format_RGB32
        ).copy()
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def _screen_for_rect(rect: tuple[int, int, int, int]) -> QScreen | None:
    """非 Windows 的回退：按逻辑几何选交集最大的屏（不处理混合 DPI）。"""
    target = QGuiApplication.primaryScreen()
    best = -1
    for screen in QGuiApplication.screens():
        overlap = screen.geometry().intersected(QRect(*rect))
        area = max(0, overlap.width()) * max(0, overlap.height())
        if area > best:
            best = area
            target = screen
    return target


def _grab_rect(rect: tuple[int, int, int, int], *, max_edge: int = 0) -> QImage:
    """默认取画面实现：Windows 用 GDI，其余平台退回 Qt 抓屏 + 裁剪。"""
    if sys.platform == "win32":
        return _grab_desktop_rect(rect, max_edge=max_edge)
    x, y, width, height = (int(value) for value in rect)
    screen = _screen_for_rect((x, y, width, height))
    image, target = grab_screen_image(screen)
    geometry = target.geometry()
    ratio_x = image.width() / max(1, geometry.width())
    ratio_y = image.height() / max(1, geometry.height())
    source = QRect(
        round((x - geometry.x()) * ratio_x),
        round((y - geometry.y()) * ratio_y),
        max(1, round(width * ratio_x)),
        max(1, round(height * ratio_y)),
    ).intersected(image.rect())
    if source.isEmpty():
        raise ScreenshotError("outside_screen", "窗口不在任何显示器内")
    crop = image.copy(source)
    if max_edge and max(crop.width(), crop.height()) > int(max_edge):
        crop = crop.scaled(
            int(max_edge),
            int(max_edge),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return crop


def capture_window(
    rect: tuple[int, int, int, int],
    *,
    hwnd: int = 0,
    app_info: str = "",
    max_edge: int = WINDOW_MAX_EDGE,
    grabber=None,
) -> ScreenCapture:
    """抓前台窗口那一块画面：只在内存里，不落盘、不写日志正文。

    ``rect`` 是窗口的屏幕**物理像素**矩形 ``(x, y, w, h)``（来自
    ``foreground_window_info``）。Windows 上用 GDI ``BitBlt`` 直接抓这块物理矩形，
    不需要逻辑/物理换算；只截到不足一半（大半在屏外）时判失败，宁可不看不截错屏。
    ``grabber`` 只给测试注入。
    """
    if not rect or len(rect) != 4:
        raise ScreenshotError("no_window", "没有可抓取的窗口")
    x, y, width, height = (int(value) for value in rect)
    if width <= 0 or height <= 0:
        raise ScreenshotError("no_window", "窗口尺寸无效")
    virtual = _virtual_screen_rect()
    if virtual is not None:
        overlap = QRect(*virtual).intersected(QRect(x, y, width, height))
        if overlap.isEmpty() or overlap.width() * overlap.height() < width * height * 0.5:
            raise ScreenshotError("outside_screen", "窗口大部分不在显示器内")
        x, y, width, height = (
            overlap.x(),
            overlap.y(),
            overlap.width(),
            overlap.height(),
        )
    grab = grabber or (lambda box: _grab_rect(box, max_edge=max_edge))
    image = grab((x, y, width, height))
    if image is None or image.isNull():
        raise ScreenshotError("capture_failed", "窗口画面为空")
    from .foreground import foreground_app_info
    from .image_hash import dhash

    return ScreenCapture(
        png=encode_png(image),
        width=int(image.width()),
        height=int(image.height()),
        screen_name="",
        app_info=str(app_info or foreground_app_info()),
        hwnd=int(hwnd or 0),
        dh_hash=dhash(image),
    )


def capture_desktop(window=None) -> ScreenCapture:
    """抓**整个虚拟桌面**（所有显示器并集，物理像素）；非 Windows 退主屏。

    「看看屏幕」在主分支里是 ``ImageGrab.grab(all_screens=True)``；多屏 + 混合 DPI
    下按单块屏抓会漏掉用户正在看的那块，所以 Windows 上统一走虚拟桌面矩形，
    再由 ``shrink_for_vision()`` 封顶后送模型。
    """
    virtual = _virtual_screen_rect()
    if virtual is not None:
        try:
            image = _grab_desktop_rect(virtual)
        except ScreenshotError:
            logger.debug("virtual desktop grab failed; falling back to primary screen")
        else:
            from .foreground import foreground_app_info

            return ScreenCapture(
                png=encode_png(image),
                width=int(image.width()),
                height=int(image.height()),
                screen_name="",
                app_info=foreground_app_info(),
            )
    return capture_screen(window)


def shrink_png(png: bytes, *, max_edge: int = 1024) -> bytes:
    """按长边（物理像素）把 PNG 缩到 ``max_edge`` 以内；解码失败时原样返回。"""
    data = bytes(png or b"")
    if not data or max_edge <= 0:
        return data
    image = QImage.fromData(data, "PNG")
    if image.isNull():
        return data
    if max(image.width(), image.height()) <= int(max_edge):
        return data
    scaled = image.scaled(
        int(max_edge),
        int(max_edge),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.isNull():
        return data
    try:
        return encode_png(scaled)
    except ScreenshotError:
        return data


def sample_window(
    rect: tuple[int, int, int, int], *, max_edge: int = 192
) -> tuple[int, int, int]:
    """只算窗口画面的指纹（不编码 PNG）：``(dh_hash, width, height)``。

    稳态每个心跳都要比一次「画面变没变」，这里用 ``StretchBlt`` 直接缩到
    ``max_edge`` 以内再哈希，省掉整幅位图读取与 PNG 编码（实测 1200×800：
    ~65 ms → ~5 ms）；真正要发请求时才调 :func:`capture_window` 拿大图。
    """
    if not rect or len(rect) != 4:
        raise ScreenshotError("no_window", "没有可抓取的窗口")
    x, y, width, height = (int(value) for value in rect)
    if width <= 0 or height <= 0:
        raise ScreenshotError("no_window", "窗口尺寸无效")
    virtual = _virtual_screen_rect()
    if virtual is not None:
        overlap = QRect(*virtual).intersected(QRect(x, y, width, height))
        if overlap.isEmpty():
            raise ScreenshotError("outside_screen", "窗口不在显示器内")
        x, y, width, height = (
            overlap.x(),
            overlap.y(),
            overlap.width(),
            overlap.height(),
        )
    image = _grab_rect((x, y, width, height), max_edge=max_edge)
    from .image_hash import dhash

    return dhash(image), int(image.width()), int(image.height())
