"""Real-process smoke coverage for the reusable Phase 3B verifier."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_worker_smoke_uses_production_entry_and_exits():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_phase3b_frozen_worker.py"), "--source", "--idle-seconds", "0"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for phase in ("hello", "ready", "observe_foreground", "shutdown_sent", "PROACTIVE_WORKER_SMOKE_OK returncode=0"):
        assert phase in result.stdout
    # The verifier must not print titles or the raw observation payload.
    assert '"window"' not in result.stdout
    assert '"title"' not in result.stdout


def test_missing_frozen_binary_fails_without_launch(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_phase3b_frozen_worker.py"), str(tmp_path / "missing.exe")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr
