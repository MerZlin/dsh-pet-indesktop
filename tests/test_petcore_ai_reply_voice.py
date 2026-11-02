# -*- coding: utf-8 -*-
"""AI 回复朗读流水线（petcore 移植）：拆句、滴灌顺序、预合成前锋、新覆盖旧。"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import test_petcore_ai_mod as ai_mod

reply_voice = None


def setup_module():
    global reply_voice
    reply_voice = _load_reply_voice()


def _load_reply_voice():
    import importlib.util
    import sys

    path = ai_mod.MOD_DIR / "host" / "reply_voice.py"
    spec = importlib.util.spec_from_file_location("ai_chat_reply_voice", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _wait_until(predicate, budget: float = 5.0) -> bool:
    """轮询等待状态收敛（宽预算，不固定 sleep 猜时序）。"""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


# --------------------------------------------------------------- 拆句与清洗


def test_split_sentences_basic():
    assert reply_voice.split_sentences("第一句。第二句！第三句？") == [
        "第一句。",
        "第二句！",
        "第三句？",
    ]


def test_clean_for_speech_strips_markdown():
    text = (
        "# 标题\n\n**加粗**和`代码`，见[链接文字](http://x)。\n"
        "- 列表项\n\n```\nprint(1)\n```\n\n正文。"
    )
    cleaned = reply_voice.clean_for_speech(text)
    assert "print(1)" not in cleaned
    assert "代码略" in cleaned
    assert "http://x" not in cleaned
    assert "链接文字" in cleaned
    assert not cleaned.startswith("#")
    assert "正文。" in cleaned


def test_hard_split_uses_nearest_soft_break():
    piece = "这是一句特别长的话，" + "内容" * 40 + "，然后继续。"
    parts = reply_voice.split_sentences(piece, max_chars=80)
    assert len(parts) >= 2
    assert all(len(part) <= 80 for part in parts)


def test_punctuation_only_fragment_dropped():
    assert reply_voice.split_sentences("只有标点。\n---\n。。") == ["只有标点。"]


def test_empty_and_whitespace():
    assert reply_voice.split_sentences("") == []
    assert reply_voice.split_sentences("   \n  ") == []


# --------------------------------------------------------------- 播放器（纯逻辑）


class _Speaker:
    def __init__(self, *, enabled: bool = True):
        self.enabled_flag = enabled
        self.jobs: list[tuple[str, object, object]] = []
        self.precached: list[str] = []
        self.cancels = 0

    def speak(self, sentence, on_finished, on_error):
        self.jobs.append((sentence, on_finished, on_error))

    def precache(self, sentence):
        self.precached.append(sentence)

    def cancel(self):
        self.cancels += 1

    def enabled(self):
        return self.enabled_flag


def _player(speaker: _Speaker, **kwargs) -> tuple:
    player = reply_voice.ReplyVoicePlayer(
        speak=speaker.speak,
        precache=speaker.precache,
        enabled=speaker.enabled,
        cancel=speaker.cancel,
        **kwargs,
    )
    return player, speaker


def test_player_speaks_sentences_one_at_a_time():
    player, speaker = _player(_Speaker())
    assert player.play("第一句。第二句！第三句？") is True
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。"]

    _, on_finished, _ = speaker.jobs[0]
    on_finished()
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句！"]

    _, on_finished, _ = speaker.jobs[1]
    on_finished()
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句！", "第三句？"]

    _, on_finished, _ = speaker.jobs[2]
    on_finished()
    assert len(speaker.jobs) == 3, "念完就不再送"


def test_player_precaches_next_sentence():
    player, speaker = _player(_Speaker())
    player.play("第一句。第二句。")
    assert speaker.precached == ["第二句。"], "送出第一句时预合成第二句"


def test_player_single_sentence_no_precache():
    player, speaker = _player(_Speaker())
    player.play("就一句。")
    assert speaker.precached == [], "只有一句时没有下一句可预合成"


def test_player_precache_failure_silent_and_chain_continues(caplog):
    speaker = _Speaker()

    def failing(sentence):
        raise RuntimeError("backend down")

    speaker.precache = failing
    player, speaker = _player(speaker)
    with caplog.at_level(logging.DEBUG):
        player.play("第一句。第二句。")
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。"], "预合成失败不挡送句"
    _, on_finished, _ = speaker.jobs[0]
    on_finished()
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句。"], (
        "预合成失败静默退化：轮到时照旧在线合成")
    assert "预合成下一句失败" in caplog.text
    assert speaker.precached == [], "失败那次的 precache 没记进正常名单"


def test_player_new_reply_cancels_and_invalidates_old_chain():
    player, speaker = _player(_Speaker())
    player.play("旧回复第一句。旧回复第二句。")
    old_finished, old_error = speaker.jobs[0][1], speaker.jobs[0][2]
    assert speaker.cancels == 1, "每次开念都先打断（第一次是空转，无害）"

    player.play("新回复甲。新回复乙。")
    assert speaker.cancels == 2, "新回复必须打断上一轮还在播的音频"
    old_finished()
    assert [s for s, _f, _e in speaker.jobs] == ["旧回复第一句。", "新回复甲。"], (
        "旧链的 finished 作废，不得再交旧回复的第二句")


def test_player_error_stops_the_chain():
    player, speaker = _player(_Speaker())
    player.play("第一句。第二句。第三句。")
    _, _finished, on_error = speaker.jobs[0]
    on_error(SimpleNamespace(reason="unavailable"))
    _, on_finished, _ = speaker.jobs[0]
    on_finished()
    assert len(speaker.jobs) == 1, "一句失败就停整条链，不再送后续句子"


def test_player_disabled_does_nothing():
    speaker = _Speaker(enabled=False)
    player, speaker = _player(speaker)
    assert player.play("随便说点什么。") is False
    assert speaker.jobs == []


def test_player_toggle_off_mid_chain_stops():
    speaker = _Speaker()
    player, speaker = _player(speaker)
    player.play("第一句。第二句。")
    speaker.enabled_flag = False
    _, on_finished, _ = speaker.jobs[0]
    on_finished()
    assert len(speaker.jobs) == 1, "开关热关掉后不再送后续句子"


def test_player_stop_clears_pending():
    player, speaker = _player(_Speaker())
    player.play("第一句。第二句。")
    player.stop()
    _, on_finished, _ = speaker.jobs[0]
    on_finished()
    assert len(speaker.jobs) == 1
    assert player.pending == 0


def test_player_speak_exception_stops_chain(caplog):
    speaker = _Speaker()

    def broken(sentence, on_finished, on_error):
        raise RuntimeError("channel broken")

    speaker.speak = broken
    player, speaker = _player(speaker)
    with caplog.at_level(logging.DEBUG):
        player.play("第一句。第二句。")
    assert "送朗读句失败" in caplog.text
    assert player.pending == 0


def test_player_without_precache_still_works():
    speaker = _Speaker()
    player = reply_voice.ReplyVoicePlayer(
        speak=speaker.speak,
        enabled=speaker.enabled,
        cancel=None,
        precache=None,
    )
    assert player.play("第一句。第二句。") is True
    _, on_finished, _ = speaker.jobs[0]
    on_finished()
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句。"]


# --------------------------------------------------------------- 运行时接线


class _FakeSpeech:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, object, object]] = []
        self.precached: list[str] = []
        self.cancels = 0

    def speak(self, request, *, on_finished=None, on_error=None, owner="", slot=None, guard=None):
        self.jobs.append((str(request.text), on_finished, on_error))

    def synthesize(self, request):
        self.precached.append(str(request.text))

    def cancel_owner(self, owner):
        self.cancels += 1


def _runtime_with_speech(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    speech = _FakeSpeech()
    context.ports.speech = speech
    context.ports.config.set("ai_voice_reply", True)
    runtime = ai_mod._create_host(context).runtime_factory(context)
    return runtime, speech


def test_runtime_speak_reply_splits_and_precaches(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    runtime._speak_reply("你好呀。我很好！")
    assert [s for s, _f, _e in speech.jobs] == ["你好呀。"]
    assert _wait_until(lambda: speech.precached == ["我很好！"]), (
        "预合成跑在后台线程，轮询等它落账")
    _, on_finished, _ = speech.jobs[0]
    on_finished()
    assert [s for s, _f, _e in speech.jobs] == ["你好呀。", "我很好！"]


def test_runtime_speak_reply_disabled_without_switch(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    speech = _FakeSpeech()
    context.ports.speech = speech
    runtime = ai_mod._create_host(context).runtime_factory(context)
    runtime._speak_reply("你好呀。")
    assert speech.jobs == [], "开关默认关着不该念"


def test_runtime_new_reply_cancels_previous(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    runtime._speak_reply("旧回复一。旧回复二。")
    assert speech.cancels == 1, "开念先打断（第一次空转）"
    runtime._speak_reply("新回复一。")
    assert speech.cancels == 2, "新回复打断上一轮"


def test_runtime_stop_stops_voice_player(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    runtime._speak_reply("第一句。第二句。")
    player = runtime._reply_voice
    runtime.stop()
    _, on_finished, _ = speech.jobs[0]
    on_finished()
    assert len(speech.jobs) == 1, "stop 后旧链回调作废"
    assert player.pending == 0
