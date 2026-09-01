# -*- coding: utf-8 -*-
"""Linux Wayland 会话下默认改用 xcb 平台插件的回归测试。

根因：Wayland 协议不允许客户端自行移动顶层窗口，桌宠拖动依赖的
QWidget.move() 会被合成器静默忽略（表现为"无法拖动"）；透明无边框
窗口在原生 wayland 插件下还有重绘残留（拖影）。修复：创建
QApplication 之前检测 Wayland 会话并把 QT_QPA_PLATFORM 默认设为 xcb，
用户显式设置过该变量时尊重其选择。
"""

import os
import sys

import pytest

from pet.app import _default_xcb_platform_on_wayland


@pytest.fixture
def _clean_platform_env(monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    return monkeypatch


def test_wayland_session_defaults_to_xcb(_clean_platform_env):
    _clean_platform_env.setenv("WAYLAND_DISPLAY", "wayland-0")
    _default_xcb_platform_on_wayland()
    assert os.environ.get("QT_QPA_PLATFORM") == "xcb"


def test_xdg_session_type_wayland_also_detected(_clean_platform_env):
    # 某些环境只导出 XDG_SESSION_TYPE，不导出 WAYLAND_DISPLAY
    _clean_platform_env.setenv("XDG_SESSION_TYPE", "wayland")
    _default_xcb_platform_on_wayland()
    assert os.environ.get("QT_QPA_PLATFORM") == "xcb"


def test_explicit_qt_qpa_platform_is_respected(_clean_platform_env):
    _clean_platform_env.setenv("WAYLAND_DISPLAY", "wayland-0")
    _clean_platform_env.setenv("QT_QPA_PLATFORM", "wayland")
    _default_xcb_platform_on_wayland()
    assert os.environ.get("QT_QPA_PLATFORM") == "wayland"


def test_x11_session_left_untouched(_clean_platform_env):
    _clean_platform_env.setenv("XDG_SESSION_TYPE", "x11")
    _default_xcb_platform_on_wayland()
    assert os.environ.get("QT_QPA_PLATFORM") is None


def test_non_linux_left_untouched(_clean_platform_env):
    _clean_platform_env.setenv("WAYLAND_DISPLAY", "wayland-0")
    _clean_platform_env.setattr(sys, "platform", "win32")
    _default_xcb_platform_on_wayland()
    assert os.environ.get("QT_QPA_PLATFORM") is None
