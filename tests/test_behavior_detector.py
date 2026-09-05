# -*- coding: utf-8 -*-
"""行为模式检测（behavior_detector）单元测试。

覆盖：
- 行为分类：工具名 → 细分类（SEARCH/READ/THINK/NAV/EDIT/EXECUTE/TEST），
  命令型工具按 argv0 判定（bash+pytest → TEST 等）；
- 双窗口规则：W6 同类 >= 3 → control；W10 同类 >= 3 → warning；
  W10 同类 >= 4 → control；
- 大类规则：W6 EXPLORATION >= 5 且 ACTION == 0 → control；
  W10 EXPLORATION >= 7 且 ACTION <= 1 → warning；
- step 去重：同一步并行事件只算一次行为决策；
- cooldown 门控：触发后至少新增 N 个 step 才允许再次触发；
- Judge：Control 命中时调用 judge，缺省降级 REPLAN；
- idle / turn-end 重置；set_enabled 开关。

注意：macro W6「全探索无行动」规则优先级高于细分类 W10 warning，且最后一个
step 要等下一个 step 到达才会被 flush 计入窗口——测试序列按此设计，避免
与「W6 全探索 → control」或「W10 同类 → warning」相互抢占。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication

from pet.behavior_detector import (
    DEFAULT_MIN_STEPS_BETWEEN,
    BehaviorClass,
    BehaviorMacro,
    BehaviorPatternDetector,
    JudgeVerdict,
    PatternLevel,
    PatternReason,
    build_judge_prompt,
    classify_tool,
    macro_of,
    normalize_tool,
    parse_verdict,
)

pytest.importorskip("PySide6.QtWidgets")


class _FakeClock:
    """可手动推进的单调时钟。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _call(tool: str, step, args_key: str = "") -> dict:
    rec = {"event": "tool/call", "tool": tool, "step": step}
    if args_key:
        rec["argsKey"] = args_key
    return rec


def _make_detector(**kw) -> tuple[BehaviorPatternDetector, _FakeClock]:
    clock = _FakeClock()
    det = BehaviorPatternDetector(clock=clock, **kw)
    det.set_enabled(True)
    return det, clock


class _Collector:
    """收集 warning/control 信号。"""

    def __init__(self, det: BehaviorPatternDetector) -> None:
        self.warnings: list[dict] = []
        self.controls: list[dict] = []
        self.resolved: list[str] = []
        det.pattern_warning.connect(lambda k, p: self.warnings.append(p))
        det.pattern_control.connect(lambda k, p: self.controls.append(p))
        det.pattern_resolved.connect(lambda k: self.resolved.append(k))


