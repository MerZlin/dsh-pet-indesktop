# PR 报告：穿透卡死——click-only grab 看门狗收尾 + 自动穿透态双时机收敛

> **基线**：`d6bd3f3`（main，含 #272）
> **分支**：`fix/stuck-cursor-passthrough`　**日期**：2026-10-15
> **范围**：8 个文件（实现 3、测试 3、文档 2）
> **关联**：用户反馈「没开灵动岛时桌宠拖不动」；双侧独立定位（本仓静态穷举 + 报告方 AI 的合成单击取证）

## 一、核心特性

**症状**：overlay 拓扑下桌宠窗口「看得见但点不到」——单击、拖拽全部零反应，
且点击无法自愈（点击本身被穿透吃掉）。报告方一度误判为「灵动岛开关」相关。

**根因**（两条独立证据链同指一处）：

1. click-only 按下（锁定位 / 未按 SHIFT）只设 `_mouse_grab`、不设
   `_press_global`（`pet/overlay_window.py:717-726`），而看门狗
   `_check_stale_press` 首行 `if self._press_global is None: return`——
   click-only 留下的 grab **永远无人收尾**。
2. 一次丢失的 release（alt-tab / 模态抢焦点 / 屏迁移）后 `_mouse_grab`
   永久卡住 → 壳层 `_cursor_transition_blocked()` 恒真 → 光标隐藏态触发的
   整窗穿透（`WA_TransparentForMouseEvents`）再也退不出去。
3. 「灵动岛」的关联是共因错觉：岛的拖拽状态翻转会顺带补一次穿透态收敛
   （冲掉卡死），且关岛只能进设置页保存（保存触发的热重载也会重下发输入
   状态）——岛是偶然的"自我修复器"，不是病因。报告方 AI 的合成单击取证
   （事件零到达）锁定了穿透方向，与本仓静态穷举结论互证。

**修法**（两道，互为兜底）：

| # | 修复 | 说明 |
|---|---|---|
| 1 | 看门狗补 click-only/孤儿 grab 收尾 | `_press_global` 空但 `_mouse_grab` 非空、且左键已不在按下态 → `_finish_grab`；真拖拽路径逐位不变；合法长按（左键仍按下）不收 |
| 2 | 自动穿透态收敛（无闸即清） | 新增 `OverlayShell._converge_auto_cursor_hidden`（幂等）：显示/恢复与真到达窗口的输入两个时机触发；无滞留不动、grab 在途记 `_converge_pending` 待收尾后补做、否则即清。真实 HIDDEN 由 20Hz watcher 一拍内重建（屏迁移后 ≤50ms 误拦截窗口是刻意接受的自愈代价）。用户手动穿透不受影响 |
| 3 | 看门狗同口径收瞄准会话 | click-only 按住也能进弹弓瞄准（既有宽松判据，已登记 BACKLOG）；看门狗 click-only 分支与拖拽支同口径：先 `_cancel_slingshot` 收会话再收尾，sprite 不停 DRAG、右键菜单不被拦 |

**红线 / 不变量**：真拖拽语义逐位不变（`_press_global` 非空分支不改）；
grab 在途时收敛不立即动手（记待办，grab 收尾后补做）；
`mouse_through` 用户手动开关不受收敛影响；非 Windows 平台零行为变化。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/overlay_window.py` | +44 / −3 | ① `_check_stale_press` 补 click-only/孤儿 grab 收尾分支（同判据：左键不在按下态；docstring 写明为什么只看 grab 不看 click-only 旗标），分支内先 `_cancel_slingshot` 收瞄准会话再收尾（与拖拽支同口径）；② `_note_user_input` 尾部挂 `_user_input_cb`（getattr 守卫，无壳 no-op）；③ `showEvent` 在可见性上报**之前**调 `_shown_cb`（先收敛再补拍）；④ V-7 行内注释同步新范围 |
| `pet/overlay_shell.py` | +74 / −59 | 新增 `_converge_auto_cursor_hidden`（幂等两道闸：无滞留不动 / grab 在途记 `_converge_pending` 由 `_on_grab_finished` 补做，否则即清 + `_apply_effective_mouse_through`）；`__init__` 与屏迁移重建两处装配 `_shown_cb`/`_user_input_cb`（同入口同语义）；`_cursor_transition_blocked` docstring 改准（任意 grab 在途，含 click-only）；docstring 如实写明撤 HIDDEN 闸的裁决与代价 |
| `pet/tick_driver.py` | +1 / −1 | 行内注释同步（"卡死的拖拽"→"卡死的 grab"） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_overlay_hardening.py` | +64 / −1 | V-7 族新增看门狗 click-only 收尾回归 + 守卫（合法长按不收尾、孤儿 grab 收尾） |
| `tests/test_overlay_window_capabilities.py` | +132 / −28 | 光标可见性族新增：卡死复现链（丢 release → grab 滞留 → 穿透锁死 → 修复后解开）、输入到达收敛、显示/恢复收敛（真 show→hide→show 循环）、P2-1 组合（生产路径 HIDDEN 置位 + 卡死 grab + UNKNOWN 探针 → 记待办 → 看门狗收尾后补做解开）、P2-2 改判（show 先清 → watcher 重报 HIDDEN 再建穿透）；守卫：在途 grab 记待办不动手、合法长按不收尾 |
| `tests/test_sprite_slingshot.py` | +33 / −0 | 看门狗 click-only 分支收瞄准会话的红→绿回归（grab 清掉且 sprite 不停 DRAG） |

