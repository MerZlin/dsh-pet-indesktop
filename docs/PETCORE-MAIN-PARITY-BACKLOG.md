# 主分支 ↔ petcore 功能差距台账

> **基线**：`feat/petcore-vpet-architecture`（原P8为v0.1.0；当前本地候选v0.2.0，P4-c3 E）
> **对照对象**：主分支旧架构 `pet/`（v4.3.4 系列）
> **用途**：这是“还差什么”的唯一清单。每轮收口时更新状态；新发现但本轮不做的
> 缺口先记在这里，再决定进哪个批次。改代码前先查本表，避免把“示例 mod”当成
> “已对齐”。
>
> 状态口径：
> - **已对齐**：用户可见行为与主分支等价，且有测试或实机证据。
> - **部分**：有入口/降级实现，但形态或能力弱于主分支。
> - **未做**：petcore 完全没有对应能力。
>
> 关联文档：[`PETCORE-SPEC.md`](PETCORE-SPEC.md)（最终形态）、
> [`PETCORE-ROADMAP.md`](PETCORE-ROADMAP.md)（批次）、
> [`PETCORE-LOG-INDEX.md`](PETCORE-LOG-INDEX.md)（改动史）。

## 1. 已对齐 / 比主分支更强

| 功能域 | 行为 | 证据 |
|---|---|---|
| 窗口与输入 | 透明置顶工具窗、逐像素命中、点击穿透（开关只在托盘菜单 + 设置页）、窗口置顶、锁定位置、Shift 拖拽 | `tests/test_petcore_geometry.py`、`test_petcore_interaction.py` |
| 拖拽物理 | 弹簧跟随、松手按轨迹甩出、静止放手下落、甩出力度四档 | `engine/physics.py`、`test_petcore_interaction.py` |
| Q 弹 / 回旋 / 弹弓 / 投掷 | Q 弹、黄金回旋（含“跳过点击动画”子项）、弹弓瞄准发射、飞行弹跳逐次反馈 | `ui/effects.py`、`ui/motion.py`、`ui/slingshot.py` |
| 边缘探头 | 贴边 45° 探头、点击拉直、5 秒回探头、探头期只播 idle/转向、拖出甩飞授权击飞旋转 | `ui/edge_probe.py`、`tests/test_petcore_edge_probe.py` |
| 多宠 | 单进程多宠（上限 `MAX_PETS = 99`，含主宠）、独立配置 `config-pet-N.json`、落座避让、碰撞冲量、轻碰推动、探头宠不可被轻碰推走 | `ui/pet_manager.py`、`tests/test_petcore_pet_push.py` |
| 动画节拍 | 待机一圈掷骰、四项行为权重可调、动作间隔、播放速率、手动播放入口、事件动画 `play_event()` | `engine/behavior.py`、`tests/test_petcore_animation.py` |
| 气泡 | 头部锚点、跟随桌宠移动、文本/图片两种内容、彩蛋图片可进气泡；气泡与彩蛋窗跟随宿主置顶（同图层） | `ui/bubble.py`、`tests/test_petcore_bubble.py` |
| 大小 | 10%–300%、滑条 + 手输 + 四档预设；右键默认隐藏大小 | `engine/geometry.py`、设置「外观与交互」 |
| 设置 | 8 个侧边栏页、页内分页、搜索到行、实时预览、Enter 保存、非模态 | `ui/settings/`、`test_petcore_settings.py` |
| 菜单 | 默认顺序对齐主分支、菜单编排器、可隐藏/排序/别名/图标、勾选项对勾；彩蛋固定第一项、播放速率默认隐藏 | `menu_templates/petcore-default-v1.json`、`test_petcore_menu_layout.py` |
| 动画菜单预览 | 分类子菜单懒加载 + 代表帧缩略图（内存/磁盘缓存、2 线程、可关） | `media/thumbnails.py`、`test_petcore_thumbnails.py` |
| 直播捕获 | `stream_capture_mode` 切普通顶层 Window（含气泡），OBS/直播姬可枚举 | `platform/win.py`、`test_petcore_stream_mode.py` |
| 字号与气泡缩放 | 界面字号 10–18、气泡字号 50–300%、自语配图概率/大小可调 | `config.py`、`bubble.py`、`test_petcore_self_talk.py` |
| 彩蛋 | 菜单首行头像 + 标题 + 「请点击」、气泡内显示图片、独立窗口模式、目录/标题/开关可配 | `ui/easter_egg_entry.py`、`test_petcore_easter_egg.py` |
| 音效 | 点击音效包（默认/小黄鸭/自定义文件/文件夹），碰撞/碰边复用 | `media/click_sound.py` |
| 扩展体系 | mod manifest/授信 `.trusted`/安装卸载/CLI、设置页聚合、加载诊断 | `mods/`、`test_petcore_loader.py` |
| 安装与自启 | 单实例锁、开机自启、托盘、多实例数据根、安装向导选装扩展 | `platform/`、`packaging/petcore.iss` |
| 角色资源 | WebM/GIF/帧序列、`body_box`/`head_box`、`move_strides`/`move_curves`、`pack-character` CLI | `content/`、`tests/test_petcore_character_pack.py` |
| 联网底座 | 代理失败自动直连、五类失败分类、allowlist 架构守卫、keyring 密钥 | `petcore/net/`、`petcore/api/`、`test_petcore_net.py` |

