# pet.mod_api.v1 接口基线

[入口](README.md) · [兼容策略](COMPATIBILITY.md) · [host-only](HOST-ONLY.md) · [Worker](HOST-WORKER.md)

适用于 Core 4.2.5 起的本地可信功能包。以下是公开薄接口；`pet.app`、`PetWindow`、全局 `Config`、其他 DLC 私有模块不在兼容承诺内。包身份、哈希、版本和启停状态由 Core 校验；接口隔离不是 Python 安全沙箱。

## 1. 定义与挂载
```python
from pet.mod_api.v1 import Contribution, FeatureDefinition

def create_host():
    return FeatureDefinition(
        "demo.my-mod",
        (Contribution("hello", "menu", command="hello"),),
        settings_factory=create_settings,
        runtime_factory=lambda context, **kwargs: Runtime(context),
    )
```
`FeatureDefinition` 的 `mount_contract` 默认 `mod/v1`。Core 据此通用挂载，不按官方 ID / factory 名称判断。旧定义默认空，继续走旧兼容接线，不能给旧 AI / 识屏包盲目加 `mod/v1`，否则会重复挂载。

factory 模块及 `create_host()` **必须是无副作用、可无 GUI 导入的元数据声明**。只在实际 `create_settings` / Runtime 构造中导入 Qt、WorkerClient 和业务依赖。生产探针没有 Qt；顶层导入 QWidget 会导致包校验失败。

运行时在 GUI 线程提供 `commands: dict[str, callable]` 与 `start()`、`stop()`、`close()`。启用调用 start，停用撤销旧命令并 stop，Core 退出 close。start / stop 应可重复，close 必须释放定时器、窗口、连接和任务；不要阻塞 GUI，也不要让停用后旧菜单继续执行。

## 2. 菜单和设置
- `Contribution(id, kind="menu", command="hello", factory=...)`：id 包内唯一，command 对应 Runtime.commands。
- 菜单 factory 签名 `(menu, handle)`；可添加 QAction，回调检查 `handle.active` 后调用 `handle.invoke()`。不保存 Core 窗口对象。
- 未提供菜单 factory 时显示贡献 ID；建议提供中文可读标题。
- `settings_factory(context, parent=None)` 返回 QWidget，或带 `.widget` 的组件。必须实现以下协议：

| 方法 | 约定 |
|---|---|
| `dirty()` | 是否有未保存编辑 |
| `draft()` | 返回自身草稿，不写磁盘 |
| `confirm_save()` | 校验 / 原子保存；成功 True，失败 False 并保留编辑 |
| `discard_changes()` | 重新载入已保存值，成功 True |
| `dispose()` | 断开连接、取消任务、释放资源；不保存草稿 |

设置实例按 owner 局部挂载。其他页草稿不因 MOD 列表刷新而重建。影响有草稿的扩展时由 Core 询问保存 / 放弃 / 取消；不要从后台刷新覆盖输入。

## 3. 自有配置与数据
`FeatureHostContext` 是 Core 绑定的不可变端口集合，不要自己构造生产 context 或猜其他 owner 的路径。

```python
revision = context.configuration.revision()
values = context.configuration.read_namespace()
values["message"] = "你好"
context.configuration.commit_namespace(values, expected_revision=revision)

memory = context.documents["memory"].read()
memory["count"] = int(memory.get("count", 0)) + 1
context.documents["memory"].write(memory)

state = context.state_documents["state"]
with state.locked():
    value = state.read()
    state.write(value)
```
配置提交有版本冲突检测，冲突时保留草稿、重新载入或让用户处理，不静默覆盖另一个设置进程。`documents` 适合单拥有者文档；跨进程读改写使用带锁的 `state_documents`。删除包默认保留这些个人数据。不得把 Key 写到命名空间或日志。

context 上还存在供旧官方接线使用的端口；通用 MOD 不应假定 `desktop`、`window`、`user_data` 可用。本期不提供任意桌宠窗口操作、截图授权或跨 DLC 对象访问；需要的能力应另提公共接口，而非导入私有模块。

## 4. Core 简易 API
每次新请求前调用 `context.api.resolve(purpose)`，不要缓存 Key 或旧模型。可用用途为 `chat.send`、`files.interpret`、`balance.query`、`manual_look`、`analyze_frame`；仍受当前执行状态检查。配置入口对用户只有主 Key / 可选视觉 Key，无需用户填 owner / ID。

返回 `ApiRequest` 不可变快照，包含该次请求所需地址、路径、模型、超时、TLS 参数、临时 Key 及版本。Key 不落盘、不拼进错误消息。`effective_version(purpose)` 可用于比较配置代次；`metadata(purpose)` 用于无密钥展示，`subscribe(callback)` 返回取消订阅函数。具体字段以 `pet/api_ports.py` 的公开值对象为准。

API 端口只解析配置，不替你完成网络请求。网络 / TLS / 超时应保留正确错误分类；不要把文字连通等同视觉能力验证。自动回退到另一家地址时不能发送原服务 Key。停用时取消任务，普通配置更新不污染已开始请求的快照。

## 5. WorkerClient / 子进程
只在 Runtime 构造中导入：
```python
from pet.mod_api.v1 import WorkerClient
self.worker = WorkerClient(context)
self.worker.ready.connect(self.on_ready)
self.worker.response.connect(self.on_result)  # (request_id, dict)
self.worker.error.connect(self.on_error)      # 脱敏诊断字符串
```
- `start() -> bool`：经 Core 验证的启动描述；仅返回已启动流程，不等于 READY。
- `is_ready`：当前授权有效且完成 HELLO / READY。
- `request(operation, arguments=None) -> str | None`：只能 READY 后发请求；保存 ID 对应自己的 UI。
- `cancel(id) -> bool`：取消已知请求并过滤迟到响应；处理器须配合取消。
- `stop()`：取消 pending、请求自然退出；`close()` 再解除生命周期绑定，不可复用。

子进程使用 `pet.mod_api.worker_v1.serve(owner, handler)`；handler 为 `(operation, arguments, cancel_event) -> dict`。先接管租约再 HELLO，Core configure 后 READY。不要把 stdout 当日志；取消是合作式，handler 必须有超时 / 检查 Event。实用完整例子见 Echo 教程。

## 实际效果与限制
作者可独立增加菜单、设置、自有持久化和经校验的 Worker，无需导入 Core GUI 内部对象。v1 不提供所有官方能力，也不承诺任意第三方依赖已打包进 Core；host-only 依赖以 Core 已提供环境为限，额外运行时优先自行封装 Worker。
