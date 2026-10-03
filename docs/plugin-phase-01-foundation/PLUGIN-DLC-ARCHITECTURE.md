# Phase 1：资源型 DLC 架构基线

> **修订：2026-09-27；状态：已完成角色资源基础闭环，不等于完整插件生态。**
>
> 阶段顺序见[总路线图](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)，功能归属见[唯一交付总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)。本轮只修订合同，不改变已实现资源代码。返回[文档索引](../INDEX.md)。

## 1. 目标和非目标

保留已有目标：Core 无可选 DLC 仍可显示最小桌宠、基础交互和退出；角色由 Registry 发现、ContentManager 安装/激活/升级/卸载/回滚；坏包不覆盖有效 active 或用户配置。

Phase 1 不执行 Python `entrypoint`，不把资源包当 Worker/功能插件，不实现第三方 SDK、远程 catalog 或 Workshop。现有 SHA-256/路径检查不是发布者身份认证；资源签名扩展点不等于签名发布体系已完成。后续可执行官方包即使来自本地 ZIP，也必须先完成受控加载和来源信任，不能沿用资源开发模式来放行代码。

`assets/characters` 双路径兼容在后续打包、迁移与用户验证完成前保留，不用未经验证的删除换取缩包。

## 2. 四类边界与两种交付

- **内容**：动画、图片、台词/人格文本、预录音效、主题、节日素材、manifest。
- **状态**：角色/位置/缩放等 Core 状态与各功能独立配置、启用状态、用户数据。
- **展示原语**：气泡、动画请求、通知、音效通道属于 Core；功能专属 UI 不因此属于 Core 安装包。
- **功能策略/执行**：提醒、聊天、识屏等不是角色资源；可由主进程官方组件与 Worker 合作，并由所属功能包交付。

```text
Core Kernel + Host Ports + ContentProvider
  ├─ CharacterRegistry / ContentManager → 数据型角色 DLC
  ├─ 官方功能宿主 → 功能专属策略、UI、适配器（后续可选交付）
  └─ Worker 宿主 → 独立进程（不自动等于独立安装包）
```

纯资源包只能被读取，不获得全局配置、GUI 私有对象、网络、密钥或进程执行权限。完整 Starter 是资源包；Core 只需最小 fallback，不因为角色默认选中就永久把全部素材放回 Core。

## 3. 已完成的 Phase 1 基线

以[Phase 1 交付报告](../PR-REPORT-PLUGIN-DLC-PHASE1-2026-09-24.md)及现有测试为依据：

- `CharacterRegistry` 来源顺序：已安装 DLC → 仓库 `content/` Starter → 外部 `characters/` → `assets/characters` legacy → Core fallback。
- `content/characters/shenshen/` Starter；本地目录和 ZIP 由 `ContentManager` 安装。
- manifest、`kind=content`、版本/Core/platform、路径安全及逻辑内容 SHA-256 校验。
- staging、多版本、active/previous、原子激活、自检与回滚；失败不破坏旧有效版本。
- `catalog.list_available_characters()`、`resolve_character_video_dir()`、`load_character_manifest()`、身体/头部框接口继续兼容；调用者不直接拼安装路径。
- 资源缺失/损坏有诊断与 fallback，不阻塞 Core 启动。

**完整闭环主要针对角色包。** 主题、台词、音效、节日等属于资源分类，但不能由此宣称这些独立包类型和所有管理 UI 已实现；可执行功能安装更不是本阶段成果。

## 4. 资源目录与安全

```text
仓库只读来源：content/characters/<character-id>/manifest.json + videos/...
兼容来源：    assets/characters/<character-id>/...
用户安装层：  <platform-data>/dsh-pet-standalone/content/
                characters/<character-id>/versions/<version>/...
                characters/<character-id>/active.json / previous.json
                staging/  cache/  logs/
```

保持包内相对路径及大小写；角色 `body_box`、`head_box`、动作与移动参数归资源。用户安装不写回仓库来源，不覆盖 Core 可执行文件或配置。逻辑 SHA-256 对目录/ZIP 使用同一语义；拒绝绝对路径、`..`、符号链接与越界解压，验证声明资源存在。

具体字段和开发校验限制见 [API 合同](PLUGIN-API-CONTRACT.md)。未来官方可执行包另有 loader/信任及权限检查，不给 ContentManager 增加任意执行入口。

