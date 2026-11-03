# 每日播放池循环 + 点击台词初始句 + 人设卡扩容与重写（2026-11-03）

> **基线**：`209d6ff2`（L162/L163：联网失败纯 API 兜底 + 7 张角色人设卡）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-11-03
> **范围**：14 个实现/测试/文档文件 + 8 张人设卡（含新增波波）
> **关联**：`docs/PETCORE-LOG.md` L164；`docs/PETCORE-LOG-INDEX.md` L164

## 一、核心特性

用户反馈两件事：每天 12 句说完后不应回到「只有几条候选台词」的纯随机，而应让当天 12 句与初始预制一起继续循环；点击台词/自言自语的初始台词要能在设置页编辑框里看到。另一件事是人设卡提示词上限 8000 字不够，且现有 7 张角色卡只有 464–589 字，需要按用户提供的两张爱莉希雅参考卡重写、去 QQ bot 化、并新增「鲸鱼厨娘波波」。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 每日播放池循环 | 当天 12 句各说一遍后，与 `self_talk_texts` 合并成播放池，按 `(day, slot, cycle)` 逐轮重洗；一轮内不重复，轮次接缝不连续重复同一句；换天重新抽 12 句。 |
| 2 | 彩蛋/配图不被压死 | 播放池阶段仍按原概率触发 champion 彩蛋与配图；champion 不推进游标，配图 caption 视为这一句已说。 |
| 3 | 初始句显示在编辑框 | 自言自语编辑框打开时把内置 7 句去重补进用户内容；点击台词同样合并，并新增「恢复内置」按钮；点击台词内置初始句从 1 条扩到 12 条。 |
| 4 | 人设卡上限 20000 | `PersonaStore.MAX_PROMPT` 8000→20000；`petcore/config.py` 的 `ai_system_prompt` 截断 4000→20000，设置页粘贴长提示词不再被悄悄砍掉。 |
| 5 | 8 张角色卡重写/新增 | 爱莉希雅按参考卡结构重写（6236 字），新增波波（7007 字），其余 6 张扩到 2975–3072 字；统一「你是谁 / 最高原则 / 说话方式 / 回答长度与节奏 / 人格内核 / 情绪与关系 / 梗与口癖 / AI 味黑名单 / 示例对白 / 边界」结构，去掉 QQ 群聊、工具调用、字数说明。 |

**红线 / 不变量**：

