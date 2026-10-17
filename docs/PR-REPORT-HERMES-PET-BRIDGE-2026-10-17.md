# Hermes Agent Link bridge + Hermes API Server chat route（2026-10-17）

基线：`a8593a8`（v4.3.1 发布提交）。
范围：4 个实现文件（新增 3、测试 1 新增）+ 本报告 + `docs/INDEX.md` 一条登记。
关联：`docs/AGENT_LINK_PROTOCOL.md`、`integrations/dsh-pet-bridge/`（DSH 侧既有桥）。
状态：**新增能力，不改任何既有 DSH 联动逻辑、不改桌宠持久设置、不引入第三方依赖。**

## 一、核心特性

桌宠原有的自定义 Agent Link 已经能读统一 JSONL，但 Hermes Agent 没有对应的写入端，所以「让桌宠显示 Hermes 正在干活/审批等待」此前无从落地。本 PR 增加一个 Hermes 插件，把生命周期 hook 翻译成桌宠既有协议，并把「桌宠 Chat 走 Hermes API Server」这条接入路径写成可复现文档。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 状态桥接 | `pre_llm_call` / `pre_tool_call` / `post_tool_call` / `on_session_end` → `thinking` / `working` / `idle` / `attention` / `error`，只写状态与短工具名 |
| 2 | 审批提示 | `on_human_input_request` / `on_human_input_resolved`（仅 `kind="approval"`）→ 桌宠既有 `approval/request`、`approval/resolved`，只落不透明的 `requestId` / `sessionId` |
| 3 | Chat 走 Hermes API | 文档给出 API Server 接入参数、loopback 绑定、空工具集与 `direct_model_requests` 选型；客户端不改代码 |

**红线 / 不变量**：

- 不写 prompt、回复、命令、工具参数/结果、路径或凭据，只写状态机所需的最小字段；
- 写盘失败必须静默（不能把桌宠故障传染成 Agent 回合失败）；不发任何网络请求；
- 桌宠自身 Chat（`api_server`）不产生开始/结束气泡，避免覆盖自己的回复；
- 审批事件在两个方向上都必须保留。

## 二、修改文件说明

`git diff --stat`（新增文件按 `--no-index` 统计）：

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `integrations/hermes-pet-bridge/__init__.py` | +253 / -0 | 插件主体。六个 hook 的注册、状态聚合、按 turn 的准入、审批事件写出 |
| `integrations/hermes-pet-bridge/plugin.yaml` | +11 / -0 | 清单：`version: 1.2.1`、声明六个 `provides_hooks` |
| `integrations/hermes-pet-bridge/README.md` | +83 / -0 | 数据边界、安装步骤、Chat 走 API Server 的配置、已知限制 |
| `tests/test_hermes_pet_bridge.py` | +309 / -0 | 17 条回归（详见 §五） |
| `docs/PR-REPORT-HERMES-PET-BRIDGE-2026-10-17.md` | 新增 | 本报告 |
| `docs/INDEX.md` | +2 / -0 | 报告入口 |

未改动（刻意）：`pet/agent_link.py`（消费端零改动，只使用既有自定义 Agent 协议）、`pet/chat/*`（Chat 走标准 OpenAI 兼容接口，无需客户端改动）、`integrations/dsh-pet-bridge/*`（DSH 桥一字未动）。

### 实现要点

1. **聚合而非逐会话覆盖**：多会话并存时 `working > thinking > idle`，一个会话结束不会把另一个正在跑的会话显示成空闲。
2. **按 turn 准入**：Hermes 的后台记忆/技能整理 fork 复用父会话 ID、`_persist_disabled=True`，因此**不发** `pre_llm_call` / `on_session_end`，但**会发**工具 hook（`agent/background_review.py:985-1005`、`agent/turn_context.py:805`、`agent/turn_finalizer.py:795`）。桥接只接受「由 `pre_llm_call` 登记过的 turn」的工具事件，否则后台整理会在真实回合结束后把状态永久写回 `working`——用户侧表现就是再也不弹开始/完成提示。无 `turn_id` 的调用方保留旧行为。
3. **turn_id 做键而非 session_id**：上下文压缩会中途换 session ID；以稳定 `turn_id` 记账，`on_session_end` 清理全部别名，避免遗留忙态。
4. **工具失败不清空并发**：同一 turn 并行工具用 `tool_call_id` 集合跟踪，最后一个返回才退出 `working`；失败先发 `error`，其余在途调用仍在内存中保持忙碌。
5. **api_server 抑制会传递到子代理**：`platform == "api_server"` 或其 `parent_session_id` 命中时抑制祖先链，普通 CLI 子代理照常上报。
6. **写者身份**：每次进程加载生成 `inst` UUID 写入记录，便于支持分片写者的消费端区分（本 PR 不修改自定义消费端）。
7. **原子写**：`O_APPEND` 单次 `os.write`，聚合与写盘在同一把锁内，避免旧快照覆盖新状态。

