# -*- coding: utf-8 -*-
"""Modern settings 控件库：可复用的设置控件、装配脚手架及配套常量/helper。

从 modern_settings_dialog.py 纯机械搬移（只搬代码 + 改 import，不改逻辑/行为/样式）。
"""
from __future__ import annotations

import os
from pathlib import Path

import shiboken6

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
    THEMED_ICON_REQUEST,
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
    ("语音", "sound"),
    ("文件识别", "file"),
    ("更新", "update"),
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

#: 内置点击音效包：id 必须与 ``pet/config.py::_CLICK_SOUND_BUILTINS`` 一致
#: （``default`` = ``assets/sounds/click.wav``，其余 id 即 ``assets/sounds/<id>``）。
BUILTIN_CLICK_PACKS = (
    ("default", "默认包"),
    ("duck", "小黄鸭包"),
    ("Deep!Seek!", "Deep!Seek!"),
    ("to鲸wu", "to鲸wu"),
)


class ClickSoundPackPicker(QWidget):
    """点击音效包选择器（内置默认/小黄鸭/自定义单文件/自定义文件夹）。"""

    changed = Signal()

    def __init__(self, pack: dict | None = None, parent=None):
        super().__init__(parent)
        self.mode_select = ModernSelect(self, width=170)
        for pack_id, label in BUILTIN_CLICK_PACKS:
            self.mode_select.addItem(label, "builtin:" + pack_id)
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
            key = "builtin:" + pack_id
            if self.mode_select.findData(key) < 0:
                key = "builtin:default"
            self.mode_select.setCurrentData(key)
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
        self.close_button.setIcon(vector_widget_icon(self.close_button, "exit", 14))
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
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.path_label.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
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
        # 抽屉销毁时移除事件过滤器，杜绝宿主持悬空 QObject*（崩溃点漂移根因之一）。
        self.destroyed.connect(self._release_host_filter)
        self.hide()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(480, max(360, round(parent.width() * 0.46)))
        self.setGeometry(parent.width() - width, 0, width, parent.height())

    def _sync_flow_height(self) -> None:
        """0ms 定时回调：flow 高度同步（isValid 守卫，避免延迟回调撞上已销毁对象）。"""
        flow = self.flow
        if shiboken6.isValid(flow):
            flow._sync_height()

    def _release_host_filter(self, _obj=None) -> None:
        """抽屉销毁时从宿主移除事件过滤器，杜绝宿主持悬空 QObject*。

        用 parentWidget() 而非实例属性取值：teardown 期间 Shiboken 可能重包装
        C++ 对象（Python 侧实例属性丢失），parentWidget() 是 C++ 方法、始终可用。
        """
        host = self.parentWidget()
        if host is None:
            return
        try:
            if shiboken6.isValid(host):
                host.removeEventFilter(self)
        except RuntimeError:
            pass  # 宿主已随 Qt C++ 侧销毁：无需再移除

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
        # 以 self 为 context 的 singleShot：抽屉销毁时 Qt 自动取消，不再在已删对象上回调。
        QTimer.singleShot(0, self, self._sync_flow_height)

