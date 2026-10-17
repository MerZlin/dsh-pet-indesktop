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
AgentLinkManager（语义事件直连消费）
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

Bridge 会直接补充字面字段 `projectName`、`sessionName`。`label` 仅保留给工具标签等业务语义，不再承载会话名称。Pet 的 `agent-event/v1` 统一记录字段为：

```text
schema, ts, source, agentName, projectId, projectName,
sessionId, sessionName, turn?, step?, event, data, callId?, requestId?
```

## 当前 DSH 适配器的映射示例

| DSH 来源 | Bridge event | 关键字段 | Pet 用途 |
|---|---|---|---|
| `approval/requested` Mux | `approval/request` | `rpcId`, `sessionId`, `approvalId`, `toolName` | 审批请求 |
| `approval/asked` session | 仅 `approval/asked`（状态/审计转发） | `event`, `step`, `sessionId`, `agentName`（状态事件只带身份字段，无 approvalId/callId） | PR57 起不再映射为 `approval/request`：只驱动 `dsh_state` 锁存 waiting_approval，不弹审批气泡 |
| `approval/resolved` Mux | `approval/resolved`、`interaction/resolved`、`user_action` | `approvalId`, `sessionId`, `outcome` | 关闭审批气泡 |
| `approval/decided` session | `approval/decided`、`user_action` | `approvalId`, `rpcId`, `sessionId` | 关闭已完成审批 |
| `question/requested` Mux | `question/requested` | `rpcId`, `sessionId`, `questions[]` | 用户问题 |
| `question/resolved` Mux | `question/resolved`、`interaction/resolved`、`user_action` | `rpcId`, `sessionId`, `outcome` | 关闭问题气泡 |
| `tool/call(ask_user_question)` | `question/requested` | `callId`, `sessionId`, `questions[]` | Mux 不可用时的兼容入口 |
| 匹配的 `tool/result` | `question/resolved` | `callId`, `sessionId` | 结束 fallback 问题（PR57 起由 `resolveQuestion` 直接写盘，不再补写 `user_action` 兜底块） |
| `turn/step start/end` | 同名 event | `sessionId`, `step` | 状态和行为检测 |
| `tool/call` | `tool/call` | `tool`, `target`, `callId` | 工具活动，不是审批 |
| `tool/result` | `tool/result` | `tool`, `target`, `ok`, `callId` | 工具结果 |
| `assistant/message` | `assistant/message` | `text`, `sessionId` | 消息和行为分析 |
| `agent/request-error` | `agent/request-error` | 错误字段（`errorCode`/`errorMessage`，无 `sessionId`） | 模型请求错误（stuck_detector 取根因码） |
| 模型访问失败（429）/ LLM 错误 | `model_access` / `llm_error` | `errorCode`, `sessionId` | Pet 错误提醒 |
| `execution/failed` | `execution/failed` | `errorCode`, `sessionId` | 硬失败提醒 |

