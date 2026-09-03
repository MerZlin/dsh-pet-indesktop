# -*- coding: utf-8 -*-
"""新版设置控件库：modern_settings_dialog 使用的自定义控件。

批 6-7 从 modern_settings_dialog.py 整体搬移（逐行搬移，零逻辑改动）：
控件类的 paintEvent/样式逻辑一个像素都不变；设置页构建与配置写回仍留在
modern_settings_dialog.py，本模块仅负责控件本身与其样式常量。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QFileInfo, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .config import DEFAULT_QUICK_LAUNCH_APPS
from .context_menus.icons import vector_widget_icon
from .context_menus.quick_launch import fitted_application_icon


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


class ClickSoundPackPicker(QWidget):
    """点击音效包选择器（内置默认/小黄鸭/自定义单文件/自定义文件夹）。"""

    changed = Signal()

    def __init__(self, pack: dict | None = None, parent=None):
        super().__init__(parent)
        self.mode_select = ModernSelect(self, width=170)
        self.mode_select.addItem("默认包", "builtin:default")
        self.mode_select.addItem("小黄鸭包", "builtin:duck")
        self.mode_select.addItem("自定义单文件", "file")
        self.mode_select.addItem("自定义文件夹（随机）", "folder")

        self.file_picker = ResourcePathPicker("", name_filter=AUDIO_NAME_FILTER, parent=self)
        self.folder_picker = ResourcePathPicker("", directory=True, parent=self)

        self.stack = QStackedWidget(self)
        empty_page = QWidget(self)
        self.stack.addWidget(empty_page)         # 0: builtin (hidden)
        self.stack.addWidget(self.file_picker)    # 1: file
        self.stack.addWidget(self.folder_picker)  # 2: folder

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.mode_select)
        layout.addWidget(self.stack)

        self.mode_select.currentIndexChanged.connect(self._on_mode_changed)
        self.file_picker.edit.textChanged.connect(lambda: self.changed.emit())
        self.folder_picker.edit.textChanged.connect(lambda: self.changed.emit())

        self.set_pack(pack or {})

    def _on_mode_changed(self, index: int) -> None:
        data = self.mode_select.currentData()
        if data == "file":
            self.stack.setCurrentIndex(1)
            self.stack.show()
        elif data == "folder":
            self.stack.setCurrentIndex(2)
            self.stack.show()
        else:
            self.stack.setCurrentIndex(0)
            self.stack.hide()
        self.changed.emit()

    def value(self) -> dict:
        data = str(self.mode_select.currentData() or "builtin:default")
        if data.startswith("builtin:"):
            bid = data.split(":", 1)[1]
            return {"kind": "builtin", "id": bid, "path": ""}
        if data == "file":
            return {"kind": "file", "id": "custom", "path": self.file_picker.text()}
        if data == "folder":
            return {"kind": "folder", "id": "custom", "path": self.folder_picker.text()}
        return {"kind": "builtin", "id": "default", "path": ""}

    def set_pack(self, pack: dict) -> None:
        pack = pack if isinstance(pack, dict) else {}
        kind = str(pack.get("kind") or "builtin").strip().lower()
        pack_id = str(pack.get("id") or "default").strip()
        path = str(pack.get("path") or "")

        if kind == "builtin":
            if pack_id == "duck":
                self.mode_select.setCurrentData("builtin:duck")
            else:
                self.mode_select.setCurrentData("builtin:default")
            self.stack.setCurrentIndex(0)
            self.stack.hide()
        elif kind == "file":
            self.file_picker.setText(path)
            self.mode_select.setCurrentData("file")
            self.stack.setCurrentIndex(1)
            self.stack.show()
        elif kind == "folder":
            self.folder_picker.setText(path)
            self.mode_select.setCurrentData("folder")
            self.stack.setCurrentIndex(2)
            self.stack.show()
        else:
            self.mode_select.setCurrentData("builtin:default")
            self.stack.setCurrentIndex(0)
            self.stack.hide()


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


def _system_dark() -> bool:
    """按系统调色板判断深色模式（QSS 的 color 不自动级联到子控件，
    深色系统下未显式设 color 的控件会落到 palette 白字，白底上看不清）。"""
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.palette().window().color().lightness() < 128


_DARK_POPUP_OVERRIDE = """
QMenu#ModernSelectPopup { background: #2a2a30; color: #e4e4e9; border: 1px solid #45454f; }
QMenu#ModernSelectPopup::item { color: #e4e4e9; }
QMenu#ModernSelectPopup::item:selected { background: #3a3a46; }
"""
