# -*- coding: utf-8 -*-
"""AI 回复朗读流水线：真实 SpeechResult 回调、按宠隔离、预合成有界。"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import pytest

from petcore.contracts.speech import SpeechResult

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
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _result() -> SpeechResult:
    return SpeechResult(path=Path("x.wav"), provider="p", voice="v")


# --------------------------------------------------------------- 拆句与清洗


def test_split_sentences_basic():
    assert reply_voice.split_sentences("第一句。第二句！第三句？") == [
        "第一句。",
        "第二句！",
        "第三句？",
    ]


def test_split_english_sentences():
    assert reply_voice.split_sentences("Hello there. How are you?") == [
        "Hello there.",
        "How are you?",
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


def _player(speaker: _Speaker, **kwargs):
    player = reply_voice.ReplyVoicePlayer(
        speak=speaker.speak,
        precache=speaker.precache,
        enabled=speaker.enabled,
        cancel=speaker.cancel,
        **kwargs,
    )
    return player, speaker


def test_player_speaks_sentences_one_at_a_time_with_speech_result():
    player, speaker = _player(_Speaker())
    assert player.play("第一句。第二句！第三句？") is True
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。"]

    _, on_finished, _ = speaker.jobs[0]
    # 真实 CoreSpeechPort 会带 SpeechResult 回调；零参数实现必须在这里失败。
    on_finished(_result())
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句！"]

    _, on_finished, _ = speaker.jobs[1]
    on_finished(_result())
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句！", "第三句？"]

    _, on_finished, _ = speaker.jobs[2]
    on_finished(_result())
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
    on_finished(_result())
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句。"], (
        "预合成失败静默退化：轮到时照旧在线合成"
    )
    assert "预合成下一句失败" in caplog.text
    assert speaker.precached == [], "失败那次的 precache 没记进正常名单"


def test_player_new_reply_cancels_and_invalidates_old_chain():
    player, speaker = _player(_Speaker())
    player.play("旧回复第一句。旧回复第二句。")
    old_finished, old_error = speaker.jobs[0][1], speaker.jobs[0][2]
    assert speaker.cancels == 1, "每次开念都先打断（第一次是空转，无害）"

    player.play("新回复甲。新回复乙。")
    assert speaker.cancels == 2, "新回复必须打断上一轮还在播的音频"
    old_finished(_result())
    assert [s for s, _f, _e in speaker.jobs] == ["旧回复第一句。", "新回复甲。"], (
        "旧链的 finished 作废，不得再交旧回复的第二句"
    )


def test_player_error_stops_the_chain():
    player, speaker = _player(_Speaker())
    player.play("第一句。第二句。第三句。")
    _, _finished, on_error = speaker.jobs[0]
    on_error(type("Err", (), {"reason": "unavailable"})())
    _, on_finished, _ = speaker.jobs[0]
    on_finished(_result())
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
    on_finished(_result())
    assert len(speaker.jobs) == 1, "开关热关掉后不再送后续句子"


def test_player_stop_clears_pending_and_cancels_audio():
    player, speaker = _player(_Speaker())
    player.play("第一句。第二句。")
    player.stop()
    assert speaker.cancels >= 2, "stop 必须取消正在播的音频"
    _, on_finished, _ = speaker.jobs[0]
    on_finished(_result())
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
    on_finished(_result())
    assert [s for s, _f, _e in speaker.jobs] == ["第一句。", "第二句。"]


# --------------------------------------------------------------- 运行时接线


class _FakeSpeech:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, object, object, int | None]] = []
        self.precached: list[str] = []
        self.cancel_slots: list[int | None] = []
        self.cancel_owners = 0
        self.precache_started = 0
        self.precache_active = 0
        self.precache_max = 0
        self.precache_gate = threading.Event()
        self.precache_gate.set()

    def speak(
        self,
        request,
        *,
        on_finished=None,
        on_error=None,
        owner="",
        slot=None,
        guard=None,
    ):
        self.jobs.append((str(request.text), on_finished, on_error, slot))

    def synthesize(self, request):
        self.precache_started += 1
        self.precache_active += 1
        self.precache_max = max(self.precache_max, self.precache_active)
        try:
            self.precache_gate.wait(timeout=5.0)
            self.precached.append(str(request.text))
        finally:
            self.precache_active -= 1

    def cancel_slot(self, slot):
        self.cancel_slots.append(slot)

    def cancel_owner(self, owner):
        self.cancel_owners += 1


def _runtime_with_speech(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    speech = _FakeSpeech()
    context.ports.speech = speech
    context.ports.config.set("ai_voice_reply", True)
    runtime = ai_mod._create_host(context).runtime_factory(context)
    return runtime, speech


def test_runtime_speak_reply_uses_slot_and_precaches(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    try:
        runtime._speak_reply("你好呀。我很好！", slot=2)
        assert [(s, slot) for s, _f, _e, slot in speech.jobs] == [("你好呀。", 2)]
        assert _wait_until(lambda: speech.precached == ["我很好！"]), (
            "预合成跑在后台线程，轮询等它落账"
        )
        _, on_finished, _, _ = speech.jobs[0]
        on_finished(_result())
        assert [(s, slot) for s, _f, _e, slot in speech.jobs] == [
            ("你好呀。", 2),
            ("我很好！", 2),
        ]
    finally:
        runtime.stop()


def test_two_slots_do_not_cancel_each_other(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    try:
        runtime._speak_reply("一号第一句。一号第二句。", slot=1)
        runtime._speak_reply("二号第一句。二号第二句。", slot=2)
        assert speech.cancel_slots == [1, 2], "每个 slot 只取消自己的音频"
        assert [slot for _s, _f, _e, slot in speech.jobs] == [1, 2]
    finally:
        runtime.stop()


def test_precache_is_bounded_to_one_in_flight(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    speech.precache_gate.clear()
    try:
        runtime._speak_reply("一。二。三。四。五。六。", slot=0)
        assert _wait_until(lambda: speech.precache_started >= 1)
        # 给后续 submit 机会；有界 worker 不应超过一个在飞。
        time.sleep(0.15)
        assert speech.precache_active == 1
        assert speech.precache_max == 1
        speech.precache_gate.set()
        assert _wait_until(lambda: speech.precache_active == 0)
        assert speech.precache_max == 1
    finally:
        speech.precache_gate.set()
        runtime.stop()


def test_runtime_stop_cancels_audio(tmp_path: Path):
    runtime, speech = _runtime_with_speech(tmp_path)
    runtime._speak_reply("第一句。第二句。", slot=3)
    speech.cancel_slots.clear()
    runtime.stop()
    assert speech.cancel_slots == [3], "stop 必须取消该 slot 正在播的音频"


def test_runtime_speak_reply_disabled_without_switch(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    speech = _FakeSpeech()
    context.ports.speech = speech
    runtime = ai_mod._create_host(context).runtime_factory(context)
    try:
        runtime._speak_reply("你好呀。", slot=0)
        assert speech.jobs == [], "开关默认关着不该念"
    finally:
        runtime.stop()


def test_precache_worker_overwrites_pending_and_keeps_one_in_flight():
    gate = threading.Event()
    started: list[str] = []

    def synthesize(sentence: str) -> None:
        started.append(sentence)
        gate.wait(timeout=5.0)

    worker = reply_voice.PrecacheWorker(synthesize)
    try:
        worker.submit("first")
        assert _wait_until(lambda: started == ["first"])
        worker.submit("second")
        worker.submit("third")
        gate.set()
        assert _wait_until(lambda: len(started) >= 2)
        time.sleep(0.05)
        assert started == ["first", "third"], "未开始的 second 必须被 third 覆盖"
    finally:
        worker.close()
