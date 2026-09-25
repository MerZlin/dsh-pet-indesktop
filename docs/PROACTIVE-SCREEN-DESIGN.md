# 主动识屏：设计与实施档案

> 本文是主动识屏相关设计稿与实施手册的统一入口（2026-09-24 整理）。
>
> 当前实现状态以代码、测试和最新 PR 报告为准；本文保留原始方案与实施手册，主要用于追溯设计决策、验收口径和历史修订，不应把早期“尚未动工”的描述视为当前状态。
>
> 原始文件已归档到 [`archive/PROACTIVE_SCREEN_PLAN.md`](archive/PROACTIVE_SCREEN_PLAN.md) 和 [`archive/PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md`](archive/PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md)。

## 当前阅读顺序

1. 先看本项目当前的实现代码和测试；
2. 再看本文的“实施手册”部分了解验证与迁移边界；
3. 需要追溯原始设计文字时，再打开归档原文。

## 当前路线边界

主动识屏属于插件化路线中后续的高风险能力，目标是迁移到独立 Worker，而不是让视觉模型、截图和网络请求直接阻塞 Core。当前 Phase 1/2 只建立资源 DLC 和 Core 插件运行时基础，不代表主动识屏已经完成 Worker 化。

---

## A. 原始设计方案

# 主动识屏（Proactive Screen Watch）与多 Agent 感知 — 已验证设计方案

> 版本：v1（2026-08，经联网调研与代码核实）
> 原则：**低功耗、多开友好、绝不打扰用户、一切敏感能力默认关闭、全部可配置**。

---

## 一、已验证的技术依据（每条都有出处）

| # | 技术点 | 结论 | 依据 |
|---|---|---|---|
| 1 | 前台窗口信息（进程名/标题/矩形） | ✅ 可行，Windows 用 Win32；`GetWindowRect` 返回物理像素，与 `ImageGrab` 像素坐标系一致 | 现有 `pet/vision.py foreground_app_info()` + MS Win32 文档 |
| 2 | 最大化窗口矩形含隐形边框 | ⚠️ 需用 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` 代替 `GetWindowRect` 取可见边界 | Win32 已知行为 |
| 3 | 多屏负坐标裁剪 | ✅ PIL `ImageGrab(all_screens=True)` 支持负坐标（左上角可为负）；裁剪用 `GetSystemMetrics(SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN)` 原点换算 | [Pillow ImageGrab 文档](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html) |
| 4 | 用户闲置检测 | ✅ Windows `GetLastInputInfo`（系统级、每会话）；macOS 用 `CGEventSourceSecondsSinceLastEventType`；Linux X11 用 XScreenSaver。一期仅实现 Windows | [MS GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo) |
| 5 | 画面变化检测 | ✅ Pillow 缩至 9×8 灰度 dHash + Hamming 距离，纯函数可单测；阈值默认 8/64 | dHash 标准算法 |
| 6 | 智谱免费视觉模型 | ✅ `glm-4.6v-flash` 免费、OpenAI 兼容、支持 `data:image/jpeg;base64`、可关思考模式 | [智谱 OpenAI 兼容文档](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)、[GLM-4.6V-Flash 文档](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.6v-flash) |
| 7 | GLM 免费档具体 RPM/TPM | ⚠️ 官方未公开具体数值 → 采用保守软流控（最小间隔 60s + 每日上限 15 + 429 指数退避 + 连续失败自动停一天），撞限流概率≈0 | 官方限流页无公开数值 |
| 8 | DeepSeek 视觉计费 | ✅ 每图最多 **384 token**（先等比缩到约 800×800）；输入 1.5/3.0 元每百万 token，输出 4.5/9.0 元每百万 token | [DeepSeek 定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[视觉 token 换算](https://api-docs.deepseek.com/zh-cn/guides/vision#token-usage) |
| 9 | DeepSeek 视觉请求格式 | ✅ base64 data URL、`detail: low` 可省 token、图片只允许在 user 消息 | 同上 |
| 10 | Claude Code hooks 事件 | ✅ `SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / PostToolUseFailure / Stop / SubagentStop` 等可执行本地命令 | [Claude Code Hooks 文档](https://code.claude.com/docs/en/hooks) |
| 11 | DSH 事件 | ✅ DSH 插件可通过 `ctx.on(...)` 监听事件（`tools/pre-execute`、`session/event` 等）；Clawd 用 DSH 插件事件驱动桌宠状态，字段见其 eventMap | [DSH 插件开发教程](https://dev.to/henry_lin_3ac6363747f45b4/deepseek-harness-dsh-cha-jian-kai-fa-jiao-cheng-4h6j)、[Clawd deepseek-harness.js](https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/agents/deepseek-harness.js) |
| 13 | mtime 轮询不可行 | ✅ 已确认是历史方案失败根因（批量刷盘、无法区分状态）；永久弃用 | 同上 |

---

## 二、相对此前方案的 4 处修正（重要）

1. **鼠标穿透不再阻断主动识屏**：`mouse_through=True` 时默认仍允许识屏（读屏只读画面、不交互，没有冲突）；但提供「鼠标穿透时仍主动识屏」选项，放右键二级菜单 + 设置页，默认开启，用户可关。
3. **截图必须在后台线程执行**：`ImageGrab` 全屏抓取单次约 50~200ms，绝不能在主线程做（会造成动画卡顿）；改为 worker 线程抓取→裁剪→dHash→信号回主线程，主线程只做毫秒级 Win32 判定。
4. **前台窗口矩形用 DWM 边界**：`GetWindowRect` 对最大化窗口含隐形边框，会多截 8px 边缘；改用 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`，失败时回退 `GetWindowRect`。

