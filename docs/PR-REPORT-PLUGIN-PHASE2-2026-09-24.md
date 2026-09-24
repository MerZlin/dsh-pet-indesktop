# Phase 2 Core 插件运行时实施报告

> **基线**：`a1c3b6b`（Phase 2 Core 插件运行时规划文档）
> **分支**：`plugin-dlc-v5`　**日期**：`2026-09-24`
> **范围**：18 个实现、测试和阶段文档文件，另新增本交付报告
> **关联**：Phase 1 资源型 DLC 基础闭环提交 `2b8640c`；Core 自动更新基线 `45619ce`

## 一、核心特性

本轮完成 Phase 2 的第一条可运行闭环：在 Phase 1 资源 DLC Registry 之上新增受限的 Core 插件运行时，并将节日提醒迁移为官方 `in_process` 插件 `official.festival-reminder`。Core 通过 `PluginContext` 向插件提供配置、事件、展示、调度、命令、内容和结构化日志端口；插件不再需要访问 `AppShell`、`PetWindow` 私有字段或全局 `Config.data`。

### 已落地能力

| # | 能力 | 说明 |
|---|---|---|
| 1 | `PluginRegistry` | 发现、显式注册、依赖排序、启停、故障隔离和诊断；不执行未知来源的 Python `entrypoint`。 |
| 2 | `PluginContext` | 为每个插件创建隔离配置、事件、展示、调度、命令、内容、capability 和日志端口。 |
| 3 | `CoreEventBus` | GUI 线程同步事件总线；校验 JSON payload，捕获回调异常并取消故障订阅。 |
| 4 | 配置命名空间 | 新增 `config.data["plugins"][plugin_id]`；保留旧扁平节日字段并支持渐进迁移和 `legacy` 保存。 |
| 5 | 官方节日提醒插件 | 复用现有节日日期逻辑，使用插件 scheduler、presentation、speech 和 command 端口，停用时清理 timer、订阅和命令。 |
| 6 | AppShell 生命周期接入 | UI 原语可用后启动插件，退出时先发布 shutdown 事件，再逆序停止插件，兼容旧 facade。 |

**红线 / 不变量**：本轮未修改 `pet/updater.py`、`pet/update_settings.py` 和 Core 自动更新协议；未引入 worker、网络请求、远程 DLC 下载、Steam Workshop 或任意第三方 Python 代码执行；Phase 1 的 Starter DLC 和 `assets/characters` fallback 保持可用；未跟踪的 `plugin-roadmap-demo.html` 未加入本轮改动。

## 二、修改文件说明

以下增删统计来自实现完成后的 `git diff --numstat`；新增文件的删除数为 0。交付报告本身不计入下面的功能文件统计。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/plugins/__init__.py` | +57 / −0 | 导出 Phase 2 runtime 公共类型和生命周期接口。 |
| `pet/plugins/builtin/__init__.py` | +5 / −0 | 建立官方内置插件包边界。 |
| `pet/plugins/builtin/festival_reminder/__init__.py` | +90 / −0 | 注册 `official.festival-reminder` manifest、factory、capability 和插件默认配置。 |
| `pet/plugins/capabilities.py` | +33 / −0 | 定义 capability 集合和调用授权检查，阻止未声明能力被使用。 |
| `pet/plugins/config.py` | +246 / −0 | 提供插件配置命名空间、旧扁平字段读取、迁移、`legacy` 保留和保存适配。 |
| `pet/plugins/events.py` | +153 / −0 | 实现同步 `CoreEventBus`、订阅生命周期、JSON payload 校验和回调故障处理。 |
| `pet/plugins/manifest.py` | +128 / −0 | 解析并校验 Phase 2 `in_process` manifest、Core/API 兼容范围、依赖和 capability。 |
| `pet/plugins/ports.py` | +366 / −0 | 提供展示、调度、命令、内容和结构化日志端口；端口内部检查 capability。 |
| `pet/plugins/runtime.py` | +526 / −0 | 实现 `PluginRecord`、`PluginRegistry`、依赖拓扑排序、启停、故障隔离和诊断。 |
| `pet/festival_service.py` | +80 / −45 | 将节日服务改造成可由 `PluginContext` 注入端口的实现，同时保留旧宿主兼容路径。 |
| `pet/app.py` | +203 / −6 | 在 `AppShell` 中创建和管理 runtime，接入 content provider、插件启动/停止、兼容 facade 和退出清理。 |
| `pet/config.py` | +10 / −0 | 增加 `plugins` 根配置并在重载时保留插件命名空间。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_plugin_runtime.py` | +427 / −0 | 覆盖 Registry、依赖、状态、启停、诊断、EventBus、配置隔离、capability、官方插件 timer/命令清理和 Qt scheduler。 |
| `tests/test_config_schema.py` | +2 / −1 | 更新配置 schema 白名单和键数量快照，确保新增 `plugins` 命名空间不会被归一化丢失。 |

