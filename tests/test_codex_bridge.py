"""Loopback authorization and read-only quota contracts of the optional bridge."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture
def local_bridge(monkeypatch):
    from pet.codex_bridge import server as module

    def forbidden_generation(*args, **kwargs):
        raise AssertionError("Read-only or rejected requests must never generate a model turn")

    monkeypatch.setattr(module, "generate", forbidden_generation)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    port = server.server_address[1]
    server.config = {"port": port, "model": "catalog-model", "localToken": "fixture-only-token"}
    server.models = [{"model": "catalog-model"}]
    server.model_lock = threading.Lock()

    class Quota:
        def read(self):
            return {"ok": True, "windows": []}

    server.usage_reader = Quota()
    ready = threading.Event()

    def serve():
        ready.set()
        server.serve_forever(poll_interval=0.05)

    worker = threading.Thread(target=serve)
    worker.start()
    assert ready.wait(15)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=15)
        assert not worker.is_alive()


def request(server, path, headers, method="GET", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=15)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "change", [{"Authorization": ""}, {"Authorization": "Bearer wrong"}, {"Host": "attacker.example"}, {"Origin": "https://attacker.example"}]
)
def test_foreign_or_unauthenticated_clients_are_rejected(local_bridge, change):
    headers = {"Authorization": "Bearer fixture-only-token", "Host": f"127.0.0.1:{local_bridge.server_address[1]}", **change}
    assert request(local_bridge, "/health", headers)[0] == 401
    assert request(local_bridge, "/v1/chat/completions", headers, "POST", json.dumps({"messages": [{"role": "user", "content": "hello"}]}))[0] == 401


def test_authenticated_read_endpoints_do_not_generate(local_bridge):
    headers = {"Authorization": "Bearer fixture-only-token"}
    assert request(local_bridge, "/health", headers) == (200, {"ok": True, "auth": "codex-chatgpt", "model": "catalog-model"})
    assert request(local_bridge, "/v1/usage", headers) == (200, {"ok": True, "windows": []})
    assert request(local_bridge, "/v1/models", headers)[1]["data"][0]["id"] == "catalog-model"


def test_no_chat_entry_rejects_companion_before_constructing_app():
    from pathlib import Path
    import subprocess
    import sys

    entry = Path(__file__).resolve().parents[1] / "packaging/pet_entry_no_chat.py"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(entry), "--codex-companion"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0 and "full Chat build" in result.stderr


def test_quota_cache_expires_at_reset_and_only_reads_rate_limits(monkeypatch):
    from pet.codex_bridge import usage

    clock = [100.0]
    calls = []

    class Rpc:
        def __init__(self, executable, **kwargs):
            pass

        def call(self, method, params, **kwargs):
            calls.append(method)
            return {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": 110}}}

        def close(self):
            pass

    monkeypatch.setattr(usage, "Rpc", Rpc)
    monkeypatch.setattr(usage.time, "time", lambda: clock[0])
    monkeypatch.setattr(usage.time, "monotonic", lambda: clock[0])
    reader = usage.UsageReader({"codexExecutable": "network-boundary-fixture"})
    assert reader.read()["windows"][0]["remainingPercent"] == 80
    clock[0] = 105
    assert reader.read()["updatedAt"] == 100
    clock[0] = 111
    assert reader.read()["updatedAt"] == 111
    clock[0] = 112
    reader.read()
    assert calls == ["account/rateLimits/read", "account/rateLimits/read"]
