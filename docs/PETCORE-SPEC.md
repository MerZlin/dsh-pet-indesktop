# petcore 最终形态规格（SPEC）

> 基线：`feat/petcore-vpet-architecture`，2026-10-20。
> 本文回答「这个项目最终要做成什么样」。实现进度、批次与未验证项见
> [`PETCORE-ROADMAP.md`](PETCORE-ROADMAP.md)；每次改动的记录见
> [`PETCORE-LOG.md`](PETCORE-LOG.md) 与 [`PETCORE-LOG-INDEX.md`](PETCORE-LOG-INDEX.md)。

## 26.9 审计修复后的契约增补（2026-10-26）

- **迁移回滚**：`rollback()` 覆盖/删除前先写 `<target>.pre-rollback-<时间戳>`；同一 target
  的多条 restore 只按**最早**那条恢复；`not_found`/`failed_write` 计入失败，只有全部成功
  才删标记；域级 `completed` 只在同批无失败时写入。
- **更新安装**：`UpdateService.install()` 必须与 `select_asset(manifest)` 登记的文件名/
  大小/摘要三者一致（侧车不能自证）；重启后可从缓存清单还原判据。
- **诊断与构建门**：`petcore --check` 只读（`Config.load(persist=False)`，不写
  `config.json`/`migration-backup`）；`--check --strict` 在 `menu_template != json`、
  `builtin_readable == 0`、`characters` 为空或 diagnostics 非空时退出 1，构建脚本必须用
  它并解析 `check.json`。
- **对话完整性**：`ChatResult` 增加 `incomplete`/`end_reason`；SSE 无 `[DONE]`、无
  `finish_reason`、或响应体为空时 `end_reason` 不是 `done`，调用方必须提示；HTTP 200 +
  `data: {"error":…}` 按状态码分类抛错。
- **配置扩展键**：见 `docs/PETCORE-MOD-FORMAT.md` §14.3.1（`extra` 区块）。


## 1. 一句话目标

把 dsh-pet 从单体的 `pet/` 包重构成 **一个可独立运行的 core + 一组可插拔 mod**：
core 负责生命周期、窗口、动画、菜单/设置宿主与扩展运行时；角色素材、功能域、
平台能力都以内容包或 mod 的形式接入。最终发布入口切换到 petcore，`pet/` 收敛为
兼容层并删除。

## 2. 最终用户看到什么

一个桌宠应用，至少具备：

- **桌宠本体**：透明无边框窗口、逐像素点击、拖拽、滚轮缩放、右键菜单、托盘、
  气泡、多屏归属与热插拔恢复；贴边按角色身体框，不按动画画布。
- **边缘探头**（P1-F）：贴到屏幕左右缘后自动 45° 部分出屏（默认约 55% 投影
  宽度，实机为头/上半身）；点击拉直，5 秒后回到探头姿态；拖拽/关闭开关立即取消。
- **角色与素材**：内置角色；用户可导入角色资源包（`pack-character` 生成 content
  mod）；WebP 帧序列 / WebM / GIF 三条素材路线都能被 core 消费；后续可增加 PNG
  序列等后端。
- **设置**：非模态设置窗口；核心设置实时反馈到桌宠；保存落盘、取消回滚；
  支持搜索；菜单编排、扩展管理、诊断与关于。
- **扩展**：内置 demo + 用户 mods 目录；内容包（角色/音效/台词/主题）与功能包
  （菜单动作、设置页、行为）分离；无热卸载、无沙箱的诚实边界。
- **功能域**：F01 屏幕理解、F02 AI、F03 Agent、F04 账户、F05 音乐、F06 语音、
  F07 提醒、F08 台词、F09 灵动岛、F10 趣味、F11 快捷启动——**执行**以 mod/worker
  形式接入；F02 AI 与 F04 账户所需的 API 配置、探测、凭据与额度由 core 提供。
- **API 与账户**：设置页「API 与账户」维护服务（Base URL/模型/超时/余额协议）
  与密钥（只进系统钥匙串）；服务规格落 `api.json`，密钥永不写盘；mod 通过
  `ApiPort` 拿不可变快照，不接触存储实现。
- **快捷键/自启**：可配置的「显示/隐藏桌宠」全局快捷键；Windows 开机自启开关与
  `petcore autostart status`。
- **多宠/多实例**：多实例（`--instance NAME`）各自独立数据根与 lock；单进程多宠由
  `PetManager` 提供 slot 0 主宠 + slot 1–15 子宠，每宠独立配置与碰撞。

## 3. 架构目标

```text
petcore/
  engine/      纯逻辑：枚举、几何、动画链、行为、位移、状态（禁 Qt）
  contracts/   端口与数据契约（只用标准库）
  net/         唯一联网出口：代理兜底、失败分类（标准库）
  api/         API 服务规格、探测、凭据端口实现（密钥只进钥匙串）
  account/     账户与额度（H7 落地，网络同样只经 net/）
  media/       Clip 后端、库、fallback、音效（唯一允许适配 pet.* 媒体实现的层）
  content/     旧素材只读适配、mod 内容、收集、打包（legacy.py 允许 import pet.*）
  mods/        manifest、发现、启停、安装、feature 宿主、CLI
  platform/    Windows 输入、屏幕几何、会话、开机自启
  ui/          窗口、渲染、气泡、托盘、菜单模型/布局/编辑器、设置
  cli.py       启动、--check、mods/pack 子命令
```

依赖方向是单向的：`ui → engine/contracts/media/content/mods/platform`；
`engine` 与 `contracts` 不知道 Qt，也不知道 `pet`。跨域联动只走端口、事件与
能力检查。

### 3.1 Core 必须保留的能力

- 应用生命周期、单实例锁、日志、配置读写与迁移、数据目录。
- 桌宠窗口：透明、置顶、点击穿透、拖拽、逐像素命中、缩放、显示器归属。
- 动画：GraphType/AnimatType 寻址、播放链、行为权重、位移与夹取、fallback。
- 摸头互动：使用角色 `head_box` 做头部命中，连续往返移动触发合格单次动画；缺失 `head_box` 时回退 `body_box`，范围和动画池按宠物保存。
- 动画节拍：待机一圈结束才掷骰；四项权重（待机/转向/随机动作/漫游）归一化，
  设置页可调。
- 语义事件动画：`events/<group>/` 与随机动作池分离，由 `play_event()` 显式触发。
- 呈现：气泡、托盘、右键菜单、菜单布局解析与编辑、设置宿主。
- 扩展宿主：manifest 校验、发现/启停、feature 端口、内容包收集、诊断。
- **统一联网**：`net/` 是唯一 HTTP 实现（系统代理不通自动直连）；`api/` 提供
  API 服务规格、探测、凭据端口与 `ApiPort`；`account/` 提供账户与额度。
- 最小可用角色与素材 fallback，保证「没有 mod 也能跑」。

### 3.2 不允许放进 Core 的东西

- 具体业务域的执行（AI 对话、识屏、音乐播放、语音合成、提醒、台词库等）——
  仍走 worker 或 mod；core 只提供它们需要的网络/API/账户能力。
- 支付、更新器；任何对 `pet.*` 的反向依赖（`media/` 与 `content/legacy.py` 是
  唯一例外）。
- 键盘/网络库绕开 `net/`：mod 不得 import `petcore.net`、`petcore.api`、
  `petcore.account`，只能拿端口。

## 4. 菜单与设置契约（硬规则）

1. **菜单/设置一一对应**：右键菜单里能看见、能调整的每个 Core 动作，必须在
   设置里有一个可到达的入口。映射表在 `petcore/ui/settings/coverage.py`，
   `tests/test_petcore_settings_coverage.py` 机器校验；`modern_settings` 豁免。
2. **菜单是视图，不是唯一入口**：动作可以在菜单里隐藏，但设置入口必须常驻。
   当前默认菜单隐藏「大小」，设置 →「外观与交互」→「大小」常驻。
3. **设置实时反馈**：影响桌宠外观/交互的设置，改控件即生效；保存只负责落盘，
   取消/关闭恢复打开时的快照。外部副作用（安装 mod、写开机自启）仍按显式保存
   或即时确认执行，并在界面上说明。
4. **非模态**：设置窗口（含「关于与诊断」页）不得阻塞桌宠的拖动、点击与滚轮。
5. **大小范围**：10%–300%，滑条 + 手输百分比 + 四档预设；core 内所有缩放入口
   共用 `clamp_scale()`。
6. **贴边按身体框**：位置钳制、右下角/主屏落位、位置恢复与跨屏迁移一律以角色
   `body_box × scale` 为锚点，窗口透明边允许越界；`body_box` 缺失才回退整窗。
   `head_box` 也作为摸头互动的可选命中区域；缺失时摸头命中回退到 `body_box`，边缘探头仍以 `body_box` 的旋转投影宽度为准。
7. **搜索是真的搜索**：搜索框的匹配口径包含每个设置行的标题与说明，命中后自动
   切到该页并滚到那一行；只按页面标题匹配不算实现。
8. **页内分页不截断**：标签条放进横向滚动区并带翻页箭头；窗口缩到最小宽度时
   右侧分页必须仍然可达。
9. **子选项跟随父开关**：从属设置（如「点击时跳过点击动画」之于「点击触发黄金
   回旋」）缩进显示，父项关闭时隐藏。
10. **朝向口径**：角色素材原生朝左；`facing == "right"` 才水平镜像（与主分支一致）。
11. **碰边反馈逐次生效**：抛掷/弹射过程中每次「离地→接触」边界都要 Q 弹 + 音效，
    不允许整个会话只触发一次。
12. **气泡贴着角色**：锚点用 `head_box`（回退 `body_box`/整窗），气泡不压住角色、
    不越出可用区；不许再用整窗 `frameGeometry()` 当锚点。
13. **Enter = 保存并退出**：设置窗口里 `Return/Enter` 直接保存关闭，只有多行文本框
    （候选台词等）允许换行。
14. **命令反馈不受免打扰抑制**：`say/notify` 的 `force=True` 只给「用户主动点的命令」；
    自动冒泡（自言自语、提醒）仍受免打扰限制。
15. **菜单勾选必须可见**：勾选标记画在**行尾**（`menu_style.MenuCheckLayer`，
    13px 强调色圆 + 白勾；QSS 的 `QMenu::indicator` 只留 1px 且 `image: none`）；
    勾选项**必须同时有图标**——QMenu 的勾选列与图标列是同一个位置，勾选塞回 indicator
    就等于勾选项不能有图标（L101）。
16. **碰撞不能瞬移**：`MotionController.set_velocity()` 切到抛掷前必须把内部坐标重锚到
    窗口当前位置；碰撞位移一律经过身体框钳制。
17. **探头只被真撞击打断**：只有相对接近速度 ≥ `HIT_FEEDBACK_MIN_SPEED`（300px/s）
    才算“真正撞上”：先 `arm_throw_egg()` 再 `cancel("collision", restore=True)`，
    然后完整分离。轻碰或走路宠物蹭上时，探头宠物按无限质量处理，只推开对方，
    不得取消探头、不得把旋转姿态推走。
18. **待机一圈才掷骰**：IDEL 是 `B_LOOP + loop=True`，`_on_finished` 不得用
    `player.advance()` 续播自身；必须在圈末掷骰。四项权重按比例归一化，
    `config.json` 的 `anim_weight_idle/turn/act/move` 可调，0 表示从不。
19. **事件动画独立**：`events/<group>/` 载入为 `GraphType.EVENT` + `event_group`，
    不进随机动作池；余额等事件只能经 `play_event(group, variant)` 触发。
20. **联网只有三个出口**：只有 `petcore/net/`、`petcore/api/`、`petcore/account/`
    可以 import `urllib/http/socket/requests/aiohttp/httpx`；mod 只能拿 HostPorts，
    不得 import 这三个包（`tests/test_petcore_architecture.py` 机器校验）。
