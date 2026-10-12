import json, os, shlex, subprocess
import sys
import pytest
from pathlib import Path
from pet.codex_install import stage_plugin, ENABLED


def prepared(tmp_path):
    root = stage_plugin(tmp_path / "marketplace")
    (root / ENABLED).touch()
    plugin = root / "plugins/dsh-pet-codex"
    command = json.loads((plugin / "hooks/hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0][
        "commandWindows" if sys.platform == "win32" else "command"
    ]
    return root, command


def invoke(command, home, payload):
    return subprocess.run(
        command if sys.platform == "win32" else shlex.split(command),
        input=payload,
        text=True,
        capture_output=True,
        env={**os.environ, "CODEX_HOME": str(home), "XDG_CACHE_HOME": str(home / "cache"), "LOCALAPPDATA": str(home / "cache")},
        timeout=20,
    )


def test_invalid_json_returns_empty_success_and_redacted_log(tmp_path):
    root, command = prepared(tmp_path)
    result = invoke(command, tmp_path, "secret-sensitive-invalid-json")
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    log = tmp_path / "dsh-pet-bridge/hooks.log"
    assert log.exists()
    assert "JSONDecodeError" in log.read_text()
    assert "secret-sensitive" not in log.read_text()


def test_missing_bridge_still_returns_empty_success(tmp_path):
    root, command = prepared(tmp_path)
    for script in (root / "runtime").glob("*/bridge.py"):
        script.unlink()
    result = invoke(command, tmp_path, "{}")
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    assert "worker_exit" in (tmp_path / "dsh-pet-bridge/hooks.log").read_text()


def test_worker_timeout_and_noisy_exit_are_isolated(tmp_path):
    root, command = prepared(tmp_path)
    bridge = next((root / "runtime").glob("*/bridge.py"))
    bridge.write_text("import time\ntime.sleep(10)\n")
    result = invoke(command, tmp_path, "{}")
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    assert "worker_timeout" in (tmp_path / "dsh-pet-bridge/hooks.log").read_text()
    bridge.write_text('import sys\nprint("sensitive stdout")\nprint("sensitive stderr",file=sys.stderr)\nraise SystemExit(2)\n')
    result = invoke(command, tmp_path, "{}")
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    assert "sensitive" not in (tmp_path / "dsh-pet-bridge/hooks.log").read_text()


def test_state_failure_and_logging_failure_do_not_block(tmp_path):
    root, command = prepared(tmp_path)
    state_root = tmp_path / "dsh-pet-bridge"
    state_root.mkdir()
    (state_root / "state.json").write_text("invalid secret-state")
    payload = json.dumps(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t"))
    result = invoke(command, tmp_path, payload)
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    text = (state_root / "hooks.log").read_text()
    assert "write_state" in text and "secret-state" not in text
    (state_root / "hooks.log").unlink()
    (state_root / "hooks.log").mkdir()
    fallback = tmp_path / "cache/dsh-pet"
    fallback.mkdir(parents=True)
    (fallback / "hooks.log").mkdir()
    result = invoke(command, tmp_path, payload)
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")


def test_logs_rotate_and_have_fallback(tmp_path, monkeypatch):
    import runpy

    guard = runpy.run_path("integrations/dsh-pet-codex/scripts/diagnostics.py")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    root = tmp_path / "home/dsh-pet-bridge"
    root.mkdir(parents=True)
    (root / "hooks.log").write_bytes(b"x" * guard["MAX_LOG_BYTES"])
    guard["diagnostic"]("test_error", ValueError("SECRET"))
    assert (root / "hooks.log.1").stat().st_size == guard["MAX_LOG_BYTES"]
    assert (root / "hooks.log").stat().st_size < 1024
    (root / "hooks.log").unlink()
    (root / "hooks.log").mkdir()
    guard["diagnostic"]("fallback_test")
    assert "fallback_test" in (tmp_path / "cache/dsh-pet/hooks.log").read_text()


def test_headless_entry_returns_success(tmp_path):
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "pet", "--agent-hook"],
        input="bad-json",
        text=True,
        capture_output=True,
        env={**os.environ, "CODEX_HOME": str(tmp_path)},
        timeout=20,
    )
    assert (r.returncode, r.stdout.strip(), r.stderr) == (0, "{}", "")
    assert "JSONDecodeError" in (tmp_path / "dsh-pet-bridge/hooks.log").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX lock injection")
def test_lock_timeout_is_logged_but_not_blocking(tmp_path):
    import fcntl

    root, command = prepared(tmp_path)
    state_root = tmp_path / "dsh-pet-bridge"
    state_root.mkdir()
    with (state_root / "state.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = invoke(command, tmp_path, json.dumps(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t")))
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    assert "TimeoutError" in (state_root / "hooks.log").read_text()


def test_large_input_is_drained_and_logged(tmp_path):
    root, command = prepared(tmp_path)
    result = invoke(command, tmp_path, "sensitive" * 150000)
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    text = (tmp_path / "dsh-pet-bridge/hooks.log").read_text()
    assert "input_too_large" in text and "sensitive" not in text


def test_missing_logger_is_reported_by_supervisor(tmp_path):
    root, command = prepared(tmp_path)
    next((root / "runtime").glob("*/diagnostics.py")).unlink()
    result = invoke(command, tmp_path, "{}")
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    assert "worker_exit" in (tmp_path / "dsh-pet-bridge/hooks.log").read_text()
