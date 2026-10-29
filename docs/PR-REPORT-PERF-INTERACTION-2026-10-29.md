# PR 报告：性能热点修复 + 仅左键穿透/右键交互 + 可调 Q 弹（2026-10-29）

> **基线**：`ac0340a9`（feat/petcore-vpet-architecture 当前 HEAD，含 L111–L128 未提交工作树）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-10-29
> **范围**：实现 29 个文件、测试 20 个文件、文档 6 个文件（含 3 个新增测试文件与 1 个新增纯逻辑文件）
> **关联**：用户反馈「DSH 思考时 CPU 高」「QQ 音乐同步歌词线程泄漏」「AI 对话偶发网络不可达」；截图里的 `idle_low_fps_enabled` / `tick 标称` 属于旧版 `pet/`，本报告以 petcore 为主。

## 一、核心特性

用户这一轮要三件事：修 CPU 性能漏洞、把点击穿透改成「只穿透左键」并换用右键操作、把 Q 弹做成可调可关。另追加一条：AI 对话偶发「网络不可达」要修，联网工具失败要稳定回退纯 API 回答并由 LLM 说明。

| # | 能力 | 说明 |
|---|---|---|
| 1 | FPS/ETW 探针不再 churn | 岛隐藏/关闭当场停采；停采保留定宽占位（行宽不变，折叠计划不再 1Hz 振荡）；FPS 目标退出后不重开死 pid；换目标过 3s 会话闸门 + 2s 去抖；进程级一次残留 `dsh-pet-fps-*` ETW 会话清扫 |
| 2 | 音乐取词单飞 | 一条常驻 `lyric-fetch` 线程 + 单槽队列，新请求取消在飞请求；播放中同名降级身份记别名，不再清歌词/重置配额；失败按歌名 60s 负缓存；暂停窗口枚举降到 2s；SMTC 管理器/会话列表按线程缓存 |
| 3 | DSH 联动不再每 2s 重启动画 | 上次请求的动画仍在播就不重发；`AGENT_ANIMATION_MIN_SECONDS=8` 旋转地板；`_requested_loop` 才允许 `release_loop()` |
| 4 | 全屏探测连拍确认 | `confirm_polls=2`（1Hz 下约 2s）；`taskmgr.exe` 不算全屏应用；20 拍交替从 21 次 hide/show 降到 1 次 |
| 5 | 左键穿透（可选） | 开启 `click_through` 后：左键单击松手时转发给下层窗口、左键长按 250ms 拖动；右键单击=旧左键单击（点击反馈/台词/余额/Q 弹/音效）、右键长按 350ms=菜单（松手弹出）；拖拽中右键仍进弹弓 |
| 6 | 可调 Q 弹 | 新增 `click_squash_bounces`（1–5，默认 1）与 `click_squash_strength`（30–200%，默认 100），并在设置页暴露 `click_squash_ms`（80–1000ms）；关闭 `click_squash_enabled` 后点击/碰撞/撞岛/落地都不弹 |
| 7 | 常驻定时器/每帧开销收口 | 单宠不启动 16ms 碰撞表；隐藏宠停输入轮询；`frame_pixmap` 不再每帧缩放/镜像，改到 `paintEvent` 按目标矩形绘制；帧序列同 clip 重播不拉回第 0 帧 |
| 8 | 对话网络修复 + 联网回退 | `urlopen` 对 500/502/503/504 换路；钉直连 300s→45s 且失败可回代理；流式 3 次路由计划（默认→只直连→只代理）；检索失败只发状态行 + system 提示，不触发 `chat.error` |
| 9 | 锁屏/睡眠停播 | `SessionWatcher` 处理 WTS lock/unlock 与 suspend/resume；锁定期间停播放节拍、碰撞表、输入轮询，解锁续播当前帧 |

**红线 / 不变量**：关闭 `click_through` 时左键单击/拖动/右键菜单/弹弓逐位保持原语义；`bounces=1, strength=1` 的 Q 弹几何与旧实现逐位一致；FPS/音乐/对话的现有失败分类与「不支持不占位」口径不变；不新增依赖，不改打包产物。

## 二、修改文件说明

