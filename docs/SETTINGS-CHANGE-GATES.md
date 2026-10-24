# 设置变更准入与准出标准

本文件是 Settings System 的变更门禁。适用于新增、移动、重命名、删除或改变任何持久设置，以及修改设置页布局、保存语义、平台可见性和依赖关系。

## 准入：什么时候值得进入设置页

候选项必须同时满足：

1. **它是偏好**：表达用户希望应用以后持续采用的行为，而不是一次性命令、当前状态或诊断信息。
2. **低频且跨任务**：用户无需在主要交互过程中频繁切换；高频命令应留在右键菜单或任务现场。
3. **有可靠默认值**：未配置时产品仍能安全、可预测地工作；默认值适合多数用户。
4. **归属唯一**：能归入一个稳定 `domain_id/group_id`；其他入口只提供搜索别名或深链，不复制开关。
5. **值得让用户决策**：产品无法可靠自动推断，操作系统也没有更合适的全局控制。
6. **契约完整**：已定义支持能力、平台差异、依赖项、保存时机、迁移和恢复策略。

以下内容默认不准入：

- “播放一次”“立即隐藏”“检查更新”等命令；
- 可由应用稳定推断的实现参数；
- 仅用于暴露内部调试状态的开关；
- 操作系统已经统一管理的 DPI、系统字体或全局辅助功能；
- 没有稳定默认值、迁移方式或失败恢复路径的实验参数。

确需暴露的专家参数进入一层 `advanced` 披露，并提供风险/效果说明。实验功能必须有明确生命周期、默认关闭、兼容策略和移除条件。

## 准入记录

每个设置在注册表中至少声明：

```text
setting_id
domain_id / group_id
title / description / search_aliases
default
capability_requirement / platform_availability
disclosure_level             # primary | advanced
dependency
preview_target
commit_policy                # immediate | on_finish | confirm
migration
recovery
```

新增一级能力域还需证明：它代表长期独立用户目标，预计包含至少两个稳定设置组，且放入现有域会显著降低可发现性。平台名称、代码模块和短期功能品牌不能单独构成能力域。

## 准出：修改设置页何时可交付

### 1. 契约

- 归属、文案、默认值、依赖、能力条件、披露层级和保存语义均已评审。
- 配置向前/向后迁移可解释；旧 key、旧入口和搜索别名有明确去向。
- 失败时有默认值、回滚或安全入口，不会让用户无法重新打开设置或退出应用。

### 2. TDD

- 每个行为切片先出现能因缺少该行为而失败的 focused test，再以最小实现转绿。
- 纯规则从公开领域 seam 测试；Qt 行为从设置窗口或实际菜单 seam 测试，不断言私有调用顺序。
- 至少覆盖默认值、持久化 round trip、迁移、依赖、平台 capability matrix、搜索/深链、保存策略和恢复路径。
- 相关测试与全量 `python -m pytest -q` 通过；`git diff --check` 通过。

### 3. 布局与可访问性

- `compact / standard / wide` 内容宽度下无裁切、遮挡、不可达控件或错误滚动。
- 系统字体、放大字体、中英文长文案和 High DPI 下布局按字体度量增高或换行。
- Tab 顺序、键盘操作、可见焦点、accessible name/description 和焦点恢复可用。
- 明暗主题的文字、边界、禁用态、hover/pressed/focus 状态可辨认。
- 动效有明确用途、可中断并尊重系统 reduced-motion 能力；设置表单不使用破坏阅读顺序的瀑布流。

### 4. 跨平台

- macOS、Windows、Linux 共享 setting ID、默认值、依赖和保存语义。
- 永久不支持的能力不创建控件；暂不可用的能力保留并解释原因。
- capability fake 覆盖三端组合；至少在当前可用平台完成真实 GUI 验收。
- 交付说明区分“真实平台已验证”和“模型/CI 已覆盖”，不推断未执行的平台结果。

### 5. 视觉与文档

- 受影响页面和菜单有代表性截图：默认、长文本、窄宽度、平台条件和错误/禁用态。
- 实时预览与最终呈现使用同一模型，不维护演示专用副本。
- 改变 Shared UX Contract、Settings System 或 Menu Action Model 时更新 `CONTEXT.md`；只有长期且非显然的工程约束才进入 agent 文档。

满足以上全部适用条目才可交付；不适用项需在变更记录中说明原因。

## 变更记录

### 2026-10-25 — 新增全局键 `bubble_click_through`（「气泡鼠标穿透」行）

本刀**新增 1 个持久设置键与 1 行设置项**（气泡独立于桌宠本体的鼠标穿透，issue #367），
不改任何既有键的默认值、归属与保存语义。证据见
[`PR-REPORT-BUBBLE-CLICK-THROUGH-2026-10-25.md`](PR-REPORT-BUBBLE-CLICK-THROUGH-2026-10-25.md)。

| 项 | 本刀口径 |
|---|---|
| setting_id / 行键 | `bubble_click_through`（行键与配置键同名，`Config._initial_data` 默认 `False`） |
| 归属 | 「互动」域 →「输入」组，紧随「鼠标穿透」行（用户找气泡穿透会先看整宠穿透那一行） |
| 标题 / 说明 | 「气泡鼠标穿透」/ 说明写明四件事：点击穿透到下层、本体保持可交互、与整宠穿透相互独立可任意组合、审批/提问按钮弹出期间自动恢复可点且穿透期间点气泡不再触发快速对话（点本体仍可）；仅 Windows |
| default | `False`（**关**）。关 = 气泡代码从不调用 Win32 穿透原语（测试钉死），行为与旧版逐位一致 |
| 保存时机 / commit_policy | `immediate`（随设置窗关闭的既有 `_write_config` 写回，不新增写盘路径）；legacy `refresh_pet_settings` 与 overlay 壳三注入点热改即时生效，无需重启 |
| capability / 平台可用性 | **仅 Windows 创建控件与行**（`WS_EX_TRANSPARENT` 是 Windows 原生样式；非 Windows 无独立穿透原语——同 `cursor_hidden_passthrough` 的平台闸门口径，不留"改了没反应"的活开关）。行键与配置键同名 ⇒ 默认值标记走"同名回落"自动覆盖，无需登记 |
| 依赖 | 与 `mouse_through`（整宠穿透）**相互独立**；与捕获兼容模式（`stream_capture_mode`）互斥生效——气泡是主窗子控件、无独立 HWND 时整个开关静默不生效（不写主窗样式） |
| 迁移 | **新键**：老配置没有它 → 取默认关（升级用户呈现不变）；`Config.reload` 白名单与 `_bool_or_default` 归一同步登记（`tests/test_config_schema.py` 快照 + `tests/test_bubble_click_through.py` 钉住字符串脏值不误开） |
| 搜索 | 口语别名「气泡穿透」→ `bubble_click_through`（label 是「气泡鼠标穿透」，子串匹配认不出省掉"鼠标"的半截说法）；关键词「穿透到下层」经 hint 命中 |
| 恢复 | 无需恢复路径：失败面只有写盘失败（既有设置窗关闭闸门）；隐藏期间改意愿由下次 showEvent 收敛，无半套状态 |
| 不做 | 不给右键菜单/托盘加快捷开关（issue 只要求设置页）；不做非 Windows 的 `WindowTransparentForInput` 接入（重建原生窗口有频闪风险，无真实平台验证）；交互气泡（审批按钮）期间的让位是硬编码行为而非用户开关——按钮必须能点 |

准出对应：

1. **契约**：键、默认值、归属、依赖、平台可见性、披露层级（primary）、保存与生效时机
   逐条见上表；新键无旧键/旧入口/旧别名迁移问题；失败路径不新增。
2. **TDD**：`tests/test_bubble_click_through.py` 12 例先红（6 failed + 6 errors）后绿，
   覆盖默认关零调用、生效判据三条件、showEvent 重放、捕获子模式、双宿主接线、设置行
   默认/round trip/非 Windows 不建行、搜索别名、配置归一；schema 快照同步。
3. **布局与可访问性**：无新增控件类型（复用 `ToggleSwitch` 与 `SettingRow`）；
   「输入」组追加一行，组名与行序契约不变；说明全文入搜索索引。
4. **跨平台**：产品代码平台分支只有既有的 `sys.platform == "win32"` 闸门；
   ubuntu/macos CI 腿用 `sys.platform` 替换用例覆盖"零调用、不建行"两条契约；
   真实 GUI 验收仅本机 Windows（真实平台命中路由探针五态全对，见 PR 报告 §五），
   macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；PR 报告与 `docs/INDEX.md` 登记同步。

### 2026-10-21 — 新增一级域「功能」（功能模块启用/停用卡片，staged 语义）

本刀**新增 1 个一级能力域与 1 个持久设置的可见入口**（`feature_activation` 键与迁移
语义在期 1 地基里已就位：`pet/features.py` 清单、`pet/feature_activation.py` 快照/
待生效分层，本刀不改它们的 API）；不改任何既有键的默认值、归属与保存语义。
设计稿：`feature-modules` SPEC（§2 架构、§5.2 设置层、§9 已拍决策：侧栏新域
「功能」放「桌面组件」之后 + 「推荐套件」一键勾选）。

| 项 | 本刀口径 |
|---|---|
| 键 | `feature_activation`（`{"version":1,"enabled":[...]}`），**不是新键**：期 1 已进 `Config._initial_data`（默认 `None` = 未判定）、reload 白名单与 `_NULL_ACCEPTING_KEYS`。本刀只是给它第一个 UI 入口 |
| 归属 | 新增一级域「功能」（侧栏放「桌面组件」之后，图标 `template`）。**准入复核**：代表长期独立用户目标（模块级启用/停用，与任何单功能的参数调优不同层），自带 **5 个稳定分组**（生活/声音/智能/联动/系统，跟随 `features.CATEGORY_ORDER`，≥2 组的机器门 `test_new_top_level_domain_must_own_at_least_two_setting_groups` 过）；放现有域会把 14 张卡片埋进别的任务页，可发现性显著下降 |
| 页面形态 | 卡片列表（仿 `character_packs_page` 的卡片语言，复用 `settingsCard`/`settingLabel`/`settingHint` 令牌）：功能名、一句话 desc、开关；徽标 联网 / 可能要花钱/凭据 / 会拉起外部程序（取自 `FeatureSpec.needs_*`）。顶部说明条 + 常驻「重启后生效」提示；快捷按钮「推荐套件 / 全部开启 / 全部关闭」（纯 UI 捷径，只改 pending 集）。**卡片不是 SettingRow**（一个键装 14 个模块的集合，不是逐键偏好），与角色包卡片同口径 |
| default / 迁移 | 无迁移：老用户首次打开 pending 即迁移基线全集（`pending_enabled` 语义），无需迁移提示；合法空集不回退（`[]` 是用户明确选择的纯核心）；未知模块 ID 写回时原样保留（SPEC §4「不执行但保留」） |
| 保存时机 / commit_policy | `immediate` 但 **staged**：卡片开关只改内存 pending 集；`_write_config` 末尾统一 save 前委托 `FeaturesPage.write_to_config`——pending 与盘上 `pending_enabled` 不同才 `config.set`（未改动不 set，避免无谓写盘触发主进程 watcher），不自己 save、不调 `write_enabled`。取消/关窗不保存即丢弃。**生效时机是重启**（激活快照在启动早期冻结，页面如实写「重启后生效；当前正在运行的功能不受影响」） |
| 依赖呈现 | 硬依赖如实显示不强制拦：被勾选功能缺依赖时，在**被依赖者**卡下挂一行「××需要它才能用」（`features.resolve_dependencies` 判 blocked）；保存不拦（blocked 模块运行时自然不可用） |
| 搜索 | 卡片是普通 QWidget，**不进**搜索索引（索引 = `SettingRow` + `AuthorRow`，既有决定）；功能名经各自既有设置行可搜（天气→互动域行、抽签→互动域行等），与「命令类控件不进索引」同一纪律 |
| 侧栏预算 | 能力域 10 → 11：`SIDEBAR_ROW_HEIGHT` 34 → **31**（实测 800×560 下 11×31 余量 17px，与 10×34 的 19px 相当；间距 3 与行距 1 不动——压它们才是「拿所有域的呼吸空间换一个域」）。行高 pins（`test_settings_interaction_tabs` / `test_desktop_pet_features`）与域清单快照同步更新 |
| 恢复 | 无需恢复路径：失败面只有写盘失败，走既有设置窗关闭闸门；损坏配置值在用户未改动时一字不动 |
| 平台 / 变体 | 三端同建（模块清单是静态数据，无平台分支）；no-chat 变体的「本构建不支持」呈现是 SPEC 期 5 范围，本刀不做 |

准出对应：

1. **契约**：键、默认值、归属、披露层级（全部 primary）、保存与生效时机逐条见上表；
   旧键/旧入口/旧别名无迁移问题（键在期 1 已注册）。
2. **TDD**：`tests/test_settings_features.py` 12 例（页面装配 14 卡 5 组、staged 不落盘、
   有改动才 set / 未改动不 set、全部关闭 → 盘上 `enabled=[]` 不回退、依赖提示与不拦保存、
   未知 ID 保留、快捷按钮、取消丢弃）；侧栏/域契约快照（`test_menu_layout` /
   `test_desktop_pet_features` / `test_settings_interaction_tabs` /
   `test_settings_pet_tabs` / `test_settings_sidebar_and_chrome`）随新域同步。