## 2. 部分对齐（有入口但弱于主分支）

| 功能域 | 主分支 | petcore 现状 | 计划 | 验收 |
|---|---|---|---|---|
| 多屏 | overlay 覆盖虚拟桌面、逐屏世界、混合 DPI | 每宠一窗 + `multi_screen` 钳制；混合 DPI/跨屏连续性未实机 | P9 | 双屏不同缩放下拖拽/投掷/探头 |
| 角色导入 | 设置内 GUI 导入、预览、切换 | 只有 `petcore pack-character` CLI 与放置目录扫描 | P9 | 设置内导入 zip/目录并预览 |
| 动画目录 | 13 类 GraphType 全映射（Touch_Head/Say/Sleep/StartUP/Work/Raised_Dynamic…） | 只映射 idle/turn/move/click/drag/random/events；其余无播放入口 | P12 | 每类至少一个入口 + 播放测试 |
| 彩蛋 | 头像/提示行/标题/目录均可配 | 菜单行头像取目录首图（不可单独配）、提示固定「请点击」；图片大小/字号已可调 | P9 | 设置可配 avatar/hint |
| 点击音效 | duck 按压/释放配对音 | 单次随机音 | P9 | 按下/松开配对 |
| 音乐 | SMTC/播放器识别、歌词、唱跳动画、播放器启动 | **反超主分支**（2026-10-26，`media.music` 1.2.0）：三源识别（SMTC + WASAPI 音频会话 + 播放器窗口标题，主分支只有 SMTC + foobar 窗口标题）、歌词候选四维打分 + lrclib（主分支是"第一个歌手名命中"）、取词冷却重试、每宠 lease、诊断面板；实机只验证到「网易云在播 → 在播」与「SMTC 置空 → 窗口标题兜底」两条，pause/mute/多宠观感待用户环境 | P5 收口 | 有播放器时跑一遍唱跳与歌词 |
| 语音 | TTS provider、自言自语语音、口型、语音报时 | 已对齐（P4-b，口型除外）：core `SpeechPort` 四家后端 + 缓存/预检/钥匙串；2026-10-27 新增本地模型 provider、「语音」设置页、自言自语/点击台词语音与 champion 彩蛋；`voice.chime` 重写并去掉 Qt；口型主分支也没实现，不在本轮 | P5 收口 | 真机四后端各合成一条 |
| 设置域 | 桌宠/互动/菜单/语音/文件识别/AI/自动化/关于 | 9 页（2026-10-27 新增「语音」）；缺 AI/自动化/文件/更新/桌面组件/天气 | P9–P11 | 每域有页与搜索 |
| 右键菜单 | AI 对话/看屏幕/截图提问/音乐控制/天气/余额/Agent/主动识屏/待办/报时/抽签/节日/检查更新 | AI 三项（P3）+ 天气、文件解读（P4-a）已加；音乐/Agent/主动识屏/余额/检查更新仍缺 | P4-b/P4-c | 菜单项 + 设置落点 + coverage |
| 诊断/性能 | FPS/GPU/网络监控、卡顿采样 | 关于页诊断文本 + 日志 | P12 | 实时指标页 |
| 平台 | Windows/macOS 实机 | 仅 Windows 实机；macOS/Linux 未验证 | P12 | 两平台冒烟 |

## 3. 未做（主分支有、petcore 完全没有）

### 3.1 账户 / AI / 识屏 / Agent

