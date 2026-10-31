from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore.config import Config
from petcore.net import asr as asr_net
from petcore.platform.mic import MicRecorder, wav_bytes
from petcore.speech.asr import AsrService
from petcore.ui.asr_channel import AsrChannel


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


# ---------------------------------------------------------------- 录音


class FakeHeader:
    def __init__(self, size: int) -> None:
        self.dwBytesRecorded = int(size)


class FakeInputApi:
    def __init__(self, pcm: bytes = b"\x01\x00" * 8000) -> None:
        self.pcm = pcm
        self.opened = False
        self.started = False
        self.stopped = False
        self.closed = False
        self.prepared = False

    def num_devices(self) -> int:
        return 1

    def device_name(self, index: int) -> str:
        return "测试麦克风"

    def open(self, device, sample_rate, channels, max_bytes):
        self.opened = True
        return "handle", FakeHeader(len(self.pcm)), bytearray(max_bytes)

    def start(self, handle) -> None:
        self.started = True

    def stop(self, handle) -> None:
        self.stopped = True

    def reset(self, handle) -> None:
        pass

    def recorded_bytes(self, header) -> int:
        return header.dwBytesRecorded

    def read_buffer(self, buffer, size: int) -> bytes:
        return bytes(self.pcm[:size])

    def unprepare(self, handle, header) -> None:
        pass

    def close(self, handle) -> None:
        self.closed = True


def test_wav_bytes_wraps_pcm16() -> None:
    wav = wav_bytes(b"\x01\x00" * 100, sample_rate=16000, channels=1)
    import wave

    with wave.open(BytesIO(wav), "rb") as stream:
        assert stream.getnchannels() == 1
        assert stream.getframerate() == 16000
        assert stream.getsampwidth() == 2
        assert stream.getnframes() == 100


def test_recorder_stop_returns_a_playable_wav() -> None:
    api = FakeInputApi()
    recorder = MicRecorder(api)
    assert recorder.devices()[0].name == "测试麦克风"
    recorder.start(0, max_seconds=3)
    assert recorder.recording is True
    wav = recorder.stop()
    assert recorder.recording is False
    assert api.started and api.stopped and api.closed
    assert wav.startswith(b"RIFF")


def test_recorder_cancel_discards_audio() -> None:
    api = FakeInputApi()
    recorder = MicRecorder(api)
    recorder.start(0, max_seconds=3)
    recorder.cancel()
    assert recorder.recording is False
    assert api.closed is True


# ---------------------------------------------------------------- 识别服务


def test_asr_service_uses_the_http_recognizer(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        {
            "asr_engine": "http",
            "asr_endpoint": "http://127.0.0.1:8000/v1/audio/transcriptions",
        },
    )
    calls = []

    def fake(endpoint, wav, **kwargs):
        calls.append((endpoint, wav, kwargs))
        return "你好"

    service = AsrService(config, http_recognizer=fake)
    assert service.recognize(b"RIFFxxxx") == "你好"
    assert calls[0][0].endswith("/v1/audio/transcriptions")
    assert calls[0][2]["language"] == "zh"


def test_asr_service_system_fallback_deletes_the_temp_wav(tmp_path: Path) -> None:
    config = Config(tmp_path, {"asr_engine": "sapi"})
    seen = []

    def fake(path, *, timeout):
        seen.append(Path(path))
        assert Path(path).is_file()
        return "系统识别"

    service = AsrService(config, system_recognizer=fake)
    assert service.recognize(wav_bytes(b"\x00\x00" * 100)) == "系统识别"
    assert seen and not seen[0].exists()


def test_asr_service_requires_an_endpoint_for_http(tmp_path: Path) -> None:
    service = AsrService(Config(tmp_path, {"asr_engine": "http"}))
    with pytest.raises(asr_net.AsrError) as caught:
        service.recognize(b"RIFFxxxx")
    assert caught.value.reason == "unconfigured"


# ---------------------------------------------------------------- HTTP 客户端


class _Handler(BaseHTTPRequestHandler):
    status = 200
    payload = b'{"text":"hello"}'
    bodies: list[bytes] = []

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        type(self).bodies.append(self.rfile.read(length))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *args):
        return