## 三、性能分析

方法：`python <scratch>/dsh_bridge_review_probe.py`，Windows 本机、Python 3.11.15、真实磁盘写入（临时目录，不接生产事件流）。

| 指标 | 实测 | 归属 |
|---|---|---|
| 1000 轮 × 4 hook（共 4000 条记录） | 431,886 字节，平均 **0.198 ms/hook**（含 mkdir/open/append/close） | 新增路径 |
| api_server 抑制 10,000 对 pre/end | 平均 **1.906 µs/对**，文件大小不变 | 新增路径 |
| 结束后 `_SESSION_STATES` / `_ACTIVE_TOOLS` / 标记 | 均为 0 | 内存 |

结论：① 稳态开销只发生在真实 hook 上，每条约 0.2 ms，相对一次模型往返可忽略；② 抑制路径无系统调用、无写盘；③ 不新增线程、网络、定时器；④ 内存随并发会话/在途工具线性增长并随 `on_session_end` 清理。**未测与旧实现的同机对照，不声称更快。**

## 四、实机运行记录

- **真实 hook 契约核对**：逐个读 `hermes-agent/agent/turn_context.py`、`agent/inline_tool_executors.py`、`tools/human_input_hooks.py`、`agent/turn_finalizer.py` 的实际发射点，字段 `session_id` / `turn_id` / `tool_call_id` / `status` / `error_type` / `request_id` / `kind` / `parent_session_id` 均来自源码，不是按名字猜的。
- **真实事件文件**：本机 Hermes 运行后 `%APPDATA%\dsh-pet-standalone-webm-chat\agent-events\hermes.jsonl` 持续产生 `thinking` / `working` / `idle` / `error`，网关重启后 `inst` 由 `e74ecef0…` 变为 `410b792f…`，证明新进程确实加载了 1.2.1 而非仅磁盘替换。
- **后台整理回归的现场证据**：`~/.hermes/logs/agent.log` 记录两轮 `origin=background_review` 于 18:15 完成，同期事件流却再未回到 `idle`；这正是 §二.2 修正的现场依据。
- **桌宠侧**：管理员确认修复后提示恢复（重开联动/重启网关后），并明确接受每句 Chat 新建 API 会话的行为（不属于缺陷）。
- **无法自动验证**：本机工具运行在非交互 Windows 会话，无法读取用户桌面窗口截图；审批提示由管理员在实际操作中观察，未用危险命令人为触发。

## 五、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 聚焦 | `python -m pytest tests/test_hermes_pet_bridge.py -q` | **17 passed** |
| 静态 | `ruff check integrations/hermes-pet-bridge tests/test_hermes_pet_bridge.py` | All checks passed |
| 插件 | `hermes plugins validate <dir>` | manifest / loadable / hooks / security 全通过 |
| 报告纪律 | `python -m pytest tests/test_pr_report_discipline.py -q` | 通过 |

覆盖：最小字段与隐私（prompt/命令/结果不进文件）、失败静默、会话结束的 `completed/interrupted/failed` 三态、api_server 抑制及其子/孙代理、审批 request/resolved 配对且非审批 kind 忽略、并行工具、后台整理 fork 不入账、压缩换 session ID、无 turn_id 的 CLI shutdown 清理、多写者 `inst` 区分。

**未跑整仓全量**（本机缺少 OCR/TTS 等运行依赖，且改动仅新增目录）；未做 macOS/Linux 实机验证。

## 六、Chat 走 Hermes API Server（不涉及代码）

`README` 记录可直接复现的配置：

- Base URL `http://127.0.0.1:8642/v1`，路径 `/chat/completions`；
- 仅绑定 loopback、保留强 bearer key；
- 只要纯文本 Chat 时设 `platform_toolsets.api_server: []`；
- 需要在客户端直接选模型时启用 `platforms.api_server.direct_model_requests`，否则用虚拟模型 `hermes-agent`。

OAuth 凭据留在 Hermes，客户端只保存本地 API Server 的 bearer key。

## 七、已知限制

- 插件无心跳、无日志轮转、无进程死亡回收：强杀进程可能留下忙态，重启网关可清。
- 自定义 Agent 消费端目前不读取 `inst` 做写者分片；多进程（网关 + CLI）同时运行时仍可能互相覆盖状态。本 PR 只提供元数据，接入属消费端后续工作。
- 后台整理通过「未登记 turn 的工具事件」被排除；若未来 Hermes 让 fork 也发 `pre_llm_call`，需改用显式 origin 字段。
- 审批气泡的具体文案由桌宠既有模板决定，插件只传身份。

## 八、风险与回滚

纯新增目录与文档，不触碰既有代码路径；卸载即 `git revert` 或删除插件目录，无持久数据迁移、无配置键变更、无遗留状态。
