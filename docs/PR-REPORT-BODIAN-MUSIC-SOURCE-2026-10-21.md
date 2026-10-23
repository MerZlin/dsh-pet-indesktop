# 波点音乐歌词适配 — PR 报告（2026-10-21，2026-10-23 按 4.4.0 重做）

> **基线**：`v4.4.0`（`34d810b1`，功能模块目录化之后）
> **分支**：`feat/bodian-music-source`　**日期**：`2026-10-21` 起，`2026-10-23` 基于 4.4.0 重做
> **范围**：30 个文件（实现 15、测试 12、文档 3），+2933 / -41
> **关联**：[`PR-REPORT-ISSUE-228-BILI-LYRICS-2026-10-07.md`](PR-REPORT-ISSUE-228-BILI-LYRICS-2026-10-07.md)（foobar 补充源，本改动复用同一接入点）、[`PR-REPORT-SODA-LYRICS-ROUTE-2026-10-16.md`](PR-REPORT-SODA-LYRICS-ROUTE-2026-10-16.md)（汽水音乐歌词，同类"某播放器不上报"问题）

> **开发方式**：本改动由 **DeepSeek Harness（dsh）AI 代理辅助开发**——代码、测试与本文档
> 由 AI 代理起草，需求确定、方案取舍、实机验收与最终提交由仓库使用者完成。
> 所有验证数字均为**真实机器实测**（不是推断、不是 mock），命令与原始输出见 §五。

## 〇、这一版相对 2026-10-21 首版改了什么

首版 PR 的审查给出两个阻断（B1 / B2）与五条建议，本版逐条处理；同时因为 4.4.0 完成了
功能模块目录化，整个补丁**在新结构上重做了一遍**（不是 rebase——三个目标文件都被改名
搬家了，旧路径已删且无 shim）。

| 审查项 | 处理 | 落点 |
|---|---|---|
| **B1**（阻断）设置行从未挂出 | 已修。挂载链追加 `+ settings_music.bodian_lyrics_rows(self)`；`claim()` 列表追加 `"bodian_lyrics"`。**新增回归用例走真实 `ModernSettingsDialog` 构造**，断言 `settingRow_bodian_lyrics` 取到对象、且行真的包裹控件 | `pet/modern_settings_dialog.py`、`tests/test_bodian_playing.py` |
| **B2**（阻断）报告与分支内容不符 | 已修。§二/§六 全部按真实 `git diff --numstat` 重写；**删掉了首版声称改过、实际未改的两个文件**（`tests/test_lyric_sources.py` 首版确实没动、`tests/test_music_player_settings.py` 首版确实没动——本版前者真改了，后者仍**未改**，不再声称） | 本文档 |
| ① 门禁文档缺 `bodian_lyrics_enabled` 记录 | 已补变更记录 | `docs/SETTINGS-CHANGE-GATES.md` |
| ② 「不缓存 None」文档与实现不一致 | 已修**实现**（文档口径是对的）：`playing_state()` 只在拿到确定结果时才写缓存，`None` 每拍重读。新增 `test_unknown_playing_state_is_not_cached` / `test_known_playing_state_is_cached` 两条 | `pet/music/bodian.py` |
| ③ foobar 固定优先 vs 文案「只在没有其它播放器在播时才生效」 | 已修**文案**（不改顺序）：设置行如实写「只在没有其它播放器在播时才会被问到；若同时开着 foobar2000 且打开了它的歌词开关，foobar 会优先……关掉 foobar 或关掉它的开关即可让波点接手」。`_read_supplementary_playback` 的 docstring 也写明这个用户可见后果 | `pet/music/settings.py`、`pet/music/now_playing.py` |
| ④ `_log_files()` 先截断再排序 | 已修：先收全、按 mtime 排序、最后截前 8。新增 `test_newest_log_wins_even_when_there_are_many`（造 12 个日志，最新状态只写进 glob 枚举序第 11 位的那个） | `pet/music/bodian.py` |
| ⑤ 补真锁与中文路径两条用例 | 已补。真锁用 `BEGIN EXCLUSIVE` **真占锁**（不是 mock 异常）；中文路径把整个数据目录放进「用户 数据/AppData#1/Local」并额外含 `#` 与空格，直接打 `file:` URI 编码这条最脆的路 | `tests/test_bodian_playing.py` |
| #332 预告：新模块落 `pet/music/bodian.py` | 已按此落地，与 `pet/music/foobar.py` 对齐（相对导入 `from . import`） | `pet/music/bodian.py` |

### 后续两轮（2026-10-23，实机验证后追加）

| 事项 | 处理 | 落点 |
|---|---|---|
| **拼音歌词 bug**（用户实机报：「天使」显示 `Yěguāng zhàqiáo dōu`） | 已修。lrclib 对中文歌常有一条**全拼音（带声调）**版本，且它当初是**直接构造** `Lyrics` 返回、绕过了 `_as_lyrics` 的质量判据；而 lrclib 优先级高于网易云，QQ 音乐又常慢过 1.2s 的优先窗口，于是拼音版胜出。修法：新增 `_looks_romanized()` 判据（整首无汉字 + 半数以上带声调拉丁行），并让 lrclib 也走 `_as_lyrics` | `pet/music/lyric.py`、`tests/test_music_lyric.py` |
| **切歌 / 暂停**（用户问能否实现） | 已实现。波点的 UIA 按钮**不支持** `InvokePattern`、`DefaultAction` 为空，定向 `WM_APPCOMMAND` 与 `PostMessage` 鼠标消息它也不认——四条路都验过。唯一可行的是**系统媒体键**（实测波点不在前台也响应），因此新增 `bodian_control.py`，并加两道闸：波点进程在跑 + 最近一次采样来源确实是波点 | `pet/music/bodian_control.py`、`pet/music/lyric_controller.py`、`pet/context_menus/shared.py`、`tests/test_bodian_control.py` |
| **快进后歌词同步** | **做不到**，如实登记（见 §七）。波点把播放位置完全留在 libmpv 进程内：数据库只有 `hist_song` 一张表且无位置字段，日志 `playPos` 是**播放列表索引**、`position:` 只在切歌瞬间记录旧歌位置，UIA 唯一的 Slider 是音量（`Value='39%'`，不支持 RangeValue），无 mpv IPC 管道 | 见 §七 |
| **右键「打开波点音乐给主人放歌」** | 已加。注册表 `PLAYERS` 追加 `bodian`（键表是唯一来源，设置行/路径选择器/菜单入口全自动派生），菜单动作 `music_open_bodian` 同时登记注册表 + 默认模板 + `features.py` 模块清单。**语义与其余四个不同**：波点不注册 SMTC，发播放指令一定失败，所以启动流程对它跳过"发指令 + 轮询等 SMTC"（否则每次白等 10 秒），只负责把程序打开、由它自己续播 | `pet/music/players.py`、`pet/context_menus/{shared,registry}.py`、`pet/features.py`、`pet/menu_templates/modern-default-v1.json`、`README.md` |

