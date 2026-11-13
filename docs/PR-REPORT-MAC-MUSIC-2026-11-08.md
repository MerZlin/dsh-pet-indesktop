# macOS 音乐联动（2026-11-08）

> **基线**：`34d810b`（chore(update): update.json 指向 v4.4.0）
> **分支**：`feat/mac-music`（开发时为 `claude/magical-galileo-9mftoe`）　**日期**：`2026-11-08`
> **范围**：18 个文件 + 3 张截图（实现 8〔新增 1〕、测试 6〔新增 4〕、脚本 1、文档 3），`+2588 / −40`（含本报告 281 行）
> **关联**：[SMTC 媒体类型过滤](PR-REPORT-SMTC-MEDIA-TYPE-2026-10-06.md)（否决规则与唱歌探针的来源）、
> [Firefox 媒体标识](PR-REPORT-FIREFOX-MEDIA-SESSION-2026-10-20.md)（浏览器识别口径）、
> [四播放器启动入口](PR-REPORT-MUSIC-PLAYER-PATHS-2026-10-16.md)（路径 ≠ 歌词）

**验收边界（先说清）**：本 PR 在 Linux 开发容器里完成，**没有在真 Mac 上运行过**。
§五写明了哪些是真实执行过的、哪些只被替身覆盖、以及 Mac 上验收要跑的探针命令。
不把「测试绿」写成「Mac 上可用」。
（2026-11-13 补：真 Mac 上跑过一次只读探针，已确认项与仍未验证项见 §九；验收仍未完成。）

## 一、核心特性

音乐联动此前整条只在 Windows 上工作：读曲目靠 SMTC（WinRT）、唱歌门靠 pycaw 峰值表、
找播放器靠扫 `.exe`、启动靠 `os.startfile`——四处都有 `sys.platform != "win32"` 的早退，
macOS 上歌词、切歌/暂停、自动唱歌、「打开〈播放器〉给主人放歌」全部静默失效。

本 PR 给四条链路各补上 macOS 实现，调用方（歌词控制器、唱歌门、右键菜单、设置页）不分平台：

| # | 能力 | macOS 实现 |
|---|---|---|
| 1 | 读正在播放（歌词） | 系统「正在播放」（MediaRemote）。**经 `/usr/bin/osascript` 跑 JXA 读取**——macOS 15.4 起 `mediaremoted` 只把正在播放信息回给 `com.apple.*` 进程，普通应用（含本进程）直接调 `MRMediaRemoteGetNowPlayingInfo` 只拿到空结果。常驻助手进程、行协议、请求 id 配对、超时、退避、空闲退出 |
| 2 | 暂停 / 切歌 / 上一首 | `MRMediaRemoteSendCommand`（ctypes，进程内）——15.4 之后被收紧的只有读，命令仍对普通进程有效 |
| 3 | 自动唱歌 | 没有峰值表 → 以「正在播放」为准：探针缓存里有被接受、且**正在播**的会话才唱（新增 `now_playing.song_session_playing`） |
| 4 | 打开播放器并播放 | 「应用程序」文件夹里按包名找 `.app`，找不到再把这两个文件夹浅扫一层、按各包 Info.plist 的 bundle id 认（纯本地有界，不起进程）；`open` 启动；会话身份 = 应用包 Info.plist 的 `CFBundleIdentifier` |
| 5 | 设置页 | 「浏览器媒体会话参与歌词」在 mac 上建控件（真生效）；路径选择器过滤 `*.app`；四条说明按平台写实（不再在 mac 上写「仅 Windows 可用」） |

网易云音乐 / QQ音乐 / 酷狗 / 汽水音乐的 Mac 版、Apple Music、Spotify、浏览器——只要出现在
控制中心「正在播放」里就能读到，与 SMTC「一份会话列表覆盖所有播放器」同一个思路。

**红线 / 不变量**

- **Windows 行为逐位不变**：所有 mac 分支都挂在 `sys.platform == "darwin"` 之后；Windows 的
  SMTC 选择器、峰值门、扫盘、`os.startfile` 一行未动。唯一共享改动是 `is_browser_session` 多认
  一张 mac bundle id 表（表内 id 不可能与 Windows exe 名重名，实测开销见 §四）。
- **主线程不读系统媒体**：唱歌门在 mac 上同样只读后台探针的缓存，osascript 助手只在采样线程 /
  探针线程 / 菜单 worker 线程里被调用。
- **否决规则只有一份**：视频 / 浏览器开关在 mac 上复用 `_source_allows_singing`。
- **不发 Apple Events、不要授权**：助手脚本只经 ObjC 桥读本机框架，不 `tell application`
  任何播放器，不触发「自动化」弹窗，Info.plist 不需要 `NSAppleEventsUsageDescription`。
- **无新依赖、无新配置键**：只用标准库（`subprocess`/`select`/`ctypes`/`plistlib`）与系统自带的
  `/usr/bin/osascript`、`/usr/bin/open`。播放器定位保持「纯本地有界目录查找、不起进程」
  （`test_detection_stays_local_and_bounded` 钉住；初版用过 Spotlight `mdfind`，被这条守卫
  拦下后改为浅扫，见 §三）。