> 只列本批新增/改动的文件；工作树里另有 L106–L128 的历史未提交改动（语音、重置、多宠上限等），不在本报告范围。行数取 `git diff --numstat`（新增文件取 `wc -l`）。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/platform/fps.py` | +274 / −11 | 目标退出 `target_gone`/`gone_pid` 不重开死 pid；`set_target` 同 pid 幂等；`MIN_SESSION_SECONDS=3` + `TARGET_CHANGE_DEBOUNCE=2`；`shutdown(wait_seconds=1.0)`；`logman` 残留会话清扫（纯解析函数 + 守护线程 + 只停属主已死会话） |
| `petcore/ui/probes.py` | +178 / −33 | 停采推定宽 `sampling` 占位而非清快照；unsupported 缓存 60s 只推一次；`_retarget_fps` 活着不读前台/退出后退避 30s/同 pid 不重复 set；`shutdown(1s)` |
| `petcore/ui/island.py` | +72 / −10 | `stop()`/`set_visible(False)` 收口 `_sync_sampling`；`set_probe_sampling()` 定宽占位；`_metric_segments` 在「已接探针但无快照」时占同宽，unsupported/未接仍不占位 |
| `petcore/island/segments.py` | +6 / −0 | `PROBE_BOX_TEXT`/`PROBE_UNITS` 成为占位宽度单一真值 |
| `examples/petcore-mods/media.music/host/runtime.py` | +368 / −30 | `LyricFetchWorker` 单线程单槽队列 + 取消 + 有界 join；播放分支同名别名；歌名级 60s 负缓存；GUI 线程不再内联采样；`_cache()` 移到取词线程 |
| `examples/petcore-mods/media.music/host/lyrics.py` | +46 / −4 | `LyricsCancelled` + `cancelled=` 贯穿三源，每跳 HTTP 前收手 |
| `examples/petcore-mods/media.music/host/sampling.py` | +67 / −6 | `WINDOW_PAUSED_INTERVAL=2.0` + `WindowCache`；暂停只降窗口枚举，SMTC/WASAPI 仍 1s |
| `examples/petcore-mods/media.music/host/now_playing.py` | +70 / −6 | 每线程 SMTC manager + 会话列表缓存（列表 2s 刷新），选中会话曲目/进度仍每拍 |
| `examples/petcore-mods/ai.agent/host/links.py` | +54 / −2 | 上次请求仍在播则跳过；8s 旋转地板；旧 Core 无 `animation_state` 时退回旧节流 |
| `examples/petcore-mods/ai.agent/host/runtime.py` | +36 / −3 | `animation_state()` 转发；`_requested_loop` 所有权判断，done 不误清别人的 loop |
| `petcore/ui/fullscreen_hide.py` | +57 / −9 | `confirm_polls` 连拍确认；关闭/stop 清候选；配置关闭仍立即恢复 |
| `petcore/platform/fullscreen.py` | +4 / −2 | `taskmgr.exe` 排除 |
| `petcore/net/http.py` | +133 / −14 | `routes=` 单次路由计划；5xx（无 Retry-After）换路；钉直连 45s + 失败回代理/清钉；请求每次 clone |
| `petcore/api/chat.py` | +263 / −53 | 3 次流式路由计划；`ResearchOutcome(notes, searched, failed)`；失败时 system 提示「联网检索暂时不可用」+ 状态行；HttpStatusError/取消不重试 |
| `petcore/engine/pointer.py` | +120（新） | `PointerArbiter` 穿透模式状态机；`POINTER_DRAG_HOLD_MS=250` / `POINTER_MENU_HOLD_MS=350` |
| `petcore/platform/win.py` | +135 / −5 | `forward_left_click()` 临时透明 + SendInput 左键 down/up；64 位安全 argtypes；逐像素轮询不再整窗穿透，按住/转发期间不抢写；`stop()/resume()` |
| `petcore/platform/session.py` | +98 / −6 | WTS lock/unlock、power suspend/resume → pause/resume 回调；`WTSRegisterSessionNotification` HWND argtypes |
| `petcore/media/clip.py` | +36 / −1 | `pause/resume` 透传；`rearm_same()`（帧序列在跑时不重开） |
| `petcore/ui/pet_window.py` | +358 / −50 | 穿透模式左右长按/右键菜单（松手弹）/转发；`_finish_drag_release`/`_launch_slingshot` 复用；锁屏 pause/resume + `_play_spec` 闸门；隐藏停输入轮询；paint 目标矩形/镜像/1 项不缩放帧 |
| `petcore/ui/pet_manager.py` | +74 / −5 | 碰撞表 ≥2 只才启动（Coarse），单宠 0 唤醒；pause/resume 会话 |
| `petcore/ui/renderer.py` | +9 / −25 | 只做 QImage→QPixmap，不再每帧缩放/镜像/设 DPR |
| `petcore/ui/effects.py` | +18 / −1 | Q 弹读三键并传给几何；关闭开关直接 return |
| `petcore/engine/squash.py` | +15 / −2 | `bounces`/`strength` 参数；`bounces=1,strength=1` 保持旧几何 |
| `petcore/config.py` | +15 / −2 | 两个新每宠键的默认值与归一化（1–5 / 30–200） |
| `petcore/ui/settings/page_interaction.py` | +66 / −1 | Q 弹三行（次数/强度/时长）+ 跟随开关置灰 + 搜索关键词 |
| `petcore/ui/settings/pages_core.py` | +2 / −1 | 穿透 hint 改为仅左键语义 |
| `petcore/ui/app.py` | +45 / −3 | 会话 pause/resume 接线；托盘穿透文案 |
| `pet/frameseq_clip.py` | +13 / −2 | 可选 `timer_type`（默认 Precise 不变）+ `rearm_same()` |
| `petcore/AGENTS.md` | 见 diff | Q 弹键、仅左键穿透、碰撞表按需、帧渲染、锁屏停播、探针占位/目标生命周期、歌词单飞口径 |
| `docs/PETCORE-SPEC.md` | +9 / −2 | Q 弹参数与仅左键穿透语义 |
| `docs/SETTINGS-CHANGE-GATES.md` | +41 / −0 | 两个新键的准入/准出记录 |
| `docs/PETCORE-MOD-FORMAT.md` | 见 diff | 音乐取词单飞/负缓存/采样降频口径 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_fps_probe.py` | +197 | target_gone、同 pid 幂等、3s 闸门、有界 shutdown、logman 解析/清扫/一次/非 Windows |
| `tests/test_petcore_probe_hub.py` | +245 / −2 | 停采占位、行宽不变、unsupported 不占位、隐藏停采、blocked 缓存、retarget 退避、有界 shutdown |
| `tests/test_petcore_island_window.py` | +41 | stop/hide/set_visible 交空集 |
| `tests/test_petcore_music_mod.py` | +305 / −3 | 100 次切歌并发 ≤1、同名别名、60s 负缓存、stop join、取消 1 跳、不唤醒 worker |
| `tests/test_petcore_music_sources.py` | +299 | 暂停窗口枚举 2s、SMTC manager/列表缓存、per-thread 隔离、absent/playing 基线 |
| `tests/test_petcore_agent_mod.py` | +173 / −5 | 仍在播只 play 一次、8s 地板、locked 仍受地板、旧桥兼容、loop 所有权 |
| `tests/test_petcore_fullscreen_hide.py` | +118 / −2 | 2 拍确认、20 拍交替只 1 次、taskmgr、可配置/钳制、关闭清候选 |
| `tests/test_petcore_http_safety.py` | +187 | 代理 502→直连 200、两路 5xx、Retry-After 不换、401/403/429 不换、routes 不污染、钉 TTL/回代理/清钉 |
| `tests/test_petcore_api_chat_stream.py` | +339 / −28 | 502 换路、200 后断流→强制直连、IncompleteRead、3 次上限、HttpStatusError/已吐正文不重试 |
| `tests/test_petcore_api_chat.py` | +213 / −2 | 工具失败/预算超时→纯 API + system 提示；planner 失败仍回答；无 system 时插入 |
| `tests/test_petcore_ai_mod.py` | +163 | 搜索失败不触发 mod error path，状态行/done 正常 |
| `tests/test_petcore_interaction.py` | +119 / −1 | Q 弹三键/几何边界；穿透开左键转发、右键单击/长按菜单、左键长按拖动；穿透关旧行为 |
| `tests/test_petcore_pointer.py` | +90（新） | 状态机全路径（单击/长按/拖拽中右键/重置/取消） |
| `tests/test_petcore_platform_win.py` | +113（新） | 转发置位/恢复/失败回退；不透明像素不整窗穿透；按住期间不透明 |
| `tests/test_petcore_paint.py` | +103（新） | 镜像在 paint 侧、Q 弹从底边、目标矩形 |
| `tests/test_petcore_renderer.py` | +9 / −11 | 不再缩放/设 DPR；像素不镜像 |
| `tests/test_petcore_multipet.py` | +34 / −3 | 单宠不启碰撞表、双宠启动、关闭停 |
| `tests/test_petcore_config_scope.py` | +19 | 两个新键默认/钳制/每宠作用域 |
| `tests/test_petcore_settings.py` | +111 / −2 | 三行存在/写入/取消回滚/搜索 |
| `tests/test_petcore_session.py` | +22 | WTS lock/unlock、power suspend/resume 去重 |

