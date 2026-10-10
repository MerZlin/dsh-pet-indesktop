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
4. **有明确生效范围的文档，标题或首段必须写清基线**（分支 / 版本 / 日期 / 实测用例数）；只描述"当时的快照"的文档必须自带「历史快照」警示（参见 `archive/OPTIMIZATION_CHECKLIST.md`、`archive/HANDOVER_2026-09.md` 的写法）。
5. **「何时必读」写触发条件，不写文档摘要**：写成"改 X 之前必读"，不要写成"介绍了 X"。

---

## 根级入口

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`../README.md`](../README.md) | 面向用户与贡献者的主说明：功能、安装、构建、更新日志与踩坑。 | 第一次接触项目；查用户可见行为、发布形态、上游同步状态。 |
| [`../AGENTS.md`](../AGENTS.md) | 工程指南：项目结构、变更纪律、CI 成本纪律、"Context pointers" 触发表、agent skills 入口。 | 提交任何代码之前；尤其改动碰撞选举、ffmpeg 派生、打包、菜单、设置、PR 合并前，先查 "Context pointers"。 |
| [`../CONTEXT.md`](../CONTEXT.md) | 领域术语表（Shared UX Contract / Settings System / Menu Action Model / Report Gate / Session-End Spawn Freeze 等）与禁用说法。 | 命名新概念、写设计文档、或需要确认"这个词在本项目里到底指什么"时；提案与既有术语冲突时必须先读。 |
| [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | 第三方素材与组件的授权声明。 | 新增/替换动画素材、图标、字体或第三方库时。 |
| [`../SPEC.md`](../SPEC.md) | 当前目标、非目标、插件化边界、兼容性和工程质量门。 | 修改架构边界、公共 API、测试门禁或发布约束前必读。 |
| [`../LOG.md`](../LOG.md) | 已完成变更、验证结果、风险和未完成项的事实日志。 | 交接、恢复中断工作或追溯某次规范化改动时。 |
| [`../LOG-INDEX.md`](../LOG-INDEX.md) | `LOG.md` 的日期与主题索引。 | 快速定位某次实现、验证或交付记录时。 |

---

## PR、Issue 与 Release 记录

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [RECORDS-INDEX.md](RECORDS-INDEX.md) | PR 报告、Issue/Bug、Release 和工程过程记录的分类导航；不移动、不合并历史证据。 | 查找某次 PR、Issue、版本发布或工程复盘记录时；迁移记录目录前必读。 |
## MOD 管理中心与作者接口（实施中）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [MOD 管理中心实施计划](modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md) | 经批准的本地列表、v1、教程与验证合同。 | 修改扩展管理、角色启停或公开作者接口前。 |
| [PLAN](../.scratch/mod-authoring-v1/PLAN.md) · [HANDOFF](../.scratch/mod-authoring-v1/HANDOFF.md) · [STATUS](../.scratch/mod-authoring-v1/STATUS.md) · [WORKLOG](../.scratch/mod-authoring-v1/WORKLOG.md) · [SUMMARY](../.scratch/mod-authoring-v1/SUMMARY.md) | M00–M05 的持续状态、证据与停点。 | 恢复本轮开发或核对是否真的交付前。 |

## 构建与发布

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md) | onedir 打包流水线：绿色版 zip + Inno Setup 安装包，目标是运行期零解压、不产生 `_MEI` 缓存。 | **改 PyInstaller spec、打包资源、或平台构建脚本（`scripts/build_onedir.ps1` / `build_macos.sh` / `build_linux.sh`）时必读**（AGENTS.md 口径）。 |
| [`STABLE_BUILDS.md`](STABLE_BUILDS.md) | 稳定版构建冻结记录：受保护文件名、构建隔离规则、防止误覆盖稳定版产物。 | **改发布/构建工作流时必读**（AGENTS.md 口径）。注意其冻结对象是 onefile 时代的 `dist/*.exe`，现行发布形态已是 onedir（见文末过时清单）。 |
| [`BUILD-CI-FAILURE-NOTES-2026-08.md`](BUILD-CI-FAILURE-NOTES-2026-08.md) | 三平台打包/CI 反复踩坑与最终解法汇总（持续更新）：脚本统一入口、资源漏收集、依赖只在叶子模块导入导致整族用例红等。 | CI 连续两轮红、或遇到"本机红 CI 绿"的依赖类假红时；动手重试之前先查这里是否已有同类记录。 |
| [`ACCEPTANCE_TESTS.md`](ACCEPTANCE_TESTS.md) | 验收测试文件清单：设置窗口、DSH Bridge、Qt 生命周期/全量三条验收路径的精确命令与当前实测基线。 | 提 PR 前跑验收、或需要确认"这个改动该跑哪几个测试文件"时；改动测试边界后必须同步更新本文基线数字。 |
| [`RELEASE-v4.2.0.md`](RELEASE-v4.2.0.md) | v4.1.0 → v4.2.0 的完整功能与修复汇总（含全部合入 PR 与各平台产物清单）。 | 写发布说明、回答"这个功能从哪个版本开始有"、或判断某行为是哪个 PR 引入时；**v4.2.0 之后的变更改看 [`RELEASE-v4.2.1.md`](RELEASE-v4.2.1.md)**。 |
| [`RELEASE-v4.2.1.md`](RELEASE-v4.2.1.md) | **v4.2.0 → v4.2.1 的发布稿（2026-09-23 已发布）**：57 个已合并 PR / 192 个提交的完整汇总（含 #181 歌词代理修复、#182 流畅度与岛墙批次）。文件分两段：`## 📦 下载` 至 `## 🙏 致谢` 是**发布正文**，`RELEASE-BODY-END` 注释之后的**发布前测试清单**（勾选式）、**视频预演脚本**（逐段分镜）与维护者清单**只在仓库内使用、不随 Release 发布**。 | **准备发布、跑人工验收、或录制演示视频时必读**；改版本号、打 tag、换 Release 正文（用文件头那行命令生成 body.md，别整份贴）、或需要"这一版到底该验哪些行为"的清单时。 |
| [`archive/BUILD_ARTIFACTS-2026-08-22.md`](archive/BUILD_ARTIFACTS-2026-08-22.md) | 历史构建产物记录：EXE 路径、大小、SHA-256 与启动验证结果。 | 仅在追溯旧 onefile 产物时阅读；日常构建流程看 `ONEDIR_PACKAGING.md`。 |

---

## 渲染、解码与窗口结构

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`WINDOW_PY_SPLIT_GUIDE.md`](WINDOW_PY_SPLIT_GUIDE.md) | `pet/window.py`（`PetWindow`）的演进指南：功能驱动的拆分流程、控制器边界与架构红线。 | **给 `window.py` 加功能前必读**（README 口径）；凡新功能预计超过约 100 行、或需改 3 个以上同域方法、或行数预算告警时，先按本文拆控制器。现行行数预算的实测值与「为什么这次只能校准而不是拆分」见 [`PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md`](PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md)。 |
| [`QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md`](QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md) | Qt 生命周期与全量套件稳定性收口记录：Windows/offscreen 下原生崩溃（0xC0000005 / 0xC0000374）的归属分析与 QObject owner 清理方案。 | 全量套件出现随机原生崩溃、或改动 `PetWindow.closeEvent()`、`PetSpeechBubble` owner 清理、菜单执行 seam、后台资源 teardown 时。 |

