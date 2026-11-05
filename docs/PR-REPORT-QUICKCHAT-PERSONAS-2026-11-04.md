# PR 报告：快速对话深色模式黑底 + 人设卡角色扮演化与 MyGO/祥子扩卡（2026-11-04）

分支：`feat/petcore-vpet-architecture`　对应 LOG：`docs/PETCORE-LOG.md` L168

用户反馈：

1. 快速对话窗口纯黑、看不见文字（截图：白色 surface 中间一块黑色输出区）；
2. 后加的人设卡回复太短、更像桌宠陪聊，希望 AI 读卡后直接角色扮演；
3. 新增 5 张 MyGO 卡（千早爱音 / 长崎素世 / 高松灯 / 椎名立希 / 要乐奈）与 2 张
   丰川祥子卡（客服小祥 / 正剧向）。

---

## 1. 修改文件说明

`git diff --numstat`（2026-11-04）：

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `petcore/ui/chat/quick.py` | +96/−3 | 新增 `quick_chat_palette()`，`QuickChatBubble` 自带浅色调色板；`scroll.viewport()`、`output_host`、`image_host` 关闭自动填底并显式透明。根因：外观只写在 QSS、底色走系统调色板，深色模式下 viewport 用 `Base=#000000` 涂黑，正文写死 `#1f2328`。 |
| `tests/test_petcore_quick_chat.py` | +71 | 深色调色板下真实渲染断言：输出区背景亮度 >200、文字区存在 <120 暗像素；测试结束恢复调色板。 |
| `examples/petcore-mods/ai.chat/persona_presets/anon.json` | 新增 14.6 KB | 千早爱音：4.9k 字，11 节骨架 + 角色扮演约定 + 6–8 句。 |
| `.../soyo.json` | 新增 13.6 KB | 长崎素世：4.5k 字，同上（贵安、红茶、碇司令手势、算计与怕被丢下）。 |
| `.../tomori.json` | 新增 12.5 KB | 高松灯：4.2k 字，短句停顿、金平糖/石头/西瓜虫、一辈子执念。 |
| `.../taki.json` | 新增 12.6 KB | 椎名立希：4.2k 字，嘴毒/严格/姐姐光环下的劣等感/只在灯前软化。 |
| `.../rana.json` | 新增 12.8 KB | 要乐奈：4.3k 字，猫系自由、看谱即弹、抹茶荞麦面、「有趣的女人」。 |
| `.../sakiko.json` | 新增 12.5 KB | 丰川祥子（正剧向）：4.3k 字，家变/打工/骄傲/拒绝怜悯。 |
| `.../sakiko_cs.json` | 新增 12.3 KB | 客服小祥（梗向）：4.1k 字，客服话术外壳 + 本人自尊/疲惫；Staff A 五连叹号 vs Staff S 单个句号。 |
| `.../chizuru.json` | +2/−2 | 3.0k→4.7k：补角色扮演约定、6–8 句、加厚演员/嘴硬/不服输的具体反应。 |
| `.../emilia.json` | +2/−2 | 3.0k→4.7k：同上（帕克、礼仪、不谙世事但要强）。 |
| `.../lin_pianpian.json` | +2/−2 | 3.0k→4.5k：同上（明末扬州、温柔里的韧劲）。 |
| `.../mom.json` | +2/−2 | 3.1k→4.6k：同上（妈妈边界不变，不写成恋爱）。 |
| `.../rem.json` | +2/−2 | 3.0k→4.6k：同上（女仆/自卑/坚定）。 |
| `.../zhuang_fangyi.json` | +2/−2 | 3.1k→4.6k：同上（工作狂/小闪电/雷法飞剑）。 |
| `.../elysia.json` | +1/−1 | 6.2k→6.4k：插入角色扮演约定、长度改 6–8 句（其余人设保持）。 |
| `.../bobo.json` | +1/−1 | 7.0k→7.2k：插入角色扮演约定、长度改「六到八句短句」（保留软萌/呆感/黑暗料理）。 |
| `tests/test_petcore_ai_persona.py` | +67/−1 | `MODERN_PERSONA_IDS`（15 张）+ 角色扮演段/六到八句/长度/描述上限/唯一 `{name}` 可 format。 |
| `packaging/petcore.iss` | +3 | ai.chat 已安装时升级安装同步包内文件（`Check: DirExists(...) and not IsTaskSelected('mod_aichat')`），未安装仍不写盘。 |
| `docs/PETCORE-MOD-FORMAT.md` | +29 | 人格卡口径：11 节骨架、角色扮演约定、6–8 句、测试守卫。 |
| `docs/PETCORE-SPEC.md` | +27/−1 | 现代角色卡清单与短格式卡的保留说明。 |

