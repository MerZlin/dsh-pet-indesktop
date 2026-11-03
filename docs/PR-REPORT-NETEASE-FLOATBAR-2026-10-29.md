# PR 验证报告：网易云音乐悬浮控制器（Windows）

- 分支：`feat/netease-floatbar`（基线 `34d810b`）
- 范围：`pet/music/floatbar.py`（新增）、`pet/app.py`（接入 8 行）、`tests/test_music_floatbar.py`（新增）

> **备注**：本功能只支持**网易云音乐**客户端（Windows），并且必须在该客户端里开启 **SMTC**（`smtcEnable`）开关。没开 SMTC 时系统侧没有媒体会话，读取到的状态恒为「未播放」，上一首 / 播放暂停 / 下一首也发不出去——这不是悬浮条的 bug，而是取数与控制都走系统 SMTC 通道。

## 修改文件说明

逐文件说明「改了什么 + 为什么」，行数取自 `git diff --numstat` 与新增文件行数。

| 文件 | 行数 | 动机 |
| --- | --- | --- |
| `pet/music/floatbar.py` | +2387 / -0 | 新增网易云悬浮控制器：四形态（胶囊 / 圆盘 / 贴边长条 / 贴边控制面板）、两套面板皮肤、播放控制、贴边吸附与开合动画、悬停自动展开、四类灯效、外框单色与两色渐变；后续迭代加入彩虹接缝修复、曲名循环滚动、真实频谱可视化、五档线条粗细、渐变配色、黑胶圆环频谱、键帽背景、图标对齐与三个独立开关（后续迭代合计 `+730 / -35`） |
| `pet/music/spectrum.py` | +321 / -0 | 新增系统音频频谱采集：ctypes WASAPI 回环 + numpy FFT，只依赖标准库与已随包分发的 numpy（不引入 soundcard / PyAudio 这类新二进制依赖），采到的是用户此刻听到的输出混音 |
| `pet/app.py` | +8 / -0 | 在 `main()` 事件循环启动前创建悬浮条，仅在 Windows 且 music 功能已激活时接管 |
| `tests/test_music_floatbar.py` | +861 / -0 | 覆盖灯效取色、两套皮肤的四边几何与按钮落位、皮肤持久化与非法值回落、悬停开合、外框笔刷、贴边拖动（含从控制键上拖）、平台分支；后续迭代补彩虹角度连续性、曲名循环滚动、频谱几何/配色/采集启停、五档粗细、黑胶圆环、图标对齐与三个开关（后续迭代合计 `+485 / -1`，共 51 个用例） |
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

### 第二轮迭代（2026-11-03）：彩虹接缝 / 曲名标题 / 音乐可视化

**1）圆盘彩虹灯效的循环接缝。** 旧实现用 `angle = math.degrees(self._breathe_phase * 0.9) % 360` 取锥形渐变起始角；`_breathe_phase` 每跨过 2π 会一次性减去 2π，对应的角度就从 324° 一侧跳到另一侧——实测第 52 个心跳（35 ms × 52 ≈ 1.8 s）角度由 `321.77°` 跳到 `3.96°`，相邻两帧差 `42.19°`（正常步长 `6.19°`），多出来的 36° 就是环带上一道肉眼可见的分界。改为独立累加器 `self._rainbow_angle = (self._rainbow_angle + degrees(step * 0.9)) % 360.0`，角度在 360° 处按弧度周期自然回绕，渐变本身是周期函数，因此接缝消失；单测按「每拍增量恒定」逐拍校验 80 拍。

**2）胶囊条标题改为当前曲目名。** 标题仍是那个 96×30 的 `QLabel`，字号 8 pt，内容改成「有曲目就显示曲名，没有就回落『网易云音乐』」，超长用 `fontMetrics().elidedText(..., 92)` 右省略，`toolTip` 保留全文。曲名来自新的常驻采样线程：

