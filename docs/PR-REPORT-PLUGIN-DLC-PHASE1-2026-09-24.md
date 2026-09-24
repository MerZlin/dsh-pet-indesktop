# Phase 1 资源型 DLC PR 报告

> **基线**：当前 Phase 1 实现工作树（资源 DLC 文档与现有 `catalog.py`）
> **日期**：`2026-09-24`
> **范围**：资源 DLC 运行时、Starter DLC、角色 Registry、安装管理器、构建识别、测试和架构文档
> **关联**：[`plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md`](plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md)

## 一、核心特性

本轮为 Phase 1 建立了资源型 DLC 的本地闭环：`content/characters/shenshen` 可作为官方 Starter DLC 被发现；目录和 ZIP 可以通过同一套 manifest、路径安全、版本兼容和 SHA-256 校验；安装、升级、激活、回滚、卸载通过独立用户数据目录完成；现有 `assets/characters` 与 `catalog` 公共函数继续兼容。该轮不引入远程下载、设置页 UI、可执行插件或签名强制校验，也不修改 Core 自动更新链。

| # | 能力 | 说明 |
|---|---|---|
| 1 | Manifest 与安全校验 | 校验资源包类型、版本、平台、Core 兼容性、角色路径、可执行入口、ZIP 路径和 SHA-256。 |
| 2 | Starter DLC | 新增 `content/characters/shenshen`，作为仓库内官方资源包；旧 `assets/characters/shenshen` 仍保留为 fallback。 |
| 3 | Character Registry | 解析已安装 DLC、仓库 Starter DLC、外部旧目录和 legacy assets，并按固定优先级选择角色来源。 |
| 4 | ContentManager | 提供本地目录/ZIP 的校验、安装、升级、激活、卸载和回滚 API，使用 staging 与原子 active 指针。 |
| 5 | CLI 与构建接入 | 提供 `python -m pet.content` 基础命令；WebM 构建识别 `content/`，GIF 构建不打入 WebM Starter DLC。 |

**红线 / 不变量**：

- `pet/updater.py`、`pet/update_settings.py` 和现有 Core 自动更新协议未修改。
- 资源 DLC `entrypoint` 必须为空，不执行任意 Python 代码。
- DLC 安装不写入 Core 目录，不删除用户配置；失败时不替换当前 active 版本。
- `catalog.list_available_characters()`、`resolve_character_video_dir()`、manifest/body/head box 公共入口保持兼容。

## 二、修改文件说明

