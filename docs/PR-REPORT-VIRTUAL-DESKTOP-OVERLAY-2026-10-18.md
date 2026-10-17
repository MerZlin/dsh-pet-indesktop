# 单合成窗覆盖整个虚拟桌面（跨屏不再依赖宿主移交）

> 分支 `feat/virtual-desktop-overlay`，基线 `a19157f`（v4.3.2 之后）。关掉
> #303 / #283 的「拖不过去」，并把 #302 的「第三方软件把桌宠当全屏应用」
> 一起收口。

## 〇、为什么换架构

现状是 **每屏一张合成窗**：每块有宠物的屏幕各有一个 `OverlayWindow`，由
`pet/screen_host.py` 的 `ScreenHost` 管理，窗口几何取 `screen.geometry()`，
世界坐标是**屏内局部坐标**；跨屏靠「缝上通道」把精灵从一个宿主搬到另一个
（`_open_seam_domain` → `route_seam_after_advance` → `_transfer_sprite` →
`_sync_seam_projections`）。

两条用户实测反馈都出在这个模型上：

- **#303 / #283**：开启「允许桌宠跨屏」并重启后，往左侧副屏拖时身体被钳在
  `x=0`、松手弹回主屏，副屏宿主窗**自始至终没有创建**。报告者 2604 个采样点
  显示：光标最左到逻辑 `x=-928`（已在副屏内约 900 像素），而身体 x 常态恒为 0。
- **#302**：窗口铺满 `screen.geometry()`（**含任务栏**），Wallpaper Engine
  （`playbackfullscreen=pause`）与 TrafficMonitor（`hide_main_wnd_when_fullscreen`）
  按窗口矩形自己判定全屏，于是点「抽签 / 天气」时 WE 动态壁纸停住、TM 悬浮窗
  消失。报告者的对照实验很干净：同一进程、同一只宠、同一操作，用
  `SetWindowPos` 把窗口高度从 1440 改成 1392（可用区）后，WE 的 GPU 占用从
  「25.5% → 0.0%」变回「24.3 → 25.0% 不动」。

两个现象共同说明：**「一张窗 = 一块屏」这个前提本身是负担**。一张窗覆盖整个
虚拟桌面后，跨屏不再是「窗口之间搬东西」，而是同一张窗内自由移动——#303 那
一整层机制随之消失；窗口矩形改取各屏**可用区**的并集，单屏下就等于任务栏之外
的区域，#302 的单屏场景同时收口。

## 一、核心特性

1. **一张合成窗**：`_hosts` 长度恒为 1，永不创建第二个屏的窗口。
2. **窗口矩形 = 各屏 `availableGeometry()` 的并集包围矩形**（不是
   `virtualGeometry()`——后者含每屏任务栏，等于主动把自己做成「全屏矩形」）。
3. **活动区是矩形集合，不是单矩形**：各屏可用区平移到窗口局部坐标后是一**组**
   矩形。错位拼接的屏之间存在没有显示器的空洞（本机副屏 1080 高、主屏 1440
   高，`x∈[-1920,0], y∈[1080,1392]` 就是空洞），用单矩形会让宠走进看不见的地方。
   复用 `pet/pet_sprite.py` 已有的 `set_clamp_rects` 机制，把「跨缝临时域」升级
   为「常态域」。
4. **`allow_cross_screen` 语义收紧但保留**：窗永远铺满全桌面；开关**关**时活动区
   只留家那一块屏（宠出不去），**开**时是全部屏的矩形集合。设置行仍有产品意义，
   进程快照语义（`overlay_shell.py` 建壳期一次）不变。
5. **刷新率取全屏最大**：单宿主后成员屏只剩一块，按它取数会让 60Hz 副屏把整组
   tick 拖到低档（本机主屏 170Hz）。
6. **屏归属与比例**：`sprite.home_screen` 每拍按身体中心刷新；落盘 `rx/ry` 的分母
   是「该宠身体中心所在屏的可用区」（保持每屏语义），恢复时用 profile 的
   `screen_name` 找回那块屏，找不到才退化为整桌面比例。

## 二、修改文件说明