3. **布局与可访问性**：无新增控件类型、无新增 QSS（复用卡片与 `ToggleSwitch` 令牌）；
   开关带 `accessibleName`（「启用<功能名>」）；默认窗口 11 域全部可见
   （`test_default_window_shows_every_domain_without_scrolling` 原判据不变）。
4. **跨平台**：产品代码无新平台分支；真实 GUI 验收仅本机 Windows，macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录。

### 2026-10-19 — 设置页搜索：↑/↓ 逐条翻命中（无新增设置项）

用户反馈「搜索结果不能用方向键上下翻」。审计：`eventFilter` 原本只吃 `Return`/`Enter`，
按回车循环；而浏览器查找栏 / VSCode / Windows 设置搜索都用 ↑/↓ 翻结果。

| 项 | 内容 |
|---|---|
| 交互 | 搜索框内 `↓` 前进、`↑` 后退、`Return`/`Enter` 前进（原行为不变）；第一条按 `↑` 回绕到最后一条 |
| 实现 | `_search_settings(advance: bool)` 改成 `advance: int` 步长（+1 / -1 / 0），取模自带回绕；单行 `QLineEdit` 的 ↑/↓ 没有默认行为，不与光标移动冲突 |
| 影响面 | 仅键盘交互，不新增/移动/删除任何持久设置键，配置 schema 零改动 |
| 测试 | `tests/test_settings_search_aliases.py` 新增 2 条（↓ 前进两条再 ↑ 退回；第一条按 ↑ 回绕到最后）；产品代码退回 HEAD 时两条都真红（计数卡在 `1/`） |

### 2026-10-18（第二轮）— 设置页搜索：Ctrl+F 聚焦 + 索引纳入「更新」页作者行（无新增设置项）

用户反馈「设置页找功能有点麻烦」。审计结论：搜索**本来就有**（侧栏搜索框 → 跳页 +
高亮 + 回车翻页），缺的是"不用鼠标"和"索引不全"，不是缺一层导航。

| 项 | 内容 |
|---|---|
| Ctrl+F | 设置窗内任意位置把焦点送进侧栏搜索框并全选（`QShortcut(StandardKey.Find)`，作用域 `WidgetWithChildrenShortcut`）。**不加任何新的过滤/隐藏行为** |
| 索引补全 | `_search_rows` = 标准设置行 + 「更新」页作者账号行（`settings_about.AuthorRow` 按 `SettingRow` 同形暴露 `label`/`hint_label`，排版不变） |
| 明确不做 | ①真过滤（只留命中行）——跳转式搜索更不容易把人困在空列表里；②侧栏命令类控件（恢复默认/保存并退出）进索引——既有决定，见本文件 2026-10-17 条 |
| 明确不做（提案、待用户定） | 把「语音」域拆成两个页内标签（43 行 / 单卡 28 行 / 内容高 2056 vs 视口 454）、「自动化与联动」拆标签（60 行 10 组）——数据支持，但会动标签契约与组描述覆盖，等用户点头 |
| 行数预算 | `modern_settings_dialog.py` 按惯例校准到实测（带日期注释） |

### 2026-10-18 — 新增「互动 · 快捷键」页内标签 + 3 个全局快捷键键（默认全空）

用户需求原文：「可以给 AI 对话 / 看看屏幕 / 截图提问这三个功能增加快捷键吗？默认空，
需要用户自己设置。」

| 项 | 内容 |
|---|---|
| 新增键（3） | `hotkey_open_chat` / `hotkey_look_screen` / `hotkey_screen_capture`，**默认 `""`（不绑）** |
| 作用域 | **全局**（主 `config.json`）：三个动作都是进程级的，不随每只宠不同；不进 `PER_PET_KEYS`（进了就是双真值，全局写盘会无条件剔除） |
| 归属 | 「互动」域的**第五个页内标签「快捷键」**（不新开一级域：侧栏在 800×560 下只剩 ~17px 余量，且新域须 ≥2 稳定组） |
| 默认安全性 | 空默认 = 未注册任何系统级热键，行为与加这个功能之前**逐位相同** |
| 归一化 | `pet/hotkey_spec.normalize()`：顺序/大小写统一，不认识或残缺一律清空——配置里不存脏值 |
| 必填判据 | 必须含至少一个修饰键（Ctrl/Alt/Shift/Win）：无修饰的系统级热键会抢走普通打字 |
| 独占失败 | Windows 全局热键表是独占的；`RegisterHotKey` 失败记 warning 并让该项不生效，不假装成功 |
| 平台 | 只有 Windows 能注册；其它平台设置项照常可见可存，但不注册（`hotkeys.is_supported()`） |
| 实现面 | 新增 `pet/hotkey_spec.py`（纯文本口径）/ `pet/hotkeys.py`（Win32 注册 + `WM_HOTKEY` 分发）/ `pet/settings_hotkeys.py`（三行）；`pet/platform_win.py` 加两个 ctypes 封装；`pet/app.py` 三处接线（启动装、外部配置变更重注册、退出注销） |

不复制动作实现：三个回调分别落到右键菜单同一条入口（`PetInstance.open_chat` /
`OverlayShell.look_at_screen` / `OverlayShell.on_screen_capture`）。

### 2026-10-17（第二轮）— 「互动 · 抽签」标签改名「趣味」+ 新增一级域准入守卫（无新增/删除设置项）

本刀**不改任何配置键、不改默认值、不改设置行**，改的是**页内标签名**与**准入判据的执行方式**。

| 项 | 内容 |
|---|---|
| 标签改名 | `pet/settings_interaction.py` 的 `TAB_LABELS` 第四项由「抽签」改为「**趣味**」。理由：她 2026-10-15 的归类要求是「抽签这个功能…归类为趣味」。**卡片/分组名仍是「抽签」** |
| 为什么不复开一级域 | 本文 2026-10-17 的首条记录已判【趣味】一级域不合格（新域须 ≥2 稳组、且为塞第 11 个域压缩侧栏间距会损害所有域的点击目标）。**标签名与容器层级是两件事**：改名不必重新过一级域准入 |
| 新增机器守卫 | `tests/test_settings_sidebar_and_chrome.py::test_new_top_level_domain_must_own_at_least_two_setting_groups`：逐域数 `SettingsSection`，**新增**一级域 <2 组即红；现存单组域（`文件识别`、`更新`）走快照豁免，并反向钉住"它们仍是 1 组" |
| 守卫有效性 | 按「旧红新绿」自证：临时把快照里的「更新」移出 → **1 failed**；恢复 → **1 passed** |
| 文档校正 | `docs/DEV-HANDOVER.md` §3.1 行数预算表按实测刷新（`window.py` 4671 / 4529；`modern_settings_dialog.py` 2535 / 2535） |

准出对照：契约（无新键、无迁移）✓；TDD（守卫先红后绿已证）✓；布局（默认 800×560 仍无滚动、侧栏仍 10 域）✓；跨平台（纯 Qt 文案）✓；视觉与文档（本节 + PR 报告 + `INDEX.md` 登记）✓。

### 2026-10-18 — 新增全局键 `todo_reminder_speak`（「待办提醒 → 语音播报」行）

本刀**新增 1 个持久设置键与 1 行设置项**（待办到点是否念出来），不改任何既有键的
默认值、归属与保存语义。证据见
[`PR-REPORT-TODO-VOICE-BROADCAST-2026-10-18.md`](PR-REPORT-TODO-VOICE-BROADCAST-2026-10-18.md)。

| 项 | 本刀口径 |
|---|---|
| setting_id / 行键 | `todo_reminder_speak`（行键与配置键同名，`pet/config.py::Config._initial_data`） |
| 归属 | 「自动化与联动」域 →「待办提醒」组第三行（排在总开关、提前量之后）。面板 `pet/todo_panel.py` **不复制控件**，底部深链文案补上「语音播报」 |
| 标题 / 说明 | 「语音播报」/「到点时把提醒念出来；音色 / 语速 / 音量与「语音报时」共用同一套（设置 → 语音），本项只控制开与关。免打扰期间不出声，也不在结束后补播。」 |
| default | `False`（**关**）。主动打扰型能力默认关，与 `voice_chime_enabled` / `festival_reminder_speak` 同口径；关着时用户可见行为与改动前逐字相同 |
| 保存时机 / commit_policy | `immediate`（随设置窗关闭的既有 `_write_config` 写回，不新增写盘路径）；保存后 `AppShell._sync_chime_service()` 使音频通道路由即刻生效，无需重启 |
| capability / 平台可用性 | 三端都创建，无平台分支。**实际出声**取决于合成后端是否可用（同语音报时：edge 需网络、SAPI 需本机语音包）；后端不可用时只降级成"有气泡没声音"，不影响提醒本身 |
| 依赖 | 依赖待办提醒总开关 `todo_reminder_enabled`（关掉时待办服务不跑，本键自然无效——`AppShell._todo_speak_wanted()` 是两个键的合取）；音频通道路由复用「语音报时」的音色 / 语速 / 音调 / 音量，**因此不新增任何语音参数键** |
| 迁移 | **新键**：老配置没有它 → 取默认关（升级用户呈现不变）；`Config.reload` 白名单与 `_bool_or_default` 归一同步登记（`tests/test_config_schema.py` 的两份快照钉住，字符串 `"false"` 不会被误开） |
| 恢复 | 无需恢复路径：失败面只有写盘失败（走既有设置窗关闭闸门）与"通道拿不到"，后者只丢声音不丢提醒 |
| 披露层级 | `primary`（就在「待办提醒」这一组里，不进任何折叠） |
| 默认值标记 | 行键与顶层配置键同名 ⇒ 按 `settings_default_marks` 的既有回落规则自动可比默认值，勾选后出现「已修改」标记与单项重置入口，无需登记新映射 |
| 通道生命周期 | `AppShell._chime_wanted()` 增加第五个消费者 `_todo_speak_wanted()`。**不接这一处就是死开关**：每次设置保存 `_sync_chime_service()` 都会把通道 `stop()` 并置 `None`，用户勾了开关却一声不响（同 2026-10-17 抽签语音那条产品阻断） |
| 文案单一来源 | 气泡与语音共用纯函数 `todo_reminder.fire_text()`，两处不会漂移；**不做**语音专用文案（也就没有文案模板键） |
| 免打扰语义 | 免打扰（含全屏等效）拦下的那一档**不出声、也不在结束后补播**：过期提醒突然出声比不提醒更糟，与通道 `speak(respect_quiet=True)` 的"入队即丢弃"一致 |
| 不做 | 不新增语速 / 音色 / 音量 / 音量倍增键（归属唯一：那三项是音频通道级真值）；不做语音专用文案模板；不做"只在桌宠不可见时才念"这类第二套触发规则；面板不加开关 |

准出对应：

1. **契约**：键、默认值、归属、依赖、平台可见性、披露层级、保存时机逐条见上表；旧键/
   旧入口/旧别名无迁移问题（新键）；失败路径（通道缺失、合成后端不可用）只降级为
   "有气泡没声音"。
2. **TDD**：`tests/test_todo_reminder.py` 先红后绿 6 例（开关关静音、开关开与气泡逐字
   一致、免打扰不补播、配置 round trip + 字符串布尔、builder 单源、设置页保存后
   `_chime_wanted()` 为真）；红证据为 `3 failed / 3 passed`（那 3 条绿的是"默认关"一侧
   的护栏），转绿后 `6 passed`。配置侧由 `tests/test_config_schema.py` 的两份快照 +
   归一化路径覆盖；`_chime_wanted()` 的既有五消费者用例族（
   `tests/test_voice_chime_service.py`）原样全绿。
3. **布局与可访问性**：无新增控件类型、无新增 QSS（复用 `ToggleSwitch` 与 `SettingRow`）；
   组内追加一行，行序断言（`tests/test_settings_page_guide.py` 的「待办提醒」组）不改；
   说明里写明"免打扰期间不出声、不补播"，搜索与 `accessibleDescription` 都带全文。
   `modern_settings_dialog.py` 行数预算按实测 2535 → **2539**（带日期注释，未静默放宽）。
4. **跨平台**：产品代码无新增平台分支；真实 GUI 验收仅本机 Windows，macOS / Linux 未跑
   （不推断）。
5. **视觉与文档**：本记录即变更记录；PR 报告
   [`PR-REPORT-TODO-VOICE-BROADCAST-2026-10-18.md`](PR-REPORT-TODO-VOICE-BROADCAST-2026-10-18.md)
   与 `docs/INDEX.md` 登记同步。

### 2026-10-17 — slot0 迁移「源清理」口径回正：清源与 `Config.save` 同一条规则，不丢数据改由备份承担

本刀**不新增/不删除任何配置键、不改默认值、不改设置页**；改的是**迁移与保存语义**
（`migrate_slot0_profile` 在目标档案已存在时的源清理口径），属本文适用范围。

