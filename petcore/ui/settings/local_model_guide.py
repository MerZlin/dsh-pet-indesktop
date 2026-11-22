"""API 页「本地模型指南」的静态文案（随包 import，不依赖 docs/ 目录）。"""

from __future__ import annotations

import sys
from pathlib import Path

from ... import paths

LOCAL_MODEL_GUIDE_KEYWORDS: tuple[str, ...] = (
    "本地模型",
    "本地部署",
    "本地大模型",
    "GGUF",
    "llama.cpp",
    "llama-server",
    "Ollama",
    "LM Studio",
    "vLLM",
    "GPT-SoVITS",
    "api_v2",
    "本地语音",
    "本地 TTS",
    "复制给 AI",
    "local",
)

LOCAL_MODEL_GUIDE_TEMPLATE = """我是这台电脑上「DSH 桌宠（petcore）」的用户，想使用本地部署的大模型（对话）和本地语音生成模型（TTS）。
请用中文帮我一步步完成部署与配置：先询问并确认我电脑上的硬件条件（CPU / 内存 / 显卡与显存）以及我手头已有的模型文件路径，再按下面的桌宠规则给我可执行命令和设置填写指导。

## 0. 本机路径与核心原理（给 AI 的约束）
- 程序目录：{program_dir}
- 数据目录：{data_root}
- API 服务配置：{api_json_path}
- 主设置文件：{config_path}

重要前提：
1. 桌宠本体**不会**直接加载 `.gguf`、`.onnx` 或 `.pth` 模型文件（设置里没有填本地模型文件路径的地方），而是通过 **本机 HTTP（`http://127.0.0.1:<端口>`）** 连接到你先在本机启动的推理服务。
2. 桌宠连接 `127.0.0.1` / `localhost` 默认走直连、不经过系统代理；如需连接同一局域网另一台机器的 `http://192.168.x.x` / `10.x.x.x`，请在服务设置中勾选「允许局域网 http」。
3. 不要把 API Key、Cookie 或密码写进 `api.json` / `config.json`（本地服务 Key 留空即可）。

---

## 1. 本地部署对话模型（GGUF / llama.cpp / Ollama / LM Studio / vLLM）

### 方式 A：使用 `llama-server.exe` 直接跑 `.gguf` 文件（CPU 也能跑，无需 CUDA）
1. 准备支持目标模型架构的较新版 `llama.cpp`（例如 Gemma 4 架构需 `b12397` 或更新版本的 Windows 发行包）。
2. 在终端启动本地服务端（把 `<模型路径.gguf>` 换成你的实际 `.gguf` 文件路径）：
   ```powershell
   llama-server.exe -m "<模型路径.gguf>" --alias local-model -c 8192 -np 1 -t 6 --jinja --reasoning off --host 127.0.0.1 --port 9931
   ```
   关键参数说明：
   - `--reasoning off`：**带思考通道的模型必须加这个参数**（例如 Gemma 4 / Qwen 思考模型），否则模型会把内容全写进 `reasoning_content`，导致桌宠收到空正文（「空气泡」）；桌宠无法替 `llama-server` 关掉思考通道。
   - `--jinja`：使用 GGUF 自带的对话模板（支持 system 与工具结构）。
   - `--alias local-model`：`/v1/models` 返回的模型名，填进桌宠设置「模型」一栏。
   - `-c 8192 -np 1`：单并发槽位 + 8K 上下文，可容纳较长人设提示词与历史记录并省内存。

### 方式 B：其它常见本地服务端与默认地址
| 本地服务端 | 桌宠内置预设名称 | 默认 Base URL | 备注 |
|---|---|---|---|
| llama.cpp | `llama.cpp server（本地）` | `http://127.0.0.1:9931` | 先启动 `llama-server.exe` |
| Ollama | `Ollama（本地）` | `http://127.0.0.1:11434` | 先运行 `ollama serve` 与 `ollama pull <模型>` |
| LM Studio | `LM Studio（本地）` | `http://127.0.0.1:1234` | 在 LM Studio 的 Developer 页开启 Local Server |
| vLLM | `vLLM（本地）` | `http://127.0.0.1:8000` | 先启动 `vllm serve <模型>` |

### 桌宠里对话模型在哪里填、怎么填
1. 打开 **设置 → API 与账户 → 服务配置**（当前页第一个分页）：
   - 点左侧服务列表下方 **新增**（保留你原有的线上服务不覆盖）。
   - **内置预设**：选上面表格对应的本地服务（例如 `llama.cpp server（本地）`），会自动填好 `Base URL`、`Chat Path`（`/v1/chat/completions`）、`模型列表路径`（`/v1/models`）与 `余额协议`（`无`）。
   - **名称**：填方便辨认的名称（例如 `本地 Gemma 4`）。
   - **模型**：先确保本地服务已启动，点下方 **拉取模型** 后在 **模型列表** 下拉选中（或手填 `--alias` / 服务里的模型名，例如 `local-model`）。
   - **视觉模型**：如果该模型没有加载多模态视觉权重（mmproj），请**留空**。
   - **超时**：本地 CPU 首次处理长人设预填充可能较慢，建议调到 **120 秒**（范围 1–300 秒）。
   - **API Key**：**留空**。
   - 点右下角 **测试连接** 确认绿色连通，再点 **保存服务**。
2. 打开 **设置 → 扩展管理 → 加装扩展 → AI 对话**：
   - **模型 → 对话服务**：选择刚才保存的本地服务（**模型覆盖** 保持留空）。
   - **联网搜索 → 允许联网搜索**：本地 CPU 部署**建议取消勾选**，否则每轮回答前会多跑一次搜索意图判断，首字等待时间加倍。
   - **人格与视觉 → 允许截图提问** 与 **主动识屏 → 启用主动识屏**：若模型无视觉能力，建议取消勾选。

---

## 2. 本地部署语音生成模型（TTS HTTP）

桌宠的 **设置 → 语音** 支持三类本地 HTTP 语音协议：`GPT-SoVITS（api_v2 POST /tts）`、`OpenAI 兼容（/v1/audio/speech）` 与 `通用 JSON（{text, voice, speed}）`。

### 方式 A：GPT-SoVITS（`api_v2 POST /tts`）
- **重要前置条件**：**仅有单个 SoVITS 解码器 `.onnx` 或 `.pth` 文件不能直接从文字发出声音**。完整推理必须具备整套 GPT-SoVITS 环境及：
  1. GPT 权重（`t2s` `.ckpt`）与配套 SoVITS 权重（`.pth`）；
  2. BERT、HuBERT 特征模型与 G2P（文本音素化）依赖；
  3. 一段 3–10 秒干净无伴奏、无混响的参考人声 `.wav` 及其对应的准确参考文本。
- 在配置好权重的 GPT-SoVITS 环境中启动服务端：
  ```powershell
  python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
  ```

### 方式 B：OpenAI 兼容 / 通用 JSON 本地语音服务
- 如使用 Kokoro-FastAPI、openedai-speech、LocalAI 或自写简单 HTTP TTS 服务，按其文档启动本机 HTTP 服务并记录监听端口、接口路径与返回音频格式（`mp3` / `wav`）。

### 桌宠里语音模型在哪里填、怎么填
打开 **设置 → 语音**：
1. 先填写 **自定义 / 本地部署 TTS（HTTP）** 卡片：
   - **服务地址**：填 `http://127.0.0.1:9880`（按实际服务端口填写；不要带查询串或 `#`）。
   - **协议**：GPT-SoVITS 选 **`GPT-SoVITS（api_v2 POST /tts）`**；其它按服务实际接口选择 **`OpenAI 兼容（/v1/audio/speech）`** 或 **`通用 JSON（{text, voice, speed}）`**。
   - **请求路径**：留空按协议默认（GPT-SoVITS 默认 `/tts`，OpenAI 兼容默认 `/v1/audio/speech`）。
   - **模型名 / 音频格式**：OpenAI 兼容协议按服务端要求填写；GPT-SoVITS 固定返回 `wav`（页面会自动禁用格式选择）。
   - **超时**：首次加载权重可能较慢，建议调至 **60–120 秒**。
   - **GPT-SoVITS 必填项**：
     - **参考音频**：点 **选择文件…** 选中 3–10 秒干净参考音频；
     - **参考文本**：准确填写参考音频里说的原话；
     - **语言 → 参考 / 合成**：分别选择对应语种（如 `中文`、`English`、`日本語`）。
   - **本地 API Key**：通常留空。
   - 点卡片底部的 **测试本地模型** 确认可以成功返回音频。
2. 再到上方 **语音后端** 卡片切换主后端：
   - **主后端**：改为 **`本地部署模型（HTTP）`**；
   - **回退后端**：可按需选 **`Windows 本地语音（离线，毫秒级）`** 或 **`不回退`**；
   - 点 **试听** 验证确实能出声后保存设置。

---

## 3. 给 AI 协助用户时的排查与修改规则
- 验证对话服务是否就绪：请求 `GET http://127.0.0.1:<端口>/health` 与 `GET http://127.0.0.1:<端口>/v1/models`。
- 对话只吐空白气泡：优先检查是否漏加 `--reasoning off`，或模型名与 `模型` 一栏不匹配。
- 首字超过 30 秒报超时：把「API 与账户 → 超时」调高到 `120–300` 秒，并关闭「AI 对话 → 允许联网搜索」。
- 用户只有一个 `.onnx` / `.pth` 想接语音：如实告知缺少 GPT（`.ckpt`）、BERT、HuBERT、G2P 与参考音频，不能直接合成；请先引导补齐 GPT-SoVITS 完整服务端。
- 需要通过 CLI 帮用户读写桌宠设置键时，引导用户打开 **设置 → 使用手册 → 给 AI 的提示词** 获取 `petcore settings` 命令行规则；严禁擅自把密钥写入 `api.json` 或 `config.json`。
"""


def _resolve_program_dir(program_dir: Path | str | None = None) -> str:
    if program_dir is not None and str(program_dir).strip():
        return str(program_dir)
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    try:
        return str(paths.repo_root())
    except Exception:  # noqa: BLE001 - 非源码目录运行回退到工作目录
        return str(Path.cwd())


def render_local_model_guide(
    data_root: Path | str,
    *,
    program_dir: Path | str | None = None,
) -> str:
    """渲染包含真实本机路径的可复制本地部署模型指南文本。"""
    root = Path(str(data_root))
    text = LOCAL_MODEL_GUIDE_TEMPLATE
    for placeholder, value in (
        ("{program_dir}", _resolve_program_dir(program_dir)),
        ("{data_root}", str(root)),
        ("{api_json_path}", str(root / "api.json")),
        ("{config_path}", str(root / "config.json")),
    ):
        text = text.replace(placeholder, value)
    return text
