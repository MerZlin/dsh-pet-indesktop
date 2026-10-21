# -*- coding: utf-8 -*-
"""edge-tts provider 的健壮性回归（2026-09-22 实机排查后补）。

事故背景：用户报「edge 语音合成失败」。实机查下来是两件事叠加：

1. 微软**下架了音色**——老清单 31 款里有 10 款（晓涵/晓辰/晓梦/晓墨/晓秋/晓睿/晓双/
   晓萱/晓颜/晓悠）已不在在线音色表里，配上它们只会得到 ``NoAudioReceived``，用户
   看到的是「声音没了」；她配置里用的正是晓涵。
2. **连发请求会偶发** ``NoAudioReceived``（同一批 en-US 音色间隔 6 秒重试全部成功，
   连发则整批失败），于是缓存里堆了一串 0 字节 mp3。

本文件锁住四条对策：内置清单不再含已知下线音色、preflight 换音色并说明、synth 重试、
synth 主音色失败退默认音色。全部用例不打网络（edge_tts 用替身）。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet.tts import edge as edge_mod  # noqa: E402

#: 2026-09-22 实测已从微软在线音色表消失的 10 款（我方老清单里的）
KNOWN_DEAD_VOICES = (
    "zh-CN-XiaochenNeural",
    "zh-CN-XiaohanNeural",
    "zh-CN-XiaomengNeural",
    "zh-CN-XiaomoNeural",
    "zh-CN-XiaoqiuNeural",
    "zh-CN-XiaoruiNeural",
    "zh-CN-XiaoshuangNeural",
    "zh-CN-XiaoxuanNeural",
    "zh-CN-XiaoyanNeural",
    "zh-CN-XiaoyouNeural",
)


@pytest.fixture(autouse=True)
def _clean_voice_cache():
    edge_mod.reset_voice_cache()
    yield
    edge_mod.reset_voice_cache()


class _FakeCommunicate:
    """edge_tts.Communicate 替身：按脚本决定第几次成功、产出多长。"""

    calls: list[tuple[str, str]] = []
    #: 每次调用后的结局：'ok' 写字节；'empty' 写 0 字节；'raise' 抛 NoAudioReceived
    plan: list[str] = ["ok"]
    payload = b"MP3-BYTES"

    def __init__(self, text, voice, rate=None, pitch=None, proxy=None):
        self.voice = voice

    async def save(self, path):
        index = len(_FakeCommunicate.calls)
        _FakeCommunicate.calls.append((self.voice, str(path)))
        outcome = _FakeCommunicate.plan[min(index, len(_FakeCommunicate.plan) - 1)]
        if outcome == "raise":
            raise RuntimeError("NoAudioReceived: No audio was received.")
        if outcome == "empty":
            Path(path).write_bytes(b"")
            return
        Path(path).write_bytes(_FakeCommunicate.payload)


def _install_fake_edge_tts(monkeypatch, *, live_voices=None):
    """装一个假的 edge_tts 模块（不联网），并让 provider 认为它可用。"""
    module = types.ModuleType("edge_tts")

    async def list_voices(proxy=None):
        return [{"ShortName": name} for name in (live_voices or [])]

    module.list_voices = list_voices
    module.Communicate = _FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    monkeypatch.setattr(edge_mod, "_EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(edge_mod, "EDGE_RETRY_DELAY_S", 0.0)  # 测试别真睡
    _FakeCommunicate.calls = []
    _FakeCommunicate.plan = ["ok"]
    return module


# ============================================================ 清单与校验集


def test_bundled_voice_list_drops_dead_voices():
    """内置清单不得再包含已知下线音色（否则用户一选就是静音）。"""
    bundled = edge_mod.bundled_voices()
    still_there = [v for v in KNOWN_DEAD_VOICES if v in bundled]
    assert still_there == [], f"这些音色已下线，不该留在内置清单里：{still_there}"
    assert edge_mod.DEFAULT_VOICE in bundled, "默认音色必须在清单里"
    assert len(bundled) >= 20, "清单不该被削得太狠（在线仍有 36 款中英音色）"


def test_known_voices_prefers_live_cache_else_bundled(monkeypatch):
    """拿不到在线表时退回内置清单；拉到之后以在线表为准。"""
    assert edge_mod.known_voices() == edge_mod.bundled_voices()

    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural", "en-US-AriaNeural"])
    refreshed = edge_mod.refresh_voice_list(force=True)
    assert refreshed == frozenset({"zh-CN-XiaoxiaoNeural", "en-US-AriaNeural"})
    assert edge_mod.known_voices() == refreshed


def test_refresh_voice_list_failure_keeps_previous(monkeypatch):
    """在线表拉取失败不该让报时跟着失败：保留旧值并返回 None。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])

    async def boom():
        raise RuntimeError("网络不通")

    sys.modules["edge_tts"].list_voices = boom
    assert edge_mod.refresh_voice_list(force=True) is None
    assert edge_mod.known_voices() == edge_mod.bundled_voices(), "失败后仍用内置清单"


