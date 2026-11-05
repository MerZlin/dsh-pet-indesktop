# petcore ↔ v4.3 / v4.4 功能对照与重写记录（2026-11-05 快照）

> **基线**：`feat/petcore-vpet-architecture` @ `33ddbbd1`（L166–L170 之后）
> **对照对象**：主分支 v4.3.0（2026-10-15）→ v4.3.4（2026-10-19）、v4.4.0（2026-10-22）
> **性质**：**历史快照**，不是长期权威清单；后续每轮收口请更新
> [`PETCORE-MAIN-PARITY-BACKLOG.md`](PETCORE-MAIN-PARITY-BACKLOG.md) 并在本文追加「第 N 轮」。
> **关联**：[`PETCORE-FUNCTION-MATRIX-2026-10-22.md`](PETCORE-FUNCTION-MATRIX-2026-10-22.md)
> （上一版 v4.3.4 矩阵，本文在它之上补到 v4.4.0 并改写 core/mod 归属与重写记录）、
> [`PETCORE-SPEC.md`](PETCORE-SPEC.md)（core/mod 边界与最终形态）、
> [`PETCORE-LOG-INDEX.md`](PETCORE-LOG-INDEX.md)（L166–L170 的逐条改动史）、
> [`PR-REPORT-QQ-BRIDGE-AND-UPSTREAM-AUDIT-2026-11-05.md`](PR-REPORT-QQ-BRIDGE-AND-UPSTREAM-AUDIT-2026-11-05.md)（本轮 QQ/上游审查）。

## 0. 口径

- **已做 / core**：能力在 `petcore/` 里，不装任何 mod 也有。
- **已做 / 内置 feature**：在 `petcore/builtin/features/`，随 core 的 `_internal` 分发、
  始终加载；不是用户可安装的 mod。
- **已做 / mod**：在 `examples/petcore-mods/`，安装器 `mod_*` 任务默认**不勾选**；
  不装就不会出现在菜单/设置里。
- **未做**：petcore 没有对应能力，或只有入口但能力明显弱于主分支。
- **形态不同**：功能存在，但实现/入口与主分支不是一一对应，不能按「没做」计。

## 1. 一句话结论

v4.3 的**截图提问、AI 五连、灵动岛、音乐/歌词、免打扰、直播捕获、帧序列、多屏/多宠、
设置/菜单、全局快捷键**都已在 petcore 落地；其中 AI 对话、音乐、OCR、天气、文件解读、
网页 AI、Agent、抽签/节日/待办/报时、QQ 被**移进 mod**，core 只保留端口、UI 壳、会话、
网络、语音、记忆、素材、更新与扩展框架。

真正没做的主要是 **v4.4 的三件 AI 深化**（场景扮演、分层记忆 v2、聊天分页）和几个
单点：**HDR 截图、AI 通用时间感知、foobar2000 窗口标题源、设置 Ctrl+F**；此外
**非 Windows 平台层**未移植。统一 16 项「功能页」、聊天窗关闭销毁、Android 是**形态不同**，
不是能力缺失。

## 2. 功能对照

### 2.1 已做，放在 core

