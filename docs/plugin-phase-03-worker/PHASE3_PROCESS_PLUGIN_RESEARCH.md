# Phase 3：进程边界、迁移决策与复建依据

> 修订：2026-09-27。本文是现行设计依据，不再是“纯研究”。3A 已有封存记录，3B 已实现并有本地验证与用户手测反馈；独立进程尚不等于独立安装包。

## 1. 为什么先拆事件采集

Agent Link 同时承载日志 tail、协议解析、规范化、桥接、Qt 定时器及展示。3A 只将文件读取、轮转/截断、原始记录解析与语义转换放到 Worker，保留公共入口和用户可见行为，避免一次重写整个 `AgentLinkManager`。

自动识屏与手动识屏随后共用第二类 Worker，策略留在 GUI 主进程、截图及视觉请求放到子进程。保留主进程策略是状态权威选择，不是将功能永久绑定 Core 安装包的理由。

## 2. 协议与启动边界

同一可执行文件以 `--worker agent-link-events` 或 `--worker proactive-screen` 在 `pet.app` 导入之前分流。使用程序路径和参数数组，不拼 shell；Worker 不初始化 Qt GUI。Core 通过异步 `QProcess` 收发 stdin/stdout JSONL，不在 GUI 主循环阻塞等待。

`pet-worker/v1` 的消息包含 protocol、worker_id、type、timestamp、payload，RPC 还必须带非空 request_id：

- 控制与推送：hello、ready、config_push、event、error、heartbeat、shutdown。
- Phase 3B 请求与响应：request、response，包含 operation、业务 generation 及结构化结果/错误。
- JSONL 单行上限 64 KiB；非法 JSON、版本/身份/类型错误和过时代数不得冒泡到 Core。

`agent-event/v1` 独立定义 Agent 事件字段与语义，不把 DSH bridge 的文件 tail、WebSocket、hook 安装等变成通用 Worker 协议。进程 generation 与功能请求 generation 各自校验，不能混用。

默认生命周期参数：握手 5 秒、心跳 5 秒、15 秒失联判定、优雅关闭 2 秒；60 秒内最多重启 3 次，退避 0.5/1/2 秒。退出遵循停止生产者 → shutdown → 限时 terminate/kill；EOF 和父进程异常路径均需回归。实际实现与证据以相应设计/报告为准。

## 3. Phase 3A 已收口，不重新制造待办

[稳定性收口报告](PHASE3A-STABILITY-CLOSEOUT.md) 保存组合 Qt 测试、冻结 Worker、父子退出、队列/背压、事件一致性、fallback、有界性能和 Windows 验证的状态。原 PR 报告与收口报告不在本次修订中改写。

继续保留回归：事件顺序、轮转/截断、重启不重复消费、旧 generation 丢弃、限次重启、故障后单一来源 fallback、正常入口与冻结入口。已有有界样本沿用，用户已决定不追加长期 soak；不得再把“新增长时间运行测试”当作下一阶段前置条件。

主进程暂保留 Agent Link 展示、prompt、桥接安装/卸载及设置兼容；未来这些专属部分归 Agent 功能包，账户余额归账户与用量，Core 仅提供通用宿主与用户确认能力。

## 4. Phase 3B 当前边界

```text
GUI 主进程：是否允许、白名单、dwell/idle、limiter、用户确认、记忆与展示
Worker：前台信息、截图、dHash、视觉请求与响应解析
本机管道：pet-worker/v1 request/response + heartbeat + 生命周期
```

自动流程先观察，由主进程批准才截图/分析；每次自动 HTTP 尝试反向申请 budget_check。手动请求单独授权、不计自动额度；shared 模式共用 Worker，结果路由到发起窗口。截图只保留有 TTL 的内存帧，不经 IPC 回传、不落盘。

`config_push` 无密钥；Core 从安全存储取出选定的单次请求凭据临时下发，不传完整 Provider/Config/聊天数据库。Worker 无 keyring 或 Core 私有对象访问；完成后释放可控引用，但不承诺 Python 内存密码学擦除。网络期间保持控制循环与心跳，取消为协作式。

[3B 设计](PHASE3B-PROACTIVE-SCREEN-DESIGN.md) 保留准确上限和复建顺序；[实施报告](../PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md) 是原本地证据。用户 2026-09-27 对齐时已报告识屏手测正常，不将该反馈扩展成跨平台或独立安装验收。

## 5. Phase 3C：逐项决定进程形态

后续 AI/文件解释、余额、歌词、播放器、Harness 等按风险决定是否独立 Worker：

- 真实阻塞/崩溃风险与权限、秘密边界；
- 与主进程数据、UI 的耦合以及可测试性；
- 启动频率、常驻 RSS/CPU 和通信成本；
- 能否保留行为、回滚和明确故障诊断。

不是每项都需要一个 Worker，也不能等全部进程化才交付选装。官方功能是否可拔除是硬目标，进程数量不是成绩指标。完整归属见 [功能总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)。

## 6. 与 Phase 4/5 的交接

Phase 4A 审计屏幕理解的策略、UI、adapter、vision、fallback、依赖与构建产物，决定受控官方包加载方案，证明最小 Core 不带专属实现。Phase 4B 实现安装/启用/撤销贡献/卸载/重装/升级/回滚。Phase 5A 完成 Setup 和 ZIP/便携交付，Phase 5B 优先 AI 对话与文件理解，再推广其余领域。

AI Chat QWidget 不强制独立进程，但必须归 AI 功能包；QThread 不算 Worker。屏幕理解可以独立配置视觉服务，不依赖聊天；共同模型能力通过窄接口或显式依赖解决，不能把全套 AI 塞回 Core。第三方 SDK/Workshop 仍条件化，不阻塞官方选装。

## 7. 验证、故障回退与复建

协议用确定性单元测试，生命周期用真实 QProcess，Qt 使用 QApplication 和事件同步；外部截图/模型在 CI mock 边界，人工体验单独记录。冻结程序、退出、队列、秘密脱敏和实际产物必须有证据，不用源码 mock 替代。

3A 出问题先回退已安装 Agent 功能的 in_process 来源；3B 出问题保留 3A，回退或禁用识屏执行路径。未来卸载/停用后禁止 fallback 复活；清理任务与贡献、协调实例和占用文件后再移除包。保留配置，破坏性清数据另行确认。

## 8. 给使用者的效果说明

当前 Worker 是 Core 管理的独立进程，通过本机管道连接，不是恶意代码沙箱，也还不是可以拷走的 DLC。现有文件在 `pet/workers/` 与功能适配路径中。未来官方包会包含功能自己的 UI、策略和 Worker，Core 只留下通用接口；具体安装目录和包格式在 Phase 4A/5A 定案。安装后入口出现，停用不工作，卸载后入口与专属文件移除，数据默认留下。屏幕理解先做成样板，AI 对话紧随其后，不等第三方生态。

导航：[阶段入口](README.md) · [总路线](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md) · [Phase 4](../plugin-phase-04-updates/README.md)
