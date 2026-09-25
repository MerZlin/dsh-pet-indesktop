# Phase 3A Worker 插件运行时实施报告

> **基线**：`01d003b`（`docs(plugin): 修订 v5 插件化路线与阶段计划`）
> **分支**：`codex/phase3-worker`
> **日期**：`2026-09-25`
> **范围**：Worker 协议、QProcess 宿主、Agent Link 事件采集 Worker、Core 适配、背压、冻结程序和真实进程验收
> **阶段**：Phase 3A 稳定性封存；主动识屏 Worker（Phase 3B）未开始
> **提交策略**：本报告与 Phase 3A 代码/测试单独提交；不包含现有文档整理、自动更新文件或未跟踪演示 HTML。

## 一、结论与边界

Phase 3A 已完成稳定性封存门禁，可以作为后续 Phase 3B 的可回滚基线。Agent Link 的日志 tail、文件偏移、原始记录解析和语义事件规范化已经可以由独立 Worker 承担；Core 仍保留 `AgentLinkManager` 的公共入口、展示策略、气泡/动作/对话、成本、设置兼容入口和 DSH bridge 管理。

默认运行模式为 `auto`：Core 优先启动 Worker；握手、心跳或运行失败时执行有限重启，超过重启预算后切换到旧的 `in_process` event source。`in_process` 模式仍保留为回归测试和紧急回滚路径。

本阶段明确没有迁移：完整 Agent Link UI、主动识屏、Chat UI、AI 服务、DSH bridge 安装/卸载、远程 DLC、Steam Workshop 或未知 Python `entrypoint`。`pet-worker/v1` 只负责 Worker 生命周期控制，`agent-event/v1` 继续负责 Agent 业务事件语义，二者没有把 DSH bridge 的传输细节提升为通用协议。

## 二、修改文件说明

### 2.1 实现文件

新增文件的增删统计按提交前文件行数记录；已跟踪文件使用 `git diff --numstat` 复核。提交前应再次执行该命令，提交后的权威记录以 `git show --numstat --oneline HEAD` 为准。

| 文件 | 增删/规模 | 改动意图 |
|---|---:|---|
| `pet/workers/__init__.py` | +27 / −0 | 导出 Worker 协议、宿主和 Agent Link Worker API。 |
| `pet/workers/protocol.py` | +170 / −0 | 定义 `pet-worker/v1`、消息模型、编码/解码、字段类型和单行大小校验。 |
| `pet/workers/supervisor.py` | +473 / −0 | 使用异步 `QProcess` 管理握手、心跳、重启退避、优雅关闭、进程释放和 fault 状态。 |
| `pet/workers/worker_entry.py` | +26 / −0 | 建立官方 Worker 显式 allowlist；未知 Worker 不执行任意 Python 入口。 |
| `pet/workers/agent_link_source.py` | +416 / −0 | 抽取 Qt-free 文件 tail、目录 glob、轮转/截断、pending backlog、记录解析和语义规范化。 |
| `pet/workers/agent_link_worker.py` | +183 / −0 | 实现 Agent Link Worker 的 stdin/stdout JSONL 主循环、配置、prime、事件、heartbeat 和 EOF 退出。 |
| `pet/workers/agent_link_adapter.py` | +292 / −0 | 将 Worker 消息适配为既有 Agent Link 信号，过滤 stale generation，并提供 bounded outbox/backpressure 诊断。 |
| `pet/__main__.py` | +21 / −0 | 在导入 `pet.app` 前增加 `--worker` 分流，同时保留普通、设置和清理入口。 |
| `packaging/pet_entry.py` | +7 / −2 | 冻结主程序入口支持同一可执行文件的 `--worker` 参数。 |
| `packaging/pet_entry_no_chat.py` | +6 / −0 | 无 Chat 构建入口支持 `--worker` 参数。 |
| `pet/agent_link.py` | +148 / −14 | 保留原有 facade，接入 `worker`/`in_process`/`auto` 模式、fallback、generation 和退出顺序。 |

### 2.2 测试文件

| 文件 | 增删/规模 | 覆盖行为 |
|---|---:|---|
| `tests/conftest.py` | +27 / −0 | Worker 测试 teardown、Qt 退出事件隔离和阶段 marker。 |
| `tests/test_workers_protocol.py` | +137 / −0 | JSONL 合法/非法消息、字段类型、大小、payload、协议、request ID 和 generation。 |
| `tests/test_workers_lifecycle.py` | +358 / −0 | 真实 QProcess 握手、配置、事件、heartbeat、EOF、关闭、崩溃重启、fault、stale generation、父进程退出和 UI 导入隔离。 |
| `tests/test_workers_source.py` | +85 / −0 | pending backlog、poll 上限、prime、轮转/截断和稀疏背压诊断。 |
| `tests/test_agent_link_worker_integration.py` | +239 / −0 | Worker/Core 事件转发、auto fallback、显式 in-process 回滚、outbox 上限、关键事件保护和跨批次顺序。 |

