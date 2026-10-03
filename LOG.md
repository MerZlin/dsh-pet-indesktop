# 项目变更日志

## Phase 4B-1 唯一安装状态与安全恢复

2026-09-30：基线 ce16484，完成测试优先的状态校验、revision/操作幂等、真实内核锁、状态写入中断恢复及签名描述解析。初始可收集的行为测试 24 failed，最终新增 86 项状态测试；相关 216 passed / 1 skipped，全量 **3547 passed / 12 skipped / 13 warnings（343.74s）**；进程族连续三次各 10 passed。静态门通过，命令及警告差异见 [本轮报告](docs/PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md)。

50 条收据读取中位 38.119ms，50 次提交中位 67.219ms；不允许后续 GUI 高频同步读取。状态摘要不代替签名，描述不是租约，不从旧备份复活卸载状态。四份记录持续更新；不接生产启动、不实现版本租约/安装器/UI，不改变当前菜单和识屏，不提交、不推送。人工识屏、安全存储与托盘退出仍未验收。

本文件只记录已经发生并完成验证的工程、架构和文档变更。路线规划请看 [`SPEC.md`](SPEC.md) 与 [`docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md`](docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)；详细交付证据请看 [`docs/INDEX.md`](docs/INDEX.md) 中的 PR 报告。

## 2026-09-30

### 协作规范与成果同步

- 已按本次授权写入[详细协作模板](docs/agents/planning-and-reporting.md)，同步 `AGENTS.md`、交接/事项记录规范与 `SPEC.md`；计划和汇报采用正式工程正文，末尾解释实际使用效果及限制。
- 已落盘本任务[设计与验收](docs/agents/WORKFLOW-STANDARDIZATION-2026-09-30.md)、[PLAN](.scratch/workflow-standardization/PLAN.md)、[HANDOFF](.scratch/workflow-standardization/HANDOFF.md)、[STATUS](.scratch/workflow-standardization/STATUS.md)，并补齐 [Phase 4B STATUS](.scratch/phase4b-local-management/STATUS.md)。完成后保留最终记录；不纳入原始日志、缓存、构建产物或密钥。
- 起点 `e0edc89`、远程跟踪 `9834612`，五个待推送提交累计 171 个路径；逐提交新增内容的有限敏感模式/生成物路径检查未发现候选，不将此称为完整安全审计。HTML 已跟踪且与远程同版本；自动更新及其他保护文件无本轮修改。
- 本次只固化规范和同步成果，不实施 Phase 4B、不改运行时/测试/构建，也不新增手测结论。不开子智能体；本次提交及推送授权不自动延续到后续任务。
- 本轮新验证：全量 **3459 passed / 12 skipped / 14 warnings，323.85 秒**；skip/warning 数量与继承基线一致，均为现有 Qt 弃用提示。Ruff 通过、格式 460 文件、mypy 59 文件、文档 113 份、报告/产品文案 44 passed；任务记录互链另检通过。
- 九文件 Qt/Worker 组合在 20 个低优先级负载进程下 **3 × 65 passed**（20.31/20.84/20.81 秒），各轮平均 CPU 100%；负载进程已全部回收。不是长期 soak，不代替冻结构建或人工手测。
- 规范已独立提交 `a2779cbeeea84a26d92f58391e59494555d3475f`：15 份 Markdown，+441/-26；九项保护文件摘要不变。提交前末次文档 113 份及报告/文案 44 passed（0.71s），六份任务文本 24 个目标通过。本条由随后独立记录 `beba389eaace3d0b5ffa6b56c618845368475047` 回填，不改写原提交。
- 2026-09-30 02:21:35 UTC+08:00：刷新远程后无分叉，正常推送 `9834612..beba389`；本地 HEAD、远程跟踪与 `ls-remote` 均核验为 `beba389eaace3d0b5ffa6b56c618845368475047`，HTML blob 一致，工作树干净。五个既有成果与两份规范/记录提交均已同步。未创建 PR、未合并主分支、不宣称 CI 或人工验收完成；W01–W08 完成，Phase 4B 仍未实施。最终记录提交自身的同步在提交后再核验，不递归预填 SHA。

### Phase 4B 计划落盘与本地备份检查点

