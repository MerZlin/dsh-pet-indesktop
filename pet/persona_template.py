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
    "command": "待审批命令（approval.command；已折叠为单行、超长截断）",
    "label": "工具/操作的中文标签（approval.tool、activity.*）",
    "body": "问题内容（question.one；含 header 前缀）",
    "count": "数量（question.many=问题数；rate_limit.many=连续限流次数）",
    "reasons": "循环/行为检测的判断原因（watchdog.*、pattern.*；已格式化为文本）",
    "detail": "桥接安装失败详情（bridge.install.failed）",
    "text": "余额查询结果文本（balance.result）",
    "tool": "原始工具名（activity.*）",
    "target": "操作目标：文件路径/URL/命令（activity.*；上游记录未提供时占位符原样保留）",
    "callId": "工具调用 ID（activity.*；同上）",
    "step": "turn 内步骤序号（activity.*；同上）",
    "ok": "工具是否成功（activity.*；同上）",
}

# 上游记录字段（raw_record 携带、模板可透读）：审批/提问/限流/失败类文案
# 在收到对应事件的同一轮触发，此时这些字段可靠；工具类文案的 tool/target/
# callId/step/ok 已由调用点显式注入（保证可用）。状态机与本地检测器触发的
# 文案（start/thinking/agent.*/done.* 等）不保证拿到最新记录，勿依赖。
# 基础字段：ts/agent/event/source 为桥接 writeRecord 固定写入；projectName/label/
# agentName 由 session/meta 补充；agent_key 由 Pet 侧 _remember_dialogue_record 注入。
# sessionName 桥接从不写出，不得宣称。
BASE_FIELDS = ("ts", "agent", "agent_key", "agentName", "event", "source", "sessionId", "projectName", "label")
UPSTREAM_FIELDS = {
    "base": BASE_FIELDS,
    "tool": ("tool", "target", "callId", "ok", "step"),
    "approval": ("rpcId", "approvalId", "requestId", "callId", "toolName", "command", "sessionId", "outcome"),
    "question": ("rpcId", "questionRpcId", "callId", "sessionId", "questions"),
    "error": ("errorCode", "errorMessage", "errorText", "retryExhausted", "retries", "source"),
    "rate_limit": ("errorCode", "errorMessage", "sessionId"),
}
DISPLAY_HINTS = {
    "activity.default": "{name} 正在处理 {tool}（目标 {target}）。",
    "activity.edit": "{name} 正在编辑 {target}。",
    "activity.read": "{name} 正在读取 {target}。",
    "activity.run": "{name} 正在运行 {target}。",
    "activity.search": "{name} 正在搜索 {target}。",
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
    "control.failed": "控制请求暂未送达 {name}，可继续操作。",
    "control.interrupt.pending": "已请求终止 {name} 当前执行。",
    "control.interrupt.success": "终止请求已回传给 {name}。",
    "control.replan.pending": "正在暂停 {name}，生成下一步规划…",
    "control.replan.success": "重新规划提示已发送给 {name}。",
    "dsh.writeback.failed": "回写 DSH 失败，请到 DSH 界面处理。",
    "done.attention": "{name} 停下来了，结果请主人确认。",
    "done.success": "{name} 这一轮完成啦。",
    "failure.generic": "{name} 本轮运行失败，请检查后再运行。",
    "failure.retry": "{name} 本轮多次重试后仍未成功（错误：{errorMessage}，视最新记录而定）。",
    "failure.tool": "{name} 本轮工具执行失败（错误：{errorMessage}，视最新记录而定）。",
    "llm_error.api": "AI 服务暂时没有回应（{errorCode}：{errorMessage}，视最新记录而定）。",
    "pattern.control": "{name} 行为模式异常，已建议干预：{reasons}。",
    "pattern.warning": "{name} 行为模式需要留意：{reasons}。",
    "question.empty": "{name} 在等你回答。",
    "question.many": "{name} 有 {count} 个问题等你回答。",
    "question.one": "{name} 在问你：{body}",
    "rate_limit.many": "已连续限流 {count} 次，请稍后再试（错误：{errorMessage}，视最新记录而定）。",
    "rate_limit.one": "通信被限流了，请稍后再试（错误：{errorMessage}，视最新记录而定）。",
    "start": "{name} 开始干活啦～",
    "stuck.reminder": "{name} 可能卡住了，去看一眼吧。",
    "thinking": "{name} 正在认真想办法……",
    "watchdog.intervention": "{name} 可能陷入重复排查：{reasons}。",
    "watchdog.unknown": "{name} 检测到重复探索，判断服务暂时不可用。",
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
    "watchdog.intervention": ("ExplorationWatchdog Judge 结果",), "watchdog.unknown": ("ExplorationWatchdog Judge 结果",),
    "rate_limit.one": ("rate_limit（bridge，errorCode=429）",), "rate_limit.many": ("rate_limit（bridge，errorCode=429）",),
    "llm_error.api": ("llm_error（bridge）",),
    "done.success": ("状态机 idle（SessionEnd / turn/end / task_complete / state=idle）",),
    "done.attention": ("状态机 attention（Stop / SubagentStop）",),
    "failure.retry": ("execution/failed",), "failure.tool": ("execution/failed",), "failure.generic": ("execution/failed",),
    "control.replan.pending": ("Pet watchdog 控制",), "control.replan.success": ("watchdog/control-result",),
    "control.interrupt.pending": ("Pet watchdog 控制",), "control.interrupt.success": ("watchdog/control-result",),
    "control.failed": ("watchdog/control-result",),
    "pattern.warning": ("BehaviorPatternDetector（本地检测）",), "pattern.control": ("BehaviorPatternDetector（本地检测）",),
    "stuck.reminder": ("StuckDetector（本地检测）",),
    "balance.loading": ("Pet 内置余额查询",), "balance.result": ("Pet 内置余额查询",),
}

