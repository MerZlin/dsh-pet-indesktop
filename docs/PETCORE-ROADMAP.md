# petcore 路线图

> 基线：`feat/petcore-vpet-architecture`，2026-10-20。状态以实际验证为准，未跑过的项不写成通过。

## 0. 编号约定（2026-10-22 起）

两套编号不要混用：

- **P0–P6**：2026-10-22 起的**交付阶段**，只出现在 §20、`PETCORE-HANDOFF-PLAN-2026-10-22.md`
  和后续新报告里。
- **H1–H8.4**：**历史批次**（重构完成期），由旧的 `P1-A…P8.4` 改名而来，只用于回顾。
  历史文档（`PETCORE-LOG.md`、`docs/PR-REPORT-PETCORE-P1…P8…`）保留旧写法，按下表换算。

| 旧写法 | 新写法 | 主题 |
|---|---|---|
| P1-A | H1-A | UI 风格对齐 |
| P1-B / P1-C / P1-D / P1-E / P1-F | H1-B / H1-C / H1-D / H1-E / H1-F | 核心体验、窗口与设置、角色包、贴边、探头 |
| P2-A / P2-B | H2-A / H2-B | 交互/多宠/关于、内置 mod |
| P3 / P4 / P5 / P6 | H3 / H4 / H5 / H6 | 收口、安装器、易用性、动画与联网 |
| P7 | H7 | 探头/击飞与安装治理 |
| P8 / P8.1 / P8.2 / P8.3 / P8.4 | H8 / H8.1 / H8.2 / H8.3 / H8.4 | 多宠/气泡/设置、观感/菜单/弹弓、隐藏气泡与托盘、托盘与出图、穿透归位与高清 |

> `M0–M6` 是初始 core 里程碑，与上面两套都不同轴，保留原名。

## 1. 本轮（M0–M6）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 分支、venv、路径/配置/日志、contracts、`--check` 骨架 | 完成 |
| M1 | GraphCatalog、动画链、行为权重、位移/夹取、状态 | 完成（单测通过） |
| M2 | WebP 帧序列 / WebM / GIF 后端、ClipLibrary、音效原语 | 完成（单测 + 实机首帧通过） |
| M3 | mod manifest/loader/state/install/CLI、旧素材只读 provider | 完成（33 条 mod 测试通过） |
| M4 | 透明窗、逐像素穿透、多屏、会话结束、自启、气泡、菜单/托盘 | 完成（无头测试 + 实机窗口截图；真双屏未验） |
| M5 | 设置宿主与五个 Core 页面 | 完成（7 条无头测试通过） |
| M6 | 文档、PR 报告、结构守卫、实机证据 | 完成（本轮范围） |

## 2. 后续（按批次顺序）

| 批次 | 内容 |
|---|---|
| **H1-A UI 风格对齐（已完成）** | 右键/托盘菜单换成主分支同款视觉：浅/深 QSS、26px 行高、图标列、勾选/子菜单箭头、分隔符、跟随系统深浅；设置页补齐主分支视觉骨架：侧栏 34px 行高 + 3px 选中强调条 + 图标、页面标题 22px、卡片与行度量、内容宽度上限、搜索状态行；用实机截图与主分支并排对照。 |
| **H1-C 窗口与设置体验（已完成）** | 设置/关于改非模态，打开时可拖动桌宠；大小改为 10–300% 滑条 + 手输百分比；设置实时预览、保存落盘、取消回滚；大小默认移出右键菜单；菜单项→设置入口覆盖矩阵；spec/log/log-index/README/AGENTS 文档体系。 |
| **H1-B 核心体验（已完成，打包未实测）** | 全局快捷键、开机自启 CLI、多实例（每实例一只宠）、打包入口脚本、WebM→WebP 首跑供给/世代 GC 包装。 |
| **H1-D 角色资源包导入（已完成）** | `pack-character` 从 manifest+媒体目录生成 content mod（mod schema 1 + `character` 字段）；校验/复制/硬链接；安装复用 `mods install`。 |
| **H1-E 贴边按身体框（已完成）** | 位置钳制、右下角/主屏落位、位置恢复/比例/跨屏迁移改用 `body_box × scale`；透明画布允许出屏；旧配置回退旧比例。 |
| **H1-F 边缘探头（已完成）** | 45° 探头状态机、`body_box` 投影宽度、探头放宽域、设置/菜单开关、旋转渲染与命中反向映射；`head_box` 预留。 |
| H2 功能域迁包（第一批） | 无网络/无 worker：F08 台词、F10 趣味、F07 提醒、F11 快捷启动；每域一个 `mods/<id>`。 |
| H3 功能域迁包（第二批） | 平台适配：F04 账户、F05 音乐、F06 语音、F09 灵动岛；随后 worker 三域：F01 屏幕理解、F02 AI、F03 Agent。 |
| H4 取代旧壳 | 新壳功能齐备后切换发布入口，`pet/` 收敛为兼容层，最终删除。 |

## 3. H1-A：风格对齐的范围与验收

状态（2026-10-20）：已完成。默认菜单顺序、可自定义布局、编辑器、QSS/图标、设置页侧栏骨架均已落地；浅/深截图见 `.scratch/petcore-evidence/{menu,settings-menu}-*.png`。

### 3.1 右键/托盘菜单（真正的缺口）

现状：`MenuModel` 已经支持动作/勾选/子菜单/顺序/隐藏，但 `build()` 用的是 Qt 原生 `QMenu`，没有主分支的视觉。

要移植：

- QSS：`QMenu` 背景/边框/圆角、`::item` 26px 行高与内边距、`::item:selected`、`::item:disabled`、`::separator`、`::indicator`、子菜单箭头；浅/深两套 token，跟随系统。
- 图标列：把主分支 `context_menus/icons.py` 里与菜单相关的绘制逻辑**移植到 `petcore/ui/icons.py`**（不 import `pet`，保持架构守卫），给每个 Core 菜单项和 mod 菜单项配图标。
- 结构：补 `separator` 贡献类型与分组语义；角色/缩放子菜单保持现状。
- 交互：键盘上下/回车/ Esc、点击外部关闭、菜单出现在光标上方/下方时不出屏、勾选项状态实时反映配置。

验收：浅/深两套实机截图与主分支并排；菜单项高度、图标尺寸、分隔符间距逐项对照；`test_petcore_menu.py` 增加 QSS token 与分隔符/图标断言；不新增 `pet` import。

### 3.2 设置页（已对齐一部分）

现状：`petcore/ui/settings/style.py` 已经用主分支的浅/深调色板与圆角（12px 卡片、7px 输入/按钮），差在骨架细节。

要补齐：

- 侧栏：行高固定 34px、选中态 3px 左侧强调条（与强调色同源）、图标列；换主题时按记录重画图标。
- 页面：标题 22px、区块标题/说明层级、卡片内边距与行距、表单阅读宽度上限 800px、侧栏最小宽度。
- 搜索：结果为空时的状态行文案与位置。
- 视口：900×620 与 720×500 两档、100%/125%/150% DPI 下无裁切、无横向滚动。

验收：与主分支同页并排截图；`test_petcore_settings.py` 增加行高/强调条/宽度上限/空搜索断言；实机截图进 PR 报告。

### 3.3 顺序与成本

先菜单（所有 Core 命令与后续 mod 入口都吃这套样式），再设置页。两项都是视觉收口，不改数据模型与端口；预计各 1–2 天，必须先出截图再合。

## 4. Core / mod 边界

