# 动画节拍回归修复 + SMTC 卡死自愈（音乐识别）— 2026-10-29

> **基线**：`24540952`（L122–L134 候选包构建证据）之后的本地工作树
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-10-29
> **范围**：18 个文件（实现 8、测试 3 新增 + 1 修改、文档 6）
> **关联**：[`PR-REPORT-PERF-INTERACTION-2026-10-29.md`](PR-REPORT-PERF-INTERACTION-2026-10-29.md)
> （L134 的 `CoarseTimer` 就是本次动画回归的来源）、[`PETCORE-LOG.md`](PETCORE-LOG.md) L135–L136

## 一、核心特性

用户报两件事：「新版本动画播放看着有点慢，开到 2 倍速还是慢」和「音乐播放识别不到了」。

1. **动画节拍**：L134 把帧表定时器从 `PreciseTimer` 改成 `CoarseTimer`，理由是"Precise
   会把系统计时器精度钉在 1ms"。**这个理由经 A/B 实测不成立**（`timeBeginPeriod(1)`
   前后 QTimer 均值 41.8 / 42.1ms 相同，Qt 6 不调用它）。而 Windows 的计时器颗粒是
   15.625 ms，Coarse 会把 24fps 的 42ms 量化成 47ms（1x 只有 21.3fps）、2x 的 21ms
   量化成 31.25ms（**2x 只有 32fps，等于只开了 1.33 倍**）。改回 Precise 即恢复。

2. **音乐识别**：本机 Windows SMTC 服务的
   `GlobalSystemMediaTransportControlsSessionManager.RequestAsync()` **永不返回**
   （纯 Python 与 Windows PowerShell 5.1 的 .NET WinRT 投影都超时 → 系统级）。
   而我们的"超时"是 `asyncio.wait_for(_run_sync(...))`——卡住的是**同步 COM 调用**，
   `wait_for` 根本轮不上。后果链：采样线程停在 `_collect()` → `_inflight` 永远 True →
   此后每一拍 `poll()` 直接 return → 识别永久停摆（设置页「识别诊断」也一直转圈）。
   现在 SMTC 读取跑在专用线程上、有 0.5s 硬超时与 30s 降级窗，降级期用
   「播放器窗口标题 + WASAPI」继续识别，读取线程恢复后自动切回主源。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 帧表节拍回到 Precise | 1x 23.8fps / 2x 47.6fps（改前 21.3 / 32.0） |
| 2 | SMTC 硬超时 + 降级 | 0.5s 超时（首读 ×4）、30s 降级窗、窗到期退役旧线程重探（有上限）、读线程空出来即自愈；退化不判 `ERROR`，直接判窗口源 |
| 3 | 采样拍自愈 | `poll()` 在飞看门狗（3s）命中时把"读不到"直接交给判定层 + `_collect` 异常兜底，单拍卡死/异常不再冻结整条链路 |
| 4 | 同播放器 paused 的 peak 判据 | `AUDIBLE_PEAK=0.005` 区分「残留 Active」与「SMTC 状态落后但真在放」；这种暂停带 `soft_paused`，运行时 2 拍迟滞 |

**红线 / 不变量**：

- `smtc_error`（本拍读失败 → 保留上一拍、不动歌词）与 `smtc_degraded`（持续读不到 →
  改判窗口源）是两种语义，不许合并。
- 无退化时行为逐位不变：`read_sample()` / `_bounded()` / 旧判定表全部照旧。
- 帧序列的「未到货不跳帧」语义不变（只换定时器类型，不加追帧调度）。
- 识别链路仍然零 Qt、零网络；日志不含 URL、路径、曲名。

## 二、修改文件说明

