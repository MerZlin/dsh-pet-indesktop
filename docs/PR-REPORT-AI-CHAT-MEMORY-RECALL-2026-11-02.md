# AI 对话：用户气泡宽度与跨会话记忆召回（2026-11-02）

> **基线**：`feat/petcore-vpet-architecture` 工作区（`d098a5fe`）
> **分支**：`feat/petcore-vpet-architecture`（本地工作区，未提交）
> **范围**：气泡宽度 + 记忆渲染/可见性（本报告只覆盖 PR A 的两部分；记忆管理与外部同步见
> `PR-REPORT-AI-CHAT-MEMORY-MANAGER-2026-11-02.md`）
> **关联**：`docs/PETCORE-LOG.md` L158

## 一、核心特性

修两个用户可见问题：

1. **用户气泡过窄**：旧实现只设了 `maxWidth`，`QLabel` 的 wordWrap sizeHint 在
   `QSizePolicy.Maximum` 下被压到正文最小宽 120px；1180px 窗口实测用户气泡 144px、
   正文 120px，24 个字被折成 3 行。改为按 `QFontMetrics` 量最长行显式定宽，短句保持紧凑、
   长句铺到可用宽的 86%。
2. **跨会话记忆「记了却取不到」**：真实卡 `card.json` 有 29 条，包含
   「我星期三不上课，改为星期四上课了」；但 `ai.chat` 每轮只传 `limit=3`，旧 `render`
   按「最初/锚点/和主人/桌宠/…/最近」固定分区选满 3 条就停，普通用户事实重要度 58
   落在第 4 区「桌宠」，永远进不了前 3 条；`_sections` 还会按 importance 重排，把查询
   相关度排序整个覆盖。DSH 路径不传 `limit`，所以表现成「DSH 记得、AI 对话不记得」。