| 项 | 本刀口径 |
|---|---|
| 被修的口径 | 2026-10-16 的 #289 把 `already-exists` 支改成"**不清源**"（源每宠键原样保留、登记在 `retained_keys`）。实测该口径**在盘上不成立**：`Config.save()` 对全局身份**无条件** `pop(PER_PET_KEYS \| SLOT_LOCAL_KEYS)`（`pet/config.py:2328`），下一次保存就把源里那批键抹掉——`retained_keys` 描述的是"保留"，用户拿到的是空配置（已用真 `Config.save()` 跑通复现）。而全局配置 + 槽档案同时装每宠值 = 规范禁止的**双真值** |
| 回正为 | `created` 与 `already-exists` **都清源每宠键**（全局 `config.json` 不留每宠键，只有一条规则）；`already-exists` 照旧**绝不写目标**（锁内复查 + `test_slot0_migration_never_overwrites_existing_profile` 逐字节钉死）。与 2026-10-10 那条"验证既有目标后清源"一致，也就是**回到 main 的既有口径**——`origin/main`（`056dc2a`）的 `pet/pet_profiles.py` 与合并基 `6a68570` 逐字节相同，"不清源"只是这个 PR 自己的改动 |
| 不丢数据由谁承担 | **备份**。`_backup_file` 在这条支路之前跑（`config.json.pre-slot0-migration.bak`，已存在则保留第一份、不覆盖）。清理前再验一次"这份备份**装的就是即将被清掉的那批值**"（`_backup_holds_values`，值逐条相等——比"键在不在"强）；若规范备份路径已被**另一份快照**占用（旧快照也能过键存在性校验），**不覆盖它**，另存时间戳恢复副本 `config.json.pre-slot0-migration-recovery-<毫秒>-<pid>.bak`，结果走新增字段 `recovery_path`。取不到恢复副本就**不清源**并返回 `backup-failed`——宁可不做，也不做不可恢复的删除 |
| 相对 main 的净增量 | **只有上面"恢复保证"那半边**（清源口径与 main 一致）。实测对照：同一现场跑在 `origin/main` 上，`already-exists` 同样清源、同样不覆盖旧备份，但**没有恢复副本**（连 `recovery_path` 字段都不存在）⇒ 规范备份被旧快照占用时当前每宠键**真丢 6 个**；本刀把这条路堵上 |
| 可诊断 | `already-exists` 结果：`retained_keys` = 未采用的源每宠键、`reason` 点名备份/副本文件名、另有一条 WARNING 写明路径与键名 |
| **明确不做** | 不引入任何新的持久键、不引入 `retain`/迁移框架；不改 `Config.save()` 的 `pop`（那是单真值的落地处）；**不**把源几何"补进"既有档案——「不覆盖目标」是已发布契约，所以本刀**修不了**"位置落回右下角"（覆盖层落点取自档案，全局 rx/ry 无读者，见下） |

准出对应：

1. **契约**：键/默认值/归属/依赖零变更；改的是保存与迁移语义，逐条见上表。失败路径
   仍走既有词表（`backup-failed` / `backup-invalid` / 锁类），其中 `backup-failed`
   在本刀新增一处触发点（恢复副本写不出）且**两个方向都不动**（源保留、目标不写）。
2. **TDD**：`tests/test_pet_profiles.py` 改写 1 例（原"不清源"契约改成"清源 + 可恢复"）、
   新增 3 例（与真 `Config.save()` 的一致性、旧快照占位时另存副本且不覆盖旧备份、
   取不到副本就不清源且目标不动）；消融（撤掉产品改动）**4 failed**，先红后绿在案。
3. **布局与可访问性**：不适用——本刀无 UI 改动（不新增/移动/删除任何设置行）。
4. **跨平台**：无平台分支（判据是文件与值，与平台无关）；验证仅本机 Windows。
5. **视觉与文档**：本记录即变更记录；同步改了 `pet/pet_profiles.py` 的模块头与函数
   docstring（清理口径、`retained_keys`/`recovery_path` 语义）、
   [`PR-REPORT-ZERO-PET-NOTICE-AND-SLOT0-MIGRATION-2026-10-16.md`](PR-REPORT-ZERO-PET-NOTICE-AND-SLOT0-MIGRATION-2026-10-16.md) §九（原报告 §二/§三 的"不清源"表述已更正）
   与 `docs/INDEX.md` 对应行。

### 2026-10-16 — 新增全局键 `allow_cross_screen`（「允许桌宠跨屏」行）

本刀**新增 1 个持久设置键与 1 行设置项**（多屏能力的总开关），不改任何既有键的默认值、
归属与迁移。

| 项 | 本刀口径 |
|---|---|
| setting_id / 行键 | `cross_screen`（配置键 `allow_cross_screen`，`pet/config.py::Config._initial_data`） |
| 归属 | 常规域（`settings`）→「窗口与系统」组，排在窗口旗标类（置顶 / 全屏隐藏 / 光标穿透 / 直播捕获）之后 |
| 标题 / 说明 | 「允许桌宠跨屏」/ `settings_pet_controls.CROSS_SCREEN_HINT`：开启后每块有桌宠的显示器各开一张窗口、可以拖到别的显示器上；关闭时全部宠只留在启动那块屏、拖不过去也不会多开窗口；**各宠原来记着的屏不会被清掉**，重新开启后仍回到各自那块屏；**重启后生效**。文案末尾逐字带 `GLOBAL_SCOPE_NOTE`，由 `SettingRow.set_scope_note` 削进行内标注（说明表仍是唯一来源），搜索索引与 `accessibleDescription` 都保留全文 |
| default | `False`（**关**）。关 = 单显示器口径与旧行为逐位相同：一张窗、一组控制器、拖不过缝 |
| 保存时机 / commit_policy | `immediate`（随设置窗关闭的既有 `_write_config` 写回，不新增写盘路径）；**生效时机是重启**——壳在构造时把该键**快照一次**（`OverlayShell._cross_screen_allowed`），运行期不重读，与 `stream_capture_mode` 同口径 |
| capability / 平台可用性 | 三端都创建（多显示器不是平台专属）；**非 overlay 拓扑（legacy 单窗 / 捕获模式）下置灰** + tooltip「仅单合成窗（overlay）拓扑生效」——legacy 的 `PetWindow` 自己就是一个真窗口、本来就能被拖到别的显示器，本键在那边没有第二个读点，摆着不置灰就是"改了没反应"的活开关 |
| 依赖 | 无前置开关；关闭态下**临时承载**在启动那块屏上的宠一个几何键都不写（屏归属与归属那块屏上的比例都保留），承载屏 == 归属的宠照旧逐次落盘（比例与有意换屏的屏名同一次原子写）——所以关掉再开回来回到各自那块屏的原位置，而单屏用户的普通拖动持久化不受影响 |
| 迁移 | **新键**：老配置没有它 → 取默认关（升级用户呈现不变）；`Config.reload` 白名单与 `_bool_or_default` 归一同步登记（`tests/test_config_schema.py` 的两份快照钉住） |
| 恢复 | 无需恢复路径：失败面只有写盘失败，走既有设置窗关闭闸门；置灰态下控件不可改，不产生半套状态 |
| 披露层级 | `primary`（就在首屏那一组里，不进任何折叠） |
| 默认值标记 | 登记在 `settings_default_marks`（出厂默认 `False` 可比），因此勾选后会出现「已修改」标记与单项重置入口 |
| 不做 | 不做"关闭时禁止写位置"（那会破坏单屏普通拖动持久化）；不做运行期热切换（重启级是刻意选择，避免勾一下当场多出/收掉窗口）；不把 `screen_name` 的归属语义扩成第二份真值（仍只有档案一处） |

准出对应：

1. **契约**：键、默认值、归属、依赖、平台可见性、披露层级、保存与生效时机逐条见上表；
   失败路径沿用既有设置窗保存闸门；旧键/旧入口/旧别名无迁移问题（新键）。
2. **TDD**：新增 `tests/test_settings_cross_screen.py` 5 例（默认关 + 保存 round trip、
   全局作用域标注与可访问说明、非 overlay 拓扑置灰 + tooltip、默认值标记与单项重置入口、
   四组搜索词命中）；配置侧由 `tests/test_config_schema.py` 的两份快照 + 归一化路径覆盖；
   行为侧（开关如何影响窗口数/跨屏路由/位置归属）在
   `tests/test_overlay_cross_screen_switch.py`（8 例，含**三个进程往返**证重启级与
   关闭→开启 round trip）。消融：撤置灰闸令 1 例红、撤作用域登记令 1 例红、撤壳侧闸门
   分别令 2/3/4 例红（逐条见 PR 报告 §三之五）。
3. **布局与可访问性**：无新增控件类型、无新增 QSS（复用 `ToggleSwitch` 与 `SettingRow`）；
   行序只在「窗口与系统」末尾追加一行（`tests/test_settings_ia_first_cut.py` 与
   `tests/test_settings_compat_layering.py` 的行序断言同步更新）；全局作用域标注挂在
   标签那一行、不额外占行；说明里含"重启后生效"与作用域全文，可搜索。
4. **跨平台**：无新平台分支（三端都建行）；置灰判据是既有的拓扑判定
   （`settings_pet_controls._is_overlay_topology`），不新增 `sys.platform` 分支。
   真实 GUI 验收仅本机 Windows，macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；PR 报告
   [`PR-REPORT-PER-PET-MULTISCREEN-2026-10-16.md`](PR-REPORT-PER-PET-MULTISCREEN-2026-10-16.md)
   与 `docs/INDEX.md` 登记同步；`AGENTS.md` 多屏段落补上开关与屏归属语义。

### 2026-10-16 — 音乐播放器路径：`music_player_paths` 扩到四个播放器（**同一字典，不新增配置格式**）

本刀**不新增配置键、不新增设置页分组、不改保存语义**：`music_player_paths` 仍是同一个
字典键，成员从 2 个（`netease` / `qqmusic`）扩到最多 4 个（新增 `kugou` / `soda`），
**没有新增任何一层配置结构、没有迁移、默认值仍是 `{}` = 走自动搜索**。改的是：设置页该组
多了两行（同一折叠组内）、右键菜单音乐子菜单多了两条「打开…给主人放歌」入口。
证据见 [`PR-REPORT-MUSIC-PLAYER-PATHS-2026-10-16.md`](PR-REPORT-MUSIC-PLAYER-PATHS-2026-10-16.md)。

| 项 | 本刀口径 |
|---|---|
| 键集（准入） | `music_player_paths` 的允许键 = `music_players.player_keys()`（后端注册表键集），**不再写死两键**。归属唯一：主 `config.json` 里的进程级字典，不进 `PER_SLOT_SETTING_KEYS`（四个键同规，子宠页保存由设置进程的配置路由直写主配置） |
| 行键（准入） | `music_player_netease` / `music_player_qqmusic` **一字未改**（存量行键、搜索结果与用户熟悉的位置都保持），新增 `music_player_kugou` / `music_player_soda`；四个行键由注册表派生 ⇒ 注册表加播放器必然有行。行序 = 注册表序，检测行在后 |
| 披露层级 | 四行仍属既有的高级折叠组「播放器路径与标题格式（高级）」（非 Windows 上组名退化为「播放器路径（高级）」）——**披露层级未变**，只是组内多了两行 |
| 默认值与迁移 | 默认 `{}` 未变；**无迁移**：旧配置两个成员原样保留，新成员缺省即「走自动搜索」。回滚方向：把 `kugou` / `soda` 从代码摘掉后，磁盘上这两个成员会作为表外键被 `_clean_music_player_paths` 丢弃（静默清理、不报错，与旧版丢未知键同一行为） |
| 平台可见性 | 与四个歌词源开关不同，**路径行不做平台门**（路径输入四端都有意义；非 Windows 上搜不到即置灰并给出手填入口） |
| 保存语义 | 仍「关闭即保存」；保存时四个 picker 逐键读取，全空 = 回到自动搜索并清一次路径缓存。**保存语义未变** |
| 文案（必须写明的事） | 每个播放器行的说明与检测行说明都必须写明：这些路径**只影响右键菜单的「打开…给主人放歌」**，歌词走系统媒体会话、与本路径无关。理由：歌词取不到的用户最可能来这里改路径，改完还是没歌词——那属于「设置页把用户引到错误的修复路径」。检测行还须写明「每次点击都会重新搜一遍（上次没找到的也会再找）、会覆盖上面各行原有的内容、检测期间你自己改过的行会保留」 |
| 检测交互语义（第二轮） | 「自动检测」从"触发一次幂等预热"改为**显式重扫**：先 `music_players.clear_cache()` 作废旧结果（含负结果），再逐键预热，300ms 轮询只读缓存。三处必须与用户预期一致：① 只回填**从检测开始到现在文本未变过**的行（用户边等边手填的路径不被盖掉，并在结果里说明"已保留你填的内容"；检测前就有的旧路径允许被结果替换并说明"替换了检测前填的 X"）；② 仍在扫 / 超时未完成的格子报**未完成**（可重试），**不能说成「未找到」**；③ 补扫每键每轮有上界 `DETECT_MAX_RESCANS_PER_PLAYER = 2`，耗尽后**不承诺后台还有线程在跑**。**GUI 线程始终零扫盘**（只有后台扫描线程碰文件系统） |
| 检测预算 | 由写死 20s 改为 `DETECT_TIMEOUT_PER_PLAYER_SECONDS (20s) × len(PLAYER_KEYS)` = 80s。四键的浅扫被同一把 `_search_lock` 串行化，本机实测四键齐出 **21.66s（修订 A）/ 24.50s（修订 B）**，**两版都超旧预算**——不放大就会把找得到的播放器报成「未找到」。超时只是兜底：出齐结果立即收尾，不等满预算 |

