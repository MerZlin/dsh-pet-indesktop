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
| [`RELEASE-v4.2.0.md`](RELEASE-v4.2.0.md) | v4.1.0 → v4.2.0 的完整功能与修复汇总（含全部合入 PR 与各平台产物清单）。 | 写发布说明、回答"这个功能从哪个版本开始有"、或判断某行为是哪个 PR 引入时；**v4.2.0 之后的变更改看 [`RELEASE-v4.2.1.md`](RELEASE-v4.2.1.md)**。 |
| [`RELEASE-v4.2.1.md`](RELEASE-v4.2.1.md) | **v4.2.0 → v4.2.1 的发布稿（2026-09-23 已发布）**：57 个已合并 PR / 192 个提交的完整汇总（含 #181 歌词代理修复、#182 流畅度与岛墙批次）。文件分两段：`## 📦 下载` 至 `## 🙏 致谢` 是**发布正文**，`RELEASE-BODY-END` 注释之后的**发布前测试清单**（勾选式）、**视频预演脚本**（逐段分镜）与维护者清单**只在仓库内使用、不随 Release 发布**。 | **准备发布、跑人工验收、或录制演示视频时必读**；改版本号、打 tag、换 Release 正文（用文件头那行命令生成 body.md，别整份贴）、或需要"这一版到底该验哪些行为"的清单时。 |
| [`BUILD_ARTIFACTS-2026-08-22.md`](BUILD_ARTIFACTS-2026-08-22.md) | 单次构建产物记录：EXE 路径、大小、SHA-256 与启动验证结果。 | 需要核对历史 onefile 产物哈希时（README 仍引用此路径）；日常构建流程看 `ONEDIR_PACKAGING.md`。 |

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
| [`issue-draft-主动识屏v420.md`](issue-draft-主动识屏v420.md) | v4.2.0「主动识屏永不触发」的 issue 草稿（基线 v4.2.0）：`MultiWindowProxy._physics_mode` 返回 `bool` 破坏哨兵语义，G1 守卫恒为真从而每次 tick 静默拦截；附最小修复建议与同版本启动装配缺口。 | 排查 `proactive_screen` 不触发、或改 `multi_window_shared.py` 的 `_physics_mode` 聚合语义与启动装配时。 |

---

