# PR 报告：用户反馈八组需求（2026-10-30）

## 1. 修改文件说明

基线：`feat/petcore-vpet-architecture`，起点 `7cfe023c`（已推送到 origin）。
工作区统计：**41 个已跟踪文件修改（+2700 / −103）**，另有 **26 个新文件/目录**
（约 4.1k 行，含 6 个人设 JSON 与 1 个 DSH 通道插件）。

### 动画治理（用户反馈 1）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `petcore/engine/animation_policy.py`（新，138 行） | 12 类分类 + `auto_off`/`blocked`/`random_auto` 策略 | 把「排除法随机池」换成正式分类；支持逐动画/逐类开关 |
| `petcore/engine/behavior.py` | `BehaviorDirector` 接 `policy_provider`，四类候选按策略过滤；`random_auto=false` 时 act 权重归零 | 随机动作总开关与「仅手动」必须作用在掷骰处，而不是菜单层 |
| `petcore/ui/pet_window.py` | `_animation_policy()`/`_pick_manual()`；待机/点击/拖拽/抛掷/摸头/`play_animation`/`play_event` 统一闸门；待机全过滤时保底播第一个 idle | 任何入口都不能绕过用户禁用；待机不许把桌宠冻住 |
| `petcore/ui/menu_actions.py` | 菜单按 `manual_allowed` 过滤，新增「拖拽/悬起」组 | 禁用项不该在菜单里出现；手动播放与自动池分离 |
| `petcore/ui/settings/page_interaction.py`（+231） | 「随机动作」总开关 + 概率；动画树（自动/允许两列）；全部允许/仅手动/禁用/刷新 | 用户能一眼看到当前角色的全部动画并精细控制 |
| `petcore/config.py` | `anim_random_auto` / `anim_auto_off` / `anim_blocked`，钳制与去重 | 每宠作用域；重启保留；名单跨角色保留 |

### 聊天语义动作（用户反馈 1 的 AI/DSH 部分）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `examples/petcore-mods/ai.chat/host/animation_semantics.py`（新，153 行） | 关键词规则 + 中文名匹配 + 防重复 + 思考动作 | AI 回复没有动作语义；需要纯逻辑、可测的映射 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | 首个 delta 播思考、done 播语义动作；`ai_animation_*` 三个键；隐藏/循环/接管态闸门 | 「生成时播放、可打断原动画」但不能抢音乐循环或拖拽 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | 「对话动作」段（开关/思考/最短间隔/朗读回复） | 可关闭，避免把桌宠当噪音源 |
| `examples/petcore-mods/ai.agent/host/links.py` | 六态动作池（thinking/attention/success/error），保留 8s 地板与「还在播不重发」 | 原来只有 busy 一种动作；DSH 状态更细 |
| `examples/petcore-mods/ai.agent/host/protocol.py` / `runtime.py` / `settings_page.py` | `PetCommand` 解析 + `_apply_command`（动作名校验/1s 限流/隐藏不播）+「接受 DSH 指令」开关 | DSH 控制桌宠的最小安全消费端 |

### 聊天字体 / 人设 / 表情 bug（用户反馈 4、5）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `petcore/ui/chat/styles.py` | 17 处字号改 `@F_N@` 比例派生 + `@FONT_FAMILY@`；范围 10–24 | 「所有字号和字体可自定义」只改一个基准即可整体缩放 |
| `petcore/ui/chat/window.py` / `controller.py` | `font_family`、`set_font_family`、`apply_appearance` 推送；`chat_font_size`/`chat_font_family` 读取 | 只作用对话窗，实时生效，不重开窗 |
| `examples/petcore-mods/ai.chat/persona_presets/*.json`（新 6 个） | 鲸鱼娘女仆/温柔/傲娇/元气/冷静/猫娘 | 基础人设卡可选，不需要用户从零写提示词 |
| `examples/petcore-mods/ai.chat/host/persona_presets.py`（新，94 行） | 预设加载 + `ai_persona` 解析（预设/custom/未知回退） | 坏文件/未知 id 不崩；旧自定义提示词继续生效 |
| `petcore/ui/easter_egg.py` | `selected_image()` 注入 RNG，`rng.choice(candidates)` | 修「随机只放第一张」；头像显式配置时仍固定 |

