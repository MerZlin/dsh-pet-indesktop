# PR 报告：测试在「真实桌面」上的 9 个失败归因与修复（2026-09-29）

> **基线**：`3611672`（天气 + 免打扰/汇总三批已落地）
> **分支**：`main`（本地改造分支，未推送）　**日期**：2026-09-29
> **范围**：测试 4 个文件（**产品代码 0 改动**）
> **关联**：用户在本机跑的完整报告（`9 failed, 3016 passed`）；相关档案
> [`PR-REPORT-WEATHER-2026-09-29.md`](PR-REPORT-WEATHER-2026-09-29.md) 第九节的环境归因

## 一、核心特性

用户在本机（真实桌面、能写 `%APPDATA%`）跑全量得到 **9 failed / 3016 passed**。
逐条查完：**全部是测试自身的老假设与一处线程竞态，不是产品缺陷**。修完之后
这 9 个在同样条件下转绿，产品行为一位没变。

| # | 失败用例 | 根因 | 修法 |
|---|---|---|---|
| 1–6 | `tests/test_drag_move_coalescing.py`（6 条） | 假屏幕 `1920×1200` **放不下**测试目标：角色身体框实测 309×259，目标 (1874,1088)/(1974,1128) 会被正常贴边钳到 (1611,941) | 假屏放大到 `4096×3072`；另加 autouse fixture 关掉 #186 的「多屏活动区域」快照（它走 `QGuiApplication.screens()`，会绕过假屏把窗口钳到真实桌面） |
| 7 | `tests/test_pet_interaction_locks.py::test_shift_drag_requires_shift` | 同上（同一套假屏 + 钳位） | 同上 |
| 8 | `tests/test_desktop_pet_features.py::test_image_directory_picker_…` | 生成的图片名是 `"02-" + "very-long-image-name-" * 8`，加上 `tmp_path` 已有 ~90 字符后**超过 Windows MAX_PATH**，`Image.save()` 直接 `FileNotFoundError` | 重复次数 8 → 3（仍是"长名字"，够验证截断与 tooltip，不再超长） |
| 9 | `tests/test_session_end_ffmpeg_guard.py::test_control_group_spawns_normally_without_session_end` | reader 是 `clip.start()` 之后**异步**跑起来的，紧接着断言 `spy.read_frames_calls` 会抢跑（另一台机器上偶发红） | 加有界等待 `_wait_until()`（同 `tests/test_agent_link_threads.py` 惯例），并补 `import time` |

**红线 / 不变量**：产品代码（`pet/**`）**一行未改**；`_BigScreen` 的"无边界"语义
保持不变（只是放大到真能装下被测目标）；多屏钳位语义仍由
`tests/test_edge_reachability.py` 专门锁定。

## 二、修改文件说明

### 测试

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `tests/test_drag_move_coalescing.py` | +26 / −5 | ① `_BigScreen` 1920×1200 → 4096×3072，并把"为什么必须这么大"（身体框 309×259、实测钳位值 1611,941）写进类 docstring；② 新增 autouse fixture `_single_screen_desktop`：把 `window_placement.desktop_area` 打桩成 `None`，避免多显示器/缩放屏上 #186 的多屏快照绕开假屏 |
| `tests/test_pet_interaction_locks.py` | +13 / −2 | 同上：假屏放大 + 同一个 autouse fixture（该文件也有 SHIFT/锁位拖拽用例，同样依赖假屏） |
| `tests/test_desktop_pet_features.py` | +3 / −1 | 长文件名重复 8 → 3 次，附 MAX_PATH 说明 |
| `tests/test_session_end_ffmpeg_guard.py` | +21 / −2 | `import time` + `_wait_until()` 有界等待；两处"对照组必须有调用"的断言改为等待式（失败路径最多多等 5 秒） |

### 产品代码

**未改动**：`pet/**` 一个字节都没动。这次的失败全部出在测试对环境的假设上。

## 三、实现要点

1. **假屏幕的尺寸是一个隐式契约**：测试想让"贴边钳位不参与判定"，但它给的
   `1920×1200` 只比目标大一点点——加上身体框后就不够了。放大到 4096×3072 后，
   所有被测目标都落在边界内，钳位自然不参与；这比"改断言去迎合钳位结果"更贴近测试意图。
2. **#186 的多屏快照是第二个边界来源**：`move_window_towards()` 里
   `host._screen_available()`（假屏）与 `desktop_area()`（真实屏幕枚举）是两条路。
   单屏机器上 `desktop_area()` 返回 `None`，看起来"假屏说了算"；但多显示器/远程桌面
   下它会返回真实区域并**优先**决定钳位范围。测试里显式关掉它，才能在任何桌面上确定。
