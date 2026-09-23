# PR 报告：issue #186 恢复多显示器跨屏拖拽/抛掷 + 托盘图标消失 + 右键菜单「鼠标穿透」去重（2026-09-23）

> **基线**：`main@7d622db`（v4.2.1 已发布）→ 分支 `fix/186-cross-screen-tray-menu-2026-09-23`（分支名刻意不含任何第三方品牌词——`tests/test_desktop_pet_features.py::test_product_copy_has_no_external_brand_reference` 会扫 `docs/`）
> **提交**：`ea17bfa`（右键菜单去重）→ `cd7821b`（#186 跨屏）→ `b37a44c`（托盘图标）→ 本报告所在 docs 提交
> **三项互不依赖**，可分别 `git revert`；本报告按 `AGENTS.md` 的 Delivery evidence discipline 交付三份证据（§二 / §四 / §五）。
> **关联**：issue #186（本 PR 关闭）；相邻文档见 [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)、[`CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md`](CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md)、[`WINDOW_PY_SPLIT_GUIDE.md`](WINDOW_PY_SPLIT_GUIDE.md)、[`PR-REPORT-ISLAND-RESHOW-NOCHAT-2026-09-23.md`](PR-REPORT-ISLAND-RESHOW-NOCHAT-2026-09-23.md)（同一批发布后补丁）。

---

## 一、三个问题与根因（都已核实到提交/代码级，不是推测）

### 1. issue #186：4.2.1 起桌宠丢不到副屏

用户反馈原文：*「用 4.2.0 版本，桌宠可以被从主副屏来回丢，4.2.1 更新完发现丢不到别的屏幕去了。」*

| 版本 | 拖拽落位 | 抛掷反弹边界 |
|---|---|---|
| 4.2.0 | `_tick_drag_physics` 直接 `self.move(...)`（**无钳制**）→ 窗口跟着手能到副屏 | `_tick_throw_physics` 里 `avail = self._screen_available().availableGeometry()` + `margin = self._w/3` |
| 4.2.1 | 统一进 `move_window_towards`，把**身体框 + 窗口**钳进 `host.screen().availableGeometry()` | `throw_bounds` 同源，同样只用本屏可用区 |

- `b418733`（#137「Linux 屏幕边缘无法贴边」）**不在 v4.2.0 里**（`git merge-base --is-ancestor b418733 v4.2.0` → 否），它把落位统一成「主动钳进当前屏工作区」，顺手把跨屏能力收掉了；`throw_bounds` 用同一份边界，于是抛掷也在本屏边缘反弹。
- **只修抛掷不够**：拖拽路径（`pet/window.py:3319/3377/3388`、`_consume_drag_move` 的 `:3201`）走的是同一个 `_move_window_towards`——把请求 2500（副屏中央）交给它，会被钳在本屏右缘（`tests/test_multi_screen_interaction.py::test_without_snapshot_drag_stays_on_current_screen` 就是这条对照）。桌宠连副屏都到不了，抛掷自然无从谈起。
- 边缘探头（`c2d0e10`）只影响探头露出量的坐标系换算（`pet/edge_probe.py:301-305/513-525` 走的是显式 `body_bounds`），与反弹墙无关，**本批不动它**。

### 2. 托盘图标消失（4.2.1 起）

`_build_tray` 在 `win.show()` 之后**立刻**取 `QIcon(win.icon_pixmap())`；而 4.2.1 的 `da8f291`（perf：解码 GUI 减负）把「jumpToFrame 冷路径 GUI 同步解码首帧」删掉了，`pet/webm_clip.py` 里写得很明确：*「GUI 线程永不同步解码首帧」*。此刻 `_frame_pixmap` 还是 `None`、`clip.currentPixmap()` 也是 `None`，于是 `icon_pixmap()` 返回空 `QPixmap` → 托盘条目**没有图标**；全仓除 `_build_tray` 外**没有任何地方**再 `setIcon`，图标就永久缺失。用户侧表现即「托盘图标消失了」——而这个条目正是桌宠隐藏/鼠标穿透后的恢复入口。

### 3. 右键菜单「鼠标穿透」是重复入口

