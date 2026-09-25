# v5 插件化 / DLC 总路线图

> **基线日期：2026-09-25**
>
> 本文是 v5 插件化重建的总入口。路线已经根据当前代码、Phase 1–3 的实际进度以及外部架构评估重新收敛。它不把“完整第三方插件生态”视为默认目标，而是优先保证稳定 Core、官方资源 DLC、官方低风险功能和高风险能力的进程隔离。

## 1. 路线结论

目标不是把现有 Python 模块机械地一对一搬成插件，而是建立清晰的运行边界：

1. **稳定 Core**：窗口、动画、基础交互、配置、生命周期、更新、故障诊断。
2. **Core Host Ports**：Core 向功能提供受限的气泡、通知、语音、调度、命令和内容服务端口。
3. **Content DLC**：角色、动画、台词、音效和主题等不包含可执行代码的内容包。
4. **Official In-process Features**：低风险、无网络、受限的官方功能插件。
5. **Worker Features**：网络、截图、AI、Agent、外部程序等高风险或持续阻塞能力。
6. **Conditional Distribution / Ecosystem**：只有实际需要远程发布或社区生态时，才启用分发适配和第三方 SDK。

这六层分别回答不同问题：

| 层 | 负责 | 不负责 |
|---|---|---|
| Core Kernel | “桌宠能否启动、显示、移动和退出” | AI、网络、角色内容策略 |
| Host Ports | “插件如何请求 Core 提供的能力” | 暴露 `PetApp`、`PetWindow` 私有对象 |
| Content DLC | “桌宠显示什么内容” | 执行 Python、启动进程、访问网络 |
| In-process Feature | “Core 内如何运行低风险策略” | 未声明能力、密钥和外部程序 |
| Worker Feature | “高风险任务在哪里执行” | 直接修改 Core 状态或 UI |
| Distribution / Ecosystem | “内容如何发布、安装和维护” | 成为 Core 启动的硬依赖 |

## 2. 长期边界判断

每个新功能进入路线前，至少通过四个问题：

- **拔掉测试**：拔掉该功能后，Core 是否仍能启动、移动、显示并完成退出？如果不能，它不是可选插件，而是 Core 依赖。
- **溺水测试**：网络、外部进程或 Worker 崩溃时，桌宠是否仍能操作？如果不能，必须继续下沉到 Worker 或增加故障隔离。
- **换皮测试**：替换角色和主题时，行为策略、配置和 Core 是否保持不变？如果不能，说明内容与运行时仍然耦合。
- **离线测试**：断网时，基础桌宠、资源 DLC 和低风险官方功能是否仍然可用？网络功能必须降级而不是阻塞启动。

这些测试比“有几个插件文件”更能判断拆分是否正确。

## 3. 依赖方向

稳定方向是：

```text
Core Kernel
  └─> Host Ports / Event Bus / Config Boundary
        ├─> Content Provider ──> Content DLC
        ├─> Official In-process Feature
        └─> Worker Supervisor ──> Worker Process
                                      └─> agent-event/v1 等业务语义
```

约束：

- Content、Feature Plugin 和 Worker 不反向导入 `PetWindow`、`AppShell` 私有字段或全局 `Config.data`。
- `pet-worker/v1` 是 Worker 生命周期控制协议；`agent-event/v1` 是 Agent 业务事件语义，二者不混为一套传输协议。
- DSH bridge 的文件 tail、WebSocket、外部工具和安装细节不自动升级为通用 Worker 协议。
- “同一可执行文件 + `QProcess`”解决进程故障隔离和部署复杂度问题，但**不会自动减小 PyInstaller 包体**；包体变化必须由独立的 import/dependency audit 证明。

## 4. 阶段状态与路线

状态词汇固定为：`已完成基线`、`实施中`、`计划中`、`条件启用`、`发布前验收`。

| 阶段 | 当前状态 | 目标与出口 |
|---|---|---|
| Phase 1 | **已完成基线** | Resource DLC 的 Registry、Starter DLC、本地目录/ZIP 安装、校验、激活、回滚和 legacy fallback；远程发布、强制签名和第三方 SDK仍未完成。 |
| Phase 2 | **已完成基线** | Core 插件运行时、受限 `PluginContext`、Event Bus、配置命名空间和官方节日提醒插件；当前进入 API 冻结和边界验证，不承诺第三方 SDK或包体瘦身。 |
| Phase 3A | **实施中** | Agent Link 事件采集 Worker 稳定、QProcess 生命周期、fallback、冻结程序 smoke、父子进程清理、背压和性能基线。 |
| Phase 3B | **计划中** | Phase 3A 满足稳定性门后，再隔离主动识屏的截图、dHash、视觉请求和网络响应解析。 |
| Phase 3C | **计划中** | 余额、歌词、文件解释、外部播放器等能力逐项评估，不承诺全部迁移。 |
| Phase 4 | **计划中 / 条件启用** | 本地 DLC 事务作为必须保持的底座；远程 catalog、镜像、签名、自动检查和管理 UI 只有需要远程发布时启用。 |
| Phase 5 | **条件启用** | 本地目录/ZIP 始终支持；GitHub Release、CDN、Workshop 只在有实际分发需求时接入。 |
| Phase 6 | **条件启用** | 先开放 content 文档和示例；只有签名、权限、兼容矩阵和维护能力成熟后，才评估第三方 Worker SDK。 |
| Phase 7 | **发布前验收** | 三平台启动、目录权限、Worker、Starter DLC、升级/回滚、包体/启动/RSS/CPU/下载体积和故障恢复的最终发布门。 |