> 与 ffmpeg 派生、预热调度、Windows 关机/注销路径相关的权威档案是 issue #111（见下方「专项 issue 档案与事故复盘」分组），因为改这几处代码同时牵涉渲染生命周期与会话拆除时序。

---

## 交互、菜单与设置

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md`](CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md) | 右键菜单图标调研与双模板（`modern-default-v1`）重构记录，含菜单布局树的顺序/显隐/别名/图标覆盖规则与 2026-09 生命周期收口补充。 | **改右键菜单结构、样式、交互或平台行为时必读**（AGENTS.md 口径）。**删除已有菜单项**（入口收敛）时还要看 [`PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md`](PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md)：模板/注册表/legacy 三处怎么改，以及用户旧 `context_menu_layout` 里的残留节点怎么清。 |
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
| [`ISSUE-EDGE-TTS-VOICE-DEPRECATION-2026-09-22.md`](ISSUE-EDGE-TTS-VOICE-DEPRECATION-2026-09-22.md) | 事故档案：edge 合成「没声音」的两条根因（微软下架音色 + 连发偶发空音频）与对策（音色表兜底、重试、非空缓存判定）。 | **改语音报时的合成/缓存路径、或再遇到「配置了却没声音」时必读**；它记录了 `NoAudioReceived` 为什么不等于网络问题的判断链。 |
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
| [`archive/AGENT_LINK_LIVE_TEST.md`](archive/AGENT_LINK_LIVE_TEST.md) | 历史 Agent 联动实机测试说明：一次性外部验证任务及其当时基线。 | 仅在追溯 2026-08 的实机验证过程时阅读；当前验证入口以现行测试和交接文档为准。 |

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
| [`PROACTIVE-SCREEN-DESIGN.md`](PROACTIVE-SCREEN-DESIGN.md) | 主动识屏统一设计与实施档案：合并原始方案、实施手册、验收口径和历史修订，并明确当前 Worker 化边界。 | 修改主动识屏、截图、dHash、视觉模型或其后续 Worker 迁移前阅读；需要追溯原文时查看 `archive/`。 |
| [`issue-draft-主动识屏v420.md`](issue-draft-主动识屏v420.md) | v4.2.0「主动识屏永不触发」的 issue 草稿（基线 v4.2.0）：`MultiWindowProxy._physics_mode` 返回 `bool` 破坏哨兵语义，G1 守卫恒为真从而每次 tick 静默拦截；附最小修复建议与同版本启动装配缺口。 | 排查 `proactive_screen` 不触发、或改 `multi_window_shared.py` 的 `_physics_mode` 聚合语义与启动装配时。 |

---

## 专项 issue 档案与事故复盘

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md`](ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md) | issue #111 专项档案：Windows 关机/注销弹 `0xc0000142` 的根因（会话拆除期派生进程）与「会话结束冻结」闸门设计。 | **改 ffmpeg 派生（`webm_clip` 的 reader / 首帧 / meta / exe 探测）、预热调度、或任何在 Windows 关机/注销时运行的东西（`session_watcher`、`match_shutdown`、`AppShell._on_session_end`）时必读**（AGENTS.md 口径）。 |
| [`PR-MERGE-LESSONS-2026-09-12.md`](PR-MERGE-LESSONS-2026-09-12.md) | PR 合并三则教训：叠放 PR 在 squash 父 PR 后必然冲突、预算/红线只在两 PR 组合时才破、时序测试 flake 纪律。 | **合并 PR 之前必读**（AGENTS.md 口径）。 |
| [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md) | 代理/VPN 影响面清单：歌词取词（代理下 20~41s 超时）、edge-tts 语音、更新检查（jsdelivr 只有代理能通）、余额/识屏/对话（用户自配端点）、localhost 类（本地 TTS / DSH 联动）各自该不该走代理，附 30 秒探针与推荐分流配置。 | **改任何联网功能，或用户报「某功能昨天还好好的 / 歌词没了 / 语音不出声 / 更新检查失败」时必读**（系统代理与 VPN 是一等嫌疑）；也用于回答"桌宠为什么不自己绕过代理"。 |

> 注：`AGENTS.md` 的 "Context pointers" 还指向 `docs/ISSUE-42-POSIX-COLLISION-IPC-2026-08-31.md`（碰撞选举 / QLocal IPC / 协调者锁），但该文件在当前工作树中不存在。改动碰撞选举、QLocal IPC 或协调者锁之前，需要先确认该文档是被删除、改名还是从未入库——本索引无法为它登记有效条目。

---

## PR 报告存档

- [MOD 管理中心与作者接口 v1](PR-REPORT-MOD-CENTER-V1-2026-10-10.md)：列表、公开接口与冻结交付验证；审查 M00–M05 时必读。

- [项目内识屏 Worker 与 API 连接反馈（2026-10-10）](PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md)：Core4.2.4/Screen1.0.3 的可信 portable runtime、READY后心跳、故障重试、按钮旁HTTP结果码、鲸鱼娘图标与最终冻结链路证据；继续本轮验收、检查构建hash和人工限制前必读。

- [PR-REPORT-SIMPLE-API-2026-10-09](PR-REPORT-SIMPLE-API-2026-10-09.md)：恢复旧版主/可选视觉 Key 简易设置、即时消费与鲸鱼图标；本次增量、全量、冻结实机和新候选/人工步骤证据。

- [PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09](PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)：Setup 项目目录安装、保留/删除 data 卸载、关联清理 best-effort 的 U01–U06 实现与验证；验收新 Setup 时必读。

- [Phase5A 通用本地 DLC 收尾实施报告（2026-10-07）](PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md)：保留T历史；R中央API/即时配置/手动识屏/Core-only修复、真实原生seam红绿、最新默认4417p/15s、3×152满CPU、新Setup/6产物hash、逐文件/性能/本机冻结与人工步骤；工程范围验证完成，待用户人工验收，用户确认前不关闭Phase5A。

- [Phase5A 收尾实现计划（2026-10-07）](PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md)：Setup 官方包内嵌自动启用、Setup 外 ZIP/目录第三方 DLC、Core GUI subsystem 的逐步实现计划、测试矩阵和人工验收门；T01–T07 历史保留，R01–R07 缺陷修复继续跟踪；真实 Provider、屏幕与新 Setup 安装卸载人工门另记。
- [Phase5A 代码实现交付（2026-10-07）](PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md)：实现后续接入口、文件责任、保护边界、当前产物和准确验收状态。
- [Phase5A/5B-1 本地分发实施报告](PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md)：保留历史构建/测试/Setup 证据；2026-10-07 追加本轮文档重写和 `.scratch` 清理结果。旧绿色结果不代表新 registration/router/Setup 内嵌方案已实现。

- [Phase 4B 真实用户人工验收](PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md)：固定人工entry、真实Worker与独立E盘配置的准备/用户回执，临时信任与正式分发门分开；执行人工验收或补正式信任前必读。

