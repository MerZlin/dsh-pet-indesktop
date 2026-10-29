# petcore 变更日志索引

> 读取规则：**先读本表，只打开命中的 `LNNN` 条目**。完整的改动细节、验证命令与
> 遗留项在 [`PETCORE-LOG.md`](PETCORE-LOG.md)；本表只保留「一句话 + 关键词」，
> 避免把整份 log 塞进上下文。
>
> 写入规则：每次追加 log 条目时同步加一行；`tests/test_petcore_docs.py` 校验
> `LNNN` 一一对应。
>
> 编号约定：历史行里的 `P1-A…P8.4` 是旧编号，等价于
> [`PETCORE-ROADMAP.md`](PETCORE-ROADMAP.md) §0 的 `H1-A…H8.4`；
> 2026-10-22 起新计划使用 `P0–P6`，两套不要混用。

| 编号 | 日期 | 批次/主题 | 一句话 | 关键词 |
|---|---|---|---|---|
| L001 | 2026-10-20 | M0–M6 | 从 `main` 新建分支，落地可独立运行的 petcore 最小 core、mod 加载器、设置宿主与多屏几何。 | core、contracts、mod、--check、pack-legacy、多屏 |
| L002 | 2026-10-20 | P1-A | 右键/托盘菜单与设置页视觉对齐主分支，菜单改为可自定义布局。 | 菜单 QSS、图标、侧栏、MenuEditor、menu_layout |
| L003 | 2026-10-20 | P1-C | 设置/关于非模态可拖宠、大小 10–300%、设置实时预览、菜单默认隐藏大小、菜单/设置覆盖矩阵、文档体系。 | 非模态、实时预览、300%、coverage、spec/log/README/AGENTS |
| L004 | 2026-10-20 | P1-E | 贴边/落位/恢复/比例/跨屏迁移改用 `body_box` 身体框，透明画布允许出屏；记录 P1-F 边缘探头设计。 | body_box、贴边、clamp_body、body_rx、corner、edge probe |
| L005 | 2026-10-20 | P1-F | 边缘探头状态机、45° 旋转、命中反向映射、设置/菜单开关。 | edge probe、45°、PEEKING、旋转、head_box |
| L006 | 2026-10-20 | P1-B | 全局快捷键、自启 CLI、多实例、打包入口脚本、WebM→WebP 首跑供给。 | hotkey、autostart、instance、PyInstaller、provision |
| L007 | 2026-10-20 | P1-D | `pack-character` 从外部素材目录生成 content 角色包。 | character pack、pack-character、manifest、body_box |
| L008 | 2026-10-20 | P2-A | 关于并入设置、单进程多宠、Q弹/拖拽/弹弓/回旋/投喂/碰撞、菜单重排、本地 PyInstaller+Inno 构建。 | about、multi-pet、Q弹、physics、collision、PyInstaller、Inno |
| L009 | 2026-10-20 | P2-B1/B2 | 内置离线 mod 加载器与 12 个官方 mod、HostPorts 扩展、菜单/设置覆盖。 | builtin mod、ports、self-talk、quick-launch、quiet、links、memory、network、music |
| L010 | 2026-10-20 | P3 收口 | 设置页聚合到「扩展管理」页内分页；5 个 mod 移为 examples 可导入样板；快捷启动移入工具与帮助。 | settings tabs、ExtensionsPage、examples、allow_feature_mods、quick launch |
| L011 | 2026-10-20 | P3 收口 | 移动朝向按方向设置、位移按 move_curves 帧驱动，修复反向/走滑。 | facing、move_curves、move_position_at_frame、quantize_move |
| L012 | 2026-10-20 | P3 收口 | 子命令前的全局 `--root/--portable/--instance` 生效；裸 QWidget 设置页可见但标注不保存；重打包 0.1.0。 | CLI、subcommand、--root、ExtensionsPage、PyInstaller、Inno |
| L013 | 2026-10-20 | P4 收口 | 朝向口径改为主分支（right 才镜像）、回旋起表、碰边逐次反馈、探头吸附、多宠防穿模与落座、设置搜索/分页、安装器选装扩展与无终端窗口。 | facing、golden spin、bounce、edge snap、collision、spawn、search、Inno、trust |
| L014 | 2026-10-20 | P5 易用性 | 气泡贴头部锚点、台词多行大框、Enter 保存、免打扰页文案/状态、命令反馈 force、菜单对勾、碰撞改三圆链。 | bubble、anchor、QPlainTextEdit、Enter、quiet、force、checkmark、circles |
| L015 | 2026-10-20 | 碰撞修复 | `set_velocity` 重锚内部坐标（修瞬移）；被撞的探头宠物先退出探头再分离（修被撞斜）。 | teleport、set_velocity、probe、collision、tilt |
| L016 | 2026-10-20 | P5 net 测试 | 为 `petcore/net` 补 33 条传输兜底与错误分类单测；架构守卫未豁免 `net/` 的 urllib 导入仍红。 | net、urlopen、force_direct、classify、retry_after、architecture |
| L017 | 2026-10-20 | P6 动画节拍 | 待机一圈改为掷骰（修一直 idle）、gap 期间继续播待机、四项概率可调、播放动画菜单分组。 | idle、roll、weights、animation gap、re-arm、menu |
| L018 | 2026-10-20 | P6 事件动画 | `events/<group>` 独立为 GraphType.EVENT，`play_event()` 按名字/索引触发，余额动画不再混入随机动作。 | EVENT、event_group、balance、play_event、legacy |
| L019 | 2026-10-20 | P6 core 联网/API | 网络守卫改 allowlist；`petcore/net` + `petcore/api` + keyring + 设置页「API 与账户」+ `HostPorts.api`；构建脚本收集 keyring。 | net、api、keyring、ApiPort、api.json、settings、allowlist |
| L020 | 2026-10-21 | P7 探头/击飞 | 碰撞先授权击飞旋转再退出探头；飞行不掷骰/点击忽略；探头与彩蛋只播 idle/转向。 | collision_throw、throw egg、thrown、probe filter、click |
| L021 | 2026-10-21 | P7 安装/扩展管理 | setup 带鲸鱼娘图标；随附扩展改到 `{app}\mods`；扩展页改竖向导航+基础/加装分组；mod 中文名 `name_zh`。 | SetupIconFile、install_mods_dir、ExtensionsNav、name_zh、filter |
| L022 | 2026-10-21 | P7 彩蛋/音效 | 彩蛋图片弹窗与点击音效包（默认/小黄鸭/自定义）；碰撞/碰边复用音源。 | easter egg、click_sound_pack、duck、wav、QMediaPlayer |
| L023 | 2026-10-21 | P8 多宠/气泡/设置/打包 | 轻碰推动与探头优先、子宠单独退出、气泡跟随/图片、彩蛋菜单行、互动 8 分页、Core 特性归位、扩展分组折叠、安装目录页与菜单模板兜底。 | collision push、probe、quit_this、bubble follow、image、menu builder、host_page、collapsible、DisableDirPage、menu_template |
| L024 | 2026-10-21 | P8.1 字号/菜单/弹弓/直播 | 界面/气泡字号、彩蛋图片大小、自语配图概率、彩蛋置顶与菜单尾部、播放动画缩略图、弹弓右键抑制、点击监听补挂、X 保存、直播捕获兼容。 | ui_font_size、bubble_text_scale、self_talk_image_chance、migrate_p8_menu、thumbnail、slingshot、attach_window、stream_capture_mode |
| L025 | 2026-10-21 | P8.2 隐藏气泡/配图/托盘 | 隐藏态不冒泡并收起气泡；彩蛋与自语配图合并到「彩蛋与配图」；托盘菜单支持显示/隐藏、设置、逐宠控制与退出。 | hidden bubble、bubble hide、easter+self-talk images、tray menu、toggle visibility |
| L026 | 2026-10-21 | P8.3 托盘/自语出图 | 托盘菜单不再在 Context 时替换（改原地重建）；自语/点击台词共用配图选择器，失败回退文字且异常后继续排队；`--check` 增加配图目录/可读数。 | tray context menu、aboutToShow、talk_images、pick_choice、self_talk_images、readable |
| L027 | 2026-10-21 | P8.4 穿透归位/高清 | 点击穿透移出右键菜单改由托盘统一开关（p9 迁移）；彩蛋/气泡/头像按屏幕 DPR 解码，修 1.5 倍屏发虚。 | migrate_p9_menu、tray click-through、media/images、DPR、load_pixmap、logical_size |
| L028 | 2026-10-22 | P0 主分支对齐审查 | 新增主分支功能矩阵、AI 交接计划和审查报告；冻结每宠作用域、WorkerHost、迁移回滚、默认扩展和发布门。 | parity matrix、handoff、audit、scope、worker、migration、rollback |
| L029 | 2026-10-22 | P1 配置作用域/基础迁移 | 设置窗口绑定当前宠物；全局/每宠键分流；点击台词/自语按宠物运行；standalone 与 slot 基础迁移并备份。 | ScopedConfig、pet scope、settings target、legacy migration、slot、feature lifecycle |
| L030 | 2026-10-22 | P2 WorkerHost/离线接缝 | 真实子进程 WorkerHost、角色目录导入、Graph 类别映射、5 个官方 feature 默认安装。 | WorkerHost、JSONL、cancel、heartbeat、character importer、Graph、installer tasks |
| L031 | 2026-10-22 | P3 Core 账户/余额基础 | Core 账户服务、余额缓存/错误分类、钥匙串读取和 `ApiPort.fetch_balance()`。 | AccountService、BalanceResult、cache、keyring、ApiPort、DeepSeek |
| L032 | 2026-10-22 | P3 每宠会话/worker 请求接缝 | 增加按 slot/角色隔离的会话存储、消息限制和 WorkerSession.request 协议接缝。 | SessionStore、ChatSession、slot、character、request、delta、done |
| L033 | 2026-10-22 | 插件市场 v1 | 静态索引 + 多源 artifact + SHA-256 + 安装/更新/回滚 + 市场设置页 + `petcore market` CLI；npm 只留 URL 接口。 | market、index、sha256、Range、resume、backup、rollback、market-lock、page_market、allow_feature_mods |
| L034 | 2026-10-22 | 安装器口径修正 | 5 个官方扩展恢复默认不勾选（iss `Flags: unchecked` + 测试反转），文档口径同步；顺手修正 README 的安装位置。 | opt-in、unchecked、iss、installer、examples README |
| L035 | 2026-10-22 | 编号统一 | 历史批次改名 H1–H8.4，P0–P6 只表示未来交付阶段；ROADMAP §0 加换算表，SPEC/LOG-INDEX/INDEX 同步。 | numbering、H series、P0–P6、ROADMAP §0、mapping |
| L036 | 2026-10-22 | P3 流式/窗口/截图/热键 | Core 流式 SSE、ChatPort 聊天窗口、内存截图端口与四条可并存热键；菜单/设置同步。 | stream_chat、ChatPort、ChatWindow、ScreenPort、hotkeys、ai 快捷键 |
| L037 | 2026-10-22 | P3 ai.chat 扩展 | host-only AI 对话扩展（prompt/路由/截图提问/设置页）+ 安装器可选任务。 | ai.chat、host-only、prompt、vision、installer、opt-in |
| L038 | 2026-10-22 | 构建签名/打包收尾 | 本机开发证书签名 exe/setup（SAC 4551 拦截），构建脚本支持 -SignThumbprint，P3 安装包产出并验证。 | code signing、Smart App Control、4551、SignThumbprint、ISCC、setup |
| L039 | 2026-10-22 | P3 对话/识屏/截图提问 | 主分支现代双栏对话窗移植、看看屏幕气泡+静默入历史、框选截图问答窗与 10% 鲸蛙彩蛋、`ScreenAskPort`、按宠命令派发。 | ChatWindow、modern UI、record、ScreenAskPort、capture_region、jingwa、slot dispatch |
| L040 | 2026-10-22 | AI 边界收口 | `ai.chat` 拆成包入口+prompts/runtime/settings；线程安全 Scheduler、只读 ConfigPort、移除 SettingsPort；聊天窗背景/强调色/跟随/置顶设置与实时预览；截图历史明确不落盘。 | package entrypoint、SchedulerBridge、ConfigPort、runtime no Qt、apply_appearance、follow pet、trusted settings UI |
| L041 | 2026-10-22 | 截图灰屏 + 思考/联网 | 冻结帧强制 QImage/DPR=1（QPixmap 导致 drawImage 崩溃=灰屏）；thinking/reasoning_effort、思维链面板与回显；联网 planner（web_search/fetch_url）、四引擎、搜索 Key 只进钥匙串。 | grab_screen_image、QPixmap、reasoning_content、research、websearch、thinking、ChatPort.status |
| L042 | 2026-10-22 | 截图四动作 + 快速对话 | 动作条补齐提取文字/翻译/问问肥鱼/复制图像；OCR 归 `tool.ocr`（本地 rapidocr 优先、AI 兜底）；`on_action` 按 priority 回退；点桌宠气泡开快速对话并与完整窗共用会话。 | RegionPick、on_action、priority、tool.ocr、rapidocr、QuickChatPort、bubble.clicked |
| L043 | 2026-10-22 | 动作条对比度 | 显式深色按钮文字/禁用灰，修深色主题下浅条白字看不清；像素级回归 + 重建安装。 | QSS、ButtonText、对比度、screen-selection-bar |
| L044 | 2026-10-22 | AI 模块分布盘点 | core/mod 代码表、core 里没有的业务、启动/传输/窗口/OCR 实测占用与四项已知耦合；纯文档批次。 | AI 模块、core 占用、7.1MB、145MB、ChatTruncated、ARCHITECTURE 23 |
| L045 | 2026-10-22 | P4-a 取数/天气/文件解读 | 新增 `HttpPort`（按请求路由、共享 deadline、4MB 上限）与 `tool.weather`、`tool.file-reader` 两个可导入 mod；安装器两条 unchecked 任务；`files.read` 能力。 | HttpPort、fetch_url、prefer_direct、retry_statuses、tool.weather、open-meteo、tool.file-reader、on_drop、files.read |
| L046 | 2026-10-22 | P4-b 语音/音乐/循环 | Core 新增 `SpeechPort`（edge/豆包/MiMo/SAPI + 缓存/预检/重试/钥匙串）与 `play_animation(loop)`/`release_loop()`；`voice.chime`、`media.music` 重写为 1.1.0（歌词两源、SMTC、唱跳）。 | SpeechPort、edge-tts、SAPI、cache、preflight、voice.chime、media.music、lyrics、SMTC、loop、release_loop |
| L047 | 2026-10-23 | P4-b2 分组/卸载/气泡/音乐识别 | 右键「扩展」按 mod 分组、mod 设置页 H2/H3；`.trusted` 包可卸载（清单删除、保留未知文件与个人数据、草稿保护）；气泡 248→360 列宽 + 分页/页码/滚轮 + 副标题；音乐只识别已配置播放器、设置页切歌、气泡「我在唱《…》」。 | H2/H3、owner 分组、remove_at、install.json、trusted、bubble paginate、column_for_text、subtitle、music_filter、allowed_app_ids |
| L048 | 2026-10-23 | P4-b2.1 菜单/设置/气泡修正 | 扩展 hub 不再平铺 children、按 allowed 分组；`migrate_p10_menu` 把 links/快捷启动/网络/内存/免打扰搬回模板位置；expand 设置行改纵向；气泡改 `wordWrap=False` + 显式固定宽高，短句不再折行/裁切。 | extensions_hub、allowed、migrate_p10、p10、settingTextBlock、wordWrap、setFixedSize、heightForWidth、孤行 |
| L049 | 2026-10-23 | 设置窗口标题栏按钮 | `SettingsDialog` 补 `WindowMinMaxButtonsHint`，标题栏出现最小化/最大化；非模态与保存/取消语义不变。 | WindowMinMaxButtonsHint、showMinimized、showMaximized、settingsDialog |
| L050 | 2026-10-23 | P4-c1 窗口抓屏 + 主动识屏 | 前台窗口 hwnd/rect、`capture_window()`、dHash；`ai.chat` 8 s 看门狗（白名单/停留/限额/去重）、12 个 `ai_proactive_*` 全局键、菜单勾选项 + 设置分节；只冒气泡不唤窗。 | foreground_window、capture_foreground、dh_hash、image_hash、ProactiveWatcher、ProactiveLimiter、ai_proactive_*、ConfigPort.set、idle_seconds、ai.chat:proactive、GDI BitBlt、sample_foreground、SAC 4551 |

