# PR 报告：穿透卡死——click-only grab 看门狗收尾 + 自动穿透态双时机收敛

> **基线**：`d6bd3f3`（main，含 #272）
> **分支**：`fix/stuck-cursor-passthrough`　**日期**：2026-10-15
> **范围**：5 个文件（实现 3、测试 2）
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
| 2 | 自动穿透态双时机收敛 | 新增 `OverlayShell._converge_auto_cursor_hidden`（幂等）：每次显示/恢复（`_shown_cb`）+ 任何真到达窗口的鼠标输入（`_user_input_cb`）时，无拖拽在途即清 `_auto_cursor_hidden`；用户手动穿透不受影响 |

**红线 / 不变量**：真拖拽语义逐位不变（`_press_global` 非空分支不改）；
拖拽在途时收敛绝不动手（归 `_cursor_restore_pending` 那一路）；
`mouse_through` 用户手动开关不受收敛影响；非 Windows 平台零行为变化。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/overlay_window.py` | +34 / −3 | ① `_check_stale_press` 补 click-only/孤儿 grab 收尾分支（同判据：左键不在按下态；docstring 写明为什么只看 grab 不看 click-only 旗标）；② `_note_user_input` 尾部挂 `_user_input_cb`（getattr 守卫，无壳 no-op）；③ `showEvent` 在可见性上报**之前**调 `_shown_cb`（先收敛再补拍，光标真隐藏时同一拍 watcher 会重新报 HIDDEN 置回，不会被抹掉）；④ V-7 行内注释同步新范围 |
| `pet/overlay_shell.py` | +32 / −0 | 新增 `_converge_auto_cursor_hidden`（幂等；`_auto_cursor_hidden` 为假或拖拽在途即返回，否则清滞留 + `_apply_effective_mouse_through`）；`__init__` 与屏迁移重建两处装配点各挂 `_shown_cb`/`_user_input_cb` |
| `pet/tick_driver.py` | +1 / −1 | 行内注释同步（"卡死的拖拽"→"卡死的 grab"） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_overlay_hardening.py` | +64 / −1 | V-7 族新增看门狗 click-only 收尾回归 + 守卫（合法长按不收尾、孤儿 grab 收尾） |
| `tests/test_overlay_window_capabilities.py` | +107 / −1 | 光标可见性族新增：卡死复现链（丢 release → grab 滞留 → 穿透锁死 → 修复后解开）、输入到达收敛、显示/恢复收敛；守卫：在途 grab 时收敛不动手 |

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

**已知边界（如实登记）**：看门狗两支都依赖 `QApplication.mouseButtons()`——
若 release 落到别的应用且本进程此后不再收到任何鼠标事件，Qt 可能一直报
左键按下（拖拽支改动前同此口子）。补齐需要挂钟超时或 `GetAsyncKeyState`，
会引入"误杀合法长按"的新语义，本 PR 不动，留实机验证决定。

## 四、性能分析

- 看门狗新分支：只在 `_press_global is None` 且 `_mouse_grab` 非空时多一次
  `mouseButtons()` 读取（纳秒级 Qt 状态读），穿透轮询每拍一次的既有节奏不变。
- 收敛钩子：`_shown_cb` 每次显示/恢复一次；`_user_input_cb` 挂在
  `_note_user_input`（真实输入事件频率，人手动上限），函数体前两条短路
  （`_auto_cursor_hidden` 为假 / 拖拽在途）即返回。稳态开销 = 零（状态正常时
  两次布尔判断）。
- 无新增线程/定时器/系统调用；内存零增长（两个函数指针属性）。

## 五、实机运行记录

本机 Windows 11，offscreen 无法复现"OS 层丢 release"，故实机段验证的是
**修复不破坏正常交互**（回归面）而非复现原症状：

- 源码态启动（`python -m pet`，overlay 拓扑）：主宠单击（台词/音效正常）、
  拖拽跟随、松手放下、右键菜单、开关鼠标穿透——均正常。
- 子宠 spawn 后同项复验正常；灵动岛开/关切换后拖拽正常（原报告场景）。
- 卡死链路的确定性复现与修复后验证由测试承担（真实 Qt 事件分发 +
  `QApplication.sendEvent`；丢失 release 用 FakeGrab 直接构造卡死态，
  这是该缺陷唯一可确定的复现方式——如实说明）。

局限：真实"alt-tab 丢 release"的端到端复现没有自动化路径（OS 级事件丢失
不可脚本化）；修复后的长期表现依赖用户侧观察。

## 六、验证

- 聚焦集（输入/穿透/overlay 族 21 文件）：**371 passed**（重复 3 次一致）；
  扩大批（overlay + 共享/托盘/交互探针 35 文件）：**659 passed, 0 failed**。
- 换目录复跑（绝对路径）：32 passed（防路径硬编码）。
- `ruff check` 全部改动文件干净。
- 全量套件结果见 PR 描述（推前闸门）。
- 双盲审结论见 PR 描述（ops5.5 / astra）。
