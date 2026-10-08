# PR 报告：DSH 联动完成提醒（`turn/end → success`）

> **基线**：`ee699c7`（Merge pull request #233 from MerZlin/feature/agent-link-subtraction）
> **分支**：`fix/agent-link-success-done-notify`　**日期**：2026-10-07
> **范围**：2 个文件（实现 1、测试 1；文档即本文件）　**⚠ 实现以 §九 / §十 为准**（§九 的 `_saw_failure` 设计已被 §十 取代；最终数字见 §十）
> **关联**：issue **#234**（`[Bug] DSH 联动：回合成功结束（turn/end → success）不触发任何完成提醒`）

## 一、核心特性

DSH 联动开启后，Agent 跑完一个回合应当弹完成气泡（并按 `sound_done_enabled` 播
`done` 音效）。实际表现是**几乎永远不响**：只在 DSH 偶发补发
`AgentStatus{state:"idle"}` 时才响一次（报告者统计 676 条桥接记录里 `idle` 只出现
1 次），用户侧观感为「概率触发」。

**根因是两处缺失，不是一处。** issue 正文定位到的是第二处，并给了一份「在
`_on_agent_state` 里加一条 `success` 边沿」的最小 diff——**那份 diff 单独落地是空
操作**，因为 `success` 根本走不到 `_on_agent_state`：

| 环节 | 事实 | 后果 |
|---|---|---|
| 桥接侧 | `index.js:432-437` 明确声明 `STATE_EVENT_TYPES`（含 `turn/end`）写的记录**不带 `state` 字段**，"legacy AgentStatus working/idle baseline is untouched" | `turn/end` 只对收敛器可见 |
| legacy 分派 | `VALID_STATES = {idle, thinking, working, attention, sleeping, error}`——**没有 `success`**；`normalize_event_state("turn/end", "")` 与 `normalize_event_state("AgentStatus", "success")` 均返回 `""` | `_poll` 里 `continue`，记录被丢弃 |
| 收敛器出口 | `_on_dsh_converged_state` 只处理 `offline` / `thinking` / `waiting_approval` | `success` 在此**再次被丢弃** |
| 完成边沿 | `_on_agent_state` 的完成确认只有 `busy→attention/error` 与 `busy→idle` | 即使前两关通了也仍缺边沿 |

所以修法必须补**两侧**：收敛器出口把 `success` 送进呈现管线（与既有 `thinking`
同款通路），呈现管线再把 `busy → success` 视为完成边沿。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 收敛器出口 `success` | `_on_dsh_converged_state` 新增 `success` 分支 → `notify_dsh_state("success")` |
| 2 | 完成边沿 `busy → success` | `_on_agent_state` 新增该边沿，复用既有 800ms 稳定确认与 `_DONE_COOLDOWN_S` |

**红线 / 不变量**：

- 只新增边沿，**不改** `VALID_STATES`、不改 legacy 分派语义——`success` 仍不是
  legacy 词汇，避免其他 Agent（Claude/Cursor）的归一化口径被动变化；
- `success` **只**从收敛器出口进入管线，保持「一条状态一个来源」，不产生双通路；
- 复用既有 800ms 稳定确认与 5s 冷却，不引入新的定时器与抖动面；
- `busy → error` 仍然不播 `done` 音效、不报「已完成」（回归守卫见测试 4）。

## 二、修改文件说明

实现与测试共 2 个文件，`git diff --numstat` 为 `+248 / −0`（`pet/agent_link.py` +19、
`tests/test_agent_link.py` +229）；另有本报告与 `docs/INDEX.md` 的登记行，属纯文档新增。

> **本节为第一轮数字**（`c06ce4a`）。后续两轮自检修正见 §九 / §十——**实现与测试的
> 权威数字以 §10.4 为准**（第一轮 +19/+229 的行数与用例数均已被取代）。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/agent_link.py` | +19 / −0 | ① `_on_dsh_converged_state`（L2988-2994）新增 `to_state == "success"` 分支，调 `notify_dsh_state("success")` 后 return；② 同函数 docstring 增加 `success` 条目，写明「legacy 词汇表不认它、收敛器出口是唯一通路」；③ `_on_agent_state` 的完成确认边沿链（L3068-3074）在 `attention/error` 与 `idle/sleeping` 之间插入 `elif state == "success" and prev_raw in self._BUSY_STATES:` → `_schedule_done_check(agent_key)`。三处均为新增行，无既有行改动 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_agent_link.py` | +229 / −0 | 新增 `TestDshTurnEndDoneNotify`（6 条），见下表 |

