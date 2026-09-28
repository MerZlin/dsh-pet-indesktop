# PR 报告：补齐四项（回收内存 / 定时提醒 / 用久了提醒 / 全局快捷键）2026-09-29

> **基线**：`ff82327`（AI 协作复盘那批）
> **分支**：`main`（本地改造分支，未推送）　**日期**：2026-09-29
> **范围**：实现 13 个文件（9 个新增模块）+ 测试 9 个文件（5 新增 / 4 更新契约）+ docs 2
> **关联**：功能补齐方案（会话交付物）里剩下的 P3~P6；
> 本批**未跑 pytest**——按 [`AI-LESSONS-2026-09-29.md`](AI-LESSONS-2026-09-29.md) 的
> 第 6 条硬约定，测试执行权归用户（下方第六节给了建议命令）。

## 一、核心特性

把方案里剩下的四项一次性补齐。四项都遵循同一条架构：**纯逻辑模块（不 import Qt）+ 服务壳
（线程/节拍 + 气泡）+ 菜单/设置接线**，因此判定逻辑都能离线单测。

| # | 功能 | 用户看到什么 | 纯逻辑模块 | 服务壳 |
|---|---|---|---|---|
| 1 | **回收内存** | 右键菜单「回收内存」→ 冒一句「收拾好啦：22 个程序腾出 2.6 GB」 | `pet/memory_trim.py` | `pet/memory_trim_service.py` |
| 2 | **定时提醒** | 菜单「定时提醒」开关；规则到点说一句（可先报今日汇总） | `pet/timed_reminder.py` | `pet/timed_reminder_service.py` |
| 3 | **用久了提醒** | 菜单「用久了提醒」开关；某应用连续用满设定时长说一句 | `pet/app_usage.py` | `pet/app_usage_service.py` |
| 4 | **全局快捷键** | 菜单「全局快捷键」开关；在任何程序里按 Ctrl+Alt+1/2/3 都有反应 | `pet/hotkey_rules.py` | `pet/hotkey_service.py` |

**红线 / 不变量**：

1. 四项都不新增第三方依赖（回收内存用 ctypes、快捷键用 Win32 + Qt 原生过滤器）；
2. 四项的规则/键位/台词都住在 `config.json`，设置页只给开关——不让 `modern_settings_dialog.py`
   继续臃肿；
3. 气泡统一走 `pet/window_alerts.show_alert()`，因此**免打扰期间一律被压住并攒着**；
4. 非 Windows 平台：回收内存与快捷键整体退化（设置与菜单照旧可见），其余两项与平台无关。

## 二、修改文件说明

### 新增实现

| 文件 | 行数 | 改动意图 |
|---|---|---|
| `pet/memory_trim.py` | +276 | 温和回收工作集：`select_targets()`（太小/名单/自己三跳）、`summarize()` 文案、`_Win32Backend`（**argtypes 全部声明**，防 64 位句柄截断）。OS 出口可注入，判定离线可测 |
| `pet/memory_trim_service.py` | +131 | 后台线程收一轮 → 队列 → 主线程 300ms tick 冒泡；默认跳过前台进程 |
| `pet/timed_reminder.py` | +173 | 规则清洗（每天/每周/每隔 N 分钟）+ `due_rule_ids()`：`"YYYY-MM-DD HH:MM"` 槽位去重、interval 首拍只盖戳、错过的窗口不补报 |
| `pet/timed_reminder_service.py` | +130 | 20 秒节拍；`summary: true` 的规则先接今日汇总；菜单开关 + 立即生效 |
| `pet/app_usage.py` | +149 | `UsageTracker` 状态机：换应用重置、`FG_IDLE_FREEZE=600` 秒静默冻结、首次 + 每 `repeat` 分钟重复、`{app}/{minutes}/{hm}` 变量 |
| `pet/app_usage_service.py` | +131 | 5 秒节拍；复用 `vision.foreground_window_info()` / `get_system_idle_seconds()`，不重写 Win32 |
| `pet/hotkey_rules.py` | +135 | 键名解析（**必须带 Ctrl**、拒绝 Ctrl+C/V/X/Z 等保留键、只认字母数字/F1~F24/白名单特殊键）、规则清洗、默认三条 |
| `pet/hotkey_service.py` | +229 | `RegisterHotKey(None, id, mods\|MOD_NOREPEAT, vk)` + `QAbstractNativeEventFilter` 接 `WM_HOTKEY`；注册失败**如实回报**键序；动作分发（说一句 / 看天气 / 看余额） |
| `pet/settings_reminders.py` | +76 | 设置页「提醒与快捷键」组的 4 个开关（细则键仍在 config.json） |

