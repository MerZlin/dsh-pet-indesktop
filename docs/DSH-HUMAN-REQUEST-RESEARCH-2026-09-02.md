# DSH 人工请求事件调研与 Bridge 说明

## 目的与范围

本文记录 2026-09-02 对 DSH `0.1.1-rc.2` 安装包、官方类型定义和官方扩展文档的核对结果。
目标是确认哪些 DSH 信号表示 Agent 可能暂停并等待用户批准、拒绝或回答，以及哪些信号
只是工具或生命周期记录。

本文描述 DSH 当前契约，不代表 Bridge 已完成全部支持。实现状态以
`integrations/dsh-pet-bridge/index.js` 和测试结果为准。

## 结论摘要

当前应识别三类 Agent 侧人工交互：

1. 统一工具审批：`approval/requested`，以及对应的 `approval/asked` 审计事件。
2. 用户问题：`question/requested`，包括单选、多选、自由文本和 Plan Review。
3. 动态 Cordis 客户端运行审批：`cordis/request-run`，但仅在
   `requiresApproval === true` 时成立。

凭据登录中的 `authorization.prompt()` 是配置期授权流程，不属于 Agent session 审批队列。

## 一、统一工具审批

工具是否需要人工决定，取决于它是否进入 DSH 的统一 Approval Service，而不是工具名称。
因此 `edit`、`write`、`bash`、`pwsh` 或沙箱升级都可能触发审批，但普通工具调用本身不是
审批请求。

相关信号：

```text
approval/request
approval/asked
approval/requested
approval/decided
approval/resolved
```

Bridge 对外转发时应保留：

```text
sessionId
rpcId
approvalId
toolName
callId?
reason?
```

完成事件按 `sessionId + approvalId` 匹配，必要时再使用 `callId`；回答需要回显原始
`rpcId`。

## 二、用户问题

正式的阻塞请求是：

```text
question/requested
question/resolved
```

一个 `question/requested` 可以包含多个问题项，不能只保留第一项。每项可能包含：

```text
id
question
detail?
header?
options[]
multiSelect?
intent?
```

目前已知的 `intent` 是 `plan-review`。它是问题的语义和展示意图，不是新的事件类型。

当 Mux 不可用时，`tool/call` 且 `name === "ask_user_question"` 可以作为兼容来源；只有该
调用确实处于 pending 状态时，匹配的 `tool/result` 才能结束它。

关联规则：

```text
question/requested.msg.rpcId       -> 原始问题请求
question/resolved.questionRpcId    -> 原始问题请求
```

不能使用 `question/resolved` 外层新生成的 `msg.rpcId` 代替 `questionRpcId`。

## 三、动态 Cordis 客户端运行审批

带浏览器端代码的动态 Cordis 包在运行时可能需要页面上的人批准：

```text
cordis/request-run
cordis/request-run-resolved
```

请求中应保留：

```text
requestId
agentId
pluginId
packageId
mode
name
purpose
requiresApproval
```

只有 `requiresApproval === true` 才生成 Pet 交互请求。纯 Host 包直接运行时，不应凭
`cordis_run` 工具名生成审批气泡。

Cordis 不能复用普通 `approval` 的响应模型，因为它有自己的运行生命周期和结果集合：

```text
approved | rejected | cancelled | failed | completed
```

## 四、独立分类：凭据授权

`ctx.authorization.prompt()` 用于 OAuth、验证码、密钥或账号选择等配置期对话，支持：

```text
text
secret
select
```

其终态是 `authorization/settled`。该流程不进入 Agent 请求历史，也不属于当前 Web Mux
的 approval/question union。除非产品明确要求 Pet 代理设置页授权，否则保持在配置 UI 内部。

## 明确不能当作审批的事件

以下事件不能单凭名称生成用户审批气泡：

```text
agent/request
agent/request-error
tool/call
tool/result
plan/mode
session/queue
普通 edit 调用
普通 write、bash、pwsh 调用
```

`agent/request` 是模型请求生命周期事件；`plan/mode` 是模式状态；`session/queue` 是队列
操作；普通工具事件只是动作记录。它们只有在同时产生正式请求信号时，才与人工交互有关。

## 对当前 Bridge 的核对结论

作为 P0 验收依据，必须注意：

1. 不能用 `edit/requested`、`permission/requested`、`action/requested` 等正则猜测审批。
   如果日志中出现官方契约之外的事件，应先记录原始事件、生产者和完整 payload。
2. `question/requested` 必须保留 `detail` 和 `intent`，否则 Plan Review 语义会丢失。
3. `question/resolved` 必须使用 `questionRpcId` 关联原请求。
4. 若接入 `cordis/request-run`，应增加独立的 Cordis kind，不应伪装成普通 approval。
5. 所有完成事件必须按 session 和自身请求 ID 幂等移除 Pet pending 队列中的对应项目，不得
   依赖队列位置或气泡文本。

## 官方依据

- [GUI layering and RPC protocol](https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-07-19-gui-layering-and-rpc-protocol.md)
- [Web permission and approval](https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/feature/2026-07-23-web-permission-and-approval.zh.md)
- [User question types](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/interaction/user-questions/src/types.ts)
- [ask_user_question](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/interaction/tool-ask-user/README.md)
- [Cordis host runner](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/extensions/cordis-host-runner/README.md)
- [Authorization service](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/credentials/authorization/README.zh.md)
- [Event producer and consumer map](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/event-producer-consumer.md)
