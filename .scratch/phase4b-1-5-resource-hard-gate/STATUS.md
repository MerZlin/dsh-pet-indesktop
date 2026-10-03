# Phase 4B-1.5 资源 DLC 硬门：当前状态

更新：2026-10-03。设计：[PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md](../../docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md)。任务：[PLAN.md](PLAN.md)。交接：[HANDOFF.md](HANDOFF.md)。

| 阶段/门 | 当前状态 | 说明 |
|---|---|---|
| 4B-1 唯一安装状态 | 已完成 | `state.json`、revision、操作幂等和状态恢复已实现；不等于资源文件事务。 |
| 4B-1.5 资源 DLC 硬门 | 代码、自动化、文档、保护门与人工验收已通过 | 资源根路径、Registry/播放链、冲突清理、Starter/cache 边界和资源侧恢复已完成；公开稳定 API 仍受后续开放门约束。 |
| 4B-2 跨进程版本租约 | 已完成 | 内部实现、自动化与 Windows 实机验收已完成并推送；4B-3 尚未开始。 |
| 资源公开稳定接口 | 未开放 | 可继续内部验证，不能供外部作者依赖未封存的 manifest/path 细节。 |
| Phase 6 资源 SDK | 未开放 | 依赖资源硬门和后续开放顺序。 |
| 用户可见新操作 | 无 | 没有新增安装、卸载或管理界面。 |
| 提交/推送 | 已完成/随本轮同步交付 | 4B-1.5 与 4B-2 实现提交已推送；本轮文档状态同步按当前授权提交并推送。 |

## 已有证据

- 资源专项及相关播放链：`43 passed in 4.29s`；
- 硬门测试连续 3 次：`28 passed in 3.80s`、`28 passed in 3.61s`、`28 passed in 3.77s`；
- Ruff lint/format、`mypy pet/content` 通过；
- 全量测试（最终文档编辑前代码状态）：`3562 passed, 12 skipped, 13 warnings in 356.90s`。

## 未完成与限制

- 4B-1.5 资源包人工安装、重启播放、可见桌面和托盘人工门已由用户确认通过；公开稳定 API 仍未开放；
- 不读取真实 Key、不截图、不请求模型；不把资源自动化测试写成用户确认；
- 4B-2 租约已完成；尚未实现 4B-3 安装/升级/卸载通用事务、4B-4 管理 UI、4B-5 两种冻结 Core 完整流程；
- 未开放资源作者 SDK、Worker 预览、远程 catalog 或 Workshop。

## 下一步

文档/索引/日志/保护门与人工验收均已通过；4B-2 已完成并推送。当前仍未进入 4B-3 安装/升级/卸载事务；公开稳定 API 和管理 UI 仍需后续门。

## 完成后的实际使用效果

桌宠现有界面和使用方式不变。代码层面资源包安装后的真实解析与播放链更可靠，但用户目前仍不能在桌宠里点击安装、停用或卸载资源；这些属于 4B-3 至 4B-5 的后续交付。

## 2026-10-03 测试副本人工补充与同步说明

- 使用仓库有效资源制作隔离测试副本 `package-shenshen-1.0.1`，独立 `APPDATA` 下完成 validate、安装、列表、Registry/catalog 和 `MovieLibrary` offscreen 探针；manifest digest 与实际包摘要一致，idle WebM 可读。
- 首次真实 Windows GUI 启动后，用户确认视觉通过并正常退出。
- 随后重启实例完成终端侧版本/Registry/idle 资源探针；用户于 2026-10-03 确认 4B-1.5 人工门通过。公开稳定 API 不属于本次人工门确认，仍保持未开放。
- 本阶段资料已纳入实现提交 `5a1b6e0` 并推送至 `origin/codex/phase3-worker`；人工门通过不等于资源 SDK 或公开生态已开放。