## 二、修改文件说明

增删行数（`git add -N` 后 `git diff --numstat 34d810b`，截图不计）：

| 文件 | 增 / 删 | 文件 | 增 / 删 |
|---|---|---|---|
| `pet/music/mac_media.py`（新增） | +569 / −0 | `tests/test_mac_media.py`（新增） | +420 / −0 |
| `pet/music/now_playing.py` | +202 / −5 | `tests/test_now_playing_mac.py`（新增） | +262 / −0 |
| `pet/music/players.py` | +140 / −3 | `tests/test_music_players_mac.py`（新增） | +171 / −0 |
| `pet/music/settings.py` | +68 / −14 | `tests/test_music_launch_mac.py`（新增） | +174 / −0 |
| `pet/context_menus/shared.py` | +47 / −4 | `tests/test_music_player_settings.py` | +65 / −6 |
| `pet/music/detect.py` | +27 / −2 | `tests/test_architecture.py` | +4 / −0 |
| `pet/settings_pet_controls.py` | +6 / −4 | `scripts/probe_mac_now_playing.py`（新增） | +139 / −0 |
| `pet/modern_settings_dialog.py` | +2 / −2 | `README.md` / `docs/INDEX.md` / 本报告 | +9 / +2 / +281 |

截图（新增，渲染方式见 §五-3）：`docs/screenshots/mac-music-2026-11-08/` 下
`page_darwin_lyrics.png`、`page_darwin_paths.png`、`page_win32_lyrics.png`。

### 实现

| 文件 | 改动意图 |
|---|---|
| `pet/music/mac_media.py`（**新增**） | macOS 后端，零 Qt。① `JXA_HELPER_SCRIPT`：读一行请求 id → 读一次 `MRNowPlayingRequest`（`localNowPlayingItem.nowPlayingInfo` 的 title/artist/album/duration/elapsed/timestamp/rate/mediaType、`localNowPlayingPlayerPath.client` 的 bundle id / 父应用 / 显示名、`localIsPlaying`）→ 写一行 JSON（带回 id）；stdin EOF 即退出。② `_Helper`：Popen 管道 + `select` 读，按 id 丢弃迟到应答，单行上界 64 KiB。③ 生命周期：单次请求超时 3 s（超时杀进程）、连续 3 次失败退避 30 s、助手报「不可用」退避 600 s、空闲 30 s 由收尾线程关掉、0.5 s 复用窗口合并歌词采样与唱歌探针的并发请求。④ `parse_reply` / `position_at`：纯函数，外部输入按不可信收；位置按 `elapsed + (now − timestamp) × rate` 外推（mac 的 elapsed 只在状态变化时更新，不能套 SMTC 的「< 5 s」门），timestamp 在未来 / 比整首歌还旧 60 s 以上 → `None`（回退本地计时）。⑤ `send_command`：ctypes 惰性加载 `MRMediaRemoteSendCommand`，失败只试一次 |
| `pet/music/now_playing.py` | darwin 分支：`get_now_playing` → `_mac_playback`（同形状 `Playback`，同一套否决）；`skip_track` / `toggle_play_pause` → `_mac_command`（没有「正在播放」应用时不发）；`play_session_for` → `_mac_play_session_for`（只在目标 bundle id 就是此刻的「正在播放」应用时发 Play，已在播不发）；会话探针 `_read_session_facts` 按平台取事实表；新增 `song_session_playing`（给没有峰值表的平台当「在放歌」判据）与共用的 `_fresh_session_facts`（`song_session_accepted` 改为调用它，语义不变）；新增 `media_session_supported()`（平台判据唯一一处）；`_MAC_BROWSER_BUNDLE_IDS` + WebKit 子进程前缀进 `is_browser_session` |
| `pet/music/detect.py` | darwin → `_mac_music_playing()`：只读探针缓存；无数据按「没在放」（mac 没有峰值兜底，放行等于每次启动先唱一拍） |
| `pet/music/players.py` | `_MAC_APPS`（四个播放器的包名 + bundle id 线索，注册表键集一致由测试钉住）；`_search_mac_app`（`/Applications`、`~/Applications` 先按包名，再浅扫一层按 Info.plist 的 bundle id 认，命中多个按包名确定性取第一个）；`_mac_app_entries`（只看根目录直接的 `*.app`、每根最多 512 条目）；`_is_launchable`（mac 上手填 `.app` 目录有效）；`app_bundle_root` / `bundle_identifier` / `session_key` |
| `pet/context_menus/shared.py` | `_launch_player_and_play`：会话身份改走 `music_players.session_key`（Windows 仍是 exe basename，现有用例原样通过）；darwin 经 `_open_mac_app`（`/usr/bin/open <应用包>`，15 s 超时）启动，`open` 失败弹气泡说明而不是静默等 10 秒；轮询节奏提成 `_LAUNCH_POLL_SECONDS` / `_LAUNCH_POLL_ATTEMPTS`（值不变） |
| `pet/music/settings.py` | `browser_media_supported` 改用 `now_playing.media_session_supported`；`player_name_filter`（mac `*.app`）；`_player_row_hint` / `detect_row_hint` / `lyric_row_hint` / `sing_row_hint` 按平台写实 |
| `pet/settings_pet_controls.py` | 浏览器媒体开关的建控件判据改为 `media_session_supported()`（与行同一个判据，不留游离开关） |
| `pet/modern_settings_dialog.py` | 「音乐自动唱歌」「显示歌词」两行说明改取 `settings_music` 的函数（**行数不变**，预算无影响） |