同一条开关挂在三处：设置页（`setting_id=mouse_through`）、系统托盘菜单、右键菜单（modern 模板 + legacy 树）。按主人要求只删右键菜单那份，设置页与托盘保留——穿透开启后桌宠不再接收鼠标事件，托盘是唯一的一键恢复/切换入口（issue #74 当初专门要求过可用恢复入口，`pet/window.py:4205` 的恢复提示也正是这么写的）。

---

## 二、修改文件说明

`git diff --numstat`（三个提交合计，相对 `origin/main`）：

| 文件 | +/- | 改了什么 + 为什么 |
|---|---|---|
| `pet/window_placement.py` | +113/−5 | **#186 的全部几何**：新增 `DesktopArea`（各屏可用区的包围矩形 + 逐屏可用区）、`desktop_area()`（有效屏 ≥2 且几何可用才返回快照，否则 `None`）、`band_bounds()`（按宠物当前所在屏幕带收窄包围矩形，防错位拼接的空洞）；`move_window_towards()` / `throw_bounds()` 接受 `interaction_area`（缺省读 host 上的快照），显式 `body_bounds`（边缘探头）优先级最高；`throw_bounds` 拿不到屏幕对象时退化为当前位置零尺寸盒（冻结）而不是抛异常；模块 docstring 补多屏入口与报告指针。 |
| `pet/window.py` | +23/−0 | **只做接线**：`_interaction_area` 快照的取用/释放（`__init__`、拖拽起点、`_enter_physics_mode`、`_stop_physics`、普通拖拽松手）；托盘用的一次性信号 `frame_ready`（声明 + 首帧标志 + `_rebuild_frame` 里发一次，发射点用 `getattr` 兜底以兼容只挂载 `_rebuild_frame` 的假窗口——这一条是全量套件抓出来的，见 §六.5）。物理 tick 里只读快照，不重复枚举显示器。 |
| `pet/app.py` | +22/−2 | `_build_tray`：图标为空时先用 `_tray_placeholder_icon()`（复用既有 `vector_menu_icon(_, "pet", 64)`，不新增素材、不依赖窗口 QSS/DPR）保证托盘可见，并订阅 `frame_ready` 在首帧就绪后换成角色头像；新增 `_tray_placeholder_icon` 方法。 |
| `pet/context_menus/registry.py` | +2/−4 | 注册表去掉 `mouse_through` 的 spec/标签/图标三条登记 + import，右键菜单不再有该项。 |
| `pet/context_menus/legacy.py` | +0/−2 | legacy 菜单树去掉 `add_mouse_through` 调用与 import。 |
| `pet/context_menus/shared.py` | +0/−9 | 删除 `add_mouse_through`（删完即死代码；全仓只有上面两处引用，无测试引用、无再导出）。 |
| `pet/menu_templates/modern-default-v1.json` | +0/−1 | modern 默认模板 `桌宠控制` 子菜单去掉该节点。 |
| `pet/config.py` | +30/−1 | `_clean_menu_layout_override` 载入时一次性剔除用户旧布局里残留的 `mouse_through` 节点——菜单编辑器没有「删除菜单项」操作，不清理会被一直回存成「此平台不可用」幽灵项（`SETTINGS-CHANGE-GATES.md`「旧 key/旧入口有明确去向」）。 |
| `scripts/probe_multi_screen_area.py` | +150/−0（新） | 多屏探针：打印逐屏 geometry/availableGeometry/DPR、算出的活动区域、「往每块屏拖」的无快照/有快照对照与抛掷四边。本机单屏时明确打印「不适用」——这就是「本机为什么不能真实复现跨屏」的证据。 |
| `tests/test_multi_screen_interaction.py` | +416/−0（新） | 17 条回归（假双屏驱动真实钳制/边界代码）。 |
| `tests/test_tray_icon_ready.py` | +198/−0（新） | 7 条回归（托盘占位/升级/已就绪/无信号兜底 + 根因留证）。 |
| `tests/test_menu_layout.py` | +41/−1 | 默认菜单「桌宠控制」期望 12→11 项；新增「旧布局残留被清掉」「注册表不再登记该 id」两条。 |
| `tests/test_architecture.py` | +9/−1 | `WINDOW_PY_LINE_BUDGET` 按文件约定校准到实测 4671（带日期注释，说明 +23 全是贴着交互/物理入口与时序的接线）。 |
| `README.md` | +9/−2 | 鼠标穿透关闭入口文案改为「托盘菜单（或桌宠设置 → 互动）」；拖动一行标注多屏可跨屏；补本批「发布后补丁」变更记录。 |
| `docs/INDEX.md` | +3/−2 | 登记本报告，并把 `WINDOW_PY_SPLIT_GUIDE` / `CONTEXT-MENU-RESEARCH-AND-REFACTOR` 两行的「何时必读」链到它（新文档入场规则第 2 条）。 |