### 接线改动

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/config.py` | +39 / −0 | 新增 8 个键：`mem_skip_foreground`、`mem_min_size_mb`、`timed_on`、`timed_rules`、`app_usage_on`、`app_usage_rules`、`hotkeys_on`、`hotkeys` + 各自清洗 |
| `pet/context_menus/registry.py` | +58 / −0 | 新增 4 个动作：`memory_trim`、`timed_reminder`、`app_usage_toggle`、`hotkeys_toggle`（后三个是可勾开关）+ 标签/图标 |
| `pet/menu_templates/modern-default-v1.json` | +4 / −0 | 新版模板加 4 个 action 节点 |
| `pet/menu_templates/modern.json` | +1 / −1 | 旧版菜单 `tools` 分组补 4 个入口 |
| `pet/modern_settings_dialog.py` | +4 / −0 | 只多 import、「自动化与联动」域一行、保存链一行与注释 |
| `pet/app.py` | +55 / −0 | `_service_windows()` 助手 + `_sync_timed_reminder/_sync_app_usage/_sync_hotkeys`，并在启动、设置保存（含外部配置变更）三处同步 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_memory_trim.py`（新增） | +129 / −0 | 三跳口径、`human_mb` 格式化、`clean_min_mb` 夹取、统计聚合（trimmed/denied/failed/freed）、backend 抛错兜底、文案三分支 |
| `tests/test_timed_reminder.py`（新增） | +107 / −0 | `HH:MM` 归一（含 `930`/`9:5`）、星期清洗、规则清洗与上限、同分钟只触发一次、错过窗口不补报、interval 首拍只盖戳 |
| `tests/test_app_usage.py`（新增） | +93 / −0 | 换应用重置、静默冻结、首次到点、重复间隔、规则关闭/空台词跳过、变量替换 |
| `tests/test_hotkey_rules.py`（新增） | +89 / −0 | 键名解析 15 组参数化（含保留键与"必须带 Ctrl"）、显示名归一、规则清洗去重与动作白名单、内置表有效 |
| `tests/test_settings_reminders.py`（新增） | +91 / −0 | 4 个开关初值来自 config、二次装配不重建、行 `objectName`、写回 4 键 |
| `tests/test_menu_layout.py` | +23 / −0 | 根节点 id / `registered` / 解析结果 / QMenu 渲染序列四处补 4 个新动作 |
| `tests/test_desktop_pet_features.py` | +7 / −0 | 根菜单文本序列补 4 个新入口 |
| `tests/test_config_schema.py` | +11 / −2 | 白名单快照补 8 键；文件头数字同步实测（默认值 143 / 白名单 138） |
| `tests/test_architecture.py` | +4 / −1 | `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 2392 → 2396，理由写在常量上方 |

### 未改动（明确说明）

- `pet/window.py`：一行未改（菜单经注册表直接调服务，气泡走 `window_alerts`）。
- 未引入 `psutil` 之外的新依赖；回收内存与快捷键都走 ctypes / Qt 自带能力。

## 三、实现要点

1. **四项同一形状**：纯逻辑（可离线测）+ 服务壳（线程/节拍 + 气泡）+ 菜单/设置接线。
   这也是本仓库既有服务的形状（`voice_chime_service` / `todo_reminder` / `quiet_service`）。
2. **免打扰天然兼容**：四个新气泡都用 `alert_type`（`memory_trim` / `timed_reminder` /
   `app_usage` / `hotkey`）走提醒队列；免打扰期间被压住并计入"攒了 N 条"。
3. **节拍成本分级**：定时提醒 20 秒（够用）+ 用久了提醒 5 秒（精度需要）+
   回收内存/天气/免打扰按需 300ms 且**队列空即停表**，没有常驻空转。
4. **快捷键的保守策略**：必须带 Ctrl、拒绝 `Ctrl+C/V/X/Z/S/F/W/T/N/R` 等保留键、
   只认白名单键；`RegisterHotKey` 失败（键被占用）会记录键序并在开关时冒泡告知，
   不静默失败。
5. **config.json 是规则的家**：设置页只给开关，细则（规则表/键位/台词）写在配置里，
   避免 `modern_settings_dialog.py` 继续膨胀（本批只 +4 行）。

## 四、性能分析

**说明**：本批**没有跑 pytest**（约定见文首），因此这里给的是**静态可推算的成本**与
`ruff`/`py_compile` 的实测结果，不含运行时采样。等用户跑完全量后可在报告里补实测。

| 指标 | 数值 | 归属 |
|---|---|---|
| 新增常驻节拍 | 定时提醒 20s 一拍、用久了 5s 一拍（仅开启时） | 新增 |
| 按需节拍 | 回收内存 300ms，只在发起后跑到结果回来 | 新增 |
| 快捷键 | 零轮询（纯消息驱动，只在按键时被唤醒） | 新增 |
| 回收内存的系统调用 | 一次 EnumProcesses + 每进程 1~3 次 OpenProcess/GetProcessMemoryInfo + 1 次 EmptyWorkingSet | 新增（仅手动触发） |
| 新依赖 | 0 | — |

**结论**：① 稳态开销 = 定时提醒 + 用久了两个 QTimer（各 1 个，关闭即停）；
② 新增路径成本集中在手动触发的回收内存与按键响应；③ 不新增第三方依赖；
④ 内存：四项各挂一个小 dict/对象，规则表规模有上限（40/12/20 条）。

## 五、实机运行记录

**本轮人家只跑了静态检查**（按 2026-09-29 定下的硬约定：pytest 归用户）：

```text
.venv\Scripts\python -m ruff check pet/ tests/     → All checks passed!
.venv\Scripts\python -m py_compile <17 个文件>      → exit 0
```

**必须由用户在真机确认的四件事**（人家做不到，也不猜）：

1. **回收内存真能腾出内存**：点菜单「回收内存」，看气泡数字与任务管理器里的可用内存；
2. **快捷键真能按下**：Ctrl+Alt+1/2/3 在别的程序里也响应；把某个组合先被别的软件占用，
   确认开关会提示"有几个键被别的程序占着"；
3. **定时提醒真能到点**：把 `timed_rules` 里某条改成 1 分钟后的 `HH:MM` 等它响；
   interval 型首拍不炸（不该一开就说话）；
4. **用久了提醒真会算**：把某应用的 `minutes` 改成 1，实际用满看是否只提示一次；
   挂机 10 分钟确认不提示（静默冻结）。

## 六、测试与验证（交给用户）

建议按这个顺序跑（先聚焦、后全量）：

```powershell
cd <仓库路径>   # 例如本机：…\Documents\…\dsh-pet-indesktop
$env:TMP="$PWD\.tmp"; $env:TEMP="$PWD\.tmp"; New-Item -ItemType Directory -Force $env:TMP | Out-Null