## 一、核心特性

波点音乐（`bodian_pc.exe`，Flutter + media_kit/libmpv）**不注册 Windows 系统媒体会话**，
也不在窗口标题里写歌名，因此歌词与唱歌链路完全看不见它。本改动为它新增一条
**补充来源**：读它自己写在本机的播放历史库（曲目）与日志（播放状态）。

这解决了「用波点音乐时桌宠没有歌词」——用户看到的变化是气泡里出现
「我在唱《歌名》」和逐行歌词。**两条现成通道为什么都不通**，本机实测：

```
# 波点正在播放时，系统媒体会话数
SMTC session count -> 0

# 它的程序与全部插件 DLL 里没有任何系统媒体控制 API 引用
（对 app.so 与 *.dll 做 SystemMediaTransportControls 检索：0 命中）

# 主窗口标题是常量，不含歌名
hwnd=0x910B6 class=BODIAN_FLUTTER_WIN32_WINDOW title=[波点音乐]
```

| # | 能力 | 说明 |
|---|---|---|
| 1 | 读取当前曲目 | 歌名 / 歌手 / 专辑 / 总时长，来自 `songDB.db` 的 `hist_song` 表最新一行 |
| 2 | 判定播放 / 暂停 | 读 `bdlog/log_*.log` 末尾最后一条 `mpv playing event=` |
| 3 | 接入歌词链路 | 复用既有的 SMTC 采样与三源取词，不改取词逻辑 |
| 4 | 设置开关 | 「设置 → 桌宠 → 显示与音乐 → 音乐关联 → 波点音乐歌词」，默认关，仅 Windows |
| 5 | 不抢别人的歌 | 只在系统媒体会话**没有在播会话**时才被询问 |

**红线 / 不变量**：

- **只读**：数据库用 `mode=ro` 只读 URI 打开；日志只读末尾 64 KB。从不写、不建表、不抢写锁。
- **不猜进度**：`Playback.position` 恒为 `None`。库里的 `time` 是"开始播放的时刻"，
  当成真值会骗过 `LyricTracker`，把「歌词对齐」整组置灰——那样用户连手动校正都失去。
- **不影响其他播放器**：补充源只在 SMTC 没有在播会话时被询问，且它的 `app_id`
  不写入 SMTC 跟踪位（`is_supplementary_source` 是唯一入口）。
- **读不到就当没有**：路径不存在 / 表不存在 / JSON 坏 / 进程没跑 → `None`，绝不猜。

## 二、修改文件说明