**未改动（刻意）**：`pet/physics.py`（重力/反弹系数/子步长/静止判定一字未动）、`pet/edge_probe.py`、`pet/settings_interaction.py` 与 `settings_pet_controls.py`（设置页穿透开关）、`pet/config.py` 的 `mouse_through` 默认值/白名单、`pet/app.py` 托盘菜单里的穿透项与 `sync_tray_checks`、`shared.py` 的「鼠标穿透时仍允许主动识屏」（`allow_when_mouse_through`，是另一条设置）。

---

## 三、实现要点

### 3.1 一次交互 = 一个多屏活动区域快照

```
拖拽起点 / _enter_physics_mode('drag'|'throw')  →  desktop_area()  ← 一次交互只枚举一次显示器
        ↓ 存到 host._interaction_area
物理 tick（120/165Hz）：move_window_towards() / throw_bounds() 只 getattr 读快照
        ↓
_stop_physics() / 普通拖拽松手  →  self._interaction_area = None  → 回到本屏语义
```

`band_bounds(area, body)` 是防「错位拼接空洞」的关键：与身体框 **x 带**相交的屏决定上下边界，与 **y 带**相交的屏决定左右边界。例：主屏 `1536×816`（可用）+ 副屏 `1536,0,1920×1040`——

- 宠物在主屏上（身体 x 带只压主屏）→ 活动带 = `(0,0) 3456×816`：**左右可以跨到副屏，上下仍守主屏底边**（不会沉到主屏下方那片没有显示器的区域）；
- 宠物整体落在副屏上 → 活动带 = 副屏可用区，可用满 1040 高；
- 拼接整齐的同尺寸双屏 → 收窄结果恒等于包围矩形，行为就是「整个桌面」。

**不变量**：多屏快照生效时，任意落点后身体框都至少与某块屏的可用区相交（`test_body_always_tiles_a_screen_across_requests` 用 42 个落点扫描断言）——宠物不会被丢进看不见的空洞。

### 3.2 托盘图标：占位先顶住，首帧到了换头像

`_build_tray`：`QIcon(win.icon_pixmap())` 为空 → `_tray_placeholder_icon()`（同一套矢量图标语言）→ 建托盘 → 订阅 `frame_ready` 换角色帧。首帧已就绪（如热切换角色后立刻重建托盘）时不连信号；替身窗口没有该信号时 `getattr` 兜底。`frame_ready` 只在 `_rebuild_frame` 首次成功产出可显示帧时发一次（`_frame_ready_emitted` 标志），GUI 线程同步 emit，无竞态、无定时器。

### 3.3 菜单入口收敛

modern 模板 + legacy 树 + 注册表三处同步删除，`add_mouse_through` 随之成死代码一并删除；用户旧布局的残留节点在 `Config` 载入路径剔除。设置页与托盘菜单原样保留（`tests/test_pet_interaction_locks.py` 的托盘同步用例、`tests/test_menu_layout.py` 的设置页归属用例都保持绿，正好证明两者没被误伤）。

---

## 四、性能分析

**环境**：Windows 10（本机），Python 3.11.1，PySide6（Qt 6.x），`QT_QPA_PLATFORM=offscreen`，单核基准；样本量见各条。测法是 `timeit` 直接调生产函数，宿主用 `scripts/probe_multi_screen_area.py` 的 `_Host`（与真实 `PetWindow` 同一条代码路径）。

