# 项目变更日志

## MOD 管理中心与公共 v1（2026-10-10）

M00 后实施统一本地列表、默认停用导入、即时启停/设置、按实例角色使用及回退、批量操作和公开 v1；离线 hello-local/Echo、角色副本与台词模板教程完成。最终 Core4.2.5/两个DLC1.0.4 已构建，保留4.2.4与鲸鱼图标。全收集4568节点按273文件隔离进程执行，4553 passed/15 skipped，无遗漏；先前单进程Qt原生崩溃不计通过。最终冻结 Core 的 Echo 返回、删除待退出/下次启动物理清理及官方生产识屏Worker握手/自然退出通过。M05三轮高负载及报告收尾进行中；不触碰真实安装/Key/屏幕。新功能未提交推送。[报告](docs/PR-REPORT-MOD-CENTER-V1-2026-10-10.md) / [最终交接](.scratch/mod-authoring-v1/HANDOFF.md)。

## Phase5A 用户确认与源检查点（2026-10-10）

用户已明确确认 4.2.4 候选实际体验无问题；这覆盖本轮真实使用验收，不代表其他平台或未来 MOD 界面通过。按本次明确授权，对 159 个源码/测试/脚本/文档复验并准备提交到 `origin/codex/phase3-worker`；13 个生成目录与真实用户数据排除。Ruff 与初步差异检查通过；暂存差异发现两行新报告尾随空格，已修正文档。全量 4509 passed /15 skipped /14 warnings（1910.76s），26 族满 CPU 三遍各 292 passed，643 输入摘要一致；负载自然退出、零残留。门禁已通过，随后实际提交并正常推送 `0a299612714e5fad55a24a5506dfce938b9eeb1c`，远端 SHA 已核对。该授权不延伸到后续 MOD 新功能。下方 S/R 快照保留反馈前状态。


<!-- S04_CURRENT_START -->
## Phase5A S修复与连接反馈（2026-10-10）

S01–S04 工程交付完成；真实 Provider/屏幕效果与用户安装体验仍待确认，Phase5A 不关闭。S修复可信portable运行目录、诊断顺序和手动重试；真实冻结验收另发现验证耗时误入心跳预算，test-first改为READY后心跳。用户追加按钮旁成功/失败与实际HTTP码，网络/TLS/超时单列，备注保留且测试不保存。Setup行为不改；鲸鱼娘图标由最终PE10帧及Windows私有快捷方式实显核验。

全量：4507 passed, 15 skipped, 14 warnings in 802.39s (0:13:22)；exit 0，643 个输入前后摘要一致。高负载：第1轮 169 passed in 47.59s、exit 0、CPU 中位 100.0%（含调度49.875s）；第2轮 169 passed in 47.06s、exit 0、CPU 中位 99.75%（含调度48.656s）；第3轮 169 passed in 47.69s、exit 0、CPU 中位 100.0%（含调度49.813s）；三轮全部通过且自有负载进程自然退出。最终Core/Screen/Setup重建及真实冻结Core→安装包→生产Worker→租约HELLO/READY→自然退出0/0通过。逐文件、性能实测、失败史与人工顺序见 [S报告](docs/PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md) 和 [HANDOFF](.scratch/phase5a-local-distribution/HANDOFF.md)。保留旧候选与原dirty，不读取真实Key/屏幕，不改真实安装，无提交/推送/正式发布；真实用户门不代签。
<!-- S04_CURRENT_END -->

<!-- R_MEMORY_20261008_START -->
## Phase5A R修复工程交付与人工待验（2026-10-08）

2026-10-08：Core 4.2.2 / AI 1.0.2 / Screen 1.0.1 的 R01–R05 修复已实现；最新默认单进程全量 4417 passed /15 skipped /14 warnings、自然 exit0，3×152 项满CPU复跑通过，已重建受影响 Core/新 Setup并复核其余交付输入。工程范围验证完成，待用户人工验收，Phase5A 未正式关闭。 原T历史/旧产物/失败记录保留。Core中央API与用途授权、四聊天/文件/余额即时提交、Worker闭合依赖与手动自动生命周期、Core-only系统卸载已按R01–R05红绿实现；R06最新单进程4432项4417passed/15skipped/14warnings/1391.19s、621输入前后不变，15族满CPU三轮各152passed、20自有worker全自然0。HEAD既有循环GC/QThread借用根因公开seam red→green23p/8.48s；v4租约ready迟达44.040s仅测试90s Event预算，最新全量/负载重新验证。

已重建受影响Core/新setup-lifecycle；本轮Worker/helper/ZIP输入仍相符，root207/stage212/resources1000/bundle2103/PYZ2188/GUI PE=2审计，6项SHA、最新Core四保留profile与no-DLC设置自然退出0，payload/ledger不变。API与guard实测性能、140累计文件说明、真实机记录与人工步骤见[同一报告§8](docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md)。末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。

两根合计预算11.25GiB、旧生成物不删除；进入R前78dirty路径保留，无暂存/提交/推送/子智能体/正式发布。当前停点[HANDOFF](.scratch/phase5a-local-distribution/HANDOFF.md)：用户先备份/自然退出、覆盖新Setup更新旧unins000.exe，再验真实API/屏幕、两来源Core-only保留/重装及单包范围；工程自动化不能代替用户确认，Phase5A未正式关闭。
<!-- R_MEMORY_20261008_END -->