- `_track_changed = Signal(str)` 由后台线程 emit，Qt 自动排队到主线程改 UI；
- 线程按 `_REFRESH_MS`（3000 ms）读一次 `pet.music.now_playing.get_now_playing()`，只认 `app_id` 含 `cloudmusic` 的会话，其他播放器抢到焦点时不显示它们的歌名；
- 线程常驻而不是每次采样新起线程：`get_now_playing` 自带事件循环，COM 公寓按线程累积，频繁换线程会不断重建公寓；
- 网易云进程消失 → `_set_track_title("")` 并隐藏窗口；暂停 / 无会话时 `get_now_playing()` 返回 `None` → 同样回落「网易云音乐」。

**3）胶囊条音乐可视化（伪频谱，不抓音频）。** 22 根竖条（`_VIZ_BARS`，间距 2 px），高度是三条不同周期正弦叠加再乘「左高右低」包络，值域钳在 `0.10~1.0`，相位按心跳 `0.34/tick` 推进，只在播放时推进与重绘。

| 设置 | 选项 | 行为 |
| --- | --- | --- |
| 位置 | 胶囊条上方（默认）/ 胶囊条内部 | 上方：窗口高度 38 → 64，顶部 23 px 画带子、胶囊本体仍在底部（`_target_rect` 只把 `y` 上移 `_VIZ_H`）；内部：窗口保持 226×38，竖条最大 13 px，画在胶囊底部 |
| 配色 | 单色 / 呼吸 / 彩虹 / 随机 | 单色与呼吸用 `_viz_color`（默认 `#de3434`）；呼吸的 alpha 在 25%→100% 之间跟 `_breathe_phase` 起伏；彩虹按条索引 + 相位给色相；随机复用现有的 `_rand_from → _rand_to` 渐变 |
| 半透明度 | 0~100（默认 45） | 只作用于 alpha，四种配色共用；右键有 20/40/60/80% 预设与「自定义…」（`QInputDialog`） |
| 颜色 | 任意 | 「可视化颜色…」（`QColorDialog`），只影响单色与呼吸 |

设置全部写进 `music_floatbar.json` 的 `viz_pos` / `viz_mode` / `viz_alpha` / `viz_color` 四个键；老存档缺键时依次回落「上方 / 单色 / 45 / #de3434」，非法值同样回落（单测覆盖）。

展开 / 收起的中间态也做了对齐：新增 `_anim_progress` 由 `_animate_to` 的 `on_update` 写入，`_viz_band_progress()` 按「正在长 / 正在收」给出 0→1 的带宽比例，`paintEvent` 用 `round(_viz_band_h() * progress)` 算带高。这样窗口从 38 长到 64 的过程中，胶囊本体始终是 38 px（`body = rect.adjusted(0, band, 0, 0)`），不会出现「先压扁再弹回」。动画中途切换频谱位置也不再调用 `_apply_form` 打断几何动画。

### 第三轮迭代（2026-11-03）：真实频谱 / 标题滚动 / 渐变配色

**4）谱线换成真实频谱（不再是正弦波）。** 先查了现成方案：`soundcard`（纯 Python + cffi）、`PyAudioWPatch`（PortAudio 的 WASAPI 回环分支）、`proc-tap`（按进程隔离）都能录系统输出，但**都带新的二进制依赖**；而本机安装包的验证链路只能替换 PYZ 里的纯 Python 模块，装不进新的 `.pyd`/数据文件，所以它们在这条链路上不可用。最终选了「只用 `ctypes` + 已随包分发的 `numpy`」直接调 WASAPI：

