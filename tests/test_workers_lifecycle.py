# -*- coding: utf-8 -*-
"""Real-process tests for the Phase 3 worker host and Agent Link adapter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QProcess, QTimer
from PySide6.QtWidgets import QApplication

from pet.workers.agent_link_adapter import WorkerAgentEventSource
from pet.workers.protocol import PROTOCOL, build_message
from pet.workers.supervisor import WorkerSupervisor


def _wait_until(predicate, *, timeout_ms: int = 8000) -> None:
    """Pump the Qt loop until *predicate* becomes true or the budget expires."""
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
    # Worker lifecycle tests run in the same pytest process as GUI/Agent Link
    # tests, so use QApplication consistently instead of creating a
    # QCoreApplication that cannot safely coexist with PetWindow.
    return QApplication.instance() or QApplication([])


def _stop_supervisor(supervisor: WorkerSupervisor) -> None:
    supervisor.stop()
    if supervisor.process is not None:
        _wait_until(lambda: supervisor.state == supervisor.STOPPED, timeout_ms=5000)


def test_default_command_uses_worker_entrypoint() -> None:
    program, arguments = WorkerSupervisor._resolve_command("agent-link-events", None, None)

    assert program == sys.executable
    assert arguments == ["-m", "pet", "--worker", "agent-link-events"]


def test_agent_link_worker_handshake_event_and_graceful_shutdown(tmp_path: Path, qt_app) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.touch()
    supervisor = WorkerSupervisor(
        "agent-link-events",
        program=sys.executable,
        arguments=["-m", "pet", "--worker", "agent-link-events"],
        heartbeat_interval_ms=250,
        heartbeat_timeout_ms=3000,
    )
    messages = []
    diagnostics = []
    supervisor.message_received.connect(messages.append)
    supervisor.diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))

    try:
        assert supervisor.start(
            {
                "sources": [
                    {
                        "agent_key": "dsh",
                        "path": str(events_file),
                        "kind": "file",
                        "agent_name": "dsh",
                        "scan_interval": 0.05,
                    }
                ],
                "poll_interval": 0.05,
            }
        )
        _wait_until(lambda: supervisor.state == supervisor.READY)
        assert supervisor.generation == 1

        with events_file.open("a", encoding="utf-8") as stream:
            stream.write('{"ts":1,"agent":"dsh","event":"tool/call","tool":"bash"}\n')

        _wait_until(lambda: any(message.type == "event" for message in messages))
        event = next(message for message in messages if message.type == "event")
        assert event.payload["generation"] == supervisor.generation
        assert event.payload["agent"] == "dsh"
        assert event.payload["record"]["tool"] == "bash"
        assert not any(stage == "protocol" for stage, _payload in diagnostics)
    finally:
        _stop_supervisor(supervisor)

    assert supervisor.state == supervisor.STOPPED
    assert supervisor.process is None


def test_worker_preserves_event_order_across_bounded_polls(tmp_path: Path, qt_app) -> None:
    """A real Worker process must drain a multi-batch append without duplicates."""
    events_file = tmp_path / "events.jsonl"
    events_file.touch()
    supervisor = WorkerSupervisor(
        "agent-link-events",
        program=sys.executable,
        arguments=["-m", "pet", "--worker", "agent-link-events"],
        heartbeat_interval_ms=250,
        heartbeat_timeout_ms=3000,
    )
    messages = []
    supervisor.message_received.connect(messages.append)
    total = 512

    try:
        assert supervisor.start(
            {
                "sources": [{"agent_key": "dsh", "path": str(events_file), "kind": "file"}],
                "poll_interval": 0.05,
            }
        )
        _wait_until(lambda: supervisor.state == supervisor.READY)

        with events_file.open("a", encoding="utf-8") as stream:
            for sequence in range(total):
                stream.write(
                    json.dumps(
                        {
                            "ts": sequence,
                            "agent": "dsh",
                            "event": "tool/call",
                            "tool": f"tool-{sequence}",
                            "sequence": sequence,
                        }
                    )
                    + "\n"
                )

        _wait_until(
            lambda: sum(message.type == "event" for message in messages) >= total,
            timeout_ms=10000,
        )
        events = [message.payload["record"] for message in messages if message.type == "event"]
        sequences = [record["sequence"] for record in events]
        assert sequences == list(range(total))
        assert len(set(sequences)) == total
    finally:
        _stop_supervisor(supervisor)


def test_worker_entry_does_not_import_desktop_ui(tmp_path: Path, qt_app) -> None:
    """The worker route must stay independent from the desktop UI import graph."""
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
    process.setArguments(["-m", "pet", "--worker", "agent-link-events"])
    process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    process.start()
    assert process.waitForStarted(5000)
    process.closeWriteChannel()

    _wait_until(lambda: process.state() == QProcess.ProcessState.NotRunning, timeout_ms=5000)
    assert process.exitCode() == 0
    assert not bytes(process.readAllStandardError())
    assert json.loads(probe.read_text(encoding="utf-8")) == []


def test_agent_link_worker_exits_after_stdin_eof(qt_app) -> None:
    """The standalone worker must terminate when its parent closes stdin."""
    process = QProcess()
    process.setProgram(sys.executable)
    process.setArguments(["-m", "pet", "--worker", "agent-link-events"])
    process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    process.start()
    assert process.waitForStarted(5000)
    process.closeWriteChannel()

    _wait_until(lambda: process.state() == QProcess.ProcessState.NotRunning, timeout_ms=5000)
    assert process.exitCode() == 0
    assert not bytes(process.readAllStandardError())


@pytest.mark.skipif(os.name != "nt", reason="parent pipe cleanup probe uses Windows tasklist")
def test_worker_exits_when_parent_process_dies(tmp_path: Path) -> None:
    """A worker must not survive after its owning parent loses its stdin pipe."""
    marker = tmp_path / "worker-pid.txt"
    project_root = Path(__file__).resolve().parents[1]
    helper = tmp_path / "parent_probe.py"
    helper.write_text(
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "marker = Path(sys.argv[1])\n"
        "root = sys.argv[2]\n"
        "worker = subprocess.Popen([sys.executable, '-m', 'pet', '--worker', 'agent-link-events'],\n"
        "    cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "hello = worker.stdout.readline()\n"
        "if not hello.startswith(b'{'):\n"
        "    os._exit(3)\n"
        "marker.write_text(str(worker.pid), encoding='ascii')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    parent = subprocess.run(
        [sys.executable, str(helper), str(marker), str(project_root)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert parent.returncode == 0, parent.stderr
    worker_pid = int(marker.read_text(encoding="ascii"))

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {worker_pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        if not any(str(worker_pid) in line for line in probe.stdout.splitlines()):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"worker process {worker_pid} survived parent exit")


def test_worker_crash_restarts_then_enters_fault(qt_app) -> None:
    supervisor = WorkerSupervisor(
        "fake-crash",
        program=sys.executable,
        arguments=["-c", "import sys; sys.exit(7)"],
        handshake_timeout_ms=1000,
        max_restarts=1,
        restart_backoff_s=(0.0,),
    )
    diagnostics = []
    supervisor.diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))

    assert supervisor.start()
    _wait_until(lambda: supervisor.state == supervisor.FAULT, timeout_ms=5000)

    assert any(stage == "restart_scheduled" for stage, _payload in diagnostics)
    assert any(stage == "fault" for stage, _payload in diagnostics)
    supervisor.stop()


def test_stop_releases_already_stopped_process_reference(qt_app) -> None:
    supervisor = WorkerSupervisor(
        "fake-exit",
        program=sys.executable,
        arguments=["-c", "import sys; sys.exit(0)"],
        max_restarts=0,
    )

    assert supervisor.start()
    _wait_until(lambda: supervisor.state == supervisor.FAULT, timeout_ms=5000)
    assert supervisor.process is None

    # Repeated teardown is intentionally idempotent and must not resurrect or
    # retain a stopped QProcess object.
    supervisor.stop()
    supervisor.stop()
    assert supervisor.state == supervisor.STOPPED
    assert supervisor.process is None


def test_worker_without_heartbeat_is_faulted(qt_app) -> None:
    hello = json.dumps(
        {
            "protocol": PROTOCOL,
            "worker_id": "fake-heartbeat",
            "type": "hello",
            "timestamp": "2026-09-25T00:00:00Z",
            "payload": {},
        },
        separators=(",", ":"),
    )
    ready = json.dumps(
        {
            "protocol": PROTOCOL,
            "worker_id": "fake-heartbeat",
            "type": "ready",
            "timestamp": "2026-09-25T00:00:00Z",
            "payload": {"generation": 1},
        },
        separators=(",", ":"),
    )
    script = f"import sys,time; hello={hello!r}; print(hello, flush=True); sys.stdin.readline(); ready={ready!r}; print(ready, flush=True); time.sleep(30)"
    supervisor = WorkerSupervisor(
        "fake-heartbeat",
        program=sys.executable,
        arguments=["-c", script],
        heartbeat_interval_ms=250,
        heartbeat_timeout_ms=300,
        max_restarts=0,
    )
    diagnostics = []
    supervisor.diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))

    assert supervisor.start()
    _wait_until(lambda: supervisor.state == supervisor.FAULT, timeout_ms=5000)

    assert any(stage == "failure" and payload.get("reason") == "heartbeat timeout" for stage, payload in diagnostics)
    assert any(stage == "fault" for stage, _payload in diagnostics)
    supervisor.stop()


def test_adapter_drops_stale_generation_events(qt_app) -> None:
    source = WorkerAgentEventSource()
    diagnostics = []
    source.worker_diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))
    source._running = True
    source._emit_gen = 2

    source._on_message(
        build_message(
            "agent-link-events",
            "event",
            {"generation": 1, "agent": "dsh", "record": {"event": "tool/call"}},
        )
    )

    assert diagnostics == [("stale_event", {"generation": 1, "current": 2})]
    source.stop()
