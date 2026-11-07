# AI 碎碎念：按性格 LLM 现场生成 + 动作联动（2026-11-07）

分支 `feat/ai-self-talk`（基线 `34d810b` = v4.4.0）。碎碎念（随机自言自语）从
`self_talk_texts` 固定句池升级为可选走 LLM：按用户填写的性格描述，每次触发现场
生成一句随机台词；并把台词与桌宠动作做了双向联动。**任何失败一律回落固定句池**
——碎碎念是装饰性功能，本刀的硬约束是"它可以从不会更好，但不能消失"。

## 一、产品语义与核心特性

1. **AI 碎碎念开关 + 性格设定**（两个新每宠键）：
   - `self_talk_ai_enabled`（默认关）/ `self_talk_ai_personality`（≤2000 字，留空不生效）；
   - 开启后周期气泡与点击触发的随机回退都走 LLM；AI 关闭或任何一环失败时行为
     与改动前逐字相同；
   - 模型/密钥**零新增配置**：复用聊天子系统 `ChatSettings.active_config`（协议/
     baseUrl/模型/key，`Config.resolve_api_key` 兼容 keyring）。
2. **随机性设计**：随机主题池（16 类）+ 时间锚点（周几/时段/HH:MM）+ 最近 6 句
   防重复注入，纯靠 temperature 抖动在不同请求间常撞句，所以随机性主要来自
   prompt 侧。
3. **动作联动**（跟 AI 开关走，无独立新键）：
   - *点击联动*：点击触发的随机回退，AI 按"正在做X动作"（当前点击动画名）生成
     回应，如挠痒咯咯笑→求饶；用户手动绑定过台词的点击动画仍优先说绑定句
     （`ClickTalkBindingsDialog` 既有功能，优先级不变）；
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
   `AppShell._on_about_to_quit`（`cancel_all()`）。**绝不上 GUI 线程发请求，绝不
   让回调在 worker 线程碰气泡。**
5. **失败回落矩阵**（全部静默，不打扰用户）：没配模型 / 请求失败 / 12s 超时 /
   输出不可用（清洗后为空）→ 回落本次已抽好的固定句；气泡被审批等重要气泡占用
   → 整句丢弃不覆盖；生成中再次触发 → 本轮回固定句，不排队（防请求堆积）。

## 二、修改文件说明

| 文件 | 内容 |
|---|---|
| `pet/self_talk_ai.py`（新） | 全部生成逻辑：prompt 组装（纯函数）/ 输出清洗（剥引号、前缀、多行、截 80 字）/ 每宠状态（在飞 worker + 历史 deque）/ `_resolve_root_config`（真 Config 解析，见 §三.1）/ `_provider_and_config`（murmur 场景改写）/ `_Worker`+`_Bridge`（线程纪律）/ `maybe_show_ai_self_talk`（唯一接管口）/ 动作联动（`_can_play_action` / `pick_random_action_clip` / `_play_action`）。chat 子系统 import 全部延迟在函数内：no-chat 变体（打包剔除 `pet.chat`）能力自动缺席 |
| `pet/window_alerts.py` | `show_random_self_talk` 文本尾接接管口；`show_click_self_talk` 立朗读旗 + 动作名旗（`_self_talk_ai_speak_next` / `_self_talk_ai_action_name`，入口读走——出图路径不能漏旗到下一轮） |
| `pet/overlay_shell.py` | 两处 sprite 宿主（`_SpriteSelfTalkHost` / `OverlayShell`）的同款入口读旗 + 文本尾接管（window 版与两处 overlay 版共三个宿主形态，全部覆盖） |
| `pet/app.py` | `_on_about_to_quit` 收口在飞 worker |
| `pet/config.py` | 两个新键：默认值 dict、reload 白名单（每宠 spawn 允许名单同元组）、`set()` 再归一化集合、清洗（bool + strip/截断 2000） |
| `pet/pet_profiles.py` | `PER_PET_SETTING_KEYS` +2（`SLOT_ROUTED_KEYS` 是它的单一来源引用，自动跟随） |
| `pet/settings_pet_controls.py` | AI 开关 + 性格编辑控件与接线；`update_ai_self_talk_rows` / `save_self_talk_ai_settings` 两个模块函数——实现刻意全放本模块（modern_settings_dialog 有行数预算） |
| `pet/settings_interaction.py` | 「自言自语」组新增两行（行键与配置键同名，default marks 自动可比；QPlainTextEdit 不在受支持控件集自动豁免） |
| `pet/modern_settings_dialog.py` | 仅 +2 行挂钩（显隐联动尾调 + 保存落盘）；行数预算 2722→2724（带日期注释，按该测试自己的校准条款） |
| `tests/test_self_talk_ai.py`（新） | 23 条：清洗 / prompt 注入（动作优先、空动作、随机主题真随机）/ 配置门（enabled、base_url、model、no-chat ImportError）/ 接管口（失败回落、在飞不排队、气泡占用丢弃、点击朗读同句）/ 根 Config 解析（overlay 病灶形态回归，见 §三.1）/ 动作联动（先播不等待、点击动作名进请求、子宠退化、宿主形态判定） |
| `tests/test_config_schema.py` 等 | 双快照 +2 键；文案 lint 白名单 +2（"AI" 是产品级说法）；`tests/test_architecture.py` 预算注释 |
| `.gitignore` / `run-dev.bat` | 开发环境：`.venv/` 入 ignore；venv 一键启动脚本（可整体摘除，不影响功能） |
| `docs/SETTINGS-CHANGE-GATES.md` | 变更记录按门禁格式登记（准入表 + 迁移/恢复/收口口径） |

