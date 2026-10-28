"""全屏应用探测（零 Qt）：桌宠「全屏时自动隐藏」的判据。

从主分支 ``pet/platform_win.py`` 的 ``_fullscreen_geometry_hit`` /
``_fs_user_busy_state`` / ``_fg_fullscreen_probe`` 搬过来，按 petcore 分层重写：
前台窗口信息复用 :mod:`petcore.platform.foreground`（hwnd/进程/标题/DWM 矩形、
cloaked 判定），这里只补样式位、窗口类名与显示器几何。

判定链（任一条命中即「真全屏」）：

1. 前台窗口可见、非最小化、非 cloaked（``foreground_window_info`` 已过滤）；
2. 排除本进程/桌宠变体、已知覆盖层工具进程、shell 窗口、``WS_EX_TOOLWINDOW``；
3. 几何：窗口覆盖**它所在那块屏**的完整几何（含任务栏）且（无标题栏 或 置顶）；
4. 兜底：``SHQueryUserNotificationState`` 报 D3D 全屏/演示模式（3/4）。

非 Windows 一律返回「不支持」+ 明确原因，**绝不假装命中**；任何异常都在内部
吞掉（``False`` + 原因），不抛给调用方。日志只记命中与否与原因摘要，不记标题
正文，也不写进程路径。零 Qt、零联网库。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import sys

from . import foreground

logger = logging.getLogger("petcore.platform.fullscreen")

_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CAPTION = 0x00C00000  # WS_BORDER | WS_DLGFRAME（带标题栏）
_WS_EX_TOPMOST = 0x00000008  # 置顶：真全屏游戏/视频几乎必带，普通最大化窗口不带
_WS_EX_TOOLWINDOW = 0x00000080  # 工具窗口：截图覆盖层/输入法候选框/悬浮面板
_MONITOR_DEFAULTTONEAREST = 2

#: 几何判定的容差（像素）。显示器几何与 DWM 框架边界来自不同 API，边框/圆角
#: 阴影在个别驱动上会差 1–2px；容差再大就会把「贴着任务栏的最大化窗口」算成
#: 全屏（任务栏至少 30px），所以硬上限就是 2。
GEOMETRY_TOLERANCE_PX = 2

#: 只认这两种无歧义的「真全屏应用」状态。``QUNS_BUSY(2)`` 不收：Win11 下它对
#: 任意全屏置顶窗都会报 BUSY，1Hz 探测会自激成频闪（主分支 2026-09-23 实测）。
_FULLSCREEN_BUSY_STATES = (3, 4)

_SKIP_CLASSES = frozenset({
    "Progman",  # 桌面
    "WorkerW",
    "Shell_TrayWnd",  # 任务栏
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",  # 开始菜单/通知中心全屏层
})

#: 已知覆盖层工具进程（截图/取色工具的全屏监听层不是「全屏应用」）。实测
#: PixPin 打字时热键监听闪现全屏覆盖层曾致桌宠误隐藏频闪。
_SKIP_PROCESSES = frozenset({"pixpin.exe", "snipaste.exe"})


class _WinRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    """``GetMonitorInfoW`` 的 MONITORINFO（只用 rcMonitor：含任务栏的完整几何）。"""

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _WinRect),
        ("rcWork", _WinRect),
        ("dwFlags", ctypes.c_ulong),
    ]


# ------------------------------------------------------------------ 纯几何


def _edges(geom) -> tuple[float, float, float, float]:
    """``(left, top, right, bottom)``；兼容 QRect（方法）、win32 RECT（属性）与
    ``(l, t, r, b)`` 四元组（纯几何测试与调用方的便捷形式）。"""
    left = getattr(geom, "left", None)
    if left is None and not isinstance(geom, (str, bytes)):
        try:
            values = tuple(geom)
        except TypeError:
            values = ()
        if len(values) == 4:
            return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))
    top = getattr(geom, "top")
    right = getattr(geom, "right")
    bottom = getattr(geom, "bottom")
    left = left() if callable(left) else left
    top = top() if callable(top) else top
    right = right() if callable(right) else right
    bottom = bottom() if callable(bottom) else bottom
    return float(left), float(top), float(right), float(bottom)


def fullscreen_geometry_hit(
    left: float,
    top: float,
    right: float,
    bottom: float,
    geom,
    has_caption: bool,
    topmost: bool = False,
    tolerance: int = GEOMETRY_TOLERANCE_PX,
) -> bool:
    """窗口覆盖整屏几何，且（无标题栏 或 置顶）= 真全屏。

    判据组合的原因：

    - 带标题栏的普通/最大化窗口（含 Windows 自动隐藏任务栏的场景）不置顶 → 排除；
    - 真全屏游戏/视频多数去掉标题栏（无标题栏直接命中）；Unity/UE 系游戏
      （如绝区零）全屏时保留 WS_CAPTION 样式位但几乎必带 WS_EX_TOPMOST，用置顶位
      兜住；
    - 已最大化后按 F11 的窗口（IsZoomed 仍为真、标题栏被清掉）也正常命中。

    ``geom`` 兼容 QRect（方法访问）与 win32 RECT（属性访问）；容差只放宽「窗口
    边界比屏幕小 1–2px」，不改变标题栏/置顶的判据。
    """
    if has_caption and not topmost:
        return False
    gl, gt, gr, gb = _edges(geom)
    slack = max(0, int(tolerance))
    return (
        left <= gl + slack
        and top <= gt + slack
        and right >= gr - slack
        and bottom >= gb - slack
    )


def _overlap_area(
    rect: tuple[float, float, float, float],
    other: tuple[float, float, float, float],
) -> float:
    width = min(rect[2], other[2]) - max(rect[0], other[0])
    height = min(rect[3], other[3]) - max(rect[1], other[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height


def monitor_rect_for(
    rect: tuple[float, float, float, float],
    monitors,
) -> tuple[float, float, float, float] | None:
    """窗口**所在那块屏**的完整几何；与所有屏都不相交时返回 ``None``。

    判据是「相交面积最大」而不是「左上角落在哪块屏」：跨屏/副屏负坐标/竖屏旋转
    下，左上角与窗口主体经常不在同一块屏，按左上角取会把窗口和错的屏比较。
    """
    best = None
    best_area = 0.0
    for monitor in monitors:
        area = _overlap_area(rect, _edges(monitor))
        if area > best_area:
            best = _edges(monitor)
            best_area = area
    return best


def window_covers_monitor(
    rect: tuple[float, float, float, float],
    monitors,
    has_caption: bool,
    topmost: bool = False,
    tolerance: int = GEOMETRY_TOLERANCE_PX,
) -> bool:
    """``rect`` 是否**铺满它自己那块屏**；多显示器下按窗口所在屏判断。"""
    monitor = monitor_rect_for(rect, monitors)
    if monitor is None:
        return False
    return fullscreen_geometry_hit(
        rect[0], rect[1], rect[2], rect[3], monitor, has_caption, topmost, tolerance
    )


# ------------------------------------------------------------------ 平台状态


def supported() -> bool:
    """全屏探测是否可用（仅 Windows；其它平台如实报不支持）。"""
    return sys.platform == "win32"


def user_busy_state() -> tuple[bool, int]:
    """``SHQueryUserNotificationState``：Windows 自报的全屏/演示忙状态。

    与几何判定互补——几何判定在 DPI 虚拟化、跨屏、DWM 边界差异下可能漏判，而这个
    API 是 Windows 自己（Focus Assist/通知静默）判定「用户正在全屏」的依据。返回
    ``(是否全屏忙, 原始状态值)``；读不到时 ``(False, -1)``。
    """
    if not supported():
        return False, -1
    try:
        state = ctypes.c_int(0)
        shell32 = ctypes.windll.shell32
        shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        shell32.SHQueryUserNotificationState.restype = ctypes.c_long
        result = shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if result != 0:  # S_OK
            return False, -1
        return state.value in _FULLSCREEN_BUSY_STATES, int(state.value)
    except Exception:  # noqa: BLE001 - 探测失败按「没在全屏」处理
        logger.debug("SHQueryUserNotificationState failed", exc_info=True)
        return False, -1


def _window_class(hwnd) -> str:
    try:
        user32 = ctypes.windll.user32
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value
    except Exception:  # noqa: BLE001 - 类名读不到只影响排除项
        logger.debug("GetClassNameW failed", exc_info=True)
        return ""


def _window_styles(hwnd) -> tuple[bool, bool, bool]:
    """``(has_caption, topmost, tool_window)``；读失败按「无标题栏/非置顶」否认命中。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        style = int(user32.GetWindowLongW(hwnd, _GWL_STYLE) or 0)
        exstyle = int(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) or 0)
    except Exception:  # noqa: BLE001
        logger.debug("GetWindowLongW failed", exc_info=True)
        return True, False, False
    return (
        bool(style & _WS_CAPTION),
        bool(exstyle & _WS_EX_TOPMOST),
        bool(exstyle & _WS_EX_TOOLWINDOW),
    )


