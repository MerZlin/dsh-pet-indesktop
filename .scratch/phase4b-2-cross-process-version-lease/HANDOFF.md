# Phase 4B-2 交接

## 精确停点
2026-10-03：已建立本阶段正式记录；跨进程 lease coordinator、`KernelLock`、`FeatureStateMonitor`、Host/Settings 接入和 Worker handoff 代码已在工作树。下一步是完成真实 QProcess 交接红/绿测试，并修正未确认 reservation 的保守判定。

## 已知约束
- 不使用子智能体。
- 保留既有 dirty worktree，不覆盖用户修改；实施阶段不自动提交/推送，本轮已获得用户明确提交/推送授权。
- 不能用 PID、时间戳、TTL 或 heartbeat 单独证明租约已释放。
- Worker bootstrap 不得导入 UI、`pet.app` 或 `pet.plugins`。
- 4B-1.5 人工资源/公开 API 门继续 pending，不得在本轮宣称公开完成。

## 最近验证
- `python -m pytest -q tests/test_feature_version_lease.py`：6 passed。
- `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_feature_state_monitor.py`：2 passed。
- 相关 focused suite：137 passed, 1 skipped（历史单次结果；最终验收前需重跑）。

## 下一条操作
1. 增加真实 Windows `QProcess` handoff/no-confirmation 测试；
2. 将 released、未确认的 `worker_reservation` 判为 `pending_confirmation`，不自动清理；
3. 运行 affected timing tests 三次并执行全量门；
4. 写入实际 `numstat`、性能数字、实机输出和限制到 PR 报告。


## 2026-10-03 最终交接

### 精确停点
Phase 4B-2 的内部实现、自动化测试、Windows 实机 QProcess 交接、三次高负载、静态检查、offscreen 全量测试和文档证据已完成。实现提交 `5a1b6e0` 已推送；下一阶段不能直接按“公开插件/资源完成”处理，需先完成 4B-1.5 人工/公开门的独立验收。

### 最终验证
- affected focused：`270 passed, 1 skipped in 26.64s`；
- timing 3x：每次 `270 passed, 1 skipped`，PowerShell `27.972s / 27.921s / 26.350s`；
- offscreen full：`3581 passed, 12 skipped, 13 warnings in 479.78s`；真实桌面 QPA 另有 6 个既有屏幕边界失败，不作为本阶段产品回归；
- Ruff/format 通过；受影响实现模块定向 `mypy` 14 个文件通过；`mypy pet tests` 的仓库既有基线为 `1796 errors in 199 files`，不宣称全仓库通过；
- Windows benchmark：200 acquire/close median 31.007ms、p95 44.302ms；1000 selection revalidate median 3.387ms；200 free occupancy inspect median 1.191ms；
- `python scripts/check_docs.py`：`Markdown link check passed: 123 files scanned`；`python -m pytest -q tests/test_pr_report_discipline.py`：`49 passed in 0.56s`；`git diff --check`：退出码 0，仅有 LF/CRLF 转换提示。

### 继续工作时先读
1. `docs/PR-REPORT-PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-2026-10-03.md`；
2. 本目录 `STATUS.md`、`PLAN.md`、`SUMMARY.md`；
3. `docs/PROJECT-ENTRY.md` 与 `docs/INDEX.md`；
4. 4B-1.5 状态记录，确认人工门已通过、公开稳定 API 仍未开放。

### 不得做的事
不要使用 `reset --hard`、覆盖用户改动或强推；不要用 PID/TTL/heartbeat 清理版本；不要把 validation-only 路径或无 handoff 的 legacy Worker probe 解释为生产租约成功；不要在 4B-3 前新增破坏性删除。

## 2026-10-03 提交与远程核验

- 实现提交：`5a1b6e0`（`feat: 完成 Phase 4B 资源与跨进程版本租约`）。
- 推送目标：`origin/codex/phase3-worker`；远程 `ls-remote` 为 `5a1b6e0ae7cc9a16bab6bebed8e3b9c52c4b2865`。
- 本地 `git rev-list --left-right --count HEAD...origin/codex/phase3-worker`：`0 0`。
- 没有强推；忽略的测试副本、日志、缓存和构建产物未纳入提交。
- 4B-1.5 的人工门已由用户确认通过；公开稳定 API/SDK 仍按独立开放门处理，不能因本次提交/推送改写为公开完成。

## 2026-10-03 用户确认后的当前状态

- Phase 4B-1.5 的人工验收门已由用户确认通过；公开稳定资源 API/SDK 仍未开放。
- Phase 4B-2 的内部实现、自动化、实机证据、文档与提交推送均已完成；实现提交为 `5a1b6e0`，本轮文档状态同步将在当前任务形成新的文档提交。
- 4B-3 安装/升级/卸载事务尚未开始；后续先单独规划事务边界、幂等、中断恢复和回滚，不在本轮提前实现。
