# Phase 3A Worker 稳定性封存补充报告

> **日期**：2026-09-25
> **基线提交**：`e2f269f feat(workers): 封存 Phase 3A Agent Link Worker`
> **范围**：Qt 测试进程隔离、真实 Core 优雅退出、冻结 Worker smoke、Windows 可见桌面复验
> **阶段结论**：Phase 3A 的 Agent Link Worker 稳定性门已收口；Phase 3B 主动识屏 Worker 尚未开始。

## 一、最终结论与边界

此前组合测试在同一个 pytest 进程中出现 Windows 原生崩溃 `0xC0000409`。复核后，问题稳定指向测试基础设施：Worker 生命周期测试创建了 `QCoreApplication`，随后 Agent Link GUI 测试创建 `QApplication`，两种 Qt 应用对象在同一进程中共存会触发原生层冲突。

本轮将生命周期 fixture 统一为 `QApplication`，保留真实 `QProcess` 边界，不改变 Worker 生产代码。组合测试连续三轮通过，未再出现 `0xC0000409`。

本轮还补齐了两类此前缺少的证据：

- 真实 `python -m pet` Core 进程通过测试专用 `sitecustomize` 自动退出，实际执行 `QApplication.aboutToQuit` 和 Worker graceful shutdown；
- 新构建的 PyInstaller onedir 可执行文件通过 `--worker agent-link-events` 的 hello/config_push/ready/shutdown 协议 smoke。

Phase 3A 的边界仍然是：Worker 只负责 Agent Link 日志 tail、解析和语义事件规范化；`AgentLinkManager` 的展示、动作、对话、成本、设置兼容入口和 DSH bridge 仍由 Core 管理。Phase 3B 主动识屏不会因为本报告自动开始。

## 二、修改文件说明

本轮只修改测试、验证脚本和本报告索引，不修改运行时 Worker 协议、Core CLI、自动更新或业务功能。

| 文件 | 增删/规模 | 改动意图 |
|---|---:|---|
| `tests/test_workers_lifecycle.py` | `+6 / -2` | 将 Worker 生命周期 fixture 从 `QCoreApplication` 统一为 `QApplication`，避免与 Agent Link GUI 测试在同一 pytest 进程中发生 Qt 应用对象冲突。保留真实 `QProcess` 测试边界。 |
| `tests/test_phase3a_app_shutdown.py` | `+202 / -0` | 新增真实 Core/Worker 优雅退出探针；不增加正式 CLI 参数，使用测试专用 `sitecustomize` 触发退出并记录 Worker PID、退出码和清理结果。 |
| `scripts/verify_phase3a_frozen_worker.py` | `+134 / -0` | 新增可复用冻结 Worker smoke 脚本；只启动既有冻结程序的 `--worker agent-link-events` 入口，不构建、不修改 PyInstaller 配置。 |
| `docs/plugin-phase-03-worker/PHASE3A-STABILITY-CLOSEOUT.md` | 新增 | 记录 S0–S8/R0 稳定性门、实机证据、限制和 Phase 3B 进入条件。 |
| `docs/INDEX.md` | `+1 / -0` | 登记本封存补充报告，保持 Phase 3A 报告链可追溯。 |

提交前使用 `git diff --numstat` 复核上述统计；新增文件以工作树实际行数为准。

## 三、稳定性门状态

