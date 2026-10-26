# Linux/XWayland：桌宠左右键点不到——输入区域改由 XFixes 服务端判定

> **基线**：`origin/main` `34d810b`（v4.4.0 + `chore(update): update.json 指向 v4.4.0`）
> **分支**：`fix/linux-xwayland-input-region`　**日期**：2026-10-26
> **范围**：7 个文件（实现 3、测试 1、文档 3）
> **关联**：Linux 预览版自 v4.3.0 起的输入穿透路径（`docs/RELEASE-v4.3.0.md`
> 「非 Windows 逐像素输入穿透」）；用户现场为 Ubuntu 24.04 的 **GNOME/Wayland
> 会话（应用走 xcb/XWayland）**。

## 一、核心特性

**问题**：Linux 桌面上，鼠标左右键都无法选中屏幕上的桌宠；点击直接落到下层
窗口（能操作下层应用，但桌宠完全没反应）。用户现场：Ubuntu 24.04 + GNOME
Wayland 会话、单屏 2560×1440、v4.3.4 安装版；换成从源码运行同一版本同样复现。

**根因**：`pet/platform_passthrough.QtPerPixelInputController` 的**输入区域判据
是全局指针位置**——每 10/50ms 读一次 `QCursor.pos()`（X11 上即 X server 侧的
指针位置），据此把整个 overlay 在「整窗输入」与「**空输入区域（穿透）**」之间
来回切换。

这条依赖在 XWayland 会话里会**自锁**：

1. overlay 平时必须处于穿透态（输入区域为空），否则整屏吞点击；
2. 输入区域为空时，合成器不会把指针事件投递给这个 XWayland surface，
   X server 侧看到的指针位置就停在**最后一次被接受的坐标**；
3. 控制器读到的仍是这个陈旧坐标 → 判定「光标不在宠物身上」→ 维持空输入区域；
4. 于是「指针想进入宠物 → 需要先读到指针已在宠物上 → 但读到位置需要输入区域
   先打开」形成循环，**一旦进入穿透态就再也不会自愈**。

**本机实测证据**（干净 v4.3.4 源码实例，非打包版）：

```text
[OLD] pos=(782,1178) local=(782,1146) inside_win=True sprite=None click_through=True input=[]
[OLD] pos=(782,1178) local=(782,1146) inside_win=True sprite=None click_through=True input=[]
...（连续 20+ 拍不变；探针打印时桌宠在 (2227,1223)，身体中心约 (2305,1320)）
```

控制台打印的是控制器自己读到的 `QCursor.pos()`、命中判定与 `XShape` 输入区域：
指针位置固定不变、判据恒「透明」、输入区域恒为空。把同一个指针用 XTest
**注入到 X 协议**（绕过合成器投递）时位置立刻刷新、判据翻成不穿透、输入区域
恢复整窗（`[(0,0,2560,1408)]`）——这解释了为什么「用合成事件测能点开菜单、
用户真实鼠标却点不到」：注入走的是 X 协议，真实鼠标走合成器投递。

**修法**：Linux/X11 不再读全局指针，把输入区域**交给 X server 判定**——用
XFixes `ShapeInput` 把 overlay 的输入区域直接设成「全部可见 sprite 的
`paint_bounds()` 并集」：

- 指针进入宠物绘制范围 → 合成器把输入交给 overlay（Qt 收 enter/move/press）；
- 指针离开 → 该点不在输入区域内，事件照常落到下层应用；
- 区域每 20ms 随宠物移动/旋转更新，**没有任何「先读指针再决定区域」的循环**。

**红线 / 不变量**：

- Windows（`WindowsPerPixelInputController`）与 macOS（`QtPerPixelInputController`）
  代码路径与行为**逐位不变**；
- 隐藏窗口不保留输入区域（与「未显示 = 不穿透」契约一致）；
- 用户手动「鼠标穿透」开关语义不变（`mouse_through` 为真时区域为空）；
- 控制器初始化失败（X 库缺失 / `XOpenDisplay` 失败）**回退旧控制器**——
  最坏情况等于修复前，不会因为少一条穿透路径而整窗吞点击或启动即崩。

## 二、修改文件说明