Core 只保留：生命周期、窗口与交互、动画/基础物理、通用呈现、菜单/设置宿主、配置与扩展宿主、最小 fallback。

F01–F11 一律做成 mod。跨域联动只走端口、事件与能力检查，不互相 import。

## 5. 明确延后

- 每屏多宠、跨进程碰撞 IPC、overlay 每屏 `ScreenHost`；
- 桌面通知、全局快捷键、自动光标隐藏；
- 配置多槽位、备份/迁移、Core 自更新、keyring；
- Agent 联动动画、音乐唱歌、`animation_gap`、共享解码 broker、预测预热；
- WebM→WebP 转换、世代退役与 GC；
- VPet 素材导入与 PNG 序列后端（只留接口）。

## 6. H1-C 验收（2026-10-20）

| 项 | 判定 |
|---|---|
| 设置/关于可移动桌宠 | 两个窗口均为 `NonModal`；打开时拖动/点击/滚轮桌宠仍有效。 |
| 大小 | 滑条 10–300%、数字框可直接输入、四档预设与恢复默认；`Config`/`PetState`/`PetWindow` 共用夹取。 |
| 实时预览 | 改角色/大小/置顶/穿透，桌宠立即变化；点保存才写 `config.json`；取消/关闭恢复快照。 |
| 菜单默认 | 默认布局不含「大小」；菜单编辑器可重新勾选；子菜单含「自定义大小…」。 |
| 菜单/设置一致性 | `coverage.py` 覆盖所有 Core 菜单项（`modern_settings` 豁免），测试守住。 |
| 文档 | `PETCORE-SPEC.md`、`PETCORE-LOG.md`、`PETCORE-LOG-INDEX.md`、`petcore/README.md`、`petcore/AGENTS.md` 均登记在 `docs/INDEX.md`。 |

## 7. H1-E 贴边身体框（2026-10-20 已完成）

| 项 | 判定 |
|---|---|
| 单屏贴边 | 身体框左右缘贴屏，窗口透明边可越界；`clamp_body()` 是唯一入口。 |
| 多屏 | 身体跨屏缝合法；落进屏间空洞按最近屏收口（`clamp_body_to_screens()`）。 |
| 恢复 | `body_rx/body_ry` 优先；旧 `rx/ry` 回退；选屏用身体矩形。 |
| 落位 | 右下角身体右缘留 24px、身体底贴可用区底；主屏同理。 |
| 回退 | 无 `body_box` 时整窗钳制，行为与改造前一致。 |

## 8. H1-F 边缘探头（2026-10-20 已完成）

- 配置/入口：`edge_probe_enabled`（默认关）、设置「外观与交互」复选框、右键「桌宠控制」勾选项；`coverage.py` 登记。
- 纯几何：`engine/edge.py` 的 `edge_side_at_rest()` / `rotated_bounds()` / `probe_target_x()` / `probe_body_bounds()`；探头放宽域必须减去 `body_local.x`。
- 控制器：`ui/edge_probe.py`，`QTimer(16ms)` + 可注入时钟，状态机 `OFF/ENTERING/PEEKING/STRAIGHTENING/STRAIGHTENED/RETURNING`。
- 渲染/命中：探头姿态绕帧中心旋转；`is_transparent_at()` 反向旋转后再做 alpha 判定；点击由探头消费，拖动取消探头。
- 验收：左/右缘 45° 部分出屏（body 投影约 55%）、点击拉直、5s 回探头、拖走取消、关闭开关恢复常规钳制；macOS/Linux 只做无头几何测试并如实登记。

## 9. H1-B 核心体验（2026-10-20 完成；打包未实测）

| 项 | 状态 |
|---|---|
| 全局快捷键 | 完成：`hotkey_spec` + Windows `RegisterHotKey`，设置页可改；真实按键未在本轮自动化验证。 |
| 开机自启 | 完成：源码/打包命令文本 + `petcore autostart on/off/status`。 |
| 多实例 | 完成：`--instance NAME` 独立数据根/lock；单进程多宠由 P2 补齐（slot 0 主宠 + slot 1–15）。 |
| 打包入口 | 完成：`packaging/petcore_entry.py` 与 `scripts/build_petcore.ps1`；P2 已在本项目内安装 PyInstaller/Inno 并实际构建。 |
| 首跑供给 | 完成包装：`media/provision.py` 复用 `pet.frameseq_provision`；实机把 shenshen 的 2 段转成 frameseq（其余 104 段仍 webm）。 |

## 10. H1-D 角色资源包导入（2026-10-20 完成）

- `pack-character <source> --out <dir>` 生成 `character.<id>` content mod；
- source = `manifest.json` + `videos/`（或媒体在根目录）；校验 body/head、媒体存在、id 合法；
- 复制/硬链接媒体与 `frameseq/`；安装复用 `petcore mods install`；
- 实机用合成包验证；**未做真实大素材导入**。

## 11. H2-A 交互、多宠与关于（2026-10-20 完成）

| 项 | 状态 |
|---|---|
| 关于并入设置 | 完成：删除独立窗口/菜单项；设置页含诊断、复制、项目主页。 |
| 单进程多宠 | 完成：slot 0 主宠 + slot 1–15；独立配置、恢复、收回、碰撞；`--check` 输出 active_pets。 |
| 点击 Q 弹 | 完成：220ms、底部居中、旋转/探头叠加、命中逆变换。 |
| 拖动物理/弹弓/黄金回旋/投喂/探头击飞 | 完成；默认拖动物理关，弹弓默认开。 |
| 菜单顺序 | 完成：主分支顺序 + 新架构项归位；`extensions_hub` 承接未排布 mod。 |
| 设置页 | 新增「互动与物理」「多宠」；所有 Core 菜单项有落点。 |
| 播放速率/动作等待间隔 | 完成。 |
| 本地打包 | 完成：PyInstaller 6.22.3 + Inno Setup 6.7.3；onedir 与安装包实际构建，`--check --out` 通过。 |

## 12. H2-B 内置 mod（离线域）

已落地内置 7 个：`fun.self-talk`、`fun.click-talk`、`tool.quick-launch`、`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info`。`fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music` 已移到 `examples/petcore-mods/` 作为可导入样板（`petcore mods install` + `allow_feature_mods`）；安装后菜单默认进「扩展」，设置页进「扩展管理」页内标签。

下一批：P3 网络/worker 域（AI、识屏、账户、天气、Agent 联动）。**P6 修订**：账户与
API（配置/探测/凭据/额度）归 core（`petcore/net` + `petcore/api` + `petcore/account`），
AI/识屏/音乐/语音等**执行**仍走 worker 或 mod；P3 表里的「账户走 mod」不再成立。

## 13. H3 收口（2026-10-20）

| 项 | 状态 |
|---|---|
| 设置页聚合 | 完成：`SettingsTabContainer` + `ExtensionsPage`；侧边栏固定 7 页；「扩展管理」页内标签承载 ModsPage 与所有 mod 设置页。 |
| 可导入样板 | 完成：5 个 feature 包移到 `examples/petcore-mods/`；`petcore mods install` + `allow_feature_mods`；内置只留 7 个。 |
| 快捷启动位置 | 完成：`tool.quick-launch:open_menu` 移入「工具与帮助」。 |
| 移动朝向 | 完成：按移动方向设置 `state.facing`，首帧即正确镜像。 |
| 位移同步 | 完成：`move_curves` + `move_position_at_frame` 帧驱动；静态帧不位移，末拍到位。 |
| 文档/门禁 | 完成：L010–L012 已写；`591 passed, 5 skipped`；`ruff` 绿；`dist/` 已按 P3 代码重出 onedir 与 Inno 安装包。 |
| CLI 与扩展页兜底 | 完成：子命令前的 `--root/--portable/--instance` 生效；裸 `QWidget` 设置页可见并标注不参与保存。 |

