# petcore 工作约定（给 agent 与贡献者）

> 本文件只约束 `petcore/` 与 `tests/test_petcore_*.py`。仓库级规则（PR 三节证据、
> CI 成本纪律、文档登记）仍以根 [`../AGENTS.md`](../AGENTS.md) 为准。

## 1. 每次改动必须写 log

**硬要求**：任何代码、测试、文档改动完成后，必须同时做两件事：

1. 在 [`../docs/PETCORE-LOG.md`](../docs/PETCORE-LOG.md) 追加一条 `## LNNN`，写清：
   - 日期、批次/主题；
   - 改了哪些文件（`git diff --numstat` 口径）；
   - 行为变化与设计理由；
   - 验证命令与真实结果；
   - 未验证项、风险和后续动作。
2. 在 [`../docs/PETCORE-LOG-INDEX.md`](../docs/PETCORE-LOG-INDEX.md) 加一行索引：
   `LNNN / 日期 / 批次 / 一句话 / 关键词`。

读取 history 时**先读索引**，只打开命中的那一条，不要整份 log 塞进上下文。
`tests/test_petcore_docs.py` 会校验 log 与 log-index 的 `LNNN` 一一对应。

## 2. 架构红线

- `petcore/engine/` 禁 PySide6；纯逻辑只依赖标准库与同层模块。
- `petcore/contracts/` 只用标准库。
- 直接 `import pet.*` 只允许 `petcore/media/` 与 `petcore/content/legacy.py`。
- 网络库（`urllib/http/socket/requests/aiohttp/httpx`）只允许出现在 `petcore/net/`、
  `petcore/api/`、`petcore/account/`；mod（builtin 与 examples）不得 import 这三个包，
  只能拿 HostPorts。
- 密钥只进系统钥匙串：`config.json`、`api.json` 都不许出现 API Key；`ApiRequest`
  的 `repr` 不含密钥。
- `ui → engine/contracts/media/content/mods/platform` 单向；不要让 core 反向依赖具体业务域。

结构守卫：`tests/test_petcore_architecture.py`。红了不许改测试放行。

## 3. 菜单与设置

- **菜单/设置一一对应**：Core 菜单项必须在 `petcore/ui/settings/coverage.py` 有
  设置落点；新增菜单项不登记 = `tests/test_petcore_settings_coverage.py` 红。
  `modern_settings` 豁免。
- 菜单默认隐藏不等于删除：隐藏项仍要留在模板里，用户可在设置 →「菜单」重新勾选。
- **mod 设置页聚合**：所有 `settings_page` 贡献只进「扩展管理」的页内标签
  （`ui/settings/tabs.py`），不要再追加侧边栏页；P6 起侧边栏 8 页（「API 与账户」
  是 core 页，不是 mod 页）。保存/取消/释放由 `ExtensionsPage` 委托每个内嵌页。
- **设置搜索必须搜到行**：`_apply_filter` 的匹配口径含页面内所有 `QLabel` 文案，
  命中后 `reveal_text()` 切页 + 滚到该行；只按页面标题过滤不算实现。
- **页内分页不许截断**：标签条在 `QScrollArea` 里，两侧翻页箭头按溢出显隐，
  `setCurrentIndex` 必须 `_ensure_visible`。
- 设置改动必须实时反馈到桌宠；保存及标题栏 X 关闭负责落盘；取消按钮/Esc 恢复打开时的快照（与 SPEC 的设置窗口契约、现有测试一致）。
- 设置/关于窗口必须是非模态的，不得阻塞桌宠拖动、点击、滚轮。
- 大小范围固定 10%–300%，所有入口共用 `petcore.engine.geometry.clamp_scale()`。
- **位置钳制必须用 `body_box`**：贴边、右下角/主屏落位、位置恢复/比例、跨屏迁移
  一律走 `body_local_rect()` + `clamp_body()/clamp_body_to_screens()`；不许再用
  整窗 `content_size()` 直接贴边。`body_box` 缺失才回退整窗。`head_box` 已解析，预留给后续更精细的
  露头锚点；当前探头曝光以 `body_box` 投影宽度为准。
- **边缘探头**：贴边判定用身体框，旋转投影用帧中心；`is_transparent_at()` 必须
  与 `paintEvent` 的旋转互逆，否则点击和画面对不上。探头放宽域必须减去
  `body_local.x`。
- **快捷键/自启/多实例**：快捷键只做 Windows `RegisterHotKey`，失败必须可见；
  自启命令文本必须区分 `sys.frozen`；多实例用独立数据根与 lock，不许共享配置。
- **角色包**：`pack-character` 产物必须是标准 content mod（`character.<id>`、
  `kind=content`、`character.media`）；校验 body/head 与媒体存在；不许路径逃逸。
- **Q 弹/旋转命中**：`paintEvent` 与 `is_transparent_at()` 的逆变换顺序必须是
  「先 Q 弹逆变换、再旋转逆变换」；探头角度、黄金回旋、击飞旋转相加，不许互相覆盖。
  Q 弹幅度/次数/时长取 `click_squash_strength`（30–200，默认 100）/`click_squash_bounces`
  （1–5，默认 1）/`click_squash_ms`（80–1000，默认 220）；`click_squash_enabled` 关闭时
  点击/碰撞/撞岛/落地的所有 Q 弹入口都不弹。
- **帧表节拍固定 Precise**：几何动画的帧序列 clip（`create_frame_clip`）必须用
  `PreciseTimer`（`FrameSeqClip` 的默认值）。Windows 计时器颗粒 15.625ms，
  `CoarseTimer` 会把 24fps 的 42ms 量化成 47ms（1x 21.3fps）、2x 的 21ms 量化成
  31ms（2x 只有 32fps = 用户说的"开了 2 倍速还是慢"）；Precise 实测 1x 23.8fps /
  2x 47.6fps（旧版主分支满速档也一直用它）。
- **每帧不预缩放**：`renderer.frame_pixmap` 只做 QImage→QPixmap；缩放与镜像由
  `paintEvent` 按目标矩形（含 Q 弹 rect）绘制，镜像用 painter 变换而不是每帧
  `transformed()` 造中间 pixmap。
- **多宠配置隔离**：主宠 `config.json`、子宠 `config-pet-N.json`；子宠播种不得
  复制位置/屏幕/朝向；**宠-宠**碰撞只能通过 `PetManager` 的 tick 改位置与速度
  （灵动岛是同步硬墙，见下面「岛 × 桌宠碰撞」一条）。
- **Core 内置特性**：`petcore/builtin/features/<id>/` 里的 7 个包
  （`fun.click-talk`、`fun.self-talk`、`tool.quick-launch`、`tool.quiet-mode`、
  `tool.links`、`tool.memory-trim`、`tool.network-info`）是 `CORE_FEATURE_IDS`，
  无条件加载、不可停用/卸载、不进扩展导航；其 `settings_page` 必须写 `host_page`
  内嵌到 Core 页（interaction/menu/about）。`core.feature-demo` 是接缝示例，归
  「基础扩展」。第三方 feature 仍受 `allow_feature_mods` 控制。
  `fun.divination`/`fun.festival`/`reminder.todo`/`voice.chime`/`media.music` 在
  `examples/petcore-mods/`，是可导入样板，不得再放回 builtin；Windows 安装向导会把
  它们做成可勾选项且**默认不勾选**（不勾选完全不写盘，`Flags: unchecked`），装好后带
  `.trusted` 标记，归「加装扩展」。
- **移动同步**：窗口位置只能由 `move_position_at_frame(plan, frames_elapsed)` 按
  clip 帧号与 `move_curves` 推进；移动开始必须先设置 `state.facing` 再渲染首帧。
  不许恢复固定 33ms 线性步进或只按 `state.facing` 猜方向。
- **待机节拍**：IDEL 是 `B_LOOP + loop=True`，`_on_finished` 里**不许**先走
  `player.advance()`（它会原样返回同一个 idle spec，桌宠永远待机）；圈末要么继续
  播 gap 待机、要么 `_roll_next()`。同 clip 重播只 `start()` re-arm，不 `stop()`。
- **行为权重**：`anim_weight_idle/turn/act/move` 存百分比，`normalize_weights()`
  归一化；默认 30/10/40/20，0 表示从不。`BehaviorDirector.weights_provider` 每次
  掷骰读配置，设置改完即生效。
- **探头/击飞**：`probe_active` 或 `effects.throw_egg_active` 时 `_play_spec()` 只允许
  idle/转向（主分支 `_effects_filter_switch`）；探头会话 `_pending_flip` 必须为 False。
  `motion.thrown` 期间 `_on_finished()` 只 re-arm 当前素材、不掷骰，`_on_click()` 直接
  return（主分支非 normal 不消费点击）。
- **击飞彩蛋**：探头宠物被真正撞上时，`PetManager._collide_pair()` 必须先
  `arm_throw_egg()` 再 `edge_probe.cancel("collision", restore=True)`；低速碰撞没进入
  抛掷时立刻 `end_throw_egg()`。`MotionController` 每 tick 只在彩蛋激活时更新角度，
  `stop()` 无条件回正。
- **安装器位置**：随附扩展落 `{app}\mods\<id>` + `.trusted`，`paths.install_mods_dir()`
  只在冻结包存在，loader 与 `ui/app.py` 的「用户 mod 根」都必须含它；用户导入仍落
  数据目录 `mods/`。随安装目录的包也可以在设置内卸载（只删包目录，个人数据保留）。
- **安装向导目录页**：`packaging/petcore.iss` 必须 `DisableDirPage=no` 且
  `UsePreviousAppDir=yes`；每次打开向导都显示路径页，检测到同 `AppId` 已安装时
  预选上次安装目录（`InstallLocation`）。不许用 `auto` 把目录页藏掉。