| 功能 | 说明 | 计划 |
|---|---|---|
| DeepSeek 账户与余额 | 登录态、token、余额查询、余额档位动画 | P8 后续（`petcore/account/`） |
| AI 对话 | chat/persona/快捷对话/模型路由/聊天记录 | 部分（P7 收口）：Core 已移植主分支现代双栏对话窗（侧栏会话、Markdown 气泡、附件、置顶）、流式/停止/会话落盘；外观四项（背景/强调色/跟随/置顶）有设置入口并实时预览；**深度思考**（`thinking` + `reasoning_effort`、思维链折叠面板、按会话回显）与**联网搜索**（规划轮 + `web_search`/`fetch_url` 工具、资料注入、状态行、博查/Tavily/百度/DDG 四引擎、搜索 Key 只进钥匙串）已接。`ai.chat` 仍负责 prompt/路由，运行时代码无 Qt。快速对话气泡（点桌宠气泡）已接，与完整窗共用会话；历史会话批量管理未做 | P8 / P4 |
| 看看屏幕 | 整屏识屏、气泡答复、同步进聊天记录 | 已对齐（P3 收口）：气泡答复 + `ChatPort.record()` 静默入历史；默认不主动唤出窗口（`ai_look_screen_auto_open` 开关）；4 秒冷却与忙碌提示同主分支；抓屏改**虚拟桌面**（所有显示器并集，同主分支 `all_screens=True`），多屏/混合 DPI 不再只抓宠物所在屏 | 保持 |
| 截图提问 / 主动识屏 | 框选、截图问答浮窗、视觉模型、独立彩蛋 | 部分（P8）：四个动作全部接上——提取文字（`tool.ocr` 本地 rapidocr 优先，失败回退 `ai.chat` 视觉）、翻译（本地原文或视觉）、问问肥鱼、复制图像（core 剪贴板）；动作处理者按 priority 回退，core 不含 OCR/提示词。主动识屏与每日预算未做；HDR 屏仍未移植主分支 DXGI FP16 抓屏 | P4 |
| Agent 联动 | DSH 桥目录 + 统一 `agent-events/*.jsonl` + 自定义事件文件，六态 → 联动动作/过程气泡/完成通知/音效；**只读**（不装插件、不注入 hooks） | 已对齐（P4-c2）：`ai.agent` 可卸载扩展；写方安装与 Cursor/OpenCode 直读见 c2.1 |
| Worker 运行时 | `contracts/worker.py` 已有协议，执行层未实现 | P9 |

### 3.2 内容与提醒

| 功能 | 说明 | 计划 |
|---|---|---|
| 天气 / 城市 | 天气查询、菜单、气泡/灵动岛展示 | 已对齐（P4-a）：`tool.weather` 双源双出口、城市列表/地区码、失败真名两源；菜单「天气」+ 设置页「立即查看」；灵动岛展示留 P5 |
| 灵动岛 | 顶部胶囊、分段（CPU/内存/网络/时间）、告警色、点击展开卡片、拖动换位 | 已对齐：胶囊/卡片/停靠细条/动效/余额主信息/第三方段/GPU-FPS 探针/16 个 `island_*` 键；**岛 × 桌宠碰撞已回补（L103）**，新增可选的 C 盘剩余段；仍未做岛聊天 |
| 文件识别 | 拖文件解读、菜单入口、设置页 | 已对齐（P4-a）：`tool.file-reader` 订阅 `on_drop`，有界读取 + 心跳气泡 + 入会话（可关）；只读不删；菜单「解读刚才拖入的文件」 |
| 检查更新 / 在线更新 | 版本检查、下载、替换、回滚 | P11（需确认 Core 自更新是否仍是非目标） |
| 待办/节日/报时/抽签官方化 | 目前是 `examples/petcore-mods/` 可导入样板，不是默认功能 | P11 |
| 歌词 / Now Playing / 唱歌 | 播放器识别、歌词对齐、唱跳动画 | 已对齐（P4-b）：`media.music` 1.1.0；真实播放器下的逐行同步待用户环境验证 |
| TTS provider / 自语语音 / 口型 | 多后端、音色语速、口型同步 | 已对齐（P4-b，口型除外）：core `SpeechPort` 四家后端；2026-10-27 增加本地 HTTP provider 与「语音」页，自言自语/点击台词/彩蛋默认朗读；口型主分支亦未实现 |

### 3.3 交互与系统

| 功能 | 说明 | 计划 |
|---|---|---|
| 三个全局热键 | `open_chat`/`look_screen`/`screen_capture` | 已对齐（P3）：四条热键并存、失败可见 | 保持 |
| 预测预热 / 共享解码 / FPS 治理 | 性能层仍在 backlog | P12 |
| VPet PNG 后端 | 直接读 VPet 角色包 | P12 |
| macOS / Linux | 打包、输入、窗口行为 | P12 |

## 4. 默认安装口径差异（容易误判为“已对齐”）

以下 9 个扩展在 `examples/petcore-mods/`，安装向导勾选才会装到
`{app}\mods\<id>`；不勾选时菜单/设置里完全没有：

`fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music`、
`ai.chat`、`tool.ocr`、`tool.weather`、`tool.file-reader`。

因此**不能**把“示例 mod 存在”当成“默认功能已交付”。要宣称对齐，必须在
安装向导勾选后跑一遍真实安装验收。

## 5. 本轮（P8）已修

| 问题 | 处理 |
|---|---|
| 打包漏 `menu_templates/` 导致右键缺「播放动画/彩蛋图片」 | 构建补 `--add-data`；`load_default()` 内置兜底常量；`--check` 报告 `menu_template`；合并结果落盘 |
| 子宠右键退出把主宠一起退掉 | `PetWindow.quit_from_menu()`：子宠只 `close_slot` |
| 走路宠物推不动别人 / 反向卡死 | 轻碰不再切 `thrown`、不再取消移动计划，改为平移计划继续走 |
| 走路宠物把探头宠物挤下边缘 | 轻碰时探头视为无限质量，只推开走路宠物 |
| 气泡不跟随桌宠 | `moveEvent` 合并后 `bubble.reposition()`；缩放后重排 |
| 气泡不显示彩蛋图片 | `SpeechBubble.show_image()`；彩蛋默认气泡显示，可切独立窗口 |
| 扩展管理分组错误 / 不能折叠 | 可折叠组头；setup 5 个归「加装」；7 个 Core 特性并入 Core 设置页 |
| 「互动与物理」太长 | 8 个页内分页 |
| setup 不显示安装路径 | `DisableDirPage=no` + `UsePreviousAppDir=yes` + 旧路径预选 |

## 5.1 P8.1 已修

| 问题 | 处理 |
|---|---|
| 字号/图片大小不可调 | `ui_font_size`（设置/菜单/彩蛋行）、`bubble_text_scale`（气泡）、`easter_egg_image_scale`、`self_talk_image_scale` |
| 自言自语从不出图 | `self_talk_image_chance`（默认 30）+ 图片目录；失败回退文字 |
| 右键菜单「播放速率」占位 | 默认隐藏（菜单编辑器仍可打开） |
| 弹弓发射后松右键弹菜单 | 瞄准/发射后 0.35s 内抑制 `contextMenuEvent` |
| 点击台词要开一次设置才生效 | 监听补挂到新子宠 + 点击台词 `force=True` |
| 设置 X 关闭不保存 | X = 保存关闭；取消/Esc 仍恢复快照 |
| 彩蛋按钮位置不显眼 | 固定菜单第一项；设置项在分界线之上，分界线下方只有退出 |
| 直播软件抓不到桌宠 | `stream_capture_mode` 切普通顶层 Window（含气泡） |
| 播放动画没有预览 | 分类子菜单懒加载 + 代表帧缩略图（2 线程、可关） |

## 6. 维护规则

1. 发现新差距先加一行，再决定批次；不要在 roadmap 里另起一份清单。
2. 完成一项时把状态改为「已对齐」，补测试名或实机记录，并写进
   `PETCORE-LOG.md`（编号只看 `PETCORE-LOG-INDEX.md`）。
3. 「示例 mod」和「默认功能」必须分开写；默认安装口径见第 4 节。
4. 验收方式必须是可复现的命令或明确的实机步骤，不写“应该没问题”。

## 7. 2026-10-22 审查冻结补充

主分支完整功能矩阵见 [`PETCORE-FUNCTION-MATRIX-2026-10-22.md`](PETCORE-FUNCTION-MATRIX-2026-10-22.md)。
本轮新增的 P0 缺口：

- **每宠配置作用域**：设置窗口和内置 feature 仍读取 `host.config`，必须绑定当前宠物；
  `PetManager.SHARED_KEYS` 不能继续同步角色、缩放、播放速率、台词和音效等每宠键。
- **迁移与回滚**：v4 全局配置、slot 配置、会话、待办、API 规格和凭据引用尚无迁移协议。
- **WorkerHost**：只有协议和端口，没有真实子进程宿主、心跳、取消、超时、重启和代次拒收。
- **账户**：API 设置已存在，`petcore/account/`、余额缓存和额度服务未实现。
- **默认安装**：5 个 example mod 仍不能按“示例存在”计为默认功能；安装向导里它们是可选安装，默认不勾选。

有意差异：播放速率维持 `0.5–2.0x`；不支持 VPet LPS/PNG；桌宠身份上限 `MAX_PETS = 99`（含主宠，L126 起由 16 放宽）。

## 8. P1 基础作用域收口（2026-10-22）

