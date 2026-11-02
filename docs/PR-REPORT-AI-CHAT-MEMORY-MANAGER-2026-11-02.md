# AI 对话：记忆管理页、只增不改修复与 DSH 外部记忆互通（2026-11-02）

> **基线**：`feat/petcore-vpet-architecture` 工作区（`d098a5fe`）
> **分支**：`feat/petcore-vpet-architecture`（本地工作区，未提交）
> **范围**：记忆编辑/删除/整理、外部记忆只读镜像、对话窗「记忆」页
> **关联**：`docs/PETCORE-LOG.md` L159；检索/气泡部分见
> `PR-REPORT-AI-CHAT-MEMORY-RECALL-2026-11-02.md`

## 一、核心特性

1. **记忆可改、不再只增**：`card.json` 的条目新增 `updated_at/superseded_by/origin`；
   新增单条编辑、删除（留归档可恢复）、置顶、恢复；同义事实跨天合并，带
   「改成/改为/更正/记错/不再/不是…而是」且**最长公共子串 ≥4 且覆盖旧文本 60%** 的新事实
   会把旧事实标为 `superseded_by`；`tidy()` 只做这两类保守操作，可一键整理、可恢复。
2. **助手自述不再污染记忆**：`append_message(role="assistant")` 写 `kind="reply"`，渲染
   永不注入；旧数据用会话原文扫描一次性标记（`migration-replies.json` 幂等）。
3. **记忆页**：对话窗标题栏新增「记忆」页（`chat_view`），可搜索/按范围筛选/新增/编辑/
   置顶/删除/恢复/整理/立即同步，并可展开「本轮注入预览」看见模型实际收到的记忆块。
4. **DSH 记忆互通**：只读镜像本机 `dsh.memory` 的 `<data_root>/memory/facts.jsonl` 与
   meow-memory 的 `<workspace>/.dsh-meow/memory.db`；AI/DSH 主人私聊通道默认互见，QQ 隔离。
   现有 `/pet/prompt context.memory` 注入保持不变；外部源只读，删除镜像=静音，不写回。