---

## 三、主动识屏最终漏斗（默认全关，用户开启后才存在）

```
QTimer 8s（仅 enabled && 可见时运行；禁用/隐藏时零定时器）
 ├─ 1) 桌宠守卫：isVisible()；鼠标穿透按「穿透时仍识屏」选项决定是否跳过
 ├─ 2) 白名单：前台进程名/标题 fnmatch 匹配（不区分大小写）
 ├─ 3) 停留门限：同一前台窗口连续 ≥ dwell_seconds（默认 60s）
 ├─ 4) 闲置守卫：GetLastInputInfo ≥ min_idle_seconds（默认 30s）
 └─ 5) 通过后 → 后台线程抓前台窗口区域
        ├─ 9×8 灰度 dHash，与上次同 app 快照比较
        ├─ 变化 < 阈值 → 丢弃（挂机/静止不触发）
        └─ 变化 ≥ 阈值 → 6) 频控
              ├─ 全局冷却 ≥ cooldown_minutes（默认 5 分钟）
              ├─ 今日次数 < daily_cap（默认 15）
              ├─ 距上次请求 ≥ 60s（免费模型硬下限）
              └─ 通过 → JPEG(最长边 768, q70) → 视觉模型 → 气泡
                   └─ 429：退避 2/4/8s ×3；连续 3 次失败 → 当天自动停用（仅日志，不打扰）
```

### 配置 schema（config.json 新增）

```jsonc
"proactive_screen": {
  "enabled": false,
  "allow_when_mouse_through": true,   // 用户要求：穿透不阻断，但可自主关闭
  "whitelist": [],                     // ["code.exe", "cursor.exe", "*bilibili*", "标题:某文档"]
  "dwell_seconds": 60,
  "min_idle_seconds": 30,
  "cooldown_minutes": 5,
  "daily_cap": 15,
  "min_request_interval_seconds": 60,
  "change_threshold": 8,
  "prefer_free_provider": true        // true=有 glm 视觉配置优先免费；否则用聊天 provider
}
```

### 成本与限流（已验证数字）

- **DeepSeek**：每图 ≤384 token 输入（官方上限），回复约 50~200 token 输出。
  - 单次最坏：`384×3.0/1e6 + 200×9.0/1e6 ≈ 0.0012 + 0.0018 = ¥0.003`；
  - 每天 15 次上限 ≈ **¥0.05/天封顶**，实际通常 <¥0.01。
- **GLM-4.6V-Flash**：免费；官方未公开 RPM/TPM，用「60s 最小间隔 + 15 次/天 + 429 退避 + 连续失败自动停」保证不撞限流。
- 本地端侧 VLM（Ollama/LM Studio）留作后续 `local` 选项，零成本。

### 性能账（不违背低功耗初衷）

- 功能关闭：**零定时器、零截图、零网络**。
- 功能开启：8s 一次 Win32 判定（微秒级）；只有白名单命中且停留+闲置满足才截图（1 次/8s，后台线程）；模型请求一天最多 15 次。
- 桌宠隐藏/全屏自动隐藏时随现有 `_pause_activity` 一起暂停。

### 与现有代码映射

| 新功能 | 位置 |
|---|---|
| `match_process_whitelist()`（纯函数） | `pet/proactive.py` 新增 |
| `image_dhash()` / `hamming()`（纯函数） | `pet/proactive.py` |
| `RateLimiter`（冷却/每日上限/持久化，纯函数+文件） | `pet/proactive.py` |
| `foreground_window_info()`（进程/标题/Rect/DWM 边界/虚屏原点） | 扩展 `pet/vision.py` |
| `capture_window_rect()`（后台线程抓取+裁剪） | `pet/vision.py` |
| `ProactiveWatcher`（8s QTimer + 漏斗 + 线程信号桥） | `pet/proactive.py`，挂到 `PetWindow` |
| 右键二级菜单「主动识屏」（开关/穿透允许/设置入口） | `pet/window.py` contextMenuEvent |
| 设置页（白名单、频率、上限、免费优先） | `pet/settings_dialog.py` |