准出对应：

1. **契约**：无新键 / 无默认值变更 / 无迁移（键集改为注册表派生本身就是"以后不再有键集漂移"的
   契约）；归属、平台可见性、保存语义三项未变；失败路径（手填路径不存在）**不变**：菜单置灰 +
   提示，绝不静默回退自动搜索。
2. **TDD**：`tests/test_music_player_settings.py` 先红后绿（前序执行方的取证日志
   `.scratch/music-player-paths/red-before.txt`：撤掉产品改动时 **19 failed / 13 passed**；
   接线后 **33 passed**，随后按审查补到 **35 passed**）；同批新增/改造
   `tests/test_music_player_detect_refresh.py`（**新文件 631 行 / 11 例**：显式重扫作废旧负缓存、
   检测期不覆盖用户手填、在飞扫描相容与有界补扫、超时区分未完成与没找到、GUI 线程零扫盘）、
   `tests/test_music_player_cache.py`（含注册表、真实安装布局命中、**版本挑选**：`cur_path` 压过更"新"的
   兄弟目录 / 数字版本降序 / 越界与破损描述回退、有界性、手填权威）、
   `tests/test_menu_layout.py`（8 例 + 默认布局期望补两键）、`tests/test_config_schema.py`（4 例，
   含 `reload()` 与重启往返）、`tests/test_sprite_menu_parity.py`（1 例，overlay 侧四条入口）、
   `tests/test_settings_pet_tabs.py` / `tests/test_settings_ia_first_cut.py`（高级行期望清单）。
   **本轮审查未复现红**：产品改动已落工作树（且审查期被同批执行方改过一版，见报告 §〇），
   不允许为取证回退产品代码，故红证据采用执行方日志；绿证据由审查方自己重跑（较早修订聚焦
   269 passed、当前修订聚焦 **327 passed**、ruff 全绿、文档门 160 passed）。
   **全量门 = 已过**：当前修订 `7792 passed / 15 skipped / 0 failed`（`full-final-ready.log`，
   命令见报告 §六）。历史口径已被替代：全量 #1（较早修订）1 failed / 7767 passed，唯一红是
   另一条线的计时 flake（已由 agent1133 改成同事件循环配对判据，独立 review 通过、mutation 能
   抓旧缺陷）；全量 #2 在 74% 处随会话退出被杀、无 summary。两者都不是当前结论。
3. **布局与可访问性**：真实 `windows` 平台插件下三档场景（900×760 明 / 900×760 暗 /
   720×500 + 1.5 倍字体）四行 + 检测按钮全部 `hint_clipped=False`、`inside=True`、
   `h_scroll_visible=False`（几何原文 `geometry.txt`，截图 3 张在
   `.scratch/music-player-paths/shots/`；**这批几何/截图取自修订 A**——行数与行高未变，
   但检测行说明文案在修订 B 改过）——**截图由父级复核**，本记录只登记数字与路径。
   新增行沿用既有 `SettingRow` 行壳与既有高级组，不新增控件形态；**检测的交互语义变过**
   （见上表「检测交互语义」），但它仍收在同一个按钮与同一个说明行里，没有新增控件。
4. **跨平台**：产品代码不新增平台分支（路径行不做平台门是**本刀的选择**，非能力差异）；
   检测的自动搜索与非 Windows 行为逐字节未改；macOS / Linux 真实 GUI **未跑**（不推断）。
5. **视觉与文档**：本记录即变更记录；`README.md` 补用户入口（四个启动入口 + 路径 ≠ 歌词，
   并在「发布状态」里明确标注**本轮候选、尚未发布**）；`docs/INDEX.md` 登记新报告并更新
   `SETTINGS-CHANGE-GATES` 与 `PR-REPORT-MUSIC-MULTI-ARTIST-2026-10-15` 两行的「何时必读」；
   `CONTEXT.md` 无新概念需要登记（"Runtime Disabled" 与"进程级键"都是既有术语）。

### 2026-10-16 — 设置页打磨收口：常规域两处归属调整 + 呈现层（无新增/删除设置项）

本刀**不新增、不删除任何持久设置**：配置键、默认值、归属唯一性、依赖与迁移零改动，
**行键一个都没动**。改的是设置页布局与呈现（本文适用范围里的「修改设置页布局」与
「平台可见性」中的分组可见性），逐项登记。证据见
[`PR-REPORT-SETTINGS-POLISH-2026-10-16.md`](PR-REPORT-SETTINGS-POLISH-2026-10-16.md)。

| 项 | 本刀口径 |
|---|---|
| 「光标隐藏时自动穿透」回主组 | 行键 `cursor_hidden_passthrough` 从"与直播捕获兼容同组"回到「窗口与系统」。理由：它与置顶 / 全屏隐藏同为**窗口旗标类偏好**，用户找它会先看这一组。**披露层级 = primary**（就在首屏那一组里，不进任何折叠）。行本身、文案与平台闸门（仅 Windows 创建）一字未改 |
| 「直播捕获兼容」独立高级组 | 行键 `stream_capture` 从「窗口与系统」拆出，单独成 `SettingsSection("直播捕获（高级）", advanced=True)`。**披露层级 = advanced + 折叠**：组描述只写一句"这一项做什么"（`SECTION_GUIDE`），重启级切换 / OBS 捕获方式 / 平台限制 / 作用域四段仍逐字留在**行内**（展开即见全文），搜索照旧可达（组标题与行内文案都入索引）。非 Windows 上该行不构造 ⇒ 该组在该平台为空、不出现（该平台常规域因此是 3 个区） |
| 跳转条门槛与判据 | 页内跳转条的判据从"显着那一拍的视口"改为"落定几何"，并把页内标签那一层（标签栏 + 容器 spacing）算进内容高。短页不摆跳转条的判据 = 「区数 ≥ 4」且「真实内容高 > 可用高」；真实内容高扣掉页尾留白（布局下外边距），可用高把跳转条自己占的那段加回来（显隐无关 ⇒ 只有一个不动点）。测试侧量法同步改为量**整块滚动内容**，不再拿常规域量临界带 |
| 表单阅读宽度 | 表单内容封顶 `CONTENT_READING_MAX_WIDTH = 800`（默认 800×560 窗口用不到）；「菜单」域自己声明 `contentMaxWidth=1240`。页头宽度改取**滚动视口宽**（有滚动条的页不再比内容宽出一条） |
| 作用域声明的位置 | 原先拼在灰说明末尾的那句（`（对所有桌宠生效）` / `此设置影响所有桌宠。`）拆成 `SettingRow.set_scope_note` 的独立标注：挂在**标签那一行**（不独占一行、不增加行高）。`accessibleDescription` 仍是"说明 + 标注"全文，搜索索引（`settings_search._row_text`）也读标注 ⇒ 搜「所有桌宠」照旧命中。**文案只有说明表一份来源**：`settings_pet_controls.GLOBAL_SCOPE_ROWS` 登记的是"从说明里削掉哪句、标注显示哪句"，不新写文案 |
| 侧栏密度与宽度 | 侧栏布局 spacing 9→6、导航行距 2→1：slot 页有两个重置按钮时 10 个能力域在默认 800×560 下装不下（「更新」被截在视口外）。侧栏宽度由固定 200px 改为**按字体度量取最小值**（`SIDEBAR_MIN_WIDTH` 仍是设计令牌与下限），1.5 倍字体下按钮文案不再被省略 |
| 重置按钮文案按作用域 | slot 页有两个破坏性动作，**两个按钮各自把作用域写在标签上**（「重置本宠设置…」/「重置全局设置…」）——靠 tooltip 区分等于让人猜；全局页只有一个动作，沿用「恢复默认设置…」，不自称"本宠"（那里没有多宠划分）。两个动作、两套确认文案、两个作用域的实现一字未改 |
| 图标呈现 | ① 切主题后按记录（`THEMED_ICON_ROLE` / `themedIconRequest`）重画导航 / 保存并退出 / 重置 / 搜索框 / 各页按钮的图标——图标是位图，换 QSS 不会自己变色；② 选中态 ink 与侧栏 3px 左条共用 `settings_selection_accent(dark)` **一个真值**，两档对选中底 ≥3:1（WCAG 1.4.11 非文本对比）；③ `save` 不再与 `back` 共用字形。右键菜单与聊天窗的图标路径逐字节未变 |
| 运行期应用图标 | 设置窗 `setWindowIcon(load_application_icon())`，图标从 `<_MEIPASS>/assets/icon.ico` 读（源码运行时取仓库根同一相对路径）。三端构建脚本各加一行 `--add-data`：`--icon` 只写**文件图标**，窗口图标不读它；漏这一行构建不报错、图标静默消失，故由 `tests/test_application_icon.py` 的打包契约测试守住 |

准出对应：

1. **契约**：无新键 / 无默认值变更 / 无迁移（`cursor_hidden_passthrough` 与 `stream_capture`
   两个键各自的行、文案、平台闸门逐字未动）；披露层级与分组归属的变更见上表前两条；
   搜索别名、行键、默认值标记覆盖率与依赖语义一字未改；失败路径不新增。
2. **TDD**：新增 `tests/test_settings_sidebar_and_chrome.py`（13 例：默认窗口 10 域全部可见、
   两种窗口尺寸下每域可达、放大字体不被裁、阅读宽度与页头同一条契约、紧凑窗不裁切、
   设置窗带应用图标、图标不可用时仍能开窗、侧栏宽度按策略跳过撑满控件）、
   `tests/test_settings_scope_notes.py`（13 例：声明从说明移入标注、重复调用不吞字、
   全局键行带标注、窗口级注入不丢、搜索仍命中、最坏组合不被压窄、标注不给行加高）、
   `tests/test_settings_theme_polish.py`（19 例：四档主题下四类图标的墨色、浅→深→浅往返、
   选中 ink 与左条同色且 ≥3:1、菜单/聊天窗图标不取选中强调色、每个记录了请求的消费者都
   画成设置窗墨色）、`tests/test_settings_compat_layering.py`（8 例：穿透行回主组、
   折叠组内文案完整、组标题可达、作用域标注可搜、紧凑宽度无横向滚动）、
   `tests/test_application_icon.py`（21 例：`_MEIPASS` 与源码根双候选、缺件/坏件回退空图标、
   无 QApplication 时不构造 QIcon、缓存不跨应用实例、三端脚本真的带了这一行且目标目录等于
   产品常量、多尺寸与生成器一致）。`tests/test_settings_page_guide.py` 新增/改造带用例
   （临界带改用三平台都 ≥4 区的「自动化与联动」、页内标签域两条新用例、量法改为整块滚动内容）。
   先红后绿证据：三端必红（非 Windows 腿模拟）4 failed → 31 passed / 9 skipped；
   撤掉本轮产品改动 2 failed → 40 passed；撤掉上一轮"当场推平再复核" 3 failed；
   撤 QGuiApplication 闸门 2 例红（含子进程真实崩溃 exit 3221226505）。
3. **布局与可访问性**：真实 `windows` 平台插件下四档窗口（800×560 / 1100×760 / 1600×900 /
   720×500 + 1.5 倍字体）× 明暗两档 × 10 个域全量截图（80 张，见
   `.scratch/settings-polish2/`）；默认窗口下 10 个能力域全部可见（「更新」不再被截）；
   标注挂在标签那一行、`setWordWrap(False)`（同一行两个可换行标签会让 Qt 的
   `heightForWidth` 退化成按几何求和，实测能虚高 103px）；最坏组合（最长标注 + 最长标签 +
   1.5 倍字体）在 720 最小窗口下仍有约 15px 余量，由用例盯住。
4. **跨平台**：产品代码新增的平台判据只有运行期图标的路径候选（`_MEIPASS` vs 源码根，
   与平台无关）；`cursor_hidden_passthrough` / `stream_capture` 的 `sys.platform` 闸门
   **一个字没动**（非 Windows 上常规域仍是 3 个区，本轮只让测试不再拿它量临界带）。
   非 Windows 腿用「pytest 初始化之后替换 `sys.platform`」模拟（边界：按 `os.name` 分支的
   模块级常量换不掉，已知且已记录）；macOS / Linux 真实 GUI **未跑**（不推断）。
5. **视觉与文档**：本记录即变更记录；证据报告
   [`PR-REPORT-SETTINGS-POLISH-2026-10-16.md`](PR-REPORT-SETTINGS-POLISH-2026-10-16.md)
   与 `docs/INDEX.md` 登记同步；`ONEDIR_PACKAGING.md` 新增「运行期应用图标」一节；
   `SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md` 补「后继变更（2026-10-16）」记录
   常规域这两处归属调整（历史行不回写）；`CONTEXT.md` 无新概念需要登记。

### 2026-10-12 — 游戏帧率的权限模型改为「一次性加入 `Performance Log Users` 组」（取代下一条）

