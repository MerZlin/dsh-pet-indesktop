# 文档连续性与项目入口：实施计划

- **任务编号**：DOC-CONTINUITY-2026-10-02
- **状态**：已完成（文档变更未提交）
- **目标**：固化施工记录、跨对话任务总结、项目入口文档和开发者版 README。
- **范围**：协作规则、计划/汇报规范、交接规范、项目入口、README、当前开放任务记录、索引与日志。
- **非目标**：不修改 `pet/` 运行时代码、`tests/` 测试实现、`packaging/` 打包脚本、自动更新实现、Phase 4B 生产逻辑、演示 HTML；不提交、不推送。

## 稳定任务清单

| 编号 | 任务 | 状态 | 完成证据 |
|---|---|---|---|
| DOC-0 | 记录工作树与保护文件基线 | 已完成 | 当前对话基线命令及 `WORKLOG.md` |
| DOC-1 | 创建本任务 PLAN/WORKLOG/HANDOFF/STATUS/SUMMARY | 已完成 | 本目录五份记录 |
| DOC-2 | 更新 `AGENTS.md` 授权与子智能体规则 | 已完成 | `AGENTS.md` 协作授权补充章节 |
| DOC-3 | 更新计划、汇报和交接规范 | 已完成 | `docs/agents/planning-and-reporting.md`、`handoff.md` |
| DOC-4 | 新建施工规则文档 | 已完成 | `docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md` |
| DOC-5 | 新建渐进式披露入口文档 | 已完成 | `docs/PROJECT-ENTRY.md` |
| DOC-6 | 重写开发者版 `README.md` | 已完成 | 根目录 `README.md` |
| DOC-7 | 补档 Phase 4B-1.5 与 Phase 4B 任务记录 | 已完成 | 两个 `.scratch` 任务目录中的 WORKLOG/SUMMARY 及既有记录更新 |
| DOC-8 | 更新 `docs/INDEX.md`、`LOG.md`、`LOG-INDEX.md` | 已完成 | 索引、日志和连续性入口已登记 |
| DOC-9 | 运行文档与保护门检查 | 已完成 | 文档检查 121 份；PR 纪律 47 passed；`diff --check` 通过；保护文件无差异 |
| DOC-10 | 完成最终交接与跨对话总结 | 已完成 | 本目录最终版 `HANDOFF.md`、`STATUS.md`、`SUMMARY.md` |

## 依赖与停止条件

- DOC-2 至 DOC-8 只修改文档和任务记录；如果发现代码、测试或保护文件意外变化，停止并分类。
- 文档链接检查、PR 报告纪律或 `git diff --check` 失败时，先修正本轮新增链接/格式；不通过删除链接规避。
- 不使用 `git add -A`、`git add .`、`reset --hard` 或强推。
- 本轮不创建本地提交；若后续用户明确要求备份，再按显式文件清单提交。远程推送必须另行授权。
- 默认不开子智能体；只有用户明确要求时才允许使用。

## 实际验证结果

- `python scripts/check_docs.py`：`Markdown link check passed: 121 files scanned`，退出码 0。
- `python -m pytest -q tests/test_pr_report_discipline.py`：`47 passed`，退出码 0。
- `D:\DELL\Git\cmd\git.exe diff --check`：退出码 0；仅出现 Git 的 CRLF 转换提示，没有空白错误。
- `pet/updater.py`、`pet/update_settings.py` 无差异；`plugin-roadmap-demo.html` 未进入状态。
- 本轮只涉及文档、规则和任务记录，因此没有重新运行全量运行时测试；这不改变既有代码测试结果，也不宣称本轮全量运行时测试全绿。

## 完成判定

- 新对话可以从 `docs/PROJECT-ENTRY.md` 逐层进入路线和当前任务。
- README 面向开发者，明确运行、测试、Worker/DLC 边界和文档入口。
- 施工记录保留步骤、证据和准确停点；任务总结只保留可继续工作的最终事实。
- 当前 Phase 4B-1.5、Phase 4B 的状态与未完成门没有被夸大。
- 本任务已完成，但仍未创建本地提交或远程推送；后续需要按用户单独授权处理。
