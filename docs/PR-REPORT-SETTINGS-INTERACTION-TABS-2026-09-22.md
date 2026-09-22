# 「互动」域设置页分页 + 抽 `pet/settings_interaction.py` 变更记录

> 结论先说：设置 → **互动** 原先 18 行 / 3 组**平铺、零折叠**（主人反馈"单页面太多东西"），
> 现在按「菜单」域的既有做法改成**页内任务标签**：**点击与音效**（输入 + 点击反馈）、
> **自言自语**（气泡台词与配图）。侧栏 9 个域保持不变；整域的行定义搬进新模块
> `pet/settings_interaction.py`，`modern_settings_dialog.py` **净减 94 行**
> （2441 → 2347），行数预算**首次因拆分下调**。
>
> 本报告按 `docs/PR-REPORT-TEMPLATE.md` 的三份证据组织。

---

## 一、修改文件说明（改了什么 + 为什么）

### 1.1 为什么

- 互动域 18 行 / 3 组**平铺无折叠**，是当时最长的"无折叠"域；设置页滚动很长、找东西靠翻。
- 「菜单」域已有页内 `SettingsTabContainer` 先例（菜单编排 / 快捷启动 / 外观），
  且实现日志里写明了判据：**同域内多个可独立完成的同级任务 → 分页；存在连续配置关系的不拆**。
  互动域两个标签正好是两个同级任务（点击行为 / 周期气泡），符合判据。
- 附带收益：整域搬出上帝对话框，缓解了反复触顶的行数预算（今天已有 3 次校准记录）。

### 1.2 逐文件（`git diff --numstat`，含新文件与截图）

| 文件 | 增 | 删 | 改了什么 / 为什么 |
|---|---:|---:|---|
| `pet/settings_interaction.py`（新） | 167 | 0 | 本域全部设置行 + 页装配：`build_interaction_domain()` 返回两个标签的 `SettingsTabContainer`（点击与音效 / 自言自语），行仍用 `SettingRow` 建、`objectName` 仍是 `settingRow_<键>` |
| `pet/modern_settings_dialog.py` | 9 | 103 | **净减 94 行**：删掉 `click_rows` 定义、自言自语 section、`mouse_through` 行、`self_talk_bubble_style` 行；互动域装配改为一行调用；加 1 行 import |
| `tests/test_settings_interaction_tabs.py`（新） | 193 | 0 | 6 条聚焦用例：标签键/名与侧栏不变、每行路由到正确标签且父开关打开后可达、非当前标签的行只是不可见（对象仍在）、搜索自动切标签、组名不变、**所有行仍在某域页内且无「待分类（开发期）」** |
| `tests/test_architecture.py` | 5 | 1 | 行数预算 **2441 → 2347**（带日期理由；注释里点明这是本文件第一次**因拆分为下调**） |
| `scripts/capture_settings_pages.py` | 32 | 0 | 新增 `--interaction-details`（逐标签出图）；修 `--image-previews`：先切到「自言自语」标签再截，否则会静默截到另一个标签页、抽屉看起来"消失" |
| `docs/SETTINGS-REDESIGN-IMPLEMENTATION-LOG.md` | 24 | 0 | 记录本次分页决策与判据（对齐既有"什么时候适合分页"口径） |
| `docs/DEV-HANDOVER.md` | 3 | 3 | 预算/实测与新模块同步；"设置页再拆分"条目更新为"已迈出第一步" |
| `docs/INDEX.md` + 本报告 + 4 张截图 | — | — | 报告登记；截图 = 两个标签 × 1100/720 宽度 + 1.3 倍字体 |

### 1.3 契约与不变量（为什么这样做是安全的）

- **侧栏 9 个域及其顺序不变**：域清单是外部契约（截图脚本按索引取页、多处用例按名称断言），
  页内标签才是同域任务的容器。
- **组名不变**：`输入` / `点击反馈` / `自言自语` 被 3 处用例硬编码
  （`test_menu_layout.py:1020`、`test_desktop_pet_features.py:1424`、`test_requested_regressions.py:187`）。
- **行不进 `all_rows` 快照**：它们在 `_rebuild_domain_navigation` 里构建（晚于快照），
  因此不需要 `claim`，也不会被判成「待分类（开发期）」——与 `settings_file_interpret` 同口径。
- **保存链一字未改**：`_write_config` 全部基于控件属性，与行挂在哪个容器无关。
- **显隐联动不变**：`_update_self_talk_controls` 等按 `findChild(SettingRow, "settingRow_<键>")` 工作，
  行的 `objectName` 未变。