以下数字取自 `git diff --numstat`（基线 `34d810b1`，2026-10-23 实测）。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/music/bodian.py`（**新增**） | +481 / −0 | 波点曲目来源本体：路径解析（`data_dir`/`database_path`/`log_dir`）、只读查询 `_read_newest_row`、**SQLite `file:` URI 百分号编码** `_sqlite_uri`（中文用户名与 `#`/`%`/空格）、不可信输入解析 `_parse_row`/`_text`/`_seconds`、播放状态 `_parse_playing`/`playing_state`（**只缓存确定结果**）、**日志先排序再截断** `_log_files`、进程判定 `process_running`、缓存与降级 `_current_row`/`read_track`。零 Qt；相对导入 `from . import`（与 `foobar.py` 对齐） |
| `pet/music/now_playing.py` | +100 / −11 | 新增 `is_bodian_source`、`is_supplementary_source`（补充源唯一判断，取代散落的 `is_foobar_source` 判定）、`_read_bodian_playback`、`_read_supplementary_playback`（统一分发两路补充源，**foobar 在前**）；`_read_async`/`get_now_playing` 加 `allow_bodian`（默认 `False`，保持旧行为）；"没有 WinRT"分支改为也走统一分发（否则补上波点后这条分支会把新源漏掉） |
| `pet/music/lyric_controller.py` | +16 / −4 | 新增 `_bodian_lyrics_enabled()`（每拍读 cfg，与 foobar/browser 同规，改了开关不必重启）；采样线程把 `allow_bodian` 带给 `now_playing`；跟踪位判断由 `is_foobar_source` 改为 `is_supplementary_source`（否则下一个补充源会漏一处） |
| `pet/music/settings.py` | +42 / −1 | `bodian_lyrics_supported()` / `bodian_lyrics_rows()`：一行设置项，文案如实写清「为什么单独一条」「读的是本地记录、只读」「进度是推算的、可用歌词对齐校正」「**foobar 优先**，foobar 开着会挡住波点」；`_SOURCE_SWITCHES` 登记保存 |
| `pet/config.py` | +11 / −0 | 新键 `bodian_lyrics_enabled`（默认 `False`）：默认值 dict、`reload()` 白名单、`set()` 归一化触发集、`_normalize_pet_settings`（`_bool_or_default`）四处登记 |
| `pet/modern_settings_dialog.py` | +2 / −2 | **B1 修复**：`bodian_lyrics_rows(self)` 接进「音乐关联」挂载链（同行追加，不破行数预算）；`claim(...)` 列表追加 `"bodian_lyrics"`（防止模块未激活时掉进「待分类」） |
| `pet/settings_pet_controls.py` | +8 / −0 | `bodian_lyrics_check` 控件，仅 Windows 创建（与 `foobar_lyrics_check`/`music_browser_media_check` 同规） |
| `pet/music/settings_default_marks.py` | +1 / −0 | `"bodian_lyrics": "bodian_lyrics_enabled"`，让该行获得「已修改」标记与单项重置 |
| `pet/music/bodian_control.py`（**新增**） | +185 / −0 | 切歌/暂停通道：`SendInput` 投递系统媒体键（`VK_MEDIA_NEXTTRACK` / `PREVIOUSTRACK` / `PLAYPAUSE`）。含 x64 `INPUT` 结构体正确定义（**40 字节**；少声明成员会让 `SendInput` 返回 0 并报 `ERROR_INVALID_PARAMETER`，表现成"播放器不响应"），以及两道防误伤闸（进程在跑 + 来源确为波点）。零 Qt、零第三方依赖 |
| `pet/music/lyric.py` | +43 / −1 | 新增 `_looks_romanized()`：整首无汉字、且半数以上行含带声调拉丁字母 → 判为"拼音歌词"，按该源未命中处理。同时修 `_fetch_from_lrclib` 绕过 `_as_lyrics` 的缺口（三个源现在都过同一个质量判据） |
| `pet/context_menus/shared.py` | +36 / −3 | 「暂停 / 播放」改走 `_toggle_play_pause(pet)`（控制器路径，能判断要不要发媒体键），不再直调 `now_playing.toggle_play_pause`；新增 `add_music_open_bodian`；`_launch_player_and_play` 对不注册 SMTC 的播放器跳过"发播放指令 + 轮询等会话" |
| `pet/music/players.py` | +19 / −0 | `PLAYERS` 追加 `bodian`（`bodian_pc.exe`，目录名 `bodian`/`Bodian`/`波点音乐`）；新增 `_NO_SMTC_PLAYERS` 与 `registers_smtc()`，把"这个播放器会不会出现在系统会话里"变成一个可测的判据而不是散落的 `if` |
| `pet/context_menus/registry.py` | +4 / −1 | `music_open_bodian` 的动作、规范文案「打开波点音乐」、图标 `play`、`MenuActionSpec` 四处登记 |
| `pet/features.py` | +1 / −0 | `music` 模块的 `menu_action_ids` 追加 `music_open_bodian`（模块停用时入口一并消失） |
| `pet/menu_templates/modern-default-v1.json` | +2 / −1 | 默认树在 `music_open_soda` 之后追加 `music_open_bodian`——**只登记注册表不加模板，老用户布局里永远看不到这个入口** |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_bodian_playing.py`（**新增**） | +862 / −0 | 56 例：数据来源形状（8）、不可信输入（14，含参数化）、**最脆的两条路径**（真 `BEGIN EXCLUSIVE` 锁 2 例 + 中文/含 `#` 路径 2 例）、降级与边界（9，含日志先排序再截断 2 例、缓存语义 2 例）、接线（7）、配置与设置行（4，含 **B1 回归：真实 `ModernSettingsDialog` 构造**） |
| `tests/test_lyric_sources.py` | +71 / −0 | **B2 的核心缺口**：`allow_bodian` 从配置到采样线程的接线（走真实 `LyricController` 采样线程）、缺键/脏值按关、`cfg.get` 抛异常时赋值不崩、波点源不占 SMTC 跟踪位 |
| `tests/test_config_schema.py` | +4 / −0 | `RELOAD_WHITELIST_SNAPSHOT` 加键 + 白名单计数注释（162 → 163） |
| `tests/test_bodian_control.py`（**新增**） | +227 / −0 | 15 例：结构体布局（40 字节）、动作→虚拟键码映射、非 Windows、进程闸、`next`/`previous`/`pause` 分发、部分注入按失败且**不重试**、异常吞掉，以及控制器接线四条（SMTC 成功不重发 / 波点失败回退 / 网易云失败**不许**发 / 无来源**不许**发） |
| `tests/test_music_lyric.py` | +143 / −0 | 7 例拼音相关：判据自检四条（拼音 / 汉字 / 纯英文 / 汉字为主夹杂拼音）+ 三条回归（lrclib 拼音按未命中、lrclib 汉字照常通过、端到端优先选汉字源） |
| `tests/test_menu_layout.py` | +4 / −1 | 三处默认树/注册表/预热快照补 `music_open_bodian`；`_PLAYER_KEYS` 加 `bodian` |
| `tests/test_sprite_menu_parity.py` | +5 / −2 | 覆盖层菜单的启动入口清单改为**从注册表取键**（原来写死 4 个，加播放器就得手改） |
| `tests/test_music_player_cache.py` | +4 / −2 | `_EXPECTED_PLAYERS` 加 `bodian`（`bodian_pc.exe` + 目录名） |
| `tests/test_music_player_settings.py` | +6 / −3 | 五个播放器的控件属性名 / 行键 / 搜索词契约表 |
| `tests/test_music_player_detect_refresh.py` | +2 / −1 | 检测刷新的控件属性名契约表 |
| `tests/test_architecture.py` | +4 / −0 | `music/bodian.py` 进「纯逻辑层零 Qt」守卫名单 |

### 文档

| 文件 | 增删 | 内容 |
|---|---|---|
| `docs/SETTINGS-CHANGE-GATES.md` | +46 / −0 | `bodian_lyrics_enabled` 的变更记录（准入/归属/默认值/迁移/平台/capability/准出五项） |
| `docs/INDEX.md` | +1 / −0 | 本报告登记 |
| `docs/PR-REPORT-BODIAN-MUSIC-SOURCE-2026-10-21.md`（**新增**，本文件） | +532 / −0 | 交付证据（修改文件说明 / 性能分析 / 实机运行记录） |
| `README.md` | +17 / −4 | 「不向系统上报播放信息的播放器（Windows）」小节（foobar / 波点两行表 + 两路优先关系 + 切歌与快进的真实局限）；播放器入口清单补波点，「四个播放器」改「每个播放器」 |

### **未改动**