- 已落盘[本地管理设计](docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)、[任务清单](.scratch/phase4b-local-management/PLAN.md)及[交接记录](.scratch/phase4b-local-management/HANDOFF.md)。仅完成规划记录；4B-1 状态层尚未写测试或实现，不能认为安装/卸载闭环已完成。
- 固定同一数据根统一安装/整包启停/卸载、版本占用时待用户自然退出；状态 → 租约 → 可恢复事务 → 管理 UI → 两种冻结产物完整使用链，前门通过后再下一步。
- 用户单独授权把截至目前所有修改做本地备份；保留 Phase 4A 全部成果，不推送，不混入构建、日志、临时密钥或缓存。保护文件及本轮增量按保存的 146 文件基线检查。
- 2026-09-29 完成本次备份检查，2026-09-30 续写并核对源码未变：全量 **3459 passed / 12 skipped / 14 warnings（321.65 秒）**，相关专项 **108 passed**，Ruff/格式 **460 文件**、mypy **59 文件**、文档 **111 份**通过。首轮仅新增设计的分支名称触发产品文案扫描；调整元数据归属后完整复跑通过，不修改测试规则。详细命令和失败记录见交接。
- 修正审计中指向未入库生成 `.spec` 的链接，改为实际构建入口；候选文档相对链接核对包含 337 个目标，保护文件 9 项摘要保持一致。本地备份提交 `5f04a99` 已完成（149 个文件，16922 行新增、3843 行删除），未推送；另以独立文档提交回填检查点，不改写历史。不以这些源码验证代替冻结构建或当前版本用户手测。


## 2026-09-29

### Phase 4A host 与独立构建

- 第四步已有实现与 Windows 自动化基线：识屏权威代码迁入 `features/screen_understanding/{host,common,worker}`，Core 提供受限状态/配置/数据/凭据及展示端口；旧路径为兼容委托。聊天侧拥有 `chat.external-turn/v1` 的会话保存、刷新和去重。
- 官方目录包使用 cryptography/Ed25519、完整摘要清单、固定 factory、版本隔离命名空间及进程内版本使用句柄。验证版只注入测试信任锚；不生成正式密钥、不新增安装状态，不把 Python host 当强沙箱。
- 两种真实无识屏 Core（含聊天/无聊天）和自包含 Worker 验证构建已通过 10/10 冻结场景、Qt DLL 链、独立依赖及合成图像/本地 HTTP 请求。原默认构建不替换；无用户屏幕、真实密钥或付费模型请求。
- 组合测试曾有 Qt 原生崩溃：定位到 QProcess 信号闭包强引用 Supervisor，已用回归锁定、弱引用及当前进程校验修复；修复后九文件组合三次各 65 passed，全量 **3457 passed、12 skipped、13 warnings / 367.80s**。早期失败日志保留，不以偶然通过隐藏问题。
- 新增本步报告后的最终复验 **3459 passed、12 skipped、14 warnings / 348.90s**（退出码 0）；新增 2 项为报告纪律参数用例，Qt 弃用提示的 window 计数增加 1，未新增过滤。文档链接 110 份和报告纪律 43 项通过；最终输出已留档。
- Ruff lint/format（460 文件）及 59 文件 mypy 通过；包体、启动/RSS、请求/退出耗时与逐文件增量见[实施报告](docs/PR-REPORT-SCREEN-HOST-BUILD-2026-09-29.md)。构建门只覆盖本机 Windows；当前版本用户手测、自动识屏及托盘退出、其他平台和正式发布仍未验收。
- 下一步 Phase 4B 才处理权威安装状态、跨进程版本占用、导入/卸载/升级回滚。保留前三步未提交修改；不开子智能体、不提交、不推送，不改自动更新和演示 HTML。

## 2026-09-28

### Phase 4A 菜单与设置贡献

- 增加 owner/scope 贡献和唯一命令注册身份、事务回滚、内存功能状态及执行租约。屏幕理解接管现代/legacy 菜单与独立视觉/自动策略组件；保留布局和旧入口，不新增托盘项或整包停用按钮。
- 安全设置在停用时保留草稿，正常移除支持保存/放弃/取消；故障留非敏感只读草稿、清密码并撤销搜索/深链/保存与定时任务。真实窗口和独立设置子进程覆盖禁用、旧动作、共享执行及清理。
- 首轮全量暴露本轮两个回归（窗口规模门、旧测试缺请求代数），已通过机械提取入口及补齐旧测试修复；相关专项 40 passed、1 skipped。最终全量 **3257 passed、11 skipped、13 warnings（502.62s）**；Ruff/格式 403 文件及 mypy 45 文件通过，有界注册/菜单/设置残留为零，详见[实施报告](docs/PR-REPORT-SCREEN-CONTRIBUTIONS-2026-09-28.md)。不放宽架构门，不改 Worker 协议或自动更新。
- 当前配置及贡献版本尚未用户手测；原生 Qt 不显示窗口的组件探针不是完整人工验收。本步不提供真实可卸载 DLC，保留既有未提交成果；未提交、未推送。

## 2026-09-27

### Phase 4A 独立视觉配置与确认迁移

