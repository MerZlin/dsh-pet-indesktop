from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QPoint, QTimer
from PySide6.QtWidgets import QApplication, QMenu


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_visible_menu_action_waits_until_menu_closes(app):
    from petcore.ui.menu_dispatch import connect_action
    menu = QMenu()
    action = menu.addAction("打开设置")
    calls = []
    loop = QEventLoop()
    connect_action(action, lambda: (calls.append(menu.isVisible()), loop.quit()))
    menu.popup(QPoint(50, 50))
    action.trigger()
    assert not calls
    menu.hide()
    budget = QTimer(loop)
    budget.setSingleShot(True)
    budget.timeout.connect(loop.quit)
    budget.start(5000)
    try:
        loop.exec()
    finally:
        budget.stop()
    assert calls == [False]
    menu.close()


def test_native_menu_tracking_waits_for_close_even_when_qwidget_is_not_visible(app, monkeypatch):
    from petcore.ui.menu_dispatch import connect_action
    from PySide6.QtGui import QGuiApplication
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "cocoa")
    menu = QMenu()
    action = menu.addAction("设置")
    calls = []
    loop = QEventLoop()
    connect_action(action, lambda: (calls.append("opened"), loop.quit()))
    # Cocoa's NSMenu drives these signals without showing a QWidget popup.
    menu.aboutToShow.emit()
    assert not menu.isVisible()
    action.trigger()
    app.processEvents()
    assert not calls
    menu.aboutToHide.emit()
    budget = QTimer(loop)
    budget.setSingleShot(True)
    budget.timeout.connect(loop.quit)
    budget.start(5000)
    try:
        loop.exec()
    finally:
        budget.stop()
    assert calls == ["opened"]
    menu.close()


def test_trayless_recovery_restores_input_and_visibility(app):
    from petcore.ui.recovery import RecoveryWindow
    calls = []
    host = SimpleNamespace(
        set_all_visible=lambda value: calls.append(("visible", value)),
        set_all_click_through=lambda value: calls.append(("through", value)),
        open_settings=lambda: calls.append("settings"),
        quit_app=lambda: calls.append("quit"),
    )
    window = RecoveryWindow(host)
    window.show()
    window.restore_button.click()
    assert calls == [("through", False), ("visible", True)]
    window.close()
    assert window.isMinimized()
    window.hide()
    window.deleteLater()


def test_trayless_recovery_does_not_veto_explicit_application_exit():
    """QApplication.quit sends Close before aboutToQuit; recovery must allow it."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    code = textwrap.dedent("""
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QPushButton
        from petcore.ui.recovery import RecoveryWindow
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        host = SimpleNamespace(_closing=False, open_settings=lambda: None)
        def quit_app():
            host._closing = True
            app.quit()
        host.quit_app = quit_app
        window = RecoveryWindow(host)
        window.show()
        # Ordinary title-bar close still minimizes, preserving the recovery path.
        assert not window.close()
        assert window.isMinimized()
        window.showNormal()
        button = next(b for b in window.findChildren(QPushButton) if b.text() == "退出应用")
        QTimer.singleShot(0, button.click)
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: app.exit(73))
        watchdog.start(5000)
        code = app.exec()
        app.shutdown()
        raise SystemExit(code)
    """)
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr or f"quit was vetoed: {result.returncode}"


def test_long_platform_diagnostics_are_not_clipped_in_narrow_settings(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from petcore.config import Config
    from petcore.platform import desktop
    from petcore.ui.settings.page_about import AboutPage
    capabilities = desktop.capabilities
    monkeypatch.setattr(desktop, "capabilities", lambda **_: capabilities(platform="linux", qpa="xcb", environ={"DISPLAY": ":0"}))
    host = SimpleNamespace(root=tmp_path, config=Config(tmp_path), characters=lambda: [], diagnostics=[])
    # Exercise the owning page directly. A full SettingsDialog also constructs
    # audio-device and unrelated extension pages; its native teardown belongs
    # to those lifecycle suites, not to this label-height regression.
    page = AboutPage(host)
    page.resize(480, 680)
    page.show()
    try:
        app.processEvents()
        label = next(label for label in page.findChildren(QLabel) if "全局快捷键：" in label.text())
        assert label.height() >= label.heightForWidth(label.width())
    finally:
        page.close()