- `pet/music/foobar.py`、`pet/music/title.py`：与本次无关，一字未动。
- `pet/music/detect.py`、`pet/music/players.py` 的**搜索算法**（`_search_filesystem` /
  `_versioned_install` / `_shallow_scan`）：一字未动。`players.py` 只在 `PLAYERS` 字典里
  追加了一个 `bodian` 条目、新增 `_NO_SMTC_PLAYERS` / `registers_smtc()`。
- 音量控制：见 §七，**不做**（波点的音量在它自己 UI 里，UIA 那个 Slider 不支持
  `RangeValue`，写不进去）。

### 关于首版「虚报文件」的更正记录（审查 B2）

首版报告声称改了 `tests/test_lyric_sources.py` 与 `tests/test_music_player_settings.py`，
实际两个都没动——这是审查 B2 点出的问题，本版如实更正：

| 文件 | 首版 | 本版（2026-10-23） |
|---|---|---|
| `tests/test_lyric_sources.py` | 声称改了，**实际没动** | **真的改了**（+71）：补上 `allow_bodian` 从配置到采样线程的接线测试——这正是 B2 说的「至今无测试」那条 |
| `tests/test_music_player_settings.py` | 声称改了，**实际没动** | **真的改了**（+6/−3），但改的不是首版声称的那三组用例，而是五个播放器的契约表（加 `bodian` 后同步）。B1 的回归覆盖在 `tests/test_bodian_playing.py` 里，不在这个文件 |

## 三、实现要点

### 数据从哪来（本机实测，2026-10-21）

| 事实 | 来源 |
|---|---|
| 曲目：每开始播一首插一行，`json` 列 35 个字段 | `%LOCALAPPDATA%\cn.wenyu.bodian\bodian_pc\database\songDB.db` 的 `hist_song` |
| 写库时机：切歌后约 0.26 s 落库 | 发 `VK_MEDIA_NEXT_TRACK` 后轮询 `select max(ord)` |
| 播放/暂停：`mpv playing event=true/false` | `…\bdlog\log_0.log`，切歌/暂停即时落盘 |
| 库的 journal 模式 | `pragma journal_mode` → `delete`（写事务期间读会短暂拿 `database is locked`） |

### 四处刻意的口径（其中两处是本轮审查改出来的）

1. **只读**。`sqlite3.connect("file:…?mode=ro", uri=True, timeout=0.2)`；日志用 `seek` 读尾部。
   实机验证：读一次后库目录文件集合不变（`test_database_is_opened_read_only`）。
2. **不猜进度**。`position=None` 是**刻意**的：`LyricTracker.load` 会把非 `None` 当"播放器
   上报的真值"，从而关掉手动对齐（`align_available()` 返回 False）。波点没有进度，必须如实说"没有"。
3. **读不到就当没有**。`_parse_row` 对 `json` 按不可信输入处理；`_text` **不做 `str()` 兜底**
   ——把数字 `42` 当歌名会拿它去搜到别人的歌（这条是 TDD 时抓出来的真实缺陷）。
4. **"不知道"不缓存**（审查建议②）。`playing_state()` 只在 `_parse_playing` 返回确定布尔值
   时写缓存；`None` 每拍重读。理由很具体：波点刚启动时日志还没写出第一行，那一拍读到
   `None`，若缓存住就整整一秒判定不了播放状态，而歌词本来可以一拍内出来。

### 缓存与降级

| 缓存 | 键 | TTL | 目的 |
|---|---|---|---|
| 行级解析 | `(ord, id, time)` | 无（换行即失效） | 同一首歌每秒不重复解析 JSON |
| 播放状态 | — | 1 s，**仅确定结果** | 合并同一拍内的重复询问；`None` 不缓存 |
| 进程扫描 | — | 2 s | 进程表遍历 2.9 ms，1 s 一拍不能每拍扫 |

**降级**：库短暂不可读（写事务撞锁）时，只有在 **`process_running()` 为真** 的前提下才沿用
上次曲目，上限 10 s。进程退出后立即返回 `None`——否则波点关了歌词会永远停在上一首。

### 为什么新增"统一分发"而不是再加一个 `if`

`_read_supplementary_playback(allow_foobar, fmt, allow_bodian)` 是补充源的唯一分发点，
`is_supplementary_source` 是"这条采样要不要占 SMTC 跟踪位"的唯一判断。加第二个补充源时
若各自散落一个 `if`，漏一处就表现为"那个播放器的歌词时好时坏"。

### 关于 `file:` URI 的编码（审查建议⑤带出来的实现修正）

首版直接拼 `"file:%s?mode=ro" % path.as_posix()`。这在中文 Windows 上**会静默失败**：
`%LOCALAPPDATA%` 通常是 `C:\Users\<中文名>\AppData\Local`，而 `file:` URI 对非 ASCII 与
保留字符（`?` `#` `%` `&`）都有语义——`#` 之后会被当成 fragment 截断，SQLite 于是打开一个
不存在的路径，表现为"读不到歌"且**没有任何报错**（异常被吞成 debug 日志）。

本版改为 `quote(posix, safe="/:")`，并先把路径里已有的 `%` 编成 `%25`（否则会被当成转义起点）。
`test_sqlite_uri_encodes_reserved_characters` 与 `test_non_ascii_install_path_is_readable`
两条用例守在这一点上——后者把整个数据目录放进「用户 数据/AppData#1/Local」。

## 四、性能分析

**方法（可复现）**：`python bench_bodian.py`（基准脚本随 PR 附在 `.scratch/bodian-bench/`）
**环境**：Windows 11 Pro / CPython 3.11.15 / PySide6 6.12.0 / psutil 7.2.2 / 本机 364 个进程
**样本量**：见下表 n

> **口径说明（如实登记）**：下表数字取自 **2026-10-21 首版基线**（`c3862d3`）。4.4.0 的
> 目录化是纯搬移（`#332` 自述"零运行时行为变化"，AST 47/51 逐字节相同），本版新增的
> URI 编码是一次 `quote` 调用、日志排序从"截断后排序"改成"排序后截断"（同为对 ≤8 个
> `stat` 结果排序），两者都不改变数量级。**未在 4.4.0 上重跑基准**——这条如实写在这里，
> 不拿旧基线数字冒充新基线的实测。

