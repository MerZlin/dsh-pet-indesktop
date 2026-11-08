from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon

from ..platform import macos


class TrayIcon(QSystemTrayIcon):
    def __init__(
        self,
        menu_builder,
        icon_path: Path,
        toggle_visibility=None,
        parent=None,
    ):
        icon = QIcon(str(icon_path)) if Path(icon_path).exists() else QIcon()
        super().__init__(icon, parent)
        self._menu_builder = menu_builder
        self._toggle_visibility = toggle_visibility
        self._retired: list = []
        self._cocoa = macos.is_cocoa()
        self._menu_session = None
        self.setToolTip("dsh-pet core")
        self._menu = menu_builder(None)
        self._bind_popup_session()
        if not self._cocoa:
            self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def _bind_popup_session(self) -> None:
        if self._cocoa:
            self._menu.aboutToShow.connect(self._begin_popup_session)
            self._menu.aboutToHide.connect(self._end_popup_session)

    def _begin_popup_session(self) -> None:
        from .menu import menu_session

        if self._menu_session is None:
            self._menu_session = menu_session(self._menu)
            self._menu_session.__enter__()

    def _end_popup_session(self) -> None:
        if self._menu_session is not None:
            session, self._menu_session = self._menu_session, None
            session.__exit__(None, None, None)

    def _on_activated(self, reason) -> None:
        if self._cocoa and reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.Context,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            # Cocoa starts tracking an assigned NSMenu before aboutToShow.
            # Rebuilding its dynamic actions there dismisses that native menu.
            # Use the same QMenu popup/session as the pet's context menu, while
            # retaining the native status item as the always-available entry.
            self._menu.popup(self.geometry().bottomLeft())
            return
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if callable(self._toggle_visibility):
                self._toggle_visibility()
                return

    def refresh(self) -> None:
        refresher = getattr(self._menu, "_petcore_refresh", None)
        if callable(refresher):
            # 原地重建动作，绝不替换 Qt 正在使用的 context menu。
            refresher()
            return
        old = self._menu
        self._menu = self._menu_builder(None)
        self._bind_popup_session()
        if not self._cocoa:
            self.setContextMenu(self._menu)
        self._retired.append(old)
        QTimer.singleShot(0, lambda: self._drop_retired(old))

    def _drop_retired(self, menu) -> None:
        try:
            self._retired.remove(menu)
        except ValueError:
            pass
