# 4.4.0 反馈三项修复 + AI 表情包回复（2026-11-03）

> **基线**：`bd830a6e`（petcore 0.5.0 / 日志 L165）　**分支**：petcore 本地工作树
> **日期**：2026-11-03　**范围**：实现 15（新建 2）/ 测试 7（新建 1）/ 文档 8 / 素材 1
> **关联**：[`PETCORE-SPEC.md`](PETCORE-SPEC.md)、[`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md)、
> [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)

## 〇、用户反馈与结论

4.4.0 的用户反馈三条，逐条核实当前 petcore 的现状：

| # | 反馈 | 当前 core 的结论 | 本刀 |
|---|---|---|---|
| 1 | 桌宠没有工具能力，模型却调用工具，把代码打出来 | **未支持也没有兜底**：`build_payload` 不声明 tools；`_extract_delta` 忽略 `delta.tool_calls`；`finish_reason="tool_calls"` 被当 `done`，空正文被删成空气泡 | 流式层剥离工具调用文本 + `end_reason="tool_calls"`；`ai.chat` 显示能力说明；提示词加能力边界 |
| 2 | 长系统提示词被截断，后面读不到 | **部分存在**：`ai_system_prompt` 在 20000 字静默砍尾（`Config.set` 当场归一化），设置页没有计数/提示 | `MAX_AI_SYSTEM_PROMPT` 单一常量 + 截断 warning；设置页计数器 + 硬截断 + 标红 |
| 3 | Markdown 图片语法被当代码输出 | **只支持一半**：md4c 生成 `<img>`，但本地图按原尺寸铺开、远程图破图、GIF 只有首帧；围栏里的图片语法仍显示代码 | 本地/data 图片内联贴纸（GIF 动）、远程占位、图片-only 围栏解包 |

新增：AI 对话可选表情包回复（`ai_meme_reply_enabled`，默认关）。图片来自彩蛋目录
（`easter_egg_image_dir`，空 = `assets/big_blue_fat_fish`；用户改过就用改后的）。
取图机制参考 `yyh-001/dsh-meme`：每张图带 `caption/tags/keywords`，模型写
`[表情: 文件名或标签]`，前端消费标记换成图片；本实现不照搬工具调用与图库市场。

## 一、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/api/chat.py` | +118 / −6 | `strip_tool_call_text()` 剥离正文 `<tool_call>`/函数调用形状 JSON；`_extract_tool_calls()` 读原生 `tool_calls`；`tool_calls`/无正文 → `end_reason="tool_calls"` + `incomplete`；普通 JSON 代码块不误删 |
| `petcore/config.py` | +17 / −1 | `MAX_AI_SYSTEM_PROMPT=20000`；截断记长度 warning；新增全局 `ai_meme_reply_enabled`（默认 False） |
| `petcore/mods/meme_images.py`（新建） | 237 行 | 彩蛋目录解析（与 `EasterEggController.image_dir()` 同口径）、可选 `meme-manifest.json`、60 张上限、白名单解析（不接受路径穿越）、`[表情:]` 消费与本地兜底 |
| `petcore/ui/chat/inline_images.py`（新建） | 186 行 | 本地/data 图片抽取、静态贴纸/`AnimatedImage`、图片-only 围栏解包、清理 QMovie |
| `petcore/ui/chat/markdown_view.py` | +29 / −1 | 远程 `<img>` 换 `[图片：alt]`；本地/data 图注入 `width="240"` |
| `petcore/ui/chat/widgets.py` | +77 / −5 | `MessageBubble` 挂贴纸、离开流式态强制重渲染、GIF 只有最新一条起播 |
| `petcore/ui/chat/quick.py` | +64 / −2 | 快速对话同一套贴纸（≤160） |
| `petcore/ui/chat/window.py` | +13 / 0 | `_animate_latest_images()`：最后一条助手消息起播，历史首帧 |
| `petcore/ui/chat/controller.py` | +7 / 0 | 记忆只存文字，剥掉图片 Markdown |
| `examples/petcore-mods/ai.chat/host/prompts.py` | +46 / −2 | `CAPABILITY_BOUNDARY`、`meme_prompt`、历史剥图片/标记 |
| `examples/petcore-mods/ai.chat/host/persona_presets.py` | +5 / −5 | `resolve_prompt` 统一追加能力边界（自定义人格卡也覆盖） |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +95 / −11 | `_prompt(meme=)`、目录缓存、`_apply_meme_reply`、`tool_calls` 可见文案、OCR/翻译/截图不追加表情包 |
| `examples/petcore-mods/ai.chat/host/constants.py` | +9 / 0 | `MEME_CATALOG_LIMIT`、`TOOL_UNSUPPORTED_TEXT` |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +94 / −4 | 表情包勾选行 + 当前目录/张数 hint；提示词计数器/硬截断；保存/回填/搜索词 |
| `examples/petcore-mods/ai.chat/mod.json` | +1 / −1 | 1.5.0 → 1.6.0 |
| `assets/big_blue_fat_fish/meme-manifest.json`（新建） | 149 行 | 24 张默认表情包的中文 caption/tags/keywords |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_api_chat_stream.py` | +86 / 0 | 原生 tool_calls、正文 tool_call、混合正文、工具形状 JSON、普通 JSON 不误删 |
| `tests/test_petcore_ai_mod.py` | +236 / −2 | 能力边界/自定义人格、tool_calls 可见文案（主/快速/看看屏幕）、表情包 prompt/收尾/路径穿越/历史剥离/设置行、提示词计数器 |
| `tests/test_petcore_ai_persona.py` | +23 / −1 | 20000 上限常量、截断 warning、人格卡/核常量一致、自定义 prompt 含边界 |
| `tests/test_petcore_meme_images.py`（新建） | 149 行 | 目录口径、manifest 合法/坏项/缺失、60 张抽样、name/stem/tag/caption、路径穿越、标记兜底 |
| `tests/test_petcore_chat_ui.py` | +78 / 0 | 本地 PNG 贴纸、GIF 最新一条动/历史首帧、图片-only 围栏、远程占位 |
| `tests/test_petcore_quick_chat.py` | +21 / 0 | 快速对话贴纸 ≤160 |
| `tests/test_petcore_config_scope.py` | +11 / 0 | `ai_meme_reply_enabled` 全局键、默认 False、布尔归一 |

### 文档

`docs/PETCORE-LOG.md`（L166）、`docs/PETCORE-LOG-INDEX.md`、
`docs/PETCORE-SPEC.md`、`docs/PETCORE-MOD-FORMAT.md`、
`docs/SETTINGS-CHANGE-GATES.md`、`petcore/AGENTS.md`、
`examples/petcore-mods/README.md`、本报告 + `docs/INDEX.md`。

## 二、性能分析

**方法（可复现）**：`PYTHONPATH=. QT_QPA_PLATFORM=offscreen .venv/Scripts/python .scratch/probe_ai_meme_perf.py`
（探针留在 `.scratch/`，不入库；脚本内 `time.perf_counter`，n=50，catalog(1000)/MessageBubble 用 n=20）。
环境：Windows 11 / Python 3.12 / PySide6 6.12.0 / offscreen。

| 指标 | 实测中位 / p95 | 归属 |
|---|---|---|
| `catalog(24)` | 0.97 / 1.54 ms | 新增：开启表情包且缓存未命中时一次（默认目录） |
| `catalog(1000)` | 27.4 / 34.8 ms | 新增：大目录首次/目录变化后一次 |
| `prompt_listing(24)` | 0.017 / 0.019 ms | 新增：每请求一次 |
| `apply_meme(5KB, marker)` | 0.024 / 0.030 ms | 新增：回复收尾一次 |
| `extract_inline_images(5KB+图)` | 0.093 / 0.123 ms | 新增：每条助手消息最终渲染一次 |
| `markdown_to_html(5KB)` | 0.274 / 0.497 ms | 既有路径基线 |
| `strip_tool_call_text(tool)` | 0.009 / 0.012 ms | 新增：每条回复一次 |
| `MessageBubble(PNG 640×480)` | 9.50 / 11.76 ms | 新增：每条带图消息一次 |
| `MessageBubble(GIF 8×4)` | 4.35 / 5.80 ms | 同上（含 `AnimatedImage` 构造） |

**逐条回答**

1. **稳态开销**：表情包关闭时 `_prompt(meme=False)` 不扫目录、不拼清单，稳态与改动前
   相同；打开后清单按 `(目录 mtime, manifest mtime)` 缓存，目录不变时每请求只多一次
   dict 比较。`extract_inline_images` 只在消息**最终渲染**跑（流式态跳过），普通纯文本
   消息没有 `![](` 时提前返回，成本为一次正则扫描。`strip_tool_call_text` 对普通回复
   只做 3 个正则 + 一次 `strip`，在 5KB 回复上 0.009ms。
2. **新增路径的绝对成本与触发频率**：`catalog(24)` 0.97ms/请求（缓存未命中才跑）；
   大目录 1000 张 27.4ms（`os.scandir` 已比 `Path.iterdir()+is_file()` 快约 30%），
   只在首次或目录/manifest 变化后跑——GUI 线程会感知到这一下，默认目录可忽略。
   图片贴纸每条带图消息构造一次（640×480 PNG 9.5ms / 8×4 GIF 4.35ms，含解码/缩放/
   控件创建）；`AnimatedImage` 播放沿既有实测（构造 ~1.08ms、重绘 ~0.012ms/帧），
   GIF 停播后无 QMovie 定时器。
3. **有无新的系统调用 / 网络 / 磁盘 / 线程**：**零网络**（远程图片不自动下载）；
   新增的磁盘只有「扫描彩蛋目录 + 读 manifest」——24 张 0.97ms，1000 张 27.4ms；
   没有新增线程、没有新增定时器；`AnimatedImage` 的 QMovie 只在 `show` 时起播，
   历史消息和窗口隐藏时停。
4. **内存有没有增长**：`MemeCatalog` 只存路径/短字符串（24 张约几 KB，1000 张约
   几十 KB，有 60 张抽样上限）；贴纸控件挂在消息气泡下，随气泡销毁；GIF 用
   `CacheNone`，峰值只多当前帧位图。

## 三、实机运行记录

> 本机真实环境：Windows 11 / Python 3.12 / PySide6 6.12.0；源码树运行（SAC 强制模式下
> 新构建 exe 可能被拦，本轮不重建安装包）。

```bash
# 1) 聚焦测试（最新一轮）
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest \
    tests/test_petcore_{api_chat,api_chat_stream,ai_mod,ai_persona,meme_images,chat_ui,quick_chat,images,animated_image,settings,config_scope}.py -q
283 passed in 53.49s

# 2) 静态门禁
$ .venv/Scripts/ruff check petcore/ examples/petcore-mods/ tests/
All checks passed!

# 3) 构建自检
$ .venv/Scripts/python.exe -m petcore --check
# 输出 strict.problems == []（完整 JSON 见 .scratch/check-*.json）

# 4) 全量门禁
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q
# 跑到 72% 时在 tests/test_proactive.py::test_self_talk_yields_to_important_bubble
# （旧 pet/ 线）触发 Windows fatal exception: access violation，进程原生崩溃、
# 没有 summary。该测试不在本次改动路径上（petcore 的 chat/UI/config 均未涉及）。
# 崩溃前唯一的 petcore 相关失败是 test_petcore_architecture::test_no_network_stack_in_core
# （inline_images 引了 urllib.parse）——已改用 QUrl 并单独复跑绿。
# 因此最终门禁改用 petcore 子集（本次改动的全部落点）：
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_petcore_*.py -q
# 30% 时同样的 Windows 原生异常（无失败用例输出、无 summary）。
# 最终门禁 = 上面的 283 个 focused + ruff + --check --strict + 文档门：
#   283 passed in 53.49s / All checks passed! / exit=0 strict.problems=[]
#   361 passed in 2.87s（test_petcore_docs + test_pr_report_discipline）

# 5) 文档门禁
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest \
    tests/test_petcore_docs.py tests/test_pr_report_discipline.py -q
```

**真 Qt 对象现场**（`.scratch/probe_ai_meme_live.py`，offscreen 真实事件循环）：

```text
png widgets 1 (240, 180)
gif widgets 1 AnimatedImage ...\meme.gif running True
older static True QLabel
fenced clean '' refs 1
sticker (240, 180)
settings row False True True
config value True
```

对应行为：640×480 PNG 被钳到 240×180；GIF 走 `AnimatedImage` 且 `is_running=True`；
加入下一条消息后旧 GIF 变回 `QLabel` 首帧；「围栏里只有一张本地图」被解包
（clean 为空、refs=1）；设置页默认不勾选、hint 含目录名与「1 张」，勾选后
`ai_meme_reply_enabled=True`。

**源码树启动**（真实进程，非 CI）：

```bash
$ python -m petcore --root "$ROOT" mods install examples/petcore-mods/ai.chat
installed ai.chat 1.6.0 -> ...\mods\ai.chat

$ QT_QPA_PLATFORM=offscreen timeout 10 python -m petcore \
    --root "$ROOT" --allow-feature-mods --verbose
# exit=124（10 秒后由 timeout 结束，属预期）
2026-11-03 19:49:06 INFO petcore.cli: starting petcore 0.5.0
2026-11-03 19:49:10 INFO petcore.ui.app: 启动耗时 qapp=259ms lock=266ms host=1389ms
    character=1437ms attach=1765ms restore=1831ms
（无 traceback / 无 mod 加载错误）
```

无 GUI 的 offscreen 启动只验证「装得上、起得来、无异常」；窗口观感与真实
150%/200% DPI 的 GIF 动效需用户实机目视（见未验证项）。

## 四、未验证与风险

- **4.4.0 旧线的提示词截断根因未复现**：本轮只保证 petcore 的 20000 上限可见且不再静默；
  旧版已经丢失的尾段无法恢复。
- **远程 Markdown 图不自动下载**：为避免 UI 线程联网/跟踪像素，`http(s)` 显示
  `[图片：alt]`；需要真实远程图渲染时要另开 `HttpPort` + 缓存方案。
- **GIF 观感未在 150%/200% DPI 真机目视**：offscreen 的 DPR 恒为 1，只能断言
  `AnimatedImage` 的逻辑尺寸/DPR 写回；真机缩放观感待用户确认。
- **超大目录**：>1000 张的扫描耗时只做本地探针，低配机/网络盘未实测。
- **工具执行未实现**：本轮只兜底「模型误输出工具调用」；真正给桌宠加工具能力是后续议题。
- **dsh-meme 的市场/学习/IM 投递**未移植；只借用标记 + caption/tags 清单。
- **全量套件在旧 `pet/` 测试 `test_proactive.py` 原生崩溃**：72% 时 Windows access
  violation，没有 summary；崩溃前的 4%/45% 失败无法逐条点名（旧线）。随后单独跑
  `tests/test_petcore_*.py` 也在 30% 处被同一个 Windows 原生异常打断（此前没有失败
  用例）。本次最终门禁因此取「改动落点的 283 个 focused + 文档门 + ruff + --check」；
  合并前若要求全量绿，应在无并发负载的干净环境重跑一次。
- **SAC 强制模式**：未重建/签名安装包，未实机安装。

## 五、回滚

- 关闭 `ai_meme_reply_enabled`（默认）即完全回到旧行为；`ai.chat` 版本回退到 1.5.0
  只影响设置页行。
- 图片渲染/工具兜底是 core 改动，回滚本报告涉及的提交即可；没有配置迁移、没有
  删除用户数据、没有新增网络/线程。
