"""Chat public API; data-only consumers must not initialize the Qt service."""

from .models import ChatMessage, ChatSession, ChatSettings, ProviderConfig

__all__ = ["ChatMessage", "ChatSession", "ChatSettings", "ProviderConfig", "ChatService"]


def __getattr__(name: str):
    if name == "ChatService":
        from .service import ChatService

        return ChatService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