class TestClassifyTool:
    def test_search_family(self):
        for tool in ("web_search", "WebSearch", "Search", "search_internet", "google_search"):
            assert classify_tool(tool) is BehaviorClass.SEARCH, tool

    def test_read_family(self):
        for tool in ("Read", "Grep", "Glob", "cat", "Get-Content", "head", "tail", "rg"):
            assert classify_tool(tool) is BehaviorClass.READ, tool

    def test_think_family(self):
        for tool in ("think", "reasoning", "Reason", "plan"):
            assert classify_tool(tool) is BehaviorClass.THINK, tool

    def test_navigation_family(self):
        for tool in ("pwd", "ls", "dir", "cd", "find", "realpath", "which"):
            assert classify_tool(tool) is BehaviorClass.NAVIGATION, tool

    def test_edit_family(self):
        for tool in ("Edit", "Write", "Patch", "apply_patch", "create_file"):
            assert classify_tool(tool) is BehaviorClass.EDIT, tool

    def test_execute_family(self):
        for tool in ("Bash", "Pwsh", "PowerShell", "exec", "command", "run"):
            assert classify_tool(tool) is BehaviorClass.EXECUTE, tool

    def test_test_family(self):
        for tool in ("pytest", "npm_test", "npm test", "Playwright", "vitest", "run_tests"):
            assert classify_tool(tool) is BehaviorClass.TEST, tool

    def test_command_tool_by_argv0(self):
        # bash 跑 pytest → TEST；bash 跑 pip → EXECUTE；bash 跑 curl → SEARCH
        assert classify_tool("bash", "argv0:pytest") is BehaviorClass.TEST
        assert classify_tool("bash", "argv0:pip,command") is BehaviorClass.EXECUTE
        assert classify_tool("bash", "argv0:curl") is BehaviorClass.SEARCH
        assert classify_tool("bash", "argv0:ls") is BehaviorClass.NAVIGATION
        # 无 argv0 的 bash 兜底 EXECUTE
        assert classify_tool("bash") is BehaviorClass.EXECUTE

    def test_unknown_tool_is_other(self):
        assert classify_tool("totally_unknown_xyz") is BehaviorClass.OTHER

    def test_normalize(self):
        assert normalize_tool("  Web_Search ") == "web_search"
        assert normalize_tool("Read") == "read"
        assert normalize_tool("Get-Content") == "get-content"

    def test_macro_mapping(self):
        assert macro_of(BehaviorClass.SEARCH) is BehaviorMacro.EXPLORATION
        assert macro_of(BehaviorClass.READ) is BehaviorMacro.EXPLORATION
        assert macro_of(BehaviorClass.THINK) is BehaviorMacro.EXPLORATION
        assert macro_of(BehaviorClass.NAVIGATION) is BehaviorMacro.EXPLORATION
        assert macro_of(BehaviorClass.EDIT) is BehaviorMacro.ACTION
        assert macro_of(BehaviorClass.EXECUTE) is BehaviorMacro.ACTION
        assert macro_of(BehaviorClass.TEST) is BehaviorMacro.ACTION


class TestDualWindowFineClass:
    def test_w6_three_same_class_control(self):
        """用户示例：6 个事件里已有 3 次 Search → control。"""
        det, _ = _make_detector()
        col = _Collector(det)
        # Search, Think, Read, Search, Bash, Think, Search, Search
        # （第 8 步使最近 6 步 = Read/Search/Bash/Think/Search/Search → Search=3）
        det.feed_record("dsh", _call("web_search", 1))
        det.feed_record("dsh", _call("think", 2))
        det.feed_record("dsh", _call("Read", 3))
        det.feed_record("dsh", _call("web_search", 4))
        det.feed_record("dsh", _call("Bash", 5))
        det.feed_record("dsh", _call("think", 6))
        assert not col.controls  # 此时 W6（步1-6）里 Search=2，未触发
        det.feed_record("dsh", _call("web_search", 7))  # W6（步2-7）= Search=2，仍未触发
        assert not col.controls
        det.feed_record("dsh", _call("web_search", 8))  # W6（步3-8）= Search=3 → control
        assert col.controls, "最近 6 步内 Search=3 应触发 control"
        assert col.controls[0]["level"] == PatternLevel.CONTROL.value
        assert col.controls[0]["reason"] == PatternReason.FINE_REPEAT_W6.value

    def test_w10_three_same_class_warning(self):
        det, _ = _make_detector()
        col = _Collector(det)
        # 10 个 step 里散落 3 次 Search（穿插 ACTION 避免 macro W6 抢占）→ warning
        steps = [("web_search", 1), ("Read", 2), ("Bash", 3), ("think", 4),
                 ("web_search", 5), ("Edit", 6), ("pwd", 7), ("Grep", 8),
                 ("web_search", 9), ("Bash", 10)]
        for tool, s in steps:
            det.feed_record("dsh", _call(tool, s))
        assert col.warnings, "W10 同类=3 应触发 warning"
        assert col.warnings[0]["reason"] == PatternReason.FINE_REPEAT_W10_WARN.value
        assert col.warnings[0]["class"] == BehaviorClass.SEARCH.value
        assert col.warnings[0]["count"] == 3

    def test_w10_four_same_class_control(self):
        det, _ = _make_detector(cooldown_seconds=0)
        col = _Collector(det)
        # 10 个 step 里有 4 次 Search（分布开避免 W6=3）→ control
        # 注意：第 7 步时 W10 Search=3 会先触发 warning，这里只断言最终 control
        steps = [("web_search", 1), ("Read", 2), ("Bash", 3), ("web_search", 4),
                 ("Edit", 5), ("think", 6), ("web_search", 7), ("Read", 8),
                 ("Bash", 9), ("web_search", 10), ("Edit", 11)]
        for tool, s in steps:
            det.feed_record("dsh", _call(tool, s))
        assert col.controls, "W10 同类=4 应触发 control"
        assert col.controls[0]["reason"] == PatternReason.FINE_REPEAT_W10_CONTROL.value
        assert col.controls[0]["count"] == 4

    def test_distinct_tools_same_behavior_class_aggregate(self):
        """Read/Grep/Glob/cat 归 READ：工具名不同也算同类。"""
        det, _ = _make_detector()
        col = _Collector(det)
        # 6 个 step 全为 READ 族（含 1 个 ACTION 避免 macro W6 抢占）
        steps = [("Read", 1), ("Grep", 2), ("Glob", 3), ("Bash", 4),
                 ("cat", 5), ("head", 6), ("tail", 7)]
        for tool, s in steps:
            det.feed_record("dsh", _call(tool, s))
        # W6（步2-7）= Grep/Glob/Bash/cat/head/tail → READ=5
        assert col.controls, "W6 内 READ 族 5 次应触发 control"
        assert col.controls[0]["class"] == BehaviorClass.READ.value


