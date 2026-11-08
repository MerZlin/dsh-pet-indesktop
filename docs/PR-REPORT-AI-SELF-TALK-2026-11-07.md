# AI 碎碎念：按性格 LLM 现场生成 + 动作联动（2026-11-07）

分支 `feat/ai-self-talk`（基线 `34d810b` = v4.4.0）。碎碎念（随机自言自语）从
`self_talk_texts` 固定句池升级为可选走 LLM：按用户填写的性格描述，每次触发现场
生成一句随机台词；台词与桌宠动作双向联动。硬约束：**任何失败一律静默回落固定
句池——它可以从不会更好，但不能消失**。

## 一、核心特性

1. **AI 碎碎念开关 + 性格设定**（两个新每宠键）：
   - `self_talk_ai_enabled`（默认关）/ `self_talk_ai_personality`（≤2000 字，留空不生效）；
   - 开启后周期气泡与点击触发的随机回退都走 LLM；AI 关闭或任何一环失败时行为
     与改动前逐字相同；
   - 模型/密钥**零新增配置**：复用聊天子系统 `ChatSettings.active_config`
     （`Config.resolve_api_key` 兼容 keyring）。
2. **随机性设计**：16 类随机主题池 + 时间锚点（周几/时段/HH:MM）+ 最近 6 句防
   重复注入。纯靠 temperature 抖动在不同请求间常撞句，随机性主要放 prompt 侧。
3. **动作联动**（跟 AI 开关走，无独立新键）：
   - *点击联动*：点击触发的随机回退，AI 按「正在做X动作」（当前点击动画名）生成
     回应；用户绑定过台词的点击动画仍优先说绑定句（`ClickTalkBindingsDialog`
     既有功能，优先级不变）；
   - *周期联动*：每轮 AI 碎碎念在门闸全过后从当前角色 `random/` 池随机抽一个
     动作 clip，`request_link_anim` **先播出来**（一次性/不打断在播/存待播的既有
     语义），台词围绕动作生成——抽到「下五子棋」就边下边吐槽。"先动后说"刻意
     为之：动作不依赖生成结果，AI 失败回落固定句时动作照播（最坏是说了句与动作
     无关的话，可接受）；
   - *子宠退化*：子 sprite 宿主的 `request_link_anim` 是静默 no-op（联动动作只属
     于主宠），`_can_play_action` 据此把子宠排除在动作联动外——不给子宠配它做
     不出来的动作，子宠自动退化为纯文字碎碎念。
4. **请求场景化改写**：压超时（min(配置,12s)）、压 token（120）、关思考链、关联网；
   生成走 QThread worker + 主线程桥接 QObject（queued signal 回 UI），进程收口挂
   `AppShell._on_about_to_quit`（`cancel_all()`）。绝不上 GUI 线程发请求，绝不
   让回调在 worker 线程碰气泡。
5. **失败回落矩阵**（全部静默）：没配模型 / 请求失败 / 12s 超时 / 输出不可用 →
   回落本次已抽好的固定句；气泡被审批等重要气泡占用 → 整句丢弃不覆盖；生成中
   再次触发 → 本轮回固定句，不排队（防请求堆积）。
6. **no-chat 变体**：`pet.chat` 的 import 全部延迟在函数内探测，打包剔除后能力
   自动缺席，设置页两行整体收掉（`chat_build_capable` 判定）。

## 二、修改文件说明