### 测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_mac_media.py`（新增） | 解析真值表（字段映射、父应用归属、坏形状 / 坏数值）；位置外推（在播 / 暂停 / 夹紧 / 无时间轴 / 时钟异常 / 速率）；**真实子进程**跑同一行协议：常驻不重启、无条目、迟到旧 id 丢弃、挂住超时被杀、起即死退避（只起 `HELPER_MAX_FAILURES` 次）、「不可用」只起一次、空闲自退、复用窗口、4 线程并发串行；命令（调用参数 / 非 mac / 加载失败 / FFI 异常 / 枚举值）；助手脚本与解析器的键契约、经 `/usr/bin/osascript`、不含 `Application(` |
| `tests/test_now_playing_mac.py`（新增） | darwin 下 `get_now_playing` 映射 / 位置外推 / 无时间轴 / 暂停 / 空标题 / 视频否决 / 四种浏览器随开关 / 原生播放器不是浏览器 / mac 不问 foobar / 不抛；控制命令与「没有正在播放不发」/ 被拒；`play_session_for` 只打目标、读此刻状态、已在播不发；探针事实表、只读缓存、否决、首拍 None；`detect.is_music_playing` 在 mac 上的四种状态 |
| `tests/test_music_players_mac.py`（新增） | 真实目录树：四个包名命中、用户「应用程序」、空目录不算应用、改过包名按 bundle id 命中（大小写无关）、不进子目录、条目上界、多命中确定性、负缓存不重扫、手填 `.app` 有效 / 普通目录无效 / 非 mac 上 `.app` 无效、`app_bundle_root`、`session_key`（Info.plist / 坏 plist / Windows basename） |
| `tests/test_music_launch_mac.py`（新增） | 点击路径 worker：mac 上 `open` 应用包 + 按 bundle id 轮询、已在「正在播放」不再启动、`open` 失败弹气泡且不轮询、`_open_mac_app` 收回到 `.app` 且带超时 |
| `tests/test_music_player_settings.py` | 原「非 Windows 不出浏览器行」改为平台矩阵（Linux 无、mac/Windows 有）——**这是本需求刻意改变的旧行为**；新增 mac 上真建对话框建出控件并保存、选择器 `*.app`、四条说明按平台 |
| `tests/test_architecture.py` | `music/mac_media.py` 加入零 Qt 纯模块名单 |

### 脚本与文档

| 文件 | 内容 |
|---|---|
| `scripts/probe_mac_now_playing.py`（新增） | Mac 实机探针：助手原始往返（含 stderr）、产品路径首读与稳态耗时、`--watch` 看外推、产品层视图（浏览器开关两档、唱歌探针）、四个播放器定位与 bundle id、`--toggle` 发一次暂停/播放 |
| `README.md` | 新增「音乐联动（macOS）」小节，写明**尚未真机验收** |
| `docs/INDEX.md` | 「PR 报告存档」登记本报告 |
| 本报告 | — |

### 未改动（故意）

- `pet/music/lyric_controller.py`：采样线程、令牌、暂停记账都与平台无关；`_tracked_app_id`
  在 mac 上照常回传（mac 分支忽略它——系统只有一个「正在播放」应用）。
- `pet/music/foobar.py` 与 foobar 两行设置：窗口标题源走 Win32 枚举，mac 上不提供
  （foobar2000 for Mac 若上报「正在播放」，走 MediaRemote 自然就有）。
- `pet/window_alerts.py` / `pet/overlay_shell.py` 的唱歌链路：只调 `is_music_playing()`，不分平台。
- `requirements.txt` / `scripts/build_macos.sh`：无新三方依赖；标准库由 PyInstaller 静态收集。
- 菜单注册表 / `features.py`：九个音乐动作 id 不变，不分平台。

## 三、实现要点

**为什么是 osascript + JXA，而不是 ctypes 直接读**：15.4 起读接口对非 `com.apple.*`
进程返回空、不报错（Pock now-playing widget 源码注释、mediaremote-adapter README、
SKaplanOfficial 的 JXA gist 三处一致）。备选是 mediaremote-adapter 的 perl 路线——它要随包
分发一个编译好的 `MediaRemoteAdapter.framework`（需要 Xcode 构建、签名），本仓库的 mac
构建脚本没有这套工具链，JXA 方案只用系统自带二进制。代价：私有框架，未来系统更新可能
再收紧（§七）。

**为什么助手常驻**：每拍新起 osascript 要付进程启动 + 框架加载。行协议 + 请求 id 让
「超时后才迟到的应答」不会被当成下一拍的结果；`select` 读 + 超时杀进程保证一次挂住的
MediaRemote 调用最多钉住采样线程 3 秒（与 Windows 探针 `SESSION_PROBE_TIMEOUT_SECONDS`
同量级）。

