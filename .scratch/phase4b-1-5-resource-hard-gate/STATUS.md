# Phase 4B-1.5 资源 DLC 硬门：当前状态

更新：2026-10-02。设计：[PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md](../../docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md)。任务：[PLAN.md](PLAN.md)。交接：[HANDOFF.md](HANDOFF.md)。

| 阶段/门 | 当前状态 | 说明 |
|---|---|---|
| 4B-1 唯一安装状态 | 已完成 | `state.json`、revision、操作幂等和状态恢复已实现；不等于资源文件事务。 |
| 4B-1.5 资源 DLC 硬门 | 代码、自动化、文档与保护门已通过；本地封存就绪 | 资源根路径、Registry/播放链、冲突清理、Starter/cache 边界和资源侧恢复已完成；尚未提交或推送，人工/公开发布仍待验收。 |
| 4B-2 跨进程版本租约 | 未开始 | 必须在 4B-1.5 封存后再实施。 |
| 资源公开稳定接口 | 未开放 | 可继续内部验证，不能供外部作者依赖未封存的 manifest/path 细节。 |
| Phase 6 资源 SDK | 未开放 | 依赖资源硬门和后续开放顺序。 |
| 用户可见新操作 | 无 | 没有新增安装、卸载或管理界面。 |
| 提交/推送 | 未完成 | 本轮不提交、不推送；保留工作树增量。 |

## 已有证据

- 资源专项及相关播放链：`43 passed in 4.29s`；
- 硬门测试连续 3 次：`28 passed in 3.80s`、`28 passed in 3.61s`、`28 passed in 3.77s`；
- Ruff lint/format、`mypy pet/content` 通过；
- 全量测试（最终文档编辑前代码状态）：`3562 passed, 12 skipped, 13 warnings in 356.90s`。

## 未完成与限制

- 当前版本资源包人工安装、重启播放、可见桌面和托盘操作未测试；
- 不读取真实 Key、不截图、不请求模型；不把资源自动化测试写成用户确认；
- 未实现 4B-2 租约、4B-3 安装/升级/卸载通用事务、4B-4 管理 UI、4B-5 两种冻结 Core 完整流程；
- 未开放资源作者 SDK、Worker 预览、远程 catalog 或 Workshop。

## 下一步

文档/索引/日志/保护门复核已通过；当前保留“本地封存就绪、尚未提交或推送”状态。获得独立提交授权并封存后，再制定并执行 4B-2。任何人工或发布门失败，仍停在 4B-1.5，不进入租约或管理 UI。

## 完成后的实际使用效果

桌宠现有界面和使用方式不变。代码层面资源包安装后的真实解析与播放链更可靠，但用户目前仍不能在桌宠里点击安装、停用或卸载资源；这些属于 4B-3 至 4B-5 的后续交付。

## 2026-10-03 测试副本人工补充与同步说明

- 使用仓库有效资源制作隔离测试副本 `package-shenshen-1.0.1`，独立 `APPDATA` 下完成 validate、安装、列表、Registry/catalog 和 `MovieLibrary` offscreen 探针；manifest digest 与实际包摘要一致，idle WebM 可读。
- 首次真实 Windows GUI 启动后，用户确认视觉通过并正常退出。
- 随后重启实例完成终端侧版本/Registry/idle 资源探针；该次重启的独立视觉确认未单独记录，因此“重启播放、桌面/托盘、公开稳定 API”仍保持 pending。
- 本阶段资料已纳入实现提交 `5a1b6e0` 并推送至 `origin/codex/phase3-worker`；不因此把资源硬门升级为公开完成。
