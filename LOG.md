# 项目变更日志

本文件只记录已经发生并完成验证的工程、架构和文档变更。路线规划请看 [`SPEC.md`](SPEC.md) 与 [`docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md`](docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)；详细交付证据请看 [`docs/INDEX.md`](docs/INDEX.md) 中的 PR 报告。

## 2026-09-27

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
