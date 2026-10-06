"""Chat public API; data-only consumers must not initialize the Qt service."""

from typing import TYPE_CHECKING

from pet.feature_distribution import BUILTIN_AI

if TYPE_CHECKING:
    from features.ai_chat.host.chat.service import ChatService as ChatService

if not BUILTIN_AI:
    raise ModuleNotFoundError("AI is not built in; use the verified official package", name="pet.chat")

from .models import ChatMessage, ChatSession, ChatSettings, ProviderConfig  # noqa: E402 - fail-closed BUILTIN_AI gate precedes any feature import

__all__ = ["ChatMessage", "ChatSession", "ChatSettings", "ProviderConfig", "ChatService"]


def __getattr__(name: str):
    if name == "ChatService":
        from .service import ChatService

        return ChatService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
