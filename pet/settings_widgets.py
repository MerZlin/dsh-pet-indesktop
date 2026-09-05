# -*- coding: utf-8 -*-
"""Modern settings 控件库：可复用的设置控件、装配脚手架及配套常量/helper。

从 modern_settings_dialog.py 纯机械搬移（只搬代码 + 改 import，不改逻辑/行为/样式）。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFontDatabase, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QBoxLayout,
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .config import (
    DEFAULT_QUICK_LAUNCH_APPS,
)
from .context_menus.icons import (
    CUSTOM_ICON_SUFFIXES,
    vector_widget_icon,
)
from .context_menus.quick_launch import fitted_application_icon


def _system_font_families() -> tuple[str, ...]:
    """缓存系统字体族列表。

    macOS 上 QFontDatabase.families() 走 CoreText 枚举，首次调用可达数百 ms，
    设置窗口每次打开都重建实例，同步枚举会明显拖慢打开速度。
    """
    if _system_font_families._cache is None:
        _system_font_families._cache = tuple(QFontDatabase.families())
    return _system_font_families._cache

_system_font_families._cache = None

def _system_dark() -> bool:
    """按系统调色板判断深色模式（QSS 的 color 不自动级联到子控件，
    深色系统下未显式设 color 的控件会落到 palette 白字，白底上看不清）。"""
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.palette().window().color().lightness() < 128

BROWSER_CONTROL_SPEC = {
    "field_height": 32,
    "border": "#cfd4da",
    "border_hover": "#aeb6c0",
    "focus": "#0a84ff",
    "radius": 7,
    "scrollbar_width": 8,
}

SETTINGS_DOMAIN_NAV = (
    ("常规", "settings"),
    ("桌宠", "pet"),
    ("互动", "interaction"),
    ("菜单", "application"),
    ("桌面组件", "island"),
    ("AI 与对话", "chat"),
    ("自动化与联动", "automation"),
)

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

def _widget_dark(widget: QWidget | None = None) -> bool:
    current = widget
    while current is not None:
        explicit = current.property("settingsDark")
        if explicit is not None:
            return bool(explicit)
        current = current.parentWidget()
    return _system_dark()

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
        track = "#0a84ff" if self.isChecked() else ("#3a3a42" if _widget_dark(self) else "#dedede")
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

class MasonryLayout(QLayout):
    """A true shortest-column layout whose cards retain their image ratios."""

    def __init__(self, parent=None, *, column_count: int = 3, spacing: int = 10):
        super().__init__(parent)
        self.column_count = max(1, int(column_count))
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        width = max(0, rect.width() - left - right)
        gap = self.spacing()
        column_width = max(1, (width - gap * (self.column_count - 1)) // self.column_count)
        heights = [top] * self.column_count
        for item in self._items:
            column = min(range(self.column_count), key=heights.__getitem__)
            widget = item.widget()
            height = widget.heightForWidth(column_width) if widget and widget.hasHeightForWidth() else item.sizeHint().height()
            x = rect.x() + left + column * (column_width + gap)
            y = rect.y() + heights[column]
            if apply:
                item.setGeometry(QRect(x, y, column_width, height))
            heights[column] += height + gap
        return max(heights, default=top) - (gap if self._items else 0) + bottom

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, max(0, width), 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = 420
        return QSize(width, self.heightForWidth(width))

    def minimumSize(self) -> QSize:  # noqa: N802
        return QSize(300, self.heightForWidth(300))

class MasonryImageCard(QWidget):
    """Aspect-ratio thumbnail with an elided filename caption."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid() and max(source_size.width(), source_size.height()) > 512:
            source_size.scale(QSize(512, 512), Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(source_size)
        self.pixmap = QPixmap.fromImage(reader.read())
        self.setObjectName("masonryImageCard")
        self.setToolTip(path.name)
        self.setAccessibleName(path.name)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def _image_height(self, width: int) -> int:
        if self.pixmap.isNull() or self.pixmap.width() <= 0:
            return 96
        natural = round(width * self.pixmap.height() / self.pixmap.width())
        return max(72, min(230, natural))

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._image_height(width) + 28

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(120, self.heightForWidth(120))

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        image_height = self._image_height(self.width())
        image_rect = QRectF(0, 0, self.width(), image_height)
        clip = QPainterPath()
        clip.addRoundedRect(image_rect, 9, 9)
        painter.setClipPath(clip)
        if self.pixmap.isNull():
            painter.fillRect(image_rect, QColor("#e9ebee"))
        else:
            scaled = self.pixmap.scaled(
                image_rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            source_x = max(0, (scaled.width() - self.width()) // 2)
            source_y = max(0, (scaled.height() - image_height) // 2)
            painter.drawPixmap(image_rect.toRect(), scaled, QRect(source_x, source_y, self.width(), image_height))
        painter.setClipping(False)
        painter.setPen(QColor("#d8d8e0" if _widget_dark(self) else "#404348"))
        text = painter.fontMetrics().elidedText(self.path.name, Qt.TextElideMode.ElideRight, max(0, self.width() - 4))
        painter.drawText(QRectF(2, image_height + 5, self.width() - 4, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

class MasonryFlow(QWidget):
    def __init__(self, parent=None, *, column_count: int = 3):
        super().__init__(parent)
        self.setObjectName("imageMasonryFlow")
        self.layout = MasonryLayout(self, column_count=column_count)
        self.cards: list[MasonryImageCard] = []

    def set_paths(self, paths: list[Path]) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.cards = [MasonryImageCard(path, self) for path in paths]
        for card in self.cards:
            self.layout.addWidget(card)
        self._sync_height()

    def _sync_height(self) -> None:
        width = max(300, self.width())
        self.setMinimumHeight(self.layout.heightForWidth(width))
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

class ImagePreviewDrawer(QFrame):
    """Right-side on-demand image browser; decoding is deferred until opening."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("imagePreviewDrawer")
        self.setProperty("surface", "drawer")
        self.title_label = QLabel("图片预览", self)
        self.title_label.setObjectName("imagePreviewTitle")
        self.count_label = QLabel("0 张图片", self)
        self.count_label.setObjectName("imagePreviewCount")
        self.close_button = QPushButton(self)
        self.close_button.setObjectName("imagePreviewClose")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setIcon(vector_widget_icon(self, "exit", 14))
        self.close_button.setAccessibleName("关闭图片预览")
        self.close_button.clicked.connect(self.hide)
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addWidget(self.count_label)
        header.addStretch(1)
        header.addWidget(self.close_button)
        self.path_label = QLabel(self)
        self.path_label.setObjectName("imagePreviewPath")
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("imagePreviewScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.flow = MasonryFlow(column_count=3)
        self.scroll.setWidget(self.flow)
        self.empty_label = QLabel("这个目录中没有可预览的图片", self)
        self.empty_label.setObjectName("imagePreviewEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        root.addLayout(header)
        root.addWidget(self.path_label)
        root.addWidget(self.scroll, 1)
        root.addWidget(self.empty_label, 1)
        parent.installEventFilter(self)
        self.hide()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(480, max(360, round(parent.width() * 0.46)))
        self.setGeometry(parent.width() - width, 0, width, parent.height())

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._sync_geometry()
        return super().eventFilter(watched, event)

    def open_directory(self, value: str) -> None:
        directory = Path(str(value or "")).expanduser()
        paths: list[Path] = []
        if directory.is_dir():
            try:
                candidates = sorted(
                    path for path in directory.iterdir()
                    if path.is_file() and path.suffix.lower() in CUSTOM_ICON_SUFFIXES
                )
                paths = [path for path in candidates if QImageReader(str(path)).canRead()]
            except OSError:
                paths = []
        self.path_label.setText(str(directory))
        self.path_label.setToolTip(str(directory))
        self.count_label.setText(f"{len(paths)} 张图片")
        self.flow.set_paths(paths)
        self.scroll.setVisible(bool(paths))
        self.empty_label.setVisible(not paths)
        self._sync_geometry()
        self.show()
        self.raise_()
        QTimer.singleShot(0, self.flow._sync_height)

class ResourcePathPicker(QWidget):
    """Absolute-path field with a native file or directory chooser."""

    def __init__(
        self, value: str, *, directory: bool = False,
        name_filter: str = IMAGE_NAME_FILTER, image_preview: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.directory = bool(directory)
        self.name_filter = name_filter
        self.edit = QLineEdit(self)
        self.edit.setMinimumWidth(250)
        self.edit.setText(str(value))
        self.button = QPushButton("选择…", self)
        self.button.setFixedWidth(66)
        self.button.clicked.connect(self.choose)
        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        path_row.addWidget(self.edit, 1)
        self.preview_button = (
            QPushButton("预览", self) if self.directory and image_preview else None
        )
        if self.preview_button is not None:
            self.preview_button.setIcon(vector_widget_icon(self, "screen", 14))
            self.preview_button.clicked.connect(self._open_preview)
            path_row.addWidget(self.preview_button)
        path_row.addWidget(self.button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(path_row)
        self.image_preview = None

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

    def _open_preview(self) -> None:
        host = self.window()
        drawer = host.findChild(ImagePreviewDrawer, "imagePreviewDrawer")
        if drawer is None:
            drawer = ImagePreviewDrawer(host)
        drawer.open_directory(self.text())

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
    color = QColor("#a8adb4" if _widget_dark(widget) else "#62676d") if widget.isEnabled() else QColor("#aeb2b7")
    painter.setPen(QPen(color, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    center_x = widget.width() - 10.0
    offset = 1.8 if down else -1.8
    painter.drawLine(QPointF(center_x - 2.6, center_y - offset), QPointF(center_x, center_y + offset))
    painter.drawLine(QPointF(center_x, center_y + offset), QPointF(center_x + 2.6, center_y - offset))

SETTINGS_POPUP_OBJECT_NAME = "SettingsPopup"

SETTINGS_POPUP_STYLESHEET = """
QMenu#SettingsPopup {
    background: #ffffff;
    color: #202020;
    border: 1px solid #d8d8d8;
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
}
QMenu#SettingsPopup::item {
    min-height: 22px;
    padding: 4px 28px 4px 12px;
    border-radius: 7px;
}
QMenu#SettingsPopup::item:selected { background: #eeeeee; }
QMenu#SettingsPopup::indicator { width: 0; height: 0; }
"""

_DARK_POPUP_OVERRIDE = """
QMenu#ModernSelectPopup { background: #2a2a30; color: #e4e4e9; border: 1px solid #45454f; }
QMenu#ModernSelectPopup::item { color: #e4e4e9; }
QMenu#ModernSelectPopup::item:selected { background: #3a3a46; }
"""

def settings_popup_stylesheet(widget: QWidget | None = None) -> str:
    style = SETTINGS_POPUP_STYLESHEET
    if _widget_dark(widget):
        style += _DARK_POPUP_OVERRIDE.replace("ModernSelectPopup", SETTINGS_POPUP_OBJECT_NAME)
    return style

def configure_settings_action_popup(menu: QMenu) -> QMenu:
    """Apply the one shared settings popover surface to any menu."""
    menu.setObjectName(SETTINGS_POPUP_OBJECT_NAME)
    menu.setStyleSheet(settings_popup_stylesheet(menu))
    menu.setProperty("menuStyle", "modern")
    menu.setProperty("settingsPopup", True)
    return menu

class SettingsPopupAction(QAction):
    """Logical checked state painted by SettingsPopupMenu on the trailing edge."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._settings_checkable = False
        self._settings_checked = False

    def setCheckable(self, checkable: bool) -> None:  # noqa: N802
        self._settings_checkable = bool(checkable)
        self.changed.emit()

    def isCheckable(self) -> bool:  # noqa: N802
        return self._settings_checkable

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self._settings_checked = bool(checked)
        self.changed.emit()

    def isChecked(self) -> bool:  # noqa: N802
        return self._settings_checked

class SettingsPopupMenu(QMenu):
    """Shared menu surface for selectors and commands, including right checks."""

    def addAction(self, *args):  # noqa: N802
        if len(args) == 1 and isinstance(args[0], str):
            action = SettingsPopupAction(args[0], self)
            super().addAction(action)
            return action
        return super().addAction(*args)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(
            QColor("#a0a6b0" if _widget_dark(self) else "#454545"),
            1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
        ))
        for action in self.actions():
            if not action.isVisible() or not action.isCheckable() or not action.isChecked():
                continue
            rect = self.actionGeometry(action)
            x = rect.right() - 17.0
            y = rect.center().y()
            painter.drawLine(QPointF(x - 4, y), QPointF(x - 1, y + 3))
            painter.drawLine(QPointF(x - 1, y + 3), QPointF(x + 5, y - 5))

class SettingsMenuButton(QPushButton):
    """Command-menu trigger with the same anchor and chevron as ModernSelect."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._popup_menu: QMenu | None = None
        self.setProperty("settingsMenuButton", True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self.showPopup)

    def setPopupMenu(self, menu: QMenu) -> None:  # noqa: N802
        self._popup_menu = configure_settings_action_popup(menu)

    def popupMenu(self) -> QMenu | None:  # noqa: N802
        return self._popup_menu

    def showPopup(self) -> None:  # noqa: N802
        if self._popup_menu is None or not self.isEnabled():
            return
        self._popup_menu.setMinimumWidth(self.width())
        self._popup_menu.popup(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        _draw_chevron(self, self.height() / 2.0, down=True)

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

    def popupStyleSheet(self) -> str:  # noqa: N802
        return settings_popup_stylesheet(self)

    def showPopup(self) -> None:  # noqa: N802
        self.aboutToShowPopup.emit()
        popup = self._popup
        if popup is None:
            popup = configure_settings_action_popup(SettingsPopupMenu(self))
            self._popup = popup
        else:
            # Reuse one native popup instead of retaining a new child QMenu on
            # every open. Deleting on close is unsafe here because Qt performs
            # that deletion asynchronously while Python still owns the wrapper.
            popup.clear()
        popup.setMinimumWidth(self.width())
        for index, (text, _) in enumerate(self._items):
            action = popup.addAction(text)
            action.setCheckable(True)
            action.setChecked(index == self._index)
            action.triggered.connect(
                lambda _checked=False, index=index: self.setCurrentIndex(index)
            )
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
        dark = _widget_dark(self)
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
        label.setWordWrap(True)
        hint_label = QLabel(hint, self)
        hint_label.setObjectName("settingHint")
        hint_label.setWordWrap(True)
        label.setBuddy(control)
        if not control.accessibleName():
            control.setAccessibleName(title)
        if hint and not control.accessibleDescription():
            control.setAccessibleDescription(hint)
        if stacked:
            row = QVBoxLayout(self)
            row.setContentsMargins(16, 10, 16, 10)
            row.setSpacing(0)
            row.addWidget(label)
            row.addWidget(hint_label)
            row.addSpacing(7)
            row.addWidget(control)
            self._responsive_layout = None
            self.setProperty("responsiveStacked", True)
        else:
            row = QHBoxLayout(self)
            row.setContentsMargins(16, 10, 16, 10)
            row.setSpacing(18)
            copy = QVBoxLayout()
            copy.setContentsMargins(0, 0, 0, 0)
            copy.setSpacing(2)
            copy.addWidget(label)
            copy.addWidget(hint_label)
            copy.addStretch(1)
            row.addLayout(copy, 1)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._responsive_layout = row
            self.setProperty("responsiveStacked", False)
        self.label = label
        self.hint_label = hint_label
        self.control = control

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        layout = self._responsive_layout
        if layout is None:
            return
        copy_minimum = min(320, max(180, self.label.sizeHint().width()))
        control_width = max(
            self.control.minimumWidth(), self.control.sizeHint().width()
        )
        preferred_inline_width = getattr(
            self.control, "preferred_inline_width", None
        )
        if callable(preferred_inline_width):
            control_width = max(control_width, preferred_inline_width())
        required_width = 32 + copy_minimum + 18 + control_width
        stacked = self.width() < required_width
        if self.property("responsiveStacked") is stacked:
            return
        self.setProperty("responsiveStacked", stacked)
        layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if stacked
            else QBoxLayout.Direction.LeftToRight
        )
        layout.setSpacing(7 if stacked else 18)
        layout.setAlignment(
            self.control,
            Qt.AlignmentFlag(0)
            if stacked
            else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.updateGeometry()

class ResponsiveActionRow(QWidget):
    """Keep a primary control and adjacent actions usable under localization."""

    def __init__(self, primary: QWidget, actions: list[QWidget], parent=None):
        super().__init__(parent)
        self.primary = primary
        self.actions = list(actions)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._mode = None
        self._reflow("compact")

    def _effective_width(self, widget: QWidget) -> int:
        return max(
            widget.minimumWidth() or widget.minimumSizeHint().width(),
            widget.sizeHint().width(),
        )

    def preferred_inline_width(self) -> int:
        widths = [self._effective_width(self.primary), *(
            self._effective_width(action) for action in self.actions
        )]
        return sum(widths) + self.grid.horizontalSpacing() * (len(widths) - 1)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        widgets = [self.primary, *self.actions]
        width = max(
            (widget.minimumWidth() or widget.minimumSizeHint().width() for widget in widgets),
            default=0,
        )
        heights = [
            widget.minimumHeight() or widget.minimumSizeHint().height()
            for widget in widgets
        ]
        if self._mode == "inline":
            height = max(heights, default=0)
        elif self._mode == "stacked":
            action_height = max(heights[1:], default=0)
            height = heights[0] + (
                self.grid.verticalSpacing() + action_height if self.actions else 0
            )
        else:
            height = sum(heights) + self.grid.verticalSpacing() * max(0, len(heights) - 1)
        return QSize(width, height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        action_row_width = sum(
            max(action.minimumWidth(), action.sizeHint().width())
            for action in self.actions
        ) + self.grid.horizontalSpacing() * max(0, len(self.actions) - 1)
        if self.width() >= self.preferred_inline_width():
            mode = "inline"
        elif self.width() >= action_row_width:
            mode = "stacked"
        else:
            mode = "compact"
        self._reflow(mode)

    def _reflow(self, mode: str) -> None:
        if self._mode == mode:
            return
        self._mode = mode
        self.setProperty("responsiveMode", mode)
        self.setProperty("responsiveStacked", mode != "inline")
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(len(self.actions) + 1):
            self.grid.setColumnStretch(column, 0)
        if mode == "compact":
            self.grid.addWidget(self.primary, 0, 0)
            for row, action in enumerate(self.actions, start=1):
                self.grid.addWidget(action, row, 0)
        elif mode == "stacked":
            self.grid.addWidget(self.primary, 0, 0, 1, max(1, len(self.actions)))
            for index, action in enumerate(self.actions):
                self.grid.addWidget(action, 1, index)
        else:
            self.grid.addWidget(self.primary, 0, 0)
            for index, action in enumerate(self.actions, start=1):
                self.grid.addWidget(action, 0, index)
        self.grid.setColumnStretch(0, 1)
        self.updateGeometry()

class ResponsiveToggleActionRow(QWidget):
    """Reflow a toggle, an expanding detail editor, and one trailing action."""

    def __init__(self, toggle: QWidget, detail: QWidget, action: QWidget, parent=None):
        super().__init__(parent)
        self.toggle = toggle
        self.detail = detail
        self.action = action
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._stacked = None
        self._reflow(True)

    def preferred_inline_width(self) -> int:
        return (
            self.toggle.sizeHint().width()
            + self.detail.sizeHint().width()
            + self.action.sizeHint().width()
            + self.grid.horizontalSpacing() * 2
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Localized controls need breathing room; reflow before they touch the
        # card edge rather than waiting for literal minimum-size overflow.
        self._reflow(self.width() < self.preferred_inline_width() + 48)

    def _reflow(self, stacked: bool) -> None:
        if self._stacked is stacked:
            return
        self._stacked = stacked
        self.setProperty("responsiveStacked", stacked)
        while self.grid.count():
            self.grid.takeAt(0)
        if stacked:
            self.grid.addWidget(self.toggle, 0, 0)
            self.grid.addWidget(self.action, 0, 1, Qt.AlignmentFlag.AlignRight)
            self.grid.addWidget(self.detail, 1, 0, 1, 2)
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnStretch(1, 0)
        else:
            self.grid.addWidget(self.toggle, 0, 0)
            self.grid.addWidget(self.detail, 0, 1)
            self.grid.addWidget(self.action, 0, 2)
            self.grid.setColumnStretch(0, 0)
            self.grid.setColumnStretch(1, 1)
            self.grid.setColumnStretch(2, 0)
        self.updateGeometry()

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

class SettingsDisclosureHeader(QPushButton):
    """QSS-owned one-level disclosure without platform-native tool chrome."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("advancedSectionToggle")
        self.setText(title)
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"展开{title}")
        self.chevron = QLabel("›", self)
        self.chevron.setObjectName("disclosureChevron")
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.toggled.connect(self._sync_chevron)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(240, 42)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.chevron.setGeometry(max(0, self.width() - 36), 0, 28, self.height())

    def _sync_chevron(self, expanded: bool) -> None:
        self.chevron.setText("⌄" if expanded else "›")

class SettingsSection(QWidget):
    def __init__(self, title: str, rows: list[SettingRow], parent=None, *, advanced: bool = False):
        super().__init__(parent)
        self.advanced = advanced
        self.rows = list(rows)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        if advanced:
            self.toggle = SettingsDisclosureHeader(title, self)
            layout.addWidget(self.toggle)
        else:
            self.toggle = None
            label = QLabel(title, self)
            label.setObjectName("sectionTitle")
            layout.addWidget(label)
        self.card = SettingsCard(rows, self)
        layout.addWidget(self.card)
        if self.toggle is not None:
            self.card.setVisible(False)
            self.toggle.toggled.connect(self._set_expanded)

    def refresh_dependency_visibility(self) -> None:
        """Hide a section when every row is suppressed by a parent setting."""
        self.setVisible(any(
            all(getattr(row, "_visibility_dependencies", {}).values())
            for row in self.rows
        ))

    def _set_expanded(self, expanded: bool) -> None:
        self.card.setVisible(expanded)
        if self.toggle is not None:
            self.toggle.setAccessibleName(
                f"{'收起' if expanded else '展开'}{self.toggle.text()}"
            )
            self.toggle.update()

class _CurrentPageStack(QStackedWidget):
    """Do not let a hidden tab impose its minimum width on the active task."""

    def sizeHint(self) -> QSize:  # noqa: N802
        current = self.currentWidget()
        return current.sizeHint() if current is not None else QSize()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        current = self.currentWidget()
        if current is None:
            return QSize()
        hint = current.minimumSizeHint()
        return QSize(0, hint.height())

class SettingsTabContainer(QWidget):
    """Keyboard-accessible in-page tabs for peer tasks within one domain."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsTaskTabs")
        self._keys: list[str] = []
        self._labels: list[str] = []
        self._buttons: list[QPushButton] = []
        self.tab_bar = QWidget(self)
        self.tab_bar.setObjectName("settingsTaskTabBar")
        self.tab_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.tab_layout = QHBoxLayout(self.tab_bar)
        self.tab_layout.setContentsMargins(3, 3, 3, 3)
        self.tab_layout.setSpacing(2)
        self.tab_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.stack = _CurrentPageStack(self)
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self.tab_bar)
        layout.addWidget(self.stack)

    def addTab(self, key: str, label: str, page: QWidget) -> None:  # noqa: N802
        key = str(key)
        button = QPushButton(str(label), self.tab_bar)
        button.setObjectName("settingsTaskTab")
        button.setProperty("navigationStyle", "plugin")
        button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setAccessibleName(f"切换到{label}")
        index = len(self._keys)
        button.clicked.connect(lambda _checked=False, index=index: self.setCurrentIndex(index))
        self._keys.append(key)
        self._labels.append(str(label))
        self._buttons.append(button)
        self.tab_layout.addWidget(button)
        self.stack.addWidget(page)
        if index == 0:
            button.setChecked(True)
            self.stack.setCurrentIndex(0)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def labels(self) -> tuple[str, ...]:
        return tuple(self._labels)

    def currentKey(self) -> str:  # noqa: N802
        index = self.stack.currentIndex()
        return self._keys[index] if 0 <= index < len(self._keys) else ""

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        self._buttons[index].setChecked(True)
        self.stack.updateGeometry()
        self.stack.currentWidget().updateGeometry()
        self.updateGeometry()

    def setCurrentKey(self, key: str) -> None:  # noqa: N802
        if key in self._keys:
            self.setCurrentIndex(self._keys.index(key))

    def key_for_descendant(self, widget: QWidget) -> str:
        for index, key in enumerate(self._keys):
            if self.stack.widget(index).isAncestorOf(widget) or self.stack.widget(index) is widget:
                return key
        return ""

    def activate_for_descendant(self, widget: QWidget) -> bool:
        key = self.key_for_descendant(widget)
        if not key:
            return False
        self.setCurrentKey(key)
        return True

class _SettingsPageShell(QWidget):
    """Keep the fixed page header aligned with the centered scroll content."""

    def __init__(self, content_max_width: int, parent=None):
        super().__init__(parent)
        self.content_max_width = int(content_max_width)
        self.heading_host: QWidget | None = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.heading_host is not None:
            available = max(0, self.width() - 30 - 28)
            self.heading_host.setFixedWidth(min(self.content_max_width, available))

def _line_edit(text: str = "", *, password: bool = False, width: int = 240) -> QLineEdit:
    edit = QLineEdit(text)
    edit.setMinimumWidth(width)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit

class QuickLaunchItemRow(QWidget):
    """Two-line quick-launch row; the owning list keeps selection and drag."""

    def __init__(self, name: str, detail: str, parent=None):
        super().__init__(parent)
        self.setObjectName("quickLaunchItemRow")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.name_label = QLabel(name, self)
        self.name_label.setObjectName("quickLaunchName")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail_label = QLabel(detail, self)
        self.detail_label.setObjectName("quickLaunchDetail")
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail_label.setToolTip(detail)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        copy = QVBoxLayout(self)
        copy.setContentsMargins(62, 5, 10, 5)
        copy.setSpacing(1)
        copy.addWidget(self.name_label)
        copy.addWidget(self.detail_label)

class QuickLaunchEditor(QWidget):
    """Small application picker persisted into the modern menu."""

    changed = Signal()

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
        self.list.setSpacing(2)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.list.viewport().installEventFilter(self)
        self.count_label = QLabel("0 个快捷项", self)
        self.count_label.setObjectName("quickLaunchCount")
        self.empty_label = QLabel("还没有快捷启动项，可从“添加”开始。", self)
        self.empty_label.setObjectName("quickLaunchEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setFixedHeight(64)
        self.add_button = SettingsMenuButton("添加", self)
        self.add_button.setIcon(vector_widget_icon(self, "add", 15))
        self.add_menu = configure_settings_action_popup(SettingsPopupMenu(self.add_button))
        self.choose_application_action = self.add_menu.addAction("选择应用…")
        self.add_default_action = self.add_menu.addAction("添加默认浏览器")
        self.choose_application_action.triggered.connect(self._choose_application)
        self.add_default_action.triggered.connect(self._add_default_browser)
        self.add_button.setPopupMenu(self.add_menu)
        self.remove_button = QPushButton("移除所选", self)
        self.remove_button.setIcon(vector_widget_icon(self, "remove", 15))
        self.remove_button.clicked.connect(self._remove_checked)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(7)
        toolbar.addWidget(self.count_label)
        toolbar.addStretch(1)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self.list)
        layout.addWidget(self.empty_label)
        for item in apps:
            self.add_app(item)
        self._sync_content_height()

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
        item = QListWidgetItem(icon, "")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, app)
        item.setToolTip(app["path"] or "使用系统默认浏览器")
        item.setSizeHint(QSize(0, 52))
        self.list.addItem(item)
        detail = "系统默认浏览器" if app["kind"] == "default_browser" else app["path"]
        self.list.setItemWidget(item, QuickLaunchItemRow(app["name"], detail, self.list))
        self._sync_content_height()

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
        self._sync_content_height()

    def _add_default_browser(self) -> None:
        if not any(item.get("kind") == "default_browser" for item in self.apps()):
            self.add_app(DEFAULT_QUICK_LAUNCH_APPS[0])

    def _sync_content_height(self) -> None:
        count = self.list.count()
        self.count_label.setText(f"{count} 个快捷项")
        self.empty_label.setVisible(count == 0)
        self.list.setVisible(count > 0)
        if count:
            self.list.setFixedHeight(min(226, count * 56 + 10))
        self.changed.emit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            watched is self.list.viewport()
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = event.position().toPoint()
            item = self.list.itemAt(point)
            if item is not None:
                row = self.list.visualItemRect(item)
                if point.x() <= row.left() + 36:
                    checked = item.checkState() == Qt.CheckState.Checked
                    item.setCheckState(
                        Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
                    )
                    return True
        return super().eventFilter(watched, event)