下一条的「按需 UAC 独立助手」已整体归档（源码、测试、原生工程、随包资源移入
`.scratch/product-fps-uac-20261011/archived-uac-helper/`，产品与打包不再引用）。
原因：常驻提权进程 + 自产未签名原生程序 + 跨进程授权协议，维护面和安全面都远大于
它解决的问题。

| 项 | 本刀口径 |
|---|---|
| 权限模型 | 用户在设置页点「授予采集权限…」→ 系统弹**一次**管理员授权窗口 → 以管理员身份执行 `Add-LocalGroupMember -SID S-1-5-32-559`，把**当前账户**加入 `Performance Log Users`。桌宠本体始终普通权限：**不常驻提权进程、不装服务、不改注册表** |
| 实现 | `pet/fps_permission.py`：按 SID 定位组（与系统语言无关）；**当前用户身份取自进程令牌**（`GetTokenInformation(TokenUser)` 的 SID，不信 `USERNAME`/`USERDOMAIN` 环境变量），SID 过严格正则（只含数字与连字符）才进脚本，脚本里不出现任何用户可控文本；两层 PowerShell 都用 `GetSystemDirectoryW` 下的绝对路径；内外两层走 `-EncodedCommand`；管理员脚本自带执行期限（等待上限前 10 秒），超时后才批准的 UAC 不加组；已是成员视为成功（幂等）；只有原生 `ERROR_CANCELLED` 才算 `cancelled`，其余启动失败单独报错 |
| 设置行 | 灵动岛页帧率段下一行 `game_fps_permission`（**仅 Windows 创建**，跟随帧率开关显隐）。**没有配置键、不写盘**；状态用进程内 API 现查（`CheckTokenMembership` 只认**已启用**的组；`NetLocalGroupGetMembers` 按 SID 查账户实际组成员关系；不起子进程，不卡设置页）。授权任务是设置进程内的后台线程（同一时刻只跑一个），GUI 用挂在对话框上的 `QTimer` 轮询回填。**设置页默认是独立进程**：关掉设置页即结束等待线程（已拉起的 UAC/管理员进程不受影响），重开后以账户实际组成员关系判「待重新登录」，不靠内存；加组幂等，重复授权无副作用 |
| 状态口径 | 三态：已具备（当前令牌含该组）／已授权待重新登录（账户已在组里，跨设置进程判得出，但组成员关系只进**新登录令牌**）／未授权。**不把"已加入"说成"已能采集"** |
| 哪些路径不许弹授权窗口 | 只有用户点那个按钮才弹；开关、开机恢复、切目标、重试一律不弹。权限类采集失败仍是终态不自动重试 |
| 撤销 | 文案写明：在「计算机管理 · 本地用户和组」把自己移出该组 |
| 已验 | `tests/test_fps_permission.py`（SID 校验拒 ASCII/弯引号注入、四种引号全拒、绝对路径、过期检查先于加组、退出码映射、超时 = `unknown` 且不记已授权、进程级状态）、`tests/test_settings_fps_permission.py`（Windows 创建/非支持平台不创建、显隐、三态按钮、成功/取消/错误/未知/异常回填、同进程内授权中关闭销毁再重开接上结果且只跑一次、已结束结果在重建后回填、待重新登录跨对话框保留、状态查询不起子进程、不写配置；`query_status` 用账户实际组成员判跨进程「待重新登录」）；本机真跑：令牌 SID 合法、`CheckTokenMembership` 返回 False、`account_in_group` 返回 False → `missing`（对照：同函数查 Administrators 组返回 True，查 Users 组返回 False），耗时约 8 ms；生成的内外两层脚本经 PowerShell Parser 0 语法错误，过期分支真跑退出码 3 |
| **未验** | 真实提权加组（本机从未执行）、重新登录后真实 ETW 会话、任何真实帧读数；真实独立设置子进程关闭/重开的端到端（单测只覆盖同进程重建 + 组成员真值函数） |

### 2026-10-11（追补，**已被上一条取代**）— 游戏帧率的权限模型改为「按需 UAC 独立助手」

本刀**不动任何设置键、不动设置页布局**，只登记一条**权限模型变更**（设置页文案与交互
口径的输入）。它取代本文件同日游戏帧率条目、以及 `PR-REPORT-GAME-FPS-2026-10-11.md`
早先「把用户加进 `Performance Log Users` 组 / 桌宠以管理员运行」的二选一建议。

| 项 | 本刀口径 |
|---|---|
| 权限模型 | **不加组、不把桌宠整体提权**。改为**按需 UAC 独立助手**（随包 `resources/fps-helper/`）。**模型是**：设置页给出一个明确的「授权本次采集」操作 → 主进程先弹自己的确认 → 用户确认后才 `runas` 拉起助手读 ETW。授权在**本次 FPS 明确开启期间**有效，关功能 / 退出桌宠即退出助手，**不保留跨重启权限** |
| 持久化 | **不持久化"已获权限"**：磁盘上的 `island_metrics` / `game_fps_*` 只表达功能意愿，**不构成授权**；授权是当前进程实例/会话内的短期状态，去重、可取消，旧磁盘消息不得启动授权 |
| 哪些路径不许弹 UAC | 开关、开机恢复、切游戏目标、隐藏/显示、暂停恢复、重试退避**一律不弹**；只有用户点那一个操作才弹。用户在 UAC 上点"否"= 取消，**不重试、不循环弹窗**，等待再次点击 |
| 依赖与恢复 | 未授权时 `session_factory` 抛异常、卡片写**「需要授权」**（不摆假数字、不显示 0）；权限类失败仍是**终态不自动重试**。已经在 `Performance Log Users` 组里、普通权限就能采的机器**不经 UAC**（不强制提权） |
| 必须如实说的常驻事实 | 授权期间助手是**一个常驻进程**（暂停采集时也在，只停 ETW 与后端）。设置页文案**不许**写"零开销 / 零进程" |
| 代码状态（**接线已完成 ≠ 权限已验证**） | 传输层（`pet/fps_elevation_transport.py`：授权控制器 / 状态 / 取消 / 去重 / 会话适配）与请求线（`pet/fps_authorization.py`：主进程 `QLocalServer` 服务 + 设置进程异步客户端 + 主进程真确认框 + "已授权才走提权助手"的分发）都已实现并有独立测试；**设置页接线已完成**——灵动岛页帧率段下两行：只读状态行 `game_fps_authorization` + 动作行 `game_fps_authorize`（按钮「授权本次采集…」），点它只向主进程发一条本机请求、由**主进程**弹确认框，状态每 2 秒轮询一次且**只在帧率段开着时**跑。**两行不是设置项**：没有配置键、不写盘，"不持久化已获权限"因此在设置面也成立。指标行那句"首次使用可能需要在系统里授予相应权限"**保留**——普通权限机器（已在 `Performance Log Users` 组里）本来就照常出读数、不必授权。**仍未验**：真实 UAC 提权、真实 ETW 会话、任何真实帧读数（本机从未执行过 `runas`）。取证见 [`PR-REPORT-GAME-FPS-2026-10-11.md`](PR-REPORT-GAME-FPS-2026-10-11.md) §十 |
| 边界 | 助手是本地构建的**未签名**候选（UAC 显示未知发布者）；**真实 UAC 提权、ETW 采集与任何真实帧读数本机均未验证**。打包侧三道校验与这一处边界见 [`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md)「按需 UAC 采集助手」、[`PR-REPORT-GAME-FPS-2026-10-11.md`](PR-REPORT-GAME-FPS-2026-10-11.md) §九 |

### 2026-10-11 — 游戏帧率段：`island_metrics` 新增 `fps` 成员 + 2 个进程级目标键

本刀**新增 1 个岛指标成员与 2 个持久设置键**（3 行设置：1 个指标开关 + 2 个跟随设置），
**不动**任何既有键的默认值、归属与迁移。后端来自官方 PresentMon 独立程序（随 Windows
包分发，见 [`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md) 的「随包分发的第三方二进制」）。

| 项 | 本刀口径 |
|---|---|
| 新增键 | `game_fps_target_mode`（枚举 `foreground`（默认）/ `process`）与 `game_fps_process`（默认空）。**都是进程级（全局）键**：不在 `PER_SLOT_SETTING_KEYS` 里，岛与采集链路一律读主配置；子宠页由设置进程的配置路由直写主配置（`settings_slot_config` 的 2026-10-09 口径），**因此不置灰**——这次没有"改了等于没改"的行 |
| `game_fps_process` 的准入形态 | 只存**一个纯 exe 名**（如 `game.exe`）。归一化四步且幂等（取最后一段路径 → 只取第一个空白段 → 剥引号并从首个非法字符处截断 → 限长 120）：路径分隔符、空白、引号与命令元字符（分号 / 与号 / 竖线 / 美元符 / 反引号 / 尖括号 / `^` / `%` / `*` / `?` / 冒号）全部不合法。这是**安全契约**而非排版洁癖——该值只与进程镜像名比对，**永不拼进命令行、永不 shell、永不作为程序启动**；空 = 没有指定（不是"随便挑一个帧率最高的程序"，设置页文案明说） |
| 新指标成员 | `island_metrics` 的 `fps`（行键 `island_fps`，label「灵动岛指标 · 游戏帧率」）。**默认关**（`island_metrics` 出厂仍是空列表）——升级用户不会突然多出一段；开启才启动采集，关闭/折叠/隐藏/退出都停 |
| 归属 | 「灵动岛」页 → 「桌面组件」组，与其它内置指标行**同层级、同风格、同可见性依赖**（同一个 `SettingRow` 装配、同一个「灵动岛指标 ·」前缀、同一套 island_on × info_on 门）。两行跟随设置只在帧率段开启时显示（与「延迟探测间隔」跟网络开关同规） |
| 依赖与恢复 | 依赖链：岛可见 × 信息槽开 × 帧率段勾选 → 才采集。能力缺失（非 Windows / 内置采集程序不在）→ 段**不占位**、卡片写原因；权限被拒 → 卡片写真实原因（不拿假读数顶上），且**权限类失败是终态不重试**（显式重开或改设置才重试），失败路径不新增阻塞 |
| 契约完整性 | 平台差异（仅 Windows，其余平台保持行 + 说明「不支持」，不隐藏）、保存时机（随设置窗关闭的既有写回，不新增路径）、迁移（两个键都是**新键**：老配置没有它们 → 取默认，出厂默认与重置共用 `Config._initial_data()`，没有第二份真值）、搜索别名（`帧数` → `island_fps`；关键词 FPS/fps/帧率/帧数 四条实测命中）、默认值标记（`island_fps` 进 `NO_DEFAULT_ROWS`——它是 `island_metrics` 的**列表成员**，没有单一标量默认值，与另四个岛指标行同归类） |
| 文案纪律 | 三行都写明"这是游戏的 Present 帧率、不是桌宠动画帧率、不是显卡占用推算"，并如实写"首次使用可能需要在系统里授予权限""有一定开销（多少以实机测量为准）"——**不承诺零开销**，不把未授权的机器说成"暂时没有数据" |

准出对应：

1. **契约**：键、默认值、归属、依赖、平台可见性、失败路径逐条见上表；两个键都是新键，
   无旧键/旧入口/旧别名迁移；默认关 + 默认空目标 ⇒ 存量用户行为逐位不变。
2. **TDD**：`tests/test_settings_game_fps.py`（606 行）在**公开 seam**（真实
   `ModernSettingsDialog` + 真实 `Config`）上覆盖配置准入（`fps` 成员、枚举归一、
   纯 exe 名归一含路径/参数/引号/元字符/限长/幂等、reload 与 `set()` 往返、出厂默认
   与重置）与设置页（三行存在与文案口径、下模式启用态、岛 × 信息槽 × 帧率三条依赖
   显隐、保存写回保既有指标序 + fps 追尾、搜索四词、默认值标记与单项重置、子宠页
   可编辑且落主配置、窄窗与放大字体布局）；配套快照文件同步更新
   （`tests/test_settings_default_marks.py` 的 `NO_DEFAULT_ROWS`、
   `tests/test_settings_copy_lint.py` 的 `island_fps` 白名单）。后端与岛侧的用例见
   `.scratch/product-game-fps-20261011/HANDOFF.md`。
3. **布局与可访问性**：真实 `windows` 平台插件（**不是 offscreen**）下四种组合实测
   （900×760 明色 foreground、900×760 明色 process、1200×820 暗色 process、
   720×560 暗色 + 1.5 倍字体），几何断言全过（行不越出视口、滚到底后可见、
   程序名输入框装得下自己的内容、标签说明无纵向裁切）；证据脚本
   `.scratch/game-fps/real_plugin_layout_probe.py`，截图
   `.scratch/game-fps/settings_capture.py` → `.scratch/game-fps/shots/*.png`（四张）。
   下拉与输入框宽度按字体度量给最小宽度，窄窗由 `SettingRow` 的响应式换行接管。
4. **跨平台**：产品代码无新平台分支（判据是 `sys.platform`，已有的能力判定路径）；
   非 Windows 上**保留行并说明原因**（"只在 Windows 上有这条采集能力"），不隐藏、
   不假装支持。真实 GUI 验收仅本机 Windows，macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；`ONEDIR_PACKAGING.md`（后端随包与两道构建门）、
   `THIRD_PARTY_NOTICES.md` 第 6 节（MIT 许可随包）、`docs/INDEX.md` 与
   `docs/PR-REPORT-GAME-FPS-2026-10-11.md`（本次交付证据）同步；`CONTEXT.md`
   无新概念需要登记（"Present 帧率"的口径写在 `pet/game_fps.py` 与设置页文案里）。

