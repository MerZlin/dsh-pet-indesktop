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
| 12 | Codex / Cursor 日志 tail | ✅ Clawd 已验证：JSONL byte-offset tail（非 mtime），有 replay/backfill 防护 | [Clawd codex-log-monitor.js](https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/agents/codex-log-monitor.js) |
| 13 | mtime 轮询不可行 | ✅ 已确认是历史方案失败根因（批量刷盘、无法区分状态）；永久弃用 | 同上 |

---

## 二、相对此前方案的 4 处修正（重要）

1. **鼠标穿透不再阻断主动识屏**：`mouse_through=True` 时默认仍允许识屏（读屏只读画面、不交互，没有冲突）；但提供「鼠标穿透时仍主动识屏」选项，放右键二级菜单 + 设置页，默认开启，用户可关。
2. **多 Agent 感知默认全部关闭、逐 Agent 可选**：不默认扫描、不默认写 hooks；右键二级菜单「Agent 联动」内对 `DSH / Claude Code / Cursor / Codex` 分别勾选。
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

右键二级菜单「Agent 联动」：`[ ] DSH  [ ] Claude Code  [ ] Cursor  [ ] Codex`，默认全不勾选；每项开启才启动对应监视器，关闭即停（卸载 hooks 可选）。

| Agent | 事件源（已验证） | 实现 |
|---|---|---|
| DSH | DSH 插件事件（`session/event` 等；Clawd eventMap：SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/Stop…） | 提供轻量 DSH 插件包，把事件写入 `agent-events/dsh.jsonl`；桌宠 tail |
| Claude Code | 官方 hooks（PreToolUse/PostToolUse/Stop/SubagentStop/…）执行本地脚本追加事件 | 开启时经用户确认把 hook 写入 `.claude/settings.json`；桌宠 tail `agent-events/claude.jsonl` |
| Cursor | `~/.cursor/projects/**/agent-transcripts/*.jsonl` | byte-offset tail（1.5s，仅最近目录，有界） |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 同 Clawd：byte-offset tail + backfill 防护 |

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
