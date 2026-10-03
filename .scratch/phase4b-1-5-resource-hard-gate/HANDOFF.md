# Phase 4B-1.5 资源 DLC 硬门：当前准确停点

更新：2026-10-03。分支：`codex/phase3-worker`。

## 当前准确停点

Phase 4B-1.5 的资源代码、自动化、文档、保护门与人工验收已经完成并通过；实现提交 `5a1b6e0` 已推送，不进入公开稳定 API/SDK 开放门。Phase 4B-2 已完成，4B-3 尚未开始。本轮没有开子智能体。

已改动的核心实现仍在工作树：

- `pet/content/manager.py`：资源操作记录、operation-owned 清理、安装冲突保护和资源侧恢复；
- `pet/content/registry.py`：直接 package root 与明确嵌套 layout 的解析；
- `pet/content/manifest.py`：拒绝发布根中的派生 cache；
- `tests/test_content_dlc_hard_gates.py`：资源硬门专项测试。

## 最后一次运行时证据

- 资源专项及相关播放链：`43 passed in 4.29s`；
- 资源硬门测试连续 3 次：`28 passed in 3.80s`、`28 passed in 3.61s`、`28 passed in 3.77s`；
- 全量（最终文档编辑前的代码状态）：`3562 passed, 12 skipped, 13 warnings in 356.90s`；
- `ruff check` 通过；`ruff format --check` 报告 `436 files already formatted`；
- `mypy pet/content`：8 个源文件无问题。

这些是代码状态的运行时证据；随后只做文档与记录编辑，需要重新执行文档/保护门，不把未重跑的全量测试说成文档编辑后的新结果。

## 本次文档与保护复核（2026-10-02）

- `python scripts/check_docs.py`：`Markdown link check passed: 119 files scanned`。
- `python -m pytest -q tests/test_pr_report_discipline.py`：`47 passed（pytest exit code 0；运行耗时随环境变化，不作为固定基线）`。
- `git diff --check`：退出码 0；仅有换行规范提示，无 whitespace error。
- `pet/updater.py`、`pet/update_settings.py` 无差异；`plugin-roadmap-demo.html` 未进入状态。
- 全量运行时测试最后一次在本轮文档编辑前完成；本轮只编辑文档/记录，未重跑全量。

## 当前仍未完成

- 公开稳定资源 API/SDK、应用内管理 UI、通用安装/升级/卸载事务和远程分发；
- 真实 keyring/API Key/模型/屏幕操作；
- 默认构建切换、应用内管理 UI、4B-2 租约、4B-3 通用事务和远程分发；
- 资源接口正式公开或 Phase 6 资源 SDK 开放。

## 下一条准确动作

1. 已更新设计、PR 报告、`docs/INDEX.md`、`LOG.md`、`LOG-INDEX.md` 和 `SPEC.md`；
2. 已通过文档链接、PR 纪律、`git diff --check` 和保护文件检查；
3. 当前准确状态为“4B-1.5 代码、自动化、文档、保护门与人工门通过；4B-2 已完成并推送；公开稳定 API/SDK 未开放”；
4. 后续只规划 4B-3 安装/升级/卸载事务，不在本轮跳到管理 UI。

## 保护范围

不得修改：`pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`、历史 PR 报告、外部评审原文件、用户数据、生成构建产物和密钥。保留既有 Phase 4A、4B-1 及文档整理改动。

## 完成后的实际使用效果

当前没有新增用户操作。资源硬门人工验收已通过，代码让“安装后 Registry 能解析、播放链能找到资源、冲突不误删、恢复不误报”具备自动化与人工证据；资源接口尚未作为稳定公开 DLC 接口发布，也没有应用内安装/卸载按钮；下一步为后续规划的 4B-3、4B-4 和 4B-5。

## 2026-10-03 用户确认后的当前状态

- 用户确认 Phase 4B-1.5 资源 DLC 人工验收门已通过；此前“人工安装/重启播放/可见桌面及托盘仍 pending”只属于 2026-10-02 的历史停点。
- Phase 4B-1.5 的代码、自动化、文档、保护门和人工门均已通过；公开稳定资源 API、公开 SDK 与应用内管理 UI 仍未开放。
- Phase 4B-2 已完成并推送到 `origin/codex/phase3-worker`；4B-3 安装/升级/卸载事务尚未开始。
- 本轮不修改运行时代码、不新增 UI；后续先规划 4B-3，再决定实现范围。
