# 自言自语「配图概率」（self_talk_image_chance）变更记录

> 结论先说：点击桌宠时弹出的**配图**原先和文本**等权随机**——图片目录里图越多，文本越轮不到（实测某配置 24 张图 + 5 句文本 → 出图概率 **82.8%**）。现在改为**先掷一次骰子决定"这次出图还是出文本"**，骰子权重由新设置「配图概率」决定（0~100%，默认 **30%**，0% = 只出文本）。用户要的"调低"和"自己选"由这一个控件同时满足。

---

## 一、问题（实测数据）

`show_random_self_talk` 原先把文本与图片拼成一个列表后 `random.choice`，即**按条数等权**。真实配置：配图目录 `assets/big_blue_fat_fish` 有 **24 张图**，`self_talk_texts` **5 句** → 出图概率 `24/29 ≈ 82.8%`，用户反馈"点击老是冒图片"。

## 二、方案

- 新增配置键 `self_talk_image_chance`（整数百分比 0~100，默认 30）。
- 抽签顺序：**先决定类型再选内容**——按概率掷骰，落在图片则在整个图片池里等权选一张，否则在文本池里等权选一句。用户设的百分比就是真实出图概率。
- **只有一边有内容时不掷骰子**（只有图就出图、只有文本就出文本）：否则会出现"这次点击什么都不显示"。
- 越界值统一钳到 0~100；配置缺失/读取失败回落到默认 30（与 `self_talk_speak_enabled` 同样的"运行时读配置 + 安全默认"写法）。
- 纯逻辑缝 `pick_self_talk_choice(texts, images, chance)` 放 `pet/window_alerts.py`，可脱离 Qt 单测。

## 三、使用方式

设置 → **互动** 域 → **自言自语** 组 → **「配图概率」**（0~100%）：
- `0%` = 只出文本（等价于"关掉配图"，但图片目录仍保留，随时调回来）
- `30%` = 默认，文本为主、偶尔配图
- `100%` = 只出图
- 想回到从前那种"图多"的观感：填 `图数/(图数+文本数)` 的百分比（例如 24 图 + 5 句 → 83%）

点击桌宠与定时自言自语**共用**这一概率。

## 四、注意事项

1. **默认 30% 是有意的行为变更**：原先等权随机时，图片多的用户实际出图率极高，配图本该是点缀。改变默认值是为了让"文本还能轮到"，用户可通过控件任意调整（含 0% 与 100%）。
2. 配图概率**只影响类型选择**，不影响配图大小（`self_talk_image_scale`）、目录（`self_talk_image_dir`）或气泡时长。
3. 图片气泡**不出声**（没有可朗读的文本，`_last_self_talk_text` 记 None），所以"文本占比"可以从日志出声条数间接实测——本记录的验证就用了这个口径。
4. 行为只在**桌宠重启后**生效（窗口启动时读一次并归一化；与其它设置一致走保存→关设置页→应用）。

## 五、设置变更记录（`docs/SETTINGS-CHANGE-GATES.md` 对照）

### 5.1 准入（6 条）

| 准入条目 | 结论 |
|---|---|
| 它是偏好 | ✓ "配图出现的频率"是长期偏好，不是一次性命令 |
| 低频且跨任务 | ✓ 设一次长期有效 |
| 有可靠默认值 | ✓ 默认 30%，产品无需配置即可用（文本为主、偶尔配图） |
| 归属唯一 | ✓ 互动 / 自言自语（显式 `claim("self_talk_image_chance")`，已加归属断言） |
| 值得让用户决策 | ✓ 出图频率没有客观最优值，产品无法推断 |
| 契约完整 | ✓ 见 5.2 |

### 5.2 准入记录

```text
setting_id             self_talk_image_chance
domain_id / group_id   互动 / 自言自语
title / description    配图概率 / 点击与定时自言自语时显示配图的概率，其余显示文本…
search_aliases         无（设置页没有搜索机制）→ 不适用
default                30
capability_requirement 无（纯本地随机，无需服务/权限）
platform_availability  Windows / macOS / Linux 一致，无平台分支
disclosure_level       primary
dependency             随「自言自语」总开关整组显隐（已列入显隐名单）
preview_target         无专用预览（点一下桌宠即真实生效）
commit_policy          on_finish（关闭设置页统一写回）
migration              全新键：老配置无此键 → 取默认 30；无旧键/旧入口需要处理
recovery               值越界钳到 0~100；配置损坏按默认 30；不想出图设 0%
```

