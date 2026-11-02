# AI 对话联网失败纯 API 兜底 + 记忆页下拉配色 + 7 张角色人设卡（2026-11-02）

> **基线**：`27604166`（PR #424 语音回调/多宠修复 + 身份接力/feiyu 只读融合，L160–L161）
> **分支**：工作区（未提交）　**日期**：2026-11-02
> **范围**：7 个实现/测试文件 + 7 张人设卡 + 文档
> **关联**：用户反馈截图（记忆页「已删除」下拉白字白底）、联网对话偶发「网络不可达」、
> 新增角色人设卡需求（妈妈 / 爱莉希雅 / 艾米莉亚 / 蕾姆 / 水原千鹤 / 林翩翩 / 庄方怡）

## 一、核心特性

| # | 能力 | 说明 |
|---|---|---|
| 1 | 记忆页下拉框亮色可读 | `QComboBox` 的弹出列表是独立 `QAbstractItemView`，不继承组合框本体的 QSS。系统深色调色板下 `Text/WindowText` 是白色、`Base` 也是白色，用户看到「白字和底色混了」。新增强制规则后，浅色 `#252a32` / `#ffffff`、深色 `#e6e8ec` / `#1b1f27`。 |
| 2 | 联网 planner 不再改写进程路由 | `_post_json()`（planner）走 `_open_response(pollute=False)`；短 JSON 请求的成败不再把进程级「固定直连」钉住，正式 SSE 仍走原有的「默认 → 只直连 → 只代理」三路由计划。 |
| 3 | 正式回答网络失败 → 纯 API 兜底 | 联网开启、正式回答在传输层报 `network`、且一个字都没吐出去时，发状态行「网络不可达，已改用纯 API 回答（未联网核实）」，用**检索前的原始消息** + 网络说明重建请求，不再带任何检索资料；模型在回答开头说明「这次联网没有成功（网络不可达），以下内容未联网核实」。用户拿到的是回复，不是错误卡。 |
| 4 | 7 张角色人设卡 | 包内新增妈妈、爱莉希雅、艾米莉亚、蕾姆、水原千鹤、林翩翩、庄方宜；每张包含身份/性格/说话方式/相处方式/回答长度（默认 6–10 句）/擅长/边界/示例语气。角色卡用「桌面上以 `{name}` 的形象出现，但你的名字是 X」的写法，桌宠换角色也不会把 AI 身份改错。 |

**红线 / 不变量**：

- 已经显示给用户的正文/思维链绝不重发；半条回答仍按 `interrupted` 收尾。
- HTTP 明确答复（401/403/429/500/502/503/504）与取消不触发纯 API 兜底；不把服务器
  答复伪装成网络问题。
- 检索阶段已有的降级（planner/工具/注入失败）不变；新兜底只补「正式回答传输失败」
  这一条路径。
- 不新增配置键、不写用户数据目录、不改设置页保存/取消契约。
- 人设卡 schema 不变（`schema/id/name/description/prompt`），包内只读。

## 二、修改文件说明

`git diff --numstat`（新增文件另列）：

### 实现