---

## 四、多 Agent 感知（默认全关、逐 Agent 独立开关）


| Agent | 事件源（已验证） | 实现 |
|---|---|---|
| DSH | DSH 插件事件（`session/event` 等；Clawd eventMap：SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/Stop…） | 提供轻量 DSH 插件包，把事件写入 `agent-events/dsh.jsonl`；桌宠 tail |
| Claude Code | 官方 hooks（PreToolUse/PostToolUse/Stop/SubagentStop/…）执行本地脚本追加事件 | 开启时经用户确认把 hook 写入 `.claude/settings.json`；桌宠 tail `agent-events/claude.jsonl` |
| Cursor | `~/.cursor/projects/**/agent-transcripts/*.jsonl` | byte-offset tail（1.5s，仅最近目录，有界） |

统一输出：`(agent, session_id, state)` → 桌宠切动作（thinking→写代码/working→敲击桌面/attention→气泡提醒）。

**结论：Gemini 说“必须放弃 mtime 轮询”完全正确；你之前体验差的根因就是 mtime。方案可做好，但 DSH 部分要先读一遍 DSH 仓库 `docs/event-producer-consumer.md` 确认可用事件名再写插件。**

---

## 五、测试与验收清单（Phase 1 先做纯函数）

- `match_process_whitelist`：精确/通配/大小写/标题匹配/空名单=不匹配；
- `image_dhash`：同图距离 0；不同图距离大；9×8 灰度正确；
- `RateLimiter`：冷却、跨天重置、每日上限、持久化往返、并发写不损坏；
- 窗口矩形换算：负坐标多屏裁剪、DWM 边界回退；
- 集成：白名单外 0 次请求；白名单内静止 0 次；连续变化被冷却拦截；达上限后静默；鼠标穿透默认放行、关选项后跳过；隐藏时 watcher 停止。
- 全量回归保持绿色（当前基线 130 passed / 4 skipped）。


---

## B. 实施手册与历史修订

# 主动识屏 + 多 Agent 联动 — 实施手册（Kimi K3 终审版）

> 文档状态：**方案已确认，代码未动工**。待用户下“动手指令”后按本手册分阶段实施。
> 适用范围：`D:\dsh-pet-pr`（dsh-pet-indesktop 独立桌宠，PySide6）。
> 复审对象：本手册的规格是否自洽、可实施、不违背“低功耗 + 多开 + 不影响使用”三条铁律。

---

## 0. 基线快照（复审前必读）

- 当前分支：`perf/startup-and-hidden-cpu`（基于 origin/main v3.1.1，共 5 个提交）。
- 测试基线：`QT_QPA_PLATFORM=offscreen .\.venv\Scripts\python.exe -m pytest -q` → **130 passed / 4 skipped**。
- 已合并的既往优化（复审时不得回退）：
  1. 窗口隐藏暂停解码/定时器；全屏自动隐藏期间 `_fullscreen_timer` 保持存活；
  2. 素材懒加载 + 分级预热（高优先级 idle/turn/click/drag/move 立即、随机池延迟 2s）；
  3. 多开 `--instance` 配置隔离 + 启动避让；
  4. WebM 元数据跨进程缓存、余额跨实例缓存、日志按 PID 隔离、vision/PIL 懒导入；
  5. 角色显示名、右键全屏自动隐藏开关、no_mirror 补充。
- 本手册新增内容当前**一个文件都未创建**：`pet/proactive.py`、相关测试、配置字段均未写入。

---

## 1. 最终确认的决策清单（不可再改，除非用户重新确认）

