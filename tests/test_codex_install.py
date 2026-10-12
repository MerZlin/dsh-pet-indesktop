"""Installer staging contract without changing the user's Codex profile."""

import json
import sys
from pathlib import Path

import pytest

from pet.codex_install import ENABLED, OWNER, PLUGIN, stage_plugin
from tests.test_codex_hook_runtime import invoke


def test_stage_refuses_unowned_directory(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    (root / "personal.json").write_text("keep")
    with pytest.raises(ValueError):
        stage_plugin(root)
    assert (root / "personal.json").read_text() == "keep"
    assert not (root / OWNER).exists()


def test_loaded_command_survives_cache_removal_and_disable(tmp_path):
    import shutil

    root = stage_plugin(tmp_path / "marketplace")
    marker = root / ENABLED
    marker.touch()
    hooks = root / "plugins" / PLUGIN / "hooks/hooks.json"
    command = json.loads(hooks.read_text())["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows" if sys.platform == "win32" else "command"]
    shutil.rmtree(hooks.parents[1])
    payload = json.dumps(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t"))
    result = invoke(command, tmp_path, payload)
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    state = tmp_path / "dsh-pet-bridge/state.json"
    before = state.read_bytes()
    marker.unlink()
    result = invoke(command, tmp_path, payload)
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "{}", "")
    assert state.read_bytes() == before