21. **密钥不落盘**：API Key 只进系统钥匙串（`KeyringCredentials`），`config.json`
    与 `api.json` 都不含密钥；`ApiRequest.__repr__` 不显示密钥；钥匙串不可用时
    退回进程内存并在界面如实说明。
22. **探头/击飞只播 idle 与转向**：`probe_active` 或击飞彩蛋激活时，所有非
    `IDEL` / `MOVE(in_place)` 的素材一律降级待机；探头会话不翻朝向。
23. **飞行是接管态**：`motion.thrown` 期间不掷骰、不响应左键点击；落地 `stop()`
    后才由 `_on_motion_stopped()` 接回行为链。
24. **探头击飞才授权旋转**：碰撞确认重叠后先 `arm_throw_egg()` 再
    `edge_probe.cancel("collision")`；从探头姿态拖出去甩飞同样授权；低速碰撞没进入
    抛掷时立刻回正。
25. **安装器的扩展装进安装目录**：随附扩展落 `{app}\mods\<id>` 并带 `.trusted`，
    卸载随 `{app}`；用户导入仍落数据目录 `mods/`，可在设置里卸载。
26. **扩展管理用竖向导航与可折叠分组**：`扩展管理` 独立在最上；`基础扩展` 只放
    随程序内置的接缝包；`加装扩展` 放安装向导勾选投放的 `.trusted` 包与用户导入的
    包。组头可折叠，左侧列表独立滚动；mod 优先显示 `name_zh`。
27. **安装向导每次都显示目录页**：`DisableDirPage=no`、`UsePreviousAppDir=yes`；
    检测到同 `AppId` 已安装时预选上次安装路径。不得使用 `DisableDirPage=auto`
    把目录页藏掉，也不得把选装扩展装到用户数据目录。规则出处：
    `packaging/petcore.iss` 顶部注释 + 本条。
