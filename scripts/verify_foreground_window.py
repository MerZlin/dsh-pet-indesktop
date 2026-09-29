"""Explicit visible-desktop probe; never run implicitly by pytest or headless CI.

Usage: python scripts/verify_foreground_window.py
Exit 0: stable eligible foreground and real metadata verified.
Exit 2: desktop preconditions not met (not an acceptance pass).
Exit 1: verification failed (not an environment skip).

Read-only WinAPI queries only: no focus changes, input, screenshots or network.
Output deliberately omits window titles, process names/paths, HWNDs and PIDs.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.vision import foreground_window_info  # noqa: E402 -- standalone development script


@dataclass(frozen=True)
class ProbeResult:
    exit_code: int
    reason: str
    attempts: int


@dataclass(frozen=True)
class _Foreground:
    hwnd: int
    reason: str = ""


def _has_area(rect: ctypes.wintypes.RECT) -> bool:
    return rect.right > rect.left and rect.bottom > rect.top


def _inspect_foreground() -> _Foreground:
    """Check only the prerequisites, independently of metadata extraction.

    This intentionally does not read title/process data. Keep eligibility in
    agreement with vision.foreground_window_info, including optional DWM and
    User32 rectangle fallback; do not infer readiness from that function's None.
    """
    if sys.platform != "win32":
        return _Foreground(0, "unsupported_platform")
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
    user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
    user32.IsIconic.restype = ctypes.wintypes.BOOL
    user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetWindowRect.restype = ctypes.wintypes.BOOL
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return _Foreground(0, "no_foreground")
    if not user32.IsWindowVisible(hwnd):
        return _Foreground(hwnd, "not_visible")
    if user32.IsIconic(hwnd):
        return _Foreground(hwnd, "minimized")

    dwm = None
    try:
        dwm = ctypes.windll.dwmapi.DwmGetWindowAttribute
        dwm.argtypes = [ctypes.wintypes.HWND, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
        dwm.restype = ctypes.c_long
        cloaked = ctypes.c_int(0)
        if dwm(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return _Foreground(hwnd, "cloaked")
    except (AttributeError, OSError, TypeError, RuntimeError):
        dwm = None

    rect = ctypes.wintypes.RECT()
    if dwm is not None:
        try:
            if dwm(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0 and _has_area(rect):
                return _Foreground(hwnd)
        except (AttributeError, OSError, TypeError, RuntimeError):
            pass
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)) and _has_area(rect):
        return _Foreground(hwnd)
    return _Foreground(hwnd, "invalid_rect")


def valid_metadata(info: object, hwnd: int) -> bool:
    """Validate the real function's contract without printing private data."""
    if not isinstance(info, dict) or set(info) != {"hwnd", "pid", "process", "title", "rect"}:
        return False
    if type(info["hwnd"]) is not int or info["hwnd"] != hwnd or hwnd <= 0:
        return False
    if type(info["pid"]) is not int or info["pid"] < 0:
        return False
    if not isinstance(info["process"], str) or not isinstance(info["title"], str):
        return False
    rect = info["rect"]
    return isinstance(rect, tuple) and len(rect) == 4 and all(type(value) is int for value in rect) and rect[2] > 0 and rect[3] > 0


def verify_foreground() -> ProbeResult:
    for attempt in range(1, 4):
        try:
            before = _inspect_foreground()
        except Exception:
            # Even exception messages may contain user context. Emit codes only.
            return ProbeResult(1, "preflight_failed", attempt)
        if before.reason:
            return ProbeResult(2, before.reason, attempt)
        failed = False
        try:
            info = foreground_window_info()
        except Exception:
            failed = True
            info = None
        try:
            after = _inspect_foreground()
        except Exception:
            return ProbeResult(1, "postflight_failed", attempt)
        if before != after:
            continue
        if failed:
            return ProbeResult(1, "function_raised", attempt)
        if not valid_metadata(info, before.hwnd):
            return ProbeResult(1, "invalid_metadata", attempt)
        return ProbeResult(0, "foreground_verified", attempt)
    return ProbeResult(2, "foreground_unstable", 3)


def main() -> int:
    result = verify_foreground()
    print(json.dumps(asdict(result), ensure_ascii=True, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