| 项 | 状态 | 证据 |
|---|---|---|
| 设置窗口绑定当前宠物 | 已对齐 | `test_settings_dialog_binds_to_the_clicked_child_pet` |
| 每宠/全局配置读写分流 | 已对齐 | `tests/test_petcore_config_scope.py` |
| 点击台词/自语按宠物分流 | 已对齐 | `test_petcore_click_listeners.py`、`test_petcore_self_talk.py` |
| 旧 standalone 与 slot 基础迁移 | 已对齐 | `test_legacy_config_and_slot_profiles_migrate_with_backup` |
| 会话/待办/API 文件迁移 | 未做 | 等待对应业务消费者阶段 |

## 9. P2 Worker/离线接缝（2026-10-22）

| 项 | 状态 | 证据 |
|---|---|---|
| 统一 WorkerHost | 已对齐（基础） | `tests/test_petcore_worker.py` 真实子进程测试 |
| 角色目录 GUI 导入 | 已对齐（基础） | `tests/test_petcore_character_pack.py` |
| VPet Graph 类别寻址 | 已对齐（映射） | `build_animations()` 文件夹映射测试 |
| 5 个官方 feature 可选安装（默认不勾） | 已对齐（安装任务） | `test_official_feature_tasks_are_opt_in` |
| 旧 `pet` 媒体后端解耦 | 未做 | `petcore/media/backends.py` 仍为适配层 |
| AI/识屏/Agent worker 业务 | 对话与截图提问已接（P3）；Agent/OCR/翻译未做 | P4 |

## 10. P3 Core 账户基础（2026-10-22）

| 项 | 状态 | 证据 |
|---|---|---|
| Core 余额服务/缓存/错误分类 | 已对齐（基础） | `tests/test_petcore_account.py` |
| `HostPorts.api.fetch_balance()` | 已对齐（协议） | `tests/test_petcore_api.py`、account tests |
| 余额菜单、自动刷新、档位动画 | 未做 | 等异步 UI/worker 阶段 |
| AI/识屏/Agent 业务 | 对话与截图提问已接（P3）；其余未做 | P4 后续 |

| 每宠聊天会话存储 | 已对齐（基础） | `tests/test_petcore_sessions.py`；尚未接入聊天 UI |

## 11. P4-a 已对齐（2026-10-22）

| 项 | 状态 | 证据 |
|---|---|---|
| 通用取数端口 `HttpPort` | 已对齐 | `tests/test_petcore_http_port.py`（17 条：路由顺序/换路由条件/共享 deadline/体积上限/错误映射） |
| 天气双源双出口 | 已对齐 | `tests/test_petcore_weather_mod.py`；真实取数 0.24–0.26s（CN）、4.51s（open-meteo 两跳） |
| 天气失败真值表 | 已对齐 | 不存在城市 → 两源各点名原因，1.86s，不静默 |
| 文件解读 | 已对齐 | `tests/test_petcore_file_reader_mod.py`（扫描预算/有界读取/只读/失败可见） |
| 安装向导选装 | 已对齐 | `mod_weather` / `mod_filereader`，`Flags: unchecked`，`{app}\mods\<id>` + `.trusted` |
| `files.read` 能力声明 | 已对齐 | `contracts/capabilities.py`；能力仍是声明，不是沙箱 |

**仍缺（本段没做）**：天气逐城市子菜单与「明天」（主分支也没有）、歌词取词（P4-b）、
主动识屏与每日预算（P4-c）、Agent 联动（P4-c）。

## 12. P4-b2 已对齐（2026-10-23）

| 项 | 状态 | 证据 |
|---|---|---|
| 右键「扩展」按 mod 分组 | 已对齐 | `tests/test_petcore_menu.py::test_extensions_hub_groups_actions_per_mod` |
| mod 设置页 H2/H3 | 已对齐 | `tests/test_petcore_settings.py`；示例 mod 探针 7/7 |
| 随安装包的扩展可卸载 | 已对齐（主分支更强） | `tests/test_petcore_install.py`（清单删除/未知文件保留/trusted 删除/停用记录清理） |
| 气泡最大宽度与长文本 | 已对齐（换成分页） | `tests/test_petcore_bubble.py`（列宽 248→360、分页、页码、滚轮） |
| 气泡副标题「我在唱《…》」 | 已对齐 | `tests/test_petcore_music_mod.py::test_sung_bubble_carries_subtitle_when_port_supports_it` |
| 音乐只识别指定播放器 | 已对齐（主分支没有） | `tests/test_petcore_music_mod.py`（路径推导识别名 + 额外识别名 + 选择器过滤） |
| 音乐设置页切歌入口 | 已对齐 | `MusicPage`「切歌与播放控制」四个按钮，复用 `now_playing.transport()` |

