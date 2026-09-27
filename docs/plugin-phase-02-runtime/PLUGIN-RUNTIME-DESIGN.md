# Phase 2：Core 插件运行时设计

> **修订：2026-09-27；状态：运行时已完成基线，新增选装贡献合同待 Phase 4/5 实现**
>
> 本文是官方插件运行时的内部设计合同。它不是第三方 SDK 的兼容承诺，也不负责解决 PyInstaller 包体问题。

## 1. 设计目标

Phase 2 已完成基线的职责是四件事；下面的贡献扩展另行标为后续目标：

1. 给官方低风险功能提供受限 Core 服务端口。
2. 隔离插件配置、事件、命令和调度任务。
3. 让插件故障、停止和依赖错误不会阻塞 Core。
4. 为 Phase 1 Content provider 和 Phase 3 Worker 建立依赖方向。

## 2. 依赖方向

```text
Core Kernel
  ├─> CoreEventBus / Config Boundary / Host Ports
  ├─> ContentProviderRegistry ──> Phase 1 Content DLC
  ├─> PluginRegistry ──> Official In-process Features
  └─> Worker Supervisor ──> Phase 3 Worker Processes
```

禁止方向：

- 插件导入 `PetWindow`、`AppShell` 私有实现或持有 Core 内部窗口。官方功能可以拥有自己的 QWidget UI，但必须遵循 GUI 线程和统一生命周期合同；这些 UI 随功能包交付，不因运行在主进程就归入 Core 安装包。
- 插件修改 `Config.data` 全局结构或读取其他插件命名空间。
- Content DLC 反向依赖功能插件或执行入口。
- Core 为了插件方便而反向依赖 Worker 的具体日志 tail、WebSocket 或外部工具。

## 3. PluginRegistry

```python
class PluginRegistry:
    def discover(self) -> list[PluginRecord]: ...
    def enable(self, plugin_id: str) -> None: ...
    def disable(self, plugin_id: str) -> None: ...
    def start_all(self) -> None: ...
    def stop_all(self) -> None: ...
    def diagnostics(self) -> list[PluginDiagnostic]: ...
```

当前只允许显式内置 manifest + factory：

- 重复 ID、API/Core 不兼容、缺失依赖和依赖环只生成诊断，不阻塞 Core。
- 依赖按拓扑顺序启动，逆序停止。
- 未知来源的 Python `entrypoint` 不执行。
- 启动异常进入 `fault`；停止异常记录诊断并继续停止其他插件。
- 停止后清理事件订阅、命令和定时器，但不承诺解释器模块热卸载。

## 4. PluginContext

```python
class PluginContext:
    plugin_id: str
    core_version: str
    api_version: str
    config: PluginConfigStore
    events: CoreEventBus
    presentation: PresentationPort
    scheduler: SchedulerPort
    commands: CommandRegistry
    content: ContentProviderRegistry
    capabilities: CapabilitySet
    logger: StructuredLogger
```

Context 是能力边界，不是 `AppShell` 的转发别名。插件只拿到自己的服务对象；服务对象不得泄露 Core 私有字段。

### Host ports

- `PresentationPort.show_bubble()` / `notify()` / `speak()`。
- `SchedulerPort.call_later()` / `call_repeating()`。
- `CommandRegistry.register()`，停用时返回可撤销句柄。
- `ContentProviderRegistry.list()` / `resolve()`。
- 结构化日志和诊断。

## 5. CoreEventBus

```python
@dataclass(frozen=True)
class CoreEvent:
    type: str
    source: str
    timestamp: datetime
    payload: dict
```

首批事件：

```text
core.app.started
core.app.shutdown_requested
core.config.changed
pet.character.changed
pet.window.clicked
plugin.lifecycle.changed
plugin.error
```

约束：

- Phase 2 事件只在 GUI 线程同步分发，不跨线程、不跨进程。
- payload 必须可 JSON 序列化；时间戳由 Core 生成。
- 回调异常由总线捕获，必要时取消该订阅并将插件标记为 fault。
- 不直接暴露 Qt signal、`QApplication`、`PetApp` 或 `PetWindow`。

## 6. 配置隔离

存储形状：

```text
config.data["plugins"][plugin_id] = {
  "schema_version": 1,
  "settings": {...},
  "legacy": {...}
}
```

插件只能使用自己的 `PluginConfigStore`：

```text
get(key, default)
set(key, value)
update(values)
save()
migrate(...)
```

