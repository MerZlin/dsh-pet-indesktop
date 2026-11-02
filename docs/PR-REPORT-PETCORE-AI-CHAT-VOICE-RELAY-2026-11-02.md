# PR 报告：petcore ai.chat 回复朗读流水线 + 身份接力记忆后端（2026-11-02）

> PR：`feat/ai-chat-voice-pipeline-relay-memory`（base:
> `feat/petcore-vpet-architecture`）
> 摘要：①把主分支已实测的「回复朗读按句滴灌 + 预合成前锋」移植进 petcore
> `ai.chat`（原先 `ai_voice_reply` 是整段合成一条龙）；②新增「身份接力记忆」
> 可选后端，桌宠与 DSH 共用同一份 `facts.jsonl`（同键新压旧 / 作废盖章 /
> 来源优先），并注入 persona.md / handover.md。两项都默认关闭，关闭时行为
> 逐位不变。

## 修改文件说明

| 文件 | 增删 | 改了什么 + 为什么 |
|---|---|---|
| `examples/petcore-mods/ai.chat/host/reply_voice.py` | +230（新增） | `clean_for_speech` / `split_sentences` 与主分支 `pet/chat_reply_voice.py` 同口径（去 Markdown、按句末标点切句、超 80 字就近软切），保证朗读内容与主分支已实测版本一致；`ReplyVoicePlayer` 只依赖注入的 `speak/precache/enabled/cancel`，链式 `on_finished` 交棒、generation 作废旧链、失败停链——不 import Qt / net / api，可离线测试。 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +137 / −19 | `_speak_reply` 从「整段一次 speak」改为按句 `speech.speak(..., owner=OWNER)` 滴灌；送出当前句的同时把下一句交给 `SpeechPort.synthesize()`（后台线程预热缓存，失败静默退化为在线合成）；新回复先 `cancel_owner(OWNER)` 打断上一轮；`stop()` 清空朗读队列。删除只服务于整段合成的 `_speech_text`（含一个不再需要的 `re` import）。记忆侧新增 `_relay_enabled/_relay_dir`，`_remember_text` 在接力开启时写 `facts.jsonl`（source=owner，与 DSH 侧「主人原话」同档），`_memory_context` 用 recall 注入，`_prompt` 追加 persona.md/handover.md（按 mtime 缓存）。 |
| `examples/petcore-mods/ai.chat/host/relay_store.py` | +268（新增） | facts.jsonl 适配层，与 DSH 版 dsh-identity-relay 的 `store.mjs` 逐字段对齐：同键新压旧（superseded + supersededBy + retractNote，不删行）、内容完全相同才去重、recall 来源五档优先 + 时间新→旧、硬上限 20、关联带出 ≤3、密钥模式拒写、进程内写锁；外加 `read_identity_text` 读 persona.md/handover.md。 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +77 / −1 | 「朗读回复」提示文案改为按句 + 预合成的真实行为；「长期记忆」新增三行（接力开关默认关、目录默认 `~/.dsh/identity-memory`、注入人格默认开），状态行显示接力统计；三个新键登记进 `_KEYS`，`_sync_memory` 补齐回读。 |
| `examples/petcore-mods/ai.chat/mod.json` | 1 / 1 | 版本 1.4.0 → 1.5.0。 |
| `tests/test_petcore_ai_reply_voice.py` | +315（新增） | 拆句/清洗 5 条 + 播放器行为 11 条（不叠音、预合成前锋、单句不预合成、预合成失败静默退化、新覆盖旧、失败停链、热开关、无预合成退回、stop）+ 运行时接线 5 条。 |
| `tests/test_petcore_ai_relay_memory.py` | +370（新增） | 适配层语义 26 条（对齐 DSH 版 store.mjs 行为测试，含同键新压旧/去重/来源排序/硬上限/关联带出/作废/密钥拒写/坏行跳过）+ 运行时接线 9 条 + 设置页往返 1 条。 |
| `docs/PETCORE-LOG.md` / `PETCORE-LOG-INDEX.md` | +（登记） | L158（朗读流水线）、L159（接力记忆）各一条 + 索引两行。 |

## 性能分析

环境：Windows 11，Python 3.12.14（`dsh-pet-src\.venv`），工作树
`robot-body\dsh-pet-vpet` @ `d098a5f`。基准脚本一次性运行，样本与命令如下：

