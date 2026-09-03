# -*- coding: utf-8 -*-
"""Single maintained modern context-menu dispatcher."""
from __future__ import annotations

import json
from importlib import resources

from PySide6.QtWidgets import QMenu

from .context_menus import build_legacy_menu, build_modern_menu
from .context_menus.icons import pet_avatar_menu_icon, vector_menu_icon
from .context_menus.menu_styles import (
    apply_modern_menu_style,
    install_modern_check_indicators,
)
from .context_menus.menu_styles.common import install_responsive_menu_style, install_stay_open_interaction

TEMPLATE_IDS = ("legacy", "modern")

# 内置兜底模板：打包缺资源（如某个平台漏 --add-data）时菜单仍可用，
# 结构与 pet/menu_templates/*.json 保持一致。
_FALLBACK_TEMPLATES = {
    "modern": {
        "id": "modern",
        "name": "新版菜单",
        "switch_to": "legacy",
        "switch_label": "切换回旧版菜单",
        "groups": [
            {"id": "interaction", "items": ["ojingjing", "chat"]},
            {"id": "playback", "items": ["animations_hub", "character"]},
            {"id": "functions", "items": ["playback_speed", "size", "drag_physics", "return_corner", "no_move", "on_top", "autostart", "spawn_pet"]},
            {"id": "tools", "items": ["harness", "deepseek_web", "quick_launch"]},
            {"id": "settings", "items": ["modern_settings"]},
            {"id": "template", "items": ["switch_template"]},
            {"id": "exit", "items": ["quit"]},
        ],
    },
    "legacy": {
        "id": "legacy",
        "name": "旧版菜单",
        "switch_to": "modern",
        "switch_label": "切换到新版菜单",
        "groups": [
            {"id": "assistant", "items": ["chat", "chat_settings", "pet_settings"]},
            {"id": "spawn", "items": ["spawn_pet"]},
            {"id": "animation", "items": ["animation_categories", "playback_speed", "drag_physics", "character"]},
            {"id": "window", "items": ["return_corner", "on_top", "no_move", "autostart", "size"]},
            {"id": "tools", "items": ["harness"]},
            {"id": "template", "items": ["switch_template"]},
            {"id": "exit", "items": ["quit"]},
        ],
    },
}


def normalize_template_id(template_id) -> str:
    """把配置里的菜单模板标识归一化为合法值。

    缺失/空值/非法值一律回退 ``modern``（现行默认模板）。
    load_menu_template 与 populate_context_menu 两个入口共用此函数，
    避免同一非法配置在两个入口回退结果不一致（历史分歧：前者曾回退 legacy）。
    """
    if template_id is None:
        return "modern"
    tid = str(template_id).strip().lower()
    return tid if tid in TEMPLATE_IDS else "modern"


def load_menu_template(template_id: str) -> dict:
    template_id = normalize_template_id(template_id)
    path = resources.files("pet.menu_templates").joinpath(f"{template_id}.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # 打包资源缺失时回退内置模板，保证右键菜单始终可用
        return dict(_FALLBACK_TEMPLATES[template_id])
    if data.get("id") != template_id or not isinstance(data.get("groups"), list):
        raise ValueError(f"无效的右键菜单模板: {template_id}")
    return data


def populate_context_menu(menu: QMenu, pet) -> None:
    # 按配置分发新旧菜单模板（legacy 仅供偏好旧交互的用户；「切换菜单模板」
    # 项通过 set_context_menu_template + reopen_context_menu 立即生效）
    cfg = getattr(pet, "cfg", None)
    template_id = normalize_template_id(
        cfg.get("context_menu_template", "modern") if cfg is not None else "modern"
    )
    template = load_menu_template(template_id)
    if template_id == "legacy":
        build_legacy_menu(menu, pet, template)
        install_responsive_menu_style(menu)
        install_stay_open_interaction(menu)
        return
    apply_modern_menu_style(menu, cfg.get("context_menu_appearance", {}) if cfg is not None else {})
    build_modern_menu(menu, pet, template)
    install_modern_check_indicators(menu)
    install_responsive_menu_style(menu)
    install_stay_open_interaction(menu)


__all__ = [
    "TEMPLATE_IDS",
    "load_menu_template",
    "normalize_template_id",
    "populate_context_menu",
    "pet_avatar_menu_icon",
    "vector_menu_icon",
]
