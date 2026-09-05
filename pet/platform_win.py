# -*- coding: utf-8 -*-
"""
Win32 平台层 —— 从 pet/window.py 剥离（结构优化批 6-3）。

纯搬移：逐行搬移不改逻辑；ctypes 调用约定、argtypes 声明与搬移前逐字符一致。
这些是直接触碰原生 API 的代码，任何一处改动都可能造成真实崩溃。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtGui import QCursor

from . import vision as vision_mod

if TYPE_CHECKING:
    from .window import PetWindow


# ---- Win32：全屏判定用常量/结构 ----
GWL_STYLE = -16             # GetWindowLongW：取窗口样式
GWL_EXSTYLE = -20           # GetWindowLongW：取扩展样式
_WS_CAPTION = 0x00C00000    # WS_BORDER | WS_DLGFRAME（带标题栏）
_WS_EX_TOPMOST = 0x00000008  # 置顶：真全屏游戏/视频几乎必带，普通最大化窗口不带
_WS_EX_TRANSPARENT = 0x00000020


class _WinRect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _WinMonitorInfo(ctypes.Structure):
    """GetMonitorInfoW 的 MONITORINFO（只读 rcMonitor：显示器完整几何，物理像素）。"""
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', _WinRect),
                ('rcWork', _WinRect), ('dwFlags', ctypes.c_ulong)]


def _set_windows_click_through(hwnd: int, enabled: bool, user32=None) -> bool:
    """切换 layered HWND 的输入穿透扩展样式。"""
    user32 = user32 or ctypes.windll.user32
    style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    updated = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
    if updated == style:
        return False
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, updated)
    return True


class WindowsPerPixelInputController:
    """根据光标所在像素动态切换 layered window 的输入穿透。

    HTTRANSPARENT 只能继续命中当前线程的窗口，无法穿透到其他应用。
    WS_EX_TRANSPARENT 会让 Windows 在命中时跳过 layered 桌宠窗口；独立
    定时器在窗口不再收到鼠标消息时仍能检测光标并恢复角色区域交互。
    """

    NORMAL_POLL_INTERVAL_MS = 10
    DRAG_POLL_INTERVAL_MS = 100

    def __init__(self, window: "PetWindow") -> None:
        self._window = window
        self._timer = QTimer(window)
        self._timer.setInterval(self.NORMAL_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def should_click_through(self, global_pos: QPoint) -> bool:
        win = self._window
        if win.mouse_through:
            return True
        if getattr(win, '_press_global', None) is not None or not win.isVisible():
            return False
        local = win.mapFromGlobal(global_pos)
        if not QRect(0, 0, win.width(), win.height()).contains(local):
            return False
        return win._is_transparent_at(local)

    def refresh(self) -> None:
        try:
            enabled = self.should_click_through(QCursor.pos())
            _set_windows_click_through(int(self._window.winId()), enabled)
        except (AttributeError, OSError, RuntimeError):
            logging.debug("更新 Windows 逐像素鼠标穿透失败", exc_info=True)

    def set_drag_active(self, active: bool) -> None:
        """拖拽按下/松手时切换轮询频率。

        拖拽（_press_global 非 None）期间 should_click_through 恒返回 False，
        每 10ms 轮询纯属空转：降频到 100ms 减少 Win32/QCursor 调用。
        松手后立即恢复原频率并强制刷新一次穿透状态；非拖拽状态重复调用是 no-op。
        """
        if active:
            if self._timer.interval() != self.DRAG_POLL_INTERVAL_MS:
                self._timer.setInterval(self.DRAG_POLL_INTERVAL_MS)
            return
        if self._timer.interval() == self.NORMAL_POLL_INTERVAL_MS:
            return
        self._timer.setInterval(self.NORMAL_POLL_INTERVAL_MS)
        self.refresh()

    def stop(self) -> None:
        self._timer.stop()
        if not self._window.mouse_through:
            try:
                _set_windows_click_through(int(self._window.winId()), False)
            except (AttributeError, OSError, RuntimeError):
                pass


_FS_SKIP_CLASSES = {
    'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
    'Windows.UI.Core.CoreWindow',  # 开始菜单/通知中心全屏层
}

# 已知覆盖层工具进程（截图/取色工具的全屏监听层不是"全屏应用"）：
# 实测 PixPin（pixpin.exe）打字时热键监听闪现全屏覆盖层曾致桌宠误隐藏频闪
_FS_SKIP_PROCS = {'pixpin.exe', 'snipaste.exe'}


def _fullscreen_geometry_hit(l: float, t: float, r: float, b: float,
                             geom, has_caption: bool, topmost: bool = False) -> bool:
    """覆盖整屏几何，且（无标题栏 或 置顶）= 真全屏。

    判据组合的原因：
    - 带标题栏的普通/最大化窗口（含 Windows 自动隐藏任务栏场景）不置顶 → 排除；
    - 真全屏游戏/视频：多数去掉标题栏（无标题栏直接命中）；Unity/UE 系游戏
      （如绝区零）全屏时保留 WS_CAPTION 样式位但几乎必带 WS_EX_TOPMOST，用
      置顶位兜住；
    - 已最大化后按 F11 的窗口（IsZoomed 仍为真、标题栏被清掉）也正常命中。

    geom 兼容 QRect（方法访问）与 win32 RECT（属性访问）。
    """
    if has_caption and not topmost:
        return False
    gl = geom.left() if callable(getattr(geom, "left", None)) else geom.left
    gt = geom.top() if callable(getattr(geom, "top", None)) else geom.top
    gr = geom.right() if callable(getattr(geom, "right", None)) else geom.right
    gb = geom.bottom() if callable(getattr(geom, "bottom", None)) else geom.bottom
    return l <= gl and t <= gt and r >= gr and b >= gb


def _fs_user_busy_state() -> tuple[bool, int]:
    """SHQueryUserNotificationState：Windows 自报的全屏/演示忙状态。

    与几何判定互补——几何判定在 DPI 虚拟化、跨屏、DWM 边界差异下可能漏判，
    而这个 API 是 Windows 自己（Focus Assist/通知静默）判定"用户正在
    全屏"的依据，游戏和全屏视频都会触发。返回 (是否全屏忙, 原始状态值)。
    """
    if os.name != 'nt':
        return False, -1
    try:
        state = ctypes.c_int(0)
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:  # S_OK
            return False, -1
        # 2=QUNS_BUSY(全屏应用运行中) 3=QUNS_RUNNING_D3D_FULL_SCREEN 4=QUNS_PRESENTATION_MODE
        return state.value in (2, 3, 4), state.value
    except Exception:
        return False, -1


def _fg_fullscreen_probe() -> tuple[bool, str]:
    """前台窗口全屏探测，返回 (是否全屏, 诊断描述)。

    可在任意线程调用——不触碰 Qt 对象。判定链：
    1. foreground_window_info()（vision.py）：排除不可见/最小化/cloaked
       窗口，取 DWM 框架边界（物理像素，与本进程 DPI awareness 一致）；
    2. 排除本进程、已知覆盖层工具进程（_FS_SKIP_PROCS）与 shell 窗口；
    3. 排除 WS_EX_TOOLWINDOW 工具窗口（截图覆盖层/输入法候选框/悬浮面板）；
    4. 几何判定：窗口覆盖所在显示器完整几何（含任务栏），且无标题栏或置顶；
    5. 兜底判定：Windows SHQueryUserNotificationState 报告全屏忙状态。
    """
    if os.name != 'nt':
        return False, "非 Windows"
    u32 = ctypes.windll.user32
    # 句柄是 64 位指针：显式声明签名，避免 ctypes 默认 int32 截断
    u32.MonitorFromWindow.restype = wintypes.HANDLE
    u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    info = vision_mod.foreground_window_info()
    if not info:
        return False, "无可判定前台窗口(不可见/最小化/cloaked)"
    hwnd = info['hwnd']
    # 排除本进程与其他变体/多开的桌宠进程（置顶小窗，几何不会误判，
    # 但 SHQueryUserNotificationState 兜底需要进程名兜底排除）
    proc = info.get('process', '')
    if info.get('pid') == os.getpid() or proc.lower().startswith('dsh-pet-'):
        return False, f"前台是桌宠自身 {proc}"
    # 已知覆盖层工具进程永不视为全屏（实测：PixPin 截屏覆盖层全屏无边框置顶，
    # 用户打字时其热键监听闪现覆盖层 → 误命中全屏 → 桌宠频闪）。
    if proc.lower() in _FS_SKIP_PROCS:
        return False, f"覆盖层工具进程 {proc}"
    # 排除桌面/任务栏等 shell 窗口
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    cls = buf.value
    if cls in _FS_SKIP_CLASSES:
        return False, f"shell 窗口 {cls}"

    style = u32.GetWindowLongW(hwnd, GWL_STYLE)
    has_caption = bool(style & _WS_CAPTION)
    exstyle = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    topmost = bool(exstyle & _WS_EX_TOPMOST)
    # 工具窗口（WS_EX_TOOLWINDOW：截图覆盖层/输入法候选框/悬浮面板）永不视为
    # 全屏——实测 PixPin 截屏覆盖层（全屏、无标题栏、置顶）曾触发桌宠误隐藏
    # 频闪（用户打字时 PixPin 覆盖层闪现 → 几何覆盖误判全屏）。
    if exstyle & 0x00000080:  # WS_EX_TOOLWINDOW
        return False, f"工具窗口 cls={cls} proc={info.get('process', '')}"
    x, y, w, h = info['rect']
    # 窗口所在显示器的完整几何（与 GetWindowRect/DWM 边界同为
    # 本进程 DPI awareness 下的坐标，天然一致）
    mon = u32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = _WinMonitorInfo()
    mi.cbSize = ctypes.sizeof(_WinMonitorInfo)
    if not u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False, f"GetMonitorInfoW 失败 cls={cls}"
    if _fullscreen_geometry_hit(
            x, y, x + w, y + h, mi.rcMonitor, has_caption, topmost):
        return True, f"几何覆盖 cls={cls} proc={info.get('process', '')}"
    busy, bstate = _fs_user_busy_state()
    if busy:
        return True, (f"SHQueryUserNotificationState={bstate} "
                      f"cls={cls} proc={info.get('process', '')}")
    detail = (f"未命中 cls={cls} proc={info.get('process', '')} "
              f"caption={has_caption} topmost={topmost} "
              f"rect=({x},{y},{x + w},{y + h}) "
              f"monitor=({mi.rcMonitor.left},{mi.rcMonitor.top},"
              f"{mi.rcMonitor.right},{mi.rcMonitor.bottom}) busy={bstate}")
    return False, detail


def _fg_fullscreen_win32() -> bool:
    """前台窗口是否真全屏。仅返回布尔值，诊断细节见 _fg_fullscreen_probe。"""
    try:
        return _fg_fullscreen_probe()[0]
    except Exception:
        return False