- [Phase 4B Windows 连续收尾报告](PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)：LPAC、生产事务、扩展管理、双冻结 Core 与最终工程证据；核对当前实施/完成口径与人工门前必读。

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [Phase 4B-3 功能包事务部分实现报告](PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md) | 2026-10-03历史中间快照：当时部分实现、无OS沙箱默认拒绝、故障注入与实机文件锁证据；当前状态见2026-10-04连续收尾报告，不代表完整验收 |
| [Phase 4B-1 唯一安装状态与安全恢复](PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md) | 严格状态、revision 并发、内核锁、中断恢复及签名验证衔接；真实进程与有界成本证据，不含安装器和版本租约。 | 调整安装状态、恢复证明或进入 4B-2 跨进程占用前。 |
| [Phase 4B-1.5 资源 DLC 硬门](PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md) | 资源根路径、Registry/播放链、冲突清理、Starter/cache 边界和资源侧恢复；自动化完成，实机与公开接口仍待封存。 | 修改资源安装/播放/Registry、判断资源接口是否可公开或进入 4B-2 前。 |
| [Phase 4B-2 跨进程版本租约](PR-REPORT-PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-2026-10-03.md) | Core/Settings/Worker 的跨进程 OS 租约、Worker 父子交接、状态 revision/digest 复核和保守占用判定；自动化与 Windows 实机证据已完成，公开资源门仍 pending。 | 修改版本占用、Worker 交接、旧请求授权或进入 4B-3 本地事务前。 |
| [Phase 4A host 与自包含 Worker 独立构建](PR-REPORT-SCREEN-HOST-BUILD-2026-09-29.md) | 单一功能源码、受限端口/聊天服务、Ed25519 加载；两个无识屏 Core 与 10 项冻结场景、Qt 生命周期根因及实测成本。 | 继续 Phase 4B 安装事务、调整独立构建/加载或检查当前验收限制前。 |
| [Phase 4A 菜单与设置贡献](PR-REPORT-SCREEN-CONTRIBUTIONS-2026-09-28.md) | owner 注册/撤销、旧句柄隔离、菜单布局保留、设置草稿/清理和共享生命周期；未完成物理拆包及本版手测。 | 修改功能入口归属、命令授权、设置保存或扩展管理前。 |
| [Phase 4A 独立视觉配置与确认迁移](PR-REPORT-VISION-CONFIG-2026-09-27.md) | 独立模型、作用域凭据、可恢复迁移/并发保护与设置；无聊天进程边界及 3224 项全量通过，真实 keyring 和用户操作待验收。 | 修改视觉配置/迁移/凭据或接续贡献注册前。 |
| [Phase 4A 通用平台查询](PR-REPORT-DESKTOP-QUERY-2026-09-27.md) | stdlib 查询后端、旧 API 兼容、普通/共享 Qt 无识屏依赖验证及性能证据；不是完整拆包。 | 修改前台、光标、idle 查询或继续屏幕理解拆包前。 |
| [`PR-REPORT-ISLAND-HIDDEN-CHAT-DEADLOCK-2026-09-23.md`](PR-REPORT-ISLAND-HIDDEN-CHAT-DEADLOCK-2026-09-23.md) | 纯桌宠版岛隐藏死锁修复：无聊天构建 hidden_chat 单击路由回退展开卡片（岛能力开关 + 设置页开关按构建变体隐藏）。 | 改灵动岛单击路由 / hidden_chat 设置 / 打包变体（无 pet.chat）行为时。 |
| [`PR-REPORT-TEMPLATE.md`](PR-REPORT-TEMPLATE.md) | PR 报告模板：三份交付证据（修改文件说明 / 性能分析 / 实机运行记录）的逐节骨架与判定标准。 | **开新 PR 写报告前必读并整份复制**；2026-09-22 起三份证据是硬要求（`AGENTS.md` Delivery evidence discipline），由 `tests/test_pr_report_discipline.py` 机器化校验。 |
| [PR-REPORT-PLUGIN-DLC-PHASE1-2026-09-24.md](PR-REPORT-PLUGIN-DLC-PHASE1-2026-09-24.md) | Phase 1 资源型 DLC 实现报告：manifest 校验、Starter DLC、Registry、目录/ZIP 安装、回滚、兼容性与验证结果。 | 改资源 DLC、角色 Registry、内容安装事务、Starter DLC 打包或需要核对本轮测试限制时。 |
| [`PR-REPORT-PLUGIN-PHASE2-2026-09-24.md`](PR-REPORT-PLUGIN-PHASE2-2026-09-24.md) | Phase 2 Core 插件运行时实施报告：Registry、Context、EventBus、配置命名空间、capability、官方节日提醒插件、性能探针和验收限制。 | 改 Core 插件生命周期、官方 in-process 插件、插件配置隔离或准备进入 Phase 3 worker 迁移前。 |
| [`PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md`](PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md) | Phase 3B 自动/手动识屏 Worker：边界、回归、冻结程序、性能与剩余验收。 | 修改识屏 IPC、凭据、共享窗口或退出行为前必读。 |
| [`PR-REPORT-PLUGIN-PHASE3-2026-09-25.md`](PR-REPORT-PLUGIN-PHASE3-2026-09-25.md) | Phase 3A Worker 实施报告：JSONL 协议、QProcess 宿主、Agent Link 事件采集、崩溃恢复、fallback 和真实进程验证。 | 修改 Worker 生命周期、Agent Link 事件采集边界或进入主动识屏 Worker（Phase 3B）前必读。 |
| [plugin-phase-03-worker/PHASE3A-STABILITY-CLOSEOUT.md](plugin-phase-03-worker/PHASE3A-STABILITY-CLOSEOUT.md) | Phase 3A 稳定性封存补充报告：Qt 组合测试隔离、真实 Core 优雅退出、冻结 Worker smoke、Windows 可见桌面和 S0–S8/R0 门状态。 | 进入 Phase 3B、复核 Worker 退出/打包证据或回滚 Phase 3A 时必读。 |
| [`PR-REPORT-ENGINEERING-NORMALIZATION-2026-09-24.md`](PR-REPORT-ENGINEERING-NORMALIZATION-2026-09-24.md) | 工程规范化实施报告：测试分类与覆盖率基线、Ruff/mypy/pre-commit、统一检查入口、CI 质量/桌面矩阵和文档链接门禁。 | 修改测试分类、质量门、静态检查、CI 工作流、四大工程文档或一键验证入口时必读。 |
| [`PR-REPORT-PR76-2026-09-10.md`](PR-REPORT-PR76-2026-09-10.md) | PR76 批次的完整报告：事件汇报概率门 + Persona 模板升级 + 全链路错误语义统一（46 文件，+3004/−917）。 | 追溯 PR76 批次改了什么、以及概率门/persona 模板/错误语义三条线的组合动机时。 |
| [`PR-REPORT-GATES-2026-09-10.md`](PR-REPORT-GATES-2026-09-10.md) | 汇报概率门专项 PR 报告：8 个门表、判决语义（`roll < probability`）、可注入 rng 的测试考量、提交点自检。 | 调整汇报概率门、或需要"为什么未知事件不抽稀/边界取小于"这类判决语义依据时。 |
| [`PR-REPORT-VOICE-CHIME-2026-09-15.md`](PR-REPORT-VOICE-CHIME-2026-09-15.md) | 语音报时（voice_chime）PR 报告：六种调度模式、20s tick 判定与槽位盖戳幂等、edge-tts 合成与缓存、设置页接入。 | 改语音报时调度/合成/播放、或需要复用其"纯逻辑零 Qt 依赖可测"结构时；也要改共用音频通道的第三方（节日语音 / 点击台词朗读）时。 |
| [`PR-REPORT-FESTIVAL-REMINDER-2026-09-16.md`](PR-REPORT-FESTIVAL-REMINDER-2026-09-16.md) | 节日提醒（festival_reminder）PR 报告：46 个日子、314 条节日文案、提醒时机二选一、与语音报时共用音频通道且报时让位。 | 改节日数据/文案/提醒时机，或调整与语音报时的让位规则时。 |
| [`PR-REPORT-music-lyric-2026-09-16.md`](PR-REPORT-music-lyric-2026-09-16.md) | 歌词显示 + OBS 气泡朝向修复 + agent 计费的改动说明（含人工说明与 AI 生成的详细部分）。 | 改歌词显示/延迟设置、OBS 模式气泡朝向、或 agent 计费（余额差值法）时；注意文首人工说明标注了计费的已知偏差。歌词**取不到词**（有歌名没词、每首 9 秒）看 [`PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md`](PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md)。 |
| [`PR-REPORT-SELF-TALK-PRECACHE-2026-09-20.md`](PR-REPORT-SELF-TALK-PRECACHE-2026-09-20.md) | 点击台词朗读 + 本机语音预缓存（`self_talk_speak_enabled` / `self_talk_voice_precache_enabled`）：复用报时音频通道、后台补齐、缓存命名契约与 0 字节残file 判定。 | 改点击朗读/预缓存触发点、台词语音缓存命名或残file 判定、或调整 `_chime_wanted` 的通道存在性条件时。 |
| [`PR-REPORT-SELF-TALK-IMAGE-CHANCE-2026-09-20.md`](PR-REPORT-SELF-TALK-IMAGE-CHANCE-2026-09-20.md) | 自言自语「配图概率」（`self_talk_image_chance`，默认 30）：把"文本+图片等权随机"（实测出图 82.8%）改成先掷骰子再在池内等权选。 | 改 `show_random_self_talk` 的抽签逻辑、或需要"为什么默认值从等权变成 30%"的依据与回滚口径时。 |
| [`PR-REPORT-MUSIC-PLAYER-PATHS-2026-09-22.md`](PR-REPORT-MUSIC-PLAYER-PATHS-2026-09-22.md) | 音乐播放器路径设置（`music_player_paths`）PR 报告：设置页两行路径 + 后台「自动检测」、路径变了才清缓存、菜单提示改指设置页；含真机端到端与缺陷注入记录。 | 改 `pet/settings_music.py`、`pet/music_players.py` 的路径解析/缓存、或右键菜单「打开…给主人放歌」的可用性与提示文案时。 |
| [`PR-REPORT-SETTINGS-INTERACTION-TABS-2026-09-22.md`](PR-REPORT-SETTINGS-INTERACTION-TABS-2026-09-22.md) | 「互动」域设置页分页 PR 报告：页内任务标签（点击与音效 / 自言自语）+ 整域抽成 `pet/settings_interaction.py`（对话框净减 94 行、预算首次因拆分下调）；含"功能不丢"的机器化断言与三档宽度截图。 | 改互动域的行/分组/标签、把某个域也改成分页、或调整 `scripts/capture_settings_pages.py` 的截图入口时。 |
| [`PR-REPORT-music-lyric-align-2026-09-22.md`](PR-REPORT-music-lyric-align-2026-09-22.md) | 歌词对齐（`music_lyric_align`）PR 报告：手动校准快进/半途起播、会话选择与会话粘滞、`advance` 与 `line_now` 的分工；含性能实测表与网易云「不上报进度」的实机复现记录。 | 改歌词位置来源/对齐入口/多播放器会话选择时；或需要「为什么不做自动识别快进」的排查证据（桌面歌词不可读探针）时。**「对齐菜单点不动 / 歌词整首不显示」看 [`PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md`](PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md)**（估算位置冒充真值 + 系统代理拖死取词两处修复）。 |
| [`PR-REPORT-LOCAL-WIP-BATCH-2026-09-22.md`](PR-REPORT-LOCAL-WIP-BATCH-2026-09-22.md) | 本地 WIP 批次 PR 报告：交付证据纪律（三份证据 + 机器化校验）、`build_onedir.ps1` 的 Qt 绑定排他（不修则构建被 PyInstaller 中止）、产物 TTS 自检脚本（真产物假红 → 修掉）、`character_head_box()` 与 shenshen 头部框数据。 | 改 `scripts/build_onedir.ps1` 的排除清单、`scripts/verify_bundle_tts.py` 的闭包判定、`pet/catalog.py` 的 `body_box`/`head_box` 取值，或要写新的 PR 报告（含三个必备章节的实例）时。 |
| [`PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md`](PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md) | 歌词取词被系统代理拖死 + 网易云「歌词对齐」被误关的 PR 报告：系统代理下三源 20~41s 全超时 → 每首未缓存曲目「0 行/9.00s」，改直连后 1.27s/62 行；估算位置不再冒充「播放器上报的真值」。 | 改歌词取词的网络出口/超时/失败日志时；或排查「歌词突然全都没有」「歌曲只有歌名没有词」「歌词对齐菜单点不动」这类反馈时（含现场日志判读口径）。**影响面与推荐设置见 [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)**。 |
| [`PR-REPORT-ISLAND-RESHOW-NOCHAT-2026-09-23.md`](PR-REPORT-ISLAND-RESHOW-NOCHAT-2026-09-23.md) | 纯桌宠版「桌宠隐藏后单击灵动岛无反应」的发布后补丁报告（`1c3a59c`）：岛发 `chat_requested` → 无 Chat 变体的 `_show_island_chat` 可用性闸门静默返回，整次点击被吞；改为无对话能力时直接「显示桌宠」。 | 改灵动岛单击路由 / `_chat_from_island` / 隐藏态交互面 / 无 Chat 变体的可用性闸门时；或再遇「点了没反应」类反馈要先看这条链路（岛 → AppShell → 闸门）时。 |
| [`PR-REPORT-PERF-ISLAND-CONSOLIDATED-2026-09-23.md`](PR-REPORT-PERF-ISLAND-CONSOLIDATED-2026-09-23.md) | 流畅度/解码减负 + 岛远端硬墙 + 音效缓存 + 设置收口的 PR 报告：走路帧间补点（位置交付 28.6Hz→~160Hz）、碰撞 >50ms 卡顿 133→3、子宠进程补挂远端硬墙、零拷贝消融的诚实记录（崩溃案机理=绘制重入，未结案）。 | 改 `movement.move_anim_tick`/走路位移、webm 冷路径/首帧缓存/meta 后台化、岛碰撞远端模式与静态成员发布、音效候选缓存、或「多开」设置项时；排查 Qt6Gui 绘制重入崩溃时也要读（含消融对比与取证指针）。 |
| [`PR-REPORT-ONLINE-UPDATE-2026-09-24.md`](PR-REPORT-ONLINE-UPDATE-2026-09-24.md) | 在线更新 PR 报告：多源 manifest、镜像回退、大小/SHA-256 校验、Windows Inno Setup 自动安装、设置页与版本显示。 | 改 `pet/updater.py`、更新设置页、发布 manifest 或安装器重启行为时。 |
| [`PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md`](PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md) | 发布后补丁报告（三个独立提交）：① issue #186 多显示器跨屏拖拽/抛掷恢复——#137 把落位统一钳进本屏，改成「一次交互一个多屏活动区域快照」（`DesktopArea` + `band_bounds` 防错位空洞）；② 托盘图标消失——首帧不再同步解码后 `icon_pixmap()` 为空，占位图标 + `frame_ready` 换角色头像；③ 右键菜单「鼠标穿透」去重（设置页 + 托盘保留）。 | 改窗口落位/钳制/抛掷边界（`pet/window_placement.py`、`_interaction_area` 快照生命周期）时；改托盘图标/`_build_tray` 时；或再遇「托盘图标不见了」「桌宠拖不到副屏」这类反馈时（含单屏不可复现的探针口径）。 |