EVENT_FIELDS = {
    "start": BASE_FIELDS + ("name",),
    "thinking": BASE_FIELDS + ("name",),
    "activity": BASE_FIELDS + ("name", "tool", "label", "target", "callId", "step", "ok"),
    "approval": BASE_FIELDS + ("name", "label", "command", "rpcId", "approvalId", "requestId", "callId", "toolName", "outcome"),
    "question": BASE_FIELDS + ("name", "body", "count", "rpcId", "questionRpcId", "callId", "questions"),
    "error": BASE_FIELDS + ("name", "errorCode", "errorMessage", "errorText", "retryExhausted", "retries"),
    "llm_error.api": BASE_FIELDS + ("errorCode", "errorMessage"),
    "watchdog": BASE_FIELDS + ("name", "reasons"),
    "pattern": BASE_FIELDS + ("name", "reasons"),
    "control": BASE_FIELDS + ("name",),
    "rate_limit": BASE_FIELDS + ("count", "errorCode", "errorMessage"),
    "balance": ("text",),
}

# 每个事件 key 由调用点显式注入的参数（保证可用）；entries 里与
# EVENT_FIELDS 组字段合并去重（组字段在前，key 补充在后）。
PARAMETERS: dict[str, tuple[str, ...]] = {
    "start": ("name",), "thinking": ("name",),
    "activity.read": ("name", "tool", "label", "target", "callId", "step", "ok"),
    "activity.search": ("name", "tool", "label", "target", "callId", "step", "ok"),
    "activity.edit": ("name", "tool", "label", "target", "callId", "step", "ok"),
    "activity.run": ("name", "tool", "label", "target", "callId", "step", "ok"),
    "activity.default": ("name", "tool", "label", "target", "callId", "step", "ok"),
    "agent.attention": ("name",), "agent.error": ("name",),
    "agent.missing": ("name",), "bridge.install.pending": ("name",),
    "bridge.install.success": ("name",), "bridge.install.failed": ("name", "detail"),
    "bridge.uninstall.failed": ("name",), "dsh.writeback.failed": (),
    "approval.command": ("name", "command"), "approval.tool": ("name", "label"),
    "approval.generic": ("name",), "question.empty": ("name",),
    "question.one": ("name", "body"), "question.many": ("name", "count"),
    "watchdog.warning": ("name", "reasons"), "watchdog.intervention": ("name", "reasons"),
    "watchdog.unknown": ("name",), "rate_limit.one": ("count",),
    "rate_limit.many": ("count",), "llm_error.api": (),
    "done.success": ("name",), "done.attention": ("name",),
    "failure.retry": ("name",), "failure.tool": ("name",), "failure.generic": ("name",),
    "control.replan.pending": ("name",), "control.replan.success": ("name",),
    "control.interrupt.pending": ("name",), "control.interrupt.success": ("name",),
    "control.failed": ("name",), "pattern.warning": ("name", "reasons"),
    "pattern.control": ("name", "reasons"), "stuck.reminder": ("name",),
    "balance.loading": (), "balance.result": ("text",),
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
    if key == "balance.loading" or key == "balance.result": return "balance"
    return key


# JSON 没有注释语法，因此导出的便携文档以 `_说明` 键携带使用指南（放在文件最顶部）。
# 导入侧只读取 template / phrases / entries 等业务键，这段自述在导入时会被忽略，可随意保留或删除。
EXPORT_GUIDE: dict[str, Any] = {
    "这是什么": (
        "本文件是桌宠「表达风格」一键导出的角色台词模板（格式 persona-phrases/v1），"
        "覆盖桌宠自言自语、Agent 状态、审批、提问、错误、限流等全部弹窗/气泡文案。"
        "本段（_说明）只是给人或 AI 阅读的注释，导入时会被自动忽略，整段删除也不影响使用。"
    ),
    "怎么改（最常用）": [
        "1. 改 phrases：每个 key 是一类事件的文案，值是候选文案数组；数组里每项一句，实际弹出时轮换使用。"
        "改成 [] 表示留空，该事件自动沿用原有模式台词。",
        "2. 文案里可用 {变量} 占位符，弹出时自动代入真实信息，例如 {name}（Agent 名称）、{command}（待审批命令）。"
        "变量分两级：variables 是每个事件保证可用的参数（见各 entries 的 parameters）；"
        "upstream 是上游事件记录附带字段，审批/提问/限流/工具/失败类文案在事件触发时可读，其他场景不保证有值。",
        "3. 想整体换风格：改 mode（legacy=原有模式 / whale_maid=鲸鱼娘女仆模式 / custom=自定义台词），"
        "并顺带改 name / description；导入后会自动切到「自定义台词」。",
        "4. 想精确改某一句：到 entries 按 key 找到同一项，参考 sources（什么事件触发）与 parameters（该项可用变量），"
        "再改顶层 phrases 中同名 key（两处应保持一致）。",
        "5. 改完把整个 JSON 原样粘贴回设置页「自定义台词」的输入框，点「导入模板」即生效。",
    ],
    "顶层字段涵义": {
        "_说明": "本段注释，导入时忽略，可保留或删除。",
        "template": "模板格式版本标识 persona-phrases/v1，导入时校验用，请勿改动。",
        "mode": "表达风格：legacy=原有模式；whale_maid=鲸鱼娘女仆模式；custom=自定义台词。",
        "name": "这套台词的名字，仅作标识，可随意修改。",
        "description": "整份模板用途的一句话说明，可随意修改或删除。",
        "variables": "每个事件保证可用的 {变量} 占位符及含义，写文案时对照参考，一般无需改动。",
        "upstream": "上游事件记录附带字段（{任意字段}、{payload.xx}、{data.xx} 等）；审批/提问/限流/工具/失败类文案在事件触发时可读，其他场景不保证有值，依赖时请写好留空回退。",
        "phrases": "核心编辑区：事件 key → 候选文案数组（编辑方法见上方『怎么改』）。",
        "entries": "逐事件明细表，与 phrases 一一对应：列出每个 key 的触发来源 sources、可用变量 parameters 与示例 displayHint，"
        "方便人/AI 弄清每句台词在什么场景出现、能写哪些信息。",
    },
    "entries 项内字段涵义": {
        "key": "事件标识（与顶层 phrases 的键一致）：如 start=开始工作、thinking=思考、activity.read=读取文件、approval.command=命令审批。",
        "description": "该 key 的说明文字（当前为占位，内容与 key 相同），可自行补充更易读的说明。",
        "sources": "触发该文案的上游事件来源名，帮助理解在什么时刻出现，一般不改。",
        "parameters": "该项文案可用的 {变量} 清单（含义见顶层 variables）。",
        "displayHint": "用占位符写出的一句话示例，展示该事件能表达的信息上限，方便你或 AI 判断写多少内容；不会直接展示给用户。",
        "phrases": "与顶层 phrases 中同名 key 的数组，两处应保持一致。",
    },
    "事件 key 分组（按前缀识别场景）": {
        "start / thinking": "Agent 开始工作 / 思考中。",
        "activity.read / search / edit / run / default": "干活过程：读取文件 / 搜索 / 编辑代码 / 运行测试 / 其他工具。",
        "agent.attention / error / missing": "Agent 状态：需要你处理 / 出错 / 尚未检测到。",
        "approval.command / tool / generic": "审批：命令审批 / 工具审批 / 通用审批。",
        "question.empty / one / many": "提问：无选项等待选择 / 单个问题 / 多个问题。",
        "watchdog.warning / intervention / unknown": "循环检测（重复排查）：警告 / 建议干预 / 无法判断。",
        "pattern.warning / pattern.control": "行为重复检测：警告 / 自动干预。",
        "rate_limit.one / many、llm_error.api": "限流（单次/连续） / AI 服务出错。",
        "done.success / done.attention": "收尾：任务完成 / 停下等你确认。",
        "failure.retry / tool / generic": "本轮失败：重试后仍失败 / 工具执行失败 / 通用失败。",
        "control.replan.* / control.interrupt.* / control.failed": "看门狗控制：重新规划 / 终止 / 控制失败。",
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
        group = _event_group(key)
        parameters = list(dict.fromkeys(EVENT_FIELDS.get(group, ()) + PARAMETERS.get(key, ())))
        entries.append({"key": key, "description": key, "sources": list(EVENT_SOURCES.get(key, ())), "parameters": parameters, "displayHint": DISPLAY_HINTS.get(key, ""), "phrases": copy.deepcopy(value)})
    mode = str(config.get("dialogue_mode", "custom") or "custom")
    document = {
        "template": TEMPLATE_VERSION,
        "mode": mode if mode in {"legacy", "whale_maid", "custom"} else "custom",
        "name": str(config.get("persona_template_name", "我的角色台词") or "我的角色台词"),
        "description": "Pet 全部弹窗/气泡内容模板。每个 entries 项的 parameters 是该事件保证可用的参数（组字段为上游记录字段，事件触发时可读）；同时支持自动读取上游事件记录字段。",
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
