# 项目入口：dsh-pet-indesktop

<!-- S04_CURRENT_START -->
**2026-10-10 当前状态：4.2.4 已由用户确认实际体验；已在 `origin/codex/phase3-worker` 推送并核对检查点 `0a299612714e5fad55a24a5506dfce938b9eeb1c`。后续 MOD 管理中心、公共 v1 和教程已实现；final4 冻结 Core/Setup 与真实生产 Worker/Settings 烟测通过，M05 以可运行检查点准备本地提交。**

**现行合同**：简易主 Key + 可选视觉 Key，不填服务 ID/用途授权；测试按钮旁显示结果码，测试不保存。Setup 每次显示路径页，采用 portable `<项目目录>/data`；卸载程序与 data/plugins，个人 data 默认保留、可选删除。MOD 管理采用本地统一列表，导入后点启用即时挂载；角色启用不换装，使用只切当前实例。Setup 行为不变。

**候选分开记录**：已验收4.2.4仍为 `_s01b/setup-release/dsh-pet-core-webm-setup.exe`；M05新候选4.2.5与两个1.0.4的工程证据见 [MOD 报告](PR-REPORT-MOD-CENTER-V1-2026-10-10.md) / [状态](../.scratch/mod-authoring-v1/STATUS.md)。满 CPU 压力族非阻塞限制仍单独留档。旧候选用户确认不扩张为新界面、真实Provider或其他平台已验收。真实安装/Key/屏幕未修改，新候选不是正式签名稳定版。

**入口**：[MOD 使用/制作教程](modding/README.md) · [公共 v1](modding/API-V1.md) · [兼容矩阵](modding/COMPATIBILITY.md) · [实施计划](modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md) · [Phase5A历史交付](PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md)。受信任 Python MOD 不是沙箱，私有模块不属于公共兼容承诺。
<!-- S04_CURRENT_END -->

> 这是给新对话、新贡献者和继续施工的开发者看的渐进式入口。它不是普通用户安装教程，也不替代阶段设计、API 合同、测试计划和 PR 报告。

> 历史 Phase4B 发布记录（2026-10-04；下述暂缓只指当时）：Phase 4B 源修改 `98bbfba` 已正常推送到 `origin/codex/phase3-worker` 并独立核对远端SHA；新全量3841项及满CPU3×154项通过；准确状态见同组交接与工程报告。正式信任锚/分发按用户决定暂缓，不生成官方私钥、不正式发布。

## 1. 第一层：项目定位与当前结论

`dsh-pet-indesktop` 是一个基于 Python + PySide6 的桌面宠物应用。项目当前同时推进两条线：

- **稳定 Core**：窗口、动画、基础交互、配置、生命周期、通用平台查询、菜单/设置宿主和扩展管理基础；
- **可选交付**：资源 DLC、官方功能 host、官方 Worker，以及未来条件启用的第三方外部 Worker/生态。

当前路线不是“拆出更多 Python 文件”就算完成，而是逐步证明：

```text
不安装也能启动
→ 安装后入口出现
→ 停用后不执行
→ 卸载后文件与入口消失
→ 重装恢复保留配置
```

重要边界：Worker 是独立进程，但不自动等于可卸载 DLC；Core 内运行的 Python host 不承诺即时热卸载；不开放任意第三方 Python `entrypoint`。

当前最重要的路线位置：

```text
Phase 4B-1       唯一安装状态账本：已完成
Phase 4B-1.5     资源 DLC 硬门：代码、自动化、文档、保护门与人工验收已通过；公开稳定 API 仍未开放
Phase 4B-2       跨进程版本租约：内部实现与自动化/实机验收完成；已推送至对应实施分支（精确分支见工程报告）
Phase 4B-3       本地生产事务与LPAC：Windows工程闭环验收完成
Phase 4B-4/4B-5  管理界面/双冻结Core完整Windows矩阵与最终门通过；人工/发布门单列
```

当前准确停点与证据见 [连续收尾报告](PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)。[2026-10-03 事务检查](PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md)是历史中间快照，不替代本轮工程门；正式信任锚和发布验收仍单列。