**红线 / 不变量**：单写者仍是 core `MemoryService`；外部 DB/JSONL 不建表、不写行、不改
schema；`archive.jsonl` 永不进提示词；第三方 `dsh.memory` 扩展不改；QQ 群聊不泄漏私聊事实。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `petcore/interop/memory.py` | +706 / −59（与 PR A 合并） | 新增字段、RLock、编辑/删除/恢复/整理/外部镜像 API；`ingest` 重复合并 + 更正覆盖；`normalize_text` 改 `str.translate`。 |
| `petcore/interop/external_memory.py` | +279（新） | dsh.memory facts + meow-memory SQLite 只读读取；`assistant_texts()` 会话扫描。 |
| `petcore/ui/memory_service.py` | +244 / −5（与 PR A 合并） | 外部同步 worker（启动 +3s / 每 10 分钟）、`ai_memory_import_dsh`、entries/update/delete/restore/tidy/sync/status；apply 在工作线程，GUI 只写 marker。 |
| `petcore/contracts/host.py` | +34 / −0 | `MemoryPort` 增量方法；`remember(..., pinned=False)`。 |
| `examples/petcore-mods/ai.chat/host/memory_view.py` | +719（新） | 记忆页 QWidget：列表/搜索/筛选/编辑对话框/预览/外部状态。 |
| `examples/petcore-mods/ai.chat/host/__init__.py` | +13 / −0 | 注册 `chat_view` 与 `view_factories`（懒导入 Qt）。 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +52 / −4 | `ai_memory_import_dsh` 行；「管理记忆…」按钮；`_KEYS` 补两键。 |
| `petcore/config.py` | +10 / −0 | `ai_memory_import_dsh` 全局键 + 归一化。 |
| `petcore/ui/chat/window.py` | +18 / −8 | `pet_slot` 参数 + `set_pet_slot` 注入。 |
| `petcore/ui/chat/controller.py` | +1 / −0 | 建窗时传 `state.slot`。 |
| `petcore/ui/chat/styles.py` | +60 / −0 | 记忆页浅/深 QSS。 |
| `docs/INTEROP-ARCHITECTURE.md` | +22 / −6 | §4 记忆矩阵/渲染/外部镜像/未验证项。 |
| `docs/SETTINGS-CHANGE-GATES.md` | +2 / −0 | 两个新键的门禁表。 |
| `petcore/AGENTS.md` | +19 / −3 | 记忆单写者、相关度渲染、可见性矩阵、外部只读、chat_view 约定。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_petcore_external_memory.py` | +172（新） | facts 跳过 superseded/importance 映射；meow 只读 + archived 过滤；apply 更新/删除/静音；会话原文标记 reply。 |
| `tests/test_petcore_memory_view.py` | +255（新） | 列表/搜索/范围筛选/外部只读/置顶/恢复/删除/整理/同步/注入预览。 |
| `tests/test_petcore_interop_memory.py` | +252 / −0（与 PR A 合并） | 重复合并、更正覆盖、误合并防护、编辑/删除/恢复、tidy 幂等、pinned。 |
| `tests/test_petcore_chat_ui.py` | +72 / −0（与 PR A 合并） | `chat_view` 收到 `pet_slot`。 |
| `tests/test_petcore_ai_mod.py` | +1 / −0 | 贡献列表新增 `chat_view`。 |

### 未改动（故意）

- 第三方用户扩展 `%APPDATA%/dsh-pet-core/mods/dsh.memory` 不改；只读它的 `facts.jsonl`。
- `dsh-market-expand` 不接入：它是插件市场更新/回滚工具，不读写记忆。
- 不引入 Ollama/向量库；`dsh-memory-semantic` 的「只读上游 + 混合检索 + 可测量降级」用
  本地 bigram/新近度/重要度落地。

## 三、实现要点

- **单写者 + 锁**：`MemoryStore` 用 `RLock`；`save_card`/`ingest`/编辑/整理/外部 apply
  串行，`render`/`entries`/`stats`/`external_status` 只读不加锁（`os.replace` 原子写，
  读永远拿到旧卡或新卡）。外部 apply 在 worker 内完成，GUI `render` 不被 364ms 的批量写
  拖住；GUI 只写幂等 marker。
- **重复/更正**：`normalize_text` 用 `str.translate` 去空白/标点；`ingest` 先查同文本
  （跨天）→ touch `recall_count`/取更高 importance；再对带更正词的 owner 消息做
  「最长公共子串 ≥4 且覆盖旧文本 60%」的覆盖判定（只比较前 80 个规范化字符）。
  `tidy()` 只处理本地条目（`origin` 为空），外部镜像不参与 O(n²) 比较。
- **外部镜像**：`external.json` 记录每个来源的状态/最后同步/静音 origin；`apply_external`
  按 origin 更新/删除，源里消失的镜像归档；`origin` 同时是 UI 只读徽标和恢复依据。
- **记忆页**：`chat_view` 懒建；`ChatWindow` 把 `pet_slot` 注入视图；设置页只保留两个
  开关的唯一所有权行，记忆页只显示状态与执行命令。

## 四、性能分析

**方法**：Windows 11 / Python 3.12 / PySide6；临时数据根；500 条卡、20 条新消息；
2000 行合成 meow SQLite；29 条真实卡规模；同机计时。

| 指标 | 实测 | 归属 |
|---|---:|---|
| `render` 500 条 | 13.88 ms/call | 每轮聊天一次 |
| `render` 29 条（真实规模） | 2.40 ms/call | 典型路径 |
| `ingest` 500 条 / 新消息 | 35.77 ms（旧 30.41，+18%） | 每轮提交一次 |
| `tidy` 500 条本地条目 | 174.0 ms（第二次 9.0 ms） | 首轮同步/手动整理一次 |
| meow 2000 行读取 | 11.0 ms | 启动 + 每 10 分钟 worker |
| meow 2000 行 apply | 364.5 ms | worker 内；GUI 只在 `ingest`/`recall` 时可能等锁 |
| 记忆页首次构建 | < 20 ms（1000×760，6 行） | 打开「记忆」页一次 |

- 稳态：聊天每轮多约 5ms（500 条上限）的 `ingest`，相对 API 请求可忽略；典型 29 条约 2ms。
- 新增：一个 `ThreadPoolExecutor(max_workers=1)`，只在启动 + 每 10 分钟触发；无网络；
  外部 SQLite 只读；无常驻线程。
- 内存：外部镜像受 `ai_memory_max_entries`（默认 500）压缩；`tidy` 只比较本地条目。
- 最大绝对成本是一次性 meow 2000 行 apply（364ms）——已移出 GUI 线程；若同步期间用户
  提交消息，`ingest` 可能等这把 RLock，最坏约 0.36s；下一次同步可复测。

## 五、实机运行记录

**真实 `dsh.memory` 事实（本机）**：

```
$ .venv/Scripts/python.exe - <<'PY'
from pathlib import Path
from petcore.interop.external_memory import read_dsh_facts
from petcore.interop.memory import MemoryStore
src = read_dsh_facts(Path(r"%APPDATA%/dsh-pet-core"))
print(src.status, len(src.records), src.path)
store = MemoryStore(Path(tempfile.mkdtemp())/"memory", "shenshen")
print(store.apply_external(src.key, list(src.records), status=src.status, label=src.label, path=src.path))
print(store.render("pet", max_chars=2000))
PY
```

真实输出：

```
status ok 2 C:\Users\26102\AppData\Roaming\dsh-pet-core\memory\facts.jsonl
 - 用户叫张** | importance 100 | origin dsh-facts:f1793179665510
 - 主人喜欢鲸鱼 | importance 100 | origin dsh-facts:f1793463673000
