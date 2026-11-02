# 桌宠 × DSH × QQ 互通与人格记忆（2026-10-31 基线）

> 基线：`feat/petcore-vpet-architecture` / petcore 0.3.0；DSH 真机、QQ 真机未在本机验证，
> 文中「已验证」均指假服务/单元测试，真机项在每节末尾单列。

## 1. 进程与职责

```
DSH 桌面端（Node/Cordis）
  └─ integrations/dsh-pet-bridge v2（同一进程）
       ├─ 脱敏状态 JSONL  %APPDATA%/dsh-pet-bridge/dsh-<pid>.jsonl
       └─ HTTP+SSE 127.0.0.1:6791  /pet/*
            ▲  x-pet-token（pet-channel.json）
            │
petcore（Python/PySide6 单进程）
  ├─ petcore/net/dsh.py       DshClient（只连回环）
  ├─ petcore/ui/chat/         DshHub（进程内一条 SSE）+ DshView
  ├─ petcore/interop/         MemoryStore / Watermark（纯 stdlib）
  ├─ petcore/ui/memory_service.py  MemoryService（GUI 线程 + 10 分钟维护）
  ├─ petcore/chat/session_store.py channel=ai|dsh|qq 的统一会话库
  └─ petcore/net/webhook.py   本地回环 OneBot 事件入口
        ▲  x-webhook-token
        │
OneBot v11 网关（NapCat/Lagrange 等，用户自装）
        ▲ HTTP API（send_private_msg / send_group_msg）
        │
im.qq mod（examples/petcore-mods/im.qq，Qt-free runtime + 设置页）
```

- **DSH 是唯一 agent 运行时**：桌宠不复制 DSH 的工具循环，只持久化投影并注入记忆上下文。
- **QQ 只是另一个通道**：入站事件由 core WebhookPort 收，mod 只做归一化/策略/出站；socket 不在 mod 里。
- **DSH 状态与聊天同进程**：v2 合并了旧的 `dsh-pet-bridge` 与 `dsh-pet-channel`；状态 JSONL 仍只写脱敏元数据。

## 2. 通道契约

### 2.1 DSH v2

| 端点 | 作用 |
|---|---|
| `GET /pet/ping` | `{ok, version, bootId, sessionId, pending, sessions}` |
| `GET /pet/events?since=` / `Last-Event-ID` | SSE；`id: <seq>`、`bootId`、512 帧环重放；`reset=true` 表示太旧需重取历史 |
| `POST /pet/prompt {text, sessionId?, context?}` | `context={memory,persona}` 作为内部 system 注入，不进历史 |
| `GET /pet/history?sessionId&sinceSeq&limit&includeInternal` | 默认过滤 `source.kind=plugin`；返回 `text/reasoning/activity/ts/seq` |
| `POST /pet/answer {id,allow}` | 幂等；未知/过期 410 `expired` |
| `POST /pet/cancel` | 取消当前轮 + 清空排队 prompt + pending 审批，返回 `cleared` |
| `GET /pet/sessions` / `POST /pet/new` | 当前 + 已归档会话 / 新建 DSH 会话 |
| `GET /pet/approvals` | 重连后恢复待审批卡 |

- 审批：`tools/pre-execute` 先返回 reason 拦下；同意后记一次性 grant（绑定 tool + argsHash），
  再发内部 follow-up 继续；超时自动取消并 follow-up。`askApproval` 默认 false，沙箱默认 `danger-full-access`。
- `pet_control` 工具与 `/pet say|play|notify` 仍写 `dsh-petcmd-<pid>.jsonl`，`ai.agent` 消费。
- 隐私：状态 JSONL 不写正文/命令/参数/结果摘要；聊天 SSE 带正文，仅回环 + token。

### 2.2 QQ（Beta）

- 入站：网关 POST `http://127.0.0.1:<port><path>`，`x-webhook-token`/`Authorization: Bearer`/`?token=`；
  只接受 JSON，1 MiB 上限；空 token 拒绝启动。
- 归一化：`channel=qq`、`conversation_id=group:<id>|private:<user_id>`、`sender/sender_id/external_id=message_id/ts`。
- 策略：群/私聊白名单（空 = 忽略该类）、`message_id` 去重、CQ 码转纯文本、单条 4000 字截断。
- 回复：`off|notify|ai`（默认 notify）；`ai` 用 `ai_service` + `ai_model` + `MemoryPort.render_prompt("qq")`；
  `dsh` 自动回复留待下一版（当前只在 DSH 会话里手动互通）。