## 5. Fallback 与故障隔离

诊断至少保留 `plugin_id`、`version`、`failure_stage`、`reason`、`fallback_source`。坏 manifest、不兼容、hash 错误、动画缺失、视频不可读或激活自检失败时：

```text
当前角色上一版本 → Starter DLC → legacy assets/characters → Core fallback
```

卸载激活角色前转到可用版本/资源 fallback。资源 fallback 保证基础桌宠仍显示；**它不授权卸载识屏、聊天等功能后恢复隐藏实现**。功能停用/卸载后的行为按其安装状态和授权决定，不能套用角色容错规则。

## 6. 后续阶段与重建起点

- Phase 2 通过 ContentProvider 接入，不重复扫描；菜单/设置包所有权是后续合同，不倒写为已完成。
- Phase 3 保留 Worker 运行隔离；非敏感 `config_push` 与获授权的单次请求临时凭据分开，Worker 不读取 Core Config/keyring。
- Phase 4A/4B 审计并实现首个可拔除官方功能及本地扩展管理；借鉴既有事务，不把资源安装器改成代码加载器。
- Phase 5 官方 Setup/ZIP 选装与 AI 等功能推广必做；Phase 6 第三方生态条件化；Phase 7 是发布门。

失败重启：保留 **ContentManager + CharacterRegistry + 资源 fallback**，先禁用失败功能再验证资源发现、角色切换、配置隔离和离线启动，不回退整个 Core 或删除用户资源。

## 7. 尚未完成及验收要求

远程 catalog、签名发布/公钥轮换、资源管理 UI、通用其他资源包类型、第三方 SDK 及完整跨平台实机/包体验收分别列为后续项。官方功能的本地管理不能等到远程分发或第三方生态。

重建本阶段时先跑资源专项与角色/启动回归，再核对无资源启动、目录/ZIP 等价校验、安装中断、升级失败回滚、非法路径和旧资源兼容；涉及打包/配置/生命周期的实现需全量回归。文档修订不能替代这些实现证据。

## 8. 你最终能看到什么

角色资源可以换、坏了可以回退，最小桌宠不会因为没装完整角色而无法启动。当前已有角色本地安装服务，但还不是“所有功能都可装卸”。以后聊天/识屏有各自功能包，可能含主进程组件和通过 IPC 连接的 Worker；它们不放进角色目录，也不因角色 fallback 自动装回来。具体功能包路径与安装体验由 Phase 4/5 落地。

## 8. 基线评审后的资源硬门（2026-10-02）

Phase 1 是**角色资源实现基线**，尚不能写成发布级闭环。安装成功必须包含“Registry 能解析并实际播放”，而不是只写入 `active.json` 或 `state.json`。

### 8.1 根目录与回退不变量

- 安装版本根和 `CharacterRegistry` 解析根必须一致；目录包与 ZIP 包分别验证。
- Starter、legacy、Core fallback 和用户资源要有明确优先级。Starter 是 Core 可用的内置 fallback 还是独立资源包，必须在实现证据中固定；Core 更新成功、失败和回滚都不能移除 Starter。
- 用户资源不能被 Core 更新删除；资源 fallback 不得复活已卸载的功能包。
- 内容发布根只保存可发布文件；派生缓存放在独立 cache 根，不进入 manifest 摘要、版本清单或激活目录。

### 8.2 P0 封存条件

在进入远程分发或第三方生态前，必须有安装→Registry→播放、重启、激活、回滚和冲突安装保护的真实回归，并验证故障注入后的旧版本摘要和播放仍保持。只检查指针、目录存在或 manifest 可读不算通过。

详细问题分类与重启点见[DLC 基线评审响应](../plugin-roadmap/DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md)。

### 8.3 当前实现与公开状态（2026-10-02）

当前资源硬门代码、自动化、文档与保护门已通过，本地封存就绪但尚未提交，不等于资源接口已经对外稳定。公开资源作者接口必须等待 4B-1.5 的目录/ZIP 解析、Registry→播放、冲突保护、Starter/Core 更新保护、cache 隔离和中断恢复证据；内部样例可以提前制作，但要显式标记为内部验证。

资源包仍是纯内容，不执行代码；官方功能 host、Worker 包和第三方外部 Worker 具有不同的加载、信任和卸载合同，不能用资源包的 `manifest` 直接推导代码执行权限。
