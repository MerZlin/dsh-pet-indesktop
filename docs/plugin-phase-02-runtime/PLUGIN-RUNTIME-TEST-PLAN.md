# Phase 2：Core 插件运行时测试计划

> **修订基线：2026-09-25；状态：已完成基线，保留为 API 冻结验收门**

## 1. 测试原则

按行为而不是按实现文件组织：Core、Content、官方插件、配置边界和 Worker 宿主分别验证。Qt 对象使用真实 event loop；时序用 `Event`、`Condition`、`QSignalSpy` 或带宽松上限的轮询；不新增固定 `sleep` 猜状态。

Phase 2 不用覆盖率数字替代边界测试，尤其关注离线启动、插件失败、停用清理和迁移恢复。

## 2. 测试矩阵

| 类别 | 关键行为 | 方式 | 门 |
|---|---|---|---|
| Core 基础 | 无可选插件/无插件目录仍启动、显示、退出 | Qt offscreen + 启动回归 | 阻塞 |
| Registry | manifest 缺失/损坏、重复 ID、版本/依赖环只生成诊断 | 单元 + registry 集成 | 阻塞 |
| 生命周期 | 依赖顺序启动、逆序停止、启动/停止异常隔离 | fake official plugin + event loop | 阻塞 |
| EventBus | JSON payload、订阅、回调异常、owner 清理 | 单元 | 阻塞 |
| 配置隔离 | 只读自己的 namespace、legacy 双向适配、不覆盖 Core | tmp 配置 + migration fixture | 阻塞 |
| 迁移恢复 | 重复执行、中断、坏输入、备份恢复、multi-instance | 临时目录 + 故障注入 | 阻塞 |
| Festival | 提醒、立即提醒、语音让位、配置热更新、停止无残留 | Qt integration | 阻塞 |
| Content | Starter DLC 发现、provider resolve、失效时 fallback | Phase 1 回归 | 阻塞 |
| Worker boundary | 只推送授权配置摘要，不泄露 keyring/全局对象 | fake worker / protocol test | 阻塞 |
| 更新保护 | updater 文件无非预期变化 | diff guard | 阻塞 |
| 可见桌面 | 菜单、托盘、气泡和退出用户行为 | Windows 实机 | 发布前 |

## 3. 必测故障路径

- 插件入口不存在或 factory 抛异常，Core 仍可启动。
- 插件回调抛异常后订阅被取消，其他插件继续收到事件。
- 停止插件后 QTimer、事件订阅、命令和对象引用全部清理。
- 配置迁移中断后恢复旧备份，重新执行不会重复破坏数据。
- 关闭节日提醒插件后，桌宠基础点击、移动、角色切换不变。
- Content provider 失效时仍能加载 Starter DLC/legacy/fallback。
- Core 与 Worker 的控制协议、业务事件语义和配置摘要边界不会混淆。

## 4. 验证命令

聚焦回归：

```text
python -m pytest -q tests/test_plugin_runtime.py tests/test_festival.py tests/test_voice_chime_service.py
python -m pytest -q tests/test_content_dlc.py tests/test_app_startup_fallback.py
```

最终回归：

```text
python -m pytest -q
python -m ruff check pet tests
python -m ruff format --check pet tests
```

实际命令以仓库当前测试文件为准；若某个平台无法创建 Qt/IPC 资源，必须记录环境原因，不得静默跳过。

## 5. 性能和实机证据

记录插件发现耗时、启动耗时、常驻 RSS 增量、定时器数量和停止清理时间。离线启动必须独立记录；可见桌面测试不能用 offscreen 结果代替。Core 自动更新文件用 diff guard 检查，Phase 2 不修改更新协议。

## 6. Phase 2 验收门

1. Core 无可选插件可离线启动。
2. Phase 1 Starter DLC 仍可发现、加载和 fallback。
3. Registry 能发现、启停并诊断官方插件。
4. Context 不暴露 UI 私有实现或全局配置。
5. 至少一个官方 in-process 插件完成真实迁移。
6. 插件故障不阻塞 Core，停用无资源残留。
7. 配置 namespace、legacy、备份和恢复测试通过。
8. Worker 只收到授权配置摘要。
9. `pet/updater.py` / `pet/update_settings.py` 无非预期变化。
10. 进入 Phase 3 前保留可回滚的 Phase 2 checkpoint。

## 7. 失败重启点

```text
保留 Phase 1
→ 禁用 official.festival-reminder
→ 恢复最小 PluginContext / Config / EventBus
→ 先通过 Core 离线、Content fallback 和配置迁移测试
→ 再重新启用官方插件
```
