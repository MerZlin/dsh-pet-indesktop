# window.py 演进指南

**文档性质**：本文是项目结构治理实践的经验整理与演进建议，供贡献者参考，
不构成强制规范。CI 的强制检查来自 `.github/workflows/pr-test.yml`（完整
测试套件 + ruff）；其中与本文直接相关的架构护栏是
`tests/test_architecture.py` 的三项断言（依赖方向、窗口私有面、行数预算，
覆盖范围见第 5 节）。

**适用范围**：`pet/window.py`（`PetWindow`，4239 行 / 237 个类级方法
（AST 直接定义口径）/ 159 个不同名实例字段（全文 `self.xxx =` 赋值
去重口径，2026-09-03 实测））及其后续演进。

**版本**：2026-09-03，对应分支 `perf/stage-1`。

---

## 1. 背景与现状

`PetWindow` 是桌宠主窗口类，历史上承担了渲染、交互、动画状态机、碰撞
客户端、平台适配、气泡调度、设置写回等几乎全部窗口侧职责。2026-09 的
结构治理已将其中的碰撞客户端、平台层、共享解码 broker 接线等拆分为独立
模块，但该类仍是仓库内最大的单一类。

结构治理同时在 CI（`.github/workflows/pr-test.yml`，完整测试套件 + ruff）
之上建立了三条架构红线（`tests/test_architecture.py`）：

1. 纯逻辑层（collision / physics / collision_codec）不依赖 Qt；
2. decode_broker 不反向依赖 window / webm_clip；
3. window.py 行数不超过预算值（超出即测试失败）。

注：红线 2 约束的是模块级 import 依赖方向，不等于「控制器不访问窗口
实例」——近邻控制器持有窗口引用并读写其状态是当前的实际做法（见 §5）。

## 2. 演进策略：功能驱动拆分

本项目对 window.py 采用「功能驱动拆分」策略，即：**拆分的时机与范围由
正在开发的功能决定，不进行无功能牵引的预防性拆分。**

该策略的依据来自治理期间的实践观察：

- 预防性全量拆分缺乏真实验收标准（仅能以"测试仍绿"判定），而 Qt
  生命周期类回归难以被 offscreen 测试覆盖，风险不可控；
- 直接在大型类上持续叠加功能，会使后续拆分的分离成本随代码量持续增长；
- 功能开发过程中进行拆分，功能本身即为拆分的验收标准，回归定位最快。

**建议的启动条件**（满足其一即建议评估拆分）：

1. 新功能预计在该类中新增超过约 100 行；
2. 新功能需要修改某一域的 3 个以上既有方法；
3. CI 行数预算触发告警。

不满足上述条件的小改动，直接修改 window.py 是合理的，无需为此拆分模块。

## 3. 推荐拆分流程

以下流程在 `collision_client.py` 的拆分中验证过，可作为参照（代码本身
即为可阅读的范例）。

1. **确定边界**：参照第 4 节域地图确定涉及方法的归属域；域边界不明确时，
   建议先与熟悉该域的维护者讨论。
2. **梳理字段依赖**：列出目标域每个方法读写的实例字段，区分「仅本域使用」
   （可随域迁出）与「跨域共享」（留在窗口类，经构造注入或回调访问）。
   当前不同名实例字段 159 个（赋值去重口径），此步骤的准确性
   直接决定拆分质量，建议逐字段核对而非依赖估计。
3. **建立控制器**：新模块持有本域字段；对窗口的反向依赖经构造参数注入，
   避免循环 import（参考 `collision_client.py` 的常量注入与
   `agent_link_reducer.py` 的 Callable 注入两种模式）。
4. **搬移与变更分离**：拆分提交仅做机械搬移，不夹带逻辑或数值修改，
   以保证回归可归因。
5. **保留兼容面**：window.py 保留薄委托 property / 转发方法，使既有调用
   与测试断言不因拆分而改变。
6. **验收**：测试套件全绿（`QT_QPA_PLATFORM=offscreen python -m pytest -q`）、
   `ruff check pet/ tests/` 全绿、架构红线测试通过（拆分后可在 PR 中同步
   下调行数预算）；涉及线程或生命周期的拆分，建议补充实机冒烟（此项为
   建议，非 CI 门禁）。
7. **PR 说明**：注明拆分域、迁出字段、保留共享字段及理由。

## 4. 域地图

以下为按方法聚类的参考划分，边界为近似划分而非严格分层。

### 4.1 屏幕可见性与位置

