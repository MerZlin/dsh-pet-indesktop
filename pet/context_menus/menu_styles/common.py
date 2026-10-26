# -*- coding: utf-8 -*-
"""Shared style tokens and submenu inheritance without layout assumptions."""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMenu, QProxyStyle, QStyle

# 右键菜单样式表的 font-family 在 Windows 上必须是**单族**，而且该族要覆盖菜单里的中文。
# 2026-09-28 真 Qt(Windows 6.11) 分步实测：QFontDatabase 的族枚举本身只要 ~16MB，但只要
# 用一个「请求族覆盖不了待测文本」或「多族列表」的 QFont 去测量中文条目，Qt 就会为缺失
# 字形做回退搜索而枚举整个字体库，把 C:\Windows\Fonts 的 528 个字体文件（~831MB）映射进
# 进程——菜单弹出一次后 msyh.ttc 37.6MB、StaticCache.dat 19.2MB、simhei/arial 等常驻，
# RSS +70~150MB 不回。旧 macOS 三族栈、把它换成 Windows 三族栈、单族但不存在，三种写法
# 实测都触发，所以 Windows 直接给一个覆盖中文的系统 UI 单族。
if sys.platform == "win32":
    SYSTEM_FONT_STACK = '"Microsoft YaHei UI"'
elif sys.platform == "darwin":
    SYSTEM_FONT_STACK = '"SF Pro Text", ".AppleSystemUIFont", "PingFang SC"'
else:
    SYSTEM_FONT_STACK = '"Noto Sans CJK SC", "WenQuanYi Micro Hei", "sans-serif"'


class ResponsiveMenuStyle(QProxyStyle):
    """Keep submenu aim protection without its one-second sibling stall."""

    _MENU_HINTS = {
        QStyle.StyleHint.SH_Menu_Scrollable: 1,
        QStyle.StyleHint.SH_Menu_SubMenuPopupDelay: 60,
        QStyle.StyleHint.SH_Menu_SubMenuSloppyCloseTimeout: 120,
        QStyle.StyleHint.SH_Menu_SubMenuSloppySelectOtherActions: 1,
        QStyle.StyleHint.SH_Menu_SubMenuUniDirection: 0,
        QStyle.StyleHint.SH_Menu_SubMenuUniDirectionFailCount: 1,
    }

    def styleHint(self, hint, option=None, widget=None, return_data=None):  # noqa: N802 - Qt API
        if isinstance(widget, QMenu) and hint in self._MENU_HINTS:
            return self._MENU_HINTS[hint]
        return super().styleHint(hint, option, widget, return_data)


def install_responsive_menu_style(app: QApplication) -> None:
    # QMenu 在构造时缓存交互参数；创建后 setStyle 不会刷新这些缓存。
    if not isinstance(app.style(), ResponsiveMenuStyle):
        app.setStyle(ResponsiveMenuStyle(app.style()))


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
    """把父菜单的现代外观原样套到子菜单上。

    旧版样式（``menuStyle == "legacy"``）随旧版菜单退役后不复存在：本函数
    永远落现代分支，``menuStyle`` 缺失/为空时也按现代处理——否则任何
    "父菜单还没打上 modern 属性"的子菜单都会静默掉样式（树里一处没套上，
    那棵子树就露原生外观）。
    """
    from .modern import apply_modern_menu_style

    apply_modern_menu_style(submenu, {
        "theme": parent.property("modernTheme") or "system",
        "density": parent.property("modernDensity") or "standard",
        "corner_radius": parent.property("modernCornerRadius") or 12,
        **dict(parent.property("modernAppearance") or {}),
    })
