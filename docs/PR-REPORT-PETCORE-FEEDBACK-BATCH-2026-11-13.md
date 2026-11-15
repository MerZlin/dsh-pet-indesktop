# PR 报告：petcore 反馈批次 4（朝向 / 岛按钮 / 气泡风格 / 岛全屏 / 拖动倾斜 / 静息成本）

- 日期：2026-11-13
- 分支：`feat/petcore-vpet-architecture`
- 范围：`petcore/`（唯一随包入口）+ 对应测试、`scripts/bench_petcore_idle_cpu.py`、文档
- 不在范围：`pet/` 旧壳（迁移期参照层）；交互按钮式气泡；快速对话窗跟随气泡风格

## 一、六项需求与实现

| # | 需求 | 实现要点 | 主要落点 |
|---|---|---|---|
| 1 | 自定义角色动画左右反了 | 新增每宠 `anim_facing_default` + `anim_facing_groups`；`_mirrored()` 改为「`facing` 与本组素材原生朝向不同才镜像」，`text_clips` 仍永不镜像；只改渲染，不改移动/转向数学 | `petcore/ui/pet_window.py`、`petcore/config.py`、`petcore/ui/settings/pages_core.py` |
| 2 | 岛卡片显示/隐藏桌宠按钮 | 卡片第 4 个按钮 + 岛级 `toggle_pet_requested`；文案随 `set_pet_visible()` 变化；宿主接 `_toggle_pet_visible`；顺带修掉 `island_click_action="toggle_pet"` 只发信号没人接的死开关 | `petcore/ui/island.py`、`petcore/ui/app.py`、设置页 |
| 3 | 把原分支气泡类型加回来 | 五条预设数据搬回 `contracts/bubble_styles.py`，Qt 绘制层 `ui/bubble_style.py`：配色/圆角/三层柔和阴影/朝向角色的三角尾巴/`breath_bubble` 有机轮廓；尾巴尖沿边投影到角色中心并钳进圆角（长气泡靠边不再指向气泡中点）；`breath_bubble` 左窄右宽的留白让文字靠左；文本气泡改为自绘表面（QSS 透明、不双画）；`self_talk_bubble_style`（每宠，默认 classic_top）；旧壳同名键补进迁移别名 | `petcore/contracts/bubble_styles.py`（新）、`petcore/ui/bubble_style.py`（新）、`petcore/ui/bubble.py`、`fun.self-talk` 设置页 |
| 4 | 灵动岛可勾选全屏隐藏 | 探测器仍只有一个：`set_enabled(auto or island)` 决定起表、`set_hide_pets(auto)` 单独门控宠物；翻转发 `fullscreen_changed(bool)`，宿主只恢复「自己藏的那次」；全屏期间 `apply_island_settings()` 不把岛拉回画面；默认不勾 | `petcore/ui/fullscreen_hide.py`、`petcore/ui/app.py`、设置页 |
| 5 | 拖动时被牵引的旋转 | `MotionController` 按最近 ~120ms 水平拖速给目标角（±7°），弹簧 k=120/c=10；**方向与拖动方向一致**（往右拖顺时针，实机反馈反向后修正）；松手 0.6s 回零并停表；甩飞立即清零。旋转收敛到 `_rotation_angle()` 单一出处（探头 + 特效 + 倾斜相加），paint/命中/input_revision 共用 | `petcore/ui/motion.py`、`petcore/ui/pet_window.py`、`drag_lean_enabled` + 设置行 |
| 6 | 旧版 CPU 30% 反馈是否仍在 | 见「二、诊断」：重写版**没有**线程爆炸与 T1 分层锁死，但有同构缺陷——探头 PEEKING 静息态把闲置降帧永久挡掉（实测单核 40.6–44.9%，56s 无衰减）。修门控 + ClipLibrary 16 段 LRU + 成本统计 + 基准脚本 `--left-edge` | `petcore/ui/pet_window.py`、`petcore/media/library.py`、`scripts/bench_petcore_idle_cpu.py` |

## 二、诊断：旧版 CPU 反馈在重写版的对应物

现场（本机，安装版 `E:\dsh-pet-core\dsh-pet-core.exe` 0.5.0，PID 17584，`playback_speed=2.0`、岛启用、单宠）：

- **单核 CPU 40.6–44.9%**（7 个 8s 窗口，56s，无衰减）；任务管理器口径约 2.5%（16 逻辑核）。
- **线程 25–31、句柄 ~2260**，无 ffmpeg 子进程（跑的是帧序列）→ 旧版「线程 52→795、1737 线程/11769 句柄」的形态在重写版**不存在**。
- **窗口 `GetWindowRect = (-292,220,640×360)`**；`probe_target_x("left", 0.55, rotated_body, bounds)` 用 shenshen 的 `body_box=[212,60,428,330]` 算得 **-292.2** → 该宠正停在「贴左缘 45° 探头 PEEKING」（`EDGE_PEEK_EXPOSURE=0.55`）。
- 根因：`_idle_low_fps_allowed()` 把 `_effects_lock_active()`（含 `probe_active`）列为满速条件。探头只锁**动画选择**，PEEKING 却是**静息态**——于是 30 秒闲置降帧永远不触发，48fps 解码一直跑。旧版「T1 退不出」的分层在 petcore 不存在（没有 tier governor），但「静息态永久满速」这个状态机缺陷同构。

## 三、性能分析（实测数字）

> 环境：Windows 11，16 逻辑核；同一台机器、同一份 `config.json` 起点（`playback_speed=2.0`、`island_enabled=true`）；数据根 `.scratch/cpu-*`（不碰用户配置）。
> 命令：`python scripts/bench_petcore_idle_cpu.py --mode idle --seconds 60 --set playback_speed=2.0 --set island_enabled=true [--left-edge]`
> 口径：脚本按「进程树 CPU 秒 / 墙钟 / 逻辑核数」输出总 CPU%，同时给出单核%。