**红线 / 不变量**：`inbox.jsonl` 仍是跨进程唯一写入口；`card.json` 仍由
`MemoryService` 单写者维护；`archive.jsonl` 永不进提示词；旧 `feature-data` 记忆只做
一次性导入；记忆预算仍按 2200/2600/2000 字裁剪。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `petcore/ui/chat/widgets.py` | +22 / −1 | 新增 `MessageBubble.preferred_width()`；用户正文最小宽 120→0，让气泡按实测文本宽或上限定宽。 |
| `petcore/ui/chat/window.py` | +18 / −8 | 消息/输入区边距 76→44（紧凑 12）；user 上限 66%→86%；`_update_bubble_widths` 改 `setFixedWidth(preferred_width)`；`ChatWindow(pet_slot=)` 与视图 `set_pet_slot` 注入（A 阶段先落，B 阶段使用）。 |
| `petcore/interop/memory.py` | +706 / −59（与 PR B 合并） | `render/recall` 改相关度优先 top-K；新增 `_visible_scopes`、`_entry_score`、`_select_for_render`、`_compose_render`；问句降权；排除 `kind=reply`/`superseded_by`；渲染头加「与当前对话冲突以当前为准」。 |
| `petcore/ui/memory_service.py` | +244 / −5（与 PR B 合并） | assistant 消息写 `kind="reply"`；`render_prompt/recall` 传 `ai_memory_share_channels`。 |
| `petcore/config.py` | +10 / −0 | 新增全局键 `ai_memory_share_channels`（默认 `True`）及归一化。 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +52 / −4 | 新增共享开关行；修正「总预算 800 字」「按创建时间淘汰」两条过期文案。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_petcore_interop_memory.py` | +252 / −0（与 PR B 合并） | 真实卡形状 top-K、AI/DSH 互见/QQ 隔离、`share_channels=False`、reply/superseded 排除、重复合并、更正覆盖、编辑/删除/恢复、tidy 幂等/误合并防护、pinned 新条目。 |
| `tests/test_petcore_chat_ui.py` | +72 / −0 | 长/短用户气泡宽度、assistant 占满整列、`chat_view` 收到 `pet_slot`。 |
| `tests/test_petcore_ai_memory.py` | +6 / −0 | 设置页新开关与配置写入。 |

### 未改动（故意）

- `sprite_collision`/`pet/` 旧聊天实现不动；截图对应的是 petcore `ChatWindow`。
- 不引入 Ollama/向量检索；本轮只做本地混合检索。

## 三、实现要点

- **定宽而不是只设上限**：`preferred_width()` 取 `min(cap, max(160, natural + 26))`，
  其中 `natural` 是 `QFontMetrics.horizontalAdvance` 的最长一行；assistant 始终占满
  `available`。`setFixedWidth` 同时压住 min/max，避免再次被布局压回 120px。
- **相关度优先**：`_entry_score` = `2.2*字面 + 0.9*30天半衰 + 0.7*重要度 + 0.25*pin +
  0.35*first + 0.03*召回次数`；问句（问号/疑问词）字面相乘 0.35，避免「我星期几上课啊」
  这类问题本身把事实挤掉。`limit` 先按分数取 top-K，再按原分区排版；`first` 在 K≥2 时
  保底一条。预算超限时先丢**已输出**的最低分条目，重排到装得下。
- **可见性矩阵**：`shared` 永远可见；`pet`/`dsh` 默认互见（主人私聊通道）；`qq` 只与
  `shared` 互通。关掉 `ai_memory_share_channels` 退回旧的严格 scope。

## 四、性能分析

**方法**：`C:\Users\26102\AppData\Local\Temp` 下临时卡；Windows 11 / Python 3.12 /
PySide6；500 条条目、100 次 `render`（查询轮换，含空查询），计时含 `prompt-pet.md` 落盘。
旧实现从 `git show HEAD:petcore/interop/memory.py` 动态加载后同卡同查询对比。

| 指标 | 实测 | 归属 |
|---|---:|---|
| 500 条 `render`（新） | 12.626 ms/call | 每轮聊天一次 |
| 500 条 `render`（旧） | 9.667 ms/call | 旧实现 |
| 增量 | +2.96 ms（+31%） | 打分/选择/重排 |
| 29 条真实卡规模 `render` | 2.403 ms/call | 典型路径 |

- 稳态开销：每轮多约 3ms（500 条上限），相对一次 API 请求可忽略；典型 29 条约 2.4ms。
- 新增路径：只在 `render/recall` 内做 O(n) 打分 + 排序；无新增线程、网络、系统调用。
- 内存：候选列表是卡条目的引用列表，不复制正文；`scores` 是 id→float，O(n)。
- 渲染仍每次写 `prompt-<scope>.md`（既有行为），是本基准里最大的固定 IO 成本。

## 五、实机运行记录

**根因现场（真实卡，只读）**：

```
$ .venv/Scripts/python.exe - <<'PY'
from petcore.interop.memory import MemoryStore
s = MemoryStore(r"%APPDATA%/dsh-pet-core/interop/memory", "shenshen")
print(s.render("pet", query="我星期几上课啊", max_chars=2200, max_entries=3))
PY
```

修改前（旧实现）：

```
长期记忆（供参考；不要逐条复述，除非与问题直接相关）：
【锚点】
- 我喜欢鲸鱼
【和主人】
- 我喜欢千早爱音
- 记住，我喜欢千早爱音
```

修改后：

```
长期记忆（供参考；不要逐条复述，除非与问题直接相关；若与当前对话冲突，以当前说法为准）：
【桌宠】
- 我星期三不上课，改为星期四上课了
- 我星期三上课
- 我星期几上课啊？
```

（第二条旧事实会在 PR B 的 `tidy()` 中标为 `superseded_by`。）

**GUI 截图**（真 Windows 平台，非 offscreen）：

- `docs/screenshots/ai-chat-memory-2026-11-02/bubble-1180.png`：用户 24 字消息一行显示；
  修改前同文本为 3 行（正文 120px）。
- `docs/screenshots/ai-chat-memory-2026-11-02/bubble-640.png`：560/640 窄窗下仍为一行，
  assistant 占满整列。

**测试**：

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q \
  tests/test_petcore_interop_memory.py tests/test_petcore_memory_service.py \
  tests/test_petcore_ai_memory.py tests/test_petcore_chat_ui.py
→ 70 passed
```

全量 petcore（最终工作区）：

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q tests/test_petcore_*.py
→ 2984 passed, 1 skipped in 355.33s
```

`ruff check` 改动的 Python 文件 → All checks passed。

## 六、未验证 / 边界

- 未连接真实 API 跑一轮对话；用真实卡离线渲染验证检索结果。
- 问句降权是启发式；极短或纯表情消息仍可能占 top-K。
- 气泡宽度的截图来自真实 Windows 平台 + 源码树，不是安装包内；打包后的字体/DPR 仍需
  一次目视复核。
- 本报告只覆盖 PR A；记忆管理/外部同步的证据见
  `PR-REPORT-AI-CHAT-MEMORY-MANAGER-2026-11-02.md`。
