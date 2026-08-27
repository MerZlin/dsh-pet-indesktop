# -*- coding: utf-8 -*-
"""Legacy context-menu layout.

Keep this module intentionally independent from ``modern.py``: preserving the
original interaction model is more important than sharing layout code.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from .shared import (
    add_action,
    add_autostart,
    add_drag_physics,
    add_deepseek_web,
    add_harness,
    add_no_move,
    add_on_top,
    add_quit,
    add_return_corner,
    add_spawn_pet,
    add_template_switch,
    build_animation_categories,
    build_character_menu,
    build_size_menu,
    build_speed_menu,
)


def build_legacy_menu(menu: QMenu, pet, template: dict) -> None:
    """Build only the classic flat menu and its original settings entry."""
    chat = getattr(pet, "on_open_chat", None)
    chat_settings = getattr(pet, "on_open_chat_settings", None)
    legacy_settings = getattr(pet, "on_open_legacy_settings", None)
    if chat is not None:
        add_action(menu, "AI 对话", None, chat, close_on_trigger=True)
    if chat_settings is not None:
        add_action(menu, "AI 设置", None, chat_settings, close_on_trigger=True)
    if legacy_settings is not None:
        add_action(menu, "桌宠设置", None, legacy_settings, close_on_trigger=True)

    menu.addSeparator()
    build_animation_categories(menu, pet, icons=False, legacy_labels=True)
    build_speed_menu(menu, pet, icons=False)
    add_drag_physics(menu, pet, icons=False)
    build_character_menu(menu, pet, icons=False)

    menu.addSeparator()
    add_return_corner(menu, pet, icons=False)
    add_on_top(menu, pet, icons=False)
    add_no_move(menu, pet, icons=False)
    add_autostart(menu, icons=False)
    add_spawn_pet(menu, pet)
    build_size_menu(menu, pet, icons=False)

    menu.addSeparator()
    add_harness(menu, pet, icons=False)
    add_deepseek_web(menu, icons=False)

    menu.addSeparator()
    add_template_switch(menu, pet, str(template["switch_label"]), str(template["switch_to"]), icons=False)

    menu.addSeparator()
    add_quit(menu, pet, icons=False)
