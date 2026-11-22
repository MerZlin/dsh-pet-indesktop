# 本地模型：llama.cpp 对话与 GPT-SoVITS 语音

> 基线：`feat/petcore-vpet-architecture`，2026-11-15。实测机器：Ryzen 7 H 255
> （8C/16T）、15.3 GB RAM、Radeon 780M（无 CUDA）、Windows 11、Python 3.12.10。
> 本文只写**本机实测过**的数字；未验证的能力会明确标注。

本仓库**不随包分发** llama.cpp、GGUF 或 GPT-SoVITS 权重：前者是原生二进制
（SAC/签名与体积），后两者是用户自己的模型资产。桌宠只负责「连过去」。

## 一、llama.cpp 本地对话（GGUF）

### 1. 运行时

官方 release 的 Windows CPU 包即可（无需 CUDA）：

```
https://github.com/ggml-org/llama.cpp/releases/download/b12397/llama-b12397-bin-win-cpu-x64.zip
sha256 dcac7cf226b9b19fb0708232eebb11f0e3ce464f86cacae61d66cf8d9d26113f
```

解压后 `llama-server.exe` 与同目录 DLL 一起使用；`--version` 应显示
`version: 1.0.0-dev (build 12397 …)`。太旧的构建不认识 `gemma4` 架构。

### 2. 启动（关键参数）

```
llama-server.exe ^
  -m "D:\26102\Documents\QQ\gemma-4-E4B-it-UD-IQ3_XXS.gguf" ^
  --alias gemma-4-e4b -c 8192 -np 1 -t 6 ^
  --jinja --reasoning off ^
  --host 127.0.0.1 --port 9931
```

- `--reasoning off` **必须**：这是带思考通道的模型，默认会把整段回答写进
  `reasoning_content`。实测未关闭时 `content` 为空、`finish_reason=length`，
  桌宠看到的是「空气泡」。
- `--jinja`：用 GGUF 自带 chat template（支持 system 与 tools）。
- `--alias`：`/v1/models` 返回的模型名，填进桌宠的「模型」一栏。
- `-np 1`：单槽，省一份 KV cache；`-c 8192` 够放 3.5k token 人设 + 历史。
- 端口 9931 与「API 与账户 → 内置预设 → llama.cpp server（本地）」一致。

### 3. 桌宠侧配置

1. 设置 →「API 与账户」→ 内置预设选「llama.cpp server（本地）」→ 保存服务；
   模型名可点「拉取模型」选 `gemma-4-e4b`，或手填。
2. 超时建议 ≥120 秒（`api.json` 的 `timeout`，范围 1–300）。
3. 「AI 对话」设置里把「对话服务」指向该服务（留空 = 第一个服务）。
4. 本地模型建议**关掉联网检索**：每轮会先跑一次 planner 请求（预填充整段对话），
   本地 CPU 上等于多等一次首字。

### 4. 本机实测（2026-11-15）

模型：`gemma-4-E4B-It` / IQ3_XXS / 3.71 GB / 7.5B，`n_ctx_train=131072`。

| 指标 | 实测 | 说明 |
|---|---|---|
| 冷启动加载 | 1.9–3.3 s | 页缓存已热；首次从磁盘读会更久 |
| 人设提示词 | 5,017 字符 → 3,576 token | `persona_presets/anon.json` + 能力边界 |
| 预填充 | 165–202 tok/s | 3,595 token 约 17.8–21.8 s |
| 解码 | 7.4–8.5 tok/s | 135 ms/token |
| 首轮（冷人设，36–51 token） | 8.6–22.4 s | 与人设/历史长度线性相关 |
| 同前缀第二次 | TTFT 0.15–0.6 s；整轮 3.3–7.5 s | llama.cpp 的 prompt cache 生效 |
| 常驻内存 | 2.44–2.93 GB | `-np 1`，启动后空闲 |
| 系统剩余内存 | 3.7 GB | 15.3 GB 机器，模型加载后 |

能力：

- **tools 可用**：`--jinja` 下模型能返回标准 `tool_calls`（实测 `web_search`
  的 `{"query":"上海天气"}`）；联网检索的 planner 轮不会被协议拒绝。
- **system 角色被接受**：人设提示词按 system 发送，回复保持人设与中文口语。
- **视觉不可用**：`/v1/models` 的 capabilities 只有 `completion`，GGUF 没有
  mmproj；「看看屏幕」「截图提问」「主动识屏」对本地服务会失败。
- 回复长度：不传 `max_tokens` 时按模型自己的停止符收尾；实测 20–80 字，
  4B 级事实准确度有限，适合闲聊/角色扮演，不适合当知识库。

### 5. 顺带修掉的传输缺陷

本地模型首字要 1–20 秒，暴露了一个既有 bug：`http.client.HTTPResponse.fp` 是
`_io.BufferedReader`，**一次读超时后**它永久报
`OSError: cannot read from timed out object`。旧实现用 0.5 秒 socket 超时轮询取消，
于是本地服务上每次都读死（真机：三次路由重试都在 1 秒内失败，服务端日志是客户端
主动断开）。现在改成「`select` 等可读 + `read1` 增量 + 自建行缓冲」，TLS 侧再用
`SSLSocket.pending()` 兜住「已解密但 select 看不见」的数据；取消仍由
`_start_cancel_watcher` 关闭响应兜底；回归见
`tests/test_petcore_api_chat_stream.py::test_slow_first_byte_is_not_treated_as_a_dead_stream`。