- 出站：`HostPorts.http` POST 网关 `/send_private_msg|send_group_msg`，token 只读钥匙串 `im.qq/access_token`。
- Webhook token：首次启用自动生成并写钥匙串；设置页可查看/复制/生成新 token（生成后需重启桌宠让监听换新 token）。

## 3. 会话库（schema 2）

```
<data_root>/sessions[-slot-N]/<character>/<session-id>.json
{
  "schema": 2, "session_id": "...", "title": "...", "channel": "ai|dsh|qq",
  "conversation_id": "group:123", "created_at": 0, "updated_at": 0, "archived_at": 0,
  "messages": [{"id","role","content","ts","channel","sender?","external_id?","meta?",
                "reasoning?","activity?"}]
}
```

- v1→v2：读入即补 `channel=ai`、`id=m<index>`、`ts=0`；第一次写回前把原文件复制为 `<name>.pre-v2`。
- `SessionStore.list_sessions(channel=)`、`find_by_conversation()`、`ensure_conversation()`、
  `append(external_id=)` 按 external_id 去重。
- 侧栏分组：AI 对话 / DSH 对话 / QQ 消息；组内日期分节；归档/删除按 channel 生效。
- `ChatPort.open_channel/ingest/register_channel/messages` 是 mod 的唯一入口；`dsh` 通道由 core 注册，
  `qq` 由 `im.qq` 注册，`ai` 仍走兼容的 `on_submit(owner, cb)`。

## 4. 分层人格记忆

```
<data_root>/interop/memory/<character>/
  inbox.jsonl        只追加；AI/DSH/QQ 消息与显式「记住」都写这里
  card.json          单写者（MemoryService）；重要度/情绪/清晰度/pin/召回次数/superseded_by/origin
  archive.jsonl      压缩/删除/静音/外部移除的条目（只追加，永不进提示词）
  seen.json          去重键（日期+发送者+文本）
  external.json      外部镜像来源、最后同步时间、静音 origin
  prompt-<scope>.md  渲染块：pet 2200 / dsh 2600 / qq 2000 字
  migration-replies.json / tidy-v2.json  一次性整理标记
```

- 写入：`append_inbox` → `ingest()`（单写者）→ 评分/去重/更正覆盖 → 卡；`maintain()` 启动 + 每 10 分钟。
  用户显式新增/编辑/删除/恢复也经 core `MemoryService`（同一个单写者）；删除保留归档，可恢复。
- 去重与更正：同一规范化文本跨天不再追加（touch `recall_count`、取更高 importance）；带
  `改成/改为/更正/记错/不再/不是…而是` 且**最长公共子串 ≥4 且覆盖旧文本 60%** 时把旧条目
  标为 `superseded_by=新条目`；`tidy()` 只做这两类保守操作，不物理删除，可在「记忆」页恢复。
- 助手回复：`kind="reply"` 只留档、不进提示词；旧数据用会话原文一次性标记（`migration-replies.json`）。
- 评分（纯函数）：基础 8；主人私聊 +42 / 群里 +32；直接对她说 +8；情绪 0–24；第一次/最早 +30
  （`importance=max(100)` 且 pin）；约定 +16；矛盾/道歉 +14；关于自己 +5；<6 字 −12；夹 1–100。
- 衰减：7 天未召回开始降清晰度（vivid→clear→fuzzy→trace），pin 不衰减；容量 1000 默认，超限按
  `(pin, importance, last_seen)` 保留，淘汰项进 `archive.jsonl`。
- 渲染：候选先按「bigram 相关度 + 30 天半衰新近度 + 重要度 + pin/first + 召回次数」打分，
  `limit` 是**按分数取 top-K**（不再按分区优先级截断），选完再按
  `最初/锚点/和主人/桌宠/DSH/QQ/最近/模糊` 排版；超预算先丢最低分的已输出条目。
- 可见性：`shared` 永远可见；AI 的 `pet` 与 DSH 的 `dsh` 默认互见
  （`ai_memory_share_channels=True`，关掉退回严格 scope）；`qq` 只与 `shared` 互通。
