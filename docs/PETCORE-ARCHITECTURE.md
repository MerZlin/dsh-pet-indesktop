# petcore 架构

> 基线：分支 `feat/petcore-vpet-architecture`，2026-10-20。本文描述首版 core 的分层、契约与边界，不代替实现。
>
> 需要一张能点开的全局图（分层、启动路径、插件流水线、端口注入状态、小游戏 mod 可行性）：
> [`PETCORE-ARCHITECTURE-MAP.html`](PETCORE-ARCHITECTURE-MAP.html)。图是导读，本文与
> [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md) 才是字段与契约的真源。

## 1. 目标

把 dsh-pet 从单个 `pet/` 包重构为 **core + mod**：

- core 只负责让桌宠活着、可交互、可管理扩展；
- 角色与素材是 content 包，功能是 feature 包；
- 新 core 与旧 `pet/` 并存，按功能域逐步迁移，最终替换发布入口。

## 2. 分层

| 层 | 目录 | 职责 | 依赖约束 |
|---|---|---|---|
| 引擎 | `petcore/engine/` | 动画目录、动画链、行为决策、位移/夹取、状态 | 仅标准库 |
| 契约 | `petcore/contracts/` | 端口、内容模型、贡献类型、能力、worker 协议 | 仅标准库 |
| 媒体 | `petcore/media/` | WebP 帧序列 / WebM / GIF 后端、库、音效 | Qt；允许适配 `pet.frameseq_clip` / `pet.webm_clip` / `pet.library.GifClip` |
| 内容 | `petcore/content/` | 旧素材只读适配、mod 角色构建 | 允许 `pet.frameseq_provision` 做帧序列采纳判定 |
| 联网 | `petcore/net/` | 唯一 HTTP 出口：系统代理兜底、失败分类 | 仅标准库 |
| API | `petcore/api/` | 服务规格、`api.json`、钥匙串凭据、文本探测 | 可用 `net/`；密钥不进磁盘 |
| 账户 | `petcore/account/` | 登录态、额度查询（P7） | 可用 `net/` |
| 加载器 | `petcore/mods/` | 发现、校验、启停、安装/卸载、诊断 | 标准库 |
| 平台 | `petcore/platform/` | 逐像素穿透、屏幕/多屏、会话结束、开机自启 | Qt + ctypes |
| 外壳 | `petcore/ui/` | 透明窗、渲染、气泡、菜单布局与编辑器、托盘、设置 | Qt |
| 入口 | `petcore/cli.py` | 启动、`--check`、`mods` 子命令 | 全部 |

`tests/test_petcore_architecture.py` 机械守卫：

- `engine/` 不得 import PySide6/PyQt/shiboken；
- `contracts/` 只允许标准库；
- 直接 import `pet.*` 只允许出现在 `petcore/media/` 与 `petcore/content/legacy.py`；
- 网络库（`urllib/http/socket/requests/aiohttp/httpx`）只允许出现在 `petcore/net/`、`petcore/api/`、`petcore/account/`；mod 不得 import 这三个包。

## 3. 启动路径

```
python -m petcore
  → cli.main
  → Config.load(data_root)
  → QApplication
  → QLockFile 单实例
  → CoreHost
       ├─ legacy.load_all()            只读 assets/characters
       ├─ mods.loader                  content/feature 包
       └─ CharacterSpec 列表
  → PetWindow.start(character)
       ├─ GraphCatalog + AnimationPlayer + BehaviorDirector
       ├─ ClipLibrary（frameseq → webm → gif）
       ├─ 位置恢复（platform/screens）
       └─ 首帧 → 显示
  → TrayIcon + MenuModel
  → SessionWatcher + 屏幕变化监听
  → QApplication.exec()
```

`--check` 走同一条内容链路但不创建窗口，输出角色、动画数量、每段 clip 实际采用的后端、诊断与配置快照，便于实机取证。

## 4. 关键契约

- `contracts/content.py`：`AnimationSpec`、`CharacterSpec`、`Clip`、`ContentProvider`
- `contracts/host.py`：`PresentationPort`、`SchedulerPort`（任意线程可调，回调回 GUI 线程）、`AudioPort`、`CredentialsPort`、`ConfigPort`（只读）、`StoragePort`、`CommandPort`、`MenuPort`、`WindowsPort`、`InputPort`、`SystemPort`、`WorkerPort`、`CapabilityPort`。设置页是 Core 托管的受信 UI，直接拿 `ScopedConfig`，不再有未实现的 `SettingsPort`。
- `contracts/feature.py`：`FeatureDefinition`、`FeatureContext`、`Runtime`
- `contracts/contributions.py`：`Contribution`、`SettingsPageSpec`
- `contracts/capabilities.py`：能力声明与 `CapabilityDenied`
- `contracts/worker.py`：`petcore-worker/v1` JSONL 信封

feature 包的生命周期是 `discover → validate → enable → start`，`stop/close` 幂等并撤销订阅；不承诺热卸载。

## 5. 数据与状态

| 路径 | 内容 |
|---|---|
| `%APPDATA%/dsh-pet-core/config.json` | 角色、缩放、位置/屏幕比例、跨屏、音量、菜单偏好、`menu_layout` |
| `%APPDATA%/dsh-pet-core/mods-state.json` | 停用集合 |
| `%APPDATA%/dsh-pet-core/mods/<id>/` | 用户安装的包 |
| `%APPDATA%/dsh-pet-core/feature-data/<owner>/` | feature 包私有数据 |
| `%APPDATA%/dsh-pet-core/logs/petcore.log` | 轮转日志 |
| `{app}/mods/<id>/` | 安装器随附的包（仅冻结包；带 `.trusted`，卸载随 `{app}`） |
| `<repo>/mods/<id>/` | 开发期包源 |
| `assets/characters/<id>/` | 旧素材只读源 |