- **扩展管理**：左侧竖向导航（`#extensionsNav`）顺序为「扩展管理 → 插件市场 →
  基础扩展 → 加装扩展」，两个组头可点击折叠；`tabs` 门面只为兼容旧测试/深链，不要退回横向标签。
  `基础扩展` 只放 `petcore/builtin` 接缝包；安装向导 `.trusted` 与用户导入都算
  `加装扩展`。mod 展示优先 `manifest.name_zh`；Core 内置项标「已并入 Core」且
  禁用启停/卸载。
- **插件市场**：市场是 Core 基础设施（`petcore/market/` + 设置页），不是可停用的 mod；
  索引只存元数据，artifact 走有序 URL 列表；feature 安装仍受 `allow_feature_mods`。
  契约见 `docs/PETCORE-MARKET.md`，改市场、索引格式或下载链路前必读。
- **彩蛋/音效**：`easter_egg` 菜单 → `window.show_easter_egg()`；点击音效走
  `media/click_sound.py` 的 `click_sound_pack`（默认/duck/文件/文件夹），碰撞与碰边
  复用同一入口；不许回到硬编码 `click.wav`。
- **事件动画**：`events/<group>/` 是 `GraphType.EVENT` + `event_group`，不进
  `act()` 的随机动作池；只能 `play_event(group, variant)` 显式触发。
- **API 配置/密钥**：`api.json` 只存服务规格，密钥只进 `KeyringCredentials`；
  设置页只在显式保存时落盘，keyring 不可用时退内存并在界面说明。mod 通过
  `HostPorts.api`（`ApiPort`）拿 `ApiMetadata/ApiRequest`，不许 import `petcore.api`。
- **普通取数只经 `HttpPort`**：天气、歌词这类 GET 一律走 `HostPorts.http`；查询串用
  `HttpRequest.params`（编码在 core），mod 不许 import `urllib`。`prefer_direct` 是
  **单请求**语义：显式路由请求不得改写 `net/http.py` 的进程级「固定直连」结论，
  换路由只允许发生在传输失败或 `retry_statuses`（默认 500/502/504），404/429/503 与
  带 `Retry-After` 的响应不换。新增取数域先定路由：国内源直连、海外源走代理。
- **天气口径**：中国天气网 `prefer_direct=True`、open-meteo 默认路由；单请求 6 秒、
  整次 15 秒预算，预算耗尽不再发请求；两源都失败必须点名每个源的原因，不许静默。
- **文件解读只读**：`tool.file-reader` 只读被拖入的文本文件，**不许修改/移动/删除**；
  路径不写日志正文；扫描 20000 文件/2 秒、单次 200000 字符，超出要如实提示。
- **能力声明**：新增能力必须先登记 `contracts/capabilities.py`（P4-a 加了 `files.read`）；
  能力只是声明与用户授权，不是沙箱。
- **语音合成只在 core**：mod 只经 `HostPorts.speech`；`petcore/net/tts.py` 是唯一
  import edge-tts / 豆包 / MiMo 的地方，`petcore/speech/` 禁 Qt；缓存命中必须**非空**，
  失败产物立刻删除；音色失效要无网兜底（`preflight`），`synthesize` 再兜底默认音色；
  密钥 owner 固定 `speech.<provider>`，`config.json`/`api.json` 不许出现。
- **本地 TTS 必须直连**：`local` provider（OpenAI 兼容 / 通用 JSON）一律
  `direct_only=True` + `allowed_schemes=("http","https")`（失败也不切代理），缓存键必须含
  endpoint/protocol/path/model/format；endpoint 为空时 provider 标记「未配置」并回退，
  不许在合成阶段才失败。
- **音色独立键**：Edge 用 `speech_voice`、SAPI 用 `speech_sapi_voice`、豆包/MiMo/本地
  各用自己的键；`SpeechRequest.rate/volume` 留空沿用 `speech_rate`/`speech_volume`。
- **自语/点击台词语音默认开**：`self_talk_voice_enabled` / `click_talk_voice_enabled`
  默认 true，彩蛋 `self_talk_champion_chance` 默认 8%；关闭后只冒泡。语音失败不得
  影响气泡与下一次排期。
  **播放音量取 `SpeechResult.volume`**（= 合成时实际采用的值，含 `speech_volume` 配置）：
  `SpeechRequest.volume` 留空是「沿用配置」，把它原样交给 `float()` 会 TypeError、整句
  静默消失（`on_error` 为 None 时连日志都没有）。feature 调 `speak()` 一律带 `on_error`
  只记 `reason`，别让「有气泡没声音」变成无迹可寻。
- **重置设置是命令不是偏好**：`petcore/settings_reset.py` 按 `GLOBAL_CONFIG_KEYS` 把
  「重置全局」与「重置本宠」定义成**两组键**（主宠的全局/本宠键同在 `config.json`，子宠
  本宠键在 `config-pet-N.json`）；`active_pets`、`positions` 永不重置，本宠重置另保
  `character`/`rx`/`ry`/`screen_name`。两个按钮放设置侧栏底部（不进页面列表/搜索索引），
  确认框默认取消；重置落盘后**直接 `done(Accepted)`**，跳过 `_commit()` 与快照回滚——
  否则旧控件值会把刚写的默认值翻回去。不碰 `api.json`/钥匙串/开机自启（后者是纯注册表状态）。
- **致谢数据只读 + 断网可显示**：静态条目（作者账号/特别鸣谢）只从
  `petcore/community/data/credits.json` 读；动态条目（贡献者/issue·PR 作者/粉丝）取缓存与
  随包快照中「较新且非空」的一份。运行期**永不写回** `petcore/community/data/*`，刷新只写
  `<data_root>/community/*-cache.json`；失败/半截名单不得覆盖完整名单。
- **B 站凭据只进钥匙串**：SESSDATA 存 owner `community.bilibili`，界面只显示
  「已配置/未配置」，日志、异常、诊断快照不得出现 Cookie；完整粉丝抓取只在有凭据时进行。
- **粉丝总数按 B 站口径比**：`fans.json`/缓存同时记 `total`（`data.total`）与去重后的
  `names`；判断「有没有新粉丝」只许比 `CommunityService.known_total()`，比唯一名字数会
  永远差几条同名占位，退化成每次打开关于页都重拉全部页。
- **签名全局唯一**：固定 `<data_root>/community/signature.png` + `signature.json`，
  新签名原子覆盖；不新增配置键，也不随设置窗「取消」回滚（保存按钮即生效）。
- **多气泡只走 presentation**：序列用 `PresentationPort.say_sequence`；新的
  `say()/show_image()/隐藏/关闭` 必须取消剩余队列，禁止在 feature 里自己拼定时器。
- **扩展功能只按注册出现**：属于某个扩展的菜单项必须由它自己的
  `Contribution(kind="menu")` 注册（id 形如 `<owner>:<id>`）；**不许**在 `core_items()`
  里为扩展功能写死入口——没装扩展时菜单里就该没有（L108 之前 AI 三项就是这么漏出来的）。
  默认布局按注册 id 摆位，旧 id 用 `menu_layout.migrate_pNN_menu` 迁移。
- **气泡是桌宠窗口的从属窗口**：`SpeechBubble(self)` 必须保持 parent 是桌宠窗口
  （owned window），层级才会跟着宿主走；右键菜单一律走 `ui.menu.show_context_menu`
  （扩展通过 `host.show_context_menu`），不要各自 `menu.exec()`——那会让菜单落回普通层，
  被置顶的气泡压住。
- **非置顶不许抢前台**：`always_on_top` 为假时，气泡的显示/换页/旗标重建**不得**把气泡或
  桌宠抬到别的应用之上，也不得激活桌宠窗口——**裸 `show()` 也会抬高整条 owner 链**（真机
  `GW_HWNDPREV` 实测），所以显示必须走 `SpeechBubble._present()`：置顶/直播才 `raise_()`，
  非置顶则先把宿主塞回原来的上邻居再贴宿主下方。离屏/非 Windows 下 `user32` 不可用时走
  「不 raise」分支。
- **菜单会话高于一切宠物窗口**：菜单弹出期间（`menu.menu_session()`，`show_context_menu`
  已包住整段 `exec`）桌宠与气泡的 `raise_()` 必须被抑制，菜单不能被气泡压住；会话结束后
  自动恢复。菜单自己的 `WindowStaysOnTopHint` 仍只跟桌宠的置顶开关走，不许为了修层级把
  菜单永久置顶。（托盘原生菜单由 Shell 弹出、不在会话内，属已知边界。）
- **展示会话（presentation gate）**：`PresentationPort.begin_scene(owner)` /
  `end_scene` / `refresh_scene` 是「谁在占用气泡」的唯一判据；会话期间其它 owner 的
  `say()/say_sequence()/show_image()` 一律返回 False 且**不动现有气泡**，`force=True` 仍可显示。核心只认
  owner 字符串，不认识「歌词」这类业务词；持有者必须每拍 `refresh_scene` 续期（45s 无续期
  自动释放，防 mod 崩溃后永久静音）。歌词持有 `media.music`；点击台词（含配图）、命令反馈、
  定时提醒/报时走 `force=True`；自语气泡（含配图）拿到 False 时**连朗读一起跳过**
  （看不见的话不该听得见）。
- **动画循环**：`play_animation(name, loop=True)` 只给「持续动作」（唱歌/报时）；
  `_play_spec()` 是循环的唯一清除点，`release_loop()` 只解除标志。探头、击飞、拖拽
  接管时 `_on_finished()` 必须先解除循环，不许让循环绕过「探头只播 idle/转向」的闸门。
  **持有循环的调用方要自己复核**：用 `PresentationPort.animation_state()` 每拍检查循环是否
  还在手上，被清掉就按退避重新请求（丢租约时不许再发 `release_loop()`，会误伤别人的循环）。
