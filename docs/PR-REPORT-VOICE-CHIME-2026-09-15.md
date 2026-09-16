---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: e1503192c50cbf4a9392ca071882b90f_e7886b73b1a011f18304525400aeaaa3
    ReservedCode1: CXrnJC/Hv3pYYlmqS7kjBCjK8dL+jzCvm8BADuBPTlSTofzAnk6SasOgQUQgEFuHqHDDhSrdC+xX5zBVlYTNetcmAO70eN0bTXGEvv/USDcvwwG4SNxfuTd8H2p/DU4Zza39I6zf7Bu+5Qe6TEkfOuIggEinQQ6O8MS2YWsdBd8Adjw6SdGewk+qE3U=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: e1503192c50cbf4a9392ca071882b90f_e7886b73b1a011f18304525400aeaaa3
    ReservedCode2: CXrnJC/Hv3pYYlmqS7kjBCjK8dL+jzCvm8BADuBPTlSTofzAnk6SasOgQUQgEFuHqHDDhSrdC+xX5zBVlYTNetcmAO70eN0bTXGEvv/USDcvwwG4SNxfuTd8H2p/DU4Zza39I6zf7Bu+5Qe6TEkfOuIggEinQQ6O8MS2YWsdBd8Adjw6SdGewk+qE3U=
---







# PR Report: 语音报时（voice_chime）—— 调度判定 + edge-tts 合成播放 + 设置页接入 + 纯逻辑测试补齐

新增语音报时功能：支持整点 / 每30分钟 / 每15分钟 / 每5分钟 / 每分钟 / 自定义时间点六种调度，服务层以 20s tick 判定命中并经槽位盖戳幂等，后台线程用 edge-tts 合成语音（缓存哈希去重、上限 200 文件），QtMultimedia 播放；设置页在「自动化与联动 → 语音报时」注册完整开关、调度模式、自定义时间点、音色/语速/音调/音量与立即试听，右键菜单新增「立即报时」「关闭语音报时」两项；纯逻辑层（`pet/voice_chime.py`）零 Qt 依赖可无 GUI 测试。本次收尾补齐纯逻辑单元测试（113 用例）、修复测试暴露的 2 处源码缺陷（`normalize_chime_config(None)` 返回结构与清洗分支不一致、0/12 点中文时刻索引越界）并同步一处菜单刷新回归测试的语音报时回调 patch。

> **第二轮改进（2026-09-15 用户反馈 5 项）**：① 修复设置页「立即试听」按钮未进 SettingRow 导致打包版不可见；② 音色下拉扩充至 31 款中英文音色（友好中文标签）；③ 修复报时气泡：`show_bubble` 被设置窗口抑制时经 `_speech_bubble` 直显，避免只听声不见字；④ 新增 `voice_chime_show_bubble` / `voice_chime_show_quote` 两个独立开关（默认开，三处登记）；⑤ 预合成降延迟：距下一报时点 ≤60s 提前后台合成并写缓存，到点直接播缓存近零延迟，未完成回退即时合成；同步校准 `modern_settings_dialog.py` 行数预算 2018→2270（详见「六、第二轮改进」）。

---

## 一、核心特性

### 1. 纯逻辑决策层（新增 `pet/voice_chime.py`）

- 配置默认值与逐项清洗：`clean_schedule` / `clean_custom_times`（HH:MM 归一化、多分隔符、非法项丢弃）/ `clean_voice` / `clean_rate`（钳制 ±100）/ `clean_pitch`（钳制 ±50）/ `clean_volume`（0-100），统一出口 `normalize_chime_config`；
- 调度判定 `is_chime_minute`：six 模式（hourly / every_30 / every_15 / every_5 / every_minute / custom）；
- 距下一报时点秒数 `next_chime_in_seconds`：常规模式 1..3600，自定义跨天回落 1..86400，空自定义列表按 24h 兜底；
- 槽位幂等 `chime_slot`：`YYYY-MM-DDTHH:MM#模式`，同分钟只报一次（防 tick 重复触发）；
- 报时文本组装 `build_chime_text` / `build_chime_sentence`：12 小时制中文口播（凌晨/早上/上午/中午/下午/晚上）+ 随机台词/歌词（第三轮已改为按 8 小时槽位轮换，见「七、第三轮改进」）；
- edge-tts 参数格式化 `edge_rate_arg`（`+10%`）/ `edge_pitch_arg`（`+5Hz`）与音频缓存键 `cache_key`（内容+音色+语速+音调 16 位短哈希）。