def _monitor_rect(hwnd):
    """窗口所在显示器的完整几何（``rcMonitor``，本进程 DPI awareness 下的像素）。"""
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = ctypes.wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(_MonitorInfo),
    ]
    monitor = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info.rcMonitor


def probe_fullscreen() -> tuple[bool, str]:
    """前台窗口全屏探测，返回 ``(是否全屏, 原因摘要)``；**永不抛异常**。

    可在任意线程调用（只碰 Win32，不触碰 Qt 对象）。原因摘要只含窗口类名、进程
    名、样式位与矩形数字——**不含窗口标题正文**。
    """
    try:
        return _probe()
    except Exception:  # noqa: BLE001 - 探测边界：绝不把异常抛给宿主
        logger.debug("fullscreen probe failed", exc_info=True)
        return False, "探测异常：已按未命中处理"


def _probe() -> tuple[bool, str]:
    if not supported():
        return False, "非 Windows：不支持全屏检测"
    info = foreground.foreground_window_info()
    if not info:
        return False, "无可判定前台窗口(不可见/最小化/cloaked)"
    rect = info.get("rect")
    if not rect:
        return False, "前台窗口矩形不可读"
    hwnd = info.get("hwnd")
    process = str(info.get("process") or "")
    lowered = process.lower()
    # 本进程与其他变体/多开的桌宠：置顶小窗几何不会误判，但 SHQueryUserNotificationState
    # 兜底需要进程名兜底排除。
    if int(info.get("pid") or 0) == os.getpid() or lowered.startswith("dsh-pet-"):
        return False, f"前台是桌宠自身 {process}"
    if lowered in _SKIP_PROCESSES:
        return False, f"覆盖层工具进程 {process}"
    window_class = _window_class(hwnd)
    if window_class in _SKIP_CLASSES:
        return False, f"shell 窗口 {window_class}"
    has_caption, topmost, tool_window = _window_styles(hwnd)
    if tool_window:
        return False, f"工具窗口 cls={window_class} proc={process}"
    x, y, width, height = (int(value) for value in rect)
    monitor = _monitor_rect(hwnd)
    if monitor is not None and fullscreen_geometry_hit(
        x, y, x + width, y + height, monitor, has_caption, topmost
    ):
        return True, f"几何覆盖 cls={window_class} proc={process}"
    busy, busy_state = user_busy_state()
    if busy:
        return (
            True,
            f"SHQueryUserNotificationState={busy_state} "
            f"cls={window_class} proc={process}",
        )
    if monitor is None:
        monitor_text = "不可读"
    else:
        monitor_text = (
            f"({monitor.left},{monitor.top},{monitor.right},{monitor.bottom})"
        )
    return False, (
        f"未命中 cls={window_class} proc={process} "
        f"caption={has_caption} topmost={topmost} "
        f"rect=({x},{y},{x + width},{y + height}) "
        f"monitor={monitor_text} busy={busy_state}"
    )


def probe_fullscreen_hit() -> bool:
    """只取布尔值的便捷入口（诊断细节见 :func:`probe_fullscreen`）。"""
    return bool(probe_fullscreen()[0])