---

## 交接、阶段快照与变更汇总

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`DEV-HANDOVER.md`](DEV-HANDOVER.md) | 开发交接文档：本地运行、改配置、加功能、跑测试、重新打包的全流程，面向接手「语音报时」定制分支的开发者。 | 新人上手或需要一份"从零到跑起来"的完整流程时；它是三份交接文档中基线最新的一份（2026-09-16）。 |
| [`archive/HANDOVER_2026-09.md`](archive/HANDOVER_2026-09.md) | 历史 perf/stage-1 性能+结构线交付手册。 | 仅用于追溯旧交付批次；正文数值已被后续实现取代。 |
| [`archive/PROJECT_HANDOFF.md`](archive/PROJECT_HANDOFF.md) | 历史项目交接文档：主动识屏 + 多 Agent 联动时期的工作区状态。 | 仅用于追溯 v3.1.1 时期的决策和遗留事项。 |
| [`CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md`](CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md) | 自 v4.1.0 以来开发版变更汇总：按合入顺序的主线演进表、性能线/结构线细节，含"实现后被回滚/取代"的口径说明。 | 需要逐 PR 粒度的开发期变更脉络、或核对"某功能是否真的上线"（第六节列了被取代项）时。 |
| [`archive/UPSTREAM-INTEGRATION-2026-08-26.md`](archive/UPSTREAM-INTEGRATION-2026-08-26.md) | 历史上游合并与新版 UI 收敛记录。 | 仅用于追溯 2026-08 的合并策略和维护边界。 |
| [`archive/OPTIMIZATION_CHECKLIST.md`](archive/OPTIMIZATION_CHECKLIST.md) | 历史性能优化复核清单，使用早期 `--instance`/`PetApp` 口径。 | 只作为历史模板参考，禁止按现状逐条执行。 |