| # | 决策 | 定论 |
|---|---|---|
| D1 | 默认灵敏度 | **平衡档**：停留 45s / 冷却 5min / 每日 15 次；**闲置判定默认关闭**（2026-08-27 用户终审改定：默认 0，可开关可自定义） |
| D2 | 用户自主权 | 提供 **安静 / 平衡 / 活跃 / 自定义** 四档；所有参数在设置页可独立调整 |
| D3 | “立即试看一次”按钮 | **不做**。用户即时验证复用现有右键「看看屏幕」 |
| D4 | 主动识屏输出 | 仅气泡展示，**默认不写入 AI 会话**；与自言自语同一心智模型 |
| D5 | 感知性设计 | 开启时一次性告知气泡；触发前有先兆（小动作或“让我看看…”短气泡）；无新按钮 |
| D6 | 鼠标穿透 | 穿透时**默认仍允许识屏**；右键二级菜单提供独立开关 `[x] 鼠标穿透时仍允许主动识屏` |
| D8 | 截屏范围 | 仅截**前台白名单窗口区域**；不截全屏；主动识屏截图**不落盘**（纯内存） |
| D9 | 平台边界 | 主动识屏 v1 **仅 Windows**（GetForegroundWindow/GetLastInputInfo/DWM 均为 Win32）；非 Windows 不显示入口 |
| D10 | 低功耗铁律 | 功能关闭 = 零定时器/零截图/零网络/零新增进程；开启后仅 8s 一次微秒级 Win32 判定；隐藏时随现有 `_pause_activity` 暂停 |

---

## 2. 主动识屏 — 触发漏斗（最终规格）

```
QTimer 8s（仅 enabled==True 且窗口可见时存在）
 ├─ G1 桌宠守卫：isVisible()；若 mouse_through==True，须 allow_when_mouse_through==True 才继续
 ├─ G2 白名单：前台进程名/标题 fnmatch 匹配（不区分大小写）
 ├─ G3 停留：同一前台窗口连续停留 ≥ dwell_seconds
 ├─ G4 闲置（**默认关闭**）：require_idle==True 时才判定——GetLastInputInfo 空闲 ≥ min_idle_seconds
 └─ G5 后台线程：抓窗口区域 → dHash
       ├─ 与上次同 app 快照 Hamming < change_threshold → 丢弃
       └─ 差异 ≥ change_threshold → G6 频控
            ├─ 距上次请求 ≥ min_request_interval_seconds（默认 60s，免费模型硬下限）
            ├─ 距上次触发 ≥ cooldown_minutes
            ├─ 今日次数 < daily_cap
            └─ 通过 → 先兆气泡 → JPEG(最长边 768, q70) → 视觉模型 → 关怀气泡
                 └─ 429/网络错误：复用 vision.py 现有重试（见 §6，非 2/4/8 指数退避）；
                    连续 3 次请求失败 → 当日熔断（仅日志，不打扰）
```

默认参数与合法范围：

| 参数 | 默认 | 范围 |
|---|---|---|
| dwell_seconds | 45 | 15 ~ 600 |
| min_idle_seconds | 30 | 0 ~ 3600（仅 `require_idle=true` 时生效；require_idle 默认 false = 不要求闲置） |
| cooldown_minutes | 5 | 0.5 ~ 120（支持 0.5 分钟粒度，2026-08-27 用户反馈后放宽） |
| daily_cap | 15 | 1 ~ 9999（2026-08-27 用户反馈后取消 100 硬顶） |
| min_request_interval_seconds | 60 | 30–3600 |
| change_threshold | 8 | 0–32（Hamming 距离 0–64） |

---

## 3. 主动识屏 — 配置规格（config.json 新增）

```jsonc
"proactive_screen": {
  "enabled": false,                      // 默认关闭
  "preset": "balanced",                  // quiet / balanced / active / custom
  "allow_when_mouse_through": true,      // D6
  "whitelist": [],                       // ["code.exe", "cursor.exe", "*bilibili*", "title:*会议*"]
  "dwell_seconds": 45,
  "require_idle": false,                   // 默认关闭：工作/敲键盘时也允许触发；true 时才启用下方闲置判定
  "min_idle_seconds": 30,                  // 仅 require_idle=true 时生效
  "cooldown_minutes": 5,
  "daily_cap": 15,
  "min_request_interval_seconds": 60,
  "change_threshold": 8,
  "prefer_free_provider": true,          // true=视觉配置为 GLM 时优先免费；否则用聊天 provider
  "pre_cue": true                        // 触发前先兆提示
}
```

预设映射（选 preset 即覆盖三个频率参数；custom 表示全部来自用户手填。
**idle 不参与预设**——`require_idle` / `min_idle_seconds` 是独立开关，任何预设下默认都是关闭的）：

| preset | dwell | cooldown | cap |
|---|---|---|---|
| quiet | 90 | 10 | 8 |
| balanced | 45 | 5 | 15 |
| active | 20 | 3 | 25 |
| custom | 使用用户自填值 | 同左 | 同左 |

实现要求：
- 配置读取必须经 `pet/config.py` 的白名单机制登记，且与默认 dict 深合并（保留新增默认键）；
- 运行时使用 `effective_proactive_config(raw)` 得到最终值；所有数值 `clamp` 到合法范围；非法 preset 回退 `balanced`；
- `require_idle=false` 时 effective 配置中 `min_idle_seconds` 视为 0（G4 直接通过），字段原值保留不动。