未改动：默认卡 `whale_maid` 与 `gentle`/`tsundere`/`genki`/`calm`/`cat`（测试断言依赖其
「回答长度 / 六句话」口径）。

---

## 2. 性能分析

环境：Windows 11 26200、Python 3.12.10、PySide6 6.12.0、offscreen（无真实桌面/音频）。

### 2.1 稳态开销

| 路径 | 改前 | 改后 |
|---|---|---|
| 快速对话构建/渲染 | viewport 按系统调色板填底（深色 `#000000`） | 多一次 `setPalette`（微秒级）+ 三处 `autoFillBackground(False)`；每帧绘制不变 |
| 人格卡加载 | 21 张 JSON，读文件 + 缓存 | 不变（仍是 21 张；新卡 7 张，单张 ~12–14 KB） |
| 提示词长度 | 短卡 ~3.0k 字 | 现代卡 4.1k–7.2k 字（仍远低于 20000 上限） |
| 安装包 | ai.chat 仅勾选时写入 | 已安装时多一条刷新条目；未安装行为不变 |

### 2.2 新增路径成本与触发频率

- 调色板：`QuickChatBubble` 构造时一次（每只宠每会话一次），10 个 `setColor`；
  不新增定时器/线程/网络。
- 人格卡：只改内容，不加运行时代码；加载频率 = 设置页打开/会话开始，沿用 mtime 缓存。
- 快速对话渲染：无新增每帧工作。

### 2.3 系统调用 / 网络 / 磁盘 / 线程

- 无新增系统调用、网络、线程。
- 磁盘：人格卡多 7 个文件（~91 KB）；安装器多一条已在盘上的源目录刷新（仅升级时）。

### 2.4 内存

- 快速对话：无新增常驻对象（调色板 ~几十字节）。
- 提示词：单卡 +1.0–1.6k 字 → 单次请求的系统提示 token 约 +1.5k–3k（中文按字计），
  属用户明确要求的回复长度/角色扮演强度代价；上限 20000 字未变。

---

## 3. 实机运行记录

### 3.1 深色模式复现与修复（offscreen，真实 Qt 绘制）

改前（把 `QApplication` 调色板换成深色，再建气泡并渲染）：

```
scroll #000000 / output_host #141414 / label 背景 #1e1e1e   ← 黑底
正文颜色写死 #1f2328                                       ← 黑字，看不见
```

改后同一探针：

```
scroll #fcfcfe / output_host #fcfcfe / label 背景 #fcfcfe
文字区最暗像素 lightness = 36（暗色文字，可读）
```

### 3.2 人格卡

- `load_presets()` 实跑返回 **21 张**，7 张新卡都在；逐张 JSON 解析、id 唯一、
  `description` ≤160、`{name}` 唯一且 `format(name=…)` 可执行。
- 15 张现代卡：11 节齐全、含「角色扮演约定」与「六到八句话」、≥3500 字。

### 3.3 单测

```powershell
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe -m pytest -q tests/test_petcore_quick_chat.py `
  tests/test_petcore_ai_persona.py tests/test_petcore_ai_mod.py `
  tests/test_petcore_chat_ui.py tests/test_petcore_chat_views.py `
  tests/test_petcore_settings.py tests/test_petcore_settings_search.py tests/test_quick_chat.py
# 224 passed
.venv\Scripts\python.exe -m ruff check petcore/ tests/test_petcore_quick_chat.py tests/test_petcore_ai_persona.py
# All checks passed!
```

### 3.4 安装包产物

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 `
  -Clean -Installer -OutputRoot rel-storage-2026-11-04a `
  -SignThumbprint 217337C099EC8624AC481BFC9F3E1664D0A442E1
```

- `rel-storage-2026-11-04a/dsh-pet-core-setup.exe`：310,520,704 B（296.14 MiB），
  SHA-256 `3274DDE571F270B549F47EFB80751635ACAAF68999CFDEE9EF4D89B967959B55`，
  Authenticode `Valid`（`CN=DSH Pet Core Dev`；主 exe 同）。
- 构建内 `--check --strict` 通过（`menu_template=json / characters=1 /
  builtin_images=24 / daily_lines=1200 / diagnostics=[]`）。
- Inno 日志确认 22 个 `persona_presets` 文件进包（含 7 张新卡）；打包产物真机启动
  `character=263ms`、`features=1061ms`（首帧后分三拍），无 Traceback/ERROR。

### 3.5 未能自动验证

- 真实 Windows 深色模式桌面上的肉眼观感（像素级探针覆盖同一路径，但没有真机截图）。
- 新卡接真实模型后的多轮对话质量（取决于所选模型；文案本身已按角色差异写）。
- 安装器刷新条目的实机升级路径（未在已装机器上跑安装向导）。
