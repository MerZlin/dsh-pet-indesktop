# PR 验证报告：网易云音乐悬浮控制器（Windows）

- 分支：`feat/netease-floatbar`（基线 `34d810b`）
- 范围：`pet/music/floatbar.py`（新增）、`pet/app.py`（接入 8 行）、`tests/test_music_floatbar.py`（新增）

> **备注**：本功能只支持**网易云音乐**客户端（Windows），并且必须在该客户端里开启 **SMTC**（`smtcEnable`）开关。没开 SMTC 时系统侧没有媒体会话，读取到的状态恒为「未播放」，上一首 / 播放暂停 / 下一首也发不出去——这不是悬浮条的 bug，而是取数与控制都走系统 SMTC 通道。

## 修改文件说明

逐文件说明「改了什么 + 为什么」，行数取自 `git diff --numstat` 与新增文件行数。

| 文件 | 行数 | 动机 |
| --- | --- | --- |
| `pet/music/floatbar.py` | +1692 / -0 | 新增网易云悬浮控制器：四形态（胶囊 / 圆盘 / 贴边长条 / 贴边控制面板）、两套面板皮肤、播放控制、贴边吸附与开合动画、悬停自动展开、四类灯效、外框单色与两色渐变 |
| `pet/app.py` | +8 / -0 | 在 `main()` 事件循环启动前创建悬浮条，仅在 Windows 且 music 功能已激活时接管 |
| `tests/test_music_floatbar.py` | +377 / -0 | 覆盖灯效取色、两套皮肤的四边几何与按钮落位、皮肤持久化与非法值回落、悬停开合、外框笔刷、贴边拖动（含从控制键上拖）、平台分支 |
| `docs/INDEX.md` | +1 / -0 | 按新文档入场规则登记本报告 |

形态与交互（本次新增，均走 300 ms 几何动画）：

| 形态 | 尺寸 | 内容 |
| --- | --- | --- |
| `expanded` 胶囊 | 226×38 | 标题 + 上一首 / 播放暂停 / 下一首 + 收起 |
| `disc` 圆盘 | 38×38 | 靠近边缘的镂空环带 + ♪ |
| `edge_bar` 贴边长条 | 12×96（竖直边） | 中间镂空灯条；不亮灯时镂空处真透明 |
| `edge_panel` 贴边控制面板（黑胶） | 160×64（竖直边） | 贴屏一侧直边 + 朝屏内一侧半圆（D 形），半圆里画黑胶唱片、矩形段哑光，三个控制键横排 |
| `edge_panel` 贴边控制面板（圆角矩形） | 48×120（竖直边） | 纯圆角矩形（48 厚，比上一版的 64 瘦一圈），贴屏侧两个角保持直角、朝屏内一侧 16 px 圆角，三个控制键竖排 |

两套皮肤的「从屏幕里伸出来」观感由两件事共同构成：

- 贴屏侧两个角是直角（`_rounded_body_path` 用「圆角矩形 ∪ 两个直角方片」把这两角还原；黑胶本来就是直边）；
- 贴屏侧叠了一条 14 px 宽的暗色渐变带（`_BEZEL_SHADE`，`_paint_matte` 里沿 u=0 铺开），模拟屏幕边框压出来的投影，视觉上像面板是从边框后面推出来的。

四条边（左 / 右 / 上 / 下）共用一套规范坐标：u = 离屏距离、v = 沿边长度，由 `_edge_transform()` 统一给 `left` / `right`（镜像）/ `top` / `bottom`（旋转 ±90°）。皮肤与按钮布局只按「垂直屏边 / 沿屏边」两个方向写一次，因此四边自动适配，不需要为上下边再写一套。

交互：

- 圆盘拖到边缘 24 px 内 → 吸附成长条。
- 光标进入长条 30 px 范围 → 自动展开成控制面板；光标离开面板 450 ms → 自动收回成长条。
- 点击长条同样展开面板；面板上只留三个控制键，标题与收起键隐藏。
- **贴边态拖动（本轮修复）**：在长条或面板上按住左键，沿「垂直屏边」方向拉出 ≥ 30 px（`_EDGE_DETACH_PX`）→ 当场换成 38×38 圆盘跟手，贴边状态解除；没到阈值则钉在屏幕边缘、只沿边滑动；松手时若仍贴边则保持当前形态。
- **按在控制键上也能拖（本轮修复）**：三个键盖住了面板中部，`QPushButton` 默认会独吞按下事件，导致「抓住面板往出拉」完全没反应。`_GlyphButton` 现在自己接管鼠标：位移 < 5 px 松手 = 正常点击（`click()`），超过 5 px = 把按下/移动/松手整段序列转交父窗口的拖拽逻辑，并在换圆盘前先 `grabMouse()`，避免按钮被隐藏后丢掉后续移动。
- 右键 →「外框颜色」：6 个预设（4 单色 + 2 渐变）、自定义单色（`QColorDialog`）、两色渐变（先后选两个颜色）。渐变沿窗口对角线铺开，固定 alpha 225。
- 右键 →「面板主题」：黑胶半圆（控制键横排）/ 圆角矩形（控制键竖排），立即重建当前形态并写入 `music_floatbar.json` 的 `panel_skin`；老存档没有这个键时回落黑胶。

