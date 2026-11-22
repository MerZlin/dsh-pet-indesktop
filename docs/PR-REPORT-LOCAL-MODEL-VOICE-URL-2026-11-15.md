# 本地模型 / 自训语音 / aboutme 目录 URL（2026-11-15）

> **基线**：`f62b51aa`（L200 累计提交之后）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-11-15
> **范围**：20 个文件（实现 9、测试 4、文档 7；新增 `petcore/net/aboutme_repo.py`
> 与 `docs/LOCAL-MODELS.md`）
> **关联**：[`LOCAL-MODELS.md`](LOCAL-MODELS.md)、
> [`PETCORE-CHARACTER-PACKS.md`](PETCORE-CHARACTER-PACKS.md) §6a

## 一、核心特性

用户给了三个真实资产/需求：本地 `gemma-4-E4B` GGUF、自己训练的 GPT-SoVITS 权重、
以及「设置页要能一键填入 aboutme 角色包 URL」。本轮结果：

1. **本地 GGUF 能对话**：用 llama.cpp `b12397` 的 `llama-server` 加载，桌宠原有
   「llama.cpp server（本地）」预设直接可用。实测这台机器解码 7.4–8.5 tok/s、
   3,595 token 人设预填充 17.8–21.8 s；冷启动首轮 8.6–22.4 s，同前缀第二轮
   3.3–7.5 s。**但没有随包运行时**（已与用户确认只做实测+文档）。
2. **发现并修复一个真实传输缺陷**：本地模型首字慢（1–20 s）时，旧 SSE 读取器用
   0.5 s socket 超时轮询取消；`http.client` 的 `BufferedReader` 一次读超时后永久
   报 `OSError: cannot read from timed out object`，于是每次对话都在 1 秒内失败
   （真机复现：`ai call failed … cause=OSError`，服务端日志是客户端主动断开）。
   现在改成 `select` 等可读 + `read1` 增量 + 自建行缓冲。
3. **自训语音接进来**：`local` TTS 新增 `gpt_sovits` 协议（api_v2 `POST /tts`），
   设置页新增参考音频/参考文本/两种语言；缓存键包含参考音频与提示文本。
   实测 262 MB ONNX 只是 SoVITS 解码器（RTF 0.10），完整发声还缺 GPT/BERT/HuBERT
   与参考音频——文档写清了缺口与部署步骤，不随包、不进程内推理（已确认）。
4. **aboutme URL**：「扩展管理 → 从链接安装」新增角色包下拉 + 只读 URL +
   「打开」「填入输入框」；安装器支持 GitHub 角色目录 URL（`…/tree/main/pets/gpt`），
   按 `manifest.json`/`actions.json` 里声明的 WebP 逐个取回再打成 `character.<id>`，
   不需要上游提供 zip。默认只取常用动作（≤8 张精灵表，与 curated 下载同口径），
   勾选「下载全部动作」才整包下载。真机 `pets/dsh`：8/8 张、8.0 MB、34.6 s，
   安装后 8 段 clip。

**红线 / 不变量**

- 本地语音仍只直连 `127.0.0.1`（`direct_only=True`），不把台词/路径交给系统代理。
- 参考音频/提示文本是配置不是密钥；`config.json` 不出现任何 Key。
- 目录 URL 只用解析出的 `owner/repo/ref/path` 自己拼 `raw.githubusercontent.com`，
  从不请求用户给的原始主机；`discard()` 只删自己登记的临时目录。
- 既有 `openai` / `simple` 本地协议与 zip 安装路径行为不变。

## 二、修改文件说明

