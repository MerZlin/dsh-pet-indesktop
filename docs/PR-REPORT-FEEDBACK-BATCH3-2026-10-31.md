# 反馈批次 3：音频输出 / 每日千句 / 并行下载 / 桌宠×DSH×QQ 互通与人格记忆

> **基线**：`0dadd90a`（上一批备份提交；本批在工作区）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-10-31
> **范围**：core 音频/会话/记忆/DSH 通道/QQ 适配/联网检索 + 4 个新测试域
> **关联**：[`INTEROP-ARCHITECTURE.md`](INTEROP-ARCHITECTURE.md)、[`PETCORE-LOG.md`](PETCORE-LOG.md) L143–L148

## 一、核心特性

| # | 能力 | 说明 |
|---|---|---|
| 1 | 音频输出设备 | core 全局键 `audio_output_device`；点击音效、Agent 提示音、自语/报时 TTS 共用；手动设备拔掉静音、不回退默认 |
| 2 | 每日一句 1200 句 | 随包池 + 每天按 `(day,slot)` 抽 12 句 + `daily.json:index` 推进 + 圆角编号预览 |
| 3 | PCL2 式下载 | 多连接 Range（1–8，默认 4）+ 镜像 TTFB 竞速 + 段级续传 + 进度条/速度/ETA/取消 |
| 4 | 会话 schema 2 | `channel=ai|dsh|qq` + 消息 provenance + 侧栏三分组 + v1→v2 备份迁移 |
| 5 | 分层人格记忆 | inbox → 衰减卡 → 预算渲染；AI/DSH/QQ 共用；旧 ai.chat 记忆一次性迁移 |
| 6 | DSH v2 | bridge+channel 单插件；SSE id/since/bootId 重放、历史过滤、记忆注入、审批 argsHash/410、cancel 清队列 |
| 7 | QQ Beta | `im.qq` + core WebhookPort：OneBot 入站白名单/去重/CQ、出站 send_*、AI 回复、钥匙串 |
| 8 | 联网增强 | 多引擎扇出 + URL 去重 + article/main 正文抽取 + 标题/描述/发布时间 + 多引擎/正文上限设置 |

**红线 / 不变量**：旧 `pet/` 产品代码不改；`config.json`/`api.json` 不出现任何 Key；
DSH 状态 JSONL 仍不写正文/命令/参数指纹/结果摘要；webhook 只绑 127.0.0.1 且空 token 拒绝；
手动音频设备不存在时**绝不**回落到默认设备；下载 200（忽略 Range）只回退单流一次。

## 二、修改文件说明

