# PR 报告：助手回复朗读（2026-10-20）

> **基线**：`origin/main` `147170f7`（v4.3.4 之后）　**分支**：`feat/chat-reply-voice`　**日期**：2026-10-20
> **范围**：4 改 + 2 新增（代码与测试 `+543 / −10`，另含 docs 两份 `+96`，合计 `+639 / −10`；`git diff --numstat`，见 §二 表格）
> **环境**：Windows 11 x64、Python 3.12.10（本项目独立 venv）、PySide6 6.12.0、`QT_QPA_PLATFORM=offscreen`

## 一、核心特性

**需求**：把 AI 回复念出来（含识图提问的回答），默认关闭。

四条结构性约束决定了实现形态，而不是"在聊天窗里加一个播放器"：

| # | 约束 | 后果 |
|---|---|---|
| 1 | 对话有三个入口（快速对话 / 灵动岛对话 / 完整聊天窗），但都汇到 `ChatService.register_global_finished` | 只订阅这一个点；在三个 UI 里各埋一份必然漏一处 |
| 2 | 音频通道的待播槽**深度 1 且新覆盖旧**（为报时设计，见 `VoiceChimeService.speak` 的 docstring） | 一次性把三句 `speak()` 进去只剩最后一句；必须问 `channel_idle` 再发下一句 |
| 3 | 这条通道已有五个使用者（报时 / 节日 / 点击自言 / 抽签 / 待办播报） | 朗读不新起通道，是第六个使用者；结构上不可能叠音 |
| 4 | 歌词链与 `ChatService` 无交集 | "只念对话、不念歌词"是设计而非巧合，另有测试守卫挡后人误接 |

**默认关**（`chat_reply_voice_enabled: False`）：关着时不建对象、不订阅、不建定时器，行为与基线逐字节一致。

**一个与上游 PR 描述的差异（如实登记）**：原始 PR 描述里有"把既有 `_channel_idle()` 提成公开 `channel_idle()`（+12 行）"一条。核对基线发现**上游早已有公开只读属性** `VoiceChimeService.channel_idle`（`pet/voice_chime_service.py:675`），故此条**不需要改**，本 PR 未动 `voice_chime_service.py` 一个字符，朗读直接消费既有公开面。

## 二、修改文件说明

`git diff --cached --numstat`（基线 `147170f7`）：

| 文件 | 增删 | 改了什么 / 为什么 |
|---|---|---|
| `pet/chat_reply_voice.py` | **+260**（新增） | 三件事：`clean_for_speech()`（去代码块留「代码略」、去链接目标、去 markdown 记号/图片/尖括号标签）、`split_sentences()`（句末切句 + 超 80 字按就近标点/硬切）、`ChatReplyReader`（订阅、队列、`pump()`、`enabled` 热读）。模块顶层**不** import `pet.chat`：无聊天打包变体里 `pet.chat` 不存在，订阅那一刻才惰性取 `ChatService`，取不到就只保留纯文本能力 |
| `pet/app.py` | **+85 / −10** | `_chime_wanted()` 增加第六个消费者 `_chat_reply_voice_wanted()`；新增 `_ensure_chat_reply_reader()` / `_release_chat_reply_reader()` / `_chat_reply_channel_idle()` / `_speak_chat_reply()`；`_sync_chime_service()` 让朗读随通道同生共死（**先摘订阅再停通道**）；退出收口处先摘订阅。`_speak_chat_reply` 走既有 `ensure_audio_channel()`（它会补读用户配置：合成后端/音量），不另起一条合成路径 |
| `pet/config.py` | **+2** | 默认值 `chat_reply_voice_enabled: False` + `reload()` 白名单 1 行（不改白名单，用户改配置要重启才生效） |
| `pet/chat/ai_settings_page.py` | **+12** | 「AI 与对话」页新增「助手回复朗读」一节，复用同页既有 `SettingsSection` / `SettingRow` / `ToggleSwitch`，未新增布局代码 |
| `tests/test_chat_reply_voice.py` | **+181**（新增） | 9 项：2 条纯函数、滴灌顺序、忙时不丢队列、新回复覆盖旧队列、默认关门禁、启停订阅幂等、`channel_idle` 公开面守卫、歌词链零引用守卫 |
| `tests/test_config_schema.py` | **+3** | 新键进 schema 快照（`RELOAD_WHITELIST_SNAPSHOT`），并按既有惯例补一行日期注释（白名单 154 → 155） |
| `docs/PR-REPORT-CHAT-REPLY-VOICE-2026-10-20.md` | **+95**（新增） | 本报告（三份交付证据载体） |
| `docs/INDEX.md` | **+1** | 按「新文档入场规则第 1 条」登记 |

**为什么不自己算音频时长**：靠估算长度决定何时发下一句，会在网络抖动/合成失败时错位；`channel_idle` 是唯一与实际播放一致的信号。

