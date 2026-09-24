# Phase 2 Core 插件运行时设计

> **规划基线**：2026-09-24
> **实现状态**：Core runtime、配置隔离、`official.festival-reminder`、全量自动化门禁、性能取样和 Windows offscreen AppShell smoke 已完成；真实可见桌面与跨平台验收仍待发布前补齐

本文定义 Phase 2 的 Core 插件运行时，并记录当前实现与目标合同的对应关系。它是面向官方插件的第一版稳定边界，不是第三方 Python 插件执行规范，也不承诺任意模块的热卸载。

## 1. 目标与非目标

### 目标

- 让 Core 在没有任何可选插件时正常启动；
- 为官方进程内插件提供不暴露 `PetApp`、`PetWindow` 和全局配置的 `PluginContext`；
- 以事件、服务端口和 capability 作为唯一接入面；
- 依赖按拓扑顺序启动、按逆序停止；
- 启动、回调、调度和停止错误被隔离并形成结构化诊断；
- 迁移一个真实但低风险的 `official.festival-reminder` 插件；
- 保持现有设置页、菜单、语音让位和启动提醒行为兼容。

### 非目标

- 不执行未知来源的 Python `entrypoint`；
- 不开放第三方可执行插件、不做沙箱和权限弹窗；
- 不实现 worker、跨线程/跨进程事件和 JSONL IPC；
- 不实现远程 catalog、Steam Workshop 或 DLC 更新；
- 不把 Chat UI、AI、Agent、视觉、歌词、余额和外部程序迁移到本阶段；
- 不承诺解释器级模块卸载。停用的定义是服务停止、订阅取消、命令注销和任务清理。

## 2. 当前实现映射

| 设计对象 | 实现文件 | 说明 |
|---|---|---|
| `CoreEvent` / `CoreEventBus` | `pet/plugins/events.py` | 同步事件、JSON payload、owner 订阅和异常隔离。 |
| `PluginManifest` / `PluginDiagnostic` | `pet/plugins/manifest.py` | manifest 解析、`in_process` 边界、API/Core 兼容检查。 |
| `CapabilitySet` | `pet/plugins/capabilities.py` | 端口调用前拒绝未声明能力。 |
| `PluginConfigStore` | `pet/plugins/config.py` | 插件命名空间、legacy 快照、旧字段双向兼容。 |
| Core ports | `pet/plugins/ports.py` | Presentation、Scheduler、Command、Content、StructuredLogger。 |
| `PluginContext` / `PluginRegistry` | `pet/plugins/runtime.py` | 显式 factory、依赖图、生命周期、故障诊断。 |
| `official.festival-reminder` | `pet/plugins/builtin/festival_reminder/__init__.py` | 官方首个 in-process 插件。 |
| AppShell 生命周期 | `pet/app.py` | 发现、懒构造、UI 就绪后启动、配置事件、退出收口。 |
| Core 配置保留 | `pet/config.py` | `plugins` 顶层命名空间在 reload/save 中保留。 |

Phase 2 的 discovery 只接受 Core 显式登记的 factory。`manifest.entrypoint` 即使出现在数据中，也不会触发未知 Python 代码加载。

## 3. 运行时对象模型

### 3.1 `PluginRegistry`

公开接口：

```python
class PluginRegistry:
    def discover(self) -> list[PluginRecord]: ...
    def enable(self, plugin_id: str) -> None: ...
    def disable(self, plugin_id: str) -> None: ...
    def start_all(self) -> None: ...
    def stop_all(self) -> None: ...
    def diagnostics(self) -> list[PluginDiagnostic]: ...
```

当前职责：

- 读取显式登记的官方插件 manifest/factory 并建立 `PluginRecord`；
- 校验 `id`、`kind`、`api_version`、Core 兼容范围和依赖；
- 检测重复 ID、缺失依赖和依赖环；
- 依赖按拓扑序启动，已创建插件按逆序停止；
- 每个插件独立记录 `discovered`、`enabled`、`prepared`、`running`、`stopped`、`disabled` 或 `fault`；
- 启动、调度回调、事件回调和停止异常只进入诊断，不阻塞 Core；
- 支持首次 discover 后继续登记新的官方 factory，不重建已有运行记录。

### 3.2 `PluginRecord` 与诊断

当前 `PluginRecord` 保存：

```text
manifest
factory
enabled
state
instance
context
diagnostics
```

`PluginDiagnostic` 保存：

```text
plugin_id
stage
reason
severity
status
details
```

