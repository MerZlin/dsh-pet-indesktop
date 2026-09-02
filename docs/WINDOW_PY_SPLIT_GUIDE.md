# window.py 增量拆分公约（加功能前必读）

状态：现行公约 ｜ 建立于：结构线收官（perf/stage-1，2026-09）｜ 强制力：`tests/test_architecture.py` 的 window.py 行数预算红线 + CI

---

## 0. 一句话公约

**往 window.py 加功能之前，先把你要碰的那片逻辑拆成独立控制器，再在拆干净的地基上写功能。**

window.py 已从 4307 行/207 方法的上帝类拆出碰撞客户端、平台层、broker 接线等模块，
但仍有约 4200 行、220+ 方法、70+ 个共享实例字段。它不再是"能塞就塞"的地方：
CI 里有行数预算红线（`test_window_py_line_budget`），超了测试直接红。

## 1. 为什么是这个公约（而不是"提前全拆"或"不许拆"）

2026-09 结构线的实证结论：

- **提前全拆（为整洁而拆）不可行**：window.py 剩余的纠缠是 70+ 个共享字段的隐式契约，
  没有功能牵引的拆分没有真实验收标准（只能说"测试还绿"），而 Qt 生命周期类回归
  测试覆盖不到（实机才暴露）。
- **不拆直接加功能更不可行**：每加一个功能，上帝类更胖、以后拆的成本更高（要先把
  新功能代码从窗口逻辑里分离出来再拆，成本超线性增长）。
- **唯一可持续的路**：拆分跟着真实功能走。谁要加功能，谁就先把那片域切下来——
  此时拆分有最实的验收标准（新功能要用它），拆错了立刻暴露。

这就是"功能驱动拆分"：拆分的时机、范围、验收全由正在开发的功能决定。

## 2. 触发条件（满足其一就必须先拆）

1. 新功能需要在 window.py 里新增超过 ~100 行；
2. 新功能需要改动某个域的 3 个以上既有方法；
3. CI 行数预算红了（说明有人没遵守前两条）。

## 3. 标准拆分流程（照做即可）

以"把域 X 拆成 `pet/window_X.py` 的 XController"为例：

1. **圈地**：按下文 §4 的域地图确认你要碰的方法属于哪个域。如果域地图里没有
   你要的域，先和 maintainer 讨论补地图，不要自己发明边界。
2. **读纠缠**：把该域每个方法读一遍，列出它读写的 `self._xxx` 字段清单；
   标出哪些字段**只**被本域用（直接搬走），哪些被别域共享（留窗口，控制器
   经构造注入或回调访问）。这步省不得——76 个共享字段里标错一个就是隐性 bug。
3. **建控制器**：新文件 `pet/window_<域>.py`，类持有自己的字段；对窗口的反向
   依赖通过构造参数注入（参考 `collision_client.py`：构造时传入交互常量，
   避免循环 import；参考 `agent_link_reducer.py`：依赖以 Callable 注入）。
4. **搬移不改逻辑**：逐方法机械搬移，一行逻辑不改、一个数值不动。
   每搬一组就跑测试。
5. **留兼容委托**：window.py 里保薄的转发/委托属性（参考现有
   `_collision_session` / `_predicted_bounces` 等 property 块）——既有调用面
   和测试断言不因拆分而变。
6. **验收**（缺一不可）：
   - `QT_QPA_PLATFORM=offscreen python -m pytest -q` 全绿；
   - `ruff check pet/ tests/` 全绿；
   - `tests/test_architecture.py` 绿（行数预算应随拆分**下调**，在 PR 里同步改）；
   - 实机四场景冒烟：拖拽（含弹弓/抛掷）、多开碰撞、聊天开关窗、Agent 联动。
7. **PR 描述**写明：拆了哪个域、搬走哪些字段、哪些字段是共享的及理由。

## 4. 域地图（window.py 剩余部分的功能归属）

