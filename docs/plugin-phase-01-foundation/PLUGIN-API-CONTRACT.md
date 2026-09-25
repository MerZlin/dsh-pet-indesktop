# Phase 1：插件 / DLC API 合同

> **状态：内部官方接口基线（2026-09-25）**
>
> 本文冻结 Phase 1 资源 DLC 的内部边界，并为 Phase 2/3 提供术语。它不是第三方 SDK 承诺；任何对外兼容保证都必须经过官方插件和 Worker 的实际迁移验证。

## 1. 接口分层

### 当前稳定的内部官方接口

- `CharacterRegistry`：扫描、列出、解析可用角色。
- `ContentManager`：验证、安装、激活、卸载和回滚本地资源包。
- `ContentProviderRegistry`：由 Core 向功能插件提供内容查询。
- Phase 2 的 `PluginContext`、`CoreEventBus`、配置存储和受限 Host Port。
- Phase 3 的 Worker Supervisor 和 `pet-worker/v1` 控制消息。

### 未来可能公开但目前不承诺兼容的接口

- 第三方 content SDK。
- 第三方 worker SDK、权限申请和签名发布。
- 远程 catalog、社区目录和 Workshop adapter。

不要根据当前 Python 类名推断公开 API。公共接口必须通过版本合同、错误语义和迁移测试冻结。

## 2. Content manifest

```json
{
  "id": "official.character.shenshen",
  "name": "深深角色包",
  "version": "1.0.0",
  "kind": "content",
  "api_version": "1",
  "core_requires": ">=5.0.0,<6.0.0",
  "platforms": ["windows", "macos", "linux"],
  "dependencies": [],
  "capabilities": ["character", "animation", "phrases"],
  "entrypoint": null,
  "content": {"characters": ["shenshen"]},
  "integrity": {"sha256": "...", "signature": null}
}
```

规则：

- `kind` 必须是 `content`；`entrypoint` 必须为空。
- `id`、角色 ID、版本号和资源相对路径使用受限字符。
- Core 版本和平台不兼容时不得激活。
- 目录和 ZIP 使用同一套按 POSIX 相对路径排序的逻辑 SHA-256。
- ZIP 拒绝绝对路径、`..`、符号链接和越界解压。
- 本地正式校验要求 SHA-256；开发环境只有显式 `allow_unsigned=True` 才可接受未签名包。
- `signature` 保留验证接口；签名强制校验属于远程可信发布阶段，不在 Phase 1 假装已完成。

## 3. Content provider

Core 负责资源来源和 fallback；功能插件只通过 provider 查询：

```python
class CharacterRegistry:
    def scan(self) -> list[CharacterPackage]: ...
    def get(self, character_id: str) -> CharacterPackage | None: ...
    def list_available(self) -> list[CharacterPackage]: ...
    def resolve(self, character_id: str) -> CharacterPackage: ...
```

provider 不允许：

- 修改 Core 全局配置；
- 直接创建或操作 `PetWindow`；
- 绕过 Registry 拼接安装目录；
- 在内容包中执行 Python 或启动子进程。

## 4. PluginContext 预留边界

Phase 2 的进程内插件只能从 `PluginContext` 获取：

- `PresentationPort`：气泡、通知、语音；
- `SchedulerPort`：一次性和重复调度；
- `CommandRegistry`：注册可撤销的命令；
- `ContentProviderRegistry`：角色和资源查询；
- `CoreEventBus`：稳定事件；
- `PluginConfigStore`：自己的命名空间；
- capability 集合和结构化 logger。

不得暴露 `PetApp`、`AppShell`、`PetWindow` 私有字段、全局 `Config.data`、Qt 私有信号或 keyring 内容。

## 5. Worker 控制协议与业务事件语义

两者必须分开：

- **`pet-worker/v1`**：Worker 的 `hello`、`ready`、`config_push`、`heartbeat`、`error`、`shutdown` 和 `event` 控制/传输外壳。
- **`agent-event/v1`**：Agent Link 业务事件的字段、规范化结果和语义版本。

`pet-worker/v1` 不规定 DSH bridge 的日志 tail、WebSocket、外部工具或安装细节；这些是具体 Worker 的实现来源。Worker 只接收经过筛选的配置摘要，不读取 `Config.data`、密钥或 token。

## 6. 能力和失败语义

能力必须声明并由 Core 检查。Phase 2 节日提醒允许 `notification.present`、`speech.present`、`settings.read/write`、`scheduler.timer` 和 `menu.contribute`；不得默认获得 `network.request`、`screenshot.capture`、`process.spawn` 或 `secret.read`。

插件启动、回调或停止失败时进入诊断状态，不阻塞 Core。资源包失败时遵循上一版本、Starter DLC、legacy、Core fallback。停用插件只保证停止服务、取消订阅和任务，不承诺 Python 模块从解释器内存热卸载。

## 7. 版本策略

- `api_version` 只描述对应层的合同，不能跨层复用。
- Core 兼容范围、平台、依赖和能力必须在激活前检查。
- 破坏性修改先增加新版本合同和迁移适配，不静默改变旧字段。
- 第三方公开前必须补齐签名、权限、兼容矩阵、弃用周期和可诊断错误。
