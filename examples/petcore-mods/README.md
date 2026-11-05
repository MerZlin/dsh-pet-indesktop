# 可导入的 petcore feature mod 样板

这 12 个目录是**可安装的 feature mod 包**，不是 core 内置。它们是照着官方文档写的参考实现，
适合复制结构、抄端口用法、对照排错。安装与字段规则见
[`../../docs/PETCORE-MOD-DEVELOPMENT.md`](../../docs/PETCORE-MOD-DEVELOPMENT.md) 与
[`../../docs/PETCORE-MOD-FORMAT.md`](../../docs/PETCORE-MOD-FORMAT.md)。

| 目录 | 版本 | 一句话 |
|---|---|---|
| `fun.divination` | 1.1.0 | 48 签、同日全宠共享/轮次去重、完整解签、首次互动提示与可选语音 |
| `fun.festival` | 1.1.0 | 公历/农历/24 节气/浮动节日、本地生日与可选自动提醒（默认关闭） |
| `reminder.todo` | 1.1.0 | 按宠物隔离的待办 CRUD、一次性/每日、提前/到期与下一条预览 |
| `voice.chime` | 1.1.1 | 语音报时与节日让位（走 core `SpeechPort`：edge / SAPI / 豆包 / MiMo） |
| `media.music` | 1.2.0 | Now Playing（SMTC）+ 歌词 + 唱跳动画 + 播放器启动 |
| `ai.chat` | 1.6.0 | AI 对话 + 看图 + 截图提问 + 快速对话编排 + 可选表情包回复（彩蛋目录，GIF 会动；需先配 API 服务与密钥） |
| `tool.ocr` | 1.0.0 | 截图 OCR（本地引擎 / 已配置 AI 兜底） |
| `tool.weather` | 1.0.0 | 天气实况（中国天气网直连 + open-meteo 代理兜底） |
| `tool.file-reader` | 1.0.0 | 拖文件解读（读文本类文件 → AI 摘要，只读不删） |
| `tool.ai-browser` | 2.0.0 | **`chat_view` 样板**：在 AI 对话窗里加一页「网页 AI」（QtWebView + Edge WebView2） |
| `ai.agent` | 1.1.0 | Agent 联动（DSH 桥目录 / 统一 `agent-events/*.jsonl` / Claude Code hooks / Cursor / Codex / OpenCode；六态动画 + 进度气泡 + 完成通知；Claude hooks 只在设置页显式安装） |
| `im.qq` | 0.1.0 | OneBot v11 / QQ 互通（入站 Webhook + 出站 `HttpPort`，Qt-free 运行时） |

> 版本与能力以各包 `mod.json` 为准。这些包要求 Core `>=0.2.0,<1.0.0`（部分要求 `>=0.4.0`）、
> API `1`；本地源码能力不等于已经上架，候选 ZIP/远程地址仍待分发阶段。

## 安装

```bash
.venv/Scripts/python -m petcore mods install examples/petcore-mods/fun.divination
.venv/Scripts/python -m petcore mods install examples/petcore-mods/fun.festival
.venv/Scripts/python -m petcore mods install examples/petcore-mods/reminder.todo
.venv/Scripts/python -m petcore mods install examples/petcore-mods/voice.chime
.venv/Scripts/python -m petcore mods install examples/petcore-mods/media.music
.venv/Scripts/python -m petcore mods install examples/petcore-mods/ai.chat
.venv/Scripts/python -m petcore mods install examples/petcore-mods/tool.ocr
.venv/Scripts/python -m petcore mods install examples/petcore-mods/tool.weather
.venv/Scripts/python -m petcore mods install examples/petcore-mods/tool.file-reader
.venv/Scripts/python -m petcore mods install examples/petcore-mods/tool.ai-browser
.venv/Scripts/python -m petcore mods install examples/petcore-mods/ai.agent
.venv/Scripts/python -m petcore mods install examples/petcore-mods/im.qq
```

或一键：

```bash
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/install_example_mods.ps1
```