---

## 调研与设计稿（尚未进入实现）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`plugin-phase-03-worker/PHASE3B-PROACTIVE-SCREEN-DESIGN.md`](plugin-phase-03-worker/PHASE3B-PROACTIVE-SCREEN-DESIGN.md) | Core/Worker 双通道、request/response、额度、密钥、重建顺序和用户最终效果。 | 继续 Phase 3B 实现或失败后重建时必读；规划识屏选装交付另读中期对齐。 |
| [`plugin-phase-03-worker/PHASE3_PROCESS_PLUGIN_RESEARCH.md`](plugin-phase-03-worker/PHASE3_PROCESS_PLUGIN_RESEARCH.md) | Phase 3 调研/设计稿：把 AI 聊天、Agent 联动、主动识屏等可关功能从主进程拆到独立进程/插件容器的成本与收益。 | 讨论"关闭即不加载"的内存天花板、或考虑把某功能移出主进程之前。 |
| [`OPEN-SOURCE-HARNESS-RISK-RESEARCH.md`](OPEN-SOURCE-HARNESS-RISK-RESEARCH.md) | 开源 Harness 风险与轨迹设计调研：DSH / LangGraph / SWE-agent 的事件模型、身份、Action/Observation 对比与 Pet 采用结论。 | 设计或调整 Pet 侧的事件轨迹/风险聚合模型时；查"为什么保留 raw facts 而不派生风险"的结论来源。 |

---

## agent 工作约定（`docs/agents/`）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`agents/domain.md`](agents/domain.md) | 单上下文仓库的领域文档约定：先读根 `CONTEXT.md`，再读相关 ADR，术语保持一致，与 ADR 冲突的方案要显式标注。 | 探索一个陌生领域、或提出可能与既有决策冲突的方案之前。 |
| [`agents/issue-tracker.md`](agents/issue-tracker.md) | Local Markdown issue tracker 约定：feature/spec/ticket 的目录结构与状态行格式。 | 创建或读取 issue、spec、ticket 时（`.scratch/<feature-slug>/`）。 |
| [`agents/triage-labels.md`](agents/triage-labels.md) | 五个标准 triage 状态的映射表与含义。 | 给 issue 打标签、或需要把外部角色名映射到本仓库状态名时。 |
| [`agents/handoff.md`](agents/handoff.md) | 工作交接约定：准确停点、实际验证、提交状态与最终交接的保留规则。 | 恢复、暂停或结束正式计划时；先读交接并核对工作树。 |
| [`agents/planning-and-reporting.md`](agents/planning-and-reporting.md) | 计划、汇报及四份留档的唯一详细模板；正式说明后附实际使用效果。 | 编写正式计划、阶段汇报及交接记录前必读。 |
| [`agents/WORKFLOW-STANDARDIZATION-2026-09-30.md`](agents/WORKFLOW-STANDARDIZATION-2026-09-30.md) | 本轮规范固化与成果同步的设计、验收门和保护范围。 | 复核本次文档改动及推送授权时。 |
| [规范固化任务清单](../.scratch/workflow-standardization/PLAN.md) | W01–W08 的任务依赖、完成状态及证据入口。 | 核对本轮剩余工作时。 |
| [规范固化交接记录](../.scratch/workflow-standardization/HANDOFF.md) | 本轮准确停点、验证、提交和后续命令。 | 续作或审查本轮交付时。 |
| [规范固化当前状态](../.scratch/workflow-standardization/STATUS.md) | 本轮实现、自动化、人工待办与远程同步的独立状态摘要。 | 快速判断本轮是否完成时。 |

---

## 疑似过时/重复文档

通读全部 `docs/*.md` 后的发现如下。判定口径：**描述内容与现状差距悬殊、且已被更新的文档取代**（过时）；或**两份文档覆盖同一主题且读者无法判断以谁为准**（重复）。本小节只登记，不构成删除建议——处置需由维护者决定。

### 疑似过时

