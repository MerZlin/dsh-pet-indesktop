"""Explicit one-time legacy preview/confirm. No legacy resolution during execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

from pet.credentials import CredentialError

from ..common.models import VisionProfile, VisionSettings
from .config import VisionConfigService, digest

DEFAULT_PROMPT = "你是一只可爱的桌面宠物，请用自然、友善的中文和用户交流。"


def legacy_source(data: dict) -> dict:
    return {"chat": data.get("chat", {}), "prefer_free_provider": data.get("proactive_screen", {}).get("prefer_free_provider", True)}


def source_revision(data: dict) -> str:
    # Digest only, never persist or display the source (which may have in-memory secrets).
    return digest(legacy_source(data))


def _provider(data: dict) -> tuple[dict, str]:
    # Match legacy ChatSettings/ProviderConfig parsing without importing chat at runtime.
    chat = data.get("chat", {})
    chat = chat if isinstance(chat, dict) else {}
    raw = chat.get("providers", {})
    providers = {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    active = str(chat.get("active_provider", ""))
    if providers:
        active = active if active in providers else next(iter(providers))
        provider = dict(providers[active])
        provider.setdefault("api_key_ref", f"provider/{active}")
    else:
        provider = {"api_key_ref": ""}
    return provider, str(chat.get("default_system_prompt", DEFAULT_PROMPT))


def _number(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]  # Legacy JSON may contain non-numeric values.
    except (TypeError, ValueError):
        return default


def _tokens(value: object) -> int:
    try:
        return max(1, int(_number(value, 2048)))
    except (ValueError, OverflowError):
        return 2048


def _legacy_profile(data: dict, mode: str) -> VisionProfile:
    provider, prompt = _provider(data)
    independent = not provider.get("vision_same_as_chat", True)
    if mode == "automatic" and not data.get("proactive_screen", {}).get("prefer_free_provider", True):
        independent = False
    model = str(provider.get("vision_model", "")).strip() if independent else ""
    if not model:
        model = str(provider.get("model", "deepseek-v4-flash")).strip()
        if "vision" not in model.lower():
            if model.lower().endswith("deepseek-v4-flash"):
                model += "-vision-exp"
            elif model.lower().startswith("deepseek"):
                model = "deepseek-v4-flash-vision-exp"
    return VisionProfile(
        mode,
        (provider.get("vision_base_url") if independent else "") or provider.get("base_url", "https://api.deepseek.com"),
        model,
        provider.get("chat_path", "/v1/chat/completions"),
        prompt,
        max(1.0, _number(provider.get("timeout", 60), 60)),
        min(2.0, max(0.0, _number(provider.get("temperature", 0.7), 0.7))),
        _tokens(provider.get("max_tokens", 2048)),
        bool(provider.get("verify_ssl", True)),
    )


def _legacy_secret(data: dict, mode: str, reader: Callable[[str], str]) -> str:
    provider, _ = _provider(data)
    independent = not provider.get("vision_same_as_chat", True)
    if mode == "automatic" and not data.get("proactive_screen", {}).get("prefer_free_provider", True):
        independent = False
    if independent:
        return provider.get("vision_api_key", "") or reader(provider.get("vision_api_key_ref", ""))
    return reader(provider.get("api_key_ref", "")) or provider.get("api_key", "")


@dataclass(frozen=True)
class MigrationPreview:
    source_revision: str
    target_revision: str
    automatic: VisionProfile
    manual: VisionProfile
    automatic_credential: str
    manual_credential: str


class VisionMigration:
    def __init__(self, service: VisionConfigService, *, legacy_secret_reader: Callable[[str], str] | None = None):
        self.service = service
        self.reader = legacy_secret_reader or service.read_legacy_secret

    def preview(self) -> MigrationPreview:
        data = self.service.config.migration_source()
        states = []
        for mode in ("automatic", "manual"):
            try:
                states.append("available" if _legacy_secret(data, mode, self.reader) else "missing")
            except CredentialError:
                states.append("unavailable")
        return MigrationPreview(source_revision(data), self.service.revision(), _legacy_profile(data, "automatic"), _legacy_profile(data, "manual"), *states)

    def confirm(self, preview: MigrationPreview) -> None:
        if self.service.settings().profiles:
            raise ValueError("already_configured")
        data = self.service.config.migration_source()

        def guard(current: dict) -> None:
            if source_revision(current) != preview.source_revision:
                raise ValueError("source_changed")

        guard(data)
        auto = _legacy_profile(data, "automatic")
        manual = _legacy_profile(data, "manual")
        auto_key = _legacy_secret(data, "automatic", self.reader)
        manual_key = _legacy_secret(data, "manual", self.reader)
        same = (asdict(auto) | {"profile_id": "shared"}) == (asdict(manual) | {"profile_id": "shared"})
        if same and auto_key == manual_key:
            profiles = {"shared": replace(auto, profile_id="shared")}
            bindings = {"automatic": "shared", "manual": "shared"}
            secrets = {"shared": auto_key}
        else:
            profiles = {"automatic": auto, "manual": manual}
            bindings = {"automatic": "automatic", "manual": "manual"}
            secrets = {"automatic": auto_key, "manual": manual_key}
        self.service.apply(VisionSettings(profiles, bindings, "confirmed"), secrets, expected_revision=preview.target_revision, source_guard=guard)

    def recover(self) -> None:
        self.service.recover()
