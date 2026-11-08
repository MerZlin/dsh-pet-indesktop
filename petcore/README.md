# petcore 开发者上手

> 这是 `feat/petcore-vpet-architecture` 分支的新 core。旧入口 `python -m pet`
> 与 `pet/` 仍然保留，作为迁移期的参照与兼容层；本目录不 import `pet`，只有
> `petcore/media/` 与 `petcore/content/legacy.py` 两个适配层例外。

## 当前开发状态（2026-11-09）

当前 Core 为 **0.5.0 / API 1**，使用单进程、多宠独立窗口。近期对话/记忆/语音、
角色台词、QQ 桥接及 Linux/macOS 适配摘要见 [分支 README](../README.md)。
本页侧重开发接线；版本号、源码能力、候选产物和原生验收是不同层面的状态。

- Linux 第六轮候选在 Ubuntu 22.04 构建，并在 Ubuntu 24.04.5 GNOME Wayland + xcb
  验证真实交互、输入法、安装升级与重登自启；未发布正式 Release。
- Mac 已在 Intel macOS 15.8.2 / Qt 6.12.0 验证源码交互、菜单、截图、全屏转场和自启；
  arm64 构建目标尚缺对应硬件，没有 `.app` 产物或安装验收。
- Windows 整仓仍有七项既有失败，压力下 Qt 退出超时未解决；两端各完成 24 组短性能样本，
  30 分钟内存观察已取消。详细范围见 [Linux 报告](../docs/PR-REPORT-PETCORE-POSIX-2026-11-07.md)
  与 [Mac 报告](../docs/PR-REPORT-PETCORE-MACOS-2026-11-08.md)。

较早阶段的数字保留在各批报告中，不作为当前全绿结论。规格和进度分别查看
[SPEC](../docs/PETCORE-SPEC.md)、[日志索引](../docs/PETCORE-LOG-INDEX.md)。

## 1. 环境

Linux/macOS 的源码适配与候选构建入口见 [POSIX 桌面说明](../docs/PETCORE-POSIX.md)。
POSIX 验收使用 Python 3.12，依赖安装需带 `packaging/petcore-posix-constraints.txt`；
Linux 固定 PySide6 6.11.3，macOS 固定 6.12.0。下方 Windows 命令在仓库根目录执行。

Windows（Git Bash）：

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -U pip
.venv/Scripts/python -m pip install -r requirements.txt pytest ruff
```

> 本机 pip 直连可能卡住；可加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`，
> 或用 `uv pip install --python .venv/Scripts/python.exe ...`。

## 2. 运行

```bash
# 只做内容/配置自检，不创建窗口
.venv/Scripts/python -m petcore --check

# 启动桌宠
.venv/Scripts/python -m petcore --character shenshen

# 指定数据目录与 mods 目录
.venv/Scripts/python -m petcore --root ./data --mods-dir ./mods

# 第二个独立应用实例（独立数据目录/lock；进程内多宠由 PetManager 管理）
.venv/Scripts/python -m petcore --instance pet2

# 本项目内安装打包工具（PyInstaller 到 .venv，Inno Setup 到 tools/InnoSetup）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/setup_packaging.ps1

# 构建 onedir + 安装包（需要先跑 setup_packaging.ps1）
# 默认窗口化（无终端窗口）；只排障时加 -Console
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean -Installer

# 代码签名（Windows 11 Smart App Control 会拦截未签名的新构建，错误码 4551）：
# 一次性创建「本机开发证书」并信任（只写 CurrentUser，不需要管理员）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/make_dev_signing_cert.ps1
# 之后把打印出的指纹传给构建脚本；exe 会在 --check 冒烟前签好，setup 在 ISCC 之后签
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 `
  -Clean -Installer -SignThumbprint <证书指纹>

# 把旧素材打成一个 content 包
.venv/Scripts/python -m petcore pack-legacy shenshen --out ./dist/shenshen.zip

# 把外部角色素材目录打成可安装的 content 包
.venv/Scripts/python -m petcore pack-character ./my-character --out ./mods/character.my --id my

# 同步跑一轮 WebM→WebP 首跑供给（正常启动时会后台跑一轮）
.venv/Scripts/python -m petcore provision shenshen

