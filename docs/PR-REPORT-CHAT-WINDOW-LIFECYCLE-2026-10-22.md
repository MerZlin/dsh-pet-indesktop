# 聊天窗「关闭即销毁」：关窗后内存真的还回去（2026-10-22）

> **基线**：`4753617`（main：功能模块目录化 #332）
> **分支**：`fix/chat-window-lifecycle`　**日期**：2026-10-22
> **范围**：8 个文件（实现 5、测试 2、文档 2，其中 `lifecycle.py` 与
> `test_chat_window_lifecycle.py` 为新增）
> **关联**：4.4.0 候选实机反馈「AI 对话窗口关闭后占用的内存不返还，顺带检查
> 其他功能有没有这个问题」；[`PR-REPORT-CHAT-HISTORY-PAGING-2026-10-21.md`](PR-REPORT-CHAT-HISTORY-PAGING-2026-10-21.md)（#318 分页，本报告要证明没被绕过）

## 一、核心特性

**用户看到什么**：关掉 AI 对话窗口之后，那棵窗口树（约 61 个控件 / 约 1 MB
Python 堆分配）真的被还给系统；下次点「AI 对话」会重新构造一个干净窗口，历史
与设置照旧（首屏仍是分页后的最新 50 条，开窗 0.18–0.20 s，不是 #318 前的 6–12 s）。

**先证伪再修**（`AGENTS.md` 的工程纪律：内存问题先证明它到底是什么，再动手）：

| 判据（探针两轮「构造→show→close」） | 改前实测 | 结论 |
|---|---|---|
| 第二轮是否新建对象 | modern/legacy/quick **不新建**（`new=False`，0 控件增长、pet 分配 ±0.3 KB） | **反复开关不涨 ⇒ 不是泄漏** |
| 关窗后控件是否还在 | modern 61 / legacy 49 / quick 18 个控件仍在，对象 `valid`、槽位 `cached` | **一次性驻留不返还**（缓存单例的设计后果） |
| 待办面板 | 关窗后 0 控件、对象 destroyed、槽位 None | **已是关闭即销毁，无需改动** |

所以用户报告的现象属实，但根因不是「泄漏」而是**缓存单例只 hide 不销毁**；修法
是把 modern / legacy 两套完整聊天窗改成关闭即销毁。`quick_chat` 与 `todo_panel`
按同一份探针数据**故意不动**：前者 18 控件 / 约 31 KiB、反复开关零增长，属于高频
动作上的一次性驻留；后者本来就是关闭即销毁。两处都在代码里写明理由。

**红线 / 不变量**：

1. 关窗时的收尾语义一条不许少：已完成未排空的回复必须先落库再销毁（否则回复永远
   丢失、重开卡在「生成中」）；`service.stop()` 取消在途生成；`set_pet_window(None)`
   解除桌宠位置监听。
2. **不许带着在飞 `QThread` 一起死**：`ChatService` 的 worker 运行中被销毁会让进程
   直接崩（`shutdown` 的 docstring 记着同款踩坑）。关窗时若还有 worker 在跑，销毁
   推迟到它们退出之后。
