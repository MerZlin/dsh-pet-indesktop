# 可导入 petcore mod 样板

这 10 个目录是**可安装的 feature mod 包**，不是 core 内置：

- `fun.divination`：1.1.0：48 签、同日全宠共享/轮次去重、完整解签、首次互动提示和可选语音
- `fun.festival`：1.1.0：公历/农历/24 节气/浮动节日、本地生日和可选自动提醒（默认关闭）
- `reminder.todo`：1.1.0：按宠物隔离的待办 CRUD、一次性/每日、提前/到期与下一条预览
- `voice.chime`：1.1.1：语音报时与节日让位（走 core `SpeechPort`：edge / SAPI / 豆包 / MiMo）
- `media.music`：Now Playing（SMTC）+ 歌词 + 唱跳动画 + 播放器启动
- `ai.chat`：AI 对话 + 截图提问（需要先在「设置 → API 与账户」配置服务与密钥）
- `tool.ocr`：截图 OCR（本地引擎 / 已配置 AI 兜底）
- `tool.weather`：天气实况（中国天气网直连 + open-meteo 代理兜底）
- `tool.file-reader`：拖文件解读（读文本类文件 → AI 摘要，只读不删）
- `tool.ai-browser`：1.0.0：只开国模 AI 对话站的轻量浏览器（DeepSeek/豆包/千问/Kimi/文心/智谱/元宝/星火；地址栏、站点按钮与主框架跳转共用白名单；宿主无内嵌 Chromium 时自动降级 Edge 应用窗口/系统浏览器）
- `ai.agent`：Agent 联动（读 DSH 桥目录 / `agent-events/*.jsonl`，六态动画 + 进度气泡 + 完成通知）

> 实现、自动化和实机验收分别记账，见
> [P4-c3 E 与提醒域联合验收](../../docs/PR-REPORT-PETCORE-P4C3-E-2026-10-24.md)。这四个版本要求 Core `>=0.2.0,<1.0.0`、API `1`；本地源码能力不等于已经上架，候选 ZIP/远程地址仍待后续分发阶段。

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
```

或一键：

```bash
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/install_example_mods.ps1
```

安装后还必须在设置 →「扩展管理」勾选「允许功能扩展」，然后点「重新加载扩展」；也可以重新启动 petcore。
feature 包只从用户数据目录 `%APPDATA%/dsh-pet-core/mods/` 加载，仓库里的 `examples/` 不会被直接执行。

### Windows 安装包

正式安装包（`dist/dsh-pet-core-setup.exe`）把这些包做成了安装向导里的可勾选项（默认不勾选），
勾选后才装到 `{app}\mods\<id>`（安装目录下），并附一个 `.trusted` 标记文件——那是
「安装器逐包授权」的证据，带着它就不需要再勾「允许功能扩展」。不勾选就完全不会
写盘；卸载时随安装目录一起删除。

## 包格式

```text
<id>/
  mod.json            schema=1 / kind=feature / execution=host-only / entrypoint
  host/factory.py     create_host(context) -> FeatureDefinition
  # 或包入口：host/__init__.py:create_host（loader 支持相对 import）
```

规范见 [`../../docs/PETCORE-MOD-FORMAT.md`](../../docs/PETCORE-MOD-FORMAT.md)；从零写一个扩展的动手指南（最小骨架、接线、安装与排错）见 [`../../docs/PETCORE-MOD-DEVELOPMENT.md`](../../docs/PETCORE-MOD-DEVELOPMENT.md)。

## 可用的宿主端口

`context.ports` 提供：

- `presentation`：`say` / `play_animation` / `play_event` / `show_image` / `notify`
- `scheduler`：`call_later` / `call_every` / `cancel`
- `storage`：`data_dir(owner)` / `config_dir(owner)`
- `config`：只读配置 `get(key, default)`；运行时代码不要直接持有 `host.config`
- `interaction`：`on_click` / `on_drop` / `on_tick`（`on_drop` 拿到的是已投喂的文件路径列表）
- `pets`：只读宠物句柄（含 `play_animation` / `play_event`）
- `http`：普通取数 `request(HttpRequest)` / `get_json(url, params=…)`；路由与错误分类在 core
- `speech`：`providers/preflight/synthesize/speak/stop/set_secret`；合成、缓存与密钥在 core
- `presentation.play_animation(name, loop=True)` / `release_loop()`：持续动作（唱歌/报时）
- `quiet`：进程级免打扰

运行时代码不要 import PySide6；需要回 GUI 线程用 `ports.scheduler.call_later()`。
只有设置页（Core 托管的受信 UI）可以用 QtWidgets 并直接使用 `context.host.config`
（ScopedConfig）。跨域能力仍应走端口/事件，不直接 import `pet.*`。

## 约定

- 有 `Contribution(kind="menu")` 的 mod 必须同时提供 `Contribution(kind="settings_page")`。
- 菜单项 id 在 Core 内统一为 `<owner>:<contribution>`。
- 安装后菜单贡献默认进入「扩展」子菜单；可在设置 →「菜单」拖到根或「工具与帮助」。
- 卸载只删除用户数据目录下的包；`examples/` 源目录不会被修改。


## 提醒偏好与数据（P4-c3）

- 设置仍按宠物隔离；自动节日/生日与所有新增语音默认关闭，生日默认空。手动节日查询不是自动提醒授权。
- 待办旧文本清单无损转为无时间条目；默认提前5分钟（0–60），迟到10分钟以内补发，超时跳过。关闭宠物只暂停其调度。
- 待办/节日的v2状态按slot存储；`daily-v2.json`签文与轮次进程级共享。原子写成功后才交付，设置取消不撤销已触发记录。
- 旧`daily.json`当天签文保留，次日进入48签；旧配置/迁移备份不删除。代码回滚也不删除v2，旧代码只能读取旧格式数据，不会把新调度变回旧清单。
- 免打扰期间有界暂存，解除后每宠一份跨扩展文字摘要，不补播语音；进程被终止会丢失尚未交付的内存摘要。
- 完整设置/数据契约见[MOD-FORMAT §17](../../docs/PETCORE-MOD-FORMAT.md)，逐批记录见[L059–L061](../../docs/PETCORE-LOG-INDEX.md)。