### 2026-10-10 — 终审修复批：全局 reset 入口 / 开机自启权限 / 死开关退役

本刀**删除 1 个持久设置的可见入口**、**摘除 1 处置灰**、**新增 2 个命令类入口**，
没有新增设置键、没有改默认值/归属/依赖/迁移。

| 项 | 本刀口径 |
|---|---|
| 全局 reset 无 UI 可达 | 设置页所有产品入口都带 `--instance slot-N`（含 slot0），侧栏原先只有一个「恢复默认设置…」按钮 → 走 `SlotRoutedConfig.reset_to_defaults`（本宠），而**已经在实现里活着的全局 reset 在产品 UI 上完全不可达**（只有手工命令行 `--settings` 能到）。修法：slot 页侧栏给出**两个动作**——「恢复默认设置…」（本宠，原按钮与文案逐字不变）与新增「恢复全局默认…」；全局动作落在 `settings_slot_config.global_config_of(config)`（= 路由视图背后的那份 `config.json`，显式全局身份，**不新建第三个 Config 实例**），文案复用既有的 `RESET_HEADLINE / RESET_KEPT / RESET_BODY`。作用域由 `resolve_reset_scope(host, scope)` 显式传递（缺省仍按配置面判，兼容既有调用面） |
| 「开机自启」在 slot0/子宠页被误禁 | 旧判据 `if config.instance_id` 在 slot0 拿到显式身份（`slot-0`）后把**主宠自己的页面**也禁掉，tooltip 还写「仅主桌宠可设置」→ 指向用户正站着的地方。修法：判据退场，控件在任意宠的页面都可编辑，tooltip 与行说明改为一句话讲清作用域（「影响所有桌宠：系统登录项只有一份。」/行 hint「（影响所有桌宠）」）。`sync_system_autostart` 只在**全局**作用域收敛 OS 登录项（本宠作用域一个全局键都没重置，去写 OS 就是把用户没改过的登录项改掉） |
| 「一键退出子肥鱼」静默 no-op | 这是**全局**动作，但写者按身份字符串真值回填 target：slot0 的 `slot-0` → `target=0`，而两个消费者都把非 `None` target 当**子槽**去 `_spawned` 里找（主 sprite 不在其中）→ 点了没反应。修法：新增 `settings_slot_config.is_main_identity(instance_id)`（判据 = slot 号，与 `app.py` 的 `slot_id in (None, 0)` 同源），主身份 → `target=None`（全部）、子宠 → 该子槽；按钮的 enable 判据同源（主宠页可用，子宠页置灰 + 指向托盘的真实可达提示） |
| 死开关退役 | `spawn_inherit_dynamic_island`（「生小肥鱼继承灵动岛」）在设置页可编辑、写主配置，但**唯一行为读点** `slot_manager.seed_slot_config_from_main` 生产 0 调用点，新 spawn 只拷 `PER_PET_SETTING_KEYS`（不含该键），岛本身是进程级单例读主配置 → 开关两态在运行期没有任何差别。整块退役：控件创建、设置行、保存写回、域 claim 四处一并删除；**键本身的清洗/出厂默认保留**（旧键按迁移纪律读入即弃，不新增第三份真值，`Config` 一处未动） |
| 文案与实现相反 | 「一键退出子肥鱼」行说明写「并删除它们的配置、会话与待办数据」，实现保留（`_remove_spawned_sprite` / `clear_spawned_pets` 只出活跃清单）→ 改为「它们的档案、设置、会话与待办数据全部保留，随时可以再生成」；「播放速率」行写「控制所有桌宠动画的播放速度」，而 `playback_speed` 在 `PER_PET_SETTING_KEYS` 内且不在窗口级集合里 → 改为每宠口径 |
| 设置进程消费端 | 两个消费者（`overlay_shell._consume_settings_command`、`app._consume_settings_command`）语义**不改**：`target=None` 本就是"全部子肥鱼"。捕获模式下静默无效是**有意行为**（legacy 拓扑没有子宠可退，见 `PR-REPORT-STREAM-CAPTURE-PHASE-A-2026-10-07.md`），本刀未动 |

准出对应：

1. **契约**：删除的键 `spawn_inherit_dynamic_island` 的去向已写明（读入即弃、清洗与
   出厂默认保留、不再有 UI 与写回）；新增的两个入口是**命令**（本文准入清单明确排除
   命令类进入设置行，它们与「保存并退出」同区的侧栏按钮）；无新键、无默认值/归属/迁移
   变更；失败路径不新增（全局 reset 失败仍弹告警且不关窗）。
2. **TDD**：`tests/test_settings_reset_defaults.py` 新增 3 例（两个作用域都在、全局动作
   真的重置全局面且不动每宠档案/活跃集合/凭据、本宠动作不吃全局）+ 改造 2 例（OS 收敛
   只在全局作用域）；`tests/test_settings_subslot_autostart.py` 重写为「任意宠都可编辑
   + 作用域明示 + 子宠页保存落主配置」；`tests/test_settings_and_resources.py` 新增
   `target=None`（含 `slot-0` 身份）、子宠页提示可达、行文案不承诺删除三例；
   `tests/test_settings_pet_tabs.py` 的标签行表随删除同步。先红后绿证据在
   `.scratch/product-final-fix-product-wiring-20261010/`。
3. **布局与可访问性**：slot 页侧栏多一个同风格按钮（无新增控件类型、无新增 QSS）；
   行序只少了被删的那一行；置灰摘除只影响 `setEnabled` 与 tooltip。
4. **跨平台**：无新平台分支（判据只读身份与配置面）。
5. **视觉与文档**：本记录即变更记录。

### 2026-10-10 — 第三方灵动岛组件：设置页组件行 + 配置准入的声明源

- **新增的不是新设置项，而是既有项 `island_metrics` 的行集合**：声明清单里有哪些组件，
  设置页「桌面组件 → 灵动岛指标」就多几行（与内置段行**同层级、同风格、同可见性依赖**：
  同一个 `SettingRow` 装配、同一个「灵动岛指标 ·」前缀、同一套 island_on × info_on 门）。
  勾选/保存仍写同一个 `island_metrics` 列表，顺序规则不变（已有且仍勾的按原序在前，
  新勾的按典范序追尾；典范序 = 内置段 + 组件按清单扫描序）。
- **准入的真源是声明清单**（`pet/island_extension_host.py` 读出的静态 JSON metadata），
  **不是运行期注册表**：主进程与独立设置进程读同一份，所以"设置进程保存一次就把用户的
  组件偏好清掉"这类跨进程分歧不可能发生（准入不看"谁在本进程注册过"）。
- **行只读静态 metadata**：设置页不 import 任何组件代码（provider），所以组件是"暂缺
  文件"还是"文件坏了"在设置页上都只是文案差别，不存在执行第三方代码的窗口。
- **停用 / 暂缺 / 卸载三态**：停用 = 取消勾选（保留声明与偏好、不采样、不撤阈值）；
  暂缺 = 清单在而 provider 文件不在（**行照旧可勾**，hint 写明"这份偏好会保留，但当前
  不采样也不在岛上显示"）；卸载 = 删组件目录（行消失、阈值随声明撤回）。
- **准出对应**：1）契约——无新键、无默认值/归属/迁移变更；组件的 label/hint 来自组件
  自己的声明（`label` / `source` / `provider`），失败路径不新增。2）TDD——
  `tests/test_island_extension_chain.py` 覆盖行存在与 label 口径、勾选保存、重启读回、
  与内置行的先后序、停用不撤阈值、跨进程设置路径只读不执行（独立解释器）；
  全部先红后绿（隔离副本消融证据在 `_plan` 外的本地 `.scratch/`）。3）布局与可访问性——
  无新增控件类型、无新增 QSS、无行序变化（组件行追加在内置行之后，无组件时设置页
  与从前逐像素相同）。4）跨平台——无新平台分支（目录与 JSON 都是标准库）。
  5）本记录即变更记录。

### 2026-10-10 — 固定身份与唯一配置归属

- 所有拓扑的 `slot-0..128` 设置/运行配置使用 `SlotRoutedConfig`：每宠偏好、位置与朝向只写对应 `config-slot-N.json`，全局功能只写 `config.json`；显式空身份恒为global。raw slot Config整表保存拒绝。
- slot0迁移先备份、纯快照迁移清洗抽档案、验证既有目标后清源；跨文件非事务，失败可重跑且坏目标不清源。旧dirty global不能把每宠键写回。
- 本宠reset保位置、身份、active与共享历史；global reset不写任何profile/几何/active，AI密钥保留。profile代次侧车的pending reset阻普通旧写，新reader也不得越过partial；显式reset可恢复。
- 碰撞world四参数仍全局消费，本批不移键、不改求解。没有新增设置键或第三真值。
- 当前证据见本地 `.scratch/product-slot0-implementation-20261010/`：作用域/迁移/旧token/reset/三档案恢复/捕获/菜单派发相关用例；真实GUI、全量、性能与最终产品双审尚未进行，不以offscreen代替这些门。

### 2026-10-09 — 子宠设置页的配置读写路由（架构级死开关修复）

本刀**不新增、不改动、不移除任何设置项**：配置键、默认值、归属、依赖与迁移零改动；
改的是**保存语义与置灰可见性**（本文适用范围里的「保存语义」与「平台可见性」中的
身份可见性）。键集、行键、搜索索引、默认值标记表一个都没动。

| 项 | 本刀口径 |
|---|---|
| 问题 | overlay 拓扑下右键子宠 → 桌宠设置跑在独立设置进程里（`--instance slot-N` → 读写 `config-slot-N.json`），而运行期只有 `PER_SLOT_SETTING_KEYS` 的键逐宠取源（`overlay_shell._SpriteConfig.get`），**其余键一律读主配置**。于是子宠页上「语音报时 / 节日 / 待办 / 灵动岛 / 菜单外观 / 窗口级 7 键 …」约百行：保存成功、回显正确、运行期零效果。窗口级 7 行还标着「（对所有桌宠生效）」——实际对所有桌宠都不生效 |
| 修法 | 设置进程的配置面按 key 分流（新增 `pet/settings_slot_config.py`）：分流键 → slot 文件，写走既有 `overlay_spawn_state.write_slot_setting` 的原子单键写（保留 `user_customized` 语义）；其余键 → 主 `config.json`，落盘遵守 **reload → set → save**（`Config.save` 是整表写，不先重读会盖回主进程/托盘的并发改动）。装配点是 `ModernSettingsDialog.__init__` 的配置入口（控件从配置构造，路由必须在构造前就位，否则初值仍来自 slot 文件的无效快照） |
| 分流范围 | 仅 **overlay 拓扑下的 slot 身份**。主宠设置进程（无 `--instance`）与 legacy 单窗（含捕获模式）原样返回同一个配置对象、行为逐位不变——legacy 里那只宠读的就是自己那份 `config-slot-N.json` 的**全部**键，分流反而会让它的设置失效 |
| 键集单一来源 | `PER_SLOT_SETTING_KEYS` 从 `overlay_shell.py` 挪进零 Qt 的 `overlay_spawn_state.py`（独立设置进程禁止导入 overlay_shell：会连带素材库/ffmpeg/托盘），壳侧改为引用它；`tests/test_settings_slot_config_route.py` 用 `is` 钉住两侧是同一对象。分流键集 = 该集合 ∪ `{"collision_enabled"}`（它有专属逐宠读点 `read_slot_collision_enabled`，写主配置既改不动这只宠也会改掉别的宠的继承值） |
| 恢复默认 | `SlotRoutedConfig.reset_to_defaults` 只重置 slot 身份文件，**主配置一个键都不碰**。未闸住时 `Config.reset_to_defaults` 的整表语义会打到所有桌宠共用的主配置上（消融实测：`pet_opacity` 66→100、`voice_chime_enabled` True→False） |
| 置灰摘除 | 原先因「改了等于没改」置灰的 7 处恢复可用（清单见下）；`settings_widgets.mark_main_pet_only` / `MAIN_PET_ONLY_TOOLTIP` 随之无消费者，整块删除。**仍在置灰**（理由与本问题不同）：开机自启（OS 登录项，`settings_pet_controls`——**2026-10-10 已改**：任意宠可编辑 + 行内明示"影响所有桌宠"，见本文件最上面那条记录）、直播捕获兼容（拓扑输入 + 捕获模式下 spawn 入口本就拒绝，`tests/test_stream_capture_copy.py` 另钉）、harness 的 desktop 平台项 |
| 窗口级 7 行 | 「（对所有桌宠生效）」标注保留——路由接通后它变成真话 |
| 不做 | 不改 `PER_SLOT_SETTING_KEYS` 内容、不改 slot 迁移/落种逻辑、不改 `settings.lock` 机制、不改 `collision_enabled` 的专属读点、不给分支加设置项 |

置灰摘除清单（每组同一句理由：进程级键 + 子宠页曾写 slot 文件 + 运行期只读主配置）：