3. 重开不得绕过分页（#318）：首屏仍然只渲染最新 50 条。
4. 缓存槽位必须跟着窗口走：槽里留下死对象会让 `show()` / `switch_character` /
   `sync_look_to_chat` 在已删 C++ 对象上抛 `RuntimeError`。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/chat/lifecycle.py` | **+74 / −0（新增）** | 两套聊天窗共用的收口语义：`release_window_when_idle(window, service)`——没有在飞 worker 就 `deleteLater`，有就等它们的 `finished` 再删（并在「判定忙碌」与「建立连接」之间复查一次，避免 worker 恰好在缝隙里退出导致永不销毁）；删之前先 `service.detach()` 摘掉天线（见下）。两套 UI 布局互不相干，但「关掉就把窗口树还回去」必须逐位一致，故集中一处而不是写两份。 |
| `pet/chat/widgets.py` | **+27 / −5** | modern `ChatWindow`：① `WA_DeleteOnClose` 显式置 False + 注释说明为什么不挂它（Qt 那一条会在 `close()` 里无条件 `deleteLater`，赶在在飞 worker 退出之前删掉宿主对象）；② 新增 `done()` 覆盖——X / Esc / `reject()` 三条收口路径的公共出口，在这里调 `release_window_when_idle`（只靠 `WA_DeleteOnClose` 覆盖不到 Esc，因为 QDialog 的 Esc 走 `reject()` 不走 `close()`）；③ `closeEvent` 去掉 `self.hide(); event.ignore()`，改为 `super().closeEvent(event)`：**不能先 hide**，QDialog 的 `closeEvent` 只在 `isVisible()` 时 `reject()`，先隐藏就既不发 `finished`（app 层靠它清缓存槽）也不走销毁；④ `showEvent` 注释更新（窗口不再复用）。 |
| `pet/chat/legacy_widgets.py` | **+15 / −3** | classic `ChatWindow` 同款镜像（该文件按设计是 modern 的独立实现，收口三条改动逐条对齐）。 |
| `pet/chat/service.py` | **+19 / −1** | ① 新增 `has_running_workers()`：销毁时机判据，含「已 stop、线程仍在校尾」的 worker（`busy` 只看当前那一只，会漏）；② 新增 `detach()` + `_detached` 标记，`_current()` 首行联动——关窗即销毁之后，worker 已发出但尚未投递的**排队**信号可能落在服务已删之后，那时 `self.stopped.emit()` 会抛 `Signal source has been deleted`（本批实测抓到过，见 §五）；置位后所有排队回调第一行就返回。 |
| `pet/app.py` | **+52 / −9** | ① `open_modern_chat` / `open_legacy_chat`：缓存槽取值改为 `_chat_window_usable()`（`None` 或 C++ 已删都当空），新窗口交给 `_own_chat_window()` 挂收口链——`finished` 一响就把 `modern_chat_window` / `legacy_chat_window` / `chat_window` 三个引用位清掉；② `_present_dialog` 开头加 `shiboken6.isValid` 早退（缓存窗已销毁就不必再呈现，与 `open_network_info` 同款）；③ `_close_instance_subwindows` 对已排定销毁的聊天窗不再补 `deleteLater`（会赶在 worker 之前删宿主），docstring 同步更新；④ `_show_quick_chat` / `open_todo_panel` 补注释，写明探针数据与「为什么这两处不动」。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_chat_window_lifecycle.py` | **+306 / −0（新增）** | 14 例（modern/legacy 参数化）：① 关窗后 `shiboken6.isValid` False、三个槽位清空；② 关窗解除桌宠位置监听；③ 重开是新对象且「发一条消息」链路通到 provider 并落库；④ 开-关三轮后存活控件回到基线、`ChatService._global_finished_listeners` 不翻倍、窗口与 service 都不被引用链挂着；⑤ 重开仍走分页（120 条历史 → 首屏 50 + 未渲染 70）；⑥ 窗口未关时再点一次「AI 对话」复用同一只、不叠第二个；⑦ 在飞请求关窗：先 stop，等 worker 退出后才销毁（8 s 宽预算）。 |
| `tests/test_chat_timeline_scroll.py` | **+6 / −4** | `test_reopening_window_returns_to_latest_reply` 按新契约改写：原用例先 `close()` 再对**同一个**窗口 `show()`（旧「关闭=隐藏复用」语义，全量门抓到这条红），改成关窗后重新构造窗口；断言不变——重开仍贴在最新一条回复底部。 |
| `tests/test_chat_subsystem.py` | **+27 / −9** | 两条既有用例按新契约改写：`test_close_while_streaming_still_cancels_the_request` 补「关窗后窗口确实销毁」断言、收尾用 `_close_if_alive`（关闭即销毁后不能再 `close()` 死对象）；`test_close_during_typewriter_drain_saves_reply_and_resets` 的「重开仍是就绪态」从「同一个复用窗口」改成「新构造的窗口」，并把控件级断言换到新窗口上（旧窗口已销毁）。 |

### 未改动（明确说明）

- `pet/quick_chat.py`、`pet/todo/panel.py`：探针证实一个是一次性驻留（18 控件 /
  约 31 KiB、反复开关零增长）、一个本来就是关闭即销毁；按任务口径保持原样。
- `pet/chat/settings_dialog.py`：`chat_settings_dialog` 已经有 `WA_DeleteOnClose` +
  `finished` 清槽（与本次同型且正确），且设置默认走独立进程，未动。

## 三、实现要点