# ============================================================ preflight


def test_voice_label_prefers_bundled_then_deprecated_map():
    """提示里说人话：「晓涵」而不是 zh-CN-XiaohanNeural（随 provider 一起搬来）。"""
    assert edge_mod.voice_label("zh-CN-XiaoxiaoNeural") == "晓晓"
    assert edge_mod.voice_label("zh-CN-XiaohanNeural") == "晓涵"
    assert edge_mod.voice_label("some-unknown-voice") == "some-unknown-voice"


def test_preflight_swaps_removed_voice_and_explains(monkeypatch):
    """配置音色已下线：换默认音色并给出「换哪一个」的说明（GUI 线程不联网）。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural"])

    values, note = edge_mod.PROVIDER.preflight({"voice": "zh-CN-XiaohanNeural", "rate": 0, "pitch": 29})

    assert values["voice"] == edge_mod.DEFAULT_VOICE
    assert values["pitch"] == 29, "只改音色，别的参数不动"
    assert "晓涵" in note and "晓晓" in note, "提示里要说人话（中文名），不是音色 id"
    assert "音色" in note and "设置" in note, "提示要告诉用户去哪里换"


def test_preflight_keeps_live_voice_and_silent_when_unavailable(monkeypatch):
    """在线音色原样返回；edge-tts 本身不可用时不改配置（免得误导）。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])
    values, note = edge_mod.PROVIDER.preflight({"voice": "zh-CN-XiaoxiaoNeural"})
    assert values["voice"] == "zh-CN-XiaoxiaoNeural" and note == ""

    monkeypatch.setattr(edge_mod, "_EDGE_TTS_AVAILABLE", False)
    values, note = edge_mod.PROVIDER.preflight({"voice": "zh-CN-XiaohanNeural"})
    assert values["voice"] == "zh-CN-XiaohanNeural" and note == ""


# ============================================================ synth 重试与兜底


def test_synth_retries_then_succeeds(monkeypatch, tmp_path):
    """连发被偶发拒绝：重试后成功（这是 0 字节 mp3 的直接对策）。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])
    _FakeCommunicate.plan = ["raise", "ok"]
    out = tmp_path / "chime.mp3"

    edge_mod.PROVIDER.synth("现在是上午九点整。", {"voice": "zh-CN-XiaoxiaoNeural",
                                                 "rate": "+0%", "pitch": "+0Hz"}, out)

    assert len(_FakeCommunicate.calls) == 2, "第一次失败后必须重试"
    assert out.read_bytes() == _FakeCommunicate.payload


def test_synth_falls_back_to_default_voice(monkeypatch, tmp_path):
    """主音色怎么试都不行：退到默认音色，产出的音频照常可用。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural"])
    # 前两次（主音色）失败，之后（默认音色）成功
    _FakeCommunicate.plan = ["raise", "raise", "ok"]
    out = tmp_path / "chime.mp3"

    edge_mod.PROVIDER.synth("现在是上午九点整。", {"voice": "zh-CN-XiaoyiNeural",
                                                 "rate": "+0%", "pitch": "+0Hz"}, out)

    voices = [voice for voice, _path in _FakeCommunicate.calls]
    assert voices[:2] == ["zh-CN-XiaoyiNeural", "zh-CN-XiaoyiNeural"]
    assert voices[-1] == edge_mod.DEFAULT_VOICE, "最后必须退到默认音色"
    assert out.stat().st_size > 0


