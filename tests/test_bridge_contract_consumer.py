# -*- coding: utf-8 -*-
"""Regression coverage for the Pet side of the DSH bridge contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.agent_link import DshMonitor
from pet.agent_link import AgentLinkManager
from pet.bridge_contract import (
    BRIDGE_CAPABILITIES,
    BRIDGE_EVENT_INVENTORY,
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_VERSION,
)
from pet.config import Config


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _record(event: str = "AgentStatus", **extra) -> dict:
    return {
        "event": event,
        "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
        "bridgeVersion": BRIDGE_VERSION,
        **extra,
    }


def _append(path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_incompatible_dsh_record_is_stopped_before_all_consumers(tmp_path):
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    states = []
    activities = []
    normalized = []
    incompatible = []
    monitor.raw_record.connect(lambda *args: raw.append(args))
    monitor.state_changed.connect(lambda *args: states.append(args))
    monitor.activity.connect(lambda *args: activities.append(args))
    monitor.normalized_event.connect(lambda event: normalized.append(event))
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record(bridgeProtocolVersion=99, state="working", tool="bash"))
    monitor._poll()
    assert not raw
    assert not states
    assert not activities
    assert not normalized
    assert len(incompatible) == 1
    assert incompatible[0][1]["receivedProtocolVersion"] == 99
    monitor.stop()


def test_valid_dsh_record_keeps_existing_state_activity_and_raw_paths(tmp_path):
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    states = []
    activities = []
    monitor.raw_record.connect(lambda *args: raw.append(args))
    monitor.state_changed.connect(lambda *args: states.append(args))
    monitor.activity.connect(lambda *args: activities.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record(state="working", tool="bash"))
    monitor._poll()
    assert raw and raw[0][1]["event"] == "AgentStatus"
    assert states == [("dsh", "working")]
    assert activities == [("dsh", "bash")]
    monitor.stop()


def test_every_declared_bridge_event_is_consumed_without_unknown_fallback(tmp_path):
    """Producer inventory entries all reach the Pet raw consumer as known events."""
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    unknown = []
    monitor.raw_record.connect(lambda _agent, record: raw.append(record["event"]))
    monitor.unknown_bridge_event.connect(lambda *args: unknown.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    for event in sorted(BRIDGE_EVENT_INVENTORY):
        extra = {}
        if event == "bridge/hello":
            extra = {
                "capabilities": sorted(BRIDGE_CAPABILITIES),
                "emittedEvents": sorted(BRIDGE_EVENT_INVENTORY),
            }
        _append(monitor.events_file, _record(event, **extra))
    monitor._poll()
    assert set(raw) == set(BRIDGE_EVENT_INVENTORY)
    assert unknown == []
    monitor.stop()


def test_pet_control_audit_records_are_not_mistaken_for_bridge_output(tmp_path):
    """dsh*.jsonl includes Pet control logs, which have a separate local contract."""
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    incompatible = []
    unknown = []
    monitor.raw_record.connect(lambda _agent, record: raw.append(record["event"]))
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.unknown_bridge_event.connect(lambda *args: unknown.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    for event in ("pet/control-clicked", "pet/control-queued"):
        _append(monitor.events_file, {"agent": "pet", "event": event})
    monitor._poll()
    assert raw == ["pet/control-clicked", "pet/control-queued"]
    assert incompatible == []
    assert unknown == []
    monitor.stop()


def test_hello_requires_the_exact_event_inventory(tmp_path):
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    incompatible = []
    raw = []
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.raw_record.connect(lambda *args: raw.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record("bridge/hello", emittedEvents=["AgentStatus"]))
    monitor._poll()
    assert len(incompatible) == 1
    assert not raw
    _append(
        monitor.events_file,
        _record(
            "bridge/hello",
            capabilities=sorted(BRIDGE_CAPABILITIES),
            emittedEvents=sorted(BRIDGE_EVENT_INVENTORY),
        ),
    )
    monitor._poll()
    assert len(incompatible) == 1
    assert raw and raw[0][1]["event"] == "bridge/hello"
    monitor.stop()


def _exported_js_strings(source: str, name: str) -> set[str]:
    match = re.search(
        rf"export const {name} = Object\.freeze\(\[(.*?)\]\);", source, re.S,
    )
    assert match is not None, f"missing JavaScript export: {name}"
    return set(re.findall(r'\"([^\"]+)\"', match.group(1)))


def _impl_const_js_strings(source: str, name: str) -> set[str]:
    # impl 层的清单不是 `export const X = Object.freeze(...)`，而是
    # `const X = Object.freeze(...)` + 底部 `export { X }`（壳/实现双副本）。
    # 热重载后新实现若改动契约，Pet 侧会拒绝 hello——但测试必须在事前抓住
    # 双副本漂移（否则壳副本与 Pet 一致、impl 副本却已跑偏，测试全绿）。
    match = re.search(
        rf"const {name} = Object\.freeze\(\[(.*?)\]\);", source, re.S,
    )
    assert match is not None, f"missing impl const: {name}"
    return set(re.findall(r'\"([^\"]+)\"', match.group(1)))


def test_bundled_bridge_and_pet_contracts_are_identical():
    """The two runtimes may not add an event/capability/version independently.

    The bridge is now a stable shell (index.js) plus impl/<version>/index.js.
    The impl layer carries its own copy of the protocol constants and the
    hello record is written by the impl layer, so this test must check BOTH
    the shell export AND the impl-layer copy against Pet.  Only reading the
    shell would leave the runtime-emitting impl copy unverified.
    """
    root = Path(__file__).resolve().parents[1]
    bridge_root = root / "integrations" / "dsh-pet-bridge"
    source = (bridge_root / "index.js").read_text(encoding="utf-8")
    package = json.loads((bridge_root / "package.json").read_text(encoding="utf-8"))

    # 当前激活版本的实现层（与 package.json.version 精确对应，不扫全部历史
    # 版本——与壳 probeDisk 的选中语义一致）。
    impl_dir = bridge_root / "impl" / str(package.get("version", ""))
    impl_source = (impl_dir / "index.js").read_text(encoding="utf-8")

    shell_inventory = _exported_js_strings(source, "BRIDGE_EVENT_INVENTORY")
    impl_inventory = _impl_const_js_strings(impl_source, "BRIDGE_EVENT_INVENTORY")
    assert shell_inventory == impl_inventory == set(BRIDGE_EVENT_INVENTORY), (
        "shell/impl/Pet event inventory drifted: "
        f"shell-only={sorted(shell_inventory - set(BRIDGE_EVENT_INVENTORY))} "
        f"impl-only={sorted(impl_inventory - set(BRIDGE_EVENT_INVENTORY))} "
        f"pet-only={sorted(set(BRIDGE_EVENT_INVENTORY) - shell_inventory)}"
    )
    shell_capabilities = _exported_js_strings(source, "BRIDGE_CAPABILITIES")
    impl_capabilities = _impl_const_js_strings(impl_source, "BRIDGE_CAPABILITIES")
    assert shell_capabilities == impl_capabilities == set(BRIDGE_CAPABILITIES), (
        "shell/impl/Pet capability inventory drifted: "
        f"shell-only={sorted(shell_capabilities - set(BRIDGE_CAPABILITIES))} "
        f"impl-only={sorted(impl_capabilities - set(BRIDGE_CAPABILITIES))} "
        f"pet-only={sorted(set(BRIDGE_CAPABILITIES) - shell_capabilities)}"
    )
    assert package["version"] == BRIDGE_VERSION
    protocol = re.search(r"export const BRIDGE_PROTOCOL_VERSION = (\d+);", source)
    assert protocol is not None
    impl_protocol = re.search(r"const BRIDGE_PROTOCOL_VERSION = (\d+);", impl_source)
    assert impl_protocol is not None
    assert int(protocol.group(1)) == int(impl_protocol.group(1)) == BRIDGE_PROTOCOL_VERSION


def test_hello_requires_the_exact_capability_inventory():
    record = _record(
        "bridge/hello",
        capabilities=["agent-status"],
        emittedEvents=sorted(BRIDGE_EVENT_INVENTORY),
    )
    from pet.bridge_contract import validate_bridge_record

    result = validate_bridge_record(record)
    assert not result
    assert result.reason == "bridge capabilities do not match Pet"


def test_incompatible_bridge_bubble_is_actionable_and_rate_limited(tmp_path):
    _qapp()
    bubbles = []
    clock = [100.0]

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    manager = AgentLinkManager(
        DummyWindow(), Config(base=tmp_path), clock=lambda: clock[0], min_interval=0,
    )
    details = {
        "reason": "unsupported bridge protocol version",
        "receivedProtocolVersion": 99,
        "receivedBridgeVersion": "9.9.9",
        "expectedProtocolVersion": 1,
        "expectedBridgeVersion": BRIDGE_VERSION,
    }
    manager._on_bridge_incompatible("dsh", details)
    manager._on_bridge_incompatible("dsh", details)
    assert len(bubbles) == 1
    assert all(value in bubbles[0] for value in ("99", "1", "9.9.9", BRIDGE_VERSION))
    assert "更新" in bubbles[0] and "重装" in bubbles[0]
    clock[0] += manager._UNKNOWN_BRIDGE_REMIND_COOLDOWN_S + 1
    manager._on_bridge_incompatible("dsh", details)
    assert len(bubbles) == 2
    manager.shutdown()


def test_incompatible_bridge_warning_ignores_report_probability(tmp_path):
    """Protocol incompatibility is required health feedback, not event sampling."""
    _qapp()
    bubbles = []

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    cfg = Config(base=tmp_path)
    agent_cfg = dict(cfg.get("agent_link", {}))
    agent_cfg["report_gates"] = {
        **agent_cfg.get("report_gates", {}),
        "bridge": 0.0,
    }
    cfg.set("agent_link", agent_cfg)
    manager = AgentLinkManager(DummyWindow(), cfg, min_interval=0)
    manager._on_bridge_incompatible(
        "dsh", {"reason": "missing bridge protocol version"},
    )
    assert len(bubbles) == 1
    manager.shutdown()


# ----------------------------------------------------------------------
# Effect coverage: a declared event must actually DO something.
#
# The original inventory test only proved that every declared event reaches the
# raw stream and avoids the unknown-event warning.  That let two classes of
# defect survive a fully green suite:
#   * a producer event with no consumer at all (silently dropped), and
#   * a state-table entry spelled differently from what the bridge emits
#     (llm_error vs llm/error), so the state transition never fired.
# These tests assert the *effect* instead of mere delivery, so either
# regression fails here rather than in production.
# ----------------------------------------------------------------------

def _state_effect(event: str) -> str | None:
    """The DshState a bridge record produces, or None when nothing happens."""
    from pet.dsh_state import map_event_to_state

    return map_event_to_state({"event": event})


def test_declared_events_have_an_effect_consumer():
    """Every declared event must hit a semantic, state, or explicit consumer.

    Delivery is not effect: the sibling inventory test only proves an event
    reaches ``raw_record`` and dodges the unknown-event warning.  Here every
    declared event must additionally reach a *behavioural* consumer, which is
    one of:

    * ``map_event_to_state``            -> a DshState transition
    * ``normalize_event``               -> a semantic event
    * an explicit ``_poll`` signal branch
    * the exploration watchdog classifier

    An event that only satisfies the inventory whitelist is treated as inert,
    because the whitelist is exactly what hid the previous silent drops.
    """
    from pet.agent_event_normalizer import normalize_event
    from pet.agent_event_protocol import parse_agent_event
    from pet.exploration_watchdog import WatchdogClass, classify_event
    from pet import agent_link

    def has_signal_branch(event: str) -> bool:
        """True when _poll forwards this event on a dedicated signal."""
        return event in agent_link._POLL_SIGNAL_EVENTS

    inert: list[str] = []
    for event in sorted(BRIDGE_EVENT_INVENTORY):
        if _state_effect(event) is not None:
            continue
        if has_signal_branch(event):
            continue
        if classify_event({"event": event}) != WatchdogClass.OTHER:
            continue
        try:
            normalized = normalize_event(
                parse_agent_event(
                    {"event": event, "agent": "dsh"},
                    source_hint="dsh",
                    agent_name_hint="dsh",
                )
            )
        except Exception:  # noqa: BLE001
            normalized = None
        if normalized is not None:
            continue
        inert.append(event)

    assert inert == [], (
        "these events are declared in BRIDGE_EVENT_INVENTORY but no Pet "
        f"consumer acts on them (silently dropped): {inert}"
    )


def test_llm_error_event_drives_the_error_state():
    """The bridge emits ``llm_error``; the state table must know that spelling.

    Regression: the table carried ``llm/error`` (slash), which the bridge never
    emits, so an API-level failure produced no error state.
    """
    from pet.dsh_state import DshState

    assert _state_effect("llm_error") == DshState.ERROR
    # The legacy slash spelling stays accepted for older producers.
    assert _state_effect("llm/error") == DshState.ERROR


def test_compaction_and_web_search_events_are_recognised():
    """Compaction and web-search watchdog events must reach a real consumer."""
    from pet.exploration_watchdog import WatchdogClass, classify_event

    assert _state_effect("context_compacted") is not None
    # Search begin/end must classify as web search for the watchdog.
    assert classify_event({"event": "web_search_begin"}) == WatchdogClass.SEARCH_WEB
    assert classify_event({"event": "web_search_end"}) == WatchdogClass.SEARCH_WEB


def test_control_and_diagnostic_events_reach_a_consumer(tmp_path):
    """Bridge control/diagnostic records must not be silently discarded."""
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    incompatible = []
    monitor.raw_record.connect(lambda _agent, record: raw.append(record["event"]))
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    for event in ("bridge/control-received", "bridge/diagnostic"):
        _append(monitor.events_file, _record(event))
    monitor._poll()
    assert set(raw) == {"bridge/control-received", "bridge/diagnostic"}
    assert incompatible == []
    monitor.stop()


def test_first_install_success_prompts_restart(tmp_path):
    """首次安装成功必须提示重启 DSH（运行中的 DSH 不自动加载新装插件）。

    安装只写盘（profile 依赖 + bundles），DshMonitor.install_bridge 返回
    first_install 标志：首次安装提示重启（仅这一次）；已安装刷新不提示
    （壳已在跑、热重载接手）。提示必须直接 show_bubble（不走 persona 模板，
    模板会覆盖掉它）。"""
    _qapp()
    bubbles = []

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    cfg = Config(base=tmp_path)
    manager = AgentLinkManager(DummyWindow(), cfg, min_interval=0)
    # 首次安装（first_install=True）：必须提示动作（重启 或 启动，取决于 DSH 是否在跑）
    manager._on_install_finished("dsh", True, "已安装到 1 个 dsh 实例", first_install=True)
    assert bubbles, "首次安装成功必须弹气泡"
    assert any("请重启" in b or "请启动" in b for b in bubbles), \
        f"首次安装气泡必须提示重启/启动 DSH: {bubbles}"
    # 已安装刷新（first_install=False）：不得提示重启
    bubbles.clear()
    manager._on_install_finished("dsh", True, "已更新", first_install=False)
    assert bubbles, "刷新成功也应弹气泡"
    assert not any("请重启" in b or "请启动" in b for b in bubbles), \
        f"刷新成功不得提示重启/启动（热重载已接手）: {bubbles}"
    manager.shutdown()
