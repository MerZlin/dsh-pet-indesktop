# -*- coding: utf-8 -*-
"""Modern context-menu adapter driven by the shared Menu Action Model."""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from ..menu_layout import resolve_menu_layout
from .menu_styles.modern import set_square_corners, square_corners_enabled
from .registry import MENU_ACTIONS

#: 「桌宠控制」子菜单在默认模板里的标题（用户可能用 alias 改名，所以再留一条
#: 「含开机自启」的兜底识别）。
_PET_CONTROLS_TITLE = "桌宠控制"


def _pet_controls_submenu(menu: QMenu) -> QMenu | None:
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None and submenu.title() == _PET_CONTROLS_TITLE:
            return submenu
    for action in menu.actions():
        submenu = action.menu()
        if submenu is None:
            continue
        texts = {child.text() for child in submenu.actions()}
        if "开机自启" in texts:
            return submenu
    return None


def _install_corner_toggle(menu: QMenu) -> None:
    """在「桌宠控制」里挂一个「菜单直角」开关（勾选=直角，取消=圆角）。

    外观是在建树之前应用的，所以这里改的是**下一次**右键：点一下菜单收起，
    重新右键就能看到新的形状。
    """
    submenu = _pet_controls_submenu(menu)
    if submenu is None:
        return
    action = submenu.addAction("菜单直角")
    action.setCheckable(True)
    action.setChecked(square_corners_enabled())

    def _toggle(checked: bool) -> None:
        set_square_corners(bool(checked))
        menu.close()

    action.toggled.connect(_toggle)


def build_modern_menu(menu: QMenu, pet) -> None:
    """Resolve user/default layout and populate one real Qt menu."""
    result = resolve_menu_layout(
        pet.cfg.get("context_menu_layout"),
        registered_actions=MENU_ACTIONS.ids,
        available_actions=MENU_ACTIONS.available_ids(pet),
    )
    MENU_ACTIONS.populate(
        menu, pet, result.nodes, enabled_actions=MENU_ACTIONS.enabled_ids(pet)
    )
    _install_corner_toggle(menu)