每个域标注：核心方法（真实方法名，按字母聚类）｜主要共享字段｜拆分难度｜对应已宣布功能。

### 4.1 屏幕可见性域 → ScreenVisibilityController

边缘探头等"贴边/全屏感知"功能的地基。

- 方法：`_start_fs_watch` / `_stop_fs_watch` / `_fs_watch_loop` /
  `_fg_fullscreen_win32`（薄委托→platform_win）/ `_fg_fullscreen_probe` /
  `_fs_user_busy_state` / `_on_fullscreen_changed` / `set_auto_hide_fullscreen` /
  `_on_application_state_changed` / `_arm_screen_restore_retry` /
  `_disarm_screen_restore_retry` / `_screen_retry_tick` / `_on_screen_added_restore`
- 位置存取与多实例避让：`_restore_position` / `_save_position` / `save_position` /
  `_go_default_corner` / `go_default_corner` / `_screen_available` /
  `screen_available` / `_live_instance_rects` / `_rects_overlap` / `_pid_alive` /
  `_write_runtime_marker`
- 共享字段（示例，拆前必须自行复核）：`_fs_watch_stop`、`_screen_restore_timer`、
  `_position_listeners`、位置/屏幕名配置
- 难度：中。watcher 是后台线程，拆时注意线程边界留在窗口侧。
- 对应功能：**边缘探头**。

### 4.2 动画链域 → AnimationChainController

自定义角色动作/动作池功能的地基。

- 方法：`_switch` / `_switch_fallback` / `switch_clip` / `_fallback_playable_idle` /
  `_schedule_switch_retry` / `_cancel_pending_switch_retry` /
  `_on_switch_retry_timeout` / `_connect_movie` / `_on_clip_finished` /
  `_on_anim_ended` / `_pick` / `_pick_next` / `_start_animation_gap` /
  `_cancel_animation_gap` / `_play_animation_gap_step` / `_on_animation_gap_timeout` /
  `set_animation_gap` / `set_playback_speed`
- 共享字段：`self.anim` / `self.movie` / `self.idles` / 分类动作池 /
  `_switch_retry_timer` 等
- 难度：高。动画链是全局最忙的状态机，联动/碰撞/菜单都会触发 `_switch`。
  broker 钩子（`_broker_register` / `_broker_unregister` / `_broker_*`）与
  `_switch` 耦合——拆这片时必须连 broker 注册/解注册的对称性一起搬。
- 对应功能：**自定义角色动作、动作池编排**。

### 4.3 交互动物理域 → InteractionController

拖拽/弹弓/抛掷的手感改造才需要拆；没有功能立项前**不要动**。

- 方法：`mousePressEvent` / `mouseMoveEvent` / `mouseReleaseEvent` /
  `_schedule_drag_move` / `_consume_drag_move` / `_flush_drag_move` /
  `_clear_drag_move` / `_sync_drag_polling` / 弹弓族（`_enter_slingshot` …
  `_launch_slingshot` / `_slingshot_geometry` / `_slingshot_trajectory_*`）/
  `_on_move_tick` / `_trigger_move` / `_try_move` / `_cancel_move` /
  `_collision_clamp_pos` / `_set_interaction_hold` / `_update_interaction_hold` /
  `_reset_press_hold_state` / `_is_in_interactive_area`
- 难度：最高。事件→状态转换与 Qt 事件循环、物理定时器、碰撞上报三方纠缠。

### 4.4 气泡与自语域 → BubbleCoordinator

气泡类新功能（新气泡样式/多气泡）的地基。

- 方法：`show_bubble` / `hide_speech_bubble` / `hold_bubble` /
  `set_bubble_suppressed` / `_on_speech_bubble_clicked` /
  `_try_open_quick_chat_from_bubble` / 自语族（`_schedule_self_talk` /
  `_show_self_talk_text` / `_show_random_self_talk` / `_show_click_self_talk` /
  `_on_self_talk_timeout` / `_read_self_talk_texts` / `set_self_talk_settings`）/
  `_check_music_sing`
