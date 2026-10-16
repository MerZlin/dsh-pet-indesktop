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
# 不接收激活：鼠标点击不夺前台（issue #98）。工具窗口（Tool）不带该位时，
# 点击桌宠会把它变成前台窗口，用户随后的键盘输入全部落到桌宠上，而桌宠不处理
# Ctrl+C/Ctrl+V —— 观感就是"整机复制粘贴失效"，点回原窗口或退出桌宠才恢复。
_WS_EX_NOACTIVATE = 0x08000000

# ---- Win32：置顶重申（SetWindowPos）用常量 ----
_HWND_TOPMOST = -1      # 插到 topmost 带最上（每次调用都重排，不只是置样式位）
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


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


def _set_windows_no_activate(hwnd: int, user32=None) -> bool:
    """置位 WS_EX_NOACTIVATE：鼠标点击桌宠不再夺走前台/键盘焦点（issue #98）。

    只影响"激活"：窗口照样收到鼠标与键盘消息（点击、拖拽、逐像素穿透判定都不
    受影响，实测点击仍能进 mousePressEvent），但不会成为前台窗口，因此用户正在
    编辑的应用保持前台与输入焦点——Ctrl+C/Ctrl+V 不会再落到桌宠上。

    与改 flags 不同，这里只改扩展样式位，不重建原生窗口。
    返回 True 表示本次真的改了样式。
    """
    user32 = user32 or ctypes.windll.user32
    style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    updated = style | _WS_EX_NOACTIVATE
    if updated == style:
        return False
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, updated)
    return True


def _set_windows_topmost(hwnd: int, on: bool, user32=None) -> bool:
    """原生重设置顶：``SetWindowPos(HWND_TOPMOST / HWND_NOTOPMOST)``。

    与 Qt ``WindowStaysOnTopHint`` 的差别正是这里存在的理由：Qt 只在 flags
    **变化**时写一次 ``WS_EX_TOPMOST``，此后窗口只是"身在 topmost 带里"，而带内
    先后仍由"谁最近被激活/显示"决定；带 ``WindowDoesNotAcceptFocus`` 的窗口
    （灵动岛、桌宠）从不激活，一旦被别的 topmost 窗（本进程聊天窗/设置窗/气泡、
    第三方置顶工具）盖住就再也不会自己回来。本函数**每次调用都重排 z 序**，
    把窗口重新插到 topmost 带最上。

    ``SWP_NOACTIVATE``：只改 z 序、不夺用户键盘焦点（与 issue #98 同口径）；
    ``SWP_NOMOVE``/``SWP_NOSIZE``：不动几何，不产生重排/重绘。
    ``user32=None`` 时取真实 user32 并声明 argtypes——64 位下 HWND 是指针，
    不声明会被 ctypes 默认 int32 截断成无效句柄（口径同 6ebfd2b 的
    ``window.py::_win_set_topmost``）。返回 API 是否成功。
    """
    if user32 is None:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
    return bool(user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST if on else _HWND_NOTOPMOST,
        0, 0, 0, 0,
        _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
    ))