（`git diff --numstat`，2026-11-15 工作区口径）

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/api/chat.py` | +97 / −24 | 新增 `_StreamLines`：`select` 等可读 + `read1` 增量 + 自建行缓冲，替代「0.5 s socket 超时 + `readline`」；TLS 用 `SSLSocket.pending()` 兜住已解密数据；EOF 时把没有换行的最后一截（普通 JSON 响应）交回调用方；删掉会毒化读缓冲的 `_set_read_timeout` / `_readline`。 |
| `petcore/net/aboutme_repo.py`（新） | +230 | 仓库地址、9 个角色包快照、`tree_url()`、`parse_tree_url()`（只认 github.com `<owner>/<repo>/tree/<ref>/<path>`，拒绝 `..`/编码绕过）、`essential_actions()`（常用动作选择，与 curated 同口径）、`safe_relative()`、`raw_file_url()`（中文文件名按 URL 转义）。 |
| `petcore/mods/url_install.py` | +286 / −62 | `is_tree_url` / `fetch_tree`（index + 逐精灵表下载、默认 ≤8 常用动作、可选整包；单文件 16 MB、总量 512 MB、≤400 个、失败清理）；`install_tree` 与 zip 分支共用 `_install_sprite_dir`（staging/回滚）；`discard` 支持删除登记过的临时目录；`_sprite_preview` 抽出复用。 |
| `petcore/net/tts.py` | +46 / −6 | `LOCAL_PROTOCOLS` 加 `gpt_sovits`；`local_synthesize` 新增 `ref_audio/prompt_text/prompt_lang/text_lang`，按 api_v2 形状发 JSON；该协议固定 wav；`PROVIDER_SPECS["local"].fields` 同步。 |
| `petcore/speech/service.py` | +32 / −4 | `_local_settings()` 带新字段（路径 ≤512、文本 ≤500、语言码清洗）；`_flavor()` 缓存键加入参考音频/参考文本/两种语言；`_config_gate()` 对 `gpt_sovits` 要求参考音频；新增 `_short_lang`。 |
| `petcore/config.py` | +33 / −1 | 4 个新键的默认值、`GLOBAL_CONFIG_KEYS`、归一化（`_speech_lang`）；`_LOCAL_PROTOCOLS` 加 `gpt_sovits`。 |
| `petcore/settings_schema.py` | +4 / −0 | 4 个新键的中文标签（设置公开接口/CLI 用）。 |
| `petcore/ui/settings/page_voice.py` | +105 / −3 | 协议下拉新增 GPT-SoVITS；参考音频（文件选择）+ 参考文本 + 参考/合成语言；非该协议时禁用这些行、格式选择禁用并提示固定 wav；新字段接入 `_current/_snapshot/_sync_text_fields/discard_changes`。 |
| `petcore/ui/settings/page_mods.py` | +106 / −16 | 「从链接安装」加 aboutme 角色包下拉、只读 URL、打开/填入按钮与「下载全部动作」开关；组合框独占一行、URL + 两个按钮一行（720×500 下 Mods 页横向滚动仍为 0，见 `test_petcore_extensions_geometry.py`）；`_UrlPreviewJob` 支持目录 URL 与进度；`_UrlInstallJob` 按目录/zip 分派。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_api_chat_stream.py` | +35 / −2 | 新增 `delay_before_payload` 夹具与「慢首字不能被当成死流」回归；既有 22 例全绿。 |
| `tests/test_petcore_speech_local.py` | +118 | gpt_sovits 请求体、缺参考音频不可用、强制 wav 后缀、缓存键随参考音频变化、配置归一化、全局键。 |
| `tests/test_petcore_settings_voice.py` | +52 | 协议切换启用/禁用行与写入独立键；取消恢复 GPT-SoVITS 字段。 |
| `tests/test_petcore_url_install.py` | +240 | 目录 URL 往返/拒绝形态、essential_actions、桩传输的 `fetch_tree`(+整包开关)+`install_tree`、404、`discard` 只删登记目录、设置页填入/打开/默认不整包。 |

### 文档

| 文件 | 改动 |
|---|---|
| `docs/LOCAL-MODELS.md`（新） | llama.cpp 启动参数（含 `--reasoning off`）、桌宠设置映射、实测表；GPT-SoVITS 缺口、api_v2 部署与设置对照、边界。 |
| `docs/PETCORE-CHARACTER-PACKS.md` | §6a 补「扩展管理 → 从链接安装」支持 GitHub 角色目录 URL。 |
| `docs/PETCORE-LOG.md` / `docs/PETCORE-LOG-INDEX.md` | L201 与本条索引。 |
| `docs/INDEX.md` | 登记本报告与 `LOCAL-MODELS.md`。 |
| `docs/SETTINGS-CHANGE-GATES.md` | 变更记录补 GPT-SoVITS 4 个新键（准入、作用域、保存语义、迁移、测试）。 |

### 未改动（刻意）

- `petcore/api/presets.py` 的 `llamacpp` 预设只确认了端口/路径正确，本轮没有改它的
  文案（启动命令写进了 `LOCAL-MODELS.md`，避免把用户路径写死进代码）。