# 开机自启
.venv/Scripts/python -m petcore autostart status
```

`--check` 会输出角色、每段动画实际使用的 clip 后端、mod 列表与诊断；排查
「素材为什么没加载」时先看它，不要先开窗口猜。

## 3. 目录地图

| 路径 | 职责 | 约束 |
|---|---|---|
| `engine/` | 枚举、几何、动画链、行为、位移、状态 | **禁 PySide6**，纯逻辑 |
| `contracts/` | 内容、宿主、事件、feature、贡献、能力、worker 端口 | **只用标准库** |
| `media/` | Clip 后端注册、库、fallback、音效 | 允许适配 `pet.webm_clip` / `pet.library` |
| `content/` | 旧素材只读适配、mod 内容、收集、打包 | `legacy.py` 允许 import `pet.*` |
| `net/` | 唯一网络出口：代理兜底、失败分类 | 只用标准库；其他包不得 import 网络库 |
| `api/` | API 服务规格、`api.json`、钥匙串凭据、探测 | 密钥不落盘 |
| `account/` | 账户配置与额度查询 | 网络只经 `net/` |
| `mods/` | manifest、发现、启停、安装、feature 宿主、CLI | 标准库为主 |
| `platform/` | Windows/Linux/macOS 能力、输入、原生窗口、屏幕、自启 | Qt 与原生窗口操作留在 GUI 线程 |
| `ui/` | 窗口、渲染、气泡、托盘、菜单、设置 | Qt |
| `builtin/` | Core 内置特性（`CORE_FEATURE_IDS`，设置页按 `host_page` 内嵌）+ demo 接缝 | 入口只能是 `host/factory.py:create_host` |
| `menu_templates/` | 默认菜单布局 JSON | 与 `menu_layout.py` schema 一致 |

入口是 `petcore/cli.py`；`petcore/ui/app.py` 的 `CoreHost` 负责把配置、内容、
菜单、设置、托盘和 feature 串起来。

### AI 对话模块在哪

- **core**：`contracts/*` 的端口、`api/chat.py`（SSE/思考参数/联网检索）、
  `net/websearch.py`、`chat/session_store.py`、`ui/chat/`（双栏窗 + 快速气泡）、
  `ui/screen/`（截图动作条 + 结果窗）、`ui/ports.py` 的装配。
- **mod**：`examples/petcore-mods/ai.chat`（人格/提示词/消息组装/翻译/快速对话编排）、
  `examples/petcore-mods/tool.ocr`（本地 OCR 引擎与随包模型）。
- 启动成本实测约 **7.1 MB / 63.5 ms**（对话窗包导入），传输层首次对话才加载；
  OCR 引擎首次识别 +145 MB 常驻。完整代码表、占用与已知耦合见
  [`../docs/PETCORE-ARCHITECTURE.md` §23](../docs/PETCORE-ARCHITECTURE.md#23-ai-对话模块分布与-core-占用2026-10-22)。

## 4. 加一个 Core 菜单项

1. 在 `petcore/ui/menu_actions.py` 的 `core_items()` 注册 `MenuItem`。
2. 在 `petcore/ui/settings/coverage.py` 给它登记设置落点（页面 key + 行名）。
3. 在对应设置页真的做出那个入口。
4. 在 `petcore/menu_templates/petcore-default-v1.json` 决定默认显隐与位置。
5. 跑 `tests/test_petcore_settings_coverage.py`：少任何一步都会红。

菜单项 id 是稳定契约：改 id 等于破坏用户已保存的 `menu_layout`，需要写迁移。

## 5. 加一个 mod

最小内容：

```text
mods/<id>/
  mod.json
```

`mod.json` 的字段、校验规则、安装/停用语义见
[`../docs/PETCORE-MOD-FORMAT.md`](../docs/PETCORE-MOD-FORMAT.md)。

feature mod 的入口只能是 `host-only`（worker 还未实现）。**开发期也要装到数据目录**：
loader 的判据是「位于 `user_mods_dirs`（`<数据根>/mods`、冻结包的 `<安装目录>/mods`）**且**
（`allow_feature_mods` 或安装目录里的 `.trusted`）」，所以放在仓库 `mods/` 或 `--mods-dir`
里的 **feature 包永远是 disabled**（content/角色包不受此限）。正确姿势：

```bash
.venv/Scripts/python -m petcore mods validate ./my-mod
.venv/Scripts/python -m petcore mods install ./my-mod
.venv/Scripts/python -m petcore --allow-feature-mods      # 或设置 →「扩展管理」勾选
```

动手写一个 mod 的完整流程（最小骨架、菜单/设置页接线、端口选型、线程铁律、安装与排错）
见 [`../docs/PETCORE-MOD-DEVELOPMENT.md`](../docs/PETCORE-MOD-DEVELOPMENT.md)；该文件里的最小骨架
是被脚本从文档抽出、实际 `validate → install → 加载 → 菜单可见` 跑过的，可直接复制。

内置官方 mod 放在 `petcore/builtin/features/<id>/`，由
`FeatureManager.load_builtin_features()` 直接加载（受信任，不要求
`--allow-feature-mods`）。新增内置 mod 时至少提供：

1. `mod.json`（`kind=feature`、`execution=host-only`、`entrypoint`）；
2. `host/factory.py` 的 `create_host(context)`；
3. 菜单贡献的 `command` 必须在该 runtime 的 `commands` 字典里；
4. 有菜单贡献就必须有 `settings_page`，否则
   `tests/test_petcore_builtin_mods.py` 会红。

可用端口见 `petcore/ui/ports.py`：`presentation`、`scheduler`、`audio`、
`storage`、`pets`、`interaction`、`quiet`、`http`（P4-a 通用取数）；
`context.host` 提供共享配置。
内置清单（7 个）：`fun.self-talk`、`fun.click-talk`、`tool.quick-launch`、
`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info`。

可导入样板（12 个）在 `examples/petcore-mods/`：`fun.divination`、`fun.festival`、
`reminder.todo`、`voice.chime`、`media.music`、`tool.weather`、`tool.file-reader`、
`ai.chat`、`tool.ocr`、`tool.ai-browser`、`ai.agent`、`im.qq`。用
`petcore mods install examples/petcore-mods/<id>` 安装，并在设置 →「扩展管理」
开启「允许功能扩展」；安装后菜单默认进「扩展」，设置页进入「扩展管理」→「加装扩展」。
详细说明见 [`examples/petcore-mods/README.md`](../examples/petcore-mods/README.md)。
`--root <数据目录>` 可写在 `mods` 前后：`petcore --root D:/pets mods list` 与
`petcore mods --root D:/pets list` 等价，`--portable` / `--instance` 同理。

## 6. 加一个角色资源包

目录、manifest、动画分类、`body_box`/`head_box`、`move_strides` 与帧序列采纳
规则见 [`../docs/PETCORE-CHARACTER-PACKS.md`](../docs/PETCORE-CHARACTER-PACKS.md)。
角色包导入用 `pack-character`：source 可以是 `manifest.json` + `videos/`（或媒体在根目录）
的素材目录，产物是 `character.<id>` content mod（**是目录，不是 zip**），之后用
`petcore mods install` 安装；也可以在设置 →「常规 → 数据 → 角色资源包」直接导入目录或 ZIP。
`pack-legacy` 与它共用同一构建器。落位、三条导入入口与不支持项见
[`../docs/PETCORE-MOD-DEVELOPMENT.md`](../docs/PETCORE-MOD-DEVELOPMENT.md) §9。

## 7. 测试与门禁

```bash
# petcore 单测（无头）
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py -q

# 结构守卫 + 文档/CI 纪律
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest \
  tests/test_petcore_*.py tests/test_architecture.py \
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q

# ruff（仓库基线包含 pet/ tests/ petcore/）
.venv/Scripts/ruff check pet/ tests/ petcore/
```

结构守卫在 `tests/test_petcore_architecture.py`：

- `engine/` 禁 PySide6；
- `contracts/` 只用标准库；
- 直接 import `pet.*` 只允许 `media/` 与 `content/legacy.py`；
- 网络库只允许在 `net/`、`api/`、`account/` 导入；mod 经宿主端口访问网络。

## 8. 改完必须做的事

1. 在 [`../docs/PETCORE-LOG.md`](../docs/PETCORE-LOG.md) 追加一条 `## LNNN`。
2. 在 [`../docs/PETCORE-LOG-INDEX.md`](../docs/PETCORE-LOG-INDEX.md) 加一行索引
   （先读索引，只读命中条目，避免整份 log 进上下文）。
3. 新文档登记到 [`../docs/INDEX.md`](../docs/INDEX.md)。
4. 行为改动补 PR 报告（三节证据）：修改文件说明 / 性能分析 / 实机运行记录。

约定细节见 [`AGENTS.md`](AGENTS.md)。

## 9. 当前已知边界

- 真双屏未在本机验证（本机 1 块物理屏），多屏逻辑只有伪造几何单测。
- Linux GNOME/xcb 候选和 Intel Cocoa 源码已有原生输入/窗口/设置焦点证据；
  不能外推为 KDE/Hyprland、原生 Wayland 或 Apple Silicon 产物已经验收。
- 没有热卸载、没有 mod 沙箱；feature 只支持 host-only。
- 旧 `menu_layout` 的显隐不会被新默认模板强行改写；默认隐藏「大小」只对
  使用默认布局的用户生效。
- 单进程多宠已实现，容量以 `config.MAX_PETS` 为准；每宠独立窗口，
  不引入跨进程碰撞 IPC 或旧版 overlay `ScreenHost`。
- 与主分支的完整功能差距（AI/识屏/Agent/账户/音乐/语音/天气/灵动岛/更新等）
  维护在 [`../docs/PETCORE-MAIN-PARITY-BACKLOG.md`](../docs/PETCORE-MAIN-PARITY-BACKLOG.md)，
  不要用“示例 mod 存在”判断某域已交付。
- Windows 11-05 的 0.5.0 开发签名 setup 是历史本地产物，不包含后续全部 POSIX 改动；
  Linux 候选已验，Mac arm64 包未交付。构建入口和产物范围见分支 README。
- 安装器随附扩展落 `{app}/mods/<id>` 并附 `.trusted`，用户导入落数据根 `mods/`；
  可选扩展不勾选不写盘，具体任务以 `packaging/petcore.iss` 为准。
- 朝向口径：素材原生朝左，`facing == "right"` 才镜像（`_mirrored()`）；反了就是
  「位移方向和面朝向相反」。
- 碰边反馈按接触沿触发，每次落地/撞墙都 Q 弹 + 音效；多宠碰撞分离一律经过
  `_clamp_position()`，拖拽中不参与碰撞结算。
- 全局快捷键只在 Windows 注册，真实按键未在本轮自动化验证。
- Linux 已有三档缩放四边视口与原生输入记录；完整探头/旋转组合及真实多屏仍需按
  POSIX 验收表补测，不能由一次拖动或 offscreen 用例代替。
- 首跑供给只验证了包装与 shenshen 的前 2 段实机转换，未跑全量素材。
- 设置页不再每个 mod 占一个侧边栏页：`ExtensionsPage` 用**左侧竖向导航**聚合「扩展管理」和所有 mod 设置页，按「基础扩展 / 加装扩展」分组；侧边栏 8 页（P6 新增 core 页「API 与账户」）。
- mod 展示名优先 `mod.json` 的 `name_zh`，缺失回退 `name`；安装器随附的包落 `{app}/mods`（`.trusted`），用户导入仍落数据目录 `mods/`。
- 动画节拍：待机一圈结束才掷骰；`anim_weight_idle/turn/act/move` 可调（默认 30/10/40/20），`events/<group>/` 只能经 `play_event()` 触发。
- `tool.ocr` 的 rapidocr 引擎常驻桌宠进程（首次识别约 +145 MB RSS），与主分支「一次识别一个子进程」不同；不点「提取文字」不会加载引擎。
- 联网与 API：只有 `net/`、`api/`、`account/` 可以 import 网络库；API Key 只进系统钥匙串（`KeyringCredentials`），`api.json` 只存服务规格。
- 移动同步：窗口位置由 clip 帧号 + `move_strides.json` 的 `curve` 驱动；移动开始先按方向设置 `state.facing`，再渲染首帧。不要退回固定 33ms 线性步进。