首版只读导入旧 `%APPDATA%/dsh-pet-standalone/config.json` 的 `character/scale/rx/ry/screen_name` 与 `mouse_through`，绝不写回。

## 6. 与 VPet 的对应

| VPet | petcore |
|---|---|
| `VPet-Simulator.Core` | `petcore/engine` + `contracts` |
| `VPet-Simulator.Windows.Interface` | `petcore/contracts` |
| `VPet-Simulator.Windows` | `petcore/ui` + `platform` |
| mod 目录 + `info.lps` | `mods/<id>/mod.json` |
| `GraphType/AnimatType/ModeType` | 同名枚举 |
| `PNGAnimation` 文件名尾数计时 | `Clip` 后端注册接口（预留，未实现） |
| 单窗口、屏幕归属 | `platform/screens`（屏名 + 比例 + 热插拔） |

只借分层与寻址模型，不兼容 LPS 格式，也不导入 VPet 素材。

## 7. 菜单布局

- 默认模板：`petcore/menu_templates/petcore-default-v1.json`；schema 与主分支 `modern-default-v1` 同构（`action` / `separator` / `submenu`，子菜单最多一层）。
- 解析：`petcore/ui/menu_layout.py` 负责校验、隐藏/不可用过滤、分割线归一化、旧默认动作合并、mod 动作落入「扩展」子菜单、旧 `menu_hidden/menu_order` 迁移。
- 渲染：`MenuModel` 按解析后的节点构建 `QMenu`；`petcore/ui/menu_style.py` 提供浅/深 QSS，`petcore/ui/icons.py` 绘制图标；托盘与右键共用。
- 编辑：设置 →「菜单」使用 `MenuEditor`，支持拖拽排序/入子菜单、显隐、分割线、新建/删除子菜单、别名、内置图标或本地图片图标（≤5 MB，contain/cover）、重置默认、预览；保存写 `config.menu_layout` 后立即重建菜单。
- 默认模板中 `size` 为 `visible: false`（P1-C 起）：大小调整默认收进设置 →「外观与交互」→「大小」，菜单编辑器仍可重新勾选；用户在菜单里取消隐藏后，子菜单尾部的「自定义大小…」会打开设置的外观页。
- 旧默认布局的一次性迁移由 `migrate_default_size_visibility()` 负责：只有「去掉 mod 动作后 Core 结构与旧默认完全一致」的 `menu_layout` 才会把 `size` 改为隐藏；用户改过顺序、显隐、别名或图标的布局保持原样。
- 菜单/设置一一对应（硬规则）：右键菜单里能看见、能调整的每个 Core 动作，必须在设置里有一个可到达的入口。映射表在 `petcore/ui/settings/coverage.py`，`tests/test_petcore_settings_coverage.py` 守住；新增菜单项却没给设置入口会直接红。`modern_settings`（设置宿主本身）豁免。mod 贡献的菜单项由 mod 自己的设置页负责说明。
- 扩展功能只按注册出现（L108）：属于扩展的菜单项由该扩展的 `Contribution(kind="menu")` 注册成 `<owner>:<id>`，默认布局按这个 id 摆位；没装扩展就不注册、不出现。`AI 对话 / 看看屏幕 / 截图提问` 以前是 `core_items()` 里写死的特例（没装 `ai.chat` 也在），现已改成注册项，旧布局由 `migrate_p11_menu()` 改写 id。
- 菜单层级：桌宠与气泡同属一个置顶层（气泡是桌宠窗口的**从属窗口**），菜单统一走 `petcore/ui/menu.py::show_context_menu`——桌宠置顶时菜单也置顶，否则它会被后 `raise_()` 的气泡压住。扩展侧通过 `host.show_context_menu()` 复用同一规则（扩展不许 import `petcore.ui`）。

## 8. 设置与窗口交互

- **非模态**：设置与关于都是 `NonModal` 的独立窗口（`SettingsDialog` / `AboutDialog`），不阻塞桌宠的拖动、点击和滚轮；`CoreHost` 持有唯一实例，重复打开时复用并置前。
- **实时预览 + 取消回滚**：设置页控件变化先写内存配置，经 16 ms 合并后调用 `CoreHost.apply_live_settings()`，把角色/缩放/置顶/穿透差异应用到桌宠；`set_*` 的 `persist=False` 分支不落盘。只有「保存」调用 `config.save()`；「取消 / 关闭」恢复打开时的配置快照并重新应用。
- **大小范围**：`SCALE_MIN = 0.10`、`SCALE_MAX = 3.00`；设置页滑条与数字框都是 10–300%，四档预设（50/72/85/100%）保留。`Config`、`PetState`、`PetWindow` 共用 `clamp_scale()`，旧配置超过 300% 会被夹到 300%。
- **窗口定位**：设置/关于打开时由 `petcore/ui/placement.py` 放到桌宠旁边并夹在可用屏幕内，避免被置顶桌宠完全遮住。
- 菜单编排、扩展启停仍是显式保存/立即执行语义，不强行实时。

## 9. 贴边、摆放与身体框