class TestMacroRules:
    def test_w6_explore_only_control(self):
        """Search/Read/Think/Grep/Search/Read：6/6 全探索、0 行动 → control。"""
        det, _ = _make_detector()
        col = _Collector(det)
        for tool, s in [("web_search", 1), ("Read", 2), ("think", 3), ("Grep", 4),
                        ("web_search", 5), ("Read", 6)]:
            det.feed_record("dsh", _call(tool, s))
        assert col.controls, "W6 全探索无行动应触发 control"
        assert col.controls[0]["reason"] == PatternReason.MACRO_EXPLORE_W6.value

    def test_w6_explore_with_action_not_control(self):
        det, _ = _make_detector()
        col = _Collector(det)
        # 5 个 EXPLORATION + 1 个 ACTION（Bash 放在中间，确保任意 W6 窗口都有 ACTION）
        for tool, s in [("web_search", 1), ("Read", 2), ("Bash", 3), ("think", 4),
                        ("Grep", 5), ("web_search", 6)]:
            det.feed_record("dsh", _call(tool, s))
        # EXPLORATION=5 但 ACTION>=1 → 不触发 macro W6（默认 ACTION==0 才 control）
        assert not col.controls

    def test_w10_explore_no_action_warning(self):
        det, _ = _make_detector()
        col = _Collector(det)
        # 10 个 step：9 探索 + 1 行动（Bash 放第 5 步，避免 W6 全探索抢占）
        steps = [("web_search", 1), ("Read", 2), ("think", 3), ("Grep", 4),
                 ("Bash", 5), ("web_search", 6), ("ls", 7), ("pwd", 8),
                 ("Read", 9), ("dir", 10)]
        for tool, s in steps:
            det.feed_record("dsh", _call(tool, s))
        # W10 EXPLORATION=9 >= 7 且 ACTION=1 <= 1 → warning（macro）
        assert col.warnings, "W10 探索为主且行动极少应触发 warning"
        assert col.warnings[0]["reason"] == PatternReason.MACRO_EXPLORE_W10.value