### 语音输入 / 语音对话 / TTS（用户反馈 2、7）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `petcore/platform/mic.py`（新，331 行） | WinMM `waveIn` 录音、设备枚举、WAV 封装；ctypes 边界可替身 | 全仓零录音代码；不新增原生 DLL |
| `petcore/platform/asr_windows.py`（新，98 行） | `System.Speech` 经 PowerShell 同步识别 WAV | SAPI COM 文件识别拿不到事件；System.Speech 实测可用 |
| `petcore/net/asr.py`（新，150 行） | OpenAI 兼容 multipart `/v1/audio/transcriptions`，只直连 | 对接 faster-whisper/FunASR 等成熟本地服务 |
| `petcore/speech/asr.py`（新，104 行） | HTTP / 系统兜底、临时 WAV 清理、稳定 reason | 统一识别服务；测试可注入两个识别器 |
| `petcore/ui/asr_channel.py`（新，154 行） | 单次录音线程 + Qt 信号；最长录音/取消 | GUI 不碰声卡；锁屏/睡眠可 cancel |
| `petcore/ui/chat/widgets.py` / `quick.py` / `window.py` / `controller.py` / `quick_controller.py` | 话筒按钮、录音态、识别结果填输入框/自动发送 | 完整聊天窗与快速气泡共用同一条链路 |
| `petcore/ui/settings/page_voice.py` | 「语音输入」段（引擎/地址/模型/语言/自动发送/最长录音/超时） | 本地 ASR 服务可配；系统识别免配置 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | `ai_voice_reply` 朗读回复（去 Markdown、≤500 字） | 语音对话闭环 |
| `petcore/ui/settings/page_voice.py` | 本地 TTS 段标题改「自定义 / 本地部署 TTS（HTTP）」 | v4.4.0 用户反馈找不到自定义 TTS 入口 |

### API 本地模型 + opencode（用户反馈 6）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `petcore/api/spec.py` | `models_path`/`extra_headers`/`allow_lan_http`/`preset`；私网 http 显式允许；密钥样式的头值拒绝 | 本地模型与 opencode 需要；api.json 仍不许出现 Key |
| `petcore/api/headers.py`（新，41 行） | opencode 主机补自己的 UA + 稳定 `x-opencode-session` | issue #368 的报头要求；不伪造 OpenCode 客户端 |
| `petcore/api/models.py`（新，95 行） | `GET /v1/models` 解析 | 本地模型/opencode 模型下拉 |
| `petcore/api/presets.py`（新，131 行） | DeepSeek/OpenCode 免费/Go/Ollama/LM Studio/llama.cpp/vLLM | 一键预设；DeepSeek 默认 |
| `petcore/api/chat.py` / `probe.py` | 合并自定义头；opencode 头同时用于探测 | 测试连接与实际请求同口径 |
| `petcore/ui/settings/page_api.py` | 预设下拉、模型列表、局域网 http、自定义头 | 用户不需要手填路径与模型 |
| `petcore/ui/app.py` | 首次启动 `api.json` 不存在时播种 DeepSeek + OpenCode 免费 | 初始携带免费选项，但 DeepSeek 第一 |