## Phase5A夜间修复与暂停交接（2026-10-06）

用户要求先总结交接，未开始任务暂停。仅AI Core03正常启动/真实receipt/菜单/自然退出0通过；新增身份绑定验收驱动9测试。双包确认可见owner标题公开seam红绿，相关23通过。冻结长路径三层根因修复后相关59通过，新helper08/Worker04在LPAC长短路径均完整握手退出且父端五项隔离true；完整原生权限canary通过，无放宽安全或读取真实秘密。

Core04以正式公钥策略和新helper构建审计（455,783,226B/1424文件/2260PYZ），未跑新版四组合、未重签/重打分发。full-01=4294 passed/15 skipped/14 warnings/796.84s，最新full-02已收齐4298 passed /15 skipped /14 warnings /924.58s、exit0；见[准确交接](.scratch/phase5a-local-distribution/HANDOFF.md)。quality-02受影响mypy60初红7错误，驱动补类型后单文件mypy/9测试通过；总体复验、高负载、新正常Worker/特殊路径/完整性能/Setup/便携/人工门均未完成。证据、逐文件增删和实测见[原实施报告追加章节](docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md)。

最新no-follow生成物抽样9.699864GiB，本夜1.904173GiB；12GiB上限，未扩大清理范围、未绕过旧拒绝。原WIP、旧Core、个人数据与正式密钥/签名根保留；无暂存、提交、推送、发布或子智能体。现在无需人工操作；恢复实施后先补安全/运行验证，再一次性安排敏感及体验门。


<!-- PHASE4B_PUBLICATION_START -->
## Phase 4B 分支发布（2026-10-04）

用户明确授权提交推送到 `origin/codex/phase3-worker`；基线 `bd048d5`，85 文件白名单保留原WIP。生成物/私钥/个人数据不入库，不合并、不强推、不正式发行。主提交 `98bbfba78232bb0881628cbbfb36cfdaa519fcb8`（85文件/+14095/-156）已正常push，并由ls-remote/fetch独立核对远端SHA，ahead/behind0/0。

本次新门：Ruff全仓、format518、affected mypy38/default26通过；专项223 passed /1 skipped /1 warning（139.93s）；未过滤全量3841 passed /13 skipped /14 warnings（571.34s）；20个自有CPU fixture下风险族连续3×154 passed，各遍CPU median/p95均100.0%。命令、样本量和单次时间见[工程报告](docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)和[准确交接](.scratch/phase4b-local-management/HANDOFF.md)。

127文件文档链接、101项报告纪律与cached diff门通过；新增WORKLOG末尾空白已修正后复验。最终纯文档封存与源码提交分开，不回填自身SHA。本轮不改变产品源码，TDD不适用；压力fixture已正常回收，不终止普通Core/用户进程、不重建冻结产物。五个新建测试临时目录的删除被策略拒绝，126.432 MiB夹具保留且不入库，未绕过。正式信任锚/分发T0～T3继续暂缓，未执行人工门及UX-M3未证实原因保留；当前Core03及原profile/凭据不受提交推送影响。
<!-- PHASE4B_PUBLICATION_END -->

## Scratch 生成物清理（2026-10-04）

用户确认原294目标范围后，移除132,245个旧冻结构建/依赖/生成夹具文件（31.184 GiB逻辑大小）。`.scratch`完整只读盘点34.987→3.809 GiB；E盘净空闲实测增加31.435 GiB，全部删除目标消失、0失败/跳过，原28个reparse点跳过未跟随。删除与安全复核670.909秒，最终全根盘点108.866秒；一次性存储治理不改变产品稳态路径。

当前Core03两变体、原manual-session/profile/凭据域和安装1.0.0、人工包源、任务记录/证据/before-image保留；9项文件和3个ZIP摘要核验一致，公开账本仍revision17/enabledTrue/pending=null。没有强退、改系统ACL、读取私人数据、产品代码修改或Git提交/推送/发布。正式信任锚与分发按用户决定暂缓；当前构建不是正式发行产物，未解决UX-M3与未执行人工门不冒充通过。

证据与准确停点见[同组最终交接](.scratch/phase4b-local-management/HANDOFF.md)及同目录cleanup result/after；文档链接/PR报告纪律/报告门101 passed（3.96秒），git diff --check通过、暂存为空。本轮不为清理重跑全量或生成新冻结构建，历史结果按原日期保留。使用效果：原启动方式/APPDATA继续可用，用户不需要终端操作。

## Phase 4B 连续收尾（2026-10-04）

基线 `bd048d5`，保留原WIP；Windows LPAC/可信冻结probe、生产事务与启动receipt、跨进程生命周期和“常规 / 扩展管理”完成。最新Core10无聊天/带聊天各7行27步通过：真实管理UI目录/ZIP安装、实际Worker/生成HTTP、启停、多进程自然释放卸载、重装保留profile/vault、升级/retained回滚、自检拒绝、导入候选后加载失败与重启previous真实receipt；无源码回退。PYZ/native重读及185/201个源输入与当前文件匹配。helper20权限门1 passed（57.07s），原生UI/相关合同69 passed（63.92s）。