class TestStepDedup:
    def test_parallel_calls_in_same_step_count_once(self):
        """Step 12 里 Search A/B/C 只算一次 SEARCH，不能触发 control。"""
        det, _ = _make_detector()
        col = _Collector(det)
        det.feed_record("dsh", _call("web_search", 12, "query A"))
        det.feed_record("dsh", _call("web_search", 12, "query B"))
        det.feed_record("dsh", _call("web_search", 12, "query C"))
        # 同一 step 并行 3 次 → 1 次 SEARCH 决策；加上其他行为也不构成重复
        det.feed_record("dsh", _call("Read", 13))
        det.feed_record("dsh", _call("Bash", 14))
        det.feed_record("dsh", _call("pwd", 15))
        assert not col.controls, "同一步并行调用不应计为同类 3 次"
        assert not col.warnings


class TestCooldownGate:
    def test_after_trigger_needs_new_steps(self):
        det, clock = _make_detector(min_steps_between=3, cooldown_seconds=0)
        col = _Collector(det)
        # 触发一次 control（W6/W10 Search=3）
        for tool, s in [("web_search", 1), ("Read", 2), ("web_search", 3),
                        ("pwd", 4), ("web_search", 5), ("Grep", 6)]:
            det.feed_record("dsh", _call(tool, s))
        assert len(col.controls) == 1
        # 紧接着再来（新增 1 step，不足 3）→ 不重复触发
        det.feed_record("dsh", _call("web_search", 7))
        assert len(col.controls) == 1
        # 新增 3 step 后（step8 - step5 = 3），step gate 通过 → 允许再次触发
        det.feed_record("dsh", _call("Read", 8))
        assert len(col.controls) == 2, "新增 >=3 step 后应允许再次触发"
        det.feed_record("dsh", _call("think", 9))
        assert len(col.controls) == 2
        # step10: 仅 2 step 过去 → 仍被 gate 挡住
        det.feed_record("dsh", _call("web_search", 10))
        assert len(col.controls) == 2
        # step11: 3 step 过去 → 允许第三次触发
        det.feed_record("dsh", _call("web_search", 11))
        assert len(col.controls) >= 3, "新增 >=3 step 后应允许第三次触发"

    def test_cooldown_time_gate(self):
        det, clock = _make_detector(cooldown_seconds=60)
        col = _Collector(det)
        for tool, s in [("web_search", 1), ("Read", 2), ("web_search", 3),
                        ("pwd", 4), ("web_search", 5), ("Grep", 6)]:
            det.feed_record("dsh", _call(tool, s))
        assert len(col.controls) == 1
        # 时间冷却内即使新增 step 也不触发（时间兜底优先于 step 门控）
        clock.advance(10)
        det.feed_record("dsh", _call("web_search", 7))
        det.feed_record("dsh", _call("web_search", 8))
        det.feed_record("dsh", _call("web_search", 9))
        assert len(col.controls) == 1
        # 超过时间冷却且新增 step >= 3 → 允许再次触发
        clock.advance(60)
        det.feed_record("dsh", _call("web_search", 10))
        assert len(col.controls) >= 2


