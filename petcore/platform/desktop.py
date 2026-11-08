"""Desktop capabilities and pre-QApplication setup; safe in headless diagnostics."""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

CAPABILITY_LABELS = {
    "global_hotkeys": "全局快捷键", "fullscreen_detection": "全屏自动隐藏",
    "foreground_window": "前台窗口识别", "microphone": "系统麦克风",
    "system_speech": "系统语音合成", "system_media": "系统媒体识别",
    "gpu": "GPU 监控", "game_fps": "游戏帧率", "autostart": "登录自启",
    "desktop_input": "桌宠定位与输入",
    "screen_capture": "截图",
}


@dataclass(frozen=True)
class DesktopCapability:
    supported: bool
    available: bool
    reason: str = ""


def prepare_environment(environ=None, *, platform=None, plugin_dir=None) -> None:
    env = os.environ if environ is None else environ
    platform = platform or sys.platform
    if platform != "linux":
        return
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        # Bundled Ubuntu 22.04 GLib cannot load newer host GVfs extensions.
        # Keep local GIO implementations; never mix incompatible module/library versions.
        env["GIO_MODULE_DIR"] = str(Path(sys._MEIPASS) / "gio-modules")
        env.pop("GIO_EXTRA_MODULES", None)
    if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        env.setdefault("QT_QPA_PLATFORM", "xcb")
    if "fcitx" in env.get("XMODIFIERS", "").lower():
        env.setdefault("QT_IM_MODULE", "fcitx")
    if plugin_dir is None:
        spec = importlib.util.find_spec("PySide6")
        if spec and spec.origin:
            plugin_dir = Path(spec.origin).parent / "Qt" / "plugins"
    if plugin_dir is not None:
        # PySide's private Qt ABI must not load system Qt/fcitx plugins (#71).
        env["QT_PLUGIN_PATH"] = str(plugin_dir)
        external = env.get("QT_QPA_PLATFORM_PLUGIN_PATH")
        if external and Path(external) != Path(plugin_dir) / "platforms":
            env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)


def capabilities(*, platform=None, qpa=None, environ=None) -> dict[str, DesktopCapability]:
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    qpa = (qpa or env.get("QT_QPA_PLATFORM", "")).split(":")[0].split(";")[0].lower()
    desktop = platform in ("win32", "darwin", "linux")
    windows = platform == "win32"
    ready = DesktopCapability(True, True)
    win_only = DesktopCapability(windows, windows, "" if windows else "此能力目前仅支持 Windows。")
    result = {name: win_only for name in (
        "global_hotkeys", "fullscreen_detection", "foreground_window", "microphone",
        "system_speech", "system_media", "gpu", "game_fps",
    )}
    result["autostart"] = DesktopCapability(desktop, desktop, "" if desktop else "当前平台没有自启后端。")
    result["desktop_input"] = ready
    result["screen_capture"] = DesktopCapability(desktop, desktop)
    if platform == "darwin" and sys.platform == "darwin" and qpa not in ("offscreen", "minimal"):
        from . import macos
        try:
            granted = macos.screen_capture_preflight()
        except (OSError, AttributeError):
            result["screen_capture"] = DesktopCapability(True, False, "无法检查系统录屏权限，请在桌面会话中重新运行诊断。")
        else:
            if not granted:
                result["screen_capture"] = DesktopCapability(True, False, "需要在系统设置的屏幕录制权限中允许此应用；点击截图入口可请求授权。")
    if platform == "linux":
        if qpa.startswith("wayland"):
            result["desktop_input"] = DesktopCapability(False, False, "原生 Wayland 暂不支持桌宠定位；请安装 XWayland 并使用 QT_QPA_PLATFORM=xcb。")
        elif not env.get("DISPLAY") and qpa not in ("offscreen", "minimal"):
            result["desktop_input"] = DesktopCapability(True, False, "没有 X11 DISPLAY；请在桌面会话安装并启用 XWayland/xcb。")
    elif not desktop:
        result["desktop_input"] = DesktopCapability(False, False, "当前桌面平台尚未适配。")
    return result


def startup_problem(*, platform=None, qpa=None, environ=None) -> str:
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    qpa = (qpa or env.get("QT_QPA_PLATFORM", "xcb")).split(":")[0].split(";")[0]
    capability = capabilities(platform=platform, qpa=qpa, environ=env)["desktop_input"]
    if not capability.available:
        return capability.reason
    if platform == "linux" and qpa == "xcb":
        # Loading the plugin without creating its platform integration reports
        # missing shared libraries before QApplication would abort the process.
        from PySide6.QtCore import QLibraryInfo, QPluginLoader
        plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
        loader = QPluginLoader(str(plugins / "platforms/libqxcb.so"))
        if not loader.load():
            return ("Qt xcb 无法加载。Ubuntu 请安装 libxcb-cursor0 libxcb-xfixes0 "
                    "libxkbcommon-x11-0 libegl1；Wayland 会话还需 xwayland。\n" + loader.errorString())
    return ""


def diagnostic_report(*, qpa=None) -> dict:
    return {
        "platform": sys.platform,
        "qpa": qpa or os.environ.get("QT_QPA_PLATFORM", "auto"),
        "session": os.environ.get("XDG_SESSION_TYPE", ""),
        "capabilities": {key: asdict(value) for key, value in capabilities(qpa=qpa).items()},
    }
