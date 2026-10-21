# PR 报告：余额气泡金额短语不再被拆开（issue #216 剩余项）

> **基线**：`ba11bdb`（AI 对话全局记忆 #325）
> **分支**：工作区未提交　**日期**：2026-10-21
> **范围**：5 个已改文件 + 2 个新文件（实现 3、测试 3、文档 2；`docs/INDEX.md` 只登记）
> **关联**：issue #216 的剩余断行项。副标题几何已由 [`PR-REPORT-ISSUE-216-BALANCE-SUBTITLE-2026-10-07.md`](PR-REPORT-ISSUE-216-BALANCE-SUBTITLE-2026-10-07.md) 收口，本报告不重开那一部分。

## 一、核心特性

余额气泡正文里的「余额 / 充值 / 赠送」会和紧跟的货币金额一起换行。自己放得下的一段留在同一行；比行宽还长才按字形拆开。缓存、`format_balance` 和接口原文仍是明文。

协作方 2026-10-12 确认前三项副标题缺陷已修好，并明确把断行留下。当前 `_wrap_bubble_lines` 在没有连接符时仍是逐字换行。本机 13px 度量下，生产列宽 244px 会把报告串折成 `赠送 ¥2.3` / `4）`。加上显示层连接符之后，同一宽度折成 `余额 ¥5.99（充值 ¥10.00 / ` / `赠送 ¥2.34）`。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 只保护金额短语 | `format_balance_display` 匹配「标签 + 现有分隔 + 完整货币数字」，用 U+2060 WORD JOINER 把可见字符粘成一段。小数、零、负数、长数字、无空格和冒号分隔都覆盖。对不上的句子原样返回，再调用一次结果不变。 |
| 2 | 换行把整段当原子 | 连接段放得下就整段挪到下一行。超长段按字形回退，至少落下一个字。分页和省略共用这条路径。行首禁则不从放得下的段中间撕字。 |
| 3 | 标记不进预算 | `bubble_max_lines`、`bubble_column_for_text`、`page_dwell_ms` 计数前去掉 WORD JOINER。`normalize_bubble_text` 不把它当成空白折掉。 |

**红线 / 不变量**：`format_balance` 的明文字符串断言不变。没有连接符的正文仍走原来的逐字循环。不新增配置键，不改网络、缓存格式、副标题颜色和窗口几何。

## 二、修改文件说明

`git diff --numstat`（基线 `ba11bdb`，新文件按 `wc -l` 计为纯增）。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/balance.py` | +34 / −0 | `format_balance_display`。正则只认「余额\|充值\|赠送」后接 `¥`/`￥` 金额（可负号、小数、可选冒号）。连接符插在每个可见字符之间，所以空格还在，只是零宽。模块不 import Qt。 |
| `pet/app.py` | +2 / −0 | `_show_balance_payload` 在 `_persona_text` 和副标题之后、`win.show_bubble` 之前调用一次。实时、内存缓存、文件缓存、旧字符串和人设句共用这一次上屏。 |
| `pet/speech_bubble_text.py` | +193 / −9 | 无连接符走原逐字函数。有连接符时按段排，并用同一套度量。省略号碰到放得下的段时整段留下或整段去掉。预算三个函数去掉连接符再数。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_balance.py` | +41 / −0 | 显示格式化、幂等、零与负数、长数字、对不上的句子；去掉连接符回到原可见串。`format_balance` 精确断言未改。 |
| `tests/test_speech_bubble.py` | +149 / −0 | 真实 `QFontMetrics` 推出能拆开「赠送」和「余额 ¥」的宽度；分页、省略、标点、强制换行、超长段、窄于一个字形的预算。 |
| `tests/test_balance_bubble_word_wrapping.py` | +193 / −0 | 真实 `PetSpeechBubble`。字典与旧字符串、人设「口袋还剩 {text}」、重复上屏、源字典不变。`classic_top` / `breath_bubble` × 字号 0.5 / 1.0 / 1.5 / 3.0，加上现有峰谷副标题。 |

### 未改动

`pet/speech_bubble.py`、精灵路由、副标题颜色、持久化设置、网络拉取、窗口几何都没动。呼吸泡画布仍用 `len(normalize_bubble_text(...))` 数字符，WORD JOINER 会算进去，这是既有公式的副作用，见第七节。

## 三、实现要点

WORD JOINER（U+2060）是 Cf、不是空白。`normalize_bubble_text` 的 `\s` 不会吞掉它，所以不用 NBSP。连接符包在已有空格两侧，可见间距不变。

换行只在已有布局函数里做。候选行用现在的 `QFontMetrics.horizontalAdvance`，量之前去掉连接符，宽度与可见字一致。一段自己的 advance 不超过行宽时，整段移到下一行。段比行宽还宽时，按「一个可见字 + 贴在它前面的连接符」回退，空行和丢字都不会出现。行首禁则字符若和整段一起放得下，就把整段拉下来；放不下就在段之前断开，不从段中间抽一个字。