### 2. 台词/歌词库（新增 `pet/voice_chime_quotes.py`）

纯数据模块，中英文各 40 条短句；音色以 `zh` 开头时主池为中文库（85% 概率，偶插英文），否则反之，避免音色与文本语言完全错配。（第三轮已改为按 8 小时槽位顺序轮换、不再随机：zh 音色只用中文库，非 zh 只用英文库，见「七、第三轮改进」。）

### 3. 调度服务 + 合成播放（新增 `pet/voice_chime_service.py`）

- 模块顶层不 import Qt，QTimer / QtMultimedia 方法内惰性导入；`VoiceChimeService` 不继承 QObject，持有无主 QTimer，由 AppShell 持有引用保证生命周期（对齐 `TodoReminderService`）；
- 20s tick 调度 → `is_chime_minute` 判定 + 槽位盖戳幂等；
- 合成在后台线程跑 edge-tts（asyncio），完成后经 queued 信号桥回 GUI 线程，用 `QMediaPlayer` + `QAudioOutput` 播放；
- 音频缓存于 `config.dir/voice_chime_cache`，同句不重复合成；edge-tts 缺失时降级为仅气泡提示。

### 4. 设置页（新增 `pet/voice_chime_settings.py`）

自含 QWidget 页（对齐 `exploration_watchdog_settings.py`），提供 `apply_to_config` / `refresh_from_config` 与 `settings_saved` 信号；在 `modern_settings_dialog.py` 的 automation 域注册并参与 `_write_config` 保存，含「立即试听」透传。

---

## 二、修改文件说明（8 改 + 4 新，另含本次收尾补丁 2 文件）

### 新增文件（4）

| 文件 | 说明 |
|------|------|
| `pet/voice_chime.py` | 纯逻辑决策层：默认值 / 清洗 / 六种调度判定 / 秒数计算 / 槽位幂等 / 文本组装 / edge-tts 参数格式化 / 缓存键，零 Qt 零 edge_tts |
| `pet/voice_chime_quotes.py` | 中英文台词/歌词库（各 40 条），纯数据零依赖 |
| `pet/voice_chime_service.py` | 调度服务（20s tick）+ 后台线程 edge-tts 合成 + QtMultimedia 播放 + 缓存去重（上限 200 文件清理）|
| `pet/voice_chime_settings.py` | 语音报时设置页（开关 / 调度 / 自定义时间 / 音色 / 语速 / 音调 / 音量 / 试听）|

### 修改文件（8）

| 文件 | 说明 |
|------|------|
| `pet/config.py` | 新增 7 个 `voice_chime_*` 顶层键默认 dict；`reload()` 白名单元组登记 |
| `pet/app.py` | `AppShell` 懒创建语音报时服务、`start()` 启动 20s tick、`_sync_chime_service()` 按配置启停（对齐 `_sync_todo_service` 模式）、设置关闭回调联动、aboutToQuit/测试收口停止释放；右键「立即报时」「关闭语音报时」回调 |
| `pet/modern_settings_dialog.py` | 设置页注册到 automation 域 + `_write_config` 写回 + 试听透传 |
| `pet/context_menus/registry.py` | 注册「立即报时」「关闭语音报时」两个菜单动作 |
| `pet/menu_templates/modern-default-v1.json` | tools 段新增两个菜单节点 |
| `tests/test_config_schema.py` | `DEFAULTS_SNAPSHOT` / `RELOAD_WHITELIST_SNAPSHOT` 同步登记 7 个新顶层键 |
| `tests/test_menu_layout.py` | modern-default-v1 节点顺序断言、resolve 期望列表、populate 期望根标签补两项 |
| `tests/test_desktop_pet_features.py` | 期望标签列表补「立即报时」「关闭语音报时」（于「桌宠设置」前）|

### 本次收尾补丁（1 新 + 1 改）

| 文件 | 说明 |
|------|------|
| `tests/test_voice_chime.py` | **新增**：纯逻辑契约测试 113 用例，覆盖六种调度判定 / 距下一报时点秒数 / 槽位幂等 / 报时文本与台词组装 / edge-tts rate-pitch 格式化 / 逐项清洗与 normalize / 缓存键 |
| `tests/test_requested_regressions.py` | `test_modern_settings_finished_refreshes_even_on_rejected` 补 `_sync_chime_service` 回调 patch（对齐 `_sync_todo_service` 既有惯例）|

