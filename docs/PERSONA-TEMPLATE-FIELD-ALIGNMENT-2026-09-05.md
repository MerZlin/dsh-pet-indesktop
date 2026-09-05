# 台词模板字段对齐审计（2026-09-05）

背景：上游（origin/main）大规模重构合并（`8fb74a5` 等）后，`pet/persona_template.py`
导出模板里宣称的字段/参数与运行时实际传给 `PhrasePicker` 的值严重脱节——模板告诉
用户/AI「可以写 {riskScore}、{arguments}」，运行时却从未注入，占位符原样露出。
本文是逐事件 key 的对齐审计结论，并记录本次修正。回归防线：
`tests/test_persona_template.py::test_template_parameters_match_runtime_call_sites`、
`tests/test_settings_and_resources.py::test_dialogue_key_params_match_runtime_call_sites`。

## 一、渲染链路与“参数”的两个层级

渲染入口都汇聚到 `PhrasePicker.custom/get(key, fallback, **values)`：

- `pet/agent_link.py` `AgentLinkManager._dialogue()`（约 2096 行）：
  `values = dict(self._dialogue_context) | 显式 kwargs`。
- `pet/app.py` `_persona_text()`（约 81 行）：余额气泡，只有调用点 kwargs，无上下文记录。
- `pet/window.py` `_expression_style_text()`（约 3755 行）：内置自言自语复用 `thinking` key，无参数。

`_dialogue_context` 由 `mon.raw_record`（`_remember_dialogue_record`，约 2086 行）更新，
是**最近一条**桥接 JSONL 记录（嵌套 `data` 已拍平）。因此可用参数分两级：

1. **保证参数（显式 kwargs）**：调用点直接传入，任何时候可用 → 模板 `variables` + `PARAMETERS`。
2. **上下文字段（raw_record）**：仅当文案与对应事件**同轮触发**时可靠
   （approval/question/rate_limit/llm_error/execution/failed/tool 类，监视器在
   `_poll` 同一次迭代里先发 `raw_record` 再发对应信号，队列顺序保证上下文就是本事件；
   状态机、本地检测器、桥接安装流程、余额查询触发的文案**不保证**）→ 模板 `upstream.fields`。

上游记录字段以 `docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md` 为准。

## 二、逐事件 key 审计表（调用点 → 保证参数）

| key | 调用点（pet/agent_link.py 除非注明） | 保证参数 | 可靠上下文字段 |
|---|---|---|---|
| start / thinking | `_maybe_notify_start` / `_thinking_text` | name | 无（状态机触发） |
| activity.read/search/edit/run/default | `_on_agent_activity`（显式传 values 字典） | name、tool、label、target、callId、step、ok（target 等取自按 agent 缓存的最近 tool/call 记录，有则注入） | 同左（已显式化） |
| agent.attention / agent.error | `_on_agent_state` | name | 无（状态机触发） |
| agent.missing | 1729 | name | 无 |
| bridge.install.pending/success | 1751/1808 | name | 无 |
| bridge.install.failed | 1756 | name, detail | 无 |
| bridge.uninstall.failed | 1828/1836 | name | 无 |
| dsh.writeback.failed | 2716/2754 | （无） | 不保证（POST 结果路径） |
| approval.command | 2219 | name, command（单行折叠+截断 160） | rpcId、approvalId、requestId、callId、toolName、sessionId、outcome |
| approval.tool | 2227/2232 | name, label | 同上 |
| approval.generic | 2234/2236 | name | 同上 |
| question.empty | 2314 | name | rpcId、callId、sessionId、questions |
| question.one | 2342 | name, body（含 header 前缀） | 同上 |
| question.many | 2317 | name, count | 同上 |
| watchdog.warning | 2974 | name, reasons（已格式化） | 不保证（本地检测器） |
| watchdog.intervention | 3110 | name, reasons | 不保证 |
| watchdog.unknown | 3101 | name | 不保证 |
| pattern.warning / pattern.control | 2958 | name, reasons | 不保证 |
| rate_limit.one / rate_limit.many | 3224 | count | errorCode、errorMessage、sessionId |
| llm_error.api | 3325 | （无） | errorCode、errorMessage、sessionId |
| done.success / done.attention | 2801/2799 | name | 无（状态机触发） |
| failure.retry / failure.tool / failure.generic | 3653-3657 | name | errorCode、errorMessage、errorText、retryExhausted、retries、source（execution/failed 同轮） |
| control.replan.pending / interrupt.pending | 3576/3603 | name | 不保证 |
| control.replan.success / interrupt.success / failed | 3549-3562 | name | 不保证（operation/ok/detail **未**传入） |
| stuck.reminder | 2899 | name | 不保证 |
| balance.loading | pet/app.py:474 | （无） | 无 |
| balance.result | pet/app.py:119 | text | 无 |

自言自语（window.py）复用 `thinking` key、无参数；`{name}` 不会代入。

## 三、本次发现并修正的错位

### persona_template.py（导出模板真相源）

- **删除 cordis 组**：`_on_cordis_request` 不走 `_dialogue`（自己拼文案），且
  `phrase_keys()` 无 cordis key——VARIABLES 的 pluginId/packageId/mode/purpose/
  requiresApproval、UPSTREAM_FIELDS["cordis"]、EVENT_FIELDS["cordis"] 全是死字段。
