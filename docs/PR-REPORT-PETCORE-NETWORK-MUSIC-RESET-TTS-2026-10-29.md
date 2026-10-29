# PR 报告：对话网络重试 + 音乐暂停收词 + 重置设置移植 + 语音出声（2026-10-29）

主题：petcore 版四个用户可见缺陷/功能，一次交付。

| 用户报告 | 根因 | 结果 |
|---|---|---|
| AI 对话偶发「网络不可达」，下一轮又正常 | 瞬时传输失败没有重试；`_force_direct` 永不复位 | 零输出时自动重发一次；路由结论 300s 过期 |
| 「看看屏幕」气泡显示英文 `network` | `ai.chat` 的 reason→文案表只映射了 `internal` | 六类中文文案，且不重复拼 |
| 音乐暂停后歌词还在换 | 同一台播放器的残留 Active 会话被窗口源判成在播 | 同源排除 + 暂停边沿立刻收气泡 |
| 点桌宠/自言自语有气泡没声音 | `SpeechRequest.volume=None` 被 `float()` 抛 TypeError，`on_error` 为空 | 播放音量取 `SpeechResult.volume` |
| 要 4.4.0 的两个重置按钮 | petcore 完全没有重置能力 | `petcore/settings_reset.py` + 侧栏两按钮 |

## 修改文件说明

`git diff --numstat`（相对工作区 HEAD；两个新文件按 `wc -l` 计）：

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `petcore/api/chat.py` | +113/−28 | `_Progress` 记录「是否已经吐过正文/思维链」；正式请求抽成 `_stream_once()`，`stream_chat` 在外层最多 2 次尝试（仅 `network`/`tls` + 零输出 + 未取消）；新增 `_open_response()` 给 planner 与正式请求共用打开阶段重试；`HTTPError` 立刻按状态码分类（不重试）。 |
| `petcore/net/http.py` | +18/−5 | `_force_direct` 加 TTL（`DEFAULT_DIRECT_PIN_SECONDS=300`）；`force_direct()` 过期返回 False、`reset_transport()` 同时清两个量。一次加速器抖动不再把进程永久钉在直连。 |
| `petcore/ui/ports.py` | +14/−3 | `CoreApiPort.stream_chat(timeout=None)`：留空时取 `api.json` 的 `metadata.timeout`（钳 5–300s），设置页那一行真的生效；`resolve()` 只调一次。 |
| `petcore/contracts/api.py` | +1/−1 | `ApiPort.stream_chat` 的 `timeout` 默认值改 `None`（与实现同口径）。 |
| `petcore/contracts/host.py` | +7 | `PresentationPort.hide(owner="")`：只收自己持有会话时的气泡。 |
| `petcore/ui/app.py` | +14 | `_PresentationAdapter.hide()`：owner 非空且闸门不归它 → no-op；否则 `window.hide_bubble()`。 |
| `petcore/ui/pet_window.py` | +6 | `hide_bubble()`（先断开气泡队列再 `bubble.hide()`）。 |
| `petcore/contracts/speech.py` | +7 | `SpeechResult.volume: float = 1.0` —— 合成时**实际采用**的音量。 |
| `petcore/speech/service.py` | +8/−1 | 三处 `SpeechResult(...)`（缓存命中 / 兜底音色命中 / 新合成）都带 `volume=self._volume_value(request.volume)`。 |
| `petcore/ui/speech_channel.py` | +6/−2 | `_play` 用 `result.volume` 播；不再把 `job.request.volume`（可能为 None）交给音频层；legacy `play()` 也收到音量。 |
| `petcore/builtin/features/fun.click-talk/host/factory.py` | +14 | `speak(..., on_error=...)` 只记 `reason`（不记台词）；失败不再无声。 |
| `petcore/builtin/features/fun.self-talk/host/factory.py` | +14 | 同上（含 champion 逐句朗读）。 |
| `petcore/ui/settings/dialog.py` | +100 | 侧栏底部两枚重置按钮（objectName/accessibleName/tooltip）；确认框（DestructiveRole「重置」、默认与 Esc 取消）；`_reset_scope()` → `apply_reset` → 全局额外 `sync_shared_settings`+`reload_menu` → `apply_live_settings` → `_close_after_reset()`（跳过 `_commit` 与快照回滚）。 |
| `petcore/settings_reset.py` | 新增 +98 | 纯逻辑：`reset_keys/reset_values/apply_reset`；全局键 = `GLOBAL_CONFIG_KEYS ∩ DEFAULTS`，本宠键 = `DEFAULTS − GLOBAL_CONFIG_KEYS`；`active_pets`/`positions` 永不重置，本宠另保 `character`/`rx`/`ry`/`screen_name`；写盘失败回滚内存并抛 `ResetError`。 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +16/−2 | `_REASON_TEXT` 补全六类中文（与 core 同口径，mod 不能 import core）；`AiChatFailure` 不再拼成「网络不可达：网络不可达」。 |
| `examples/petcore-mods/media.music/host/recognizer.py` | +13/−1 | SMTC 暂停时窗口源只认**另一台**播放器；同源记 reason 后按暂停处理。 |
| `examples/petcore-mods/media.music/host/runtime.py` | +16 | 暂停边沿调 `presentation.hide(SCENE_OWNER)`（新增 `_hide_sung_bubble()`）。 |
| `petcore/AGENTS.md` | +24 | 新增/改写四条约会：传输抖动重试口径、固定直连 TTL、对话超时取配置、暂停同源排除 + `hide` 只收自己、语音播放音量、重置作用域。 |
| `docs/PETCORE-LOG.md` / `PETCORE-LOG-INDEX.md` | +143 / +4 | L122–L125 与索引行。 |
| 测试（10 个文件） | +390/−29 | 见下（另有 1 个新增测试文件 +145 行）。 |
| `tests/test_petcore_settings_reset.py` | 新增 +145 | 8 例：作用域键集合、保留项、落盘、主宠拆半、子宠只写自己、失败回滚。 |
| `tests/test_petcore_api_chat_stream.py` | +113/−27 | 裸 socket 服务端加 `accepts`/`failures`（SO_LINGER RST）：首连被 RST → 重试成功且只吐一次正文；两次都失败 → `network` 且只 2 次连接；半截分块 → `protocol` 且不重试；已有 delta 不重试。 |
| `tests/test_petcore_settings.py` | +89/−2 | 两个按钮存在且不进页面索引、取消不写、确认后 `_commit` 零调用、本宠重置保身份与全局字号；另加两条几何用例（720×500 × 13/18px：按钮都在侧栏视口内且可见）。 |
| `tests/test_petcore_speech.py` | +52 | `TrackedAudio` 复刻真机 `float(volume)`：请求留空 → 播放音量 1.0；`speech_volume=0.4` → 0.4。 |
| 其余测试 | +127 | `api_chat.py` 端口超时、`http_safety.py` 钉子过期、`ai_mod.py` 文案、`music_sources.py`/`music_mod.py` 暂停同源与收气泡、`presentation_gate.py` hide 归属。 |

