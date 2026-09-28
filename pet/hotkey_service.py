# -*- coding: utf-8 -*-
"""全局快捷键服务：Windows 注册 + Qt 原生消息过滤器 + 动作分发。

Win32 把 ``WM_HOTKEY`` 投给**注册它的那个线程**，所以注册必须发生在主线程，
并由 Qt 的原生事件过滤器接住（本模块装一个 ``QAbstractNativeEventFilter``）。
非 Windows 平台整体退化为 no-op（``ensure_installed`` 直接返回），设置与菜单照旧可用。

规则解析在 ``pet/hotkey_rules.py``（纯逻辑、可离线单测）。
"""
from __future__ import annotations

import ctypes
import logging
import random
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication

from . import hotkey_rules as rules_mod

logger = logging.getLogger("dsh-pet-standalone")

WM_HOTKEY = 0x0312
_FILTER_ATTR = "_hotkey_filter"
_IDS_ATTR = "_hotkey_ids"
_SLOTS_ATTR = "_hotkey_slots"
_FAILED_ATTR = "_hotkey_failed"


class _MSG(ctypes.Structure):
    """Win32 MSG 的最小可用子集（只读 message / wParam）。"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long),
    ]


class _HotkeyNativeFilter(QAbstractNativeEventFilter):
    """把 WM_HOTKEY 转成分发调用（返回 True 表示已消费）。"""

    def __init__(self, host) -> None:
        super().__init__()
        self._host = host

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            if bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                return False, 0
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        except Exception:
            return False, 0
        if msg.message != WM_HOTKEY:
            return False, 0
        try:
            on_hotkey(self._host, int(msg.wParam or 0))
        except Exception:
            logger.exception("快捷键处理失败")
        return True, 0


def _cfg_value(host, key, default=None):
    cfg = getattr(host, "cfg", None)
    try:
        return cfg.get(key, default) if cfg is not None else default
    except Exception:
        return default


def enabled(host) -> bool:
    return bool(_cfg_value(host, "hotkeys_on", True))


def rules(host) -> list[dict]:
    return rules_mod.clean_hotkeys(_cfg_value(host, "hotkeys", None))


def failed_sequences(host) -> list[str]:
    """上一轮注册失败（键被别的程序占用 / 系统拒绝）的键序，给设置页与气泡用。"""
    return list(getattr(host, _FAILED_ATTR, []) or [])


def _user32():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    return user32


def unregister_all(host) -> None:
    ids = getattr(host, _IDS_ATTR, None)
    if not isinstance(ids, (list, tuple)):
        ids = []          # 宿主可能是测试替身：任何非列表值都按"没注册过"处理
    ids = list(ids)
    setattr(host, _IDS_ATTR, [])
    setattr(host, _SLOTS_ATTR, {})
    if not ids or sys.platform != "win32":
        return
    try:
        user32 = _user32()
        for hotkey_id in ids:
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
    except Exception:
        pass


def register_all(host) -> list[str]:
    """按配置注册；返回注册失败的键序（键被占用时如实回报，不静默）。"""
    unregister_all(host)
    failed: list[str] = []
    slots: dict[int, dict] = {}
    if sys.platform != "win32":
        setattr(host, _FAILED_ATTR, failed)
        return failed
    try:
        user32 = _user32()
    except Exception:
        setattr(host, _FAILED_ATTR, failed)
        return failed
    for index, rule in enumerate(rules(host)):
        if not rule.get("on", True):
            continue
        parsed = rules_mod.hotkey_parse(rule.get("seq"))
        if parsed is None:
            failed.append(str(rule.get("seq") or ""))
            continue
        mods, vk = parsed
        hotkey_id = rules_mod.HOTKEY_BASE + index
        try:
            ok = bool(user32.RegisterHotKey(None, hotkey_id,
                                            mods | rules_mod.HOTKEY_NOREPEAT, vk))
        except Exception:
            ok = False
        if ok:
            slots[hotkey_id] = dict(rule)
        else:
            failed.append(str(rule.get("seq") or ""))
    setattr(host, _IDS_ATTR, list(slots))
    setattr(host, _SLOTS_ATTR, slots)
    setattr(host, _FAILED_ATTR, failed)
    return failed


def ensure_installed(host) -> None:
    """装过滤器 + 按开关注册（幂等）。启动、设置保存、菜单切换都调它。"""
    if not enabled(host):
        stop(host)
        return
    app = QApplication.instance()
    if app is not None and getattr(host, _FILTER_ATTR, None) is None:
        native_filter = _HotkeyNativeFilter(host)
        app.installNativeEventFilter(native_filter)
        setattr(host, _FILTER_ATTR, native_filter)
    register_all(host)


def stop(host) -> None:
    unregister_all(host)
    native_filter = getattr(host, _FILTER_ATTR, None)
    app = QApplication.instance()
    if native_filter is not None and app is not None:
        try:
            app.removeNativeEventFilter(native_filter)
        except Exception:
            logger.debug("移除快捷键过滤器失败", exc_info=True)
    setattr(host, _FILTER_ATTR, None)


def toggle(host) -> bool:
    cfg = getattr(host, "cfg", None)
    new_value = not enabled(host)
    if cfg is not None:
        cfg.set("hotkeys_on", new_value)
        saver = getattr(cfg, "save", None)
        if callable(saver):
            saver()
    if new_value:
        ensure_installed(host)
        failed = failed_sequences(host)
        if failed:
            _present(host, "有几个键被别的程序占着，人家先跳过：" + "、".join(failed))
        else:
            _present(host, "快捷键开好啦，按一下试试嘛")
    else:
        stop(host)
        _present(host, "快捷键先关掉了哦")
    return new_value


def on_hotkey(host, hotkey_id: int) -> None:
    """原生消息里收到 WM_HOTKEY：按这条规则的动作来一下。"""
    slots = getattr(host, _SLOTS_ATTR, None)
    if not isinstance(slots, dict):
        slots = {}
    rule = slots.get(hotkey_id)
    if not rule:
        return
    act = str(rule.get("act") or "lines")
    if act == "weather":
        from . import weather_service

        weather_service.show_weather(host)
        return
    if act == "balance":
        callback = getattr(host, "on_show_balance", None)
        if callable(callback):
            callback(host)
        else:
            _present(host, "还没配余额呢，主人先去设置里填 Key 嘛")
        return
    lines = [str(item).strip() for item in (rule.get("lines") or []) if str(item).strip()]
    if lines:
        _present(host, random.choice(lines), duration_ms=3200, alert_type="hotkey")


def _present(host, text: str, *, duration_ms: int = 4200,
             alert_type: str = "hotkey") -> None:
    from . import window_alerts

    try:
        window_alerts.show_alert(
            host, text, duration_ms=duration_ms, sticky=False,
            alert_type=alert_type, priority=3,
        )
    except Exception:
        logger.exception("快捷键气泡展示失败")
