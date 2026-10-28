# AI 对话联网检索误伤 + 图片彩蛋 GIF + API 出厂默认值 + 素材包外链（2026-10-28）

> **基线**：`4fbca8a3`（引入联网检索的提交；本轮工作树起点）
> **分支**：petcore 本地工作树　**日期**：2026-10-28
> **范围**：27 个文件（实现 12，其中新建 2；测试 10，其中新建 2；文档 5）
> **关联**：[`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)、
> [`PR-REPORT-THANKS-VOICE-2026-10-27`](PR-REPORT-PETCORE-THANKS-VOICE-2026-10-27.md)、
> [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)

## 一、核心特性

用户报了四个问题，逐条落地：

1. **AI 对话「明明不需要联网也失败」**：不是检索判定错，而是两条真实缺陷叠在一起——
   ① 检索引擎请求（`petcore/net/websearch.py`）走 `http.urlopen()`，把「代理失败→直连成功」
   固化成了**进程级**结论，之后的正式回答请求不再尝试代理，于是在必须走代理的网络里
   `NetworkError(network)`；② 即使真的断流，已经流式显示的正文也被丢掉，界面只剩
   「请求失败：网络不可达 / 本次回复未保存」。现在搜索/读页是**单请求路由**（不污染进程结论），
   并且断流时**保留已生成正文**、标注「这次回答没有正常结束」。
2. **图片彩蛋支持 GIF**：`media/images.py` 之前用 `QImageReader.read()` 只取首帧。新增按 DPR 解码的
   `AnimatedImage`（QMovie 帧按 `逻辑 × dpr` 缩、绘制前写回 DPR），彩蛋的两条呈现路径
   （默认气泡 / `window` 弹窗）都支持动图，静态图路径一行未改。
3. **API 页出厂即 DeepSeek**：新建服务草稿预填名称 / Base URL / Chat Path / 模型 / 视觉模型 / 余额协议
   （与主分支出厂值同一套），小白只需要填 API Key；新增「API 申请」行，显示
   `https://platform.deepseek.com/api_keys` 并提供「打开 DeepSeek 开放平台」按钮。
4. **常规页两个素材包模块**：「数据」卡片在「角色资源包」下方新增「完整角色素材包」与
   「高清完整角色素材包」两行，每行两枚横排按钮（GitHub 下载 / 夸克网盘）。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 检索不污染路由 | `urlopen(pollute=False)` 只给搜索/读页；进程级「固定直连」结论只由正式请求自己决定 |
| 2 | planner 短预算 | 判定轮用 ≤20s 的 `net_timeout`，不再吃满 60s |
| 3 | 断流保留半条回答 | `stream_chat` 在已有增量时返回 `incomplete=True, end_reason="interrupted"`；mod 走 `done` 而不是 `error` |
| 4 | 动图彩蛋 | `AnimatedImage`（QMovie + DPR），气泡与弹窗共用；收起/隐藏即停播 |
| 5 | API 出厂默认值 | 只填 Key 即可保存；`api.json` 里仍然没有 Key |
| 6 | 素材包外链 | 两行四枚按钮，只调 `QDesktopServices.openUrl`，不发网络请求 |

**红线 / 不变量**：

- `prefer_direct` 仍是**单请求**语义：`fetch_url` 的既有行为不变；`urlopen` 只有在 `pollute=True`（默认）
  且调用方没给 `opener` 时才可能改写进程结论。
- 一个字都没收到时的失败仍抛 `NetworkError`（不许把空回答伪装成功）。
- API Key 只进钥匙串（owner `core.api`），`api.json` / `config.json` 不出现密钥（新增测试断言）。
- 彩蛋静态图（PNG/JPG）的质量口径不变：仍走 `load_pixmap`（按物理像素解码 + 写回 DPR）。
- 没有新增任何持久化配置键；「取消」语义不受影响（预填只改草稿初值，`dirty()` 未改动时仍为 False）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/net/http.py` | +4 / −1 | `urlopen()` 新增 `pollute: bool = True`；污染条件加 `pollute`；docstring 写明「自行选路由的调用方必须传 `pollute=False`」 |
| `petcore/net/websearch.py` | +2 / −0 | `_open()` 传 `pollute=False`（`search` 与 `fetch_page_text` 共用这条边界，一处覆盖全模块） |
| `petcore/api/chat.py` | +24 / −2 | ① planner 的 `_post_json` 改传 `net_timeout`；② 读流阶段：已有 `parts` 时不再抛错，关掉响应后返回 `end_reason="interrupted"` 的半条 `ChatResult`；③ `ChatResult` 与 `stream_chat` docstring 补 `interrupted` 语义 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +14 / −0 | 主路径与快速气泡的失败分支：`partial` 非空时 `done(半条 + 说明)`，只有空正文才 `error` + 失败气泡 |
| `petcore/media/images.py` | +44 / −0 | 新增 `is_animated()`（先看 `imageCount()`，单帧 GIF 不会被误判）与 `logical_size_for()`（只读图片头，不解码像素） |
| `petcore/ui/animated_image.py`（新建，199 行） | 新文件 | `AnimatedImage(QWidget)`：`QMovie` + `setScaledSize(逻辑×dpr)`，`paintEvent` 写回 DPR 后 1:1 绘制；`show/hide` 起停播、`set_path` 复用、`close` 释放 movie |
| `petcore/ui/easter_egg.py` | +33 / −6 | `EasterEggPopup` 按 `is_animated` 分叉：静态路径原样，动图用 `AnimatedImage`；窗口尺寸公式形状不变；新增 `closeEvent` 停播 |
| `petcore/ui/bubble.py` | +69 / −6 | `show_image()` 路径入参且是动图时走动图控件（挂在 `image_label` 的 8px 内边距里），静态路径不变；`show_text()` / `hideEvent` 停播 |
| `petcore/ui/external_links.py`（新建） | 新文件 | 设置页共用外链常量：`REPO_URL` / `QUARK_URL` / `DEEPSEEK_API_URL` / `CHARACTER_PACKS_URL`（单一真值） |
| `petcore/ui/settings/page_api.py` | +54 / −9 | 出厂默认值常量 + `_start_draft()` 预填 + 状态文案；新增「API 申请」行（只读 URL + 打开按钮）；模型行 hint 写清可改 `deepseek-chat`；`keywords()` 补别名 |
| `petcore/ui/settings/pages_core.py` | +36 / −0 | 「数据」卡片追加两个素材包行 + `_pack_links_row()` 横排外链构造器；`keywords()` 补 `素材包/下载/高清/网盘/github` |
| `petcore/ui/settings/page_about.py` | +1 / −2 | `REPO_URL` 改为从 `../external_links` 导入（去掉重复字面量，保留同名符号） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_websearch.py` | +47 / −0 | 搜索「代理失败→直连成功」后 `force_direct()` 仍为 False，且随后的普通请求仍先试代理 |
| `tests/test_petcore_api_chat.py` | +63 / −0 | planner 传输失败仍能回答；开关开启 + 无 `tool_calls` 时整轮恰好 2 个请求、planner 预算 ≤20s |
| `tests/test_petcore_api_chat_stream.py` | +40 / −0 | 断流保留半条（`incomplete/interrupted` + 状态行）；无增量仍抛 `NetworkError` |
| `tests/test_petcore_ai_mod.py` | +57 / −0 | 有 `partial` 时调 `done`（不弹错误卡、文本含「已保留前面的内容」）；无 `partial` 仍 `error` |
| `tests/test_petcore_images.py` | +53 / −0 | `is_animated` 真/假、`logical_size_for` 等比不超限 |
| `tests/test_petcore_bubble.py` | +85 / −0 | 动图在播、切静态图停播、换图复用控件、气泡 hide 后停播 |
| `tests/test_petcore_easter_egg.py` | +82 / −0 | 弹窗动图尺寸/DPR、`window` 模式走 `AnimatedImage` 且 `close()` 后停、默认气泡模式播动图 |
| `tests/test_petcore_animated_image.py`（新建，166 行） | 新文件 | 9 例：逻辑/物理尺寸、`max_side` 钳位、真实事件循环推进帧、hide/show、`set_path` 复用、`close` 释放、缺文件退化成 1×1 |
| `tests/test_petcore_api.py` | +81 / −0 | 空 store 的预填值与 `dirty() is False`；只填 Key 能保存且 `api.json` 无 Key；开放平台按钮的 URL |
| `tests/test_petcore_settings_assets_links.py`（新建，160 行） | 新文件 | 两行位置/每行两枚按钮/宽度上限/搜索命中/点击 URL/无草稿差异 |

### 未改动（避免评审误判）

- `petcore/ui/chat/window.py` 的 `show_error` 没动：断流不再走错误路径，错误卡只剩「一个字都没有」的场景。
- `petcore/api/spec.py` / `petcore/api/store.py` 没动：预填只发生在设置页草稿，不改 `from_dict` 对坏数据的容错。
- `petcore/ui/settings/coverage.py` 没动：它是**菜单**覆盖表，往里加无对应菜单项的 key 会让
  `tests/test_petcore_settings_coverage.py` 变红。
- `packaging/petcore.iss`、`examples/petcore-mods/README.md`、`tool.ai-browser` 相关文件是工作树里
  **既有**的未提交改动（L110 的本地验证产物），与本轮无关，未触碰。

## 三、实现要点

1. **路由污染是怎么被关掉的**：`http.urlopen()` 原本只有一个进程级结论 `_force_direct`，任何一次
   「代理失败→直连成功」都会把它置真并清空尝试列表。新增 `pollute` 后，搜索/读页这类
   「URL 来自模型、路由由自己定」的请求不再参与这个判定；正式 API 请求仍是 `pollute=True`，
   所以「系统代理不可用」的结论依旧会被正常学到一次（不是禁用它）。
2. **断流保留半条**：读流循环里的 `except Exception` 分三支——取消、已有增量（记下异常、`break`）、
   其它（照旧分类抛错）。`finally` 负责 `stop_watch` / `watcher.join` / `response.close()`，
   之后才返回 `ChatResult(incomplete=True, end_reason="interrupted")`；mod 侧把半条正文连同
   「（网络不可达：这次回答没有正常结束，已保留前面的内容。）」交给 `done`，会话里也留下这条记录。
3. **GIF 的 DPR 口径**：QMovie 出的帧 `devicePixelRatio()` 恒为 1，`QLabel.setMovie()` 在 1.5 倍屏会
   被当成逻辑像素再放大一次。`AnimatedImage` 用 `setScaledSize(逻辑 × dpr)` 让解码器直接出物理像素，
   绘制前 `setDevicePixelRatio(dpr)`，`paintEvent` 里 `drawPixmap(0, 0, pm)` 就是 1:1 贴图，
   与 `media/images.py` 对静态图的口径一致。
4. **动图生命周期**：`showEvent` 起播、`hideEvent` 停播、`closeEvent` 停播 + 断 `frameChanged` +
   `deleteLater`；气泡的 `show_text()` 与 `hideEvent` 都显式停播（否则 4 秒定时收起后 GIF 还在解码）。
5. **API 预填不破坏「脏」判定**：`_populate()` 把同一批默认值刷进控件并把当前表单存成 `_baseline`，
   所以页面一打开 `dirty()` 仍是 False；用户只填 Key 时差异只有 Key，保存后 `api.json` 仍不含密钥。
6. **素材包两行不新增配置**：URL 是代码常量（`external_links.py`，将来换真实素材页只改一行），
   按钮只调 `QDesktopServices.openUrl`，因此不参与设置保存/取消。

## 四、性能分析

**方法（可复现）**：`PYTHONPATH=. QT_QPA_PLATFORM=offscreen .venv/Scripts/python .scratch/probe_docs_perf.py`
（探针留在 `.scratch/`，不入库；样本量见下表，脚本内用 `time.perf_counter` 逐次取中位与 p95）
环境：Windows 11 家庭版中文版 / Python 3.12.10 / PySide6 6.12.0 / `QT_QPA_PLATFORM=offscreen`；
动图样本 `.scratch/probe_big.gif`（3348 B，480×288，五帧）。

| 指标 | 实测：中位 / p95 | 归属 |
|---|---|---|
| `is_animated()`（GIF） | 0.045 / 0.060 ms（n=50） | 新增：每次 `show_image` 一次 |
| `is_animated()`（PNG） | 0.032 / 0.038 ms（n=50） | 新增：静态图也要多查一次 |
| `logical_size_for()`（GIF） | 0.041 / 0.053 ms（n=50） | 新增：只有动图走 |
| `AnimatedImage(...)` 构造（QMovie + 首帧预热） | 1.076 / 1.651 ms（n=20） | 新增：只有动图，换图一次 |
| `AnimatedImage` 重绘（480×288，`grab()` 计） | 0.012 / 0.021 ms（n=200） | 新增：帧率驱动；offscreen 合成偏乐观 |
| `SpeechBubble.show_image(GIF)` | 1.282 / 2.296 ms（n=20） | 相对静态路径 |
| `SpeechBubble.show_image(PNG)` | 1.262 / 1.964 ms（n=20） | **既有路径基线** |
| `GeneralPage` 构造（含新增两行） | 12.506 / 30.085 ms（n=20） | 既有 + 新增 2 行 |
| `ApiSettingsPage` 构造（含「API 申请」行） | 2.140 / 3.237 ms（n=20） | 既有 + 新增 1 行 |

**结论（逐条）**

1. **稳态开销**：静态配图只多一次 `is_animated()`——0.032–0.060 ms，占 `show_image` 总耗时
   （1.26 ms）的 2%–5%，与 GIF 路径的 1.282 ms 相比在噪声内。动图在播时按 GIF 自身帧率解码，
   10 fps 下约 `0.012 ms × 10 = 0.12 ms/s`；气泡收起、弹窗关闭、切回文字都会 `stop()`，
   停播后没有 QMovie 定时器（`hideEvent`/`closeEvent` 是唯一开关）。
2. **新增路径的绝对成本与触发频率**：动图构造 1.08 ms（一次 `QMovie` 建 + 首帧预热），只在
   用户真的把 `.gif` 当彩蛋/配图时发生一次；同一气泡换图走 `set_path` 复用控件。设置页新增
   3 行控件，`GeneralPage` 12.5 ms / `ApiSettingsPage` 2.1 ms 的构造耗时里占不到 1 ms
   （每行一个 `QWidget + QHBoxLayout` + 2 个按钮，QSS 与既有按钮同款）。
3. **有无新的系统调用 / 网络 / 磁盘 / 线程**：**零**。任务 1 的路由改动只加一个布尔判断
   （`pollute`），尝试次数上限不变（仍是最多两次：代理→直连）；`pollute=False` 只是不写
   进程级标志。断流分支把「抛异常」换成「返回半条结果」，不新增任何 I/O；planner 预算从
   `timeout`（默认 60s）收紧到 `net_timeout`（≤20s），最坏等待时间反而下降。素材包外链与
   API 开放平台按钮只调 `QDesktopServices.openUrl`，本进程不发请求。GIF 走本地解码，不联网。
4. **内存与缓存有没有增长**：`QMovie` 用 `CacheNone`（不缓存全部帧），峰值只多一张当前帧
   位图（480×288×4 ≈ 0.55 MB）；`AnimatedImage` 是气泡/弹窗的子控件，随宿主销毁
   （`closeEvent` 里断 `frameChanged` + `deleteLater`），没有进程级缓存。

**时间收益（任务 1）**：离线/代理不可达时，判定轮的等待上限从 60 s 降到 ≤20 s；正式请求断流时
用户拿到「已生成的那部分 + 说明」，而不是空白错误卡——数据不再被丢弃。

**全量门禁耗时**：`QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q` →
`10734 passed, 18 skipped, 19 warnings in 1592.84s (0:26:32)`（本机单进程，非 CI）。

## 五、实机运行记录

本机真实环境（Windows 11 / Python 3.12.10 / PySide6 6.12.0），不是 CI：

```bash
# 1) 全量门禁
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q
10734 passed, 18 skipped, 19 warnings in 1592.84s (0:26:32)

# 2) 本轮 14 个相关测试文件
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest \
    tests/test_petcore_websearch.py tests/test_petcore_api_chat.py \
    tests/test_petcore_api_chat_stream.py tests/test_petcore_ai_mod.py \
    tests/test_petcore_images.py tests/test_petcore_bubble.py \
    tests/test_petcore_easter_egg.py tests/test_petcore_animated_image.py \
    tests/test_petcore_api.py tests/test_petcore_settings_assets_links.py \
    tests/test_petcore_settings.py tests/test_petcore_pages_smoke.py \
    tests/test_petcore_settings_coverage.py tests/test_petcore_architecture.py -q
246 passed in 38.81s

# 3) 静态门禁与文档门禁
$ .venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/
All checks passed!
$ .venv/Scripts/python -m petcore --check            # strict.problems == []
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest \
    tests/test_petcore_docs.py tests/test_pr_report_discipline.py -q
321 passed in 7.82s
```

**真实对象现场（`.scratch/probe_docs_live.py` → `probe_docs_live.json`，真 Qt 对象 + 抓帧）**

| 检查 | 真实输出 |
|---|---|
| GIF 彩蛋弹窗（`easter_egg_display="window"` 等价路径） | 弹窗 504×368，`popup.animated_image is not None`，`is_running == True`，抓帧中心像素 `#28c83c`（当前帧颜色，不是首帧也不空白） |
| 弹窗关闭 | `is_running == False`（`closeEvent` 停播生效） |
| 气泡配图（默认 `easter_egg_display="bubble"`） | `show_image(gif)` 后 `is_running == True`；`show_text("…")` 后 `is_running == False` |
| API 页出厂值 | `DeepSeek` / `https://api.deepseek.com` / `/v1/chat/completions` / `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` / 余额协议 `deepseek`，`dirty() == False` |
| API 申请按钮 | 文案「打开 DeepSeek 开放平台」，只读地址 `https://platform.deepseek.com/api_keys`，点击后 `QDesktopServices.openUrl` 收到同一 URL |
| 常规页两个素材包 | 两行标题都在；四枚按钮文本 `GitHub 下载 / 夸克网盘 / GitHub 下载 / 夸克网盘`；依次点击收到的 URL 与预期完全一致（`github.com/MerZlin/dsh-pet-indesktop`、`pan.quark.cn/s/68fc681ae486` 各两次） |

**为什么任务 1 没有「现场复现」这一段**：本机网络没有「API 必须走代理、搜索引擎可直连」这种组合，
无法把原故障原地重现。替代证据是单元层的真实路由序列：假 opener 下 `search()` 的 `use_proxy`
序列为 `[True, False]` 且 `http.force_direct() is False`，紧接着的普通请求序列仍是 `[True, False]`；
改前同一断言红（`assert True is False`）。**结论只到这一层**，端到端需要用户在出问题的机器上复跑确认。

## 六、未验证项与风险

- **任务 1 的端到端复现依赖代理环境**：本机网络没有「必须走代理才能连 API」的配置，无法原地复现
  「检索吃掉代理结论 → 正式回答失败」。证据只到单元层（假 `_opener` 的 `use_proxy` 序列
  `[True, False]` + `force_direct() is False`）。修完仍建议用户在出现该问题的机器上复跑一次，
  并在日志里确认不再出现「网络请求改走直连：系统代理不可用」紧跟检索轮。
- **GIF 没在 150%/200% DPI 真机目视确认**：offscreen 的 `devicePixelRatio` 恒为 1，只能断言
  `movie.scaledSize() == 逻辑 × dpr` 与 DPR 写回；观感需真机看一眼。
- **API 出厂模型名未实测**：本机无真实 Key，`deepseek-v4-flash` / `deepseek-v4-flash-vision-exp`
  对用户账号是否有效未验证；模型行 hint 已写「如账号不支持可改成 `deepseek-chat`」。
- **两个素材包 GitHub 按钮当前指向仓库根**：仓库里没有独立的素材包下载页，`CHARACTER_PACKS_URL`
  先等于 `REPO_URL`，换页面只改这一行；夸克按钮用的是既有分享地址
  `https://pan.quark.cn/s/68fc681ae486`。
- **设置页观感**：offscreen 无 CJK 字体，截图全是方块，因此没有提供视觉截图；改用几何断言
  （新增行宽度上限、720×500 下「保存服务」可达）。真机观感待用户确认。