**为什么唱歌门在 mac 上「无数据 = 不唱」**：Windows 的「无数据放行」是因为峰值表还在
把关（真有声音才唱）；mac 没有峰值表，放行就是无条件唱。读系统输出电平要「屏幕录制」或
Core Audio tap 授权——为一个唱歌动画向用户要录屏权限不值。

**为什么命令前先读一次**：MediaRemote 命令发给「正在播放」应用，没有这个应用时发了
也不知道去哪；与 Windows「没有会话 → False」同口径。`play_session_for` 读的是
`max_age=0`（此刻），不用复用窗口里的旧值，避免把 Play 发给刚换掉的应用。

**为什么不用 Spotlight 找播放器**：初版在包名对不上时用 `mdfind` 按 bundle id 查，被既有守卫
`test_music_player_cache.py::test_detection_stays_local_and_bounded`（定位模块不许联网 / 读注册表 /
**起进程**）拦下。这条守卫是 Windows 侧扫盘事故（PR #147 取证）后定下的纪律，不绕开它：改为把
`/Applications`、`~/Applications` 浅扫一层（不进子目录、每根最多 512 条目），逐个读 Info.plist
比 bundle id。覆盖面差别只在「装在这两个文件夹以外」的情况，那时走手动选 `.app`（§七-6）。

**bundle id 只是线索，不是身份**：`_MAC_APPS` 里的 bundle id 只用于包名对不上时的浅扫匹配；判身份读
找到的应用包自己的 Info.plist。QQ音乐 / 汽水的 bundle id 在公开资料里查不到或只有一处
（见 `players.py` 注释），即使写错，包名命中照样能找到、身份照样对。

## 四、性能分析

**环境**：Linux 6.18 x86_64（4 vCPU 云容器）/ CPython 3.13.18 / PySide6 6.11.3。**不是 macOS**——
真 osascript + MediaRemote 的耗时只能在 Mac 上用 `scripts/probe_mac_now_playing.py` 测（它打印
首读与稳态 20 次的中位/最大值），这里不编数字。下表测的是本 PR 自己的代码：

| 指标 | 实测 | 方法 / 样本 | 归属 |
|---|---|---|---|
| 助手往返（行协议 + `select` 读 + JSON 解析），**真实 JXA 脚本**跑在 Node 的 ObjC 桥替身上 | 中位 **0.065 ms**，p95 0.143 ms，最大 8.1 ms | 1000 次，`read_now_playing(max_age=0)` | 新增，mac 上每拍 ≤1 次 |
| 首个请求（起助手进程 + 首次应答） | 23.3 ms（Node 替身；osascript 冷启动在 Mac 上实测） | 1 次 | 新增，每次助手启动 1 次 |
| 复用窗口命中（歌词 1 s + 唱歌探针 2 s 叠加时） | 中位 **0.31 µs** | 10000 次 | 新增 |
| 常驻助手空闲 CPU | **0 tick / 5 s**（阻塞在 stdin 读，不轮询） | `/proc/<pid>/stat` utime+stime | 新增 |
| `parse_reply` | 2.33 µs/次 | `timeit` best of 5 × 100000 | 新增 |
| `position_at` | 0.24 µs/次 | best of 5 × 200000 | 新增 |
| `is_browser_session("cloudmusic.exe")`（**Windows 热路径**） | 基线 0.287 µs → 现 0.252 µs（噪声内，无变化） | best of 5 × 200000，基线取 `34d810b` 原文件 | 既有，每拍 |
| `now_playing` 冷导入（隔离解释器） | 基线 53.4 ms → 现 57.2 ms（+3.8 ms，其中 `mac_media` 自身 1.6 ms，余为首次导入 `subprocess`/`json`；应用内这两个早已加载） | `python -X importtime`，11 次中位 | 新增，每进程 1 次 |
| mac 播放器定位：包名命中 | 0.02 ms | 50 次，tmp 目录 | 新增，后台线程 |
| mac 播放器定位：未命中、浅扫 160 个应用读 Info.plist（最坏） | 中位 16.5 ms，最大 19.9 ms | 50 次 | 新增，后台线程，负结果缓存后不重扫 |
| 四播放器全查（1 命中 + 3 未命中） | 中位 48.2 ms | 20 次 | 新增，「自动检测」/启动预热 |
| `cached_player`（GUI 线程，缓存热） | 0.27 µs | 20000 次 | 既有路径，未变 |
| `session_key`（读 Info.plist） | 73 µs/次 | 2000 次 | 新增，只在菜单点击的 worker 里 |

**结论**

1. **稳态开销**：Windows / Linux 路径无变化（唯一共享改动 `is_browser_session` 实测在噪声内）。macOS
   上歌词开启时每秒一次助手往返（替身实测 0.065 ms 的本进程开销；助手侧 MediaRemote 调用耗时待 Mac
   实测），唱歌探针每 2 s 一次且大多落在 0.5 s 复用窗口外——合计 ≤1.5 次/秒。
2. **新增路径成本与频率**：助手启动每次功能启用 1 次（空闲 30 s 后收掉，再用再起）；播放器定位
   最坏 ~50 ms，只在后台预热 / 「自动检测」/ 菜单点击 worker 里，负结果缓存。
