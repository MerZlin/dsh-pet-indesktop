# Phase 2：运行时回归与可选交付扩展测试计划

> 修订：2026-09-27。运行时基线已有自动化证据；下表的后续门是计划，不宣称物理选装已通过。

## 1. 现有基线回归

| 测试族 | 负责行为 | 验证入口 |
|---|---|---|
| Registry | allowlist、manifest/API/Core 兼容、重复 ID、依赖顺序与反向停止 | `tests/test_plugin_runtime.py` |
| Context/能力 | 受限端口、未声明能力拒绝、配置命名空间、不泄露全局配置 | `tests/test_plugin_runtime.py` |
| 事件与任务 | JSON payload、GUI 线程、回调错误隔离、停用后事件/命令/定时器清理 | `tests/test_plugin_runtime.py` |
| 官方节日提醒 | 提醒、配置兼容、语音让位、关闭后不再执行 | `tests/test_festival.py`、`tests/test_voice_chime_service.py` |
| 资源与主窗口 | Phase 1 Registry/fallback、可选功能关闭不影响基本运行 | `tests/test_content_dlc.py`、相关启动测试 |

文档引用已有报告只代表当时的证据。改动后按风险重新跑实际命令，不能拿过去的 passed 数宣称本轮全量测试通过。

## 2. 新增选装合同的验收门（Phase 4/5 落地）

| 门 | 行为与证据 | 当前定位 |
|---|---|---|
| 无功能包 Core | 离线启动；基础窗口、角色、拖拽、托盘正常；构建产物不含目标功能专属代码/依赖 | 必须新增物理产物验收，不等于关闭开关 |
| 依赖方向 | Content 禁止代码；Feature/Worker 不导入 Core UI 私有实现；功能自身 UI 可以在 GUI 主进程运行 | 需要导入边界及受控加载测试 |
| 配置迁移 | 备份、验证、中断恢复、幂等、legacy 保留、实例隔离 | 完整拆包迁移不能用旧字段适配测试代替 |
| UI 贡献 | 未安装无菜单/设置/搜索/快捷键；安装启用后出现；停用不执行；卸载按 owner 撤销 | 现有命令注册只是基础 |
| 设置无副作用 | 仅打开设置不启动截图、网络、轮询或 Worker | 必须使用实际生命周期观察 |
| 卸载与重装 | 停止任务/Worker/UI，撤销订阅和命令，移除文件；默认留数据；重装恢复偏好 | 多实例、占用文件和待重启状态都要覆盖 |
| fallback | 仅已安装、启用、授权的功能可降级；卸载后不能调用 Core 中的隐藏实现 | 官方样板硬门 |
| Worker 授权 | 非敏感 config_push；临时单次请求凭据；无 keyring/全局配置访问；日志不含秘密 | 对齐 Phase 3B，不将“禁止密钥快照”误写为禁止合法临时授权 |
| 更新保护 | Core 更新不装回已卸载功能；DLC 不覆盖 Core 或用户数据；回滚兼容 | 由更新事务与安装状态测试验证 |

状态合同以 [API 合同](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md) 为准，迁移以 [迁移计划](../plugin-phase-01-foundation/PLUGIN-MIGRATION-v4-to-v5.md) 为准，不在测试文档另造规则。

## 3. Qt 与故障隔离纪律

- GUI 相关族在同一进程复用 `QApplication`，不能先创建 `QCoreApplication` 再尝试创建 GUI 应用。
- Qt 对象归属线程不变；时序用事件/信号或带宽松预算的轮询，不新增固定 sleep 猜状态。
- Worker 使用真实子进程验证退出、崩溃、超时和 generation；只 mock 无法确定性运行的 OS/网络边界。
- 注入插件启动、停止、回调和贡献注册异常，确认其他插件与 Core 继续工作。
- 明确区分运行停用、解释器模块驻留和物理卸载，不把 Qt UI 隐藏当成服务退出。

## 4. 验证入口与报告

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_plugin_runtime.py tests/test_festival.py tests/test_voice_chime_service.py tests/test_content_dlc.py
python -m pytest -q
python -m ruff check pet tests
python -m ruff format --check pet tests
```

上面是实现阶段命令，不表示本次纯文档修订执行了运行时全量回归。涉及生命周期/配置/打包时须跑全量，并记录环境、样本、耗时、RSS、定时器/进程和退出行为；人工桌面与跨平台记录不能用 offscreen 代替。

当前纯文档修订执行链接、PR 报告纪律和 diff 检查。新门落地后应增加对应测试入口，而非修改表格就认定完成。

## 5. 重启点与给使用者的效果说明

运行时失败时保留 Phase 1，禁用官方进程内功能，从 Context/EventBus/Config 最小边界重新验证。未来功能包的 UI、Worker 和适配器共同随包管理，Core 提供通用连接；具体安装目录由 Phase 4A/5A 确定。你应该能够看到“没装就没有入口，装好才出现，卸载后停止并移除”，重装仍保留之前的偏好。以上体验属于后续目标，不是当前 Phase 2 已经提供的安装器。

相关：[运行时设计](PLUGIN-RUNTIME-DESIGN.md) · [阶段入口](README.md) · [功能归属总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)
