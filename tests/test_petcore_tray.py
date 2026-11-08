from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.ui.app import CoreHost


class FakeWindow:
    def __init__(self, visible: bool) -> None:
        self.visible = visible
        self.corner = False
        self.quit = False
        self.click_through = False
        self.said: list[str] = []

    def isVisible(self) -> bool:  # noqa: N802
        return self.visible

    def set_visible(self, value: bool) -> None:
        self.visible = bool(value)

    def return_corner(self) -> None:
        self.corner = True

    def quit_from_menu(self) -> None:
        self.quit = True

    def set_click_through(self, value: bool) -> None:
        self.click_through = bool(value)

    def say(self, text, duration_ms: int = 4000) -> None:
        del duration_ms
        self.said.append(str(text))


class FakePets:
    def __init__(self, windows) -> None:
        self.windows = dict(windows)

    def each(self):
        return [self.windows[slot] for slot in sorted(self.windows)]

    def set_visible_all(self, value: bool) -> None:
        for window in self.windows.values():
            window.set_visible(value)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _host(visible: bool = False):
    windows = {0: FakeWindow(visible), 1: FakeWindow(visible)}
    host = SimpleNamespace(
        pets=FakePets(windows),
        window=windows[0],
        tray=None,
        open_settings=lambda *a, **k: None,
        open_data_dir=lambda: None,
        reload_mods=lambda: None,
        quit_app=lambda: None,
    )
    host.toggle_all_visible = lambda: CoreHost.toggle_all_visible(host)
    host.populate_tray_menu = lambda menu: CoreHost.populate_tray_menu(host, menu)
    host.set_all_visible = lambda value: CoreHost.set_all_visible(host, value)
    host._toggle_one = lambda window: CoreHost._toggle_one(host, window)
    host._windows = lambda: CoreHost._windows(host)
    host.all_click_through = lambda: CoreHost.all_click_through(host)
    host.set_all_click_through = lambda value: CoreHost.set_all_click_through(host, value)
    return host, windows


def test_tray_menu_shows_the_pet_when_hidden(app) -> None:
    host, windows = _host(visible=False)
    menu = CoreHost.build_tray_menu(host)
    labels = [action.text() for action in menu.actions() if action.text()]
    assert labels[0] == "显示桌宠"
    menu.actions()[0].trigger()
    assert windows[0].isVisible() and windows[1].isVisible()


def test_tray_menu_hides_the_pet_when_visible(app) -> None:
    host, windows = _host(visible=True)
    menu = CoreHost.build_tray_menu(host)
    assert menu.actions()[0].text() == "隐藏桌宠"
    menu.actions()[0].trigger()
    assert not windows[0].isVisible() and not windows[1].isVisible()


def test_tray_menu_has_per_pet_controls_and_quit(app) -> None:
    host, windows = _host(visible=True)
    menu = CoreHost.build_tray_menu(host)
    labels = [action.text() for action in menu.actions()]
    assert "桌宠设置" in labels
    assert "打开数据目录" in labels
    assert "重新加载模块" not in labels
    assert "重新加载扩展" in labels
    assert labels[-1] == "退出"

    submenus = [action.menu() for action in menu.actions() if action.menu() is not None]
    assert len(submenus) == 2
    assert [action.text() for action in submenus[0].actions()] == ["隐藏", "回到右下角"]
    assert [action.text() for action in submenus[1].actions()] == ["隐藏", "回到右下角", "退出这只"]

    submenus[1].actions()[0].trigger()
    assert windows[1].isVisible() is False
    submenus[1].actions()[2].trigger()
    assert windows[1].quit is True


def test_tray_menu_rebuilds_actions_before_showing(app) -> None:
    host, windows = _host(visible=True)
    menu = CoreHost.build_tray_menu(host)
    assert menu.actions()[0].text() == "隐藏桌宠"

    windows[0].set_visible(False)
    windows[1].set_visible(False)
    menu.aboutToShow.emit()

    assert menu.actions()[0].text() == "显示桌宠"