| 文件 | 增删 | 改了什么 + 为什么 |
|---|---|---|
| `petcore/api/chat.py` | +106 / −2 | `_open_response()` 增加 `pollute` 参数并透传 `urlopen`；`_post_json()` 传 `pollute=False`，切断 planner 对进程级「固定直连」的污染。新增 `_NETWORK_FALLBACK_PROMPT` / `_NETWORK_FALLBACK_STATUS` / `inject_network_fallback_notice()`。把原正式回答的三路由重试抽成 `_stream_answer()`（逐位保留默认→直连→代理、0.6s 间隔、已吐字不重发），`stream_chat()` 在其外层只对「联网开启 + 传输层 network + 未吐字 + 未取消 + 非 HttpStatusError」做一次纯 API 重试。`original` 在检索前保留，兜底不带走任何检索资料。 |
| `petcore/ui/chat/styles.py` | +13 / −0 | `MODERN_QSS` 增加 `QComboBox QAbstractItemView` 浅色规则（背景/文字/边框/选中/内边距/行高），`DARK_OVERRIDES` 追加同选择器深色覆盖。只新增规则，不改既有选择器字节。 |
| `examples/petcore-mods/ai.chat/persona_presets/mom.json` | 新增 7 行 | 妈妈：家常、爱操心但不控制，不催婚不催生，正事认真听。 |
| `examples/petcore-mods/ai.chat/persona_presets/elysia.json` | 新增 7 行 | 爱莉希雅：崩坏3 粉色妖精小姐，浪漫/爱撒娇/珍惜美好，温柔而坚定。 |
| `examples/petcore-mods/ai.chat/persona_presets/emilia.json` | 新增 7 行 | 艾米莉亚：Re:Zero 半精灵，温柔/认真/有点天然/坚韧。 |
| `examples/petcore-mods/ai.chat/persona_presets/rem.json` | 新增 7 行 | 蕾姆：Re:Zero 蓝发女仆，勤快/温柔/略显自卑但坚定。 |
| `examples/petcore-mods/ai.chat/persona_presets/chizuru.json` | 新增 7 行 | 水原千鹤：租借女友，外在完美、内里认真嘴硬，演员梦想。 |
| `examples/petcore-mods/ai.chat/persona_presets/lin_pianpian.json` | 新增 7 行 | 林翩翩：《哀鸿：城破十日记》明末扬州女子，柔软通透、外柔内刚；不写露骨内容。 |
| `examples/petcore-mods/ai.chat/persona_presets/zhuang_fangyi.json` | 新增 7 行 | 庄方宜：《明日方舟：终末地》武陵管代/天师，麒麟族、雷法、自称「小闪电」。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_memory_view.py` | +57 / −0 | 强制应用调色板 `Text/WindowText=#ffffff`，打开 `memory-scope` 弹出列表，断言浅色文字亮度低于底色、深色文字亮度高于底色。当前实现会红，补 QSS 后绿。 |
| `tests/test_petcore_api_chat.py` | +91 / −1 | ① planner 的 `_post_json` 必须给 `urlopen` 传 `pollute=False`；② 检索成功、正式回答连续 3 次抛 `NetworkUnreachable` 时，纯 API 兜底返回正文、状态行含「网络不可达」「纯 API」、兜底 system 含网络说明且不含「联网搜索结果」。 |
| `tests/test_petcore_api_chat_stream.py` | +1 / −1 | 测试用 `direct_urlopen` 接缝显式吸收 `pollute`，不把它传给 `OpenerDirector.open`。 |
| `tests/test_petcore_ai_mod.py` | +1 / −1 | 同上，mod 级降级测试的直连接缝吸收 `pollute`。 |
| `tests/test_petcore_ai_persona.py` | +38 / −0 | 7 张卡存在、id/名称匹配、prompt ≥400 字且含 `{name}`「回答长度」「边界」「六到十句」；`resolve_prompt("elysia", "", "鲸鱼娘")` 仍包含「爱莉希雅」。 |

### 文档

- `docs/PETCORE-LOG.md` 追加 `L162`；`docs/PETCORE-LOG-INDEX.md` 加索引行。
- 本报告 + `docs/INDEX.md`「PR 报告存档」登记。

## 三、实现要点

1. **弹出列表为什么不吃 QSS**：`QComboBox#memory-scope { color: ... }` 只作用于组合框
   本体；弹出列表是 `QAbstractItemView` 顶层窗口，QSS 选择器必须显式命中它。实测
   （offscreen，强制 `Text/WindowText=#ffffff`）：修复前 `view Text=#ffffff`、
   `Base=#ffffff`；补 `QComboBox QAbstractItemView` 后 `Text=#252a32`、
   `Base=#ffffff`；深色表 `Text=#e6e8ec`、`Base=#1b1f27`。
2. **兜底边界**：`_stream_answer()` 已经把「三路由重试」与「已吐字不重发」拆开；
   外层只捕获 `errors.NetworkError`，并排除 `HttpStatusError`（服务器有答复）、
   `progress.emitted`（正文/思维链已到用户眼前）、取消。兜底 payload 由
   `build_payload(request, plain_messages, ...)` 重建，模型/温度/思考参数/超时全部
   沿用；`plain_messages` 是检索前的 `original` 加一条 `_NETWORK_FALLBACK_PROMPT`，
   不携带 `_CONTEXT_PROMPT` 的任何资料。
