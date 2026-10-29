"""语音合成的联网后端：core 里唯一允许 import 第三方 TTS 栈的地方。

mod 只经 ``HostPorts.speech``（``petcore.contracts.speech.SpeechPort``）说话，
合成、重试、缓存与凭据都在 core。本模块只做「一次合成」：

- 纯函数式的 provider 声明（``PROVIDER_SPECS`` / ``DEFAULT_VOICES``），
  服务层据此生成端口列表、校验音色、决定缓存后缀；
- 同步的 ``*_synthesize`` 函数（无 Qt、无事件循环要求，调用方放在后台线程）；
- 失败一律收敛成 :class:`petcore.net.errors.NetworkError` 子类（``reason`` 稳定），
  **不把异常正文、响应体、密钥或代理地址写进消息与日志**。

``edge_tts`` 只在真正合成时惰性 import：首次 import 实测秒级，而语音默认可能
根本不用。``urllib`` 只经 :mod:`petcore.net.http` 的统一出口（带代理/直连兜底
与失败分类）。
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import os
import threading
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..contracts.speech import SpeechRequest
from .errors import (
    AUTH,
    NETWORK,
    PROTOCOL,
    TIMEOUT,
    NetworkError,
    NetworkTimeout,
    classify_exception,
)
from .http import fetch_url

logger = logging.getLogger("petcore.net.tts")

#: `edge_tts` 的首次 import 必须串行：它会在模块级建 SSL 上下文，多个播报线程同时
#: 首次 import 时在 Windows 上偶发原生 access violation（2026-10-29 全量套件三次两崩，
#: 栈全在 importlib + ssl.create_default_context）。串行只发生在第一次，之后是普通 import。
_EDGE_IMPORT_LOCK = threading.Lock()

EDGE_TIMEOUT_S = 60.0
DOUBAO_TIMEOUT_S = 60.0
DOUBAO_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
DOUBAO_RESOURCE_ID = "seed-tts-2.0"
DOUBAO_SAMPLE_RATE = 24000
DOUBAO_MAX_BYTES = 8_000_000
MIMO_BASE_URL = "https://api.xiaomimimo.com"
MIMO_SPEECH_PATH = "/v1/audio/speech"
MIMO_MODEL = "mimo-v2.5-tts"
MIMO_TIMEOUT_S = 30.0
MIMO_MAX_BYTES = 8_000_000

LOCAL_TIMEOUT_S = 30.0
LOCAL_MAX_BYTES = 8_000_000
#: 本地服务两种形状：OpenAI 兼容 / 可配置路径的简单 JSON。
LOCAL_PROTOCOLS = ("openai", "simple")
LOCAL_DEFAULT_PATHS = {"openai": "/v1/audio/speech", "simple": "/tts"}


@dataclass(frozen=True, slots=True)
class TtsProviderSpec:
    """一个后端的声明：展示名、是否需要联网、配置字段与默认音色。

    ``dependency`` 是可选第三方模块（``importlib.util.find_spec`` 探测，不 import）；
    ``platform`` 非空时还要求 ``os.name`` 相符（sapi 只在 Windows 可用）；
    ``secret_fields`` 是存进系统钥匙串的字段名（值绝不进 ``config.json``）；
    ``optional_secrets`` 里的字段可留空（本地服务的 API Key 通常不需要）；
    ``suffix`` 是产物音频的真实扩展名（缓存文件按它落盘，播放器才不会误判）。
    """

    id: str
    label: str
    online: bool
    fields: tuple[str, ...] = ()
    default_voice: str = ""
    suffix: str = "mp3"
    dependency: str = ""
    platform: str = ""
    secret_fields: tuple[str, ...] = ()
    optional_secrets: tuple[str, ...] = ()
    voice_options: tuple[tuple[str, str], ...] = ()


#: 微软在线音色（2026-09-22 按在线表重建；已下架的 10 款不再列出）。
EDGE_VOICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("zh-CN-XiaoxiaoNeural", "晓晓（女 · 自然）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女 · 活泼）"),
    ("zh-CN-YunjianNeural", "云健（男 · 浑厚）"),
    ("zh-CN-YunxiNeural", "云希（男 · 阳光）"),
    ("zh-CN-YunxiaNeural", "云夏（男 · 少年）"),
    ("zh-CN-YunyangNeural", "云扬（男 · 新闻播报）"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北（女 · 东北腔）"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮（女 · 陕西腔）"),
    ("zh-TW-HsiaoChenNeural", "曉臻（女 · 台灣腔）"),
    ("zh-TW-HsiaoYuNeural", "曉雨（女 · 台灣腔）"),
    ("zh-TW-YunJheNeural", "雲哲（男 · 台灣腔）"),
    ("zh-HK-HiuGaaiNeural", "曉佳（女 · 粵語）"),
    ("zh-HK-HiuMaanNeural", "曉文（女 · 粵語）"),
    ("zh-HK-WanLungNeural", "雲龍（男 · 粵語）"),
    ("en-US-AriaNeural", "Aria（英文女声 · 自然）"),
    ("en-US-JennyNeural", "Jenny（英文女声 · 甜美）"),
    ("en-US-MichelleNeural", "Michelle（英文女声 · 温暖）"),
    ("en-US-AnaNeural", "Ana（英文女声 · 童声）"),
    ("en-US-AvaNeural", "Ava（英文女声 · 新一代）"),
    ("en-US-EmmaNeural", "Emma（英文女声 · 新一代）"),
    ("en-US-GuyNeural", "Guy（英文男声 · 沉稳）"),
    ("en-US-ChristopherNeural", "Christopher（英文男声 · 沉稳）"),
    ("en-US-EricNeural", "Eric（英文男声 · 年轻）"),
    ("en-US-RogerNeural", "Roger（英文男声 · 成熟）"),
    ("en-US-AndrewNeural", "Andrew（英文男声 · 新一代）"),
    ("en-US-BrianNeural", "Brian（英文男声 · 新一代）"),
    ("en-US-SteffanNeural", "Steffan（英文男声 · 沉稳）"),
    ("en-US-AvaMultilingualNeural", "Ava（多语言女声）"),
    ("en-US-EmmaMultilingualNeural", "Emma（多语言女声）"),
    ("en-US-AndrewMultilingualNeural", "Andrew（多语言男声）"),
    ("en-US-BrianMultilingualNeural", "Brian（多语言男声）"),
    ("en-GB-SoniaNeural", "Sonia（英音女声）"),
    ("en-GB-RyanNeural", "Ryan（英音男声）"),
    ("en-GB-LibbyNeural", "Libby（英音女声）"),
    ("en-GB-MaisieNeural", "Maisie（英音女童）"),
    ("en-GB-ThomasNeural", "Thomas（英音男声）"),
)

DOUBAO_VOICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("zh_female_peiqi_uranus_bigtts", "佩奇猪（儿童）"),
    ("zh_female_xiaoxue_uranus_bigtts", "儿童绘本"),
    ("zh_male_tiancaitongsheng_uranus_bigtts", "天才童声"),
    ("zh_female_yingtaowanzi_uranus_bigtts", "樱桃丸子"),
    ("zh_female_vv_uranus_bigtts", "Vivi（通用女声）"),
    ("zh_female_sophie_uranus_bigtts", "魅力苏菲"),
)

MIMO_VOICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("冰糖", "冰糖（女 · 中文默认）"),
    ("茉莉", "茉莉（女 · 中文）"),
    ("苏打", "苏打（男 · 中文）"),
    ("白桦", "白桦（男 · 中文）"),
    ("Mia", "Mia（女 · 英文）"),
    ("Chloe", "Chloe（女 · 英文）"),
    ("Milo", "Milo（男 · 英文）"),
    ("Dean", "Dean（男 · 英文）"),
)


PROVIDER_SPECS: dict[str, TtsProviderSpec] = {
    spec.id: spec
    for spec in (
        TtsProviderSpec(
            id="edge",
            label="edge-tts（微软 · 免 Key）",
            online=True,
            fields=("voice", "rate", "pitch", "volume"),
            default_voice="zh-CN-XiaoxiaoNeural",
            suffix="mp3",
            dependency="edge_tts",
            voice_options=EDGE_VOICE_OPTIONS,
        ),
        TtsProviderSpec(
            id="sapi",
            label="Windows 本地语音（离线，毫秒级）",
            online=False,
            fields=("voice", "rate", "volume"),
            default_voice="",
            suffix="wav",
            dependency="comtypes",
            platform="nt",
        ),
        TtsProviderSpec(
            id="doubao",
            label="豆包语音 2.0（需 API Key）",
            online=True,
            fields=("voice", "api_key"),
            default_voice="zh_female_peiqi_uranus_bigtts",
            suffix="mp3",
            secret_fields=("api_key",),
            voice_options=DOUBAO_VOICE_OPTIONS,
        ),
        TtsProviderSpec(
            id="mimo",
            label="小米 MiMo（需 API Key）",
            online=True,
            fields=("voice", "api_key"),
            default_voice="冰糖",
            suffix="mp3",
            secret_fields=("api_key",),
            voice_options=MIMO_VOICE_OPTIONS,
        ),
        TtsProviderSpec(
            id="local",
            label="本地部署模型（HTTP）",
            online=False,
            fields=("endpoint", "protocol", "path", "model", "voice", "format", "timeout", "api_key"),
            default_voice="",
            suffix="mp3",
            secret_fields=("api_key",),
            optional_secrets=("api_key",),
        ),
    )
}

DEFAULT_VOICES: dict[str, str] = {
    provider_id: spec.default_voice for provider_id, spec in PROVIDER_SPECS.items()
}


# ---------------------------------------------------------------- 可用性（离线）


def dependency_present(module: str) -> bool:
    """``find_spec`` 探测（**不 import 本体**）；父包缺失等异常一律按缺失处理。"""
    if not module:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def provider_available(provider_id: str) -> tuple[bool, str]:
    """``(可用, 不可用原因码)``；只看平台与可选依赖，不联网、不读凭据。"""
    spec = PROVIDER_SPECS.get(str(provider_id or "").strip().lower())
    if spec is None:
        return False, "unavailable"
    if spec.platform and os.name != spec.platform:
        return False, "unavailable"
    if spec.dependency and not dependency_present(spec.dependency):
        return False, "unavailable"
    return True, ""


def edge_known_voices() -> frozenset[str]:
    """内置音色集合（离线；用于把已下架音色换成默认音色）。"""
    return frozenset(name for name, _label in EDGE_VOICE_OPTIONS)


def clean_voice(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text[:64] if text else str(default or "")


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return max(low, min(high, number))


def edge_rate_arg(rate: Any) -> str:
    """倍率（0.5–2.0）→ edge-tts 的 ``+x%`` 偏移。"""
    percent = round((_clamp(rate, 0.5, 2.0, 1.0) - 1.0) * 100)
    percent = max(-100, min(100, percent))
    return f"{percent:+d}%"


def edge_pitch_arg(pitch: Any) -> str:
    """音调（Hz 偏移，-50..50）→ ``+xHz``。"""
    value = round(_clamp(pitch, -50.0, 50.0, 0.0))
    return f"{value:+d}Hz"


def edge_volume_arg(volume: Any) -> str:
    """音量（0.0–1.0）→ edge-tts 的百分比偏移（0.0 = -100%，1.0 = +0%）。"""
    percent = round((_clamp(volume, 0.0, 1.0, 1.0) - 1.0) * 100)
    percent = max(-100, min(100, percent))
    return f"{percent:+d}%"


def _proxy_url() -> str | None:
    """系统代理（edge-tts 的 aiohttp 不读环境变量，必须显式传）。

    返回值只在 ``Communicate`` 里用一次，绝不写日志。
    """
    try:
        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - 代理探测失败按直连处理
        return None
    return proxies.get("https") or proxies.get("http") or None


def _map_error(exc: BaseException) -> NetworkError:
    """异常 → 稳定分类；消息只带异常类型名（正文可能含令牌/签名 URL）。"""
    reason = classify_exception(exc)
    if reason is None:
        name = type(exc).__name__.lower()
        module = type(exc).__module__.lower()
        if "timeout" in name:
            reason = TIMEOUT
        elif "aiohttp" in module or "connect" in name:
            reason = NETWORK
        else:
            reason = PROTOCOL
    return NetworkError(type(exc).__name__, reason=reason)


def _require_audio(path: Path, provider: str, *, truncated: bool = False) -> None:
    """音频产物必须非空且**完整**。

    传输层会标记 ``Body.truncated``（响应被 ``max_bytes`` 截断，或实际字节数少于
    ``Content-Length``）；半截 mp3 一旦原子写进缓存，同文本以后就永远播那段残缺音频。
    """
    if truncated:
        raise NetworkError(f"{provider} 返回的音频不完整", reason=PROTOCOL)
    try:
        size = Path(path).stat().st_size
    except OSError:
        raise NetworkError(f"{provider} 没有产出音频", reason=PROTOCOL) from None
    if size <= 0:
        raise NetworkError(f"{provider} 返回了空音频", reason=PROTOCOL)


# ---------------------------------------------------------------- edge-tts


def edge_synthesize(
    text: str,
    *,
    voice: str = "",
    rate: float = 1.0,
    pitch: float = 0.0,
    volume: float = 1.0,
    out_path: Path | str,
    timeout: float = EDGE_TIMEOUT_S,
) -> None:
    """微软在线音色合成到 ``out_path``（mp3）。"""
    try:
        with _EDGE_IMPORT_LOCK:
            import edge_tts
    except Exception as exc:  # noqa: BLE001 - 缺依赖统一转成 unavailable
        raise NetworkError("edge-tts 不可导入", reason="unavailable") from exc

    target = clean_voice(voice, default=DEFAULT_VOICES["edge"])
    if target not in edge_known_voices():
        # 合成层再兜一次底：配置里存着已下架音色时不要直接失败。
        target = DEFAULT_VOICES["edge"]
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    proxy = _proxy_url()

    async def _save() -> None:
        communicate = edge_tts.Communicate(
            str(text or ""),
            target,
            rate=edge_rate_arg(rate),
            pitch=edge_pitch_arg(pitch),
            volume=edge_volume_arg(volume),
            proxy=proxy,
        )
        await communicate.save(str(destination))

    try:
        asyncio.run(
            asyncio.wait_for(_save(), timeout=max(1.0, float(timeout)))
        )
    except asyncio.TimeoutError as exc:
        raise NetworkTimeout("edge-tts 合成超时") from exc
    except Exception as exc:  # noqa: BLE001 - 分类后只留类型名
        mapped = _map_error(exc)
        logger.debug("edge-tts 合成失败：%s（%s）", type(exc).__name__, mapped.reason)
        raise mapped from exc
    _require_audio(destination, "edge-tts")


# ---------------------------------------------------------------- 豆包（火山引擎）


def doubao_synthesize(
    text: str,
    *,
    voice: str = "",
    api_key: str = "",
    rate: float = 1.0,
    pitch: float = 0.0,
    volume: float = 1.0,
    out_path: Path | str,
    timeout: float = DOUBAO_TIMEOUT_S,
) -> None:
    """火山引擎语音 2.0（SSE，逐片 base64）合成到 ``out_path``。

    ``rate``/``volume``/``pitch`` 该接口都不吃：变调需要 ffmpeg 子进程，本子系统
    不做（主分支的 ``asetrate`` 变调没有随 P4-b 移植，配置里也没有音调项）。
    """
    del rate, pitch, volume
    key = str(api_key or "").strip()
    if not key:
        raise NetworkError("豆包 API Key 未配置", reason=AUTH)

    speaker = clean_voice(voice, default=DEFAULT_VOICES["doubao"])
    body = {
        "user": {"uid": "dsh-pet"},
        "req_params": {
            "text": str(text or ""),
            "speaker": speaker,
            "audio_params": {"format": "mp3", "sample_rate": DOUBAO_SAMPLE_RATE},
        },
    }
    headers = (
        ("Content-Type", "application/json"),
        ("X-Api-Key", key),
        ("X-Api-Resource-Id", DOUBAO_RESOURCE_ID),
        ("X-Api-Request-Id", str(uuid.uuid4())),
    )
    try:
        _status, _headers, payload = fetch_url(
            DOUBAO_ENDPOINT,
            method="POST",
            headers=headers,
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
            max_bytes=DOUBAO_MAX_BYTES,
        )
    except NetworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - 传输层意外形态
        raise _map_error(exc) from exc

    audio = _parse_doubao_sse(payload)
    if not audio:
        raise NetworkError("豆包没有返回音频", reason=PROTOCOL)
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(audio)
    _require_audio(destination, "豆包", truncated=bool(getattr(payload, "truncated", False)))


def _parse_doubao_sse(payload: bytes) -> bytes:
    """SSE 逐行解析：音频片段 base64 拼接；非 0 码只保留数值（不回显 message）。"""
    chunks: list[bytes] = []
    for line in payload.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("event:", ":")):
            continue
        raw = line[5:].strip() if line.startswith("data:") else line
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(frame, dict):
            continue
        data = frame.get("data")
        if isinstance(data, str) and data:
            try:
                chunks.append(base64.b64decode(data))
            except (ValueError, TypeError):
                continue
            continue
        code = frame.get("code")
        if code not in (None, 0, 20000000):
            reason = AUTH if code in (401, 403) else PROTOCOL
            raise NetworkError(f"豆包返回 {code}", reason=reason)
    return b"".join(chunks)


# ---------------------------------------------------------------- 小米 MiMo


def mimo_synthesize(
    text: str,
    *,
    voice: str = "",
    api_key: str = "",
    rate: float = 1.0,
    pitch: float = 0.0,
    volume: float = 1.0,
    out_path: Path | str,
    timeout: float = MIMO_TIMEOUT_S,
) -> None:
    """小米 MiMo（OpenAI 兼容 ``/v1/audio/speech``）合成到 ``out_path``。

    响应既可能是音频字节，也可能是主分支用过的 chat/completions 形状
    （``choices[0].message.audio.data`` 的 base64），两种都认。
    """
    del pitch, volume
    key = str(api_key or "").strip()
    if not key:
        raise NetworkError("MiMo API Key 未配置", reason=AUTH)

    body: dict[str, Any] = {
        "model": MIMO_MODEL,
        "input": str(text or ""),
        "voice": clean_voice(voice, default=DEFAULT_VOICES["mimo"]),
        "response_format": "mp3",
    }
    speed = _clamp(rate, 0.25, 4.0, 1.0)
    if abs(speed - 1.0) > 0.01:
        body["speed"] = speed
    headers = (
        ("Content-Type", "application/json"),
        ("Accept", "audio/mpeg, application/json"),
        ("api-key", key),
        ("Authorization", f"Bearer {key}"),
    )
    try:
        _status, response_headers, payload = fetch_url(
            MIMO_BASE_URL.rstrip("/") + MIMO_SPEECH_PATH,
            method="POST",
            headers=headers,
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
            max_bytes=MIMO_MAX_BYTES,
        )
    except NetworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - 传输层意外形态
        raise _map_error(exc) from exc

    content_type = ""
    for name, value in response_headers:
        if name.lower() == "content-type":
            content_type = str(value).lower()
            break
    audio = payload
    if "json" in content_type or payload.lstrip()[:1] == b"{":
        audio = _parse_mimo_json(payload)
    if not audio:
        raise NetworkError("MiMo 没有返回音频", reason=PROTOCOL)
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(audio)
    _require_audio(destination, "MiMo", truncated=bool(getattr(payload, "truncated", False)))


def _parse_mimo_json(payload: bytes) -> bytes:
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        raise NetworkError("MiMo 返回了无法解析的响应", reason=PROTOCOL) from None
    if not isinstance(data, dict):
        raise NetworkError("MiMo 返回了无法解析的响应", reason=PROTOCOL)
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        audio = message.get("audio") if isinstance(message, dict) else None
        encoded = audio.get("data") if isinstance(audio, dict) else None
        if encoded:
            try:
                return base64.b64decode(str(encoded))
            except (ValueError, TypeError):
                raise NetworkError("MiMo 音频解码失败", reason=PROTOCOL) from None
        raise NetworkError("MiMo 响应里没有音频", reason=PROTOCOL)
    error = data.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        reason = AUTH if str(code).lower() in {"401", "403", "authentication_error"} else PROTOCOL
        raise NetworkError(f"MiMo 返回 {str(code or 'error')[:40]}", reason=reason)
    raise NetworkError("MiMo 返回了无法识别的响应", reason=PROTOCOL)


# ---------------------------------------------------------------- 本地模型（HTTP）


def _local_protocol(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in LOCAL_PROTOCOLS else "openai"


def _local_format(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"mp3", "wav"} else "mp3"


def local_url(endpoint: str, path: str = "", protocol: str = "openai") -> str:
    """拼接本地服务地址；地址缺失/非 http(s) 一律按协议错误处理。"""
    base = str(endpoint or "").strip()
    if not base:
        raise NetworkError("本地语音地址未配置", reason=PROTOCOL)
    if "://" not in base:
        base = "http://" + base
    parts = urlsplit(base)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise NetworkError("本地语音地址必须是 http(s)", reason=PROTOCOL)
    route = str(path or "").strip() or LOCAL_DEFAULT_PATHS[_local_protocol(protocol)]
    if not route.startswith("/"):
        route = "/" + route
    return base.rstrip("/") + route


def local_synthesize(
    text: str,
    *,
    endpoint: str = "",
    protocol: str = "openai",
    path: str = "",
    model: str = "",
    voice: str = "",
    format: str = "mp3",
    api_key: str = "",
    rate: float = 1.0,
    volume: float = 1.0,
    out_path: Path | str,
    timeout: float = LOCAL_TIMEOUT_S,
) -> None:
    """本地部署模型合成到 ``out_path``（OpenAI 兼容 / 简单 JSON 两种协议）。

    本地请求**必须直连**：系统代理经常把 127.0.0.1 也带走，表现成「服务明明起着却
    连不上」。响应既可以是音频字节，也可以是 JSON 里的 base64（``audio/data/
    audio_base64``）。
    """
    del volume  # 本地服务没有统一的音量字段；音量在播放端控制
    protocol = _local_protocol(protocol)
    suffix = _local_format(format)
    url = local_url(endpoint, path, protocol)
    speed = _clamp(rate, 0.25, 4.0, 1.0)

    if protocol == "openai":
        body: dict[str, Any] = {
            "model": str(model or "").strip() or "tts-1",
            "input": str(text or ""),
            "voice": str(voice or "").strip() or "default",
            "response_format": suffix,
        }
        if abs(speed - 1.0) > 0.01:
            body["speed"] = speed
    else:
        body = {
            "text": str(text or ""),
            "voice": str(voice or "").strip(),
            "speed": speed,
        }

    headers = [
        ("Content-Type", "application/json"),
        ("Accept", "audio/*, application/json"),
    ]
    key = str(api_key or "").strip()
    if key:
        headers.append(("Authorization", f"Bearer {key}"))

    try:
        _status, response_headers, payload = fetch_url(
            url,
            method="POST",
            headers=tuple(headers),
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=max(1.0, float(timeout)),
            max_bytes=LOCAL_MAX_BYTES,
            prefer_direct=True,
            direct_only=True,
            allowed_schemes=("http", "https"),
        )
    except NetworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - 传输层意外形态
        raise _map_error(exc) from exc

    content_type = ""
    for name, value in response_headers:
        if name.lower() == "content-type":
            content_type = str(value).lower()
            break
    audio = payload
    if "json" in content_type or payload.lstrip()[:1] == b"{":
        audio = _parse_local_json(payload)
    if not audio:
        raise NetworkError("本地语音没有返回音频", reason=PROTOCOL)

    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(audio)
    _require_audio(destination, "本地语音", truncated=bool(getattr(payload, "truncated", False)))


def _parse_local_json(payload: bytes) -> bytes:
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        raise NetworkError("本地语音返回了无法解析的响应", reason=PROTOCOL) from None
    if not isinstance(data, dict):
        raise NetworkError("本地语音返回了无法解析的响应", reason=PROTOCOL)
    for field in ("audio", "data", "audio_base64"):
        encoded = data.get(field)
        if not isinstance(encoded, str) or not encoded:
            continue
        raw = encoded.split(",", 1)[1] if encoded.startswith("data:") else encoded
        try:
            return base64.b64decode(raw)
        except (ValueError, TypeError):
            raise NetworkError("本地语音音频解码失败", reason=PROTOCOL) from None
    error = data.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        reason = AUTH if str(code).lower() in {"401", "403", "authentication_error"} else PROTOCOL
        raise NetworkError(f"本地语音返回 {str(code or 'error')[:40]}", reason=reason)
    raise NetworkError("本地语音响应里没有音频", reason=PROTOCOL)


# ---------------------------------------------------------------- 统一入口


def synthesize(
    provider_id: str,
    request: SpeechRequest,
    out_path: Path | str,
    *,
    api_key: str = "",
    local: dict[str, Any] | None = None,
) -> None:
    """按 provider 分派一次合成；``sapi`` 在 :mod:`petcore.platform.sapi`。"""
    pid = str(provider_id or "").strip().lower()
    if pid == "edge":
        edge_synthesize(
            request.text,
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
            volume=request.volume,
            out_path=out_path,
            timeout=EDGE_TIMEOUT_S,
        )
        return
    if pid == "doubao":
        doubao_synthesize(
            request.text,
            voice=request.voice,
            api_key=api_key,
            rate=request.rate,
            pitch=request.pitch,
            volume=request.volume,
            out_path=out_path,
            timeout=DOUBAO_TIMEOUT_S,
        )
        return
    if pid == "mimo":
        mimo_synthesize(
            request.text,
            voice=request.voice,
            api_key=api_key,
            rate=request.rate,
            pitch=request.pitch,
            volume=request.volume,
            out_path=out_path,
            timeout=MIMO_TIMEOUT_S,
        )
        return
    if pid == "local":
        settings = dict(local or {})
        if api_key:
            settings["api_key"] = api_key
        local_synthesize(
            request.text,
            endpoint=str(settings.get("endpoint") or ""),
            protocol=str(settings.get("protocol") or "openai"),
            path=str(settings.get("path") or ""),
            model=str(settings.get("model") or ""),
            voice=request.voice or str(settings.get("voice") or ""),
            format=str(settings.get("format") or "mp3"),
            api_key=str(settings.get("api_key") or ""),
            rate=request.rate,
            volume=request.volume,
            out_path=out_path,
            timeout=float(settings.get("timeout") or LOCAL_TIMEOUT_S),
        )
        return
    raise NetworkError(f"未知的语音后端：{pid}", reason=PROTOCOL)


def platform_modules() -> dict[str, bool]:
    """诊断用：可选依赖是否装得上（只探测，不 import）。"""
    return {
        module: dependency_present(module)
        for module in ("edge_tts", "comtypes", "keyring")
    }


__all__ = [
    "DEFAULT_VOICES",
    "PROVIDER_SPECS",
    "TtsProviderSpec",
    "clean_voice",
    "dependency_present",
    "doubao_synthesize",
    "edge_known_voices",
    "edge_pitch_arg",
    "edge_rate_arg",
    "edge_synthesize",
    "edge_volume_arg",
    "local_synthesize",
    "local_url",
    "mimo_synthesize",
    "platform_modules",
    "provider_available",
    "synthesize",
]
