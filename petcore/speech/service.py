"""语音服务：后端链、缓存、重试与凭据；**Qt-free、网络实现只在 net/**。

``SpeechService`` 是 ``HostPorts.speech`` 背后的实现的一半（另一半是
``petcore.ui.ports.CoreSpeechPort``，负责后台线程与共享音频通道）：

- :meth:`SpeechService.providers` / :meth:`preflight` / :meth:`effective_voice`
  **只看配置、凭据与 ``find_spec``，不联网**，可以在 GUI 线程直接调；
- :meth:`SpeechService.synthesize` 是阻塞的（调用方放后台线程）：先查缓存，
  未命中才调合成器，产物先写临时文件再原子换入缓存目录；
- 合成器可注入（``synthesizer(provider_id, request, out_path)``），测试因此
  完全不碰网络与音频设备；
- 失败收敛成 :class:`petcore.contracts.speech.SpeechError`，``reason`` 用稳定分类。

凭据（豆包 / MiMo 的 API Key）只进注入的 ``credentials``，owner 是
``speech.<provider>``；本模块不写 ``config.json``，也不回显 Key。
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..contracts.speech import (
    SPEECH_REASONS,
    SpeechError,
    SpeechProviderInfo,
    SpeechRequest,
    SpeechResult,
)
from .cache import SpeechCache

logger = logging.getLogger("petcore.speech")

DEFAULT_BACKEND = "edge"
DEFAULT_FALLBACK = "sapi"
#: 主音色最多试几次（失败后间隔 ``RETRY_DELAY_S`` 再来一次）
RETRY_ATTEMPTS = 2
#: 一次回退到默认音色时只试一次
FALLBACK_ATTEMPTS = 1
RETRY_DELAY_S = 1.5
DEFAULT_CACHE_LIMIT = 200
CREDENTIAL_OWNER = "speech.{provider}"

Synthesizer = Callable[[str, SpeechRequest, Path], None]


class SpeechService:
    def __init__(
        self,
        root: Path,
        *,
        config: Any = None,
        credentials: Any = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self.root = Path(root)
        self.config = config
        self.credentials = credentials
        self._synthesizer = synthesizer
        self._cache = SpeechCache(self.root, limit=self.cache_limit())

    # ------------------------------------------------------------ 配置
    def _cfg(self, key: str, default: Any) -> Any:
        getter = getattr(self.config, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(key, default)
        except Exception:  # noqa: BLE001 - 配置坏掉不该让语音不可用
            return default
        return default if value is None else value

    def backend(self) -> str:
        return self._clean_id(self._cfg("speech_backend", DEFAULT_BACKEND), DEFAULT_BACKEND)

    def fallback(self) -> str:
        text = str(self._cfg("speech_fallback", DEFAULT_FALLBACK) or "").strip().lower()
        return text

    def cache_limit(self) -> int:
        try:
            value = int(self._cfg("speech_cache_limit", DEFAULT_CACHE_LIMIT))
        except (TypeError, ValueError):
            value = DEFAULT_CACHE_LIMIT
        return max(10, min(2000, value))

    # ------------------------------------------------------------ 端口
    @property
    def cache(self) -> SpeechCache:
        return self._cache

    def cache_dir(self) -> Path:
        return self._cache.directory()

    def providers(self) -> tuple[SpeechProviderInfo, ...]:
        net_tts = self._net()
        infos: list[SpeechProviderInfo] = []
        for spec in net_tts.PROVIDER_SPECS.values():
            available, reason = net_tts.provider_available(spec.id)
            if available:
                available, reason = self._config_gate(spec.id)
            if available and spec.secret_fields:
                required = [
                    field for field in spec.secret_fields
                    if field not in spec.optional_secrets
                ]
                if not all(self._secret(spec.id, field) for field in required):
                    available, reason = False, "auth"
            infos.append(
                SpeechProviderInfo(
                    id=spec.id,
                    label=spec.label,
                    online=bool(spec.online),
                    fields=tuple(spec.fields),
                    available=bool(available),
                    reason=str(reason or ""),
                )
            )
        return tuple(infos)

    def preflight(self, provider: str = "") -> str:
        """解析本次要用的后端（离线）。显式 provider 无效/不可用时沿回退链走。"""
        net_tts = self._net()
        chain: list[str] = []
        explicit = self._clean_id(provider, "")
        if explicit:
            chain.append(explicit)
        chain.extend(self._chain())
        for candidate in chain:
            if candidate in net_tts.PROVIDER_SPECS and self._ready(candidate):
                return candidate
        for candidate in chain:
            if candidate in net_tts.PROVIDER_SPECS:
                return candidate
        return DEFAULT_BACKEND

    def effective_voice(self, provider: str = "") -> str:
        """``preflight`` 之后真正会用的音色（离线；已失效音色换成默认音色）。"""
        resolved = self.preflight(provider)
        return self._voice_for(resolved, "")

    # ------------------------------------------------------------ 合成
    def synthesize(self, request: SpeechRequest) -> SpeechResult:
        text = str(getattr(request, "text", "") or "")
        if not text.strip():
            raise SpeechError("protocol", "没有要合成的文字")
        # 留空的语速/音量沿用语音页的全局值；显式传值优先（voice.chime 等）。
        request = replace(
            request,
            rate=self._rate_value(request.rate),
            volume=self._volume_value(request.volume),
        )

        net_tts = self._net()
        provider = self.preflight(request.provider)
        suffix = self._suffix_for(provider)
        voice = self._voice_for(provider, request.voice)
        flavor = self._flavor(provider, voice, request)

        cached = self._cache.hit(provider, flavor, text, suffix=suffix)
        if cached is not None:
            return SpeechResult(
                path=cached,
                provider=provider,
                voice=voice,
                cached=True,
                text=text,
                volume=self._volume_value(request.volume),
            )

        default_voice = net_tts.DEFAULT_VOICES.get(provider, "")
        candidates = [voice]
        if default_voice and default_voice != voice:
            candidates.append(default_voice)

        last = SpeechError("unavailable")
        for index, candidate in enumerate(candidates):
            # 缓存键必须按**实际使用的音色**算：兜底音色的产物写进首选音色的键，
            # 之后同一句台词会永远播默认音色，却报告成用户选的那个（审计 AUD-20）。
            candidate_flavor = self._flavor(provider, candidate, request)
            destination = self._cache.path(provider, candidate_flavor, text, suffix=suffix)
            if index:
                cached_fallback = self._cache.hit(
                    provider, candidate_flavor, text, suffix=suffix
                )
                if cached_fallback is not None:
                    return SpeechResult(
                        path=cached_fallback,
                        provider=provider,
                        voice=candidate,
                        cached=True,
                        text=text,
                        volume=self._volume_value(request.volume),
                    )
            tries = RETRY_ATTEMPTS if index == 0 else FALLBACK_ATTEMPTS
            for attempt in range(tries):
                if attempt:
                    time.sleep(RETRY_DELAY_S)
                attempt_request = replace(
                    request, provider=provider, voice=candidate, text=text
                )
                temp = self._cache.temp_path(destination)
                try:
                    self._call(provider, attempt_request, temp)
                    self._cache.replace(temp, destination)
                except SpeechError as exc:
                    last = exc
                except Exception as exc:  # noqa: BLE001 - 统一收敛成 reason
                    last = SpeechError(self._reason(exc), type(exc).__name__)
                else:
                    self._trim()
                    return SpeechResult(
                        path=destination,
                        provider=provider,
                        voice=candidate,
                        cached=False,
                        text=text,
                        volume=self._volume_value(request.volume),
                    )
                finally:
                    self._cache.discard(temp)
        raise last

    # ------------------------------------------------------------ 凭据
    def set_secret(self, provider: str, field: str, value: str) -> None:
        spec = self._spec(provider)
        if spec is None or field not in spec.secret_fields:
            raise ValueError(f"unknown speech secret: {provider}.{field}")
        if self.credentials is None:
            raise SpeechError("unavailable", "凭据存储不可用")
        owner = CREDENTIAL_OWNER.format(provider=spec.id)
        try:
            self.credentials.set(owner, str(field), str(value or ""))
        except Exception as exc:  # noqa: BLE001 - 钥匙串坏了也不能抛给界面
            raise SpeechError("unavailable", "凭据存储不可用") from exc

    def secret_status(self, provider: str) -> dict[str, bool]:
        spec = self._spec(provider)
        if spec is None:
            return {}
        return {field: bool(self._secret(spec.id, field)) for field in spec.secret_fields}

    # ------------------------------------------------------------ 内部
    def _net(self):
        from ..net import tts as net_tts

        return net_tts

    def _spec(self, provider: str):
        net_tts = self._net()
        return net_tts.PROVIDER_SPECS.get(str(provider or "").strip().lower())

    @staticmethod
    def _clean_id(value: Any, default: str) -> str:
        text = str(value or "").strip().lower()
        return text or default

    def _chain(self) -> tuple[str, ...]:
        chain = [self.backend()]
        fallback = self.fallback()
        if fallback and fallback not in chain:
            chain.append(fallback)
        return tuple(chain)

    def _ready(self, provider: str) -> bool:
        net_tts = self._net()
        available, _reason = net_tts.provider_available(provider)
        if not available:
            return False
        available, _reason = self._config_gate(provider)
        if not available:
            return False
        spec = net_tts.PROVIDER_SPECS.get(provider)
        for field in getattr(spec, "secret_fields", ()):
            if field in getattr(spec, "optional_secrets", ()):
                continue
            if not self._secret(provider, field):
                return False
        return True

    def _secret(self, provider: str, field: str) -> str:
        if self.credentials is None or not field:
            return ""
        owner = CREDENTIAL_OWNER.format(provider=provider)
        try:
            value = self.credentials.get(owner, str(field))
        except Exception:  # noqa: BLE001 - 读不到就按没配
            return ""
        return str(value or "")

    def _configured_voice(self, provider: str) -> str:
        key = {
            "doubao": "speech_doubao_voice",
            "mimo": "speech_mimo_voice",
            "local": "speech_local_voice",
            "sapi": "speech_sapi_voice",
        }.get(provider, "speech_voice")
        return str(self._cfg(key, "") or "").strip()[:64]

    def _voice_for(self, provider: str, requested: str) -> str:
        net_tts = self._net()
        default = str(net_tts.DEFAULT_VOICES.get(provider, "") or "")
        voice = str(requested or "").strip()[:64] or self._configured_voice(provider)
        if provider == "edge" and voice:
            # 微软会下架音色；配置里存着的老名字直接换成默认音色（离线判断）。
            if voice not in net_tts.edge_known_voices():
                return default
        return voice or default

    def _flavor(self, provider: str, voice: str, request: SpeechRequest) -> str:
        try:
            rate = float(request.rate)
        except (TypeError, ValueError):
            rate = 1.0
        try:
            pitch = float(request.pitch)
        except (TypeError, ValueError):
            pitch = 0.0
        try:
            volume = float(request.volume)
        except (TypeError, ValueError):
            volume = 1.0
        flavor = f"{provider}|{voice}|{rate:.3f}|{pitch:.3f}|{volume:.3f}"
        if provider == "local":
            # 本地服务的地址/协议/路径/模型/格式都会改变声音；不换键就会把
            # 上一个服务的音频当命中（与兜底音色缓存键同一类审计）。
            local = self._local_settings()
            flavor = "|".join(
                (
                    flavor,
                    str(local["endpoint"]),
                    str(local["protocol"]),
                    str(local["path"]),
                    str(local["model"]),
                    str(local["format"]),
                )
            )
        return flavor

    def _local_settings(self) -> dict[str, Any]:
        net_tts = self._net()
        protocol = str(self._cfg("speech_local_protocol", "openai") or "").strip().lower()
        if protocol not in net_tts.LOCAL_PROTOCOLS:
            protocol = "openai"
        audio_format = str(self._cfg("speech_local_format", "mp3") or "").strip().lower()
        if audio_format not in {"mp3", "wav"}:
            audio_format = "mp3"
        route = str(self._cfg("speech_local_path", "") or "").strip()[:120]
        if route and not route.startswith("/"):
            route = "/" + route
        try:
            timeout = float(self._cfg("speech_local_timeout", 30.0) or 30.0)
        except (TypeError, ValueError):
            timeout = 30.0
        return {
            "endpoint": str(self._cfg("speech_local_endpoint", "") or "").strip().rstrip("/"),
            "protocol": protocol,
            "path": route,
            "model": str(self._cfg("speech_local_model", "") or "").strip()[:64],
            "voice": str(self._cfg("speech_local_voice", "") or "").strip()[:64],
            "format": audio_format,
            "timeout": max(5.0, min(120.0, timeout)),
        }

    def _config_gate(self, provider: str) -> tuple[bool, str]:
        if provider == "local" and not self._local_settings()["endpoint"]:
            return False, "config"
        return True, ""

    def _rate_value(self, value: Any) -> float:
        number = self._cfg("speech_rate", 1.0) if value is None else value
        try:
            number = float(number)
        except (TypeError, ValueError):
            number = 1.0
        return max(0.5, min(2.0, number))

    def _volume_value(self, value: Any) -> float:
        number = self._cfg("speech_volume", 1.0) if value is None else value
        try:
            number = float(number)
        except (TypeError, ValueError):
            number = 1.0
        return max(0.0, min(1.0, number))

    def _suffix_for(self, provider: str) -> str:
        if provider == "local":
            return str(self._local_settings()["format"])
        spec = self._net().PROVIDER_SPECS.get(provider)
        return spec.suffix if spec is not None else "mp3"

    def _call(self, provider: str, request: SpeechRequest, out_path: Path) -> None:
        synthesizer = self._synthesizer
        if synthesizer is not None:
            synthesizer(provider, request, out_path)
            return
        self._default_synthesizer(provider, request, out_path)

    def _default_synthesizer(
        self, provider: str, request: SpeechRequest, out_path: Path
    ) -> None:
        if provider == "sapi":
            from ..platform.sapi import sapi_synthesize

            sapi_synthesize(
                request.text,
                voice=request.voice,
                rate=request.rate,
                volume=request.volume,
                out_path=out_path,
            )
            return
        from ..net import tts as net_tts

        net_tts.synthesize(
            provider,
            request,
            out_path,
            api_key=self._secret(provider, "api_key"),
            local=self._local_settings() if provider == "local" else None,
        )

    def _trim(self) -> None:
        limit = self.cache_limit()
        self._cache.limit = limit
        if self._cache.count() > limit:
            self._cache.trim(limit)

    @staticmethod
    def _reason(exc: BaseException) -> str:
        from ..net.errors import NetworkError

        if isinstance(exc, SpeechError):
            return exc.reason
        if isinstance(exc, NetworkError):
            return exc.reason if exc.reason in SPEECH_REASONS else "network"
        if isinstance(exc, (TimeoutError,)):
            return "timeout"
        if isinstance(exc, (FileNotFoundError, PermissionError, ImportError)):
            return "unavailable"
        if isinstance(exc, (ValueError, TypeError)):
            return "protocol"
        if isinstance(exc, OSError):
            return "unavailable"
        return "unavailable"