- 外部只读镜像：`petcore/interop/external_memory.py` 读四类来源——
  `dsh.memory` 的 `<data_root>/memory/facts.jsonl`、meow-memory 的
  `<workspace>/.dsh-meow/memory.db`、身份接力的 `facts.jsonl`（默认
  `~/.dsh/identity-memory`）、feiyu 的 `sessions/<sid>/log.jsonl`（默认
  `~/.dsh/feiyu-memory`，可由 `ai_memory_feiyu_root`/`ai_memory_feiyu_session`
  指定）。`MemoryService` 启动后 3 秒、之后每 10 分钟在单线程 worker 同步
  （`ai_memory_import_dsh` 总开关）。身份接力/feiyu 的坏行返回 `partial`：只新增/
  更新镜像，**不删除**已有条目；只有完整 `ok` 才执行移除。外部条目带 `origin`、
  `scope=dsh`，在「记忆」页显示只读徽标；删除=加入静音表，绝不写回源。
  多个源镜像到同一句话时，`render` 按规范化文本去重，只保留分数最高的一条。
- 迁移：旧 `feature-data/ai.chat/memory/global.jsonl` 与 `<character>.jsonl` 一次性导入
  `source=legacy`，旧文件原样保留，标记 `interop/memory/migration-ai-chat-<character>.json` 幂等。
- 人格层：`petcore/interop/persona.py` 提供核心/场景/日常组合 + 预算裁剪；`ai.chat` 的设置页
  仍是人格卡 UI，`MemoryPort` 负责记忆上下文。

## 5. 未读水位与快照

`petcore/interop/messages.py`：
- 每个会话有本地单调 `seq`（不是平台 messageId）；
- `read_through` 只能推进到真的展示过或确定不会再展示的位置；
- `snapshot(limit, recent_limit)` 返回 `messages/included_unread_count/unread_count/read_through_seq/
  partial/folded{count,from_seq,to_seq}`，一次注入太多未读时折叠。

## 6. 插件边界与权限

| 关注点 | 位置 | 说明 |
|---|---|---|
| 会话库、DSH 客户端/视图、记忆服务 | core | 窗口与 Qt 只能在 core |
| DSH 状态→动画/气泡 | `ai.agent` mod | 只读 JSONL + `PresentationPort` |
| QQ 入站/出站/策略 | `im.qq` mod | 无 socket；WebhookPort + HttpPort |
| 记忆卡维护 | core `MemoryService` | 单写者；mod 只 append / 只读端口；外部源（dsh.memory、meow-memory）只读镜像 |
| 设置键 | core `DEFAULTS`（音频/DSH/联网）或 `config.extra`（QQ） | 全局键进 `GLOBAL_CONFIG_KEYS` |
| 凭据 | 钥匙串 | `speech.*`、`core.websearch`、`im.qq/access_token`、`im.qq/webhook_token` |

失败必须可见：webhook 启动失败/端口占用、token 缺失、OneBot 地址错误、DSH 未运行/审批过期、
记忆迁移失败都会冒泡或写设置页状态；日志不写正文、token、完整 URL/查询串。

## 7. 验证与未验证

已验证（本机）：
- `tests/test_petcore_dsh.py` 14 passed（假 HTTP/SSE、审批终态、共享 SSE、安装器迁移）
- `tests/test_petcore_webhook.py` 3 passed；`test_petcore_qq_mod.py` 3 passed（假 OneBot/假 webhook）
- `tests/test_petcore_interop_memory.py` + `test_petcore_memory_service.py` 10 passed
- `tests/test_petcore_sessions.py` + `test_petcore_chat_ui.py` 41 passed
- `node --test tests/test_bridge_*.js` 44 passed；`verify_import.mjs` 通过

未验证（必须由用户真机复核）：
- DSH 桌面端真实 `sessionQuery.observeSession` / 审批 / `/pet/new` / 记忆 context 注入；
- meow-memory 真机：本机没有 `.dsh-meow/memory.db`，只做合成库单测；`dsh.memory` 的
  `facts.jsonl` 只读镜像在本机验证，第三方扩展本身不改；
- QQ 网关（NapCat/Lagrange）真实登录、收发、群白名单、CQ 解析；
- 多角色/多宠的 DSH 会话隔离（当前 DSH lane 以进程为主，QQ 会话按 conversation_id 分）。
