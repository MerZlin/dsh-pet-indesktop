"""Actual Qt/background transactions; generated roots, no real profiles."""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication

from pet import feature_state_io as io
from pet.core_uninstall import CoreUninstallCoordinator
from tests.test_core_uninstall import installed_services


def pump(app, predicate):
    deadline = time.monotonic() + 30
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert predicate()


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    old = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.setQuitOnLastWindowClosed(old)


def test_real_two_owner_removal_is_off_gui_and_requires_confirmation(tmp_path, monkeypatch, qapp):
    from pet.core_uninstall_ui import CoreUninstallDialog, CoreUninstallRuntime

    data, services = installed_services(tmp_path)
    threads = []
    original = io.atomic_write

    def write(path, raw):
        threads.append(threading.get_ident())
        return original(path, raw)

    monkeypatch.setattr(io, "atomic_write", write)
    runtime = CoreUninstallRuntime(CoreUninstallCoordinator(services), qapp)
    dialog = CoreUninstallDialog(runtime)
    assert callable(dialog.scroll), "native QWidget.scroll must remain callable"
    assert dialog.removal_evidence is None
    results = []
    runtime.result_ready.connect(results.append)
    dialog.show()
    assert runtime.submit("prepare")
    pump(qapp, lambda: bool(results))
    assert results[-1].status == "awaiting_confirmation"
    assert callable(dialog.result) and dialog.result() == 0
    assert dialog.confirm_button.isEnabled()
    assert all(service.store.read().state.enabled for service in services.values())
    dialog.confirm_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert results[-1].status == "completed" and results[-1].evidence is not None
    assert dialog.removal_confirmed
    assert dialog.removal_evidence is results[-1].evidence
    assert not dialog.confirm_button.isEnabled()
    assert threads and threading.get_ident() not in threads
    assert (data / "config.json").exists()
    dialog.close()
    runtime.close()


def test_window_close_during_write_does_not_abort_accepted_uninstall(tmp_path, monkeypatch, qapp):
    from pet.core_uninstall_ui import CoreUninstallDialog, CoreUninstallRuntime

    data, services = installed_services(tmp_path)
    coordinator = CoreUninstallCoordinator(services)
    prepared = coordinator.prepare()
    runtime = CoreUninstallRuntime(coordinator, qapp)
    dialog = CoreUninstallDialog(runtime)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    blocked, release = threading.Event(), threading.Event()
    original = io.atomic_write

    def write(path, raw):
        if path.name == "state.json" and not blocked.is_set():
            blocked.set()
            assert release.wait(30)
        return original(path, raw)

    monkeypatch.setattr(io, "atomic_write", write)
    dialog.show()
    assert runtime.submit("apply", prepared.plan)
    try:
        pump(qapp, blocked.is_set)
        dialog.close()
        qapp.processEvents()
        assert runtime.busy and runtime.closed
        assert not runtime.submit("prepare")
    finally:
        release.set()
    pump(qapp, lambda: not runtime.busy)
    assert all(service.store.read().status == "uninstalled" for service in services.values())
    assert (data / "config.json").exists()


def test_unknown_command_cannot_supply_success_or_paths(tmp_path, qapp):
    from pet.core_uninstall_ui import CoreUninstallRuntime

    data, services = installed_services(tmp_path)
    runtime = CoreUninstallRuntime(CoreUninstallCoordinator(services), qapp)
    assert not runtime.submit("startup-success", True)
    assert not runtime.submit("delete-path", str(data))
    assert not runtime.busy
    assert all(service.store.read().state.enabled for service in services.values())
    runtime.close()


@pytest.mark.parametrize("width", [720, 1100])
def test_removal_dialog_has_wrapped_accessible_controls(tmp_path, qapp, width):
    from pet.core_uninstall_ui import CoreUninstallDialog, CoreUninstallRuntime

    data, services = installed_services(tmp_path)
    runtime = CoreUninstallRuntime(CoreUninstallCoordinator(services), qapp)
    dialog = CoreUninstallDialog(runtime)
    dialog.resize(width, 500)
    dialog.show()
    assert runtime.submit("prepare")
    pump(qapp, lambda: not runtime.busy)
    assert dialog.confirm_button.isVisible()
    assert dialog.confirm_button.accessibleName()
    assert dialog.status_label.wordWrap() and dialog.detail_label.wordWrap()
    assert dialog.minimumSizeHint().width() <= width
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    dialog.close()
    runtime.close()