- 实现独立 `VisionProfile/Settings/RequestConfig`、每实例配置、作用域 keyring 端口与确认迁移；自动/手动分别绑定，未确认或无安全凭据时保持待配置，不借聊天 Key。当前 Worker 和进程内回滚都只使用独立配置。
- 迁移先脱敏预览，确认后备份目标非敏感状态、复制到新凭据作用域、验证并锁内重读提交；阶段恢复只清理未使用引用，不删聊天凭据或覆盖后来编辑。Core 普通保存保护磁盘上较新命名空间；真实双进程竞争/陈旧保存测试通过。
- 自动化域增加独立屏幕理解组件，旧聊天视觉页改为说明/跳转；无聊天可打开并保存，未配置手动入口引导设置。保留自动关闭仍可手动、气泡先呈现、聊天可选同步和原多窗口路由；配置变化使旧结果失效。
- 源码执行路径解除聊天 Provider/HTTP 辅助依赖，只提取共用基础 HTTP 函数并保留聊天兼容导出。真实新子进程阻断 `pet.chat`，Qt 设置/窗口/请求及 Worker 使用替身截图、凭据和本地 HTTP 服务通过；不是实际用户屏幕或收费模型验收。
- 最终全量 **3224 passed、11 skipped、14 warnings，300.50s**；核心类型检查 **11 files success**。首次全量的 10 个失败按新引入兼容/测试问题与一次不可重复的退出探针时序观察记录，窄修后完整重跑；未放宽架构行数门或 skip。报告新增后的文档纪律另验，不把旧通过数量当成新增报告已通过。
- 有界实测：500 次已配置解析均值 **0.3614ms**，25 次确认迁移均值 **55.115ms**；使用 MemoryVault，不代表系统 keyring 性能。真实 Qt offscreen 检查浅/深主题、720/1100 宽及 1.3 字号，共 25 张状态图，未作可见桌面验收。详见[本步报告](docs/PR-REPORT-VISION-CONFIG-2026-09-27.md)。
- 报告沿用本轮日期 2026-09-27，测试日志机器本地时间为 2026-09-28（Asia/Shanghai）。未访问用户真实 Key、截图或外部模型，未长期 soak/重建/提交/推送。既有平台查询/文档改动保留；真实安全存储、新设置用户操作、自动识屏及托盘退出仍待验收。
- 本轮完成配置与凭据独立，不是可卸载 DLC；下一步按原顺序落实菜单/设置贡献与可选聊天服务，再推进独立构建。

### Phase 4A 通用平台查询与兼容适配

- 新增 stdlib-only `DesktopQueryPort`、不可变结果及 Windows 后端；旧 `vision` 查询入口委托，返回字段、光标字符串、默认值和注入边界不变。非 Windows 明确不支持，不假装完成原生实现。
- 基础全屏、单窗口/共享光标监视和设置白名单改用通用查询，不动截图、网络、Worker、配置、触发策略、自动更新和打包。新接口区分失败与零空闲，旧接口仍以 `0.0` 降级。
- 测试先确认缺少新模块及禁止导入识屏的失败，再实现；真实 Qt 新子进程阻断 `pet.vision`，普通/共享窗口及监视清理通过。新增 54 个行为用例，确定性前台和光标旧断言保留。
- 专项 **110 passed**；相关域 **435 passed、1 skipped**；实现完成后全量 **3177 passed、11 skipped、13 warnings（308.84s）**。中途新引入的遗留 `Path` 导入缺失和设置文件行数超限已窄修，并完整复跑；未修改门槛。
- 真实前台探针退出码 **0**、`foreground_verified`，无截图/抢焦点/联网。每查询 20 次预热 + 500 次计时的有界样本和额外分配样本记录在[实施报告](docs/PR-REPORT-DESKTOP-QUERY-2026-09-27.md)；没有长期 soak，Qt offscreen 不是可见桌面验收。
- 设计、阶段入口及索引登记首步完成，其余交付接口仍待实现；报告保留逐文件增量与最终静态/文档门结果。用户手测事实不变：手动正常；自动、停用和托盘退出未验收，不判故障。
- 保留本轮开始时脏工作树，不提交、不推送、不重建。此步只清除基础查询依赖，不宣称最小 Core 产物或完整可卸载 DLC 已完成。

- 最终静态/文档门：Ruff lint 与 377 文件格式检查、单模块 mypy/编译通过；107 份文档链接、37 项报告纪律与产品文案扫描通过。852 文件基线复核仅本轮 18 文件有增量，暂存区为空，无提交/推送。

### Phase 4A 最小拆包设计与可选聊天联动

