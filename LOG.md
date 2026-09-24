# 项目变更日志

本文件只记录已经发生并完成验证的工程、架构和文档变更。路线规划请看 [`SPEC.md`](SPEC.md) 与 [`docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md`](docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)；详细交付证据请看 [`docs/INDEX.md`](docs/INDEX.md) 中的 PR 报告。

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
