# 多 Agent 联动统一事件协议与扩展指南

> 文档用途：说明桌宠「Agent 联动」的底层事件协议，以及第三方如何接入任意 AI Agent（不改桌宠代码，或新增一个内置 Agent 各需要做什么）。
> 读者：想把自家 Agent / CLI 工具接入桌宠联动的开发者与集成方。

---

## 1. 架构总览：本地文件事件总线（不访问外网）

```
Agent 侧（写方）                          桌宠侧（读方）
┌──────────────────────────┐            ┌────────────────────────────────────┐
│ 插件 / hooks / 直读适配    │   追加写    │ ByteOffsetTailer 字节偏移增量 tail   │
│ DSH 桥接插件 (Node)       │  ────────▶ │ （1.5s 轮询线程，不回放历史）        │
│ Claude Code hooks 脚本    │  JSONL 文件 │   ↓ normalize_event_state 归一      │
│ Cursor/OpenCode 直读适配  │            │   ↓ 六态词汇                        │
└──────────────────────────┘            │ AgentLinkManager 状态机             │
                                        │   → 动画切换 / 气泡 / 音效           │
                                        └────────────────────────────────────┘

DSH 通道只有**一个读方**：`DshMonitor`（一个 `DirGlobTailer` + 一个轮询线程）。
每条桥接记录解析一次后分发给两个消费者：信号分派（气泡/动画/音效管线）与
`pet/dsh_state.py` 的纯收敛器（8 态收敛 + 审批/问题锁存），DSH 端口探活
（offline 判定的唯一来源）也并入同一线程（3s 一节拍，候选端口 3080/38080/
19387 与 `$DSH_PORT`）。
```

- **通信方式**：事件通道是纯本地文件追加写 + 增量读，全程不访问外网（联动唯一的网络形态是本机回环端口探活，用于判定 DSH 是否在线）。2026-10 减法删除了旧的 mux 订阅与 `/api/respond`；2026-10-30 起新增可选的 `dsh-pet-channel`（本地回环 HTTP+SSE，用户显式安装才启用）承担 DSH 对话双向通道，事件 JSONL 仍保持只读。
- **读方保证**：byte-offset tail 不回放历史事件；文件不存在时静默空转；半行缓冲不丢事件；单次读取有界（64KB）。
- **低功耗**：联动默认全关；桌宠隐藏时监视器全线 pause，显示时 resume。

## 2. 统一事件协议（写方契约）

### 2.1 事件文件位置

走统一通道的 Agent，事件文件固定为：

```
<config.dir>/agent-events/<agent>.jsonl
```

`<config.dir>` 按平台（`<变体>` 为 onedir 变体后缀，源码运行无后缀）：

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\dsh-pet-standalone[-变体]\agent-events\` |
| macOS | `~/Library/Application Support/dsh-pet-standalone[-变体]/agent-events/` |
| Linux | `~/.config/dsh-pet-standalone[-变体]/agent-events/` |

### 2.1b 桌宠控制命令（可选，`pet/command`）

`dsh-pet-channel` 的 `pet_control` 工具或 `/pet say|play|notify` 会往
`dsh-pet-bridge/dsh-petcmd-<pid>.jsonl` 追加：

```json
{"event":"pet/command","cmd":"play_animation","name":"写代码","agent":"dsh","inst":"..."}
```

`cmd` 只允许 `say|play_animation|notify`；`text` ≤200 字、`name` ≤64 字且必须在
当前角色动画里存在；消费端 1 秒最多一条，隐藏态不播，未知动作静默丢弃。
命令文件同样以 `dsh*.jsonl` 命名，由 `ai.agent` 的 GlobTailer 读取。

### 2.2 JSON 行格式

文件每行一个 JSON 对象（JSON Lines，UTF-8 追加写）：

```jsonc
// 形态一：事件名（推荐，桌宠按内置映射归一为状态）
{"ts": 1750000000.0, "agent": "myagent", "event": "PreToolUse", "tool": "bash"}

// 形态二：显式状态（直接给六态词汇，优先级最高）
{"ts": 1750000000.0, "agent": "myagent", "state": "working"}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `ts` | 建议 | 事件时间戳（秒，浮点）。桌宠当前不校验，但写方应带，便于排查 |
| `agent` | 建议 | Agent 标识（与文件名一致即可）；桌宠以监视器的 agent_key 为准 |
| `event` | 二选一 | 事件名，见 §2.4 内置映射；不认识的会被忽略 |
| `state` | 二选一 | 显式状态，必须是 §2.3 六态词汇之一；优先于 `event` |
| `tool` | 可选 | 工具名（小写），用于「过程汇报」气泡（如 `bash` → 「正在跑命令」） |

### 2.3 六态状态词汇（读方统一词汇）

