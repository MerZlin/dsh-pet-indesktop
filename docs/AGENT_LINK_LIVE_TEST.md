# Agent 联动实机测试说明（交给 Kimi Code）

> 用途：让 Kimi Code 在用户本机（Windows）直接运行桌宠，做真实端到端验证。
> 前提：工作区 `D:\dsh-pet-pr`，当前分支 `perf/startup-and-hidden-cpu`。
> **不要 git commit / push。**

---

## 0. 当前状态

- 测试基线：`QT_QPA_PLATFORM=offscreen .\.venv\Scripts\python.exe -m pytest -q` → **185 passed / 4 skipped**
- 语法编译：`python -m compileall -q pet tests` → 通过
- 工作区改动**均未提交**，包含：
  - 主动识屏（Phase 1~5 首项）
  - 多 Agent 联动监视器（DSH / Claude Code / Cursor / Codex / OpenCode）
  - 设置 UI 与右键菜单

---

## 1. 整体做了什么

### 1.1 主动识屏
桌宠按白名单主动截取前台窗口区域、调用视觉模型、气泡关怀；拥有短期陪伴记忆（能记住你上次在干嘛，切换活动时自然吐槽）。

### 1.2 多 Agent 联动
右键「Agent 联动」二级菜单可开启 5 个 Agent 的监听；监听对应 Agent 的本地状态，驱动桌宠动作与气泡：
- `thinking` → 切「写代码」或「深度思考碎碎念」
- `working` → 切「原地敲击桌面互动」（或「轻快记录」）
- `attention` → 气泡「主人，Agent 这边需要你看一眼～」
- `error` → 气泡「Agent 执行好像遇到报错了…」
- `idle/sleeping` → 回待机

---

## 2. 新增/改动文件清单

| 文件 | 说明 |
|---|---|
| `pet/agent_link.py`（新增） | 多 Agent 监视器核心：`ByteOffsetTailer`、`BaseAgentMonitor`、`DshMonitor`、`ClaudeCodeMonitor`、`CursorMonitor`、`CodexMonitor`、`OpenCodeMonitor`、`AgentLinkManager` |
| `pet/config.py` | 新增 `agent_link` 默认块（dsh/claude/cursor/codex/opencode 全 False） |
| `pet/window.py` | 实例化 `AgentLinkManager`；右键菜单 5 项开关；隐藏/恢复时 `pause()/resume()` |
| `pet/settings_dialog.py` | 主动识屏设置组（含「清除陪伴记忆」按钮） |
| `pet/vision.py` | 视觉链路 bytes 级重构 + 前台窗口信息 + 短期记忆注入 |
| `pet/proactive.py` | 主动识屏 Watcher + 频控 + dry_run + 短期记忆 |
| `tests/test_agent_link.py`（新增） | 10 个 Agent 联动测试 |
| `tests/test_proactive.py` | 主动识屏全链路测试 |

---

## 3. 各 Agent 实现方式

| Agent | 机制 | 完成度 |
|---|---|---|
| **DSH** | 统一 JSONL Byte-Offset Tail，事件来自 DSH 插件写入 `agent-events/dsh.jsonl` | ⚠️ 监视器+协议完成，**DSH 插件包未实现**（`# TODO DSH plugin verification`） |
| **Claude Code** | 官方 hooks（PreToolUse/PostToolUse/Stop/SessionStart/UserPromptSubmit）写入 `~/.claude/settings.json`，事件追加到 `agent-events/claude.jsonl` | ⚠️ 已实现，但**hooks 配置格式可能有误，需实测确认** |
| **Cursor** | 多文件 Byte-Offset Tail：`%USERPROFILE%\.cursor\projects\**\agent-transcripts\*.jsonl`，上限 50 文件，最近 1 天 | ✅ 已实现，需真机验证 |
| **Codex** | 会话 Byte-Offset Tail：`%USERPROFILE%\.codex\sessions\**\*.jsonl`，上限 30 文件 | ✅ 已实现，需真机验证 |
| **OpenCode** | 统一 JSONL Tail，事件来自 opencode 插件写入 `agent-events/opencode.jsonl` | ⚠️ 监视器+协议完成，**opencode 插件包未实现**（`# TODO opencode plugin package`） |

统一状态词汇：`idle / thinking / working / attention / sleeping / error`
统一事件文件：`<config.dir>\agent-events\<agent>.jsonl`

---

## 4. 已知风险 / 需实测确认的点（重点）