- 新增 [屏幕理解最小拆包设计](docs/plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)，落实五组待实现合同：纯平台查询、独立视觉配置与安全凭据、owner 贡献、定向聊天外部问答、自包含 Worker 与可信加载。完成的是设计，不是代码迁移或可卸载交付。
- 识屏与聊天可独立安装配置；同时启用时交换完整分析文字，聊天包负责会话和持久化，Core 只发现/授权/路由。保留无窗口可保存、生成中跳过、手动来源窗口和共享自动多目标的语义；明确幂等消费、接收失败不影响气泡、不传截图/Key、不排队补发。
- 自动/手动旧视觉行为分别解析，经脱敏预览与用户确认一次性迁入独立 profile/凭据，之后不跟随聊天配置；文件与 keyring 分步恢复，不明文备份或删除聊天源凭据。这里只规定流程，未读取或迁移用户密钥。
- 屏幕包暂定 `official.screen-understanding`，同版 host + 自包含 Worker，应用数据根下 `state.json` 管理安装状态，版本租约协调多实例；正式代码执行前验证发布者签名及文件清单。独立交付后重启耗尽即暂停/重试/回滚，不回 Core 截图或请求网络；当前 3B fallback 代码保持原样。
- 同步 API、迁移、更新协议、Phase 4 入口、文档索引及 SPEC；不重写原拆包审计或历史封存报告。实现细节、真实独立构建、装卸和跨平台验证仍未完成。
- 本轮验证：`python scripts/check_docs.py` **106 份文件通过**；`python -m pytest -q tests/test_pr_report_discipline.py` **35 passed（1.01s）**；产品文案边界专项 **1 passed、94 deselected（0.61s）**；`git diff --check` 通过。纯文档改动，未重跑全量运行时测试、mypy 或冻结构建，不将历史全绿当成本轮结果。
- 工作区按本轮开始时的 851 文件 SHA-256 基线保护，只新增设计并更新 8 份范围内文档；保留既有测试边界/审计/阶段文档改动。运行时代码、测试、打包、自动更新及演示 HTML 未改，无截图、模型请求、暂存、提交或推送。
- 人工验收仍仅有手动“看看屏幕”正常；自动识屏未继续等待、尚未验收，不判定故障；停用与托盘退出仍待人工验收。下一步依设计先实施纯平台查询接缝，不直接开始整包搬迁。

### 前台窗口测试边界与 Phase 4A 只读审计