### DSH 双向通道（用户反馈 8）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `integrations/dsh-pet-channel/*`（新，约 750 行） | HTTP+SSE 通道、审批、历史、`pet_control` 工具、`/pet say｜play｜notify` 兜底、命令 JSONL | 桌宠 ↔ DSH 输入输出与状态互通 |
| `petcore/net/dsh.py`（新，230 行） | 读 `pet-channel.json`；ping/prompt/answer/cancel/history/SSE | 只连回环；token 只进头 |
| `petcore/ui/chat/dsh_controller.py`（新，108 行） | SSE 后台线程 + Qt 信号 | GUI 不阻塞 |
| `petcore/ui/chat/dsh_view.py`（新，332 行） | 同窗 DSH 时间线/工具卡/审批/输入/带入上下文 | 在一个聊天窗内切换 AI / DSH |
| `petcore/ui/chat/window.py` | `QStackedWidget` 双视图 + 标题栏 AI/DSH 切换 | 不新建第二个窗口、不新增 `(owner, slot)` 键 |
| `petcore/platform/dsh_install.py`（新，181 行） | 备份 `package.json` 后写 dependencies/bundles + 目录联接 | 用户显式点击才安装；可回滚 |
| `petcore/ui/settings/pages_core.py` | 「常规 → DSH 连接」安装/检查/状态 | 安装入口可见 |
| `petcore/AGENTS.md` | 「联动只读」改为「默认只读；用户显式点击可安装」 | 红线随需求更新，避免实现与守卫对撞 |

### 长期记忆（用户反馈 8.3）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `examples/petcore-mods/ai.chat/host/memory.py`（新，225 行） | global/character 两域 JSONL、中文 bigram 检索、500 上限淘汰、坏行跳过 | 零依赖、可手改、断网可用 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | 「记住…」规则写入；提交时取 top-K 注入 system；全局前缀 | 不改会话格式就能有长期记忆 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | 「长期记忆」段（开关/注入条数/上限/打开目录/清空当前角色） | 可查看可清除 |
| `petcore/config.py` | `ai_memory_enabled` / `ai_memory_inject` / `ai_memory_max_entries` | 全局作用域 |

### 测试与打包

- 新增测试：`test_petcore_animation_policy.py`（12）、`test_petcore_ai_animation.py`（10）、
  `test_petcore_ai_persona.py`（4）、`test_petcore_asr.py`（12）、
  `test_petcore_api_presets.py`（10）、`test_petcore_dsh.py`（8）、
  `test_petcore_ai_memory.py`（6）。
- 扩展既有测试：`test_petcore_agent_mod.py`（+98 行）、`test_petcore_playback.py`（+64）、
  `test_petcore_chat_ui.py`（+55）、`test_petcore_easter_egg.py`、`test_petcore_settings_voice.py`。
- `dsh-pet-core.spec` 与 `scripts/build_petcore.ps1` 增加 `integrations` 数据目录，
  安装包内设置页才能找到随包插件。

## 2. 性能分析

实测环境：本机 Windows，Python 3.12.10，`.venv`；样本与方法如下。

| 路径 | 实测 | 结论 |
|---|---|---|
| 行为掷骰（106 段素材、策略过滤） | 20,000 次 **557.6 ms = 27.9 µs/次** | 相比过滤前多一次列表扫描；每次待机圈末一次，稳态可忽略 |
| 语义动作匹配（100+ 候选） | 5,000 次 **296.0 ms = 59.2 µs/次** | 每次回复一次；可忽略 |
| 长期记忆检索（500 条 × 两域） | 2,000 次 **9.69 s = 4.8 ms/次** | 每次用户提交一次；相对于 LLM 请求可忽略；无新线程/网络 |
| 语音录音 | 2 秒得到 **63,662 字节** WAV（16 kHz/mono/16bit） | 录音线程按需创建，停止即退出；稳态 0 线程 |
| 系统识别兜底 | 已知 3 秒语音约 **1.0 s** 返回（PowerShell 子进程） | 每次识别一个短命进程；只在用户选「系统识别」时 |
| HTTP ASR | 本地 mock 服务 <10 ms（不含服务端模型） | 真实耗时取决于用户本地服务 |
| DSH SSE | 本地 fake 服务逐帧读取，无轮询/无额外线程（1 个连接线程） | 只在 DSH 页打开时建连，关闭窗口停止 |