资源接口虽然已经通过 4B-1.5 的代码、自动化和人工验收门，但在后续稳定 API/生态开放门通过前，仍只能作为内部验证接口，不能对外承诺稳定兼容。Phase 6 的资源 SDK、外部 Worker 预览和第三方生态均未开放。

## 2. 第二层：仓库结构

```text
pet/              桌宠运行时代码、Core、插件宿主、Worker 和功能兼容层
  content/        角色资源安装、manifest、Registry、播放链
  plugins/        官方插件运行时、功能包验证/绑定
  screen_understanding/  屏幕理解配置、适配和兼容实现
  workers/        Worker 协议、Supervisor、Worker 入口
features/         功能 host 与功能实现的分层目录（逐步迁移中）
content/          内容管理相关公共资源/数据目录
assets/           内置角色、动画和发布素材
integrations/     外部工具、Agent/桥接等集成边界
tests/            确定性回归、Qt/进程边界、DLC 和生命周期测试
scripts/          构建脚本、静态检查、真实环境探针和验证工具
packaging/        PyInstaller/安装器入口和构建变体

docs/             架构、路线、API、验收、PR 报告和项目入口
  agents/         协作、计划、交接和工程规则
  plugin-roadmap/ 总路线与功能交付/开放顺序
  plugin-phase-01-foundation/ 资源 DLC 基础
  plugin-phase-02-runtime/     Core 插件运行时
  plugin-phase-03-worker/      Worker 与 Phase 3A/3B
  plugin-phase-04-updates/     拆包、状态、租约、事务和更新
.scratch/         当前任务的 PLAN/WORKLOG/HANDOFF/STATUS/SUMMARY
```

`pet/`、`tests/`、`packaging/` 属于产品和验证实现，修改前必须按阶段计划和测试门执行。`docs/` 与 `.scratch/` 是工程记录；`.scratch/` 只显式登记任务记录，不加入构建产物、缓存、原始日志或密钥。

## 3. 第三层：文档导航

### 必读入口与规范

- [`docs/PROJECT-ENTRY.md`](PROJECT-ENTRY.md)：本入口，渐进式了解项目和当前状态；
- [`docs/INDEX.md`](INDEX.md)：仓库文档唯一索引；
- [`docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md`](agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md)：施工记录、交接、总结和授权规则；
- [`docs/agents/planning-and-reporting.md`](agents/planning-and-reporting.md)：计划和汇报模板；
- [`docs/agents/handoff.md`](agents/handoff.md)：交接与跨对话阅读顺序；
- [`SPEC.md`](../SPEC.md)：项目目标、边界和验收约束；
- [`AGENTS.md`](../AGENTS.md)：仓库级工程规则。

### 当前路线与阶段

- [`plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md`](plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)：总路线、阶段出口和条件门；
- [`plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md`](plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)：功能最终归属、依赖和交付形态；
- [`plugin-roadmap/PLUGIN-DLC-OPENING-ORDER-AND-THIRD-PARTY-INTERFACES.md`](plugin-roadmap/PLUGIN-DLC-OPENING-ORDER-AND-THIRD-PARTY-INTERFACES.md)：资源、官方功能、Worker 和第三方开放顺序；
- [`plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md`](plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md)：外部基线评审响应和 P0 修复门。

### 当前重点阶段

- [`plugin-phase-01-foundation/`](plugin-phase-01-foundation/)：资源 DLC 基础，但仍受资源硬门和发布条件约束；
- [`plugin-phase-03-worker/README.md`](plugin-phase-03-worker/README.md)：Agent Link、主动识屏 Worker 和 Phase 3A/3B 证据；
- [`plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md`](plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)：屏幕理解拆包边界；
- [`plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md`](plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md)：资源硬门；
- [`plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md`](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)：4B-1 至 4B-5 本地管理路线；
- [`plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md`](plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md)：资源包、功能 host、Worker 包与远程分发的更新边界。

### 记录与证据

