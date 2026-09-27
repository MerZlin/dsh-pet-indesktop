# -*- coding: utf-8 -*-
"""Real-process boundary tests for the Phase 3B proactive-screen worker."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QProcess, QTimer
from PySide6.QtWidgets import QApplication

from pet.workers.proactive_screen_adapter import ProactiveScreenWorkerAdapter
from pet.workers.supervisor import WorkerSupervisor


def _wait_until(predicate, *, timeout_ms: int = 8000) -> None:
    """Pump the Qt event loop until *predicate* becomes true."""
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)

    def check() -> None:
        if predicate():
            loop.quit()

    poll.timeout.connect(check)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert predicate(), f"condition not met within {timeout_ms}ms"


@pytest.fixture()
def qt_app():
    return QApplication.instance() or QApplication([])


def _stop_supervisor(supervisor: WorkerSupervisor) -> None:
    supervisor.stop()
    if supervisor.process is not None:
        _wait_until(lambda: supervisor.state == supervisor.STOPPED, timeout_ms=5000)


def _worker_args(worker_id: str) -> list[str]:
    return ["-m", "pet", "--worker", worker_id]


def test_default_command_supports_proactive_worker() -> None:
    program, arguments = WorkerSupervisor._resolve_command("proactive-screen", None, None)

    assert program == sys.executable
    assert arguments == _worker_args("proactive-screen")


def test_proactive_worker_handshake_observe_and_shutdown(qt_app) -> None:
    supervisor = WorkerSupervisor(
        "proactive-screen",
        program=sys.executable,
        arguments=_worker_args("proactive-screen"),
        heartbeat_interval_ms=250,
        heartbeat_timeout_ms=3000,
    )
    responses = []
    diagnostics = []
    supervisor.response_received.connect(responses.append)
    supervisor.diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))

    try:
        assert supervisor.start({"max_edge": 640, "frame_ttl": 5.0})
        _wait_until(lambda: supervisor.state == supervisor.READY)

        request_id = supervisor.send_request(
            "observe_foreground",
            {"kind": "automatic"},
            generation=7,
        )
        assert request_id
        _wait_until(lambda: any(message.request_id == request_id for message in responses))
        response = next(message for message in responses if message.request_id == request_id)
        assert response.type == "response"
        assert response.payload["operation"] == "observe_foreground"
        assert response.payload["generation"] == 7
        assert response.payload["status"] == "ok"
        assert isinstance(response.payload["result"], dict)
        assert not any(stage == "protocol" for stage, _payload in diagnostics)
    finally:
        _stop_supervisor(supervisor)

    assert supervisor.state == supervisor.STOPPED
    assert supervisor.process is None


def test_proactive_worker_rejects_unknown_operation_without_leaking_payload(qt_app) -> None:
    supervisor = WorkerSupervisor(
        "proactive-screen",
        program=sys.executable,
        arguments=_worker_args("proactive-screen"),
    )
    responses = []
    supervisor.response_received.connect(responses.append)
    secret = "unit-test-secret-not-for-wire-log"

    try:
        assert supervisor.start({"max_edge": 800})
        _wait_until(lambda: supervisor.state == supervisor.READY)
        request_id = supervisor.send_request(
            "not_an_operation",
            {"api_key": secret, "provider": {"api_key": secret}},
            generation=3,
        )
        assert request_id
        _wait_until(lambda: any(message.request_id == request_id for message in responses))
        response = next(message for message in responses if message.request_id == request_id)
        assert response.payload["status"] == "error"
        assert response.payload["error_code"] == "unknown_operation"
        assert secret not in response.payload.get("message", "")
    finally:
        _stop_supervisor(supervisor)


def test_adapter_sanitizes_config_and_routes_observation(qt_app) -> None:
    adapter = ProactiveScreenWorkerAdapter(
        program=sys.executable,
        arguments=_worker_args("proactive-screen"),
        budget_checker=lambda _kind, _generation: True,
    )
    observations = []
    adapter.observation_ready.connect(observations.append)

    try:
        assert adapter.start(
            {
                "max_edge": 768,
                "jpeg_quality": 80,
                "frame_ttl": 10,
                "platform": "windows",
                "api_key": "must-not-be-config",
                "provider": {"api_key": "must-not-be-config"},
            }
        )
        _wait_until(lambda: adapter.ready)
        assert adapter.supervisor._config == {
            "max_edge": 768,
            "jpeg_quality": 80,
            "frame_ttl": 10,
            "platform": "windows",
        }

        request_id = adapter.observe_foreground(11)
        assert request_id
        _wait_until(lambda: bool(observations))
        envelope = observations[0]
        assert envelope["request_id"] == request_id
        assert envelope["generation"] == 11
        assert envelope["status"] == "ok"
        assert "api_key" not in json.dumps(envelope, ensure_ascii=False)
    finally:
        adapter.stop()
        if adapter.supervisor.process is not None:
            _wait_until(lambda: adapter.state == WorkerSupervisor.STOPPED, timeout_ms=5000)


def test_proactive_worker_does_not_import_desktop_ui(tmp_path: Path, qt_app) -> None:
    """The proactive worker route must not initialize the desktop UI graph."""
    probe = tmp_path / "worker-imports.json"
    (tmp_path / "sitecustomize.py").write_text(
        "import atexit, json, os, sys\n"
        "from pathlib import Path\n"
        "@atexit.register\n"
        "def _write_probe():\n"
        "    target = os.environ.get('PET_WORKER_IMPORT_PROBE')\n"
        "    if target:\n"
        "        blocked = ('pet.app', 'pet.window', 'pet.chat', 'PySide6.QtWidgets')\n"
        "        modules = sorted(name for name in sys.modules if name in blocked or name.startswith('PySide6.QtWidgets.'))\n"
        "        Path(target).write_text(json.dumps(modules), encoding='utf-8')\n",
        encoding="utf-8",
    )
    process = QProcess()
    environment = process.processEnvironment()
    inherited_pythonpath = environment.value("PYTHONPATH")
    pythonpath = str(tmp_path)
    if inherited_pythonpath:
        pythonpath += os.pathsep + inherited_pythonpath
    environment.insert("PYTHONPATH", pythonpath)
    environment.insert("PET_WORKER_IMPORT_PROBE", str(probe))
    process.setProcessEnvironment(environment)
    process.setProgram(sys.executable)
    process.setArguments(_worker_args("proactive-screen"))
    process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    process.start()
    assert process.waitForStarted(5000)
    process.closeWriteChannel()

    _wait_until(lambda: process.state() == QProcess.ProcessState.NotRunning, timeout_ms=5000)
    assert process.exitCode() == 0
    assert not bytes(process.readAllStandardError())
    assert json.loads(probe.read_text(encoding="utf-8")) == []


def test_proactive_capabilities_are_required_before_config(qt_app):
    from pet.workers.protocol import build_message

    adapter = ProactiveScreenWorkerAdapter()
    supervisor = adapter.supervisor
    supervisor._state = supervisor.STARTING
    failures = []
    supervisor.failed.connect(failures.append)
    supervisor._handle_message(build_message("proactive-screen", "hello", {"capabilities": ["logging.write"]}))
    assert failures == ["worker capability mismatch"]
    supervisor.stop()


def test_stopping_worker_cannot_resurrect_or_deliver_rpc(qt_app):
    from pet.workers.protocol import build_message

    supervisor = WorkerSupervisor("proactive-screen")
    supervisor._state = supervisor.STOPPING
    responses = []
    supervisor.response_received.connect(responses.append)
    supervisor._handle_message(build_message("proactive-screen", "ready", {"generation": 0}))
    supervisor._handle_message(build_message("proactive-screen", "response", {"generation": 0}, request_id="late"))
    assert supervisor.state == supervisor.STOPPING
    assert responses == []
    supervisor.stop()