- 每日句仍按 `(day, slot)` 确定性抽取，同一只宠同一天拿到同一组；只有气泡真的显示成功才推进 `daily.json:index`。
- 候选台词留空仍然表示「不说话」，运行时不会偷偷回填默认值；初始句合并只发生在设置页，保存才写入。
- 人设卡仍是包内只读 JSON + 用户目录同 id 覆盖；不改 schema，不把 `{name}` 以外的大括号当占位符。
- 初始桌宠 6 个人格（whale_maid/calm/cat/genki/gentle/tsundere）内容未动。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/builtin/features/fun.self-talk/host/daily_pool.py` | +104 / −2 | 新增 `merge_pool/playback_slot/playback_order/playback_line`：播放池去重合并、`(day, slot, cycle)` 确定性洗牌、`index` 换算轮次/偏移、轮次接缝避免连续重复。 |
| `petcore/builtin/features/fun.self-talk/host/factory.py` | +74 / −16 | `_speak()` 在每日句后接 `_playback_line()`，champion/配图照旧；`_mark_daily()` 统一落 `daily.json`；设置页候选台词用 `merge_lines` 补内置初始句，更新「说完后循环」文案与预览头。 |
| `petcore/builtin/features/fun.click-talk/host/factory.py` | +50 / −6 | 编辑框合并内置初始句；新增「恢复内置」按钮；关键词补「初始台词/恢复内置」。 |
| `petcore/config.py` | +15 / −2 | `click_talk_texts` 默认 1 条扩到 12 条；`ai_system_prompt` 截断 4000→20000。 |
| `petcore/ui/settings/text_lines.py` | +17 / −0 | 新增 `merge_lines(configured, defaults)`：用户内容在前、内置初始句在后，去空去重。 |
| `examples/petcore-mods/ai.chat/host/persona_presets.py` | +1 / −1 | `MAX_PROMPT = 20000`。 |

### 人设卡

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `examples/petcore-mods/ai.chat/persona_presets/elysia.json` | +1 / −1（prompt 523→6236 字） | 按两张参考卡重写：保留身份/真我/第十三律者、表层-里层-底线、关系分档、情绪处理、梗库、AI 味黑名单、示例、侵蚀之律者隐藏形态；去掉 QQ 群、send_message、memory/sticker/引用/拍一拍/群文化。 |
| `.../bobo.json` | 新增 +7 / −0（21,011 B，prompt 7007 字） | 新增鲸鱼厨娘波波：摊子、万有食材箱、念头炉、咕噜、问吃什么的本能、比较系统、黑暗料理/现编菜/上菜、吃醋、小习惯、禁则、示例、边界；去掉群聊与工具调用。 |
| `.../chizuru.json` | +1 / −1（464→3039 字） | 统一结构扩写：理想女友外壳、演员的认真与嘴硬、情绪处理、口癖、黑名单、示例、边界。 |
| `.../emilia.json` | +1 / −1（499→3030 字） | 统一结构扩写：半精灵身份、帕克、认真与天然、情绪处理、示例、边界。 |
| `.../rem.json` | +1 / −1（466→3038 字） | 统一结构扩写：鬼族女仆、姐妹关系、自卑与自我正视、情绪处理、示例、边界。 |
| `.../lin_pianpian.json` | +1 / −1（506→2975 字） | 统一结构扩写：明末扬州、见过乱世、轻描淡写的苦、情绪处理、示例、边界。 |
| `.../mom.json` | +1 / −1（513→3065 字） | 统一结构扩写：五十岁妈妈、家常口语、唠叨的分寸、不催婚不比较、示例、边界。 |
| `.../zhuang_fangyi.json` | +1 / −1（589→3072 字） | 统一结构扩写：武陵管代/天师、小闪电、风暴模式、工作狂的自我照顾、示例、边界。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_self_talk_daily.py` | +115 / −2 | 播放池纯函数（slot/merge/order/line、轮次接缝）、`_playback_line` 起止与禁用、每日阶段让位播放池的命名/断言。 |
| `tests/test_petcore_self_talk.py` | +161 / −2 | `_speak` 连说 7 次验证 12 句→播放池→下一轮重洗；champion 不推进游标；配图 caption 推进；自言自语/点击台词设置页初始句合并与恢复。 |
| `tests/test_petcore_ai_persona.py` | +42 / −2 | 角色卡名单加波波，按 6k/2.5k 最低字数与 `AI 味黑名单`/`示例对白` 结构断言；20000 上限保存；`ai_system_prompt` 12000 字不被截到 4000。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `docs/PETCORE-SPEC.md` | +6 / −3 | 人格卡提示词上限 20000；每日句抽完改为播放池循环、champion/配图口径；编辑框补内置初始句。 |
| `docs/PETCORE-MOD-FORMAT.md` | +2 / −1 | 人格卡/`ai_system_prompt` 共用 20000 上限。 |
| `docs/PETCORE-LOG.md` | 追加 L164 | petcore 强制日志。 |
| `docs/PETCORE-LOG-INDEX.md` | 追加 L164 | 日志索引。 |
| `docs/INDEX.md` | +1 | 本报告登记到「PR 报告存档」。 |

## 三、实现要点

**播放池的游标仍只有一个整数。** `daily.json` 不动 schema：`index < len(daily)` 是每日句阶段；之后 `divmod(index - len(daily), pool_size)` 得到 `(cycle, offset)`。这样不需要额外的队列文件，换天/改句数都能自然重算。