### 2.3 有意未纳入提交的文件

以下现有工作区改动保持原样，不属于本次提交：

- `README.md`；
- `docs/` 下已有的删除、归档、索引和主动识屏文档改动；
- `docs/PROACTIVE-SCREEN-DESIGN.md`、`docs/RECORDS-INDEX.md` 及 `docs/archive/`；
- `plugin-roadmap-demo.html`；
- `pet/updater.py`、`pet/update_settings.py`。

## 三、实现与稳定性收口

### 3.1 协议和生命周期

- 所有消息使用单行 UTF-8 JSON，协议版本为 `pet-worker/v1`。
- 校验 `protocol`、`worker_id`、`type`、`timestamp`、`request_id` 和 `payload`，拒绝未知消息类型、非法 JSON、非对象 payload 和超大单行消息。
- Core GUI 线程只使用 `QProcess` 异步信号，不执行阻塞式 `wait()`。
- Worker 支持 `hello`、`ready`、`config_push`、`event`、`error`、`heartbeat`、`shutdown`。
- 结束的 `QProcess` 引用会被释放；旧 generation 的迟到事件会被丢弃。

### 3.2 父子进程和冻结程序

- `python -m pet --worker agent-link-events` 在导入 `pet.app`、`PetWindow`、Chat UI 和 `QApplication` 前分流。
- Windows onedir 冻结产物能用同一个 executable 完成 Worker hello、config_push、ready、shutdown。
- 父进程异常退出时，Worker 通过 stdin EOF 自行结束；Core 关闭时保留 terminate/kill 超时链路。

### 3.3 背压和事件一致性

- Core 暂停 outbox 固定容量为 256；activity 可按优先级淘汰；无法安全淘汰关键 state/event pair 时丢弃并产生结构化诊断。
- Worker source 对单次文件读取、glob 扫描和 poll 设置上限；未处理记录保留在 pending backlog，不静默丢事件。
- `prime()` 在 Worker ready 前完成初始历史回填，避免配置推送与第一次 poll 之间的启动竞态。
- 实际进程测试验证 512 条顺序事件全部到达、无重复、顺序一致；Worker 崩溃后的 legacy fallback 仍能接收后续事件。

## 四、性能分析

以下数据来自 2026-09-25 Windows 本机真实进程，不是 mock，也不是 CI。测试工具和产物均位于 ignored 的 `.scratch/phase3a-stability/`，不进入提交。

### 4.1 单次 200 事件样本

命令：`python .scratch/phase3a-stability/measure_worker.py`

| 指标 | 实测 |
|---|---:|
| hello | 81.43 ms |
| ready | 82.24 ms |
| 事件延迟 min / p50 / p95 / max | 67.50 / 99.11 / 126.46 / 129.44 ms |
| Worker RSS 最大值 | 16.035 MB |
| CPU 采样最大值 | 0.0%（本次低负载样本；不外推为长期 CPU 为零） |
| 优雅关闭 | 7.33 ms |
| 重启后第二次 hello / ready | 88.50 / 105.85 ms |
| stderr | 0 bytes |

### 4.2 30 秒长时样本

命令：`python .scratch/phase3a-stability/long_run_worker.py`

- 持续时间：30.0 秒；发送 512 条事件；接收 512 条，唯一且顺序正确。
- 事件延迟：min 29.02 ms / p50 108.56 ms / p95 249.60 ms / max 257.35 ms。
- RSS：15.918–15.926 MB，样本增量约 0.008 MB。
- CPU：198 个样本，最大 11.2%，p95 为 0.0%。
- 优雅关闭：7.21 ms；stderr：0 bytes。

这些数字证明本阶段路径在当前 Windows 环境下没有出现明显内存增长、事件重复或阻塞式退出；它们不是所有机器和无限时长运行的上界。进入发布前仍应按实际目标补充更长的 soak test。

### 4.3 系统调用、网络和线程变化

- Worker 只使用本地 stdin/stdout JSONL、文件 tail 和 Qt-free 解析；本阶段没有新增网络请求、API Key、token 或 secret 读取。
- 主进程新增一个可选 Worker 子进程和对应 `QProcess` 管理；Agent Link Worker 未启动时仍可使用 `in_process` 路径。
- 本阶段没有承诺 PyInstaller 包体下降；同一 executable + `QProcess` 解决的是故障隔离，不自动减少冻结包体。

## 五、实机运行记录

### 5.1 冻结程序 Worker smoke

环境：Windows，Python 3.11.1，PyInstaller 6.20.0。

命令：使用 `dsh-pet-standalone-webm` onedir 冻结产物启动 `--worker agent-link-events`。

关键输出：

```text
hello payload capabilities agent_events.read/settings.read/logging.write pid=15024
ready generation=1 source_count=0
FROZEN_WORKER_SMOKE_OK returncode=0
```

