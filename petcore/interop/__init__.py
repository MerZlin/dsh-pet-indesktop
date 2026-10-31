"""跨通道互通：消息账本、分层人格记忆与人格层组合。

本包只允许标准库；`ui`/`net`/`platform` 不得从这里反向依赖。mod 可以 import
这里的纯函数与 `MemoryStore`，但持久化入口仍由 core 的 `MemoryService` 提供。
"""

from .memory import (
    MemoryCard,
    MemoryEntry,
    MemoryStore,
    extract_emotion,
    redact,
    score_memory,
)
from .persona import compose_layers, trim_layers

__all__ = [
    "MemoryCard",
    "MemoryEntry",
    "MemoryStore",
    "compose_layers",
    "extract_emotion",
    "redact",
    "score_memory",
    "trim_layers",
]