新增系统调用/线程/磁盘：录音（waveIn 设备句柄，录音期间）、系统识别（每次一个
PowerShell 子进程）、DSH（1 条 SSE 线程 + 1 条短命 POST 线程/操作）、记忆（每次提交
读两份 JSONL，≤500 条）。无新增原生 DLL、无新增常驻轮询。

## 3. 实机运行记录

- **备份**：`git push origin feat/petcore-vpet-architecture`，远端从 `ac0340a9` 到
  `7cfe023c`；工作区干净。
- **录音**：`MicRecorder` 枚举到「麦克风阵列 (Senary Audio)」，录 2 秒得 63,662 字节
  RIFF/WAV；`AsrService` 系统兜底对 TTS 生成的「你好今天天气怎么样」识别为
  UTF-8 hex `e4bda0e5a5bde4bb8ae5a4a9e5a4a9e6b094e6808ee4b988e6a0b7`（即
  「你好今天天气怎么样」）。
- **opencode**：`probe_text(opencode-free, "")` → `ProbeResult(reason='rate_limit',
  http_status=429, retry_after=34556.0)`；与官方「免费模型要求 OpenCode 客户端」一致，
  因此 DeepSeek 保持默认，免费档只做可选预设。
- **本地模型端口**：127.0.0.1 的 11434（Ollama）、1234（LM Studio）、9931
  （llama.cpp）、8000（vLLM）均未监听；预设已内置，未做真实模型对话。
- **DSH**：本机 DSH 桌面端当前未运行，`pet-channel.json` 不存在；通道插件通过
  `node --check` 与 `verify_import.mjs`；Python 客户端/视图对本地 fake HTTP/SSE
  服务通过（ping/prompt/history/events/审批帧）。
- **卡死反馈**：见本文 §4；不成立，没有产品修复。
- **测试**：`pytest -q tests/test_petcore_*.py` = **2560 passed / 1 skipped**
  （196.60 s）；`ruff check petcore/ examples/petcore-mods tests/` 通过；
  `python -m petcore --check` 通过；docs / architecture / CI 门禁通过。
  全量 `pytest -q` 在 66% 处原生崩溃（exit 139，崩点
  `tests/test_quick_chat.py::test_switch_character_moves_session_and_prompt_to_new_character`），
  栈里是 `media.music` worker 与 `pet/overlay_shell` 自语法线程；该用例单独跑通过，
  `test_petcore_music_* + test_quick_chat` 组合 **216 passed**，因此按旧套件顺序性
  原生崩溃处理，未归因到本次改动（见 §5）。

## 4. 「桌宠卡死」反馈的核查结论

用户给的截图把原因归到 `ai.chat` 的抓图注释与右键菜单第一项。逐条核对：

| 说法 | 结论 |
|---|---|
| `ai.chat` L242 注释「抓图本身已经在 GUI 线程」 | 注释实际在 `examples/petcore-mods/ai.chat/host/runtime.py:316`；L242 是 `_active_slot()` 的 return |
| 「看看屏幕」是右键菜单第一项/很容易点到 | 默认模板顺序是 彩蛋 → 切换角色 → 分隔线 → 播放动画 → AI 对话 → 看看屏幕；本机实际布局里「看看屏幕」是第 5/6 项，第 2 项是「切换角色」 |
| 抓图 + 后续模型请求把主线程卡住 | 抓图在 GUI 线程（实测约 34–205ms，已登记），模型请求在 `ai-look-{slot}` 工作线程；后半段与代码相反 |
| 超过 5 秒被 Windows 判 AppHang | 本机事件日志近 4 天没有 `dsh-pet-core.exe` 的 1002；日志显示 10-29 19:43 的进程一直活到 22:37，20:25/20:31 没有重启；20:33 起 GUI 定时器仍正常输出 |

结论：**该分析不成立**，不做产品修复；只把它作为取证教训（应查 Windows 事件日志
1002 + WER/栈采样，而不是源码注释）。`git diff` 中没有为这条反馈改任何抓图/菜单代码。

## 5. 未验证项与风险

