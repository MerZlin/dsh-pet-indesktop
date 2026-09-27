# Phase 3B：主动识屏 Worker 边界、实现与复建计划

> 实施起点：`9834612`；原报告日期：2026-09-26；本设计修订：2026-09-27。实现分支与逐次验证见原报告。
> 状态：实现与本地自动化已有通过记录；用户随后反馈“识屏无误，手动测试没问题”。
> [原实施报告](../PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md) 与 [用户对齐记录](../grill-2026-09-27-插件化中期对齐.md) 分别保留证据，不将手测扩写为跨平台、安装/卸载或全部发布验收。
> 进入依据：[Phase 3A 稳定性收口](PHASE3A-STABILITY-CLOSEOUT.md)；
> 上级路线：[Phase 3](README.md)、[v5 总路线](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)。

## 1. 目标、非目标和不变量

迁移自动识屏与手动“看看屏幕”的高风险执行路径，不重写策略或 GUI。
同一可执行文件的 `--worker proactive-screen` 在 `pet.app` 导入之前分流；
独立 OS 进程使用 QProcess 的 stdin/stdout，与 Core 交换 `pet-worker/v1` JSONL。

不实现 Worker DLC、远程安装、第三方代码、Chat UI 拆分、长期 soak；不修改 Core 自动更新。
同一 EXE 隔离故障不等于缩小包体；本轮不承诺内存下降或发布体积下降。

| GUI 主进程权威（运行位置） | Worker 执行 | 不跨越的边界 |
|---|---|---|
| 用户开关、白名单、dwell、idle、Agent Link 忙碌守卫 | 查询前台进程/窗口 | Worker 不决定是否打扰用户 |
| 8 秒观察调度、每日额度、逐次网络尝试许可 | 截图、dHash、JPEG、视觉 HTTP 重试 | 自动重试每次向 Core 请求许可 |
| 配置、keyring、请求 generation、结果有效性 | 单次凭据、单帧短期内存、受限任务 | 无 Config.data、keyring、Qt GUI、PetApp/PetWindow 导入 |
| 气泡、动作、语音、记忆和同步 | 返回结构化结果/通用错误 | 不把 GUI 对象或完整会话传给 Worker |

**运行位置不是交付归属。**本表中的识屏专属策略、设置/UI、adapter、记忆和 fallback 以后随屏幕理解功能包交付；Core 保留通用配置/授权、展示和进程宿主。当前内置拆边界不是物理卸载完成。包归属以 [功能总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md) 为准。

`PluginContext`/Phase 2 不新增第三方权限。capabilities 握手是官方实现的能力合同，
不是 OS 沙箱；同用户进程仍具有该用户权限。真正不可信插件不在本轮支持范围内。

## 2. 控制协议与业务请求

`pet-worker/v1` 保留 Phase 3A 的 hello/ready/config_push/event/error/heartbeat/shutdown，
增加 `request` 和 `response`，二者必须携带非空 request_id。
Agent Link 继续使用 `agent-event/v1` 语义，不借用识屏 RPC，也不改变其 event 结构。

请求 payload：`operation`、`generation`、`arguments`。
响应 payload：相同 operation/generation、`status`、`result` 或
`error_code/message/retryable`。未知操作、超限行、非法 JSON、过期代数被拒绝或诊断。

| 操作 | 发起方 | 作用 |
|---|---|---|
| observe_foreground | Core | 查询窗口，不截图 |
| capture_foreground | Core | 按预期 hwnd/pid 截图，返回 frame_id/dHash/窗口元数据 |
| analyze_frame | Core | 消费指定内存帧，按本次 Provider 请求视觉模型 |
| manual_look | Core | 用户主动触发全屏截图与视觉请求 |
| release_frame | Core | 丢弃未变化、不应分析的帧 |
| cancel | Core | 协作式取消指定请求或本代任务 |
| budget_check | Worker | 每次自动 HTTP 尝试前，向 Core 申请额度 |

区分两种代数：Supervisor generation 区分进程 incarnation；Watcher generation
区分配置/可见性/请求策略周期。不能拿进程代数替代业务代数。
反向 budget_check 必须关联仍 pending 的自动 analyze_frame，且匹配请求代数；
手动请求不消耗自动每日额度。

## 3. 自动识屏数据流

