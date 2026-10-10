# Phase5A 通用本地 DLC 收尾实施报告（2026-10-07）

> **当前R状态（2026-10-08）**：2026-10-08：Core 4.2.2 / AI 1.0.2 / Screen 1.0.1 的 R01–R05 修复已实现；最新默认单进程全量 4417 passed /15 skipped /14 warnings、自然 exit0，3×152 项满CPU复跑通过，已重建受影响 Core/新 Setup并复核其余交付输入。工程范围验证完成，待用户人工验收，Phase5A 未正式关闭。 详细最新证据只用§8；§1–§7保留T历史。
>
> **以下为原 T01–T07 历史（2026-10-07），不代表 R 修复版通过。** 本报告只记录 2026-10-07 当前工作树的实际运行；最终全量 `4334 passed, 15 skipped, 15 warnings in 759.16s` 和三轮各 `97 passed` 均已实际退出。代码、自动化、构建、实机、用户确认、Git 和正式发布分别记账。
>
> 工作区 `E:\AI\DSH\dsh-pet-indesktop`；分支 `codex/phase3-worker`；基线 HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。用户授权自主完成 T01–T07 与收尾，但不授权真实系统 Setup、读取真实 profile/密钥、Provider 请求、强杀进程或 Git 发布；未使用子智能体，未暂存/提交/推送。
>
> 设计：[收尾计划](PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md) · [实现交付](PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md) · [阶段设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md)。持续记录：[PLAN](../.scratch/phase5a-local-distribution/PLAN.md) / [STATUS](../.scratch/phase5a-local-distribution/STATUS.md) / [HANDOFF](../.scratch/phase5a-local-distribution/HANDOFF.md) / [WORKLOG](../.scratch/phase5a-local-distribution/WORKLOG.md) / [SUMMARY](../.scratch/phase5a-local-distribution/SUMMARY.md)。历史报告不作为本轮新合同通过证明。

## 1. 目标、范围、根因与实现

**目标**：Setup 内只内嵌官方 AI/Screen，勾选后调用无 Qt 维护自动 preflight/apply，第一次正常 Core 启动完成 receipt；Setup 外显式选择 ZIP/目录按 manifest 路由为受信任本地 DLC，未知 owner 不再必须在官方集合中；Core 主 EXE 使用 GUI subsystem。

**根因**：旧 verifier、事务和租约把官方注册当作通用 owner 身份；单一 Screen manager 吃到 AI/第三方输入；Setup 依赖 `{src}\packages` 且进入有确认 UI 的普通入口；Core spec 使用 console=True。不是再加若干 owner/factory 例外，而是把中性注册与官方发布策略分开。

| 任务 | 落地方式 | 验收状态 |
|---|---|---|
| T01 | 四个公开 seam 红测试，未知 owner、manifest 路由、Setup 内嵌、GUI spec | 先红后绿，保留证据 |
| T02 | 同一个 FeatureRegistration 形状；local bounded manifest 注册，signed/official 保持闭合 | 相关测试通过 |
| T03 | 复用状态账本、事务、租约、startup receipt；通用 context/凭据命名空间，不新增平行安装器 | 真 LPAC、跨进程启动/重启与卸载通过 |
| T04 | 独立 ZIP/目录按钮、bounded router、正确 owner manager、exact token 自动 apply、第三方管理卡片 | 深链接/焦点/Tab/缩放/无内置功能场景回归通过 |
| T05 | ISS 编译时内嵌两 ZIP、临时提取、无 QApplication 维护；重复安装重入；官方输入验证 | 当前 Setup 编译与冻结维护矩阵通过；真实系统 Setup 未运行 |
| T06 | Core console=False 与真实 PE subsystem=2；不统一改变 Worker/诊断窗口策略 | 当前 Core 实机启动/退出通过 |
| T07 | 当前源码重建、完整套件、性能、实机报告、索引与五记录 | 授权范围内完成；系统安装、Provider、用户确认/发布另记 |

**约束与替代路线**：不引入远程 catalog、自动下载、签名撤销或社区 SDK；不对任意 Python 作静态“安全扫描”承诺。官方 ID 仍有 factory/execution/capability 额外合同，明确绑定的 verifier 不允许 owner mismatch；未知 local owner 不是 Screen wildcard。用户选择只是取消发布者认证，路径/重解析点、大小/文件数、完整清单、SHA-256、Core/API/平台兼容、事务和启动确认不能放宽。已导入 Python 的 lease 只在原进程自然退出后释放，不用 binding.close 冒充热卸载。

## 2. 修改文件说明

### 2.1 范围与逐文件证据

下表为最终收口时 `git diff --numstat` 加未跟踪文本行数的**累计工作树**清单，不把所有既有脏改动归为本轮新写。本轮核心新增是中性 registration、local router/generic 管理与存储、Setup 维护/内嵌、GUI PE 门及失败回归；Screen 默认配置/迁移、部分原生 probe/Worker 和历史报告差异在开始时已存在，保留并验证，不擅自覆盖。新增文件按 `+完整行数/-0` 标记；无删除文件；生成物、截图、原始日志、测试资料和秘密均不纳入版本清单。

<!-- FILE_TABLE_START -->
| 文件 | 状态 | + / − 行 | 改了什么 + 为什么 |
|---|---|---:|---|
| `.scratch/phase5a-local-distribution/HANDOFF.md` | 修改 | +394 / −0 | 记录准确停点、当前构建/验证版本、授权边界和下一条人工操作；不把历史通过当成本轮通过。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | 修改 | +296 / −0 | 沿用 T01–T07 编号并更新完成证据、人工门与停止条件；避免另起一套计划。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | 修改 | +326 / −0 | 分开实现、自动化、实机、用户确认和 Git/发布状态；保留未授权系统安装门。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | 修改 | +302 / −0 | 更新跨对话摘要和通用本地 DLC 决策；下一对话无需重复推翻已验证方向。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | 修改 | +340 / −0 | 留存 red→green、构建、失败分类、实机/性能和收尾命令；保留既有历史。 |
| `docs/INDEX.md` | 修改 | +9 / −5 | 登记本轮计划、交接和 PR 报告，更新 Phase5A/Phase6 指针；保证新文档可发现。 |
| `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md` | 新增（未跟踪） | +255 / −0 | 保留并更新用户指定 T01–T07 实施计划；历史根因与当前结果分开。 |
| `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md` | 新增（未跟踪） | +118 / −0 | 保留并更新指定代码交付合同与实现后交接；明确不再采用官方 owner 白名单导入。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md` | 修改 | +341 / −1 | 保留并整理先前工作树已有验收证据和未测边界；不将其历史数字冒充本轮验证。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md` | 新增（未跟踪） | +322 / −0 | 新增逐文件、性能、实机和未测门报告；固化当前源码证据而非仅依赖原始日志。 |
| `docs/PROJECT-ENTRY.md` | 修改 | +9 / −3 | 将入口由“代码待实现”更新为当前交付和人工门；指向最新报告与阶段记录。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | 修改 | +105 / −15 | 收敛 Setup 内嵌官方选装与用户主动信任本地包；发布签名仍是未来独立策略。 |
| `docs/plugin-phase-05-distribution/README.md` | 修改 | +15 / −9 | 更新阶段入口、内嵌包安装和当前验收状态；移除旁置 packages 作为正常前置。 |
| `docs/plugin-phase-06-ecosystem/README.md` | 修改 | +7 / −5 | 明确本地通用 DLC 已落地而在线生态/正式信任体系未交付；避免阶段范围混淆。 |
| `features/screen_understanding/common/models.py` | 修改 | +30 / −0 | 保留视觉共享配置的独立、无凭据默认值；Screen 不再被 AI 配置存在性绑定。 |
| `features/screen_understanding/host/manual.py` | 修改 | +6 / −1 | 保留缺 Key、不可用和配置错误的区分提示；无凭据实机验收可得到可解释结果。 |
| `features/screen_understanding/host/migration.py` | 修改 | +40 / −0 | 保留显式授权的缺失凭据引用修复、空值与源校验；不覆盖已有视觉配置。 |
| `features/screen_understanding/host/settings.py` | 修改 | +21 / −59 | 保留独立视觉 Key 状态与手动配置，移除隐式 AI 迁移入口；减少跨功能耦合。 |
| `packaging/core_webm.iss` | 修改 | +73 / −9 | 内嵌两个经预检的官方 ZIP，临时解包后调用无 GUI 安装维护并校验返回码/日志；不依赖 {src}\packages。 |
| `packaging/phase4a_validation_entry.py` | 修改 | +7 / −7 | 在 GUI 初始化前统一分流 Core 维护入口；维护行为不走旧有 QApplication/授权弹窗路径。 |
| `packaging/phase4b_manual_entry.py` | 修改 | +7 / −0 | 手动验收变体也早期识别维护命令；与冻结 Core 实机矩阵使用同一产品入口。 |
| `pet/__main__.py` | 修改 | +13 / −5 | 增加有界维护参数解析和早期分流；确保 install-packages 不创建 QApplication 或正常启动弹窗。 |
| `pet/app.py` | 修改 | +6 / −1 | 在应用启动发现已安装通用 owner 并注册 manager/生命周期；未知包不再遗漏到官方固定循环之外。 |
| `pet/core_maintenance.py` | 修改 | +149 / −0 | 增加只接受显式官方选装集合的 preflight/apply、重复安装/重新启用与 JSONL 返回记录；Setup 自动启用而非只预检。 |
| `pet/credentials.py` | 修改 | +3 / −2 | 凭据 owner namespace 接受合法本地标识，保持 owner 隔离；不读取或迁移真实用户 Key。 |
| `pet/feature_build_policy.py` | 修改 | +6 / −3 | 保留显式 local activation 构建开关与官方 capability ceiling；不放宽 signed-only 构建。 |
| `pet/feature_config.py` | 修改 | +5 / −2 | 本地配置 namespace 以合法 owner 校验替代 official lookup；通用包仍不能跨 owner 读写。 |
| `pet/feature_host_bindings.py` | 修改 | +101 / −13 | 增加 descriptor 驱动的本地 host/worker 绑定和最小上下文；不给未知 host desktop/window/legacy 凭据。 |
| `pet/feature_install_state.py` | 修改 | +9 / −3 | 泛化状态 owner 校验，保留 revision/digest/事务字段合同；避免安装落盘时再次被官方白名单拒绝。 |
| `pet/feature_lifecycle_contract.py` | 修改 | +2 / −3 | 生命周期 owner 合同泛化到合法本地标识；依然隔离跨 owner 的关闭/草稿操作。 |
| `pet/feature_management.py` | 修改 | +186 / −10 | 增加中性目录/ZIP路由、按 owner 创建/发现管理器和异步确认绑定应用；管理不再把 AI 或第三方送到 Screen。 |
| `pet/feature_management_ui.py` | 修改 | +142 / −12 | 统一 ZIP/目录选择、动态第三方卡片与提示，承接设置页装配 helper；保留回调销毁和 line budget。 |
| `pet/feature_package_probe.py` | 修改 | +2 / −2 | 启动探针按 verifier.accepts_descriptor 校验激活策略；本地授权包不再硬要求 trusted_official。 |
| `pet/feature_package_startup.py` | 修改 | +5 / −6 | 按 descriptor.execution_kind 启动、泛化 owner 并保留 startup receipt；未知 owner 可跨重启确认。 |
| `pet/feature_package_transactions.py` | 修改 | +10 / −8 | 事务验证与 owner 约束改用中性策略；保留路径/清单/哈希、锁、回滚和 lease 安全门。 |
| `pet/feature_probe_adapter.py` | 修改 | +6 / −5 | 将 parent-owned local activation 策略送到隔离 helper并按策略复验快照；不由候选包决定信任政策。 |
| `pet/feature_probe_crypto.py` | 修改 | +6 / −4 | 更新 headless verifier 的 local/signed 双策略说明；libsodium 兼容仍受构建封存输入约束。 |
| `pet/feature_probe_materials.py` | 修改 | +4 / −2 | 探针材料 owner 与 descriptor 按中性标识验收；保留封存、快照和恢复边界。 |
| `pet/feature_probe_windows.py` | 修改 | +1 / −1 | Windows 隔离启动的 owner 校验接受合法本地 ID；不移除 LPAC/Job/封存检查。 |
| `pet/feature_startup_contract.py` | 修改 | +3 / −2 | 启动上下文 owner 检查不再绑定官方集合；保留 runtime 类型和 owner 一致性。 |
| `pet/feature_version_lease.py` | 修改 | +2 / −4 | 租约 owner 使用通用标识；真实进程退出才释放代码占用，仍不承诺 Python 热卸载。 |
| `pet/local_package_intents.py` | 修改 | +201 / −9 | 受限 manifest 预读、自动注册/路由和选择确认令牌；从指定包识别 owner 而非沿用官方列表。 |
| `pet/modern_settings_dialog.py` | 修改 | +26 / −30 | 装配统一扩展管理入口，声明新 row 的 capability claim，并让 extensions 深链接聚焦 ZIP；保持原 line budget。 |
| `pet/official_features.py` | 修改 | +40 / −5 | 引入中性 FeatureRegistration、ID/factory 校验与兼容别名；官方注册仅提供额外策略，不是本地集合。 |
| `pet/plugins/feature_packages.py` | 修改 | +7 / −4 | loader 使用激活策略、descriptor 与中性 owner 合同；signed-only 验证仍可拒绝同一无签名包。 |
| `pet/plugins/package_trust.py` | 修改 | +103 / −19 | 区分有界预读、local 与 signed-only 全验；未知 v1/v2 owner/factory 可注册，保留官方 ceiling/兼容/路径/哈希限制。 |
| `pet/runtime_layout.py` | 修改 | +4 / −2 | 运行目录 namespace 接受合法通用 owner，并继续拒绝路径穿越；避免持久化命名空间隐含 official-only。 |
| `pet/window.py` | 修改 | +11 / −0 | 保留键盘语义上下文菜单的稳定身体锚点；实机探针可仅向自有窗口发 WM_CONTEXTMENU、不抢焦点。 |
| `scripts/build_core_webm_setup.py` | 新增（未跟踪） | +83 / −0 | 新增 canonical Setup 编译入口和官方包预检；只有 manifest/兼容/哈希通过才调用 ISCC。 |
| `scripts/build_feature_management_delivery.py` | 修改 | +32 / −19 | 交付构建以 explicit local activation policy 和当前 helper snapshot 为输入；不再把签名密钥作为本地发行前置。 |
| `scripts/build_feature_management_manual.py` | 修改 | +63 / −15 | 手动实机变体同步无密钥本地构建与 helper 输入；保留不注册系统自启动的验收边界。 |
| `scripts/build_feature_probe.py` | 修改 | +30 / −15 | 保留直接执行 import 路径修复和 headless module 排除（含提前网络初始化的 multiprocessing）；让真实 LPAC verifier 可启动。 |
| `scripts/build_feature_probe_native.py` | 修改 | +3 / −0 | 保留 Py_SetPath 分号边界拒绝；避免 native helper 搜索路径被截断/扩展。 |
| `scripts/build_screen_delivery.py` | 修改 | +196 / −43 | 生成 GUI Core spec，读取 PE subsystem，支持当前 local helper/源码和资源封存；保留 Core 与 Worker 的不同窗口策略。 |
| `scripts/feature_probe_entry.py` | 修改 | +16 / −3 | 严格解析 parent-owned allow_local_packages，兼容旧 signed-only 输入；使用同一 verifier 而非绕过签名/完整性。 |
| `scripts/validate_phase5a_delivery.py` | 修改 | +39 / −15 | 保留进程身份约束的实机驱动，更新内嵌选装/无密钥与正常退出判据；不把未测系统安装填成通过。 |
| `scripts/validate_phase5a_setup.py` | 新增（未跟踪） | +96 / −0 | 新增有界的 Setup 官方包合同验证；安装分发不接受任意本地包或异常官方 execution/capability。 |
| `tests/_feature_ui_child.py` | 修改 | +6 / −2 | 将统一入口深链接/Tab 焦点更新到 ZIP→目录，同时保留 Screen 卡片自身的原生键盘可达性检查。 |
| `tests/test_core_maintenance_entry.py` | 修改 | +16 / −0 | 覆盖维护在 Qt/app 初始化之前分流与无 GUI 入口；安装路径不能误触正常 UI。 |
| `tests/test_core_setup_template.py` | 修改 | +17 / −2 | 覆盖内嵌包、temp 解包/维护、禁止外部旁置依赖；更新模板责任而非移除门禁。 |
| `tests/test_feature_management.py` | 修改 | +15 / −0 | 覆盖从 Screen 管理表面选择 AI 的自动 owner 路由；错误 package 不污染 Screen 状态。 |
| `tests/test_feature_management_build.py` | 修改 | +36 / −1 | 覆盖本地策略 snapshot 和冻结 GUI subsystem 的构建合同；无需本地密钥。 |
| `tests/test_feature_management_ui.py` | 修改 | +2 / −2 | 将 extensions 深链接焦点断言同步中性导入入口；保留多矩阵、草稿、异步生命周期测试。 |
| `tests/test_feature_manual_acceptance.py` | 修改 | +35 / −0 | 覆盖 manual 变体本地构建、独立视觉配置/缺 Key 的可解释验收行为。 |
| `tests/test_feature_probe_build.py` | 修改 | +22 / −0 | 覆盖 helper headless exclusions/直接导入与封存输入；防止 LPAC 启动前拉起网络/GUI 依赖。 |
| `tests/test_feature_version_lease.py` | 修改 | +1 / −0 | 测试策略显式允许本地 fixture，保留真实双进程/退出租约门；不改宽松为假阳性。 |
| `tests/test_feature_worker_handoff.py` | 修改 | +1 / −0 | Worker handoff fixture 显式使用本地激活，继续验证父/子 reservation 和自然退出所有权。 |
| `tests/test_official_feature_contracts.py` | 修改 | +6 / −2 | 覆盖新增本地入口 row 的 capability ownership，保留两个官方卡片独立责任。 |
| `tests/test_phase5a_delivery_acceptance.py` | 修改 | +71 / −14 | 覆盖更新后的实机驱动/选装组合、身份及正常退出判据；不靠 mock 宣称真实 Setup 执行。 |
| `tests/test_phase5a_local_activation.py` | 新增（未跟踪） | +58 / −0 | 新增无签名本地激活、同包 signed-only 拒绝与构建 policy snapshot 测试；两条信任路线互不污染。 |
| `tests/test_phase5a_setup.py` | 新增（未跟踪） | +143 / −0 | 新增 Setup 包预编译/维护顺序、返回码、重复待启动安装回归；避免勾选只做 preflight。 |
| `tests/test_phase5a_t01_regressions.py` | 新增（未跟踪） | +369 / −0 | 新增 unknown owner/factory、AI 路由、Setup 内嵌、Core GUI 的 red 回归，再扩展本地 integrity/context/lifecycle 合同。 |
| `tests/test_runtime_layout.py` | 修改 | +5 / −2 | namespace 正例加入 third.party，反例改为 ../third.party；保留 scope 唯一性而非拒绝合法 generic owner。 |
| `tests/test_screen_ai_migration.py` | 新增（未跟踪） | +101 / −0 | 保留并验证显式授权且仅缺失凭据可修复的独立视觉迁移；不改写已有设置。 |
| `tests/test_screen_delivery_build.py` | 修改 | +94 / −1 | 覆盖新 Core spec/PE 和输入封存、local policy 及 helper 合同；构建事实不能仅看注释。 |
| `tests/test_screen_manual_host.py` | 修改 | +13 / −0 | 保留视觉缺 Key/不可用提示回归；未请求真实 Provider。 |
| `tests/test_screen_settings.py` | 修改 | +32 / −23 | 保留独立视觉配置 UI 与缺凭据状态合同；移除对隐式 AI 迁移入口的旧期望。 |
| `tests/test_settings_process_isolation.py` | 修改 | +1 / −0 | 设置进程 lease fixture 显式使用本地策略；继续真实 Qt/独立设置生命周期覆盖。 |
<!-- FILE_TABLE_END -->