省略把最后一行和剩余正文拼起来再截断。拼回去会把被换行拆开的连接段重新粘上，所以有连接符时不直接把 `elidedText` 切进段中间：整段放得进「行宽 − 省略号」就留下，否则停在段之前。段自己就比行宽长时仍交给 `elidedText`。

## 四、性能分析

工作发生在气泡上屏和重排，不在每一帧。空闲路径不调用格式化，也不调用换行。

**方法（可复现）**：进程内脚本，`QT_QPA_PLATFORM=offscreen`，`/tmp/dsh-pet-venv/bin/python`（Python 3.13.14，PySide6 6.12.0）。`QFont` 像素 13，`QFontMetrics.horizontalAdvance`。每个用例 warmup 50 次，再计 1000 次；计时期间 `gc.disable()`。基线是同一 `paginate_bubble_text` 吃明文（无连接符，走原逐字循环）。修补后吃 `format_balance_display` 的结果。报告串是 `余额 ¥5.99（充值 ¥10.00 / 赠送 ¥2.34）`，advance 249px。窄宽 80px 来自 `horizontalAdvance("赠送 ¥2.34测")`。生产换行预算 `bubble_wrap_width()` = 244px。无标记长段是 340 字的中文段落，两条路径都没有连接符，所以测的是现有逐字循环本身。

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---|---|
| 报告串 @80px，明文 | 中位 112.6 µs，p95 148.7 µs | 既有逐字换行 |
| 报告串 @80px，连接段 | 中位 70.0 µs，p95 81.8 µs | 新增路径，上屏时一次 |
| 报告串 @244px，明文 | 中位 251.8 µs，p95 312.3 µs；折成 `赠送 ¥2.3` / `4）` | 既有 |
| 报告串 @244px，连接段 | 中位 100.2 µs，p95 151.5 µs；`赠送 ¥2.34）` 整段在第二行 | 新增路径 |
| 340 字无标记段 @244px | 中位 3100.8 µs，p95 4290.6 µs | 既有逐字换行 |
| 340 字无标记段 @80px | 中位 1642.6 µs，p95 2121.2 µs | 既有逐字换行 |
| 200 次后 `gc.collect`，tracemalloc 峰值 | 报告串约 2.5 KB；长段 @244px 4520 B，@80px 8144 B。回收后只剩 tracemalloc 自己的约 1.2 KB | 瞬态 |
| 空闲 300ms | traced 0 B，线程仍是 `MainThread`，`lsof` 无 TCP/UDP，fd 数 92 不变 | 既有空闲 |
| 一次分页前后 | fd 92 → 92，网络套接字 0，线程数 1 | 新增路径无 I/O |

**结论**：

1. 稳态空闲没有新增开销。换行不在帧循环里。
2. 余额上屏多一次格式化加一次连接段换行。报告串在 244px 上中位 100 µs，低于原来的逐字 252 µs（少了很多次 `horizontalAdvance`）。触发频率是用户看余额或缓存命中后上屏，不是每帧。
3. 没有新的系统调用、网络、磁盘或线程。`lsof -nP` 在空闲和一次分页之后都没有 TCP/UDP。
4. 分页返回的行是瞬态分配。200 次之后回收，产品代码没有留下成块的持久内存。连接符只活在这一次交给气泡的字符串上，不写入缓存。

## 五、实机运行记录

环境：macOS 27.2 arm64，Python 3.13.14，PySide6 6.12.0。Qt 平台探针：`cocoa` 能起来，主屏 `1800×1169`，设备像素比 2.0。字体：PingFang SC、Hiragino Sans GB、Songti SC、Heiti SC、Arial Unicode MS 都在；Microsoft YaHei 与字体族 “Sans Serif” 不在。气泡默认字体落到 `.AppleSystemUIFont`，Qt 打出 “Replace uses of missing font family Sans Serif”。WORD JOINER 的 `horizontalAdvance` 为 0。

**根因**：同一套 13px 度量、明文、生产预算 244px：

```text
['余额 ¥5.99（充值 ¥10.00 / 赠送 ¥2.3', '4）']
```

连接段之后：

```text
['余额 ¥5.99（充值 ¥10.00 / ', '赠送 ¥2.34）']
```

窄宽 80px 明文把 `赠送 ¥2.34` 折成 `赠送 ¥2.3` / `4）`；连接段保持 `赠送 ¥2.34）` 在同一行。

**用户可见**：`QT_QPA_PLATFORM=cocoa`，真实 `PetSpeechBubble`，人设「口袋还剩 {text}」，余额 `¥12.34 / 充值 ¥10.00 / 赠送 ¥2.34`，峰谷副标题开着色。抓取后分别叠到 `#1b1b1f` 和 `#f7f4ee`。

