# Phase 5：DLC 分发适配

> **状态：条件启用（2026-09-25）**

Phase 5 只有在需要向用户持续发布外部 DLC 时才实施。它不是 Core 基础加载逻辑的前置条件。

## 始终支持

- 仓库内 `content/` Starter DLC；
- 用户数据目录中的已安装内容；
- 本地目录和本地 ZIP，用于开发、恢复和离线安装。

## 条件适配器

- GitHub Release；
- CDN catalog 和镜像；
- 组织内分发源；
- Steam Workshop（若实际需要）。

每个来源只负责发现、下载和校验，最终都交给 Phase 1/4 的统一 `ContentManager` 事务。Workshop 必须是独立 adapter，不引入 Steam 依赖，不修改 Core 基础加载逻辑。

## 进入条件

- 已有明确的外部 DLC 发布需求；
- 本地安装、回滚和兼容检查稳定；
- catalog、签名、镜像失败和隐私/权限边界已定义；
- 分发来源不会成为 Core 启动硬依赖。