### 收尾过程中修复的源码缺陷（`pet/voice_chime.py`）

1. `normalize_chime_config(None)` 原先直接返回平铺键默认 dict，与其余分支的规范化键（`enabled` / `schedule` / …）结构不一致；改为 `config = {}` 走统一清洗出口；
2. `build_chime_text` 在 0 点 / 12 点时 `_HOUR_CN[hour_12]` 越界（`hour_12=12`，元组索引仅 0-11）；改为 `_HOUR_CN[hour_12 % 12]`，0/12 点正确输出「凌晨/中午十二点整」。

---

## 三、性能影响评估

| 项 | 评估 |
|----|------|
| 20s 调度 tick | 每 tick 仅 `is_chime_minute` + `chime_slot` 纯计算（datetime 构造 + 集合查找），开销可忽略；不命中时无任何 IO |
| 语音合成 | edge-tts 在后台线程（asyncio）执行，完成后 queued 信号桥回 GUI 线程，不阻塞界面 |
| 音频缓存 | 按「文本+音色+语速+音调」SHA1 短哈希去重，同句不重复合成；缓存目录上限 200 文件，超限清理最旧文件 |
| 降级路径 | edge-tts 缺失时服务不崩溃，降级为仅气泡提示，用户可后续安装后启用 |

---

## 四、本地验证记录

| 验证项 | 结果 |
|--------|------|
| ruff check（改动文件） | 全部通过（`pet/voice_chime.py`、`tests/test_voice_chime.py` 等）|
| ruff format --check（改动文件） | `pet/voice_chime.py` / `tests/test_voice_chime.py` 已格式化通过；`tests/test_requested_regressions.py` 为文件既有未格式化状态（非本次引入）|
| 新增测试 | `tests/test_voice_chime.py` 113 passed（纯逻辑层，全部同步断言，无固定 sleep）|
| 相关菜单/配置测试族 | `test_menu_layout.py` + `test_desktop_pet_features.py` + `test_config_schema.py` + `test_config_domains.py` = 196 passed |
| 全量测试 | 2120 passed, 7 skipped；1 个环境性假红（见下）|
| 端到端试听 | edge-tts 合成 mp3（约 13.9KB）经 QMediaPlayer 播放无错（历史实现时验证）|

### flake 判定（两项均为环境性，与本次无关）

1. **`tests/test_proactive.py::TestVisionAndWatcherPhase2::test_foreground_window_info_real_call_no_shadow_bug`**：真实前台窗口调用随桌面状态漂移（历史已在 PR-MERGE-LESSONS 教训 3 记载）；全量失败后单独复跑 **1 passed**，非本次引入。
2. **`tests/test_drag_move_coalescing.py::test_drag_coalesce_timer_is_about_120hz`**：定时精度 7ms vs 8ms 抖动（历史上下文已记录）；连续 3 遍高负载复跑 **13 passed 全绿**，判定与本次改动无关。

---

## 五、已知风险与后续

1. `modern_settings_dialog.py` 行数预算本轮校准 2018→2270（实测 2255，语音报时设置页接入 + 两开关/试听透传，拆分仍为待办）；`window.py` 预算保持 4478。后续若在设置页新增域需同步检查两项预算。
2. 合成依赖网络 edge-tts 服务；离线环境仅能气泡提示（已降级处理）。预合成方案已覆盖在线延迟主因（合成等待）；Windows 本地 SAPI 备选音色零延迟降级评估后改动面较大（需新合成器抽象 + 音色映射表），本轮未实施，列入后续候选。

---

## 六、第二轮改进（用户反馈 5 项）

### 1. 设置页「立即试听」按钮打包版不可见 —— 已修复

- **根因**：`voice_chime_settings.py` 中 `preview_btn` 原为独立 `QPushButton`，置于私有 `QHBoxLayout`，而设置对话框通过 `findChildren(SettingRow)` 收集行来渲染页面，未包裹进 `SettingRow` 的控件不会被收集，开发态直接看页面正常、打包版整页收集时该按钮丢失。
- **修复**：`preview_btn` 移入「语音」分区 `SettingRow("voice_chime_preview", "立即试听", …, self.preview_btn)`，clicked → `apply_to_config()` + `preview_requested.emit("")`，父级 `_on_voice_chime_preview` 走 `on_voice_chime_now` 透传合成播放。