## 三、实现要点

- **点击穿透只放左键**：不能整窗挂 `WS_EX_TRANSPARENT`（那样右键一起丢），也不能用 `WM_NCHITTEST`（旧版 `pet/platform_win.py` 已实测它只命中同线程窗口，无法跨进程）。实现是：窗口保持可命中；左键按下只记「待定」，250ms 内松开就临时置透明 + `SendInput` 一次 down+up 把点击转发给下层，超过阈值才进拖动。右键单击/长按由窗口自己处理，长按只做闸门、松手才弹菜单（避免 `QMenu.exec` 把这次 release 当选中）。
- **Q 弹可调**：`pulse = abs(sin(pi * bounces * p)) * strength * decay`；所有脉冲都只压扁/横向鼓出，矩形从底边向上，不会越过窗口顶部；`bounces=1,strength=1` 与旧 `sin(pi*p)` 完全一致。命中测试继续用 `squash_rect()` 的逆变换，与绘制同一矩形。
- **每帧不预缩放**：旧路径每帧 `QPixmap.fromImage → scaled(Smooth) → [transformed] → drawPixmap`；新路径只 `fromImage`，`paintEvent` 用目标矩形 `drawPixmap` + `SmoothPixmapTransform`，镜像用 painter 变换绕目标中心。目标矩形按源宽高比适配（与旧 `KeepAspectRatio + 左上绘制` 同语义），Q 弹用 squash rect。
- **探针不缩行**：GPU/FPS 段宽度只由 `PROBE_BOX_TEXT`/`PROBE_UNITS` 模板决定；停采推 `sampling` 占位（`--`）而不是删段，折叠计划不会因为采样状态变化而重排。unsupported/未接探针仍不占位。
- **FPS 目标生命周期**：`target_gone` 是「PresentMon 正常退出（0）」而不是失败；同 pid 不重开，只有新 pid 的 `set_target` 解除；3s 最短会话 + 2s 换目标去抖防焦点抖动；残留 `dsh-pet-fps-*` 会话由 `logman` 一次清扫，只停属主已死的，查询失败不关闸。
- **音乐取词单飞**：`LyricFetchWorker` 队列只保留最新 job；`submit()` 取消 queued + running，`fetch_lyrics(cancelled=)` 每跳 HTTP 前检查；取消结果不记失败/冷却/负缓存；`stop()` 有界 join。播放分支同名别名保留 `_track_key/_track/_lines/_fetch_attempts`，只重锚进度。
- **对话网络**：`urlopen` 的 5xx 换路只对「确有另一条路」生效（`_has_other_route` 检查系统代理/no_proxy），否则 5xx 会重复请求；`routes=` 是单次计划，不写进程级「固定直连」；`stream_chat` 只在还没吐正文/思维链时重试，最多 3 次；检索失败只发状态行 + system 提示。