---

## 4. Phase 1 规格：`pet/proactive.py` 纯函数（零 UI、零网络、零 Win32）

复审重点：**所有函数必须无副作用、可注入时钟/随机源、100% 单测**。

### 4.1 白名单匹配
```python
match_process_whitelist(rules: list[str], process_name: str, window_title: str) -> bool
```
语义：
- `rules` 为空 → False（白名单为空 = 永不触发）；
- 每条规则大小写不敏感；支持 `*` `?` 通配（fnmatch 语义）；
- 无前缀规则**仅匹配进程名**（gpt-5.6-sol 终审修订：标题可能含敏感信息，标题匹配必须显式 title:）；
- `title:` 前缀规则仅匹配窗口标题；
- 任一规则命中即 True。
边界：进程名/标题可为空串；规则可为空串（忽略）；UWP 前台进程为 `ApplicationFrameHost.exe` 时靠 `title:` 规则命中。

### 4.2 画面变化检测
```python
image_dhash(img) -> int          # 9×8 灰度 → 64bit dHash
hamming_distance(h1: int, h2: int) -> int   # 0~64
```
语义：缩至 9×8 灰度，逐行相邻像素比较得 64 位；相同图距离 0；`change_threshold` 比较用。
边界：非 3 通道/任意尺寸输入均可处理（先 convert("L")）。

### 4.3 频控 RateLimiter
```python
class ProactiveLimiter:
    def __init__(self, state_path, cfg, *, clock=time.time, today=...)  # 可注入
    def allow(self) -> tuple[bool, str]   # (是否放行, 拒绝原因)
    def record_success(self) -> None
    def record_failure(self) -> bool      # 返回是否触发当日熔断
```
状态文件：`<config.dir>/proactive_screen_state.json`，字段：
```jsonc
{ "date": "2026-08-26", "count": 0, "last_trigger": 0.0,
  "last_request": 0.0, "consecutive_failures": 0, "paused_until_date": "" }
```
规则（顺序判定）：
1. 跨天 → 重置 count 与熔断状态；
2. `paused_until_date == today` → 拒绝（当日熔断）；
3. `count >= daily_cap` → 拒绝；
4. `now - last_request < min_request_interval_seconds` → 拒绝；
5. `now - last_trigger < cooldown_minutes*60` → 拒绝；
6. 否则放行。
`record_failure`：连续失败 ≥3 → `paused_until_date=today`。
持久化：`.tmp` + 原子替换；文件损坏 → 回退全新状态，不崩溃。
边界：时钟可注入（单测）；日期函数可注入；原子替换保证并发写最多丢一次计数，绝不写坏文件。
多实例语义（已确认）：`config.dir` 在所有实例间共享（只有 config 文件名按 instance 区分），
故状态文件**不按 instance 拆分**——`daily_cap` / 最小间隔 / 冷却是**跨实例全局**的，
多开不会放大每日请求总量；这与 §9 的「文件级共享缓存模式」一致（同余额跨实例缓存）。

### 4.4 其余纯函数
```python
dwell_satisfied(entered_ts, now, dwell_seconds) -> bool
idle_satisfied(last_input_seconds, min_idle_seconds) -> bool
should_watch(visible, interacting, mouse_through, allow_when_mouse_through) -> bool
effective_proactive_config(raw: dict) -> dict   # 默认+P预设+用户值，clamp
```
`should_watch` 语义：`visible=False` 或 `interacting=True` → False；`mouse_through=True` 时取决于 `allow_when_mouse_through`。

---

## 5. Phase 2 规格：Win32 采集与 Watcher（先日志后实机）

