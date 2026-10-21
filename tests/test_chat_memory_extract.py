# -*- coding: utf-8 -*-
"""记忆档 B：回复后自动提炼（静默）测试。

覆盖任务规格：
- ops 解析：合法 JSON、混杂文本中的 JSON、坏 JSON 按无操作、未知 op 忽略；
- 合并：ADD 去重 hits、UPDATE 命中/未命中、FORGET 命中/未命中、source=auto 标记；
- 护栏：短文本跳过、30s 限频、两开关各自关闭不发、chat 模块未激活不发；
- 静默：提炼异常不抛出、无气泡/系统通知（与关闭基线逐调用对比）；
- 异步：finished 后对话路径不被提炼阻塞（慢 fake provider 断言回复先完成）。
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from pet.chat.memory import ChatMemory, memory_path
from pet.chat.memory_extract import (
    EXTRACT_INTERVAL_SECONDS,
    parse_ops,
    schedule_extraction,
)


def _qapp():
    app = QApplication.instance()
    return app or QApplication([])


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    from pet.chat import memory_extract

    memory_extract._last_run.clear()
    yield
    memory_extract._last_run.clear()


class _RecordingProvider:
    """记录每次调用；stream 产出固定文本（提炼 drain 路径）。"""

    def __init__(self, reply: str = "[]"):
        self.reply = reply
        self.stream_calls: list = []
        self.complete_calls: list = []

    def stream(self, messages, config, cancel, **_kw):
        self.stream_calls.append(messages)
        yield self.reply

    def _complete_json(self, payload, config, _holder):
        self.complete_calls.append(payload)
        return {"content": self.reply}


def _config(tmp_path, **overrides):
    from pet.config import Config

    config = Config(tmp_path)
    # 调度类用例测的是每轮语义：默认把频率闸放到 1（产品默认 5 由
    # test_extract_every_five_turns_by_default 与 test_interval_invalid_* 专测）
    overrides.setdefault("chat_memory_extract_interval", 1)
    for key, value in overrides.items():
        config.set(key, value)
    return config


def _join_extract_threads(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threading.enumerate():
        if thread.name == "chat-memory-extract" and thread.is_alive():
            thread.join(max(0.0, deadline - time.monotonic()))


# ---------------------------------------------------------------- ops 解析
def test_parse_ops_valid_json():
    ops = parse_ops('[{"op":"ADD","text":"我不吃辣"},{"op":"FORGET","text":"旧偏好"}]')
    assert ops == [{"op": "ADD", "text": "我不吃辣"}, {"op": "FORGET", "text": "旧偏好"}]


def test_parse_ops_extracts_json_from_prose():
    raw = '好的，整理结果如下：\n[{"op":"ADD","text":"周三有课"}]\n以上。'
    assert parse_ops(raw) == [{"op": "ADD", "text": "周三有课"}]


def test_parse_ops_code_fenced_json():
    raw = '```json\n[{"op":"UPDATE","text":"我不吃辣"}]\n```'
    assert parse_ops(raw) == [{"op": "UPDATE", "text": "我不吃辣"}]


@pytest.mark.parametrize("raw", ["", "not json at all", "[{broken", '{"op":"ADD"}', "null", "[]"])
def test_parse_ops_bad_input_is_noop(raw):
    assert parse_ops(raw) == []


def test_parse_ops_unknown_op_and_malformed_items_ignored():
    raw = (
        '[{"op":"DELETE","text":"x"},{"op":"ADD","text":"有效"},'
        '"not-a-dict",{"op":"ADD","text":"  "},{"op":"ADD"}]'
    )
    assert parse_ops(raw) == [{"op": "ADD", "text": "有效"}]


def test_parse_ops_caps_count_and_text_length():
    from pet.chat.memory_extract import _MAX_OPS, _OP_TEXT_CHARS

    raw = "[" + ",".join('{"op":"ADD","text":"条目%d"}' % i for i in range(_MAX_OPS + 5)) + "]"
    assert len(parse_ops(raw)) == _MAX_OPS
    long_op = parse_ops('[{"op":"ADD","text":"' + "长" * 1000 + '"}]')
    assert len(long_op[0]["text"]) == _OP_TEXT_CHARS


# ---------------------------------------------------------------- 合并（ChatMemory.apply_ops）
def test_apply_ops_add_marks_auto_source(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    assert mem.apply_ops([{"op": "ADD", "text": "我不吃辣"}], source="auto") is True
    entries = mem.load()
    assert [e["text"] for e in entries] == ["我不吃辣"]
    assert entries[0]["source"] == "auto"


def test_apply_ops_add_dedup_bumps_hits_and_keeps_manual_source(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    entry, _ = mem.add("我不吃辣")  # 档 A 手动钉，source=manual
    mem.apply_ops([{"op": "ADD", "text": "我不吃辣"}], source="auto")
    entries = mem.load()
    assert len(entries) == 1
    assert entries[0]["hits"] == entry["hits"] + 1
    assert entries[0]["source"] == "manual"  # 手动钉过的不被自动提炼改写来源


def test_apply_ops_update_hit_reaffirms(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    entry, _ = mem.add("周三有课")
    mem.apply_ops([{"op": "UPDATE", "text": "周三有课"}], source="auto")
    entries = mem.load()
    assert len(entries) == 1
    assert entries[0]["hits"] == entry["hits"] + 1
    assert entries[0]["updated_at"] >= entry["updated_at"]


def test_apply_ops_update_miss_becomes_add(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    mem.apply_ops([{"op": "UPDATE", "text": "新事实"}], source="auto")
    entries = mem.load()
    assert [e["text"] for e in entries] == ["新事实"]
    assert entries[0]["source"] == "auto"


def test_apply_ops_forget_hit_removes_and_miss_ignored(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    mem.add("要忘掉的")
    mem.add("要保留的")
    mem.apply_ops(
        [{"op": "FORGET", "text": "要忘掉的"}, {"op": "FORGET", "text": "不存在的"}],
        source="auto",
    )
    assert [e["text"] for e in mem.load()] == ["要保留的"]


def test_apply_ops_empty_is_noop(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    assert mem.apply_ops([], source="auto") is False
    assert mem.apply_ops([{"op": "ADD", "text": "  "}], source="auto") is False
    assert not (tmp_path / "memory.json").exists()


# ---------------------------------------------------------------- 护栏
def _schedule(tmp_path, config, provider, user_text="我不吃辣，晚上吃什么", reply="吃点清淡的吧"):
    return schedule_extraction(
        config=config,
        memory_path=tmp_path / "memory.json",
        user_text=user_text,
        reply_text=reply,
        provider=provider,
        provider_config=_provider_config(),
    )


def _provider_config():
    from pet.chat.models import ProviderConfig

    return ProviderConfig("test-provider")


@pytest.mark.parametrize("short", ["嗯", "!!!", "😀😀", "好的", "  ", "哈哈！"])
def test_guard_short_user_text_skipped(tmp_path, short):
    provider = _RecordingProvider('[{"op":"ADD","text":"x"}]')
    assert _schedule(tmp_path, _config(tmp_path), provider, user_text=short) is False
    _join_extract_threads()
    assert provider.stream_calls == [] and provider.complete_calls == []


def test_guard_rate_limit(tmp_path, monkeypatch):
    provider = _RecordingProvider('[{"op":"ADD","text":"我不吃辣"}]')
    config = _config(tmp_path)
    assert _schedule(tmp_path, config, provider) is True
    _join_extract_threads()
    assert len(provider.complete_calls) == 1
    # 30 秒内第二次：跳过
    assert _schedule(tmp_path, config, provider) is False
    _join_extract_threads()
    assert len(provider.complete_calls) == 1
    # 拨快 monotonic 超过限频窗口：放行
    from pet.chat import memory_extract

    real = time.monotonic
    monkeypatch.setattr(memory_extract.time, "monotonic",
                        lambda: real() + EXTRACT_INTERVAL_SECONDS + 1)
    assert _schedule(tmp_path, config, provider) is True
    _join_extract_threads()
    assert len(provider.complete_calls) == 2


@pytest.mark.parametrize(
    "overrides",
    [{"chat_memory_enabled": False}, {"chat_memory_auto_extract": False}],
)
def test_guard_either_switch_off_sends_nothing(tmp_path, overrides):
    provider = _RecordingProvider('[{"op":"ADD","text":"我不吃辣"}]')
    assert _schedule(tmp_path, _config(tmp_path, **overrides), provider) is False
    _join_extract_threads()
    assert provider.stream_calls == [] and provider.complete_calls == []


def test_guard_chat_module_inactive_sends_nothing(tmp_path):
    from pet import feature_activation as fa

    provider = _RecordingProvider('[{"op":"ADD","text":"我不吃辣"}]')
    fa.freeze_snapshot_for_tests(["weather", "todo"])  # 不含 chat
    try:
        assert _schedule(tmp_path, _config(tmp_path), provider) is False
    finally:
        fa.reset_snapshot_for_tests()
    _join_extract_threads()
    assert provider.stream_calls == [] and provider.complete_calls == []


def test_extraction_happy_path_writes_auto_entry(tmp_path):
    provider = _RecordingProvider('[{"op":"ADD","text":"我不吃辣"}]')
    assert _schedule(tmp_path, _config(tmp_path), provider) is True
    _join_extract_threads()
    entries = ChatMemory(tmp_path / "memory.json").load()
    assert [e["text"] for e in entries] == ["我不吃辣"]
    assert entries[0]["source"] == "auto"
    # 非流式轻量调用：走 _complete_json，一个请求
    assert len(provider.complete_calls) == 1
    payload = provider.complete_calls[0]
    assert payload["stream"] is False
    rendered = str(payload["messages"])
    assert "我不吃辣，晚上吃什么" in rendered and "吃点清淡的吧" in rendered


# ---------------------------------------------------------------- 静默
def test_extract_exception_never_raises(tmp_path, caplog):
    class _Boom:
        def _complete_json(self, payload, config, _holder):
            raise RuntimeError("provider 炸了")

        def stream(self, messages, config, cancel, **_kw):
            raise RuntimeError("provider 炸了")
            yield  # pragma: no cover

    with caplog.at_level("ERROR"):
        assert _schedule(tmp_path, _config(tmp_path), _Boom()) is True
    _join_extract_threads()
    assert not (tmp_path / "memory.json").exists()
    assert any("记忆提炼失败" in rec.message for rec in caplog.records) or any(
        "记忆提炼" in rec.message for rec in caplog.records
    )


def test_service_finished_hook_exception_does_not_propagate():
    from pet.chat.service import ChatService

    _qapp()
    service = ChatService()
    seen = []
    service.finished.connect(lambda rid, text: seen.append((rid, text)))
    service.memory_extract_hook = lambda *a: 1 / 0
    service._request_id = "r1"
    service._extract_user_text = "我不吃辣"
    service._extract_config = object()
    service._finished("r1", "回复")  # hook 炸穿也必须照常发出 finished
    assert seen == [("r1", "回复")]


# ---------------------------------------------------------------- 异步 / 端到端（真实 finished 路径）
class _ScriptedProvider:
    """主回复秒回；提炼调用阻塞在事件上，用于断言对话路径不被提炼拖住。"""

    def __init__(self, ops_json: str = '[{"op":"ADD","text":"我不吃辣"}]'):
        self.ops_json = ops_json
        self.extract_started = threading.Event()
        self.extract_release = threading.Event()

    def stream(self, messages, config, cancel, **_kw):
        system = str(messages[0].get("content", "")) if messages else ""
        if system.startswith("你是记忆整理助手"):
            self.extract_started.set()
            self.extract_release.wait(5)
            yield self.ops_json
        else:
            yield "那今晚吃点清淡的吧。"


def _pump_until(app, predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("pump 超时：条件未达成")
        time.sleep(0.005)


def _modern_window(tmp_path, pet=None, notifier=None):
    from pet.config import Config
    from pet.chat.widgets import ChatWindow

    _qapp()
    cfg = Config(tmp_path)
    cfg.set("chat_memory_extract_interval", 1)
    return ChatWindow(cfg, "shenshen", pet_window=pet, notifier=notifier)


def _reply_landed(window) -> bool:
    """回复路径彻底完成的观测点：打字机排空、助手消息已落会话。"""
    return bool(window.session.messages and window.session.messages[-1].role == "assistant")


def test_finished_path_not_blocked_by_extraction(tmp_path):
    app = _qapp()
    scripted = _ScriptedProvider()
    window = _modern_window(tmp_path)
    try:
        window.service.provider = scripted
        window.input.setPlainText("我不吃辣，晚上吃什么")
        window.send_message()
        # 等提炼线程真的卡住（说明 finished 已投递、hook 已调度）
        _pump_until(app, scripted.extract_started.is_set)
        # 此刻提炼还堵着：但回复路径早已完成（不在忙态、助手消息已落会话）
        _pump_until(app, lambda: _reply_landed(window))
        assert window.service.busy is False
        assert window.session.messages[-1].content == "那今晚吃点清淡的吧。"
        assert not (window.store.root / "shenshen" / "memory.json").exists()
        # 放行提炼 → 落 auto 条目
        scripted.extract_release.set()
        _join_extract_threads()
        entries = ChatMemory(window.store.root / "shenshen" / "memory.json").load()
        assert [e["text"] for e in entries] == ["我不吃辣"]
        assert entries[0]["source"] == "auto"
    finally:
        scripted.extract_release.set()
        window.close()
        window.deleteLater()


class _RecordingPet:
    def __init__(self):
        self.calls: list = []

    def set_chat_status(self, state, text=""):
        self.calls.append(("set_chat_status", state, text))

    def show_bubble(self, *args, **kwargs):
        self.calls.append(("show_bubble", args, kwargs))


def test_extraction_is_silent_no_bubble_no_notify(tmp_path):
    """静默硬要求：开启提炼的完整一轮与关闭基线相比，宠物气泡/系统通知零新增。"""
    app = _qapp()

    def run_once(enabled: bool):
        pet = _RecordingPet()
        notices = []
        window = _modern_window(tmp_path / ("on" if enabled else "off"),
                                pet=pet, notifier=lambda *a, **k: notices.append(a))
        scripted = _ScriptedProvider()
        scripted.extract_release.set()  # 提炼不阻塞，直接完成
        try:
            window.config.set("chat_memory_auto_extract", enabled)
            window.service.provider = scripted
            window.input.setPlainText("我不吃辣，晚上吃什么")
            window.send_message()
            # 统一排空协议：两趟都跑到「回复彻底落完 + 提炼线程收口 + 队列冲净」
            _pump_until(app, lambda: _reply_landed(window))
            if enabled:
                _pump_until(app, scripted.extract_started.is_set)
            _join_extract_threads()
            for _ in range(10):
                app.processEvents()
                time.sleep(0.002)
        finally:
            window.close()
            window.deleteLater()
        return pet.calls, notices

    baseline_pet, baseline_notices = run_once(False)
    enabled_pet, enabled_notices = run_once(True)
    assert enabled_pet == baseline_pet  # 宠物侧调用序列与基线逐一致
    assert enabled_notices == baseline_notices
    assert not [c for c in enabled_pet if c[0] == "show_bubble"]


# ---------------------------------------------------------------- 提炼频率（每 N 轮）
def _schedule_turns(config, path, *, user="我不吃辣，晚上吃什么好", reply="试试清汤锅"):
    from pet.chat.memory_extract import schedule_extraction

    return schedule_extraction(
        config=config, memory_path=path, user_text=user, reply_text=reply,
        provider=None, provider_config=None)



@pytest.fixture(autouse=True)
def _reset_turn_counters():
    from pet.chat import memory_extract as me

    with me._last_run_lock:
        me._turn_count.clear()
        me._last_run.clear()
        me._pending_turns.clear()
    yield
    with me._last_run_lock:
        me._turn_count.clear()
        me._last_run.clear()
        me._pending_turns.clear()


def test_extract_every_five_turns_by_default(tmp_path, monkeypatch):
    from pet.chat import memory_extract as me

    monkeypatch.setattr(me.time, "sleep", lambda *_: None)
    fired = []
    monkeypatch.setattr(me.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: fired.append(1)})())
    cfg = _config(tmp_path, chat_memory_extract_interval=5)
    path = tmp_path / "memory.json"
    # 默认 5：前 4 轮不安排，第 5 轮安排；第 6-9 不安排，第 10 再安排
    for i in range(1, 11):
        me._last_run.clear()  # 清掉 30s 限频只看轮次闸
        got = _schedule_turns(cfg, path)
        assert got is (i % 5 == 0), f"第 {i} 轮"
    assert len(fired) == 2


def test_extract_every_turn_when_interval_is_one(tmp_path, monkeypatch):
    from pet.chat import memory_extract as me

    monkeypatch.setattr(me.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: None})())
    cfg = _config(tmp_path, chat_memory_extract_interval=1)
    me._last_run.clear()
    assert _schedule_turns(cfg, tmp_path / "memory.json") is True


def test_interval_invalid_falls_back_to_default(tmp_path):
    from pet.chat.memory_extract import _extract_every_turns

    assert _extract_every_turns(_config(tmp_path / "c", chat_memory_extract_interval="abc")) == 5
    assert _extract_every_turns(_config(tmp_path / "a", chat_memory_extract_interval=0)) == 1
    assert _extract_every_turns(_config(tmp_path / "b", chat_memory_extract_interval=99)) == 20


def test_turns_before_the_gate_are_buffered_not_dropped(tmp_path, monkeypatch):
    """轮次闸放行前的轮次不丢：interval=2 时，第 1 轮的内容必须出现在第 2 轮
    触发的提炼入参里（修复前只提当轮、前 N-1 轮被静默丢弃——盲审应修 #2）。"""
    from pet.chat import memory_extract as me

    captured = []
    def fake_thread(**kw):
        captured.append(kw["args"])
        return type("T", (), {"start": lambda self: None})()
    monkeypatch.setattr(me.threading, "Thread", fake_thread)
    cfg = _config(tmp_path, chat_memory_extract_interval=2)
    path = tmp_path / "memory.json"
    me._last_run.clear()
    assert _schedule_turns(cfg, path, user="我叫张三", reply="你好张三") is False
    me._last_run.clear()
    assert _schedule_turns(cfg, path, user="我不吃辣", reply="记下啦") is True
    assert len(captured) == 1
    _path, turns, _provider, _provider_config = captured[0]
    assert [u for u, _r in turns] == ["我叫张三", "我不吃辣"]
    transcript = me._dialogue_transcript(turns)
    assert "我叫张三" in transcript and "我不吃辣" in transcript


def test_transcript_total_budget_keeps_newest(tmp_path):
    """拼接整体超预算时左截留最新（老内容先丢）。"""
    from pet.chat import memory_extract as me

    turns = [(f"用户{i}" + "x" * 900, f"回复{i}" + "y" * 900) for i in range(5)]
    text = me._dialogue_transcript(turns)
    assert len(text) <= me._DIALOGUE_TOTAL_CHARS
    assert "用户4" in text and "回复4" in text  # 最新的一定在
