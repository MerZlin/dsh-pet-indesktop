# 素材与播放架构改造（无损档 + 资源契约 + 帧流水线缓存 + 分包下载）（2026-10-19）

> **基线**：`147170f`（v4.3.4 发布后的 main）
> **分支**：`feat/asset-arch-lossless`（本地，未推送）　**日期**：2026-10-19
> **范围**：4 个产品模块修改 + 4 个新模块 + 2 个新脚本 + 11 个测试文件（含大量测试替身签名同步）
> **关联**：`.scratch/asset-arch/PLAN.md`（方案 v2）、`.scratch/asset-arch/RESOURCE-CONTRACT.md`、
> `.scratch/asset-arch/GATE-diff-vs-lossless.md`（差分闸门结案）

> **状态：未完成。** 见 §五「未完成与待补」。全库位精确验证与真机硬指标验收都要等素材重出完成
> （后台进行中），本轮**不主张**这两条已达标。

## 一、核心特性

修的是用户报的两条画质问题，根因都已实测定位：

1. **「动画闪白点」= 有损编码**。消融实验（同一段 90 帧连续，逐帧统计「只在一帧出现、前后帧没有的近白像素」）：
   完全不处理 + Q70 = 37.2/帧、只做白底反解 + Q70 = 37.1、全链（反解+收色+腐蚀+清底板）+ Q70 = 37.0、
   **全链 + 无损 = 8.6**、源 webm = 15.9。⇒ 处理链一步都没多产生白点，全部来自 Q70 的振铃。
   原始分辨率下各档对照（源 18.9）：无损 **18.8** / Q95 34.7 / Q90 45.3 / Q70 81.1。
2. **「太糊」= 源头分辨率**。源素材 640×360、0.063–0.080 bit/像素/帧（二次压缩），角色 alpha 包围盒只有
   212×267，而屏幕上占 300–340 物理像素 —— **放大显示**。有损 WebP 的色度固定 4:2:0 又雪上加霜。

因此本刀做三件事：**把运行编码层改成位精确无损**、**让角色目录可以完全没有源 webm（资源契约）**、
**补上播放边界换后端与失败帧不发布**这三类真缺陷。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 无损编码档 v6 | `ENCODER_DESC = "libwebp lossless (bgra straight, unblend-white-v6)"`，`lossless=True, exact=True, method=2`；`exact` 是「透明区 RGB 不被改写」的前提 |
| 2 | 失败帧不再发布混合产物 | 一帧处理失败 ⇒ **整段作废**（旧行为是跳过该帧仍按同档发布） |
| 3 | 播放边界换后端 | `movie(..., allow_swap=True)` 只在 `bind_clip`（旧 clip 已 stop）时换代；其它调用永远拿原对象 |
| 4 | 无源视频资源契约 | `character.json` 索引 + `_load_all` 无源分支 + 按索引采纳（meta 帧数 + 磁盘帧数双核对） |
| 5 | 帧流水线缓存 | 有界并发解码 + 字节预算 LRU + 在途去重 + 读取租约（`pet/frame_pipeline.py`，尚未接线） |
| 6 | 分包下载 | 注册表解析 + HTTP Range 断点续传 + sha256/字节数双校验（`pet/pack_download.py`） |
| 7 | 验收链路 | `scripts/acceptance_metrics.py` 真机采样 + `scripts/verify_frameseq_bitexact.py` 全库位精确 |

**红线 / 不变量**

- 采纳闸门只看 `ENCODER_DESC`；改档 = 全部旧世代失效（既有迁移路径，本次沿用）。
- 同一 clip 的产物必须同属一条处理链：**不许发布混合档位/混合处理状态的整段**。
- 帧数契约不变：`meta.frames` 必须等于磁盘实际帧数。
- 不使用 `WEBP_QUALITY`（已删除并加断言锁死），防止有人把有损挂回来。

## 二、修改文件说明

