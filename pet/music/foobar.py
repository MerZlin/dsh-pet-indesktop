# -*- coding: utf-8 -*-
"""foobar2000 的曲目来源：进程 + 主窗口标题（issue #228）。

**为什么需要它**：foobar2000 默认**不注册 SMTC**（Windows 系统媒体控制），也不会
出现在 ``now_playing`` 的会话列表里，所以歌词链路看不见它。要让 foobar 用户有
歌词，只有两条路：

1. **装组件走 SMTC**（``foo_mediacontrol`` 等）——那条路走的就是现有的 SMTC 采样，
   代码零改动，属于"用户装了我们自动就支持"，写在设置文案里推荐；
2. **读它的窗口标题**（本模块）——零依赖，但 foobar 的窗口标题是**用户可配置的
   titleformat 串**（「显示 → 默认用户界面 → 窗口标题」），出厂默认值随版本与
   主题变化，没有稳定契约。所以本模块只提供"抓标题"这一步，怎么切歌名/歌手交给
   纯函数 ``music_title.parse_foobar_title``（用户可在配置里给出格式串）。

**它在链路里的地位是补充源**：SMTC 有在播会话时根本不会问它（见
``now_playing._read_foobar_playback``）。原因很直接——foobar 的窗口标题里没有
播放状态，foobar 开着但没在播时标题可能仍停在上一首，抢 SMTC 的位会把正在听
的歌顶掉。

**窗口怎么挑**：只认该进程**无属主**的可见顶层窗口（``GetWindow(hwnd, GW_OWNER)
== 0``）。foobar 的「参数选项」这类对话框是主窗口的从属窗口，用这一条就能滤掉，
免得把 "Preferences" 当成歌名。仍有多条时取最长的一条。
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from . import title as music_title

log = logging.getLogger(__name__)

FOOBAR_EXE = "foobar2000.exe"
# 作为 ``Playback.app_id`` 的取值——**刻意不是 SMTC 会话 id**：它让调用方能区分
# "这条来自窗口标题、别拿它去覆盖 SMTC 的跟踪位"（now_playing.is_foobar_source）。
FOOBAR_APP_ID = FOOBAR_EXE

# 进程扫描的缓存时长（秒）。Lyric 采样是 1s 一拍，扫描实测 2.5~2.9ms/次；缓存后
# 平均开销降到约 1.3ms/拍，同时 foobar 启动最多 2 秒后被发现——够快，也不至于
# 每秒扫一遍进程表。
PROCESS_SCAN_TTL_SECONDS = 2.0

# 窗口列表里最多看几条（防止某个 foobar 版本开出一堆窗口时白枚举）。
_MAX_WINDOWS = 8

_pid_lock = threading.Lock()
_pid_cache: tuple[float, tuple[int, ...]] | None = None


def _scan_pids() -> tuple[int, ...]:
    """扫一遍 foobar2000.exe 的 pid；没有 psutil / 扫描失败一律按"没在跑"。"""
    try:
        import psutil
    except Exception:
        return ()
    pids: list[int] = []
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                if str(proc.info.get("name") or "").lower() == FOOBAR_EXE:
                    pids.append(int(proc.pid))
            except Exception:
                continue
    except Exception:
        log.debug("扫描 foobar2000 进程失败", exc_info=True)
        return ()
    return tuple(pids)


def foobar_pids(*, use_cache: bool = True) -> tuple[int, ...]:
    """当前在跑的 foobar2000 进程 pid（带 TTL 缓存，见模块常量）。"""
    global _pid_cache
    now = time.monotonic()
    with _pid_lock:
        cached = _pid_cache
    if use_cache and cached is not None and (now - cached[0]) <= PROCESS_SCAN_TTL_SECONDS:
        return cached[1]
    pids = _scan_pids()
    with _pid_lock:
        _pid_cache = (time.monotonic(), pids)
    return pids


def reset_pid_cache() -> None:
    """清掉进程缓存（测试用；产品路径不需要）。"""
    global _pid_cache
    with _pid_lock:
        _pid_cache = None


def _window_titles_for_pids(pids) -> list[str]:
    """这些进程的**无属主可见顶层窗口**标题（Windows；其他平台返回空）。"""
    if sys.platform != "win32" or not pids:
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []
    wanted = {int(pid) for pid in pids}
    titles: list[str] = []
    try:
        user32 = ctypes.windll.user32
        # 显式声明签名：默认 restype=c_int 会在 64 位下截断 HWND。
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.EnumWindows.restype = wintypes.BOOL

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _visit(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in wanted or not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, 4):  # GW_OWNER != 0 → 从属窗口（对话框等）
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            text = str(buffer.value or "").strip()
            if text:
                titles.append(text)
            return True

        user32.EnumWindows(callback(_visit), 0)
    except Exception:
        log.debug("枚举 foobar2000 窗口标题失败", exc_info=True)
        return []
    return titles


def read_track(title_format: str = "") -> tuple[str, str] | None:
    """读 foobar2000 正在播的 ``(歌名, 歌手)``；读不到返回 ``None``。

    任何异常都按"读不到"处理——歌词只是锦上添花，不能因为第三方程序影响桌宠。
    """
    pids = foobar_pids()
    if not pids:
        return None
    candidates: list[tuple[int, tuple[str, str]]] = []
    for title in _window_titles_for_pids(pids)[:_MAX_WINDOWS]:
        parsed = music_title.parse_foobar_title(title, title_format)
        if parsed is not None:
            candidates.append((len(title), parsed))
    if not candidates:
        return None
    # 实测性顾虑：主窗口之外仍可能残留无属主窗口（皮肤/独立窗口）。曲目标题几乎
    # 总比这些窗口的标题长，取最长的一条。
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