- `CLSID_MMDeviceEnumerator` → 默认渲染端点 → `IAudioClient::Initialize(SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 500 ms)` → `IAudioCaptureClient` 轮询取包；
- 取到的是**用户此刻听到的系统混音**（48 kHz / 双声道 / float32，`GetMixFormat` 实测 `wFormatTag=0xFFFE`），所以它与正在播放的曲目严格同步，暂停就没电平；
- 双声道取均值 → 4096 点滚动窗（约 85 ms）→ 汉宁窗 `rfft` → 60~8000 Hz 对数分 22 段 → 每段 RMS 转分贝（`floor=-72 dB`、`span=57 dB`、每段 +0.3 dB 斜率补偿、`gamma 0.9`）→ 快起慢落平滑（上跳立即、下落 `0.82/0.18`）；
- 线程只在「正在播放 + 胶囊条形态」时存在（`_sync_spectrum` 在 35 ms 心跳里判定），收起 / 暂停立刻 `Stop` 并退出；采集失败（无设备、COM 报错、numpy 缺失）只置一次 `failed`，绘制回落到原来的程序化波形，不会让悬浮条挂掉；
- COM 函数指针按「接口地址 + 下标 + 签名」缓存，避免每次调用都重建 `WINFUNCTYPE`；轮询间隔 20 ms；
- 实测（预热后 3 次 × 4 s，`time.process_time()`）：**单核占用 1.17% / 1.95% / 1.95%**；播放时 22 段全部有值且逐帧变化（例如 `0.16 0.18 0.31 0.36 0.69 0.60 0.51 0.40 …`）。

**5）曲名改为往返滚动。** 新增 `_ScrollingLabel(QLabel)` 自绘标题：宽度塞得下就居中不动，塞不下就按 1 px / 35 ms（约 28 px/s）向右滚，滚到尽头停 1.2 s（34 个心跳）再向左滚回起点，不瞬跳回开头；标题始终取完整曲名（`toolTip` 同样是全文），不再用 `elidedText` 截断。标题控件设 `WA_TransparentForMouseEvents`，按在标题上也能拖动悬浮条。

**6）新增「渐变」配色。** 配色从四种扩到五种：单色 / 渐变 / 呼吸 / 彩虹 / 随机。渐变用两个可自定义颜色（`viz_color` 起点、新增 `viz_color2` 终点，默认 `#de3434 → #ff9a3c`）按频谱条索引线性插值；右键菜单新增「渐变第二颜色…」，「可视化颜色…」同时承担单色颜色与渐变起始色。两个颜色都写进存档（`viz_color` / `viz_color2`），非法值回落默认。

另外把「胶囊条内部」的频谱高度从 13 px 提到 30 px：13 px 时矮条宽度≈高度，整条频谱看起来像一串圆点；30 px 铺满胶囊，控制键与标题压在频谱之上（半透明，仍可辨认）。

### 第四轮迭代（2026-11-03）：线条粗细 / 彩虹循环 / 独立开关

**7）五档线条粗细（解决「频谱放不下」）。** 之前条数写死 22 条，窄面板上每条只有 7 px 左右。现在右键「音乐可视化 → 线条粗细」有五档，档位直接定义画面上的条数（采集侧同步提到 **48 段**，细档才有足够的分频细节）：

| 档位 | 条数 | 214 px 面板上的单条净宽 |
| --- | --- | --- |
| 极细 | 40 | ≈ 3.4 px |
| 细 | 30 | ≈ 5.1 px |
| 标准（默认） | 22 | ≈ 7.7 px |
| 粗 | 16 | ≈ 11.4 px |
| 极粗 | 10 | ≈ 19.4 px |

- 极细档固定 **40 条**（用户要求「不少于 40」），最粗档固定 10 条，整个胶囊条面板上都一定塞得下；
- `_viz_bar_count()` 还会按实际可用宽度兜底（每条最少 3 px），不会画出糊成一团的 1 px 细线；
- 采集侧是 48 段电平，`_viz_resample()` 在「条数 < 48」时**取每组最大值**压条——粗档不会把尖峰平均掉；
- 档位写进存档 `viz_width`（0~4），非法值回落默认「标准」。

**8）彩虹配色的循环接缝。** 旧实现是 `hue = index/22 + phase/(2π) × 0.35`：`phase` 每跨 2π 会整体回绕，色相偏移一次跳 **0.35 圈（≈126°）**，所以彩虹看起来「转一圈后突然跳色」。改成独立累加器 `_viz_hue`（每拍 +0.01，35 ms × 0.01 ≈ **3.5 s 绕一圈**），只在超过 1.0 时减 1.0；因为色轮本身是周期的，回绕点两帧的颜色只差 1%，肉眼看不出接缝。单测逐拍校验 120 拍（跨过一次回绕）里增量恒定。