1. **`STABLE_BUILDS.md`** — 冻结对象是 onefile 时代的 `dist/dsh-pet-standalone-webm.exe` / `gif.exe`，而当前工作树连 `dist/` 目录都不存在，发布形态早已是 onedir 目录 + `dist-onedir/*-portable.zip` + Inno Setup 安装包；基线提交 `420f20a` 也远早于当前 HEAD。冻结规则本身仍有价值，但其「当前基线」与文件名已与现状不符。
2. **`archive/BUILD_ARTIFACTS-2026-08-22.md`** — 记录的产物路径（`dist/*.exe`）与哈希对应已不再产生的 onefile 构建；同一文档内的测试基线为 29 passed / 137 passed，距当前 1895 passed 的规模差两个数量级。README 已改为引用归档路径，现行构建流程见 `ONEDIR_PACKAGING.md`。
3. **`archive/PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md`** — 首段自标「方案已确认，代码未动工」，基线为 v3.1.1 / 130 passed；而识屏相关模块（`pet/proactive.py`、`proactive_limiter.py`、`proactive_memory.py`、`vision.py`、`window_screen.py`）均已存在并有对应 PR 报告，其"待动工"前提已完全失效。
4. **`archive/PROACTIVE_SCREEN_PLAN.md`** — 同上，v1 设计稿的有效性判断（"当前基线 130 passed / 4 skipped"）远落后于现状；作为**设计依据与出处**仍有价值，但作为"待实施计划"已过时。
5. **`archive/OPTIMIZATION_CHECKLIST.md`** — 文档已自行标注「历史快照，请勿按现状逐条执行」（`--instance`/`PetApp` 已被 `--slot`/`AppShell` 取代），确认过时。
6. **`archive/PROJECT_HANDOFF.md`** — 基线 `D:\dsh-pet-pr` / v3.1.1 / 194 passed，且其内容已被 `DEV-HANDOVER.md`（2026-09-16，基线 `feat/voice-chime`）在"交接文档"这一职能上取代。
7. **`archive/HANDOVER_2026-09.md`** — 自带两条历史快照警示（首帧缓存预算 32MB 已被改为 8MB；`decode_broker_enabled` 与 `decode_broker.py` 已移除，改为进程内 `DecodeFanoutHub`），正文描述已被取代。
8. **`archive/UPSTREAM-INTEGRATION-2026-08-26.md`** — 一次性合并记录，其"合并后有什么"的内容已被 `CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md` 与 `RELEASE-v4.2.0.md` 完整覆盖；仅"维护边界"一节仍有独立价值。
9. **`archive/AGENT_LINK_LIVE_TEST.md`** — 面向一次性外部实机验证任务的说明书（工作区 `D:\dsh-pet-pr`、分支 `perf/startup-and-hidden-cpu`、基线 185 passed），任务场景已不存在。

### 疑似重复

1. **`PR-REPORT-PR76-2026-09-10.md` 与 `PR-REPORT-GATES-2026-09-10.md`** — 同日、同一主题（事件汇报概率门 `report_gates`）的两份报告：前者把概率门作为 PR76 批次中的一项特性描述（并列出 4 个门的默认值），后者是专项报告（列出完整 8 门表与判决语义）。两者门数与默认值表述不完全一致，读者难以判断以谁为准。以专项报告 `PR-REPORT-GATES-2026-09-10.md` + `SETTINGS-REPORT-PROBABILITY-2026-09-10.md` 为现行口径。
2. **`SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md` 与 `SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md`** — 两者都给出设置页的页面/侧栏归属方案：前者是 2026-08-27 已实现的归属表，后者是 2026-08-31 的分类调研结论（7 个稳定侧栏入口）。同一问题两个版本的答案并列存在。
3. **`SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md` 与 `SETTINGS-REDESIGN-Q6-Q7-DOMAIN-LAYOUT-DECISION.md`** — 重叠：Q6/Q7 文档第 1 节重复给出"能力域划分规则"（作用对象/用户意图/能力所有权/生命周期/平台差异五条），与 Q4 的结论范围重合；差异主要在布局系统与 skill 评估，可考虑收敛为一份。
4. **历史 handover 已归档**（`archive/HANDOVER_2026-09.md` / `archive/PROJECT_HANDOFF.md`）— 当前交接入口以 `DEV-HANDOVER.md` 为准，归档文件仅用于追溯旧基线。
5. **`DSH-HUMAN-REQUEST-RESEARCH-2026-09-02.md` 与 `DSH-REQUEST-EVENT-CATALOG.md`** — 后者自述基于前者的调研结果，属"调研 + 速查表"的伴生关系，重叠度可控（一份叙述、一份字段表）。**不建议合并**，但建议在两份文档中互相显式标注"速查看 catalog、背景看 research"以消除歧义。
6. **主动识屏文档已合并** — 当前入口为 `PROACTIVE-SCREEN-DESIGN.md`；`archive/PROACTIVE_SCREEN_PLAN.md` 与 `archive/PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md` 保留为原始档案，不再作为现行规范。

## 插件化与 DLC 分阶段文档

> 2026-10-02 现行路线：**运行解耦与真正可拔除交付并行；官方选装必做，第三方生态条件化。** Phase 1/2 保留实现基线，但 Phase 1 的安装后解析、冲突清理、Starter/Core 更新保护和真实播放仍有 P0 硬门；Phase 3A/3B 证据不等于资源/功能包可卸载。4B 先过资源硬门，再按状态账本→租约→本地事务→管理 UI 推进。用户仅确认手动“看看屏幕”，自动未继续等待验收（不判故障），停用/托盘退出未测，不代表全部人工、安装卸载或三平台验收完成。详见[DLC 基线评审响应](plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md)。

### 总入口与单一职责

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [功能归属与交付总表](plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md) | 唯一功能主表：Core、资源类型和 11 个官方逻辑领域的实现、运行/交付、UI、数据、依赖与可拔除标准；不等于 11 个安装包。 | 确认某功能归属、依赖、包内外边界或防止遗漏 AI 对话与文件理解时。 |
| [v5 总路线图](plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md) | 阶段顺序、状态、出口、证据和失败重启点：屏幕理解样板之后重点拆 AI 对话。 | 规划下一阶段、调整优先级或判断交付完成度时。 |
| [中期 grill 对齐](grill-2026-09-27-插件化中期对齐.md) | 保留 Q1–Q5 原决定，追加本轮功能归属与 AI 排期，区分用户确认、规划默认与待定产物。 | 追溯官方选装、Setup/ZIP、便携、菜单设置注册等要求为何确定时。 |
| [DLC 开放顺序与第三方接口路线](plugin-roadmap/PLUGIN-DLC-OPENING-ORDER-AND-THIRD-PARTY-INTERFACES.md) | 记录 VPet 对比、资源 DLC/官方功能/用户自制 Worker 的开放顺序、接口层级、数据存储和 Python host 卸载判定。 | 设计用户自制 DLC、开放本地目录/ZIP、确定第三方 Worker 边界或讨论热卸载宣传时。 |
| [DLC 基线评审响应](plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md) | 汇总旧快照评审的已改进、部分改进、P0 未解决和未复现问题，并定义资源硬门、信任根、Windows 事务及 Phase 1–7 调整。 | 修改 DLC 安装/Registry、包信任、租约/事务、Phase 5 分发或 Phase 6 生态前必读。 |
| [DLC 开放顺序 grill](grill-2026-10-02-dlc开放顺序与第三方接口.md) | 本轮确认的边界记录：第一批用户自制功能采用资源 DLC + 外部 Worker，暂不开放任意 Python host 和 Steam/Workshop。 | 追溯本轮用户确认和推荐答案时。 |