- **歌词**：直连优先，最多切一次代理（同一 deadline，仅传输失败或 500/502/504，
  带 `Retry-After` 不切）；**不重复换路**；取词失败的重试由运行时按 60 秒冷却控制
  （每曲 ≤3 次，首次失败说清原因），不是网络层重发；**取词只有一条常驻
  `lyric-fetch` 线程（单槽队列，提交新请求会取消在飞请求）**，并发 ≤1，不许每次取词
  `Thread(...).start()`；失败按**歌名**记 60s 负缓存（跨「艺人晚一拍/窗口源兜底」的
  身份抖动不重取）；播放分支的同名降级身份记别名、不得重建曲目/清配额；不落盘音频、
  不记录 URL/代理/密钥；歌词气泡同一行只冒一次且间隔 ≥2s；无播放器时命令必须给明确原因。
- **暂停语义**：播放器明确 `paused` → **下一拍就 `release_loop()`**（`_silence_limit` 对 paused
  返回 0）；`music_sing_grace_seconds` 只给 `absent`（切歌抖动/播放器关掉，且至少
  `ABSENT_CONFIRM_TICKS` 拍）。暂停期间**不取词、不重置歌词与行号、不冒「歌词获取失败」**，
  也不消耗「每曲 ≤3 次」的配额；暂停时识别结果换了身份（SMTC 掉线 → 窗口标题兜底）按同一首处理
  （记别名），不许当换歌重建曲目。
  **同一台播放器的残留 Active 会话不许把暂停翻成在播**：SMTC 说暂停时仍要问窗口源，但只认
  「另一台」播放器（`normalized_exe(window.exe) != normalized_exe(smtc.app_id)`）；同一台播放器
  暂停后 WASAPI 会话常仍挂 `active`，接受它会让本地时钟继续走、歌词每两秒换一句。
  暂停的**边沿**（上一拍在播 → 这一拍 paused）必须 `presentation.hide(SCENE_OWNER)` 收掉最后
  一句，别让它再挂 2–6 秒。
- **收起气泡只收自己的**：`PresentationPort.hide(owner)` 在 `owner` 非空时只在闸门仍归它时生效；
  歌词持有者可以收自己的气泡，绝不顺手动别人的（点击台词/提醒）。
- **朝向口径**：角色素材原生朝左，`facing == "right"` 才水平镜像（同主分支）。
  `PetState.facing` 默认 `left`；反了就是「位移方向和面朝向相反」。
- **碰边反馈**：抛掷/弹射按「离地→接触」的接触沿触发 `_on_motion_bounce()`，每次
  落地或撞墙都要 Q 弹 + 音效；不许用「整个会话只触发一次」的布尔闸。
- **边缘探头贴边**：`edge_side_at_rest` 的严格容差只用于判定「已经贴边」；进入探头
  用 `EDGE_ENGAGE_TOLERANCE_PX`（28px）并 `edge_snap_x` 吸附压边——落地反弹会停在
  离边十几像素处。
- **多宠碰撞**：分离结果必须走 `window._clamp_position()`（身体框钳制）；轻碰只做位置
  分离，不写速度、不取消移动计划，分离后 `shift_move_plan(dx, dy)` 让漫游继续；
  只有 `impact_speed >= HIT_FEEDBACK_MIN_SPEED` 才 `motion.set_velocity()`（切 thrown）。
  拖拽中的桌宠不参与碰撞结算（否则会把别人一路推走/顶出屏幕）。
- **碰撞几何**：`engine/collision.py` 只用内切三圆链 + 主分支分离规则
  （slop 0.5 / 60% / 1..12px / 连续 3 tick 强制分离）。不许退回整框椭圆——
  椭圆四角不判撞，看起来就是穿模。
- **多宠上限与粗筛**：身份上限是 `MAX_PETS = 99`（`config.py`，含主宠；`_pets()` 归一化、
  旧 `config-pet-N.json` 迁移与 `PetManager` 共用这一个常量，不许再出现第二个 16）。
  上限不是免费的：`collide()` 先过 `rects_may_touch()`（内切圆链必在矩形内 ⇒ 矩形分离
  必不接触），`PetManager._tick_collision` 用按拍缓存的身体框（`_rect_cache`，`_apply_body`
  移动即时失效）把 99 只的 n² 粗筛压到 1.2ms/拍；只有可能接触的配对才构造 `CollisionBody`
  跑圆链数学。粗筛是**必要**条件，判定结果一个都不许变。
- **岛 × 桌宠碰撞 = 同步硬墙 + 事件驱动拍飞**：几何在 `engine/island_field.py`
  （零 Qt：胶囊体育场 `min(高, 44)` + 矩形径向半径 + 中心在轴上强制向上 + 冲量公式），
  接线在 `ui/island_collision.py`（`host.island_field`），**不建定时器**。墙挂在
  `PetWindow._clamp_position`（拖拽/漫游/碰撞分离/缩放/换屏共用的位置出口）；抛掷走
  `MotionController._tick_throw` 的反射；岛自己动了由几何回调推出被压住的桌宠，
  且必须经 `window._clamp_position()` 同一条出口（否则推出但不结算）。
  **发起漫游时对 60–240px 外目标点的预钳必须传 `settle=False`**（只算几何、不结算）：
  否则「目标点位移」会被当成接近速度，站着不动的桌宠被凭空弹飞。不许改成
  `PetManager` 的 16ms 采样——单宠时 `_tick_collision` 直接早退，而且帧间会钻进再被弹
  （主分支 issue #146 的抽搐）。
- **岛墙闸门一条都不能少**：不可见 / 停靠细条 / 几何动画中 → 无墙；探头宠豁免；
  拖拽中的桌宠只推出不结算；抛掷中只推出（速度反射在物理步里做，两处改速度会打架）。
- **岛速采样照桥**：几何动画不采样、尺寸变化只重置采样点、`dt<0.01` 跳过且**不清零**
  （否则拖拽全程岛速恒 0、只推不拍）、瞬移守卫、松手那一拍作废基线、同 rect 的
  配对上报保住速度、非拖拽超速清零、拖拽中钳到 1500px/s。少一条就是「凭空拍飞」
  或「拖岛拍不动」。
- **撞岛反馈只有一个闸**：挤压 + 音效 + 岛果冻统一走
  `IslandField.notify_hit()`（0.25s/只），非抛掷撞岛与抛掷撞墙两条链共用；
  各留一份节流就是「先响一声、追着又响一声」。抛掷撞岛用 `physics.RESTITUTION=0.68`，
  **绝不能再乘 1.3**（能量泵）。
- **落岛 = 落地**：抛掷撞到胶囊上表面且竖直速度已 <`GROUND_BOUNCE_STOP_VY` 时，
  当帧清零速度并 `motion.stop()`；`physics.is_at_rest` 只认屏幕底边，不收尾就永远卡
  在 thrown（动画停在飞行帧）。
- **碰撞开启时不做自动避让**：`_avoid_island_overlap` 先判 `island_collision`；
  两套语义同时开会把胶囊弹到屏幕角落。`avoid_overlaps()` 方法本身保留（关掉碰撞即
  恢复旧的「岛让位」）。
- **磁盘段（C 盘剩余）可选、默认关**：`platform/diskmon.py` 零 Qt、5 秒一拍
  （钳制 [5,60]）、只做一次 `shutil.disk_usage`（GetDiskFreeSpaceEx，无磁盘 IO）；
  读不到报 `error` + 原因，**绝不写 0**（0G 会被当成「C 盘满了」）。胶囊上是单盒
  （`C 盘 412G`，单位不随读数在 G/T 间跳，盒样本 `888G`），卡片详情写「总/已用」。
  它**不进告警表**：现表语义是「越高越危险」，剩余空间方向相反。
- **气泡锚点**：`say()`/`show_image()` 用 `bubble_anchor_rect()`（`head_box` →
  `body_box` → 整窗），不许用整窗 `frameGeometry()`。桌宠 `moveEvent` 必须同帧合并后
  `bubble.reposition()`，缩放后也要重排；气泡不是一次性快照。
- **设置键位**：`SettingsDialog` 里 Enter = 保存退出，只有多行文本框放行换行。
- **命令反馈**：菜单/设置触发的命令反馈用 `say/notify(force=True)`，别被免打扰吞掉。
- **菜单勾选**：勾选标记画在**行尾**（`menu_style.MenuCheckLayer`，13px 强调色圆 + 白勾，
  圆心距行右缘 21px），QSS 的 `QMenu::indicator` 只留 1px 且 `image: none` —— QMenu 的
  勾选列与图标列是同一个位置，勾选塞回 indicator 就等于勾选项不能有图标（旧实现正是如此，
  主动识屏/Agent 联动/免打扰全部空白）。勾选项**必须**照设图标（L101）。
- **图标槽位 18px / 笔画随画布缩放 / DPR 只乘一次**：菜单槽位 `icons.MENU_ICON_SIZE = 18`
  （主分支 `small_icon_size(menu)=18`），设置侧栏按 `SIDEBAR_ICON_SIZE = 16` 绘制、视图
  槽位仍 18、行高 34。矢量图标在**36 单位设计稿**上按槽位缩放，**笔宽按设计单位（1.6）
  一起缩放**（18px ≈0.8px，逻辑像素地板 0.75px）——把笔宽钉成恒 1.6 逻辑像素会让 18px
  墨量变成初版的 1.8–6 倍，观感就是「图标又粗又大」（实测 settings 120 vs 初版 68）。
  画布按 `logical × DPR` 建并 `setDevicePixelRatio` 后，**QPainter 的坐标系已经是逻辑
  像素**：`painter.scale()` 只许乘 `logical/36`，再乘一次 DPR 会让字形放大 1.5/2 倍并贴死
  画布右下角（150% 屏上 play/screen/quit 实测包围盒 (14,8,26,26)，显示器底座整段被裁）。
  不许把设计稿直接画进 18px 画布（会被裁一半）。菜单尺寸走 `QMenu { icon-size: 18px }`
  （`QMenu::icon { width/height/left }` 会被 Qt 忽略），行高 `max(18, 字号+5)` + item
  `3px 29px 3px 13px`（= 主分支 modern 的 24px 行）。QSS 里不能写 `#` 注释
  （会被当成 ID 选择器，整条规则失效）。