| 指标 | 实测（median / mean / max） | 归属 |
|---|---|---|
| 冷路径 `read_track()`（读库+解析+读日志） | 3560.90 / 3740.80 / 5815.10 µs，n=100 | 新增；**仅在开关开启且无 SMTC 在播会话时**，1 Hz |
| 热路径 `read_track()`（缓存命中） | 1766.60 / 1843.65 / 2735.30 µs，n=200 | 新增；同上 |
| `_read_newest_row()`（仅 SQLite 只读查询） | 1969.20 / 2085.71 / 3585.60 µs，n=200 | 新增热路径主体 |
| `playing_state()`（清缓存后读 64 KB 日志尾巴） | 2048.55 / 2045.41 / 2924.30 µs，n=100 | 新增 |
| `process_running()`（清缓存，扫 364 进程） | 2904.80 / 2983.34 / 3948.50 µs，n=30 | 新增；2 s TTL 后 ≈0 |
| **开关关闭时** `_read_supplementary_playback(全关)` | **0.30 / 0.36 / 16.70 µs**，n=500 | 默认路径 |
| 既有 `get_now_playing()` 基线 | 3450 µs，n=100 | 既有 |
| 开启波点后 `get_now_playing()` | 5683 µs，n=100 | **增量 +2233 µs** |

**结论**（逐条回答）：

1. **稳态开销有没有变化、变了多少**：开关**关闭（默认）**时增量 0.30 µs/拍，即
   `now_playing` 多一次布尔判断——相对既有 3450 µs 是 0.01%，可视为零。开关**开启**时
   增量 **+2233 µs/拍（1 Hz）**，即约 **0.22%** 的单核占用，且**不在 GUI 线程**
   （歌词采样线程 `music-lyric-sample`）。
2. **新增路径的绝对成本与触发频率**：单次 1.8~3.6 ms，频率 1 Hz，且只在
   「开关开 + 无 SMTC 在播会话」时触发；有 SMTC 在播会话时根本不调用。
3. **有没有引入新的系统调用 / 网络 / 磁盘 / 线程**：
   - 系统调用：每次采样 1 次 `stat` + 1 次 SQLite `open/exec/close` + 1 次 `seek+read`（64 KB）
   - 网络：**无**（模块不 import 任何网络库）
   - 磁盘：只读，无写入（用例 `test_database_is_opened_read_only` 守卫）
   - 线程：**无新增线程**（跑在既有的 `music-lyric-sample` 线程上）
4. **内存与缓存有没有增长**：三个缓存各存至多一项，大小恒定，不随播放次数增长；
   日志尾巴读进内存后即释放，峰值 64 KB。

**已知的可优化点（本次不做）**：热路径 1.77 ms 里有约 1.5 ms 是**每拍重开 SQLite 连接**
的开销。改成常驻连接可降到 µs 级，但要处理「波点写库期间长连接持有旧快照」与连接泄漏，
收益是 0.17% 单核——不值得在这个 PR 里做。

## 五、实机运行记录

本机真实环境（Windows 11 Pro，波点音乐 1.1.7 安装于 `D:\Program Files (x86)\bodian`）。

**验证环境说明（如实登记）**：本轮验证跑在**源码版桌宠**上（`python -m pet`，基线
`v4.4.0` + 本 PR 改动），依赖为 CPython 3.11.15 + 独立依赖目录。这是仓库内既有的开发
运行方式（`run.bat` 就是 `pythonw -m pet`），与打包版共用同一份产品代码。**未使用打包版
EXE 复验**——打包流程在 CI 上，本地只验证产品代码本身。

> **2026-10-23 重做后的实机复验记录见本节末尾**（§5.5），首版记录保留在 §5.1–§5.4
> 以便对照。

### 5.1 根因/前提的现场复现

**波点播放中，系统媒体会话数为 0**（这是整件事的前提）：

```
$ python smtc_probe.py   # 只读 WinRT SMTC 会话
session count: 0
```

同一次运行里，桌宠自己的切歌/暂停通道对波点全部失败（证明它没有可操作对象）：

```
skip_track(next) -> False
skip_track(previous) -> False
toggle_play_pause() -> False
SMTC session count -> 0
```

**它的数据在哪、什么时候写**（发媒体键 → 轮询库）：

```
--- before @ 10:28:05
 hist rows (newest first): [(4, 64434818, '2026-10-21 10:14:06.881137'), …]
sending VK_MEDIA_NEXT_TRACK ...
--- after +1.0s cumulative @ 10:28:06
 hist rows (newest first): [(5, 45808256, '2026-10-21 10:28:05.963828'), …]
```

**播放状态在日志里**：

```
2026-10-21 10:28:57.095 [I] mpv playing event=true
2026-10-21 10:28:59.209 [I] mpv playing event=false ← 按暂停
2026-10-21 10:29:01.264 [I] mpv playing event=true ← 恢复
```

### 5.2 用户可见行为的确认

源码版桌宠真实启动 + 开启开关 + 波点播放，**端到端日志**：

```
12:29:25,135 INFO alert enqueue alertType=music_lyric priority=1 alertId=music-lyric-estimated
12:29:25,160 INFO 歌词取词完成: 蔡依林 - 如果我没有伤口 -> 63行, 耗时 0.00s
12:29:25,166 INFO 歌词按检测时刻对齐: 已过去 0.03s（其中暂停 0.00s）
```

**界面确认**（截图）：桌宠气泡显示「**我在唱《如果我没有伤口》/ 填：林迈可**」。

启动日志同时证明素材与解码链路正常（不是"进程起来但画面空白"）：

```
12:29:08,640 INFO 素材加载完成：shenshen 106 段动画
12:29:08,854 INFO ffmpeg 孤儿防护已就绪：Job Object（KILL_ON_JOB_CLOSE）
12:29:09,535 INFO 进入事件循环
```

