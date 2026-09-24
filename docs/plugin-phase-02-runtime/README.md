# Phase 2：Core 插件运行时

> **规划基线**：2026-09-24
> **当前状态**：基础实现、全量自动化回归、性能取样和 Windows offscreen AppShell smoke 已完成；真实可见桌面与 macOS/Linux 验收仍是发布前门禁

## 本阶段定位

Phase 2 在 Phase 1 资源型 DLC 闭环之上，建立 Core 内的受限插件运行时。目标不是把现有 Python 模块机械地搬进插件目录，而是先定义一组由 Core 持有、可测试、可诊断的服务端口，并用一个低风险的官方 `in_process` 插件验证边界。

首个迁移对象是 `official.festival-reminder`。它复用现有节日日期逻辑和提醒语义，但通过 `PluginContext` 获取配置、事件、展示、调度、命令和音频能力，不再直接依赖 `AppShell`、`PetWindow` 私有字段或全局 `Config.data`。

## 已确认边界

- Phase 2 只实现 Core 插件运行时基础设施和一个官方 `in_process` 插件；
- 所有 Phase 2 插件仍运行在 GUI 主进程、GUI 线程；
- `content` DLC 继续由 Phase 1 的 `CharacterRegistry`、`ContentManager` 和 provider 负责；
- 官方插件通过显式 allowlist/factory 注册，不执行未知来源的任意 Python `entrypoint`；
- 采用 `PluginRegistry`、`PluginContext`、`CoreEventBus`、capability 和配置命名空间；
- 插件启动、事件回调和停止错误只能影响插件自身，不能阻塞桌宠主窗口和 Core 退出；
- 停用只保证停止服务、取消订阅、清理定时任务和命令，不承诺 Python 模块热卸载；
- 不实现 worker、JSONL IPC、网络插件、远程 DLC 下载、Steam Workshop 或第三方可执行插件；
- 不加入完整插件管理设置页，只提供运行时诊断和开发入口；
- `pet/updater.py`、`pet/update_settings.py` 及现有 Core 自动更新协议不在本阶段范围内。

## 当前实现

### 已完成的代码路径

| 能力 | 实现位置 | 当前口径 |
|---|---|---|
| 事件总线和 JSON payload 校验 | `pet/plugins/events.py` | 同步、GUI 线程、按 owner 清理；回调异常会移除订阅并 fault owner。 |
| manifest、兼容性和诊断 | `pet/plugins/manifest.py` | 只允许 `in_process`；检查 API/Core 版本和 entrypoint 边界。 |
| 插件配置隔离 | `pet/plugins/config.py` | 使用 `plugins.<plugin_id>.settings`，保留 `legacy`，兼容旧扁平字段。 |
| capability | `pet/plugins/capabilities.py` | 配置、展示、语音、调度、菜单等端口在调用前检查。 |
| Core 服务端口 | `pet/plugins/ports.py` | Presentation、Scheduler、Command、Content 和结构化日志。 |
| Registry / Context / 生命周期 | `pet/plugins/runtime.py` | 显式 factory、依赖拓扑启动、逆序停止、故障隔离和诊断。 |
| 官方节日提醒插件 | `pet/plugins/builtin/festival_reminder/__init__.py` | 通过端口使用 `FestivalReminderService`；不开放任意 Python 入口。 |
| AppShell 接入 | `pet/app.py` | UI 就绪后启动插件，退出先发事件再停插件；旧 facade 保留。 |
| Core 配置保留插件命名空间 | `pet/config.py` | reload/save 不会丢失 `plugins` 数据。 |

### 当前测试覆盖

- `tests/test_plugin_runtime.py` 已覆盖事件 payload、回调故障隔离、依赖排序、依赖失败、配置 legacy 映射、capability、官方节日插件端口和清理；
- `tests/test_festival.py`、`tests/test_voice_chime_service.py` 和 `tests/test_app_startup_fallback.py` 保持通过；
- 具体命令、样本量、全量测试结果和已知基线失败记录在 [Phase 2 PR 报告](../PR-REPORT-PLUGIN-PHASE2-2026-09-24.md)；
- 性能样本、全量门禁和 Windows offscreen AppShell smoke 已在本轮完成；真实可见桌面、macOS/Linux 和发布基线仍未由本轮覆盖，不能用 offscreen 结果替代。

## 主要产出

| 文档 | 用途 |
|---|---|
| [`PLUGIN-RUNTIME-DESIGN.md`](PLUGIN-RUNTIME-DESIGN.md) | Registry、Context、Event Bus、capability、配置隔离、AppShell 生命周期和节日提醒迁移合同。 |
| [`PLUGIN-RUNTIME-TEST-PLAN.md`](PLUGIN-RUNTIME-TEST-PLAN.md) | 测试矩阵、Qt 线程约束、性能基线、实机探针和 Phase 2 验收门。 |
| [`../PR-REPORT-PLUGIN-PHASE2-2026-09-24.md`](../PR-REPORT-PLUGIN-PHASE2-2026-09-24.md) | 逐文件变更、性能数字、验证命令和实机限制记录。 |

## 实施顺序与完成状态

1. `[x]` 实现没有插件时也可工作的 Registry、诊断模型和事件总线；
2. `[x]` 接入 `PluginConfigStore`，提供旧扁平字段的双向兼容适配；
3. `[x]` 接入展示、调度、命令、内容 provider 和结构化日志端口；
4. `[x]` 由 `AppShell` 按固定生命周期启动和停止插件；
5. `[x]` 把节日提醒迁移为 `official.festival-reminder`，保留旧菜单、设置和测试 facade；
6. `[x]` 记录并复核 Registry 发现/生命周期耗时、常驻内存增量和定时器数量；
7. `[x]` 完成全量回归和 Windows offscreen AppShell smoke；真实可见桌面与跨平台运行继续作为发布前门禁，再进入 Phase 3 worker 迁移。

## 与其他阶段的关系

- 开始前必读 [`../plugin-phase-01-foundation/`](../plugin-phase-01-foundation/)；
- Phase 2 不改变 Phase 1 的内容加载优先级和安装目录；
- Phase 3 才定义跨进程 worker 与 JSON Lines 协议；
- Phase 4 才把 DLC catalog、远程下载、staging 更新和回滚中心化；
- Phase 6/7 才强制签名发布和第三方插件生态。
