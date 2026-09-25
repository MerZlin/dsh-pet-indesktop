# Phase 3：进程插件研究与实施基线

> **状态：Phase 3A 已有实现，稳定性封存前（2026-09-25）**
>
> 本文不再把 Phase 3 描述为纯研究。当前实现集中在 Agent Link 的“事件采集层”，不是把完整 `AgentLinkManager`、气泡、动作、对话、成本展示或 DSH bridge 全部移出主进程。

## 1. 为什么先迁移事件采集

`pet/agent_link.py` 同时包含日志 tail、协议解析、事件规范化、Qt 定时器、桥接管理、气泡、动作、对话和成本展示。一次性拆出整个类风险过高。当前拆分只把文件读取、原始记录解析和 `agent-event/v1` 语义转换放进 Worker；Core 继续掌握用户可见策略和兼容入口。

主动识屏包含截图、前台窗口、dHash、视觉模型请求和限流策略，也不作为首个 Worker，必须等待 3A 稳定后再拆。

## 2. 两个协议层

### `pet-worker/v1`：Worker 控制协议

每行是一条 UTF-8 JSON：

```json
{
  "protocol": "pet-worker/v1",
  "worker_id": "agent-link-events",
  "type": "hello",
  "request_id": "optional-id",
  "timestamp": "2026-09-25T00:00:00Z",
  "payload": {}
}
```

支持 `hello`、`ready`、`config_push`、`event`、`error`、`heartbeat`、`shutdown`。Core 通过异步 `QProcess` 的 stdout/stderr 和生命周期信号处理，不在 GUI 线程阻塞 `wait()`。

默认约束：握手 5 秒、心跳 5 秒、15 秒失联判定、优雅关闭等待 2 秒；60 秒内最多自动重启 3 次，退避 0.5/1/2 秒。每次重启增加 generation，旧 Worker 的迟到事件必须丢弃。

### `agent-event/v1`：Agent 业务事件语义

该层定义规范化 Agent 事件的字段和语义。它不规定 DSH bridge 的文件 tail、WebSocket、外部工具、安装或配置修改方式。不要把 bridge 的全部传输细节提升为通用 Worker 协议。

## 3. 当前实现范围

已具备：

- Worker 入口分流，正常 `python -m pet`、`--settings` 和 `--uninstall-cleanup` 不改变。
- Core 异步进程宿主和状态诊断。
- hello/ready、配置摘要、heartbeat、event、error、shutdown。
- Agent Link source：目录扫描、文件偏移、轮转/截断、边界解析、事件规范化。
- Core 适配器：校验 Worker ID、协议和 generation 后转给现有 Agent Link 处理入口。
- 崩溃检测、有限重启、fallback 到旧 in-process source。
- 协议和真实子进程测试。

## 4. 明确保留在 Core

- `AgentLinkManager` 公共入口和设置兼容。
- 气泡、动画、对话、成本和用户通知。
- Qt 定时器、展示策略、prompt 交互和 DSH bridge 安装/卸载。
- 用户开关、降级判断和 Core 状态权威。

Worker 只读取允许的事件来源，不访问 `Config.data`、`PetApp`、`PetWindow`、keyring 或 secret。

## 5. Phase 3A 待完成清单

- [ ] frozen PyInstaller executable 能启动 Worker 并完成 smoke。
- [ ] Core 退出、异常退出、terminate/kill 路径有父子进程树证据。
- [ ] 队列上限、事件洪峰和背压策略冻结。
- [ ] stale generation、重启后重复消费和 fallback 不重复监听通过长时测试。
- [ ] 记录 RSS、CPU、事件延迟、重启耗时、文件读取频率和日志增长。
- [ ] Windows 实机验证后形成独立 Phase 3A 封存记录。

## 6. Phase 3B：主动识屏

进入条件：3A 连续运行稳定、无重复事件和 stale generation 问题、退出清理和 Windows 实机记录完整、协议不再频繁变更。

拆分边界：

```text
Core：开关、白名单、dwell、limiter、用户确认、记忆、展示
Worker：前台窗口、截图、dHash、视觉请求、网络响应解析
```

CI 使用 fake worker 和 mock 网络边界，不依赖真实截图、模型服务或用户桌面。

## 7. Phase 3C：逐项而不是整批迁移

余额、歌词、文件解释、外部播放器和 Harness 的后续决定必须记录：

- 主进程是否被网络或外部程序阻塞；
- 是否含密钥、权限或用户确认；
- Worker 常驻成本和启动频率；
- Core/Worker 之间的数据契约是否稳定；
- 真实故障是否能被隔离和诊断。

## 8. 测试与验收

协议测试覆盖缺字段、坏 JSON、未知类型、版本不兼容、超大消息、不可序列化 payload、事件语义和旧 generation。进程测试覆盖启动、握手、配置、心跳、超时、关闭、崩溃、重启、fault、非法输出、EOF 和 Core 退出。

Phase 3A 出口：

1. Worker 可承担 Agent Link 事件采集。
2. Core 不因 Worker 崩溃退出，并可限次重启或 fallback。
3. 控制协议和业务事件语义分离。
4. 正常入口、设置入口、清理入口和自动更新文件不受影响。
5. frozen smoke、父子清理、背压和长时基线完成。
6. 旧 Agent Link 行为无新增失败，变更可单独回滚。

## 9. 回滚

运行模式切回 `in_process`，停止 Worker，保留旧 source，清理 generation 和 Worker 诊断。协议、宿主、Agent Link source 和 Core 适配分开提交；不使用 reset 覆盖用户改动，不删除旧监视代码直到稳定周期结束。
