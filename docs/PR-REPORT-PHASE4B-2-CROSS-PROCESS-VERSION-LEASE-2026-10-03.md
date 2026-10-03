# Phase 4B-2 跨进程版本租约实施报告（2026-10-03）

> **状态**：内部实现、自动化和 Windows 实机证据完成；实现提交 `5a1b6e0` 已推送至 `origin/codex/phase3-worker`。Phase 4B-1.5 的资源人工安装/重启播放/桌面托盘/公开稳定 API 仍 pending，不以本报告宣称公开完成。
> **基线**：`ce16484`（本阶段提交前父提交）　**实现提交**：`5a1b6e0`　**分支**：`codex/phase3-worker`　**日期**：`2026-10-03`
> **范围**：实现 14 个文件、测试 5 个文件、文档/记录 11 个文件（含本报告）。本报告只归因 Phase 4B-2 直接变更；既有 dirty worktree 的其他文件不在本阶段归因范围内。
> **关联**：[Phase 4B-2 设计](plugin-phase-04-updates/PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md)、[Phase 4B-1.5 报告](PR-REPORT-PHASE4B-1.5-RESOURCE-HARD-GATE-2026-10-02.md)、[阶段状态](../.scratch/phase4b-2-cross-process-version-lease/STATUS.md)

## 一、核心特性

本阶段把已安装 Feature 版本的“能否删除/替换”从 PID、时间戳或诊断 JSON 提升为跨进程 OS 内核锁证明：每个 Feature 版本有独立 leases 协调锁和每个租约独立内核锁；只要仍有活跃锁，或系统无法证明锁/交接已安全释放，就返回 `occupied` 或 `pending_confirmation`，不执行破坏性清理。旧选择在执行前重新绑定 `feature_id`、`version`、`revision`、manifest/package digest、enabled/active 和 generation。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 跨进程版本租约 | `FeatureVersionLeaseCoordinator` 提供 Host、Settings、Worker reservation/child lease；`CrossProcessLease.close()` 幂等。 |
| 2 | 状态与租约同临界区复核 | 获取租约前重新读取 state、校验安装 revision/digest 和已验证 `PackageDescriptor`，拒绝过期或不匹配选择。 |
| 3 | Host / Settings 生命周期 | Host 进入 Python interpreter 后不立即释放；Settings lease 绑定 Qt 对话框生命周期；validation-only 显式目录不改变原进程内行为。 |
| 4 | Worker 父子交接 | 父进程先 reservation，受控环境字段传一次性 opaque token；child 在 import runtime 前 claim，JSONL hello 明确 `lease_claimed`，确认后父进程才释放。 |
| 5 | 状态监视与旧请求保护 | `QFileSystemWatcher` 只负责唤醒，1 秒轮询补偿；`verify_current_selection` / `is_current_selection` 在执行和结果展示前复核。 |

**红线 / 不变量**：

- 不以 PID、时间戳、TTL、heartbeat 或租约 JSON 单独证明进程仍存活/已经释放；
- 不支持 Python hot-unload；Host import/factory 失败、禁用或撤销不会让已进入 interpreter 的版本立即释放；
- 不修改已签名 Worker `program` 与 `arguments`，不接受任意第三方 Python 入口；
- child bootstrap 不导入 UI、`pet.app` 或 `pet.plugins`；
- 生产安装路径才获取跨进程租约，显式验证目录明确标记为 validation-only；
- 4B-2 不新增安装器 UI、远程目录、公开 SDK，亦不覆盖用户已有 dirty worktree。

## 二、修改文件说明

