"""API 服务规格与端点校验。

配置文件里只描述「连哪儿、怎么连」，凭据一律走钥匙串（见 ``credentials``）。
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from ..contracts.api import ApiMetadata

MAX_ENDPOINT_LENGTH = 2048
MAX_CHAT_PATH_LENGTH = 512
BALANCE_PROTOCOLS = frozenset({"none", "deepseek"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
SECRET_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie"}
)

_SERVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


def _fail(code: str) -> ValueError:
    return ValueError(code)


def endpoint_host(value: Any) -> str:
    """只取 host（小写）；给 UI 判断「本地/远程」用，不做校验。"""
    try:
        return (urllib.parse.urlparse(str(value or "")).hostname or "").lower()
    except ValueError:
        return ""


def _is_private_ipv4(host: str) -> bool:
    parts = str(host or "").split(".")
    if len(parts) != 4:
        return False
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return False
    if any(number < 0 or number > 255 for number in numbers):
        return False
    return (
        numbers[0] == 10
        or (numbers[0] == 172 and 16 <= numbers[1] <= 31)
        or (numbers[0] == 192 and numbers[1] == 168)
    )


def validate_endpoint(value: Any, *, allow_private_http: bool = False) -> str:
    """校验 Base URL；返回原值（去尾斜杠交给调用方）。

    只接受 https，或指向本机/显式允许的局域网私网地址的 http。
    拒绝空白/控制字符/非 ASCII、反斜杠、userinfo、query、fragment 与坏端口。
    """
    if not isinstance(value, str):
        raise _fail("api_endpoint_invalid")
    endpoint = value
    if not endpoint:
        raise _fail("api_endpoint_empty")
    if len(endpoint) > MAX_ENDPOINT_LENGTH:
        raise _fail("api_endpoint_too_long")
    for char in endpoint:
        if char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F:
            raise _fail("api_endpoint_chars")
        if ord(char) > 0x7E:
            raise _fail("api_endpoint_chars")
    if "\\" in endpoint:
        raise _fail("api_endpoint_chars")
    if "@" in endpoint:
        raise _fail("api_endpoint_userinfo")
    if "?" in endpoint or "#" in endpoint:
        raise _fail("api_endpoint_query")

    scheme, separator, remainder = endpoint.partition("://")
    scheme = scheme.lower()
    if not separator or scheme not in {"http", "https"}:
        raise _fail("api_endpoint_scheme")
    authority, _, path = remainder.partition("/")

    host, port = _split_authority(authority)
    if not host:
        raise _fail("api_endpoint_host")
    if scheme == "http":
        loopback = host.lower() in LOOPBACK_HOSTS
        if not loopback and not (allow_private_http and _is_private_ipv4(host)):
            raise _fail("api_endpoint_insecure")
    if port is not None and not 1 <= port <= 65535:
        raise _fail("api_endpoint_port")
    if path and any(char in path for char in "?#\\"):
        raise _fail("api_endpoint_chars")
    return endpoint


def _split_authority(authority: str) -> tuple[str, int | None]:
    if not authority:
        raise _fail("api_endpoint_host")
    if authority in LOOPBACK_HOSTS:
        return authority, None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0:
            raise _fail("api_endpoint_host")
        host = authority[1:closing]
        tail = authority[closing + 1 :]
        if not tail:
            return host, None
        if not tail.startswith(":"):
            raise _fail("api_endpoint_host")
        return host, _parse_port(tail[1:])
    host, separator, port = authority.rpartition(":")
    if not separator:
        return authority, None
    if not host:
        raise _fail("api_endpoint_host")
    return host, _parse_port(port)


def _parse_port(value: str) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise _fail("api_endpoint_port")
    return int(value)


@dataclass(frozen=True, slots=True)
class ApiService:
    service_id: str
    name: str
    base_url: str
    chat_path: str = "/v1/chat/completions"
    model: str = ""
    vision_model: str = ""
    verify_ssl: bool = True
    timeout: int = 60
    balance_protocol: str = "none"
    models_path: str = "/v1/models"
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    allow_lan_http: bool = False
    preset: str = ""

    def validate(self) -> "ApiService":
        """返回规范化副本；任何不合规字段都抛 ``ValueError``（消息是稳定代码）。"""
        service_id = str(self.service_id or "").strip()
        if not _SERVICE_ID_RE.match(service_id):
            raise _fail("api_service_id_invalid")
        name = str(self.name or "").strip()
        if not name or len(name) > 128:
            raise _fail("api_name_invalid")
        base_url = validate_endpoint(
            str(self.base_url or "").strip(), allow_private_http=bool(self.allow_lan_http)
        ).rstrip("/")
        if not base_url:
            raise _fail("api_endpoint_invalid")
        chat_path = str(self.chat_path or "").strip()
        if (
            not chat_path.startswith("/")
            or len(chat_path) > MAX_CHAT_PATH_LENGTH
            or any(char in chat_path for char in "?#\\")
            or any(char.isspace() for char in chat_path)
        ):
            raise _fail("api_chat_path_invalid")
        models_path = str(self.models_path or "/v1/models").strip()
        if (
            not models_path.startswith("/")
            or len(models_path) > MAX_CHAT_PATH_LENGTH
            or any(char in models_path for char in "?#\\")
            or any(char.isspace() for char in models_path)
        ):
            raise _fail("api_models_path_invalid")
        try:
            timeout = int(self.timeout)
        except (TypeError, ValueError, OverflowError):
            raise _fail("api_timeout_invalid") from None
        if not 1 <= timeout <= 300:
            raise _fail("api_timeout_invalid")
        balance_protocol = str(self.balance_protocol or "none")
        if balance_protocol not in BALANCE_PROTOCOLS:
            raise _fail("api_balance_protocol_invalid")
        headers = self._clean_headers(self.extra_headers)
        return replace(
            self,
            service_id=service_id,
            name=name,
            base_url=base_url,
            chat_path=chat_path,
            models_path=models_path,
            model=str(self.model or "").strip(),
            vision_model=str(self.vision_model or "").strip(),
            verify_ssl=bool(self.verify_ssl),
            timeout=timeout,
            balance_protocol=balance_protocol,
            extra_headers=headers,
            allow_lan_http=bool(self.allow_lan_http),
            preset=str(self.preset or "").strip()[:64],
        )

    @staticmethod
    def _clean_headers(value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping):
            return {}
        if len(value) > 16:
            raise _fail("api_header_too_many")
        result: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name or "").strip()
            if not HEADER_NAME_RE.match(name):
                raise _fail("api_header_invalid")
            if name.lower() in SECRET_HEADERS:
                raise _fail("api_header_secret")
            text = str(raw_value or "").strip()
            if not text or len(text) > 1024:
                raise _fail("api_header_invalid")
            if any(char in text for char in "\r\n\x00"):
                raise _fail("api_header_invalid")
            lowered = text.lower()
            if lowered.startswith(("sk-", "bearer ", "basic ")):
                raise _fail("api_header_secret")
            result[name] = text
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "chat_path": self.chat_path,
            "models_path": self.models_path,
            "model": self.model,
            "vision_model": self.vision_model,
            "verify_ssl": self.verify_ssl,
            "timeout": self.timeout,
            "balance_protocol": self.balance_protocol,
            "extra_headers": dict(self.extra_headers),
            "allow_lan_http": self.allow_lan_http,
            "preset": self.preset,
        }

    @classmethod
    def from_dict(cls, service_id: str, record: Any) -> "ApiService":
        if not isinstance(record, dict):
            raise _fail("api_service_invalid")
        raw = record.get("timeout", 60)
        try:
            timeout = int(raw)
        except (TypeError, ValueError, OverflowError):
            timeout = 60
        headers = record.get("extra_headers")
        return cls(
            service_id=str(service_id),
            name=str(record.get("name") or ""),
            base_url=str(record.get("base_url") or ""),
            chat_path=str(record.get("chat_path") or "/v1/chat/completions"),
            models_path=str(record.get("models_path") or "/v1/models"),
            model=str(record.get("model") or ""),
            vision_model=str(record.get("vision_model") or ""),
            verify_ssl=bool(record.get("verify_ssl", True)),
            timeout=timeout,
            balance_protocol=str(record.get("balance_protocol") or "none"),
            extra_headers=dict(headers) if isinstance(headers, dict) else {},
            allow_lan_http=bool(record.get("allow_lan_http", False)),
            preset=str(record.get("preset") or ""),
        )

    def metadata(self, version: str = "") -> ApiMetadata:
        return ApiMetadata(
            service_id=self.service_id,
            name=self.name,
            base_url=self.base_url,
            chat_path=self.chat_path,
            model=self.model,
            vision_model=self.vision_model,
            verify_ssl=self.verify_ssl,
            timeout=self.timeout,
            balance_protocol=self.balance_protocol,
            version=str(version),
            models_path=self.models_path,
            extra_headers=dict(self.extra_headers),
        )
