# -*- coding: utf-8 -*-
"""Portable persona phrase template generation."""
from __future__ import annotations

import copy
from typing import Any

from .persona_phrases import phrase_keys

TEMPLATE_VERSION = "persona-phrases/v1"

# 显式注入的展示参数：每个 key 渲染时由调用点（pet/agent_link.py、pet/app.py）
# 直接以 kwargs 传入，保证可用。上游记录附带字段见 UPSTREAM_FIELDS。
# 对齐审计见 docs/PERSONA-TEMPLATE-FIELD-ALIGNMENT-2026-09-05.md。
VARIABLES = {
    "name": "Agent 展示名称（所有事件都会注入）",
    "command": "命令文本（approval.command=待审批命令；activity.*=工具命令，上游记录提供时可用；已折叠单行、超长截断）",
    "label": "标签（approval.tool/activity.*=工具中文标签；approval.command/generic、question.*、rate_limit.*、failure.*=会话标签，上游提供时可用）",
    "body": "问题内容（question.one；含 header 前缀）",
    "count": "数量（question.many=问题数；rate_limit.many=连续限流次数）",
    "reasons": "循环/行为检测的判断原因（watchdog.*、pattern.*；已格式化为文本）",
    "detail": "桥接安装失败详情（bridge.install.failed）",
    "text": "余额查询结果文本（balance.result）",
    "tool": "原始工具名（activity.*）",
    "toolName": "审批原始工具名（approval.*；上游记录提供时可用）",
    "argsKey": "工具参数摘要键（activity.*；上游记录提供时可用）",
    "callId": "工具调用 ID（activity.*；上游记录提供时可用）",
    "step": "turn 内步骤序号（activity.*；上游记录提供时可用）",
    "sessionName": "会话显示名（来自会话元数据 projectName/label/agentName；仅解析出真实名称时才注入，无元数据时占位符自动隐藏，不会回退成 sessionId）",
    "projectName": "会话所属项目名（含 sessionId 的弹窗均可用；上游记录提供时可用）",
    "errorCode": "错误码（rate_limit.*、failure.*；上游记录提供时可用）",
    "errorMessage": "错误信息原文（rate_limit.*、failure.*；上游记录提供时可用）",
    "consecutiveRetryCount": "已连续限流次数（rate_limit.*；上游记录提供时可用）",
    "retry": "本轮重试序号（rate_limit.*；上游记录提供时可用）",
    "retries": "本轮已重试次数（failure.*；上游记录提供时可用）",
    "retryExhausted": "是否重试耗尽（failure.*；上游记录提供时可用）",
}