`git diff --numstat`（相对 `147170f`）：

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/frameseq_provision.py` | +41 / −27 | ① `ENCODER_DESC`/`UNBLEND_MARK` 升到 v6 无损，v5 串进 `LEGACY_ENCODER_DESCS`；② `WEBP_QUALITY` 删除，改 `WEBP_LOSSLESS/WEBP_EXACT/WEBP_METHOD=2`；③ 阶段二落盘改 `lossless=…, exact=…`；④ **单帧失败改整段作废**（`convert_clip` 里 `skipped>0` ⇒ discard + 返回失败）；⑤ 17 处 docstring/注释从 Q70 改成无损，避免文档描述旧行为 |
| `pet/library.py` | +129 / −9 | ① `_find_pack_index`（只认两种布局，防串角色）+ `_load_all` 无源分支；② `rescan_frameseq` 按 `character.json` 采纳（meta 帧数 + `frames_on_disk` 双核对）；③ `movie(allow_swap=)` + `_movie_kinds` + `_retire_clip`；④ 无源且未采纳时抛明确 `KeyError` 而不是静默造空 clip |
| `pet/frameseq_clip.py` | +32 / −0 | ① 接入 `frame_timing` 观测（`_apply`/`_on_loaded`/`_advance` 等待分支）；② `_reanchor_timeline()` 统一重锚（起播/恢复/变速/跳帧），计划表用**有效**帧率 `fps × playback_speed` |
| `pet/pet_sprite.py` | +2 / −1 | `bind_clip` 传 `allow_swap=True`（播放边界才允许换后端） |
| `pet/app.py` | +7 / −0 | `PET_FRAME_TIMING_OUT` 设置时启动时间线周期落盘（未设置是纯分支） |
| `scripts/check_frameseq_bundle.py` | +2 / −2 | 构建门档串常量同步到 v6（门内常量必须逐字等于 provision，见 `test_frameseq_bundle_gate.TestGenerationHomology`） |
| `tools/convert_frameseq.py` | +3 / −4 | 删掉「热集无损 / 冷集 Q80」的历史描述（v6 一档到底），`--include` 只决定转哪些目录 |

### 新增

| 文件 | 行数 | 作用 |
|---|---|---|
| `pet/frame_timing.py` | 177 | 源帧时间线账本（计划时间/就绪/提交/落后/重复）+ 进程级注册表 + 周期落盘；`PET_FRAME_TIMING=1` 才开，关闭时零开销 |
| `pet/character_pack.py` | 258 | `character.json` 解析与校验（schema/字段/路径穿越防护/sha256 格式/分件可用性） |
| `pet/frame_pipeline.py` | 253 | 有界并发解码 + 字节预算 LRU + 在途去重 + 读取租约（**尚未接线**，见 §五） |
| `pet/pack_download.py` | 233 | 注册表解析 + Range 断点续传 + sha256/字节数双校验 + 半成品清理 |
| `scripts/verify_frameseq_bitexact.py` | 175 | 全库位精确验证：Qt 解码 vs 生产端规范 RGBA（参考走生产端同一条阶段一） |
| `scripts/acceptance_metrics.py` | 200 | 真机硬指标验收：拉起实例、进程树 CPU/私有内存采样、读时间线、出 PASS/FAIL |

### 测试

| 文件 | 改动 |
|---|---|
| `tests/test_frame_timing.py`（新，13 例） | 落后不被抹平、暂停/恢复重锚、变速不误判、重复提交检测、注册表有界、落盘原子性 |
| `tests/test_character_pack.py`（新，17 例） | 解析/校验/4 种路径穿越/重复名/分件过滤 |
| `tests/test_frame_pipeline.py`（新，12 例） | 命中/去重（8 请求 1 解码）/字节预算淘汰/租约保护/超预算不缓存/解码失败不杀线程/8 线程并发不死锁 |
| `tests/test_pack_download.py`（新，13 例） | **本机 HTTP 服务器**测真实 Range：续传、服务端不支持 Range 时从头写、sha/字节数不符删半成品、不可信 URL 拒绝 |
| `tests/test_library_sourceless.py`（新，4 例） | 无源包能建库能播、少帧不采纳、非法索引报错、不捡别人索引 |
| `tests/test_frameseq_provision.py` | 冻结串改 v6、`WEBP_QUALITY` 不存在断言、阶段二产物从「必须全有损」翻成「必须全无损」、单帧失败用例从「跳过仍发布」翻成「整段作废」 |
| `tests/test_convert_frameseq.py` | 同上（产物 VP8L 断言翻转） |
| `tests/test_frameseq_clip.py` | 新增「只在显式绑定时换后端」用例（+50 行） |
| 40 个测试文件 | 测试替身的 `movie()` 签名加 `**_kw`（接口新增关键字参数的连带影响，非逻辑改动） |

## 三、性能分析

### 3.1 四组基线的真实对比（本机，8 宠、60Hz 绘制、24fps、进程树口径）

诚实台架 `bench2/run.py`：计划时间表固定不重排，落后 = 计划应显示帧数 − 实际已显示帧数，
`paintEvent` 执行时间单独记。

| 源 | 进程树 CPU | 子进程 | tick p95 | p99 | max | >25ms | paint p95 | 未就绪 | **落后帧** |
|---|---|---|---|---|---|---|---|---|---|
| webm 流式（4.2.1） | 134.1% | 8 ffmpeg | 17.08 | 17.56 | 24.97 | 0 | 1.54 ms | 0 | **104** |
| Q70 帧序列（v4.3.4） | 85.8% | 0 | 16.98 | 17.64 | 19.19 | 0 | 2.00 ms | 0 | 0 |
| **独立无损帧（v6，本刀）** | **67.6%** | 0 | 17.34 | 18.32 | 19.66 | 0 | 1.86 ms | 0 | 0 |
| 无损差分 K=12（未采纳） | 93.5% | 0 | 17.22 | 18.07 | 24.22 | 0 | 1.85 ms | 0 | 0 |

结论：**无损档不仅画质位精确，CPU 还比现状 Q70 低 18 个百分点**（无损 WebP 解码比有损快），
并与 webm 流式打平（且没有 280 MB 的 ffmpeg 进程内存）。差分组省 39% 磁盘但多付 26 个百分点 CPU，
按用户「CPU 优先、磁盘够用即可」的口径**不通过闸门**（结案见 `GATE-diff-vs-lossless.md`）。

### 3.2 稳态开销 / 新增路径成本

- **播放路径**：本刀不改解码/绘制逻辑，只在 `PET_FRAME_TIMING=1` 时记账；关闭时全是 `None` 分支，
  无分配、无时钟读取、无新线程。
- **淘汰与共享**：`frame_pipeline` 尚未接线，**当前零运行时存在**。
- **生成/转换**：编码从 Q70（~45 ms/帧）变为无损 method=2（~83 ms/帧），**单 clip 约 +9 秒**；
  但差分图/无损档的**编码产物 IO 更少**（无中间态改动）。全库重出实测约 **1 段/分钟**。
- **新增系统调用/网络/线程**：`pack_download` 只有下载时才跑（当前未接线）；`frame_timing`
  的周期落盘线程仅在 `PET_FRAME_TIMING_OUT` 设置时启动（默认无）。

### 3.3 体积

| 项 | 数值 |
|---|---|
| 现状 Q70 素材 | 425 MB（17.1 KB/帧） |
| **v6 无损素材** | **1327 MB**（53.5 KB/帧，单 clip 12.6 MB） |
| 差分组（未采纳） | 813 MB |

## 四、实机运行记录

1. **真素材转换（本机真实素材树，非桩）**：`tools/convert_frameseq.py --force --include random,events`
   逐段重出；单段产物 241 帧 12.6 MB，`meta.encoder` 正确写 v6。
2. **位精确验证（干净单段）**：`scripts/verify_frameseq_bitexact.py` 对 `turn/东张西望`
   验证通过 —— **241 帧 0 不匹配、抽样最大差 0**。
3. **真机验收脚本冒烟**：`scripts/acceptance_metrics.py` 实跑真实应用实例 3 次，
   CPU 58–82%、私有内存增量 28–44 MiB；并因此抓到观测代码两个真缺陷（见下）。
4. **被抓到并修掉的两个观测缺陷**（台架发现不了）：
   - 暂停/恢复不重锚 ⇒ 假落后 `lag_frames=22425` 而只播 482 帧；
   - 变速/跳帧不重锚 ⇒ `max_lag` 258（修复后 **12**）。
5. **一次真实事故**：两个并发转换进程互相删 `.tmp`，日志成片 `No such file or directory`；
   **本轮新增的「整段作废」规则正确拒绝了发布混合产物**（`FAIL click\点击回应-元气挥手:
   阶段二有 8 帧处理失败，整段作废`）。
6. **门**：`ruff check pet tests scripts tools` 全绿；相关族 `pytest` **270 passed / 1 skipped**
   （帧序列、构建门、库构造、帧缓存、时间线、角色包、分包下载、webm 生命周期）。

## 五、未完成与待补（如实登记）

| 项 | 状态 |
|---|---|
| 全库 106 段 / 25,423 帧位精确验证 | **未跑完**：素材重出后台进行中，完成后立即执行 `verify_frameseq_bitexact.py` |
| 真机硬指标（8 宠 CPU/内存/间隔/停顿） | **未取得有效数据**：当前多数 clip 仍在 webm 回退路径（`timelines=0` 时脚本拒绝出判定）。等素材齐了重跑 |
| 角色管理 UI（分包下载的界面） | **未实现** |
| `frame_pipeline` 接线 | **未接线**。理由：实测预取线程 8 宠下约 38% 占用、不是瓶颈；接线会改变线程语义（回调跨线程 → 需 queued signal），风险与收益不成比例。保留为「多宠同段去重 + 字节预算」的后续优化，接缝在 `FrameSeqClip._request` / `_PrefetchWorker.prefetch` |
| 全量 pytest | **未跑**：为避免与后台重出抢 CPU 导致时序用例假红，等素材完成后再跑 |
| astra 终审 | **未做**（按约定：终审通过才推 PR） |
| 差分容器 | 按闸门结论不采纳，未实现（候选条件见 `GATE-diff-vs-lossless.md`） |

## 六、风险与回滚

- **回滚**：`git revert` 本提交即可。盘上 v6 世代会因档位不符**不被采纳**并回退 webm 播放，
  后台按旧档重出——两个方向都只走既有迁移机制，无需手工清理目录。
- **风险**：① 素材体积从 425 MB 涨到 1327 MB（这是「保真」的代价，方案里已与用户确认「磁盘换画质」）；
  ② `character.json` 是新增资源格式，旧包没有它时**行为完全不变**（走 webm/gif 扫描）；
  ③ 无源包路径是本轮新增能力，已在 4 个用例上验证，但**尚未在真实下载包上端到端验证**（依赖 §五的 UI 与分包）。

---

## 七、第二轮：astra 终审结论与本轮修复（2026-10-19 追加）

**终审结论：不通过。** 列出 9 个阻塞推 PR 的缺陷（B1–B9）。逐条处置如下，**未修的不写成已修**。

| 编号 | 缺陷 | 处置 |
|---|---|---|
| **B1** | `names()` 返回 `list(self._paths)`，无源包 `_paths` 为空 ⇒ **应用拿到零个动画** | **已修**：口径改为「声明了 **且** 素材可用」（在 `_frameseq_dirs` 或 `_paths`）；补 2 条回归（可播放、未通过校验的不进池） |
| **B2** | 无源采纳只比帧数，**不校验档位/内容/连续帧** ⇒ 旧 Q70 目录可被交给无损播放器 | **已修**：新增 `character_pack.admissible()` 失败关闭校验（严格 int 帧数、`encoder` 必须等于当前档、fps/画布/`content_sha`、帧文件必须严格连续 `f_0001..N`）；9 条测试 |
| **B3** | `SharedCharacterMedia` 不带包索引 ⇒ 兄弟库重扫清空共享映射后无法重建 | **已修**：`pack_index`/`pack_root` 进共享视图并在采纳时恢复；「两库、任一库重扫」回归 |
| **B4** | `_retire_clip()` 按 `("stop","close","cleanup")` 且首个可调用即 break ⇒ **`close()` 永不执行**，预取 worker 不走队列屏障就被 `deleteLater` | **已修**：顺序改为 `close → cleanup → stop`（`FrameSeqClip.close()` 才含队列屏障与线程销毁协议） |
| **B5** | 换后端只比 `frameseq/webm/gif` **类型** ⇒ 同类型换世代永不换；legacy 窗口未接 | **部分修**：身份改为 `(后端, 资源身份)` 并补「同类型换世代必须换实例」回归。**legacy/capture 窗口路径仍未接**（`window.py:1570` 仍用默认 `movie()`），登记为遗留 |
| **B6** | `frame_timing.stats()` 用了不存在的 `submit_at`（不在 `__slots__`、未初始化）⇒ **任何 `stats()` 必崩**；周期落盘线程吞异常导致"没数据"被误读成"全走 webm" | **已修**：改为 O(1) 的 `first_at`/`last_at`；13 条时间线测试重跑通过。**这是本轮自查失职**：加 `window_s` 后没有重跑测试 |
| **B7** | 验收脚本只定义不判定 p95/p99/停顿；缺指标也 PASS | **已修**：产品侧加 16ms 真实间隔探针（`frame_timing.note_tick` + `app.py` 一个只观测的 QTimer）；脚本逐项判 p95≤18ms / p99≤20ms / ≥100ms=0，**缺任何必需指标判 `INVALID`**（退出码 3），不再 PASS |
| **B8** | 位精确脚本缺世代目录时 `continue`，只验到一段也报"全部位精确" | **已修**：缺世代/缺源/档位不符一律判不完整；`--partial`（含 `--limit`）明确输出 `PARTIAL`，不承载全库门禁 |
| **B9** | 交付证据未达硬门（全库位精确、真机硬指标、全量 pytest、终审） | **未完成**：见 §五；等素材重出完成后依次执行 |

### 本轮追加的自查结论

- **B6 是本轮最严重的一条**：它证明"改了代码没重跑相关测试"会立刻产生必崩的路径。已把该模块的测试重跑纳入每次改动后的固定动作。
- **真机验收必须在空闲机器上跑**：本轮在 4 个转换分片占 2.4 核时跑过一次，得到 CPU>100%、间隔 p95/p99 超标的结果——那是测量污染，不能作为判定依据。

---

## 八、第三轮：交付证据补齐（2026-10-20 追加）

§五 里挂着的三项证据**已全部产出于本机真实环境**（不是台架估算）。

### 8.1 全库位精确验证 ✅

`python scripts/verify_frameseq_bitexact.py --jobs 4`

```
合计 25423 帧 / 106 段，31.7 分钟：全部位精确
```

逐段逐帧：**Qt 解码重建 == 生产端规范 RGBA**（参考走生产端同一条阶段一 `-pix_fmt bgra → 无损 webp`，
不是 rawvideo——两条路径的 YUV→RGB 取整不同，实测同一帧差 2572 字节，用错口径会把「管线没错」误判成不位精确）。
缺世代目录 / 缺源 / 档位不符一律判不完整，抽样模式必须显式输出 `PARTIAL`（审计项 B8）。

### 8.2 全量 pytest ✅

```
8463 passed, 15 skipped, 3123 warnings in 1644.66s (0:27:24)
```

### 8.3 真机硬指标 ✅（空闲机、真实应用实例、90 秒窗口）

| 指标 | 门槛 | 实测 | 判定 |
|---|---|---|---|
| 进程树 CPU | ≤100% 单核 | **23.7%** | ✅ |
| 播放媒体增量私有内存 | ≤128 MiB | **2.5 MiB** | ✅ |
| 绘制间隔 p95（稳态） | ≤18 ms | **17.53 ms** | ✅ |
| 绘制间隔 p99（稳态） | ≤20 ms | **18.66 ms** | ✅ |
| 稳态 ≥100 ms 停顿 | 0 | **0**（max 37.57 ms） | ✅ |
| 到期帧未就绪 | 0 | **0** | ✅ |
| 落后帧（`max_lag`） | 0 | **1** | ✅ |

**启动段单独报告、不隐藏**：1 次 **866.92 ms** 停顿，发生在应用启动期
（`MovieLibrary` 构造实测仅 218 ms，其余在 QApplication/主题/窗口/托盘初始化）。

### 8.4 为了让上面这组数字**可信**，观测口径改了四轮

| 现象 | 根因 | 修法 |
|---|---|---|
| `lag 243` | **闲置降帧**（产品有意把 idle 降到 12fps）被当成落后 | 识别持续减速 → `throttled`，单独报告不计入掉帧门禁 |
| `lag 730` | 播放速率被外部改动（飞行倍率等）后账本没重锚 | 每个 tick 开头对齐有效帧率，换速率的第一拍不再用旧帧率算计划 |
| `lag 243` 残留 | **>1 秒的提交空档**（挂起/停表）被算成落后 | 空档即恢复边界：重锚 + 记 `long_gaps` |
| `max_lag 8~19` | 起播头几帧的固有偏移 | 头 4 帧不计落后（建立节奏过程） |
| 「未就绪」把换段误计成掉帧 | 旧帧顶着 vs 真无图混为一谈 | 拆成 `miss`（无图，计门禁）与 `start_waits`（旧帧顶着，独立报告） |

**口径纪律**：`lag` 只统计 >1 帧的落后（每帧必然晚 <1 帧是定时器采样与帧间隔的必然结果）；
硬指标判**稳态**（跳过启动 5 秒），启动段数字照常输出。

### 8.5 仍未做（如实登记）

| 项 | 状态 |
|---|---|
| 差分 K=12 | 按实测闸门**不采纳**（省 39% 磁盘但多付 26 个百分点 CPU，且比现状 Q70 还高）；结案与重新启用条件见 `GATE-diff-vs-lossless.md` |
| `frame_pipeline` 接线 | **未接线**。它是为差分的顺序重建写的（有界并发 + 截止时间优先级）；差分不采纳后独立无损只要一句 `QImage(路径)`，单线程预取实测 8 宠下约 38% 占用，接线无收益。模块保留且带 16 条测试，但**产品路径不依赖它** |
| legacy/直播捕获的「运行中热升级」 | 已知限制：捕获模式下若某 clip 已被缓存成 `WebMClip`（素材当时未就绪），帧序列到货后本次运行不升级，重启生效。**不影响直播捕获本身**（`movie()` 只要帧序列在册就建 `FrameSeqClip`），也不影响画质 |
| 角色管理 UI 的下载源 | UI 已落地并有端到端测试（本机 HTTP → 下载 → 校验 → 安装 → 库加载），但**没有配置真实下载源**（需要在 GitHub Release 发布分件资产并回填注册表 URL）；未配置时页面明确提示，不静默 |

---

## 九、第四轮：终审（第二轮）问题闭环（2026-10-20 追加）

第二轮终审判「不能推」，列 9 条 + 分包三处。逐条处置如下（**未闭环的照实写**）。

### 9.1 已闭环

| 项 | 问题 | 处置 |
|---|---|---|
| **B1** | `names()` 过滤了，但 `folder_files` 没过滤 → 动作池/菜单仍含不可播放条目 | `folder_files` 与可播放集合取交集（`_prune_to_playable`）；抽出 `_apply_pack_index` 单一实现，并加 `refresh_playable_pool()` 让**运行中装好的新段立刻进池**（原来要等重启）。两条回归：坏条目不进池、新装段立刻可用 |
| **B2** | 内容摘要只比"meta 自己声明"的，被篡改但没摘要照样采纳 | **失败关闭**：`meta.content_sha` 缺失即拒收；安装时把已校验的摘要写进 meta（不重算全量帧，避免 1.3GB 启动读放大）；补 `fps` 有限正数检查（NaN 不再能过） |
| **B7** | `start_waits` 把「播放中缺帧」记成「开场晚一拍」——**把冻结改写成人眼不可辨** | 拆成四类：`miss`（空白）/ `deadline_miss`（播放中缺帧，**该判 0**）/ `start_waits`（本段还没显示过帧）/ `max_stale_ms`（旧帧持续多久）。验收的「未就绪=0」改为 `miss + deadline_miss` |
| **B8** | `missing` 收了但没参与结论，`--partial` 没用 | 现在输出 `期望/选中/缺世代/非当前档`；过滤模式一律 `PARTIAL`；缺世代或非当前档判不完整。实测 `--clip` 已正确输出 PARTIAL |
| 安全 | `part.json` 的 `clip.dir` 未校验就 `rmtree` → 可越界删目录 | 复用 `safe_rel_dir` + `resolve()` 后路径组件级包含判断（不用 `startswith`）；回归：目标外文件必须存活 |
| 事务 | core/extra 并发无锁；逐 clip 先删后移有空窗；崩溃留残留 | 每版本目录一把跨进程锁（过期可接管）+ **先改名后落地**（`dst → __old__ → 新版 → 删 __old__`）+ `recover_target()` 崩溃恢复（宁可恢复旧素材也不让动画消失） |
| 发现链 | UI 装到 `<id>/<版本>/`，而解析只认 `<id>/videos` | `catalog.character_pack_dirs()` + `resolve_character_video_dir()` 支持角色包布局且**高版本优先**；安装根目录改用外部角色根（可写、发现链认的那一个）。回归**不注入 asset_dir**，走真实解析链 |

### 9.2 未闭环（如实登记）

| 项 | 现状 |
|---|---|
| **B5 内容身份** | 身份仍是 `(后端, 路径)`。分包改为**版本目录**后，「同路径换内容」不再发生（新版本 = 新路径 = 身份变化），但「同一路径内容被外部改写」仍检测不到——这条依赖 B2 的 meta 摘要（已加，但只有装包路径会写） |
| legacy / 直播捕获的热升级 | 未接线（已知限制：捕获模式下某 clip 已缓存成 WebMClip 时，帧序列到货后本次运行不升级） |
| `frame_pipeline` | 仍未接线。它是为差分的顺序重建写的；差分不采纳后单线程预取实测 8 宠约 38%，接线无收益 |
| 装配层回调 | **已接线**：`PetApp._create_library` 把「刷新所有活库」注册进设置页钩子（`character_packs_page.register_refresh_hook`），装完即刷、不必重启；本轮又补了缓存失效，见第十节 |

---

## 十、第五轮：装配层缓存失效（2026-10-20 追加）

### 10.1 问题

`refresh_playable_pool()` 会把三张表（manifest / folder_files / frameseq_dirs）换成新对象，
但两处**下游缓存**没跟着失效，装完新分件仍要重启才生效：

| 缓存 | 失效后果 |
|---|---|
| `SharedCharacterMedia`（同角色只读媒体视图） | 刷新后注册表里仍是旧对象；**之后新建**的同角色兄弟库采用陈旧表 → 多宠下「新装的段只在一个宠物身上生效」 |
| `SpriteBehavior._cats_cache`（按库对象的分类池） | 手动菜单/自动行为的分类池永不重建 → 新段播不到、播不动的段被反复选中 |

### 10.2 处置

- `MovieLibrary.playable_stamp`：`_prune_to_playable` 每收敛一次 `+1`，作为「可播放集合版本号」。
- `MovieLibrary._sync_shared_media()`：刷新后把新表写回共享视图；已被注册表取代的旧视图不回写（防旧覆盖新）。
- `SpriteBehavior._categories`：缓存改成 `(stamp, cats)`，版本号不一致就重建。

### 10.3 证据

| 项 | 结果 |
|---|---|
| 回归（库侧） | `test_refresh_syncs_shared_media_and_bumps_stamp`：版本号上涨、共享视图含新段、**后建的兄弟库** `names()` 含新段 |
| 回归（行为层） | `test_categories_cache_invalidates_on_playable_stamp_change`：版本号变 → 重建；同版本 → 命中缓存（不每次重建） |
| 红-绿验证 | 屏蔽 `_sync_shared_media`、去掉版本号判定后，两条回归**各自变红**；恢复后变绿（本机实测，输出见下） |
| 相关测试族 | `pytest tests/test_library_sourceless.py tests/test_sprite_behavior.py tests/test_library_shared_media.py tests/test_overlay_hardening.py tests/test_sprite_menu_parity.py` → **119 passed** |
| ruff | `ruff check pet tests scripts tools` → All checks passed |

红-绿原始输出：

```
① 无同步修复      -> 1 failed in 1.37s
② 无版本号判定    -> 1 failed in 1.57s
已还原 sprite_behavior.py: True
```

### 10.4 仍未覆盖（如实登记）

- **legacy / 直播捕获拓扑**的 `PetWindow.cats` 仍在窗口构造时算一次；该拓扑下运行中装包，菜单要重启才更新（overlay 拓扑逐次经 `behavior._categories` 取，不受影响）。
- 菜单对「未安装的段」的做法是**不显示**（`folder_files` 已与可播放集合取交集），没有做「置灰 + 提示」；`core` 之外还有什么可下载，由设置页「角色与素材」负责告知。点未安装段不会崩（`bind_clip` 不抛），但也不该在菜单里出现——现在不会了。

---

## 十一、第六轮：安装事务日志与启动恢复（2026-10-20 追加）

### 11.1 问题（astra 第 1 项）

安装是「逐个 clip 改名落地 → 合并 ``character.json``」的多步操作。原实现只有
``recover_target()``，且**只在安装开始时**调用——崩在两次安装之间就没人修：

| 崩溃位置 | 后果 |
|---|---|
| 落地循环中途 | 某段停在 ``X.__old__``、新目录没就位 → 那段动画**消失**，直到下次装包才被还原 |
| 索引合并之前 | 帧都在磁盘上，``character.json`` 里没有 → 这批素材凭空空缺 |
| 无日志 | 启动时无法判断「上次装到哪一步」，只能干等下一次安装 |

### 11.2 处置

- ``JOURNAL_NAME = ".install-journal.json"``：安装前写日志（``phase=place``，含 ``part.json`` 副本），
  全部落地后改 ``phase=merge``，索引合并成功才删除；写入用 tmp + ``os.replace`` 原子替换。
- ``recover_target()`` 收尾接 ``_roll_forward_journal()``：``phase=merge`` → **前滚**
  （把磁盘上确实就位的段并进索引）；其它阶段 → 只还原 ``__old__``、**不合并**
  （宁可让用户重下，也不装半套）；无论哪种都删日志，同一份事务不会被收第二次。
- 就位判据用安装时写进 ``meta.json`` 的 ``content_sha`` 凭证，**不重算全量帧摘要**
  —— 启动恢复不许变成一次全库读放大。
- ``recover_installs(target, timeout=2.0)``：启动期入口。无日志 → 一次 ``is_file()`` 返回；
  有日志 → 抢短超时锁（有安装正在跑就跳过，它自己会收口）。
- 接线：``MovieLibrary._load_all`` 找到角色包索引后、``_apply_pack_index()`` 之前收口；
  发生前滚则重读索引，让新段当次生效。

### 11.3 证据

| 用例 | 验证点 |
|---|---|
| ``test_install_part_leaves_no_journal`` | 装成功不留日志 |
| ``test_recover_installs_rolls_forward_pending_index_merge`` | 声明两条、只落地一条 → 只前滚 1 条；再收一次是 no-op |
| ``test_recover_installs_restores_old_dir_when_placement_crashed`` | ``phase=place`` → 旧目录还原、索引不写 |
| ``test_recover_installs_no_journal_is_a_noop`` | 零日志零成本：不建锁、不碰索引 |
| ``test_recover_installs_skips_when_install_in_flight`` | 持锁时跳过、日志不动 |
| ``test_startup_recovery_commits_crashed_install``（库侧端到端） | 建库即前滚，该段进 ``names()`` 与动作池 |

红-绿（本机实测）：``_roll_forward_journal`` 打成 no-op → 前滚用例红；
``recover_installs`` 打成 no-op → 库侧端到端用例红。恢复后
``test_pack_download.py`` **34 passed**、``test_library_sourceless.py`` **9 passed**，
``ruff check pet tests scripts tools`` 全过。

### 11.5 坏输入加固（同日补）

事务日志是磁盘上的文件，用户可能断电/杀进程留下半个 journal，或手改坏。旧实现里
`part_doc["clips"]` 若不是 list，`clip.get` 会抛 `AttributeError`，一路穿到
`MovieLibrary._load_all` —— **素材坏了不该让整个应用起不来**。现在三层防守：

1. `_clips_on_disk`：`clips` 不是 list → 返回空；元素不是 dict → 跳过；`dir` 不是字符串 → 跳过；
2. `_roll_forward_journal`：前滚整体 `try`（`OSError/ValueError/TypeError` → 记 warning、跳过合并，
   素材不动）；日志读不出来 → 记 warning 后清理；
3. `MovieLibrary._load_all`：收口整体 `except Exception` → 记 warning 按未收口继续起库。

回归：`test_recover_installs_survives_corrupt_journal`（垃圾 JSON 与 `clips: "oops"` 两种）。
红-绿实测：把上面两处守卫**同时**撤掉 → 用例红；恢复后绿（只撤一处仍绿：两层防护重叠，
该用例证明的是这一对，不是单层）。

### 11.4 仍未覆盖（如实登记）

- 崩在**解压阶段**（日志还没写）只留 ``.install-*`` 暂存目录，仍由下一次安装清掉；
  启动期不扫它——为了省掉每次启动的全库遍历。
- 前滚只提交「磁盘就位且摘要对得上」的段；缺的那几段不会写进索引，下载页因此仍认为
  该分件没装完（这正是我们要的语义）。
---

## 十二、第七轮：验收口径对齐（2026-10-20 追加）

终审第 3 项点名的三处**测量口径**错误，逐条改掉。这一节改的是「怎么算合格」，
不是产品行为——口径错了不会让产品变坏，只会让不合格看起来合格。

### 12.1 降帧判定：从「反推」改成「产品显式声明」

原实现：`throttled = slow_presents > presented / 4`（提交明显慢于标称的比例过半）。
两个方向都会错：真卡顿被当成"有意降帧"豁免掉；产品明说自己在慢放时反而可能漏判。

现在：

- `FrameTimeline.note_pace(ratio)`：播放器在重锚时间线时**显式**写入 `playback_speed`；
  `paced_down = ratio < 0.999`，`stats()["throttled"]` 只读这个字段。
- 原来的反推口径降级成**可疑信号** `slowdown_conflict`（产品说原速、实际一大半提交慢
  于标称），进验收门禁 `无未申报降速 = 0`。

### 12.2 内存基线：从「启动后 8 秒」改成「素材就位之前」

原实现：进程起来 8 秒后才取基线——那时素材早已常驻，等于把常驻媒体内存从账上抹掉，
于是怎么测增量都接近 0（上一份验收写的 2.5 MiB 就是这个口径的产物）。

现在：

- 产品在库加载完成时打点 `frame_timing.mark_media_ready()`，落盘带 `media_ready_s`
  与 `media_ready_wall`（墙钟，供跨进程对齐）。
- 脚本从进程启动起 **0.5 秒一次细采样**（默认前 12 秒），覆盖"素材就位"那一拍，
  之后转 5 秒一次。
- 口径：`媒体增量 = 就位之后的峰值 − 就位之前最后一次采样`；同时分项报告
  `residency`（常驻媒体）与 `growth`（播放期增长）。
- 拿不到打点 → `baseline=fallback-first-sample`，并让 `内存基线可信` 判 **False**
  （状态直接 INVALID），不许拿旧口径冒充合格。

### 12.3 `max_stale_ms` 从「只报告」升级为门禁

`deadline_miss` 只说"缺了几次"，`max_stale_ms` 说"缺得多难看"（旧帧顶着的时长）。
门槛 **200 ms**（24fps 下 5 帧，超过就是肉眼可见的画面停住）。

### 12.4 证据

| 用例 | 钉住的东西 |
|---|---|
| `test_throttled_follows_declared_pace_not_slow_presents` | 未声明降速 + 全慢提交 → `throttled=False` 且 `slowdown_conflict=True`；`note_pace(0.25)` 后正好相反 |
| `test_playback_speed_is_declared_to_timeline` | 播放器 `set_playback_speed(0.25)` 必须把倍率写进账本（接线断了这条就失效） |
| `test_media_ready_marker_is_dumped_once` | 打点幂等、落盘带秒数与墙钟 |
| `test_memory_baseline_is_before_media_ready` | 同一组采样：新口径 97 MiB，**旧口径报 0 MiB**（断言里直接算了旧口径作对照） |
| `test_memory_baseline_falls_back_loudly` | 无打点/窗口没覆盖 → 明确标注，不冒充合格 |
| `test_max_stale_ms_is_gated` | 阈值存在且判定方向正确 |
| `test_new_gates_are_wired_into_checks` | 三条新判定行真的进了 `checks`（只加阈值不算接线） |

红-绿（本机实测）：把 `throttled` 改回反推 → ① 变红；删掉 `note_pace` 调用 → ② 变红；
落盘丢掉 `media_ready_wall` → ③ 变红。恢复后
`test_frame_timing.py + test_frameseq_clip.py + test_acceptance_metrics.py +
test_architecture.py + test_flight_frame_pacing.py + test_idle_low_fps.py +
test_perfstats.py` → **137 passed**，ruff 全过。
---

## 十三、第八轮：预取窗口与真机定位（2026-10-20 追加）

换成诚实口径（第十二节）之后，真机第一次跑就 **FAIL**——而且这次失败是**真的**，
不是口径噪声。这一节记的是怎么从「指标红」一路挖到根因，以及最后的产品改动。

### 13.1 失败链（每次改动只动一处）

| 跑 | 改了什么 | 未就绪 | 旧帧最长 | 累计落后 | max_lag | 空档 | 结论 |
|---|---|---|---|---|---|---|---|
| 14 | 新口径首跑 | >0 | — | — | — | >0 | 无 timing 明细（脚本漏写字段），先补明细 |
| 15 | 补 `timing` | 3 | 44.6 | 2451 | 16 | 1 | 落后集中在**首段 idle**：10 秒正好交 240 帧（24fps 正确），却记落后 3144 |
| 16 | 首帧锚点 + 停播声明 | 16 | 141.9 | 565 | 6 | 0 | 首段噪声消掉，暴露**真**落后：`碎碎念` 段 11 次未就绪、旧帧顶 141.9ms |
| 17 | 预取窗口 3 帧 | 6 | 43.2 | **0** | **0** | 0 | 落后清零；剩下每次都是「顶一拍」（≈一个帧间隔） |
| 18 | 末帧预取下一圈首帧 | 7 | 42.9 | 0 | 0 | 0 | 接缝本身不是主因（数量没降） |
| 19 | 窗口 6 帧 | 3 | 43.0 | 0 | 0 | 0 | 窗口更大能再压，但没到 0 |
| 20/21 | 加「缺帧时刻」诊断 | 2 / 7 | 42.7 | 0 | 0 | 0 | **决定性证据**：`miss_at` 全部是 `[0.04s, 帧1]` |
| 22 | 补 start 的「首帧已在手」分支 | **0** | **0** | **0** | **0** | 0 | 全绿 |
| 23 | 窗口收回 3 帧复核 | **0** | **0** | **0** | **0** | 0 | 全绿（少驻留 3 张解码帧） |

### 13.2 根因与改动

| 改动 | 根因 | 位置 |
|---|---|---|
| **预取窗口 3 帧**（原为链式 1 帧） | 只提前一帧时，任何慢过一个帧间隔（41.7ms）的解码都会变成「到期帧未就绪」；实机 90 秒 16 次、旧帧最长顶 141.9ms | `frameseq_clip.PREFETCH_LOOKAHEAD` / `_request_window()` |
| **末帧上屏即预取下一圈首帧** | 循环接缝处最容易顶一拍；多解一帧（≈0.9MB，`stop()` 会清）换接缝无缝 | `_advance()` 末帧分支 |
| **`start()` 的「首帧已在手」分支补窗口** | 该分支原来**不排任何请求**：上一轮加了接缝预取后帧 0 常在手，于是换段后第一个 tick 必然等帧 1 —— 实机每次换段记一次未就绪（`miss_at` 全部 `[0.04s, 帧1]`） | `start()` |

顺带修掉两处**观测口径**问题（第 12 节没覆盖到的、真机跑出来才暴露的）：

- **首帧锚点**：计划表原点原来是「clip 创建时刻」，首帧到货前的等待（冷盘/预热）
  被永久记成播放落后（首段 idle 累出 3144 帧的假落后）。改成播放器在**首帧真的
  上屏**那一刻调 `FrameTimeline.anchor()`——只挪原点、不碰任何计数（计数里
  `start_waits` 才是"首帧晚到"的正确表达）。
- **停播声明**：`long_gaps` 的语义是「可见且正播着的时候 >1 秒没有新帧」= 真冻结。
  一段动作播完之后宠物停在末帧等人调度，那几秒不是冻结；现在播放器在
  `stop()` / `pause()` / 播完时调 `FrameTimeline.note_stop()` 显式声明。

### 13.3 最终硬指标（acceptance-23，空闲机、真实实例、90 秒、9 条时间线、2099 帧）

| 指标 | 实测 | 门槛 |
|---|---|---|
| 到期帧未就绪（miss + deadline_miss） | **0** | 0 |
| 最坏落后 | **0** 帧 | ≤3（额外自检） |
| 稳态旧帧最长 | **0 ms** | ≤200 |
| 稳态 ≥100ms 停顿 | **0** | 0 |
| 间隔 p95 / p99（稳态） | **17.29 / 18.46 ms** | ≤18 / ≤20 |
| 进程树 CPU（稳态） | **21.8%** 单核 | ≤100% |
| 媒体增量私有内存 | **87.2 MiB**（常驻 13.5 + 播放期增长 73.7；基线取素材就位之前） | ≤128 MiB |
| 结论 | **PASS** | |

启动期单独报告：全窗口 1 次 496.6ms 停顿（应用启动开销，不在稳态门禁内）。

### 13.4 注意（如实登记）

- 媒体增量从旧口径的 2.5 MiB 变成 87.2 MiB，**不是变差了，是以前没算常驻部分**。
  其中 73.7 MiB 是播放期增长（首帧/解码缓存与预热），90 秒全程稳定在 144~147 MiB
  不再上涨（无泄漏）。
- 预取窗口 6 帧也实测过（acceptance-22 全绿），但指标没有更好、每段还多驻留
  3 张解码帧，所以最终取 3。
- 这三次失败全部是**测量与预取的交互**问题，素材本身位精确、解码正确。
- **8 宠 CPU 的真机测量是本轮遗留**：判据要求「8 宠稳态进程树 CPU ≤100% 单核」，
  本轮所有真机跑都是**单宠**（21.8%）。8 宠的数字（67.6%）来自更早一轮的对照基线
  （报告 §3.1），本轮改动不改每帧解码次数（预取窗口只是把同样的解码提前，不增加
  总量；观测打点关闭时零开销），所以理论上只会持平或更好——但这属于**推理，不是测量**。
  补测方法：在应用里开 8 只宠（或 `--pets 8` 口径的台架），机器空闲时跑
  `python scripts/acceptance_metrics.py --seconds 120 --pets 8`，门禁会强制要求
  至少 8 条跑够样本的时间线，否则判 INVALID。
---

## 十四、第九轮：第四轮终审 12 条阻塞的处置（2026-10-20 追加）

第四轮盲审给了 12 条 P1 + 1 条 P2，结论是**不能推**。逐条处置如下（**没有一条是靠
调阈值过的**；每条都有红-绿回归，且都先证明旧行为是错的）。

| 编号 | 问题 | 处置 | 回归 |
|---|---|---|---|
| B1 | 安装完成的刷新钩子**跨不了进程**：设置页是独立进程（`--settings`，禁止导入 `pet.app`），同进程 `_REFRESH_HOOKS` 永远没有注册者 | 新增跨进程指令 `CMD_CHARACTER_PACK_REFRESH`（复用既有 claim/ack 指令通道）；页面装完同时发同进程钩子与跨进程指令；legacy 与 overlay 两个消费端都接线 | `test_page_registers_cross_process_notifier`（真写指令文件）、`test_pack_refresh_command_round_trip` |
| B2 | 就算通知到了，**首次装包/版本更新也切不过去**：刷新只重读旧 `_pack_root` | 新增 `MovieLibrary.reload_media_root()`：重走发现链，根变了就换素材目录、就地清空共享帧映射、重加载并重新发布共享视图；在播 clip 由 `movie(allow_swap=True)` 在播放边界换代 | `test_reload_media_root_switches_to_new_version`（v1 → v2 真换根） |
| B3 | 启动恢复漏**首次安装**；前滚失败仍删日志 | ①新增 `catalog.character_pack_pending_dirs()`（有事务日志、无索引的目录），起库前先收口；②`_roll_forward_journal` 失败**保留日志**等重试 | `test_first_install_crash_is_recovered_before_discovery`、`test_roll_forward_failure_keeps_journal` |
| B4 | 事务会破坏已装素材：落地即删旧目录、meta 非原子写、`phase=place` 无回滚、锁按年龄抢活锁 | 旧目录留到**索引提交之后**才删；meta 改 tmp+`os.replace`；`phase=place` 恢复改成**回滚**（删未提交新内容、还原旧目录）；锁只在"确认持有者已死"或"判不出死活且超龄"时接管 | `test_placement_crash_rolls_back_to_old_material`、`test_index_commit_failure_keeps_rollback_material`、`test_lock_is_not_stolen_from_a_live_holder` |
| B5 | 显示槽复用首帧不记账；冷启动会**跳过帧 0**（甚至单帧素材先发 finished） | `_img_frame == 0` 分支也走 `_apply`（记账+锚点）；`_advance` 在首帧未上屏时把 `nxt` 锁在帧 0 | `test_display_slot_frame_zero_counts_as_shown`、`test_cold_start_never_skips_frame_zero`、`test_single_frame_clip_does_not_finish_before_showing` |
| B6 | 首帧等待只数次数、不记时长，旧帧顶几秒也能过 | 时间线记 `first_wait_ms`（含"仍在等"的进行中时长）；验收新增门禁 **首帧等待 ≤500ms** | `test_first_frame_wait_duration_is_gated`、`test_first_wait_still_in_progress_is_reported` |
| B7 | 声明过慢放的时间线被**整条豁免**，慢放期真冻结能掩盖 | 不再豁免：所有时间线照样判缺帧/旧帧/空档（计划已按 `fps × speed` 计）；`slowdown_conflict` 仅作可疑信号 | `test_paced_timeline_cannot_hide_a_real_freeze`（旧断言已改，它固化的是错口径） |
| B9 | 内存边界标记在库构造与预热排期**之后** | 改名 `mark_media_load_start()` 并挪到**构造素材库之前**；脚本按"加载开始之前"取基线 | `test_late_boundary_would_hide_the_growth`（同一组采样：正确边界 100MiB vs 取晚 30MiB） |
| B10 | 分包漏 `text_clips.json` / `move_strides.json` → 文字反显、移动丢步幅 | core 分件打包带上两个 sidecar；安装端与 manifest 同一处原子落地 | 打包/安装用例（见 `test_pack_download`） |
| B11 | 只装 extra 的版本也能成为最高优先级活动包（顶掉完整包） | `character_pack.core_gaps()` + 发现链只留 **core 闭环**版本（无 core 段也算不闭环），不达标只记 warning 不参与发现 | `test_extra_only_version_never_becomes_active` |
| B12 | 配置真实来源后，打开设置页会**在 GUI 线程联网** | 注册表拉取移到后台线程（`_RegistryLoader` + `QLabel` 占位），失败/成功都 queued 回 GUI；响应体积上限 1 MiB；对象析构时 emit 异常吞掉 | `test_build_content_never_blocks_gui_on_registry`、`test_registry_fetch_is_size_capped` |

**红-绿**（本机实测，逐条撤销新守卫 → 对应用例变红）：B5 两条、B3 两条、B4 四条、
B9 一条、B11 一条、B7 一条。受触区测试族 **558 passed**，ruff 全绿。

证据对账（终审指出的报告错误）：§13.3 的 p95/p99 已从 `17.41/18.68` 改为
**`17.29/18.46`**（以 `acceptance-23.json` 原始值为准）。

## 十五、仍未闭环（下一轮）

| 项 | 说明 |
|---|---|
| **B8 验收对象** | ①p95/p99 来自 16ms 探针定时器，**不是真实绘制路径**；②`--pets 8` 数的是"累计时间线条数"，单宠轮播九段即可满足，**不是八宠并发**。修法要产品侧在真实绘制路径打点 + 记录"同时在播的宠物集合与时间区间"，再补八宠真机验收。这是本轮唯一剩下的 P1 |
| app_min 准入 | 注册表里的 `app_min` 仍只解析不用于准入（终审说是次要项，本轮未做） |
| `_sync_shared_media` 优先级 | 共享视图的 `priority` 表未随之更新（新增交互 clip 的预热优先级）——已登记，随预热整理一起做 |
| 8 宠 CPU | 仍旧只有单宠真机（21.8%）+ 历史基线推理，见 §13.4 |
---

## 十六、八宠真机首测：**不达标**（2026-10-20）

B8 修完之后第一次真正测到「8 宠同时在线」（`acceptance-26-8pets.json`，`PET_AUTOSPAWN=8`
临时生成 8 只，不写档案、退出即消失；并发由真实 paint 统计：**max 8 / owners 8**，口径可信）。

| 指标 | 单宠（acceptance-25） | **8 宠（acceptance-26）** | 门槛 | 判定 |
|---|---|---|---|---|
| 进程树 CPU | 22.3% | **124.2%** | ≤100% | ❌ |
| 媒体增量内存 | 97.0 MiB | **142.8 MiB** | ≤128 | ❌ |
| 到期帧未就绪 | 0 | **1225 / 15901 帧（7.7%）** | 0 | ❌ |
| 最坏落后 | 0 帧 | **7 帧**（旧帧最长顶 122.5ms） | ≤3 | ❌ |
| 上屏延迟 p95 / p99 | 1.89 / 2.91ms | **4.33 / 7.26ms** | ≤18 / ≤20 | ✅ |
| 事件循环 p95 / p99 | 17.27 / 18.23ms | **19.65 / 22.40ms** | ≤18 / ≤20 | ❌ |
| ≥100ms 停顿 | 0 | 0 | 0 | ✅ |

**这是本轮最重要的结论，而且和我在 §13.4 里的推断相反**：我当时写"8 宠按推理只会持平
或更好"，实测是明确变差。单宠那套"预取窗口 3 帧"的余量在 8 宠下被吃光了。

### 已知事实与**尚未验证**的假设

事实：`_shared_thread` 是**一个进程级 QThread**，8 只宠的所有帧解码都排在它上面（每 clip
还各带 3 帧预取窗口 → 最多 24 个在途请求）。CPU 124% 说明进程已经在用超过一个核，
而上屏延迟只有 4.3ms —— 时间不是花在"等帧上屏"，而是花在**别的地方**。

假设（**都需要测量，不许当结论**）：

1. 单线程预取饱和：192 次解码/秒（8×24）串行排队 → 部分宠物等解码；
2. GUI 线程渲染成本：8 个 sprite × 24fps 的绘制 + 合成；
3. 内存增长来自 8 套首帧/解码缓存（`delta 142.8 = 常驻 19 + 播放期 123.8`）。

### 下一步（下一轮开工）

1. **先测再改**：给预取 worker 加"排队时长/解码时长"计数，给 paint 加"单次绘制耗时"，
   用 8 宠跑一次定位这 124% 到底花在哪（这两个计数只在 `PET_FRAME_TIMING` 下开）；
2. 按定位结果改：若是解码串行 → 预取线程池（多 worker + 队列）；若是渲染 → 降低
   多宠时的绘制频率/合并脏区；若是内存 → 收紧 per-clip 预取窗口（多宠时自适应变浅）；
3. 改完重跑八宠真机，**达标才谈推 PR**。

在 8 宠达标之前，本 PR 按终审口径**不能推**。
### 16.1 归因实测（acceptance-27，8 宠、120 秒）

加了两个**只观测**的计数器：worker 侧"单帧解码耗时"、sprite 侧"单次绘制耗时"。

| 项 | 实测 | 占一个核 |
|---|---|---|
| 解码累计 | **83.36 s**（20k+ 帧，p50 2.37 ms / p95 10.54 ms / **max 74.57 ms**） | **≈62%** |
| 绘制累计 | **4.42 s**（p50 0.048 ms / p95 0.091 ms） | **≈3%** |
| 进程树 CPU | 122.6% | — |

结论（这次是测出来的，不是猜）：**绘制不是问题，解码是**。单线程预取把 8 只宠的
192 次/秒解码串在一起，`p95 10.5ms` 的排队尾巴直接变成 `deadline_miss 1037`。

另一条关键事实：**复用率很高** —— 120 秒里 256 次播放只涉及 **23 个不同 clip**
（`被鼠标拖拽悬空反馈` 111 次、`待机呼吸休闲` 57 次、`东张西望` 29 次）。

### 16.2 两条候选改法（按证据排序，实施前不许下结论）

1. **预取线程池**（2~3 worker + 共享队列）：直接拆掉串行队列。预期消除
   `p95 10.5ms / max 74.6ms` 的排队尾巴；风险在 worker 生命周期（退役/销毁必须
   留在各自线程，仓里有 AV 前科注释）。
2. **跨宠解码帧共享**（`frame_pipeline` 的真实用途：按 `(clip 目录, 帧号)` 去重 +
   字节预算）：23 个 clip 被 8 只宠反复播，同一帧可能被多只同时要。
   注意：共用需要缓存窗口覆盖相位差，预算放不下整段 240 帧（216 MiB），
   所以收益要实测，不能想当然。
3. 备选：多宠时把 per-clip 预取窗口自适应收窄（3 → 1），同时降排队峰值与内存
   （当前 149 MiB > 128 门槛）。

**代码状态**：`PET_FRAME_TIMING=1` 时才记这两个计数器，产品路径零开销；
`PET_AUTOSPAWN` 是测量用逃生门（不落档案）。八宠达标前本 PR 不推。


---

## 十七、方向修订：分包改为「插件市场式 · 重启生效」（2026-10-20 追加）

**用户拍板的方向修订**，依据三方独立结论（用户原始诉求「像 dsh 插件市场那样下载好重启
就能用」、astra 第五轮盲审、k3 对 VPet/dshmarket 的实测调研，见
`.scratch/asset-arch/PACK-MARKET-DESIGN-v2.md`）：**热切换整链删除，激活改为重启生效**。
同时作废两条本轮早前自设的口径：「8 宠稳态进程树 CPU ≤100% 单核」不是用户需求（§16 的
八宠门槛随之解除）；`PET_AUTOSPAWN` 逃生门、`frame_pipeline`（死代码）、`frame_timing`
埋点（含渲染热路径打点）全部移除，产品代码恢复零观测埋点。预取线程池 1→3 保留
（用户点名的唯一性能改动）。

**删除**（热生效链）：`CMD_CHARACTER_PACK_REFRESH` 及 app/overlay 两个消费端、
`library.reload_media_root()` / `refresh_playable_pool()` / `refresh_live_libraries()`、
设置页 `_REFRESH_HOOKS` / `_IPC_NOTIFIERS` / `_wire_ipc_notifier`、每宠选择
（`read_selection` / `write_selection` / `catalog.resolve_selected_pack` / 库 `selection`
参数）。**保留**：`allow_swap` / `playable_stamp` / `_prune_to_playable` —— 它们还服务
运行时 webm→帧序列供给链（astra 第五轮明确点名不能顺手删）。

**新增/加固**（对应 astra 第五轮 7 条阻塞）：

- 安装根固定用户数据目录（`catalog.user_characters_dir()`），不再写 exe 旁/cwd；
- `core_gaps` 升级为真闭包：`CORE_REQUIRED_CATEGORIES`（idle/move/turn/click/drag）
  每类 ≥1 段 + 逐段 meta 摘要 + 磁盘帧数对账；
- 安装期「能播」验证（`_verify_clip_playable`）：帧连续、meta 自洽、**首尾帧真实解码 +
  画布与声明一致**——32×24 冒充 640×360 的包被拒（e2e 自己的假声明同步修正）；
- 不可变发行（`_check_release_identity`）：同路径不同内容拒绝、core/extra 不可互盖、
  幂等重装放行；
- 解包防线（`_validate_zip_entries`）：解包总字节/文件数/单文件上限、路径组件逐个校验
  （绝对/盘符/上跳/反斜杠/ADS/尾点尾空格/控制字符/Windows 保留名）、大小写折叠重名、
  符号链接、类型白名单（仅 `.webp`/`.json`）；
- 事务收口：sidecar（manifest/text_clips/move_strides）与 clip **同提交同回滚**
  （文件型 `__old__`）；`phase=merge` 前滚必须分件**完整**，缺段即回滚、不承认半个分件；
- 设置页市场化：默认官方清单（jsdelivr 三镜像，`packs-index.json` 随仓库 main 更新，
  **发新素材包不需要用户更新应用**）、远端∪本地合并视图（未下载/已安装/当前使用/
  `app_min` 不满足置灰）、磁盘不足预检、下载对象锁（`.dl.lock`）、清单地址过可信白名单、
  离线文案「当前离线，已安装素材不受影响」；
- 发布门（`scripts/build_character_pack.py`）：core 与闸门同常量、extra 按 ~256MB 切块
  （extra1…extraN，块名即 part 归属，与注册表/索引/页面三方一致）、构建后**真装进
  临时目录**走完整安装校验 + core 闭包（门不过不许发）、自动并入仓库根
  `packs-index.json`。

**实测**：真素材打包 core 11 段/137.7MB + extra1~5 各 ~250MB + extra6 30.5MB
（106 段/25,423 帧全 v6），发布门真实安装通过。聚焦测试 421 passed，全量 pytest 与
astra 第六轮复审见本节后续更新。

**仍未闭环**：

- 720p 几何按「角色」而非按「选中的包」取数（`pet/pet_sprite.py` 身体框限定 640×360），
  高清包落地前必须修（`PACK-MARKET-DESIGN-v2` §9）；
- 「有更新」检测（清单 version/sha 比 receipt）未做，等注册表格式冻结后补；
- 启动期坏包回退只在日志可见，设置页尚未标红（恢复页思路，后补）；
- Release 资产上传与 `packs-index.json` 提交是对外发布动作，**等用户点头**。

---

## 十八、审查循环终态：astra 六至九轮全闭环（2026-10-20 追加）

§17 的重构落地后，astra 又做了四轮盲审（第六轮 13 条、第七轮 9 条、第八轮 2 条 P1、
第九轮 1 条残余窗口），全部修复并有回归测试。关键条目：

- **第六轮**：设置页 `QObject→QWidget` parent 崩溃（清单返回即炸，测试没覆盖回调）；
  启动恢复对「有索引但 core 暂缺」的目录触发不到；锁接管竞态；新安装覆盖保留中的
  事务日志；续传 416 卡死；`part.json` 校验与运行时索引解析不同尺；发行身份可被
  「新名字指向旧目录」绕过；完整性不认「删帧留 meta」；`core_gaps` 缺 encoder/fps/
  帧数/连续编号；重定向链未逐跳校验；删素材后仍显示「已安装」；「使用这套」无反馈。
- **第七轮**：合法 ZIP 目录项被误拒（第六轮修复引入的回归）；`_TargetLock` 换成
  **OS 级互斥**（msvcrt/fcntl，进程死亡内核自动放锁，「死锁接管」整类消除）；
  恢复时序改为「先收口崩溃事务、再解析素材根」；预检默认值就地写回；路径别名
  （`./`/大小写）按规范化+小写键拒；place 阶段 sidecar 进日志；白名单补
  `release-assets.githubusercontent.com`；装完当页重建「已安装」区块（选择闭环）。
- **第八轮（两条 P1，数据丢失级）**：place 恢复按「计划名单」删 sidecar——刚恢复的
  旧 sidecar 被再删、尚未触碰的旧 sidecar 被误删。修法：sidecar **增量记录**
  （`"replaced"/"new"`，先记账后落地），回滚只删本次新放的。
- **第九轮**：残余窗口（新 sidecar 落地后、日志提交前崩溃漏删）→ 登记挪到落地**之前**；
  收尾：sidecar `.tmp` 复制中断残留进 `recover_target` 清理。

**证据**：每轮修复配回归测试（红-绿）；分包相关 107 条聚焦测试全绿；受影响面
300+ passed；发布门（真装 + core 闭包 + 运行时采纳闸门 + 包内帧率一致）真素材
三次实测通过；ruff 全绿。终跑全量 pytest 结果见 PR 描述。

**审查边界如实说**：astra 各轮为只读静态评审；跨进程锁互斥无实测（OS 原语，
同进程双句柄有回归）；「下载到真实 GitHub Release 重定向链」未实测（白名单已含
三个 githubusercontent CDN 主机，发布前建议先传一个测试资产验证一次真实下载）。