安装后还必须在设置 →「扩展管理」勾选「允许功能扩展」，然后点「重新加载扩展」；
也可以启动时加 `--allow-feature-mods`。**feature 包只从用户数据目录
`%APPDATA%/dsh-pet-core/mods/`（或安装器投放的 `{app}/mods`）加载**；
仓库里的 `examples/` 不会被直接执行，放在 `<repo>/mods` 或 `--mods-dir` 的 feature 永远是 disabled。

### Windows 安装包

正式安装包把这些包做成了安装向导里的可勾选项（默认不勾选）。勾选后才装到
`{app}\mods\<id>`，并附一个 `.trusted` 标记——那是「安装器逐包授权」的证据，
带着它就不需要再勾「允许功能扩展」。不勾选就完全不会写盘；卸载时随安装目录一起删除。

## 包格式

```text
<id>/
  mod.json            schema=1 / kind=feature / execution=host-only / entrypoint
  host/factory.py     create_host(context) -> FeatureDefinition
  # 或包入口：host/__init__.py:create_host（loader 支持相对 import）
```

规范见 [`../../docs/PETCORE-MOD-FORMAT.md`](../../docs/PETCORE-MOD-FORMAT.md)；
从零写一个扩展的动手指南（最小骨架、接线、安装与排错）见
[`../../docs/PETCORE-MOD-DEVELOPMENT.md`](../../docs/PETCORE-MOD-DEVELOPMENT.md)；
想让 AI 代写就把 [`../../docs/PETCORE-MOD-AI-GUIDE.md`](../../docs/PETCORE-MOD-AI-GUIDE.md) 整份交给它。

## 可用的宿主端口

`context.ports` 提供（有实现的 22 个）：

- `presentation`：`say` / `say_sequence` / `show_image` / `notify` / `play_animation` / `play_event`
- `scheduler`：`call_later` / `call_every` / `cancel` / `cancel_owner`
- `storage`：`data_dir(owner)` / `config_dir(owner)` / `data_root()`
- `config`：只读 `get(key, default)`；`set` 只给菜单勾选/命令切换
- `pets`：只读宠物句柄（`body_rect` / `say` / `play_animation` / `animations` / `config`）
- `interaction`：`on_click` / `on_drop` / `on_tick`（`on_drop` 拿到已投喂的文件路径）
- `quiet`：进程级免打扰
- `reminders`：带守卫的提醒交付
- `http`：普通取数 `request(HttpRequest)` / `get_json(...)`；路由与错误分类在 core
- `speech`：`providers/preflight/synthesize/speak/stop/set_secret`
- `chat` / `quick_chat`：对话窗与快速气泡（窗口、Markdown、会话在 core）
- `api`：LLM 服务门面（SSE/代理/错误分类在 core）
- `screen` / `screen_ask`：截图与截图问答
- `audio` / `credentials` / `resources` / `system` / `memory` / `webhook` / `worker`

**恒为 `None`（不要照 dataclass 写）**：`windows`、`menu`、`commands`、`input`、`capabilities`。
端口完整选型与线程约束见开发指南 §4。

运行时代码不要 `import PySide6`；需要回 GUI 线程用 `ports.scheduler.call_later()`。
只有设置页（Core 托管的受信 UI）可以用 QtWidgets 并使用 `context.host.config`（ScopedConfig）；
跨域能力仍应走端口/事件，不直接 `import pet.*`。

## 约定

- **真正生效的 contribution 只有 `menu` / `settings_page` / `chat_view`**；
  `command` / `hotkey` / `card` / `install_hook` 没有消费点，`sounds/themes/phrases/seasonal`
  运行期不消费（只有 `character` 块会被加载）。
- 菜单项 id 在 Core 内统一为 `<owner>:<contribution>`；命令表是 runtime 的 `commands` 字典。
- 安装后菜单贡献默认进入「扩展」子菜单；可在设置 →「菜单」拖到根或其它子菜单。
- 有菜单贡献的**示例/内置**包必须同时提供 `settings_page`（测试强制）；第三方扩展这是约定。
- 卸载只删除数据目录下的包；`feature-data/<owner>`、配置键、钥匙串一律保留。
- 改 `chat_view` 代码后必须重启应用；改设置页要关掉设置窗再打开。详见开发指南 §7.3。
