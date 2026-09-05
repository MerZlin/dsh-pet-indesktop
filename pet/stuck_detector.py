# -*- coding: utf-8 -*-
"""DSH Agent 卡住检测与人工介入建议引擎（StuckDetector）。

桌宠端推断事件 ``pet/intervention-recommended`` 的定义与实现：
直接监听桥接增强事件（``tools/result``、``tool/call``、``agent/request-error``、
``llm/retry``、``assistant/message``）的滑动窗口，按规则计算卡住评分，
达到阈值时发射信号驱动桌宠动画/气泡。

设计文档详见 ``docs/STUCK_DETECTOR.md``。
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from enum import Enum
from functools import lru_cache

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("dsh-pet-standalone")

# ---------------------------------------------------------------------------
# 常量 / 默认配置
# ---------------------------------------------------------------------------

# 卡住评分规则分值
SCORE_CONSECUTIVE_FAILURES = 1      # 同一任务连续 2 次工具失败
SCORE_REPEATED_TIMEOUTS = 1         # 60~90s 内 2 次 timeout
SCORE_SAME_GOAL_LOOP = 1            # 连续 3 次工具调用围绕同一目标
SCORE_SIMILAR_ERROR_TEXT = 1        # 连续失败错误文本高度相似
SCORE_RETRY_LANGUAGE = 1            # 模型文本出现重试措辞
SCORE_NO_PROGRESS_SAME_CAUSE = 2    # 相同根因连续 3 次无改善

# 默认阈值
DEFAULT_WORRIED_THRESHOLD = 3       # stuck_score >= 3 → 担忧动画
DEFAULT_INTERVENE_THRESHOLD = 5     # stuck_score >= 5 → 建议介入提醒
DEFAULT_WINDOW_SECONDS = 90.0       # 滑动窗口（秒）
DEFAULT_COOLDOWN_SECONDS = 300.0    # 同 Agent 提醒冷却

# 重试措辞正则
_RETRY_TEXT_RE = re.compile(
    r"(再试|重试|换一种|换种|换个|另一种|另一种方式|换个方式|换一个思路|"
    r"换个角度|换个方案|换个方向|换种方式|换种思路|再(次|来)?(尝试|试)|"
    r"\bretry\b|\bstill hangs\b|\bhang(ing)?\b|\btry again\b|\bstuck\b)",
    re.IGNORECASE,
)

# 超时关键词
_TIMEOUT_RE = re.compile(
    r"timeout|timed ?out|超时|ETIMEDOUT|ESOCKETTIMEDOUT|ECONNABORTED|"
    r"read ?timed ?out|connect ?timed ?out",
    re.IGNORECASE,
)

# 错误相似度阈值（Jaccard bigram 相似度）
_ERROR_SIMILARITY_THRESHOLD = 0.55

# 焦虑动画关键词映射（从当前角色 acts 池里按语义挑选）
WORRIED_KEYWORDS = ("焦急", "着急", "气急败坏", "抓狂", "拍打", "敲桌", "烦恼", "抓狂", "焦虑")

# 默认提醒文案
DEFAULT_STUCK_REMINDER = (
    "主人，{name} 好像卡在环境/网络问题上转圈圈了…… 人工介入可能更快哦。"
)

# 干预原因代码
class StuckReason(str, Enum):
    CONSECUTIVE_FAILURES = "repeated_tool_failures"
    REPEATED_TIMEOUTS = "repeated_timeouts"
    SAME_GOAL_LOOPING = "same_goal_looping"
    SIMILAR_ERROR_TEXT = "similar_error_text"
    RETRY_LANGUAGE = "retry_language"
    NO_PROGRESS_SAME_CAUSE = "no_progress_same_root_cause"

# 「同一目标」规则只看动作型工具：命令（带 argv0 指纹）或明确的动作工具。
# 只读观察工具（read/grep/glob/…）反复调用是正常探索，不视为钻牛角尖。
_ACTION_TOOLS = {
    "bash", "shell", "pwsh", "powershell", "exec", "command", "run",
    "pip", "pip3", "npm", "npx", "pnpm", "curl", "wget", "install",
    "webfetch", "fetch", "web_search", "websearch", "browser",
}


def _is_goal_candidate(e) -> bool:
    """是否为「同一目标循环」规则的候选调用（动作型工具）。"""
    if not e.tool:
        return False
    if "argv0:" in (e.args_key or ""):
        return True
    return e.tool.lower() in _ACTION_TOOLS

# 严重程度
class StuckSeverity(int, Enum):
    WORRIED = 1          # 担忧——只播动画，不弹气泡
    RECOMMEND = 2        # 建议介入——动画 + 持续提醒气泡
    MUST_INTERVENE = 3   # 必须介入（由 approval/question 阻塞，本模块不产生）


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _normalize_error(text: str) -> str:
    """归一化错误文本：小写、去空白、去常见噪声片段。"""
    if not text:
        return ""
    s = text.lower()
    # 去时间戳 / 数字 / 路径 / 控制字符
    s = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", "", s)
    s = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", "", s)
    s = re.sub(r"\b\d+\b", "n", s)
    s = re.sub(r"[a-z]:\\(?:[^\\\s]+\\?)*", "path", s)
    s = re.sub(r"/(?:[^\s/]+/)+", "path", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _bigram_jaccard(a: str, b: str) -> float:
    """两个字符串的字符二元组 Jaccard 相似度（0~1）。"""
    if not a or not b:
        return 0.0
    def bigrams(s: str) -> set[str]:
        return {s[i:i+2] for i in range(len(s) - 1)}
    ba = bigrams(a)
    bb = bigrams(b)
    if not ba and not bb:
        return 1.0
    union = ba | bb
    if not union:
        return 0.0
    return len(ba & bb) / len(union)


def _goal_fingerprint(tool: str, args_key: str) -> str:
    """同一目标的指纹：工具名 + 参数摘要。"""
    return f"{tool}|{args_key}"


def _is_timeout(record: dict) -> bool:
    """判断一条事件是否涉及超时。"""
    if record.get("timeout"):
        return True
    for key in ("errorText", "errorMessage", "errorCode"):
        val = str(record.get(key, "") or "")
        if _TIMEOUT_RE.search(val):
            return True
    return False


def _error_code_fingerprint(record: dict) -> str:
    """提取错误码指纹（用于「相同根因」判定）。"""
    ec = str(record.get("errorCode", "") or "").strip()
    if ec:
        return ec
    # 从错误文本中提取常见错误码模式
    et = str(record.get("errorText", "") or "")
    m = re.search(r"\b(ECONNREFUSED|ECONNRESET|EAI_AGAIN|ETIMEDOUT|"
                  r"ESOCKETTIMEDOUT|ENOTFOUND|EACCES|EPERM|ENOENT|"
                  r"HTTP_4\d{2}|HTTP_5\d{2}|4\d{2}|5\d{2})\b", et, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    for key in ("errorMessage",):
        val = str(record.get(key, "") or "")
        m = re.search(r"\b(4\d{2}|5\d{2})\b", val)
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# 工具事件快照（滑动窗口中的一条记录）
# ---------------------------------------------------------------------------

class ToolEvent:
    """滑动窗口中的一条工具事件快照。"""

    __slots__ = ("ts", "tool", "args_key", "ok", "duration_ms", "error_code",
                 "error_text", "is_timeout", "event_type")

    def __init__(self, ts: float, record: dict) -> None:
        self.ts = ts
        self.tool = str(record.get("tool", "") or "")
        self.args_key = str(record.get("argsKey", "") or "")
        self.ok = bool(record.get("ok", True))
        self.duration_ms = record.get("durationMs")  # may be None
        self.error_code = str(record.get("errorCode", "") or "")
        # errorText（tool/result）或 errorMessage（agent/request-error / llm/retry）
        self.error_text = str(record.get("errorText") or record.get("errorMessage") or "")
        self.is_timeout = _is_timeout(record)
        self.event_type = str(record.get("event", "") or "")


# ---------------------------------------------------------------------------
# 卡住检测器
# ---------------------------------------------------------------------------

class StuckDetector(QObject):
    """Agent 卡住检测器：消费桥接事件，输出卡住评分与干预建议。

    用法：:

        detector = StuckDetector()
        detector.feed_record("dsh", {"event": "tool/result", "tool": "pip", ...})
        detector.score_changed.connect(handler)
        detector.intervention_recommended.connect(handler)
    """

    # 卡住评分变化：（agent_key, score, reason_list, is_peak）
    score_changed = Signal(str, int, list, bool)

    # 建议介入：（agent_key, payload）
    # payload = {"type": "pet/intervention-recommended", "reason": str, "severity": int}
    intervention_recommended = Signal(str, object)

    # 卡住已解除（score 归零）：
    stuck_resolved = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        worried_threshold: int = DEFAULT_WORRIED_THRESHOLD,
        intervene_threshold: int = DEFAULT_INTERVENE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock=None,
    ) -> None:
        super().__init__(parent)
        self._window_seconds = float(window_seconds)
        self._worried_threshold = int(worried_threshold)
        self._intervene_threshold = int(intervene_threshold)
        self._cooldown_seconds = float(cooldown_seconds)
        self._clock = clock or time.monotonic

        # 每个 Agent 的滑动窗口：list[ToolEvent]
        self._windows: dict[str, list[ToolEvent]] = defaultdict(list)

        # 每个 Agent 的最后一次 assistant 文本（用于重试措辞检测）
        self._last_assistant_text: dict[str, str] = {}

        # 当前评分
        self._scores: dict[str, int] = {}

        # 上次弹提醒的时刻（用于冷却）
        self._last_intervene: dict[str, float] = {}
        # 上次弹提醒时的分数 & 原因
        self._last_intervene_score: dict[str, int] = {}

        # 剪枝定时器
        self._prune_timer = QTimer(self)
        self._prune_timer.setInterval(15000)  # 15s 清理一次
        self._prune_timer.timeout.connect(self._prune)
        self._prune_timer.start()

        # 功能开关（默认关，与 Agent 联动一致；由 AgentLinkManager 配置）
        self._enabled = False

    # ------------------------------------------------------------ 开关 / 生命周期

    def set_enabled(self, enabled: bool) -> None:
        """启用/停用卡住检测。停用时清空全部状态并静默。"""
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.reset_all()

    def is_enabled(self) -> bool:
        return self._enabled

    def pause(self) -> None:
        """桌宠隐藏时暂停剪枝定时器（低功耗）。"""
        self._prune_timer.stop()

    def resume(self) -> None:
        if self._enabled:
            self._prune_timer.start()

    # ------------------------------------------------------------ 公开 API

    def feed_record(self, agent_key: str, record: dict) -> None:
        """消费一条桥接事件记录。"""
        if not self._enabled:
            return
        event = str(record.get("event", "") or "")

        # assistant/message 文本（用于重试措辞检测）
        if event == "assistant/message":
            text = str(record.get("text", "") or "")
            if text:
                self._last_assistant_text[agent_key] = text
            # 不进入工具窗口，但会影响评分重算
            self._recompute(agent_key)
            return

        # 工具调用 / 结果 / 错误 → 进入窗口
        if event in ("tool/call", "tool/result", "agent/request-error", "llm/retry"):
            ev = ToolEvent(self._clock(), record)
            # agent/request-error 是模型请求失败（网络/鉴权/限流），按失败处理；
            # llm/retry 本身是「即将重试」的信号，不入失败统计，但参与重试规则。
            if event == "agent/request-error":
                ev.ok = False
            window = self._windows[agent_key]
            window.append(ev)
            self._prune_one(window)
            self._recompute(agent_key)
            return

        # 空闲 / 离线 → 重置
        if event in ("AgentStatus",) and record.get("state") in ("idle", "sleeping"):
            self._reset(agent_key)
            return

        # turn/end → 成功完成，重置
        if event == "turn/end":
            self._reset(agent_key)
            return

    def reset(self, agent_key: str) -> None:
        """手动重置指定 Agent 的卡住状态（DSH 离线/新任务开始等）。"""
        self._reset(agent_key)

    def reset_all(self) -> None:
        """重置全部 Agent 的卡住状态。"""
        for key in list(self._windows):
            self._reset(key)

    def get_score(self, agent_key: str) -> int:
        """获取当前卡住评分。"""
        return self._scores.get(agent_key, 0)

    def get_config_overrides(self, config: dict) -> None:
        """从配置字典中读取覆盖参数。"""
        self._window_seconds = float(config.get("stuck_window_seconds", DEFAULT_WINDOW_SECONDS))
        self._worried_threshold = int(config.get("stuck_worried_threshold", DEFAULT_WORRIED_THRESHOLD))
        self._intervene_threshold = int(config.get("stuck_intervene_threshold", DEFAULT_INTERVENE_THRESHOLD))
        self._cooldown_seconds = float(config.get("stuck_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))

    # ------------------------------------------------------------ 评分引擎

    def _recompute(self, agent_key: str) -> None:
        """重新计算指定 Agent 的卡住评分并发射信号。"""
        window = self._windows.get(agent_key, [])
        now = self._clock()

        # ---- 步骤 1：收集基础统计 ----
        # 成败只认「结果类」事件：tool/result 的 ok 字段 + agent/request-error（恒失败）；
        # tool/call 记录 ok 恒为 True、llm/retry 是重试信号，都不能当成功/失败。
        outcome_events = [e for e in window if e.event_type in ("tool/result", "agent/request-error")]
        failures = [e for e in outcome_events if not e.ok]
        timeouts = [e for e in failures if e.is_timeout]

        # 失败连跑：窗口内最长的连续失败段（仅按结果事件计，无成功间隔）
        consecutive_fail_run = 0
        best_fail_run = 0
        for e in outcome_events:
            if e.ok:
                consecutive_fail_run = 0
            else:
                consecutive_fail_run += 1
                best_fail_run = max(best_fail_run, consecutive_fail_run)

        # ---- 步骤 2：逐条算分 ----
        score = 0
        reasons: list[str] = []

        # +1 同一任务连续 2 次工具失败
        if best_fail_run >= 2:
            score += SCORE_CONSECUTIVE_FAILURES
            reasons.append(StuckReason.CONSECUTIVE_FAILURES)

        # +1 60~90 秒内出现 2 次 timeout
        if len(timeouts) >= 2:
            # 检查窗口内是否有 ≥2 个 timeout 在 90s 内
            ts_list = sorted(e.ts for e in timeouts)
            if len(ts_list) >= 2 and (ts_list[-1] - ts_list[0]) <= self._window_seconds:
                score += SCORE_REPEATED_TIMEOUTS
                reasons.append(StuckReason.REPEATED_TIMEOUTS)

        # +1 连续 3 次工具调用都围绕同一目标
        # 只看动作型工具（命令/网络/安装），排除 read/grep 等只读观察工具；
        # 目标指纹 = 工具名 + 参数摘要（命令型含 argv0）。
        goal_events = [e for e in window if _is_goal_candidate(e) and e.event_type == "tool/call"]
        if len(goal_events) >= 3:
            from collections import Counter
            recent_goals = goal_events[-6:]
            fp_counts = Counter(_goal_fingerprint(e.tool, e.args_key) for e in recent_goals)
            most_common = fp_counts.most_common(1)
            if most_common and most_common[0][1] >= 3:
                score += SCORE_SAME_GOAL_LOOP
                reasons.append(StuckReason.SAME_GOAL_LOOPING)

        # +1 连续失败错误文本高度相似
        if len(failures) >= 3:
            norm_texts = [_normalize_error(e.error_text) for e in failures[-5:]]
            similar_pairs = 0
            for i in range(len(norm_texts)):
                for j in range(i + 1, len(norm_texts)):
                    if _bigram_jaccard(norm_texts[i], norm_texts[j]) >= _ERROR_SIMILARITY_THRESHOLD:
                        similar_pairs += 1
            if similar_pairs >= 3:
                score += SCORE_SIMILAR_ERROR_TEXT
                reasons.append(StuckReason.SIMILAR_ERROR_TEXT)

        # +1 模型文本出现重试措辞，或窗口内出现过 LLM 层重试（llm/retry）
        last_text = self._last_assistant_text.get(agent_key, "")
        retry_evidence = bool(last_text and _RETRY_TEXT_RE.search(last_text)) or \
            any(e.event_type == "llm/retry" for e in window)
        if retry_evidence:
            score += SCORE_RETRY_LANGUAGE
            reasons.append(StuckReason.RETRY_LANGUAGE)

        # +2 相同根因连续 3 次没有改善
        # 检查最近的失败是否共享同一错误码指纹，且无成功间隔
        if len(failures) >= 3:
            recent_fails = failures[-3:]
            fps = [_error_code_fingerprint({"errorCode": e.error_code, "errorText": e.error_text})
                   for e in recent_fails]
            # 检查是否有至少 3 个相同的非空指纹
            from collections import Counter
            fp_counts = Counter(f for f in fps if f)
            if fp_counts and fp_counts.most_common(1)[0][1] >= 3:
                # 确认中间没有成功（只按结果事件计；tool/call / llm/retry 不算成功）
                fail_indices = [i for i, e in enumerate(outcome_events) if not e.ok][-3:]
                if len(fail_indices) >= 3:
                    has_success_between = any(
                        outcome_events[i].ok
                        for i in range(fail_indices[0], fail_indices[-1] + 1)
                    )
                    if not has_success_between:
                        score += SCORE_NO_PROGRESS_SAME_CAUSE
                        reasons.append(StuckReason.NO_PROGRESS_SAME_CAUSE)

        # ---- 步骤 3：比较旧分数，发射变化 ----
        old_score = self._scores.get(agent_key, 0)
        self._scores[agent_key] = score

        if score != old_score:
            is_peak = score > old_score
            self.score_changed.emit(agent_key, score, reasons, is_peak)

        # ---- 步骤 4：阈值判断 ----
        # 检查是否达到干预推荐阈值
        if score >= self._intervene_threshold:
            # 冷却检查
            last_time = self._last_intervene.get(agent_key, 0.0)
            last_score = self._last_intervene_score.get(agent_key, 0)
            cooldown = self._cooldown_seconds
            # 分数显著升高（超过 1 分）可提前重发
            if now - last_time >= cooldown or (score - last_score) >= 2:
                self._last_intervene[agent_key] = now
                self._last_intervene_score[agent_key] = score
                # 选最严重的原因为主原因
                primary = self._pick_primary_reason(reasons, score)
                self.intervention_recommended.emit(agent_key, {
                    "type": "pet/intervention-recommended",
                    "reason": primary,
                    "severity": StuckSeverity.RECOMMEND,
                    "score": score,
                    "reasons": reasons,
                })
        elif score >= self._worried_threshold:
            # 担忧等级（仅动画，无气泡）
            # 不频繁发射：只在分数首次进入此区间时
            if old_score < self._worried_threshold:
                primary = self._pick_primary_reason(reasons, score)
                self.intervention_recommended.emit(agent_key, {
                    "type": "pet/intervention-recommended",
                    "reason": primary,
                    "severity": StuckSeverity.WORRIED,
                    "score": score,
                    "reasons": reasons,
                })
        else:
            # 分数回到正常区间
            if old_score > 0 and old_score >= self._worried_threshold:
                self.stuck_resolved.emit(agent_key)

    # ------------------------------------------------------------ 内部

    def _reset(self, agent_key: str) -> None:
        """重置指定 Agent 的卡住状态。"""
        had_score = self._scores.get(agent_key, 0) > 0
        self._windows.pop(agent_key, None)
        self._last_assistant_text.pop(agent_key, None)
        self._scores.pop(agent_key, None)
        self._last_intervene.pop(agent_key, None)
        self._last_intervene_score.pop(agent_key, None)
        if had_score:
            self.stuck_resolved.emit(agent_key)

    def _prune_one(self, window: list[ToolEvent]) -> None:
        """移除窗口内超过时限的事件。"""
        if not window:
            return
        cutoff = self._clock() - self._window_seconds
        while window and window[0].ts < cutoff:
            window.pop(0)

    def _prune(self) -> None:
        """定时清理全部 Agent 窗口中的过期事件。"""
        now = self._clock()
        cutoff = now - self._window_seconds
        for agent_key in list(self._windows):
            w = self._windows[agent_key]
            while w and w[0].ts < cutoff:
                w.pop(0)
            if not w:
                del self._windows[agent_key]
                self._last_assistant_text.pop(agent_key, None)
                self._scores.pop(agent_key, None)

    @staticmethod
    def _pick_primary_reason(reasons: list[str], score: int) -> str:
        """从触发原因列表中选主原因（按优先级）。"""
        priority = [
            StuckReason.NO_PROGRESS_SAME_CAUSE,
            StuckReason.REPEATED_TIMEOUTS,
            StuckReason.CONSECUTIVE_FAILURES,
            StuckReason.SAME_GOAL_LOOPING,
            StuckReason.SIMILAR_ERROR_TEXT,
            StuckReason.RETRY_LANGUAGE,
        ]
        for p in priority:
            if p in reasons:
                return p
        return reasons[0] if reasons else "unknown"


# ---------------------------------------------------------------------------
# 帮助函数：生成提醒文案
# ---------------------------------------------------------------------------

def stuck_reminder_text(agent_name: str, custom_text: str = "") -> str:
    """生成卡住建议介入文案。"""
    if custom_text:
        return custom_text.replace("{name}", agent_name)
    return DEFAULT_STUCK_REMINDER.replace("{name}", agent_name)