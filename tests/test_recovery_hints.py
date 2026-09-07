# -*- coding: utf-8 -*-
"""“小白找不回来”开关的恢复提示回归测试（issue #74）。

覆盖两类危险方向：
- 开启「鼠标穿透」后桌宠不可点击 → 应在开启瞬间提示去托盘/设置里恢复；
- macOS 关闭「显示 Dock 图标」后 Dock 入口消失 → 应提示去菜单栏托盘图标恢复。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from pet.app import PetInstance
from pet.config import Config
from pet.window import PetWindow
from tests.test_window_pause import FakeLibrary


def _qapp():
    return QApplication.instance() or QApplication([])


def _make_win(tmp_path, monkeypatch):
    _qapp()
    win = PetWindow(FakeLibrary(), Config(base=tmp_path))
    # 避免穿透切换真的重建原生窗口/调用 Win32，本组测试只关心提示。
    monkeypatch.setattr(win, "_apply_effective_mouse_through", lambda enabled=None: None)
    return win


class _FakeWin:
    def __init__(self):
        self.bubbles = []
        self.visible = True

    def show_bubble(self, text, **kwargs):
        self.bubbles.append(text)

    def isVisible(self):
        return self.visible


class _FakeTray:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, text, icon, duration_ms):
        self.messages.append((title, text))


def _bare_instance(tmp_path):
    inst = PetInstance.__new__(PetInstance)
    inst.config = Config(base=tmp_path)
    return inst


def test_enabling_mouse_through_shows_recovery_hint(tmp_path, monkeypatch):
    app = _qapp()
    win = _make_win(tmp_path, monkeypatch)
    try:
        hints = []
        monkeypatch.setattr(win, "isVisible", lambda: True)
        monkeypatch.setattr(win, "show_bubble", lambda text, **kw: hints.append(text))

        win.set_mouse_through(True)
        assert len(hints) == 1, "开启鼠标穿透应提示一次恢复位置"
        assert "鼠标穿透" in hints[0]

        # 关闭方向不需要提示；再次开启才提示。
        win.set_mouse_through(False)
        win.set_mouse_through(True)
        assert len(hints) == 2
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


def test_enabling_mouse_through_hint_suppressed_when_hidden_or_suppressed(tmp_path, monkeypatch):
    app = _qapp()
    win = _make_win(tmp_path, monkeypatch)
    try:
        hints = []
        monkeypatch.setattr(win, "isVisible", lambda: True)
        monkeypatch.setattr(win, "show_bubble", lambda text, **kw: hints.append(text))

        win._bubble_suppressed = True
        win.set_mouse_through(True)
        assert hints == [], "设置窗口打开（气泡抑制）期间不应弹恢复提示"

        win._bubble_suppressed = False
        monkeypatch.setattr(win, "isVisible", lambda: False)
        win.set_mouse_through(False)
        win.set_mouse_through(True)
        assert hints == [], "桌宠隐藏时不应尝试在不可见窗口上弹提示"
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


def test_dock_hidden_recovery_hint_uses_bubble_when_visible(tmp_path, monkeypatch):
    import pet.app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    inst = _bare_instance(tmp_path)
    win = _FakeWin()
    inst.win = win
    inst.shell = SimpleNamespace(tray=_FakeTray())

    inst._hint_dock_hidden_recovery()

    assert len(win.bubbles) == 1
    assert "显示 Dock 图标" in win.bubbles[0]
    assert "托盘" in win.bubbles[0] or "菜单栏" in win.bubbles[0]


def test_dock_hidden_recovery_hint_uses_tray_when_pet_hidden(tmp_path, monkeypatch):
    import pet.app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    inst = _bare_instance(tmp_path)
    win = _FakeWin()
    win.visible = False
    inst.win = win
    tray = _FakeTray()
    inst.shell = SimpleNamespace(tray=tray)

    inst._hint_dock_hidden_recovery()

    assert win.bubbles == []
    assert len(tray.messages) == 1
    assert "显示 Dock 图标" in tray.messages[0][1]


def test_dock_hidden_recovery_hint_noop_off_macos(tmp_path, monkeypatch):
    import pet.app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "win32")
    inst = _bare_instance(tmp_path)
    win = _FakeWin()
    inst.win = win
    inst.shell = SimpleNamespace(tray=_FakeTray())

    inst._hint_dock_hidden_recovery()

    assert win.bubbles == []
    assert inst.shell.tray.messages == []