- 全屏监视：`_start_fs_watch` / `_stop_fs_watch` / `_fs_watch_loop` /
  `_fg_fullscreen_win32`（薄委托至 platform_win）/ `_fg_fullscreen_probe` /
  `_fs_user_busy_state` / `_on_fullscreen_changed` /
  `set_auto_hide_fullscreen` / `_on_application_state_changed`
- 屏幕恢复重试：`_arm_screen_restore_retry` / `_disarm_screen_restore_retry` /
  `_screen_retry_tick` / `_on_screen_added_restore`
- 位置持久化与多实例避让：`_restore_position` / `_save_position` /
  `save_position` / `_go_default_corner` / `go_default_corner` /
  `_screen_available` / `screen_available` / `_live_instance_rects` /
  `_rects_overlap` / `_pid_alive` / `_write_runtime_marker`
- 相关字段（拆分前请再次核对）：`_fs_stop`、`_fs_thread`、
  `_screen_restore_armed`、`_position_listeners` 等
- 注意：watcher 运行于后台线程，拆分时建议将线程边界保留在窗口侧。
  另请注意本域是粗粒度归并：全屏 watcher（线程）、位置恢复
  （Qt `screenAdded` 信号）、多实例避让（文件/进程探测）的实现机制
  差异较大，不能据此推断它们可作为一个整体迁移。
- 拆分难度评估：中。

### 4.2 动画链

- 方法：`_switch` / `_switch_fallback` / `switch_clip` /
  `_fallback_playable_idle` / `_schedule_switch_retry` /
  `_cancel_pending_switch_retry` / `_on_switch_retry_timeout` /
  `_connect_movie` / `_on_clip_finished` / `_on_anim_ended` / `_pick` /
  `_pick_next` / `_start_animation_gap` / `_cancel_animation_gap` /
  `_play_animation_gap_step` / `_on_animation_gap_timeout` /
  `set_animation_gap` / `set_playback_speed`
- 相关字段：`self.anim` / `self.movie` / `self.idles` / 各分类动作池 /
  `_switch_retry_timer` 等
- 注意：该状态机是联动、碰撞、菜单等多方的汇聚点（外部模块经公开 seam
  间接触发，不直接调 `_switch`）；broker 的注册/解注册钩子
  与 `_switch` 耦合，拆分时需连同该对称性一并迁移。
- 拆分难度评估：高。

### 4.3 交互与物理

- 方法：`mousePressEvent` / `mouseMoveEvent` / `mouseReleaseEvent` /
  `_schedule_drag_move` / `_consume_drag_move` / `_flush_drag_move` /
  `_clear_drag_move` / `_sync_drag_polling` / 弹弓相关（`_enter_slingshot` …
  `_launch_slingshot` / `_slingshot_geometry` / `_slingshot_trajectory_*`）/
  `_on_move_tick` / `_trigger_move` / `_try_move` / `_cancel_move` /
  `_collision_clamp_pos` / `_set_interaction_hold` /
  `_update_interaction_hold` / `_reset_press_hold_state` /
  `_is_in_interactive_area`
- 注意：事件到状态的转换与 Qt 事件循环、物理定时器、碰撞上报三方耦合。
- 拆分难度评估：最高。

### 4.4 气泡与自语

- 方法：`show_bubble` / `hide_speech_bubble` / `hold_bubble` /
  `set_bubble_suppressed` / `_on_speech_bubble_clicked` /
  `_try_open_quick_chat_from_bubble` / `_schedule_self_talk` /
  `_show_self_talk_text` / `_show_random_self_talk` /
  `_show_click_self_talk` / `_on_self_talk_timeout` / `_read_self_talk_texts` /
  `set_self_talk_settings` / `_check_music_sing`
- 注意：气泡绘制本体位于 speech_bubble.py；窗口侧除调度与定位外，
  还保留公开气泡 API、气泡占用状态（`_bubble_busy_until`）与交互回调，
  实际边界比「纯调度层」更宽。
- 拆分难度评估：中。

### 4.5 设置写回

`refresh_pet_settings` 与 `_show_context_menu` 为两个较大的配置回填方法。
新增设置项时，建议将「配置键 → 窗口行为」的映射抽取为独立的 mapper
函数或映射表，避免在两个方法中继续叠加分支。

### 4.6 已迁出的域

下列职责已有独立模块承载，相关新逻辑建议直接加入对应模块：

| 职责 | 模块 |
|---|---|
| 碰撞客户端（窗口侧为薄委托） | collision_client.py |
| 碰撞物理 / 协议 / IPC | collision.py / collision_codec.py / collision_ipc.py |
| 平台层 | platform_win.py / platform_mac.py |
| 共享解码 broker | decode_broker.py（窗口侧 `_broker_*` 块为接线+首播决策状态机，非纯转发） |
| 帧缓存 / 性能打点 | frame_cache.py / perfstats.py |

