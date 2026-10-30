"""统一 Agent 事件协议：把一行 JSONL 归一成六态。

纯解析，不碰文件、不碰 Qt。协议见 ``docs/AGENT_LINK_PROTOCOL.md`` §2：
写方每行一个 JSON 对象，``state`` 直接给六态，或用 ``event`` 让桌宠按内置表映射。

DSH 桥接（``integrations/dsh-pet-bridge``）也走这套字段：它把 DSH 的
``turn/start``、``tool/call`` 之类的原始事件名写进 ``event``，另外带 ``tool``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: 读方统一词汇（协议 §2.3）。
SIX_STATES = ("thinking", "working", "attention", "error", "idle", "sleeping")
#: 忙碌态：只有这两个状态会驱动联动动作与「干活」气泡。
BUSY_STATES = ("thinking", "working")

#: 单行上限。超长行一律整行丢弃：被拼接的畸形记录不该进内存，也不该驱动动画。
MAX_LINE_BYTES = 4096
#: 展示字段的字符上限（agent 标识 / 工具名 / 事件名）。
MAX_FIELD_CHARS = 64
#: 桌宠控制命令的正文/动作名上限。
MAX_COMMAND_TEXT = 200
MAX_COMMAND_NAME = 64
MAX_COMMAND_MS = 10000

#: 事件名 → 六态。Claude Code hooks 名、DSH 桥接名与裸状态名共用一张表。
#: 表外的事件名一律忽略（绝不默认当成 working）：transcript 类密集写入会把
#: 「不认识」变成持续抽搐。
EVENT_STATES: dict[str, str] = {
    # 协议 §2.4：Claude Code hooks 兼容名
    "SessionStart": "idle",
    "SessionEnd": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Stop": "attention",
    "SubagentStop": "attention",
    "StopFailure": "error",
    # DSH 桥接（index.js 的 STATE_EVENT_TYPES + 显式事件）
    "turn/start": "thinking",
    "user/message": "thinking",
    "step/start": "working",
    "step/end": "working",
    "command/run": "working",
    "command/done": "working",
    "tool-workflow/run-start": "working",
    "tool-workflow/run-end": "working",
    "tool/call": "working",
    "tool/result": "working",
    "assistant/message": "working",
    "llm/retry": "working",
    "turn/end": "attention",
    "execution/failed": "error",
    "agent/request-error": "error",
    "llm_error": "error",
    "approval/asked": "attention",
    "approval/request": "attention",
    "question/requested": "attention",
    "cordis/request-run": "attention",
    # 裸状态名（写方图省事直接写 event）
    "thinking": "thinking",
    "working": "working",
    "attention": "attention",
    "error": "error",
    "idle": "idle",
    "sleeping": "sleeping",
}

#: ``turn/end`` 带这些原因时不算「干完活」——用户中止或被阻塞的回合静默收尾。
QUIET_TURN_END_REASONS = frozenset({"aborted", "blocked", "max_tokens", "unknown"})

#: 工具名 → 用户可读文案（不展示命令全文/路径/参数）。键一律小写。
TOOL_LABELS: dict[str, str] = {
    "read": "正在读文件",
    "write": "正在写文件",
    "edit": "正在改代码",
    "notebookedit": "正在改代码",
    "bash": "正在跑命令",
    "shell": "正在跑命令",
    "pwsh": "正在跑命令",
    "powershell": "正在跑命令",
    "cmd": "正在跑命令",
    "grep": "正在搜索",
    "glob": "正在搜索",
    "search": "正在搜索",
    "memory_search": "正在翻记忆",
    "webfetch": "正在查网页",
    "websearch": "正在查网页",
    "web_search": "正在查网页",
    "web_fetch": "正在查网页",
    "fetch": "正在查网页",
    "browser": "正在查网页",
    "read_page": "正在读网页",
    "task": "正在派活给子代理",
    "todowrite": "正在列计划",
    "ask_user_question": "正在等你回答",
}
UNKNOWN_TOOL_LABEL = "正在调用工具"
#: 过程汇报气泡文案上限（超出截断加「…」）。
ACTIVITY_TEXT_LIMIT = 80


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """一行事件的归一结果。"""

    agent: str
    state: str
    tool: str = ""
    event: str = ""
    writer: str = ""

    @property
    def busy(self) -> bool:
        return self.state in BUSY_STATES


@dataclass(frozen=True, slots=True)
class PetCommand:
    """DSH → 桌宠的控制命令（``event: "pet/command"``）。"""

    cmd: str
    text: str = ""
    name: str = ""
    duration_ms: int = 0
    agent: str = "dsh"
    writer: str = ""


def _text(value: object, limit: int = MAX_FIELD_CHARS) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def normalize_state(record: dict) -> str:
    """显式 ``state`` 优先；否则按 ``event`` 查表；都不认返回空串。"""
    explicit = _text(record.get("state"), 32).lower()
    if explicit in SIX_STATES:
        return explicit
    event = _text(record.get("event"), 96)
    state = EVENT_STATES.get(event, "")
    if not state:
        # 事件名大小写不敏感兜底（写方手写 JSON 时常capitalize）。
        state = EVENT_STATES.get(event.lower(), "")
    if state == "attention" and event == "turn/end":
        reason = _text(record.get("reasonKind") or record.get("reason_kind"), 32).lower()
        if reason in QUIET_TURN_END_REASONS:
            return "idle"
    return state


def parse_line(raw: bytes | str) -> AgentEvent | None:
    """解析一行事件。空行、坏 JSON、未知事件、超长行一律返回 ``None``。"""
    text = _decode(raw).lstrip("\ufeff").strip()
    if not text or len(text) > MAX_LINE_BYTES:
        return None
    try:
        record = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    state = normalize_state(record)
    if not state:
        return None
    agent = _text(record.get("agent") or record.get("agent_key") or record.get("source"))
    tool = _text(record.get("tool")) or _text(record.get("tool_name"))
    event = _text(record.get("event"), 96)
    writer = _text(record.get("inst"), 64)
    return AgentEvent(
        agent=agent.lower(), state=state, tool=tool.lower(), event=event, writer=writer
    )


def parse_command(raw: bytes | str) -> PetCommand | None:
    """解析 ``pet/command`` 行；普通状态事件返回 ``None``。"""
    text = _decode(raw).lstrip("\ufeff").strip()
    if not text or len(text) > MAX_LINE_BYTES:
        return None
    try:
        record = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    if _text(record.get("event"), 96) != "pet/command":
        return None
    cmd = _text(record.get("cmd"), 32).lower()
    if cmd not in {"say", "play_animation", "notify"}:
        return None
    body = _text(record.get("text"), MAX_COMMAND_TEXT)
    name = _text(record.get("name") or record.get("animation"), MAX_COMMAND_NAME)
    try:
        duration = int(record.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration = 0
    duration = max(0, min(MAX_COMMAND_MS, duration))
    if cmd == "play_animation" and not name:
        return None
    if cmd != "play_animation" and not body:
        return None
    agent = _text(record.get("agent") or "dsh", 32)
    return PetCommand(
        cmd=cmd,
        text=body,
        name=name,
        duration_ms=duration,
        agent=agent.lower(),
        writer=writer_of(agent, _text(record.get("inst"), 64)),
    )


def writer_of(record_agent: str, inst: str = "") -> str:
    """写者归属：多实例 DSH 各写一份，靠 ``inst`` 区分；无则用默认槽。"""
    return _text(inst, 64) or "default"


def tool_label(tool: str) -> str:
    key = str(tool or "").strip().lower()
    if not key:
        return ""
    return TOOL_LABELS.get(key, UNKNOWN_TOOL_LABEL)


def activity_text(event: AgentEvent) -> str:
    """过程汇报文案：有工具名就报工具，没有就空（不刷无信息气泡）。"""
    label = tool_label(event.tool)
    if not label:
        return ""
    if len(label) > ACTIVITY_TEXT_LIMIT:
        label = label[: ACTIVITY_TEXT_LIMIT - 1] + "…"
    return label