## 三、实机踩坑与根因（两条，都值得留档）

1. **「填了人设不生效」——overlay 宿主形态错配**：首版把宿主的 `cfg` 直接传给
   `_provider_and_config`，而其中 `config.chat_settings()` 只有真 `Config` 才有；
   overlay 拓扑下宿主 cfg 是 `_SpriteConfig` 每宠视图（`AttributeError`）→ 被兜底
   `except Exception` 吞成 None → **AI 链路静默永不触发**，且日志零报错（失败回落
   本就是静默设计）。修法 = `_resolve_root_config(host)`：每宠开关/性格仍读宿主
   cfg，chat 设置从真 Config 解析（PetWindow.cfg 本身是真 Config；壳走 `_config`；
   子宠宿主经 `_shell._config`）。**教训**：跨宿主多态传 cfg 时"全局键"与"每宠键"
   必须分对象取；兜底 except 会把形态错配藏成静默 no-op——宿主形态要专门写回归
   测试（`_OverlaySpriteHost` 假件复刻病灶形态）。
2. **每宠键的落盘位置**：主宠的每宠键经 #271 分流存在 `config-slot-0.json` 而非
   主 `config.json`（AI 开关/人设也在 slot 档案里）；调试"开关到底存没存上"时两
   个文件都要看。

## 四、门禁与测试

- focused：`tests/test_self_talk_ai.py` 23 passed；
- 相邻族：config schema / default marks / copy lint / profile snapshot /
  overlay self-talk / click speech / chat subsystem / architecture 全绿
  （76 + 32 passed 两批抽测）；
- 全量：**9055 passed / 9 failed**——9 个失败在未改动的基线 `34d810b` 上同样失败
  （假日数据 2027 时间炸弹 ×2、OCR 真机项 ×5、winmm/memory 环境项 ×2），本刀
  **零回归**；
- `ruff check pet/ tests/` 干净；`git diff --check` 干净；
- 实机：4.4.0 构建替换安装版真机验证（overlay 拓扑、106 段动画、日志零错误）；
  真实链路（keyring 密钥 → DeepSeek → 碎碎念请求）离线全链路通过。

## 五、限制与未做（如实登记）

1. **TTS 点击朗读**走"AI 句落地那刻朗读"的接线与单测，真机开了朗读开关的组合
   未端到端听测；
2. **macOS 未验证**：三宿主中 overlay 两处已覆盖，`window.py` 拓扑 macOS 分支仅
   结构级（与既有平台分支测试口径一致）；
3. **子宠无动作联动**（宿主 no-op，自动退化纯文字）——给子宠补联动动作控制器是
   另一刀；
4. 动作联动**无独立开关**（跟 AI 总闸走）；若想要"AI 句但不乱动"再拆键；
5. 模型仅支持 OpenAI 兼容协议（复用聊天子系统既有边界）；`thinking`/联网在
   碎碎念请求里强制关闭；
6. 上游 `update.json` / 发布流程未动（本刀不改版本号）。

## 六、风险与回滚

- 回滚 = 关 `self_talk_ai_enabled`（或清空性格），行为与改动前逐字相同；键不删，
  无迁移负担；
- 最坏面：模型端点不可用 → 12s 内回落固定句（有上限，不会久等）；在飞 worker
  由 `_LIVE_WORKERS` 强引用 + `aboutToQuit` 收口，进程退出不带飞线。