| # | 用例 | 断言要点 |
|---|---|---|
| 1 | `test_working_to_success_fires_done_bubble_and_sound` | `working → success` 进入 `_done_pending`；走**真实定时器信号** `timer.timeout.emit()` 后弹完成气泡并请求 `builtin:agent-done` |
| 2 | `test_success_then_busy_within_confirm_window_cancels` | `success → working`（确认窗口内回忙）停掉定时器、不弹气泡、不播 done |
| 3 | `test_success_then_idle_does_not_double_notify` | DSH 偶发补发的 `success → idle` 不得再排一次（`idle` 边沿要求 prev_raw 忙碌），同一回合只有一条完成提醒 |
| 4 | `test_error_period_success_stays_on_look_at_it_path` | 回归守卫：`working → error` 不播 done、不报「已完成」；其后的 `error → success`（prev_raw 非忙碌）不得补干净完成提醒 |
| 5 | `test_converged_success_reaches_the_pipeline` | **钉住缺失的那一环**：`_on_dsh_converged_state("working","success","turn/end")` 必须让 `_last_raw == "success"` 且进入完成确认 |
| 6 | `test_real_bridge_record_sequence_ends_with_done` | **端到端**：issue 的真实记录形状（`turn/start` → `tool/call` → `turn/end`）经**真实** `DshStateConverger` 与真实出口后弹完成提醒；同时断言 `success not in VALID_STATES` 且 `turn/start`/`turn/end` 归一化均为空串（钉住「唯一通路」这一前提） |

### 未改动（故意）

| 文件 | 为什么不动 |
|---|---|
| `pet/dsh_state.py` | 收敛器已正确产出 `success`（`"turn/end": DshState.SUCCESS`），缺陷全在消费侧 |
| `pet/agent_link.py` 的 `VALID_STATES` / `normalize_event_state` | 把 `success` 加进 legacy 词汇表会同时改变 Claude/Cursor 的归一化口径，且与桥接「简单事件不带 state」的设计冲突；换条路修才是本 issue 的正确面 |
| `integrations/dsh-pet-bridge/index.js` | 桥接行为正确（简单事件不带 `state` 是**有意设计**，注释已写明），无需改动 |

## 三、实现要点

**为什么两处都要补。** `success` 的生命周期是「收敛器内部状态」：它既不是 legacy
词汇（`VALID_STATES` 无此项），也不由桥接的聚合基线写出。因此它是**收敛器独占**
的状态，必须以收敛器为出口——这与 `thinking` 的处理完全同构（`thinking` 同样是
legacy 基线不可见、由收敛器补进管线），所以直接复用 `notify_dsh_state` 这一既有
通路，不新造机制。

**为什么不用「把 success 加进 VALID_STATES」。** 那是改词汇表去迁就一个事件名：
`normalize_event_state` 是**所有** Agent 监视器（Claude/Cursor/Custom/DSH）共用的
归一化入口，加值会影响它们的判决口径；而且桥接明确不写 `state` 字段，legacy 分派
拿不到 `success` 这个值——加了也不生效。

**为什么边沿需要 `prev_raw in self._BUSY_STATES` 前置。** 与既有两条边沿一致：
只有「从忙碌态走出来的收尾」才算完成。该前置在真实链路上恒成立——每个回合的
`turn/start` 都会让收敛器产出 `thinking`，而 `thinking` 本来就走收敛器出口进管线
（见测试 6 记录的完整序列）。

**为什么复用 800ms 确认窗口。** `success` 与 `idle` 面临同样的抖动风险（如
SubagentStop 后主 Agent 继续干活）。复用既有 `_schedule_done_check` 即自动获得
「确认期内回忙则取消」与 `_DONE_COOLDOWN_S = 5s` 双重保护，零新增定时器。

**顺带收益（未额外改代码）**：`success` 现在会走完 `_fire_done`，其中的
`_cost_finish(agent_key)` 得以执行——此前 DSH 链路的消费统计同样卡在未结算状态。

## 四、性能分析

**方法（可复现）**：`QT_QPA_PLATFORM=offscreen`。新增的两条守卫表达式用 `timeit` 直测
（各 2×10⁶ 次）：

```python
timeit('state == "success" and prev in BUSY',
       globals={"state": "idle", "prev": "working", "BUSY": ("working", "thinking")},
       number=2_000_000)
timeit('to_state == "success"', globals={"to_state": "working"}, number=2_000_000)
```

端到端计时真实方法调用（各 5×10⁴ 次）：建一个 `AgentLinkManager(Win(), Config(base=tmp)，`
`min_interval=0.0)`，先 `_on_agent_state("dsh", "working")` 置入忙碌态，再分别计时
`_on_agent_state("dsh", "idle")`（稳态 idle 路径）与
`_on_dsh_converged_state("working", "working", "tool/call")`（收敛器非 success 出口）；
`Win` 为 no-op 窗口桩（`isVisible` 恒真、`show_bubble` 丢弃、`request_link_*` 空实现）。
同一段探针分别以 `PYTHONPATH` 指向 `ee699c7` 原始树与本次分支树运行，各跑 3 遍取中位。
环境：Windows 11 / Python 3.12.14 / PySide6 6.11.2。