## 二、GPT-SoVITS 自训语音

### 1. 现有文件够不够？

`vocals_e20_s400_v2Pro.onnx`（262 MB）只是 **SoVITS 解码器**：

- 输入：`codes`（语义 token，1×1×T）、`text`（音素 id）、`refer_spec`（参考音频
  频谱）、`sv_emb`（说话人向量）、`noise_scale`；
- 输出：32 kHz 波形。

完整推理还缺 **GPT（t2s 自回归，文本→codes）、BERT（文本特征）、HuBERT（参考
音频特征）、G2P（jieba/g2pw 音素化）与参考音频**。本机没有 GPT-SoVITS 安装，
`.pth` 也只是 SoVITS 权重，所以**单独拿这个 ONNX 发不出声音**。

本机实测（onnxruntime 1.31，CPU 8 线程，随机输入）：

| 阶段 | 耗时 | 说明 |
|---|---|---|
| 会话加载 | 2.05 s | 262 MB / opset 17 |
| 2.4 s 音频（T=60） | 0.25 s | RTF 0.10 |
| 6.0 s 音频（T=150） | 0.67 s | RTF 0.11 |

所以瓶颈不在这个 ONNX，而在缺的 GPT/BERT/HuBERT。

### 2. 推荐用法：本机跑 api_v2，桌宠连过去

1. 在 **配置了 GPT/SoVITS 权重的 GPT-SoVITS 环境**里启动 API 服务：

   ```
   python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
   ```

   首次推理会加载模型，慢；之后按句返回 wav。`api_v2` 同时支持
   `GET /tts`（查询串）与 `POST /tts`（JSON），桌宠只用 POST。
2. 桌宠：设置 →「语音」→「自定义 / 本地部署 TTS（HTTP）」：

   | 设置项 | 填什么 |
   |---|---|
   | 服务地址 | `http://127.0.0.1:9880` |
   | 协议 | **GPT-SoVITS（api_v2 POST /tts）** |
   | 请求路径 | 留空 = `/tts` |
   | 参考音频 | 3–10 秒干净人声（无伴奏/混响），如 `D:\voices\ref.wav` |
   | 参考文本 | 参考音频里说的那句话（必须一致） |
   | 语言 → 参考 / 合成 | 常见 `zh` / `en` / `ja` / `ko` / `yue` |
   | 音频格式 | 固定 wav（协议忽略此选项） |
   | 超时 | 首次加载模型慢，建议 60–120 秒 |

3. 「语音」页的「语音服务」选「本地部署模型（HTTP）」，或把「语音兜底」留空并
   只把本地模型放在后端链首位；「试听」按钮会走同一条合成链。

请求体（实测断言在 `tests/test_petcore_speech_local.py`）：

```json
{
  "text": "今天也要开心",
  "text_lang": "zh",
  "ref_audio_path": "D:\\voices\\ref.wav",
  "prompt_text": "参考音频里说的话",
  "prompt_lang": "zh",
  "text_split_method": "cut5",
  "speed_factor": 1.0,
  "media_type": "wav",
  "streaming_mode": false
}
```

缓存键包含地址/协议/路径/模型/格式/**参考音频/参考文本/两种语言**：换参考音频
不会再命中上一个音色的缓存；GPT-SoVITS 的产物固定 `.wav`，不会按 mp3 后缀缓存。

### 3. 能不能打包进桌宠？

不建议，也不能只靠这个 ONNX：

- 权重（GPT + BERT-large + HuBERT + SoVITS）是 GB 级，且 BERT/HuBERT/GPT 需要
  torch 运行时；随安装包分发会显著膨胀，还要处理模型许可与 THIRD_PARTY_NOTICES。
- 进程内全 ONNX 需要把 GPT（含 KV cache）、BERT、HuBERT 都导出并接 G2P，是另一个
  项目的工作量；本机没有可用的 GPT 权重，无法验证。
- 当前路线是「本地服务 + 桌宠连过去」：桌宠零权重新增，换模型/音色只改服务端。

## 三、边界与未验证项

- 本机未跑通**真实** GPT-SoVITS api_v2（缺 GPT/BERT/HuBERT 与参考音频）；
  协议用桩服务端验证，真实音色质量与 CPU 实时率需要在有完整权重的机器上复测。
- 本地 GGUF 的视觉、长上下文（>8k）与多轮记忆未做压力测试；实测都是 8k 上下文。
- 机器差异极大：本文所有 tok/s 只对这台 Ryzen 7 H 255 + IQ3_XXS 有效，别当通用数字。
- 参考音频路径与提示文本存 `config.json`（不是密钥）；语音服务仍只直连
  `127.0.0.1`，不走系统代理。

相关实现：[`petcore/net/tts.py`](../petcore/net/tts.py)、
[`petcore/speech/service.py`](../petcore/speech/service.py)、
[`petcore/ui/settings/page_voice.py`](../petcore/ui/settings/page_voice.py)、
[`petcore/api/chat.py`](../petcore/api/chat.py)、
[`petcore/api/presets.py`](../petcore/api/presets.py)。