3. **新的系统调用 / 进程 / 线程 / 网络 / 磁盘**：macOS 上新增 1 个常驻子进程（`osascript`，按需起、
   空闲退出）+ 1 条收尾线程（随助手生灭）；`open` 只在点击「打开播放器」时 1 次；读 Info.plist
   只在定位/点击时；**无网络、不写盘**。Windows / Linux 无任何新增。
4. **内存**：本进程只多一个 `MacNowPlaying` 缓存对象；助手进程常驻内存在 Mac 上实测（Node 替身
   49 MB 不代表 osascript）。

## 五、实机运行记录

**本机是 Linux 云容器，没有 Mac**——下面每条都写清是在哪里、跑的是什么。

1. **为什么不能在本机自动验证 macOS 行为（探针证据）**：

   ```text
   $ python3 scripts/probe_mac_now_playing.py
   macOS ?  machine=x86_64  python=3.13.18  frozen=False
   不是 macOS：本探针只在 Mac 上有意义。
   exit=2
   ```

   本机没有 `/usr/bin/osascript`、没有 MediaRemote 框架，也装不了任何 Mac 播放器；JXA 的 ObjC 桥
   语义（`ObjC.unwrap` 对 `NSNumber`/`NSDate` 的行为、`localIsPlaying` 是否返回 JS 布尔）只有真
   系统能回答。

2. **真实 JXA 脚本 × 真实 Python 协议的往返**（本机执行）：把 `JXA_HELPER_SCRIPT` 原文交给 Node
   （`node --check`：语法 OK），前置一层按 JXA 文档行为写的 ObjC 桥替身，由 `mac_media` 的产品代码
   驱动：

   ```text
   playing: MacNowPlaying(bundle_id='com.netease.163music', ..., title='晴天', artist='周杰伦',
            album='叶惠美', duration=269.0, elapsed=30.0, timestamp=1794104521.9, rate=1.0, playing=True, ...)
   position≈ 35.0
   same helper pid: True  roundtrip median 0.072 ms p95 0.289 ms
   safari: com.apple.Safari False None None
   no item: None helper alive: True
   unsupported: None helper after: None retry_after set: True
   exit on stdin EOF: rc=0 in 0.008s
   ```

   证明的是：脚本的 stdin 行循环、id 回带、字段映射、「不可用」应答、EOF 自退与 Python 侧配对
   无误；**不证明**真 ObjC 桥给出的值形状——那一层是替身。

3. **设置页（真实对话框，强制 `sys.platform` 渲染）**：本机 offscreen 构建 `ModernSettingsDialog`，按
   搜索深链同一路径（侧栏 → `reveal_settings_row` → 滚动）抓整窗：
   `docs/screenshots/mac-music-2026-11-08/page_darwin_lyrics.png`（「浏览器媒体会话参与歌词」在、
   foobar 两行不在、唱歌/歌词说明是 mac 口径）、`page_darwin_paths.png`（四行写「指向 .app 应用」、
   检测说明写「应用程序」文件夹、选择器过滤器 `应用程序 (*.app);;所有文件 (*)`）、
   `page_win32_lyrics.png`（Windows 对照：foobar 两行仍在，结构不变）。这是**同一份控件代码在
   Linux 上的渲染**，字体与 macOS 原生外观不同。

4. **真应用启动（Linux，确认本 PR 不破坏启动与音乐模块装配）**：隔离 `HOME`，配置
   `feature_activation={"enabled":["music"]}`、`music_lyric_enabled=True`、`music_sing_enabled=True`，
   `QT_QPA_PLATFORM=offscreen timeout 25 python3 -m pet`：进程跑满 25 s 被 timeout 收掉（exit 124），
   日志到「进入事件循环」后正常 tick，无 Traceback / ERROR。

5. **边界与失败路径（本机执行，替身子进程）**：助手挂住 → 0.3 s 超时返回 None 并杀进程；助手起即死 →
   只起 3 次后退避；「不可用」→ 只起 1 次；空闲 → 自行退出（`tests/test_mac_media.py`）。

