# 歌词对齐（`music_lyric_align`）PR 报告

> **基线**：`56f8ad2`（Merge PR #173 from klxxya/fix/issue146-consolidated）
> **分支**：`feat/music-lyric-align`　**日期**：2026-09-22
> **范围**：12 个文件（实现 7、测试 3、基准脚本 1、本报告 1）
> **上游文档**：[`PR-REPORT-music-lyric-2026-09-16.md`](PR-REPORT-music-lyric-2026-09-16.md)（歌词显示本体）
> **结构**：按 [`DEV-HANDOVER.md`](DEV-HANDOVER.md) §8.3，并落实「修改文件说明 / 性能分析 / 实机运行记录」三段硬要求。

---

## 一、核心特性

用户反馈：**「歌词识别不到我快进了，也识别不到我从一半开始播的歌」**。

根因不是歌词本身，而是**位置来源**：网易云音乐通过 SMTC 完全不上报播放进度，歌词只能按本地时钟从「检测到切歌的时刻」起累加。本地时钟在原理上感知不到用户的拖动，所以：

1. 快进一次 → 歌词**永久**错位，且没有任何自动信号可纠正；
2. 从歌的中间开始播 → 歌词从第一句开始念（或干脆空白），因为基准被设成了 0。

本 PR 给出一个**一次点击就能对准**的入口，并顺手修掉三处相关缺陷：

| # | 能力 | 说明 |
|---|---|---|
| 1 | 手动对齐菜单 | 右键「音乐 → 歌词对齐」：回到开头（现在这句算开头）/ 上一句 / 下一句 / 后退 5 秒 / 前进 5 秒 |
| 2 | 半途起播不再静默 | 启动时歌已在播且无进度上报：先报歌名（不再因「跳过首句」提前 return 而什么都不显示），基准置 0 并给一次性提示「歌词按开始时间估算，进度可能不准，可用『歌词对齐』校正」 |
| 3 | 倒退到首句之前不再残留 | 新增 `LyricTracker.line_now()`：`advance()` 用 `-1` 同时表示「无行可显」和「与上拍相同」，调用方在倒退场景下分不清，只能沿用错位旧歌词 |
| 4 | 多会话不再乱跳 | `_pick_playing_session` 四条优先级；修复「全部暂停时歌词莫名跳到浏览器会话」 |

**红线**：手动对齐只作用于**本地累加模式**。播放器一旦上报真实进度（Chrome / QQ 音乐），对齐入口在菜单里置灰，且 `position(reported=...)` 会直接覆盖手动基准——绝不与真值打架。

---

## 二、修改文件说明

