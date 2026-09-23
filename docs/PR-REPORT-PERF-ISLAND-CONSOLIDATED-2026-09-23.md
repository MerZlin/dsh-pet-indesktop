# PR 报告：流畅度/解码减负 + 岛远端硬墙 + 音效缓存 + 设置收口（2026-09-23）

分支：`fix/perf-island-consolidated`（4 个提交，rebase 于 origin/main `419001c` 之上，零冲突）。

## 1. 修改文件说明

### fix(perf): 走路帧间补点 + 解码 GUI 减负 + 抛掷动画加速 + 冻结采样器

| 文件 | 增/删 | 改了什么 / 为什么 |
|---|---|---|
| `pet/movement.py` | +33/-1 | 新增 `move_anim_tick(host)`：走路位置在帧回调之间按墙钟外推等效帧号补点（锚点封顶 +1 帧防漂移）。旧路径走路位置更新仅 ~28.6Hz（24fps 素材帧驱动），高刷屏上每 ~6 显示帧才动一次，是"帧数低/不流畅"观感的直接来源 |
| `pet/window.py` | +54/-32 | `_move_anim_timer`（_tick_ms/PreciseTimer）接线补点；`_rebuild_frame` 取帧改回经 `QPixmap.toImage()`（与 clip 显示槽/首帧缓存无共享别名，见"崩溃消融"说明）；`_throw_low_speed_switch` 回退分支加 warm 池闸门；`gui_stall_sampler` 接线（>60ms 空窗落冻结现场） |
| `pet/webm_clip.py` | +54/-91 | `jumpToFrame(0)` 冷路径不再 GUI 同步解码首帧（旧帧由窗口顶着）；`_process_frame` 源帧 0 顺手写首帧缓存（try-acquire，GUI 不阻塞）；首帧缓存改存 `.copy()`（消融）；meta 探测主线程踢后台（`_ensure_meta` 后台化、`warm_meta` 用 `_from_warm=True` 放行）；新增 `currentImage()`/`clear_display_frame()`（新架构 sprite 使用，旧窗口路径不消费） |
| `pet/library.py` | +21/-1 | 新增 `clip_current_image(clip)`（零拷贝取当前帧，空帧回退 `currentPixmap().toImage()`）；旧窗口路径已不消费，保留给 sprite 路径 |
| `pet/physics.py` | +11/-0 | `flight_anim_speed`：抛掷飞行期动画速率随速度 1.0→1.75×（高速飞行不再"慢动作滑翔"） |
| `pet/gui_stall_sampler.py` | +95/-0（新增） | perfstats 观测模式下 GUI 冻结现场采样（栈样本），常态零开销 |
| `tests/test_move_sync.py` | +168/-0 | 补点语义：墙钟外推、锚点封顶、物理模式不补、曲线素材静帧段 |
| `tests/test_first_frame_no_gui_decode.py` | +106/-0（新增） | 冷路径不产生 GUI 同步解码 |
| `tests/test_meta_no_gui_probe.py` | +90/-0（新增） | meta 探测不在主线程发生 |
| `tests/test_clip_current_image.py` | +177/-0（新增） | 零拷贝三分支与新旧链像素一致 |
| `tests/test_webm_first_frame_lock.py` | +7/-246 | 首帧锁语义随冷路径改动重写（删掉了对同步解码的建模） |
| `tests/test_physics.py` / `test_throw_flight_anim.py` | +8/+48 | flight_anim_speed 曲线与边界 |
| `tests/test_architecture.py` | +5/-1 | window.py 行预算 4632→4635（消融回退净 +3，注释史逐行说明） |

### fix(island-collision): 子宠进程补挂远端硬墙 + 几何发布洪峰合流 + 岛冲量归属守卫

