# 两个官方 DLC：接口用法与内部接线

[入口](README.md) · [v1](API-V1.md) · [兼容策略](COMPATIBILITY.md)

案例版本 AI 1.0.4 / 识屏 1.0.4。它们是在 v1 之前拆出的功能，继续保持旧接线以避免业务回退；**是生命周期与端口设计参考，不是可直接改 ID 发布的第三方模板**。第三方从 hello-local / echo-worker 起步。

## AI 对话（host-only）
源码：`features/ai_chat/host/factory.py`、`runtime.py`、`contribution_settings.py`。

```text
Core 发现 / 校验 / 执行状态
 → create_host() 元数据
 → Core 绑定 FeatureHostContext
 → runtime_factory → AiRuntime
 → 菜单命令 / 聊天入口
 → 每次请求 context.api 解析主 API
 → DLC 执行文字 / 文件请求
```
可借鉴：factory 延迟导入，Contribution 将菜单与命令分离，设置组件保留草稿，API 在请求前解析，停用撤销执行授权并取消任务。Key 属于 Core 安全存储，聊天业务配置 / 个人聊天数据不因删除 DLC 安装副本而清除。

内部部分：现代 / 经典 / 快捷 / 灵动岛窗口、文件解读和旧菜单桥接，以及 `pet.context_menus.shared` 等导入，属于既有兼容接线；不在 `pet.mod_api.v1` 作者契约内。不要复制这些私有 import。新通用 MOD 的菜单 factory 只接收 `(menu, handle)`。

## 识屏（host-worker）
源码：`features/screen_understanding/host/factory.py`、`worker_adapter.py`、`runtime.py`、`manual.py`；生产进程入口由构建脚本冻结。

```text
手动 / 自动入口
 → Core 绑定窗口值快照和有限操作端口
 → DLC 策略、预算、白名单和请求生命周期
 → 已验证 WorkerLaunch / 包租约
 → 独立生产 Worker：HELLO / READY
 → 截图 / 视觉服务请求 / 结果
 → 按请求与当前授权过滤 → 显示气泡
```
可借鉴：手动生命周期不由自动开关误取消，旧请求结果不能穿过停用屏障，运行目录使用项目内可信 `data/feature-runtime`，请求配置按用途解析，不把启动故障误报成 Key 错误。

内部部分：DesktopQueryPort、FeatureWindowPorts、专用 VisionConfigService、屏幕策略与旧 Worker adapter。普通第三方 v1 context 不自动获得这些端口；Echo 用公开 WorkerClient 演示通用握手 / 取消，不应声称也获得了截屏接口。Worker 是进程分离，不是对可信 Python 的安全沙箱。

## 数据与执行边界
| 内容 | 归属 / 行为 |
|---|---|
| 主 / 视觉 Key | Core 安全存储，JSON 只有引用 |
| AI / 识屏业务设置 | 对应 owner 命名空间 / 个人数据 |
| 包代码、生产 Worker | `data/plugins/<ID>/versions/<版本>`，管理器删除受管理版本 |
| 原始 ZIP | 管理器外部源，不删除 |
| Core 自己的设置草稿 | DLC 局部刷新不能覆盖 |

两个官方包因展示元数据和新交付基线升为1.0.4；生产 Worker 的通用租约 owner 与共享元数据原子写入输入也有变化，因此本次重新构建而非复用旧二进制。不把 AI 执行搬回 Core，也不把识屏 Worker 回退为 Core 内执行。旧格式无 name / description 仍可用。

## 实际效果与限制
可以学习官方 DLC 如何分离配置、业务、窗口桥接和 Worker；真正有长期兼容保证的创作入口仍是 v1 + 离线样例。复制官方私有依赖不享受公共 API 兼容承诺。