28. **Core 内置特性不是可选扩展**：`fun.click-talk`、`fun.self-talk`、
    `tool.quick-launch`、`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、
    `tool.network-info` 由 Core 无条件加载，不参与安装/停用/卸载；它们的
    `settings_page` 通过 `host_page` 内嵌到对应 Core 设置页，不再出现在扩展管理导航。
29. **Core 内置特性设置页归位**：互动与物理（点击台词/自言自语/免打扰）、
    菜单（快捷启动）、关于与诊断（链接与项目/网络信息/回收内存）。新增这类特性
    必须在贡献里写 `host_page`，并由 `tests/test_petcore_builtin_mods.py` 守住。
30. **气泡跟随**：`say()`/`show_image()` 定位后，桌宠每次移动（漫游/拖拽/抛掷/探头）
    都要重排气泡；缩放后重新排。气泡不是一次性快照。
31. **轻碰不改变行为态**：碰撞分离只有在 `impact_speed >= HIT_FEEDBACK_MIN_SPEED`
    时才写速度/切 `thrown`；轻碰只平移位置，并把双方移动计划整体平移继续走。
    拖拽中的桌宠不参与碰撞结算。
32. **菜单模板不能因打包丢失**：`petcore/menu_templates/` 必须随 `--add-data` 打包；
    `load_default()` 有内置兜底常量，`--check` 报告 `menu_template=json|builtin`。
    任何情况下缺模板都不得退化成只剩「设置/退出」两项。

33. **字号与图片大小是全局可调参数**：`ui_font_size`（10–18）作用于设置页、右键菜单
    与彩蛋菜单行；`bubble_text_scale`（50–300%）作用于气泡文字与内边距；
    `easter_egg_image_scale` / `self_talk_image_scale` 只改显示上限，不改源图。
34. **自言自语配图**：`self_talk_image_chance` 0–100（默认 30）决定是否出图；目录空时
    用内置 `assets/big_blue_fat_fish`；出图失败必须回退文字气泡。点击台词属用户主动
    命令，固定 `force=True`。
35. **弹弓期间禁止右键菜单**：右键进瞄准、发射/取消后 0.35s 内，
    `contextMenuEvent` 不得构建菜单；普通右键不受影响。
36. **设置 X = 保存**：标题栏关闭保存并落盘；「取消」/Esc 恢复打开时快照。
    内嵌页也必须走同一套 `_commit()`。
37. **菜单固定头尾**：`easter_egg` 永远是第一项；`playback_speed` 默认隐藏；
    尾部固定 `modern_settings → separator → quit`。旧布局由 `migrate_p8_menu()`
    幂等迁移，用户仍可在菜单编辑器里调整其他项。
38. **直播捕获兼容**：`stream_capture_mode` 开启时宠物窗口与气泡必须是普通顶层
    `Window`（非 Tool），并清掉 Win32 `WS_EX_TOOLWINDOW/WS_EX_NOACTIVATE`；
    关闭恢复 Tool。不得因此禁用多宠（每只窗口都可单独捕获）。
39. **播放动画预览**：分类子菜单只在 `aboutToShow` 填充；缩略图解码必须在
    非 GUI 线程（最多 2 线程），GUI 线程只转 `QPixmap`；`menu_animation_icons`
    关闭时不得发起解码。
40. **点击/拖放监听覆盖未来宠物**：`InteractionAdapter` 必须保存回调并能
    `attach_window()` 到新生成的子宠；`PetManager.spawn()/restore()` 负责调用，
    否则点击台词/投喂类功能在子宠上永远不触发。
41. **气泡与彩蛋窗必须与桌宠同层**：`always_on_top` 是宿主口径，气泡
    （`SpeechBubble.set_host_on_top`）与彩蛋弹窗（`EasterEggPopup(on_top=)`）都要跟随；
    桌宠置顶时气泡也置顶，否则图片气泡会被桌宠盖住（文字气泡贴头顶，看不出差别）。
42. **鼠标穿透只在托盘菜单**：右键菜单模板不包含 `click_through`（旧布局由
    `migrate_p9_menu()` 幂等摘除）；托盘菜单的「鼠标穿透」一次作用到所有桌宠并
    用气泡说明恢复位置。设置页「外观与交互 → 点击穿透」保留为第二个入口。
    理由：穿透开启后右键菜单本身点不到，托盘是唯一稳定的恢复入口（与主分支一致）。
43. **对话只有一条通道**：mod 只能通过 `HostPorts.chat`（`ChatPort`）驱动聊天窗口；
    窗口、Markdown 渲染、会话落盘与取消都在 core。流式 HTTP/SSE 只能出现在
    `petcore/api/`，`petcore/chat/` 保持 Qt-free 且零网络依赖。
44. **截图只走内存**：截屏由 core 的 `ScreenPort` 提供，PNG 直接内联进请求，
    **不写磁盘、不写日志正文**；未声明 `screenshot.capture` 的扩展拿不到端口。
45. **热键失败必须可见**：四条全局热键（显示隐藏 / AI 对话 / 看看屏幕 / 截图提问）
    各自独立注册；注册失败要落到气泡与设置行，不许静默。
46. **图片按设备像素比装载**：彩蛋弹窗、气泡配图、菜单头像一律走
    `petcore/media/images.py`（`load_pixmap/scale_pixmap/logical_size/screen_dpr`）：
    逻辑尺寸上限 × 屏幕 `devicePixelRatio` = 物理解码尺寸，pixmap 写回 DPR，
    QLabel 只设逻辑尺寸。禁止「先缩到逻辑像素再交给 QLabel」——1.5 倍缩放的
    屏幕上会被二次放大，这就是「彩蛋图发虚」的根因。

## 5. 资源与扩展供应链

### 5.1 素材路线

| 路线 | 现状 | 目标 |
|---|---|---|
| WebP 帧序列 | core 直接消费 | 默认路线；首跑供给、世代退役与 GC 由 core/worker 负责 |
| WebM | 通过 `pet.webm_clip` 只读适配 | 过渡期保留；可转帧序列后退役 |
| GIF | 通过 `pet.library.GifClip` 只读适配 | 过渡期保留 |
| 角色资源包 | 规划中（P1-D） | 统一 manifest v2、导入/校验/预览 CLI、冲突策略 |
| VPet 素材 | 只留 `Clip` 后端接口 | 不承诺兼容 LPS；需要时写独立后端 |

### 5.2 角色包

- 一个角色包 = 目录 + manifest：角色 id、显示名、动画文件/帧序列、`body_box`/
  `head_box`、`move_strides`、预览图、授权与来源字段。
- 包可以被复制到 `mods/<id>/` 或打包成 content mod；同 id 冲突策略为
  「拒绝覆盖内置、用户包之间按显式安装覆盖」。
- 转换（WebM/GIF/PNG → 帧序列）在 worker 中执行，core 只消费结果；转换失败要
  有诊断，不得静默回退到错误素材。
- 详见 [`PETCORE-CHARACTER-PACKS.md`](PETCORE-CHARACTER-PACKS.md)。

### 5.3 mod

- `mod.json` 声明 id、kind、版本、入口、能力、依赖与贡献；内容包与功能包同一套
  发现/启停/安装流程。
- feature 只能在 `--allow-feature-mods`、或带安装器授信标记 `.trusted` 的用户
  mods 目录下加载；默认只加载内置 demo。没有热卸载与沙箱，文档必须如实说明。
- Windows 安装向导把随附官方扩展做成可勾选项（默认不勾）：不勾选就不写盘；勾选时装到
  安装目录 `{app}\mods\<id>` 并带 `.trusted`。卸载随 `{app}` 一起删除。
- 贡献类型：菜单动作、设置页、角色/音效/台词/主题内容、事件订阅；跨域不互相
  import。
- 详见 [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md)。

## 6. 交付门

一个批次算完成，必须同时满足：

1. 结构守卫绿：`petcore/engine/` 禁 Qt、`contracts/` 只用标准库、`pet.*`
   只在允许的两个文件中出现、网络库只出现在 `net/`/`api/`/`account/`、mod 不
   import 这三个包。
2. 测试绿：`tests/test_petcore_*.py` + `tests/test_architecture.py` +
   `tests/test_pr_report_discipline.py` + `tests/test_ci_workflows.py`。
3. ruff 绿：`ruff check pet/ tests/ petcore/`。
4. 实机证据：真实启动、用户可见行为、截图/命令输出；不能自动验证的项要写清
   「为什么不能」与探针结果。
5. 性能证据：受影响路径的实测数字（命令、环境、样本量），不用形容词代替。
6. 文档：PR 报告三段证据 + `docs/INDEX.md` 登记 + `PETCORE-LOG.md` 追加条目 +
   `PETCORE-LOG-INDEX.md` 建索引。

## 7. 非目标（当前阶段明确不做）

- 热卸载、mod 沙箱、签名/权限隔离。
- 跨进程碰撞 IPC、每屏多宠、overlay 的 `ScreenHost` 全套。
- Core 自更新、支付、云同步。
- 兼容 VPet 的 LPS 素材格式。
- 在没有实测的情况下承诺 macOS/Linux 的窗口层级与输入行为。

## 8. 退出旧架构的判定

只有同时满足以下条件，才把发布入口从 `pet/` 切到 `petcore`：

- P2 第一批功能域（F07/F08/F10/F11）在 petcore 下可用且有验收；
- 设置/菜单/多屏/托盘/气泡达到主分支用户可见行为；
- 角色包导入与首跑素材供给可用；
- 打包产物在 Windows 实机跑通，macOS/Linux 至少有无头/构建验证；
- 旧 `pet/` 只作为兼容层被调用，不再承载新功能。

之后 `pet/` 只修兼容性缺陷，最终删除。

## 9. P2 已落地（2026-10-20）

- **关于**：独立 `AboutDialog` 与右键 `about` 已删除；版本、数据目录、日志、诊断快照、复制诊断、项目主页全部在设置「关于与诊断」。
- **单进程多宠**：`PetManager` 管理 slot 0 主宠与 slot 1–15 子宠；每宠独立 `config-pet-N.json`；生成/收回/重启恢复；宠物间椭圆近似碰撞与 Q 弹；设置页「多宠」。
- **交互 Core**：点击 Q 弹（220ms，旋转/探头叠加、命中逆变换）、拖动物理（弹簧/抛掷/碰边）、弹弓、黄金回旋、探头击飞旋转、拖文件投喂、碰撞；菜单「播放动画」「播放速率」「拖动物理」「不自动移动」「黄金回旋」「生小肥鱼」「退出子肥鱼」。
- **内置 mod**：`fun.self-talk`、`fun.click-talk`、`tool.quick-launch`、`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info`；受信任内置包随 core 加载，第三方 feature 仍需 `allow_feature_mods`。`fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music` 是 `examples/petcore-mods/` 的可导入样板，不再内置。
- **设置聚合**：所有 mod `settings_page` 进入「扩展管理」页内标签（`SettingsTabContainer`）；P6 起侧边栏 8 页（新增「API 与账户」）；2026-10-27 起 9 页（新增「语音」，排在「互动与物理」之后）。
- **移动同步**：朝向由移动方向决定；窗口位置由 clip 帧号 + `move_curves` 推进，静态帧不位移，末拍精确到位。

- **打包**：PyInstaller 6.22.3 装入 `.venv`；Inno Setup 6.7.3 便携安装到 `tools/InnoSetup`；`scripts/build_petcore.ps1 -Clean -Installer` 实际产出 onedir 与安装包，`--check --out` 取证。
- **动画**：`animation_gap_seconds`、`playback_speed` 生效；Q 弹/回旋/击飞/投喂/碰撞属于本轮交互动画。
- 后续：P3 网络/worker 域（F01/F02/F03/F04/F05/F06/F07/F09），P4 动画与性能 backlog（共享解码、预测预热、WebM 全量、VPet PNG、Agent 联动动画、音乐唱歌、动态岛、head_box 精细探头）。见 [`PETCORE-ANIMATION-BACKLOG.md`](PETCORE-ANIMATION-BACKLOG.md)。


> 本章及以下 P6/P7/P8.x 已按 ROADMAP §0 的编号约定改称 H6…H8.4（历史批次）；
> 当前交付阶段用 P0–P6，见 §15 与 ROADMAP §20。

## 10. H6 已落地（2026-10-20）

- **动画节拍**：待机一圈结束即掷骰（此前 IDEL 的 `B_LOOP + loop=True` 被 `advance()`
  原样续播，桌宠永远停在待机）；`animation_gap_seconds` 改为「间隙继续播待机、到点掷骰」；
  同一 clip 重播只 re-arm 不硬停。
- **可调概率**：设置 →「互动与物理」→「自主行为概率」四个百分比；默认 30/10/40/20，
  按比例归一化，0 表示从不；改完下一次掷骰即生效。
- **Q 弹参数**：设置 →「互动与物理」→「点击与音效」的 Q 弹开关、弹跳次数（1–5）、
  弹性强度（30–200%）、持续时间（80–1000ms）；默认 1 次/100%/220ms，关闭后点击、
  碰撞、撞岛、落地都不弹。
- **鼠标穿透（仅左键）**：托盘开关一次作用所有宠。Windows 上左键单击在松手时转发给
  下层窗口、左键长按（250ms）拖动；右键单击=旧左键点击、右键长按（350ms）=菜单；
  拖拽中右键仍进弹弓。非 Windows 仍保持旧整窗穿透（右键不可用）。
- **事件动画**：`events/<group>/` 载入为 `GraphType.EVENT` + `event_group`；余额 6 个
  动画不再混进随机动作池，由 `play_event()` 按名字/索引触发。
- **手动播放**：右键「播放动画」按待机/转向/移动/点击回应/随机动作/事件分组，
  叶子点一次播一次，播完回掷骰链。
- **core 联网**：`petcore/net/`（代理失败自动直连 + 五类失败分类）与
  `tests/test_petcore_net.py`；架构守卫改为 allowlist（`net/`、`api/`、`account/`），
  并新增「mod 不得 import 这三个包」。
- **core API**：`contracts/api.py`（`ApiMetadata/ApiRequest/ApiPort`）、`petcore/api/`
  （规格校验、`api.json` 原子写、keyring 凭据、文本探测）；设置页「API 与账户」；
  菜单「工具与帮助 → API 与账户」；`HostPorts.api` 与 `CoreApiPort`。
- **账户**：H8 落地（H7 只做交互/安装/扩展管理/彩蛋与音效；`account/` 仍留占位）。

## 11. H7 已落地（2026-10-21）

- **探头/击飞**：碰撞确认重叠后先授权 `arm_throw_egg()` 再退出探头；飞行/彩蛋期间
  只允许 idle/转向；`motion.thrown` 不掷骰、左键点击返回；低速碰撞立即回正。
- **安装器**：`SetupIconFile`/`UninstallDisplayIcon` 用鲸鱼娘 `assets/icon.ico`；
  5 个随附扩展改到 `{app}\mods\<id>`（`.trusted`），应用同时扫描安装目录；
  `--icon` 让主 exe 也带图标。
- **扩展管理**：左侧竖向滚动导航（基础扩展/加装扩展），ModsPage 加同口径过滤；
  `mod.json` 新增 `name_zh`，13 个内置/示例包已补中文名。
- **彩蛋与音效**：菜单「工具与帮助 → 彩蛋图片」随机弹 `assets/big_blue_fat_fish`
  的图片（可配置目录/标题）；点击音效包支持默认/小黄鸭/自定义文件/文件夹，
  碰撞与碰边复用同一音源。

## 12. H8 已落地（2026-10-21）

- **多宠推动**：轻碰分离不再取消移动计划、不再切 `thrown`；被推开的宠把漫游计划
  整体平移继续走。探头宠物在轻碰时按无限质量处理，只推开对方；真撞击（≥300px/s）
  才退出探头并授权击飞旋转。
- **子宠退出**：右键「退出这只」只关闭当前子宠并写回 `active_pets`；主宠仍保持运行。
- **气泡**：跟随桌宠移动与缩放；支持图片内容（彩蛋默认进气泡），可切独立窗口。
- **彩蛋菜单行**：头像 + 标题 + 「请点击」的整行控件，点击在菜单关闭后打开。
- **设置 IA**：「互动与物理」拆成 8 个页内分页；「菜单」拆菜单编排/快捷启动；
  「关于与诊断」拆版本诊断/致谢/链接/网络/回收内存；新增侧边栏「语音」。
- **Core 内置特性**：7 个原内置 feature 并入 Core，设置页按 `host_page` 内嵌；
  扩展管理只保留「扩展管理 + 基础扩展 + 加装扩展」，组头可折叠。
- **安装向导**：目录页每次显示，已装过时预选上次安装路径。
- **打包回归**：`menu_templates` 进入 PyInstaller 数据；`load_default()` 内置兜底；
  `--check` 输出 `menu_template`，杜绝右键菜单缺项。
- **差距台账**：新增 [`PETCORE-MAIN-PARITY-BACKLOG.md`](PETCORE-MAIN-PARITY-BACKLOG.md)，
  后续 P9–P12 的待办以该表为准。

## 13. H8.1 已落地（2026-10-21）

- **字号与图片大小**：`ui_font_size`（10–18）同步设置页、右键菜单与彩蛋菜单行；
  `bubble_text_scale`（50–300%）控制气泡文字/内边距；`easter_egg_image_scale`
  控制彩蛋图上限；`self_talk_image_scale` 控制自语配图上限。
- **自言自语出图**：`self_talk_image_chance`（0–100%，默认 30）命中时从
  `self_talk_image_dir`（空 = 内置 `assets/big_blue_fat_fish`）随机取图，作为气泡
  配图显示；0% = 只说话。点击台词是用户主动命令，`say(force=True)` 不受免打扰抑制。
- **点击反馈顺序**：开「点击查看余额」时余额气泡先冒（含失败原因那条），点击台词
  （`_notify_click()` → `fun.click-talk`）**等余额气泡收起后**才通知——`SpeechBubble.closed`
  （`hideEvent` 发）→ `PetWindow._flush_click_talk()`；宿主在余额结果展示后调
  `notify_click_balance_presented()` 接续。连点只排一条（跟最后一次点击），没有气泡可等
  （免打扰/窗口隐藏/结果一直不来/宿主无 `request_balance`）时 9s 兜底或立刻回落。
- **菜单顺序**：`easter_egg` 固定为第一项；`playback_speed` 默认隐藏；
  尾部固定为 `… → modern_settings → separator → quit`（分界线下方只有退出）。
  `migrate_p8_menu()` 对旧布局幂等迁移。
- **动画预览**：播放动画分类子菜单改为 `aboutToShow` 懒加载；动作带代表帧缩略图
  （`media/thumbnails.py`，内存 LRU 128 + 磁盘缓存，2 线程）；随机动作按名称前缀
  分组；`menu_animation_icons` 可关。
- **弹弓右键**：右键进入瞄准后，发射/取消后的 0.35s 内以及瞄准期间
  `contextMenuEvent` 一律不再弹菜单。
- **点击监听**：`InteractionAdapter.attach_window()` 给新生成的子宠补挂 click/drop
  监听；点击台词从启动第一只宠起就生效。
- **设置关闭**：标题栏 X = 保存并关闭；「取消」/Esc 仍恢复打开时快照。
- **直播捕获兼容**：`stream_capture_mode` 让桌宠与气泡从 Tool 窗口切到普通顶层
  `Window`，OBS/直播姬可枚举；捕获方式需选 Windows 10 1903+（BitBlt 黑屏）。
- **隐藏态**：`say()` / `show_image()` 在桌宠不可见时直接返回；`set_visible(False)`
  同时收起气泡。任何 feature 都不能绕过这个闸门。
- **设置合并**：彩蛋与自言自语配图合并到「互动与物理 → 彩蛋与配图」一页；
  自言自语页只保留节奏与候选台词。
- **托盘菜单**：独立于右键菜单（显示/隐藏桌宠、桌宠设置、数据目录、重载扩展、
  逐宠显示/隐藏/回右下角/退出这只、退出）；托盘左键也能切换显示。

## 14. H8.4 已落地（2026-10-21）

- **鼠标穿透归位托盘**：右键菜单不再有「点击穿透」（`migrate_p9_menu()` 幂等摘除
  旧布局里的该项）；托盘菜单新增可勾选「鼠标穿透」，一次作用到所有桌宠，开启时
  气泡提示「可从托盘菜单关闭」。设置页「外观与交互 → 点击穿透」保留。
- **气泡同层**：气泡与彩蛋弹窗跟随桌宠 `always_on_top`（同主分支
  `speech_bubble.set_host_on_top`），修「图片气泡掉到桌宠下面」。
- **图片高清**：新增 `petcore/media/images.py`，彩蛋弹窗、气泡配图、菜单头像统一按
  「逻辑上限 × 设备像素比」解码并写回 DPR；1.5 倍缩放的屏幕上不再被 QLabel 二次
  放大。实测弹窗渲染锐度（梯度能量）10.99 → 16.37（+49%）。

## 15. 2026-10-22 审查冻结的迁移契约

- **每宠作用域**：当前宠物上下文负责角色、缩放、动画、物理、音效、台词、位置与会话；
  全局上下文负责菜单、托盘、网络/API、更新、共享解码、默认扩展与进程级资源。
  设置窗口和 feature port 必须绑定打开它的宠物，不能默认读取主宠配置。
- **Worker**：业务 worker 只能通过 `contracts/worker.py` 的 JSONL 协议和 `HostPorts.worker`
  接入。Core 必须统一提供启动、握手、心跳、取消、超时、崩溃重启、代次拒收和退出收口。
- **迁移**：发布入口切换前必须迁移 v4 全局配置、slot 配置、会话、待办、API 规格和凭据引用；
  原文件先备份，迁移失败不得覆盖原数据，并提供可读诊断与回滚。
- **Core/mod**：API 服务规格、钥匙串、账户、额度和统一网络出口属于 Core；AI、识屏、Agent、
  音乐、语音、天气、文件识别和提醒执行属于 mod/worker。
- **有意差异**：播放速率保持 `0.5–2.0x`；本轮不支持 VPet LPS/PNG；petcore 的桌宠身份上限
  是 `MAX_PETS = 99`（含主宠，slot 0..98；L126 起由 16 放宽）。差距和阶段归属见
  [`PETCORE-FUNCTION-MATRIX-2026-10-22.md`](PETCORE-FUNCTION-MATRIX-2026-10-22.md)。
- **发布门**：只有 P0–P6 阶段完成、配置迁移可回滚、默认扩展安装可验收、Windows 冻结包
  通过功能矩阵且旧 `pet/` 不再承载新功能后，才切换 `python -m petcore` 为正式入口。

## 16. P1 已落地的配置作用域基础（2026-10-22）

- `ScopedConfig` 让设置页和 Core feature 区分全局键与当前宠物键。
- 设置对话框按打开它的窗口绑定 slot；实时预览和保存都针对该宠物。
- `PetManager` 只同步全局键；新子宠从主宠播种每宠配置，恢复/生成/收回会通知 feature 生命周期。
- 旧 standalone 配置与 slot 档案会在目标不存在时迁移，旧源只读，新目录保存 `migration-backup/`。
  会话、待办和 API 文件迁移等待对应消费者阶段。

## 17. P2 已落地的 WorkerHost 与离线接缝（2026-10-22）

- `petcore/platform/worker.py` 提供统一 `WorkerHost/WorkerSession`：真实子进程、JSONL 握手、
  配置推送、取消、错误、退出回收、owner 替换和可选心跳超时。
- CoreHost 持有单一 WorkerHost；退出流程会先收口 worker。业务 worker 仍需在 P3/P4 由 mod 接入。
- 设置常规页可从角色目录构建并安装 content mod；旧素材目录补齐 VPet Graph 类别的寻址映射。
- 安装器的随附官方 feature 默认不勾选，勾选才写盘；它们继续保持 feature mod 边界。

## 18. P3 账户基础（2026-10-22）

- `petcore/account/AccountService` 属于 Core，通过 `ApiStore`、`KeyringCredentials` 和统一
  `net` 出口提供余额查询、30 秒内存缓存、多币种选择和稳定错误分类。
- `HostPorts.api.fetch_balance()` 是 mod 的唯一余额入口；请求响应体和密钥不得进入日志。
- 余额菜单、自动刷新、档位动画和灵动岛展示要等异步 UI/worker 阶段接入，不能把同步服务
  直接放进 Qt 菜单回调。
- `petcore/chat/SessionStore` 按 slot 和角色目录隔离会话，限制消息数量并原子写入；它不保存
  API Key、服务凭据或 worker 状态。



## 16. P3 落地（2026-10-22）

- **流式对话**：`petcore/api/chat.py` 提供 `stream_chat()`（SSE / 一次性 JSON 回退、
  取消、超时、代理兜底、稳定错误分类）；`ApiPort.stream_chat()` 是 mod 唯一入口。
- **聊天宿主**：`ChatPort` + `petcore/ui/chat/`（主分支同款双栏对话窗、Markdown
  气泡、流式合帧、停止/新建会话、附件、按 `(owner, slot)` 隔离）；会话复用
  `petcore/chat/session_store.py`。跨线程只走信号，worker 线程不碰 Qt。
- **截图提问**：`ScreenPort.capture_region()` 负责框选（Core 覆盖层），
  `ScreenAskPort` 负责提问窗口；两者都只处理内存 PNG。整屏 `ScreenPort.capture()`
  仍用于「看看屏幕」，实测 1920×1200@1.5 抓屏 204.7ms、PNG 367KB。
- **热键**：`hotkey_toggle_pet` / `hotkey_open_chat` / `hotkey_look_screen` /
  `hotkey_screen_capture`，四条各自独立的 Win32 id；失败在设置页与气泡可见。
- **AI 扩展**：`examples/petcore-mods/ai.chat` 是参考实现（host-only）：prompt/人格/
  路由在 mod，传输/窗口/密钥在 core；安装向导提供可勾选项（默认不勾）。无扩展时
  菜单/热键会明确提示「AI 扩展未安装」。
- **打包签名**：构建脚本支持 `-SignThumbprint`（开发证书见
  `scripts/make_dev_signing_cert.ps1`）；exe 在 `--check` 前签名，setup 在 ISCC 后签名。
  未签名产物会被本机 Smart App Control 拦下（4551），这不是代码问题。
- **未做**：OCR、翻译、框选、主动识屏、Agent、TTS（P4）。

## 19. 对话 / 识屏 / 截图提问的最终边界（2026-10-22）

这一节冻结「效果对齐主分支，但执行仍归 mod」的边界，后续不要再把窗口或传输重写一遍。

- **对话窗口是 Core**：`petcore/ui/chat/` 直接移植主分支的现代双栏对话窗（侧栏会话、
  标题栏、Markdown 气泡、附件、浮动输入框、置顶/最小化/关闭）。mod 只能经
  `HostPorts.chat` 提交、流式回传、停止；不得自建聊天窗口或直接操作气泡。
- **运行时代码无 Qt**：`ai.chat` 的 `host/runtime.py` 不 import PySide6，需要回
  GUI 线程一律经 `HostPorts.scheduler.call_later()`；只有 Core 托管的设置页
  （`host/settings_page.py`）可以用 QtWidgets。
- **配置读取分两层**：运行时只经 `HostPorts.config`（只读 `ConfigPort`）读配置；
  设置页作为受信 UI，允许直接使用 Core 给的 `ScopedConfig`。不再保留未实现的
  `SettingsPort` 假契约。
- **主对话与截图提问是两个窗口**：主对话用 `ChatPort`，截图提问用
  `ScreenAskPort`；两者都按 `(owner, pet_slot)` 隔离，互不串流。
- **对话窗外观**：`chat_background`（默认白/内置主题/本地图片）、`chat_accent`、
  `chat_follow_pet`、`chat_always_on_top` 均有设置入口；改动经
  `ChatController.apply_appearance()` 实时作用到已打开的窗口。跟随使用
  `PetWindow.pet_moved` + 40ms 合并定位，优先右侧、放不下切左侧、最后钳在可用区。
- **看看屏幕**：Core 只提供整屏 `ScreenCapture`（含前台应用描述）；mod 负责
  prompt 与请求。答复必须先落到发起宠的气泡，再通过 `ChatPort.record()` **静默**
  写入该宠的会话记录；不得主动唤出对话窗口，除非 `ai_look_screen_auto_open=true`。
- **截图提问**：框选覆盖层、图片预览、多轮消息、加载态与 10% 鲸蛙加载彩蛋都在
  Core；mod 只通过 `ScreenAskPort.on_ask(question, png, history, slot) -> str`
  同步返回整段答复（Core 在后台线程调用它）。图片只在内存中流转，不落盘、不进日志。
  多轮历史**每次采集独立**：`open()` 清空上一轮，只存内存；不写磁盘（图片不能落盘，
  纯文本脱离当次图片没有追问语义）。
- **彩蛋口径**：截图提问窗口标题栏固定 DS 鲸鱼；等待答复时按
  `random.random() < 0.1` 抽一次鲸蛙，整次请求保持不轮播。这个彩蛋独立于桌宠菜单的
  「鲸鱼娘彩蛋」，不复用后者的图片目录/概率配置。
- **失败可见**：没有 API 服务、缺少扩展、视觉开关关闭、框选取消分别给出不同反馈；
  取消框选是静默返回，不算错误。

## 20. P7 深度思考与联网搜索（2026-10-22）

这一节冻结「思考/联网仍属 Core 传输能力、业务编排在 mod」的边界。

- **思考参数在 Core**：`petcore/api/chat.py::build_payload()` 负责
  `thinking={"type": enabled|disabled}` 与 `reasoning_effort`；`auto` 模式不发送
  任何思考字段（严格校验的服务会 400），显式开/关才下发。`stream_chat()` 把
  `reasoning_content` / `reasoning` 通过 `on_reasoning` 单独回调，**不拼进正文**；
  只出思维链且 `finish_reason == "length"` 时抛 `ChatTruncated`，提示调大输出上限。
- **联网检索在 Core**：`petcore/net/websearch.py` 是唯一搜索引擎实现
  （百度/DuckDuckGo 免 Key，博查/Tavily 需 Key；免 Key 两个互为兜底，遇人机验证
  如实报错、不绕过）。`stream_chat(web_search=)` 先跑独立一轮非思考 planner
  （`web_search` / `fetch_url` 工具，≤3 轮 × ≤4 次），把资料按主分支口径注入
  system，再走正式流式回答；任何检索失败只降级成状态行，不阻断回答。
- **搜索 Key 只进钥匙串**：owner `core.websearch`、key 为引擎 id；
  `ApiPort.set_web_search_key()` / `web_search_key_status()` 是唯一读写入口，
  `config.json` 与 `api.json` 都不出现 Key。
- **思考/联网是全局设置**：`ai_thinking`（auto/enabled/disabled）、
  `ai_reasoning_effort`（low/high/max）、`ai_web_search`、`ai_web_engine`
  （baidu/duckduckgo/bocha/tavily）、`ai_web_results`（1–10）、`ai_web_fetch`；
  设置入口在「扩展管理 → 加装扩展 → AI 对话」。与现有 `ai_model` / `ai_temperature`
  一致，不按 API 服务分别保存（主分支是 per-provider，这是已知差异）。
- **面板在 Core 窗口**：`ChatPort.reasoning()/status()` → `ChatController` 排队信号 →
  `ChatWindow.append_reasoning()/add_activity()` → `MessageBubble` 的折叠面板；
  窗口没开就丢弃，绝不主动唤窗。`reasoning`/`activity` 随 assistant 记录落盘，
  重新打开会话时回显（旧会话没有字段照常加载）。
- **截图冻结帧契约**：`grab_screen_image()` 必须返回 `QImage` 且
  `devicePixelRatio == 1.0`（裸设备像素）。Qt6 的 `QScreen.grabWindow` 返回
  `QPixmap`，直接交给 `QPainter.drawImage` 会抛 TypeError，覆盖层整层画不出来
  （用户看到的是一整屏灰色）；裁切/编码仍能工作，所以「框出来的图正常」。

## 21. P8 截图动作条与快速对话（2026-10-22）

- **动作条四个动作**：提取文字 / 翻译 / 问问肥鱼 / 复制图像。core 只提供覆盖层、
  按钮可点性、结果窗与剪贴板；`copy` 在 core 完成（PNG 在内存里写系统剪贴板），
  其余动作交给扩展。
- **动作调度**：`ScreenAskPort.on_action(owner, callback, priority=, actions=)` 是
  处理者注册表；core 按 priority 从小到大调用，回调返回空串或抛异常就换下一个。
  这就是「本地 OCR 优先、AI 兜底」的机制，core 里没有任何引擎判断或提示词。
- **本地 OCR 是扩展**：`examples/petcore-mods/tool.ocr`（加装扩展，安装任务
  `mod_ocr` 默认不勾）用 rapidocr + `resources/rapidocr_models`（经
  `ResourcesPort.bundle_path` 取路径），显式自建 onnxruntime session，不下载模型。
  失败返回空串，由 `ai.chat` 的视觉模型兜底；引擎异常不弹给用户。
- **翻译**：`ai.chat` 注册 `translate`；有本地 OCR 原文时走纯文本翻译，没有时把
  图交给视觉模型；目标语言自动判定（中日韩占多→英文，否则中文）。
- **快速对话**：点桌宠气泡 → `SpeechBubble.clicked` → `CoreHost.open_quick_chat`
  → `QuickChatPort`；紧凑气泡（标题/状态/Markdown 输出/输入/去聊天窗）在 core，
  流式回答在 `ai.chat`。与完整聊天窗共用同一会话，提交前落 user、完成后落
  assistant，`去聊天窗` 看到同一段对话。
- **依赖现实**：冻结包无法让 mod 自己安装 Python 包，`rapidocr` / `onnxruntime` /
  `cv2` 随 exe 打包；没有安装 `tool.ocr` 时这些代码不会被调用，动作条按能力置灰。

## 22. AI 对话模块的最终分布（2026-10-22）

- **core 只背通用能力**：`contracts/*` 的端口、`api/chat.py` 的流式/搜索传输、
  `net/websearch.py` 的搜索引擎、`chat/session_store.py` 的会话、`ui/chat/` 的
  双栏窗与快速气泡、`ui/screen/` 的截图动作条与结果窗、`ui/ports.py` 的端口装配。
- **业务全在 mod**：`ai.chat` 负责人格/提示词/消息组装/翻译/快速对话编排；
  `tool.ocr` 负责本地 OCR 引擎与模型。core 不 import 它们。
- **允许的耦合**：`ai_*`/`chat_*` 设置键在 core 配置白名单里（作用域与类型校验）；
  `ChatTruncated` 是 core 里唯一残留的用户向文案。
- **启动成本**（实测）：对话窗包导入约 7.1 MB / 63.5 ms；传输层首次对话才加载
  （6.4 MB / 77.9 ms）；OCR 引擎首次识别 +145 MB 常驻。
- 详细代码表、占用与结论见
  [`PETCORE-ARCHITECTURE.md` §23](PETCORE-ARCHITECTURE.md#23-ai-对话模块分布与-core-占用2026-10-22)。

## 23. P4-a：通用取数端口与两个业务 mod（2026-10-22）

**Core 只加一条端口，业务留在 `examples/petcore-mods/`。**

| 项 | 落点 |
|---|---|
| `HttpPort` 契约 | `petcore/contracts/http.py`：`HttpRequest/HttpResponse/HttpError`，`params` 由 core 编码 |
| 传输与路由 | `petcore/net/http.py::fetch_url`：`prefer_direct`、`retry_statuses`、共享 deadline、`max_bytes` |
| 端口实现 | `petcore/ui/ports.py::CoreHttpPort`，装配进 `HostPorts.http`；`CoreHost.ports` 供设置页取用 |
| 天气 | `tool.weather`：中国天气网直连 + open-meteo 代理兜底；6s/请求、15s 总预算 |
| 文件解读 | `tool.file-reader`：`interaction.on_drop` → 有界读取 → `api.stream_chat` → 气泡 + `ChatPort.record` |

规则：

1. **路由按请求决定**，显式路由请求不改写进程级「固定直连」结论；换路由只发生在传输失败
   或 500/502/504，404/429/503 与带 `Retry-After` 的响应不换。
2. **mod 仍然不 import `petcore.net|api|account`**，也不 import `urllib`；`params` 编码由 core 做。
3. **文件解读只读**：不修改/移动/删除被拖入的文件；路径不写日志正文；结果默认进会话，
   可在设置里关掉。
4. **默认安装口径不变**：两个新扩展都是安装向导可勾选项（`Flags: unchecked`），
   装到 `{app}\mods\<id>` 并带 `.trusted`，不勾选完全不写盘。
5. 新增能力声明 `files.read`（读取用户拖入的文本文件）；能力仍是声明，不是沙箱。

实测（2026-10-22，本机）：

| 场景 | 结果 |
|---|---|
| 中国天气网「鹿城」 | `鹿城 多云 20.3℃，西风 1级，湿度 58%`，0.26s |
| 中国天气网「北京」 | `北京 多云 11.3℃，南风 2级，湿度 52%`，0.24s |
| open-meteo 兜底「上海」 | `上海 局部多云 17.7℃，东北风 2级，湿度 71%`，4.51s（两跳） |
| 不存在的城市 | 两源各点名原因，1.86s，不静默 |

## 24. P4-b：语音端口、报时与音乐歌词（2026-10-22）

**合成与传输归 core，日程与台词归 mod。**

| 层 | 落点 |
|---|---|
| 契约 | `petcore/contracts/speech.py`：`SpeechRequest/SpeechResult/SpeechProviderInfo/SpeechError/SpeechPort` |
| 传输 | `petcore/net/tts.py`：edge-tts(WSS)、豆包 SSE、MiMo(OpenAI 兼容)、`PROVIDER_SPECS` |
| 离线 | `petcore/platform/sapi.py`：Windows SAPI5（ctypes COM，线程级 `CoInitialize`） |
| 服务 | `petcore/speech/service.py`：后端链、无网预检换默认音色、缓存（非空才命中）、2 次重试 + 默认音色兜底、失败删产物、密钥 owner `speech.<provider>` |
| 端口 | `petcore/ui/ports.py::CoreSpeechPort`：后台合成 → `AudioAdapter` 共享音频通道 → 回调；`stop/playing` |
| 报时 | `voice.chime` 1.1.0：6 档日程 + 自定义时刻、按日轮换台词、朗读/气泡文案分离、60 秒预缓存、免打扰丢弃 |
| 音乐 | `media.music` 1.2.0：三源识别（SMTC / WASAPI 音频会话 / 播放器窗口标题，判定表见 `PETCORE-MOD-FORMAT.md` §13.3）、歌词三源打分（直连→代理一次）、LRC 解析/偏移、唱跳循环、播放器启动 |

**动画循环接缝**：`PresentationPort.play_animation(name, loop=True)` 让一段素材一直播，
`release_loop()` 解除；探头、击飞、拖拽接管时 `_on_finished()` 自动解除，不破
「探头/抛掷只允许 idle/转向」的既有闸门。

规则：

1. **mod 不自己合成、不 import 网络库**：`voice.chime`/`media.music` 只经
   `HostPorts.speech` / `HostPorts.http`；后者是 P4-a 的端口。
2. **缓存命中要求非空文件**：0 字节产物会被删除并重新合成（边缘 TTS 曾因此永久静音）。
3. **音色失效必须兜底**：`preflight()` 无网也能把未知音色换成默认值；`synthesize()`
   再兜底一次默认音色。
4. **密钥只进钥匙串**：owner `speech.edge|speech.doubao|speech.mimo`，`config.json`
   与 `api.json` 不许出现；设置页只显示状态，不回显值。
5. **歌词路由**：直连优先，只在传输失败或 500/502/504 且无 `Retry-After` 时切一次代理，
   共享同一 deadline；不重试、不落盘音频、不记录 URL/代理/密钥。
6. **默认安装口径不变**：`voice.chime`/`media.music` 仍是安装向导可勾选（默认不勾）。

实测（2026-10-22，本机）：edge 合成 26,208 B / 3.39s，缓存命中 0.0s；SAPI 97,234 B
wav；歌词真实抓到 33 行（酷狗源，0.66s）；SMTC 无播放器时 47ms 返回明确原因。

### 24.1 本地模型、语音页与多气泡（2026-10-27）

- **本地 provider**：`PROVIDER_SPECS["local"]`（离线、API Key 可选、`optional_secrets`）。
  两种协议：`openai` → `POST {endpoint}/v1/audio/speech`（`model/input/voice/response_format/speed`）；
  `simple` → 可配路径的 `{text,voice,speed}`。响应可以是音频字节或 JSON 里的
  `audio|data|audio_base64`。请求强制 `prefer_direct=True`、`allowed_schemes=("http","https")`。
- **缓存键**：本地 flavor 额外包含 `endpoint/protocol/path/model/format`；换服务不会播旧音频。
  缓存后缀由 `speech_local_format`（mp3/wav）决定。
- **语音页**：侧边栏「语音」= 后端链/音色/语速/音量/缓存上限/试听 + 本地模型 + 在线 Key +
  清缓存；密钥走钥匙串 `speech.<provider>`；本地 endpoint 为空时 provider 标记「未配置」并回退。
  `SpeechRequest.rate/volume` 留空时由 core 用 `speech_rate`/`speech_volume` 填充，显式传值优先；
  SAPI 音色独立键 `speech_sapi_voice`（不再与 Edge 共用）。
- **本地直连**：本地请求用 `direct_only=True`，失败也不切系统代理（避免把本地 Key 与朗读
  文本发给代理）。
- **自言自语/点击台词出声**：`self_talk_voice_enabled`、`click_talk_voice_enabled` 默认 true；
  朗读失败不影响气泡与下一次排期。报时/抽签/节日/待办仍用各自开关。
- **champion 彩蛋**：`self_talk_champion_enabled` + `self_talk_champion_chance`（默认 8%）。
  命中时 5 条气泡依次出现（英文主文案 + 中文副标题），逐句朗读：
  `Maybe not today, / maybe not tomorrow, / maybe not next month, / but one day, /
  you will be a champion.`
- **多气泡接缝**：`PresentationPort.say_sequence(items)`（`text, subtitle, duration_ms`）；
  `PetWindow` 单次定时器推进，新的 `say/show_image/隐藏/关闭` 取消剩余；免打扰与隐藏态同
  `say()` 闸门。端口缺失时 feature 回退为单条合成气泡。

## 25. P4-b2：分组、可卸载与气泡分页（2026-10-23）

1. **扩展分组**：右键「扩展」按 mod 分成二级子菜单；mod 设置页一个 H2 + 若干 H3。
   菜单模板的 schema 不变，分组只发生在 `extensions_hub` 的运行时填充。
2. **可卸载**：安装器投放的 `.trusted` 包也能在设置里卸载；删除按 `install.json`
   清单，未知文件保留，个人数据保留；先撤运行时/菜单/模块，再删文件。
3. **气泡**：列宽 248→360（按字数放宽、乘字号缩放、按屏宽收敛）；长文本分页，
   每页短 3 行/长 6 行，页码点 + 滚轮翻页；副标题（音乐「我在唱《…》」）与正文
   同一卡片；点击仍打开快速对话。
4. **音乐识别范围**：默认不过滤；开启后只认已配置播放器（exe 名或额外识别名），
   过滤同时作用于唱歌、歌词与传输控制。**2026-10-26 起**识别是三源合成
   （SMTC / 音频会话 / 播放器窗口标题），完整判定表见 `PETCORE-MOD-FORMAT.md` §13.3。
5. **P4-c 边界**：主动识屏、`ai.agent`、提醒域转正、菜单模板迁移本轮不做，
   见 ROADMAP §25.2。
6. **窗口**：设置窗口保留原生标题栏并提供最小化/最大化（`WindowMinMaxButtonsHint`）；
   非模态、实时预览、X 保存、「取消」/Esc 回滚的语义不变。

## 26. P4-c1：窗口级抓屏与主动识屏（2026-10-23）

1. **抓屏分层**：整屏/框选仍是 `ScreenPort.capture()` / `capture_region()`；新增的
   窗口采样是 `foreground_window()`（只读元数据：`hwnd/process/title/rect`）、
   `sample_foreground()`（只算指纹，不编码 PNG）与 `capture_foreground()`（窗口
   画面 + `dh_hash`）。Windows 上用 GDI `BitBlt`/`StretchBlt` 按**物理像素矩形**
   直接抓，不再做逻辑/物理换算（150% 缩放实测：先用 Qt 逻辑几何裁剪会 100% 失败）。
   抓图与哈希只发生在 Core，mod 不 import `petcore.platform`。
2. **隐私双复核**：`capture_foreground()` 抓图前后各查一次前台 `hwnd`，不一致就丢弃
   画面并返回 `None`；画面只存在内存里，日志只记进程名/句柄/指纹，不记标题正文。
3. **dHash 与两段式采样**：`platform/image_hash.py` 的 64 位差分哈希（9×8 灰度、
   相邻比较）；先缩放再转灰度（`1200×800` 实测 9.87 ms → 1.58 ms），平滑重采样
   避免采样抖动。稳态心跳只走 `sample_window()`（缩到 192 边、不编码 PNG，
   实测 ≈34 ms/次）；确认画面变了才 `capture_window()`（768 边 + PNG，≈66 ms/次）。
   `dh_hash` 为 0 表示「无法比较」；基线一律用缩略图采样的指纹。
4. **主动识屏（`ai.chat` 的一部分）**：8 秒心跳；门槛依次是「启用 + 白名单非空 →
   免打扰 → 前台窗口 → 白名单 → 同一窗口停留够久 → 仅闲置（可选）→ 限额 →
   画面有变化 → 盖戳」，任一不满足就安静跳过。答复只冒气泡（6–20 秒按时长），
   不同步打开对话窗；`ai_proactive_sync_chat` 为真时用 `ChatPort.record()` 静默入会话。
5. **限额**：每日上限（默认 15）、最小请求间隔（60 s）、冷却（300 s）、
   连续 3 次失败当日熔断；状态文件 `feature-data/ai.chat/proactive_state.json`，
   原子写；dry-run 只写日志、不消耗额度。
6. **白名单**：大小写不敏感 `fnmatch`；无前缀只匹配进程名，`title:` 前缀只匹配
   标题；为空 = 功能完全不工作。设置页可「从当前前台窗口添加」按软件或按标题。
7. **配置键（全部全局作用域）**：`ai_proactive_enabled`、`ai_proactive_whitelist`、
   `ai_proactive_dwell_seconds`、`ai_proactive_change_threshold`、
   `ai_proactive_daily_cap`、`ai_proactive_cooldown_seconds`、
   `ai_proactive_min_interval_seconds`、`ai_proactive_require_idle`、
   `ai_proactive_min_idle_seconds`、`ai_proactive_dry_run`、
   `ai_proactive_pre_cue`、`ai_proactive_sync_chat`。
8. **菜单**：右键「主动识屏」是勾选项（`ai.chat:proactive`），模板位置在
   「看看屏幕」之后；旧布局由通用模板合并（`default-action-added:ai.chat:proactive`）
   自动补入，幂等。未安装 `ai.chat` 时该项不存在。
9. **P4-c 余项**：`ai.agent` 已在 P4-c2 落地（见 §28，只读联动、不含写方安装）；
   提醒域仍缺到期/提前/重复调度、农历与 48 签：当前仅清单 CRUD、8 个公历节日查询、
   6 条每日签文。随包可安装不是完整业务验收。2026-10-24 后续 P4-c3 已获批准，当前只执行 A 质量门禁，尚未开始新增提醒与仓库分发实现。

## 27. 系统缩放（DPI）口径（2026-10-23）

> 一条总则：**界面、布局、钳制、命中判定一律用 Qt 逻辑像素；只有「抓屏/指纹/
> 送模型」这条链路用物理像素。** 两侧不许混算——混算的典型症状就是「窗口矩形乘了
> 一次 DPR，结果裁不到画面」。

1. **抓屏**：`foreground_window_info()` 给的 `rect` 是 Win32 物理像素；窗口抓图用
   GDI `BitBlt`/`StretchBlt` 按该矩形直接抓，不经过 Qt 几何、不再乘 DPR。
   `sample_window()`（192 边）与 `capture_window()`（768 边）的上限都是**物理像素**，
   所以同一份内容在 100%/150%/200% 下送给模型的像素预算一致。
2. **「看看屏幕」抓虚拟桌面**：Windows 上 `capture_desktop()` 抓所有显示器并集
   （物理像素，主分支同为 `all_screens=True`），不再只抓宠物所在屏；非 Windows
   退回主屏。多屏/混合 DPI 下不会漏掉用户正在看的那块屏。
3. **送模型的图统一封顶**：`ScreenPort.shrink_for_vision(png, max_edge=1024)`
   （`VISION_MAX_EDGE`）按物理像素封顶；`ai.chat` 的「看看屏幕 / 截图提问 /
   提取文字兜底 / 翻译」都先过这一步。**本地 OCR 与「复制图像」必须用原图**，
   否则高 DPI 下小字会先被缩掉。
4. **屏布局**：`platform/screens.snapshot_from_qt()` 用 `availableGeometry()`（逻辑
   像素），`dpr` 只记录不参与矩形换算；`_wire_screens()` 监听
   `logicalDotsPerInchChanged` 与 `geometryChanged`，缩放变化后重排宠物与气泡。
5. **框选**：`SelectionRect.device_rect(dpr)` 左/上 `floor`、右/下 `ceil`，分数 DPR
   不会切掉半像素；裁剪在物理像素图上完成。
6. **图标与缩略图**：`ui/icons.py` 按 `screen_dpr()` 用物理尺寸绘制（1x/1.5x/2x
   分别 36/54/72）；菜单动画缩略图按 `physical_side(64, dpr)` 解码并写回 DPR，
   高分屏不糊。
7. **图片装载**：`media/images.py` 的 `load_pixmap/scale_pixmap/logical_size` 是唯一
   读图口径（逻辑上限 × DPR 解码、写回 DPR）；不要在别处直接 `QPixmap(path)` 后再
   按逻辑尺寸贴图。
8. 已知边界：混合 DPI 多屏下窗口跨屏时视觉大小随屏缩放变化（逻辑尺寸不变），
   与主分支一致；菜单勾选标记是矢量自绘（`MenuCheckLayer`，按窗口 DPR 直接绘制），
   不需要 @2x 资源。

## 28. P4-c2：Agent 联动 `ai.agent`（2026-10-23）

> 定位：`ai.agent` 是一个**可卸载的 feature mod**，不是 Core 能力。Core 只多出三个
> 通用接缝（`StoragePort.data_root()`、`PresentationPort.visible()`、
> `PetHandle.animations()`）与 13 个 `ai_agent_*` 全局键，不认识任何具体 Agent。

1. **事件来源（只读）**
   - DSH 桥目录：`<数据根父目录>/dsh-pet-bridge/dsh*.jsonl`（多实例各写一份，按
     mtime 最多同时看 8 个文件）。
   - 统一通道：`<数据根>/agent-events/<agent>.jsonl`（协议见
     [`AGENT_LINK_PROTOCOL.md`](AGENT_LINK_PROTOCOL.md) §2）。
   - 自定义 Agent：`ai_agent_custom_agents` 最多 8 条，`{key,name,path}`，`path`
     可以是文件也可以是有 `*.jsonl` 的目录。
   - **没有写权限**：不装桥接插件、不跑 pnpm、不写 `~/.claude`、不写任何 Agent 配置。
2. **六态与事件映射**：`thinking / working / attention / error / idle / sleeping`。
   `state` 字段优先；否则按 `event` 查表（Claude hooks 名 + DSH 事件名 + 裸状态名）。
   表外事件**一律忽略**。`turn/end` 带 `reasonKind ∈ {aborted, blocked, max_tokens,
   unknown}` 时收敛为 `idle`（不报「干完活」）。
3. **读取口径**：字节偏移 tail；**首次见到已存在的文件跳到末尾**（不回放历史），
   监视开始后才出现的文件从头读；单次 ≤64KB；半行缓冲上限 8KB，超过即丢弃该行；
   文件截断/轮转只重读一代。文件 IO 在一条后台线程（1.5 s 节拍），解析结果经
   `scheduler.call_later(0, …)` 回 GUI 线程。
4. **显示口径**
   - 忙碌（`thinking/working`）：联动动作池轮换 `写代码` / `吃Token`，每第 3 次插播
     `轻快记录` / `漂浮踏步`；精确名缺失按关键词回退，再退到角色任意 act。
   - 过程气泡：按工具名报进度（`正在跑命令` 等），同 Agent ≥10 s、全局 ≥8 s、
     同一文案 ≥60 s，文案截 80 字；无工具名不报。
   - 完成通知：折叠状态 `busy → 非busy` 后 800 ms 稳定确认才报；确认窗口内又忙起来
     则静默取消；同 Agent 完成通知冷却 5 s；本轮出现过 `error` 报「这轮好像没能跑完」
     而不是「干完活啦」。
   - 多写者：DSH 多实例按 `inst` 分片，**任一写者忙即视为忙**，一个实例回待机不会
     把另一个实例判成完成。
   - 隐藏态不切动画（`PresentationPort.visible()`）。
5. **音效**（默认关）：开工 / 完成 / 报错三个音，`builtin:agent-*` 指向
   `assets/sounds/agent/*.wav`，也可填绝对路径；同 Agent 同类音效受
   `ai_agent_sound_cooldown_seconds` 约束。
6. **配置键**（全部全局作用域，13 个）

   | 键 | 默认 | 范围 |
   |---|---|---|
   | `ai_agent_enabled` | `False` | 总开关（菜单勾选项写它） |
   | `ai_agent_dsh_enabled` | `True` | 监听 DSH 桥目录 |
   | `ai_agent_events_enabled` | `True` | 监听统一事件目录 |
   | `ai_agent_custom_agents` | `[]` | ≤8 条 `{key,name,path}`；key `[a-z0-9][a-z0-9_-]{0,31}`，不得占用 `dsh` |
   | `ai_agent_animation_enabled` | `True` | 联动动作 |
   | `ai_agent_activity_bubbles` | `True` | 过程气泡 |
   | `ai_agent_done_notice` | `True` | 完成通知 |
   | `ai_agent_min_interval_seconds` | `2.0` | 0.5–10 |
   | `ai_agent_sound_enabled` | `False` | 音效总开关 |
   | `ai_agent_sound_volume` | `0.65` | 0–1 |
   | `ai_agent_sound_cooldown_seconds` | `2.0` | 0–10 |
   | `ai_agent_sound_start/done/error_path` | `builtin:agent-*` | ≤500 字符 |

7. **菜单与设置**：右键「Agent 联动」是勾选项（`ai.agent:enabled`），模板位置在
   「主动识屏」之后，旧布局由通用合并补入；设置页在「扩展管理」页内标签
   （顺序 660），一个 H2 + 五个 H3（总开关 / 事件来源 / 动画与气泡 / 音效 / 诊断）。
   诊断面板列出每个来源的目录状态与最近一行事件，「发送测试事件」写一条 demo
   事件走完整链路。
8. **不做（P4-c 余项）**：DSH 桥接插件一键安装（c2.1）、Claude hooks 注入、
   Cursor transcript / OpenCode sqlite 直读、审批/提问常驻卡片、live 回合成本统计。

## 29. P5-a1：灵动岛 v1（2026-10-23）

> 定位：灵动岛是 **Core 的独立顶层窗口**（不是 mod）：它要读系统指标、要在桌宠隐藏后
> 仍然存在，而这两件事都不该由扩展承担。主分支 4661 行的 `pet/dynamic_island.py` 在这里
> 拆成「零 Qt 契约 + 零 Qt 布局 + 平台采样 + 唯一 Qt 窗口」四层，砍掉动效/停靠细条/
> 第三方段/聊天/碰撞/FPS/GPU（见 §29.6）。

1. **分段结构**（`petcore/island/segments.py`，零 Qt）
   - `SegmentSnapshot`：`id/label/text/max_text/state/detail`；非法内容（换行）直接拒注，
     label 超 6 字符截断并告警。
   - **盒宽自测量**：盒宽 = `horizontalAdvance(label) + 4(有 label) + horizontalAdvance(max_text)`，
     按 `(id, 字体指纹)` 缓存。**读数永不进宽度**——这是整岛不抖的根。
   - **运行期护栏**：实测文案超盒宽 → 二分右省略 + warning（一段失控不得推歪整岛）。
   - **四态**：`ok` 才有真读数；`sampling/unknown/error` 一律同宽 `--`；`unsupported` 不占位。
   - **告警级**：`cpu (80, 92)`、`mem (85, 95)` 两级 + 3pt 滞回；无读数的拍原样沿用上一级
     （不褪色）；没阈值的段恒无色。
2. **布局层**（`petcore/island/layout.py`，零 Qt）
   - 段间 19px 槽位、竖线居中、数值盒右对齐；有单位时是**双盒**（数值盒 + 1px + 单位盒，
     单位盒统一取该行最宽单位）。
   - **收缩流水线**（纯函数、可逆）：名字省略(96→40px) → 名字隐藏 → 尾部整段折 `+N`
     → custom 主信息省略 → 保底（主信息与状态点永不折，允许溢出）。
   - `apply_shrink()` 把计划的结构套到**当刻**的段列表：读数每秒在变，计划只决定
     「折哪几段」；`+N` 的 N 按实际折掉的段数算，chip 告警级取当刻被折段最高级
     （`error` 一律按 `crit`）。
   - `anchored_x()`：宽度变化按屏幕三分区定锚点（左 1/3 固定左缘、右 1/3 固定右缘、
     中间固定中心），避免贴边胶囊"先长出屏再被顶回"。
3. **采样层**（`petcore/platform/netmon.py`、`sysmon.py`，零 Qt）
   - 网络：`psutil.net_io_counters()` 差分 + EMA(0.5)，首拍只建基线（返回 None，不伪造 0），
     计数器回绕按 0；`format_rate` 5 列定宽，`format_network_slot` = `↓ 1.2M ↑ 340K`。
     **不做 UDP 延迟探测**：那需要 socket，而 core 的红线把网络库锁在 `net/`。
   - CPU/内存：`CpuMeter` 首拍建基线；间隔硬上限 1 Hz；`MAX_TEXT="888%"` 是盒宽样本。
   - 两条采样线程只在「岛可见 + 该段启用 + 该段没被折叠」时运行；`stop()` 幂等。
4. **窗口**（`petcore/ui/island.py`，唯一 Qt 面）
   - 几何：高固定 44、内缩 6、圆角 = 高/2、宽 = 行自然宽（下限 120）、默认贴屏可用区
     右上角 16px；拖动松手夹回屏幕并写 `island_x/island_y`。
   - 交互：点胶囊展开/收起卡片（`island_click_action=toggle_pet` 时改为透传点击）；
     拖动即收起卡片；展开时悬停暂停 15s 自动收起。
   - **拖动与停靠（2026-10-26 移植主分支）**：起拖阈值取 Qt 平台值
     （`QApplication.startDragDistance()`；`QWidget` 没有同名方法，旧实现每次拖拽都在
     第一帧抛 `AttributeError`）；拖动期间几何完全冻结。落点按**松手光标**选屏，
     先夹回屏幕再判停靠（距边 ≤40px，四边等权），随后重算尺寸、写
     `island_x/island_y` 与 `island_dock_edge`，再切模式。
   - **停靠细条**：停靠后窗口收成 16px 厚（左右停靠长 64px，竖直中心用
     `rect.top() + h // 2` 防漂移），靠屏侧画 2px 强调线；细条态**内容一像素都不画**，
     因此 `_sync_sampling()` 一并停掉全部采样器。
   - **悬停滑出**：悬停在细条上滑出胶囊（可悬停区同时包含滑出胶囊与细条两张矩形，
     逐矩形 `contains`，避免左右停靠上下各 10px 死区造成 1s 自持振荡）；
     光标离开 800ms 后收回，收回前必须复检可悬停区。
   - **几何动画**：停靠/滑出/展开/收起共用 180ms `ease_out_cubic` 插值，16ms 表
     **只在动画期间运行**；每帧 `set_geometry_changed_callback` 通知宿主。
     悬停另走 1.02 缩放弹簧，事件反馈（余额/消息）走竖压横鼓的果冻弹簧。
   - 卡片：一行一段（段名 / 值 · 详情），错误段写原因并标 `crit`；展开态整窗是一块圆角
     面板，行按**卡片宽**重排（放不下就折 `+N`，绝不画到窗外）。
   - 绘制：胶囊/面板自绘（深/浅/玻璃三套配色 + 五档强调色），颜色来自常量而不是 QSS；
     告警色只染数值与单位，label/竖线不沾；`island_show_status` 的画法是行尾一个状态点，
     颜色取行内最高告警级（无色时用强调色）。胶囊左侧**不画头标**：内容（名字/信息位）
     直接从 `CONTENT_PAD = 19px` 起排（L101 删掉了强调色圆点）。
5. **配置键**（全部全局作用域，16 个）

   | 键 | 默认 | 取值 |
   |---|---|---|
   | `island_enabled` | `False` | 总开关（菜单「灵动岛」写它） |
   | `island_show_name` / `island_show_status` | `True` | 角色名 / 状态点 |
   | `island_show_messages` | `False` | 消息显示：开启才记录并在展开卡片列出最近 8 条气泡消息（含「清除消息」按钮）；关闭时不记录也不显示，胶囊读数不受影响 |
   | `island_info_mode` | `"time"` | `time` / `custom` |
   | `island_custom_text` | `""` | ≤80 字符 |
   | `island_style` | `"dark"` | `dark` / `light` / `glass` |
   | `island_opacity` | `1.0` | 0.4–1.0（只作用底板） |
   | `island_accent` | `"blue"` | blue/green/purple/pink/orange |
   | `island_click_action` | `"expand"` | `expand` / `toggle_pet` |
   | `island_metrics` | `["cpu","mem"]` | `network/cpu/mem/disk/gpu/fps`，去重保序，≤8；`gpu`/`fps` 未接入探针时不占位（原因只在卡片一行）；`disk` = 系统盘（`%SystemDrive%`）**剩余**容量，5s 一拍、默认关 |
   | `island_x` / `island_y` | `None` | 有限值且 \|v\| ≤ 100000，否则自动定位；停靠态存的是**胶囊滑出位** |
   | `island_edge_dock` | `True` | 贴边停靠开关；关闭后不动 `island_dock_edge` 的存储值 |
   | `island_dock_edge` | `"none"` | `none/top/bottom/left/right`，白名单校验；开关关闭时读取统一退化为 `none` |
   | `island_collision` | `True` | 岛 × 桌宠碰撞开关：开=胶囊是硬墙（撞岛被弹、拖岛拍飞），关=不阻挡且恢复 1Hz 自动让位 |

6. **岛 × 桌宠碰撞（L103）**：胶囊是桌宠世界里的静态障碍物，三条不变量：
   **同步硬墙**（身体框进不了岛区，挂在 `PetWindow._clamp_position`，与屏幕边界同语义，
   不靠 16ms 采样——那是主分支 issue #146 的抽搐来源）、**只覆盖胶囊本体**
   （`min(窗口高, 44)`，展开卡片的卡片区域不是幽灵墙）、**矩形径向半径**
   （`rect_radial`，椭圆四角不判撞）。姿态闸门：不可见 / 停靠细条 / 几何动画中无墙；
   探头宠豁免；拖拽中的桌宠只推出；抛掷中只推出（反射在物理步里做）。真撞门
   `相对接近速度 ≥ 80px/s`（漫游实测 4.5–24px/s，所以走路只会被挡住），冲量
   `-(1+1.3)vn` 经抛掷档软限速；抛掷撞墙用 `e=0.68`（绝不再乘 1.3，会能量泵）；
   落在岛顶且竖直速度 <120 当帧收手。反馈（挤压/音效/岛果冻）只有一个 0.25s 闸。
   岛速采样：动画不采样、尺寸变化只重置采样点、`dt<0.01` 跳过（拖拽中不清零、非拖拽的同 rect 上报清零）、
   瞬移守卫、松手那一拍作废基线、同 rect 配对上报保速、非拖拽超速清零。碰撞开启时不做自动避让。
   两条补丁：**发起漫游时对 60–240px 外目标点的预钳只算几何**（`settle=False`，否则站着不动的
   桌宠会因为「目标点速度」被凭空弹飞）；**停在胶囊顶上的桌宠在岛走开后补一次自由落体**
   （normal 态没有重力通道，不补就悬在半空；判据是它没被用户挪走）。宠-宠分离不许覆盖
   已经在抛掷里的速度（岛冲量与分离速度只留先到的那个）。

7. **P5-a2 状态（2026-10-26 二次校正）**：已实现——卡片操作按钮与最近消息、余额主信息、第三方段注册面（`SegmentRegistry` + 释放回调）、GPU（PDH）与 FPS（PresentMon）真探针、折叠停采样、桌宠几何避让，以及本轮移植的**停靠细条（`island_edge_dock` / `island_dock_edge`）+ 悬停滑出 + 几何动画 + 悬停缩放 + 事件果冻**（L096）。**仍未做**：灵动岛聊天、"桌宠隐藏后由岛唤起对话"。（状态点五态/声纳/呼吸/淡化见 L099，岛 × 桌宠碰撞与 C 盘剩余段见 L103。）旧版此处把已实现项也写成"本批不做"，与代码和 ROADMAP 冲突，已更正。
7. **菜单与设置**：右键「桌宠控制 → 灵动岛」是勾选项（`dynamic_island`），模板位置在
   `edge_probe` 之后；设置落点在「外观与交互 → 灵动岛」（启用/显示元素/信息位/指标段/
   配色/不透明度/单击行为），改动实时生效、取消回滚。


## 2026-10-24 已有能力质量审查

本轮仅修已有行为，不增加功能。实现状态、自动化结果、Windows 单屏源码实机及
未验证范围分别见 [质量审查报告](PR-REPORT-PETCORE-QUALITY-REVIEW-2026-10-24.md)。
报告中的仓库全量失败/崩溃不能用 petcore 选定测试通过抵消；门禁收口前不启动下一阶段。


## 2026-10-24 P4-c3 执行边界

按 A质量阻断 → B通用端口 → C待办 → D节日/生日 → E48签 → 功能扩展分发验收串行推进。
A真实整仓pytest完整通过并正常退出前不开始新增功能；实现/自动化/Windows源码/候选包/公开发布
分别记账。当前不改Core或mod版本、不迁移用户数据、不发布、不开发角色v6、Agent写端或灵动岛二阶段。
A批完整pytest已完成：9716passed、20skipped、15warnings、exit0；Windows隔离源码退出通过，
但这不是候选包/新提醒能力验收。当前批次证据：[P4-c3 A报告](PR-REPORT-PETCORE-P4C3-A-2026-10-24.md)。已批准的后续能力不得提前标成已实现。


## 2026-10-24 P4-c3 B增量接口

B已实现按slot展示/真实通知交付、DND跨扩展单摘要、共享有界语音及实际播放完成/生命周期取消。
本批整仓9729passed/20skipped/14warnings、pytest正常退出；高负载三遍32passed、Windows隔离源码及性能证据通过，不沿用A结果。
C待办、D节日生日、E48签与分发尚未实施。详见[B报告](PR-REPORT-PETCORE-P4C3-B-2026-10-24.md)。


## 2026-10-24 P4-c3 C待办数据契约

C已实现按宠物持久化调度/面板/安全迁移；关联95passed。Core候选0.2.0/API1、todo1.1.0，未发布。性能/实机与累计全仓待C–E冻结重验；D/E尚未实现。详见[C报告](PR-REPORT-PETCORE-P4C3-C-2026-10-24.md)。


## 2026-10-24 P4-c3 D节日生日契约

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

## 2026-10-24 P4-c3-D1 分发契约边界

分发阶段（T00–T07）只加固「市场通道」，不改业务：

| 边界 | 口径 |
|---|---|
| 预算 | 仅市场 feature 包：64 MiB 压缩 / 256 MiB 展开 / 10000 条目；本地目录/ZIP 安装与角色/content 包保持零预算 |
| 来源 | `PackageOrigin` 区分 market / local-dir / local-zip / bundled；只有安装向导投放的包可带 `.trusted`，市场与本地包内自带的 `.trusted` 被剔除 |
| 传输 | `TransferSpec` 默认只允许 https；`RESUME_IF_MATCH` 要求 206 的 `Content-Range` 起点等于本地长度 |
| 有效范围 | 契约已冻结（MOD-FORMAT §18）；逐跳、读流预算、根索引、候选 ZIP 与安装包候选仍属后续批次，未实现 |

默认索引源、公开 Release、安装包签名与 SAC 处理均未改变；不得把本表当作「分发已可用」的结论。

## 2026-10-25 重构收尾边界补充

本轮只把已确认的运行时/分发阻断收口为可验证契约：市场任务由应用级 registry 持有并在退出阶段有界收口；远程功能包预算按来源与 kind 计算；本地 `file://` 必须来自显式本地索引且受授权根目录约束；回滚返回结构化状态；候选索引与已发布索引通过显式状态区分；`WorkerHost` 对握手、代次和迟到消息有边界；帧 pixmap 以物理尺寸生成并写回 DPR。

这些收口不等于完成全部主分支 parity。会话/待办/API 迁移、媒体后端完全脱离旧 `pet`、角色设置内导入、多屏混合 DPI 实机、候选安装器和默认入口切换仍须独立验收；没有证据时保持“部分/待验证”。

## 2026-10-25 重构收尾实现快照

本节是对本规范历史目标的执行状态补充，不改变既有契约。当前相对 `e79e2a5f` 已落地：

- 迁移返回可诊断状态，备份优先、原子写入、失败保留原文件；会话与提醒数据保留 schema/version 边界。
- `WorkerSession`/`WorkerHost` 对 READY、非法消息、取消、有界退出和代次迟到回调有明确处理。
- 设置内角色导入复用现有角色包校验，拒绝路径穿越与覆盖；点击音效使用按下/释放 token；彩蛋头像/标题/提示遵循设置取消契约。
- 余额入口使用异步任务与分类失败反馈；GraphType 菜单只对已有资源懒加载，不新增动画引擎。

验收口径仍需区分：实现、petcore 自动化通过、Windows 源码检查通过、候选包/跨平台待验证。当前不满足候选包和双屏实机前置条件，因此不切换默认入口、不宣称公开分发完成；`petcore/media` 与 `petcore/content/legacy.py` 的旧实现兼容边界继续保留。
## 2026-10-25 P5 运行时能力

P5 的 petcore 运行时能力已按以下契约实现：

- Dynamic Island 复用现有窗口、布局和 SegmentRegistry；固定模板宽度，展开卡片 340–420 像素范围内布局；隐藏、折叠、停用和退出时停止相关采样与反馈；消息队列有界，余额不轮询，仅由点击/右键/卡片刷新触发。
- 余额入口统一到 `BalancePresenter`，以 `slot` 为隔离键进行缓存、单飞和代次拒收；API key 不进入配置、日志或岛面文本。
- 菜单和设置使用显式 icon size、图标列宽和字体度量安全边距；QComboBox 箭头槽、文字和下拉项独立留出空间，不依赖全局 Qt 补丁。
- 性能计数器只保存聚合数字；默认系统指标采样不快于 1 Hz；关闭/隐藏时不启动无效采样线程。GPU/FPS 无探针时返回具体 unavailable 状态而非 0。
- `petcore.update` 只信任 HTTPS manifest/asset host，支持 ETag、缓存、镜像、断点、Range、大小和 SHA-256；源码运行及非 Windows `install()` 固定返回 `unsupported_runtime`，只有冻结 Windows 进程可启动独立安装器。

本节“已实现”不等同于“Windows 实机/候选安装包/公开 Release 已通过”；本轮证据见 `PR-REPORT-PETCORE-P5-COMPLETE-2026-10-25.md`。

## 2026-10-26 视觉与设置收尾（图标口径 / 外观分页 / 余额峰谷 / 岛动效）

**图标口径（最终）**
- 槽位：菜单 `icons.MENU_ICON_SIZE = 18`；设置侧栏按 `SIDEBAR_ICON_SIZE = 16` 绘制、
  视图槽位 18、行高 34。矢量图标在 36 单位设计稿上整体缩放到槽位，**笔宽按设计单位
  （1.6）一起缩放**（18px ≈0.8px，逻辑像素地板 0.75px）。把笔宽钉成恒逻辑像素会让
  小槽位墨量翻数倍，属于禁止做法。
- 菜单几何：`QMenu { padding 6; border-radius 12; icon-size: 18px }`、
  `QMenu::item { min-height max(18, 字号+5); padding 3px 29px 3px 13px; border-radius 9 }`、
  `QMenu::icon { left: 9px }`、分隔线 `margin 4px 4px`、右箭头 `7×11 / margin-right 10`
  （= 主分支 modern 的 24px 行）。
- 动画预览：整帧素材先按 alpha 包围盒裁剪，再按 **1.04** 填充率（y −0.2）画进槽位。

**设置结构**
- 「外观与交互」四页内分页：`size` 大小与外观 / `island` 灵动岛 / `text` 文字与快捷键 /
  `screen` 屏幕与位置；搜索跨分页命中由 `base.reveal_text()` 自动切页。
- 余额相关全部收在「互动与物理 → 点击与音效 → 余额提示」：点击查看余额（开启后余额气泡
  先冒、点击台词排在它收起之后）、峰谷提示颜色、
  峰谷文案（默认 / 梁文峰梁文谷 / 自定义）、两条自定义文本（仅自定义显示）。
  API 页只保留余额服务选择与一行位置说明。

**余额峰谷契约**
- 新增全局键：`balance_tier_labels_mode`（default/liangwen/custom，默认 default）、
  `balance_tier_label_peak` / `balance_tier_label_idle`（≤12 字，custom 生效）、
  `balance_tier_color_enabled`（默认 True）。
- `petcore/account/pricing.py` 是唯一判定处：北京时间（固定 +08:00）、周六日与法定节假日
  全天空闲、工作日 09–12/14–18 高峰；下一档位在 09/12/14/18 边界上向后扫 40 天取最早翻转；
  文案「DeepSeek 当前{峰} · 下一{谷} {今天 HH:MM|明天 HH:MM|周四 HH:MM|下周一 HH:MM}」；
  颜色高峰 `#e5484d`、空闲 `#30a46c` 只作用在副标题文字上。节假日由调用方注入
  `holiday_lookup(date)`（core 不反向依赖 `fun.festival`）。
- 气泡副标题走既有 `subtitle` 通道（`_notify(..., subtitle=)` → `say(..., subtitle=)`）。

**灵动岛动效与缓存纪律**
- 状态点五态：实心 / 空心环 / 红底白感叹；优先级 错误 > 待确认 > 工作 > 隐藏 > 完成 > 空闲；
  完成 4s 瞬态；空心环不画珠感高光。「隐藏」由宠物可见性派生（`set_pet_visible`）。
- 声纳环：1600ms、半径 5→13、α = 90·(1−t)³、1.5px 笔；只在 working + 可见 + 非展开 +
  非停靠 + `show_status` 时跑（33ms 表）。
- 呼吸：`notify_event("reply")` 起 5s 窗口，整窗平移 `round(1.5·sin(2.6t))`，整数像素。
- 内容淡化：结构变化播 130ms（旧图 α 1→0 线性、按新图透明区遮罩），读数换字不播；
  **窗口宽度不进结构键**（几何动画每帧都变，含进去等于每帧重起淡化 + 掩码尺寸不符被
  Qt 丢掉 → 旧新各画一遍 = 重影），改用行预算 + 段 id 序列；尺寸不同一律不淡化。
- 内容位图缓存：`width × 44`（DPR 自带）单张；键含段文案/状态/severity/`box_text`/
  `elide_px`、名字、`show_*`、配色、accent、dot(形状,色)、字体键、DPR、宽度；
  `FontChange`/DPR 变化清缓存；起拖跳终点。
- 状态按来源归并：`set_agent_state(state, source=…)`（chat / screen / core），
  `layout.resolve_agent_state()` 取最高优先（错误 > 待确认 > 工作）；一个来源发 idle
  不清别的来源。`error` 带 8s TTL。**余额结果只写余额面，不动状态点**。
- 外壳与内容都有位图/形变：`paintEvent` 开 `SmoothPixmapTransform`；投影下伸按剩余
  高度收敛（果冻 1.12 + 悬停 1.03 + 呼吸 +2 会推过 44px 窗高）；声纳环裁剪到胶囊路径。
- 外壳：三层投影 `(1,+1,α28)(2,+2.5,α16)(3,+4,α8)`、顶部 sheen（top+3/42%/α14·0·34）、
  软陶 bevel（内缩 1.5、上白 26 下黑 46，light 230/20，glass 90/(35,50,60,26)）、
  glass 四停点渐变（214/168/146/196 × opacity）+ 白 α130 keyline、段间竖线竖直渐变、
  单位盒 α×0.7（头标内环随头标一起在 L101 退役）。

**状态来源（真实接线）**：chat 起流→working（source=chat）、`_apply_done`→done、
`_apply_error`→error；截图提问 `waiting_changed`→working/idle（source=screen）；
可见性四条路（快捷键 / 托盘全部 / 托盘逐宠 / 桌宠右键隐藏）都经
`PetWindow.set_visible()` → `host._sync_island_pet_visible()` → hidden 派生。
`waiting` 目前只有 API 与渲染，没有真实触发点（等审批流接入后上行）。

**语义说明**：胶囊状态点只表达**智能体五态**；指标告警仍由段数值颜色与 chip 上的
4px 告警点表达（不使用状态点）。

## 反馈批次 2（2026-10-31）增量契约

**聊天窗布局**：`ai_layout` 拥有 scroll 的 stretch；`composer_card`/`ChatComposer`
纵向 Fixed；非空态时间线用顶部 Expanding spacer 底部对齐。输入法 preedit 期间
placeholder 为空，commit/取消/FocusOut 恢复。

**音频设备**：`music_audio_device` 空=跟随系统默认（runtime 每 5s 比较默认 id，
变化即丢 WASAPI manager）；非空=手选端点（每 30s 确认存在，拔掉回退默认并记
warning）。设置页端点列表在短命线程枚举，30s 缓存。`asr_device` 是 WinMM 输入索引，
`asr_device_name` 只回显。

**直播兼容**：`stream_capture_mode` 布尔 + `stream_capture_keycolor` 布尔 +
`stream_key_color` `#RRGGBB`。色键模式窗口不透明、整窗铺底色、气泡同样铺底；
直播模式置顶走 `SetWindowPos`，不重建 HWND；每次改窗旗标后重放
`apply_stream_window`。`capture_probe.probe_window` 只读报告 exstyle 与
PrintWindow/BitBlt 非黑占比，不写标题正文。

**设置搜索**：匹配标签/按钮/下拉项/列表项/树项/`searchKeys`；树项完整名优先；
下拉/列表只滚动定位，不改当前值；命中折叠 mod 页时自动展开左栏分组。

**DSH**：`DshHub` 进程内单条 SSE，多视图订阅；审批卡提交后置灰、resolved 终态、
失败/断线恢复；通道同会话只保留一个 pending，默认 10 分钟超时自动取消。

**ASR 实时**：`AsrChannel.partial` 只在录音线程产生；同刻最多一个增量识别在飞；
`sapi` 间隔 ≥1.5s。预览不得覆盖用户已输入文本，final 才 `insert_text`。

**人格卡**：包内 `persona_presets/*.json` 只读；用户卡在
`<data_root>/feature-data/ai.chat/personas/`，同 id 覆盖内置。保存校验 id/名称/
说明/提示词长度，原子写；runtime 按目录+文件 mtime 缓存。

**会话**：`ChatSession.archived_at`（0=未归档）仍属 schema 1；侧栏按
今天/昨天/本周/本月/更早分节，组头折叠；归档会话默认隐藏，可切换显示；
三点菜单归档/删除，删除确认默认取消。

**菜单**：`ai_hub`「AI 助手」收 `ai.chat:open` / `look_screen` / `proactive`；
`migrate_p12_menu` 幂等，且只在已有 AI 项或已有 hub 时迁移。

**自语每日**：`self_talk_daily_enabled/texts/sequence`；每日句按日期确定性抽取，
每个 slot 每天只说一次；`self_talk_daily_sequence` 时按 `|`/换行/句末标点拆段，
复用 `say_sequence` 的逐句展示/朗读。
