# Phase 4B-2 摘要

Phase 4B-2 将安装状态 revision/digest 与跨进程 OS 内核租约绑定：JSON 记录只用于诊断和一次性交接，删除决策必须依赖独立租约锁；任何活跃或无法证明已释放的版本都返回 `occupied` 或 `pending_confirmation`。

当前有效事实：

- Phase 4B-1.5 可作为内部前置门，但资源人工安装、重启播放、桌面/托盘和公开稳定 API 仍 pending；
- 基础 lease coordinator、长生命周期 lock、state monitor、Host/Settings/Worker 接入已落盘；
- validation-only 显式目录路径不获取跨进程租约，生产安装选择才获取；
- Worker 通过受控环境字段传递一次性 token，子进程在 import runtime 前 claim child lease，父进程收到 JSONL `lease_claimed` 后才释放 reservation；
- 尚未完成真实 QProcess handoff 全覆盖、最终全量测试、PR 报告、提交或推送。

下一停点：完成 handoff/no-confirmation 测试，修正异常退出后的保守清理，然后执行所有验收门并更新本摘要。

## 最终结论（2026-10-03）

Phase 4B-2 的内部实现与验收完成：跨进程 OS 租约是删除决策的唯一权威，JSON 只作诊断/交接；活跃或无法证明释放的版本返回 `occupied` 或 `pending_confirmation`。Host、Settings 和 Worker 拥有独立生命周期租约，Worker 父 reservation 在 child lease 被确认前不会释放；状态 revision/digest 和 generation 变化会使旧选择、旧请求和旧结果失效。

证据：受影响聚焦套件 `270 passed, 1 skipped`；时序族三次各 `270 passed, 1 skipped`；offscreen 全量 `3581 passed, 12 skipped, 13 warnings`；Ruff、format、受影响实现模块定向 mypy、真实 multiprocessing/QProcess/Qt event loop 和 Windows 实机 benchmark 均通过。`mypy pet tests` 仍有仓库既有基线 `1796 errors in 199 files`，未宣称全仓库通过。详细文件增删、性能数字、现场探针与限制在 `docs/PR-REPORT-PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-2026-10-03.md`。

边界：4B-1.5 的人工资源包安装、重启播放、桌面/托盘和公开稳定 API 仍 pending；当前没有自动 Settings Feature selector 或完整 QAction/结果展示调用面；不支持 hot-unload、安装器 UI、远程目录、公开 SDK 或任意第三方 Python 入口。4B-3 不得以本阶段自动化结果替代这些门。实现提交 `5a1b6e0` 已推送，工作树中的忽略临时测试包/日志/缓存没有进入提交。
