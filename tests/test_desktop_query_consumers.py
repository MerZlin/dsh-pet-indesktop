"""Consumer policies remain unchanged while their OS seam moves out of vision."""

import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from pet import desktop_query, platform_win, window_screen
from pet.multi_window_shared import SharedFullscreenWatcher
from tests.desktop_query_fakes import WindowApi

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def api(monkeypatch):
    api = WindowApi().with_watch_queries()
    monkeypatch.setattr(ctypes, "windll", api.dlls, raising=False)
    monkeypatch.setattr(desktop_query, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(platform_win, "os", SimpleNamespace(name="nt", getpid=lambda: 999))
    return api


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("style", 0, True),
        ("style", 0x00C00000, False),
        ("exstyle", 0x80, False),
        ("class_name", "Shell_TrayWnd", False),
        ("pid", 999, False),
        ("visible", False, False),
    ],
)
def test_fullscreen_policy_uses_real_query_backend(api, field, value, expected):
    api.rect = api.monitor_rect
    setattr(api, field, value)
    assert platform_win._fg_fullscreen_probe()[0] is expected
    assert len(api.user32.GetForegroundWindow.calls) == 1


def test_fullscreen_topmost_and_busy_fallback_preserved(api):
    api.rect, api.style, api.exstyle = api.monitor_rect, 0x00C00000, 8
    assert platform_win._fg_fullscreen_probe()[0] is True
    api.rect, api.exstyle, api.busy_state = (20, 30, 100, 100), 0, 3
    assert platform_win._fg_fullscreen_probe()[0] is True


class ClockStop:
    """Finite deterministic clock at the wait boundary, not a sleep-based test."""

    def __init__(self):
        self.ticks = 0
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        self.ticks += 1
        return self.ticks > 40

    def is_set(self):
        return self.ticks > 40

    def now(self):
        return self.ticks / 20


class QueryHost(QObject):
    """Only the loop input contract; actual PetWindow is covered in the subprocess gate."""

    cursor_visibility_changed = Signal(str)
    fullscreen_changed = Signal(bool)
    auto_hide_fullscreen = True
    _fs_last = False
    _cursor_hidden_passthrough_enabled = staticmethod(lambda: True)
    _watch_required = staticmethod(lambda: True)
    _fg_fullscreen_probe = staticmethod(platform_win._fg_fullscreen_probe)


@pytest.mark.parametrize("shared", [False, True])
def test_query_cadence_and_native_call_count_unchanged(app, api, monkeypatch, shared):
    host, clock = QueryHost(), ClockStop()
    host._fs_stop = clock
    cursor = []
    if shared:
        watcher = SharedFullscreenWatcher(SimpleNamespace(instances=[SimpleNamespace(win=host)]))
        watcher._stop = clock
        watcher.cursor_visibility_changed.connect(cursor.append)
        from pet import multi_window_shared

        monkeypatch.setattr(multi_window_shared, "time", SimpleNamespace(monotonic=clock.now))
        watcher._loop()
    else:
        host.cursor_visibility_changed.connect(cursor.append)
        window_screen.fs_watch_loop(host, monotonic=clock.now)
    assert clock.waits == [0.05] * 41
    assert cursor == ["SHOWING"] * 40
    assert len(api.user32.GetCursorInfo.calls) == 40
    assert len(api.user32.GetForegroundWindow.calls) == 2
    assert len(api.closed) == 2


@pytest.mark.parametrize("choice,expected", [("process", "editor.exe"), ("title", "title:*fixture title*"), ("cancel", "")])
def test_whitelist_real_dialog_query_and_dedup(app, tmp_path, monkeypatch, choice, expected):
    from pet import modern_settings_dialog as settings
    from pet.config import Config

    monkeypatch.setattr(settings.autostart_mod, "is_enabled", lambda: False)
    dialog = settings.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    api = WindowApi()
    monkeypatch.setattr(ctypes, "windll", api.dlls, raising=False)
    monkeypatch.setattr(desktop_query, "sys", SimpleNamespace(platform="win32"))
    dialog.pro_whitelist_edit.clear()
    clicked = []

    def choose():
        for box in dialog.findChildren(QMessageBox):
            if box.isVisible():
                target = {
                    "process": next(b for b in box.buttons() if b.text() == "按软件（推荐）"),
                    "title": next(b for b in box.buttons() if b.text() == "按标题关键词"),
                    "cancel": box.button(QMessageBox.StandardButton.Cancel),
                }[choice]
                clicked.append(True)
                target.click()

    timer = QTimer(dialog)
    timer.timeout.connect(choose)
    timer.start(5)
    deadline = QTimer(dialog)
    deadline.setSingleShot(True)
    deadline.timeout.connect(lambda: [b.reject() for b in dialog.findChildren(QMessageBox)])
    deadline.start(5000)
    try:
        dialog._do_pro_add_foreground()
        dialog._do_pro_add_foreground()
        assert clicked == [True, True]
        assert dialog.pro_whitelist_edit.toPlainText() == expected
        assert len(api.user32.GetForegroundWindow.calls) == 2
        assert dialog.pro_add_btn.isEnabled()
        assert dialog.pro_add_btn.text() == "从当前前台窗口添加…"
    finally:
        timer.stop()
        deadline.stop()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