| 文件 | 增/删 | 改了什么 / 为什么 |
|---|---|---|
| `pet/island_collision.py` | +172/-6 | 远端模式碰撞体：本进程无岛时也建体，几何由碰撞快照回喂（`on_remote_snapshot`；`member=None` stale-keep 不撤墙，`FLAG_PAUSED` 立即撤墙；`_REMOTE_WALL_TTL_S=8s`）；宿主侧 `_publish_static_state`（2s 心跳 + 几何变化合流 ≤10Hz）；直连硬墙（stadium 钳制/轻贴推出/抛掷撞墙/反推被压桌宠）对宿主与远端同路径生效 |
| `pet/collision.py` | +6/-5 | 静态成员常量与 flags（`ISLAND_MEMBER_ID`、`FLAG_STATIC`、`STATIC_RESTITUTION=1.3`） |
| `pet/collision_ipc.py` | +61/-1 | `submit_static_state` 第二成员通道（静态成员上行，member_id 可覆盖） |
| `pet/collision_client.py` | +22/-0 | `island_local_owned` 双重结算守卫：宿主进程丢弃协调者转发的岛冲量（撞岛业务归本进程直连链）；快照里的静态成员回喂岛碰撞体 |
| `pet/app.py` | +22/-8 | `_sync_island_collision` 重写：有岛=直连+发布，无岛=远端模式；`attach_publisher` 挂第一个持有碰撞会话的实例（碰撞总开关关闭时静默跳过） |
| `tests/test_island_remote_wall.py` | +390/-0（新增） | 远端建体/快照回喂/撤墙/TTL/几何变化推挤被压桌宠 |

### fix(sound): 音效包候选解析进程级缓存

| 文件 | 增/删 | 改了什么 / 为什么 |
|---|---|---|
| `pet/click_sound.py` | +31/-8 | `_duck_candidates` 进程级候选缓存：`resolve_click_sound_pair` 每次撞击在 GUI 线程 iterdir + 逐文件 is_file（碰碰车 ≤4Hz 叠加），改缓存复用 |
| `tests/test_click_sound.py` | +24/-0 | 缓存命中/失效/重置语义 |
| `tests/conftest.py` | +1/-0 | `_reset_caches_for_tests` 清理钩子（防缓存跨用例污染） |

### chore(settings): 设置页隐藏「多开」开关（拓扑收口 Phase A）

| 文件 | 增/删 | 改了什么 / 为什么 |
|---|---|---|
| `pet/modern_settings_dialog.py` | +6/-11 | 移除「多开」SettingsSection 与注册表行；`_save()` 不再写该键（存量值随 config.save() 原样回写） |
| `pet/settings_pet_controls.py` | +2/-2 | 不再构造 `single_process_spawn_check` ToggleSwitch |
| `tests/test_spawn_toggle_hidden.py` | +70/-0（新增） | 锁定三条：分组无「多开」、存量 True 保存不丢、默认 False round trip |

## 2. 性能分析

环境：Windows 11，2560×1440 @170Hz 主屏；素材 shenshen 24fps 640×360；
PySide6 6.11.2。测量工具：外挂 GetWindowRect 1ms 轮询（`probe_move_cadence.py`）
+ perfstats 内置 jank watchdog（>50ms 分桶）+ 冻结现场采样器。

### 2.1 走路帧间补点（收益主项）

| 指标 | 修复前 | 修复后 | 样本 |
|---|---|---|---|
| 走路位置交付间隔 p50 | 35.0ms（~28.6Hz） | **6.2ms（~160Hz）** | 基线 probe_move_cadence（n=168）；修复后 cadence_225612.jsonl（4634 次位置移动实测 p50 6.3ms） |
| 走路位置交付间隔 p90/p99 | 36.5 / 39.5ms | 10.4 / 18.4ms | 同上 |
| 单调走路子段零反向步 | — | 51/53（2 例最深 -2px 整数取整噪声，不可见） | cadence_000643.jsonl，300s 真人使用环境 |

稳态开销：补点定时器仅在移动计划存活期间运行（`_move_anim_timer`，
~6ms/tick，单次外推为纯浮点运算 <1µs）；静止/隐藏/物理模式不运行。
新增系统调用：无（补点走既有 `_move_window_towards` 出口，频率与
旧"帧驱动位移"同源同径）。

### 2.2 解码 GUI 减负（碰撞卡顿主项）

| 指标 | 修复前 | 修复后 | 样本 |
|---|---|---|---|
| 碰撞压力场景 >50ms 卡顿 | 133 次 | **3 次** | probe2 碰撞链场景 |
| 暖机稳态 100ms+ 冻结 | 每 3s 一次（GUI 同步解码首帧 ~166ms） | **0 次**（残留 4 次全部在启动冷池期） | probe2 快照归因 |
| 音效解析 resolve_click_sound_pair | 每撞击 iterdir+is_file ≤4Hz | 0.43ms/次（缓存命中） | 实机 perfstats |