- **搜索不变但要"切标签"**：命中非当前标签的行时由 `activate_for_descendant` 自动切过去
  （菜单域既有机制），本 PR 补了用例钉住它。

## 二、性能分析（实测数字）

| 项 | 数字 / 结论 | 依据 |
|---|---|---|
| 设置页构建 | 行数相同、容器多一层：互动域 18 行 → 2 个标签页；`modern_settings_dialog.py` 2441 → 2347 行（**−94**） | 实测 `wc -l` |
| 运行时开销 | 标签切换只改 `QStackedWidget` 当前页（Qt 既有实现），无重建、无定时器、无 IO；非当前标签的控件不被绘制（`isVisibleTo=False`），长页首帧绘制量下降 | Qt 语义 + 用例 `test_rows_of_the_other_tab_are_out_of_view_but_not_destroyed` |
| 新增线程 / 系统调用 / 网络 / 磁盘 | **零新增**：纯布局改动 | 代码审阅 |
| 内存 | 行对象数量不变（只是换父容器），无新增常驻缓存 | 同上 |
| 三档宽度无裁切 | 1100 / 720 两档截图逐行核对：说明文本按字体度量换行、控件右对齐、无横向溢出；1100×1.3 倍字体下同样完整 | `docs/screenshots/settings-interaction-tabs-2026-09-22/` |
| 测试耗时 | 聚焦 6 条 3.09s；受影响大族（9 个文件）298 passed / 46.90s；全量见 §3.3 | pytest 输出 |

## 三、实机运行记录

环境：Windows 桌面会话、Python 3.11.1、PySide6 6.11.1；截图与交互均在**真实 Qt 会话**
（非 offscreen）产出。

### 3.1 视觉（设置 → 互动）

```
$ python scripts/capture_settings_pages.py <dir> --width 1100 --interaction-details --image-previews
$ python scripts/capture_settings_pages.py <dir> --width 720  --interaction-details
$ python scripts/capture_settings_pages.py <dir> --width 1100 --font-scale 1.3 --interaction-details
→ 03-互动-点击与音效.png / 03-互动-自言自语.png / 03-互动-图片目录抽屉.png（各档宽度）
```

- 「点击与音效」标签：输入（鼠标穿透）+ 点击反馈（点击音效 / 音效音源 / 音量 / 试听 /
  点击显示余额 / 点击触发自言自语 / 点击台词朗读 / 台词自动预缓存 …）；
- 「自言自语」标签：气泡方案 / 气泡自言自语 / 显示时间 / 最短间隔 / 最长间隔 / 候选内容 /
  图片目录 / 配图大小 / **配图概率**；
- 720 px 与 1.3 倍字体下说明文本换行正常、控件不被裁切；
- `--image-previews` 抽屉仍能打开（且现在会先切到「自言自语」标签再截）。

### 3.2 交互与"功能不丢"

```text
tests/test_settings_interaction_tabs.py ......            [100%]   6 passed in 3.09s
```

覆盖：标签键/名与侧栏不变；每行可由标签键路由、父开关打开后在所属标签内可见；
非当前标签的行 `isHidden()==False`（只是不可见，控件对象仍在，保存链不受影响）；
搜索「配图概率」自动切到「自言自语」、搜索「点击音效」切回「点击与音效」；
组名与顺序不变；**所有 `settingRow_*` 仍落在某个域页内且没有「待分类（开发期）」**。

### 3.3 门禁

```text
python -m ruff check pet/ tests/                 -> All checks passed!
QT_QPA_PLATFORM=offscreen python -m pytest -q --basetemp=C:/ptt
-> 2793 passed, 11 skipped, 12 warnings in 230.92s (0:03:50)
```

回归面：既有硬编码用例全绿（`test_menu_layout` 的组名/顺序、`test_desktop_pet_features`
的依赖显隐与 section 标题集合、`test_requested_regressions` 的点击音效行位置、
`test_music_player_settings` 的新行归属）。

## 四、风险与回滚

- **风险**：行定义搬家的过程中若有遗漏会掉进「待分类（开发期）」——已用机器化断言
  （所有行仍在某域页内 + 无该标签）钉住，并在本 PR 中先跑红后跑绿。
- **回滚**：把 `interaction = settings_interaction.build_interaction_domain(self)` 换回
  原来的 `page_content([...])` 三组 claim，并恢复 `modern_settings_dialog.py` 中被删的四段行定义。
- **遗留**：「自动化与联动」94 行仍是最长域（其中 39 行已在折叠框内），按同法分页是下一步；
  macOS/Linux 真实 GUI 未验收（纯布局改动、无平台分支，但如实声明）。