| L051 | 2026-10-23 | DPI 适配：模型图封顶/图标缩略图 | 新增 `VISION_MAX_EDGE` 与 `ScreenPort.shrink_for_vision()`（物理像素封顶 1024），AI 四处送图统一走它；菜单图标/动画缩略图按 `screen_dpr()` 物理绘制；SPEC §27 固化「逻辑 vs 物理」口径。 | DPI、devicePixelRatio、shrink_for_vision、VISION_MAX_EDGE、physical_side、availableSizes、缩略图、逻辑像素、物理像素 |
| L052 | 2026-10-23 | P4-c2 Agent 联动 | `ai.agent` 只读联动扩展：DSH 桥目录 + `agent-events/*.jsonl` + 自定义文件，六态 → 动作池/过程气泡/完成确认/音效；Core 加 `data_root()`/`visible()`/`animations()` 三条接缝与 13 个 `ai_agent_*` 键。 | ai.agent、Agent link、ByteTailer、GlobTailer、LinkEngine、六态、data_root、visible、animations、ai_agent_*、mod_agent |
| L053 | 2026-10-23 | P5-a1 灵动岛 v1 | 顶部胶囊 + CPU/内存/网络段 + 告警色 + 展开卡片 + 13 个 `island_*` 键；契约/布局零 Qt、采样 1 Hz、宽度只由模板驱动。 | 灵动岛、island、segments、layout、plan_info_row、+N chip、sysmon、netmon、island_metrics、dynamic_island、状态点 |