def test_tray_menu_keeps_the_row_end_check_layer_across_rebuilds(app) -> None:
    """托盘菜单也要勾选层：QSS indicator 只剩 1px，没层 = 鼠标穿透的状态看不见。"""
    from petcore.ui.menu_style import CHECK_LAYER_ATTR, MenuCheckLayer

    host, _windows = _host(visible=True)
    menu = CoreHost.build_tray_menu(host)
    layer = getattr(menu, CHECK_LAYER_ATTR)
    assert isinstance(layer, MenuCheckLayer)

    # 弹出前会原地重建动作：新 QAction 必须由层的 aboutToShow 同步链补接 toggled。
    menu.aboutToShow.emit()
    for _ in range(3):
        app.processEvents()
    rebuilt = next(item for item in menu.actions() if item.text() == "鼠标穿透")
    assert rebuilt.property("menuCheckBound") is True
    assert rebuilt.isCheckable() and rebuilt.isChecked() is False
    menu.close()


def test_tray_menu_toggles_click_through_for_every_pet(app) -> None:
    host, windows = _host(visible=True)
    menu = CoreHost.build_tray_menu(host)
    action = next(item for item in menu.actions() if item.text() == "鼠标穿透")
    assert action.isCheckable() is True
    assert action.isChecked() is False

    action.trigger()
    assert all(window.click_through for window in windows.values())
    assert any("鼠标穿透" in text for text in windows[0].said)
    assert all("仅左键" not in text and "右键长按" not in text for text in windows[0].said)

    menu.aboutToShow.emit()
    checked = next(item for item in menu.actions() if item.text() == "鼠标穿透")
    assert checked.isChecked() is True
    checked.trigger()
    assert not any(window.click_through for window in windows.values())


def test_context_activation_does_not_replace_the_menu(app, monkeypatch) -> None:
    from petcore.ui.tray import TrayIcon

    calls = []
    tray = TrayIcon(lambda _parent: None, Path("missing.ico"), lambda: calls.append("toggle"))
    monkeypatch.setattr(tray, "refresh", lambda: calls.append("refresh"))
    tray._on_activated(tray.ActivationReason.Context)
    assert calls == []

    tray._on_activated(tray.ActivationReason.Trigger)
    assert calls == ["toggle"]

def test_repeated_tray_refresh_releases_owned_submenus(app):
    import gc

    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QMenu

    host, _windows = _host(visible=True)
    menu = CoreHost.build_tray_menu(host)
    try:
        for _ in range(30):
            previous = [action.menu() for action in menu.actions() if action.menu()]
            menu.aboutToShow.emit()
            for child in previous:
                # Restrict deferred deletion to this menu's retired children.
                QCoreApplication.sendPostedEvents(child, QEvent.Type.DeferredDelete)
            app.processEvents()
            gc.collect()
            assert all(not shiboken6.isValid(child) for child in previous)
            assert len(menu.findChildren(QMenu)) == 2
        current = [action.menu() for action in menu.actions() if action.menu()]
        current[1].actions()[0].trigger()
        assert not _windows[1].isVisible()
    finally:
        shiboken6.delete(menu)


def test_cocoa_status_activation_opens_shared_menu_without_toggling(app, monkeypatch):
    """The Cocoa status item must not clear a tracking NSMenu or hide pets."""
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu

    from petcore.platform import macos
    from petcore.ui.menu import menu_open
    from petcore.ui.menu_dispatch import connect_action
    from petcore.ui.tray import TrayIcon

    monkeypatch.setattr(macos, "is_cocoa", lambda: True)
    menu = QMenu()
    calls = []
    action = menu.addAction("Settings")
    connect_action(action, lambda: calls.append(("settings", menu.isVisible())))
    tray = TrayIcon(lambda _parent: menu, Path("missing.ico"), lambda: calls.append("toggle"))
    try:
        assert tray.contextMenu() is None
        for reason in (tray.ActivationReason.Trigger, tray.ActivationReason.Context):
            tray.activated.emit(reason)
            assert menu.isVisible()
            assert menu_open()
            assert calls == []
            menu.close()
            assert not menu_open()
        tray.activated.emit(tray.ActivationReason.Trigger)
        loop = QEventLoop()
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if calls else None)
        poll.start(10)
        watchdog.start(5000)
        QTimer.singleShot(0, lambda: QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                                                    pos=menu.actionGeometry(action).center()))
        loop.exec()
        assert calls == [("settings", False)]
    finally:
        menu.close()
        tray.hide()