# 上游记录字段——以桥接插件源码（integrations/dsh-pet-bridge/index.js）逐事件
# writeRecord/writeRecordDedup 实际写出的字段为准（2026-09-06 核实）：
#   公共: ts/agent/event 恒有；sessionId 存在时补 projectName/sessionName（session/meta）
#   Pet 侧注入: agent_key（_remember_dialogue_record）
#   tool/call: tool/argsKey/command/callId/step/sessionId —— 无 target/ok（在 tool/result）
#   approval/request: rpcId/approvalId/toolName/command/sessionId —— 无 requestId/callId/outcome
#   question/requested: rpcId(mux)/callId/sessionId/questions —— 无 questionRpcId（在 resolved）
#   execution/failed: source/errorCode/errorMessage/retries/retryExhausted
# 审批/提问/限流/失败/工具类文案与记录同轮触发，这些字段可靠；状态机与本地
# 检测触发的文案（start/thinking/agent.*/done.* 等）不保证拿到记录，勿依赖。
BASE_FIELDS = ("ts", "agent", "agent_key", "event", "sessionId", "projectName", "sessionName", "label")
UPSTREAM_FIELDS = {
    "base": BASE_FIELDS,
    "tool/call": ("tool", "argsKey", "command", "callId", "step"),
    "tool/result": ("tool", "command", "callId", "ok", "timeout", "errorCode", "errorText", "resultSummary", "durationMs"),
    "approval/request": ("rpcId", "approvalId", "toolName", "command", "sessionId"),
    "approval/resolved": ("rpcId", "approvalId", "outcome", "sessionId"),
    "question/requested": ("rpcId", "callId", "sessionId", "questions"),
    "question/resolved": ("rpcId", "callId", "sessionId"),
    "rate_limit": ("errorCode", "errorMessage", "consecutiveRetryCount", "retry", "sessionId"),
    "llm_error": ("errorCode", "errorMessage", "retry"),
    "execution/failed": ("source", "errorCode", "errorMessage", "retries", "retryExhausted"),
}
DISPLAY_HINTS = {
    "activity.default": "{name} 正在处理 {tool}。",
    "activity.edit": "{name} 正在编辑（{tool}）。",
    "activity.read": "{name} 正在读取（{tool}）。",
    "activity.run": "{name} 正在运行（{tool}）。",
    "activity.search": "{name} 正在搜索（{tool}）。",
    "agent.attention": "{name} 需要你看一眼。",
    "agent.error": "{name} 好像出错了，主人帮忙看一下吧。",
    "agent.missing": "暂时没有检测到本机安装 {name}。",
    "approval.command": "{name} 请求执行：{command}",
    "approval.generic": "{name} 有审批等你决定。",
    "approval.tool": "{name} 在请求审批：{label}",
    "balance.loading": "让我看看余额…",
    "balance.result": "余额情况：{text}",
    "bridge.install.failed": "{name} 的通信桥没有装好：{detail}",
    "bridge.install.pending": "正在给 {name} 接上通信桥…",
    "bridge.install.success": "{name} 的联动插件安装完成。",
    "bridge.uninstall.failed": "{name} 的通信桥没有完全卸载，需要手动检查。",
    "dsh.writeback.failed": "回写 DSH 失败，请到 DSH 界面处理。",
    "done.attention": "{name} 停下来了，结果请主人确认。",
    "done.success": "{name} 这一轮完成啦。",
    "failure.generic": "{name} 本轮运行失败，请检查后再运行。",
    "failure.retry": "{name} 本轮多次重试后仍未成功。",
    "failure.tool": "{name} 本轮工具执行失败。",
    "llm_error.api": "AI 服务暂时没有回应。",
    "pattern.control": "{name} 行为模式异常，已建议干预：{reasons}。",
    "pattern.warning": "{name} 行为模式需要留意：{reasons}。",
    "question.empty": "{name} 在等你回答。",
    "question.many": "{name} 有 {count} 个问题等你回答。",
    "question.one": "{name} 在问你：{body}",
    "rate_limit.many": "已连续限流 {count} 次，请稍后再试。",
    "rate_limit.one": "通信被限流了，请稍后再试。",
    "start": "{name} 开始干活啦～",
    "stuck.reminder": "{name} 可能卡住了，去看一眼吧。",
    "thinking": "{name} 正在认真想办法……",
    "watchdog.warning": "{name} 近期存在重复探索行为：{reasons}。",
}
EVENT_SOURCES = {
    "start": ("状态机 thinking/working", "UserPromptSubmit", "turn/start"),
    "thinking": ("状态机 thinking", "UserPromptSubmit"),
    "activity.read": ("tool/call",), "activity.search": ("tool/call",),
    "activity.edit": ("tool/call",), "activity.run": ("tool/call",),
    "activity.default": ("tool/call",),
    "agent.attention": ("状态机 attention（Stop / SubagentStop / state=attention）",),
    "agent.error": ("状态机 error（PostToolUseFailure / StopFailure / state=error）",),
    "agent.missing": ("Agent 监视器本地检测",),
    "bridge.install.pending": ("Pet 桥接安装流程",), "bridge.install.success": ("Pet 桥接安装流程",),
    "bridge.install.failed": ("Pet 桥接安装流程",), "bridge.uninstall.failed": ("Pet 桥接卸载流程",),
    "dsh.writeback.failed": ("Pet 回写 DSH 响应",),
    "approval.command": ("approval/request（兼容旧名 approval/requested）",),
    "approval.tool": ("approval/request（兼容旧名 approval/requested）",),
    "approval.generic": ("approval/request（兼容旧名 approval/requested）",),
    "question.empty": ("question/requested", "tool/call(ask_user_question)"),
    "question.one": ("question/requested", "tool/call(ask_user_question)"),
    "question.many": ("question/requested", "tool/call(ask_user_question)"),
    "watchdog.warning": ("ExplorationWatchdog（本地检测）",),
    "rate_limit.one": ("rate_limit（bridge，errorCode=429）",), "rate_limit.many": ("rate_limit（bridge，errorCode=429）",),
    "llm_error.api": ("llm_error（bridge）",),
    "done.success": ("状态机 idle（SessionEnd / turn/end / task_complete / state=idle）",),
    "done.attention": ("状态机 attention（Stop / SubagentStop）",),
    "failure.retry": ("execution/failed",), "failure.tool": ("execution/failed",), "failure.generic": ("execution/failed",),
    "pattern.warning": ("BehaviorPatternDetector（本地检测）",), "pattern.control": ("BehaviorPatternDetector（本地检测）",),
    "stuck.reminder": ("StuckDetector（本地检测）",),
    "balance.loading": ("Pet 内置余额查询",), "balance.result": ("Pet 内置余额查询",),
}