| L054 | 2026-10-24 | 已复现缺陷收口 | Python3.11清理、Qt菜单父子/刷新释放、非阻塞世代采样、折叠采样与Agent停止重启。 | onerror、QMenu、SamplingLoop、cancel、island、Agent |
| L055 | 2026-10-24 | 后台资源与逐域修复 | HTTP关闭、Speech线程归属、天气/文件/音乐/AI迟到结果和取消、proactive身份、MSG ABI。 | close、scheduler、generation、vision、SMTC、MSG |
| L056 | 2026-10-24 | 质量证据与阶段校正 | 五域审查、亮暗菜单回归、性能与源码实机、整仓失败归属；不宣布全绿/不进入分发。 | PR报告、性能、125%DPI、全量缺口、6签、8节日 |

| L057 | 2026-10-24 | P4-c3 A质量门禁 | 隔离环境、设置原生退出/菜单/Pillow根因修复；整仓9716passed正常退出、三遍高负载与本批实机证据；B–E尚未实施。 | Python3.11、DeferredDelete、QMenu、全量、性能、原生退出 |

| L058 | 2026-10-24 | P4-c3 B通用端口 | slot交付、DND汇总、单语音队列/实际播放完成及生命周期取消；整仓9729passed、源码Windows与高负载验证通过。 | speech、on_finished、notification、quiet、generation |