### 2.2 设置与工程预算

统一入口是扩展管理能力行，不新增持久化设置；扩展深链接现在定位 `local_package_import` 并聚焦 ZIP，官方卡片仍可通过键盘抵达。第三方卡片与 draft/lifecycle endpoint 按 owner 挂接，关闭时解绑，不把暂存草稿自动保存/丢弃。

`pet/modern_settings_dialog.py` 曾达 2455 行，触发既有 2395 行架构门；把组装移动到现有 `feature_management_ui.py` 后为 2377 行，**没有提高预算或取消断言**。16 个原生 UI 和 24 个域/深链接矩阵的旧 Screen 焦点合同已更新为独立导入合同，同时保留官方目录→ZIP Tab 可达性。凭据测试由“合法第三方一定拒绝”改为 root/instance/owner 七组命名空间不碰撞，并保留非法路径拒绝。

## 3. 测试与验证

### 3.1 Test-first 证据

| 检查点 | 实际输出 | 结论 |
|---|---|---|
| 首批 T01（产品修改前） | `4 failed in 5.24s` | verifier 官方硬校验、router 缺失、Setup 外部 packages、Core console=True 四处预期红灯 |
| 独立导入按钮公开 seam | `1 failed, 1 passed in 4.38s` | 补独立入口后相关集合 `96 passed in 101.36s` |
| 首次全量 -x | `1 failed, 373 passed, 1 skipped in 21.72s` | 架构行数门；拆组装，不加预算 |
| 重复维护安装 | `1 failed in 1.84s` | 允许合法 awaiting_startup_confirmation 重入并正确读取 StateResult.state |
| 扩展行域/深链接 | 分别 `1 failed in 1.22s`、`1 failed in 0.99s` | 补 capability claim，聚焦中性入口 |
| 相关最终集合 | `109 passed in 41.75s` | T01、架构、Qt、维护、深链接/隔离相关绿灯 |
| 上一完整套件 | `17 failed, 4317 passed, 15 skipped, 14 warnings in 743.00s` | 16 个原生子进程旧焦点预期 + 1 个 old official-only scope 预期；未掩盖或豁免 |
| 合同同步后专项 | `65 passed in 48.37s` | 原生 16 个布局矩阵、runtime namespace、T01 通过 |
| Ruff/格式/空白 | `All checks passed!`；`63 files already formatted`；`git diff --check` exit 0 | 当前 Python 变更和差异检查通过 |

最终全量：**`4334 passed, 15 skipped, 15 warnings in 759.16s (0:12:39)`，exit 0**，命令 `$env:QT_QPA_PLATFORM='offscreen'; python -u -m pytest -q`，原始结果 `implementation-20261007/pytest-final-current.log`。早先两次主动中断没有结果，不算通过。源码/测试修改后不沿用历史绿色结果。受影响时序族高负载三遍已完成，结果如下。最终全量之后只改文档与自有诊断脚本，没有修改产品/测试源码。

### 3.2 可复现命令

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONIOENCODING='utf-8'
python -m pytest -q tests/test_phase5a_t01_regressions.py tests/test_phase5a_local_activation.py tests/test_phase5a_setup.py tests/test_core_maintenance_entry.py
python -m pytest -q tests/test_feature_management_native_ui.py tests/test_runtime_layout.py
python -m ruff check pet tests scripts features packaging
python -m pytest -q
```

原生 UI 族的实际子进程使用 Windows 平台和真实 Qt 事件循环，不用 offscreen 替代可见桌面。真实 LPAC 安装/启动记录另列，不用 MockChecker 冒充隔离链。

### 3.3 满负载三轮时序门与最后审计

命令 `python .scratch/phase5a-local-distribution/implementation-20261007/highload_reruns.py`。20 个自有 CPU worker（逻辑 CPU=20），每秒采样全机 CPU；被测族为 `test_feature_management_ui.py`、`test_feature_management_native_ui.py`、`test_settings_process_isolation.py`、`test_feature_version_lease.py`，每轮 97 项。Qt/租约测试仍使用真实事件循环/进程；没有固定 sleep 猜测断言时序。

| 轮次 | pytest 实际输出 | 驱动全链 s | CPU 中位 / 均值 % | exit |
|---|---|---:|---|---:|
| 1 | `97 passed in 200.99s` | 212.2985937 | 100.0 / 99.9972067 | 0 |
| 2 | `97 passed in 182.02s` | 184.4014972 | 100.0 / 99.9993506 | 0 |
| 3 | `97 passed in 177.87s` | 189.5171436 | 100.0 / 99.9993590 | 0 |

20 个 worker 经 multiprocessing Event 协作停止并自然 join，exit code 全部 0，owned_workers_remaining=[]；没有 kill/terminate。原始轮次日志、CPU 样本和退出信息留在 `implementation-20261007/highload-results.json`。性能表的采样发生在 CPU 负载之前，不混用两个环境。

最后 `final_code_verification.py` 重跑 Ruff、63 个 Python 文件 format --check、正常 `git diff --check` 和当前构建源码/资源审计，四项 exit 0（分别 0.127785s、0.1552643s、0.1639295s、3.8821307s）。没有以改变 autocrlf 的方式制造/忽略 CRLF 空白结果；构建审计再次确认 201/206/1021/2126 项一致。

<!-- FINAL_DOCS_VERIFICATION -->
最后新报告专项 `59 passed`，exit 0；13 份相关 Markdown 的 267 个相对链接无断链、无尾随空白。累计 69 修改 + 9 新增的 78 项文件说明/行数已核对，无删除、无暂存；20 负载 worker 全 exit 0、残留 []，5 个已记录自有进程身份复核无残留，未枚举/操控其他用户进程。结果 `implementation-20261007/final-document-verification.json`。
<!-- FINAL_DOCS_VERIFICATION_END -->

## 4. 性能分析

### 4.1 环境、命令和样本

Windows kernel `10.0.26100`、Python `3.11.1` x64、PySide6 `6.11.1`、PyInstaller `6.20.0`、逻辑 CPU 20。路由性能在构建/实机矩阵结束后独立 Python 进程测量，预热 1 次，50 次样本；647 B 最小第三方包、764 B ZIP；`perf_counter_ns`、psutil CPU/RSS/thread/io 前后差。

命令 `python .scratch/phase5a-local-distribution/implementation-20261007/evidence_probe.py`。原始 JSON 留在自有生成目录，但本表已固化必要数字，不要求提交生成物才能理解结果。

| 路径 | n | 中位 ms | p95 ms | 50 次 CPU ms | RSS 增量 B | read_count / read_bytes | write_count / write_bytes | 线程 |
|---|---:|---:|---:|---:|---:|---|---|---|
| 观测 noop | 50 | 0.0003 | 0.0011 | 15.625 | 585728 | 0 / 0 | 0 / 0 | 5→5 |
| 目录 manifest 路由 | 50 | 0.90535 | 2.7802 | 78.125 | 12288 | 100 / 25150 | 0 / 0 | 5→5 |
| ZIP manifest 路由 | 50 | 0.3835 | 0.6427 | 31.25 | 8192 | 250 / 51600 | 0 / 0 | 5→5 |
| 完整目录验证 | 50 | 2.13995 | 2.2951 | 125.0 | 12288 | 350 / 32350 | 0 / 0 | 5→5 |

这些是热缓存小包成本，不是大型 ZIP、冷盘或所有第三方行为的吞吐承诺。psutil 的 Windows IO 是进程计数，不等于逐个内核 syscall；路径校验的 `other_count` 分别 1750/1600/10400。CPU 15.625ms 量化粒度和 noop 的 RSS 585728 B 表明观测/分配器噪声存在；不把几 KB 正增量称为“零内存开销”。

### 4.2 稳态、触发频率与新增资源

- manifest 路由仅在用户选择 ZIP/目录或既有选择适配器中触发，不进入宠物每帧/碰撞/动画路径；只读 manifest/ZIP central directory，目录/条目有上限，不执行 payload。
- 导入才创建正确 owner manager；第三方管理复用既有 FeatureStateMonitor，每个非内置 manager 有一个 QThread、QFileSystemWatcher 和默认 1000ms 回读 fallback，并建立原有本机 lifecycle IPC 端点。不是“新增功能没有线程/磁盘成本”。
- preflight/apply 在有限管理线程中执行，调用真实 LPAC helper/必要 Worker、staging/哈希、原子状态/日志/租约磁盘写入；不强杀 Qt 线程或进程。当前 native 目录/ZIP 全链分别 12.6842328s/7.2828071s，样本各 1；目录首轮含先前 awaiting_release 恢复，不能用来比较源形式速度。
- Setup 维护为安装选择时的一次性路径，正常稳态不重复执行；没有新增下载/Provider 调用。本地包经用户信任后自身可能访问网络，Core 的包格式验证不能当成网络沙箱。
- 冻结 Core 正常启动（creationflags=0，不由探针隐藏控制台）后，按可见 pet/receipt 同步，安排 30s 观测间隔，再采样 3×5s。两组串行，各为一个自有 profile、同一 Core；只采 Core PID，未把 ffmpeg 子进程 CPU 合算成总应用成本。最小第三方 factory 不产生业务负载，不能外推复杂第三方包。

| Core / 样本 | 观测 s | CPU ms | RSS 前→后 B | ΔRSS B | 线程前→后 | read_count / read_bytes | write_count / write_bytes |
|---|---:|---:|---|---:|---|---|---|
| empty / 1 | 5.009077 | 1218.75 | 145580032→145629184 | 49152 | 25→26 | 3490 / 108795272 | 11 / 6599 |
| empty / 2 | 5.005755 | 1015.625 | 145629184→145608704 | -20480 | 26→25 | 3485 / 109694302 | 10 / 6380 |
| empty / 3 | 5.015916 | 968.75 | 145608704→144572416 | -1036288 | 25→25 | 3515 / 109651350 | 12 / 6793 |
| third / 1 | 5.010020 | 1140.625 | 147070976→147931136 | 860160 | 28→27 | 3552 / 108803782 | 11 / 6150 |
| third / 2 | 5.018493 | 984.375 | 147931136→147931136 | 0 | 27→27 | 3576 / 110641558 | 10 / 6380 |
| third / 3 | 5.014967 | 1328.125 | 147931136→147963904 | 32768 | 27→28 | 3554 / 108804304 | 11 / 6593 |

empty 15.030747s 累计 Core CPU 3203.125ms、RSS 净 -1007616 B；third 15.043481s 累计 CPU 3453.125ms、RSS 净 +892928 B。串行随机动画、缓存、后台调度未锁定，这不是可归因 A/B 回归结论，也不证明长期没有泄漏。Windows read_bytes 包含 pipe 等 IO（动画解码输出），不能当成新增磁盘流量。最早未 settle 的 empty 首个 5s RSS +32559104 B、CPU 4515.625ms，明确属于启动/预热观测，未拿来伪装稳态。

命令 `python .scratch/phase5a-local-distribution/implementation-20261007/frozen_third_steady_v2.py`；采样中间回执 `frozen-third-steady-v2.json`；最终完整回执 `frozen-third-steady-final.json` 包含三次自然退出与停用/卸载最终状态（原驱动写在 `frozen-third-steady.json`，已另存清晰终态名称）。

### 4.3 当前冻结 Core 启动（每种 n=1）

边界为“桌宠可见且已选包 pending startup receipt 清除”，不是主观体感；每种在全新自有 APPDATA，未打开设置/调用 Provider。首次 cold/warm 和系统缓存不控制，不能当作 A/B 回归预算。

| 已选包 | startup ms | RSS B | 线程 | 维护 s |
|---|---:|---:|---:|---:|
| empty | 6275.0799 | 185876480 | 39 | 不执行 |
| AI | 3142.4075 | 209453056 | 32 | 6.7486546 |
| Screen | 4118.0713 | 203546624 | 33 | 15.5287427 |
| both | 4714.1567 | 219303936 | 34 | 20.5310391 |

Core 构建实测 142.5049351s。合并 Phase5A 历史/当前生成物和本轮自有短 profile 的最后空间快照（2026-10-07 16:59 +08:00）为 6,265,636,235 B / 48,377 文件，6 GiB 上限剩余 176,814,709 B。按逻辑文件大小统计、含自有回执；后续仅末次小日志/JSON变化，不再构建或复制大产物。未清空受保护历史产物换取空间，精确快照 `implementation-20261007/space-final.json`。

## 5. 实机运行记录

### 5.1 当前构建与源码对应

本轮构建输入、helper/Worker、官方包、Setup 和证据留在 `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\implementation-20261007`；Core bundle 为降低 ISCC 路径长度移到同阶段的 `c07`，未移动历史产物。新 helper 固定 manifest digest `376a30a368c6051660f166eb5ce555cc7c679be2825263d8911a6b6b63665562`；复用已经核验的 native-05 无 UI bootloader/leaf，重新冻结当前通用 verifier；libsodium 1.0.22 输入 17,690,194 B、SHA-256 `3e03a726fac4bc09cb61d8f29d658ef7a5eca0811de59082130414f7ca2e4279`。

Core 的实际构建调用如下（不是再次运行构建）：

```python
from pathlib import Path
from scripts.build_screen_delivery import build_core

