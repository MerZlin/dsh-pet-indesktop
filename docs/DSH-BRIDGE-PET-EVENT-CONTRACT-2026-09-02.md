# Agent 适配器 → Pet 事件契约与接口说明

本文记录当前 DSH 适配器的三层关系，并定义其他 Agent 接入 Pet 时需要遵守的共同契约。
这里将 Bridge 统一称为“Agent 适配器”：它负责把某个 Agent 的原始事件转换为 Pet 能理解
的标准事件。接入新的 Agent 时，只需实现一个遵循本契约的适配器，Pet 侧即可复用现有的
状态处理、交互队列、按钮回写和气泡展示逻辑，不需要为每个 Agent 重写一套 UI。

本文描述当前代码，不把尚未接入的事件写成已支持接口：

```text
Agent 原始事件 / Mux frame
        ↓
Agent 适配器转换并写入标准 JSONL
        ↓
Pet 对应 Monitor 读取并发出 Qt Signal
        ↓
AgentLinkManager / AgentEventRuntime
        ↓
PetWindow 提醒队列、气泡和桌宠行为
```

当前 DSH 适配器的人工请求调研见 `docs/DSH-HUMAN-REQUEST-RESEARCH-2026-09-02.md`。

## 适配器接入原则

Pet 不直接依赖某个 Agent 的内部事件名称、日志格式或 UI。新的 Agent 适配器需要完成：

1. 读取 Agent 自己的事件源。
2. 转换为 `agent-event/v1` 标准记录。
3. 为审批、问题和完成事件提供稳定关联键。
4. 通过对应的 Monitor Signal 交给 `AgentLinkManager`。
5. 如果支持用户操作，实现与 Agent 自己的响应回写通道。

只要适配器输出的事件语义和关联键符合本文契约，Pet 就能复用状态映射、多 session 隔离、
pending 队列、气泡优先级和幂等清理逻辑。适配器可以使用文件、Socket、HTTP 或其他 Agent
原生通道；传输方式不是 Pet 契约的一部分。

## 旧式基础适配器（新适配器之前）

远程仓库 `MerZlin/dsh-pet-indesktop` 的 `origin/main` 及其历史实现表明，早期 Agent
适配器主要是“状态观察器”，并没有完整的请求/响应事件概念。它们只提供 Agent 是否开始、
正在运行、调用了什么工具、结束或失败等基础信息。

旧式适配器的共同输出可以概括为：

```text
开始工作      → thinking / working
读取或搜索    → activity(tool)
执行命令      → activity(tool) + working
编辑或写入    → activity(tool) + working
完成          → idle
失败          → error
等待用户      → attention（仅状态提示，不代表可回写审批）
```

### Claude Code 旧适配器

通过官方 hooks 触发本地脚本，把简单 JSON 行追加到 `agent-events/claude.jsonl`。
历史适配的 hooks 包括：

```text
SessionStart
UserPromptSubmit
PreToolUse
PostToolUse
PostToolUseFailure
Stop
```

通常只有 `event`、`agent`、`ts` 和可选的 `tool` 字段，没有 session/request/rpc 关联键。

### Cursor 旧适配器

直接读取 `~/.cursor/projects/**/agent-transcripts/*.jsonl`，从 transcript 结构推断状态：

```text
role=user                         → thinking
role=assistant + tool_use         → working + activity(tool)
role=assistant + 纯文本            → idle
```

未知 transcript 行会被忽略。它是内容观察和状态推断，不是审批协议。

### OpenCode 旧适配器

只读 `~/.local/share/opencode/opencode.db` 的 `event` 表，按 rowid 增量读取：

```text
message.updated(role=user)       → thinking
message.part.updated(step-start) → working
message.part.updated(reasoning)   → thinking
message.part.updated(step-finish)→ idle
message.part.updated(tool)        → activity(part.tool)
```

它不会从 `edit`、`read` 或 `bash` 的工具名称推导出审批请求。

