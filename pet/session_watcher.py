# -*- coding: utf-8 -*-
"""会话结束探测（Windows 关机/注销）：issue #111。

问题：桌宠对「操作系统已宣告会话结束」零感知。关机时动画链仍在正常运转，
随时 CreateProcess 新的 ffmpeg 取帧进程；此时登录会话（窗口站/桌面堆/CSRSS）
已在拆除，新进程的 user32/gdi32 初始化失败（0xc0000142），系统弹出错误对话框
阻塞关机。ffmpeg 启停频率高（冷首帧预热/reader 换代/元数据探测/圈末回收），
撞上关机窗口的概率因此几乎为 100%。

本模块只做两件事：
1. **探测**会话结束：Windows 上经应用级原生事件过滤器捕获
   ``WM_QUERYENDSESSION`` / ``WM_ENDSESSION``；并连接 Qt 会话框架信号
   ``commitDataRequest`` / ``aboutToQuit`` 作次生兜底。
2. **置位**进程级闸门 ``pet.webm_clip.set_session_ending()``，并调用注入的
   安全网回调（``AppShell._on_session_end``）终止现有 reader。

为什么原生过滤器是权威信号：``WM_QUERYENDSESSION`` 在会话拆除**之前**送达，
是唯一能真正赶在窗口期前生效的时机；Qt 会话框架的信号是次生路径，且在
Windows 上是否触发受 Qt 版本/会话管理器实现影响，不能单靠它。

平台：POSIX 上不安装原生过滤器（无此消息），仍保留闸门与信号接线，行为等价。
线程：只在 GUI 线程创建/安装/触发（原生事件过滤器与 Qt 信号都在 GUI 线程）。
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from typing import Callable, Optional

import shiboken6
from PySide6.QtCore import QCoreApplication, QObject

from . import webm_clip

logger = logging.getLogger(__name__)

WM_QUERYENDSESSION = 0x0011  # 会话即将结束：关机/注销前的最后一次询问
WM_ENDSESSION = 0x0016       # 会话已结束（拆除开始）

# 只关心的消息号 → 日志用 reason。**先比对消息号再解引用**：事件过滤器每帧都
# 会被调用，绝不能对任意消息都去读 lParam 指向的内存（非 WM_* 消息的 lParam
# 是任意值，当作指针解引用会触发访问违规——实测在 CPython 上表现为
# "Windows fatal exception: access violation"）。
_SESSION_END_MESSAGES = {
    WM_QUERYENDSESSION: 'native_query_end_session',
    WM_ENDSESSION: 'native_end_session',
}

# Windows MSG 结构（原生事件过滤器的 message 指针在 Windows 上即 MSG*）。
# 必须用真实 ctypes 结构解析：字段布局错误会让解析静默失效（假绿）。
_MSG_FIELDS = [
    ('hwnd', wintypes.HWND),
    ('message', wintypes.UINT),
    ('wParam', wintypes.WPARAM),
    ('lParam', wintypes.LPARAM),
    ('time', wintypes.DWORD),
    ('pt', wintypes.POINT),
]


class _WinMsg(ctypes.Structure):
    """Windows MSG（仅取本模块需要的字段，布局与 winuser.h 一致）。"""

    _fields_ = _MSG_FIELDS

    def message_id(self) -> int:
        return int(self.message)


def session_end_reason(message) -> Optional[str]:
    """把原生事件过滤器的 message 参数翻译成会话结束原因（非会话消息返回 None）。

    只读 Qt 给出的 ``MSG*`` 的 ``message`` 字段，解析失败（非 Windows / 指针
    失效 / PySide6 传参形态变化）一律返回 None——本模块只做观测，任何异常都
    必须吞掉，绝不让 Qt 事件循环因探测器崩掉。
    """
    if not isinstance(message, int) or message <= 0:
        return None
    try:
        raw = ctypes.string_at(message, ctypes.sizeof(_WinMsg))
        msg_id = _WinMsg.from_buffer_copy(raw).message_id()
    except Exception:
        return None
    return _SESSION_END_MESSAGES.get(msg_id)


class SessionWatcher(QObject):
    """会话结束探测器：置位 ffmpeg spawn 闸门 + 跑安全网回调（幂等）。

    生命周期：由 AppShell 创建并强引用持有，进程存活期间常驻。
    """

    def __init__(self, app=None, on_session_end: Optional[Callable[[], None]] = None,
                 install_native_filter: bool = True) -> None:
        super().__init__(None)
        self._app = app if app is not None else QCoreApplication.instance()
        self._on_session_end = on_session_end
        self._install_native_filter = bool(install_native_filter)
        self._armed = False
        self._installed = False
        self._signals_connected = False

    # ------------------------------------------------------------ 状态
    @property
    def armed(self) -> bool:
        """是否已收到会话结束通知（幂等 latch）。"""
        return self._armed

    # ------------------------------------------------------------ 安装
    def install(self) -> bool:
        """安装原生过滤器并接线 Qt 会话信号（幂等；无 QApplication 时为无操作）。"""
        if self._installed:
            return True
        if self._app is None or not shiboken6.isValid(self._app):
            return False
        self.connect_app_signals()
        if self._install_native_filter and os.name == 'nt':
            try:
                self._app.installNativeEventFilter(self)
            except AttributeError:
                pass  # 鸭子类型替身（测试桩）没有该方法：只保留信号兜底路径
            except Exception:
                logger.debug('安装会话结束原生事件过滤器失败', exc_info=True)
        self._installed = True
        return True

    def connect_app_signals(self) -> bool:
        """连接 Qt 会话框架信号（次生兜底路径；幂等）。"""
        if self._signals_connected or self._app is None:
            return False
        connected = False
        for name, reason in (('commitDataRequest', 'qt_commit_data_request'),
                             ('aboutToQuit', 'about_to_quit')):
            signal = getattr(self._app, name, None)
            if signal is None:
                continue
            try:
                signal.connect(lambda _reason=reason: self.arm(_reason))
            except Exception:
                logger.debug('连接 %s 会话信号失败', name, exc_info=True)
            else:
                connected = True
        self._signals_connected = connected
        return connected

    # ------------------------------------------------------------ 原生过滤器
    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        """应用级原生事件过滤器：Windows 关机/注销消息 → 置位闸门。

        恒返回 ``(False, 0)``：只观测、不拦截——绝不 veto 关机，也不改变 Qt 的
        默认处理（Qt 对 WM_QUERYENDSESSION 的应答语义保持原样）。
        """
        if not self._armed:
            reason = session_end_reason(message)
            if reason is not None:
                self.arm(reason)
        return (False, 0)

    # ------------------------------------------------------------ 触发
    def arm(self, reason: str = '') -> None:
        """置位会话结束（幂等）：先关 spawn 闸门，再跑安全网回调。

        顺序不可颠倒：闸门先落，后续任何代码路径、任何回调异常都不可能再让
        ffmpeg 起来。回调只跑一次（重复的 WM_QUERYENDSESSION/WM_ENDSESSION 与
        aboutToQuit 都会到这里）。
        """
        if self._armed:
            return
        self._armed = True
        logger.info(
            '收到会话结束通知（%s）：停止派生 ffmpeg 子进程并静默退出', reason or 'unknown',
        )
        self.apply_session_ending()
        if self._on_session_end is not None:
            try:
                self._on_session_end()
            except Exception:
                logger.exception('会话结束收口失败（闸门已置位，不再派生进程）')

    def apply_session_ending(self) -> None:
        """只置位 spawn 闸门（安全网回调的前置步，异常隔离）。"""
        try:
            webm_clip.set_session_ending(True)
        except Exception:
            logger.debug('置位会话结束闸门失败', exc_info=True)


def install_session_watcher(app=None, on_session_end=None) -> SessionWatcher:
    """便捷入口：创建并安装探测器（AppShell 使用）。

    平台判定在 ``SessionWatcher.install()`` 内完成：只有 Windows
    （``os.name == 'nt'``）才注册原生事件过滤器，POSIX 上仅保留闸门与 Qt
    会话信号接线。
    """
    watcher = SessionWatcher(app=app, on_session_end=on_session_end)
    watcher.install()
    return watcher