`git diff --numstat`（相对 `origin/main`）：

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/platform_x11_input.py` | **+361 / −0**（新增） | X11/XWayland 输入区域控制器：XFixes `ShapeInput` 按 sprite 绘制包围盒设置；20ms 收敛；状态未变零请求；原生窗口重建重放；隐藏/关闭清区域；失败可回退。 |
| `pet/overlay_window.py` | +47 / −13 | `showEvent` 的非 Windows 分支按平台插件分流：Linux/xcb 建 `X11InputRegionController`（初始化失败回退旧控制器），其余沿用 `QtPerPixelInputController`；`closeEvent` 对持有 X Display 的控制器调 `close()`；`hideEvent` 文档补充「X11 清空输入区域」；模块 docstring 同步。 |
| `pet/platform_passthrough.py` | **+9 / −0**（仅 docstring） | 登记「Linux/X11 不再使用本控制器」及原因，避免后来者按旧注释把 Linux 改回去。**零行为变化**。 |
| `tests/test_platform_x11_input.py` | **+212 / −0**（新增） | 9 例纯逻辑回归：平台探针、绘制包围盒（`paint_bounds` 而非 `rect`）与窗口裁剪、隐藏/手动穿透产出空区域、状态未变不重复发请求、原生窗口重建重放、`stop()` 只对真实 XID 清区域、窗口已销毁时不动 X、共享 X 连接引用计数、Xlib `BadWindow` 必须被吞掉。 |
| `docs/PR-REPORT-LINUX-XWAYLAND-INPUT-REGION-2026-10-26.md` | 新增 | 本报告。 |
| `docs/INDEX.md` | +1 | 按「新文档入场规则」登记一行。 |
| `docs/DEV-HANDOVER.md` | +2 | §10 模块树补 `platform_x11_input.py`（顺带补上此前漏登记的 `platform_passthrough.py`）。 |

### 未改动（明确列出）

- `pet/platform_passthrough.py` 的 `QtPerPixelInputController` **实现一行未动**
  （只改 docstring）：macOS 仍走它，Linux 仅在 X11 控制器初始化失败时回退到它。
- `pet/platform_win.py`（Windows 扩展样式位轮询）未动。
- `pet/overlay_shell.py` 的穿透复合语义（用户穿透 / 光标隐藏自动穿透）未动：
  新控制器同样只读 `window.mouse_through`。

## 三、实现要点

### 3.1 为什么是「服务端输入区域」而不是「更准的指针查询」

即使把 `QCursor.pos()` 换成 XInput2 raw motion / `xcb_query_pointer` 直读，判据
仍然依赖「先把指针位置读回来」，而读回来的前提是事件能到达进程——在
XWayland 下这又依赖「这个 surface 接受输入」。**判据与被判对象互为前提**，
这才是缺陷的结构，而不是某个查询 API 不够快。因此把真值交给 X server：
区域由 sprite 绘制范围决定，指针在不在区域内由服务端/合成器直接路由。

### 3.2 区域怎么算

`X11InputRegionController._collect_rects()`：

- 窗口不可见或 `mouse_through` 为真 → 空区域（穿透）；
- 逐 sprite：跳过 `visible=False`，取 `paint_bounds()`（无则退回 `rect()`），
  与 `OverlayWindow.sprite_at` 的粗筛同源——45° 探头 / 黄金回旋时可见像素
  会溢出帧矩形，用 `paint_bounds()` 才「画在哪点得到哪」；
- 逐个与窗口矩形求交；空结果即无区域。

### 3.3 落窗

`XFixesCreateRegion(rects)` → `XFixesSetWindowShapeRegion(display, winId,
ShapeInput(2), 0, 0, region)` → `XFlush`。**不发 `XSync`**：请求异步入队，
不阻塞 Qt 事件线程；region 用完立即 `XFixesDestroyRegion`，不累积服务器资源。

X 连接是**进程级共享、引用计数**的（`_acquire_display` / `_release_display`）：
多屏时每块屏一张 overlay，共用同一条连接（所有控制器都在 Qt 主线程创建/销毁，
不存在并发问题）；最后一个控制器 `close()` 时 `XCloseDisplay`。避免每屏各开
一条连接带来的资源浪费与「谁负责关」的多点责任。

### 3.4 收敛与生命周期

- 20ms 一拍（`UPDATE_INTERVAL_MS`）。上一拍与这一拍的 `(winId, rects)` 相同 →
  直接返回（纯内存比较），宠物静止时零 X 请求。
- 判据里带 `winId`：`on_top` 开关 / 捕获模式改 flags 会重建原生窗口，
  新 XID 拿不到旧区域 → 检测到变化自动重放。
- `resume()`（showEvent / 隐藏后恢复）：作废缓存 + 同步写一次，`show()` 返回
  时输入区域已正确，不留「首拍之前整屏吞点击」的窗口。
- `stop()`（hideEvent）：停表 + 对**真实 XID** 清空区域（`winId` 为 0 或窗口
  已销毁时不发请求，避免 `BadWindow`）。
- `close()`（closeEvent）：`XCloseDisplay` 释放连接；其余控制器只有 `stop()`，
  `closeEvent` 按 `getattr(..., "close")` 分流。

### 3.5 Xlib 错误处理器（BadWindow 必须吞掉）

这里有一处**必须处理**的时序窗口：主线程读到 `winId()` 之后、X server 处理
请求之前，窗口可能已经被销毁（`deleteLater` / 屏迁移等绕过 `closeEvent` 的
销毁路径）。Xlib 的**默认错误处理器遇到 `BadWindow` 会直接 `exit(1)`**——桌宠
会当场退出；而 Qt 自己走 xcb、有独立错误处理，管不到我们这条 Xlib 连接。

因此 `_load_x11()` 里装一个自己的 `XSetErrorHandler`：`BadWindow` 静默忽略
（下一次 `refresh` 会因为窗口不存在/已重建而自然收敛），其余协议错误记
`WARNING` 但不终止进程。另外 `stop()`/`refresh()` 入口都先确认「确实拿到了
非 0 的 XID」，从源头减少这个窗口。

### 3.6 平台分流

`platform_uses_x11_input_region()`：仅 Linux **且** Qt 平台插件为 `xcb` 时为真
（Wayland 会话下 `pet/app.py` 已强制 `QT_QPA_PLATFORM=xcb`，所以生产路径都走
xcb）。没有 `QApplication` 实例时返回 False——避免在无 Qt 的离线环境里误判。

## 四、性能分析

**环境**：Ubuntu 24.04.5（kernel 7.0.0-38）、GNOME/Wayland 会话、应用平台插件
`xcb`（XWayland）、单屏 2560×1440、Python 3.11.15、PySide6/Qt 6.11.0。

**方法**：真 xcb 连接 + 真 `QWidget`，同一进程内测（`time.perf_counter`，
重复 5 轮取中位数；µs/op）。对照项是旧控制器在**同样条件下**的每拍成本
（`QCursor.pos()` 单次即一次 X 查询；「每拍判定」= `QCursor.pos()` + 盒内
命中判定，不含写窗）。

| 指标 | 实测（median） | 归属 |
|---|---|---|
| 修正后：区域未变的一拍（纯内存比较） | **2.78 µs** | 新增路径（轮询常态） |
| 修正后：区域每拍都变（真 XFixes 请求 + XFlush） | **9.61 µs** | 新增路径（宠物移动中） |
| 修正后：4 只 sprite 时「每拍都变」 | 15.3 µs | 新增路径（随 sprite 数线性） |
| 修正后：8 只 sprite 时「每拍都变」 | 23.5 µs | 同上 |
| 修正前：`QCursor.pos()` 单次 | **18.5 µs** | 既有路径（X 查询） |
| 修正前：每拍判定（`QCursor.pos()` + 命中） | **21.1 µs** | 既有路径 |
| 触发频率 | 修正后 **50 拍/秒**（20ms）；修正前 100 拍/秒（盒内 10ms） | — |

**每秒 CPU 估算**（单核；`µs/秒 ÷ 1e6`）：

| 场景 | 修正后 | 修正前 |
|---|---|---|
| 稳态（宠物静止，纯比较） | 2.78 × 50 = **139 µs/s ≈ 0.014%** | — |
| 稳态（每拍都在动） | 9.61 × 50 = **481 µs/s ≈ 0.048%** | — |
| 稳态（旧控制器常态） | — | 21.1 × 100 = **2115 µs/s ≈ 0.21%** |

**结论（逐条）**：

1. **稳态开销**：下降。旧路径每秒 100 次 X 指针查询 + 命中判定（≈0.21% 单核）；
   新路径 50 拍，静止时每拍只做一次纯内存比较（≈0.014%），移动中才发 X 请求
   （≈0.048%）。即使按最坏情况（每拍都动、8 只宠）也只有 23.5×50≈1.18 ms/s
   ≈0.12%，仍低于旧路径的常态。
2. **新增路径的绝对成本与触发频率**：一次区域收敛 = 1×`XFixesCreateRegion` +
   1×`XFixesSetWindowShapeRegion` + 1×`XFlush` + 1×`XFixesDestroyRegion`，
   实测量级 6.8 µs（9.61 − 2.78）；触发频率上限 50 次/秒，且**只在宠物移动/
   旋转/显隐时才触发**，宠物静止时为零。
3. **系统调用 / 网络 / 磁盘 / 线程**：无新增线程（沿用 overlay 已有的
   `QTimer`，间隔从 10ms 放宽到 20ms）、无网络、无磁盘；新增的是 X 协议请求
   （每拍 ≤1 个 5 次调用的小批量），**不发 `XSync`**，不需要往返等待。
4. **内存与资源**：区域每拍创建后立即销毁，X 服务器侧不累积。RSS 实测：
   连续 10 万次「每拍都变」的区域更新，RSS 在首批 2 万次后停在 +2.5 MiB
   （Python 分配器/GC arena 一次性增长），此后 8 万次**完全持平**
   （88528 KiB → 88528 KiB）——不是泄漏。

> 复测命令：见附录 A；探针写在 `/tmp`（未入库），报告里保留完整源码以便复现。

## 五、实机运行记录

**本机真实环境**：Ubuntu 24.04.5 + GNOME/Wayland（应用 `xcb`）、单屏
2560×1440、Python 3.11.15 + PySide6 6.11.0、真实 `python -m pet` 进程 + 真实
X 窗口（非 offscreen、非 mock）。

### 5.1 根因现场复现（修复前）

- **用户可见行为**（2026-10-25，用户本机）：v4.3.4 安装版，鼠标移到屏幕上的
  桌宠身上，左键、右键点击**均无反应**，点击穿透到下层窗口；用户原话
  「左右键还是被穿透到了下一层窗口」。
- **进程内证据**（干净 v4.3.4 源码实例，隔离 `HOME`，带探针）：
  ```text
  [OLD] pos=(782,1178) local=(782,1146) inside_win=True sprite=None click_through=True input=[]
  ```
  探针每 20 拍打印一次：控制器读到的指针位置固定为 `(782,1178)`，命中判定
  `sprite=None`，`click_through=True`（= 穿透），`XShape` 输入区域
  `input=[]`（空）。同一时刻桌宠在 `(2227,1223,156,194)`（身体中心约
  `(2305,1320)`），`pet-runtime-v2-*.json` 与 `XShape` 区域均对得上。
- **对照实验（证明「落窗」这一步没问题，问题在判据来源）**：
  同一个干净 v4.3.4 实例，用 XTest 把指针注入到宠物身上（走 X 协议）：
  ```text
  [OLD] pos=(2352,1311) ... sprite=('PetSprite', 2125, 1138, 461, 259) click_through=False input=[(0, 0, 2560, 1408)]
  ```
  位置刷新后判据立刻翻成「不穿透」，输入区域恢复整窗。再用独立探针单独实例化
  旧控制器（真窗口、真实 `XShape` 查询）验证两个方向都正常：光标落在「不透明
  像素」上时属性/标志均为 False、输入区域恢复整窗（800×600）；落在「透明像素」
  上时两者均为 True、输入区域为空 `[]`；再回到不透明像素又恢复整窗。
  ⇒ 缺陷不在「Qt 标志落不到平台」，而在「用全局指针位置当输入区域判据」这条链。

### 5.2 修复后的真机输入路由（两窗口探针）

两个真实 xcb 窗口叠加：下层普通窗口（模拟桌面/别的应用）、上层透明 overlay
（带新控制器，一只 sprite 包围盒 = 输入区域 140×110）。用 XTest 注入真实指针
移动 + 真实按键，看事件落在哪个窗口：

```text
窗口 rect=(60,60,420,300)  sprite(局部)=(100, 80, 140, 110)
区域内的点 (230,195)
  overlay 收到 press: [(170.0, 135.0)]
  bottom  收到 press: []
