# Phase 2 Core 插件运行时测试计划

> **规划基线**：2026-09-24
> **实现状态**：测试矩阵和验收门已定义，测试文件尚未全部创建

## 1. 测试原则

- 先在公开 seam 上写失败测试，再实现 runtime；
- Qt 对象始终在 GUI 线程创建和销毁；Phase 2 事件总线不跨线程；
- 使用真实 Qt event loop 验证调度、窗口展示和退出顺序，不用 mock 掩盖生命周期问题；
- 不使用固定 `sleep` 猜测时序，使用 `QSignalSpy`、`Event`、`Condition` 或轮询状态加宽预算；
- headless 环境统一设置 `QT_QPA_PLATFORM=offscreen`；
- 运行时共享配置、AppShell 生命周期、Qt 定时器和现有节日/语音服务，完成后跑全量测试；
- 所有失败诊断必须区分产品失败、平台限制和测试环境时序抖动。

## 2. 计划测试文件

| 测试文件 | 责任 |
|---|---|
| `tests/test_plugin_runtime.py` | Registry 发现、依赖排序、状态、启停、诊断和故障隔离。 |
| `tests/test_plugin_events.py` | EventBus payload、订阅 owner、异常取消和生命周期事件。 |
| `tests/test_plugin_config.py` | 配置命名空间、旧扁平字段迁移、legacy 保留和隔离。 |
| `tests/test_plugin_capabilities.py` | capability allow/deny、越权诊断和端口调用边界。 |
| `tests/test_festival_plugin.py` | 官方节日提醒插件的启动、停用、提醒、语音和配置更新。 |
| 现有节日/启动测试 | `tests/test_festival.py`、`tests/test_voice_chime_service.py`、启动 fallback 和菜单兼容回归。 |

以上是目标测试布局；实现阶段可以按现有测试组织调整，但不得减少覆盖面。

## 3. Runtime 基础设施矩阵

| 场景 | 预期结果 | 验证重点 |
|---|---|---|
| 无插件目录/无可选插件 | Core 正常启动和退出 | 主窗口、托盘、Phase 1 Starter DLC 不受影响 |
| manifest 缺失或损坏 | 只生成诊断，不阻塞 Core | `failure_stage=manifest` |
| API/Core/platform 不兼容 | 插件拒绝启动 | 诊断含兼容范围和当前版本 |
| 重复插件 ID | 冲突双方均不可启动 | 不隐式选择枚举顺序 |
| 缺失依赖 | 依赖方禁用 | 无连锁阻塞无关插件 |
| 依赖环 | 环内插件拒绝启动 | 记录环成员和拓扑失败原因 |
| 正常依赖图 | 拓扑序启动、逆序停止 | 记录调用顺序 |
| 插件 `start()` 异常 | 插件 fault，Core 继续 | 主窗口仍可交互 |
| 插件 `stop()` 异常 | 继续停止其他插件和 Core | 退出不被单点异常卡住 |
| 事件回调异常 | 取消该订阅并 fault owner | 异常不冒泡到 Qt 主循环 |
| 停用插件 | 订阅、任务、命令全部清理 | 诊断残留数为零 |
| payload 含 QObject/bytes 等非 JSON 值 | 发布被拒绝 | 发布者收到结构化错误 |

## 4. EventBus 测试

必须覆盖：

- `CoreEvent.timestamp` 由 Core 生成且带时区；
- `source` 不能被插件伪造为其他 owner；
- 同步发布按订阅顺序执行，回调修改订阅不会破坏当前迭代；
- 取消订阅后不再收到事件；
- 按 owner 批量取消只影响对应插件；
- 回调异常记录 `plugin.error`/诊断并自动移除订阅；
- payload 经 JSON 序列化检查，错误不会进入其他订阅者；
- `core.app.started`、`core.app.shutdown_requested`、`core.config.changed`、`pet.character.changed`、`pet.window.clicked` 和 `plugin.lifecycle.changed` 均有最小 schema 测试。

## 5. 配置隔离测试

- 一个插件不能读取另一个插件的命名空间；
- `plugins.<plugin_id>.settings` 优先于旧扁平字段；
- 旧 `festival_reminder_*` 字段可以迁移到新命名空间；
- 迁移后旧字段和原始值保留在 `legacy`；
- 迁移异常时 Core 不退出，插件使用默认值并保留错误诊断；
- 设置页继续写旧字段时，插件能收到 `core.config.changed`；
- 插件写入不能覆盖 Core 配置、其他插件配置或 secrets；
- 多实例 `config-slot-N.json` 隔离语义保持不变；
- 配置热更新不重复创建不必要的任务和事件订阅。

## 6. Capability 与服务端口测试