`git diff --numstat`（产品 4 个文件 **净 −660 行**）：

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `pet/overlay_shell.py` | +460 / −1102 | 见下 |
| `pet/overlay_window.py` | +64 / −143 | `__init__` 窗口矩形改由注入的桌面矩形取；`_on_screen_geometry_changed` 不再把窗拉回单屏几何（**两处几何写入点必须同改**，否则另一个人每收到一次屏信号就把窗切回去）；逐只 DPR；删除跨屏投影（`_projections` / `set_projection` / `projection_rect` / paint 里的投影段）、`detach_sprite_for_transfer`、`_follow_overlay` / `_sprite_event_pos` 两个单窗下恒等的钩子 |
| `pet/screen_host.py` | +45 / −21 | 新增 `activity` 矩形集合与 `set_activity` / `apply_activity`；`set_bounds` 换域后**必须重挂集合**（`_effective_clamp` 优先用 rects，不重挂的话第一次几何变化就把多矩形语义吃掉） |
| `pet/tick_driver.py` | +14 / −16 | `_refresh_rate` 改为枚举 `QGuiApplication.screens()` 取最高 |

`overlay_shell.py` 的实质改动：

- `_create_host`：唯一宿主 + 并集窗口 + 活动区。
- 新增屏/几何段：`_seed_screens` / `_screens` / `_desktop_bounds` / `_screen_rect` /
  `_activity_rects` / `_screen_at_global` / `_screen_of_sprite` / `_advance_screen_sync` /
  `_sync_sprite_screens`。
- `_restore_screen`（原 `_host_for_restore`）：只决定复活落哪块屏。
- `move_sprite_to_screen`：单窗下的唯一跨屏 seam——`_place_sprite_at` /
  `_move_sprite_onto_screen` / `_remap_sprite_ratio` / `_body_ratio` / `_place_body_ratio`。
- 屏事件 `handle_screen_added` / `handle_screen_removed`：全部屏的信号接线、拔屏换家。
- `_sync_host_geometry` + `_geometry_plan`：**先取比例 → 换域 → 再落点**（顺序见下）。
- 删除的整块：`_screen_touch_group`、`_seam_domain_rects`、`_open_seam_domain`、
  `_close_seam_domain`、`route_seam_after_advance`、`_sync_seam_projections`、
  `_route_one_seam`、`_keep_seam_domain`、`_transfer_sprite`、`_place_sprite_in_host`、
  `_reap_empty_hosts`、`_release_host`、`_surviving_host`、`_absorb_host_sprites`、
  `_resite_main_host`、`route_drag_to_screen`、`_desired_global_pos`、
  `_sprite_body_center_global`、`_sync_island_host`、`_rebuild_all_bubbles`、
  `_snapshot_transient_bubble`、`_snapshot_sprite_bubble`、`_rebind_sprite_bubble`、
  `_bubble_copy`、`_built`、`_local_bounds`、`_apply_bounds`、`_migrate_sprite_position`、
  `_sync_geometry`。删后全仓 grep 这些名字 **0 处残留引用**（`_local_bounds` 仅
  剩 `pet/window.py`、`pet/window_placement.py` 的 legacy 同名函数与相关测试）。

**`_sync_host_geometry` 的四步顺序，每一步都对应一个已修过的实机缺陷**（注释已写进
代码）：① 先按旧活动区取每只的比例（换域会就地钳一次，旧比例当场消失）；
② 再换窗口矩形与活动区；③ 最后按新矩形写落点（`set_pos` 受当前域钳制，先写会被
旧域截断——实测宽 1000→1600 时比例落点 x≈1230 被旧边界钳成 744）；④ 原点真变时
同步气泡跟随器与岛墙原点（重建跟随器会丢正在显示的气泡）。

**测试**：新增 `tests/test_overlay_virtual_desktop.py`（7 条）；按新语义修正 17 个
既有文件。**「窗数」类断言按设计反转**（`len(hosts())==2` → 单窗 + 活动区矩形数），
**行为类断言一条没放宽**：几何迁移、气泡原点、`rx/ry` 不被旧边界截断、岛墙原点、
逐只 DPR、grab 协议收尾全部继续成立。

## 三、性能分析

实测本机（Win11 / Py3.13.7 / PySide6 6.11.2 / 主屏 2560×1440@170Hz + 左侧副屏
1920×1080@60Hz，DPR 均 1.0），110Hz tick 下的稳态开销：

| 路径 | p50 | 说明 |
|---|---|---|
| `_sync_sprite_screens`（每拍刷新屏归属） | 0.024 ms（1 只）/ 0.071 ms（3 只） | **单屏直接跳过** |
| `_refresh_rate` | 0.004 ms | 只列举屏幕 |
| `_sync_host_geometry` | 0.12–0.26 ms | 只在屏事件触发（插拔/改分辨率/改任务栏），不是每拍 |

