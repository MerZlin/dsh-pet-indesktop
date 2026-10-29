"""Windows MSG ABI: no real shutdown/logoff messages are sent."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

import pytest

from petcore.platform.session import SessionWatcher

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows MSG layout")


@pytest.mark.parametrize("message_id", [0x0011, 0x0016])
def test_native_session_message_uses_pointer_width_correct_msg(message_id):
    calls = []
    watcher = SessionWatcher(lambda: calls.append(True))
    message = wintypes.MSG()
    message.hWnd = 0x123456
    message.message = message_id
    assert watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)) == (False, 0)
    assert calls == [True]
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    assert calls == [True]


def test_handle_high_bits_are_not_a_session_message():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64-bit handle regression")
    calls = []
    watcher = SessionWatcher(lambda: calls.append(True))
    message = wintypes.MSG()
    message.hWnd = (0x11 << 32) | 0x1234
    message.message = 0x0200
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    assert calls == []


def test_lock_and_power_messages_pause_and_resume_once():
    calls = []
    watcher = SessionWatcher(
        lambda: calls.append("end"),
        on_lock=lambda: calls.append("lock"),
        on_unlock=lambda: calls.append("unlock"),
    )
    message = wintypes.MSG()
    message.message = 0x02B1  # WM_WTSSESSION_CHANGE
    message.wParam = 0x7
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    message.wParam = 0x8
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    message.message = 0x0218  # WM_POWERBROADCAST
    message.wParam = 0x0004
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    message.wParam = 0x0012
    watcher.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    assert calls == ["lock", "unlock", "lock", "unlock"]