### 阶段文档与索引

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/plugin-phase-02-runtime/README.md` | +35 / −26 | 将 Phase 2 从规划基线更新为实施状态、边界、交付门和后续限制。 |
| `docs/plugin-phase-02-runtime/PLUGIN-RUNTIME-DESIGN.md` | +133 / −197 | 记录实际 runtime 合同、端口、生命周期、配置迁移和节日插件接入。 |
| `docs/plugin-phase-02-runtime/PLUGIN-RUNTIME-TEST-PLAN.md` | +45 / −46 | 更新测试矩阵、Qt 线程约束、性能探针和 Windows smoke 结果。 |
| `docs/INDEX.md` | +1 / −0 | 登记 Phase 2 详细文档和本报告入口。 |

### 未改动

- `pet/updater.py`、`pet/update_settings.py`：Core 自动更新并行会话文件保持原样。
- `content/`、Phase 1 资源 DLC 安装服务：本轮只通过 provider 接入，不重复实现资源扫描和安装。
- `plugin-roadmap-demo.html`：仍为未跟踪文件，未加入提交范围。
- 未迁移 Chat UI、AI、Agent、视觉、歌词、余额查询和外部程序联动。

## 三、实现要点

1. **运行时状态**：每个插件拥有独立 `PluginRecord`，状态区分 `discovered`、`enabled`、`running`、`disabled` 和 `fault`。manifest、API/Core 兼容性、重复 ID、缺失依赖和依赖环都落入诊断，不阻塞 Core。
2. **依赖生命周期**：启动按拓扑序进行，只有依赖已处于 `running` 才启动依赖方；停止按逆序进行。单个 `start()` 或 `stop()` 异常只影响对应记录，并继续处理其他插件。
3. **事件边界**：`CoreEvent` 的时间戳由 Core 生成；payload 必须可 JSON 序列化；回调异常被总线捕获，订阅被移除并将 owner 标记为 fault。Phase 2 不做跨线程事件。
4. **配置边界**：插件只能拿到自己的 `PluginConfigStore`。旧字段读取成功后写入 `plugins.<plugin_id>.settings`，原始旧值保留到 `legacy`，避免设置页仍写旧字段时发生静默丢失。
5. **官方插件 allowlist**：`official.festival-reminder` 使用显式 factory 注册。runtime 不根据外部 manifest 动态 import 任意 entrypoint，第三方可执行插件生态留给后续阶段。
6. **AppShell 兼容**：旧的 `_sync_festival_service()`、`trigger_festival_now()`、`toggle_festival_reminder()` 和 PetWindow 回调继续存在，但委托给插件 runtime；当 factory 失败时，兼容 fallback 会被明确停止，避免遗留 QTimer。
7. **停用清理**：插件 context 的 subscription、scheduler handle、command registration 均由 context 管理，停用或退出时统一清理；不承诺 Python 模块从解释器内存热卸载。

## 修改文件说明

本节标题同时保留模板要求的逐文件说明；具体文件和增删统计见上方第二节。

## 性能分析

**复现命令**：在 Windows 本机以 `QT_QPA_PLATFORM=offscreen` 执行内联 Python probe，使用 `time.perf_counter_ns()` 统计 1000/200 次循环，并用 `tracemalloc` 记录插件激活前后内存；另执行 200 次 `remind_now` 延迟探针。探针使用实际 `QApplication`、`QTimer` 和 Phase 2 runtime，配置与音频/通知外部边界使用内存端口以保持样本确定。

**环境**：Windows 10 10.0.26100-SP0，Python 3.11.1，PySide6 6.11.1，单线程 GUI 进程，`QT_QPA_PLATFORM=offscreen`。

| 指标 | 实测 | 样本/归属 |
|---|---:|---|
| 空 Registry `discover` | mean 3.74 µs；median 2.30 µs；p95 3.60 µs；max 588.90 µs | n=1000；新增 runtime 冷路径 |
| noop plugin discover/start/stop | mean 35.25 µs；median 24.40 µs；p95 61.60 µs；max 7263.60 µs | n=1000；新增 runtime 生命周期 |
| festival Qt discover/start/stop | mean 5266.01 µs；median 4314.55 µs；p95 9890.30 µs；max 16137.60 µs | n=200；真实 `QApplication`/`QTimer` |
| `remind_now` 延迟 | mean 4385.21 µs；median 3818.60 µs；p95 7970.30 µs；max 13777.20 µs | n=200；插件呈现端口路径 |
| 激活定时器数量 | 1 → 0 | festival 启动 → stop；新增 timer 仅在插件启用时存在 |
| 线程数量 | 1 → 1 | Qt probe 前后；未新增线程 |
| tracemalloc 当前内存 | active delta +6112 bytes；peak delta +7984 bytes；stop 后 current 56 bytes | 单次 festival Qt probe；Python 分配采样，不等同进程 RSS |

**结论**：

- Core 没有插件目录时只执行轻量 Registry 路径；空 Registry 的 p95 为 3.60 µs。
- noop 生命周期 p95 为 61.60 µs；真实 festival Qt 生命周期 p95 为 9890.30 µs，主要成本来自 Qt timer/service 初始化，且只在插件启用时触发。
- `remind_now` 的 p95 为 7970.30 µs；本轮没有新增网络请求、子进程、跨进程 IPC 或后台线程。
- 常驻 timer 仅在 festival 插件运行时为 1 个，停止后为 0；线程数保持 1。tracemalloc 采样在停止后回落到 56 bytes 当前分配，但未替代完整进程 RSS 基线。
- 角色资源、Core updater 和 Phase 1 DLC 安装路径未因 Phase 2 增加新的磁盘扫描、网络访问或下载行为。

## 实机运行记录

以下记录来自 Windows 本机真实 Python/Qt 运行，不是 CI，也不是 mock 的 AppShell 生命周期。为了避免无显示器环境阻塞，使用 `QT_QPA_PLATFORM=offscreen`；因此能够确认 Qt 对象、timer、命令和退出清理，但不声称完成真实可见像素、鼠标点击和三平台验收。

### 1. 无可选插件启动路径

探针创建实际 `Config`、`AppShell`、Starter DLC Registry 和 Qt 应用，未启用 festival 插件，输出：

```text
registry_before_start= discovered
registry_after_start= disabled enabled= False
starter_character= shenshen
plugin_diagnostics= []
registry_after_shutdown= disabled
```

这证明没有可选插件时 Core 仍完成启动和退出，Starter DLC 角色仍可解析，插件诊断为空。运行过程中观察到 3 条既有 `webm_clip` click 资源 meta warning，但没有阻塞启动，也没有生成 plugin fault。

### 2. 官方节日插件启动、命令和停止

同一 Windows 本机以启用 festival reminder 的临时配置运行实际 `AppShell`，输出：

```text
festival_state= running
festival_timer_count= 1
festival_commands= ('official.festival-reminder.remind_now', 'official.festival-reminder.toggle')
remind_now_invoked=True
timer_count_after_shutdown= 0
record_after_shutdown= stopped
runtime_instance_present_after_shutdown= True
```

`runtime_instance_present_after_shutdown=True` 是探针在 shutdown 前保存的本地变量仍有引用，不表示 runtime record 保留 live instance；`record_after_shutdown=stopped` 且 `timer_count_after_shutdown=0` 才是运行时清理状态。命令调用、调度器创建和应用退出顺序均通过实际 Qt 对象完成。

### 3. 失败/边界路径

自动化实机探针覆盖了禁用插件路径、无插件诊断为空、插件 stop 后 timer 清零和插件 command 清理。manifest/API/Core 不兼容、重复 ID、依赖缺失/环、非 JSON payload、未声明 capability 和 plugin start/stop 异常由 `tests/test_plugin_runtime.py` 的真实 runtime 对象测试覆盖；这些测试确认错误停留在插件诊断，不冒泡到主窗口。

无法在当前 offscreen 探针中验证的项目：真实桌面可见气泡、系统通知弹层、鼠标菜单交互，以及 macOS/Linux 平台 adapter。原因是当前会话使用 Windows 10.0.26100-SP0 和 offscreen Qt；没有以此环境冒充跨平台或可见 UI 结果。后续发布前需要在真实显示桌面及三平台分别补跑。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests` | `All checks passed!` |
| 编译检查 | `python -m py_compile pet/plugins/*.py` 及修改过的 Python 文件 | 通过 |
| 聚焦 | `QT_QPA_PLATFORM=offscreen; python -m pytest -q tests/test_config_schema.py tests/test_plugin_runtime.py tests/test_festival.py tests/test_voice_chime_service.py tests/test_app_startup_fallback.py tests/test_feature_gating.py` | `158 passed in 11.50s` |
| 全量 | `QT_QPA_PLATFORM=offscreen; python -m pytest -q` | `2990 passed, 11 skipped, 13 warnings in 419.67s (0:06:59)` |
| 报告纪律 | `python -m pytest -q tests/test_pr_report_discipline.py` | `29 passed in 0.53s` |
| 差异空白 | `git diff --check` | 实现和文档修改已通过 |

