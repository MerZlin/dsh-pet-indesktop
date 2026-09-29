"""Validated, independently persisted vision settings and ephemeral request DTO."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from pet.http_compat import normalize_chat_endpoint

PLUGIN_ID = "official.screen-understanding"
MODES = frozenset({"automatic", "manual"})


def validate_endpoint(base: str, path: str) -> str:
    endpoint = normalize_chat_endpoint(base, path)
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError("invalid_endpoint")
    return endpoint


@dataclass(frozen=True)
class VisionProfile:
    profile_id: str
    base_url: str
    model: str
    chat_path: str = "/v1/chat/completions"
    system_prompt: str = ""
    timeout: float = 60.0
    temperature: float = 0.7
    max_tokens: int = 2048
    verify_ssl: bool = True
    credential_ref: str = ""

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.profile_id):
            raise ValueError("invalid_profile_id")
        validate_endpoint(self.base_url, self.chat_path)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("missing_model")
        if not isinstance(self.system_prompt, str) or not isinstance(self.verify_ssl, bool) or not isinstance(self.credential_ref, str):
            raise ValueError("invalid_profile")
        if not math.isfinite(self.timeout) or self.timeout < 1 or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("invalid_request_parameters")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens < 1:
            raise ValueError("invalid_max_tokens")

    @property
    def endpoint(self) -> str:
        return validate_endpoint(self.base_url, self.chat_path)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VisionProfile:
        if not isinstance(raw, dict):
            raise ValueError("invalid_profile")
        allowed = cls.__dataclass_fields__
        if set(raw) - set(allowed):
            raise ValueError("unknown_profile_fields")
        try:
            return cls(**raw)
        except (TypeError, KeyError, OverflowError) as exc:
            raise ValueError("invalid_profile") from exc


@dataclass(frozen=True)
class VisionSettings:
    profiles: dict[str, VisionProfile] = field(default_factory=dict)
    bindings: dict[str, str] = field(default_factory=dict)
    migration_state: str = "pending"
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VisionSettings:
        if not isinstance(raw, dict) or raw.get("schema_version", 1) != 1:
            raise ValueError("unsupported_vision_schema")
        try:
            profiles = {key: VisionProfile.from_dict(value) for key, value in raw.get("profiles", {}).items()}
            bindings = dict(raw.get("bindings", {}))
            if any(key != value.profile_id for key, value in profiles.items()):
                raise ValueError("invalid_profile_id")
            if set(bindings) - MODES or any(value not in profiles for value in bindings.values()):
                raise ValueError("invalid_binding")
            return cls(profiles, bindings, str(raw.get("migration_state", "pending")))
        except (TypeError, AttributeError) as exc:
            raise ValueError("invalid_vision_settings") from exc


@dataclass(frozen=True)
class VisionRequestConfig:
    """Only passed to a single authorized execution. Never persisted or logged."""

    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    chat_path: str = "/v1/chat/completions"
    system_prompt: str = ""
    timeout: float = 60.0
    temperature: float = 0.7
    max_tokens: int = 2048
    verify_ssl: bool = True

    # Compatibility with the existing pure vision executor; no provider inference.
    vision_same_as_chat = True
    vision_model = ""

    def to_dict(self, include_secret: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_secret:
            data.pop("api_key")
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VisionRequestConfig:
        if not isinstance(raw, dict) or not isinstance(raw.get("api_key", ""), str):
            raise ValueError("invalid_request")
        data = {key: value for key, value in raw.items() if key in cls.__dataclass_fields__}
        # Validate via the persisted model; ephemeral secrets are kept out of it.
        VisionProfile.from_dict({"profile_id": "request", **{k: v for k, v in data.items() if k != "api_key"}})
        return cls(**data)

    @classmethod
    def from_profile(cls, profile: VisionProfile, secret: str) -> VisionRequestConfig:
        data = asdict(profile)
        data.pop("profile_id")
        data.pop("credential_ref")
        return cls(**data, api_key=secret)
