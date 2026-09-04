# -*- coding: utf-8 -*-
"""Portable persona phrase template generation."""
from __future__ import annotations

import copy
from typing import Any

from .persona_phrases import phrase_keys

TEMPLATE_VERSION = "persona-phrases/v1"
VARIABLES = {
    "name": "Agent 展示名称（Pet 解析）", "command": "待执行命令（审批参数）", "label": "工具或操作名称（Pet 映射）",
    "body": "问题内容（由 header + question 生成）", "count": "数量/连续次数（Pet 计算）", "reasons": "Watchdog/Judge 风险原因（Pet 格式化）",
    "detail": "安装/桥接错误详情", "text": "显示文本/余额结果",
    "agent": "上游 Agent 标识", "agent_key": "Pet Agent key", "agentName": "上游 Agent 显示名",
    "event": "上游事件名称", "source": "事件来源", "ts": "事件时间戳",
    "sessionId": "会话标识", "sessionName": "会话显示名", "projectName": "项目名称", "label_upstream": "上游会话标签",
    "tool": "原始工具名", "toolName": "审批工具名", "target": "工具目标", "callId": "工具调用 ID",
    "rpcId": "Mux 请求 ID", "requestId": "请求 ID", "approvalId": "审批 ID", "questionRpcId": "问题请求 ID",
    "arguments": "工具参数", "questions": "问题数组", "errorCode": "错误码", "errorMessage": "错误信息", "errorText": "错误文本", "retry": "重试序号",
    "retries": "本轮重试次数", "retryExhausted": "是否重试耗尽", "consecutiveRetryCount": "连续限流次数", "ok": "工具是否成功", "timeout": "是否超时", "resultSummary": "工具结果摘要", "durationMs": "工具耗时",
    "verdict": "Judge 决策", "reason": "Judge/行为模式原因", "class": "行为模式类别", "window": "统计窗口", "risk": "风险评分", "riskScore": "风险评分", "riskReasons": "风险原因数组", "targets": "目标数组", "targetCount": "目标数量", "generation_id": "检测代次", "goal": "任务目标", "outcome": "交互结果", "decision": "用户决定", "provider": "模型服务商", "phase": "控制阶段", "operation": "控制操作", "pluginId": "Cordis 插件 ID", "packageId": "Cordis 包 ID", "mode": "Cordis 运行模式", "purpose": "Cordis 请求用途", "requiresApproval": "是否需要审批",
}
UPSTREAM_FIELDS = {
    "base": ("ts", "agent", "event", "source", "agentName", "sessionId", "sessionName", "projectName", "label"),
    "tool": ("tool", "toolName", "target", "callId", "arguments", "command", "argsKey", "step"),
    "approval": ("rpcId", "requestId", "approvalId", "sessionId", "toolName", "command", "outcome"),
    "question": ("rpcId", "questionRpcId", "callId", "sessionId", "questions", "outcome"),
    "error": ("errorCode", "errorMessage", "errorText", "source", "retryExhausted", "retries"),
    "rate_limit": ("errorCode", "errorMessage", "sessionId", "consecutiveRetryCount", "retry"),
    "watchdog": ("risk", "riskScore", "reasons", "riskReasons", "steps", "targets", "targetCount", "generation_id", "goal"),
    "pattern": ("verdict", "reason", "class", "count", "window"),
    "cordis": ("requestId", "agentId", "pluginId", "packageId", "mode", "name", "purpose", "requiresApproval", "outcome"),
}
DISPLAY_HINTS = {
    "activity.default": "{agentName}｜{projectName} / Session {sessionId}｜{label}｜工具 {toolName} / {tool}｜目标 {target}；必要时补 {command}。",
    "activity.edit": "{agentName}｜{projectName} / Session {sessionId}｜正在编辑 {target}｜工具 {toolName} / {tool}｜步骤 {step}。",
    "activity.read": "{agentName}｜{projectName} / Session {sessionId}｜正在读取 {target}｜工具 {toolName} / {tool}。",
    "activity.run": "{agentName}｜{projectName} / Session {sessionId}｜执行 {command}｜工具 {toolName} / {tool}｜目标 {target}。",
    "activity.search": "{agentName}｜{projectName} / Session {sessionId}｜搜索/工具 {toolName} / {tool}｜目标 {target}｜参数 {arguments}。",
    "agent.attention": "需要处理的 Agent {name}；当前参数仅能定位到 Agent。",
    "agent.error": "{agentName}｜{projectName} / {sessionName}｜错误 {errorMessage}；必要时补 {errorCode}、{errorText}、重试 {retries}。",
    "agent.missing": "缺失/离线的 Agent {name}；当前参数仅能定位到 Agent。",
    "approval.command": "{agentName}｜{projectName} / {sessionName}｜审批命令 {command}｜工具 {toolName} / {tool}｜参数 {arguments}｜审批 ID {approvalId}。",
    "approval.generic": "{agentName}｜{projectName} / {sessionName}｜待审批操作 {label}｜工具 {toolName} / {tool}｜命令 {command}｜参数 {arguments}｜审批 ID {approvalId}。",
    "approval.tool": "{agentName}｜{projectName} / {sessionName}｜审批工具 {toolName} / {tool}（{label}）｜命令 {command}｜参数 {arguments}｜审批 ID {approvalId}。",
    "balance.result": "{name}｜余额/结果 {text}｜总计 {total}；需要更多说明时补 {info}。",
    "bridge.install.failed": "{name}｜桥接安装失败｜原因 {detail}。",
    "bridge.install.pending": "{name}｜桥接安装中。", "bridge.install.success": "{name}｜桥接安装成功。",
    "bridge.uninstall.failed": "{name}｜桥接卸载失败；当前参数没有失败详情。",
    "control.failed": "{agentName}｜Session {sessionId}｜控制 {operation} 失败｜阶段 {phase}｜详情 {detail}｜结果 {ok}。",
    "control.interrupt.pending": "{agentName}｜Session {sessionId}｜正在执行 {operation}｜阶段 {phase}；必要时补 {detail}。",
    "control.interrupt.success": "{agentName}｜Session {sessionId}｜{operation} 成功｜阶段 {phase}｜结果 {ok}。",
    "control.replan.pending": "{agentName}｜Session {sessionId}｜正在执行 {operation}｜阶段 {phase}；必要时补 {detail}。",
    "control.replan.success": "{agentName}｜Session {sessionId}｜{operation} 成功｜阶段 {phase}｜结果 {ok}；必要时补 {detail}。",
    "done.attention": "{name} 已停止并需要用户确认；当前参数仅能定位到 Agent。",
    "done.success": "{name} 已完成；当前参数仅能定位到 Agent。",
    "failure.generic": "{agentName}｜{projectName} / {sessionName}｜执行失败 {errorMessage}｜错误码 {errorCode}；必要时补 {errorText}。",
    "failure.retry": "{agentName}｜{projectName} / {sessionName}｜重试后仍失败 {errorMessage}｜已重试 {retries}｜错误码 {errorCode}。",
    "failure.tool": "{agentName}｜{projectName} / {sessionName}｜工具/操作 {label} 失败｜{errorMessage}｜错误码 {errorCode}。",
    "llm_error.api": "AI 服务异常；可尝试使用 {errorCode}、{errorMessage} 和 {retry} 说明原因。",
    "pattern.control": "{agentName}｜Session {sessionId}｜检测 {class}｜原因 {reason} / {reasons}｜次数 {count}｜窗口 {window}｜决策 {verdict}。",
    "pattern.warning": "{agentName}｜Session {sessionId}｜检测 {class}｜原因 {reason} / {reasons}｜次数 {count}｜窗口 {window}｜决策 {verdict}。",
    "question.empty": "{agentName}｜{projectName} / {sessionName}｜等待用户回答｜问题 {body}｜数量 {count}｜请求 ID {questionRpcId}。",
    "question.many": "{agentName}｜{projectName} / {sessionName}｜有 {count} 个问题｜首要问题 {body}｜问题数据 {questions}｜请求 ID {questionRpcId}。",
    "question.one": "{agentName}｜{projectName} / {sessionName}｜问题 {body}｜选项/问题数据 {questions}｜请求 ID {questionRpcId}。",
    "rate_limit.many": "{agentName}｜Session {sessionId}｜连续限流 {count} 次｜错误 {errorMessage}｜重试序号 {retry}。",
    "rate_limit.one": "{agentName}｜Session {sessionId}｜发生限流｜错误 {errorMessage}｜重试序号 {retry}。",
    "start": "{agentName}｜{projectName} / {sessionName}｜开始 {label}。",
    "stuck.reminder": "无动态参数，只能固定提示“任务可能卡住”。",
    "thinking": "{agentName}｜{projectName} / {sessionName}｜正在处理 {label}。",
    "watchdog.intervention": "{agentName}｜Session {sessionId}｜风险 {riskScore}｜原因 {reasons} / {riskReasons}｜目标 {goal}｜涉及 {targetCount} 个目标 {targets}。",
    "watchdog.unknown": "{agentName}｜Session {sessionId}｜Watchdog 无法判断｜风险 {riskScore}｜原因 {reasons} / {riskReasons}｜目标 {goal}。",
    "watchdog.warning": "{agentName}｜Session {sessionId}｜风险 {riskScore}｜原因 {reasons} / {riskReasons}｜目标 {goal}｜涉及 {targetCount} 个目标 {targets}。",
}
EVENT_SOURCES = {
    "start": ("agent/status", "UserPromptSubmit", "turn/start"),
    "thinking": ("UserPromptSubmit", "agent/status"),
    "activity.read": ("assistant/message", "tool/call"), "activity.search": ("assistant/message", "tool/call"),
    "activity.edit": ("assistant/message", "tool/call"), "activity.run": ("assistant/message", "tool/call"),
    "activity.default": ("assistant/message", "tool/call"),
    "agent.attention": ("Stop", "SubagentStop", "state=attention"), "agent.error": ("PostToolUseFailure", "StopFailure", "state=error"),
    "agent.missing": ("Agent monitor/config detection",),
    "bridge.install.pending": ("Pet bridge install",), "bridge.install.success": ("Pet bridge install",),
    "bridge.install.failed": ("Pet bridge install",), "bridge.uninstall.failed": ("Pet bridge uninstall",),
    "dsh.writeback.failed": ("Pet response writeback",),
    "approval.command": ("approval/requested → approval/request",), "approval.tool": ("approval/requested → approval/request",), "approval.generic": ("approval/requested → approval/request",),
    "question.empty": ("question/requested", "tool/call(ask_user_question)"), "question.one": ("question/requested", "tool/call(ask_user_question)"), "question.many": ("question/requested", "tool/call(ask_user_question)"),
    "watchdog.warning": ("ExplorationWatchdog.warning",), "watchdog.intervention": ("ExplorationWatchdog.judge_result",), "watchdog.unknown": ("ExplorationWatchdog.judge_result",),
    "rate_limit.one": ("agent/request-error", "llm/retry → rate_limit"), "rate_limit.many": ("agent/request-error", "llm/retry → rate_limit"), "llm_error.api": ("llm/retry → llm_error",),
    "done.success": ("SessionEnd", "turn/end", "task_complete", "state=idle"), "done.attention": ("Stop", "SubagentStop", "state=attention"),
    "failure.retry": ("execution/failed" ,), "failure.tool": ("execution/failed",), "failure.generic": ("execution/failed",),
    "control.replan.pending": ("Pet watchdog control",), "control.replan.success": ("watchdog/control-result",),
    "control.interrupt.pending": ("Pet watchdog control",), "control.interrupt.success": ("watchdog/control-result",), "control.failed": ("watchdog/control-result",),
    "pattern.warning": ("BehaviorPatternDetector.warning",), "pattern.control": ("BehaviorPatternDetector.control",),
    "balance.result": ("balance query result",),
}

