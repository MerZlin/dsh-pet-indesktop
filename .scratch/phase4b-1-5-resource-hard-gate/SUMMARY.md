# Phase 4B-1.5：资源 DLC 硬门跨对话任务总结

## 1. 任务目标

在唯一安装状态账本（4B-1）之后，先把资源 DLC 的安装、Registry 解析、实际播放、冲突保护、Starter/Core fallback、派生 cache 隔离和资源侧恢复做成硬门，再进入跨进程版本租约（4B-2）。

## 2. 当前项目和分支状态

- 仓库：`E:\AI\DSH\dsh-pet-indesktop`
- 分支：`codex/phase3-worker`
- 工作树：包含 4B-1、4B-1.5 及本次文档连续性补档的未提交改动；不要使用 `reset --hard` 覆盖。
- 当前任务：代码、自动化、文档与保护门已具备本地封存基础；本文件只总结最终有效事实，不保存原始调试输出。

## 3. 已完成事项

- 修复并验证资源版本根路径与 Registry 解析。
- 覆盖目录包、ZIP 包、重启扫描、active/previous/fallback 和实际资源路径。
- 防止同版本不同内容冲突安装破坏旧 active；异常清理限定为本次操作拥有的 staging/temp。
- 覆盖安装到资源索引/播放链、Starter fallback、发布内容与派生 cache 隔离、资源侧中断恢复。
- 资源专项 `43 passed`；硬门专项连续三次各 `28 passed`。
- 阶段设计、PR 报告、索引、日志和阶段状态已有对应记录；本轮新增施工记录与本总结。

## 4. 已修改或新增文件

核心实现与测试（已存在于本阶段工作树）：

- `pet/content/manager.py`
- `pet/content/manifest.py`
- `pet/content/registry.py`
- `tests/test_content_dlc_hard_gates.py`

阶段证据与记录：

- `docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md`
- `docs/PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md`
- `.scratch/phase4b-1-5-resource-hard-gate/PLAN.md`
- `.scratch/phase4b-1-5-resource-hard-gate/HANDOFF.md`
- `.scratch/phase4b-1-5-resource-hard-gate/STATUS.md`
- `.scratch/phase4b-1-5-resource-hard-gate/WORKLOG.md`
- `.scratch/phase4b-1-5-resource-hard-gate/SUMMARY.md`

## 5. 关键架构和路线决策

- 资源版本目录是发布内容根；运行时派生 cache 必须在版本目录之外。
- 资源包不执行代码；资源接口稳定公开必须晚于 4B-1.5 所有硬门。
- Starter 是 Core 内置最小 fallback，不等于用户可删除的普通资源包；fallback 不得偷偷复活用户卸载的功能。
- `active/previous` 不是完整事务；后续通用管理继续按 4B-2/4B-3/4B-4/4B-5 分阶段推进。
- Worker、功能 host、安装管理和第三方生态不因资源硬门完成而提前开放。

## 6. 尚未完成事项

- 当前版本资源包的用户人工安装、重启后播放和可见桌面确认。
- 4B-2 跨进程 host/settings/worker 版本租约与状态同步。
- 4B-3 通用安装、升级、卸载和恢复事务。
- 4B-4 应用内扩展管理。
- 4B-5 两种真实构建产物的完整使用流程。
- Phase 6 资源 SDK 正式开放、外部 Worker 预览和第三方生态。

## 7. 当前阻塞与风险

- 资源接口只能作为内部验证，不能宣传为稳定第三方接口。
- 自动化通过不等于当前版本用户手测完成。
- Windows 文件占用、杀软/EDR、跨卷和三平台发布证据仍在后续门。
- 当前任务未重新核对远程分支；不在本总结中声称已推送。

## 8. 用户已经确认的计划

继续顺序为：`4B-1 状态账本 → 4B-1.5 资源硬门 → 4B-2 跨进程租约 → 4B-3 本地事务 → 4B-4 管理界面 → 4B-5 真实构建端到端`。
资源 DLC 先于第三方 Worker 开放；不开放任意 Python host，不承诺 Python host 即时热卸载；默认不开子智能体。

## 9. 用户偏好的回答方式

- 中文、正式、务实、简洁，先讲工程事实和边界。
- 每次计划/汇报最后用直白语言说明实际可体验效果和限制。
- 严格区分计划、实现、自动化、实机、用户确认、提交和推送。
- 需要新对话继续时，先读项目入口、阶段 STATUS/PLAN/HANDOFF/SUMMARY 和设计文档。

## 10. 必须保护的文件和边界

- 不修改 `pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`。
- 不覆盖用户已有工作树改动，不使用 `git add -A`、`git add .` 或 `reset --hard`。
- 不进入 4B-2 实现，不实现管理 UI、远程分发或第三方代码加载。
- 本阶段不读取真实 Key、不截图、不调用真实模型、不做长期 soak。

## 11. 新对话必须先阅读的文件

1. `docs/PROJECT-ENTRY.md`
2. `.scratch/phase4b-1-5-resource-hard-gate/STATUS.md`
3. `.scratch/phase4b-1-5-resource-hard-gate/PLAN.md`
4. `.scratch/phase4b-1-5-resource-hard-gate/HANDOFF.md`
5. `.scratch/phase4b-1-5-resource-hard-gate/SUMMARY.md`
6. `docs/plugin-phase-04-updates/PHASE4B-1.5-RESOURCE-HARD-GATE-DESIGN.md`
7. `docs/PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md`

## 12. 下一步准确动作

先完成本轮文档连续性任务的索引、日志和验证；本阶段本身的下一条工程动作是在独立授权后保存本地备份，然后重新确认 4B-2 入口。不得把本文件视为 4B-2 已开始。

## 13. 当前提交、推送和验证状态

- 4B-1.5：未在本轮创建提交；未推送。
- 已有专项和阶段测试结果如上，属于对应代码状态的历史/阶段证据，不冒充本轮全量测试。
- 本轮文档连续性检查尚未执行，执行结果写回当前任务记录，不在此处预写通过。

## 14. 面向用户的实际效果与限制

当前用户界面没有新增安装或卸载操作。资源硬门人工验收已通过，资源包安装更可能真实走到播放链，冲突不会误删旧版本，中断不会假装成功；但资源接口尚未正式对外稳定开放，真正的应用内管理仍需 4B-3 之后的阶段。

## 用户确认后的当前状态（2026-10-03）

Phase 4B-1.5 的人工验收门已由用户确认通过。代码、自动化、文档与保护门也已通过；公开稳定 API/SDK 仍未开放。Phase 4B-2 已完成并推送，后续阶段为 4B-3 安装/升级/卸载事务；本轮不实施 4B-3。