class ResourcePathPicker(QWidget):
    """Absolute-path field with a native file or directory chooser."""

    def __init__(
        self, value: str, *, directory: bool = False,
        name_filter: str = IMAGE_NAME_FILTER, image_preview: bool = False,
        dialog_title: str | None = None, parent=None,
    ):
        super().__init__(parent)
        self.directory = bool(directory)
        self.name_filter = name_filter
        # 选择对话框标题：默认沿用图片语义（既有调用方不变），非图片用途可覆盖
        # （例如「音乐播放器程序」选 .exe 时不该写「选择图片」）。
        self.dialog_title = str(
            dialog_title or ("选择图片目录" if self.directory else "选择图片")
        )
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
            self.preview_button.setIcon(vector_widget_icon(self.preview_button, "screen", 14))
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
            selected = QFileDialog.getExistingDirectory(self, self.dialog_title, start)
        else:
            selected, _ = QFileDialog.getOpenFileName(self, self.dialog_title, start, self.name_filter)
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
        # 置灰项（能力暂不可用，如非 Windows 的「桌面端界面」）：只影响弹窗里
        # 该项能否被点中。自绘文本区永远是当前值，调用方保证当前值不是置灰项。
        self._disabled: set[int] = set()
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
        self._disabled.clear()
        self._index = -1
        self.setText("")
        self.update()

    def setItemDisabled(self, index: int, disabled: bool = True) -> None:  # noqa: N802
        """置灰单项：弹窗里点不动（平台能力暂不可用，见 docs/SETTINGS-CHANGE-GATES.md §4）。"""
        if not 0 <= index < len(self._items):
            return
        if disabled:
            self._disabled.add(index)
        else:
            self._disabled.discard(index)

    def isItemDisabled(self, index: int) -> bool:  # noqa: N802
        return index in self._disabled

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

    def suggestedWidth(self) -> int:  # noqa: N802
        """装得下最长项文案所需的控件宽度。

        自绘文本区不带省略号（``paintEvent`` 直接 ``drawText`` 到
        ``width() - 34``：左内缩 10 + 右侧 24 留给 chevron），宽度不够就**裁字**。
        调用方加长下拉文案时用本方法取宽度，别再手算固定值。"""
        widest = max(
            (self.fontMetrics().horizontalAdvance(text) for text, _ in self._items),
            default=0,
        )
        return widest + 34

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
            action.setEnabled(index not in self._disabled)
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
        # 「已修改」标记 + 单项「改回默认」入口（设置页第三刀）。默认态两者都收起，行的
        # 呈现与改动前逐位一致；样式全部复用既有令牌（标记走 settingHint 的小字、按钮走
        # 通用 QPushButton），不新增 QSS 面。谁点亮由对话框判定，本类只负责呈现。
        self.modified_mark = QLabel("· 已修改", self)
        self.modified_mark.setObjectName("settingHint")
        self.modified_mark.hide()
        # 作用域标注（「影响所有桌宠」这类）：从灰说明的末尾拆出来，**挂在标签这一行的右边**。
        # 它与说明那句是两回事——说明讲"这一项做什么"，标注讲"改了谁受影响"，粘在一句话的
        # 末尾读起来像说明的一部分（见 ``set_scope_note``）。为什么在同一行而不是独占一行：
        # 它只值几个字，独占一行会让每一行的行高多出一整行——常规域带标注的那一行，800 宽下
        # 页面就从"装得下"变成"要滚"，页头重新长出跳转条（短页不摆的判据被自己顶翻了）。
        # **不换行（``setWordWrap(False)``）是必须的**，不是省事：同一行里有两个可换行标签时
        # Qt 的 ``QLayout.heightForWidth`` 会退化成按当前几何求和（实测标签行量出 96px，而
        # 标签自己只要 13px）——页内标签容器取各页的最大值，那份虚高会算进"内容超不超视口"，
        # 于是从未显示过的隐藏标签页也能把整域的页高抬起来（实测 +103px）。只留标签一个
        # 可换行项后判据回到标签自己。样式复用 settingHint 的小字，不新增 QSS 面；
        # 前面那枚间隔点只是为了不让「随桌宠启动 dsh 服务」和整句「此设置影响所有桌宠。」连成一串。
        self.scope_note = QLabel("", self)
        self.scope_note.setObjectName("settingHint")
        self.scope_note.setWordWrap(False)
        self.scope_note.hide()
        self.scope_separator = QLabel("·", self)
        self.scope_separator.setObjectName("settingHint")
        self.scope_separator.hide()
        self.default_reset_button = QPushButton("改回默认", self)
        self.default_reset_button.setObjectName("settingDefaultReset")
        self.default_reset_button.setAutoDefault(False)
        self.default_reset_button.setDefault(False)
        self.default_reset_button.setToolTip(
            "把这一项改回默认值。保存时机跟这一项自己的语义一致：即时生效的开关会立刻"
            "写入配置，其余项随设置窗关闭保存。"
        )
        # 一页里有几十个同名按钮，读屏必须能说清是改哪一项（Tab 到才听得懂）
        self.default_reset_button.setAccessibleName(f"把「{title}」改回默认值")
        self.default_reset_button.hide()
        title_line = QHBoxLayout()
        title_line.setContentsMargins(0, 0, 0, 0)
        title_line.setSpacing(6)
        title_line.addWidget(label)
        title_line.addWidget(self.scope_separator)
        title_line.addWidget(self.scope_note)
        title_line.addStretch(1)
        mark_line = QHBoxLayout()
        mark_line.setContentsMargins(0, 0, 0, 0)
        mark_line.setSpacing(8)
        mark_line.addWidget(self.modified_mark)
        mark_line.addWidget(self.default_reset_button)
        mark_line.addStretch(1)
        if stacked:
            row = QVBoxLayout(self)
            row.setContentsMargins(16, 10, 16, 10)
            row.setSpacing(0)
            row.addLayout(title_line)
            row.addWidget(hint_label)
            row.addLayout(mark_line)
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
            copy.addLayout(title_line)
            copy.addWidget(hint_label)
            copy.addLayout(mark_line)
            copy.addStretch(1)
            row.addLayout(copy, 1)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._responsive_layout = row
            self.setProperty("responsiveStacked", False)
        self.label = label
        self.hint_label = hint_label
        self.control = control

    def set_scope_note(self, note: str) -> None:
        """给本行挂上/摘掉作用域标注（「（影响所有桌宠）」这类）。

        **为什么要从灰说明里拆出来**：这句原本拼在说明的末尾
        （「登录系统后自动启动桌宠。（影响所有桌宠）」），读起来仍像在讲"这一项做什么"，
        而它讲的是**改了谁受影响**——两件事该分开。拆法：

        - 说明里**已经带着这句**时（文案表里逐字写着，如 ``STREAM_CAPTURE_RESTART_HINT``、
          ``HARNESS_TARGET_HINT_*``）：从说明末尾削掉，挪到标注里。文案表因此仍是唯一
          来源，不必在别处再抄一份。
        - 说明里没有这句时（overlay 窗口级键是运行期按拓扑补的）：只挂标注。

        **呈现位置是标签那一行的右边**（构造期就摆好，这里只切显隐；标注本身不换行，
        理由见构造期那段注释）：独占一行会让每个带标注的行多占一整行——常规域 800 宽下
        页面因此从"装得下"变"要滚"，页头重新长出跳转条。

        **可访问性与搜索不许丢**：``accessibleDescription`` 保持"说明 + 标注"的全文
        （它在构造期就从 hint 取过一次，删说明末尾时不能跟着丢），搜索索引读的也是
        这一份（见 ``settings_search._row_text``）。``accessibleName`` 不动。

        ``note`` 传空串等于摘掉标注（说明不动，摘不到原文就不还原——调用方只在
        "本行本来就没有这句"时才该传空）。
        """
        note = str(note or "")
        text = self.hint_label.text()
        if note and text.rstrip().endswith(note):
            self.hint_label.setText(text.rstrip()[: -len(note)].rstrip())
        self.scope_note.setText(note)
        self.scope_note.setVisible(bool(note))
        self.scope_separator.setVisible(bool(note))
        control = self.control
        if control is None:
            return
        description = control.accessibleDescription()
        if not note or note in description:
            return
        control.setAccessibleDescription(f"{description}{note}" if description else note)

    def set_default_state(self, default_text: str | None, *, can_reset: bool) -> None:
        """切换本行的「已修改」呈现；``default_text=None`` 表示当前值就是默认值。

        基线 hint 取「当前文本去掉上一次追加的默认值行」，而不是构造时那份原文：
        别的入口会往 hint 尾部追加说明（子宠页的范围说明见
        ``settings_pet_controls._apply_window_scope_rows``），写死原文会把它们抹掉。
        每次刷新都重取基线，外部改写与这里的增删就不会互相踩。
        依赖隐藏不做可见性记账：标记与按钮各自 ``setVisible``，父行不可见时它们自然
        不呈现，父行重新显示后标记跟着回来（依赖行"改过没有"这件事一直记着）。
        """
        modified = default_text is not None
        base_hint = self.hint_label.text().split("默认：", 1)[0].rstrip("\n")
        self.hint_label.setText(
            base_hint if not modified else f"{base_hint}\n默认：{default_text}"
        )
        self.modified_mark.setVisible(modified)
        self.default_reset_button.setVisible(modified and can_reset)

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
    def __init__(self, rows: list[QWidget], parent=None):
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

