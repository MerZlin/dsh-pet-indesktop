# -*- coding: utf-8 -*-
"""Modern-inspired sidebar settings panel used by the modern context menu."""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QDialog,
    QColorDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import autostart as autostart_mod
from . import catalog
from .agent_link import AgentLinkManager
from .config import (
    DEFAULT_CONTEXT_MENU_APPEARANCE,
    DEFAULT_MENU_EASTER_EGG,
    DEFAULT_QUICK_LAUNCH_APPS,
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    _float_or_default,
)
from .context_menus.icons import vector_widget_icon
from .context_menus.quick_launch import fitted_application_icon
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset, store_fun_asset
from .speech_bubble import BUBBLE_STYLE_PRESETS
from .persona_phrases import phrase_keys


def _system_font_families() -> tuple[str, ...]:
    """缓存系统字体族列表。

    macOS 上 QFontDatabase.families() 走 CoreText 枚举，首次调用可达数百 ms，
    设置窗口每次打开都重建实例，同步枚举会明显拖慢打开速度。
    """
    if _system_font_families._cache is None:
        _system_font_families._cache = tuple(QFontDatabase.families())
    return _system_font_families._cache


_system_font_families._cache = None


BROWSER_CONTROL_SPEC = {
    "field_height": 32,
    "border": "#cfd4da",
    "border_hover": "#aeb6c0",
    "focus": "#0a84ff",
    "radius": 7,
    "scrollbar_width": 8,
}

BROWSER_CONTROL_STYLESHEET = """
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #ffffff;
    color: #202124;
    border: 1px solid #cfd4da;
    border-radius: 7px;
    padding: 4px 8px;
    selection-background-color: #0a84ff;
    selection-color: #ffffff;
}
QLineEdit, QSpinBox, QDoubleSpinBox { min-height: 20px; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {
    border-color: #aeb6c0;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    border: 2px solid #0a84ff;
    padding: 3px 7px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    width: 18px;
    border: none;
    border-left: 1px solid #e3e5e8;
    border-bottom: 1px solid #eceef0;
    border-top-right-radius: 6px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 18px;
    border: none;
    border-left: 1px solid #e3e5e8;
    border-bottom-right-radius: 6px;
}
QScrollBar:vertical {
    width: 8px;
    margin: 0;
    background: transparent;
}
QScrollBar::handle:vertical {
    min-height: 24px;
    margin: 1px;
    background: #c4c8cc;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #9fa5ab; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal {
    height: 8px;
    margin: 0;
    background: transparent;
}
QScrollBar::handle:horizontal {
    min-width: 24px;
    margin: 1px;
    background: #c4c8cc;
    border-radius: 4px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


class ToggleSwitch(QAbstractButton):
    """Small native-looking toggle used by settings cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = "#0a84ff" if self.isChecked() else ("#3a3a42" if _system_dark() else "#dedede")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(track))
        painter.drawRoundedRect(QRectF(0, 1, 38, 20), 10, 10)
        knob_x = 19.0 if self.isChecked() else 2.0
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#c9c9c9"), 0.5))
        painter.drawEllipse(QRectF(knob_x, 2, 18, 18))


IMAGE_NAME_FILTER = "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)"
AUDIO_NAME_FILTER = "音频文件 (*.wav *.mp3 *.ogg *.flac *.m4a)"