### 阶段导航

| 阶段 | 状态 | 目录 | 用途与出口 |
|---|---|---|---|
| Phase 1 | 角色资源实现基线，P0 待封存 | [foundation](plugin-phase-01-foundation/) | 角色 Registry、ContentManager 与 fallback 已有实现；安装后解析、冲突清理、Starter/Core 更新保护和真实播放仍是硬门。 |
| Phase 2 | 已完成运行时基线 / 后续贡献合同待实施 | [runtime](plugin-phase-02-runtime/) | 官方 Context/EventBus/配置/生命周期；功能 UI 可在主进程运行但归功能包交付。 |
| Phase 3A/3B/3C | 3A 已封存；3B 全量通过、人工门未完成；3C 已完成本地风险评估 | [worker](plugin-phase-03-worker/) | 维护 Agent/识屏隔离，其他能力逐项评估，不阻塞选装样板。 |
| Phase 4A/4B | 4A 边界基线；4B 先过资源硬门再做本地管理 | [updates](plugin-phase-04-updates/) | 依次接续 4B-1 状态账本、4B-1.5 资源硬门、4B-2 租约、4B-3 事务和 4B-4 UI；不替换默认构建或宣称人工验收完成。 |
| Phase 5A/5B-1 | T01–T07 本轮授权范围收尾完成 / 人工发布门未执行 | [distribution](plugin-phase-05-distribution/) | Setup 官方包内嵌自动启用；Setup 外 ZIP/目录按 manifest 自动路由为本地第三方 DLC；Core GUI subsystem；当前源码构建、授权实机、最终全量 4334/15 和满负载三轮各 97 项通过；真实 Setup/Provider/用户人工门未执行、另获授权；其余5B独立推进。 |
| Phase 6 | 条件启用 | [ecosystem](plugin-phase-06-ecosystem/) | 第三方内容/Worker SDK、社区生态；不是官方包交付的前置。 |
| Phase 7 | 正式发布前验收 | [release](plugin-phase-07-release/) | 最小 Core、承诺选装范围、三平台、迁移装卸、性能与恢复；第三方未开放不阻塞。 |

### Phase 1–3 现行合同与证据入口

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [Phase 1 架构](plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md) | 资源 DLC 的已实现边界、Core 原语/策略/内容区分，以及 legacy/fallback 恢复点。 | 修改角色来源、资源安装或审计功能是否真正离开 Core 前。 |
| [插件 API 合同](plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md) | 官方内部 API、资源/可执行包区分、UI 贡献状态表、Worker 控制/业务协议与临时凭据边界。 | 新增端口、manifest、菜单设置注册或 IPC 时；菜单/设置状态以本合同为准。 |
| [v4→v5 迁移合同](plugin-phase-01-foundation/PLUGIN-MIGRATION-v4-to-v5.md) | 数据所有者、备份、幂等恢复、实例、会话、密钥和卸载默认保留数据。 | 改 schema、拆功能配置、设计便携或清除用户数据前。 |
| [Phase 2 入口](plugin-phase-02-runtime/README.md) | 已完成运行时与尚未实现的贡献注册、物理拆包分开记录。 | 开始或交接官方插件运行时工作时。 |
| [运行时设计](plugin-phase-02-runtime/PLUGIN-RUNTIME-DESIGN.md) | Registry/Context/EventBus、生命周期、依赖方向，以及后续 owner 贡献合同。 | 修改插件端口、GUI 生命周期、故障隔离或功能宿主时。 |
| [运行时测试计划](plugin-phase-02-runtime/PLUGIN-RUNTIME-TEST-PLAN.md) | 当前行为回归与新增离线、迁移恢复、贡献清理、物理可拔除门分组验收。 | 设计插件/包状态测试、准备 Phase 4 验收时。 |
| [Phase 3 入口](plugin-phase-03-worker/README.md) | 3A、3B 实现与证据状态、3C 风险评估及到可拔除交付的移交。 | 迁移网络/截图/Agent 执行、判断隔离是否已变成交付时。 |
| [Worker 架构与阶段计划](plugin-phase-03-worker/PHASE3_PROCESS_PLUGIN_RESEARCH.md) | QProcess、`pet-worker/v1` 与 `agent-event/v1`、request/response、重启/fallback 和剩余边界。 | 修改 Worker 协议、进程清理、Source/Adapter 或评估其他能力时。 |
| [Phase 3B 识屏设计](plugin-phase-03-worker/PHASE3B-PROACTIVE-SCREEN-DESIGN.md) | 已有自动/手动数据流、单次凭据和 shared Worker；把 UI/策略/执行共同移交 Phase 4/5。 | 修改主动识屏、手动看屏幕、权限或构建拆包前。 |
| [Phase 3A 稳定性收口](plugin-phase-03-worker/PHASE3A-STABILITY-CLOSEOUT.md) | 保留原稳定性验证证据，不按后续目标覆写原结论。 | 复查 3A 验收、异常退出或冻结 Worker 基线时。 |
| [Phase 3B 最新收尾](plugin-phase-03-worker/PHASE3B-STABILITY-CLOSEOUT.md) | 保留旧失败和构建证据，§10 追加前台边界修复后全量 3123 passed、显式探针及手动正常/自动未验收的澄清，不声明全部封存。 | 判断 3B 是否可继续迁移、复验构建或补人工记录前。 |
| [Phase 3C 隔离风险评估](plugin-phase-03-worker/PHASE3C-ISOLATION-ASSESSMENT.md) | AI、共享依赖、音乐、语音、账户、外部服务和低风险功能的源码/测试依据、所有者与取舍。 | 决定新增 Worker、审计屏幕理解拆包或 AI 交付前。 |