**仍缺（P4-c）**：主动识屏、`ai.agent`、提醒域转正、菜单模板迁移；以及 VPet LPS 兼容、
进程内 Python 模块热卸载（卸载仍要求重启完成的模块不重载）。

## 13. P4-c1 已对齐（2026-10-23）

| 项 | 状态 | 证据 |
|---|---|---|
| 前台窗口元数据（hwnd/rect） | 已对齐 | `petcore/platform/foreground.py`（DWM 扩展边框、cloaked 过滤、`idle_seconds()`） |
| 窗口级抓图 | 已对齐（换实现） | `petcore/platform/screenshot.py::capture_window()`：整屏抓取 + 物理像素裁剪；主分支用 PIL 合成，未引新依赖 |
| 64 位 dHash 去重 | 已对齐 | `petcore/platform/image_hash.py`；`tests/test_petcore_screenshot.py`（条纹位序、Hamming、裁剪） |
| 主动识屏四道门 | 已对齐 | 白名单/停留 45 s/日上限 15/间隔 60 s/冷却 300 s/3 次失败熔断；`tests/test_petcore_proactive.py`（17 条） |
| 只冒气泡、不唤窗 | 已对齐 | `test_petcore_ai_mod.py::test_runtime_proactive_answer_records_without_opening_the_window` |
| 隐私双复核 | 已对齐 | `capture_foreground(expected_hwnd=)` 前后各查一次；不一致返回 `None` |
| 菜单勾选项 + 设置分节 | 已对齐 | 模板 `ai.chat:proactive` + 通用合并；设置页「主动识屏」12 个键 |
| 陪伴记忆（`proactive_memory`） | 未做 | 主分支最多 20 条的进程+活动记忆；需单独设计隐私口径，随 c2 评估 |
| 免费视觉 provider（`prefer_free_provider`） | 未做 | 跟随 core 的 API 服务选择，暂不区分免费视觉端点 |
| HDR 屏 DXGI 抓屏 | 未做 | 与主分支一致仍未移植；当前用 Qt 抓屏，HDR 可能偏色 |

**2026-10-24 校正**：`ai.agent` 只读 JSONL/六态/节流以及音乐、Agent 菜单节点已实现，
见 ROADMAP §27。该次质量审查时尚缺待办调度、农历节日、48 签和 Agent 写方/Claude hooks 注入；前三项随后由P4-c3 C–E实现，见下方更新。
自动化通过不等于真实外部服务验收；当前证据与整仓门禁限制见
[质量审查](PR-REPORT-PETCORE-QUALITY-REVIEW-2026-10-24.md)。


## 14. P4-c3 C–E提醒域（2026-10-24当前状态）

| 能力 | 实现状态 | 证据/仍有边界 |
|---|---|---|
| 完整待办 | 已实现 | 一次性/每日/提前/到期/预览、10分钟宽限、按宠物v2文件/去重、CRUD与暂停；C报告及联合门禁 |
| 节日与节气 | 已实现 | 公历/农历/24节气/西方浮动、同日合并、默认自动关闭、语音让位；无专属动画项 |
| 生日 | 已实现（增加） | 本地MM-DD/合成日期验证、02-29只闰年、受自动总开关而不受分类影响 |
| 每日一签 | 已实现 | 48签/稳定ID和权重、无放回轮次、全宠共享当天签、旧签当天保留、真实结束揭签、有界预热 |
| 默认安装/公开分发 | 未做 | 仍为可导入功能扩展，不默认授予执行；ZIP、远程清单、下载加固和候选包验收留后续 |
| 跨平台/真实服务 | 未验收 | Windows源码已测；真实外部TTS/其他Python/双屏/macOS/Linux/冻结包缺口见报告 |

总证据：[E联合验收](PR-REPORT-PETCORE-P4C3-E-2026-10-24.md)。主分支对齐依据当前仓库tracked纯逻辑，而非过时origin/main引用或未接通的动画开关；原历史报告不改写。

## 2026-10-25 重构收尾审查快照

本轮已实际收口的范围不是“全部主分支能力迁移”，而是两个可独立回滚的阻断批次：