| L059 | 2026-10-24 | P4-c3 C | 待办调度、slot持久化与安全迁移，关联95passed，最终联合证据见L061 | todo、宽限、去重、迁移 |

| L060 | 2026-10-24 | P4-c3 D | 节日历法与生日调度、默认关闭和让位规则，关联126passed；联合验收见L061 | festival、生日、农历、去重 |


| L061 | 2026-10-24 | P4-c3 E | 48签/真实结束揭签/有界预热、chime让位；整仓9837passed正常退出、三遍高负载与实机/性能通过 | divination、48、on_finished、迁移、生命周期、性能 |

| L062 | 2026-10-24 | 分发详细交接与提交 | T00–T09、三worker写集与安全/验收边界；主交付 `b77d4436`，复验见交接报告 | handoff、分发、计划、多智能体、提交 |

| L063 | 2026-10-24 | 动画画质评估计划 | 同源素材/DPR/Q70证据、T00–T06串行步骤与停止门；仅文档落盘，产品未改 | 动画、DPR、Q70、小小桌宠、计划、未实施 |

| L064 | 2026-10-24 | 分发计划本机环境校正 | 路径/pwsh/venv(3.12.10)/PyInstaller/rapidocr 事实按本机重写，T00/T08 口径同步；文档批，整仓复跑 | 分发计划、环境、3.12、PyInstaller、rapidocr、全仓 |

| L065 | 2026-10-24 | P4-c3-D1/T01 契约冻结 | 预算/传输/来源三契约、fetch_artifact 签名、origin 剔除包内 .trusted；11+63 测试与 ruff 通过，解包预算待 T03 | 分发、ArchiveLimits、TransferSpec、PackageOrigin、trusted、契约 |

| L066 | 2026-10-24 | P4-c3-D1/T02+T03+T05 | 逐跳传输/解包预算/可复现构建器 三 worker 并行；46+62 测试、ruff、4 包构建与 verify 4/4；新增 allowed_hosts | 传输、Content-Range、416、预算、构建器、可复现、allowed_hosts |

| L067 | 2026-10-24 | P4-c3-D1/T04 | 市场安装接 fetch_artifact + limits、能力/api/core 四类交叉校验、锁失败回滚、回环 http 契约；1738 passed | 授权、verify_manifest、事务、回滚、allow_loopback_http |

| L068 | 2026-10-24 | P4-c3-D1/T06 | 根索引（blocked）+ file: 索引/artifact + 市场页销毁持有线程；22 passed，file: 从 42s 降到 0.67s | 根索引、file://、dispose、QThread、nofallback |

| L069 | 2026-10-24 | P4-c3-D1/T07（D1 收口） | 真实 ZIP + file: 索引的授权/安装/加载/升级/回滚/数据保真；5 passed，60 循环资源无残留 | 端到端、FeatureManager、回滚、SHA、资源循环 |

| L070 | 2026-10-24 | D2/T08 冻结包 | `-OutputRoot`/`-PythonPath` + iss 源目录覆盖（修「打旧包」）、开发证书签名、冻结包自检与四包安装；D1 验收说明 | 打包、签名、Inno、SAC、冻结包、验收说明 |

| L071 | 2026-10-24 | D2/T09 收口 | before/after 无回退、整仓 9941 passed、安装器四组合矩阵、发布手册；未发布 | 性能对照、整仓、安装器、发布准备 |
| L072 | 2026-10-25 | 市场生命周期与授权修复 | 应用级市场任务 registry、可恢复下载、feature 解包预算、file 授权、结构化回滚、candidate/published 索引状态；90 passed | QThread、resume、archive limits、file://、rollback、candidate |
| L073 | 2026-10-25 | WorkerHost 与 DPR 渲染收口 | worker 握手/心跳/取消/代次拒收，帧按屏 DPR 使用物理像素并保留镜像；5 passed | worker、generation、heartbeat、DPR、frame_pixmap |
| L074 | 2026-10-25 | 重构收尾状态与交付证据 | candidate/published 文档边界、市场/Worker/DPR 收口范围、最终门禁待补原始输出 | 交付、parity、handoff、验收边界 |

| L075 | 2026-10-25 | 运行时迁移与 Worker 收口 | 迁移状态/诊断、session/todo schema、Worker 有界退出和非法消息拒收；新增 156 行回归 | migration、schema、Worker、shutdown |
| L076 | 2026-10-25 | 角色导入与媒体生命周期 | ZIP 安全导入、点击按下/释放 token、彩蛋头像/提示、关闭音频取消 | character、ZIP、click sound、easter egg |
| L077 | 2026-10-25 | parity 设置与动画入口 | ZIP 设置入口、余额异步查询、彩蛋设置契约、GraphType 懒加载菜单 | settings、balance、GraphType、menu |
| L078 | 2026-10-25 | 重构收尾文档与 petcore-only 最终门禁 | 运行时/媒体/parity 状态、最终报告、交接断点；petcore 1479 passed，专项门禁 302 passed，Ruff 通过 | 交付、petcore-only、报告、handoff、缺口 |

| L079 | 2026-10-25 | 候选包构建交接与弱模型任务说明 | PyInstaller/Inno Setup、隔离验收、SHA 证据、停止条件与 petcore-only 门禁；不上传 Release | 候选包、安装器、交接、证据 |
| L080 | 2026-10-25 | petcore 摸头互动 | 头部命中、连续往返判定、可配置单次动画池、设置契约与回归证据；1490 passed；实现提交 `2a5f80e9` | head_pat、head_box、InteractionPage、动画池、生命周期 |
| L081 | 2026-10-25 | P5 收口 | 灵动岛、余额、菜单/设置几何、性能治理和 Core 更新服务已实现，petcore-only 门禁通过，冻结包与 GPU/FPS 实机留缺口。 实现提交 `ce8c67fb`。 | island、balance、UI、performance、update、petcore-only |