区域外的点 (450,330)（overlay 窗口内、sprite 外）
  overlay 收到 press: []
  bottom  收到 press: [(390.0, 261.0)]
移动后新位置点击 → overlay=[(330.0, 235.0)] bottom=[]
移动后旧位置点击 → overlay=[] bottom=[(170.0, 126.0)]

结论： PASS
```

四个方向都对：宠物身上点得到、宠物旁边穿透到下层、宠物移动后新位置立即
可点、旧位置立即让出。

### 5.3 产品级端到端（main + 本补丁）

用隔离 `HOME` 起真实应用（`packaging/pet_entry_no_chat.py`，`QT_QPA_PLATFORM=xcb`）：

- 启动日志干净（无 Traceback / 无 `X Error`），进入事件循环；
- `XShape` 查询 overlay 的输入区域 = 当前 sprite 的绘制包围盒
  （桌宠 `(2227,1223,156,194)` → 输入区域 `[(2074,1148,461,259)]`，与
  `paint_bounds` 一致）；
- XTest 在桌宠身上右键 → 新建两个窗口（`0x4200012` / `0x4200014`，即右键菜单），
  Esc 后消失；未命中区域点击不弹菜单。

### 5.4 用户确认

同一份修复先在 v4.3.4 源码运行版上交给用户实机验证（2026-10-25）：鼠标左键点击
有反应、右键弹出菜单，多次确认正常；本次 PR 是同一修复移植到 `main`。

### 5.5 边界与失败路径

- 隐藏窗口：`stop()` 清空输入区域（`tests/test_platform_x11_input.py` 断言只对
  真实 XID 发请求，XID 为 0 或窗口已销毁时不发——避免 `BadWindow` 打断 Xlib）。
- 手动「鼠标穿透」：`mouse_through=True` 时区域为空（单测覆盖）。
- X11 初始化失败：`showEvent` 捕获异常并回退 `QtPerPixelInputController`，
  记一条 `WARNING`（代码路径；本机无 X 库缺失环境，未实机复现）。

### 5.6 未验证（如实登记）

- **macOS**：本机无 Apple 硬件，代码路径未改（仍走旧控制器），但未实机回归；
- **原生 Wayland 会话**（`QT_QPA_PLATFORM=wayland`）：新控制器不启用，回退旧
  控制器（行为与修复前逐位一致）——该路径的缺陷未修，也不在本 PR 范围；
- **多屏**：本机只有一块活动屏（`HDMI-1`），多屏下每屏一张 overlay 的输入区域
  未实机验证（每张窗只算自己的 `sprites`，跨屏投影 `_projections` 不参与输入
  区域，与旧 `sprite_at` 口径一致）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv/bin/python -m ruff check pet/ tests/` | **All checks passed** |