**曲目读取的独立探针**（不经过桌宠 UI，直接调 `bodian.read_track()`）：

```
--- read #1 (5.16 ms)
 title = 'Insight XXXIII'
 artist = 'Julien Marchal'
 album = 'INSIGHT III'
 duration= 165.0
 playing = False
```

另一次不同曲目的读数（证明不是单曲缓存）：

```
 title = 'There’s a Field (That’s Only Yours)'
 artist = 'Daniel Caesar&Rex Orange County'
 album = "Rearrange My World / There’s a Field (That's Only Yours)"
 duration= 124.0
```

**播放状态切换的实机确认**（按媒体键前后）：

```
before: playing_state = False
sent VK_MEDIA_PLAY_PAUSE
after : playing_state = True track.playing = True
```

### 5.3 边界与失败路径

- **开关关闭（默认）时完全不进这个模块**：`allow_bodian=False` → `get_now_playing()` 返回
  `None`（波点在播也不出歌词）。
- **进程退出后不沿用旧曲目**：用例 `test_stale_row_is_dropped_when_process_gone` 守卫；
  真机上停掉波点后 `read_track()` 返回 `None`。
- **库被外部删除**：`test_missing_database_yields_no_track` → `None`，不抛。
- **脏数据**：`{"name": 42, "artist": null}` → `None`（不把 `"42"` 当歌名）。这条用例
  在实现初版时**是红的**（`_text` 会 `str()` 兜底），改的是实现而不是测试。
- **只读确认**：读一次前后库目录文件集合不变（`test_database_is_opened_read_only`）。

### 5.4 无法自动验证的能力 + 为什么

- **真实歌词气泡的视觉确认**：已用截图完成，但**未做像素级断言**——气泡渲染走 Qt 合成，
  自动化截图对比在 CI 上不稳定，故只作人工确认记录，不加断言。
- **波点升级后数据结构是否变化**：无法自动验证。缓解是"读不到就当没有"，且模块对
  未知/超长 JSON 一律按损坏处理，降级为"没有歌词"而不是错误歌词。
- **多播放器同播时的选择**：本轮未做真机对照（需同时开两个播放器并各放一首）。
  代码层面已由 `_read_async` 的 `snapshot.playing` 判断保证补充源不抢在播会话，
  且 foobar 固定在前（文案已如实写明），但**这不等于**验证过"网易云在播 + 波点也开着"
  与"foobar 开着不播 + 波点正在播"这两个现场行为。

### 5.5 2026-10-23 在 v4.4.0 上的复验

**环境**：同一台机器，基线换成 `v4.4.0`（`34d810b1`），代码路径变成 `pet/music/bodian.py`。

**(a) 真实波点数据可读**（本机 `%LOCALAPPDATA%` 下是 10-21 那次的真实库，94 KB）：

```
data_dir      = C:\Users\86166\AppData\Local\cn.wenyu.bodian\bodian_pc
database_path = ...\database\songDB.db
log_dir       = ...\bdlog
process_running = False

--- _read_newest_row (直接查库，绕过进程闸) ---
  ord = 23  id = 607234894  time = 2026-10-21 16:08:04.357228
  json len = 2249
  parsed = {'title': '赐我茉莉', 'artist': 'Bo Peep', 'album': '赐我茉莉',
            'duration': 199.0, 'key': (23, 607234894, '2026-10-21 16:08:04.357228')}
  read latency: 1652.5 us
```

这条证明**中文路径与真实库结构都没问题**：`C:\Users\86166\...`（用户名是英数，
但整条路径经 `file:` URI 编码后能正常打开），单次只读查询 1.65 ms，与首版基准的
1.97 ms 同量级。

**(b) 复验时抓到并修掉一个真实 bug：进程不在仍然返回上一首。**

上一条输出里 `process_running = False`（波点进程已退出两天），而首版实现在这种
情况下**照样返回** `BodianTrack(title='赐我茉莉', ...)`——因为进程判定只挂在
`_current_row` 的"库读不到"降级分支上，库读得到时压根不看进程。后果正是我自己在
模块 docstring 里写明要避免的那条：**波点退出后歌词永远停在上一首**。

修法：在读库**之前**判进程，与 `pet/music/foobar.py` 的 `if not pids: return None`
同一先例；进程判定函数本身抛异常时不拦截（fail-open，避免一次扫描失败吞掉歌词）。

修后同一条命令：

```
process_running = False
raw row still readable = True      ← 库和最后一行都还在
read_track() = None                ← 修复生效
```

**这个 bug 是被真机跑出来的，不是测试跑出来的**——修之前 54 条用例全绿，因为没有
一条覆盖"进程不在但库里有数据"。已补两条回归用例
（`test_process_gone_yields_no_track_even_when_row_reads_fine` 与
`test_process_check_failure_does_not_block_reading`），并做针对性消融：撤掉这道闸
→ **1 failed**，逐字节还原。

**(c) 设置页可达性（B1）**：真实构造 `ModernSettingsDialog` 后取到

```
settingRow_bodian_lyrics = <SettingRow name="settingRow_bodian_lyrics">
bodian_lyrics_check      = <ToggleSwitch>   38 x 22
  row label = 波点音乐歌词
```

不再是首版审查实测到的 `None` / (0,0) 幽灵控件。该断言已固化成用例
`test_row_is_actually_mounted_in_the_settings_dialog`（消融可令其变红）。

**(d) 端到端歌词链路：已在 4.4.0 上实机跑通**（2026-10-23，用户亲手跑）

从本机 `pet-14348.log`（用户从 `_scratch/v440full` 启动的实例，14:10:17 – 14:23:17）
取到的真实记录——**波点正在播放、歌词链路完整工作**：