ROOT = Path(r"E:\AI\DSH\dsh-pet-indesktop")
OUT = ROOT / ".scratch/phase5a-local-distribution/implementation-20261007"
build_core(
    ROOT, OUT / "core-current", chat=False,
    entrypoint=ROOT / "packaging/phase4b_manual_entry.py",
    probe_bundle=OUT / "probe/dist/dsh-feature-probe",
    probe_manifest_sha256="376a30a368c6051660f166eb5ce555cc7c679be2825263d8911a6b6b63665562",
)
```
**manual-acceptance-only 变体**保留正常生产启动/管理，但关闭系统自启动注册；本轮不是 Authenticode 正式发布。

`python -m scripts.build_screen_worker --output .scratch/phase5a-local-distribution/implementation-20261007/worker-current --probe-bootloader .scratch/phase5a-local-distribution/acceptance-night-20261006/builds/native-05/probe-run.exe --probe-native-extension .scratch/phase5a-local-distribution/acceptance-night-20261006/builds/native-05/_dsh_probe_native.pyd` 重新构建非 synthetic 当前 Worker。旧 Worker 输入因 `models.py` 变化被拒绝，不能仅因为旧包能运行就复用。使用当前 `assemble_ai_package`/`assemble_package` 生成无签名官方 AI 1.0.1 / Screen 1.0.0 ZIP。

```powershell
python -m scripts.build_core_webm_setup `
  --core-dir .scratch/phase5a-local-distribution/c07 `
  --package-dir .scratch/phase5a-local-distribution/implementation-20261007/packages `
  --core-output-dir .scratch/phase5a-local-distribution/implementation-20261007/setup-current `
  --core-version 4.2.1
```

实际 ISCC 退出 0，Setup 生成 249,514,139 B。第一次编译因 311 字符资源路径压缩失败；只移动本轮新 Core bundle 到短路径 `c07`，最长 255，原证据保留原路径，另记录移动校验，未移动旧用户产物。只读复核：201 个 root 源码、206 个 staged snapshot（含 5 个明确生成的策略/入口文件）、1021 资源、2126 个 bundle 文件的名字/大小/SHA-256 全相符，GUI PE=2；当前 Worker 输入与官方 Setup manifest 复验通过。

| 产物 | B | SHA-256 |
|---|---:|---|
| c07/dsh-pet-core-webm.exe | 17260975 | `0f5de807f1f758a0ab97e645fe9c252ad30ebf42a9c234ab25384f3444cc8eed` |
| setup-current/dsh-pet-core-webm-setup.exe | 249514139 | `cbb08a9b21eb267464ca1f8bf8c264952cb0059815f54d2f4e8bb2dfd3fc03e4` |
| official.ai-chat.zip | 118378 | `8d76815be4de162802052fb349b76ca2c14bd608a03b029f09eb0b834581044c` |
| official.screen-understanding.zip | 24454464 | `87ad5511cbb86b0dd5cc35588e325eaf4bc0c136e7da5dbc2f63250b6d0d3b00` |

### 5.2 真实 Windows 冻结 Core/维护矩阵

命令 `python .scratch/phase5a-local-distribution/implementation-20261007/frozen_matrix.py empty ai screen both`。各 case 的 APPDATA/LOCALAPPDATA/HOME/TEMP 均指向自有 `.scratch/p5a07-owned/frozen-*`，UIA 操作先核验 PID/create time/exe/窗口句柄，只操控自有窗口，通过“退出”自然关闭，无 terminate/kill。

| case | maintenance | 首次正常 Core | 菜单行为 | 退出 |
|---|---|---|---|---|
| empty | 不执行 | 桌宠可见；无已选包 | 没有 AI、看看屏幕、主动识屏 | 0 |
| AI | 0；无可见窗；revision 3 pending | pending 清除，revision 4 | 只出现 AI 对话 | 0 |
| Screen | 0；无可见窗；revision 3 pending | pending 清除，revision 4 | 只出现 看看屏幕/主动识屏 | 0 |
| both | 0；无可见窗；两个 pending | 两者清除，revision 4 | 三个对应入口均出现 | 0 |

**不是真实 Inno Setup 的四种勾选实测**：这里直接调用新冻结 Core 的维护入口、随后正常启动。已证明当前产物维护/加载/菜单合同，未证明安装向导、注册表、快捷方式或真实系统卸载。没有运行安装器来“补”证据。

### 5.3 未知第三方目录/ZIP 与原生设置 UI

命令 `python .../native_dlc_probe.py`：最小 v2 `third-party.example` / `third-party/v1`、host-only、capabilities=[]、无 manifest.sig。目录和 ZIP 都使用当前**真实冻结 LPAC helper**，不是 MockChecker；preflight awaiting_confirmation → exact token apply awaiting_startup_confirmation → 两个真实 Python 子进程 ProductionFeatureStartup load_current 都 completed、owner 正确、desktop=None → 停用 completed → 自然退出后的卸载 completed，最终 versions 空/active None/enabled false/pending None。该 fixture 验证注册和生命周期，不宣称第三方复杂业务或 arbitrary Worker 行为已验收。

第一次诊断将 binding.close 错当作 lease 释放，卸载实际 awaiting_release；保留失败记录，改用进程自然退出后再回收，不改守卫或强杀。源码双进程启动不是冻结 Core 的真实启动证据，后者补测单独列出。

原生窗口命令 `$env:QT_QPA_PLATFORM='windows'; $env:PYTHONIOENCODING='utf-8'; python .../evidence_probe.py ui`。本机真实 Windows Qt、独立 GUI profile，720/1100 宽度与 light/dark 四张截图均逐一检查：独立 ZIP/目录可见且 enabled、告知本地 Python 可执行不是沙箱，横向滚动 maximum=0。宽布局节标题在 deep-link 滚动遮罩顶部部分裁切，不影响入口；源构建的内置卡片不支持物理卸载，这不等于 frozen Core 行为。第一次诊断 stdout 的 cp1252 输出失败为环境问题，设 utf-8 后退出 0。

### 5.4 新冻结 Core 的未知 owner 启动/重启与 GUI 进程树

命令 `python .../frozen_third_steady_v2.py`，source transaction + 当前真实 LPAC helper 把同一无签名 ZIP 写入 **RuntimeLayout 明确的冻结产品资料根**（不是人工 picker 导入）。Core 默认创建参数 `creationflags=0`，不由驱动强制隐藏控制台；新 profile 首次启动 2683.8227ms、再次启动 2658.8185ms，owner `third-party.example` 的 revision=4、enabled=true、active=1.0.0、pending=null，两次都经真实菜单退出 0。receipt 对应 digest `5c457852194e2f791166a5d729228e9d23121378e6c431883929dad2522f81e0`。安装全链 10.5235107s（n=1），所有 Core 自然退出后停用/卸载 completed，revision=7、versions={}、active=null、enabled=false、pending=null。

额外 `python .../t06_default_tree_v2.py` 使用默认 GUI 启动；32s 内 62 次仅记录 root 及当前实际 descendant 的 Windows 窗口。记录到 Core、ffmpeg 和 conhost 的 PID/createTime/executable，ffmpeg ConsoleWindowClass 存在但 visible=false；没有自有进程树中的可见终端，菜单自然退出 0。驱动不隐藏控制台（creationflags=0）；这仍不是 Explorer 手工双击或无限时长保证。

两个探针最初的失败均保留：`Config(base=...)` 附加源码 APP_DIR_NAME，Core 找不到在另一目录写入的 owner；改为 `Config(layout=RuntimeLayout.discover(...))` 后首启/重启通过，未修改产品。额外进程树探针只按标题匹配把小浮层当成第二个 pet，补既有 pet 高度判据后通过；不是关闭浮层或修改应用规避。失败后只向 PID/createTime/executable 校验后的自有 Qt 结束端点发送正常关闭消息，进程退出 0；没有强杀。

### 5.5 失败分类与没有自动完成的原因

- **已修复新问题**：路由/通用 owner/Setup/GUI 首批红灯；独立入口遗漏域 claim；现代设置文件超行数门；重复维护 startup-pending 重入。
- **过时测试合同**：旧 Screen 深链接焦点、official-only 凭据预期；按新需求修预期并保留更严格的 owner/path/Tab 断言，不以跳过测试处理。
- **构建/诊断环境**：ISCC 长路径已用自有短 relocation 解；stdout cp1252 已明确纠正；第一次租约诊断假设错误，产品守卫保留。
- **未执行而非失败**：真实系统 Setup 涉及注册表、快捷方式、现有安装/卸载门，未获得当前任务此项授权；真实 Provider/屏幕识别需用户密钥且可能收费/涉及真实截图，禁止代填或采集；人工偏好不是机器测试能够判断。

## 6. 验收门、风险、回滚和准确交接

| 原计划人工项 | 本轮证据 | 尚未证明 |
|---|---|---|
| 1 直接启动 Core 无终端 | 当前 GUI PE=2、默认 GUI 创建参数、自有进程树 62 次观测无可见终端，正常退出 | Explorer 手工双击与用户主观确认 |
| 2–5 Setup empty/AI/Screen/both | 当前 Setup 编译；冻结维护→Core 四 case 真实通过 | 真实安装向导选择/系统注册/卸载 |
| 6 无 Setup 旁置 packages 仍安装 | ISS 内嵌/dontcopy/临时提取、静态与构建输入门 | 实际系统安装后再验证 |
| 7 UI 选官方 AI ZIP | public chooser/router/auto apply 回归；真实 AI 维护+启动 | 实际冻结文件选择器人工导入 |
| 8–9 未知第三方 ZIP/目录 | 真 LPAC/source 两种输入全生命周期；冻结 Core ZIP 已安装 payload 首次启动/重启通过 | 冻结 picker 人工操作；复杂第三方业务 |
| 10 坏 manifest/错误摘要 | 结构/摘要/owner/路径负例与错误 UI 自动化 | 冻结 picker 上主观可读性 |
| 11 重启 owner 正确 | source 双进程 + 新冻结 Core 首启/重启，owner/revision/digest/pending 正确 | 用户实际包行为 |
| 12 停用/卸载撤入口与执行 | 公共生命周期/租约回归；source 及冻结应用自然退出后停用/卸载，版本表清空 | 真实系统卸载及用户业务贡献 |
| 13 不索要 keys/signature | 本轮所有 fixture 无签名/发布 keys、全链无认证提示；local/signed 策略隔离回归 | 真实 Setup 文案用户确认 |

不宣布 Phase5A/5B 全部正式交付；社区生态、公开 SDK、签名撤销和 Authenticode/SmartScreen 仍在后续阶段。macOS/Linux 未做本轮实机验收。Python 第三方包是主动信任的代码；未来不可信执行必须另开隔离设计。未知第三方 localhost-worker 的复杂实机业务没有验收，不能用官方 Worker/handoff 测试替代。15 个 skipped 不计为通过；本轮 15 个 warnings 为 Qt deprecated API 和重复 ZIP 项负例告警，详见全量原始输出，未因告警修改门禁。

回滚保留原 worktree/历史构建，未 reset/checkout/stage；安装包操作依旧 revision/token/lease/recovery 协议，不能手改状态或直接删占用目录。源码有 201 项匹配当前 Core 快照，若后续产品修改必须重建，不复用本报告哈希。Git 当前 dirty，未提交/推送；提交和正式发布需当前任务独立授权。

## 7. 实际使用效果与限制

你现在可以在当前测试 Core 的扩展管理中选择受信任 ZIP 或目录：manifest 自动决定 owner/factory，而不是把第三方塞到 Screen/官方集合中；通过完整校验和事务后持久化启用，awaiting_startup_confirmation 时仍需正常 Core 启动确认。Setup 已变成内嵌官方 AI/Screen 的编译产物，不再依赖身旁 packages，主 Core 使用 GUI subsystem。

已加载 Python 升级/卸载等待原进程自然退出，不承诺热卸载；本地包不是沙箱。本轮交付实现、当前构建和可复核证据，不代表真实系统 Setup/Provider/用户人工发布门全部完成。最后文档校验已通过，授权范围内不再有待执行的代码任务；后续真实系统安装、Provider 与用户确认按同组 STATUS/HANDOFF 另获授权，不能返回旧官方硬编码路线。

---

<!-- R_REPAIR_REPORT_START -->
## 8. R01–R07 人工缺陷修复与本次交付（2026-10-07 开始，2026-10-08 更新）

**本节是修复版的权威证据；§1–§7保留原T01–T07历史。** 2026-10-08：Core 4.2.2 / AI 1.0.2 / Screen 1.0.1 的 R01–R05 修复已实现；最新默认单进程全量 4417 passed /15 skipped /14 warnings、自然 exit0，3×152 项满CPU复跑通过，已重建受影响 Core/新 Setup并复核其余交付输入。工程范围验证完成，待用户人工验收，Phase5A 未正式关闭。 更新时间 2026-10-08T17:07:52+08:00。基线HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`、分支 `codex/phase3-worker`；进入R前69修改+9新增dirty路径保留。下表是相对HEAD累计差异，不全部归为R新改；未暂存/提交/推送、没有子智能体。

### 8.1 根因、修复方案与任务状态

| 缺陷 | 根因 / 证据 | 修复及边界 |
|---|---|---|
| AI可聊、余额报未配置 | AI DLC与Core余额两套凭据，缺失提示在联网前 | Core多服务API与owner/purpose授权；同Key可绑文字/视觉/余额，DeepSeek余额适配器保留，不支持协议另提示 |
| 保存后已有窗口用旧配置 | 假Key公开seam红灯，runtime/window缓存旧配置 | 四类聊天与文件每个新请求解析提交快照；queued展示、跨进程版本兜底；失效保留输入 |
| 冻结识屏握手前退出 / 手动被取消 | Worker缺中性pet.official_features；自动关闭时刷新误取消手动 | 两构建路径共享闭合依赖；真实租约/HELLO/READY/退出硬门；手动自动生命周期分离、不回退Core |
| DLC部分卸后Core仍阻塞 | 旧系统卸载先逐包事务再staging门 | CoreRemovalPermit仅身份/占用/数据根门与范围内集成；不加载DLC工厂/事务，不由残留ledger/pending阻塞 |
| 默认全量Qt原生AV | HEAD既有QObject.thread()借用main QThread wrapper被PySide parent heuristic归到cyclic endpoint，GC析构主线程数据 | 公开seam red→green；记录创建线程threading.get_ident()作GUI防自等待判断，保持queued/后台wait/取消；不加keeper/私有bit/全局刷事件 |