- **唯一锚点**：角色 manifest 的 `body_box`（640×360 源像素）是贴边、摆放、恢复、比例的定位基准；`PetWindow._body_local` 缓存 `body_box × scale` 的窗口局部矩形。**不允许再用整窗 `content_size()` 直接做贴边钳制**——那会把素材透明边当成身体，桌宠会离屏幕边缘留一大截。
- **单屏**：`clamp_body()` 要求身体框完整落在可用区内，窗口透明边允许越界；左右身体贴边时窗口可以部分出屏。
- **多屏**：`clamp_body_to_screens()` 先判断身体是否逐点落在各屏并集内（跨屏缝合法），落进屏间空洞才按位移最小的屏收口。
- **恢复**：位置同时保存 `pos_x/pos_y`、旧 `rx/ry` 与新 `body_rx/body_ry`；有 `body_rx/body_ry` 时优先用「身体合法窗口范围」还原，旧配置回退旧比例，不会跳位。选屏用身体矩形而不是整窗。
- **右下角/主屏落位**：身体右缘距可用区右缘 `CORNER_MARGIN`，身体底贴可用区底；不再按画布右下角算。
- **回退**：`body_box` 缺失（或角色包未声明）时回退整窗，行为与改造前一致。
- **边缘探头（P1-F）**：用 `body_box` 做贴边判定，露出比例的分母是身体框绕帧中心旋转后的投影宽度；`probe_body_bounds()` 左右各放宽一个身体宽，并必须减去 `body_local.x`。`head_box` 已解析但当前未参与曝光计算，留给后续更精细的露头锚点。

## 10. 边缘探头（P1-F）

- 纯几何在 `petcore/engine/edge.py`：`edge_side_at_rest`、`rotated_bounds`、`probe_target_x`、`probe_body_bounds`；`engine/` 仍零 Qt。
- 状态机在 `petcore/ui/edge_probe.py`：`OFF/ENTERING/PEEKING/STRAIGHTENING/STRAIGHTENED/RETURNING`，16ms `PreciseTimer`，时钟可注入；拖拽/隐藏/换角色/换缩放/屏幕变化取消。
- 窗口：`PetWindow.set_probe_pose()` 保存角度/曝光；`paintEvent` 绕帧中心旋转；`is_transparent_at()` 先反向旋转再做 alpha 判定；`move_probe_to()` 允许窗口部分出屏。
- 曝光：常驻 `0.55`、点击拉直 `0.82`、5s 后回到 `0.55`；角度 ±45°。
- 开关：`edge_probe_enabled`（默认关）→ 设置「外观与交互」+ 右键「桌宠控制」；`coverage.py` 登记。
- 边界：非 shenshen 的大身体框若旋转投影超出窗口，先由曝光/角度钳制并在日志记诊断；macOS/Linux 的真实窗口出屏行为未实机验证。

## 11. P1-B 核心体验

- **全局快捷键**：`petcore/platform/hotkey_spec.py` 纯文本解析；`hotkeys.py` 用 Windows `RegisterHotKey` + `WM_HOTKEY`，只绑定 `toggle_pet`；设置 →「显示/隐藏快捷键」实时重注册，注册失败日志告警不假装成功。
- **开机自启**：`autostart.command_text()` 区分源码（`pythonw -m petcore`）与 `sys.frozen`（直接 exe）；CLI `petcore autostart on|off|status`。
- **多实例**：`petcore --instance NAME` 使用独立数据根 `dsh-pet-core-<NAME>` 与独立 lock；每个实例一只宠。单进程多宠由 P2 的 `PetManager` 补齐（见 §13）。
- **打包入口**：`packaging/petcore_entry.py` + `scripts/build_petcore.ps1`（PyInstaller onedir，含 assets 与 `pet.*` 媒体适配）+ `packaging/petcore.iss`（Inno Setup）。P2 已在 `.venv` 安装 PyInstaller 6.22.3、在 `tools/InnoSetup` 便携安装 Inno Setup 6.7.3，并实际构建 onedir 与安装包。
- **首跑供给**：`petcore/media/provision.py` 复用 `pet.frameseq_provision.provision_once`（WebM→WebP、世代退役/GC）；`run()` 启动后 daemon 线程跑一轮（60s 起手上限、1 个迁移配额）；CLI `petcore provision <character>` 可同步跑。

## 12. P1-D 角色包导入

- `petcore/content/character_pack.py`：`validate_character_source()` + `build_character_pack()`；source 可以是 `manifest.json` + `videos/`（或媒体在根目录）的素材目录。
- 产物是标准 content mod：`mod.json` 的 `id=character.<id>`、`kind=content`、`character.media="videos"`，并写 `body_box/head_box/canvas/feet_y` 与可选 `move_strides/text_clips/phrases`；复制/硬链接媒体与 `frameseq/`。
- CLI `petcore pack-character <source> --out <dir> [--id] [--name] [--version] [--copy]`；`pack-legacy` 内部改为同一构建器。
- 安装仍走 `petcore mods install`；冲突/校验沿用 `mods/manifest.py` 与 `mods/install.py`。

## 13. P2 交互与多宠

- `petcore/engine/physics.py`：弹簧、松手初速、抛掷、弹弓参数（纯标准库）。
- `petcore/engine/squash.py`：Q 弹几何；`engine/spin.py`：黄金回旋缓动；`engine/collision.py`：椭圆近似碰撞；`engine/feeding.py`：投喂统计预算。
- `petcore/ui/effects.py`：Q 弹/回旋/探头击飞角度；`ui/motion.py`：拖拽/抛掷/碰边；`ui/slingshot.py`：弹弓瞄准；`ui/feeding.py`：拖文件反馈。
- `PetWindow` 统一 `paintEvent` 变换与 `is_transparent_at()` 逆变换（先 Q 弹、再旋转），避免画面与命中错位。
- `petcore/ui/pet_manager.py`：slot 0 主宠 + slot 1–15 子宠；`config-pet-N.json` 独立配置；16ms 碰撞 tick；`active_pets` 恢复。
- 菜单：`animations_hub`、`playback_speed`、`drag_physics`、`no_move`、`golden_spin`、`spawn_pet`、`clear_spawned_pets`；默认模板按主分支顺序并给 `extensions_hub` 留位。
- 关于：`AboutPage` 是唯一关于入口，`AboutDialog` 已删除。

