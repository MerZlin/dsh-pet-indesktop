# PR 报告：Agent 联动接入 AstrBot（2026-11-06）

> **基线**：`34d810b`（chore(update): update.json 指向 v4.4.0）
> **分支**：`claude/kind-thompson-lvicmz`　**日期**：`2026-11-06`
> **范围**：20 个文件（实现 12：pet/ 9 + 新插件 3；测试 4；文档 3 + 工具 1）
> **关联**：[多 Agent 联动统一事件协议](AGENT_LINK_PROTOCOL.md) §3.1；先例
> [Hermes Agent Link 桥接](PR-REPORT-HERMES-PET-BRIDGE-2026-10-17.md)

## 一、核心特性

给「Agent 联动」加第五个内置通道 **AstrBot**（多平台 LLM 聊天机器人框架）。右键菜单
「Agent 联动 → AstrBot」勾选后：选 AstrBot 目录 → 同意框写明要写入的插件目录与事件文件
→ 桌宠把随包分发的 AstrBot 插件复制进 `<data>/plugins/astrbot_plugin_dsh_pet_bridge/`
→ 重启 AstrBot（或在 WebUI 插件页重载）后，AstrBot 每处理一条需要 LLM 的消息，桌宠就
播「写代码」联动动作、弹「AstrBot 正在思考」/ 工具过程气泡，回复完成后回待机并弹完成提醒。

| # | 能力 | 说明 |
|---|---|---|
| 1 | AstrBot 桥接插件 | `integrations/astrbot-pet-bridge/`：订阅 `on_llm_request` / `on_using_llm_tool` / `on_llm_tool_respond` / `on_llm_response` / `after_message_sent`，按在途回合折叠六态，追加写 JSONL；无第三方依赖、不联网、写失败静默 |
| 2 | 一键安装 / 卸载 | `AstrBotMonitor.install_bridge` 临时目录写好再整体替换；只覆盖/删除带 `.dsh-pet-bridge` 标记的目录，用户同名插件拒绝覆盖 |
| 3 | Docker 友好的事件位置 | 事件文件在 `<data>/plugin_data/astrbot_plugin_dsh_pet_bridge/astrbot.jsonl`：容器内外都落在挂载的 data 目录 |
| 4 | 目录记忆 | `agent_link.astrbot_root` 记住所选 data 目录；关闭联动保留，再开免选；卸载清理按它删插件 |
| 5 | 入口面 | 菜单、懒创建判据、功能模块配置键、卸载清理、台词按 Agent 分层（`AGENT_NAMES` 自动带出「AstrBot 专属文案」） |

**红线 / 不变量**：默认关闭；写外部目录前必须弹窗同意；读方不在 AstrBot 目录里创建任何
东西；事件只有 `ts/agent/inst/state/tool`，绝不写消息正文、提示词、会话/用户/群号、工具
参数与结果；既有四个 Agent 的行为与气泡文案不变。

## 二、修改文件说明