| L082 | 2026-10-25 | P5 更新取消与可选探针收尾 | 更新下载中/取消状态和 generation 拒收，GPU/FPS 可选 snapshot 接入；Ruff、聚焦 121、CPU 压力 3×121、petcore 1513 通过；实机/冻结包仍留缺口。 实现提交 `ce8c67fb`。 | update、cancel、GPU、FPS、island、petcore-only |
| L083 | 2026-10-26 | P5 远端交付 | 实现 `ce8c67fb` 已推送目标分支；提交前 Ruff、petcore 1513、文档/架构 306 通过，回填 SHA 与验收边界。 | Git、push、petcore-only、交付 |
| L084 | 2026-10-26 | P5 硬伤修复 | 余额投递改走 QtScheduler、cancel 锁外取消、两个菜单项落模板、避让用 body_rect 且不落盘、更新取消/侧车摘要/安装门禁、段 unsupported 不占位、性能计数出口；1522 passed。 | balance、deadlock、menu、island、update、performance |
| L085 | 2026-10-26 | 旧数据全量迁移 | `petcore/migration/` 七域（配置/slot/活跃集合/会话/待办/API/凭据）+ 标记幂等 + dry-run + 回滚 + `petcore migrate` + `--check` 诊断；迁移族 41 passed。 | migration、marker、rollback、sessions、todos、api、keyring |
| L086 | 2026-10-26 | GPU/FPS 真探针 | PDH 读数器与 DXGI 名字、PresentMon 单会话与 1 秒事件窗、一次性 UAC 授权、岛探针 hub 与按需启停、打包随附 PresentMon；本机 GPU 真读数，FPS 权限受限；1612 passed。 | GPU、PDH、FPS、PresentMon、probes、packaging |
| L087 | 2026-10-26 | petcore 全量代码审计（只读） | 九域并行深读 + 红队证伪，28 条发现（P0×1/P1×9/P2×12/P3×6）：本地导入 `.trusted` 绕过授权、rollback 非回滚、Config 丢未登记键、旧键 41/159 静默丢失、代理兜底空转、reload 跑旧代码、SSE 断流当成功、岛展开致探针 1Hz 振荡、API 页按钮不可达、打包收束失效；红队推翻 1 条、降级 6 条。 | audit、security、migration、config、http、reload、island、settings、packaging |
| L088 | 2026-10-26 | 安全与授权（审计修复） | 授信=位置+标记（导入剥 `.trusted`）、更新安装回查清单、`file://`/内网拦截与重定向剥头、压缩包 ADS/保留名/大小写重名。 | security、trust、update、redirect、zip |
| L089 | 2026-10-26 | 配置与迁移完整性（审计修复） | config `extra` 区块（未知键可存、密钥值拒绝）、补 41 条同名旧键并记 dropped、`--check` 只读、坏配置留档、回滚留 `.pre-rollback` 且失败保标记、completed 判据收紧、待办历史改 list[str]。 | config、extra、migration、rollback、dropped |
| L090 | 2026-10-26 | 网络与对话正确性（审计修复） | `urlopen` 每轮重建请求、Retry-After 不换路、新增 tls 分类、`Body.truncated` 拒半截音频、SSE `incomplete/end_reason` 与错误帧、检索 45s 预算、取消打断阻塞读、余额单飞、兜底音色缓存键。 | http、sse、tts、balance、cancel |
| L091 | 2026-10-26 | 运行时卫生（审计修复） | 重载前 detach + 清子模块、`SystemExit` 转诊断、`cancel_owner` 真实现、`on_tick` 返回注销函数。 | reload、mods、scheduler、uninstall |
| L092 | 2026-10-26 | UI 可达性与采样口径（审计修复） | 岛卡片态不停采且卡片宽不再棘轮、设置页横向滚动、互动页差异写入、菜单编排不自行落盘、搜索含按钮/下拉、覆盖表修正、探针每拍调和重试。 | island、probes、settings、search、coverage |
| L093 | 2026-10-26 | 结构守卫与构建门（审计修复） | 守卫解析相对 import + 三组新红线、枚举下沉 contracts、`-OutputRoot` 收束与 `-DryRun`、`--check --strict` 构建门、presentmon 前置校验、capabilities 读出口。 | architecture、contracts、build、strict |
| L094 | 2026-10-26 | 画质与动画（审计修复） | 命中与绘制互逆、冷 meta 墙钟推进 + 看门狗、壁纸按 DPR、`load_pixmap` 按需解码、portable 根/空 root/实例缓存目录。 | hit-test、move、dpr、images、paths |
| L095 | 2026-10-26 | 点击显示余额验收与构建冒烟等待修复 | 余额链路本就在 P5 实现（配置键→设置页→点击→单飞→气泡/岛），补 4 例点击回归；修构建冒烟对 -Windowed exe 不等待（& $exe 52ms 返回）导致读旧/缺报告的问题，改 Start-Process -Wait 并加新例。 | click_show_balance、balance、Start-Process、build gate、setup |
| L096 | 2026-10-26 | 图标槽位与灵动岛停靠/动效移植 | 菜单/侧栏图标收到 18px 槽位（设计稿缩放 + 笔宽补偿），动画缩略图裁 alpha 包围盒后适配槽位；修灵动岛拖不动（QWidget 无 startDragDistance）、移植 40px 贴边停靠/16px 细条/悬停滑出/180ms 几何动画/悬停缩放/事件果冻，细条停采。 | icons、thumbnail、island、dock、GeoAnimation |
| L097 | 2026-10-26 | 图标重量与菜单/侧栏几何对齐 | 笔画改回随画布缩放（18px ≈0.8px、地板 0.75px）；墨量 settings 120→64；菜单行高 32→24、`icon-size: 18px`、item `3px 29px 3px 13px`；侧栏行高 34、图标按 16 绘制；缩略图填充率 1.04；新增墨量/行高回归。 | icons、stroke、menu、sidebar、thumbnail、QSS |
| L098 | 2026-10-26 | 外观页内分页、点击查看余额与峰谷提示 | 外观页切四页（大小与外观/灵动岛/文字与快捷键/屏幕与位置）；点击查看余额搬到「点击与音效 → 余额提示」；新增 `petcore/account/pricing.py`（档位/下一档位/措辞/标签/颜色/HTML 转义）；4 个全局峰谷键；余额气泡副标题接线。 | appearance、tabs、click_show_balance、pricing、peak、tier、subtitle |
| L099 | 2026-10-26 | 灵动岛五项视觉动效收尾 | 五态状态点（含 4s 完成瞬态与感叹矢量）、工作态声纳环、回复后 5s 呼吸、130ms 内容淡化 + 内容位图缓存、三层投影/sheen/软陶 bevel/glass 渐变/竖线渐变/头标内环/单位 α；chat/截图/显隐/余额四处真实状态来源。 | island、dot、sonar、breathe、fade、cache、shadow、sheen、bevel |
| L100 | 2026-10-26 | 本轮文档、门禁、打包与提交 | SPEC/AGENTS/BACKLOG/INDEX 同步；三份 PR 报告；`.gitignore` 补 `rel-*/`；全量门禁、构建签名安装器与推送。 | docs、spec、agents、backlog、build、gitignore、release |
| L101 | 2026-10-26 | 菜单图标 DPR 修复与灵动岛去头标 | 图标 DPR 只乘一次（修 150% 下字形放大并裁掉右下角）、勾选标记挪行尾让勾选项恢复图标、子菜单图标回退链与 8 枚缺失字形、截图提问/主动识屏换 distinct 图标；灵动岛删头标与 island_show_icon、新增默认关闭的 island_show_messages。 | icons、dpr、menu、check-layer、tray-menu、submenu-icon、island、header-dot、messages |
| L102 | 2026-10-26 | 点击余额与点击台词的先后顺序 | `click_show_balance` 时余额气泡先冒，点击台词排队到 `SpeechBubble.closed` 之后再通知（连点合并、9s 兜底、缓存同步路径单独识别）；宿主在余额结果展示后调 `notify_click_balance_presented()`。 | click-balance、click-talk、bubble、closed、queue |
| L103 | 2026-10-26 | 灵动岛碰撞回归 + C 盘剩余段 | 岛墙挂 `_clamp_position` 同步钳制 + 抛掷反射 + 事件驱动拍飞（闸门/岛速采样照桥）；撞岛反馈单闸 0.25s；落岛当落地；新增默认开的 `island_collision` 与默认关的 `disk` 段（碰撞层 0 定时器 0 线程；磁盘段 5s 一拍、一条按需线程）。 | island、collision、stadium、throw-reflect、island-speed、bump、disk、diskmon |
| L104 | 2026-10-26 | 设置「扩展管理」两轴弹性 | 导航宽随字号/内容弹性（下限恒 190、上限 260×字号系数）、浏览器吃满窗口高度（下限 420）、本页内容不再封顶 860，其它页仍 860；导航字号与行高同源（13→34/32、18→39/37）；`reflow()` 钩子让字号热切换不必重建对话框。 | extensions、geometry、nav-width、fill-height、font-scale、reflow、qss |
| L105 | 2026-10-26 | 音乐识别重写：三源 + 状态机 + 歌词打分 | SMTC + WASAPI 音频会话 + 播放器窗口标题三源判定；修 8 个老缺陷（抖动清歌词、唱歌永久放弃、取词拉黑、切宠残留、无令牌、纯音乐先唱、say 失败推进、inflight 卡死）；歌词候选四维加权 + lrclib + 查询词清洗；设置页识别诊断 + `scripts/music_probe.py`；新键 `music_title_format`。 | music、smtc、wasapi、audio-session、window-title、recognizer、sampling、diagnostics、lyrics-score、lrclib、music_title_format |
| L106 | 2026-10-27 | 语音全链路 + champion 彩蛋 + 关于页致谢/签名 | 新增「语音」页与本地 HTTP TTS provider（OpenAI 兼容/通用 JSON、强制直连、缓存键含 endpoint/model）、自语/点击台词语音与 8% 多气泡彩蛋、关于页六区致谢（作者/贡献者/特别鸣谢/issue·PR/粉丝分页搜索/唯一签名）、夸克分享地址；B 站粉丝快照已抓（3948 位）。 | speech、local-tts、voice-page、self-talk、champion、credits、fans、signature、bilibili、github、cookie、keyring |
| L107 | 2026-10-27 | Smart App Control 拦截 setup/卸载器：安装时签 unins000.exe | 定性为 SAC（`VerifiedAndReputablePolicyState=1`）拦下安装时生成、从未签名的 `unins000.exe`（CodeIntegrity 3077/3118，Defender 0 检出）；Inno 加 `SignTool`+`SignedUninstaller`，构建脚本传 `/Sdevsign`/`/DSignUninstaller`，新增可复用的单文件签名脚本与测试机装证书脚本；实测装/跑/卸全通过、3077 增量 0；不关闭 SAC。 | packaging、inno、signtool、smart-app-control、uninstaller、authenticode、build_petcore |
| L108 | 2026-10-27 | AI 三项改为扩展注册项 + 气泡层级跟随桌宠 | AI 对话/看看屏幕/截图提问 从 core 写死项改成 ai.chat 的 menu contribution（模板与兜底布局改 `ai.chat:*`、新增 `migrate_p11_menu`、菜单命令带子宠 slot）；气泡改为桌宠窗口的从属窗口、右键菜单走 `ui.menu.show_context_menu` 同层；扩展详情新增「来源」；安装器扩展勾选加 5 条 opt-in 门禁；清掉用户目录 4 个旧扩展副本。 | menu、registration、ai.chat、menu_layout、migration、bubble、z-order、installer、opt-in、mods |
| L109 | 2026-10-27 | 新增《petcore 扩展制作指南》（动手向） | 新建 `docs/PETCORE-MOD-DEVELOPMENT.md`：三类产物边界、四处落位（仓库/数据目录/安装目录/--mods-dir 及 feature disabled 判据）、最小可运行骨架（代码由脚本从文档抽出实测：validate→install→加载→菜单→设置页）、菜单与设置页真实接线键、端口选型与线程铁律、本地闭环与排错表、ZIP/安装器分发、角色包三条导入路径与不支持项、内容块未接线提醒；INDEX 登记并互链四份文档。 | docs、mod、feature、character-pack、pack-character、ports、settings-page、install、threading |
| L110 | 2026-10-27 | 新增 tool.ai-browser（AI 站白名单浏览器）+ 接口文档探针 | 8 站白名单（地址栏/主框架/站点按钮同源）、内嵌 QWebEngineView 窗口 + 命名 profile、无内核时降级 Edge `--app`/系统浏览器、设置页显示内核档位；真机实测首屏 2.2–4.9s、进程树 +337MB 且关窗不释放；文档与实机的五处差异写进报告。 | mod、ai-browser、webengine、allowlist、edge-app、fallback、host-capability、docs-gap |
| L111 | 2026-10-28 | 联网检索不再污染进程路由 + 断流保留半条回答 | `urlopen(pollute=False)` 让搜索/读页只做单请求路由（不再把「直连成功」钉成进程结论）、planner 改用 ≤20s 网络预算、读流已有增量时返回 `incomplete/interrupted` 半条结果、mod 失败分支用 `done` 保住已生成正文。 | websearch、urlopen、pollute、force-direct、proxy、planner、interrupted、partial、network |
| L112 | 2026-10-28 | 图片彩蛋支持 GIF 动图（气泡 + 弹窗） | 新增 `AnimatedImage`（QMovie 帧按 `逻辑×dpr` 解码、绘制前写回 DPR）与 `is_animated/logical_size_for`；彩蛋气泡与 `window` 弹窗两条路径都支持动图，静态图路径不变；收起/隐藏即停播。 | gif、qmovie、animated-image、dpr、easter-egg、bubble、images |
| L113 | 2026-10-28 | API 页出厂预填 DeepSeek + 开放平台跳转 | 新建服务草稿预填名称/Base URL/Chat Path/模型/视觉模型/余额协议（与主分支同值），小白只填 Key；新增「API 申请」行（只读 URL + 打开默认浏览器按钮）；新增 `ui/external_links.py` 统一外链常量。 | api-settings、deepseek、defaults、external-links、qdesktopservices、keyring |
| L114 | 2026-10-28 | 常规页新增两个素材包外链模块 | 「数据」卡片在「角色资源包」后追加「完整角色素材包」「高清完整角色素材包」两行，各两枚横排按钮（GitHub 下载 / 夸克网盘）；纯外链、不新增配置键、不改 coverage。 | settings、general、character-packs、github、quark、external-links、search |
| L115 | 2026-10-28 | 气泡/桌宠/右键菜单层级修复 | 非置顶时 `show()` 也会抬高 owner 链 → 新增 `_present()`（Win32 `SetWindowPos` 复位上邻居）、菜单会话 `menu_session()` 抑制桌宠与气泡 raise；保持气泡为桌宠 owned window。 | z-order、raise、owned-window、menu-session、setwindowpos、hwndprev、always-on-top |
| L116 | 2026-10-28 | 全屏自动隐藏（搬主分支） | 新增 `platform/fullscreen.py`（零 Qt 探测：整屏几何 + 无标题栏/置顶、QUNS 3/4、非 Windows 报不支持、自排除）+ `ui/fullscreen_hide.py`（唯一 1Hz 状态机：只隐藏当时可见的、离开只恢复自动隐藏的、用户手操优先、关闭即停表）+ 全局键 `auto_hide_fullscreen`（默认 True）+ 常规页一行。 | fullscreen、auto-hide、quns、foreground、timer、settings、global-key |
| L117 | 2026-10-28 | 音乐三修 + 展示会话闸门 | 唱歌循环被核心清掉后按 2^n 退避补播（丢租约不发 release）、歌词/唱歌持有 `presentation` 会话让自语气泡（及朗读）不再打断、单曲循环按进度回退/曲长回绕/暂停边沿重开一圈且复用缓存歌词；新增 `ui/presentation_gate.py` 与 `PresentationPort` 增量面。 | music、singing-rearm、loop-lease、presentation-gate、scene、lyrics、single-track-loop、self-talk |
| L118 | 2026-10-28 | 角色重命名（别名） | 全局键 `character_aliases`（≤24 字符，空名回默认）+ `_display_spec()` 统一显示名；右键「切换角色」子菜单末尾与常规页「角色」行两个入口，共用 `ui/rename_dialog.py`；不改菜单模板/coverage。 | character-alias、rename、display-name、settings、menu、global-key |
| L119 | 2026-10-28 | 清单收 `summary`/`summary_zh` + 首次未签名安装包 | `KNOWN_FIELDS` 补 `summary`/`summary_zh`（市场索引用同一名字；第三方清单写了会被当成 unknown field 警告，把 `--check --strict` 构建门顶红，实测命中用户目录里的 `dsh.channel`）；用 `build_petcore.ps1 -Clean -Installer -OutputRoot dist-unsigned-2026-10-28` 出了 275 MB 未签名 setup（NotSigned，SAC=0 下可启动、打包体 `--check --strict` 退出码 0）。 | manifest、summary_zh、unknown-field、strict-check、build、installer、unsigned、sac |
| L120 | 2026-10-28 | 联网失败不再谎报「网络不可达」+ 路由污染面清零 | `ai.chat` 四类失败改走 `_failure_reason()`（无 `.reason` → 新分类 `internal`，只打一条 `type/reason/service/cause` 日志；发请求前先 `_require_service()`）；真机探针证明启动窗口内没有任何请求会碰 `_force_direct`；顺手把 market/update 的 `urlopen` 改成 `pollute=False` 并加源码守卫（只有 API 主机可钉路由）。 | ai.chat、error-classification、internal、network、force-direct、pollute、log、market、update |
| L121 | 2026-10-28 | 音乐暂停：立即停唱 + 暂停期间不取词不报错 | `_silence_limit()` 对 `paused` 返回 0（宽限只留给 absent）；暂停分支不再 `_begin_track`，同名降级身份记别名；取词失败只在播放中冒泡、暂停窗内退配额；设置页 hint 同步「暂停立即停唱」。 | music、pause、grace、lyrics、track-alias、quota、silence-limit |
| L122 | 2026-10-29 | 对话传输瞬时失败自动重试一次 | `_Progress` + `_stream_once()`：只在「正文/思维链都还没吐」且分类 `network`/`tls` 时重发一次并提示「网络抖动，正在重试…」；`_open_response()` 共用打开阶段重试；`_force_direct` 加 300s TTL；对话超时改取 `api.json`；`ai.chat` 的 reason 文案补全中文。 | network、retry、tls、force-direct、ttl、timeout、api.json、reason-text |
| L123 | 2026-10-29 | 音乐暂停：同源窗口会话不再翻盘 + 收气泡 | SMTC 暂停时窗口源只认**另一台**播放器；新增 `PresentationPort.hide(owner)`（只收自己的）与 `PetWindow.hide_bubble()`，播放→暂停的边沿立刻收掉最后一句。 | music、pause、window-source、same-exe、hide、presentation-gate、bubble |
| L124 | 2026-10-29 | 移植重置本宠/重置全局按钮 | 新 `petcore/settings_reset.py` 按键作用域重置（全局键 vs 本宠键；保 `active_pets`/`positions` 与本宠身份位置；失败回滚）；设置侧栏底部两枚按钮 + 确认框；重置后直接关闭，跳过 `_commit` 与快照回滚。 | reset、settings、scoped-config、global-keys、identity、sidebar、rollback |
| L125 | 2026-10-29 | 点击/自语的语音出声 | `SpeechResult.volume` 携带合成时的有效音量，`CoreSpeechPort._play` 不再把 `SpeechRequest.volume=None` 交给 `float()`；两个内置 feature 的 `speak()` 带 `on_error` 记 `reason`。 | speech、tts、volume、playback、click-talk、self-talk、silent-failure |
| L126 | 2026-10-29 | 桌宠身份上限 16 → 99 + 碰撞粗筛 | `config.MAX_PETS = 99` 作为单一真值（`_pets()`、旧 slot 迁移、`PetManager`、设置页共用）；`rects_may_touch()` 在 `collide()` 入口做轴对齐粗筛，`PetManager` 按拍缓存身体框——99 只的碰撞拍从 28.4ms 降到 1.2ms。 | max-pets、identity、collision、broad-phase、rect-cache、multi-pet、settings |
| L127 | 2026-10-29 | 语音首次 import 串行化 + 测试禁真合成 | `_EDGE_IMPORT_LOCK` 串行化 edge-tts 的惰性 import；`conftest` 加**会话级** autouse 桩把 `net.tts.synthesize` 变成立刻抛 `network`（并压掉重试间隔）——全量套件里多线程首次 import 触发的 Windows 原生崩溃不再出现。 | crash、access-violation、ssl、import-lock、conftest、speech、test-hygiene |
| L128 | 2026-10-29 | 联网检索失败一律退回纯 API 回答 | `stream_chat()` 的 `research()` 与 `inject_search_context()` 各包一层降级：失败只发状态行（「联网检索不可用，将直接回答」/「联网资料无法并入提问」）并照常用**原始会话**发正式请求；异常只记类型。 | web-search、degrade、plain-answer、research、resilience、chat |