## 5. 参考范例：collision_client.py 的拆法

`collision_client.py` 是「功能驱动拆分」的完整范例，可直接对照阅读：

1. 控制器持有已抽取的域状态（session / epoch / peer_snapshots /
   predicted_bounces 等）；窗口保留组合对象、兼容委托 property 与必要的
   宿主状态；
2. 窗口保留同名委托 property（get/set 转发），既有测试与调用面零改动；
3. 控制器需要的窗口常量在构造时注入，不做模块级 `import window`；
4. 信号直接连接控制器方法，不经窗口转发；
5. 需要兜底访问时，窗口侧增加只读公开 seam（如 `collision_app_session`
   property）。

关于私有访问的真实边界（易误读，请注意）：CI 断言
`test_window_private_surface_frozen` **只覆盖 `app.py` / `agent_link.py` /
`context_menus/` 三个外围调用面**——这些模块不得访问 `win._xxx`。
而 `collision_client.py`、`platform_win.py` 等窗口的近邻控制器目前仍
按既有约定访问窗口私有成员（属当前事实而非违规），
`agent_link_presentation.py` 也保留了一处有注释登记的例外
（`win._bubble_busy_until`）。新拆控制器时建议优先走公开 seam；确实
需要近邻访问时，参照这些既有模块保持克制并在注释中说明。

## 6. 已知风险点（来自项目事故与审查记录）

以下为治理与审查过程中实际发生/发现过的问题，供参考（详细记录在
`_plan/` 工作档案中，该目录不入库）：

- **跨模块私有成员访问（外围调用面）**：治理期间曾清理 8 处
  （app.py / agent_link.py / context_menus），现有 CI 断言防止这三处
  回潮（覆盖范围见 §5 第 5 点）。
- **拆分夹带行为变更**：会导致回归无法归因，拆分与行为修改应分开提交。
- **跨线程操作 QObject**：跨线程 `QTimer.singleShot(0, app, callable)`
  属于 PySide6 已知危险用法（治理期间的事故记录，仓内无单独 issue 可溯）；
  跨线程通信应使用以 GUI 侧 QObject 为 receiver 的 queued 信号。
- **已销毁对象的 destroyed 槽**：PySide6 在 C++ 侧删除对象时不会调用该
  对象自身 bound-method 形式的 destroyed 槽；cleanup 回调应使用无
  receiver 的 lambda（代码内搜「Fix A1」可见范例）。
- **新增配置键的登记**：普通顶层键需同步默认值字典、reload 白名单与
  `test_config_schema.py` 快照，缺一则测试失败（刻意设计）；特例键
  （version / proactive_screen / agent_link / chat）走专门的合并/迁移
  路径，不要塞进普通白名单。布尔键应使用 `_bool_or_default` 归一化。
- **offscreen 测试盲区**：线程与时序类回归无法被 offscreen 测试发现，
  存在测试全绿但实机异常的先例；涉及线程/生命周期的改动应实机验证。

## 7. 暂不处理的部分及理由

以下各项为经过评估后的**主动缓办决策**，均非缺陷或遗留 bug。每项附
缓办理由与建议的重新评估时机。

| 项 | 缓办理由 | 建议的重新评估时机 |
|---|---|---|
| 交互与物理域拆分（§4.3） | 该域与其他域耦合最深，拆分回归风险最高；当前无功能需求 | 有交互手感类功能立项时 |
| webm_clip.py 进一步拆分 | 该文件承担 reader 生命周期管理，近期刚完成多轮生命周期修复，稳定性优先 | 下一次需要修改 WebM 播放逻辑时 |
| config_domains 调用点迁移 | facade 已建立且 normalize 复用现有逻辑；迁移调用点无行为差异，属纯结构调整 | 下一次新增设置项时一并进行 |
| 测试中的源码字符串断言改造 | 相关断言目前均能通过且有意义；批量重写为行为断言的工作量与风险不成比例 | 维护到对应测试文件时局部改进 |
| QQuickWindow 前端迁移 | 属产品架构级变更；已完成完整设计稿（含平台矩阵与回退方案），当前 raster 路径经优化后无实测瓶颈 | 出现合成器级视觉需求，或 perfstats 数据显示渲染成为瓶颈时 |

---

*本文档随 `docs/HANDOVER_2026-09.md` 一同交付。对本文内容有异议或补充，
欢迎通过 PR 修订。*