| 指标 | BEFORE (`ee699c7`) | AFTER (本分支) | 归属 |
|---|---|---|---|
| 守卫 `state == "success" and prev in BUSY` | — | **22.1 ns/次** | 新增（`_on_agent_state`） |
| 守卫 `to_state == "success"` | — | **17.5 ns/次** | 新增（收敛器出口） |
| 端到端 `_on_agent_state("dsh", "idle")` | 1.166 / 1.169 / 1.187 µs（中位 **1.169**） | 1.163 / 1.189 / 1.237 µs（中位 **1.189**） | 稳态热路径 |
| 端到端 `_on_dsh_converged_state(非 success 出口)` | 0.958 / 0.959 / 0.962 µs（中位 **0.959**） | 0.974 / 0.985 / 0.988 µs（中位 **0.985**） | 收敛器出口 |

**结论**（逐条）：

1. **稳态开销**：两条热路径各多**一次字符串比较**。端到端中位差 +20 ns（+1.7%）与
   +26 ns（+2.7%），与守卫单次实测（22.1 / 17.5 ns）同量级；三次复跑的组内极差
   （BEFORE 21 ns、AFTER 74 ns）说明该差值已在噪声量级内。忙碌态
   （`working`/`thinking`）走第一条 `if` 即命中，**不进入** `elif` 链，开销为 0。
2. **新增路径成本与触发频率**：`success` 分支每个 `turn/end` 命中一次。DSH 每回合的
   收敛状态变更是个位数（`turn/start` → `tool/call`/`step/start`… → `turn/end`），
   故每回合新增总成本 < 0.2 µs；`_schedule_done_check` 复用的是**既有**路径
   （原本 `busy→idle` 就在调），不是新增链路。
3. **系统调用 / 网络 / 磁盘 / 线程**：**均无新增**。改动只加分支判断；定时器是既有
   `QTimer` 机制，不新建线程、不新增 I/O。相反，`success` 现在会走
   `_cost_finish`，可能**减少**一次本应发生却被跳过的余额查询收尾。
4. **内存与缓存**：**无增长**。未新增任何容器或缓存；`_done_pending` / `_done_cooldown`
   是既有结构，仅多一个键的常规生命周期。

## 五、实机运行记录

**诚实前提**：本机 `%APPDATA%\dsh-pet-bridge` **不存在**——即 DSH 桌宠联动从未在这台
机器上启用过（验证命令：`Test-Path (Join-Path $env:APPDATA "dsh-pet-bridge")` → `False`）。
因此**真实 DSH 会话的「真机端到端」（开启联动 → 跑一个回合 → 看气泡与音效）本次无法
提供**，不在这里用沉默代替结论。

能提供的实机证据是「真实源码路径 + 真实记录形状 + 真实收敛器 + 真实出口」的链路复现：

1. **根因现场复现（本机真实解释器，非 mock）**——归一化实测：

   ```
   VALID_STATES = ['attention', 'error', 'idle', 'sleeping', 'thinking', 'working']
   'success' in VALID_STATES                      -> False
   normalize_event_state('turn/end', '')          -> ''
   normalize_event_state('AgentStatus','success') -> ''
   normalize_event_state('turn/end', 'success')   -> ''
   ```

   这正是「报告者那份最小 diff 不生效」的机器证据：`success` 无法从 legacy 进入管线。

2. **桥接侧设计事实（源码原文，`integrations/dsh-pet-bridge/index.js:432-437`）**：

   > Records carry only an event field and no state field, so the legacy AgentStatus
   > working/idle baseline is untouched and the legacy DshMonitor (which ignores
   > unknown event types) keeps working.

   说明「简单事件不进 legacy 词汇表」是**有意设计**，收敛器出口是唯一通路。

3. **真实记录序列的端到端**：`tests/test_agent_link.py::TestDshTurnEndDoneNotify::test_real_bridge_record_sequence_ends_with_done`
   用 issue #234 证据链里的真实记录形状（`turn/start` / `tool/call` / `turn/end`，
   含 `sessionId` / `agentName`）驱动**真实** `DshStateConverger`，经真实
   `_on_dsh_converged_state` 出口，断言完成气泡文本（`已完成本轮任务`）与
   `builtin:agent-done` 音效请求都已产生。修复前该用例红，修复后绿。

4. **用户可见行为的确认**：**未获真人确认**——本机无联动环境，无法请真人复验。
   请有 DSH + 桌宠联动环境的维护者按 issue #234 的复现步骤验收（开联动 → 跑完一个
   回合 → 应出现「干完活啦」类气泡 + done 音效）。

5. **无法自动验证的能力（为什么不能自动）**：真实 DSH 会话的 `turn/end` 依赖运行中的
   DSH 宿主进程；本机 DSH 桌面端正在运行（Web GUI `127.0.0.1:19387`）但**未安装桥接
   插件**（桥目录不存在），且安装桥接需要改动宿主 profile 的插件树——属于用户环境
   改动，不在本次只读验证范围内。故以「真实记录形状 + 真实收敛器」替代，并如实标注
   这一层差距。

6. **边界与失败路径的实机观察**：`error` 周期不播 done 音效、不报「已完成」
   （测试 4，修复前后均为绿——它是既有正确行为的守卫，不是本次新增的红→绿）；
   确认窗口内回忙会取消（测试 2，修复前红）。