EVENT_FIELDS = {
    "start": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "sessionName", "projectName", "label", "name"),
    "thinking": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "sessionName", "projectName", "label", "name"),
    "activity": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "projectName", "label", "tool", "toolName", "target", "callId", "arguments", "argsKey", "command", "step", "name", "label"),
    "approval": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "sessionName", "projectName", "label", "rpcId", "requestId", "approvalId", "callId", "toolName", "tool", "arguments", "command", "outcome", "name", "label"),
    "question": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "sessionName", "projectName", "label", "rpcId", "questionRpcId", "callId", "questions", "outcome", "name", "body", "count"),
    "error": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "sessionName", "projectName", "label", "errorCode", "errorMessage", "errorText", "retry", "retries", "retryExhausted", "source", "name"),
    "watchdog": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "risk", "riskScore", "reasons", "riskReasons", "steps", "targets", "targetCount", "generation_id", "goal", "name", "reasons"),
    "pattern": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "verdict", "reason", "class", "count", "window", "name", "reasons"),
    "control": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "requestId", "operation", "ok", "phase", "detail", "name"),
    "rate_limit": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "errorCode", "errorMessage", "consecutiveRetryCount", "retry", "count"),
    "cordis": ("agent", "agent_key", "agentName", "event", "source", "ts", "sessionId", "requestId", "agentId", "pluginId", "packageId", "mode", "name", "purpose", "requiresApproval", "outcome"),
    "balance": ("text", "info", "total", "name"),
}