## 四、性能分析

**方法（可复现）**：

```bash
# 帧路径微基准（offscreen，QImage 画布，不含 DWM 合成，80 帧均值）
QT_QPA_PLATFORM=offscreen .venv/Scripts/python - <<'PY'
# 旧：fromImage + scaled(Smooth) + drawPixmap；新：fromImage + drawPixmap(targetRect)+Smooth
PY

# 真机空闲/DSH（隔离根，PET_FRAMESEQ=0 排除首跑供给，idle 权重 100）
.venv/Scripts/python .scratch/perf-2026-10-29/measure_app.py idle
.venv/Scripts/python .scratch/perf-2026-10-29/measure_app.py dsh

# 全量/定向
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py tests/test_architecture.py -q
```

环境：Windows 11、Python 3.12、PySide6、`.venv`；CPU 采样为 `psutil.Process.cpu_percent` 进程树（含子进程），0.25s 一拍。

| 指标 | 实测 | 归属 |
|---|---|---|
| 帧路径 461×259 | 0.73 → **0.47 ms/帧**（−36%） | 热路径（旧 scaled+draw vs 新 draw-scaled） |
| 帧路径 1920×1080 | 5.99 → **2.93 ms/帧**（−51%） | 热路径 |
| 单宠碰撞表 | 62.5 Hz → **0 次**（<2 只停表） | 稳态 |
| 隐藏宠输入轮询 | 20 次/s → **0**（hide 停表） | 稳态 |
| 岛 `hub._poll()` 1Hz（GPU+FPS） | **377 µs**（max 1.05 ms） | 热路径 |
| `island.stop()` | 空闲 **9.6 µs** / 首次 **0.60 ms**；停采前后 plan 宽度 **636 不变** | 新增收口 |
| DSH 忙碌 60s 模型 | `play_animation` **20 → 5 次**（每 3s → 每 12s） | 热路径（模型） |
| DSH 真机 45s | **4 个 ffmpeg 子进程**（≈每 11s 一次；旧口径每 2s 一次 ≈22 个） | 热路径（真机） |
| 全屏 20 拍交替 | `set_visible` **21 → 1 次** | 热路径（脚本） |
| 空闲真机（idle 权重 100） | CPU mean **11.66%** / p50 **6.2%** / p95 **36.8%** / max **56.2%**；线程 19 | 稳态（24fps 待机动画） |
| DSH 真机（busy 事件 45s） | CPU mean **15.23%** / p50 **10.9%** / p95 **49.2%** / max **77.8%**；ffmpeg 4 个 | 热路径 |