### 5.1 `pet/vision.py` 扩展（保持 PIL 懒导入现状）
```python
foreground_window_info()  # 【新增函数】→ {hwnd, pid, process, title, rect(x,y,w,h)} | None
get_foreground_window_rect() -> tuple[int,int,int,int] | None   # 可由上者派生
get_system_idle_seconds() -> float   # GetLastInputInfo；非 Windows 恒返回 0.0
capture_window_rect(rect) -> PIL.Image | None   # 后台线程内调用
```
**命名勘误（终审修订）**：现有函数是 `foreground_app_info() -> str`（vision.py:54，
返回 `"进程名 | 标题"` 字符串，window.py:1099 在用），**不是** `foreground_window_info()`。
实施要求：`foreground_window_info()` 为新增；`foreground_app_info()` 改为基于前者的
薄封装（拼字符串），签名与返回格式保持不变——`看看屏幕` 现有行为不允许变。
硬性要求：
- 可见边界优先 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`，失败回退 `GetWindowRect`；
- 多屏负坐标：用 `GetSystemMetrics(SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN)` 得到虚屏原点，`ImageGrab(all_screens=True)` 抓取后按原点差裁剪；**不得**依赖 PIL bbox 负坐标行为；
- `capture_window_rect` 只在 worker 线程执行；主线程绝不允许出现 `ImageGrab`；
- 目标窗口不可见/最小化/被 cloaked（`DWMWA_CLOAKED`）→ 返回 None，本轮静默跳过。

### 5.2 `ProactiveScreenWatcher`（`pet/proactive.py` 内实现）
- 单一 `QTimer`，间隔 8s，仅 `enabled==True` **且白名单非空**时 `start()`；
  `setEnabled(False)` 或白名单被清空即 `stop()`（与验收 #3「白名单为空 = 无定时器」对齐）；
- 挂到 `PetWindow`，`_pause_activity()` 时停止、`_resume_activity()` 时按配置重启（与现有隐藏暂停一致）；
- **生命周期（终审修订）**：watcher 必须是 `PetWindow` 的子对象（parent=self），在 `PetWindow.__init__`
  中创建；**不得做成 app 级单例**。角色热切换会新建 `PetWindow` 并延迟销毁旧窗口
  （app.py:475 `switch_character`），父子关系保证旧 watcher 随旧窗口销毁、
  新 watcher 随新窗口按配置重建——否则切角色后会出现两个 watcher 双倍截图/请求；
- 主线程只做 G1~G4（微秒级）；G5 起用 `threading.Thread(daemon=True)` + `Signal` 回主线程；
- 结果只走 `show_bubble`；不写会话、不弹窗、不抢焦点；
- **日志模式**：先用 `logging.info` 输出“本次会触发”而不调模型，实机验证 1~2 天再进入 Phase 3。

---

## 6. Phase 3 规格：视觉链路与防护

- **不落盘重构（终审修订，必做）**：现有 `ask_about_screen(image_path, ...)` 第一个参数是
  文件路径（vision.py:128 `Path(image_path).read_bytes()`），与 D8「主动识屏截图不落盘」直接冲突。
  实施方式：把请求构造 + 发送抽成内部函数（如 `_post_vision_request(jpeg_bytes, app_info, system_prompt, p)`），
  `ask_about_screen` 变为「读文件 → 调内部函数」的薄封装；主动识屏在内存里编码 JPEG 后直接传 bytes。
  任何实现都不得为主动识屏写临时文件。
- 重试现状（终审勘误）：现有代码**不是** 2/4/8s 指数退避——vision.py:170-186 为至多 3 次尝试，
  429 时固定 `sleep(2.0)` 且至多重试 2 次，网络错误固定 `sleep(1.0)`。v1 **保持该行为不变**
  （避免改动既有「看看屏幕」）；若日后要指数退避，单独提案、单独评审。
- Provider 选择：
  - `prefer_free_provider=True` **且 `vision_same_as_chat=False`**（终审补充前置条件：
    同聊天模型时 vision_base_url 被忽略，见 vision.py:126），且视觉配置
    （`vision_base_url` 或 `vision_model`）指向智谱 `glm-4.6v-flash` → 用视觉配置；
  - 否则用聊天 provider 的视觉推导（现有 `resolve_vision_model` 逻辑不变）；
- DeepSeek 兜底时按现有 `vision.py` 逻辑（`thinking disabled`、`max_tokens≥4096` 等）不变；
- 熔断：`ProactiveLimiter.record_failure()` 连续 3 次 → 当日停用；恢复条件 = 次日；用户手动关再开**不**清除熔断（防刷）；
- **dry-run 模拟模式（终审补充，Phase 3 必做）**：Phase 2 日志模式只 logging、不更新 limiter 状态，导致日志触发频率高于真实模式。
  要求在 `ProactiveLimiter` 中支持 `dry_run` 标志或独立 dry-run 状态文件（如 `proactive_screen_dryrun_state.json`）：
  - `dry_run=true` 时仍走 `allow()` 的冷却/最小请求间隔/每日上限判定，但**不增加真实 `count`、不写入真实 `last_trigger`**；
  - 为让节流贴近真实，dry_run 至少应维护独立的 `last_request`（使 60s 最小间隔生效），必要时也为 dry_run 维护独立 `count`/`last_trigger`；
  - `dry_run=true` 仅用于 Phase 2 日志/验证期，绝不允许消耗用户当日真实额度；
  - 当进入真实视觉请求（Phase 3）时，才按现有语义调用 `record_attempt()` / `record_success()` / `record_failure()` 更新真实状态；
- 费用/限流已核实：
  - DeepSeek 每图 ≤384 token 输入（官方换算），单次最坏约 ¥0.003，15 次/天 ≈ ¥0.05 封顶；
  - GLM-4.6V-Flash 免费；官方未公开 RPM/TPM，故 60s 最小间隔 + 每日上限 + 退避 + 熔断四重防护。

---

## 7. Phase 4 规格：设置 UI 与右键二级菜单

### 7.1 设置页（`pet/settings_dialog.py` 新增分组「主动识屏」）
- 总开关；
- 预设下拉：安静 / 平衡 / 活跃 / 自定义；选自定义才展开三个频率参数；
- 「仅当我闲置时触发」勾选 + 闲置秒数输入（勾选才启用输入；与右键二级菜单同一开关，双向同步）；
- 白名单多行编辑 + 「从当前前台窗口添加」按钮（延迟 3 秒采样前台窗口后，弹窗让用户明示选择「按软件（进程名）」或「按标题关键词（title:）」——不两行都塞）；
- 每日上限、鼠标穿透允许、先兆提示、免费优先 复选框；
- 保存即生效（复用现有 `settings_saved → refresh_pet_settings` 路径）。

### 7.2 右键二级菜单（`pet/window.py contextMenuEvent`）
```
主动识屏 ▸  [x] 开启主动识屏
           [x] 鼠标穿透时仍允许主动识屏
           [x] 触发前先兆提示
           [ ] 仅当我闲置时触发（默认不勾；秒数在设置页调）
           打开设置…
