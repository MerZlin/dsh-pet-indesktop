# Phase 3：Worker 插件运行时

> 2026-09-27 实现检查点：3A 已有封存基线，3B 自动/手动识屏已实现；当前仍是内置 Worker，不是可卸载 DLC。
> 3B 不宣称全部稳定性封存：用户已反馈识屏正常，但人工细项尚未确认；本次全量有一项真实桌面环境相关失败，后续收尾另行记录。

## Phase 3A 基线

同一可执行文件、QProcess、`pet-worker/v1`、日志采集、heartbeat、generation、有限重启、fallback、背压和进程级测试。
证据见 [3A 收口报告](PHASE3A-STABILITY-CLOSEOUT.md)，保留历史结论，不新增长期 soak。

## Phase 3B 实现与回滚

- Core 保留开关、白名单、dwell/idle、limiter、用户状态、generation、单次凭据授权和展示。
- Worker 执行前台观察、截图、dHash、视觉请求；新增 request/response，不更改 Agent 业务事件。
- 自动和手动通道、shared 单 Worker、来源路由、暂停/取消/超时及退出已纳入测试。
- `config_push` 不含密钥；请求临时携带授权凭据，Worker 不访问完整配置或 keyring。
- 内部 `auto/in_process/disabled` 作为宿主及测试注入模式，不是新增设置页开关。
- 原实现和性能证据见 [实施报告](../PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md)，设计与复建见 [3B 设计](PHASE3B-PROACTIVE-SCREEN-DESIGN.md)。

当前组合测试三次通过，新 `webm-chat` 构建/Qt 链/3A 与 3B smoke 通过；全量 `1 failed, 3079 passed`，唯一失败为旧前台窗口测试在不可见前台环境下预期非空，未修改/跳过该测试。托盘自然退出等人工门仍待确认，不能仅凭实现提交宣布全部通过。

## Phase 3C：逐项评估

AI、歌词、余额、语音、文件解释、外部播放器和 Harness 等按阻塞、取消、退出、权限、依赖和常驻成本评估；不因已有宿主就增加 Worker。本检查点不开始新的迁移。

## 后续边界

- 独立进程不等于减少 PyInstaller 包体，需要单独的 import/构建依赖审计。
- UI 和业务状态留在主进程，不等于这些业务代码永久属于 Core 安装包。
- 配置迁移必须备份、可恢复，保留旧模式回滚路径。
- [研究与阶段设计](PHASE3_PROCESS_PLUGIN_RESEARCH.md)、[Phase 2 宿主](../plugin-phase-02-runtime/README.md)、[API 合同](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md)。

## 面向使用者

识屏仍由 Core 用本机 stdin/stdout JSONL 管道连接后台 Worker；无需手动启动。关闭自动识屏不等于禁止手动“看看屏幕”。代码随主程序发布，目前不能通过删除单个 Worker 文件来卸载功能。