### 2. 音色选择过少 —— 扩充至 31 款中英文音色

- `pet/voice_chime.py` 新增 `VOICE_OPTIONS`（31 项，`value + 友好中文标签`）：zh-CN 15 款（晓晓/晓伊/云健/云希/云扬/晓辰/晓涵/晓梦/晓墨/晓秋/晓睿/晓双/晓萱/晓颜/晓悠）+ 方言 2 款（辽宁晓北/陕西晓妮）+ zh-TW 2 款（曉臻/雲哲）+ zh-HK 2 款（曉佳/曉文）+ en-US 8 款（Aria/Jenny/Guy/Ana/Michelle/Christopher/Eric/Roger）+ en-GB 2 款（Sonia/Ryan）。
- 设置页 `voice_select` 改用 `ModernSelect` 下拉（`addItem(label, value)` + `setCurrentData`）；配置值不在预设列表时自动追加「自定义：xxx」项并选中（`_sync_voice_select`）。
- 向后兼容：`COMMON_VOICES` 由 `VOICE_OPTIONS` 派生（旧文本提示格式不变）。

### 3. 报时无气泡显示 —— 已修复

- **根因**：服务 `_bubble` 仅调 `win.show_bubble`，而 `show_bubble` 在设置窗口打开等场景被 `_bubble_suppressed` 抑制（`window.py` 对非主动事件的统一策略）；用户报时时往往正开着设置页调音色，于是「只听声不见字」。
- **修复**：`voice_chime_service._bubble` 增加降级路径——`show_bubble` 被抑制时直接经 `win._speech_bubble.show_text(text, rect, duration_ms, pet_scale=scale)` 在桌宠气泡位直显（对齐 `TodoReminderService` 的气泡位调用方式）；同时整体受新开关 `voice_chime_show_bubble` 控制。

### 4. 新增「关闭气泡」「关闭台词/歌词」两个独立开关 —— 已实现

- 新顶层键：`voice_chime_show_bubble`、`voice_chime_show_quote`（默认 `True`），三处登记：`config.py` 默认 dict + `reload()` 白名单 + `tests/test_config_schema.py` 快照（白名单 81→83 键，`DEFAULTS_SNAPSHOT` 83→85 键）。
- 纯逻辑层：`clean_flag`（JSON bool / 常见字符串真值 / 非法回落默认）+ `normalize_chime_config` 输出 `show_bubble` / `show_quote`；`build_chime_sentence` 在 `show_quote=False` 时仅返回报时文本（不带台词）。
- 设置页「语音」分区新增两个 `ToggleSwitch` 行；服务 `_bubble` 按 `show_bubble` 生效，预合成/即时合成的文本组装按 `show_quote` 生效。

### 5. 语音延迟高 —— 预合成机制（零延迟播缓存）

- **根因**：原实现报时点命中后才发起 edge-tts 在线合成（网络 + 合成耗时），体感延迟高。
- **修复**（`voice_chime_service.py`）：
  - 新增 `PRECACHE_WINDOW_S = 60`：`_on_tick` 未命中报时点时，若距下一报时点 ≤60s 且该槽位尚未预合成，则后台线程提前合成该次报时音频（含随机台词）并写缓存，同时记录 `_precache_slot / _precache_text / _precache_path`；
  - 到点 `_on_tick` 命中时 `_consume_precache(slot)`：命中则返回记录文本并直接播缓存文件（近零延迟），气泡文字与播放内容天然一致；未完成/文件丢失则清空占位回退即时合成；
  - 合成完成回调按 `_synthesis_role`（`play` / `precache`）区分：预合成完成仅记录路径不播放；预合成失败丢弃占位到点即时合成；
  - `_busy` 时跳过预合成（20s tick 内窗口 60s 足够重试）；配置变更 `apply_config` 重置预合成占位防串台。
- SAPI 本地音色零延迟降级：评估后改动面较大（合成器抽象 + 音色映射），本轮仅实现预合成方案并说明（见「五、已知风险与后续」）。

### 测试与验证