- 没有把 `llama-server` 或 GGUF 打进安装包；没有引入 onnxruntime 之外的推理依赖。
- `petcore/content/data/aboutme_packs.json` 仍是 dsh/dsh1 两个 curated 包（许可文件
  齐全的才进索引）；其他 7 个角色包走目录 URL 安装。

## 三、实现要点

- **为什么 `select` + `read1`**：`readline` 触发的读超时会把 `_io.BufferedReader`
  标记成不可再用；`read1` 在 `select` 说可读后调用，正常情况不会超时，且它已经处理
  chunked 解码。行缓冲放在我们自己的 `bytearray` 里，因此不会出现「select 说有数据、
  但行还在库缓冲里」的等待。TLS 连接额外用 `SSLSocket.pending()` 判断「已解密但
  select 看不见」的缓冲，避免 HTTPS 下白等。
- **取消**：保留 `SOCKET_POLL_SECONDS=0.5` 的轮询节奏（现在只用于 `select` 的等待
  上限），`_start_cancel_watcher` 继续在取消时关闭响应；两种打断方式互不依赖。
- **目录安装的预算**：单文件 16 MB（精灵表实测 ~1.5 MB）、总量 512 MB、≤400 个文件；
  临时目录在 `<data_root>/cache/mods/url-install/tree/tree-*`，`discard` 只删登记的
  路径，另有 24 小时陈旧目录清扫。
- **TTS 缓存**：`gpt_sovits` 固定 wav 后缀与 `media_type`，避免「缓存里是 wav、
  播放器按 mp3 解」的静音；参考音频/提示文本进缓存键，换音色不会命中旧音频。
- **配置**：新增 4 个全局键，语言码只做字符集/长度清洗，不写死版本相关的语言表；
  服务端按自己的版本拒绝并给出可见错误。

## 四、性能分析

环境：Ryzen 7 H 255（8C/16T）、15.3 GB RAM、无 CUDA、Windows 11、Python 3.12.10；
llama.cpp `b12397` CPU x64；模型 `gemma-4-E4B-It` IQ3_XXS（3.71 GB）。

| 指标 | 实测 | 归属 |
|---|---|---|
| llama-server 冷启动加载 | 1.9–3.3 s | 既有（模型加载，不在桌宠进程） |
| 人设预填充（3,595 token） | 17.8–21.8 s，165–202 tok/s | 既有（本地服务） |
| 解码 | 7.4–8.5 tok/s（135 ms/token） | 既有（本地服务） |
| 冷首轮（36–51 token） | 8.6–22.4 s | 既有 |
| 同前缀第二轮 | TTFT 0.15–0.6 s，整轮 3.3–7.5 s | 既有（prompt cache） |
| llama-server 常驻内存 | 2.44–2.93 GB（`-np 1`） | 既有 |
| SSE 读取器修复前 | 每次对话 3 次尝试、每次 ~1 s，全部失败 | 新增（回归修复） |
| SSE 读取器修复后 | 同一请求 8.6 s 成功（364 token 预填充 + 51 token 解码） | 新增 |
| GPT-SoVITS ONNX 解码器加载 | 2.05 s | 新增（仅实测，不进产品） |
| 同上 6.0 s 音频 | 0.67 s（RTF 0.11） | 新增（仅实测） |
| GPT-SoVITS 协议新增开销 | 1 次 HTTP POST + 1 个 JSON 编码；无新线程/定时器 | 新增 |
| aboutme 目录安装（真实网络） | `pets/dsh` 8 张常用精灵表 8.0 MB / 34.6 s；`actions.json` 35 KB / 0.78 s；单张 1.5 MB / 3.2–12.4 s（本机加速代理） | 新增 |

**结论**

1. **稳态开销**：修复后每轮流式读只多一次 `select`（毫秒级）与 64 KB 内存缓冲；
   空闲没有任何新定时器。`gpt_sovits` 只在用户选该协议时走新分支。
2. **新增路径成本**：目录安装是「2 个索引请求 + N 个精灵表请求」，全部串行、无
   GitHub API 配额；单包按素材量 13–130 MB。协议切换只是设置写入。
3. **系统调用/网络/线程**：新增网络只在显式安装动作（后台 QThread，既有模式）；
   语音/对话仍只有原有请求。`_StreamLines` 用 `select` 替代原来的 socket 超时轮询，
   系统调用次数同量级。
