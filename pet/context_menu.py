# -*- coding: utf-8 -*-
"""Single maintained modern context-menu dispatcher."""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from .context_menus import build_modern_menu
from .context_menus.menu_styles import (
    apply_modern_menu_style,
    install_modern_check_indicators,
)
from .context_menus.menu_styles.common import install_stay_open_interaction


def populate_context_menu(menu: QMenu, pet) -> None:
    """按现代布局建一棵真实 Qt 菜单（旧版菜单已于 2026-10-08 全量退役）。

    现代树的形状由 ``context_menu_layout``（默认 ``modern-default-v1``）与
    动作注册表决定，本函数只负责「样式 → 建树 → 勾选指示/停留交互」
    这条固定流水线。
    """
    cfg = getattr(pet, "cfg", None)
    apply_modern_menu_style(menu, cfg.get("context_menu_appearance", {}) if cfg is not None else {})
    build_modern_menu(menu, pet)
    install_modern_check_indicators(menu)
    install_stay_open_interaction(menu)


__all__ = [
    "populate_context_menu",
]
