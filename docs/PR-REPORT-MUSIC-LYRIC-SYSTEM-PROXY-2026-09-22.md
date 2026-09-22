# PR 报告：歌词取词被系统代理拖死 + 网易云「歌词对齐」被误关（2026-09-22）

> **基线**：`0983706`（Merge PR #180，main 当前 tip）
> **分支**：`fix/music-lyric-system-proxy`　**日期**：2026-09-22
> **范围**：3 个代码/测试文件（实现 2、测试 1），另有本报告与 INDEX 登记
> **关联**：[`PR-REPORT-music-lyric-align-2026-09-22.md`](PR-REPORT-music-lyric-align-2026-09-22.md)、
> [`PR-REPORT-music-lyric-2026-09-16.md`](PR-REPORT-music-lyric-2026-09-16.md)、
> [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)（代理/VPN 影响面清单）

## 一、核心特性

用户反馈「合并前源码版桌宠还能识别网易云的歌词和快进进度，合并后不行了」。
排查结论是**两个独立缺陷**，都不是合并引入的：

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 只有缓存过的歌显示歌词，未缓存曲目一律「没歌词」 | 歌词请求走 `urllib.request.urlopen`，**继承系统代理**（用户开着全局模式 VPN，`127.0.0.1:12450`）；实测三源单次 20~41 秒 > `HTTP_TIMEOUT` 8 秒 → 每次都在 9 秒死线处放弃、返回 0 行 | 歌词请求改用**显式禁用代理**的 opener（`ProxyHandler({})`），并把失败原因由 `log.debug` 升到 `log.warning` |
| 2 | 拖完进度条后「音乐 → 歌词对齐」点不动（用户说的"快进进度不行"） | 网易云不上报 SMTC 进度，`_on_lyrics_ready` 给的是**本地估算**位置，却被 `LyricTracker.load` 按 `position is not None` 当成"播放器上报的真值" → `align_available()` 返回 False → 整个子菜单置灰（而控制器刚弹过提示让他去点这个菜单） | `load()` 新增 `reported` 参数，生产路径显式传 `reported=reported is not None`；估算值不再冒充真值 |

**红线 / 不变量**：

- 真上报进度的播放器（QQ 音乐 / Chrome）**不得**被允许手动对齐——位置听真值，
  `test_reported_position_still_disables_align` 钉住这一条。
- 取词仍是「缓存 → 三源并发 → 优先级择优」，失败仍返回 `None` 且**不自动重试**
  （`_no_lyric_keys` 语义不变），不给后台加重复流量。
- 歌词仍不下载音频、不加密、不碰用户收听历史。

## 二、修改文件说明

`git diff --numstat`（`0983706` → 分支）：

| 文件 | 增删 |
|---|---|
| `pet/music_lyric.py` | +68 / −3 |
| `pet/music_lyric_controller.py` | +22 / −5 |
| `tests/test_music_lyric.py` | +153 / −0 |

合计 3 文件 +243 / −8；无新增/删除文件（报告与 INDEX 登记另行计入）。

### 实现

**`pet/music_lyric.py`（+68 / −3）**

- 新增 `_proxy_bypass_logged`、`_host_of(url)`、`_build_opener()`、`_note_proxy_bypass_once()`、
  `_open_direct(request, *, timeout)`；`_http_get_json` 改走 `_open_direct`。
  为什么：`urllib.request.urlopen` 会自动套用系统代理（Windows 注册表 / 环境变量），
  而这三个源都是公开接口、其中两个是国内域名，走代理只会多绕一段境外路。
- 失败分支 `log.debug("歌词请求失败: %s", url)` → `log.warning("歌词请求失败 %s（%.2fs）: %s: %s", 主机, 耗时, 异常类型, 异常)`。
  为什么：应用的 `logging.basicConfig(level=INFO)` 会吃掉 DEBUG，于是线上日志里
  只剩控制器的「0行, 耗时 9.00s」——根因被静默吞掉，这次排查全靠翻用户数据目录的日志。
- 检测到系统代理时记**一行** INFO（`_proxy_bypass_logged` 保证每进程一次，避免每首 5 行噪声），
  写明被绕过的代理地址。
- 模块 docstring 增补「网络边界：歌词请求一律直连，不走系统代理」及事故出处。