3. **异步断言必须有界等待**：reader 线程的 spawn 与断言之间天然有竞态，等待式断言
   只在"真的没发生"时才失败——和 `test_agent_link_threads.py` 的既有惯例一致。
4. **不改产品去迁就测试**：这四条都不涉及产品语义，所以一行产品代码都没动；
   若产品真有错，报告里会写明是哪条链路（本例没有）。

## 四、性能分析

**本改动不触碰任何运行时路径**（`pet/**` 未改），因此稳态开销、系统调用、内存均为 0 变化。
可测的只有测试自身的墙钟：

**方法（可复现）**：`.venv\Scripts\python -m pytest <files> -q`
环境：Windows 11 (10.0.26200)，Python 3.12.14，PySide6 6.11.2。

| 指标 | 实测 | 归属 |
|---|---|---|
| 四个修复文件 | `56 passed in 2.71s` | 修复后 |
| 邻近回归集（8 个文件，含 edge/拖拽/会话门/webm/move_sync） | `231 passed in 30.63s` | 修复后 |
| 失败路径新增等待上限 | ≤ 5s（仅当断言真的不成立时） | 测试 |

**结论**：① 稳态开销无变化（产品未改）；② 新增路径只有测试里的有界轮询；
③ 未引入新的系统调用/网络/磁盘/线程到产品；④ 内存无变化。

## 五、实机运行记录

1. **起点是用户的本机报告**：`9 failed, 3016 passed, 9 skipped`（真实桌面、可写 `%APPDATA%`，
   所以 `test_click_sound` 那 4 条在用户侧是**通过**的）。
2. **钳位根因的现场探针**（本机真实平台，`work/probe_drag_clamp2.py`）：

   ```text
   platform: windows
     screen: \\.\DISPLAY1 QRect(0, 0, 1707, 1067)  available QRect(0, 0, 1707, 1067)  dpr 1.5
   desktop_area(): None
   窗口尺寸: QSize(461, 281)　身体框: 309×259
   假屏 1920×1200：virtual_pos = (1611, 941)   期望 (1674, 1008)   ← 被贴边钳位
   假屏 4096×3072：virtual_pos = (1674, 1008)  期望 (1674, 1008)   ← 完全吻合
                   消费后 = (1974, 1128)        期望 (1974, 1128)
   ```

   → 结论明确：**假屏给的可用区不足**，与产品无关。
3. **修复后实跑**：

   ```text
   pytest -q tests/test_drag_move_coalescing.py tests/test_pet_interaction_locks.py \
            tests/test_session_end_ffmpeg_guard.py \
            tests/test_desktop_pet_features.py::test_image_directory_picker_opens_right_drawer_with_three_column_masonry
   → 56 passed in 2.71s
   ```

4. **邻近回归实跑**（防止放大假屏/关多屏快照带来的副作用）：

   ```text
   pytest -q tests/test_edge_reachability.py tests/test_drag_move_coalescing.py \
            tests/test_pet_interaction_locks.py tests/test_session_end_ffmpeg_guard.py \
            tests/test_webm_reader_lifecycle.py tests/test_webm_clip_lifecycle.py \
            tests/test_move_sync.py tests/test_desktop_pet_features.py
   → 231 passed in 30.63s
   ```

5. **本机仍然红、但与本改动无关的项（如实列出）**：本机沙箱禁写工作区之外，
   所以 `tests/test_click_sound.py` 的 4 条（写 `%APPDATA%\...\sounds_cache`）
   在本机必红——已用"把缓存目录指到工作区"验证过修复后是 `19 passed`；
   另 `test_proactive…foreground_window_info_real_call`（需真实前台窗口）、
   `test_harness_launcher…fallback_without_dsh`（PATH 无 `node`/`dsh`）在本机也红，
   同样与产品无关（用户在真实桌面跑时只需确认这两条）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv\Scripts\python -m ruff check pet/ tests/` | ✅ All checks passed |
| 四个修复文件 | `pytest -q <4 files>` | ✅ 56 passed |
| 邻近回归集 | `pytest -q <8 files>` | ✅ 231 passed |
| 产品改动面 | `git diff --stat -- pet/` | ✅ 空（未改一行） |

## 七、已知限制与后续

- 本机沙箱无法覆盖"真实桌面"与"可写 `%APPDATA%`"，所以最终确认仍需用户在**自己的终端**再跑一次全量；
  预期：这 9 条转绿，只剩下需要真实前台窗口 / PATH 有 `dsh` 的个别用例。
- 多屏钳位语义不在本文件覆盖范围内（由 `tests/test_edge_reachability.py` 负责）。

## 八、风险与回滚

- **影响面**：仅测试。产品行为零变化。
- **回滚**：`git revert` 本提交即可；无配置迁移、无落盘数据。
