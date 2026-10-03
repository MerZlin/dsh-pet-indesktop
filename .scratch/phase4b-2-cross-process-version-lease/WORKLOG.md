# Phase 4B-2 工作日志

## 2026-10-03
- 读取 `docs/PROJECT-ENTRY.md`、documentation continuity 五份记录、Phase 4B-1.5 状态和 Phase 4B local management 状态，并按工程模式加载详细协议。
- 确认 Phase 4B-1.5 作为内部前置门；人工资源包、桌面/托盘和公开稳定 API 继续 pending。
- 检查到工作树已有前序 dirty 修改；本阶段不覆盖、不提交、不推送。
- 建立本阶段 `PLAN.md`、`STATUS.md`、`HANDOFF.md`、`WORKLOG.md`、`SUMMARY.md` 和设计文档。
- 基础代码现状：`feature_state_io` 已支持长生命周期 `KernelLock`；新增 lease coordinator、state monitor、child bootstrap；Host/Settings/Worker seam 已接入。
- focused lease/state-monitor/相关 worker suite 的历史单次结果已记录，仍需本轮最终重跑。
- 当前停点：补齐真实 QProcess takeover 测试和父进程异常/未确认 reservation 的保守门。

## 2026-10-03 收尾记录

- 完成真实 `QProcess` reservation → child lease takeover 和 no-confirmation 失败路径；父 reservation 只在 JSONL `lease_claimed` 确认后释放。
- 将父进程在交接中异常退出、但 reservation 未被安全确认的状态固定为 `pending_confirmation`，不依赖超时、PID 或时间戳删除。
- 增加 `verify_current_selection` / `is_current_selection`，以 state revision、digest、enabled/active 和 generation 重新验证旧选择；无效选择拒绝执行/结果展示。
- 运行最终 focused suite：`169 passed, 1 skipped in 19.28s`。
- 运行受影响时序族高负载三次：每次 `72 passed`，PowerShell wall time `12602ms / 11780ms / 12348ms`。
- 第一轮全量出现 12 个失败，其中 10 个是低层原子写入 OSError 未保持既有 `StateError("io_error")` 合同；补充兼容包装后，相关状态测试 `88 passed`，最终全量 `3579 passed, 12 skipped, 13 warnings in 338.02s`。
- `python -m ruff check pet features tests`、`python -m ruff format --check pet features tests`（439 files）和 `python -m mypy` 均通过。
- Windows 实测 benchmark（Python 3.11.1 / Windows 10 10.0.26100）：lease acquire/close 200 样本 median 31.007ms、p95 44.302ms、max 53.347ms；selection revalidate 1000 样本 median 3.387ms、p95 5.699ms；free occupancy inspect 200 样本 median 1.191ms、p95 1.304ms。
- 已补齐 PR 报告、索引、入口、日志和本阶段五份 scratch 记录；没有提交、推送或远程验证。
- 文档收尾门通过：`python scripts/check_docs.py` 扫描 123 份文件；PR 报告纪律 `49 passed`；`tests/test_check_docs.py` 为 `2 passed`；`git diff --check` 退出码 0（仅换行转换提示）。

## 2026-10-03 用户授权后的最终核验

- 用户明确授权：将截至目前应纳入范围的修改提交并推送到当前远程分支；保留用户工作树，不强推，不加入测试生成包、日志、缓存或密钥。
- 影响测试族重新执行 1 次：`270 passed, 1 skipped in 26.64s`；同一命令高负载连续 3 次：每次 `270 passed, 1 skipped`，PowerShell wall time `27.972s / 27.921s / 26.350s`。
- 按项目规定的 `QT_QPA_PLATFORM=offscreen; python -m pytest -q` 全量：`3581 passed, 12 skipped, 13 warnings in 479.78s`。
- 直接真实桌面 QPA 的全量测试出现 6 个既有拖拽合帧/屏幕边界失败；offscreen 门通过，未把桌面分辨率差异误报为本阶段回归。
- 受影响实现模块定向 `mypy` 14 个文件通过；`python -m mypy pet tests` 的仓库既有基线为 `1796 errors in 199 files`，不宣称全仓库通过。
- Phase 4B-1.5 测试副本的终端验证和首次真实 GUI 视觉/正常退出已记录；重启实例的终端探针通过，但独立视觉确认仍未单独记录，人工/公开门继续 pending。