**`pet/music_lyric_controller.py`（+22 / −5）**

- `LyricTracker.load(lines, *, now, position, reported=None)`：新增 `reported` 参数，
  `_reported_position = (position is not None) if reported is None else bool(reported)`；
  默认 `None` 保持旧口径，既有内部/单测调用点行为不变。
- `_on_lyrics_ready`：`self._tracker.load(..., reported=reported is not None)`。
  为什么：无进度时这里传的 `position = now - detected_at` 是**估算值**，旧的
  `position is not None` 判定把它当真值 → `align_available()` 关掉手动对齐，
  网易云用户失去唯一的纠错手段。
- `_reported_position` 与 `uses_reported_position` 的注释/docstring 同步说明
  「估算值不算真值」。

### 测试（`tests/test_music_lyric.py`，+153 / −0）

全部离线（打桩 `urllib.request.build_opener` / `getproxies`，不外发请求）：

| 用例 | 覆盖 | 修复前的表现 |
|---|---|---|
| `test_lyric_request_bypasses_system_proxy` | 歌词请求必须自建直连 opener，且带 `ProxyHandler({})`、超时仍是 `HTTP_TIMEOUT` | 红：`_http_get_json` 返回 None（走的是会继承代理的 `urlopen`） |
| `test_lyric_request_failure_is_logged_at_warning` | 失败必须落在 WARNING 且带主机 + 异常类型 | 红：`caplog` 里没有任何 ≥WARNING 记录 |
| `test_bypassing_system_proxy_is_logged_once` | 「绕过系统代理」只记一次、且含代理地址 | 红：`_proxy_bypass_logged` 不存在 |
| `test_align_available_after_net_ease_lyrics_arrive` | **走生产路径**（`_on_playback_ready → _on_lyrics_ready`）后，网易云必须能手动对齐，且 `resync_to_line` / `nudge` 可用 | 红：`align_available()` 返回 False（对齐菜单整组置灰） |
| `test_reported_position_still_disables_align` | 反面：真上报进度时仍不许对齐、位置听真值 | 绿（护栏） |

> 第 4 条为什么要单独写：既有用例都用 `_tracker.load(position=None)` 直接建状态，
> **绕过了生产路径**，所以缺陷 2 在旧实现下是全绿的——这正是它溜进 main 的原因。

### 未改动（故意）

- `pet/voice_chime_service.py`（edge-tts 合成）、`pet/self_talk_voice.py`（本机 CosyVoice 预缓存）、
  `pet/click_sound.py`（音效，且它**不联网**）、`pet/balance.py`、`pet/updater.py`、
  `pet/vision.py` 的代理行为**一律不动**：实测更新清单只有走代理才通、TTS 端点直连也
  可用，所以"一刀切绕过代理"会踩坏别的功能（逐条数字见
  [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)）。
- `pet/music_lyric.py` 的三源并发与死线逻辑（`_PRIORITY_GRACE` / `HTTP_TIMEOUT + 1`）不变，
  失败路径仍然是 9 秒，不引入第二遍请求。
- 未新增任何持久设置（否则要触发 `docs/SETTINGS-CHANGE-GATES.md` 的设置页/白名单/迁移工作量）。

## 三、性能分析

命令与样本：本机 Windows，系统代理 `http://127.0.0.1:12450`（监听进程 `core`，
启动于 2026-09-22 18:37:41），`E:\Program Files (x86)\Dev-Cpp\python.exe`（CPython 3.11.1），
曲目 `讨厌红楼梦 - 陶喆`（未缓存）。

**① 修复前后同一条生产调用（`fetch_lyrics(..., use_cache=False)`，代理保持开启）**

| 版本 | 耗时 | 结果 |
|---|---|---|
| 修复前（`origin/main`） | **9.00s** | `None`（0 行） |
| 修复后（本分支） | **1.27s** | 62 行（首句 `讨厌红楼梦 - 陶喆`） |
| 修复后（第二次，命中磁盘缓存） | **0.02s** | 62 行 |

**② 单源对照（同一时刻、同一台机、代理开启）**