`git diff --numstat`（工作树）：

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/media/clip.py` | +6 / −7 | `create_frame_clip` 去掉 `timer_type=CoarseTimer`（回到 `FrameSeqClip` 默认 Precise），删掉"钉 1ms"的注释，写明实测依据 |
| `examples/petcore-mods/media.music/host/now_playing.py` | +252 / −0 | `SmtcSample.degraded`；新增 `SmtcReader`（专用线程 + 单槽请求 + 0.5s 硬超时（首读 2s）+ 30s 降级窗 + 窗到期退役旧线程重探（`SMTC_MAX_STUCK_THREADS=2`）+ `stop()` 有界 + 线程内 `close_thread_loop()`；每条线程独立事件，退役线程不与新线程抢请求） |
| `examples/petcore-mods/media.music/host/sampling.py` | +4 / −0 | `Sample.smtc_degraded`；从 `SmtcSample.degraded` 传出（退化不短路 audio/window） |
| `examples/petcore-mods/media.music/host/recognizer.py` | +75 / −21 | `AUDIBLE_PEAK` + `_audible()`；`smtc_error` 只在**非**退化时返回 `ERROR`；同播放器 paused 证据改用 peak 判据，并标 `Decision.soft_paused` |
| `examples/petcore-mods/media.music/host/runtime.py` | +113 / −4 | `start()` 建 `SmtcReader`、`_collect()` 在采集时解析接缝（`_smtc_callable`）、`stop()` 先停采样线程再收 reader；`INFLIGHT_LIMIT_SECONDS=3` 看门狗（命中时把退化样例交给判定层）+ `_collect_safely` 共用；`SOFT_PAUSE_CONFIRM_TICKS=2` 迟滞；退化日志开始/恢复各一条 |
| `examples/petcore-mods/media.music/host/settings_page.py` | +17 / −3 | 「识别诊断」改走**模块级共用**的有界 reader（每次点击新建会在卡死机器上每次漏一条线程），收尾只关音频会话 |
| `examples/petcore-mods/media.music/host/diagnostics.py` | +4 / −0 | 退化时 SMTC 段显示「读取超时（已降级：用播放器窗口标题 + 音频会话识别）」 |
| `scripts/music_probe.py` | +29 / −22 | 探针同样走 `SmtcReader`（`try/finally` 收口），卡死时输出降级原因 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_frame_pacing.py`（新，63 行） | +63 | 帧表必须 `PreciseTimer`；24fps 的 1x=42ms / 2x=21ms |
| `tests/test_petcore_music_smtc_timeout.py`（新，212 行） | +212 | 永挂 reader → `read()` 有界返回 `degraded=True`；降级窗不派发；窗到期重探；重试上限后明说"已停止重试"；恢复后自动读到真值；`stop()` 有界；收口后不建线程；采样层传出 `smtc_degraded` |
| `tests/test_petcore_music_sampling_watchdog.py`（新，253 行） | +253 | 卡死拍 3s 后作废并把"读不到"交给判定层；未超预算仍跳过；`_collect` 抛异常后线程活着；reader 接管链（`poll()` 落地退化样例、`stop()` 解绑）；soft-pause 迟滞（1 拍保留 / 2 拍确认） |
| `tests/test_petcore_music_sources.py` | +54 / −3 | 降级 + 窗口源 → 在播；降级 + 无源 → 不在播（不是 `error`）；同播放器 paused + peak>0 → 在播；原「残留 Active」用例改为 `peak=0.0`（口子从"看状态"变成"看有没有声音"） |

### 文档

| 文件 | 增删 | 内容 |
|---|---|---|
| `docs/PETCORE-LOG.md` | +67 | L135（帧表节拍）、L136（SMTC 超时降级 + peak） |
| `docs/PETCORE-LOG-INDEX.md` | +2 | L135 / L136 索引行 |
| `docs/PETCORE-MOD-FORMAT.md` | +13 / −2 | §13.3 判定表补两行（退化、同播放器 peak 判据）+ 两条硬要求 |
| `petcore/AGENTS.md` | +15 | 帧表节拍固定 Precise；SMTC 有界读取/卡死降级；peak 判据 |
| `docs/INDEX.md` | +1 | 本报告登记 |

### 未改动（故意）

- `petcore/ui/pet_manager.py` 的 16ms `CoarseTimer` 碰撞表：32Hz 对分离判定的观感足够，
  是 CPU 取舍，与动画节拍无关（多宠时把它改 Precise 会多一倍唤醒）。
- `pet/frameseq_clip.py`：默认仍是 Precise，`timer_type` 参数保留给排障 A/B。
- `now_playing.transport()`：本来就在一次性后台线程里，卡死只会漏一条 daemon 线程
  （用户手点触发，有界），不在本次范围内改成 reader 通道。

## 三、实现要点

- **为什么读线程而不是只加超时**：Python 不能打断卡住的同步调用，`asyncio.wait_for`
  也只能在事件循环里生效。把 WinRT 调用关进自己的线程、用 `Event.wait(timeout)` 等
  结果，采样线程才真正有界；卡住的那条线程只允许一个（单槽请求，卡住期间不排队）。