- [`RECORDS-INDEX.md`](RECORDS-INDEX.md)：PR、Issue、Release 和工程记录导航；
- [`docs/PR-REPORT-TEMPLATE.md`](PR-REPORT-TEMPLATE.md)：运行行为变更的证据模板；
- `.scratch/documentation-continuity/`：本次入口与协作规范施工记录；
- `.scratch/phase4b-local-management/`：4B 总任务当前状态、租约/事务后续交接；
- `.scratch/phase4b-1-5-resource-hard-gate/`：资源硬门当前状态与证据摘要；
- `plugin-phase-04-updates/PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md`：4B-2 设计、红线与停止条件；
- `.scratch/phase4b-2-cross-process-version-lease/`：4B-2 的计划、状态、交接、工作日志和总结。

## 4. 第四层：按问题查文档

| 想了解什么 | 先读 | 再读 |
|---|---|---|
| 当前做到哪里 | 当前任务 `STATUS.md`、本入口第 1 节 | `docs/INDEX.md`、路线图 |
| 继续当前任务 | `STATUS.md` → `PLAN.md` → `HANDOFF.md` → `SUMMARY.md` | 对应阶段设计文档 |
| 资源 DLC | `PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md` | Phase 1 架构/API/迁移、开放顺序文档 |
| 跨进程版本占用 | `PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md` 与 `../.scratch/phase4b-2-cross-process-version-lease/STATUS.md` | 租约实现报告、Worker 协议、状态账本 |
| Worker | Phase 3 README 与封存报告 | Phase 4A 屏幕包设计、Worker 协议 |
| 安装/卸载 | `PHASE4B-LOCAL-MANAGEMENT-DESIGN.md` | `PLUGIN-UPDATE-PROTOCOL.md`、4B scratch |
| 菜单与设置贡献 | Phase 2 runtime 文档、Phase 4A 设计 | 当前功能贡献 PR 报告 |
| AI 对话与屏幕理解联动 | `PHASE4A-SCREEN-PACKAGE-DESIGN.md` | Vision config、贡献和 host/build 记录 |
| 第三方作者接入 | DLC 开放顺序与第三方接口文档 | 资源 manifest/API 合同；未开放前不得假设稳定 |
| 验证证据 | 对应 PR 报告和 scratch `WORKLOG.md` | 当前 `STATUS.md`，区分历史/本轮结果 |
| 恢复中断任务 | `HANDOFF.md` 和 `SUMMARY.md` | 设计文档与下一条命令 |

## 5. 第五层：状态词汇与不确定性

文档中的状态必须区分：

- **计划中/设计中**：只有方案，不代表实现；
- **已实现**：代码或文档已落盘；
- **自动化验证通过**：命令和结果已记录；
- **Windows/其他实机验证通过**：真实环境已记录；
- **用户已确认**：用户明确反馈；
- **已提交**：本地 Git 提交已核验；
- **已推送**：远程分支已核验；
- **尚未验证/条件启用**：不能推断为失败或完成。

历史测试结果必须注明日期/版本，不能冒充本轮结果。用户未手测的功能保持“未验收”，不因自动化通过改写。当前 4B-1.5 的资源自动化证据不等于用户已经能在桌宠中安装、停用或卸载资源。

## 6. 新对话的最短路径

如果只想快速继续：

```text
docs/PROJECT-ENTRY.md
→ .scratch/phase4b-local-management/STATUS.md
→ .scratch/phase4b-local-management/PLAN.md
→ .scratch/phase4b-local-management/HANDOFF.md
→ .scratch/phase4b-local-management/SUMMARY.md
→ .scratch/phase4b-1-5-resource-hard-gate/STATUS.md
→ PHASE4B-LOCAL-MANAGEMENT-DESIGN.md
```

如果当前对话专门处理本次文档规范，再将 `.scratch/documentation-continuity/` 五份记录作为优先任务上下文。

## 7. 保护边界

默认保持不变：`pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`、用户已有脏工作树、历史 PR 报告、外部评审原文件、真实 Key、用户数据和构建产物。任何提交或推送都必须按当前任务重新确认授权。

## 8. 完成后的实际使用效果

这个入口不会改变桌宠界面，也不会新增安装或卸载按钮。它的作用是让新对话和新开发者可以先看一页，再逐步进入路线、阶段、任务记录和验证证据；继续施工时不会把“Worker 独立”“功能停用”“物理可卸载”混成同一件事。
