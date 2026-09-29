# 变更日志索引

> 详细记录见 [`LOG.md`](LOG.md)。本索引只保留主题入口和日期，避免把日志内容复制成第二份权威说明。

| 日期 | 主题 | 入口 | 关键提交/状态 |
|---|---|---|---|
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
