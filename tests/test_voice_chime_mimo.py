# -*- coding: utf-8 -*-
"""小米 MiMo TTS 后端（语音报时的第二个合成后端）回归测试。

三层覆盖：
1. 纯逻辑（pet/voice_chime.py）：后端清洗、尝试链顺序、请求体构造、响应解析、
   缓存键按后端隔离；
2. 服务层（pet/voice_chime_service.py 的 _TTSWorker / _usable_attempts）：
   请求形状、音频写盘、缺 Key 与接口报错的降级、后备后端接棒；
3. 设置页与凭据：新键写回、edge 行按后端显隐、API Key 只进钥匙串不进 config。

全部用例不打网络、不碰真钥匙串（http_util / tts_secrets 一律 monkeypatch）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPlainTextEdit, QPushButton

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet import tts, tts_secrets  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.voice_chime import (  # noqa: E402
    BACKEND_EDGE,
    BACKEND_MIMO,
    DEFAULT_BACKEND,
    DEFAULT_MIMO_MODEL,
    DEFAULT_MIMO_VOICE,
    MIMO_MODEL_VOICE_DESIGN,
    MIMO_STYLE_MAX_LEN,
    MIMO_VOICE_OPTIONS,
    cache_key,
    clean_backend,
    clean_mimo_model,
    clean_mimo_style,
    clean_mimo_voice,
    default_chime_config,
    mimo_payload,
    normalize_chime_config,
    parse_mimo_audio,
    synth_attempts,
)


def _attempt(provider_id, **overrides):
    """按注册表里的 provider 造一条合成尝试（值来自字段默认值 + 覆盖）。"""
    provider = tts.get(provider_id)
    values = provider.values({})
    values.update(overrides)
    return tts.TtsAttempt(provider=provider_id, values=values, plan=provider.plan(values))


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ============================================================ 纯逻辑


def test_default_backend_is_edge_with_shipped_mimo_voices():
    """默认后端是 edge-tts（2026-10-13 改，原为 mimo：面向公众发包时陌生人没有 API Key）；
    MiMo 仍是可选后端，内置音色表非空且含默认音色。"""
    assert DEFAULT_BACKEND == BACKEND_EDGE
    assert default_chime_config()["voice_chime_tts_backend"] == BACKEND_EDGE
    ids = {value for value, _label in MIMO_VOICE_OPTIONS}
    assert len(ids) == 8, "官方内置音色 8 款（中文 4 + 英文 4）"
    assert DEFAULT_MIMO_VOICE in ids
    assert {"冰糖", "茉莉", "苏打", "白桦"} <= ids, "中文音色必须可选"
    assert {"Mia", "Chloe", "Milo", "Dean"} <= ids, "英文音色必须可选"


def test_cleaners_fall_back_for_garbage_and_tame_style():
    """非法后端/模型/音色回落默认；风格指令去控制字符、压换行、按上限截断。"""
    assert clean_backend("NoSuchBackend") == DEFAULT_BACKEND
    assert clean_backend("EDGE") == BACKEND_EDGE, "大小写不敏感"
    assert clean_mimo_model("gpt-4o-audio") == DEFAULT_MIMO_MODEL
    assert clean_mimo_voice("") == DEFAULT_MIMO_VOICE
    assert clean_mimo_style("轻快\n带点\x07笑意") == "轻快 带点笑意"
    assert len(clean_mimo_style("啊" * (MIMO_STYLE_MAX_LEN + 50))) == MIMO_STYLE_MAX_LEN


def test_synth_attempts_orders_primary_then_edge_fallback():
    """尝试链：主后端在前，回退开关打开时 edge 收尾；关掉回退只剩主后端。"""
    cfg = normalize_chime_config({"voice_chime_tts_backend": BACKEND_MIMO})
    assert [a.provider for a in synth_attempts(cfg)] == [BACKEND_MIMO, BACKEND_EDGE]
    assert [a.ext for a in synth_attempts(cfg)] == ["wav", "mp3"], "扩展名随后端"

    no_fallback = normalize_chime_config({
        "voice_chime_tts_backend": BACKEND_MIMO,
        "voice_chime_tts_fallback": False,
    })
    assert [a.provider for a in synth_attempts(no_fallback)] == [BACKEND_MIMO]

    edge_only = normalize_chime_config({"voice_chime_tts_backend": BACKEND_EDGE})
    assert [a.provider for a in synth_attempts(edge_only)] == [BACKEND_EDGE], (
        "edge 是链路末端，不回退到自己"
    )


def test_cache_key_separates_backends_and_voices_and_keeps_edge_format():
    """缓存键必须按后端与音色隔离；edge 分支保持历史格式（老缓存仍命中）。"""
    text = "现在是上午九点整。加油"

    def cfg_for(backend, **overrides):
        raw = {
            "voice_chime_tts_backend": backend,
            "voice_chime_voice": "zh-CN-XiaoxiaoNeural",
            "voice_chime_mimo_voice": "冰糖",
            "voice_chime_mimo_style": "",
            **overrides,
        }
        return normalize_chime_config(raw)

    edge_cfg = cfg_for(BACKEND_EDGE)
    mimo_cfg = cfg_for(BACKEND_MIMO)
    assert cache_key(text, edge_cfg, BACKEND_EDGE) != cache_key(
        text, mimo_cfg, BACKEND_MIMO
    ), "换后端不得命中同一缓存"
    assert cache_key(text, mimo_cfg, BACKEND_MIMO) != cache_key(
        text, cfg_for(BACKEND_MIMO, voice_chime_mimo_voice="苏打"), BACKEND_MIMO
    ), "换 MiMo 音色必须换缓存"
    assert cache_key(text, mimo_cfg, BACKEND_MIMO) != cache_key(
        text, cfg_for(BACKEND_MIMO, voice_chime_mimo_style="温柔一点"), BACKEND_MIMO
    ), "换风格指令必须换缓存"

    legacy = hashlib.sha1(f"{text}|zh-CN-XiaoxiaoNeural|+0%|+0Hz".encode("utf-8")).hexdigest()[:16]
    assert cache_key(text, edge_cfg, BACKEND_EDGE) == legacy, "edge 缓存键必须与历史逐字节一致"


def test_mimo_payload_puts_text_in_assistant_and_voice_in_audio():
    """官方约定：待合成文本在 assistant，风格指令在 user（可选），音色在 audio。"""
    attempt = {
        "backend": BACKEND_MIMO,
        "model": DEFAULT_MIMO_MODEL,
        "voice": "茉莉",
        "style": "温柔、语速稍慢",
    }
    payload = mimo_payload("现在是上午九点整", attempt)
    assert payload["model"] == DEFAULT_MIMO_MODEL
    assert payload["stream"] is False
    assert payload["audio"]["voice"] == "茉莉"
    assert payload["audio"]["format"] == "wav"
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][1]["content"] == "现在是上午九点整"
    assert payload["messages"][0]["content"] == "温柔、语速稍慢"

    silent = mimo_payload("现在是上午九点整", {**attempt, "style": ""})
    assert [m["role"] for m in silent["messages"]] == ["assistant"], "无风格指令时不发 user"


def test_voice_design_model_omits_builtin_voice():
    """音色设计模型：user 消息是音色描述，audio 里不得再带内置音色 id。"""
    payload = mimo_payload(
        "现在是上午九点整",
        {
            "backend": BACKEND_MIMO,
            "model": MIMO_MODEL_VOICE_DESIGN,
            "voice": "冰糖",
            "style": "年轻女声，清亮、略带笑意",
        },
    )
    assert payload["model"] == MIMO_MODEL_VOICE_DESIGN
    assert "voice" not in payload["audio"]
    assert payload["messages"][0]["content"] == "年轻女声，清亮、略带笑意"


def test_parse_mimo_audio_decodes_and_explains_failures():
    """响应解析：正常解出字节；异常形态给可读原因，不抛裸 KeyError。"""
    audio = b"RIFF-fake-wav-bytes"
    payload = {
        "choices": [{"message": {"audio": {"data": base64.b64encode(audio).decode()}}}]
    }
    assert parse_mimo_audio(payload) == audio

    with pytest.raises(ValueError, match="invalid api key"):
        parse_mimo_audio({"error": {"message": "invalid api key"}})
    with pytest.raises(ValueError, match="choices"):
        parse_mimo_audio({"usage": {}})
    with pytest.raises(ValueError, match="音频数据"):
        parse_mimo_audio({"choices": [{"message": {}}]})
    with pytest.raises(ValueError, match="解码失败"):
        parse_mimo_audio({"choices": [{"message": {"audio": {"data": "!!!not-base64!!!"}}}]})


def test_normalize_chime_config_carries_backend_fields():
    """normalize 输出必须带全部后端字段（服务层直接消费这些契约键）。"""
    cfg = normalize_chime_config({
        "voice_chime_tts_backend": "mimo",
        "voice_chime_tts_fallback": "false",
        "voice_chime_mimo_model": MIMO_MODEL_VOICE_DESIGN,
        "voice_chime_mimo_voice": "苏打",
        "voice_chime_mimo_style": " 懒散 ",
    })
    assert cfg["backend"] == BACKEND_MIMO
    assert cfg["fallback"] is False, "字符串 'false' 要按假值清洗"
    assert cfg["mimo_model"] == MIMO_MODEL_VOICE_DESIGN
    assert cfg["mimo_voice"] == "苏打"
    assert cfg["mimo_style"] == "懒散"


# ============================================================ 服务层：worker


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mimo_ok_payload(audio: bytes) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"audio": {"data": base64.b64encode(audio).decode()}}}]}
    ).encode("utf-8")


def test_mimo_worker_posts_request_and_writes_audio(tmp_path, monkeypatch):
    """mimo 合成：带 Key 打 POST，把 base64 音频写进缓存文件（扩展名 wav）。"""
    import pet.voice.chime_service as svc_mod

    seen: list[dict] = []

    def fake_urlopen(request, timeout=None, context=None):
        seen.append({
            "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "body": json.loads(request.data.decode("utf-8")),
            "timeout": timeout,
        })
        return _FakeResponse(_mimo_ok_payload(b"WAV-BYTES"))

    monkeypatch.setattr("pet.http_util.urlopen", fake_urlopen)

    out = tmp_path / "chime.wav"
    results: list[tuple] = []
    worker = svc_mod._TTSWorker(
        "现在是上午九点整。",
        (_attempt("mimo", api_key="test-key-123", style="轻快"),),
        (out,),
        lambda path, text, error: results.append((path, text, error)),
    )
    worker.run()

    assert results == [(str(out), "现在是上午九点整。", "")]
    assert out.read_bytes() == b"WAV-BYTES"
    assert len(seen) == 1
    assert seen[0]["url"] == "https://api.xiaomimimo.com/v1/chat/completions"
    assert seen[0]["headers"]["api-key"] == "test-key-123"
    assert seen[0]["headers"]["authorization"] == "Bearer test-key-123"
    assert seen[0]["body"]["messages"][-1]["content"] == "现在是上午九点整。"
    assert seen[0]["timeout"] == tts.mimo.MIMO_TIMEOUT_S


def test_mimo_worker_without_key_reports_key_missing_without_network(tmp_path, monkeypatch):
    """没配 Key：不发请求，回调带回 MIMO_KEY_MISSING（GUI 侧据此给可操作提示）。"""
    import pet.voice.chime_service as svc_mod

    called: list = []
    monkeypatch.setattr("pet.http_util.urlopen", lambda *a, **k: called.append(a))
    out = tmp_path / "chime.wav"
    errors: list[str] = []
    svc_mod._TTSWorker(
        "现在是上午九点整。",
        (_attempt("mimo"),),  # 字段默认值里没有 Key
        (out,),
        lambda path, text, error: errors.append(error),
    ).run()

    assert errors == [svc_mod.MIMO_KEY_MISSING]
    assert called == [], "缺 Key 不该白打一次网络"
    assert not out.exists()


def test_worker_falls_through_to_edge_when_mimo_fails(tmp_path, monkeypatch):
    """主后端失败：后备后端接棒，写它自己的缓存文件（mp3）并回调成功。"""
    import pet.voice.chime_service as svc_mod

    attempts = (_attempt("mimo"), _attempt("edge"))
    paths = (tmp_path / "a.wav", tmp_path / "b.mp3")

    def fake_mimo_synth(text, plan, out_path):
        raise RuntimeError("HTTP 401: invalid api key")

    def fake_edge_synth(text, plan, out_path):
        Path(out_path).write_bytes(b"MP3")

    monkeypatch.setattr(tts.get("mimo"), "synth", fake_mimo_synth)
    monkeypatch.setattr(tts.get("edge"), "synth", fake_edge_synth)

    results: list[tuple] = []
    svc_mod._TTSWorker(
        "现在是上午九点整。", attempts, paths,
        lambda path, text, error: results.append((path, text, error)),
    ).run()

    assert results == [(str(paths[1]), "现在是上午九点整。", "")]
    assert paths[1].read_bytes() == b"MP3"
    assert not paths[0].exists()


def test_worker_reports_primary_error_when_every_backend_fails(tmp_path, monkeypatch):
    """全失败：回调带走主后端错误（用户选的那个最该知道），后备错误用 | 拼接。"""
    import pet.voice.chime_service as svc_mod

    attempts = (_attempt("mimo"), _attempt("edge"))
    paths = (tmp_path / "a.wav", tmp_path / "b.mp3")

    def fake_mimo_synth(text, plan, out_path):
        raise RuntimeError("mimo boom")

    def fake_edge_synth(text, plan, out_path):
        raise RuntimeError("edge boom")

    monkeypatch.setattr(tts.get("mimo"), "synth", fake_mimo_synth)
    monkeypatch.setattr(tts.get("edge"), "synth", fake_edge_synth)

    errors: list[str] = []
    svc_mod._TTSWorker(
        "现在是上午九点整。", attempts, paths,
        lambda path, text, error: errors.append(error),
    ).run()

    assert len(errors) == 1
    assert errors[0].startswith("RuntimeError: mimo boom"), "首先报主后端的错"
    assert "edge boom" in errors[0]


def test_usable_attempts_drops_backends_that_cannot_run(monkeypatch):
    """可用性预判：缺库 / 缺 Key 的后端直接摘掉，链空时上层走「只有气泡」降级。"""
    import pet.voice.chime_service as svc_mod

    service = svc_mod.VoiceChimeService.__new__(svc_mod.VoiceChimeService)
    service._cfg = normalize_chime_config({"voice_chime_tts_backend": BACKEND_MIMO})

    monkeypatch.setattr("pet.tts.edge._EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(svc_mod.tts_secrets, "get", lambda ref: "")
    assert [a.provider for a in service._usable_attempts()] == [BACKEND_EDGE], (
        "没配 Key 时不该白试 MiMo，直接走 edge"
    )

    monkeypatch.setattr(svc_mod.tts_secrets, "get", lambda ref: "k")
    assert [a.provider for a in service._usable_attempts()] == [BACKEND_MIMO, BACKEND_EDGE]

    monkeypatch.setattr("pet.tts.edge._EDGE_TTS_AVAILABLE", False)
    assert [a.provider for a in service._usable_attempts()] == [BACKEND_MIMO]

    monkeypatch.setattr(svc_mod.tts_secrets, "get", lambda ref: "")
    assert service._usable_attempts() == (), "两个都不可用时由调用方给提示"


# ============================================================ 缓存维护


def test_prune_cache_counts_mimo_wav_files(tmp_path):
    """MiMo 的 wav 缓存也必须参与裁剪：只 glob mp3 会让它无上限地涨。"""
    import os

    import pet.voice.chime_service as svc_mod

    service = svc_mod.VoiceChimeService.__new__(svc_mod.VoiceChimeService)
    service._cache_dir = tmp_path
    service._last_prune_at = 0.0
    service.MAX_CACHE_FILES = 2
    for index in range(5):
        path = tmp_path / f"chime-{index}.wav"
        path.write_bytes(b"wav")
        os.utime(path, (1000 + index, 1000 + index))

    service._prune_cache()

    remaining = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert len(remaining) == 2, "超上限必须按 mtime 淘汰最旧的 wav"
    assert "chime-0.wav" not in remaining
    assert "chime-4.wav" in remaining


# ============================================================ 设置页与凭据


def test_settings_page_round_trips_backend_keys(tmp_path):
    """5 个新键走设置页写回 → 落盘 → 重新加载：值必须活下来（白名单门禁）。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    cfg = Config(base=tmp_path)
    page = VoiceChimeSettingsPage(cfg)
    page.backend_select.setCurrentData(BACKEND_EDGE)
    page.fallback_check.setChecked(False)
    page.mimo_model_select.setCurrentData(MIMO_MODEL_VOICE_DESIGN)
    page.mimo_voice_select.setCurrentData("苏打")
    page.mimo_style_edit.setPlainText("沉稳一点，语速正常")  # 多行框：setPlainText
    page.apply_to_config()
    cfg.save()

    reloaded = Config(base=tmp_path)
    assert reloaded.get("voice_chime_tts_backend") == BACKEND_EDGE
    assert reloaded.get("voice_chime_tts_fallback") is False
    assert reloaded.get("voice_chime_mimo_model") == MIMO_MODEL_VOICE_DESIGN
    assert reloaded.get("voice_chime_mimo_voice") == "苏打"
    assert reloaded.get("voice_chime_mimo_style") == "沉稳一点，语速正常"


