# Phase 4B-1.5：资源 DLC 硬门设计与验收详情

> 状态：资源代码、自动化、文档、保护门与人工验收已通过；公开稳定 API/生态开放仍待后续门
>
> 记录日期：2026-10-03（人工验收状态由用户确认）
>
> 前置：Phase 4B-1 唯一安装状态账本已完成；Phase 4B-2 跨进程版本租约已完成并推送。

## 1. 目标与阶段定位

Phase 4B-1.5 是 Phase 4B-1 状态账本之后、Phase 4B-2 跨进程租约之前的资源专项硬门。它不增加内部拆分数量，而是验证资源 DLC 的真实可用性和失败保护：

```text
安装
→ CharacterRegistry 正确解析
→ catalog / MovieLibrary 能找到真实资源
→ 重启后仍可用
→ 升级、回滚不破坏旧版本
→ 冲突安装不误删旧版本
→ 中断操作可恢复
```

本门的代码、自动化和人工验收已经通过；资源 manifest、作者示例和校验工具在后续稳定 API/生态开放门通过前仍只能标记为内部验证，不对外承诺稳定兼容。资源接口稳定的公开前置条件还包括明确的路径合同、发布信任和后续事务/开放顺序证据。

## 2. 非目标

本阶段不实现：

- 远程下载、catalog、Steam Workshop 或其他复杂分发；
- 任意第三方 Python 代码、Python host 或 Worker DLC；
- 官方功能 host、通用功能包安装器或应用内扩展管理界面；
- Phase 4B-2 版本租约与跨进程状态同步；
- Core 自动更新协议重构、默认安装包切换或便携发布；
- 公开稳定 SDK 的最终承诺；
- 把资源 `ContentManager` 改造成可执行代码加载器。

## 3. 当前实现事实与风险

- `ContentManager` 当前把单角色版本放在 `characters/<character-id>/versions/<version>/`，版本目录本身包含 `manifest.json`、`videos/` 等发布内容。
- `CharacterRegistry._package_from_root()` 需要严格区分“版本目录直接包含 videos”与“多角色包通过 characters/<id> 嵌套”的两种 layout，不能盲目向已安装版本根目录追加 `characters/<id>`。
- `catalog.resolve_character_video_dir()` 和 `MovieLibrary` 是实际播放链路；只检查 `active.json` 不能证明资源可用。
- `ContentManager.install()` 的异常清理必须只删除本次 operation 创建的 staging 或临时版本目录，不能通过当前 active 版本名推断所有权。
- 4B-1 的 `state.json` 只是安装状态账本，不等于资源文件事务、租约或卸载完成。
- `content_sha256` 当前针对发布版本根目录内的文件；运行时派生缓存必须位于版本目录之外，不能改变包身份。

## 4. 内部修复顺序

### 4B-1.5.0 基线和失败测试

- 保存工作树、4B-1 状态服务和现有资源测试基线。
- 新增 `tests/test_content_dlc_hard_gates.py`，先固定失败原因；测试收集失败不能冒充行为 red。
- 不修改 Phase 4A、4B-1 或保护文件的既有改动。

### 4B-1.5.1 统一资源包根路径合同

规范单角色资源包：

```text
<app-data>/characters/<character-id>/versions/<version>/
  manifest.json
  videos/
  ...
```

版本目录就是 `package_root`，Registry 直接解析 `package_root/videos`。目录包、ZIP 包和重启后扫描使用同一规范化根路径；多角色嵌套 layout 必须由 manifest 明确表达，不用目录名启发式扩展。

必须验证目录安装、ZIP 安装、重启扫描、active/previous/fallback 的实际 `CharacterPackage.root` 和 `video_dir`。

### 4B-1.5.2 冲突安装和清理所有权

- 同版本同摘要重复安装必须幂等，不覆盖现有版本。
- 同版本不同摘要必须拒绝，旧 active、previous 和文件摘要不变。
- 异常时只清理 operation-owned staging/temp；不调用依赖 plugin ID 的 active 推断来决定删除。
- 在复制、替换、激活 pointer 和 self-check 失败点注入故障，证明旧版本不被误删。

### 4B-1.5.3 安装到实际播放 E2E

最小强断言：

```text
ContentManager.install()
→ CharacterRegistry.scan/get()
→ catalog.resolve_character_video_dir()
→ MovieLibrary / 资源索引
→ 指定动画文件可读
```

目录、ZIP、初装、重启、升级、回滚、缺资源和 manifest/目录不一致都要覆盖。若环境不能解码视频，至少验证真实路径、manifest、MovieLibrary 索引和文件可读性，不能退化为 pointer 断言。

### 4B-1.5.4 Starter DLC 与 Core 更新保护

Starter 定义为 Core 内置的最小 fallback，不是用户可删除的普通外置包。优先级：

```text
有效用户资源 → 有效 Starter/Core fallback → legacy → 明确无资源
```

必须验证 Core 更新成功、失败、回滚不删除 Starter 或用户资源；用户停用/卸载不被 fallback 偷偷重新启用。若当前构建真实模型不同，先记录差异再补实现，不能文档先宣称满足。

### 4B-1.5.5 内容、缓存和 hash 隔离

版本目录只读、只保存可发布内容；帧序列、缩略图、索引等派生缓存写入独立 cache 根。缓存变化不改变 content hash；版本根出现未声明派生文件时拒绝、标记损坏或由验证失败处理。安装、激活、回滚和 Core 更新不能覆盖外置 cache。

本门不引入新的 manifest file-list schema；先用目录只读与 cache 外置合同解决风险。

### 4B-1.5.6 资源卸载中断和恢复

