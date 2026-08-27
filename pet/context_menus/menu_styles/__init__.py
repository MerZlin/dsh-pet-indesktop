"""Versioned context-menu styles."""

from .legacy import apply_legacy_menu_style
from .modern import apply_modern_menu_style, install_modern_check_indicators

__all__ = ["apply_legacy_menu_style", "apply_modern_menu_style", "install_modern_check_indicators"]
