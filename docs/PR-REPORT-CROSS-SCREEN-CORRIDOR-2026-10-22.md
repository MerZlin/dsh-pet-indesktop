# PR 报告：跨屏「过缝通行 + 落点吸附」与侧墙低速截止（2026-10-22）

> 分支 `fix/440-acceptance-round`（cd908b7 + 6f7c30b + 3d17616）。触发：4.4.0 候选
> 实机验收的两条反馈——① 主屏（2560×1440 右）→ 副屏（1920×1080 左）在底部带
> 像有墙过不去，低速时在屏幕边黏住一下又反弹；② 聊天窗内存（另 PR 处理）。

## 一、修改文件说明

| 文件 | 增删 | 改了什么 + 为什么 |
|---|---|---|
| `pet/physics.py` | +14/−3 | 侧墙加 `WALL_BOUNCE_STOP_VX=60`：低速撞墙 vx 归零沿墙滑落，不再「贴墙一帧黏住 → RESTITUTION 弹回」。地板早有 `GROUND_BOUNCE_STOP_VY=120` 同款截止，侧墙漏了十年——用户实机看到的「黏住反弹」就是它 |
| `pet/sprite_physics.py` | +26/−24 | `_effective_bounds`：band_bounds → **并集外接矩形**（过缝通行语义）；落地收口（is_at_rest）鸭子类型调用吸附；吸附实现唯一来源化（盲审 A1） |
| `pet/pet_sprite.py` | +31/−2 | `set_pos`：拖拽/抛掷期间多矩形域放宽为并集外接（含空洞）；`_snap_into_screen_if_needed`（吸附唯一实现）；`on_release` 低速分支吸附；构造期 getattr 兜底（`__init__` 的 set_pos 先于字段初始化，实测 AttributeError） |
| `pet/window_placement.py` | +44/−4 | 新增 `snap_to_nearest_screen` 纯函数：覆盖判定（QRegion，盲审 A2 后从「中心在屏内」改为「身体被并集完整覆盖」）+ 最近屏最近边缘落点（身体比屏大有钳回） |
| `pet/sprite_collision.py` | +6/−0 | `_settle_supported`（落岛落定，THROWN→NORMAL 第三出口）补鸭子类型吸附（盲审 S1：岛停靠在空洞上方时可把宠钉死在空洞） |
| `pet/sprite_slingshot.py` | +5/−0 | `cancel(resume_drag=False)` 强制 NORMAL 前先吸附（盲审 S2：锚点在空洞时 drop_gravity 兜底被覆盖） |
| `tests/test_cross_screen_corridor.py` | +246（新） | 用户布局建模 ×10：过缝穿墙、落地吸附、拖拽过缝+松手吸附、屏内松手不吸附、纯函数三布局、单屏逐位不变、半身悬空吸附、落岛吸附、弹弓取消吸附时序、弹簧档松手吸附 |
| `tests/test_sprite_physics.py` | +18/−7 | 错位拼接旧墙测试改写为新语义（旧断言=band 挡墙，新断言=穿缝+落地在屏）；`test_wall_low_speed_slides_instead_of_sticky_bounce`（先红后绿实跑核实） |
| `tests/test_physics.py` | +20 | 侧墙截止回归（先红后绿实跑核实） |

**语义定稿**（spec 在 `.scratch/cross-screen-corridor/SPEC.md`，.scratch 为本地不入库约定）：
通行域 = 各屏并集外接矩形（含空洞——看不见是过渡态，不是非法态）；静止域 = 各屏本身；
松手/落地/落岛/弹弓取消四个收口点，身体不被并集覆盖 → 吸附到最近屏。
**漫游不改**（目标点只在屏内，永不隐形）；**legacy/capture 路径不动**（`move_window_towards`
继续用 band_bounds，登记为已知差异）。单屏/未开跨屏：并集恒等单矩形，行为逐位不变。

## 二、性能分析

- **稳态开销**：零新增周期工作。`set_pos` 每次多一个 `len(_clamp_rects)>1` 短路判断
  （单屏恒 False）；吸附只在四个收口点各调一次（QRegion 构造 ≤2 矩形，微秒级）。
- **新增路径成本**：`snap_to_nearest_screen` 每收口一次 ≤4 次矩形运算 + 一次 QRegion
  构造（两矩形），实测 10 条用例含数百次吸附断言总耗时 <0.1s。
- **无新增系统调用/网络/磁盘/线程**；无内存增长（吸附不分配持久对象，QRegion 栈上即弃）。
- 抛掷积分路径（throw_step 子步循环）指令数逐位不变；侧墙分支多一次 abs 比较。

## 三、实机运行记录

- **先红后绿核实**：`test_wall_low_speed_slides_instead_of_sticky_bounce` 放 base（607cfff）
  实跑 FAILED（行为性红）；错位拼接改写测试在 base 实跑 FAILED（`pos.x()=1137.6 < 1920`
  被旧墙挡回——精确复现旧语义）；分支全绿（k3 盲审实跑复核，非引用）。
- **全量门禁**：`pytest tests/ -q`（offscreen，本机）**8874 passed / 16 skipped / 0 failed**
  （1985s，6f7c30b）；盲审修复（3d17616）后四个物理族 139 passed。
- **盲审两轮**（k3，互不知结论）：第一轮「修复 2 应修后可合并」（S1 落岛、S2 弹弓取消
  两个吸附漏口 + A1 去重 + A2 覆盖判定）；修复后第二轮复审（本报告提交前结论以复审
  记录为准）。
- **真实场景还原**：用户布局（右 2560×1440 + 左 1920×1080 顶对齐）建模进
  `test_cross_screen_corridor.py`——主屏底部带左抛穿缝、落空洞吸附副屏右下，与实机
  现象一一对应。
- **无法自动验证项**：过缝瞬间的「看不见过渡态」观感（隐形时长 = 穿缝时间）与吸附
  落点手感，需实机确认；弹弓手感阈值 WALL_BOUNCE_STOP_VX=60 的体感（盲审 A5 评估为
  合理：只影响收敛尾段 88px/s 以下的撞墙），需实机确认。

## 四、登记

- INDEX：`docs/INDEX.md`「PR 报告存档」已登记（随本 PR 同提交）。
- 后续：4.4.1 评估「吸附改滑动动画」（当前为一次到位）与 legacy 路径语义对齐。