| 4.3/4.4 功能 | petcore 落点 | 说明 |
|---|---|---|
| 截图框选 / 动作条 / 复制图像 | `petcore/ui/screen/selection.py`、`petcore/contracts/screen.py` | `ocr/translate/ask/copy` 四动作；OCR 与视觉问答由 mod 接 |
| Markdown / 深度思考 / 联网搜索 / 会话路由 | `petcore/ui/chat/`、`petcore/api/chat.py`、`ai.chat` | 正式聊天窗、快捷气泡、经典窗共用 Markdown；思考与联网在 `ai.chat` |
| 灵动岛段化 / 第三方段 | `petcore/island/`、`petcore/ui/island.py` | 段词表、布局、第三方注册与保留字、折叠规则 |
| 免打扰 / 全屏自动隐藏 / 直播捕获 | `petcore/ui/`、`petcore/config.py` | `quiet`、`auto_hide_fullscreen`、`stream_capture_mode` |
| 帧序列 v3/v5（无损 WebP 帧） | `petcore/media/provision.py`、`petcore/content/` | 长 clip 仍走视频路径（混合制） |
| 素材包市场 / 应用内下载 | `petcore/market/`、`petcore/ui/settings/page_market.py` | 下载、校验、导入；不自动安装 |
| 拖拽弹簧 / Q 弹 / 回旋 / 弹弓 | `petcore/engine/`、`petcore/ui/effects.py` | 与主分支手感对齐 |
| 多宠 / 每宠配置 / 落座避让 | `petcore/ui/pet_manager.py`、`config-slot-N.json` | 单进程多宠，上限 99 |
| 每屏一窗 / 跨屏 / 屏内坐标 | `petcore/ui/overlay_shell.py`、`petcore/screen_host.py` | 见 §3 重写 1 |
| 设置页 / 菜单编排 / 搜索到行 | `petcore/ui/settings/`、`petcore/menu_templates/` | 8 个主页面 + 页内分页 + 菜单编排器 |
| 语音底层 / TTS provider / 缓存键 | `petcore/speech/`、`petcore/net/tts.py` | edge/sapi/mimo/doubao；缓存按实际 flavor |
| 余额 / 检查更新 / 快捷键 / 托盘 | `petcore/account/`、`petcore/update/`、`petcore/platform/hotkeys.py` | 余额单飞、更新下载校验、三动作全局快捷键 |
| FPS/GPU/网络监控探针 | `petcore/platform/fps.py` 等 | Windows only；真实 FPS 仍受 Performance Log Users 权限限制 |

### 2.2 已做，放在 mod（安装器默认不勾选）

| mod（版本） | 对应 4.3/4.4 功能 | 备注 |
|---|---|---|
| `ai.chat` 1.6.0 | AI 对话、深思考/联网、识屏问答、记忆页、回复朗读、主动识屏、人设卡 | core 只保留聊天壳/会话/API |
| `tool.ocr` 1.0.0 | 本地 OCR（截图提取文字） | 失败回退 `ai.chat` 视觉 |
| `media.music` 1.2.0 | 音乐识别、歌词、B站标题清洗、四播放器、Firefox SMTC | 三源识别 + 四维歌词打分；foobar 窗口源未移植 |
| `tool.weather` 1.0.0 | 天气 / 城市 | 双源双出口 |
| `tool.file-reader` 1.0.0 | 拖文件解读 | 只读、有界读取、入会话 |
| `tool.ai-browser` 2.0.0 | 网页版 AI 内嵌 | QtWebView + WebView2 |
| `ai.agent` 1.1.0 | DSH / Claude / Cursor / Codex / OpenCode 联动 | 只读外部源 |
| `fun.divination` 1.1.0 | 每日一签 | 4.3.1 |
| `fun.festival` 1.1.0 | 节日/生日提醒 | 4.3.1 |
| `reminder.todo` 1.1.0 | 待办 + 到点语音 | 4.3.3；`todo_reminder_speak` |
| `voice.chime` 1.1.1 | 整点报时 + 语音 | 4.3.0/4.3.1 |
| `im.qq` 0.1.0 | QQ 互通（OneBot） | 4.3 没有，petcore 新增 |

### 2.3 内置 feature（随 core，不是用户 mod）

`fun.self-talk`、`fun.click-talk`、`tool.links`、`tool.memory-trim`、
`tool.network-info`、`tool.quick-launch`、`tool.quiet-mode`。

这 7 个是「core 包内的 feature mod」：不经过安装器勾选、不走用户数据目录的
`mods/`，但仍通过 `FeatureManager` 的贡献/端口机制加载。回答「算 core 还是 mod」时，
它们应算 **core 侧的内置 feature**，不是 `examples/petcore-mods` 的可安装 mod。

### 2.4 未做 / 部分 / 形态不同