# 每个事件 key 在所属组字段之外额外补充的常用参数；entries 里与
# EVENT_FIELDS 组字段合并去重（组字段在前，key 补充在后）。
PARAMETERS: dict[str, tuple[str, ...]] = {
    "start": ("name",), "thinking": ("name",), "activity.read": ("name",),
    "activity.search": ("name",), "activity.edit": ("name",), "activity.run": ("name",),
    "activity.default": ("name",), "agent.attention": ("name",), "agent.error": ("name",),
    "agent.missing": ("name",), "bridge.install.pending": ("name",),
    "bridge.install.success": ("name",), "bridge.install.failed": ("name", "detail"),
    "bridge.uninstall.failed": ("name",), "dsh.writeback.failed": ("name", "detail"),
    "approval.command": ("name", "command"), "approval.tool": ("name", "label"),
    "approval.generic": ("name",), "question.empty": ("name", "body", "count"),
    "question.one": ("name", "body"), "question.many": ("name", "count"),
    "watchdog.warning": ("name", "reasons"), "watchdog.intervention": ("name", "reasons"),
    "watchdog.unknown": ("name", "reasons"), "rate_limit.one": ("count",),
    "rate_limit.many": ("count",), "llm_error.api": ("name", "errorCode", "errorMessage", "retry"),
    "done.success": ("name",), "done.attention": ("name",),
    "failure.retry": ("name",), "failure.tool": ("name",), "failure.generic": ("name",),
    "control.replan.pending": ("name",), "control.replan.success": ("name",),
    "control.interrupt.pending": ("name",), "control.interrupt.success": ("name",),
    "control.failed": ("name",), "pattern.warning": ("name",),
    "pattern.control": ("name", "reasons"), "stuck.reminder": (),
    "balance.query": ("name",), "balance.result": ("text",),
}