```
$env:PYTHONUTF8=1; .venv\Scripts\python bench_relay_voice.py
样例长度(字符): 138
split_sentences: 0.046 ms/次, 拆出 4 句
store_stats(真库 C:\Users\admin\.dsh\identity-memory): 1.65 ms -> 89 条/81 active/8 superseded
recall_facts(真库,'身体'): 2.37 ms/次, 命中 8 条（前 5 来源: owner×4, evidence）
```

（`split_sentences` 为 5000 次均值、`recall_facts` 为 50 次均值。）

逐条回答：

- **稳态开销**：朗读开关默认关、接力开关默认关。关闭时新增代码只有一次
  `ai_voice_reply`/`ai_memory_relay_enabled` 的配置读取与一次布尔判断，无新增
  线程、无新增磁盘 IO、无新增网络；行为与改动前逐位一致（相关族测试 97+43
  passed）。
- **新增路径成本与触发频率**：每次回复完成触发一次 `split_sentences`
  （138 字样例 0.046 ms，可忽略）；每句播放触发一次预合成线程创建（回复长度
  决定次数，每次一个 daemon 线程，合成完成即退出）；每轮对话触发一次
  `recall_facts`（89 条真库 2.37 ms）与一次 persona/handover mtime 检查
  （命中缓存后不读盘）。
- **系统调用/网络/磁盘**：预合成复用的 `SpeechPort.synthesize` 有缓存层
  （命中即返回，无网络）；未命中时走与普通朗读完全相同的合成链路，不新增网络
  形态。接力记忆的读 = 一次 facts.jsonl 全量读（89 行 ≈ 30 KB 级），写只在用户
  说「记住…」时发生（append+原子重写，与 DSH 侧同一实现）。无新增定时器。
- **内存增长**：`ReplyVoicePlayer` 队列只存当前回复的句子列表（句数 ≤ 回复
  长度/句长，几十个字符串级别）；`_relay_identity` 缓存 persona/handover 两份
  文本（各 ≤4000 字）。无累积增长。
- **并发边界**：预合成线程与语音队列 worker 对同一句可能并发合成——同键同产物、
  mkstemp 临时文件 + `os.replace` 原子替换，最坏结果是多花一次合成（无错音频）；
  接力写锁只在进程内，跨进程并发写是「后写覆盖」（与 DSH 版 README Q8 同一
  已知边界）。

## 实机运行记录

1. **测试全绿**（本机真实执行，非 CI）：

```
QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_ai_reply_voice.py tests/test_petcore_ai_relay_memory.py -q
→ 54 passed in 3.70s
QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_ai_mod.py tests/test_petcore_ai_memory.py
  tests/test_petcore_ai_persona.py tests/test_petcore_ai_startup.py tests/test_petcore_quick_chat.py
  tests/test_petcore_interop_memory.py -q
→ 97 passed in 9.50s
QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_speech.py tests/test_petcore_settings_voice.py
  tests/test_petcore_memory_service.py -q
→ 43 passed in 5.27s
ruff check <全部改动文件> → All checks passed!
```

2. **真库只读对账**：新适配层直接读本机 DSH 身份接力真库
   `C:\Users\admin\.dsh\identity-memory`：`store_stats` 得 89 条 / 81 active /
   8 superseded / 0 archived，`recall_facts('身体')` 命中 8 条且来源排序 owner
   在前——与 DSH 侧 relay 工具的统计口径一致（同一时刻数字吻合）。这是「两个
   身体读同一份记忆」的格式对账证据，不是三端并发写实测。

3. **如实登记（未验证项）**：
   - petcore 打包版受本机 Smart App Control 拦截（SAC 强制的已知限制），无法
     打包实机点 GUI；源码运行版的语音后端未在本机真机配置，因此 **petcore 的
     朗读听感（句间停顿）没有实机验收**。主分支同方案的听感已由用户实测
     「基本没有停顿」（证据见主分支
     `docs/PR-REPORT-CHAT-REPLY-VOICE-PRECACHE-2026-11-02.md`），本条目是
     逐语义移植 + 22 条行为测试守卫。
   - 三端（桌宠/DSH/QQ）同时写同一份 facts.jsonl 的并发场景未实测；已知
     边界为后写覆盖，建议单侧写、另一侧只读。
   - 接力检索仍是字面包含（同 DSH 侧 recall 口径），语义/同义检索属远期项。

## 风险 / 回滚

- 两个开关都默认关；关闭时行为逐位不变（相关族测试守住）。
- 回滚：`git revert` 两个提交即可；数据无迁移——接力记忆只读共享目录，
  本机 JSONL 记忆原样保留。