| 位置 | 键 |
|---|---|
| `settings_music.py` 播放器路径两行 + 「自动检测」按钮 | `music_player_paths`（右键菜单与 `music_players` 读主配置） |
| `settings_music.py` 浏览器媒体会话 | `music_browser_media_enabled`（歌词源解析读主配置） |
| `settings_music.py` foobar 两行 | `foobar_lyrics_enabled` / `foobar_title_format`（歌词源解析读主配置） |
| `settings_network.py` 指标四开关 + 探测间隔 + 菜单入口 | `island_metrics` / `network_probe_interval_seconds` / `network_info_menu_enabled`（采样器/岛/右键菜单读主配置） |
| `settings_quiet.py` 免打扰两行 | `quiet_minutes_default` / `quiet_also_on_fullscreen`（右键菜单命令、全屏联动、托盘读主配置） |
| `settings_weather.py` 天气三行 | `weather_enabled` / `weather_city` / `weather_city_list`（右键菜单与「查看天气」读主配置） |
| `modern_settings_dialog.py` harness 自启开关 + 目标下拉 | `harness_autostart` / `harness_launch_target`（服务拉起读主配置） |

准出对应：

1. **契约**：无键/默认值/归属/依赖/迁移变更；搜索索引、行键、默认值标记覆盖率与
   页内分组一字未改。失败路径不新增：保存失败时的报错路径仍是设置进程启动时那份
   slot 文件路径（两份文件同目录，未变）。
2. **TDD**：新增 `tests/test_settings_slot_config_route.py` 11 例（保存路由、per-slot
   键不污染主配置、初值路由、恢复默认只碰 slot 文件、reload-merge 并发纪律、主身份/
   legacy/非 slot 身份不分流、键集单一来源、harness 置灰摘除）；4 个既有文件的置灰
   用例改为「可编辑 + 值落主配置」的契约（`test_music_player_settings.py` 3 例、
   `test_quiet_mode.py`、`test_weather_settings.py`、`test_settings_network.py` 2 例）。
   三组消融（撤分流 / 撤恢复默认闸 / 撤落盘前重读）各自令对应用例变红，先红后绿证据
   在案。
3. **布局与可访问性**：无新增控件、无新增 QSS、无行序变化；摘除置灰只影响
   `setEnabled` 与 tooltip（子宠页这些行现在与主宠页同态）。
4. **跨平台**：产品代码无新平台分支（路由只判拓扑与身份）；真实 GUI 验收仅本机
   Windows（离屏平台 + 真实控件与真实配置文件断言），macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；`mark_main_pet_only` 的删除与仍在置灰的两处
   已在上表登记。

### 2026-10-08 — 右键菜单模板键退役（`context_menu_template`，唯一设置项删除）

旧版扁平右键菜单全量退役 ⇒ 唯一在设置页表达它的持久设置 `context_menu_template`
（行键 `menu_template`，标题「菜单版本（新版 / 旧版）」）失去唯一消费者，整键删除。
这是本文适用范围里的**删除持久设置 + 删除设置行**，逐项记录。

| 项 | 本刀口径 |
|---|---|
| 准入复核 | 删除不新增准入；被删项本身也不再满足准入第 5 条（产品无法可靠自动推断已被"只剩一套菜单"的事实取代） |
| 键与去向 | `context_menu_template` 从 `Config._initial_data()`、reload 白名单、`_normalize_pet_settings()`、设置页控件与保存回写、默认值标记表、搜索别名表一处不剩。**旧 key 有明确去向**：`Config._migrate_menu_template_config()` 在读盘路径读旧值 → 记一次 info → 丢弃（同 `decode_broker_enabled` 先例），不报错、不回退、不写回；主配置与每个 slot 配置走同一条构造路径 |
| 旧入口去向 | 菜单里的「切换到新版菜单 / 切换回旧版菜单」项随旧版菜单删除（`add_template_switch` 无消费者）；「AI 设置」按 Q6/Q7 既定决定保持并入「桌宠设置 → AI 与对话」，不复活独立快捷入口 |
| 搜索别名 | 「菜单模式」→ `menu_template` 的别名删除：该行已不存在，别名留着只会空转。搜「菜单模式 / 旧版菜单 / 切换菜单」自此 0 候选（不是没搜到） |
| 恢复路径 | 无需恢复：菜单恒为现代树，无失败分支；设置页其余行不受影响 |
| 平台 | 无平台分支（该键与平台无关） |
| 不适用 | 新增设置项、披露层级、capability matrix、预览——本刀没有对应改动 |

准出对应：

1. **契约**：键/默认值/归属/依赖变更见上表；旧 key、旧入口、旧搜索别名逐条有去向（本文「准出」第 1 条）。
2. **TDD**：`tests/test_config_schema.py` 新增 4 例（主配置静默丢弃 + 不写回 + slot 配置丢弃 + 磁盘上键消失），
   先红后绿；`tests/test_menu_layout.py` 把"切到旧版并落盘"的旧用例改为"设置页不再有菜单版本控件、
   保存不再写回该键"；`tests/test_architecture.py` 增加"退役键只许出现在迁移函数里"的守卫
   （实现前它准确指向 `modern_settings_dialog.py:916/2367` 与 `settings_default_marks.py:89` 三处残留）。
3. **布局与可访问性**：删除一行（含其下拉）后「菜单」域仍只有「内容与布局」一组，行序与
   搜索索引、默认值标记覆盖率快照同步更新；无新增控件、无新 QSS。
4. **跨平台**：无平台分支；真实 GUI 验收仅本机 Windows（截图为程序化断言替代，见 PR 报告）。
5. **视觉与文档**：本记录即变更记录；README 的三处「一键回到旧版」表述已更正；
   Q6/Q7 规划书的迁移条目已勾掉；`CONTEXT.md` 未提及菜单模板键，无需更新。

### 2026-10-08 — 设置页第三刀：默认值呈现 + 单项重置（纯呈现层）

本刀**不新增、不改动、不移除任何设置项**：配置键、默认值、归属、依赖与保存语义零改动，
因此「准入」6 条不需要重新逐条论证；改的是设置页交互（本文适用范围里的「修改设置页布局、
保存语义」），按「准出」逐项记录。设计口径：`.scratch/SETTINGS-UI-DESIGN-K3-20261007.md`
§五第三刀（≤ +150 行）。

| 项 | 本刀口径 |
|---|---|
| 判定 | 行当前值 ≠ `Config` 出厂默认 → 该行进入「已修改」态（标记 + 默认值小字 + 重置入口）；默认态三者全不呈现 |
| 默认值来源 | `Config._initial_data()`（唯一真相源）。新增 `pet/settings_default_marks.py` 只登记「行 → 配置键路径」，**一个默认值都不写**；对账用例见 `tests/test_settings_default_marks.py` |
| 覆盖 | 四类控件（ToggleSwitch / ModernSelect / BrowserSpinBox / QLineEdit）共 136 行中的 120 行有可比默认值；其余 16 行（AI 服务商逐条目字段、`island_metrics` 列表成员、台词编辑层记忆）没有单一默认值 ⇒ 不显示标记，清单钉在测试快照 `NO_DEFAULT_ROWS` |
| 单项重置 | 只把控件值设回默认；落盘时机跟着**该行自己的保存语义**走——即时生效型开关（`click_sound_enabled` / `agent_link.sound_enabled`，控件 `toggled` 已直连宿主的 `_apply_*_enabled_now`）改一下就写盘，其余行随设置窗关闭的既有 `_write_config` 写回（`closeEvent` / Esc 也写）；本刀不新增写回路径、不绕过保存流；不弹确认框（代价低、可再改回）。全量「恢复默认设置…」流程未动 |
| 禁改项 | 子宠页的进程级 / `mark_main_pet_only` 行照旧置灰，且不给重置入口；依赖隐藏行不显示标记（可见性由 Qt 传播，不做额外记账） |
| 不做 | 四类之外的控件（QDoubleSpinBox / QComboBox / 颜色与路径选择器 / 复合控件）整行不参与，既不显示标记也不报错；未纳入的行等于「没有可比默认值」，不视为漏改 |
| 不适用 | 新增设置项、迁移、平台 capability matrix、披露层级——本刀没有对应改动 |

准出对应：

1. **契约**：无键/默认值/归属/依赖变更；失败路径不涉及（标记只是呈现，重置仍受既有保存与校验约束）。
2. **TDD**：新增 `tests/test_settings_default_marks.py` 13 例（默认态无标记、三类控件 + QLineEdit 的标记与默认值文本、单项重置不写盘、保存 round trip、子宠页禁改项、依赖隐藏行、无默认值行不报错、覆盖率快照、布局与可访问性、无 chat 打包变体、映射表只登记路径、默认值来源护栏）；关闭本功能的消融实验下 10/13 变红（剩下 3 例是表结构与默认值来源的护栏，不依赖接线）。
3. **布局与可访问性**：三档宽度（720 / 900 / 1200）+ 真实 Windows GUI（`windows` 平台插件、SF Pro Text 13px）实测——标记与重置按钮都在行内、不与控件重叠、行高按新增一行文本增高；重置按钮有 `accessibleName`（含行标题）与键盘焦点。明暗主题复用既有 `settingHint` / `QPushButton` 令牌，无新增 QSS 面。
4. **跨平台**：无平台分支（四类控件的判定与 `Config` 默认值跨平台同源）；真实 GUI 验收仅本机 Windows，macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；代表截图未产出（本会话无图像复核手段，改用程序化几何断言 + 真实 GUI 实测数字，见第 3 条）；未改 Shared UX Contract / Settings System / Menu Action Model，`CONTEXT.md` 无需更新。

补记（终审修复批，同日）：本刀有两处表述与现实不符，按现实更正，行为零改动——① 单项重置的落盘时机改为如实表述（见上表「单项重置」行）：即时生效型开关重置后立即写盘，其余随设置窗关闭写回；`pet/settings_widgets.py` 的按钮 tooltip 与 `pet/settings_default_marks.py` 的模块 docstring 同步更正。② macOS 专属行「显示 Dock 图标」原缺默认值映射（行键 `dock_icon` ≠ 配置键 `show_dock_icon`）：macOS 上该行既不出「已修改」标记也不能改回默认，且覆盖率快照必红；映射已补，并加了一条 darwin 模拟下的快照用例（`tests/test_settings_default_marks.py::test_macos_only_rows_align_with_the_same_snapshot`）。

### 2026-10-08 — 设置页第四刀：搜索增强（折叠组标题 + 口语别名入索引，纯呈现层）

本刀**不新增、不改动、不移除任何设置项**：配置键、默认值、归属、依赖与保存语义零改动；
改的是设置页搜索的候选集装配（本文适用范围里的「修改设置页布局、保存语义」中的交互面），
按「准出」逐项记录。设计口径：第一刀遗留（搜「启动项」0 命中）+ `.scratch/SETTINGS-UI-DESIGN-K3-20261007.md` §五第四刀。

| 项 | 本刀口径 |
|---|---|
| 折叠组标题入索引 | 只认可折叠组（`SettingsSection(advanced=True)` 与 `CollapsibleGroup`）；普通小标题不入（本就在首屏可见，纳入只会灌水）。命中组标题 = 该组**全部行**入候选，展开/切标签沿用既有祖先链机制 |
| 口语别名 | 人工策展小表（`pet/settings_search.py::ROW_ALIASES`，7 条），每条有「真实用户会这么说」的理由；命中方向只认「别名 ⊂ 查询」。已考察并有意不收：穿透/唱歌/歌词/背景/提示音（关键词已覆盖，加了空转）、恢复默认（落点是侧栏按钮不是设置行，测试钉住 0 命中防误判） |
| 次序与去重 | 关键词 → 组标题 → 别名，跨源去重保序；关键词永远排最前 ⇒ 既有首次命中/域跳转/页内标签切换与改动前逐位一致（9 个查询实测首命中全同） |
| 依赖语义 | 依赖隐藏行「不改配置只给说明」的语义不变（「网络延迟检测」命中仍给「需先开启」说明，实测） |
| 不做 | 拼音/分词引擎、新依赖、候选集纳入非 SettingRow 元素（侧栏按钮等） |

准出对应：

1. **契约**：无键/默认值/归属/依赖变更；本模块不读配置（只读行文案与控件归属）。
2. **TDD**：新增 `tests/test_settings_search_aliases.py` 13 例（组标题命中高级组/折叠框、口语别名、去重计数、无义词回归、既有搜索回归含回车遍历、no-chat 变体、缓存语义）；消融（撤模块+还原内联）下 11/13 变红，先红后绿证据在案。
3. **布局与可访问性**：候选集装配不改变任何行的呈现；状态行/计数语义不变。
4. **跨平台**：无平台分支；真实 GUI 验收仅本机 Windows，macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；搜索性能净增约 1.0 ms/击键（memo 后 1.73 ms/次，221 行，实测）；`CONTEXT.md` 无需更新。

### 2026-10-08 — 设置页第五刀：按区直找（恢复子分区 + 页内跳转条 + 组描述 + 视觉层级，纯呈现层）