## 七、已知限制与后续

- Phase 2 只完成 GUI 主进程、GUI 线程内的官方 in-process 插件；worker/JSONL IPC 留给 Phase 3。
- 不执行未知来源的 Python entrypoint，不开放第三方可执行插件加载；第三方 SDK、签名和发布生态留给 Phase 6/7。
- 没有设置页插件管理 UI；当前通过 runtime 默认配置、兼容 facade 和开发/测试入口启停。
- 没有 Python 模块热卸载保证；停用保证服务、订阅、timer 和 command 清理。
- `festival_reminder` 仍保留旧宿主兼容路径，以避免现有设置页和测试一次性破坏；后续可在迁移稳定后删除重复 facade。
- 本轮只完成 Windows offscreen 实机 smoke；真实可见桌面、macOS/Linux、启动时间/RSS/包体发布基线仍是跨平台发布前门禁。
- 当前观察到的 `webm_clip` click 资源 meta warning 与 Phase 2 runtime 无关，但建议在独立媒体资源任务中追踪。

## 八、风险与回滚

- **风险**：插件启用配置异常可能使单个 festival 插件进入 fault；runtime 会隔离该插件，Core 仍可启动。用户可清除 `config.data["plugins"]` 中对应命名空间或禁用该插件。
- **风险**：旧设置页仍读写 `festival_reminder_*` 平面字段；适配层保留 `legacy` 并优先兼容旧字段，暂不静默删除旧配置。
- **回滚**：代码层可回退本轮 Phase 2 实现和 `a1c3b6b` 之后的工作；配置中的 `plugins` 根键为空时会回到无可选插件路径，不覆盖 Core 更新文件和 Phase 1 DLC 文件。
- **数据残留**：回滚不会删除用户的旧节日配置；插件命名空间和 `legacy` 数据可保留，待后续迁移工具处理。没有新增网络缓存、worker 数据或外部进程状态。

## 九、阶段结论

Phase 2 的 Core runtime、配置隔离、官方节日提醒迁移、自动化回归和 Windows offscreen AppShell smoke 已完成。按照本报告的证据边界，代码验收门可以进入后续 Phase 3 规划；真实可见桌面和 macOS/Linux 验收仍必须在正式发布前补齐，不能由本报告的 offscreen 结果替代。



\r\n