- 原生 DPR 2.0，`classic_top`：两行 `口袋还剩 余额 ¥12.34（充值 ¥10.00 / ` 与 `赠送 ¥2.34）`。三段金额都在一行里。行宽 222≤226、78≤226。副标题一行，底边 64 ≤ 轮廓 70。深色和浅色底上正文与红/绿副标题都看得见。
- 同一屏 `breath_bubble`：三行，三段金额各自完整。副标题两行，底边 149 ≤ 轮廓 168。
- `QT_SCALE_FACTOR=1.75` 再开一个 cocoa 进程：平台报告设备像素比 3.5、几何 `1029×668`。经典泡与呼吸泡的金额段仍不拆，副标题底边仍在轮廓内（经典 64≤70，呼吸 149≤168）。

**边界**：字典载荷在上屏前后逐字段相同，正文里没有 WORD JOINER；第二次上屏字符串与第一次相同；没有 `info` 的旧字符串不触发余额动画。`QT_SCALE_FACTOR=1.75` 的 offscreen 虚拟屏只有 `457×457`，`test_bubble_tail_computed_for_target_position_not_current` 把两次锚点都夹到同一点 `(335,410)`，滑动动画因此不起。这是夹取，不是断行回归；同一进程里金额段用例是绿的。

**未验证**：没有 Windows，没有 Microsoft YaHei，也没有 Windows 上的 DPR 1.75。上面的 cocoa 与 offscreen 记录不能当成那台机器的证据。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `/tmp/dsh-pet-venv/bin/python -m ruff check pet/ tests/` | All checks passed（实现后，以及满载三连跑之后再跑一次） |
| 改产品代码之前 | 同一组聚焦用例 | 10 failed / 67 passed（缺 `format_balance_display`，逐字换行拆开金额段，预算把连接符算进字数） |
| 聚焦 | `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_balance.py tests/test_speech_bubble.py tests/test_balance_bubble_word_wrapping.py tests/test_bubble_subtitle_budget.py tests/test_bubble_text_scale.py tests/test_app_lazy_imports.py tests/test_sprite_bubble.py` | 125 passed |
| 聚焦 ×1.75 | 同一命令，新进程 `QT_SCALE_FACTOR=1.75` | 124 passed，1 failed：`test_bubble_tail_computed_for_target_position_not_current`（见第五节的 457×457 夹取） |
| 受影响时序族满载 3 遍 | 16 个忙循环占住 CPU，连续三遍跑 `test_balance_bubble_word_wrapping.py`、`test_speech_bubble.py`、`test_sprite_bubble.py`、`test_bubble_subtitle_budget.py`、`test_bubble_text_scale.py` | 三遍都是 102 passed，耗时 6.2s / 4.8s / 5.3s |
| 全量 | 单进程跑到约 26% 时被会话超时杀掉。补上 `keyring` 与 `imageio-ffmpeg` 后用 `pytest -n 8 --dist loadfile` 跑完：`8687 passed, 94 skipped, 53 failed`，146.70s。失败集中在没装 `edge-tts` 的 `test_voice_chime_service.py`（单进程复现日志「请运行 pip install edge-tts」）、缺 `omegaconf` 的一条 OCR 用例，以及 `test_island_bevel_fade.py::test_sample_value_change_never_fades`（offscreen 取样 36 &lt; 60，单进程同样红）。没有余额或气泡换行用例失败。 | 见左 |
| 先前环境红的复测 | 装上 `keyring` 与 `imageio-ffmpeg` 后单进程重跑动画衔接、keyring 子类、两条 frameseq 转换 | 4 passed |

## 七、已知限制与后续

- 不做任意中文分词。只有「余额 / 充值 / 赠送 + 货币金额」被粘住。
- 呼吸泡画布宽度仍按 `len(normalize_bubble_text)` 增长。示例上屏串可见 36 字、带连接符 59 字，画布会比明文略宽。`pet/speech_bubble.py` 不在本次范围。文字大小 3.0 的呼吸泡正文盒高 113px，三行行距 46px 共 138px，第三行在垂直居中后与盒子相交但会被裁掉一截。行宽仍然装得下每一行，副标题预算仍然满足。经典泡按行数定高，行槽整段在盒内。
- Windows、微软雅黑、以及那台机器上的 DPR 1.75 没有实机。

## 八、风险与回滚

影响面是余额气泡这一次上屏的字符串，以及任何已经带有 U+2060 的气泡正文。没有新配置键，没有缓存格式变化。回滚是还原 `pet/balance.py`、`pet/app.py`、`pet/speech_bubble_text.py`；磁盘上没有残留标记。