## 14. 内置 mod 与端口

- `petcore/builtin/features/<id>/mod.json` + `host/factory.py`；`FeatureManager.load_builtin_features()` 扫描并加载，受信任、不受 `allow_feature_mods` 限制。
- `petcore/ui/ports.py`：`QtScheduler`、`AudioAdapter`、`StorageAdapter`、`PetRegistry`、`InteractionAdapter`、`QuietMode`；`HostPorts` 新增 `pets`/`interaction`/`quiet`。
- P4-a 新增 `HostPorts.http`（`CoreHttpPort`）：唯一普通取数出口；路由策略、错误分类与
  体积上限都在 `petcore/net/http.py::fetch_url`，mod 只给 URL/参数/路由偏好。
- P4-b 新增 `HostPorts.speech`（`CoreSpeechPort`）：合成、缓存、预检、密钥全在
  `petcore/speech/` + `petcore/net/tts.py`；动画循环用 `play_animation(loop=True)` /
  `release_loop()`，探头/击飞/拖拽接管时自动解除。
- `FeatureContext.host` 暴露宿主（只读使用），使 mod 能读取共享配置；mod 仍不应直接 import `pet.*`。
- 菜单贡献统一 `<owner>:<contribution>`；每个有菜单的 mod 必须有设置页。

## 15. 设置页聚合（P3 收口）

- `petcore/ui/settings/tabs.py` 的 `SettingsTabContainer` 复刻主分支页内分页：`#settingsTaskTabBar` + `#settingsTaskTab` + `QStackedWidget`。
- P8 起 `ExtensionsPage` 仍是**左侧竖向导航 + 右侧内容**（`#extensionsNav`），但分组改为
  「扩展管理」独立项 + 可折叠的「基础扩展」（只放程序内置接缝包）与「加装扩展」
  （安装向导 `.trusted` + 用户导入）；7 个 Core 内置特性的设置页不再进这里，而是按
  `host_page` 内嵌到「互动与物理 / 菜单 / 关于与诊断」。
  `tabs` 门面只为兼容旧调用，内部不再是横向标签条。ModsPage 同口径过滤，展示 `name_zh`。
- `SettingsPage.prepare_embedded()` 隐藏内嵌页重复标题、收紧边距；`dirty/draft/confirm_save/discard_changes/dispose` 由 `ExtensionsPage` 统一委托。
- `ModsPage` 提供「允许功能扩展」复选框与「重新加载扩展」按钮；feature mod 安装后需要开启并重载。
- 侧边栏固定 7 页；`SettingsDialog` 不再把 contributed page 追加成侧边栏项。

## 16. 可导入样例与移动同步

- 内置只保留 7 个强交互 mod；`fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music`、`tool.weather`、`tool.file-reader` 移到 `examples/petcore-mods/`，可用 `petcore mods install` 安装。
- `CharacterSpec.move_curves` 保存 `move_strides.json` 的 `curve`；`content/legacy.py` 与 `content/modcontent.py` 解析并清洗（0..1、≥2 项）。
- `engine/motion.py` 新增 `body_reach`、`choose_move_direction`、`quantize_move`、`move_position_at_frame`；位置由 clip 帧号 + curve 推进，静态帧不位移。
- `PetWindow._begin_move` 先按方向设置 `state.facing`，再渲染首帧；`_on_frame` 调用 `_advance_move_from_frame`；`_on_finished` 提交终点/循环重播。固定 33ms 线性步进已删除。


## 17. P4：授信、碰撞与设置交互

- **扩展授信**：`petcore/mods/trust.py` 用扩展目录内的 `.trusted` 空文件表示「安装器
  逐包授权」。`loader.load_mods()` 的判定是「在用户 mods 目录 + (`allow_feature_mods`
  或 `.trusted`)」；`ui/app.py::attach_window()` 只认 `mod.enabled`，不再自己复读全局
  开关，否则随安装包投放的扩展永远激活不了。
- **安装器**：`packaging/petcore.iss` 的 9 个随附扩展是 `[Tasks]`（全部 `Flags: unchecked`）；源文件来自
  `examples/petcore-mods/`，目标 `{app}\mods\<id>`（安装目录，卸载随应用删除），另投放
  `packaging/trust_marker.txt`（重命名为 `.trusted`）。不勾选不写盘。
- **朝向**：素材原生朝左，`_mirrored()` 只在 `facing == "right"` 时镜像；
  `PetState.facing` 默认 `left`。
- **碰边**：`MotionController._tick_throw()` 维护「左/右/下/上」四向接触元组，接触沿
  变化且冲击速度 ≥ `BOUNCE_TRIGGER_SPEED` 时调用 `_on_motion_bounce()`。
- **多宠**：`PetManager._collide_pair()` 跳过拖拽中的一方；碰撞时取消双方移动计划；
  分离结果经 `window._clamp_position()`。落座用 `engine/spawn.choose_spawn_position()`。
- **边缘探头**：严格贴边判定用 1px；进入探头用 28px 余量并把身体吸附到边上
  （`edge_snap_x`）。
- **设置**：搜索口径 = 页面标题 + `keywords()` + 页面内所有 `QLabel` 文案；
  `reveal_text()` 切页并滚动到命中行。标签条在 `QScrollArea` 内，翻页箭头按溢出显隐。
## 18. P5：气泡锚点、菜单对勾、碰撞圆链