**为什么销毁排在 `done()` 而不是 `closeEvent` 里直接 `deleteLater()`**：窗口的收口
有三条路——点 X（`close()`）、按 Esc（`QDialog::keyPressEvent` → `reject()`）、
程序内 `reject()`。`done()` 是三条路的公共出口，把销毁挂在那里，Esc 那条也不会
留下「隐藏但活着」的窗口。

**为什么先判 `has_running_workers()` 才删**：`_Worker` 是 `QThread`（非 daemon），
运行中被销毁会打印 `QThread: Destroyed while thread is still running` 并中止进程。
关窗只 `stop()`（置 cancel 事件 + 关响应），线程收尾可能还要几十毫秒，所以有在飞
worker 时把 `deleteLater` 挂到它们的 `finished` 上。

**为什么还要 `detach()`**：Qt 的排队连接把已发出的信号投进事件队列，投递时机可能
落在接收对象的 C++ 实体消失之后。本批第一版（`WA_DeleteOnClose` + 直接删）实测就
在 `_stopped` 里抛了 `Signal source has been deleted`；`detach()` 让所有排队回调
在 `_current()` 第一行短路，不碰任何 Qt 对象。**连接本身的拆除**是另一条对称点：
`ChatService` 构造时 `app.aboutToQuit.connect(self.shutdown)`，服务随窗口一起销毁
时 Qt 自动断连——测试里用 weakref 钉住「关窗后 service 可被回收」证明这条断连生效。

**取舍**：`quick_chat` 保持缓存单例——它是高频动作（每次点气泡就开口），而完整
聊天窗是低频重操作（一次开窗 ~1 MB）。用「低频动作付重建成本」换「关掉就还内存」，
方向是对的；反过来给气泡也做销毁重建只会在高频路径上白付会话/prompt 重建。

## 四、性能分析

**方法（可复现）**：
`QT_QPA_PLATFORM=offscreen python -u .scratch/chat-window-close/probe_window_lifetime.py`
（生命周期与内存）、`.../probe_open_close_cost.py`（开/关窗耗时，n=5）、
`.../probe_open_cost_paged.py`（开窗耗时 vs 历史长度，n=3）。
环境：Windows 11 · CPython 3.13 · PySide6 6.11.2 · offscreen。

| 指标 | 实测（改前 → 改后） | 归属 |
|---|---|---|
| modern 关窗后存活控件 | **61 → 0** | 热路径（用户关窗） |
| legacy 关窗后存活控件 | **49 → 0** | 同上 |
| modern 关窗后残留 pet 分配 | 975.4 KiB（常驻，永不还） → 0 控件、对象 destroyed；第 2/3 轮 pet 增量 +57 / +18 KiB（缓存预热，收敛） | 同上 |
| 开窗耗时（modern，空会话） | 36.7 ms 均值（19.4–97.7，n=5）；关闭+销毁 3.2 ms | 新增（重建成本） |
| 开窗耗时（legacy，空会话） | 16.3 ms 均值（11.7–28.6，n=5）；关闭+销毁 1.2 ms | 新增 |
| 开窗耗时 vs 历史长度（modern） | 0 条 38.1 ms / 200 条 183.3 ms / 800 条 201.7 ms；首屏恒为 50 条（未渲染 150 / 750） | 既有分页收益，**未被绕过** |
| 连续 20 次开-关（modern） | 首次 +1127 KiB（一次性导入/懒缓存），之后每轮 +6/+6/+3/+3/+3/+2/+2/+2/+4/+1/+2/+2/+1/+1/+1/+2/+1/+2/+1 KiB → 收敛到 0，19 轮合计 +48 KiB | 稳态 |

**结论**：

