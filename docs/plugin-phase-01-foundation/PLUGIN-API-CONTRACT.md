# 插件 API 合同（v1）

> 状态：设计合同，基线日期 2026-09-24。总体边界见 [`PLUGIN-DLC-ARCHITECTURE.md`](PLUGIN-DLC-ARCHITECTURE.md)，更新行为见 [`PLUGIN-UPDATE-PROTOCOL.md`](../plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md)。

## 1. Manifest

每个插件目录必须有 `manifest.json`：

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
  "integrity": {"sha256": "...", "signature": "..."}
}
```

约束：`id` 稳定且使用小写 ASCII、`.`、`-`、`_`；`version` 为 `major.minor.patch`；`kind` 只能是 `content`、`in_process`、`worker`；`api_version` 主版本不兼容时拒绝；`core_requires` 和 `platforms` 必须匹配；`content` 的 `entrypoint` 必须为空；代码插件的 entrypoint 必须是包内相对路径；正式包必须有 SHA-256 和签名，开发模式只能通过显式开关放宽签名要求。

entrypoint、资源路径和依赖路径必须规范化，拒绝绝对路径、`..` 穿越和目录外符号链接目标。

## 2. 生命周期

```text
discover → validate → resolve_dependencies → enable → start
                                           ↘ disable / fault
start → stop → disabled
```

- `content` 只做数据校验和资源注册，不执行代码；
- `in_process` 的 `start/stop` 必须可重复调用，停止后不得继续订阅事件；
- `worker` 由 Core 管理子进程，不得自行接管 Core 退出流程；
- 异常进入 `fault`，记录诊断并隔离；
- 第一阶段不承诺 Python 模块热卸载。

## 3. PluginContext

Context 是插件获得 Core 服务的唯一入口，至少提供 plugin ID、Core/API 版本、只读 Core 信息、插件配置命名空间、Event Bus、气泡/动画/通知/音效原语、capability 检查、结构化日志和 worker IPC 代理。

插件不得保存或修改 `PetApp`、`PetWindow` 私有引用，不得绕过 Context 直接访问全局配置或 Qt 对象。

## 4. 配置合同

```text
core.json
instances/config-slot-N.json
plugins/<plugin-id>/config.json
plugins/<plugin-id>/data/
```

插件只能读写自己的 `plugins.<plugin_id>` 命名空间。插件配置迁移必须提供版本号和迁移函数；失败时保留原始数据并标记 `legacy`。密钥、token、API Key 不属于普通配置，继续使用 keyring/安全存储。

## 5. Event Bus

事件必须带 `type`、`source`、Core 生成的 `timestamp` 和 JSON 可序列化 `payload`。稳定事件名使用前缀，例如 `core.app.started`、`core.app.shutdown_requested`、`pet.character.changed`、`pet.window.clicked`、`pet.animation.requested`、`plugin.worker.ready` 和 `plugin.worker.error`。

订阅者异常必须被 Event Bus 捕获并记录，不得沿事件调用栈回传到主窗口；高频事件必须声明频率和是否允许丢弃。

## 6. Capability

初始能力建议为：

```text
character.read       animation.request     speech.present
sound.play            notification.present settings.read
settings.write        network.request       filesystem.user_data
screenshot.capture   process.spawn
```

`network.request`、`screenshot.capture`、`process.spawn` 和密钥访问等高风险能力默认不授予进程内第三方插件。正式安装时，manifest、用户授权和平台策略必须同时满足。

## 7. Worker JSONL

每行一个 UTF-8 JSON 对象，禁止多条消息拼一行：

```json
{
  "type": "hello",
  "request_id": "optional-id",
  "protocol_version": 1,
  "payload": {}
}
```

Core 启动 worker；worker 发送 `hello`；Core 校验后发送 `config_push`；worker 发送 `ready`；运行期使用 `event`、`heartbeat`、`error`；Core 发送 `shutdown` 并等待有限时间；超时后终止进程并记录原因。协议版本不兼容必须是可诊断错误，不能静默表现为“插件没反应”。

## 8. 兼容策略

API 主版本不兼容时拒绝加载；次版本新增可选字段时旧插件继续运行；未知字段忽略并记录调试日志；缺少依赖只禁用依赖链；插件异常隔离并可有限重启；Core 降级后重新校验全部插件。
