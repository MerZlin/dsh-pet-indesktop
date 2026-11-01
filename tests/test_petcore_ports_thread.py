# -*- coding: utf-8 -*-
"""展示端口（PresentationPort）的线程亲和回归。

现场事故：第三方 mod（旧 ``dsh.channel``）在自己的 SSE 线程里直接
``presentation.say()`` / ``notify()``。``say()`` 会操作气泡控件、``notify()`` 会走
``QSystemTrayIcon.showMessage``——两者都只能在 GUI 线程做，否则会和 GUI 线程互锁
（用户看到的「审批卡弹出时桌宠卡死」，Windows 记 AppHang）。

本文件钉三件事：后台线程的 say/notify 会被排队到 GUI 线程执行；GUI 线程调用
保持同步语义；排队返回 False 而不是假装已显示。
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QWidget

from petcore.ui.app import _PresentationAdapter
from petcore.ui.ports import QtScheduler


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QThread.msleep(5)
    QApplication.processEvents()
    return bool(predicate())


class _SpyPet(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.shown: list[str] = []
        self.caller: QThread | None = None
        self.visible = True

    def isVisible(self) -> bool:  # noqa: N802 - Qt API
        return self.visible

    def say(self, text: str, duration_ms: int = 4000, *, subtitle: str = "") -> bool:
        self.caller = QThread.currentThread()
        self.shown.append(str(text))
        return True


class _Host:
    def __init__(self, pet: QWidget) -> None:
        self.window = pet
        self._active_window = pet
        self.pets = None
        self.quiet = None
        self.settings_dialog = None
        self.island = None
        self.scheduler = QtScheduler()


def _adapter(pet: QWidget) -> _PresentationAdapter:
    return _PresentationAdapter(_Host(pet))


def test_say_from_worker_thread_runs_on_the_gui_thread(app) -> None:
    pet = _SpyPet()
    pet.show()
    app.processEvents()
    adapter = _adapter(pet)
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(adapter.say("后台气泡")),
        name="legacy-mod-sse",
        daemon=True,
    )
    try:
        thread.start()
        thread.join(timeout=2.0)
        assert result == [False], "排队中的展示不能假装已经显示"
        assert pet.shown == [], "worker 线程不得同步操作气泡"
        assert _wait_until(lambda: bool(pet.shown))
        assert pet.shown == ["后台气泡"]
        assert pet.caller == app.thread()
    finally:
        pet.close()
        pet.deleteLater()


def test_notify_from_worker_thread_is_marshalled(app) -> None:
    pet = _SpyPet()
    pet.show()
    app.processEvents()
    adapter = _adapter(pet)
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(adapter.notify("提醒", "后台通知")),
        name="legacy-mod-worker",
        daemon=True,
    )
    try:
        thread.start()
        thread.join(timeout=2.0)
        assert result == [True], "已排队的通知按已受理返回"
        assert _wait_until(lambda: bool(pet.shown))
        assert pet.shown == ["提醒：后台通知"]
        assert pet.caller == app.thread()
    finally:
        pet.close()
        pet.deleteLater()


def test_say_on_the_gui_thread_stays_synchronous(app) -> None:
    pet = _SpyPet()
    pet.show()
    app.processEvents()
    adapter = _adapter(pet)
    try:
        assert adapter.say("前台气泡") is True
        assert pet.shown == ["前台气泡"]
        assert pet.caller == app.thread()
    finally:
        pet.close()
        pet.deleteLater()