6. **Mac 上的验收命令**（交给有 Mac 的评审/用户；报告追加真机输出后才算验收）：

   ```text
   # 先在任一播放器里放一首歌（控制中心里能看到它）
   python3 scripts/probe_mac_now_playing.py --watch 10      # 读到曲目、进度随秒增长、四个播放器定位
   python3 scripts/probe_mac_now_playing.py --toggle        # 暂停/播放是否真的切换（再跑一次恢复）
   # 再在桌宠里：设置开「显示歌词」「音乐自动唱歌」→ 看气泡歌词与唱歌动画；
   # 右键「音乐 → 切歌 / 暂停 / 打开网易云音乐给主人放歌」
   ```

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python3 -m ruff check pet/ tests/ scripts/probe_mac_now_playing.py`；`git diff --check` | All checks passed；无空白问题 |
| 先红（基线 `34d810b` worktree，跑新测试） | 同下四个新文件 + `test_music_player_settings.py` | `test_mac_media` / `test_now_playing_mac` 收集即 ImportError（模块不存在）；其余 **10 failed + 13 errors**（35 passed 为既有设置用例与「非 mac 上 .app 无效」这条不回归守卫） |
| 后绿（聚焦） | `pytest tests/test_mac_media.py tests/test_now_playing_mac.py tests/test_music_players_mac.py tests/test_music_launch_mac.py` | **81 passed** |
| 相关族 | 音乐 / now_playing / 设置 / 菜单 / 门控 / 架构 / 依赖 / 报告纪律等 26 个文件 | **967 passed, 2 skipped** |
| 满载 3 遍（4 核各一个死循环占满） | 新增 4 个文件 ×10 遍 | **10/10 绿**（81 passed，每遍约 2 s） |
| 全量 | `QT_QPA_PLATFORM=offscreen python3 -m pytest -q` | **9071 passed, 83 skipped, 7 failed**（1329 s）。其中 1 条是本 PR 引入的禁词（`settings.py` 注释里写了 "SMTC"，`test_settings_ia_first_cut` 拦下）——**已修复**并单独复跑通过；其余 6 条见下 |
| 架构红线 | `tests/test_architecture.py`（`music/mac_media.py` 已加入零 Qt 名单） | 通过 |

**全量里与本 PR 无关的 6 条失败**：在**未修改的基线 `34d810b` 独立 worktree**、同解释器同依赖下逐条复跑，
**6 条全部同样失败**——`test_balance` ×2（`date(2027,1,1)` 已能查到「元旦节」，节假日数据随日期
变化）、`test_island_alert_colors` ×2 与 `test_island_extension_chain` ×1（容器字体度量下找不到收缩级
/ 几何差 1 px）、`test_memory_recall` ×1（归档标记）。未扩大范围去修，也不据此宣称全量全绿。

**满载复跑发现的既有段错误（不是本 PR 引入，如实登记）**：把既有的
`test_now_playing_session_probe.py` + `test_music_player_detect_refresh.py` + `test_music_player_cache.py`
放在满载下跑，**基线 `34d810b` 上 14 遍崩 4 遍**（本分支 10 遍崩 1 遍），每次都在
`test_launch_player_bubbles_hint_when_player_missing` 开头 `Fatal Python error: Segmentation fault`
（`<freed thread state>`）。成因判断：前一条用例在 worker 线程还在 `bridge._worker_done.emit()`
时就 `shiboken6.delete(pet)`——信号桥是宿主的子对象，「销毁与 emit 并发」。本 PR 的新用例改为
**先 join `music-launch` 线程、处理完 queued 信号再销毁宿主**（`_release`），新用例满载 10/10 绿；
既有用例未改（超出本需求范围，建议另立修复：同一竞态在产品里对应「worker 收工瞬间宿主窗口被销毁」）。

## 七、已知限制与后续

1. **未在真 Mac 上验收**。尤其三处只能在真机确认：JXA 桥对 `NSNumber` / `NSDate` 的
   unwrap 语义（脚本已对两种可能都兜底）、`MRMediaRemoteSendCommand` 在当前系统版本对
   普通进程仍有效、四个播放器 Mac 版是否都上报「正在播放」（含时间轴）。验收命令见 §五。
2. **私有框架**：Apple 再收紧时，助手会报「不可用」→ 长退避，表现为 mac 上歌词 / 唱歌
   静默停用（不崩、不弹窗）；`probe_mac_now_playing.py` 第 1 项会直接显示原因。
3. **只有一个「正在播放」应用**：两个播放器同时在播时，跟随系统选中的那个（与媒体键一致）；
   没有 Windows 那种多会话选择。
4. **不上报「正在播放」的声音不唱**（游戏、部分网页播放器）——行文案已写明。
5. 汽水 / QQ音乐 Mac 版的 bundle id 未经公开资料确认，仅影响「包名对不上时」的浅扫匹配（§三）。
6. 装在「应用程序」以外（或其子文件夹里）的播放器不会被自动找到——与 Windows「不做全盘
   扫描」同口径，需要在「播放器路径（高级）」里手动选 `.app`。

## 八、风险与回滚

- 影响面：只有 darwin 分支与 `is_browser_session` 的一张新表；Windows / Linux 路径不变。
- 无新配置键、无迁移、不写盘；助手进程随本进程退出（管道 EOF）或空闲 30 s 退出，不留残留。
- 回滚：`git revert` 本 PR 即可，无落盘状态。

## 九、第二轮：真 Mac 探针记录（2026-11-13）

§五第 6 条写明「报告追加真机输出后才算验收」。本节是第一次在真 Mac 上跑探针的原始记录——
**只完成了一部分**，没完成的部分逐条写在下面，不把「探针能跑」写成「Mac 上已验收」。

**环境**：macOS 27.2（26B123）/ arm64 / CPython 3.13.18（仓库 `.venv`），分支 `feat/mac-music` @ `3764740`。
探针开始时没有任何应用在播放；没有替用户启动播放器，也没有用 `--toggle` 改动用户的播放状态。

```text
$ python scripts/probe_mac_now_playing.py --watch 10
macOS 27.2  machine=arm64  python=3.13.18  frozen=False
== 1. 助手脚本原始往返（osascript -l JavaScript）
   exit=0
   stdout={"ok":true,"item":null,"id":"probe"}