| 功能域 | 当前状态 | 证据 |
|---|---|---|
| 市场任务生命周期 | **已对齐（本轮修复）**：应用级 `MarketJobRegistry` 持有索引/安装/回滚线程，页面销毁只取消并移交，退出做有界等待 | `petcore/ui/market_jobs.py`、`petcore/ui/settings/page_market.py`、`tests/test_petcore_market_hardening.py` |
| 市场下载续传与授权 | **已对齐（本轮修复）**：稳定 `.part`/元数据、Range 分支、远程 feature 解包预算、`file://` 根目录授权及回滚结构化状态 | `petcore/market/download.py`、`petcore/net/market_transport.py`、`petcore/market/install.py`、市场 hardening tests |
| WorkerHost | **基础已对齐（本轮收口）**：真实握手等待、代次拒收、取消/退出事件和异常路径有回归覆盖；业务 mod 接入仍按功能域验收 | `petcore/platform/worker.py`、`tests/test_petcore_worker.py` |
| 媒体 DPR | **基础已对齐（本轮收口）**：帧 pixmap 按物理尺寸缩放并写回 DPR，窗口帧入口传入屏幕 DPR；换屏/混合 DPI 实机仍缺 | `petcore/ui/renderer.py`、`tests/test_petcore_renderer.py` |
| 候选索引状态 | **已对齐（本轮修复）**：构建器默认 `candidate` 并保持 blocked，只有显式 `published` 才解除 | `scripts/build_petcore_extensions.py`、`tests/test_petcore_extension_build.py` |

仍为 **部分/未做** 的条目保持原状态，不因本轮测试通过而改成已对齐：设置内角色导入、多屏实机、GraphType 全映射、点击音效按下/释放配对、会话/待办/API 全量迁移、旧 `pet` 媒体适配层清理、公开 Release 与默认入口切换。

## 2026-10-25 重构收尾全量实施快照

本快照只描述本轮实际代码与证据，不把“计划项”写成“已完成”。相对基线 `e79e2a5f`，本轮代码提交为 `da107abe`、`c456ff56`、`42856889`；最终证据和交接由发布收口提交补齐。

| 领域 | 当前结论 | 证据与边界 |
|---|---|---|
| 配置/会话/待办迁移 | **原快照口径过宽，2026-10-26 校正**：本快照当时只有「迁移记录/备份/原子写/schema 校验」，会话与待办**并没有迁移代码**；真正的七域迁移见 `petcore/migration/`（L085） | `MigrationRecord`、备份优先、原子写入、诊断状态；`SessionStore` 与 reminder store 的 schema/失败保真；损坏文件不删除。默认目录 `python -m petcore --check` 本次 exit 0。 |
| WorkerHost | **已实现，自动化通过** | `petcore/platform/worker.py` 覆盖 READY、非法消息、取消、有界 shutdown、迟到回调边界；未新增虚构业务调用方。 |
| 角色设置内导入 | **已实现，自动化通过** | `petcore/content/importer.py` 复用包校验边界，拒绝路径穿越/重复覆盖/恶意 ZIP；设置页提供入口；未实现远程角色素材下载。 |
| GraphType 入口 | **已实现，自动化通过** | `menu_actions.py` 对已有 `GraphType` 提供懒加载分组；资源缺失时保持隐藏并回退现有动作，不宣称每个角色资源齐全。 |
| 点击音效 | **已实现，自动化通过** | `ClickSoundPair` 以 token 配对按下/释放，失焦、拖拽、关闭时取消，重复释放不重播。 |
| 彩蛋配置 | **已实现，自动化通过** | `easter_egg_avatar/title/hint` 的默认、预览、保存、取消、重置与回退；不新增第二套窗口。 |
| 账户/余额设置入口 | **已实现，自动化通过** | 设置页异步余额查询、disposed 迟到信号抑制和分类错误反馈；没有真实外部服务调用，不把失败显示成 0。 |
| 旧 `pet` 媒体边界 | **部分收口** | 架构守卫继续限制直接导入只在 `petcore/media/` 与 `petcore/content/legacy.py`；兼容层仍保留，未声称旧实现已删除。 |
| 多屏/混合 DPI 实机 | **待验证** | 代码保留已有 DPR/屏幕边界；本机只有 1 个屏幕、DPR 1.0，未做双屏拔出与混合 DPI 实机动作。 |
| PyInstaller/候选包/default entrypoint | **未完成/待环境** | 本 `.venv` 未安装 PyInstaller；不切换默认入口，不生成“可发布”结论。 |
| macOS/Linux、真实 TTS/CDN、公开 Release | **未验证/不在本轮** | 当前 Windows 环境无对应条件；未使用历史报告冒充本次通过，也未上传 Release。 |
## 2026-10-25 P5 parity snapshot