`cordis/request-run`、`cordis/request-run-resolved` 已接入。这是动态 Cordis 客户端
运行审批，不能伪装成普通 `approval`，因此桥接按 `kind: "cordis"` 单独写盘：原始 request
整体嵌在 `payload` 下（`requiresApproval` 也在 `payload` 内），顶层只带 `requestId`、
`agentId`/`sessionId` 等身份字段。Pet 侧门禁从嵌套 `payload.requiresApproval === true`
判定，同时兼容旧版桥/手写桩把字段平铺在顶层的写法。`authorization.prompt()` 属于配置期
凭据授权，不进入当前 Agent/Pet 审批队列。

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
`question/resolved` 用字段 `rpcId` 关联原请求（取 `payload.questionRpcId`，缺失时回退该
mux 帧外层的 `rpcId`），不能用 resolved 时新产生的 rpcId。

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
cordis_requested = Signal(str, object)
cordis_resolved = Signal(str, object)
raw_record = Signal(str, object)       # agent_key, raw record
normalized_event = Signal(object)       # SemanticEvent
execution_failed = Signal(str, object)
session_meta = Signal(str, object)
model_access = Signal(str, object)
llm_error = Signal(str, object)
user_action = Signal(str, object)
unknown_bridge_event = Signal(str, object)
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
QuestionEvent, UserActionEvent, InteractionResolvedEvent
```

公共字段为 `source`、`agent_name`、`session_id`、`step`、`data`。

`InteractionResolvedEvent` 还包含：

```text
kind, request_id, rpc_id, approval_id, call_id, outcome
```

语义事件当前只有 `AgentLinkManager._on_normalized_event` 一个消费方（`normalized_event`
信号直连）：它只用 `RetryEvent` 维护模型访问失败连续计数、用
`InteractionResolvedEvent` 做交互 resolved 兜底清理，其余 dataclass 属于分类契约，
暂无消费者。原先预留的 `AgentEventRuntime` 分发层自引入起零消费方，已在 PR57 移除。

`ControlResultEvent` 已随 mux 中继与控制队列的删除一并退役（仓库内 `*.py` / `*.js`
都没有生产者，消费者也不识别它）。旧桥若仍写 `control-result`、
`bridge/control-result` 或 `watchdog/control-result`，会落 `agent_link` 的
`unknown_bridge_event` 提醒路径——这是退役事件的预期口径（提醒更新 bridge），
不再单独为它保留语义分支。

## AgentLinkManager 交互接口

阻塞交互按 `interaction_id -> item` 保存（**有界**：`_PENDING_INTERACTION_MAX` = 256 条，
满了淘汰最低优先级里最旧的一条并留 warning + 计数；淘汰只影响本地呈现，不写回上游）。
Item 可能包含：

```text
kind, text, agent_key, interactive, rpc_id?, approval_id?, call_id?,
session_id?, questions?, alert_id, priority, delivered, _ts_mono
```

`delivered` = 这条交互是否真的交给了可承接的呈现目标（有可见窗）；为假时它只活在
本表里，由 `redeliver_pending_interactions()` 在呈现目标回来后按同一 `interaction_id`
补投（显示 owner 按 `alert_id` 就地合并，最多一份可见）。`_ts_mono` 只用于无身份
降级提示的 TTL 收口，带身份的审批/问题是长待批状态，不适用 TTL。

当前管理接口：

```python
pending_interactions_for(agent_key: str) -> dict[str, dict]
redeliver_pending_interactions() -> int
dismiss_all_interactions() -> None
dismiss_all_approvals() -> None  # 兼容别名
```

请求入口是 `_on_approval_request()`、`_on_question_request()`；完成入口是
`_on_approval_resolved()`、`_on_question_resolved()`、`_on_normalized_event()` 和
`_resolve_interaction()`。收尾匹配的唯一实现是 `_resolve_matching_interaction()`
（来源 + 会话 + **同字段**身份），四条入口共用它：同一条记录喂两条通道时，第二条
结构性 no-op。

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
4. `cordis/request-run` 已接入，但与原 `approval` 区分：`requiresApproval` 嵌在
   `payload` 内，报告时按 cordis 运行审批说明，不能混作普通审批。
5. 官方契约之外的 `edit/requested` 等事件必须保留原始 payload 和生产者信息，不能用
   正则直接归类为审批。

## 远程消费端（手机）与只读传输端点

契约本身只规定记录语义，传输方式不属契约（见「适配器接入原则」第 5 条）。目前
有一个远程消费端的实现：`integrations/dsh-pet-bridge/lan-stream.js` —— 它把桥目录
里**逐行的原始记录**用只读 HTTP 端点转发给同一局域网内的手机桌宠。

必须保持不变的三条：

1. **不新增事件协议**：`records[]` 里的对象就是桥接插件 `encodeRecord` 写进
   `dsh-{pid}.jsonl` 的那一行，字段、`inst` 写者归属、脱敏口径全部原样；端点不做
   改名、不补默认值、不按自己的理解重排。
2. **传输端不参与插件加载**：该文件不被 `index.js` import（零依赖红线与
   `verify_import.mjs` 的动态 import 禁令），DSH 插件树启动路径完全不变；它由用户
   显式启动，默认只监听回环地址。
3. **挂载语义与桌宠一致**：不带游标时只返回 EOF 游标、不回放历史；只交出完整行
   （半个行留到下一次轮询），与 `DirGlobTailer` 的 tail 口径相同。消费端的统一
   状态映射必须照抄 `pet/dsh_state.py`（手机侧是
   `android/.../DshLinkState.kt`），不得各自发明「什么算在忙」。

契约测试：`tests/test_bridge_lan_stream.js`（`node --test`，随 PR 门禁一起跑）。

## 代码依据

- [Bridge](integrations/dsh-pet-bridge/index.js)
- [只读远程传输端](integrations/dsh-pet-bridge/lan-stream.js)、[其契约测试](tests/test_bridge_lan_stream.js)
- [AgentLinkManager 与监视器](pet/agent_link.py)
- [统一事件协议](pet/agent_event_protocol.py)
- [事件规范化](pet/agent_event_normalizer.py)
- [PetWindow](pet/window.py)