- 已声明 capability 的端口调用成功；
- 未声明 `network.request`、`screenshot.capture`、`process.spawn`、`secret.read` 时调用被拒绝；
- `show_bubble()`、`notify()`、`speak()` 不要求插件持有窗口或 AppShell 引用；
- 调度任务自动带 owner，停用后全部取消；
- command 注册冲突、重复注销和 owner 清理可诊断；
- content provider 只代理 Phase 1 Registry，不产生第二套角色优先级。

## 7. 官方节日提醒插件测试

### 生命周期

- 启用后创建 30 秒调度任务并能正常提醒；
- 停用后不再触发提醒；
- 启动/退出过程中不残留 QTimer、订阅、命令或插件引用；
- 启动失败时桌宠仍可显示和交互；
- 重复 enable/start 不重复创建服务。

### 行为兼容

- `remind_now` 不受总开关影响；
- 语音关闭时只展示文字；
- 气泡不可用时降级 `notification.present`；
- Voice Chime 与节日提醒的音频时隙让位保持现有测试语义；
- 重复提醒抑制、启动补提醒和日历/节气配置行为保持不变；
- 配置热更新不重置不必要的调度状态；
- 旧菜单入口和设置入口仍通过兼容 facade 生效。

### 故障注入

- presentation port 抛错时只记录插件诊断；
- scheduler 创建失败时插件 fault，Core 继续；
- 配置迁移失败时保留 legacy 并使用安全默认值；
- capability 被撤销时调用被拒绝且插件不越权。

## 8. AppShell 集成和回归

按真实生命周期验证：

1. 构造 `AppShell`；
2. 注册 Phase 1 content provider；
3. 创建窗口、托盘和展示端口；
4. 启动插件并观察 `core.app.started`；
5. 触发设置变更、角色切换、点击和立即提醒；
6. 发布 `core.app.shutdown_requested`；
7. 逆序停止插件，再执行既有 IPC、窗口和更新退出流程。

必须保持通过的现有族：

- `tests/test_festival.py`；
- `tests/test_voice_chime_service.py`；
- 相关 `AppShell` 启动 fallback、单进程和菜单测试；
- `tests/test_content_dlc.py` 及 Phase 1 全量回归；
- `pet/updater.py` / `pet/update_settings.py` 相关自动更新测试。

## 9. 性能和真实桌面验收

实现阶段必须在同一台真实开发机记录：

| 指标 | 测量方式 | 通过条件 |
|---|---|---|
| 插件发现耗时 | 冷启动连续 10 次，记录从 Registry 创建到 discover 完成的毫秒数 | 记录均值、P95，不得以形容词代替数字 |
| 插件启动耗时 | 连续 10 次记录 `start_all()` 完成时间 | 与无插件基线对比并解释增量 |
| 常驻内存增量 | 无插件/启用节日插件各稳定 60 秒后采样 5 次 | 记录工作集或进程 RSS，说明采样方法 |
| 定时器数量 | 运行中统计 Core/插件 owner 的 timer 数 | 记录稳定数量，停用后插件计数归零 |
| 首次提醒延迟 | 触发 `remind_now` 记录请求到展示/通知的时间 | 记录均值和最大值 |

真实桌面记录至少包含：启动、看到桌宠、启用提醒、立即提醒、停用提醒、关闭应用，以及关闭后没有残留 Python/Qt 子任务的确认。不能自动验证的行为必须给出探针输出和人工观察说明。

## 10. Phase 2 验收门

只有以下条件全部满足才算完成：

1. 无可选插件时 Core 可启动；
2. Phase 1 Starter DLC 仍能被发现和加载；
3. Registry 能发现、启停并输出诊断；
4. Context 不暴露 `PetApp`、`PetWindow` 私有字段或全局 `Config.data`；
5. `official.festival-reminder` 完成真实迁移；
6. 插件故障不阻塞 Core 和应用退出；
7. 配置命名空间、旧字段兼容和 secrets 边界通过测试；
8. 自动更新文件和协议未被修改；
9. 全量测试没有新增失败；
10. 完成发现/启动/内存/定时器性能记录；
11. 生成包含逐文件说明、性能实测和真实桌面记录的 Phase 2 PR 报告。

## 11. 建议验证命令

实现完成后至少执行：

```text
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_plugin_runtime.py tests/test_plugin_events.py tests/test_plugin_config.py tests/test_plugin_capabilities.py tests/test_festival_plugin.py
python -m pytest -q tests/test_content_dlc.py tests/test_festival.py tests/test_voice_chime_service.py
python -m pytest -q
python -m py_compile pet/plugins/*.py pet/plugins/builtin/festival_reminder/*.py
```

如果某个 Qt/平台族在本机受显示器或 socket 限制，必须保留实际输出、隔离族和重跑说明，不能用“测试过了”替代证据。