| 项 | 来源 | 结论 | 证据 / 现状 |
|---|---|---|---|
| HDR 截图（FP16 + SDR 白点） | 4.3.0 | **未做** | `petcore/platform/screenshot.py` 只用 Qt `grabWindow` / GDI `BitBlt`，无 HDR 通道；`ui/screen/overlay.py` 提到 HDR 只是压暗值 |
| AI 通用时间感知（现在几点、距上条多久） | 4.3.0 | **未做** | 只有 `petcore/api/chat.py:432` 的联网检索日期；正式对话 prompt 无时间注入 |
| foobar2000 窗口标题源 | 4.3.0 | **未做** | `examples/petcore-mods/media.music/host/now_playing.py:22` 明确「未移植」；只做 SMTC/WASAPI |
| 设置 `Ctrl+F` 直接搜 | 4.3.4 | **未做** | `ui/settings/dialog.py:203` 有搜索框，但无 `QShortcut`/`Ctrl+F` |
| 场景扮演（场景卡 + 在场人物 + 动作段） | 4.4.0 | **未做** | `grep roleplay` 在 `petcore/` 与 `examples/petcore-mods/` 零命中；`ai.chat` 只有人设卡 |
| 分层记忆 v2（user/fact/lesson/topic + keywords + 14 天归档） | 4.4.0 | **未做** | `interop/memory.py` 的 `MemoryEntry` 是 importance/decay/relevance；无 layer/keywords/topic 归档；`recall()` 是相关度 top-K |
| 长会话聊天分页 | 4.4.0 | **未做** | `ui/chat/window.py:1462` `load_messages()` 一次灌入；`controller.py:339/343` 直接传 `session.messages` |
| 非 Windows 平台层 | 4.3.0/4.3.1/4.4 | **未做** | `petcore/platform/` 只有 `win.py` 等 Windows 实现；macOS 失活、Spaces、非 Windows 穿透均未移植 |
| 统一 16 项「功能」页 | 4.4.0 | **形态不同** | `page_mods.py` 有启用/停用 + 安装器 `mod_*` 任务；7 个内置 feature 不在此页；island/live capture/cross-screen 走各自设置键。没有单一 16 项勾选页，但模块化能力已在 |
| 聊天窗关闭即销毁 | 4.4.0 | **形态不同** | `ui/chat/window.py:1931` 的 `closeEvent()` 是隐藏复用 + 会话保留，不是销毁 |
| Android 0.2.x | 4.3.1 | **独立产品** | `android/` 是 Java 原生应用，不在 petcore setup；petcore 仍是 Windows 桌面核心 |
| macOS Spaces / 输入法焦点 / Qt 插件路径消毒 | 4.4.0 | **未移植** | petcore 无 macOS 平台层；`QT_PLUGIN_PATH` 消毒在 petcore 未找到 |

## 3. 重写记录（怎么重写）

1. **多屏 / 多宠**
   - 主分支 4.3.3 是「一张合成窗铺满虚拟桌面」，4.3.4 退回「每块屏一张合成窗」。
   - petcore 改成 **每块被占用的显示器一个 `ScreenHost` + `OverlayWindow` + `PetSprite`**：
     每屏有自己的行为/碰撞/物理世界，坐标是**屏内坐标**；`screen_name` 记录宠物属于哪块屏，
     `move_sprite_to_screen()` 是正常拖拽跨屏的缝；`TickDriver`、共享服务、菜单/设置仍全进程单例。
   - 收益：混合 DPI 不依赖一张窗的单一 DPR；两块屏的坐标/碰撞不会互相污染；跨屏拖拽是显式
     「移交归属」，不是把一个 sprite 从一个宿主搬到另一个宿主。
2. **聊天**
   - 主分支：`pet/chat` 单体 + 现代/经典两套窗口 + 快捷气泡。
   - petcore：core 的 `ChatPort` / `ChatController` / `ChatSessionStore` + `ChatWindow` 视图栈；
     `ai.chat` 只负责 prompt/人设/记忆/动画；DSH、QQ、`chat_view` 都走同一套 channel/view。
   - 收益：AI 功能可整体停用/卸载；core 聊天壳不为某个模型/人设写死。
3. **AI 对话**
   - 从 core 单体拆成「core 窗口/会话/API/工具 + `ai.chat` 业务」。所以 AI 功能在
     设置/扩展里可停用，core 仍保留完整聊天窗和 API 层。