## 14. H4 交互与安装器收口（2026-10-20）

| 项 | 状态 |
|---|---|
| 朝向镜像 | 完成：改用主分支口径——素材原生朝左，`facing == "right"` 才镜像；`PetState.facing` 默认 `left`。 |
| 黄金回旋接续 | 完成：`_begin_spin` 起表（此前角度冻结在 0°，点击动画结束后“没有回旋”）。 |
| 碰边反馈 | 完成：按「离地→接触」沿触发，每次落地/撞墙都 Q 弹 + 音效（此前整个抛掷只响一次）。 |
| 边缘探头 | 完成：贴边判定给 28px 余量并吸附压边，落地反弹停在边旁也能探头。 |
| 多宠穿模 | 完成：碰撞分离按身体框钳进屏幕；碰撞打断移动计划；拖拽中不与其它宠结算。 |
| 子宠落座 | 完成：`engine/spawn.py` 逐圈找空位并保证整只落在可用区内。 |
| 首次点击卡顿 | 完成：`SoundPlayer` 缓存 `QSoundEffect` 并预建；启动后预热全部点击 clip。 |
| 设置分页溢出 | 完成：标签条进横向滚动区，两侧翻页箭头，切页自动滚到可见。 |
| 设置搜索 | 完成：搜索口径包含页面内所有行文案，命中后自动切页 + 滚到该行。 |
| 黄金回旋子选项 | 完成：「点击时跳过点击动画」缩进为子项，仅在「点击触发黄金回旋」开启时显示。 |
| 安装器 | 完成：窗口化构建（无终端窗口）；5 个扩展改为安装向导可勾选，装到 `%APPDATA%\dsh-pet-core\mods\`，附 `.trusted` 授信标记。 |
| 已安装扩展展示 | 完成：「扩展管理」列表标出「随安装包」，选中显示类型/版本/位置/授权来源。 |


## 15. H5 交互易用性（2026-10-20）

| 项 | 状态 |
|---|---|
| 气泡贴合 | 完成：锚点改 `head_box`（回退 `body_box`），候选布局不压角色，gap 6px。 |
| 台词大框 | 完成：自言自语/点击台词改 `QPlainTextEdit`，一行一条，兼容旧 `a | b`。 |
| Enter 保存 | 完成：设置窗口 Enter = 保存退出，多行框内保持换行。 |
| 免打扰页 | 完成：默认档下拉 + 当前状态 + 恢复提醒 + 立即静默四档，附说明文案。 |
| 命令反馈 | 完成：`say/notify(force=True)` 绕过免打扰；网络信息/回收内存/免打扰都有气泡。 |
| 菜单对勾 | 完成（L101 改口径）：行尾 `MenuCheckLayer` 自绘（13px 强调色圆 + 白勾），`QMenu::indicator` 只留 1px；勾选项照设有图标。 |
| 碰撞 | 完成：内切三圆链 + slop/60%/12px/连续 3 tick 强制分离，实际两只 shenshen 不互穿。 |

## 16. H6 动画节拍 + core 联网 / API（2026-10-20）

| 项 | 状态 |
|---|---|
| 待机掷骰 | 完成：IDEL 圈末不再 `advance()` 续播自身，改为 `_roll_next()`；`test_petcore_playback` 钉住。 |
| 动画间隔 | 完成：`animation_gap_seconds` 改成「间隙播待机、到点掷骰」，不再冻在末帧。 |
| 可调概率 | 完成：设置「互动与物理 → 自主行为概率」四项百分比，归一化后即时生效。 |
| 手动播放 | 完成：右键「播放动画」按待机/转向/移动/点击/随机/事件分组，播完回掷骰链。 |
| 事件动画 | 完成：`events/<group>/` → `GraphType.EVENT` + `event_group`；`play_event()` 按名字/索引；余额 6 个动画不再混入随机动作。 |
| core 联网 | 完成：`petcore/net/` 代理兜底 + 五类失败分类；守卫改 allowlist + mod 不得 import。 |
| core API | 完成：`contracts/api.py`、`petcore/api/`（规格/存储/钥匙串/探测）、`HostPorts.api`；设置页「API 与账户」+ 菜单入口。 |
| 账户/额度 | 未做：P8 落地（`petcore/account/`），API 已留 `balance_protocol` 与 `ApiRequest`。 |
| 打包 | 待重出：keyring 收集与 Windows 后端进构建脚本，需重建安装包实测。 |

**计划变更**：① API/账户从「mod/worker」改为 core 能力，执行仍在 worker/mod；
② keyring 从「明确不做」改为 core 凭据存储；③ 动画播放从「clip 循环」改为
「待机一圈 → 掷骰 + 可调权重」；④ `events/*` 从随机动作池独立；⑤ 侧边栏 7→8 页。

## 17. H7 探头/击飞与安装/扩展治理（2026-10-21）

| 项 | 状态 |
|---|---|
| 击飞旋转触发 | 完成：碰撞确认重叠后先 `arm_throw_egg()` 再退出探头；低速碰撞立即回正。 |
| 飞行接管态 | 完成：`motion.thrown` 不掷骰、不响应左键；落地 `stop()` 后才回行为链；进入抛掷即绑飞行/拖拽素材。 |
| 探头素材闸门 | 完成：`probe_active`/彩蛋激活时只允许 idle/转向，探头不翻朝向。 |
| 安装器图标 | 完成：`SetupIconFile=..\assets\icon.ico`、`UninstallDisplayIcon`；主 exe `--icon`。 |
| 随附扩展位置 | 完成：改到 `{app}\mods\<id>`，应用扫描安装目录；`.trusted` 仍逐包授权，随卸载删除。 |
| 扩展管理 | 完成：左侧竖向导航 + 基础/加装分组（独立滚动），ModsPage 同口径过滤；`name_zh` 中文名。 |
| 彩蛋图片 | 完成：菜单「彩蛋图片」随机弹内置鲸鱼娘图片；设置可换目录/标题/开关。 |
| 点击音效 | 完成：默认/小黄鸭/自定义文件/文件夹；duck mp3 已补 wav；碰撞/碰边复用。 |
| 打包 | 待重出：setup 图标与 `{app}\mods` 需 `-Clean -Installer` 重建验收。 |

**计划变更**：① 账户/额度从 P7 顺延到 P8；② 安装器的选装扩展从用户数据目录改到
安装目录（卸载随应用）；③ 扩展管理从横向页内标签改为竖向导航；④ 彩蛋/点击音效
回到 core（不再只是旧主分支的彩蛋入口）。

## 18. H8 多宠/气泡/设置重组与打包回归（2026-10-21）

| 项 | 状态 |
|---|---|
| 菜单模板缺失 | 完成：构建补 `--add-data petcore/menu_templates`；`load_default()` 内置兜底；`--check` 输出 `menu_template`；合并结果落盘。 |
| 多宠推动 | 完成：轻碰不取消移动计划、不切 `thrown`；计划平移继续走；拖拽仍排除。 |
| 探头优先 | 完成：轻碰时探头按无限质量；真撞击（≥300px/s）才退出并授权击飞旋转。 |
| 子宠退出 | 完成：右键「退出这只」只收回子宠。 |
| 气泡跟随/图片 | 完成：`reposition()` + `moveEvent` 跟随；`show_image()`；彩蛋默认气泡显示。 |
| 彩蛋菜单行 | 完成：头像 + 标题 + 「请点击」整行控件。 |
| 互动与物理分页 | 完成：8 个页内分页（点击/点击台词/自言自语/拖拽/碰撞/动画/彩蛋/免打扰）。 |
| 设置归位 | 完成：菜单页加快捷启动；关于页加链接/网络/回收内存；7 个内置特性并入 Core。 |
| 扩展管理 | 完成：组头可折叠；setup 5 个归「加装」；Core 特性不再出现在扩展导航。 |
| 安装向导路径 | 完成：`DisableDirPage=no` + `UsePreviousAppDir=yes` + 旧路径预选。 |
| 差距台账 | 完成：新增 [`PETCORE-MAIN-PARITY-BACKLOG.md`](PETCORE-MAIN-PARITY-BACKLOG.md)。 |
| 打包 | 待本轮重出：`-Clean -Installer` 后验证 `_internal/petcore/menu_templates/` 与 setup 目录页。 |

**计划变更**：① P8 原定“账户与额度”顺延到 P9 起点（本轮先修交互/设置/打包回归）；
② 7 个内置 feature 从“内置 mod”改为“Core 内置特性”，设置页内嵌而非扩展导航；
③ 差距不再散落在聊天里，统一维护在 parity backlog；④ 安装器新增“目录页必须显示”
硬规则，后续改 `petcore.iss` 先看 SPEC §4.27。

## 19. H8.1 观感/菜单/弹弓/直播兼容（2026-10-21）

| 项 | 状态 |
|---|---|
| 字号 | 完成：`ui_font_size`（10–18）同步设置页/菜单/彩蛋行；`bubble_text_scale`（50–300%）气泡。 |
| 彩蛋图片大小 | 完成：`easter_egg_image_scale`（50–300%）作用于弹窗与气泡内彩蛋图。 |
| 自言自语配图 | 完成：`self_talk_image_chance`（默认 30）、目录、图片大小；失败回退文字。 |
| 菜单顺序 | 完成：彩蛋第一、播放速率默认隐藏、尾部 modern_settings → separator → quit；旧布局幂等迁移。 |
| 播放动画预览 | 完成：分类子菜单懒加载 + 代表帧缩略图（2 线程、内存/磁盘缓存、可关）。 |
| 弹弓右键 | 完成：瞄准/发射后 0.35s 内不弹右键菜单。 |
| 点击台词启动生效 | 完成：监听补挂到新子宠；点击台词 `force=True`。 |
| 设置 X 保存 | 完成：X = 保存关闭；取消/Esc = 恢复快照。 |
| 直播捕获兼容 | 完成：`stream_capture_mode` 切普通顶层 Window（含气泡），多宠每窗可捕获。 |
| 打包 | 待本轮重出：`-Clean -Installer` 后验证 setup 与 `--check`。 |

## 20. P0–P6 主分支对齐计划（2026-10-22）

审查矩阵见 [`PETCORE-FUNCTION-MATRIX-2026-10-22.md`](PETCORE-FUNCTION-MATRIX-2026-10-22.md)，
可交接任务见 [`PETCORE-HANDOFF-PLAN-2026-10-22.md`](PETCORE-HANDOFF-PLAN-2026-10-22.md)。

| 阶段 | 目标 | 状态 | 用户可见效果 |
|---|---|---|---|
| P0 | 审查、矩阵、Core/mod/worker 边界与迁移契约冻结 | 已完成 | 行为不变，但每项差距都有唯一归属和验收 |
| P1 | 每宠/全局配置作用域、当前宠物上下文、v4/slot 基础迁移和回滚 | 已完成（基础） | 子宠设置不再改错主宠，旧配置和 slot 档案可安全迁移；会话/待办/API 文件留给对应业务阶段 |
| P2 | WorkerHost、完整 Graph、GUI 角色导入、5 个离线扩展可选安装 | 已完成（基础） | 待办/报时/节日/抽签/音乐需在安装向导勾选（默认不勾）；业务运行和媒体解耦仍待 P4/P5 |
| P3 | Core 账户/余额、AI 对话、识屏、截图提问、三个全局热键 | 已完成（基础 + 本轮效果对齐） | 主分支同款双栏对话窗、看看屏幕气泡+静默入历史、框选截图问答窗与 10% 鲸蛙彩蛋、按宠 slot 派发；OCR/翻译/主动识屏留给 P4 |
| P4 | Agent、TTS、音乐歌词、天气、文件识别和提醒业务域 | 部分已实现 | a/b/c1/c2 只读联动已有代码；提醒调度、农历、48 签和写方未做；实机/外部服务验收不等同自动化 |
| P5 | 灵动岛、性能治理、更新、安装器 E2E 和系统生命周期 | 代码域已实现 / petcore 自动化通过 | P5-a2、余额、UI 几何、性能计数和更新服务已落地；真实桌面、GPU/FPS、冻结安装器和 Release 仍按报告标记为环境缺口 |
| P6 | 真双屏、混合 DPI、跨平台冒烟、发布入口切换和旧壳收敛 | 进行中（旧数据全量迁移、GPU/FPS 真探针已完成） | 新架构成为可回滚的正式发布入口；剩余：入口切换、安装器 E2E、双屏/混合 DPI、macOS/Linux |

执行纪律：每阶段先写红测试，完成代码/验证/报告后更新四份必同步文档并创建一个
可运行的本地提交。播放速率 `0.5–2.0x`、VPet LPS/PNG 和首轮 16 宠上限是明确有意差异。

## 21. 插件市场 v1（2026-10-22）

静态索引 + artifact 多源 + 下载校验 + 安装/更新/回滚已落地，作为 Core 的
`petcore/market/` 与设置页「插件市场」；契约见 [`PETCORE-MARKET.md`](PETCORE-MARKET.md)。

| 项 | 状态 |
|---|---|
| 索引 | 完成：`index.json` schema 1、ETag/disk cache、官方 raw → GitHub Contents API 备用源、自定义镜像 |
| 下载 | 完成：多 URL 回退、`.part` Range 续传、size + SHA-256 |
| 安装 | 完成：staging → manifest 交叉校验 → 备份 → 原子替换 → `market-lock.json` |
| 更新/回滚 | 完成：版本比较、最近 2 份备份、回滚后反向备份 |
| UI | 完成：扩展管理左导航「插件市场」、搜索/分类/状态、离线过期提示 |
| CLI | 完成：`petcore market list/show/install/update/rollback/doctor` |
| 官方索引仓库 | 待创建：`MerZlin/dsh-pet-extensions`（本批只冻结契约与 schema） |
| 签名/CDN/npm | 明确不做：见 `PETCORE-MARKET.md` §8 与 §10 |

## 22. P3 收口：对话 / 识屏 / 截图提问效果对齐（2026-10-22）

目标不是重写这些功能，而是把主分支已经验证过的窗口与交互搬进 Core，同时保留
`ai.chat` 的 mod 定位。

| 项 | 状态 |
|---|---|
| 主对话窗 | 完成：`petcore/ui/chat/` 移植主分支现代双栏窗（960×700、会话侧栏、Markdown 气泡、附件、浮动输入框、置顶/最小化、无边框拖拽）；`ChatController` 仍按 `(owner, slot)` 隔离 |
| 看看屏幕 | 完成：整屏图 + 前台应用描述，气泡答复；`ChatPort.record()` 静默写入该宠会话；默认不打开对话窗，`ai_look_screen_auto_open` 才主动唤出；4 秒冷却/忙碌提示 |
| 截图提问 | 完成：`ScreenPort.capture_region()` 框选（Core 覆盖层）+ `ScreenAskPort` 截图问答窗（Core UI）；mod 通过 `on_ask(question, png, history, slot) -> str` 在后台线程返回整段答复 |
| 独立彩蛋 | 完成：截图问答窗标题固定 DS 鲸鱼，等待答复时 10% 抽鲸蛙；每次请求只抽一次，独立于菜单彩蛋 |
| 按宠派发 | 完成：`run_feature_command` 把发起 slot 传给声明 `slot` 参数的扩展命令；子宠菜单打开自己的对话/截图 |
| mod 结构 | 完成：`ai.chat` 拆成 `constants/prompts/runtime/settings_page` 与包入口 `host/__init__.py`；loader 支持包相对 import；运行时无 Qt |
| 端口收口 | 完成：线程安全 `SchedulerPort`、只读 `ConfigPort`；移除未实现的 `SettingsPort`；设置页明确为受信 UI |
| 外观/跟随 | 完成：背景（默认/内置主题/本地图）、强调色、跟随桌宠、置顶四个设置入口 + `apply_appearance()` 实时预览 |
| 截图历史 | 明确：每次采集独立、只存内存、不落盘；`open()` 清空上一轮 |
| 未做 | OCR、翻译、复制动作条、主动识屏、每日预算、思考/联网过程面板（P4） |

## 23. P4-a：取数端口 / 天气 / 文件解读（2026-10-22）

| 项 | 状态 |
|---|---|
| `HttpPort` | 完成：`contracts/http.py` + `net/http.py::fetch_url` + `CoreHttpPort`；按请求路由、共享 deadline、4MB 上限、五类错误 |
| `tool.weather` | 完成：双源双出口、城市列表/地区码、失败真值表、设置页「立即查看」；真实取数 0.24–0.26s（CN）与 4.51s（open-meteo 两跳） |
| `tool.file-reader` | 完成：`on_drop`、20000 文件/2s 扫描预算、200000 字符有界读取、心跳气泡、摘录入会话；只读不删 |
| 安装器 | 完成：`mod_weather` / `mod_filereader` 两条 unchecked 任务，装到 `{app}\mods\<id>` |
| 文档 | 完成：SPEC §23、MOD-FORMAT §11、ARCHITECTURE §14/§16/§17、backlog、log `L045` |
| 未做 | 天气「明天」与逐城市子菜单（主分支也没有）、主动识屏（P4-c）、歌词取词（P4-b） |

**计划变更**：原计划天气命令含 `tomorrow`，实际只做 `show`——主分支右键也只有「显示天气」，
逐城市子菜单需要 mod 动态 populate 端口，留到 P4-c 菜单模板批次一起评估。

## 24. P4-b：SpeechPort / 报时 / 音乐歌词（2026-10-22）

| 项 | 状态 |
|---|---|
| SpeechPort | 完成：edge/豆包/MiMo/SAPI 四家；缓存、预检、重试、默认音色兜底、钥匙串 |
| 动画循环接缝 | 完成：`play_animation(loop=True)` + `release_loop()`；探头/击飞/拖拽自动解除 |
| `voice.chime` | 完成：重写到 SpeechPort，6 档日程、台词轮换、预缓存、免打扰丢弃、`voice_chime_*` 11 键 |
| `media.music` | 完成：SMTC、歌词两源（直连→代理一次）、LRC/偏移/节流、唱跳循环、四播放器启动 |
| 配置 | 完成：`speech_*` 8 键 + `voice_chime_*` 11 键 + `music_*` 10 键全部全局作用域并归一化 |
| 打包 | 完成：构建补 `edge_tts`、`winrt.windows.media.*`、`pycaw` |
| 未做 | 真实播放器下的唱跳/歌词逐行验证（本机无播放器）、豆包/MiMo 真实合成（无 Key）、口型（主分支也没有） |

**计划变更**：原计划「TTS provider 由 mod 声明字段、core 只暴露 providers」保留；
但 provider 的**传输**全部落在 `petcore/net/tts.py`，因此 `voice.chime` 不再需要
`QTextToSpeech`，PySide6 在它的运行时里彻底消失。

## 25. P4-b2 修正批次与 P4-c 待办（2026-10-23）

### 25.1 P4-b2（本轮）

| 项 | 状态 |
|---|---|
| 右键「扩展」分组 | 完成：按 `owner` 生成二级子菜单，显示 manifest `name_zh`；Core 内置特性不再混入 |
| mod 设置页层级 | 完成：`group_heading()`（H2）+ `section(level=3)`（H3）；9 个示例 mod 全部重排 |
| 工具与帮助 | 核验：DeepSeek / GitHub / 夸克三个入口已在模板与 `tool.links` 中，补回归测试 |
| mod 卸载 | 完成：`install.json` 清单逐文件删除、未知文件保留、`.trusted` 包可卸、先撤贡献再删文件、清 `mods-state.json`、草稿保护；个人数据保留 |
| 气泡 | 完成：列宽 248→360 自适应、长文本分页 + 页码点 + 滚轮翻页、副标题与正文同一面板 |
| 音乐 | 完成：`music_filter_enabled` / `music_filter_extra_ids`（按已配置播放器识别名过滤）、设置页「识别范围」「切歌与播放控制」、气泡副标题「我在唱《…》」 |
| 打包 | 完成：`[UninstallDelete] {app}\mods`，清 `__pycache__` |
| 文档 | 完成：SPEC §25、MOD-FORMAT §13、parity §12、log `L047`、PR 报告 |

**计划变更**：P4-c 不在本轮实施，整体后移为下一批次（见 25.2）。「气泡可滚动」按主分支
实际口径落为「分页 + 页码点 + 滚轮翻页」，不是滚动条。

### 25.2 P4-c（c1/c2 已实现，c3 A 全仓退出门已通过；2026-10-24 更新）

| 项 | 状态 |
|---|---|
| Core 窗口级抓屏 | **完成（P4-c1）**：`foreground_window_info()` 增 `hwnd/rect`（DWM 扩展边框、cloaked 过滤）；`capture_window()`、`dhash()`、`ScreenPort.foreground_window()` / `capture_foreground()`、`SystemPort.idle_seconds()` |
| `ai.chat` 主动识屏 | **完成（P4-c1）**：8 s 心跳；白名单（进程名 / `title:`）→ 停留 45 s → dHash 去重（阈值 8）→ 日上限/间隔/冷却/熔断；默认关、只进气泡、不落盘；12 个 `ai_proactive_*` 全局键；菜单勾选项 + 设置分节 |
| `ai.agent` 新 mod | c2 只读部分已实现（§27）；JSONL / 六态 / 节流 / 来源开关；写方安装、真实外部 Agent 端到端验收另计 |
| 提醒域转正 | 未完成：待办只有清单 CRUD；节日只有 8 个公历查询；抽签只有 6 条。调度/农历/签文扩充不能视作只差市场登记；已批准后续 C/D/E，A 整仓门禁通过前不开发 |
| 菜单模板迁移 | 部分完成（c1）：主动识屏节点已进模板并由通用合并补入旧布局；音乐、agent 节点现已实现（P4-b/c2），不再列作 c3 未实现项 |
| 验收状态 | c1 源码树真机 dry-run 通过（`process=Notepad.exe hwnd=… dhash=…`）；本机新构建被 Smart App Control 拦截（3077），安装包未在本机安装验证 |


**人工验收集中项**：真实双屏/混合 DPI、安装器全流程、真实钥匙串与余额请求、实际声卡、
角色导入界面、非 Windows 窗口行为。

## 26. P4-c1 补充：DPI 适配（2026-10-23）

| 项 | 状态 |
|---|---|
| 送模型图像封顶 | 完成：`VISION_MAX_EDGE=1024` + `ScreenPort.shrink_for_vision()`；AI 四处送图统一走它，本地 OCR/复制用原图；缩放/编码移到请求线程 |
| 菜单图标 DPR | 完成：`icon()` / `file_icon()` 按 `screen_dpr()` 物理绘制（1x/1.5x/2x → 36/54/72） |
| 动画缩略图 DPR | 完成：按 `physical_side(64, dpr)` 解码并写回 DPR |
| 口径文档 | 完成：SPEC §27、MOD-FORMAT §14、`petcore/AGENTS.md` DPI 红线 |
| 未做 | 混合 DPI 真机双屏复核；模型图是否改逻辑像素口径（取决于实测识别率）。菜单对勾已改矢量自绘，不需要 @2x 资源（L101） |

## 27. P4-c2：Agent 联动 `ai.agent`（2026-10-23）

| 项 | 状态 |
|---|---|
| Core 接缝 | 完成：`StoragePort.data_root()`、`PresentationPort.visible()`（`_PresentationAdapter`）、`PetHandle.animations()`（`GraphType.ACT` 名字）、`ai.agent` 图标 |
| 配置 | 完成：13 个 `ai_agent_*` 全局键 + 归一化（自定义条目 ≤8、key 校验、音量/冷却/间隔夹紧） |
| mod 本体 | 完成：`examples/petcore-mods/ai.agent` v1.0.0（host-only）：`protocol.py` / `tailer.py` / `links.py` / `runtime.py` / `settings_page.py` |
| 来源 | 完成：DSH 桥目录（`dsh*.jsonl`，≤8 文件）、统一 `agent-events/*.jsonl`、自定义文件/目录（≤8 条） |
| 行为 | 完成：六态映射、动作池轮换（每 3 次插播摸鱼）、过程气泡 10/8/60 s 节流、800 ms 完成确认、5 s 完成冷却、错误回合不报成功、多写者折叠、隐藏态不切动画、三档音效 |
| 菜单/设置 | 完成：`ai.agent:enabled` 勾选项进模板；设置页 H2+5×H3（含诊断与「发送测试事件」） |
| 安装器 | 完成：`mod_agent` 任务（`Flags: unchecked`）+ `{app}\mods\ai.agent` + `.trusted` |
| 门禁 | 完成：新增 `tests/test_petcore_agent_mod.py` 70 条；选定 petcore + 公共门禁 1426 passed（历史记录，非仓库全量）；ruff 全绿；`--check` exit 0（隔离 root 安装后 `diagnostics: []`） |
| 未做（c2.1） | DSH 桥接插件一键安装（不跑 pnpm、不写 DSH profile）；Claude hooks 注入；Cursor transcript / OpenCode sqlite 直读；审批/提问常驻卡片；live 回合成本统计；`ai.agent` 插件市场条目 |

**计划变更**：原 §25.2 把 c2 估成「DSH JSONL + 自定义 JSONL」，实际只做**只读**那半——
写方安装与直读适配器单独列为 c2.1。2026-11-02：`ai.agent` 1.1.0 已补齐 Claude hooks
显式安装、Cursor / OpenCode 只读直读与第四个 rollout Agent；Core 仍不出现 Agent 名。
`reminder.todo` / `fun.festival` / `fun.divination` 已是随包可选扩展并带 `.trusted`，但这不代表业务齐全。
待办调度、农历节日及 48 签仍未实现，不能以市场条目或文档替代实现验收。
前序质量审查不做这些新能力。2026-10-24 用户随后批准 c3：A 质量阻断 → B 通用接口 → C 待办 → D 节日生日 → E 48签 → 功能扩展仓库分发。当前只执行 A，后三域与分发尚未实现。

## 28. P5-a1：灵动岛 v1（2026-10-23）

| 项 | 状态 |
|---|---|
| 零 Qt 契约 | 完成：`petcore/island/segments.py`（状态词表、盒宽自测量、字段盒、两级告警 + 3pt 滞回）；移植主分支同名单测 40 条 |
| 零 Qt 布局 | 完成：`petcore/island/layout.py`（段落位、双盒单位、收缩流水线、`+N` chip、三分区锚点）；17 条单测 |
| 采样 | 完成：`petcore/platform/sysmon.py`（CPU/内存 1 Hz）、`netmon.py`（速率差分 + EMA，5 列定宽；**无延迟探测**——socket 只许在 `net/`）；33 条单测 |
| 窗口 | 完成：`petcore/ui/island.py`（44px 胶囊、三套配色、五档强调色、状态点、展开卡片、拖动换位、15s 自动收起、隐藏即停采样） |
| 配置 | 完成：13 个 `island_*` 全局键 + 归一化（指标白名单/去重、透明度夹取、坐标校验） |
| 菜单/设置 | 完成：`dynamic_island` 勾选项（`pet_controls` 内、`edge_probe` 之后）+ 「外观与交互 → 灵动岛」分节（改动实时生效） |
| 门禁 | 完成：新增 108 条测试；选定 petcore + 公共门禁 1541 passed（历史记录，非仓库全量）；ruff 全绿；`--check` exit 0 |
| 真机 | 完成：真 DPI(150%) 抓图核对（深色胶囊 434×44 逻辑 / 651×66 物理、展开 349×205），9 秒整程序冒烟 0 错误 |
| 未做（P5-a2） | 停靠细条、动效/声纳、卡片操作按钮与最近消息、余额主信息、第三方段注册面、GPU/FPS 段、灵动岛聊天与碰撞 |
| 后续补记（L096/L099/L101/L103） | 停靠细条与几何动画、状态点五态/声纳/呼吸/内容淡化、去头标与消息开关、**岛 × 桌宠碰撞（同步硬墙 + 抛掷反射 + 拖岛拍飞）与可选的 C 盘剩余段** 均已落地；仍未做：岛聊天、「隐藏后由岛唤起对话」 |

**计划变更**：原 §20 把 P5 估成「灵动岛 + 性能治理 + 更新 + 安装器 E2E」，实际按价值拆批：
P5-a1 只交**能天天看见的骨架**（胶囊 + 3 段 + 卡片），动效与扩展面后置；性能治理与安装器
E2E 仍在 P5-b/P5-c。


## 2026-10-24 已有能力质量审查

本轮仅修已有行为，不增加功能。实现状态、自动化结果、Windows 单屏源码实机及
未验证范围分别见 [质量审查报告](PR-REPORT-PETCORE-QUALITY-REVIEW-2026-10-24.md)。
报告中的仓库全量失败/崩溃不能用 petcore 选定测试通过抵消；门禁收口前不启动下一阶段。


## 2026-10-24 P4-c3 A：当时执行状态（后续见E）

用户已批准进入仓库分发准备与完整提醒域，但必须先通过整仓正常退出门禁。
A已完成旧设置退出、菜单所有权、Pillow兼容与隔离环境修复；完整pytest 9716passed/20skipped、exit0，
高负载三遍各143passed，源码原生探针正常退出；性能及内存留存限制见
[本批报告](PR-REPORT-PETCORE-P4C3-A-2026-10-24.md)。B–E与分发未开始，Core/API/mod版本未调整，
没有候选ZIP、公开Release或已验收安装包。前序质量报告保留当时的失败/崩溃结果。


## 2026-10-24 P4-c3 B当时执行状态（后续见E）

B已实现按slot展示/真实通知交付、DND跨扩展单摘要、共享有界语音及实际播放完成/生命周期取消。
本批整仓9729passed/20skipped/14warnings、pytest正常退出；高负载三遍32passed、Windows隔离源码及性能证据通过，不沿用A结果。
C待办、D节日生日、E48签与分发尚未实施。详见[B报告](PR-REPORT-PETCORE-P4C3-B-2026-10-24.md)。


## 2026-10-24 P4-c3 C执行状态

C已实现按宠物持久化调度/面板/安全迁移；关联95passed。Core候选0.2.0/API1、todo1.1.0，未发布。性能/实机与累计全仓待C–E冻结重验；D/E尚未实现。详见[C报告](PR-REPORT-PETCORE-P4C3-C-2026-10-24.md)。


## 2026-10-24 P4-c3 D执行状态

D已实现纯历法/节气/浮动节日、slot调度/去重和本地MM-DD生日；自动/语音默认关闭，手动查询不授予自动授权。关联126passed。fun.festival1.1.0未发布；综合证据待C–E冻结，E尚未实施。见[D报告](PR-REPORT-PETCORE-P4C3-D-2026-10-24.md)。


## 2026-10-24 P4-c3 E与提醒域当前状态

以上A–D条目是各批次当时快照，不代表仍缺提醒域。当前Core候选0.2.0/API1，todo/festival/divination均1.1.0、chime1.1.1；C完整待办、D历法/节气/生日、E48签/提示/真实结束揭签/预热及让位均已实现。

| 层次 | 当前结论 |
|---|---|
| 已实现 | 按宠物配置/待办调度、节日生日默认自动关闭、48签当天全宠共享、版本化迁移、DND摘要与生命周期取消 |
| 自动化 | 最终整仓9837passed / 20skipped / 15warnings / 1565.27s / exit0；petcore/公共门1662passed、CPU高负载3×86passed、Ruff通过，详见E报告 |
| Windows源码 | 隔离本地安装四扩展、双宠、隐藏通知、设置保存取消、生日、真实PCM播放后揭签、30及120轮生命周期、正常退出已验证 |
| 候选包/远程上架 | 未执行，待分发阶段；没有extensions-index或公开Release，不声称安装包/远端下载链路通过 |
| 未实现 | 角色素材下载/v6、Agent写端、灵动岛二阶段、Core自更新、节日专属动画；不纳入本轮 |

详细数据契约见[MOD-FORMAT §17](PETCORE-MOD-FORMAT.md)，收口证据及未验证范围见[E报告](PR-REPORT-PETCORE-P4C3-E-2026-10-24.md)。本轮遵照用户要求执行至E，不越过边界自动发布。


## 2026-10-24 下一阶段详细交接（执行未开始）

A–E收口后的功能扩展分发按[详细执行计划](superpowers/plans/2026-10-24-petcore-p4c3-distribution.md)的T00–T09推进；继任模型可使用最多三个互不重叠写集的worker，主模型锁定公共接口、串行集成与最终验收。本次只编写交接并按用户授权提交/推送A–E及文档，不构建候选、不上传Release、不合并main。交付记录见[交接报告](PR-REPORT-PETCORE-P4C3-HANDOFF-2026-10-24.md)。


## 2026-10-24 P4-c3-D1 执行状态（T00–T07，进行中）

用户批准 D1 = T00–T07；T08 冻结包与 T09 发布门禁留 D2（SAC/签名决策与整仓耗时不与代码冻结混批）。

| 任务 | 状态 | 证据 |
|---|---|---|
| T00 基线 | 完成 | `.scratch/petcore-p4c3-distribution/baseline-20261024-202323/`（status/numstat/untracked/freeze/source-sha256/env.json，SAC=1）；before 性能 `before-perf.json`（download 32.739 / resume 33.141 / extract 12.993 / install 38.512 / page 13.369 ms，3 轮 15+60 s） |
| T01 契约冻结 | 完成 | `contracts/archive.py`、`contracts/market.py`、`contracts/http.py`、`net/market_transport.py`、`install_package(origin=)`；契约 11 passed、市场族 63 passed、ruff 全绿；MOD-FORMAT §18 |
| T02 逐跳传输 | 完成（`94d80650`） | 206 对齐/416 两义/读流预算/跳转三重约束；25 条安全回归；新增 `allowed_hosts` 支持 GitHub Release | 
| T03 解包预算 | 完成（`359446b5`） | `open_pack/install(limits=)`、预检条目与声明展开、计数写入防造假；9 条回归 |
| T05 候选构建器 | 完成（`34a247cb`） | 白名单可复现 ZIP + 独立校验器；4 包实测 19169/41454/9655/17522 字节，verify 4/4 |
| T04 授权/事务 | 完成 | 市场安装接 `fetch_artifact` 与 `limits`；能力/api/core 四类交叉校验；锁失败回滚；`allow_loopback_http` 契约；全量 1738 passed |
| T06 索引/CLI/UI | 完成 | 根 `extensions-index.json`（四包 blocked，size/sha 已固定）；`file:` 索引与 artifact 可离线用且不回落公网；市场页 dispose 持有运行中线程；22 passed |
| T07 真实装卸回滚 | 完成 | 真实 ZIP + `file:` 索引：未授权零落盘、四包安装并真实 `FeatureManager` 加载、合成升级/回滚/再升级且数据 SHA 不变；5 passed / 3.20s；30× 装卸 + 30× 页开关无 staging/part 残留、线程恒 4 |
| T08 冻结包 | 完成 | `-OutputRoot`/`-PythonPath` 隔离构建 + iss 源目录覆盖；exe/setup 均开发证书签名 Valid；冻结包 `--check` 715 ms / diagnostics []，冻结 CLI 装四包全 exit 0；Inno 96.3 s，setup 261.7 MB |
| T09 发布前门禁 | 完成（未发布） | before/after 无回退（下载 32.74→32.49 ms、p95 38.77→34.23）；整仓 9941 passed / 18 skipped / exit 0；安装器四组合矩阵全通过；发布手册 [PETCORE-EXTENSIONS-RELEASE.md](PETCORE-EXTENSIONS-RELEASE.md) 固化流程与回滚 |

范围不变：别把 T01 的契约读成「分发可用」；解包预算与逐跳校验尚未生效。

## 2026-10-25 重构收尾执行状态

本轮执行了市场安全/生命周期阻断修复、`WorkerHost` 运行时边界和渲染 DPR 基础收口，并完成了候选索引状态与证据文档校正。对应提交为 `9e973b53`、`e371f92c`。

| 状态 | 范围 | 说明 |
|---|---|---|
| 已实现并有自动化证据 | 市场线程退出、下载断点续传、远程 feature 解包预算、`file://` 授权、回滚结果语义、candidate/published 索引状态、WorkerHost 握手/代次、基础 DPR | 见本轮聚焦测试与最终报告；未将离线测试等同于远程 HTTPS/实机通过 |
| 已实现但未完成本轮实机门禁 | 媒体完整迁移、混合 DPI/双屏、多宠跨屏、候选安装器、默认入口切换 | 继续保留为待验收，不提前标记完成 |
| 明确不在本轮 | GitHub Release 上传、真实账号、Agent 写端、灵动岛二阶段、角色 v6 | 保持既有边界 |

最终入口切换的前置条件仍是全量 pytest 正常退出、候选包可在隔离目录安装/更新/回滚、Windows 实机证据齐全；本轮不满足时不切换默认入口。

## 2026-10-25 重构收尾实现状态

本轮按“运行时与迁移 → 媒体/角色边界 → 已有 parity 设置与入口 → 证据收口”分批提交。验证策略按最新交接要求收窄为 `petcore`：不再重复运行旧 `pet` 实现的整仓门禁；此前在范围收窄前已经完成的一次整仓结果只作为历史证据保留。

| 状态 | 范围 | 证据 |
|---|---|---|
| 已实现 / 自动化通过 | 迁移诊断、session/todo schema、WorkerHost 有界退出、角色 ZIP 导入、点击音效 token、彩蛋设置、余额异步入口、GraphType 懒加载菜单 | `da107abe`、`c456ff56`、`42856889`；`tests/test_petcore_refactor_final.py` 及关联 petcore 测试 |
| Windows 源码检查通过 | `python -m petcore --check`，隔离数据目录和默认检查均 exit 0，diagnostics 为空；Qt 探针为 1 屏/DPR 1.0 | `.scratch/petcore-refactor-final/`；不等价于双屏或候选包验收 |
| 已有但仍需真实环境验证 | 混合 DPI/双屏、媒体跨屏、外部余额/TTS、安装器候选包 | 当前机器缺少双屏、PyInstaller 和真实服务条件 |
| 明确未做/不在本轮 | Agent 写端、灵动岛第二阶段、角色远程下载、VPet PNG/LPS、Core 自更新、公开 Release/CDN | 继续保留原有边界 |

本轮不切换 `python -m petcore` 默认入口，不将兼容层删除，也不把“自动化通过”改写成“跨平台/候选包已通过”。

## 2026-10-25 摸头互动（petcore）

| 状态 | 结论 | 证据 |
|---|---|---|
| 已实现 | `HeadPatTracker`、头部命中/透明像素过滤、连续往返判定、默认/自定义/全部单次动画池、动作接管与生命周期清理 | `petcore/engine/head_pat.py`、`petcore/ui/pet_window.py` |
| petcore 自动化测试通过 | `test_petcore_*.py` 全部 **1490 passed**；Ruff **All checks passed**；`python -m petcore --check` 返回 0 | `tests/test_petcore_head_pat.py`、交互/设置回归；本机命令输出见 PR 报告 |
| Windows 实机通过 | 尚未执行真实桌面鼠标摸头交互；offscreen Qt 仅验证事件与状态接缝，不等同实机通过 | 需要在隔离配置目录执行真实窗口探针 |
| 明确未做 | 不改旧 `pet` 实现，不新增线程、定时器、网络请求或菜单入口 | 本轮范围约束 |
## 2026-10-25 P5 收口状态

本阶段实现了 petcore P5 的五个代码域：灵动岛 P5-a2、按宠物点击显示余额、菜单/设置图标与下拉框几何修复、运行时性能计数与采样治理、检查更新及冻结 Windows 安装版自更新边界。

| 状态 | 范围 | 证据与限制 |
|---|---|---|
| 已实现 | 灵动岛展开卡片、消息队列、余额刷新、第三方段注册、折叠停采样、避让、GPU/FPS 真探针 | `petcore/ui/island.py`、`petcore/island/segments.py`、`petcore/platform/{gpu,gpu_monitor,fps}.py`、`petcore/ui/probes.py`；**停靠细条 `edge_dock` 未做**（只有 12 px 贴边吸附），弹簧动效/声纳/呼吸、岛聊天与碰撞仍未做 |
| 已实现 | 左键/右键/灵动岛共用 `BalancePresenter`，按 slot 单飞、缓存、迟到结果拒收 | `petcore/account/presenter.py`、`petcore/ui/app.py`、`petcore/ui/pet_window.py` |
| 已实现 | 菜单/托盘/设置导航显式图标尺寸、QSS 图标列宽、QComboBox 字体度量安全边距 | `petcore/ui/menu_model.py`、`petcore/ui/menu_style.py`、`petcore/ui/settings/dialog.py`、`style.py` |
| 已实现 | 1 Hz 指标采样、隐藏/关闭停采样、UI 合并计数、缓存/网络/磁盘/worker 计数及 15s+60s×3 探针 | `.scratch/petcore-p5-complete/performance-2026-10-25.json`；当前机器为单屏 offscreen |
| 已实现 | HTTPS manifest、ETag/缓存、镜像回退、Range/断点、大小/摘要校验、取消；源码/非 Windows 禁止原地替换，冻结 Windows 仅启动独立安装器 | `petcore/update/`、`petcore/cli.py`；未上传 Release，当前 venv 未安装 PyInstaller 模块 |
| petcore 自动化通过 | Ruff、全部 `tests/test_petcore_*.py`、P5 聚焦族、高负载 3 遍、`python -m petcore --check` | 本次报告 `PR-REPORT-PETCORE-P5-COMPLETE-2026-10-25.md` |
| Windows 实机可见 UI | 未执行本轮真实桌面窗口/安装器交互 | 只执行了 Windows 进程级 `--check` 与 offscreen Qt；不可把它写成实机 UI 通过 |
| 环境缺口 | GPU/FPS 原生探针、冻结安装器、双屏混合 DPI、真实余额服务、公开 CDN/Release | 已实现降级和安全拒绝路径，待具备条件时单独验收 |

## 2026-10-26 P6 进展：旧数据全量迁移 + GPU/FPS 真探针

| 项 | 状态 | 落点与边界 |
|---|---|---|
| 全局配置 / slot / 活跃集合迁移 | 已完成 | `petcore/migration/config_data.py`；只补缺不覆盖，slot0 并入全局；旧源不改 |
| 会话迁移 | 已完成 | `petcore/migration/sessions.py`；ISO 时间 → epoch float（新 schema 只认 float） |
| 待办迁移 | 已完成 | `petcore/migration/todos.py`；`todo_items.json` → `reminder.todo` v2，写出前后各用扩展 `validate()` 校验 |
| API 规格 / 凭据迁移 | 已完成 | `petcore/migration/accounts.py`；`chat.providers` → `api.json`，钥匙串 `dsh-pet-standalone` → `dsh-pet-core`；密钥不进报告 |
| 迁移幂等 / dry-run / 回滚 | 已完成 | `<root>/migration.json` 标记、`petcore migrate --dry-run/--rollback`、`--check` 的 `migration` 段 |
| 旧配置键级扩表 | 未做 | 旧 `config.json` 99 个未映射键仍在别名表外；补 `_LEGACY_KEY_ALIASES` 即生效 |
| GPU 探针（PDH） | 已完成 / 本机实测 | `petcore/platform/{gpu,gpu_monitor}.py`；本机 2–3.5 ms/拍，冷启 283.6 ms |
| FPS 探针（PresentMon） | 代码完成 / 本机未取数 | `petcore/platform/fps.py`、`fps_permission.py`；本机 `access denied`，需 Performance Log Users + 重新登录 |
| 探针接线与设置 | 已完成 | `petcore/ui/probes.py`，按需启停；设置放开 GPU/帧率两段并加授权行 |
| 打包随附 PresentMon | 脚本已改 / 未重建 | `scripts/build_petcore.ps1` 带 `resources/presentmon` 并过 `verify_presentmon.py` |
| 发布入口切换 | 未做 | 前置条件见 SPEC §「切换前置条件」；本轮不动 |
| 安装器 E2E、真双屏/混合 DPI、macOS/Linux | 未做 | 需另一台机器与真实桌面条件 |
