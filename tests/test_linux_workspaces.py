"""Workspace hints follow native window lifetimes without taking focus."""
from PySide6.QtWidgets import QApplication, QWidget
from pet import platform_linux
from tests.test_agent_link_threads import wait_until


def test_workspace_hint_reapplied_after_show(monkeypatch):
    app = QApplication.instance() or QApplication([])
    requests = []
    monkeypatch.setattr(platform_linux.sys, 'platform', 'linux')
    monkeypatch.setattr(platform_linux.QGuiApplication, 'platformName', lambda: 'xcb')
    monkeypatch.setattr(platform_linux, '_request_all_desktops', lambda wid: requests.append(wid))
    window = QWidget()
    try:
        platform_linux.keep_on_all_desktops(window)
        watcher = window._all_desktops_filter
        platform_linux.keep_on_all_desktops(window)
        assert window._all_desktops_filter is watcher
        window.show()
        assert wait_until(lambda: len(requests) >= 1)
        assert requests[-1] == int(window.winId())
        window.hide()
        before = len(requests)
        watcher.schedule()
        app.processEvents()
        assert len(requests) == before
        window.show()
        assert wait_until(lambda: len(requests) > before)
    finally:
        window.close()
        window.deleteLater()


def test_other_platform_has_no_workspace_filter(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(platform_linux.QGuiApplication, 'platformName', lambda: 'offscreen')
    window = QWidget()
    platform_linux.keep_on_all_desktops(window)
    assert not hasattr(window, '_all_desktops_filter')
    window.close()
    app.processEvents()
