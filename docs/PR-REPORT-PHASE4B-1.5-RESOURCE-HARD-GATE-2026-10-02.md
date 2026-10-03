# Phase 4B-1.5 资源 DLC 硬门实施报告（2026-10-02）

> 状态：资源代码、自动化、文档与保护门已通过；本地封存就绪，尚未提交、未推送；人工/公开发布仍待验收。
> 范围：修复资源包根路径解析、冲突安装清理、安装到播放链、Starter/Core fallback、派生 cache 隔离和资源侧中断恢复。
> 非目标：4B-2 跨进程租约、通用安装器、管理 UI、远程分发、Worker DLC、默认构建切换。

## 当前事实与证据边界

本报告对应当前分支 `codex/phase3-worker` 的未提交工作树。外部评审提出的资源 P0 风险已按窄范围代码和回归测试处理；本报告不把资源自动化通过扩大解释为发布级 DLC 已开放。Phase 4B-1 的 `state.json` 状态账本仍与资源文件事务、租约和管理 UI 分开。

## 修改文件说明

本阶段核心增量如下，另有既有 Phase 4A/4B-1 文档与记录改动保留在同一脏工作树中：

| 文件 | 修改内容与原因 |
|---|---|
| `pet/content/manager.py` | 增加资源操作记录、operation-owned staging/temp 清理、冲突保护、激活自检回滚和资源侧卸载恢复，避免异常路径误删既有版本。 |
| `pet/content/registry.py` | 支持规范 package root 与明确声明的嵌套布局，修复已安装版本被重复追加角色路径的问题。 |
| `pet/content/manifest.py` | 拒绝发布根内的派生 cache，避免运行时缓存改变内容身份或混入激活版本。 |
| `tests/test_content_dlc_hard_gates.py` | 新增 28 项资源硬门回归，覆盖目录/ZIP、重启、冲突、播放路径、fallback、cache 和中断恢复。 |
| `docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md` | 固化资源硬门目标、顺序、非目标、实现事实和验收限制。 |
| `docs/PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md` | 本报告；记录文件、性能、实机限制和准确停点。 |
| `docs/INDEX.md`、`LOG.md`、`LOG-INDEX.md`、`SPEC.md` | 登记本阶段设计、证据和当前 Phase 4B 编号/状态。 |
| `.scratch/phase4b-1-5-resource-hard-gate/*` | 同步 PLAN/HANDOFF/STATUS，保留后续可继续执行的精确停点。 |

本报告不覆盖、不改写历史 PR 报告、外部评审原文件、自动更新实现或生成构建产物。变更行数以阶段提交前的最终 `git diff --numstat` 为准；当前尚未提交，故不在报告中伪造提交级行数。

## 性能分析

以下为代码状态、文档最终编辑前的有界实测，不是长期 soak：

| 样本 | 命令/环境 | 实际结果 |
|---|---|---|
| 资源专项与播放相关回归 | `python -m pytest -q tests/test_content_dlc.py tests/test_content_dlc_hard_gates.py tests/test_library_warm_priority.py tests/test_library_priority_warm.py tests/test_island_content_cache.py` | 43 passed，4.29 秒。 |
| 资源硬门短程复跑 | `python -m pytest -q tests/test_content_dlc_hard_gates.py`，连续 3 次 | 28 passed，分别 3.80 秒、3.61 秒、3.77 秒。 |
| 全量回归 | `QT_QPA_PLATFORM=offscreen; python -m pytest -q` | 3562 passed、12 skipped、13 warnings，356.90 秒；这是最终文档编辑前的代码结果。 |
| 静态检查 | `python -m ruff check pet tests scripts`；`python -m ruff format --check pet tests scripts`；`python -m mypy pet/content` | Ruff 通过；436 files already formatted；mypy 8 个源文件无问题。 |

本阶段新增的主要成本是资源操作记录、目录校验和窄范围文件恢复；没有新增常驻进程、网络请求或长期线程。专项测试使用临时目录和确定性资源，不读取真实 Key、不截图、不调用收费模型。未执行长期 soak；不能用这些结果推导跨平台文件系统或真实磁盘断电持久性。

## 实机运行记录

本阶段未完成当前版本的用户资源包人工安装、可见桌面播放、重启后播放或托盘退出验证。没有为了补证据读取用户真实资源、密钥、截图或调用模型。此前用户确认的手动“看看屏幕”不属于资源 DLC 硬门证据；自动识屏也没有被本阶段重新判定。

可复现的真实环境探针尚未运行，因此本节结论是“待实机验收”，不是“实机通过”。自动化测试已覆盖资源路径、索引和文件可读性合同，但不等于用户在桌面上看到动画实际播放。

## 文档与保护复核

- `python scripts/check_docs.py`：`Markdown link check passed: 119 files scanned`。
- `python -m pytest -q tests/test_pr_report_discipline.py`：`47 passed（pytest exit code 0；运行耗时随环境变化，不作为固定基线）`。
- `D:\DELL\Git\cmd\git.exe diff --check`：退出码 0；仅有换行规范提示，无 whitespace error。
- 保护文件核对：`diff -- pet/updater.py pet/update_settings.py` 为空；`status --short -- plugin-roadmap-demo.html` 为空。
- 全量运行时测试最后一次在本轮文档编辑前完成（`3562 passed, 12 skipped, 13 warnings in 356.90s`）；本轮仅文档/记录编辑，未重跑全量。

## 验收结论与剩余限制

- 已实现：资源硬门代码、专项测试、短程稳定性复跑、静态检查和文档证据。
- 本地封存门：代码、自动化、文档与保护文件检查均已通过；阶段独立提交仍待单独授权。
- 未完成：用户资源包实机安装/重启播放、默认构建切换、正式签名/公开 SDK。
- 未开始：4B-2 跨进程租约、4B-3 通用安装/升级/卸载事务、4B-4 管理 UI、4B-5 两种冻结 Core 端到端流程。

## 回滚与下一步

阶段代码可按文件主题独立回滚；不使用 `reset` 覆盖既有工作树。若本轮文档或保护门失败，停在 Phase 4B-1.5，不进入 4B-2；若后续资源实机门失败，保留旧资源路径和可回滚点，修复后重新执行资源专项。

完成本轮文档/保护复核并获得单独提交授权后，才建立 Phase 4B-1.5 封存提交。之后再按新计划设计和实施 4B-2，不能用资源自动化结果代替租约或管理验收。

## 完成后的实际使用效果

当前桌宠界面和菜单没有新增操作，用户仍不能在桌宠里点击安装、停用或卸载资源。代码层面，资源包安装后更可能沿着正确的 Registry → catalog → MovieLibrary 路径找到实际文件，冲突安装不会因异常清理误删旧版本，缓存也不会冒充发布内容。资源 DLC 仍未作为公开稳定接口发布；应用内管理、重装保留配置和真正可拔除功能包留到后续 4B-2 至 4B-5。

## 2026-10-03 测试副本人工补充证据

- 使用仓库有效资源制作隔离测试副本 `package-shenshen-1.0.1`，独立 `APPDATA` 下完成 validate、安装、列表、Registry/catalog 和 `MovieLibrary` offscreen 探针；manifest digest 与实际包摘要一致，idle WebM 可读。
- 首次真实 Windows GUI 启动后，用户确认视觉通过并正常退出。
- 随后重启实例完成终端侧版本/Registry/idle 资源探针；该次重启的独立视觉确认未单独记录，因此“重启播放、桌面/托盘、公开稳定 API”仍保持 pending。
- 这份补充证据只提高本地/人工确认精度，不把资源硬门改写成公开 DLC 完成。