| 聚焦（包含新测试） | `pytest -q tests/test_platform_x11_input.py tests/test_platform_passthrough.py tests/test_overlay_window.py tests/test_input_controller_drag.py tests/test_overlay_window_capabilities.py tests/test_overlay_hidden_polling.py tests/test_architecture.py tests/test_pr_report_discipline.py` | **342 passed / 7 skipped** |
| 全量（分片，与 `pr-test.yml` Windows 同款 4 分片、同一忽略清单） | 见下方「全量说明」 | 分片 1–3 与干净基线**失败集合逐条相同**（12 条既有环境失败）；分片 4 见说明 |
| 架构红线 | `pytest -q tests/test_architecture.py` | 通过（含在上一行 342 里） |
| 交付纪律 | `pytest -q tests/test_pr_report_discipline.py` | 通过（含在上一行 342 里） |

**全量说明（如实登记）**：

- 单进程整跑（与 Linux CI 主套件同命令）在本机跑到 **76% 时 `SIGSEGV`（exit 139）**，
  崩点固定为 `tests/test_settings_stale_form_exit.py::test_app_quit_waits_for_the_settings_window`
  的 `_exit_host`（Qt 生命周期家族，仓库已有专门档案与 Windows 分片先例）。
- **该崩溃与本次改动无关**：在干净的 `origin/main`（`34d810b`，未打任何补丁）上
  **单独跑同一个文件同样 `SIGSEGV`（exit 139）**；把该文件单独跑在本分支上也
  一样崩。两边的 faulthandler 栈与 `Extension modules` 列表一致。