- 难度：中。气泡本体已在 speech_bubble.py，这里剩调度与定位。

### 4.5 设置写回域

`refresh_pet_settings`（~90 行）与 `_show_context_menu`（~110 行）两个回填大方法：
不急着拆类，但**加设置项时**应把配置键→窗口行为的映射抽成 mapper 函数/表，
别继续在两个大方法里加 if 分支。

### 4.6 已拆出的域（不要再往 window.py 里加回这些逻辑）

- 碰撞客户端：collision_client.py（窗口侧全部是薄委托，见 `_collision_*` property 块）
- 碰撞物理/协议：collision.py / collision_codec.py / collision_ipc.py
- 平台层：platform_win.py / platform_mac.py
- broker：decode_broker.py（窗口侧只有 `_broker_*` 薄转发）
- 帧缓存：frame_cache.py；性能打点：perfstats.py

## 5. 范例：collision_client.py 是怎么拆的（可照抄的模式）

1. 控制器持有全部域字段（`session`/`epoch`/`peer_snapshots`/`predicted_bounces`/
   冷却时间戳……），窗口侧一个不留；
2. 窗口保留同名委托 property（get/set 转发），既有测试与调用面零改动；
3. 控制器需要的窗口交互常量（拖拽中/抛掷中等判定值）在**构造时注入**，
   不反向 import window；
4. 信号直接连控制器方法（`session.impulse_ready.connect(client._on_collision_impulse)`），
   不经窗口转发；
5. 窗口侧新增只读 seam（如 `collision_app_session` property）供控制器兜底，
   而不是让控制器摸 `win._xxx` 私有字段——**跨模块私有访问是红线**，
   `tests/test_architecture.py` 会拦。

## 6. 禁忌（每条都对应真实事故）

- **禁止跨模块访问 `win._xxx` / `pet._xxx`**：S2 批次清掉过 8 处，CI 有断言。
  需要就加公开 seam。
- **禁止搬移时顺手改逻辑/数值**：拆分 PR 只做搬移。"顺手优化"让回归无法归因。
- **禁止在 GUI 线程外直接操作 QObject**：PySide6 跨线程 `QTimer.singleShot(0, app, callable)`
  会原生崩溃；跨线程通信用"以 GUI 侧 QObject 为 receiver 的 queued 信号"。
- **C++ 已销毁对象不调自身 bound-method 的 destroyed 槽**：cleanup 回调用无 receiver
  的 lambda（Fix A1 模式，代码里有多处范例）。
- **新增配置键必须过三关**：默认值 dict + reload 白名单 + `test_config_schema.py`
  快照——漏登记测试直接红（这是刻意设计）。布尔键用 `_bool_or_default` 归一
  （`bool("false") is True` 的坑踩过）。
- **实机四场景冒烟不可省**：offscreen 测试测不出 Qt 生命周期/时序回归
  （有过实机 15 分钟定格而测试全绿的先例）。

## 7. 后续路线（不写死排期，只写触发条件）

| 候选方向 | 触发条件 | 参考 |
|---|---|---|
| InteractionController 拆分 | 有手感/交互类功能立项 | §4.3 |
| webm_clip.py 拆分（reader 生命周期/孤儿注册表） | 下次有人动 WebM 播放 | 结构线 S6 记录 |
| config_domains 调用点迁移 | 下次加设置项时顺手 | pet/config_domains.py |
| A17 测试债（源码字符串断言→行为断言） | 维护到对应测试文件时顺手 | pyproject.toml 豁免表 |
| QQuickWindow 前端迁移 | 需要合成器级视觉（粒子/多层特效）或"GUI 忙时动画卡"成为实测瓶颈 | 完整设计稿已有存档（含平台矩阵/回退/分批计划），需要时再立项；硬前置 = §4.1/4.2 两域拆分完成 |
