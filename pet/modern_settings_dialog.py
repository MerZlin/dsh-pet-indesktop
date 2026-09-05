# -*- coding: utf-8 -*-
"""Modern-inspired sidebar settings panel used by the modern context menu."""
from __future__ import annotations

import logging
import os
import sys
import threading
import json
from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QClipboard, QFontDatabase, QIcon, QImageReader, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QDialog,
    QColorDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLayout,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import autostart as autostart_mod
from . import catalog
from .agent_link import AgentLinkManager
from .click_sound import warm_click_sound_effects
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
from .context_menus.icons import (
    CUSTOM_ICON_SUFFIXES,
    custom_icon_file_error,
    vector_widget_icon,
)
from .context_menus.quick_launch import fitted_application_icon
from .context_menus.registry import CUSTOM_ICON_CHOICES, MENU_ACTIONS
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset, store_fun_asset
from .menu_layout import (
    load_default_menu_layout,
    materialize_implicit_separators,
    merge_default_menu_actions,
    resolve_menu_layout,
)
from .speech_bubble import BUBBLE_STYLE_PRESETS
from .persona_phrases import phrase_keys, default_phrases
from .persona_template import PARAMETERS, build_persona_template, template_json


# 语言配置页只展示用户能理解的事件名称；内部 key 仍用于保存和渲染。
DIALOGUE_LABELS = {
    "start": "开始工作", "thinking": "思考", "activity.read": "读取文件",
    "activity.search": "搜索或查找", "activity.edit": "编辑代码",
    "activity.run": "运行或测试", "activity.default": "其他工具操作",
    "agent.attention": "需要用户处理", "agent.error": "Agent 出错",
    "agent.missing": "未找到 Agent", "bridge.install.pending": "安装桥接中",
    "bridge.install.success": "桥接安装成功", "bridge.install.failed": "桥接安装失败",
    "bridge.uninstall.failed": "桥接卸载失败", "dsh.writeback.failed": "回写 DSH 失败",
    "approval.command": "审批命令", "approval.tool": "审批工具",
    "approval.generic": "审批提示", "question.empty": "等待选择",
    "question.one": "单个用户问题", "question.many": "多个用户问题",
    "watchdog.warning": "循环检测警告", "watchdog.intervention": "循环检测干预",
    "watchdog.unknown": "循环判断不可用", "rate_limit.one": "单次限流",
    "rate_limit.many": "连续限流", "llm_error.api": "AI 服务错误",
    "done.success": "任务完成",
    "done.attention": "任务暂停待确认", "failure.retry": "重试后失败",
    "failure.tool": "工具执行失败", "failure.generic": "执行失败",
    "control.replan.pending": "重新规划处理中", "control.replan.success": "重新规划完成",
    "control.interrupt.pending": "终止处理中", "control.interrupt.success": "终止完成",
    "control.failed": "控制请求失败", "stuck.reminder": "卡住提醒",
    "pattern.warning": "行为重复警告", "pattern.control": "行为重复干预",
    "balance.loading": "查询余额中", "balance.result": "余额结果",
}

DIALOGUE_PARAMS = {
    "name": "Agent 名称", "command": "待审批命令", "label": "工具标签",
    "body": "问题内容", "count": "数量", "reasons": "判断原因",
    "detail": "错误详情", "text": "显示文本",
    "tool": "原始工具名", "target": "操作目标", "callId": "工具调用 ID",
    "step": "步骤序号", "ok": "是否成功",
}

# 与 persona_template.PARAMETERS 保持同一真相源：调用点注入什么，这里就宣称什么。
DIALOGUE_KEY_PARAMS = dict(PARAMETERS)


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