### Phase 4–7 实施与发布计划

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [Phase 4 入口](plugin-phase-04-updates/README.md) | 查询、配置、贡献、host 和自包含 Worker 已有 Windows 自动化基线；4B 接续真实安装卸载及管理。 | 开始本地安装事务、正式交付或核对当前状态前。 |
| [Phase 4A 屏幕理解拆包审计](plugin-phase-04-updates/PHASE4A-SCREEN-DELIVERY-AUDIT.md) | 只读核对平台查询、聊天/密钥、UI 注册、数据/卸载及构建来源，区分事实、建议、待验证；不是已完成拆包。 | 冻结屏幕理解包边界、依赖/加载方案或设计最小 Core 验证前。 |
| [Phase 4A 屏幕理解最小设计](plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md) | 受限端口、可选聊天服务、签名目录加载和独立产物已实现；区分验证描述/进程内使用句柄与待实现安装状态。 | 调整功能 host、联动、构建、加载及 Phase 4B 安装边界前。 |
| [Phase 4B 本地管理设计](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md) | 4B-1 状态服务和 4B-2 跨进程版本租约已实现并通过本轮自动化/实机验收；4B-3 安装事务和管理界面尚未实施。 | 开始本地安装、停用、卸载、重装或状态同步前。 |
| [Phase 4B-1.5 资源硬门设计](plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md) | 资源安装→Registry→播放、冲突保护、Starter/cache 隔离和资源恢复硬门；代码、自动化、文档与保护门已通过，本地封存就绪但尚未提交。 | 进入 4B-2 前必读。 |
| [Phase 4B-3 本地事务设计](plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) | 官方功能包目录/ZIP staging、完整预检、安装升级卸载事务、版本租约等待、启动确认、回滚、延迟 GC 和故障恢复边界。 | **实施或审查官方功能包安装、升级、卸载、恢复和删除前必读**。 |
| [Phase 4B-2 跨进程版本租约设计](plugin-phase-04-updates/PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md) | 每个安装版本的 OS 租约目录、Host/Settings/Worker 生命周期、父子交接、状态监视与 revision-bound 旧请求保护；不提供 hot-unload 或公开 SDK。 | 修改租约、版本切换、Worker reservation 或进入 4B-3 前必读。 |
| [Phase 4B 任务清单](../.scratch/phase4b-local-management/PLAN.md) · [交接记录](../.scratch/phase4b-local-management/HANDOFF.md) | 逐门进度、实际验证与下一条操作；文本明确纳入 Git，保留最终交接，不收录构建临时目录。 | 恢复 Phase 4B 工作或核对未完成门前。 |
| [Phase 4B 当前状态](../.scratch/phase4b-local-management/STATUS.md) | 区分 Phase 4A 历史成果、4B-1 状态进度、后续未实施项、人工待办及远程同步。 | 恢复 Phase 4B 或判断安装管理是否已可用前。 |
| [安装与更新协议](plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md) | 共用事务和权威状态、信任校验、原子激活、回滚、卸载/重装与 Core/DLC 更新隔离。 | 改 Setup/ZIP/应用内安装器、代码加载、文件占用或远程来源前。 |
| [Phase 5 官方分发](plugin-phase-05-distribution/README.md) | 5A 小 Core Setup 官方内嵌选装、Setup 外本地 ZIP/目录导入和 5B AI 推广；远程适配按需。 | 设计安装向导、ZIP/目录路由、便携、AI 拆包批次或其他官方包前。 |
| [Phase 5A 本地分发设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) | 当前合同：Setup 只内嵌官方 AI/Screen；Setup 外显式 ZIP/目录按 manifest 接受本地第三方 DLC；签名不是本地前置；历史章节保留审计记录。 | 实施 registration、验证器、事务/启动、UI router、Setup 或 Core 构建前必读。 |
| [Phase 6 条件生态](plugin-phase-06-ecosystem/README.md) | 未来公开作者/目录/SDK/审核/签名生态；与 Phase5A 用户主动导入的本地可信 Python DLC 分开，不把本地路径包装成公开沙箱。 | 评估第三方作者、公开签名发布、目录、撤销或 Workshop 时。 |
| [Phase 7 发布门](plugin-phase-07-release/README.md) | 最小 Core 与官方选装范围、三平台、配置恢复、真实卸载、性能与可信发布。 | 准备正式发布、确定承诺范围或补发布证据时。 |

实现菜单与设置仍须阅读 [菜单结构研究](CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md)和[设置变更门](SETTINGS-CHANGE-GATES.md)；独立视觉配置已在自动化域新增组件，未重排顶层导航；屏幕理解贡献已接入，其他功能与真实安装状态仍待后续迁移。历史 PR 报告继续在“PR 报告存档”登记，不以计划正文代替交付证据。

## 在线更新与网络发布

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [ONLINE-UPDATE.md](ONLINE-UPDATE.md) | Core 安装包在线更新协议：多源 manifest、真实二进制镜像、大小/SHA-256 校验、Windows Inno Setup 自动安装和回滚边界。 | **改自动更新、更新设置页、发布 manifest、下载校验或安装器重启行为前必读**；网络出口问题另读 [NETWORK-PROXY-AND-VPN-2026-09-22.md](NETWORK-PROXY-AND-VPN-2026-09-22.md)。 |

## 项目入口与协作连续性（2026-10-02）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [项目入口](PROJECT-ENTRY.md) | 渐进式披露项目总览、目录树、路线状态、按问题查文档和不确定性。 | 新对话、新贡献者或不清楚当前状态时首先阅读。 |
| [开发者上手 README](../README.md) | 面向开发者的运行、测试、Worker/DLC 边界和贡献检查清单。 | 开始本地开发、验证或审阅架构前阅读。 |
| [施工连续性规范](agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md) | 计划、施工记录、交接、状态、任务总结及 Git 授权规则。 | 创建或恢复持续任务时阅读。 |
| [DLC 开放顺序与第三方接口](plugin-roadmap/PLUGIN-DLC-OPENING-ORDER-AND-THIRD-PARTY-INTERFACES.md) | 资源 DLC、官方功能包、外部 Worker 和条件 Feature Host 的开放顺序。 | 设计作者接口、包类型或生态开放门时阅读。 |
| [DLC 基线评审响应](plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md) | 外部评审问题的已改进、部分改进、未解决和后续硬门。 | 调整 Phase 1 P0、4B 资源硬门或发布条件时阅读。 |
| [Phase 4B-1.5 资源硬门设计](plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md) | 资源安装→Registry→播放、冲突保护、Starter/cache 隔离和资源恢复门。 | 进入 4B-2 前必读。 |
| [Phase 4B 总任务清单](../.scratch/phase4b-local-management/PLAN.md) · [状态](../.scratch/phase4b-local-management/STATUS.md) · [交接](../.scratch/phase4b-local-management/HANDOFF.md) · [总结](../.scratch/phase4b-local-management/SUMMARY.md) | 4B-1、4B-1.5 当前证据、4B-2 已完成记录和 4B-3/4/5 未完成项。 | 恢复本地安装管理路线时阅读。 |
| [Phase 4B-2 任务记录](../.scratch/phase4b-2-cross-process-version-lease/PLAN.md) · [状态](../.scratch/phase4b-2-cross-process-version-lease/STATUS.md) · [交接](../.scratch/phase4b-2-cross-process-version-lease/HANDOFF.md) · [总结](../.scratch/phase4b-2-cross-process-version-lease/SUMMARY.md) | 4B-2 的实现、测试、实机证据和限制；4B-1.5 人工验收已通过，公开稳定 API 仍单独受开放门约束。 | 恢复跨进程租约工作或核对 4B-3 前置条件时阅读。 |
| [当前文档连续性状态](../.scratch/documentation-continuity/STATUS.md) · [任务总结](../.scratch/documentation-continuity/SUMMARY.md) | 本轮规则、入口、README 和验证停点。 | 接续本轮文档任务时阅读。 |

`.scratch` 链接是当前任务记录入口，不是普通设计文档；不要把构建缓存、原始日志、私钥或生成产物登记到索引。
