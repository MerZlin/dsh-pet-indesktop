# -*- coding: utf-8 -*-
"""edge 合成健壮性回归：音色下架兜底、连发重试、空音频不算缓存。

事故（2026-09-22 实机排查，用户报「edge 语音合成失败」）叠加了两件事：

1. 微软会**下架音色**——老清单 31 款里有 10 款（晓涵/晓辰/晓梦/晓墨/晓秋/晓睿/
   晓双/晓萱/晓颜/晓悠）已不在在线音色表里；配上它们只会得到 ``NoAudioReceived``，
   用户看到的现象是「没声音」而不是报错。她配置里用的正是晓涵。
2. **连发请求会偶发** ``NoAudioReceived``（同一批 en-US 音色间隔 6 秒逐个重试全部
   成功，连发则整批失败），失败的合成会在缓存里留下 0 字节 mp3。

本文件锁住四条对策：内置清单不含已知下线音色、``resolve_voice`` 兜底并说明、合成
重试与退默认音色、缓存命中要求非空。全部用例不打网络（edge_tts 用替身）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pet.voice_chime_service as svc_mod  # noqa: E402
from pet.voice_chime import (  # noqa: E402
    DEFAULT_VOICE,
    DEPRECATED_VOICE_LABELS,
    VOICE_OPTIONS,
    voice_label,
)

#: 2026-09-22 实测已从微软在线音色表消失的那 10 款
KNOWN_DEAD_VOICES = tuple(DEPRECATED_VOICE_LABELS)


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    """每个用例都从干净的缓存出发（在线表 + 一次性提示）。"""
    monkeypatch.setattr(svc_mod, "_live_voices", None)
    monkeypatch.setattr(svc_mod, "_live_voices_at", 0.0)
    monkeypatch.setattr(svc_mod, "_VOICE_SUBSTITUTION_NOTICES", set())
    monkeypatch.setattr(svc_mod, "EDGE_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", True)
    yield


class _FakeCommunicate:
    """``edge_tts.Communicate`` 替身：按脚本决定第几次成功、产出多长。"""

    calls: list[str] = []
    plan: list[str] = ["ok"]
    payload = b"MP3-BYTES"

    def __init__(self, text, voice, rate=None, pitch=None):
        self.voice = voice

    async def save(self, path):
        index = len(_FakeCommunicate.calls)
        _FakeCommunicate.calls.append(self.voice)
        outcome = _FakeCommunicate.plan[min(index, len(_FakeCommunicate.plan) - 1)]
        if outcome == "raise":
            raise RuntimeError("NoAudioReceived: No audio was received.")
        if outcome == "empty":
            Path(path).write_bytes(b"")
            return
        Path(path).write_bytes(_FakeCommunicate.payload)


def _install_fake_edge_tts(monkeypatch, *, live_voices=None):
    module = types.ModuleType("edge_tts")

    async def list_voices():
        return [{"ShortName": name} for name in (live_voices or [])]

    module.list_voices = list_voices
    module.Communicate = _FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    _FakeCommunicate.calls = []
    _FakeCommunicate.plan = ["ok"]
    return module


# ============================================================ 清单与在线表


def test_bundled_voice_list_has_no_deprecated_voice():
    """内置清单不得再含已下线音色（否则用户一选就是「没声音」）。"""
    bundled = {value for value, _label in VOICE_OPTIONS}
    still_there = [v for v in KNOWN_DEAD_VOICES if v in bundled]
    assert still_there == [], f"这些音色已被微软下架，不该留在清单里：{still_there}"
    assert DEFAULT_VOICE in bundled
    assert len(bundled) >= 20, "在线仍有 30+ 款中英音色，清单不该被削得太狠"


def test_voice_label_prefers_bundled_then_deprecated_map():
    """提示里说人话：「晓涵」而不是 zh-CN-XiaohanNeural。"""
    assert voice_label("zh-CN-XiaoxiaoNeural") == "晓晓"
    assert voice_label("zh-CN-XiaohanNeural") == "晓涵"
    assert voice_label("some-unknown-voice") == "some-unknown-voice"


def test_known_voices_uses_live_table_then_bundled(monkeypatch):
    """拿不到在线表时退回内置清单；拉到之后以在线表为准。"""
    assert svc_mod.known_voices() == {value for value, _label in VOICE_OPTIONS}

    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural", "en-US-AriaNeural"])
    refreshed = svc_mod.refresh_voice_list(force=True)
    assert refreshed == frozenset({"zh-CN-XiaoxiaoNeural", "en-US-AriaNeural"})
    assert svc_mod.known_voices() == refreshed


def test_refresh_voice_list_failure_keeps_previous(monkeypatch):
    """在线表拉取失败不该让报时跟着失败：返回 None 并保留旧值。"""
    _install_fake_edge_tts(monkeypatch, live_voices=["zh-CN-XiaoxiaoNeural"])

    async def boom():
        raise RuntimeError("网络不通")

    sys.modules["edge_tts"].list_voices = boom
    assert svc_mod.refresh_voice_list(force=True) is None
    assert svc_mod.known_voices() == {value for value, _label in VOICE_OPTIONS}


# ============================================================ resolve_voice


def test_resolve_voice_swaps_deprecated_voice_and_explains(monkeypatch):
    """配置音色已下线 → 换默认音色，并给出「去哪儿换」的说明（不联网）。"""
    _install_fake_edge_tts(monkeypatch, live_voices=[DEFAULT_VOICE, "zh-CN-XiaoyiNeural"])

    voice, note = svc_mod.resolve_voice("zh-CN-XiaohanNeural")

    assert voice == DEFAULT_VOICE
    assert "晓涵" in note and "晓晓" in note, "提示要说中文名"
    assert "设置" in note, "提示要告诉用户去哪儿改"


def test_resolve_voice_keeps_live_voice(monkeypatch):
    """在线音色原样返回，不产生提示。"""
    _install_fake_edge_tts(monkeypatch, live_voices=[DEFAULT_VOICE, "zh-CN-XiaoyiNeural"])
    assert svc_mod.resolve_voice("zh-CN-XiaoyiNeural") == ("zh-CN-XiaoyiNeural", "")


def test_resolve_voice_silent_when_edge_unavailable(monkeypatch):
    """edge-tts 本身不可用时不改配置（免得误导用户以为换了音色就行）。"""
    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", False)
    assert svc_mod.resolve_voice("zh-CN-XiaohanNeural") == ("zh-CN-XiaohanNeural", "")


# ============================================================ worker：重试与兜底


def _run_worker(monkeypatch, tmp_path, voice, plan, name="chime.mp3"):
    _FakeCommunicate.plan = plan
    results: list[tuple] = []
    worker = svc_mod._TTSWorker(
        "现在是上午九点整。",
        voice,
        "+0%",
        "+0Hz",
        tmp_path / name,
        lambda path, text, error: results.append((path, text, error)),
    )
    worker.run()
    return results


def test_worker_retries_then_succeeds(monkeypatch, tmp_path):
    """连发被偶发拒绝：重试后成功（0 字节 mp3 的直接对策）。"""
    _install_fake_edge_tts(monkeypatch, live_voices=[DEFAULT_VOICE])

    results = _run_worker(monkeypatch, tmp_path, DEFAULT_VOICE, ["raise", "ok"])

    assert results[0][2] == "", "第二次应当成功"
    assert len(_FakeCommunicate.calls) == 2
    assert (tmp_path / "chime.mp3").stat().st_size > 0


def test_worker_falls_back_to_default_voice(monkeypatch, tmp_path):
    """主音色怎么试都不行：退到默认音色，用户仍然听得到报时。"""
    _install_fake_edge_tts(monkeypatch, live_voices=[DEFAULT_VOICE, "zh-CN-XiaoyiNeural"])

    results = _run_worker(monkeypatch, tmp_path, "zh-CN-XiaoyiNeural", ["raise", "raise", "ok"])

    assert results[0][2] == ""
    assert _FakeCommunicate.calls[-1] == DEFAULT_VOICE


def test_worker_treats_empty_audio_as_failure(monkeypatch, tmp_path):
    """服务端「成功」但没给音频（0 字节）也要算失败，且不留空文件。"""
    _install_fake_edge_tts(monkeypatch, live_voices=[DEFAULT_VOICE])

    results = _run_worker(monkeypatch, tmp_path, DEFAULT_VOICE, ["empty"])

    assert "空文件" in results[0][2] or "合成失败" in results[0][2]
    assert not (tmp_path / "chime.mp3").exists(), "失败产物必须清掉"


def test_worker_reports_missing_edge_tts(monkeypatch, tmp_path):
    """真导入失败（半装/被禁用）：依旧回既有 EDGE_TTS_MISSING 降级码。"""
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    results = _run_worker(monkeypatch, tmp_path, DEFAULT_VOICE, ["ok"])

    assert results[0][2] == svc_mod.EDGE_TTS_MISSING


# ============================================================ 缓存命中要求非空


def test_cache_hit_requires_non_empty_file(tmp_path):
    """0 字节/半截文件不算命中：否则播出一声静音且永远不再重合成。"""
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    good = tmp_path / "good.mp3"
    good.write_bytes(b"MP3")

    assert svc_mod._cache_hit(good) is True
    assert svc_mod._cache_hit(empty) is False
    assert svc_mod._cache_hit(tmp_path / "missing.mp3") is False


def test_voice_substitution_notice_is_announced_once(monkeypatch):
    """同一条提示每个进程只弹一次（不刷屏），但每次都写日志。"""
    _install_fake_edge_tts(monkeypatch, live_voices=[DEFAULT_VOICE])
    service = svc_mod.VoiceChimeService.__new__(svc_mod.VoiceChimeService)
    bubbles: list[str] = []
    service._bubble = lambda text: bubbles.append(text)

    voice, note = svc_mod.resolve_voice("zh-CN-XiaohanNeural")
    service._announce_voice_substitution(note)
    service._announce_voice_substitution(note)

    assert voice == DEFAULT_VOICE
    assert len(bubbles) == 1