| 验证项 | 结果 |
|--------|------|
| ruff check（8 个改动文件） | 全部通过 |
| ruff format（8 个改动文件） | 5 个自动格式化、2 个已合规、1 个仅注释微调 |
| `tests/test_voice_chime.py` | **121 passed**（原 113 + 新增 8：show_quote 关闭 / 音色库 20+ / clean_flag / 预合成窗口·busy·槽位 / 缓存命中·未命中回退 / 气泡开关） |
| `tests/test_config_schema.py` | 通过（快照 85 键） |
| 相关测试族（menu / desktop / config_domains / requested_regressions / architecture） | 358 passed（含行数预算校准后） |
| 全量测试 | **2127 passed, 7 skipped**；`test_drag_move_coalescing_timer` 为历史已记载环境性假红（定时精度 7ms vs 8ms 抖动，与本次无关，复跑仍为环境漂移） |

## 七、第三轮改进（台词/歌词 8 小时槽位轮换 + 自定义台词）

### 1. 轮换规则（取代随机选取）

- **槽位划分**：按本地时间每 8 小时一槽（0-8 / 8-16 / 16-24），槽位序号 `quote_slot_serial(now) = 日期序数 × 3 + 小时 // 8`，相邻槽位序号恰好差 1（含跨天 16-24 → 次日 0-8 连续递增）；条目下标 = 槽位序号 % 池长度。
- **行为**：同一槽位内固定返回同一条（报时不再跳变），跨槽位顺序切换到下一条；中英文库各自独立轮换。
- **签名变更**：`pick_quote(cfg)` → `pick_quote(now, cfg)`（纯函数、时间可注入便于测试）；`build_chime_sentence` 同步把 `now` 传入。取消原“85% 主池 + 15% 跨池随机”分支：zh 音色只用中文库，非 zh 只用英文库。

### 2. 自定义台词/歌词（新顶层配置键）

- 新键：`voice_chime_custom_quotes_zh` / `voice_chime_custom_quotes_en`（默认空串），三处登记：`pet/config.py` 默认 dict + `reload()` 白名单 + `tests/test_config_schema.py` 快照（白名单 85 → 87 键）。
- 清洗 `clean_custom_quotes()`：兼容多行字符串（`\n` / `\r\n` / `\r`）与序列输入；去空行、去首尾空白、去重保序、剔除控制字符；单条超长按 `_MAX_CUSTOM_QUOTE_LEN`（120 字符）截断。
- 优先级：对应语言的自定义条目非空时**整体替换**内置库并参与同一套 8 小时轮换；为空（空串 / 空白行 / 空序列 / 键缺失）时回退内置库；中英文各自独立判定。

### 3. 设置页入口

- 新增「台词/歌词」分区，两个 `SettingRow(..., stacked=True)` 包裹多行 `QPlainTextEdit`（带示例占位文案）；说明文案写清「与内置库的关系（填了整体替换、留空回退）」与「8 小时轮换（0-8 / 8-16 / 16-24 各一条，同一时段固定同一条、跨时段顺序下一条）」，并注明中文/英文分别作用于中文音色与非中文音色。
- 控件包在 `SettingRow` 内，确保被 `findChildren(SettingRow)` 收进共享域卡片（沿用第二轮「非 SettingRow 控件不可见」经验）。
- `apply_to_config` / `refresh_from_config` 读写两新键（写回键数 9 → 11，`modern_settings_dialog.py` 注释同步更新）。

### 4. 文案一致性

`pet/voice_chime_quotes.py`、`pet/voice_chime_service.py`、设置页三处“随机台词”表述统一改为“按 8 小时槽位轮换的台词/歌词”。

### 测试与验证

| 验证项 | 结果 |
|--------|------|
| `ruff check pet/ tests/` | **All checks passed** |
| `tests/test_voice_chime.py` | **128 passed**（原 121：替换 2 个随机用例，新增 9 个轮换/自定义用例 —— 槽宽 8h、同槽稳定、跨槽/跨天顺序 +1、中英各自池、自定义优先、空值回退（`""`/空白/`()`/`[]`/`None`/键缺失）、自定义语言隔离、清洗拆分·去重·截断、报时语句组装含自定义） |
| `tests/test_config_schema.py` / `tests/test_architecture.py` | 通过（快照 87 键；行数预算无变化：`modern_settings_dialog.py` 2256 行 ≤ 2270，`window.py` 未改动） |
| 全量 `pytest -q` | **2135 passed, 7 skipped**；唯一失败 `test_drag_move_coalescing::test_drag_coalesce_timer_is_about_120hz`（定时器实测 7ms vs 期望 8ms）为历史已记载环境性假红，与本次改动无关 |

