# Phase 2 Core 插件运行时测试计划

> **规划基线**：2026-09-24
> **当前状态**：runtime 专项测试、聚焦回归、全量门禁、性能探针和 Windows offscreen AppShell smoke 已完成；真实可见桌面与跨平台验收仍待发布前补齐

## 1. 测试原则

- 先在公开 seam 上写测试，再实现 runtime；
- Qt 对象始终在 GUI 线程创建和销毁；Phase 2 事件总线不跨线程；
- 运行时单元测试使用可控 scheduler，生命周期测试使用真实 Qt event loop；
- 不使用固定 `sleep` 猜测时序，使用事件同步、状态轮询或宽预算；
- headless 环境统一设置 `QT_QPA_PLATFORM=offscreen`；
- 共享配置、AppShell 生命周期、Qt 定时器和现有节日/语音服务完成后跑全量测试；
- 所有失败诊断必须区分产品失败、平台限制和测试环境时序抖动。

## 2. 当前测试文件和目标拆分

| 测试文件 | 当前责任/状态 |
|---|---|
| `tests/test_plugin_runtime.py` | 已创建；覆盖 Registry、manifest/API/Core 兼容诊断、重复 ID、依赖排序/环/失败、状态、启停异常隔离、EventBus、配置、capability 和官方节日插件清理。 |
| `tests/test_plugin_events.py` | 目标拆分文件；当前覆盖合并在 `test_plugin_runtime.py`。 |
| `tests/test_plugin_config.py` | 目标拆分文件；当前覆盖合并在 `test_plugin_runtime.py`。 |
| `tests/test_plugin_capabilities.py` | 目标拆分文件；当前覆盖合并在 `test_plugin_runtime.py`。 |
| `tests/test_festival_plugin.py` | 目标拆分文件；当前官方插件端口测试合并在 `test_plugin_runtime.py`，行为回归由现有节日测试覆盖。 |
| 现有节日/启动测试 | `tests/test_festival.py`、`tests/test_voice_chime_service.py`、`tests/test_app_startup_fallback.py` 已作为回归门。 |

Phase 2 不为了追求文件数量而机械拆测试；只要覆盖面和诊断粒度保持，后续可在 Phase 3 前按领域拆分。

## 3. Runtime 基础设施矩阵

| 场景 | 预期结果 | 当前状态 |
|---|---|---|
| 无可选插件 | Core 正常启动和退出 | Windows offscreen AppShell smoke 和全量回归通过。 |
| manifest 缺失或损坏 | 只生成诊断，不阻塞 Core | Registry 诊断测试已覆盖。 |
| API/Core 不兼容 | 插件拒绝启动 | Registry 兼容性测试已覆盖。 |
| 重复插件 ID | 新定义拒绝并诊断 | Registry 重复 ID 测试已覆盖。 |
| 缺失依赖 | 依赖方 fault，不阻塞无关插件 | 已有回归测试。 |
| 依赖环 | 环内插件拒绝启动 | Registry 依赖环测试已覆盖。 |
| 正常依赖图 | 拓扑序启动、逆序停止 | 已有回归测试。 |
| 插件 `start()` 异常 | 插件 fault，Core 继续 | Registry 异常隔离测试已覆盖。 |
| 插件 `stop()` 异常 | 继续停止其他插件 | Registry 异常隔离测试已覆盖。 |
| 事件回调异常 | 取消订阅并 fault owner | 已有回归测试。 |
| 停用插件 | 订阅、任务、命令全部清理 | 已有官方插件回归测试。 |
| 非 JSON payload | 发布被拒绝 | 已有回归测试。 |

## 4. EventBus 测试

必须覆盖：

- `CoreEvent.timestamp` 由 Core 生成且带时区；
- scoped subscription 的 owner 固定为插件 ID；
- 同步发布使用订阅快照，回调修改订阅不会破坏当前迭代；
- 取消订阅后不再收到事件；
- 按 owner 批量取消只影响对应插件；
- 回调异常记录诊断、移除订阅并 fault owner；
- payload 经 JSON 序列化检查，错误不会进入其他订阅者；
- `core.app.started`、`core.app.shutdown_requested`、`core.config.changed` 和 `plugin.lifecycle.changed` 已在 AppShell/Registry 路径接入；`pet.character.changed`、`pet.window.clicked` 保留为后续 Core 事件接入点。

## 5. 配置隔离测试

- 一个插件不能读取另一个插件的命名空间；
- `plugins.<plugin_id>.settings` 优先于旧扁平字段；
- 旧 `festival_reminder_*` 字段可以迁移到新命名空间；
- 迁移后旧字段和原始值保留在 `legacy`；
- 设置页继续写旧字段时，插件能通过 `core.config.changed` 收到更新；
- 插件写入不能覆盖 Core 配置、其他插件配置或 secrets；
- `Config.reload()` 保留 `plugins` 命名空间；
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
- `tests/test_app_startup_fallback.py` 及相关 AppShell 启动、单进程和菜单测试；
- `tests/test_content_dlc.py` 及 Phase 1 全量回归；
- `pet/updater.py` / `pet/update_settings.py` 相关自动更新测试。

## 9. 性能和真实桌面验收

实现阶段必须在同一台真实开发机记录：

| 指标 | 测量方式 | 结果记录位置 |
|---|---|---|
| 插件发现耗时 | Registry 创建到 `discover()` 完成，至少 100 次样本 | Phase 2 PR 报告 |
| 插件启动耗时 | `start_all()` 完成，至少 100 次样本，并与空 Registry 对照 | Phase 2 PR 报告 |
| 常驻内存增量 | `tracemalloc`/进程 RSS，说明采样方法和局限 | Phase 2 PR 报告 |
| 定时器数量 | 按 owner 统计，停用后插件计数归零 | Phase 2 PR 报告 |
| 首次提醒延迟 | `remind_now` 到 Presentation port 调用 | Phase 2 PR 报告 |

真实桌面记录至少包含：启动、看到桌宠、启用提醒、立即提醒、停用提醒、关闭应用，以及关闭后没有残留 Python/Qt 子任务的确认。不能自动验证的行为必须给出探针输出和人工观察说明，不以单元测试替代。

## 10. Phase 2 验收门

只有以下条件全部满足才算正式封板：

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

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_plugin_runtime.py tests/test_festival.py tests/test_voice_chime_service.py tests/test_app_startup_fallback.py
python -m pytest -q tests/test_content_dlc.py tests/test_festival.py tests/test_voice_chime_service.py
python -m pytest -q
python -m ruff check pet/plugins tests/test_plugin_runtime.py pet/app.py pet/config.py pet/festival_service.py
python -m py_compile pet/plugins/*.py pet/plugins/builtin/festival_reminder/*.py
```

如果某个 Qt/平台族在本机受显示器或 socket 限制，必须保留实际输出、隔离族和重跑说明，不能用“测试过了”替代证据。