# 1) 本批新增的纯逻辑用例
.\.venv\Scripts\python -m pytest -q tests/test_memory_trim.py tests/test_timed_reminder.py `
    tests/test_app_usage.py tests/test_hotkey_rules.py tests/test_settings_reminders.py

# 2) 契约（菜单 / 配置 / 架构 / 设置页）
.\.venv\Scripts\python -m pytest -q tests/test_menu_layout.py tests/test_config_schema.py `
    tests/test_architecture.py tests/test_settings_interaction_tabs.py

# 3) 全量
.\.venv\Scripts\python -m pytest -q
```

预期：前两组全绿（新增用例约 60 条）；全量在修掉真实桌面 9 个失败之后应当也是绿
（上一轮基线：`3027 passed, 9 skipped`）。

## 七、已知限制与后续

1. **规则没有图形编辑器**：`timed_rules` / `app_usage_rules` / `hotkeys` 目前要在
   `config.json` 里改（设置页只给开关）。做编辑器是下一步的 UI 活（参考
   `click_talk_dialog.py` 那种小对话框）。
2. **回收内存不自动定时**：刻意只做手动（自动回收会让游戏/IDE 重新读盘）。
3. **快捷键非 Windows 退化**：macOS/Linux 上开关可用但不注册（设置页说明写在行文案里）。
4. **用久了提醒只认前台 exe 名**：同一 exe 的多个实例算一个（按进程名聚合）。

## 八、风险与回滚

- **影响面**：新增 4 个菜单项、8 个配置键、2 个常驻 QTimer（仅在开关打开时运行）、
  1 个全局快捷键注册表项（仅 Windows）。
- **配置迁移**：8 个键均为纯新增键，`version` 不需升；老 `config.json` 直接读默认值。
- **回滚**：`git revert` 本提交即可；无落盘数据（快捷键注册在进程退出时由系统回收，
  但**关闭开关会显式注销**）。

---

# 第二轮（同日稍后）：用户报告 9 个失败 + 测试闪烁 —— 归因与修复

用户在真机跑全量得到 `9 failed, 3071 passed, 9 skipped`，并反馈"测试时会切屏闪烁"。
逐条查完：**6 个是本批引入的缺陷，3 个是测试环境差异**；闪烁与 CI 平台设置不一致有关。

## 一、六个缺陷（本批引入，全部已修）

| # | 失败用例 | 根因 | 修法 |
|---|---|---|---|
| 1 | `test_memory_trim.py::test_human_mb_formats` | `human_mb(0)` 落到 KB 分支给出 "0 KB" | 0/负数显式返回 "0 MB"（`pet/memory_trim.py`） |
| 2–4 | `test_app_usage.py` 三条 | `UsageTracker.feed` 里 `self._clock or now` —— **时钟为合法的 0.0 时被当成假值**，于是首拍之后所有 gap 都算 0，永远到不了阈值 | 改成显式 None 判断（`base = self._clock if self._clock is not None else now`） |
| 5 | `test_hotkey_rules.py::test_normalize_seq_makes_display_form` | `normalize_seq` 把多字符 token 一律首字母大写（`space` → `Space`），与测试期望的"保留原样"不符 | 只把 F1~F24 与单字母规范化，其余保持用户写法 |
| 6 | `test_hotkey_rules.py::test_hotkey_parse_reads_function_keys_and_letters` | 测试想验 `Ctrl+A`，但 `Ctrl+A` 在**保留名单**里（抢了等于替用户按全选） | 保留名单不动（实现是对的），把用例改成 `Ctrl+G` 并显式断言 `Ctrl+A is None` |
| 7–8 | `test_pet_app_binds_about_to_quit_once_to_current_window`、`test_settings_process_isolation.py::test_apply_external_config_change_fans_out_to_all_instances`、`test_voice_chime_service.py::test_about_to_quit_stops_voice_chime_service` | 两处"测试替身兼容"问题：① 五个新服务用 `QTimer(host)`，而宿主在测试里是普通对象/Mock（**不是 QObject**）→ `TypeError`；② `hotkey_service.unregister_all/on_hotkey` 直接 `list(getattr(host, ...))`，Mock 属性不可迭代 → `TypeError` | ① 全部改成**无主 QTimer()**（与本仓库既有服务同口径，引用挂在 host 属性上）；② 属性取值加 `isinstance` 守卫（非列表/非 dict 一律当"没注册过"） |

## 二、测试闪烁：与 CI 的平台设置不一致

**根因**：CI（`.github/workflows/pr-test.yml`）显式设了 `QT_QPA_PLATFORM: offscreen`，
而本机直接 `pytest` 用的是**真实 Windows 平台** —— 用例会真的创建透明置顶的桌宠窗口，
于是跑测试时桌面上"切屏闪烁"。

**修法**：`tests/conftest.py` 顶部（导入 Qt 之前）加 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`，
**与 CI 完全一致**；想用真实平台观察窗口行为时，先设 `$env:QT_QPA_PLATFORM = "windows"` 即可覆盖
（用的是 `setdefault`，环境变量优先）。