| L129 | 2026-10-29 | 探针 churn 收口 + 岛折叠振荡 | 岛 stop/hide/set_visible 当场交空集；停采推定宽占位（不缩行）；FPS 记 target_gone、set_target 同 pid 幂等、MIN_SESSION_SECONDS=3/去抖 2s；进程级一次 logman 残留会话清扫；hub.shutdown(1s) 有界等待。 | probe、fps、etw、logman、island、fold-oscillation、churn、shutdown |
| L130 | 2026-10-29 | 音乐取词单飞 + 身份稳定 + 负缓存 | 唯一常驻 `lyric-fetch` 线程（单槽队列/取消在飞/stop 有界 join）；播放分支同名降级身份记别名；失败按歌名 60s 负缓存；暂停窗口枚举 2s、SMTC 管理器/会话列表按线程缓存；`music_enabled=false` 不唤醒采样。 | music、lyrics、thread、cancel、negative-cache、identity、smtc、sampling |
| L131 | 2026-10-29 | DSH 联动动画不再每 2 秒重启 | `animation_state()` 判断上次请求仍在播就不重发；`AGENT_ANIMATION_MIN_SECONDS=8` 地板；`_requested_loop` 才允许 release_loop，一次性动作不误清别人的循环。 | agent-link、dsh、animation、restart、ffmpeg、churn、loop-lease |
| L132 | 2026-10-29 | 全屏连拍确认 + 任务管理器排除 | `confirm_polls=2` 连拍确认才 hide/show；`taskmgr.exe` 不算全屏应用；20 拍交替从 21 次 set_visible 降到 1 次。 | fullscreen、flap、confirm-polls、taskmgr、hide、quns |
| L133 | 2026-10-29 | 对话 5xx 换路 + 钉直连可撤销 + 联网失败说明 | `urlopen` 对 500/502/503/504 换路、`DEFAULT_DIRECT_PIN_SECONDS=45` 且钉住失败回代理；流式 3 次路由计划（默认→只直连→只代理）；`ResearchOutcome.failed` 时 system 提示联网工具暂不可用、不触发 chat.error。 | chat、http、proxy、502、force-direct、retry、web-search、fallback |
| L134 | 2026-10-29 | 仅左键穿透/右键交互 + 可调 Q 弹 + 帧/碰撞/锁屏 | Windows 穿透只放左键（松手 SendInput 转发、左键长按拖动、右键单击反馈/长按菜单）；`click_squash_bounces/strength/ms` 三键；渲染去每帧中间 pixmap；碰撞表单宠停表；锁屏/睡眠停播。 | passthrough、mouse、click-through、right-click、q-squash、elasticity、render、collision-timer、session-lock |
| L135 | 2026-10-29 | 帧表节拍回到 PreciseTimer | `create_frame_clip` 去掉 Coarse（Windows 15.625ms 颗粒把它量化成 31ms → 2x 只有 32fps）；实测 Precise 1x 23.84fps / 2x 47.62fps。 | animation、pacing、frameseq、timer、coarse、precise、2x-speed |
| L136 | 2026-10-29 | SMTC 硬超时降级 + peak 判据 | `SmtcReader`（专用线程 + 0.5s 硬超时 + 30s 降级窗 + 自愈）；退化不走 ERROR、改判窗口源；`poll()` 在飞看门狗 + `_collect` 异常兜底；同播放器 paused 用 peak 区分"真在播/残留 Active"。 | music、smtc、timeout、degraded、watchdog、peak、recognition、self-heal |