def test_settings_page_keeps_api_key_out_of_config(tmp_path, monkeypatch):
    """API Key 只进钥匙串：config 里不得出现明文，输入框随即清空。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        tts_secrets, "set",
        lambda ref, value: (stored.__setitem__(ref, value), True)[1],
    )
    monkeypatch.setattr(tts_secrets, "get", lambda ref: stored.get(ref, ""))

    cfg = Config(base=tmp_path)
    page = VoiceChimeSettingsPage(cfg)
    page.mimo_key_edit.setText("sk-mimo-secret")
    page.apply_to_config()

    assert stored == {tts_secrets.MIMO_API_KEY_REF: "sk-mimo-secret"}
    assert page.mimo_key_edit.text() == "", "落库后输入框应清空（不在界面上留明文）"
    assert "sk-mimo-secret" not in json.dumps(cfg.data, ensure_ascii=False), (
        "密钥绝不能进 config.json"
    )


def test_settings_page_empty_key_field_keeps_saved_key(tmp_path, monkeypatch):
    """留空 = 不改动已保存的 Key（每次保存设置不该把 Key 抹掉）。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    calls: list[tuple] = []
    monkeypatch.setattr(tts_secrets, "set", lambda ref, value: calls.append((ref, value)) or True)
    monkeypatch.setattr(tts_secrets, "get", lambda ref: "already-saved")

    page = VoiceChimeSettingsPage(Config(base=tmp_path))
    page.apply_to_config()

    assert calls == [], "留空不得触发写入"


