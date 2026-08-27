# -*- coding: utf-8 -*-
"""Shared style tokens and submenu inheritance without layout assumptions."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QMenu, QProxyStyle, QStyle

SYSTEM_FONT_STACK = '"SF Pro Text", ".AppleSystemUIFont", "PingFang SC"'
ICON_COLOR = "#55585c"


class ResponsiveMenuStyle(QProxyStyle):
    """Keep submenu aim protection without its one-second sibling stall."""

    def styleHint(self, hint, option=None, widget=None, return_data=None):  # noqa: N802 - Qt API
        overrides = {
            QStyle.StyleHint.SH_Menu_Scrollable: 1,
            QStyle.StyleHint.SH_Menu_SubMenuPopupDelay: 60,
            QStyle.StyleHint.SH_Menu_SubMenuSloppyCloseTimeout: 120,
            QStyle.StyleHint.SH_Menu_SubMenuSloppySelectOtherActions: 1,
            QStyle.StyleHint.SH_Menu_SubMenuUniDirection: 0,
            QStyle.StyleHint.SH_Menu_SubMenuUniDirectionFailCount: 1,
        }
        if hint in overrides:
            return overrides[hint]
        return super().styleHint(hint, option, widget, return_data)

def install_responsive_menu_style(menu: QMenu) -> None:
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            install_responsive_menu_style(submenu)
    proxy = ResponsiveMenuStyle()
    proxy.setParent(menu)
    menu.setStyle(proxy)
    menu._responsive_menu_style = proxy


class StayOpenMenuFilter(QObject):
    """Trigger leaf commands without treating the click as a menu dismissal.

    Opening another window or clicking away still closes the popup naturally;
    local toggles and animation commands remain available for rapid adjustment.
    """

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if isinstance(watched, QMenu) and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            action = watched.actionAt(event.position().toPoint())
            if action is None or action.isSeparator() or action.menu() is not None or not action.isEnabled():
                return False
            if bool(action.property("closeOnTrigger")):
                return False
            action.trigger()
            event.accept()
            return True
        return False


def install_stay_open_interaction(menu: QMenu) -> None:
    filters = []
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            install_stay_open_interaction(submenu)
    event_filter = StayOpenMenuFilter(menu)
    menu.installEventFilter(event_filter)
    filters.append(event_filter)
    menu._stay_open_filters = filters


def system_ui_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def inherit_menu_style(parent: QMenu, submenu: QMenu) -> None:
    style_id = str(parent.property("menuStyle") or "legacy")
    if style_id == "modern":
        from .modern import apply_modern_menu_style

        apply_modern_menu_style(submenu, {
            "theme": parent.property("modernTheme") or "system",
            "density": parent.property("modernDensity") or "standard",
            "corner_radius": parent.property("modernCornerRadius") or 12,
            **dict(parent.property("modernAppearance") or {}),
        })
    else:
        from .legacy import apply_legacy_menu_style

        apply_legacy_menu_style(submenu)
        submenu.setFont(parent.font())
