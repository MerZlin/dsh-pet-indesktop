# Phase 2 Core 插件运行时设计

> **规划基线**：2026-09-24
> **实现状态**：设计合同；当前仓库尚未实现 `pet/plugins/`

本文定义 Phase 2 的 Core 插件运行时。它是面向官方插件的第一版稳定边界，不是第三方 Python 插件执行规范，也不承诺任意模块的热卸载。

## 1. 目标与非目标

### 目标

- 让 Core 在没有任何可选插件时正常启动；
- 为官方进程内插件提供不暴露 `PetApp`、`PetWindow` 和全局配置的 `PluginContext`；
- 以事件、服务端口和 capability 作为唯一接入面；
- 依赖按拓扑顺序启动、按逆序停止；
- 启动/回调/停止错误被隔离并形成结构化诊断；
- 迁移一个真实但低风险的 `official.festival-reminder` 插件；
- 保持现有设置页、菜单、语音让位和启动提醒行为兼容。

### 非目标

- 不在 Phase 2 执行未知来源的 Python `entrypoint`；
- 不开放第三方可执行插件、不做沙箱和权限弹窗；
- 不实现 worker、跨线程/跨进程事件和 JSONL IPC；
- 不实现远程 catalog、Steam Workshop 或 DLC 更新；
- 不把 Chat UI、AI、Agent、视觉、歌词、余额和外部程序迁移到本阶段；
- 不承诺解释器级模块卸载。停用的定义是服务停止、订阅取消和任务清理。

## 2. 运行时对象模型

### 2.1 `PluginRegistry`

建议接口：

```python
class PluginRegistry:
    def discover(self) -> list[PluginRecord]: ...
    def enable(self, plugin_id: str) -> None: ...
    def disable(self, plugin_id: str) -> None: ...
    def start_all(self) -> None: ...
    def stop_all(self) -> None: ...
    def diagnostics(self) -> list[PluginDiagnostic]: ...
```

职责边界：

- 读取内置官方插件描述并建立 `PluginRecord`；
- 校验 `id`、`kind`、`api_version`、Core 兼容范围、平台和依赖；
- 通过显式 factory allowlist 构造官方插件；
- 检测重复 ID、缺失依赖和依赖环；
- 维护 `discovered`、`disabled`、`ready`、`running`、`fault`、`stopped` 等状态；
- 将每个阶段的拒绝原因写入 `PluginDiagnostic`，但不阻塞 Core 主窗口。

Phase 2 的 discovery 不等于任意代码加载。`manifest.entrypoint` 即使存在，也不能让未知来源代码自动进入主进程；只有 Core 编译/打包时登记的官方 factory 才可实例化。

### 2.2 `PluginRecord` 与诊断

每个运行时记录至少保存：

```text
plugin_id
name
version
kind
api_version
core_requires
dependencies
capabilities
factory_key
state
enabled
failure_stage
reason
started_at / stopped_at
```

诊断必须可被日志和未来设置页消费。典型失败阶段包括 `manifest`、`compatibility`、`dependency`、`factory`、`start`、`event_callback`、`stop` 和 `cleanup`。

### 2.3 `PluginContext`

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

上下文是按插件创建的视图：

- `config` 只能访问 `plugins.<plugin_id>`；
- `events` 发布事件时自动填充 `source=plugin_id`，订阅 owner 固定为该插件；
- 每个 port 在调用前检查 capability；
- 不提供 `app`、`window`、`config.data`、Qt widget、Qt signal 或任意 Core service 容器；
- 不把内部 Python 对象作为 payload 传递，只允许 JSON 可序列化值。

## 3. 官方插件发现与依赖

### 3.1 Phase 2 来源限制

Phase 2 只发现两类来源：

1. Core 内置的官方 in-process factory；
2. Phase 1 已注册的 content provider（资源包，不含可执行入口）。

不从用户目录扫描和执行任意 `.py`、wheel 或 manifest entrypoint。后续第三方生态必须在 Phase 6/7 重新定义签名、权限和兼容矩阵后才可开放。

### 3.2 依赖解析

- 依赖以插件 ID 和版本范围声明；
- 缺失依赖只禁用依赖方，并记录 `missing_dependency`；
- 依赖环中的全部成员进入 `fault`/`disabled` 诊断，不启动任何成员；
- 启动使用拓扑序；停止使用已启动节点的逆拓扑序；
- 同一插件 ID 重复注册时不猜测优先级，全部拒绝并报告冲突。