本刀**不新增、不改动、不移除任何设置项**：配置键、默认值、归属、依赖与保存语义零改动，
**行键一个都没动**；改的是设置页的布局、分组呈现与行措辞（本文适用范围里的「修改设置页
布局」）。分支 `feature/settings-ux-polish`，三个提交各自独立可回滚。

| 项 | 本刀口径 |
|---|---|
| 恢复被压平的子分区 | 域装配原先把旧页的行用 `findChildren` 整表捞出来塞进单张卡片，旧页的子分区就此消失。主动识屏 15 行拆回 4 组（开关与节奏 2 / 什么时候看 4 / 看哪些软件 3 / 细调（高级）6）、语音报时 12 行拆 3 组（何时报 3 / 用什么声音 4 / 说什么 5）、节日提醒 12 行拆 3 组（开关 5 / 时间 3 / 文案 4）；组名带页名前缀。行键、行序与域归属一字未改，只换容器 |
| 自动化与联动重排 | 顶层顺序 = `page_content` 装配表顺序：待办提醒 → 免打扰 → 主动识屏四组 → 消费统计 → Agent 联动（折叠）→ 文案风格与模板 → 自定义台词（折叠）。删掉 `insertWidget(0/1)` 倒插逻辑；`page_content` 最小扩展为可接收已构造好的 QWidget（折叠框） |
| 桌面胶囊 | 「桌面胶囊（灵动岛）」的压平是 2026-09-17 定稿口径，本刀**不动**（只有探测间隔另立高级组） |
| 页内跳转条 | 页头标题下一排 chip（`QPushButton#settingsPageAnchor`），一格一个可见分区；当前页 / 当前标签 ≥4 个区才显示（实测：AI 与对话 7、自动化与联动 10、语音 6、桌宠·动作与碰撞 6 出现；常规 / 互动 / 菜单 / 桌面组件 / 文件识别 / 更新 不出现）。点击把该区**顶到视口上沿**（留 8px，不用 `ensureWidgetVisible`——长卡片里它等于没跳）；目标是高级组或折叠框时先展开再滚（口径同搜索命中）。整组被前置开关隐藏时 chip 同步隐藏；页内标签切换时按当前标签重建（`SettingsTabContainer` 新增 `currentKeyChanged` 信号） |
| 组描述 | `SettingsSection` / `CollapsibleGroup` 新增 `description` 与 `set_description()`：组标题下一句人话（`QLabel#sectionDescription`，12px），**高级组收起时也显示**。文案表 `SECTION_GUIDE` 覆盖全部 49 个可见组；「更新」域是命令/状态面板，没有任何分区，故无描述 |
| 行标题重写 | 14 行只改措辞、行键不动（如「质量倍率」→「体重倍率」、「dry-run 验证模式」→「试运行（只记日志、不花钱）」）；旧说法补进 `settings_search.ROW_ALIASES`（+12 条，表内共 19 条），老用户仍搜得到 |
| 视觉层级 | `QLabel#sectionTitle` 13px→15px/600、上方留 6px；新增 `#sectionDescription` 12px（浅 `#8a8d93` / 深 `#8e8e97`）；`QPushButton#settingsTaskTab` min-height 26→32、padding 0 16px、13px；新增 chip 样式（圆角 12、浅底、hover/focus 2px 蓝描边）。明暗两套 QSS 同步 |
| 不做 | 侧栏 10 域与 `SETTINGS_DOMAIN_NAV` 未动；搜索候选集装配未动；右键菜单里的「dry-run 验证模式」动作文案未动（那是菜单面，另有自己的测试契约） |
| 不适用 | 新增设置项、配置迁移、平台 capability matrix、披露层级——本刀没有对应改动 |

准出对应：

1. **契约**：无键 / 默认值 / 归属 / 依赖变更。行键与域归属是外部契约，逐条由
   `tests/test_settings_page_guide.py` 钉住（拆组前后 15 / 12 / 12 行一个不漏，且没有任何行掉进
   「待分类（开发期）」）。失败路径不涉及：本刀不做写入。
2. **TDD**：新增 `tests/test_settings_page_guide.py` 20 例（布局顺序辅助函数、三处拆组、自动化页顶层顺序、
   跳转条生成/门槛/顶对齐跳转/展开后再滚/跟随隐藏/标签切换重建、720×500 不越界不裁切、
   描述覆盖率守卫、字典无过期键、描述过黑话判据、高级组收起时描述可见、14 行定稿措辞、旧词可搜）。
   消融实验：撤掉批 1 的产品改动 3 例变红；撤掉批 2 的 `install(self)` 接线 9 例变红（20 例中），
   先红后绿在案。
3. **布局与可访问性**：720×500 最小窗口下 chip 不越界（超宽自动换行，实测两行）、组描述不纵向裁切；
   chip 可 Tab 聚焦，accessibleName 为「跳到「X」分区」；主文件行数预算按实测从 2495 校准到 2497
   （只在装配处加了 import 与 `install(self)` 接线，主体全在新模块）。
4. **跨平台**：产品代码无新平台分支（分区遍历、跳转条、描述是同一条 Qt 代码路径），但
   **测试按平台条件分叉**：主动识屏整页与 foobar / 浏览器媒体两行只在 Windows 构造
   （`settings_pet_controls` 的平台闸门），依赖它们的用例改为「按被测对话框实际构造结果
   驱动」（有则验、无则跳过或换一个三平台都有的等效目标），组描述覆盖率与「无过期键」守卫
   只对本平台实际存在的组校验。Ubuntu / macOS 会碰到的平台差异已在
   `sys.platform = "linux"` 的模拟构造（`import pet` 前打补丁，跑真实
   `ModernSettingsDialog`）与同一补丁下的聚焦 pytest 里逐条跑过；真实 GUI 验收仅本机 Windows
   ——`windows` 平台插件实测 sectionTitle 15px/600、sectionDescription 12px `#8a8d93`、任务标签高 34px、
   chip 84×28 且实际渲染底色 `#f0f1f3`（证明 QSS 令牌生效）；macOS 真实机未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；代表性截图在 `.scratch/settings-ux-polish/`（本刀为呈现层改动，
   截图仅供人工复核，未做像素级比对）；未改 Shared UX Contract / Settings System / Menu Action Model，
   `CONTEXT.md` 无需更新。

### 2026-10-17 — 每日一签（PR #290）归位「互动 · 抽签」+ 4 个新键登记

本刀**新增 4 个持久键**（3 个开关 + 1 个运行时状态）与**行归属（IA）**的变更：作者提交
（`1dc37ed2`）曾新增一个只有一组的**一级域「趣味」**（能力域 10 → 11）并为此把侧栏
布局间距 3→0、导航行距 1→0；本轮按本文准入判**不合格**并撤回。签库 48 支、取签确定性、
状态口径、语音播报一条未改。

| 项 | 本刀口径 |
|---|---|
| 新增键 | `divination_enabled`（默认 **开**）/ `divination_speak`（默认关）/ `divination_hint`（默认开）与运行时状态 `divination_state`（默认 `{}`；`{date, quote_id, drawn[≤48], hint_date}`）。**都是全新键**：老配置没有它们 → 取默认；出厂默认与「恢复全局默认」共用 `Config._initial_data()`，没有第二份真值。`divination_state` **没有设置行**——它是状态不是偏好，设置页不读也不写它 |
| 三个开关为什么是**进程级**键 | 签是一天一支的进程级状态（不逐宠）：三个键都不在 `PER_SLOT_SETTING_KEYS`，子宠页改它们由设置进程的配置路由直写主配置（`settings_slot_config`），因此**不置灰**。由 `tests/test_settings_slot_config_route.py` 的 `main_only` 清单与"子宠页保存落主配置"用例钉住 |
| 准入复核（新一级域不合格的三条理由） | ① 本文「准入记录」末段：新增一级能力域须"代表长期独立用户目标、预计包含**至少两个**稳定设置组"——【趣味】只有一个组、四行；② 同一能力域内的**同级任务**适合页内标签（既有先例：菜单域的 菜单编排/快捷启动/外观；互动域的 点击与音效/自言自语/天气）；③ 代价不对称——为在默认 800×560 里多塞第 11 个域，作者把侧栏布局间距与导航行距压到 0，**所有域**的点击目标与呼吸空间一起缩水 |
| 归位落点 | 「互动」域 → 第四个页内标签「抽签」（`settings_interaction.TAB_KEYS/TAB_LABELS` 追加 `("divination","抽签")`）。判据是代码事实：抽签与「天气」同族——"用户主动拉一次、桌宠给一个结果"的互动面；且它的每日提示本来就挂在"点桌宠"这条交互路径上（`divination_hint_on_interaction`）。行与控件仍由 `pet/settings_divination.py` 构建（与 `settings_weather` 同口径），对话框只留一行保存委托 |
| 侧栏间距撤回 | `modern_settings_dialog`：布局间距 **0→3**、导航行距 **0→1**；`settings_widgets.SETTINGS_DOMAIN_NAV` 删掉 `("趣味","play")`（回到 10 个域）；`SIDEBAR_ROW_HEIGHT` 上方那段"要从间距里挤"的注释随之删除。本机实测：800×560 下侧栏余量 **17px**、720×560 同样全落在视口内（探针 `.scratch/divination-pr290/probe_settings_layout.py`，真实 `windows` 插件） |
| 文案去向 | 总开关关掉时那句去处提示 `设置 → 趣味 里打开` → `设置 → 互动 · 抽签 里打开`（用户照着去找，指向已不存在的域就是误导）；`config.py` / `settings_page_guide.py` 里同处注释一并归位；「试听今天的签」行说明里的 Markdown 星号 `**不改签号**` 去掉（Qt 里就是两个字形星号） |
| 搜索 / 默认标记 | 三行行键与顶层配置键同名 → 走 `settings_default_marks` 的"同名回落"路径，进「已修改」标记覆盖面（按钮行「试听」不参与）；搜「抽签」「每日一签」「试听今天的签」都命中，且**自动切到抽签标签**（否则结果在视野外）。两组都有用例 |
| 旁路预合成（与设置无关但同 PR 的通道语义） | `VoiceChimeService.precache()` 的预合成角色与**报时预合成**分家：新增 `AUX_PRECACHE_ROLE`，旁路**不碰**报时那一组 `_precache_slot/_text/_bubble/_path` 占位。理由：两者原共用 `role="precache"`，旁路**失败**会清掉报时**已合好**的占位（到点白等一次冷合成）、**成功**会把 `_precache_path` 覆盖成旁路的 mp3。消融：换回旧实现时本轮 3 条新用例全红 |
| 不做 | 不改签库/取签算法/档位权重/状态形状；不动右键菜单段位与模板；不动「互动」域既有三个标签；不新增任何"预合成管理"通用框架（只要一个角色常量与三处分支） |
| 不适用 | capability matrix（签与平台无关，产品代码无新平台分支）；披露层级（三行都在 primary，无 expert 参数） |

准出对应：

1. **契约**：键、默认值、归属、依赖、保存时机（随设置窗关闭的既有写回，不新增路径）、
   迁移（4 个全新键，无旧键/旧入口/旧别名需要去向）逐条见上表；行键与域归属是外部契约，
   由 `tests/test_menu_layout.py` / `test_settings_pet_tabs.py` / `test_settings_interaction_tabs.py`
   / `test_desktop_pet_features.py` 四处按**实际构造**断言（10 域 + 四标签）。失败路径不新增：
   总开关关掉时点击给一句去处提示且**不写状态**（用例 + 实机各一条）。
2. **TDD**：本轮新增/改造 7 个测试文件（净 +249 / −14）。先红的证据：把
   `pet/voice_chime_service.py` 换回 `HEAD` 版本后，旁路预合成的 3 条新用例**全部失败**
   （两条参数化 + 一条完成路径），恢复后全绿；IA 归位侧的四条新用例在改动前一条都不成立
   （没有「抽签」标签、侧栏间距是 0）。
3. **布局与可访问性**：真实 `windows` 平台插件（非 offscreen）下六档实测——
   800×560 / 720×560 × 明暗两档 + 800×560 @1.5 倍字体：10 个域全部落在侧栏视口内并逐个
   可选中切页、抽签四行不横向裁切、说明文字按 `heightForWidth` 不纵向裁切、滚到底最后一行
   可见（探针脚本见 `.scratch/divination-pr290/probe_settings_layout.py`，六档全过）。
   截图四档（项目自带 `scripts/capture_settings_pages.py --interaction-details`：
   `03-互动-抽签.png` 等）逐张人工复核；1.5 倍字体下窗口最小高度被自身撑到 746px
   （该档不受 800×560 约束），布局同样无裁切。
4. **跨平台**：产品代码无新平台分支（判据是标准库与 Qt 通用控件）；真实 GUI 验收仅本机
   Windows，macOS / Linux 未跑（不推断）。
5. **视觉与文档**：本记录即变更记录；交付证据见
   [`PR-REPORT-DIVINATION-2026-10-17.md`](PR-REPORT-DIVINATION-2026-10-17.md)（逐文件说明 /
   离线性能实测 / 实机运行记录）并在 [`INDEX.md`](INDEX.md) 的「PR 报告存档」登记。
   `CONTEXT.md` 无新概念需要登记（「每日一签」的口径写在 `pet/divination.py` 的模块
   docstring 与设置页文案里）。
