"""Dispatch only after native menu tracking has released focus/input grabs."""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu

logger = logging.getLogger(__name__)


def connect_action(action, callback):
    menus = []
    parent = action.parent()
    while parent is not None:
        if isinstance(parent, QMenu):
            menus.append(parent)
            if not getattr(parent, "_petcore_tracking_installed", False):
                parent._petcore_tracking_installed = True
                parent._petcore_tracking_open = False
                parent.aboutToShow.connect(lambda m=parent: setattr(m, "_petcore_tracking_open", True))
                parent.aboutToHide.connect(lambda m=parent: setattr(m, "_petcore_tracking_open", False))
        parent = parent.parent()

    def busy(menu):
        try:
            visible = menu.isVisible()
        except RuntimeError:
            return False
        native_tracking = QGuiApplication.platformName() == "cocoa"
        return visible or (native_tracking and bool(getattr(menu, "_petcore_tracking_open", False)))

    def triggered(checked=False):
        args = (checked,)
        def invoke():
            import inspect
            try:
                inspect.signature(callback).bind(*args)
            except (TypeError, ValueError):
                callback()
            else:
                callback(*args)

        from .menu import menu_open
        if QGuiApplication.platformName() != "cocoa" and not menu_open() and not any(busy(menu) for menu in menus):
            invoke()
            return

        def dispatch():
            if any(busy(menu) for menu in menus) or menu_open():
                QTimer.singleShot(20, dispatch)
                return
            # QObject signal connections trim excess signal arguments. Do the
            # same for Python wrappers, without catching callback TypeErrors.
            invoke()

        # Cocoa emits aboutToHide from NSMenuDelegate.menuDidClose. Waiting for
        # that signal avoids guessing native fade timing with a fixed delay.
        QTimer.singleShot(0, dispatch)
    action.triggered.connect(triggered)