def test_settings_page_clear_button_removes_saved_key(tmp_path, monkeypatch):
    """「清除」按钮：立刻从钥匙串删掉，随后保存也不会把它写回来。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    state = {"ref": tts_secrets.MIMO_API_KEY_REF}
    cleared: list[str] = []
    monkeypatch.setattr(tts_secrets, "clear", lambda ref: cleared.append(ref))
    monkeypatch.setattr(tts_secrets, "get", lambda ref: "")
    monkeypatch.setattr(tts_secrets, "set", lambda ref, value: True)

    page = VoiceChimeSettingsPage(Config(base=tmp_path))
    # 按对象名取「清除」：行里还有「改回默认」按钮，按类型 findChild 会先抓到它。
    clear_btn = page.mimo_key_row.findChild(QPushButton, "ttsSecretClear")
    assert clear_btn is not None, "密钥行必须带清除按钮"
    clear_btn.click()
    page.apply_to_config()

    assert cleared == [state["ref"]]


def test_settings_page_swaps_edge_and_mimo_rows(tmp_path):
    """切后端时参数行跟着换：MiMo 下 edge 的音色/语速/音调隐藏，反之亦然。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    page = VoiceChimeSettingsPage(Config(base=tmp_path))
    page.backend_select.setCurrentData(BACKEND_MIMO)
    assert not page.edge_voice_row.isVisibleTo(page)
    assert not page.edge_rate_row.isVisibleTo(page)
    assert page.mimo_voice_row.isVisibleTo(page)
    assert page.mimo_key_row.isVisibleTo(page)

    # 音色设计模型没有「内置音色」可挑，音色行随之隐藏
    page.mimo_model_select.setCurrentData(MIMO_MODEL_VOICE_DESIGN)
    assert not page.mimo_voice_row.isVisibleTo(page)

    page.mimo_model_select.setCurrentData(DEFAULT_MIMO_MODEL)
    page.backend_select.setCurrentData(BACKEND_EDGE)
    assert page.edge_voice_row.isVisibleTo(page)
    assert page.edge_pitch_row.isVisibleTo(page)
    assert not page.mimo_voice_row.isVisibleTo(page)
    assert not page.mimo_key_row.isVisibleTo(page)