- **无新增线程、进程、网络请求、磁盘写入**。
- **内存无增长路径**：删掉的是整层投影缓存与宿主表，新增只有每屏一个 `QRect`。
- **净删 660 行产品代码**，每拍的宿主循环从 N 元素退化到 1 元素。
- 刷新率仍取 170Hz（`tick` 间隔 6ms 不变）——若按成员屏取，副屏 60Hz 会把整组
  拖到 16ms，这是本次明确避免的回归。

## 四、实机运行记录

真机探针（`.scratch/probe_virtual_desktop_overlay.py`，非 mock，读真实 QScreen）：

```
name='H27T22S'            geometry=(0,0,2560,1440)     available=(0,0,2560,1392)     dpr=1.0 refresh=170.0
name='\\\\.\\DISPLAY2'    geometry=(-1920,0,1920,1080)  available=(-1920,0,1920,1032)  dpr=1.0 refresh=60.0
各屏可用区并集: (-1920,0,4480,1392)
全屏最大刷新率: 170.0 → 6ms
allow_cross_screen=False → hosts 1, 窗口==并集 True, 活动区 1 块, tick 6ms
allow_cross_screen=True  → hosts 1, 窗口==并集 True, 活动区 2 块, tick 6ms
拖到左侧副屏后身体中心（全局）: (-1689.5, 729.5)   落在副屏可用区内: True   未被钳在 x=0: True
```

注意并集高度是 **1392**（不是 1440）：这正是 #302 的修法——窗口不再覆盖主屏任务栏。

新用例 **先红后绿**（`git stash` 掉 4 个产品文件后跑 `tests/test_overlay_virtual_desktop.py`）：

```
6 failed, 1 passed
E  assert QRect(0,0,1920,1080) == QRect(0,0,1920,1040)      # 窗口含任务栏（#302）
E  assert QRect(0,0,1920,1080) == QRect(0,0,3840,1040)      # 不是并集 / 仍是多窗
E  AttributeError: 'OverlayShell' object has no attribute '_screen_of_sprite'   # #303
```

`git stash pop` 后：`7 passed in 1.88s`。

门禁：

- 聚焦 12 个多屏族 + 新文件：**183 passed**（`QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:randomly ...`）。
- `ruff check pet/ tests/` → `All checks passed!`
- 整仓全量：`8160 passed, 15 skipped, 1 failed`，唯一红是
  `tests/test_proactive.py::TestVisionAndWatcherPhase2::test_capture_window_rect_coordinates_and_clamping`。
  **该失败与本改动无关**：把 `pet/` 全部 stash 回基线后在纯净 `a19157f` 上单跑同样红
  （`assert img is not None`，Pillow 抓屏路径），根因是本机虚拟屏原点为负
  （`GetSystemMetrics(76) = -1920`），用例写死了 `(100,100)` 的正坐标假设。

## 五、已知限制与风险（如实）

1. **混合缩放**：一张窗只有一个 DPR（Qt 物理限制）。逐只 `set_dpr` 只保证
   pixmap/命中图按各自屏重建，整窗合成仍按一个 DPR 绘制——**混合 DPI 下必有一块
   屏的角色略糊或偏大**。没有伪造，也没有声称已解决。本机两屏同为 100%，未实测。
2. **物理/行为/边缘探头吃并集矩形**：形状错位的屏上，宠被"看不见的墙"挡住时，
   位置钳制是对的（多矩形），但力/反弹/探头边界用的是并集矩形的四边，观感上像
   撞到洞边。逐只 bounds 化属后续可做项，本次故意不做。
3. **屏集合缓存**：建壳时取 Qt 枚举、之后由 `screenAdded` / `screenRemoved` 维护；
   绕过 Qt 信号的外部改动不会被感知（生产路径不存在，测试里用 handler 模拟）。
4. **`_migrate_to_screen` 语义收窄**：只换"家"并同步几何，不再重建窗口/气泡/重新
   注册锁屏通知（HWND 不变）；原先依附在该重建上的用例已改成"不变量仍在"的护栏。
5. **老档案位置漂移**：`rx/ry` 分母语义保持不变（仍是每屏可用区），但**副屏被拔掉
   后恢复**的那一档会退化为整桌面比例，位置与拔屏前不同。这是有意的取舍——不做
   多版本迁移。
6. **#302 只覆盖单屏场景**：多屏下窗口矩形是并集包围矩形，必然包含某块屏的任务栏
   那一行，WE / TM 这类按矩形自判全屏的软件在多屏下仍可能误判。报告里给的用户级
   绕法（给 `dsh-pet-standalone-webm-chat.exe` 加「全屏时保持运行」规则 /
   取消「全屏时隐藏主窗口」）依然适用。