## 六、测试与验证

> **第一轮门禁**（`c06ce4a`）。第二轮修正后的复跑见 §9.6。

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed!** |
| 聚焦（新回归类） | `pytest -q tests/test_agent_link.py::TestDshTurnEndDoneNotify` | **6 passed**（修复前 4 failed / 1 passed / 1 后加） |
| 联动族 | `pytest -q tests/test_agent_link.py tests/test_agent_link_subtraction.py tests/test_agent_link_threads.py tests/test_agent_link_dep_specs.py tests/test_agent_event_protocol.py tests/test_dsh_state.py` | **295 passed, 1 skipped**（×3 复跑，12.22–18.84s） |
| 受影响时序族满载 3 遍 | 同上 ×3 | 三遍同口径全绿 |
| 全量 | `python -m pytest -q` | **4312 passed, 11 skipped, 0 failed**（511.71s） |
| 断言有效性 | 见下 | 已验 |

**断言有效性（红→绿对照）**：修复前 `TestDshTurnEndDoneNotify` 的 4 条新契约用例全红，
失败点分别落在「`'dsh' in {}`（`_done_pending` 无此键）」与「`_last_raw` 停在
`working` 而非 `success`」——即**同时**暴露了两处缺失，证明断言能区分新旧实现。

**缺陷注入 / 消融实验（证明「只改一行」不够）**：把实现临时削成 **issue #234 正文建议
的那份最小 diff**——即保留 `_on_agent_state` 的 `busy → success` 边沿、**只移除**收敛器
出口的 `success` 分支——重跑本类：

| 用例 | 消融后（只有报告者的 diff） | 完整修复 |
|---|---|---|
| 1 `working_to_success_fires_done_bubble_and_sound` | passed | passed |
| 2 `success_then_busy_within_confirm_window_cancels` | passed | passed |
| 3 `success_then_idle_does_not_double_notify` | passed | passed |
| 4 `error_period_success_stays_on_look_at_it_path` | passed | passed |
| 5 `converged_success_reaches_the_pipeline` | **FAILED** — `assert 'working' == 'success'` | passed |
| 6 `real_bridge_record_sequence_ends_with_done` | **FAILED** — `桥接记录序列未推进到 success：{'dsh': 'thinking'}` | passed |

即 `2 failed, 4 passed`：**用例 5/6 正是用来否证那份最小 diff 的防线**——若只按 issue
正文的建议改一行，本 PR 的回归会当场变红，而用户侧行为不会有任何变化。

**已知的既有噪声（非本次引入，已做基线对照）**：联动族在纯净 `ee699c7` 树上同样出现
2 条 `PytestUnhandledThreadExceptionWarning`（`Signal source has been deleted`，来自
`BaseAgentMonitor` 销毁与 worker 探活的收尾竞态）。已用同一份 venv 在未改动的
`ee699c7` 检出上复跑对应用例，**同样报这 2 条**，确认为既有问题，未在本次范围内处理。

## 七、已知限制与后续

1. **真实 DSH 端到端未验收**（见 §五.4/5）：需要一台启用了 DSH 联动的机器跑一遍
   「开联动 → 跑完一个回合」。本 PR 只保证到「真实记录形状 → 真实收敛器 → 真实出口」
   这一层。
2. **`prev_raw in self._BUSY_STATES` 前置**：完成提醒要求呈现管线此前见过该 Agent 的
   忙碌态。真实链路由每回合的 `turn/start → thinking`（已被转发）保证；但若桌宠在一次
   回合**中途**才启用联动，则该回合的 `turn/end` 不会补一次完成提醒——这是刻意的保守
   取舍（与既有两条边沿一致），不视为缺陷。
3. **同族相邻缺陷（本次不修，另案）**：`_on_dsh_converged_state` 对
   `to_state == "offline"` 只做 `dismiss_all_interactions()` 后 return，**不释放忙碌
   态**。探针实测（同一 no-op 窗口桩 + `min_interval=0.0`）：

   ```python
   mgr.monitors["dsh"]._running = True
   mgr.notify_dsh_state("working")
   # after working : _last_raw == "working" | any_busy() is True
   mgr._on_dsh_converged_state("working", "offline", "")
   # after offline : _last_raw == "working" | any_busy() is True   ← 未释放
   #                 _done_pending 无 "dsh"；dsh_state_changed 已 emit ('working','offline')
   ```

   即 DSH 在忙碌中退出/离线后，`_last_raw` 永久停在 `working` → `any_busy()` 恒真
   （忙碌动画与高帧率档位不释放）。与 #234 属于同一族（收敛器独占的终态未进呈现
   管线），但症状不同、改动面不同，按「一个 PR 只做一件事」另案处理，不在本 PR 夹带。