class ResourcePathPicker(QWidget):
    """Absolute-path field with a native file or directory chooser."""

    def __init__(self, value: str, *, directory: bool = False, name_filter: str = IMAGE_NAME_FILTER, parent=None):
        super().__init__(parent)
        self.directory = bool(directory)
        self.name_filter = name_filter
        self.edit = QLineEdit(self)
        self.edit.setMinimumWidth(250)
        self.edit.setText(str(value))
        self.button = QPushButton("选择…", self)
        self.button.setFixedWidth(66)
        self.button.clicked.connect(self.choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(str(value))

    def choose(self) -> None:
        current = self.text()
        start = current if current else str(Path.home())
        if self.directory:
            selected = QFileDialog.getExistingDirectory(self, "选择图片目录", start)
        else:
            selected, _ = QFileDialog.getOpenFileName(self, "选择图片", start, self.name_filter)
        if selected:
            self.setText(str(Path(selected).expanduser().resolve()))


class ColorSwatchButton(QAbstractButton):
    """Compact painted color well that does not depend on native button CSS."""

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        self._color = QColor(value)
        self.setFixedSize(36, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("选择颜色")

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, value) -> None:  # noqa: N802
        color = QColor(value)
        self._color = color if color.isValid() else QColor("#ffffff")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#aeb3b8"), 1.0))
        painter.setBrush(self._color)
        painter.drawRoundedRect(QRectF(3.5, 3.5, self.width() - 7.0, self.height() - 7.0), 6, 6)


class ColorPicker(QWidget):
    """Editable #RRGGBB field paired with the native color panel."""

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        self.edit = QLineEdit(str(value), self)
        self.edit.setFixedWidth(96)
        self.button = ColorSwatchButton(value, self)
        self.button.clicked.connect(self.choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.edit)
        layout.addWidget(self.button)
        self.edit.textChanged.connect(self._sync_swatch)
        self._sync_swatch(self.edit.text())

    def text(self) -> str:
        return self.edit.text().strip()

    def choose(self) -> None:
        initial = QColor(self.text())
        color = QColorDialog.getColor(initial if initial.isValid() else QColor("#ffffff"), self, "选择颜色")
        if color.isValid():
            self.edit.setText(color.name(QColor.NameFormat.HexRgb))

    def _sync_swatch(self, value: str) -> None:
        color = QColor(value)
        if color.isValid():
            self.button.setColor(color)


def _draw_chevron(widget, center_y: float, *, down: bool) -> None:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    color = QColor("#a8adb4" if _system_dark() else "#62676d") if widget.isEnabled() else QColor("#aeb2b7")
    painter.setPen(QPen(color, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    center_x = widget.width() - 10.0
    offset = 1.8 if down else -1.8
    painter.drawLine(QPointF(center_x - 2.6, center_y - offset), QPointF(center_x, center_y + offset))
    painter.drawLine(QPointF(center_x, center_y + offset), QPointF(center_x + 2.6, center_y - offset))


MODERN_SELECT_POPUP_STYLESHEET = """
QMenu#ModernSelectPopup {
    background: #ffffff;
    color: #202020;
    border: 1px solid #d8d8d8;
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
}
QMenu#ModernSelectPopup::item {
    min-height: 22px;
    padding: 4px 28px 4px 12px;
    border-radius: 7px;
}
QMenu#ModernSelectPopup::item:selected { background: #eeeeee; }
QMenu#ModernSelectPopup::indicator { width: 0; height: 0; }
"""


class ModernSelect(QAbstractButton):
    """Custom-painted selector with a Modern-style popover, not a QComboBox."""

    currentIndexChanged = Signal(int)
    aboutToShowPopup = Signal()

    def __init__(self, parent=None, *, width: int = 132):
        super().__init__(parent)
        self._items: list[tuple[str, object]] = []
        self._index = -1
        self._hovered = False
        self._popup: QMenu | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(BROWSER_CONTROL_SPEC["field_height"])
        self.setFixedWidth(width)
        self.clicked.connect(self.showPopup)

    def addItem(self, text: str, data=None) -> None:  # noqa: N802
        self._items.append((str(text), data))
        if self._index < 0:
            self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._index = -1
        self.setText("")
        self.update()

    def itemData(self, index: int):  # noqa: N802
        return self._items[index][1] if 0 <= index < len(self._items) else None

    def itemText(self, index: int) -> str:  # noqa: N802
        return self._items[index][0] if 0 <= index < len(self._items) else ""

    def setItemData(self, index: int, value, role=None) -> None:  # noqa: N802
        # Foreground roles are unnecessary because the custom popup owns its
        # palette; other calls update the stored data payload.
        if role is None and 0 <= index < len(self._items):
            text, _old = self._items[index]
            self._items[index] = (text, value)

    def findData(self, data) -> int:  # noqa: N802
        for index, (_, item_data) in enumerate(self._items):
            if item_data == data:
                return index
        return -1

    def setCurrentData(self, data) -> None:  # noqa: N802
        index = self.findData(data)
        if index >= 0:
            self.setCurrentIndex(index)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._items) or index == self._index:
            return
        self._index = index
        self.setText(self._items[index][0])
        self.currentIndexChanged.emit(index)
        self.update()

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def currentData(self):  # noqa: N802
        return self._items[self._index][1] if 0 <= self._index < len(self._items) else None

    def currentText(self) -> str:  # noqa: N802
        return self._items[self._index][0] if 0 <= self._index < len(self._items) else ""

    @staticmethod
    def popupStyleSheet() -> str:  # noqa: N802
        if _system_dark():
            return MODERN_SELECT_POPUP_STYLESHEET + _DARK_POPUP_OVERRIDE
        return MODERN_SELECT_POPUP_STYLESHEET

    def showPopup(self) -> None:  # noqa: N802
        self.aboutToShowPopup.emit()
        popup = self._popup
        if popup is None:
            popup = QMenu(self)
            popup.setObjectName("ModernSelectPopup")
            popup.setStyleSheet(self.popupStyleSheet())
            self._popup = popup
        else:
            # Reuse one native popup instead of retaining a new child QMenu on
            # every open. Deleting on close is unsafe here because Qt performs
            # that deletion asynchronously while Python still owns the wrapper.
            popup.clear()
        popup.setMinimumWidth(self.width())
        for index, (text, _) in enumerate(self._items):
            action = QWidgetAction(popup)
            option = ModernSelectOption(text, index == self._index, popup)
            option.clicked.connect(lambda checked=False, index=index: self.setCurrentIndex(index))
            option.clicked.connect(popup.close)
            action.setDefaultWidget(option)
            popup.addAction(action)
        popup.popup(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark = _system_dark()
        bg, border_idle, fg = ("#2e2e35", "#4a4a54", "#e4e4e9") if dark else ("#ffffff", "#cfd4da", "#202124")
        hover_border = "#56565f" if dark else "#aeb6c0"
        border = "#0a84ff" if self.hasFocus() else (hover_border if self._hovered else border_idle)
        painter.setBrush(QColor(bg))
        painter.setPen(QPen(QColor(border), 1.5 if self.hasFocus() else 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0), 8, 8)
        painter.setPen(QColor(fg))
        painter.drawText(QRectF(10, 0, self.width() - 34, self.height()), Qt.AlignmentFlag.AlignVCenter, self.currentText())
        painter.end()
        _draw_chevron(self, self.height() / 2.0, down=True)


class ModernSelectOption(QAbstractButton):
    def __init__(self, text: str, selected: bool, parent=None):
        super().__init__(parent)
        self._text = text
        self._selected = selected
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setMinimumWidth(116)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark = _system_dark()
        hover_bg, fg, check = (
            ("#3a3a46", "#e4e4e9", "#a0a6b0") if dark else ("#eeeeee", "#202020", "#454545")
        )
        if self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(hover_bg))
            painter.drawRoundedRect(QRectF(2, 1, self.width() - 4, self.height() - 2), 7, 7)
        painter.setPen(QColor(fg))
        painter.drawText(QRectF(10, 0, self.width() - 36, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        if self._selected:
            pen = QPen(QColor(check), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            x = self.width() - 17.0
            painter.drawLine(QPointF(x - 4, 15), QPointF(x - 1, 18))
            painter.drawLine(QPointF(x - 1, 18), QPointF(x + 5, 10))


class BrowserSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(92)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        _draw_chevron(self, self.height() * 0.29, down=False)
        _draw_chevron(self, self.height() * 0.71, down=True)


class BrowserDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(92)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        _draw_chevron(self, self.height() * 0.29, down=False)
        _draw_chevron(self, self.height() * 0.71, down=True)


class SettingRow(QFrame):
    """A label and hint on the left, with one control aligned to the right."""

    def __init__(self, key: str, title: str, hint: str, control: QWidget, parent=None, *, stacked: bool = False):
        super().__init__(parent)
        self.setObjectName(f"settingRow_{key}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("stackedControl", stacked)
        label = QLabel(title, self)
        label.setObjectName("settingLabel")
        hint_label = QLabel(hint, self)
        hint_label.setObjectName("settingHint")
        hint_label.setWordWrap(True)
        if stacked:
            row = QVBoxLayout(self)
            row.setContentsMargins(14, 9, 14, 9)
            row.setSpacing(0)
            row.addWidget(label)
            row.addWidget(hint_label)
            row.addSpacing(7)
            row.addWidget(control)
        else:
            row = QHBoxLayout(self)
            row.setContentsMargins(14, 9, 14, 9)
            row.setSpacing(18)
            copy = QVBoxLayout()
            copy.setContentsMargins(0, 0, 0, 0)
            copy.setSpacing(2)
            copy.addWidget(label)
            copy.addWidget(hint_label)
            copy.addStretch(1)
            row.addLayout(copy, 1)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label = label
        self.hint_label = hint_label
        self.control = control


class SettingsCard(QFrame):
    def __init__(self, rows: list[SettingRow], parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.rows = list(rows)
        self.separators: list[QFrame] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for index, row in enumerate(rows):
            if index:
                separator = QFrame(self)
                separator.setObjectName("cardSeparator")
                separator.setFixedHeight(1)
                self.separators.append(separator)
                layout.addWidget(separator)
            layout.addWidget(row)
        self.refresh_separators()

    def refresh_separators(self) -> None:
        """Keep dividers attached to visible rows during progressive disclosure."""
        visible_before = False
        for index, row in enumerate(self.rows):
            if index:
                self.separators[index - 1].setVisible(not row.isHidden() and visible_before)
            visible_before = visible_before or not row.isHidden()


class SettingsSection(QWidget):
    def __init__(self, title: str, rows: list[SettingRow], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        label = QLabel(title, self)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        layout.addWidget(SettingsCard(rows, self))


def _line_edit(text: str = "", *, password: bool = False, width: int = 240) -> QLineEdit:
    edit = QLineEdit(text)
    edit.setMinimumWidth(width)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class QuickLaunchEditor(QWidget):
    """Small application picker persisted into the modern menu."""

    def __init__(self, apps: list[dict], parent=None):
        super().__init__(parent)
        self.list = QListWidget(self)
        self.list.setObjectName("quickLaunchList")
        self.list.setMinimumHeight(116)
        self.list.setIconSize(QSize(22, 22))
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDragEnabled(True)
        self.list.setAcceptDrops(True)
        self.list.setDropIndicatorShown(True)
        self.add_button = QPushButton("添加应用", self)
        self.add_button.setIcon(vector_widget_icon(self, "add", 15))
        self.remove_button = QPushButton("移除勾选", self)
        self.remove_button.setIcon(vector_widget_icon(self, "remove", 15))
        self.default_button = QPushButton("添加默认浏览器", self)
        self.default_button.setIcon(vector_widget_icon(self, "web", 15))
        self.add_button.clicked.connect(self._choose_application)
        self.remove_button.clicked.connect(self._remove_checked)
        self.default_button.clicked.connect(self._add_default_browser)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(7)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.default_button)
        buttons.addStretch(1)
        buttons.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        for item in apps:
            self.add_app(item)

    def add_app(self, app: dict) -> None:
        app = dict(app)
        if app.get("kind") == "default_browser":
            icon = vector_widget_icon(self, "web", 22)
            app = {"name": str(app.get("name") or "默认浏览器"), "path": "", "kind": "default_browser"}
        else:
            path = str(app.get("path") or "")
            if not path:
                return
            provider_icon = QFileIconProvider().icon(QFileInfo(path))
            if provider_icon.isNull():
                icon = vector_widget_icon(self, "application", 17)
            else:
                icon = fitted_application_icon(provider_icon, 22, self)
            app = {"name": str(app.get("name") or Path(path).stem), "path": path, "kind": "application"}
        item = QListWidgetItem(icon, app["name"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, app)
        item.setToolTip(app["path"] or "使用系统默认浏览器")
        self.list.addItem(item)

    def apps(self) -> list[dict]:
        return [dict(self.list.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(self.list.count())]

    def _choose_application(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择需要快捷启动的应用",
            "/Applications" if os.path.isdir("/Applications") else "",
            "应用程序 (*.app);;所有文件 (*)",
        )
        if path:
            self.add_app({"name": Path(path).stem, "path": path, "kind": "application"})

    def _remove_checked(self) -> None:
        for index in range(self.list.count() - 1, -1, -1):
            if self.list.item(index).checkState() == Qt.CheckState.Checked:
                self.list.takeItem(index)

    def _add_default_browser(self) -> None:
        if not any(item.get("kind") == "default_browser" for item in self.apps()):
            self.add_app(DEFAULT_QUICK_LAUNCH_APPS[0])


class _AiSettingsPage(QWidget):
    test_done = Signal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        # No-chat bundles exclude pet.chat and never construct this optional page.
        from .chat.models import ProviderConfig, SecretStore
        from .chat.providers import test_connection

        self.config = config
        self._provider_config_type = ProviderConfig
        self._secret_store_type = SecretStore
        self._test_connection = test_connection
        self.settings = config.chat_settings()
        provider = self.settings.active_config
        self._test_thread = None
        self.test_done.connect(self._on_test_done)

        self.name = _line_edit(provider.name)
        self.url = _line_edit(provider.base_url)
        self.model = _line_edit(provider.model)
        self.key = _line_edit(password=True)
        self.prompt = QPlainTextEdit(self.settings.default_system_prompt)
        self.prompt.setMinimumSize(240, 80)
        self.timeout = BrowserSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(provider.timeout))
        self.temperature = BrowserDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(provider.temperature)
        self.tokens = BrowserSpinBox()
        self.tokens.setRange(1, 32768)
        self.tokens.setValue(provider.max_tokens)
        self.skip_ssl = ToggleSwitch()
        self.skip_ssl.setChecked(not provider.verify_ssl)
        self.chat_ui_style = ModernSelect(self, width=190)
        self.chat_ui_style.addItem("肥鱼版 DeepSeek", "modern")
        self.chat_ui_style.addItem("肥鱼牌小手机", "classic")
        self.chat_ui_style.setCurrentData(str(config.get("chat_ui_style", "modern")))
        self.vision_same = ToggleSwitch()
        self.vision_same.setChecked(bool(provider.vision_same_as_chat))
        self.vision_model = _line_edit(provider.vision_model)
        self.vision_url = _line_edit(provider.vision_base_url)
        self.vision_key = _line_edit(password=True)

        from .chat.themes import theme_names
        self._background_themes = list(theme_names())
        self._background_values = {
            "classic": str(config.get("chat_background", "") or ""),
            "modern": str(config.get("modern_chat_background", "") or ""),
        }
        self._background_display = {
            "classic": {
                "opacity": int(config.get("chat_background_opacity", 100) or 100),
                "fill": str(config.get("chat_background_fill", "cover") or "cover"),
            },
            "modern": {
                "opacity": int(config.get("modern_chat_background_opacity", 100) or 100),
                "fill": str(config.get("modern_chat_background_fill", "cover") or "cover"),
            },
        }
        self._background_style = str(self.chat_ui_style.currentData() or "modern")
        self.background_select = ModernSelect(self, width=180)
        self.background_picker = ResourcePathPicker(
            "",
            parent=self,
        )
        self.background_opacity = BrowserSpinBox(self)
        self.background_opacity.setRange(10, 100)
        self.background_opacity.setSuffix(" %")
        self.message_card_opacity = BrowserSpinBox(self)
        self.message_card_opacity.setRange(10, 100)
        self.message_card_opacity.setSuffix(" %")
        self.message_card_opacity.setValue(
            int(config.get("modern_chat_card_opacity", 84) or 84)
        )
        self.background_fill = ModernSelect(self, width=160)
        self.background_fill.addItem("填充裁剪", "cover")
        self.background_fill.addItem("完整适应", "contain")
        self.background_fill.addItem("拉伸铺满", "stretch")
        self._populate_background_options(self._background_style)
        self.chat_ui_style.currentIndexChanged.connect(self._on_chat_ui_style_changed)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._run_test)
        self.test_result = QLabel("验证当前 Provider、API 地址和凭据是否可用。")
        self.test_result.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(SettingsSection("模型与连接", [
            SettingRow("provider_name", "Provider 名称", "用于区分当前使用的模型服务。", self.name),
            SettingRow("api_url", "API 地址", "OpenAI Chat Completions 兼容服务地址。", self.url),
            SettingRow("model", "模型", "发送请求时使用的模型标识。", self.model),
            SettingRow("api_key", "API Key", "凭据优先保存到系统钥匙串。", self.key),
            SettingRow("system_prompt", "System Prompt", "定义桌宠对话时的身份、语气和行为。", self.prompt, stacked=True),
            SettingRow("connection_test", "连接测试", self.test_result.text(), self.test_button),
        ], self))
        vision_rows = [
            SettingRow("vision_same", "视觉模型复用聊天模型", "开启后自动选择兼容的视觉模型，用于“看看屏幕”。", self.vision_same),
            SettingRow("vision_model", "视觉模型", "关闭复用后使用的多模态模型标识；留空则自动推导。", self.vision_model),
            SettingRow("vision_url", "视觉 API 地址", "留空复用聊天服务地址。", self.vision_url),
            SettingRow("vision_key", "视觉 API Key", "留空复用聊天服务凭据。", self.vision_key),
        ]
        root.addWidget(SettingsSection("视觉能力", vision_rows, self))
        self._vision_override_rows = vision_rows[1:]
        self.vision_same.toggled.connect(self._update_vision_visibility)
        self._update_vision_visibility(self.vision_same.isChecked())
        self._test_row = self.findChild(SettingRow, "settingRow_connection_test")
        if self._test_row is not None:
            self.test_result = self._test_row.hint_label
        root.addWidget(SettingsSection("生成参数", [
            SettingRow("timeout", "请求超时", "等待模型服务响应的最长时间。", self.timeout),
            SettingRow("temperature", "Temperature", "数值越高，回答越随机。", self.temperature),
            SettingRow("max_tokens", "最大输出 Token", "限制模型单次回复的最大长度。", self.tokens),
            SettingRow("skip_ssl", "跳过 SSL 证书验证", "仅用于本地网关或自签名证书。", self.skip_ssl),
        ], self))
        root.addStretch(1)

    def appearance_rows(self) -> list[SettingRow]:
        """Controls visually owned by the Appearance page, persisted with AI settings."""
        rows = [
            SettingRow(
                "chat_ui_style", "对话窗口",
                "肥鱼版 DeepSeek 提供宽屏现代体验；肥鱼牌小手机保留紧凑经典体验。",
                self.chat_ui_style,
            ),
            SettingRow(
                "chat_background", "对话背景",
                "肥鱼版 DeepSeek 与肥鱼牌小手机均支持纯色、内置主题或自定义图片。",
                self.background_select,
            ),
            SettingRow("chat_background_file", "自定义背景图片", "支持常见图片格式，使用绝对路径。", self.background_picker),
            SettingRow("chat_background_opacity", "图片不透明度", "调节背景图可见强度；消息卡片会独立保证正文可读。", self.background_opacity),
            SettingRow("chat_background_fill", "填充方式", "选择裁剪铺满、完整显示或拉伸铺满窗口。", self.background_fill),
            SettingRow(
                "modern_chat_card_opacity", "消息卡片不透明度",
                "调节肥鱼版 DeepSeek 消息卡片透出背景的程度。",
                self.message_card_opacity,
            ),
        ]
        self._background_file_row = rows[-4]
        self._background_detail_rows = rows[-3:-1]
        self._message_card_opacity_row = rows[-1]
        self.background_select.currentIndexChanged.connect(self._update_background_visibility)
        self._update_background_visibility()
        return rows

    def _current_background_value(self) -> str:
        value = self.background_select.currentData()
        return self.background_picker.text() if value == "custom" else str(value or "")

    def _capture_background_value(self) -> None:
        self._background_values[self._background_style] = self._current_background_value()
        self._background_display[self._background_style] = {
            "opacity": self.background_opacity.value(),
            "fill": str(self.background_fill.currentData() or "cover"),
        }

    def _populate_background_options(self, style: str) -> None:
        self.background_select.clear()
        self.background_select.addItem("纯色背景", "")
        # 内置主题两种对话窗口风格都可用（肥鱼版 DeepSeek 与肥鱼牌小手机一致）
        for key, label in self._background_themes:
            self.background_select.addItem(label, f"builtin:{key}")
        self.background_select.addItem("自定义图片", "custom")
        value = str(self._background_values.get(style, "") or "")
        if value.startswith("builtin:") and self.background_select.findData(value) >= 0:
            self.background_select.setCurrentData(value)
            self.background_picker.setText("")
        elif value:
            self.background_select.setCurrentData("custom")
            self.background_picker.setText(value)
        else:
            self.background_select.setCurrentData("")
            self.background_picker.setText("")
        display = self._background_display.get(style, {})
        self.background_opacity.setValue(int(display.get("opacity", 100)))
        self.background_fill.setCurrentData(str(display.get("fill", "cover")))
        self._update_background_visibility()

    def _on_chat_ui_style_changed(self, _index: int = -1) -> None:
        self._capture_background_value()
        self._background_style = str(self.chat_ui_style.currentData() or "modern")
        self._populate_background_options(self._background_style)

    def _update_vision_visibility(self, reuse_chat_model: bool) -> None:
        for row in self._vision_override_rows:
            row.setVisible(not reuse_chat_model)
        card = self._vision_override_rows[0].parentWidget() if self._vision_override_rows else None
        if isinstance(card, SettingsCard):
            card.refresh_separators()

    def _update_background_visibility(self, _index: int = -1) -> None:
        row = getattr(self, "_background_file_row", None)
        if row is not None:
            row.setVisible(self.background_select.currentData() == "custom")
            has_image = bool(self.background_select.currentData())
            for detail_row in getattr(self, "_background_detail_rows", []):
                detail_row.setVisible(has_image)
            card = row.parentWidget()
            if isinstance(card, SettingsCard):
                card.refresh_separators()
        card_opacity_row = getattr(self, "_message_card_opacity_row", None)
        if card_opacity_row is not None:
            card_opacity_row.setVisible(self._background_style == "modern")

    def provisional_config(self):
        provider = self.settings.active_config
        return self._provider_config_type(
            provider.provider_id,
            self.name.text().strip() or provider.name,
            self.url.text().strip(),
            provider.chat_path,
            self.model.text().strip(),
            provider.api_key_ref,
            # 表单未填时回退钥匙串：凭据默认存系统钥匙串，直接读 api_key 为空
            self.key.text() or provider.api_key or self._secret_store_type().get(provider.api_key_ref),
            float(self.timeout.value()),
            float(self.temperature.value()),
            int(self.tokens.value()),
            verify_ssl=not self.skip_ssl.isChecked(),
        )

    def _run_test(self) -> None:
        if self._test_thread is not None and self._test_thread.is_alive():
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.test_result.setText("正在连接模型服务…")
        self._test_thread = threading.Thread(
            target=self._run_test_worker,
            args=(self.provisional_config(),),
            daemon=True,
            name="pet-modern-settings-connection-test",
        )
        self._test_thread.start()

    def _run_test_worker(self, provider) -> None:
        self.test_done.emit(*self._test_connection(provider, timeout=10.0))

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        self.test_result.setText(message)
        self.test_result.setStyleSheet("color: #16843b;" if ok else "color: #c9362b;")
        self._test_thread = None

    def save(self) -> None:
        # 保存前基于磁盘最新配置重取快照：另一个设置窗口（AI 设置/桌宠设置 AI 页）
        # 可能在本窗口打开期间改过 Provider 结构；UI 字段随后覆盖到新鲜快照上，
        # 未暴露/结构性的改动（新增 provider、切换 active_provider）不被旧快照吞掉。
        self.settings = self.config.chat_settings()
        provider = self.settings.active_config
        provider.name = self.name.text().strip() or provider.name
        provider.base_url = self.url.text().strip()
        provider.model = self.model.text().strip()
        provider.timeout = float(self.timeout.value())
        provider.temperature = float(self.temperature.value())
        provider.max_tokens = int(self.tokens.value())
        provider.verify_ssl = not self.skip_ssl.isChecked()
        provider.vision_same_as_chat = self.vision_same.isChecked()
        provider.vision_model = self.vision_model.text().strip()
        provider.vision_base_url = self.vision_url.text().strip()
        vision_key = self.vision_key.text()
        if vision_key:
            provider.vision_api_key_ref = provider.vision_api_key_ref or f"provider/{provider.provider_id}/vision"
            if not self._secret_store_type().set(provider.vision_api_key_ref, vision_key):
                provider.vision_api_key = vision_key
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")
        key = self.key.text()
        if key:
            provider.api_key_ref = provider.api_key_ref or f"provider/{provider.provider_id}"
            if not self._secret_store_type().set(provider.api_key_ref, key):
                provider.api_key = key
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")
        self.settings.default_system_prompt = self.prompt.toPlainText().strip()
        self.config.set("chat_ui_style", self.chat_ui_style.currentData())
        self._capture_background_value()
        self.config.set("chat_background", self._background_values["classic"])
        self.config.set("modern_chat_background", self._background_values["modern"])
        self.config.set("chat_background_opacity", self._background_display["classic"]["opacity"])
        self.config.set("chat_background_fill", self._background_display["classic"]["fill"])
        self.config.set("modern_chat_background_opacity", self._background_display["modern"]["opacity"])
        self.config.set("modern_chat_background_fill", self._background_display["modern"]["fill"])
        self.config.set("modern_chat_card_opacity", self.message_card_opacity.value())
        self.config.set_chat_settings(self.settings)


class ModernSettingsDialog(QDialog):
    """Settings window matching Modern's sidebar and rounded-card hierarchy."""

    settings_saved = Signal()

    def __init__(self, config, parent=None, *, include_ai: bool = True):
        super().__init__(parent)
        self.config = config
        self.include_ai = bool(include_ai)
        self.ai_page = None
        self.setProperty("modernStyle", True)
        self.setProperty("menuStyle", "modern")
        self.setWindowTitle("桌宠设置")
        self.resize(800, 560)
        self.setMinimumSize(720, 500)
        self._positioned_away = False
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(13)
        self.setFont(font)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar_pane = QFrame(self)
        sidebar_pane.setObjectName("sidebarPane")
        sidebar_pane.setFixedWidth(188)
        sidebar_layout = QVBoxLayout(sidebar_pane)
        sidebar_layout.setContentsMargins(12, 16, 12, 12)
        sidebar_layout.setSpacing(9)
        self.save_exit_button = QPushButton("保存并退出", sidebar_pane)
        self.save_exit_button.setObjectName("saveAndExit")
        self.save_exit_button.setIcon(vector_widget_icon(self.save_exit_button, "back", 16))
        self.save_exit_button.clicked.connect(self._save)
        self.save_exit_button.setAutoDefault(False)
        self.save_exit_button.setDefault(False)
        sidebar_layout.addWidget(self.save_exit_button)
        self.search_edit = QLineEdit(sidebar_pane)
        self.search_edit.setObjectName("settingsSearch")
        self.search_edit.setPlaceholderText("搜索设置…")
        self.search_edit.addAction(
            vector_widget_icon(self, "search", 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.installEventFilter(self)
        sidebar_layout.addWidget(self.search_edit)
        self.search_status = QLabel("", sidebar_pane)
        self.search_status.setObjectName("searchStatus")
        self.search_status.setWordWrap(True)
        self.search_status.hide()
        sidebar_layout.addWidget(self.search_status)
        self.sidebar = QListWidget(sidebar_pane)
        self.sidebar.setObjectName("settingsSidebar")
        self.sidebar.setSpacing(2)
        sidebar_layout.addWidget(self.sidebar, 1)

        self.pages = QStackedWidget(self)
        body.addWidget(sidebar_pane)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        self._build_pet_controls()
        if include_ai:
            self.ai_page = _AiSettingsPage(config, self)

        general_content = QWidget()
        general_layout = QVBoxLayout(general_content)
        general_layout.setContentsMargins(0, 0, 0, 0)
        general_layout.setSpacing(18)
        launch_rows = [
            SettingRow("autostart", "开机自启", "登录系统后自动启动桌宠。", self.autostart_check),
        ]
        if sys.platform == "darwin":
            launch_rows.append(SettingRow(
                "dock_icon", "显示 Dock 图标", "在 macOS Dock 中显示桌宠应用；关闭后仍可通过桌宠和托盘操作。",
                self.dock_icon_check,
            ))
        general_layout.addWidget(SettingsSection("应用启动", launch_rows, general_content))
        window_rows = [
            SettingRow("on_top", "窗口置顶", "始终将桌宠保持在其他窗口上方。", self.on_top_check),
        ]
        if sys.platform == "win32":
            window_rows.extend([
                SettingRow("auto_hide_fullscreen", "全屏时自动隐藏", "全屏游戏或视频期间自动隐藏桌宠。", self.auto_hide_fullscreen_check),
                SettingRow("stream_capture", "直播捕获兼容", "让 OBS 等工具能够枚举并捕获桌宠窗口。", self.stream_capture_check),
            ])
        general_layout.addWidget(SettingsSection("窗口与系统", window_rows, general_content))
        if self.balance_refresh_spin is not None:
            general_layout.addWidget(SettingsSection("后台服务", [
                SettingRow("balance_refresh", "余额自动刷新", "设置后台刷新间隔；0 分钟表示关闭。", self.balance_refresh_spin),
                SettingRow("balance_tier_mode", "峰谷提示文案", "选择 DeepSeek 高峰/空闲提示的显示风格。", self.balance_tier_mode_select),
                SettingRow("balance_tier_peak", "高峰自定义文本", "仅“自定义”模式生效；留空回退默认“高峰”。", self.balance_tier_peak_edit, stacked=True),
                SettingRow("balance_tier_idle", "空闲自定义文本", "仅“自定义”模式生效；留空回退默认“空闲”。", self.balance_tier_idle_edit, stacked=True),
                SettingRow("balance_tier_color", "峰谷提示颜色", "开启后高峰显示红色、低谷显示绿色；关闭则使用普通气泡文字颜色。", self.balance_tier_color_check),
            ], general_content))
        general_layout.addStretch(1)
        self._add_page("常规", "settings", self._page_shell("常规", general_content))

        behavior_content = QWidget()
        behavior_layout = QVBoxLayout(behavior_content)
        behavior_layout.setContentsMargins(0, 0, 0, 0)
        behavior_layout.setSpacing(16)
        behavior_layout.addWidget(SettingsSection("动画", [
            SettingRow("playback_speed", "播放速率", "控制所有桌宠动画的播放速度。", self.speed_select),
            SettingRow("animation_gap", "动作等待间隔", "非待机动作之间的休息时间；0 秒表示连续播放。", self.gap_spin),
            SettingRow("no_move", "不移动", "暂停桌宠在桌面上的自动移动。", self.no_move_check),
            SettingRow("mouse_through", "鼠标穿透", "开启后桌宠不接收鼠标事件，点击穿透到下层窗口。", self.mouse_through_check),
            SettingRow("music_sing", "音乐自动唱歌", "检测到后台播放音乐时，自动播放唱歌动画。", self.music_sing_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("拖拽", [
            SettingRow("drag_physics", "拖动物理", "启用拖拽惯性、重力和边缘反弹。", self.drag_physics_check),
            SettingRow("lock_position", "锁定位置", "桌宠固定不动，无法拖动（点击互动仍有效）。", self.lock_position_check),
            SettingRow("shift_drag", "SHIFT+左键拖动", "开启后必须按住 SHIFT 再左键才能拖动桌宠。", self.shift_drag_check),
        ], behavior_content))
        click_rows = [
            SettingRow("click_sound", "点击音效", "点击桌宠时播放轻量反馈音效。", self.click_sound_check),
            SettingRow("click_sound_path", "音效文件", "使用绝对路径；留空时使用内置默认音效。", self.click_sound_picker, stacked=True),
            SettingRow("click_self_talk", "点击触发自言自语", "点击时随机显示一条自言自语内容。", self.click_self_talk_check),
        ]
        if self.click_balance_check is not None:
            click_rows.insert(2, SettingRow(
                "click_balance", "点击显示余额", "点击桌宠时查询并用气泡展示模型服务余额。",
                self.click_balance_check,
            ))
        behavior_layout.addWidget(SettingsSection("点击反馈", click_rows, behavior_content))
        behavior_layout.addWidget(SettingsSection("自言自语", [
            SettingRow("self_talk", "气泡自言自语", "让桌宠偶尔显示一条随机思考气泡。", self.self_talk_check),
            SettingRow("self_talk_duration", "显示时间", "每条文字或图片气泡保持显示的时间。", self.self_talk_duration_spin),
            SettingRow("self_talk_min", "最短间隔", "上一条气泡消失后，到下一条出现前的最短空闲时间。", self.min_spin),
            SettingRow("self_talk_max", "最长间隔", "上一条气泡消失后，到下一条出现前的最长空闲时间。", self.max_spin),
            SettingRow("self_talk_texts", "候选内容", "每行一条；留空时恢复内置文本。", self.texts_edit, stacked=True),
            SettingRow("self_talk_images", "图片目录", "从目录中的常见图片格式随机选择；默认使用内置彩蛋图片池，留空时只显示文本。", self.self_talk_image_dir_picker, stacked=True),
        ], behavior_content))
        # Agent 联动：每个 Agent 一行自定义思考文案
        agent_thinking_rows = []
        for agent_key, edit in self.thinking_text_edits.items():
            agent_name = AgentLinkManager.AGENT_NAMES.get(agent_key, agent_key)
            default = AgentLinkManager._THINKING_DEFAULTS.get(agent_key, f"{agent_name} 正在深度烧烤……")
            agent_thinking_rows.append(
                SettingRow(f"agent_thinking_{agent_key}", f"{agent_name} 思考文案",
                           f"默认：{default}；支持 {{name}} 占位符；留空用默认。",
                           edit, stacked=True)
            )
        behavior_layout.addWidget(SettingsSection("Agent 联动 · 思考气泡文案", agent_thinking_rows, behavior_content))
        behavior_layout.addStretch(1)
        self._add_page("桌宠行为", "play", self._page_shell("桌宠行为", behavior_content))

        dialogue_content = QWidget()
        dialogue_layout = QVBoxLayout(dialogue_content)
        dialogue_layout.setContentsMargins(0, 0, 0, 0)
        dialogue_layout.setSpacing(16)
        dialogue_layout.addWidget(SettingsSection("现有事件台词", [
            SettingRow("dialogue_mode", "台词模式", "选择原有、鲸鱼娘女仆或逐句自定义模式。", self.dialogue_mode_select),
        ], dialogue_content))
        labels = {
            "start": "开始工作", "thinking": "思考", "activity.read": "读取", "activity.search": "搜索",
            "activity.edit": "编辑", "activity.run": "运行/测试", "activity.default": "其他工具",
            "approval.command": "审批命令", "approval.tool": "审批工具", "approval.generic": "审批提示",
            "question.empty": "无选项问题", "question.one": "用户问题", "question.many": "多个问题",
            "watchdog.warning": "循环警告", "watchdog.intervention": "循环干预", "watchdog.unknown": "Judge 不可用",
            "rate_limit.one": "限流", "rate_limit.many": "连续限流", "done.success": "任务完成",
            "done.attention": "任务暂停", "failure.retry": "重试失败", "failure.tool": "工具失败", "failure.generic": "执行失败",
        }
        dialogue_rows = [
            SettingRow(f"dialogue_{key}", labels.get(key, key), "留空则使用基础模式台词。", edit, stacked=True)
            for key, edit in self.dialogue_phrase_edits.items()
        ]
        dialogue_layout.addWidget(SettingsSection("逐句自定义（自定义模式）", dialogue_rows, dialogue_content))
        dialogue_layout.addStretch(1)
        self._add_page("台词风格", "chat", self._page_shell("台词风格", dialogue_content))

        appearance_content = QWidget()
        appearance_layout = QVBoxLayout(appearance_content)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(16)
        appearance_layout.addWidget(SettingsSection("桌宠显示", [
            SettingRow("scale", "桌宠大小", "调整桌宠在桌面上的显示尺寸。", self.scale_combo),
            SettingRow("pet_opacity", "不透明度", "调整桌宠窗口的整体透明度；100% 为完全不透明。", self.pet_opacity_spin),
            SettingRow(
                "self_talk_bubble_style", "气泡方案",
                "选择气泡视觉与相对桌宠的位置；贴近屏幕边缘时自动换位。",
                self.bubble_style_select,
            ),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("菜单外观", [
            SettingRow("menu_theme", "颜色主题", "可跟随系统，或固定使用浅色/深色菜单。", self.menu_theme_select),
            SettingRow("menu_density", "菜单密度", "调整新版右键菜单的菜单项高度和分组留白。", self.menu_density_select),
            SettingRow("menu_radius", "圆角大小", "调整新版右键菜单和子菜单的外轮廓圆角。", self.menu_radius_select),
            SettingRow("menu_font", "UI 字体", "设置新版菜单使用的界面字体。", self.menu_font_select),
            SettingRow("menu_font_size", "UI 字号", "同步调整主菜单与多级菜单的字号。", self.menu_font_size_select),
            SettingRow("menu_translucent", "半透明菜单", "使用接近 Modern 的半透明浮层表面。", self.menu_translucent_check),
            SettingRow("menu_opacity", "表面不透明度", "调整菜单背景透出桌面内容的程度。", self.menu_opacity_spin),
        ], appearance_content))
        if self.ai_page is not None:
            appearance_layout.addWidget(SettingsSection("AI 对话外观", self.ai_page.appearance_rows(), appearance_content))
        appearance_layout.addWidget(SettingsSection("浅色主题", [
            SettingRow("light_background", "背景色", "浅色菜单的浮层背景。", self.light_background_picker),
            SettingRow("light_foreground", "文字色", "浅色菜单的主要文字与图标颜色。", self.light_foreground_picker),
            SettingRow("light_hover", "悬停色", "鼠标悬停菜单项时的背景。", self.light_hover_picker),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("深色主题", [
            SettingRow("dark_background", "背景色", "深色菜单的浮层背景。", self.dark_background_picker),
            SettingRow("dark_foreground", "文字色", "深色菜单的主要文字与图标颜色。", self.dark_foreground_picker),
            SettingRow("dark_hover", "悬停色", "鼠标悬停菜单项时的背景。", self.dark_hover_picker),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("彩蛋入口", [
            SettingRow("egg_enabled", "显示彩蛋", "控制新版菜单首行彩蛋入口是否显示。", self.egg_enabled_check),
            SettingRow("egg_title", "入口标题", "显示在圆形头像右侧的文字。", self.egg_title_edit),
            SettingRow("egg_hint", "右侧提示", "显示在鼠标指针图标后的短提示。", self.egg_hint_edit),
            SettingRow("egg_avatar", "头像图片", "使用绝对路径；支持常见图片格式。", self.egg_avatar_picker),
            SettingRow("egg_image_dir", "弹窗图片目录", "使用绝对路径；每次点击会随机选择一张图片。", self.egg_image_dir_picker),
        ], appearance_content))
        appearance_layout.addStretch(1)
        self._add_page("外观", "appearance", self._page_shell("外观", appearance_content))

        launcher_content = QWidget()
        launcher_layout = QVBoxLayout(launcher_content)
        launcher_layout.setContentsMargins(0, 0, 0, 0)
        launcher_layout.setSpacing(18)
        self.quick_launch_editor = QuickLaunchEditor(
            self.config.get("quick_launch_apps", DEFAULT_QUICK_LAUNCH_APPS),
            launcher_content,
        )
        launcher_layout.addWidget(SettingsSection("已配置应用", [
            SettingRow(
                "quick_launch_apps",
                "应用快捷启动",
                "这些应用将按图标和名称显示在新版右键菜单的“快捷启动”子菜单中。",
                self.quick_launch_editor,
                stacked=True,
            ),
        ], launcher_content))
        launcher_layout.addStretch(1)
        self._add_page("快捷启动", "application", self._page_shell("快捷启动", launcher_content))

        if sys.platform == "win32" and self.include_ai:
            self._add_page("主动识屏", "screen", self._page_shell("主动识屏", self._proactive_page_content()))

        if self.ai_page is not None:
            self._add_page("AI 设置", "chat", self._page_shell("AI 设置", self.ai_page))

        # Agent Exploration Loop Watchdog 独立设置页
        from .exploration_watchdog_settings import WatchdogSettingsPage
        agent_link_cfg = self.config.get("agent_link", {})
        self.watchdog_page = WatchdogSettingsPage(self.config, agent_link_cfg, self)
        self._add_page("循环检测", "watchdog", self._page_shell("循环检测", self.watchdog_page))

        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self._search_rows = self.findChildren(SettingRow)
        self._search_matches: list[SettingRow] = []
        self._search_index = -1
        self.search_edit.textChanged.connect(self._search_settings)

        self.self_talk_check.toggled.connect(self._update_self_talk_controls)
        self.menu_translucent_check.toggled.connect(self._update_translucency_controls)
        self._update_self_talk_controls(self.self_talk_check.isChecked())
        self._update_translucency_controls(self.menu_translucent_check.isChecked())
        # 初始同步须在全部 SettingRow 构建完成后执行，否则 findChild 找不到行
        self._update_click_sound_controls(self.click_sound_check.isChecked())

        self.setStyleSheet(self._stylesheet())

    def _build_pet_controls(self) -> None:
        self.scale_combo = ModernSelect(self, width=132)
        current_scale = float(self.config.get("scale", catalog.DEFAULT_SCALE))
        scales = list(catalog.SCALE_STEPS)
        if not any(abs(current_scale - value) < 0.001 for value in scales):
            scales.append(current_scale)
            scales.sort()
        for scale in scales:
            self.scale_combo.addItem(f"{int(round(catalog.CANVAS_W * scale))} px", scale)
        self.scale_combo.setCurrentIndex(self.scale_combo.findData(current_scale))

        self.on_top_check = ToggleSwitch(self)
        self.on_top_check.setChecked(bool(self.config.get("on_top", True)))
        self.no_move_check = ToggleSwitch(self)
        self.no_move_check.setChecked(bool(self.config.get("no_move", False)))
        self.mouse_through_check = ToggleSwitch(self)
        self.mouse_through_check.setChecked(bool(self.config.get("mouse_through", False)))
        self.drag_physics_check = ToggleSwitch(self)
        self.drag_physics_check.setChecked(bool(self.config.get("drag_physics", False)))
        self.lock_position_check = ToggleSwitch(self)
        self.lock_position_check.setChecked(bool(self.config.get("lock_position", False)))
        self.shift_drag_check = ToggleSwitch(self)
        self.shift_drag_check.setChecked(bool(self.config.get("shift_drag", False)))
        self.pet_opacity_spin = BrowserSpinBox(self)
        self.pet_opacity_spin.setRange(10, 100)
        self.pet_opacity_spin.setSuffix(" %")
        self.pet_opacity_spin.setValue(int(_float_or_default(self.config.get("pet_opacity", 100), 100, 10, 100)))
        self.autostart_check = ToggleSwitch(self)
        self._autostart_initial = autostart_mod.is_enabled()
        self.autostart_check.setChecked(self._autostart_initial)
        self.dock_icon_check = None
        if sys.platform == "darwin":
            self.dock_icon_check = ToggleSwitch(self)
            self.dock_icon_check.setChecked(bool(self.config.get("show_dock_icon", True)))
        self.click_sound_check = ToggleSwitch(self)
        self.click_sound_check.setChecked(bool(self.config.get("click_sound_enabled", True)))
        self.click_sound_picker = ResourcePathPicker(
            str(self.config.get("click_sound_path", "") or ""),
            name_filter=AUDIO_NAME_FILTER,
            parent=self,
        )
        self.click_sound_check.toggled.connect(self._update_click_sound_controls)
        self.click_balance_check = None
        if self.include_ai:
            self.click_balance_check = ToggleSwitch(self)
            self.click_balance_check.setChecked(bool(self.config.get("click_show_balance", False)))
        self.click_self_talk_check = ToggleSwitch(self)
        self.click_self_talk_check.setChecked(bool(self.config.get("click_show_self_talk", False)))
        self.music_sing_check = ToggleSwitch(self)
        self.music_sing_check.setChecked(bool(self.config.get("music_sing_enabled", False)))
        self.balance_refresh_spin = None
        self.balance_tier_mode_select = None
        self.balance_tier_peak_edit = None
        self.balance_tier_idle_edit = None
        self.balance_tier_color_check = None
        if self.include_ai:
            self.balance_refresh_spin = BrowserSpinBox(self)
            self.balance_refresh_spin.setRange(0, 1440)
            self.balance_refresh_spin.setSuffix(" 分钟")
            self.balance_refresh_spin.setValue(int(self.config.get("balance_refresh_minutes", 0) or 0))
            self.balance_tier_mode_select = ModernSelect(self, width=180)
            self.balance_tier_mode_select.addItem("空闲 / 高峰（默认）", "default")
            self.balance_tier_mode_select.addItem("梁文谷 / 梁文峰", "liangwen")
            self.balance_tier_mode_select.addItem("自定义", "custom")
            self.balance_tier_mode_select.setCurrentData(
                str(self.config.get("balance_tier_labels_mode", "default") or "default")
            )
            self.balance_tier_peak_edit = QLineEdit(self)
            self.balance_tier_peak_edit.setPlaceholderText("高峰文本，例如：梁文峰")
            self.balance_tier_peak_edit.setText(str(self.config.get("balance_tier_label_peak", "") or ""))
            self.balance_tier_idle_edit = QLineEdit(self)
            self.balance_tier_idle_edit.setPlaceholderText("空闲文本，例如：梁文谷")
            self.balance_tier_idle_edit.setText(str(self.config.get("balance_tier_label_idle", "") or ""))
            self.balance_tier_color_check = ToggleSwitch(self)
            self.balance_tier_color_check.setChecked(bool(self.config.get("balance_tier_color_enabled", True)))
        self.auto_hide_fullscreen_check = None
        self.stream_capture_check = None
        if sys.platform == "win32":
            self.auto_hide_fullscreen_check = ToggleSwitch(self)
            self.auto_hide_fullscreen_check.setChecked(bool(self.config.get("auto_hide_fullscreen", True)))
            self.stream_capture_check = ToggleSwitch(self)
            self.stream_capture_check.setChecked(bool(self.config.get("stream_capture_mode", False)))

        self.speed_select = ModernSelect(self, width=112)
        current_speed = float(self.config.get("playback_speed", 1.0))
        speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        if not any(abs(current_speed - value) < 0.001 for value in speeds):
            speeds.append(current_speed)
            speeds.sort()
        for speed in speeds:
            self.speed_select.addItem(f"{speed:g}x", speed)
        self.speed_select.setCurrentData(current_speed)
        self.gap_spin = BrowserDoubleSpinBox(self)
        self.gap_spin.setRange(0.0, 3600.0)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSuffix(" 秒")
        self.gap_spin.setValue(float(self.config.get("animation_gap_seconds", 0.0)))

        self.self_talk_check = ToggleSwitch(self)
        self.self_talk_check.setChecked(bool(self.config.get("self_talk_enabled", False)))
        self.self_talk_duration_spin = BrowserDoubleSpinBox(self)
        self.self_talk_duration_spin.setRange(1.0, 300.0)
        self.self_talk_duration_spin.setSingleStep(0.5)
        self.self_talk_duration_spin.setDecimals(1)
        self.self_talk_duration_spin.setSuffix(" 秒")
        self.self_talk_duration_spin.setValue(float(self.config.get(
            "self_talk_duration_seconds", DEFAULT_SELF_TALK_DURATION_SECONDS
        )))
        self.bubble_style_select = ModernSelect(self, width=172)
        for value, preset in BUBBLE_STYLE_PRESETS.items():
            self.bubble_style_select.addItem(str(preset["label"]), value)
        self.bubble_style_select.setCurrentData(
            str(self.config.get("self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self.min_spin = BrowserDoubleSpinBox(self)
        self.max_spin = BrowserDoubleSpinBox(self)
        for spin, value in (
            (self.min_spin, self.config.get("self_talk_min_interval", DEFAULT_SELF_TALK_MIN_INTERVAL)),
            (self.max_spin, self.config.get("self_talk_max_interval", DEFAULT_SELF_TALK_MAX_INTERVAL)),
        ):
            spin.setRange(5.0, 3600.0)
            spin.setDecimals(0)
            spin.setSuffix(" 秒")
            spin.setValue(float(value))
        self.texts_edit = QPlainTextEdit(self)
        self.texts_edit.setMinimumSize(240, 82)
        self.texts_edit.setMaximumHeight(170)
        texts = self.config.get("self_talk_texts", DEFAULT_SELF_TALK_TEXTS)
        self.texts_edit.setPlainText("\n".join(str(item) for item in texts))
        self.self_talk_image_dir_picker = ResourcePathPicker(
            str(self.config.get("self_talk_image_dir", "") or ""),
            directory=True,
            parent=self,
        )

        # Agent 联动：每个 Agent 的自定义 thinking 气泡文案
        agent_link_cfg = self.config.get("agent_link", {})
        thinking_texts = agent_link_cfg.get("thinking_texts") or {}
        # 兼容旧的全局 thinking_text 字段
        legacy_text = str(agent_link_cfg.get("thinking_text", "") or "")
        self.thinking_text_edits: dict[str, QLineEdit] = {}
        for agent_key, agent_name in AgentLinkManager.AGENT_NAMES.items():
            edit = QLineEdit(self)
            default = AgentLinkManager._THINKING_DEFAULTS.get(agent_key, f"{agent_name} 正在深度烧烤……")
            edit.setPlaceholderText(default)
            text = str(thinking_texts.get(agent_key, "") or "")
            if not text and legacy_text:
                text = legacy_text
            edit.setText(text)
            edit.setClearButtonEnabled(True)
            self.thinking_text_edits[agent_key] = edit

        self.dialogue_mode_select = ModernSelect(self, width=190)
        for label, value in (("原有模式", "legacy"), ("鲸鱼娘女仆模式", "whale_maid"), ("自定义台词", "custom")):
            self.dialogue_mode_select.addItem(label, value)
        self.dialogue_mode_select.setCurrentData(str(self.config.get("dialogue_mode", "legacy") or "legacy"))
        configured_phrases = self.config.get("dialogue_phrases", {})
        self.dialogue_phrase_edits: dict[str, QLineEdit] = {}
        for key in phrase_keys():
            edit = QLineEdit(self)
            edit.setText(str(configured_phrases.get(key, "") or ""))
            edit.setPlaceholderText("留空使用基础模式台词；支持 {name}、{command}、{body}、{count}、{reasons}")
            edit.setClearButtonEnabled(True)
            self.dialogue_phrase_edits[key] = edit

        appearance = self.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
        self.menu_theme_select = ModernSelect(self, width=132)
        for label, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            self.menu_theme_select.addItem(label, value)
        self.menu_theme_select.setCurrentData(appearance.get("theme", "system"))
        self.menu_density_select = ModernSelect(self, width=132)
        for label, value in (("紧凑", "compact"), ("标准", "standard"), ("宽松", "spacious")):
            self.menu_density_select.addItem(label, value)
        self.menu_density_select.setCurrentData(appearance.get("density", "standard"))
        self.menu_radius_select = ModernSelect(self, width=112)
        for radius in (8, 12, 16, 18):
            self.menu_radius_select.addItem(f"{radius} px", radius)
        self.menu_radius_select.setCurrentData(int(appearance.get("corner_radius", 12)))
        self.menu_font_select = ModernSelect(self, width=172)
        self.menu_font_select.addItem("系统默认", "system")
        self._menu_fonts_populated = False
        current_font = str(appearance.get("ui_font") or "system")
        if current_font != "system":
            # 保留当前配置值无需枚举字体库，确保用户未展开选择器直接保存时
            # 不会把自定义字体静默重置为 system。
            self.menu_font_select.addItem(current_font, current_font)
        self.menu_font_select.setCurrentData(current_font)
        # Windows 字体较多时首次枚举可阻塞数秒。零延迟定时器仍会在
        # 设置窗口首帧绘制前运行，因此改为仅在用户真正展开字体选择器时加载。
        self.menu_font_select.aboutToShowPopup.connect(self._populate_menu_fonts)
        self.menu_font_size_select = ModernSelect(self, width=112)
        for size in range(10, 19):
            self.menu_font_size_select.addItem(f"{size} px", size)
        self.menu_font_size_select.setCurrentData(int(appearance.get("ui_font_size", 13)))
        self.menu_translucent_check = ToggleSwitch(self)
        self.menu_translucent_check.setChecked(bool(appearance.get("translucent", True)))
        self.menu_opacity_spin = BrowserDoubleSpinBox(self)
        self.menu_opacity_spin.setRange(0.72, 1.0)
        self.menu_opacity_spin.setSingleStep(0.02)
        self.menu_opacity_spin.setDecimals(2)
        self.menu_opacity_spin.setValue(float(appearance.get("opacity", 0.94)))

        def color_picker(key: str) -> ColorPicker:
            return ColorPicker(str(appearance.get(key) or DEFAULT_CONTEXT_MENU_APPEARANCE[key]), self)

        self.light_background_picker = color_picker("light_background")
        self.light_foreground_picker = color_picker("light_foreground")
        self.light_hover_picker = color_picker("light_hover")
        self.dark_background_picker = color_picker("dark_background")
        self.dark_foreground_picker = color_picker("dark_foreground")
        self.dark_hover_picker = color_picker("dark_hover")

        egg = self.config.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG)
        self.egg_enabled_check = ToggleSwitch(self)
        self.egg_enabled_check.setChecked(bool(egg.get("enabled", True)))
        self.egg_title_edit = _line_edit(str(egg.get("title") or "厉害了我的鲸"), width=240)
        self.egg_hint_edit = _line_edit(str(egg.get("hint") or "请点击"), width=160)
        avatar = resolve_fun_asset(egg.get("avatar"), oijingjing_image_path())
        image_dir = resolve_fun_asset(egg.get("image_dir"), oijingjing_image_path().parent)
        self.egg_avatar_picker = ResourcePathPicker(str(avatar.resolve()), parent=self)
        self.egg_image_dir_picker = ResourcePathPicker(str(image_dir.resolve()), directory=True, parent=self)


    # ------------------------------------------------------------ 主动识屏
        if sys.platform == "win32" and self.include_ai:
            self._build_proactive_controls()
    def _build_proactive_controls(self) -> None:
        """主动识屏页控件（仅 Windows + 有聊天能力时挂载）。"""
        from .proactive import effective_proactive_config

        pro = effective_proactive_config(self.config.get("proactive_screen", {}))

        self.pro_enabled_check = ToggleSwitch(self)
        self.pro_enabled_check.setChecked(bool(pro["enabled"]))
        self.pro_dryrun_check = ToggleSwitch(self)
        self.pro_dryrun_check.setChecked(bool(pro["dry_run"]))

        self.pro_preset_select = ModernSelect(self, width=160)
        for key, label in (
            ("balanced", "平衡（推荐）"),
            ("quiet", "安静"),
            ("active", "活跃"),
            ("custom", "自定义参数"),
        ):
            self.pro_preset_select.addItem(label, key)
        idx = {"quiet": 1, "balanced": 0, "active": 2, "custom": 3}.get(pro["preset"], 0)
        self.pro_preset_select.setCurrentIndex(idx)
        self.pro_preset_select.currentIndexChanged.connect(self._on_pro_preset_changed)

        self.pro_dwell_spin = BrowserSpinBox(self)
        self.pro_dwell_spin.setRange(15, 600)
        self.pro_dwell_spin.setValue(int(pro["dwell_seconds"]))

        self.pro_cooldown_spin = BrowserDoubleSpinBox(self)
        self.pro_cooldown_spin.setRange(0.5, 7200)
        self.pro_cooldown_spin.setDecimals(2)
        self.pro_cooldown_unit = ModernSelect(self, width=80)
        self.pro_cooldown_unit.addItem("分钟", "min")
        self.pro_cooldown_unit.addItem("秒", "sec")
        self._pro_set_cooldown_display(float(pro["cooldown_minutes"]))
        self.pro_cooldown_unit.currentIndexChanged.connect(self._on_pro_cooldown_unit_changed)

        self.pro_min_interval_spin = BrowserSpinBox(self)
        self.pro_min_interval_spin.setRange(30, 3600)
        self.pro_min_interval_spin.setValue(int(pro["min_request_interval_seconds"]))

        self.pro_cap_spin = BrowserSpinBox(self)
        self.pro_cap_spin.setRange(1, 9999)
        self.pro_cap_spin.setValue(int(pro["daily_cap"]))

        self.pro_idle_check = ToggleSwitch(self)
        self.pro_idle_check.setChecked(bool(pro["require_idle"]))
        self.pro_idle_spin = BrowserSpinBox(self)
        self.pro_idle_spin.setRange(5, 3600)
        raw_idle = (self.config.get("proactive_screen", {}) or {}).get("min_idle_seconds", 30)
        self.pro_idle_spin.setValue(int(raw_idle or 30))

        self.pro_through_check = ToggleSwitch(self)
        self.pro_through_check.setChecked(bool(pro["allow_when_mouse_through"]))
        self.pro_precue_check = ToggleSwitch(self)
        self.pro_precue_check.setChecked(bool(pro["pre_cue"]))
        self.pro_free_check = ToggleSwitch(self)
        self.pro_free_check.setChecked(bool(pro["prefer_free_provider"]))

        self.pro_whitelist_edit = QPlainTextEdit(self)
        self.pro_whitelist_edit.setPlaceholderText("msedge.exe\ntitle:*会议*")
        self.pro_whitelist_edit.setPlainText("\n".join(str(x) for x in pro["whitelist"]))
        self.pro_whitelist_edit.setMinimumHeight(72)

        self.pro_add_btn = QPushButton("从当前前台窗口添加…", self)
        self.pro_add_btn.setProperty("variant", "ghost")
        self.pro_add_btn.clicked.connect(self._on_pro_add_foreground)
        self._pro_add_timer = QTimer(self)
        self._pro_add_timer.setSingleShot(True)
        self._pro_add_timer.timeout.connect(self._do_pro_add_foreground)

        self.pro_clear_mem_btn = QPushButton("清除陪伴记忆", self)
        self.pro_clear_mem_btn.setProperty("variant", "ghost")
        self.pro_clear_mem_btn.clicked.connect(self._on_pro_clear_memory)

    def _pro_set_cooldown_display(self, minutes: float) -> None:
        unit = "sec" if minutes < 1 else "min"
        self._pro_apply_cooldown_unit(unit, minutes)

    def _pro_apply_cooldown_unit(self, unit: str, minutes: float) -> None:
        self.pro_cooldown_unit.blockSignals(True)
        self.pro_cooldown_unit.setCurrentIndex(1 if unit == "sec" else 0)
        if unit == "sec":
            self.pro_cooldown_spin.setRange(30, 7200)
            self.pro_cooldown_spin.setDecimals(0)
            self.pro_cooldown_spin.setValue(min(7200, max(30, round(minutes * 60))))
        else:
            self.pro_cooldown_spin.setRange(0.5, 120)
            self.pro_cooldown_spin.setDecimals(2)
            self.pro_cooldown_spin.setValue(min(120.0, max(0.5, minutes)))
        self._pro_cooldown_last_unit = unit
        self.pro_cooldown_unit.blockSignals(False)

    def _on_pro_cooldown_unit_changed(self) -> None:
        old = getattr(self, "_pro_cooldown_last_unit", "min")
        v = float(self.pro_cooldown_spin.value())
        minutes = v / 60.0 if old == "sec" else v
        self._pro_apply_cooldown_unit(self.pro_cooldown_unit.currentData(), minutes)

    def _pro_cooldown_minutes(self) -> float:
        v = float(self.pro_cooldown_spin.value())
        return v / 60.0 if self.pro_cooldown_unit.currentData() == "sec" else v

    def _on_pro_preset_changed(self, _index: int) -> None:
        from .proactive import PRESET_DEFAULTS
        vals = PRESET_DEFAULTS.get(self.pro_preset_select.currentData())
        if vals:
            self.pro_dwell_spin.setValue(vals["dwell_seconds"])
            self._pro_set_cooldown_display(float(vals["cooldown_minutes"]))
            self.pro_cap_spin.setValue(vals["daily_cap"])

    def _on_pro_add_foreground(self) -> None:
        self.pro_add_btn.setEnabled(False)
        self.pro_add_btn.setText("请在 3 秒内切换到目标窗口…")
        self._pro_add_timer.start(3000)

    def _do_pro_add_foreground(self) -> None:
        self.pro_add_btn.setEnabled(True)
        self.pro_add_btn.setText("从当前前台窗口添加…")
        from . import vision
        info = vision.foreground_window_info()
        if not info:
            QMessageBox.information(self, "添加前台窗口", "未能检测到有效的前台窗口，请将目标软件置顶后再试。")
            return
        proc = str(info.get("process", "")).strip()
        title = str(info.get("title", "")).strip()
        box = QMessageBox(self)
        box.setWindowTitle("添加到白名单")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"检测到前台窗口：\n进程：{proc or '（未知）'}\n标题：{title or '（空）'}\n\n要按哪种方式关注它？"
        )
        btn_proc = box.addButton("按软件（推荐）", QMessageBox.ButtonRole.AcceptRole)
        btn_title = box.addButton("按标题关键词", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        lines = [x.strip() for x in self.pro_whitelist_edit.toPlainText().splitlines() if x.strip()]
        if box.clickedButton() is btn_proc and proc and proc not in lines:
            lines.append(proc)
        elif box.clickedButton() is btn_title and title:
            rule = f"title:*{title}*"
            if rule not in lines:
                lines.append(rule)
        else:
            return
        self.pro_whitelist_edit.setPlainText("\n".join(lines))

    def _on_pro_clear_memory(self) -> None:
        from .proactive import ProactiveMemory
        ProactiveMemory(self.config.dir / "proactive_screen_memory.json").clear()
        QMessageBox.information(self, "陪伴记忆", "已清空主动识屏的短期陪伴记忆。")

    def _proactive_page_content(self) -> QWidget:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(SettingsSection("总开关与节奏", [
            SettingRow("proactive_enabled", "开启主动识屏",
                       "她会偶尔看一眼你在用的软件并说句话。截图只在内存处理、不落盘、不写入会话。",
                       self.pro_enabled_check),
            SettingRow("proactive_dry_run", "dry-run 验证模式",
                       "开启后满足条件只写日志、不调用模型、不消耗额度。", self.pro_dryrun_check),
            SettingRow("proactive_preset", "陪伴节奏预设",
                       "平衡 45s/5min/15次；安静 90s/10min/8次；活跃 20s/3min/25次（停留/冷却/每日上限）。",
                       self.pro_preset_select),
        ], content))
        layout.addWidget(SettingsSection("频率参数（自定义预设时生效）", [
            SettingRow("proactive_dwell", "窗口停留门限（秒）", "同一前台窗口持续停留该时长才可能触发。",
                       self.pro_dwell_spin),
            SettingRow("proactive_cooldown", "关怀冷却间隔", "两次关怀的最短间隔，支持秒/分钟。",
                       self._pro_cooldown_row()),
            SettingRow("proactive_min_interval", "最小请求间隔（秒）", "免费模型的硬保护，不建议调太小。",
                       self.pro_min_interval_spin),
            SettingRow("proactive_daily_cap", "每日请求上限", "DeepSeek 视觉单次约 ¥0.003；上限 9999 约等于不限。",
                       self.pro_cap_spin),
        ], content))
        layout.addWidget(SettingsSection("触发条件", [
            SettingRow("proactive_require_idle", "仅当我闲置时触发", "勾选后，敲键盘/动鼠标时不打扰。",
                       self.pro_idle_check),
            SettingRow("proactive_idle_seconds", "闲置判定秒数", "勾选上方后，闲置该秒数才触发。",
                       self.pro_idle_spin),
            SettingRow("proactive_through", "鼠标穿透时仍识屏", "桌宠处于鼠标穿透状态时是否继续工作。",
                       self.pro_through_check),
            SettingRow("proactive_pre_cue", "触发前先兆提示", "触发前先冒一句「让我看看……」。",
                       self.pro_precue_check),
            SettingRow("proactive_free", "识屏优先用独立视觉配置", "开：服务商配了独立视觉端点（如免费的智谱 GLM-4.6V-Flash）时识屏走它；关：始终跟随聊天模型。",
                       self.pro_free_check),
        ], content))
        layout.addWidget(SettingsSection("白名单", [
            SettingRow("proactive_whitelist",
                       "白名单（每行一条）",
                       "进程名（如 msedge.exe）= 关注这个软件；title:关键词 = 只关注标题含该词的窗口。留空 = 不识屏。",
                       self.pro_whitelist_edit, stacked=True),
            SettingRow("proactive_whitelist_add", "快捷添加",
                       "点击后 3 秒内切换到目标窗口，自动采样进程名/标题。", self.pro_add_btn),
            SettingRow("proactive_memory_clear", "陪伴记忆",
                       "只存进程名和活动分类（不落标题、不存截图），可随时清空。",
                       self.pro_clear_mem_btn),
        ], content))
        layout.addStretch(1)
        return content

    def _pro_cooldown_row(self) -> QWidget:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(self.pro_cooldown_spin)
        h.addWidget(self.pro_cooldown_unit)
        return row

    def _update_self_talk_controls(self, enabled: bool) -> None:
        keys = (
            "self_talk_duration", "self_talk_min", "self_talk_max",
            "self_talk_texts", "self_talk_images", "click_self_talk",
        )
        controls = (
            self.self_talk_duration_spin, self.min_spin, self.max_spin,
            self.texts_edit, self.self_talk_image_dir_picker,
            self.click_self_talk_check,
        )
        for key, control in zip(keys, controls):
            control.setEnabled(bool(enabled))
            row = self.findChild(SettingRow, f"settingRow_{key}")
            if row is not None:
                row.setEnabled(bool(enabled))

    def _populate_menu_fonts(self) -> None:
        if shiboken6.isValid(self) is False or self._menu_fonts_populated:
            return
        self._menu_fonts_populated = True
        appearance = self.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
        for family in _system_font_families():
            if self.menu_font_select.findData(family) < 0:
                self.menu_font_select.addItem(family, family)
        current_font = str(appearance.get("ui_font") or "system")
        if self.menu_font_select.findData(current_font) < 0:
            self.menu_font_select.addItem(current_font, current_font)
        self.menu_font_select.setCurrentData(current_font)

    def _update_click_sound_controls(self, enabled: bool) -> None:
        row = self.findChild(SettingRow, "settingRow_click_sound_path")
        if row is not None:
            row.setVisible(bool(enabled))
            card = row.parentWidget()
            if isinstance(card, SettingsCard):
                card.refresh_separators()

    def _update_translucency_controls(self, enabled: bool) -> None:
        self.menu_opacity_spin.setEnabled(bool(enabled))
        row = self.findChild(SettingRow, "settingRow_menu_opacity")
        if row is not None:
            row.setEnabled(bool(enabled))

    def move_away_from_pet(self) -> None:
        """把窗口定位到不与桌宠相交的位置。

        在 show() 之前调用（_present_dialog 的 before_present），窗口首帧
        即落在最终位置，避免 Windows 上"先显示默认位置再跳走"的两段式。
        """
        if self._positioned_away:
            return
        self._positioned_away = True
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            self._move_away_from(parent.geometry())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().showEvent(event)
        # 兜底：未经 _present_dialog 直接 show 的路径仍要避让桌宠
        self.move_away_from_pet()

    def _move_away_from(self, pet_geo: QRect) -> None:
        """首次显示时把窗口移到不与桌宠相交的位置（右侧优先，再左侧/下方/上方）。"""
        size = self.size()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect()
        for rect in (
            QRect(pet_geo.right() + 12, pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left() - 12 - size.width(), pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.bottom() + 12, size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.top() - 12 - size.height(), size.width(), size.height()),
        ):
            if avail.contains(rect):
                self.move(rect.topLeft())
                return

    def _page_shell(self, title: str, content: QWidget) -> QWidget:
        page = QWidget(self.pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 26, 18)
        layout.setSpacing(12)
        heading = QLabel(title, page)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        scroll = QScrollArea(page)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        content.setMaximumWidth(960)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def _add_page(self, label: str, icon_name: str, page: QWidget) -> None:
        item = QListWidgetItem(vector_widget_icon(self, icon_name, 16), label)
        item.setSizeHint(QSize(0, 34))
        self.sidebar.addItem(item)
        self.pages.addWidget(page)

    def _clear_search_matches(self) -> None:
        for row in self._search_rows:
            if row.property("searchMatch"):
                row.setProperty("searchMatch", False)
                row.style().unpolish(row)
                row.style().polish(row)

    def _search_settings(self, query: str, *, advance: bool = False) -> None:
        query = query.strip().lower()
        self._clear_search_matches()
        if not query:
            self._search_matches = []
            self._search_index = -1
            self.search_status.hide()
            return
        matches = [
            row for row in self._search_rows
            if query in f"{row.label.text()} {row.hint_label.text()} {row.objectName()}".lower()
        ]
        if not matches:
            self._search_matches = []
            self._search_index = -1
            self.search_status.setText("未找到匹配的设置")
            self.search_status.show()
            return
        if matches != self._search_matches:
            self._search_matches = matches
            self._search_index = 0
        elif advance:
            self._search_index = (self._search_index + 1) % len(matches)
        row = matches[self._search_index]
        row.setProperty("searchMatch", True)
        row.style().unpolish(row)
        row.style().polish(row)
        page_index = 0
        for index in range(self.pages.count()):
            if self.pages.widget(index).isAncestorOf(row):
                page_index = index
                break
        self.sidebar.setCurrentRow(page_index)
        page = self.pages.widget(page_index)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        if scroll is not None:
            scroll.ensureWidgetVisible(row, 0, 24)
        self.search_status.setText(
            f"{self._search_index + 1}/{len(matches)} · {row.label.text()}"
        )
        self.search_status.show()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            watched is self.search_edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self._search_settings(self.search_edit.text(), advance=True)
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _stylesheet() -> str:
        return _settings_stylesheet()



    def _apply_autostart(self) -> None:
        """应用「开机自启」开关：仅在实际改动时写入系统登录项。

        保存按钮与直接关闭（X / Esc）共用，保证三条路径行为一致。
        """
        if self.autostart_check.isChecked() != self._autostart_initial:
            # set_enabled 返回 bool（enable()/disable()）；仅在明确失败时提示。
            ok = autostart_mod.set_enabled(self.autostart_check.isChecked())
            if ok is False:
                QMessageBox.warning(
                    self,
                    "开机自启设置失败",
                    "写入开机自启失败：可能被安全软件拦截。\n"
                    "可稍后在托盘菜单重试，或检查安全软件/系统优化工具的拦截记录。",
                )

    def _save(self) -> None:
        """「保存并退出」：写入配置并关闭对话框。"""
        self._saved_via_button = True
        self._write_config()
        self._apply_autostart()
        self.settings_saved.emit()
        self.accept()

    def _write_config(self) -> bool:
        """把当前控件值写入 config 并落盘（按钮与直接关闭共用）。

        保存前从磁盘重读：吸收外部对本对话框未暴露字段的改动。
        已知限制：已暴露字段仍是 last-writer-wins（对话框获胜）。
        返回是否成功落盘；失败时提示用户。
        """
        self.config._load()
        minimum = min(self.min_spin.value(), self.max_spin.value())
        maximum = max(self.min_spin.value(), self.max_spin.value())
        texts = [line.strip()[:120] for line in self.texts_edit.toPlainText().splitlines() if line.strip()]
        self.config.set("scale", float(self.scale_combo.currentData()))
        self.config.set("on_top", self.on_top_check.isChecked())
        if self.dock_icon_check is not None:
            self.config.set("show_dock_icon", self.dock_icon_check.isChecked())
        self.config.set("no_move", self.no_move_check.isChecked())
        self.config.set("mouse_through", self.mouse_through_check.isChecked())
        self.config.set("drag_physics", self.drag_physics_check.isChecked())
        self.config.set("lock_position", self.lock_position_check.isChecked())
        self.config.set("shift_drag", self.shift_drag_check.isChecked())
        self.config.set("pet_opacity", int(self.pet_opacity_spin.value()))
        self.config.set("click_sound_enabled", self.click_sound_check.isChecked())
        self.config.set("click_sound_path", self.click_sound_picker.text())
        if self.click_balance_check is not None:
            self.config.set("click_show_balance", self.click_balance_check.isChecked())
        self.config.set("click_show_self_talk", self.click_self_talk_check.isChecked())
        self.config.set("music_sing_enabled", self.music_sing_check.isChecked())
        if self.balance_refresh_spin is not None:
            self.config.set("balance_refresh_minutes", int(self.balance_refresh_spin.value()))
            self.config.set(
                "balance_tier_labels_mode",
                str(self.balance_tier_mode_select.currentData() or "default"),
            )
            self.config.set("balance_tier_label_peak", self.balance_tier_peak_edit.text().strip())
            self.config.set("balance_tier_label_idle", self.balance_tier_idle_edit.text().strip())
            self.config.set("balance_tier_color_enabled", self.balance_tier_color_check.isChecked())
        if self.auto_hide_fullscreen_check is not None:
            self.config.set("auto_hide_fullscreen", self.auto_hide_fullscreen_check.isChecked())
        if self.stream_capture_check is not None:
            self.config.set("stream_capture_mode", self.stream_capture_check.isChecked())
        self.config.set("playback_speed", float(self.speed_select.currentData()))
        self.config.set("animation_gap_seconds", self.gap_spin.value())
        self.config.set("self_talk_enabled", self.self_talk_check.isChecked())
        self.config.set("self_talk_bubble_style", self.bubble_style_select.currentData())
        self.config.set("self_talk_min_interval", minimum)
        self.config.set("self_talk_max_interval", maximum)
        self.config.set("self_talk_duration_seconds", self.self_talk_duration_spin.value())
        self.config.set("self_talk_texts", texts or list(DEFAULT_SELF_TALK_TEXTS))
        self.config.set("self_talk_image_dir", self.self_talk_image_dir_picker.text())
        self.config.set("dialogue_mode", str(self.dialogue_mode_select.currentData() or "legacy"))
        self.config.set("dialogue_phrases", {
            key: edit.text().strip() for key, edit in self.dialogue_phrase_edits.items() if edit.text().strip()
        })
        # Agent 联动：自定义 thinking 文案（合并写回，不覆盖 agent_link 其他开关）
        agent_cfg = dict(self.config.get("agent_link", {}))
        agent_cfg["thinking_texts"] = {
            key: edit.text().strip()
            for key, edit in self.thinking_text_edits.items()
            if edit.text().strip()
        }
        agent_cfg.pop("thinking_text", None)  # 旧的全局字段已迁移到 thinking_texts
        # 循环检测设置页（合并写回，不覆盖 agent_link 其他字段）
        if self.watchdog_page is not None:
            agent_cfg = self.watchdog_page.apply_to_config(agent_cfg)
        self.config.set("agent_link", agent_cfg)
        self.config.set("context_menu_appearance", {
            "theme": self.menu_theme_select.currentData(),
            "density": self.menu_density_select.currentData(),
            "corner_radius": self.menu_radius_select.currentData(),
            "ui_font": self.menu_font_select.currentData(),
            "ui_font_size": self.menu_font_size_select.currentData(),
            "translucent": self.menu_translucent_check.isChecked(),
            "opacity": self.menu_opacity_spin.value(),
            "light_background": self.light_background_picker.text(),
            "light_foreground": self.light_foreground_picker.text(),
            "light_hover": self.light_hover_picker.text(),
            "dark_background": self.dark_background_picker.text(),
            "dark_foreground": self.dark_foreground_picker.text(),
            "dark_hover": self.dark_hover_picker.text(),
        })
        self.config.set("menu_easter_egg", {
            "enabled": self.egg_enabled_check.isChecked(),
            "title": self.egg_title_edit.text(),
            "hint": self.egg_hint_edit.text(),
            # 内置 assets 内的路径归一化回相对值，保持 portable（目录移动/自更新后仍可用）
            "avatar": store_fun_asset(self.egg_avatar_picker.text(), oijingjing_image_path()),
            "image_dir": store_fun_asset(self.egg_image_dir_picker.text(), oijingjing_image_path().parent),
        })
        self.config.set("quick_launch_apps", self.quick_launch_editor.apps())
        if self.ai_page is not None:
            self.ai_page.save()
        if sys.platform == "win32" and self.include_ai and hasattr(self, "pro_enabled_check"):
            from .proactive import PRESET_DEFAULTS
            pro_data = dict(self.config.get("proactive_screen", {}) or {})
            preset = self.pro_preset_select.currentData()
            # 非 custom 预设下改了数值 → 自动落为 custom，否则运行时会被预设覆盖（gemini 审查发现）
            if preset in PRESET_DEFAULTS:
                pv = PRESET_DEFAULTS[preset]
                if (self.pro_dwell_spin.value() != pv["dwell_seconds"]
                        or abs(self._pro_cooldown_minutes() - pv["cooldown_minutes"]) > 1e-6
                        or self.pro_cap_spin.value() != pv["daily_cap"]):
                    preset = "custom"
            pro_data.update({
                "enabled": self.pro_enabled_check.isChecked(),
                "dry_run": self.pro_dryrun_check.isChecked(),
                "preset": preset,
                "dwell_seconds": self.pro_dwell_spin.value(),
                "cooldown_minutes": self._pro_cooldown_minutes(),
                "min_request_interval_seconds": self.pro_min_interval_spin.value(),
                "daily_cap": self.pro_cap_spin.value(),
                "require_idle": self.pro_idle_check.isChecked(),
                "min_idle_seconds": self.pro_idle_spin.value(),
                "allow_when_mouse_through": self.pro_through_check.isChecked(),
                "pre_cue": self.pro_precue_check.isChecked(),
                "prefer_free_provider": self.pro_free_check.isChecked(),
                "whitelist": [x.strip() for x in self.pro_whitelist_edit.toPlainText().splitlines() if x.strip()],
            })
            self.config.set("proactive_screen", pro_data)
        self.config.set("autostart_wanted", self.autostart_check.isChecked())
        ok = self.config.save()
        if not ok:
            QMessageBox.warning(
                self,
                "保存失败",
                "配置未能写入磁盘，改动可能在重启后丢失。\n\n配置路径："
                + str(self.config.path),
            )
        return ok

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """直接关闭（X / Esc）时同样落盘，避免修改丢失。

        设置项都是即时型偏好，与右键菜单/托盘修改的写入时机保持一致；
        已走「保存并退出」则跳过（防重复写入）。
        """
        if not getattr(self, "_saved_via_button", False):
            try:
                self._write_config()
                self._apply_autostart()
            except Exception:
                logging.exception("关闭设置时保存配置失败")
        super().closeEvent(event)
def _system_dark() -> bool:
    """按系统调色板判断深色模式（QSS 的 color 不自动级联到子控件，
    深色系统下未显式设 color 的控件会落到 palette 白字，白底上看不清）。"""
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.palette().window().color().lightness() < 128


# 深色系统的覆盖段：追加在浅色 QSS 之后（后写规则优先）
_DARK_OVERRIDE = """
QDialog { background: #202024; color: #e4e4e9; }
QFrame#sidebarPane { background: #26262b; border-right: 1px solid #34343a; }
QStackedWidget { background: #202024; }
QLineEdit#settingsSearch { background: #2e2e35; color: #e4e4e9; }
QPushButton#saveAndExit { color: #e4e4e9; }
QPushButton#saveAndExit:hover { background: #33333c; }
QListWidget#settingsSidebar::item { color: #b8b8c0; }
QListWidget#settingsSidebar::item:hover { background: #2e2e36; color: #f0f0f5; }
QListWidget#settingsSidebar::item:selected { background: #3a3a46; color: #ffffff; }
QLabel#pageTitle { color: #f0f0f5; }
QLabel#sectionTitle { color: #d8d8e0; }
QFrame#settingsCard { background: #2a2a30; border: 1px solid #3a3a42; }
QFrame#cardSeparator { background: #33333a; }
QLabel#settingLabel { color: #e0e0e6; }
QLabel#settingHint { color: #9a9aa3; }
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #66666e; }
SettingRow[searchMatch="true"] { background: #2c3a4e; }
QListWidget#quickLaunchList { background: #26262c; border: 1px solid #3c3c44; }
QListWidget#quickLaunchList::item:selected { background: #3a3a46; color: #ffffff; }
QPushButton { background: #3a3a42; border: 1px solid #4a4a54; color: #e4e4e9; }
QPushButton:hover { background: #44444e; }
QToolButton { color: #e4e4e9; }
QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #e4e4e9; }
"""

_DARK_BROWSER_OVERRIDE = """
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #2e2e35; color: #e4e4e9; border: 1px solid #45454f;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover { border-color: #56565f; }
QSpinBox::up-button, QDoubleSpinBox::up-button { border-left: 1px solid #45454f; border-bottom: 1px solid #45454f; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #55555e; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #6a6a74; }
"""

_DARK_POPUP_OVERRIDE = """
QMenu#ModernSelectPopup { background: #2a2a30; color: #e4e4e9; border: 1px solid #45454f; }
QMenu#ModernSelectPopup::item { color: #e4e4e9; }
QMenu#ModernSelectPopup::item:selected { background: #3a3a46; }
"""


def _settings_stylesheet() -> str:
    """浅色基础 QSS + 显式控件文字色补丁；深色系统时追加深色覆盖段。"""
    light_patch = """
        QPushButton { color: #202020; }
        QToolButton { color: #202020; }
        QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #202020; }
    """
    base = _LIGHT_SETTINGS_STYLESHEET + light_patch
    if not _system_dark():
        return base + BROWSER_CONTROL_STYLESHEET
    return base + _DARK_OVERRIDE + BROWSER_CONTROL_STYLESHEET + _DARK_BROWSER_OVERRIDE


_LIGHT_SETTINGS_STYLESHEET = """
QDialog {
    background: #fcfcfd;
    color: #202020;
    font-family: "SF Pro Text", ".AppleSystemUIFont", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#sidebarPane {
    background: #f7f7f8;
    border: none;
    border-right: 1px solid #e3e5e8;
}
QStackedWidget { background: #fcfcfd; }
QLineEdit#settingsSearch {
    min-height: 30px;
    padding: 0 8px;
    background: #f0f1f3;
    border: 1px solid transparent;
    border-radius: 15px;
    color: #202020;
}
QLineEdit#settingsSearch:focus {
    border: 2px solid #0a84ff;
    padding: 0 7px;
}
QPushButton#saveAndExit {
    min-height: 28px;
    padding: 2px 8px;
    text-align: left;
    background: transparent;
    border: none;
    border-radius: 8px;
    font-weight: 500;
}
QPushButton#saveAndExit:hover { background: #e9eaec; }
QLabel#searchStatus {
    padding: 0 5px;
    color: #777b80;
    font-size: 11px;
}
QListWidget#settingsSidebar {
    background: transparent;
    border: none;
    outline: none;
    font-size: 13px;
    font-weight: 500;
}
QListWidget#settingsSidebar::item {
    min-height: 26px;
    padding: 4px 10px;
    border-radius: 9px;
    color: #4e4e4e;
}
QListWidget#settingsSidebar::item:hover {
    background: #eceef1;
    color: #202020;
}
QListWidget#settingsSidebar::item:selected {
    background: #e3e5e8;
    color: #171717;
}
QLabel#pageTitle {
    font-size: 26px;
    font-weight: 600;
    color: #171717;
}
QLabel#sectionTitle {
    font-size: 14px;
    font-weight: 600;
    color: #2b2b2b;
}
QFrame#settingsCard {
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 11px;
}
QFrame#cardSeparator {
    background: #eceef1;
    border: none;
    margin-left: 14px;
    margin-right: 14px;
}
QLabel#settingLabel {
    font-size: 14px;
    font-weight: 600;
    color: #252525;
}
QLabel#settingHint {
    font-size: 12px;
    font-weight: 400;
    color: #777777;
}
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #a6a8ac; }
SettingRow[searchMatch="true"] {
    background: #eaf3ff;
    border-radius: 8px;
}
QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget {
    background: transparent;
}
QListWidget#quickLaunchList {
    background: #fbfbfb;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    outline: none;
    padding: 3px;
}
QListWidget#quickLaunchList::item {
    min-height: 30px;
    padding: 3px 7px;
    border-radius: 6px;
}
QListWidget#quickLaunchList::item:selected { background: #e8e8e8; color: #202020; }
QPushButton {
    min-height: 26px;
    padding: 1px 12px;
    background: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 7px;
    font-weight: 500;
}
QPushButton:hover { background: #f0f0f0; }
"""