4. **内存**：`_StreamLines` 每请求最多多 64 KB 行缓冲；目录安装逐文件写盘、不整流
   读入内存（单文件 16 MB 上限）。桌宠进程本身没有新增常驻线程。

## 五、实机运行记录

1. **GGUF 加载与协议**：`llama-server.exe -m …GEMME…gguf --alias gemma-4-e4b -c 8192
   -np 1 -t 6 --jinja --reasoning off --host 127.0.0.1 --port 9931`；
   `/health` 返回 `{"status":"ok"}`；`/v1/models` 返回
   `{"id":"gemma-4-e4b", "n_ctx":8192, "n_params":7518069290, "ftype":"IQ3_XXS"}`。
2. **未关思考通道的反例**：不加 `--reasoning off` 时，
   `finish_reason=length`、`content=""`、正文全在 `reasoning_content`；桌宠会显示
   空气泡（已在文档写明必须加该参数）。
3. **修复前真机复现**：源码启动的桌宠（隔离数据根）发消息 → 状态「出错：网络不可达」；
   `app.log`：`ai call failed: type=NetworkError reason=network service=local cause=OSError`；
   临时诊断日志：`OSError('cannot read from timed out object')`；服务端在 ~1 s 处
   `stop: cancel task`。
4. **修复后同一窗口**：回复气泡显示「大家好呀，我是Shenshen，一只住在主人桌面上的
   鲸鱼娘女仆。今天我可以听主人聊聊天…」；服务端 timing：
   `prompt eval 2201 ms / 364 tokens (165 tok/s)`、`eval 6391 ms / 51 tokens (7.8 tok/s)`、
   总 8.59 s。
5. **桌宠自己的 `stream_chat`**：冷/热/第 2 轮/取消四例；取消 `end_reason=cancelled`，
   1.09 s 收口。
6. **tools**：带 `tools` 的非流式请求返回 `finish_reason=tool_calls`，参数
   `{"query":"上海天气"}`。
7. **GPT-SoVITS ONNX**：onnxruntime 1.31 加载 2.05 s；T=60/150 两组随机输入，
   RTF 0.10/0.11（仅解码器）。
8. **GPT-SoVITS 协议**：桩服务端校验 POST `/tts` 的 JSON 字段与 wav 产物
   （`tests/test_petcore_speech_local.py`）；**真实 api_v2 未跑**——本机缺 GPT/BERT/
   Hubert/参考音频，已在报告与文档如实标注。
9. **aboutme 目录**：真实 GitHub 直链抽样：`actions.json` 35,181 B / 0.78 s；
   三个精灵表 1,498,764 B / 8.9 s、1,479,288 B / 12.4 s、1,496,420 B / 3.2 s；
   **端到端** `pets/dsh`：8/8 张常用精灵表、8.0 MB、34.6 s，`install_tree` 成功，
   `mods/character.dsh/mod.json` 存在、`videos/` 下 8 个 webp；临时目录已清理。
10. **门禁**：`ruff check petcore tests` 全绿；聚焦 139 passed；全量
    `QT_QPA_PLATFORM=offscreen pytest -q` = **11940 passed, 22 skipped, 7 failed（27:29）**。
    7 例均非本批：4×`test_bundle_slim`（缺 QtWebView DLL）、`test_desktop_pet_features`
    外部品牌引用、`test_packaging_keyring_notices` 的 UTF-8 BOM（L200 已登记），以及
    `test_chat_subsystem::test_append_message_atomic_across_store_instances`（全量负载下偶发，
    单独 3/3 通过）。前一次全量在 `test_quick_chat` 原生 access violation，单独 9/9 通过，
    重跑未复现。

## 六、未验证 / 风险

- 本机网络经过 hosts 加速代理：整包（92 张、约 130 MB）下载耗时不可控，默认只装常用
  8 张；「下载全部动作」在正常网络下按单文件 3–12 s 估算也要数分钟。
- 真实 GPT-SoVITS api_v2 未联调（缺权重/参考音频），音色质量与 CPU 实时率未知。
- 本地 GGUF 的 >8k 上下文、多轮记忆、视觉（无 mmproj）未验证。
- 首次下载目录包没有总体积预估；只在状态行显示「x/N」，超 512 MB 才中止。
- 目录安装依赖上游 `actions.json` 的 `sources.webp` 字段；字段改名需要更新解析。