## 4. `CoreEventBus`

### 4.1 事件模型

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

- 事件总线只在 GUI 线程同步执行；Phase 2 不跨线程投递；
- `timestamp` 由 Core 生成，使用带时区的时间；
- `payload` 在发布前验证可 JSON 序列化；
- 回调异常由总线捕获，取消该订阅，并把 owner 插件标记为 `fault`；
- 总线不暴露 Qt 信号、`QApplication`、`PetApp` 或 `PetWindow`；
- 发布者不能伪造其他插件的 `source`。

### 4.2 第一批稳定事件

```text
core.app.started
core.app.shutdown_requested
core.config.changed
pet.character.changed
pet.window.clicked
plugin.lifecycle.changed
plugin.error
```

事件 payload 只放稳定值，例如角色 ID、插件 ID、配置键和错误摘要，不放 widget、QObject、线程对象或密钥。

## 5. Core 服务端口与 capability

### 5.1 展示与语音

`PresentationPort` 至少提供：

```python
show_bubble(text, *, instance_id=None, duration_ms=None)
notify(title, text, *, level="info")
speak(text, *, voice=None, slot="plugin")
```

端口负责：

- 找到合适的桌宠实例和展示层；
- 气泡不可用时降级系统通知；
- 将语音请求接入 Core 的统一音频时隙策略；
- 不让插件直接持有 `PetWindow` 或调用私有 `_speech_bubble`。

节日提醒不再调用 `AppShell.festival_service` 判断是否能说话，而是通过 `speech.present`/音频时隙策略向 Core 请求；Voice Chime 与节日提醒的让位关系由 Core 统一裁决。

### 5.2 调度器

```python
call_later(delay_ms, callback, *, owner: str) -> TaskHandle
call_repeating(interval_ms, callback, *, owner: str) -> TaskHandle
cancel(task: TaskHandle) -> None
cancel_owner(owner: str) -> None
```

调度器在 GUI 线程创建 Qt timer，但插件只看 `TaskHandle`。停用插件时必须执行 `cancel_owner(plugin_id)`，并能在诊断中报告残留任务数。

### 5.3 命令和内容 provider

`CommandRegistry` 只允许插件注册带 owner 的命令：

```python
register(command_id, callback, *, owner, title, capability="menu.contribute")
unregister_owner(owner)
invoke(command_id, *, source="core")
```

`ContentProviderRegistry` 只做代理，不重复扫描 Phase 1 内容目录：

```python
list() -> list[ContentPackage]
resolve(kind: str, item_id: str) -> ContentPackage | None
```

角色资源仍由 `CharacterRegistry` 决定优先级和 fallback，插件运行时只消费 provider 结果。

### 5.4 capability

首个节日提醒插件允许：

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

capability 缺失时由 Core 抛出可诊断的拒绝异常，并写入 `plugin.error`；插件不得通过反射、全局导入或上下文逃逸绕过检查。

## 6. `PluginConfigStore` 与旧配置兼容

### 6.1 存储形状

新命名空间采用：

```json
{
  "plugins": {
    "official.festival-reminder": {
      "schema_version": 1,
      "settings": {
        "enabled": true,
        "speak": true,
        "calendar": "cn"
      },
      "legacy": {}
    }
  }
}
```

插件接口：

```python
store.get(key, default=None)
store.set(key, value)
store.update(values)
store.save()
store.migrate(migration)
```

实现要求：

- store 对象只绑定一个 `plugin_id`，不能读取其他插件命名空间；
- 写入只允许更新该插件的 `settings`、`schema_version` 和迁移记录；
- 不允许插件直接访问 `Config.data`，也不覆盖 Core 字段；
- keyring、安全存储和 API Key 不进入普通插件配置。

### 6.2 旧扁平字段迁移

第一阶段采用兼容适配，不重写全部 `Config._normalize_pet_settings`：

1. 优先读取 `plugins.official.festival-reminder.settings`；
2. 命名空间缺失时读取现有 `festival_reminder_*` 扁平字段；
3. 首次成功读取后写入新命名空间；
4. 原字段原样保存在 `legacy`，不静默删除；
5. 旧设置页继续写扁平字段时，适配层同步更新插件 store 并发布 `core.config.changed`；
6. 新插件写入命名空间时，兼容 facade 可按需要回写旧字段，直到设置页完成迁移。