**结论（逐条）**：
1. **稳态开销**：单宠碰撞表与隐藏输入轮询归零；帧路径缩放/镜像成本按上表下降 36–51%；待机动画仍按 24fps 播放（用户明确不做待机降帧），空闲 CPU 的 p50 约 6.2% 单核，主要是 640×360 WebP 解码 + 24fps 绘制。
2. **新增路径成本**：`island.stop()` 9.6µs（空闲）/0.60ms（首次带探针停采）；`hub._poll()` 377µs/秒；探针占位 176µs/次；转发点击一次 SendInput（~80ms 透明窗口，无轮询）；Q 弹几何只多两次乘法/除法，仍在 16ms 效果表内；负缓存/身份别名只在取词/换歌时走。
3. **系统调用/网络/磁盘/线程**：新增 `SendInput`（仅用户左键单击时）、`logman query/stop`（进程级一次、守护线程、仅 FPS 启用且首次 start）、`WTSRegisterSessionNotification`（一次）；音乐新增一条常驻 `lyric-fetch` 线程（替代每次取词新线程），取词并发 ≤1；没有新增网络轮询或磁盘写；ETW 只在岛需要 FPS 且可见/未折叠时存在。
4. **内存/缓存**：新增只有 `PointerArbiter`/两个长按 QTimer、负缓存 ≤64 条、探针占位快照、`_ThreadCache` 的 manager/会话列表（每线程一份）；没有帧缓存增长；`frame_pixmap` 不再每帧分配缩放后的中间 pixmap，短时分配压力下降。

## 五、实机运行记录

本机 Windows 11，源码树运行（不是 CI、不是 mock）：