最终全量06：3821 passed, 13 skipped, 15 warnings in 514.51s；20个自有burner下时序/生命周期族连续3×166 passed /1warning，各遍CPU采样median/p95均100%。Ruff、Pythonformat515和受影响mypy46通过；87项app/settings类型债、7份历史Markdown格式债逐条与HEAD相同，无新增。全量05Qt客户端事件循环测试、深路径/GUI依赖、kernel-lock重试、反向额度协议等普通故障已保留根因/red/green，不删断言、放宽隔离或使用历史通过冒充本次。

性能实测和逐文件/实机三份证据见[连续收尾报告](docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)。Windows工程闭环通过不等于本人真实识屏/凭据/托盘、其他平台、正式信任锚或Setup/发布验收；默认内置功能无假物理卸载。本轮无暂存、提交、推送、发布或子智能体。早期本轮中间结果保留于同组WORKLOG及报告，不覆盖旧日期历史。

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

## 2026-10-04 — Phase4B人工体验与正式信任推进（实施草案）

已获用户授权代执行终端，先准备真实Worker/正常入口的独立人工验收构建，待用户效果回执后补正式信任与分发。旧自动化工程证据保留；临时key不转正式、原APPDATA不变，未提交/推送/发布。准确进度见同组HANDOFF。

### 2026-10-04 人工验收准备完成与首条状态回执

两种新人工冻结Core、真实Worker、三个临时签名版本/ZIP已准备并审计；不使用生成图像、网络或vault模拟端口。目录安装经用户GUI接受，真实生产加载清除pending；用户明确回执“设置显示已启用”，仅登记状态显示，真实识屏/凭据重启/退出/其他操作仍待人工验收。新全量复跑3833 passed/13 skipped/15 warnings（546.99s），此前失败与修正保留于[人工验收报告](docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md)。正式key/发行签名/分发未完成，未提交、推送或发布。

用户随后确认真实手动识屏“结果符合”、通过托盘正常退出“已退出”。仅在内核版本租约free且原自有进程结束后代重启；用户不重填密钥再识屏“得到了正确结果”，因此登记手动效果与重启后凭据可用的行为门通过。自动识屏、其他管理操作和第二变体仍待逐项确认，正式信任/分发未开始；本轮不读取真实凭据、截图、模型回答或个人日志。


### 2026-10-04 11:29 升级后的真实用户效果确认

用户此前已确认包级停用入口撤销、重新启用入口及真实结果恢复，以及ZIP同摘要幂等提示。升级1.0.0→1.0.1遇到host占用时保持等待；用户自然退出后，执行助手只在本次进程结束/内核租约free后代重启，实际生产加载清除pending。用户现在明确反馈“版本显示和识屏结果正确”；公共账本revision9/active1.0.1/previous1.0.0/enabledTrue/pending=null，Core26260身份匹配。此为升级显示和真实手动效果的人工闭环，不扩大为回滚/卸载/重装、自动识屏或第二变体通过。下一步保留当前Core，用户从管理UI接受retained previous回滚；不强退、不热替换、不代点确认，不读取个人数据。


### 2026-10-04 11:46～11:48 回滚人工通过与卸载预检

用户针对回滚后1.0.0版本显示与真实手动识屏两项要求回复“确认通过”，公共账本revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null与Core34672身份吻合；回滚人工子项通过。用户随后粘贴卸载预检摘要，与唯一公共journal和不可变摘要匹配，delete_versions为1.0.0/1.0.1、awaiting_confirmation、accepted=false，尚未接受卸载。确认页的“回滚目标1.0.0”和宽泛签名文案是通用模板语义问题UX-M2：实际卸载只预检删除边界、接受后不能取消恢复启用。已在用户接受前澄清，后续按操作类型修复并回归，正式分发前不能留此误导。继续由用户UI接受/反馈安全等待，不代理删除，不清理个人数据；人工全门、正式信任/分发尚未完成。

### 2026-10-04 人工卸载锁竞争修复

用户已确认点击最终按钮但得到 management_lock_busy；state revision12/enabledTrue/pending=null，不视为卸载通过。旧异常归类把管理/租约/状态锁混为一类；真实 queued 生命周期与自有内核锁复现入口撤销早于接受提交。五文件白名单先保存原 WIP，再 RED 7 项 -> GREEN 7 项；实际 queued 卸载/host pin 闭环1项通过。错误来源、旧摘要残留、同进程重开重试丢失和 UX-M2 卸载文案已修复；锁顺序/CAS/数据保留未改变。相关回滚 lazy 调用遗漏5项红已回溯修复，原五项绿。全量与双新冻结构建仍推进；不读取用户配置/凭据/日志，不手改账本、不强退、不提交/推送，详见持续人工验收报告。

### 2026-10-04 锁竞争修复累计门与原生夹具回溯

