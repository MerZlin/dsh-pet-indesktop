# 待办提醒到点语音播报（`todo_reminder_speak`）

> **基线**：`a19157f`（`main`，Merge PR #300）
> **分支**：`feat/todo-voice-broadcast`　**日期**：`2026-10-18`
> **范围**：9 个文件（实现 6、测试 3；另有 2 个 `docs/` 文档与 1 份本报告）
> **关联**：设置变更准入记录见 [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md) 的
> 2026-10-18 条目；音频通道复用关系见
> [`PR-REPORT-TTS-PROVIDERS-2026-10-14.md`](PR-REPORT-TTS-PROVIDERS-2026-10-14.md) 与
> [`PR-REPORT-DIVINATION-2026-10-17.md`](PR-REPORT-DIVINATION-2026-10-17.md)（同一条
> `_chime_wanted()` 通道生命周期缺口，本刀是第五个消费者）。

## 一、核心特性

待办提醒此前只有"气泡 / 系统通知"两档视觉提醒：桌宠不在前台（被别的窗口挡住、
最小化到托盘、或用户正在别处打字）时，一条 8 秒就散的气泡等于没提醒。本刀新增
**一个默认关闭的偏好键 `todo_reminder_speak`**：开启后每条到点的待办在冒气泡的
同时，经**语音报时服务那条进程内唯一音频通道**把同一句话念出来。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 到点念出来 | 文案与气泡**共用** `todo_reminder.fire_text()` 一个 builder，两处不会漂移 |
| 2 | 复用唯一音频通道 | `AppShell.ensure_audio_channel()` → `VoiceChimeService.speak(text, log_tag="待办提醒")`，音色 / 语速 / 音调 / 音量沿用「语音报时」的那一套（**不新增**任何语音参数键） |
| 3 | 免打扰不补播 | 免打扰（含全屏等效）拦下的那一档不出声、也不在结束后补播；通道自身 `respect_quiet=True` 也是"入队即丢弃" |
| 4 | 通道生命周期接得住 | `_chime_wanted()` 增加"待办语音"这个消费者，开关打开后通道不会被 `_sync_chime_service()` 停掉（否则就是"开关能存、点了没声"的死开关） |
| 5 | 面板不复制开关 | 待办面板底部深链文案补上"语音播报"，偏好仍单一归属设置页「自动化与联动 → 待办提醒」 |

**红线 / 不变量**：① 语音失败只能降级成"有气泡没声音"，不许影响提醒本身；② 默认关
（主动打扰型能力）；③ 条目存储、盖戳语义、打铃时刻一个字节不变；④ 不新建第二条
播放链路（叠音的结构性保证来自"进程内唯一通道"）。

## 二、修改文件说明

`git diff --numstat`（工作树 vs `a19157f`）：

```text
2	1	docs/INDEX.md
45	0	docs/SETTINGS-CHANGE-GATES.md
15	2	pet/app.py
6	0	pet/config.py
5	1	pet/modern_settings_dialog.py
3	1	pet/settings_pet_controls.py
2	2	pet/todo_panel.py
44	8	pet/todo_reminder.py
5	1	tests/test_architecture.py
3	0	tests/test_config_schema.py
104	0	tests/test_todo_reminder.py
```