## 专项 issue 档案与事故复盘

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md`](ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md) | issue #111 专项档案：Windows 关机/注销弹 `0xc0000142` 的根因（会话拆除期派生进程）与「会话结束冻结」闸门设计。 | **改 ffmpeg 派生（`webm_clip` 的 reader / 首帧 / meta / exe 探测）、预热调度、或任何在 Windows 关机/注销时运行的东西（`session_watcher`、`match_shutdown`、`AppShell._on_session_end`）时必读**（AGENTS.md 口径）。 |
| [`PR-MERGE-LESSONS-2026-09-12.md`](PR-MERGE-LESSONS-2026-09-12.md) | PR 合并三则教训：叠放 PR 在 squash 父 PR 后必然冲突、预算/红线只在两 PR 组合时才破、时序测试 flake 纪律。 | **合并 PR 之前必读**（AGENTS.md 口径）。 |
| [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md) | 代理/VPN 影响面清单：歌词取词（代理下 20~41s 超时）、edge-tts 语音、更新检查（jsdelivr 只有代理能通）、余额/识屏/对话（用户自配端点）、localhost 类（本地 TTS / DSH 联动）各自该不该走代理，附 30 秒探针与推荐分流配置。 | **改任何联网功能，或用户报「某功能昨天还好好的 / 歌词没了 / 语音不出声 / 更新检查失败」时必读**（系统代理与 VPN 是一等嫌疑）；也用于回答"桌宠为什么不自己绕过代理"。 |

> 注：`docs/ISSUE-42-POSIX-COLLISION-IPC-2026-08-31.md`（碰撞选举 / QLocal IPC / 协调者锁）从未入库，
> `AGENTS.md` 曾经指向它的那条 Context pointer 已随 Phase 4.4b 退役（碰撞 IPC / 协调者文件锁
> 整体删除）一并移除——该课题不再存在待读文档。

---

## PR 报告存档

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`PR-REPORT-QT-LIFECYCLE-CRASH-FAMILY-2026-10-04.md`](PR-REPORT-QT-LIFECYCLE-CRASH-FAMILY-2026-10-04.md) | Qt 生命周期崩溃家族根治：根因 = 丢弃未收口的 clip/worker 被 GC 析构撞上共享预取线程的在途 queued 交付（mac .ips 原生栈实锤 qtPythonMetacall 0x8）；修复 = clip/worker 进程级强钉 + 配图加载单 worker 队列化 + 配图缓存进程级共享 + 收口无条件作废在飞批次；CI 两道隔离（mac 拆进程 + 肇事单条 deselect）全摘，v15/v17 两轮三平台同口径全绿。 | 改 `pet/frameseq_clip.py` 生命周期/`pet/overlay_shell.py` 配图加载链、排查「测试套件/进程退出时段错误」、或引用崩溃家族历史时必读。 |
| [`PR-REPORT-FULL-REVIEW-FIXES-2026-10-03.md`](PR-REPORT-FULL-REVIEW-FIXES-2026-10-03.md) | 全量大审修复批次：两家独立审查（astra/ops5.5）27 条发现并集，主代理逐条裁决（修 20/缓 5/否 1/产品决策 1），ds 六批修复 60+ 新回归全部红→绿——冻结根因之外的 20 条：`_idle_trim_timer` 停摆、worker 回收死路、预热竞态倒写、WebM 暂停丢帧、计划时长速率错配、暂停所有权冲突、spawn 暂停继承、restart 对称收口、预热绕闸、碰撞累计写回（手感变化待实机）、隐藏宠退出碰撞、旋转像素命中、SessionWatcher 三层修复（锁屏通知真接入）、退出收口加固、主库 shutdown、配置显式 null、flipped 回退、start 定序、句柄追踪、孤儿终局、limiter fail-closed、slot 原子写、缓存有界、屏迁移两刀。 | 评审/合并本批前必读；改播放暂停/碰撞写回/会话接入/退出收口/屏迁移/持久化任一域，或再遇「冻结/隐形障碍/迁移后位置错」时必读；缓修 5 条（非 Windows 拓扑穿透=发布阻断级）与未验收边界在其中。 |
| [`PR-REPORT-STALE-CLIP-PAUSE-FREEZE-2026-10-03.md`](PR-REPORT-STALE-CLIP-PAUSE-FREEZE-2026-10-03.md) | clip 暂停标记跨绑定滞留致画面永久冻结（拖拽后卡住 / 提起动画变静态图 / 挂机回来三只全冻但还在移动，三症同源）：隐藏期换绑只 stop 旧 clip 不清 `_paused`，恢复只续当前 clip，旧 clip 再绑时 `start()` 看到滞留标记不起定时器 = 冻在首帧；修法 = `bind_clip` 在 sprite 未暂停时补一次幂等 `resume()`（+9 行，0.299µs/次实测），契约持有方对齐下推状态，隐藏期零推进契约不变。 | 改 `pet/pet_sprite.py` 的 bind/restart/pause/resume、`pet/frameseq_clip.py` / `pet/webm_clip.py` 的 start/pause/resume 语义、隐藏/挂起/全屏避让路径，或再遇「动画冻在静态帧但宠物还在动」时必读。 |
| [`PR-REPORT-FRAMESEQ-V3-PIPELINE-2026-09-29.md`](PR-REPORT-FRAMESEQ-V3-PIPELINE-2026-09-29.md) | 帧序列统一档位 v3（`unblend-white-v3`）全量重出与打包部署：106 段全部收敛为 Q70 + 白底反解 v3，档串写进 meta 并成为**采纳条件**（旧三代产物一律不再采纳）；两段式转换（无损中间帧 → 逐帧反解收色 → Q70）与「为什么不让 ffmpeg 一趟做完」；v4 镂空还原**挂起**（实机误切白色鲸鳍）。磁盘 **573.3 → 379.1 MB**（−33.9%）、便携包 zip **716.0 MiB**、部署树内素材经本人现场核验为 106 段全 v3；含与交接记录「721→379MB / 908→716MB」的口径差异（721/908 不可复测，不作证据）。 | 改 `pet/frameseq_provision.py`（档串/采纳闸门/发布回滚/反解链）、`tools/convert_frameseq.py`、或要重出素材世代、判断「为什么某段帧序列没被采纳」、引用素材体积与打包大小之前必读。 |
| [`PR-REPORT-MEMORY-POLLING-BATCH-2026-09-29.md`](PR-REPORT-MEMORY-POLLING-BATCH-2026-09-29.md) | 内存与轮询批次（M1/M2/M3 + N1/N2/N3）：帧序列表**物化**（每段 241 个 Path 常驻）改按编号现推（常驻 Path 240→1、外推 318 clip ≈72.2MB 堆）；右键菜单树按次累积收口（15 轮 QMenu +255/QAction +1170 → **0/0**）；桥目录轮询**真 bug**（按名字序取前 64 → 活会话文件名字序第 74 位永读不到，改 mtime 倒序）；配图缓存过采样 **36.85 → 11.27MB**；meta 缓存死条目体检（33.9ms vs 366ms）——并**纠偏**两处派单前提（字体 87MB 是测量假象、5497 条死条目现场已不存在，均不认领收益）。含整夜 USS 小时均值与子进程=0 的实测口径。 | 改帧表/`FrameSeqClip` 帧路径、右键菜单收口（`release_menu_tree`）、桥目录轮询与 tailer offset、自言自语配图缓存尺寸、meta 缓存加载，或再遇「内存一路涨 / 帧表占几十 MB」时必读。 |
| [`PR-REPORT-GUI-IDLE-JANK-2026-09-29.md`](PR-REPORT-GUI-IDLE-JANK-2026-09-29.md) | GUI 空转与卡顿批次（I3 + F-PERF + G + H）：岛撞速度**无界**与 `_animating()` 空转（连撞 60 击 `_squish_v` 207→≤3.5、`_tilt_v` 109→≤40）、桥内**重复派发**撞击音效（2 次→1 次）、拖岛穿透轮询未降档；慢速爬行白刷重绘（`overlay.update` 251→142）、气泡跟随全量重建（120→0）、目录轮询 stat 风暴（9.761→4.685 ms/轮）；应用级 `eventFilter` 快路径（4.40→0.96µs）与 `PetSprite.rect()` 记忆化（2.30→0.77µs）——**本次补报告独立复算了 py-spy 栈顶占比**（10.75%/4.41%，与记录口径 9.4%/2.1% 并列不合并）；死窗口（C++ 已销毁）帧回调的存活闸（0.079µs/次）。 | 改岛撞击动画/音效派发/拖拽档位、sprite 脏区上报与几何 memo、气泡跟随、目录轮询节流、应用级事件过滤器、overlay 异步投递入口，或排查「GUI 线程空转/掉帧」时必读。 |
| [`PR-REPORT-HARNESS-OWNERSHIP-2026-09-29.md`](PR-REPORT-HARNESS-OWNERSHIP-2026-09-29.md) | node 桥门控收紧与退出归属收口（K1/K2）：自动拉起不再只看 `enable_chat + harness_autostart`，门控加上「DSH 联动真开」（否则白拉一个 41.9MB、无人消费的 node.exe）；退出/联动关闭时按**三重条件**（登记表命中 + 存活 + 终止前重读命令行）杀掉自拉起实例，用户手动起的实例永不误杀；含 Windows 实测「`which dsh` = `%APPDATA%\npm\dsh.CMD` → 直接子进程镜像是 cmd.exe」这一必须多认 `cmd` 的理由、空表收口 3µs/0 系统调用、有实例 0.7–1.0s。 | 改 `pet/harness_launcher.py`、`pet/app.py` 的自启门控/退出路径、Harness 菜单可用性，或再遇「桌宠退出后 node/dsh 还在跑」「联动没开也拉起服务」时必读。 |
| [`PR-REPORT-SHARED-MEDIA-LIBRARY-2026-09-29.md`](PR-REPORT-SHARED-MEDIA-LIBRARY-2026-09-29.md) | 同角色多宠共享只读媒体视图 + 首帧解码跨库复用：为什么**否决**「复用同一个 `MovieLibrary` 实例」（clip 是 QObject + 播放态，共享即串扰），采用 B+（只读数据共享 / 播放态 per-pet / 预热责任单点与交接 / WebM 首帧按媒体身份跨库共享 + 有界等待 2s）；3 库 clip 对象 318→120、帧 0 解码 21→7、源哈希 318→106、预热 spawn 12→4、稳态私有内存 +4.7→+2.35MB。**纠偏**：内存收益是 **1~2MB 级而非 15MB 级**（单 clip 实测 `FrameSeqClip` 5.2KB / `WebMClip` 12.9KB）。 | 改 `pet/library.py` 的建库/预热/`rescan_frameseq`、`pet/webm_clip.py` 首帧缓存与预算、多宠（子宠/切角色）生命周期，或再算「三宠同角色多占多少内存」时必读。 |
| [`PR-REPORT-REFRESHRATE-REWIRE-2026-09-30.md`](PR-REPORT-REFRESHRATE-REWIRE-2026-09-30.md) | 同分辨率切刷新率 tick 不重读（任务A 实锤缺口）：`refreshRateChanged` 接入 overlay 几何信号族（`pet/overlay_window.py` 两处 hunk，与该文件并行会话 WIP 划清归属），60↔165Hz 切换 tick 立即重读；FakeScreen 补信号+先红后绿回归；稳态零开销（+1 空闲连接）；真人手动切换场景未实机复现（诚实登记）。 | 改 `pet/overlay_window.py` 屏信号接线/DPR 几何族、TickDriver 档位依据，或排查「切了刷新率桌宠不变速/空转」时必读。 |
| [`PR-REPORT-FLIGHT-BEH-BATCH-2026-09-27.md`](PR-REPORT-FLIGHT-BEH-BATCH-2026-09-27.md) | 飞行期禁气泡+飞行边沿+落地预热去重（F-BEH）：气泡入口统一读 `_bubble_blocked(sprite)` 按 sprite 门禁（主飞→子宠照常），提醒入队不丢落地再弹、一次性播报飞行期丢弃，预热同飞行只提交一批；门禁亚微秒级/次（0.15-0.66µs 区间实测），红探针 13 failed→14 passed，聚焦族 812 passed；歌词气泡飞行期仍冒=已知残留。 | 改气泡/自言自语/提醒/播报的隐藏判定、飞行状态机、落地预热，或排查「飞行中冒气泡」时必读。 |
| [`PR-REPORT-ISLAND-TOPMOST-2026-09-29.md`](PR-REPORT-ISLAND-TOPMOST-2026-09-29.md) | 灵动岛置顶性丢失修复：岛带 `WS_EX_NOACTIVATE`（`0x08080088`）**从不激活**，一旦被激活过的 topmost 窗盖住，Windows 不给通知 → 永不回位；回归定位到上游 3.1.1 合并（`ae6838a`）删掉了 legacy 的 `_win_set_topmost`/`_topmost_watchdog`，属**上游合并回归**而非 overlay 引入。修法是 `SetWindowPos` 重申（show/点击/本进程焦点/应用 Active + 复用既有 30s 心跳，**不新增定时器**，外部抢前台不重申）。含真桌面真 HWND 的 Z 序前后对照。 | 改灵动岛显隐/Z 序/窗口旗标、`pet/platform_win.py` 的 win32 层、overlay 与岛的层级关系，或再遇「岛被别的窗口盖住再也不上来」时必读（macOS/Linux 未验证）。 |
| [`PR-REPORT-WINDOWS-PARITY-JANK-2026-09-27.md`](PR-REPORT-WINDOWS-PARITY-JANK-2026-09-27.md) | Windows 专项（**功能继承 + 优化收口，最终全量见报告**）：两轮旧版功能继承修复（第一轮 G1–G5：岛碰撞开关门、同屏几何子宠迁移 + 可见气泡与岛墙全局原点、碰撞音量直取配置 + 点击/碰撞门独立 + pack 快照缓存、宠物间总碰撞的 per-sprite/per-slot 资格位与窄刷新、ffmpeg 回收阈值接线；第二轮 B1–B8：岛拖拽、菜单作用对象、app 宿主、动画状态机、音效音源、气泡起止同步、壳接线、per-slot 逐只设置、设置页文案）+ 新架构优化 O1–O5（帧路径 QPixmap 惰性化/帧 0 不重复解码/`mirrored`→`flipped`；隐藏期停穿透与光标轮询 + 穿透态缓存；零可见 sprite→T3、逐只停节拍、`WM_WTSSESSION_CHANGE`/`WM_POWERBROADCAST` 只降档；`DshStateTracker` 按门惰性启动、overlay 下共享全屏 watcher 不启动），含真机 A/B（三宠待机/移动，可见常态收益在噪声内、收益集中在隐藏/锁屏/后台）+ 随机动作/事件转 Q80 帧序列（§十四：R1 帧表懒列消除 90–110MB 膨胀、R2 冷集供给与 GUI 减负，真机三宠 act 首帧 134→18ms、私有内存 −31MB），逐项红→绿真实 rc（不相加成总数）、作废轮与原因、未验证清单与本地 PR 说明草稿。 | 需要「Windows 侧继承收口与优化改了什么、哪些还没验、真机 A/B 到什么口径」时；接着跑最终全量 / 实机验收 / 准备提交之前必读（末尾断点）。**该报告只记到 §十七（09-28 T1/I1/I2）**；09-28 夜之后的批次已另立 [`PR-REPORT-MEMORY-POLLING-BATCH-2026-09-29.md`](PR-REPORT-MEMORY-POLLING-BATCH-2026-09-29.md)（M/N）、[`PR-REPORT-GUI-IDLE-JANK-2026-09-29.md`](PR-REPORT-GUI-IDLE-JANK-2026-09-29.md)（I3/F-PERF/G/H）、[`PR-REPORT-HARNESS-OWNERSHIP-2026-09-29.md`](PR-REPORT-HARNESS-OWNERSHIP-2026-09-29.md)（K1K2）、[`PR-REPORT-SHARED-MEDIA-LIBRARY-2026-09-29.md`](PR-REPORT-SHARED-MEDIA-LIBRARY-2026-09-29.md)、[`PR-REPORT-ISLAND-TOPMOST-2026-09-29.md`](PR-REPORT-ISLAND-TOPMOST-2026-09-29.md)、[`PR-REPORT-FRAMESEQ-V3-PIPELINE-2026-09-29.md`](PR-REPORT-FRAMESEQ-V3-PIPELINE-2026-09-29.md)，**引用「最新状态」时按日期读，均不重写本文**。 |
| [`PR-REPORT-MEMORY-DIET-SINGLE-PET-2026-09-24.md`](PR-REPORT-MEMORY-DIET-SINGLE-PET-2026-09-24.md) | 内存瘦身专项（单宠稳态）：用 tracemalloc 双口径 + 进程内/进程外 WS 采样把「+170MB」拆开——架构差价实为 **+33~43MB**，用户观察的 280-305MB 复现为**3 宠**（330.6MB，依据 `overlay-active-pets.json=[4,5]`），真正在涨的是「已停播 clip 残留解码帧」（单宠 480s 实测 **+6.91MB/min**）。第一刀池级回收把斜率压到 **+0.40MB/min**、稳态均值 214.75→**156.17MB**；第二刀懒加载按配置关闭的功能模块 −7.7MB。含 10 项归属表、逐刀功能等价论证、未采纳方向与全部复现命令。 | 再遇「内存涨/重启后一路涨到 300MB+」、改素材池预热与 clip 队列生命周期、改 `pet.app` 导入图、或需要「这份配置下每 MB 到底是谁的」权威口径时。 |
| [`PR-REPORT-THROWN-ISLAND-PUMP-2026-09-24.md`](PR-REPORT-THROWN-ISLAND-PUMP-2026-09-24.md) | thrown × 灵动岛静态成员碰撞泵能根修：`FLAG_THROWN` 未接线导致每次触岛按 `STATIC_RESTITUTION=1.3` 放大法向速度、落在岛上的 thrown 又永远满足不了 `is_at_rest`（实机「碰撞后画面卡住不动」）；修法是入场一次性弹床 + 抛掷中 ≤1 + 静态支撑落定出口 + 地面段收敛，`repro.py` 54/54 → 0/54，含 legacy 对照与 µs 级 tick 实测。 | 改碰撞冲量/静态成员/抛掷物理（`collision.py`/`sprite_collision.py`/`physics.py`/`sprite_physics.py`）、改灵动岛碰撞语义、或再遇「被击飞的宠物落不了地 / 抛掷 clip 停最后一帧」时。 |
| [`PR-REPORT-SINGLE-OVERLAY-WINDOW-2026-09-24.md`](PR-REPORT-SINGLE-OVERLAY-WINDOW-2026-09-24.md) | 单合成窗新架构产品化总报告：45 提交（overlay 渲染拓扑/交互全 parity/多宠生命周期/帧序列化 B 档/多进程层退役/T5 默认化），30 分钟碰碰车长稳与三宠稳态实测、QUNS 频闪案等六个真缺陷修复、实机交互全记录与已知边界。 | 评审/合并本分支前必读；改 overlay 任何子系统、帧序列化、拓扑分流、或需要「新架构是什么/验证过什么/边界在哪」的权威口径时。 |
| [`PR-REPORT-ARCHITECTURE-FAIR-COMPARISON-2026-09-26.md`](PR-REPORT-ARCHITECTURE-FAIR-COMPARISON-2026-09-26.md) | 新旧架构公平性能对照（基础比较完成，最终候选回归已补验通过）：旧真多进程 `2786c15` vs 新 `fair-movefix-20260926-a`（on）逐场景 B N N B——单宠 idle 整树 CPU 近持平、三宠 idle 整树 CPU −17.2%/RSS −59.8%/进程 6→1/ffmpeg 归零、switch 绘制就绪明显快（非 present）、flight/collision 因物理常量与落定时长不同不可比总 CPU；含三个修复IDLE/MOVE 截尾与音效 warm的机器证据范围、缓存 +155 MB 与并存/LRU 无据、最终候选全量回归通过（3638 passed / 11 skipped / 0 failed），长稳仍有缺口。 | 需要「新版到底赢在哪、哪些还没验」的权威口径时；给出回退建议、判断能否默认交付、或引用单/三宠 idle 与切换数字之前。 |
| [`PR-REPORT-OVERLAY-REGRESSION-FIXES-2026-09-24.md`](PR-REPORT-OVERLAY-REGRESSION-FIXES-2026-09-24.md) | overlay 部署后六类实机回归修复 + 上游 main 同步：切动画闪消失（frameseq 显示槽清空）、头槌误复入（残留倒计时当证据）、岛撞只推不弹（静态成员速度通道缺失）、移动动画匹配层七缺（F1-F7：drag/圈末 re-arm/位移曲线/飞行倍率/计划撤销/开播失败/探头闸门）、self_talk 族气泡接线（含配置热改）、上游 PR #187 合入与三处语义调和。含根因证据链、µs 级开销实测、3415 全量门禁与部署冒烟记录。 | 改 frameseq 首帧/clip 绑定、throw_egg/边缘探头状态机、岛速与碰撞世界静态成员、sprite 行为状态机移动/拖拽/飞行、self_talk 气泡族时必读。 |
| [`PR-REPORT-OVERLAY-REVIEW-FIXES-2026-09-24.md`](PR-REPORT-OVERLAY-REVIEW-FIXES-2026-09-24.md) | DS+GPT 双端全量审查收口（37 条发现全修）：阻断级 B1 岛幽灵速度双闸/M1-M2 圈末 re-arm 序列与帧签名/M3 拖拽阈值语义（含 M4 探头点击拉直复活）、GPT 岛显隐路由与岛撞阈值截断；应修批 M5 六死开关/M6 config 同步/M7-M11/M12 节能三项/M14 逐宠显隐/M15 捕获模式；M16 证据纪律与 B2 显式登记。含未修另案诚实清单（GPT#3 慢帧、M13 哨兵、建议级 16 条）。 | 改 overlay 输入仲裁（按下/拖拽/点击语义）、岛碰撞阈值、config↔sprite 设置同步、tick 节能档位、逐宠显隐、或需要「双端审查还欠什么」口径时必读。 |
| [`PR-REPORT-OVERLAY-MENU-PARITY-2026-09-24.md`](PR-REPORT-OVERLAY-MENU-PARITY-2026-09-24.md) | overlay 右键菜单完整 parity：facade 改为按 `context_menu_template` 分发 legacy/modern 建造器（修「设置说新版、右键出旧版」）、缺失条目逐项补齐（AI 对话/黄金回旋/音乐子菜单/边缘探头/大小/Harness/主动识屏/Agent 联动/模板切换…）+ 顺带修复 `close_on_trigger` 命令不派发（整类条目点不动）；含真实配置树导出、真实 PetWindow 机器比对与性能实测。 | 改 overlay 右键菜单/facade 方法面、`context_menu_template` 分流、菜单条目增删（模板感知）、或再遇「菜单点不动」「新版设置出旧菜单」时；改 overlay 音乐/看屏/黄金回旋宿主时也读。 |
| [`PR-REPORT-PHASE4-4-RETIRE-MULTIPROCESS-2026-09-23.md`](PR-REPORT-PHASE4-4-RETIRE-MULTIPROCESS-2026-09-23.md) | Phase 4.4a/4.4b 多进程多宠退役层（停用 → 删除）报告：T6 清单逐条处置（碰撞 IPC/codec/client、instance_launcher、slot 锁、child_pet_cleanup、岛远端墙、spawn 开关键）、机器化守卫（文件不存在 + 全树零 import）、导入期 A/B 实测、两条拓扑真进程冒烟与既存 frameseq 缺陷。 | 改碰撞/多宠生命周期、`slot_manager`、`multi_window_shared`、runtime 标记、`PET_RENDER_TOPOLOGY` 分流时；或需要「为什么多进程多宠层被删、还剩什么」的权威口径时。 |
| [`PR-REPORT-ISLAND-HIDDEN-CHAT-DEADLOCK-2026-09-23.md`](PR-REPORT-ISLAND-HIDDEN-CHAT-DEADLOCK-2026-09-23.md) | 纯桌宠版岛隐藏死锁修复：无聊天构建 hidden_chat 单击路由回退展开卡片（岛能力开关 + 设置页开关按构建变体隐藏）。 | 改灵动岛单击路由 / hidden_chat 设置 / 打包变体（无 pet.chat）行为时。 |
| [`PR-REPORT-TEMPLATE.md`](PR-REPORT-TEMPLATE.md) | PR 报告模板：三份交付证据（修改文件说明 / 性能分析 / 实机运行记录）的逐节骨架与判定标准。 | **开新 PR 写报告前必读并整份复制**；2026-09-22 起三份证据是硬要求（`AGENTS.md` Delivery evidence discipline），由 `tests/test_pr_report_discipline.py` 机器化校验。 |
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
| [`HANDOVER_2026-09.md`](HANDOVER_2026-09.md) | perf/stage-1 性能+结构线的交付手册（自带历史快照警示，含后续批次更正）。 | 追溯 perf/stage-1 那条线做了什么时；**正文数值已被后续批次更新**，实际以代码与 `_plan/current/` 档案为准。 |
| [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md) | 项目交接文档：主动识屏 + 多 Agent 联动时期的完整工作区状态、设计决策、遗留 TODO。 | 追溯识屏/联动落地时期的状态快照时；基线为 v3.1.1 / 194 passed，与现状差距较大。 |
| [`CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md`](CHANGELOG-DEV-SINCE-v4.1.0-2026-09-09.md) | 自 v4.1.0 以来开发版变更汇总：按合入顺序的主线演进表、性能线/结构线细节，含"实现后被回滚/取代"的口径说明。 | 需要逐 PR 粒度的开发期变更脉络、或核对"某功能是否真的上线"（第六节列了被取代项）时。 |
| [`UPSTREAM-INTEGRATION-2026-08-26.md`](UPSTREAM-INTEGRATION-2026-08-26.md) | 上游合并与新版 UI 收敛记录：合并策略、维护边界（菜单/设置单一路径、两套聊天窗口互不覆盖）与 macOS 验收产物。 | 追溯"为什么只维护新版菜单/设置、经典聊天窗口为何保留"这类维护边界决策时。 |
| [`OPTIMIZATION_CHECKLIST.md`](OPTIMIZATION_CHECKLIST.md) | 性能优化复核清单（自带「历史快照，请勿按现状逐条执行」警示；`--instance`/`PetApp` 已被 `--slot`/`AppShell` 取代）。 | 只作为"当时怎么做性能复核"的模板参考；**不要按现状逐条执行**。 |

---

## 调研与设计稿（尚未进入实现）

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [`plugin-phase-03-worker/PHASE3_PROCESS_PLUGIN_RESEARCH.md`](plugin-phase-03-worker/PHASE3_PROCESS_PLUGIN_RESEARCH.md) | Phase 3 调研/设计稿：把 AI 聊天、Agent 联动、主动识屏等可关功能从主进程拆到独立进程/插件容器的成本与收益。 | 讨论"关闭即不加载"的内存天花板、或考虑把某功能移出主进程之前。 |
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
2. **`BUILD_ARTIFACTS-2026-08-22.md`** — 记录的产物路径（`dist/*.exe`）与哈希对应已不再产生的 onefile 构建；同一文档内的测试基线为 29 passed / 137 passed，与当前测试规模差两个数量级。README 仍引用此路径，属悬空引用。
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

## 插件化与 DLC 分阶段文档

| 阶段 | 目录 | 用途 |
|---|---|---|
| 总路线 | [`plugin-roadmap/`](plugin-roadmap/) | 查看完整阶段路线、依赖和阶段出口。 |
| Phase 1 | [`plugin-phase-01-foundation/`](plugin-phase-01-foundation/) | 修改架构、API、资源 DLC 或 v4→v5 迁移边界前必读。 |
| Phase 2 | [`plugin-phase-02-runtime/`](plugin-phase-02-runtime/) | 实现 Core 插件运行时前必读。 |
| Phase 3 | [`plugin-phase-03-worker/`](plugin-phase-03-worker/) | 迁移网络、Agent、视觉和外部程序前必读。 |
| Phase 4 | [`plugin-phase-04-updates/`](plugin-phase-04-updates/) | 实现 DLC 安装、更新、校验和回滚前必读。 |
| Phase 5 | [`plugin-phase-05-distribution/`](plugin-phase-05-distribution/) | 增加本地、GitHub、CDN 或 Workshop 来源前必读。 |
| Phase 6 | [`plugin-phase-06-ecosystem/`](plugin-phase-06-ecosystem/) | 开放第三方插件 SDK、签名和社区 catalog 前必读。 |
| Phase 7 | [`plugin-phase-07-release/`](plugin-phase-07-release/) | 做跨平台正式发布和生态运维前必读。 |

## 在线更新与网络发布

| 文档 | 一句话内容 | 何时必读 |
|---|---|---|
| [ONLINE-UPDATE.md](ONLINE-UPDATE.md) | Core 安装包在线更新协议：多源 manifest、真实二进制镜像、大小/SHA-256 校验、Windows Inno Setup 自动安装和回滚边界。 | **改自动更新、更新设置页、发布 manifest、下载校验或安装器重启行为前必读**；网络出口问题另读 [NETWORK-PROXY-AND-VPN-2026-09-22.md](NETWORK-PROXY-AND-VPN-2026-09-22.md)。 |