4. **`_run_pnpm()` 的编码问题**（issue #234 末尾的「可选独立项」）：Windows 中文环境
   下 `subprocess.run(..., capture_output=True, text=True)` 会因 pnpm 输出的 `✓` 触发
   `UnicodeDecodeError`、返回空 stdout/stderr，导致 `pnpm add 失败` 后面没有原因。
   同样与完成提醒无关，**未**包含在本 PR 内，建议单独立项（修法：
   `encoding="utf-8", errors="replace"`）。

## 八、风险与回滚

- **影响面**：仅 DSH 联动链路的状态呈现。其它 Agent（Claude Code / Cursor / Custom /
  OpenCode）不受影响——它们不产出 `success`，新边沿对它们是死分支。
- **开关**：无新增配置键，无开关。行为变化仅在 `agent_link.dsh` 启用时可见。
- **配置迁移**：**无**。不新增/不删除任何持久化键，不涉及配置文件读写。
- **回滚**：`git revert` 即可，无残留状态——不新增落盘数据、不新增缓存、不新增定时器；
  回滚后行为退回「完成提醒只在撞上 `AgentStatus idle` 时偶发」的原状。
- **风险等级**：低。两处新增分支均为既有权重的复用；最大的行为变化是 DSH 用户会开始
  收到本该收到的完成提醒，这正是 issue 的诉求。

---

## 九、第二轮自检修正（2026-10-08）：硬失败回合被误报为「成功完成」

> 本章**追加**于第一轮交付之后，保留新旧对照（模板要求：后续轮次不重写覆盖）。
> 上文 §一–§八 描述的是第一轮（`c06ce4a`）的状态；涉及行数/门禁数字处以本章为准。

### 9.1 触发

PR 提交一天后维护者无活动：`upstream/main` 仍停在 `ee699c7`（本分支 0 behind / 1 ahead，
无需 rebase），PR 上 0 评论 0 review；CI 两次 run 均为 `action_required`（首次贡献者的
workflow 需维护者批准才运行，即 `mergeable_state=unstable` 是「检查根本没跑」而非
「跑了没过」）。**既然 CI 这次兜不住，就本地加码复检**，并在维护者静止期间主动再过一遍
改动边界。复检由一名独立审查者（对抗性、不共享作者假设）与作者各查一遍。

### 9.2 发现（作者自查命中，第一轮引入的**误报**）

第一轮的 `busy → success` 边沿把**硬失败**的回合也当成成功收尾：

| 环节 | 事实（源码） |
|---|---|
| 桥接 | `writeStateEvent(type, step, sessionId, agentName)` 只写 `{event, step, sessionId, agentName}`——**不带 `reason` / `kind`**（`integrations/dsh-pet-bridge/index.js:477-483`） |
| 桥接 | `turn/end` 的 `reason.kind === "error"` 时，桥**另写一条 `execution/failed`**（同文件 1008），随后才写 `turn/end`（同文件 1024） |
| 收敛器 | `execution/failed` **不是**收敛器状态（不在 `_EVENT_TO_STATE`），而 `turn/end` 一律收敛为 `DshState.SUCCESS`（`pet/dsh_state.py:99`）→ 失败回合在收敛器里就是 `success` |
| 管理器 | `_on_execution_failed`（`pet/agent_link.py:4390`）只弹失败提醒，**不设** `_saw_error` / `_saw_alert`，**不撤**在飞的完成确认 |

后果：一次硬失败（`tool_failed` / `model_retry_exhausted` / 被模型访问提醒抑制的限流失败）
会**同时**给出「本轮运行失败」提醒与 800ms 后的「干完活啦，去看看成果吧～」气泡，并播
`builtin:agent-done` 音效。第一轮之前该路径是静默的（无完成提醒），所以这是**第一轮引入的
回归**，不是既有缺陷。

### 9.3 复现（红→绿）

新增 3 条用例，修复前实测（`pytest -q tests/test_agent_link.py::TestDshTurnEndDoneNotify`）：

```
FAILED ...::test_hard_failure_does_not_report_success
FAILED ...::test_failure_signal_after_success_cancels_pending_done
FAILED ...::test_failure_marker_clears_on_next_busy_cycle
3 failed, 6 passed in 0.96s
```

| # | 用例 | 钉住的契约 |
|---|---|---|
| 7 | `test_hard_failure_does_not_report_success` | `execution/failed` 之后 `success` 不得排完成确认、不得弹完成气泡、不得播 done 音效 |
| 8 | `test_failure_signal_after_success_cancels_pending_done` | **到达顺序无关**：`success` 先到（确认已排）时，随后的失败记录必须撤掉在飞的确认 |
| 9 | `test_failure_marker_clears_on_next_busy_cycle` | 标记只约束本轮：失败后重新开始干活，下一轮正常收尾仍必须能提醒（不能一次失败就永久静音） |

