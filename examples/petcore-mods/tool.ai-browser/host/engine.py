"""宿主内核能力探测：只查模块是否存在，不导入 Qt，也不碰窗口。

安装包（PyInstaller onedir）没有随包 QtWebEngine，`find_spec` 会返回 None；
源码环境（`.venv`）返回 spec。mod 必须据此选择打开方式，不能假设宿主一致。
"""

from __future__ import annotations

import importlib.util

_MODULES = ("PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore")
_CACHE: bool | None = None


def engine_available() -> bool:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = all(importlib.util.find_spec(name) is not None for name in _MODULES)
        except (ImportError, ValueError):
            _CACHE = False
    return bool(_CACHE)


def engine_reason() -> str:
    if engine_available():
        return "内嵌 Chromium 可用"
    return "宿主未内置 Chromium 内核"