```
- 仅 Windows 显示「主动识屏」组；菜单状态与配置实时同步；
- Agent 联动每项默认不勾；勾选即启动对应监视器，取消即停（并可选卸载注入的 hooks）。

---

## 8. 多 Agent 联动规格（默认全关、逐项独立）

### 8.1 统一事件协议
事件文件：`<config.dir>/agent-events/<agent>.jsonl`，每行：
```jsonc
{"ts": 1750000000.123, "agent": "claude", "session_id": "x", "event": "PreToolUse", "state": "working"}
```
状态词汇（供桌宠切动作）：`idle / thinking / working / attention / sleeping / error`。

### 8.2 各 Agent 事件源（已调研验证）
| Agent | 事件源 | 实现 |
|---|---|---|
| DSH | **内置桥接插件**（`integrations/dsh-pet-bridge`）：订阅 `agent/created`、`agent/status`、`agent/request-error`、`cordis/request-run(-resolved)` 与 `session/event`（turn/tool/审批/问题/命令），按实例写入固定桥目录 `<数据基目录>/dsh-pet-bridge/dsh-{pid}.jsonl`（消费端 glob `dsh*.jsonl`，兼容旧版单文件 `dsh.jsonl`）；勾选时弹确认框一键安装（**node 直调 pnpm add + 自维护 profile 的 `dsh.profile.bundles` 层**——不走 `dsh plugin`，规避其在 Windows 上拆碎含空格路径的缺陷），关闭自动卸载（幂等）。已实测：插件需声明 `dsh.bundle` + `cordis.patch.yml` 才会成为 profile layer |
| Claude Code | 官方 hooks（PreToolUse/PostToolUse/PostToolUseFailure/Stop/SessionStart/UserPromptSubmit） | 勾选开启时弹确认框，经用户同意后把 hook 命令写入 `.claude/settings.json`，命令追加一行到事件文件 |
| Cursor | `~/.cursor/projects/**/agent-transcripts/*.jsonl`（**真实格式**：`{role, message:{content:[...]}}`——user→thinking、assistant+tool_use→working、assistant 纯文本→idle） | byte-offset tail，1.5s，仅最近 1 天目录，文件数上限 50 |
| OpenCode | **原生 SQLite 直读**（`~/.local/share/opencode/opencode.db` 的 event 表，rowid 偏移轮询，只读模式不阻塞写入）——**无需插件** | 1.5s 轮询，backfill 防护 |

硬性约束：
- **绝不用 mtime 轮询**（已被证伪的方案）；
- 每个监视器独立 QTimer，关闭即停；桌宠隐藏时暂停；
- tail 状态（offset 等）持久化于状态文件；文件轮转/删除时安全降级，不崩溃、不回放旧行；
- 状态 → 动作映射：`thinking→写代码/深度思考`，`working→敲击桌面`，`attention→气泡“需要你看一眼”`，`error→气泡`，`sleeping→待机`。

---

## 9. 性能与功耗预算（Kimi K3 逐项复核）

| 项 | 预算 |
|---|---|
| 功能关闭 | 0 定时器 / 0 截图 / 0 网络 / 0 新进程 |
| 8s 心跳（仅开启时） | 每 tick ≤ 5 次 Win32 调用，微秒级 |
| 截图频率上限 | 白名单+停留全中时 1 次/8s（后台线程；闲置为可选项，require_idle=true 时才额外要求）；实际由 dHash 丢弃大多数 |
| 视觉请求上限 | 冷却 + 每日上限 + 60s 最小间隔 + 熔断 |
| Agent 监视器（单开时） | 1.5s 一次有界 tail，仅 1~2 个目录，隐藏时暂停 |
| 多开 | 复用现有 `--instance` 配置隔离与文件级共享缓存模式 |

---

## 10. 验收标准（全部满足才算完成）

1. 全量测试 **0 失败**（允许 offscreen 既有 4 skip），目标 130 → **140+**；
2. Phase 1 纯函数 100% 覆盖（白名单/dHash/限流/预设合并/守卫）；
3. 白名单为空或功能关闭：实测无任何定时器与请求；
4. 白名单外/静止画面/冷却期/超每日上限：0 次模型请求；
5. 鼠标穿透开启时默认仍识屏；关闭「穿透时仍允许」后立即跳过；
6. 隐藏/全屏自动隐藏/角色切换：watcher 停止且恢复正确；
7. 主线程无 ImageGrab；拖动/动画期间无卡顿；
8. 429 与连续失败熔断可复现；费用估算与文档一致；
9. 多 Agent 默认全关；单项开启后事件 1~2s 内反映到动作/气泡；关闭即停；
10. 日志模式先过 1~2 天实机，再放开模型调用。

---

## 11. 明确不做清单（复审时视为红线）

- ❌ 不新增「立即试看一次」按钮（复用「看看屏幕」）；
- ❌ 不用 mtime 轮询检测 Agent 状态；
- ❌ 不在主线程截图；不截全屏（主动识屏只截白名单窗口）；
- ❌ 主动识屏截图不落盘、不写入 AI 会话；
- ❌ 任何新功能不默认开启；多 Agent 不默认安装/修改任何 hooks；
- ❌ 不引入 psutil 等新第三方依赖（Agent 探测用现有标准库/Win32）；
- ❌ 不影响既有 130 项测试语义；不改变桌宠动画/交互/低功耗既有行为。

---

## 12. 终审修订记录（2026-08-27，Kimi K3 对照实码复审）

复审方式：逐条对照 `D:\dsh-pet-pr` 现有代码（vision.py / config.py / window.py / app.py），
并重跑测试基线（130 passed / 4 skipped，与 §0 一致）。修订如下：

1. **§2/§6 429 退避勘误**：原文「已有 429 重试（2/4/8s ×3）」与实码不符——
   vision.py:170-186 实为至多 3 次尝试、429 固定 2s、网络错误固定 1s。v1 保持现状，已改述。
2. **§6 新增必做重构项**：`ask_about_screen` 只收文件路径，与 D8「截图不落盘」冲突；
   必须抽 bytes 级内部函数，禁止为主动识屏写临时文件。
3. **§5.1 命名勘误**：现有函数是 `foreground_app_info() -> str`（window.py:1099 在用），
   `foreground_window_info()` 是新增而非「扩展返回值」；前者改薄封装，行为不变。
4. **§5.2 与验收 #3 矛盾修复**： watcher 启动条件补上「白名单非空」，否则验收 #3
   「白名单为空无定时器」无法成立。
5. **§5.2 生命周期红线**：角色热切换会新建 `PetWindow`（app.py:475），watcher 必须是
   窗口子对象而非 app 单例，否则切角色后双倍触发。
6. **§4.3 多实例语义明确**：状态文件跨实例共享，`daily_cap` 为全局每日上限。
7. **§6 GLM 前置条件补充**：`prefer_free_provider` 生效需 `vision_same_as_chat=False`，
   否则 vision_base_url 被忽略（vision.py:126）。
9. **闲置判定改为默认关闭（2026-08-27 用户定）**：新增独立开关 `require_idle`（默认 false，
   工作中也允许触发），`min_idle_seconds` 仅在其开启时生效；idle 从预设表中移出（预设只管
   dwell/cooldown/cap 三项）；右键「主动识屏」二级菜单新增 `[ ] 仅当我闲置时触发`，
   秒数在设置页自定义。同步修订 D1、§2 漏斗与参数表、§3、§7.1、§7.2。

结论：**方案整体自洽、可实施，修订后可交付执行**。Phase 1~2 风险低（纯函数 + 日志模式），
主要实施风险集中在 §6 的 ask_about_screen 重构（触碰既有「看看屏幕」路径，需回归测试覆盖）。