- **降级窗 30s + 窗到期重探**：卡住的调用会一直占着读取线程；窗内立即返回退化样例
  （实测 3.8–6.0ms/拍），避免每拍都付 0.5s。窗到期还卡就**退役**那条线程（解绑、
  `closing.set()`、不 join——Python 不能杀线程）另起一条探测，最多退役
  `SMTC_MAX_STUCK_THREADS=2` 条，之后理由改成"已停止重试"。读取线程一旦返回（无论
  成功还是 WinRT 报错）就清掉降级标记，下一拍自然回到真实读取——不需要重启程序。
  每条线程一对独立事件：退役线程若与新线程共享 `wake`，两个消费者会抢同一个单槽请求。
- **首读宽限**：winrt 惰性 import + 建管理器 + 首次枚举是这条链路最贵的一步，首读用
  `max(timeout, min(2s, timeout×4))`，避免把冷启动误报成"媒体服务卡死"。
- **退化不是错误**：`STATUS_ERROR` 的语义是"本拍读失败、保留上一拍"，持续卡死按它
  处理等于永远停在最后一首歌上。所以退化只跳过 SMTC、照常问窗口源；窗口源也没有
  才判"不在播"（歌词冻结、宽限后停唱）。
- **peak 判据 + 迟滞**：同一个播放器暂停后 WASAPI 会话常仍 `active`（否则"暂停了还在
  唱"），而 SMTC 状态也可能落后于实际播放（否则"在放歌却识别不到"）。两种情况状态码
  一模一样，唯一区别是**有没有声音**：`peak ≥ 0.005` 才算在出声。这种暂停标
  `soft_paused`，运行时连续 2 拍才认（`PAUSED` 的缺席宽限是 0，单拍静音不迟滞就会
  1Hz 释放—重播、气泡忽隐忽现）。
- **在飞看门狗要落地**：只把 `_inflight` 置回 False 不够——worker 真卡死时再没有
  sample 落地，状态机会停在最后一拍（继续唱歌、歌词不动）。命中时直接构造
  `smtc_degraded` 样例交给判定层（卡死线程迟到的结果 seq 更旧，会被 `_delivered_seq`
  丢弃）；日志只记一次，恢复后复位。
- **接缝在采集时解析**：`_collect()` 用 `_smtc_callable()`（有 reader 走有界读取，
  否则用测试替身/同步接缝），而不是在 `start()` 里改写 `sample_smtc`——否则 `stop()`
  期间采样线程的最后一拍可能又落到无界 `read_sample` 上，重新卡死。

## 四、性能分析

环境：Windows 10 19045、i7-12700、32GB、`.venv` Python 3.12、`QT_QPA_PLATFORM=offscreen`；
样本为真素材 `assets/characters/shenshen/frameseq/click/点击回应-傲娇生气.g…`（241 帧 / 24fps）。

**① 帧表节拍（采样 4 秒窗口，逐帧 `frameChanged` 时间戳）**

| 配置 | 1x | 2x |
|---|---|---|
| PreciseTimer（本次） | **23.84 fps**（96 帧 / 3.99s） | **47.62 fps**（190 帧 / 3.97s） |
| CoarseTimer（L134，改前） | 21.33 fps（86 帧 / 3.99s） | 32.00 fps（128 帧 / 3.97s） |

QTimer 基准（2 秒，单计时器）：`Coarse 21ms` 实测 mean **31.3ms**（=2×15.625）；
`Coarse 42ms` mean **47.2ms**；`Precise 42ms` mean **41.8ms**（no-begin）/
**42.1ms**（`timeBeginPeriod(1)`）——Precise 的节拍均值就是目标值，A/B 前后一致。
（注：这只说明节拍均值正确；"Qt 是否抬高系统计时器分辨率"本次没有可用探针，不作断言。）

**稳态开销**：定时器类型不改变唤醒次数（1x 24 次/秒、2x 48 次/秒，与改前相同），
只是不再被量化到 15.6ms 边界。**无新增定时器、无新增系统调用、无新增磁盘/网络。**

**② 音乐识别（本机 SMTC 卡死现场）**

`scripts/music_probe.py --all --seconds 6 --interval 1`（每拍计时包含三源采集）：

| 拍 | 采样耗时 | 结果 |
|---|---|---|
| 1 | **2155.9 ms**（首读宽限 2s） | `SMTC：读取超时（已降级…）`，判定=不在播，理由写明超时 |
| 2 | 6.2 ms | 降级窗内立即返回（WASAPI 会话照常列出） |

同一现场下 `MusicRuntime` 全链路（`.scratch/verify_music_runtime.py`，真 `SmtcReader`
+ 真音频/窗口源，7 拍）：第 1 拍 2000ms 等满首读宽限后降级（这一拍还没有判定），
第 2–7 拍 15–31ms 全部 `decision=absent / degraded=True`；`stop()` 对卡死 reader 记
一条 "still stuck"（有界 0.2s），GUI 不冻结。

