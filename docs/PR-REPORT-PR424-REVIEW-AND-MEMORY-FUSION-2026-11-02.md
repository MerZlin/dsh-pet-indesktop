# PR 报告：PR #424 审查 / 语音修复 / 身份接力与 feiyu 只读融合（2026-11-02）

> **基线**：`feat/petcore-vpet-architecture`，本地记忆基线 `b15c6f5d`（L158/L159）
> **审查对象**：PR #424 `feat/ai-chat-voice-pipeline-relay-memory`（head `6cc3e39f`）
> **结论**：语音方案成立、修 P0 后合并；身份接力记忆**不按原实现合并**，改为 core
> 只读外部源；贡献者 `dsh-fy-mod-puls-max` 的叙事通过 feiyu `log.jsonl` 只读融合。
> **关联**：`docs/PETCORE-LOG.md` L160/L161；架构更新见 `docs/INTEROP-ARCHITECTURE.md` §4。

## 一、审查结论（PR #424）

### 阻断项

1. **P0 语音回调契约**：`reply_voice.py:212` 的 `finished()` 零参数，而
   `SpeechPort.on_finished` 是 `Callable[[SpeechResult], None]`
   （`petcore/contracts/speech.py:109`，`speech_channel.py:171/203-210` 会传结果）。
   真实端口下 `TypeError` 被 `_callback` 吞掉，**每条多句回复只念第一句**。PR 的
   22 条语音测试 fake 也用零参数，所以 54 条全绿没有覆盖真实契约。
2. **P1 身份接力写路径**：`relay_store.py:100-103` 用 `Path.write_text` 整文件重写，
   没有 tmp+`os.replace`/fsync；`:57-61` 读失败返回 `[]`，随后把 `[]` 当真值写回；
   `:179-181` 按 `lines[-cap:]` 会删掉最老的 active 事实。与报告声称的「原子重写/
   不删行」相反。它从 `ai.chat` runtime 直接写 `~/.dsh/identity-memory/facts.jsonl`，
   违反本地「记忆单写者 / 外部记忆只读」两条红线，并绕开 `MemoryPort`、`redact`、
   可见性矩阵、渲染评分和记忆页。
3. **P1 召回与作用域**：`relay_store.py:221-223` 用整条用户消息做字面 `in`，真实问句
   几乎不命中；`runtime.py:616-630` 无 scope/slot/subject，所有角色共用一个池。
4. **P1 坏配置**：`runtime.py:251-266` 在 `_relay_dir()` 抛错时 `relay_dir` 未绑定，
   `except OSError` 捕不到 `UnboundLocalError`。
5. **P2 多宠朗读**：`runtime.py:474-506` 无 `slot=`，`cancel_owner(OWNER)` 会取消所有
   宠物的朗读；`runtime.py:508-523` 每句起一个线程，无上限/取消/总长。
6. **合并冲突**：`settings_page.py:_KEYS` 与本地 `ai_memory_share_channels/
   ai_memory_import_dsh` 同锚点硬冲突；PR 还把本地已修正的「每域上限」说明改回旧文案；
   PR 的 `L158/L159` 与本地已提交的 L158/L159 重号；`docs/INDEX.md` 同锚点冲突。
7. **本机事实**：本机没有 `~/.dsh/identity-memory` 与 `dsh-identity-relay`；PR 报告里的
   89 条真库是作者机器 `C:\Users\admin\.dsh\identity-memory`，本机无法复现。本机
   `dsh.memory` 真源仍是 `%APPDATA%/dsh-pet-core/memory/facts.jsonl`（2 条）。

### 采用的替代方案

- 语音：保留 PR 的拆句/滴灌设计，修回调、per-slot、预合成有界、stop 取消、英文断句。
- 记忆：**不搬** `relay_store` 写函数；在 core `external_memory.py` 增加身份接力与
  feiyu 两个**只读** `SourceResult`，由 `MemoryService` 单线程 worker 同步，复用
  `apply_external` / `render` / 记忆页；跨源同文在渲染层去重。

## 二、修改文件说明