双manual-core-03已冻结并核对产品快照；03尚未启动，不把旧程序当修复版。新全量/负载原生AV已按组合二分定位新增页面重建夹具，单纯processEvents/全局DeferredDelete不解决；补齐只处理自有页面及manager的实际Cpp销毁，67项受影响时序族真实满负载连续三遍通过（每遍CPU median/p95 100%），详见持续人工报告及manual-lock-high-load-02证据。此前失败保留，全量03无过滤运行中；最新Ruff/format518/affected mypy通过，相关报告门99 passed，白名单/保护文件/空暂存审计通过。卸载人工尚未接受/完成，下一步关闭旧Settings后代启03管理专用设置重新确认；不读取私人内容、不删除用户数据、不提交/推送。

### 2026-10-04 13:48 锁失败修复最终门与新人工窗口

完整无过滤full04退出0：3841 passed/13 skipped/14 warnings，pytest498.82s，wrapper499.795s。新增Qt页面重建夹具的自有manager/控件实际销毁已修正，管理时序族67项满CPU三遍通过；旧meta正对照只补实际调用Event同步/finally cleanup，相关42项另满CPU三遍通过；不改动画/ffmpeg/GUI异步生产逻辑。静态、报告门及21文件白名单/19 before-image/2保护文件/空暂存审计通过，原WIP与历史失败保留。

旧Settings已自然退出，13:47只读精确EXE核验仍只有原Core34672；13:47～13:48代开manual-core-03管理专用设置（PID7060/creation1791092844.339965），沿用原APPDATA，启动4.968s/RSS140451840字节/11线程/CPU1.296875s，正常可见窗口。公开账本仍revision12/enabledTrue/pending=null，新设置无所属版本租约；没有代理确认或删除安装文件，不读私人配置/凭据/日志、不强退、不重置账本。下一步由用户在新窗口卸载并最终确认，先观察真实占用，再自然退出桌宠和安全重试。卸载/ZIP重装与正式信任分发未通过；不提交/推送/发布。

### 2026-10-04 13:55 卸载接受与真实安全等待

用户在新管理窗口确认后回执version_in_use。只读公共账本revision13/enabledFalse/pending=tx-56514a949d454d6792cbc34de9234d83，journal accepted=true/pending_runtime_release，删除集合1.0.0与1.0.1、deleted_versions为空。直接探测现有内核锁且不清理租约记录：仅Core34672的1.0.0 host占用，1.0.1 free；新Settings7060无功能版本占用，两版本文件仍在。已请用户通过托盘自然退出桌宠、保留管理窗口，退出后先核验租约再由用户安全重试。只登记接受/等待环节，不宣称物理卸载或数据保留重装通过；不强退、不手改状态、不代删除、不读私人数据、不提交推送。产品及测试未修改，证据详见持续人工报告和manual-user-uninstall-await-release-01.json。

### 2026-10-04 14:01 卸载物理文件与未安装提交核验

用户安全重试后确认“操作已完成”，公开revision14/active=null/previous=null/enabledFalse/pending=null/versions={}，已接受卸载journal completed/deleted_versions=[1.0.0,1.0.1]；versions目录无条目，两版本目录均消失。原Core已退出，新管理Settings7060仍运行，物理卸载门通过。证据manual-user-uninstall-completed-01.json。仅读公共安装证据、精确自有进程和生成ZIP摘要，无手工删除/代理确认/私人配置或凭据读取。下一步在新管理UI选择v1.zip并确认，随后验收真实生产加载、原设置/凭据可用及真实识屏；此时不把个人数据保留行为提前标通过。产品和测试未改，不提交/推送/发布。

### 2026-10-04 14:19 ZIP新安装经普通设置真实加载确认

用户从卸载后空账本revision14经管理UI确认ZIP新安装，revision16等待启动。助手代启Core03窗口但pending未清除/无host租约，未判成功；用户关闭管理设置后仍未推进。随后代启同EXE普通--settings生产入口，公开revision17/active1.0.0/enabledTrue/pending=null，原install journal completed，Settings15792拥有真实host/settings内核租约。首Core31020仍无host绑定，原因未证实并登记UX-M3，不声称仅关设置修复；下一步用户自然退出该Core、助手再启动核验，用户不重填密钥验证原设置和识屏。冷启动n1：Core窗口3.923s、普通Settings3.363s，原始私人日志未读取、无强退/手工receipt/状态修改。产品测试源未变，纯记录门续验；正式信任/分发未完成、无暂存/提交/推送。

## Phase 4B ZIP重装后Core自然重启（2026-10-04 14:30）

用户自然退出后，终端核验原03 Core/Settings均已结束，再代执行正常Core入口，沿用同一人工APPDATA；Core30256/creation1791095122.0622613实际桌宠窗出现，冷启动n=1为4.218s。只读公共核验revision17/active1.0.0/enabledTrue/pending=null、ZIPinstall journal completed；精确Core持有匹配版本/revision/摘要的native occupied host租约，正常execution resolver resolved。没有强退、写state、代理receipt、清理租约或读取私人内容。

下一步按用户偏好一次汇报验收：入口恢复、“已启用 · 1.0.0”、原设置保留、不重填密钥识屏正确。真实凭据/设置保留和结果尚未用户确认；首次Core未产生pending加载确认的UX-M3根因仍未证实，不宣称修复。产品/测试源未改，记录/报告门续验；正式信任、自动/第二变体仍待验收，无提交/推送/发布。证据见[人工报告](docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md)。

