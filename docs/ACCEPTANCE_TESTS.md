# 验收测试文件清单

仓库中不存在 `tests/test_modern_settings_dialog.py` 和 `tests/test_dsh_pet_bridge.py`。检查 Git 历史（包括所有本地及远程引用）后，未发现这两个路径曾被提交、重命名或拆分后保留原文件名。因此验收不创建同名空文件，而按实际测试边界执行下列文件。

## 设置窗口验收

现代设置窗口的测试已按功能拆分：

- `tests/test_settings_and_resources.py`：现代设置窗口基本控件与配置 round-trip；
- `tests/test_collision_settings.py`：现代设置窗口碰撞控件、持久化和运行时同步；
- `tests/test_settings_subslot_autostart.py`：子槽位下现代设置窗口的开机自启行为；
- `tests/test_desktop_pet_features.py`：现代设置侧栏、页面归属、表达风格及依赖控件；
- `tests/test_settings_event_gating.py`：设置窗口打开时的事件/提醒拦截边界。

验收命令（每个路径都由 pytest 收集并执行）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q `
  tests/test_settings_and_resources.py `
  tests/test_collision_settings.py `
  tests/test_settings_subslot_autostart.py `
  tests/test_desktop_pet_features.py `
  tests/test_settings_event_gating.py
```

## DSH Bridge 验收

Bridge 验收采用真实插件源码上的 Node contract 测试，并结合桌宠端真实事件解析/状态处理测试：

- `tests/test_bridge_control_contract.js`：读取并检查实际的 `integrations/dsh-pet-bridge/index.js`，验证控制结果、审批/问题身份字段和未知会话行为；
- `tests/test_agent_link.py`：真实 `AgentLinkManager`、tailer、事件映射、请求生命周期和交互队列；
- `tests/test_dsh_state.py`：真实 DSH JSONL 状态读取与状态转换。

验收命令：

```powershell
node --check integrations/dsh-pet-bridge/index.js
node --test tests/test_bridge_control_contract.js
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_agent_link.py tests/test_dsh_state.py
```

## Qt 生命周期与最终全量验收

Windows/offscreen 的最终验证必须分两步执行，避免高并发跨进程压力测试掩盖普通
Qt 生命周期问题：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q -k "not cross_process_concurrent_publish_read_stress"
python -m pytest -q tests/test_decode_broker_shm.py::test_cross_process_concurrent_publish_read_stress
```

第一步应先完整结束且无 native abort；第二步只能在第一步通过后运行。本轮实测结果：

- 主套件：`1421 passed, 7 skipped, 1 deselected`；
- 跨进程压力测试：`1 passed`。

生命周期重点覆盖 `PetWindow.closeEvent()` 的幂等关闭、外置
`PetSpeechBubble` 的 owner 清理、右键菜单执行 seam、AgentLink/WebM/session
后台资源 teardown。详细说明见
[`QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md`](QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md).