- **气泡锚点**：`PetWindow.bubble_anchor_rect()` 用角色 `head_box`（回退 `body_box`/整窗）
  换算到全局坐标；`SpeechBubble._place()` 按「上居中 → 上偏左右 → 两侧 → 下方」找不压住
  角色的位置，`BUBBLE_GAP=6`。
- **菜单对勾**：QSS 菜单项由样式表引擎绘制，`PE_IndicatorMenuCheckMark` 不会回调样式；
  而 QMenu 的勾选列与图标列又是同一个位置，把勾选塞进 `QMenu::indicator` 就等于勾选项
  不能有图标。因此勾选标记改由行尾层自绘（`menu_style.MenuCheckLayer`：13px 强调色圆 +
  白勾，圆心距行右缘 21px，随 `aboutToShow`/`toggled` 重画；托盘菜单在
  `CoreHost.build_tray_menu()` 里同样装层），`QMenu::indicator` 只留 1px + `image: none`，
  勾选项照设 icon（L101 之前的「运行时生成 14px 对勾 PNG + 勾选项不设 icon」已退役）。
  mod 贡献可用 `data={"checked": callable}` 声明状态项。
- **免打扰**：`QuietMode.remaining_seconds()`；`PresentationPort.say/notify` 的 `force`
  决定是否绕过免打扰抑制——命令反馈走 `force=True`，自动冒泡不走。
- **碰撞**：`engine/collision.py` 用内切三圆链（`circles_from_rect`）求最深重叠，
  分离遵循 0.5px slop / 60% 重叠 / 1..12px / 连续 3 tick 强制完整分离（上限 4×12px），
  同心用 `stable_tie_break(slot_a, slot_b)`；`PetManager` 每 tick 最多两轮分离。
- **岛 × 桌宠碰撞（L103）**：灵动岛是静态障碍物，和屏幕边界同级——几何在
  `engine/island_field.py`（零 Qt），接线在 `ui/island_collision.py`
  （`CoreHost.island_field`，**零定时器 / 零线程**，全事件驱动）。三个入口：
  `PetWindow._clamp_position`（拖拽/漫游/碰撞分离/缩放/换屏共用的位置出口，
  身体框永远进不了岛区）、`MotionController._tick_throw`（抛掷绕开位置出口，
  在物理步里按法向反射 + 落岛当落地）、岛的几何回调（拖岛/停靠/展开 → 把被压住的
  桌宠经同一条 `_clamp_position` 推出）。岛速采样照主分支桥的全套闸门（动画不采样/
  尺寸变化重置/`dt<0.01` 跳过且不清零/瞬移守卫/松手闸/配对上报/超速钳制）；
  撞岛反馈（挤压/音效/岛果冻）只有一个 0.25s 闸。开碰撞时关闭 1Hz 自动避让。

## 18. P8：Core 内置特性、内嵌设置页与碰撞推动

- **Core 内置特性**：`fun.click-talk`、`fun.self-talk`、`tool.quick-launch`、
  `tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info` 由
  `FeatureManager.load_builtin_features()` 无条件加载，但分组为 `core`
  （`CORE_FEATURE_IDS`）：不参与安装/停用/卸载，也不进扩展管理导航。
- **设置页内嵌**：`SettingsPageSpec` 新增 `host_page`；`FeatureManager.embedded_pages(page)`
  按 order 返回 Core 特性页。`SettingsPage.tab()/embed_page()` 把内嵌页放进
  `SettingsTabContainer`，`dirty/confirm_save/discard_changes/dispose` 由父页统一委托。
  映射：互动与物理（点击台词/自言自语/免打扰）、菜单（快捷启动）、关于与诊断
  （链接/网络信息/回收内存）。
- **扩展管理分组**：导航顺序「扩展管理 → 基础扩展 → 加装扩展」。`.trusted`
  （安装向导投放）与用户导入都算加装；只有 `petcore/builtin` 的接缝包算基础。
  组头可点击折叠，子项用 `setHidden()` 收起。
- **碰撞推动**：`PetManager._collide_pair()` 轻碰只做位置分离，不写速度、不取消
  移动计划；分离后调用 `PetWindow.shift_move_plan(dx, dy)` 让漫游继续。
  `impact_speed >= HIT_FEEDBACK_MIN_SPEED` 才写速度/切 `thrown`。
  探头宠物在轻碰时用 `dataclasses.replace(body, static=True)` 当无限质量，
  只有真撞击才 `arm_throw_egg()` + `cancel("collision")`。
- **气泡跟随**：`PetWindow.moveEvent()` 用 `QTimer.singleShot(0)` 同帧合并后
  `bubble.reposition(head_rect)`；`set_scale()` 后重排。`SpeechBubble` 支持文本与图片
  两种内容，彩蛋默认进气泡（`easter_egg_display=bubble|window`）。
- **打包回归**：`petcore/menu_templates` 必须进 PyInstaller 数据；`load_default()`
  内置兜底常量，`--check` 输出 `menu_template=json|builtin`，避免旧布局缺项无法补齐。

## 20. P1 作用域实现（2026-10-22）

- `petcore.config.ScopedConfig` 是设置页和 Core feature 使用的统一视图：显式全局键路由到
  主配置，其余键路由到目标宠物的 `Config`。
- `SettingsDialog(target_window=...)` 绑定当前窗口；`CoreHost._apply_live_to()` 对每只宠物
  从自己的 profile 读取角色/缩放/物理/动画/台词，对全局键读取主配置。
- `FeatureManager.pet_added/pet_removed` 是运行时生命周期接缝，自语等按宠物排程的 feature
  不再只覆盖启动时的主宠。
- 迁移器当前处理 standalone 主配置和 slot profile；会话、待办和 API 文件等业务数据必须由
  对应功能阶段实现并清洗敏感字段。

## 21. P2 WorkerHost 实现（2026-10-22）

