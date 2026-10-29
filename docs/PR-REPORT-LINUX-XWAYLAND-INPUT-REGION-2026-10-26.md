# Linux/XWayland：桌宠左右键点不到——输入区域改由 XFixes 服务端判定

> **基线**：`origin/main` `34d810b`（v4.4.0 + `chore(update): update.json 指向 v4.4.0`）
> **分支**：`fix/linux-xwayland-input-region`　**日期**：2026-10-26
> **范围**：8 个文件（实现 4、测试 1、文档 3）
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
XFixes `ShapeInput` 把 overlay 的输入区域设成「sprite 当前帧**画出来的不透明
像素**」（逐行矩形并集）：

- 指针落在宠物身体像素上 → 合成器把输入交给 overlay（Qt 收 enter/move/press）；
- 指针落在**包围盒内的透明区域** → 该点不在输入区域内，事件照常落到下层应用
  （不会出现「点了没反应、也不穿透」的死区）；
- 区域随动画帧/移动/旋转重算（20ms 定时拍里先做一次廉价签名比较，静止画面
  零渲染、零 X 请求），**没有任何「先读指针再决定区域」的循环**。

**历史：为什么 v4.3.0 之前没有这个问题**

| 版本 | 输入区域怎么来 | 结果 |
|---|---|---|
| v4.2.1 及更早（`PetWindow` 单宠真窗口） | **非 Windows 每帧 `QWidget.setMask(alpha mask)`**（`window.py::_sync_mask`，`git show v4.2.1:pet/window.py` 2269 行）：窗口小、mask 由**当前帧的 alpha 轮廓**算出，X11 侧由 Qt 下发成 XShape（bounding + input）——区域由画面决定，**与指针位置无关**。Windows 走另一条：不 `setMask`（1-bit 裁剪会破坏半透明边缘），改用 `WS_EX_TRANSPARENT` 轮询（`GetCursorPos`），Win32 的光标查询恒准。 | 正常。点宠物命中窗口，点旁边穿透。 |
| v4.3.0 起（默认拓扑改成**单张全屏 overlay**） | 一个铺满全屏的透明窗承载**全部**宠物，无法再「一个窗口一个 alpha mask」（全屏逐帧 alpha 位图代价过大；且 Windows 明确不用 mask）。非 Windows 的穿透实现（`2288870`，2026-10-07，随 v4.3.0 首次发布）**照搬了 Windows 的轮询协议**：每 10/50ms `QCursor.pos()` 决定整窗「输入/穿透」。 | 在本机（X11/macOS）失效。作者在提交信息与发布稿里**明确登记未实机验证**：「非 Windows 的逐像素输入穿透有若干路径只有替身证据」「macOS / Linux 的真实 GUI 未实机验收……请当预览对待」。`platform_passthrough.py` 至今保留这段自我声明。 |

也就是说：**4.3.0 换掉了「由画面决定输入区域」的机制，换成「由全局指针位置决定」，
而后者在 Win32 上成立的假设（`GetCursorPos` 恒准）在 X11/XWayland 上不成立**。
这不是某个函数写错，而是把「查询全局指针」当成了跨平台事实——`platform_passthrough`
的模块 docstring 里甚至写了「Qt 6.8 源码证据 + 本机 offscreen 实测」，但真实 X11
GUI 从未跑过。

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
| `pet/platform_x11_input.py` | **+597 / −0**（新增） | X11/XWayland 输入区域控制器：XFixes `ShapeInput` 按 sprite 绘制包围盒设置；20ms 收敛；状态未变零请求；原生窗口重建重放；隐藏/关闭清区域；失败可回退。 |
| `pet/pet_sprite.py` | **+32 / −0** | 新增 `input_revision()`：把「帧/位置/缩放/探头角/抛掷角/Q 弹/DPR」收成一个可比较代次元组，平台层据此判断逐像素轮廓要不要重算（不用 `id()`，避免对象复用撞假命中）。 |
| `pet/overlay_window.py` | +47 / −13 | `showEvent` 的非 Windows 分支按平台插件分流：Linux/xcb 建 `X11InputRegionController`（初始化失败回退旧控制器），其余沿用 `QtPerPixelInputController`；`closeEvent` 对持有 X Display 的控制器调 `close()`；`hideEvent` 文档补充「X11 清空输入区域」；模块 docstring 同步。 |
| `pet/platform_passthrough.py` | **+9 / −0**（仅 docstring） | 登记「Linux/X11 不再使用本控制器」及原因，避免后来者按旧注释把 Linux 改回去。**零行为变化**。 |
| `tests/test_platform_x11_input.py` | **+288 / −0**（新增） | 11 例纯逻辑回归：平台探针、**逐像素轮廓（透明角落必须让出输入、面积≈不透明像素）**、轮廓缓存只在代次变化时重算、包围盒兜底与窗口裁剪、隐藏/手动穿透产出空区域、状态未变不重复发请求、原生窗口重建重放、`stop()` 只对真实 XID 清区域、窗口已销毁时不动 X、共享 X 连接引用计数、Xlib `BadWindow` 必须被吞掉。 |
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

