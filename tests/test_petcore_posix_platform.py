from __future__ import annotations

import json
import plistlib
import re
import shlex
import sys
from pathlib import Path

import pytest

from petcore.platform import autostart


def test_wayland_uses_xcb_without_overriding_explicit_choice():
    from petcore.platform.desktop import prepare_environment

    env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0", "XMODIFIERS": "@im=fcitx"}
    prepare_environment(env, platform="linux")
    assert env["QT_QPA_PLATFORM"] == "xcb"
    assert env["QT_IM_MODULE"] == "fcitx"
    explicit = {"WAYLAND_DISPLAY": "wayland-0", "QT_QPA_PLATFORM": "offscreen"}
    prepare_environment(explicit, platform="linux")
    assert explicit["QT_QPA_PLATFORM"] == "offscreen"
    other = {"WAYLAND_DISPLAY": "wayland-0"}
    prepare_environment(other, platform="darwin")
    assert "QT_QPA_PLATFORM" not in other


def test_linux_environment_does_not_load_foreign_qt_plugins(tmp_path):
    from petcore.platform.desktop import prepare_environment

    plugins = tmp_path / "PySide6" / "Qt" / "plugins"
    env = {"QT_PLUGIN_PATH": "/usr/lib/qt6/plugins", "QT_QPA_PLATFORM_PLUGIN_PATH": "/other/qt"}
    prepare_environment(env, platform="linux", plugin_dir=plugins)
    assert env["QT_PLUGIN_PATH"] == str(plugins)
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in env


def test_frozen_linux_isolates_gio_modules_from_newer_host_glib(tmp_path, monkeypatch):
    from petcore.platform.desktop import prepare_environment

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    env = {"GIO_MODULE_DIR": "/usr/lib/gio/modules", "GIO_EXTRA_MODULES": "/external/gio"}
    prepare_environment(env, platform="linux", plugin_dir=tmp_path / "plugins")
    assert env["GIO_MODULE_DIR"] == str(tmp_path / "gio-modules")
    assert "GIO_EXTRA_MODULES" not in env
    monkeypatch.setattr(sys, "frozen", False)
    source_env = {"GIO_MODULE_DIR": "/source/gio", "GIO_EXTRA_MODULES": "/external/gio"}
    prepare_environment(source_env, platform="linux", plugin_dir=tmp_path / "plugins")
    assert source_env["GIO_MODULE_DIR"] == "/source/gio"
    assert source_env["GIO_EXTRA_MODULES"] == "/external/gio"


@pytest.mark.parametrize("platform,frozen,expected", [
    ("linux", True, True), ("linux", False, False), ("darwin", True, False),
])
def test_frozen_linux_dialog_policy_avoids_host_gtk_input_modules(monkeypatch, platform, frozen, expected):
    from PySide6.QtCore import QCoreApplication, Qt
    from petcore.platform import window_policy

    attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
    previous = QCoreApplication.testAttribute(attribute)
    QCoreApplication.setAttribute(attribute, False)
    monkeypatch.setattr(window_policy.sys, "platform", platform)
    monkeypatch.setattr(window_policy.sys, "frozen", frozen, raising=False)
    try:
        window_policy.configure_application()
        assert QCoreApplication.testAttribute(attribute) is expected
    finally:
        QCoreApplication.setAttribute(attribute, previous)


def test_capabilities_distinguish_os_support_and_missing_session():
    from petcore.platform.desktop import capabilities

    linux = capabilities(platform="linux", qpa="xcb", environ={})
    assert linux["autostart"].supported
    assert not linux["global_hotkeys"].supported
    assert not linux["desktop_input"].available
    assert linux["desktop_input"].reason
    assert capabilities(platform="linux", qpa="xcb", environ={"DISPLAY": ":0"})["desktop_input"].available
    assert not capabilities(platform="linux", qpa="wayland", environ={})["desktop_input"].supported
    assert capabilities(platform="darwin", qpa="cocoa")["desktop_input"].available
    assert capabilities(platform="win32")["global_hotkeys"].supported


