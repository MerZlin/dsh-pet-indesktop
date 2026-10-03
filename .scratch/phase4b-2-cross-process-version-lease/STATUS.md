# Phase 4B-2 状态

- **阶段**：跨进程版本租约
- **日期**：2026-10-03
- **当前状态**：内部实现与自动化/实机验收完成；实现提交 `5a1b6e0` 已推送至 `origin/codex/phase3-worker`，4B-1.5 人工/公开门保持 pending
- **前置门**：Phase 4B-1.5 内部门通过；资源人工/公开门保持 pending
- **提交/推送**：`5a1b6e0` 已创建并推送；本地 HEAD、远程跟踪和 `ls-remote` 已核验一致

## 已完成（初始记录）
- 已读取项目入口、连续性记录、Phase 4B-1.5 状态和本地管理状态。
- 已保留 dirty worktree，未覆盖前序用户修改。
- 已建立跨进程 lease 基础模块、长生命周期 `KernelLock`、状态监视器和 Worker bootstrap。
- Host、Settings 注入点和 Worker reservation/takeover 回调已接入；validation-only 路径仍保持进程内行为。
- `tests/test_feature_version_lease.py` 6 passed；`tests/test_feature_state_monitor.py` 2 passed；相关 focused suite 137 passed, 1 skipped（此前单次结果，最终门仍需重跑）。

## 进行中（初始记录）
- 补齐真实 QProcess 的 reservation → child lease takeover / no-confirmation 场景。
- 修正未确认 Worker reservation 的保守占用判定，避免父进程异常退出后误清理。
- 增加/核对 revision-bound 旧请求与结果的公开内部校验 seam。
- 完成 Windows 实机、三次高负载、ruff/mypy/pytest、文档检查和 PR 报告。

## 未完成（初始记录）
- 全量测试和最终静态检查尚未执行。
- 真实桌面/托盘/人工资源包验收不属于本阶段，仍保持 pending。
- 未提交、未推送、未做远程验证。

## 风险
Windows/POSIX 文件锁语义、父进程异常退出时的 handoff 窗口、残留记录清理以及现有 dirty worktree 是主要风险。无法证明安全时必须返回 `occupied` 或 `pending_confirmation`，不得删除版本。


## 2026-10-03 最终状态

### 已完成
- 跨进程租约协调器、长生命周期 OS `KernelLock`、租约诊断记录和保守占用检查已实现；`occupied`/`pending_confirmation` 在无法证明安全时阻止清理。
- 已接入生产安装路径的 Host、Standalone Settings 注入 seam、Worker reservation → child lease takeover；显式目录验证路径仍为 validation-only。
- `FeatureStateMonitor` 使用独立 Qt worker、`QFileSystemWatcher` 即时唤醒和默认 1 秒补偿轮询；状态文件 revision/digest 作为最终权威。
- 真实 multiprocessing、真实 `QProcess`、Qt event loop、Windows/POSIX 锁行为、旧选择拒绝、Settings 对话框持有期和 Worker 隔离测试已通过。
- 最终受影响聚焦套件：`270 passed, 1 skipped in 26.64s`。
- 受影响时序族连续三次：每次 `270 passed, 1 skipped`，PowerShell 实测 `27.972s / 27.921s / 26.350s`。
- `ruff check`、`ruff format --check`、受影响实现模块定向 `mypy`、offscreen 全量 `pytest` 已通过：`3581 passed, 12 skipped, 13 warnings in 479.78s`；`mypy pet tests` 仍为仓库既有基线 `1796 errors in 199 files`。真实桌面 QPA 下另有 6 个屏幕边界相关既有失败。
- PR 报告、`docs/INDEX.md`、`LOG.md`、`LOG-INDEX.md`、`docs/PROJECT-ENTRY.md` 和本阶段五份 scratch 记录已更新。

### 仍明确未完成/不属于本阶段
- Phase 4B-1.5 的资源包人工安装、重启后播放、可见桌面/托盘行为和公开稳定 API 仍 pending。
- 当前仓库没有自动选择已安装 Feature 的 Settings 生产入口，也没有完整的 Feature QAction/结果展示调用面；本阶段提供 `verify_current_selection` / `is_current_selection` 内部 seam，不宣称全产品入口已迁移。
- 不新增安装器 UI、远程目录、公开 SDK、任意第三方 Python 入口或 hot-unload；4B-3 事务和 4B-4/4B-5 管理/真实构建端到端仍未开始。
- 本轮提交、推送和远程核验已完成；既有用户修改未被覆盖，未使用强推；另有重启 GUI 实例仍由正常生命周期管理，不作为提交内容。

### 风险结论
当前未发现必须停止 4B-2 的风险停止条件。若后续在 Windows 锁/重命名语义、父子交接或残留清理中无法证明安全，应保留版本、返回 `occupied` 或 `pending_confirmation`，不得推进 4B-3 的破坏性清理。
- 文档收尾门：链接检查 123 份通过；PR 报告纪律 49 passed；文档单测 2 passed；`git diff --check` 退出码 0。