def _event_group(key: str) -> str:
    if key.startswith("activity."): return "activity"
    if key.startswith("approval."): return "approval"
    if key.startswith("question."): return "question"
    if key.startswith("failure.") or key == "agent.error": return "error"
    if key.startswith("watchdog."): return "watchdog"
    if key.startswith("pattern."): return "pattern"
    if key.startswith("control."): return "control"
    if key.startswith("rate_limit."): return "rate_limit"
    if key == "balance.result": return "balance"
    return key


def build_persona_template(config: dict[str, Any] | None) -> dict[str, Any]:
    """Build a complete portable document without leaking runtime settings."""
    config = config if isinstance(config, dict) else {}
    raw = config.get("dialogue_phrases", config)
    raw = raw if isinstance(raw, dict) else {}
    phrases = {}
    entries = []
    for key in phrase_keys():
        value = raw.get(key, [])
        if isinstance(value, str):
            value = [value] if value.strip() else []
        elif isinstance(value, list):
            value = [item.strip() for item in value if isinstance(item, str) and item.strip()][:8]
        else:
            value = []
        phrases[key] = copy.deepcopy(value)
        group = _event_group(key)
        parameters = list(dict.fromkeys(EVENT_FIELDS.get(group, ()) + PARAMETERS.get(key, ())))
        entries.append({"key": key, "description": key, "sources": list(EVENT_SOURCES.get(key, ())), "parameters": parameters, "displayHint": DISPLAY_HINTS.get(key, ""), "phrases": copy.deepcopy(value)})
    mode = str(config.get("dialogue_mode", "custom") or "custom")
    return {"template": TEMPLATE_VERSION, "mode": mode if mode in {"legacy", "whale_maid", "custom"} else "custom", "name": str(config.get("persona_template_name", "我的角色台词") or "我的角色台词"), "description": "Pet 全部弹窗/气泡内容模板。每个 entries 项的 parameters 是该事件常用参数；同时支持自动读取上游事件新增字段。", "variables": copy.deepcopy(VARIABLES), "upstream": {"description": "模板渲染会自动合并当前上游事件的顶层字段，并保留完整对象于 payload/data；未来新增字段无需修改此模板格式。显式别名（如 name、command）优先。", "fields": copy.deepcopy(UPSTREAM_FIELDS), "wildcards": ["{任意顶层字段}", "{payload.嵌套字段}", "{data.嵌套字段}", "{questions[0][options][0][label]}"], "privacy": "仅建议展示脱敏后的状态/元数据；不要把代码、命令全文或文件内容写入模板文案。"}, "phrases": phrases, "entries": entries}


def template_json(config: dict[str, Any] | None) -> str:
    import json
    return json.dumps(build_persona_template(config), ensure_ascii=False, indent=2) + "\n"