| 文件 | 内容 |
|---|---|
| `pet/self_talk_ai.py`（新） | 全部生成逻辑：prompt 组装（纯函数）/ 输出清洗（剥引号、前缀、多行、截 80 字）/ 每宠状态（在飞 worker + 历史 deque）/ `_resolve_root_config`（真 Config 解析，见 §三.1）/ `_provider_and_config`（murmur 场景改写）/ `_Worker`+`_Bridge`（线程纪律）/ `maybe_show_ai_self_talk`（唯一接管口）/ 动作联动（`_can_play_action` / `pick_random_action_clip` / `_play_action`）。chat 子系统 import 全部延迟在函数内 |
| `pet/window_alerts.py` | `show_random_self_talk` 文本尾接接管口；`show_click_self_talk` 立朗读旗 + 动作名旗（`_self_talk_ai_speak_next` / `_self_talk_ai_action_name`，入口读走——出图路径不能漏旗到下一轮） |
| `pet/overlay_shell.py` | 两处 sprite 宿主（`_SpriteSelfTalkHost` / `OverlayShell`）的同款入口读旗 + 文本尾接管。window 版与两处 overlay 版共三个宿主形态，全部覆盖 |
| `pet/app.py` | `_on_about_to_quit` 收口在飞 worker |
| `pet/config.py` | 两个新键：默认值 dict、reload 白名单（每宠 spawn 允许名单同元组）、`set()` 再归一化集合、清洗（bool + strip/截断 2000） |
| `pet/pet_profiles.py` | `PER_PET_SETTING_KEYS` +2（`SLOT_ROUTED_KEYS` 是它的单一来源引用，自动跟随） |
| `pet/settings_pet_controls.py` | AI 开关 + 性格编辑控件与接线；`update_ai_self_talk_rows` / `save_self_talk_ai_settings` 两个模块函数——实现刻意全放本模块（modern_settings_dialog 有行数预算） |
| `pet/settings_interaction.py` | 「自言自语」组新增两行（行键与配置键同名，default marks 自动可比；QPlainTextEdit 不在受支持控件集自动豁免） |
| `pet/modern_settings_dialog.py` | 仅 +2 行挂钩（显隐联动尾调 + 保存落盘）；行数预算 2722→2724（带日期注释，按该测试自己的校准条款） |
| `tests/test_self_talk_ai.py`（新） | 23 条：清洗 / prompt 注入 / 配置门 / 接管口 / 根 Config 解析（overlay 病灶形态回归，见 §三.1）/ 动作联动 |
| `tests/test_config_schema.py` 等 | 双快照 +2 键；文案 lint 白名单 +2（"AI" 是产品级说法）；`tests/test_architecture.py` 预算注释 |
| `.gitignore` / `run-dev.bat` | 开发环境：`.venv/` 入 ignore；venv 一键启动脚本（不想要可整体摘除，不影响功能） |
| `docs/SETTINGS-CHANGE-GATES.md` | 变更记录按门禁格式登记（准入表 + 迁移/恢复/收口口径） |

## 三、实现要点（含两条实机踩坑的根因）

1. **「填了人设不生效」——overlay 宿主形态错配（首版实机回归的根因）**：初版把
   宿主的 `cfg` 直接传给读 chat 设置的函数，而 `config.chat_settings()` 只有真
   `Config` 才有；overlay 拓扑下宿主 cfg 是 `_SpriteConfig` 每宠视图
   （`AttributeError`）→ 被兜底 `except Exception` 吞成 None → **AI 链路静默永不
   触发**，且日志零报错（失败回落本就是静默设计）。修法 = `_resolve_root_config`：
   每宠开关/性格仍读宿主 cfg，chat 设置从真 Config 解析（PetWindow.cfg 本身是真
   Config；壳走 `_config`；子宠宿主经 `_shell._config`）。
   **教训**：跨宿主多态传 cfg 时"全局键"与"每宠键"必须分对象取；兜底 except 会
   把形态错配藏成静默 no-op——宿主形态要专门写回归测试（`_OverlaySpriteHost`
   假件复刻病灶形态进测试）。
2. **每宠键的落盘位置**：主宠的每宠键经 #271 分流存在 `config-slot-0.json` 而非
   主 `config.json`（AI 开关/人设也在 slot 档案里）；调试"开关到底存没存上"时两
   个文件都要看。
3. **三个宿主形态都要接接管口**：`window_alerts`（window 版）+ overlay 两处
   sprite 宿主各自复刻了抽句逻辑，文本尾三处同款接入；朗读旗/动作名旗在函数
   **入口**读走（出图路径不能把旗漏到下一轮周期碎碎念）。
4. **先动后说的取舍**：备选是"台词生成完再播动作"（说动同步）——否决，理由是
   生成延迟 0.6~1.5s 起步、最坏 12s，让桌宠干等不自然；动作先行 + 台词晚到与
   "气泡跟着角色走"的既有体验一致。

## 四、性能分析

**方法（可复现）**：`python - <<EOF`（timeit 包络 `maybe_show_ai_self_talk`，及
真实端到端 3 次采样）　环境：Windows 10 x64 / Python 3.12 / `.venv`（PySide6 6.12.0）

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---|---|
| `import pet.self_talk_ai`（冷进程首跑） | 536.80 ms（大头是 PySide6.QtCore 共享成本；应用内 QtCore 已加载，边际≈文件读+执行） | 新增（惰性 import：首次碎碎念触发才付） |
| AI 关闭时门闸开销 | **0.72 µs/次**（10 万次均值；触发频率 30~180s 一次） | 新增热路径（可忽略） |
| 生成请求 prompt 尺寸 | 366 字符（system 288 + user 78）≈ 183 token 量级 | 新增（每轮一次） |
| 端到端生成延迟（DeepSeek 真实采样 ×3） | **0.63 / 0.91 / 1.46 s**（上限 12s，超时回落固定句） | 新增（后台线程，不占 GUI） |
| 在飞并发 | 同宿主恒 ≤1 个 worker（在飞再触发直接回固定句） | 新增 |
| 内存 | 每宿主 deque 6×≤80 字 + worker 对象；QThread 不带父引用、终态 `deleteLater` | 新增（可忽略） |