兼容规则：优先读命名空间；缺失时从旧扁平字段适配；成功后迁移到命名空间；旧值保留 `legacy`。密钥继续由 keyring 管理。完整备份、重复执行、中断恢复和物理拆包迁移是后续验收项，不能只凭现有命名空间适配认定全部完成；权威合同见 [迁移计划](../plugin-phase-01-foundation/PLUGIN-MIGRATION-v4-to-v5.md)。

## 7. Capability

节日提醒允许：

```text
notification.present
speech.present
settings.read
settings.write
scheduler.timer
menu.contribute
```

默认拒绝：

```text
network.request
screenshot.capture
process.spawn
secret.read
bridge.install
```

Core 在调用端口时再次检查能力，而不是只相信 manifest。能力不足返回可诊断错误，不让插件绕过端口访问底层对象。

## 8. 生命周期接入

```text
AppShell.__init__
→ 创建 Registry
→ 注册 Phase 1 ContentProvider
→ 发现并验证官方插件（暂不启动）
→ 创建窗口/托盘/展示端口
→ AppShell.start 启动启用插件
→ 发布 core.app.started
→ 设置变更发布 core.config.changed
→ 发布 shutdown_requested
→ 逆序停止插件并清理资源
→ 继续现有 Core 退出流程
```

旧的节日提醒 facade 可以保留给设置页和测试，但其实现委托给插件，不重新构造服务。

## 9. 与 Worker 的关系

Phase 2 只提供 Worker 的宿主边界，不把 Worker 细节塞进进程内插件：

- `pet-worker/v1` 负责 Worker 生命周期、握手、心跳、配置摘要、关闭，以及 Phase 3B 的 `request/response` 请求路由。
- `agent-event/v1` 负责 Agent Link 的业务事件字段和语义。
- Worker 不获得 `PluginContext` 内部对象、全局配置或 keyring 访问能力；`config_push` 不含密钥。Core 可在授权的单次请求内临时下发 API Key，完成、失败、取消或超时后释放引用，不记录到日志和磁盘。
- GUI 主进程负责状态权威、用户确认、展示和降级；Worker 负责高风险执行。这里描述运行位置，功能专属策略、UI 和适配器的交付所有者仍是对应功能包，Core 只保留通用宿主和端口。

## 10. API 冻结与失败重启

API 冻结前必须由官方插件、Phase 1 Content provider 和至少一个真实 Worker 迁移验证。若官方插件引入耦合：

```text
保留 Phase 1
→ 禁用官方 in_process 插件
→ 缩减到 PluginContext / Config / EventBus 最小接口
→ 用官方插件重新验证
```

不得因为 Phase 2 API 设计不理想而回退或删除资源 DLC；也不得在没有实际需求时扩展第三方兼容。

## 11. 后续菜单与设置贡献扩展（尚未完成）

统一采用 [API 合同](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md) 的包所有者和状态规则。现有 `CommandRegistry` 不等于已经有完整的包级菜单、设置、搜索和快捷键注册系统。

Phase 4/5 必须补齐：

1. 贡献描述、所有者 ID、撤销句柄与唯一命令身份，托盘和右键共享命令而不复制功能状态。
2. 设置页懒加载、字段及搜索归属；停用仍可配置，但页面打开不触发后台工作。
3. 卸载/故障/不兼容时统一撤销或提供诊断入口，保留用户排序偏好，不能留下空动作。
4. 多实例占用协调、停止任务、关闭功能 UI、撤销贡献后再移除文件；不能卸载解释器模块时如实标记待重启，不伪装热卸载成功。
5. fallback 受安装、启用和授权状态约束；没有安装的功能不能由隐藏的 Core 实现复活。

先由屏幕理解样板验证，再供 AI 对话与文件理解使用；第三方 SDK 不是前提。[功能归属总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md) 是交付所有者的唯一清单，具体包格式和受控加载方案在 Phase 4A 确定。

## 12. 给使用者的效果说明

功能可以在主窗口里提供设置和聊天界面，同时把耗时工作交给 Worker；“在同一个窗口里使用”不等于“必须装在本体里”。当前基线仍以官方内置插件为主，未来功能代码和资源放在受管理的功能包中，Core 保存通用端口。通过扩展管理安装后出现入口，卸载后撤销入口并默认保留个人数据。包位置和格式尚未冻结，不能把现有资源目录直接当作 Python 插件目录。
