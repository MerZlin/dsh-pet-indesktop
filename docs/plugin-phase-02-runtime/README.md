# Phase 2：Core 插件运行时

> **状态：已完成基线，进入 API 冻结和边界验证（2026-09-25）**
>
> Phase 2 不是包体瘦身方案，也不是第三方插件生态。它的作用是为官方低风险功能提供受限、可诊断、可停止的 Core 运行时边界，并为 Content provider 和后续 Worker 留出稳定接口。

## 已完成基线

- `PluginRegistry`：显式 allowlist/factory、发现、依赖排序、启停和诊断。
- `PluginContext`：展示、调度、命令、内容、事件、配置、能力和日志端口。
- `CoreEventBus`：GUI 线程同步事件、订阅清理和回调故障隔离。
- `PluginConfigStore`：`plugins.<plugin_id>` 命名空间、legacy 兼容和隔离写入。
- `official.festival-reminder`：复用既有节日逻辑，保持设置、菜单、提醒、语音让位和停止清理行为。
- Phase 1 `CharacterRegistry` / `ContentProvider` 接入，未重复实现资源扫描。
- 运行时故障不会阻塞 Core；停用会取消事件订阅、命令和调度任务。

## 当前定位

Phase 2 进入“API 冻结和边界验证”而不是继续无边界扩展：

- 官方插件优先，暂不执行未知 Python `entrypoint`。
- `PluginContext` 不暴露 `PetApp`、`AppShell`、`PetWindow` 私有字段或全局 `Config.data`。
- Core 掌握状态权威和展示能力；高风险执行逻辑进入 Phase 3 Worker。
- 新增接口必须先由官方插件和真实 Worker 迁移验证，再讨论第三方兼容。
- 不承诺通过插件拆分自动减小 PyInstaller 包体；包体需要独立 import/dependency audit。

## 不包含

- Worker、JSONL IPC、AI、Agent、视觉、歌词、余额和外部程序迁移。
- 远程 DLC 下载、Steam Workshop 和完整插件管理设置页。
- 任意第三方 Python 执行或模块热卸载。
- Core 自动更新协议重构。

## 需要继续验证的门

1. 无可选插件时 Core 离线启动并保持基础交互。
2. Content、Plugin、Worker 不反向导入 UI 私有实现。
3. 配置迁移可重复执行、失败可恢复，多实例仍隔离。
4. 停用官方插件后没有定时器、事件订阅或命令残留。
5. 关闭官方插件不影响 Core 基础交互和 Phase 1 Starter DLC。
6. Worker 只收到授权的配置摘要。
7. `pet/updater.py` 与 `pet/update_settings.py` 无非预期变化。

## 失败重启点

```text
保留 Phase 1
→ 禁用官方 in_process 插件
→ 只恢复 PluginContext / Config / EventBus 最小接口
→ 重新验证官方插件
```

## 关联文档

- [`PLUGIN-RUNTIME-DESIGN.md`](PLUGIN-RUNTIME-DESIGN.md)：运行时边界和依赖方向。
- [`PLUGIN-RUNTIME-TEST-PLAN.md`](PLUGIN-RUNTIME-TEST-PLAN.md)：行为测试、线程约束和验收门。
- [`../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md`](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md)：跨阶段 API 术语。
- [`../plugin-phase-03-worker/README.md`](../plugin-phase-03-worker/README.md)：Worker 边界和当前实施状态。