- `platform.worker.WorkerHost` 是进程级宿主，`HostPorts.worker` 只暴露最小 `start()` 入口；
  `WorkerSession` 负责协议和 pipe 生命周期。
- Worker stdout/stderr 各有独立 reader，stdout 解码错误只回调诊断，不会杀死 Core；关闭先发
  `shutdown`，超时后 terminate/kill。
- WorkerHost 目前是基础设施，业务 feature 仍需在后续阶段声明 worker entrypoint 和结果事件。

## 22. P3 账户基础（2026-10-22）

- `petcore/account/AccountService` 使用 `ApiStore` 的服务规格和 `KeyringCredentials` 的密钥，
  通过 `petcore.net.http.urlopen` 发起有界余额请求。
- `CoreApiPort.fetch_balance()` 只返回结构化余额结果或稳定 `AccountError.reason`；mod 不直接
  import account/net 实现。
- 余额 UI 必须异步调用该服务，不能在 Qt 菜单回调里同步阻塞网络。
- `petcore/chat/SessionStore` 的路径包含 slot 与角色 id，消息历史原子写入并限制长度；
  它不读取凭据，也不把 worker 临时状态写入会话文件。

## 19. P1–P6 目标架构补充（2026-10-22）

### 19.1 当前宠物上下文

`CoreHost` 保留进程级服务，但每个窗口必须有一个明确的 `PetContext`。设置对话框、
PresentationPort、InteractionPort 和 feature runtime 不得通过隐式的 `host.config`
猜测目标宠物。全局配置只能由标记为 global 的页面和端口写入；每宠配置必须由当前
`PetContext` 的 `Config` 保存。

### 19.2 WorkerHost

业务 worker 不直接创建 Qt 对象，也不直接访问配置文件。Core 的 WorkerHost 负责子进程、
JSONL 握手/心跳/事件/错误、取消、超时、崩溃重启、代次拒收和退出收口，完成后通过
`HostPorts.worker` 向 mod 暴露最小能力。

### 19.3 迁移与发布

从旧 `pet` 入口切换前，迁移器必须处理全局配置、slot 配置、会话、待办、API 规格和
凭据引用；原文件先备份，失败保持原文件不变。发布入口切换必须可回滚，旧 `pet/` 在
迁移完成前只作为兼容层，不承载新功能。

实现（2026-10-26）：`petcore/migration/`。`__init__.py` 只管顺序、幂等标记
（`<root>/migration.json` 的 `completed`）、dry-run、备份与回滚映射；七个域各一个
`migrate(ctx)`：`config_data.py`（全局配置 / slot / 活跃集合）、`sessions.py`、
`todos.py`、`accounts.py`（API 规格 + 凭据）。域函数只返回条目，备份与写失败由
`MigrationContext` 自动记账。入口是 `petcore migrate [--dry-run|--force|--rollback]`，
`--check` 只读标记与备份目录、不跑迁移。

### 19.4 平台探针边界

`petcore/platform/` 是唯一碰系统 API 的地方，且必须零 Qt、零网络库、不 import
`pet.*`。GPU 用 ctypes 直连 `pdh.dll`（`gpu.py`）并复用 `SamplingLoop`
（`gpu_monitor.py`）；FPS 用随包 PresentMon 子进程（`fps.py`）与一次性 UAC 授权
（`fps_permission.py`）。三者的共同不变量：只有 `ok` 带读数，其余一律 `None` +
具体原因，**不伪造 0**。岛不直接调平台：它在 `_sync_sampling()` 里报「还需要哪些
段」，由 `petcore/ui/probes.py` 的 `IslandProbeHub` 启停采样器并逐秒推段快照。

## 23. AI 对话模块分布与 core 占用（2026-10-22）

> 口径：**业务在 mod，窗口/会话/传输在 core**。这一节回答两个问题：AI 功能现在
> 分布在哪、core 为它背了哪些代码和运行成本。

### 23.1 代码分布

**core（通用能力，不 import 任何 mod）**

| 位置 | 行数 | 职责 |
|---|---:|---|
| `contracts/api.py`、`contracts/chat.py`、`contracts/screen.py`、`contracts/host.py` | 458（含非 AI 契约） | `ApiPort` / `ChatPort` / `QuickChatPort` / `ScreenAskPort` / `ResourcesPort` 等纯契约 |
| `api/chat.py` | ~600 | SSE 流式、thinking 参数、联网检索轮、错误分类 |
| `api/{store,spec,credentials,probe}.py` | ~580 | API 服务规格与 `api.json`、keyring、连通性探测（通用） |
| `net/{http,errors,websearch}.py` | 825 | 代理兜底、失败分类、四引擎搜索与读页 |
| `chat/session_store.py` | 161 | 每宠/角色会话文件 |
| `ui/chat/` | 3769 | 双栏对话窗、紧凑快速气泡、消息泡、Markdown、主题、两个控制器 |
| `ui/screen/` | 1813 | 框选覆盖层、动作条、截图问答/结果窗、动作调度、品牌图标（截图能力共用） |
| `ui/ports.py` | — | `CoreApiPort`、`QtScreenPort`、`ResourcesAdapter`、端口装配 |
| `ui/bubble.py`、`ui/pet_window.py`、`ui/app.py` | — | 气泡点击入口、`QuickChatController`、`open_quick_chat()` |
| `config.py` | — | 16 个 `ai_*` / `chat_*` 全局键（作用域 + 类型归一化） |

**mod（业务）**

