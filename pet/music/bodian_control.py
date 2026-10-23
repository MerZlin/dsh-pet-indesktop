# -*- coding: utf-8 -*-
"""向不支持 SMTC 的播放器（波点音乐）投递**系统媒体键**，实现切歌 / 暂停。

**为什么需要它**：波点不注册 Windows 系统媒体会话，所以 ``now_playing`` 的
``try_skip_next_async`` / ``try_toggle_play_pause_async`` 对它全部返回 ``False``
（实测 2026-10-21：三个调用都失败，``GetSessions()`` 返回 0）。但它在**进程里装了
系统级媒体键钩子**——实测 2026-10-23：桌宠窗口是前台、波点在后台时，注入
``VK_MEDIA_PLAY_PAUSE`` 它仍然响应（日志出现 ``mpv playing event=true``）。
所以媒体键是唯一可达的控制通道。

**为什么不用 UIA**：波点的「上一曲 / 暂停 / 下一曲」三个按钮在 UIA 树里**存在**，
但**不支持** ``InvokePattern``、``LegacyIAccessible`` 的 ``DefaultAction`` 也是空串
（实测 2026-10-23），所以按不动。定向 ``WM_APPCOMMAND`` 与 ``PostMessage`` 的鼠标
消息它同样不认——这三条都验过，别重复试。

**代价（必须让调用方知道）**：媒体键是**系统级**的，会送到"当前接管媒体键的那个
程序"。所以本模块**只在确认波点在跑、且它确实是当前来源时**才允许调用——否则就是
"想给波点切歌，结果把网易云换了"。

**本模块不做的事**：不抢前台、不移动鼠标、不模拟点击、不读第三方进程内存。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from . import bodian as bodian_playing

log = logging.getLogger(__name__)

#: 系统媒体键虚拟键码。
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

#: 动作名 → 虚拟键码。``next`` / ``previous`` 与 ``now_playing.skip_track`` 的
#: ``direction`` 参数同名，避免调用方再做一次映射。
_ACTIONS = {
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
    "play_pause": VK_MEDIA_PLAY_PAUSE,
}


# ---------------------------------------------------------------- Win32 结构
#
# **``INPUT`` 必须是 40 字节（x64）**：``SendInput`` 按 ``cbSize`` 校验，尺寸不对会
# 直接返回 0 并置 ``ERROR_INVALID_PARAMETER``。实测踩过这个坑——只声明键盘成员时
# union 只有 24 字节、``sizeof(INPUT)`` = 32，媒体键一个都没注入成功，看起来像是
# "波点不响应媒体键"，其实是参数非法。所以三个成员都要声明，让 union 取到 32。
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def input_struct_size() -> int:
    """``sizeof(INPUT)``——x64 上必须是 40（测试与自检用）。"""
    return ctypes.sizeof(INPUT)


def _user32():
    """延迟加载 user32（非 Windows 返回 ``None``；导入期不碰 ctypes）。"""
    if sys.platform != "win32":
        return None
    try:
        lib = ctypes.WinDLL("user32", use_last_error=True)
        lib.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        ]
        lib.SendInput.restype = wintypes.UINT
        return lib
    except Exception:
        log.debug("加载 user32 失败", exc_info=True)
        return None


def send_media_key(vk: int) -> bool:
    """按下并松开一个媒体键；成功返回 ``True``。

    ``SendInput`` 一次投递两个事件（down + up），返回值应为 2。任何异常/非 2 都按
    失败处理——**不重试**：媒体键是全局动作，重试会把别的播放器也切了。
    """
    lib = _user32()
    if lib is None:
        return False
    events = (INPUT * 2)()
    events[0].type = INPUT_KEYBOARD
    events[0].u.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_EXTENDEDKEY, 0, None)
    events[1].type = INPUT_KEYBOARD
    events[1].u.ki = _KEYBDINPUT(
        vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0, None
    )
    try:
        sent = lib.SendInput(2, events, ctypes.sizeof(INPUT))
    except Exception:
        log.debug("SendInput 媒体键失败", exc_info=True)
        return False
    if sent != 2:
        log.debug("SendInput 未完整注入（%s/2）", sent)
        return False
    return True


def target_available() -> bool:
    """现在能不能把媒体键发给波点。

    两道闸，缺一不可：

    1. Windows；
    2. **波点进程在跑**（``bodian.process_running``，带 2s 缓存）。这条是防误伤用的：
       媒体键是全局的，波点没开时发出去只会落到别的播放器身上。
    """
    if sys.platform != "win32":
        return False
    try:
        return bool(bodian_playing.process_running())
    except Exception:
        return False


def _send(action: str) -> bool:
    vk = _ACTIONS.get(action)
    if vk is None:
        return False
    if not target_available():
        return False
    ok = send_media_key(vk)
    if ok:
        log.info("已向波点发送媒体键：%s", action)
    return ok


def skip(direction: str = "next") -> bool:
    """切到下一首 / 上一首。``direction`` 取 ``"next"`` 或 ``"previous"``。"""
    return _send("next" if direction != "previous" else "previous")


def toggle_pause() -> bool:
    """暂停 / 恢复。"""
    return _send("play_pause")
