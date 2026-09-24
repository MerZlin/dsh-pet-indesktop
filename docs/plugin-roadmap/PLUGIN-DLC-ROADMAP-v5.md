# v5 插件化 / DLC 分阶段路线图

> 基线日期：2026-09-24。本文是插件化/DLC 重建的总路线图，不代表后续阶段已经实现。
> Phase 1 的架构、API 和迁移边界见 [`../plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md`](../plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md)。

## 总体原则

- Core 保持最小、稳定、可独立启动；
- content DLC 不执行代码；
- 轻量官方能力才进入 `in_process`；
- 网络、截图、AI、Agent 和外部程序默认进入 worker；
- Core 更新与 DLC 更新分离；
- 第一阶段不直接兼容 VPet 文件格式，只借鉴其内容分域和来源适配思想；
- 第一阶段不接入 Steam Workshop，未来以独立 adapter 方式增加；
- 当前 `pet/updater.py` 和 `pet/update_settings.py` 的更新工作不被插件化重构覆盖。

## 阶段总览

| 阶段 | 目标 | 主要出口 |
|---|---|---|
| Phase 1 | 基础边界与资源 DLC | manifest、资源 provider、Starter DLC 基础 |
| Phase 2 | Core 插件运行时 | Registry、Context、Event Bus、官方 in-process 插件 |
| Phase 3 | Worker 插件运行时 | JSONL IPC、崩溃隔离、Agent/视觉/网络迁移 |
| Phase 4 | DLC 更新中心 | catalog、staging、校验、原子激活、回滚 |
| Phase 5 | 通用分发适配 | 本地、zip、GitHub、CDN、Workshop adapter 预留 |
| Phase 6 | 第三方插件生态 | SDK、签名、capability、社区 catalog |
| Phase 7 | 跨平台正式发布 | 三平台基线、兼容矩阵、发布与运维体系 |

## Phase 1：基础边界与资源 DLC

### 目标

冻结 Core、content、in-process、worker 的边界，建立资源 DLC 的最小闭环。

### 主要工作

- 外部角色目录扫描；
- manifest 校验；
- 角色版本和 Core 兼容性；
- `body_box`、`head_box`、动作分类和步幅数据读取；
- 资源 fallback；
- 本地安装、卸载、升级和回滚的接口设计；
- v4 配置迁移边界；
- 保持现有内置角色资源可用。

### 阶段出口

- 无 DLC 时 Core 可以启动；
- 角色资源可以由 provider 提供；
- 非法路径和损坏资源不会阻塞 Core；
- `shenshen` 可以作为官方 Starter DLC 的迁移目标。

## Phase 2：Core 插件运行时

### 目标

让 Core 具备稳定的插件发现、配置、事件和生命周期管理能力。

### 主要工作

- `PluginManifest`；
- `PluginRegistry`；
- `PluginContext`；
- `CoreEventBus`；
- capability 检查；
- 插件配置命名空间；
- content provider 注册；
- 官方 in-process 插件；
- 插件故障隔离和诊断。

### 依赖

Phase 1 的 manifest、资源目录和兼容检查必须先稳定。

### 阶段出口

- 插件可以发现、启用、禁用和诊断；
- 插件异常不阻塞主窗口；
- 插件不能直接修改全局 Core 配置；
- 至少一个 content 插件和一个官方 in-process 插件完成迁移。

## Phase 3：Worker 插件运行时

### 目标

把网络、AI、截图、Agent 和外部程序能力隔离到独立进程。

### 主要工作

- `WorkerManager`；
- JSONL 本地 IPC；
- `hello`、`ready`、`config_push`、`event`、`error`、`heartbeat`、`shutdown`；
- 版本握手；
- 心跳和超时；
- 有限重启和退避；
- graceful shutdown；
- worker 打包；
- 进程级测试。

### 迁移顺序

1. Agent Link；
2. 主动识屏；
3. DSH bridge；
4. AI Chat service；
5. 歌词、余额和外部播放器等网络型能力。

### 阶段出口

- worker 崩溃不导致 Core 退出；
- worker 协议错误可诊断；
- 应用退出后没有残留 worker；
- 至少两个高风险能力完成真实进程迁移。

## Phase 4：DLC 更新中心

### 目标

让 DLC 拥有独立于 Core 的下载、校验、激活、回滚和诊断能力。

### 主要工作

- `plugins-index.json`；
- 多镜像；
- staging；
- 文件大小、SHA-256 和签名校验；
- manifest 校验；
- 版本目录与 active 指针；
- 原子切换；
- 启动自检；
- 自动回滚；
- DLC 管理设置页。

### 阶段出口

- Core 更新不删除 DLC；
- DLC 更新不修改 Core；
- 不兼容 DLC 自动禁用；
- 激活失败自动回滚；
- 下载中断可重试。

## Phase 5：通用分发适配

### 目标

让分发来源与插件加载器解耦。

### 主要工作

- 本地开发目录；
- 本地 zip；
- GitHub Release；
- 静态 CDN；
- catalog source adapter；
- 安装来源统一化；
- Workshop adapter 预留。

### 约束

Steam Workshop 不作为基础插件架构的前置条件。没有 Steam 环境时，本地目录、zip 和 CDN 仍必须可用。

## Phase 6：第三方插件生态

### 目标

在 Core API 稳定后开放受控的第三方插件能力。

### 开放顺序

1. content DLC 文档和示例；
2. content DLC SDK；
3. worker 插件 SDK；
4. capability 授权；
5. 签名发布；
6. 社区 catalog；
7. 作者信息、标签和兼容矩阵。

### 安全边界

第三方插件默认使用 worker。网络、截图、进程启动和文件访问必须声明 capability，并经过用户授权和平台策略检查。

## Phase 7：跨平台正式发布

### 目标

把插件化架构变成稳定的跨平台发布和运维体系。

### 主要工作

- Windows、macOS、Linux 目录和权限适配；
- 插件签名和公钥轮换；
- Starter DLC 独立发布；
- 插件诊断导出；
- 包体、启动时间、内存和下载体积基线；
- 插件兼容矩阵；
- API 主版本和弃用策略；
- v4 → v5 迁移报告；
- 旧插件停用通知。

### 阶段出口

- Core 与 Starter DLC 可以独立发布；
- 三个平台完成基础启动验证；
- 插件升级、回滚和迁移可重复测试；
- API 版本和弃用策略可以公开给插件作者。

## 暂不实施

- 任意 Python 模块热卸载；
- 未签名正式插件；
- 第三方代码默认进入 Core 进程；
- Chat UI 立即拆成独立 Qt 进程；
- Steam Workshop 作为 Phase 1 前置依赖；
- 通过机械搬运现有 Python 模块来制造插件边界。