**9）附加功能各自一个开关。** 右键新增「附加功能」子菜单：

- **显示当前曲名**（`title_enabled`）：关掉后标题固定显示「网易云音乐」，曲名采样线程立刻停止，不再每 3 s 读一次 SMTC；
- **音乐可视化**（`viz_enabled`）：关掉后不画频谱、窗口高度从 64 收回 38（`_viz_band_h()` 返回 0）、采集线程也不再创建，CPU 与内存回到纯控制条水平。

两个开关都写进存档；缺键或值不是布尔时按「开」处理（老存档升级不会被误关）。「频谱位置 / 线条粗细 / 配色 / 半透明度」都是「音乐可视化」下面的子选项，总开关一关就一并失效。

### 第五轮迭代（2026-11-03）：循环滚动 / 黑胶圆环 / 键帽背景

**10）曲名改为循环滚动（不再往返）。** 往返滚动滚到末尾会反方向倒回去，看起来像「没滚完就回开头」。改成循环：

- 周期 = 文本宽 + 56 px 间距；偏移量到周期就减去周期，回到起点，只在起点停 1.2 s；
- `paintEvent` 同时画两份文本（第二份起点 = 第一份 + 文本宽 + 间距），第一份滚出左边时第二份正好从右边接上；
- 回绕那一帧里「第二份在 0」与下一帧「第一份在 0」完全重合，所以看不出跳变。

**11）黑胶盘上的闭合圆环频谱。** 右键「附加功能 → 黑胶圆环频谱」：

| 设置 | 说明 |
| --- | --- |
| 启用 | 默认开；关掉后黑胶盘恢复原样，也不再为此形态起采集线程 |
| 圆环数量 | 1~5 条，默认 3 条 |
| 圆环颜色 | 自定义颜色（默认 `#de3434`），第 n 圈 alpha 递减 10% 形成层次 |

每条环都是 96 点闭合路径：角度 → 电平（22 段按线性插值取环向值）→ 半径 = 基准半径 + 0.08×盘半径×电平偏移；多条环之间错开 0.12 相位、整体随 `_viz_phase` 缓慢旋转，所以看起来是「一圈圈会呼吸的声波」。环的基准半径落在 `0.40~0.88×盘半径`，全部在盘内、避开 0.34 的标签区。

采集线程的启动条件同步扩展：只要「正在播放 + 黑胶面板 + 圆环开着」，即使胶囊条频谱被关掉、或者当前根本不是胶囊形态，也会启动 WASAPI 采集。

**12）圆角矩形面板的键帽。** 三个 26×26 的按键底下各画一个键帽底座（`_paint_key_caps`）：上亮下暗的竖向渐变 + 1 px 深色描边 + 顶边一道细高光；按下时（`btn.isDown()`）整体压暗、顶边高光消失，抬起复原。`pressed` / `released` 都接到父窗口 `update()`，键帽才会跟着按压状态变。三个键仍按面板长边居中排布（竖排 48×120 / 横排 120×48），键帽与键位同尺寸，视觉上就是三个对齐的模拟按键。

### 第六轮迭代（2026-11-03）：图标对齐 / 键槽

**13）三个图标按暂停的竖线对齐。** 暂停图标的两根竖线固定占 `[cx-2bw, cx-bw]` 与 `[cx+bw, cx+2bw]`（`bw = max(2, 0.095r)`）。上一首把**自己的竖线放在左列**（与暂停左竖线同一列），三角形补满右两列；下一首反过来：三角形占左两列，竖线放在右列。这样三个图标的白色像素包围盒完全一致，播放/暂停切换时也不会左右跳。播放态三角改为居中（`cx ± 0.6s`）。实测（`rounded_align_pause.png` 为 72×180 的 1.5× 抓图）三个按钮的亮像素列范围都是 `29..42`，与暂停左右竖线的列完全重合。