1. **Claude Code hooks 配置格式**：`install_hooks()` 目前把 hook 写成**字符串**，但官方规范要求**数组对象**。实测如果 hooks 不触发，大概率是这个原因。
2. **打包版 hooks 命令**：`install_hooks()` 用的是 `sys.executable`，在 PyInstaller 打包版里会指向桌宠 exe，可能导致 `-c` 命令无效。**源码运行没问题，打包版需注意。**
3. **授权拒绝后菜单不回弹**：开启 Claude Code 时弹确认框，如果用户点“否”，菜单 checkbox 仍会保持在勾选态。实测时注意。
4. **状态变更无去抖**：Cursor/Codex 密集写日志时，桌宠可能被连续切动作（看起来抽搐）。实测观察是否需要加节流。
5. **半行截断**：`ByteOffsetTailer` 没有“半行缓冲”，如果某行恰好跨过读取块边界，可能丢事件。典型场景暂未触发，但可作为后续加固。
6. **DSH / OpenCode 插件包未实现**：要看到“DSH 在跑 → 桌宠切动作”，**现在做不到**，因为没有插件把事件写进对应 jsonl。只能先通过手动向 `agent-events/dsh.jsonl` / `agent-events/opencode.jsonl` 追加事件行来验证监视器链路。

---

## 5. 实机测试步骤（Windows）

### 0) 启动源码版桌宠
```powershell
cd D:\dsh-pet-pr
python -m pet
```

### 1) 先验证右键菜单不崩
- 右键桌宠 → 应看到「主动识屏」和「Agent 联动」两个二级菜单；
- 「Agent 联动」应显示 5 项：DSH / Claude Code / Cursor / Codex / OpenCode，默认全不勾。

### 2) 验证 Claude Code 联动
1. 右键 → Agent 联动 → 勾选 **Claude Code**；
2. 应弹出确认框：是否允许注入 `~/.claude/settings.json` hooks；
3. 同意后查看 `%USERPROFILE%\.claude\settings.json`，确认 hooks 字段格式是否为数组对象（若为字符串则记录问题）；
4. 在终端启动 `claude` 并让它做一件事；
5. 观察桌宠是否切换“写代码/敲击桌面/气泡提醒”；
6. 记录：hooks 是否真实触发？动作是否切换？

### 3) 验证 Cursor 联动
1. 勾选 **Cursor**；
2. 打开 Cursor 并跑一个 Agent；
3. 观察桌宠动作；
4. 若 cursor 目录不存在或没有 transcript，可手动在
   `%USERPROFILE%\.cursor\projects\test\agent-transcripts\test.jsonl`
   追加一行 `{"type":"PreToolUse"}`，应在 1.5s 内看到桌宠切“敲击桌面”。

### 4) 验证 Codex 联动
1. 勾选 **Codex**；
2. 启动 codex 会话（`codex` 或 `codex --full-auto`）让它做事；
3. 观察桌宠动作；
4. 记录：是否过度触发（每条工具行都切动作）？是否需要节流。

### 5) 验证 DSH / OpenCode 手动注入（插件未装，先验证链路）
1. 勾选 **DSH**；
2. 手动向 `<APPDATA>\dsh-pet-standalone\agent-events\dsh.jsonl` 追加：
   ```json
   {"ts": 1750000000.1, "agent": "dsh", "session_id": "x", "event": "UserPromptSubmit", "state": "thinking"}
   ```
3. 观察桌宠是否在 1.5s 内切“写代码”；
4. OpenCode 同理追加到 `agent-events/opencode.jsonl`。

### 6) 验证隐藏暂停
- 勾选任一 Agent 后，右键「隐藏桌宠」→ 观察监视器是否暂停（可用日志或再次显示后是否立即恢复）；
- 显示后应恢复。

### 7) 验证菜单授权拒绝回弹
- 勾选 Claude Code，在确认框点“否”；
- 观察菜单项是否仍显示勾选（预期：若仍勾选，就是 bug）。

---

## 6. 验收判断

| 项 | 通过标准 |
|---|---|
| 右键菜单 | 无崩溃，5 项齐全 |
| Claude Code | hooks 写入真实有效；Agent 跑动时桌宠切动作/气泡 |
| Cursor | 真实 transcript 触发动作；无过度抽搐 |
| Codex | 真实 rollout 触发动作；无过度抽搐 |
| DSH / OpenCode | 手动注入事件行能驱动动作；插件包仍未实现，不影响本链路验证 |
| 隐藏暂停 | 隐藏后无 IO/动作；显示后恢复 |
| 授权拒绝 | checkbox 应回弹到未勾选 |

---

## 7. 汇报格式

请按以下格式输出：
```
## 实机测试结论
- 右键菜单：通过 / 异常
- Claude Code：通过 / 异常（附 settings.json 关键片段）
- Cursor：通过 / 异常
- Codex：通过 / 异常
- DSH / OpenCode 手动注入：通过 / 异常
- 隐藏暂停：通过 / 异常
- 授权拒绝回弹：通过 / 异常
- 疑似 bug 清单：无 / 具体问题
- 建议：可进入下一步 / 需先修复 xxx
```
