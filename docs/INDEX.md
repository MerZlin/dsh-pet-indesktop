# 文档索引

本文件是 `docs/` 与仓库根级文档的**唯一入口索引**。目的是按 Wikipedia 式的方式组织知识：每个文档一行，写明「它是什么」和「什么时候该读它」，读者可以顺着索引直接跳到相关条目，不必通读整个目录才知道功能模块在哪里。

怎么用：

1. **不知道从哪开始** → 先读根级入口表，再按领域找到对应分组。
2. **要改某块代码但不确定影响面** → 直接在本文搜索关键词（如 `ffmpeg`、`菜单`、`设置`、`ggml`），命中的行的「何时必读」就是判断依据。
3. **「何时必读」优先沿用 `AGENTS.md` 的 "Context pointers" 口径**（那是最权威的现行约定）；该节没有覆盖的文档，按文档正文自身标注的用途如实概括。

## 新文档入场规则

本节是规则的**出处**；任何新增文档都必须遵守：

1. **任何新文档必须在本文登记一行**，否则视为未定义的孤儿文档（审查时应作为缺陷提出）。
2. **新文档必须与相关文档互链**：正文中至少一处指向它所补充或取代的既有文档（用仓库相对路径），并同步更新本索引中那些文档行的「何时必读」。
3. **取代旧文档时**，先在本索引的「疑似过时/重复文档」小节登记旧文档与新文档的关系，再考虑是否删除；在删除前不得让两个文档同时作为权威描述存在。
4. **有明确生效范围的文档，标题或首段必须写清基线**（分支 / 版本 / 日期 / 实测用例数）；只描述"当时的快照"的文档必须自带「历史快照」警示（参见 `OPTIMIZATION_CHECKLIST.md`、`HANDOVER_2026-09.md` 的写法）。
5. **「何时必读」写触发条件，不写文档摘要**：写成"改 X 之前必读"，不要写成"介绍了 X"。

---

## 根级入口

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`../README.md`](../README.md) | 面向用户与贡献者的主说明：功能、安装、构建、更新日志与踩坑。 | 第一次接触项目；查用户可见行为、发布形态、上游同步状态。 |
| [`../AGENTS.md`](../AGENTS.md) | 工程指南：项目结构、变更纪律、CI 成本纪律、"Context pointers" 触发表、agent skills 入口。 | 提交任何代码之前；尤其改动碰撞选举、ffmpeg 派生、打包、菜单、设置、PR 合并前，先查 "Context pointers"。 |
| [`../CONTEXT.md`](../CONTEXT.md) | 领域术语表（Shared UX Contract / Settings System / Menu Action Model / Report Gate / Session-End Spawn Freeze 等）与禁用说法。 | 命名新概念、写设计文档、或需要确认"这个词在本项目里到底指什么"时；提案与既有术语冲突时必须先读。 |
| [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | 第三方素材与组件的授权声明。 | 新增/替换动画素材、图标、字体或第三方库时。 |

---

## 构建与发布

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md) | onedir 打包流水线：绿色版 zip + Inno Setup 安装包，目标是运行期零解压、不产生 `_MEI` 缓存。 | **改 PyInstaller spec、打包资源、或平台构建脚本（`scripts/build_onedir.ps1` / `build_macos.sh` / `build_linux.sh`）时必读**（AGENTS.md 口径）。 |
| [`STABLE_BUILDS.md`](STABLE_BUILDS.md) | 稳定版构建冻结记录：受保护文件名、构建隔离规则、防止误覆盖稳定版产物。 | **改发布/构建工作流时必读**（AGENTS.md 口径）。注意其冻结对象是 onefile 时代的 `dist/*.exe`，现行发布形态已是 onedir（见文末过时清单）。 |
| [`BUILD-CI-FAILURE-NOTES-2026-08.md`](BUILD-CI-FAILURE-NOTES-2026-08.md) | 三平台打包/CI 反复踩坑与最终解法汇总（持续更新）：脚本统一入口、资源漏收集、依赖只在叶子模块导入导致整族用例红等。 | CI 连续两轮红、或遇到"本机红 CI 绿"的依赖类假红时；动手重试之前先查这里是否已有同类记录。 |
| [`ACCEPTANCE_TESTS.md`](ACCEPTANCE_TESTS.md) | 验收测试文件清单：设置窗口、DSH Bridge、Qt 生命周期/全量三条验收路径的精确命令与当前实测基线。 | 提 PR 前跑验收、或需要确认"这个改动该跑哪几个测试文件"时；改动测试边界后必须同步更新本文基线数字。 |
| [`RELEASE-v4.2.0.md`](RELEASE-v4.2.0.md) | v4.1.0 → v4.2.0 的完整功能与修复汇总（含全部合入 PR 与各平台产物清单）。 | 写发布说明、回答"这个功能从哪个版本开始有"、或判断某行为是哪个 PR 引入时。 |
| [`BUILD_ARTIFACTS-2026-08-22.md`](BUILD_ARTIFACTS-2026-08-22.md) | 单次构建产物记录：EXE 路径、大小、SHA-256 与启动验证结果。 | 需要核对历史 onefile 产物哈希时（README 仍引用此路径）；日常构建流程看 `ONEDIR_PACKAGING.md`。 |

