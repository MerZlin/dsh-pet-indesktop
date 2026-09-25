# -*- coding: utf-8 -*-
"""Phase 3A real Core/Worker graceful-shutdown probe.

This test starts the normal ``python -m pet`` entrypoint in a child process,
forces Agent Link into Worker mode through a test-only ``sitecustomize`` hook,
and lets the real ``QApplication.aboutToQuit`` cleanup path run.  The hook is
used instead of adding a product-only auto-exit flag, so the public CLI remains
unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from pet.config import APP_DIR_NAME

_SITE_CUSTOMIZE = r'''
"""Test-only hooks for the Phase 3A Core shutdown probe."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# Worker children inherit PYTHONPATH.  Do not import the GUI modules in the
# worker process: its --worker route must keep the production import boundary.
# The hook simply becomes a no-op for that process; it must never terminate the
# Worker before the protocol handshake can run.

def _append(event: str, **payload) -> None:
    marker = os.environ.get("PHASE3A_SHUTDOWN_MARKER")
    if not marker:
        return
    record = {"event": event, **payload}
    with Path(marker).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


base = None if "--worker" in sys.argv else os.environ.get("PHASE3A_SHUTDOWN_BASE")
if base:
    import pet.app as app_module
    import pet.config as config_module

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell
    from pet.workers.supervisor import WorkerSupervisor

    base_path = Path(base)
    config_module._default_base = lambda: base_path
    app_module._default_base = lambda: base_path

    original_shell_init = AppShell.__init__

    def shell_init(self, *args, **kwargs):
        config = args[1] if len(args) > 1 else kwargs.get("config")
        if config is not None:
            agent_link = dict(config.data.get("agent_link", {}) or {})
            agent_link["dsh"] = True
            agent_link["worker_mode"] = "worker"
            config.data["agent_link"] = agent_link
        return original_shell_init(self, *args, **kwargs)

    AppShell.__init__ = shell_init

    original_shell_start = AppShell.start

    def shell_start(self, *args, **kwargs):
        result = original_shell_start(self, *args, **kwargs)
        app = QApplication.instance()
        if app is not None:
            # Keep enough time for the real Worker handshake before asking the
            # real AppShell.aboutToQuit path to stop it.
            QTimer.singleShot(2500, app.quit)
        return result

    AppShell.start = shell_start

    original_supervisor_started = WorkerSupervisor._on_started

    def supervisor_started(self, *args, **kwargs):
        process = self.process
        _append(
            "started",
            worker_id=self.worker_id,
            pid=int(process.processId()) if process is not None else 0,
        )
        return original_supervisor_started(self, *args, **kwargs)

    WorkerSupervisor._on_started = supervisor_started

    original_supervisor_finished = WorkerSupervisor._on_process_finished

    def supervisor_finished(self, exit_code, exit_status):
        _append(
            "finished",
            worker_id=self.worker_id,
            exit_code=int(exit_code),
            exit_status=str(exit_status),
        )
        return original_supervisor_finished(self, exit_code, exit_status)

    WorkerSupervisor._on_process_finished = supervisor_finished
'''


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


@pytest.mark.integration
@pytest.mark.platform
def test_real_core_graceful_shutdown_stops_agent_link_worker(tmp_path: Path) -> None:
    """The normal Core exit path must release its Worker without force-kill."""

    data_root = tmp_path / "data"
    app_root = data_root / APP_DIR_NAME
    bridge_root = data_root / "dsh-pet-bridge"
    hook_root = tmp_path / "hook"
    marker = tmp_path / "shutdown-events.jsonl"
    app_root.mkdir(parents=True)
    bridge_root.mkdir(parents=True)
    hook_root.mkdir(parents=True)

    # Keep the persisted input intentionally small; Config supplies the normal
    # defaults and the hook only overrides the Worker mode for this probe.
    (app_root / "config.json").write_text(
        json.dumps({"agent_link": {"dsh": True, "worker_mode": "worker"}}),
        encoding="utf-8",
    )
    (hook_root / "sitecustomize.py").write_text(textwrap.dedent(_SITE_CUSTOMIZE), encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "PHASE3A_SHUTDOWN_BASE": str(data_root),
            "PHASE3A_SHUTDOWN_MARKER": str(marker),
            "PYTHONPATH": os.pathsep.join([str(hook_root), str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")]),
        }
    )

    started = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, "-m", "pet", "--slot", "0"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=35)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"Core graceful-shutdown probe timed out\nstdout:\n{stdout}\nstderr:\n{stderr}")
    elapsed = time.perf_counter() - started

    records = []
    if marker.exists():
        records = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines() if line.strip()]

    started_records = [item for item in records if item.get("event") == "started"]
    finished_records = [item for item in records if item.get("event") == "finished"]

    assert process.returncode == 0, (
        f"Core returned {process.returncode}; elapsed={elapsed:.3f}s\nstdout:\n{stdout[-4000:]}\nstderr:\n{stderr[-4000:]}\nrecords={records}"
    )
    assert started_records, f"Core never started Agent Link Worker\nstdout:\n{stdout}\nstderr:\n{stderr}"
    assert finished_records, f"Supervisor never observed Worker finish\nrecords={records}\nstderr:\n{stderr}"
    assert all(item.get("exit_code") == 0 for item in finished_records), records

    worker_pids = [int(item.get("pid", 0)) for item in started_records]
    assert all(pid > 0 for pid in worker_pids), records
    assert not any(_pid_is_running(pid) for pid in worker_pids), records
