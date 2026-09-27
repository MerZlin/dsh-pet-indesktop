"""Normal Core entrypoint/quit, isolated data and a real proactive Worker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pet.config import APP_DIR_NAME
from tests.test_phase3a_app_shutdown import _pid_is_running

_HOOK = r"""import json, os, sys
from pathlib import Path
if "--worker" not in sys.argv and os.environ.get("PHASE3B_QUIT_BASE"):
    import pet.app as app_module
    import pet.config as config_module
    from pet.app import AppShell
    from pet.workers.supervisor import WorkerSupervisor
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    root = Path(os.environ["PHASE3B_QUIT_BASE"])
    config_module._default_base = lambda: root
    app_module._default_base = lambda: root
    def record(event, **data):
        with Path(os.environ["PHASE3B_QUIT_MARKER"]).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, **data}) + "\n")
    previous_state = WorkerSupervisor._set_state
    def set_state(self, state):
        previous_state(self, state)
        if self.worker_id == "proactive-screen":
            record(state, pid=int(self.process.processId()) if self.process else 0)
            if state == "ready":
                QTimer.singleShot(0, QApplication.instance().quit)
    WorkerSupervisor._set_state = set_state
    previous_finished = WorkerSupervisor._on_process_finished
    def finished(self, code, status):
        if self.worker_id == "proactive-screen":
            record("finished", code=int(code))
        previous_finished(self, code, status)
    WorkerSupervisor._on_process_finished = finished
    previous_start = AppShell.start
    def start(self, *args, **kwargs):
        result = previous_start(self, *args, **kwargs)
        watcher = self.instance.win._ensure_proactive_watcher()
        watcher._start_worker({})  # Also test shutdown on non-Windows; no screenshot is requested.
        QTimer.singleShot(12000, QApplication.instance().quit)  # Failure bound, not synchronization.
        return result
    AppShell.start = start
"""


@pytest.mark.integration
@pytest.mark.platform
def test_normal_core_quit_stops_proactive_worker(tmp_path):
    root = tmp_path / "data"
    app_root = root / APP_DIR_NAME
    app_root.mkdir(parents=True)
    (app_root / "config.json").write_text(
        json.dumps(
            {
                "proactive_screen": {"enabled": True, "whitelist": ["no-such-process.exe"]},
                "agent_link": {"dsh": False, "claude": False, "cursor": False, "opencode": False},
            }
        ),
        encoding="utf-8",
    )
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(textwrap.dedent(_HOOK), encoding="utf-8")
    marker = tmp_path / "events.jsonl"
    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PHASE3B_QUIT_BASE": str(root),
        "PHASE3B_QUIT_MARKER": str(marker),
        "PYTHONPATH": os.pathsep.join([str(hook), str(repo), os.environ.get("PYTHONPATH", "")]),
    }
    process = subprocess.run([sys.executable, "-m", "pet", "--slot", "0"], cwd=repo, env=env, capture_output=True, text=True, timeout=45)
    records = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()] if marker.exists() else []
    assert process.returncode == 0, (process.returncode, records, process.stderr[-4000:])
    ready = [item for item in records if item["event"] == "ready"]
    assert ready, (records, process.stderr[-4000:])
    assert any(item["event"] == "stopping" for item in records), records
    assert any(item["event"] == "finished" and item["code"] == 0 for item in records), (records, process.stderr[-4000:])
    assert not any(_pid_is_running(item["pid"]) for item in ready)