以下增删来自本阶段提交前工作树：已修改的 tracked 文件使用 `git diff --numstat` 的整段变更；未跟踪文件使用当前文件行数作为 `+N / −0`。因此这些数字是交付证据和审查边界，不应被误读为一个干净 commit 的纯阶段 delta；前序 4B-1 文件特别标注为混合文件。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/feature_state_io.py` | +189 / −0 | 新增 `KernelLock`、Windows `LockFileEx`/POSIX `flock`、原子写入和路径安全辅助；为租约与状态 CAS 提供真实长生命周期内核锁。 |
| `pet/feature_version_lease.py` | +610 / −0 | 新增跨进程 lease coordinator、selection、occupancy、Host/Settings/Worker 角色、一次性 reservation takeover、保守清理及进程退出释放。 |
| `pet/feature_state_monitor.py` | +158 / −0 | 新增独立 Qt worker、`QFileSystemWatcher` 唤醒和 1 秒补偿轮询；通知只触发重新读取，revision 仍由状态账本判定。 |
| `pet/feature_install_state.py` | +464 / −0 | 前序 4B-1 未跟踪文件；本阶段补齐低层 OSError 到既有 `StateError("io_error")` 契约的兼容包装，避免 lease 接入改变状态层错误语义。行数为当前整文件，不是纯本阶段增量。 |
| `pet/plugins/feature_packages.py` | +94 / −31 | Host 进入 verified interpreter 后保留 process lease；新增 installed Host/Settings acquisition seam，导入/factory 失败不热卸载。 |
| `pet/plugins/package_binding.py` | +17 / −2 | 生产安装路径要求 selection + coordinator 并绑定 Host/Settings/Worker lease；validation-only 显式目录路径保持进程内行为。 |
| `pet/plugins/worker_launch.py` | +44 / −1 | 父进程先取得 worker reservation，使用受控环境字段传 token，保留签名 program/arguments，确认 child lease 后才释放 reservation。 |
| `pet/workers/launch.py` | +26 / −0 | 扩展 Worker launch 状态与幂等 handoff confirm/abort 回调，保留前五个位置参数兼容性。 |
| `pet/workers/supervisor.py` | +9 / −0 | 要求生产 handoff 的初始 JSONL hello 带 `lease_claimed=true`，否则不确认父 reservation。 |
| `pet/workers/lease_bootstrap.py` | +34 / −0 | 新增最小 child bootstrap；只读取受控环境、claim child lease、设置确认标记，不导入 UI、`pet.app` 或 `pet.plugins`。 |
| `pet/__main__.py` | +14 / −3 | Worker 进入实现 import 前执行 child bootstrap；Settings 支持显式注入 lease，并在 Qt 事件循环结束后释放。 |
| `pet/settings_standalone.py` | +17 / −0 | 新增 dialog 生命周期绑定 seam；对话框 finished 前持续持有 settings lease。 |
| `features/screen_understanding/worker/runtime.py` | +1 / −1 | 官方 Worker hello 增加 `lease_claimed` 状态，供 Supervisor 明确确认交接。 |
| `pet/workers/agent_link_worker.py` | +9 / −1 | 官方 Worker hello 增加 `lease_claimed` 状态；不改变 Worker 隔离和协议入口。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_feature_version_lease.py` | +235 / −0 | 真实 multiprocessing、双 Core、Core/Settings/Worker 并占、revision/digest 拒绝、导入失败占用、reservation takeover、崩溃后 OS 锁释放和 pending 清理。 |
| `tests/test_feature_state_monitor.py` | +83 / −0 | 验证 watcher/轮询、独立 Qt worker、revision 权威读取和关闭行为。 |
| `tests/test_feature_worker_handoff.py` | +161 / −0 | 真实 Windows `QProcess`、Qt event loop、child claim 确认、无确认失败和 reservation abort。 |
| `tests/test_feature_packages.py` | +45 / −0 | 验证 Host import/factory 失败后 process lease 仍占用，显式释放后才可清理。 |
| `tests/test_settings_process_isolation.py` | +36 / −0 | 验证真实 QDialog 存活期间 settings lease 占用，finished 后释放。 |