3. **路由语义**：`_post_json` 是 planner 的短 JSON 请求，`pollute=False` 只影响
   「这次请求是否固化进程级固定直连」，不改变其自身「代理优先→直连」的尝试顺序；
   正式回答仍按 `STREAM_ROUTE_PLANS` 换路。这样一次搜索 planner 的成功/失败不会
   替正式 SSE 下结论。
4. **角色卡身份**：`{name}` 在 `resolve_prompt` 里是当前桌宠的显示名；角色卡把
   `{name}` 放在「桌面形象」位置，正文用固定本名（如「你的名字是爱莉希雅」），
   所以换桌宠不会把 AI 身份改错。人设卡仍是包内只读 JSON，用户可在设置页复制/
   改名/覆盖为个人卡。

## 四、性能分析

**方法**：本机 Windows 11 / Python 3.12 / PySide6；`QT_QPA_PLATFORM=offscreen`；
样式构造 300 次取中位数；人设加载把原 6 张与全 13 张分别复制到临时目录，各
`load_presets()` 200 次取中位数（运行时按目录+mtimes 缓存，只在缓存失效时读盘）。

| 指标 | 实测 | 归属 |
|---|---|---|
| `MODERN_QSS` 字节数 | 14,542 B（新增约 653 字符） | 新增；每次窗口套样式只解析一次 |
| `modern_stylesheet(dark=False)` | 36.2 µs/次（300 次中位数） | 新增/既有；窗口样式重建路径 |
| `modern_stylesheet(dark=True)` | 38.3 µs/次（300 次中位数） | 新增/既有；窗口样式重建路径 |
| `load_presets()` 6 张卡 | 0.569 ms/次（200 次中位数） | 既有；缓存失效时 |
| `load_presets()` 13 张卡 | 1.186 ms/次（200 次中位数） | 新增；缓存失效时，增量约 0.617 ms |
| 正常路径额外请求 | **0 次** | 新增兜底；只在正式回答传输失败时触发 |
| 失败路径额外请求 | 3 次路由尝试 + **1 次**纯 API 请求 | 新增兜底；触发频率 = 正式回答 network 失败且未吐字 |

**结论**：

① 稳态开销：记忆页 QSS 只多 653 字符，样式构造仍为几十微秒；对话正常路径没有
新增请求、线程、磁盘或网络调用。人设卡只在 `load_presets()` 缓存失效（设置页/
人格卡目录 mtime 变化）时多读 7 个小 JSON，实测 +0.617 ms，不是每轮对话读盘。

② 新增路径成本：只有「联网开启 + 正式回答 transport `network` + 未吐字」才多一次
纯 API 请求；这是用户明确要求「一定要有回复」的代价。HTTP 状态、取消、已吐字
均不触发，避免把同一错误重发。

③ 系统调用/网络/磁盘/线程：无新增线程；无新增磁盘写入；planner 的 `pollute=False`
不改变其网络尝试次数；兜底多一次 POST（失败路径）。

④ 内存/缓存：无新增缓存；`original` 是原消息列表的浅拷贝，生命周期只在本次调用。

## 五、实机运行记录

**1. 根因现场复现（offscreen，本机真实 Qt）**

```text
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe - <<'PY'
... QApplication; palette Text/WindowText=#ffffff; QComboBox#memory-scope ...
... modern_stylesheet(dark=False) ...
PY
修复前：combo text=#252a32, view text=#ffffff, view base=#ffffff
修复后：combo text=#252a32, view text=#252a32, view base=#ffffff
深色表：view text=#e6e8ec, view base=#1b1f27

同一探针换成本项目真实的 `MemoryView`（假 MemoryPort + 对话窗 QSS）：
memory-scope popup text=#252a32, base=#ffffff（修复前为 #ffffff / #ffffff）。
```

另用 `view.grab()` 存 `tmp/memory_scope_light.png`：弹出列表文字为深色、可见
（offscreen 无中文字体，显示为方块，但颜色对比已由 palette 证明）。该临时图未入库。

**2. 纯 API 兜底（本机假服务端 + 真实 `stream_chat`）**

