# -*- coding: utf-8 -*-
"""Modern context-menu layout with seven explicit functional groups."""
from __future__ import annotations

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu

from .fun_entry import add_ojingjing_entry
from .quick_launch import add_quick_launch_menu
from .shared import (
    add_action,
    add_agent_link_menu,
    add_proactive_menu,
    add_autostart,
    add_drag_physics,
    add_hide_pet,
    add_look_screen,
    add_balance,
    add_deepseek_web,
    add_harness,
    add_mouse_through,
    add_no_move,
    add_on_top,
    add_quit,
    add_return_corner,
    add_spawn_pet,
    add_submenu,
    add_template_switch,
    add_update_help,
    build_animation_categories,
    build_character_menu,
    build_size_menu,
    build_speed_menu,
    DEEPSEEK_WEB_URL,
)


def build_modern_menu(menu: QMenu, pet, template: dict) -> None:
    """Build the modern layout without importing or mutating legacy UI code."""
    def start_group() -> None:
        actions = menu.actions()
        if actions and not actions[-1].isSeparator():
            menu.addSeparator()

    # Playful profile-style entry: deliberately exclusive to the modern menu.
    easter_egg = pet.cfg.get("menu_easter_egg", {})
    if easter_egg.get("enabled", True):
        add_ojingjing_entry(menu, easter_egg)
        menu.addSeparator()

    # 1. 交互：AI 对话按设置选择现代/经典窗口。
    chat = getattr(pet, "on_open_chat", None)
    if chat is not None:
        add_action(menu, "AI 对话", "chat", chat, close_on_trigger=True)
    add_look_screen(menu, pet)

    # 2. 播放。
    start_group()
    animations = add_submenu(menu, "播放动画", "play")
    build_animation_categories(animations, pet, icons=False, leaf_role_icons=True)
    build_character_menu(menu, pet)

    # 3. 功能：直接铺在一级菜单，分隔线承担分类职责。
    start_group()
    build_speed_menu(menu, pet)
    build_size_menu(menu, pet)
    add_drag_physics(menu, pet)
    add_return_corner(menu, pet)
    add_hide_pet(menu, pet)
    add_no_move(menu, pet)
    add_mouse_through(menu, pet)
    add_on_top(menu, pet)
    add_autostart(menu, pet)
    add_spawn_pet(menu, pet)

    # 4. 工具：同样直接显示，避免为了两个动作增加一级导航。
    start_group()
    add_balance(menu, pet)
    # 纯桌宠（无 Chat/DSH 联动）版本不再显示“启动 DeepSeek Harness”，
    # 仅保留“打开网页版 DeepSeek”。
    if getattr(pet, "on_open_chat", None) is not None:
        add_harness(menu, pet)
    add_deepseek_web(menu)
    add_quick_launch_menu(menu, pet.cfg)
    add_update_help(menu, pet)

    # 4.5 识屏与联动（本 PR 新增功能组；主动识屏仅 Windows + 有聊天能力时显示）
    start_group()
    add_proactive_menu(menu, pet)
    add_agent_link_menu(menu, pet)

    # 5. 现代桌宠设置面板（包含 AI 设置侧栏页）。
    start_group()
    modern_settings = getattr(pet, "on_open_modern_settings", None)
    if modern_settings is not None:
        add_action(menu, "桌宠设置", "settings", modern_settings, close_on_trigger=True)

    # 5.5 模板切换：现代版菜单也要能切回旧版（模板声明了 switch_to，运行时必须落实）
    switch_to = str(template.get("switch_to", "") or "")
    if switch_to:
        start_group()
        add_template_switch(menu, pet, str(template.get("switch_label") or "切换回旧版菜单"), switch_to)

    # 6. 退出。
    start_group()
    add_quit(menu, pet)