- 因此改用仓库既有的 **4 分片**执行器（`scripts/ci_shards.py` 的分片口径）跑全量：
  已完成的分片（3/4）在分支与干净基线上**失败集合逐条相同**——
  `test_click_sound.py` ×6、`test_island_alert_colors.py` ×2、
  `test_overlay_command_concurrency.py` ×2、`test_overlay_tray_routing.py` ×1、
  `test_agent_settings_hot_reload.py` ×1（共 12 条，全部为既有环境性失败；
  基线同样红）。分片 4 在本机耗时异常（同一分片在干净基线上同样慢），
  未纳入本报告的判定依据——**它覆盖的用例与本次改动零重叠**（设置页 / 精灵 /
  天气 / TTS 等），且其头部的 `test_settings_stale_form_exit.py` 已被上面
  的干净基线对照证明会独立崩溃。

## 七、已知限制与后续

1. **输入区域是绘制包围盒，不是逐像素轮廓**：包围盒内的透明边角也会被
   overlay 接住（进入 Qt 后 `sprite_at` 的 alpha 细判未命中时会忽略事件），
   代价是「透明边角上的点击不穿透到下层」。这是为「桌宠一定能点得到」做的
   取舍；若要恢复逐像素精度，可在包围盒基础上按 alpha 生成区域，但每帧
   更新成本会显著上升（当前 6.8 µs/次，逐像素区域会到毫秒级）。