顺带收益：本机跑测试不再往桌面上弹真窗口，也能让几何类断言与 CI 行为一致。

## 三、本轮验证（仍未跑 pytest）

```text
ruff check pet/ tests/                     → All checks passed!
py_compile（11 个改动文件）                 → exit 0
work/probe_batch_fixes.py（13 项最小断言）  → 全部通过
```

探针覆盖的正是这六处修复：`human_mb(0)`、tracker 首拍/到点/重复、`normalize_seq`
特殊键与 F 键、`Ctrl+G` 可用 / `Ctrl+A` 保留、`hotkey_service` 面对 Mock 属性不炸、
`timed_reminder_service` 对非 QObject 宿主也能起表。

> 探针第一版自身还踩了一下：没建 `QApplication` 就 `QTimer.start()`，`is_running` 恒 False
> ——QTimer 需要事件循环所属的 QApplication，这与产品无关，已在探针里补上。

## 四、请用户复跑

```powershell
cd <仓库路径>
$env:TMP="$PWD\.tmp"; $env:TEMP="$PWD\.tmp"; New-Item -ItemType Directory -Force $env:TMP | Out-Null
.\.venv\Scripts\python -m pytest -q
```

预期：上一轮那 6 个失败转绿；并且**跑测试时桌面不再闪烁**（窗口都在 offscreen 里）。
若仍有与"真实平台"相关的用例想验，用 `$env:QT_QPA_PLATFORM = "windows"` 单独跑那一个文件。