## Phase 4B 卸载重装的数据保留体验通过（2026-10-04 14:52）

用户明确确认“入口恢复，原设置保留，未重填密钥，结果正确”，限定no-chat人工03/1.0.0在两版本物理卸载后ZIP新安装、真实生产确认与Core重启的实际使用。公共复核revision17/active1.0.0/enabledTrue/pending=null、ZIP事务completed、Core30256身份和native host租约匹配；私人配置/凭据/请求内容不读，不宣称全部数据字节一致。

正式T0候选方案已补入同一总设计，用户归属/加密PKCS8仓库外保管/本机专用口令输入/独立离线备份/更新Core锚的轮换撤销待确认。现有正式锚/helper pin仍空且fail-closed，既有构建只接manual/validation，不把人工测试key或119 passed/1 skipped/22.40s前置合同复验当正式发行通过。本轮只更新公开文档、保存10份before-image及公共证据；没有产品/测试源修改、正式密钥生成、提交、推送或发布。UX-M3首次加载问题及自动/其他人工门继续单列。见[人工报告](docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md)。


## 2026-10-04 UTC：Phase 5A / 5B-1 累计源码验证（未完成交付）

统一小 Core 与两包独立管理的实施仍为 e2687be 后未提交 WIP。AI 实现已迁至 features/ai_chat/host；新增 RuntimeLayout、manifest v2/host-only、离线签名/生产材料工具、旁置包 Setup 与卸载前置门、显式普通数据/凭据引用导入确认工具。稳定全量134：4171 passed、14 skipped、14 warnings、701.35s；Ruff/format139文件、配置mypy56文件通过。高负载三遍及生成数据性能记录继续在同组任务记录中更新。

正式密钥未创建，生产信任锚为空；正式冻结产物、完整附件/资源导入、真实安装/用户确认/干净环境门尚未完成，无提交/推送/发布。red91 未隔离入口测试曾意外复制旧真实 APPDATA 配置/会话约26KB至新产品目录；已中止并修复自动旧数据迁入路径、补回归和后续测试 APPDATA 隔离；意外目录保留且不作为验收，不能声称全程未触碰真实数据。

准确停点见 [.scratch/phase5a-local-distribution/HANDOFF.md](.scratch/phase5a-local-distribution/HANDOFF.md)；设计见 [Phase5A](docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md)。当前实际效果是源码/生成夹具下的独立选装与显式导入闭环基础，不是用户可下载的正式新交付物。


### 2026-10-04 UTC：满载、数字性能与证据停点

稳定源码全量134后，真实CPU高负载136 Qt/IPC/进程族三遍各178 passed（87.67/99.89/92.87s），20个本轮CPU进程median/p95均100%并已回收。生成数据导入基准135：preflight20样本median/p95 41.052/115.257ms、apply10样本168.159/382.651ms、recover10样本79.154/121.167ms；源码warm进程数字不等于正式冻结性能。space138拥有根1,645,640,384B/6GiB，无清理其他任务或旧验收产物。

逐文件、性能、原生实机/网络初始化负项限制、测试隔离事故及未完成门见[实施证据报告](docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md)。最新report139 101 passed/0.76s、Markdown129文件、diff-check退出0；无提交、推送、发布。下一步正式密钥创建仍先确认仓库外私钥/公开政策目标，不覆盖；用户仅本地masked窗口输入密码。完整资源/附件映射、新产品专属自启动清理、正式材料及真实人工/干净环境门继续实施，不宣称Phase完成。


### UTC 2026-10-04T21:58:40+00:00：正式密钥创建目标已确认，启动本地密码窗口

用户确认新建外仓正式key和公开策略（key_id official-release-2026），不覆盖且不延伸为迁移/安装/发布授权。生成夹具专项141 20 passed/1.62s。CLI37572准确自身Qt窗口可见；首次隐藏控制台造成初始Qt窗口隐藏，只queued恢复本窗口并复验，不重启、不读密码/子控件/截图。窗口证据 signing-launch-142；核验时两目标尚不存在，不记创建成功。当前用户只在本地输入密码，之后代理读公共结果/指纹和私钥元数据继续；密码不经聊天/日志/argv/env。无产品/测试源码变更、无提交推送发布，Phase5未完整完成。


## 当前停点：正式密钥创建已核对（UTC 2026-10-05T01:14:39.201695+00:00）

- 用户本轮回复“已输入”，已完成之前授权的本地密码操作。可信CLI公开成功记录与正式公开策略一致，诊断日志0字节；私钥文件302B、创建UTC 2026-10-05T01:09:19.881520+00:00。仅检查私钥元数据，没有读取/展示私钥内容。
- key_id `official-release-2026`，公钥指纹 `dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab`；公开策略限定两个官方owner及现有能力，未撤销。证据：`signing-launch-142/key-creation-receipt.json`。脱离式启动没有捕获退出码，不能补写退出0；可信CLI仅在两个文件成功写入后输出该成功记录。
- 正式加密密钥/公开政策位于仓库外 `E:/AI/DSH/release-signing/`；备份副本、解密/恢复检查、正式签包/冻结Core/分发验收尚未执行。创建授权不延伸到真实数据导入、安装卸载或发布。
- 下一步先补生产PYZ必须包含导入/维护真实入口的失败回归，以及LPAC网络canary必须实际抵达Winsock连接调用的证据，保留安全边界；随后继续剩余资源导入、新产品自启动清理和正式构建。
- Phase5A/5B-1仍未完整交付。既有full134/负载136为修改前源码历史；无提交/推送/发布/子智能体。

