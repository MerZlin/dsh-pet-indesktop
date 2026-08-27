# -*- coding: utf-8 -*-
"""Frameless, stackable image windows for the playful menu entry."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCloseEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def oijingjing_image_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "big_blue_fat_fish" / "ojingjing.jpg"


def resolve_fun_asset(path: str | Path | None, fallback: Path) -> Path:
    if path is None or not str(path).strip():
        return fallback
    candidate = Path(str(path or "")).expanduser()
    if candidate.is_absolute():
        return candidate
    bundled = Path(__file__).resolve().parents[1] / candidate
    return bundled if bundled.exists() else fallback


def popup_image_paths(directory: str | Path | None = None) -> list[Path]:
    directory = resolve_fun_asset(directory, oijingjing_image_path().parent)
    supported = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in supported)


class OjingjingWindow(QWidget):
    def __init__(
        self,
        image_path: Path,
        close_all: Callable[[], None],
        on_closed: Callable[[QWidget], None],
        title: str = "厉害了我的鲸",
    ) -> None:
        super().__init__(None)
        self._on_closed = on_closed
        self._drag_start: QPoint | None = None
        self._window_start: QPoint | None = None
        self.setObjectName("ojingjingImageWindow")
        self.setProperty("sourceImage", str(image_path))
        self.setWindowTitle(str(title or "厉害了我的鲸"))
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        side = 520 if available is None else max(280, min(560, int(available.height() * 0.64)))

        card = QWidget(self)
        card.setObjectName("ojingjingCard")
        card.setStyleSheet(
            "#ojingjingCard { background: white; border: 1px solid #d8d8d8; "
            "border-radius: 16px; }"
            "QPushButton { min-height: 30px; padding: 0 16px; border: 1px solid #d5d5d5; "
            "border-radius: 8px; background: #f7f7f7; color: #222; font-size: 13px; }"
            "QPushButton:hover { background: #ececec; }"
            "QPushButton#closeAllButton { background: #202020; color: white; border-color: #202020; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(10)

        image = QLabel(card)
        image.setObjectName("ojingjingFullImage")
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source = QPixmap(str(image_path))
        image.setPixmap(
            source.scaled(
                side,
                side,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        image.setFixedSize(side, side)
        image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout.addWidget(image)

        controls = QHBoxLayout()
        controls.addStretch(1)
        close_button = QPushButton("关闭", card)
        close_button.setObjectName("closeButton")
        close_button.clicked.connect(self.close)
        controls.addWidget(close_button)
        close_all_button = QPushButton("全部关闭", card)
        close_all_button.setObjectName("closeAllButton")
        close_all_button.clicked.connect(close_all)
        controls.addWidget(close_all_button)
        card_layout.addLayout(controls)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(card)
        # Avoid QWidget.adjustSize() here: on the Cocoa plugin a freshly
        # created translucent frameless tool window can crash while querying
        # the native frame. Its content dimensions are deterministic.
        self.setFixedSize(side + 36, side + 82)

    def begin_drag_at(self, global_pos: QPoint) -> None:
        self._drag_start = QPoint(global_pos)
        self._window_start = self.pos()

    def drag_to(self, global_pos: QPoint) -> None:
        if self._drag_start is None or self._window_start is None:
            return
        self.move(self._window_start + global_pos - self._drag_start)

    def end_drag(self) -> None:
        self._drag_start = None
        self._window_start = None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_drag_at(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.drag_to(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            self.drag_to(event.globalPosition().toPoint())
            self.end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._on_closed(self)
        super().closeEvent(event)


class OjingjingWindowManager:
    def __init__(self) -> None:
        self.windows: list[OjingjingWindow] = []

    def _forget(self, window: QWidget) -> None:
        self.windows = [item for item in self.windows if item is not window]

    def open_window(self, *, show: bool = True, config: dict | None = None) -> OjingjingWindow:
        config = dict(config or {})
        paths = popup_image_paths(config.get("image_dir"))
        if not paths:
            raise FileNotFoundError(f"弹窗图片目录为空: {oijingjing_image_path().parent}")
        if show:
            self.restore_all()
        window = OjingjingWindow(
            random.choice(paths), self.close_all, self._forget,
            title=str(config.get("title") or "厉害了我的鲸"),
        )
        offset = (len(self.windows) % 7) * 24
        window.setProperty("cascadeOffset", offset)
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x = available.center().x() - window.width() // 2 + offset
            y = available.center().y() - window.height() // 2 + offset
            window.move(x, y)
        self.windows.append(window)
        if show:
            window.show()
            window.raise_()
            window.activateWindow()
        return window

    def restore_all(self) -> None:
        for window in self.windows:
            window.show()
            window.raise_()
        if self.windows:
            self.windows[-1].activateWindow()

    def close_all(self) -> None:
        for window in list(self.windows):
            window.close()
        self.windows.clear()


_MANAGER = OjingjingWindowManager()


def open_ojingjing_window(config: dict | None = None) -> OjingjingWindow:
    return _MANAGER.open_window(config=config)


def restore_ojingjing_windows() -> None:
    _MANAGER.restore_all()
