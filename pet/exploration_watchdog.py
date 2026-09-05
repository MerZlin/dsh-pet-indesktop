# -*- coding: utf-8 -*-
"""Agent Exploration Loop Watchdog.

This module deliberately consumes the normalized DSH event stream instead of
counting raw tool calls.  A step is the unit of agency: parallel calls in one
step are merged, while sessions remain isolated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("dsh-pet-standalone")
WATCHDOG_VERSION = "exploration-watchdog-2026-09-01.1"


class WatchdogClass(str, Enum):
    SEARCH_WEB = "SEARCH_WEB"
    SEARCH_CODE = "SEARCH_CODE"
    READ = "READ"
    GLOB = "GLOB"
    NAVIGATION = "NAVIGATION"
    THINK = "THINK"
    EDIT = "EDIT"
    RUN = "RUN"
    TEST = "TEST"
    OTHER = "OTHER"


class WatchdogMacro(str, Enum):
    EXPLORATION = "EXPLORATION"
    ACTION = "ACTION"
    OTHER = "OTHER"


class JudgeVerdict(str, Enum):
    UNKNOWN = "UNKNOWN"
    NORMAL = "NORMAL"
    REPLAN = "REPLAN"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


_EXPLORATION = frozenset({
    WatchdogClass.SEARCH_WEB, WatchdogClass.SEARCH_CODE,
    WatchdogClass.READ, WatchdogClass.GLOB, WatchdogClass.NAVIGATION,
    WatchdogClass.THINK,
})
_ACTION = frozenset({WatchdogClass.EDIT, WatchdogClass.RUN, WatchdogClass.TEST})
_SEARCH_WORDS = re.compile(r"search|web.?search|browser|curl|wget|fetch", re.I)
_CODE_SEARCH_WORDS = re.compile(r"\b(?:grep|rg|ripgrep)\b|search.?files|code.?search", re.I)
_READ_WORDS = re.compile(r"^read(?:_file|_files)?$|cat|head|tail|view|open.?file|file.?read", re.I)
_GLOB_WORDS = re.compile(r"glob|find.?files|list.?files", re.I)
_NAV_WORDS = re.compile(r"^(pwd|ls|dir|cd|cwd|getcwd|tree|stat|which|realpath)$", re.I)
_THINK_WORDS = re.compile(r"think|reason|plan|analy[sz]e|reflect|deliberat", re.I)
_EDIT_WORDS = re.compile(r"edit|write|patch|create.?file|append|modify|replace|insert", re.I)
_TEST_WORDS = re.compile(r"pytest|playwright|jest|vitest|mocha|cypress|test|lint|typecheck|mypy", re.I)
_RUN_WORDS = re.compile(r"bash|pwsh|powershell|shell|exec|command|terminal|run", re.I)
_COMMAND_READ_WORDS = re.compile(r"^(get-content|type|cat|head|tail|read|read-file)\b", re.I)
_COMMAND_SEARCH_WORDS = re.compile(r"^(grep|rg|ripgrep|select-string|findstr)\b", re.I)
_COMMAND_GLOB_WORDS = re.compile(r"^(glob|find-files)\b", re.I)
_COMMAND_SEMANTIC_ENTRY = re.compile(
    r"\b(?:get-content|select-string|findstr|ripgrep|grep|rg|glob|find-files|"
    r"pytest|playwright|npm|pnpm|yarn|git|python|node|cargo|dotnet|"
    r"type|cat|head|tail|pwd|ls|dir|cd|tree|stat|which|realpath)\b",
    re.I,
)
_CONTROL_QUEUE_WORDS = re.compile(r"dsh-pet-bridge[\\/]watchdog-(?:request|response)-|watchdog-(?:request|response)-[^\s]*\.json", re.I)


def _text(value, limit=240) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _args_obj(record: dict):
    args = record.get("args", record.get("arguments", record.get("input", "")))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return args
    return args


def _canonical_command(command) -> str:
    """Canonicalize the executed portion of a shell command.

    Shell tools often carry a separate presentation label, and PowerShell
    expressions may print such a label before invoking the real command.  We
    anchor at the first known executable/cmdlet and retain its arguments, so
    real path/query/flag changes remain visible while card wording does not.
    """
    value = _text(command, 800)
    if not value:
        return ""
    match = _COMMAND_SEMANTIC_ENTRY.search(value)
    if match:
        value = value[match.start():]
    value = re.sub(r"\s+", " ", value).strip()
    return _text(value, 500)


def _target(record: dict, tool: str) -> str:
    """Extract a short target without retaining full command/output contents."""
    command = record.get("command")
    if command:
        return _canonical_command(command)
    for key in ("target", "filePath", "path", "pattern", "query", "searchQuery"):
        value = record.get(key)
        if value:
            return _text(value)
    args = _args_obj(record)
    if isinstance(args, dict):
        for key in ("filePath", "path", "target", "pattern", "query", "searchQuery", "command", "cmd", "shell", "script"):
            value = args.get(key)
            if value:
                return _canonical_command(value) if key in {"command", "cmd", "shell", "script"} else _text(value)
        return _text(" ".join(str(k) for k in sorted(args)))
    args_key = _text(record.get("argsKey"), 180)
    match = re.search(r"(?:^|,)argv0:([^,]+)", args_key, re.I)
    if match:
        return _canonical_command(match.group(1).strip('\\\"\''))
    return _text(args) or _text(tool)


def classify_event(record: dict) -> WatchdogClass:
    event = _text(record.get("event")).lower()
    tool = _text(record.get("tool") or record.get("toolName") or record.get("name"))
    text = f"{tool} {_text(record.get('argsKey'))} {_target(record, tool)}"
    if _CONTROL_QUEUE_WORDS.search(text):
        return WatchdogClass.RUN
    command_word = _target(record, tool).split(None, 1)[0].lower() if _target(record, tool) else ""
    if "reasoning" in event or "think" in event or _THINK_WORDS.search(tool):
        return WatchdogClass.THINK
    if "web_search" in event or "websearch" in event or _SEARCH_WORDS.search(tool):
        return WatchdogClass.SEARCH_WEB
    if _CODE_SEARCH_WORDS.search(text):
        return WatchdogClass.SEARCH_CODE
    if _COMMAND_SEARCH_WORDS.search(command_word):
        return WatchdogClass.SEARCH_CODE
    if _COMMAND_GLOB_WORDS.search(command_word):
        return WatchdogClass.GLOB
    if _COMMAND_READ_WORDS.search(command_word):
        return WatchdogClass.READ
    # Compatibility with older bridge records that only retained argv0 in
    # argsKey.  This is a generic command hint, not a tool/file special case.
    if re.search(r"\b(?:get-content|type|cat|head|tail|read|read-file)\b",
                 _text(record.get("argsKey")), re.I):
        return WatchdogClass.READ
    if _GLOB_WORDS.search(tool):
        return WatchdogClass.GLOB
    if _NAV_WORDS.search(tool) or _NAV_WORDS.search(command_word):
        return WatchdogClass.NAVIGATION
    if _TEST_WORDS.search(text) and (event in {"command/run", "exec_command_begin", "tool/call"} or "test" in text.lower()):
        return WatchdogClass.TEST
    if _EDIT_WORDS.search(tool):
        return WatchdogClass.EDIT
    if _RUN_WORDS.search(tool) or event in {"command/run", "exec_command_begin", "tool-workflow/run-start"}:
        return WatchdogClass.RUN
    if _READ_WORDS.search(tool) or event in {"fs/read", "file/read", "read_file"}:
        return WatchdogClass.READ
    return WatchdogClass.OTHER


def macro_of(cls: WatchdogClass) -> WatchdogMacro:
    if cls in _EXPLORATION:
        return WatchdogMacro.EXPLORATION
    if cls in _ACTION:
        return WatchdogMacro.ACTION
    return WatchdogMacro.OTHER


def make_fingerprint(record: dict, cls: WatchdogClass, target: str) -> str:
    tool = _text(record.get("tool") or record.get("toolName") or record.get("name")).lower()
    args_key = _text(record.get("argsKey") or record.get("fingerprint") or _args_obj(record), 180)
    raw = f"{cls.value}|{tool}|{target.lower()}|{args_key.lower()}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def _think_key(record: dict, target: str) -> str:
    """Stable key for repeated reasoning, excluding presentation noise."""
    text = (record.get("text") or record.get("summary") or
            record.get("content") or target or "")
    text = re.sub(r"\s+", " ", _text(text, 360)).strip().lower()
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:20] if text else ""


def build_judge_prompt(goal: str, steps: list[dict], risk: dict) -> str:
    compact = []
    for step in steps[-10:]:
        compact.append({
            "step": step.get("step"),
            "behaviors": step.get("behaviors", []),
            "targets": step.get("targets", [])[:4],
            "think": _text(step.get("think", ""), 160),
            "evidence": _text(step.get("evidence", ""), 160),
            "action": bool(step.get("action")),
        })
    return (
        "判断 Agent 是否陷入低信息增益的重复探索循环。只输出 JSON，不要 Markdown。\n"
        "verdict 必须是 NORMAL、REPLAN、ASK_USER、STOP 之一；同时返回 reason、next_action、confidence。\n"
        f"用户目标：{_text(goal, 600)}\n"
        f"近期步骤：{json.dumps(compact, ensure_ascii=False)}\n"
        f"风险：{json.dumps(risk, ensure_ascii=False)}"
    )


def build_replan_prompt(goal: str, steps: list[dict], judge: dict | None = None) -> str:
    """Build the one-shot context sent when the user presses Replan.

    Unlike the detector Judge, this deliberately includes every compact event
    captured in the recent steps, so the planning call can see the actual
    tool arguments/results rather than only the risk summary.
    """
    return (
        "你是 Agent 的重新规划助手。请基于下面完整的近期步骤，输出一段给 Agent 的可执行重新规划指令。\n"
        "要求：总结当前目标、最强假设、支持/反对证据，并指定一个最小可证伪实验；在实验完成前不要继续重复 Search/Read。\n"
        f"当前用户目标：{_text(goal, 800)}\n"
        f"近期步骤完整上下文：{json.dumps(steps[-10:], ensure_ascii=False)[:10000]}\n"
        f"检测原因：{json.dumps(judge or {}, ensure_ascii=False)[:1200]}"
    )


def parse_judge_result(value) -> dict:
    if isinstance(value, dict):
        data = value
    else:
        raw = _text(value, 3000)
        try:
            data = json.loads(raw)
        except Exception:
            upper = raw.upper()
            return {"verdict": JudgeVerdict.UNKNOWN.value, "reason": "judge-invalid-response", "next_action": "", "confidence": 0.0}
    verdict = str(data.get("verdict", "UNKNOWN")).upper()
    if verdict not in {v.value for v in JudgeVerdict}:
        verdict = JudgeVerdict.UNKNOWN.value
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"verdict": verdict, "reason": _text(data.get("reason"), 300),
            "next_action": _text(data.get("next_action"), 300), "confidence": confidence}


class _Step:
    def __init__(self, step):
        self.step = step
        self.classes = set()
        self.targets = set()
        self.fingerprints = set()
        self.think = ""
        self.evidence = ""
        self.evidence_new = False
        self.action = False
        self.events = []
        self.details = []
        # Keep multiplicity inside one Agent step without pretending parallel
        # calls are separate decisions.  This catches a five-call identical
        # burst while Search x3 still occupies one sliding-window step.
        self.call_classes = Counter()
        self.call_targets = Counter()
        self.call_fingerprints = Counter()
        self.exploration_targets = set()
        self.exploration_fingerprints = set()
        self.think_fingerprints = Counter()
        self.think_chars = 0
        self.think_started_at = None
        self.think_active = False
        self.long_think_reported = False
        self.closed = False

    def payload(self):
        return {"step": self.step, "behaviors": sorted(c.value for c in self.classes),
                "targets": sorted(self.targets), "fingerprints": sorted(self.fingerprints),
                "think": self.think, "evidence": self.evidence, "evidence_new": self.evidence_new, "action": self.action,
                "events": list(self.details),
                "call_count": sum(self.call_fingerprints.values()),
                "think_count": sum(self.think_fingerprints.values()),
                "think_chars": self.think_chars,
                "think_active": self.think_active,
                "think_duration_seconds": round(max(0.0, time.monotonic() - self.think_started_at), 1)
                if self.think_active and self.think_started_at is not None else 0}


class ExplorationWatchdog(QObject):
    warning = Signal(str, object)
    judge_required = Signal(str, object)
    judge_result = Signal(str, object)
    resolved = Signal(str)

    def __init__(self, parent=None, *, judge=None, goal_provider=None, timeout=8.0, cooldown_steps=3):
        super().__init__(parent)
        log.info("watchdog version=%s source=%s", WATCHDOG_VERSION, __file__)
        self.enabled = True
        self.mode = "manual"
        self.warning_threshold = 3
        self.control_threshold = 5
        self.timeout = float(timeout)
        self.cooldown_steps = int(cooldown_steps)
        self.early_grace_seconds = 5 * 60
        self.long_run_seconds = 10 * 60
        self.long_think_seconds = 120
        self.judge = judge
        self.goal_provider = goal_provider
        self._states = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dsh-watchdog")
        self._lock = threading.RLock()
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(1000)
        self._think_timer.timeout.connect(self._poll_long_think)
        self._think_timer.start()

    def close(self):
        self._think_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def set_judge(self, judge):
        self.judge = judge

    def configure(self, config: dict):
        config = config if isinstance(config, dict) else {}
        # 缺少新字段的旧配置按新功能默认值处理；旧 pattern_detect 不应把
        # 新 Watchdog 默默关掉，避免升级后功能状态取决于历史实验开关。
        self.enabled = bool(config.get("exploration_watchdog_enabled", True))
        self.mode = str(config.get("exploration_watchdog_mode", "manual")).lower()
        if self.mode not in {"manual", "auto"}:
            self.mode = "manual"
        self.warning_threshold = int(config.get("exploration_watchdog_warning_threshold", 3))
        self.control_threshold = int(config.get("exploration_watchdog_control_threshold", 5))
        self.timeout = float(config.get("exploration_watchdog_judge_timeout", 8.0))
        self.cooldown_steps = int(config.get("exploration_watchdog_cooldown_steps", 3))
        self.early_grace_seconds = max(60, int(config.get("exploration_watchdog_early_grace_minutes", 5)) * 60)
        self.long_run_seconds = max(self.early_grace_seconds, int(config.get("exploration_watchdog_long_run_minutes", 10)) * 60)
        self.long_think_seconds = max(10, int(config.get("exploration_watchdog_long_think_seconds", 120)))

    def grant_grace(self, session: str, minutes: int | None = None) -> None:
        """Give a session time to absorb a user/system continuation decision."""
        with self._lock:
            state = self._states.get(session)
            if state is None:
                return
            seconds = self.early_grace_seconds if minutes is None else max(60, int(minutes) * 60)
            state["grace_until"] = time.monotonic() + seconds
            # Do not immediately re-inspect the same history after the button
            # action; new Agent steps must arrive first.
            state["last_inspected_seq"] = state["seq"] + (1 if state["current"] else 0)

    def reset(self, session_key: str):
        with self._lock:
            self._states.pop(session_key, None)
        self.resolved.emit(session_key)

    def feed_record(self, agent_key: str, record: dict):
        if not self.enabled or not isinstance(record, dict):
            return
        event = _text(record.get("event"))
        session = _text(record.get("sessionId") or record.get("session_id") or agent_key) or agent_key
        if event in {"AgentStatus"} and record.get("state") in {"idle", "sleeping"}:
            if session == agent_key:
                with self._lock:
                    sessions = list(self._states)
                for key in sessions:
                    self.reset(key)
            else:
                self.reset(session)
            return
        if event in {"turn/start", "turn/end", "task_complete", "thread/rolled_back"}:
            self.reset(session)
            return
        if event == "step/end":
            with self._lock:
                state = self._states.get(session)
                if state and state.get("current") is not None:
                    state["current"].think_active = False
            return
        if event == "user/message":
            goal = _text(record.get("text") or record.get("content") or record.get("summary"), 1200)
            now = time.monotonic()
            with self._lock:
                state = self._states.setdefault(session, {"steps": OrderedDict(), "current": None,
                    "last_inspected_seq": 0, "seq": 0, "last_level": "", "goal": "",
                    "started_at": now, "grace_until": now + self.early_grace_seconds,
                    "agent_name": _text(record.get("agentName") or record.get("agent") or agent_key),
                    "agent_key": agent_key})
                if goal:
                    state["goal"] = goal
            return
        cls = classify_event(record)
        if cls is WatchdogClass.OTHER:
            return
        step = _text(record.get("step") or record.get("turnId") or record.get("turn") or f"seq:{record.get('eventSeq', id(record))}")
        target = _target(record, _text(record.get("tool") or record.get("name")))
        fp = make_fingerprint(record, cls, target)
        with self._lock:
            now = time.monotonic()
            state = self._states.setdefault(session, {"steps": OrderedDict(), "current": None,
                "last_inspected_seq": 0, "seq": 0, "last_level": "", "goal": "",
                "started_at": now, "grace_until": now + self.early_grace_seconds,
                "agent_name": _text(record.get("agentName") or record.get("agent") or agent_key),
                "agent_key": agent_key})
            current = state["current"]
            if current is None or current.step != step:
                if current is not None:
                    current.closed = True
                    state["seq"] += 1
                    state["steps"][state["seq"]] = current
                current = _Step(step)
                state["current"] = current
            current.classes.add(cls)
            # Think is context around a decision, not itself a target/tool
            # decision.  Keeping it out of generic target/fingerprint counts
            # prevents the normal Think→Tool cadence from looking repetitive.
            if cls is not WatchdogClass.THINK:
                current.targets.add(target)
                current.fingerprints.add(fp)
                if cls in _EXPLORATION:
                    current.exploration_targets.add(target)
                    current.exploration_fingerprints.add(fp)
            current.events.append(event)
            current.details.append({k: _text(record.get(k), 500) for k in (
                "event", "tool", "toolName", "argsKey", "target", "filePath",
                "path", "pattern", "query", "command", "text", "summary",
                "resultSummary", "evidence", "errorText", "ok", "timeout",
            ) if record.get(k) is not None})
            if cls in _EXPLORATION and event in {"tool/call", "command/run", "tool-workflow/run-start",
                         "exec_command_begin", "mcp_tool_call_begin"}:
                current.call_classes[cls] += 1
                current.call_targets[target] += 1
                current.call_fingerprints[fp] += 1
            current.action = current.action or cls in _ACTION
            if cls is WatchdogClass.THINK:
                raw_think = record.get("text") or record.get("summary") or record.get("content") or ""
                current.think = _text(raw_think, 200)
                current.think_chars += len(str(raw_think))
                if not current.think_active:
                    current.think_started_at = now
                    current.long_think_reported = False
                current.think_active = True
                think_key = _think_key(record, target)
                if think_key:
                    current.think_fingerprints[think_key] += 1
            else:
                current.think_active = False
            if event.endswith("result") or event.endswith("end"):
                current.evidence = _text(record.get("evidence") or record.get("resultSummary") or record.get("errorText"), 200)
                current.evidence_new = str(record.get("evidenceStatus") or "") == "new"
            if len(state["steps"]) > 20:
                state["steps"].popitem(last=False)
            payload = self._evaluate_locked(session, state)
        if payload:
            self._emit_decision(session, payload)

    def _poll_long_think(self):
        """Detect a still-running Think without waiting for a Think-end event."""
        pending = []
        now = time.monotonic()
        with self._lock:
            for session, state in self._states.items():
                current = state.get("current")
                if current is None or not current.think_active or current.think_started_at is None:
                    continue
                duration = now - current.think_started_at
                if duration < self.long_think_seconds or current.long_think_reported:
                    continue
                current.long_think_reported = True
                current_seq = state["seq"] + 1
                pending.append((session, {
                    "type": "pet/exploration-watchdog",
                    "level": "judge",
                    "risk": 0,
                    "reasons": ["单次 Think 持续超过阈值"],
                    "steps": [s.payload() for s in self._window(state, 10)],
                    "session_id": session,
                    "mode": self.mode,
                    "goal": state.get("goal", ""),
                    "agent_key": state.get("agent_key", ""),
                    "agent_name": state.get("agent_name") or state.get("agent_key", ""),
                    "elapsed_seconds": round(max(0.0, now - state.get("started_at", now))),
                    "threshold_phase": "long-think",
                    "warning_threshold": self.warning_threshold,
                    "control_threshold": self.control_threshold,
                    "long_think_seconds": self.long_think_seconds,
                    "think_duration_seconds": round(duration, 1),
                    "current_step": current_seq,
                }))
        for session, payload in pending:
            self.judge_required.emit(session, payload)

    def _window(self, state, n):
        items = list(state["steps"].values())
        if state["current"] is not None:
            items.append(state["current"])
        return items[-n:]

    def _score(self, w6, w10):
        score = 0
        reasons = []
        class_repeat = False
        target_repeat = False
        fingerprint_points = 0
        think_fingerprint_points = 0
        all_items = [("W6", w6), ("W10", w10)]
        for label, items in all_items:
            # Think frequency is expected in normal agent operation and has no
            # standalone risk meaning.  It is evaluated relationally below.
            classes = Counter(c for s in items for c in s.classes
                              if c in _EXPLORATION and c is not WatchdogClass.THINK)
            targets = Counter(t for s in items for t in s.exploration_targets)
            fps = Counter(f for s in items for f in s.exploration_fingerprints)
            class_limit, target_limit, fp_limit = ((3, 3, 2) if label == "W6" else (4, 4, 3))
            # A few Reads are normal while orienting in a codebase.  Only let
            # Read alone contribute the class-repeat point after four steps;
            # target/fingerprint repetition still catches a genuine loop.
            if label == "W6" and len(classes) == 1 and next(iter(classes), None) is WatchdogClass.READ:
                class_limit = 4
            unique_targets = len({t for s in items for t in s.exploration_targets})
            if classes and max(classes.values()) >= class_limit:
                class_repeat = True; reasons.append(f"{label} 同类重复")
            if targets and max(targets.values()) >= target_limit and unique_targets <= 1:
                target_repeat = True; reasons.append(f"{label} target 重复")
            if fps and max(fps.values()) >= fp_limit:
                fingerprint_points = max(fingerprint_points, 2 if label == "W6" else 3)
                reasons.append(f"{label} fingerprint 重复")
            explore = sum(bool(set(s.classes) & _EXPLORATION) for s in items)
            actions = sum(bool(set(s.classes) & _ACTION) for s in items)
            unique_targets = len({t for s in items for t in s.exploration_targets})
            if label == "W6" and explore >= 5 and unique_targets <= 1:
                score += 3; reasons.append("W6 探索密集且 target 单一")
            # 高 target diversity 本身代表持续获得新信息；“无行动”规则只在
            # 探索对象也高度收敛时成立，避免误伤正常的资料梳理阶段。
            has_new_evidence = unique_targets >= 5 or any(s.evidence_new for s in items)
            if label == "W10" and explore >= 8 and actions == 0 and not has_new_evidence:
                score += 2; reasons.append("W10 探索密集且无行动")
            think_fps = Counter(k for s in items for k in s.think_fingerprints)
            if think_fps:
                think_limit = 3 if label == "W6" else 4
                if max(think_fps.values()) >= think_limit:
                    points = 1
                    think_fingerprint_points = max(think_fingerprint_points, points)
                    reasons.append(f"{label} 重复 Think")
        # Intra-step calls are not extra decisions, but a large batch of the
        # exact same execution is still useful risk evidence.  Three parallel
        # calls remain below the default warning threshold; five identical
        # calls produce a warning even during the startup grace period.
        max_burst = max(
            (max(step.call_fingerprints.values(), default=0) for step in w10),
            default=0,
        )
        if max_burst >= 5:
            score += 4; reasons.append("单步相同命令批量重复")
        elif max_burst >= 3:
            score += 2; reasons.append("单步相同命令重复")

        # Think-loop detection is relational: compare the decision/evidence
        # state produced after each completed reasoning step.  Frequency and
        # ratio alone are intentionally ignored.
        completed = [s for s in w10 if s.closed]
        cycles = []
        for index, item in enumerate(completed):
            if not item.think_fingerprints:
                continue
            # A reasoning event can occupy its own DSH step.  Associate the
            # following non-Think steps with it until the next reasoning step,
            # so Think→Read is one cycle rather than a false Think-only cycle.
            segment = [item]
            cursor = index + 1
            while cursor < len(completed) and not completed[cursor].think_fingerprints:
                segment.append(completed[cursor])
                cursor += 1
            classes = tuple(sorted({c.value for s in segment for c in s.classes
                                    if c is not WatchdogClass.THINK}))
            targets = tuple(sorted({t for s in segment for t in s.exploration_targets}))
            fingerprints = tuple(sorted({f for s in segment for f in s.exploration_fingerprints}))
            cycles.append({
                "signature": (classes, targets, fingerprints),
                "progress": any(s.action or s.evidence for s in segment),
                "think_chars": item.think_chars,
            })

        recent3 = cycles[-3:]
        if len(recent3) == 3 and not any(c["progress"] for c in recent3):
            signatures = [c["signature"] for c in recent3]
            if all(not any(part for part in sig) for sig in signatures):
                score += 3; reasons.append("连续 Think 未形成可执行决策")
            elif len(set(signatures)) == 1:
                score += 3; reasons.append("Think 后决策状态未变化")
            if sum(c["think_chars"] for c in recent3) >= 600 and len(set(signatures)) <= 1:
                score += 1; reasons.append("推理输出增长但无状态推进")

        recent4 = cycles[-4:]
        if len(recent4) == 4 and not any(c["progress"] for c in recent4):
            signatures = [c["signature"] for c in recent4]
            if signatures[0] == signatures[2] and signatures[1] == signatures[3] \
                    and signatures[0] != signatures[1]:
                score += 2; reasons.append("Think 后决策在两个状态间往返")
        # W6/W10 是两个观察尺度，不把同一风险维度重复加倍；否则正常的
        # 多目标 Read 序列会因同时满足两个窗口的“同类重复”而误报。
        score += int(class_repeat) + (2 if target_repeat else 0) + fingerprint_points
        # W6/W10 are two views of the same Think repetition; only the stronger
        # window contributes, avoiding accidental double penalties.
        score += think_fingerprint_points
        if any(s.action for s in w6 + w10):
            score -= 2; reasons.append("近期有 Edit/Run/Test")
        unique = len({t for s in w10 for t in s.exploration_targets})
        if unique >= 5:
            score -= 1; reasons.append("target diversity 较高")
        # A new target after a Think, or concrete evidence from a tool result,
        # is a progression signal.  Do not interrupt a search that is visibly
        # narrowing the hypothesis.
        seen = set()
        thought_then_new_target = False
        had_think = False
        for item in w10:
            if item.think:
                had_think = True
            if had_think and any(t not in seen for t in item.targets):
                thought_then_new_target = True
            seen.update(item.targets)
        if thought_then_new_target:
            score -= 1; reasons.append("Think 后访问新 target")
        if any(s.evidence_new for s in w10):
            score -= 1; reasons.append("近期获得新证据")
        return max(0, score), reasons

    def _evaluate_locked(self, session, state):
        current_seq = state["seq"] + (1 if state["current"] else 0)
        if current_seq - state["last_inspected_seq"] < self.cooldown_steps and state["last_inspected_seq"]:
            return None
        elapsed = max(0.0, time.monotonic() - state.get("started_at", time.monotonic()))
        in_grace = time.monotonic() < state.get("grace_until", 0.0)
        if in_grace:
            warning_threshold = self.warning_threshold + 1
            control_threshold = self.control_threshold + 1
            phase = "early/grace"
        elif elapsed >= self.long_run_seconds:
            warning_threshold = max(1, self.warning_threshold - 1)
            control_threshold = max(warning_threshold + 1, self.control_threshold - 1)
            phase = "long-run"
        else:
            warning_threshold = self.warning_threshold
            control_threshold = self.control_threshold
            phase = "normal"
        w6, w10 = self._window(state, 6), self._window(state, 10)
        score, reasons = self._score(w6, w10)
        if score < warning_threshold:
            return None
        level = "warning" if score < control_threshold else "judge"
        state["last_inspected_seq"] = current_seq
        payload = {"type": "pet/exploration-watchdog", "level": level, "risk": score,
                   "generation_id": uuid.uuid4().hex,
                   "reasons": reasons, "steps": [s.payload() for s in w10],
                   "riskScore": score,
                   "targetCount": len({t for s in w10 for t in s.exploration_targets}),
                   "targets": sorted({t for s in w10 for t in s.exploration_targets}),
                   "session_id": session, "mode": self.mode, "goal": state.get("goal", ""),
                   "agent_key": state.get("agent_key", ""),
                   "agent_name": state.get("agent_name") or state.get("agent_key", ""),
                   "elapsed_seconds": round(elapsed), "threshold_phase": phase,
                   "warning_threshold": warning_threshold,
                   "control_threshold": control_threshold}
        return payload

    def _emit_decision(self, session, payload):
        if payload["level"] == "warning":
            self.warning.emit(session, payload)
            return
        self.judge_required.emit(session, payload)

    def judge_payload(self, session: str, payload: dict):
        """Run the injected judge off the Qt thread and emit its structured result."""
        if self.judge is None:
            payload["judge"] = {"verdict": JudgeVerdict.UNKNOWN.value, "reason": "judge-unavailable", "next_action": "", "confidence": 0.0, "goal": payload.get("goal", "")}
            self.judge_result.emit(session, payload)
            return
        goal = self.goal_provider(session) if callable(self.goal_provider) else payload.get("goal", "")
        payload.setdefault("goal", _text(goal, 600))
        prompt = build_judge_prompt(goal, payload.get("steps", []), {"risk": payload.get("risk"), "reasons": payload.get("reasons", [])})
        future = self._executor.submit(self.judge, prompt)

        def done(f):
            try:
                raw = f.result(timeout=self.timeout)
                log.info("watchdog judge response generation=%s session=%s raw=%s",
                         payload.get("generation_id", ""), session, _text(raw, 300))
                payload["judge"] = parse_judge_result(raw)
            except Exception as exc:
                log.warning("exploration watchdog judge failed generation=%s session=%s error=%s",
                            payload.get("generation_id", ""), session, exc)
                payload["judge"] = parse_judge_result("")
            self.judge_result.emit(session, payload)
        # callback 本身只会在 future 完成后运行；另起等待线程，才能让
        # timeout 真正限制 Judge，不把 Qt/Watchdog 线程卡住。
        threading.Thread(target=done, args=(future,), daemon=True).start()
