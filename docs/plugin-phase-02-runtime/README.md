# Phase 2：Core 插件运行时

> **规划基线**：2026-09-24
> **状态**：规划已冻结，运行时代码尚未实现

## 本阶段定位

Phase 2 在 Phase 1 资源型 DLC 闭环之上，建立 Core 内的受限插件运行时。目标不是把现有 Python 模块机械地搬进插件目录，而是先定义一组由 Core 持有、可测试、可诊断的服务端口，并用一个低风险的官方 `in_process` 插件验证边界。

首个迁移对象确定为 `official.festival-reminder`。它复用现有节日日期逻辑和提醒语义，但不再直接依赖 `AppShell`、`PetWindow` 私有字段或全局 `Config.data`。

## 已确认边界

- Phase 2 只实现 Core 插件运行时基础设施和一个官方 `in_process` 插件；
- 所有 Phase 2 插件仍运行在 GUI 主进程、GUI 线程；
- `content` DLC 继续由 Phase 1 的 `CharacterRegistry`、`ContentManager` 和 provider 负责；
- 官方插件通过显式 allowlist/factory 注册，不执行未知来源的任意 Python `entrypoint`；
- 采用 `PluginRegistry`、`PluginContext`、`CoreEventBus`、capability 和配置命名空间；
- 插件启动/停止错误只能影响插件自身，不能阻塞桌宠主窗口和 Core 退出；
- 停用只保证停止服务、取消订阅和清理定时任务，不承诺 Python 模块热卸载；
- 不实现 worker、JSONL IPC、网络插件、远程 DLC 下载、Steam Workshop 或第三方可执行插件；
- 不加入完整插件管理设置页，只提供运行时诊断和开发入口；
- `pet/updater.py`、`pet/update_settings.py` 及现有 Core 自动更新协议不在本阶段范围内。

## 当前状态

已完成：

- Phase 1 资源 DLC 基础闭环已提交，Starter DLC 可由 Core 内容 Registry 发现；
- Phase 2 的范围、首个官方插件和 API 隔离原则已确认；
- 本目录已建立设计合同和测试计划。

尚未完成：

- `pet/plugins/` 运行时包尚未创建；
- `PluginRegistry`、`PluginContext` 和 `CoreEventBus` 尚未接入 `AppShell`；
- `FestivalReminderService` 尚未迁移为官方插件；
- 尚无 Phase 2 PR 报告或性能实测记录。

## 主要产出

| 文档 | 用途 |
|---|---|
| [`PLUGIN-RUNTIME-DESIGN.md`](PLUGIN-RUNTIME-DESIGN.md) | 运行时 API、生命周期、服务端口、配置隔离和节日提醒迁移合同。 |
| [`PLUGIN-RUNTIME-TEST-PLAN.md`](PLUGIN-RUNTIME-TEST-PLAN.md) | 测试矩阵、Qt 线程约束、性能基线和 Phase 2 验收门。 |

## 实施顺序

1. 先实现没有插件时也可工作的 Registry、诊断模型和事件总线；
2. 接入 `PluginConfigStore`，提供旧扁平字段的只读迁移和双向兼容适配；
3. 接入展示、调度、命令、内容 provider 和结构化日志端口；
4. 由 `AppShell` 按固定生命周期启动和停止插件；
5. 把节日提醒迁移为 `official.festival-reminder`，保留旧菜单、设置和测试 facade；
6. 记录发现耗时、启动耗时、常驻内存增量和定时器数量；
7. 通过全量测试和真实桌面运行后，再决定是否进入 Phase 3 worker 迁移。

## 与其他阶段的关系

- 开始前必读 [`../plugin-phase-01-foundation/`](../plugin-phase-01-foundation/)；
- Phase 2 不改变 Phase 1 的内容加载优先级和安装目录；
- Phase 3 才定义跨进程 worker 与 JSON Lines 协议；
- Phase 4 才把 DLC catalog、远程下载、staging 更新和回滚中心化；
- Phase 6/7 才强制签名发布和第三方插件生态。