### 当前实际效果与限制
密码操作已完成，本轮不需要在聊天提供任何秘密；尚不能把新小Core视为正式交付。以下密码等待状态为当时的历史事实，不是当前状态。

---

## 2026-10-05 Phase5A 最新累计复验（UTC 2026-10-05T04:49:44.602394+00:00；仍实施中）

正式密钥/双包签名已经完成，旧“待输入/未创建”仅为当时历史。229公开Qt回归先红，230修复卸载确认窗scroll接口遮蔽与可空receipt；相关186 passed，受影响56+AI host26源mypy通过。最新全量234为4258 passed /14 skipped /14 warnings /861.35s，源码哈希未变；CPU满载233三遍各227 passed，109.71/122.16/128.58s，CPU median/p95均100%。

新helper05、权限矩阵224和正式双包LPAC225通过；Core02尚不是最新Core。空间237已用5,871,902,944B，余570,548,000B，2GiB新Core预留不满足；213清理未执行，不绕过。自有测试Core PID36460等待自然退出，不强退。报告逐文件证据持续修正，最终交付/真实安装与导入/人工/干净环境未完成。没有提交、推送、发布或子智能体，没有新增真实数据操作。

239/240补记：Ruff实际模块路径初次失败保留；用已安装绝对native工具完成全范围Ruff/151format，mypy26/56/26、文档129、165报告/构建专项、diff-check通过。241只读清理提案未执行，8项1,633,900,888B，待目标/影响确认与工具安全条件；不重试213/core-01拒绝。最终Core03、真实安装/导入、人工与干净环境仍未完成。

## 2026-10-05 Phase5A文本门与未清理停点（243～248）

## 当前停点：文本复验完成，等待限定空间清理确认（UTC 2026-10-05T05:22:53.605378+00:00）

- 用户“已输入”已用于正式加密密钥与双DLC签名，未再次创建密钥、不读取私钥、不通过聊天索取秘密。分支codex/phase3-worker / e2687be，原WIP全部保留，暂存为空；无提交、推送、发布或子智能体。
- 最新完整Python累计门仍为full234：4258 passed /14 skipped /14 warnings /861.35s，运行期间源码不变。真实满CPU233三遍各227 passed；Ruff/151 format、配置26/受影响56/AI host26 mypy、129文档及165相关门已通过。239 Ruff模块路径失败保留，240使用既有绝对路径原生工具通过，未注入PYTHONPATH或重装。
- 243最终补充检查：文档129、报告101、tracked diff-check通过，但发现新文件3处空白（AI QSS空EOF与Setup英文消息2处行末空格），因此该追加审计总体退出1，不能写成整体通过。244首次修正按LF检查原始CRLF而失败，发生在所有写入之前；245保留原行尾方式，只修上述文本，并把消息分隔符写在Pascal字符串连接处。
- 246文本相关回归190 passed /1 skipped /140.61s；文档129、报告101、tracked diff-check与所有新增文本空白复核通过。pet/features/scripts/tests的Python源码与full234哈希一致。没有新增Python逻辑、配置、线程或持久化变更，故这次纯空白/提示分隔符切片采用专项+相关验证，不重复14分钟全量；不能把full234说成测试了未来改动。
- 已签名AI源包和ZIP均未改写；AI仓库QSS少1空行使最终payload哈希需要重新生成并签名，现有正式包作为历史候选保留，不原地修签名。Core02落后4处Core源且带旧helper04，Core03必须重建并携带helper05；Worker02与screen签包未因文本修正变化。最终统一签名另走可信本地解锁，当前不再索要密码。
- 空间247只读测量：5,901,946,176B，余540,504,768B，29个reparse剪枝未跟随；不足2,147,483,648B的Core03预留。241八项目标1,633,900,888B只是提案，未删除，须删前再核对边界/拥有/占用、保留结果并获得目标确认且工具允许。213/core-01拒绝不重试、不绕过，旧Phase4B/真实profile/凭据/事故根始终保护。
- 247确认仅本轮空包测试Core PID36460仍驻留，exe与创建时间匹配，APPDATA为bench-frozen-core-219/APPDATA。不强退；用户可使用这个测试Core自己的退出菜单自然退出。219启动性能仍0有效样本，不能写达标。
- OPS-PROBE-MATERIALS（Status: ready-for-agent）：生产run保留自检文件快照，现有cleanup_owned_probe只恢复记录profile，未被pet生产调用；事务GC仅处理已退役versions。实际成功225材料仍在，确认文件积累缺口，但未做生产崩溃profile泄漏实测，也不等于隔离突破。下一切片先在公开seam补红测：活进程不得清理、已释放的明确拥有材料有界回收、未知/证据冲突/链接不删除、清理失败留可恢复记录；接入恢复只信拥有记录和真实释放，不能全根扫描猜测归属。新逻辑会要求重跑全量/高负载及重建。
- 准确下一步：请求用户确认PLAN241八项明确白名单；仅在原生工具允许、安全边界/占用证明成立时处理，并重新核对6GiB/2GiB空间门。同步补有界probe材料恢复后再最终Core03、双包和分发签名、冻结请求/升级/卸载/便携/Setup闭环。真实安装卸载/数据导入/密钥备份新目标另行确认；人工/干净环境、Authenticode/SmartScreen/Inno许可仍未验收。