| 位置 | 行数 | 职责 |
|---|---:|---|
| `examples/petcore-mods/ai.chat/host/runtime.py` | 623 | 消息组装、对话/看看屏幕/截图问答/动作回退/快速对话编排 |
| `.../prompts.py` | 191 | 人格、识屏、OCR、翻译提示词与目标语言判定 |
| `.../settings_page.py` | 538 | AI 对话设置页（模型、思考、联网、外观） |
| `.../__init__.py`、`constants.py` | 41 | 入口与常量 |
| `examples/petcore-mods/tool.ocr/host/` | 291 | 本地 OCR 引擎（rapidocr + 随包模型）、优先处理者注册 |

### 23.2 core 里**没有**什么

- 没有人格/系统提示词、OCR 提示词、翻译提示词——全在 `ai.chat/host/prompts.py`。
- 没有模型路由、历史裁剪、温度/思考强度选择——mod 组装消息并传参。
- 没有 OCR 引擎与模型——在 `tool.ocr`。
- 没有搜索 API Key——只在系统钥匙串（owner `core.websearch`）。
- 唯一的用户向残留文案是 `api/chat.py` 的 `ChatTruncated`（「思考用完了输出上限」）；
  动作条标题、「看看这块区域」属于 core UI 文案，不是业务策略。

### 23.3 实测占用

环境：Windows 11、Python 3.12、PySide6、`QT_QPA_PLATFORM=offscreen`，单次采样。

| 路径 | 增量 | 触发时机 |
|---|---:|---|
| `import petcore.ui.chat` | **+7.1 MB / 63.5 ms** | 启动时 `CoreHost` 创建 `ChatController`/`QuickChatController` |
| `import petcore.api.chat`（含 `net.websearch`） | **+6.4 MB / 77.9 ms** | 首次真正发起对话（`CoreApiPort.stream_chat` 内惰性 import） |
| `QuickChatBubble` 实例 | **+1.8 MB / 2.5 ms** | 点桌宠气泡后创建，之后复用 |
| `ChatWindow` 实例 | **+4.9 MB / 15.9 ms** | 打开完整对话窗 |
| `ScreenAskWindow` 实例 | **+2.6 MB / 10.8 ms** | 截图问答 / 提取文字 / 翻译结果窗 |
| `tool.ocr` 引擎 | 首次识别 **+145 MB RSS**，之后常驻 | 第一次点「提取文字」；不点不加载 |

- **常驻线程/进程**：0。流式请求与 OCR 都是按次后台线程；OCR 在桌宠进程内运行
  （主分支是每次一个子进程，这里是有意的差异，见 P8 报告）。
- **磁盘**：只有每宠/角色的会话 JSON；截图与 OCR 图像只在内存，不落盘。
- **网络**：只有用户显式对话/联网/翻译时发生；启动不联网。

### 23.4 已知耦合（可接受，但改功能前要知道）

1. 16 个 `ai_*` / `chat_*` 键写在 `petcore/config.py` 的白名单与归一化里；
   新增一个 AI 设置需要同时改 core 配置（作用域与类型校验），改完不用改 core 其他代码。
2. `chat_background/accent/follow/always_on_top` 是 core 窗口外观，放在 core 合理。
3. 截图动作条与结果窗属于 core 截图能力，AI 只是它的消费者；`tool.ocr` 卸载后
   动作条按能力置灰。
4. 如果要求 core **零用户文案**，下一步可以把 `ChatTruncated` 改成稳定 `reason`，
   由 mod 映射成中文提示（目前只有这一处）。

### 23.5 结论

core 为 AI 背的是「窗口 + 会话 + 传输 + 搜索协议 + 端口」这批通用能力，启动成本
约 **7 MB / 64 ms**（对话窗包导入），真正的传输层在首次对话时才加载；
业务提示词、模型策略、OCR 引擎都在 mod。当前没有业务逻辑渗进 core，
唯一的轻度耦合是配置键白名单与一条截断文案。

## 24. P4-c1 窗口采样与主动识屏（2026-10-23）

- **前台窗口**：`petcore/platform/foreground.py` 的 `foreground_window_info()` 返回
  `{pid, process, title, hwnd, rect}`；矩形优先 DWM `EXTENDED_FRAME_BOUNDS`，
  cloaked 窗口视为没有。`idle_seconds()` 用 `GetLastInputInfo`，非 Windows 返回 0。
- **抓图**：Windows 上 `capture_window()` / `sample_window()` 用 GDI 按窗口的
  **物理像素矩形**直接抓虚拟桌面（`BitBlt` / `StretchBlt`），不做逻辑/物理换算；
  非 Windows 退回 Qt「交集最大的屏 + 裁剪」。150% 缩放下「Qt 逻辑几何 + DPR 裁剪」
  会把窗口裁到不足一半（实测 100% 失败），所以主路径必须是 GDI。
- **两段式**：稳态心跳只 `sample_foreground()`（192 边缩略图 → 指纹，不编码 PNG）；
  画面确实变了才 `capture_foreground()`（768 边 + PNG）发请求。
- **端口**：`ScreenPort.foreground_window()/capture_foreground(expected_hwnd=)`
  （抓图前后各查一次前台窗口）；`SystemPort.idle_seconds()`；`ConfigPort.set()`
  （仅菜单命令）；`build_ports()` 现在装配 `SystemAdapter`。
- **mod 侧**：`examples/petcore-mods/ai.chat/host/proactive.py` 的
  `ProactiveWatcher` 是 `scheduler.call_every(8000)` 驱动的看门狗，配置每 tick
  现读；`ProactiveLimiter` 状态落在 `feature-data/ai.chat/proactive_state.json`。
- **线程**：抓图在 GUI 线程（Qt `grabWindow` 不保证跨线程安全，单次 ≈18 ms/8 s），
  模型请求复用 `ai.chat` 的 daemon 线程，回调经 `scheduler.call_later()` 回 GUI 线程；
  停用/关闭用代次号丢弃迟到结果。
