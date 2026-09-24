# -*- coding: utf-8 -*-
"""右键菜单树释放（delete_when_idle）生命周期回归。

菜单 exec 返回后，若动画图标解码 worker 未结束，菜单树不是立即 deleteLater，
而是每 50ms 轮询等待（delete_when_idle）。轮询定时器必须绑定 menu 作为
context：窗口/菜单先销毁时，未绑定的回调会继续触发并访问已删 C++ 对象
（GUI 线程 RuntimeError traceback）。
"""

from __future__ import annotations

import sys
import time

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMenu

import pet.window as window_mod
from pet import catalog
from pet.config import Config
from pet.window import PetWindow


class FakeClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        pass

    def start(self):
        return True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        pass

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self):
        self._clips = {n: FakeClip() for n in [catalog.IDLE, catalog.TURN, catalog.MOVES[0], catalog.CLICKS[0], catalog.DRAG]}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()
        self.move_strides = {}
        self.move_curves = {}

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


class _Screen:
    def availableGeometry(self):
        return QRect(0, 0, 1920, 1080)

    def devicePixelRatio(self):
        return 1.0


class _SlowPool:
    """前两次 waitForDone 未结束（触发 50ms 轮询重挂），之后完成。"""

    def __init__(self):
        self.calls = 0

    def clear(self):
        pass

    def waitForDone(self, _ms):
        self.calls += 1
        return self.calls > 2


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_delete_when_idle_timer_drops_after_menu_destroyed(app, tmp_path, monkeypatch):
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    monkeypatch.setattr(win, "_screen_available", lambda: _Screen())
    monkeypatch.setattr(win, "_rebuild_frame", lambda: None)

    pool = _SlowPool()
    built: list[QMenu] = []

    def fake_populate(menu, pet):
        submenu = menu.addMenu("动画")
        submenu._animation_icon_pool = pool
        built.append(menu)

    monkeypatch.setattr(window_mod, "_populate_context_menu", fake_populate)
    monkeypatch.setattr(PetWindow, "_exec_context_menu", lambda self, menu, pos: None)

    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda et, ev, tb: errors.append(ev))

    win._show_context_menu(QPoint(100, 100))
    assert built, "菜单必须经 populate 构建"
    menu = built[0]
    assert pool.calls == 1, "worker 未结束：首轮轮询后应已重挂 50ms 定时器"

    # 窗口/菜单在轮询途中销毁：context 绑定的定时器必须随 menu 失效被丢弃，
    # 未绑定则继续触发并对已删 C++ 对象 deleteLater（RuntimeError）。
    # 注意：offscreen 下 deleteLater+processEvents 不会真正销毁对象（实测），
    # 必须显式 sendPostedEvents(DeferredDelete)。
    from PySide6.QtCore import QCoreApplication, QEvent

    menu.deleteLater()
    QCoreApplication.sendPostedEvents(menu, QEvent.Type.DeferredDelete)
    app.processEvents()
    _pump(app, 0.5)

    assert pool.calls < 3, "菜单销毁后轮询定时器必须停止触发"
    assert errors == [], f"销毁后不得访问已删 C++ 对象：{errors}"

    win.close()
    app.processEvents()