# ============================================================ 接口化：provider 即插即用


class _FakeProvider(tts.TtsProvider):
    """假后端：只声明字段 + 实现 plan/flavor/synth，用来验证「写个 provider 就能插进来」。"""

    id = "fake"
    label = "测试后端（假）"
    is_fallback = True
    availability_code = "fake-unavailable"
    unavailable_message = "测试后端不可用：随便填点什么就好"
    fields = (
        tts.TtsField(
            "voice", "voice_chime_fake_voice", "假音色",
            kind="select", default="甲", options=(("甲", "甲"), ("乙", "乙")),
        ),
        tts.TtsField(
            "style", "voice_chime_fake_style", "假风格指令",
            kind="multiline", default="", min_height=88,
        ),
        tts.TtsField(
            "api_key", "voice_chime_fake_api_key", "假 Key",
            kind="secret", default="", secret_ref="tts/fake",
        ),
    )

    def plan(self, values: dict) -> dict:
        return {"provider": self.id, "voice": values.get("voice"), "ext": "ogg"}

    def flavor(self, values: dict) -> str:
        return f"fake|{values.get('voice')}"

    def synth(self, text: str, plan: dict, out_path) -> None:
        Path(out_path).write_bytes(b"OGG")


@pytest.fixture
def fake_provider():
    provider = _FakeProvider()
    tts.register(provider)
    try:
        yield provider
    finally:
        tts.unregister("fake")


