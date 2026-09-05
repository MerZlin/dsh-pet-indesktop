# 开源 Harness 风险与轨迹设计调研

## 范围
本调研覆盖 DSH、LangGraph、SWE-agent 以及通用开源 Harness 设计。

## 对比表

| 维度 | DSH | LangGraph | SWE-agent | Pet 采用结论 |
|---|---|---|---|---|
| Event Model | `session/event` 事实流与 `agent/*` 实时/控制流分层 | 图节点执行与 checkpoint 状态 | trajectory 中保存 action/observation | 保留 raw facts，Pet 聚合 Run/Step |
| Identity | session、agent、turn、step | `thread_id` 绑定可恢复状态 | trajectory/run 标识 | `(source, session_id)` 隔离，无 ID 不合并 |
| Action | tool/call、command、step | 节点调用和工具调用 | action step | 记录 ToolCall/Action |
| Observation | tool/result、agent 状态 | 节点返回值与 checkpoint | tool output/observation | 记录 ToolResult/Evidence，限制大小 |
| Reasoning | assistant/chunk、reasoning 事件 | 状态/节点消息 | trajectory 中可选 thought | delta 只累计，不逐条计风险 |
| Retry | llm/retry、request-error | 节点失败/重试由应用处理 | agent loop 中记录失败 | Pet 按 session 统计，不由 Bridge 派生 |
| Loop Detection | 事件层没有最终风险结论 | 图状态可恢复，不等价风险判断 | trajectory 可 inspect/replay | 采用轨迹进展信号，不直接按 Read 次数 |
| Stop | agent 控制接口 | interrupt/resume | agent stop/exit | Risk 与 Policy 分离，控制链独立 |
| Interrupt | agent 控制及审批事件 | interrupt 保存中断值，使用 thread_id 恢复 | 人工/环境停止 | Interaction 是可恢复运行状态的一部分 |
| Human-in-the-loop | approval/question + control | interrupt 后 resume | 轨迹/环境交互 | 统一 InteractionResolved，精确身份匹配 |
| Persistence | JSONL bridge 事实 | checkpoint 持久化 thread 状态 | trajectory 日志 | 先做 bounded JSONL，未来可回放 |
| Replay | 依赖事件文件 | checkpoint 恢复 | inspector 查看 trajectory | 保留短轨迹摘要和诊断字段 |
| Cost Control | bridge 批量写盘/大小上限 | checkpoint、执行控制 | step/time/token 限制 | 数据大小、时间、错误与工具能力共同输入 |

## 可借鉴设计

1. **事实与控制分层**：DSH 的 `session/event` 与 `agent/*` 边界适合 Bridge；Bridge 不计算业务风险。
2. **可恢复身份**：LangGraph 将中断值与 `thread_id` 作为运行状态，而非仅展示事件；Pet 交互必须用 session 和交互 ID 精确恢复/关闭。
3. **轨迹一等公民**：SWE-agent 的 trajectory/inspector 说明 action、observation、结果应独立保存，不能只根据最后工具名决策。
4. **短摘要而非完整内容**：Pet 风险器应接收有限的 recent steps、evidence 和 progress signals，不保存完整代码、思考或命令输出。

## 不适合本项目的设计

- 将 Pet 改造成完整图执行引擎：当前 Pet 只观察和控制，不拥有 Agent 执行图。
- 复制 LangGraph checkpoint 存储：会扩大状态迁移和持久化范围；当前先使用 session 隔离的内存窗口。
- 复制 SWE-agent 全量 trajectory：会违反本项目敏感数据和文件大小限制；只保留 bounded summaries。
- 在 Bridge 内实现 loop/risk/judge：会重新形成 Bridge/Pet 双重语义。

## 需要验证的假设

- 不同 Agent 能否稳定提供 session/turn/step；缺失时必须降级而不是猜测。
- tool/result 是否始终带 callId；没有 callId 时不能跨 session 关联。
- resultSummary/evidenceHash 是否足以判断新证据，需用真实样本验证。
- 长 Think 的时间占比是否比 Think 数量更能区分正常推理与无进展。
- 外部 approval/question resolved 与 tool/result 的到达顺序是否跨版本稳定。

## 最终采用的设计

Pet 内部采用：

```text
Run(source, session)
  └── Step(turn, step)
        ├── Reasoning(summary/duration)
        ├── actions: ToolCall/Action
        ├── observations: ToolResult
        └── evidence: bounded Evidence
```

风险估计分为五组：行为重复、信息增益、任务推进、轨迹方向、运行成本/状态。输出结构化 `RiskEstimate`，包含 score、confidence、signals、progress；不产生台词或控制按钮。现有 W6/W10 仅作为兼容候选窗口，不能作为最终规则。

Watchdog Policy 单独将风险映射为 `NORMAL`、`WARNING`、`JUDGE_REQUIRED` 或 `CONTROL_REQUIRED`，并负责 generation、cooldown 和交互抢占。

## 明确放弃的设计

- 仅按 Read/Think 数量触发风险；
- Bridge 产生 `rate_limit`、`execution/failed` 风险结论；
- 无 session ID 时猜测合并事件；
- 用完整思考、代码或命令作为长期诊断数据；
- 把控制请求结果当作风险估计；
- 要求 Claude、Cursor、OpenCode、Custom Agent 同步升级输入协议。

## 参考资料

- [LangGraph Interrupts](https://langchain-5e9cc07a.mintlify.app/oss/python/langgraph/interrupts#pause-using-interrupt)
- [LangGraph Interrupt reference](https://reference.langchain.com/python/langgraph/types/interrupt)
- [SWE-agent Agent reference](https://swe-agent.com/latest/reference/agent/)
- [SWE-agent trajectory inspection](https://swe-agent.com/latest/usage/inspector/)
- [SWE-agent CLI](https://swe-agent.com/1.0/usage/cli/)