- **动画缩略图必须裁剪**：整帧素材（640×390 里角色只占中间一小块）要先按 alpha
  包围盒裁掉透明留白、再适配进 18px 槽位（`icons.fitted_icon`，主分支
  `fitted_pet_pixmap_icon` 同款）；不许把整帧或带 DPR 的 pixmap 直接塞进 `QIcon`
  ——前者让角色只剩几个像素，后者让 Qt 按物理像素当逻辑像素算、图标再小一半。
- **碰撞不瞬移**：`MotionController.set_velocity()` 切 `thrown` 前必须
  `_x/_y = window.x()/y()`——内部坐标只在该方法里重锚，漏掉就是「被撞闪现到别处」。
- **探头只被真撞击打断**：`impact_speed >= HIT_FEEDBACK_MIN_SPEED` 时先
  `arm_throw_egg()` 再 `_edge_probe.cancel("collision", restore=True)` 并完整分离；
  轻碰时探头 body 按 `static=True`（无限质量）处理，只推开对方并 `abort_move_plan()`，
  不得取消探头。
- **状态点五态 + 多来源归并**：`island/layout.dot_state()` 的优先级是 错误 > 待确认 >
  工作 > 隐藏 > 完成 > 空闲；「隐藏」由 `set_pet_visible()` 派生，**不接受**外部设
  `hidden`；完成是 4s 瞬态（不可见时不点表）。状态**按来源归并**
  （`set_agent_state(state, source=…)` + `layout.resolve_agent_state()`）：截图收尾发
  `idle` 不许把仍在流式回答的 `working` 关掉，后写覆盖就是这类 bug。`error` 带 8s TTL
  （petcore 的错误来源是「一次请求失败」，没有 DSH 收敛器）；**余额结果不许动状态点**
  ——失败已有卡片红字，再写点会永久钉住红感叹。空心环不许画珠感高光。
- **声纳只在 working**：`_sonar_active()` 要同时满足可见、非展开、非停靠、`show_status`、
  状态为 working 五条；4s 完成表 / 33ms 声纳表 / 16ms 淡化表 / 8s 错误表都必须按需启停，
  空闲时不许有常驻定时器；进出展开、停靠、滑出都要显式 `_sync_sonar()`（别等 1Hz 心跳）。
- **内容缓存键 vs 结构键**：位图缓存键必须包含**所有会改变像素的输入**（段文案/状态/
  severity、`box_text`、`elide_px`、名字、`show_*`、配色、accent、dot(形状,色)、字体键、
  DPR、宽度）；结构键只含结构（**行预算**、段 id 序列、折叠级、名字布局、`show_*`），
  **采样值与窗口宽度都不许进结构键**——前者让时间每秒播淡化，后者让几何动画每帧重起
  淡化、还会因新旧宽度不同导致掩码尺寸不符被 Qt 丢掉 → 旧图新图各画一遍 = 重影。
  `FontChange` 与 DPR 变化必须清缓存；起拖立即跳淡化终点帧；尺寸不同一律不淡化。
- **内容层是位图**：`paintEvent` 必须开 `SmoothPixmapTransform`（悬停 1.02 缩放时
  不 interpolation 就是最近邻，正文发毛）；呼吸位移取整（`layout.breath_offset()`）。
- **呼吸 5s 整数像素**：只有 `notify_event("reply")` 起呼吸；位移用
  `layout.breath_offset()` 取整，不许亚像素平移（13px 中文每帧重栅格化会糊）；5s 到点归零。
- **子宠落座**：位置由 `engine.spawn.choose_spawn_position()` 决定（逐圈找空位，
  必须整只落在可用区内）；不许再写「主宠右侧 +24」这种硬编码。
- **打包工具**：PyInstaller 安装在 `.venv`；Inno Setup 便携安装在
  `tools/InnoSetup`（不入库）。默认 `--windowed`（不许出现终端窗口），
  `-Console` 只给排障用；`sys.stdout is None` 由 `cli._ensure_streams()` 兜底。
  候选包必须传 `-OutputRoot` 隔离输出（dist/work/spec、`--check` 与 Inno `/O` 全部收束；
  拒绝仓库根与已含 `dsh-pet-core-setup.exe` 的目录），`-Clean` 只传 PyInstaller 的
  `--clean`、不删除任何目录。
  **冒烟必须等待 GUI 子系统进程**：`-Windowed` 产物用 `& $exe --check …` 调用时
  PowerShell **不等待**（实测 52ms 返回、`$LASTEXITCODE` 为空、`check.json` 还没落盘），
  于是构建门实际在「读旧报告或直接抛路径不存在」。必须
  `Start-Process -FilePath $exe -ArgumentList @("--check --strict","--out","…") -Wait -PassThru`
  并按 `$smoke.ExitCode` 判红，再断言报告存在且 `LastWriteTimeUtc` 前进（不许用删除旧文件
  来绕开，`Remove-Item` 在本脚本里仍属禁止项）。
- **构建产物必须签名**：本机 Smart App Control 会拦截未签名的新构建
  （`CreateProcess` 返回 4551「An Application Control policy has blocked this file」），
  PyInstaller exe 与 Inno setup 都会被拦。用
  `scripts/make_dev_signing_cert.ps1` 建本机信任的开发证书，构建时传
  `-SignThumbprint <指纹>`；签名必须发生在 `--check` 冒烟**之前**，setup 在 ISCC
  之后签。开发证书只在本机可信，正式分发要换 CA 证书。
  **2026-10-23 更新**：SAC 转强制模式后（`VerifiedAndReputablePolicyState=1`），
  连开发证书签名的新 exe 也会被拦（3077），而更早构建的旧 exe 仍可运行。此时：
  ① 不要用不可运行的新 dist 覆盖可用安装目录；② 冒烟失败不是代码问题，改用源码树
  真机验证并在报告里如实登记；③ 需要本机跑新构建只能由用户关闭 SAC（关闭后需重装
  系统才能再开）。同一条策略也会拦未签名的原生扩展（先例：
  `shapely/_geos*.pyd` → 本地 OCR 退化为 AI 兜底）。
- **扩展授信**：安装器投放的官方 feature 包带 `.trusted` 标记（`mods/trust.py`），
  与全局 `allow_feature_mods` 并列；loader 与 `ui/app.py` 只认 `mod.enabled`，
  不许再各自复读全局开关。
- 菜单布局 schema 的子菜单最多一层；改 schema 必须写迁移与文档。
- **菜单模板打包**：`petcore/menu_templates/*.json` 必须进 PyInstaller `--add-data`；
  `load_default()` 有内置兜底常量，`--check` 输出 `menu_template=json|builtin`。
  任何情况都不许退化成只剩「设置/退出」两项。
- **菜单固定头尾**：`easter_egg` 永远是第一项；`playback_speed` 默认隐藏；尾部固定
  `modern_settings → separator → quit`。改默认模板时同步 `BUILTIN_DEFAULT_LAYOUT` 与
  `migrate_p8_menu()`，旧布局必须幂等迁移。
- **字号/图片大小**：`ui_font_size`（10–18）同时作用于设置页、右键菜单、彩蛋菜单行；
  `bubble_text_scale`（50–300）作用于气泡；`easter_egg_image_scale` 与
  `self_talk_image_scale` 只改显示上限。新增字号消费点必须读这三个键，不许另写死值。
- **点击反馈顺序**：`click_show_balance` 开着时余额气泡先冒，点击台词
  （`_notify_click()`）**等余额气泡收起后**才通知——`SpeechBubble.closed` → `_flush_click_talk()`，
  宿主在余额结果展示后调 `window.notify_click_balance_presented()`。连点合并成一条，
  无气泡可等时 9s 兜底（`CLICK_TALK_FALLBACK_MS`）；不许再写成「先 notify_click 再请求余额」
  （那样台词会先闪一下再被余额盖掉）。
- **自言自语配图**：`self_talk_image_chance`（0–100，默认 30）决定是否出图；空目录用
  `assets/big_blue_fat_fish`；出图失败必须回退 `say()`。点击台词固定 `force=True`。
- **弹弓右键**：右键进瞄准以及发射/取消后的 0.35s 内，`contextMenuEvent` 必须直接
  return；否则「发射玩法」会误弹菜单。
- **设置关闭语义**：标题栏 X = `_commit()` 保存并关闭；「取消」/Esc 恢复打开时快照。
  改 `closeEvent`/`accept`/`reject` 时三者的语义必须保持这个区分。
- **设置窗口按钮**：`SettingsDialog` 保留原生标题栏并带 `WindowMinMaxButtonsHint`
  （最小化/最大化）；不要改成 frameless 自绘标题栏，也不要把最大化做成“铺满内容区”
  的假最大化。最小化/还原时非模态、实时预览与保存/取消语义不变。
- **直播捕获兼容**：`stream_capture_mode` 开启时宠物与气泡必须是普通顶层 `Window`
  （清掉 `WS_EX_TOOLWINDOW/WS_EX_NOACTIVATE`），关闭恢复 Tool；不得因此禁用多宠。
- **动画缩略图**：分类子菜单只在 `aboutToShow` 填充；解码只能在非 GUI 线程（≤2），
  GUI 线程只转 `QPixmap`；`menu_animation_icons=False` 时不得发起解码。
- **点击/拖放监听**：`InteractionAdapter` 必须能 `attach_window()` 到新生成的子宠；
  `PetManager.spawn()/restore()` 负责调用。新增依赖点击/拖放的 feature 不许假设
  「只有启动时那只宠」。
- **隐藏态不冒泡**：`PetWindow.say()` / `show_image()` 必须先判 `isVisible()`；
  `set_visible(False)` 必须 `bubble.hide()`。第三方 feature 通过 presentation 端口
  说话时同样受这条闸门约束，不要自己直接操作气泡。