```text
# 1) 全量（本批最终）
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py tests/test_architecture.py -q
→ 2478 passed, 1 skipped in 211.67s
# 加文档/PR 门（同批）
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py \
  tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_petcore_docs.py -q
→ 2805 passed, 1 skipped in 213.54s

# 2) ruff / 只读自检
.venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/
→ All checks passed!
git diff --check
→ 只有 CRLF 提示，无空白错误
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m petcore --check --root .scratch/check-perf
→ check.json：菜单模板 json、角色/扩展/语音段正常

# 3) 真机空闲（隔离根；PET_FRAMESEQ=0 排除首跑供给；anim_weight_idle=100）
.venv/Scripts/python .scratch/perf-2026-10-29/measure_app.py idle
→ {"samples":165,"cpu_mean":11.66,"cpu_p50":6.2,"cpu_p95":36.8,"cpu_max":56.2,
   "threads_max":19,"spawned_children":["python.exe"]}

# 4) 真机 DSH 忙碌模拟（桥文件每 1.5s 写 thinking；45s 采样）
.venv/Scripts/python .scratch/perf-2026-10-29/measure_app.py dsh
→ {"samples":157,"cpu_mean":15.23,"cpu_p50":10.9,"cpu_p95":49.2,"cpu_max":77.8,
   "spawned_children":["python.exe","ffmpeg…","conhost…","ffmpeg…",…]}
```

- **现场前提复核**：本机 `logman query -ets` rc=0、0 个 `dsh-pet-fps-*` 残留会话；`petcore/platform/fps.py` 的 `sweep_residual_sessions` 在这一环境下不会命中真实残留（如实登记）。真机 FPS 采集仍受 Performance Log Users 权限限制（plain user → `access denied`），**没有真实帧读数**，只有探针生命周期/占位/停采的自动化与计时证据。
- **QQ 音乐**：本机没有登录态/播放会话，SMTC/WASAPI 真实链路未实测；歌词单飞/负缓存/并发 1 由假端口计数验证（96 passed）。不伪造「QQ 音乐已修复」。
- **对话**：本机无真实代理 502/SSE 断流，全部用假 opener/响应验证（115 passed）；未用真实 Key 发请求，避免产生费用/隐私内容。
- **真机鼠标端到端**（下层按钮、右键菜单、左键长按拖动）**未在本轮执行**：需要真实鼠标输入且会移动/点击用户桌面；`QTest` 真事件已覆盖穿透开/关的窗口内动作，`SendInput` 转发路径由 `tests/test_petcore_platform_win.py` 假 `SendInput` 验证置位/恢复/失败回退。审查者可按设置页 hint 手动复测。
- **本机空闲 CPU 的 p95 36.8%**：45s 内约 5% 的 0.25s 样本；待机动画是 24fps 持续解码+绘制（用户选择不做待机降帧），不是探针/歌词/ffmpeg churn；DSH 真机的 4 个 ffmpeg 是 10s clip 自然换段（≈11s 一次），不是旧版每 2s 重启。

## 六、风险与未验证

- **左键转发的边界**：下层拖拽/框选/滚动条拖动不支持；hover/光标形状不穿透；提权窗口（UIPI）收不到合成输入；非 Windows 仍是旧整窗穿透（右键不可用）。设置 hint 与本报告均已写明。
- **右键菜单松手弹出**：长按只做闸门，不是按住期间弹；这是为了不让 `QMenu.exec` 把 release 当成选中。真机手感需用户复测。
- **FPS/ETW**：真机权限仍 `access denied`；`logman stop` 分支未命中真实残留会话；`MIN_SESSION_SECONDS=3` 比主分支 1s 保守，换游戏最多延迟约 3s。
- **音乐**：`stop()` 仍可能留一条在飞 HTTP 调用 ≤8s（有界 join，结果按 generation 丢弃）；负缓存按歌名，60s 内同名不同曲共用一次封锁；`media.music` 版本未 bump，`dist-extensions` 未重打包。
- **对话**：超时现在也可重试，无 token 的流最多 3×timeout 才报错；配置了代理时真 5xx 会多一次请求；`urlopen` 最终仍抛原始 HTTPError（市场 304/探测需要 `code`），由 `_open_response` 统一转分类。
- **锁屏停播**：只停宠物播放/碰撞/输入轮询；灵动岛探针、音乐采样仍按各自节拍运行。
- **旧版 `pet/`**：截图里的 `idle_low_fps_enabled` / tick / `pet/fps_monitor.py` 没有主动改行为（按用户「以 petcore 为主」的决定）；共享的 `pet/frameseq_clip.py` 只加了可选参数，默认 Precise 不变。
