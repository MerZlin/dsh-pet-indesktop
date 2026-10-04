# 变更日志索引

> 详细记录见 [`LOG.md`](LOG.md)。本索引只保留主题入口和日期，避免把日志内容复制成第二份权威说明。

| 日期 | 主题 | 入口 | 关键提交/状态 |
|---|---|---|---|
| 2026-10-04 | Phase 4B 分支发布 | [发布记录](LOG.md#phase-4b-分支发布2026-10-04) · [工程报告](docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md) · [最终交接](.scratch/phase4b-local-management/HANDOFF.md) | `98bbfba` 85文件源修改已提交推送并远端SHA核验；全量3841及3×154满CPU新通过，文档封存另列；正式信任/分发继续暂缓 |
| 2026-10-04 | Scratch 生成物清理 | [清理记录](LOG.md#scratch-生成物清理2026-10-04) · [最终交接](.scratch/phase4b-local-management/HANDOFF.md) | 294目录/132,245文件，34.987→3.809 GiB；E盘净增31.435 GiB，保护摘要/state不变；正式信任/分发暂缓，无提交/推送 |
| 2026-10-04 | Phase 4B真实人工验收准备 | [人工记录](docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md) · [准确停点](.scratch/phase4b-local-management/HANDOFF.md) | no-chat目录安装/真实识屏/启停/ZIP幂等/升级/回滚通过；两版本物理卸载、ZIP新安装revision17及Core真实加载核验，用户确认原设置/凭据与结果保留；正式信任锚/分发按用户决定暂缓，正式key未生成；首Core问题未证明修复，自动/第二变体等门单列；无提交/推送 |
| 2026-10-04 | Phase 4B 连续收尾 | [LOG.md](LOG.md#phase-4b-连续收尾2026-10-04) · [工程报告](docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md) · [准确停点](.scratch/phase4b-local-management/HANDOFF.md) | 4B-3/4/5 Windows工程闭环完成，最新Core10双七行/全量/满负载三遍通过；人工/发布单列，未提交/推送 |
| 2026-10-03 | Phase 4B-1.5 人工验收确认与状态同步 | [LOG.md](LOG.md#phase-4b-15-人工验收确认与状态同步-2026-10-03) · [项目入口](docs/PROJECT-ENTRY.md) · [4B 状态](.scratch/phase4b-local-management/STATUS.md) | 用户确认 4B-1.5 人工门已通过；4B-2 已完成并推送；4B-3 进入后续规划，公开稳定 API 仍未开放 |
| 2026-10-03 | Phase 4B-2 跨进程版本租约 | [LOG.md](LOG.md#phase-4b-2-跨进程版本租约-2026-10-03) · [设计](docs/plugin-phase-04-updates/PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md) · [实施报告](docs/PR-REPORT-PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-2026-10-03.md) · [任务状态](.scratch/phase4b-2-cross-process-version-lease/STATUS.md) | 内部实现、自动化和 Windows 实机证据完成；4B-1.5 人工门已由用户确认通过，公开稳定 API 仍未开放；`5a1b6e0` 已提交并推送至 `origin/codex/phase3-worker` |
| 2026-10-02 | 文档连续性、项目入口与开发者 README | [LOG.md](LOG.md#文档连续性项目入口与开发者-readme-2026-10-02) · [入口](docs/PROJECT-ENTRY.md) · [规范](docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md) · [任务总结](.scratch/documentation-continuity/SUMMARY.md) | 文档、规则与任务记录已补齐；文档门已通过，未提交未推送 |
| 2026-10-02 | Phase 4B-1.5 资源 DLC 硬门 | [LOG.md](LOG.md#phase-4b-15-资源-dlc-硬门) · [设计](docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md) · [实施报告](docs/PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md) · [任务状态](.scratch/phase4b-1-5-resource-hard-gate/STATUS.md) | 资源硬门代码、自动化、文档与保护门已通过；本地封存就绪，4B-2 未开始，未提交未推送 |
| 2026-10-02 | DLC 基线评审对齐与路线加固 | [LOG.md](LOG.md#dlc-基线评审对齐与路线加固) · [评审响应](docs/plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md) · [任务状态](.scratch/dlc-baseline-review-remediation/STATUS.md) | 文档路线已修订；Phase 1 P0 与 4B-1.5 硬门新增；代码未修改、未提交、未推送，历史验证待执行 |
| 2026-09-30 | Phase 4B-1 唯一安装状态与安全恢复 | [LOG.md](LOG.md#phase-4b-1-唯一安装状态与安全恢复) · [实施报告](docs/PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md) · [当前状态](.scratch/phase4b-local-management/STATUS.md) | 状态层已实现；全量 3547 passed，进程族 3 × 10 passed；未提交、未推送，4B-2 未开始 |
| 2026-09-30 | 协作规范与成果同步 | [LOG.md](LOG.md#协作规范与成果同步) · [规范](docs/agents/planning-and-reporting.md) · [当前状态](.scratch/workflow-standardization/STATUS.md) | `a2779cb` 规范、`beba389` 记录；全量 3459 passed，高负载 3 × 65 passed；远程已核验到 `beba389`，4B 未实施 |
| 2026-09-30 | Phase 4B 计划与本地备份检查点 | [LOG.md](LOG.md#phase-4b-计划落盘与本地备份检查点) · [设计](docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md) · [交接](.scratch/phase4b-local-management/HANDOFF.md) | `5f04a99` 本地备份；4B 仅计划、未实施；全量 3459 passed，未推送 |
| 2026-09-29 | Phase 4A host 与独立构建 | [LOG.md](LOG.md#phase-4a-host-与独立构建) · [实施报告](docs/PR-REPORT-SCREEN-HOST-BUILD-2026-09-29.md) | Windows 独立验证产物 10/10，全量 3457 passed；安装闭环/人工验收待办，已纳入 `5f04a99` 本地备份，未推送 |
| 2026-09-28 | Phase 4A 菜单与设置贡献 | [LOG.md](LOG.md#phase-4a-菜单与设置贡献) | owner 生命周期/菜单与设置已接入，全量 3257 passed；本版未手测、已纳入 `5f04a99` 本地备份，未推送 |
| 2026-09-27 | Phase 4A 独立视觉配置与确认迁移 | [LOG.md](LOG.md#phase-4a-独立视觉配置与确认迁移) | 全量 3224 passed；配置/凭据/无聊天边界通过自动化，原生 keyring 与用户操作待验收；已纳入 `5f04a99` 本地备份，未推送 |
| 2026-09-27 | Phase 4A 通用平台查询与兼容适配 | [LOG.md](LOG.md#phase-4a-通用平台查询与兼容适配) | 查询切片已实现，全量 3177 passed；真实前台探针通过，不等于完整拆包；已纳入 `5f04a99` 本地备份，未推送 |
| 2026-09-27 | Phase 4A 最小拆包设计与可选聊天联动 | [LOG.md](LOG.md#phase-4a-最小拆包设计与可选聊天联动) | 五组接口与独立交付边界已设计，未迁移；文档门通过，已纳入 `5f04a99` 本地备份，未推送 |
| 2026-09-27 | 前台窗口测试边界与 Phase 4A 只读审计 | [LOG.md](LOG.md#前台窗口测试边界与-phase-4a-只读审计) | 全量 3123 passed；自动/退出人工门仍未验收；已纳入 `5f04a99` 本地备份，未推送 |
| 2026-09-27 | Phase 3B 收尾与 Phase 3C 风险评估 | [LOG.md](LOG.md#phase-3b-收尾与-phase-3c-风险评估) | `1d60b89` 实现检查点；本条对应收尾文档独立提交；全量环境门与人工细项未关闭，未推送 |
| 2026-09-27 | 官方功能可选交付路线统一 | [LOG.md](LOG.md#官方功能可选交付路线统一) | `2e02067`；功能总表与 Phase 1–7 正文同步，本地提交，未推送 |
| 2026-09-27 | 插件化中期 grill 对齐 | [LOG.md](LOG.md#插件化中期-grill-对齐) | `2e02067`；Q1–Q5 已确认，仅文档记录，未推送 |
| 2026-09-24 | Phase 2 Core 插件运行时 | [Phase 2 PR 报告](docs/PR-REPORT-PLUGIN-PHASE2-2026-09-24.md) | `9d294ea`，已封存 |
| 2026-09-24 | 测试分类与覆盖率开发依赖 | [LOG.md](LOG.md#测试分类与覆盖率开发基线) | `db454d9`，已验证 |
| 2026-09-24 | 工程规范化计划 | [SPEC.md](SPEC.md#5-工程质量门) | 分阶段执行中 |
| 2026-09-24 | 工程规范化工具链与统一质量门禁 | [LOG.md](LOG.md#工程规范化工具链与统一质量门禁) | `db4e224`，完整门禁与覆盖率合并已验证 |

> 历史条目的“未推送”等描述表示当时状态；本轮远程同步以最新状态记录和实际核验为准，不倒写历史。

## 维护规则

- 新增日志先写入 `LOG.md`，再在本表添加一行；
- 只记录已经执行的事实，计划项必须标注为计划，不写成已完成；
- PR 级证据放 `docs/PR-REPORT-*.md`，这里仅做索引。