def _section_description_label(text: str, parent: QWidget) -> QLabel:
    """组标题下的一句人话（``sectionDescription``）：文案在调用方，这里只管呈现。"""
    label = QLabel(str(text), parent)
    label.setObjectName("sectionDescription")
    label.setWordWrap(True)
    label.setVisible(bool(text))
    return label


class SettingsSection(QWidget):
    def __init__(self, title: str, rows: list[QWidget], parent=None, *, advanced: bool = False,
                 description: str = ""):
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
        # 描述是标题的兄弟而不是卡片的一部分：高级组收起时它照常看得见（否则
        # 「这里是什么」要先展开才知道）。空串＝整条收起，与改动前逐位一致。
        self.description_label = _section_description_label(description, self)
        layout.addWidget(self.description_label)
        self.card = SettingsCard(rows, self)
        layout.addWidget(self.card)
        if self.toggle is not None:
            self.card.setVisible(False)
            self.toggle.toggled.connect(self._set_expanded)

    def set_description(self, text: str) -> None:
        self.description_label.setText(str(text))
        self.description_label.setVisible(bool(text))

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
class CollapsibleGroup(QWidget):
    """可折叠分组容器：一个折叠头 + 若干「小标题 + 设置卡」子分组。

    用于把同类设置收进一个可折叠框（例如按事件聚合类别划分的汇报概率门，
    每类里滑块与该类气泡文案行同组），子分组复用 ``SettingsSection`` 以保持
    既有视觉令牌与分隔线行为。
    """

    def __init__(self, title: str, parent=None, *, expanded: bool = False, description: str = ""):
        super().__init__(parent)
        self.setObjectName("collapsibleGroup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.toggle = SettingsDisclosureHeader(title, self)
        layout.addWidget(self.toggle)
        # 与 SettingsSection 同口径：描述常驻在折叠头下面，收起时也看得见。
        self.description_label = _section_description_label(description, self)
        layout.addWidget(self.description_label)
        self.body = QWidget(self)
        self.body.setObjectName("collapsibleGroupBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(14)
        layout.addWidget(self.body)
        self.groups: list[SettingsSection] = []
        self.body.setVisible(expanded)
        self.toggle.setChecked(expanded)
        self.toggle.toggled.connect(self._set_expanded)
        self._sync_accessible_name()

    def add_group(self, title: str, rows: list) -> SettingsSection:
        """追加一个「小标题 + 设置卡」子分组，返回该分组以便后续操作。"""
        section = SettingsSection(title, [row for row in rows if row is not None], self.body)
        self.groups.append(section)
        self.body_layout.addWidget(section)
        return section

    def set_description(self, text: str) -> None:
        self.description_label.setText(str(text))
        self.description_label.setVisible(bool(text))

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(bool(expanded))

    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self._sync_accessible_name()

    def _sync_accessible_name(self) -> None:
        state = "收起" if self.toggle.isChecked() else "展开"
        self.toggle.setAccessibleName(f"{state}{self.toggle.text()}")


def reveal_settings_row(row: SettingRow, page: QWidget) -> None:
    """把搜索命中的行露出来：展开它所在的折叠框，并切到它所在的页内标签。

    两种折叠形态都要管：``CollapsibleGroup`` 是 QWidget 嵌套（判祖先即可）；
    语音报时的后端参数行由 ``voice_chime_tts_panel.TtsBackendPanel`` 折叠——那批行会被
    「语音」域搬进一张平卡，容器活不下来，所以它只在行上挂一个 ``_reveal_fold`` 回调。
    """
    reveal = getattr(row, "_reveal_fold", None)
    if callable(reveal):
        reveal()
    ancestor = row.parentWidget()
    while ancestor is not None and ancestor is not page:
        if isinstance(ancestor, CollapsibleGroup):
            # 命中折叠框内的行：先自动展开，否则搜索结果存在但看不见。
            ancestor.set_expanded(True)
        elif isinstance(ancestor, SettingsSection) and ancestor.advanced:
            # 高级折叠组同理：不展开等于折了搜不到（新老全部 advanced 组一体生效）。
            ancestor.toggle.setChecked(True)
        if isinstance(ancestor, SettingsTabContainer):
            ancestor.activate_for_descendant(row)
            break
        ancestor = ancestor.parentWidget()


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

    #: 当前标签变了（键，不是下标）：页内跳转条按当前标签重建自己要列的区。
    currentKeyChanged = Signal(str)

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
        self.currentKeyChanged.emit(self.currentKey())

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
        self._scroll: QScrollArea | None = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._scroll is None:
            self._scroll = self.findChild(QScrollArea, "settingsScroll")
            if self._scroll is not None:
                # 视口尺寸才是"内容能有多宽"的真值，而页刚装好时视口还没落定——只在
                # 自己的 resizeEvent 里算会量到一个未布局的视口（实测页头塌成 34px）。
                # 盯视口的 Resize：那一刻量到的就是内容当前能用的宽。
                self._scroll.viewport().installEventFilter(self)
        self._sync_heading_width()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if (event.type() == QEvent.Type.Resize and self._scroll is not None
                and watched is self._scroll.viewport()):
            self._sync_heading_width()
        return super().eventFilter(watched, event)

    def _sync_heading_width(self) -> None:
        """页头宽 = min(内容宽上限, 滚动视口宽)。

        取**视口**宽（不是"页宽 − 页边距"）：竖滚动条占掉的那一条不在视口里，内容也就在
        那以外的宽度里居中；页头若按页宽算，在有滚动条的页上会比内容宽出一条。宽度只是
        上限（内容封顶见 CONTENT_READING_MAX_WIDTH），页头跟着同一个数走才是一条契约。
        """
        if self.heading_host is None:
            return
        viewport = self._scroll.viewport().width() if self._scroll is not None else 0
        available = viewport if viewport > 0 else max(0, self.width() - 30 - 28)
        self.heading_host.setFixedWidth(min(self.content_max_width, available))


#: 表单内容的最大阅读宽度（页头标题与滚动内容共用这一个宽度契约）。
#:
#: **只管宽窗**：默认窗口 800×560 下内容宽只有 542px（用不到它），窄窗更用不到
#: （``maximumWidth`` 只封顶、不撑宽，所以窄窗不会被它裁）。它约束的是超宽窗口——
#: 表单是一列"标签 + 右侧控件"，拉到上千像素后一行里的标签与控件隔着一整屏，眼睛
#: 读一行要横跨过去，控件也不再看得出属于哪一行。
#:
#: 取值实测过：把内容宽从 960 一路收到 700，「被迫改成竖排」的行数在十个域里**一个
#: 都没变**（``.scratch/settings-polish2/probe_width.py``）——800 只是让行变短，不会
#: 代价是换布局。例外是「菜单」域：布局编辑器要"编辑区 + 预览"两栏并排，自己在内容
#: 上声明 ``contentMaxWidth=1240``（本常量只做默认值）。
CONTENT_READING_MAX_WIDTH = 800

#: 侧栏宽度令牌（见 references/visual-system.md 的 Layout 表）：默认字体下的固定宽度。
#: 放大字体下按**内容**加宽（见 :class:`SettingsSidebarPane`），不缩字号、不裁文案。
SIDEBAR_MIN_WIDTH = 200

#: 侧栏导航图标与行高：图标按当前主题渲染一次、切主题时重画（见 :func:`refresh_settings_icons`）。
SIDEBAR_ICON_SIZE = 16
SIDEBAR_ROW_HEIGHT = 34

#: 项里的矢量图标请求：``(名字, 尺寸)``。侧栏导航行与快捷启动行都存这一格——列表项不是
#: 控件，没法走 ``themedIconRequest`` 动态属性，只能记在数据角色上。取 ``UserRole`` 之后的
#: 第一格：侧栏项此前不存任何数据，快捷启动项用 ``UserRole`` 存应用字典，都不碰这里。
THEMED_ICON_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def settings_sidebar_item(host, label: str, icon_name: str) -> QListWidgetItem:
    """建一个侧栏导航项：图标按 ``host`` 当前的主题渲染，并把请求记在项上。

    记账是**为换主题准备的**：图标是画出来的位图，``ModernSettingsDialog`` 换主题只换
    QSS，位图不会自己变色。``refresh_settings_icons`` 按这一格重画，所以侧栏行不需要
    各自接线，新增一行也不会漏（见 ``context_menus.icons.THEMED_ICON_REQUEST``）。
    """
    item = QListWidgetItem(vector_widget_icon(host, icon_name, SIDEBAR_ICON_SIZE), label)
    item.setData(THEMED_ICON_ROLE, (str(icon_name), SIDEBAR_ICON_SIZE))
    item.setSizeHint(QSize(0, SIDEBAR_ROW_HEIGHT))
    return item


#: 「保存并退出」的保存时机说明（按钮 tooltip）。写的是**真的会发生的事**：
#: 直接关窗（X / Esc）走的是同一条保存闸门；两个音效开关改一下立刻写盘；写入失败时
#: 对话框会问「继续编辑 / 放弃未保存的改动并关闭」。
#:
#: 为什么是 tooltip 而不是「未保存」标记：表单控件自己持有草稿，全仓库没有「表单里的值
#: 与磁盘不一样」的可靠信号（``Config.has_local_changes`` 比的是 Config 内存与最后一次
#: 成功保存的基线，而编辑期间控件值根本不写进 Config；现有的「· 已修改」标记比的是
#: **出厂默认**，不是盘上现值）。没有真信号就不许伪造一个，于是把保存时机说明白。
SAVE_EXIT_TOOLTIP = (
    "写入设置并关闭窗口。直接关窗（X 或 Esc）同样会保存；"
    "「音效总开关」「点击音效」这类开关改一下就会立刻写盘；"
    "写入失败时会问你是继续编辑，还是放弃未保存的改动并关闭。"
)


def settings_save_exit_button(pane, on_click) -> QPushButton:
    """侧栏底部的「保存并退出」：软盘图标 + 保存时机说明 + 非默认钮。

    外观（图标名字、tooltip、焦点旗标）与接线集中在这里，对话框只留一行——设置窗的行数
    预算按文件约定校准（见 ``tests/test_architecture.py``），控件 chrome 归本模块。
    回车不触发本钮：它是命令而非「默认动作」，误触等于提前落盘并关窗。
    """
    button = QPushButton("保存并退出", pane)
    button.setObjectName("saveAndExit")
    button.setIcon(vector_widget_icon(button, "save", 16))
    button.setToolTip(SAVE_EXIT_TOOLTIP)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.clicked.connect(on_click)
    return button


def refresh_settings_icons(root) -> int:
    """按 ``root``（设置窗）当前的主题重画它里面已渲染过的矢量图标，返回重画枚数。

    **为什么需要这一步**：设置窗的图标是位图，描边色在渲染那一刻烘进像素；换主题时
    QSS 立刻重排、图标不会自己换色。渲染入口把请求记在**消费者**上（``context_menus.icons``），
    这里按同一个名字与尺寸重画一次——因此新增图标不用各自接线，也不会漏掉旧图标。

    三种消费者各自的重画方式：

    - **控件**（按钮、``SettingsMenuButton``…）：``setIcon`` 写回同一个控件（记录就挂在
      消费者自己身上，见 ``_remember_themed_icon``）；
    - **``QLineEdit``**：它没有 ``setIcon``，图标挂在左上角那个 action 上（设置窗里只有
      搜索框这一处，图标是它唯一的 action）；
    - **列表项**（侧栏导航行、快捷启动行）：项不是控件，请求记在 ``THEMED_ICON_ROLE`` 上。

    成本实测（本机，7 次中位，见 ``.scratch/settings-navfix/VERIFICATION.md`` §6）：一次
    调用约 16 ms = 全窗扫 3174 个控件的属性 5 ms + 重画 17 枚 8.5 ms + 侧栏/列表项约 3 ms；
    开窗因此 +24 ms（595 → 619 ms），切一次主题 +21 ms（349 → 370 ms）。不新增系统调用/
    网络/磁盘/线程。
    """
    redrawn = 0
    for host in [root, *root.findChildren(QWidget)]:
        request = host.property(THEMED_ICON_REQUEST)
        if not request:
            continue
        name, size = request
        icon = vector_widget_icon(host, str(name), int(size))
        setter = getattr(host, "setIcon", None)
        if callable(setter):
            setter(icon)
            redrawn += 1
        elif isinstance(host, QLineEdit) and host.actions():
            host.actions()[0].setIcon(icon)
            redrawn += 1
    for view in root.findChildren(QListWidget):
        for index in range(view.count()):
            item = view.item(index)
            request = item.data(THEMED_ICON_ROLE)
            if not request:
                continue
            name, size = request
            item.setIcon(vector_widget_icon(view, str(name), int(size)))
            redrawn += 1
    return redrawn


def sidebar_content_width(layout) -> int:
    """侧栏内容需要的净宽度（不含容器自己的外边距与边框）。

    为什么不能用 ``layout.minimumSize()``：搜索框与导航列表都是撑满列的控件，它们的
    ``sizeHint`` 是 Qt 默认的「十七个字符宽」（实测 269px），拿它当依据会把侧栏顶到
    290px——一条与内容无关的宽度。判据只看**不撑满**的控件（按钮 / 版本号这类按文案
    取宽的），它们才是放大字体下真正会被压到裁字的那几个。
    """
    needed = 0
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if widget is None or widget.isHidden():
            continue
        # 撑满列的控件（导航列表、搜索框）宽度由列决定，不参与"需要多宽"。
        if widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding:
            continue
        needed = max(needed, widget.sizeHint().width())
    return needed


class SettingsSidebarPane(QFrame):
    """设置窗侧栏容器：宽度 = ``max(200, 内容所需)``，且**跟着字体走**。

    200px 是设计令牌（``SIDEBAR_MIN_WIDTH``），但按钮文案在放大字体下会长过它：实测
    1.5 倍字体（20px）时侧栏按钮（「重置本宠设置…」）的 ``sizeHint`` 要 186px，而
    200px 的侧栏内宽只有 175px——文字被省略号吃掉。宽度因此按内容算，默认字体下恒为
    200（逐像素不变）。

    **为什么盯事件而不是只算一次**：字号可能在构造之后才变大（系统/无障碍放大字体、
    截图脚本的 ``--font-scale``）。逐控件 ``setFont`` 不会给本容器发 FontChange，但会让
    父布局收到 ``LayoutRequest``；子控件换了样式表同理（``StyleChange``）。这几类事件任一
    到达就重排一次重算——重算是幂等的，宽度没变时什么都不做，不会自激。

    **重算必须推到下一拍事件循环**：``QDialog.setStyleSheet`` 会在样式重算过程中把
    ``StyleChange`` 派发给每个子控件，在那一刻改自己的固定宽度会重入 Qt 的样式/布局失效
    流程——本机实测直接 **access violation**（``_apply_selected_theme`` → ``setStyleSheet``，
    ``tests/test_settings_reset_defaults.py`` 与 ``test_settings_sidebar_and_chrome.py``
    都能复现）。推到下一拍既避开重入，语义也够用：宽度只在字号与布局落定之后才需要跟着变。
    定时器挂在容器自己身上（child timer），容器销毁时一并销毁，不会回调到已析构的对象。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebarPane")
        self.setFixedWidth(SIDEBAR_MIN_WIDTH)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self._sync_width)

    def event(self, event) -> bool:  # noqa: N802 - Qt API
        if event.type() in (
            QEvent.Type.LayoutRequest,
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
            QEvent.Type.Show,
        ):
            self._schedule_sync()
        return super().event(event)

    def _schedule_sync(self) -> None:
        if not self._sync_timer.isActive():
            self._sync_timer.start()

    def _sync_width(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        margins = layout.contentsMargins()
        # 内容之外还有两笔固定开销：布局的左右外边距（不进 ``contentsRect``），以及容器
        # 自己的边框（QSS 里 ``QFrame#sidebarPane`` 有一条 1px 右边框，实测 200px 的侧栏
        # 内容区只有 175px）。边框按「宽度 − 内容区宽度」量出来，不把 1px 写成字面量。
        border = max(0, self.width() - self.contentsRect().width())
        width = max(
            SIDEBAR_MIN_WIDTH,
            sidebar_content_width(layout) + margins.left() + margins.right() + border,
        )
        if width != self.maximumWidth():
            self.setFixedWidth(width)


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
        self.add_button.setIcon(vector_widget_icon(self.add_button, "add", 15))
        self.add_menu = configure_settings_action_popup(SettingsPopupMenu(self.add_button))
        self.choose_application_action = self.add_menu.addAction("选择应用…")
        self.add_default_action = self.add_menu.addAction("添加默认浏览器")
        self.choose_application_action.triggered.connect(self._choose_application)
        self.add_default_action.triggered.connect(self._add_default_browser)
        self.add_button.setPopupMenu(self.add_menu)
        self.remove_button = QPushButton("移除所选", self)
        self.remove_button.setIcon(vector_widget_icon(self.remove_button, "remove", 15))
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
        # 矢量图标（默认浏览器 / 取不到系统图标时的兜底）要记下请求：列表项不是控件，换主题
        # 时只能靠这一格重画（见 ``refresh_settings_icons``）。系统图标不记——那是别人画的。
        request: tuple[str, int] | None = None
        if app.get("kind") == "default_browser":
            request = ("web", 22)
            icon = vector_widget_icon(self, *request)
            app = {"name": str(app.get("name") or "默认浏览器"), "path": "", "kind": "default_browser"}
        else:
            path = str(app.get("path") or "")
            if not path:
                return
            provider_icon = QFileIconProvider().icon(QFileInfo(path))
            if provider_icon.isNull():
                request = ("application", 17)
                icon = vector_widget_icon(self, *request)
            else:
                icon = fitted_application_icon(provider_icon, 22, self)
            app = {"name": str(app.get("name") or Path(path).stem), "path": path, "kind": "application"}
        item = QListWidgetItem(icon, "")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, app)
        if request is not None:
            item.setData(THEMED_ICON_ROLE, request)
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
