"""Qt-threaded state monitor tests using real filesystem commits and event loops."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtWidgets import QApplication

from pet.feature_install_state import FEATURE_ID, FeatureInstallStateStore, StateChange
from pet.feature_state_monitor import FeatureStateMonitor

DIGEST = "a" * 64


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump_until(app: QCoreApplication, predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
    return bool(predicate())


def _install(store: FeatureInstallStateStore) -> None:
    store.commit(
        StateChange({"1.0.0": DIGEST}, active="1.0.0", enabled=True),
        expected_revision=0,
        operation_id="install",
    )
    (store.root / "versions" / "1.0.0").mkdir(parents=True)


def test_monitor_reads_initial_state_and_revision_from_worker_thread(tmp_path: Path, qt_app):
    store = FeatureInstallStateStore(tmp_path)
    _install(store)
    monitor = FeatureStateMonitor(store, interval_ms=80)
    states = []
    revisions = []
    errors = []
    monitor.state_changed.connect(states.append)
    monitor.revision_changed.connect(revisions.append)
    monitor.error.connect(errors.append)

    assert monitor.start() is True
    try:
        assert _pump_until(qt_app, lambda: revisions == [1])
        assert states[-1].status == "enabled"
        assert states[-1].state.revision == 1
        assert errors == []
        assert monitor.running is True
    finally:
        assert monitor.stop() is True
        assert monitor.running is False
        assert not monitor.thread.isRunning()


def test_monitor_watcher_and_polling_publish_authoritative_revision(tmp_path: Path, qt_app):
    store = FeatureInstallStateStore(tmp_path)
    _install(store)
    monitor = FeatureStateMonitor(store, interval_ms=80)
    revisions = []
    statuses = []
    monitor.revision_changed.connect(revisions.append)
    monitor.state_changed.connect(lambda result: statuses.append(result.status))

    assert monitor.start() is True
    try:
        assert _pump_until(qt_app, lambda: revisions == [1])
        store.commit(StateChange({"1.0.0": DIGEST}, active="1.0.0", enabled=False), expected_revision=1, operation_id="disable")
        assert _pump_until(qt_app, lambda: revisions == [1, 2])
        assert statuses[-1] == "disabled"

        def authoritative_revision_is_two() -> bool:
            result = store.read()
            return result.state is not None and result.state.revision == 2

        assert _pump_until(qt_app, authoritative_revision_is_two)
    finally:
        monitor.close()