class TestJudge:
    def test_parse_verdict_keywords(self):
        assert parse_verdict("STOP") is JudgeVerdict.STOP
        assert parse_verdict("REPLAN") is JudgeVerdict.REPLAN
        assert parse_verdict("ASK_USER") is JudgeVerdict.ASK_USER
        assert parse_verdict("NORMAL") is JudgeVerdict.NORMAL
        assert parse_verdict("重新规划任务") is JudgeVerdict.REPLAN
        assert parse_verdict("") is JudgeVerdict.REPLAN  # 缺省降级

    def test_prompt_contains_sequence_and_summary(self):
        prompt = build_judge_prompt("SEARCH=3", "SEARCH > READ > SEARCH")
        assert "SEARCH=3" in prompt
        assert "SEARCH > READ > SEARCH" in prompt

    def test_control_invokes_judge(self):
        calls = []

        def fake_judge(summary, tool_seq):
            calls.append((summary, tool_seq))
            return JudgeVerdict.STOP

        det, _ = _make_detector(judge=fake_judge)
        col = _Collector(det)
        for tool, s in [("web_search", 1), ("Read", 2), ("web_search", 3),
                        ("pwd", 4), ("web_search", 5), ("Grep", 6)]:
            det.feed_record("dsh", _call(tool, s))
        assert col.controls
        assert calls, "Control 命中应调用 Judge"
        assert col.controls[0]["verdict"] == JudgeVerdict.STOP.value

    def test_control_without_judge_defaults_replan(self):
        det, _ = _make_detector()
        col = _Collector(det)
        for tool, s in [("web_search", 1), ("Read", 2), ("web_search", 3),
                        ("pwd", 4), ("web_search", 5), ("Grep", 6)]:
            det.feed_record("dsh", _call(tool, s))
        assert col.controls
        assert col.controls[0]["verdict"] == JudgeVerdict.REPLAN.value

    def test_warning_does_not_invoke_judge(self):
        calls = []

        def fake_judge(summary, tool_seq):
            calls.append(1)
            return JudgeVerdict.NORMAL

        det, _ = _make_detector(judge=fake_judge)
        col = _Collector(det)
        steps = [("web_search", 1), ("Bash", 2), ("Read", 3), ("think", 4),
                 ("web_search", 5), ("Bash", 6), ("Grep", 7), ("web_search", 8),
                 ("Bash", 9), ("cat", 10)]
        for tool, s in steps:
            det.feed_record("dsh", _call(tool, s))
        assert col.warnings, "W10 Search=3 应触发 warning"
        assert not calls, "Warning 档位不调用 Judge"


class TestLifecycle:
    def test_reset_on_idle(self):
        det, _ = _make_detector()
        col = _Collector(det)
        for tool, s in [("web_search", 1), ("Read", 2), ("web_search", 3)]:
            det.feed_record("dsh", _call(tool, s))
        det.feed_record("dsh", {"event": "AgentStatus", "state": "idle"})
        assert col.resolved == ["dsh"]
        # 重置后立即创建新 collector（在重置后触发的任何 control 之前）
        col2 = _Collector(det)
        # 重置后重新累计，不沿用旧窗口：
        # 3 个 READ 在 W6 中触发 W6 control
        steps = [("Read", 10), ("Grep", 11), ("cat", 12), ("head", 13),
                 ("tail", 14), ("Bash", 16)]
        for tool, s in steps:
            det.feed_record("dsh", _call(tool, s))
        # W6 中 READ=3 触发 W6 control（在 step 12 时）
        assert col2.controls, "重置后新窗口应能重新评估并触发"
        assert col2.controls[0]["reason"] == PatternReason.FINE_REPEAT_W6.value
        assert col2.controls[0]["class"] == BehaviorClass.READ.value
        assert col2.controls[0]["count"] == 3

    def test_reset_on_turn_end(self):
        det, _ = _make_detector()
        col = _Collector(det)
        for tool, s in [("web_search", 1), ("Read", 2), ("web_search", 3)]:
            det.feed_record("dsh", _call(tool, s))
        det.feed_record("dsh", {"event": "turn/end"})
        assert col.resolved == ["dsh"]

    def test_set_enabled_gates_processing(self):
        det, _ = _make_detector()
        col = _Collector(det)
        det.set_enabled(False)
        for tool, s in [("web_search", 1), ("web_search", 2), ("web_search", 3)]:
            det.feed_record("dsh", _call(tool, s))
        assert not col.controls
        det.set_enabled(True)
        for tool, s in [("web_search", 4), ("web_search", 5), ("web_search", 6)]:
            det.feed_record("dsh", _call(tool, s))
        det.feed_record("dsh", _call("Read", 7))  # flush step6 → W6 Search=3
        assert col.controls

    def test_defaults_are_sane(self):
        det, _ = _make_detector()
        assert det.is_enabled() is True
        assert DEFAULT_MIN_STEPS_BETWEEN >= 1
        assert det._w6_control >= 1
        assert det._w10_control > det._w10_warn


def _state_probe(det: BehaviorPatternDetector, agent_key: str):
    """便捷方法：暴露内部状态供断言（生产代码不依赖）。"""
    return det._states.get(agent_key)