- **全屏自动隐藏**：`auto_hide_fullscreen`（全局键，默认 **True**，同主分支）由
  `platform/fullscreen.py`（零 Qt 探测：整屏几何 + 无标题栏/置顶，`QUNS` 只认 3/4，
  非 Windows 报「不支持」而不是假装命中）与 `ui/fullscreen_hide.py`（进程内唯一 1Hz
  `QTimer`）负责。命中只隐藏**当时可见**的宠并记账，离开只恢复它们；全屏期间用户手动
  显示/隐藏的宠以用户为准（记 override，不再抢回）；开关关闭或平台不支持时**不建定时器**。
  **翻转要连续 `confirm_polls`（默认 2）拍一致**（1 Hz 下约 2 s）：Task Manager/UWP/焦点
  闪动不许单拍命中就 hide/show 全部宠；`taskmgr.exe` 视为非全屏应用。
- **锁屏/睡眠停播**：`SessionWatcher` 的 `WM_WTSSESSION_CHANGE`（WTS lock/unlock）与
  `WM_POWERBROADCAST`（suspend/resume）必须回调
  `CoreHost.pause_for_session()`/`resume_after_session()`；锁屏时停播放节拍、碰撞表与
  输入轮询，解锁续播当前帧（不回第 0 帧）。没有锁屏/睡眠事件时行为逐位不变。
- **角色别名**：`character_aliases`（全局键，`{角色id: 名字}`，≤24 字符，空名=默认）是
  显示名的唯一来源；`CoreHost.characters()/character_for()/available_characters()` 一律经
  `_display_spec()` 出「别名 or 目录名」，**不要把别名写回目录层 `_characters`**。两个入口
  （切换角色子菜单末尾「重命名当前角色…」、常规 → 角色行「重命名…」）共用
  `ui/rename_dialog.py`；菜单入口立即落盘，设置页入口跟随该页的 dirty/取消快照语义。
- **托盘菜单独立**：托盘右键菜单由 `CoreHost.build_tray_menu()` 构建，必须含
  「显示/隐藏桌宠」（隐藏后能只靠鼠标唤回）、设置、数据目录、重载、逐宠控制与退出；
  不要再用完整右键菜单代替托盘菜单。托盘左键也切换显示。
- **托盘 Context**：`TrayIcon._on_activated(Context)` 不得 `refresh()`/替换菜单；
  菜单动作在 `aboutToShow` 里原地重建。替换正在弹出的 context menu = 菜单空白。
- **自语出图**：`media/talk_images.pick_choice()` 是唯一抽图口径；`_speak()` 必须
  `try/finally` 保证异常后仍排下一次。图片目录无图时回退内置 `big_blue_fat_fish`；
  出图失败回退文字。点击台词与自言自语共用这套逻辑。
- **配图诊断**：`--check` 的 `self_talk_images` 字段是安装包素材/Qt 插件是否正常的
  第一证据；新增配图路径或插件时不要删它。

- **配图设置归位**：彩蛋与自言自语配图统一在「互动与物理 → 彩蛋与配图」；
  自言自语页不再重复放图片目录/大小/概率，避免两个入口互相覆盖。
- **鼠标穿透（仅左键）的开关只在托盘**：右键菜单模板不含 `click_through`，旧布局由
  `migrate_p9_menu()` 幂等摘除；托盘「鼠标穿透」一次作用所有桌宠并气泡说明恢复位置。
  穿透开启后：左键单击在松手时转发给下层窗口（`SendInput` 一次 down+up，转发期间临时
  挂 `WS_EX_TRANSPARENT`，`_through_hold_until` 由窗口独占、逐像素轮询不得抢写），
  左键长按（`POINTER_DRAG_HOLD_MS`=250ms）才拖动；右键单击=旧左键点击、右键长按
  （`POINTER_MENU_HOLD_MS`=350ms）=菜单；拖拽中右键仍进弹弓。Windows 上**不许**整窗挂
  `WS_EX_TRANSPARENT`（右键会一起被放走）；非 Windows 仍走旧整窗穿透，如实登记右键不可用。
- **碰撞表按需启停**：`PetManager` 只在 ≥2 只宠时启动 16ms 碰撞表（Coarse），单宠不空转；
  `collision_enabled` 由 `_tick_collision` 每拍早退——按配置停表会让实时开关再也起不来。
- **对话通道唯一**：mod 只能经 `HostPorts.chat`（`ChatPort`）驱动聊天窗口；窗口、
  Markdown、会话、取消都在 core。`token/done/error` 可能来自后台线程，controller
  必须用信号排队，不许在 worker 线程碰控件；`petcore/chat/` 保持 Qt-free。
- **对话窗是主分支移植**：`petcore/ui/chat/` 的现代双栏窗（`deepseek-sidebar` /
  `chat-main` / `chat-main-header` / `floating-composer`）是唯一实现，不许再另写
  一套极简窗口。窗口按 `(owner, pet_slot)` 隔离；`ChatPort.record()` 只写会话和
  已存在的窗口，**不得因后台结果主动唤出窗口**。
- **流式传输只在 core**：SSE/代理兜底/取消/错误分类都在 `petcore/api/chat.py`；
  扩展与 `petcore/chat/` 不得 import `petcore.net`/`petcore.api`（架构守卫）。
- **截图动作条**：core 只提供框选、四动作条（提取文字/翻译/问问肥鱼/复制图像）、
  结果窗与剪贴板；OCR 引擎、翻译提示词、AI 问答全在 mod。`ScreenAskPort.on_action`
  按 `priority` 从小到大调用、空串/异常即换下一个，「本地优先、AI 兜底」由注册顺序
  决定，core 不认识任何具体引擎。
- **本地 OCR 归 mod**：`tool.ocr` 用 rapidocr + 随包模型（`resources/rapidocr_models`
  经 `ResourcesPort.bundle_path` 暴露），core 不 import rapidocr、不写业务提示词；
  识别失败返回空串交给 AI，不把引擎异常弹给用户。
- **快速对话**：点桌宠气泡 → `SpeechBubble.clicked` → `CoreHost.open_quick_chat`
  → `QuickChatPort`；窗口与输入在 core，流式回答在 mod。与完整聊天窗**共用同一
  会话**（提交前 core 落 user、完成后落 assistant），不得另建历史。
- **气泡可点**：气泡里的文字/图片 QLabel 必须 `WA_TransparentForMouseEvents`，
  否则点击被标签吞掉，快速对话永远打不开。
- **随附扩展任务**：`packaging/petcore.iss` 的 `mod_ocr` 与其他官方 feature 一样
  默认不勾选；勾选才把 `tool.ocr` 写到 `{app}\mods\tool.ocr` 并带 `.trusted`。
- **思考/联网传输只在 core**：`thinking` / `reasoning_effort` 的 payload、思维链
  `reasoning_content` 解析（只走 `on_reasoning`，不拼正文）、联网检索轮与
  `fetch_url` 都在 `petcore/api/chat.py`；搜索引擎实现只在 `petcore/net/websearch.py`。
  mod 只能传 `WebSearchOptions` 与回调；搜索 Key 只进钥匙串
  （owner `core.websearch`），`config.json`/`api.json` 不许出现。
- **联网检索是增强项，失败必须退回纯 API 回答**：`stream_chat()` 对 `research()` 与
  `inject_search_context()` 各包一层降级（状态行 + 原始会话），`research()` 内部对
  planner/工具的失败只发状态、不抛；异常**只记类型**（文本可能带 URL/查询串）。
  主分支口径见 `pet/chat/providers.py` 的 `_research()`／`stream()`：
  「联网检索不可用（…），将直接回答」，然后照常发正式请求。
  `research()` 返回 `ResearchOutcome(notes, searched, failed)`（planner 失败 / 任何工具
  失败 / 预算超时都算 `failed`）；**一条资料都没拿到**时正式请求前要给会话补一条 system
  指令，要求回答开头说明「联网检索暂时不可用，以下内容未联网核实」，并冒状态行
  「联网检索暂时不可用，已改用纯 API 回答」——只靠一闪而过的状态行，用户会把模型记忆
  当成刚查到的实时信息。
- **截图冻结帧必须是 QImage**：`grab_screen_image()` 接收 Qt6 的 `QPixmap` 时要
  `toImage()` 并 `setDevicePixelRatio(1.0)`；覆盖层构造函数再做一次兜底。
  `QPainter.drawImage(QRect, QPixmap)` 会抛 TypeError，整层画不出来 = 全屏灰。
- **思考/检索进度只进已开窗口**：`ChatPort.reasoning()/status()` 不得创建或唤出
  对话窗；`reasoning`/`activity` 随 assistant 记录落盘并在重开会话时回显。
- **截图不落盘**：`ScreenPort.capture()` / `capture_region()` 返回内存 PNG；转 data URL
  由调用方用标准库完成，core 不写临时文件、日志不记录图片内容。
- **看看屏幕**：答复先落发起宠气泡，再 `ChatPort.record()` 静默入历史；默认不打开
  对话窗，只有 `ai_look_screen_auto_open` 为真才主动唤出。4 秒冷却与忙碌提示同主分支。
- **截图提问端口**：框选、窗口、多轮消息在 core（`ScreenAskPort`），模型调用由 mod
  在 `on_ask(question, png, history, slot)` 回调里完成；回调跑后台线程，只碰纯数据。
  截图问答标题固定 DS 鲸鱼；等待时 10% 抽鲸蛙，每次请求只抽一次，独立于菜单彩蛋。
  **历史每次采集独立**：`open()` 清空上一轮，只存内存、不落盘（图片受隐私约束，
  纯文本脱离图片没有追问语义）。
- **mod 运行时代码无 Qt**：`host/runtime.py` 这类运行时代码不得 import PySide6；
  需要回 GUI 线程一律 `ports.scheduler.call_later()`。只有设置页（`settings_page.py`
  这类受信 UI）可以用 QtWidgets。包入口用 `host/__init__.py:create_host` 时，
  loader 会按包加载并支持相对 import。
- **配置读取分两层**：运行时只经 `HostPorts.config`（只读 `ConfigPort`）；
  设置页作为 Core 托管的受信 UI，允许直接使用 `host.config`（ScopedConfig）。
  不许把 `config` 对象塞进业务运行时长期持有。