def test_every_registered_provider_satisfies_the_contract():
    """接口契约的机器化守卫：注册表里的每个 provider 都必须满足它。

    新接入一个 TTS 时，这条用例就是「你漏声明了什么」的清单。
    """
    assert tts.provider_ids(), "至少要有内置 provider"
    for provider in tts.providers():
        assert provider.id and provider.label, f"{provider.id}: 缺少 id/label"
        assert provider.fields, f"{provider.id}: 至少要声明一个可配置字段"
        for spec in provider.fields:
            assert spec.kind in tts.FIELD_KINDS, f"{provider.id}.{spec.name}: 未知控件类型"
            assert spec.key.startswith("voice_chime_"), (
                f"{provider.id}.{spec.name}: 字段键必须是登记过的顶层配置键"
            )
            if spec.kind == "secret":
                assert spec.secret_ref, f"{provider.id}.{spec.name}: 密钥字段必须给 secret_ref"
        values = provider.values({})
        plan = provider.plan(values)
        assert plan.get("provider") == provider.id, f"{provider.id}: plan 必须标注 provider"
        assert plan.get("ext"), f"{provider.id}: plan 必须给扩展名（缓存文件名要用）"
        assert provider.flavor(values) == provider.flavor(provider.values({})), (
            f"{provider.id}: flavor 必须只依赖传入的 values（缓存键要稳定）"
        )
        assert isinstance(provider.availability(values), str)