典型失败阶段包括 `manifest`、`compatibility`、`dependency`、`factory`、`start`、`event`、`scheduler`、`config` 和 `stop`。后续设置页可以直接消费 `diagnostics()` 快照；Phase 2 不把诊断 UI 纳入 Core 主窗口。

### 3.3 `PluginContext`

插件只能通过上下文获取 Core 服务：

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

实际传给插件的是按 owner/capability 限定的视图：

- `config` 只能访问 `plugins.<plugin_id>`；
- `events` 使用 scoped owner，停用时按 owner 清理；
- `commands` 自动加 `<plugin_id>.` 前缀并按 owner 注销；
- `scheduler` 自动绑定 owner，停用时取消全部定时任务；
- `presentation`、`scheduler`、`commands` 在调用前检查 capability；
- `content` 只读代理 Phase 1 `CharacterRegistry`，不重新实现角色优先级；
- 不提供 `app`、`window`、`config.data`、Qt widget、Qt signal 或任意 Core service 容器；
- 事件 payload 只允许 JSON 可序列化的字典值。

## 4. 官方插件发现、兼容和依赖

### 4.1 来源限制

Phase 2 只发现两类来源：

1. Core 内置的官方 in-process factory；
2. Phase 1 已注册的 content provider（资源包，不含可执行入口）。

不从用户目录扫描和执行任意 `.py`、wheel 或 manifest entrypoint。后续第三方生态必须在 Phase 6/7 重新定义签名、权限和兼容矩阵后才可开放。

### 4.2 依赖解析

- 依赖以插件 ID 声明；版本兼容由 manifest/Core 校验；
- 缺失依赖只 fault 依赖方，不阻塞无关插件；
- 依赖环记录具体路径，环内插件不启动；
- 启动使用拓扑序；停止使用已创建节点的逆拓扑序；
- 同一插件 ID 重复注册不猜测优先级，新增定义被拒绝并报告冲突。

## 5. `CoreEventBus`

### 5.1 事件模型

```python
@dataclass(frozen=True)
class CoreEvent:
    type: str
    source: str
    timestamp: datetime
    payload: dict
```

公开接口：

```python
subscribe(event_type, callback, *, owner: str) -> Subscription
unsubscribe(subscription) -> None
publish(event: CoreEvent) -> None
```

约束：

- 事件总线同步运行在 GUI 线程；Phase 2 不跨线程投递；
- `CoreEvent.now()` 由 Core 生成带时区时间戳；
- payload 必须是可 JSON 序列化字典；
- 回调异常由总线捕获，移除该订阅并交给 Registry fault 对应 owner；
- 发布过程使用订阅快照，回调增删订阅不会破坏当前迭代；
- 第一批事件为 `core.app.started`、`core.app.shutdown_requested`、`core.config.changed`、`pet.character.changed`、`pet.window.clicked` 和 `plugin.lifecycle.changed`；
- 总线不暴露 `QApplication`、`PetApp` 或 `PetWindow`。

## 6. 配置命名空间和 capability

### 6.1 配置结构

```text
config.data["plugins"][plugin_id] = {
  "schema_version": 1,
  "settings": {...},
  "legacy": {...}
}
```

插件只能通过自己的 `PluginConfigStore` 访问配置。密钥、token、API Key 不进入普通插件配置，继续使用 keyring/安全存储。

### 6.2 旧扁平字段迁移

当前官方节日插件使用：

1. 优先读取 `plugins.official.festival-reminder.settings`；
2. 命名空间缺失时读取现有 `festival_reminder_*` 扁平字段；
3. 首次读取成功后写入新命名空间；
4. 原字段原样保存在 `legacy`，不静默删除；
5. 旧设置页继续写扁平字段时，适配层同步更新插件 store；
6. Core 发布 `core.config.changed`，插件按 owner 重新应用配置；
7. 插件写入时镜像回旧字段，保持现有设置页和测试兼容。

配置迁移失败时保留原始值和诊断，插件使用安全默认值继续运行，不能阻塞 Core 启动。

### 6.3 Phase 2 capability

`official.festival-reminder` 当前声明：

```text
notification.present
speech.present
settings.read
settings.write
scheduler.timer
menu.contribute
```

明确不授予：

```text
network.request
screenshot.capture
process.spawn
secret.read
```

## 7. Core 服务端口