### 3.0 为什么不是 `setMask`，也不是继续用 Qt 标志

- **`QWidget.setMask` 不行**：它会同时设 bounding 与 input 两个 shape。我们有一个
  必须表达的状态——**窗口完整可见、但对输入完全透明**（用户手动「鼠标穿透」；以及
  `mouse_through` 语义）——这只有 input-only 能表达：`setMask(空)` 按 Qt 语义是
  「清除 mask」= 恢复整窗输入，正是会吞桌面的那个 bug（issue #195 的原话：
  「不能交空 mask」）。用 mask 就等于把输入区域绑死到绘制裁剪上，代价还有
  bounding 变化引起的重绘/合成开销。
- **继续用 `Qt::WindowTransparentForInput` 标志也不行**：那是「整窗开/关」的二进制
  状态，必须*先知道*指针在哪才能决定——就是本 bug 的自锁本身。
- **不是不能复用旧代码**：macOS/原生 Wayland 仍用旧控制器（它们没有 XFixes 输入
  区域可用），Linux 只在初始化失败时回退它。

### 3.1 为什么是「服务端输入区域」而不是「更准的指针查询」

即使把 `QCursor.pos()` 换成 XInput2 raw motion / `xcb_query_pointer` 直读，判据
仍然依赖「先把指针位置读回来」，而读回来的前提是事件能到达进程——在
XWayland 下这又依赖「这个 surface 接受输入」。**判据与被判对象互为前提**，
这才是缺陷的结构，而不是某个查询 API 不够快。因此把真值交给 X server：
区域由 sprite 绘制范围决定，指针在不在区域内由服务端/合成器直接路由。

### 3.2 区域怎么算（逐像素轮廓）

`X11InputRegionController._sprite_region()`：

1. **渲染**：把 sprite 按与 `paintEvent` 相同的入口 `sprite.paint(painter)` 画到
   离屏 `QImage(ARGB32_Premultiplied)`——探头角、抛掷角、镜像、Q 弹压缩全由
   `paint()` 自己施加，本模块不复制任何变换逻辑（复制必然漂移）。
   大 sprite（>6 万逻辑像素）按 2 倍下采样渲染：区域粒度 2 逻辑像素，点感无差别，
   渲染与扫描都省一半以上。
2. **阈值**：取 alpha 通道，阈值 `_ALPHA_HIT_THRESHOLD = 16`，与
   `OverlayWindow.ALPHA_HIT_THRESHOLD` / `PetSprite.alpha_at` **同口径**——
   「能点到哪」必须等于「`sprite_at` 认哪」，否则会出现「区域接住了、Qt 判透明」
   的 1px 死区。
3. **压矩形**：numpy 一次算完整幅图的 0→1 / 1→0 跳变（`np.diff`），按行切出连续
   不透明段，再垂直合并「上下相邻且 x/宽相同」的行。实测一帧 128～152 条矩形。
4. **坐标**：图像坐标 × `rect_scale` + 包围盒原点×DPR = 窗口物理坐标（X 窗口
   坐标系是设备像素），再按窗口矩形裁剪。