class MenuLayoutEditor(QWidget):
    """Draft tree editor with a preview derived from the same nodes."""

    changed = Signal()

    def __init__(
        self, layout: dict | None, parent=None, *,
        available_actions=None, enabled_actions=None,
    ):
        super().__init__(parent)
        self.available_actions = frozenset(available_actions or MENU_ACTIONS.ids)
        self.enabled_actions = frozenset(
            enabled_actions if enabled_actions is not None else self.available_actions
        )
        self.tree = QTreeWidget(self)
        self.tree.setObjectName("menuLayoutTree")
        self.tree.setHeaderLabels(["菜单项", "状态", "位置"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(72)
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 92)
        self.tree.setColumnWidth(2, 92)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(18)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setAccessibleName("右键菜单内容与布局")
        self.editor_label = QLabel("菜单结构", self)
        self.editor_label.setObjectName("menuLayoutEditorLabel")
        self.editor_hint = QLabel("拖动表头分隔线调整列宽", self)
        self.editor_hint.setObjectName("menuLayoutEditorHint")
        editor_panel = QFrame(self)
        editor_panel.setObjectName("menuLayoutEditorPanel")
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(10, 10, 10, 10)
        editor_layout.setSpacing(6)
        editor_heading = QHBoxLayout()
        editor_heading.addWidget(self.editor_label)
        editor_heading.addStretch(1)
        editor_heading.addWidget(self.editor_hint)
        editor_layout.addLayout(editor_heading)
        editor_layout.addWidget(self.tree)
        self.preview = QTreeWidget(self)
        self.preview.setObjectName("menuLayoutPreview")
        self.preview.setHeaderHidden(True)
        self.preview.setUniformRowHeights(True)
        self.preview.setIndentation(18)
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview.setAccessibleName("右键菜单实时预览")
        self.preview_label = QLabel("实时菜单预览", self)
        self.preview_label.setObjectName("menuLayoutPreviewLabel")
        preview_panel = QFrame(self)
        preview_panel.setObjectName("menuLayoutPreviewPanel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.preview)

        self.order_button = SettingsMenuButton("排序", self)
        self.order_button.setIcon(vector_widget_icon(self, "edit", 14))
        self.order_menu = configure_settings_action_popup(SettingsPopupMenu(self.order_button))
        self.move_up_action = self.order_menu.addAction("上移")
        self.move_down_action = self.order_menu.addAction("下移")
        self.move_up_action.triggered.connect(lambda: self._move_selected(-1))
        self.move_down_action.triggered.connect(lambda: self._move_selected(1))
        self.order_button.setPopupMenu(self.order_menu)

        self.move_button = SettingsMenuButton("移动到", self)
        self.move_button.setIcon(vector_widget_icon(self, "multi_select", 14))
        self.move_menu = configure_settings_action_popup(SettingsPopupMenu(self.move_button))
        self.move_menu.aboutToShow.connect(self._rebuild_move_menu)
        self.move_button.setPopupMenu(self.move_menu)

        self.submenu_button = SettingsMenuButton("插入", self)
        self.submenu_button.setIcon(vector_widget_icon(self, "add", 14))
        self.submenu_menu = configure_settings_action_popup(SettingsPopupMenu(self.submenu_button))
        self.new_submenu_action = self.submenu_menu.addAction("新建子菜单…")
        self.insert_separator_action = self.submenu_menu.addAction("插入分割线")
        self.new_submenu_action.triggered.connect(self._create_submenu)
        self.insert_separator_action.triggered.connect(self._insert_separator_after_selected)
        self.submenu_button.setPopupMenu(self.submenu_menu)

        self.customize_button = SettingsMenuButton("自定义", self)
        self.customize_button.setIcon(vector_widget_icon(self, "edit", 14))
        self.customize_menu = configure_settings_action_popup(SettingsPopupMenu(self.customize_button))
        self.rename_action = self.customize_menu.addAction("更换别名…")
        self.change_icon_action = self.customize_menu.addAction("选择内置图标…")
        self.choose_icon_file_action = self.customize_menu.addAction("选择图片文件…（最大 5 MB）")
        self.choose_icon_file_action.setToolTip(
            "支持 PNG、JPG、WebP、BMP、GIF、TIFF；作为静态方形菜单图标显示"
        )
        self.icon_display_menu = configure_settings_action_popup(SettingsPopupMenu("图片显示方式", self.customize_menu))
        self.icon_contain_action = self.icon_display_menu.addAction("完整显示")
        self.icon_cover_action = self.icon_display_menu.addAction("裁切填满")
        self.icon_contain_action.setCheckable(True)
        self.icon_cover_action.setCheckable(True)
        self.customize_menu.addMenu(self.icon_display_menu)
        self.restore_presentation_action = self.customize_menu.addAction("恢复默认名称与图标")
        self.rename_action.triggered.connect(self._rename_selected)
        self.change_icon_action.triggered.connect(self._change_selected_icon)
        self.choose_icon_file_action.triggered.connect(self._choose_selected_file_icon)
        self.icon_contain_action.triggered.connect(
            lambda: self._set_selected_file_display("contain")
        )
        self.icon_cover_action.triggered.connect(
            lambda: self._set_selected_file_display("cover")
        )
        self.restore_presentation_action.triggered.connect(self._restore_selected_presentation)
        self.customize_button.setPopupMenu(self.customize_menu)

        self.more_button = SettingsMenuButton("更多", self)
        self.more_button.setIcon(vector_widget_icon(self, "more", 14))
        self.more_menu = configure_settings_action_popup(SettingsPopupMenu(self.more_button))
        self.delete_submenu_action = self.more_menu.addAction("删除所选子菜单…")
        self.delete_separator_action = self.more_menu.addAction("删除所选分割线")
        self.more_menu.addSeparator()
        self.reset_action = self.more_menu.addAction("恢复默认布局")
        self.delete_submenu_action.triggered.connect(self._delete_selected_submenu)
        self.delete_separator_action.triggered.connect(self._delete_selected_separator)
        self.reset_action.triggered.connect(self.reset_default)
        self.delete_submenu_action.setEnabled(False)
        self.delete_separator_action.setEnabled(False)
        self.more_button.setPopupMenu(self.more_menu)

        toolbar = QWidget(self)
        toolbar.setObjectName("menuEditorToolbar")
        self.toolbar = toolbar
        self.toolbar_layout = QGridLayout(toolbar)
        self.toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_layout.setHorizontalSpacing(7)
        self.toolbar_layout.setVerticalSpacing(7)
        self.toolbar_buttons = (
            self.order_button, self.move_button,
            self.submenu_button, self.customize_button, self.more_button,
        )
        self._toolbar_mode = None

        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setObjectName("menuEditorSplit")
        self.split.addWidget(editor_panel)
        self.split.addWidget(preview_panel)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(toolbar)
        box.addWidget(self.split, 1)

        self._preview_refresh_pending = False
        self._pending_empty_submenus: list[QTreeWidgetItem] = []
        self.tree.itemChanged.connect(self._on_changed)
        tree_model = self.tree.model()
        tree_model.rowsMoved.connect(self._schedule_preview_refresh)
        tree_model.rowsInserted.connect(self._schedule_preview_refresh)
        tree_model.rowsRemoved.connect(self._on_tree_rows_removed)
        tree_model.modelReset.connect(self._schedule_preview_refresh)
        self.tree.currentItemChanged.connect(self._sync_command_state)
        self.set_layout(layout or load_default_menu_layout())
        self._update_layout_mode()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_layout_mode()

    def _update_layout_mode(self) -> None:
        if hasattr(self, "split"):
            mode = "wide" if self.width() >= 760 else ("medium" if self.width() >= 600 else "compact")
            compact = mode == "compact"
            if self.property("layoutMode") != mode:
                self.setProperty("layoutMode", mode)
                self.style().unpolish(self)
                self.style().polish(self)
            self._reflow_toolbar(mode)
            self.editor_hint.setVisible(not compact)
            self.tree.setColumnHidden(2, compact)
            self.split.setOrientation(
                Qt.Orientation.Horizontal if mode == "wide" else Qt.Orientation.Vertical
            )
            window_height = self.window().height()
            preferred = (
                min(760, max(540, window_height - 160))
                if mode != "wide"
                else min(620, max(360, window_height - 360))
            )
            self.setMinimumHeight(preferred)

    def _reflow_toolbar(self, mode: str) -> None:
        if self._toolbar_mode == mode:
            return
        self._toolbar_mode = mode
        while self.toolbar_layout.count():
            self.toolbar_layout.takeAt(0)
        columns = {"wide": 5, "medium": 3, "compact": 2}[mode]
        for index, button in enumerate(self.toolbar_buttons):
            if mode == "wide":
                button.setMaximumWidth(132)
                button.setMinimumWidth(104)
                button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            else:
                button.setMaximumWidth(16777215)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.toolbar_layout.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.toolbar_layout.setColumnStretch(column, 0 if mode == "wide" else 1)
        self.toolbar_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            if mode == "wide" else Qt.AlignmentFlag.AlignTop
        )

    def _sync_command_state(self, current=None, _previous=None) -> None:
        item = current or self.tree.currentItem()
        parent = item.parent() if item is not None else None
        sibling_parent = parent or self.tree.invisibleRootItem()
        index = sibling_parent.indexOfChild(item) if item is not None else -1
        self.move_up_action.setEnabled(index > 0)
        self.move_down_action.setEnabled(
            item is not None and index < sibling_parent.childCount() - 1
        )
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        node_type = data.get("type") if data else ""
        self.move_button.setEnabled(node_type == "action")
        self.customize_button.setEnabled(node_type in {"action", "submenu"})
        icon = data.get("icon") if data else None
        self.icon_display_menu.setEnabled(
            node_type in {"action", "submenu"}
            and isinstance(icon, dict)
            and icon.get("kind") == "file"
        )
        display = icon.get("display") if isinstance(icon, dict) else ""
        self.icon_contain_action.setChecked(display == "contain")
        self.icon_cover_action.setChecked(display == "cover")
        self.delete_submenu_action.setEnabled(
            node_type == "submenu"
        )
        self.delete_separator_action.setEnabled(node_type == "separator")

    def _rebuild_move_menu(self) -> None:
        self.move_menu.clear()
        root_action = self.move_menu.addAction("根菜单")
        root_action.setData("__root__")
        root_action.triggered.connect(lambda: self._move_selected_to("__root__"))
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            item = root.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") != "submenu":
                continue
            target_id = str(data.get("id") or "")
            action = self.move_menu.addAction(item.text(0))
            action.setData(target_id)
            action.triggered.connect(
                lambda _checked=False, target_id=target_id: self._move_selected_to(target_id)
            )

    def set_layout(self, layout: dict) -> None:
        layout, _diagnostics = merge_default_menu_actions(
            layout, registered_actions=MENU_ACTIONS.ids
        )
        layout = materialize_implicit_separators(layout)
        self._pending_empty_submenus.clear()
        self.tree.blockSignals(True)
        self.tree.clear()
        for node in layout.get("nodes", []):
            self._append_node(None, node)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._sync_command_state()
        self._on_changed()

    def _schedule_preview_refresh(self, *_args) -> None:
        """Coalesce the remove/insert phases of cross-parent tree moves."""
        if self._preview_refresh_pending:
            return
        self._preview_refresh_pending = True
        QTimer.singleShot(0, self._flush_preview_refresh)

    def _on_tree_rows_removed(self, parent_index, *_args) -> None:
        if parent_index.isValid():
            parent = self.tree.itemFromIndex(parent_index)
            data = parent.data(0, Qt.ItemDataRole.UserRole) if parent is not None else {}
            if data and data.get("type") == "submenu":
                self._pending_empty_submenus.append(parent)
        self._schedule_preview_refresh()

    def _flush_preview_refresh(self) -> None:
        self._preview_refresh_pending = False
        for submenu in self._pending_empty_submenus:
            self._remove_empty_submenu(submenu)
        self._pending_empty_submenus.clear()
        self._on_changed()

    def _append_node(self, parent: QTreeWidgetItem | None, node: dict) -> None:
        node_type = str(node.get("type") or "")
        node_id = str(node.get("id") or "")
        alias = str(node.get("alias") or "").strip()
        original = str(node.get("label") or MENU_ACTIONS.label(node_id))
        label = f"{alias}（{original}）" if alias else original
        if node_type == "separator":
            label = "— 分割线"
        item = QTreeWidgetItem([label, "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, {
            "type": node_type,
            "id": node_id,
            "section": node.get("section"),
            "label": node.get("label"),
            "alias": alias,
            "icon": node.get("icon") if "icon" in node else None,
        })
        available = node_type != "action" or node_id in self.available_actions
        item.setData(0, Qt.ItemDataRole.UserRole + 1, available)
        enabled = node_type != "action" or node_id in self.enabled_actions
        item.setData(0, Qt.ItemDataRole.UserRole + 2, enabled)
        if node_type != "separator":
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if node_type == "submenu":
            # Submenus stay at the root: they can receive actions, but cannot be
            # dragged into one another. Their root order is changed with the
            # explicit move buttons.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        elif node_type == "action":
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        else:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        if node_type != "separator":
            item.setCheckState(0, Qt.CheckState.Checked if node.get("visible", True) else Qt.CheckState.Unchecked)
        if node_type == "action" and node_id in {"modern_settings", "quit"}:
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        if not available:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._sync_item_icon(item)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in node.get("children", []):
            self._append_node(item, child)

    def item_for_action(self, action_id: str) -> QTreeWidgetItem | None:
        def find(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                if data.get("type") == "action" and data.get("id") == action_id:
                    return item
                found = find(item)
                if found is not None:
                    return found
            return None
        return find(self.tree.invisibleRootItem())

    def value(self) -> dict:
        def encode(item: QTreeWidgetItem) -> dict:
            data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
            node_type = data.get("type")
            node = {
                "type": node_type,
                "id": data.get("id"),
                "visible": True if node_type == "separator" else item.checkState(0) == Qt.CheckState.Checked,
            }
            if data.get("section"):
                node["section"] = data["section"]
            if data.get("alias"):
                node["alias"] = str(data["alias"])[:40]
            if data.get("icon") is not None:
                icon = data["icon"]
                node["icon"] = dict(icon) if isinstance(icon, dict) else str(icon)[:40]
            if node_type == "submenu":
                node["label"] = str(data.get("label") or item.text(0)).strip()[:40]
                node["children"] = [encode(item.child(i)) for i in range(item.childCount())]
            return node
        root = self.tree.invisibleRootItem()
        return {"schema_version": 1, "layout_id": "user", "nodes": [encode(root.child(i)) for i in range(root.childCount())]}

    def set_enabled_actions(self, enabled_actions) -> None:
        """Refresh runtime state styling without mutating the layout tree."""
        self.enabled_actions = frozenset(enabled_actions)
        def refresh(parent) -> None:
            for index in range(parent.childCount()):
                item = parent.child(index)
                data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                enabled = data.get("type") != "action" or data.get("id") in self.enabled_actions
                item.setData(0, Qt.ItemDataRole.UserRole + 2, enabled)
                refresh(item)
        refresh(self.tree.invisibleRootItem())
        self._on_changed()

    def reset_default(self) -> None:
        self.set_layout(load_default_menu_layout())

    def set_item_alias(self, action_id: str, alias: str) -> None:
        item = self.item_for_action(action_id)
        if item is None:
            return
        self._set_item_alias(item, alias)

    def set_item_icon(self, action_id: str, icon_name: str) -> None:
        item = self.item_for_action(action_id)
        if item is None:
            return
        self._set_item_icon(item, icon_name)

    def set_item_file_icon(self, action_id: str, path, display: str = "contain") -> bool:
        item = self.item_for_action(action_id)
        if item is None:
            return False
        return self._set_item_file_icon(item, path, display)

    def _set_item_file_icon(self, item: QTreeWidgetItem, path, display: str) -> bool:
        candidate = Path(path).expanduser().resolve()
        if custom_icon_file_error(candidate):
            return False
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        data["icon"] = {
            "kind": "file",
            "path": str(candidate),
            "display": "cover" if display == "cover" else "contain",
        }
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._sync_item_icon(item)
        self._sync_command_state(item)
        self._on_changed()
        return True

    def insert_separator(self, *, after_action_id: str | None = None) -> None:
        target = self.item_for_action(after_action_id) if after_action_id else self.tree.currentItem()
        parent = target.parent() if target is not None else None
        owner = parent or self.tree.invisibleRootItem()
        index = owner.indexOfChild(target) + 1 if target is not None else owner.childCount()
        existing_ids: set[str] = set()
        def collect_ids(nodes) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                existing_ids.add(str(node.get("id") or ""))
                collect_ids(node.get("children", []))
        collect_ids(self.value().get("nodes", []))
        number = 1
        while f"user.separator-{number}" in existing_ids:
            number += 1
        item = QTreeWidgetItem()
        self._configure_detached_node(item, {
            "type": "separator", "id": f"user.separator-{number}", "visible": True,
        })
        owner.insertChild(index, item)
        self.tree.setCurrentItem(item)
        self._on_changed()

    def _configure_detached_node(self, item: QTreeWidgetItem, node: dict) -> None:
        """Configure a node before insertion without coupling to tree ownership."""
        node_type = str(node.get("type") or "")
        node_id = str(node.get("id") or "")
        original = str(node.get("label") or MENU_ACTIONS.label(node_id))
        alias = str(node.get("alias") or "").strip()
        label = (
            "— 分割线" if node_type == "separator"
            else f"{alias}（{original}）" if alias else original
        )
        item.setText(0, label)
        item.setData(0, Qt.ItemDataRole.UserRole, {
            "type": node_type, "id": node_id, "section": node.get("section"),
            "label": node.get("label"), "alias": alias,
            "icon": node.get("icon") if "icon" in node else None,
        })
        item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, True)
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)

    def _set_item_alias(self, item: QTreeWidgetItem, alias: str) -> None:
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        alias = str(alias).strip()[:40]
        data["alias"] = alias
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        default = data.get("label") or MENU_ACTIONS.label(str(data.get("id") or ""))
        item.setText(0, f"{alias}（{default}）" if alias else str(default))
        self._on_changed()

    def _set_item_icon(self, item: QTreeWidgetItem, icon_name: str) -> None:
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        data["icon"] = None if icon_name == "default" else str(icon_name or "none")
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._sync_item_icon(item)
        self._sync_command_state(item)
        self._on_changed()

    def _sync_item_icon(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") == "separator":
            item.setIcon(0, QIcon())
            return
        item.setIcon(0, MENU_ACTIONS.icon(
            self, str(data.get("id") or ""), data.get("icon")
        ))

    def _rename_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        text, accepted = QInputDialog.getText(
            self, "更换菜单别名", "显示名称", text=str(data.get("alias") or "")
        )
        if accepted:
            self._set_item_alias(item, text)

    def _change_selected_icon(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        labels = [label for label, _value in CUSTOM_ICON_CHOICES]
        selected, accepted = QInputDialog.getItem(self, "更换菜单图标", "图标", labels, 0, False)
        if accepted:
            value = next(value for label, value in CUSTOM_ICON_CHOICES if label == selected)
            self._set_item_icon(item, value)

    def _choose_selected_file_icon(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择菜单图标",
            str(Path.home()),
            IMAGE_NAME_FILTER,
        )
        if not selected:
            return
        error = custom_icon_file_error(selected)
        if error:
            QMessageBox.warning(self, "无法使用此图标", error)
            return
        self._set_item_file_icon(item, selected, "contain")

    def _set_selected_file_display(self, display: str) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        icon = data.get("icon") if data else None
        if not isinstance(icon, dict) or icon.get("kind") != "file":
            return
        self._set_item_file_icon(item, icon.get("path") or "", display)

    def _restore_selected_presentation(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        self._set_item_alias(item, "")
        self._set_item_icon(item, "default")

    def _insert_separator_after_selected(self) -> None:
        self.insert_separator()

    def _delete_selected_separator(self) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        if item is None or not data or data.get("type") != "separator":
            return
        parent = item.parent() or self.tree.invisibleRootItem()
        parent.removeChild(item)
        self._on_changed()

    def _move_selected(self, offset: int) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent() or self.tree.invisibleRootItem()
        index = parent.indexOfChild(item)
        target = index + offset
        if 0 <= target < parent.childCount():
            parent.takeChild(index)
            parent.insertChild(target, item)
            self.tree.setCurrentItem(item)
            self._on_changed()

    def _promote_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            return
        old_parent = item.parent()
        old_parent.removeChild(item)
        self.tree.addTopLevelItem(item)
        self._remove_empty_submenu(old_parent)
        self.tree.setCurrentItem(item)
        self._on_changed()

    def _remove_empty_submenu(self, item: QTreeWidgetItem | None) -> bool:
        if item is None or item.childCount() != 0:
            return False
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") != "submenu" or item.parent() is not None:
            return False
        root = self.tree.invisibleRootItem()
        index = root.indexOfChild(item)
        if index < 0:
            return False
        root.takeChild(index)
        return True

    def _move_selected_to(self, target_id: str) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        item_data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if target_id == "__root__":
            self._promote_selected()
            return
        if item_data.get("type") == "submenu":
            return
        target = None
        root = self.tree.invisibleRootItem()
        def find(parent):
            nonlocal target
            for index in range(parent.childCount()):
                candidate = parent.child(index)
                data = candidate.data(0, Qt.ItemDataRole.UserRole) or {}
                if data.get("type") == "submenu" and data.get("id") == target_id:
                    target = candidate
                    return
                find(candidate)
        find(root)
        if target is None or target is item:
            return
        parent = item.parent() or root
        parent.removeChild(item)
        target.addChild(item)
        if parent is not root:
            self._remove_empty_submenu(parent)
        target.setExpanded(True)
        self.tree.setCurrentItem(item)
        self._on_changed()

    def _create_submenu(self) -> None:
        label, accepted = QInputDialog.getText(self, "新建子菜单", "子菜单名称")
        label = label.strip()
        if not accepted or not label:
            return
        existing = {
            str((self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) or {}).get("id") or "")
            for i in range(self.tree.topLevelItemCount())
        }
        index = 1
        while f"user.submenu-{index}" in existing:
            index += 1
        self._append_node(None, {
            "type": "submenu",
            "id": f"user.submenu-{index}",
            "label": label[:40],
            "visible": True,
            "children": [],
        })
        self._on_changed()

    def _delete_selected_submenu(self) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        if item is None or not data or data.get("type") != "submenu":
            return
        answer = QMessageBox.question(
            self,
            "删除子菜单",
            f"确定删除“{item.text(0)}”吗？\n其中的菜单项会保留并移到根菜单。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        root = self.tree.invisibleRootItem()
        index = root.indexOfChild(item)
        children = [item.takeChild(0) for _ in range(item.childCount())]
        root.takeChild(index)
        for offset, child in enumerate(children):
            root.insertChild(index + offset, child)
        if children:
            self.tree.setCurrentItem(children[0])
        elif root.childCount():
            self.tree.setCurrentItem(root.child(min(index, root.childCount() - 1)))
        self._on_changed()

    def _on_changed(self, *_args) -> None:
        self.tree.blockSignals(True)
        self.preview.clear()
        def refresh_positions(source):
            for index in range(source.childCount()):
                item = source.child(index)
                data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                if data.get("type") == "separator":
                    item.setText(1, "布局")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                elif item.data(0, Qt.ItemDataRole.UserRole + 1) is False:
                    item.setText(1, "此平台不可用")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                elif item.checkState(0) != Qt.CheckState.Checked:
                    item.setText(1, "已隐藏")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                elif item.data(0, Qt.ItemDataRole.UserRole + 2) is False:
                    item.setText(1, "已停用")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                else:
                    item.setText(1, "已启用")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                item.setToolTip(2, item.text(2))
                refresh_positions(item)
        refresh_positions(self.tree.invisibleRootItem())
        resolved = resolve_menu_layout(
            self.value(),
            registered_actions=MENU_ACTIONS.ids,
            available_actions=self.available_actions,
        )
        def add_preview(nodes, target):
            for node in nodes:
                if node.get("type") == "separator":
                    clone = QTreeWidgetItem(["────────"])
                    target.addChild(clone)
                    continue
                action_id = str(node.get("id") or "")
                label = str(node.get("alias") or node.get("label") or MENU_ACTIONS.label(action_id))
                clone = QTreeWidgetItem([label])
                icon = MENU_ACTIONS.icon(self, action_id, node.get("icon"))
                if not icon.isNull():
                    clone.setIcon(0, icon)
                if node.get("type") == "action" and action_id not in self.enabled_actions:
                    clone.setFlags(clone.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    clone.setToolTip(0, MENU_ACTIONS.disabled_reason(action_id))
                target.addChild(clone)
                add_preview(node.get("children", ()), clone)
        add_preview(resolved.nodes, self.preview.invisibleRootItem())
        self.preview.expandAll()
        self.tree.blockSignals(False)
        self.changed.emit()


class _AiSettingsPage(QWidget):
    test_done = Signal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setObjectName("aiSettingsContent")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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
        self._provider_drafts: dict[str, dict] = {}
        self._deleted_provider_ids: set[str] = set()
        self._loading_provider = False
        self.test_done.connect(self._on_test_done)

        self.provider_combo = ModernSelect(self, width=230)
        self.add_provider_btn = QPushButton("添加", self)
        self.delete_provider_btn = QPushButton("删除", self)
        for pid, provider_item in self.settings.providers.items():
            self.provider_combo.addItem(self._provider_label(provider_item), pid)
        self.provider_combo.setCurrentData(self.settings.active_provider)

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
        self.system_notify_check = ToggleSwitch()
        self.system_notify_check.setChecked(bool(config.get("system_notifications_enabled", True)))
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
        provider_row = ResponsiveActionRow(
            self.provider_combo,
            [self.add_provider_btn, self.delete_provider_btn],
        )
        root.addWidget(SettingsSection("API 列表", [
            SettingRow(
                "provider_list", "API 列表",
                "选择要编辑/切换的模型服务；保存后当前选中项会作为 active_provider 生效。",
                provider_row,
            ),
        ], self))
        root.addWidget(SettingsSection("模型与连接", [
            SettingRow("provider_name", "Provider 名称", "用于区分当前使用的模型服务。", self.name),
            SettingRow("api_url", "API 地址", "OpenAI Chat Completions 兼容服务地址。", self.url),
            SettingRow("model", "模型", "发送请求时使用的模型标识。", self.model),
            SettingRow("api_key", "API Key", "凭据优先保存到系统钥匙串。", self.key),
            SettingRow("system_prompt", "System Prompt", "定义桌宠对话时的身份、语气和行为。", self.prompt, stacked=True),
            SettingRow("connection_test", "连接测试", self.test_result.text(), self.test_button),
        ], self))
        root.addWidget(SettingsSection("系统通知", [
            SettingRow(
                "system_notifications_enabled", "系统通知",
                "对话完成 / 生成失败 / 需要授权时，即使切走窗口也会在桌面右下角提醒。",
                self.system_notify_check,
            ),
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
        root.addWidget(SettingsSection("生成参数（高级）", [
            SettingRow("timeout", "请求超时", "等待模型服务响应的最长时间。", self.timeout),
            SettingRow("temperature", "Temperature", "数值越高，回答越随机。", self.temperature),
            SettingRow("max_tokens", "最大输出 Token", "限制模型单次回复的最大长度。", self.tokens),
            SettingRow("skip_ssl", "跳过 SSL 证书验证", "仅用于本地网关或自签名证书。", self.skip_ssl),
        ], self, advanced=True))
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.add_provider_btn.clicked.connect(self._add_provider)
        self.delete_provider_btn.clicked.connect(self._delete_provider)
        self._update_provider_buttons()
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

    # ------------------------------------------------------------ API 列表管理
    @staticmethod
    def _provider_label(p) -> str:
        name = str(p.name or p.provider_id)
        model = str(p.model or '').strip()
        return f"{name} · {model}" if model else name

    def _capture_current_draft(self) -> None:
        pid = self.settings.active_provider
        if not pid or pid not in self.settings.providers:
            return
        existing = self._provider_drafts.get(pid, {})
        key_text = self.key.text()
        vkey_text = self.vision_key.text()
        self._provider_drafts[pid] = {
            "name": self.name.text().strip(),
            "base_url": self.url.text().strip(),
            "model": self.model.text().strip(),
            # 输入框为空表示“不修改/不覆盖”，保留草稿里已录入但尚未保存的 Key；
            # 否则 _load_provider_ui() 清空输入框后会把草稿 Key 覆盖成空。
            "key": key_text if key_text else existing.get("key", ""),
            "timeout": float(self.timeout.value()),
            "temperature": float(self.temperature.value()),
            "max_tokens": int(self.tokens.value()),
            "vision_model": self.vision_model.text().strip(),
            "vision_same_as_chat": self.vision_same.isChecked(),
            "vision_base_url": self.vision_url.text().strip(),
            "vision_key": vkey_text if vkey_text else existing.get("vision_key", ""),
            "verify_ssl": not self.skip_ssl.isChecked(),
        }

    def _load_provider_ui(self, provider_id: str) -> None:
        p = self.settings.providers.get(provider_id)
        if p is None:
            return
        draft = self._provider_drafts.get(provider_id, {})
        self.settings.active_provider = provider_id
        self.name.setText(draft.get("name") if draft.get("name") is not None else p.name)
        self.url.setText(draft.get("base_url") if draft.get("base_url") is not None else p.base_url)
        self.model.setText(draft.get("model") if draft.get("model") is not None else p.model)
        self.key.clear()
        self.timeout.setValue(int(draft.get("timeout", p.timeout)))
        self.temperature.setValue(float(draft.get("temperature", p.temperature)))
        self.tokens.setValue(int(draft.get("max_tokens", p.max_tokens)))
        self.vision_model.setText(draft.get("vision_model", p.vision_model))
        self.vision_same.setChecked(bool(draft.get("vision_same_as_chat", p.vision_same_as_chat)))
        self.vision_url.setText(draft.get("vision_base_url", p.vision_base_url))
        self.vision_key.clear()
        self.skip_ssl.setChecked(not bool(draft.get("verify_ssl", p.verify_ssl)))
        self._update_provider_buttons()

    def _on_provider_changed(self, _index: int = -1) -> None:
        if self._loading_provider:
            return
        self._capture_current_draft()
        pid = self.provider_combo.currentData()
        if pid and pid in self.settings.providers:
            self._load_provider_ui(pid)

    def _new_provider_id(self) -> str:
        # 避免复用已删除的 provider_id：否则保存合并时新建项会被删除集合过滤掉。
        used = set(self.settings.providers) | set(self._deleted_provider_ids)
        i = len(self.settings.providers) + 1
        while f"api-{i}" in used:
            i += 1
        return f"api-{i}"

    def _add_provider(self) -> None:
        self._capture_current_draft()
        base = self.settings.active_config
        new_id = self._new_provider_id()
        new = self._provider_config_type(
            new_id,
            name=f"{base.name} 副本",
            base_url=base.base_url,
            chat_path=base.chat_path,
            model=base.model,
            api_key_ref=f"provider/{new_id}",
            timeout=base.timeout,
            temperature=base.temperature,
            max_tokens=base.max_tokens,
            vision_model=base.vision_model,
            vision_same_as_chat=base.vision_same_as_chat,
            vision_base_url=base.vision_base_url,
            vision_api_key_ref=f"provider/{new_id}/vision",
            verify_ssl=base.verify_ssl,
        )
        self.settings.providers[new_id] = new
        self._provider_drafts[new_id] = {
            "name": new.name,
            "base_url": new.base_url,
            "model": new.model,
            "key": "",
            "timeout": new.timeout,
            "temperature": new.temperature,
            "max_tokens": new.max_tokens,
            "vision_model": new.vision_model,
            "vision_same_as_chat": new.vision_same_as_chat,
            "vision_base_url": new.vision_base_url,
            "vision_key": "",
            "verify_ssl": new.verify_ssl,
        }
        self._loading_provider = True
        try:
            self.provider_combo.addItem(self._provider_label(new), new_id)
            self.provider_combo.setCurrentIndex(self.provider_combo.count() - 1)
        finally:
            self._loading_provider = False
        self.settings.active_provider = new_id
        self._load_provider_ui(new_id)
        self._update_provider_buttons()

    def _delete_provider(self) -> None:
        if len(self.settings.providers) <= 1:
            return
        pid = self.provider_combo.currentData()
        if not pid or pid not in self.settings.providers:
            return
        self.settings.providers.pop(pid, None)
        self._provider_drafts.pop(pid, None)
        self._deleted_provider_ids.add(pid)
        self._loading_provider = True
        try:
            self.provider_combo.clear()
            for p in self.settings.providers.values():
                self.provider_combo.addItem(self._provider_label(p), p.provider_id)
            self.settings.active_provider = next(iter(self.settings.providers))
            self.provider_combo.setCurrentData(self.settings.active_provider)
        finally:
            self._loading_provider = False
        self._load_provider_ui(self.settings.active_provider)
        self._update_provider_buttons()

    def _update_provider_buttons(self) -> None:
        self.delete_provider_btn.setEnabled(len(self.settings.providers) > 1)

    def _apply_draft_to_provider(self, provider_id: str) -> None:
        p = self.settings.providers.get(provider_id)
        draft = self._provider_drafts.get(provider_id)
        if p is None or not draft:
            return
        if draft.get("name"):
            p.name = draft["name"]
        p.base_url = draft.get("base_url") or p.base_url
        p.model = draft.get("model") or p.model
        p.timeout = float(draft.get("timeout", p.timeout))
        p.temperature = float(draft.get("temperature", p.temperature))
        p.max_tokens = int(draft.get("max_tokens", p.max_tokens))
        p.vision_model = draft.get("vision_model", p.vision_model)
        p.vision_same_as_chat = bool(draft.get("vision_same_as_chat", p.vision_same_as_chat))
        p.vision_base_url = draft.get("vision_base_url", p.vision_base_url)
        p.verify_ssl = bool(draft.get("verify_ssl", p.verify_ssl))
        key = str(draft.get("key") or "")
        if key:
            p.api_key_ref = p.api_key_ref or f"provider/{provider_id}"
            if not self._secret_store_type().set(p.api_key_ref, key):
                p.api_key = key
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")
        vkey = str(draft.get("vision_key") or "")
        if vkey:
            p.vision_api_key_ref = p.vision_api_key_ref or f"provider/{provider_id}/vision"
            if not self._secret_store_type().set(p.vision_api_key_ref, vkey):
                p.vision_api_key = vkey
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")

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
        # 可能在本窗口打开期间改过 Provider 结构；先保留本窗口的 provider 草稿/
        # 新增/删除，再与磁盘快照合并，避免覆盖其它窗口新增的结构。
        old_settings = self.settings
        self._capture_current_draft()
        self.settings = self.config.chat_settings()
        for pid in list(self.settings.providers):
            if pid in self._deleted_provider_ids:
                self.settings.providers.pop(pid, None)
        for pid, provider in old_settings.providers.items():
            if pid not in self.settings.providers and pid not in self._deleted_provider_ids:
                self.settings.providers[pid] = provider
        active_pid = self.provider_combo.currentData()
        if active_pid in self.settings.providers:
            self.settings.active_provider = active_pid
        elif self.settings.providers:
            self.settings.active_provider = next(iter(self.settings.providers))
        for pid in list(self.settings.providers):
            self._apply_draft_to_provider(pid)
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
        self.config.set("system_notifications_enabled", self.system_notify_check.isChecked())
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
        sidebar_pane.setFixedWidth(200)
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
        self.sidebar.setIconSize(QSize(18, 18))
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
        autostart_desc = "登录系统后自动启动桌宠。" if not self.config.instance_id else "登录系统后自动启动桌宠。（仅主桌宠可设置）"
        launch_rows = [
            SettingRow("autostart", "开机自启", autostart_desc, self.autostart_check),
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
                SettingRow("cursor_hidden_passthrough", "光标隐藏时自动穿透", "Windows 光标隐藏后，桌宠自动穿透点击；光标出现立即恢复。适用于游戏，也可能影响自动隐藏光标的视频播放器。", self.cursor_hidden_passthrough_check),
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

        island_content = QWidget()
        island_layout = QVBoxLayout(island_content)
        island_layout.setContentsMargins(0, 0, 0, 0)
        island_layout.setSpacing(18)
        island_layout.addWidget(SettingsSection("灵动岛", [
            SettingRow("dynamic_island_enabled", "启用灵动岛", "显示独立胶囊小窗；桌宠隐藏后仍可常驻。", self.island_enabled_check),
            SettingRow("dynamic_island_icon", "显示图标", "在胶囊左侧显示角色图标。", self.island_icon_check),
            SettingRow("dynamic_island_name", "显示名称", "显示当前角色名称。", self.island_name_check),
            SettingRow("dynamic_island_info", "显示信息槽", "显示时间/余额/自定义短文本等信息。", self.island_info_check),
            SettingRow("dynamic_island_status", "显示状态灯", "显示右侧状态圆点。", self.island_status_check),
            SettingRow("dynamic_island_info_mode", "信息槽内容", "选择信息槽显示的内容；自定义文本在下方填写。", self.island_info_mode_select),
            SettingRow("dynamic_island_style", "背景风格", "黑色 / 白色 / 苹果式玻璃质感。", self.island_style_select),
            SettingRow("dynamic_island_icon_value", "图标", "选择灵动岛左侧显示的预制 emoji 图标。", self.island_icon_select),
            SettingRow("dynamic_island_custom_text", "自定义短文本", "信息槽选择“自定义短文本”时显示的内容。", self.island_custom_text_edit, stacked=True),
        ], island_content))
        island_layout.addStretch(1)
        self._add_page("灵动岛", "island", self._page_shell("灵动岛", island_content))

        behavior_content = QWidget()
        behavior_layout = QVBoxLayout(behavior_content)
        behavior_layout.setContentsMargins(0, 0, 0, 0)
        behavior_layout.setSpacing(16)
        behavior_layout.addWidget(SettingsSection("动画", [
            SettingRow("playback_speed", "播放速率", "控制所有桌宠动画的播放速度。", self.speed_select),
            SettingRow("animation_gap", "动作等待间隔", "非待机动作之间的休息时间；0 秒表示连续播放。", self.gap_spin),
            SettingRow("idle_low_fps", "闲置降帧", "一段时间不操作桌宠时，动画按半帧率呈现（24fps 素材 → 12fps 效果）；任何交互立即恢复全帧率。", self.idle_low_fps_check),
            SettingRow("no_move", "不移动", "暂停桌宠在桌面上的自动移动。", self.no_move_check),
            SettingRow("mouse_through", "鼠标穿透", "开启后桌宠不接收鼠标事件，点击穿透到下层窗口。", self.mouse_through_check),
            SettingRow("music_sing", "音乐自动唱歌", "检测到后台播放音乐时，自动播放唱歌动画。", self.music_sing_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("拖拽与弹射", [
            SettingRow("drag_physics", "拖动物理", "启用拖拽惯性、重力和边缘反弹。", self.drag_physics_check),
            SettingRow("throw_strength", "甩出力度", "控制桌宠被甩出或弹射发射时的最大速度限制。", self.throw_strength_select),
            SettingRow("slingshot_enabled", "弹弓弹射", "拖拽桌宠时点击右键进入蓄力瞄准，松开左键弹射飞出（Esc或右键取消）。", self.slingshot_check),
            SettingRow("lock_position", "锁定位置", "桌宠固定不动，无法拖动（点击互动仍有效）。", self.lock_position_check),
            SettingRow("shift_drag", "SHIFT+左键拖动", "开启后必须按住 SHIFT 再左键才能拖动桌宠。", self.shift_drag_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("多开碰撞", [
            SettingRow("collision_enabled", "碰撞开关", "多开桌宠之间发生碰撞物理互动。开启鼠标穿透的桌宠仍会参与碰撞，锁定位置的桌宠作为固定障碍。", self.collision_enabled_check),
            SettingRow("collision_restitution", "弹性系数", "碰撞反弹的能量保留程度（0~1.00，默认 0.82）。", self.collision_restitution_spin),
            SettingRow("collision_friction", "摩擦系数", "擦边碰撞时的切向摩擦阻力（0~0.30，默认 0.08）。", self.collision_friction_spin),
            SettingRow("collision_mass_scale", "质量倍率", "桌宠的基础质量加权倍率（0.5~2.0，默认 1.0）。", self.collision_mass_scale_spin),
            SettingRow("collision_impulse_cap", "冲量上限", "单次碰撞能施加的最大冲量上限（1000~12000，默认 9000）。", self.collision_impulse_cap_spin),
            SettingRow("collision_sound_enabled", "碰撞音效", "碰撞时播放音效反馈。", self.collision_sound_check),
            SettingRow("collision_sound_volume", "碰撞音量", "调整碰撞音效播放音量。", self.collision_sound_volume_spin),
        ], behavior_content))
        self.collision_policy_note = QLabel("碰撞参数由当前协调者桌宠的设置决定")
        self.collision_policy_note.setObjectName("settingHint")
        self.collision_policy_note.setWordWrap(True)
        self.collision_policy_note.setContentsMargins(14, 0, 14, 0)
        behavior_layout.addWidget(self.collision_policy_note)
        click_rows = [
            SettingRow("click_sound", "点击音效", "点击桌宠时播放轻量反馈音效。", self.click_sound_check),
            SettingRow("click_sound_pack", "音效音源", "选择预设音效包、自定义音频文件或文件夹随机播放。", self.click_sound_picker, stacked=True),
            SettingRow("click_sound_volume", "音效音量", "调整点击音效播放音量。", self.click_sound_volume_spin),
            SettingRow("click_sound_preview", "试听音效", "测试当前选择的点击音效。", self.click_sound_preview_btn),
            SettingRow("click_self_talk", "点击触发自言自语", "点击时随机显示一条自言自语内容。", self.click_self_talk_check),
        ]
        if self.click_balance_check is not None:
            click_rows.insert(4, SettingRow(
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
            SettingRow("self_talk_image_scale", "配图大小", "气泡里配图的显示尺寸（100% 为默认）。", self.self_talk_image_scale_spin),
            SettingRow("click_talk_bindings", "点击动画台词绑定", "为每个点击动画设置专属自言自语台词。", self.click_talk_bindings_btn),
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

        # Agent 联动：音效设置
        agent_sound_rows = [
            SettingRow("agent_sound_enabled", "Agent 音效联动", "当 Agent 开始工作、任务完成或发生错误时播放提示音。", self.agent_sound_check),
            SettingRow("agent_sound_start", "开始工作提示音", "Agent 进入工作状态时播放。", self.agent_sound_start_widget, stacked=True),
            SettingRow("agent_sound_done", "任务完成提示音", "Agent 完成任务时播放。", self.agent_sound_done_widget, stacked=True),
            SettingRow("agent_sound_error", "发生错误提示音", "Agent 出现错误异常时播放。", self.agent_sound_error_widget, stacked=True),
            SettingRow("agent_sound_volume", "音效音量", "调整 Agent 提示音音量。", self.agent_sound_volume_spin),
            SettingRow("agent_sound_cooldown", "冷却时间", "防止短时间内频繁触发音效；0 表示无时间冷却（仍单次去重）。", self.agent_sound_cooldown_spin),
        ]
        behavior_layout.addWidget(SettingsSection("Agent 联动 · 提示音效", agent_sound_rows, behavior_content))
        labels = DIALOGUE_LABELS
        behavior_layout.addWidget(SettingsSection("表达风格", [
            SettingRow("dialogue_mode", "表达风格", "控制桌宠自言自语、候选内容和主动气泡的说话方式；同时覆盖 Agent 状态、审批、提问、错误、限流等所有气泡。内置「原有模式」与「鲸鱼娘女仆模式」不可编辑；选择「自定义台词」后，可粘贴下方 JSON 一键导入全部弹窗文案。", self.dialogue_mode_select),
        ], behavior_content))
        behavior_layout.addWidget(SettingsCard([
            SettingRow(
                "dialogue_template_actions", "弹窗文案模板（JSON）",
                "一键复制当前全部弹窗内容模板到剪贴板；把复制的 JSON 粘贴回「导入模板」可一次覆盖所有「自定义台词」，也可以直接发给 AI 依角色卡改写。事件留空时自动沿用原有模式文案；模板占位符会自动读取上游事件字段。",
                self.dialogue_template_actions,
                stacked=True,
            ),
        ], behavior_content))
        behavior_layout.addWidget(SettingsCard([
            SettingRow(
                f"dialogue_{key}",
                labels.get(key, key),
                "留空则使用基础模式台词。可用参数："
                + "、".join("{" + item + "}" for item in DIALOGUE_KEY_PARAMS.get(key, ()))
                if DIALOGUE_KEY_PARAMS.get(key)
                else "留空则使用基础模式台词。可用参数：无",
                edit,
                stacked=True,
            )
            for key, edit in self.dialogue_phrase_edits.items()
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("待办提醒", [
            SettingRow("todo_reminder_enabled", "待办提醒",
                       "到点通过气泡或桌面通知提醒；待办条目在右键菜单「待办提醒」面板中管理。",
                       self.todo_reminder_check),
            SettingRow("todo_reminder_lead_minutes", "提前提醒",
                       "到点前提前提醒的分钟数（0~60，0 = 不提前，仅准点提醒一次）。",
                       self.todo_reminder_lead_spin),
        ], behavior_content))
        behavior_layout.addStretch(1)
        self._add_page("桌宠行为", "play", self._page_shell("桌宠行为", behavior_content))

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

        menu_content = QWidget()
        menu_page_layout = QVBoxLayout(menu_content)
        menu_page_layout.setContentsMargins(0, 0, 0, 0)
        menu_page_layout.setSpacing(18)
        self.menu_template_select = ModernSelect(menu_content, width=156)
        self.menu_template_select.addItem("新版菜单", "modern")
        self.menu_template_select.addItem("旧版兼容菜单", "legacy")
        self.menu_template_select.setCurrentData(
            str(self.config.get("context_menu_template", "modern") or "modern")
        )
        menu_available_actions = set(MENU_ACTIONS.ids)
        if sys.platform != "win32":
            menu_available_actions.discard("proactive_screen")
        if not self.include_ai:
            menu_available_actions.difference_update({"chat", "look_screen", "balance", "proactive_screen"})
        self.menu_available_actions = frozenset(menu_available_actions)
        menu_enabled_actions = set(menu_available_actions)
        if not self.config.get("quick_launch_apps", DEFAULT_QUICK_LAUNCH_APPS):
            menu_enabled_actions.discard("quick_launch")
        if not self.config.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG).get("enabled", True):
            menu_enabled_actions.discard("ojingjing")
        self.menu_layout_editor = MenuLayoutEditor(
            self.config.get("context_menu_layout"),
            menu_content,
            available_actions=menu_available_actions,
            enabled_actions=menu_enabled_actions,
        )
        menu_page_layout.addWidget(SettingsSection("内容与布局", [
            SettingRow("menu_template", "菜单模式", "旧版仅用于迁移期兼容；内容编排只作用于新版菜单。", self.menu_template_select),
            SettingRow("context_menu_layout", "菜单编排", "调整显示、顺序和层级；左侧编辑，右侧同步预览。", self.menu_layout_editor, stacked=True),
        ], menu_content))
        menu_page_layout.addStretch(1)
        self._add_page("菜单", "application", self._page_shell("菜单", menu_content))

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

        # AI rows are composed directly into the final capability domain by
        # _rebuild_domain_navigation. Do not temporarily hand the controller
        # widget to a QScrollArea: that creates a second Qt ownership path when
        # its rows are reparented into the shared card system.

        # Agent Exploration Loop Watchdog 独立设置页
        from .exploration_watchdog_settings import WatchdogSettingsPage
        agent_link_cfg = self.config.get("agent_link", {})
        self.watchdog_page = WatchdogSettingsPage(self.config, agent_link_cfg, self)
        self._rebuild_domain_navigation()
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self._search_rows = self.findChildren(SettingRow)
        self._search_matches: list[SettingRow] = []
        self._search_index = -1
        self.search_edit.textChanged.connect(self._search_settings)

        self.self_talk_check.toggled.connect(self._update_self_talk_controls)
        self.menu_translucent_check.toggled.connect(self._update_translucency_controls)
        self.island_enabled_check.toggled.connect(self._update_island_controls)
        self.island_icon_check.toggled.connect(self._update_island_icon_controls)
        self.island_info_check.toggled.connect(self._update_island_info_controls)
        self.island_info_mode_select.currentIndexChanged.connect(self._update_island_custom_text)
        self.egg_enabled_check.toggled.connect(self._update_egg_controls)
        self.egg_enabled_check.toggled.connect(self._sync_menu_action_states)
        self.quick_launch_editor.changed.connect(self._sync_menu_action_states)
        self.collision_enabled_check.toggled.connect(self._update_collision_controls)
        self.collision_sound_check.toggled.connect(self._update_collision_sound_controls)
        if hasattr(self, "pro_enabled_check"):
            self.pro_enabled_check.toggled.connect(self._update_proactive_controls)
            self.pro_idle_check.toggled.connect(self._update_proactive_idle_controls)
        self._update_self_talk_controls(self.self_talk_check.isChecked())
        self._update_translucency_controls(self.menu_translucent_check.isChecked())
        self._update_island_controls(self.island_enabled_check.isChecked())
        self._update_egg_controls(self.egg_enabled_check.isChecked())
        self._sync_menu_action_states()
        self._update_collision_controls(self.collision_enabled_check.isChecked())
        if hasattr(self, "pro_enabled_check"):
            self._update_proactive_controls(self.pro_enabled_check.isChecked())
        # 初始同步须在全部 SettingRow 构建完成后执行，否则 findChild 找不到行
        self._update_click_sound_controls(self.click_sound_check.isChecked())
        self._update_agent_sound_controls(self.agent_sound_check.isChecked())
        self._update_agent_sound_subcontrols()

        self.menu_theme_select.currentIndexChanged.connect(self._apply_selected_theme)
        self._apply_selected_theme()

    def _sync_menu_action_states(self, *_args) -> None:
        enabled = set(self.menu_available_actions)
        if not self.quick_launch_editor.apps():
            enabled.discard("quick_launch")
        if not self.egg_enabled_check.isChecked():
            enabled.discard("ojingjing")
        self.menu_layout_editor.set_enabled_actions(enabled)

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
        self.cursor_hidden_passthrough_check = None
        if sys.platform == "win32":
            self.cursor_hidden_passthrough_check = ToggleSwitch(self)
            self.cursor_hidden_passthrough_check.setChecked(bool(self.config.get("cursor_hidden_passthrough", True)))
        self.drag_physics_check = ToggleSwitch(self)
        self.drag_physics_check.setChecked(bool(self.config.get("drag_physics", False)))

        # 甩出力度四档：gentle (轻柔) / standard (标准) / strong (强力) / crazy (疯狂)
        self.throw_strength_select = ModernSelect(self, width=132)
        self.throw_strength_select.addItem("轻柔", "gentle")
        self.throw_strength_select.addItem("标准", "standard")
        self.throw_strength_select.addItem("强力", "strong")
        self.throw_strength_select.addItem("疯狂", "crazy")
        current_strength = str(self.config.get("throw_strength", "standard") or "standard")
        self.throw_strength_select.setCurrentData(current_strength if current_strength in {"gentle", "standard", "strong", "crazy"} else "standard")

        # 弹弓弹射开关
        self.slingshot_check = ToggleSwitch(self)
        self.slingshot_check.setChecked(bool(self.config.get("slingshot_enabled", True)))

        # 多开碰撞设置
        self.collision_enabled_check = ToggleSwitch(self)
        self.collision_enabled_check.setChecked(bool(self.config.get("collision_enabled", True)))
        self.collision_restitution_spin = BrowserDoubleSpinBox(self)
        self.collision_restitution_spin.setRange(0.0, 1.0)
        self.collision_restitution_spin.setSingleStep(0.05)
        self.collision_restitution_spin.setDecimals(2)
        self.collision_restitution_spin.setValue(float(_float_or_default(self.config.get("collision_restitution", 0.82), 0.82, 0.0, 1.0)))
        self.collision_friction_spin = BrowserDoubleSpinBox(self)
        self.collision_friction_spin.setRange(0.0, 0.30)
        self.collision_friction_spin.setSingleStep(0.01)
        self.collision_friction_spin.setDecimals(2)
        self.collision_friction_spin.setValue(float(_float_or_default(self.config.get("collision_friction", 0.08), 0.08, 0.0, 0.30)))
        self.collision_mass_scale_spin = BrowserDoubleSpinBox(self)
        self.collision_mass_scale_spin.setRange(0.5, 2.0)
        self.collision_mass_scale_spin.setSingleStep(0.1)
        self.collision_mass_scale_spin.setDecimals(2)
        self.collision_mass_scale_spin.setValue(float(_float_or_default(self.config.get("collision_mass_scale", 1.0), 1.0, 0.5, 2.0)))
        self.collision_impulse_cap_spin = BrowserDoubleSpinBox(self)
        self.collision_impulse_cap_spin.setRange(1000.0, 12000.0)
        self.collision_impulse_cap_spin.setSingleStep(500.0)
        self.collision_impulse_cap_spin.setDecimals(0)
        self.collision_impulse_cap_spin.setValue(float(_float_or_default(self.config.get("collision_impulse_cap", 9000.0), 9000.0, 1000.0, 12000.0)))
        self.collision_sound_check = ToggleSwitch(self)
        self.collision_sound_check.setChecked(bool(self.config.get("collision_sound_enabled", True)))
        self.collision_sound_volume_spin = BrowserSpinBox(self)
        self.collision_sound_volume_spin.setRange(0, 100)
        self.collision_sound_volume_spin.setSuffix(" %")
        collision_sound_vol = float(self.config.get("collision_sound_volume", 0.70))
        self.collision_sound_volume_spin.setValue(int(round(collision_sound_vol * 100)))

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
        if self.config.instance_id:
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅主桌宠可设置")
        self.dock_icon_check = None
        if sys.platform == "darwin":
            self.dock_icon_check = ToggleSwitch(self)
            self.dock_icon_check.setChecked(bool(self.config.get("show_dock_icon", True)))

        # 点击音效控件群
        self.click_sound_check = ToggleSwitch(self)
        self.click_sound_check.setChecked(bool(self.config.get("click_sound_enabled", True)))
        self.click_sound_picker = ClickSoundPackPicker(
            self.config.get("click_sound_pack"),
            parent=self,
        )
        self.click_sound_volume_spin = BrowserSpinBox(self)
        self.click_sound_volume_spin.setRange(0, 100)
        self.click_sound_volume_spin.setSuffix(" %")
        click_vol = float(self.config.get("click_sound_volume", 0.70))
        self.click_sound_volume_spin.setValue(int(round(click_vol * 100)))

        self.click_sound_preview_btn = QPushButton("试听", self)
        self.click_sound_preview_btn.setIcon(vector_widget_icon(self, "sound", 14))
        self.click_sound_preview_btn.setFixedWidth(72)
        self.click_sound_preview_btn.clicked.connect(self._preview_click_sound)

        self.click_sound_check.toggled.connect(self._update_click_sound_controls)
        # 音效开关即时生效：对话框的批量写回发生在关闭时，但声音开关是即时
        # 听觉反馈——用户关掉后期望立刻静音，而不是等关对话框。
        self.click_sound_check.toggled.connect(self._apply_click_sound_enabled_now)
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
        self.idle_low_fps_check = ToggleSwitch(self)
        self.idle_low_fps_check.setChecked(bool(self.config.get("idle_low_fps_enabled", False)))
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
            image_preview=True,
            parent=self,
        )
        self.self_talk_image_scale_spin = BrowserSpinBox(self)
        self.self_talk_image_scale_spin.setRange(50, 300)
        self.self_talk_image_scale_spin.setSuffix(" %")
        self.self_talk_image_scale_spin.setValue(int(self.config.get("self_talk_image_scale", 100)))
        self.click_talk_bindings_btn = QPushButton("编辑…", self)
        self.click_talk_bindings_btn.setObjectName("clickTalkBindingsButton")
        self.click_talk_bindings_btn.clicked.connect(self._open_click_talk_bindings)

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
        self.dialogue_phrase_edits: dict[str, QPlainTextEdit] = {}
        for key in phrase_keys():
            edit = QPlainTextEdit(self)
            raw_value = configured_phrases.get(key, "")
            if isinstance(raw_value, list):
                edit.setPlainText("\n".join(str(item) for item in raw_value if isinstance(item, str)))
            else:
                edit.setPlainText(str(raw_value or ""))
            edit.setMinimumHeight(48)
            edit.setMaximumHeight(120)
            params = DIALOGUE_KEY_PARAMS.get(key, ())
            if params:
                param_text = "、".join(
                    "{" + item + "}（" + DIALOGUE_PARAMS[item] + "）"
                    for item in params
                )
                placeholder = "留空使用基础模式台词；本事件支持：" + param_text
            else:
                placeholder = "留空使用基础模式台词；本事件无可替换参数"
            edit.setPlaceholderText(placeholder)
            self.dialogue_phrase_edits[key] = edit

        self.dialogue_template_import_edit = QPlainTextEdit(self)
        self.dialogue_template_import_edit.setObjectName("dialogueTemplateImportEdit")
        self.dialogue_template_import_edit.setPlaceholderText(
            "粘贴 persona-phrases/v1 JSON 模板到这里，然后点击“导入模板”"
        )
        self.dialogue_template_import_edit.setMinimumHeight(92)
        self.dialogue_template_import_edit.setMaximumHeight(180)
        self.dialogue_template_export_btn = QPushButton("一键复制模板", self)
        self.dialogue_template_export_btn.setObjectName("dialogueTemplateExportButton")
        self.dialogue_template_export_btn.clicked.connect(self._export_dialogue_template)
        self.dialogue_template_import_btn = QPushButton("导入模板", self)
        self.dialogue_template_import_btn.setObjectName("dialogueTemplateImportButton")
        self.dialogue_template_import_btn.clicked.connect(self._import_dialogue_template_json)
        self.dialogue_template_actions = QWidget(self)
        dialogue_template_actions_layout = QVBoxLayout(self.dialogue_template_actions)
        dialogue_template_actions_layout.setContentsMargins(0, 0, 0, 0)
        dialogue_template_actions_layout.setSpacing(6)
        dialogue_template_actions_layout.addWidget(self.dialogue_template_import_edit)
        dialogue_template_buttons = QHBoxLayout()
        dialogue_template_buttons.setContentsMargins(0, 0, 0, 0)
        dialogue_template_buttons.addWidget(self.dialogue_template_export_btn)
        dialogue_template_buttons.addWidget(self.dialogue_template_import_btn)
        dialogue_template_actions_layout.addLayout(dialogue_template_buttons)
        self.agent_sound_check = ToggleSwitch(self)
        self.agent_sound_check.setChecked(bool(agent_link_cfg.get("sound_enabled", False)))

        # 辅助构建包含“开关+路径选择+试听”的组合控件
        def _build_agent_event_row(evt_key: str, default_builtin: str) -> tuple[QWidget, ToggleSwitch, ResourcePathPicker, QPushButton]:
            toggle = ToggleSwitch(self)
            toggle.setChecked(bool(agent_link_cfg.get(f"sound_{evt_key}_enabled", True)))
            path_val = str(agent_link_cfg.get(f"sound_{evt_key}_path") or default_builtin)
            picker = ResourcePathPicker(path_val, name_filter=AUDIO_NAME_FILTER, parent=self)
            preview_btn = QPushButton("试听", self)
            preview_btn.setIcon(vector_widget_icon(self, "sound", 14))
            preview_btn.setFixedWidth(72)
            preview_btn.clicked.connect(lambda _, k=evt_key: self._preview_agent_sound(k))
            container = ResponsiveToggleActionRow(toggle, picker, preview_btn, self)
            return container, toggle, picker, preview_btn

        (self.agent_sound_start_widget, self.agent_sound_start_check,
         self.agent_sound_start_picker, self.agent_sound_start_preview) = _build_agent_event_row("start", "builtin:agent-start")

        (self.agent_sound_done_widget, self.agent_sound_done_check,
         self.agent_sound_done_picker, self.agent_sound_done_preview) = _build_agent_event_row("done", "builtin:agent-done")

        (self.agent_sound_error_widget, self.agent_sound_error_check,
         self.agent_sound_error_picker, self.agent_sound_error_preview) = _build_agent_event_row("error", "builtin:agent-error")

        self.agent_sound_volume_spin = BrowserSpinBox(self)
        self.agent_sound_volume_spin.setRange(0, 100)
        self.agent_sound_volume_spin.setSuffix(" %")
        agent_vol = float(agent_link_cfg.get("sound_volume", 0.65))
        self.agent_sound_volume_spin.setValue(int(round(agent_vol * 100)))

        self.agent_sound_cooldown_spin = BrowserDoubleSpinBox(self)
        self.agent_sound_cooldown_spin.setRange(0.0, 30.0)
        self.agent_sound_cooldown_spin.setSingleStep(0.5)
        self.agent_sound_cooldown_spin.setDecimals(1)
        self.agent_sound_cooldown_spin.setSuffix(" 秒")
        self.agent_sound_cooldown_spin.setValue(float(agent_link_cfg.get("sound_cooldown_seconds", 2.0)))

        self.agent_sound_check.toggled.connect(self._update_agent_sound_controls)
        self.agent_sound_check.toggled.connect(self._apply_agent_sound_enabled_now)
        self.agent_sound_start_check.toggled.connect(lambda: self._update_agent_sound_subcontrols())
        self.agent_sound_done_check.toggled.connect(lambda: self._update_agent_sound_subcontrols())
        self.agent_sound_error_check.toggled.connect(lambda: self._update_agent_sound_subcontrols())

        # 待办提醒：偏好两键（条目在右键菜单「待办提醒」面板中管理）
        self.todo_reminder_check = ToggleSwitch(self)
        self.todo_reminder_check.setChecked(bool(self.config.get("todo_reminder_enabled", True)))
        self.todo_reminder_lead_spin = BrowserSpinBox(self)
        self.todo_reminder_lead_spin.setRange(0, 60)
        self.todo_reminder_lead_spin.setSuffix(" 分钟")
        self.todo_reminder_lead_spin.setValue(int(self.config.get("todo_reminder_lead_minutes", 5) or 0))

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
        self.egg_image_dir_picker = ResourcePathPicker(
            str(image_dir.resolve()), directory=True, image_preview=True, parent=self,
        )

        # 灵动岛
        island_cfg = self.config.get("dynamic_island", {})
        if not isinstance(island_cfg, dict):
            island_cfg = {}
        self.island_enabled_check = ToggleSwitch(self)
        self.island_enabled_check.setChecked(bool(island_cfg.get("enabled", False)))
        self.island_icon_check = ToggleSwitch(self)
        self.island_icon_check.setChecked(bool(island_cfg.get("show_icon", True)))
        self.island_name_check = ToggleSwitch(self)
        self.island_name_check.setChecked(bool(island_cfg.get("show_name", True)))
        self.island_info_check = ToggleSwitch(self)
        self.island_info_check.setChecked(bool(island_cfg.get("show_info", True)))
        self.island_status_check = ToggleSwitch(self)
        self.island_status_check.setChecked(bool(island_cfg.get("show_status", True)))
        self.island_info_mode_select = ModernSelect(self, width=160)
        for label, value in (
            ("当前时间", "time"),
            ("余额峰谷", "balance_tier"),
            ("余额数值", "balance"),
            ("自定义短文本", "custom"),
        ):
            self.island_info_mode_select.addItem(label, value)
        self.island_info_mode_select.setCurrentData(str(island_cfg.get("info_mode") or "time"))
        self.island_style_select = ModernSelect(self, width=160)
        for label, value in (
            ("黑色", "dark"),
            ("白色", "light"),
            ("玻璃质感", "glass"),
        ):
            self.island_style_select.addItem(label, value)
        self.island_style_select.setCurrentData(str(island_cfg.get("style") or "dark"))
        self.island_icon_select = ModernSelect(self, width=160)
        for emoji in ("🐳", "🐟", "🐙", "🦭", "🐧", "🐱", "🐶", "🌟", "⚡", "❤️"):
            self.island_icon_select.addItem(emoji, emoji)
        self.island_icon_select.setCurrentData(str(island_cfg.get("icon") or "🐳"))
        self.island_custom_text_edit = _line_edit(str(island_cfg.get("custom_text") or ""), width=220)

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
            "self_talk_texts", "self_talk_images", "self_talk_image_scale", "click_self_talk",
            "click_talk_bindings",
        )
        self._set_setting_rows_visible(keys, enabled)

    def _update_island_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible((
            "dynamic_island_icon", "dynamic_island_name", "dynamic_island_info",
            "dynamic_island_status", "dynamic_island_info_mode",
            "dynamic_island_style", "dynamic_island_icon_value",
            "dynamic_island_custom_text",
        ), enabled, dependency="island_enabled")
        self._update_island_icon_controls(self.island_icon_check.isChecked())
        self._update_island_info_controls(self.island_info_check.isChecked())

    def _update_island_icon_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible(
            ("dynamic_island_icon_value",),
            enabled,
            dependency="island_show_icon",
        )

    def _update_island_info_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible(
            ("dynamic_island_info_mode", "dynamic_island_custom_text"),
            enabled,
            dependency="island_show_info",
        )
        self._update_island_custom_text()

    def _update_island_custom_text(self, _index: int | None = None) -> None:
        self._set_setting_rows_visible(
            ("dynamic_island_custom_text",),
            self.island_info_mode_select.currentData() == "custom",
            dependency="island_info_mode",
        )

    def _update_egg_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible(
            ("egg_title", "egg_hint", "egg_avatar", "egg_image_dir"),
            enabled,
            dependency="egg_enabled",
        )

    def _update_collision_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible((
            "collision_sound_enabled", "collision_restitution", "collision_friction",
            "collision_mass_scale", "collision_impulse_cap", "collision_sound_volume",
        ), enabled, dependency="collision_enabled")
        self._update_collision_sound_controls(self.collision_sound_check.isChecked())

    def _update_collision_sound_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible(
            ("collision_sound_volume",),
            enabled,
            dependency="collision_sound_enabled",
        )

    def _update_proactive_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible((
            "proactive_dry_run", "proactive_preset", "proactive_dwell",
            "proactive_cooldown", "proactive_min_interval", "proactive_daily_cap",
            "proactive_require_idle", "proactive_idle_seconds", "proactive_through",
            "proactive_pre_cue", "proactive_free", "proactive_whitelist",
            "proactive_whitelist_add", "proactive_memory_clear",
        ), enabled, dependency="proactive_enabled")
        self._update_proactive_idle_controls(self.pro_idle_check.isChecked())

    def _update_proactive_idle_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible(
            ("proactive_idle_seconds",),
            enabled,
            dependency="proactive_require_idle",
        )

    def _set_setting_rows_visible(
        self,
        keys: tuple[str, ...],
        visible: bool,
        *,
        dependency: str = "parent",
    ) -> None:
        """Show dependent settings as a complete group and repair card dividers."""
        cards: set[SettingsCard] = set()
        sections: set[SettingsSection] = set()
        for key in keys:
            row = self.findChild(SettingRow, f"settingRow_{key}")
            if row is None:
                continue
            dependencies = getattr(row, "_visibility_dependencies", {})
            dependencies[dependency] = bool(visible)
            row._visibility_dependencies = dependencies
            row.setVisible(all(dependencies.values()))
            card = row.parentWidget()
            if isinstance(card, SettingsCard):
                cards.add(card)
                section = card.parentWidget()
                if isinstance(section, SettingsSection):
                    sections.add(section)
        for section in sections:
            section.refresh_dependency_visibility()
        for card in cards:
            card.refresh_separators()

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

    def _open_click_talk_bindings(self) -> None:
        from .click_talk_dialog import ClickTalkBindingsDialog

        click_names = None
        parent = self.parent()
        if parent is not None and hasattr(parent, "clicks") and parent.clicks:
            click_names = list(parent.clicks)
        dialog = ClickTalkBindingsDialog(self.config, click_names=click_names, parent=self)
        dialog.exec()

    def _preview_click_sound(self) -> None:
        """试听当前选择的点击音效配置（不保存配置）。"""
        from .click_sound import choose_sound, play_sound, resolve_click_sound_candidates

        pack = self.click_sound_picker.value()
        candidates = resolve_click_sound_candidates(pack, self.config.dir)
        sound_file = choose_sound(candidates)
        if sound_file:
            vol = float(self.click_sound_volume_spin.value()) / 100.0
            play_sound(sound_file, volume=vol)

    def _preview_agent_sound(self, event_name: str) -> None:
        """试听当前填写的 Agent 音效（不保存配置、不触发 Agent 业务逻辑）。"""
        from .click_sound import play_sound, resolve_builtin_sound

        picker = {
            "start": self.agent_sound_start_picker,
            "done": self.agent_sound_done_picker,
            "error": self.agent_sound_error_picker,
        }.get(event_name)
        if picker is None:
            return
        path_str = picker.text().strip()
        if not path_str:
            path_str = f"builtin:agent-{event_name}"

        target = None
        if path_str.startswith("builtin:"):
            target = resolve_builtin_sound(path_str)
        else:
            p = Path(path_str).expanduser()
            if p.is_file():
                target = p

        if target:
            vol = float(self.agent_sound_volume_spin.value()) / 100.0
            play_sound(target, volume=vol)

    def _import_dialogue_template(self) -> None:
        """导入默认台词模板：将所有预设台词填充到自定义编辑框。"""
        defaults = default_phrases()
        for key, edit in self.dialogue_phrase_edits.items():
            if key in defaults:
                edit.setPlainText(defaults[key])
        QMessageBox.information(self, "导入成功", "已导入全部默认台词模板。")

    def _import_dialogue_template_json(self) -> None:
        """Import a complete persona template from the inline JSON editor."""
        raw = self.dialogue_template_import_edit.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "导入失败", "请先粘贴 JSON 模板。")
            return
        try:
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError("模板根节点必须是 JSON 对象")
            template_name = str(document.get("template", "") or "")
            if template_name and not template_name.startswith("persona-phrases/"):
                raise ValueError("不是兼容的 persona-phrases 模板")
            phrases = document.get("phrases")
            if not isinstance(phrases, dict):
                phrases = document.get("dialogue_phrases")
            if not isinstance(phrases, dict):
                raise ValueError("模板缺少 phrases 对象")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "导入失败", f"JSON 模板无效：{exc}")
            return
        for key, edit in self.dialogue_phrase_edits.items():
            value = phrases.get(key, "")
            if isinstance(value, list):
                edit.setPlainText("\n".join(str(item) for item in value if isinstance(item, str)))
            elif value is not None:
                edit.setPlainText(str(value))
        # entries[].phrases 兜底：顶层 phrases 缺失/为空的 key 用 entries 补齐
        #（顶层有内容时以顶层为准，不被 entries 覆盖）。
        entries = document.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                edit = self.dialogue_phrase_edits.get(str(entry.get("key") or "").strip())
                if edit is None:
                    continue
                current = phrases.get(entry["key"])
                has_top = (
                    (isinstance(current, list) and any(isinstance(i, str) and i.strip() for i in current))
                    or (isinstance(current, str) and current.strip())
                )
                if has_top or edit.toPlainText().strip():
                    continue
                value = entry.get("phrases")
                if isinstance(value, list):
                    text = "\n".join(str(item) for item in value if isinstance(item, str) and item.strip())
                    if text:
                        edit.setPlainText(text)
        self.dialogue_mode_select.setCurrentData("custom")
        self.dialogue_template_import_edit.clear()
        QMessageBox.information(self, "导入成功", "已导入全部弹窗内容模板；点击“保存并退出”后生效。")

    def _dialogue_phrase_values(self) -> dict[str, list[str]]:
        return {
            key: [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]
            for key, edit in self.dialogue_phrase_edits.items()
        }

    def _current_dialogue_template(self) -> dict:
        return build_persona_template({
            "dialogue_mode": self.dialogue_mode_select.currentData() or "legacy",
            "dialogue_phrases": self._dialogue_phrase_values(),
        })

    def _export_dialogue_template(self) -> None:
        """Export the complete current template to the clipboard (no file dialog)."""
        try:
            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("系统剪贴板不可用")
            clipboard.setText(json.dumps(self._current_dialogue_template(), ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入系统剪贴板：{exc}")
            return
        QMessageBox.information(
            self, "导出成功",
            "模板已复制到剪贴板：可直接粘贴给 AI 依角色卡改写，"
            "或粘贴回「导入模板」输入框一键导回。",
        )

    def _update_click_sound_controls(self, enabled: bool) -> None:
        for row_key in ("click_sound_pack", "click_sound_volume", "click_sound_preview"):
            row = self.findChild(SettingRow, f"settingRow_{row_key}")
            if row is not None:
                row.setVisible(bool(enabled))
                card = row.parentWidget()
                if isinstance(card, SettingsCard):
                    card.refresh_separators()

    def _update_agent_sound_controls(self, enabled: bool) -> None:
        """Agent 音效总开关联动控制整组子项可见性/可用性。"""
        for row_key in ("agent_sound_start", "agent_sound_done", "agent_sound_error", "agent_sound_volume", "agent_sound_cooldown"):
            row = self.findChild(SettingRow, f"settingRow_{row_key}")
            if row is not None:
                row.setVisible(bool(enabled))
                card = row.parentWidget()
                if isinstance(card, SettingsCard):
                    card.refresh_separators()
        if enabled:
            self._update_agent_sound_subcontrols()

    def _update_agent_sound_subcontrols(self) -> None:
        """单事件关闭时保留开关，仅隐藏其路径选择器和试听按钮。"""
        for toggle, picker, preview in (
            (self.agent_sound_start_check, self.agent_sound_start_picker, self.agent_sound_start_preview),
            (self.agent_sound_done_check, self.agent_sound_done_picker, self.agent_sound_done_preview),
            (self.agent_sound_error_check, self.agent_sound_error_picker, self.agent_sound_error_preview),
        ):
            visible = toggle.isChecked()
            picker.setVisible(visible)
            preview.setVisible(visible)

    def _update_translucency_controls(self, enabled: bool) -> None:
        self._set_setting_rows_visible(("menu_opacity",), enabled)

    def _apply_agent_sound_enabled_now(self, checked: bool) -> None:
        """音效总开关即时生效，不等对话框关闭（合并写回，不动其他 agent_link 键）。"""
        agent_cfg = dict(self.config.get("agent_link", {}))
        agent_cfg["sound_enabled"] = bool(checked)
        self.config.set("agent_link", agent_cfg)
        self.config.save()

    def _apply_click_sound_enabled_now(self, checked: bool) -> None:
        """点击音效开关即时生效，不等对话框关闭。"""
        self.config.set("click_sound_enabled", bool(checked))
        self.config.save()

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
        if not getattr(self, "_initial_focus_assigned", False):
            self._initial_focus_assigned = True
            self.sidebar.setFocus(Qt.FocusReason.OtherFocusReason)

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
        content_max_width = int(content.property("contentMaxWidth") or 960)
        page = _SettingsPageShell(content_max_width, self.pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 28, 20)
        layout.setSpacing(12)
        heading_host = QWidget(page)
        heading_host.setObjectName("pageHeader")
        heading_host.setMaximumWidth(content_max_width)
        heading_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        heading_layout = QVBoxLayout(heading_host)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(0)
        heading = QLabel(title, heading_host)
        heading.setObjectName("pageTitle")
        heading_layout.addWidget(heading)
        page.heading_host = heading_host
        layout.addWidget(heading_host, 0, Qt.AlignmentFlag.AlignHCenter)
        scroll = QScrollArea(page)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        content.setMaximumWidth(content_max_width)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def _add_page(self, label: str, icon_name: str, page: QWidget) -> None:
        item = QListWidgetItem(vector_widget_icon(self, icon_name, 16), label)
        item.setSizeHint(QSize(0, 34))
        self.sidebar.addItem(item)
        self.pages.addWidget(page)

    def _rebuild_domain_navigation(self) -> None:
        """Move existing rows into stable capability domains without cloning state."""
        old_pages = {
            self.sidebar.item(index).text(): self.pages.widget(index)
            for index in range(self.pages.count())
        }
        all_rows = list(self.findChildren(SettingRow))
        claimed: set[SettingRow] = set()

        def claim(*setting_ids: str) -> list[SettingRow]:
            rows = []
            for setting_id in setting_ids:
                row = self.findChild(SettingRow, f"settingRow_{setting_id}")
                if row is not None and row not in claimed:
                    claimed.add(row)
                    rows.append(row)
            return rows

        def claim_prefix(prefix: str) -> list[SettingRow]:
            rows = [row for row in all_rows if row.objectName().startswith(f"settingRow_{prefix}") and row not in claimed]
            claimed.update(rows)
            return rows

        def page_content(sections) -> QWidget:
            content = QWidget(self)
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(18)
            for section in sections:
                title, rows, *options = section
                rows = [row for row in rows if row is not None]
                if rows:
                    layout.addWidget(SettingsSection(
                        title,
                        rows,
                        content,
                        advanced=bool(options and options[0]),
                    ))
            layout.addStretch(1)
            return content

        general = page_content([
            ("应用启动", claim("autostart")),
            ("窗口与系统", claim("dock_icon", "on_top", "auto_hide_fullscreen", "cursor_hidden_passthrough", "stream_capture")),
        ])
        collision_primary = claim("collision_enabled", "collision_sound_enabled")
        collision_advanced = claim(
            "collision_restitution",
            "collision_friction",
            "collision_mass_scale",
            "collision_impulse_cap",
            "collision_sound_volume",
        )
        pet = page_content([
            ("显示", claim("scale", "pet_opacity")),
            ("动画与移动", claim("playback_speed", "animation_gap", "idle_low_fps", "no_move", "music_sing")),
            ("拖拽与弹射", claim("drag_physics", "throw_strength", "slingshot_enabled", "lock_position", "shift_drag")),
            ("多开碰撞", collision_primary),
            ("碰撞参数（高级）", collision_advanced, True),
        ])
        interaction = page_content([
            ("输入", claim("mouse_through")),
            ("点击反馈", claim_prefix("click_")),
            ("自言自语", claim("self_talk_bubble_style", "self_talk", "self_talk_duration", "self_talk_min", "self_talk_max", "self_talk_texts", "self_talk_images", "self_talk_image_scale")),
            ("表达风格", claim_prefix("dialogue_")),
        ])
        # click_talk_bindings shares the click_ prefix and remains in interaction.
        menu = SettingsTabContainer(self)
        menu.addTab("layout", "菜单编排", page_content([
            ("内容与布局", claim("menu_template", "context_menu_layout")),
        ]))
        menu.addTab("launcher", "快捷启动", page_content([
            ("已配置应用", claim("quick_launch_apps")),
        ]))
        menu.addTab("appearance", "外观", page_content([
            ("菜单外观", claim("menu_theme", "menu_density", "menu_radius", "menu_font", "menu_font_size", "menu_translucent", "menu_opacity")),
            ("高级配色", claim(
                "light_background", "light_foreground", "light_hover",
                "dark_background", "dark_foreground", "dark_hover",
            ), True),
            ("彩蛋入口", claim_prefix("egg_")),
        ]))
        menu.setProperty("contentMaxWidth", 1240)
        island_rows = list(old_pages.get("灵动岛", QWidget()).findChildren(SettingRow))
        claimed.update(island_rows)
        desktop_components = page_content([("桌面胶囊（灵动岛）", island_rows)])

        ai_sections = None
        if self.ai_page is not None:
            balance_rows = claim_prefix("balance_")
            appearance_rows = claim("chat_ui_style", "chat_background", "chat_background_file", "chat_background_opacity", "chat_background_fill", "modern_chat_card_opacity")
            ai_sections = page_content([
                ("API 列表", claim("provider_list")),
                ("模型与连接", claim(
                    "provider_name", "api_url", "model", "api_key",
                    "system_prompt", "connection_test",
                )),
                ("系统通知", claim("system_notifications_enabled")),
                ("视觉能力", claim(
                    "vision_same", "vision_model", "vision_url", "vision_key",
                )),
                ("生成参数（高级）", claim(
                    "timeout", "temperature", "max_tokens", "skip_ssl",
                ), True),
                ("对话窗口", appearance_rows),
                ("余额与服务状态", balance_rows),
            ])
            ai_sections.setObjectName("settingsDomain_ai")
            # Keep the control owner alive for save/dependency behavior, but
            # visible rows now belong directly to the shared domain layout.
            self.ai_page.setParent(self)
            self.ai_page.hide()

        proactive_rows = list(old_pages.get("主动识屏", QWidget()).findChildren(SettingRow))
        claimed.update(proactive_rows)
        watchdog_rows = list(self.watchdog_page.findChildren(SettingRow))
        claimed.update(watchdog_rows)
        automation = page_content([
            ("Agent 文案", claim_prefix("agent_thinking_")),
            ("Agent 提示音", claim_prefix("agent_sound_")),
            ("待办提醒", claim("todo_reminder_enabled", "todo_reminder_lead_minutes")),
            ("主动感知", proactive_rows),
            ("循环检测", watchdog_rows),
        ])

        # Preserve any newly added row until it receives an explicit domain decision.
        leftovers = [
            row for row in all_rows
            if row not in claimed
            and (self.ai_page is None or not self.ai_page.isAncestorOf(row))
        ]
        if leftovers:
            layout = automation.layout()
            layout.insertWidget(max(0, layout.count() - 1), SettingsSection("待分类（开发期）", leftovers, automation))

        while self.pages.count():
            self.pages.removeWidget(self.pages.widget(0))
        self.sidebar.clear()
        domain_content = {
            "常规": general,
            "桌宠": pet,
            "互动": interaction,
            "菜单": menu,
            "桌面组件": desktop_components,
            "AI 与对话": ai_sections,
            "自动化与联动": automation,
        }
        for label, icon in SETTINGS_DOMAIN_NAV:
            content = domain_content.get(label)
            if content is None:
                continue
            self._add_page(label, icon, self._page_shell(label, content))
        for page in old_pages.values():
            page.deleteLater()

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
        ancestor = row.parentWidget()
        while ancestor is not None and ancestor is not page:
            if isinstance(ancestor, SettingsTabContainer):
                ancestor.activate_for_descendant(row)
                break
            ancestor = ancestor.parentWidget()
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

    def _apply_selected_theme(self, *_args) -> None:
        theme = str(self.menu_theme_select.currentData() or "system")
        dark = theme == "dark" or (theme == "system" and _system_dark())
        self.setProperty("settingsDark", dark)
        self.setStyleSheet(_settings_stylesheet(theme))
        for control in self.findChildren(ModernSelect):
            if control._popup is not None:
                control._popup.setStyleSheet(control.popupStyleSheet())
            control.update()
        for popup in self.findChildren(QMenu):
            if popup.property("settingsPopup"):
                configure_settings_action_popup(popup)
        for control in self.findChildren(ToggleSwitch):
            control.update()

    def _stylesheet(self) -> str:
        theme = self.menu_theme_select.currentData() if hasattr(self, "menu_theme_select") else "system"
        return _settings_stylesheet(str(theme or "system"))



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
        if not self._write_config():
            return
        self._saved_via_button = True
        self._apply_autostart()
        self.settings_saved.emit()
        self.accept()

    def _write_config(self) -> bool:
        """把当前控件值写入 config 并落盘（按钮与直接关闭共用）。

        保存前从磁盘重读：吸收外部对本对话框未暴露字段的改动。
        已知限制：已暴露字段仍是 last-writer-wins（对话框获胜）。
        返回是否成功落盘；失败时提示用户。
        """
        menu_layout_value = self.menu_layout_editor.value()
        menu_validation = resolve_menu_layout(
            menu_layout_value,
            registered_actions=MENU_ACTIONS.ids,
            available_actions=MENU_ACTIONS.ids,
        )
        if menu_validation.source == "fallback":
            QMessageBox.warning(
                self,
                "菜单布局无效",
                "菜单布局未保存：" + "、".join(menu_validation.diagnostics),
            )
            return False
        self.config.reload()
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
        self.config.set("throw_strength", str(self.throw_strength_select.currentData() or "standard"))
        self.config.set("slingshot_enabled", self.slingshot_check.isChecked())
        self.config.set("collision_enabled", self.collision_enabled_check.isChecked())
        self.config.set("collision_restitution", self.collision_restitution_spin.value())
        self.config.set("collision_friction", self.collision_friction_spin.value())
        self.config.set("collision_mass_scale", self.collision_mass_scale_spin.value())
        self.config.set("collision_impulse_cap", self.collision_impulse_cap_spin.value())
        self.config.set("collision_sound_enabled", self.collision_sound_check.isChecked())
        self.config.set("collision_sound_volume", float(self.collision_sound_volume_spin.value()) / 100.0)
        self.config.set("lock_position", self.lock_position_check.isChecked())
        self.config.set("shift_drag", self.shift_drag_check.isChecked())
        self.config.set("pet_opacity", int(self.pet_opacity_spin.value()))
        self.config.set("click_sound_enabled", self.click_sound_check.isChecked())
        self.config.set("click_sound_pack", self.click_sound_picker.value())
        self.config.set("click_sound_volume", float(self.click_sound_volume_spin.value()) / 100.0)
        warm_click_sound_effects(
            self.config.get("click_sound_pack"),
            data_dir=self.config.dir,
        )
        existing_island = self.config.get("dynamic_island", {})
        if not isinstance(existing_island, dict):
            existing_island = {}
        self.config.set("dynamic_island", {
            "enabled": self.island_enabled_check.isChecked(),
            "show_icon": self.island_icon_check.isChecked(),
            "show_name": self.island_name_check.isChecked(),
            "show_info": self.island_info_check.isChecked(),
            "info_mode": str(self.island_info_mode_select.currentData() or "time"),
            "custom_text": self.island_custom_text_edit.text().strip(),
            "show_status": self.island_status_check.isChecked(),
            "style": str(self.island_style_select.currentData() or "dark"),
            "icon": str(self.island_icon_select.currentData() or "🐳"),
            "x": existing_island.get("x"),
            "y": existing_island.get("y"),
        })
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
        if self.cursor_hidden_passthrough_check is not None:
            self.config.set("cursor_hidden_passthrough", self.cursor_hidden_passthrough_check.isChecked())
        if self.stream_capture_check is not None:
            self.config.set("stream_capture_mode", self.stream_capture_check.isChecked())
        self.config.set("playback_speed", float(self.speed_select.currentData()))
        self.config.set("animation_gap_seconds", self.gap_spin.value())
        self.config.set("idle_low_fps_enabled", self.idle_low_fps_check.isChecked())
        self.config.set("self_talk_enabled", self.self_talk_check.isChecked())
        self.config.set("self_talk_bubble_style", self.bubble_style_select.currentData())
        self.config.set("self_talk_min_interval", minimum)
        self.config.set("self_talk_max_interval", maximum)
        self.config.set("self_talk_duration_seconds", self.self_talk_duration_spin.value())
        self.config.set("self_talk_texts", texts or list(DEFAULT_SELF_TALK_TEXTS))
        self.config.set("self_talk_image_dir", self.self_talk_image_dir_picker.text())
        self.config.set("self_talk_image_scale", self.self_talk_image_scale_spin.value())
        # Agent 联动：自定义 thinking 文案与音效（合并写回，不覆盖 agent_link 其他开关）
        self.config.set("dialogue_mode", str(self.dialogue_mode_select.currentData() or "legacy"))
        self.config.set("dialogue_phrases", {
            key: lines for key, lines in self._dialogue_phrase_values().items() if lines
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

        # Agent 联动音效写回
        agent_cfg["sound_enabled"] = self.agent_sound_check.isChecked()
        agent_cfg["sound_start_enabled"] = self.agent_sound_start_check.isChecked()
        agent_cfg["sound_start_path"] = self.agent_sound_start_picker.text().strip() or "builtin:agent-start"
        agent_cfg["sound_done_enabled"] = self.agent_sound_done_check.isChecked()
        agent_cfg["sound_done_path"] = self.agent_sound_done_picker.text().strip() or "builtin:agent-done"
        agent_cfg["sound_error_enabled"] = self.agent_sound_error_check.isChecked()
        agent_cfg["sound_error_path"] = self.agent_sound_error_picker.text().strip() or "builtin:agent-error"
        agent_cfg["sound_volume"] = float(self.agent_sound_volume_spin.value()) / 100.0
        agent_cfg["sound_cooldown_seconds"] = float(self.agent_sound_cooldown_spin.value())

        self.config.set("agent_link", agent_cfg)
        self.config.set("todo_reminder_enabled", self.todo_reminder_check.isChecked())
        self.config.set("todo_reminder_lead_minutes", int(self.todo_reminder_lead_spin.value()))
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
        self.config.set("context_menu_template", self.menu_template_select.currentData())
        default_menu_nodes = load_default_menu_layout().get("nodes", [])
        self.config.set(
            "context_menu_layout",
            None if menu_layout_value.get("nodes") == default_menu_nodes else menu_layout_value,
        )
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

    def reject(self) -> None:  # noqa: N802 - Qt API
        """Esc 路径与关闭按钮一致：保存设置并应用开机自启。"""
        if not getattr(self, "_saved_via_button", False):
            try:
                self._write_config()
                self._apply_autostart()
            except Exception:
                logging.exception("Esc 关闭设置时保存配置失败")
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """直接关闭（X / Esc）时同样落盘，避免修改丢失。

        设置项都是即时型偏好，与右键菜单/托盘修改的写入时机保持一致；
        已走「保存并退出」则跳过（防重复写入）。
        """
        if not getattr(self, "_saved_via_button", False):
            try:
                if not self._write_config():
                    event.ignore()
                    return
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
QWidget#settingsTaskTabBar {
    background: #292930;
    border: none;
    border-radius: 8px;
}
QPushButton#settingsTaskTab {
    min-height: 26px;
    padding: 0 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #aaaab3;
}
QPushButton#settingsTaskTab:hover { background: #33333b; color: #f0f0f5; }
QPushButton#settingsTaskTab:checked {
    background: #41414b;
    border-color: #50505b;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#settingsTaskTab:focus { border: 2px solid #0a84ff; }
QLabel#pageTitle { color: #f0f0f5; }
QLabel#sectionTitle { color: #d8d8e0; }
QFrame#settingsCard { background: #2a2a30; border: 1px solid #3a3a42; }
QFrame#cardSeparator { background: #33333a; }
QLabel#settingLabel { color: #e0e0e6; }
QLabel#settingHint { color: #9a9aa3; }
QLabel#quickLaunchName { color: #e0e0e6; }
QLabel#quickLaunchDetail, QLabel#quickLaunchCount, QLabel#quickLaunchEmpty,
QLabel#menuLayoutEditorLabel, QLabel#menuLayoutPreviewLabel,
QLabel#menuLayoutEditorHint { color: #a8a8b0; }
QLabel#quickLaunchEmpty { background: #26262c; border-color: #3c3c44; }
QFrame#menuLayoutEditorPanel, QFrame#menuLayoutPreviewPanel {
    background: #26262c; border: 1px solid #3c3c44; border-radius: 10px;
}
QFrame#imagePreviewDrawer {
    background: #242429; border: none; border-left: 1px solid #44444d;
}
QScrollArea#imagePreviewScroll, QScrollArea#imagePreviewScroll > QWidget > QWidget,
QWidget#imageMasonryFlow { background: transparent; }
QLabel#imagePreviewTitle { color: #f0f0f5; font-size: 16px; font-weight: 600; }
QLabel#imagePreviewCount, QLabel#imagePreviewPath { color: #9999a2; }
QLabel#imagePreviewEmpty { color: #9999a2; }
QPushButton#imagePreviewClose { background: transparent; border: none; font-size: 20px; }
QPushButton#imagePreviewClose:hover { background: #393940; }
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #66666e; }
SettingRow[searchMatch="true"] { background: #2c3a4e; }
QListWidget#quickLaunchList { background: #26262c; border: 1px solid #3c3c44; }
QListWidget#quickLaunchList::item:selected { background: #3a3a46; color: #ffffff; }
QTreeWidget#menuLayoutTree, QTreeWidget#menuLayoutPreview {
    background: #2a2a30;
    border-color: #3a3a42;
}
QTreeWidget#menuLayoutTree::item:hover { background: #303036; }
QTreeWidget#menuLayoutTree::item:selected { background: #3a3a46; color: #ffffff; }
QTreeWidget#menuLayoutTree QHeaderView::section {
    background: #303036;
    border-bottom-color: #404048;
    color: #a8a8b0;
}
QLabel#menuLayoutPreviewLabel { color: #a8a8b0; }
QMenu { background: #2a2a30; color: #e4e4e9; border: 1px solid #45454f; }
QMenu::item:selected { background: #3a3a46; }
QPushButton { background: #3a3a42; border: 1px solid #4a4a54; color: #e4e4e9; }
QPushButton:hover { background: #44444e; }
QPushButton#advancedSectionToggle {
    min-height: 40px; padding: 0 38px 0 14px; text-align: left;
    background: #2a2a30; border: 1px solid #3a3a42; border-radius: 10px;
    color: #e4e4e9; font-size: 13px; font-weight: 600;
}
QPushButton#advancedSectionToggle:hover { background: #303036; border-color: #45454d; }
QPushButton#advancedSectionToggle:focus { border: 2px solid #0a84ff; }
QLabel#disclosureChevron { color: #a8a8b0; font-size: 18px; background: transparent; }
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


def _settings_stylesheet(theme: str = "system") -> str:
    """浅色基础 QSS + 显式控件文字色补丁；深色系统时追加深色覆盖段。"""
    light_patch = """
        QPushButton { color: #202020; }
        QToolButton { color: #202020; }
        QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #202020; }
    """
    base = _LIGHT_SETTINGS_STYLESHEET + light_patch
    dark = theme == "dark" or (theme == "system" and _system_dark())
    if not dark:
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
QWidget#aiSettingsContent { background: transparent; }
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
QWidget#settingsTaskTabBar {
    background: #f0f1f3;
    border: none;
    border-radius: 8px;
}
QPushButton#settingsTaskTab {
    min-height: 26px;
    padding: 0 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #60646a;
}
QPushButton#settingsTaskTab:hover { background: #e5e7ea; color: #202020; }
QPushButton#settingsTaskTab:checked {
    background: #ffffff;
    border-color: #d6d9de;
    color: #202020;
    font-weight: 600;
}
QPushButton#settingsTaskTab:focus { border: 2px solid #0a84ff; }
QListWidget#settingsSidebar::item:selected {
    background: #e3e5e8;
    color: #171717;
}
QLabel#pageTitle {
    font-size: 22px;
    font-weight: 600;
    color: #171717;
}
QLabel#sectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: #2b2b2b;
}
QFrame#settingsCard {
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 12px;
}
QFrame#cardSeparator {
    background: #eceef1;
    border: none;
    margin-left: 14px;
    margin-right: 14px;
}
QLabel#settingLabel {
    font-size: 13px;
    font-weight: 500;
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
QLabel#quickLaunchCount, QLabel#menuLayoutEditorLabel, QLabel#menuLayoutPreviewLabel,
QLabel#menuLayoutEditorHint {
    color: #6f7378;
    font-size: 12px;
    font-weight: 500;
    padding-left: 2px;
}
QLabel#quickLaunchName { color: #252525; font-size: 13px; font-weight: 500; }
QLabel#quickLaunchDetail { color: #777777; font-size: 11px; }
QLabel#quickLaunchEmpty {
    color: #777777;
    background: #fbfbfb;
    border: 1px dashed #d9d9d9;
    border-radius: 8px;
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
QLabel#menuLayoutEditorHint { font-weight: 400; }
QFrame#menuLayoutEditorPanel, QFrame#menuLayoutPreviewPanel {
    background: #fbfbfc;
    border: 1px solid #e2e4e8;
    border-radius: 10px;
}
QFrame#imagePreviewDrawer {
    background: #ffffff;
    border: none;
    border-left: 1px solid #d9dce1;
}
QScrollArea#imagePreviewScroll, QScrollArea#imagePreviewScroll > QWidget > QWidget,
QWidget#imageMasonryFlow { background: transparent; }
QLabel#imagePreviewTitle { color: #202124; font-size: 16px; font-weight: 600; }
QLabel#imagePreviewCount, QLabel#imagePreviewPath { color: #777b80; font-size: 11px; }
QLabel#imagePreviewEmpty { color: #777b80; }
QPushButton#imagePreviewClose { background: transparent; border: none; font-size: 20px; }
QPushButton#imagePreviewClose:hover { background: #eceef1; }
QTreeWidget#menuLayoutTree, QTreeWidget#menuLayoutPreview {
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 10px;
    outline: none;
    padding: 4px;
}
QTreeWidget#menuLayoutTree::item, QTreeWidget#menuLayoutPreview::item {
    min-height: 28px;
    padding: 1px 5px;
    border-radius: 6px;
}
QTreeWidget#menuLayoutTree::item:hover { background: #f4f5f6; }
QTreeWidget#menuLayoutTree::item:selected {
    background: #e9eef5;
    color: #202020;
}
QTreeWidget#menuLayoutPreview::item:hover { background: transparent; }
QTreeWidget#menuLayoutTree QHeaderView::section {
    min-height: 28px;
    padding: 0 8px;
    background: #f7f7f8;
    border: none;
    border-bottom: 1px solid #e8e9ec;
    color: #6f7378;
    font-size: 12px;
    font-weight: 500;
}
QMenu {
    background: #ffffff;
    color: #202020;
    border: 1px solid #d7d9dd;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item { min-height: 26px; padding: 2px 24px 2px 10px; border-radius: 6px; }
QMenu::item:selected { background: #edf2f7; }
QMenu::item:disabled { color: #a6a8ac; }
QPushButton {
    min-height: 26px;
    padding: 1px 12px;
    background: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 7px;
    font-weight: 500;
}
QPushButton:hover { background: #f0f0f0; }
QPushButton:focus {
    border: 2px solid #0a84ff;
    padding: 0 11px;
}
QPushButton[settingsMenuButton="true"] { padding-right: 26px; }
QPushButton[settingsMenuButton="true"]:focus { padding-right: 25px; }
QPushButton#advancedSectionToggle {
    min-height: 40px;
    padding: 0 38px 0 14px;
    text-align: left;
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 10px;
    color: #252525;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#advancedSectionToggle:hover { background: #f7f7f8; border-color: #d7d9dd; }
QPushButton#advancedSectionToggle:focus { border: 2px solid #0a84ff; }
QLabel#disclosureChevron { color: #777b80; font-size: 18px; background: transparent; }
"""