# 每个事件 key 由其对应的上游方法（调用点）显式注入的参数——这是该弹窗
# 「能获取到的字段」的完整清单，entries[].parameters 与之逐 key 严格相等。
# 分两类：无条件注入的（保证可用）+ 条件注入的（CONDITIONAL_PARAMETERS，
# 上游未提供/为空/为 null 时占位符自动隐藏，不会原样露出）。
# 改调用点 kwargs 时必须同步改这里（有 AST 回归测试）。
PARAMETERS: dict[str, tuple[str, ...]] = {
    "start": ("name",), "thinking": ("name",),
    "activity.read": ("name", "tool", "label", "command", "argsKey", "callId", "step",
                      "sessionName", "projectName"),
    "activity.search": ("name", "tool", "label", "command", "argsKey", "callId", "step",
                        "sessionName", "projectName"),
    "activity.edit": ("name", "tool", "label", "command", "argsKey", "callId", "step",
                      "sessionName", "projectName"),
    "activity.run": ("name", "tool", "label", "command", "argsKey", "callId", "step",
                     "sessionName", "projectName"),
    "activity.default": ("name", "tool", "label", "command", "argsKey", "callId", "step",
                         "sessionName", "projectName"),
    "agent.attention": ("name",), "agent.error": ("name",),
    "agent.missing": ("name",), "bridge.install.pending": ("name",),
    "bridge.install.success": ("name",), "bridge.install.failed": ("name", "detail"),
    "bridge.uninstall.failed": ("name",), "dsh.writeback.failed": (),
    "approval.command": ("name", "command", "toolName", "sessionName", "projectName", "label"),
    "approval.tool": ("name", "label", "toolName", "sessionName", "projectName"),
    "approval.generic": ("name", "toolName", "sessionName", "projectName", "label"),
    "question.empty": ("name", "sessionName", "projectName", "label"),
    "question.one": ("name", "body", "sessionName", "projectName", "label"),
    "question.many": ("name", "count", "sessionName", "projectName", "label"),
    "watchdog.warning": ("name", "reasons"),
    "rate_limit.one": ("count", "errorCode", "errorMessage", "consecutiveRetryCount", "retry",
                       "sessionName", "projectName"),
    "rate_limit.many": ("count", "errorCode", "errorMessage", "consecutiveRetryCount", "retry",
                        "sessionName", "projectName"),
    "llm_error.api": (),
    "done.success": ("name",), "done.attention": ("name",),
    "failure.retry": ("name", "source", "errorCode", "errorMessage", "retries", "retryExhausted",
                      "sessionName", "projectName"),
    "failure.tool": ("name", "source", "errorCode", "errorMessage", "retries", "retryExhausted",
                     "sessionName", "projectName"),
    "failure.generic": ("name", "source", "errorCode", "errorMessage", "retries", "retryExhausted",
                        "sessionName", "projectName"),
    "pattern.warning": ("name", "reasons"),
    "pattern.control": ("name", "reasons"), "stuck.reminder": ("name",),
    "balance.loading": (), "balance.result": ("text",),
}

# 条件可用参数：调用点仅在上游记录提供该字段（非空/非 null）时才注入；缺失时
# 渲染端自动隐藏对应占位符（不会原样露出 {xxx}）。仍是「上游方法能获取到的
# 字段」（保留在 entries.parameters 中），但与保证注入的参数不同——设置页提示
# 与导出文档据此区分表述。
# 注意（2026-09-06 桥接源码核实）：tool/call 记录只含
# tool/argsKey/command/callId/step/sessionId——target/ok 仅存在于 tool/result
# 与 watchdog reasoning 记录，活动气泡在 tool/call 同轮触发时拿不到，不得宣称。
# label 同名双义：activity.*/approval.tool 的 label=工具中文标签（保证注入，
# 不含会话标签）；approval.command/generic、question.*、rate_limit.*、failure.*
# 的 label=会话标签（条件注入）。
CONDITIONAL_PARAMETERS: dict[str, tuple[str, ...]] = {
    "activity.read": ("command", "argsKey", "callId", "step", "sessionName", "projectName"),
    "activity.search": ("command", "argsKey", "callId", "step", "sessionName", "projectName"),
    "activity.edit": ("command", "argsKey", "callId", "step", "sessionName", "projectName"),
    "activity.run": ("command", "argsKey", "callId", "step", "sessionName", "projectName"),
    "activity.default": ("command", "argsKey", "callId", "step", "sessionName", "projectName"),
    "approval.command": ("toolName", "sessionName", "projectName", "label"),
    "approval.tool": ("toolName", "sessionName", "projectName"),
    "approval.generic": ("toolName", "sessionName", "projectName", "label"),
    "question.empty": ("sessionName", "projectName", "label"),
    "question.one": ("sessionName", "projectName", "label"),
    "question.many": ("sessionName", "projectName", "label"),
    "rate_limit.one": ("errorCode", "errorMessage", "consecutiveRetryCount", "retry",
                       "sessionName", "projectName"),
    "rate_limit.many": ("errorCode", "errorMessage", "consecutiveRetryCount", "retry",
                        "sessionName", "projectName"),
    "failure.retry": ("source", "errorCode", "errorMessage", "retries", "retryExhausted",
                      "sessionName", "projectName"),
    "failure.tool": ("source", "errorCode", "errorMessage", "retries", "retryExhausted",
                     "sessionName", "projectName"),
    "failure.generic": ("source", "errorCode", "errorMessage", "retries", "retryExhausted",
                        "sessionName", "projectName"),
}


