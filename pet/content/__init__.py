"""资源型 DLC 运行时支持。"""

from .manager import ContentError, ContentManager
from .models import (
    CharacterPackage,
    ContentManifest,
    ContentPackage,
    InstallResult,
    ValidationResult,
)
from .registry import CharacterRegistry

__all__ = [
    "CharacterPackage",
    "CharacterRegistry",
    "ContentError",
    "ContentManager",
    "ContentManifest",
    "ContentPackage",
    "InstallResult",
    "ValidationResult",
]
