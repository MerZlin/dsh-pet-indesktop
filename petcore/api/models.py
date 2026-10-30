"""``GET /v1/models``：填充设置页的模型下拉（失败不弹窗）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ..net import http as http_mod
from .headers import extra_headers

MAX_MODELS = 200
MAX_BODY = 512 * 1024
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ModelListError(RuntimeError):
    def __init__(self, reason: str = "network") -> None:
        self.reason = str(reason or "network")
        super().__init__(self.reason)


def _is_local(base_url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(str(base_url or "")).hostname or "").lower()
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS or host.startswith("192.168.") or host.startswith(
        "10."
    )


def list_models(service, api_key: str = "", *, timeout: float = 3.0) -> tuple[str, ...]:
    base_url = str(getattr(service, "base_url", "") or "").rstrip("/")
    path = str(getattr(service, "models_path", "/v1/models") or "/v1/models")
    if not base_url:
        raise ModelListError("unconfigured")
    headers = extra_headers(
        base_url,
        version=str(getattr(service, "version", "") or ""),
        headers=dict(getattr(service, "extra_headers", {}) or {}),
    )
    headers["Accept"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url + path, headers=headers, method="GET"
    )
    try:
        response = http_mod.urlopen(
            request,
            timeout=max(1.0, min(10.0, float(timeout))),
            routes=(False,) if _is_local(base_url) else None,
            pollute=False,
        )
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        raise ModelListError("auth" if code in (401, 403) else "protocol") from None
    except Exception:  # noqa: BLE001 - 连接失败只给稳定分类
        raise ModelListError("network") from None
    try:
        raw = response.read(MAX_BODY + 1)
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(raw) > MAX_BODY:
        raise ModelListError("protocol")
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise ModelListError("protocol") from None
    raw_items = doc.get("data") if isinstance(doc, dict) else doc
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("models") or raw_items.get("data")
    if not isinstance(raw_items, list):
        raise ModelListError("protocol")
    names: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
        else:
            name = ""
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text)
        if len(names) >= MAX_MODELS:
            break
    if not names:
        raise ModelListError("empty")
    return tuple(names)
