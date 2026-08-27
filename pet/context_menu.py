# -*- coding: utf-8 -*-
"""Single maintained modern context-menu dispatcher."""
from __future__ import annotations

import json
from importlib import resources

from PySide6.QtWidgets import QMenu

from .context_menus import build_modern_menu
from .context_menus.icons import pet_avatar_menu_icon, vector_menu_icon
from .context_menus.menu_styles import (
    apply_modern_menu_style,
    install_modern_check_indicators,
)
from .context_menus.menu_styles.common import install_responsive_menu_style, install_stay_open_interaction

TEMPLATE_IDS = ("legacy", "modern")


def load_menu_template(template_id: str) -> dict:
    template_id = str(template_id or "legacy").strip().lower()
    if template_id not in TEMPLATE_IDS:
        template_id = "legacy"
    path = resources.files("pet.menu_templates").joinpath(f"{template_id}.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("id") != template_id or not isinstance(data.get("groups"), list):
        raise ValueError(f"无效的右键菜单模板: {template_id}")
    return data


def populate_context_menu(menu: QMenu, pet) -> None:
    template = load_menu_template("modern")
    apply_modern_menu_style(menu, pet.cfg.get("context_menu_appearance", {}))
    build_modern_menu(menu, pet, template)
    install_modern_check_indicators(menu)
    install_responsive_menu_style(menu)
    install_stay_open_interaction(menu)


__all__ = [
    "TEMPLATE_IDS",
    "load_menu_template",
    "populate_context_menu",
    "pet_avatar_menu_icon",
    "vector_menu_icon",
]