- `PresentationPort.show_bubble()`：桌宠气泡；失败后由插件决定是否调用 `notify()`；
- `PresentationPort.notify()`：Core 自绘桌面通知降级；
- `PresentationPort.speak()`：进入 Core 统一音频通道，不暴露 `VoiceChimeService` 实例；
- `SchedulerPort.call_later()` / `call_repeating()`：GUI 线程 QTimer，owner 自动绑定；
- `CommandRegistry.register()`：命令带插件前缀和 owner；
- `ContentProviderRegistry.list()` / `resolve()`：只读代理 Phase 1 registry；
- `StructuredLogger`：日志自动带 `plugin_id`。

## 8. `AppShell` 生命周期接入

固定顺序：

1. `AppShell.__init__` 创建 `PluginRegistry`；
2. 注册 Phase 1 `CharacterRegistry` provider；
3. 扫描并验证官方插件 manifest/factory，但不启动；
4. 创建桌宠窗口、托盘和展示服务；
5. `AppShell.start()` 在 UI 原语可用后同步配置并启动启用插件；
6. 所有可启动插件处理完成后发布 `core.app.started`；
7. 设置变更先同步旧 facade，再发布 `core.config.changed`；
8. 退出先发布 `core.app.shutdown_requested`；
9. 按逆序停止插件，取消任务、命令和事件订阅；
10. 再执行既有窗口、IPC、更新和其他 Core 服务退出流程。

插件启动失败时，Registry 记录 fault 并继续启动桌宠。插件停止失败时，先继续清理其他插件和 Core 资源，再汇总诊断；禁止让单个插件异常卡住退出。

## 9. `official.festival-reminder` 迁移合同

### 9.1 保留的逻辑

- 复用 `pet/festival.py` 的纯日期判断、文案和重复提醒抑制；
- 保持 30 秒调度语义、启动补提醒和 `remind_now` 不受总开关限制；
- 保持语音关闭时的文字提醒和气泡不可用时的通知降级；
- 保持现有 `tests/test_festival.py`、`tests/test_voice_chime_service.py` 的核心语义。

### 9.2 替换的宿主依赖

| 当前耦合 | Phase 2 端口 |
|---|---|
| 直接读取 `app.config` | `PluginConfigStore` |
| 直接访问 `PetWindow.show_bubble` / 私有气泡字段 | `PresentationPort.show_bubble()` |
| 直接调用 `system_notify` | `PresentationPort.notify()` |
| 直接调用 `ensure_audio_channel` | `PresentationPort.speak()` + Core 音频时隙策略 |
| 直接创建并持有 QTimer | `SchedulerPort.call_repeating()` |
| 菜单/设置入口直接绑服务对象 | `CommandRegistry` + 兼容 facade |

### 9.3 兼容 facade

以下入口暂时保留，但只委托给 Registry/插件，不再构造独立服务逻辑：

- `AppShell._sync_festival_service()`；
- `AppShell.trigger_festival_now()`；
- `AppShell.toggle_festival_reminder()`；
- `PetWindow.on_festival_now`；
- `PetWindow.on_toggle_festival`。

插件关闭后必须没有属于该插件的 QTimer、事件订阅、命令和后台引用；不要求 Python 模块从解释器卸载。

## 10. 故障隔离与诊断

- manifest、兼容性、依赖、factory、启动、事件回调、调度和停止阶段分别记录 failure stage；
- 插件异常不能冒泡到主窗口事件循环；
- 事件回调异常会取消订阅并 fault 该 owner；
- 启动失败插件不会自动无限重试；Phase 2 只允许显式重新 enable/start；
- 诊断包含 plugin ID、阶段、原因、严重级别和结构化 details；
- `diagnostics()` 返回列表副本，供日志、测试和未来设置页读取。

## 11. 当前文件结构与后续边界

```text
pet/plugins/
  __init__.py
  capabilities.py
  config.py
  events.py
  manifest.py
  ports.py
  runtime.py
  builtin/
    __init__.py
    festival_reminder/
      __init__.py
```

Phase 2 先允许官方插件实现与 Core 同仓库；只有 API 经过真实运行、故障测试和迁移复盘后，才讨论把它发布为外部可安装插件。Phase 3 另行定义 worker、JSONL IPC 和跨进程恢复，不在本模块中提前混入。

## 12. 实施出口

代码基础设施和官方插件迁移已完成；封板前仍需：

1. 运行 ruff、编译、聚焦测试和全量测试；
2. 对已知基线失败与 Phase 2 新失败做差异归因；
3. 记录发现/启动耗时、内存探针、owner 定时器数量；
4. 在真实 Windows 桌面尝试启动、提醒、停用和退出；若当前自动化环境无法安全显示窗口，必须记录探针输出和限制；
5. 生成并登记 Phase 2 PR 报告；
6. 通过后才进入 Phase 3 worker 迁移。