## 五、用户回执（同日）：全绿

```text
3079 passed, 10 skipped, 14 warnings in 208.51s (0:03:28)
```

对照上一轮 `9 failed, 3071 passed, 9 skipped`（231.67s）：**9 个失败全部转绿**，
且整体快了约 23 秒（窗口不再上真实桌面，少了一堆 WM/合成器交互）；
其中一条用例从 failed 变成 skipped（它本来就声明"offscreen 下不适用"）。

---

# 第三轮（同日稍后）：规则图形编辑器 + 菜单编排的中文标签

用户要求两件事：① 把"规则只能改 config.json"这个已知限制解掉（图形编辑器）；
② 菜单编排列表里 `voice_chime_now` 这类**英文 id** 要变成中文（默认中文）。

## 一、规则图形编辑器（`pet/rules_editor.py`）

一个对话框，三个页内标签，对应三张规则表：

| 标签页 | 配置键 | 列 |
|---|---|---|
| 定时提醒 | `timed_rules` | 启用 / 类型（每天·每周·每隔）/ 时间 / 星期 / 间隔 / 带汇总 / 台词 |
| 用久了提醒 | `app_usage_rules` | 启用 / 进程名 / 分钟 / 重复 / 台词 |
| 全局快捷键 | `hotkeys` | 启用 / 按键 / 动作（说一句·看天气·看余额）/ 台词 |

- 每页有「添加一行 / 删除选中 / 恢复本页默认」；台词一栏**每行一句**；
- **非法行不许保存**：写错的行会被指出来（"定时提醒第 2 行写得不完整"），
  例如时间写成 `25:99`、每隔没写间隔、快捷键没带 Ctrl 或用了 `Ctrl+C` 这类保留键；
- 保存即写回 `config.json` 并**立刻生效**：快捷键重新注册、用久了换规则、定时提醒保持节拍；
- 入口有两个：右键菜单「编辑规则…」，以及设置页「自动化与联动 → 提醒与快捷键 → 编辑规则…」。

**分层**：文本 ↔ 规则的转换与校验全在纯逻辑模块 `pet/rules_text.py`（不 import Qt），
所以"哪一行非法"这种判定能离线单测；对话框只负责摆控件与收集单元格。

## 二、菜单编排里的英文 id（默认中文）