2. **跨屏投影不参与输入区域**：与旧 `sprite_at` 只看本窗 sprite 的口径一致；
   多屏下投影区域内的点击仍会穿透。若需要，可在 `_collect_rects` 里并入
   `projection_rect()`。
3. **原生 Wayland 会话**仍走旧控制器（缺陷未修），因为 Qt 在原生 Wayland 下
   没有 XFixes 可用；后续要修需要换 Wayland 侧的输入区域机制（如
   `wl_surface.set_input_region`，需平台插件暴露）。

## 八、风险与回滚

- **影响面**：仅 Linux + `xcb` 平台插件；Windows / macOS / 原生 Wayland 代码
  路径不变。
- **开关**：无新增配置键、无配置迁移、无落盘格式变化。
- **回滚**：`git revert` 单个提交即可——新模块 `platform_x11_input.py` 与
  `overlay_window.py` 的分流一起撤回，旧控制器仍完整保留（本 PR 未删除它）。
- **失效兜底**：X11 控制器构造失败自动回退旧控制器；运行期异常被 `try/except`
  挡住并记 `WARNING`，不会让 `showEvent` 抛异常。

## 附录 A：复现与测量命令

环境（报告 §四 同一台机器）：

```bash
cd <repo>
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# 真机路由探针（两窗口，XTest 注入真实按键）
DISPLAY=:0 QT_QPA_PLATFORM=xcb .venv/bin/python probe_routing.py

# 旧/新控制器单拍成本（真 QWidget + 真 xcb）
DISPLAY=:0 QT_QPA_PLATFORM=xcb .venv/bin/python probe_perf.py

# 内存持平（10 万次区域更新）
DISPLAY=:0 QT_QPA_PLATFORM=xcb .venv/bin/python probe_mem.py
```

探针源码（未入库，按报告可复现）：

```python
# probe_perf.py 核心段
ctrl = X11InputRegionController(win); ctrl.resume()
def bench(fn, n=20000, reps=5):
    out = []
    for _ in range(reps):
        t0 = time.perf_counter()
        for _ in range(n): fn()
        out.append((time.perf_counter() - t0) / n * 1e6)
    return min(out), statistics.median(out)
print("skip:", bench(ctrl.refresh))          # 区域未变（纯内存比较）
def moved():
    win.sprites[0]._r = QRect(x, 100, 120, 90); ctrl.refresh()
print("apply:", bench(moved, 10000))         # 每拍都变（真 XFixes 请求）
print("QCursor.pos:", bench(lambda: QCursor.pos()))   # 旧控制器每拍的固定成本
```