```
2026-10-23 14:12:00,550 INFO alert enqueue alertType=music_lyric priority=1 alertId=music-lyric-estimated
2026-10-23 14:12:01,805 INFO 歌词取词完成: Bcalm&Lonely Llama - Blue Pacific -> 0行（纯音乐）, 耗时 1.23s
2026-10-23 14:12:12,901 INFO 歌词取词完成: Faime - Rain -> 31行, 耗时 1.36s
2026-10-23 14:12:12,902 INFO 歌词按检测时刻对齐: 已过去 1.36s（其中暂停 0.00s）
2026-10-23 14:12:18,874 INFO 歌词取词完成: 张惠妹 - 听海 -> 52行, 耗时 1.34s
2026-10-23 14:13:11,845 INFO 歌词取词完成: 张震岳 - 在凌晨 -> 32行, 耗时 1.30s
```

同一份日志里还有**用户亲手触发的控制动作**，走的都是本 PR 的产品代码：

```
2026-10-23 14:13:08,447 INFO 已向波点发送媒体键：next        ← 右键「切歌」
2026-10-23 14:17:23,146 INFO 已向波点发送媒体键：play_pause  ← 右键「暂停 / 播放」
```

这一条同时补上了首版 §5.2 的缺口：**4.4.0 上真的看到歌词了**，不只是"数据读得到"。
（`Blue Pacific -> 0行（纯音乐）` 是正确判定：那首确实无词，源码按纯音乐处理。）

**(e) 仍未做的实机验证（如实登记）**：

- 性能数字取自 10-21 首版基线（见 §四口径说明），**未在 4.4.0 上重跑基准**。
- 多播放器同播未做真机对照（同时开两个播放器各放一首）。

### 5.6 拼音歌词 bug（用户实机报，2026-10-23）

用户截图：波点放「天使 / 刘思鉴」，气泡里是 `Răng wǒ píngjìng yào shǒujī de fāngchí`。

**复现**（三个源分别跑）：`qq` 72 行全汉字 / `lrclib` **52 行全拼音** / `netease` 67 行汉字。

**根因**：`lrclib` 对中文歌常有一条**全拼音（带声调）**的歌词版本，它和汉字版一样
"完整 + 时间轴正确"，看不出问题；而它当初是**直接构造 `Lyrics`** 返回的，绕过了
`_as_lyrics` 的质量判据。lrclib 优先级又高于网易云，QQ 音乐只要慢过 `_PRIORITY_GRACE`
（1.2 s）就轮到它——实测三次里两次是拼音、一次是汉字，**是竞态**，不是稳定返回。

**修法**：新增 `_looks_romanized()`（整首 <5% 行含汉字、且 ≥50% 行含带声调拉丁字母），
并让 `_fetch_from_lrclib` 也走 `_as_lyrics`。纯英文歌词不受影响（英文没有 `āáǎà`）。

**修后实测**（每首跑 3 次）：`天使` 三次全部 67 行汉字；`如果我没有伤口` 三次全汉字。
拼音结果计数 **0**。

### 5.7 切歌 / 暂停的实机验证（2026-10-23）

走产品代码 `bodian_control.skip("next")`：

```
process_running = True
sizeof(INPUT)   = 40
target_available= True

切歌前: (37, 393069850, '小霞 (Live)')
调用 bodian_control.skip('next') ...
  返回 = True
切歌后: (38, 374049023, '烟花的台词')
  ✓ 曲目 '小霞 (Live)' -> '烟花的台词'
```

数据库新行在 0.5 s 内出现，**切歌真的生效**。

**排查过程中修正过两个错误结论**（如实登记，避免后人重踩）：

1. 一开始用 `SendInput` 测媒体键，返回 0 就判断"波点不响应媒体键"——错。真实原因是
   我声明 `INPUT` 结构体时只写了键盘成员，`sizeof(INPUT)` = **32**（x64 应为 **40**），
   `SendInput` 报 `ERROR_INVALID_PARAMETER`。补全 `MOUSEINPUT`/`HARDWAREINPUT` 后一次成功。
   现在 `bodian_control.input_struct_size()` 与对应用例把这条钉住。
2. 测试期间用媒体键两次把用户的歌**暂停**了，已发键恢复为播放状态（日志确认
   `mpv playing event=true`）。这类验证必须用可逆动作，并确认恢复。

### 5.8 播放器入口（右键「音乐 → 打开波点音乐」）的实机验证

注册表自动检测命中本机真实安装：

```
=== 注册表键 ===
  ('netease', 'qqmusic', 'kugou', 'soda', 'bodian')

=== 每个播放器的检测结果 ===
  netease   网易云音乐    smtc=True  path=None
  qqmusic   QQ音乐     smtc=True  path=None
  kugou     酷狗音乐     smtc=True  path=None
  soda      汽水音乐     smtc=True  path=None
  bodian    波点音乐     smtc=False path=C:\Program Files (x86)\bodian\bodian_pc.exe
```

`D:\Program Files (x86)\bodian\bodian_pc.exe`（本机另一份安装）同样命中——
两个盘符各有一份、目录名大小写不同，`bodian`/`Bodian` 两种拼法都覆盖到了。

**没有重复启动波点做验证**（它当时正在播放，再启一份只会多出一个进程）。启动分支
的行为由用例与代码审查保证：`registers_smtc("bodian") is False` → 走"只打开、
不发播放指令、不轮询 SMTC"那条分支。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/ tests/` | **All checks passed!**（ruff 0.17.1） |
| 本改动 10 个相关文件 | `pytest -q tests/test_bodian_playing.py tests/test_lyric_sources.py tests/test_music_player_settings.py tests/test_config_schema.py tests/test_architecture.py tests/test_settings_default_marks.py tests/test_settings_copy_lint.py` | **247 passed in 124.77s** |
| 架构红线 | `pytest -q tests/test_architecture.py` | **16 passed** |
| 配置 schema | `pytest -q tests/test_config_schema.py` | **68 passed** |
| **断言有效性（消融）** | `python ablate_bodian.py`（逐条撤掉产品改动后复跑对应用例） | **8/8 全部按预期变红，文件逐字节还原** |

**消融明细**（这是"测试真的能抓缺陷"的证据，不是装饰）：

