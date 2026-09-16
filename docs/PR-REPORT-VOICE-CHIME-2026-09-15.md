---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: e1503192c50cbf4a9392ca071882b90f_2b77d603b0f011f1b128525400f8a581
    ReservedCode1: K7xgXNznaue62WeL8ScOf+FoyjgFC+GvUdR1un+Gy8EK/cLCdDCXY3ax4lgTl3MoCJLuqW+HB9Y1AkaFTpB8fKIJIw58Yyr2/Aq7gozglZb4Dqv/lEjHUZ1znjvE83XWhth/cZo9hC8TTDBQsfMHYyqe4FXbog59xzMV+I01Hz/eOZdh1/JZU1uUJbs=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: e1503192c50cbf4a9392ca071882b90f_2b77d603b0f011f1b128525400f8a581
    ReservedCode2: K7xgXNznaue62WeL8ScOf+FoyjgFC+GvUdR1un+Gy8EK/cLCdDCXY3ax4lgTl3MoCJLuqW+HB9Y1AkaFTpB8fKIJIw58Yyr2/Aq7gozglZb4Dqv/lEjHUZ1znjvE83XWhth/cZo9hC8TTDBQsfMHYyqe4FXbog59xzMV+I01Hz/eOZdh1/JZU1uUJbs=
---

# PR Report: 语音报时（voice_chime）—— 调度判定 + edge-tts 合成播放 + 设置页接入 + 纯逻辑测试补齐

新增语音报时功能：支持整点 / 每30分钟 / 每15分钟 / 每5分钟 / 每分钟 / 自定义时间点六种调度，服务层以 20s tick 判定命中并经槽位盖戳幂等，后台线程用 edge-tts 合成语音（缓存哈希去重、上限 200 文件），QtMultimedia 播放；设置页在「自动化与联动 → 语音报时」注册完整开关、调度模式、自定义时间点、音色/语速/音调/音量与立即试听，右键菜单新增「立即报时」「关闭语音报时」两项；纯逻辑层（`pet/voice_chime.py`）零 Qt 依赖可无 GUI 测试。本次收尾补齐纯逻辑单元测试（113 用例）、修复测试暴露的 2 处源码缺陷（`normalize_chime_config(None)` 返回结构与清洗分支不一致、0/12 点中文时刻索引越界）并同步一处菜单刷新回归测试的语音报时回调 patch。

---

## 一、核心特性

### 1. 纯逻辑决策层（新增 `pet/voice_chime.py`）

- 配置默认值与逐项清洗：`clean_schedule` / `clean_custom_times`（HH:MM 归一化、多分隔符、非法项丢弃）/ `clean_voice` / `clean_rate`（钳制 ±100）/ `clean_pitch`（钳制 ±50）/ `clean_volume`（0-100），统一出口 `normalize_chime_config`；
- 调度判定 `is_chime_minute`：six 模式（hourly / every_30 / every_15 / every_5 / every_minute / custom）；
- 距下一报时点秒数 `next_chime_in_seconds`：常规模式 1..3600，自定义跨天回落 1..86400，空自定义列表按 24h 兜底；
- 槽位幂等 `chime_slot`：`YYYY-MM-DDTHH:MM#模式`，同分钟只报一次（防 tick 重复触发）；
- 报时文本组装 `build_chime_text` / `build_chime_sentence`：12 小时制中文口播（凌晨/早上/上午/中午/下午/晚上）+ 随机台词/歌词；
- edge-tts 参数格式化 `edge_rate_arg`（`+10%`）/ `edge_pitch_arg`（`+5Hz`）与音频缓存键 `cache_key`（内容+音色+语速+音调 16 位短哈希）。

### 2. 台词/歌词库（新增 `pet/voice_chime_quotes.py`）

纯数据模块，中英文各 40 条短句；音色以 `zh` 开头时主池为中文库（85% 概率，偶插英文），否则反之，避免音色与文本语言完全错配。

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

1. `voice_chime_settings.py` 行数未触碰 `modern_settings_dialog.py` 红线（2018 行预算保持）；后续若在设置页新增域需同步检查该预算与 `window.py` 4425 行预算。
2. 合成依赖网络 edge-tts 服务；离线环境仅能气泡提示（已降级处理），后续可考虑接入本地 TTS 引擎。
*（内容由AI生成，仅供参考）*
