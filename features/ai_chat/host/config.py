"""AI-owned interpretation and CAS persistence; secrets only through Core vault."""

from __future__ import annotations

import copy
from dataclasses import replace

from pet.credentials import CredentialError
from pet.feature_config import namespace_revision
from pet.feature_ports import FeatureHostContext

OWNER = "official.ai-chat"
UI_FIELDS = frozenset(
    {
        "file_interpret",
        "chat_ui_style",
        "chat_follow_pet",
        "chat_always_on_top",
        "chat_background",
        "modern_chat_background",
        "chat_background_opacity",
        "chat_background_fill",
        "modern_chat_background_opacity",
        "modern_chat_background_fill",
        "modern_chat_card_opacity",
        "chat_bg_crops",
        "system_notifications_enabled",
    }
)


class AiConfiguration:
    """Compatibility-shaped adapter without exposing Core Config or filesystem."""

    def __init__(self, context: FeatureHostContext):
        if not isinstance(context, FeatureHostContext) or context.owner != OWNER:
            raise TypeError("AI configuration requires owner-bound context")
        self.context = context
        self.reload()

    def reload(self):
        self._namespace = self.context.configuration.read_namespace()
        self._revision = namespace_revision(self._namespace)
        self._staged = copy.deepcopy(self._namespace)

    @property
    def dir(self):
        if self.context.user_data is None:
            raise PermissionError("owner_data_not_granted")
        return self.context.user_data.root

    @property
    def instance_id(self):
        return self.context.user_data.instance_id if self.context.user_data else ""

    def character_alias(self, character):
        return self.context.user_data.alias_for(character) if self.context.user_data else ""

    def get(self, key, default=None):
        if key in {"character", "self_talk_bubble_style"} and self.context.user_data is not None:
            return copy.deepcopy(self.context.user_data.read_display().get(key, default))
        if key not in UI_FIELDS:
            raise PermissionError("preference_not_granted")
        return copy.deepcopy(self._staged.get("ui", {}).get(key, default))

    def set(self, key, value):
        if key not in UI_FIELDS:
            raise PermissionError("preference_not_granted")
        self._staged.setdefault("ui", {})[key] = copy.deepcopy(value)

    def chat_settings(self):
        from .chat.models import ChatSettings

        value = self._staged.get("chat")
        if value is None:
            value = self.context.preferences.read()
        # Never adopt plaintext or dereference a legacy secret while opening UI.
        value = copy.deepcopy(value)
        if isinstance(value, dict):
            for provider in value.get("providers", {}).values():
                if isinstance(provider, dict):
                    provider.pop("api_key", None)
                    provider.pop("vision_api_key", None)
        return ChatSettings.from_dict(value)

    def set_chat_settings(self, settings):
        if any(provider.api_key or provider.vision_api_key for provider in settings.providers.values()):
            raise CredentialError("plaintext_secret_not_allowed")
        self._staged["chat"] = settings.to_dict(include_secrets=False)

    def save(self):
        self.context.configuration.commit_namespace(self._staged, expected_revision=self._revision)
        self.reload()
        return True

    def save_provider_secret(self, provider, secret):
        # Explicitly entered secrets get a fresh scoped reference. A legacy ref
        # is not evidence authorizing migration, enumeration or deletion.
        return self.context.credentials.save(provider.provider_id, provider.base_url, secret)

    def require_execution(self):
        check = self.context.execution_authorized
        if check is None or not check():
            raise CredentialError("execution_not_authorized")

    def resolve_provider_secret(self, provider, *, operation="chat.send"):
        self.require_execution()
        return self.context.credentials.acquire(provider.api_key_ref, provider.provider_id, provider.base_url, operation)

    def request_config(self, provider, *, operation="chat.send"):
        return replace(provider, api_key=self.resolve_provider_secret(provider, operation=operation))

    def resolve_api_key(self, provider):
        return self.resolve_provider_secret(provider)