## 性能分析

本机（Windows, Python 3.12, 空闲态）实测，命令见每行；形容词一律换成数字。

| 路径 | 实测 | 说明 |
|---|---|---|
| `_open_response` 固定开销（stub urlopen，20000 次） | **0.47 µs/次** | 新增的重试壳只有函数调用 + `range` 循环；同一请求里 `ssl.create_default_context()` 本身 **14.5–26.3 ms**（改造前后都在，只是位置从内联变成调用）。 |
| `settings_reset.apply_reset(SCOPE_GLOBAL)` | **2.92 ms/次** | 5 轮 × 每轮 3 次取中位；含 `snapshot` + 129 个键比较 + `mkstemp`+`os.replace` 原子写盘。本宠作用域 81 键。只在用户点按钮时发生。 |
| `recognizer.decide()`（SMTC 暂停） | **4.27 µs**（无窗口源）/ **9.05 µs**（同源窗口源） | 5 轮 × 每轮 2000 次取中位；多出的 4.8 µs 只出现在「paused 且采到窗口」的拍子上，1 Hz 调用 → 每秒多 5 µs。 |
| 播放音量解析 | **0.26 µs/次** | 每次播放一次 `float(getattr(...))`；无新增线程/网络/磁盘。 |

稳态开销与新增触发频率：

- 网络重试只在传输失败时发生（不是每轮都多一次请求）；成功路径只多 0.47 µs 与一次 `_Progress` 计数。
- `_force_direct` 的 TTL 检查是两次 `time.monotonic()` 比较（每次 `urlopen`，<1 µs）。
- 统计 `paused` 边沿的 `hide()` 只在「上一拍在播 → 这一拍 paused」那一拍调用一次。
- 重置与语音修复不改线程模型：没有新增常驻线程、定时器、系统调用、网络或磁盘访问；内存无增长（`_Progress` 是一次性的两个整数）。

## 实机运行记录

真机 = 本机 Windows（源码树 venv，非 CI、非 mock）。

1. **点击台词出声**（`.scratch/tts_click_probe.py`，真实 `SpeechService` + `CoreSpeechPort` +
   `AudioAdapter` + 真实 Qt 事件循环）：
   ```
   finished: True
   errors: []
   cache_files: 43 newest: 9574b9582bb7ce9e536c2b7b16faa5bf10b915ca.mp3
   ```
   同一次运行里 Qt/ffmpeg 打印了 `Duration: 00:00:03.50 ... Audio: mp3 ... 24000 Hz` 并播完
   （`on_finished` 到达），本机听到声音。改前同一调用会走 `TypeError → on_error(None)` 静默。