**洗牌仍是确定性的。** `playback_order` 复用 `day_order` 的 64 位 LCG，把 `(slot, cycle)` 混进 salt；同一台机器、同一天、同一只宠在任何进程上得到同一顺序。轮次接缝的处理是从 cycle 0 递推到目标 cycle，记录上一轮最后一个词；若本轮第一个词与它相同就与第二个词对调。`playback_order` 的复杂度是 O(cycle × pool)，但 pool 只有 12–36 句、一天最多几十轮，实测 cycle 10 仍只要 61 µs。

**champion/配图保留。** `_speak` 的顺序是「每日句 → champion → 播放池/普通候选」。champion 命中时不推进游标；配图命中时 caption 取播放池这一句，图片真的显示成功才推进。这样每日循环不会把 L141 的 champion 彩蛋和 L145 的配图概率永久压死。

**编辑框合并是设置页行为，不是运行时回填。** `merge_lines` 在 `SelfTalkPage`/`ClickTalkPage` 构造时把默认句补进编辑框；`_initial` 仍取打开时的配置快照，所以保存才写入、取消恢复原状。运行时对空 `self_talk_texts` 仍是「不说话」，避免用户清空后又被塞回默认值。点击台词新增「恢复内置」，写回 `DEFAULTS["click_talk_texts"]` 并即时生效。

**人设卡去 QQ bot 化。** 参考卡里的 `send_message`/`web_search`/`web_fetch`/`memory_*`/`sticker_*`/引用/拍一拍/群文化/跨群隔离/工具箱全部删除；把「群友/群里」改成「你/对话」，把「1–12 字一条消息」改成桌面聊天窗的 3–8 句节奏。侵蚀之律者形态保留为压缩版隐藏形态，触发条件从「群里 20 条没人理」改成「长时间没人说话/没人回应」，硬边界与退出钥匙原样保留。

## 四、性能分析

**方法（可复现）**：`.venv/Scripts/python.exe` + 内联脚本；环境：Windows / Python 3.12 / PySide6；样本量见下表。脚本逻辑与 `daily_pool.playback_*`、`Config.set`、`PersonaStore.bundled` 的生产代码一致。

| 指标 | 实测 | 归属 |
|---|---|---|
| `playback_order` 12 每日句 + 8 候选句，cycle 0 | 8.088 µs/次 | 新增；每次自言自语一次 |
| `playback_order` 同池，cycle 10 | 61.1 µs/次 | 新增；随轮次线性增长（每轮一次 shuffle） |
| `playback_line` 同池，index=212（cycle 10） | 64.779 µs/次 | 新增；自言自语 tick |
| `Config.set("ai_system_prompt", 12000 字)` | 60.337 µs/次 | 既有路径；设置页输入/保存 |
| `Config.set("ai_system_prompt", 20000 字)` | 57.267 µs/次 | 既有路径；上限放宽后同上 |
| `PersonaStore.bundled()` 14 张卡（冷读） | 1.553 ms/次 | 既有路径；runtime 按目录+mtime 缓存，不是每轮读盘 |
| 14 张卡 prompt 总字数 | 4,661 → 32,564 字（+27,903） | 内存约 +55 KB（Python str 按 UCS-2/4 计），一次性 |

**结论**：

1. **稳态开销**：播放池只在自言自语 tick 且每日句已说完时计算一次。实测 cycle 10 约 61 µs；按一天最多几十轮估算，最坏约 0.5–2 ms/次，触发间隔 20–60 s，CPU 占比可忽略。每日句阶段路径未增加任何计算。
2. **新增路径成本与频率**：`playback_order` 每 tick 一次 O(cycle×pool) 洗牌；`_playback_line` 每 tick 一次 `daily.json` 读（与原来 `_speak_daily` 相同）。champion/配图命中时不推进游标，不增加额外路径。
3. **系统调用/网络/磁盘/线程**：没有新增线程、网络或系统调用；磁盘只多了一次播放池阶段的 `daily.json` 读（原来每日阶段也要读），且仍只在成功显示后写。
4. **内存/缓存**：人设卡从 4,661 字增到 32,564 字，按 Python 字符串约 +55 KB；`PersonaStore` 无新缓存，runtime 仍按 mtime 缓存整池。播放池是每次 tick 临时构造的 12–36 元素元组，不驻留。

