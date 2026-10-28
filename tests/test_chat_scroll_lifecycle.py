"""Pending chat scrolling must stop as soon as the window closes."""

import threading

import pytest

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
import shiboken6

from pet.chat.legacy_widgets import ChatWindow as LegacyChatWindow
from pet.chat.widgets import ChatWindow as ModernChatWindow
from pet.config import Config


app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("window_type", [LegacyChatWindow, ModernChatWindow])
def test_close_cancels_scroll_while_request_is_still_exiting(tmp_path, window_type):
    entered = threading.Event()
    release = threading.Event()

    class Provider:
        def stream(self, messages, config, cancel):
            entered.set()
            assert release.wait(30), "test did not release the network boundary"
            yield "reply"

    window = window_type(Config(tmp_path), "shenshen")
    window.service.provider = Provider()
    try:
        window.resize(700, 500)
        window.show()
        window.append_look_sync("question", "long reply\n" * 100)
        app.processEvents()
        bar = window.scroll.verticalScrollBar()
        if bar.maximum() == 0:
            assert QSignalSpy(bar.rangeChanged).wait(5000), "timeline layout did not finish"
        assert bar.maximum() > 0
        window.composer.input.setPlainText("wait for the network")
        window.send_message()
        assert entered.wait(5), "request did not enter the provider"
        window.reject()
        assert window.service.has_running_workers()
        assert shiboken6.isValid(window), "closing must wait for the worker"
        bar.setValue(0)
        # Observe past the existing 80ms deadline through a real Qt event loop.
        marker = QTimer()
        marker.setTimerType(Qt.TimerType.PreciseTimer)
        marker.setSingleShot(True)
        marker.setInterval(200)
        elapsed = QSignalSpy(marker.timeout)
        marker.start()
        assert elapsed.wait(5000), "Qt deadline was not delivered"
        assert bar.value() == 0, "a closed window received late scrolling"
    finally:
        if shiboken6.isValid(window):
            window.reject()
            destroyed = QSignalSpy(window.destroyed)
            release.set()
            assert window.service.shutdown(), "provider worker did not exit"
            assert destroyed.wait(10000), "window did not finish its queued release"
        else:
            release.set()
