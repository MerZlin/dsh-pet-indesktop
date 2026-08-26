# -*- coding: utf-8 -*-
"""macOS 激活策略（防抢焦点）冒烟测试。

在 macOS runner（CI）上实测 ctypes→objc 调用链：
- _mac_set_accessory_activation() 设置成功且读回 activationPolicy == 1 (Accessory)
- 应用处于 Accessory 策略时窗口 show 不激活、不抢焦点（策略层面的前提）

Windows / Linux 上跳过（无 AppKit/objc runtime 语义）。
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS only（AppKit/objc runtime 语义）",
)


def test_macos_accessory_activation_succeeds():
    """设置 Accessory 策略必须成功（含读回验证），失败即说明抢焦点根因仍在。"""
    from pet.app import _mac_set_accessory_activation

    assert _mac_set_accessory_activation(attempt=0) is True


def test_macos_activation_policy_readback_is_accessory():
    """直接读 NSApplication.activationPolicy，必须为 1（Accessory）。"""
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
    assert policy == 1