动画：

- 展开（长条 → 面板、圆盘 → 长条）用 `OutQuart`（起步快、收尾稳），收贴（面板 → 长条）用 `InOutCubic`（两头缓、中段匀速），时长仍 300 ms。
- 去掉了旧版「窗口透明度 bell」的微呼吸：窗口透明度动画和几何动画叠在一起会让边缘出现忽明忽暗的抖动，现在整个过渡只有尺寸变化，透明度恒为 1。
- 收贴时不再提前隐藏控制键，而是让按钮按目标尺寸先就位、随窗口变窄被逐个裁出，观感像面板一点点收进屏幕边框。

设计约束：

- 不新增 SMTC 轮询。播放状态直接读 `now_playing._session_facts`（纯内存），沿用桌宠既有数据源。
- 点击控制与右键菜单同画像：临时 daemon 线程调 `toggle_play_pause` / `skip_track`，不占用主线程。
- 导入期零副作用：`create_floatbar()` 显式创建，不包装 `QApplication.__init__`。
- 非 Windows 平台 `_is_cloudmusic_running()` 直接返回 `False`，`create_floatbar()` 返回 `None`，窗口不建。

## 性能分析

新增开销共两处，均为定频轻量调用。测量环境：Windows 11 x64、Python 3.11.9、单次进程枚举 20 次取平均。

| 项 | 触发频率 | 实测 |
| --- | --- | --- |
| `_is_cloudmusic_running()`（`kernel32!CreateToolhelp32Snapshot` 枚举进程，全量约 200 个进程） | 1200 ms（仅网易云进程探测定时器） | 1.10 ms/次（20 次平均） |
| 播放状态读取（`now_playing._session_facts`，纯内存列表遍历） | 3000 ms | 微秒级，未单独计时 |
| 灯效重绘（`QTimer` 重绘镂空灯带） | 35 ms，仅「圆盘 / 贴边长条 + 正在播放」时生效 | 未单独计时；不播放时定时器直接跳过重绘 |
| 悬停轮询（`QCursor.pos()` + `frameGeometry().contains()`） | 80 ms | 微秒级；仅在贴边长条 / 控制面板两态做判定，其余形态首行即返回 |

- 稳态开销：进程枚举占单核约 0.09%（1.10 ms / 1200 ms），播放状态读取与悬停轮询可忽略；空闲（网易云未运行）时悬浮条整体隐藏，仅保留 1.2 s 一次进程探测。
- 新增路径成本：贴边吸附与三种开合（圆盘↔长条、长条↔面板）都是一次几何计算 + 300 ms `QVariantAnimation`，不在每个 tick 上跑。无新增系统调用类别（仅进程快照，与现有 `psutil` 路径同级）；无新增网络请求；无新增线程（点击控制走短命 daemon 线程，结束后即回收）；仅一个状态文件 `music_floatbar.json` 在拖动/收起/改灯效/改外框时写入，无高频写盘。
- 内存：1 个无边框 `QWidget` + 5 个 `QPushButton`/`QLabel` + 4 个 `QTimer` + 每个动画一个临时 `QVariantAnimation`（结束即 `deleteLater`），无额外缓存；未做数值化堆内存测量。

## 实机运行记录

真实环境（非 CI、非 mock）：Windows 11 本机，PySide6 6.12.0 + Python 3.13.9 跑单测，另在本地 PyInstaller onedir 构建上验证同一份逻辑。