`git diff --numstat`（工作区，未含新增文件）：

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---:|---|
| `examples/petcore-mods/ai.chat/host/reply_voice.py` | +281（新） | 拆句/清洗、`ReplyVoicePlayer`（完成回调接收 `SpeechResult`、RLock、总长/句数上限、stop 取消）、`PrecacheWorker`（单线程单槽、最多一个在飞）。 |
| `tests/test_petcore_ai_reply_voice.py` | +406（新） | 23 条：真实 `SpeechResult` 回调、双 slot 互不取消、预合成单飞/覆盖、stop 取消、英文断句、失败停链。 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +79/−24 | `_speak_reply(text, slot)`；按 slot 缓存 player/worker；`speak(..., slot=slot)`；`cancel_slot`；`stop()` 关闭全部 player/worker；删除旧整段 `_speech_text`。 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +55/−3 | 「同步外部记忆」总开关文案；身份接力目录、feiyu 目录/会话三行；保存/回读；朗读提示改真实上限。 |
| `examples/petcore-mods/ai.chat/host/memory_view.py` | +14/−6 | 身份接力/feiyu 只读徽标；`partial` 显示「部分读取」；「同步外部记忆」按钮与删除确认文案。 |
| `petcore/interop/external_memory.py` | +260/−1 | `read_identity_facts`（content/active/subject/来源五档/空文件与读取中变化 partial/单源上限 500）、`read_feiyu_logs`（add/amend/del/restore/supersede/purge、fact/event、milestone、未知 op 忽略但不冻结删除、单源上限 500）、`read_external_sources` 新参数。 |
| `petcore/interop/memory.py` | +108/−21 | `_dedupe_by_text` 渲染前跨源同文去重（保留 first/pinned 保底）；`_compact` 非置顶名额至少 60% 留给本地条目；`apply_external` 预计算本地文本集（旧 O(card×records) 1998ms → 84.63ms）并在 amend 时更新 importance/created_at；`_replace_with_retry` 解决 Windows 读-替换竞态（`WinError 5`）。 |
| `petcore/ui/memory_service.py` | +82/−1 | `_identity_dir` / `_feiyu_source`（含 `~/.dsh/feiyu-memory-root.json`）；同步 worker 传四源；切换 feiyu 会话后按旧 key 清掉不再命中的镜像。 |
| `petcore/config.py` | +16 | `ai_memory_identity_dir` / `ai_memory_feiyu_root` / `ai_memory_feiyu_session`：DEFAULTS、GLOBAL_CONFIG_KEYS、归一化。 |
| `petcore/AGENTS.md` | +8/−4 | 外部记忆只读源扩为四类；partial 不删除；跨源同文去重。 |
| `docs/INTEROP-ARCHITECTURE.md` | +10/−4 | §4 四源、partial、跨源去重。 |
| `docs/PETCORE-LOG.md` / `PETCORE-LOG-INDEX.md` | +93 | L160（语音）、L161（外部源/去重）。 |
| `docs/SETTINGS-CHANGE-GATES.md` | +4/−1 | 外部源总开关与三个新键的准入记录。 |
| `docs/INDEX.md` | +1 | 本报告登记。 |
| `tests/test_petcore_external_memory.py` | +462 | 16 条：identity/feiyu 映射、subject 最新、空/截断/文件变化 partial、feiyu 未知 op 不冻结、会话目录逃逸、来源上限、只读哈希、四源汇总。 |
| `tests/test_petcore_memory_service.py` | +136 | 5 条：同步 worker 接入 identity + feiyu；切换 feiyu 会话清旧镜像。 |
| `tests/test_petcore_interop_memory.py` | +157 | 24 条：跨源同文只渲染一次、first 保底、partial 不删除镜像、来源配额、importance amend、Windows replace 重试。 |
| `tests/test_petcore_memory_view.py` | +43 | 6 条：身份/feiyu 只读徽标、partial 状态。 |
| `tests/test_petcore_ai_memory.py` | +22 | 新键默认值/归一化 round trip；设置页三行保存。 |
| `examples/petcore-mods/ai.chat/mod.json` | 1/1 | 1.4.0 → 1.5.0。 |

**明确没有做**：没有把 `relay_store.append_fact/retract_fact` 搬进 core；没有让
`ai.chat` runtime 读写身份接力目录；没有自动双向同步；没有移植 puls-max 的情绪引擎。

## 三、性能分析

环境：Windows 11 / Python 3.12.14 / PySide6，临时数据根，20 次均值；命令为进程内
`time.perf_counter` 基准（临时 identity 1000 行、feiyu 1000 op、500 条卡）。

| 指标 | 实测 | 归属 |
|---|---:|---|
| `read_identity_facts` 1000 行 | 14.66 ms/次 | 单线程 worker，启动 + 每 10 分钟 |
| `read_feiyu_logs` 1000 op | 13.09 ms/次 | 同上 |
| `render` 500 条（有跨源去重） | 15.11 ms/次 | 每轮聊天一次；top-K 后写 `prompt-<scope>.md` |
| `render` 500 条（关闭去重，同进程） | 13.60 ms/次 | 去重增量 +1.51 ms（+11%） |
| `apply_external` 500 条卡 + 1000 条外部记录（首次） | 84.63 ms/次 | 同步 worker；旧实现逐条重建本地文本集为 1998 ms（24× 慢），现预计算本地集合 |
| `apply_external` 同批第二次（无新增） | 82.34 ms/次 | 同步 worker；仍按 1000 条记录做 origin 查找 |
| `split_sentences` 138 字 | 0.033 ms/次 | 每条回复完成一次 |