1. **稳态开销**：无新增轮询 / 定时器 / 线程。关窗多了一步 `detach()`（O(#worker)，
   通常 0–1 个）与一次 `deleteLater`，实测关闭+销毁 1.2–3.2 ms。
2. **新增路径成本与频率**：重建成本只在「用户开窗」时付，modern 37 ms / legacy
   16 ms（空会话）、200 条历史 183 ms；关窗一次 3 ms 量级。触发频率＝用户点开
   对话窗的频率，属低频重操作。
3. **有无新的系统调用/网络/磁盘/线程**：无。销毁只是释放对象；`detach()` 不建线程。
4. **内存**：关窗后控件树 100% 释放（61/49 → 0），连续 20 次开关的增量收敛到 0
   （19 轮 +48 KiB，且逐轮递减）。改前则是「开一次常驻 ~1 MB 到进程结束」。

## 五、实机运行记录

**1）根因/前提的现场复现（改前基线，探针原始输出留档
`.scratch/chat-window-close/probe-baseline.log`）**：

```
[modern_chat_window] round1: new=True open+2669.8KB / +976.0KB pet / +61 控件
                     → 关后留存 2669.0KB / 975.4KB pet / 61 控件；窗=valid 槽=cached
                     round2: new=False open+-98.7KB / +0.3KB pet / +0 控件；窗=valid 槽=cached
[legacy_chat_window] round1: +334.0KB / +87.7KB pet / +49 控件 → 关后留存 333.5KB / 49 控件；cached
[todo_panel]         round1: +126.0KB / +41.1KB pet / +34 控件 → 关后留存 121.7KB / 0 控件；destroyed 槽=None
```

读法：第二轮 `new=False` 且 0 控件增长 ⇒ 反复开关**不涨**（不是泄漏）；关窗后
modern/legacy 的 61/49 个控件原样活着 ⇒ **不返还**（缓存单例）。待办面板关窗后
0 控件 ⇒ 本来就是关闭即销毁。

**2）改后同一探针（3 轮）**：

```
[modern_chat_window] round1: +2691.1KB total / +981.6KB pet / +61 控件 → 关后留存 2679.5KB / 970.6KB pet / 0 控件；窗=destroyed 槽=None
                     round2: new=True +-40.4KB / +56.8KB pet / +61 控件 → 关后 0 控件；destroyed 槽=None
                     round3: new=True +-80.1KB / +17.8KB pet / +61 控件 → 关后 0 控件；destroyed 槽=None
[legacy_chat_window] round1: +328.5KB / +80.5KB pet / +49 控件 → 关后 0 控件；destroyed 槽=None
[quick_chat]         round1: +140.1KB / +30.6KB pet / +18 控件 → 关后 18 控件；valid 槽=cached
                     round2/3: new=False +-0.2 / +0.0KB pet / +0 控件   ← 有意保留的缓存单例
[todo_panel]         round1: +126.6KB / +41.7KB pet / +34 控件 → 关后 0 控件；destroyed 槽=None
```

**3）本机真实进程内抓到的次生缺陷（已修）**：第一版实现（`WA_DeleteOnClose` +
`done()` 直接删）在 `pytest tests/test_chat_subsystem.py` 里打出

```
RuntimeError: Signal source has been deleted
  service.py:158 _stopped → self.stopped.emit(rid)
```

——worker 的排队信号落在服务已删之后。这就是 `ChatService.detach()` + `_detached`
判据的来源；补上之后同一用例不再打印任何 traceback。

**4）用户可见行为的确认**：`tests/test_chat_window_lifecycle.py` 走**真
`PetInstance` 入口**（`open_modern_chat` / `open_legacy_chat`）验证「关→开→发一条
消息」链路：重开是新对象、`service.send` 收到请求、回复写进会话；120 条历史时重开
首屏仍是 50 条 + 「加载更早」。**GUI 目视（真人点 X 看内存回落）未做**——本机口径
是 offscreen 探针 + 自动化用例，原因见下条。

**5）无法自动验证的能力**：任务管理器里「关窗后进程工作集是否回落」不能自动断言
——Windows 的分配器不保证把释放的页立刻还给 OS（工作集只会在内存压力下回收），
所以「工作集不降」既不能证明泄漏、也不能证明修好。可自动、且与内存真正相关的是
**存活控件数与 C++ 对象存活状态**，本报告用它作为判据。真实上屏/焦点行为同样未验
（offscreen 无窗口管理器）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet/chat/lifecycle.py pet/chat/widgets.py pet/chat/legacy_widgets.py pet/chat/service.py pet/app.py tests/test_chat_window_lifecycle.py` | All checks passed |
| 聚焦（聊天子系统） | `pytest tests/test_chat_subsystem.py -q` | **149 passed, 1 skipped** |
| 聚焦（新增生命周期） | `pytest tests/test_chat_window_lifecycle.py -q` | **14 passed** |
| 受影响面（app 级宿主/岛对话/多窗/分页/快速对话） | `pytest tests/test_overlay_app_hosts.py tests/test_island_chat.py tests/test_island_chat_identity.py tests/test_single_process_spawn.py tests/test_quick_chat.py tests/test_child_bubble_quick_chat.py tests/test_chat_history_paging.py -q` | **115 passed** |
| 时间线（关闭重开落最新回复） | `pytest tests/test_chat_timeline_scroll.py -q` | **2 passed**（改契约前该文件红 1 条） |
| 受影响面（设置收口/记忆/朗读/待办） | `pytest tests/test_settings_stale_form_exit.py tests/test_requested_regressions.py tests/test_chat_memory.py tests/test_chat_memory_extract.py tests/test_chat_reply_voice.py tests/test_todo_reminder.py -q` | **250 passed, 1 skipped** |
| 全量 | `QT_QPA_PLATFORM=offscreen pytest -q --ignore=tests/test_webm_reader_lifecycle.py --ignore=tests/test_webm_clip_lifecycle.py --ignore=tests/test_webm_first_frame_lock.py --ignore=tests/test_low_priority_warm_interaction_yield.py` | **8816 passed, 15 skipped**, 0 failed（30:24，日志 `.scratch/chat-window-close/full-suite2.log`） |

**断言有效性**：`test_reopen_keeps_history_paging` 在新旧实现下都能区分——若销毁
重建路径改成一口气渲染全部历史，`len(_bubbles)` 会变 120 而不是 50；
`test_chat_window_is_destroyed_on_close` 在改前实现下必红（`isValid` 为 True、
槽位仍 `cached`，这正是 §五-1 的探针读数）。

## 七、已知限制与后续

1. **`quick_chat` 仍是缓存单例**（有意）：18 控件 / 约 31 KiB、反复开关零增长。
   若将来气泡长起来（附件预览、图片输出），这条判断需要重新用同一个探针复测。
2. **销毁是异步的**（`deleteLater`）：`close()` 返回后到真正析构之间还有一个事件
   循环拍。所有读缓存槽的代码都改走 `_chat_window_usable()` / `isValid`，但第三处
   新增的裸引用仍应照此写。
3. **未验真实屏上的观感**：重开窗口的定位（`position_near_pet`）与首次开窗走同一条
   代码，但 offscreen 不验证窗口管理器下的焦点/置顶序列。
4. **退出路径的既有风险未扩大也未修复**：`_close_instance_subwindows` 在极端情况下
   （退出瞬间恰有在飞请求）仍可能先删窗口再让 worker 收尾；本批只是不再额外补刀
   （见 `_release_scheduled` 判断）。修它需要把退出路径也接到 lifecycle 上，属下一批。

## 八、风险与回滚

- **影响面**：只有 modern/legacy 两套完整聊天窗的生命周期；`quick_chat`、待办面板、
  设置页不受影响。没有新增配置键、没有落盘格式变化。
- **风险**：① 销毁后仍有代码读缓存槽 → 已用 `finished` 清槽 + `isValid` 三重收口
  （槽位判定 / `_present_dialog` 早退 / 已有的 `island` 可见性检查）；② 在飞 worker
  被宿主带走 → `has_running_workers` + 等 `finished` + `detach` 三层。
- **回滚**：`git revert` 本提交即可，无残留状态（窗口本来就是内存对象，没有落盘
  迁移）。回滚后行为逐位回到「关闭只隐藏」，用户会看到「关窗后内存不回落」的旧现象。

## 六点一、全量门结果（两轮，如实记录）

| 轮次 | 命令 | 结果 | 说明 |
|---|---|---|---|
| 第 1 轮 | 同 §六「全量」行 | **8815 passed, 15 skipped, 1 failed**（31:18） | 唯一红点 `test_chat_timeline_scroll.py::test_reopening_window_returns_to_latest_reply`——它先 `close()` 再对**同一个**窗口 `show()`，依赖旧的「关闭=隐藏复用」语义。属于本批语义变更必须同步的用例，不是产品缺陷。 |
| 修正 | `pytest tests/test_chat_timeline_scroll.py -q` | **2 passed** | 用例改成「关窗后重新构造窗口」，断言意图不变（重开仍贴最新回复底边）。 |
| 第 2 轮 | 同 §六「全量」行 | **8816 passed, 15 skipped, 0 failed**（30:24） | 全绿。日志 `.scratch/chat-window-close/full-suite2.log`。 |

四个 `--ignore` 与 CI 主套件同口径（webm 生命周期族按 `docs/PR-MERGE-LESSONS-2026-09-12.md`
的时序族隔离先例）。