稳态开销：冷路径不再同步解码 = 事件循环不再被 100-337ms 解码阻塞
（这也消除了它对 paint/queued 回调的强制串行化）；meta 后台化后主线程
零 ffprobe 探测。新增线程：无新增（复用既有 warm/reader 线程池）。
新增系统调用：无；磁盘 IO 减少（meta 缓存复用）。

### 2.3 岛远端硬墙（新路径成本与触发频率）

- 几何发布：2s 心跳 + 几何变化合流 ≤10Hz（旧实现 30Hz 采样），上行报文
  <4KiB/条（协议上限内）；碰撞 tick 33ms 既有节奏不变。
- 远端墙钳制在统一位置出口同步执行（与屏幕边界钳制同径），无定时器新增；
  stale-keep 判定为纯字段比较。
- 新增 IPC 通道：`submit_static_state`（静态成员上行，仅在岛宿主进程且
  碰撞总开关开启时激活）；无新增 socket/线程（复用 `_CollisionWorker`）。

### 2.4 内存有无增长

- tracemalloc @80s 审计：Python 侧 46.7MB 结构健康（每宠 ~130MB 主要为
  Qt 原生地板，与 main 基线一致）；首帧缓存全局 8MB LRU 预算不变
  （`set_first_frame_budget` 既有机制）；`_duck_candidates` 缓存为
  每音效包数百字节路径串。
- `clear_display_frame()` 使非显示 clip 释放 ~1.84MB/段显示槽（净减少）。

### 2.5 崩溃消融的诚实记录（不许用沉默代替结论）

开发期本分支窗口曾回退"零拷贝取帧"（`window.py` 改回经 `QPixmap.toImage()`
取帧，与 clip 显示槽/首帧缓存无共享别名 + 首帧缓存 `.copy()`）——背景是 2026-09-22 部署版出现 8 次 Qt6Gui
QRasterPaintEngine 原生崩溃（WER + 3 份 CrashDumps 栈链还原，全部
GUI 线程绘制期空 d_ptr 近零解引用）。后续取证（反汇编 + 哨兵捕获
`QBackingStore::endPaint() called with active painter`）证明真正机理是
**绘制重入（同一设备双 QPainter）**，与零拷贝无因果关系；回退保留是因为
它无害且消除了唯一的跨线程共享别名面。**该崩溃案与本 PR 各修复项无因果
关系，且未结案**（机理已定位、修复在跟进，跟踪于
`.scratch/single-overlay-window/HANDOFF.md` 崩溃案一节）。

## 3. 实机运行记录

全部在真机（非 CI、非 mock、非 offscreen）执行；部署构建 =
`scripts/build_onedir.ps1 -Variant webm-chat` 产物。

1. **走路补点 A/B（真人使用 300s）**：53 个单调走路子段 51 个零反向步，
   2 个例外最深 -2px；走路节拍随速度 6-15ms；防抖动回归专项（用户硬性
   要求，历史教训）通过。
2. **碰撞链压力（真人三开互撞 5 分钟）**：>50ms 卡顿 133→3 次；两次
   fanout 看门狗告警均发生在关停边缘，降级为观察项（非运行期问题）。
3. **岛远端硬墙（多进程三宠互撞）**：子肥鱼撞岛停住（修复前直接穿岛）；
   宿主岛被撞击有 bump 反馈；岛拖拽经过桌宠上方时桌宠被推出。
4. **回归用例先红后绿**：`tests/test_move_sync.py`、`test_island_remote_wall.py`、
   `test_first_frame_no_gui_decode.py`、`test_meta_no_gui_probe.py`、
   `test_spawn_toggle_hidden.py` 均按 test-first 纪律先写红再改绿（各提交
   的聚焦门禁记录在工作日志）。
5. **部署版真人使用**：回退版（碰撞世界速率恢复原值 0.05/50/33 +
   岛被撞反馈路由移除）连续运行，中低速"莫名抖动"（此前流畅度批引入的
   回归）消失。
6. **全量门禁**：`QT_QPA_PLATFORM=offscreen pytest -q` =
   **3000 passed / 12 skipped**（2 项失败已处置：`test_window_py_line_budget`
   系 rebase 合入上游 a7489ae（window.py +4/-1）后按先例校准预算 4635→4638；
   `test_foreground_window_info_real_call_no_shadow_bug` 为真实桌面环境 flake
   ——套件运行期间前台窗口处于游戏全屏切换态，隔离复跑 2 次均通过，
   `vision.foreground_window_info()` 手动实测返回正常）；ruff
   `All checks passed!`；受影响时序测试族（collision/island/webm/move_sync/
   throw/spawn/first_frame/meta/click_sound，325 用例）高负载复跑 **3 遍
   全绿**（25.3s / 38.5s / 30.1s）。