def test_third_party_provider_plugs_into_registry_and_chain(fake_provider):
    """注册一个新 provider 后：后端清单、尝试链、扩展名、缓存键都自动跟上。"""
    assert "fake" in tts.provider_ids()
    assert tts.labels()["fake"] == "测试后端（假）"

    cfg = normalize_chime_config({"voice_chime_tts_backend": "fake"})
    attempts = synth_attempts(cfg)
    assert [a.provider for a in attempts] == ["fake", "edge"], (
        "主后端 + 声明 is_fallback 的 provider（fake 自己不重复进链）"
    )
    assert attempts[0].ext == "ogg", "扩展名由 provider 的 plan 给出"
    assert cache_key("文本", cfg, "fake") != cache_key("文本", cfg, "edge"), (
        "新后端的缓存键与既有后端天然隔离"
    )

    # 关掉回退就只剩它自己；它自报不可用时调用方能看到原因码
    no_fallback = normalize_chime_config({
        "voice_chime_tts_backend": "fake", "voice_chime_tts_fallback": False,
    })
    assert [a.provider for a in synth_attempts(no_fallback)] == ["fake"]


def test_third_party_provider_renders_settings_rows_without_ui_edits(fake_provider, tmp_path, monkeypatch):
    """设置页不为它写一行代码：控件按 fields 生成、显隐按依赖走、读写自动接线。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        tts_secrets, "set", lambda ref, value: stored.__setitem__(ref, value) or True
    )
    monkeypatch.setattr(tts_secrets, "get", lambda ref: stored.get(ref, ""))

    cfg = Config(base=tmp_path)
    page = VoiceChimeSettingsPage(cfg)

    data = {page.backend_select.itemData(i) for i in range(page.backend_select.count())}
    assert "fake" in data, "新后端自动出现在引擎下拉里"

    page.backend_select.setCurrentData("fake")
    fake_voice_row = page._field_rows[("fake", "voice")]
    assert fake_voice_row.isVisibleTo(page), "选中它的后端 → 它的字段行可见"
    assert not page._field_rows[("edge", "voice")].isVisibleTo(page), "别的后端字段行隐藏"

    page._field_widgets["voice_chime_fake_voice"].setCurrentData("乙")
    fake_style = page._field_widgets["voice_chime_fake_style"]
    assert isinstance(fake_style, QPlainTextEdit), "multiline 字段必须渲染成多行框"
    fake_style.setPlainText("整句描述：温柔但有点疲惫，语速慢一点")
    page._secret_edits["voice_chime_fake_api_key"].setText("sk-fake")
    page.apply_to_config()

    assert cfg.get("voice_chime_fake_voice") == "乙", "普通字段按声明写回 config"
    assert cfg.get("voice_chime_fake_style") == "整句描述：温柔但有点疲惫，语速慢一点"
    assert stored == {"tts/fake": "sk-fake"}, "密钥字段按 secret_ref 进钥匙串"
    assert "sk-fake" not in json.dumps(cfg.data, ensure_ascii=False), "密钥不进 config"


def test_mimo_style_row_is_a_multiline_box(tmp_path):
    """MiMo 的「风格指令」要写整句描述：必须是多行框，且长文本能原样存回配置。"""
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    cfg = Config(base=tmp_path)
    page = VoiceChimeSettingsPage(cfg)

    style_widget = page._field_widgets["voice_chime_mimo_style"]
    assert isinstance(style_widget, QPlainTextEdit), "风格指令给单行框等于看不见自己写了什么"
    assert style_widget.minimumHeight() >= 48

    long_text = "脆生生的小女孩童声，可爱但不幼稚，念古诗文的感觉，语速偏慢"
    style_widget.setPlainText(long_text)
    page.apply_to_config()

    assert cfg.get("voice_chime_mimo_style") == long_text, "整句描述必须完整落进配置"
    assert len(long_text) > 20, "这个用例的意义就在于长文本"

    # 运行中的进程按当前配置回读时也拿得到同一句（refresh 路径不截断）
    from pet.voice_chime import normalize_chime_config

    assert normalize_chime_config(cfg)["providers"]["mimo"]["style"] == long_text
