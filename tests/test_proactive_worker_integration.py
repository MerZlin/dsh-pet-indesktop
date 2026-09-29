"""Real QProcess + local HTTP; only OS screenshot/foreground boundaries are fake."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.screen_understanding.models import VisionRequestConfig
from pet.workers.proactive_screen_adapter import ProactiveScreenWorkerAdapter
from tests.test_proactive_worker_lifecycle import _wait_until


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def endpoint():
    requests = []
    entered = threading.Event()
    release = threading.Event()
    release.set()
    state = {"retry": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.headers.get("Authorization"), body))
            entered.set()
            assert release.wait(15), "test must release the HTTP response"
            code = 429 if state["retry"] and len(requests) == 1 else 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {"choices": [{"message": {"content": "safe reply vision-test-secret"}}]}
            try:
                self.wfile.write(json.dumps(data).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, entered, release, state
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def _worker_command(tmp_path):
    probe = tmp_path / "imports.json"
    # Use the production entry and transport, not a fake Worker. Accelerate
    # heartbeat only; the screenshot and foreground are deterministic OS seams.
    script = """
import atexit, io, json, sys
from pathlib import Path
from importlib.abc import MetaPathFinder
class NoChat(MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "pet.chat" or fullname.startswith("pet.chat."):
            raise AssertionError("chat dependency in screen worker")
sys.meta_path.insert(0, NoChat())
from PIL import Image
from pet import vision
from pet.workers import proactive_screen_worker as worker
worker.HEARTBEAT_INTERVAL = 0.1
vision.foreground_window_info = lambda: {"hwnd":42,"pid":7,"process":"code.exe","title":"fixture","rect":[0,0,100,80]}
vision.capture_window_rect = lambda rect: Image.new("RGB", (100,80), "navy")
def screen():
    output = io.BytesIO()
    Image.new("RGB",(100,80),"navy").save(output,"JPEG")
    return output.getvalue()
vision.capture_screen_bytes = screen
@atexit.register
def finish():
    blocked = [n for n in sys.modules if n.startswith(("PySide6", "keyring", "pet.chat")) or n in ("pet.app", "pet.window", "pet.chat.service")]
    Path(sys.argv[1]).write_text(json.dumps(blocked), encoding="utf-8")
from pet.workers.worker_entry import main
raise SystemExit(main("proactive-screen"))
"""
    return ["-c", script, str(probe)], probe


def _adapter(tmp_path, endpoint, budget):
    arguments, probe = _worker_command(tmp_path)
    adapter = ProactiveScreenWorkerAdapter(program=sys.executable, arguments=arguments, budget_checker=budget)
    assert adapter.start({"max_edge": 768})
    _wait_until(lambda: adapter.ready)
    provider = VisionRequestConfig(endpoint, "vision-test", "vision-test-secret")
    return adapter, provider, probe


def _capture(adapter):
    frames = []
    adapter.capture_ready.connect(frames.append)
    adapter.capture_foreground({"hwnd": 42, "pid": 7}, 4)
    _wait_until(lambda: bool(frames))
    return frames[0]["result"]["frame_id"]


def _stop(adapter):
    adapter.stop()
    _wait_until(lambda: adapter.state == "stopped", timeout_ms=6000)
    assert adapter.supervisor.process is None


def test_real_worker_automatic_retry_budget_selected_secret_and_import_boundary(tmp_path, endpoint, qt_app):
    url, requests, _, _, state = endpoint
    state["retry"] = True
    budgets = []
    adapter, provider, probe = _adapter(tmp_path, url, lambda *args: budgets.append(args) or True)
    replies, failures = [], []
    adapter.analysis_ready.connect(replies.append)
    adapter.request_failed.connect(lambda *args: failures.append(args))
    try:
        frame = _capture(adapter)
        adapter.analyze_frame(frame, provider, "fixture prompt", generation=4, pet_name="pet")
        _wait_until(lambda: bool(replies or failures), timeout_ms=12000)
        assert not failures
        assert budgets == [("automatic", 4), ("automatic", 4)]
        assert len(requests) == 2
        assert all(auth == "Bearer vision-test-secret" for auth, _ in requests)
        assert requests[0][1]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert "vision-test-secret" not in json.dumps(replies)
        assert "unrelated-chat-secret" not in json.dumps(requests)
    finally:
        _stop(adapter)
    assert json.loads(probe.read_text()) == []
    assert list(tmp_path.iterdir()) == [probe]  # No image/credentials on disk.


def test_real_worker_denied_budget_never_contacts_model(tmp_path, endpoint, qt_app):
    url, requests, *_ = endpoint
    adapter, provider, _ = _adapter(tmp_path, url, lambda *_: False)
    failures = []
    adapter.request_failed.connect(lambda *args: failures.append(args))
    try:
        adapter.analyze_frame(_capture(adapter), provider, "prompt", generation=4)
        _wait_until(lambda: bool(failures))
        assert failures[0][1]["error_code"] == "worker_operation_failed"
        assert requests == []
    finally:
        _stop(adapter)


def test_real_worker_network_does_not_block_heartbeat_observation_or_cancel(tmp_path, endpoint, qt_app):
    url, requests, entered, release, _ = endpoint
    release.clear()
    budgets = []
    adapter, provider, probe = _adapter(tmp_path, url, lambda *args: budgets.append(args) or True)
    replies, observations = [], []
    adapter.manual_ready.connect(replies.append)
    adapter.observation_ready.connect(observations.append)
    try:
        request = adapter.manual_look(provider, "prompt", generation=4)
        assert request
        _wait_until(entered.is_set)
        heartbeat = adapter.supervisor._last_heartbeat
        adapter.observe_foreground(4)
        _wait_until(lambda: bool(observations) and adapter.supervisor._last_heartbeat > heartbeat)
        assert adapter.cancel(request, generation=4)
        release.set()
        # A second round-trip acknowledges control-loop progress, no sleep.
        adapter.observe_foreground(4)
        _wait_until(lambda: len(observations) == 2)
        assert replies == []
        assert budgets == []  # Manual requests do not spend automatic quota.
        assert len(requests) == 1
    finally:
        release.set()
        _stop(adapter)
    assert json.loads(probe.read_text()) == []


def test_shared_watcher_one_real_worker_routes_manual_result_only_to_owner(tmp_path, endpoint, qt_app, monkeypatch):
    from types import SimpleNamespace

    from pet.config import Config
    from pet.multi_window_shared import MultiWindowProxy, SharedProactiveWatcher
    from pet.workers.supervisor import WorkerSupervisor

    arguments, probe = _worker_command(tmp_path)
    original_command = WorkerSupervisor._resolve_command
    monkeypatch.setattr(
        WorkerSupervisor,
        "_resolve_command",
        staticmethod(
            lambda worker_id, program, args: (sys.executable, arguments) if worker_id == "proactive-screen" else original_command(worker_id, program, args)
        ),
    )
    config = Config(base=tmp_path / "config")
    config.set("proactive_screen", {"enabled": False, "whitelist": ["code.exe"], "require_idle": False})
    from tests.screen_fakes import configure_vision

    configure_vision(config, monkeypatch)
    windows = [SimpleNamespace(isVisible=lambda: True, _physics_mode=None) for _ in range(2)]
    proxy = MultiWindowProxy(SimpleNamespace(config=config, instances=[SimpleNamespace(win=w) for w in windows]))
    watcher = SharedProactiveWatcher(proxy, config)
    for win in windows:
        win.proactive_watcher = watcher
    adapter = watcher._worker_adapter
    replies = [[], []]
    url, requests, entered, release, _ = endpoint
    provider = VisionRequestConfig(base_url=url, model="vision-test", api_key="vision-test-secret")
    release.clear()
    try:
        assert windows[1].proactive_watcher.request_manual_look(provider, "prompt", "pet", lambda *r: replies[1].append(r))
        _wait_until(entered.is_set)
        pid = adapter.supervisor.process.processId()
        assert pid and windows[0].proactive_watcher._worker_adapter.supervisor.process.processId() == pid
        # Automatic routing crosses the real process boundary, not Core's OS API.
        from pet import vision

        monkeypatch.setattr(vision, "foreground_window_info", lambda: pytest.fail("foreground queried in Core"))
        config.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"], "require_idle": False})
        observations = []
        adapter.observation_ready.connect(observations.append)
        watcher._on_tick()
        _wait_until(lambda: bool(observations))
        assert watcher._current_hwnd == 42  # Passed shared G1 guard, no screenshot before dwell.
        watcher.pause()  # Hiding/closing one window must not stop the shared worker.
        assert adapter.ready
        release.set()
        _wait_until(lambda: bool(replies[1]))
        assert replies[0] == [] and len(replies[1]) == 1 and replies[1][0][2] is False
        assert len(requests) == 1
        assert adapter.supervisor.process.processId() == pid
    finally:
        release.set()
        watcher.stop_all()
        _wait_until(lambda: adapter.state == "stopped")
    assert json.loads(probe.read_text()) == []
