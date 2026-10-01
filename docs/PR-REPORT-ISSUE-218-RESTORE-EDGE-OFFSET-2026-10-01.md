# PR 报告：restore 走虚拟位置语义重建绘制偏移（issue #218）

> **基线**：`2786c15`（Merge PR #190 codex/plugin-dlc-v5）
> **分支**：`fix/issue-218-restore-edge-draw-offset`　**日期**：`2026-10-01`
> **范围**：2 个文件（实现 1、测试 1、文档 0，另有本报告与 INDEX 登记两份文档）
> **关联**：[issue #218](https://github.com/MerZlin/dsh-pet-indesktop/issues/218)；来源为 B 站 v4.2.1 发布视频评论区用户实测反馈（[BV1Zph86oELe](https://www.bilibili.com/video/BV1Zph86oELe)）

## 一、核心特性

贴右/下屏幕边缘摆放的桌宠重启后不再向屏幕内侧偏移。`save_position` 保存的是**虚拟窗口中心**（实际位置 + 绘制偏移，即角色"看起来所在"的位置），而 `restore_position` 反解坐标后先做**窗口级钳位**再落窗——贴边时反解出的虚拟坐标超出 `avail.right() - _w` 被整段钳回，绘制偏移在恢复时丢失，角色相对屏幕边缘内移一个透明边距（项目注释里实测过的右侧 178px 残留一类）。B 站用户原话："我平时放在贴右下角的位置，它记录不到就会启动的时候出现在偏左的地方"。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 贴边位置还原 | 恢复改为把虚拟坐标直接交给统一出口 `_move_window_towards`，由它按身体框/工作区钳位并**重建绘制偏移**，角色重启后保持贴边视觉位置 |
| 2 | y 轴反解对齐保存公式 | 恢复侧 y 由 `_h // 2` 改为 `(h + _capture_headroom) / 2`，与保存侧 `(h + headroom) / 2` 对称（headroom ≠ 0 时旧反解有 headroom/2 的系统偏差） |
| 3 | 轻量桩回退保留 | 无 `_move_window_towards` 的轻量桩（测试桩等）回退旧的窗口级钳位 + 直接 `move()`，桩窗口不会被移出屏幕 |

**红线 / 不变量**：无 `body_box` 的角色包（身体框回退全窗口）虚拟位置恒等于窗口位置，恢复结果与改造前逐位一致；非贴边摆放的旧配置逐位一致；`_awaiting_saved_screen` 等副屏等待/重试链路不触碰。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/window_placement.py` | +15 / −4 | `restore_position`：① 有 `rx/ry` 记录时不再对反解出的 x/y 做窗口级 `min/max` 钳位，保留虚拟位置语义交给统一出口（其内部完成身体框钳位 + 窗口钳位 + 绘制偏移重建）；② y 轴反解减数改为 `(host._h + headroom) // 2` 对齐保存公式（`headroom` 取 `getattr(host, "_capture_headroom", 0)`，缺省 0 时与旧式逐位一致）；③ 落窗分支按"有没有统一出口"分流——有则 `_move_towards`（虚拟坐标请求对统一出口恒合法），无则回退旧钳位 + `host.move()`，保护轻量桩；④ 补注释说明坐标语义约束与 issue 出处 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_window_position.py` | +113 / −0 | ① `test_restore_preserves_edge_dock_draw_offset`：桩级回归——身体框声明 `body_box=(60,80,200,240)`、headroom=40 的合法贴边态（虚拟 (1720,770)、窗口 (1700,770)、偏移 (20,0)）保存后重恢复，断言窗口位置、绘制偏移、虚拟位置三者全部还原（**该测试在改造前代码上实测失败**，见第五节）；② `test_real_window_restore_keeps_edge_dock_draw_offset`：offscreen 真实 `PetWindow` + 真实 `Config` 全链路——贴边落窗 → 保存 → 新窗恢复，断言窗口位置/偏移/虚拟位置还原，屏幕几何取 offscreen 平台实测值不硬编码；③ 补本地 `app` fixture（QApplication 实例复用，与 `test_single_process_spawn.py` 同式） |

### 未改动（看起来相关但故意没动）

- `save_position`：保存侧语义（虚拟窗口中心比例）本来就是对的，问题只在恢复侧解释；
- `_default_corner_pos` / `go_default_corner`：默认角落落位走身体框语义，不受影响；
- `move_window_towards` 本体：其虚拟位置语义与钳位管线保持原样，本修复只是把恢复路径接回它。

## 三、实现要点

保存与恢复的坐标语义必须对称，这是本修复的全部依据：

1. **保存**（`save_position`）以 `virtual_pos = 实际位置 + _draw_delta` 为基准存比例——贴边时窗口被钳在工作区内、身体框贴住屏幕边缘，虚拟位置才是角色的自然位置；
2. **恢复**旧实现把比例反解回窗口矩形坐标再 `min/max` 钳位——等价于把虚拟坐标当窗口坐标用，`_draw_delta` 的信息量被整段丢弃；
3. 统一出口 `move_window_towards` 的请求语义本来就是虚拟坐标（各移动路径共用），且自会完成"身体框钳进工作区 → 窗口钳进工作区 → 差值即绘制偏移"的重建。恢复路径只要**不做多余钳位、原样透传**，贴边语义自动还原；
4. 备选方案（恢复时单独存 `_draw_delta` 并在恢复后手工重放）被否：多存一份状态引入新的兼容/迁移面，且与统一出口的既有职责重复；
5. 轻量桩无统一出口时虚拟坐标不保证合法，保留旧钳位作为回退——真实 `PetWindow` 恒走统一出口，不受影响。

## 四、性能分析

**方法（可复现）**：`QT_QPA_PLATFORM=offscreen python bench_restore.py`（`restore_position` 循环 N=20000 次取均值，含 `move_window_towards` 全钳位管线；脚本随本报告附于 PR 描述）　环境：Windows 10.0.26200 x64 / Python 3.13.15 / PySide6 offscreen

| 指标 | 实测 | 归属 |
|---|---|---|
| `restore_position` 单次调用 | 5.15 µs（N=20000，均值） | 新增路径即恢复路径本身 |
| 恢复路径执行频率 | 每窗启动一次 + 副屏上线重试/幻影屏兜底等个别分支 | 冷路径，不在任何 tick/事件循环内 |
| ruff / 全量 pytest | ruff 全过；全量 2956 passed / 11 skipped / 180s（2 个失败为 main 既有环境项，与本次无关，见第五节） | 回归面 |

**结论**：① 稳态开销零变化——改动只在启动/恢复冷路径上，待机、拖拽、抛掷、渲染等热路径一行未动；② 新增路径成本为每次恢复多一次 `getattr`（`_capture_headroom`）与一次 `callable(getattr(...))`，单次恢复全程 ~5 µs 量级，每进程生命周期仅数次；③ 无新增系统调用/网络/磁盘/线程（`move` 与 `_move_window_towards` 均为既有出口，钳位从恢复侧挪到出口内是既有逻辑，不新增执行）；④ 无内存增长（不新增常驻对象，`_draw_delta` 为既有字段）。

## 五、实机运行记录

本机真实环境（Windows 10.0.26200 x64，Python 3.13.15，PySide6 offscreen 平台）：

1. **问题在旧代码上可复现（桩级）**：`git stash push pet/window_placement.py` 后运行新测试：
   `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_window_position.py::test_restore_preserves_edge_dock_draw_offset`
   → `FAILED ... AssertionError: assert (1700, 770) == (1700, 810)`（窗口被钳位、偏移丢失，正是 issue 描述的内移现象）；恢复改动后同测试 `PASSED`。
2. **本文件全量**：`QT_QPA_PLATFORM=offscreen python -m pytest tests/test_window_position.py -v` → `10 passed in 0.79s`（含真实窗口集成测试）。
3. **受影响测试族高负载复跑 3 遍**（`tests/test_window_position.py` + `tests/test_single_process_spawn.py`，涉及位置恢复/标记/多开链路）：
   `49 passed in 6.30s` / `49 passed in 5.48s` / `49 passed in 5.58s`，三遍全绿无时序抖动。
4. **ruff**：`python -m ruff check pet/ tests/` → `All checks passed!`。
5. **全量回归**：`QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q` → `2 failed, 2956 passed, 11 skipped in 180.39s`。两个失败
   （`test_desktop_pet_features.py::test_image_directory_picker_opens_right_drawer_with_three_column_masonry`、
   `test_proactive.py::TestVisionAndWatcherPhase2::test_capture_window_rect_coordinates_and_clamping`）
   已在**未含本改动的 `main`（2786c15）上逐个复跑确认同样失败**，属本机环境既有项，与本修复无关。
6. **无法自动覆盖的说明**：真实桌面上的"拖到右下角 → 退出 → 重启看位置"含人工拖拽与可见性目测，offscreen 会话没有交互桌面与多显示器，无法在本环境自动执行；替代证据为第 1、2 条——桩级与真实 `PetWindow` 级别都在同一套 `save_position`/`restore_position`/`move_window_towards` 生产代码路径上断言了贴边偏移的保存与重建，且真实窗口级断言使用 offscreen 平台实测屏幕几何。合入后建议在真机（有 `body_box` 的角色包 + 贴右下角摆放）做一次重启目测。