| 路径 | 实测 | 说明 |
|---|---|---|
| `desktop_area()`（2 屏） | **3.1 µs/次** | **一次交互只调一次**（拖拽起点 / 进入物理模式），不在 tick 里 |
| `desktop_area()`（4 屏） | **5.6 µs/次** | 同上 |
| `move_window_towards()` 单屏/无快照 | **4.69 µs/次** | 与改造前同一分支（多快照判断只多一个 `getattr`） |
| `move_window_towards()` 有快照 | **9.17 µs/次** | 多出的是 `band_bounds` |
| `band_bounds()` | **4.13 µs/次** | 纯 QRect 比较，无系统调用 |
| `throw_bounds()` 无快照 | **2.60 µs/次** | 单屏路径与改造前一致 |
| `throw_bounds()` 有快照 | **7.39 µs/次** | 同上多出 `band_bounds` |
| `_tray_placeholder_icon()` | **174 µs/次** | 只在「建托盘时首帧未就绪」发生一次（启动 / 切角色） |

**逐条回答**：

1. **稳态开销**：**单屏用户为零**——`desktop_area()` 返回 `None`，`move_window_towards`/`throw_bounds` 走原分支（4.69 / 2.60 µs 的实测值就是改造后的单屏值）；托盘侧稳态零新增（信号一次/窗口生命周期，无定时器）。
2. **新增路径成本与触发频率**：多屏用户**只在拖拽/抛掷期间**每 tick 多 ~4.5 µs（`band_bounds`）。按 165Hz 折算：拖拽 +0.74 ms/s、抛掷（`throw_bounds` + `move_window_towards`）+1.5 ms/s ≈ **单核 0.07%~0.15%**，且只在用户正拖着/正飞着时发生；作为对照，同一时间窗里 `_rebuild_frame` 单帧就是 1~2.4 ms（本仓库既有实测）。`desktop_area()`（3.1~5.6 µs）一次交互一次，即使按最高频的「每秒一次拖拽」也只有 5.6 µs/s。
3. **有无新的系统调用 / 网络 / 磁盘 / 线程**：**都没有**。`QGuiApplication.screens()`/`availableGeometry()` 是进程内 Qt 缓存属性；托盘侧只多一个 Qt 信号连接；`_tray_placeholder_icon()` 是一次纯 CPU 绘制（QPainter，174 µs，无文件读取——刻意不依赖任何素材文件）。
4. **内存有无增长**：**无新增缓存/常驻结构**。快照是一个 `DesktopArea`（1 个 QRect + 2~4 个 QRect 的 tuple），挂在 `host._interaction_area` 上，交互结束即置 `None` 释放；托盘侧多一个 bool + 一个连接；探针/测试文件不参与运行期。

---

## 五、实机运行记录

### 5.1 本机屏幕现状（决定了能自动验证到什么程度）

```
E:\AI\DSH\dsh-pet-indesktop> python scripts/probe_multi_screen_area.py
[screens] 识别到 1 块屏
  - \\.\DISPLAY1: geometry=(0, 0) 1536x864 available=(0, 0) 1536x816 dpr=1.25
[pointer] 光标所在屏: \\.\DISPLAY1 光标位置=1214,628
[primary] 主屏: \\.\DISPLAY1: geometry=(0, 0) 1536x864 available=(0, 0) 1536x816 dpr=1.25
[area] desktop_area() = None
       原因：有效屏不足 2 块，或某块屏几何为空/异常。
       → 拖拽/抛掷按单屏语义走（本屏 availableGeometry），
         跨屏拖拽/抛掷不适用；本机无法复现 #186。
```

**这就是「为什么跨屏不能在本机自动验证」的排查证据**：开发机只有 `DISPLAY1`（且副屏现在不接），Windows 上也没有可用的「假多屏」QPA 后端。因此：

- 多屏逻辑的自动验证全部由 `tests/test_multi_screen_interaction.py` 用**假双屏驱动真实钳制/边界代码**完成（§六）；
- 真实双屏的最终验收由用户在双屏机器上跑上面这条命令 + 源码版/新产物实测（探针已经能做到「往每块屏拖的结果」自检）。

### 5.2 同一探针跑假双屏（证明代码路径真的通）