class WindowsPerPixelInputController:
    """根据光标所在像素动态切换 layered window 的输入穿透。

    HTTRANSPARENT 只能继续命中当前线程的窗口，无法穿透到其他应用。
    WS_EX_TRANSPARENT 会让 Windows 在命中时跳过 layered 桌宠窗口；独立
    定时器在窗口不再收到鼠标消息时仍能检测光标并恢复角色区域交互。
    """

    NORMAL_POLL_INTERVAL_MS = 10
    #: 光标在窗口包围盒外：逐像素判定必然「无命中 → 穿透」，低频空转即可
    #: （overlay 铺满整屏时，命中判定还要每 tick 走一遍全部 sprite 的 alpha
    #: 查询；小窗 legacy 更是绝大多数时间都在盒外）。
    IDLE_POLL_INTERVAL_MS = 50
    DRAG_POLL_INTERVAL_MS = 100
    #: 穿透态缓存不设过期时间，但每 N 次 refresh 无条件重读一次窗口样式
    #: （低频校正）：旧实现每 10ms 读一次样式，任何外部改写都会在一个 tick
    #: 内被纠正（window.py:4094-4096 的收敛契约就建立在这上面）；缓存后仍需
    #: 兜住「不知道是谁改了样式」的情况，代价是 10ms 档 1 次读/秒。
    RECHECK_EVERY_N_REFRESHES = 100

    # 类级默认值：既有测试用 object.__new__ 绕过 __init__ 拼控制器，
    # 读这些字段必须是安全的（未初始化 = 缓存无效，必然重放一次样式）。
    _applied_through: bool | None = None
    _applied_hwnd: int | None = None
    _applied_mouse_through: bool | None = None
    _refreshes_since_read: int = 0

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

    def _cursor_inside_window(self, global_pos: QPoint) -> bool:
        """光标是否落在窗口包围盒内（两段式轮询的定档判据）。"""
        win = self._window
        local = win.mapFromGlobal(global_pos)
        return QRect(0, 0, win.width(), win.height()).contains(local)

    def _sync_poll_interval(self, inside: bool) -> None:
        """按光标位置切档：盒内 10ms（逐像素跟手），盒外 50ms（低频）。

        拖拽中（``_press_global`` 非 None）不参与定档：100ms 档由
        ``set_drag_active`` 独占（拖拽期 should_click_through 恒 False，
        轮询只是保活）。
        """
        if getattr(self._window, '_press_global', None) is not None:
            return
        target = (self.NORMAL_POLL_INTERVAL_MS if inside
                  else self.IDLE_POLL_INTERVAL_MS)
        if self._timer.interval() != target:
            self._timer.setInterval(target)

    def refresh(self) -> None:
        try:
            pos = QCursor.pos()
            self._sync_poll_interval(self._cursor_inside_window(pos))
            enabled = self.should_click_through(pos)
            self._apply_through(enabled)
        except (AttributeError, OSError, RuntimeError):
            logging.debug("更新 Windows 逐像素鼠标穿透失败", exc_info=True)

    def _apply_through(self, enabled: bool) -> None:
        """把目标穿透态写进窗口样式；状态未变时不再读样式（O2）。

        跳过条件（四个条件同时成立才跳过）：
        1. 句柄未变——``setWindowFlags`` 之类的原生窗口重建会让样式归零，
           句柄变了必须重放；
        2. 目标态未变——逐像素命中的结论与上次相同；
        3. ``window.mouse_through`` 未变——仓库内唯一的外部样式写入口
           （``window.py:_apply_effective_mouse_through``）必定先改这个标志
           再写样式，标志变了即说明样式可能已被改写，缓存立即作废；
        4. 未到低频校正窗口——每 ``RECHECK_EVERY_N_REFRESHES`` 次 refresh
           无条件重读一次，兜住未知的外部改写（见该类常量注释）。

        为什么可以跳过 Win32 读：``_set_windows_click_through`` 的唯一作用就是
        把样式收敛到目标态，它自己的早退判断（读到的样式 == 目标）正是我们要
        省掉的那次读；缓存失效的四个入口覆盖了窗口重建/外部改写/停表恢复。
        """
        win = self._window
        hwnd = int(win.winId())
        mouse_through = bool(getattr(win, "mouse_through", False))
        self._refreshes_since_read += 1
        cache_valid = (
            self._applied_through == enabled
            and self._applied_hwnd == hwnd
            and self._applied_mouse_through == mouse_through
            and self._refreshes_since_read < self.RECHECK_EVERY_N_REFRESHES
        )
        if cache_valid:
            return
        _set_windows_click_through(hwnd, enabled)
        self._applied_through = enabled
        self._applied_hwnd = hwnd
        self._applied_mouse_through = mouse_through
        self._refreshes_since_read = 0

    def _invalidate_through_cache(self) -> None:
        """缓存作废（停表/恢复时调用）：下次 refresh 必然重读样式。"""
        self._applied_through = None
        self._applied_hwnd = None
        self._applied_mouse_through = None

    def resume(self) -> None:
        """隐藏后恢复（与 ``stop()`` 对称）：重开轮询表并立即收敛一次穿透态。

        停表期间窗口样式可能被外部改写、原生窗口也可能被重建，故先作废缓存
        再 refresh —— 「显示即立刻写正确穿透态」是硬要求：显示后不能有一段
        时间按陈旧样式吞掉点击。
        """
        self._invalidate_through_cache()
        self._refreshes_since_read = 0
        self.refresh()
        self._timer.start()

    def set_drag_active(self, active: bool) -> None:
        """拖拽按下/松手时切换轮询频率。

        拖拽（_press_global 非 None）期间 should_click_through 恒返回 False，
        每 10ms 轮询纯属空转：降频到 100ms 减少 Win32/QCursor 调用。
        松手后立即按光标位置重新定档（盒内 10ms / 盒外 50ms）并强制刷新一次
        穿透状态；非拖拽状态重复调用是 no-op。
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
        # 停表期间样式可能被外部改写、原生窗口也可能被重建：缓存作废，
        # 下次 refresh/resume 必然重新读一次样式（旧实现靠每次读兜住）。
        self._invalidate_through_cache()
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
        # 3=QUNS_RUNNING_D3D_FULL_SCREEN 4=QUNS_PRESENTATION_MODE——只认这两个
        # 无歧义「真全屏应用」信号。QUNS_BUSY(2) 不收：Win11 下它对任意全屏
        # 置顶窗（包括本进程自己的 overlay 合成窗）都会报 BUSY——overlay 可见
        # → BUSY=2 → 判全屏 → 隐藏 → BUSY 消退 → 显示 → BUSY=2……实机抓到
        # 1Hz 自激频闪（2026-09-23，soak 日志逐秒翻转 64 次）。真全屏游戏/
        # 视频仍由 QUNS=3 或几何判定（覆盖整屏+无标题栏/置顶）覆盖，不损失。
        return state.value in (3, 4), state.value
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


# ---- Win32：抓屏排除与物理窗口矩形（避字移动用）----
# 历史教训（2026-10-15 实机）：这里曾经有过 set_capture_exclusion
# （WDA_EXCLUDEFROMCAPTURE），目的是"抓屏时把桌宠排除掉，从而看见它背后的内容"。
# **实测不可用**：置位后窗口在物理屏幕上也**不再渲染**——用户看到的是"桌宠消失了"
# （进程活着、窗口在屏内、DWM cloaked=0、动画照常，但人眼看不见；同一台机器上
# 并排摆两个方块做 A/B，只有未置位的那个可见）。文档说的"只显示在显示器上"在这台
# 机器的显示栈（装了远程/串流/覆盖层工具）上并不成立。
# 现方案改为 PrintWindow 直接抓"桌宠底下那个窗口自己的内容"，不碰任何窗口属性，
# 因此**不要再把 WDA 加回来**。tests/test_text_avoid_controller.py 有一条断言守着这点。

PW_RENDERFULLCONTENT = 0x00000002
_DWMWA_CLOAKED = 14
_U32_PRIVATE = None
_DWM_PRIVATE = None


def _u32_private():
    """本模块专用的一份 user32 绑定（自带 argtypes，与别处的设置互不干扰）。

    **为什么必须有它**：ctypes 的 ``ctypes.windll.user32`` 是**进程级共享**的函数对象，
    谁设了 argtypes 就定死了之后所有调用者的签名。本项目 ``vision.py`` 把它设成
    ``POINTER(RECT)``，而本模块要用 ``_WinRect``（布局等价但是另一个类型）——两处互斥，
    后设的那一方会让另一方的调用点直接 ``ArgumentError``。更糟的是它**静默**：
    ``window_physical_rect`` 有 except 兜底（返回 None → 几何取不到 → 避字移动静默
    不生效），``EnumWindows`` 回调里的异常则被 ctypes 自己吞掉（同样表现为"拿不到
    窗口"）。所以本模块要用的这几个调用一律走自己这份独立绑定
    （``ctypes.WinDLL`` 每次实例化都是新对象，不参与 ``windll`` 的共享缓存）。
    """
    global _U32_PRIVATE
    if _U32_PRIVATE is None:
        lib = ctypes.WinDLL('user32', use_last_error=True)
        lib.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_WinRect)]
        lib.GetWindowRect.restype = wintypes.BOOL
        lib.IsWindowVisible.argtypes = [wintypes.HWND]
        lib.IsWindowVisible.restype = wintypes.BOOL
        lib.IsIconic.argtypes = [wintypes.HWND]
        lib.IsIconic.restype = wintypes.BOOL
        lib.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                 ctypes.POINTER(wintypes.DWORD)]
        lib.GetWindowThreadProcessId.restype = wintypes.DWORD
        _U32_PRIVATE = lib
    return _U32_PRIVATE


def window_physical_rect(hwnd: int, user32=None) -> tuple[int, int, int, int] | None:
    """窗口的 Win32 物理矩形 (x, y, w, h)；拿不到返回 None。

    本进程是 DPI aware（Qt 默认 per-monitor v2），所以这里拿到的就是**物理像素**，
    与抓屏位图同一套坐标口径；它与 Qt 逻辑坐标的换算见 text_avoid.CoordMap。

    ``user32`` 只给测试注入替身；真实路径走私有绑定（见 ``_u32_private`` 的说明），
    **不再去改共享函数的 argtypes**——那正是本模块与 ``vision.py`` 互相踩的地方。
    """
    if os.name != 'nt':
        return None
    u32 = user32 if user32 is not None else _u32_private()
    rect = _WinRect()
    try:
        if not u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
    except Exception:
        return None
    return (int(rect.left), int(rect.top),
            int(rect.right - rect.left), int(rect.bottom - rect.top))


def _is_cloaked(hwnd: int) -> bool:
    """DWM 是否把该窗口 cloaked（隐藏、不渲染）。取不到属性时按"没被隐藏"处理。

    HWND 在 64 位下是指针：这里显式声明签名（默认按 int32 传会截断句柄，查错窗口或
    直接失败），并且同样走**私有绑定**——本函数跑在 EnumWindows 回调里，异常会被
    ctypes 吞掉，表现成"这个窗口永远不算被隐藏"。
    """
    global _DWM_PRIVATE
    try:
        if _DWM_PRIVATE is None:
            lib = ctypes.WinDLL('dwmapi', use_last_error=True)
            lib.DwmGetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint,
                                                  ctypes.c_void_p, ctypes.c_uint]
            lib.DwmGetWindowAttribute.restype = ctypes.c_long
            _DWM_PRIVATE = lib
        value = ctypes.c_int(0)
        hr = _DWM_PRIVATE.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(_DWMWA_CLOAKED),
            ctypes.byref(value), ctypes.sizeof(value))
        return hr == 0 and value.value != 0
    except Exception:
        return False


def topmost_window_under(rect: tuple[int, int, int, int],
                         exclude_pid: int = 0) -> int | None:
    """返回与 rect 相交、且**盖在那块区域最上面**的那个窗口（不含 exclude_pid 的）。

    EnumWindows 按 z 序从上到下枚举，所以第一个命中就是"桌宠底下那一个"。
    跳过不可见/最小化/被 DWM 隐藏/零尺寸的窗口，避免抓到辅助窗口或空内容。
    取不到返回 None。
    """
    if os.name != 'nt':
        return None
    u32 = _u32_private()          # 私有绑定：回调里的异常会被 ctypes 吞掉，绝不能踩共享 argtypes
    left, top, width, height = rect
    right, bottom = left + width, top + height
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):  # noqa: ANN001
        if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if exclude_pid and pid.value == exclude_pid:
            return True
        r = _WinRect()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left <= 0 or r.bottom - r.top <= 0:
            return True
        if r.right <= left or r.left >= right or r.bottom <= top or r.top >= bottom:
            return True
        if _is_cloaked(hwnd):
            return True
        found.append(int(hwnd))
        return False

    try:
        u32.EnumWindows(_cb, 0)
    except Exception:
        return None
    return found[0] if found else None


def print_window_pixels(hwnd: int) -> tuple[bytes, int, int] | None:
    """用 PrintWindow(PW_RENDERFULLCONTENT) 取窗口**自身**内容，返回 (BGRA 字节, 宽, 高)。

    这是"看穿桌宠"的现方案：拿窗口自己的渲染结果，与屏幕上谁盖着谁无关，因此
    不需要给桌宠设任何窗口显示属性。实机验证：Chrome/Electron（GPU 加速）窗口能拿到
    真实内容（对 DeepSeek Harness 窗口抓取后直接 OCR 得到 366 个词框/279 个汉字）。

    局限：视频/UWP/部分全屏独占窗口可能只回黑图或纯色，调用方应把"内容方差极低"
    当作**没有数据**处理（本轮不做判断），绝不当成"这里没字"。
    """
    if os.name != 'nt':
        return None
    r = window_physical_rect(hwnd)
    if r is None or r[2] <= 0 or r[3] <= 0:
        return None
    width, height = r[2], r[3]
    u32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc = mem = bmp = None
    try:
        hdc = u32.GetWindowDC(wintypes.HWND(hwnd))
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
        gdi32.SelectObject(mem, bmp)
        if not u32.PrintWindow(wintypes.HWND(hwnd), mem, PW_RENDERFULLCONTENT):
            return None
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height      # 负数 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        buf = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(info), 0):
            return None
        return buf.raw, width, height
    except Exception:
        return None
    finally:
        try:
            if bmp:
                gdi32.DeleteObject(bmp)
            if mem:
                gdi32.DeleteDC(mem)
            if hdc:
                u32.ReleaseDC(wintypes.HWND(hwnd), hdc)
        except Exception:
            pass


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [('bmiHeader', _BitmapInfoHeader), ('bmiColors', wintypes.DWORD * 3)]
