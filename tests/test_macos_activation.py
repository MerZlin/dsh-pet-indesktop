# -*- coding: utf-8 -*-
"""macOS Dock 可见性激活策略冒烟测试。

在 macOS runner（CI）上实测 ctypes→objc 调用链：
- 默认显示 Dock 图标时使用 Regular 策略。
- 用户关闭 Dock 图标时才切换 Accessory 策略。

Windows / Linux 上跳过（无 AppKit/objc runtime 语义）。
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS only（AppKit/objc runtime 语义）",
)


def test_macos_dock_visibility_policy_can_be_applied():
    from pet.app import _mac_set_dock_icon_visible

    _mac_set_dock_icon_visible(True)


def test_macos_activation_policy_readback_is_regular_by_default():
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "cocoa":
        pytest.skip("requires the real Cocoa Qt platform plugin")
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(
        ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib"
    )
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_getClass.restype = ctypes.c_void_p
    msg = objc.objc_msgSend
    msg.restype = ctypes.c_void_p
    msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    shared = msg(
        objc.objc_getClass(b"NSApplication"),
        objc.sel_registerName(b"sharedApplication"),
    )
    msg.restype = ctypes.c_long
    msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    policy = msg(shared, objc.sel_registerName(b"activationPolicy"))
    assert policy == 0