**为什么免打扰期间也念**：`respect_quiet=False`，与「点击自言自语朗读」同口径——这是对用户刚发出那条消息的回应，属交互应答，不该被静默丢弃。

## 三、性能分析

**命令**：`python bench.py`（本项目 venv，`split_sentences(clean_for_speech(text))`，每档 `n=300`，取均值）
**样本**：45 / 1260 / 6400 字三段中文（实际长度 41 / 1620 / 8235 字符）

| 输入 | 结果 | 单次成本 |
|---|---|---|
| 41 字符 | 1 句 | **0.014 ms** |
| 1620 字符 | 60 句 | **0.319 ms** |
| 8235 字符 | 305 句 | **1.598 ms** |

逐条回答纪律要求的四问：

- **稳态开销**：零。关着时不建对象、不订阅、不建定时器，`_chime_wanted()` 只多一次 `config.get`。
- **新增路径成本与频率**：每条回复一次文本处理（上表，最长档 1.6 ms），之后每句一次 `speak()`，复用既有合成/缓存/播放路径。没有新增线程、网络、磁盘写入；`_schedule_pump` 只在通道忙时排一次 `QTimer.singleShot(250ms)`，通道空出来即停。
- **内存**：队列最坏持有"一条回复的全部句子"（上表 305 条短串，量级 KB）；回队/覆盖都是丢引用，不累积。
- **系统调用**：无新增。`speak()` 的合成仍走既有的 `_TTSWorker` 线程与缓存命中路径。

**未测**：TTS 端到端延迟（本机无 edge-tts 网络环境与音频栈，见 §四），因此**不声称**任何"更快/更省"的结论，只说文本层成本可忽略。

## 四、实机运行记录

**本机真实执行过的**（命令 + 真实输出摘要）：

1. `python -m ruff check pet tests` → `All checks passed!`
2. `python -m pytest tests/test_chat_reply_voice.py tests/test_config_schema.py -q` → **78 passed**（9 新 + 69 schema）
3. 受影响族：`test_voice_chime_service / test_background_service_gates / test_todo_reminder / test_self_talk_voice_precache / test_agent_settings_hot_reload / test_app_lazy_imports / test_harness_ownership / test_requested_regressions / test_settings_process_isolation / test_pr_report_discipline` → **577 passed, 1 skipped**
4. 文本处理基准（§三 表）

**为什么"听见声音"这条不能算已验证**：本机是干净 venv（刚装 PySide6 6.12.0），无 `edge-tts` 可用网络环境、无音频输出设备、无麦克风；`QT_QPA_PLATFORM=offscreen` 下也不构造真实播放器。所以本报告**不声称**端到端出声验证，只声称"送进通道的顺序与判据"正确——后者是假通道 + 假时钟驱动的，见 `tests/test_chat_reply_voice.py::FakeChannel`。

**未跑全量套件**：本机首次拉环境后全量 >45 分钟（与 `docs/PR-REPORT-CHAT-REPLY-VOICE-2026-10-16.md` 记录的历史情况一致），本轮只跑聚焦 + 相关族。**这不是"已通过"**，PR 门禁里的"全量 pytest"一格保持未勾。

**设置页视觉未验**：新增行沿用同页既有 `SettingsSection`/`SettingRow` 结构，但未做窄宽/放大字体/明暗主题截图比对。

## 五、已知限制

- **流式边收边念**：不做。入口在 `ChatService.delta`，但要处理"同一句被增量改写"，另开一轮。
- **长回复无上限**：8235 字符 ≈ 305 句，若反馈嫌长，下一步加"只念前 N 句"。
- **未接歌词与麦克风 ASR**：前者是设计（只念对话），后者不在本轮范围。
- **右键菜单快捷开关**：不做。设置项已能表达该偏好，再加是重复入口。

## 六、风险与回滚

默认关，关着时零行为变化；开着时只新增"说话"这一种效果，不改任何既有朗读语义（报时/节日/抽签/待办全部走原路径）。新增键无迁移（缺失即 `False`，旧版本读到会忽略）。`git revert` 后无残留落盘，唯一副作用是 `voice_chime_cache` 里多几个 mp3（既有 LRU 自行清理）。

## 七、检查清单

- [x] `ruff` 通过（`pet` + `tests` 全绿）
- [x] 本功能 9 项 + schema 78 项通过
- [x] 受影响族 577 passed / 1 skipped
- [x] `tests/test_pr_report_discipline.py` 通过（本报告已在 `docs/INDEX.md` 登记）
- [ ] 全量 `pytest`（**未跑**，理由见 §四）
- [ ] 三端 CI（本机无 CI 凭据，未触发）
- [ ] 真实出声 / 设置页视觉验收（**未验**，理由见 §四）