### 9.4 修改文件说明（第二轮增量）

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/agent_link.py` | +27 / −1 | ① `__init__` 新增 `self._saw_failure: set[str]`（与 `_saw_alert`/`_saw_error` 同族、同生命周期）；② `_on_agent_state` 忙碌分支新增 `self._saw_failure.discard(agent_key)`；③ `success` 边沿改为 `if agent_key not in self._saw_failure: self._schedule_done_check(...)`；④ `_fire_done` 在忙碌早退之后新增失败早退（`self._cost.abort` + return，对齐冷却早退先例）；⑤ `_on_execution_failed` 开头（**在可见性/抑制判断之前**）`self._saw_failure.add(agent_key)` + `self._cancel_done_check(agent_key)` |
| `tests/test_agent_link.py` | +77 / −0 | 新增用例 7/8/9 |

累计（相对 `ee699c7`）：4 文件 **+621 / −0**（实现 +45、测试 +306、报告 +269、INDEX +1）。

### 9.5 实现要点：为什么是三处，而不是一处

1. **标记点放在可见性/抑制判断之前**：失败提醒本身可能被吞（窗口隐藏时直接 return；
   限流失败被 `active_model_access` 抑制）——但「本轮不是成功完成」这个判断与提醒是否
   发出无关，所以标记必须在所有早退之前。
2. **撤在飞确认 + 调度侧抑制**：桥先写 `execution/failed` 再写 `turn/end`，但两者经
   **不同信号**投递到主线程，落点顺序不作为前提；`_cancel_done_check` 让「失败晚于
   success 到达」也成立（用例 8）。
3. **消费侧再守一道**：`_fire_done` 是唯一的呈现出口，在这里兜底可保证任何绕过调度侧的
   调用路径都不会漏出「成功」。此处**必须**一并 `_cost.abort`——否则该 agent 会永久滞留
   消费统计的 `_busy`，下一轮 `begin()` 被误判成「有别的会话在跑」（这正是仓库此前为
   「完成被冷却掐掉」修过的同一类 bug，见 `test_done_swallowed_by_cooldown_releases_cost_tracking`）。
4. **不选的方案**：把 `reason.kind` 透传到桌宠（改桥接 `writeStateEvent` + 收敛器把
   `turn/end{error}` 映射为 ERROR）才是「最正确」的修法，但要跨 JS 桥接 + 纯逻辑收敛器 +
   管理器三层、且需用户重装桥接插件，远超本 issue 的范围；本轮选择在管理器内收口，
   并把该方向登记为后续（§9.7）。

### 9.6 测试与验证（第二轮复跑）

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed!** |
| 聚焦（新回归类） | `pytest -q tests/test_agent_link.py::TestDshTurnEndDoneNotify` | **9 passed**（修复前 3 failed / 6 passed） |
| 整个联动测试文件 | `pytest -q tests/test_agent_link.py` | **190 passed, 1 skipped** |
| 联动族 | 6 个联动测试文件 | **298 passed, 1 skipped**（×3 复跑，10.27–10.35s） |
| 交付报告纪律 | `pytest -q tests/test_pr_report_discipline.py` | **73 passed** |
| 全量 | `python -m pytest -q` | 见 §9.8 登记 |

### 9.7 残留限制（第二轮新增登记）

- **`reason.kind` 仍在桥接侧被丢弃**：`turn/end` 记录不带 `reason`，所以收敛器无法区分
  `completed` / `error` / `aborted`。本轮用「同轮的 `execution/failed`」作为失败的代理信号，
  因此**只覆盖桥会写 `execution/failed` 的失败**（`kind === "error"`）。
  `kind === "aborted"`（用户主动中止）仍会被当成成功收尾并提示「干完活啦」——观感上尚可
  接受，但严格来说也不该暗示成功。根治需按 §9.5.4 透传 `reason.kind`，另案。
- **审批/问题锁存期间到达的 `turn/end` 会被收敛器整条忽略**（`pet/dsh_state.py` 的
  `handle_record`：锁存中忽略一切非阻塞事件），此时不产生 `success`、也就不触发完成提醒。
  这是收敛器的既有设计（与本次改动无关），且真实顺序通常为 `approval/decided` →
  `turn/end`，故未动；登记为后续排查项。
- 第一轮的其余限制（真实 DSH 端到端未验收、`prev_raw ∈ BUSY` 前置、`working → offline`
  后 `any_busy()` 恒真、`_run_pnpm` 编码）见 §七，本章不改变其结论。

### 9.8 第二轮结论

第一轮的**主修方向成立**（两侧补齐确实让 DSH 完成提醒恢复），但那条 `busy → success` 边沿
在「含硬失败」的回合上过宽，属第一轮引入的误报；本轮以三处收口修正，并用「红→绿」与
「到达顺序无关」两组用例钉死。教训与 §七/#224 一致：**新增一条状态边沿时，必须把该状态
在真实链路里的所有来源（含失败/中止来源）都走一遍**，否则很容易把「信号缺失」换成
「信号误报」。

> **⚠ 本章的 `_saw_failure` 独立容器 + `_cancel_done_check` 设计已被 §十 取代**
> （第三轮自检发现它自身引入跨会话误杀，且根因另有一处：收敛器 `error` 同样没进管线）。
> 本章保留为历史对照，实现以 §十 为准。

---

## 十、第三轮自检修正（2026-10-08）：独立审查 + 作者复验，根因收敛为「收敛器独占状态只补了一半」

> 本章**追加**于 §九 之后，同样保留新旧对照。实现与门禁数字以本章为准。

### 10.1 触发

PR 开单一天后维护者仍无活动（`upstream/main` 仍是 `ee699c7`；CI 两次 run 均为
`action_required`，即首次贡献者的 workflow 等维护者批准，**不是**跑失败）。既然 CI 兜不住，
除了作者自查，另派一名**独立对抗性审查者**（不共享作者假设，被要求"只报问题、必须给可运行
复现"）对冻结副本 `git archive HEAD` 复核。审查报 8 条，作者**逐条自行复现后再采纳**。

### 10.2 发现（4 条经作者独立复现确认；根因是同一条）

| 编号 | 问题 | 归属 | 作者复验输出 |
|---|---|---|---|
| **F7** | 收敛器 `error` 被 `_on_dsh_converged_state` 丢弃 → DSH 的出错回合在呈现管线里**完全不可见**：`_saw_error` / `_saw_alert` 永不置位（done 音效的「本轮出过错就不播」闸门失效），`_on_agent_state` 的 `error` 分支对 DSH 是**死代码** | 既有缺陷，但被本 PR 放大 | 收敛器序列 `working → error`，而 `_last_raw=thinking`、`_saw_error=set()` |
| **F1** | `turn/end{kind:"error"}` 但桥不写 `execution/failed` 的回合（`decideTurnEndFailure` 只在「重试≥阈值 **或** 有工具失败且无成功工具」时才写，`index.js:209-215`）仍被报成功 | 第一轮引入 | 弹「DSH 已完成本轮任务。」 |
| **F5** | 中途挂载（桌宠/联动在回合中才启动、或新 `dsh-{pid}.jsonl` 首次被发现——回填防护跳过 `turn/start`）时 `prev_raw` 为 `None`，`success` 边沿成**死路** | 第一轮引入 | 首个状态即 `success` → `_done_pending=False`（静默） |
| **F3** | 第二轮修复**自身**引入：`_done_pending` 按 agent 键，而 `_on_execution_failed` 里 `_cancel_done_check(agent_key)` 会连**别的并发会话**已排的合法完成一起掐掉（气泡/音效/消费结算全丢）；DSH 明确支持多会话并发（`index.js:30-33`） | 第二轮引入 | 会话 B 收尾后 `pending=True`，A 报失败后 `pending=False` |

未采纳为本次修改范围的（如实登记在 §10.6）：F2 `aborted` 回合、F4 审批/问题锁存吞 `turn/end`、
F6 done 音效早于冷却门禁、F8 审批回合文案与音效不一致。

**根因收敛为一条**：本 PR 的主题是「收敛器**独占**的状态没能进入呈现管线」——而 `success`
和 `error` **都是**这种状态，第一轮只补了 `success`。只补一半，既漏掉 error 回合的全部反馈，
又让新加的边沿把那些回合误报成成功。

### 10.3 修法（并**撤销** §九 的两处设计）

| # | 改动 | 理由 |
|---|---|---|
| 1 | `_on_dsh_converged_state`：`if to_state in ("success", "error")` 一并转发（原为只转发 `success`） | 两者同为收敛器独占终态；补上 error 后 `_saw_error`/`_saw_alert` 对 DSH 恢复意义，done 音效闸门与「自己看一眼」文案都回来了（同时修掉 F7 与 F1 的主子集） |
| 2 | **撤销** `_saw_failure` 独立容器与 `_fire_done` 里的失败早退（§9.4 的第 ①③④ 项） | 同一语义可由既有 `_saw_alert` / `_saw_error` 表达（`_fire_done` 本就用它们决定音效与文案），**零新机制**；独立容器是与既有簿记重复的第二套状态 |
| 3 | **撤销** `_on_execution_failed` 里的 `_cancel_done_check(agent_key)` | 它带来的跨会话误杀（F3）比它挡住的竞态更糟；且桥在**同一个 handler 内先写 `execution/failed`、再写 `turn/end`**（`index.js:1008-1025`），标记本就在 success 边沿之前落下，不需要撤 |
| 4 | `success` 边沿前置放宽为 `prev_raw in _BUSY_STATES or prev_raw is None` | `prev_raw is None` = 该 Agent 从未有过任何状态，正是中途挂载的形状；失败/中止回合的 `prev_raw` 是已见过的终态，语义不变（修 F5） |

`_on_execution_failed` 里的记账仍放在**所有早退之前**（隐藏窗口 / 被模型访问提醒抑制都会
提前 return）——否则「提醒没发出」会被误读成「没失败」。

### 10.4 修改文件说明（第三轮增量）

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/agent_link.py` | +40 / −12 | ① 收敛器出口转发 `error`（+ docstring 合并成 success/error 一条）；② 删除 `_saw_failure` 容器与其在忙碌分支的清理；③ `success` 边沿前置放宽并去掉容器判断；④ 删除 `_fire_done` 的失败早退；⑤ `_on_execution_failed` 改为复用 `_saw_alert`/`_saw_error` 且不再 cancel |
| `tests/test_agent_link.py` | +195 / −18 | 用例 4 改为走**真实收敛器链路**（原为不可达路径）；7–10 按新机制重写；新增用例 11（中途挂载） |