确认冻结程序没有进入桌宠 UI，能完成 hello/config_push/ready/shutdown，退出码为 0。

### 5.2 父进程异常退出

命令：`python -m pytest -q tests/test_workers_lifecycle.py -k "parent_process_dies or stdin_eof or handshake_event"`

结果：`3 passed, 6 deselected`。Windows `tasklist` 轮询在 10 秒预算内确认 Worker PID 消失。

### 5.3 可见 Windows 桌面

使用真实 `python -m pet`、非 `QT_QPA_PLATFORM=offscreen`，隔离 `APPDATA`/`LOCALAPPDATA` 到 `.scratch/phase3a-stability/desktop-run-20260925-154252`。

```json
{
  "python": "3.11.1",
  "pid": 27668,
  "startup_seconds": 3.031,
  "visible_windows": [[1704794, "dsh-pet-standalone"]],
  "alive_with_window": true,
  "worker_child_pids_before_kill": [15048, 27960, 28668],
  "forced_shutdown": true,
  "taskkill_returncode": 0,
  "process_returncode": 1,
  "process_tree_gone": true
}
```

确认真实桌面窗口可见，主进程在窗口存在时保持运行，并能启动 Worker 子进程。现有应用设置 `setQuitOnLastWindowClosed=False`，WM_CLOSE 会隐藏窗口而不是退出；因此本次使用 `taskkill /PID /T /F` 验证进程树清理。`process_returncode=1` 是强制终止结果，不是自然退出失败；本次不宣称自然 WM_CLOSE 退出已经验收。

### 5.4 未知 Worker 边界

```text
python -m pet --worker unknown-worker
unknown worker: unknown-worker
exit=2
```

未知 Worker 会被拒绝，不执行任意 Python 入口。

## 六、测试与验证

| 门 | 命令 | 实际结果 |
|---|---|---|
| Worker 协议/生命周期/source/集成 | `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_workers_protocol.py tests/test_workers_lifecycle.py tests/test_workers_source.py tests/test_agent_link_worker_integration.py` | `30 passed in 4.32s` |
| Agent Link 回归 | `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_agent_link.py tests/test_agent_link_threads.py tests/test_agent_link_dep_specs.py` | `244 passed, 1 skipped in 5.98s` |
| 单实例/进程回归 | `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_single_process_spawn.py tests/test_single_process_shared.py` | `56 passed in 7.28s` |
| PR 报告纪律 | `python -m pytest -q tests/test_pr_report_discipline.py` | `33 passed in 0.46s` |
| 全量回归 | `QT_QPA_PLATFORM=offscreen python -m pytest -q` | `3029 passed, 11 skipped, 13 warnings in 394.92s` |
| Ruff lint | `python -m ruff check pet tests` | `All checks passed!` |
| Ruff format | `python -m ruff format --check pet tests` | `337 files already formatted` |
| Python 编译 | `python -m py_compile` 11 个 Phase 3 入口/Worker 文件 | 通过 |
| Diff 空白 | `git diff --check` | 通过；仅提示用户已有 `docs/INDEX.md` 行尾转换 |

附加类型检查：`python -m mypy pet/workers pet/agent_link.py` 中 `pet/workers` 无错误；`pet/agent_link.py` 仍有 4 个既有类型错误（tailer 类型、`start()` 返回类型、模型访问提示参数类型和 `targets` 推断），本阶段不扩大范围修复，作为工程规范化阶段的已知基线。

## 七、已知限制与后续

- Phase 3A 只迁移 Agent Link 事件采集，完整 `AgentLinkManager` 仍在 Core。
- Phase 3B 主动识屏 Worker 尚未开始；截图、dHash、视觉请求和网络隔离保持原实现。
- 本次可见桌面验收验证了窗口显示、Worker 子进程和强制进程树清理；自然 WM_CLOSE 退出与用户交互仍需单独人工验收。
- 当前性能基线为 30 秒 soak 和 512 事件样本；后续可按发布目标增加更长时间、事件洪峰和多次重启统计。
- Phase 3A 不解决 PyInstaller 包体瘦身；包体变化需要独立的 import/dependency audit。
- 用户已有文档整理改动未被本提交重新组织；本报告只记录 Phase 3A 证据。

## 八、风险与回滚

运行时回滚优先使用配置：将 `agent_link.worker_mode` 切回 `in_process`，停止 Worker 启动并继续使用旧 event source。若需要代码回滚，使用本 Phase 3A 独立提交执行 `git revert <commit>`，不使用 `reset` 覆盖其他工作区改动。

回滚不会删除用户配置，不会修改 Core 自动更新文件，也不会移除 Phase 1/Phase 2 已有的资源 DLC 和进程内插件边界。Phase 3B 只有在本报告中的 Phase 3A 门禁稳定后才允许进入。