## 八、第四轮改进（合规梳理 + 性能优化与包体瘦身 + 重新打包）

### 1. 合规梳理结论（对照 AGENTS.md / WINDOW_PY_SPLIT_GUIDE.md / CONTEXT.md / SETTINGS-CHANGE-GATES.md）

| 核对项 | 结论 |
|--------|------|
| 顶层配置键三处登记（`config.py` 默认 dict + `reload()` 白名单 + `tests/test_config_schema.py` 快照） | 11 个 `voice_chime_*` 键（含 `custom_quotes_zh/en`）三处齐全，白名单 87 键 |
| 纯逻辑层零 Qt 依赖 | `voice_chime.py` / `voice_chime_quotes.py` 无任何 Qt / edge_tts 导入；`_AudioBridge` 的 QObject/Signal 惰性导入仅存在于 service 层（含本轮清理） |
| 文件行数预算 | `window.py` 4478（预算 4478，未改动）；`modern_settings_dialog.py` 实测 2255 ≤ 2270（第二轮校准记录保留） |
| 测试覆盖 | `tests/test_voice_chime.py` 132 用例 + `test_config_schema.py` 快照 + 菜单/架构/打包测试族全绿 |
| 孤儿文件 / 未登记模块 | `voice_chime_quotes.py` 由 `voice_chime.py` 引用，无孤儿；无未登记模块 |
| 菜单模板与断言同步 | `menu_templates/modern-default-v1.json`（立即报时 / 关闭语音报时）↔ `context_menus/registry.py` ↔ `tests/test_menu_layout.py` / `test_desktop_pet_features.py` 一致 |
| 设置页控件可见性 | 12 个 `SettingRow` 均可被 `findChildren(SettingRow)` 收集；「立即试听」位于 `settingRow_voice_chime_preview`，offscreen 下 `isVisible()` 为真；`apply_to_config` 写 11 键 / `refresh_from_config` 回读正常 |

**发现并处理的问题**：调试构建期在仓库根目录遗留 `temp_build_err.txt`（81 B）、`temp_build_log.txt`（1059 B）两个临时日志，已清理（未纳入版本控制）。

### 2. 运行性能优化（`pet/voice_chime_service.py`）

| 项 | 改动 |
|----|------|
| 合成卡死自愈 | 新增 `_busy_since` + `_release_stale_busy()`：后台合成超 `_SYNTH_TIMEOUT_S = 45s` 未回调时由 tick 释放 `_busy` 锁，避免单次网络挂起导致语音报时永久静默 |
| 缓存清理节流 | `_prune_cache` 加 `_PRUNE_MIN_INTERVAL_S = 300` 节流，20s tick 不再每次都 `glob` 整个缓存目录 |
| 对象瘦身 | `_AudioBridge` 移除无主 `QObject(_obj)` 与从未调用的 `destroy()` |
| tick 开销 | 未命中报时点时零 IO（纯计算）；预合成窗口 `PRECACHE_WINDOW_S = 60` 不变 |

`tests/test_voice_chime.py` 同步新增 4 用例（stale busy 复位 / tick 自愈 / prune 节流 / 首次 prune 放行），`_make_svc` 注入 `_busy_since` / `_last_prune_at`。

### 3. 包体瘦身（新增 `scripts/slim_bundle.py` + 构建流程接入）

移除对象为 onedir 产物中程序未使用的模块；安全机制为**白名单移除 + 依赖闭包校验（pefile 反向 import 检测）+ 必需清单校验 + 空目录清理**，任一校验失败即中止。

| 移除项 | 体积 |
|--------|------|
| `opengl32sw.dll`（软件 OpenGL 回退，全仓无引用） | 19.68 MB |
| `Qt6Quick` / `Qt6Qml*`（QML 运行时栈） | 12.70 MB |
| `Qt6Pdf.dll` + 图像插件 `qpdf.dll` | 4.41 MB |
| `Qt6OpenGL.dll` | 1.90 MB |
| `Qt6VirtualKeyboard.dll` + 平台输入法插件 | 0.41 MB |
| 112 个非中英 `*.qm` 翻译（保留 12 个中英） | 6.51 MB |
| PIL `_avif` 扩展（全仓无代码引用） | 7.52 MB |
| **合计** | **124 文件 / 53.03 MB** |

