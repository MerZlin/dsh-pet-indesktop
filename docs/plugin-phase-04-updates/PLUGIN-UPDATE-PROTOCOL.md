# Phase 4：DLC 更新协议

> **状态：计划中；本地安装能力已存在，远程能力条件启用（2026-09-25）**

## 1. 两层目标

### 必做：本地 DLC 事务

Phase 1 的 `ContentManager` 已建立目录/ZIP 安装、校验、staging、active/previous、原子切换和回滚。Phase 4 的任务是验证其与 Core 生命周期、诊断和版本迁移的关系，不另写一套安装器。

```text
本地来源
→ staging
→ manifest / Core / platform / path / size / SHA-256
→ 安装自检
→ 原子切换 active
→ 保留 previous
→ 启动自检失败回滚
```

安装中断不能破坏当前 active；卸载 active 前必须切换到其他版本或 fallback。

### 条件启用：远程 DLC 分发

catalog 条目应包含：插件 ID、版本、Core 范围、平台、下载地址/镜像、大小、SHA-256、签名、依赖、覆盖策略、回滚支持和发布说明。

```text
获取 catalog
→ 过滤平台/Core 兼容
→ 依赖检查
→ 下载 staging
→ 大小/哈希/签名/manifest 校验
→ 原子激活
→ 启动自检
→ 失败回滚
```

只有真正对外发布 DLC 时，才启用多镜像、下载重试、自动检查、签名强制校验和管理 UI。

## 2. 安全边界

- ZIP 路径穿越、绝对路径、符号链接和越界解压拒绝。
- 哈希、签名或 manifest 任一失败不得激活。
- 下载包和诊断日志保留，支持重试；不覆盖 Core 文件。
- 公钥轮换、撤销和兼容矩阵属于 Phase 7 发布门。
- 正式远程包不得执行未声明的 Python 入口；content DLC 永远不执行代码。

## 3. Core 更新关系

Core 更新链仍由 `pet/updater.py`、`pet/update_settings.py` 和现有 `update.json` 负责。本协议不重构它：

- 更新 Core 不删除 DLC 目录、版本和用户配置。
- 更新 DLC 不写入 Core 安装目录。
- Core 降级后重新检查已安装 DLC 的 `core_requires`。
- 自动更新失败保留可重试包和诊断，不把 DLC 错误归因给 Core 更新。

## 4. 验收门

- 本地目录/ZIP 事务与 Phase 1 回归一致。
- staging、原子激活、previous、启动自检和回滚有故障注入测试。
- 远程能力未启用时 Core 仍完全离线可运行。
- 远程启用前具备 catalog、签名、镜像失败和代理/VPN 诊断测试。
- Core 与 DLC 文件、缓存、日志、临时目录分离。
