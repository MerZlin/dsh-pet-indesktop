# Phase 4B-1.5：资源 DLC 硬门执行清单

更新：2026-10-02。分支：`codex/phase3-worker`。

状态：**代码、自动化、文档与保护门已通过，本地封存就绪；尚未提交或推送**。本任务位于 Phase 4B-1 状态账本之后、Phase 4B-2 跨进程版本租约之前；本轮不提交、不推送，不进入 4B-2。

权威设计：[Phase 4B-1.5 资源硬门设计](../../docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md)。阶段状态：[STATUS](STATUS.md)。准确停点：[HANDOFF](HANDOFF.md)。

## 固定边界

- 保留既有 Phase 4A、Phase 4B-1 和其他未提交改动；不使用 `reset`、`git add -A` 或 `git add .`。
- 只处理资源安装后的 Registry 解析、实际播放链路、冲突清理、Starter/Core fallback、发布内容与派生 cache 隔离、资源侧中断恢复。
- 不实现远程下载、Workshop、任意 Python 代码、官方功能 host、Worker DLC、4B-2 租约、安装管理 UI 或默认构建切换。
- 保护 `pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`、历史 PR 报告、外部评审原文件和用户数据。

## 子任务

- [x] **4B-1.5.0 基线和失败测试**：记录既有资源测试，新增 `tests/test_content_dlc_hard_gates.py`，固定失败边界后实现。
- [x] **4B-1.5.1 资源根路径与 Registry 解析**：统一目录包、ZIP 包、重启扫描、active/previous/fallback 的版本根路径语义。
- [x] **4B-1.5.2 冲突安装与异常清理**：同摘要幂等；同版本不同摘要拒绝；只清理本次 operation-owned staging/temp。
- [x] **4B-1.5.3 安装到实际播放 E2E**：贯通 Registry、catalog、MovieLibrary 索引及指定资源文件可读性；不以 pointer 存在代替播放链断言。
- [x] **4B-1.5.4 Starter/Core 更新保护**：验证 fallback 优先级、用户资源保留和已卸载资源不被 fallback 偷偷复活。
- [x] **4B-1.5.5 内容与派生缓存隔离**：发布版本根只读；派生 cache 外置，不进入内容身份或安装包。
- [x] **4B-1.5.6 卸载中断与恢复**：资源侧操作记录、禁止执行、pointer/目录清理和可重复恢复。
- [x] **4B-1.5.7 证据、文档和关闭**：设计、PR 报告、索引、日志和保护门已补齐；代码、自动化、文档与保护门已通过，本地封存就绪，尚未提交或推送。

## 证据状态

- **计划**：已落盘；路线已统一为 `4B-1 → 4B-1.5 → 4B-2 → 4B-3 → 4B-4 → 4B-5`。
- **实现**：资源代码和专项测试已完成，未改变自动更新、公共 CLI 或 Worker 协议。
- **自动化**：专项 43 passed；硬门测试连续三次分别 28 passed；最终文档编辑前全量 3562 passed、12 skipped、13 warnings。
- **静态检查与文档门**：Ruff lint/format、`mypy pet/content`、文档链接、PR 报告纪律和 `git diff --check` 已通过；保护文件也已核对无差异。
- **实机**：未进行当前版本用户资源包人工安装、重启播放、托盘退出或可见桌面验证；不将自动化替代实机证据。
- **用户确认**：本轮未进行资源 DLC 手测；此前识屏手测不属于本任务证据。
- **提交/推送**：未提交、未推送；工作树包含本任务与既有未提交改动。

## 下一步

1. 文档、索引、日志、`SPEC.md` 和保护范围复核已完成。
2. `check_docs.py`、PR 报告纪律、`git diff --check` 和保护文件检查均已通过。
3. 当前停在“本地封存就绪、尚未提交或推送”；本轮不自动提交或推送。
4. 获得独立提交授权并完成封存后，才可按单独计划进入 Phase 4B-2 跨进程版本租约。