- **命令按宠派发**：`run_feature_command(..., slot=)` 对声明 `slot` 参数的扩展命令
  必须传入发起者；子宠菜单的 AI/截图不得落到主宠窗口。
- **聊天窗外观**：`chat_background` / `chat_accent` / `chat_follow_pet` /
  `chat_always_on_top` 四个键必须有设置入口；改完经
  `ChatController.apply_appearance()` 实时作用到已打开窗口。`chat_follow_pet`
  用 `PetWindow.pet_moved` + 40ms 合并定位，不许 60Hz 反复重排。
- **四条热键独立**：显示隐藏 / AI 对话 / 看看屏幕 / 截图提问各有独立 Win32 id 与
  配置键；注册失败必须气泡 + 设置行可见，扩展缺失时提示「AI 扩展未安装」。
- **气泡同层**：`always_on_top` 变化必须同步 `bubble.set_host_on_top()`，彩蛋弹窗用
  `EasterEggPopup(..., on_top=)`；气泡旗标统一由 `SpeechBubble._window_flags()` 生成，
  不许在别处直接 `setWindowFlags`（会漏掉置顶/不接受焦点）。
- **图片装载按 DPR**：彩蛋弹窗/气泡配图/菜单头像一律走
  `media/images.py` 的 `load_pixmap/scale_pixmap/logical_size`：逻辑上限 × 屏幕
  `devicePixelRatio` 解码并写回 pixmap DPR，QLabel 只设逻辑尺寸。禁止把「已缩到
  逻辑像素、DPR=1」的 pixmap 交给 QLabel（1.5 倍屏会二次放大 = 用户说的「发虚」）。
- **卸载顺序**：先 `FeatureManager.stop_owner()` + `menu_model.unregister_owner()` +
  清包目录下的 `sys.modules`，再按 `install.json` 清单删文件，最后 `reload_mods()`。
  未登记文件保留并报数量；`feature-data/<id>`、配置键、钥匙串一律不动；
  `.trusted` 包同样可在设置内卸载，删 `{app}\mods\<id>` 并清 `mods-state.json` 停用记录。
- **扩展分组**：mod 设置页用一个 `group_heading(name_zh)`（H2）+ 若干
  `section(..., level=3)`（H3）；右键「扩展」按 `owner` 生成二级子菜单，
  `CORE_FEATURE_IDS` 不进入该菜单。单菜单项的 mod 也建子菜单。
  **`extensions_hub.children` 只表达可见性，不许平铺渲染**；`_append_node()` 收集
  children 作为 `allowed` 交给 `_populate_extensions()`。模板放在 hub 之外的 Core 项
  （links/快捷启动/网络/回收内存/免打扰）由 `migrate_p10_menu()` 幂等搬回模板位置。
- **设置宽行**：`SettingsCard.add_row(expand=True)` 必须是纵向布局（标题+提示占满整行，
  控件另起一行）；非 expand 行的文字块有 180px 最小宽度。宽控件不许与文字抢同一行，
  否则扩展管理的窄内容区会把提示压成一字一行。
- **气泡尺寸显式化**：`QLabel.setWordWrap(False)` + `_wrap_lines()` 自行折行；
  每次渲染用 `QFontMetrics` 算「最长行宽 + 4px（下限 96）」×「行数 × lineSpacing + 2」，
  `label.setFixedSize()`，副标题同宽并 `heightForWidth()`；不许再用
  `setMaximumWidth + adjustSize()`（Qt 的金比 sizeHint 会把短句压成两行并裁高）。
- **气泡分页**：长文本按「短 3 行 / 长 6 行」分页，页码点 + 滚轮翻页；
  点击仍只打开快速对话（`clicked` 信号不许被翻页吃掉）；副标题与正文同一
  `bubblePanel`，列宽走 `column_for_text()`（248→360 × 字号缩放，按屏宽收敛）。
- **SMTC 读取必须有界、卡死要降级**：`media.music` 的 SMTC 读取只走
  `now_playing.SmtcReader`（常驻 `music-smtc-reader` 线程、0.5s 硬超时（首读 ×4）、
  30s 降级窗、卡死线程每跨一次窗就退役并重探一次、`SMTC_MAX_STUCK_THREADS` 次后
  停止重试）。**不许**在采样线程上直接调 WinRT：`RequestAsync` 在系统媒体服务
  无响应时永不返回，`asyncio.wait_for` 拦不住同步 COM 调用，一次卡死就会让采样线程、
  `_inflight` 与整条识别永久停摆。退化（`smtc_degraded`）**不是** `smtc_error`：判定层
  要跳过 SMTC 改判窗口标题源；`runtime.poll()` 另有 `INFLIGHT_LIMIT_SECONDS=3` 的
  在飞看门狗（命中时直接把"读不到"交给判定层，不许只放开标志），`_worker_loop`
  必须包住 `_collect` 的异常。
- **同播放器暂停的 `peak` 判据**：`recognizer.AUDIBLE_PEAK`（0.005）——残留 Active
  会话峰值为 0，判暂停；SMTC 状态落后但真在响，判在播。只看 `state` 会在两种相反
  的情况下各错一次（"暂停了还在唱" / "在放歌却识别不到"）。这种暂停必须带
  `Decision.soft_paused`，运行时按 `SOFT_PAUSE_CONFIRM_TICKS=2` 迟滞：PAUSED 的
  缺席宽限是 0，单拍静音（淡出/间奏）不许把在播立刻收掉（否则 1Hz 释放—重播）。
- **音乐识别范围**：`music_filter_enabled` 默认关；开启后只放行
  `music_player_paths` 的 exe 名 + `music_filter_extra_ids`；否决表仍是第二道闸；
  过滤同时作用于唱歌、歌词与 `transport()`，被挡下时要点名播放器。
- **音乐识别 = 三源合成**（`media.music` 1.2.0，判定表在
  `docs/PETCORE-MOD-FORMAT.md` §13.3）：SMTC 主源、WASAPI 音频会话裁判"在不在出声"、
  已登记播放器的窗口标题兜底。**"任意进程在出声"不等于音乐**（游戏/语音不算）；
  **音频会话里查不到那个进程时按 SMTC 状态判**（蓝牙/独占/虚拟声卡不许误判暂停）；
  **"读失败"与"没在放"必须分开**（前者保留上一拍状态，后者按暂停并在宽限后解除循环）。
  窗口标题源只认 `players.KNOWN_MUSIC_EXES` ∪ 用户配置 ∪ 额外识别名，且必须有
  Active 音频；**不许把 VLC/mpv/PotPlayer 加进识别表**（放视频会被当成听歌）。
  **SMTC 会话处于暂停时仍要问窗口源**（"挂着的客户端 + 另一台在放"是常见姿势），
  有在播证据就以它为准并把理由写进 `reasons`。
- **app_id 先取 basename**：有些播放器把 app_id 报成完整安装路径；不取 basename
  会让静音/Inactive 证据失配，还会把安装路径带进气泡与诊断文本。
- **音乐状态机**：缺席不清歌词（只有换歌才替换）；唱歌失败按 2^n 拍退避、**永不永久
  放弃**；每拍一个采样令牌，迟到结果丢弃；多宠逐 slot lease / release（`for_slot`）。
  歌词取词失败按 60 秒冷却重试、每曲最多 3 次并在首次失败时说清原因；`say()` 失败
  不推进行号（同一行仍只冒一次）。
- **窗口抓屏只在 core**：`foreground_window()` / `sample_foreground()` /
  `capture_foreground()` 与 dHash 都在 `petcore/platform/`；mod 不许 import
  `petcore.platform`，只拿 `WindowSample`/`ScreenCapture`。**窗口矩形是物理像素**，
  Windows 上必须用 GDI `BitBlt`/`StretchBlt` 直接抓，不许再乘 DPR 或拿 Qt 的
  逻辑几何裁剪（150% 缩放下会裁到不足一半）。稳态只 `sample_foreground()`
  （缩略图指纹、不编码 PNG），画面变了才 `capture_foreground()`；基线固定用
  采样指纹。抓图前后必须各复核一次前台 `hwnd`，不一致丢帧；画面只存内存，日志
  只记进程名/句柄/指纹，不记标题正文。
- **主动识屏（`ai.chat`）**：8 秒心跳 + 四道门（白名单/停留/限额/去重）；
  白名单为空 = 不工作；答复只 `say()` 冒泡、用 `ChatPort.record()` 静默入会话，
  不许主动唤出对话窗；默认关闭；状态文件只在 `feature-data/ai.chat/`。
  `ai_proactive_*` 12 键全部全局作用域，键名与默认值见 SPEC §26。
- **配置写入端口**：`ConfigPort.set` 只给菜单勾选/命令切换这类显式用户动作
  （立即落盘）；运行时不许把配置当状态存储反复写，设置页仍走 `host.config`。
- **系统闲置**：`SystemPort.idle_seconds()` 非 Windows 返回 0（= 不判定闲置），
  端口缺失按 0 处理；不许在 mod 里自己调 Win32。
- **系统缩放（DPI）**：布局/钳制/命中一律逻辑像素；只有抓屏、指纹、送模型用物理
  像素。窗口抓图按 Win32 物理矩形用 GDI 直接抓，不许再乘 DPR。送模型的图必须先过
  `ScreenPort.shrink_for_vision()`（物理像素封顶 1024），本地 OCR/复制用原图。
  菜单图标与动画缩略图按 `screen_dpr()` 物理绘制/解码并写回 DPR；读图只走
  `media/images.py`。改这些链路前先看 SPEC §27。
- **Agent 联动只在 mod**：`ai.agent` 是 `examples/petcore-mods/` 里的可卸载扩展，
  Core 不得出现任何 Agent 名、事件名、阈值或动作池。Core 只提供三条接缝：
  `StoragePort.data_root()`、`PresentationPort.visible()`、`PetHandle.animations()`。