def test_synth_treats_empty_file_as_failure(monkeypatch, tmp_path):
    """服务端「成功」但没给音频（0 字节）也要算失败，不能留下空缓存。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])
    _FakeCommunicate.plan = ["empty"]
    out = tmp_path / "chime.mp3"

    with pytest.raises(RuntimeError, match="合成失败"):
        edge_mod.PROVIDER.synth("现在是上午九点整。",
                                {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz"}, out)

    assert not out.exists(), "失败产物必须清掉"


# ============================================================ 失败诊断（脱敏）


class _CredentialedFailure(Exception):
    """真实形态的失败：aiohttp 的异常文本里就带着签名 URL 与代理凭据。"""

    status = 403


@pytest.mark.parametrize(
    "error,expected",
    [
        (type("NoAudioReceived", (Exception,), {})("PRIVATE_TOKEN"), "未返回音频"),
        (_CredentialedFailure(
            "wss://host/?token=PRIVATE_TOKEN http://user:PRIVATE_PASSWORD@proxy"), "403"),
    ],
    ids=["no-audio", "http-403"],
)
def test_synth_failure_is_classified_and_credential_free(
    monkeypatch, tmp_path, caplog, error, expected
):
    """失败诊断（287 的口径搬进 provider）：用户可见的错误串只给分类后的人话，
    细节进日志且只记脱敏行——**异常原文里的签名 URL、令牌与代理凭据两边都不许出现**。

    同时守住「失败产物必须清掉」：合成是先建文件再写，半截文件留在缓存目录会被
    「文件非空即命中」判成已有缓存，播出来是一声静音且再也不重合成。
    """
    module = _install_fake_edge_tts(monkeypatch, live_voices=[edge_mod.DEFAULT_VOICE])
    monkeypatch.setattr("urllib.request.getproxies", dict)  # 直连（route=direct）

    class FailingCommunicate:
        def __init__(self, text, voice, **kwargs):
            pass

        async def save(self, path):
            Path(path).write_bytes(b"\x00half")  # 半截产物
            raise error

    module.Communicate = FailingCommunicate
    caplog.set_level(logging.DEBUG)
    out = tmp_path / "chime.mp3"

    with pytest.raises(RuntimeError) as excinfo:
        edge_mod.PROVIDER.synth("现在是上午九点整。",
                                {"voice": edge_mod.DEFAULT_VOICE,
                                 "rate": "+0%", "pitch": "+0Hz"}, out)

    message = str(excinfo.value)
    assert expected in message, f"用户看到的是分类结果，不是异常原文：{message}"
    assert "PRIVATE_TOKEN" not in message + caplog.text
    assert "PRIVATE_PASSWORD" not in message + caplog.text
    assert "version=" in caplog.text and "route=" in caplog.text, (
        f"日志要有可排查的脱敏细节（类型/版本/HTTP 状态/走没走代理）：{caplog.text}"
    )
    assert not out.exists(), "失败产物必须清掉"


def test_synth_refreshes_live_voice_list_in_background(monkeypatch, tmp_path):
    """合成线程顺手刷新在线音色表（GUI 线程不做这事）。"""
    calls: list[int] = []
    module = _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])

    async def counting_list_voices(proxy=None):
        calls.append(1)
        return [{"ShortName": "zh-CN-XiaoxiaoNeural"}]

    module.list_voices = counting_list_voices
    out = tmp_path / "chime.mp3"
    edge_mod.PROVIDER.synth("现在是上午九点整。",
                            {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz"}, out)

    assert calls == [1], "合成时应刷新一次在线音色表"
    assert asyncio.get_event_loop_policy() is not None  # 用例本身不依赖事件循环状态


# ============================================================ 服务层：提示只弹一次


def test_service_announces_voice_substitution_once(monkeypatch):
    """服务层把 preflight 的说明转达给用户，且同一进程内只弹一次。"""
    from pet.voice_chime import normalize_chime_config
    from pet.voice.chime_service import VoiceChimeService

    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])
    import pet.voice.chime_service as svc_mod

    monkeypatch.setattr(svc_mod, "_ANNOUNCED_NOTES", set())

    service = VoiceChimeService.__new__(VoiceChimeService)
    service._cfg = normalize_chime_config({
        "voice_chime_tts_backend": "edge",
        "voice_chime_voice": "zh-CN-XiaohanNeural",
    })
    service._secret_values = lambda: {}
    bubbles: list[str] = []
    service._bubble = lambda note: bubbles.append(note)

    first = service._usable_attempts()
    second = service._usable_attempts()

    assert first and first[0].values["voice"] == edge_mod.DEFAULT_VOICE, (
        "已下线的音色必须被换成默认音色（否则用户只会听到静音）"
    )
    assert first[0].plan["voice"] == edge_mod.DEFAULT_VOICE, "plan 也要跟着换"
    assert len(bubbles) == 1, "同一条提示每个进程只弹一次"
    assert "晓涵" in bubbles[0]
    assert second[0].values["voice"] == edge_mod.DEFAULT_VOICE