- **菜单**：`ai.chat:proactive` 进模板「看看屏幕」之后；旧布局由
  `_merge_template_actions()` 补入并写回，无需单独迁移函数。

## 25. P4-c2 Agent 联动数据流（2026-10-23）

```
Agent 侧（写方）                         桌宠侧（ai.agent mod）
┌────────────────────────┐   追加写     ┌──────────────────────────────────────┐
│ DSH 桥接 dsh-<pid>.jsonl│ ────────▶   │ 轮询线程 1.5 s                        │
│ agent-events/<a>.jsonl  │  JSONL      │  GlobTailer/ByteTailer（偏移/半行/64KB）│
│ 自定义事件文件/目录      │             │    ↓ parse_line → 六态                │
└────────────────────────┘             │  scheduler.call_later(0) → GUI 线程    │
                                        │    ↓ LinkEngine（折叠/节流/完成确认）   │
                                        │  PresentationPort / AudioPort          │
                                        └──────────────────────────────────────┘
```

- **端口边界**：Core 只多 `StoragePort.data_root()`、`PresentationPort.visible()`、
  `PetHandle.animations()`；`petcore` 里没有任何 Agent 名称、事件名或阈值。
- **线程**：文件 IO 全在 mod 的轮询线程；`LinkEngine` 与所有呈现调用只在 GUI 线程。
- **多写者**：`(agent, inst)` 分片保存原始状态，折叠规则「任一忙即忙」，完成确认只在
  折叠状态 `busy → 非busy` 时排定。
- **零回放**：首次见到已存在的文件跳到末尾；监视开始后才出现的文件从头读。
- **菜单**：`ai.agent:enabled` 由 mod 注册（`checked` 读 `ai_agent_enabled`），模板
  位置在 `ai.chat:proactive` 之后，旧布局由 `_merge_template_actions()` 补入。

## 26. P5-a1 灵动岛数据流（2026-10-23）

```
采样线程（1 Hz）                     零 Qt 判定层                  Qt 窗口
┌──────────────────────┐  快照   ┌────────────────────────┐  行计划  ┌──────────────┐
│ SysMonitor(psutil)   │ ──────▶ │ segments.SegmentSnapshot│ ──────▶ │ DynamicIsland │
│ NetMonitor(psutil)   │         │ layout.plan_info_row()  │         │ 自绘 + 卡片   │
└──────────────────────┘         │ layout.apply_shrink()   │         └──────────────┘
        ▲                        └────────────────────────┘                │
        │  只在「可见 + 启用 + 未折叠」时运行                                  │ 拖动/点击
        └─────────────────────────────────────────────────────────────────┘
```

- **端口边界**：灵动岛是 Core 窗口，不经 `HostPorts`；扩展与它没有耦合（mod 要显示信息
  仍然走 `PresentationPort` 的气泡）。
- **线程**：两条采样线程只做 syscall，GUI 每秒读快照；行计划与绘制都在 GUI 线程。
- **宽度白名单**：宽度键只含字体指纹、显示项、信息模式、指标 id、屏宽；读数与告警级
  都不进键——所以数字跳变不会让窗口抖动，也不会每次刷新都重建布局。
- **展开态预算**：卡片被 340–420px 钳住时，行按卡片宽重新收缩（折 `+N`），收起再用
  屏宽重排；两条路径都走同一个 `plan_info_row()`。

## 27. 语音本地模型与社区致谢数据流（2026-10-27）

```
设置「语音」页 ──> Config(speech_local_*) ──> SpeechService.synthesize
                                              │  flavor 含 endpoint/protocol/path/model/format
                                              ▼
                                    net/tts.local_synthesize
                                    （prefer_direct=True，http/https）
                                              │
                                    本地服务 / 云端 edge/sapi/豆包/MiMo
                                              ▼
                                     <root>/cache/tts/<sha1>.<mp3|wav>

自言自语/点击台词/彩蛋 ──> HostPorts.speech.speak（owner=fun.self-talk / fun.click-talk）
                        └─> PresentationPort.say_sequence（PetWindow 单定时器推进）

关于页 → 致谢 ──> CommunityService.credits()/fans()
                    │  随包 petcore/community/data/{credits,fans}.json（只读）
                    │  运行期 <root>/community/*-cache.json（刷新只写这里）
                    ▼
              CommunityRefreshJob（daemon thread）
                    │  net/github（贡献者 + issue/PR 作者）
                    │  net/bilibili（免登录粉丝总数；SESSDATA 才拉完整名单）
                    ▼
              失败/半截不覆盖；GUI 只收 progress/done/failed 信号
```

- **数据不可写随包**：`store` 只对 `<root>/community` 做写操作；`petcore/community/data` 是提交时快照。
- **粉丝总数口径**：`fans.json` 与缓存同时记 `total`（B 站 `x/relation/stat` 的 `follower`，
  含「账号已注销」这类同名占位）和去重后的 `names`。判断「有没有新粉丝」比较 `total`
  （`CommunityService.known_total()`）：与 `fetch_fan_count` 同源，所以两次读数能直接比；
  拿唯一名字数去比会永远差几条，导致每次打开关于页都重拉全部页。名单接口的 `total`
  只在 stat 取数失败时兜底。
- **凭据**：B 站 SESSDATA 与语音 Key 都只进钥匙串；诊断快照只输出 bool/计数。
- **线程**：刷新是守护线程，关闭设置窗只取消信号，不 `wait`；网络单请求有超时上限。
- **本地路由**：本地 TTS 不能被系统代理接管；云端 TTS 仍沿用 `net/http` 的代理/直连规则。
