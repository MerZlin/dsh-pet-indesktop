"""一次最小化的连通性探测（只 POST 一条 ``max_tokens=1`` 的消息）。

**响应体一律不读、不回显**——它可能原样回显请求里的密钥；结论只有稳定分类
（``ok`` / ``auth`` / ``rate_limit`` / ``network`` / ``timeout`` / ``protocol``）
加上 HTTP 状态码与 ``Retry-After``。
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..net.errors import PROTOCOL, classify_exception, classify_http_status, http_error
from ..net.http import urlopen
from .headers import extra_headers as merge_extra_headers

OK = "ok"


@dataclass(frozen=True)
class ProbeResult:
    reason: str
    http_status: int | None = None
    retry_after: float | None = None

    @property
    def ok(self) -> bool:
        return self.reason == OK


def probe_text(service, api_key: str, *, timeout: float | None = None) -> ProbeResult:
    model = str(getattr(service, "model", "") or "").strip()
    if not model:
        raise ValueError("api_model_required")
    base_url = str(getattr(service, "base_url", "") or "").rstrip("/")
    chat_path = str(getattr(service, "chat_path", "") or "/v1/chat/completions")
    seconds = timeout if timeout is not None else getattr(service, "timeout", 60) or 60
    context = None
    if not bool(getattr(service, "verify_ssl", True)):
        context = _insecure_context()
    request = _build_request(
        f"{base_url}{chat_path}",
        model,
        str(api_key or ""),
        base_url=base_url,
        version=str(getattr(service, "version", "") or ""),
        extra_headers=dict(getattr(service, "extra_headers", {}) or {}),
    )
    try:
        response = urlopen(request, timeout=float(seconds), context=context)
    except urllib.error.HTTPError as exc:
        error = http_error(exc)
        return ProbeResult(
            error.reason, http_status=error.status, retry_after=error.retry_after
        )
    except Exception as exc:  # noqa: BLE001 - 传输异常统一收敛成分类
        return ProbeResult(classify_exception(exc) or PROTOCOL)
    try:
        status = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响结论
                pass
    if 200 <= status < 300:
        return ProbeResult(OK, http_status=status)
    return ProbeResult(classify_http_status(status), http_status=status)


def _build_request(
    url: str,
    model: str,
    api_key: str,
    *,
    base_url: str = "",
    version: str = "",
    extra_headers: dict[str, str] | None = None,
) -> urllib.request.Request:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
            "stream": False,
        }
    ).encode("utf-8")
    headers = merge_extra_headers(
        base_url or url, version=version, headers=extra_headers
    )
    headers.update(
        {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    )
    return urllib.request.Request(url, data=body, headers=headers, method="POST")


def _insecure_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context