| 撤掉什么 | 变红的用例 | 结果 |
|---|---|---|
| 挂载链 `bodian_lyrics_rows(self)` | `test_row_is_actually_mounted_in_the_settings_dialog` | 1 failed |
| 采样线程 `allow_bodian=` | `test_sample_loop_passes_bodian_setting_from_config` | 1 failed |
| `is_supplementary_source` 的波点分支 | `test_bodian_source_is_not_the_tracking_slot` + `test_bodian_playback_does_not_take_the_smtc_tracking_slot` | 2 failed |
| 播放状态"只缓存确定结果" | `test_unknown_playing_state_is_not_cached` | 1 failed |
| 日志"先排序再截断" | `test_newest_log_wins_even_when_there_are_many` | 1 failed |
| SQLite URI 百分号编码 | `test_non_ascii_install_path_is_readable` | 1 failed |
| 真锁时沿用旧值 | `test_locked_database_falls_back_to_last_track` | 1 failed |

> **消融抓出了我自己的一条空转用例**（值得写下来）：日志排序那条最初挑 `log_11.log`
> 当"最新"，但 `pathlib.glob` 的枚举序是 `0,1,10,11,2,…,9`——`log_11.log` 排第 4 位，
> 旧实现"取前 8 个"会碰巧留下它，用例照旧变绿。改用 `log_9.log`（枚举序第 11 位）后
> 才真正区分新旧实现。**没有消融就发现不了这条**。

## 七、已知限制与后续

**切歌 / 暂停：已支持**（2026-10-23 追加）。桌宠的这两个动作原本走 SMTC 的
`try_skip_next_async`，波点没有会话 → 恒为 `False`。现在补一条补充通道：发**系统媒体键**
（`pet/music/bodian_control.py`）。四条更"干净"的路都验过不可行，见下表：

| 尝试的通道 | 结果（2026-10-23 实测） |
|---|---|
| SMTC `try_skip_next_async` / `try_toggle_play_pause_async` | 无会话，返回 `False` |
| UIA `InvokePattern`（「上一曲 / 暂停 / 下一曲」按钮） | 三个按钮**不支持**该 Pattern |
| UIA `LegacyIAccessible.DoDefaultAction` | `DefaultAction` 是**空串** |
| 定向 `WM_APPCOMMAND` 投给波点 HWND | 无响应 |
| `PostMessage` 的 `WM_LBUTTONDOWN/UP`（含 UIA 取坐标） | 无响应（Flutter 不认） |
| **`SendInput` 系统媒体键** | **有效**（波点不在前台时也响应，说明它装了全局钩子） |

**代价（必须让用户知道）**：媒体键是**系统级**的，会送到"当前接管媒体键的那个程序"。
所以代码里加了两道闸：**波点进程在跑**，且**最近一次采样来源确实是波点**——否则就是
"想给波点切歌，结果把网易云换了"。两道闸各有消融用例，撤掉即变红。

**本次明确不做**（都写在设置行文案与 README 里，不让用户以为是坏了）：

1. **快进后歌词自动同步**。这是真做不到，不是没做：`hist_song` 只有一个表、35 个字段
   里和时间相关的只有 `duration` 与 `mvduration`，**没有当前播放位置**；日志里的
   `playPos` 是**播放列表索引**（解析式 `playPos: N, length: N` 可佐证），
   `position:` 只在切歌瞬间记录**旧歌**位置、全程 `seek` 出现 0 次；UIA 唯一的
   `Slider` 值是音量（实测 `Value='39%'`，且不支持 `RangeValue`）；也没有 mpv IPC
   管道（常见名探测 0 命中）。所以歌词只能按"检测到这首开始的时刻 + 本机时钟"推算。
   补救是右键菜单「音乐 → 歌词对齐」——因为进度如实标记为"推算"，该入口保持可用。
   **若要真同步，只能等波点自己暴露位置**（注册 SMTC 或在库里加字段）。
2. **foobar 优先会挡住波点**（审查建议③）。foobar 源读窗口标题、没有播放状态，
   开着不播也可能读到上一首。这是**已知且已写进文案**的行为，本次不改成"按播放状态
   择优"——两路都没有真实进度，谁在播没有客观答案，改优先级只会把不确定性换个方向。
3. **「打开波点音乐给主人放歌」只负责打开程序，不负责让它开始播**。波点不注册 SMTC，
   发播放指令一定失败（`play_session_for` 恒 `False`），所以这条入口是"打开它，由它
   自己续播上次的队列"。文案与 README 都这么写，不让用户以为点了就会出声。
4. **音量控制不做**。波点的音量在它自己 UI 里，UIA 那个 Slider 不支持 `RangeValue`，
   写不进去；媒体键也没有音量键位（`VK_VOLUME_*` 是系统主音量，不是它自己的）。

**触发条件**（将来无需改本模块的情形）：

- 波点若开始注册 SMTC，本补充源会自动让位（只在无在播会话时被询问），用户可在设置里关掉它；
  切歌也会自动改回走 SMTC（`skip_track` 先试 SMTC，成功就不再发媒体键）。
- 波点若在库里增加播放位置字段，可在 `_parse_row` 里读出来并让 `_read_bodian_playback`
  返回真实 `position`——那时应同时更新 §一 的红线说明，快进同步也才有解。

## 八、风险与回滚

**影响面**：新增键默认关；关闭时改动只有 `now_playing` 里一次布尔判断（0.30 µs）。
开启后仅影响"没有 SMTC 在播会话"的那一拍，且只读第三方程序的本地文件。

**配置迁移**：新增 1 键 `bodian_lyrics_enabled`（bool，默认 `False`）。老配置无此键时
走默认值，无需迁移；`config.json` 版本号不变。

**降级路径**：波点未安装 / 未播放过 / 数据结构变化 / 库被锁 / 日志轮转 → 一律 `None`，
表现为"没有歌词"，不会产生错误歌词，也不影响其他播放器。

**回滚**：`git revert` 该提交即可——新模块与测试文件整份删除，其余文件是纯增量。
**无落盘残留**：本改动不写任何文件、不写注册表，回滚后唯一的残留是 `config.json` 里一个
未被读取的 `bodian_lyrics_enabled` 键（下次保存会被保留，不影响行为）。

---

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