1. 注入同一份逻辑的构建启动后 `pid 35924` 存活，悬浮条按网易云进程存活状态出现/消失，日志记录 `visibility: cloudmusic running=True`。
2. 源码版离屏冒烟：`create_floatbar(app)` 返回 `MusicFloatBar` 实例；贴边长条恢复为 `12 × 96` 且 `x == 屏幕可用区 left`；控制面板按皮肤为 `160 × 64`（黑胶）/ `48 × 120`（圆角矩形），仍贴左缘。
3. 灯效采样：不播放时镂空 `alpha=0`；播放时单色最亮 `(222,52,52) alpha=255`；彩虹沿长条长边方向色相不同（流动生效）。
4. 离屏跑完整交互链（`QTest.qWait` 驱动真实事件循环）：悬停 → `edge_panel`（黑胶 160×64 / 圆角 48×120，三个控制键可见、标题与收起键隐藏）；移开 450 ms → `edge_bar 12×96 @x=0`；圆盘拖到边缘 → `edge_bar`；长条拖离边缘 → `disc 38×38`；外框两色渐变在胶囊上按对角铺开。
5. 四条边（左/右/上/下）逐一生图核对（两套皮肤各一遍，`d:\fileData\tmp\floatbar_skins.py`）：轮廓一律是「贴屏侧直边 + 朝屏内半圆」的 D 形，半圆里是黑胶（径向底光 + 7 圈纹槽 + 锥形旋转高光 + 标签 + 中心孔），矩形段是哑光（噪点 + 竖向高光/压暗 + 贴屏侧 14 px 暗带）；唱片圆心压在矩形段尽头、盖住最后 `span` 一段，所以三个键的净空区只有 `cross - span`（160-64=96 px），几何断言逐边校验按钮不进入唱片包围盒；圆角矩形皮肤则是 `48×120`（竖边）/`120×48`（横边），贴屏侧两角为直角。
6. 实机安装验证 v13（覆盖 `D:\Program\dsh-pet-standalone-webm-chat\dsh-pet-standalone-webm-chat.exe`，md5 `2f16d379d4416cb7bc0fceb8968302c9`，WMI 启动 `pid 10168`）：日志 `install: QApplication.__init__ wrapped` → `create_bar: visible=True size=12x96 form=edge_bar edge=right`；`EnumWindows + GetWindowRect` 实测贴右边长条 `12×96 @1695,133`，光标移到长条上 1.5 s → `64×120 @1643,119`（当时存档皮肤为圆角矩形），移开 1.2 s → 回 `12×96`；切到黑胶皮肤时同一探针读到 `160×64 @1547,201`。`PrintWindow(flag=2)` 能抓到深色面板与矢量图标（分层窗口截屏不完整，像素级对比仍以离屏生成为准）。
7. 实机安装验证 v16（同一路径覆盖，WMI 启动 `pid 38376`）：日志 `create_bar: visible=True size=12x96 form=edge_bar edge=right`；`GetWindowRect` 实测贴右边长条 `12×96 @1695,…`（缩放 150% ⇒ 截屏坐标 `18×144 @2543,…`），光标移上去 → 圆角面板 `48×120`（截屏 `72×180`）。**拖动链路用窗口消息逐帧验证**：`PostMessage(WM_LBUTTONDOWN)` 落在面板中部（也就是中间那个控制键上）→ 日志立刻出现 `drag begin form=edge_panel edge=right` → 连续 `WM_MOUSEMOVE(MK_LBUTTON)` → `drag detach -> disc` → `WM_LBUTTONUP` → `drag end form=disc`，窗口尺寸同步变为 `38×38`（截屏 `57×57`）。在面板贴屏侧空白处按下的同一套序列结果一致。这同时证明两件事：安装包里确实是本轮代码；「按在控制键上也能拖」的转发链路在真实构建里成立。
8. 独立窗口验证（`d:\fileData\tmp\floatbar\live_bar.py`，只起 `MusicFloatBar`、不加载桌宠主程序）：`SetCursorPos + mouse_event` 模拟「光标移到长条 → 面板展开 `48×120` → 在贴屏侧按下 → 朝屏内拖 96 px」→ 事件轨迹 `MouseButtonPress drag=False` → 一串 `MouseMove drag=True` → `form=disc 38×38`，窗口 rect 同步 `72×180 → 57×57`，日志 `drag begin` / `drag detach -> disc` / `drag end`。**注意**：同一套 `SendInput/mouse_event` 打到已安装的桌宠进程时，左键按下不会送达悬浮条（前台是全屏应用 `廊坊办公`，`GetGUIThreadInfo` 显示该进程 GUI 线程无 capture；悬浮条自己的 `contextMenuEvent` 与按钮点击都收不到，但它上面的「宠物右键菜单」会弹出来），因此实机左键链路只能用窗口消息验证 + 由用户手动确认，详见「已知限制」。