Core统一 endpoint/path/model/TLS/timeout/credential_ref，Key只入系统安全存储，不进JSON/journal/日志/诊断。地址变更需要重新录Key和授权，安全存储失败不回退明文；版本CAS、prepared journal、原子发布/可重放恢复。旧来源逐项非敏感预览、用户选择确认；冲突保留草稿/旧Key，不静默合并、删除旧密钥或自动启用DLC。

通用可选 FeatureHostContext.api 仅已授权元数据、用途版本、单次不可变请求快照、订阅/跳转；每次检查执行/用途授权，不枚举服务/Key或返回全局Config。第三方同接口、没有官方owner/factory执行特判。临时Key不序列化；普通保存不污染在飞快照，撤权/删除/停用取消并拒绝迟到结果，重新授权不复活旧任务。这是防误用边界，不是受信任Python的沙箱。

| 任务 | 实际证据 | 当前状态 |
|---|---|---|
| R01 | 四根缺陷4failed/1.27s后绿，保留T历史与所有red | 实现/自动化通过 |
| R02 | 中央API、安全引用、泛化授权、CAS/恢复、显式迁移、隔离与无明文负例 | 实现/自动化通过 |
| R03 | 四聊天/文件/余额/Core设置，草稿/即时/撤权；专项106p/10.53s | 实现/自动化通过 |
| R04 | Worker闭合源码/PYZ，真实进程租约/HELLO/READY/natural exit0，手动自动分离 | 实现/自动化及冻结硬门通过 |
| R05 | Core-only与单包文案、两来源/残留/占用/失败下DLC不变断言 | 实现/自动化通过，真实系统卸载待用户 |
| R06 | 最新4432项单进程4417p/15s，3×152满CPU，重建/输入/哈希 | 工程范围验证完成，待用户人工验收 |
| R07 | 最新Core四保留profile、无DLC设置、逐文件/实测性能/实机/人工清单 | 工程范围已记录；真实用户验收待办 |

### 8.2 测试与验证：red→green、最新默认单进程和满CPU门

代表性red保留：R01 4failed/1.27s；中央边界6failed+13passed/13.40s；迁移UI5failed/3.64s；消费者9failed+4passed/9.96s；prepared授权快照2failed/1.04s；深色标准token4failed/1.03s；helper隔离1failed/0.75s；版本4failed/1.24s。最终修复专项 **106passed/10.53s**，Qt/API/独立设置/真实Worker族 **71passed/12.27s**。

**最新默认未插桩全量已经自然退出0**：2026-10-08，完整 **4432** 项、**4417 passed /15 skipped /14 warnings /1391.19s**（wall 1392.540527s）。命令 `python -m pytest -q -o tmp_path_retention_policy=failed --basetemp=<新自有pytest-plain-full-lifecycle-fixed-v2>`；没有诊断plugin、文件过滤、-k、新skip、全局事件冲刷或QApplication/QThread keeper。pet/features/scripts/tests **621** Python输入前后及最终快照一致；新增生命周期回归使旧4431增至4432，15skip沿用平台/环境条件，不算通过。14warnings是实际输出，QImage/QHoverEvent弃用与重复ZIP fixture警告保留。

生命周期新增回归是实际QApplication+主线程wrapper先创建，注册循环draft、公开QueuedFeatureLifecycle.prepare、弱引用确认endpoint确被GC、再启动新QEventLoop，子进程重复3轮。原代码 **1failed/6.63s，child0xC0000005**；仅将防自等待guard改为endpoint创建时Python线程身份，正确green-v3相关族 **23passed/8.48s**。HEAD已有旧guard，故已有原生问题根因已证，不泛化为“环境”；没有升级Qt/混库交付、保活引用或改变Worker清理语义。green-v1文件选择exit4、green-v2 CRLF拼接未命中NameError保留，不当通过。

最新源包含高负载lease测试的 **90s Event/自然join协调上限**。v4第三轮20s ready超时，state→实际lease文件44.0398054s；8次逐核满CPU独立ready探针0.485–2.656s均自然0，未重现底层原因，因此只写协调预算不足的事实，不武断归咎调度/IO环境或产品租约。产品lease/占用/退出/清理断言、期限、skip均未改，异常路径也保证自然join；相关 **32passed/19.25s**。随后默认v2及3轮v5都重新验证，不用旧绿灯遮盖这次改动。

<details>
<summary>历史默认AV、五组覆盖与原生定位过程（截至15:12；以下“未决/正在”是当时状态，不是当前结论）</summary>

**默认单进程全量没有通过**。首次混合源码诊断的 `10 failed, 4348 passed` 不算最终；之后 full2 / full3 / full4 均 `0xC0000005`，631.93 / 640.48 / 651.15 s，分别停在 Worker 启动验证嵌套 Qt 循环、设置 `app.exec`、`WorkerSupervisor.wait_for_stopped_for_tests` 清理循环。full4 约 79% 无断言失败，621 份源码前后相同；后台栈有 DLC 会话 writer 和 mem_debug，但这不是根因证明。65 项最小组合未复现；补做新 API 订阅/手动自动 Worker/共享 AppShell/Worker cleanup 的 **17 文件有界组合，226 passed in 80.35s**（墙钟 80.994 s），也未复现。该结果仅缩小排查范围，不证明完整前缀无遗留。证据 `native-owner-family-results.json` / `R06-native-owner-family.log`。两个确实需要独立 QApplication 的设置/Worker 测试改为真实子进程，未删除事件/租约断言；停止继续随崩溃位置打补丁或盲目重试。

2026-10-08 02:14:54–02:29:38（+08:00）收集 **4431 项 / 262 文件**，按排序连续完整文件分成五组，各用新进程/QApplication；无 -k、deselect 或新增 skip。逐组 nodeid 的并集及顺序与全收集完全相同，SHA-256 `ea13911422f4c2e5945b533385016731e61cbf3b2bdc6535cbdec70d31074571`；621 份源码与 full4/组前后相同。

| 组 | 文件 / 收集项 | 完整文件范围（按收集顺序） | 实际 pytest 输出 | 退出码 |
|---|---:|---|---|---:|
| 1 | 46 / 948 | `tests/test_agent_cost.py` → `tests/test_config_schema.py` | 945 passed, 3 skipped, 2 warnings in 52.45s | 0 |
| 2 | 54 / 935 | `tests/test_config_sound.py` → `tests/test_feature_probe_materials.py` | 932 passed, 3 skipped, 2 warnings in 374.57s | 0 |
| 3 | 69 / 946 | `tests/test_feature_probe_windows.py` → `tests/test_plugin_contributions.py` | 941 passed, 5 skipped, 3 warnings in 115.63s | 0 |
| 4 | 59 / 936 | `tests/test_plugin_runtime.py` → `tests/test_switch_start_failure_window.py` | 934 passed, 2 skipped, 3 warnings in 157.72s | 0 |
| 5 | 34 / 666 | `tests/test_throw_egg.py` → `tests/test_workers_source.py` | 664 passed, 2 skipped, 4 warnings in 51.10s | 0 |

合计 **4416 passed / 15 skipped / 14 warnings**，pytest 时间合计 751.47 s，五子进程 wall 合计 755.708497 s。15 skips 是现有平台/环境条件，未计为通过。收集、每组 inventory 和源码比较见本地 `repair-20261007/full-group-results.json`。这是完整收集的分进程覆盖替代证据，**不是默认单进程门变绿，也不授权合并/正式关闭**。

<!-- R_NATIVE_DIAGNOSTIC_START -->
#### 2026-10-08 连续前缀、完整单进程与失败分类

120文件/1717项连续前缀：**1710 passed / 7 skipped / 6 warnings / 516.78s**（wall517.810s，exit0，621源码未变），见 `native-continuous-prefix-v1-results.json`。前3600项/220文件v2收集指纹正确，但190.749s后在自有 `native_phase_trace_v2.py:20` AV：测试临时把 `shiboken6.isValid` 设为True，诊断仍用它保护C++指针读取。自有QObject探针确认删除后真实guard=False、临时替换=True，未读取已删除对象指针。**v2属于取证脚本失败，不作full4 AV的复现证据**；旧日志/trace/receipt均保留。

**v3完整单进程通过**：10:32:13–10:47:38 +08:00，**4416 passed / 15 skipped / 15 warnings in 898.65s**，wall899.838s、exit0。4431项/262文件，顺序SHA与原全inventory完全一致，621源码未改；不筛选/新增skip，纯Python hook不调用Qt/Shiboken原生方法、不处理Qt事件或改生命周期。证据 `native-full-python-v3-results.json` / `R06-native-full-python-v3.log`；一次通过不证明先前AV已经根除。

- 无诊断插件的完整 `pytest -q -o tmp_path_retention_policy=failed` **再次失败**：10:52:29–11:10:22 +08:00，wall1046.989 s、exit3221225477（0xC0000005），621源码不变。日志已报告3599项（3586点/13skip），最后报告位置按inventory哈希校准为 `test_shared_watcher_tick_survives_idle_windows`，崩溃在teardown `WorkerSupervisor.wait_for_stopped_for_tests:645`；当前位置不是遗留资源owner的证明。证据 `plain-full-retention-failed-v1-results.json`。pytest仅清理本次成功临时目录，保留新根50,859 B及所有旧产物；两根当时11,617,936,845 B <11GiB。
- 自有pytest故障的Windows Application/1000记录指向 Qt6Core6.11.1 RVA `0xb2ff8`；本机PE导出/反汇编定位为 **QEventLoop::exec+0x28 的进入段读取**，而非已确认派发旧Qt事件。此前缺少寄存器/owner证据，不能据栈直接归因；下面入口取证现已捕获NULL线程字段，owner仍待定位。`tests/test_single_process_shared.py` 单独无插件-q **17 passed in3.57s**。
- 只读入口取证 `qt-entry-prefix-v1` **复现同一原生异常**：11:19:51–11:35:31 +08:00，907.564 s、exit3221225477，前3600项/220文件、621源码不变。在目标用例 teardown 的原始 exec 调用前，fresh loop 原始有效性=True，主线程 QThreadData 的 thread 字段已为 **NULL（ReadProcessMemory成功）**；此前同一数据地址的 thread 字段非NULL。这与Qt6Core RVA0xb2ff8入口读取吻合，排除了“已确认由排队事件派发导致”的旧猜测，但尚未证明谁销毁/清空主QThread。两根11,618,225,425 B <11GiB。证据 `qt-entry-prefix-v1-results.json` / `native-qt-entry-prefix-v1.trace.jsonl`。
- `qt-phase-prefix-v1` 已完成（11:40:17–11:52:10 +08:00）：684.519 s、exit3221225477、621源码不变。只读标量首次非NULL→NULL出现在完整收集第 **3139 项** `test_requested_regressions::test_modern_settings_save_warns_on_failure` 的 **call 前后**；同一主线程QThreadData一直保持该NULL，后续第3599项共享Worker清理入口才崩溃。该边界是定位线索，不证明当前用例就是错误owner。两根11,618,397,923 B。
- 仅 requested regressions + shared触发用例两次诊断均通过：43 passed /1 skipped（27.41 s；加非持有 destroyed观察后29.84 s），不能替代默认全量，也未复现前序污染。
- `qt-owner-prefix-v1` 被用户中断：无完成回执，日志停于46%并有两个F标记，未取得失败详情；不分类为产品新失败/通过/原生复现，保留原日志与新测试临时根。工具会话已不可重新加入；GDB进程名限定查询无残留。
- `qt-destroyed-prefix-v2` 已完成：13:44:06–13:53:47 +08:00，前3145项 **3133 passed /12 skipped /1286 deselected /10 warnings /514.28 s**，wall519.093 s、exit0、621源码不变。主线程字段未变NULL，弱 destroyed/GC观察可能改变绑定或时序，因此不作为根因修复或默认全量门。进程正常退出时 app.destroyed 回调尝试写已关闭句柄，产生诊断 ValueError，明确是观察器收尾缺陷，非产品失败；原记录保留。两根11,618,689,763 B。
- 此前在新自有pytest子进程执行、现已结束的 `qt-native-watch-prefix-v1`（前3600项），使用不连接Qt信号的只读phase入口记录与一个原生硬件观察点，捕获谁将主线程字段写NULL。GDB仅配置自有子进程调试寄存器，不写Qt对象/改变删除/刷事件，不附加用户进程。GDB退出码不等于pytest通过；必须读取原生写入位置、完整测试结果。独立自然退出smoke已证实观察点可捕获 QThread 析构的非NULL→NULL写入，退出0；两次工具路径/嵌入Python环境失败保留。
- 2026-10-08 原生硬件观察已完成：`qt-native-watch-prefix-v1-results.json` 记录 1097.382s、621 源码指纹未变、占用 11,618,873,756B。自有 pytest inferior 在 `test_modern_settings_save_warns_on_failure` call 内由 Python GC → `SbkDeallocWrapper` → QtCore.pyd 虚析构派发 → `QThread::~QThread` 清零主线程数据字段（写入 RVA `0x1a49b3`）；随后自然退出 `0xC0000005`。GDB exit 0 不是 pytest PASS。精确调用栈与已证明/未证明边界写入 `qt-native-write-evidence-v1.json`；尚需查明错误销毁权的来源，不以保活引用掩盖。
- `qt-gc-owner-prefix-v1` 已完成：14:26:07–14:38:06 +08:00，前3139项 **3127 passed /12 skipped /1292 deselected /10 warnings /599.86 s**，wall601.880 s，621源码相同。观察到已存在主线程 wrapper 在 GC start 仍为 `ownedByPython=False`，referrers 含 QApplication；没有 NULL 写入。GC 枚举临时引用可能影响时序/回收，**不能作为根因排除或全量通过**，不据此加保活补丁。两根11,621,407,906 B。
- 当前执行新自有进程 `run_qt_owner_scalar_watch_prefix_v1.py`：本机已核实 `BindingManager::retrieveWrapper` C++ header/export，以 `ctypes.PyDLL` 在 GIL 内读取借用 raw pointer，只保留整数；不枚举 GC、不生成/持有 Qt wrapper、不连接信号、不刷事件。首次找到已有主线程 wrapper 后，在自有 GDB inferior 上同时硬件监视所有权低位与主线程 NULL 写入，读取原生调用栈与真实 wrapper 类型。前3139项为诊断，不是全量门；session55264。
- 2026-10-08 15:10 原生门根因已缩小：公开 QObject/thread 创建顺序最小复现 3×3 次 0xC0000005，正常 parent/child 对照 3 次 exit0；真实 FeatureLifecycleEndpoint.register_draft + QueuedFeatureLifecycle.prepare 的循环回调 seam 连续 3 次崩溃（`qt-feature-lifecycle-draft-public-minimal-v1-results.json`）。主线程 wrapper 的 QObject.thread() Python parent heuristic 与 GC clear/dealloc 交互是已证明机制；当前计划先补独立进程 red，再将该 GUI 防自等待判断改为 endpoint 创建时的 Python owner-thread 身份（与 FeatureHost.registry 同一约束），保留 queued signal/后台 wait/撤权顺序，不用全局 keeper、private bit 写入、全局刷事件或新 skip。Shiboken 6.11.2 单库混合诊断仍 3 次崩溃，不作为受支持升级或交付；全局 Python 环境未改。scalar-watch-v1 已自然结束，3127p/12s/1292d/10w/1226.29s 后 inferior 0xC0000005，GDB exit0 不是 PASS。准备的 v2 大前缀不再作为下一步。
- 2026-10-08 15:12 生命周期 public-seam 回归完成 red→green：独立子进程的循环 draft 收集测试原版 exit0xC0000005（red 1 failed /6.63s），改为 endpoint 创建时 `threading.get_ident()` 后，真实 GUI/background/IPC/shared Worker 族 **23 passed /8.48s、自然 exit0**；回归 3 个创建/GC/新 QEventLoop 轮次都实际收集 Endpoint，未加主线程 keeper。初次 green-v1 文件名选择错误、green-v2 CRLF 拼接未命中造成 NameError，日志保留，均未冒充通过；正确实现为 green-v3。无 Qt 包升级。下一步执行完整未加诊断插件的 4432 项单进程 -q，全量完成前不修改产品/测试输入；随后 Core/Setup 精确受影响重建与3轮满 CPU 族。

