# Phase 3：Worker 插件运行时

> **状态：Phase 3A 实施中，尚未完成稳定性封存（2026-09-25）**
>
> 当前仓库已经有 `pet-worker/v1`、异步 `QProcess` 宿主、Agent Link 事件采集 Worker、heartbeat、generation、有限重启、in-process fallback 和真实进程测试。它代表 Phase 3A 的实现起点，不代表 Phase 3 全部完成。

## 当前已实现

- 同一可执行文件通过 `python -m pet --worker agent-link-events` 进入 Worker 路径。
- Core 侧异步 QProcess 生命周期、握手、配置推送、事件接收、心跳和关闭。
- Agent Link 文件 tail、边界解析和语义事件转发到 Core 适配器。
- Worker 崩溃检测、有限重启、generation 丢弃旧事件和 in-process fallback。
- 协议、生命周期和真实进程边界测试。

## 尚未完成

- PyInstaller frozen executable Worker smoke。
- 父进程退出时的子进程树清理和异常 kill 证据。
- 长时间 RSS、CPU、事件延迟、重启和日志增长基线。
- 事件洪峰、队列上限、背压和诊断策略。
- 主动识屏 Worker（Phase 3B）。
- Chat UI、完整 AI service、歌词、余额和其他网络能力迁移。

## Phase 3A 执行顺序

1. 收口 `pet-worker/v1` 协议和 QProcess 宿主。
2. 验证 Agent Link Worker 与旧 in-process source 的语义一致性。
3. 完成 frozen smoke、父子进程清理和 Core 退出顺序。
4. 增加队列上限、背压、洪峰诊断和 stale generation 测试。
5. 完成长时间性能基线和 Windows 实机记录。
6. 独立提交并封存；任何异常都可切回 `in_process`。

## Phase 3B 进入条件与边界

只有 3A 稳定后才能开始主动识屏：

- Core：是否允许、白名单、dwell、limiter、用户确认、记忆和结果展示。
- Worker：前台窗口信息、截图、dHash、视觉请求和网络响应解析。

Worker 不掌握“是否允许打扰用户”的最终策略，也不直接创建气泡、动作或 Chat UI。

## Phase 3C：逐项评估

余额、歌词、文件解释、外部播放器和 Harness 等功能逐项按照崩溃风险、网络阻塞、权限/密钥、主进程耦合、常驻成本和可测试性决定，不承诺整体迁移。

## 横向支线

- `PyInstaller/import dependency audit`：独立测量包体和冻结 import 图，不能假设 Worker 自动减小包体。
- Core/Content/Plugin/Worker 反向依赖检查。
- 配置迁移、备份、恢复和 Worker 配置摘要过滤。

## 关联文档

- [`PHASE3_PROCESS_PLUGIN_RESEARCH.md`](PHASE3_PROCESS_PLUGIN_RESEARCH.md)：当前实现状态、协议和研究边界。
- [`../plugin-phase-02-runtime/README.md`](../plugin-phase-02-runtime/README.md)：Phase 2 API 与 Worker 入口关系。
- [`../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md`](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md)：跨阶段 API 合同。
