# Phase 3：Worker 运行隔离与官方选装衔接

> 修订：2026-09-27。**3A 已有封存基线；3B 已实现且有本地自动化记录，用户反馈识屏手测正常。两者目前仍为官方内置能力，不是可卸载 DLC。**

## 1. 已有成果与证据分层

| 范围 | 当前事实 | 证据及不能外推的结论 |
|---|---|---|
| Phase 3A | Agent Link 采集 Worker、异步 QProcess、握手/心跳/关闭、generation、有限重启、fallback、背压与真实进程测试 | [稳定性收口](PHASE3A-STABILITY-CLOSEOUT.md) 保留原结论；不新增长期 soak |
| Phase 3B 代码 | 自动识屏和手动“看看屏幕”、request/response、adapter、临时凭据、shared 路由、退出收口 | [当前设计](PHASE3B-PROACTIVE-SCREEN-DESIGN.md)；代码实现不等于物理拆包 |
| Phase 3B 自动化 | 2026-09-26 报告记录全量 3080 passed、Windows 构建、Qt DLL 链和冻结 Worker smoke | [原实施报告](../PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md) 是当时证据，本次文档修订不重跑或改写这些结果 |
| Phase 3B 用户手测 | 用户后续明确反馈“识屏无误，手动测试没问题” | [grill 对齐记录](../grill-2026-09-27-插件化中期对齐.md)；不外推成三平台、选装/卸载或全部发布验收完成 |

历史报告里的待验收项保留原貌；后续反馈与报告有不同日期和证据来源，不通过覆写历史让它们看起来同时发生。

## 2. 当前运行边界与未来交付边界

- GUI 主进程：用户开关、白名单、dwell/idle、limiter、状态权威、记忆、结果展示。
- Worker：前台信息、截图、dHash、视觉网络请求；Agent Worker 负责事件采集与规范化。
- 通用 Core：进程宿主、授权与配置端口、协议路由、展示原语、生命周期和诊断。
- **功能专属策略、UI、adapter 和 fallback 虽然在主进程运行，未来仍随相应官方功能包交付。**

`pet-worker/v1` 管进程控制及请求/响应，`agent-event/v1` 管 Agent 业务语义。`config_push` 不传密钥，已授权单次识屏请求可临时携带选定凭据；Worker 不访问 keyring 或完整配置。

## 3. Phase 3C 不阻塞选装样板

余额、歌词、文件解释、播放器、Harness 等是否需要新 Worker，按崩溃/阻塞风险、权限、常驻成本和可测试性逐项评估。**有条件的是独立进程迁移方式，不是官方功能选装是否交付。**不能要求所有功能先改成 Worker 再开始 Phase 4。

接下来两条线并行：保持既有 Worker 边界与回归稳定；推进 [Phase 4A/4B](../plugin-phase-04-updates/README.md) 的屏幕理解物理拆包和可拔除样板。样板期间可准备 AI 对话依赖/数据审计，闭环稳定后由 [Phase 5B](../plugin-phase-05-distribution/README.md) 优先实施 AI 对话与文件理解，不同时重写多个大模块。

## 4. 横向检查与回滚

1. PyInstaller/import 依赖审计，区分独立进程与包体剥离；同一 EXE 不自动减少安装体积。
2. Core、Content、Feature、Worker 的依赖方向及 UI 所有权检查。
3. 配置备份、幂等迁移、恢复和 Worker 非敏感摘要。
4. 有界性能、退出、generation、fallback 回归，不追加长期 soak。

当前内置实现可切回 `in_process`；未来选装模式中只有已安装、启用、获授权的功能允许 fallback。卸载后不能悄悄用 Core 中旧实现继续截图或联网。

## 5. 阅读入口

- [进程设计与迁移决策](PHASE3_PROCESS_PLUGIN_RESEARCH.md)
- [Phase 3B 设计与复建计划](PHASE3B-PROACTIVE-SCREEN-DESIGN.md)
- [Phase 2 宿主](../plugin-phase-02-runtime/README.md)
- [API 合同](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md)
- [功能归属总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md) · [总路线图](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)

## 6. 给使用者的效果说明

现在识屏工作已能在独立后台进程执行，Core 用本机 stdin/stdout 管道连接它，没有额外操作窗口。自动识屏关闭不等于禁止手动“看看屏幕”；要完全禁用须同时禁用执行入口。代码目前在 `pet/workers/` 和主进程适配路径里，随主程序发布，不能直接删文件当作卸载。

接下来屏幕理解会成为首个真正的官方选装包：专属代码、UI、依赖与 fallback 一起归包，装好才出现菜单/设置，应用内可以卸载并默认保留数据。具体包格式和目录由 Phase 4A/5A 验证后确定，不必等待第三方生态。AI 对话是其后的主要目标，不会被遗漏。