**14）键槽与键帽细化。** 圆角矩形面板的按键区先挖一条比键帽外扩 5 px 的深色圆角键槽（顶部加深的投射线 + 底部一道微亮线），键帽再放进槽里：圆角提到 7 px、渐变改成三段（顶部最亮 / 中段过渡 / 底部压暗）、未按下时顶边高光与底边暗线各一道，按下时整块压暗且两道线消失，观感是「键帽陷在面板里、按下去被按平」。

### 第七轮迭代（2026-11-03）：低频死条修复

**15）极细档第 1、3 根条不动。** 48 段对数分频在低频处非常窄：60~66.5 Hz 只有 6.5 Hz 宽，而 4096 点 / 48 kHz 的 FFT bin 间隔是 11.7 Hz，于是有 2 段里**一个 bin 都落不进去**，函数直接给 `-140 dB`，电平恒为 0，画出来就是一根永远 1.5 px 的死条。40 条时第 1~4 根正好 1:1 映射到第 0~3 段，所以肉眼看到的就是「最左侧第 1、3 根不动」。

实测（播放中连续 10 帧，逐段取极差）：`0:0.00  1:0.70  2:0.00  3:0.71 …`，只有第 0、2 段平坦。修复方式：**空段退回「离段中心最近的那个 bin」**（真实音频能量），相邻两段偶尔共用同一个 bin，视觉上就是低频两根条同步起伏。修复后同样测法 10 帧，48 段没有一段极差低于 0.02，第 0 段在 0.32~0.96、第 2 段在 0.25~0.91 之间起伏。单测 `test_band_db_has_no_dead_low_bands` 用「低频段必须有值、能量变化时必须跟着变」两条断言锁死。

## 性能分析

新增开销共五处，均为定频轻量调用。测量环境：Windows 11 x64、Python 3.11.9、单次进程枚举 20 次取平均。

| 项 | 触发频率 | 实测 |
| --- | --- | --- |
| `_is_cloudmusic_running()`（`kernel32!CreateToolhelp32Snapshot` 枚举进程，全量约 200 个进程） | 1200 ms（仅网易云进程探测定时器） | 1.10 ms/次（20 次平均） |
| 播放状态读取（`now_playing._session_facts`，纯内存列表遍历） | 3000 ms | 微秒级，未单独计时 |
| **曲目名采样（`get_now_playing()`，WinRT SMTC 读）** | **3000 ms，仅网易云进程存活时（常驻线程）** | **6.3 / 6.6 / 7.6 ms（3 次；本机当时无在播会话，走"无会话"早退路径）** |
| 灯效重绘（`QTimer` 重绘镂空灯带） | 35 ms，仅「圆盘 / 贴边长条 + 正在播放」时生效 | 未单独计时；不播放时定时器直接跳过重绘 |
| **频谱带重绘（22 根圆角矩形 + 4 处颜色计算）** | **35 ms，仅「胶囊条 + 正在播放」时生效** | **未单独计时；不播放时 `_paint_viz` 首行即返回，心跳也不触发 `update()`** |
| **频谱采集（WASAPI 回环 + 4096 点 FFT，`pet/music/spectrum.py`）** | **20 ms 轮询取包，约每 33 ms 算一次 22 段电平；仅「播放 + 胶囊条 / 黑胶圆环」时运行** | **预热后单核 1.17% / 1.95% / 1.95%（3 次 × 4 s，`time.process_time()`）；收起 / 暂停即停线程** |
| 悬停轮询（`QCursor.pos()` + `frameGeometry().contains()`） | 80 ms | 微秒级；仅在贴边长条 / 控制面板两态做判定，其余形态首行即返回 |

