"""A local companion starts without model entitlement or a live catalog."""

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def test_removed_desktop_version_resolves_current_path_without_inference(tmp_path, monkeypatch):
    from pet.codex_bridge import startup

    current = tmp_path / "codex.exe"
    current.write_text("OS executable fixture")
    monkeypatch.setattr(startup.shutil, "which", lambda name: str(current))
    assert startup.resolve_executable(tmp_path / "removed" / "codex.exe") == str(current)


def test_existing_saved_model_starts_pet_even_if_bridge_cannot_spawn(tmp_path, monkeypatch):
    from pet.codex_bridge import __main__ as launch
    from pet.config import APP_DIR_NAME
    from pet.chat.models import SecretStore

    profile = tmp_path / APP_DIR_NAME
    profile.mkdir()
    (profile / "codex-bridge.json").write_text(json.dumps({"model": "saved-model", "codexExecutable": "missing-codex"}))
    monkeypatch.setattr(SecretStore, "set", lambda *args: True)

    def forbidden_catalog(*args):
        raise AssertionError("Existing-profile startup must not require a model request or catalog")

    monkeypatch.setattr(launch, "catalog", forbidden_catalog)
    children = []
    os_spawn = launch.subprocess.Popen

    class PetProcess:
        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def spawn(command, **kwargs):
        if not isinstance(command, list) or "-m" not in command:
            return os_spawn(command, **kwargs)
        children.append(command)
        if "pet.codex_bridge.server" in command:
            raise OSError("OS bridge process unavailable")
        assert "pet" in command
        return PetProcess()

    monkeypatch.setattr(launch.subprocess, "Popen", spawn)
    assert launch.main(["--profile", str(tmp_path)]) == 0
    assert len(children) == 2


def test_real_bridge_health_is_available_without_codex_or_generation(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"port": port, "localToken": "fixture-only-token", "model": "saved-model",
                                 "codexExecutable": str(tmp_path / "missing-codex")}), encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "pet.codex_bridge.server", "--config", str(config)],
                               cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(os.environ, PYTHONUTF8="1"), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 20
    try:
        payload = None
        while process.poll() is None and time.monotonic() < deadline:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                connection.request("GET", "/health", headers={"Authorization": "Bearer fixture-only-token"})
                response = connection.getresponse()
                payload = json.loads(response.read())
                if response.status == 200:
                    break
            except OSError:
                time.sleep(0.02)  # Poll an explicit socket-ready condition.
            finally:
                connection.close()
        assert payload and payload.get("ok"), "Catalog failure prevented the local bridge from opening"
    finally:
        process.terminate()
        process.communicate(timeout=15)