`git diff --numstat`（基线 `34d810b`，新增文件按 `wc -l`）：

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `integrations/astrbot-pet-bridge/main.py`（新增） | +288 | AstrBot 插件。`PetBridge` 纯逻辑（在途回合表、去重追加写、1MB 轮转、180s 静默回收、256 条上限）与 `DshPetBridge(Star)` 薄壳分开，便于脱离 AstrBot 单测；新钩子按 `getattr(filter, name)` 取，旧版 AstrBot 没有工具钩子也能加载 |
| `integrations/astrbot-pet-bridge/metadata.yaml`（新增） | +6 | AstrBot 插件元数据；`name` 即安装目录名 |
| `integrations/astrbot-pet-bridge/README.md`（新增） | +49 | 安装、事件位置、状态映射、数据边界 |
| `pet/agent_link/__init__.py` | +262 / −2 | 新增 `AstrBotMonitor`（`normalize_data_dir` 认根目录或 data 目录；`detect_data_dir` 探测 `$ASTRBOT_ROOT` 等四处；`events_file_for` 与插件同规则；`set_data_dir` 换读取目标；`install_bridge` / `uninstall_bridge`）。管理器：`AGENT_NAMES` 与 `monitors` 注册；`apply_config` 启动前按配置重定向读取目录；`set_enabled` 新增 AstrBot 开/关分支与 `_resolve_astrbot_data_dir`（记住的目录优先，否则弹目录选择，预填探测结果，非 AstrBot 目录给警告不进授权）；`enable_notice` 统一菜单开启气泡（AstrBot 提示需重载插件，其余 Agent 文案与旧版逐字相同） |
| `pet/config.py` | +8 / −1 | `agent_link.astrbot`（bool，默认 False）与 `agent_link.astrbot_root`（str，截断 500）；`astrbot` 进内置键表，自定义 Agent 不得占用 |
| `pet/context_menus/shared.py` | +2 / −1 | 菜单第五项「AstrBot」 |
| `pet/window.py` / `pet/sprite_menu_facade.py` | +4/−1、+4/−1 | 两种拓扑的菜单开关在开启后改用 `manager.enable_notice(key)`；否则 AstrBot 的「需重载插件」提示会被通用气泡盖掉 |
| `pet/window_optional_services.py` | +1 / −1 | 懒创建判据含 `astrbot` |
| `pet/features.py` | +4 / −2 | `agent_link` 模块描述/别名/配置键归属加 AstrBot |
| `pet/uninstall_cleanup.py` | +7 / −3 | 卸载清理第 4 步：无其他实例使用时按 `astrbot_root` 删插件（未记录目录 = 从未安装，视为完成） |
| `pet/persona_template.py` | +1 / −1 | 台词模板说明里的 Agent 键列表 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_astrbot_pet_bridge.py`（新增） | +279 | 写方契约：用最小 `astrbot.api` 桩按文档签名调用钩子；钩子登记、最小字段与隐私（PRIVATE 串不落盘）、旧版无工具钩子可加载、并发回合折叠、最终响应收尾、消息发出兜底、失败→error、静默回收与活动续期、回合表上限、轮转、写失败不抛、写方产物经桌宠六态归一 |
| `tests/test_agent_link_astrbot.py`（新增） | +400 | 配置默认/清洗/内置键保护；目录识别与探测；安装（标记、重装覆盖己方、拒绝覆盖他人、无效目录）；**用安装后的插件反推事件路径 = 读方路径**；卸载（幂等、只删己方事件文件）；监视器端到端读取；管理器开启（目录选择→同意框含具体路径→安装→落盘→监视器指向正确文件）、已记目录跳过选择、取消/拒绝零副作用、无效目录警告、关闭卸载、他实例在用保留、按配置启动；菜单、功能模块键、卸载清理两条 |
| `tests/test_agent_link.py` | +2 | 产品默认 `agent_link` 形状加两个新键 |
| `tests/test_uninstall_cleanup.py` | +3 / −1 | 卸载清理结果字典的精确断言加 `astrbot_bridge`（未记目录 → True） |

### 文档 / 工具

| 文件 | 增删 | 说明 |
|---|---|---|
| `docs/AGENT_LINK_PROTOCOL.md` | +18 / −6 | 架构图、§3 表加 AstrBot 行与 §3.1 要点、§4 内置键、§5 改动位置表补 3 处并修正已目录化的路径、§6 红线举例 |
| `docs/PR-REPORT-ASTRBOT-AGENT-LINK-2026-11-06.md`（新增） | 本文 | |
| `docs/INDEX.md` | +1 | PR 报告存档登记 |
| `tools/bench_astrbot_link.py`（新增） | +56 | §四 的可复现基准 |

### 未改动

- `scripts/build_onedir.ps1`、三个 workflow：打包已是 `--add-data "integrations;integrations"` 整目录收集，新插件目录自动进包，无需改 spec。
- `pet/modern_settings_dialog.py`：Agent 开关只在右键菜单（设置页没有逐 Agent 开关），`astrbot_root` 由开启流程的目录选择框写入、不进设置页，按 `SETTINGS-CHANGE-GATES.md` 准入条件不新增设置控件。
- `integrations/hermes-pet-bridge/`：Hermes 走自定义通道，不受影响。

## 三、实现要点

- **为什么事件文件放在 AstrBot data 目录**：AstrBot 官方部署首推 Docker（`-v ./data:/AstrBot/data`）。写到桌宠配置目录在容器里根本不可达；写到 `data/plugin_data/<插件名>/`（AstrBot 约定的插件数据目录）则宿主机与容器看到的是同一个文件。插件按 `data/plugins/<插件>/main.py` 的上两级推 data 目录（不 `resolve()`，与桌宠按「用户选的目录」计算保持一致），测试用**安装后的插件文件**反推路径证明两侧一致。
- **回合收尾以 `on_llm_response` 为准**（实机发现，见 §五）：AstrBot 4.29.4 的 `RespondStage` 在流式结果分支提前 `return`，`after_message_sent` 根本不触发；而 `on_llm_response` 由 `MainAgentHooks.on_agent_done` 发出，流式/非流式都会到。`after_message_sent` 只作兜底（第三方 Agent 执行器不发 `on_llm_response`；非流式 LLM 报错后发错误文案）。
- **唯一没有收尾钩子的路径**：流式 + LLM 请求失败（`ToolLoopAgentRunner` 转 ERROR 态直接 yield 错误文案，不调 `on_agent_done`，RespondStage 又走流式分支）。插件用 180s 静默回收兜底：每个钩子刷新回合的活动时刻，`loop.call_later` 排一次清扫；任一新请求进来也会顺手清扫。
- **并发折叠**：AstrBot 同时服务多个群/私聊。回合键 = `id(event)`（同一事件对象贯穿整条 pipeline，实测 req/tool/resp 三处 id 相同）；任一回合有在途工具 → `working`，否则有在途回合 → `thinking`，全部结束 → `idle`。表上限 256 条，满了淘汰最旧。
- **去重**：同态不重复落盘（协议 §2.5）；`error` 是一次性提醒，不去重。插件加载/卸载各写一条 `idle`，收口上次 AstrBot 异常退出留下的忙碌态。
- **安装原子性**：先写 `<目标>.dsh-pet-tmp` 再 `os.replace`，半途失败不会留下半个插件让 AstrBot 加载报错。安装是三个小文件的复制（实测 0.3 ms），直接在 UI 线程同步做，不像 DSH 那样需要后台线程跑 pnpm。
- **备选方案（未采用）**：① 走 `custom_agents` 自定义通道 + 手动装插件（Hermes 先例）——用户要手抄路径，且 Docker 下路径对不上；② 读 AstrBot WebUI/HTTP 接口——需要凭据、要联网形态，违反联动「只碰本地文件」红线。

## 四、性能分析

**方法（可复现）**：`QT_QPA_PLATFORM=offscreen python tools/bench_astrbot_link.py`
环境：Linux 6.18.44（云容器，4 vCPU），Python 3.13.18，PySide6 6.11.3，文件系统为容器本地盘。

| 指标 | 实测 | 归属 |
|---|---|---|
| 插件一个完整回合（thinking→working→thinking→idle，4 次 `open(O_APPEND)+write+close`），N=5000 | 中位 56.4 µs，p95 108.3 µs | 新增（AstrBot 进程内，每条 LLM 消息一次） |
| 插件去重命中（同态钩子，不落盘），N=5000 | 中位 1.35 µs | 新增 |
| 插件回合表满载（256 条）内存，tracemalloc | 72.4 KiB | 新增（上限） |
| 桌宠读方空闲轮询（文件存在无新字节），N=5000 | 中位 2.0 µs，p95 3.4 µs | 新增，每 1.5 s 一次（仅开启联动时） |
| 桌宠读方空闲轮询（文件不存在），N=5000 | 中位 2.0 µs | 同上 |
| 一键安装 / 卸载，N=50 | 中位 0.31 ms / 0.29 ms | 新增，用户点菜单时一次 |
| 端到端延迟（AstrBot 写出 → 桌宠收到信号），实机 §五 | 约 1.2 s（≤ 1.5 s 轮询周期） | 既有轮询模型 |

**结论**：① 未开启 AstrBot 联动时稳态开销为零——监视器对象随管理器构造创建（管理器本身仍按既有规则懒创建），不起线程、不轮询；仅当配置里已记有 `astrbot_root` 时构造期做 2~3 次一次性 `stat` 识别目录。开启后新增一条 1.5 s 周期的 daemon 轮询线程（与其它 Agent 同模型），每拍一次 `stat`，实测 2 µs。② AstrBot 侧每条 LLM 消息新增约 56 µs 的同步追加写（相对一次 LLM 请求的百毫秒~秒级耗时不在一个量级），触发频率 = AstrBot 收到的需 LLM 处理的消息数。③ 新系统调用：插件侧 `mkdir/stat/open/write/close`（轮转时 `rename`）；桌宠侧 `stat/open/read`；安装时一次目录复制。无网络、无子进程。④ 内存：插件回合表上限 72 KiB；桌宠侧复用既有有界摄入账本（512 条），无新增缓存。事件文件 1 MB 轮转、只留一代，磁盘上限约 2 MB。

## 五、实机运行记录

本机 = 本会话的 Linux 云容器（不是 CI），用 PyPI 上的**真实 AstrBot 4.29.4**（`pip install astrbot` 进独立 venv，`astrbot init` 建根目录）。LLM 用只监听 `127.0.0.1` 的假 OpenAI 兼容端点（首轮回一个 tool_call，次轮回文本，支持 SSE 流式），因为本环境没有可用的真模型密钥；除此之外 AstrBot、插件加载、WebChat 平台、Agent 执行器、工具调用都是真实代码路径。

**1. 真实钩子登记**（用真实 `astrbot.api` 导入插件）：

```
hooks available: ['on_llm_request', 'on_llm_response', 'after_message_sent', 'on_using_llm_tool', 'on_llm_tool_respond']
registered handlers: [('OnLLMRequestEvent', 'dsh_pet_on_llm_request'), ('OnUsingLLMToolEvent', 'dsh_pet_on_using_llm_tool'), ('OnLLMToolRespondEvent', 'dsh_pet_on_llm_tool_respond'), ('OnLLMResponseEvent', 'dsh_pet_on_llm_response'), ('OnAfterMessageSentEvent', 'dsh_pet_after_message_sent')]
```

**2. 桌宠安装器装进真实 AstrBot，AstrBot 启动加载**：

```
data_dir .../astrbot-root/data
(True, '')
['.dsh-pet-bridge', 'main.py', 'metadata.yaml']
[Core] [INFO] [star.star_manager:1131]: Loading plugin astrbot_plugin_dsh_pet_bridge ...
[Core] [INFO] [star.star_manager:1215]: Plugin astrbot_plugin_dsh_pet_bridge (v1.0.0) by dsh-pet-indesktop: 把 AstrBot 的思考/工具调用/回复状态写到本地 JSONL 文件……
$ cat data/plugin_data/astrbot_plugin_dsh_pet_bridge/astrbot.jsonl
{"ts":1793956407.573659,"agent":"astrbot","inst":"bfd958632266458da6d7a24d56c64ece","state":"idle"}
```

插件加载时的 `idle` 正好落在桌宠 `AstrBotMonitor.events_file_for(data)` 计算出的路径。

**3. 失败路径的现场复现（驱动了设计修改）**：第一版插件以 `after_message_sent` 收尾。经 WebChat（真实 dashboard 登录 → `/api/chat/new_session` → `/api/chat/send`）发一条「现在几点？」，事件文件停在 `thinking`、永远没有 `idle`。读 AstrBot 源码确认 `pipeline/respond/stage.py` 的流式分支在 `OnAfterMessageSentEvent` 之前 `return`，`on_llm_response` 才是两种模式共有的收尾点；同时发现 LLM 不可达时（假端点未起）AstrBot 重试 5 次后走 ERROR 分支，两个钩子都不触发——这就是 180s 静默回收存在的理由。

**4. 修正后整链（真实 AstrBotMonitor worker 线程）**：

```
login: ok token? True
session: ok True
sse bytes: 1963 has reply: True
PET RECEIVED ('astrbot', 'state', 'thinking')
PET RECEIVED ('astrbot', 'tool', 'future_task')
PET RECEIVED ('astrbot', 'state', 'working')
PET RECEIVED ('astrbot', 'state', 'thinking')
PET RECEIVED ('astrbot', 'state', 'idle')
{"ts":1793956852.6362295,"agent":"astrbot","inst":"20b5…","state":"thinking"}
{"ts":1793956852.6676636,"agent":"astrbot","inst":"20b5…","state":"working","tool":"future_task"}
{"ts":1793956852.6726987,"agent":"astrbot","inst":"20b5…","state":"thinking"}
{"ts":1793956852.6844628,"agent":"astrbot","inst":"20b5…","state":"idle"}
```

AstrBot 日志同时有 `Agent 使用工具: ['future_task']` 与工具结果；事件文件不含消息正文「现在几点？」、回复文本或工具参数。

**5. 用户可见行为（真实 `AgentLinkManager` + 记录调用的窗口替身，时间相对发消息时刻）**：

```
PET UI +1.22s ('anim', '写代码')
PET UI +1.22s ('bubble', 'AstrBot 正在思考。')
PET UI +1.23s ('bubble', 'AstrBot 正在当前会话 中处理 正在调用工具。')
PET UI +2.03s ('idle-anim', '')
PET UI +2.03s ('bubble', 'AstrBot 已完成本轮任务。')
```

（完成提醒比 idle 晚约 0.8 s = 既有完成确认窗 `_DONE_CONFIRM_MS`。工具气泡里「当前会话 中处理」的措辞是既有通用模板对无会话名记录的回落，与本改动无关。）

**无法自动验证的部分**：本容器没有桌面会话，**没有在真实屏幕上看到宠物动画**，也没有点过真实的 Qt 目录选择框/同意框（测试里由 monkeypatch 应答）；没有接 QQ/Telegram 等真实 IM 平台与真实大模型（无凭据）；没有在 Windows/macOS 与 Docker 部署下实测（本容器无 Docker 守护进程）。Docker 场景的正确性依赖于「插件按自身位置推 data 目录」，该推导在本机由「安装后插件反推路径 = 读方路径」的测试与第 2 步实机输出共同证明，但容器卷映射本身未实测。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/ tests/ integrations/astrbot-pet-bridge/ tools/bench_astrbot_link.py` | All checks passed |
| 先红 | 实现前跑 `pytest tests/test_agent_link_astrbot.py` | 收集期 `ImportError: AstrBotMonitor`（红） |
| 聚焦 | `pytest -q tests/test_astrbot_pet_bridge.py tests/test_agent_link_astrbot.py` | 46 passed |
| 相关族 | `pytest -q tests/test_agent_link.py tests/test_features.py tests/test_feature_modules_gating.py tests/test_feature_gating.py tests/test_final_cleanup_boundaries.py tests/test_persona_template.py tests/test_agent_link_subtraction.py tests/test_hermes_pet_bridge.py tests/test_architecture.py tests/test_agent_link_threads.py tests/test_agent_settings_hot_reload.py` | 483 passed, 5 skipped |
| 断言有效性 | 临时把 `llm_response` 改回「最终响应不收尾」重跑写方测试 | 4 failed（并发折叠/流式收尾/失败/写读联通），还原后 17 passed |
| 受影响时序族满载 3 遍 | 4 个忙循环占满 4 核，同时跑 `pytest -q tests/test_agent_link_astrbot.py tests/test_astrbot_pet_bridge.py tests/test_agent_link_threads.py tests/test_agent_link.py tests/test_uninstall_cleanup.py` ×3 | 3 次均 340 passed, 2 skipped（41.8 s / 43.2 s / 43.3 s） |
| 全量 | `QT_QPA_PLATFORM=offscreen python -m pytest -q` | 9029 passed, 83 skipped, 7 failed（1337 s）。其中 `test_uninstall_cleanup.py::test_uninstall_cleanup_removes_hooks_despite_own_slot_residue` 是本改动的预期变化（结果字典多了 `astrbot_bridge` 步骤），已更新断言并单文件复跑 24 passed；其余 6 条在**未改动的基线 `34d810b`** 上同样失败（`git stash -u` 后复跑），与本改动无关，见下 |
| 架构红线 | `tests/test_architecture.py`（含在相关族） | passed |