**A/B（同机同配置，安装版 0.5.0 vs 本分支构建）**：同一数据根配置（`playback_speed=2.0`、`island_enabled=true`、`scale=1.0`、宠物停在左缘自动进入 45° 探头），启动后固定预热 120s（让首跑帧序列供给跑完，不计入静息），再用 psutil 采样进程树 60s。

| 场景 | 改前（0.5.0 安装版） | 改后（本分支构建） | 变化 |
|---|---|---|---|
| 贴左缘探头静息（单核 %，60s） | **12.2%** | **6.4%** | **−47.5%** |
| 线程数（静息） | 17 | 10 | −41% |
| 同进程总 CPU %（16 核口径） | 0.76% | 0.40% | −47% |

**现场（用户真实配置，长时间运行）**：2026-11-13 16:44 对安装版 PID 17584 采样 56s，单核 40.6–44.9%（7 个 8s 窗口，无衰减）、线程 25–31、句柄 ~2260；窗口 `x=-292` 与 `probe_target_x("left", 0.55, …)` 完全吻合，即静息态被探头挡住降帧。该实例同时开着自言自语（配图目录 433 张）与岛，绝对值和 A/B 场景不可直接相减；因果关系以 A/B 为准。

**口径说明**：`bench_petcore_idle_cpu.py` 每次新起进程，会在 60s 空闲后启动首跑帧序列供给（`converted=2–3, elapsed≈60–81s`），把供给 CPU 算进静息会得到 60%+ 的假高值；正式数字用上面的「固定预热 120s + 同进程采样」脚本（`.scratch/verify/measure_idle.py`，未入库）。未测：真实全屏游戏/视频下的岛隐藏；macOS/Linux 的同一 A/B。

新增路径成本：

- `_mirrored()` 每次绘制/10ms 穿透轮询各读 1–2 次配置字典（O(1)），无系统调用、无分配。
- `_rotation_angle()` 只做 3 次浮点加法；`input_revision()` 每拍 1 次 `round()`。
- 拖动倾斜：只在拖拽期间使用已有的 16ms `MotionController` 定时器；松手后最多再跑 0.6s，静止时零帧、零定时器。
- 岛卡片按钮：一个 `QPushButton` + 一次 `setVisible`，无新增绘制循环。
- 气泡风格：QSS 只在切换风格/字号时重建；`paintEvent` 每帧多画一个路径 + 3 条描边（文本气泡，通常 ≤ 360×200px），无新增 pixmap 分配。
- `ClipLibrary` 淘汰：每新建 1 个 clip 最多触发 1 次 `close()`；稳态无额外调用。
- 成本统计：默认 `PET_PETCORE_COST_STATS` 未设置时所有分支短路，零计数器增量。

## 四、实机运行记录（本机 Windows，源码 `python -m petcore`，独立数据根）

- **岛卡片按钮**：胶囊 → 展开卡片（截图：`隐藏桌宠 / 刷新余额 / 设置 / 清除消息` 一行排列，340–420px 内不换行）；真实鼠标点「隐藏桌宠」→ 按钮变「显示桌宠」、状态点由实心变空心、宠物窗口 `IsWindowVisible=false`；再点一次恢复。三点均截图。
- **气泡**：真实桌面上自言自语气泡（经典暖黄、#fffaf0 底、#efc261 描边、朝下尾巴、三层柔和阴影），中文正常渲染；长文本在靠屏右缘时尾巴指向角色中心（离屏渲染对照图 `long_bubble.png`）。五种预设离屏渲染对照图确认配色/圆角/水泡轮廓。
- **边缘探头**：把宠物停在左缘（`pos_x=-212`）启动，窗口落在 `-292,300`（= `probe_target_x("left", 0.55, …)`），截图确认 45° 只露头——探头功能正常（用户看到「探头不见」的那次是本报告验证用的临时实例把 `edge_probe_enabled` 关掉了，不是产品缺陷）。
- **拖动**：真实鼠标拖动宠物 80px，窗口跟随、拖拽动画与落回待机正常；倾斜方向已按实机反馈改为跟随拖动方向（见 §三 备注）。
- **全屏隐藏灵动岛**：本机没有跑真实全屏游戏/视频，**未做**人工全屏验收；由 `tests/test_petcore_island_fullscreen.py` 的 6 例覆盖（探测器信号、只恢复自己藏的那次、全屏期间设置页重放不拉回岛、只勾岛也起表）。

## 五、修改文件说明（逐文件 + `git diff --numstat` 口径）

见提交说明；新增文件行数：`petcore/contracts/bubble_styles.py` 86、`petcore/ui/bubble_style.py` 257、
`tests/test_petcore_anim_facing.py` 214、`tests/test_petcore_bubble_styles.py` 190、
`tests/test_petcore_clip_cache.py` 75、`tests/test_petcore_drag_lean.py` 242、
`tests/test_petcore_island_card_actions.py` 77、`tests/test_petcore_island_fullscreen.py` 193。

## 六、未验证项与风险

- 触摸/拖动倾斜的「方向感」是主观项：方向常量集中在 `motion._lean_target()`，实机截图确认后如需反向只改一处。
- 气泡尾巴在极窄屏/极端锚点位置会退化为「无尾巴」（`tail_side_for → none`），不画错方向。
- 图片气泡保留自己的圆角面板（预设只给颜色），与文本气泡的整块表面不完全一致——已知边界，不阻塞。
- 非 Windows 平台：全屏探测仍不支持（开关置灰）；气泡自绘与倾斜在 POSIX 上未实机验收。