| 门 | 内容 | 本轮结果 | 状态 |
|---|---|---|---|
| S0 | 范围、分支、回滚基线 | 未扩大 Phase 3A 范围，未触碰自动更新文件 | 通过 |
| S1 | JSONL 协议、QProcess、握手、心跳、关闭 | 既有协议和生命周期测试保持通过 | 通过 |
| S2 | PyInstaller 冻结 Worker smoke | 新构建 onedir 可执行文件连续 2 次完成 hello/config_push/ready/shutdown，退出码均为 0 | 通过 |
| S3 | 父进程退出、Worker 清理、自然退出 | 既有 EOF/异常父进程测试通过；新增真实 Core graceful shutdown 退出码为 0，Worker 正常退出 | 通过 |
| S4 | 队列上限、背压、溢出诊断 | 既有测试保持通过 | 通过 |
| S5 | 事件顺序、去重、generation、fallback | 组合回归保持通过，未发现旧 generation 事件或重复消费回归 | 通过 |
| S6 | 有界性能基线 | 保留既有 200 事件与 30 秒/512 事件基线；本轮不增加长期 soak test | 通过 |
| S7 | Windows 可见桌面和用户退出 | 真实窗口、桌宠、托盘菜单可见；通过托盘“退出”自然退出，Core 和 Worker 均消失 | 通过 |
| S8 | Phase 3A 提交和 PR 证据 | 原有实现报告加本补充报告；修改范围可独立回滚 | 通过 |
| R0 | 组合测试稳定性 | 7 个 Worker/Agent Link 测试文件连续 3 次均为 `274 passed, 1 skipped`，无 `0xC0000409` | 通过 |

### 3.1 R0 崩溃原因和修复

触发命令为 Worker 协议、生命周期、source、集成和 Agent Link 测试的同进程组合。单独运行各族时没有崩溃，组合运行时原生退出码为 `-1073740791`（`0xC0000409`）。

将 `tests/test_workers_lifecycle.py` 的 fixture 从 `QCoreApplication` 改为 `QApplication` 后，组合测试连续三轮通过。这个修复只调整测试进程中的 Qt 应用对象类型，没有改变 Worker、Agent Link 或 Core 的运行逻辑。

## 四、性能分析

本轮没有增加更长时间的 Worker soak test。以下既有性能数据来自 2026-09-25 Windows 本机真实进程，作为 Phase 3A 的有界基线：

### 4.1 200 事件样本

| 指标 | 实测 |
|---|---:|
| hello | 81.43 ms |
| ready | 82.24 ms |
| 事件延迟 min / p50 / p95 / max | 67.50 / 99.11 / 126.46 / 129.44 ms |
| Worker RSS 最大值 | 16.035 MB |
| CPU 采样最大值 | 0.0%（低负载样本，不外推为长期 CPU 为零） |
| 优雅关闭 | 7.33 ms |
| 重启后第二次 hello / ready | 88.50 / 105.85 ms |
| stderr | 0 bytes |

### 4.2 30 秒、512 事件样本

- 持续时间：30.0 秒；发送 512 条，接收 512 条，唯一且顺序正确；
- 延迟：min 29.02 ms / p50 108.56 ms / p95 249.60 ms / max 257.35 ms；
- RSS：15.918–15.926 MB，样本增量约 0.008 MB；
- CPU：198 个样本，最大 11.2%，p95 为 0.0%；
- 优雅关闭：7.21 ms；stderr：0 bytes。

### 4.3 本轮新增路径成本

- 真实 Core graceful shutdown 探针从启动到退出约 10.61 秒，其中包含桌面应用初始化、Worker 握手和测试预算；该数字是测试耗时，不是产品退出耗时上界；
- 可见 Windows 桌面探针记录启动约 3.031 秒，窗口可见后 Worker 子进程存在；
- Worker 仍只使用本地 stdin/stdout JSONL、文件 tail 和 Qt-free 解析，本阶段没有新增网络请求、API Key、token 或 secret 读取；
- 同一可执行文件 + `QProcess` 解决的是故障隔离，不自动减少 PyInstaller 包体。

## 五、实机运行记录

### 5.1 组合回归三轮

环境：Windows，`QT_QPA_PLATFORM=offscreen`。

命令族：

```text
python -m pytest -q tests/test_workers_protocol.py tests/test_workers_lifecycle.py tests/test_workers_source.py tests/test_agent_link_worker_integration.py tests/test_agent_link.py tests/test_agent_link_threads.py tests/test_agent_link_dep_specs.py
```