1. Core 先检查开关、可见性、交互、鼠标穿透与在飞任务，再请求 observe。
2. Core 判断白名单、dwell、idle、Agent Link 状态和 limiter，不符合时不截图。
3. Worker 在截图前后核对同一 hwnd/pid，窗口切换/消失即拒绝，避免白名单检查与截图错位。
4. Worker 返回 dHash + 不透明 frame_id；图像不通过 IPC 回传，也不落盘。
5. Core 比较 dHash；未变化 release_frame，变化时才发送单次分析授权。
6. Worker 每次实际 HTTP 尝试都 budget_check；Core 重新检查当前策略并记账。
7. Core 收到结果后再验证 request_id/generation/当前策略，只展示仍有效的结果。

新观察不能被上次已经不在白名单的窗口状态永久阻止；取消自动任务不能取消手动任务。
旧帧与过期结果不能跨 Worker 重启/配置切换使用。

## 4. 手动“看看屏幕”与多窗口

保留 `PetWindow.look_at_screen()`、`_on_look_screen()`、`_on_look_done()`。
忙碌提示、4 秒冷却、成功/错误气泡和 `on_look_synced` 仍由原窗口处理。
手动请求不要求自动识屏开关开启；手动操作本身表示这次截图授权。

普通窗口使用自己的 adapter；shared subsystem 复用一个 SharedProactiveWatcher 和
一个 Worker。自动展示仍沿用 MultiWindowProxy；手动结果仅回到发起窗口的回调，
其后的同步沿用既有机制。一窗关闭取消自身手动请求，不关停其他窗的共享服务。

仅手动使用时按需启动，任务结束且自动功能未开启后停止 Worker，不常驻后台。
Worker 忙碌/关闭中不再同时发起 legacy HTTP，避免重复截图和重复费用。

## 5. 密钥、截图与日志

- config_push 只接受 max_edge/jpeg_quality/frame_ttl/platform；Supervisor 附加进程代数。
- Core 从安全存储解析凭据，只发送选定视觉端点/模型/参数/凭据。
- 独立视觉 Provider 不夹带聊天 Key、keyring 引用、完整 Provider 或 Config.data。
- API Key 只用于单次 analyze_frame/manual_look；pending 表不保存请求正文。
- 完成、失败、取消或超时释放可控引用；Python 字符串不保证密码学擦除，
  不可中断的底层网络调用结束前仍可能持有临时引用，不声称物理内存已经清零。
- Worker 不写截图文件、不记录请求正文；错误使用有限的通用消息，回复中的实际凭据做脱敏。
- 复用 vision 请求与原有代理策略，不通过修改系统代理绕过联网问题。
- 记忆上下文沿用既有 Core 策略，仅发送本次有限上下文；不传整个聊天库。

## 6. 上限、生命周期、取消和回退

| 对象 | 上限/行为 |
|---|---|
| JSONL 单行 | 64 KiB |
| Worker 命令队列 | 64 条 |
| 自动执行任务 | 1 个 |
| 手动执行任务 | 1 个 |
| 自动待分析帧 | 1 张 + TTL；frame_id/generation 校验 |
| Adapter pending | 16 个；控制请求 15 秒、视觉请求 240 秒超时 |
| 网络期间 | 控制循环/heartbeat 继续，手动与自动互不占用任务槽 |
| cancel | 协作式，不能承诺立即打断 urllib；过期结果不展示 |
| 关闭 | shutdown → 2 秒期限 → terminate → kill；不阻塞 GUI wait |

Supervisor 复用 Phase 3A 有限重启/退避。ready/RPC 仅在合法状态接受；停止中的
迟到 ready 不能复活服务。Core 的 aboutToQuit 先取消生产者，主事件循环返回后
用最多 4 秒的局部 Qt 事件循环完成已发起的异步收口，避免 QProcess 仍活着就析构。

内部模式（代码注入，不新增设置页选项）：

- auto：产品宿主优先 Worker；终止性故障后回退旧进程内路径。
- in_process：回归/紧急回滚入口；保留旧实现。
- disabled：禁用这个执行通道；不截图、不发视觉请求。

Watcher 构造默认仍 in_process，以兼容旧调用方；正式窗口宿主和 shared 宿主显式
传 auto。不把内部模式伪装成已经发布的用户设置。

后续选装必须在这些模式之前检查安装、启用和授权状态；auto fallback 只能服务于仍获授权的已安装功能。卸载或停用不能被旧进程内路径绕过。这是 Phase 4A/4B 新验收项，不是当前已具备物理卸载状态的声明。