| 状态 | 含义 | 桌宠反应 |
|---|---|---|
| `thinking` | 思考中/刚接受指令 | 联动动作池轮换（写代码/吃Token…），可选「开始干活」气泡 |
| `working` | 执行中 | 同上 |
| `attention` | 需要用户注意（如回合结束待确认） | 重要气泡「需要你看一眼～」或并入完成确认 |
| `error` | 出错 | 重要气泡「好像遇到报错了…」+ error 音效 |
| `idle` | 空闲 | 恢复待机动画；busy→idle 触发「干完活啦」完成通知（默认开） |
| `sleeping` | 休眠 | 同 idle |

### 2.4 内置事件名 → 状态映射

写方若不想直接给 `state`，可使用以下事件名（与 Claude Code hooks 事件名兼容）：

| 事件名 | 状态 |
|---|---|
| `SessionStart` / `SessionEnd` | `idle` |
| `UserPromptSubmit` | `thinking` |
| `PreToolUse` / `PostToolUse` | `working` |
| `PostToolUseFailure` | `error` |
| `Stop` / `SubagentStop` | `attention` |
| `StopFailure` | `error` |
| `error` / `idle` / `thinking` | 同名状态 |

不在表内的事件名**一律忽略**（绝不默认当成 working，防止 transcript 类密集写入过度触发）。

### 2.5 写方注意事项（对接清单）

1. **追加写**（append），UTF-8 编码；PowerShell 写入方建议 `-Encoding UTF8`（读方已兼容首行 BOM）。
2. **去重**：连续相同状态不要重复落盘（状态切换瞬间可能抖出重复事件，重复行会占住桌宠端换帧节流位）。
3. **轮转**：事件文件超过约 1MB 时轮转（如 `dsh-{pid}.jsonl` → `dsh-{pid}.jsonl.1`，只留一代）；读方通过文件身份识别自动适配，无需特殊处理。
4. **绝不阻塞宿主**：写事件失败时静默放弃——联动是锦上添花，不能影响 Agent 本体（参考 `integrations/dsh-pet-bridge/index.js` 的做法）。
5. **隐私红线**：只写状态/事件元数据（状态、事件名、工具名），**不要**把代码内容、命令全文、文件内容、屏幕信息写进事件文件。

## 3. 内置四种接入模式（现状分析）

| Agent | 模式 | 事件来源 | 是否写外部配置 |
|---|---|---|---|
| DSH | 插件订阅 | 内置桥接插件 `integrations/dsh-pet-bridge/` 订阅 agent 生命周期事件，按实例写桥目录 `<base>/dsh-pet-bridge/dsh-{pid}.jsonl`（消费端 glob `dsh*.jsonl`，兼容旧版单文件 `dsh.jsonl`） | 安装/卸载 dsh 插件（弹窗同意） |
| Claude Code | hooks 注入 | 向 `~/.claude/settings.json` 注入官方 hooks，落地脚本把事件写 `agent-events/claude.jsonl` | 注入/卸载 hooks（弹窗同意） |
| Cursor | transcript 直读 | 直接 tail `~/.cursor/projects/**/agent-transcripts/*.jsonl`（官方转写文件，按 role/content 解析） | 否 |
| OpenCode | 数据库直读 | 只读 `~/.local/share/opencode/opencode.db` 的 `event` 表（rowid 增量） | 否 |

给新 Agent 选择接入模式的判断依据：

- **Agent 有插件/hook 机制** → 优先「hook 注入 + 统一协议写文件」（模式同 Claude Code），这是侵入最小、协议最稳的方式。
- **Agent 有现成的本地事件源**（transcript/日志/SQLite）→ 可以像 Cursor/OpenCode 那样写直读适配器（需要改桌宠代码，见 §5）。
- **Agent 什么都没有** → 只要它能执行任意命令（几乎都能），就可以让它周期性地往统一协议文件追加一行 JSON——配合 §4 的自定义通道，零代码接入。

## 4. 自定义 Agent 通道（不改桌宠代码）

桌宠支持在 `config.json` 的 `agent_link` 块里声明任意数量的自定义联动 Agent：桌宠对其事件文件做**只读监听**（统一协议），不写任何外部配置、无需授权弹窗。

```jsonc
// <config.dir>/config.json
{
  "agent_link": {
    "custom_agents": [
      {
        "key": "gemini",
        "name": "Gemini CLI",
        "path": "~/.gemini/pet-events.jsonl"
      }
    ]
  }
}
```

| 字段 | 约束 |
|---|---|
| `key` | `^[a-z0-9][a-z0-9_-]{0,31}$`，不得与内置键（`dsh`/`claude`/`cursor`/`opencode`）重复，全局唯一 |
| `name` | 联动气泡/菜单显示名；留空时用 key |
| `path` | 事件文件路径，支持 `~` 展开；文件不必预先存在（不存在时静默等待） |