### Custom Agent 旧适配器

`CustomAgentMonitor` 是只读通道，读取用户指定的 JSONL 文件，不创建目录、不改写外部
配置，也不提供 Agent 响应回写。它适合输出显式 `state` 或基础 `event/tool`：

```json
{"ts": 0, "agent": "myagent", "event": "PreToolUse", "tool": "read"}
```

旧式基础适配器的能力边界：

```text
支持：状态、工具活动、普通错误和完成提示
不保证：session 隔离、审批按钮、用户问题、resolved 精确关联、决定回写
```

因此旧适配器直接经过新的标准化解析，目前只会增加语义分析输入，不应自动获得交互
能力。缺少完整关联键的记录只能作为 Activity/Risk 分析；不能补造 `rpcId`、`requestId`
或 `sessionId`，也不能凭工具名称生成审批请求。

### 旧适配器行为的统一事件归类

旧适配器的所有有效行为，目前都可以归入以下五类；其中只有前四类会改变 Pet 的直接
表现，第五类用于分析但不应触发气泡：

| 行为类别 | 旧适配器输入 | 统一事件/Signal | Pet 处理 |
|---|---|---|---|
| 开始思考 | `UserPromptSubmit`、Cursor `role=user`、OpenCode 用户消息 | `LifecycleEvent` / `state_changed(thinking)` | 思考动画，可选开始提示 |
| 正在工作 | `PreToolUse`、Cursor assistant `tool_use`、OpenCode `step-start`、显式 `state=working` | `LifecycleEvent` / `state_changed(working)` | 工作动画 |
| 工具活动 | `tool` 字段、Cursor tool name、OpenCode `part.tool` | `ToolCallEvent` 或 `activity(agent, tool)` | 读取、搜索、编辑、执行等过程提示；不代表审批 |
| 完成或等待 | `SessionEnd`、`Stop`、OpenCode `step-finish`、显式 `state=idle/attention` | `LifecycleEvent` / `state_changed(idle/attention)` | 待机、完成提示或普通注意提示 |
| 失败 | `PostToolUseFailure`、`StopFailure`、显式 `state=error` | `ErrorEvent` / `state_changed(error)` | 错误动画和重要错误气泡 |

其中工具活动的常见工具类别是：

```text
read / grep / glob / search  → 探索 Activity
edit / write / patch         → 修改 Activity
bash / shell / pwsh         → 执行 Activity
其他工具                     → 通用 Activity
```

下面这些不是旧适配器提供的事件类别：

```text
approval/requested
question/requested
approval/resolved
question/resolved
cordis/request-run
```

因此旧适配器的“编辑”“执行命令”“读取文件”只能归类为 `ToolCallEvent`、`ActionEvent`、
`EvidenceEvent` 或风险分析输入，不能直接归类为审批请求。

## Bridge JSONL 基础记录

基础记录形如：

```json
{"ts": 0, "agent": "dsh", "event": "AgentStatus", "sessionId": "..."}
```

Bridge 可能补充 `projectName`、`label`。Pet 的 `agent-event/v1` 统一记录字段为：

```text
schema, ts, source, agentName, projectId, projectName,
sessionId, sessionName, turn?, step?, event, data, callId?, requestId?
```

## 当前 DSH 适配器的映射示例