# JSON 没有注释语法，因此导出的便携文档以 `_说明` 键携带使用指南（放在文件最顶部）。
# 导入侧只读取 template / phrases / entries 等业务键，这段自述在导入时会被忽略，可随意保留或删除。
EXPORT_GUIDE: dict[str, Any] = {
    "这是什么": (
        "本文件是桌宠「表达风格」一键导出的角色台词模板（格式 persona-phrases/v1），"
        "覆盖 Agent 联动相关的提示气泡文案：Agent 状态、过程汇报、完成、审批、提问、"
        "错误、限流、卡住与循环检测等联动提醒，以及余额气泡。"
        "随机自言自语与点击台词不在此列（在桌宠行为设置中单独配置）。"
        "本段（_说明）只是给人或 AI 阅读的注释，导入时会被自动忽略，整段删除也不影响使用。"
    ),
    "怎么改（最常用）": [
        "1. 改 phrases：每个 key 是一类事件的文案，值是候选文案数组；数组里每项一句，实际弹出时轮换使用。"
        "改成 [] 表示留空，该事件自动沿用默认模式台词。",
        "2. 文案里可用 {变量} 占位符，弹出时自动代入真实信息，例如 {name}（Agent 名称）、{command}（待审批命令）。"
        "每种弹窗能代入的字段 = 它对应上游方法显式注入的参数，见各 entries 的 parameters："
        "未标注的参数保证会被替换；标注「上游记录提供时可用」的条件参数，在上游未提供/为空/为 null 时会自动隐藏"
        "（占位符不会原样露出，无需自己写回退）。"
        "upstream 是上游事件记录附带字段，审批/提问/限流/失败/工具类文案在事件触发时可读，其他场景不保证有值。",
        "3. 想整体换风格：改 mode（legacy=默认模式 / whale_maid=鲸鱼娘女仆模式 / custom=自定义台词），"
        "并顺带改 name / description；导入后会自动切到「自定义台词」。",
        "4. 想精确改某一句：到 entries 按 key 找到同一项，参考 sources（什么事件触发）与 parameters（该项可用变量），"
        "再改顶层 phrases 中同名 key（两处应保持一致）。",
        "5. 改完把整个 JSON 原样粘贴回设置页「自定义台词」的输入框，点「导入模板」即生效。",
    ],
    "顶层字段涵义": {
        "_说明": "本段注释，导入时忽略，可保留或删除。",
        "template": "模板格式版本标识 persona-phrases/v1，导入时校验用，请勿改动。",
        "mode": "表达风格：legacy=默认模式；whale_maid=鲸鱼娘女仆模式；custom=自定义台词。",
        "name": "这套台词的名字，仅作标识，可随意修改。",
        "description": "整份模板用途的一句话说明，可随意修改或删除。",
        "variables": "各事件保证可用的 {变量} 占位符及含义，写文案时对照参考，一般无需改动。",
        "upstream": "上游事件记录附带字段（{任意字段}、{payload.xx}、{data.xx} 等）；审批/提问/限流/失败/工具类文案在事件触发时可读，其他场景不保证有值，依赖时请写好留空回退。",
        "phrases": "核心编辑区：事件 key → 候选文案数组（编辑方法见上方『怎么改』）。",
        "entries": "逐事件明细表，与 phrases 一一对应：列出每个 key 的触发来源 sources、可用变量 parameters 与示例 displayHint，"
        "方便人/AI 弄清每句台词在什么场景出现、能写哪些信息。",
    },
    "entries 项内字段涵义": {
        "key": "事件标识（与顶层 phrases 的键一致）：如 start=开始工作、thinking=思考、activity.read=读取文件、approval.command=命令审批。",
        "description": "该 key 的说明文字（当前为占位，内容与 key 相同），可自行补充更易读的说明。",
        "sources": "触发该文案的上游事件来源名，帮助理解在什么时刻出现，一般不改。",
        "parameters": "该项对应上游方法显式注入的 {变量} 清单（保证可用，含义见顶层 variables）。上游事件记录附带字段不在此列，需要时参考顶层 upstream（仅事件同轮可读）。",
        "displayHint": "用占位符写出的一句话示例，展示该事件能表达的信息上限，方便你或 AI 判断写多少内容；不会直接展示给用户。",
        "phrases": "与顶层 phrases 中同名 key 的数组，两处应保持一致。",
    },
    "事件 key 分组（按前缀识别场景）": {
        "start / thinking": "Agent 开始工作 / 思考中。",
        "activity.read / search / edit / run / default": "干活过程：读取文件 / 搜索 / 编辑代码 / 运行测试 / 其他工具。",
        "agent.attention / error / missing": "Agent 状态：需要你处理 / 出错 / 尚未检测到。",
        "approval.command / tool / generic": "审批：命令审批 / 工具审批 / 通用审批。",
        "question.empty / one / many": "提问：无选项等待选择 / 单个问题 / 多个问题。",
        "watchdog.warning": "循环检测（重复排查）：风险预警。",
        "pattern.warning / pattern.control": "行为重复检测：警告 / 自动干预。",
        "rate_limit.one / many、llm_error.api": "限流（单次/连续） / AI 服务出错。",
        "done.success / done.attention": "收尾：任务完成 / 停下等你确认。",
        "failure.retry / tool / generic": "本轮失败：重试后仍失败 / 工具执行失败 / 通用失败。",
        "bridge.*、dsh.writeback.failed": "联动桥接的安装/卸载/回写提示。",
        "stuck.reminder": "卡住检测的提醒气泡。",
        "balance.loading / balance.result": "余额查询：查询中提示 / 查询结果。",
    },
    "用 AI / 角色卡自动改写（推荐）": (
        "这份 JSON 的设计意图就是交给 AI 来写台词：把它发给支持「角色卡」/自定义人格设定的 AI"
        "（粘贴进 AI 对话，或放进角色扮演的角色卡设定里），让 AI 依据角色卡的人设与语气自动改写 phrases / entries 的文案，"
        "再把 AI 改好的 JSON 原样粘贴回设置页一键导入，即可得到符合角色气质的整套台词。"
    ),
}


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
        parameters = list(PARAMETERS.get(key, ()))
        entries.append({"key": key, "description": key, "sources": list(EVENT_SOURCES.get(key, ())), "parameters": parameters, "displayHint": DISPLAY_HINTS.get(key, ""), "phrases": copy.deepcopy(value)})
    mode = str(config.get("dialogue_mode", "custom") or "custom")
    document = {
        "template": TEMPLATE_VERSION,
        "mode": mode if mode in {"legacy", "whale_maid", "custom"} else "custom",
        "name": str(config.get("persona_template_name", "我的角色台词") or "我的角色台词"),
        "description": "Pet 全部弹窗/气泡内容模板。每个 entries 项的 parameters 是该项上游方法显式注入的参数（保证可用）；上游事件记录附带字段见顶层 upstream（仅事件同轮可读）。",
        "variables": copy.deepcopy(VARIABLES),
        "upstream": {
            "description": "模板渲染会自动合并最近一条上游事件记录的字段（审批/提问/限流/工具/失败类文案与记录同轮触发，字段可靠；状态机与本地检测触发的文案不保证有记录），并保留完整对象于 payload/data。显式别名（如 name、command）优先。",
            "fields": copy.deepcopy(UPSTREAM_FIELDS),
            "wildcards": ["{任意顶层字段}", "{payload.嵌套字段}", "{data.嵌套字段}", "{questions[0][options][0][label]}"],
            "privacy": "仅建议展示脱敏后的状态/元数据；不要把代码、命令全文或文件内容写入模板文案。",
        },
        "phrases": phrases,
        "entries": entries,
    }
    return {"_说明": EXPORT_GUIDE, **document}


def template_json(config: dict[str, Any] | None) -> str:
    import json
    return json.dumps(build_persona_template(config), ensure_ascii=False, indent=2) + "\n"