2. **音乐暂停判定**（`scripts/music_probe.py --seconds 3`，本机云音乐处于暂停）：
   ```
   SMTC：1 个会话
     cloudmusic.exe  MUSIC(1)  非播放
   音频会话：2 个（Active 0）
     cloudmusic.exe  pid=19288  inactive  mute=no  peak=0.00
   判定：暂停 · 来源=系统媒体会话 · cloudmusic.exe ·《On and On》—Repiet
   ```
   这一拍走的是 SMTC 暂停分支（音频 `inactive`），**没有**触发同源排除；同源排除本身由
   `tests/test_petcore_music_sources.py` 的两条判定表用例覆盖（真机上没抓到「暂停但会话仍 active」
   的那一刻，如实登记在 L123）。
3. **构建门**：`.venv/Scripts/python -m petcore --check` 通过（`strict.problems` 为空）。
4. **静态检查**：`.venv/Scripts/ruff check pet/ tests/ petcore/ examples/` → All checks passed。
5. **测试**：
   - `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py tests/test_architecture.py -q`
     → **2366 passed, 1 skipped in 235.93s**；
   - `tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py
     tests/test_petcore_settings_coverage.py` → **404 passed**；
   - 分族聚焦（`api_chat*`/`http_safety`/`music_*`/`presentation_gate`/`settings*`/`speech`/
     `self_talk`/`ai_mod`）全绿。
6. **一次未复现的崩溃**：全量套件曾在 `test_petcore_click_listeners.py` 期间出现一次
   Windows access violation（faulthandler dump 显示 4 个播报线程同时在 `edge_tts` 首次 import +
   `ssl.create_default_context`，主线程在 `QTest.qWait`）。同一命令随后完整跑过（2366 passed），
   单文件与「ai/api/click 三个族」也各跑过一次全绿；该崩溃与本次改动的播放路径无关
   （崩溃时都停在合成阶段，尚未进入 `_play`），但**未定位到确定根因**，如实登记。

## 未覆盖 / 风险

- **重试是否必然救回**：只重发一次（0.6s 后）；抖动超过一次仍会显示「网络不可达」。没有抓到用户
  那次失败的同一秒日志，无法确认其抖动形态。
- **重复计费**：只在「一个正文/思维链字节都没到」时重发，仍可能让服务端多处理一次请求
  （是否计费未核实）。
- **重置不做页面重建**：确认后直接关闭设置窗；以后若要「留在页面并刷新控件」，需要补一条
  「按配置重建页」的路径。
- **音乐同源排除**只在判定表用例里覆盖，真机未抓到暂停但会话仍 active 的时刻。
- **语音**只在真机上验证了点击台词这一句；定时自语共用同一条 `_play` 路径，未单独听。

## 追加（同日，L128）：联网检索失败一律退回纯 API 回答

用户追加要求：「联网失败也不要不回答，而是只走纯 API 回答」。对照主分支
`pet/chat/providers.py` 的 `research()`／`stream()`（planner 失败 → 状态行 → 照常
`_stream_answer()`），petcore 的 `research()` 本来就是同一套降级，**但调用点没有保险**：
`stream_chat()` 里 `notes = research(...)` 是裸调用，检索阶段的任何非预期异常都会让整轮
变成「本次回复未保存」。

改动（`petcore/api/chat.py`，+24/−8；`tests/test_petcore_api_chat.py` +128）：

- `research()` 调用包 `try/except Exception`：状态行「联网检索不可用，将直接回答」，`notes=""`，
  照常发正式请求；异常只记类型（异常文本可能带 URL/查询串）。
- `inject_search_context()` 同样包一层：状态行「联网资料无法并入提问，将直接回答」，
  用**原始会话**发请求。
- 取消优先：检索失败时用户已点停止 → 返回 `cancelled=True`，不再发正式请求。

验证（本机，offscreen）：

- `pytest tests/test_petcore_api_chat.py -q` → **20 passed**（新增 4 例：planner 500、planner 烂 JSON、
  `research` 抛 `RuntimeError`、`inject_search_context` 抛错；前两例断言正式请求的 messages
  与原文逐字一致）。
- 全量 `pytest tests/test_petcore_*.py tests/test_architecture.py -q` → 见交付时的最终结果。

边界：这只保证「检索失败不阻断回答」。**正式回答自身**两次都传输失败仍会报「网络不可达」
（聊天链路，按本报告前面的重试口径处理）。
