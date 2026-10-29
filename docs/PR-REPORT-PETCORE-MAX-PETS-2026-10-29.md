# PR 报告：桌宠身份上限 16 → 99 + 碰撞粗筛（2026-10-29）

用户要求「子桌宠生成上限调为无上限，或者 99 只」。取 99，理由见下；同时把碰撞拍
（n² 配对）先降到能守住 16ms 的量，否则放宽上限等于把卡顿交给用户。

## 修改文件说明

`git diff --numstat`：

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `petcore/config.py` | +7/−2 | 新增 `MAX_PETS = 99`（含主宠 slot 0 ⇒ 身份 0..98）作为单一真值；`_pets()`（`active_pets` 归一化）与旧 `config-pet-N.json` 迁移的 slot 校验从硬编码 `16` 改为读它——以前两处各写一个 16，改一处会出现「配置接受 slot 98、管理器只认 15」。 |
| `petcore/ui/pet_manager.py` | +36/−3 | 删掉本地 `MAX_PETS = 16`，从 `config` 导入同一常量；上限气泡改 f-string；新增 `_rect_cache` 与 `_body_rect()`（按拍缓存身体矩形，`_apply_body` 移动即时失效），`_collide_pair` 先用 `rects_may_touch()` 粗筛，只有可能接触的配对才构造 `CollisionBody`。 |
| `petcore/engine/collision.py` | +36 | 新增 `rects_may_touch(rect_a, rect_b, margin=1.0)`（轴对齐粗筛）并在 `collide()` 入口调用：内切三圆链只在矩形内/相切处相交 ⇒ 矩形分离必然 `None`，判定结果不变、省掉两次圆链构造与 9 对圆心距离。 |
| `petcore/ui/settings/page_pets.py` | +4/−1 | 「当前 N 只（最多 16 只）」改读 `MAX_PETS`。 |
| `petcore/AGENTS.md` | +30 | 新增「多宠上限与粗筛」条：单一常量、上限不是免费的、粗筛必须不改变判定结果。 |
| `tests/test_petcore_multipet.py` | +17/−3 | 上限用例改为注入 `MAX_PETS=4` 验边界（不开 99 个窗口），并断言 `active_pets` 归一化接受 98、丢弃 99/128/-1。 |
| `tests/test_petcore_p5.py` | +27 | `rects_may_touch` 的相交/相切/分离/退化矩形；`collide()` 远距返回 None；重叠仍报深度与法线。 |
| `docs/PETCORE-LOG.md` / `PETCORE-LOG-INDEX.md` | +45 / +1 | L126 与索引行。 |
| `docs/PETCORE-SPEC.md`、`PETCORE-FUNCTION-MATRIX-2026-10-22.md`、`PETCORE-MAIN-PARITY-BACKLOG.md` | +3/−2、+2/−1、+2/−2 | 把「最多 16 只」的三处旧口径改成 `MAX_PETS = 99`（含主宠）。 |

## 性能分析

本机（Windows, Python 3.12, 空闲态），命令与样本量写在各行；99 只 = `99×98/2 = 4851` 对/拍。

| 路径 | 实测 | 说明 |
|---|---|---|
| 旧热路径（每对） | 2×`_body()` **1.5 µs** + `collide()` **2.85 µs** ≈ **5.85 µs** | 1.5 µs 是 `body_rect()`（Qt 窗口几何）+ `motion.velocity()` + dataclass 构造的实测值。 |
| 99 只旧总成本 | **28.4 ms/拍** | 4851 × 5.85 µs；16ms 计时器直接超预算 ⇒ 掉帧。 |
| `rects_may_touch()` | **0.21 µs/对**（2,000,000 次） | 4 次区间比较；不再 `float()` 转换（调用方给的就是数值矩形）。 |
| 新热路径（每对） | 两次缓存查表 + 粗筛 = **0.25 µs**（2,000,000 次） | ⇒ 4851 对 = **1.2 ms/拍**，约为旧路径的 1/24。 |
| `collide()` 远距对 | 2.85 µs → **0.96 µs** | 粗筛放进 `collide()` 后，其它调用方也受益（返回 `None` 早退）。 |

稳态与新增触发频率：

- 碰撞拍仍是 16ms 一次，不新增定时器/线程/系统调用/磁盘/网络。
- `_rect_cache` 每拍 `clear()` 一次（O(已登记窗口数)），`_apply_body` 真的移动窗口时才
  `pop` 一项；稳态内存与窗口数同阶（99 项 ≈ 99 个四元组）。
- 粗筛只对**分离**的配对生效；相邻/重叠的配对照旧跑完整圆链数学，判定与分离结果不变。
- 上限放宽本身不产生任何稳态开销（多出来的宠物才有开销，且每只的窗口/解码开销远大于碰撞）。

## 实机运行记录

真机 = 本机 Windows（源码树 venv，非 CI、非 mock）。

1. 测试：`QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_multipet.py
   tests/test_petcore_p5.py tests/test_petcore_pet_push.py tests/test_petcore_click_listeners.py
   tests/test_petcore_config_extra.py -q` → 全绿；`pytest tests/test_petcore_*.py
   tests/test_architecture.py -q` → **2372 passed, 1 skipped in 231.55s**（连跑两遍一致）。
   首跑时套件曾在 `test_petcore_click_listeners.py` 出现 Windows access violation——
   那是「多个播报线程并发首次 import edge-tts」的既有隐患，本次一并收口（L127：
   `_EDGE_IMPORT_LOCK` + 会话级合成桩），之后两跑不再复现。
2. 静态检查：`.venv/Scripts/ruff check pet/ tests/ petcore/ examples/` → All checks passed。
3. 构建门：`.venv/Scripts/python -m petcore --check` → 通过（`strict.problems` 为空）。
4. 基准：见「性能分析」两行 2,000,000 次的实测。

## 未覆盖 / 风险

- **没有真机开满 99 只**：99 个真实窗口（每只一个动画播放器 + 配置）的内存与解码开销远大于
  碰撞本身，本刀只保证「上限放行 + 碰撞拍不炸」；满编时的实际帧率未测，不能当成已验证。
- 「已达上限」提示仍只有两处（生成失败的气泡、设置 →「多宠」状态行）；托盘与右键菜单沿用
  静默返回。
- `MAX_PETS` 的含义是**含主宠**的总数（slot 0..98），`active_pets` 仍是从 0 开始的列表。