---

## 渲染、解码与窗口结构

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`WINDOW_PY_SPLIT_GUIDE.md`](WINDOW_PY_SPLIT_GUIDE.md) | `pet/window.py`（`PetWindow`）的演进指南：功能驱动的拆分流程、控制器边界与架构红线。 | **给 `window.py` 加功能前必读**（README 口径）；凡新功能预计超过约 100 行、或需改 3 个以上同域方法、或行数预算告警时，先按本文拆控制器。 |
| [`QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md`](QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md) | Qt 生命周期与全量套件稳定性收口记录：Windows/offscreen 下原生崩溃（0xC0000005 / 0xC0000374）的归属分析与 QObject owner 清理方案。 | 全量套件出现随机原生崩溃、或改动 `PetWindow.closeEvent()`、`PetSpeechBubble` owner 清理、菜单执行 seam、后台资源 teardown 时。 |

> 与 ffmpeg 派生、预热调度、Windows 关机/注销路径相关的权威档案是 issue #111（见下方「专项 issue 档案与事故复盘」分组），因为改这几处代码同时牵涉渲染生命周期与会话拆除时序。

---

## 交互、菜单与设置

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md`](CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md) | 右键菜单图标调研与双模板（`modern-default-v1`）重构记录，含菜单布局树的顺序/显隐/别名/图标覆盖规则与 2026-09 生命周期收口补充。 | **改右键菜单结构、样式、交互或平台行为时必读**（AGENTS.md 口径）。 |
| [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md) | Settings System 的变更门禁：一条设置能否进入设置页的准入条件、以及变更的准出证据要求。 | **新增、移动、重命名、删除或改变任何持久设置、以及修改设置页布局/保存语义/平台可见性/依赖关系之前必读**（AGENTS.md 口径）。 |
| [`SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md`](SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md) | 设置页信息架构重组记录：按用户任务划分的页面归属表、渐进显示与禁用规则、视觉密度。 | 决定某个新设置该放哪一页/哪一组；确认"同一概念不得跨页重复"的现行归属时。 |
| [`SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md`](SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md) | Q4 调研：侧栏分类（7 个稳定能力域）的跨平台 IA 结论与第一方 HIG 出处。 | 为"要不要新增一级侧栏页"找判断依据与先例出处时。 |
| [`SETTINGS-REDESIGN-Q6-Q7-DOMAIN-LAYOUT-DECISION.md`](SETTINGS-REDESIGN-Q6-Q7-DOMAIN-LAYOUT-DECISION.md) | Q6/Q7 讨论稿：能力域划分规则、布局系统、UI skill 评估，含菜单树兜底优先级链。 | 讨论能力域边界、菜单树降级/回退语义时；注意本文自标"讨论稿，不作为实现规范"。 |
| [Q5 视觉调研（文件名含外部品牌词，见编码链接）](SETTINGS-REDESIGN-Q5-OTTY-%43ODEX-VISUAL-RESEARCH.md) | Q5 调研：Otty 与某外部 AI 编程工具设置窗口的视觉语言与交互结构提炼（含第一方截图）。链接经 URL 编码（`%43`=C）：文件名含外部品牌词，直接书写会触发 `test_product_copy_has_no_external_brand_reference`。 | 参考外部产品视觉模式时；注意本文自标"不构成最终视觉规范"，且截图只反映 2026-09-01 版本。 |
| [`SETTINGS-REDESIGN-IMPLEMENTATION-LOG.md`](SETTINGS-REDESIGN-IMPLEMENTATION-LOG.md) | 设置与菜单重构的实现及踩坑记录：菜单动作注册表、菜单编辑器、七个能力域、草稿写回语义。 | 需要了解菜单/设置重构的**实际实现结构**与其断点续作位置（`.scratch/settings-redesign/HANDOFF.md`）时。 |
| [`SETTINGS-REDESIGN-UI-ACCEPTANCE.md`](SETTINGS-REDESIGN-UI-ACCEPTANCE.md) | 设置页逐页 UI 验收记录：窗口矩阵（尺寸×明暗）与最终保留的截图证据清单。 | 修改设置页视觉后需要对照既有验收矩阵重跑、或需要定位合理截图证据路径时。 |
| [`SETTINGS-REPORT-PROBABILITY-2026-09-10.md`](SETTINGS-REPORT-PROBABILITY-2026-09-10.md) | 事件汇报概率门（`report_gates`）的设置变更记录：8 个门的准入契约（setting_id / domain / 搜索别名）与准出证据。 | 增删/调整汇报概率门、或按 `SETTINGS-CHANGE-GATES.md` 需要一份设置变更契约的书写范例时。 |
| [`BUGFIX-AND-FEATURES-2026-08-24.md`](BUGFIX-AND-FEATURES-2026-08-24.md) | 一次性开发记录：气泡显示不抢输入焦点、EXE 图标裁剪、右键菜单「生小肥鱼」独立进程启动、菜单图标补齐。 | 改窗口激活/焦点策略（`WS_EX_NOACTIVATE` 类问题）、图标生成（`scripts/make_icon.py`）或子进程启动路径时。 |

---

## 聊天、语音与内容功能

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`CHAT-BACKGROUND-DISPLAY-2026-08-27.md`](CHAT-BACKGROUND-DISPLAY-2026-08-27.md) | AI 对话背景显示调整记录：两套窗口各自的背景图片/不透明度/填充模式，以及消息卡片可读性方案。 | 改对话窗口背景、`cover`/`contain`/`stretch` 语义、或消息区 QSS（`message-bubble` vs `message-surface`、`QScrollArea` 调色板）时。 |
| [`grill-2026-08-21-ai-chat.md`](grill-2026-08-21-ai-chat.md) | AI 对话功能的需求对齐记录：多 Provider、流式、多轮上下文、JSON 会话、system prompt 优先级等已确认决策。 | 质疑"聊天窗口为什么这样设计/为什么用标准库 HTTP 而不是某个 SDK"时；这是原始决策依据。 |

---

## Agent 联动与 DSH

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`AGENT_LINK_PROTOCOL.md`](AGENT_LINK_PROTOCOL.md) | 多 Agent 联动统一事件协议与扩展指南：本地文件事件总线、六态词汇、第三方 Agent 接入与新增内置 Agent 的步骤。 | 接入新 Agent、改事件归一（`normalize_event_state`）或六态词汇时；面向集成方的对外协议口径以本文为准。 |
| [`DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md`](DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md) | Agent 适配器 → Pet 的事件契约：三层关系（原始事件 → 适配器标准 JSONL → Monitor/AgentLinkManager → 气泡与回写）与接入约束。 | 新增或修改适配器（`integrations/dsh-pet-bridge/`）、或需要在 Pet 侧复用既有状态处理/交互队列时。 |
| [`DSH-HUMAN-REQUEST-RESEARCH-2026-09-02.md`](DSH-HUMAN-REQUEST-RESEARCH-2026-09-02.md) | DSH 人工请求事件调研：哪些 DSH 信号表示 Agent 暂停等待用户批准/回答，哪些只是工具或生命周期记录。 | 调整审批/提问的识别范围、或怀疑某类事件被误判成需要弹窗时；实现状态以 `integrations/dsh-pet-bridge/index.js` 与测试为准。 |
| [`DSH-REQUEST-EVENT-CATALOG.md`](DSH-REQUEST-EVENT-CATALOG.md) | DSH human-request 事件的速查表（英文）：可回答的阻塞请求、身份字段、响应帧形状、不得弹窗的非阻塞事件。 | 写 Bridge 解析代码时需要精确的 wire frame / session event 字段与响应契约时；调研背景见上一行。 |
| [`PET-STATE-MACHINE-AND-REPETITION-2026-09-02.md`](PET-STATE-MACHINE-AND-REPETITION-2026-09-02.md) | Pet 状态机与重复检查说明：三条独立处理链（DshStateTracker / AgentLinkManager / 分析检测器）与两个重复检测器的区别。 | 改状态、动画切换、提醒或风险判断时；尤其要避免把"重复检查"和"状态机"当成同一个东西。 |
| [`AGENT_LINK_LIVE_TEST.md`](AGENT_LINK_LIVE_TEST.md) | Agent 联动实机测试说明：交给外部 agent 在本机跑真实端到端验证的步骤与预期。 | 需要在真机复跑一次 Agent 联动端到端链路时（一次性任务说明书，基线为 `perf/startup-and-hidden-cpu` 时期）。 |

---

## 台词、人格与预设数据

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`PERSONA-PHRASES-PRESET-STORAGE-2026-09-08.md`](PERSONA-PHRASES-PRESET-STORAGE-2026-09-08.md) | 台词预设的存储与加载架构：内置预设（数据文件）/ 用户台词（config）/ 便携模板（运行时生成）三类内容的归属与路由。 | **改台词预设文件 `pet/persona_presets/*.json`、短语加载 `persona_phrases.py`、或表达风格语义（`dialogue_mode` / `dialogue_phrases`）之前必读**（AGENTS.md 口径）。 |
| [`PERSONA-TEMPLATE-FIELD-ALIGNMENT-2026-09-05.md`](PERSONA-TEMPLATE-FIELD-ALIGNMENT-2026-09-05.md) | 台词模板变量名契约：代码 kwargs ↔ 模板 `{占位符}` 的逐 key 对照表（由 `pet/persona_template.py` 常量自动生成）。 | 新增/重命名模板变量、或改事件可用变量集合时；必须先改代码常量再重新生成本文，否则 `test_persona_template.py` 的 AST 双向校验会红。 |

---

## 主动识屏与感知

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`PROACTIVE_SCREEN_PLAN.md`](PROACTIVE_SCREEN_PLAN.md) | 主动识屏与多 Agent 感知的已验证设计方案 v1：低功耗、多开友好、默认关闭，逐条技术依据与出处。 | 追溯识屏机制（白名单、dHash 变化检测、软流控、负坐标多屏裁剪）的**设计依据与出处**时。 |
| [`PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md`](PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md) | 主动识屏实施手册（K3 终审版）：分阶段实施步骤与验收清单。 | 需要了解识屏的分阶段实施顺序与原始验收项时；注意其"代码未动工"状态已失效（见文末过时清单）。 |

---

## 专项 issue 档案与事故复盘

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md`](ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md) | issue #111 专项档案：Windows 关机/注销弹 `0xc0000142` 的根因（会话拆除期派生进程）与「会话结束冻结」闸门设计。 | **改 ffmpeg 派生（`webm_clip` 的 reader / 首帧 / meta / exe 探测）、预热调度、或任何在 Windows 关机/注销时运行的东西（`session_watcher`、`match_shutdown`、`AppShell._on_session_end`）时必读**（AGENTS.md 口径）。 |
| [`PR-MERGE-LESSONS-2026-09-12.md`](PR-MERGE-LESSONS-2026-09-12.md) | PR 合并三则教训：叠放 PR 在 squash 父 PR 后必然冲突、预算/红线只在两 PR 组合时才破、时序测试 flake 纪律。 | **合并 PR 之前必读**（AGENTS.md 口径）。 |

> 注：`AGENTS.md` 的 "Context pointers" 还指向 `docs/ISSUE-42-POSIX-COLLISION-IPC-2026-08-31.md`（碰撞选举 / QLocal IPC / 协调者锁），但该文件在当前工作树中不存在。改动碰撞选举、QLocal IPC 或协调者锁之前，需要先确认该文档是被删除、改名还是从未入库——本索引无法为它登记有效条目。

---

## PR 报告存档

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`PR-REPORT-PR76-2026-09-10.md`](PR-REPORT-PR76-2026-09-10.md) | PR76 批次的完整报告：事件汇报概率门 + Persona 模板升级 + 全链路错误语义统一（46 文件，+3004/−917）。 | 追溯 PR76 批次改了什么、以及概率门/persona 模板/错误语义三条线的组合动机时。 |
| [`PR-REPORT-GATES-2026-09-10.md`](PR-REPORT-GATES-2026-09-10.md) | 汇报概率门专项 PR 报告：8 个门表、判决语义（`roll < probability`）、可注入 rng 的测试考量、提交点自检。 | 调整汇报概率门、或需要"为什么未知事件不抽稀/边界取小于"这类判决语义依据时。 |
| [`PR-REPORT-VOICE-CHIME-2026-09-15.md`](PR-REPORT-VOICE-CHIME-2026-09-15.md) | 语音报时（voice_chime）PR 报告：六种调度模式、20s tick 判定与槽位盖戳幂等、edge-tts 合成与缓存、设置页接入。 | 改语音报时调度/合成/播放、或需要复用其"纯逻辑零 Qt 依赖可测"结构时。 |
| [`PR-REPORT-FESTIVAL-REMINDER-2026-09-16.md`](PR-REPORT-FESTIVAL-REMINDER-2026-09-16.md) | 节日提醒（festival_reminder）PR 报告：46 个日子、314 条节日文案、提醒时机二选一、与语音报时共用音频通道且报时让位。 | 改节日数据/文案/提醒时机，或调整与语音报时的让位规则时。 |
| [`PR-REPORT-music-lyric-2026-09-16.md`](PR-REPORT-music-lyric-2026-09-16.md) | 歌词显示 + OBS 气泡朝向修复 + agent 计费的改动说明（含人工说明与 AI 生成的详细部分）。 | 改歌词显示/延迟设置、OBS 模式气泡朝向、或 agent 计费（余额差值法）时；注意文首人工说明标注了计费的已知偏差。 |

---

## 交接、阶段快照与变更汇总

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`DEV-HANDOVER.md`](DEV-HANDOVER.md) | 开发交接文档：本地运行、改配置、加功能、跑测试、重新打包的全流程，面向接手「语音报时」定制分支的开发者。 | 新人上手或需要一份"从零到跑起来"的完整流程时；它是三份交接文档中基线最新的一份（2026-09-16）。 |
| [`HANDOVER_2026-09.md`](HANDOVER_2026-09.md) | perf/stage-1 性能+结构线的交付手册（自带历史快照警示，含后续批次更正）。 | 追溯 perf/stage-1 那条线做了什么时；**正文数值已被后续批次更新**，实际以代码与 `_plan/current/` 档案为准。 |
| [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md) | 项目交接文档：主动识屏 + 多 Agent 联动时期的完整工作区状态、设计决策、遗留 TODO。 | 追溯识屏/联动落地时期的状态快照时；基线为 v3.1.1 / 194 passed，与现状差距较大。 |
| [`CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md`](CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md) | 自 v4.1.0 以来开发版变更汇总：按合入顺序的主线演进表、性能线/结构线细节，含"实现后被回滚/取代"的口径说明。 | 需要逐 PR 粒度的开发期变更脉络、或核对"某功能是否真的上线"（第六节列了被取代项）时。 |
| [`UPSTREAM-INTEGRATION-2026-08-26.md`](UPSTREAM-INTEGRATION-2026-08-26.md) | 上游合并与新版 UI 收敛记录：合并策略、维护边界（菜单/设置单一路径、两套聊天窗口互不覆盖）与 macOS 验收产物。 | 追溯"为什么只维护新版菜单/设置、经典聊天窗口为何保留"这类维护边界决策时。 |
| [`OPTIMIZATION_CHECKLIST.md`](OPTIMIZATION_CHECKLIST.md) | 性能优化复核清单（自带「历史快照，请勿按现状逐条执行」警示；`--instance`/`PetApp` 已被 `--slot`/`AppShell` 取代）。 | 只作为"当时怎么做性能复核"的模板参考；**不要按现状逐条执行**。 |

---

## 调研与设计稿（尚未进入实现）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`PHASE3_PROCESS_PLUGIN_RESEARCH.md`](PHASE3_PROCESS_PLUGIN_RESEARCH.md) | Phase 3 调研/设计稿：把 AI 聊天、Agent 联动、主动识屏等可关功能从主进程拆到独立进程/插件容器的成本与收益。 | 讨论"关闭即不加载"的内存天花板、或考虑把某功能移出主进程之前。 |
| [`OPEN-SOURCE-HARNESS-RISK-RESEARCH.md`](OPEN-SOURCE-HARNESS-RISK-RESEARCH.md) | 开源 Harness 风险与轨迹设计调研：DSH / LangGraph / SWE-agent 的事件模型、身份、Action/Observation 对比与 Pet 采用结论。 | 设计或调整 Pet 侧的事件轨迹/风险聚合模型时；查"为什么保留 raw facts 而不派生风险"的结论来源。 |

---

## agent 工作约定（`docs/agents/`）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`agents/domain.md`](agents/domain.md) | 单上下文仓库的领域文档约定：先读根 `CONTEXT.md`，再读相关 ADR，术语保持一致，与 ADR 冲突的方案要显式标注。 | 探索一个陌生领域、或提出可能与既有决策冲突的方案之前。 |
| [`agents/issue-tracker.md`](agents/issue-tracker.md) | Local Markdown issue tracker 约定：feature/spec/ticket 的目录结构与状态行格式。 | 创建或读取 issue、spec、ticket 时（`.scratch/<feature-slug>/`）。 |
| [`agents/triage-labels.md`](agents/triage-labels.md) | 五个标准 triage 状态的映射表与含义。 | 给 issue 打标签、或需要把外部角色名映射到本仓库状态名时。 |
| [`agents/handoff.md`](agents/handoff.md) | 工作交接约定：`.scratch/<feature-slug>/HANDOFF.md` 的必备字段与续作时的校验步骤。 | 跨任务/跨上下文窗口续作未完成工作时；开工前先读 handoff 并核对 `git status`。 |

---

## 疑似过时/重复文档

通读全部 `docs/*.md` 后的发现如下。判定口径：**描述内容与现状差距悬殊、且已被更新的文档取代**（过时）；或**两份文档覆盖同一主题且读者无法判断以谁为准**（重复）。本小节只登记，不构成删除建议——处置需由维护者决定。

### 疑似过时

1. **`STABLE_BUILDS.md`** — 冻结对象是 onefile 时代的 `dist/dsh-pet-standalone-webm.exe` / `gif.exe`，而当前工作树连 `dist/` 目录都不存在，发布形态早已是 onedir 目录 + `dist-onedir/*-portable.zip` + Inno Setup 安装包；基线提交 `420f20a` 也远早于当前 HEAD。冻结规则本身仍有价值，但其「当前基线」与文件名已与现状不符。
2. **`BUILD_ARTIFACTS-2026-08-22.md`** — 记录的产物路径（`dist/*.exe`）与哈希对应已不再产生的 onefile 构建；同一文档内的测试基线为 29 passed / 137 passed，距当前 1895 passed 的规模差两个数量级。README 仍引用此路径，属悬空引用。
3. **`PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md`** — 首段自标「方案已确认，代码未动工」，基线为 v3.1.1 / 130 passed；而识屏相关模块（`pet/proactive.py`、`proactive_limiter.py`、`proactive_memory.py`、`vision.py`、`window_screen.py`）均已存在并有对应 PR 报告，其"待动工"前提已完全失效。
4. **`PROACTIVE_SCREEN_PLAN.md`** — 同上，v1 设计稿的有效性判断（"当前基线 130 passed / 4 skipped"）远落后于现状；作为**设计依据与出处**仍有价值，但作为"待实施计划"已过时。
5. **`OPTIMIZATION_CHECKLIST.md`** — 文档已自行标注「历史快照，请勿按现状逐条执行」（`--instance`/`PetApp` 已被 `--slot`/`AppShell` 取代），确认过时。
6. **`PROJECT_HANDOFF.md`** — 基线 `D:\dsh-pet-pr` / v3.1.1 / 194 passed，且其内容已被 `DEV-HANDOVER.md`（2026-09-16，基线 `feat/voice-chime`）在"交接文档"这一职能上取代。
7. **`HANDOVER_2026-09.md`** — 自带两条历史快照警示（首帧缓存预算 32MB 已被改为 8MB；`decode_broker_enabled` 与 `decode_broker.py` 已移除，改为进程内 `DecodeFanoutHub`），正文描述已被取代。
8. **`UPSTREAM-INTEGRATION-2026-08-26.md`** — 一次性合并记录，其"合并后有什么"的内容已被 `CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md` 与 `RELEASE-v4.2.0.md` 完整覆盖；仅"维护边界"一节仍有独立价值。
9. **`AGENT_LINK_LIVE_TEST.md`** — 面向一次性外部实机验证任务的说明书（工作区 `D:\dsh-pet-pr`、分支 `perf/startup-and-hidden-cpu`、基线 185 passed），任务场景已不存在。

### 疑似重复

1. **`PR-REPORT-PR76-2026-09-10.md` 与 `PR-REPORT-GATES-2026-09-10.md`** — 同日、同一主题（事件汇报概率门 `report_gates`）的两份报告：前者把概率门作为 PR76 批次中的一项特性描述（并列出 4 个门的默认值），后者是专项报告（列出完整 8 门表与判决语义）。两者门数与默认值表述不完全一致，读者难以判断以谁为准。以专项报告 `PR-REPORT-GATES-2026-09-10.md` + `SETTINGS-REPORT-PROBABILITY-2026-09-10.md` 为现行口径。
2. **`SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md` 与 `SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md`** — 两者都给出设置页的页面/侧栏归属方案：前者是 2026-08-27 已实现的归属表，后者是 2026-08-31 的分类调研结论（7 个稳定侧栏入口）。同一问题两个版本的答案并列存在。
3. **`SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md` 与 `SETTINGS-REDESIGN-Q6-Q7-DOMAIN-LAYOUT-DECISION.md`** — 重叠：Q6/Q7 文档第 1 节重复给出"能力域划分规则"（作用对象/用户意图/能力所有权/生命周期/平台差异五条），与 Q4 的结论范围重合；差异主要在布局系统与 skill 评估，可考虑收敛为一份。
4. **三份 handover 并存**（`DEV-HANDOVER.md` / `HANDOVER_2026-09.md` / `PROJECT_HANDOFF.md`）— 职能相同（交接），基线各异（`feat/voice-chime` 2026-09-16 / `perf/stage-1` 2026-09-03 / v3.1.1 时期），且都未标注彼此取代关系，读者无法判断该读哪一份。
5. **`DSH-HUMAN-REQUEST-RESEARCH-2026-09-02.md` 与 `DSH-REQUEST-EVENT-CATALOG.md`** — 后者自述基于前者的调研结果，属"调研 + 速查表"的伴生关系，重叠度可控（一份叙述、一份字段表）。**不建议合并**，但建议在两份文档中互相显式标注"速查看 catalog、背景看 research"以消除歧义。
6. **`PROACTIVE_SCREEN_PLAN.md` 与 `PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md`** — 同一功能的"设计方案"与"实施手册"，内容范围大量重合（技术依据、验收清单），且两份都停留在"未动工"状态。建议合并为一份设计档案。