### 当前实际效果与限制

本轮只完善源码文本与证据，没有新增安装、删除或真实数据操作。正式签名与安全自检已有阶段证据，但最终Core、安装分发和Phase5A/5B-1仍未交付，不能安装旧候选冒充完成。


## Phase5A空间授权与Core03产物门（2026-10-05）

## 当前停点：8GiB授权后Core03/ZIP/Setup生成，全量与满CPU三轮通过（UTC 2026-10-05T13:16:35.263Z）

- 分支`codex/phase3-worker` / HEAD `e2687be`，原WIP保留；无暂存、提交、推送、发布或子智能体。Phase5A/5B-1仍在实施，未宣布交付完成。
- 用户2026-10-05追加授权：本轮生成物峰值从6GiB调至**8GiB（8,589,934,592B）**，Core构建预留仍为2GiB。280只读no-follow实测7,599,215,563B（136,544常规文件；36链接/reparse剪枝），余990,719,029B；E盘212,282,949,632B可用。254/213删除被工具拒绝、实际0B释放，未重试或绕过；不扩大清理权限，不触碰旧4B/真实profile/凭据/事故AppData根/密钥。
- Core03已构建并审计：455,780,427B /1,424文件 /2,260实际PYZ模块；PyInstaller133.113s、构建服务180.321s、含预算扫描父端219.955s。嵌入helper05摘要`3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a`及正式信任策略；Core无AI/屏幕理解实现，无测试公钥或源码回退。Authenticode仍未具备。
- 最新稳定全量273：**4274 passed /15 skipped /15 warnings /657.90s**（命令父端660.976s），退出0；pet/features/scripts/tests运行期源码哈希未变。275全范围Ruff、155文件format、配置mypy26/受影响57/AI26、129文档链接、165报告/构建用例及tracked diff-check通过。最新文档修改后另行复验。
- 276真实20个自有below-normal负载进程下，17个Qt/IPC/生命周期族连续三遍各**251 passed /1 skipped**：118.76、121.54、115.61s；每遍CPU median/p95均100%/100%，全部负载回收，源码哈希未变。与273共同覆盖当前产品Python；后续只有文档和忽略目录验收脚本修改，不冒称全量覆盖未来变更。
- 267相关专项95 passed /2 skipped；270真实新父端LPAC双方各1次通过。271生成夹具20次自检/30次原生启动及拥有材料GC全部有效，AI median/p95=3552.794/3814.0633ms，屏幕=16583.77145/19648.1784ms；各10次累计回收198,911,947B/1,675,446,804B。原271汇总NameError退出1，独立验证保留失败并核实样本；RSS/IO未采集，不填造。
- 冻结278空包/仅屏幕两组合通过：Core03自行实际加载确认，screen修订4/active1.0.0/enabled/pending=null；真实菜单owner隔离、自然退出0，不借普通设置替代Core。最终仅AI/双包仍待新AI正式签名；旧Core02四组合只为历史证据。
- 277普通ZIP259,368,713B、便携ZIP259,368,880B、正式screen ZIP24,455,042B已审计。首次Setup因验收脚本字符串转义破坏ISCC路径、WinError2未启动编译器；279只重试未创建的Setup，编译退出0：222,182,771B /119.579s，SHA256 `1dbbe87cbf21abebb81825d46c5632e4667a0d0f632e0b4069959cee7a6d7233`。**编译不是安装验收**；未执行真实Setup安装，Authenticode/SmartScreen/Inno许可单列。
- 新AI无签名材料`production-builds/ai-unsigned-02`为**1.0.1**、manifest摘要`e4faefde007278aa16ae82ee6b984e0b208bea37ad68afbb4b29392f0cfbbc5a`，与当前QSS一致；旧1.0.0包不覆盖。281可信本地密码窗口已打开，284按创建身份核验子PID18108与可见标题；仅签名新`ai-signed-02`，不创建密钥、不安装/导入/联网/发布。初次30秒窗口检测短于136k文件预算扫描，记录window_not_confirmed；284只核验同一进程，没有重复启动或读取密码字段。签名尚无成功回执，密码只在本地遮罩框输入。
- 准确下一步：用户在既有本地窗口核对目标后解锁→核实AI1.0.1正式签名→最终AI/双包冻结矩阵→总分发清单签名。真实Setup安装/更新/卸载、冻结请求/升级/回滚/便携、>=10启动/状态操作性能、用户与干净Windows门仍未完成；真实安装/导入/密钥备份目标须再次展示确认。

