"""API 请求头：服务自定义头 + opencode 的客户端标识。

opencode 官方要求调用方用**自己的** User-Agent，并为每个会话发稳定的
``x-opencode-session``（issue #368 的「报头 bug」就是缺这个头）。免费模型仍要求
请求来自 OpenCode 客户端，本模块不伪造它；失败由调用方给出明确文案。
"""

from __future__ import annotations

import urllib.parse
import uuid

OPENCODE_SESSION = uuid.uuid4().hex


def _host(base_url: str) -> str:
    try:
        return (urllib.parse.urlparse(str(base_url or "")).hostname or "").lower()
    except ValueError:
        return ""


def is_opencode_host(base_url: str) -> bool:
    host = _host(base_url)
    return host == "opencode.ai" or host.endswith(".opencode.ai")


def extra_headers(
    base_url: str,
    *,
    version: str = "",
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """返回可安全合并进请求的自定义头（Authorization 已被 spec 层拒绝）。"""
    merged = {str(key): str(value) for key, value in (headers or {}).items()}
    if is_opencode_host(base_url):
        merged.setdefault(
            "User-Agent", f"dsh-pet-core/{(version or '0.2.0').strip()}"
        )
        merged.setdefault("x-opencode-session", OPENCODE_SESSION)
    return merged