## 7. 文件边界与复建顺序

```text
pet/
  proactive.py                       主进程功能策略/limiter/记忆/旧 fallback
  window.py                          手动 UI 与结果展示
  window_optional_services.py        懒创建官方 Worker watcher
  multi_window_shared.py             进程级共享与停止
  app.py                             退出编排
  chat/__init__.py                    ChatService 懒导出，避免模型导入拖入 Qt
  workers/
    protocol.py                      request/response 增量协议
    supervisor.py                    QProcess/能力握手/状态与退出
    worker_entry.py                  官方 allowlist
    proactive_screen_adapter.py      主进程功能 RPC、路由、凭据筛选、超时
    proactive_screen_worker.py       无 Qt 执行、帧、网络、反向额度
scripts/verify_phase3b_frozen_worker.py
```

重新开始时按此顺序恢复，不一次性重写 proactive.py：

1. 固定 Phase 3A 回归；先加协议/状态红测，再实现 request/response。
2. 实现 Worker，先测无 Qt/keyring、帧 TTL、秘密边界和网络期间心跳。
3. 实现 adapter 与反向预算；先测拒绝/超时/取消/stale 响应。
4. 接 Core 自动策略，再接手动公共入口与 shared 路由。
5. 测真实 Core 退出、冻结程序、实际桌面和有界性能。
6. 全量测试、静态检查、报告后才可封存；失败暂停受影响的执行迁移并保留授权范围内的 fallback。官方选装的依赖审计可并行，但不能以故障路径交付样板。

## 8. 验证门与当前证据入口

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$workerTests = (Get-ChildItem tests/test_proactive_worker_*.py).FullName
python -m pytest -q @workerTests tests/test_proactive_watcher_worker.py
python -m pytest -q tests/test_workers_protocol.py tests/test_workers_lifecycle.py tests/test_workers_source.py tests/test_agent_link_worker_integration.py
python -m pytest -q
python -m ruff check pet tests scripts
python -m ruff format --check pet tests scripts
python -m mypy pet/workers pet/proactive.py
python scripts/check_docs.py
python scripts/verify_phase3b_frozen_worker.py --source
python scripts/verify_phase3b_frozen_worker.py dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe
```

PowerShell 不一定为原生程序展开文件通配符：专项 pytest 实际执行时使用报告中
显式文件清单，或先通过 Get-ChildItem 获取文件路径数组；不能把“收集不到文件”当通过。

真实进程测试只替换 OS 截图/前台查询边界和本地 HTTP 服务，不用纯 mock 代替 QProcess。
网络实测不能使用用户密钥或付费 Provider 自动发请求；本机受控 HTTP 只证明传输/重试行为，
不冒充真实远程模型效果。可见桌面/托盘人工行为若未验证必须在报告列为待验收。

## 9. 给使用者的最终效果

- 界面基本不变，不多一个需要手动操作的 Worker 窗口。
- Worker 是独立进程，但仍由 Core 通过本机管道管理，不是完全断开的另一个桌宠。
- 自动识屏关闭后不再自动观察/截图；手动“看看屏幕”仍可按需启动一次 Worker。
  要完全不识屏，就关闭自动识屏且不使用手动入口；内部 disabled 可以同时禁用两者。
- 它目前随主安装包提供，不是可复制到 content 目录安装的 DLC，没有单独的安装/卸载 UI。
- 角色 DLC 目录不接收可执行 Worker。官方屏幕理解选装在 Phase 4/5 必须完成，不等待 Phase 6 第三方生态；包格式、可信加载、依赖形态和安装目录先在 Phase 4A/5A 验证，不能直接删除本轮源码来“卸载”。
- 未来屏幕理解包包含自动/手动入口、专属策略/UI/适配器和 Worker。菜单、设置和快捷键按包注册与撤销；停用不执行，应用内卸载默认保留偏好，重装可恢复。当前这些安装体验尚未实现。
- 屏幕理解可不安装 AI 对话，自行配置视觉服务；同步聊天是可选联动。AI 对话与文件理解是样板之后的下一项主要拆包目标，聊天安装不自动授予截图权限。
- Worker 崩溃时 Core 保持可用并有限重启/回退；这不等于对恶意插件的安全沙箱。
