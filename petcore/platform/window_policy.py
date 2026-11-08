"""Shared passive-window policy; UI actions keep normal activation behavior."""
from __future__ import annotations

import sys
import weakref

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QWidget

from . import macos


def configure_application():
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        # GTK native dialogs load the host's IBus modules into bundled GLib.
        # Qt's own dialogs use the same verified input plugin as our settings.
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)


def passive_flags(flags):
    if sys.platform != "darwin":
        return flags
    # NSPanel gets ordered out during Space transitions (#418).
    return ((flags & ~Qt.WindowType.WindowType_Mask) | Qt.WindowType.Window
            | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)


def apply(window, *, passive=True):
    if not macos.is_cocoa():
        return
    macos.apply_window(int(window.winId()), on_top=bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint), passive=passive)


def raise_passive(window):
    if macos.is_cocoa():
        apply(window)
        macos.order_front(int(window.winId()))
    elif sys.platform != "darwin":
        window.raise_()


class _Policy(QObject):
    def __init__(self, window, passive):
        super().__init__(window)
        self._window, self.passive = weakref.ref(window), passive
        self.pending = False

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            if not self.pending:
                self.pending = True
                QTimer.singleShot(0, self, self.reapply)
        return False

    def reapply(self):
        self.pending = False
        window = self._window()
        if window is not None and window.isVisible():
            apply(window, passive=self.passive)


def install(window: QWidget, *, passive=True):
    if passive:
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        if sys.platform == "darwin":
            window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
    if not macos.is_cocoa():
        return
    if getattr(window, "_desktop_policy", None) is None:
        policy = _Policy(window, passive)
        window._desktop_policy = policy
        window.installEventFilter(policy)
