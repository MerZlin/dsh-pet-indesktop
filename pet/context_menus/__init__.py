"""Independent legacy and modern desktop-pet context-menu implementations."""

from .legacy import build_legacy_menu
from .modern import build_modern_menu

__all__ = ["build_legacy_menu", "build_modern_menu"]