依赖闭包校验结论：被移除项仅互相引用（`Qt6Quick → Qt6OpenGL`、`Qt6VirtualKeyboard → Quick/Qml`、`qpdf.dll → Qt6Pdf`），程序实际使用的 `Core / Widgets / Gui / Multimedia / Network / Svg` 不受影响。

构建流程变更（`scripts/build_onedir.ps1`）：新增 `-SkipSlim`；瘦身插入 Qt runtime 复制之后、**中文编码自检与 DLL 链冒烟之前**；编码自检步骤号改为 `[1.6/3]`；构建前统一 `Stop-Process` 旧 exe（规避 `WinError 5` 文件占用）。
新增测试 `tests/test_bundle_slim.py`（10 用例）：白名单命中 / keep-opengl-sw / 依赖闭包 / 必需清单 / 空目录清理 / main 三种模式。

### 4. 体积对比

| 产物 | 瘦身前 | 瘦身后 | 变化 |
|------|--------|--------|------|
| onedir 目录（解压后） | 约 309 MB | 256.2 MB | −53.0 MB |
| portable zip | 176.2 MB（176,241,563 B） | **146.5 MB（153,645,393 B）** | **−29.7 MB（−12.8%）** |

### 5. 重新打包与真实冒烟

构建命令：`powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat`，覆盖 `dist-onedir\dsh-pet-standalone-webm-chat-portable.zip`。

| 冒烟项 | 结果 |
|--------|------|
| DLL 链校验（Shiboken / QtCore / QtGui / QtWidgets） | **ALL OK** |
| 中文编码自检 | **PASS**（字面量 / 资源 / 文件名保持 UTF-8） |
| 构建内置约束（DLL 冲突自检 / Qt runtime 校验） | 未跳过，全部通过 |
| exe 启动 | started OK；持续运行 8 分钟（含跨 0 点）无崩溃，webm 主循环日志正常 |
| 系统 Temp `_MEI*` 新增 | **0**（仅存当日 11:09 / 11:22 两个遗留目录，与本次构建无关） |
| 打包后语音链路（edge-tts 真实合成） | 每分钟报时模式下连续 5 次落盘 `voice_chime_cache\*.mp3`（约 30 KB/条）；其中 3 条缓存键与源码预期文本**逐字一致**（文本含自定义台词「打包冒烟台词甲」），证明打包产物完整读取 `voice_chime_custom_quotes_zh` 与调度配置 |
| 设置页控件收集 | 12 个 `SettingRow` 全部可收集，「立即试听」在 `settingRow_voice_chime_preview` 内且可见 |

### 6. 全量测试

`pytest -q`：**2150 passed, 7 skipped, 0 failed**（含新增 `test_bundle_slim.py` 10 用例；历史环境性假红 `test_drag_move_coalescing` 本轮复跑通过）；`ruff check` 全绿。

## 九、第五轮改进（台词/歌词「每 8 小时换批」语义修正 + 气泡阿拉伯数字）

> 用户澄清两处偏差：①「每 8 小时轮换」指**整个台词/歌词库每 8 小时整体换一批**（且同一周期内每次报时应取到不同句子），而非「一句话在 8 小时槽位/周期内固定不变」；② 气泡报时文字应**以阿拉伯数字为主**（原中阿混排不美观），语音口播仍保持中文数字。

### 1. 台词/歌词选取逻辑重做（`pet/voice_chime.py`）

| 项 | 第三轮（旧） | 第五轮（新） |
|----|--------------|--------------|
| 轮换单位 | 单条：条目下标 = 周期序号 % 库长度 | **整批**：库按序均分为 `_QUOTE_BATCHES_PER_DAY`(=3) 批，批次下标 = 周期序号 % 批数 |
| 周期内行为 | 同一句固定不变（8 小时一句） | 批次内**按序轮换**，依次取第 1/2/3… 条，用尽回环 |
| 跨周期 | 顺序切到下一条 | 整体切到**另一批**（相邻周期批次不重叠） |
| 同周期多次报时 | 结果完全相同 | **取到不同句子** |

新增/改写纯函数（仍零 Qt 零 edge_tts，可无 GUI 测试）：

