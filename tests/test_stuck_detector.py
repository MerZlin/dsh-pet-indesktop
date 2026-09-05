# -*- coding: utf-8 -*-
"""卡住检测（stuck_detector）单元测试。

覆盖：
- 档位阈值：worried（>=3 播动画）与 intervene（>=5 弹提醒）；
- 六条评分规则各自触发；
- 成功冲刷失败连跑、滑动窗口过期；
- idle / turn-end 重置；
- 冷却去抖与分数显著升高提前重发；
- set_enabled 开关；
- 事件载荷 shape（pet/intervention-recommended）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication

from pet.stuck_detector import (
    DEFAULT_INTERVENE_THRESHOLD,
    DEFAULT_WORRIED_THRESHOLD,
    StuckDetector,
    StuckReason,
    StuckSeverity,
    _bigram_jaccard,
    _goal_fingerprint,
    _normalize_error,
    stuck_reminder_text,
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


def _result(tool: str, ok: bool, *, error_text: str = "", error_code: str = "",
            args_key: str = "", duration_ms: int | None = None, timeout: bool = False) -> dict:
    return {
        "event": "tool/result",
        "tool": tool,
        "argsKey": args_key,
        "ok": ok,
        "errorText": error_text,
        "errorCode": error_code,
        "durationMs": duration_ms,
        "timeout": timeout,
    }


def _call(tool: str, args_key: str = "") -> dict:
    return {"event": "tool/call", "tool": tool, "argsKey": args_key}


def _request_error(error_code: str = "", error_message: str = "") -> dict:
    return {"event": "agent/request-error", "errorCode": error_code, "errorMessage": error_message}


def _llm_retry(retry: int = 1, error_code: str = "", error_message: str = "") -> dict:
    return {"event": "llm/retry", "retry": retry, "errorCode": error_code, "errorMessage": error_message}


def _make_detector(**kw) -> tuple[StuckDetector, _FakeClock]:
    clock = _FakeClock()
    det = StuckDetector(clock=clock, **kw)
    det.set_enabled(True)
    return det, clock


class _ReasonCollector:
    """通过 score_changed 信号收集触发的卡住原因。"""

    def __init__(self, det: StuckDetector) -> None:
        self.reasons: set[str] = set()
        det.score_changed.connect(self._on_score)

    def _on_score(self, agent_key: str, score: int, reasons: list, is_peak: bool) -> None:
        self.reasons.update(reasons)


class TestStuckDetectorScoring:
    def test_consecutive_two_failures_scores_one(self):
        det, _ = _make_detector()
        det.feed_record("dsh", _result("pip", False, error_text="boom"))
        det.feed_record("dsh", _result("curl", False, error_text="refused"))
        assert det.get_score("dsh") >= 1

    def test_success_breaks_failure_run(self):
        det, _ = _make_detector()
        det.feed_record("dsh", _result("pip", False, error_text="boom"))
        det.feed_record("dsh", _result("pip", True))  # 成功冲刷
        det.feed_record("dsh", _result("pip", False, error_text="boom2"))
        # 两次失败被成功隔开 → 不构成「连续失败连跑」
        assert det.get_score("dsh") == 0

    def test_repeated_timeouts_two_in_window(self):
        det, clock = _make_detector()
        collector = _ReasonCollector(det)
        det.feed_record("dsh", _result("pip", False, error_text="timed out", timeout=True))
        clock.advance(5)
        det.feed_record("dsh", _result("curl", False, error_text="ETIMEDOUT", timeout=True))
        assert StuckReason.REPEATED_TIMEOUTS in collector.reasons
        assert det.get_score("dsh") >= 2

    def test_same_goal_three_calls(self):
        det, _ = _make_detector()
        collector = _ReasonCollector(det)
        det.feed_record("dsh", _call("bash", "argv0:pip,command"))
        det.feed_record("dsh", _call("bash", "argv0:pip,command"))
        det.feed_record("dsh", _call("bash", "argv0:pip,command"))
        assert StuckReason.SAME_GOAL_LOOPING in collector.reasons

    def test_similar_error_text(self):
        det, _ = _make_detector()
        collector = _ReasonCollector(det)
        det.feed_record("dsh", _result("a", False, error_text="Cannot connect to host 1.2.3.4: Connection refused"))
        det.feed_record("dsh", _result("b", False, error_text="Cannot connect to host 5.6.7.8: Connection refused"))
        det.feed_record("dsh", _result("c", False, error_text="Cannot connect to host 9.10.11.12: Connection refused"))
        assert StuckReason.SIMILAR_ERROR_TEXT in collector.reasons

    def test_retry_language_text(self):
        det, _ = _make_detector()
        collector = _ReasonCollector(det)
        det.feed_record("dsh", {"event": "assistant/message", "text": "连接还是失败，我换个思路再试一次"})
        assert StuckReason.RETRY_LANGUAGE in collector.reasons
        assert det.get_score("dsh") >= 1

    def test_no_progress_same_root_cause(self):
        det, _ = _make_detector()
        collector = _ReasonCollector(det)
        for _ in range(3):
            det.feed_record("dsh", _result("pip", False, error_code="ECONNREFUSED", error_text="connection refused"))
        assert StuckReason.NO_PROGRESS_SAME_CAUSE in collector.reasons
        # 相同根因连续 3 次 → +2
        assert det.get_score("dsh") >= 2

    def test_request_error_counts_as_failure(self):
        det, _ = _make_detector()
        # 两次模型请求失败（agent/request-error）→ 连续失败连跑 +1
        det.feed_record("dsh", _request_error("ECONNREFUSED", "connect failed"))
        det.feed_record("dsh", _request_error("ECONNREFUSED", "connect failed again"))
        assert det.get_score("dsh") >= 1

    def test_llm_retry_feeds_retry_rule(self):
        det, _ = _make_detector()
        collector = _ReasonCollector(det)
        # 仅一条 llm/retry（无模型文本）→ 也应命中重试措辞规则 +1
        det.feed_record("dsh", _llm_retry(1, "429", "rate limited"))
        assert StuckReason.RETRY_LANGUAGE in collector.reasons
        assert det.get_score("dsh") >= 1

    def test_realistic_pip_proxy_scenario_recommends_intervention(self):
        """复刻典型卡住场景：不同命令、不同工具，但都围绕同一失败目标反复试探。

        pip --proxy 超时 → curl 验证代理失败 → 换 python 版本 + 国内镜像再试。
        不是「同一命令重复 3 次」（repeat-tool-reminder 不会触发），
        但 stuck_detector 应从错误指纹/重试措辞判定「该人工介入了」。
        """
        det, clock = _make_detector()
        events: list[dict] = []
        det.intervention_recommended.connect(lambda k, p: events.append(p))

        # 1) pip 装包 → 超时
        det.feed_record("dsh", _result("bash", False, error_code="ETIMEDOUT",
                                       error_text="pip download timed out after 60s", timeout=True))
        clock.advance(3)
        # 2) curl 验证代理 → 外网不通（同根因：网络不通）
        det.feed_record("dsh", _result("bash", False, error_code="ETIMEDOUT",
                                       error_text="curl: (7) Failed to connect to proxy: Connection timed out", timeout=True))
        clock.advance(3)
        # 3) 模型文本出现「换一种方式」
        det.feed_record("dsh", {"event": "assistant/message", "text": "代理有问题，换个方式验证一下网络"})
        clock.advance(3)
        # 4) 换 python3.10.9 + 国内镜像再试 → 仍失败（同根因：网络）
        det.feed_record("dsh", _result("bash", False, error_code="ETIMEDOUT",
                                       error_text="timed out connecting to mirror", timeout=True))
        clock.advance(3)
        # 5) 又一次网络类失败
        det.feed_record("dsh", _result("bash", False, error_code="ETIMEDOUT",
                                       error_text="connection reset — still no network"))

        score = det.get_score("dsh")
        assert score >= 5, f"期望达到建议介入阈值，实际 {score}"
        recommends = [e for e in events if e.get("severity") == StuckSeverity.RECOMMEND]
        assert recommends, "应触发建议介入事件"
        # 主原因应当是网络/环境类（timeout 或相同根因）
        assert recommends[0]["reason"] in (StuckReason.REPEATED_TIMEOUTS, StuckReason.NO_PROGRESS_SAME_CAUSE)

    def test_threshold_intervene_emits_event(self):
        det, _ = _make_detector()
        events: list[dict] = []
        det.intervention_recommended.connect(lambda k, p: events.append(p))

        det.feed_record("dsh", {"event": "assistant/message", "text": "我再试一次，换个方式"})
        det.feed_record("dsh", _result("pip", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))
        det.feed_record("dsh", _result("curl", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))
        det.feed_record("dsh", _result("wget", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))

        payloads = [p for p in events if p.get("severity") == StuckSeverity.RECOMMEND]
        assert payloads, "应触发建议介入事件"
        assert payloads[0]["type"] == "pet/intervention-recommended"
        assert payloads[0]["reason"] in {r.value for r in StuckReason}
        assert det.get_score("dsh") >= DEFAULT_INTERVENE_THRESHOLD

    def test_reset_on_idle(self):
        det, _ = _make_detector()
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        assert det.get_score("dsh") > 0
        det.feed_record("dsh", {"event": "AgentStatus", "state": "idle"})
        assert det.get_score("dsh") == 0

    def test_reset_on_turn_end(self):
        det, _ = _make_detector()
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        det.feed_record("dsh", {"event": "turn/end"})
        assert det.get_score("dsh") == 0

    def test_window_expiry_drops_old_events(self):
        det, clock = _make_detector(window_seconds=30)
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        assert det.get_score("dsh") >= 1
        clock.advance(60)  # 超过窗口
        det._prune()
        assert det.get_score("dsh") == 0

    def test_cooldown_suppresses_repeat(self):
        det, clock = _make_detector(cooldown_seconds=300)
        events: list[dict] = []
        det.intervention_recommended.connect(lambda k, p: events.append(p))
        # 3 个相同 timeout 失败 → 分数 5，触发一次建议介入
        for _ in range(3):
            det.feed_record("dsh", _result("pip", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))
        recommends = [e for e in events if e.get("severity") == StuckSeverity.RECOMMEND]
        assert len(recommends) == 1
        # 冷却期内继续同类失败（分数不升高）→ 不重复弹
        clock.advance(10)
        det.feed_record("dsh", _result("curl", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))
        det.feed_record("dsh", _result("wget", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))
        recommends = [e for e in events if e.get("severity") == StuckSeverity.RECOMMEND]
        assert len(recommends) == 1

    def test_score_jump_by_two_breaks_cooldown(self):
        det, clock = _make_detector(cooldown_seconds=300)
        events: list[dict] = []
        det.intervention_recommended.connect(lambda k, p: events.append(p))
        # 初始到 5 分（触发一次）
        for _ in range(3):
            det.feed_record("dsh", _result("pip", False, error_code="ETIMEDOUT", error_text="timed out", timeout=True))
        assert len([e for e in events if e.get("severity") == StuckSeverity.RECOMMEND]) == 1
        # 冷却内分数再升 ≥2（同一目标循环 +1、重试措辞 +1）→ 提前重发
        clock.advance(10)
        det.feed_record("dsh", _call("bash", "argv0:pip,command"))
        det.feed_record("dsh", _call("bash", "argv0:pip,command"))
        det.feed_record("dsh", _call("bash", "argv0:pip,command"))
        det.feed_record("dsh", {"event": "assistant/message", "text": "换个方式再试一次"})
        recommends = [e for e in events if e.get("severity") == StuckSeverity.RECOMMEND]
        assert len(recommends) >= 2, "分数显著升高应提前重发提醒"

    def test_set_enabled_gates_processing(self):
        det, _ = _make_detector()
        det.set_enabled(False)
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        assert det.get_score("dsh") == 0
        det.set_enabled(True)
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        det.feed_record("dsh", _result("pip", False, error_text="timeout"))
        assert det.get_score("dsh") >= 1


class TestStuckHelpers:
    def test_normalize_error_strips_noise(self):
        a = _normalize_error("Error 12345: Cannot connect to host 1.2.3.4 at 2026-08-30 10:00:00")
        b = _normalize_error("Error 99999: Cannot connect to host 9.9.9.9 at 2026-08-30 11:00:00")
        assert _bigram_jaccard(a, b) > 0.55

    def test_goal_fingerprint_differs_by_tool(self):
        assert _goal_fingerprint("bash", "argv0:pip") != _goal_fingerprint("curl", "argv0:pip")
        assert _goal_fingerprint("bash", "argv0:pip") == _goal_fingerprint("bash", "argv0:pip")

    def test_reminder_text_custom_and_default(self):
        assert "{name}" not in stuck_reminder_text("DSH")
        assert "DSH" in stuck_reminder_text("DSH")
        assert stuck_reminder_text("DSH", "快去看 {name}！") == "快去看 DSH！"


class TestDetectorDefaults:
    def test_defaults_are_sane(self):
        assert DEFAULT_WORRIED_THRESHOLD < DEFAULT_INTERVENE_THRESHOLD
        det, _ = _make_detector()
        assert det.get_score("dsh") == 0
        assert det.is_enabled() is True
