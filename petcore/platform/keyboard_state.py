"""键盘控制移动的按键读取：只读 WASD/方向键的按住状态。

不装钩子、不记录按键历史、不保存任何键值；只在用户显式开启「控制移动」时
由 UI 的 16ms 表轮询。非 Windows 明确返回不支持，不假装能用。
"""

from __future__ import annotations

import sys

_SUPPORTED = sys.platform == "win32"

if _SUPPORTED:
    import ctypes

    _USER32 = ctypes.windll.user32  # type: ignore[attr-defined]
    #: 虚拟键码 → 方向。只覆盖需求里的 8 个键。
    _KEYS = {
        0x57: "up",  # W
        0x53: "down",  # S
        0x41: "left",  # A
        0x44: "right",  # D
        0x26: "up",  # ↑
        0x28: "down",  # ↓
        0x25: "left",  # ←
        0x27: "right",  # →
    }
else:
    _USER32 = None
    _KEYS = {}


def supported() -> bool:
    return _SUPPORTED


def reason() -> str:
    return "" if _SUPPORTED else "键盘控制目前只支持 Windows"


def pressed_directions() -> set[str]:
    """当前按住的方向集合；非 Windows 返回空集。"""
    if _USER32 is None:
        return set()
    result: set[str] = set()
    for virtual_key, direction in _KEYS.items():
        try:
            if _USER32.GetAsyncKeyState(virtual_key) & 0x8000:
                result.add(direction)
        except Exception:  # noqa: BLE001 - 读键失败按未按处理
            continue
    return result


__all__ = ["pressed_directions", "reason", "supported"]