- `split_quote_batches(pool, batches=3)`：按序均分（各批长度差 ≤1）、保序不丢条不重复；空批自动过滤（自定义仅 1 条时只有 1 批）；非法批数兜底为单批；
- `chime_index_in_period(now, cfg)`：当前 8 小时周期内**已命中（含当前）的报时点数量**（自周期起点逐分钟回溯统计 `is_chime_minute`），即「本周期第几次报时」；周期内无更早报时返回 0，取批次首条；
- `quote_slot_serial(now)`：语义不变（跨天连续递增的全局 8 小时周期序号），改用于**选批次**而非选单条；
- `pick_quote(now, cfg)`：批次 = `split_quote_batches(库)[周期序号 % 批数]`，条目 = `批次[周期内次序 % 批长]`；中英文库各自分批，自定义台词非空时整体替换内置库后**参与同一套分批规则**，空值回退内置库。

周期 ↔ 批次对应（3 批）：0-8 → 批 0、8-16 → 批 1、16-24 → 批 2；跨天（16-24 → 次日 0-8）切到新批且周期序号连续。以每小时报时为例，同周期内 8 次报时依次取批次内第 1…8 条（批长不足则回环），因此「同一周期内每次报时听到不同句子」；跨周期则整批更换。

### 2. 气泡文本与语音文本解耦

| 通道 | 文本形态 | 生成函数 |
|------|----------|----------|
| 气泡展示 | `现在下午 15:45。{台词}`（中文时段词 + 阿拉伯数字 24 小时制） | 新增 `build_chime_bubble_text(now)` / `build_bubble_sentence(now, cfg)` |
| TTS 口播 | `现在下午三点45分。{台词}`（中文数字，12 小时制） | `build_chime_text` / `build_chime_sentence`（不变） |

- **缓存键与预合成仍按语音文本计算**，气泡格式不影响缓存复用：`cache_key(sentence, params)` 与缓存文件的一一对应关系不变；
- 预合成阶段一次算好语音文本（`_precache_text`）与气泡文本（`_precache_bubble`），到点命中即零延迟复用；
- `_consume_precache` 无论命中与否都清空整组预合成状态（含气泡文本），气泡文本由 `_on_tick` 在消费前取用，避免残留串到下一次报时（本轮修出的缺陷）；
- 即时合成路径：`_fire(sentence, now, bubble_text=None)` 未传气泡文本时按 `now` 现算；`_pending_bubble` 记录本次合成的气泡文本，合成回调 `_on_synthesized` → `_play_and_bubble(path, text, bubble)`；`_play_and_bubble` 未传气泡文本时回退 `text`（兼容旧调用）；
- 两路文本的台词**取同一条**（同一 `now` 下 `pick_quote` 幂等），不会出现气泡与语音台词不一致。

### 3. 文案与测试同步

- 设置页 `pet/voice_chime_settings.py`：启用项、台词开关、两个自定义台词输入框说明统一改为「每 8 小时整体换一批，同一周期内每次报时按序取不同条目」，并补充「气泡为阿拉伯数字、语音为中文口播」；模块 docstring 同步；
- `pet/voice_chime_quotes.py` / `pet/voice_chime_service.py` / `pet/config.py` 注释同步为「每 8 小时整体换批、批内轮换」；
- `tests/test_voice_chime.py` 重写/新增用例：分批均分与空批过滤、周期序号跨周期与跨天连续、周期内次序计数（hourly / every_15 / custom）、周期内按序轮换且同周期取到不同条目、批次用尽回环、跨天换批、中英文库各自分批轮换、自定义台词分批与跨批切换、空值回退、气泡阿拉伯数字（8 组时段参数化）、气泡与语音文本解耦（同一时刻 15:45 ↔ 下午三点45分）、关闭台词开关的气泡纯时间文本、缓存命中路径气泡文本、`_play_and_bubble` 气泡优先与回退、预合成气泡文本复用与消费清空。

### 4. 测试与验证

| 验证项 | 结果 |
|--------|------|
| `ruff check pet/ tests/` | **All checks passed** |
| `tests/test_voice_chime.py` + `test_config_schema.py` + `test_architecture.py` | **161 passed** |
| 全量 `pytest -q` | **2166 passed, 7 skipped, 1 failed**；唯一失败为 `test_drag_move_coalescing::test_drag_coalesce_timer_is_about_120hz`（`pet/window.py` 拖拽合并定时器取整 8→7），属与语音报时无关的既有红，`pet/window.py` 与 `tests/test_drag_move_coalescing.py` 均不在本轮改动文件内 |

*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