红→绿证据（修前红点关键行）：
`assert overlay._mouse_grab is None`（FakeGrab 仍在）；`assert True is False`
（`_auto_cursor_hidden` 滞留）。四条回归修前全红、修后全绿；三条守卫前后皆绿。
一处自查修正记录：显示侧用例第一版假绿（真实光标探针补拍把状态清了），
已把探针静音为 `UNKNOWN`（watcher docstring 认可的注入缝）后重取证。

## 三、实现要点

**为什么是两个时机而不是只修看门狗**：看门狗修的是"grab 卡死"这个上游病因；
但穿透态本身还缺一条"状态与现实的收敛通道"——原生穿透样式被窗口重建后
窗口其实收得到点击，旧逻辑里这种状态只能等 watcher 下一拍。显示/恢复与
真实输入是仅有的两个"确定能重新判断"的时机，补上后即使未来出现新的
滞留路径，用户点一下也能自愈，不会永久卡死。

**双审返修（收敛语义的迭代）**：初版收敛是"无 grab 即清"；一审补了 grab 在途记
待办 + 最后已知 HIDDEN 守闸；二审证明 HIDDEN 闸本身制造新的永久卡死（生产上
`_auto_cursor_hidden` 必由 HIDDEN 回调置位，探针转 UNKNOWN 后再无回调，待办补做
永远被闸住）——终审撤掉 HIDDEN 闸：**无 grab 阻挡即清，真实 HIDDEN 由 watcher
一拍内重建**。屏迁移后 ≤50ms 的误拦截窗口（计数 1→2→1 不翻转、不补探测）是
刻意接受的自愈代价。输入时机的定位保持"脱同步兜底"（真穿透开着时点击到不了
窗口；能进来即证明原生样式与标记脱同步，收敛回一致）。

**已知边界（如实登记）**：看门狗两支都依赖 `QApplication.mouseButtons()`——
若 release 落到别的应用且本进程此后不再收到任何鼠标事件，Qt 可能一直报
左键按下（拖拽支改动前同此口子）。补齐需要挂钟超时或 `GetAsyncKeyState`，
会引入"误杀合法长按"的新语义，本 PR 不动，留实机验证决定。

另有三处同族残留（均已登记 BACKLOG，本 PR 不动）：

- `OverlayShell._end_drag`（屏迁移链）直接清 `_mouse_grab`/`_press_global`
  不走壳通知——自愈路径是迁移随后的 `show()` 收敛（注意：迁移因
  `new_screen is None` 提前返回时没有这个 show 保证，仅正常迁移链成立）；
  它也不清 `_press_click_only`/`_drag_committed`、不调 `set_drag_active(False)`。
- `hideEvent`/`closeEvent` 不结束在途 grab（既有行为，click-only 侧已由
  看门狗覆盖）。
- click-only 按下能进弹弓瞄准（进入判据宽松）：本 PR 只保证看门狗收会话，
  进入条件本身的收紧归输入链路片。

## 四、性能分析

受影响路径两条：穿透轮询里的看门狗调用（每拍一次）与输入/显示时的收敛钩子。
实测（本机 Windows 11，Python 3.13，offscreen QApplication，n=10000 取均值）：

| 路径 | 实测 | 触发频率 |
|---|---|---|
| `_converge_auto_cursor_hidden` 稳态短路（无滞留时两次布尔判断） | **0.282 µs/次** | 每次显示/恢复 + 每次真到达窗口的输入 |
| `QApplication.mouseButtons()`（看门狗新分支的按键态读取） | **2.159 µs/次** | 穿透轮询每拍一次（且只在持有 grab 时才读） |

无新增线程/定时器/系统调用；内存零增长（一个布尔待办 + 两个函数指针属性）。
两条路径开销均在微秒级、触发频率受人手/显示事件上限约束，可忽略。

## 五、实机运行记录

本机 Windows 11，offscreen 无法复现"OS 层丢 release"，故实机段验证的是
**修复不破坏正常交互**（回归面）而非复现原症状。源码态启动终版
（`python -m pet`，overlay 拓扑，kimi-cu 驱动真实鼠标输入）：

- 单击主宠：正常（点击反馈/台词）；拖拽：跟随与松手放下正常；
  右键菜单：正常弹出。
- 卡死链路的确定性复现与修复后验证由测试承担（真实 Qt 事件分发 +
  `QApplication.sendEvent`；丢失 release 用 FakeGrab 直接构造卡死态，
  这是该缺陷唯一可确定的复现方式——如实说明）。

局限：真实"alt-tab 丢 release"的端到端复现没有自动化路径（OS 级事件丢失
不可脚本化）；修复后的长期表现依赖用户侧观察。

## 六、验证

- 聚焦集（输入/穿透/overlay 族 21 文件）：**374 passed**（重复 3 次一致）；
  扩大批（overlay + 共享/托盘/交互探针 35 文件）：**661 passed, 0 failed**。
- 换目录复跑（绝对路径）：59 passed（防路径硬编码）。
- `ruff check` 全部改动文件干净。
- 全量套件结果见 PR 描述（推前闸门）。
- 双盲审：第一轮 astra 需返修（P2×2：收敛请求丢弃 / 迁移误清——均已修并补
  组合回归）、ops5.5 可推（minor×2：瞄准会话收尾、注释诚实化——均已修；
  nit×2 已处理）；第二轮 astra 复核再提两条（HIDDEN 闸制造新永久卡死、
  待办丢请求语义）→ 终审撤闸简化（无 grab 即清 + watcher 一拍重建），
  终版复核结论见 PR 描述。
