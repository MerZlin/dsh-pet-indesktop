# Phase 4B-2：跨进程版本租约计划

## 目标
为已安装 Feature 版本建立跨 Core、Standalone Settings 与 Worker 的 OS 内核租约，保证版本在任何进程占用或占用状态无法证明时都不能被删除或替换。

## 范围
- 新增跨进程租约协调器、独立租约锁和受限诊断记录。
- 接入已验证安装版本的 Host、Standalone Settings 与 Worker 生命周期。
- 增加 Worker 父进程 reservation → child lease takeover 的一次性交接。
- 增加 QFileSystemWatcher + 低频轮询状态监视和 revision-bound 授权复核。
- 使用真实 Windows multiprocessing、QProcess、Qt event loop 和文档/性能证据完成验收。

## 非目标
- 不新增安装器 UI、远程目录、公开 SDK 或 Python hot-unload。
- 不修改 updater、历史报告、用户已有 dirty worktree 或无关功能。
- 实施阶段不自动提交、不自动推送、不创建 checkpoint commit；若当前任务获得用户明确授权，则按显式文件清单执行提交和推送。

## 实施步骤
- [x] 4B-2.0 建立正式记录和设计文档。
- [x] 4B-2.1 跨进程 lease 红测及基础实现。
- [x] 4B-2.2 OS 锁、记录 schema、占用检查和保守 pending 判定。
- [x] 4B-2.3 Host / Settings lease 生命周期接入。
- [x] 4B-2.4 Worker reservation / child takeover 接入。
- [x] 4B-2.5 状态监视、revision/digest 复核和旧选择拒绝接口。
- [x] 4B-2.6 完成真实 QProcess 交接测试、三次高负载、全量和文档证据门。

## 前置门
Phase 4B-1.5 作为内部前置门通过：代码、自动化、文档和保护门已通过；资源包人工安装、重启播放、桌面/托盘行为及公开稳定 API 继续保持 pending，不阻塞本阶段内部实现。

## 验收
1. 活跃或无法证明已释放的租约阻止删除；
2. Host、Settings、Worker 使用独立跨进程租约；
3. Worker 父子交接在父 reservation 释放前完成 child lease claim；
4. revision、digest、enabled、active 或 generation 不匹配时拒绝旧选择/请求/结果；
5. Worker bootstrap 不导入 UI、`pet.app` 或 `pet.plugins`；
6. Windows 与 POSIX 代码路径不依赖 PID、TTL、heartbeat 作为 liveness 证明。

## 当前状态
内部实现与验收门已完成。真实 QProcess handoff/no-confirmation、保守 reservation 判定、revision/digest 复核、三次高负载、静态检查、全量测试、PR 报告和文档纪律检查均已落盘；不代表 4B-1.5 的人工资源/公开 API 门通过。