## 测试门

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 语法编译 | `python -m py_compile pet/music/floatbar.py pet/app.py` | PASS |
| 静态检查（全仓） | `ruff check .` | PASS —— `All checks passed!` |
| 新增单测 | `pytest tests/test_music_floatbar.py -q` | PASS —— `23 passed in 1.35s`（`QT_QPA_PLATFORM=offscreen`） |
| 交付纪律 + 架构守卫 | `pytest tests/test_pr_report_discipline.py tests/test_architecture.py tests/test_music_floatbar.py -q` | PASS —— `270 passed in 5.02s` |
| 全量单测 | `pytest -q` | **8884 passed / 169 failed / 32 skipped**（1562 s）。169 个失败全部来自本机环境，与本次改动无关，见下。**注**：该次全量跑在面板皮肤重构之前；重构后只改了 `pet/music/floatbar.py` 与它的测试文件，已重跑 `ruff check .` 与上三行门禁（270 passed），未再跑一遍全量（本轮改动不触及共享模型 / 生命周期 / 打包 / 平台分支之外的面） |

全量失败归因（已做基线对照：`git stash push -- pet/app.py` 后复跑同族，失败数逐项相同）：

| 失败族 | 数量 | 根因 |
| --- | --- | --- |
| `test_voice_chime_service.py` | 51 | 本机无音频设备/`sounds_cache`，`_current_respect_quiet` 恒为 `None`。基线对照同为 51 failed / 74 passed |
| `test_frameseq_provision.py` + `test_webm_clip_*` + `test_webm_first_frame_lock.py` | 92 | 本机 ffmpeg 探测不到可用解码器（`None has no attribute 'read_frames'`）。基线对照同为 53 failed / 81 passed（本组子集） |
| `test_application_icon.py` | 2 | Windows 上 `bash -n` 无法解析 `D:\...` 路径，脚本语法检查必然 127 |
| 其余（`test_agent_link` / `test_move_sync` 等） | 24 | 沙箱禁止写 `%APPDATA%` 等目录；关闭沙箱后大部分恢复 |

这些文件没有一个导入 `pet.music.floatbar` 或依赖 `pet/app.py` 的 `main()`，因此不属于本次改动的影响面。

已知限制：

- 功能仅 Windows 有效（SMTC 与进程枚举）。macOS/Linux 上模块可导入、不建窗口。
- 网易云客户端必须开启其 `smtcEnable` 开关，否则系统侧无媒体会话，播放控制无效（读取状态为“未播放”）。
- 贴边吸附阈值 24 px、长条 12×96 为经验值，见 `_EDGE_SNAP_PX` / `_EDGE_THICK`·`_EDGE_LONG`；面板尺寸按皮肤：黑胶 `_VINYL_PERP=160`（垂直屏边厚度）×`_VINYL_ALONG=64`（沿边长度，半圆半径 = 32），圆角矩形 `_PANEL_THICK=48`×`_PANEL_LONG=120`。黑胶皮肤下 `perp` 不能小于 `along + 三个键的总宽 90 + 边距`，否则按钮会被唱片压住。悬停展开距离 30 px、移开宽限 450 ms 见 `_HOVER_NEAR_PX` / `_HOVER_LEAVE_MS`；贴边拉出阈值 30 px 见 `_EDGE_DETACH_PX`，控制键上「移动超过 5 px 才算拖动」见 `_GlyphButton._DRAG_SLOP_PX`。
- **左键链路的本机验证边界**：本机前台是全屏应用（`廊坊办公`，hwnd `133966`），`SendInput` / `mouse_event` 注入的左键按下不会送达已安装的桌宠进程（`GetGUIThreadInfo` 显示其 GUI 线程无 capture，悬浮条也收不到自己的右键菜单），但同一套注入在独立实例（`live_bar.py`）上完全正常。因此实机左键拖动用 `PostMessage(WM_LBUTTONDOWN/ WM_MOUSEMOVE/ WM_LBUTTONUP)` 逐帧验证（日志 `drag begin` → `drag detach -> disc` → `drag end`，尺寸 `48×120 → 38×38`），真实鼠标拖动仍需使用者手动确认一次。
- 四条边的贴边、拖动、皮肤适配以几何断言 + 离屏生图覆盖；实机只对右边缘做了安装验证，上下边缘的实机观感待使用者拖动圆盘到上/下边缘确认。
- 外框的 `QColorDialog` 取色未在离屏环境自动化（模态对话框无法在无头环境点按），由 `_frame_brush()` 的单测覆盖数据通路；取色交互需实机点一次确认。
- 两套皮肤、四条边的按钮落位以几何断言覆盖（按钮矩形不得进入唱片包围盒），像素级对比以离屏 `QWidget.grab()` 生成为准；实机 `PrintWindow` 对分层透明窗只能抓到部分内容，不能作为像素证据。