| 条目 | 状态 | 说明 |
|---|---|---|
| 灵动岛 P5-a2 | 已实现 / petcore 自动化通过 | 展开、消息、余额、第三方段、折叠停采样和避让已接入；**停靠细条 `island_edge_dock` / `island_dock_edge` + 悬停滑出 + 180ms 几何动画 + 悬停缩放 + 事件果冻已移植（L096）**；GPU/FPS 已接真探针（见下节） |
| 点击显示余额 | 已实现 / petcore 自动化通过 | 左键、右键、灵动岛共用 BalancePresenter，配置按 slot 隔离 |
| 菜单与设置几何 | 已实现 / petcore 自动化通过 | 图标列宽、导航 icon size、下拉框文字与箭头槽已明确度量 |
| 性能治理 | 已实现 / 有本机实测 | 1 Hz 采样约束、隐藏停采样、聚合计数；实测数据在 P5 报告与 scratch JSON |
| 检查更新/Core 自更新 | 已实现 / petcore 自动化通过 | HTTPS/ETag/断点/摘要/镜像与源码安全降级；冻结包安装器和公开 Release 待另一台 Windows 机器验收 |

不要把“自动化通过”改写为“Windows 安装器通过”或“远端发布完成”。

## 2026-10-26 P5 修复与 P6 两项快照

本快照记录本轮实际代码与实测，不把计划项写成已完成。实现细节与原始输出见
[`PR-REPORT-PETCORE-P5-FIX-P6-2026-10-26.md`](PR-REPORT-PETCORE-P5-FIX-P6-2026-10-26.md)。

| 条目 | 状态 | 说明与边界 |
|---|---|---|
| 余额投递 / 取消死锁 | 已修 + 有复现测试 | 投递改走 `QtScheduler`（两参 `singleShot` 从工作线程永不触发，已实测）；`cancel()` 锁外取消（PENDING future 同步回调会自锁，已实测挂死） |
| 菜单入口 | 已修 | `show_balance`/`check_update` 进入模板与内置兜底布局，此前注册了但永远不渲染 |
| 岛避让 | 已改口径 | 障碍物用桌宠 `body_rect()`；自动挪动不落盘、3 拍无遮挡回原位；用户拖动后作废 |
| 更新取消 / 安装门禁 | 已修 | `check()` 可取消、排队取消不被吞、`install()` 重算摘要并支持重启后安装、源码运行不给点亮安装按钮、状态消息不双写 |
| 段不可用语义 | 已对齐主分支 | `unsupported`/未接入探针不占位，原因只在卡片一行；此前会退化成 `--` + 告警红 |
| 旧数据迁移 | **七域已实现** | `petcore/migration/`：配置 / slot（含 slot0 并入全局）/ 活跃集合 / 会话 / 待办 / API 规格 / 凭据引用；标记幂等、dry-run、回滚、`petcore migrate`、`--check` 诊断 |
| 旧配置键级扩表 | **未做** | 旧 `config.json` 的 99 个未映射键（`chat_*`、`weather_*`、`voice_chime_*`、`festival_reminder_*`、`dialogue_*`、`music_*` 等）仍不在别名表；机制已就绪，补表即生效 |
| GPU 真探针 | **已实现 / 本机实测** | PDH `\GPU Engine(*)\Utilization Percentage` + DXGI 名字；本机 AMD Radeon 780M 真实读数，稳态单拍 2–3.5 ms，冷启 283.6 ms |
| FPS 真探针 | **已实现 / 本机未取得读数** | PresentMon 子进程 + 1 秒事件窗 + 进程级单会话；本机普通权限 `access denied`，样本报明确原因，**不宣称 FPS 通过**；需 Performance Log Users 且重新登录 |
| 探针接线 | 已实现 | 岛报「还需要哪些段」，宿主启停；隐藏/折叠/关段即停采，`--check` 有 `performance` 计数出口 |
| 岛动效余项 / 岛碰撞 / 岛聊天 | 动效与碰撞齐 / 岛聊天未做 | 停靠细条、几何动画、悬停缩放、事件果冻（L096）；状态点五态、声纳环、呼吸浮动、内容淡化 + 位图缓存、三层投影/sheen/bevel（L099）；**岛 × 桌宠碰撞（L103）**：同步硬墙 + 冲量弹床 + 拖岛拍飞 + 抛掷反射 + 撞岛 kick/果冻（tilt 倾斜通道刻意不做：44px 窗没有旋转裁切余量）；C 盘剩余段（L103）。**仍未做**：`waiting` 态真实来源（等审批流）、岛展开卡片里的 8px 状态点控件与自绘分隔线、灵动岛聊天、隐藏后由岛唤起对话 |
| 冻结包 PresentMon 落点 | 待验证 | 构建脚本有产物二次校验，本轮未重建安装包 |

不要把「petcore 自动化通过」或「本机 GPU 读到数」改写成「安装器通过」「FPS 已验收」。