@pytest.fixture
def asr_server():
    _Handler.status = 200
    _Handler.payload = b'{"text":"hello"}'
    _Handler.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_transcribe_posts_multipart_and_reads_text(asr_server) -> None:
    url = f"http://127.0.0.1:{asr_server}/v1/audio/transcriptions"
    text = asr_net.transcribe(
        url, wav_bytes(b"\x00\x00" * 50), model="whisper-1", language="zh"
    )
    assert text == "hello"
    body = _Handler.bodies[0]
    assert b'name="file"' in body
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'name="language"' in body and b"zh" in body


def test_transcribe_maps_http_429_to_rate_limit(asr_server) -> None:
    _Handler.status = 429
    url = f"http://127.0.0.1:{asr_server}/v1/audio/transcriptions"
    with pytest.raises(asr_net.AsrError) as caught:
        asr_net.transcribe(url, wav_bytes(b"\x00\x00" * 50))
    assert caught.value.reason == "rate_limit"


def test_transcribe_rejects_non_loopback_http() -> None:
    with pytest.raises(asr_net.AsrError) as caught:
        asr_net.transcribe("http://192.168.1.5:8000/v1/audio/transcriptions", b"x")
    assert caught.value.reason == "insecure"


# ---------------------------------------------------------------- 通道


class ChannelRecorder:
    def __init__(self) -> None:
        self.recording = False
        self.cancelled = False

    def start(self, device, *, max_seconds=30):
        self.recording = True

    def stop(self):
        self.recording = False
        return wav_bytes(b"\x00\x00" * 50)

    def cancel(self):
        self.recording = False
        self.cancelled = True


class ChannelService:
    def recognize(self, wav):
        return "识别结果"


def test_asr_channel_emits_finished(app, tmp_path: Path) -> None:
    recorder = ChannelRecorder()
    channel = AsrChannel(
        ChannelService(), Config(tmp_path), recorder=recorder
    )
    texts = []
    failed = []
    channel.finished.connect(texts.append)
    channel.failed.connect(failed.append)
    try:
        assert channel.start() is True
        channel.stop()
        assert _wait_until(lambda: bool(texts or failed))
        assert texts == ["识别结果"]
        assert failed == []
    finally:
        channel.shutdown()


def test_asr_channel_cancel_does_not_emit_text(app, tmp_path: Path) -> None:
    recorder = ChannelRecorder()
    channel = AsrChannel(
        ChannelService(), Config(tmp_path), recorder=recorder
    )
    texts = []
    failed = []
    channel.finished.connect(texts.append)
    channel.failed.connect(failed.append)
    try:
        assert channel.start() is True
        channel.cancel()
        assert _wait_until(lambda: channel.recording is False)
        assert texts == []
        assert failed == ["cancelled"]
        assert recorder.cancelled is True
    finally:
        channel.shutdown()


def test_asr_channel_refuses_when_disabled(app, tmp_path: Path) -> None:
    channel = AsrChannel(
        ChannelService(),
        Config(tmp_path, {"asr_enabled": False}),
        recorder=ChannelRecorder(),
    )
    failed = []
    channel.failed.connect(failed.append)
    try:
        assert channel.start() is False
        assert failed == ["disabled"]
    finally:
        channel.shutdown()


def test_peek_wav_reads_without_stopping() -> None:
    api = FakeInputApi()
    recorder = MicRecorder(api)
    recorder.start(0, max_seconds=3)
    wav = recorder.peek_wav()
    assert wav.startswith(b"RIFF")
    assert recorder.recording is True
    assert api.stopped is False
    recorder.stop()


def test_channel_emits_partial_then_final_text(app) -> None:
    class FakeService:
        def __init__(self) -> None:
            self.calls = 0

        def engine(self) -> str:
            return "http"

        def recognize(self, wav: bytes) -> str:
            self.calls += 1
            return "最终文本" if self.calls >= 2 else "实时文本"

    config = SimpleNamespace(
        get=lambda key, default=None: {
            "asr_enabled": True,
            "asr_stream_enabled": True,
            "asr_partial_interval_ms": 400,
            "asr_max_seconds": 30,
        }.get(key, default)
    )
    service = FakeService()
    recorder = MicRecorder(FakeInputApi())
    channel = AsrChannel(service, config=config, recorder=recorder)
    partials: list[str] = []
    finished: list[str] = []
    channel.partial.connect(partials.append)
    channel.finished.connect(finished.append)
    try:
        assert channel.start() is True
        assert _wait_until(lambda: bool(partials), timeout=3.0)
        channel.stop()
        assert _wait_until(lambda: bool(finished), timeout=3.0)
        assert partials[0] == "实时文本"
        assert finished == ["最终文本"]
    finally:
        channel.shutdown()