1. **DSH 真机双向**：DSH 桌面端未运行，未做 `/pet/ping`/prompt/审批真机连测；
   安装器只对 fake home 测试，没有写本机 `~/.dsh`。
2. **opencode 免费档**：实测 429；按用户要求保留为可选预设，DeepSeek 默认。
3. **本地 ASR 服务**：未起 faster-whisper/FunASR 服务；只验证系统兜底与 HTTP 客户端。
4. **打包**：未构建安装包（本机 SAC 强模式会拦新 exe）；spec/build 已加 `integrations`。
5. **DSH 通道安全边界**：本地 token 明文、回环端口可被本机进程探测；安装器会改
   `~/.dsh/profiles/desktop/package.json`（先备份），用户需重启 DSH。
6. **字体缩放**：基准字号 + 比例派生，不是逐控件独立字号；后续要逐元素微调需再加
   高级项。
7. **记忆**：规则写入（「记住…」）+ bigram 检索，不做自动摘要/向量；检索 4.8ms/次。
8. **v4.4.0 线未 backport**：所有功能进 petcore；桥接插件保持旧事件格式。
9. **全量套件原生崩溃**：66% 处 exit 139，崩点是旧版 `test_quick_chat.py` 的单条用例
   （单独跑通过；`petcore_music_* + test_quick_chat` 组合 216 passed）。未在本次改动
   里复现，按旧套件顺序性环境问题登记；petcore 全子套件 2560 passed / 1 skipped。

## 6. 文档

- `docs/PETCORE-LOG.md` L137 + `docs/PETCORE-LOG-INDEX.md` 一行。
- `petcore/AGENTS.md` 的「联动只读」改为「默认只读 + 用户显式安装」。
- 新增 `integrations/dsh-pet-channel/README.md`（安装/安全边界）。
- 本报告在 `docs/INDEX.md`「PR 报告存档」登记。

## 7. 复核修正（review 子 agent）

按 review 结果修了 9 处真实问题，均有聚焦测试：

1. ASR 取消没有终结信号 → `AsrChannel` cancel 分支发 `failed("cancelled")`，
   控制器清 `_recording_key`，不会永久卡「上一段语音还没结束」。
2. 长期记忆键不一致（运行时显示名 vs 设置页角色 id）→ 运行时统一用
   `config["character"]`；设置页与运行时现在读写同一文件。
3. 关闭聊天窗后重开 DSH 页是死视图 → `showEvent` 在 DSH 视图下重新 `activate()`。
4. 待机被「完全禁用」仍回退播放 → `BehaviorDirector.idle()` 不再回退到
   blocked/auto_off 素材；无可用 idle 时走静态 fallback，禁用语义优先。
5. DSH 用户消息重复两遍 → `DshView` 对连续同文 user 帧去重。
6. SSE 断线/401 被吞、永远显示已连接 → `events()` 发 `connection` 帧、指数退避、
   读超时 ≥2× keepalive；视图展示断开原因。
7. `DshClient.info()` 用前缀校验可被 `127.0.0.1.evil` 绕过 → 改用
   `urlsplit` 的 hostname 精确匹配 + 端口一致性校验。
8. 安装器 `@dsh-pet/bridge` 包名写错（把目录名当依赖键）→ 目录/包名分离；
   联接失败回滚 `package.json`；安装改到 `QThreadPool`，不再阻塞 GUI。
9. 动画分类 token（如 `["random"]`）被无关设置改动展开 → 页面新增
   `_anim_policy_dirty`，未编辑动画树时保留原始 token；取消也不再回写展开值。
10. 记忆淘汰文案与实现不符 → 改为「按创建时间淘汰」；`search_all` 先角色后全局，
    避免全局条目挤掉角色记忆。

另修 `MicRecorder.open()` 在 `waveInAddBuffer` 失败时漏 `unprepare`、
`_ASR_REASON_TEXT` 缺 `unavailable/too_large/not_recording/cancelled`、
API 页 insecure 文案仍写「只用于 localhost」。
