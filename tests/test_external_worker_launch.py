"""External launch ownership, isolation and real QProcess boundaries."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from pet.workers.supervisor import WorkerSupervisor


@pytest.fixture()
def qt_app():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=10000):
    if predicate():
        return
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    timer.start()
    deadline.start(timeout_ms)
    loop.exec()
    timer.stop()
    deadline.stop()
    assert predicate(), "external process did not reach the expected state"


@pytest.fixture()
def child_script(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        """import json, os, sys
from datetime import datetime, timezone

def send(kind, payload):
    data = json.dumps(dict(protocol="pet-worker/v1", worker_id="test-external",
        type=kind, timestamp=datetime.now(timezone.utc).isoformat(), payload=payload))
    sys.stdout.buffer.write(data.encode("utf-8") + bytes([10]))
    sys.stdout.buffer.flush()
send("hello", {})
for line in sys.stdin:
    msg = json.loads(line)
    if msg["type"] == "config_push":
        generation = msg["payload"]["generation"]
        send("ready", {"generation": generation})
        send("event", {"generation": generation, "cwd": os.getcwd(),
            "pythonpath": os.environ.get("PYTHONPATH"), "probe": os.environ.get("PROBE_VALUE")})
    elif msg["type"] == "shutdown":
        break
""",
        encoding="utf-8",
    )
    return script


def test_external_environment_drops_runtime_pollution_but_preserves_network(tmp_path):
    from pet.workers.launch import isolated_worker_environment

    core = tmp_path / "core"
    safe = tmp_path / "system"
    source = {
        "PYTHONHOME": "bad",
        "PythonPath": "bad",
        "PYTHONUSERBASE": "bad",
        "_PYI_ARCHIVE_FILE": "bad",
        "_MEIPASS2": "bad",
        "QT_PLUGIN_PATH": "bad",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "bad",
        "QML2_IMPORT_PATH": "bad",
        "PATH": os.pathsep.join((str(core / "_internal"), "", ".", str(safe))),
        "HTTPS_PROXY": "https://proxy.invalid",
        "SSL_CERT_FILE": "existing-cert",
        "SystemRoot": "system-root",
    }
    original = dict(source)
    clean = isolated_worker_environment(source, core_roots=(core,))
    assert source == original
    assert clean["PATH"] == str(safe)
    assert clean["HTTPS_PROXY"] == source["HTTPS_PROXY"]
    assert clean["SSL_CERT_FILE"] == source["SSL_CERT_FILE"]
    assert clean["SystemRoot"] == source["SystemRoot"]
    assert not any(key.upper().startswith(("PYTHON", "_PYI", "_MEIPASS")) for key in clean)
    assert "QT_PLUGIN_PATH" not in clean
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in clean
    assert "QML2_IMPORT_PATH" not in clean


def test_real_external_child_uses_launch_cwd_environment_and_holds_lease(child_script, tmp_path, qt_app):
    from pet.workers.launch import WorkerLaunch, isolated_worker_environment

    released = []
    messages = []
    work = tmp_path / "runtime"
    work.mkdir()
    environment = isolated_worker_environment({**os.environ, "PYTHONPATH": "not-core", "PROBE_VALUE": "child-only"})
    supervisor = WorkerSupervisor(
        "test-external", launch_factory=lambda: WorkerLaunch(sys.executable, ("-I", str(child_script)), str(work), environment, lambda: released.append(True))
    )
    supervisor.message_received.connect(messages.append)
    try:
        assert supervisor.start()
        wait_until(lambda: any(item.type == "event" for item in messages))
        event = next(item.payload for item in messages if item.type == "event")
        assert Path(event["cwd"]) == work
        assert event["pythonpath"] is None
        assert event["probe"] == "child-only"
        assert released == []
    finally:
        supervisor.stop()
        wait_until(lambda: supervisor.state == supervisor.STOPPED)
    assert supervisor.process is None
    assert released == [True]
    supervisor.stop()
    assert released == [True]


def test_failed_to_start_releases_each_lease_and_limits_restarts(tmp_path, qt_app):
    from pet.workers.launch import WorkerLaunch

    releases = []
    starts = []

    def acquire():
        token = len(starts)
        starts.append(token)
        return WorkerLaunch(str(tmp_path / "missing-worker.exe"), (), str(tmp_path), {}, lambda: releases.append(token))

    supervisor = WorkerSupervisor("test-external", launch_factory=acquire, max_restarts=1, restart_backoff_s=(0,))
    try:
        assert supervisor.start()
        wait_until(lambda: supervisor.state == supervisor.FAULT)
        assert starts == [0, 1]
        assert releases == [0, 1]
        assert supervisor.process is None
    finally:
        supervisor.stop()


def test_every_crash_reacquires_verified_launch(child_script, tmp_path, qt_app):
    from pet.workers.launch import WorkerLaunch

    active = set()
    count = []

    def acquire():
        assert not active, "old process still owns its version"
        token = len(count)
        count.append(token)
        active.add(token)
        return WorkerLaunch(sys.executable, ("-I", str(child_script)), str(tmp_path), dict(os.environ), lambda: active.remove(token))

    supervisor = WorkerSupervisor("test-external", launch_factory=acquire, max_restarts=1, restart_backoff_s=(0,))
    try:
        supervisor.start()
        wait_until(lambda: supervisor.state == supervisor.READY)
        supervisor.process.kill()
        wait_until(lambda: supervisor.generation == 2 and supervisor.state == supervisor.READY)
        assert count == [0, 1]
        assert active == {1}
    finally:
        supervisor.stop()
        wait_until(lambda: supervisor.state == supervisor.STOPPED)
    assert active == set()


def test_launch_validation_failure_never_starts_default_worker_or_leaks_error(qt_app):
    calls = []
    diagnostics = []

    def denied():
        calls.append(True)
        raise ValueError("private filesystem or test credential context")

    supervisor = WorkerSupervisor("test-external", launch_factory=denied)
    supervisor.diagnostic.connect(lambda *args: diagnostics.append(args))
    assert supervisor.start() is False
    assert supervisor.state == supervisor.FAULT
    assert supervisor.process is None
    assert calls == [True]
    assert "private filesystem" not in repr(diagnostics)
    assert not supervisor._restart_timer or not supervisor._restart_timer.isActive()


def test_stopped_process_callbacks_do_not_retain_supervisor(qt_app):
    """A pending child deletion must not become its parent's last Python owner."""
    import weakref

    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    supervisor = WorkerSupervisor("test-external", program=sys.executable, arguments=["-c", "pass"], max_restarts=0)
    supervisor.start()
    process = supervisor.process
    # Test-only bounded process wait leaves DeferredDelete pending, so this
    # assertion cannot accidentally pass because a nested loop deleted it.
    assert process.waitForFinished(10000)
    assert supervisor.process is None
    assert shiboken6.isValid(process)
    supervisor.stop()
    owner = weakref.ref(supervisor)
    del supervisor
    try:
        assert owner() is None, "stopped child's signal closure retains its QObject parent"
    finally:
        # On a regression keep the owner alive while deleting only our child;
        # never flush unrelated tests' pending DeferredDelete events.
        retained = owner()
        if retained is not None:
            if shiboken6.isValid(process):
                QCoreApplication.sendPostedEvents(process, QEvent.Type.DeferredDelete)
            retained.deleteLater()
            QCoreApplication.sendPostedEvents(retained, QEvent.Type.DeferredDelete)
