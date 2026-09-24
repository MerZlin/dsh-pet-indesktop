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

### 工程规范化工具链与统一质量门禁

- 已完成并封存 Ruff E4/E7/E9/F/I、全仓库格式检查、mypy 核心边界、Markdown 链接检查、`pre-commit`、跨平台 `scripts/check.py` 入口和 CI 质量/桌面矩阵；对应主题提交见 `e72bc8c`、`46e0218`、`1645276`、`3adc1c4`、`69f41c6`、`e78f1d1`、`382ca24`、`e8e7ed8`、`a9563f2`、`97e1f4a`。
- `db4e224 fix(tooling): 合并隔离测试覆盖率门禁` 修正完整门禁：主套件、WebM 时序族和低优先级预热族分别运行，使用 `coverage --append` 合并后再执行 `83%` 阈值；不再因为隔离测试未计入而误报 `82%`。
- 质量矩阵实测：`python scripts/check.py --quality` 在 Windows/Python 3.11 本机通过，`268 passed, 2738 deselected`，耗时 `14.75s`；完整门禁通过，主套件 `2937 passed, 11 skipped`、WebM `31 passed`、低优先级预热 `27 passed`，合并覆盖率 `40117` statements / `83%`。
- 未改变公共 CLI、Qt 运行时或 Core 自动更新协议；`pet/updater.py`、`pet/update_settings.py` 无差异。`plugin-roadmap-demo.html` 仍为未跟踪文件，旧文档没有删除或移动。
- 限制：本轮只完成 Windows offscreen 与本机工具链验证；可见桌面交互以及 macOS/Linux 实机记录仍需在对应环境完成。
- 后续文档修正：将 README 开发者章节中的 Python 3.10 旧说明统一为当前支持范围 Python 3.11–3.13。