### 5.3 准出（5 大项）

1. **契约** ✓ 见上；失败有默认值、有安全入口（0%），不影响设置页可用性。
2. **TDD** ✓ 先写 7 条纯逻辑用例（当时全红：`window_alerts` 还没有这两个符号），实现后全绿；另有配置默认/钳位/持久化、设置页行往返、以及**既有派发用例的按新契约更新**（它原先假设"永远选中图片"，正是本次要改的行为——先红后绿）。
   覆盖：默认值、持久化 round trip、依赖显隐、归属、0%/100% 边界、单边池、空白文本、越界钳位、读取失败回落。
   不适用：迁移（全新键）、平台 capability matrix（无平台能力差异）、搜索/深链（无搜索机制）、`git diff --check`（本机无 git，且目录不是 git 工作树）。
   全量：**2401 passed / 9 skipped / 0 failed**；`ruff check pet/ tests/` 通过。
3. **布局与可访问性** —— **视觉项未完成**：新行沿用既有 `SettingRow` + 既有数值控件 `BrowserSpinBox`（0~100、`%` 后缀），与相邻「配图大小」同构，键盘/焦点/主题均继承；**未做**三档宽度、放大字体、High DPI 的视觉核验（原因：本会话视觉后端不可用，无法出图）。
4. **跨平台** ✓ 纯本地逻辑，无平台分支；真实 GUI 验收仅 Windows（如实声明）。
5. **视觉与文档** —— 截图**未做**（原因同上）。`CONTEXT.md` 无需更新（未改 Shared UX Contract / Settings System 模型 / Menu Action Model，只是既有组内新增同构控件）。

## 六、验证记录

### 6.1 实测（真实 GUI，Windows）

配置：24 张配图 + 5 句文本，`self_talk_image_chance = 30`。

用 `F:\dsh\_measure_image_chance.py`（PostMessage 真实点击 + 数日志出声条数；图片气泡不出声，故"出声次数/点击次数"即文本占比）点击 **18 次**：

```
点击 18 次，其中出文本（=出声）累计 11 次
实测文本占比 ≈ 61%  → 实测出图率 ≈ 39%
```

对照：改动前同配置的理论出图率 **82.8%**（等权随机）。18 次抽样中 7 次出图，与 30% 的期望值（5.4 次，σ≈1.9）一致，属正常波动；方向性结论明确——**文本从"基本轮不到"变成"多数情况"**。

### 6.2 测试

`python -m pytest -q --basetemp=C:\pt` → **2401 passed, 9 skipped, 0 failed**；`ruff` 全清。
（`--basetemp` 为 Windows 长文件名 MAX_PATH 规避，见前一份变更记录。）

## 七、变更文件清单

- `pet/window_alerts.py`：新增 `DEFAULT_IMAGE_CHANCE`、`self_talk_image_chance()`、`pick_self_talk_choice()`；`show_random_self_talk` 改用新抽签
- `pet/config.py`：常量 `DEFAULT_SELF_TALK_IMAGE_CHANCE`、默认值、reload 白名单、归一化、`set()` 清单
- `pet/settings_pet_controls.py`：`self_talk_image_chance_spin`
- `pet/modern_settings_dialog.py`：SettingRow + 归属 claim + 显隐名单 + 保存写回
- `tests/test_self_talk_image_chance.py`（新）、`test_desktop_pet_features.py`、`test_config_schema.py`、`test_menu_layout.py`、`test_architecture.py`（预算 2330→2340，带日期理由）
- 仓库外实测脚本：`F:\dsh\_measure_image_chance.py`

## 八、风险与回滚

- **风险**：默认值变化会改变所有用户（含未打开设置页的人）看到的配图频率；这是有意为之，且控件就在同组内一行可调。
- **回滚**：把「配图概率」设为 `图数/(图数+文本数)` 的百分比即可复现旧观感；设 100% 只出图、0% 只出文本；代码层回滚只需删除新键与 `pick_self_talk_choice`，恢复 `random.choice(choices)`（`show_random_self_talk` 的其余分支不变）。
- **遗留**：设置页视觉/放大字体验收与 macOS/Linux 真实 GUI 验收未完成（5.3 第 3、4、5 条已标注）。
