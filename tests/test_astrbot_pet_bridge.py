# -*- coding: utf-8 -*-
"""AstrBot 桥接插件（integrations/astrbot-pet-bridge/main.py）的写方契约。

插件运行在 AstrBot 进程里；这里用最小 ``astrbot.api`` 桩替代宿主，只模拟
「装饰器登记钩子 + Star 基类」两件事，钩子按 AstrBot 文档的真实签名调用。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).parents[1] / "integrations" / "astrbot-pet-bridge"


class _Filter:
    """模拟 ``astrbot.api.event.filter``：每个钩子工厂返回打标记的装饰器。"""

    def __init__(self, names):
        for name in names:
            setattr(self, name, self._factory(name))

    @staticmethod
    def _factory(name):
        def make():
            def deco(fn):
                fn._astrbot_hook = name
                return fn
            return deco
        return make


def _load_bridge(monkeypatch, hook_names=(
    "on_llm_request", "on_llm_response", "after_message_sent",
    "on_using_llm_tool", "on_llm_tool_respond",
)):
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    event_mod.filter = _Filter(hook_names)
    event_mod.AstrMessageEvent = object

    class Star:
        def __init__(self, context):
            self.context = context

    star_mod.Star = Star
    star_mod.Context = object
    registered = []
    star_mod.register = lambda *meta: (lambda cls: registered.append(meta) or cls)
    root = types.ModuleType("astrbot")
    root.api = api
    for name, mod in (("astrbot", root), ("astrbot.api", api),
                      ("astrbot.api.event", event_mod), ("astrbot.api.star", star_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("astrbot_pet_bridge_main", BRIDGE_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._registered = registered
    return module


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _hooks(plugin) -> dict:
    found = {}
    for name in dir(type(plugin)):
        fn = getattr(type(plugin), name)
        hook = getattr(fn, "_astrbot_hook", None)
        if hook:
            found[hook] = getattr(plugin, name)
    return found


class _Tool:
    def __init__(self, name):
        self.name = name


class _Result:
    def __init__(self, is_error):
        self.isError = is_error


class _Resp:
    def __init__(self, role="assistant"):
        self.role = role
        self.completion_text = "PRIVATE REPLY"


def test_metadata_names_the_installed_directory():
    meta = (BRIDGE_DIR / "metadata.yaml").read_text(encoding="utf-8")
    assert "name: astrbot_plugin_dsh_pet_bridge" in meta
    for field in ("desc:", "version:", "author:"):
        assert field in meta


def test_default_events_path_is_under_astrbot_plugin_data(monkeypatch, tmp_path):
    monkeypatch.delenv("DSH_PET_ASTRBOT_EVENTS", raising=False)
    bridge = _load_bridge(monkeypatch)
    plugin_file = tmp_path / "data" / "plugins" / bridge.PLUGIN_NAME / "main.py"
    assert bridge.default_events_path(plugin_file) == (
        tmp_path / "data" / "plugin_data" / bridge.PLUGIN_NAME / "astrbot.jsonl")
    monkeypatch.setenv("DSH_PET_ASTRBOT_EVENTS", str(tmp_path / "x.jsonl"))
    assert bridge.default_events_path(plugin_file) == tmp_path / "x.jsonl"


def test_plugin_registers_documented_hooks_and_writes_minimal_states(monkeypatch, tmp_path):
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_ASTRBOT_EVENTS", str(events))
    bridge = _load_bridge(monkeypatch)
    plugin = bridge.DshPetBridge(context=None)
    hooks = _hooks(plugin)
    assert set(hooks) == {"on_llm_request", "on_using_llm_tool", "on_llm_tool_respond",
                          "on_llm_response", "after_message_sent"}
    assert bridge._registered and bridge._registered[0][0] == bridge.PLUGIN_NAME

    ev = object()

    async def run():
        await hooks["on_llm_request"](ev, types.SimpleNamespace(prompt="PRIVATE PROMPT"))
        await hooks["on_using_llm_tool"](ev, _Tool("Web_Search"), {"query": "PRIVATE ARGS"})
        await hooks["on_llm_tool_respond"](ev, _Tool("web_search"), {"query": "PRIVATE ARGS"}, _Result(False))
        await hooks["on_llm_response"](ev, _Resp())
        await hooks["after_message_sent"](ev)
        await plugin.terminate()

    asyncio.run(run())
    rows = _read(events)
    # 加载 idle → 思考 → 调工具 → 工具返回仍在思考 → 最终响应 idle
    # （after_message_sent 兜底与卸载 idle 都去重不落盘）
    assert [r["state"] for r in rows] == ["idle", "thinking", "working", "thinking", "idle"]
    assert rows[2]["tool"] == "web_search"
    text = events.read_text(encoding="utf-8")
    assert "PRIVATE" not in text
    assert all(set(r) <= {"ts", "agent", "inst", "state", "tool"} for r in rows)
    assert {r["agent"] for r in rows} == {"astrbot"}


def test_old_astrbot_without_tool_hooks_still_loads(monkeypatch, tmp_path):
    monkeypatch.setenv("DSH_PET_ASTRBOT_EVENTS", str(tmp_path / "e.jsonl"))
    bridge = _load_bridge(monkeypatch, hook_names=("on_llm_request", "on_llm_response", "after_message_sent"))
    plugin = bridge.DshPetBridge(context=None)
    assert set(_hooks(plugin)) == {"on_llm_request", "on_llm_response", "after_message_sent"}


def test_concurrent_turns_fold_to_busy_until_last_reply(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    b.begin_turn("a")
    b.begin_turn("b")
    b.llm_response("a", failed=False)   # b 还在思考：不得报 idle
    assert _read(path)[-1]["state"] == "thinking"
    b.message_sent("a")                 # 已收尾回合的兜底：不再动
    b.llm_response("b", failed=False)
    assert _read(path)[-1]["state"] == "idle"
    assert b.in_flight() == 0


def test_final_response_ends_turn_without_message_sent(monkeypatch, tmp_path):
    """流式回复（WebChat 等）不触发 after_message_sent：最终响应本身必须收尾。"""
    bridge = _load_bridge(monkeypatch)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    b.begin_turn("stream")
    b.llm_response("stream", failed=False)
    assert [r["state"] for r in _read(path)] == ["thinking", "idle"]


def test_message_sent_is_fallback_end(monkeypatch, tmp_path):
    """第三方执行器 / 非流式报错没有 on_llm_response：消息发出兜底收尾。"""
    bridge = _load_bridge(monkeypatch)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    b.begin_turn("third-party")
    b.message_sent("third-party")
    assert [r["state"] for r in _read(path)] == ["thinking", "idle"]


def test_command_reply_without_llm_writes_nothing(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    b.message_sent("cmd")
    assert not path.exists()


def test_failures_emit_error(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    b.begin_turn("a")
    b.tool_start("a", "shell")
    b.tool_end("a", failed=True)
    assert [r["state"] for r in _read(path)][-2:] == ["error", "thinking"]
    b.llm_response("a", failed=True)
    assert [r["state"] for r in _read(path)][-2:] == ["error", "idle"]
    b.llm_response("ghost", failed=False)          # 未知回合的成功响应：不写任何东西
    assert [r["state"] for r in _read(path)][-1] == "idle"
    assert b.in_flight() == 0
    assert bridge._response_failed(_Resp("err")) is True
    assert bridge._tool_failed(_Result(True)) is True


def test_silent_turns_expire_but_active_ones_survive(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    now = [1000.0]
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path, clock=lambda: now[0])
    b.begin_turn("lost")                   # 流式 + LLM 报错：没有任何收尾钩子
    b.begin_turn("busy")
    now[0] += bridge.TURN_IDLE_TTL_S - 1
    b.tool_start("busy", "shell")          # 有活动 → 续期
    b.tool_end("busy", failed=False)
    now[0] += 2
    b.sweep()
    assert b.in_flight() == 1              # 只回收静默的那个
    assert _read(path)[-1]["state"] == "thinking"
    now[0] += bridge.TURN_IDLE_TTL_S
    b.sweep()
    assert b.in_flight() == 0
    assert _read(path)[-1]["state"] == "idle"


def test_turn_table_is_bounded(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    b = bridge.PetBridge(tmp_path / "e.jsonl")
    for i in range(bridge.MAX_TURNS + 50):
        b.begin_turn(i)
    assert b.in_flight() == bridge.MAX_TURNS


def test_rotation_keeps_one_generation(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    monkeypatch.setattr(bridge, "ROTATE_BYTES", 64)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    for i in range(6):
        b.begin_turn(i)
        b.message_sent(i)
    assert path.with_name("e.jsonl.1").is_file()
    assert path.stat().st_size < 64 * 2


def test_write_failure_never_raises(monkeypatch, tmp_path):
    bridge = _load_bridge(monkeypatch)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    b = bridge.PetBridge(blocker / "sub" / "e.jsonl")   # 父路径是文件：mkdir 必失败
    b.begin_turn("a")
    b.message_sent("a")


def test_written_records_drive_pet_unified_reader(monkeypatch, tmp_path):
    """写方产物直接喂桌宠读方：六态归一后与写方状态一致。"""
    from pet.agent_link import normalize_event_state

    bridge = _load_bridge(monkeypatch)
    path = tmp_path / "e.jsonl"
    b = bridge.PetBridge(path)
    b.begin_turn("a")
    b.tool_start("a", "x")
    b.llm_response("a", failed=False)
    states = [normalize_event_state(str(r.get("event", "")), r["state"]) for r in _read(path)]
    assert states == ["thinking", "working", "idle"]


@pytest.mark.parametrize("name", ["main.py", "metadata.yaml", "README.md"])
def test_bundle_files_present(name):
    assert (BRIDGE_DIR / name).is_file()