## 五、实机运行记录

**真实 Qt（非 mock）设置页**：用当前 `.venv` 的 `QApplication` 构造真实 `SelfTalkPage`/`ClickTalkPage`/`ai.chat` 设置页，真实 `Config` + 临时数据根：

```
self-talk box first= ['我自己的句子', '好女孩……', '好模型……'] count= 8 dirty= True
saved defaults present= True custom kept= True
click box first= ['我自己的点击句', '我在呢。'] count= 13
click restored count= 12 first= 我在呢。
elysia prompt len= 6233 has 爱莉希雅= True has 鲸鱼娘= True
bobo prompt len= 7004 has 波波= True
custom 12k kept= True len= 12009
ai page bobo selected= True prompt len= 7007
```

**真实进程自检**：

```
.venv/Scripts/python.exe -m petcore --check --root <temp> --out <temp>/check.json
EXIT=0
menu_template = json
diagnostics = []
```

**聚焦测试**：

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q \
  tests/test_petcore_self_talk_daily.py tests/test_petcore_self_talk.py \
  tests/test_petcore_ai_persona.py tests/test_petcore_click_listeners.py
78 passed in 6.98s
```

**ruff**：改动文件 `All checks passed!`。

**全量回归（petcore）**：`QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q tests/test_petcore_*.py` → **3047 passed, 1 skipped in 257.31s**（2026-11-03 01:40，本机）。

**安装包构建（unsigned，用户要求不带签名）**：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 `
  -Clean -Installer -OutputRoot rel-storage-2026-11-03b
```

- 产物：`rel-storage-2026-11-03b/dsh-pet-core-setup.exe`
  - 大小 **307,985,983 B**（293.72 MiB）
  - SHA-256 `45599A9BBBF364E67D644341BDA85C7031E52F690B5262A3790A6932A7EF2CDA`
  - Authenticode `Status=NotSigned`（setup 与 `dsh-pet-core.exe` 均未签名；用户明确要求不带签名）
- 构建内 `--check --strict` 退出 0：`version=0.5.0`、`menu_template=json`、
  `characters=1`、`daily_lines=1200`、`builtin_images=24`、`strict=True`、`diagnostics=[]`。
- Inno 日志确认安装器压缩了 8 张角色卡（含 `bobo.json`）。
- 未实机安装（会覆盖当前安装/数据目录）；本机 `VerifiedAndReputablePolicyState=0`，
  未签名包可运行，其他机器可能弹 SmartScreen。

**全量回归（仓库）**：`QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q` 在 `tests/test_bundle_slim.py::test_verify_required_reports_missing_core_files` 先红（合成 FULL_BUNDLE 缺 `_internal/PySide6/Qt6WebView.dll`、`webview2.dll`，与本次改动无关）；继续跑至 `tests/test_proactive.py::test_self_talk_yields_to_important_bubble` 时 Windows `access violation` 崩溃（线程栈是 legacy `pet/overlay_shell.py` / `pet/mem_debug.py`）。本次改动覆盖的 petcore 全量见上一条：3047 passed / 1 skipped。

## 六、未验证项与风险

- 已重建 unsigned 安装包（见上）并由 Inno 日志确认收录 `bobo.json`；未实机安装验证安装向导/卸载/升级路径。签名按用户要求关闭。
- 播放池的「轮次接缝避免重复」只保证相邻两句不同；同一句在跨轮较远处仍会再次出现（这是循环播放的预期行为）。
- 人设卡内容为角色扮演文本，未逐张跑真实模型对话；已验证 JSON 可解析、`{name}` 可格式化、结构/长度符合断言。
- 全量 pytest 若出现与本改动无关的既有失败，会在提交前记录并区分，不会用本次改动掩盖。