### 当前实际效果与限制
空间上调已让最新小Core及ZIP/Setup产出，而不是跳过安全门。全量、滿CPU三轮和空包/仅识屏真实启动通过；本地密码窗口只用于新AI签名，不影响已验收旧桌宠。最终交付及人工/新用户环境验收仍未完成。

## Phase5A五产物与总分发签名通过（2026-10-05）

- 用户追加本轮生成物上限为8GiB，不增加删除范围。293形成五文件平面候选并前后核验SHA256，五产物合计767,710,064B；原候选和旧人工验收目录保留。
- 294经本地遮罩输入窗口完成正式总分发签名，7.809986s/n=1，签名进程退出0；296按已核对的外部正式公钥政策独立核验通过，1.249367s/n=1。不创建或覆盖私钥，密码不进入聊天、日志、argv或env，未使用包内公钥自证。
- 296只读no-follow空间快照8,374,833,244B（7.799671GiB），136,746常规文件、36链接/reparse剪枝；8GiB内余215,101,348B（205.137MiB），E盘余211,506,925,568B。这不是连续实测构建峰值；后续大文件操作须重新估算。
- Core03、AI1.0.1、screen1.0.0正式产物通过；最新全量273为4274 passed /15 skipped /15 warnings /657.90s，满CPU276三遍各251 passed /1 skipped，Ruff/format/受影响mypy通过。后续只有文档与忽略目录验收驱动，最终文本门另行复验，不用源码测试代替安装验收。
- 278空包/仅screen普通Core自身加载、菜单、自然退出通过；287仅AI实际Core加载确认通过（revision4、active1.0.1、enabled、pending=null），但菜单/自然退出驱动失败。290～298失败保留，不强退测试进程，不以原生显示位或IPC回执冒充Qt菜单/租约释放；both整行尚未运行。
- Setup目前只是279真实编译通过，不是安装；真实Setup/更新/卸载、冻结业务/升级/回滚/便携、性能剩余矩阵、人工/干净环境、签名密钥备份恢复和Authenticode仍未验收。未提交、推送、发布或使用子智能体。

### 当前实际效果与限制

本轮空间门已通过，新的小Core与五个正式交付候选可独立核验；Phase5A/5B-1完整交付尚未完成。不会自动安装或导入个人数据，平面签名目录不混入packages/证据，旧桌宠和保护目录保持不动。

收尾补记（UTC2026-10-05T14:36:32.632733+00:00）：302文档129/101报告/diff与源码一致性全通过，301原240秒遍历超时保留；不变更产品源码或probe安全预算。305再次独立验签及空间核验通过：8,374,960,188B（7.799789GiB），余214,974,404B（205.016MiB）；签名与五产物哈希不变，没有新安装、删除或大产物。304仅看到自有测试窗口原生hidden，不把元数据当自然退出；仅AI/both、真实Setup与人工门仍未完成。收尾记录补记不变更链接目标，追加报告/文本门306退出0：101 passed /1.43s、diff-check退出0、176文件覆盖/空白/源码一致性通过，同组公开回执保留。

## 2026-10-06：授权Phase5A源检查点，先验证后推送

183文本文件逐项白名单暂存，不含生成物/日志/秘密/个人数据。新全量 4298 passed, 15 skipped, 15 warnings in 929.75s (0:15:29) /931.253s，源SHA不变；21族真实满CPU三遍（第1遍 361 passed, 1 skipped, 1 warning in 332.06s (0:05:32)，wall 336.500s /CPU median 100.0% /p95 100.0%；第2遍 361 passed, 1 skipped, 1 warning in 260.70s (0:04:20)，wall 264.844s /CPU median 100.0% /p95 100.0%；第3遍 361 passed, 1 skipped, 1 warning in 259.37s (0:04:19)，wall 263.437s /CPU median 100.0% /p95 100.0%）通过。Ruff/162 format、mypy26/60/26及167报告/构建专项通过。纠正一条不存在测试路径的历史命令，不虚构原始argv。最终文档门及远端SHA检查后才写推送完成；Phase5A/5B-1仍有特殊路径、正常冻结Worker、新矩阵/正式分发及人工/干净环境门，不冒称完成。


## 2026-10-10 S01 施工草案：portable 识屏启动

按已批准 S01–S04 修复项目内运行目录误拦与同步诊断；先 red 后实现。旧 dirty/候选、真实安装/Key、Setup 行为不变；未提交推送。计划与准确停点见 `.scratch/phase5a-local-distribution/PLAN.md`、`HANDOFF.md`。


## MOD 管理中心与公共 v1：可运行检查点（2026-10-10 16:40）

final4 Core/Setup 已构建，审计通过两个 1.0.4 包、Core/Setup 鲸鱼图标和旧 4.2.4 保留。全新隔离数据根完成真实 Core→生产 Screen Worker→HELLO/READY→自然退出闭环，Core/Worker 均 exit 0，租约退出后 free；独立 Settings 扩展管理页也完成原生烟测。全量文件隔离测试 4563 passed/15 skipped，MOD 专项47 passed，Ruff/diff check通过。满 CPU 识屏短预算、子宠清理和跨进程停用失败保留为非阻塞压力限制；用户授权先提交可运行版本。本地提交待完成，不推送；真实 Setup 安装/升级/卸载和 Provider/余额/屏幕仍人工验收。
