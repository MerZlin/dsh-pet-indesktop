"""Action registration and tree rendering for the shared Menu Action Model."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu

from ..config import DEFAULT_MENU_EASTER_EGG
from .fun_entry import add_ojingjing_entry
from .quick_launch import add_quick_launch_menu, configured_quick_apps
from .shared import (
    QUARK_PAN_URL,
    REPO_URL,
    add_action,
    add_agent_link_menu,
    add_autostart,
    add_balance,
    add_deepseek_web,
    add_drag_physics,
    add_harness,
    add_hide_pet,
    add_look_screen,
    add_mouse_through,
    add_no_move,
    add_on_top,
    add_proactive_menu,
    add_quit,
    add_return_corner,
    add_spawn_pet,
    add_submenu,
    build_animation_categories,
    build_character_menu,
    build_size_menu,
    build_speed_menu,
)


ACTION_LABELS = {
    "ojingjing": "厉害了我的鲸", "chat": "AI 对话", "look_screen": "看看屏幕",
    "animations_hub": "播放动画", "character": "切换角色", "playback_speed": "播放速率",
    "size": "大小", "drag_physics": "拖动物理", "no_move": "不移动",
    "mouse_through": "鼠标穿透", "on_top": "窗口置顶", "autostart": "开机自启",
    "return_corner": "回到右下角", "hide_pet": "隐藏桌宠", "spawn_pet": "生小肥鱼",
    "quick_launch": "快捷启动", "balance": "DeepSeek 余额",
    "harness": "启动 DeepSeek Harness", "deepseek_web": "打开网页版 DeepSeek",
    "check_update": "检查更新", "github_project": "GitHub 项目页",
    "quark_download": "夸克网盘下载", "agent_link": "Agent 联动",
    "proactive_screen": "主动识屏", "modern_settings": "桌宠设置", "quit": "退出",
}


Builder = Callable[[QMenu, object], object]
Availability = Callable[[object], bool]


@dataclass(frozen=True)
class MenuActionSpec:
    build: Builder
    available: Availability = lambda _pet: True


def _callback_available(name: str) -> Availability:
    return lambda pet: callable(getattr(pet, name, None))


def _build_chat(menu, pet):
    return add_action(menu, "AI 对话", "chat", pet.on_open_chat, close_on_trigger=True)


def _build_animations(menu, pet):
    submenu = add_submenu(menu, "播放动画", "play")
    build_animation_categories(submenu, pet, icons=False, leaf_role_icons=True)
    return submenu


def _build_settings(menu, pet):
    return add_action(menu, "桌宠设置", "settings", pet.on_open_modern_settings, close_on_trigger=True)


def _build_check_update(menu, pet):
    return add_action(menu, "检查更新", "update", lambda: pet.on_check_update(pet), close_on_trigger=True)


def _build_github(menu, _pet):
    return add_action(
        menu,
        "GitHub 项目页",
        "web",
        lambda: QDesktopServices.openUrl(QUrl(REPO_URL)),
        close_on_trigger=True,
    )


def _build_quark(menu, _pet):
    return add_action(
        menu,
        "夸克网盘下载",
        "download",
        lambda: QDesktopServices.openUrl(QUrl(QUARK_PAN_URL)),
        close_on_trigger=True,
    )


class MenuActionRegistry:
    """Resolve capability-aware action IDs and render a resolved menu tree."""

    def __init__(self) -> None:
        self._specs = {
            "ojingjing": MenuActionSpec(
                lambda menu, pet: add_ojingjing_entry(
                    menu, pet.cfg.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG)
                ),
                lambda pet: bool(
                    pet.cfg.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG).get("enabled", True)
                ),
            ),
            "chat": MenuActionSpec(_build_chat, _callback_available("on_open_chat")),
            "look_screen": MenuActionSpec(add_look_screen, _callback_available("on_look_screen")),
            "animations_hub": MenuActionSpec(_build_animations),
            "character": MenuActionSpec(lambda menu, pet: build_character_menu(menu, pet)),
            "playback_speed": MenuActionSpec(lambda menu, pet: build_speed_menu(menu, pet)),
            "size": MenuActionSpec(lambda menu, pet: build_size_menu(menu, pet)),
            "drag_physics": MenuActionSpec(add_drag_physics),
            "no_move": MenuActionSpec(add_no_move),
            "mouse_through": MenuActionSpec(add_mouse_through),
            "on_top": MenuActionSpec(add_on_top),
            "autostart": MenuActionSpec(lambda menu, pet: add_autostart(menu, pet)),
            "return_corner": MenuActionSpec(add_return_corner),
            "hide_pet": MenuActionSpec(add_hide_pet),
            "spawn_pet": MenuActionSpec(add_spawn_pet, _callback_available("on_spawn_pet")),
            "quick_launch": MenuActionSpec(
                lambda menu, pet: add_quick_launch_menu(menu, pet.cfg),
                lambda pet: bool(configured_quick_apps(pet.cfg)),
            ),
            "balance": MenuActionSpec(add_balance, _callback_available("on_show_balance")),
            "harness": MenuActionSpec(add_harness),
            "deepseek_web": MenuActionSpec(lambda menu, pet: add_deepseek_web(menu)),
            "check_update": MenuActionSpec(_build_check_update, _callback_available("on_check_update")),
            "github_project": MenuActionSpec(_build_github),
            "quark_download": MenuActionSpec(_build_quark, lambda _pet: sys.platform == "win32"),
            "agent_link": MenuActionSpec(add_agent_link_menu),
            "proactive_screen": MenuActionSpec(
                add_proactive_menu,
                lambda pet: sys.platform == "win32" and callable(getattr(pet, "on_open_chat", None)),
            ),
            "modern_settings": MenuActionSpec(
                _build_settings, _callback_available("on_open_modern_settings")
            ),
            "quit": MenuActionSpec(add_quit),
        }

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._specs)

    def available_ids(self, pet) -> frozenset[str]:
        return frozenset(
            action_id
            for action_id, spec in self._specs.items()
            if spec.available(pet)
        )

    def label(self, action_id: str) -> str:
        return ACTION_LABELS.get(action_id, action_id)

    def populate(self, menu: QMenu, pet, nodes) -> None:
        previous_section = None
        rendered = 0
        for node in nodes:
            section = node.get("section")
            if rendered and section and previous_section and section != previous_section:
                menu.addSeparator()
            if node.get("type") == "submenu":
                submenu = add_submenu(menu, str(node.get("label") or ""))
                self.populate(submenu, pet, node.get("children", ()))
            else:
                spec = self._specs.get(str(node.get("id") or ""))
                if spec is not None:
                    spec.build(menu, pet)
            rendered += 1
            if section:
                previous_section = section


MENU_ACTIONS = MenuActionRegistry()