<!-- R_NATIVE_DIAGNOSTIC_END -->

</details>

可复现（Windows x64/Python3.11.1/PySide6与Qt6.11.1，QT_QPA_PLATFORM=offscreen；新basetemp，不覆旧证据）：

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONIOENCODING='utf-8'
$python='E:\Program Files (x86)\Dev-Cpp\python.exe'
& $python -m pytest -q tests/test_phase5a_repair_regressions.py tests/test_core_api_config.py tests/test_core_api_consumers.py tests/test_core_api_migration_ui.py tests/test_core_api_balance_lifecycle.py tests/test_core_maintenance_entry.py
& $python -m pytest -q
& $python -u .scratch/phase5a-local-distribution/repair-20261007/highload_repair_v5.py
& $python -m ruff check pet features scripts tests packaging
& 'D:\DELL\Git\cmd\git.exe' diff --check
```

实际有证据runner拒绝覆盖旧回执，重现应使用新命名自有输出目录。成功临时目录仅由pytest标准failed保留策略清理本次新根，旧失败/包/日志未删除。

<!-- R_LOAD_RESULTS_START -->
最新v5（15族，中央配置/消费者/余额/迁移、聊天、手动自动识屏、Worker启动/进程、独立设置、Core维护、租约、修复回归及生命周期/shared-app）：

| 轮次 | pytest输出 | wall s | 全机CPU中位 / 均值 | 结果 |
|---|---|---:|---:|---|
| 1 | 152 passed in 232.92s (0:03:52) | 234.876 | 100.0% / 99.98082% | exit0 |
| 2 | 152 passed in 236.93s (0:03:56) | 238.834 | 100.0% / 99.96577% | exit0 |
| 3 | 152 passed in 159.66s (0:02:39) | 160.806 | 100.0% / 99.99645% | exit0 |

20 logical CPUs、20自有worker逐个CPU affinity绑定，未改系统/外部进程优先级、电源或代理；实测CPU中位每轮100%。621输入前后相同。multiprocessing Event停止、自然join，20退出码均0、残留[]，没有kill/terminate。原v1路径exit4、v2非饱和、v3三轮129、v4第三轮失败原证据全保留；当前门仅引用 `highload-v5-results.json` /三轮log。
<!-- R_LOAD_RESULTS_END -->

### 8.3 修改文件说明（累计相对HEAD，含原T/用户dirty）

本轮累计 **140路径（115修改+25新增，无删除）**；其中119 Python、19 Markdown与2 Inno脚本。进入R前78dirty路径保留；以下数字是实际 `git diff --numstat` 加未跟踪文件行数，未暂存、未提交，不将全树归为R新增。generated EXE/ZIP/截图/私钥/用户数据未列入。

<!-- R_FILE_TABLE_START -->
| 文件 | 状态 | + / − 行 | 改了什么 + 为什么 |
|---|---|---:|---|
| `.scratch/phase5a-local-distribution/HANDOFF.md` | 修改 | +471 / −0 | 更新同组R当前权威状态、最新默认全量/三轮负载/当前Core与新Setup、人工准确下一步；保留T历史，WORKLOG另保留R过程失败；明确工程验证与用户确认、未提交发布。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | 修改 | +378 / −0 | 更新同组R当前权威状态、最新默认全量/三轮负载/当前Core与新Setup、人工准确下一步；保留T历史，WORKLOG另保留R过程失败；明确工程验证与用户确认、未提交发布。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | 修改 | +407 / −0 | 更新同组R当前权威状态、最新默认全量/三轮负载/当前Core与新Setup、人工准确下一步；保留T历史，WORKLOG另保留R过程失败；明确工程验证与用户确认、未提交发布。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | 修改 | +379 / −0 | 更新同组R当前权威状态、最新默认全量/三轮负载/当前Core与新Setup、人工准确下一步；保留T历史，WORKLOG另保留R过程失败；明确工程验证与用户确认、未提交发布。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | 修改 | +521 / −0 | 更新同组R当前权威状态、最新默认全量/三轮负载/当前Core与新Setup、人工准确下一步；保留T历史，WORKLOG另保留R过程失败；明确工程验证与用户确认、未提交发布。 |
| `CONTEXT.md` | 修改 | +18 / −0 | 补充 Core 中央 API、通用用途授权和 Core-only 系统卸载的领域归属；防止后续把凭据与卸载前置重新耦合到官方 DLC。 |
| `LOG-INDEX.md` | 修改 | +1 / −0 | 登记R交付工程日志/报告/交接入口及未确认边界，便于跨对话直接找到当前证据而非旧夜间暂停。 |
| `LOG.md` | 修改 | +10 / −0 | 新增同一R交付日期记录，默认原生根因红绿/最新全量负载/受影响重建与人工停点，保留旧施工与失败，不预写用户验收或提交。 |
| `README.md` | 修改 | +7 / −1 | 替换过期当前入口为R候选/最新默认全量与满CPU/新Setup及人工边界，旧日期状态显式保留为历史；不把工程通过称为Phase5A关闭。 |
| `SPEC.md` | 修改 | +13 / −3 | 固化用户确认的Core中央API/通用用途端口、请求快照/手动识屏、Core-only保留合同和4.2.2候选验收标准；旧合同数字留历史、Phase6不自动开放。 |
| `docs/INDEX.md` | 修改 | +9 / −5 | 同组报告索引指向最新单进程/三轮满CPU/新产物工程证据与用户待验；不再沿用已解决原生AV为当前阻塞。 |
| `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md` | 新增（未跟踪） | +294 / −0 | 保持T历史和R01–R07授权验收条件，记录真实原生修复/最新全量负载/新构建/11.25GiB，区分工程完备与用户未确认。 |
| `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md` | 新增（未跟踪） | +152 / −0 | 准确交付中央API/授权快照/Core-only ABI和最新Core/Setup hash、运行/性能、人工步骤；旧原生调试不再是当前下一步。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md` | 修改 | +341 / −1 | 保留并整理先前工作树已有验收证据和未测边界；不将其历史数字冒充本轮验证。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md` | 新增（未跟踪） | +708 / −0 | 同一§8保留T/原生/负载/脚本失败历史，补最新默认4417/三轮152、当前6产物hash、140逐文件行数、双路径实测性能和新冻结/人工范围。 |
| `docs/PROJECT-ENTRY.md` | 修改 | +9 / −3 | 当前入口同步Core中央API/Core-only合同、最新4417默认全量/3×152满CPU和setup-lifecycle候选；真正未测为真实用户门。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | 修改 | +144 / −15 | 更新同一R设计状态/11.25GiB预算/最新单进程与三轮证据，保持中央API/手动识屏/Core-only合同及T历史，未预写正式收尾。 |
| `docs/plugin-phase-05-distribution/README.md` | 修改 | +15 / −9 | 阶段入口更新R候选、新全量/负载/构建与真实用户边界，提醒新Setup更新旧卸载器；T历史不作当前证明。 |
| `docs/plugin-phase-06-ecosystem/README.md` | 修改 | +7 / −5 | 通用授权端口不等于公开SDK/Python沙箱；R工程门通过后仍等待用户，Phase6目录/市场/签名生态条件不自动解除。 |
| `features/ai_chat/host/chat/ai_settings_page.py` | 修改 | +11 / −0 | 新版 DLC API 编辑入口显示中央绑定并跳转 Core；保留采样、提示词和业务草稿，避免维护第二份地址/Key。 |
| `features/ai_chat/host/chat/legacy_widgets.py` | 修改 | +20 / −4 | 经典聊天在发送前解析中央最新配置并保留失败输入；为在飞请求接入授权取消/迟到结果门，旧 DLC 路径保持兼容。 |
| `features/ai_chat/host/chat/models.py` | 修改 | +3 / −0 | 增加不落盘且 repr 隐藏的请求期密钥与授权版本字段；明确业务配置序列化边界，避免秘密与请求授权身份持久化。 |
| `features/ai_chat/host/chat/providers.py` | 修改 | +4 / −3 | 网络请求消费不可变中央快照并区分授权/网络错误；入口、入网前和结果接纳校验原 grant，防止撤权后迟到结果被接受。 |
| `features/ai_chat/host/chat/request_config.py` | 新增（未跟踪） | +33 / −0 | 新增四聊天/文件共享的请求前解析、异常映射与在飞授权守卫；prepared grant 身份固定，防止撤权再授权复活旧请求。 |
| `features/ai_chat/host/chat/service.py` | 修改 | +35 / −6 | 新请求重新解析最新提交配置，普通保存不改在飞请求；支持撤权/停用时取消并保留正确的用户输入与会话状态。 |
| `features/ai_chat/host/chat/settings_dialog.py` | 修改 | +29 / −2 | 旧经典 AI 设置的 API 区改为中央服务状态与跳转，连接探针不等同提交；提示安全存储失败且保留旧兼容路径。 |
| `features/ai_chat/host/chat/widgets.py` | 修改 | +20 / −4 | 现代聊天与灵动岛入口接入请求前最新快照、失败保留输入和 grant 守卫；窗口更新通过 queued 通知而不依赖重启。 |
| `features/ai_chat/host/config.py` | 修改 | +65 / −3 | 提供中央文字/文件用途解析与授权版本，不把临时密钥写回业务 namespace；旧接口 None 时保持原配置路径。 |
| `features/ai_chat/host/file_interpret.py` | 修改 | +12 / −9 | 文件解读使用独立 files.interpret 用途绑定和授权守卫；缺配置/授权在执行前失败，不绕用聊天用途。 |
| `features/ai_chat/host/quick_chat.py` | 修改 | +9 / −2 | 快捷聊天在每次发送前解析最新已提交配置，保留无效配置时的输入；撤权/停用取消任务并拒绝迟到结果。 |
| `features/ai_chat/host/runtime.py` | 修改 | +54 / −3 | 运行时接入通用 API 端口和变更订阅，刷新已有窗口而不覆盖草稿；所有新任务仍独立解析，退出时解除订阅。 |
| `features/screen_understanding/common/models.py` | 修改 | +30 / −0 | 保持视觉无凭据业务配置与请求期最小快照分离，中央配置可带 TLS/路径；不能把 Key 写入共享业务 JSON 或绑定 AI 存在。 |
| `features/screen_understanding/host/config.py` | 修改 | +51 / −3 | 手动 manual_look 与自动 analyze_frame 按用途读取 Core 快照；只向该次 Worker 请求提供所需数据，不枚举全部密钥。 |
| `features/screen_understanding/host/factory.py` | 修改 | +1 / −1 | 向识屏 host 注入可选通用 API 与配置解析，不新增官方 factory 特权；保持旧 Core 兼容路径。 |
| `features/screen_understanding/host/manual.py` | 修改 | +20 / −1 | 手动识屏读取其授权用途，区分配置、Worker 启动/截图/网络/取消提示；自动关闭不阻塞手动，输出诊断脱敏。 |
| `features/screen_understanding/host/migration.py` | 修改 | +40 / −0 | 保留显式授权的缺失凭据引用修复、空值与源校验；不覆盖已有视觉配置。 |
| `features/screen_understanding/host/runtime.py` | 修改 | +34 / −9 | 拆分自动策略与手动生命周期、按用途比较配置/授权版本；自动关闭/空名单/无关刷新不推进共享 generation 或误取消手动。 |
| `features/screen_understanding/host/settings.py` | 修改 | +75 / −59 | 新版 API 区显示中央用途绑定并跳转 Core，保留手动/自动业务草稿和白名单/预算；旧 Core 接口兼容，不隐式搬用 AI Key。 |
| `features/screen_understanding/host/worker_adapter.py` | 修改 | +3 / −1 | 适配独立 Worker 的请求期配置与按请求取消，重试获得新租约；把启动、握手、主动取消和网络故障分开呈现。 |
| `features/screen_understanding/host/worker_diagnostics.py` | 新增（未跟踪） | +38 / −0 | 新增脱敏 Worker 启动阶段/退出码/缺模块解释与可操作提示；不泄露临时凭据、不用 Core 回退掩盖冻结故障。 |
| `features/screen_understanding/worker/runtime.py` | 修改 | +4 / −0 | Worker 接纳 host 传入的最小不可变用途快照并正确返回取消/阶段错误；不加载 Core GUI 或回退在 Core 内执行。 |
| `features/screen_understanding/worker/vision.py` | 修改 | +25 / −18 | 视觉请求消费中央 endpoint/path/model/TLS 与临时 Key，错误脱敏；保留 Provider 执行在 Worker 的进程边界。 |
| `packaging/core_webm.iss` | 修改 | +76 / −13 | 内嵌两个经预检的官方 ZIP，临时解包后调用无 GUI 安装维护并校验返回码/日志；不依赖 {src}\packages。 R 轮系统卸载文案/入口改为仅 Core，保留所有 DLC 副本和源包；Inno 只做一次卸载确认。 |
| `packaging/dsh-pet.iss` | 修改 | +1 / −1 | 更新安装候选版本为 4.2.2，与 Core/新 DLC 的最低版本一致；避免不同产物复用旧版本号。 |
| `packaging/phase4a_validation_entry.py` | 修改 | +7 / −7 | 在 GUI 初始化前统一分流 Core 维护入口；维护行为不走旧有 QApplication/授权弹窗路径。 |
| `packaging/phase4b_manual_entry.py` | 修改 | +7 / −0 | 手动验收变体也早期识别维护命令；与冻结 Core 实机矩阵使用同一产品入口。 |
| `pet/__init__.py` | 修改 | +1 / −1 | Core 版本提升为 4.2.2，和冻结 Setup 及新公共 API 合同一致；不覆盖旧 4.2.1 内容身份。 |
| `pet/__main__.py` | 修改 | +13 / −5 | 增加有界维护参数解析和早期分流；确保 install-packages 不创建 QApplication 或正常启动弹窗。 |
| `pet/api_config.py` | 新增（未跟踪） | +330 / −0 | 新增 Core 多服务/用途授权模型、执行检查和安全存储引用；CAS 原子提交、prepared journal/replay 与 endpoint 重新授权保证失败闭合。 |
| `pet/api_migration.py` | 新增（未跟踪） | +150 / −0 | 新增旧 AI/手动视觉/自动视觉/余额的非敏感预览和显式逐来源确认；用来源摘要/CAS/重放保持冲突草稿及旧密钥。 |
| `pet/api_notifications.py` | 新增（未跟踪） | +93 / −0 | 新增同进程 queued 变更和跨设置进程 500ms 版本检查；请求前解析兜底，订阅关闭解除 timer/signal，不覆盖用户草稿。 |
| `pet/api_ports.py` | 新增（未跟踪） | +41 / −0 | 新增通用不可变 API 元数据/请求/用途版本/授权身份端口；临时 Key repr 隐藏，不暴露全局 Config 或凭据枚举。 |
| `pet/api_probe.py` | 新增（未跟踪） | +47 / −0 | 新增显式最小文字连接探针、TLS/超时/错误脱敏；可能少量费用、不自动截图、不提交服务也不证明视觉/余额可用。 |
| `pet/app.py` | 修改 | +37 / −16 | 在应用启动发现已安装通用 owner 并注册 manager/生命周期；未知包不再遗漏到官方固定循环之外。 R 轮装配 Core 中央 API，向 DLC 通用注入并按撤权/停用取消请求；正常退出关闭订阅。 |
| `pet/balance.py` | 修改 | +12 / −0 | 余额读取 Core 余额用途绑定并沿用 DeepSeek 协议适配器，校验授权/服务版本并脱敏错误；不再要求重复 Key。 |
| `pet/balance_config.py` | 修改 | +24 / −1 | 增加中央余额绑定的有效配置解析，区分未授权、未配置与不支持协议；兼容旧配置而不静默合并凭据。 |
| `pet/config_transaction.py` | 修改 | +4 / −3 | 普通 Config 保存保留磁盘权威中央 API namespace 和其他 owner；防止旧窗口草稿回写覆盖新服务/版本。 |
| `pet/core_maintenance.py` | 修改 | +168 / −42 | 增加只接受显式官方选装集合的 preflight/apply、重复安装/重新启用与 JSONL 返回记录；Setup 自动启用而非只预检。 R 轮系统卸载改为 CoreRemovalPermit，保留身份/程序锁/删除门和范围内集成清理，不执行 DLC 工厂/事务或 staging/ledger 门。 |
| `pet/credentials.py` | 修改 | +3 / −2 | 凭据 owner namespace 接受合法本地标识，保持 owner 隔离；不读取或迁移真实用户 Key。 R 轮提供 Core 服务的独立安全存储 namespace 和安全引用，不明文回退或自动删除旧 Key。 |
| `pet/feature_build_policy.py` | 修改 | +6 / −3 | 保留显式 local activation 构建开关与官方 capability ceiling；不放宽 signed-only 构建。 |
| `pet/feature_config.py` | 修改 | +5 / −2 | 本地配置 namespace 以合法 owner 校验替代 official lookup；通用包仍不能跨 owner 读写。 R 轮支持中央 prepared journal/CAS 与故障重放，命名空间提交仍受数据根门保护。 |
| `pet/feature_host_bindings.py` | 修改 | +113 / −13 | 增加 descriptor 驱动的本地 host/worker 绑定和最小上下文；不给未知 host desktop/window/legacy 凭据。 R 轮所有 local host 使用可选 api，授权由执行+用途逐请求检查而非官方 owner 决定。 |
| `pet/feature_install_state.py` | 修改 | +9 / −3 | 泛化状态 owner 校验，保留 revision/digest/事务字段合同；避免安装落盘时再次被官方白名单拒绝。 |
| `pet/feature_lifecycle.py` | 修改 | +8 / −2 | 将HEAD既有GUI防自等待判断改为endpoint创建时Python线程身份，避免QObject.thread借用main wrapper被循环GC错误析构；queued/取消/后台wait不变，不用全局keeper。 |
| `pet/feature_lifecycle_contract.py` | 修改 | +2 / −3 | 生命周期 owner 合同泛化到合法本地标识；依然隔离跨 owner 的关闭/草稿操作。 |
| `pet/feature_management.py` | 修改 | +186 / −10 | 增加中性目录/ZIP路由、按 owner 创建/发现管理器和异步确认绑定应用；管理不再把 AI 或第三方送到 Screen。 |
| `pet/feature_management_ui.py` | 修改 | +144 / −14 | 统一 ZIP/目录选择、动态第三方卡片与提示，承接设置页装配 helper；保留回调销毁和 line budget。 R 轮单包卸载明确“删除已安装副本、保留原始 ZIP/源目录及个人数据”，与 Core 卸载分离。 |
| `pet/feature_package_probe.py` | 修改 | +2 / −2 | 启动探针按 verifier.accepts_descriptor 校验激活策略；本地授权包不再硬要求 trusted_official。 |
| `pet/feature_package_startup.py` | 修改 | +5 / −6 | 按 descriptor.execution_kind 启动、泛化 owner 并保留 startup receipt；未知 owner 可跨重启确认。 |
| `pet/feature_package_transactions.py` | 修改 | +10 / −8 | 事务验证与 owner 约束改用中性策略；保留路径/清单/哈希、锁、回滚和 lease 安全门。 |
| `pet/feature_ports.py` | 修改 | +3 / −0 | 在 FeatureHostContext 增加可选通用 api 端口、保留旧位置/None 兼容；第三方与官方使用相同最小接口。 |
| `pet/feature_probe_adapter.py` | 修改 | +6 / −5 | 将 parent-owned local activation 策略送到隔离 helper并按策略复验快照；不由候选包决定信任政策。 |
| `pet/feature_probe_crypto.py` | 修改 | +6 / −4 | 更新 headless verifier 的 local/signed 双策略说明；libsodium 兼容仍受构建封存输入约束。 |
| `pet/feature_probe_materials.py` | 修改 | +4 / −2 | 探针材料 owner 与 descriptor 按中性标识验收；保留封存、快照和恢复边界。 |
| `pet/feature_probe_windows.py` | 修改 | +1 / −1 | Windows 隔离启动的 owner 校验接受合法本地 ID；不移除 LPAC/Job/封存检查。 |
| `pet/feature_startup_contract.py` | 修改 | +3 / −2 | 启动上下文 owner 检查不再绑定官方集合；保留 runtime 类型和 owner 一致性。 |
| `pet/feature_version_lease.py` | 修改 | +2 / −4 | 租约 owner 使用通用标识；真实进程退出才释放代码占用，仍不承诺 Python 热卸载。 |
| `pet/local_package_intents.py` | 修改 | +201 / −9 | 受限 manifest 预读、自动注册/路由和选择确认令牌；从指定包识别 owner 而非沿用官方列表。 |
| `pet/modern_settings_dialog.py` | 修改 | +29 / −30 | 装配统一扩展管理入口，声明新 row 的 capability claim，并让 extensions 深链接聚焦 ZIP；保持原 line budget。 R 轮 AI 与对话域内增加无 DLC 的 Core API 服务页并保留 settingsScroll 深色 token；实际 2380 行，未超过 2395 预算。 |
| `pet/official_features.py` | 修改 | +40 / −5 | 引入中性 FeatureRegistration、ID/factory 校验与兼容别名；官方注册仅提供额外策略，不是本地集合。 R 轮独立 Worker 只收集中性 registration/身份依赖；该模块名字不代表运行时官方授权旁路。 |
| `pet/plugins/feature_host.py` | 修改 | +21 / −0 | 包装通用 API 端口，对每次 metadata/resolve 检查当前 DLC 执行授权；停用立即不可用，不依赖旧工厂身份特判。 |
| `pet/plugins/feature_packages.py` | 修改 | +7 / −4 | loader 使用激活策略、descriptor 与中性 owner 合同；signed-only 验证仍可拒绝同一无签名包。 |
| `pet/plugins/package_trust.py` | 修改 | +103 / −19 | 区分有界预读、local 与 signed-only 全验；未知 v1/v2 owner/factory 可注册，保留官方 ceiling/兼容/路径/哈希限制。 |
| `pet/runtime_layout.py` | 修改 | +4 / −2 | 运行目录 namespace 接受合法通用 owner，并继续拒绝路径穿越；避免持久化命名空间隐含 official-only。 |
| `pet/screen_understanding/host_binding.py` | 修改 | +4 / −4 | 旧完整构建的识屏绑定明确 api=None，维持兼容配置路径；避免意外把旧入口强制迁入新版中央服务。 |
| `pet/settings_api.py` | 新增（未跟踪） | +504 / −0 | 新增未装 DLC 也可用的 Core API 服务编辑/授权/显式迁移页；多服务、掩码 Key、后台最小探针、CAS 冲突保草稿和设置退出门。 |
| `pet/settings_balance.py` | 修改 | +7 / −14 | 余额设置显示当前中央服务/协议并跳转 Core API 页，保留旧兼容入口；不再鼓励为同一 Key 维护两份配置。 |
| `pet/window.py` | 修改 | +11 / −0 | 保留键盘语义上下文菜单的稳定身体锚点；实机探针可仅向自有窗口发 WM_CONTEXTMENU、不抢焦点。 |
| `scripts/build_core_webm_setup.py` | 新增（未跟踪） | +83 / −0 | 新增 canonical Setup 编译入口和官方包预检；只有 manifest/兼容/哈希通过才调用 ISCC。 |
| `scripts/build_feature_management_delivery.py` | 修改 | +32 / −19 | 交付构建以 explicit local activation policy 和当前 helper snapshot 为输入；不再把签名密钥作为本地发行前置。 |
| `scripts/build_feature_management_manual.py` | 修改 | +63 / −15 | 手动实机变体同步无密钥本地构建与 helper 输入；保留不注册系统自启动的验收边界。 |
| `scripts/build_feature_probe.py` | 修改 | +31 / −15 | 保留直接执行 import 路径修复和 headless module 排除（含提前网络初始化的 multiprocessing）；让真实 LPAC verifier 可启动。 R 轮 whole features 明确排除，防止公共端口依赖引入 AI/Screen payload 或 GUI/vault。 |
| `scripts/build_feature_probe_native.py` | 修改 | +3 / −0 | 保留 Py_SetPath 分号边界拒绝；避免 native helper 搜索路径被截断/扩展。 |
| `scripts/build_feature_release.py` | 修改 | +7 / −2 | 支持 Core 4.2.2 语义版本、共享 Worker 闭合清单并强制真实正常启动门；probe 明确收集公共 api_ports，避免冻结依赖漏项。 |
| `scripts/build_screen_delivery.py` | 修改 | +198 / −45 | 生成 GUI Core spec，读取 PE subsystem，支持当前 local helper/源码和资源封存；保留 Core 与 Worker 的不同窗口策略。 |
| `scripts/build_screen_worker.py` | 修改 | +6 / −0 | 两条构建路径共享精确源码/必备 PYZ 清单，补 neutral official_features/api_ports；排除 GUI、AI host、vault，不整包收 Core。 |
| `scripts/feature_probe_entry.py` | 修改 | +16 / −3 | 严格解析 parent-owned allow_local_packages，兼容旧 signed-only 输入；使用同一 verifier 而非绕过签名/完整性。 |
| `scripts/feature_release_materials.py` | 修改 | +6 / −2 | 列明 Core API 必备输入与新包 minCore >=4.2.2，保持源码/manifest 可核验；避免冻结构建与源码配置脱节。 |
| `scripts/validate_phase5a_delivery.py` | 修改 | +39 / −15 | 保留进程身份约束的实机驱动，更新内嵌选装/无密钥与正常退出判据；不把未测系统安装填成通过。 |
| `scripts/validate_phase5a_setup.py` | 新增（未跟踪） | +96 / −0 | 新增有界的 Setup 官方包合同验证；安装分发不接受任意本地包或异常官方 execution/capability。 |
| `scripts/validate_screen_worker_startup.py` | 新增（未跟踪） | +169 / −0 | 新增生产冻结 Worker 的实际租约交接、HELLO/READY、正常停止/自然退出/占用回收硬门；不用 sandbox probe 替代。 |
| `tests/_feature_ui_child.py` | 修改 | +6 / −2 | 将统一入口深链接/Tab 焦点更新到 ZIP→目录，同时保留 Screen 卡片自身的原生键盘可达性检查。 |
| `tests/test_agent_registration_scope.py` | 修改 | +2 / −3 | 用 CoreRemovalPermit 验证 Core-owned Agent 集成清理，而非要求逐包卸载证明；保留安装身份与路径范围负例。 |
| `tests/test_ai_host_contracts.py` | 修改 | +1 / −1 | 验证中央 API 注入、请求最新配置、旧接口兼容和秘密不落业务 JSON；守住 AI 执行仍属 DLC 的边界。 |
| `tests/test_build_feature_release.py` | 修改 | +7 / −0 | 增加 4.2.2 版本及必备输入/Worker 门回归；保留源版本 fixture 不被构建脚本改写的断言。 |
| `tests/test_core_api_balance_lifecycle.py` | 新增（未跟踪） | +86 / −0 | 覆盖余额复用 Core Key、协议不支持、在飞撤权与错误提示；不把余额缺授权误判为联网失败。 |
| `tests/test_core_api_config.py` | 新增（未跟踪） | +178 / −0 | 测试通用 owner/purpose 隔离、secure vault 失败无明文、CAS/journal/replay/endpoint 改址重新授权；加入第三方而非官方特判。 |
| `tests/test_core_api_consumers.py` | 新增（未跟踪） | +280 / −0 | 验证现代/经典/快捷/灵动岛与文件解读新请求即时生效、失败输入保留和授权快照；普通变更与撤权取消有不同结果。 |
| `tests/test_core_api_layout.py` | 新增（未跟踪） | +39 / −0 | 四种 720/1100 浅深主题下检查 Core API 布局/滚动/标准 token；防止深色 QScrollArea 回落成白色背景。 |
| `tests/test_core_api_migration_ui.py` | 新增（未跟踪） | +280 / −0 | 验证显式预览/提交/CAS 冲突/草稿保护、旧 Key 保留和探针不保存；安全存储只模拟不可确定的 OS 边界。 |
| `tests/test_core_balance_config.py` | 修改 | +11 / −0 | 验证中央余额用途/DeepSeek 适配和 unsupported 与 missing 区分；旧余额配置仍可显式迁移。 |
| `tests/test_core_maintenance_entry.py` | 修改 | +16 / −0 | 覆盖维护在 Qt/app 初始化之前分流与无 GUI 入口；安装路径不能误触正常 UI。 R 轮覆盖两来源、staging/pending/ledger、Core 占用及清理失败，断言 DLC/配置/源包不变。 |
| `tests/test_core_registration_cleanup.py` | 修改 | +10 / −6 | Core-only 许可覆盖自启动/桥接清理路径与权限失败；不把 DLC ledger 干净作为系统删除条件。 |
| `tests/test_core_setup_template.py` | 修改 | +17 / −2 | 覆盖内嵌包、temp 解包/维护、禁止外部旁置依赖；更新模板责任而非移除门禁。 R 轮断言只调用 Core-only 删除许可、不进行逐包卸载/恢复。 |
| `tests/test_feature_lifecycle.py` | 修改 | +41 / −0 | 保留既有生命周期测试，新增真实子进程的注册cyclic draft/公开prepare/实际GC后新QEventLoop三轮回归，原版红0xC0000005后绿；不是skip/全局清队列。 |
| `tests/test_feature_management.py` | 修改 | +15 / −0 | 覆盖从 Screen 管理表面选择 AI 的自动 owner 路由；错误 package 不污染 Screen 状态。 |
| `tests/test_feature_management_build.py` | 修改 | +36 / −1 | 覆盖本地策略 snapshot 和冻结 GUI subsystem 的构建合同；无需本地密钥。 |
| `tests/test_feature_management_ui.py` | 修改 | +2 / −2 | 将 extensions 深链接焦点断言同步中性导入入口；保留多矩阵、草稿、异步生命周期测试。 |
| `tests/test_feature_manual_acceptance.py` | 修改 | +35 / −0 | 覆盖 manual 变体本地构建、独立视觉配置/缺 Key 的可解释验收行为。 |
| `tests/test_feature_package_probe.py` | 修改 | +3 / −3 | 覆盖新公共端口在 sandbox probe 的冻结依赖及回执形状；不把工厂 probe 当成生产 Worker READY 证明。 |
| `tests/test_feature_probe_build.py` | 修改 | +30 / −0 | 覆盖 helper headless exclusions/直接导入与封存输入；防止 LPAC 启动前拉起网络/GUI 依赖。 |
| `tests/test_feature_runtime_ports.py` | 修改 | +11 / −1 | 验证第三方可选 API、用途隔离与执行停用门，使用统一 owner 接口；防止形成官方特殊授权分支。 |
| `tests/test_feature_version_lease.py` | 修改 | +20 / −11 | 保留T轮真实双进程fixture策略及占用/自然退出/清理断言；满CPU就绪迟达44.040s证据后将测试Event/join协调预算设90s并确保异常自然join，产品租约/timeout不变。 |
| `tests/test_feature_worker_handoff.py` | 修改 | +1 / −0 | Worker handoff fixture 显式使用本地激活，继续验证父/子 reservation 和自然退出所有权。 |
| `tests/test_official_feature_contracts.py` | 修改 | +6 / −2 | 覆盖新增本地入口 row 的 capability ownership，保留两个官方卡片独立责任。 |
| `tests/test_phase5a_delivery_acceptance.py` | 修改 | +71 / −14 | 覆盖更新后的实机驱动/选装组合、身份及正常退出判据；不靠 mock 宣称真实 Setup 执行。 |
| `tests/test_phase5a_local_activation.py` | 新增（未跟踪） | +58 / −0 | 新增无签名本地激活、同包 signed-only 拒绝与构建 policy snapshot 测试；两条信任路线互不污染。 |
| `tests/test_phase5a_repair_regressions.py` | 新增（未跟踪） | +226 / −0 | 首先建立四缺陷公开 seam red，再覆盖共享中央配置、Worker 依赖与 Core-only 卸载；防止只补文案而不修产品行为。 |
| `tests/test_phase5a_setup.py` | 新增（未跟踪） | +143 / −0 | 新增 Setup 包预编译/维护顺序、返回码、重复待启动安装回归；避免勾选只做 preflight。 |
| `tests/test_phase5a_t01_regressions.py` | 新增（未跟踪） | +369 / −0 | 新增 unknown owner/factory、AI 路由、Setup 内嵌、Core GUI 的 red 回归，再扩展本地 integrity/context/lifecycle 合同。 |
| `tests/test_proactive_worker_integration.py` | 修改 | +1 / −1 | 更新可解释的预算/错误码与手动重试租约合同，保留真实 Qt 和 Worker 进程边界；不通过固定 sleep 回避时序。 |
| `tests/test_runtime_layout.py` | 修改 | +5 / −2 | namespace 正例加入 third.party，反例改为 ../third.party；保留 scope 唯一性而非拒绝合法 generic owner。 |
| `tests/test_screen_ai_migration.py` | 新增（未跟踪） | +104 / −0 | 保留并验证显式授权且仅缺失凭据可修复的独立视觉迁移；不改写已有设置。 |
| `tests/test_screen_delivery_build.py` | 修改 | +94 / −1 | 覆盖新 Core spec/PE 和输入封存、local policy 及 helper 合同；构建事实不能仅看注释。 |
| `tests/test_screen_manual_host.py` | 修改 | +13 / −0 | 保留视觉缺 Key/不可用提示回归；未请求真实 Provider。 |
| `tests/test_screen_runtime.py` | 修改 | +2 / −2 | 补手动请求不受自动关闭/策略刷新误伤、用途版本取消、迟到结果及新租约重试；旧 generation 预期按新合同修正。 |
| `tests/test_screen_settings.py` | 修改 | +32 / −23 | 保留独立视觉配置 UI 与缺凭据状态合同；移除对隐式 AI 迁移入口的旧期望。 |
| `tests/test_screen_worker_boundary.py` | 修改 | +51 / −0 | 断言冻结 Worker 补齐中性身份依赖且不带 Core GUI/AI/vault；两构建路径输入集合一致。 |
| `tests/test_screen_worker_startup_gate.py` | 新增（未跟踪） | +52 / −0 | 独立 QApplication 子进程运行实际 Worker/租约/握手/自然退出，验证缺模块与失败诊断；降低全量 QApplication 生命周期交叉污染。 |
| `tests/test_settings_process_isolation.py` | 修改 | +45 / −30 | 设置进程 lease fixture 显式使用本地策略；继续真实 Qt/独立设置生命周期覆盖。 R 轮两独立设置 CLI 测试改用各自 QApplication 子进程，仍真实显示/退出/检查 settings.lock；避免与母进程已有 app.exec 混用。 |
<!-- R_FILE_TABLE_END -->

### 8.4 性能分析（真实测量与样本边界）

<!-- R_PERFORMANCE_START -->
**命令/环境/边界**：`E:\Program Files (x86)\Dev-Cpp\python.exe .scratch/phase5a-local-distribution/repair-20261007/measure_api_performance.py`，Windows x64、Python 3.11.1、PySide6 6.11.1、offscreen、20 logical CPUs。负载 worker 全退出后单独运行；新建自有 10,046 B 配置，真实 JSON、文件锁、journal/CAS、Qt event loop/500 ms timer。密钥用明确标注的 MemoryVault 假边界：**以下请求解析不含 Windows 安全存储、真实网络或 Provider 耗时**，不能当作端到端 API 延迟。每路径预热 10 次后取样。

| 路径 | n | 均值 / 中位 / p95（ms） | 样本内配置读盘 |
|---|---:|---:|---:|
| 已授权 metadata | 1000 | 0.2532 / 0.2475 / 0.2888 | 1000 |
| 用途 effective_version | 1000 | 0.2594 / 0.2513 / 0.3089 | 1000 |
| request immutable snapshot resolve | 1000 | 0.7984 / 0.7001 / 1.3117 | 2000 |
| 在飞授权 stamp guard | 1000 | 0.2547 / 0.2471 / 0.2956 | 1000 |
| 同 endpoint 配置原子保存（不更换 Key） | 50 | 48.3319 / 48.1930 / 61.9485 | 300 |
| 3 subscriber / 5 purpose 一轮 poll | 200 | 1.9260 / 1.8851 / 2.2070 | 1600 |

**稳态与触发频率**：普通 metadata/version/在飞 guard 各读配置 1 次；每个新请求 resolve 读 2 次以跨 vault 检查授权。原子保存仅显式提交触发，含 journal/替换写和读回，不随每次刷新写盘。3 订阅的 500 ms 策略每轮 8 次读；实际三个约 5 s 稳态窗口各 80 次、合计 240 次，约 **16 次读/s**（同尺寸 JSON 的逻辑数据量约 160,736 B/s；没有测内核实际 IO 字节/系统调用数，不能把两者混同）。

| 采样窗口 | 时长（s） | process CPU（s） | RSS 前→后（B） | 配置读 / 原生线程前→后 / Python 线程 |
|---|---:|---:|---:|---|
| 无订阅 1 | 5.010718 | 0.031250 | 54,616,064→55,259,136 | 0 / 5→5 / 1 |
| 无订阅 2 | 5.005883 | 0.000000 | 55,259,136→55,259,136 | 0 / 5→5 / 1 |
| 无订阅 3 | 5.003028 | 0.000000 | 55,259,136→55,259,136 | 0 / 5→5 / 1 |
| 三订阅 1 | 4.999048 | 0.031250 | 55,312,384→55,312,384 | 80 / 5→5 / 1 |
| 三订阅 2 | 5.000785 | 0.015625 | 55,312,384→55,361,536 | 80 / 5→7 / 1 |
| 三订阅 3 | 5.008102 | 0.000000 | 55,361,536→55,361,536 | 80 / 7→7 / 1 |

**新增资源与限制逐条说明**：
- 基线 15.019629 s 用 0.031250 CPU s，约单核 0.2081%；三订阅 15.007936 s 用 0.046875 CPU s，约单核 0.3123%。仅短样本、Windows CPU 时间粒度 15.625 ms，不据差值断言优化或确定归因。
- 不新增网络请求（实测 0）或 Python 后台线程（始终 1）；会增加配置读盘和 Qt timer。原生线程实测 **5→7**，来源未归因；不能声称“没有新增任何线程”。API 订阅实现本身只创建 QObject/QTimer，不能代替原生线程取证。
- 基线 RSS 窗口累计 +643,072 B，三订阅开始前比基线末 +53,248 B，三订阅窗口累计 +49,152 B；这只是约 15 s/15 s 的一次短测，不能证明长期零泄漏。
- OS secure store 成本、Provider/模型耗时、真实截图/内存、磁盘系统调用数量、跨进程长时稳定性没有纳入此表；真实 GUI profile 单样本启动/RSS/线程列于 §8.5。
- 所有自有 JSON 检查未含生成的明文假 Key；未读真实 Key。原始结果 `repair-20261007/api-performance.json` / `R07-api-performance.log`，exit 0。
<!-- R_PERFORMANCE_END -->

**生命周期guard前后实测**：`python .scratch/phase5a-local-distribution/repair-20261007/measure_lifecycle_performance.py`，同机/同PySide，负载/构建自然结束后，真实GUI公开prepare拒绝路径；每路径预热1000、7×10000次，perf_counter_ns批均值。旧baseline仅在测量内持有endpoint，并在释放前经app恢复借用wrapper正确owner，绝非产品keeper。

| 路径 | 批次数×次数 | 均值 / 中位 ns每次 |
|---|---:|---:|
| before_borrowed_qthread | 7×10000 | 774.554 / 697.720 |
| after_creator_identity | 7×10000 | 603.257 / 550.750 |

- 新路径成本/频率：仅管理生命周期prepare的GUI防自等待判断；创建endpoint时增加1个Python线程ID字段（int对象 28B），dict 272→272B；不新增聊天/视觉循环、Qt timer、订阅、网络/读盘或线程创建路径；新增Python线程身份查询为endpoint构造1次、每次prepare guard 1次，未采集内核系统调用数量。字段/字典浅尺寸不等同总内存。
- 本次RSS 49004544→49287168B、原生线程 5→5；短单进程微测，不证明长期无泄漏，不把批均值说成端到端/Provider/secure-store延迟。当前source SHA与快照相符，baseline/source SHA和7批原值存入lifecycle-performance.json。
- 首次PyInstaller成功EXE生成工具耗时205.246s，验证器选错dist路径失败；恢复同输入产物+完整bundle哈希/替换 107.075s；新Setup独立ISCC编译/核验 114.628s，各n=1，不称为性能提升。此前170.078s等为旧R构建历史，不当本次数字。
- 生产Worker HELLO2679.683ms /READY2696.877ms /自然退出总2739.376ms，n=1、0截图/0联网。Worker按所需请求冷启动，不是每帧常驻成本；完整截图/API耗时待人工。

### 8.5 实机运行记录（本机Windows、新Core、独立自有数据根）

实际运行当前 `.scratch/phase5a-local-distribution/c09/dsh-pet-core-webm.exe`，SHA `1413d5ef5aeb43de9aa693c3d7060a5849fa9b1c7ae8902c24a6c511a11c7264`；不是source/mock。unsigned/manual-acceptance-only只避免系统自启动登记，GUI/扩展管理/网络/Worker仍为产品路径。未运行真实Provider/截屏或系统安装器。

`frozen_lifecycle_reuse.py` 对四份已由R新版包真实安装的自有profile，**不重新导入/启用/重置事务**，新Core启动→UIA本进程可见菜单→点击退出自然0；启动前后逐包payload SHA与完整ledger完全相同。各n=1：

| Profile | 可见并完成启动确认 ms | RSS B | 原生线程 | 已安装payload文件 / 修改 | 退出码 |
|---|---:|---:|---:|---:|---:|
| empty | 7444.296 | 173977600 | 36 | 0 /0 | 0 |
| ai | 3470.104 | 222572544 | 32 | 30 /0 | 0 |
| screen | 3787.268 | 188948480 | 33 | 119 /0 | 0 |
| both | 5038.891 | 199659520 | 34 | 149 /0 | 0 |

AI1.0.2/Screen1.0.1仍enabled=true、revision=4、pending=null；empty无AI/识屏入口，单包/both按实际贡献。UIA仅本进程身份绑定，不读取密码值；4个Core自然退出、5个自有运行/设置PID及createTime/exe可复核。瞬时RSS/线程不是稳态泄漏/A-B。**这是新Core识别保留有效包，不是已执行系统卸载/重装**；真实两来源保留矩阵仍留用户。

旧 `frozen_repair_matrix.py` 的新根真实安装/启动证明针对旧R Core a22e146e…，作为历史保留（empty/AI/Screen/both正常退出0），不当新1413d5ef…的当前运行证明。原始normal.log/安装回执未覆盖，新运行使用normal-lifecycle命名。

无DLC的当前Core设置实机：`--settings --settings-page "AI 与对话"`，API服务/服务ID/API Key/保存按钮真实可见可用，3529.649ms到可见、3.780312s自然退出0；全新自有profile，不读password Value、不输/存Key、0网络/0截图。新回执 `frozen-settings-lifecycle.json`；旧错误sidebar ai的90s诊断超时保留。

API QWidget render（非桌面截图）在720/1100×浅/深×上/中/下12张已人工查看；标准settingsScroll/settingHint token、横向滚动0、密码掩码、按钮可达，深色4项red→green。这些UI源在最新621快照中未变；用户主观布局可反馈，渲染不代替Provider/真实屏幕。

生产Worker直接冻结EXE、非synthetic/probe：真实claim版本租约，HELLO/READY，正常STOP→exit0、occupancy_after_exit=free、diagnostics=[]、screen_requests=network_requests=0；Worker/evidence/normal-startup/startup.json对应当前闭合输入/PYZ。单工厂导入或沙箱probe不得替代正常启动硬门。

### 8.6 当前交付版本、输入核验与SHA-256

Core **4.2.2** /AI **1.0.2** /Screen **1.0.1**；新包minCore>=4.2.2。root207/stage212（5生成策略源，含manual entry与root单独校验）/resources1000/bundle2103逐项相符；Core PYZ2188、不含features/pet.chat、PE subsystem=2。完整c09 459475664B、最长绝对路径255；helper模块union198、不含features/keyring/PySide；Worker闭合源码/PYZ不带Core GUI/AI host/vault。最终只读审计 `lifecycle-final-build-audit.json`。

| 当前相对工作区产物路径 | Bytes | SHA-256 |
|---|---:|---|
| `.scratch/phase5a-local-distribution/c09/dsh-pet-core-webm.exe` | 17292328 | `1413d5ef5aeb43de9aa693c3d7060a5849fa9b1c7ae8902c24a6c511a11c7264` |
| `.scratch/phase5a-local-distribution/r422/setup-lifecycle/dsh-pet-core-webm-setup.exe` | 246922137 | `53fe18eff3e2b45acf0a23c1732675316241b9b84fe7d7ee8214b2429e854f38` |
| `.scratch/phase5a-local-distribution/r422/worker/dist/proactive-screen-worker/proactive-screen-worker.exe` | 4863029 | `9d02b34542497a9b0f355f4f04b10bb05daab3c9cd4bb2fd88530f15a68ba6f1` |
| `.scratch/phase5a-local-distribution/r422/packages/official.ai-chat.zip` | 121663 | `66ec2f6703079ae7f788dfce2c56794aca26089458ef3eac27d4bb210a44e46f` |
| `.scratch/phase5a-local-distribution/r422/packages/official.screen-understanding.zip` | 24461409 | `12bbf9a45c5f56238fda2d45e81d4f5415395290b74ec449ad5bcacf30e40c41` |
| `.scratch/phase5a-local-distribution/r422/probe-clean/dist/dsh-feature-probe/bundle.json` | 7197 | `2412335faef11f7d85cf7562b8a58a5b47e01b972e35104cf036eedea0d2b283` |

**只使用新r422/setup-lifecycle，不用旧setup-final、c07/c08或setup-current**。Core EXE须与c09完整内部目录一起运行，不能单拷EXE；建议人工用新Setup。Setup由ISCC6.7.3编译、嵌入两ZIP，**未执行系统安装/卸载**；helper表中hash是bundle.json清单，不是helper EXE。unsigned/manual-acceptance-only不是正式签名发布，Authenticode/SmartScreen未验。

受影响Core仅pet/feature_lifecycle.py输入变化：重新PyInstaller分析/PYZ/EXE，仅在独占CoreCodeGate后替换本轮自有候选EXE和base_library.zip；其余2103完整bundle资源逐项比对。旧r422/core-final全包与setup-final逐字节保留。其余Worker/helper/两ZIP由本轮R已重建、当前输入复核一致，不因不相关生命周期源变化重复构建，也不沿用旧T包。首次EXE定位器失败保存lifecycle-core-build-failed-v1.json，修复验证器后复用成功预构建，不伪造首次exit0。

两个生成根合计预算 **11.25GiB（12,079,595,520B）**，原6GiB+用户约5g授权的有界5.25GiB。没有删除旧包/Setup/失败证据或清未知staging；标准pytest仅清本次新成功temp根。最终实际总量见§8.8。

### 8.7 待用户人工验收：步骤与目标效果

0. **先更新卸载器**：先用本节新 Setup 4.2.2 覆盖安装，按需要勾选 AI/识屏或在扩展管理导入本节新 ZIP。旧 `unins000.exe` 含旧逻辑，只换源码/ZIP 不会修好它。测试前备份个人数据/原始源包；这是用户系统操作，不由本轮自主执行。
1. **Core 统一 API、迁移**：设置→“AI 与对话”→“API 服务 · Core 管理”，填写 ID/名称/地址/路径/文字和视觉模型及 Key，勾选需要的 AI 对话、文件、手动/自动识屏、余额用途；余额服务只有确实兼容 DeepSeek 时选该协议。点击“保存服务及用途授权”，见“已保存”后才算生效。若使用旧配置，从“旧配置迁移预览”逐个来源选择/确认；应保留旧配置/Key，不能静默合并不同密钥。未安装 DLC 也能打开 Core API 页；配置保存不自动启用 DLC/自动识屏。
2. **已有窗口立即用新配置**：先打开现代、经典、快捷、灵动岛聊天，再保存/修改中央绑定，分别发一条；无需重启。独立设置进程保存后同样能新请求读取最新配置。文件解读只用自己的已授权用途。测试连接最多1输出 Token、可能收费，不代表保存，也不代表视觉/余额可用；不要只点测试后就当配置生效。
3. **余额复用 Key 与错误区分**：在同一服务启用余额用途后查询，支持 DeepSeek 协议应显示可用余额，无需另输 Key；不支持协议应明确“不支持余额查询”，错误 Key/网络有对应错误，不能报第二份 Key 未配。
4. **手动识屏独立**：自动识屏关闭、白名单为空，点击“看看屏幕”；应启动 Worker 并返回真实识别结果。进行手动请求时刷新无关设置，不应误取消；自动策略仍不自动开启。缺授权、启动/握手/截图失败、网络错误、主动取消能区分，修正后能重试，不靠重启碰运气。
5. **授权/草稿与在飞行为**：普通模型配置变更，新请求用新值、在飞请求保持原快照；撤销用途、删除服务或停 DLC 应取消并拒绝迟到结果，重新授权不能复活旧任务。失效配置发送保留用户输入；别的窗口保存/普通刷新不得覆盖未保存草稿。更换服务地址要求重新输入 Key 和授权。
6. **Core-only 系统卸载，两来源各测一次**：分别使用 Setup 选装 DLC 和外部导入 DLC；自然退出 Core 后运行新卸载器，应只有 Inno 一次确认、不弹逐包 DLC 卸载，能删 Core。已导入副本、原始 ZIP/源目录、中央/旧配置、Key/个人数据应保持；staging/待处理/ledger 不应阻止 Core 删除。Core 正运行时应要求自然退出，不强杀。重装新 Core 应识别有效保留包；此前已接受的逐包卸载事务不自动恢复/启用。
7. **单包卸载文案与范围**：扩展管理单独卸载某 DLC，只删已安装副本，不删外部 ZIP/源目录与个人数据；文案应如实描述。此操作与 Core 系统卸载是两条独立流程。

人工确认必须注明版本/来源/步骤和实际结果；不要提供或粘贴真实 Key。真实 Provider 会产生费用/隐私屏幕内容，系统卸载会改注册/程序目录，本轮没有冒用用户数据做这些验收。macOS/Linux、复杂第三方 localhost-worker、Authenticode/SmartScreen/正式分发不在本次已证范围。

### 8.8 失败分类、最后闭合、回退与准确下一步

- **既有已修**：默认Qt AV经HEAD旧guard、nativewatch/公开最小seam/red→green证实，最新完整单进程自然0。不再把旧分组通过当默认门；也不把所有Qt问题泛化成环境。无全局keeper/私有Qt写入/新skip。
- **负载协调/原因边界**：v4第三轮20s ready超时有44.040s迟达证据；底层原因未确证，未伪称产品lease错/全是环境。事件90s有界测试协调、异常自然join后，全量v2和v5三轮重新通过。产品timeout/断言未改。
- **自有脚本失败**：错误load文件、sidebar ai、旧audit路径、green选择/拼接、Core EXE dist定位是探针/验证器失误；原失败独立保留。PyInstaller本身成功、候选首次失败未变；恢复路径验同输入/模块/哈希。v2负载非饱和不当满CPU通过。新增生命周期性能驱动首次错误导入不存在的 pet.feature_host，在测量前 exit1；改为实际 pet.plugins.feature_host 后用独立 v2 日志重跑，未修改产品或旧失败日志。最终文档第二次回填驱动只识别旧§12标题的空格，阶段记录初次落盘后再次执行时 exit1；修正为兼容自身新旧标题的幂等匹配，独立 v2 重跑，旧日志保留，不涉及产品/测试源码。
- **人工/平台尚未测**：真实Provider/余额协议/费用/画面、新系统Setup两来源卸载/重装、用户确认；macOS/Linux、复杂第三方localhost-worker、正式签名/SmartScreen/发布未证。未测不是失败，不能预记用户验收。
- **回退边界**：只按本表逐文件R差异且保护原用户/T改动；不reset --hard、不覆盖用户文件、不删中央namespace/旧Key/个人数据。旧完整构建保留但不建议用旧卸载器验收新版；真要回退由用户明确选择数据/版本。

<!-- R_FINAL_VERIFICATION_START -->
末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。

621源匹配最新默认单进程与三轮负载快照；6项现行产物size/SHA再次复核，5个自有PID/createTime/exe无残留（不枚举其他用户进程）。生成根 11,939,448,659B /12,079,595,520B；旧artifact_deleted=false。累计 115修改+25新增、0删除/暂存，初始78dirty路径保留。逐文件表由最新numstat刷新；源码/测试未再改，文档末次回填后独立只读复核。

最后回执 `lifecycle-final-verification.json`：engineering_gates_passed_human_acceptance_pending；phase5a_closed=false，committed=pushed=false。
<!-- R_FINAL_VERIFICATION_END -->

**下一步**：工程授权范围停止于这份人工验收候选；用户先备份、自然退出，覆盖安装**新Setup4.2.2以更新旧unins000.exe**，按§8.7逐项反馈，不提供Key。收到缺陷先复现/修复/验证；收到明确确认后才更新Phase5A正式收尾。无提交/推送/发布授权。

### 8.9 实际使用效果与限制（当前R候选）

Core配置一次Key并授权文字/视觉/余额，新请求保存即生效，已有四类聊天/文件不靠重启；自动关/空白名单不误取消手动看看屏幕；系统卸载只删Core和范围内集成，保留DLC副本、源包、配置/密钥/个人数据。新Core能识别有效保留包，不恢复已接受逐包卸载。

自动化/本机冻结工程证据已完成，但不能代替真实服务、真实内容和系统安装卸载；协议/模型必须服务支持，连接测试可能收费且不是保存，旧卸载器须新Setup更新。**Phase5A未正式关闭**，公开SDK、正式签名发布与Git发布都未执行。


### 8.10 R08 修复记录：覆盖安装 stale frozen probe 导致 `core_maintenance_incomplete:2`

#### 触发、证据与根因

2026-10-08 用户使用 R07 候选 Setup 覆盖安装时看到 `core_maintenance_incomplete:2`。检查用户本机 `core-maintenance.log` 的最近两条记录得到 `code=2`、`reason=bundle_inventory`、`status=blocked`；当前安装 `E:\dsh-pet-core-webm\_internal\feature-probe` 有 85 个文件，而干净候选和 manifest 只有 64 个。`TrustedProbeBundle.verify()` 的精确库存规则因此拒绝旧残留。根因在 Setup 覆盖复制没有清除旧的 Core-owned frozen probe 子树，不是 Key、网络或官方 DLC ZIP 的内容问题。

#### R08 修改文件与增量

| 文件 | R08 增量 | 修改内容 |
|---|---:|---|
| `packaging/core_webm.iss` | +5 / -0 | 新增 `[InstallDelete]`，在覆盖安装前只删除 `{app}\_internal\feature-probe`，避免旧 probe 文件污染精确库存；不触碰 `data`、DLC、配置、凭据和个人数据。 |
| `tests/test_core_setup_template.py` | +8 / -0 | 增加 Setup 模板失败回归，断言删除范围精确且不包含 `data`。 |

#### 自动化与本机验证

- 修复前回归：`1 failed`；修复后：`tests/test_core_setup_template.py` 为 `5 passed`。
- Inno 隔离安装语义：`compile_exit=0`、`run_exit=0`、`stale_exists=False`、`fresh_exists=True`。
- 官方 ZIP 预检通过；`ruff check`、`ruff format --check`、`git diff --check` 通过。
- `QT_QPA_PLATFORM=offscreen python -m pytest -q`：`4418 passed, 15 skipped, 14 warnings`，`702.17s`。
- 新 Setup：`E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\repair-20261008-setup-overwrite\r422\setup-overwrite-fix\dsh-pet-core-webm-setup.exe`，`246,922,162 B`，SHA-256 `8f53f5916285299dfddb7e41b5747aec5e049502722d422110b9ada8fc6fcc9b`。

#### 人工验收边界

自动化没有运行用户实际 Setup、没有改用户安装目录、没有执行系统卸载、没有调用真实 Provider 或读取真实屏幕。用户应自然退出后使用 R08 新 Setup 覆盖安装旧 4.2.2，确认 Setup 完成且 Core 能启动，再按 §8.7 验证 API/余额/聊天即时生效、手动识屏、DLC/源包/个人数据保留和 Core-only 卸载。收到用户明确确认前，`phase5a_closed=false`。

<!-- R_REPAIR_REPORT_END -->

<!-- R09_POLICY_UNINSTALL_20261009 -->
## 8.11 R09：Core-only 卸载诊断与冻结本地包激活修复

### 当前结论

R09 工程门通过，Phase5A 仍未正式关闭。用户已说明卸载前退出桌宠；本轮把失败从“是否退出桌宠”中拆出：Core 维护入口返回 `core_maintenance_incomplete:2` 时，必须看专属集成/身份/自启动清理的脱敏 reason。R09 不再把外部源目录 bridge 注册视为当前 Core-owned 集成，也不以 DLC ledger、staging 或逐包卸载状态阻塞 Core 删除。

### 修改文件与原因（当前工作树 numstat）

本仓库在本轮开始时已有 Phase5A 脏工作树；下表是相关文件当前 `git diff --numstat`，不把历史 R01–R08 行数冒充 R09 单独增量：

| 文件 | 当前 diff numstat | 本轮作用 |
|---|---:|---|
| `pet/agent_link.py` | `40 / 0` | 外部 bridge 引用不进入 Core-owned 清理范围。 |
| `pet/core_maintenance.py` | `225 / 49` | Core-only 维护、脱敏 reason 和有界日志。 |
| `scripts/build_feature_release.py` | `8 / 2` | 冻结策略显式生成 `ALLOW_LOCAL_PACKAGE_ACTIVATION=True`；同时保留 Worker/版本/Probe 交付硬门。 |
| `tests/test_agent_registration_scope.py` | `20 / 3` | 外部 profile lock/registration 失败回归。 |
| `tests/test_core_registration_cleanup.py` | `31 / 6` | Core-owned 清理与诊断失败回归。 |
| `tests/test_build_feature_release.py` | `8 / 0` | 冻结策略和 4.2.2 版本回归。 |
| `packaging/core_webm.iss` | `82 / 13` | 覆盖安装只清理 Core-owned frozen probe（R08 仍在本轮交付）。 |
| `tests/test_core_setup_template.py` | `27 / 2` | Setup 覆盖安装清理边界回归（R08）。 |

### 自动化与隔离实机结果

- 策略 red：`R09-policy-red.log`；策略 green：`2 passed in 1.08s`。
- 受影响专项：`107 passed in 63.54s`。
- 全量：`4420 passed, 15 skipped, 15 warnings`，`831.71s`，`QT_QPA_PLATFORM=offscreen`，exit 0。
- Ruff check、format check、`git diff --check`：exit 0；format 输出 `626 files already formatted`。
- 真实冻结 Core + Qt 事件循环 + 独立进程 + UIAutomation：`empty/ai/screen/both` 全部 passed，启动时间约 `8916.0 / 3139.0 / 4414.1 / 4723.4ms`；详细 RSS/threads/IO 在 `frozen-results.json`。

### 交付物

- Core：`E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\repair-20261008-uninstall-diagnosis\r422\core-uninstall-fix\dist\dsh-pet-core-webm\dsh-pet-core-webm.exe`，`17,304,043 B`，SHA-256 `34F1FE81E92E2F177D568F52E6919B75CCBD2E496F4FED7A1CB525E9D5207C3F`。
- Setup：`E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\repair-20261008-uninstall-diagnosis\r422\setup-uninstall-fix\dsh-pet-core-webm-setup.exe`，`246,781,460 B`，SHA-256 `7FB5C31657B962F9861745E046323E29DAF22077E314CF1E256352F269C5F98F`。
- Setup 编译退出码 `0`；没有把用户真实 Setup/卸载器作为自动化结果。

### 性能与资源分析

- 全量 pytest 831.71 秒覆盖 4,420 个通过用例；受影响专项 63.54 秒覆盖 107 个用例。
- 隔离冻结 Core 的 `empty/ai/screen/both` 启动时间为 8.916/3.139/4.414/4.723 秒；RSS 为 192,118,784 / 220,336,128 / 211,996,672 / 221,241,344 bytes；线程数为 38 / 33 / 32 / 34。四案自然退出码均为 0。
- `ALLOW_LOCAL_PACKAGE_ACTIVATION` 是构建时写入的策略常量，正常运行不增加线程、网络请求或持久化写入；Core 维护诊断只在一次维护结果追加一行有界 JSON，不记录 Key。R09 没有新增 API 网络调用、截图、系统代理修改或后台常驻线程。
- Setup 产物 246,781,460 bytes；Core 产物 17,304,043 bytes。以上为本机 Windows 实测，不是 CI 估计。

### 实机报告与人工未验证项

已在本机真实 Windows 10.0.26100、Inno Setup 6.7.3 环境完成 Core/Setup 构建、真实冻结 Core Qt/UIAutomation 四案和全量 pytest；安全 handoff 探针已有 `compile_exit=0`、`probe_exit=1`（初始化返回 False 的预期探针）、maintenance `0` 的隔离记录，未安装真实产品。未由 Codex执行：真实 Provider/余额数值/费用、真实屏幕内容、用户 Setup 覆盖安装、旧卸载器替换、Core-only 实际删除和 DLC/配置/个人数据保留检查。未验证不等于失败。

### 最终用户效果与限制

用户先用新 Setup 覆盖安装，再在 Core 配置一次 API 并授权文字/视觉/余额；已有聊天不需重启，余额按用途绑定，自动识屏开关不会误伤手动识屏，Core 卸载不先拆 DLC。服务必须支持对应模型/余额协议，连接测试可能产生费用；旧卸载器必须由新 Setup 覆盖更新。完成上述人工确认并明确回复前，Phase5A 保持 `phase5a_closed=false`，本轮不提交、不推送、不发布。