新增文件：`docs/PR-REPORT-TODO-VOICE-BROADCAST-2026-10-18.md`（本报告）。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/config.py` | +6 / −0 | 新键 `todo_reminder_speak`，默认 `False`。四处注册同改：`_initial_data()`（`todo_reminder_*` 两键旁）、`reload()` 白名单、`_normalize_pet_settings()`（走 `_bool_or_default`，防 `bool("false") is True`）、`set()` 的归一化触发集。漏任一处就是"设置页有行、config 没键"或"字符串布尔被误开" |
| `pet/todo_reminder.py` | +44 / −8 | ① 纯函数 `fire_text(fire)`：气泡与语音**唯一**文案来源，`_notify_fire` 里原来那行 f-string 改为调它；② `apply_config()` 把 `todo_reminder_speak` 读进 `_prefs["speak"]`；③ 新增 `_speak_fire(fire)`，形状照抄 `festival_service._speak`（`ensure_audio_channel()` → `channel.speak(text, log_tag=...)`，取不到通道 / 播放失败只记日志）；④ `_notify_fire()` 的气泡分支与"退桌面通知"分支由两个 early-return 收成 if/elif，播报落在函数尾部——免打扰那条 early-return 在它之前，所以被免打扰拦下的提醒**不出声**；⑤ 模块与类 docstring 补偏好键与播报语义 |
| `pet/app.py` | +15 / −2 | 新增 `_todo_speak_wanted()`（`todo_reminder_enabled and todo_reminder_speak`）并接进 `_chime_wanted()`（注释里的"四者"改"五者"）。**这是本刀最容易漏的一处**：不接的话每次设置保存 `_sync_chime_service()` 都会 `stop()` 并把 `voice_chime_service` 置 `None`，用户勾了开关却一声不响 |
| `pet/settings_pet_controls.py` | +3 / −1 | `host.todo_reminder_speak_check` 控件（`ToggleSwitch`），初值读配置；注释"两键"改"三键" |
| `pet/modern_settings_dialog.py` | +5 / −1 | 「待办提醒」分区加一行 `SettingRow("todo_reminder_speak", ...)`；`claim(...)` 加第三键（不登记会掉进「待分类（开发期）」）；`_write_config()` 写回。行说明写明"音色 / 语速 / 音量在设置 → 语音"与"免打扰期间不出声、不补播" |
| `pet/todo_panel.py` | +2 / −2 | 底部深链文案与模块 docstring 补"语音播报"（面板按契约**不加**控件） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_todo_reminder.py` | +104 / −0 | 6 例（清单见 §六）：`_FakeApp` 补 `ensure_audio_channel()` + `_Channel` 假通道（照 `tests/test_festival.py` 的 `_Channel`） |
| `tests/test_config_schema.py` | +3 / −0 | 白名单快照加 1 键（探针会从 `config.py` 源码真取字面量，不加速照就红） |
| `tests/test_architecture.py` | +5 / −1 | `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 2535 → 2539（**实测** 2539 = +4 行），按该文件既有约定带日期注释说明理由，未静默放宽 |

### 未改动（故意）

- `pet/overlay_shell.py` / `overlay_window.py` / `window.py` / `self_talk_voice.py` /
  `settings_slot_config.py`：本刀不碰窗口、输入与槽路由。
- `pet/voice_chime_service.py` / `pet/tts/**`：通道与合成链一个字没改，只当消费者用。
- 条目的打铃时刻、盖戳与归档（`advance_todo_state`）：零改动。

## 三、实现要点

**为什么共用通道而不是自己放音**：`VoiceChimeService` 是进程内的**唯一**音频通道，
报时 / 节日语音 / 点击朗读 / 抽签语音都排队走它。自建播放器就会出现"报时和待办同时
出声"的叠音，也会各自维护音量与音色（第二份真值）。所以本刀只加一个**消费者**，
合成、排队、播放、免打扰全部复用。

**文案为什么必须是一个 builder**：气泡文案与语音文案分头写，早晚会漂移（改了一处忘
另一处，用户听到的和看到的不一样）。`fire_text()` 是模块级纯函数，服务里两处都调它，
用例 `test_fire_text_is_the_single_source_for_bubble_and_speech` 与
`test_tick_speaks_exactly_the_bubble_text` 从两侧钉住。

**为什么免打扰分支不补播**：被免打扰压住的提醒进的是 `window_alerts.defer_quiet` 的
暂存队列，那套语义是"结束后一次性汇报有几条提醒"（视觉）。把几天前那条**念出来**是
另一回事——过期提醒突然出声比不提醒更糟。这与通道自己的
`speak(respect_quiet=True)` 契约一致（免打扰期间入队即丢弃，不留到结束后补播）。

**为什么"桌宠不可见"时也念**：气泡分支要求 `win.isVisible()`，退到系统通知的那条路
同样念。语音是"另一个通道"，不是气泡的附属——看不到气泡恰恰是最需要出声的场景。

## 四、性能分析

**方法（可复现）**：`QT_QPA_PLATFORM=offscreen python .scratch/todo-voice/probe_perf.py`
（探针脚本放在 `.scratch/todo-voice/`——该目录被 `.gitignore` 忽略，属本机留存的
一次性基准；核心循环是"同一函数重复 n 次、5 轮取 min/median"，照 §四 的样本量复跑即可）
环境：Windows 11 / Python 3.13 / PySide6 6.11.2；每项 5 轮取 min 与 median。

| 指标 | 实测 | 归属 |
|---|---|---|
| `fire_text()` | min 0.199 µs / median 0.202 µs（n=200000×5） | 新增（每次 fire 一次） |
| `_speak_fire()` 开关关 | min 0.111 µs / median 0.112 µs（n=200000×5） | 新增（默认路径，早退） |
| `_speak_fire()` 开关开 | min 0.509 µs / median 0.512 µs（n=100000×5，通道为替身） | 新增（提交给通道，合成在既有后台线程） |
| `_on_tick()` 无 fire | min 8.028 µs / median 8.150 µs（n=20000×5） | 既有热路径（30s 一次） |

**结论**：

1. **稳态开销**：`_on_tick` 的空跑成本与改动前同量级（新增成本只在真有 fire 时发生，
   即 `fire_text` 0.2 µs + `_speak_fire` 0.1~0.5 µs）。默认关时每个 tick 的增量是
   **0**（`advance_todo_state` 之前一个字节没动）。
2. **新增路径的绝对成本与触发频率**：每条待办每天最多 2 次 fire（提前档 + 正点档），
   单次调用 < 1 µs 后即返回；真正的成本是既有的 TTS 合成（后台线程 + 缓存），本刀不
   改它。
3. **有无新的系统调用 / 网络 / 磁盘 / 线程**：无新增线程、无新增磁盘写；合成时延与
   网络往返全部是**既有**通道行为（缓存命中即零网络）。本刀自身的 IO 增量为 0。
4. **内存与缓存**：不新增缓存；复用通道的合成缓存（同一段文本重复提醒时命中同一
   key）。

## 五、实机运行记录

**真实链路探针**：`QT_QPA_PLATFORM=offscreen python .scratch/todo-voice/probe_real_chain.py`
（脚本在 `.scratch/` 下，gitignore；构建方式是"真 `AppShell` + `cfg.set(...)` 九个键 +
`_sync_chime_service()` + 存一条 10:00 的待办 + `todo_service._on_tick(now=10:06)`"，
不替身任何产品模块，配置隔离在临时目录，音量 0 所以不出声）：

```text
配置目录       : C:\Users\me\AppData\Local\Temp\todo-voice-probe-wx74if3y\dsh-pet-standalone
_chime_wanted  : True
_todo_speak    : True
通道类型       : VoiceChimeService is_running = True
可用合成后端   : ['edge']
播报文案       : ⏰ 待办提醒：开会（10:00）
tick 返回耗时  : 7.2 ms
合成产物       : [('76fdc335e3e38aa0.mp3', 19728)]
通道回到空闲   : True
免打扰期间新增 : （无）  pending = 1
收尾           : 通道已停
```

产物与文案的对应关系是**可验证的**，不是"看文件名像"：`voice_chime.cache_key()` 对
`⏰ 待办提醒：开会（10:00）` 在同一份配置下算出 `76fdc335e3e38aa0`，与落盘产物同名。
ffprobe 读数：`mp3, 24000 Hz, mono, 3.29s, 48 kb/s` —— 即这段文案被真合成出来了。

- **用户可见行为的确认**：合成 + 播放器接管（`通道回到空闲` 由忙转闲）已在本机真实
  发生；**但"耳朵里听到声音"未验证**——探针刻意把 `voice_chime_volume` 设为 0。
- **边界/失败路径**：同一探针里开免打扰再 fire 一条，缓存目录**没有**新增任何文件，
  `pending_count() == 1`（进暂存队列，不出声、不合成）。
- **一条找到的既有缺陷（非本刀引入、本刀不修）**：`_TTSWorker` 线程上没有
  `CoInitialize`，某些进程状态下 SAPI 合成会失败
  `本地语音合成失败：[WinError -2147221008] 尚未调用 CoInitialize`（本机真实链路
  探针第一版正是这条 —— 报错只影响后端，通道其它功能无恙）。最小复现与对照：
  `python .scratch/todo-voice/probe_sapi_thread.py` → `worker 线程: FAIL`；
  同一脚本加 `--warm-main`（主线程先合成一小段）→ `worker 线程: OK 100322 字节`。
  **成因未查清**（是否创建 QApplication 与结果无关），故只登记现象不下结论；修法
  大概率在 `pet/tts/sapi.py` 或 `_TTSWorker`（worker 线程里 `comtypes.CoInitialize()`），
  两处都**不在本刀文件白名单内**，故未改，留给后续单独处理。若用户选"本机 SAPI"时
  命中它，表现就是"有气泡没声音"（同报时 / 节日语音 / 抽签语音）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/ tests/` | `All checks passed!` |
| 聚焦（本任务指定 7 族） | `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:randomly tests/test_todo_reminder.py tests/test_config_schema.py tests/test_feature_gating.py tests/test_settings_reset_defaults.py tests/test_architecture.py tests/test_festival.py tests/test_voice_chime_service.py` | **376 passed** |
| 交付纪律（本报告 + INDEX 登记） | 同上再加 `tests/test_pr_report_discipline.py` | **563 passed**（376 + 187） |
| 相关设置/菜单族 | `tests/test_settings_page_guide.py tests/test_settings_and_resources.py tests/test_settings_default_marks.py tests/test_settings_ia_first_cut.py tests/test_menu_layout.py tests/test_overlay_app_hosts.py` | **192 passed** |
| 其余设置族 | `tests/test_settings_ia_first_cut.py tests/test_settings_compat_layering.py tests/test_settings_slot_config_route.py tests/test_settings_stale_form_exit.py tests/test_settings_pet_tabs.py tests/test_settings_cross_screen.py tests/test_settings_copy_lint.py tests/test_settings_scope_notes.py tests/test_settings_interaction_tabs.py` | **143 passed** |
| 空白/换行 | `git diff --check` | 干净 |

**全量 `python -m pytest -q` 未跑**：本刀是"一个默认关的新键 + 一行设置项"，但确实
动了共享的 `config.py` 与 `app.py` 的通道门控，按 `AGENTS.md` 的变更纪律应由合入方
补一次全量；本轮按任务给定的门禁清单只跑到上表这几族（行间有重叠用例——第三、四行
都含 `tests/test_settings_ia_first_cut.py`，故不做简单相加）。

新增 6 例（`tests/test_todo_reminder.py`）：

1. `test_fire_text_is_the_single_source_for_bubble_and_speech` —— builder 文案逐字；
2. `test_tick_stays_silent_when_speak_is_off` —— 默认关：有气泡、`spoken == []`；
3. `test_tick_speaks_exactly_the_bubble_text` —— 开：`spoken == win.bubbles`；
4. `test_quiet_mode_neither_speaks_nor_catches_up` —— 免打扰期间不出声、结束后不补播；
5. `test_todo_speak_pref_default_off_and_string_false_stays_off` —— 默认 `False`、
   `"false"` 不许当真、round trip；
6. `test_settings_save_keeps_the_audio_channel_wanted` —— 设置页保存后
   `_chime_wanted()` 为真、关掉后为假（防死开关）。

**先红后绿（真实输出）**：产品代码改动前（已加 `fire_text` 并让 `_notify_fire` 用它，
但还没有播报能力）跑上述 6 例：

```text
FAILED tests/test_todo_reminder.py::test_tick_speaks_exactly_the_bubble_text
FAILED tests/test_todo_reminder.py::test_todo_speak_pref_default_off_and_string_false_stays_off
FAILED tests/test_todo_reminder.py::test_settings_save_keeps_the_audio_channel_wanted
3 failed, 3 passed, 45 deselected
（原始日志：.scratch/todo-voice/red-before.txt）
```

三条分别卡在：语音没播出（`Right contains one more item`）、`cfg.get("todo_reminder_speak")`
返回 `None`（键没注册）、设置页找不到 `settingRow_todo_reminder_speak`（行没加）。
另外 3 条（builder 文案、默认关静音、免打扰）在改动前就绿——它们是"默认关"这一侧的
护栏，不承担红证据。改动后同样 6 例：`6 passed, 45 deselected`。

## 七、已知限制与后续

1. **"真的听见"未验证**：实机探针音量 0（不出声），只验证到"合成产出 + 播放器接管"。
   要验可听性得在本机放开音量，本轮没有做。
2. **SAPI 后端在某些进程状态下会在合成线程上失败**（既有缺陷，见 §五末；是否创建
   QApplication 与结果无关，`--warm-main` 对照可解）。用户选"本机 SAPI"时，
   待办语音与报时 / 节日语音 / 抽签语音一样是"有气泡没声音"，日志里有
   `sapi_missing`。修法在 `pet/tts/sapi.py` 或 `_TTSWorker`（需在 worker 线程里
   `comtypes.CoInitialize()`），**不在本刀文件白名单内**，故未改。
3. **macOS / Linux 未跑**：三端共享同一配置键与同一条通道，但本轮真实运行只在
   Windows 11 上做过。
4. **语音与气泡严格同源**：如果以后要给语音单独做"口语化文案"（例如把
   `10:00` 念成"十点整"），就必须拆 builder 并补一条"两处仍同步"的用例；本刀刻意
   不做这种分化。

## 八、风险与回滚

- **影响面**：待办提醒的提醒通道选择（新增一条出声路径）+ 设置页一行 + 通道生命周期
  的一个布尔条件。默认关 ⇒ 升级用户可见行为**逐字不变**。
- **新配置键**：`todo_reminder_speak`（默认 `False`）。老配置没有它 → 取默认关，
  无迁移动作；回滚（`git revert`）后该键留在磁盘上，`reload()` 白名单不再列它 ⇒ 被
  静默忽略，不会让配置加载失败。
- **回滚步骤**：`git revert` 本提交即回到"只有气泡/系统通知"的状态；不需要清缓存
  或改档案（通道缓存的音频按文本 key 存在，回滚后只是不再产生新的）。
