"""桌宠合成窗的 Spaces 策略：原生边界与显示生命周期。"""
import os

# 文件级 offscreen 兜底（同族 166 个测试模块同例）：import 期即建 QApplication，
# 无显示环境单独跑本文件时必须已有平台插件（PR #333 审查 S1）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import QApplication

from pet import platform_mac
from pet.overlay_window import OverlayWindow

app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("initial, expected", [(258, 257), (9, 9), (0, 1)])
def test_spaces_policy_preserves_other_native_behavior(monkeypatch, initial, expected):
    from pet.platform_mac import _mac_set_window_all_spaces

    behavior = initial

    def send(receiver, selector, *args):
        nonlocal behavior
        assert receiver.value in (100, 200)
        if selector == b"window":
            return 200
        if selector == b"collectionBehavior":
            assert send.restype is ctypes.c_ulong
            return behavior
        assert selector == b"setCollectionBehavior:"
        assert send.argtypes[-1] is ctypes.c_ulong
        behavior = args[0]

    def selector(name):
        return name

    objc = SimpleNamespace(objc_msgSend=send, sel_registerName=selector)
    monkeypatch.setattr(platform_mac.sys, "platform", "darwin")
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "cocoa")
    monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _: objc)
    assert _mac_set_window_all_spaces(100)
    assert behavior == expected


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_spaces_policy_is_safe_off_cocoa(monkeypatch, platform):
    from pet.platform_mac import _mac_set_window_all_spaces

    if QGuiApplication.platformName() == "cocoa":
        pytest.skip("requires a non-Cocoa platform")
    monkeypatch.setattr(platform_mac.sys, "platform", platform)
    assert _mac_set_window_all_spaces(0) is False


def test_every_overlay_reapplies_spaces_policy_after_show(monkeypatch):
    import pet.overlay_window as overlay_mod

    seen = []
    monkeypatch.setattr(overlay_mod, "_mac_set_window_all_spaces", seen.append)
    windows = [OverlayWindow(), OverlayWindow()]
    try:
        for window in windows:
            window.show()
        assert seen == [int(window.winId()) for window in windows]
        seen.clear()
        for window in windows:
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            window.show()
        assert seen == [int(window.winId()) for window in windows]
    finally:
        for window in windows:
            window.close()
            window.deleteLater()


@pytest.mark.skipif(QGuiApplication.platformName() != "cocoa", reason="requires Cocoa")
def test_native_spaces_policy_on_hidden_overlay():
    window = OverlayWindow()
    try:
        assert not window.isVisible()
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        for on_top in (True, False, True):
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            send.restype = ctypes.c_void_p
            send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            native = send(int(window.winId()), objc.sel_registerName(b"window"))
            assert native
            send.restype = ctypes.c_ulong
            before = send(native, objc.sel_registerName(b"collectionBehavior"))
            QCoreApplication.sendEvent(window, QShowEvent())
            after = send(native, objc.sel_registerName(b"collectionBehavior"))
            assert after & 1
            assert not after & 2
            assert after & ~3 == before & ~3
    finally:
        window.close()