| 源 | 走系统代理 | 直连 |
|---|---|---|
| `c.y.qq.com`（QQ音乐，2 次请求） | 41.28s → 39 行 | 0.58s → 39 行 |
| `lrclib.net` | 22.14s → 35 行 | 0.83s / 3.47s / 1.28s → 35 行 |
| `music.163.com`（兜底） | 20.39s | 0.25s |

**③ 逐条回答纪律要求的四问**

- **稳态开销**：新增路径只在**取词时**执行（切歌 / 首次取词，一首一次），
  发起 HTTP 的条数与修复前完全相同（最多 3 源 × 1~2 请求）。
  新增计算成本 = `build_opener(ProxyHandler({}))` 实测 **122µs/次**
  （对照 `build_opener()` 208µs/次），单曲最多 5 次 ≈ **0.6ms**，
  相对 1 秒级的网络往返可忽略。空闲时（无歌 / 未播放）零调用。
- **新增路径成本与触发频率**：`_note_proxy_bypass_once()` 每进程只可能真正写日志一次
  （模块级布尔短路）；`log.warning` 仅在请求异常时触发，正常情况下零日志。
  网络出口从"系统代理"改为"直连"本身**降低**了往返耗时（上表②）。
- **有无新的系统调用 / 网络 / 磁盘 / 线程**：无。opener 对象在进程内构造，
  不建新线程、不落盘、不改注册表；`getproxies()` 读注册表只在**每进程首次**取词时发生一次。
- **内存有无增长**：无长期增长。每个请求构造的 `OpenerDirector` 随请求结束被回收
  （约 10 个 handler 对象、KB 级、GC 即归零）；模块级只多存 1 个布尔量。

**④ 门禁**

| 门 | 结果 |
|---|---|
| `python -m ruff check pet/ tests/ scripts/` | All checks passed |
| `pytest tests/test_music_lyric.py tests/test_menu_layout.py tests/test_now_playing_session.py` | **165 passed** |
| `pytest -q`（全量，`QT_QPA_PLATFORM=offscreen`） | **2866 passed / 11 skipped / 0 failed**，207.02s |
| 收集用例数 | 基线（`origin/main`）2870 → 本分支 **2877**（+5 新用例、+2 为报告参数的 `test_pr_report_discipline` 新增实例） |
| `git diff --check` | clean |

## 四、实机运行记录

**① 用户数据目录里的现场日志（修复前，`%APPDATA%\dsh-pet-standalone\pet-28844.log`）**

```
23:12:09 歌词取词完成: 周杰伦 - 晴天 -> 63行, 耗时 0.01s        ← 命中缓存
23:14:13 歌词取词完成: 周杰伦 - 说了再见 -> 0行, 耗时 9.00s      ← 三源全超时
23:14:48 歌词取词完成: 陶喆 - 飞机场的10:30 -> 0行, 耗时 9.00s
23:15:43 歌词取词完成: 陶喆 - 讨厌红楼梦 -> 0行, 耗时 9.00s
23:16:26 alert enqueue alertType=music_lyric alertId=music-lyric-estimated
```

同机更早（代理启动前的 18:12:14）：`周杰伦 - 晴天 -> 63行, 耗时 0.98s`。
`lyrics_cache/` 里最后一次成功落盘也是 18:12 —— 与代理进程启动时间（18:37:41）对得上。

**② 真实会话 A/B（本机正在播放的网易云，代理保持开启）**

采样用真的 `now_playing.get_now_playing()`（真 WinRT SMTC），取词用真的 `fetch_lyrics`：

```
真实会话: 蝴蝶 - 陶喆, position=None, app_id='cloudmusic.exe'
修复前（main 工作树）  取词: 9.01s -> None 行
修复后（本分支）       取词: 0.88s -> 53 行, 首句='蝴蝶 - 陶喆 (David Zee Tao)'
```

同一会话、同一首歌、同一时刻，只差这一次改动；`position=None` 也在真实会话上
再次印证「网易云不上报播放进度」。

**③ 控制器级端到端（真 SMTC + 真网络 + 真 Qt 事件循环）**

真 `now_playing.get_now_playing()` → 真 `MusicLyricController` → 真 `fetch_lyrics`
→ 真 `QApplication` 事件循环；只有「气泡出口」是替身（它属于 UI 层，用例里本来
就用替身，见 `tests/test_music_lyric.py` 的 `_FakeWin`）。命令（工作树内，
`QT_QPA_PLATFORM=offscreen`，系统代理保持开启）：

