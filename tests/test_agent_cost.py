# -*- coding: utf-8 -*-
"""Agent 消费统计的离线单元测试。

全部不联网：快照由测试注入。
"""

from __future__ import annotations

from pet.agent_cost import AgentCostTracker, Snapshot, format_cost


def _tracker():
    return AgentCostTracker(clock=lambda: 0.0)


# ---------------------------------------------------------------- 文案格式


def test_format_cost_normal():
    assert format_cost(0.08) == "本轮消费 ¥0.08"
    assert format_cost(0.5) == "本轮消费 ¥0.50"
    assert format_cost(1.234) == "本轮消费 ¥1.23"


def test_format_cost_below_precision():
    """余额只有两位小数，不足半分的变化本来就测不出。"""
    assert format_cost(0.0) == "本轮消费不足 ¥0.01"
    assert format_cost(0.004) == "本轮消费不足 ¥0.01"
    # 恰好半分以上就要正常显示
    assert format_cost(0.006) == "本轮消费 ¥0.01"


def test_format_cost_concurrent_marker():
    """并发时标注出来，不隐瞒数字可能混入了别人的消耗。"""
    assert format_cost(0.08, concurrent=True) == "本轮消费 ¥0.08（含其他会话）"
    assert format_cost(0.0, concurrent=True) == "本轮消费不足 ¥0.01（含其他会话）"


# ---------------------------------------------------------------- 快照与差值


def test_single_agent_cost():
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 4.70)
    assert t.finish("claude", 4.62) == "本轮消费 ¥0.08"


def test_finish_without_baseline_is_silent():
    """没登记过开始（功能中途才开）→ 不显示金额。"""
    t = _tracker()
    assert t.finish("claude", 4.62) is None


def test_finish_before_baseline_arrives_is_silent():
    """回合极短、余额还没查回来 → 不显示，而不是显示错的。"""
    t = _tracker()
    t.begin("claude")
    assert t.finish("claude", 4.62) is None


def test_balance_increase_clamps_to_zero():
    """余额反而涨了（充值/接口抖动）不能显示负数。"""
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 4.00)
    assert t.finish("claude", 5.00) == "本轮消费不足 ¥0.01"


def test_agents_are_counted_separately():
    """每个 Agent 各自算，互不干扰——这是用户选的粒度。"""
    t = _tracker()
    t.begin("claude")
    t.begin("dsh")
    t.set_baseline("claude", 10.00)
    t.set_baseline("dsh", 10.00)

    # dsh 先进先出，claude 的基线不该被它吃掉。
    # 两个都带并发标注：会话期间确实同时有两个 Agent 在跑，后结束的那个
    # 差值里同样混着别人的消费（用"此刻是否并发"判会漏掉它）。
    assert t.finish("dsh", 9.90) == "本轮消费 ¥0.10（含其他会话）"
    assert t.finish("claude", 9.95) == "本轮消费 ¥0.05（含其他会话）"


def test_concurrent_marker_only_when_multiple_busy():
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 10.00)
    # 只有自己一个在跑：不带标注
    assert t.finish("claude", 9.90) == "本轮消费 ¥0.10"


def test_concurrent_marker_when_other_still_busy():
    t = _tracker()
    t.begin("claude")
    t.begin("dsh")
    t.set_baseline("claude", 10.00)
    assert t.finish("claude", 9.90) == "本轮消费 ¥0.10（含其他会话）"


# ---------------------------------------------------------------- 状态管理


def test_begin_clears_previous_baseline():
    """上一轮遗留的数字不能被当成这一轮的起点。"""
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 10.00)
    t.finish("claude", 9.90)

    t.begin("claude")  # 新一轮，快照还没回来
    assert t.finish("claude", 9.80) is None, "不能用上一轮的基线"


def test_abort_discards_state():
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 10.00)
    t.abort("claude")
    assert t.finish("claude", 9.90) is None


def test_set_baseline_ignored_when_not_busy():
    """查询回来时 Agent 已经结束了：不该留下孤儿基线。"""
    t = _tracker()
    t.begin("claude")
    t.finish("claude", 9.90)  # 先结束了
    t.set_baseline("claude", 10.00)  # 迟到的基线
    t.begin("claude")  # 下一轮
    assert t.finish("claude", 9.50) is None, "迟到基线不该被下一轮复用"


def test_clear_resets_everything():
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 10.00)
    t.clear()
    assert t.finish("claude", 9.90) is None


def test_is_tracking_and_concurrent():
    t = _tracker()
    assert not t.is_tracking("claude")
    t.begin("claude")
    assert t.is_tracking("claude")
    assert not t.concurrent
    t.begin("dsh")
    assert t.concurrent
    t.finish("claude", 9.90)
    assert not t.concurrent


def test_snapshot_dataclass():
    s = Snapshot(total=4.7, at=123.0)
    assert s.total == 4.7 and s.at == 123.0


def test_sequential_agents_have_no_concurrent_marker():
    """先后串行执行（不重叠）时不该带并发标注。"""
    t = _tracker()
    t.begin("claude")
    t.set_baseline("claude", 10.00)
    assert t.finish("claude", 9.90) == "本轮消费 ¥0.10"

    # 第一个结束后第二个才开始
    t.begin("dsh")
    t.set_baseline("dsh", 10.00)
    assert t.finish("dsh", 9.80) == "本轮消费 ¥0.20"


def test_concurrent_flag_resets_after_all_finish():
    """全部结束后并发标志复位，下一轮串行不受上一轮影响。"""
    t = _tracker()
    t.begin("claude")
    t.begin("dsh")
    t.set_baseline("claude", 10.0)
    t.set_baseline("dsh", 10.0)
    t.finish("claude", 9.9)
    t.finish("dsh", 9.9)

    t.begin("claude")
    t.set_baseline("claude", 10.0)
    assert t.finish("claude", 9.9) == "本轮消费 ¥0.10", "上一轮的并发不该残留"