- **联动默认只读**：不自动装桥接插件、不跑 pnpm、不写 `~/.claude` 或任何 Agent 配置；
  只 tail 事件文件。**例外**：用户在「设置 → 常规 → DSH 连接」显式点击安装/更新时，
  可把随包 `integrations/dsh-pet-bridge` 与 `dsh-pet-channel` 写入 `~/.dsh`；写入前
  必须备份 profile 的 `package.json`，失败可见，绝不自动执行。
  首次见到已存在的文件跳到末尾（不回放历史），监视开始后出现的文件从头读；
  单次 ≤64KB，半行缓冲 ≤8KB，超限即丢弃。事件正文不落盘、不写日志。
- **DSH 指令**：`pet/command` 记录只接受 `say|play_animation|notify`，正文 ≤200 字、
  动作名 ≤64 且必须存在于当前角色；1 秒最多一条，隐藏态不播，未知动作静默丢弃。
- **联动线程边界**：文件 IO 只在 `runtime.py` 的轮询线程（1.5 s）；`LinkEngine` 与所有
  呈现调用只在 GUI 线程（解析结果经 `scheduler.call_later(0, …)` 回队）。`protocol.py` /
  `tailer.py` / `links.py` / `runtime.py` 不许 import PySide6。
- **联动完成语义**：按 `(agent, inst)` 分片折叠状态，任一写者忙即忙；完成通知只在
  `busy → 非busy` 后 800 ms 稳定确认时发，确认窗口内回到忙碌必须静默取消；本轮出现过
  `error` 报「这轮好像没能跑完」，不许报「干完活啦」。`turn/end` 带
  `reasonKind ∈ {aborted, blocked, max_tokens, unknown}` 收敛为 idle。
- **联动动作池**：`写代码`/`吃Token` 严格交替、每第 3 次插播 `轻快记录`/`漂浮踏步`；
  精确名缺失按关键词回退、再退到任意 act。隐藏态（`PresentationPort.visible()` 为假）
  不切动画。过程气泡同 Agent ≥10 s、全局 ≥8 s、同文案 ≥60 s，文案 ≤80 字。
- **灵动岛是 Core 窗口**：胶囊、卡片、绘制都在 `petcore/ui/island.py`；判定在
  `petcore/island/`（零 Qt），采样在 `petcore/platform/{sysmon,netmon}.py`（零 Qt）。
  不许把段判定写进窗口，也不许让窗口直接调 psutil。
- **宽度只由模板驱动**：段盒宽来自声明样本（`max_text`），**读数与告警级不进宽度键**。
  任何"按当前读数算宽"的写法都会让岛随数字抖；`+N` 的 N 按实际折掉的段数算。
- **看不清就不采**：岛隐藏、段未启用、**胶囊上**段被折进 `+N` 时必须停掉对应采样线程；
  CPU/内存采样间隔硬上限 1 Hz（更快没有意义且浪费电）。
  **卡片展开态例外**：每段都有自己的明细行，折行只是排版，不许据此停采——否则
  「推快照 → 折行 → 停采 → 清快照 → 复原」会变成 1 Hz 的启停振荡（审计 AUD-25）。
  卡片宽度必须取**胶囊态**（不折行）的自然宽，不许取折叠后的 plan 宽（越折越窄）。
- **告警只染数值**：`warn/crit` 只作用于数值与单位盒，label、竖线、段底不沾；
  无读数的拍沿用上一级（不褪色），`error` 段折叠时 chip 按 `crit` 报。
- **展开态预算**：卡片宽钳在 340–420；展开时行必须按**卡片宽**重排（放不下折 `+N`），
  收起时再用屏宽重算——两条路径共用 `layout.plan_info_row()`。
- **灵动岛默认关闭**：`island_enabled=False`；菜单「桌宠控制 → 灵动岛」与设置页同一开关，
  改动实时生效，取消回滚。
- **灵岛无头标 / 消息默认不显示**：胶囊左侧**不画**强调色圆点（`island_show_icon` 与
  「显示头标」已退役，内容直接从 `CONTENT_PAD = 19px` 起排）；展开卡片的「最近消息」
  由 `island_show_messages`（默认 `False`）控制——关闭时 `add_message()` 直接 return
  （不记录、不弹事件果冻）、卡片不出历史行、「清除消息」按钮隐藏。
- **岛拖动/停靠**：起拖阈值只能用 `QApplication.startDragDistance()`——`QWidget`
  **没有** `startDragDistance()`，写成 `self.startDragDistance()` 每次拖拽都会在
  第一帧抛 `AttributeError`（退化成单击，用户说「拖不动」）。拖动期间 `_update_size()`
  直接 return（几何冻结）、清 `_geo_from/_geo_to`（否则动画每 16ms 把窗口拽回去）；
  落点按**松手光标**选屏（`screenAt`），先夹回屏幕再判停靠，顺序不许换。
- **停靠口径**：距边 ≤`DOCK_THRESHOLD=40`（四边等权、取 `abs`）→ 收成
  `STRIP_THICKNESS=16` 细条（左右停靠长 64，竖直中心用 `rect.top() + h // 2`，
  不许用 `QRect.center()`，否则每循环漂 1px）。纯函数在
  `petcore/island/layout.py::dock_edge_for/strip_rect_for`，窗口层只做 QRect 互转。
- **细条不采样**：`_strip_visible()`（已停靠 + 没滑出 + 没展开）时内容一像素都不画，
  `_sync_sampling()` 必须据此停掉全部采样器；细条态 `_clamp_to_screen()` 直接 return。
- **悬停滑出**：可悬停区同时含「滑出胶囊」与「细条」两张矩形、逐矩形 `contains`，
  不许取包围盒；离开 800ms 收回且收回前必须复检，否则细条盖回光标会形成 1s 自持振荡。
- **岛几何动画**：停靠/滑出/展开/收起一律走 `_animate_to()`（180ms `ease_out_cubic`、
  16ms 表仅动画期间运行、每帧 `set_geometry_changed_callback` 通知宿主）。布局定型
  （`setFixedSize`）会立刻改几何，想播动画必须先退回起点再插值；不可见时直接落位。
- **岛反馈动效**：悬停缩放 1.02（弹簧，钳 0.97–1.03，展开态强制 1.0）、事件反馈
  果冻（竖压横鼓、绕胶囊中心、钳 0.85–1.12），两者都写在 `_on_anim_tick`；
  新加形变通道必须同时给钳位与停止阈值，否则连撞会把振幅泵到画不出来。
- **探针在 platform、接线在 ui**：GPU（`platform/gpu.py` + `gpu_monitor.py`，ctypes PDH +
  DXGI 名字）与 FPS（`platform/fps.py` + `fps_permission.py`，随包 PresentMon 子进程）
  都零 Qt、零网络库、不 import `pet.*`。岛**不许**直接调平台：它在 `_sync_sampling()`
  里算「还需要哪些段」（可见 + 段启用 + 未被折进 `+N`），交给
  `ui/probes.py:IslandProbeHub` 启停采样器并逐秒推快照；空集要停采并把段推回**定宽占位**
  （盒样本/单位与真实读数同一套，停采不缩行——否则「推快照 → 变宽 → 折进 +N → 停采 →
  变窄 → 复原」就是 1Hz 的折叠振荡）。`clear_probe_snapshot()` 只留给关停/停用。
- **探针不伪造读数**：GPU/FPS 只有 `ok` 带读数，其余一律 `None` + 具体原因；`unsupported`
  与「宿主没接探针」**不占位**，原因只写卡片一行；探针已接上但还没有第一份读数时占一行
  同宽占位（`--`，`sampling`）。首次读数前是 `sampling`（PDH 基线），
  不是 0。
- **GPU query 属于创建线程**：跨线程 `read`/`close` 必须拒绝（owner 线程已退出时由新线程
  接管并重建基线，否则「停采→恢复」永久失效）。`poll()` 由调用线程付冷启成本（约 0.3 s），
  GUI 线程只许 `snapshot()`。
- **FPS 权限不许自动提权**：`fps_permission.request_access()` 只在用户点设置页按钮时弹
  一次 UAC；点「否」是 `cancelled` 不是错误。没有 Performance Log Users 时如实报
  `access denied`，不许写成「0 帧」或静默。
- **FPS 目标生命周期**：目标正常退出（PresentMon 退出码 0）记 `target_gone`，**同一个
  死 pid 不许再开会话**，只有 `set_target(新 pid)` 才解除；`set_target` 同 pid 幂等
  （不重置重试代次/去抖）；换目标要过 `MIN_SESSION_SECONDS=3` + `TARGET_CHANGE_DEBOUNCE=2`；
  进程级一次残留 `dsh-pet-fps-*` ETW 会话清扫（`logman query -ets`，只停属主已死的会话，
  查询失败不关闸），`hub.shutdown(wait_seconds=1.0)` 有界等待 worker 收口。
- **迁移幂等只认标记**：`<root>/migration.json` 的 `completed` 是唯一判据，**不许**用
  「目标文件不存在」代替（第一次启动后永久失效）。七个域各一个 `migrate(ctx)`，只返回
  条目；备份/写失败由 `MigrationContext` 记账。配置域**只补缺不覆盖**，且直接写合并后的
  原始字典——走 `Config.save()` 会被 `DEFAULTS` 填满，后续域再也补不进去。
- **迁移不搬密钥到配置**：`api.json`/`config.json` 永不出现 Key；凭据只做钥匙串到钥匙串
  （`dsh-pet-standalone` → `dsh-pet-core`），值不进日志、报告与异常文本。回滚靠标记里的
  `restore` 映射（backup 为空串 = 删除新建文件），原旧文件一律不动。
- **工作线程回 GUI 只经 `gui_dispatch`**：`QTimer.singleShot(0, cb)` 两参重载在没有事件
  循环的工作线程上永不触发；用 `QtScheduler`（信号排队）。同理，持锁调
  `Future.cancel()` 会让 PENDING 任务的 done_callback 同线程重入同一把锁 = 死锁，取消必须
  在锁外做。


