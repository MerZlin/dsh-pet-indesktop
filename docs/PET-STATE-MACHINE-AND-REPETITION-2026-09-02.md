# Pet 状态机与重复检查说明

## 目的

本文说明 Pet 如何把 Agent 的事件转换成状态、动画、提醒和风险判断，重点记录当前
“重复检查”相关的两个检测器，避免把它们误认为同一个状态机。

当前有三条相互独立、再汇聚到 Pet UI 的处理链：

```text
Agent/Bridge 事件
      ├─ DshStateTracker：DSH 在线状态和阻塞状态
      ├─ AgentLinkManager：Agent 状态、活动和交互气泡
      └─ 分析检测器：BehaviorPatternDetector / ExplorationWatchdog / StuckDetector
```

## 一、基础状态机：DshStateTracker

`DshStateTracker` 的可见状态为：

```text
offline
idle
thinking
working
waiting_approval
waiting_question
success
error
```

主要转换：

| 输入 | 状态 |
|---|---|
| DSH 不在线 | `offline` |
| 在线但无活动 | `idle` |
| `user/message`、`turn/start`、`plan/mode` | `thinking` |
| `assistant/message`、`tool/call`、`tool/result`、`step/*`、`command/*` | `working` |
| `approval/asked`、`approval/request` | `waiting_approval` |
| `question/requested` | `waiting_question` |
| `approval/decided`、`question/resolved` | 回到 `working` |
| `turn/end` | `success` |
| `llm/retry`、LLM 错误 | `error` |

### 阻塞交互锁存

进入 `waiting_approval` 或 `waiting_question` 后，状态机会暂时忽略后续的
`thinking/working`，直到收到对应的 `decided/resolved`。这是为了防止 Agent 等待用户时，
后续普通状态把等待状态顶掉。

如果 DSH 长时间没有发完成事件，当前有 120 秒兜底，超时后回到 `working`。如果 DSH
离线，审批和问题锁存都会清除并转为 `offline`。

## 二、AgentLinkManager：动画和交互 UI 状态

AgentLinkManager 处理更丰富的 Pet 行为：

```text
thinking / working → 联动动画和可选过程气泡
activity           → 读取、搜索、编辑、执行等工具提示
approval request   → pending 交互 + 高优先级气泡
question request   → pending 交互 + 高优先级气泡
resolved           → 按 alert_id 精确移除对应气泡
idle / sleeping    → 清理该 Agent 的失效 pending 交互
error / failure    → 重要错误提醒
```

阻塞交互保存在：

```text
interaction_id → kind, session_id, rpc_id, approval_id, call_id, alert_id, ...
```

完成事件只应根据 `sessionId` 和请求关联键移除目标，不根据队列位置移除。按钮点击后
Pet 先本地收起交互，再异步向 DSH 回写；DSH 的 resolved 事件负责确认，重复完成事件必须
幂等。

## 三、重复检查不是状态转换

重复检查只产生风险信号，不直接代表：

```text
waiting_approval
waiting_question
error
task_complete
```

它们的输出是 `warning` 或 `control/judge`，再由 Judge 判断是否：

```text
NORMAL
REPLAN
ASK_USER
STOP
```

因此“检测到重复”不等于“自动终止 Agent”。默认没有 Judge 时，Control 会降级为
`REPLAN`，只发出重新规划建议，不直接中断 Agent。

## 四、BehaviorPatternDetector：按 step 的行为模式检查

### 4.1 行为分类

工具先归类为细分类：

```text
SEARCH、READ、THINK、NAVIGATION、EDIT、EXECUTE、TEST、OTHER
```

再归为大类：

```text
EXPLORATION = SEARCH / READ / THINK / NAVIGATION
ACTION      = EDIT / EXECUTE / TEST
OTHER       = OTHER
```

### 4.2 窗口和阈值

默认规则如下：

| 窗口 | 条件 | 输出 |
|---|---|---|
| W6 | 同一细分类出现至少 3 个 step | `control`，短时高密度重复 |
| W10 | 同一细分类出现至少 3 个 step | `warning`，重复倾向 |
| W10 | 同一细分类出现至少 4 个 step | `control`，长期重复 |
| W6 | `EXPLORATION >= 5` 且 `ACTION == 0` | `control`，纯探索无产出 |
| W10 | `EXPLORATION >= 7` 且 `ACTION <= 1` | `warning`，探索为主 |