累计（相对 `ee699c7`，以本章为准）：实现 `pet/agent_link.py` **+47 行**、测试
`tests/test_agent_link.py` **+406 行**（11 条回归），报告与 `docs/INDEX.md` 登记行为纯文档。

### 10.5 复验证据（作者探针，只读，脚本未入库）

修复后对**原始指控场景**逐条复跑（真实 `DshStateConverger` + 真实出口）：

```
F7：收敛器序列 [( '', 'thinking'), ('thinking','working'), ('working','error')]
    _last_raw=error  _saw_error={'dsh'}                              → 已修 ✅
F1：序列含 ('working','error') → ('error','success')
    气泡 ['DSH 正在思考。', 'DSH 已停止，请确认当前结果。']            → 不再报「已完成」✅
F5：挂载时 _last_raw=None，首个状态 ('','success') → _done_pending=True → 已修 ✅
F3：B 收尾后 pending=True；A 报 execution/failed 后 pending=True       → 已修 ✅
```

同时按仓库"断言有效性"要求做了**反向验证**：把用例 4 改回直接调
`_on_agent_state("dsh","error")`（不可达路径）时它照样绿——这正是原用例无效的证明；
改成走真实收敛器链路后，若把 `error` 的转发去掉则立刻红。

### 10.6 测试与验证（第三轮复跑）

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed!** |
| 聚焦（新回归类） | `pytest -q tests/test_agent_link.py::TestDshTurnEndDoneNotify` | **11 passed** |
| 整个联动测试文件 | `pytest -q tests/test_agent_link.py` | **192 passed, 1 skipped** |
| 联动族 | 6 个联动测试文件 | **300 passed, 1 skipped**（×3，10.29–10.40s） |
| 交付报告纪律 | `pytest -q tests/test_pr_report_discipline.py` | **73 passed** |
| 全量 | `python -m pytest -q` | **4320 passed, 11 skipped, 0 failed**（501.46s） |