### 文档与持续记录

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/plugin-phase-04-updates/PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md` | +58 / −0 | 固化目录、锁权威、三类生命周期、Worker 交接、状态监视、停止条件和非目标。 |
| `docs/PR-REPORT-PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-2026-10-03.md` | +177 / −0 | 本报告；记录当前 dirty worktree 下的文件、性能、实机和限制证据。 |
| `docs/INDEX.md` | +29 / −5 | 登记本报告、设计文档和 4B-2 scratch 入口。 |
| `docs/PROJECT-ENTRY.md` | +155 / −0 | 更新当前路线位置，区分 4B-2 内部完成与 4B-1.5 人工/公开 pending。 |
| `LOG.md` | +58 / −0 | 追加 4B-2 已发生事实、验证数字和用户可见限制。 |
| `LOG-INDEX.md` | +5 / −0 | 登记 2026-10-03 4B-2 日志入口。 |
| `.scratch/phase4b-2-cross-process-version-lease/PLAN.md` | +39 / −0 | 更新 4B-2.6 证据门和阶段状态。 |
| `.scratch/phase4b-2-cross-process-version-lease/STATUS.md` | +51 / −0 | 记录最终实现、验证、限制和停止条件。 |
| `.scratch/phase4b-2-cross-process-version-lease/HANDOFF.md` | +45 / −0 | 记录精确停点、最终门和后续阅读顺序。 |
| `.scratch/phase4b-2-cross-process-version-lease/WORKLOG.md` | +23 / −0 | 记录失败修复、测试、性能和文档收尾过程。 |
| `.scratch/phase4b-2-cross-process-version-lease/SUMMARY.md` | +21 / −0 | 保存跨对话的最终结论和明确边界。 |

**未改动但有意保留**：`pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`、安装器/远程目录实现、公开 SDK 和历史 PR 报告未作为本阶段实现目标；既有无关 dirty 文件没有被重置或覆盖。

## 三、实现要点

1. **锁权威与记录分离**：`leases.lock` 串行化获取、释放、占用检查和删除决策；`leases/<lease-id>.lock` 是每个进程真正持有的 OS 内核锁；`leases/<lease-id>.json` 只承载诊断和交接元数据，固定 schema/大小上限，不能替代锁。
2. **保守判定**：若匹配的 `worker_reservation` 已写入但 parent lock 在交接过程中失去，系统无法证明 child 是否已经接管，则返回 `pending_confirmation` 并保留版本；只有持锁记录明确释放，或在协调临界区内确认可安全清理时，才允许删除诊断残留。
3. **生命周期接入**：Host 在 verified interpreter import 前取得并持有 lease；Settings 在构造 Feature settings 前获取并由对话框 finished 释放；Worker 保持 reservation → child lease 的无窗口交接。`atexit` 只作为正常进程退出时的辅助释放，删除决策仍依赖 OS 锁。
4. **线程边界**：状态 watcher 的 QFileSystemWatcher、QTimer 和读取工作均在独立 Qt worker 线程；GUI 侧只接收 queued signals。Worker bootstrap 保持最小标准库边界，现有 Worker 隔离测试继续禁止 UI/Core/plugin import。
5. **授权绑定**：选择对象必须包含 Feature 身份、版本、安装 revision、digest 和已验证 descriptor；revision、enabled、active、generation 任一不匹配时拒绝旧 action/request/result，不静默回退其他版本。

## 四、性能分析

**方法（可复现）**：PowerShell 中使用临时数据根运行同一组 Python inline benchmark：200 次 `acquire_host(selection).close()`、1000 次 `is_current_selection(selection)`、200 次空闲 `inspect_occupancy(version, revision)`；临时目录位于 `.scratch/phase4b-2-cross-process-version-lease/` 并在进程退出时清理。环境：Windows 10 `10.0.26100`、Python `3.11.1`、真实 NTFS 工作树；非 CI、非 mock。

实测命令形态：

```powershell
$env:PYTHONIOENCODING='utf-8'
PowerShell here-string containing the benchmark body | python -
```

benchmark body 构造已提交的 `FeatureVersionSelection` 于 `TemporaryDirectory` 中，执行上述三组循环，使用 `time.perf_counter_ns()` 记录 min/median/p95/max，并输出一个 JSON 对象；临时目录在退出时删除。

原始输出：

```json
{"free_occupancy_inspect": {"max_us": 12350.7, "median_us": 1191.2, "min_us": 1119.2, "p95_us": 1304.0, "samples": 200}, "lease_acquire_close": {"max_us": 53346.7, "median_us": 31007.25, "min_us": 17656.6, "p95_us": 44302.4, "samples": 200}, "os": "Windows-10-10.0.26100-SP0", "pid": 31296, "python": "3.11.1", "selection_revalidate": {"max_us": 11004.2, "median_us": 3386.85, "min_us": 3072.2, "p95_us": 5698.9, "samples": 1000}}
```

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---:|---|
| Host lease acquire + close，200 样本 | median 31.007ms；p95 44.302ms；max 53.347ms | 新增路径；每次 Host 启动/关闭一次，不是每帧热路径 |
| selection revalidate，1000 样本 | median 3.387ms；p95 5.699ms；max 11.004ms | 新增授权复核；每个新请求和结果展示前按调用面触发 |
| free occupancy inspect，200 样本 | median 1.191ms；p95 1.304ms；max 12.351ms | 新增管理/清理检查；不应放入 GUI 高频循环 |
| 常驻内存 | 本轮未测 RSS；租约 JSON 上限 16 KiB、记录字段受限，无常驻网络缓存 | 新增记录/句柄；需在后续冻结构建前补 RSS/长时 soak |

**结论**：

1. 稳态 Core UI 帧循环没有新增 lease 调用；新增成本集中在 Host/Settings/Worker 启动、状态变更复核和清理决策，实测绝对值如上。
2. 新增了磁盘目录创建、JSON 原子写入/删除、协调锁和每租约 OS 内核锁；没有新增网络请求、远程访问或常驻线程（状态监视线程只在 monitor 生命周期内存在）。
3. Worker handoff 每次 Worker 启动一次；Settings 每个独立 Settings 进程/对话框一次；Host 每个生产 Feature interpreter 生命周期一次；状态 watcher 默认每秒最多一次补偿读取，文件变更由 watcher 先唤醒。
4. 没有用性能形容词替代数字；没有测得可报告的 RSS 增量，因此不把“无内存增长”写成已验证事实。

## 五、实机运行记录

### 5.1 Windows 真实进程与 Qt

以下均在本机 Windows 工作树执行，不是 CI、不是 mock：

- `QT_QPA_PLATFORM=offscreen; python -m pytest -q tests/test_feature_install_state.py tests/test_feature_state_monitor.py tests/test_feature_version_lease.py tests/test_feature_worker_handoff.py tests/test_feature_packages.py tests/test_feature_package_binding.py tests/test_external_worker_launch.py tests/test_screen_worker_boundary.py tests/test_settings_process_isolation.py tests/test_content_dlc_hard_gates.py` → `270 passed, 1 skipped in 26.64s`；其中 lease 测试使用真实 `multiprocessing`，handoff 测试使用真实 `QProcess` 和 Qt event loop。
- 上述受影响测试族连续三次同一命令 → 每次 `270 passed, 1 skipped`；PowerShell wall time 分别 `27972ms`、`27921ms`、`26350ms`（pytest 内部耗时分别 `25.81s`、`25.85s`、`25.36s`）。
- 按项目规定的 `QT_QPA_PLATFORM=offscreen; python -m pytest -q` 全量 → `3581 passed, 12 skipped, 13 warnings in 479.78s`。直接在当前真实桌面 QPA 下运行时，6 个既有拖拽合帧断言因当前物理屏幕边界裁剪失败；这属于桌面分辨率环境差异，未作为本阶段产品回归纳入结论。
- `python -m ruff check pet features tests` → `All checks passed!`。
- `python -m ruff format --check pet features tests` → `439 files already formatted`。
- 受影响实现模块定向 `mypy`（14 个文件）→ `Success: no issues found in 14 source files`；另行执行 `python -m mypy pet tests` 得到仓库既有基线失败：`1796 errors in 199 files`，因此不宣称全仓库 mypy 通过。

### 5.2 失败路径与现场边界

- Host import 失败后，`inspect_occupancy` 仍返回 occupied；只有显式 `release_process_leases()` 后才允许释放，证明“不支持 Python hot-unload”的语义没有被异常路径绕过。
- revision 或 manifest digest 变化后获取租约被拒绝；旧选择复核返回 false，不回退到其他版本。
- parent reservation 在 child 未发送 `lease_claimed` 时不会被确认；Supervisor 让启动失败，父侧 abort reservation，异常交接记录保持保守 pending。
- 父进程退出后真实 OS 锁自动释放；但若处于未确认 reservation，记录仍不能靠 PID/超时自动清除。
- 直接运行 `python -m pet --worker agent-link-events`（无 handoff 环境）现场输出：`..."lease_claimed":false...`，进程退出码 `0`。这是 validation-only/legacy 无 token 路径，不是生产 handoff 成功；生产 `Supervisor` 在 `requires_handoff` 时拒绝该 hello，故未将这条探针误报为租约通过。

### 5.3 用户可见行为与无法自动验证项

本阶段没有新增界面、安装按钮、托盘菜单或资源选择器，因此不会声称完成桌面人工验收。4B-1.5 的资源包人工安装、重启后播放、可见桌面/托盘操作和公开稳定 API 仍 pending；原因是这些是本阶段外的人工/公开门，不是用自动化静默替代。当前可确认的用户可见效果是：后续版本清理会在 Core、Settings、Worker 占用或状态不确定时拒绝删除，版本变化后旧请求/旧结果不再生效；不会新增 UI。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| Ruff lint | `python -m ruff check pet features tests` | 通过；`All checks passed!` |
| Ruff format | `python -m ruff format --check pet features tests` | 通过；`439 files already formatted` |
| Mypy（受影响实现边界） | 定向检查 14 个 Phase 4B-2 实现模块 | 通过；`Success: no issues found in 14 source files` |
| 聚焦 | 10 个受影响模块，`QT_QPA_PLATFORM=offscreen; python -m pytest -q ...` | `270 passed, 1 skipped in 26.64s` |
| 受影响时序族满载 3 遍 | 同一族命令连续 3 次 | 每次 `270 passed, 1 skipped`；PowerShell 27.972s / 27.921s / 26.350s |
| 全量 | `QT_QPA_PLATFORM=offscreen; python -m pytest -q` | `3581 passed, 12 skipped, 13 warnings in 479.78s`；真实桌面 QPA 另有 6 个屏幕边界相关既有失败，见 5.1 |
| 文档链接 | `python scripts/check_docs.py` | 通过；`Markdown link check passed: 123 files scanned` |
| PR 报告纪律 | `python -m pytest -q tests/test_pr_report_discipline.py` | 通过；`49 passed in 0.53s` |
| 空白检查 | `git diff --check` | 退出码 0；仅有既有 LF/CRLF 转换提示，无 whitespace error |

## 七、已知限制与后续

- 4B-1.5 人工资源包安装、重启播放、桌面/托盘以及公开稳定 API 仍 pending；不得用 4B-2 自动化结果替代。
- 当前源码没有自动发现/选择已安装 Feature 的 Settings 生产入口，也没有完整的 Feature QAction/结果展示调用面；本阶段只提供可复用的内部 selection revalidation seam。
- 当前官方实现仍以仓库现有单一 `FEATURE_ID` 作为 coordinator 的身份校验边界；扩展到多个官方 Feature 前，需要把 Feature registry/selection contract 一并参数化，不能仅复制租约目录。
- 没有新增安装器 UI、远程目录、公开 SDK、任意第三方 Python 入口或 hot-unload；4B-3 本地安装/升级/卸载事务、4B-4 管理 UI、4B-5 真实冻结构建仍未开始。
- Windows/POSIX 的锁行为已覆盖当前实现路径，但尚未完成跨平台真实机器矩阵、RSS 长时 soak 或断电级持久性证明。
- 本报告对应实现提交 `5a1b6e0`，已推送至 `origin/codex/phase3-worker`；本地 `rev-list` 与 `ls-remote` 已核验一致。未做 CI 或远程工作树二次验证；当前 tracked numstat 仍不是纯阶段 patch 统计。

## 八、风险与回滚

风险停止条件仍有效：若未来发现 state revision 与 lease 获取存在竞态、Windows 锁/重命名语义无法证明、PID reuse/残留记录可能误删、Worker 隔离边界出现 UI/Core/plugin import，或交接只能依赖超时猜测，则停止 4B-3，保留旧版本，返回 `occupied`/`pending_confirmation`，不执行破坏性清理。

本轮无 schema migration、无公开配置键、无远程数据；回滚应按文件主题回滚 Phase 4B-2 代码并保留用户现有 dirty 修改，不使用 `reset --hard`。若已有进程仍持有 lease，先让其按正常生命周期释放或由操作系统关闭句柄；不要手工删除租约目录证明“清理成功”。

## 完成后的实际使用效果

用户界面没有新增安装或管理按钮。版本被 Core、Settings 或 Worker 使用，或系统无法证明它已释放时，后续删除/替换路径会保持版本；旧 revision、digest、enabled/active 或 generation 的请求和结果会被拒绝或丢弃。Worker 父子交接在 child lease 被确认前不会释放 parent reservation，避免出现父租约已释放而子租约尚未接管的窗口。

## 九、Phase 4B-1.5 测试副本补充证据（2026-10-03）

- 在仓库有效资源基础上制作隔离测试副本 `package-shenshen-1.0.1`，使用独立 `APPDATA`，未读取真实 Key、未访问远程目录。
- `python -m pet.content validate`、安装、列表、Registry/catalog 解析和 `MovieLibrary` offscreen 播放探针均通过；manifest digest 与实际包摘要一致，idle WebM 可读。
- 首次真实 Windows GUI 启动后，用户确认视觉通过并正常退出；这是用户确认的测试副本播放证据。
- 随后重启实例已启动并完成终端侧安装版本/Registry/idle 资源探针；该次重启的独立视觉确认未在本记录中单独收到，因此 4B-1.5 的“重启播放、桌面/托盘和公开稳定 API”仍保持 pending，不把终端探针扩大解释为公开完成。
