# -*- coding: utf-8 -*-
"""灵动岛置顶保活：被其它 topmost 窗盖过后把自己顶回 topmost 带最上。

归因（Windows）：岛带 ``WindowStaysOnTopHint``（= WS_EX_TOPMOST）但
``WindowDoesNotAcceptFocus``（= WS_EX_NOACTIVATE），**从不被激活**；而 Windows
的 topmost 带内先后由"谁最近被激活/显示"决定——任何被激活的 topmost 窗
（本进程聊天窗/设置窗/岛对话气泡，或第三方置顶工具）都会盖在岛上面，岛自己
没有任何机制再顶回去（grep pet/ 全树：dynamic_island / island_bridge /
overlay_window / platform_win 里既无 ``raise_()`` 也无 ``SetWindowPos``）。

修法 = 恢复 6ebfd2b 的 Windows 置顶保活口径（原生 ``SetWindowPos`` 重申 +
事件驱动），落在 platform_win 抽象与岛侧：

- 原生原语 ``platform_win._set_windows_topmost``（SetWindowPos + argtypes，
  64 位 HWND 安全）；
- 岛的公开入口 ``DynamicIsland.reassert_topmost``：Windows 走原生，其它平台
  回退 Qt ``raise_()``（macOS/Linux 语义未验证）；
- 触发点全部是事件驱动或复用既有通道（show / 岛自身刷新心跳 / 本进程窗口被
  激活 / 点击岛），**不新增定时器与线程**；
- 外部应用抢前台（开始菜单、全屏游戏、第三方置顶工具）**不**重申——否则岛会
  反过来盖住系统级置顶面（legacy「全屏应用会暂时盖住桌宠」的既有语义）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from pet import platform_win
from pet.config import Config
from pet.dynamic_island import DynamicIsland


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path: Path, **overrides) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    data = {"enabled": True, "x": 400, "y": 300}
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


def _settle(app: QApplication, rounds: int = 3) -> None:
    for _ in range(rounds):
        app.processEvents()


def _spy_native_reassert(monkeypatch) -> list:
    """监视岛调用平台层原生重申的 seam（记录被重申的窗口）。

    ``raising=False``：产品改动前该 seam 不存在，此时监视表为空——测试以
    「重申没有被触发」的断言失败作为红，而不是 ImportError。
    """
    from pet import dynamic_island as island_mod

    calls: list = []
    monkeypatch.setattr(
        island_mod, "_native_reassert_topmost",
        lambda window: (calls.append(window), True)[1], raising=False)
    return calls


def _topmost_cover() -> QWidget:
    """真实的"另一个 topmost 窗"：聊天窗/设置窗/气泡都是这个旗标组合。"""
    cover = QWidget()
    cover.setWindowFlags(
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
    )
    cover.show()
    cover.raise_()
    return cover


def test_island_returns_to_front_when_own_topmost_window_takes_focus(
        tmp_path, monkeypatch):
    """复现：本进程 topmost 窗（聊天窗/设置窗/岛气泡）被激活后岛被压在下面。

    岛从不激活 ⇒ 被压下去后不会自己回来，必须由前台/焦点事件把它顶回去。
    """
    app = _qapp()
    island = _island(tmp_path)
    island.show()
    _settle(app)
    cover = _topmost_cover()
    try:
        if not hasattr(app, "focusWindowChanged"):  # Qt < 6.5 无该信号
            pytest.skip("本 Qt 版本无 QGuiApplication.focusWindowChanged")
        calls = _spy_native_reassert(monkeypatch)
        app.focusWindowChanged.emit(cover.windowHandle())
        assert calls == [island], "本进程 topmost 窗激活后岛未重申置顶"
    finally:
        cover.hide()
        island.hide()
        island.deleteLater()


def test_island_returns_to_front_when_app_becomes_active(tmp_path, monkeypatch):
    """本体窗口激活（应用回到前台）同样要把岛顶回去。"""
    app = _qapp()
    island = _island(tmp_path)
    island.show()
    _settle(app)
    try:
        calls = _spy_native_reassert(monkeypatch)
        app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        assert calls == [island], "应用回到前台后岛未重申置顶"
    finally:
        island.hide()
        island.deleteLater()


def test_island_does_not_fight_external_foreground(tmp_path, monkeypatch):
    """外部应用抢到前台时不重申：不跟开始菜单/全屏游戏/第三方置顶工具抢 z 序。"""
    app = _qapp()
    island = _island(tmp_path)
    island.show()
    _settle(app)
    try:
        calls = _spy_native_reassert(monkeypatch)
        app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
        if hasattr(app, "focusWindowChanged"):
            # 焦点移出本应用（Qt 语义）：焦点窗口变 None
            app.focusWindowChanged.emit(None)
        assert calls == [], "外部应用进入前台时岛不应重申置顶"
    finally:
        island.hide()
        island.deleteLater()


def test_island_reasserts_topmost_on_show(tmp_path, monkeypatch):
    """显示即保证 topmost 带最上（Qt 只在 flags 变化时写一次 WS_EX_TOPMOST）。"""
    app = _qapp()
    island = _island(tmp_path)
    calls = _spy_native_reassert(monkeypatch)
    island.show()
    _settle(app)
    try:
        assert calls, "岛显示后未重申置顶"
    finally:
        island.hide()
        island.deleteLater()


def test_island_click_reasserts_topmost(tmp_path, monkeypatch):
    """点击岛 = legacy「点击把窗口带回置顶组最前」口径（不抢键盘焦点）。"""
    app = _qapp()
    island = _island(tmp_path)
    island.show()
    _settle(app)
    try:
        calls = _spy_native_reassert(monkeypatch)
        pos = QPointF(10.0, 10.0)
        island.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, pos, pos,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))
        assert calls == [island], "点击岛后未重申置顶"
    finally:
        island.hide()
        island.deleteLater()


def test_existing_refresh_heartbeat_reasserts_topmost(tmp_path, monkeypatch):
    """复用既有 30s 信息心跳（不新增定时器）：系统事件导致置顶丢失的兜底。"""
    app = _qapp()
    island = _island(tmp_path)
    island.show()
    _settle(app)
    try:
        calls = _spy_native_reassert(monkeypatch)
        island._info_timer.timeout.emit()
        assert calls == [island], "既有信息心跳未重申置顶"
    finally:
        island.hide()
        island.deleteLater()


def test_hidden_island_stays_silent(tmp_path, monkeypatch):
    """隐藏即静默：不碰平台层（岛关闭时不做无谓的 Win32 z 序写入）。"""
    island = _island(tmp_path)
    island.hide()
    calls = _spy_native_reassert(monkeypatch)
    fallbacks: list = []
    monkeypatch.setattr(
        DynamicIsland, "raise_", lambda self: fallbacks.append(self))
    assert island.reassert_topmost() is False
    assert calls == [] and fallbacks == []


def test_macos_reassert_does_not_raise_or_activate(tmp_path, monkeypatch):
    """macOS 置顶保活不能通过 QWidget.raise_() 激活桌宠应用。"""
    app = _qapp()
    island = _island(tmp_path)
    island.show()
    _settle(app)
    try:
        from pet import dynamic_island as island_mod

        fallbacks: list = []
        monkeypatch.setattr(island_mod.sys, "platform", "darwin")
        monkeypatch.setattr(
            DynamicIsland, "raise_", lambda self: fallbacks.append(self))
        assert island.reassert_topmost() is True
        assert fallbacks == []
    finally:
        island.hide()
        island.deleteLater()


def test_island_keeps_topmost_and_non_activating_flags(tmp_path):
    """旗标口径不动：修 z 序不回退成"可激活窗口"（激活会抢用户键盘焦点）。"""
    island = _island(tmp_path)
    flags = island.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert island.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


class _FakeSetWindowPos:
    """可注入的 SetWindowPos 替身（要能吃 argtypes/restype 赋值，故用可调用对象）。"""

    def __init__(self, sink: list, result: int = 1) -> None:
        self._sink = sink
        self._result = result
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self._sink.append(args)
        return self._result


class _FakeUser32:
    def __init__(self, result: int = 1) -> None:
        self.calls: list = []
        self.SetWindowPos = _FakeSetWindowPos(self.calls, result)


def test_set_windows_topmost_posts_topmost_flag_without_activating():
    user32 = _FakeUser32()
    assert platform_win._set_windows_topmost(123, True, user32) is True
    assert user32.calls == [(
        123, platform_win._HWND_TOPMOST, 0, 0, 0, 0,
        platform_win._SWP_NOSIZE | platform_win._SWP_NOMOVE
        | platform_win._SWP_NOACTIVATE,
    )]


def test_set_windows_topmost_off_demotes_to_notopmost():
    user32 = _FakeUser32()
    assert platform_win._set_windows_topmost(123, False, user32) is True
    assert user32.calls[0][1] == platform_win._HWND_NOTOPMOST


def test_set_windows_topmost_reports_api_failure():
    user32 = _FakeUser32(result=0)
    assert platform_win._set_windows_topmost(123, True, user32) is False