5. **缓存两级**：
   - sprite 级：`(input_revision, dpr, bounds)` 不变就复用矩形表；
   - 合成级：每拍只算 `(dpr, 窗口尺寸, [(id(sprite), input_revision), ...])`
     签名，未变直接返回上次列表（空转一拍 45～70µs，与包围盒版同量级）。

拿不到逐像素轮廓时（假 sprite、渲染失败、numpy 不可用）退回绘制包围盒：
保证「至少点得到」，不会把桌面吞掉。

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

**方法**：同一棵源码树、同一份真实配置（隔离 `HOME`）、真实 `PetSprite` 动画和
真实 X 连接；在 `_collect_rects` 入口打点。A/B 交替各跑两轮，每轮 60 秒稳态窗口
（前 10 秒预热不计，排除素材/首帧解码）：

- **A（本 PR）**：逐像素轮廓；
- **B（对照组）**：把 `_sprite_region` 强制返回 `None` = 退回绘制包围盒
  （等于上一版行为）。

| 指标（60s 窗口） | A 逐像素 | B 包围盒 | 差值 |
|---|---|---|---|
| 输入区域计算耗时 | **981 ms / 966 ms** | 222 ms / 190 ms | **+759 / +776 ms** |
| 折算单核占用 | **1.61% / 1.64%** | 0.32% / 0.37% | **+1.27 / +1.29 个百分点** |
| 进程 CPU（整进程） | 24.27 s / 23.55 s | 25.07 s / 22.27 s | 在测量噪声内（±1.5 s） |
| 每拍常态（签名未变） | 45–70 µs | 71 µs | 同级 |
| 每次重算（换帧/移动/旋转） | p50 ≈ 488 µs、p99 ≈ 1 ms | p50 ≈ 82 µs | +406 µs/次 |
| 重算频率 | 23.8 次/秒（= 动画帧率） | 23.8 次/秒 | 同源 |
| 输入区域矩形数 | 128–385 条（随姿态） | 1 条 | — |

**结论（逐条）**：

1. **绝对值**：可见且动画时新增约 **1.3% 单核**（12.7 ms CPU/秒，约 45 s/小时）。
   相对于本机桌宠进程「可见动画时约占 30–40% 单核」的本体成本，**相对增幅约
   3.5%**；窗口隐藏时控制器停表 → 成本为 0。
2. **成本构成**：空闲拍（签名未变）2–3.5 ms/s ≈ 0.2–0.35% 单核；换帧重算
   0.5 ms × 24 次/秒 ≈ 1.2% 单核——新增成本几乎全在「每动画帧重算一次像素轮廓」。
3. **无新增线程/网络/磁盘**：沿用 overlay 已有 20ms `QTimer`；每帧一次 X 协议
   请求（`XFixesCreateRegion` + `SetWindowShapeRegion` + `XFlush` +
   `DestroyRegion`，128–385 条矩形 ≈ 1–3 KB，24 次/秒 → < 75 KB/s），**不发
   `XSync`**。
4. **内存**：4000 次连续重算 RSS +0.13 MiB（持平）；应用整体 RSS 的波动来自
   素材/帧缓存预热，包围盒对照组同样增长（+31.9 MiB vs 逐像素 +21.7 MiB，
   同窗口同量级，与区域路径无关）。
5. **可调旋钮**：区域粒度（当前大 sprite 2 倍下采样）与重算频率是两个线性旋钮。
   1:1 精度约 2.6% 单核；每 2 帧更新约 0.7% 单核（动画期边缘最多 80 ms 滞后）；
   移动/拖拽期退回包围盒可再降。当前档位在真机上点击手感与包围盒版无差别。

> 复测脚本：`/tmp` 下的 A/B 探针（未入库），命令见附录 A。

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

### 5.2 修复后的真机输入路由（两窗口探针，三种落点）

两个真实 xcb 窗口叠加：下层普通窗口（模拟桌面/别的应用）、上层透明 overlay
（一只 sprite：包围盒 300×200，只在中间画一个 160×120 的椭圆）。三个点击点：

```text
输入区域矩形数=120  包围盒=(0,0,300,200)
A 椭圆内 (200,500): overlay=True
  点击 → overlay=[(140.0, 100.0)] bottom=[]
B 包围盒内透明 (65,405): overlay=False
  点击 → overlay=[] bottom=[(5.0, 5.0)]
C 包围盒外 (550,710): overlay=False
  点击 → overlay=[] bottom=[(490.0, 310.0)]

结论： PASS（透明像素已让出输入）
```