**根因**：`MenuActionRegistry.label()` 是 `ACTION_LABELS.get(action_id, action_id)`——
**没登记标签的动作会原样显示英文 id**；而 `voice_chime_now` / `voice_chime_toggle` /
`festival_now` / `festival_toggle` 这四个只登记了图标、没登记标签（它们的菜单文案是
动态的"启用↔关闭"，写编排器名字时被漏掉了）。

**修法**：补四个中性中文名（立即报时 / 语音报时开关 / 今日节日 / 节日提醒开关），
并新增护栏用例 `tests/test_menu_action_labels.py`：**每个注册动作都必须有中文标签、
且显示名不许等于 id 本身**——以后再加动作漏登记，测试直接红。

## 三、本轮验证（仍未跑 pytest）

```text
ruff check pet/ tests/                → All checks passed!
py_compile（7 个改动/新增文件）        → exit 0
work/probe_rules_editor.py（11 项）    → 全部通过
```

探针覆盖：缺标签动作 = 空集、四个新中文名、三张表的往返映射、非法行被拒、
以及**对话框无头冒烟**（能建起来、三个标签页、三张表行数与 config 一致）。

## 四、请用户复跑

```powershell
cd <仓库路径>
$env:TMP="$PWD\.tmp"; $env:TEMP="$PWD\.tmp"; New-Item -ItemType Directory -Force $env:TMP | Out-Null
.\.venv\Scripts\python -m pytest -q
```

新增用例集中在 `tests/test_rules_text.py`、`tests/test_menu_action_labels.py`，
以及 `tests/test_menu_layout.py` / `tests/test_desktop_pet_features.py`（多了一个「编辑规则…」入口）。

---

# 第四轮（同日）：编辑器三条失败 + 一处 UX 改进

用户回执：`3 failed, 3086 passed, 10 skipped`（207.62s）。三条都出在上一轮的编辑器/设置组上：

| # | 失败用例 | 根因 | 修法 |
|---|---|---|---|
| 1 | `test_rules_text.py::test_weekday_label_round_trip` | `label_to_days()` 把空格**删掉**而不是当分隔符，于是 `"周一,周三 周六"` 被当成一个词（`周三周六`）→ 只剩 `[0]` | 空格与顿号、逗号一样当分隔符（`.replace(" ", ",")`） |
| 2 | `test_rules_text.py::test_timed_rule_round_trip_interval_and_weekly` | `timed_rule_to_row()` 里的条件写反了：**对 `interval` 反而把"间隔"一栏留空**，回读时"每隔但没写间隔"被判非法 → 返回 None | 改成"只在 `interval` 时填 `every`，每天/每周留空" |
| 3 | `test_settings_reminders.py::test_build_controls_defaults_to_on` | 上一轮往 `ROWS` 里加了「编辑规则…」按钮行，而那条用例把每一行都当开关断言 `isChecked()` | 拆成 `SWITCH_ROWS`（4 个开关）与 `BUTTON_ROWS`（1 个按钮），默认值断言只覆盖开关，按钮断言文案 |

**顺带改进（用户说"改进"，这条算人家自己找的）**：编辑器里「添加一行」以前给**全空行**，
得逐格填满才能保存，第一次用很容易撞上"写得不完整"。现在按页预填一份**合法起点**：

| 页 | 新行预填 |
|---|---|
| 定时提醒 | 是 / 每天 / 09:00 / — / — / 否 / （台词留空，回读时自动补内置台词） |
| 用久了提醒 | 是 / （进程名留空）/ 60 / 0 / — |
| 全局快捷键 | 是 / Ctrl+Alt+G / **看天气** / —（选"看天气"是因为它不需要台词，新行一加就是合法的） |

## 本轮验证（仍未跑 pytest）

```text
ruff check pet/ tests/            → All checks passed!
work/probe_round2_fixes.py（11 项） → 全部通过
```

探针覆盖：空格分隔星期、interval 往返（`every=15` 能回来）、每天/每周的间隔栏留空、
weekly 往返、以及编辑器三页"添加一行"后的新行**立刻合法**（含快捷键预填 `Ctrl+Alt+G`）。