## 4. 第二轮修正（合入前评审，2026-09-23）

合入前评审发现的问题与逐项处置：

| 发现 | 严重度 | 处置 |
|---|---|---|
| A1 冷 meta 首播瞬移（`_try_move` 吃到 meta 默认值建坏计划） | 阻断 | **已修**：`_try_move` 冷 meta 闸门（duration≤0 或 frames≤1 时本轮放弃移动，后台到位后自愈；不回退 GUI 同步探测）+ `test_try_move_skips_when_meta_cold` |
| A2 飞行加速/复位硬编码 1.0，抹掉用户「播放速率」 | 应修 | **已修**：加速与复位均按 `self.playback_speed` 复合 + `test_flight_anim_speed_composes_with_user_playback_speed` |
| A3「等效 ~42fps」无机制支撑（readrate 启动时固定） | 应修 | **措辞修正**：docstring 改为"跳帧加速"语义（动画在更短墙钟内播完，非解码端真交付 42fps）；readrate 跟随列入后续改进 |
| A4 FLAG_PAUSED「立即撤墙」在协调者链路不可达（静默清退，远端靠 8s TTL） | 应修 | **已修**：协调者墓碑机制（`_tombstones`）——暂停/不可见成员先带标记进一次快照，下一 tick 才清退 + `test_paused_member_snapshotted_once_before_purge`（既有清退回归钉同步改两阶段语义） |
| A5 碰撞总开关关闭后发布通道残留（2s 心跳持续发报） | 应修 | **已修**：新增 `IslandCollisionBody.detach_publisher()`（发一次 PAUSED + 停心跳 + 清引用）并在 `_sync_island_collision` 无可用会话分支调用 + `test_detach_publisher_stops_heartbeat_and_publishes_paused` |
| member_id 任意值可被冒名覆写（协议放宽） | 建议 | **已加固**：member_id 覆盖仅限 `ISLAND_MEMBER_ID` + `FLAG_STATIC`，其余回退连接自身 runtime_id + `test_state_message_member_id_hijack_rejected` |
| webm_clip `__init__` 注释「冷路径只 kick 后台 warm」与代码矛盾 | 应修 | **已修**：注释改为"不 kick 任何解码"（与 jumpToFrame 实现一致） |
| `test_meta_no_gui_probe` 断言 `<=1` 过松（0 也通过） | 建议 | **已修**：断言收紧为「同步踢出 + 合计恰 1 次」 |
| `gui_stall_sampler` 重复赋值与 `_keepalive` 死代码 | 建议 | **已修**：删冗余行（`sampler._win_ref` 已承担保活） |
| `test_webm_first_frame_lock.py` 未用 `import time` | 建议 | **已修**：删除 |
| 报告样本口径「n=168+」与实际不符 | 建议 | **已修**：基线（n=168）与修复后（4634 次位置移动）分开标注 |
| `clip_current_image` 等三个 API 旧路径不消费（新架构依赖） | 应修（有记录） | **保留 + 明示**：为同树 sprite 路径的依赖（带完整测试），防两批断链；PR 描述中显式声明可无 sha 回退 |
| `move_anim_tick` 从 movement 引入 window.time（分层瑕疵） | 建议 | 保留现状：它是测试接缝（monkeypatch 统一时钟），lazy import 无循环风险；后续可改为注入时钟 |
| `_on_move_tick` 只停 `_move_timer` 不停 `_move_anim_timer` | 建议 | 潜伏不可达（plan/movie 同生共死），暂不加以免动语义 |
| 多窗时 gui_stall_sampler 仅首窗生效 | 建议 | 观测工具语义，perfstats 默认关闭；多窗采样归后续 |
| 隐藏「多开」后存量 True 用户无 UI 出口、SETTINGS-CHANGE-GATES/RELEASE 文档未同步 | 建议 | 记录在案：文档同步归 4.4 文档刀；存量用户可手改 config.json（键保留） |
| 报告行预算碰撞（window.py 4635 vs 上游 +3） | — | 已按先例校准 4638（提交 f32e71a）；A1/A2 修复净增后再校准 4647（守卫必须贴着建计划点，未拆控制器，注释史逐行说明） |
