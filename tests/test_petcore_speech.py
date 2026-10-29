"""P4-b core 语音子系统：缓存 / 重试 / 回退 / 密钥 / 端口。

全部用例注入假合成器，**不联网、不碰真实音频设备**；Qt 只在端口用例里用
offscreen 平台，播放端是假实现。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from petcore import config as petcore_config
from petcore.config import Config
from petcore.contracts.speech import SpeechError, SpeechRequest
from petcore.net import tts as net_tts
from petcore.speech import SpeechCache, SpeechService
from petcore.speech import service as speech_service


class FakeConfig:
    def __init__(self, **values) -> None:
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeCredentials:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get(self, owner: str, key: str):
        return self.data.get((str(owner), str(key)))

    def set(self, owner: str, key: str, value: str) -> None:
        self.data[(str(owner), str(key))] = str(value)

    def delete(self, owner: str, key: str) -> None:
        self.data.pop((str(owner), str(key)), None)


class RecordingSynth:
    def __init__(
        self,
        payload: bytes = b"ID3-fake-audio",
        *,
        error: BaseException | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.gate = gate
        self.started = threading.Event()
        self.calls: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()

    def __call__(self, provider: str, request: SpeechRequest, out_path: Path) -> None:
        with self._lock:
            self.calls.append((provider, request.voice, request.text))
        self.started.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.error is not None:
            raise self.error
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payload)


class FakeAudio:
    def __init__(self, result=True) -> None:
        self.result = result
        self.paths: list[Path] = []
        self.volumes: list[float | None] = []
        self._lock = threading.Lock()

    def play(self, path, volume=None) -> bool:
        with self._lock:
            self.paths.append(Path(path))
            self.volumes.append(volume)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def set_volume(self, volume: float) -> None:
        pass


class TrackedAudio:
    """与 ``AudioAdapter.play_tracked`` 同款：``float(volume)`` 遇到 None 会抛。"""

    def __init__(self) -> None:
        self.volumes: list[float] = []
        self.finished = threading.Event()

    def play_tracked(self, path, *, volume=1.0, on_finished, on_error):
        value = max(0.0, min(1.0, float(volume)))  # None → TypeError（真机静默的根因）
        self.volumes.append(value)
        on_finished()
        self.finished.set()
        return True


def _make_service(
    tmp_path: Path,
    *,
    synth,
    config=None,
    credentials=None,
) -> SpeechService:
    return SpeechService(
        tmp_path,
        config=config if config is not None else FakeConfig(),
        credentials=credentials if credentials is not None else FakeCredentials(),
        synthesizer=synth,
    )


def _wait_until(predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---------------------------------------------------------------- 缓存


def test_cache_hit_requires_non_empty_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    synth = RecordingSynth(b"abc")
    service = _make_service(tmp_path, synth=synth)
    request = SpeechRequest(text="你好")

    first = service.synthesize(request)
    assert first.cached is False
    assert first.path.exists() and first.path.stat().st_size == 3
    assert len(synth.calls) == 1

    second = service.synthesize(request)
    assert second.cached is True
    assert second.path == first.path
    assert len(synth.calls) == 1

    first.path.write_bytes(b"")
    third = service.synthesize(request)
    assert third.cached is False
    assert len(synth.calls) == 2
    assert third.path.stat().st_size == 3


def test_cache_trim_keeps_newest_files_up_to_limit(tmp_path) -> None:
    cache = SpeechCache(tmp_path, limit=3)
    directory = cache.directory()
    for index in range(5):
        path = directory / f"entry-{index}.mp3"
        path.write_bytes(b"x")
        stamp = 1_000_000.0 + index
        os.utime(path, (stamp, stamp))

    assert cache.trim(3) == 2
    remaining = sorted(path.name for path in cache.entries())
    assert remaining == ["entry-2.mp3", "entry-3.mp3", "entry-4.mp3"]


def test_service_trim_respects_configured_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    synth = RecordingSynth(b"audio")
    service = _make_service(
        tmp_path, synth=synth, config=FakeConfig(speech_cache_limit=10)
    )
    for index in range(12):
        service.synthesize(SpeechRequest(text=f"第 {index} 句"))
    assert service.cache_limit() == 10
    assert len(service.cache.entries()) == 10


# ---------------------------------------------------------------- 失败与重试


def test_failed_synth_raises_reason_and_leaves_no_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    synth = RecordingSynth(error=SpeechError("network"))
    service = _make_service(tmp_path, synth=synth)

    with pytest.raises(SpeechError) as info:
        service.synthesize(
            SpeechRequest(text="hi", voice="zh-CN-XiaoyiNeural")
        )
    assert info.value.reason == "network"
    # 主音色 2 次 + 默认音色回退 1 次
    assert len(synth.calls) == 3
    assert synth.calls[0][1] == "zh-CN-XiaoyiNeural"
    assert synth.calls[-1][1] == net_tts.DEFAULT_VOICES["edge"]
    assert service.cache.entries() == []

    synth.calls.clear()
    with pytest.raises(SpeechError):
        service.synthesize(SpeechRequest(text="hi"))
    # 音色已经是默认音色：不重复试同一个音色，只有 2 次重试
    assert len(synth.calls) == 2


def test_empty_artifact_is_deleted_and_reported_as_cache_error(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    service = _make_service(tmp_path, synth=RecordingSynth(b""))
    with pytest.raises(SpeechError) as info:
        service.synthesize(SpeechRequest(text="x"))
    assert info.value.reason == "cache"
    assert service.cache.entries() == []


def test_network_exception_is_classified(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    service = _make_service(tmp_path, synth=RecordingSynth(error=TimeoutError()))
    with pytest.raises(SpeechError) as info:
        service.synthesize(SpeechRequest(text="x"))
    assert info.value.reason == "timeout"


# ---------------------------------------------------------------- 预检


def test_preflight_swaps_unknown_voice_without_synthesizing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    synth = RecordingSynth()
    service = _make_service(
        tmp_path,
        synth=synth,
        config=FakeConfig(speech_voice="zh-CN-XiaohanNeural"),  # 2026-09 已下架
    )

    assert service.preflight("edge") == "edge"
    assert synth.calls == []
    assert service.effective_voice("edge") == net_tts.DEFAULT_VOICES["edge"]

    result = service.synthesize(SpeechRequest(text="hi"))
    assert result.voice == net_tts.DEFAULT_VOICES["edge"]
    assert synth.calls[0][1] == net_tts.DEFAULT_VOICES["edge"]


def test_preflight_unknown_backend_falls_back_to_edge(tmp_path) -> None:
    service = _make_service(
        tmp_path,
        synth=RecordingSynth(),
        config=FakeConfig(speech_backend="nope", speech_fallback=""),
    )
    assert service.preflight() == "edge"


# ---------------------------------------------------------------- provider 列表


def test_provider_list_marks_missing_dependency_unavailable(
    tmp_path, monkeypatch
) -> None:
    fake = net_tts.TtsProviderSpec(
        id="fake",
        label="不存在的后端",
        online=True,
        default_voice="v",
        dependency="definitely_missing_module_xyz",
    )
    monkeypatch.setitem(net_tts.PROVIDER_SPECS, "fake", fake)
    service = _make_service(tmp_path, synth=RecordingSynth())

    infos = {info.id: info for info in service.providers()}
    assert infos["fake"].available is False
    assert infos["fake"].reason == "unavailable"
    assert infos["edge"].available is net_tts.dependency_present("edge_tts")
    assert infos["edge"].fields == net_tts.PROVIDER_SPECS["edge"].fields
    assert infos["edge"].online is True


def test_provider_without_key_reports_auth_until_secret_is_set(tmp_path) -> None:
    credentials = FakeCredentials()
    service = _make_service(
        tmp_path, synth=RecordingSynth(), credentials=credentials
    )

    before = {info.id: info for info in service.providers()}["doubao"]
    assert before.available is False
    assert before.reason == "auth"

    service.set_secret("doubao", "api_key", "key-123")
    after = {info.id: info for info in service.providers()}["doubao"]
    assert after.available is True
    assert after.reason == ""


# ---------------------------------------------------------------- 密钥


def test_secret_round_trip_uses_speech_owner(tmp_path) -> None:
    credentials = FakeCredentials()
    service = _make_service(
        tmp_path, synth=RecordingSynth(), credentials=credentials
    )

    assert service.secret_status("doubao") == {"api_key": False}
    service.set_secret("doubao", "api_key", "key-123")
    assert credentials.data == {("speech.doubao", "api_key"): "key-123"}
    assert service.secret_status("doubao") == {"api_key": True}
    assert service.secret_status("edge") == {}
    assert service.secret_status("nope") == {}

    service.set_secret("doubao", "api_key", "")
    assert service.secret_status("doubao") == {"api_key": False}
    with pytest.raises(ValueError):
        service.set_secret("edge", "api_key", "x")


# ---------------------------------------------------------------- 端口


def _application():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _port(service, audio):
    from petcore.ui.ports import CoreSpeechPort

    return CoreSpeechPort(service, audio)


def test_core_speech_port_speaks_through_shared_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    assert _application() is not None
    service = _make_service(tmp_path, synth=RecordingSynth(b"mp3-bytes"))
    audio = FakeAudio()
    port = _port(service, audio)
    done = threading.Event()
    captured = []

    token = port.speak(
        SpeechRequest(text="说话"), on_done=lambda result: (captured.append(result), done.set())
    )

    assert isinstance(token, str) and token
    assert done.wait(5.0)
    assert captured and captured[0].path.exists()
    assert audio.paths == [captured[0].path]
    assert port.playing() is False


def test_core_speech_port_passes_a_real_volume_to_tracked_playback(
    tmp_path, monkeypatch
) -> None:
    """请求留空 volume 时也必须给音频层一个真实浮点：None 会让整句静默丢失。"""
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    assert _application() is not None
    service = _make_service(tmp_path, synth=RecordingSynth(b"mp3-bytes"))
    audio = TrackedAudio()
    port = _port(service, audio)
    errors: list[SpeechError] = []

    port.speak(SpeechRequest(text="点击台词"), on_error=errors.append)

    assert audio.finished.wait(5.0)
    assert errors == []
    assert audio.volumes == [1.0]


def test_core_speech_port_playback_volume_follows_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    assert _application() is not None
    service = _make_service(
        tmp_path,
        synth=RecordingSynth(b"mp3-bytes"),
        config=FakeConfig(speech_volume=0.4),
    )
    audio = TrackedAudio()
    port = _port(service, audio)

    port.speak(SpeechRequest(text="小声一点"))

    assert audio.finished.wait(5.0)
    assert audio.volumes == [0.4]


def test_core_speech_port_stop_blocks_pending_playback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    assert _application() is not None
    gate = threading.Event()
    synth = RecordingSynth(b"mp3-bytes", gate=gate)
    service = _make_service(tmp_path, synth=synth)
    audio = FakeAudio()
    port = _port(service, audio)
    done = threading.Event()

    token = port.speak(SpeechRequest(text="别出声"), on_done=lambda result: done.set())
    assert synth.started.wait(5.0)
    port.stop(token)
    gate.set()

    assert _wait_until(lambda: not port.playing(), timeout=5.0)
    assert done.wait(0.2) is False
    assert audio.paths == []


def test_core_speech_port_audio_failure_reports_unavailable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    assert _application() is not None
    service = _make_service(tmp_path, synth=RecordingSynth(b"mp3-bytes"))
    port = _port(service, FakeAudio(result=False))
    errors: list[SpeechError] = []
    finished = threading.Event()

    port.speak(
        SpeechRequest(text="x"),
        on_error=lambda error: (errors.append(error), finished.set()),
    )

    assert finished.wait(5.0)
    assert errors and errors[0].reason == "unavailable"
    assert port.playing() is False


# ---------------------------------------------------------------- 配置


def test_config_normalizes_speech_keys(tmp_path) -> None:
    config = Config(
        tmp_path,
        {
            "speech_backend": "  NoPe ",
            "speech_fallback": "MIMO",
            "speech_voice": "x" * 80,
            "speech_rate": 9,
            "speech_volume": -3,
            "speech_cache_limit": 5,
            "speech_doubao_voice": "d" * 70,
            "speech_mimo_voice": " 冰糖 ",
        },
    )
    assert config.get("speech_backend") == "edge"
    assert config.get("speech_fallback") == "mimo"
    assert config.get("speech_voice") == "x" * 64
    assert config.get("speech_rate") == 2.0
    assert config.get("speech_volume") == 0.0
    assert config.get("speech_cache_limit") == 10
    assert config.get("speech_doubao_voice") == "d" * 64
    assert config.get("speech_mimo_voice") == "冰糖"

    low = Config(
        tmp_path,
        {"speech_rate": 0.1, "speech_volume": 3, "speech_cache_limit": 99999},
    )
    assert low.get("speech_rate") == 0.5
    assert low.get("speech_volume") == 1.0
    assert low.get("speech_cache_limit") == 2000

    for key in (
        "speech_backend",
        "speech_fallback",
        "speech_voice",
        "speech_rate",
        "speech_volume",
        "speech_cache_limit",
        "speech_doubao_voice",
        "speech_mimo_voice",
    ):
        assert key in petcore_config.GLOBAL_CONFIG_KEYS


def test_build_ports_wires_speech_and_skips_without_service(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    assert _application() is not None
    from petcore.contracts.host import HostPorts
    from petcore.ui.ports import build_ports

    class _Host:
        def __init__(self, service) -> None:
            self.root = tmp_path
            self.speech = service
            self.config = None
            self.quiet = None
            self.window = None
            self.pets = None
            self.api_store = None
            self.api_credentials = None
            self.worker_host = None

        def presentation_port(self):
            return None

    service = _make_service(tmp_path, synth=RecordingSynth())
    ports = build_ports(_Host(service), object())
    assert ports.speech is not None
    assert ports.speech.service is service
    assert ports.speech.audio is ports.audio
    assert ports.speech._scheduler is ports.scheduler

    class _NoSpeechHost(_Host):
        def __init__(self) -> None:
            super().__init__(None)
            del self.speech

    assert build_ports(_NoSpeechHost(), object()).speech is None
    assert HostPorts().speech is None


def test_check_report_lists_speech_providers(tmp_path) -> None:
    from petcore import cli
    from petcore.config import Config

    report = cli._speech_report(tmp_path, Config(tmp_path))

    assert "error" not in report, report
    assert report["backend"] in {"edge", "sapi", "doubao", "mimo"}
    providers = {item["id"] for item in report["providers"]}
    assert {"edge", "sapi"} <= providers

@pytest.mark.parametrize("failure", [SpeechError("network"), ValueError("backend failure")])
def test_synthesis_error_callback_runs_on_qt_thread(tmp_path, monkeypatch, failure):
    from PySide6.QtCore import QEventLoop, QTimer
    from petcore.ui.ports import CoreSpeechPort, QtScheduler

    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    app = _application()
    main_thread = threading.get_ident()

    def synth(*_args):
        raise failure

    service = _make_service(tmp_path, synth=synth)
    scheduler = QtScheduler()
    port = CoreSpeechPort(service, FakeAudio(), scheduler=scheduler)
    callbacks = []
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if callbacks else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    port.speak(SpeechRequest(text="error"), on_error=lambda error: callbacks.append(threading.get_ident()))
    try:
        poll.start(20)
        timeout.start(10000)
        loop.exec()
        assert callbacks == [main_thread]
        assert not port.playing()
    finally:
        poll.stop()
        timeout.stop()
        port.stop()
        app.processEvents()


def test_speech_never_plays_on_worker_when_gui_scheduler_is_unavailable(tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from petcore.ui.ports import CoreSpeechPort

    class BrokenScheduler:
        def call_later(self, _delay, _callback):
            raise RuntimeError("scheduler closed")

    service = _make_service(tmp_path, synth=RecordingSynth(b"audio"))
    audio = FakeAudio()
    port = CoreSpeechPort(service, audio, scheduler=BrokenScheduler())
    callbacks = []
    app = _application()
    # Execute the real public async path; the service completion is observable
    # through the port's token state, no guessed sleep interval.
    port.speak(SpeechRequest(text="x"), on_done=lambda _result: callbacks.append("done"))
    loop = QEventLoop()
    poll, timeout = QTimer(), QTimer()
    poll.timeout.connect(lambda: loop.quit() if not port.playing() else None)
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    try:
        poll.start(20)
        timeout.start(10000)
        loop.exec()
        assert not port.playing()
    finally:
        poll.stop()
        timeout.stop()
        port.stop()
        app.processEvents()
    assert audio.paths == []
    assert callbacks == []


def test_fallback_voice_is_cached_under_its_own_key(tmp_path, monkeypatch) -> None:
    """兜底音色的产物不许写进「请求音色」的缓存键（AUD-20）。"""
    monkeypatch.setattr(speech_service, "RETRY_DELAY_S", 0.0)
    preferred = "zh-CN-YunxiNeural"
    from petcore.net import tts as net_tts

    fallback = net_tts.DEFAULT_VOICES.get("edge", "")
    assert fallback and fallback != preferred

    class _PickSynth(RecordingSynth):
        def __call__(self, provider, request, out_path):
            if request.voice == preferred:
                with self._lock:
                    self.calls.append((provider, request.voice, request.text))
                raise speech_service.SpeechError("network", "boom")
            return super().__call__(provider, request, out_path)

    synth = _PickSynth(b"abc")
    service = _make_service(tmp_path, synth=synth)
    request = SpeechRequest(text="同一句台词", voice=preferred)

    first = service.synthesize(request)
    assert first.voice == fallback, "产物来自兜底音色，就必须报告兜底音色"

    # 第二次命中缓存：必须报兜底音色，且不能再走首选音色的键。
    second = service.synthesize(request)
    assert second.cached is True
    assert second.voice == fallback
    assert second.path == first.path


def test_truncated_audio_is_rejected(tmp_path) -> None:
    from petcore.net.tts import _require_audio

    path = tmp_path / "a.mp3"
    path.write_bytes(b"partial")
    with pytest.raises(Exception) as info:
        _require_audio(path, "豆包", truncated=True)
    assert "不完整" in str(info.value)