**结论**：① AI 关闭时稳态开销 0.72 µs/30~180s，可忽略；② 新增路径的绝对成本
是"每 30~180s 一次、prompt ~183 token、延迟 0.6~1.5s 的后台请求"，全部在
worker 线程；③ 新增网络线程与 HTTPS 出站请求（碎碎念场景，12s 硬上限）；
④ 内存增长为常量小对象。GUI 线程零新增工作（落地仅一次 `_show_self_talk_text`）。

## 五、实机运行记录

本机真实环境（Windows 10 x64，4.4.0/4.4.99 构建经 `build_onedir.ps1` 打包替换
安装版真机跑）：

- **根因现场复现**（§三.1 的证明）：用与运行时同源代码离线复现宿主取值链——

  ```
  snap = pet_profiles.read_slot_snapshot(<APPDATA>/dsh-pet-standalone-webm-chat, 0)
  → status: ok | personality len: 1210 | ai_enabled in values: True -> True
  ```

  证明配置层完好，故障在宿主形态错配（当时 `maybe_show_ai_self_talk` 收到
  `_SpriteConfig` 后 `chat_settings()` 抛 AttributeError 被兜底吞掉）；
- **用户可见行为确认**：用户报告"填的人设不生效"（症状输入）；修复版构建替换
  后，服务端链路离线全链路验证——

  ```
  keyring key present: True | len: 35
  REPLY: ……也不知道那家新开的甜品店排队要多久，烦
  REPLY: 这破五子棋又输了……算了，赢了我也不请自己吃早饭。
  ```

  真机安装版当前运行构建的启动日志零 ERROR、106 段动画正常加载
  （`pet-47244.log`）；
- **边界与失败路径实机观察**：AI 关闭 → 门闸 0.72 µs 直通固定句池；输出不可用
  （空白/全引号）→ 回落固定句（`test_maybe_show_unusable_output_falls_back`）；
  气泡占用 → 整句丢弃（`test_maybe_show_drops_line_when_bubble_busy`）；
- **无法自动验证的能力**：TTS 点击朗读 × AI 碎碎念组合需要真实音频设备人工听
  （自动化只能断言"朗读通道收到同一句文本"，已由
  `test_click_speak_flag_flows_to_display` 覆盖文本侧）；macOS 拓扑本机无设备，
  仅结构级覆盖（与既有平台分支测试口径一致）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | All checks passed! |
| 聚焦 | `pytest -q tests/test_self_talk_ai.py` | 23 passed |
| 相邻族 | `pytest -q tests/test_config_schema.py tests/test_settings_*.py tests/test_profile_snapshot_normalization.py tests/test_overlay_self_talk.py tests/test_click_self_talk_speech.py tests/test_chat_subsystem.py tests/test_alert_queue.py tests/test_bubble_text_scale.py` | 76 passed / 32 passed（两批抽测） |
| 全量 | `python -m pytest -q`（Windows 10 / Py3.12 / 本机） | 9055 passed / 9 failed——9 个在基线 `34d810b` 同样失败（假日数据 2027 时间炸弹 ×2、OCR 真机 ×5、winmm/memory 环境 ×2），**零回归** |
| 架构红线 | `pytest -q tests/test_architecture.py` | passed（行数预算按其校准条款 2722→2724，带日期注释） |
| 文案/快照族 | `pytest -q tests/test_settings_copy_lint.py tests/test_settings_default_marks.py` | passed（"AI" 白名单 +2、双快照 +2 键） |

## 七、已知限制与后续

1. TTS 点击朗读 × AI 碎碎念组合未真机听测（文本侧已测，见 §五）；
2. macOS 仅结构级覆盖；子宠无动作联动（自动退化纯文字）——给子宠补联动动作
   控制器是另一刀；
3. 动作联动无独立开关（跟 AI 总闸走）；要"AI 句但不乱动"时再拆键；
4. 模型仅 OpenAI 兼容协议（复用聊天子系统既有边界）；
5. 上游 `update.json` / 版本号未动（本刀不改版本）。

## 八、风险与回滚

- 回滚 = 关 `self_talk_ai_enabled`（或清空性格），行为与改动前逐字相同；键不删，
  无迁移负担；
- 最坏面：模型端点不可用 → 12s 内回落固定句（有上限，不会久等）；在飞 worker
  由 `_LIVE_WORKERS` 强引用 + `aboutToQuit` 收口，进程退出不带飞线。