```text
$ PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe - <<'PY'
... 本机 127.0.0.1 SSE 服务端；planner 回 web_search 工具调用；
... 搜索返回 1 条结果；正式回答在「联网搜索结果」上下文中连续抛 NetworkUnreachable；
... 纯 API 兜底请求返回 "纯 API 回复"
PY
result: 纯 API 回复
statuses: [... '已获取联网资料，正在组织回答…', '网络抖动，正在重试…',
           '网络抖动，正在重试…', '网络不可达，已改用纯 API 回答（未联网核实）']
failed answer attempts: 3
plain answer requests: 1
total requests: 3  # planner×2 + 兜底回答；3 次失败在 urlopen 接缝即抛出
fallback system: 本次联网回答没有成功：网络不可达，已自动改用纯 API 回答。……
has search notes in fallback: False
```

**3. 人设加载（本机）**

```text
load_presets() count: 6 → 13
mom 513 字 / elysia 523 / emilia 499 / rem 466 / chizuru 464 /
lin_pianpian 506 / zhuang_fangyi 589
resolve_prompt('elysia', '', '鲸鱼娘') 首行含「你是爱莉希雅」与「鲸鱼娘」
设置页 persona_combo：14 项（13 张卡 + 自定义提示词）；
选中 elysia 后 prompt_edit 首行 = 你是爱莉希雅，《崩坏3》中前纪元逐火之蛾的成员……
```

**4. 用户可见行为**

- 记忆页「已删除」下拉：修复后弹出项为深色字白底（浅色）/浅色字深底（深色）。
- 联网正式回答 network 失败：状态行提示后继续产出纯 API 回复，不再只有错误卡。
- 设置 → AI 人格下拉：新增 7 张卡，选中后系统提示词包含对应角色设定与 6–10 句
  回答长度要求。

**未做**：没有在真实桌宠窗口里逐像素截图确认（offscreen 探针已证明 palette；
中文字体在 offscreen 缺失，不能替代肉眼验收）；没有用真实第三方 API 触发一次
网络不可达（用本机假服务端在 `urlopen` 接缝注入 `NetworkUnreachable` 复现）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check petcore/api/chat.py petcore/ui/chat/styles.py examples/petcore-mods/ai.chat tests/test_petcore_memory_view.py tests/test_petcore_api_chat.py tests/test_petcore_api_chat_stream.py tests/test_petcore_ai_persona.py` | All checks passed |
| 聚焦 | `python -m pytest -q tests/test_petcore_memory_view.py tests/test_petcore_api_chat.py tests/test_petcore_api_chat_stream.py tests/test_petcore_http_safety.py tests/test_petcore_ai_persona.py tests/test_petcore_ai_mod.py` | 135 passed（18.05s） |
| 全量（petcore） | `python -m pytest -q tests/test_petcore_*.py` | 3034 passed, 1 skipped（255.67s） |
| 全仓（尝试） | `python -m pytest -q` | 约 7 分钟仍无 `-q` 输出、进程停在两个 127.0.0.1 连接上，中止；未作为通过证据 |
| 文档守卫 | `python -m pytest -q tests/test_petcore_docs.py tests/test_pr_report_discipline.py` | 357 passed |

## 七、已知限制与后续

- 兜底只认传输层 `network`：`timeout` / `tls` / `protocol` 及 HTTP 状态不触发；
  这些情况重发纯 API 通常只是重复同一错误。若用户实测「超时也必现」，再评估把
  `timeout` 纳入（会额外等一次完整超时）。
- 兜底会多消耗一次 API 请求（输入 token）；只在失败路径发生。
- `load_presets()` 新增 7 个文件后缓存失效成本 +0.617 ms；若人格卡继续增长到
  数十张，可考虑按 id 懒加载，目前 13 张不需要。
- 角色卡基于公开设定整理；「庄方怡」按《明日方舟：终末地》的「庄方宜」处理，
  「艾米莉亚」沿用用户写法（常见译名艾米莉娅）。「妈妈」按 wholesome 母亲写，
  不做恋爱化处理。

## 八、风险与回滚

- 影响面：`petcore/api/chat.py` 是 core 唯一对话出口，所有 AI 聊天（完整窗/快速
  气泡/看看屏幕/截图问答）共用；兜底条件收得很窄，正常路径行为逐位不变。
- 新增 QSS 是通用 `QComboBox QAbstractItemView`，会同时改善对话窗内其它组合框
  （如记忆编辑对话框）的弹出列表可读性；不改设置窗样式。
- 回滚：`git revert` 删除 QSS、`pollute` 参数、兜底分支与 7 张 JSON 即可；无配置
  迁移、无落盘状态、无缓存残留。