### 实现（7 个文件，+399 / −32）

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/now_playing.py` | +66 / −17 | `Playback` 增 `app_id`（会话 `source_app_user_model_id`）；新增 `_session_status` / `_session_app_id` / `_pick_playing_session`（四条优先级：受跟踪且在播 > 任一在播 > 全部暂停时受跟踪 > 首个会话）→ 供「粘住当前播放器」；`get_now_playing(tracked_app_id=None)` 起贯通 `tracked_app_id` |
| `pet/music_lyric_controller.py` | +173 / −11 | `LyricTracker` 增 `uses_reported_position` 只读属性、`reanchor(position,*,now)`、`reanchor_to_line(delta,*,now)→bool`（先扣 `lead` 再落基准，保证按一次走一句）、`line_now(now,*,reported=None)→int`（返回裸下标含 `-1` 并同步 `_index`）；**`advance()` 逐字未动**。控制器增 `_tracked_app_id` / `_estimated_hint_shown` 与 `align_available` / `resync_to_start` / `resync_to_line` / `nudge` / `_resync` / `_refresh_lyric_now` / `_maybe_hint_estimated_progress`；`_start_track` 把 `_announce()` 提到跳过判断之前（去掉那个提前 `return`）；`_on_playback_ready` 改用 `line_now` 并记录 `_tracked_app_id`；`_sample_loop` 回传 `self._tracked_app_id` |
| `pet/context_menus/shared.py` | +50 / −0 | 新增 `_align_lyric(pet, kind)`（路由 start/prev/next/back5/fwd5；**故意不走 `_run_off_main`**——纯内存微秒级操作，留在 GUI 线程）与 `add_music_lyric_align(menu, pet, *, icons=True)` 构建子菜单 |
| `pet/context_menus/registry.py` | +25 / −1 | 注册 `music_lyric_align`：标签「歌词对齐」、图标 `play`、`enabled=_music_align_ready`、`disabled_reason="当前不需要手动对齐（播放器会上报进度，或还没有歌词）"`。`_music_align_ready` **必须 isinstance 校验**（宿主 `__getattr__` 兜底会返回任意对象，只判 `None` 会把测试替身当控制器） |
| `pet/menu_templates/modern-default-v1.json` | +1 / −0 | 模板 `music` 子菜单在 `music_quit` 之后插入 `{"type": "action", "id": "music_lyric_align", "visible": true}` |
| `pet/modern_settings_dialog.py` | +1 / −1 | 「显示歌词」行说明补上「网易云不上报进度 → 用右键菜单『音乐 → 歌词对齐』校正」，让用户在设置页就能知道入口在哪 |
| `README.md` | +83 / −2 | 新增「v4.2.0 以来的变更」章节（新增功能 / 关键修复 / 回退 / 工程测试文档）与目录项，重写版本块引用与「当前状态」条目，并给「最近修复与变更记录」加完整性说明 |

### 测试（3 个文件，+664 / −1）

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_now_playing_session.py` | **新增** +224 | 14 例：伪造 WinRT 会话/管理器，覆盖「无 timeline → `position is None`」「有 timeline → 真实位置」「`app_id` 透出」「四条会话优先级逐条」「陈旧 tracked id」「`get_now_playing(tracked)`」 |
| `tests/test_music_lyric.py` | +345 / −1 | 17 例：`reanchor` 三态（移动基准 / 暂停中冻结位置同步 / 不与上报位置打架）、`line_now` 含 `-1` 与同步语义、倒退到首句之前清残影、`resync_to_line` 恰好一句（含 lead）、`nudge`、上报位置时 `_resync` 为 no-op、`align_available` 门禁、首曲半途起播报歌名并取词、估算提示只出一次、无歌词时不动作 |
| `tests/test_menu_layout.py` | +95 / −0 | `registered` 集合补 `music_lyric_align`；`test_default_layout_populates_real_qmenu_hierarchy` 增「歌词对齐」子菜单层级断言；新增 `test_music_align_ready_and_callback_routing`（可用性门禁 + 五个回调路由） |

### 工具（1 个文件，新增）

| 文件 | 用途 |
|---|---|
| `scripts/bench_music_lyric_align.py`（174 行） | 性能与实机采样的可复现入口（见第四、五节）。`--no-smtc` 可在无播放器/CI 下跑纯离线部分 |

**未改动**：`assets/characters/shenshen/videos/manifest.json`、`pet/catalog.py` 等 worktree 中既有的用户改动一律保持原样；`advance()` 与既有本地时钟用例的语义未变。

---

## 三、实现要点

- **一个 id 挂子菜单**：与 `add_harness` 同一手法——模板与用户自己编排过的布局里只出现 `music_lyric_align` 这一个 id，子菜单项在 builder 内部生成，**老用户布局无需迁移**（`menu_layout._merge_future_default_actions` 在内存里合并新模板 id，不写配置）。
- **对齐手柄是「行」不是「秒」**：用户听到的是「现在唱这句」，按行步进一次点击就到位；±5 秒留给最后一点偏差。
- **`reanchor_to_line` 先减 `lead`**：查行时会叠加歌词提前量，若直接拿 `lines[target].at` 当基准，行距短于提前量（快歌/说唱）时按一次会跳两句。
- **`line_now` 与 `advance` 分工**：`advance` 保留「`-1` = 无需重绘」的去重协议（1 Hz 采样路径继续用它，零改动）；需要「当前究竟是哪一句」的调用方改用 `line_now`。
- **位置来源单向降级**：`uses_reported_position` 一旦为真就不再走手动基准；菜单项同时置灰并给出原因，避免用户按了没反应。

---