4. **设置 / 菜单**
   - `pet/settings_widgets.py` 与旧右键菜单 → `petcore/ui/settings` 多页 + `menu_templates` +
     `MenuModel`/编排器；搜索、作用域、覆盖矩阵、页内分页都进 core。
5. **素材 / 动画**
   - live WebM 逐帧解码 → 无损帧序列 + `ClipLibrary` + 素材包市场；长 clip 保留视频路径（混合制）。
6. **语音**
   - `pet/tts` 的 edge 单后端 → `SpeechPort`/`SpeechService` 的 edge/sapi/mimo/doubao + 缓存键
     按实际合成输入（provider/voice/rate）生成 + 进程内单音频 worker；mod 只发 `SpeechRequest`。
7. **识屏**
   - core 只保留抓屏、框选、动作条、剪贴板；OCR 给 `tool.ocr`，视觉问答给 `ai.chat`，
     动作按 priority 回退；抓屏范围是虚拟桌面（所有显示器并集）。
8. **记忆**
   - 从「每轮全量 JSONL 注入」改成 `MemoryPort` 的 importance/decay/相关度 top-K +
     DSH/feiyu 只读外部源 + 记忆管理页；**但没有 4.4 的 user/fact/lesson/topic 分层 v2**。
9. **扩展体系**
   - 旧版固定功能 → mod manifest/`.trusted`/安装卸载/启用停用 + 7 个内置 feature +
     安装器 `mod_*` 勾选任务；这是 4.4「功能模块化」在 petcore 里的对应形态。
10. **灵动岛**
    - 从单体 → `island/` 的段词表、布局、第三方注册；第三方段有 id 保留字、120px 超宽规则和折叠顺序。
11. **QQ 互通（新增）**
    - 4.3 没有；petcore 以 `im.qq` mod 接 OneBot v11 HTTP：core 持有 `WebhookPort`
      （127.0.0.1、token、常量时间比较），mod 负责白名单、会话映射、AI 回复与通知。
    - 本轮修了设置页读错 `api_credentials`、裸 `Authorization`/查询串兼容与 token 持久化回读，
      见 [`PR-REPORT-QQ-BRIDGE-AND-UPSTREAM-AUDIT-2026-11-05.md`](PR-REPORT-QQ-BRIDGE-AND-UPSTREAM-AUDIT-2026-11-05.md)。

## 4. 本轮交付（同一快照）

- 代码/文档提交：`33ddbbd1`（L166–L170），已推送
  `origin/feat/petcore-vpet-architecture`。
- 签名安装包：`rel-storage-2026-11-05a/dsh-pet-core-setup.exe`
  - 315,747,992 B；SHA-256 `5d9abf14f25d00823a3cefca2ffec7a418efaee379a598747ff56eabe4b42077`
  - 签名 Valid（`CN=DSH Pet Core Dev`）；构建内 `--check --strict` 退出 0
- 门禁：petcore 子集 **3559 passed, 1 skipped**；`ruff` 全绿。
  仓库全量仍在 legacy `tests/test_quick_chat.py::test_switch_character...` 原生崩溃
  （`EXIT:139`），该用例单独 9 passed，属 **L137** 已登记的旧套件顺序性问题。

## 5. 维护提示

- 本文是 **2026-11-05 的快照**。新增/补齐上面「未做」项后，请在本文追加「第 N 轮」并
  同步 [`PETCORE-MAIN-PARITY-BACKLOG.md`](PETCORE-MAIN-PARITY-BACKLOG.md)；不要直接改旧表
  当成当前状态。
- 判断「算 core 还是 mod」时，以 [`PETCORE-SPEC.md`](PETCORE-SPEC.md) 的 core/mod 边界为准；
  本文的 7 个内置 feature 不是 `examples/petcore-mods` 的可安装 mod。
- 查具体改动史走 [`PETCORE-LOG-INDEX.md`](PETCORE-LOG-INDEX.md) 的 L166–L170；
  查 QQ 本轮证据走上面链接的 PR 报告。
