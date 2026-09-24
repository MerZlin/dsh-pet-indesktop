# -*- coding: utf-8 -*-
"""事件汇报概率门（report_gates）契约测试。

用户口径（2026-09-10 定稿）：
- 「自动化与联动 → 事件气泡触发概率」下一个**可折叠**框里，按**事件聚合类别**
  分组收纳所有事件汇报控制；
- **没有开关，只有滑块**，滑块值就是该类事件的**通过概率 0.00–1.00**
  （0 = 该类完全不汇报，1 = 全部汇报）；
- 概率门与它控制的文案行同组，位置绑定。

本文件只覆盖纯逻辑接缝（默认值 / 迁移 / 清洗 / 判决 / 事件键→门映射），
不依赖 Qt 与 tmp_path。
"""

from __future__ import annotations

import pytest

from pet.config import _clean_agent_link_data, _default_agent_link_data

# 事件聚合类别 → 门名（8 类）
GATE_KEYS = (
    "state",  # 开始干活 / 思考
    "activity",  # 过程汇报（读文件/跑命令/改代码…）
    "approval",  # 审批与提问（需要你处理）
    "done",  # 任务完成
    "exec_failed",  # 失败与错误
    "model_access",  # 模型访问失败
    "stuck",  # 检测类提醒：卡住 / 行为重复 / 循环
    "bridge",  # 桥接安装与写回、Agent 未找到、余额查询
)


def test_default_report_gates_are_probabilities():
    """默认门值：全部门在 [0, 1]；除过程汇报 0.6 外其余常开 1.0。"""
    gates = _default_agent_link_data()["report_gates"]
    assert set(gates) == set(GATE_KEYS)
    for key, value in gates.items():
        assert isinstance(value, (int, float)), f"{key} 必须是数值概率"
        assert 0.0 <= float(value) <= 1.0, f"{key} 越界：{value}"
    assert float(gates["activity"]) == pytest.approx(0.6)
    for key in ("state", "approval", "done", "exec_failed", "model_access", "stuck", "bridge"):
        assert float(gates[key]) == pytest.approx(1.0), f"{key} 应默认常开"


def test_default_ships_no_legacy_switch_keys():
    """不留布尔开关别名：旧键不再出现在默认配置里（一次性迁移，不做双写）。"""
    defaults = _default_agent_link_data()
    for legacy in ("notify_state", "notify_activity", "notify_approval", "notify_done", "notify_exec_failed", "report_probability"):
        assert legacy not in defaults, f"{legacy} 应已由 report_gates 取代"


@pytest.mark.parametrize(
    "legacy_key,gate_key",
    [
        ("notify_state", "state"),
        ("notify_activity", "activity"),
        ("notify_approval", "approval"),
        ("notify_done", "done"),
        ("notify_exec_failed", "exec_failed"),
    ],
)
@pytest.mark.parametrize("legacy_value,expected", [(True, 1.0), (False, 0.0)])
def test_legacy_switches_migrate_to_probability(legacy_key, gate_key, legacy_value, expected):
    """旧布尔开关 → 概率端点：开 = 1.0，关 = 0.0。"""
    cleaned = _clean_agent_link_data({legacy_key: legacy_value})
    assert float(cleaned["report_gates"][gate_key]) == pytest.approx(expected)


def test_legacy_report_probability_percent_migrates_to_activity():
    """旧的 report_probability（0-100 整数）→ activity 概率（0-1）。"""
    cleaned = _clean_agent_link_data({"report_probability": 60})
    assert float(cleaned["report_gates"]["activity"]) == pytest.approx(0.6)
    cleaned0 = _clean_agent_link_data({"report_probability": 0})
    assert float(cleaned0["report_gates"]["activity"]) == pytest.approx(0.0)
    cleaned100 = _clean_agent_link_data({"report_probability": 100})
    assert float(cleaned100["report_gates"]["activity"]) == pytest.approx(1.0)


def test_new_shape_wins_over_legacy_on_conflict():
    """同时存在新旧键时以新形状为准，不被旧键覆盖。"""
    cleaned = _clean_agent_link_data(
        {
            "report_gates": {"activity": 0.25},
            "report_probability": 100,
            "notify_activity": True,
        }
    )
    assert float(cleaned["report_gates"]["activity"]) == pytest.approx(0.25)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.0, 0.0),
        (1.0, 1.0),
        (0.6, 0.6),
        (-0.5, 0.0),
        (1.5, 1.0),
        ("0.75", 0.75),
        (True, 1.0),  # 布尔不是特例：True → 1.0
        (False, 0.0),  # False → 0.0
        ("abc", 0.6),  # 无法解析 → 回落该门默认（activity 默认 0.6）
        (None, 0.6),
    ],
)
def test_clean_report_gates_clamps_and_falls_back(raw, expected):
    """越界收敛到 [0, 1]，数值字符串可解析，无法解析回落该门默认（activity=0.6）。"""
    cleaned = _clean_agent_link_data({"report_gates": {"activity": raw}})
    assert float(cleaned["report_gates"]["activity"]) == pytest.approx(expected)


def test_clean_report_gates_ignores_unknown_gate_names():
    """未知门名不进配置（防止拼写错误静默生效）。"""
    cleaned = _clean_agent_link_data({"report_gates": {"nonexistent_gate": 0.5}})
    assert "nonexistent_gate" not in cleaned["report_gates"]
    assert set(cleaned["report_gates"]) == set(GATE_KEYS)


def test_clean_report_gates_keeps_other_gates_when_one_is_partial():
    """部分更新：只改一个门，其余保持默认。"""
    cleaned = _clean_agent_link_data({"report_gates": {"activity": 0.3}})
    assert float(cleaned["report_gates"]["activity"]) == pytest.approx(0.3)
    assert float(cleaned["report_gates"]["done"]) == pytest.approx(1.0)


def test_gate_for_event_maps_every_dialogue_key():
    """每个气泡文案事件键都必须映射到一个已定义的门（不留无门事件）。"""
    from pet.modern_settings_dialog import DIALOGUE_LABELS
    from pet.report_gates import gate_for_event

    unmapped = []
    for key in DIALOGUE_LABELS:
        gate = gate_for_event(key)
        if gate is None:
            unmapped.append(key)
        else:
            assert gate in GATE_KEYS, f"{key} 映射到未知门 {gate}"
    assert unmapped == [], f"这些事件键没有归属门：{unmapped}"


@pytest.mark.parametrize(
    "event_key,gate",
    [
        ("start", "state"),
        ("thinking", "state"),
        ("activity.read", "activity"),
        ("activity.run", "activity"),
        ("approval.command", "approval"),
        ("question.one", "approval"),
        ("done.success", "done"),
        ("failure.generic", "exec_failed"),
        ("failure.tool", "exec_failed"),
        ("model_access.one", "model_access"),
        ("llm_error.api", "model_access"),
        ("stuck.reminder", "stuck"),
        ("pattern.warning", "stuck"),
        ("watchdog.warning", "stuck"),
        ("bridge.install.failed", "bridge"),
        ("agent.missing", "bridge"),
        ("balance.result", "bridge"),
    ],
)
def test_gate_for_event_examples(event_key, gate):
    from pet.report_gates import gate_for_event

    assert gate_for_event(event_key) == gate