改前：同一脚本在 `request_async` 上**挂死 >20s**（超时命令直接被杀），设置页识别
诊断同样永不返回。

**稳态开销**：`music_enabled` 时新增 1 条常驻读取线程（空闲时 `Event.wait(0.25)` 轮询，
4 次/秒，无 COM 调用）；正常读取仍是每秒一次 WinRT 查询（~5ms 级，与改前相同）；
退化期每拍只多一次 `peak` 比较（可忽略）。**没有新增系统调用、网络、磁盘**；内存
增量 = 1 条线程栈 + 单槽请求对象，无累积（卡死线程每次生命周期最多 1 条）。

**③ 回归套件**：`tests/test_petcore_*.py` + `tests/test_architecture.py` +
`tests/test_pr_report_discipline.py` + `tests/test_petcore_docs.py` → **2826 passed,
1 skipped / 199.95s**（改前同口径 2805 passed / 1 skipped；全量在最终代码上跑过两遍：
189.18s 与 199.95s）；音乐+帧聚焦套件 899 passed / 44.26s；`ruff check pet/ tests/
petcore/ examples/petcore-mods/ scripts/` 全绿；`python -m petcore --check --strict` →
`strict.problems=[]`。

## 五、实机运行记录

1. **SMTC 卡死取证**（22:1x，本机，CloudMusic 三个进程在跑）：

   ```
   step1 import
   step2 request_async
   （>20s 无输出，timeout 杀掉）
   ```

   `asyncio.wait_for(..., 8.0)` 同样拦不住（>15s 不返回）。Windows PowerShell 5.1 的
   .NET WinRT 投影：`RequestAsync()` → `TIMEOUT after 8s`。**结论：系统级卡死，
   与 winrt 包/我们的代码无关。**

2. **WASAPI 正常**：同一时刻 `audio_sessions.snapshot()` 返回
   `dsh-pet-core.exe inactive`、`cloudmusic.exe inactive`（当时是暂停态，peak=0），
   说明音频源本身没坏。

3. **probe 改后**：见 §四②的表（第 1 拍 2155.9ms（首读宽限）、之后 6.2ms，诊断文本
   给出「读取超时（已降级…）」），不再挂死。

4. **MusicRuntime 全链路**（`.scratch/verify_music_runtime.py`）：第 1 拍 2000ms
   等满首读宽限并降级，第 2–7 拍 15–31ms 全部 `absent / degraded=True`；日志出现一次
   `SMTC read timed out; falling back to window-title recognition`；`stop()` 后
   reader 线程仍卡（系统 broker 未恢复），日志如实记 "still stuck"，进程退出即释放。

5. **帧率**：见 §四①（用 `.scratch` 脚本按 `frameChanged` 时间戳统计，4 秒窗口，
   各配置单独跑一次）。

6. **未做**：真实网易云「播放中」的识别（SMTC 卡死、当时曲目是暂停态，无法构造
   "真在出声"的窗口源证据）；SMTC 恢复后的自动切回（单元测试覆盖了"窗到期重探 +
  读线程返回即解除降级"，真机需要重启播放器/电脑，未在本机执行）。

## 六、未验证 / 风险

- SMTC 卡死是**系统级**的（PowerShell 5.1 同样超时）：本次修复只保证"卡死时识别
  仍可用 + 卡死恢复后自愈"，**不能修 Windows 媒体服务本身**。若重启播放器后仍卡死，
  需要在真机上继续排查（可能要在重启电脑后复验）。
- 降级期只有窗口标题 + WASAPI 两源：浏览器内播放、标题里没有歌名的场景识别能力
  弱于 SMTC（与既有兜底口径一致）。
- `AUDIBLE_PEAK=0.005` 是经验门限：只做了单测与一次真机采样，没有长时间播放的统计；
  迟滞只覆盖"同播放器 + 窗口源在播"这一条路径（2 拍），真静音 2 秒后仍判暂停。
- 卡死的 reader daemon 线程无法回收（Python 不能杀线程）：降级窗每到期一次退役一条、
  重探一次，最多 `SMTC_MAX_STUCK_THREADS=2` 条，之后停止重试（理由会明说），进程退出即释放。
- 帧率是 offscreen 计时器统计，不含 DWM 合成与真实桌面负载；真机观感需用户确认。
- 安装包里的 mod 代码来自 `examples/petcore-mods/media.music`：**重装 setup 才会覆盖**
  `{app}\mods\media.music`，只更新主程序不会带上本次音乐修复。