基线即失败的 6 条（本容器环境相关，未处理）：`test_balance.py` 两条（2027-01-01 元旦在当前节假日数据里被判成法定节日 / 定价时段 `idle≠peak`，依赖当前日期 2026-11-06 与日历数据）、`test_island_alert_colors.py` 两条（「200–1600px 屏宽找不到收缩级」，依赖本机字体度量）、`test_island_extension_chain.py::test_clock_minute_tick_changes_the_text_but_no_geometry`（跳分后宽度 268≠269，字体度量）、`test_memory_recall.py::test_manual_pin_remember_topic_unarchives`。

## 七、已知限制与后续

- **流式 + LLM 报错**最长 180 s 后才回待机，且不报 error（AstrBot 在该路径不给插件任何钩子）。AstrBot 若在 ERROR 分支补发 `on_agent_done`/新增收尾钩子，插件无需改动即会立刻收尾。
- **群聊高频**：每条 LLM 回复都是一个回合，完成气泡受既有 5 s 冷却节流；若嫌吵可关音效或改「AstrBot 专属文案」。
- AstrBot 用户手动设了 `DSH_PET_ASTRBOT_EVENTS` 时，内置通道读不到（需改用 `custom_agents` 指向同一文件），README 已写明。
- 不做审批/提问气泡：AstrBot 没有「等待人类批准」的插件钩子。
- 换 AstrBot 目录暂无 UI 入口：已记的 `astrbot_root` 仍是有效 AstrBot 目录时开启联动直接沿用；要换到另一个安装，先在菜单关闭联动，再清空 `config.json` 的 `agent_link.astrbot_root`，重新开启即弹目录选择。记的目录失效（被删/未挂载）时会自动重新弹选择。

## 八、风险与回滚

影响面限于 Agent 联动模块；新增两个 `agent_link` 子键，默认关闭/空串，旧配置读入自动补默认值，无迁移。回滚：`git revert` 即可；若用户已开启过，AstrBot 的 `data/plugins/astrbot_plugin_dsh_pet_bridge/` 会残留（回滚前先在菜单关闭 AstrBot 联动即自动删除；或手动删该目录与 `data/plugin_data/astrbot_plugin_dsh_pet_bridge/`），配置里的两个键会被旧版原样保留、无害。