@pytest.mark.parametrize("qpa", ["", "cocoa"])
@pytest.mark.parametrize("granted", [False, True])
def test_mac_diagnostics_preflight_capture_without_a_cocoa_app(monkeypatch, qpa, granted):
    from petcore.platform import desktop, macos

    calls = []
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(macos, "is_cocoa", lambda: False)
    def preflight():
        calls.append("preflight")
        return granted
    monkeypatch.setattr(macos, "_capture_api", lambda: (
        preflight, lambda: pytest.fail("diagnostics must not request permission"),
    ))
    capability = desktop.capabilities(qpa=qpa, environ={})["screen_capture"]
    assert calls == ["preflight"]
    assert capability.supported
    assert capability.available is granted
    assert bool(capability.reason) is not granted


def test_mac_diagnostics_reports_permission_probe_failure(monkeypatch):
    from petcore.platform import desktop, macos

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(macos, "is_cocoa", lambda: False)
    def unavailable():
        raise OSError("framework unavailable")
    monkeypatch.setattr(macos, "_capture_api", unavailable)
    capability = desktop.capabilities(qpa="cocoa", environ={})["screen_capture"]
    assert capability.supported and not capability.available
    assert "检查" in capability.reason


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_autostart_roundtrip_uses_argv_root_instance_and_cwd(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(autostart.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "带 空格" / "python"))
    monkeypatch.setattr(autostart, "_launch_context", None)
    root = tmp_path / "用户 数据"
    autostart.configure(root=root, instance="pet2", working_directory=tmp_path)
    assert autostart.supported()
    assert not autostart.is_enabled()
    assert autostart.enable()
    assert autostart.enable()
    assert autostart.is_enabled()
    entry = autostart.entry_path()
    raw = entry.read_bytes()
    if platform == "darwin":
        doc = plistlib.loads(raw)
        assert doc["ProgramArguments"] == [sys.executable, "-m", "petcore", "--root", str(root), "--instance", "pet2"]
        assert doc["WorkingDirectory"] == str(tmp_path)
        assert doc["RunAtLoad"] is True
        assert not doc.get("KeepAlive", False)
    else:
        text = raw.decode("utf-8")
        values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        unescape = lambda value: re.sub(r"\\(.)", lambda match: {"n": "\n", "r": "\r", "t": "\t"}.get(match[1], match[1]), value)
        assert shlex.split(unescape(values["Exec"])) == [sys.executable, "-m", "petcore", "--root", str(root), "--instance", "pet2"]
        assert unescape(values["Path"]) == str(tmp_path)
        assert "Terminal=false" in text
    assert autostart.disable() and autostart.disable()
    assert not entry.exists()


def test_linux_autostart_escapes_desktop_field_codes_and_shell_chars(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / 'pet % $ ` "'))
    monkeypatch.setattr(autostart, "_launch_context", None)
    assert autostart.enable()
    text = autostart.entry_path().read_text(encoding="utf-8")
    assert "%%" in text
    assert '\\\\$' in text and '\\\\`' in text
    assert "sh -c" not in text


def test_viewport_keeps_native_window_inside_workarea_without_changing_body_position():
    from petcore.engine.viewport import viewport_for

    canvas = (-120, 0, 320, 200)
    view = viewport_for(canvas, (0, 0, 1920, 1080))
    assert view.native == (0, 0, 320, 200)
    assert view.offset == (-120, 0)
    assert view.native[0] + view.offset[0] + 120 == 0
    large = viewport_for((-50, -40, 3000, 2000), (0, 0, 1920, 1080))
    assert large.native == (0, 0, 1920, 1080)
    assert large.offset == (-50, -40)


def test_diagnostics_are_serializable_without_qt():
    from petcore.platform.desktop import diagnostic_report

    report = diagnostic_report()
    assert "capabilities" in report and "platform" in report
    json.dumps(report)


def test_linux_data_root_honors_absolute_xdg_config_home(tmp_path, monkeypatch):
    from petcore import paths
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "配置"))
    assert paths.base_dir() == tmp_path / "配置"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-is-invalid")
    assert paths.base_dir() == Path.home() / ".config"


def test_linux_missing_xcb_dependency_has_actionable_message(monkeypatch):
    from types import SimpleNamespace
    from PySide6 import QtCore
    from petcore.platform import desktop
    monkeypatch.setattr(QtCore, "QPluginLoader", lambda _: SimpleNamespace(
        load=lambda: False, errorString=lambda: "libxcb-cursor.so.0: missing"))
    message = desktop.startup_problem(platform="linux", qpa="xcb", environ={"DISPLAY": ":0"})
    assert "libxcb-cursor0" in message and "libxcb-cursor.so.0" in message
    assert not desktop.startup_problem(platform="linux", qpa="offscreen", environ={})