## 四、性能分析

**方法（可复现）**：`python scripts/bench_music_lyric_align.py`（Python 3.11.1 / Windows，`--lines 71` = 实测样本中的最大歌词行数，`--iters 200000`）。脚本已入库，任何提交者可用同一条命令复测。

| 指标 | 实测 | 归属 |
|---|---|---|
| `LyricTracker.position()` | **104.2 ns/op** | 热路径（每秒一拍） |
| `LyricTracker.line_at()` | **621.9 ns/op** | 热路径 |
| `LyricTracker.advance()` | **641.1 ns/op** | 热路径（1 Hz 采样线程唯一每拍入口；**本次逐字未动**） |
| `LyricTracker.line_now()` | **703.5 ns/op** | 新增 |
| `LyricTracker.reanchor_to_line()` | **196.1 ns/op** | 新增（菜单点击时一次） |
| `get_now_playing()`（跨进程 SMTC） | **median 4.976 ms**（min 4.299 / max 75.369，n=12） | 既有，每秒一次 |
| `resolve_menu_layout()`（42 个 action id） | **0.2628 ms/op** | 既有，菜单展开一次 |

**结论**

1. **稳态开销零增加**：1 Hz 采样线程每拍只调 `advance()`（641 ns），新增方法**不在**这条路径上——`reanchor*` / `line_now` 只由菜单回调触发。按 641 ns/秒折算约 **0.000064 % 单核**，与既有 `get_now_playing()` 的 4.976 ms/秒（≈0.5 % 单核）相比可忽略。
2. **对手动操作是「瞬时、常量级」**：单次对齐 ≈ 0.2–0.7 µs，用户感知不到；且 `_align_lyric` 刻意留在 GUI 线程（不排线程池、不碰 WinRT），不引入新的跨线程时序面。
3. **WinRT 调用次数不变**：新增逻辑不额外调用 SMTC（`tracked_app_id` 只是把上一次的 `app_id` 带回同一次采样），网络与磁盘访问为 0。
4. **菜单解析 +1 项**：`resolve_menu_layout` 0.2628 ms / 42 个 id，与加项前同量级（单次解析，展开时一次），可忽略。
5. **内存**：`LyricTracker` 新增 2 个标量字段（`_reported_position: bool`、以及控制器侧 `_tracked_app_id: str` / `_estimated_hint_shown: bool`），无新增容器、无缓存增长。

---

## 五、实机运行记录

以下全部在**本机 Windows 桌面**上实测（`2026-09-21 ~ 09-22`），不是模拟环境。

### 5.1 根因复现（本轮实测，可复现）

```
$ python scripts/bench_music_lyric_align.py
[bench] get_now_playing : 4.976 ms/op (min 4.299 / max 75.369, n=12)
{"now_playing": {"observed": {"title": "枫", "artist": "周杰伦", "playing": true,
  "app_id": "cloudmusic.exe", "position": null, "duration": 0.0}}}
```

网易云正在播放《枫》，SMTC 给出的 `position` 为 **`null`**、`duration` 为 **0.0**——即「不上报进度」的根因在报告写作时刻仍然成立，这是本 PR 存在的前提。

同轮实测的横向对照：**Chrome** 播放同一首歌时上报完整时间线，且拖动进度条后 `position` 肉眼可见地跳变（1495.9 → 1591.9 → 1821.2 → 2079.7 秒）；网易云在「播放 / 暂停 / 切歌 / 快进」四种操作下、4 首不同歌曲上，`position` 恒为 `null`（另在早期探针中直接观察到 `position=0.000 end=0.000 last_updated=1601-01-01`）。

### 5.2 「能不能自动识别快进」的可行性排查（结论：不能，故走手动对齐）

为避免「本该自动却做了手动」的将就，本轮专门排查了网易云**桌面歌词**窗口能否当 oracle：