- **删除 watchdog/pattern 伪造字段**：risk/riskScore/riskReasons/targets/targetCount/
  generation_id/goal/verdict/reason/class/window/count 从未传入 `_dialogue`
  （它们只进了 alert 的 metadata，不是模板值）。
- **activity 组瘦身**：toolName/arguments/argsKey/command 均未传入；保留 name +
  tool/call 记录字段（tool/target/callId/ok/step）。
- **control 组瘦身**：operation/phase/detail/ok/requestId 未传入，只留 name。
- **VARIABLES 收敛为 8 个保证参数**：name/command/label/body/count/reasons/detail/text；
  其余（timeout/resultSummary/durationMs/decision/provider/label_upstream/
  consecutiveRetryCount/retry/ok 等从未可用）删除。
- **EVENT_SOURCES 对齐桥接契约**：tool/call、approval/request、question/requested、
  rate_limit/llm_error/execution/failed 等；状态机与本地检测来源标注为中文说明。
- **DISPLAY_HINTS 全部重写**：只引用对应 key 真实可用的占位符。
- **导出指南（EXPORT_GUIDE）更新**：明确「参数分两级：variables 保证可用；
  upstream 上下文字段仅事件同轮可靠」。

### persona_phrases.py

- 注册保留空 key `balance.loading`、`balance.result`（pet/app.py 真实渲染，
  此前既不能导出也不能在设置页编辑）；删除从未渲染的死 key `balance.query`。

### modern_settings_dialog.py

- 恢复**被上游合并覆盖**的 `_import_dialogue_template_json()` entries 合并逻辑
  （顶层 phrases 优先、缺失/全空时用 `entries[].phrases` 补齐）——上游重构后
  该修复丢失，导致“只改 entries 的模板导入不生效”回归。
- `DIALOGUE_KEY_PARAMS` 对齐调用点：question.empty 补 `name`、
  rate_limit.one 补 `count`、watchdog.unknown 补 `name`。

## 四、不变量与后续维护

- `PARAMETERS` 的键集合必须 == `phrase_keys()` == entries 的 key 集合（有测试）。
- 新增/修改 `_dialogue()` 调用点的显式 kwargs 时，必须同步更新
  `PARAMETERS`（及组字段、DISPLAY_HINTS），否则模板会对用户撒谎——回归测试
  只能守住“集合一致”，守不住“新增参数未登记”，需 code review 把关。
- 留空数组 = 沿用原模式台词；`_说明` 导入时忽略；导入未知键被忽略。
- 上下文字段是“最近一条记录”语义，同 Agent 并发多事件时仍以同轮触发为前提；
  不要在文档/模板里宣传跨轮可靠。

## 五、第二轮（同日）：target 显式接入表现层 + 全字段机械验证

上游反馈「target 字段没有传到气泡表现层」。审计确认：`_on_agent_activity` 之前只传
`name`，target 只存在于 raw_record（且依赖「恰好是最后一条记录」的隐式上下文）。修正：

- `AgentLinkManager` 新增按 agent 的最近工具记录缓存 `_last_tool_records`
  （`_remember_dialogue_record` 在收到带 tool 字段或 event=tool/call 的记录时更新）；
  `_on_agent_activity` 显式把 `name/tool/label` 加上记录里的 `target/callId/step/ok`
  （有则注入，缺失时占位符原样保留）传给 `_dialogue` —— activity 组字段从「上下文碰运气」
  升级为「显式保证」。
- 模板常量双向对齐：`PARAMETERS["activity.*"] = (name, tool, label, target, callId, step, ok)`；
  `pattern.warning` 补 `reasons`（运行时与 pattern.control 同点注入）；
  `UPSTREAM_FIELDS["base"]` 移除桥接从不写出的 `sessionName`（label/projectName/agentName
  由 writeRecord+session/meta 补充，有据可查）；EVENT_FIELDS 改为由 `BASE_FIELDS` 派生，
  不再逐行复制。
- `modern_settings_dialog.DIALOGUE_KEY_PARAMS` 改为 `dict(PARAMETERS)` 派生（单一真相源），
  `DIALOGUE_PARAMS` 补 tool/target/callId/step/ok 展示名。

### 全字段传输保证（测试改名）

`tests/test_persona_template.py::test_all_advertised_fields_reach_presentation_layer`
（原 test_template_parameters_match_runtime_call_sites 更名并升级）两层机械验证：

1. **AST 调用点审计**：解析 pet/agent_link.py 与 pet/app.py 全部 `_dialogue/_persona_text`
   调用点，断言 `PARAMETERS[key] == 调用点显式 kwargs 并集`（双向相等：多宣称=占位符
   永远原样露出的谎言；少宣称=已注入却不告知）。动态 key 调用点按 kwargs 签名归组
   （activity=values 展开、pattern={name,reasons}、rate_limit={count}）。
2. **桥接字段存在性**：UPSTREAM_FIELDS 每个字段必须在 `integrations/dsh-pet-bridge/index.js`
   中以词边界出现（桥确实写出），或属于 Pet 侧注入（agent_key）。

已做反向自证：向模板注入 riskScore（运行时不传）→ 测试红；向 UPSTREAM_FIELDS 注入
ghostField（桥不写）→ 测试红。

设置页 `DIALOGUE_KEY_PARAMS == dict(PARAMETERS)` 由
`test_settings_and_resources.py::test_dialogue_key_params_match_runtime_call_sites` 保证；
端到端渲染（target 真出现在气泡里）由
`test_agent_link.py::test_activity_bubble_receives_target_from_tool_record` 保证。
