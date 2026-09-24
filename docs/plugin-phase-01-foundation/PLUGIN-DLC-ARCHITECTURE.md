# 插件化 / DLC 化架构基线（v5 重建）

> 状态：设计基线，**截至 2026-09-24 尚未代表代码已经完成迁移**。
> 总路线见 [`PLUGIN-DLC-ROADMAP-v5.md`](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)。
> API 字段以 [`PLUGIN-API-CONTRACT.md`](PLUGIN-API-CONTRACT.md) 为准，更新流程以 [`PLUGIN-UPDATE-PROTOCOL.md`](../plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md) 为准，v4 数据迁移以 [`PLUGIN-MIGRATION-v4-to-v5.md`](PLUGIN-MIGRATION-v4-to-v5.md) 为准。

## 1. 目标与非目标

目标是将当前桌宠应用重建为“最小可运行 Core + 外部 DLC/插件”的分层架构：Core 在没有可选 DLC 时仍能启动、显示桌宠、完成基础交互并安全退出；角色和功能能力可以独立安装、更新、禁用和回滚；AI、截图、网络和外部程序联动默认隔离在 worker 进程；v4 用户数据可以迁移到 v5 的 Core、实例和插件命名空间。

本轮不承诺任意第三方 Python 代码的热加载/热卸载，不把 Chat QWidget 立即拆成独立 Qt 进程，也不开放没有签名、哈希和兼容性校验的正式 DLC。不能把现有 Python 模块机械地逐个改名为插件。

## 2. 分层模型

```text
Core: 生命周期 / 桌宠窗口 / 动画抽象 / 基础交互 / 配置 / IPC
      插件发现 / API / 权限 / 日志 / 诊断 / Core 自动更新

content DLC: 角色、动画、台词、音效、主题、节日素材
in_process DLC: 受限的提醒、台词、菜单、设置和展示扩展
worker DLC: AI、Agent、视觉、歌词、余额、外部程序和网络服务
```

插件类型固定为：

| 类型 | 是否包含代码 | 运行位置 | 第一阶段策略 |
|---|---:|---|---|
| `content` | 否 | Core 读取资源 | 优先落地，官方 Starter DLC 先行 |
| `in_process` | 是 | Core 进程 | 只允许受限官方插件，使用稳定 API |
| `worker` | 是 | 独立进程 | 网络、密钥、截图、外部程序能力默认采用 |

## 3. Core 主体边界

Core 必须保留：

- `QApplication`、主进程生命周期、退出与 session-end 处理；
- 桌宠窗口：透明、置顶、鼠标穿透、拖拽、缩放和位置恢复；
- 动画播放抽象、帧缓存、预热策略、WebM/GIF 后端；
- 待机、转向、移动、点击、拖拽等基础行为；
- 移动、边缘限制、基础物理和基础碰撞；
- 多实例/多角色窗口协调所需的核心 IPC；
- 气泡、动画触发、音效播放等展示原语；
- 系统托盘、基础菜单和基础设置宿主；
- 配置存储、schema migration、每实例配置隔离；
- 插件目录扫描、manifest 校验、依赖解析、启停和错误隔离；
- Core Event Bus、平台适配、日志、诊断、崩溃恢复和 Core 自动更新。

Core 不直接拥有 AI Provider、API Key、聊天服务、视觉模型、主动截图、dHash、Agent 监视器、歌词网络请求、大量角色素材、特定外部生态桥接和非基础网络轮询。Core 只提供这些能力所需的接入原语。

## 4. 资源型 DLC

资源 DLC 不包含任意可执行代码，是第一阶段优先对象：

- 角色包、动画、动作分类和 `move_strides.json`；
- `manifest.json`、身体框、头部框和动作标签；
- 角色台词、点击绑定、人格设定、音效和语音；
- 主题、气泡皮肤、菜单图标、背景；
- 节日动画、节日文案和特殊彩蛋素材。

目标目录：

```text
content/
  characters/
    shenshen/
      manifest.json
      videos/
      phrases.json
      sounds/
  themes/
  seasonal/
```