结果：连续 3 次均为：

```text
274 passed, 1 skipped
```

没有出现 `0xC0000409`，也没有新增 Python 异常。

### 5.2 真实 Core 优雅退出

探针：

```text
python -m pytest -q tests/test_phase3a_app_shutdown.py -vv
```

结果：

```text
1 passed in 10.61s
CORE_EXITED=0
```

真实 marker：

```json
{"event": "started", "worker_id": "agent-link-events", "pid": 8992}
{"event": "finished", "worker_id": "agent-link-events", "exit_code": 0, "exit_status": "ExitStatus.NormalExit"}
```

探针确认 Core 退出码为 0，Worker 退出码为 0，两个 PID 在退出后都不再运行；没有使用 `taskkill /T /F` 代替优雅退出。

### 5.3 新构建冻结程序 Worker smoke

首先用既有 onedir 构建流程生成当前冻结程序，然后运行：

```text
python scripts/verify_phase3a_frozen_worker.py dist-onedir/dsh-pet-standalone-webm/dsh-pet-standalone-webm.exe
```

连续两次均得到：

```text
hello worker_id=agent-link-events payload={'pid': ..., 'capabilities': ['agent_events.read', 'settings.read', 'logging.write']}
ready generation=1 source_count=0
shutdown_sent
FROZEN_WORKER_SMOKE_OK returncode=0
```

确认冻结 Worker 没有创建 `QApplication`、没有进入桌宠 UI，并完成了 hello/config_push/ready/shutdown。

另外，构建脚本随后进行的全量 Qt DLL chain smoke 在当前环境报告 `QtCore ImportError: DLL load failed`。这属于现有 PyInstaller/Qt 依赖链检查的环境或打包基线问题；独立的 Worker 入口已在同一新构建产物上通过两次。此问题未通过修改 spec 或引入额外依赖掩盖，后续应由打包依赖审计单独处理。

### 5.4 Windows 可见桌面和托盘退出

环境：真实 Windows 桌面 `1920x1080`，非 offscreen；数据目录隔离到 `.scratch/phase3a-stability/visible-data`，未修改用户正式配置。

实测过程：

1. 启动真实 `python -m pet --slot 0`；
2. 窗口标题 `dsh-pet-standalone` 可见，桌宠角色在屏幕右下方可见；
3. Worker 子进程 PID `8992` 存在；
4. 打开系统托盘溢出区并右键桌宠图标；
5. 看到真实菜单，包括“显示 / 隐藏”“桌宠设置”“切换角色”“退出”等；
6. 点击“退出”；
7. Core 返回 `0`，Worker marker 记录 `ExitStatus.NormalExit`、exit code `0`；
8. Core PID `21672` 和 Worker PID `8992` 均不再运行。

因此本轮不再把强制结束当作自然退出证据。`WM_CLOSE` 仍然是隐藏窗口行为，不把关闭窗口误认为退出应用。

## 六、验证命令和结果

| 检查 | 结果 |
|---|---|
| `python -m pytest -q tests/test_phase3a_app_shutdown.py -vv` | `1 passed in 10.61s` |
| Phase 3A 7 文件组合回归 × 3 | 每次 `274 passed, 1 skipped` |
| `python scripts/verify_phase3a_frozen_worker.py <fresh onedir exe>` × 2 | 每次 `FROZEN_WORKER_SMOKE_OK returncode=0` |
| `python -m pytest -q tests/test_pr_report_discipline.py` | `33 passed in 0.84s` |
| `python -m pytest -q` | `1 failed, 3029 passed, 11 skipped, 14 warnings`；唯一失败是既有文档证据与产品文案扫描规则冲突，见下方说明 |
| `python -m ruff check pet tests scripts` | `All checks passed!` |
| `python -m ruff format --check pet tests scripts` | `360 files already formatted` |
| `python -m mypy pet/workers pet/agent_link.py` | `Success: no issues found in 8 source files` |
| `python scripts/check_docs.py` | `Markdown link check passed: 98 files scanned` |

