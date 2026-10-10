"""Normal production Worker startup in an isolated real Qt/process boundary.

Do not dispatch the accumulated GUI suite's queued callbacks in this gate:
the validator owns its QApplication, and the Worker owns a second process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_gate(script, evidence):
    driver = (
        "import json; from pathlib import Path; "
        "from scripts.validate_screen_worker_startup import validate_worker_startup; "
        f"receipt = validate_worker_startup(Path({sys.executable!r}), Path({str(evidence)!r}), "
        f"arguments=({str(script)!r},), source_root=Path({str(ROOT)!r})); "
        "print(json.dumps(receipt))"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", driver], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)


def test_actual_source_worker_claims_lease_hello_ready_and_exits_naturally(tmp_path):
    script = tmp_path / "normal_entry.py"
    script.write_text("from pet.workers.screen_entry import run_screen_worker_entry\nraise SystemExit(run_screen_worker_entry())\n", encoding="utf-8")
    result = run_gate(script, tmp_path / "evidence")
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["lease_claimed"] is True
    assert receipt["hello"] is True and receipt["ready"] is True
    assert receipt["exit_code"] == 0 and receipt["natural_exit"] is True
    assert receipt["occupancy_after_exit"] == "free"
    assert receipt["screen_requests"] == receipt["network_requests"] == 0


def test_pre_hello_missing_module_fails_closed_with_redacted_diagnostic(tmp_path):
    script = tmp_path / "broken_entry.py"
    script.write_text(
        "import sys\nsys.stderr.write(\"GENERATED-SECRET\\nModuleNotFoundError: No module named 'pet.official_features'\\n\")\nraise SystemExit(1)\n",
        encoding="utf-8",
    )
    result = run_gate(script, tmp_path / "evidence")
    assert result.returncode != 0
    assert "worker_startup_failed" in result.stderr
    evidence = (tmp_path / "evidence/startup.json").read_text(encoding="utf-8")
    assert "GENERATED-SECRET" not in evidence
    assert "pet.official_features" in evidence