```
[area] desktop_area().bounds = (0, 0) 3456x1040
  screen[0] available=(0, 0) 1536x816
  screen[1] available=(1536, 0) 1920x1040
[body] 身体框（窗口局部）= (153, 65) 156x194
[drag→FAKE-1] 目标中心=(537,267)  无快照=(537,267)  有快照=(537,267)  身体框=(690, 332) 落在目标屏=True 当时的活动带=(0, 0) 3456x816
[drag→FAKE-2] 目标中心=(2265,379) 无快照=(1075,379) 有快照=(2265,379) 身体框=(2418, 444) 落在目标屏=True 当时的活动带=(0, 0) 3456x1040
[branch] 抛掷边界（有快照 / 无快照）:
  有快照: (-153.0, -65.0, 3147.0, 781.0)
  无快照: (-153.0, -65.0, 1227.0, 557.0)
```

两处对照即为 #186 的「修前 vs 修后」：往副屏拖，修前被钉在主屏右缘（x=1075），修后落到副屏（x=2265）；抛掷右界从本屏的 1227 扩到并集的 3147。

### 5.3 真实平台（Windows，非 offscreen、非 mock）托盘图标验证

```
> python -            # 真 Windows QPA：真 PetWindow + 真 MovieLibrary + 真 QSystemTrayIcon
[env] platform = windows | PySide6 Qt 3.11.1150.1013
[tray] isSystemTrayAvailable = True
[T0] icon_pixmap().isNull() = True                     ← 根因现场：show() 之后立刻取图是空图
[T0] placeholder icon isNull = False | 尺寸 = QSize(80, 80)
[T1] frame_ready 触发次数 = 1
[T1] icon_pixmap().isNull() = False | 尺寸 = 50 x 64    ← 首帧到了，角色帧可用
[T1] 最终托盘图标 isNull = False | 与占位图不同 = True   ← 托盘图标已从占位换成角色头像
```

- `[T0]` 一行的 `True` 就是用户看到的现象（空图标进托盘）；
- `[T1]` 三行证明修复后：托盘先有可见的占位图标 → 首帧就绪 → 换成角色头像，**全程无空图标**。

### 5.4 用户可见行为确认

- 托盘图标：本机真 Windows 平台已确认（§5.3），用户可在新产物上复查「托盘里有鱼头像」。
- 右键菜单：默认 modern 菜单「桌宠控制」里不再有「鼠标穿透」（`tests/test_menu_layout.py` 断言 11 项）；设置页「互动」域开关与托盘菜单项仍在。
- 跨屏拖拽/抛掷：**待用户在双屏机器上验收**（本机只有一块屏）。验收口径：把桌宠从主屏拖到副屏再丢回去；顺带跑 `probe_multi_screen_area.py`，把输出贴回 issue #186。

---

## 六、测试与验证

### 6.1 先红后绿（每项都对得上根因）

| 项 | 修前 | 修后 |
|---|---|---|
| `tests/test_multi_screen_interaction.py`（`git stash push pet/window.py pet/window_placement.py` 后跑） | **15 failed, 2 passed** | **17 passed** |
| `tests/test_tray_icon_ready.py`（`git stash push pet/app.py pet/window.py pet/window_placement.py` 后跑） | **5 failed, 2 passed** | **7 passed** |

两条「修前也通过」的用例是刻意留的**对照/留证**：`test_without_snapshot_drag_stays_on_current_screen`（把 4.2.1 的钉边行为钉死）、`test_first_frame_pixmap_is_null_before_any_frame`（把「首帧未就绪时 icon_pixmap 为空」钉死）。

### 6.2 聚焦批次（全部绿）

- `tests/test_multi_screen_interaction.py tests/test_edge_reachability.py tests/test_window_position.py tests/test_collision_window.py tests/test_throw_egg.py tests/test_physics.py tests/test_throw_flight_anim.py tests/test_movement.py tests/test_recovery_hints.py tests/test_pet_interaction_locks.py tests/test_single_process_shared.py tests/test_single_process_spawn.py tests/test_tray_icon_ready.py tests/test_architecture.py` → **247 passed**
- `tests/test_menu_layout.py tests/test_config_schema.py tests/test_pet_interaction_locks.py` → **107 passed**
- 菜单批（含 `test_settings_interaction_tabs.py`、`test_desktop_pet_features.py`）→ **211 passed**
- `python -m ruff check pet/ tests/ scripts/` → **All checks passed**

### 6.3 时序族高负载复跑