## 5. Phase 1：资源型 DLC 基线

Phase 1 的结果是一个**可验证的资源闭环**，不是完整插件生态：

- `CharacterRegistry` 统一已安装 DLC、Starter DLC、旧外部目录、legacy 资源和 Core fallback。
- `ContentManager` 负责本地目录/ZIP 的 manifest、路径、Core/platform、SHA-256、staging、active/previous、回滚和自检。
- `content` DLC 不含执行入口；`entrypoint` 必须为空。
- `assets/characters` 兼容路径在迁移验证前不得删除。

重新开始时从 `ContentManager + CharacterRegistry + fallback` 边界恢复，不回退整个 Core。

## 6. Phase 2：Core 插件运行时基线

Phase 2 的职责是把官方低风险能力接入稳定边界：

- `PluginRegistry` 使用显式 allowlist/factory；不执行未知 Python `entrypoint`。
- `PluginContext` 只提供 `PresentationPort`、`SchedulerPort`、`CommandRegistry`、`ContentProvider`、配置、事件和结构化日志。
- 插件配置放在 `plugins.<plugin_id>` 命名空间，旧扁平字段通过兼容适配逐步迁移。
- 插件故障不得阻塞 Core；停用时必须清理事件订阅、命令和定时器。
- 官方节日提醒作为 API 验证插件；关闭它不应影响基础桌宠交互。

Phase 2 失败时：保留 Phase 1，禁用官方 in-process 插件，只恢复最小 `PluginContext / Config / EventBus` 接口后重新验证。

## 7. Phase 3：Worker 优先与横向支线

Phase 3 不是“把所有功能都拆出去”，而是先处理最容易阻塞或拖垮 Core 的能力。

### 3.1 Phase 3A：Agent Link 事件采集

迁移文件 tail、原始记录解析、协议校验和语义规范化；Core 保留展示、对话、成本、bridge 安装和用户决策。实施顺序：

1. 收口 `pet-worker/v1`、QProcess 宿主和握手/心跳/关闭。
2. 稳定 Agent Link Worker 与 `agent-event/v1` 转发。
3. 完成 frozen executable smoke 和父子进程清理。
4. 增加队列上限、背压、洪峰诊断和 stale generation 丢弃。
5. 记录长时间 RSS、CPU、事件延迟、重启和日志增长。
6. Windows 实机验收后独立封存，可随时切回 `in_process`。

### 3.2 Phase 3B：主动识屏

只有 3A 稳定后才开始：Core 掌握是否允许、白名单、dwell、limiter、用户确认和记忆策略；Worker 执行前台窗口查询、截图、dHash、视觉请求和网络响应解析。

### 3.3 Phase 3C：逐项评估

余额、歌词、文件解释、外部播放器、Harness 等能力按照崩溃风险、网络阻塞、权限/密钥、主进程耦合、常驻成本和可测试性逐项决定。

### 3.4 三条横向支线

Phase 3 同时建立但不急于重写：

- **PyInstaller/import dependency audit**：验证 Worker 与 Core 实际包体、import 图和冻结程序边界；不能凭“独立进程”推断包体变小。
- **依赖边界检查**：Core、Content、Feature Plugin、Worker 的反向导入和私有字段依赖必须可检测。
- **配置迁移、备份和恢复**：迁移前备份、结果校验、legacy 保留、失败恢复和可重复执行必须成为跨阶段能力。

## 8. Phase 4：DLC 更新中心

Phase 1 已有本地安装事务；Phase 4 不重复造轮子，而是把它与更新中心、诊断和未来远程源衔接：

- 必须保持 staging、路径安全、SHA-256、active/previous、原子激活、自检和自动回滚。
- 只有需要远程发布时，才启用 catalog、多镜像、下载重试、签名强制校验、自动检查和管理 UI。
- `pet/updater.py` / `pet/update_settings.py` 继续负责 Core 更新；DLC 更新不得写 Core 文件，Core 更新不得删除 DLC。

## 9. Phase 5–7：条件闸门

- **Phase 5** 不预先接入 Steam 依赖。Workshop 必须是独立 adapter，不能进入 Core 基础加载逻辑。
- **Phase 6** 先开放 content 示例，再决定是否建设 SDK。不得开放任意第三方 Python 代码进入 Core 进程，也不承诺热卸载。
- **Phase 7** 是发布门而非当前开发前置。三平台、签名、公钥轮换、兼容矩阵和 API 弃用策略在实际对外发布时强制执行。

## 10. 当前非目标

当前路线不做：

- 立即把 Chat UI 拆成独立 Qt 进程；
- 任意 Python 插件热卸载或未知 `entrypoint` 执行；
- 以 Worker 迁移为理由重写 Core 自动更新协议；
- 因为预留 Workshop 而提前引入 Steam SDK；
- 以文档拆分数量代替行为、故障和恢复边界验证。

## 11. 关联文档

- Phase 1：[`../plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md`](../plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md)
- Phase 1 API：[`../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md`](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md)
- Phase 2：[`../plugin-phase-02-runtime/README.md`](../plugin-phase-02-runtime/README.md)
- Phase 3：[`../plugin-phase-03-worker/README.md`](../plugin-phase-03-worker/README.md)
- Phase 4：[`../plugin-phase-04-updates/README.md`](../plugin-phase-04-updates/README.md)