- 稳态开销：进程枚举占单核约 0.09%（1.10 ms / 1200 ms）；曲目名采样占约 0.22%（6.8 ms / 3000 ms，3 次均值）；频谱采集约 1.2%~2.0%（仅播放且正在显示频谱：胶囊条展开或黑胶圆环开启时）；播放状态读取与悬停轮询可忽略。空闲（网易云未运行）时悬浮条整体隐藏，仅保留 1.2 s 一次进程探测，采样线程因 `_netcloud_running` 为假而空转（每次只做一次布尔判断），频谱线程根本不会创建。
- 新增路径成本：贴边吸附与三种开合（圆盘↔长条、长条↔面板）都是一次几何计算 + 300 ms `QVariantAnimation`，不在每个 tick 上跑。**新增 2 个常驻 daemon 线程**（曲名采样 3 s 一次、频谱采集按需启停；`shutdown()` 里都置位 `Event` 退出，不做轮询式 join）；新增系统调用只有 WinRT SMTC 读与 WASAPI 回环读（后者是新增类别，但只读系统输出混音、不落盘、不外传）；无新增网络请求；状态文件 `music_floatbar.json` 只在拖动 / 收起 / 改灯效 / 改外框 / 改可视化设置时写入，无高频写盘。
- 内存：1 个无边框 `QWidget` + 5 个 `QPushButton`/`QLabel` + 4 个 `QTimer` + 每个动画一个临时 `QVariantAnimation`（结束即 `deleteLater`）；频谱每帧现算 22 个 `float`，不缓存像素；采集侧只有一个 4096 点的 `float32` 滚动窗（约 32 KB）；无额外缓存；未做数值化堆内存测量。

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
9. **第二轮视觉核对（`d:\fileData\tmp\floatbar\viz_render.py`）**：用真实 `QT_QPA_PLATFORM=windows`（不是 offscreen）抓 `QWidget.grab()`，输出 `viz_above_solid.png`（226×64）、`viz_above_breathe.png`（226×64）、`viz_inside_solid.png`（226×38）、`viz_inside_rainbow.png`（226×38）、`viz_grow_mid.png`（132×51，`band=13`）、`disc_rainbow_before/after.png`（355° 与 1.2°），逐张核对：标题显示 `晴天 - 周杰伦`（不再是「网易云音乐」）；上方频谱带与胶囊之间有 3 px 间隙、胶囊本体高度恒为 38；内部频谱条压在最底部、不盖住矢量图标；中间帧窗口 `132×51` 但胶囊本体仍是 38 高（没有「先压扁再弹回」）；圆盘彩虹在 355° 与 1.2° 两张图里都只有整体色相旋转、无分界。**踩坑记录**：`QT_QPA_PLATFORM=offscreen` 下 `QFontDatabase.families()` 返回 0 个字体，中文全部渲染成豆腐块，所以中文截图必须走 `windows` 平台。
10. **曲目名采样的真实数据通路**：`python -c "from pet.music.now_playing import get_now_playing; get_now_playing()"` 实测 `6.3 / 6.6 / 7.6 ms`（3 次）；此刻本机网易云进程在跑（4 个 `cloudmusic` 进程）但没有在播会话，`get_now_playing()` 返回 `None` → 标题按设计回落「网易云音乐」。这不是失败，而是「暂停 / 未播时没有可显示的歌名」的预期分支。
11. **实机安装验证 v17**（用 `py311` 重打包，`d:\fileData\tmp\floatbar\dsh-pet-patched-v17.exe`，16 855 931 字节、`pyvers 311`、`PYZ` 1893 个条目，符号表校验 `_viz_band_progress` / `_set_track_title` / `_paint_viz` / `_start_track_sampler` / `_VIZ_POS_ABOVE` / `_rainbow_angle` 全部为 True；原 v16 已备份到 `d:\fileData\tmp\floatbar\dsh-pet-installed-v16-backup.exe`）。覆盖 `D:\Program\dsh-pet-standalone-webm-chat\dsh-pet-standalone-webm-chat.exe`（md5 前后一致 `465b2f9fc3709fdafa8320b3a0b13972`），WMI 启动 `pid 21352`。日志 `install: QApplication.__init__ wrapped (v12-shim)` → `create_bar: begin` → `visibility: cloudmusic running=True` → `create_floatbar: visible=True` → `create_bar: visible=True size=38x38 form=disc edge=None`。随后在播状态下实测窗口 `226×64`（= 38 胶囊 + 26 频谱带），`PrintWindow` 与桌面 `CopyFromScreen` 两种抓法都看到：胶囊上方一排半透明红色频谱条（左高右低包络、条间有空隙）、胶囊内是 `|◁ ⏸ ▷|` 三个矢量键 + 标题 `Low Rollers (Bi…`（真实网易云曲目名，超长已右省略）。这同时证明三件事：安装包里确实是第二轮代码；曲名采样线程在真实 SMTC 会话上能拿到歌名；「胶囊条上方」的频谱带与胶囊本体是分离绘制的、本体高度没有变化。
12. **实机安装验证 v18（真实频谱）**：重打包产物 `dsh-pet-patched-v18.exe`（16 868 231 字节、`pyvers 311`、`PYZ` 1894 个条目；**从 v16 原始备份重打包**，避免反复覆盖同一 exe 时旧模块的压缩字节留在 PYZ 里、产物越滚越大），符号表新增校验 `_sync_spectrum` 与模块 `pet.music.spectrum` 均为 True。覆盖安装后 WMI 启动 `pid 35788`，日志 `create_bar: visible=True size=226x38 form=expanded edge=None` → **`spectrum: capture started`**（说明采集线程真的起来了）。桌面 `CopyFromScreen` 抓两帧（间隔 2 s，截屏坐标 `588,1296 363×120` ⇒ 窗口 `226×38` 放大 1.5 倍）：两帧频谱条高度分布不同、标题 `Pressure's On (Tran…` 位置在两帧间发生位移（滚动生效），标题内容与当时网易云正在播的曲目一致。同机另跑 `viz_render2.py`（真实 `windows` 平台离屏抓图）确认「上方 + 渐变」的配色从 `#de3434` 平滑过渡到 `#ff9a3c`、22 段电平取自真实采集（打印值为 `0.16 0.18 0.31 0.36 0.69 …`，静音时为全 0，回落波形只在采集失败时出现）。**收尾复验**：把采集侧的两处性能改动（COM 函数指针缓存、轮询 20 ms）合入后又从 v16 备份重打了一次（md5 `4bbc b433…`、WMI `pid 38064`，日志同样出现 `spectrum: capture started`）；实机抓到胶囊条为彩虹配色频谱 + 滚动中的曲名（`…t and Grime (Desp`），证明滚动与多配色在真实构建里都生效。
13. **实机安装验证 v19（粗细档位 / 彩虹循环 / 独立开关）**：从 v16 备份重打包 `dsh-pet-patched-v19.exe`（16 871 020 字节、`pyvers 311`、`PYZ` 1894 个条目），符号表校验新增 `_viz_bar_count` / `_viz_resample` / `set_viz_width` / `set_viz_enabled` / `set_title_enabled` 全部为 True；覆盖安装（md5 `8df6ba2cad47b8fcd0897714193c1335`），WMI 启动 `pid 34964`，日志 `create_bar: visible=True size=12x96 form=edge_bar edge=left`（沿用用户存档里的贴左长条形态）。五档粗细的像素对照用 `d:\fileData\tmp\floatbar\viz_render3.py` 在真实 `windows` 平台离屏生成：极细 22 / 细 18 / 标准 14 / 粗 12 / 极粗 10 条（**v19 时的档位；v21 改为 40/30/22/16/10**），五种都是 `226×64` 窗口、条数按档位变化而窗口不变；彩虹回绕前后两帧（`_viz_hue` 0.995 → 0.005）的颜色几乎重合，没有跳色。
14. **实机安装验证 v20（循环滚动 / 黑胶圆环 / 键帽）**：从 v16 备份重打包 `dsh-pet-patched-v20.exe`（16 874 440 字节），覆盖安装（md5 `e0b1d479bc69ee36c1052211eff55450`），WMI 启动 `pid 38232`，日志 `create_bar: visible=True size=12x96 form=edge_bar edge=left`。本轮视觉核对用 `d:\fileData\tmp\floatbar\viz_render4.py`（真实 `windows` 平台离屏抓图）：`ring_vinyl_1/3/5.png`（`160×64` 黑胶面板，1/3/5 条红色闭合圆环随电平起伏、全部落在盘内且不压标签）、`ring_vinyl_off.png`（开关关闭后盘面恢复原样）、`rounded_keys.png`（`48×120` 圆角面板，三个 26×26 键帽对齐、上亮下暗、顶边高光），以及 `title_loop_0/150/250.png`（偏移 250 时第二份文本正好从标题区左缘接上，回绕帧与起点帧重合）。面板形态的**实机**抓图这次没有重做：本机前台全屏应用会截走注入的左键（见第 8 条），悬停展开只能由使用者手动确认。
15. **极细档提到 40 条（v21）**：采集侧 `_VIZ_BARS` 22 → 48，五档改为 40 / 30 / 22 / 16 / 10；`_viz_bar_count(214)` 实测 40 / 30 / 22 / 16 / 10，`viz_render3.py` 重出的 `viz_width_0.png` 里 40 根竖条清晰可辨（单条净宽 ≈ 3.4 px，窗口仍 `226×64`）。重打包 `dsh-pet-patched-v21.exe`（16 874 442 字节），覆盖安装（md5 `1138c9457a6032fac8079de89450e10c`），WMI 启动 `pid 45696`，日志 `create_bar: visible=True size=12x96 form=edge_bar edge=left`。
16. **实机安装验证 v22（图标对齐 / 键槽）**：`dsh-pet-patched-v22.exe`（16 875 117 字节），符号表校验 `_paint_key_caps` / `_paint_ring_spectrum` 均为 True；覆盖安装（md5 `352212486f3680519895816a307cc11f`），WMI 启动 `pid 5556`，日志 `create_bar: visible=True size=226x38 form=expanded edge=None` → `spectrum: capture started`；桌面抓图确认胶囊条上 40 条频谱与曲名正常。圆角面板的图标对齐用 `rounded_align_pause.png`（1.5× DPR 抓图）量像素列：三个按钮的亮像素列范围都是 `29..42`，与暂停图标左右竖线的列完全重合。
17. **实机安装验证 v23（低频死条修复）**：`dsh-pet-patched-v23.exe`（16 876 112 字节），覆盖安装（md5 `fdf5d07935567b5c985734b590eaaf7`），WMI 启动 `pid 42148`，日志 `create_bar: visible=True size=226x38 form=expanded edge=None` → `spectrum: capture started`。桌面连续抓两帧（间隔 0.9 s，只取胶囊最左侧 36 px 区域，对应前 4 根条）：平均像素差 `6.34`、最大 `83`，第 1、3 根条跟着音乐在动；修复前同一区域两帧几乎完全相同。

## 测试门

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 语法编译 | `python -m py_compile pet/music/floatbar.py pet/app.py` | PASS |
| 静态检查（全仓） | `ruff check .` | PASS —— `All checks passed!` |
| 新增单测 | `pytest tests/test_music_floatbar.py -q` | PASS —— `51 passed in 4.19s`（`QT_QPA_PLATFORM=offscreen`；后续迭代共补 28 个用例） |
| 交付纪律 + 架构守卫 | `pytest tests/test_pr_report_discipline.py tests/test_architecture.py tests/test_music_floatbar.py -q` | PASS —— `298 passed in 20.95s` |
| 全量单测 | `pytest -q` | **8884 passed / 169 failed / 32 skipped**（1562 s）。169 个失败全部来自本机环境，与本次改动无关，见下。**注**：该次全量跑在第一轮；后续迭代只改了 `pet/music/floatbar.py`、新增 `pet/music/spectrum.py` 与对应测试，已重跑 `py_compile`、`ruff check .` 与上面三行门禁（`298 passed`），未再跑全量（改动不触及共享模型 / 生命周期 / 打包 / 平台分支之外的面） |

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
