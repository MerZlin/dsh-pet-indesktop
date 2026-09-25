# Phase 2：Core 插件运行时设计

> **修订基线：2026-09-25；状态：已完成基线，进入 API 冻结**
>
> 本文是官方插件运行时的内部设计合同。它不是第三方 SDK 的兼容承诺，也不负责解决 PyInstaller 包体问题。

## 1. 设计目标

Phase 2 只解决四件事：

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

- 插件导入 `PetWindow`、`AppShell` 私有实现或直接持有 Qt 顶层对象。
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

兼容规则：优先读命名空间；缺失时从旧扁平字段适配；成功后迁移到命名空间；旧值保留 `legacy`。迁移必须支持备份、重复执行和失败恢复，密钥继续由 keyring 管理。

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

- `pet-worker/v1` 负责 Worker 控制生命周期、握手、心跳、配置摘要和关闭。
- `agent-event/v1` 负责 Agent Link 的业务事件字段和语义。
- Worker 不获得 `PluginContext` 内部对象、全局配置或秘密。
- Core 负责状态权威、用户确认、展示和降级；Worker 负责高风险执行。

## 10. API 冻结与失败重启

API 冻结前必须由官方插件、Phase 1 Content provider 和至少一个真实 Worker 迁移验证。若官方插件引入耦合：

```text
保留 Phase 1
→ 禁用官方 in_process 插件
→ 缩减到 PluginContext / Config / EventBus 最小接口
→ 用官方插件重新验证
```

不得因为 Phase 2 API 设计不理想而回退或删除资源 DLC；也不得在没有实际需求时扩展第三方兼容。