W6 是短时爆发视图，W10 是长期趋势视图。一个 step 内并行发生的多个同类工具调用只
计算一次，因此同一步 Search A/B/C 不会被当成三次重复。

### 4.3 触发顺序与冷却

规则判断不是简单“谁先到谁触发”：

1. 先计算当前 step 的 W6、W10 和宏观类别统计。
2. 细分类 W6 control 优先于 W10 control。
3. W10 control 优先于纯探索宏观 control。
4. warning 低于 control；warning 升级为 control 时跳过普通冷却门控。
5. 同档位触发需要至少新增 3 个 step，且默认间隔 60 秒。
6. Agent 空闲、任务结束或检测器重置后，窗口清空。

### 4.4 输出信号

```python
pattern_warning = Signal(str, object)  # agent_key, payload
pattern_control = Signal(str, object)  # agent_key, payload
pattern_resolved = Signal(str)          # agent_key
```

payload 包含：

```text
type = "pet/behavior-pattern"
level
reason
class / macro
count
window
step
verdict
```

## 五、ExplorationWatchdog：按 session 的探索循环检查

ExplorationWatchdog 与 BehaviorPatternDetector 不同，它按 `session` 保存状态，重点看：

```text
重复目标
重复探索指纹
重复 Think
探索后是否产生 Action
是否出现新证据
是否访问新 target
```

它通常使用 W6/W10 观察窗口，但最终通过风险评分决定：

```text
低于 warning 阈值 → 不输出
达到 warning 阈值 → warning
达到 control 阈值 → judge_required
```

默认参数为：

```text
warning_threshold = 3
control_threshold = 5
cooldown_steps = 3
```

启动早期会提高阈值，长时间运行会降低阈值。重复 Think、重复目标和重复指纹的分数不同；
Edit/Run/Test、新 target、新证据会降低风险，避免把正常收敛过程误判成循环。

输出信号：

```python
warning = Signal(str, object)
judge_required = Signal(str, object)
judge_result = Signal(str, object)
```

`judge_required` 只表示“需要 Judge 检查”，不表示已经决定停止。

## 六、StuckDetector：失败/超时型卡住检查

StuckDetector 不是重复探索检测器，主要处理：

```text
连续工具失败
重复超时
同一目标反复失败
相同根因没有进展
LLM retry / 错误指纹
```

默认评分：

```text
stuck_score >= 3 → worried
stuck_score >= 5 → intervention_recommended
```

它与行为重复检测互补：一个 Agent 可以没有重复工具，但因连续超时被判定为卡住；也可以
反复 Read/Think 但没有错误，此时主要由行为模式或 ExplorationWatchdog 发现。

## 七、重复检查的统一生命周期

```text
tool/call / command/run / tool-workflow/run-start
        ↓
按 Agent 或 session 接收
        ↓
同 step 去重 / 目标与指纹归一化
        ↓
加入 W6/W10 或时间窗口
        ↓
计算 warning / control / stuck risk
        ↓
warning：提示
control：调用 Judge（若配置）
        ↓
NORMAL / REPLAN / ASK_USER / STOP
        ↓
turn/end、idle、session 结束或新一代控制结果后清理
```

## 八、当前已知边界

1. 旧 Agent 适配器通常只有状态和工具名，因此只能提供 Activity/Risk 输入，不能提供
   审批或问题的精确关联。
2. W6/W10 是滑动观察窗口，不是“最近 N 次工具调用”的简单计数；统计单位是 step。
3. 同一步并行工具调用会去重，防止正常并发搜索放大风险。
4. warning 不调用 Judge；control 才进入 Judge 路径。
5. Judge 返回无效 JSON 或超时，不能被当成 STOP；必须使用安全降级策略并记录原因。
6. 重复检查的 Risk/Warning/Control 不应覆盖真实 approval/question 交互，也不应直接
   清理其他 session 的 pending 事件。

## 代码依据

- [行为模式检测器](</W:/deepseek-harness/dsh-pet-indesktop/pet/behavior_detector.py>)
- [探索循环 Watchdog](</W:/deepseek-harness/dsh-pet-indesktop/pet/exploration_watchdog.py>)
- [卡住检测器](</W:/deepseek-harness/dsh-pet-indesktop/pet/stuck_detector.py>)
- [DSH 状态跟踪器](</W:/deepseek-harness/dsh-pet-indesktop/pet/dsh_state.py>)
- [Agent 联动管理器](</W:/deepseek-harness/dsh-pet-indesktop/pet/agent_link.py>)
