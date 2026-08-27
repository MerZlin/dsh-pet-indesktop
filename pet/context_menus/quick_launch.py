# -*- coding: utf-8 -*-
"""Configurable application shortcuts used only by the modern menu."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QFileInfo, QRectF, QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QFileIconProvider, QMenu

from ..config import DEFAULT_QUICK_LAUNCH_APPS
from .icons import vector_menu_icon
from .shared import add_submenu, connect_action


def configured_quick_apps(config) -> list[dict]:
    value = config.get("quick_launch_apps", DEFAULT_QUICK_LAUNCH_APPS)
    return [dict(item) for item in value if isinstance(item, dict)]


def quick_app_icon(menu: QMenu, item: dict) -> QIcon:
    if item.get("kind") == "default_browser":
        return vector_menu_icon(menu, "web")
    path = str(item.get("path") or "")
    icon = QFileIconProvider().icon(QFileInfo(path)) if path else QIcon()
    return fitted_application_icon(icon, 18, menu) if not icon.isNull() else vector_menu_icon(menu, "application")


def fitted_application_icon(icon: QIcon, size: int, widget) -> QIcon:
    """Crop provider padding and fill the requested logical icon canvas."""
    if icon.isNull():
        return icon
    dpr = max(1.0, widget.devicePixelRatioF())
    source_size = max(32, round(size * dpr * 2))
    source = icon.pixmap(source_size, source_size)
    source.setDevicePixelRatio(1.0)
    bounds = QRegion(source.mask()).boundingRect()
    if bounds.isEmpty():
        return icon
    canvas = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(
        QRectF(0.5, 0.5, size - 1.0, size - 1.0),
        source,
        QRectF(bounds),
    )
    painter.end()
    return QIcon(canvas)


def launch_quick_app(item: dict) -> bool:
    if item.get("kind") == "default_browser":
        return bool(QDesktopServices.openUrl(QUrl("https://www.google.com/")))
    path = os.path.abspath(os.path.expanduser(str(item.get("path") or "")))
    if not path:
        return False
    if sys.platform == "darwin":
        return bool(QProcess.startDetached("open", [path]))
    if sys.platform == "win32":
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if os.path.isdir(path):
        return bool(QProcess.startDetached("xdg-open", [path]))
    return bool(QProcess.startDetached(path, []))


def add_quick_launch_menu(menu: QMenu, config) -> QMenu | None:
    apps = configured_quick_apps(config)
    if not apps:
        return None
    submenu = add_submenu(menu, "快捷启动", "application")
    for item in apps:
        action = submenu.addAction(quick_app_icon(submenu, item), str(item.get("name") or "应用"))
        action.setProperty("closeOnTrigger", True)
        connect_action(action, lambda item=dict(item): launch_quick_app(item))
    return submenu
