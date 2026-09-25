# Phase 4：DLC 更新中心

> **状态：计划中；本地事务已有 Phase 1 基线，远程分发条件启用（2026-09-25）**

Phase 4 不重复实现 Phase 1 已有的目录/ZIP 安装事务，而是把本地事务、更新诊断和未来远程来源连接起来。

## 必须保持的本地事务

- staging 临时目录；
- manifest、Core/platform、路径安全和 SHA-256 校验；
- active/previous 版本；
- 原子激活；
- 激活后自检；
- 失败自动回滚；
- 用户配置与 Core 文件隔离。

## 条件启用的远程能力

只有实际需要远程发布 DLC 时才实施：

- `plugins-index.json` catalog；
- GitHub/CDN 多镜像和下载重试；
- 文件大小、哈希和签名；
- 自动检查更新和设置页管理；
- 发布说明、兼容矩阵和诊断导出。

## 更新边界

- `pet/updater.py` 和 `pet/update_settings.py` 继续负责 Core 自动更新。
- Core 更新不得删除已安装 DLC；DLC 更新不得覆盖 Core 文件。
- Core 降级或 DLC 不兼容时重新检查兼容范围并禁用/回滚，而不是强行激活。
- 远程签名强制校验在可信发布阶段启用，不能用“字段预留”冒充完成。

详细流程见 [`PLUGIN-UPDATE-PROTOCOL.md`](PLUGIN-UPDATE-PROTOCOL.md)。