全量测试的唯一失败为 `tests/test_desktop_pet_features.py::test_product_copy_has_no_external_brand_reference`。该测试会扫描 `docs/` 下的证据报告，但现有 `docs/PR-REPORT-PLUGIN-PHASE3-2026-09-25.md` 已包含分支标识，因而被产品文案规则命中；本轮新增的封存报告也曾包含同类元数据，已移除该非必要标识。该失败不涉及 `pet/`、Worker 协议、Core 生命周期或用户可见功能，属于既有测试规则与证据文档边界不一致的问题；本轮不擅自修改历史报告或测试规则，后续应单独处理。

## 七、当前限制和 Phase 3B 进入条件

Phase 3A 已满足进入 Phase 3B 的稳定性前置条件，但 Phase 3B 仍应单独规划、实现和验收。进入前必须保留以下边界：

- Phase 3B 只迁移主动识屏的高风险执行部分：前台窗口、截图、dHash、视觉请求和响应解析；
- Core 继续掌握开关、白名单、dwell、limiter、用户确认、记忆和展示策略；
- 主动识屏 Worker 初期作为官方内置能力，不是可单独下载的 Worker DLC；
- 不开放未知 Python `entrypoint`，不把 Worker 直接变成第三方代码执行入口；
- 不把 `pet-worker/v1` 和 `agent-event/v1` 混成一个业务协议；
- 不因为 Phase 3A 稳定就自动迁移 Chat、歌词、余额或其他外部服务；
- PyInstaller Qt DLL chain smoke 仍需在打包依赖审计中单独解释和收口；
- 本轮明确不增加更长时间 soak test。

## 八、回滚方式

Phase 3A 仍可通过以下方式回滚，不需要重写历史：

1. 将 Agent Link 运行模式切换为 `in_process`；
2. 停止启动 Worker，保留原有 event source；
3. 单独回滚本轮测试提交和验证脚本；
4. 不删除用户配置，不修改 `pet/updater.py`、`pet/update_settings.py`；
5. 保留 Phase 3A Worker 代码作为可选路径，直到后续发布门完成。

## 九、面向用户的最终效果

### 正常使用时

桌宠窗口、托盘、角色动画、气泡、Agent Link 展示和设置入口不增加新的操作窗口。Worker 在后台作为独立操作系统进程运行，用户通常看不到它。

### Core 和 Worker 如何连接

```text
Core 主进程
    │ QProcess
    │ stdin/stdout：pet-worker/v1 JSONL
    ▼
Agent Link Worker 子进程
```

Core 决定是否启动、推送哪些配置、接收哪些事件和如何降级；Worker 只读取日志、解析记录、生成 `agent-event/v1` 语义事件并发送心跳。Worker 崩溃时 Core 可以有限重启，重启耗尽后切回 `in_process`，桌宠基础功能继续运行。

### 能否完全关闭

可以。关闭 Agent Link Worker 后，Core 仍可启动和进行基础桌宠交互；Agent Link 会按配置停用或切回 `in_process`。本阶段的 Worker 不是 DLC，随官方 Core 一起发布，也不需要用户手动安装或卸载。

### 当前文件和安装位置

Phase 3A 的 Worker 是 Core 内置入口，源码位于：

```text
pet/workers/
  protocol.py
  supervisor.py
  worker_entry.py
  agent_link_source.py
  agent_link_worker.py
```

冻结发布时它随 Core 的 PyInstaller onedir 一起发布。未来如果 Phase 6/7 真的开放 Worker DLC，才会在用户数据目录中采用独立版本目录、`active.json`/`previous.json`、staging、签名和回滚；本轮不实现该安装/卸载流程。
