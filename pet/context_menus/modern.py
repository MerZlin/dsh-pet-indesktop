# -*- coding: utf-8 -*-
"""Modern context-menu adapter driven by the shared Menu Action Model."""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from ..menu_layout import resolve_menu_layout
from .registry import MENU_ACTIONS


def build_modern_menu(menu: QMenu, pet, template: dict) -> None:
    """Resolve user/default layout and populate one real Qt menu."""
    del template
    result = resolve_menu_layout(
        pet.cfg.get("context_menu_layout"),
        registered_actions=MENU_ACTIONS.ids,
        available_actions=MENU_ACTIONS.available_ids(pet),
    )
    menu.setProperty("menuLayoutSource", result.source)
    menu.setProperty("menuLayoutDiagnostics", list(result.diagnostics))
    MENU_ACTIONS.populate(menu, pet, result.nodes)
