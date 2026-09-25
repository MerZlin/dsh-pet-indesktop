# Phase 1：资源型 DLC 架构基线

> **状态：已完成基线（2026-09-25）**
>
> 本文描述资源 DLC 的边界和恢复方式。Phase 1 已完成资源型 DLC 的基础闭环，但不代表完整插件化架构、远程分发或第三方生态已经完成。

## 1. 目标和非目标

### 目标

- Core 在没有可选 DLC 时仍能启动、显示桌宠、完成基础交互和退出。
- 角色内容可以由 Registry 发现，由 ContentManager 安装、激活、升级、卸载和回滚。
- DLC 不覆盖 Core 文件，不直接修改用户 Core 配置。
- 错误资源沿“上一版本 → Starter DLC → legacy → Core fallback”降级。
- 为 Phase 2 官方功能插件和 Phase 3 Worker 提供稳定的内容 provider 边界。

### 非目标

- 不执行任意 Python `entrypoint`。
- 不把资源包当作功能插件或 Worker。
- 不在 Phase 1 接入远程 catalog、Steam Workshop 或第三方 SDK。
- 不强制实现发布签名；本地安装保留 SHA-256 和接口字段，远程可信发布再启用强制签名。
- 不删除 `assets/characters` 兼容路径，不用一次迁移换取未经验证的包体缩减。

## 2. 分层和边界

```text
Core Kernel
  ├─ 窗口、动画、移动、交互、配置、生命周期、fallback
  ├─ Content Provider / CharacterRegistry
  └─ Host Ports（展示、调度、命令、事件）
        ├─ Content DLC（只读资源，不执行代码）
        ├─ Official In-process Feature（Phase 2）
        └─ Worker Feature（Phase 3）
```

必须区分：

- **内容**：视频、图片、台词、音效、主题和 manifest。
- **状态**：当前角色、位置、缩放、启用状态、版本和配置。
- **展示**：气泡、动画触发、通知和语音等 Core 原语。
- **策略**：节日提醒、聊天、主动识屏等功能逻辑，不能塞进角色资源目录。

资源 DLC 只描述“有什么内容以及如何兼容”，不获得 Core 对象、密钥、网络或进程权限。

## 3. Phase 1 已完成基线

已完成并由测试覆盖的边界：

- `CharacterRegistry` 按以下顺序解析角色：
  ```text
  已安装 DLC
  → content/ Starter DLC
  → 外部 characters/ 目录
  → assets/characters legacy
  → Core fallback
  ```
- `content/characters/shenshen/` 作为官方 Starter DLC。
- `ContentManager` 支持本地解压目录和 ZIP 来源。
- manifest、`kind=content`、版本、Core/platform 兼容、路径安全和 SHA-256 校验。
- staging、多个版本、active/previous、原子激活、激活后自检和回滚。
- 旧 `catalog` 公共函数继续委托 Registry，`MovieLibrary` 和角色切换保持兼容。
- 安装失败、资源缺失、视频损坏或当前版本失效时不阻塞 Core 启动。

## 4. 资源包合同

```text
content/
  characters/
    <character-id>/
      manifest.json
      videos/
      phrases.json
      sounds/
```

manifest 至少包含 `id`、`name`、`version`、`kind`、`api_version`、`core_requires`、`platforms`、`dependencies`、`capabilities`、`entrypoint`、`content` 和 `integrity`。具体字段、拒绝规则和未来公开边界见 [`PLUGIN-API-CONTRACT.md`](PLUGIN-API-CONTRACT.md)。

路径限制：ZIP 不得有绝对路径、`..`、符号链接或越界解压；角色 ID、版本和相对资源路径使用受限字符；manifest 声明的资源必须存在。

## 5. Fallback 与诊断

DLC 出现 manifest 损坏、版本不兼容、路径非法、SHA-256 不匹配、动画缺失、视频不可读或激活自检失败时，Registry 必须返回结构化诊断：

```text
plugin_id
version
failure_stage
reason
fallback_source
```

fallback 顺序：

```text
当前角色上一版本
→ Starter DLC
→ assets/characters legacy
→ Core fallback
```

失败版本不能覆盖当前 active 版本；卸载当前版本前必须先切换到其他可用版本或 fallback。`assets/characters/<id>` 在 Phase 1 后续兼容验证、打包审计和用户迁移完成前不得删除。

## 6. 与后续阶段的关系

- Phase 2 为官方 in-process 功能提供 `ContentProviderRegistry`，不重复实现资源扫描。
- Phase 3 Worker 不直接读取全局配置或 UI 私有字段；Core 只向 Worker 推送筛选后的配置摘要。
- Phase 4 将本地事务与远程 catalog、更新诊断和可选管理 UI 衔接，但不重复实现本地安装事务。
- Phase 5–7 是否实施取决于分发需求和发布证据，不是 Phase 1 的隐含承诺。

## 7. 失败重启点

如果后续阶段失败，重新开始时保留本阶段的三个边界：

```text
ContentManager
  + CharacterRegistry
  + 上一版本 / Starter DLC / legacy / Core fallback
```

先禁用失败的功能插件或 Worker，再重新验证资源发现、角色切换、配置隔离和 Core 离线启动，不回退整个 Core 或删除用户资源。

## 8. 当前未完成项

- 远程 DLC catalog、镜像和自动更新。
- 发布环境的强制签名和公钥轮换。
- 设置页中的 DLC 管理 UI。
- 面向第三方的 SDK、兼容矩阵和发布目录。
- 完整的跨平台实机资源权限和包体验收。

这些项目属于后续阶段，不能反向改变 Phase 1 的资源安全边界。