配置后重启桌宠（或重新加载配置）即生效：右键菜单「Agent 联动」会出现该 Agent 的开关，行为与内置 Agent 一致（六态映射、开始/过程/完成气泡、音效全部可用）。最多 8 个自定义条目。

**接入示例**：让任意 Agent 在干活时执行（或由其 hook 执行）：

```bash
printf '{"ts": %s, "state": "working"}\n' "$(date +%s)" >> ~/.gemini/pet-events.jsonl   # 开工
printf '{"ts": %s, "event": "PreToolUse", "tool": "bash"}\n' "$(date +%s)" >> ~/.gemini/pet-events.jsonl  # 过程
printf '{"ts": %s, "state": "idle"}\n' "$(date +%s)" >> ~/.gemini/pet-events.jsonl      # 收工
```

## 5. 新增一个内置 Agent（改桌宠代码）

若要走一等公民路径（内置开关、专属直读适配器），当前需要改动以下位置：

| # | 位置 | 改什么 |
|---|---|---|
| 1 | `pet/config.py` `_default_agent_link_data()` | 新增默认开关键 |
| 2 | `pet/config.py` `_clean_agent_link_data()` | 键列表加入新键 |
| 3 | `pet/agent_link.py` `AgentLinkManager.__init__` | `monitors` 字典注册监视器实例 |
| 4 | `pet/agent_link.py` `AgentLinkManager.AGENT_NAMES` | 显示名 |
| 5 | `pet/context_menus/shared.py` `add_agent_link_menu()` | 菜单条目 |

监视器实现上，走统一协议的只需继承 `BaseAgentMonitor`（参考 `CustomAgentMonitor`）；已有本地事件源的覆写 `_poll()`（参考 `CursorMonitor`/`OpenCodeMonitor`），把解析结果经 `state_changed`/`activity` 信号发出即可。

> 后续演进方向（未实施）：把上述 5 处收敛为单一声明式注册表，内置与自定义 Agent 统一由注册表驱动。

## 6. 隐私与安全红线（不可违反）

1. **默认全关**：所有 Agent 联动开关默认关闭，用户显式勾选才启动。
2. **不访问外网**：联动链路只碰本地文件/本地数据库，以及本机回环（`127.0.0.1`）端口探活；任何一方不得向外网发起网络请求。
3. **只存元数据**：事件文件只有状态/事件名/工具名/错误码/身份标识，绝不写截图、代码内容、命令全文、消息正文（DSH 桥接自 2026-10 起连截断后的正文/命令/参数指纹/结果摘要也不再落盘）。有意的例外：错误摘要（`errorMessage`，截断 300 字）与问题正文（`question/requested` 的 `questions`，agent 生成的待选问题）——前者是排障所必需，后者是气泡提示的内容本体。
4. **写外部配置必须先弹窗**：任何向 Agent 配置（如 `~/.claude/settings.json`、dsh profiles）注入内容的操作，必须先经用户确认，且卸载时只清理带本桌宠标记的条目。
5. **自定义通道只读**：`custom_agents` 仅监听用户指定的文件，不创建目录、不写任何外部位置。

## 7. 本分支（petcore + `ai.agent`）的实现范围

主分支的实现是 `pet/agent_link.py`（单进程内直连 `AgentLinkManager`）。本分支把它拆成
**Core 接缝 + 可卸载 mod**：

| 能力 | 主分支 | 本分支 |
|---|---|---|
| 事件文件 tail | `ByteOffsetTailer` / `DirGlobTailer` | `examples/petcore-mods/ai.agent/host/tailer.py`（同一套偏移/半行/有界口径） |
| 六态映射 | `pet/agent_event_normalizer.py` + `dsh_state.py` | `ai.agent/host/protocol.py`（表外事件一律忽略） |
| 动画/气泡/音效 | `AgentLinkManager` | `ai.agent/host/links.py`（`LinkEngine`） |
| DSH 桥接插件安装 | 内置 `integrations/dsh-pet-bridge` + pnpm 安装 | 设置页「常规 → DSH 连接」显式安装（`dsh-pet-bridge` + `dsh-pet-channel`），先备份 profile |
| Claude hooks 注入 | 支持 | **未做**（走统一 `agent-events/claude.jsonl`） |
| Cursor / OpenCode 直读 | 支持 | **未做**（走统一 JSONL） |
| 审批/提问常驻卡片、live 成本 | 支持 | DSH 页可答审批；live 成本 **未做** |

行为口径（节流、完成确认、动作池、隐私红线）见
[`PETCORE-SPEC.md`](PETCORE-SPEC.md) §28。写方接入方式不变：仍然只是往
`agent-events/<agent>.jsonl` 追加一行 JSON。