## 七、评审修正（独立审查后补的 8 处）

首版实现后做了一轮独立代码审查（只看能证明的缺陷），以下问题**已经改进**（不是"已知未修"）：

| # | 审查发现 | 修正 |
|---|---|---|
| 1 | `_stuck` 与 `_degraded_until` 永远成对写入 ⇒ **30s 降级窗是死参数**，永不返回的 broker 场景下再也不会重探（"恢复自愈"不成立） | 窗到期仍卡 → 退役旧线程（解绑、不 join）+ 另起一条探测；`SMTC_MAX_STUCK_THREADS=2` 次后停止重试并明说"已停止重试"。补两条用例（窗到期重探、重试上限） |
| 2 | `stop()` 先把 `sample_smtc` 换回无界 `read_sample`、之后才通知采样线程收工 ⇒ 最后一拍可能重新卡死，且换线程也救不回 | 顺序改成"先 `_closing/_wake` 再收 reader"；接缝改为**采集时**解析（`_smtc_callable()`），`stop()` 不再改写 `sample_smtc` |
| 3 | 看门狗只放开 `_inflight`：worker 真卡死时再没有 sample 落地，状态机永远停在最后一拍（继续唱歌） | 命中时构造 `smtc_degraded` 样例**直接交给判定层**（按退化收循环）；迟到的结果 seq 更旧被丢弃 |
| 4 | 同步采样路径（未 `start()`）没有 `_collect` 兜底 | 抽出 `_collect_safely()`，worker 与同步路径共用 |
| 5 | 「识别诊断」每次点击新建 reader ⇒ 卡死机器上每次漏 1 条卡在 COM 的线程 | 改成模块级共用的一条 reader |
| 6 | peak 单拍门限 + `PAUSED` 零宽限 ⇒ 淡出/间奏会 1Hz 释放—重播 | `Decision.soft_paused` + 运行时 `SOFT_PAUSE_CONFIRM_TICKS=2` 迟滞 |
| 7 | 0.5s 硬超时对冷启动（winrt import + 建管理器 + 首次枚举）偏紧，健康机器也可能误报一次降级 | 首读宽限 `max(timeout, min(2s, timeout×4))` |
| 8 | 生命周期边界不重置 `_smtc_degraded_logged` / `_inflight_warned` / `_inflight_since`；reader 接管链零测试 | `start()/stop()` 统一复位；补"`start()` 装 reader → `poll()` 落地退化样例 → `stop()` 解绑"用例 |

审查还指出："Precise 不会抬高系统计时器精度"这句**证据不足**（A/B 只能证明节拍均值一致，
分不清是谁设的分辨率）。代码注释、AGENTS、本报告已改成只主张"均值正确 + 旧版满速档一直如此"。

## 八、候选包构建（signed）

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 \
  -Clean -Installer -SignThumbprint 217337C099EC8624AC481BFC9F3E1664D0A442E1 \
  -OutputRoot rel-anim-music-2026-10-29b
→ smoke --check --strict（strict.problems=[]，menu_template=json，characters=1）
→ Inno Successful compile（124.9s）；exe 与 setup Authenticode 均 Valid
   （CN=DSH Pet Core Dev；本次带可信时间戳：DigiCert SHA256 RSA4096 Timestamp Responder）
```

| 产物 | 字节 | SHA-256 |
|---|---|---|
| `dsh-pet-core-setup.exe` | 305,029,600 | `AEC08FD71C55337C34E8842E55B5A37827276A02CDC5E8702A6B57E5A7CA4579` |
| `dsh-pet-core/dsh-pet-core.exe` | 15,600,776 | `9C00793B8C2609CA6AC90606506E0B49BE62BE77004AED2434D6BD12EFF247B2` |

- 第一次构建（`rel-anim-music-2026-10-29`）在 ISCC 压缩源码的中途我还在改 mod 文件
  （`runtime.py`/`now_playing.py` 的最后两处硬化），**那份安装包的 mod 载荷可能是旧
  字节**，已作废并删除；`-2026-10-29b` 是在代码冻结后重编的，ISCC 在 23:21:47 之后
  才开始压缩（所有源码写入早于它）。
- **安装时必须勾选「音乐信息（media.music）」**（`Flags: unchecked`，默认不勾选）：
  只有勾选才会把 `{app}\mods\media.music` 覆盖成本次修复后的代码；主程序 exe 的修复
  （帧表节拍）与 mod 无关，装完即生效。
- 未在本机运行安装包（会覆盖正在使用的 `E:\dsh-pet-core` 安装并弹出 UAC）。