这是资源侧窄范围恢复，不提前实现通用 4B-3：

```text
记录操作
→ 禁止候选继续使用
→ 更新 active/previous
→ 删除版本目录
→ 记录完成
```

覆盖记录、pointer、删除前、中途删除、提交记录和进程退出故障。未证实完成就不能显示卸载成功；无法证明安全时进入待恢复/禁用，不猜测启用；配置、凭据、记忆和聊天历史不受影响。

### 4B-1.5.7 证据和关闭

记录安装、解析、播放、升级、回滚、冲突、缓存和恢复测试；有界耗时、磁盘变化、Windows 文件占用分类、自动化结果和未完成人工验收。短程故障注入/进程族连续 3 次，不增加长期 soak。

## 5. 验收矩阵

| 验收面 | 通过条件 |
|---|---|
| 根路径 | 目录与 ZIP 安装后 Registry 直接得到正确 `CharacterPackage.root`/`video_dir` |
| 实际播放 | catalog 与 MovieLibrary 找到指定资源，不只存在 active pointer |
| 冲突保护 | 同版本不同内容拒绝且旧版本摘要、active、previous 不变 |
| 清理所有权 | 失败只删除本次 operation 的 staging/temp |
| 重启升级回滚 | 重新扫描、升级和回滚后资源仍可找到 |
| Starter/fallback | Core fallback 保留；用户资源和已卸载功能不被意外恢复 |
| cache/hash | 外置 cache 不影响身份；版本根派生文件不会静默改变身份 |
| 中断恢复 | 未完成操作不冒充完成，重复恢复安全 |
| 数据保护 | 配置、凭据、记忆、额度和聊天历史不变 |
| 回归 | Phase 4A、4B-1、catalog/library 及全量测试无新增失败 |

## 6. 公开承诺分层

| 状态 | 本阶段含义 |
|---|---|
| 内部验证 | 可继续写样例和测试包，但接口细节可能变化 |
| 官方资源接口稳定 | 只有本门全部硬门及本地目录/ZIP一致性通过后才能使用 |
| 官方功能可拔除 | 需要后续官方功能包安装、停用、卸载、重装闭环 |
| Worker 开发者预览 | 需要真实官方可拔除功能样板后才进入 |

4B-1.5 不会把 Worker 数量、manifest 存在或状态账本完成误认为资源 DLC 已稳定公开。

## 7. 回滚与保护

- 每个修复主题单独提交、单独测试和可 `git revert`。
- 不执行 `git add -A`、`git add .` 或 `git reset --hard`。
- 不修改 `pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`、历史 PR 报告和外部评审原文件。
- 任一硬门失败，停在 4B-1.5，不进入 4B-2 实现、不开放资源 SDK、不进入远程分发。

## 8. 实际实现与验证结果（2026-10-02）

本阶段已完成代码与自动化验证，不代表资源 DLC 已对外稳定开放。实际变更集中在 `pet/content/manager.py`、`pet/content/registry.py`、`pet/content/manifest.py` 和 `tests/test_content_dlc_hard_gates.py`。

### 自动化结果

- 资源专项及相关播放链回归：`43 passed in 4.29s`。
- 硬门测试连续三次：分别为 `28 passed in 3.80s`、`28 passed in 3.61s`、`28 passed in 3.77s`。
- Ruff check：通过；`ruff format --check`：`436 files already formatted`。
- `python -m mypy pet/content`：`Success: no issues found in 8 source files`。
- 全量测试（最终文档编辑前的代码状态）：`3562 passed, 12 skipped, 13 warnings in 356.90s`。
- `python scripts/check_docs.py`：`Markdown link check passed: 119 files scanned`。
- `python -m pytest -q tests/test_pr_report_discipline.py`：`47 passed（pytest exit code 0；运行耗时随环境变化，不作为固定基线）`。
- `D:\DELL\Git\cmd\git.exe diff --check`：退出码 0；仅有换行规范提示，无 whitespace error。
- 保护文件核对：`diff -- pet/updater.py pet/update_settings.py` 为空；`status --short -- plugin-roadmap-demo.html` 为空。
- 全量运行时测试最后一次在本轮文档编辑前完成（`3562 passed, 12 skipped, 13 warnings in 356.90s`）；本轮仅文档/记录编辑，未重跑全量。

### 已完成的代码门

- 目录、ZIP、重启后的版本根路径和 Registry 解析；
- 同版本冲突保护、operation-owned 清理和激活自检恢复；
- Registry/catalog/MovieLibrary 的真实资源路径与可读性链路断言；
- Starter/Core fallback、已卸载用户资源不被 fallback 偷偷复活；
- 发布根与派生 cache 隔离；
- 资源侧窄范围卸载中断与可重复恢复。

### 尚未完成的证据

- 未进行用户当前版本的资源包人工安装、重启播放和托盘/可见桌面操作；
- 未进行真实 keyring、真实 API Key、真实模型、用户屏幕截图；
- 未重建或切换默认安装包，未实现应用内管理 UI、4B-2 租约或远程分发；
- 资源接口仍只属于内部验证，不能称为稳定公开作者接口。

## 9. 完成后的实际使用效果

本阶段实现完成后，用户不会新增安装按钮；真正可感知的变化是资源包不再出现“状态显示安装成功但桌宠找不到动画”的假成功。目录包和 ZIP 包都要在安装后实际找到资源，重启、升级、回滚和冲突失败不会误删旧版本，缓存不会污染版本身份。它仍不是应用内管理、Worker DLC、远程 catalog 或 Python host 热卸载；这些属于后续阶段。