### 实现（`git diff --numstat`；新文件按行数）

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/media/audio_output.py`（新） | ~250 | `AudioRoute`/`AudioOutputRouter`/`list_outputs`；热插拔、缺失设备静音、无 Qt 降级 |
| `petcore/media/sound.py` | +83/−9 | `SoundPlayer(route_provider)`；设备变更清缓存重建；静音时 `play()` 返回 False |
| `petcore/ui/ports.py` | +86/−2 | `AudioAdapter` 路由；`CoreWebhookPort`；`MemoryPort`/`webhook` 接入 HostPorts |
| `petcore/ui/app.py` | +60 | 创建 audio router/memory/webhook；实时扇出、退出关闭 |
| `petcore/ui/settings/page_voice.py` | +117 | 「音频输出」卡片 + 设备状态 + 搜索别名 |
| `petcore/ui/settings/page_interaction.py` | +4/−1 | 试听 `SoundPlayer` 接路由 |
| `petcore/config.py` | +88/−16 | 音频/下载/联网/DSH 新键；每日旧默认迁移；normalize |
| `petcore/builtin/features/fun.self-talk/daily_lines.json`（新） | 1206 行 | 1200 条唯一每日句 |
| `.../host/daily_pool.py`（新） | 117 | 池加载/去重/确定性抽取 |
| `.../host/daily.py` | +44/−7 | `index` 状态；旧 `{date}` 兼容 |
| `.../host/factory.py` | +302/−90 | 每日轮播、设置页预览、连说/试听修复 |
| `scripts/generate_daily_lines.py`（新） | 608 | 可复现 1200 句生成器 |
| `petcore/cli.py` | +44/−2 | `--check` 的 `self_talk_daily` + strict |
| `scripts/build_petcore.ps1` | +6/−2 | 构建门断言池 ≥1000 |
| `petcore/net/ranged.py`（新） | 463 | 分段计划/段级续传/装配/取消 |
| `petcore/net/market.py` | +341 | 分段传输、预检、206 严格校验、TTFB 竞速 |
| `petcore/net/market_transport.py` | +134/−18 | 分段/单流回退、artifact_name、进度单调 |
| `petcore/market/download.py` | +134/−13 | 连接数/源策略、稳定 `.part`、免 staging 整包拷贝 |
| `petcore/market/install.py` / `cli.py` | +27 | 配置接线 |
| `petcore/ui/settings/page_market.py` | +231/−8 | 进度条/速度/ETA/续传/连接数/取消 |
| `petcore/chat/session_store.py` | +183/−24 | schema 2、channel/conversation、external_id 去重、pre-v2 备份 |
| `petcore/ui/chat/controller.py` | +301/−5 | `register_channel/open_channel/ingest/messages`、通道提交路由、DSH 持久化/上下文 |
| `petcore/ui/chat/window.py` | +76/−37 | AI/DSH/QQ 三分组 + 日期分节 |
| `petcore/interop/{memory,messages,persona}.py`（新） | ~700 | inbox/衰减卡/评分/渲染/水位/人格层 |
| `petcore/ui/memory_service.py`（新） | ~230 | 启动 + 10 分钟维护、迁移、MemoryPort 实现 |
| `petcore/net/dsh.py` | +~90 | sessions/new/approvals/history 参数/SSE since/410 |
| `petcore/ui/chat/dsh_controller.py` / `dsh_view.py` | +110/−10 | cursor/bootId、帧持久化、本地历史、记忆上下文、新会话 |
| `integrations/dsh-pet-bridge/{index,channel}.js` | 合并 | v2 单插件；SSE 重放/审批 argsHash/历史过滤/context |
| `petcore/platform/dsh_install.py` | +~20 | 只装 bridge；升级移除旧 channel |
| `petcore/net/webhook.py`（新） | ~190 | 回环 webhook/token/1MiB |
| `examples/petcore-mods/im.qq/*`（新） | ~700 | OneBot 入站/出站/策略/AI 回复/设置页 |
| `petcore/net/websearch.py` | +~120 | `search_many`、article/main 正文、标题/描述/时间 |
| `petcore/api/chat.py` | +~25 | 多引擎/正文上限接线 |
| `packaging/petcore.iss` | +4 | `mod_qq` 默认不勾选 + `.trusted` |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_audio_output.py`（新） | +200 | 跟随/手动/缺失静音/热插拔/无 Qt/缓存重建/设置页 |
| `tests/test_petcore_self_talk_daily.py` | +437/−18 | 池完整性/确定性/index/旧状态/预览/试听 |
| `tests/test_petcore_net_ranged*.py`（新） | +836 | 计划/206/200 回退/429/取消/续传/并发 |
| `tests/test_petcore_market_download_segmented.py`（新） | +452 | 下载层/竞速/稳定 `.part`/安装接线 |
| `tests/test_petcore_interop_memory.py`（新） | +130 | 评分/去重/预算/衰减/迁移/水位 |
| `tests/test_petcore_memory_service.py`（新） | +70 | remember/render/QQ scope/迁移幂等 |
| `tests/test_petcore_sessions.py` | +56 | schema 2/备份/channel/external_id 去重 |
| `tests/test_petcore_chat_ui.py` | +61/−3 | 三分组/QQ 提交路由/ingest 不劫持 AI |
| `tests/test_petcore_dsh.py` | +~40 | 安装器迁移/新客户端参数/共享 SSE |
| `tests/test_petcore_webhook.py`（新） | +60 | token/路径/1MiB/空 token |
| `tests/test_petcore_qq_mod.py`（新） | +150 | 入站去重/白名单/CQ/通知/出站/AI 回复 |
| `tests/test_petcore_websearch.py` | +55 | article/main/元数据/search_many 去重 |

### 未改动

- `pet/` 产品代码（v4.4.0 主分支）未动；主分支素材包未重新生成。
- 本批未把 `dsh` 自动回复接到 QQ（`im.qq` 的 `reply_mode` 只有 off/notify/ai）；已在 L148 与
  `INTEROP-ARCHITECTURE.md` 登记为下一版。

## 三、实现要点

- **音频**：`AudioOutputRouter` 只解析一次配置并缓存 `AudioRoute.key`；设备信号触发重解析，
  变化时通知宿主把 route 扇出到所有 `PetWindow.sound` + `AudioAdapter`。`SoundPlayer` 在
  `apply_route()` 里停播 + 清两套缓存 + 按 `_warmed` 重建，避免 `QSoundEffect` 停在 Error 后永久静默。
- **每日池**：池是包内数据、不进 `DEFAULTS`；`normalize` 只把旧默认 12 句识别成 `[]`，
  不做一次性迁移文件。状态 `{date,index}` 只在气泡真的显示后推进；当天抽完回退普通自语。
- **会话 2**：`ChatSession.from_dict` 接受 schema 1/2；写回前 `_backup_legacy` 复制 `.pre-v2`；
  `append(external_id=)` 在会话内去重。`ChatPort` 的 `ai` 通道仍走旧 `on_submit(owner,cb)`，
  `dsh/qq` 走 `register_channel`，避免破坏 ai.chat。
- **记忆**：`append_inbox` 只追加；`ingest` 是唯一卡写者；评分/衰减/压缩/渲染全纯函数；
  `render` 按分区优先级和预算裁剪，旧 ai.chat 记忆一次性导入 `source=legacy`。
- **DSH v2**：bridge 的 `apply(ctx, config)` 末尾调用 `applyChannel(ctx, config)`；`inject` 改为
  `agents/sessionQuery/tools`；SSE `push` 生成传输层 `seq` 并保留 `eventSeq`；审批 grant 绑定
  `argsHash`；`/pet/history` 过滤 `source.kind=plugin`；`/pet/cancel` 重置 `lane.chain`。
- **QQ**：入站 socket 在 core `WebhookPort`，mod 只注册 handler；事件在 GUI 线程归一化/入库/冒泡，
  出站与 AI 回复在短线程里走 `HostPorts.http`/`HostPorts.api`；token 只进钥匙串。
- **联网**：`search_many` 顺序扇出、URL 去重、单引擎失败继续；`html_to_text` 优先 article/main，
  去 nav/aside/script，保留标题/描述/发布时间；`fetch_page_text` 的 `max_chars` 由设置页控制。

## 四、性能分析

**方法**：Windows 11 26200、Python 3.12.10、16 逻辑核；loopback `ThreadingHTTPServer`；
音频 QtMultimedia 真机；命令见 L143–L145 与 `.scratch/bench_ranged_download.py`。

| 指标 | 实测 | 归属 |
|---|---|---|
| 下载 96 MiB 单流 | 10.326 / 10.324 / 10.295 s，中位 9.30 MiB/s | 既有路径 |
| 下载 96 MiB 4 连接 | 2.940 / 2.912 / 2.872 s，中位 32.96 MiB/s（**3.54×**） | 新增路径 |
| 200-only 回退 | 2 请求（预检 + 单流），8 MiB / 0.393 s，无 `.seg*` | 新增回退 |
| 取消延迟（4 连接限速） | 0.056 / 0.066 / 0.066 s，中位 66 ms | 新增 |
| 每日池冷加载/缓存 | 0.71 ms / 0.19 ms | 新增（20–60s 一次） |
| 每日抽 12 句 | 0.31 ms | 新增（每 tick） |
| 每日状态写 | 1.48 ms | 新增（每条每日句一次） |
| 每日预览渲染 | 0.68 ms（12）/0.81 ms（24） | 新增（设置页） |
| 音频设备枚举 | 冷 78 ms / 暖 <0.001 ms | 新增（仅路由/设置页） |
| 音频路由真机播放 | 0.3s wav 正常完成；`QSoundEffect`/`QMediaPlayer` 设备 id 均与 route 一致 | 新增 |
| 记忆维护 | 启动 + 每 10 分钟；卡/池纯文件操作，无新线程 | 新增（低频） |
| 空闲线程/定时器 | 音频 0 常驻定时器；记忆 1 个 10 分钟 QTimer；webhook 仅启用 QQ 时 1 个监听线程 | 新增 |

**结论**：① 稳态开销只在「每日 tick 0.6ms + 记忆 10 分钟一次」量级，空闲不新增轮询；
② 新增路径成本集中在下载（收益 3.54×）与设置页一次性渲染；③ 新系统调用只有 QQ 启用时的
回环 socket，下载按连接数开 socket，结束回收；④ 记忆池常驻约 1200 字符串 + 当前角色卡，
不随对话无限增长（卡容量 1000 默认，超限进 archive）。

## 五、实机运行记录

**本机真实环境**（非 CI/mock）：

```text
$ .venv/Scripts/python -m petcore --check --strict
strict.problems = []
self_talk_daily = {schema: 1, count: 1200, unique: 1200}

$ QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_audio_output.py -q
12 passed

$ QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_qq_mod.py tests/test_petcore_webhook.py -q
6 passed

$ node --test tests/test_bridge_*.js
44 passed
$ node integrations/dsh-pet-bridge/verify_import.mjs
bridge zero-dependency smoke: import + subtraction surface + source bans OK
```

音频真机探针（`QT_QPA_PLATFORM=offscreen` + 真实 QtMultimedia，volume=0）：

```text
route follow True <默认设备名>
done ['ok']
device {0.0.0.00000000}.{b58c8d2c-...} expected {0.0.0.00000000}.{b58c8d2c-...}
prepare True effect QSoundEffect
device_match True
play True
```

全量测试（`tests/test_petcore_*.py`）收尾复跑：**2729 passed, 1 skipped in 211.15s**（2026-10-31，Windows 11 26200 / Python 3.12.10）。本机 `git diff --check`、`ruff` 均通过。打包产物与启动记录见 `docs/PETCORE-LOG.md` L149。

**未验证项（必须如实说明）**：
- 真 DSH 桌面端：本机未安装，v2 的 `observeSession`/审批/`/pet/new`/记忆 context 未真机联调；
- 真 QQ 网关：本机无账号/网关，`im.qq` 只用假 OneBot/假 webhook 测试；
- 多输出设备/蓝牙重配对：本机只有一只输出设备，靠假设备覆盖；
- QQ `dsh` 自动回复、OBS 真机抓屏、安装向导逐页交互未验证。

## 六、复审修正（独立审查子代理）

独立只读复审在合并前指出 2 个 H、12 个 M、10 个 L；已在交付前修正其中会影响数据/行为/安全的项：

| 级别 | 问题 | 修正 |
|---|---|---|
| H1 | DSH/QQ 帧写入的是新会话对象，关闭窗口 `_save(state)` 用旧快照覆盖，回答丢失 | `_persist_dsh_frame`/`ingest` 在 active session 命中时把 `state.session` 换成刚保存的新对象；新增 `test_dsh_frame_survives_later_state_save` |
| H2 | `DshView._new_session` 在工作线程直接改按钮/状态栏 | 改为 `session_finished/session_failed` 信号回 GUI，工作线程只 emit |
| M3 | 无 AI 会话时会 fallback 到 DSH/QQ 会话，AI 输入被路由到 DSH/QQ | `_ensure` 只取 `channel=ai`，没有就新建 AI 会话 |
| M4 | 折叠 channel 组后日期子组把行重新显示 | 日期组先看父 channel 是否折叠 |
| M5 | 带记忆上下文的用户 prompt 被标成 plugin，历史里消失 | 用 `【桌宠上下文·仅供理解】/【用户】` 包裹；`/pet/history` 只返回 `【用户】` 后的正文；source.kind 仍为 user |
| M6 | DSH 视图直接调 hub，用户 prompt 不落会话 | DshView 发 `send_requested` → ChatWindow → Controller `_on_dsh_submit` 落盘 + 注入记忆 + 发送 |
| M7 | DSH 重启后 transport seq 从 1 开始，external_id 冲突丢消息 | external_id 加 `bootId` |
| M8 | `/pet/history?limit=200` 超过客户端 1 MiB 读取 → JSON 截断 | `DshClient._json` 上限提到 8 MiB |
| M9 | 路由 provider 抛异常时 SoundPlayer 落回默认设备 | 保留上次 route；一次都没成功则静音 |
| M10 | QQ 空白名单等于全放行，可能自动回复所有群/私聊 | 空白名单 = 忽略该类消息（与设置页/文档一致） |
| M11 | 多引擎空列表固定回退 baidu，`ai_web_engine` 失效 | `search_many(fallback_engine=options.engine)`；normalize 允许空列表 |
| M12 | `dsh_memory_inject` / `dsh_history_limit` / `ai_web_search_max_sources` / `ai_memory_inject` 没被读 | 分别接入 `_dsh_context`、`DshHub(history_limit=)`、`WebSearchOptions.max_sources`、`render_prompt(limit=)` |
| M13 | inbox 每次全量重读 + seen 剪枝后重复入卡 | 增加 `inbox.offset`，只读新增完整行；offset 在卡保存成功后推进 |
| M14 | `shared` 记忆实际落成 pet，DSH/QQ 看不到 | inbox 增加 `scope` 字段，`ingest` 优先使用 |
| L15 | 每次播放都枚举设备 | router 缓存 configured key，只有配置/设备信号变化才重解析 |
| L16 | SSE `hello.reset` 被忽略 | reset 时清缓存并重连 |
| L17 | history 的 sessionId 被忽略、answer 重复返回 410 | 非当前 sessionId 返回空 + archived；resolved id 幂等 |
| L18 | 轮询重叠可能重复推帧 | `lane.pollingBusy` 互斥 |
| L19 | Windows webhook 端口可复用 | `allow_reuse_address=False` |
| L20 | 刷新设备丢弃未保存选择 | 保留当前 combo 选择 |
| L21 | QQ AI 回复 external_id 秒级冲突 | 改 `time.time_ns()` |
| L22 | 角色 id 可路径逃逸 | `MemoryStore._safe_name` |
| L23 | 归档回退不看 channel | 按当前 channel 回退并切视图 |

保留未改：`dsh_persona_inject` 当前只作为预留键（文档已标明）；DSH 真机/QQ 真机仍按“未验证”处理。