- **授信 = 位置 + 标记**：`mods.trust.is_trusted()` 只认 `paths.install_mods_dir()` 下的
  `.trusted`；本地导入（ZIP/目录）落盘前必须剥掉包里自带的标记（`install(keep_trust_marker=)`
  只给市场 `BUNDLED` 来源用）。包内容不能宣布自己被信任（审计 AUD-01）。
- **安装包信任锚是清单**：`UpdateService.install()` 除自洽侧车外，还必须与
  `select_asset(manifest)` 登记的文件名/大小/摘要三者一致；侧车同目录可写，不能自证。
- **config 的 `extra` 区块**：`config.json` 顶层只允许 `DEFAULTS` 键；第三方扩展偏好
  一律落 `extra`（`Config.get/set` 对调用方透明）。键名匹配
  `^[a-z0-9][a-z0-9._-]{0,127}$`，单值 ≤4KB、总量 ≤64KB，命中密钥样式的值直接拒绝。
- **迁移回滚必须可追溯**：`rollback()` 覆盖/删除前先留 `<target>.pre-rollback-<时间戳>`；
  同一 target 的多条 restore 只按**最早**那条恢复；`not_found`/`failed_write` 都算失败，
  只有全部成功才删标记。域级 `completed` 只在**同批没有失败**时写入（审计 AUD-06/09）。
- **`--check` 只读、`--strict` 才是构建门**：`--check` 用 `Config.load(persist=False)`，
  不写 `config.json`、不建 `migration-backup`；构建脚本用 `--check --strict` 并解析
  `check.json` 复核 `menu_template=json`、`builtin_readable>0`、`characters` 非空。
- **SSE 必须给完整性信号**：`ChatResult.incomplete` / `end_reason`（`done|eof|empty|cancelled`）；
  没有 `[DONE]`、没有 `finish_reason`、或响应体为空时，调用方必须提示，不许当完整回答落盘。
  HTTP 200 + `data: {"error":…}` 要按状态码分类抛错，不许吞成空回答。
- **错误分类不许说谎**：调用方只认 `NetworkError.reason`（`auth/rate_limit/network/timeout/tls/
  protocol/internal`）。异常没有 `.reason` 时**必须是** `internal`，不许退回 `network`——那会让
  「服务没选/Key 没配/JSON 坏了」显示成「网络不可达」，用户去找路由器。`ai.chat` 的四条失败路径
  （主聊天/快速气泡/看看屏幕/截图问答）统一走 `_failure_reason()` 并记一条
  `ai call failed: type=… reason=… service=… cause=…`（只打类型名，不打 Key/URL/响应体）。
  **传输抖动换路由重试**：`api/chat.py` 只在「正文与思维链都还没吐出去」时重发，最多
  3 次，路由计划依次是「默认（代理优先）→ 只直连 → 只代理」（`STREAM_ROUTE_PLANS`、
  `RETRY_DELAY_S`）；可重试分类是 `network`/`tls`/`protocol`/`timeout`（含
  IncompleteRead/BadStatusLine 这类半截响应），`HttpStatusError`（服务器明确答复）与
  取消从不重试；已经显示给用户的半条回答绝不重发（会重复正文）。
- **「固定直连」有期限且可撤销**：`net/http.py` 的进程级结论只在
  `DEFAULT_DIRECT_PIN_SECONDS`（45s）内生效，过期后重新试代理；钉着直连时直连再失败
  会把代理还回去试一次，代理成功即解除钉子，两条路都不通则清掉结论——一次加速器/VPN
  抖动不许把进程永久钉在直连上。
- **对话超时取配置**：`CoreApiPort.stream_chat(timeout=None)` 用该服务在 `api.json` 里的
  `timeout`（钳到 5–300s），不许再写死 60s——设置页那一行必须真的生效。
- **只有 API 主机可以改写进程级路由**：`urlopen(..., pollute=False)` 之外的调用点只允许
  `api/chat.py`、`api/probe.py`、`account/service.py`（同一台 API 主机的成败才算「这台 API 能不能
  走代理」）。搜索/读页/市场索引与下载/更新检查/歌词/天气一律 `pollute=False` 或走 `fetch_url`；
  `tests/test_petcore_http_safety.py::test_only_api_hosts_may_pin_the_process_route` 会红。
- **换路由要重建请求**：`ProxyHandler`/重定向会**就地**改写 `Request`；`urlopen` 每次尝试
  必须 `clone_request()`。换路条件除传输失败与 `retry_statuses` 外，还要求响应**不带**
  `Retry-After`。
- **5xx 是网关的错，不是链路的错**：`urlopen` 收到 500/502/503/504（且无 `Retry-After`）
  时，只要代理与直连确实是两条不同的路（`_has_other_route`：没有系统代理或 `no_proxy`
  命中就不重复问），就换一条路再问一次；两条路都 5xx 抛最后一条的答复。401/403/429 与带
  `Retry-After` 的答复不换路。`routes=` 是**单次调用**的路由计划（`(False,)` = 只直连），
  它**不改写**进程级「固定直连」结论。
- **设置页差异写入**：页面的 `_on_changed` 只写「与上次写出的值不同」的键；整组回写会把
  右键菜单里刚改的勾选项按旧控件值翻回去。菜单编排只写内存，落盘统一由
  `SettingsDialog._commit()` 在所有页通过后做。
- **节流缓存与实例隔离**：`--instance` 会设置 `PETCORE_INSTANCE`，缩略图等进程级缓存目录
  按实例分目录；`--root ""` 按未指定处理（Windows 上 `Path("")` 是当前目录）。

- **音频端点 = 线程 + 设备键**：`audio_sessions` 的 manager 缓存按
  `(线程 ident, device_id)` 复用；换线程/换设备必须立即重建，不走 30 秒节流。
  端点枚举只在设置页短命线程或 30 秒缓存里做；默认端点 id 短缓存 2 秒、runtime
  每 5 秒比较一次。手选端点拔掉回退默认，不许写回配置。
- **色键直播窗**：`stream_capture_keycolor` 为真时宠物/气泡必须是**不透明**窗口
  （`WA_TranslucentBackground=False`）并整窗填 `stream_key_color`；直播模式置顶
  只许 `SetWindowPos`，不许 `setWindowFlags` 重建 HWND；每次改旗标后必须重放
  `apply_stream_window`（否则 `WS_EX_NOACTIVATE` 复活）。捕获自检只读，不写
  窗口标题/正文到日志。
- **ASR partial 不覆盖手打**：`AsrChannel.partial` 只更新预览或空输入框的镜像；
  输入框已有用户文本时只显示提示行；final 才 `insert_text`。同刻最多一个增量
  识别在飞，`sapi` 间隔 ≥1.5s；录音线程退出必须收口。
- **会话归档兼容**：`ChatSession.archived_at` 是 schema 1 的可选键（缺失=未归档）；
  `list_sessions()` 默认过滤归档。侧栏组头与 `currentRowChanged` 解耦，点组头只
  折叠；归档/删除当前会话必须由 controller 回退到下一个可见会话或新建，不许让
  消息继续写进已隐藏会话。
- **人格卡只写用户目录**：`<data_root>/feature-data/ai.chat/personas/`；包内
  `persona_presets/*.json` 只读。同 id 覆盖内置；删除/恢复内置只删用户文件。
  runtime 合并池按目录+文件 mtime 缓存，保存后下一轮生效。
- **设置搜索注册**：无可见文案的行用 `register_search_terms()` 或
  `SettingsCard.add_row(search_keys=...)` 登记；`page_search_texts` 必须覆盖
  QTreeWidget 项。`reveal_text` 对下拉/列表只滚动定位，**不许改当前值**。
- **自语每日句**：状态落 `feature-data/fun.self-talk/daily.json`，按 slot 记日期；
  只有气泡真的显示成功才标记已说；连说复用 `say_sequence`，不许自己拼定时器。

## 4. 变更纪律

- 行为修复先写会红的测试，再改产品代码；Qt 行为用真实事件循环或 `offscreen`。
- 不修改 `pet/` 产品代码来让 petcore 通过；需要适配时只改 `media/` 或
  `content/legacy.py`，并在 log 里说明。
- 不引入新依赖前先确认 `requirements.txt` / 现有 imports 是否已有；没有就说清楚。
- 不提交 git，除非用户明确要求。
- 临时脚本、截图、探针输出放 `.scratch/`，不要进版本库。

## 5. 验证命令

```bash
cd E:/Work/dsh-pet-0
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py -q
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest \
  tests/test_petcore_*.py tests/test_architecture.py \
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q
.venv/Scripts/ruff check pet/ tests/ petcore/
.venv/Scripts/python -m petcore --check
```

## 6. 文档地图

| 想知道什么 | 读什么 |
|---|---|
| 最终要做成什么样 | [`../docs/PETCORE-SPEC.md`](../docs/PETCORE-SPEC.md) |
| 分层、启动路径、数据目录 | [`../docs/PETCORE-ARCHITECTURE.md`](../docs/PETCORE-ARCHITECTURE.md) |
| mod 格式与加载 | [`../docs/PETCORE-MOD-FORMAT.md`](../docs/PETCORE-MOD-FORMAT.md) |
| 角色包与素材 | [`../docs/PETCORE-CHARACTER-PACKS.md`](../docs/PETCORE-CHARACTER-PACKS.md) |
| 批次与下一步 | [`../docs/PETCORE-ROADMAP.md`](../docs/PETCORE-ROADMAP.md) |
| 还差主分支什么 | [`../docs/PETCORE-MAIN-PARITY-BACKLOG.md`](../docs/PETCORE-MAIN-PARITY-BACKLOG.md) |
| Agent 联动的事件协议 | [`../docs/AGENT_LINK_PROTOCOL.md`](../docs/AGENT_LINK_PROTOCOL.md)（实现见 SPEC §28） |
| 改过什么 | [`../docs/PETCORE-LOG-INDEX.md`](../docs/PETCORE-LOG-INDEX.md) |
| 上手命令与目录 | [`README.md`](README.md) |