关键点：**B 点在包围盒内、但当前帧是透明像素**——修复前它会被 overlay 吞掉
（用户说的「点了个寂寞」），现在事件落到下层窗口。A 点（身体像素）照常由
overlay 接住。

### 5.3 产品级端到端（main + 本补丁）

用隔离 `HOME` 起真实应用（`packaging/pet_entry_no_chat.py`，`QT_QPA_PLATFORM=xcb`）：

- 启动日志干净（无 Traceback / 无 `X Error`），进入事件循环；
- `XShape` 查询 overlay 的输入区域 = **当前帧不透明像素的逐行矩形**
  （桌宠 `(2227,1223,156,194)` 一帧实测 **128 条矩形**，外接盒
  `(2222,1188)` 起、不再是整块 461×259 包围盒；随动画帧变化）；
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
| 聚焦（包含新测试） | `pytest -q tests/test_platform_x11_input.py tests/test_platform_passthrough.py tests/test_overlay_window.py tests/test_architecture.py tests/test_pr_report_discipline.py` | **318 passed / 2 skipped** |
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

## 六点五、与 #245（社区 X11 兼容 PR）的关系

本仓库已有一个 **open** 的社区 PR **#245**（`Huffer342-WSH:fix/pyside612-runtime`），
其中 `fix(overlay)` 主题也做了 X11 输入形状（`pet/linux_overlay.py` 的
`LinuxOverlayInput`，用 `XShapeCombineRectangles` 按**绘制像素**设 input shape），
并附带虚拟桌面几何 / `X11BypassWindowManagerHint` / 拖动与气泡修复。

维护者 klxxya 在 #245 下的分流结论（2026-10-14）：

> 「（#245 里的）新功能联动与 X11 兼容部分计划下个版本按现行架构另开小 PR
> 移植（整段合会与 agent_link 重写冲突）。本 PR 先保持 open 跟踪这两块。」

本 PR 就是那句话里的「X11 兼容——输入区域」这一刀，按**当前 main 架构**重写，
范围刻意收窄到「桌宠点得到 + 桌面不被吞」：

| | #245 `LinuxOverlayInput` | 本 PR `X11InputRegionController` |
|---|---|---|
| 区域粒度 | 绘制像素（alpha 掩码，按 mask 缓存） | 绘制像素（alpha 掩码 → 逐行矩形；大图 2px 自适应） |
| 更新时机 | 事件过滤 + 调用方驱动 | 20ms 定时 + `(winId, rects)` 去重（静止零 X 请求） |
| 覆盖状态 | 输入形状 | 输入形状，含「隐藏/手动穿透 → 空区域」 |
| 附带改动 | 虚拟桌面几何、WM hint、拖动、气泡、新功能联动、Qt 修复 | **只此一项**（不动 WM/几何/拖动/气泡） |
| 与 #245 的关系 | 分支基于 4.2.x-era 架构，`mergeable_state=dirty` | 基于当前 main（`34d810b`），无冲突 |

**取舍**：两边都是像素掩码粒度；本 PR 用 2px 自适应下采样换每帧 0.5ms 级成本，
#245 用全分辨率 mask 换 1px 精度。接口（`_collect_rects` → XFixes）与生命周期
两边一致，若维护者要 1:1 精度，只需把 `render_scale` 阈值调大（有 A/B 数据）。

## 七、已知限制与后续

1. **输入区域是 2px 粒度的逐像素轮廓**（大 sprite 按 2 倍下采样渲染）：区域
   边缘与真实 alpha 轮廓最多差 2 逻辑像素——透明外缘最多 2px 会接住输入、
   身体外缘最多 2px 会穿透。这是为了把每帧成本压在 0.5ms 级的刻意取舍；
   需要 1:1 时把 `_sprite_region` 里的 `render_scale` 阈值调大即可（成本
   线性上升，已有 A/B 数据可对照）。拿不到轮廓时（假 sprite / numpy 缺失）
   退回包围盒，行为等同上一版。
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
