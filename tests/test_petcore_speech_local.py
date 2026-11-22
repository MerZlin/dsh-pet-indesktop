"""本地部署 TTS（provider `local`）：声明、两种协议、缓存键与配置归一化。

全部用例注入假传输层，**不联网**；音频播放不参与本文件。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from petcore.config import Config
from petcore.contracts.speech import SpeechError, SpeechRequest
from petcore.net import tts as net_tts
from petcore.speech import SpeechService


class FakeConfig:
    def __init__(self, **values) -> None:
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeCredentials:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get(self, owner, key):
        return self.data.get((str(owner), str(key)))

    def set(self, owner, key, value):
        self.data[(str(owner), str(key))] = str(value)

    def delete(self, owner, key):
        self.data.pop((str(owner), str(key)), None)


class RecordingSynth:
    def __init__(self, payload: bytes = b"ID3-local") -> None:
        self.payload = payload
        self.calls: list[tuple[str, SpeechRequest, Path]] = []

    def __call__(self, provider, request, out_path):
        self.calls.append((provider, request, Path(out_path)))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(self.payload)


def _local_config(**overrides):
    values = {
        "speech_backend": "local",
        "speech_fallback": "",
        "speech_local_endpoint": "http://127.0.0.1:9880",
        "speech_local_protocol": "openai",
        "speech_local_path": "",
        "speech_local_model": "kokoro",
        "speech_local_voice": "",
        "speech_local_format": "mp3",
        "speech_local_timeout": 20.0,
    }
    values.update(overrides)
    return FakeConfig(**values)


def _service(tmp_path: Path, config, synth=None) -> SpeechService:
    return SpeechService(
        tmp_path,
        config=config,
        credentials=FakeCredentials(),
        synthesizer=synth or RecordingSynth(),
    )


# ---------------------------------------------------------------- 声明


def test_local_provider_is_declared_offline_with_optional_key() -> None:
    spec = net_tts.PROVIDER_SPECS["local"]
    assert spec.online is False
    assert spec.secret_fields == ("api_key",)
    assert "endpoint" in spec.fields
    assert net_tts.provider_available("local") == (True, "")
    assert net_tts.DEFAULT_VOICES["local"] == ""


def test_local_needs_an_endpoint_to_be_usable(tmp_path: Path) -> None:
    service = _service(tmp_path, _local_config(speech_local_endpoint=""))
    infos = {info.id: info for info in service.providers()}
    assert infos["local"].available is False
    assert infos["local"].reason == "config"


# ---------------------------------------------------------------- 请求形状


def _fake_fetch(monkeypatch, payload: bytes, content_type: str = "audio/mpeg"):
    calls: list[tuple[str, dict]] = []

    def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        return 200, (("Content-Type", content_type),), payload

    monkeypatch.setattr(net_tts, "fetch_url", fake_fetch)
    return calls


def test_openai_protocol_posts_to_v1_audio_speech(monkeypatch, tmp_path: Path) -> None:
    calls = _fake_fetch(monkeypatch, b"ID3-openai")
    out = tmp_path / "out.mp3"

    net_tts.local_synthesize(
        "你好",
        endpoint="http://127.0.0.1:9880/",
        protocol="openai",
        path="",
        model="kokoro",
        voice="zf_xiaobei",
        format="mp3",
        api_key="",
        rate=1.25,
        volume=0.8,
        out_path=out,
    )

    assert out.read_bytes() == b"ID3-openai"
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:9880/v1/audio/speech"
    assert kwargs["method"] == "POST"
    assert kwargs["prefer_direct"] is True
    assert kwargs["direct_only"] is True
    body = json.loads(kwargs["body"])
    assert body["input"] == "你好"
    assert body["model"] == "kokoro"
    assert body["voice"] == "zf_xiaobei"
    assert body["response_format"] == "mp3"
    assert body["speed"] == pytest.approx(1.25)
    # 本地地址不能被系统代理带走，也不能收窄到只允许 https
    assert kwargs["allowed_schemes"] == ("http", "https")


def test_simple_protocol_uses_configured_path_and_speed(monkeypatch, tmp_path: Path) -> None:
    calls = _fake_fetch(monkeypatch, b"RIFF-simple", "audio/wav")
    out = tmp_path / "out.wav"

    net_tts.local_synthesize(
        "早",
        endpoint="http://127.0.0.1:5001",
        protocol="simple",
        path="/tts",
        model="",
        voice="s1",
        format="wav",
        api_key="secret",
        rate=0.9,
        volume=1.0,
        out_path=out,
    )

    assert out.read_bytes() == b"RIFF-simple"
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:5001/tts"
    body = json.loads(kwargs["body"])
    assert body == {"text": "早", "voice": "s1", "speed": pytest.approx(0.9)}
    headers = dict(kwargs["headers"])
    assert headers["Authorization"] == "Bearer secret"


def test_simple_protocol_accepts_base64_json(monkeypatch, tmp_path: Path) -> None:
    encoded = base64.b64encode(b"WAVE-json").decode("ascii")
    _fake_fetch(monkeypatch, json.dumps({"audio": encoded}).encode(), "application/json")
    out = tmp_path / "out.mp3"

    net_tts.local_synthesize(
        "hi",
        endpoint="http://127.0.0.1:5001",
        protocol="simple",
        path="",
        model="",
        voice="",
        format="mp3",
        api_key="",
        rate=1.0,
        volume=1.0,
        out_path=out,
    )

    assert out.read_bytes() == b"WAVE-json"


def test_local_synthesize_rejects_empty_endpoint(tmp_path: Path) -> None:
    with pytest.raises(net_tts.NetworkError) as caught:
        net_tts.local_synthesize(
            "hi",
            endpoint="",
            protocol="openai",
            path="",
            model="",
            voice="",
            format="mp3",
            api_key="",
            rate=1.0,
            volume=1.0,
            out_path=tmp_path / "x.mp3",
        )
    assert caught.value.reason == "protocol"


def test_local_synthesize_rejects_non_http_endpoint(tmp_path: Path) -> None:
    with pytest.raises(net_tts.NetworkError) as caught:
        net_tts.local_synthesize(
            "hi",
            endpoint="file:///etc/passwd",
            protocol="openai",
            path="",
            model="",
            voice="",
            format="mp3",
            api_key="",
            rate=1.0,
            volume=1.0,
            out_path=tmp_path / "x.mp3",
        )
    assert caught.value.reason == "protocol"


# ---------------------------------------------------------------- service 接线


def test_default_rate_volume_come_from_the_global_config(tmp_path: Path) -> None:
    config = FakeConfig(
        speech_backend="edge",
        speech_fallback="",
        speech_rate=1.4,
        speech_volume=0.6,
    )
    synth = RecordingSynth()
    service = _service(tmp_path, config, synth)

    service.synthesize(SpeechRequest(text="hi", provider="edge"))

    _provider, request, _path = synth.calls[0]
    assert request.rate == pytest.approx(1.4)
    assert request.volume == pytest.approx(0.6)


def test_service_uses_local_voice_and_format_suffix(tmp_path: Path) -> None:
    synth = RecordingSynth(b"RIFF-service")
    service = _service(
        tmp_path,
        _local_config(speech_local_voice="voice-a", speech_local_format="wav"),
        synth,
    )

    result = service.synthesize(SpeechRequest(text="测试", provider="local"))

    assert result.provider == "local"
    assert result.voice == "voice-a"
    assert result.path.suffix == ".wav"
    assert synth.calls and synth.calls[0][0] == "local"


def test_cache_key_changes_with_endpoint(tmp_path: Path) -> None:
    service = _service(tmp_path, _local_config())
    first = service.synthesize(SpeechRequest(text="同一句", provider="local"))

    service = _service(
        tmp_path, _local_config(speech_local_endpoint="http://127.0.0.1:9999")
    )
    second = service.synthesize(SpeechRequest(text="同一句", provider="local"))

    assert first.path != second.path
    assert first.path.exists() and second.path.exists()


def test_cache_key_changes_with_protocol_and_model(tmp_path: Path) -> None:
    service = _service(tmp_path, _local_config())
    first = service.synthesize(SpeechRequest(text="同一句", provider="local"))

    service = _service(tmp_path, _local_config(speech_local_protocol="simple"))
    second = service.synthesize(SpeechRequest(text="同一句", provider="local"))

    service = _service(tmp_path, _local_config(speech_local_model="other-model"))
    third = service.synthesize(SpeechRequest(text="同一句", provider="local"))

    assert len({first.path, second.path, third.path}) == 3


# ---------------------------------------------------------------- 配置归一化


def test_local_config_is_normalized(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        data={
            "speech_backend": "local",
            "speech_local_endpoint": "127.0.0.1:9880/",
            "speech_local_protocol": "nonsense",
            "speech_local_path": "tts",
            "speech_local_model": "  kokoro  ",
            "speech_local_voice": "  v  ",
            "speech_local_format": "ogg",
            "speech_local_timeout": 999,
        },
    )

    assert config.get("speech_backend") == "local"
    assert config.get("speech_local_endpoint") == "http://127.0.0.1:9880"
    assert config.get("speech_local_protocol") == "openai"
    assert config.get("speech_local_path") == "/tts"
    assert config.get("speech_local_model") == "kokoro"
    assert config.get("speech_local_voice") == "v"
    assert config.get("speech_local_format") == "mp3"
    assert config.get("speech_local_timeout") == pytest.approx(120.0)


def test_local_config_rejects_non_http_endpoint(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        data={"speech_local_endpoint": "file:///tmp/model", "speech_local_protocol": "openai"},
    )
    assert config.get("speech_local_endpoint") == ""


def test_local_endpoint_rejects_query_or_fragment(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        data={"speech_local_endpoint": "http://127.0.0.1:9880?x=1"},
    )
    assert config.get("speech_local_endpoint") == ""

    config = Config(
        tmp_path,
        data={"speech_local_endpoint": "http://127.0.0.1:9880#tts"},
    )
    assert config.get("speech_local_endpoint") == ""


def test_local_is_a_global_key(tmp_path: Path) -> None:
    from petcore.config import GLOBAL_CONFIG_KEYS

    assert "speech_local_endpoint" in GLOBAL_CONFIG_KEYS
    assert "speech_local_protocol" in GLOBAL_CONFIG_KEYS
    assert "speech_local_format" in GLOBAL_CONFIG_KEYS


# ---------------------------------------------------------------- GPT-SoVITS


def _gpt_sovits_config(**overrides):
    values = {
        "speech_backend": "local",
        "speech_fallback": "",
        "speech_local_endpoint": "http://127.0.0.1:9880",
        "speech_local_protocol": "gpt_sovits",
        "speech_local_path": "",
        "speech_local_model": "",
        "speech_local_voice": "",
        "speech_local_format": "mp3",
        "speech_local_timeout": 60.0,
        "speech_local_ref_audio": r"D:\voices\ref.wav",
        "speech_local_prompt_text": "参考音频里说的话",
        "speech_local_prompt_lang": "zh",
        "speech_local_text_lang": "zh",
    }
    values.update(overrides)
    return FakeConfig(**values)


def test_gpt_sovits_protocol_posts_api_v2_fields(monkeypatch, tmp_path: Path) -> None:
    """api_v2 的 POST /tts 要的是 ref/prompt/lang，不是 OpenAI 的 voice/input。"""
    calls = _fake_fetch(monkeypatch, b"RIFF-gpt", "audio/wav")
    out = tmp_path / "out.wav"

    net_tts.local_synthesize(
        "今天也要开心",
        endpoint="http://127.0.0.1:9880",
        protocol="gpt_sovits",
        path="",
        model="ignored",
        voice="ignored",
        format="mp3",
        api_key="",
        rate=1.2,
        volume=1.0,
        ref_audio=r"D:\voices\ref.wav",
        prompt_text="参考音频里说的话",
        prompt_lang="zh",
        text_lang="ja",
        out_path=out,
    )

    assert out.read_bytes() == b"RIFF-gpt"
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:9880/tts"
    body = json.loads(kwargs["body"])
    assert body["text"] == "今天也要开心"
    assert body["text_lang"] == "ja"
    assert body["ref_audio_path"] == r"D:\voices\ref.wav"
    assert body["prompt_text"] == "参考音频里说的话"
    assert body["prompt_lang"] == "zh"
    assert body["media_type"] == "wav"
    assert body["streaming_mode"] is False
    assert body["text_split_method"] == "cut5"
    assert body["speed_factor"] == pytest.approx(1.2)
    assert "voice" not in body and "model" not in body
    # 本地服务只直连；参考音频路径与台词都不该被系统代理带走
    assert kwargs["direct_only"] is True


def test_gpt_sovits_needs_reference_audio(tmp_path: Path) -> None:
    service = _service(tmp_path, _gpt_sovits_config(speech_local_ref_audio=""))
    infos = {info.id: info for info in service.providers()}
    assert infos["local"].available is False
    assert infos["local"].reason == "config"


def test_gpt_sovits_forces_wav_suffix(tmp_path: Path) -> None:
    service = _service(tmp_path, _gpt_sovits_config(speech_local_format="mp3"))
    result = service.synthesize(SpeechRequest(text="hi", provider="local"))
    # api_v2 只产出 wav：缓存后缀不能按配置里的 mp3，否则播放器解不出来。
    assert result.path.suffix == ".wav"


def test_cache_key_changes_with_reference_audio(tmp_path: Path) -> None:
    first = _service(tmp_path, _gpt_sovits_config()).synthesize(
        SpeechRequest(text="同一句", provider="local")
    )
    second = _service(
        tmp_path, _gpt_sovits_config(speech_local_ref_audio=r"D:\voices\other.wav")
    ).synthesize(SpeechRequest(text="同一句", provider="local"))
    assert first.path != second.path


def test_local_config_normalizes_gpt_sovits_fields(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        data={
            "speech_local_protocol": "gpt_sovits",
            "speech_local_ref_audio": "  D:/voices/ref.wav  ",
            "speech_local_prompt_text": "x" * 600,
            "speech_local_prompt_lang": "zh-CN",
            "speech_local_text_lang": "",
        },
    )
    assert config.get("speech_local_protocol") == "gpt_sovits"
    assert config.get("speech_local_ref_audio") == "D:/voices/ref.wav"
    assert len(config.get("speech_local_prompt_text")) == 500
    assert config.get("speech_local_prompt_lang") == "zh"
    assert config.get("speech_local_text_lang") == "zh"


def test_gpt_sovits_keys_are_global() -> None:
    from petcore.config import GLOBAL_CONFIG_KEYS

    for key in (
        "speech_local_ref_audio",
        "speech_local_prompt_text",
        "speech_local_prompt_lang",
        "speech_local_text_lang",
    ):
        assert key in GLOBAL_CONFIG_KEYS
