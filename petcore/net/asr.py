"""OpenAI 兼容语音识别客户端（``POST /v1/audio/transcriptions``）。

只读网络实现放在 ``net/``；本模块不碰 Qt、不写配置、不记 URL/密钥。
本地服务一律只直连（``routes=(False,)``），避免系统代理把 127.0.0.1 劫走。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import http as http_mod

MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_PATH = "/v1/audio/transcriptions"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class AsrError(RuntimeError):
    """稳定 ``reason``：unconfigured / insecure / auth / rate_limit / network /
    timeout / protocol / empty / too_large。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "protocol")
        self.detail = str(detail or "")[:200]
        super().__init__(self.reason)


def _endpoint_url(endpoint: str) -> str:
    text = str(endpoint or "").strip()
    if not text:
        raise AsrError("unconfigured")
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AsrError("unconfigured")
    if parsed.scheme == "http" and parsed.hostname.lower() not in LOOPBACK_HOSTS:
        raise AsrError("insecure")
    if parsed.path in {"", "/"}:
        text = text.rstrip("/") + DEFAULT_PATH
    return text


def _multipart(wav: bytes, model: str, language: str) -> tuple[bytes, str]:
    boundary = f"----dshpet{uuid.uuid4().hex}"
    lines: list[bytes] = []

    def field(name: str, value: str) -> None:
        lines.append(f"--{boundary}\r\n".encode("ascii"))
        lines.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii")
        )
        lines.append(str(value).encode("utf-8") + b"\r\n")

    lines.append(f"--{boundary}\r\n".encode("ascii"))
    lines.append(
        b'Content-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
    )
    lines.append(b"Content-Type: audio/wav\r\n\r\n")
    lines.append(wav + b"\r\n")
    if model:
        field("model", model)
    if language:
        field("language", language)
    lines.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(lines), boundary


def _reason_for_status(code: int) -> str:
    if code in (401, 403):
        return "auth"
    if code == 429:
        return "rate_limit"
    if code in (413,):
        return "too_large"
    return "protocol"


def transcribe(
    endpoint: str,
    wav: bytes,
    *,
    model: str = "",
    language: str = "zh",
    timeout: float = 30.0,
    verify_ssl: bool = True,
) -> str:
    """把 WAV 发给 OpenAI 兼容服务，返回识别文本；失败抛 :class:`AsrError`。"""
    payload = bytes(wav or b"")
    if not payload:
        raise AsrError("empty")
    if len(payload) > MAX_AUDIO_BYTES:
        raise AsrError("too_large")
    url = _endpoint_url(endpoint)
    body, boundary = _multipart(payload, str(model or ""), str(language or ""))
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "dsh-pet-core/asr",
        },
    )
    try:
        response = http_mod.urlopen(
            request,
            timeout=max(3.0, min(120.0, float(timeout))),
            routes=(False,),
            pollute=False,
        )
    except urllib.error.HTTPError as exc:
        raise AsrError(_reason_for_status(int(exc.code or 0))) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise AsrError("timeout") from None
        text = str(reason or "").lower()
        raise AsrError("timeout" if "timed out" in text else "network") from None
    except Exception as exc:  # noqa: BLE001 - 传输层异常统一收敛
        if isinstance(exc, (TimeoutError, socket.timeout)):
            raise AsrError("timeout") from None
        raise AsrError("network") from None
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AsrError("protocol")
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise AsrError("protocol") from None
    if not isinstance(doc, dict):
        raise AsrError("protocol")
    text = doc.get("text")
    if text is None:
        text = doc.get("result") or doc.get("transcript")
    text = str(text or "").strip()
    if not text:
        raise AsrError("empty")
    return text
