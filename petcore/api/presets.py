"""内置 API 服务预设（data-over-code）。

每个预设只填「连哪儿、模型名、路径」；密钥仍由用户填到钥匙串。
opencode 免费档要求请求来自 OpenCode 客户端，非客户端大概率 429：预设保留，
但 DeepSeek 仍是默认第一项。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .spec import ApiService


@dataclass(frozen=True)
class ServicePreset:
    id: str
    name: str
    base_url: str
    chat_path: str
    models_path: str
    model: str = ""
    vision_model: str = ""
    balance_protocol: str = "none"
    extra_headers: dict[str, str] = field(default_factory=dict)
    allow_lan_http: bool = False
    note: str = ""


PRESETS: tuple[ServicePreset, ...] = (
    ServicePreset(
        id="deepseek",
        name="DeepSeek 官方",
        base_url="https://api.deepseek.com",
        chat_path="/v1/chat/completions",
        models_path="/v1/models",
        model="deepseek-v4-flash",
        vision_model="deepseek-v4-flash-vision-exp",
        balance_protocol="deepseek",
        note="默认；只需填 API Key。",
    ),
    ServicePreset(
        id="opencode-free",
        name="OpenCode Zen（免费档·实验）",
        base_url="https://opencode.ai/inference",
        chat_path="/openai/v1/chat/completions",
        models_path="/v1/models",
        model="deepseek-v4.1-flash-free",
        vision_model="nemotron-3.5-super-vl-free",
        note="免费模型官方要求 OpenCode 客户端；非客户端可能 429。失败请切回 DeepSeek 或本地模型。",
    ),
    ServicePreset(
        id="opencode-go",
        name="OpenCode Go（需 Key）",
        base_url="https://opencode.ai/inference",
        chat_path="/go/openai/v1/chat/completions",
        models_path="/go/v1/models",
        model="glm-5.3-flash",
        vision_model="nemotron-3.5-super-vl-free",
        note="订阅 Go 后把 API Key 填进钥匙串。",
    ),
    ServicePreset(
        id="ollama",
        name="Ollama（本地）",
        base_url="http://127.0.0.1:11434",
        chat_path="/v1/chat/completions",
        models_path="/v1/models",
        model="",
        note="先 ollama serve；模型名用 /v1/models 拉取。",
    ),
    ServicePreset(
        id="lmstudio",
        name="LM Studio（本地）",
        base_url="http://127.0.0.1:1234",
        chat_path="/v1/chat/completions",
        models_path="/v1/models",
        model="",
        note="在 LM Studio 打开本地服务器（Developer 页）。",
    ),
    ServicePreset(
        id="llamacpp",
        name="llama.cpp server（本地）",
        base_url="http://127.0.0.1:9931",
        chat_path="/v1/chat/completions",
        models_path="/v1/models",
        model="",
        note="llama.cpp 新版默认端口 9931。",
    ),
    ServicePreset(
        id="vllm",
        name="vLLM（本地）",
        base_url="http://127.0.0.1:8000",
        chat_path="/v1/chat/completions",
        models_path="/v1/models",
        model="",
        note="vllm serve 默认监听 8000。",
    ),
)

PRESET_BY_ID: dict[str, ServicePreset] = {item.id: item for item in PRESETS}
DEFAULT_PRESET = "deepseek"


def preset_for(preset_id: object) -> ServicePreset:
    return PRESET_BY_ID.get(str(preset_id or ""), PRESET_BY_ID[DEFAULT_PRESET])


def service_from_preset(
    preset: ServicePreset, service_id: str = "", name: str = ""
) -> ApiService:
    return ApiService(
        service_id=str(service_id or preset.id),
        name=str(name or preset.name),
        base_url=preset.base_url,
        chat_path=preset.chat_path,
        models_path=preset.models_path,
        model=preset.model,
        vision_model=preset.vision_model,
        balance_protocol=preset.balance_protocol,
        extra_headers=dict(preset.extra_headers),
        allow_lan_http=preset.allow_lan_http,
        preset=preset.id,
    ).validate()


def seed_services() -> tuple[ApiService, ...]:
    """首次启动播种：DeepSeek 默认 + OpenCode 免费可选。"""
    return (
        service_from_preset(PRESET_BY_ID["deepseek"]),
        service_from_preset(PRESET_BY_ID["opencode-free"]),
    )
