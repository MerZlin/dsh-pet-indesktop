# PR 报告：气泡独立鼠标穿透（issue #367，2026-10-25）

> **基线**：`34d810b1`（upstream/main，v4.4.0 素材架构换代之后）
> **分支**：`fix/issue-367-bubble-click-through`　**日期**：2026-10-25
> **范围**：10 个文件（实现 8、测试 2、文档 3——本报告 + SETTINGS-CHANGE-GATES 变更记录 + INDEX 登记）
> **关联**：[issue #367](https://github.com/MerZlin/dsh-pet-indesktop/issues/367)（功能建议：气泡独立鼠标穿透）

## 一、核心特性

桌宠气泡（歌词/提示/审批弹窗）是独立置顶 Tool 窗口，此前落在气泡矩形内的点击会被
原生窗口"吃掉"——`WA_TransparentForMouseEvents` 只是 Qt 不处理事件，不会穿透到其它
应用。整宠「鼠标穿透」（`mouse_through`）开启时想点宠物本体只能去托盘关掉整个穿透。
本刀给气泡单独一个 OS 级穿透开关（配置键 `bubble_click_through`，默认关）：开启后
点击落在气泡上直接穿透到下层窗口，桌宠本体保持可交互，两开关相互独立、可任意组合。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 气泡独立穿透 | `WS_EX_TRANSPARENT`（复用 `platform_win._set_windows_click_through`，按 HWND 生效），零新增 Win32 代码 |
| 2 | 审批按钮自动让位 | 交互气泡（审批/提问按钮）弹出期间临时撤销穿透，收起后按意愿恢复 |
| 3 | 窗口重建样式重放 | `setWindowFlags` 重建原生窗口会丢扩展样式位，`showEvent` 无条件重放 |
| 4 | 设置页入口 | 「互动 → 输入」组「气泡鼠标穿透」行；仅 Windows 创建（其他平台无此原语，行不出现） |
| 5 | 双拓扑接线 | legacy `PetWindow`（构造 + `refresh_pet_settings` 热改）与 overlay 壳（主气泡 + 逐 sprite 气泡，配置热改同步） |

**红线 / 不变量**：
- 默认关 = 气泡代码**从不调用** Win32 穿透原语，与旧版逐位一致（测试钉死）；
- 捕获兼容模式（气泡是主窗子控件、无独立 HWND）下开关完全不生效——绝不能把穿透
  样式写到主窗 HWND 上；
- 非 Windows 平台零原生调用、设置行不创建（不留"改了没反应"的活开关）；
- 交互气泡（`_interactive_active`）期间穿透必须撤销——按钮必须能点。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/speech_bubble.py` | +67 / −0 | ① import `_set_windows_click_through`；② `__init__` 加 `_click_through_wanted`（意愿）与 `_native_through_applied`（"是否碰过原生样式"闸门）；③ `set_click_through()` 幂等意愿入口；④ `_sync_native_click_through()` 收敛（平台/捕获/可见三闸 + 默认关零调用 + 调原语）；⑤ 新增 `showEvent` 重放（本类原先只有 `hideEvent`）；⑥ `show_text` 交互分支与 `_teardown_interactive` 各接一次收敛（撤销/恢复） |
| `pet/config.py` | +5 / −0 | 新全局键 `bubble_click_through` 三处登记：`_initial_data` 默认 `False`、`reload` 白名单（`mouse_through` 旁）、`_normalize_pet_settings` 的 `_bool_or_default` 归一（盘上字符串脏值不误开） |
| `pet/settings_pet_controls.py` | +7 / −0 | `bubble_through_check` 控件，仅 `sys.platform == "win32"` 创建（同 `cursor_hidden_passthrough` 口径） |
| `pet/settings_interaction.py` | +15 / −1 | 「输入」组在「鼠标穿透」后追加「气泡鼠标穿透」行（条件装配，控件为 None 时不建行）；说明写明独立、审批让位、快速对话影响、仅 Windows |
| `pet/settings_search.py` | +3 / −0 | 口语别名「气泡穿透」→ `bubble_click_through`（label 是「气泡鼠标穿透」，子串匹配认不出省掉"鼠标"的半截说法） |
| `pet/modern_settings_dialog.py` | +2 / −0 | `_write_config` 写回（None 守卫，同 `cursor_hidden_passthrough_check`） |
| `pet/window.py` | +10 / −0 | legacy 宿主：构造处下发意愿（getattr 防御式，同 `set_text_scale` 注入口径）+ `refresh_pet_settings` 热改跟随（设置保存后立即生效） |
| `pet/overlay_shell.py` | +19 / −0 | `_apply_bubble_click_through()`（`_apply_bubble_text_scale` 的兄弟，同 getattr 守卫），挂在三个既有注入点：`_bind_main_bubble_signals`（主气泡）、`_make_bubble_follower`（逐 sprite 气泡创建）、`_refresh_sprite_bubble_style`（配置热改） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_bubble_click_through.py` | +255（新文件） | 12 例：默认关零调用、开/关切换与幂等、隐藏中意愿首显落地、审批撤销/恢复、重建后 showEvent 重放、捕获子模式全跳过 + 退出重放、非 Windows 零调用、overlay 注入 seam、设置行默认/落盘/重开、非 Windows 不建行、搜索可达（别名 + 关键词）、配置默认/脏值归一/往返 |
| `tests/test_config_schema.py` | +2 / −0 | 键清单快照补 `bubble_click_through` |

### 未改动（看起来相关但故意没动）

- `pet/platform_win.py`：原语逐字节未动（AGENTS 红线：Win32 层任何改动都可能真实崩溃）；
- `pet/sprite_bubble.py`（overlay 气泡跟随器）：它包着真 `PetSpeechBubble`，注入走壳的
  `_apply_bubble_click_through` → `follower.bubble.set_click_through`，跟随器自身无需新 API；
- `WindowsPerPixelInputController`：只看主宠窗口自己的 HWND，与气泡 HWND 无耦合；
- `_try_open_quick_chat_from_bubble`（window.py）：**保持不动**——穿透开启后落在气泡上
  且下方是桌宠本体的点击，仍会由点宠处理链接管并打开快速对话（见 §七）；
- 右键菜单/托盘不加气泡穿透快捷开关（issue 只要求设置页入口）。

## 三、实现要点

- **意愿与生效分离**：`set_click_through` 只记录用户意愿并触发收敛；实际是否写
  `WS_EX_TRANSPARENT` 由 `_sync_native_click_through` 判定——有效态 =
  `意愿 ∧ 非交互气泡 ∧ （平台=win32 ∧ 非捕获子模式 ∧ 可见）`。
- **默认关零 Win32 调用**：`_native_through_applied is not True` 且目标也是关 → 直接
  返回。开关从未打开过的进程与旧版逐位一致（测试 `test_default_off_never_touches_native_style`）。
- **showEvent 无条件重放，不检测句柄变化**：初版按 `WindowsPerPixelInputController`
  的思路缓存 `(applied, hwnd)` 靠 winId 变化检测窗口重建——实测算发现 offscreen 平台
  winId 恒为 1，检测不可移植。改为开启期间每次可见化都调一次原语收敛：helper 自带
  "样式已相同则只读不写"幂等（实测稳态 0.36 µs/次 ≈ 一次 `GetWindowLongW`），与主窗
  `NonRudeHWND` 的 showEvent 重放同口径。
- **关闭态收起后再显示不重放**：`有效态=False 且 applied 不是 True` 时跳过——新原生
  窗口本就没有穿透位，恢复"关"不需要任何写。
- **隐藏期间的意愿变化推迟到 showEvent 落地**（启动期宿主先下发意愿的场景），
  实机探针曾因此抓到"显式关闭时气泡已隐藏、位残留"的时序——隐藏窗口不参与命中
  测试，无用户可见影响，且下次显示必然收敛。
- **overlay 注入与 `bubble_text_scale` 完全同构**：同三个挂点、同 getattr 守卫（测试
  替身气泡不实现 setter 时不抛）、同"缺省读主配置"取源。

## 四、性能分析

**方法（可复现）**：`.scratch/bubble-click-through/probe_real_hit_test.py`（真实
`windows` 平台插件，非 offscreen；`D:\code\Web\deep seek\dsh-pet-367` 工作树，
Python 3.11.9 / PySide6 6.x，20000 次样本）

| 指标 | 实测 | 归属 |
|---|---|---|
| `_sync_native_click_through` 稳态（开启中、目标态不变） | **0.36 µs/次**（20000 次 7.29 ms） | 新增 |
| 单次 `GetWindowLongW` 对照 | 0.31 µs/次（20000 次 6.22 ms） | 新增（收敛即一次读+比较） |
| 默认关全程（构造→显示→隐藏→再显示） | **0 次原生调用**（测试钉死） | 新增路径不触发 |
| 意愿切换（开→关、审批撤销/恢复） | 每次跃迁 1 次读 + 至多 1 次写 | 新增，用户操作频度 |

**结论**：
① 稳态开销：功能开启时每次气泡可见化多 0.36 µs（一次样式读）；气泡显示是用户可见
事件（分页翻页不重显、sticky 刷新不触发 showEvent），频率 ≪ 1Hz，绝对量级 10⁻⁷ 秒。
功能关闭（默认）时零调用、零开销。
② 新增路径成本与频率如上表；无新增轮询/定时器——收敛只挂在状态跃迁
（`set_click_through` / 交互进出 / showEvent）上。
③ 新增系统调用仅 `GetWindowLongW`（读）与 `SetWindowLongW`（跃迁时写）两个既有
原语的调用，无网络、无磁盘、无线程。
④ 内存：每气泡两个标量字段（`bool` + `bool|None`），无缓存增长（初版的 hwnd 缓存
已随重放口径移除）。

## 五、实机运行记录

**环境**：本机真实 Windows（win32 10.0.22631，真实 `windows` 平台插件、真实 HWND），
命令：`python .scratch/bubble-click-through/probe_real_hit_test.py`。

**命中路由端到端**（`WindowFromPoint`，坐标经 `GetWindowRect` 换算成物理像素——Qt 逻辑
坐标在缩放桌面下与物理坐标不一致，是首轮探针误报 Chrome 的根因）：气泡盖在普通
靶窗上，取气泡物理中心点做命中测试——

```text
[默认关]   hit=528274 (气泡自身 Qt Tool 窗)  样式位TRANSPARENT=False   ← 点击被气泡接住（旧行为）
[开启穿透] hit=528318 (下层靶窗)            样式位TRANSPARENT=True    ← 点击穿透到下层窗口
[审批按钮期间] hit=528274 (气泡自身)          样式位TRANSPARENT=False   ← 撤销穿透，按钮可点
[收起恢复] hit=528318 (下层靶窗)            样式位TRANSPARENT=True    ← 按意愿恢复
[显式关闭] hit=528274 (气泡自身)            样式位TRANSPARENT=False   ← 干净回退
```

五态全对：`GWL_EXSTYLE` 的 `WS_EX_TRANSPARENT` 位随状态翻转，且 OS 命中测试在同一
点上的落点随之切换——这就是"点击穿透到下层窗口"的机制本体（与整宠 `mouse_through`
依赖同一位）。

**用户可见行为确认**：上表即程序化确认（命中下层靶窗 = 用户点击会落到下层应用）。
设置页行为（行存在、默认关、勾选保存重开保持、搜索「气泡穿透」可达）由
`tests/test_bubble_click_through.py` 的真实 `ModernSettingsDialog` + 真实 `Config`
用例覆盖（offscreen 平台跑 GUI 控件树，非 mock 断言）。

**边界与失败路径实机观察**：
- 捕获兼容模式（气泡=主窗子控件）整链跳过——offscreen 用例钉死"一次都不写"
  （防止把穿透写到主窗 HWND）；实机未搭完整捕获环境，此边界由单测承担并在报告如实注明。
- 隐藏期间 `set_click_through(False)` 不立即清位（不可见闸门），下次 showEvent 收敛——
  实机探针首轮抓到该时序，已确认无用户可见影响（隐藏窗口不在命中链）。

**无法自动验证的能力**：非 Windows 平台的真实行为（本机仅 Windows）——按仓库纪律
"不推断未执行的平台结果"，非 Windows 腿由 CI（ubuntu/macos）的 `sys.platform` 模拟
用例覆盖"零调用、不建行"两条契约。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests` | All checks passed |
| 聚焦（新文件） | `pytest -q tests/test_bubble_click_through.py` | **12 passed**（实现前同文件 6 failed + 6 errors 先红在案） |
| 相关族 | schema/设置 9 文件 344 passed；气泡族 10 文件 153 passed | 全绿 |
| 受影响族高负载复跑 ×3 | 气泡 6 文件 | 95 passed × 3（5.7~5.9 s/轮） |
| 全量 | `python -m pytest -q` | **9065 passed / 25 skipped / 1 failed**（1246.98 s）；唯一红是 `test_modern_settings_dialog_py_line_budget`——本刀给 `_write_config` 加的 2 行触发预算绊线，已按该守卫自述的口径把预算 2722 → 2724 校准（带日期与理由注释，`tests/test_architecture.py`），校准后该文件 16 例全绿；未为 3 行测试常量改动重跑 21 分钟全量（红因单一且确定性：读文件行数） |

先红证据：实现前 `tests/test_bubble_click_through.py` 单独运行为
`6 failed / 6 errors`（fixture 在 `speech_bubble` 上找不到
`_set_windows_click_through` 属性、设置行/配置键不存在）——产品改动落地后 12 passed。

## 七、已知限制与后续

- **穿透期间"点气泡打开快速对话"不再触发**（点击落到下层）：这是功能的定义本身
  （"点击穿透"与"点击气泡做事"物理上不可兼得）。点**桌宠本体**仍可打开快速对话
  （`_try_open_quick_chat_from_bubble` 的存在使落在气泡与本体重叠区的点击仍被点宠
  处理链接管）；设置行说明已写明。行说明同时写明审批/提问按钮弹出期间自动恢复可点。
- 捕获兼容模式（`stream_capture_mode`）下开关静默不生效（气泡无独立 HWND）；设置行
  未按捕获模式置灰——该模式是重启级拓扑开关，用户极少停留在该状态，如后续反馈
  集中可补"跟随置灰"。
- macOS/Linux 无此能力（无 `WS_EX_TRANSPARENT` 等价原语的接入层），行不创建；
  如后续要用 `WindowTransparentForInput`（会重建原生窗口）接入，须重评频闪风险。

## 八、风险与回滚

- 影响面：气泡窗口的输入路由（新键默认关 = 零行为变化）；主宠/overlay 宿主各一处
  注入；设置页一行。
- 新增键：`bubble_click_through`（全局，默认 `False`，`Config.reload` 白名单内）。
- 回滚：`git revert` 单提交即可；盘上残留的 `bubble_click_through: false` 键对旧版
  代码是未知键，`Config` 读入即弃（同 `spawn_inherit_dynamic_island` 退役先例），
  无需迁移清理。
