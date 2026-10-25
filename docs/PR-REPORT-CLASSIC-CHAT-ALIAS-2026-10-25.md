# PR 报告：经典聊天窗读取角色别名（issue #370，2026-10-25）

> **基线**：`34d810b1`（upstream/main，v4.4.0）
> **分支**：`fix/issue-370-classic-chat-alias`　**日期**：2026-10-25
> **范围**：6 个文件（实现 5、测试 1、文档 3——本报告 + INDEX 登记 + issue 回复）
> **关联**：[issue #370](https://github.com/MerZlin/dsh-pet-indesktop/issues/370)

## 一、核心特性

经典聊天窗（`chat_ui_style = "classic"`）此前不读 `character_aliases`：右键重命名后
标题仍显示素材目录名（如 `shenshen · AI 对话`）、头像固定为目录名首字母（"S"）；
现代窗正常——别名机制本身没坏，是经典窗漏读（根因：`legacy_widgets._apply_character_theme`
缺别名查询）。本刀修三件事：

| # | 能力 | 说明 |
|---|---|---|
| 1 | 经典窗标题显示自定义角色名 | 补上与现代窗一致的优先级链：别名 → manifest 名 → 目录 id |
| 2 | 头像对齐现代窗 | 经典窗标题栏头像优先**桌宠实时画面**（`icon_pixmap`），拿不到回退**显示名**首字母（"小小萱"→"小"）；顺带修现代窗在桌宠隐藏时回退字母取目录名首字母的同族问题 |
| 3 | 改名即时刷新 | 已打开的经典/现代聊天窗在改名后立刻更新标题与头像，无需关窗重开 |

**红线 / 不变量**：
- 未设置别名时显示与旧版**逐字相同**（manifest 名/目录名 + 同一首字母口径）；
- 不动角色/会话切换链（`switch_character`）、不动会话存储；
- 广播对"改的是别的角色"自然无变化（各窗只读自己 `character_id` 的别名）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/chat/legacy_widgets.py` | +38 / −3 | ① `_apply_character_theme` 补 `character_alias` 查询（标题根因修复）；② 新增 `_update_header_avatar`（实时画面 → 显示名首字母回退；画面模式去彩色圆底）；③ import 补 `QPixmap`；④ 新增公开 `refresh_character_identity()` |
| `pet/chat/widgets.py` | +10 / −1 | ① `_update_header_avatar` 回退字母从 `_initial(character_id)` 改为 `_initial(character_name)`（桌宠隐藏时不再退回 "S"）；② 同款公开 `refresh_character_identity()` |
| `pet/window.py` | +5 / −0 | legacy 改名入口：`__init__` 声明 `on_character_renamed = None`（app 注入位，同 `on_switch_character` 口径）；`_rename_character` 在 `set_character_alias` 后触发回调 |
| `pet/app.py` | +18 / −0 | ① 窗口接线处注入 `win.on_character_renamed`；② `PetInstance.refresh_chat_identity()`：对两个已打开聊天窗广播 `refresh_character_identity`（getattr 守卫，`sync_chat_character` 同款循环） |
| `pet/sprite_menu_facade.py` | +6 / −0 | overlay 改名入口：`set_character_alias` 后经 `shell._instance` 广播同一方法（与 legacy 入口对称） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_chat_classic_alias.py` | +141（新文件） | 10 例：经典窗标题别名/无别名回退、现代窗标题别名、两窗头像字母回退（别名首字符）、经典窗头像吃 stub 桌宠实时画面、无别名头像首字母口径、两窗改名即时刷新、legacy 改名入口回调触发（真 PetWindow + monkeypatch 输入框） |

### 未改动（看起来相关但故意没动）

- `pet/config.py` 的 `character_alias` / `character_display_name`：机制本身正确（现代窗
  在用），零改动；
- `switch_character` / 会话存储 / `sync_chat_character`：角色切换链不受别名影响；
- 现代窗每条消息的 avatar/meta 控件（`widgets.py:407/413` 处于 hide 状态）：属于
  "像 QQ/微信显示头像名字"的**新功能**（issue 评论里已建议另开 issue），不塞进本
  bug 修复刀；
- 自定义头像图片上传：同上，独立 feature。

## 三、实现要点

- **优先级链对齐而非复制**：经典窗的 `_apply_character_theme` 补成与现代窗同一条
  `alias or manifest or id` 链——两窗从此不会漂移；不抽公共基类（两窗 widget 结构
  差异大，强抽是过度设计）。
- **头像三态**：实时画面（桌宠在屏）→ 显示名首字母（桌宠隐藏/未接）→ 兜底 "宠"
  （`_initial` 既有口径）。画面态清空彩色圆底（方图压圆色块会穿帮）。
- **广播收敛一条 seam**：两个改名入口（legacy `PetWindow._rename_character` /
  overlay `sprite_menu_facade.rename_character`）→ `PetInstance.refresh_chat_identity`
  → 两窗 `refresh_character_identity()`（内部即 `_apply_character_theme`，标题头像
  一次到位）。回调注入沿用 `on_switch_character` 的 `_slot_wrap` 模式，不新增信号、
  不新增轮询。

## 四、性能分析

**方法（可复现）**：改动只落在窗口构造、`_apply_character_theme`（构造/换角色/换主题/
改名时各一次）与改名入口，无热路径。`.scratch/chat-alias/` 探针对关键路径计时。

| 指标 | 实测 | 归属 |
|---|---|---|
| 经典窗构造（含别名读取 + 头像字母回退） | ~2.1 s（offscreen 测试 10 例全组 2.11 s 含构造+断言；与修复前同量级——只多了 `character_alias` 一次 dict 读） | 新增路径成本 ≈ 一次 dict get |
| `refresh_character_identity`（改名广播单窗） | 微秒级（重跑一次 `_apply_character_theme`，其中 manifest 重读是既有行为） | 仅改名时触发（用户操作频度） |
| 头像实时画面路径 | `icon_pixmap(42)` = 既有托盘图标同款（`_crop_icon_pixmap` 缓存帧裁剪），改名/切角色/建窗时各一次 | 复用既有，无新解码 |
| 稳态（聊天窗开着、无改名） | **零新增调用**：无定时器、无轮询、无信号常驻 | — |

**结论**：① 稳态零开销（无任何常驻路径）；② 新增路径只在窗口构造与用户改名动作上，
绝对成本一次 dict 读 + 至多一次帧裁剪缩放；③ 无新增系统调用/网络/磁盘/线程；④ 无内存
增长（不缓存新状态，`character_name` 是既有字段）。

## 五、实机运行记录

**环境**：本机真实 Windows（win32 10.0.22631，真实 `windows` 平台插件）、v4.4.0 基线。
命令：`python .scratch/chat-alias/probe_real_classic_alias.py`，输出（截图三张在
`.scratch/chat-alias/`：`classic_alias_letter.png` / `classic_alias_live.png` /
`modern_alias_letter.png`）：

```text
[1] 经典窗标题: '小小萱 · AI 对话'          ← issue 报告的"实际"已变"期望"
[1] 经典窗头像字母(无桌宠): '小'             ← 不再是 'S'
[2] 经典窗头像=实时画面: True（字母: ''）     ← 接真桌宠后头像吃到实时帧（icon_pixmap 原生返回，未走注入兜底）
[3] 改名后标题: '小萱萱 · AI 对话'           ← 已开窗即时刷新，未关窗重开
[4] 现代窗标题: '小萱萱 AI' / 头像字母(无桌宠): '小'  ← 现代窗回退口径同步修正
[5] 清别名后标题: 'shenshen · AI 对话'      ← 恢复默认口径逐字不变（红线）
```

**边界与失败路径**：桌宠隐藏/未接（`icon_pixmap` 空图）→ 字母回退由探针 [1]/[4] 覆盖；
清别名回退由 [5] 覆盖。**无法自动验证**：junction 迁移数据目录（报告者环境）——本刀
不触碰任何路径解析，别名读取走 `Config` 既有通道（现代窗在同一环境正常即证明通道
无恙），不以沉默代替结论故在此如实登记。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests` | All checks passed |
| 聚焦（新文件） | `pytest -q tests/test_chat_classic_alias.py` | **10 passed**（实现前 7 failed + 3 passed——3 条绿的是"无别名侧"护栏，保护不许变的回退口径） |
| 聊天族 | 11 个 test_chat_* 文件 | 360 passed / 1 skipped |
| 窗口与菜单族 | test_agent_link / test_menu_layout / test_sprite_menu_parity | 361 passed / 1 skipped |
| 受影响族高负载复跑 ×3 | 聊天别名/共享行为/分页 3 文件 | 43 passed × 3（4.4~4.5 s/轮） |
| 全量 | `python -m pytest -q` | **9064 passed / 25 skipped / 0 failed**（1183.10 s） |

## 七、已知限制与后续

- 头像实时画面依赖 `pet_link` 已接桌宠；桌宠后接上时头像不自动补画（与 modern 窗
  既有口径一致，下次 `_apply_character_theme` 时补齐）；
- 现代窗"像 QQ/微信每条消息显示头像和名字"、"我的气泡偏窄"、自定义头像图片——
  已在 issue #370 回复中建议报告者另开 feature issue，不在本刀；
- 改名广播只到聊天窗；灵动岛等其它读 `character_alias` 的面（`dynamic_island`）本就
  按自己的刷新节奏读，未动。

## 八、风险与回滚

- 影响面：聊天窗标题/头像呈现 + 两条改名入口各一行广播；无配置键变更、无迁移。
- 回滚：`git revert` 单提交即可；别名数据（`character_aliases`）本就存在，回滚只是
  经典窗回到"不显示"的旧观感，无残留状态。

---

**GATES 豁免说明**：本刀不新增/移动/删除任何持久设置键、不改设置页，不属于
`docs/SETTINGS-CHANGE-GATES.md` 的适用范围，故无变更记录条目；issue 回复中已把
相关体验诉求引导为独立 feature issue。
