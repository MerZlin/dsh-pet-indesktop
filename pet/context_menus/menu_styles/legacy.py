# -*- coding: utf-8 -*-
"""Original/native menu appearance used exclusively by the legacy layout."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu


def apply_legacy_menu_style(menu: QMenu) -> None:
    menu.setObjectName("legacyContextMenu")
    menu.setProperty("menuStyle", "legacy")
    menu.setStyleSheet("")
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