以下增删行数来自 `git diff --cached --numstat`；二进制 WebM 文件由 Git 以 `- / -` 形式显示，另行列出资源统计。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/catalog.py` | +13 / −33 | 将角色目录解析和可用角色列表收口到 `CharacterRegistry`，保留旧公共函数与 legacy fallback。 |
| `pet/content/__init__.py` | +22 / −0 | 导出 Phase 1 的模型、Registry、Manager 和校验结果类型。 |
| `pet/content/models.py` | +84 / −0 | 定义 `ContentManifest`、`ContentPackage`、`CharacterPackage`、`ValidationResult` 和 `InstallResult`。 |
| `pet/content/paths.py` | +48 / −0 | 统一 bundled、installed、staging、cache、logs 和平台数据目录。 |
| `pet/content/hashing.py` | +102 / −0 | 实现目录与 ZIP 的统一逻辑 SHA-256，以及路径、重复文件、符号链接安全检查。 |
| `pet/content/manifest.py` | +233 / −0 | 解析 manifest，检查字段、ID、版本、平台、Core 范围、资源布局、空视频文件、完整性和开发模式 unsigned 行为。 |
| `pet/content/registry.py` | +193 / −0 | 实现安装版本 > Starter DLC > 外部角色目录 > legacy assets > Core fallback 的来源优先级和诊断日志。 |
| `pet/content/manager.py` | +301 / −0 | 实现目录/ZIP 安装、staging、版本保留、原子 active/previous 指针、自检失败回滚、卸载和恢复；卸载 active 版本时清理失效 previous 指针。 |
| `pet/content/__main__.py` | +47 / −0 | 提供 `validate`、`install`、`list`、`rollback` 命令行入口。 |
| `content/characters/shenshen/` | 110 个新增文件 | 将现有深深角色资源作为官方 Starter DLC；包含 1 个根 manifest、视频、动作元数据和文字片段资源。总大小 54,381,928 bytes。 |
| `scripts/build_onedir.ps1` | +3 / −0 | WebM 变体加入 `content;content`，GIF 变体不携带 WebM Starter DLC。 |
| `scripts/build_linux.sh` | +8 / −0 | Linux WebM 构建加入 `content:content`，GIF 构建保持不打包 WebM 资源。 |
| `scripts/build_macos.sh` | +8 / −0 | macOS WebM 构建加入 `content:content`，GIF 构建保持不打包 WebM 资源。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_content_dlc.py` | +221 / −0 | 覆盖 Starter Registry、正式 hash、manifest/空视频拒绝、ZIP 路径穿越、目录/ZIP hash parity、安装升级回滚、active 卸载、平台目录和构建资源隔离。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md` | +5 / −3 | 将 Phase 1 资源 DLC 实际状态、Starter DLC 和 legacy 双路径写入架构基线，明确未完成范围。 |

### 未改动

- `pet/updater.py`、`pet/update_settings.py`：当前自动更新会话的实现和接口保持不动。
- Chat UI、Worker、Steam Workshop、GitHub/CDN 下载、设置页 DLC 管理 UI：按计划留给后续阶段。
- `assets/characters/shenshen`：Phase 1 保留作为兼容 fallback，没有未经验证的破坏性删除。

## 三、实现要点

1. **统一内容哈希**：目录和 ZIP 都按排序后的 POSIX 相对路径与文件内容计算 SHA-256；根级 `manifest.json` 不重复计入包内容哈希，避免封装形式改变校验语义。
2. **安装事务**：先解压/复制到 staging，再校验并移动到 `versions/<version>`，最后通过临时 JSON 文件和 `os.replace` 切换 active 指针；失败只清理 staging 或候选版本，不覆盖旧 active。
3. **来源隔离**：Starter DLC 位于仓库 `content/`；用户安装包位于平台数据目录的 `content/characters/<id>/versions/`，不写入 Core 目录。
4. **错误隔离**：Registry 对 manifest、兼容性、资源路径和视频自检失败记录 `plugin_id`、`version`、`failure_stage`、`reason`、`fallback_source`，并回退到下一级来源。
5. **签名边界**：Phase 1 保留 `signature` 字段和验证接口，但未强制实现正式签名密钥链；正式签名纳入后续发布/生态阶段。`allow_unsigned=True` 只用于显式开发或本地安装场景，`CharacterRegistry` 正常运行默认拒绝 unsigned 包。

## 四、性能分析

**方法（可复现）**：在 Windows 工作区运行 Python 探针，20 次角色视频目录解析、5 次 Starter DLC 目录校验；环境为 Windows、Python 当前项目解释器、Phase 1 工作树。

```text
resolve_avg_ms=10.297
validate_avg_ms=103.247
starter_bytes=54381928
legacy_bytes=54381492
delta_bytes=436
```

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---:|---|
| `catalog.resolve_character_video_dir()` 20 次平均 | 10.297 ms/次 | 角色解析热路径；当前每次创建轻量 Registry，unsigned 包扫描不重复做完整 hash。 |
| Starter DLC 目录 `validate()` 5 次平均 | 103.247 ms/次 | 新增安装/校验路径；包含目录结构和内容 hash。 |
| Starter DLC 资源大小 | 54,381,928 bytes | 新增 bundled 内容。 |
| legacy 资源大小 | 54,381,492 bytes | 既有资源。 |
| 双路径增量 | 436 bytes | 文件内容相同，仅新增根 manifest；Git 工作树仍有两套路径。 |

**结论**：

1. Core 正常角色解析新增约 10.297 ms/次的当前探针成本；Phase 1 未引入网络、后台线程或长期轮询。
2. 完整目录校验为 103.247 ms/次，触发点是显式 validate/install/activate；Registry 扫描使用结构检查，避免每次启动对 54 MB Starter DLC 重复做完整 hash。
3. 安装路径新增磁盘读写：staging 复制/解压、版本目录写入、active/previous JSON 原子替换；不新增网络、线程或子进程。
4. 当前未完成长期运行内存基线；实现只保留 manifest、路径和轻量模型，不缓存视频帧。Phase 1 验收仍应在三平台打包时补测启动时间、常驻内存和最终包体。

## 五、实机运行记录

### 1. Starter DLC CLI 验证

命令：

```text
python -m pet.content validate content\\characters\\shenshen
```

真实输出：

```text
valid
official.character.shenshen@1.0.0
```

### 2. 安装、升级、回滚、卸载现场

`tests/test_content_dlc.py` 使用当前 Windows 本机临时目录完成真实目录安装、ZIP 安装、版本切换、回滚和卸载流程；并覆盖 active 版本卸载后 previous 指针清理；测试结果为：

```text
13 passed
```

其中覆盖的现场断言包括：新版本激活后 active 指针变化；回滚后恢复上一版本；坏 SHA-256 安装抛出 `ContentError` 且原 active 仍存在；恶意 ZIP 路径被拒绝。

### 3. 用户可见行为与兼容性

通过以下真实 Qt/应用测试确认现有角色切换、启动 fallback 和单进程启动路径未被资源 Registry 改坏：

```text
python -m pytest -q tests/test_click_talk_dialog.py tests/test_app_startup_fallback.py tests/test_single_process_spawn.py
45 passed
```

DLC 设置页尚未接入，因此本轮没有可记录的设置页点击操作；当前用户可见变化是 WebM 构建可以携带 `content/` Starter DLC，角色解析在资源包失效时仍启动并回退。

### 4. 边界与失败路径

- `entrypoint` 非空和不兼容 Core/platform 的 manifest 在测试中被拒绝。
- ZIP `../escape.txt` 路径在解压前被拒绝。
- SHA-256 不匹配时安装失败，当前 active 不被替换。

### 5. 无法自动验证的能力

- 当前环境是 Windows，未安装 `bash`，因此无法在本机执行 `bash -n scripts/build_linux.sh` 或 `bash -n scripts/build_macos.sh`；脚本仅做静态人工复核，Linux/macOS 实机打包仍需在对应平台补跑。
- 当前环境未提供 `ruff` 命令，因此不能声称通过 ruff；已通过 Python 编译检查和 pytest 验证。
- 未在三平台真实机器上验证平台数据目录和最终打包产物；这是 Phase 1 跨平台验收的剩余工作。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| Python 编译 | `python -m py_compile pet/content/*.py` | 通过。 |
| DLC 聚焦 | `python -m pytest -q tests/test_content_dlc.py` | 13 passed。 |
| 相关兼容性 | `python -m pytest -q tests/test_click_talk_dialog.py tests/test_app_startup_fallback.py tests/test_single_process_spawn.py` | 45 passed。 |
| 组合聚焦 | `python -m pytest -q tests/test_content_dlc.py tests/test_click_talk_dialog.py tests/test_app_startup_fallback.py tests/test_single_process_spawn.py` | 58 passed。 |
| 全量 | `python -m pytest -q` | 2957 passed, 10 skipped, 6 failed, 14 warnings；失败集中在既有 `tests/test_drag_move_coalescing.py` 的 6 个 headless 屏幕边界断言，复跑同族仍为 6 failed / 7 passed。 |
| 静态检查 | `ruff check ...` | 未执行成功：当前 Windows 环境未安装 `ruff` 命令。 |
| Shell 语法 | `bash -n scripts/build_linux.sh scripts/build_macos.sh` | 未执行成功：当前 Windows 环境未安装 `bash`。 |
| 空白检查 | `git diff --check` | 通过。 |

全量失败的共同现场证据是 Qt offscreen/headless 下拖拽目标被屏幕边界限制到 `QPoint(1611, 877)`，而断言期待未裁剪目标；失败均在 `tests/test_drag_move_coalescing.py`，不涉及本轮 `pet/content`、`catalog` 或构建脚本改动。该环境问题没有通过修改无关行为来规避。

## 七、已知限制与后续

- 暂无设置页 DLC 管理 UI。
- 暂无远程 catalog、多镜像下载、CDN/GitHub Release、Steam Workshop adapter。
- 正式签名验证和公钥轮换未完成；Phase 1 对官方 Starter DLC 写入 SHA-256；本地开发包仍可通过显式 `allow_unsigned=True` 进行验证。
- Phase 1 只实现 `content` 资源 DLC，不加载 `in_process` 或 `worker` 可执行插件。
- 当前 Starter DLC 与 legacy assets 双份存在，实测工作树资源量增加 54,381,928 bytes；后续完成安装包验证后再评估是否移除重复 bundled 资源。
- 角色解析目前每次创建 Registry；后续可在 Core 服务层提供生命周期内 Registry 实例，以减少重复扫描。

## 八、风险与回滚

本轮安装服务的运行时数据位于用户数据目录，不会把版本目录写入 Core 安装目录。回滚代码可使用 `git revert`；若只需撤销本轮资源闭环，应同时移除 `pet/content/`、`content/characters/shenshen/`、`tests/test_content_dlc.py`、构建脚本中的 `content` 参数、`catalog.py` Registry 接入和本报告/架构文档改动。已生成的用户安装目录可以独立删除，不影响 Core 自动更新链；当前 active/previous 指针和版本目录不会被 Git 回滚自动清理，需要按用户数据迁移策略手动处理。