```
[1] 真实 SMTC 采样: Playback(track=Track(title='蝴蝶', artist='陶喆', ...),
                     position=None, app_id='cloudmusic.exe')
  一次性提示: 这个播放器不上报播放进度，歌词按开始时间估算。快进或从中途开始播放后，用右键菜单「音乐 → 歌词对齐」校正。
  气泡 None | '我在唱《蝴蝶》'
  气泡 '我在唱《蝴蝶》' | '曲：Brad Olynyk/陶喆'
[2] 歌词已装载: True（has_lyrics=True）
[4] align_available=True（修复前为 False → 菜单置灰）
  气泡 '我在唱《蝴蝶》' | '当这世界已经准备将我遗弃'
[5] 「下一句」True，行号 2 -> 3   → 气泡 '像一个伤兵被留在孤独荒野里'
[6] 「前进5秒」True
[7] 「回到开头」True，气泡回到 '曲：Brad Olynyk/陶喆'
[8] 位置来源: reported=False；uses_reported_position=False
```

**④ 验证边界（不能自动的部分，给探针证据）**

- **音乐不是自动播的**：`蝴蝶` 是用户自己在网易云里放的（本 Agent 不点播放键）。
  因此这条记录的窗口是本机真实播放期间；若当时没有会话，`get_now_playing()` 会返回
  `None`（探针首行就是这条判定，不是沉默跳过）。
- **真实气泡控件未参与**：气泡出口用替身（与既有用例同口径），验证的是「歌词内容
  与行号是否正确送进气泡」，不是像素渲染；渲染侧由既有 UI 用例覆盖。
- **一次性提示的显示时长/优先级**：`show_alert` 走的是替身，只断言了「提示发出且
  文案指向「音乐 → 歌词对齐」」；真实弹窗的优先级与让路行为由既有 alert 用例覆盖。

**⑤ 用户在修复前就能自救的一条路（反证根因）**

把 `*.qq.com`、`music.163.com`、`lrclib.net` 加进代理软件的绕过/直连名单（或临时关掉
系统代理），当前 main 的歌词立刻恢复——因为取词代码一行没改。

## 五、遗留与已知边界

- 歌词请求一律直连：若某用户的网络**只有代理能出网**，歌词取词会失败（其余功能不受影响）。
  取舍理由：三个源都是公开接口，直连实测 0.25~3.5 秒且与代理状态解耦；反过来
  "继承一个慢代理"会让**所有人**的歌词在代理开启时静默失效。若将来真出现这种环境，
  再按"直连失败后走代理兜底"扩展（会牺牲失败路径耗时：9s → ~18s）。
- 失败取词仍不自动重试（一首歌一次），与本模块"歌词是锦上添花"的既有取舍一致。
- 其余联网功能**本轮刻意不动**（保持"跟随系统代理"），实测依据：更新清单走
  `cdn.jsdelivr.net` **直连失败、走代理 1.45s 成功**；edge-tts 的音色列表端点
  直连 2.08s / 走代理 2.26s **都可用**——所以"全局绕过代理"会踩坏更新检查，
  而"全局走代理"会踩坏歌词。逐功能的实测清单、30 秒探针与推荐分流配置见
  [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)。
  更正：点击音效**不联网**（本地素材 + 本地转码缓存），先前草稿把它列进"同类风险面"有误。

## 六、修改文件清单（供 PR 描述引用）

```
pet/music_lyric.py                 +68 / −3    直连 opener + 失败原因进日志 + 模块 docstring
pet/music_lyric_controller.py      +22 / −5    load(reported=...) + 生产路径传 reported
tests/test_music_lyric.py         +153 / −0    5 条用例（3 条代理/日志 + 2 条对齐闸门）
README.md                           +9 / −0    变更记录一条（问题/根因/修复/验证/边界）
docs/INDEX.md                       +3 / −2    本报告登记 + 两处音乐报告「何时必读」互链
docs/PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md   新增（224 行）
```

合计：实现 +90 / −8、测试 +153 / −0、文档 +12 / −2 与 1 个新文件。