### 10.7 残留限制（第三轮更新）

- **F2 `aborted`（用户主动中止）回合仍会被当成成功收尾**：桥故意不为 aborted 写
  `execution/failed`（`index.js:206-212`），收敛器也拿不到 `reason`。根治需桥
  `writeStateEvent` 透传 `turn/end.reason.kind` + 收敛器按 kind 分流（跨 JS 桥接 / 纯逻辑
  收敛器 / 管理器三层，另案）。
- **`kind==="error"` 但既无收敛器 error 事件、桥又不写 `execution/failed` 的回合仍报成功**：
  桥的判定是「重试≥阈值 **或** 有工具失败且无成功工具」，所以「有成功工具 + 有失败工具 +
  `kind=error`」这类回合被桥自己判为**非硬失败**（`hadSuccess` 使 `toolFailed=false`）。
  本 PR 与桥保持同一口径：跟随桥的硬失败判定；要更严格同样需要 §上一条的 `reason` 透传。
  （注：带重试的 `kind=error` 回合会产出 `llm/retry → error`，已由第 1 条修法覆盖。）
- **F4 审批/问题锁存期间到达的 `turn/end` 被收敛器整条忽略**（`pet/dsh_state.py::handle_record`
  的既有设计）→ 该回合无完成提醒；且锁存超时只回到 `WORKING`，而该状态同样不经呈现管线。
  与本次改动无关，登记为后续排查项。
- **F6 done 音效在冷却门禁之前播放**（`_fire_done` 中 `_emit_sound` 早于 `_DONE_COOLDOWN_S`
  判断）→ 5s 内第二次完成会出现"有声音、无气泡、且 `_cost.abort` 丢结算"。既有代码，非本
  次引入，未改。
- 第一轮的其余限制（真实 DSH 端到端未验收、`working → offline` 后 `any_busy()` 恒真、
  `_run_pnpm` 编码）见 §七。

### 10.8 第三轮结论

三轮下来这件事的教训是递进的，值得单独记一笔：

1. **不能按报告者指的那一行修**（§七 vs #224 的教训）——`success` 到不了那个函数；
2. **新增状态边沿必须走完该状态在真实链路里的全部来源**（§九）——否则把"信号缺失"换成
   "信号误报"；
3. **补一个状态之前先问"和它同类的状态还有哪些"**（§十）——`success` 与 `error` 同为
   收敛器独占终态，只补一个必然两头不讨好：漏掉的那类没有反馈，补上的那个还会把它们误报成
   成功。同类状态要一起补齐，机制要复用既有的（`_saw_alert`/`_saw_error`），不要另起一套。

第三条正是独立审查最有价值的贡献——它跳出了"我改的那一行对不对"，去问"这类状态的完整
集合是什么"。

