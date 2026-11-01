"""宿主能力探测：QtWebView（宿主打包）+ Edge WebView2 运行时（系统自带）。

无 Qt import：只查模块、注册表与目录，任何环境都能安全调用（测试也不起 Qt）。
两个缺口要分开报，因为补救方式完全不同：

- ``engine``：宿主没打包 ``PySide6.QtWebView``（瘦身构建/非 Windows 平台），
  重装程序或换 Windows 才有用，装运行时无用；
- ``runtime``：系统缺 Microsoft Edge WebView2 Runtime，装运行时即可。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

#: WebView2 的用户数据目录是**进程级环境变量**：必须在第一个视图创建前设好，
#: 否则会落到 %LOCALAPPDATA%\<exe 名>\EBWebView（按可执行名共用，换版本不清）。
USER_DATA_ENV = "WEBVIEW2_USER_DATA_FOLDER"

_RUNTIME_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_RUNTIME_KEYS = (
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + _RUNTIME_GUID,
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\" + _RUNTIME_GUID,
)
_RUNTIME_DIRS = (
    r"%ProgramFiles(x86)%\Microsoft\EdgeWebView\Application",
    r"%ProgramFiles%\Microsoft\EdgeWebView\Application",
    r"%LOCALAPPDATA%\Microsoft\EdgeWebView\Application",
)

_ENGINE_CACHE: bool | None = None


def platform_supported() -> bool:
    """只在 Windows 验证过（后端直连 WebView2）。"""
    return sys.platform.startswith("win")


def engine_available() -> bool:
    global _ENGINE_CACHE
    if _ENGINE_CACHE is None:
        try:
            _ENGINE_CACHE = importlib.util.find_spec("PySide6.QtWebView") is not None
        except (ImportError, ValueError):
            _ENGINE_CACHE = False
    return bool(_ENGINE_CACHE)


def _registry_version() -> str:
    try:
        import winreg
    except ImportError:
        return ""
    for path in _RUNTIME_KEYS:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, path) as key:
                    value = str(winreg.QueryValueEx(key, "pv")[0] or "").strip()
            except OSError:
                continue
            if value:
                return value
    return ""


def _directory_version() -> str:
    for template in _RUNTIME_DIRS:
        root = Path(os.path.expandvars(template))
        try:
            if not root.is_dir():
                continue
            versions = sorted(
                (child.name for child in root.iterdir() if child.is_dir()),
                reverse=True,
            )
        except OSError:
            continue
        if versions:
            return versions[0]
    return ""


def runtime_version() -> str:
    """已安装的 WebView2 运行时版本；没有则空串。"""
    if not platform_supported():
        return ""
    return _registry_version() or _directory_version()


def missing_reason() -> str:
    """``""`` = 可用；否则是第一个缺口（``platform`` / ``engine`` / ``runtime``）。"""
    if not platform_supported():
        return "platform"
    if not engine_available():
        return "engine"
    if not runtime_version():
        return "runtime"
    return ""


def missing_hint(reason: str) -> str:
    if reason == "platform":
        return "内置网页只支持 Windows（本程序没有为当前系统内置网页内核）。"
    if reason == "engine":
        return (
            "这个安装包没有内置网页内核（QtWebView）。"
            "重装本程序可以恢复；装微软运行时在这里没有用。"
        )
    if reason == "runtime":
        return (
            "系统缺少 Microsoft Edge WebView2 运行时。"
            "Win11 一般自带；缺失时点「打开微软下载页」安装后重试。"
        )
    return ""


def user_data_dir(root: object) -> Path:
    return Path(root) / "webview2"


def ensure_user_data(root: object) -> Path:
    """把 WebView2 的用户数据放在本扩展的数据目录里（登录态跟着它走）。"""
    target = user_data_dir(root)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return target
    existing = str(os.environ.get(USER_DATA_ENV, "") or "").strip()
    if existing:
        try:
            if Path(existing).resolve() == target.resolve():
                return target
        except OSError:
            pass
        # 别的实例先设过：沿用它的目录，避免两个进程抢同一份配置。
        return Path(existing)
    os.environ[USER_DATA_ENV] = str(target)
    return target