- **稳态开销**：语音开关/外部源总开关默认行为不变；关闭时只多一次布尔判断。外部源
  同步只在启动 +3s、之后每 10 分钟由现有 `memory-sync` 单线程执行；无新增常驻线程。
- **新增路径成本**：每轮聊天 `render` 多一次 O(n) `normalize_text` 去重（500 条
  +1.51ms，29 条真实卡规模远小于此）；每句朗读一次 `PrecacheWorker.submit`（覆盖
  未开始任务，不建线程）。
- **系统调用/网络/磁盘**：identity/feiyu 读取各一次 `read_text`；不建目录、不写源文件；
  `feiyu` 只读 `log.jsonl`，不解析 `index/profiles` 派生文件；SQLite 仍是 `mode=ro`。
- **线程**：新增仅语音每 slot 一个 daemon `PrecacheWorker`（首次朗读时创建，`stop()`
  关闭），没有每句线程；同步仍用现有单线程 executor。
- **内存**：identity/feiyu 记录是普通 dict，1000 行量级；`render` 去重只加一个
  `set[str]`，不复制正文；无累积缓存。
- **并发修复**：Windows 上 `os.replace` 目标被记忆页读取时会 `WinError 5`；改为 6 次
  20–120ms 退避重试，测试从稳定失败转为通过。真实权限/磁盘错误仍抛出。

## 四、实机运行记录

- **聚焦测试**（源码树，Windows 真机，非 CI）：

```
QT_QPA_PLATFORM=offscreen pytest -q \
  tests/test_petcore_ai_mod.py tests/test_petcore_ai_memory.py \
  tests/test_petcore_memory_service.py tests/test_petcore_external_memory.py \
  tests/test_petcore_interop_memory.py tests/test_petcore_memory_view.py \
  tests/test_petcore_chat_ui.py tests/test_petcore_speech.py \
  tests/test_petcore_ai_reply_voice.py
→ 206 passed
```

- **语音红→绿**：修前同一文件 `13 failed / 9 passed`（回调、双 slot、预合成有界、
  stop、英文断句均红）；修后 `23 passed`。
- **真实 `dsh.memory` 只读对账**：

```
read_dsh_facts(%APPDATA%/dsh-pet-core) → status=ok, 2 条, scope=dsh
apply_external → {'added': 2, 'updated': 0, 'removed': 0, 'count': 2}
render("pet") 可见两条；facts.jsonl 前后 sha256 不变（True）
```

- **本机缺失源**：`~/.dsh/identity-memory` → `not_found`（0 条，不建目录）；
  `~/.dsh/feiyu-memory` → `not_found`（0 条，不建目录）。
- **同步接入**：临时 identity + feiyu 库经 `MemoryService.sync_external(force=True)`
  后，`external_status` 同时出现 `identity` / `feiyu:__default__`，`render_prompt`
  能检索到两源事实（`test_external_sync_includes_identity_and_feiyu`）。
- **全量 petcore**：`QT_QPA_PLATFORM=offscreen pytest -q tests/test_petcore_*.py`
  → **3029 passed, 1 skipped in 355.67s**（最终工作区，含本轮全部新增回归）。
- `ruff check` 全部改动 Python 文件 → All checks passed。

**未验证 / 边界**

- 本机没有真实身份接力库与 feiyu 库；解析器只按 PR 字段/feiyu `log.jsonl` 格式与
  合成 1000 op 覆盖。真实 DSH 安装后需复核 `supersede/purge`、多会话与表列差异。
- 本机未配置真实语音后端，句间停顿/音质未实机听感验收；SAC 仍拦新构建。
- 未在安装包内跑；新三行设置行未做窄窗截图目视复核。
- 多角色同步仍按每角色读一遍外部源（N 角色 × M 源），本轮未加同轮缓存；单源
  14ms 量级，等真实多角色库再决定。
- 不做自动双向写回；若将来要做，需上游共享锁协议，否则 DSH 侧后写覆盖风险仍在。

## 五、回滚

- 语音：`git revert` 本提交即可；`ai_voice_reply` 开关仍默认关，行为可立即退回。
- 外部源：新键默认 `""`/`"__default__"`；关闭 `ai_memory_import_dsh` 即停止新增
  镜像，已有镜像保留；源文件从未被写，回滚不需要数据迁移。
