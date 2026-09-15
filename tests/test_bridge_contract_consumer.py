# -*- coding: utf-8 -*-
"""Regression coverage for the Pet side of the DSH bridge contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.agent_link import DshMonitor
from pet.agent_link import AgentLinkManager
from pet import dsh_control
from pet.harness_launcher import parse_dsh_server_pids
from pet.bridge_contract import (
    BRIDGE_CAPABILITIES,
    BRIDGE_DIR_ENV,
    BRIDGE_EVENT_INVENTORY,
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_VERSION,
    resolve_bridge_dir,
)
from pet.config import Config
from pet.dsh_state import DshStateTracker


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


def test_error_diagnostic_reaches_the_bridge_diagnostic_signal(tmp_path):
    """severity=error 的 bridge/diagnostic 必须走专用信号（不能只落 raw_record）。

    生产来源：壳拒绝一次"契约不符的热重载"——它保留旧实现（正确），但用户侧
    症状只是"桌宠没反应"；没有这条通道，原因在 Pet 侧完全不可见。"""
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    seen = []
    monitor.bridge_diagnostic.connect(lambda *args: seen.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record(
        "bridge/diagnostic",
        severity="error",
        reason="bridge-contract-mismatch",
        message="impl 9.9.9 的桥接契约与壳不一致……请重启 DSH 以更新桥接协议。",
    ))
    monitor._poll()
    assert seen, "error 级诊断必须转发到 bridge_diagnostic 信号"
    assert seen[0][1]["reason"] == "bridge-contract-mismatch"
    monitor.stop()


def test_error_diagnostic_bubbles_once_while_plain_diagnostic_stays_quiet(tmp_path, caplog):
    """error 级诊断弹气泡并要求重启 DSH；常规诊断只留日志、不打扰用户。"""
    import logging

    _qapp()
    bubbles = []

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    cfg = Config(base=tmp_path)
    manager = AgentLinkManager(DummyWindow(), cfg, min_interval=0)
    try:
        with caplog.at_level(logging.DEBUG, logger="dsh-pet-standalone"):
            # 常规诊断（桥进程启动写一次的环境信息，无 severity）：不弹气泡
            manager._on_bridge_diagnostic("dsh", {"event": "bridge/diagnostic", "bridgeDir": "X"})
            assert bubbles == [], f"常规诊断不得弹气泡: {bubbles}"

            detail = {
                "event": "bridge/diagnostic",
                "severity": "error",
                "reason": "bridge-contract-mismatch",
                "message": "impl 9.9.9 的桥接契约与壳不一致，已拒绝激活；请重启 DSH 以更新桥接协议。",
            }
            manager._on_bridge_diagnostic("dsh", detail)
            assert any("重启 DSH" in text for text in bubbles), f"error 诊断必须弹气泡: {bubbles}"
            assert any(
                "severity=error" in record.getMessage() and "bridge-contract-mismatch" in record.getMessage()
                for record in caplog.records
            ), [record.getMessage() for record in caplog.records]

            # 冷却窗口内不重复弹（诊断可能成串上报）
            bubbles.clear()
            manager._on_bridge_diagnostic("dsh", detail)
            assert bubbles == [], "同一 agent 冷却窗口内不得重复弹诊断气泡"
    finally:
        manager.shutdown()


def test_bridge_incompatible_leaves_the_reason_in_the_log(tmp_path, caplog):
    """契约 fail-closed 必须在日志里留下明确原因（否则只剩"桌宠没反应"）。"""
    import logging

    _qapp()
    bubbles = []

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    cfg = Config(base=tmp_path)
    manager = AgentLinkManager(DummyWindow(), cfg, min_interval=0)
    try:
        with caplog.at_level(logging.WARNING, logger="dsh-pet-standalone"):
            manager._on_bridge_incompatible("dsh", {
                "reason": "unsupported bridge protocol version",
                "receivedBridgeVersion": "0.4.0",
                "receivedProtocolVersion": 2,
                "expectedBridgeVersion": BRIDGE_VERSION,
                "expectedProtocolVersion": BRIDGE_PROTOCOL_VERSION,
                "receivedEventInventory": [],
            })
        assert bubbles, "不兼容仍应弹气泡（既有行为不变）"
        messages = [record.getMessage() for record in caplog.records]
        assert any("桥接契约校验失败" in message for message in messages), messages
        assert any("unsupported bridge protocol version" in message for message in messages), messages
        assert any("0.4.0" in message and "协议=2" in message for message in messages), messages
    finally:
        manager.shutdown()


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


def test_bridge_dir_override_wins_over_platform_defaults(tmp_path, monkeypatch):
    """桥目录的显式覆盖入口：三平台同一口径，优先于 config_dir 派生与平台默认。

    为什么需要它（本次 CI 红的根因）：桥目录是插件与桌宠的**跨侧约定**，平台默认
    路径三平台各不相同；没有正式入口时"把数据根指到别处"（CI 数据隔离／多实例／
    便携部署）在 POSIX 上只能靠改 HOME 之类的环境技巧——那是拿平台细节绕过产品
    行为，测试也就只在部分平台成立（实测：只设 APPDATA 的隔离在 macOS 上等于没设，
    记录被写进 runner 真实家目录、断言却去临时目录读 → ENOENT）。
    """
    target = tmp_path / "custom-bridge"
    # 与插件（impl/*/index.js 的 BRIDGE_DIR_ENV）同名是契约的一部分：
    # 两边必须读同一个变量名，否则各自写/读不同的目录而没人报错。
    assert BRIDGE_DIR_ENV == "DSH_PET_BRIDGE_DIR"
    monkeypatch.setenv(BRIDGE_DIR_ENV, str(target))
    assert resolve_bridge_dir() == target
    assert resolve_bridge_dir(tmp_path / "config") == target
    assert Path(dsh_control._bridge_dir()) == target


def test_bridge_dir_without_override_keeps_the_existing_rules(tmp_path, monkeypatch):
    """不设覆盖时，桥目录必须与既有规则逐字一致（默认行为零变化）。"""
    monkeypatch.delenv(BRIDGE_DIR_ENV, raising=False)
    config_dir = tmp_path / "config"
    assert resolve_bridge_dir(config_dir) == tmp_path / "dsh-pet-bridge"
    # 不给 config_dir（dsh_control 的用法）→ 平台默认，且与共享解析同源
    assert Path(dsh_control._bridge_dir()) == resolve_bridge_dir()


def test_bridge_consumers_read_the_overridden_dir(tmp_path, monkeypatch):
    """覆盖生效时，桌宠两个消费端必须都去同一个目录读——否则插件写一个、桌宠读另一个。"""
    target = tmp_path / "bridge"
    monkeypatch.setenv(BRIDGE_DIR_ENV, str(target))
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    try:
        assert Path(monitor.events_dir) == target
    finally:
        monitor.stop()
    tracker = DshStateTracker(tmp_path / "config", parent=None, scan_interval=0.0)
    try:
        assert Path(tracker._bridge_dir) == target
    finally:
        tracker.stop()


# ---------------------------------------------------------------- 桥接健康自检
# 2026-09-14 事故：打包失败清空了插件目录 → 运行中的 DSH 启动时解析不到插件就跳过 →
# 桌宠再也收不到桥接事件，而桌宠只在"首次安装"时提示过重启，对这种状态毫无感知。
# 下面这组用例把"健康自检能识别哪些状态"钉死。

def _write_profile(profile_dir: Path, *, declared: bool, bundled: bool, target: Path | None) -> None:
    """造一个假 profile：可控制"声明依赖 / 在 bundles 里 / link 目标指向哪"。"""
    profile_dir.mkdir(parents=True, exist_ok=True)
    deps = {"@dsh-pet/bridge": f"link:{target}"} if declared else {}
    bundles = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]
    if bundled:
        bundles.append("@dsh-pet/bridge")
    (profile_dir / "package.json").write_text(
        json.dumps({"name": f"profile-{profile_dir.name}", "dependencies": deps,
                    "dsh": {"profile": {"bundles": bundles}}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_dsh_server_pid_parser_only_picks_the_target_profile(tmp_path):
    """进程清单解析：只认"跑目标 profile 的 DSH"，别的 node 进程一律不算。"""
    listing = "\n".join([
        '4242 "E:\\nodejs\\node.exe" "E:\\nodejs\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js" web',
        '5150 /usr/bin/node /usr/lib/node_modules/@deepseek-ai/dsh/lib/bin.js web',
        "6161 /usr/bin/python -m pet",                      # 桌宠自己
        "7171 /usr/bin/node /opt/other/bin.js worker",       # 别的 bin.js
        "8181 /usr/bin/node /usr/lib/node_modules/@deepseek-ai/dsh/lib/bin.js other-profile",
        "not-a-pid /usr/bin/node x/bin.js web",              # pid 非法
    ])
    assert parse_dsh_server_pids(listing, ["web"]) == [4242, 5150]
    assert parse_dsh_server_pids(listing, ["other-profile"]) == [8181]
    assert parse_dsh_server_pids(listing, []) == []


def test_bridge_health_flags_a_dsh_that_never_loaded_the_plugin(tmp_path):
    """核心缺口：已装好、已启用，但运行中的 DSH 没写出自己那份实例文件 → 要提示重启。"""
    plugin = tmp_path / "bridge-plugin"
    plugin.mkdir()
    profile = tmp_path / "profiles" / "web"
    _write_profile(profile, declared=True, bundled=True, target=plugin)
    # 监视器的桥目录 = resolve_bridge_dir(config_dir) = config_dir.parent/"dsh-pet-bridge"
    bridge_dir = tmp_path / "dsh-pet-bridge"
    bridge_dir.mkdir(parents=True)
    monitor = DshMonitor("dsh", tmp_path / "config")
    try:
        ok, reason, message = monitor.bridge_health(profile_dirs=[profile], pids=[4242])
        assert ok is False
        assert reason == "not-loaded"
        assert "重启" in message and "4242" in message, message
    finally:
        monitor.stop()


def test_bridge_health_passes_once_the_dsh_wrote_its_instance_file(tmp_path):
    """同一条路径，出现 `dsh-<pid>.jsonl` 即视为已加载（不打扰用户）。"""
    plugin = tmp_path / "bridge-plugin"
    plugin.mkdir()
    profile = tmp_path / "profiles" / "web"
    _write_profile(profile, declared=True, bundled=True, target=plugin)
    bridge_dir = tmp_path / "dsh-pet-bridge"
    bridge_dir.mkdir(parents=True)
    (bridge_dir / "dsh-4242.jsonl").write_text('{"event":"bridge/hello"}\n', encoding="utf-8")
    monitor = DshMonitor("dsh", tmp_path / "config")
    try:
        ok, reason, message = monitor.bridge_health(profile_dirs=[profile], pids=[4242])
        assert ok is True, (reason, message)
        assert reason == "" and message == ""
    finally:
        monitor.stop()


def test_bridge_health_names_the_other_three_states(tmp_path):
    """未安装 / 未启用 / link 目标缺失：各自给出可操作结论，且不互相掩盖。"""
    monitor = DshMonitor("dsh", tmp_path / "config")
    try:
        plugin = tmp_path / "bridge-plugin"
        plugin.mkdir()
        # 未安装
        empty_profile = tmp_path / "profiles" / "web"
        empty_profile.mkdir(parents=True)
        (empty_profile / "package.json").write_text('{"dependencies": {}}', encoding="utf-8")
        assert monitor.bridge_health(profile_dirs=[empty_profile], pids=[])[1] == "not-installed"
        # 已安装但不在 bundles（DSH 不会加载）
        unbundled = tmp_path / "profiles2" / "web"
        _write_profile(unbundled, declared=True, bundled=False, target=plugin)
        ok, reason, message = monitor.bridge_health(profile_dirs=[unbundled], pids=[])
        assert ok is False and reason == "disabled" and "启用" in message
        # link 目标不存在
        broken = tmp_path / "profiles3" / "web"
        _write_profile(broken, declared=True, bundled=True, target=tmp_path / "gone")
        ok, reason, message = monitor.bridge_health(profile_dirs=[broken], pids=[])
        assert ok is False and reason == "link-missing" and "缺失" in message
    finally:
        monitor.stop()


def test_manager_bubbles_one_restart_hint_per_reason(tmp_path):
    """健康问题必须弹气泡（不受概率门控制），同一原因不重复刷屏。"""
    _qapp()
    bubbles = []

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    cfg = Config(base=tmp_path)
    manager = AgentLinkManager(DummyWindow(), cfg, min_interval=0)
    try:
        manager._on_bridge_health_issue("dsh", "not-loaded", "DSH 没有加载桥接插件——重启一次 DSH")
        assert len(bubbles) == 1 and "重启" in bubbles[0], bubbles
        manager._on_bridge_health_issue("dsh", "not-loaded", "DSH 没有加载桥接插件——重启一次 DSH")
        assert len(bubbles) == 1, "同一原因在冷却窗口内不得重复弹"
        # 不同原因各自提示（不会被上一个原因挤掉）
        manager._on_bridge_health_issue("dsh", "link-missing", "桥接插件文件缺失——请重新安装桥接")
        assert len(bubbles) == 2, bubbles
    finally:
        manager.shutdown()