迁移失败时保留旧值和错误原因，插件使用安全默认值继续运行，不能因为配置迁移失败阻塞 Core 启动。

## 7. `AppShell` 生命周期接入

固定顺序：

1. `AppShell.__init__` 创建 `PluginRegistry`；
2. 注册 Phase 1 `ContentProvider`；
3. 扫描并验证内置插件 manifest，但不启动；
4. 创建桌宠窗口、托盘、展示服务、调度器和命令宿主；
5. `AppShell.start()` 启动启用插件；
6. 所有可启动插件完成后发布 `core.app.started`；
7. 设置变更发布 `core.config.changed`，由插件按 owner 处理；
8. 退出先发布 `core.app.shutdown_requested`；
9. 按逆序停止插件，取消任务、命令和事件订阅；
10. 再执行现有窗口、IPC、更新和其他 Core 服务退出流程。

插件启动失败时，Registry 记录 fault 并继续启动桌宠。插件停止失败时，先继续清理其他插件和 Core 资源，再汇总诊断；禁止让单个插件异常卡住退出。

## 8. `official.festival-reminder` 迁移合同

### 8.1 保留的逻辑

- 复用 `pet/festival.py` 的纯日期判断、文案和重复提醒抑制；
- 保持 30 秒调度语义、启动补提醒和 `remind_now` 不受总开关限制；
- 保持语音关闭时的文字提醒和气泡不可用时的通知降级；
- 保持现有 `tests/test_festival.py`、`tests/test_voice_chime_service.py` 的核心语义。

### 8.2 替换的宿主依赖

| 当前耦合 | Phase 2 端口 |
|---|---|
| 直接读取 `app.config` | `PluginConfigStore` |
| 直接访问 `PetWindow.show_bubble` / 私有气泡字段 | `PresentationPort.show_bubble()` |
| 直接调用 `system_notify` | `PresentationPort.notify()` |
| 直接调用 `ensure_audio_channel` | `PresentationPort.speak()` + Core 音频时隙策略 |
| 直接创建并持有 QTimer | `SchedulerPort.call_repeating()` |
| 菜单/设置入口直接绑服务对象 | `CommandRegistry` + 兼容 facade |

### 8.3 兼容 facade

以下入口暂时保留，但只委托给 Registry/插件，不再构造或持有独立服务逻辑：

- `AppShell._sync_festival_service()`；
- `AppShell.trigger_festival_now()`；
- `AppShell.toggle_festival_reminder()`；
- `PetWindow.on_festival_now`；
- `PetWindow.on_toggle_festival`。

插件关闭后必须没有属于该插件的 QTimer、事件订阅、命令和后台引用；不要求 Python 模块从解释器卸载。

## 9. 故障隔离与诊断

- manifest、兼容性、依赖、factory、启动、事件回调和停止阶段分别记录 failure stage；
- 插件异常不能冒泡到主窗口事件循环；
- 事件回调异常会取消订阅并 fault 该 owner；
- 启动失败插件不会自动无限重试；Phase 2 只允许显式重新 enable/start；
- 诊断包含 plugin ID、版本、阶段、原因、capability、fallback/降级动作和时间；
- `diagnostics()` 返回不可变快照，供日志、测试和未来设置页读取。

## 10. 计划中的包结构

以下是目标结构，不代表当前文件已存在：

```text
pet/plugins/
  __init__.py
  models.py
  registry.py
  context.py
  events.py
  capabilities.py
  config.py
  ports.py
  diagnostics.py
  builtin/
    __init__.py
    festival_reminder/
      __init__.py
      manifest.json
      plugin.py
```

Phase 2 先允许官方插件实现与 Core 同仓库；只有 API 经过真实运行、故障测试和迁移复盘后，才讨论把它发布为外部可安装插件。

## 11. 实施顺序与出口

1. 先写 runtime/config/event bus 单元测试，再实现最小 Registry；
2. 接入 AppShell，但用空插件和故障插件验证 Core 不被阻塞；
3. 接入 content provider，确认不重复实现 Phase 1 Registry；
4. 实现 `official.festival-reminder` factory、配置适配和兼容 facade；
5. 做真实桌面启动、提醒、停用、退出和重复提醒验证；
6. 记录性能基线并生成 Phase 2 PR 报告。

Phase 2 只有在测试计划中的验收门全部满足后，才允许进入 Phase 3 worker 设计；本阶段不以“目录存在”或“接口写出来”代替真实插件生命周期验证。