== 2. 常驻助手读取（产品路径，首个请求含助手启动）
   首读: 没有读到「正在播放」（没有应用在播，或读取不可用——看第 1 项）
   首读耗时 32.7 ms
   稳态 20 次：中位 0.13 ms，最大 0.67 ms，助手 pid=98231
   第 1 秒: 没有读到「正在播放」（没有应用在播，或读取不可用——看第 1 项）
   ……（第 2–10 秒同上，共 10 行）
== 3. 产品层（now_playing）看到的结果
   allow_browser=False: None（不显示、不取词、不唱歌）
   allow_browser=True: None（不显示、不取词、不唱歌）
   唱歌探针事实表=()  song_session_playing(浏览器关)=False
== 4. 播放器定位（右键「打开…给主人放歌」）
   网易云音乐: /Applications/NeteaseMusic.app  bundle id=com.netease.163music  (0.5 ms)
   QQ音乐: 未找到  (32.4 ms)
   酷狗音乐: 未找到  (13.7 ms)
   汽水音乐: /Applications/汽水音乐.app  bundle id=com.soda.music  (0.0 ms)
{"helper_pid": 98231}
exit=0
```

**这次真机确认了什么**

| # | 结论 | 依据 | 对照（§四 / §七 的替身数字或悬念） |
|---|---|---|---|
| 1 | 真 `/usr/bin/osascript -l JavaScript` 能跑起助手脚本并按行协议应答，没有落进「不可用」分支 | 第 1 项 `exit=0`、`"ok":true`、id 回带 | §五第 2 条此前只在 Node 替身上成立 |
| 2 | 助手启动 + 首次应答 **32.7 ms**（1 次） | 第 2 项 | Node 替身 23.3 ms |
| 3 | 稳态往返中位 **0.13 ms**、最大 0.67 ms（20 次，同一助手 pid） | 第 2 项 | Node 替身中位 0.065 ms |
| 4 | 「没有正在播放」这条边界在真机上的产品层结果：不显示、不取词、不唱歌 | 第 3 项 | 此前只有替身用例 |
| 5 | 网易云 Mac 版包名与 bundle id 与代码一致（`NeteaseMusic.app` / `com.netease.163music`），包名命中 0.5 ms | 第 4 项 | §四替身 0.02 ms |
| 6 | 汽水音乐 Mac 版 bundle id 实为 `com.soda.music`，与 `_MAC_APPS` 里的值一致 | 第 4 项 | 解掉 §七第 5 条里汽水的那一半 |
| 7 | 未安装的播放器（QQ音乐 / 酷狗）走到浅扫未命中：32.4 ms / 13.7 ms（本机 `/Applications` 40 个条目） | 第 4 项 | §四替身（160 个应用）中位 16.5 ms |

**仍未验证（§七第 1 条继续有效）**

1. **读到曲目时的字段形状**：`item` 为 `null` 时走不到 `NSNumber` / `NSDate` 的 unwrap，标题 / 时长 / 进度
   外推都没有被真系统的值检验过。另外，`"item":null` 本身分不出「确实没有应用在播」和「系统对这次
   查询给了空结果」——要在放歌时复跑 `--watch 10` 才能排除后者。
2. **`MRMediaRemoteSendCommand`**：没有跑 `--toggle`（会暂停 / 恢复用户正在听的东西），切歌 / 暂停
   在 macOS 27.2 上对普通进程是否仍有效，未知。
3. **桌宠内的用户可见行为**：气泡歌词、唱歌动画、右键「打开网易云音乐给主人放歌」都没有在真机上看过。
4. **QQ音乐 / 酷狗 Mac 版**：本机未安装，包名与 bundle id（`com.tencent.QQMusicMac` / `com.kugou.mac.Music`）
   仍未在真机复核。
5. 助手进程常驻内存未采样（§四第 4 条仍是空缺）。

## 十、第三轮：真 Mac 全量暴露的 6 条失败与修复（2026-11-13）

首轮全量是在 Linux 容器里跑的（§六），darwin 分支在那里不会被走到。推送前第一次在 **macOS 主机**上跑
全量（macOS 27.2 / arm64 / CPython 3.13.18，`python scripts/ci_shards.py` 四分片），本分支比基线多出
**6 条失败**——同一台机器、同一解释器下，不含本 PR 的分支这 6 条全过。上游 CI 有 `macos-latest` 任务，
不修的话那一格会红。

### 失败与根因

| # | 用例 | 现象 | 根因 |
|---|---|---|---|
| 1–3 | `test_now_playing_session_probe.py`：`test_os_request_happens_on_probe_thread_not_the_caller`、`test_probe_survives_sampling_errors_and_still_exits`、`test_gate_uses_probe_facts_and_takes_effect_without_resampling` | 假 WinRT 一次都没被问到，探针「没有采样」 | 这些是 Windows SMTC 采样路径的线程 / 超时契约用例，靠 `_import_winrt` 打桩。本 PR 新增的 `_read_session_facts` 在 darwin 上改读 MediaRemote，mac 主机上桩被绕过，还会真的起 `osascript` 助手 |
| 4 | `test_menu_layout.py::test_new_player_entries_click_routes_its_own_key_through_the_shared_worker` | `played=['']`，期望 `['kugou.exe']` | 用例用 `D:/players/kugou.exe` 假路径、断言 exe 名；本 PR 新增的 `music_players.session_key` 在 darwin 上取 bundle id，对非 `.app` 路径是空串 |
| 5 | `test_music_player_cache.py::test_launch_player_resolves_in_worker_thread_not_calling_thread` | `'' == 'cloudmusic.exe'` | 同 #4 |
| 6 | `test_settings_copy_lint.py::test_no_jargon_outside_advanced_groups` | `music_lyric: ['QQ']` | mac 口径的「显示歌词」说明举例写了「QQ音乐」，`QQ` 被缩写判据命中；这段文案只在 darwin 渲染，Windows / Linux 上 lint 看不到 |

#1–#5 的产品行为是对的（mac 路径另有 `test_now_playing_mac.py` / `test_music_launch_mac.py` 覆盖），错在既有用例
没有声明自己守的是 Windows 口径；#6 是文案问题。

### 修改文件说明（本轮）

| 文件 | 增删 | 改动与原因 |
|---|---|---|
| `tests/test_now_playing_session_probe.py` | +8 / −1 | 假 WinRT 桩 `_install` 里把 `sys.platform` 钉成 `win32`：本文件守的是 Windows 采样路径。该路径上的平台分支只有 `_firefox_app_ids`（非 Windows 主机上 `import winreg` 失败即返回空集，与不钉时结果相同） |
| `tests/test_menu_layout.py` | +7 | 菜单建完之后、触发点击之前钉 `win32`（建菜单仍按本机平台）；断言的会话身份是 exe 名 |
| `tests/test_music_player_cache.py` | +3 | 同上，沿用同文件既有的 `monkeypatch.setattr(sys, "platform", "win32")` 写法 |
| `pet/music/settings.py` | +3 / −1 | mac 口径「显示歌词」说明的播放器举例去掉「QQ音乐」（「等」已涵盖），并留注释说明原因。不走 `_ROW_WHITELIST`：`music_lyric` 行三个平台都有，而 `QQ` 只在 mac 口径里，登记后防腐烂用例会在 Windows / Linux 判它失效 |
| `tests/test_settings_copy_lint.py` | +23 | 新增 `test_mac_music_hints_are_jargon_free_on_every_host`：强制 darwin 直接取「显示歌词」「音乐自动唱歌」两段说明过同一套判据，让三端 CI 都能拦下这类只在 mac 渲染的文案 |

`docs/screenshots/mac-music-2026-11-08/page_darwin_lyrics.png` 是改文案之前截的，图里的举例比现在多「QQ音乐、」
四个字，未重截。

### 测试与验证（本轮，macOS 主机）

| 门 | 命令 | 结果 |
|---|---|---|
| 先红 | 修复前 `python scripts/ci_shards.py` | **9001 passed, 82 skipped, 9 failed**（上表 6 条 + 基线既有 3 条） |
| 先红（新用例） | 改文案前 `pytest tests/test_settings_copy_lint.py` | 2 failed：新用例 `mac 口径的「显示歌词」说明出现黑话：['QQ']` + 上表 #6 |
| 后绿（受影响 9 个文件） | `pytest -q tests/test_settings_copy_lint.py tests/test_now_playing_session_probe.py tests/test_music_player_cache.py tests/test_menu_layout.py tests/test_music_player_settings.py` + 四个 mac 新文件 | **295 passed** |
| 静态检查 | `ruff check pet/ tests/ scripts/probe_mac_now_playing.py`；`git diff --check` | All checks passed |
| 全量 | `python scripts/ci_shards.py`（四片合计 644 s） | **9008 passed, 82 skipped, 3 failed** |
| 满载 3 遍 | 12 个忙循环占满 12 核，同时跑四个 mac 新文件 + `test_now_playing_session_probe.py` + `test_music_player_cache.py` + `test_settings_copy_lint.py` ×3 | 3 次均 **171 passed**（各约 6.3 s） |

**剩下的 3 条失败是基线既有的**：`test_balance.py` ×2、`test_memory_recall.py::test_manual_pin_remember_topic_unarchives`，
在未改动的上游 `main`（`34d810b`）独立 worktree 里单独复跑同样失败（即 §六登记的 6 条里与字体度量无关的那 3 条；
另外 3 条字体度量相关的在本机不失败）。

**如实登记的一次挂起**：修复后那轮全量里，第 3 片的 pytest 进程在 2370 条全部通过、打印完汇总之后**没有退出**，
以约 95% CPU 自旋了两个多小时。`sample` 取样显示主线程在 `exit()` 的析构阶段：
`onnxruntime::OneDsTelemetry::Shutdown → LogManagerImpl::FlushAndTeardown → TransmissionPolicyManager::handleStop`
循环 `cthread_yield`。手动结束该进程后第 4 片照常跑完。修复前那轮的第 3 片正常退出，这次挂起只出现一次、
未复现；本 PR 不引用 onnxruntime，成因没有进一步排查。