- 完成测试边界修复：移出 1 个依赖真实前台的默认回归，新增 44 个确定性用例，直接执行 `foreground_window_info()` 函数体，只替换 WinAPI 边界；历史局部 `ctypes` 导入遮蔽的内存变异被严格成功断言捕获，未改变生产实现。
- 新增 [显式桌面探针](scripts/verify_foreground_window.py)：0 为通过、2 为环境未就绪（不是通过）、1 为真实失败；最多采样 3 次，不抢焦点、截图、联网或输出私人窗口上下文。本机 N=1 返回 `foreground_verified`、退出码 0、0.5724s，只证明当次窗口查询有效，不外推到自动识屏或退出验收。
- 相关专项 **174 passed、1 skipped（36.62s）**；全量 **3123 passed、11 skipped、13 warnings（756.38s）**，无失败及 Qt 原生崩溃。总数净增 43，skip/warnings 与上一轮失败记录一致；保留旧失败证据，不新增长期 soak。
- Ruff、372 文件格式检查和新增脚本/测试编译通过；105 份 Markdown 链接通过；PR 纪律与产品文案边界 **130 passed（38.67s）**。
- 新增 [Phase 4A 只读拆包审计](docs/plugin-phase-04-updates/PHASE4A-SCREEN-DELIVERY-AUDIT.md)：Core 全屏/光标仍复用视觉模块系统查询，识屏入口/配置/凭据仍依赖聊天，菜单与安装所有权尚未闭环。现有完整包 468/468 构建输入匹配，不能当最小 Core 证据；本轮没有重建或重复 frozen smoke。
- 在 [3B 收尾报告 §10](docs/plugin-phase-03-worker/PHASE3B-STABILITY-CLOSEOUT.md#10-前台窗口测试边界修复与验收澄清) 追加本次证据，并同步阶段 README、INDEX 和日志索引。Phase 4A 只完成审计输入，不宣称已完成拆包设计或可卸载样板。
- 人工事实澄清：手动“看看屏幕”已确认正常；自动识屏因用户未继续等待而尚未验收，不据此判定故障；停用和托盘退出未测试。即使全量恢复通过，Phase 3B 仍不能宣布全部封存。
- 边界：848 个原受控文件哈希核对后，仅 7 个范围内文件变化、另新增 3 个文件；`git diff --check` 通过。运行时代码、协议、打包配置、自动更新及演示 HTML 未改变；未截图、请求真实模型、移动/删除文档、暂存、提交或推送。

### Phase 3B 收尾与 Phase 3C 风险评估

- 本轮完成已有实现复验、重新构建和本地风险评估；**3B 尚未全部封存**。用户“识屏正常”反馈保留，自动/手动覆盖、停用后手动入口及托盘自然退出细项仍待确认。
- 组合测试连续 3 次均为 340 passed、1 skipped（32.45s / 35.17s / 35.67s）；不是长期 soak。Ruff、370 文件格式检查、11 文件 mypy 通过。
- 全量复验为 **1 failed、3079 passed、11 skipped、13 warnings，373.48s**。失败为旧 `foreground_window_info_real_call_no_shadow_bug` 对真实可见前台的假设；单项也失败。WinAPI 与函数跟踪确认 HWND 存在但不可见，函数按现有合同返回 None，无 shadow 异常。归类环境前提/既有测试依赖，未改生产代码或 skip；全绿门保留待复验。
- 原流程重建 `webm-chat`，468 个构建输入前后哈希一致；Qt DLL 链、3A/3B frozen smoke 通过。构建窗口出现与强制清理不冒充托盘退出；N=1 frozen 启动 551.54ms、观察 15.72ms、关闭 87.67ms、RSS 53,026,816 bytes，约 1 秒空闲 CPU 0s。并发验证不用于性能优劣比较。
- 新增 [3B 收尾报告](docs/plugin-phase-03-worker/PHASE3B-STABILITY-CLOSEOUT.md) 与 [3C 风险评估](docs/plugin-phase-03-worker/PHASE3C-ISOLATION-ASSESSMENT.md)，同步阶段 README、INDEX 和日志索引。AI 流式执行优先审计；屏幕理解与聊天共享依赖交给 4A；其余功能按风险决定，不以 Worker 数量推进。
- 本轮未修改生产代码/测试/构建逻辑、旧实施报告、自动更新或演示 HTML；不擅自截图、请求真实模型/账户或增长期 soak。原有实现、路线修订和本次记录按独立本地提交保存，不推送。
- 本地独立提交：`1d60b89` 保存实现检查点（25 文件，+3877/-70），`2e02067` 保存既有选装路线（21 文档，+1198/-746）；本条与收尾/3C 文档另行独立保存。未推送，不以提交名称关闭失败门。
- 文档门：104 份 Markdown 链接通过；PR 纪律及产品文案边界为 36 passed、94 deselected；diff 检查通过。前两次暂存树分别对 100 / 102 份 Markdown 独立检查，不依赖后续未提交文件。全量环境门、人工细项、macOS/Linux 实机、其他变体与可卸载功能包仍是明确限制。

### 官方功能可选交付路线统一

- 状态：本轮路线文档修订与文档门验证完成；没有实现新的安装、卸载或菜单体验。
- 新增[功能交付总表](docs/plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)，登记 Core、资源和 11 个官方功能领域的主要归属、运行位置、依赖、入口、配置/数据及可拔除验收；11 个领域不是 11 个安装包或 Worker。
- 修订总路线与 Phase 1–7 现行正文：保留资源/运行时和 Worker 基线，Phase 4A/4B 先交付屏幕理解可拔除样板，Phase 5A 完成 Setup/ZIP/显式便携选装，5B 以 AI 对话与文件理解为下一主要目标，再推进 Agent 及其余领域。第三方生态条件化，不再条件化官方选装。
- 对齐菜单/设置/搜索/命令/快捷键按包注册与撤销、可执行包来源信任、统一安装状态、默认保留用户数据、迁移恢复、单次临时凭据及卸载不触发隐藏 fallback；补齐各阶段面向使用者的效果说明。
- 修改范围共 21 份文档（新增 1 份、修改 20 份既有文档）；同步 SPEC、INDEX、grill 和日志索引。grill 的 Q1–Q5 决策表保持原文，新一轮功能与 AI 安排列为追加说明；不改写历史 PR 报告或稳定性封存证据。
- 验证：`python -X utf8 scripts/check_docs.py` 通过（102 份 Markdown）；`python -m pytest -q tests/test_pr_report_discipline.py` 为 35 passed；`python -m pytest -q tests/test_desktop_pet_features.py -k product_copy_has_no_external_brand_reference` 为 1 passed、94 deselected；`git diff --check` 通过。纯文档修订未运行全量运行时测试、构建或新的 soak，不据此声明运行时全绿。
- 保护检查：范围外基线文件的存在性与 SHA-256 全部一致，只新增允许的功能总表；无文件移动或删除。既有 Phase 3B 代码/测试/未跟踪文件、README、自动更新、演示 HTML 和历史证据均保留；HEAD 与暂存区哈希未变，未暂存、提交或推送。
- 尚待实施：可信包格式/最终 ID、受控加载、Worker 产物与依赖归属、普通/便携目录和标记由 Phase 4A/5A 验证后确定；当前文档统一的是目标与验收合同，不是已交付的功能包。

### 插件化中期 grill 对齐

- 完成两轮讨论的决策记录：[中期对齐](docs/grill-2026-09-27-插件化中期对齐.md)。Q1 保留运行边界与可拔除交付双线；Q2 将官方选装设为硬目标；Q3–Q5 采用“屏幕理解”样板、Core Setup + 旁置本地官方包、ZIP 显式便携模式。
- 一并记录正文共识：Worker 不等于 DLC、宿主侧功能代码也需审计归属、菜单/设置按包状态注册与撤销、卸载不得隐式 fallback、默认保留配置，以及第三方生态/Workshop 条件化边界。未冻结的包格式、路径、UI 布局和多实例卸载规则明确列为待设计。
- 同步 `SPEC.md`、总路线图、`docs/INDEX.md` 和本日志索引的简短补充；保留旧阶段正文和已有工作树改动，不把目标写成实现状态。用户反馈识屏手动测试无误，单独标注为用户报告，不替代新增交付门。
- 验证：`python -X utf8 scripts/check_docs.py` 通过（101 个 Markdown 文件）；报告纪律与产品文案边界检查共 `36 passed in 0.68s`；`git diff --check` 通过。24 个范围外既有改动文件及自动更新/HTML 共 3 个显式保护文件的 SHA-256 与本轮基线一致，HEAD 未变、暂存区为空。未运行全量测试或构建，因为本轮仅改变文档，未改变运行时、测试或打包逻辑。
- 范围：仅文档记录；未提交、未推送，未修改自动更新、演示 HTML 或 Phase 3B 代码。下一步先设计可拔除样板的包与注册合同，再按确认后的计划实现。


## 2026-09-24

### Phase 2 Core 插件运行时封存

- 提交：`9d294ea feat(plugins): 完成 Phase 2 Core 插件运行时`。
- 完成 `PluginRegistry`、`PluginContext`、`CoreEventBus`、插件配置命名空间、capability 隔离和官方 `official.festival-reminder` 进程内插件。
- 验证：Phase 2 重点回归 162 passed；PR 报告纪律 29 passed；此前全量回归 2990 passed、11 skipped。
- 限制：当前仅完成 Windows offscreen 运行验证，真实可见桌面和 macOS/Linux 实机记录仍待后续交付。

### 测试分类与覆盖率开发基线

- 提交：`db454d9 test: 建立行为分类与覆盖率开发依赖`。
- 为 pytest 增加 `unit`、`integration`、`e2e`、`slow`、`platform`、`external`、`network` 标记，并通过收集钩子覆盖已审查的高风险测试族。未审查的历史测试保持默认全量执行但暂不强行归类。
- 新增 `requirements-dev.txt`，将覆盖率和后续静态检查工具纳入可复现开发依赖。
- 验证：`-m unit` 266 passed；Phase 1/Phase 2/节日提醒相关测试 144 passed；Ruff F 检查通过。

### 工程规范化执行计划

- 当前规范化阶段不实现 Worker 插件，不改自动更新协议，不删除或移动历史文档。
- 后续按独立提交推进：四大工程文档、Markdown 链接检查、Ruff E/F/I 与格式、mypy、pre-commit/一键验证、CI 矩阵和缓存。

### 工程规范化工具链与统一质量门禁

- 已完成并封存 Ruff E4/E7/E9/F/I、全仓库格式检查、mypy 核心边界、Markdown 链接检查、`pre-commit`、跨平台 `scripts/check.py` 入口和 CI 质量/桌面矩阵；对应主题提交见 `e72bc8c`、`46e0218`、`1645276`、`3adc1c4`、`69f41c6`、`e78f1d1`、`382ca24`、`e8e7ed8`、`a9563f2`、`97e1f4a`。
- `db4e224 fix(tooling): 合并隔离测试覆盖率门禁` 修正完整门禁：主套件、WebM 时序族和低优先级预热族分别运行，使用 `coverage --append` 合并后再执行 `83%` 阈值；不再因为隔离测试未计入而误报 `82%`。
- 质量矩阵实测：`python scripts/check.py --quality` 在 Windows/Python 3.11 本机通过，`268 passed, 2738 deselected`，耗时 `14.75s`；完整门禁通过，主套件 `2937 passed, 11 skipped`、WebM `31 passed`、低优先级预热 `27 passed`，合并覆盖率 `40117` statements / `83%`。
- 未改变公共 CLI、Qt 运行时或 Core 自动更新协议；`pet/updater.py`、`pet/update_settings.py` 无差异。`plugin-roadmap-demo.html` 仍为未跟踪文件，旧文档没有删除或移动。
- 限制：本轮只完成 Windows offscreen 与本机工具链验证；可见桌面交互以及 macOS/Linux 实机记录仍需在对应环境完成。
- 后续文档修正：将 README 开发者章节中的 Python 3.10 旧说明统一为当前支持范围 Python 3.11–3.13。

## DLC 基线评审对齐与路线加固（2026-10-02）

### 当前判断

外部文件 `D:\DELL\Documents\QQ\DLC基线评审意见-完整版.md` 对旧快照 `3257e25f365d479dec14310e4c4f8b4298169614` 的静态评审已完成分类。Phase 3 Worker、官方选装方向、禁止任意 Python host 等问题已经被当前路线修正；资源安装后 Registry 解析、冲突清理、Starter/Core 更新保护、Windows 事务和正式信任根仍未封存。

### 本轮变更

只修订路线和记录文档，新增[DLC 基线评审响应](docs/plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md)，并将 4B 调整为 4B-1 状态账本 → 4B-1.5 资源硬门 → 4B-2 租约 → 4B-3 事务 → 4B-4 管理 UI。未修改 `pet/`、`tests/`、`packaging/`、自动更新实现或演示 HTML。

### 验证状态

本条写入时文档检查尚待执行；不得把评审静态结论写成运行时复现。后续按 `.scratch/dlc-baseline-review-remediation/PLAN.md` 的命令补录实际结果。

## Phase 4B-1.5 资源 DLC 硬门（2026-10-02）

本条记录当前工作树对资源 DLC P0 硬门的实施结果，承接外部基线评审和已完成的 Phase 4B-1 状态账本。资源代码补齐了规范 package root/Registry 解析、目录与 ZIP 安装后的实际资源路径回归、冲突安装 operation-owned 清理、Starter/Core fallback 保护、发布内容与派生 cache 隔离，以及资源侧卸载中断恢复。新增 `tests/test_content_dlc_hard_gates.py`，专项相关回归 43 passed，硬门族连续三次各 28 passed；最终文档编辑前全量为 3562 passed、12 skipped、13 warnings。Ruff、格式检查和 `mypy pet/content` 通过。

当前结论是“代码、自动化、文档与保护门通过；本地封存就绪，尚未提交或推送”，不是资源接口已公开稳定。当前版本资源包人工安装/重启播放、可见桌面和托盘操作仍未验收；4B-2 跨进程租约、4B-3 通用事务、4B-4 管理 UI、4B-5 两种冻结 Core 流程均未开始。文档、报告和保护门复核完成后，等待单独提交授权；不提交、不推送，不修改自动更新或演示 HTML。

详细设计见 [Phase 4B-1.5 资源硬门设计](docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md)，实施证据见 [PR 报告](docs/PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md)，任务停点见 `.scratch/phase4b-1-5-resource-hard-gate/STATUS.md`。

### 完成后的实际使用效果

用户界面暂时没有变化，也不能在桌宠中安装、停用或卸载资源。后续资源包安装会更可靠地走到实际资源索引和加载路径，冲突不会误删旧版本；但资源 DLC 尚未对外开放，管理闭环仍需 4B-2 至 4B-5。

## 文档连续性、项目入口与开发者 README（2026-10-02）

本轮补齐了持续任务的施工记录与跨对话总结规则，新增 [`docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md`](docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md)，新增 [`docs/PROJECT-ENTRY.md`](docs/PROJECT-ENTRY.md)，并将根 [`README.md`](README.md) 重写为面向开发者的上手文档。`AGENTS.md`、计划/汇报规范和交接规范同步明确：本地 Git 提交可作为阶段备份，远程推送必须获得当前任务的明确授权，默认不开子智能体。

同时为 Phase 4B-1.5 和 Phase 4B 补齐 `WORKLOG.md`、`SUMMARY.md`，并更新索引与日志入口。4B-1 状态账本保持已完成；4B-1.5 具备代码、自动化、文档和保护门的本地封存基础；4B-2 尚未开始。上述事实不把资源接口宣传为公开稳定，也不把 Worker 或 host 说成已经可卸载。

本轮只修改文档、规则和任务记录；没有修改运行时代码、测试实现、打包脚本、自动更新文件或演示 HTML。文档门已通过：Markdown 链接检查扫描 121 份文件，PR 报告纪律测试 47 passed，`git diff --check` 通过（仅有 CRLF 转换提示）；保护文件无差异。因本轮只涉及文档、规则和任务记录，未重新运行全量运行时测试；当前未创建提交、未推送。

### 完成后的实际使用效果

新对话可从 `docs/PROJECT-ENTRY.md` 逐层了解项目，再从当前任务的 `STATUS.md`、`PLAN.md`、`HANDOFF.md` 和 `SUMMARY.md` 继续；开发者可从 README 运行和验证项目。桌宠界面、安装、停用和卸载行为没有变化，Phase 4B-2 仍未开始。

## Phase 4B-2 跨进程版本租约（2026-10-03）

本条记录当前工作树对 Phase 4B-2 的内部实现和验收结果。Phase 4B-1.5 已作为内部门通过，但资源包人工安装、重启播放、可见桌面/托盘行为和公开稳定 API 仍保持 pending；这不等于资源 DLC 已公开。本阶段新增每个 Feature 版本的跨进程 OS 租约目录、独立 `leases.lock`/版本租约锁、诊断记录、状态 revision/digest 复核和保守 `occupied`/`pending_confirmation` 判定；不使用 PID、TTL、heartbeat 或时间戳单独证明释放，也不支持 Python hot-unload。

Host 在进入已验证 Python interpreter 后保留版本租约；Standalone Settings 通过注入 seam 在对话框存活期间保留 settings lease；Worker 由父进程 reservation 交接到子进程 child lease，父进程只在 JSONL `lease_claimed` 确认后释放 reservation。子进程 bootstrap 仍不导入 UI、`pet.app` 或 `pet.plugins`。显式目录验证路径保持 validation-only 的进程内行为，没有新增安装器 UI、远程目录、公开 SDK 或任意第三方 Python 入口。

自动化证据包括真实 multiprocessing、真实 `QProcess`、Qt event loop、Windows/POSIX 锁路径和旧选择拒绝测试；当前受影响聚焦套件为 **270 passed / 1 skipped（26.64s）**，同一族高负载连续三次均为 **270 passed / 1 skipped**（PowerShell 27.972s、27.921s、26.350s），按项目规定的 offscreen 全量为 **3581 passed / 12 skipped / 13 warnings（479.78s）**。`ruff check`、`ruff format --check`（439 files）和受影响实现模块定向 `mypy`（14 个文件）通过；`mypy pet tests` 仍有仓库既有基线 `1796 errors in 199 files`，未宣称全仓库通过。Windows 实测 Python 3.11.1 / Windows 10 build 26100：200 次 acquire/close 中位 31.007ms、p95 44.302ms，1000 次选择复核中位 3.387ms，200 次空闲占用检查中位 1.191ms；详细命令和原始输出见 [Phase 4B-2 PR 报告](docs/PR-REPORT-PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-2026-10-03.md)。

本轮保留既有 dirty worktree、不覆盖用户修改；用户已在 2026-10-03 明确授权将截至目前应纳入范围的修改提交并推送到当前远程分支，实现提交 `5a1b6e0` 已推送至 `origin/codex/phase3-worker`，本地 HEAD 与远程 `ls-remote` 已核验一致。4B-2 内部代码和自动化/实机证据完成，但 4B-3 仍不得开始，直到资源硬门的人工/公开条件另行验收；当前仓库也没有自动选择已安装 Feature 的 Settings 生产入口或完整 QAction/结果展示调用面，因此只提供内部授权复核 seam，不宣称公开完成。

### 完成后的实际使用效果

用户界面没有新增安装或管理按钮；当 Core、Settings 或 Worker 持有版本，或系统无法证明租约已释放时，后续清理路径应阻止删除。版本 revision、摘要、启用状态或 generation 变化后，旧请求和旧结果不会继续生效。Worker 父子启动交接不再出现父租约已释放而子租约尚未接管的可见窗口。

## Phase 4B-1.5 人工验收确认与状态同步（2026-10-03）

用户确认 Phase 4B-1.5 的资源 DLC 人工验收门已通过，覆盖此前待确认的资源包人工安装、重启后播放、可见桌面与托盘相关人工检查。该确认补充并更新当前状态，不倒写 2026-10-02 的历史记录；公开稳定资源 API、资源 SDK、管理 UI 与完整安装/升级/卸载闭环仍未开放。

本次同步将 `README.md`、`SPEC.md`、`docs/PROJECT-ENTRY.md`、`docs/INDEX.md`、当前 Phase 4B 设计/任务状态及本日志索引中的当前描述统一为：4B-1.5 人工门已通过，4B-2 已完成并推送，下一阶段为后续规划的 4B-3。本轮不实施 4B-3，不新增用户界面，不修改运行时代码。

### 完成后的实际使用效果

项目当前可以把资源 DLC 的人工验收视为已通过，但用户仍不能在桌宠内完成官方功能包的安装、停用、卸载或重装管理；这部分要等后续 4B-3 至 4B-5 逐阶段交付。