| DSH 来源 | Bridge event | 关键字段 | Pet 用途 |
|---|---|---|---|
| `approval/requested` Mux | `approval/request` | `rpcId`, `sessionId`, `approvalId`, `toolName` | 审批请求 |
| `approval/asked` session | `approval/request`，并可写 `approval/asked` | `approvalId`, `callId`, `sessionId` | 无 Mux 的审批提示 |
| `approval/resolved` Mux | `approval/resolved`、`interaction/resolved`、`user_action` | `approvalId`, `sessionId`, `outcome` | 关闭审批气泡 |
| `approval/decided` session | `approval/decided`、`user_action` | `approvalId`, `rpcId`, `sessionId` | 关闭已完成审批 |
| `question/requested` Mux | `question/requested` | `rpcId`, `sessionId`, `questions[]` | 用户问题 |
| `question/resolved` Mux | `question/resolved`、`interaction/resolved`、`user_action` | `questionRpcId`, `sessionId`, `outcome` | 关闭问题气泡 |
| `tool/call(ask_user_question)` | `question/requested` | `callId`, `sessionId`, `questions[]` | Mux 不可用时的兼容入口 |
| 匹配的 `tool/result` | `user_action` | `callId`, `sessionId` | 结束 fallback 问题 |
| `turn/step start/end` | 同名 event | `sessionId`, `step` | 状态和行为检测 |
| `tool/call` | `tool/call` | `tool`, `target`, `callId` | 工具活动，不是审批 |
| `tool/result` | `tool/result` | `tool`, `target`, `ok`, `callId` | 工具结果 |
| `assistant/message` | `assistant/message` | `text`, `sessionId` | 消息和行为分析 |
| `agent/request-error` | `agent/request-error` | 错误字段、`sessionId` | 模型请求错误 |
| 限流 / LLM 错误 | `rate_limit` / `llm_error` | `errorCode`, `sessionId` | Pet 错误提醒 |
| `execution/failed` | `execution/failed` | `errorCode`, `sessionId` | 硬失败提醒 |

当前尚未映射：`cordis/request-run`、`cordis/request-run-resolved`。这是动态 Cordis
客户端运行审批，不能伪装成普通 `approval`。`authorization.prompt()` 属于配置期凭据
授权，也不进入当前 Agent/Pet 审批队列。

## Agent 适配器输出契约

审批请求：

```json
{
  "event": "approval/request",
  "rpcId": "mux-rpc-id",
  "sessionId": "session-id",
  "approvalId": "approval-id",
  "toolName": "pwsh",
  "callId": "call-id",
  "command": "..."
}
```

问题请求：

```json
{
  "event": "question/requested",
  "rpcId": "question-rpc-id",
  "sessionId": "session-id",
  "questions": []
}
```

问题项应保留 `id`、`question`、`detail`、`header`、`options`、`multiSelect`、`intent`。
`question/resolved` 必须用 `questionRpcId` 关联原请求，不能用 resolved 外层新的 `rpcId`。

统一完成事件形如：

```json
{
  "event": "interaction/resolved",
  "source": "dsh",
  "sessionId": "session-id",
  "kind": "approval | question",
  "requestId": "...",
  "rpcId": "...",
  "approvalId": "...",
  "callId": "...",
  "outcome": "..."
}
```

## Pet 监视器 Qt 接口（适配器接入点）

`BaseAgentMonitor` 当前暴露：

```python
state_changed = Signal(str, str)       # agent_key, state
activity = Signal(str, str)            # agent_key, tool
approval_requested = Signal(str, object)
approval_resolved = Signal(str, object)
question_requested = Signal(str, object)
question_resolved = Signal(str, object)
raw_record = Signal(str, object)       # agent_key, raw record
normalized_event = Signal(object)       # SemanticEvent
execution_failed = Signal(str, object)
session_meta = Signal(str, object)
rate_limit = Signal(str, object)
llm_error = Signal(str, object)
user_action = Signal(str, object)
```

生命周期方法：`start()`、`stop()`、`pause()`、`resume()`、`is_running()`。

`DshMonitor` 是当前 DSH 适配器对应的 Monitor，从适配器目录增量读取 `dsh*.jsonl`，支持
多个 DSH 实例的分文件消费。其他 Agent 可实现自己的 Monitor，但应发出相同的标准 Signal
语义。

## Pet 统一语义事件接口

`normalized_event` 当前可能输出：

