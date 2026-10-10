"""Pure screen package boundary: real JSONL processes, fake OS/network edges only."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_ENTRY = "features.screen_understanding.worker"
WORKER_ID = "proactive-screen"

# Block imports at resolution time, not just after startup: runtime code must
# also stay isolated while capturing, hashing, parsing config and requesting HTTP.
IMPORT_GUARD = """
import sys
from importlib.abc import MetaPathFinder
forbidden = (
    "PySide6", "PyQt5", "PyQt6", "keyring", "host",
    "pet.app", "pet.window", "pet.chat", "pet.proactive", "pet.config",
    "pet.credentials", "pet.plugins", "features.screen_understanding.host",
    "pet.screen_understanding.configuration", "pet.screen_understanding.service",
    "pet.screen_understanding.settings", "pet.screen_understanding.runtime",
    "pet.workers.proactive_screen_adapter", "pet.workers.supervisor",
    "pet.workers.agent_link_worker",
)
attempted = []
def is_forbidden(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
class NoHost(MetaPathFinder):
    def find_spec(self, fullname, *args):
        if is_forbidden(fullname):
            attempted.append(fullname)
            raise AssertionError("worker crossed import boundary: " + fullname)
sys.meta_path.insert(0, NoHost())
def assert_isolated():
    assert not attempted, attempted
    loaded = sorted(name for name in sys.modules if is_forbidden(name))
    assert not loaded, loaded
"""


def _run_probe(script: str, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result


@pytest.mark.parametrize("legacy_first", [True, False])
@pytest.mark.parametrize(
    ("legacy", "canonical", "public_names"),
    [
        ("pet.vision", "features.screen_understanding.worker.vision", ["VisionError", "ask_about_screen", "capture_screen_bytes"]),
        (
            "pet.workers.proactive_screen_worker",
            "features.screen_understanding.worker.runtime",
            ["ProactiveScreenWorker", "run_proactive_screen_worker", "_Task", "_Frame", "_WorkerOperationError"],
        ),
        (
            "pet.screen_understanding.models",
            "features.screen_understanding.common.models",
            ["VisionProfile", "VisionSettings", "VisionRequestConfig", "validate_endpoint"],
        ),
    ],
)
def test_legacy_modules_alias_single_implementation(legacy, canonical, public_names, legacy_first):
    script = (
        IMPORT_GUARD
        + """
import importlib, json
legacy, canonical, names, legacy_first = json.loads(sys.argv[1])
order = (legacy, canonical) if legacy_first else (canonical, legacy)
first, second = [importlib.import_module(name) for name in order]
assert first is second
assert first.__dict__ is second.__dict__
assert first.__name__ == canonical
assert sys.modules[legacy] is sys.modules[canonical]
for name in names:
    assert getattr(first, name).__module__ == canonical, name
# Import-only callers must not pull in media libraries either.
assert not any(name == "PIL" or name.startswith("PIL.") for name in sys.modules)
assert_isolated()
"""
    )
    _run_probe(script, json.dumps([legacy, canonical, public_names, legacy_first]))


def test_common_models_preserve_public_contract():
    models = importlib.import_module("features.screen_understanding.common.models")
    legacy = importlib.import_module("pet.screen_understanding.models")
    profile = models.VisionProfile("screen", "https://vision.invalid", "vision-model", credential_ref="opaque-ref")
    settings = models.VisionSettings({"screen": profile}, {"automatic": "screen", "manual": "screen"}, "confirmed")
    assert legacy.VisionSettings.from_dict(settings.to_dict()) == settings
    assert models.PLUGIN_ID == "official.screen-understanding"
    assert models.MODES == frozenset({"automatic", "manual"})
    assert profile.endpoint == "https://vision.invalid/v1/chat/completions"
    request = models.VisionRequestConfig.from_profile(profile, "fixture-secret")
    assert "fixture-secret" not in repr(request)
    assert "api_key" not in request.to_dict()
    assert "credential_ref" not in request.to_dict(include_secret=True)
    assert legacy.VisionRequestConfig.from_dict(request.to_dict(include_secret=True)) == request
    with pytest.raises(ValueError, match="invalid_endpoint"):
        models.VisionProfile("screen", "https://user:password@vision.invalid", "vision-model")


@pytest.mark.parametrize(("row", "expected"), [(list(range(9)), 0), (list(range(8, -1, -1)), (1 << 64) - 1), ([7] * 9, 0)])
@pytest.mark.parametrize("mode", ["L", "RGB", "RGBA"])
def test_hashing_keeps_64_bit_row_order_and_channel_conversion(row, expected, mode):
    from features.screen_understanding.common.hashing import hamming_distance, image_dhash

    image = Image.frombytes("L", (9, 8), bytes(row * 8)).convert(mode)
    assert image_dhash(image) == expected
    assert hamming_distance(expected, expected) == 0
    assert hamming_distance(0, (1 << 64) - 1) == 64
    assert hamming_distance(0b101, 0b001) == 1


def _fixture_image():
    from random import Random

    from PIL import Image

    return Image.frombytes("L", (1000, 800), Random(42).randbytes(1000 * 800)).convert("RGB")


def test_encoding_hashes_original_image_before_lossy_resize():
    from features.screen_understanding.common.hashing import image_dhash
    from features.screen_understanding.worker.runtime import ProactiveScreenWorker

    image = _fixture_image()
    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    worker._config["max_edge"] = 320
    jpeg, dhash = worker._encode_image(image)
    with Image.open(BytesIO(jpeg)) as resized:
        assert resized.size == (320, 256)
        assert dhash == image_dhash(image)
        assert dhash != image_dhash(resized), "fixture must distinguish pre/post-resize hashing"
    assert worker._frame is None


class _WireProcess:
    def __init__(self, arguments):
        self.process = subprocess.Popen(
            [sys.executable, "-B", *arguments],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.lines = queue.Queue()
        self.received = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, message_type, payload=None, request_id=None):
        from pet.workers.protocol import build_message, encode_message

        self.process.stdin.write(encode_message(build_message(WORKER_ID, message_type, payload, request_id=request_id)))
        self.process.stdin.flush()

    def receive(self, message_type, request_id=None):
        from pet.workers.protocol import decode_message

        while True:
            try:
                raw = self.lines.get(timeout=20)
            except queue.Empty:
                pytest.fail(f"worker timed out waiting for {message_type}: {self.received!r}")
            assert raw is not None, "worker stdout closed before expected message"
            message = decode_message(raw)
            self.received.append(message.as_dict())
            if message.type == "heartbeat" and message_type != "heartbeat":
                continue
            assert message.type == message_type, message.as_dict()
            assert message.worker_id == WORKER_ID
            if request_id is not None:
                assert message.request_id == request_id
            return message

    def configure(self):
        hello = self.receive("hello")
        assert hello.payload["pid"] == self.process.pid
        assert set(hello.payload["capabilities"]) == {"foreground.read", "screenshot.capture", "vision.request", "logging.write"}
        self.send("config_push", {"generation": 17, "max_edge": 320})
        ready = self.receive("ready")
        assert ready.payload["configured"] is True
        assert ready.payload["generation"] == 17

    def request(self, operation, arguments=None):
        self.send("request", {"operation": operation, "generation": 17, "arguments": arguments or {}}, request_id=operation)

    def response(self, operation):
        message = self.receive("response", operation)
        assert message.payload["operation"] == operation
        assert message.payload["generation"] == 17
        return message.payload


@contextmanager
def _wire(arguments):
    worker = _WireProcess(arguments)
    try:
        yield worker
    finally:
        if worker.process.stdin and not worker.process.stdin.closed:
            worker.process.stdin.close()
        try:
            worker.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.process.kill()
            worker.process.wait(timeout=10)
        worker.reader.join(timeout=10)
        worker.process.stdout.close()
        worker.process.stderr.close()


@pytest.mark.parametrize(
    "arguments",
    [
        ["-m", OFFICIAL_ENTRY],
        ["-m", "pet", "--worker", WORKER_ID],
        ["-m", "pet.workers.proactive_screen_worker"],
    ],
    ids=["official-package", "default-source-cli", "legacy-module"],
)
@pytest.mark.parametrize("stop", ["shutdown", "eof"])
def test_source_entry_handshake_heartbeat_and_exit(arguments, stop):
    with _wire(arguments) as worker:
        worker.configure()
        worker.send("heartbeat")
        assert worker.receive("heartbeat").payload["generation"] == 17
        # Unsupported operations must stay JSONL errors; never touch the desktop.
        worker.request("not-an-operation")
        assert worker.response("not-an-operation")["error_code"] == "unknown_operation"
        if stop == "shutdown":
            worker.send("shutdown")
        else:
            worker.process.stdin.close()
        assert worker.process.wait(timeout=15) == 0
        assert worker.process.stderr.read() == b""


def _install_boundary_fakes(legacy_patch):
    import importlib
    import json
    import urllib.request
    from io import BytesIO

    from PIL import ImageGrab

    vision = importlib.import_module("pet.vision" if legacy_patch else "features.screen_understanding.worker.vision")
    # These are read-only OS acquisition seams; encoding/hash/model/wire stay real.
    vision.foreground_window_info = lambda: {"hwnd": 42, "pid": 7, "process": "fixture.exe", "title": "fixture", "rect": [0, 0, 1000, 800]}
    vision.capture_window_rect = lambda rect: _fixture_image()
    ImageGrab.grab = lambda **kwargs: _fixture_image()

    def urlopen(request, **kwargs):
        assert request.full_url == "https://vision.invalid/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer fixture-secret"
        body = json.loads(request.data)
        assert body["model"] == "vision-model"
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return BytesIO(json.dumps({"choices": [{"message": {"content": "reply fixture-secret"}}]}).encode())

    urllib.request.urlopen = urlopen


@pytest.mark.parametrize("legacy_patch", [False, True], ids=["canonical-os-seams", "legacy-monkeypatch"])
def test_worker_execution_stays_isolated_across_capture_hash_http_and_budget(legacy_patch):
    from features.screen_understanding.common.hashing import image_dhash

    script = (
        IMPORT_GUARD
        + inspect.getsource(_fixture_image)
        + inspect.getsource(_install_boundary_fakes)
        + f"""
import runpy
_install_boundary_fakes({legacy_patch!r})
try:
    runpy.run_module({OFFICIAL_ENTRY!r}, run_name="__main__")
finally:
    assert_isolated()
    sys.stderr.write("boundary-ok\\n")
"""
    )
    provider = {"base_url": "https://vision.invalid", "model": "vision-model", "api_key": "fixture-secret"}
    with _wire(["-c", script]) as worker:
        worker.configure()
        worker.request("observe_foreground")
        observed = worker.response("observe_foreground")
        assert observed["status"] == "ok"
        window = observed["result"]["window"]
        assert window["hwnd"] == 42
        worker.request("capture_foreground", {"window": window})
        captured = worker.response("capture_foreground")
        assert captured["status"] == "ok"
        assert captured["result"]["dhash"] == image_dhash(_fixture_image())
        frame_id = captured["result"]["frame_id"]
        worker.request("analyze_frame", {"frame_id": frame_id, "provider": provider})
        budget = worker.receive("request")
        assert budget.payload == {"operation": "budget_check", "generation": 17, "arguments": {"kind": "automatic", "parent_request_id": "analyze_frame"}}
        worker.send("response", {"operation": "budget_check", "generation": 17, "status": "ok", "result": {"allowed": True}}, budget.request_id)
        analyzed = worker.response("analyze_frame")
        assert analyzed["status"] == "ok"
        assert analyzed["result"]["reply"] == "reply [redacted]"
        assert analyzed["result"]["request_metadata"] == {"kind": "automatic"}
        worker.request("analyze_frame", {"frame_id": frame_id, "provider": provider})
        assert worker.response("analyze_frame")["error_code"] == "frame_unavailable"
        worker.request("manual_look", {"provider": provider})
        manual = worker.response("manual_look")  # No automatic budget round-trip.
        assert manual["status"] == "ok"
        assert manual["result"]["reply"] == "reply [redacted]"
        assert manual["result"]["request_metadata"] == {"kind": "manual"}
        worker.request("release_frame", {"frame_id": frame_id})
        assert worker.response("release_frame")["result"] == {"released": True}
        worker.send("shutdown")
        assert worker.process.wait(timeout=15) == 0
        assert worker.process.stderr.read().splitlines() == [b"boundary-ok"]
        output = json.dumps(worker.received)
        assert "fixture-secret" not in output
        assert "data:image" not in output


@pytest.mark.parametrize(
    ("failure", "code", "hint"),
    [
        ("401", "vision_authentication_failed", "API Key"),
        ("404", "vision_protocol_unsupported", "模型或请求路径"),
        ("429", "vision_rate_limited", "限流"),
        ("network", "vision_network_failed", "代理"),
        ("empty", "vision_response_invalid", "有效回复"),
    ],
)
def test_worker_failure_categories_are_actionable_and_never_echo_provider(failure, code, hint):
    script = (
        IMPORT_GUARD
        + inspect.getsource(_fixture_image)
        + inspect.getsource(_install_boundary_fakes)
        + f"""
import runpy, urllib.error, urllib.request
from io import BytesIO
from types import SimpleNamespace
_install_boundary_fakes(False)
from features.screen_understanding.worker import vision
vision.time = SimpleNamespace(sleep=lambda seconds: None)
def fail(request, **kwargs):
    failure = {failure!r}
    if failure == "network":
        raise urllib.error.URLError("fixture-secret private-url")
    if failure == "empty":
        return BytesIO(b'{{"choices": []}}')
    raise urllib.error.HTTPError(request.full_url, int(failure), "fixture-secret", {{}}, BytesIO(b"fixture-secret private-body"))
urllib.request.urlopen = fail
try:
    runpy.run_module({OFFICIAL_ENTRY!r}, run_name="__main__")
finally:
    assert_isolated()
"""
    )
    provider = {"base_url": "https://vision.invalid", "model": "vision-model", "api_key": "fixture-secret"}
    with _wire(["-c", script]) as worker:
        worker.configure()
        worker.request("manual_look", {"provider": provider})
        response = worker.response("manual_look")
        assert response["error_code"] == code
        assert hint in response["message"]
        assert "fixture-secret" not in json.dumps(worker.received)
        assert "private-body" not in json.dumps(worker.received)
        assert "private-url" not in json.dumps(worker.received)
        worker.send("shutdown")
        assert worker.process.wait(timeout=15) == 0
        assert worker.process.stderr.read() == b""