apply {'added': 2, 'updated': 0, 'removed': 0, 'count': 2}
长期记忆（供参考；…）：
【和主人】
- 主人喜欢鲸鱼
- 用户叫张**
```

**外部同步线程**（临时根 + facts.jsonl）：

```
started True
status {'sources': [{'key': 'dsh-facts', 'label': '本机 dsh.memory', ... 'count': 1}], 'muted': 0, 'enabled': True}
entries [('用户叫张**', 'dsh-facts:f1')]
markers ['card.json', 'external.json', 'migration-replies.json', 'tidy-v2.json']
```

**真实卡副本（29 条）跑 `tidy()`**：

```
before 29
tidy {'merged': 1, 'superseded': 1, 'entries': 29}
 superseded: 我星期三上课 -> 18695428605c379e
 superseded: 用 pwsh 执行 Remove-Item -LiteralPath E -> 10f388827e79ff78
--- render ---
【桌宠】
- 我星期三不上课，改为星期四上课了
- 我星期几上课啊？
- 呜，我翻了一下记得的东西，里面真的没有主人的上课时间喵……
```

旧事实「我星期三上课」被更正覆盖，渲染 top-3 第 1 条是「星期四上课」；第三条助手自述会在首轮
`mark_replies` 后标为 `reply` 并退出提示词。

**GUI 截图**（真 Windows 平台）：

- `docs/screenshots/ai-chat-memory-2026-11-02/memory-page-1000.png`：6 条记忆、置顶/AI/DSH
  只读/共享/QQ 徽标、DSH 同步 2 条、注入预览按钮。
- `docs/screenshots/ai-chat-memory-2026-11-02/memory-page-620.png`：窄窗无裁切，按钮全可见。

**测试**：

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q \
  tests/test_petcore_interop_memory.py tests/test_petcore_external_memory.py \
  tests/test_petcore_memory_service.py tests/test_petcore_memory_view.py
→ 29 passed
```

全量 petcore（最终工作区）：

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q tests/test_petcore_*.py
→ 2984 passed, 1 skipped in 355.33s
```

`ruff check` 全部改动 Python 文件 → All checks passed。

## 六、未验证 / 边界

- 本机没有 `<workspace>/.dsh-meow/memory.db`；meow-memory 只用合成 3 表/2000 行单测覆盖，
  真实 DSH 工作区装上后需复核（尤其表列差异、WAL 锁）。
- 第三方 `dsh.memory` 扩展不改；只读其 `facts.jsonl`，真实扩展升级后字段若变需要回归。
- 记忆页未在安装包内跑（源码树 + 临时数据根）；打包后的字体/DPR 仍需目视复核。
- `tidy()` 的更正启发式可能误合并相似但不同的事实；被覆盖条目在「已更新」里可恢复，
  仍建议用户先看「本轮注入预览」。
- 外部同步期间 `ingest` 可能等 RLock 最多约 0.36s（2000 行 meow）；典型库远小于此。
- 外部镜像与本地条目共用 `ai_memory_max_entries`（默认 500）；超大 meow 库可能挤掉低分本地
  条目，本轮未做「本地/外部」分区配额，装大库后需观察「整理/已更新」并调高上限。