`desktop_area()` 只在交互开始时调用一次，本批也**不新引入线程/定时器**，按仓库纪律仍对受影响族做 CPU 打满 3 轮复跑（`test_drag_move_coalescing` / `test_multi_screen_interaction` / `test_collision_window` / `test_physics` / `test_throw_flight_anim` / `test_throw_egg` / `test_window_position` / `test_pet_interaction_locks` / `test_tray_icon_ready`）：

```
> for i in 1 2 3; do                       # 每轮先起 12 个纯 CPU 燃烧进程（本机 20 核）
>   12 × python -c "while time.time()-t < 110: pass" &
>   QT_QPA_PLATFORM=offscreen python -m pytest -q <上面 9 个族> --basetemp=C:/pt-load$i
>   wait
> done
===== ROUND 1 (12 burners on 20 cores) =====
158 passed in 4.96s
===== ROUND 2 (12 burners on 20 cores) =====
158 passed in 2.98s
===== ROUND 3 (12 burners on 20 cores) =====
158 passed in 3.11s
```

三轮全绿；本批没有新增线程/定时器/阻塞等待，时序面唯一变化是拖拽/抛掷期的 `band_bounds`（纯 CPU，~4µs）。

### 6.4 全量

```
> set QT_QPA_PLATFORM=offscreen
> python -m pytest -q --basetemp=C:/pt-full186c
2944 passed, 11 skipped, 13 warnings in 311.50s (0:05:11)
```

（本批次前三轮全量的记录：第一轮 `16 failed, 2926 passed, 11 skipped`（§6.5 的桩兼容问题）→ 第二轮 `1 failed, 2943 passed, 11 skipped`（新报告里的分支名撞上 `test_product_copy_has_no_external_brand_reference` 的品牌词黑名单，已把分支更名为 `fix/186-…` 并改写该行）→ 第三轮 **全绿**。）

### 6.5 全量套件抓出来的两条问题（诚实记录）

1. **第一次全量红了 16 条**：`tests/test_window_rendering.py` / `test_perfstats.py` / `test_window_dpr_signals.py` 里有只挂载 `_rebuild_frame` 的假窗口（`_RebuildPet` 等，不是 `PetWindow` 子类），我的首帧标志直接属性访问导致 `AttributeError`。这正是「聚焦测试绿 ≠ 可合并」的典型形态：**三处桩都不在我改的模块的测试文件里**。修法：发射点改成 `getattr(self, '_frame_ready_emitted', False)` + `getattr(self, 'frame_ready', None)` 两层兜底（`getattr` 兜底是本仓既有惯例），并把这条写进 `pet/window.py` 的注释与本报告。修后这 3 个文件 **83 passed**。
2. 因此 `window.py` 的行预算实测值从 4667 变成 **4671**（+23/−0），预算注释与数值同步校准。

### 6.6 合并后主分支 CI 的 macOS 假红（已定位并修掉，非本批引入）

- **现象**：合并后的 main push run（`35876529852`）macOS job 红，失败用例
  `tests/test_voice_chime_service.py::test_service_module_top_level_does_not_import_edge_tts`；
  同一次运行 Windows / Ubuntu 绿，**同一棵树的 PR run 的 macOS job 也是绿的**；重跑失败 job 仍红（第二次）。
- **定位（三条证据）**：① 该断言的判据是**进程级 `sys.modules`**（"模块顶层没 import edge_tts"），
  而全量套件是单进程跑的，前面用例的后台合成/台词预缓存线程会在任意时刻懒加载 `edge_tts`；
  ② 本批 diff 与 TTS 零关联（`git diff 7d622db..HEAD -U0 | grep -i "edge_tts\|voice_chime"` 为空）；
  ③ 把本批新增的两个测试文件放在该文件之前**本地复跑** → `68 passed`（没有污染）；
  本机连跑三轮全量也全绿。→ 判定为**竞态假红**，不是本批回归。
- **处置**：把该文件的两条 import 断言改成**子进程**写法（仓库既有先例：`tests/test_winmm_sound.py`
  的 QtMultimedia 断言本来就是子进程），并注入 `import edge_tts` 验证红：**2 failed**（修前同款红绿证据见 §6.1 口径）。
  竞态模式与判据已记入 [`BUILD-CI-FAILURE-NOTES-2026-08.md`](BUILD-CI-FAILURE-NOTES-2026-08.md) §5.4。
  这条修复是**测试专属**（零生产行为变更），直接推 main 以尽快恢复主分支绿。