```text
LifecycleEvent, ToolCallEvent, ToolResultEvent, ReasoningEvent,
EvidenceEvent, ActionEvent, RetryEvent, ErrorEvent, ApprovalEvent,
QuestionEvent, UserActionEvent, InteractionResolvedEvent, ControlResultEvent
```

公共字段为 `source`、`agent_name`、`session_id`、`step`、`data`。

`InteractionResolvedEvent` 还包含：

```text
kind, request_id, rpc_id, approval_id, call_id, outcome
```

`ControlResultEvent` 还包含：`request_id`、`operation`、`ok`、`phase`。

`AgentEventRuntime.dispatch()` 是语义事件 fan-out 入口；单个消费者异常不会阻断其他消费者。

## AgentLinkManager 交互接口

阻塞交互按 `interaction_id -> item` 保存。Item 可能包含：

```text
kind, text, agent_key, interactive, rpc_id?, approval_id?, call_id?,
session_id?, questions?, alert_id
```

当前管理接口：

```python
pending_interactions_for(agent_key: str) -> dict[str, dict]
dismiss_all_interactions() -> None
dismiss_all_approvals() -> None  # 兼容别名
```

请求入口是 `_on_approval_request()`、`_on_question_request()`；完成入口是
`_on_approval_resolved()`、`_on_question_resolved()`、`_on_normalized_event()` 和
`_resolve_interaction()`。

按钮点击后异步 POST `/api/respond`：审批使用 `allowed-once` 或 `rejected`；问题使用
`answer.answers[].selected`。本地交互先移除，随后等待 DSH resolved 事件确认。

## PetWindow 气泡接口

普通气泡：

```python
show_bubble(text, duration_ms=3200, subtitle=None, *, sticky=False, buttons=None) -> None
```

提醒队列：

```python
show_alert(text, *, subtitle="", duration_ms=0, buttons=None,
           sticky=True, alert_id="", priority=3, alert_type="watchdog", metadata=None) -> None
resolve_alert(alert_id: str) -> None
clear_alerts() -> None
```

`alert_id` 是队列稳定身份。`resolve_alert()` 只移除目标提醒；若目标正在展示，会自动推进
队列，不改变其他提醒顺序。

## 跨 Agent 复用边界

Pet 复用的是标准事件语义，不是 DSH 的具体事件名称。新的 Agent 适配器至少应提供：

```text
Agent 原始事件 → agent-event/v1
请求事件       → approval/request 或 question/requested
完成事件       → 对应 resolved / interaction/resolved
状态事件       → idle / thinking / working / attention / sleeping / error
用户决定回写   → Agent 自己的响应协议
```

如果某个 Agent 没有审批或用户问题能力，可以只实现状态和工具活动事件；Pet 仍可复用状态
动画和普通通知，但不会凭工具调用名称虚构审批交互。

## 当前验收重点

1. `tool/call(edit)` 不是审批，只有真实 `approval/*` 请求才生成审批交互。
2. 多 session 必须同时匹配 `sessionId` 和请求关联键。
3. resolved、decided、按钮回写和 session 结束清理必须幂等。
4. `cordis/request-run` 当前未接入，不能报告为 Pet 已支持。
5. 官方契约之外的 `edit/requested` 等事件必须保留原始 payload 和生产者信息，不能用
   正则直接归类为审批。

## 代码依据

- [Bridge](</W:/deepseek-harness/dsh-pet-indesktop/integrations/dsh-pet-bridge/index.js>)
- [AgentLinkManager 与监视器](</W:/deepseek-harness/dsh-pet-indesktop/pet/agent_link.py>)
- [统一事件协议](</W:/deepseek-harness/dsh-pet-indesktop/pet/agent_event_protocol.py>)
- [事件规范化](</W:/deepseek-harness/dsh-pet-indesktop/pet/agent_event_normalizer.py>)
- [PetWindow](</W:/deepseek-harness/dsh-pet-indesktop/pet/window.py>)
