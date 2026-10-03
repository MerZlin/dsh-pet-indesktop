# Phase 4B-2：跨进程版本租约设计

## 目标与边界

Phase 4B-1.5 的代码、自动化、文档与保护门满足内部前置条件；资源包人工安装、重启播放、桌面/托盘行为和公开稳定 API 继续保持 pending。本阶段只解决本地已安装官方 Feature 版本的跨进程占用协调，不把 Worker 独立进程等同于物理可卸载，也不新增安装器 UI、远程目录、公开 SDK 或 Python hot-unload。

## 租约布局

```text
plugins/<feature-id>/
  state.json
  locks/state.lock          # install-state CAS
  locks/leases.lock         # lease decision mutex
  leases/<lease-id>.lock    # owner-held OS lock
  leases/<lease-id>.json    # bounded diagnostic/hand-off record
```

`state.lock` 只保护状态 CAS；`leases.lock` 保护租约获取、交接、释放和占用检查的短临界区；每个租约有独立 OS 锁，持有时间覆盖真正的 Host/Settings/Worker 生命周期。记录使用固定 schema 且有大小上限。PID、时间戳、owner identity 和 heartbeat 只能用于诊断，不能单独证明进程存活或租约释放。

## 获取流程

生产安装路径向 `FeatureVersionLeaseCoordinator` 传入已验证的 `FeatureVersionSelection`：`feature_id`、semver、install `revision`、manifest digest 和受信任 `PackageDescriptor`。获取时在同一个 `leases.lock` 临界区内重新读取 `state.json`，验证 `enabled`、`active`、revision、digest、descriptor root/id/version/trust，再创建记录并持有独立锁。状态 CAS 已变化或摘要不一致时拒绝，不回退到其他版本。

validation-only 的显式目录加载仍使用原有 process-local `VersionLease`，避免把验证目录误当成生产安装版本。

## 生命周期

### Core Host

Host 在进入 verified Python interpreter 前取得 host lease；成功进入 interpreter 后将 lease 纳入进程级持有集合。import/factory 失败、禁用或撤销不能立即释放已进入 interpreter 的版本，Python hot-unload 不在范围内；由应用级 shutdown/进程退出释放。

### Standalone Settings

`--settings` 保持现有 `QLockFile` 单实例保护，不导入 `pet.app`。生产 caller 在构造 Feature settings 前取得独立 settings lease，`_exec_settings` 持有到 Qt event loop 返回；state invalid/revision/digest 变化直接拒绝，不静默回退。当前仓库没有把某个已安装 Feature settings factory 接入 standalone selector 的既有路径，因此提供了 `hold_feature_settings_lease` 和 `_run_settings(..., feature_lease=...)` 注入 seam，未虚报为完整自动 settings package flow。

### Worker

父进程先创建 `worker_reservation`；生产 `program` 和 `arguments` 原样保留，token 通过受控环境字段传给官方 child bootstrap。bootstrap 在导入 Feature runtime 前调用 `claim_worker`，OS 锁和 child record 成功后再将 `DSH_PET_FEATURE_LEASE_CLAIMED=1` 写入 child 环境。Worker 的初始 JSONL hello 必须带 `lease_claimed: true`，父 supervisor 验证后才释放 reservation。子进程崩溃/启动失败/无确认时父进程只收口自己创建的 Worker，并释放 reservation；父进程异常退出不主动按 timeout 删除仍可能被占用的 reservation。

## 状态监视和旧请求保护

`FeatureStateMonitor` 使用 worker-thread `QFileSystemWatcher` 作为即时唤醒，1 秒轮询作为补偿；通知只表示“需要重新读取”，revision 重新读取是权威。Lease acquisition 已在执行前重新校验 enabled/active/revision/digest；同时提供 `verify_current_selection` seam 供 QAction、请求和结果展示在执行/展示前拒绝旧 generation。当前仓库尚没有完整的安装 Feature QAction/结果展示调用面，因此不把通用 seam 冒充为完整 UI 接入。

## 占用检查和失败安全

只有在成功取得对应租约锁时，才可以清理已释放的记录。活动锁返回 `occupied`；锁探测失败、损坏记录、孤儿锁、未确认的 released worker reservation 或其他无法证明安全的情况返回 `pending_confirmation`。版本删除流程必须先调用 `can_remove`，本阶段不新增破坏性删除流程。

## 测试和验证

测试使用真实 `multiprocessing`、真实 `QProcess` 和 Qt event loop；时序使用 `Event`/`Condition`/有界轮询，不用固定 `sleep` 猜测。需要覆盖同版本并发、Core/Settings/Worker 占用、revision/digest 拒绝、Host import/factory 失败保留、handoff、启动失败/无确认/崩溃、父进程异常、子进程退出释放、旧选择拒绝以及 Windows/POSIX 锁路径。最终报告必须包含 ruff、mypy、全量 pytest、文档检查、`git diff --check`、3 次高负载 timing suite 和实机输出。

## 停止条件

如果 revision 与 lease claim 仍有竞态、Windows/POSIX 锁语义无法证明、PID reuse/残留记录可能误删、Worker 越过 bootstrap 导入 UI/Core/plugin、或清理只能依赖 timeout 猜测，则停止向 4B-3 推进，保持旧版本和 validation-only 路径。

## 用户可见效果和限制

本阶段不增加安装/卸载按钮。用户可见变化是正在被 Core、Settings 或 Worker 使用的版本不会被错误清理；状态变化后旧请求和结果不能继续生效。资源包人工验收、桌面/托盘验收和公开稳定 API 不属于本阶段完成范围。