- **处置的处置（`adc70e4` → `aa9fbda`，诚实记录）**：第一版子进程写法让**同一棵树 push run 的
  Ubuntu 主套件卡在 `in_progress` 20 分钟以上**（Windows 5m45s / macOS 5m0s 同期成功，上一棵树
  `b3d3db1` 的 Ubuntu 是 4m18s 通过），只能取消该 run。两个可疑点一并堵掉：① 裸 `python -c` 里
  没有 `tests/conftest.py` 的弹窗桩，子进程不再构造 `AppShell`（只做 import 级断言，"服务没被
  启动"那一半留在进程内判）；② `capture_output=True` 改为输出重定向到临时文件——超时杀进程后
  读管道等 EOF 会被持有管道的孙进程永久挂住，改文件后超时即杀即返回（`TimeoutExpired` 转
  `returncode=124`）。加固后三平台绿：**Ubuntu 4m15s / macOS 5m0s / Windows 4m59s**
  （run `35881734360`）。两条硬约束已写进 `_run_python` 的 docstring 与 CI 踩坑记录 §5.4。

---

## 七、已知限制与后续

1. **错位拼接的贴缝处**：身体框跨越两块屏时，落在较矮那块屏外侧的那一小条会被屏边裁掉（和 4.2.0 裸 `move` 时一样）。活动带收窄保证了「不会整只落进空洞」，但不承诺「贴缝处像素级完美」。
2. **真实双屏未在本机复现**（§5.1）：逻辑已由假双屏单测覆盖，最终观感必须由用户在双屏机器上验收。
3. **托盘占位图标**在「素材损坏/ffmpeg 缺失导致首帧永远不来」时会一直留着——比「没有图标」好，但真要精修可以在素材探测失败时给个提示（未做）。
4. **`context_menu_layout` 的清理是加载期一次性剔除**：若将来重新引入同名 action id，需要删掉 `pet/config.py` 里 `_MENU_LAYOUT_DROPPED_ACTIONS` 那一处（代码注释里已写明）。
5. **`WINDOW_PY_LINE_BUDGET` 上调到 4671**：按该文件既有约定「预算只随实测校准」，未为达标压行；后续给 `window.py` 加功能时仍应先按 `WINDOW_PY_SPLIT_GUIDE.md` 拆控制器。
6. **发布侧**：v4.2.1 已发布，本批修复要进产物需要重新构建并覆盖 Release 附件（沿用 `Republish Release Assets` workflow 或重建后 `--clobber`），**本 PR 不含产物**。

---

## 八、风险与回滚

| 风险 | 处置 |
|---|---|
| 快照释放点漏了 → 拖拽后漫游/落位误用并集区域 | `_stop_physics` + 普通拖拽松手 + 锁定位置打断三条路径都清；`test_snapshot_lifecycle_across_physics_modes` + `test_default_corner_and_walk_keep_current_screen_after_snapshot_cleared` 钉死 |
| 单屏用户行为漂移 | `desktop_area()` 返回 `None` ⇒ 原分支；`test_single_screen_has_no_desktop_area` / `test_throw_bounds_without_snapshot_use_current_screen` 逐位对照 |
| 边缘探头行为漂移 | 显式 `body_bounds` 优先级最高、恒本屏；`test_explicit_body_bounds_ignores_snapshot` |
| 错位布局空洞 | `band_bounds` 收窄 + 42 落点不变量用例 |
| 托盘占位长期停留 | 有界：只在首帧未就绪时出现；首帧到了必然替换（信号在 `_frame_pixmap` 赋值后同步发） |
| 用户旧布局里的幽灵菜单项 | 载入期剔除 + 测试 |
| 回滚 | 三个提交相互独立：`git revert b37a44c`（托盘）/ `git revert cd7821b`（#186）/ `git revert ea17bfa`（菜单），任一可单独回滚，互不牵连；其后一个 `docs` 提交只动文档，回滚与否都不影响运行行为 |