现有 `assets/characters/<id>/videos/` 已接近内容 DLC 边界，但迁移必须保持相对路径、大小写和 manifest 语义兼容。迁移完成前，Core 可以继续读取当前内置资源作为 fallback；不能为了拆包破坏已有角色启动路径。完整 `shenshen` 应成为官方 Starter DLC，资源搬迁属于后续实施阶段。

## 5. 功能型 DLC

### 5.1 进程内

基础自言自语和台词策略、点击台词、普通提醒、待办面板、节日提醒、语音报时、灵动岛展示、菜单/快捷启动扩展、主题/设置页扩展、基础音乐状态展示，以及不涉及高风险外部调用的行为策略，适合在 Core 进程内运行。

进程内 DLC 必须通过公开 API 接入，不得依赖 `PetApp`、`PetWindow` 私有字段。第一阶段先作为官方内置插件验证 API，再评估第三方开发。

### 5.2 独立进程

AI Chat、DSH/Claude/Cursor/OpenCode Agent 联动、`integrations/dsh-pet-bridge`、主动识屏、截图和视觉模型、DeepSeek 余额、Harness、歌词网络请求、外部播放器轮询和未来第三方自动化插件，默认放入 worker。

Core 只负责 worker 启停、本地 IPC、事件转发、展示、崩溃检测、有限重启、权限和用户确认。Chat UI 第一阶段仍是懒加载的官方 UI 插件，只有出现明确卡死、内存或稳定性证据才重新评估拆进程。

## 6. API 与信任边界

公共抽象包括：

```text
PluginManifest / PluginRegistry / PluginContext / PluginLifecycle
CoreEventBus / ContentProvider / FeatureProvider / WorkerProvider / Capability
```

插件只能通过 `PluginContext` 获取 Core 服务，不能直接修改全局 `Config.data`；配置统一进入 `plugins.<plugin_id>` 命名空间；事件必须经过 `CoreEventBus`；插件只能申请 manifest 中声明的 capability。插件失败不能阻塞 Core 启动。第一阶段不设计 Python 模块热卸载，停用只保证停止服务和解绑事件。

## 7. Worker 生命周期

统一采用 JSON Lines 本地 IPC，最小消息类型为：

```text
hello → ready → config_push / event / heartbeat / error → shutdown
```

Core 启动 worker 后完成 `hello`/`api_version` 握手，推送配置摘要，监控心跳，退出时先 graceful shutdown，超时后按平台策略终止。worker 崩溃不得导致 Core 退出；重启必须有限次、带退避并提供可诊断原因。详细字段见 [`PLUGIN-API-CONTRACT.md`](PLUGIN-API-CONTRACT.md)。

## 8. 更新与数据目录

Core 与 DLC 分开更新：Core 更新不删除 DLC，DLC 更新不覆盖 Core；DLC 不兼容时禁用并显示原因；Core 降级时重新检查 DLC；插件、缓存和运行时临时目录分离；失败保留可重试包和诊断日志。

```text
data/
  core.json
  instances/config-slot-N.json
  plugins/<plugin-id>/{config.json,data,cache,logs}
  sessions/
  secrets/
```

## 9. 实施顺序

1. **Phase 0：文档与边界冻结**：完成四份架构文档，不改变运行时行为。
2. **Phase 1：资源 DLC**：目录扫描、manifest、兼容检查、缓存、安装/卸载/回滚，转换 Starter DLC。
3. **Phase 2：Core 插件运行时**：registry、context、配置命名空间、Event Bus、启停、capability、诊断。
4. **Phase 3：Worker 插件**：按 Agent Link、主动识屏、DSH bridge、AI/网络能力迁移，并完成进程级测试。
5. **Phase 4：DLC 更新中心**：catalog、下载校验、staging、原子激活、回滚和设置页。

最低验收包括：无 DLC 启动、manifest 拒绝、依赖隔离、路径穿越拒绝、worker 崩溃隔离、Core/DLC 互不覆盖、迁移可重复执行。

## 10. 当前实现状态

本文冻结的是重建边界，不表示所有阶段已经实现。当前项目已有角色目录、manifest 和外部资源目录等可复用基础；当前 `pet/updater.py` 与 `pet/update_settings.py` 的自动更新工作属于并行会话，本架构不得覆盖、回退或强行改造其接口。
