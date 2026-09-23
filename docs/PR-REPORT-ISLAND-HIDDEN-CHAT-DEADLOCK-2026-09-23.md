# PR 报告：纯桌宠版岛隐藏死锁修复（hidden_chat 路由回退）

> **基线**：`7d02ea3`（Merge PR #182）
> **分支**：`fix/island-hidden-chat-deadlock`　**日期**：2026-09-23
> **范围**：5 个文件（实现 3、测试 2）
> **关联**：纯桌宠版用户实机反馈——桌宠经灵动岛隐藏后单击岛无任何反应，无法恢复

## 一、核心特性

纯桌宠版（打包时排除 `pet.chat` 的构建变体）存在一个恢复死锁：
「隐藏时对话气泡」开关**默认开启**（`config.py` `hidden_chat: True`），
桌宠隐藏后单击灵动岛会被路由到对话气泡（`chat_requested`），但该构建
没有聊天模块，App 侧 `_show_island_chat` 在可用性检查后静默返回——
气泡不存在、快捷卡片被路由绕过、卡片上的「显示桌宠」入口随之消失，
桌宠永远无法恢复。

修复：灵动岛新增「本构建是否具备聊天能力」开关，无聊天能力的构建里
上述单击路由**回退为展开快捷卡片**，恢复入口由卡片的「显示桌宠」按钮
承接；设置页在无聊天构建里不再展示该死路开关。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 死锁解除 | 纯桌宠版 + hidden_chat 开 + 桌宠隐藏 → 单击岛展开快捷卡片（含「显示桌宠」） |
| 2 | 行为不变 | 有聊天模块的构建维持原路由（隐藏时单击弹对话气泡），逐条测试对照 |
| 3 | 设置页收口 | 无 `pet.chat` 的打包变体不展示「隐藏时对话气泡」开关 |

**红线 / 不变量**：有聊天构建的 hidden_chat 路由语义不得改变；无聊天
构建建岛不得抛异常（既有 `test_island_survives_no_chat_packaging_variant`
继续绿）；老用户已开启的开关不需要配置迁移（运行时回退兜住）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/dynamic_island.py` | +17 / −2 | `__init__` 新增 `_chat_available=True`（默认维持现状）；新增 `set_chat_available()`——无聊天构建置 False 的语义注释即死锁机理；`mouseReleaseEvent` 的 hidden_chat 分支追加 `and self._chat_available` 条件，不满足时落入 `else: expand_card()` |
| `pet/app.py` | +4 / −0 | 建岛后立即 `set_chat_available(bool(getattr(self, "enable_chat", True)))` 喂入构建能力；`getattr` 沿用既有惯例兼容 `__new__` 测试桩（property 内 AttributeError 取默认 True） |
| `pet/modern_settings_dialog.py` | +16 / −2 | 新增 `_chat_feature_available()`（与 app.py 的 ChatService ImportError 守卫同判定口径）；「隐藏时对话气泡」SettingRow 改为按构建变体条件收录——无聊天构建不展示死路开关 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_dynamic_island_revamp.py` | +45 / −0 | red-green 两条：`set_chat_available(False)` + 桌宠隐藏 + hidden_chat 开 → 单击发 `card_expanded` 且不发 `chat_requested`（修复前反过来，复现死锁）；`set_chat_available(True)` 对照组维持气泡路由 |
| `tests/test_architecture.py` | +6 / −1 | `modern_settings_dialog.py` 行预算 2347 → 2357，按注释史追加 2026-09-23 校准记录（+10：判定助手 12 行、条件收录净 3 行，实测 2357） |

### 未改动（可选但推荐）

- `pet/config.py`：`hidden_chat` 默认值保持 True——有聊天构建的默认
  体验不变；无聊天构建由运行时回退兜住，无需改默认值做迁移。
- `pet/island_chat.py`：气泡本体无缺陷，问题是路由层把无聊天构建也
  放了进来。

## 三、实现要点

死锁涉及三方：岛（路由决策）、App（气泡可用性）、设置页（开关呈现）。
修复点选在**路由决策处**（岛侧能力开关）而不是 App 侧——App 侧
`_island_chat_available()` 本来就返回 False，但它只能"不弹气泡"，
无法把交互还给快捷卡片；只有在岛侧把路由条件补上能力判据，控制权
才能落回 `expand_card()`。备选方案「无聊天构建把 hidden_chat 默认值
改 False」被否：设置页仍显示开关会误导，且影响有聊天构建的默认体验。

## 四、性能分析

- 稳态开销：**零新增路径成本**。`set_chat_available` 只在建岛时调用
  一次；单击路由只多一次布尔属性读（`_chat_available`），无新系统
  调用/网络/磁盘/线程。
- 设置页：`_chat_feature_available()` 在装配时执行一次模块导入检查
  （`pet.chat.service` 在有聊天构建里本就会被导入，import 缓存命中；
  无聊天构建是一次失败的 import 查找，微秒级），随后行是否收录是
  一次性列表构造，无运行时成本。
- 内存：无增长（一个布尔实例属性）。
- 触发频率：能力判定仅建岛/设置页装配各一次，单击路由逐事件一次
  布尔读，均可忽略。

## 五、实机运行记录

本修复的正确性核心是「无聊天构建的路由回退」，纯桌宠版构建需要
打包排除 `pet.chat` 的完整产物才能 1:1 复现用户环境；本次以两条
offscreen 集成测试在**真实 DynamicIsland 实例 + 真实鼠标事件序列**
（`_click` 走 press→release 全路径）上复现并验证：

- red（修复前）：`set_chat_available(False)` 不存在、路由条件无能力
  判据 → 单击发 `chat_requested`、不发 `card_expanded`——即用户遇到
  的死锁形态；
- green（修复后）：同条件单击发 `card_expanded`、不发
  `chat_requested`；对照组 `set_chat_available(True)` 维持
  `chat_requested` 气泡路由；
- 已有 `test_island_survives_no_chat_packaging_variant`（`sys.modules`
  排除 `pet.chat.service` 模拟纯桌宠构建）继续绿：建岛不抛异常；
- 门禁：ruff 全绿；岛 + 设置相关族 155 passed；全量 **2913 passed /
  12 skipped**（`_plan/current/_full_island_deadlock.log`；唯一的
  行预算红已按注释史校准后复绿，见 tests/test_architecture.py 记录）。

不能自动验证的部分：纯桌宠版打包产物里的端到端点击恢复（需要完整
打包环境）——运行时回退已由上述测试在真实岛实例上锁定路由行为，
打包变体与测试注入的 `set_chat_available(False)` 在代码路径上完全
同构（`app.py` 建岛喂入即 `enable_chat`）。