| 探针 | 结果 |
|---|---|
| `GetWindowTextW(DesktopLyrics)` | 只返回窗口标题 `桌面歌词`，无歌词文本 |
| UI Automation | 单个 `ControlType.Pane`（class `DesktopLyrics`），**后代 0 个**，RawViewWalker 首子节点为空 |
| 窗口样式 / 子窗口 | `exstyle=0x00080088`（WS_EX_LAYERED\|WS_EX_TOPMOST\|WS_EX_TOOLWINDOW），**无任何子窗口** |
| 像素 | 截图 770×117 成功（916 种颜色，6.4 % 像素与背景差异 > 60）→ 有内容，但**只是像素** |
| 网易云落盘状态 | 20 分钟内被修改的文件仅 APM/BI 日志、434 MB `webdb.dat`、CEF Local Storage——无当前行/位置状态 |

文字是自绘到分层表面上的像素，唯一读取途径是 OCR（新增大依赖 + 中文 OCR + 卡拉 OK 渐变样式 + 逐行延迟），本 PR 明确不引入。**因此「手动一次点击对准」是这个能力当前唯一诚实的实现**，而不是折中。

### 5.3 功能实机确认

- 用户在本机开启歌词显示 + 网易云桌面歌词后确认：**「现在功能正常」**（2026-09-21）。
- 菜单可用性门禁实机行为：网易云在播时「音乐 → 歌词对齐」**可用**；切到 Chrome 上报进度的场景时该项**置灰**并显示原因。
- 边界实机核对：从歌的中间起播 → 先出歌名与估算提示，随后用「回到开头（现在这句算开头）」一次点击把歌词对到当前句；快进后再按一次同样一次到位。

---

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed**（含新增脚本） |
| 聚焦 | `python -m pytest -q tests/test_music_lyric.py tests/test_now_playing_session.py tests/test_menu_layout.py tests/test_voice_chime_service.py` | **197 passed** in 21.67s |
| 受影响时序族（满载 3 遍） | 3 个 `pytest -q tests/test_music_lyric.py tests/test_now_playing_session.py tests/test_menu_layout.py` **并发**运行（本地 CPU 打满） | **3/3 全绿**：各 `160 passed`，18.76 / 18.78 / 18.81 s |
| 全量 | `python -m pytest -q` | **2768 passed, 11 skipped, 0 failed** in 356.43s |
| 断言有效性 | 把 `reanchor_to_line` 里的 `- self.lead` 临时去掉后重跑 `test_resync_to_line_lands_exactly_one_line_with_lead` | **失败**：`AssertionError: ('我在唱《t》', 'C') == ('我在唱《t》', 'B')` —— 证明该断言可区分新旧实现（旧实现按一次跳两句），不是恒真断言；改回后用例恢复绿 |
| 架构红线 | `tests/test_architecture.py`（含 README 禁用词、行数预算）随全量套件通过 | 绿 |

**3 遍结果**：3/3 全绿（并发 3 进程，每轮 `160 passed`，无一用例失败）。

---

## 七、已知限制与后续

1. **对齐是手动的**：网易云不上报进度且无可读 oracle（见 5.2）。若日后播放器开放进度上报，`_music_align_ready` 会自动置灰该项——无需改代码。
2. **不含自动 seeking 校正**：没有「检测到用户拖了进度条」的信号可用（SMTC 不给、桌面歌词不可读），本 PR 不猜。
3. **酷狗等仍需在播放器设置里手动开启系统媒体控制**，否则连曲目都读不到，与本 PR 无关（既有行为，设置页文案已说明）。
4. **`_estimated_hint_shown` 每会话一次**：一次性提示不重复打扰；如果用户想要「每次都提示」，可后续加开关。

---

## 八、风险与回滚

- **影响面**：歌词气泡（`pet/music_lyric_controller.py`、`pet/now_playing.py`）与右键菜单音乐子菜单；不触碰动画、碰撞、IPC、打包链路。
- **开关**：`music_lyric_enabled` 关闭即整条链路不进菜单、不采样；新增的菜单项本身受 `_music_lyric_configured`（同一开关）与 `_music_align_ready`（能力门禁）双重约束。
- **配置迁移**：**无新增配置键**，老 `config.json` 与老菜单布局均无需迁移。
- **回滚**：单提交分支，`git revert` 一次即可；回滚后歌词回到「快进后错位」的旧行为，无残留状态、无落盘数据依赖。
