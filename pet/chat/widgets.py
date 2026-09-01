from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImageReader, QMouseEvent, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QStyle,
    QSizePolicy,
    QSpacerItem,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .models import ChatMessage
from .pet_link import PetChatLink
from .prompt import PromptBuilder, load_character_manifest
from . import themes as chat_themes
from .service import ChatService
from .session_store import SessionStore
from ..context_menus.icons import vector_widget_icon


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_ACCENT = "#3994ff"

_logger = logging.getLogger(__name__)

# 附件上限（与发送逻辑共用，超限时明确提示而非静默跳过）
MAX_ATTACHMENTS = 10
MAX_IMAGE_BYTES = 10 * 1024 * 1024          # 单张图片 10MB 上限
MAX_IMAGE_TOTAL_BYTES = 20 * 1024 * 1024     # 图片总大小 20MB 上限
MAX_TEXT_TOTAL_CHARS = 200_000               # 文本总长 20 万字符上限
_TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".py", ".log", ".yaml", ".yml", ".xml"}


def _attachment_is_image(path: Path) -> bool:
    mime = mimetypes.guess_type(path.name)[0] or ""
    return mime.startswith("image/")


def _attachment_is_text(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _attachment_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _attachment_text_chars(path: Path) -> int:
    """文本附件的字符数；只读前 MAX_TEXT_TOTAL_CHARS+1 字符，超大文件不整读。"""
    if not _attachment_is_text(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return len(fh.read(MAX_TEXT_TOTAL_CHARS + 1))
    except OSError:
        return 0


def validate_attachment_additions(current, candidates) -> tuple[list[Path], list[str]]:
    """校验新增附件，返回（应接受的新增路径, 超限警告文案）。

    规则：
    - 附件总量 ≤ MAX_ATTACHMENTS；
    - 必须为图片或文本（_attachment_is_image / _attachment_is_text 之一），否则拒绝并提示暂不支持；
    - 图片单张 ≤ MAX_IMAGE_BYTES，图片总大小 ≤ MAX_IMAGE_TOTAL_BYTES；
    - 文本总长度 ≤ MAX_TEXT_TOTAL_CHARS；
    - 重复路径 / 非文件直接忽略（不视为超限）。
    """
    accepted: list[Path] = []
    warnings: list[str] = []
    seen = set(current)
    total_count = len(current)
    image_bytes = sum(_attachment_file_size(p) for p in current if _attachment_is_image(p))
    text_chars = sum(_attachment_text_chars(p) for p in current if _attachment_is_text(p))

    for value in candidates:
        path = Path(value).expanduser().resolve()
        if path in seen:
            continue
        if not path.is_file():
            continue
        if not (_attachment_is_image(path) or _attachment_is_text(path)):
            ext = path.suffix.lower() or path.name
            warnings.append(f"暂不支持 {ext} 的文件格式（支持：文本和图片）")
            continue
        if total_count >= MAX_ATTACHMENTS:
            warnings.append(f"附件最多 {MAX_ATTACHMENTS} 个，{path.name} 未添加")
            continue
        if _attachment_is_image(path):
            size = _attachment_file_size(path)
            if size > MAX_IMAGE_BYTES:
                warnings.append(f"图片 {path.name} 超过 10MB，未添加")
                continue
            if image_bytes + size > MAX_IMAGE_TOTAL_BYTES:
                warnings.append(f"图片总大小超过 20MB，{path.name} 未添加")
                continue
            image_bytes += size
        elif _attachment_is_text(path):
            chars = _attachment_text_chars(path)
            if text_chars + chars > MAX_TEXT_TOTAL_CHARS:
                warnings.append(f"文本总长超过 20 万字符，{path.name} 未添加")
                continue
            text_chars += chars
        accepted.append(path)
        seen.add(path)
        total_count += 1
    return accepted, warnings


def resolve_bg_pixmap(config_value: str):
    """Backward-compatible shared background resolver."""
    return chat_themes.resolve_background_pixmap(config_value)


def _safe_color(value: object) -> str:
    value = str(value or "")
    return value if _COLOR_RE.fullmatch(value) else _DEFAULT_ACCENT


def _initial(character_id: str) -> str:
    text = str(character_id or "宠").strip()
    return text[:1].upper() or "宠"


def _short_title(session) -> str:
    if str(getattr(session, "custom_title", "")).strip():
        return str(session.custom_title).strip()
    for message in session.messages:
        if message.role == "user" and message.content.strip():
            text = " ".join(message.content.split())
            return text[:24] + ("…" if len(text) > 24 else "")
    try:
        created = datetime.fromisoformat(session.created_at)
        return "新会话 · " + created.astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return "新会话"


def _session_group(session, now: datetime | None = None) -> str:
    try:
        updated = datetime.fromisoformat(session.updated_at)
    except (TypeError, ValueError):
        return "更早"
    now = now or datetime.now(updated.tzinfo)
    age = now - updated
    if bool(getattr(session, "pinned", False)):
        return "置顶"
    if updated.date() == now.date():
        return "今天"
    if age < timedelta(days=7):
        return "7 天内"
    if age < timedelta(days=30):
        return "30 天内"
    return "更早"


def _chat_tool_button(parent, object_name: str, icon_name: str, tooltip: str, dark: bool = False) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName(object_name)
    if dark:
        button.setProperty("modernDark", True)  # 深色/强调色底 → 浅色图标
    button.setIcon(vector_widget_icon(button, icon_name, 16))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


class SessionListRow(QFrame):
    action_requested = Signal(str, str)
    selection_requested = Signal(str, bool)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session_id = session.session_id
        self.setObjectName("session-row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(5)
        self.selector = QToolButton(self)
        self.selector.setObjectName("session-select-button")
        self.selector.setCheckable(True)
        self.selector.setFixedSize(22, 22)
        self.selector.setText("✓")
        self.selector.setAccessibleName("选择会话")
        self.selector.toggled.connect(
            lambda checked: self.selection_requested.emit(self.session_id, checked)
        )
        self.selector.hide()
        layout.addWidget(self.selector)
        self.title = QLabel(_short_title(session), self)
        self.title.setObjectName("session-row-title")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title, 1)
        self.more = _chat_tool_button(self, "session-more-button", "more", "会话操作")
        self.more.clicked.connect(self._show_menu)
        self.more.hide()
        layout.addWidget(self.more)

    def set_selection_mode(self, enabled: bool, selected: bool = False) -> None:
        self.selector.blockSignals(True)
        self.selector.setChecked(bool(selected))
        self.selector.blockSignals(False)
        self.selector.setVisible(bool(enabled))
        self.more.setVisible(False)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.selector.isHidden():
            self.more.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self.more.underMouse():
            self.more.hide()
        super().leaveEvent(event)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("session-action-menu")
        # 浅色小菜单（深色聊天主题下仍为浅色，属已知小瑕疵；无 QSS 覆盖）。
        # color 必须显式给出：深色系统 palette 文字为白色，白底上看不见。
        menu.setStyleSheet(
            "QMenu{background:#fff;color:#252a32;border:1px solid #e1e5eb;border-radius:10px;padding:5px;}"
            "QMenu::item{min-height:25px;padding:3px 24px 3px 9px;border-radius:7px;color:#252a32;}"
            "QMenu::item:selected{background:#f0f3f8;color:#252a32;}"
            "QMenu::item:disabled{color:#9aa1ab;}"
            "QMenu::separator{height:1px;background:#eef0f4;margin:4px 8px;}"
        )
        for key, text, icon in (
            ("rename", "重命名", "rename"),
            ("pin", "置顶 / 取消置顶", "pin"),
            ("delete", "删除", "clear"),
        ):
            action = menu.addAction(vector_widget_icon(menu, icon, 16), text)
            action.triggered.connect(
                lambda _checked=False, key=key: self.action_requested.emit(self.session_id, key)
            )
        menu.popup(self.more.mapToGlobal(self.more.rect().bottomRight()))


class DeleteConversationDialog(QDialog):
    """Small app-owned confirmation dialog shared by single and batch delete."""

    def __init__(self, count: int = 1, parent=None):
        super().__init__(parent)
        self.setObjectName("delete-conversation-dialog")
        self.setWindowTitle("删除对话")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 20)
        self.card = QFrame(self)
        self.card.setObjectName("delete-dialog-card")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(26, 24, 26, 22)
        card_layout.setSpacing(12)
        subject = "该对话" if count == 1 else f"选中的 {count} 个对话"
        title = QLabel(f"删除后，{subject}将不可恢复", self.card)
        title.setObjectName("delete-dialog-title")
        detail = QLabel("由这些对话生成的分享链接也将失效", self.card)
        detail.setObjectName("delete-dialog-detail")
        card_layout.addWidget(title)
        card_layout.addWidget(detail)
        card_layout.addSpacing(10)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消", self.card)
        cancel.setObjectName("cancel-delete-button")
        confirm = QPushButton(
            "删除该对话" if count == 1 else f"删除 {count} 个对话", self.card
        )
        confirm.setObjectName("confirm-delete-button")
        confirm.setDefault(True)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        card_layout.addLayout(actions)
        layout.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(24, 29, 38, 65))
        self.card.setGraphicsEffect(shadow)

        self.setStyleSheet(
            "QDialog#delete-conversation-dialog{background:transparent;color:#181b20;}"
            "QFrame#delete-dialog-card{background:#fff;border:1px solid #e2e5ea;"
            "border-radius:18px;}"
            "QLabel#delete-dialog-title{font-size:16px;font-weight:650;}"
            "QLabel#delete-dialog-detail{font-size:13px;color:#505660;}"
            "QPushButton{min-width:92px;min-height:38px;padding:0 16px;border-radius:19px;"
            "border:1px solid #dfe2e7;background:#fff;font-size:13px;}"
            "QPushButton:hover{background:#f5f6f8;}"
            "QPushButton#confirm-delete-button{border:none;background:#ef2727;color:#fff;font-weight:600;}"
            "QPushButton#confirm-delete-button:hover{background:#d91f1f;}"
        )


class ChatTitleBar(QFrame):
    """独立聊天窗的自绘标题栏。"""

    close_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat-title-bar")
        self._drag_offset: QPoint | None = None
        self._dragging = False

    @staticmethod
    def _global_position(event: QMouseEvent) -> QPoint:
        position = getattr(event, "globalPosition", None)
        if position is not None:
            return position().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = self._global_position(event) - self.window().frameGeometry().topLeft()
            self._dragging = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and self._drag_offset is not None:
            self.window().move(self._global_position(event) - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MessageBubble(QFrame):
    retry_requested = Signal()

    def __init__(self, role: str, content: str = "", character_id: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.character_id = character_id
        self.state = "normal"
        self.setObjectName("message-bubble")
        self.setProperty("role", role)
        self.setProperty("state", self.state)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if role == "assistant" else QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Minimum,
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.avatar = QLabel("你" if role == "user" else _initial(character_id))
        self.avatar.setObjectName("bubble-avatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(34, 34)
        self.avatar.setProperty("role", role)
        self.avatar.hide()

        panel = QVBoxLayout()
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(5)
        self.meta = QLabel("你" if role == "user" else "桌宠")
        self.meta.setObjectName("bubble-meta")
        self.meta.hide()

        self.surface = QFrame(self)
        self.surface.setObjectName("message-surface")
        self.surface.setProperty("role", role)
        self.surface.setProperty("state", self.state)
        surface_layout = QVBoxLayout(self.surface)
        self.surface_layout = surface_layout
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        self.body = QLabel(self.surface)
        self.body.setObjectName("bubble-body")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setText(content)
        self.body.setMinimumWidth(360 if role == "assistant" else 120)
        surface_layout.addWidget(self.body)
        panel.addWidget(self.surface)

        self.tools = QFrame(self)
        self.tools.setObjectName("message-tools")
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(2)
        self.copy_button = _chat_tool_button(self.tools, "message-copy-button", "copy", "复制")
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.body.text())
        )
        tools.addWidget(self.copy_button)
        if role == "user":
            tools.insertStretch(0, 1)
        else:
            tools.addStretch(1)
        panel.addWidget(self.tools)

        self.error_actions = QFrame(self.surface)
        self.error_actions.setObjectName("error-actions")
        error_actions_layout = QHBoxLayout(self.error_actions)
        error_actions_layout.setContentsMargins(0, 6, 0, 0)
        error_actions_layout.setSpacing(8)
        self.status_label = QLabel(self.error_actions)
        self.status_label.setObjectName("bubble-status")
        error_actions_layout.addWidget(self.status_label)
        error_actions_layout.addStretch(1)
        self.retry_button = QPushButton("重试", self.error_actions)
        self.retry_button.setObjectName("retry-button")
        self.retry_button.clicked.connect(self.retry_requested)
        error_actions_layout.addWidget(self.retry_button)
        self.error_actions.hide()
        surface_layout.addWidget(self.error_actions)
        root.addLayout(panel, 1)

        if role == "user":
            root.setDirection(QHBoxLayout.Direction.RightToLeft)

    def set_content(self, text: str) -> None:
        self.body.setText(str(text))

    def set_state(self, state: str) -> None:
        self.state = state
        self.setProperty("state", state)
        self.surface.setProperty("state", state)
        is_error = state == "error"
        if is_error:
            self.surface_layout.setContentsMargins(12, 10, 12, 10)
        else:
            self.surface_layout.setContentsMargins(0, 0, 0, 0)
        # Error/streaming rows own their status controls inside the surface;
        # the ordinary copy toolbar would otherwise float below the card.
        self.tools.setVisible(state not in {"error", "streaming"})
        if state == "streaming":
            self.status_label.setText("正在生成…")
            self.error_actions.show()
            self.retry_button.setVisible(False)
        elif state == "error":
            self.status_label.setText("本次回复未保存")
            self.error_actions.show()
            self.retry_button.setVisible(True)
        elif state == "stopped":
            self.status_label.setText("已停止生成")
            self.error_actions.show()
            self.retry_button.setVisible(False)
        else:
            self.error_actions.hide()
        self.surface.style().unpolish(self.surface)
        self.surface.style().polish(self.surface)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class AttachmentTextEdit(QPlainTextEdit):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class AttachmentChip(QFrame):
    remove_requested = Signal(str)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path).resolve()
        self.setObjectName("attachment-chip")
        self.setToolTip(str(self.path))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 5, 4)
        layout.setSpacing(6)

        self.preview = QLabel(self)
        self.preview.setObjectName("attachment-preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(34, 34)
        # 用 QImageReader 限定解码尺寸（34px 预览），避免 10MB 级大图
        # 整图解码进内存只为显示 34×34 缩略图
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        reader.setScaledSize(self.preview.size())
        image = reader.read()
        if not image.isNull():
            self.preview.setPixmap(
                QPixmap.fromImage(image).scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.preview.setPixmap(vector_widget_icon(self.preview, "attach", 18).pixmap(22, 22))
        layout.addWidget(self.preview)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(0)
        self.name_label = QLabel(self.path.name, self)
        self.name_label.setObjectName("attachment-name")
        self.name_label.setMaximumWidth(150)
        labels.addWidget(self.name_label)
        suffix = self.path.suffix.lstrip(".").upper() or "文件"
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        self.meta_label = QLabel(f"{suffix} · {size} B", self)
        self.meta_label.setObjectName("attachment-meta")
        labels.addWidget(self.meta_label)
        layout.addLayout(labels)

        self.remove_button = _chat_tool_button(self, "attachment-remove-button", "exit", "移除附件", dark=True)
        self.remove_button.setFixedSize(20, 20)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(str(self.path)))
        self.remove_button.hide()
        layout.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.remove_button.show()
        if event is not None:
            super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.remove_button.hide()
        if event is not None:
            super().leaveEvent(event)


class ChatComposer(QFrame):
    send_requested = Signal()
    notice = Signal(str)  # 附件超限等需要明确提示用户的事件

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat-composer")
        self.setAcceptDrops(True)
        self._busy = False
        self._ime_composing = False  # 输入法组合中（防止回车误发送）
        self.attachment_paths: list[Path] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 7, 9, 7)
        root.setSpacing(3)

        self.attachment_strip = QFrame(self)
        self.attachment_strip.setObjectName("attachment-strip")
        self.attachment_layout = QHBoxLayout(self.attachment_strip)
        self.attachment_layout.setContentsMargins(0, 0, 0, 2)
        self.attachment_layout.setSpacing(5)
        self.attachment_strip.hide()
        root.addWidget(self.attachment_strip)

        self.input = AttachmentTextEdit(self)
        self.input.setObjectName("chat-input")
        self.input.setPlaceholderText("和桌宠说点什么…  Enter 发送，Shift+Enter 换行")
        self.input.setMinimumHeight(38)
        self.input.setMaximumHeight(68)
        self.input.installEventFilter(self)
        self.input.files_dropped.connect(self.add_attachments)
        root.addWidget(self.input)

        self.toolbar = QFrame(self)
        self.toolbar.setObjectName("composer-toolbar")
        footer = QHBoxLayout(self.toolbar)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(5)
        self.attach_button = _chat_tool_button(self.toolbar, "composer-attach-button", "attach", "添加附件")
        self.attach_button.clicked.connect(self.choose_attachments)
        self.mode_button = None
        footer.addWidget(self.attach_button)
        self.hint = QLabel("内容会保存到当前角色的本地会话")
        self.hint.setObjectName("composer-hint")
        footer.addWidget(self.hint)
        footer.addStretch(1)
        self.send = QToolButton(self)
        self.send.setObjectName("send-button")
        self.send.setProperty("modernDark", True)  # 强调色底 → 浅色图标
        self.send.setIcon(vector_widget_icon(self.send, "send", 17))
        self.send.setFixedSize(34, 34)
        self.send.setToolTip("发送")
        self.send.clicked.connect(self.send_requested)
        footer.addWidget(self.send)
        root.addWidget(self.toolbar)
        self.input.textChanged.connect(self._update_enabled)
        self._update_enabled()

    def choose_attachments(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(
            self,
            "选择附件",
            "",
            # 文件选择器只声明真正支持的格式：PDF 等仅能作为文件名占位、没有
            # 实际内容发送的类型不再收录（避免“宣称支持却只发文件名”的误导）。
            "支持的文件 (*.txt *.md *.json *.csv *.py *.log *.jpg *.jpeg *.png *.webp *.gif);;所有文件 (*)",
        )
        self.add_attachments(paths)

    def add_attachments(self, paths) -> None:
        new_paths, warnings = validate_attachment_additions(self.attachment_paths, paths)
        if new_paths:
            self.attachment_paths.extend(new_paths)
            self._refresh_attachments()
            self._update_enabled()
        if warnings:
            self._flash_notice("；".join(warnings))

    def _flash_notice(self, message: str) -> None:
        """超限提示：发出信号并在输入区底部提示栏短暂显示。"""
        self.notice.emit(message)
        if getattr(self, "hint", None) is not None:
            self.hint.setText(message)
            QTimer.singleShot(5000, lambda: self.hint.setText("内容会保存到当前角色的本地会话"))

    def _refresh_attachments(self) -> None:
        while self.attachment_layout.count():
            item = self.attachment_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for path in self.attachment_paths:
            chip = AttachmentChip(path, self.attachment_strip)
            chip.remove_requested.connect(lambda value: self.remove_attachment(Path(value)))
            self.attachment_layout.addWidget(chip)
        self.attachment_layout.addStretch(1)
        self.attachment_strip.setVisible(bool(self.attachment_paths))

    def remove_attachment(self, path: Path) -> None:
        self.attachment_paths = [item for item in self.attachment_paths if item != path]
        self._refresh_attachments()
        self._update_enabled()

    def clear_attachments(self) -> None:
        self.attachment_paths.clear()
        self._refresh_attachments()
        self._update_enabled()

    def attachment_prompt(self) -> str:
        blocks = []
        text_budget = MAX_TEXT_TOTAL_CHARS
        for path in self.attachment_paths:
            header = f"附件：{path.name}（{mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}）"
            if _attachment_is_text(path):
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = "[读取失败]"
                if len(content) > text_budget:
                    content = content[:text_budget]
                text_budget = max(0, text_budget - len(content))
                blocks.append(f"{header}\n```\n{content}\n```")
            else:
                blocks.append(header)
        return "\n\n".join(blocks)

    def image_payloads(self) -> list[dict]:
        payloads = []
        for path in self.attachment_paths:
            mime = mimetypes.guess_type(path.name)[0] or ""
            if not mime.startswith("image/"):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue  # 附件已删除
            if size > MAX_IMAGE_BYTES:
                # 不再静默跳过：明确提示用户该图超限
                self._flash_notice(f"图片 {path.name} 超过 10MB，未包含在消息中")
                continue
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue
            payloads.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        return payloads

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.add_attachments(paths)
        event.acceptProposedAction()

    def eventFilter(self, obj, event):
        # 输入法组合状态跟踪：组合中回车用于上屏候选，不应触发发送
        if event.type() == QEvent.Type.InputMethod:
            self._ime_composing = bool(event.preeditString())
            if event.commitString():
                self._ime_composing = False
        elif obj is getattr(self, "input", None) and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if getattr(self, "_ime_composing", False):
                    return False  # 交给输入法上屏候选
                self.send_requested.emit()
                return True
        return super().eventFilter(obj, event)

    def _update_enabled(self) -> None:
        self.send.setEnabled(self._busy or bool(self.input.toPlainText().strip()) or bool(self.attachment_paths))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.send.setIcon(vector_widget_icon(self.send, "stop" if self._busy else "send", 17))
        self.send.setToolTip("停止" if self._busy else "发送")
        self.send.setProperty("busy", self._busy)
        self._update_enabled()
        self.send.style().unpolish(self.send)
        self.send.style().polish(self.send)


class SidebarScrim(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        event.accept()


class ChatWindow(QDialog):
    def __init__(self, config, character_id: str, parent=None, pet_window=None):
        super().__init__(parent)
        self.config = config
        self.character_id = str(character_id)
        self.setObjectName("chat-window")
        self.setWindowTitle("AI 对话")
        # 窗口级图标主题：QSS 表面为浅色，深色系统 palette 前景是白色，
        # 会让关闭/最小化/新建/删除等图标白底白图不可见（见 icons._icon_theme）
        self.setProperty("menuStyle", "modern")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        # Desktop AI-chat workspace: conversation navigation on the left and
        # the focused message canvas on the right.
        self.setMinimumSize(560, 480)
        self.setMaximumSize(1600, 1200)
        self.resize(960, 700)
        # Frameless window has no system resize border on Windows; track the
        # mouse so edge hover can switch to the resize cursor and drags resize.
        self.setMouseTracking(True)
        self._resize_edges: set[str] = set()
        self._resize_global_start: QPoint | None = None
        self._resize_start_geometry: QRect | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 无边框圆角窗：窗口自身透明（QSS #chat-window 背景同步置 transparent），
        # 只显示圆角的 phone-shell，去掉窗外一圈方形背景（浅色系统白框/深色系统黑框）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        # 边缘缩放光标改用 hover 事件持续刷新：子控件无鼠标跟踪时窗口的
        # mouseMoveEvent 会停更，导致从窗口外进入后光标卡在缩放双箭头
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self.settings = config.chat_settings()
        self.prompt_builder = PromptBuilder(Path(__file__).resolve().parents[2] / "assets" / "characters")
        self.store = SessionStore(config.dir, getattr(config, "instance_id", ""))
        self.session = self._get_session()
        self.service = ChatService(parent=self)
        self.pet_link = PetChatLink(pet_window)
        self._bubble: MessageBubble | None = None
        self._bubbles: list[MessageBubble] = []
        self._text = ""
        self._active_request_id: str | None = None
        self._last_user_text = ""
        self._last_user_payload: list[dict] | None = None
        self._pending_output = ""
        self._pending_finish_text: str | None = None
        self._stream_follow_output = True
        self._typewriter_timer = QTimer(self)
        self._typewriter_timer.setInterval(18)
        self._typewriter_timer.timeout.connect(self._typewriter_tick)
        self._compact_layout = False
        self._sidebar_open = True
        self._multi_select_mode = False
        self._selected_session_ids: set[str] = set()
        self.accent_color = _DEFAULT_ACCENT
        self._base_accent = _DEFAULT_ACCENT
        self._bg_pixmap = None
        self._bg_theme = None
        self._bg_value = ""
        self._bg_scaled = None
        self._bg_scaled_size = None
        self.character_name = self.character_id
        self._character_manifest: dict = {}
        self.follow_pet = bool(config.get("chat_follow_pet", False))
        self._follow_pet_window = None
        self._follow_reposition_timer = QTimer(self)
        self._follow_reposition_timer.setSingleShot(True)
        self._follow_reposition_timer.setInterval(40)
        self._follow_reposition_timer.timeout.connect(self._reposition_after_pet_move)

        self._build()
        self._connect()
        self._apply_character_theme()
        self._refresh_sessions()
        self._load()
        self._style()
        self.set_follow_pet(self.follow_pet, persist=False)

    def set_pet_window(self, pet_window=None) -> None:
        old = self._follow_pet_window
        if old is not None and hasattr(old, "remove_position_listener"):
            old.remove_position_listener(self._on_pet_moved)
        self._follow_pet_window = None
        self.pet_link.set_window(pet_window)
        if hasattr(self, "avatar_label"):
            self._update_header_avatar()
        if self.follow_pet and pet_window is not None and hasattr(pet_window, "add_position_listener"):
            pet_window.add_position_listener(self._on_pet_moved)
            self._follow_pet_window = pet_window

    def set_follow_pet(self, enabled: bool, persist: bool = True) -> None:
        self.follow_pet = bool(enabled)
        self.follow_button.blockSignals(True)
        self.follow_button.setChecked(self.follow_pet)
        self.follow_button.blockSignals(False)
        # 勾选后按钮底变为强调色，图标需换成浅色；未勾选为浅色底深色图标
        self.follow_button.setProperty("modernDark", self.follow_pet)
        self.follow_button.setIcon(vector_widget_icon(self.follow_button, "pin", 14))
        if persist:
            self.config.set("chat_follow_pet", self.follow_pet)
            self.config.save()
        self.set_pet_window(self.pet_link.pet_window)
        if self.follow_pet and self.isVisible():
            self.position_near_pet()

    def _on_pet_moved(self, _pet=None) -> None:
        if self.follow_pet and self.isVisible() and not self._follow_reposition_timer.isActive():
            self._follow_reposition_timer.start()

    def _reposition_after_pet_move(self) -> None:
        if self.follow_pet and self.isVisible():
            self.position_near_pet()

    def toggle_always_on_top(self) -> None:
        on = self.pin_button.isChecked()
        self.config.set("chat_always_on_top", on)
        self.config.save()
        self.set_chat_always_on_top(on)

    def set_chat_always_on_top(self, on: bool) -> None:
        """切换聊天窗置顶；保留位置、尺寸与可见/激活状态。"""
        was_visible = self.isVisible()
        was_active = self.isActiveWindow()
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.setGeometry(geometry)
        if was_visible:
            self.show()
            if was_active:
                self.activateWindow()
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(bool(on))
        self.pin_button.blockSignals(False)

    def position_near_pet(self, pet_window=None, gap: int = 14) -> None:
        """Place the phone chat window beside the visible pet bounds."""
        pet = pet_window or self.pet_link.pet_window
        if pet is None:
            return
        if pet_window is not None:
            self.set_pet_window(pet_window)
        elif self.follow_pet and self._follow_pet_window is None:
            self.set_pet_window(pet)

        visible_bounds = getattr(pet, "visible_content_rect", None)
        pet_rect = visible_bounds() if callable(visible_bounds) else pet.frameGeometry()
        if pet_rect.isNull() or not pet_rect.isValid():
            pet_rect = pet.frameGeometry()
        screen = QGuiApplication.screenAt(pet_rect.center())
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        # Preserve the two-pane layout on compact displays while leaving room
        # to place the chat beside the visible pet instead of covering it.
        if available.width() < 1000 and self.width() > available.width() - 140:
            self.resize(max(self.minimumWidth(), available.width() - 140), self.height())
        size = self.frameGeometry().size()
        # Prefer the side with the least visual obstruction. If the pet is near
        # a screen edge, the first fully-contained candidate on another side wins.
        y = pet_rect.center().y() - size.height() // 2
        candidates = [
            QPoint(pet_rect.right() + gap + 1, y),
            QPoint(pet_rect.left() - size.width() - gap, y),
            QPoint(pet_rect.center().x() - size.width() // 2, pet_rect.bottom() + gap + 1),
            QPoint(pet_rect.center().x() - size.width() // 2, pet_rect.top() - size.height() - gap),
        ]
        for point in candidates:
            candidate = QRect(point, size)
            if available.contains(candidate):
                self.move(point)
                return

        # If the phone is taller than the available work area, a full candidate
        # may be impossible even though one side still has enough horizontal
        # space. Clamp every candidate, then choose the one with the smallest
        # overlap against the visible character. This prevents the old fallback
        # from forcing the phone back onto the pet when the pet is at the right
        # edge of the screen.
        def clamp_point(point: QPoint) -> QPoint:
            x = max(available.left(), min(point.x(), available.right() - size.width() + 1))
            y = max(available.top(), min(point.y(), available.bottom() - size.height() + 1))
            return QPoint(x, y)

        ranked = []
        for index, point in enumerate(candidates):
            clamped = clamp_point(point)
            candidate = QRect(clamped, size)
            intersection = candidate.intersected(pet_rect)
            overlap = intersection.width() * intersection.height() if not intersection.isEmpty() else 0
            displacement = abs(clamped.x() - point.x()) + abs(clamped.y() - point.y())
            ranked.append((overlap, displacement, index, clamped))

        _, _, _, best_point = min(ranked, key=lambda item: item[:3])
        self.move(best_point)

    def _get_session(self):
        sessions = self.store.list(self.character_id)
        return sessions[0] if sessions else self._new_session()

    def _new_session(self):
        session = self.store.create(
            self.character_id,
            self.settings.active_provider,
            self.prompt_builder.effective_system_prompt(self.settings, self.character_id),
        )
        self.store.save(session)
        return session

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(7, 7, 7, 7)
        outer.setSpacing(0)
        self.phone_shell = QFrame(self)
        self.phone_shell.setObjectName("phone-shell")
        self.phone_shell.setAutoFillBackground(True)
        outer.addWidget(self.phone_shell)

        root = QHBoxLayout(self.phone_shell)
        self.root_layout = root
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # DeepSeek-style persistent navigation rail.
        context = QFrame(self.phone_shell)
        context.setObjectName("deepseek-sidebar")
        context.setFixedWidth(238)
        self.sidebar = context
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(14, 16, 14, 14)
        context_layout.setSpacing(10)

        brand = QHBoxLayout()
        brand.setContentsMargins(2, 0, 2, 2)
        brand.setSpacing(9)
        self.avatar_label = QLabel(_initial(self.character_id))
        self.avatar_label.setObjectName("avatar-label")
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setFixedSize(34, 34)
        brand.addWidget(self.avatar_label)
        self.brand_label = QLabel("鲸语 AI")
        self.brand_label.setObjectName("brand-label")
        brand.addWidget(self.brand_label)
        brand.addStretch(1)
        context_layout.addLayout(brand)

        self.new_session_button = QPushButton("开启新对话", self.phone_shell)
        self.new_session_button.setObjectName("new-conversation-button")
        self.new_session_button.setIcon(vector_widget_icon(self.new_session_button, "add", 16))
        self.new_session_button.setToolTip("新建会话")
        self.new_session_button.setAccessibleName("新建会话")
        self.new_session_button.setFixedHeight(34)
        new_session_shadow = QGraphicsDropShadowEffect(self.new_session_button)
        new_session_shadow.setBlurRadius(14)
        new_session_shadow.setOffset(0, 4)
        new_session_shadow.setColor(QColor(35, 45, 68, 30))
        self.new_session_button.setGraphicsEffect(new_session_shadow)
        context_layout.addWidget(self.new_session_button)

        self.session_caption = QLabel("会话")
        self.session_caption.setObjectName("session-section-title")
        session_heading = QHBoxLayout()
        session_heading.setContentsMargins(0, 0, 0, 0)
        session_heading.setSpacing(4)
        session_heading.addWidget(self.session_caption, 1)
        self.multi_select_button = _chat_tool_button(
            context, "session-multi-select-button", "multi_select", "批量管理会话"
        )
        session_heading.addWidget(self.multi_select_button)
        context_layout.addLayout(session_heading)
        self.session_list = QListWidget(context)
        self.session_list.setObjectName("session-list")
        self.session_list.setFrameShape(QFrame.Shape.NoFrame)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.setSpacing(2)
        context_layout.addWidget(self.session_list, 1)
        # Compatibility alias for callers that only need count/current session.
        self.session_combo = self.session_list

        self.batch_action_bar = QFrame(context)
        self.batch_action_bar.setObjectName("session-batch-action-bar")
        batch_layout = QHBoxLayout(self.batch_action_bar)
        batch_layout.setContentsMargins(4, 8, 4, 0)
        batch_layout.setSpacing(8)
        self.batch_pin_button = QPushButton("置顶", self.batch_action_bar)
        self.batch_pin_button.setObjectName("batch-pin-button")
        self.batch_pin_button.setIcon(vector_widget_icon(self.batch_pin_button, "pin", 16))
        self.batch_delete_button = QPushButton("删除", self.batch_action_bar)
        self.batch_delete_button.setObjectName("batch-delete-button")
        self.batch_delete_button.setIcon(vector_widget_icon(self.batch_delete_button, "clear", 16))
        batch_layout.addWidget(self.batch_pin_button)
        batch_layout.addWidget(self.batch_delete_button)
        self.batch_action_bar.hide()
        context_layout.addWidget(self.batch_action_bar)

        sidebar_footer = QFrame(context)
        sidebar_footer.setObjectName("sidebar-footer")
        footer_layout = QVBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(9, 8, 9, 8)
        footer_layout.setSpacing(7)
        footer_actions = QHBoxLayout()
        footer_actions.setSpacing(6)
        self.follow_button = QToolButton(sidebar_footer)
        self.follow_button.setObjectName("follow-pet-button")
        self.follow_button.setText("跟随桌宠")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(self.follow_pet)
        self.follow_button.setToolTip("聊天窗口跟随桌宠移动")
        self.follow_button.setAccessibleName("聊天窗跟随桌宠")
        self.follow_button.setIcon(vector_widget_icon(self.follow_button, "pin", 14))
        self.follow_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.follow_button.setMinimumHeight(30)
        footer_actions.addWidget(self.follow_button, 1)
        self.delete_session_button = QToolButton(self)
        self.delete_session_button.setObjectName("delete-session-button")
        self.delete_session_button.setIcon(vector_widget_icon(self.delete_session_button, "remove", 15))
        self.delete_session_button.setToolTip("删除当前会话")
        self.delete_session_button.setAccessibleName("删除当前会话")
        footer_actions.addWidget(self.delete_session_button)
        self.clear_button = QToolButton(self)
        self.clear_button.setObjectName("clear-session-button")
        self.clear_button.setIcon(vector_widget_icon(self.clear_button, "clear", 15))
        self.clear_button.setToolTip("清空当前会话")
        self.clear_button.setAccessibleName("清空当前会话")
        footer_actions.addWidget(self.clear_button)
        footer_layout.addLayout(footer_actions)
        context_layout.addWidget(sidebar_footer)
        root.addWidget(context)

        chat_main = QFrame(self.phone_shell)
        chat_main.setObjectName("chat-main")
        self.chat_main = chat_main
        chat_main_layout = QVBoxLayout(chat_main)
        self.chat_main_layout = chat_main_layout
        chat_main_layout.setContentsMargins(0, 0, 0, 0)
        chat_main_layout.setSpacing(0)
        root.addWidget(chat_main, 1)

        # Compact conversation header doubles as the frameless drag handle.
        self.title_bar = ChatTitleBar(chat_main)
        self.title_bar.setObjectName("chat-main-header")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(20, 13, 13, 11)
        title_layout.setSpacing(8)
        title_text = QVBoxLayout()
        title_text.setSpacing(1)
        self.title_label = QLabel("初次问候")
        self.title_label.setObjectName("title-label")
        self.subtitle_label = QLabel("✦ 快速模式")
        self.subtitle_label.setObjectName("subtitle-label")
        title_text.addWidget(self.title_label)
        self.header_status = QFrame(self.title_bar)
        self.header_status.setObjectName("header-status")
        header_status_layout = QHBoxLayout(self.header_status)
        header_status_layout.setContentsMargins(0, 0, 0, 0)
        header_status_layout.setSpacing(5)
        header_status_layout.addWidget(self.subtitle_label)
        self.status_dot = QLabel("●", self.header_status)
        self.status_dot.setObjectName("status-dot")
        header_status_layout.addWidget(self.status_dot)
        self.status = QLabel("就绪", self.header_status)
        self.status.setObjectName("status-label")
        header_status_layout.addWidget(self.status)
        self.provider_label = QLabel(self.settings.active_config.model, self.header_status)
        self.provider_label.setObjectName("provider-label")
        self.provider_label.setMaximumWidth(150)
        self.provider_label.setToolTip(self.settings.active_config.model)
        self.provider = self.provider_label
        header_status_layout.addWidget(self.provider_label)
        header_status_layout.addStretch(1)
        title_text.addWidget(self.header_status)
        title_layout.addLayout(title_text)
        title_layout.addStretch(1)
        self.sidebar_toggle_button = QToolButton(self.title_bar)
        self.sidebar_toggle_button.setObjectName("sidebar-toggle-button")
        self.sidebar_toggle_button.setIcon(vector_widget_icon(self.sidebar_toggle_button, "sidebar", 17))
        self.sidebar_toggle_button.setToolTip("显示或隐藏会话侧栏")
        self.sidebar_toggle_button.setAccessibleName("显示或隐藏会话侧栏")
        title_layout.addWidget(self.sidebar_toggle_button)
        self.pin_button = QToolButton(self.title_bar)
        self.pin_button.setObjectName("window-pin-button")
        self.pin_button.setIcon(vector_widget_icon(self.pin_button, "pin", 16))
        self.pin_button.setToolTip("窗口置顶")
        self.pin_button.setAccessibleName("切换聊天窗口置顶")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(bool(self.config.get("chat_always_on_top", False)))
        title_layout.addWidget(self.pin_button)
        self.minimize_button = QToolButton(self.title_bar)
        self.minimize_button.setObjectName("window-minimize-button")
        self.minimize_button.setIcon(vector_widget_icon(self.minimize_button, "minimize", 16))
        self.minimize_button.setToolTip("最小化")
        self.minimize_button.setAccessibleName("最小化聊天窗口")
        self.close_button = QToolButton(self.title_bar)
        self.close_button.setObjectName("window-close-button")
        self.close_button.setIcon(vector_widget_icon(self.close_button, "exit", 16))
        self.close_button.setToolTip("关闭")
        self.close_button.setAccessibleName("关闭聊天窗口")
        title_layout.addWidget(self.minimize_button)
        title_layout.addWidget(self.close_button)
        chat_main_layout.addWidget(self.title_bar)

        self.content_top_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        chat_main_layout.addSpacerItem(self.content_top_spacer)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("message-scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_view = QWidget()
        self.message_view.setObjectName("message-view")
        self.message_stack = QStackedLayout(self.message_view)
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        # The empty prompt lives in a deliberately compact viewport so the
        # prompt and composer can be centred as one unit without creating a
        # meaningless scrollbar before any conversation exists.
        empty_layout.setContentsMargins(60, 20, 60, 20)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state = QLabel("有什么可以帮你的吗？\n和桌宠开始一段新的对话")
        self.empty_state.setObjectName("empty-state")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)
        empty_layout.addWidget(self.empty_state)
        self.timeline_host = QWidget()
        self.timeline_host.setObjectName("message-timeline")
        self.message_host_layout = QVBoxLayout(self.timeline_host)
        self.message_horizontal_margin = 76
        self.message_host_layout.setContentsMargins(
            self.message_horizontal_margin, 30, self.message_horizontal_margin, 30
        )
        self.message_host_layout.setSpacing(24)
        self.message_bottom_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.message_host_layout.addSpacerItem(self.message_bottom_spacer)
        self.message_stack.addWidget(self.empty_page)
        self.message_stack.addWidget(self.timeline_host)
        self.scroll.setWidget(self.message_view)
        # 用户上翻阅读历史时暂停自动滚底；回到底部或开始新回复时恢复跟随
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        self.scroll.setAutoFillBackground(True)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        chat_main_layout.addWidget(self.scroll)

        self.composer_card = QFrame(chat_main)
        self.composer_card.setObjectName("floating-composer")
        composer_card_layout = QVBoxLayout(self.composer_card)
        self.composer_card_layout = composer_card_layout
        composer_card_layout.setContentsMargins(76, 8, 76, 20)
        self.composer = ChatComposer(self.composer_card)
        shadow = QGraphicsDropShadowEffect(self.composer)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(32, 42, 61, 35))
        self.composer.setGraphicsEffect(shadow)
        composer_card_layout.addWidget(self.composer)
        self.input = self.composer.input
        self.send = self.composer.send
        chat_main_layout.addWidget(self.composer_card)
        self.content_bottom_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        chat_main_layout.addSpacerItem(self.content_bottom_spacer)

        self.sidebar_scrim = SidebarScrim(self.phone_shell)
        self.sidebar_scrim.setObjectName("sidebar-scrim")
        self.sidebar_scrim.hide()

        self.title = self.title_label
        self._set_empty_state(True)

    def _connect(self) -> None:
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button.clicked.connect(self.close)
        self.pin_button.clicked.connect(self.toggle_always_on_top)
        self.new_session_button.clicked.connect(self.new_session)
        self.delete_session_button.clicked.connect(self.delete_current_session)
        self.clear_button.clicked.connect(self.clear_session)
        self.sidebar_toggle_button.clicked.connect(self.toggle_sidebar)
        self.sidebar_scrim.clicked.connect(self.close_sidebar)
        self.follow_button.toggled.connect(self.set_follow_pet)
        self.session_list.currentRowChanged.connect(self._on_session_changed)
        self.session_list.itemClicked.connect(self._on_session_item_clicked)
        self.multi_select_button.clicked.connect(self.toggle_multi_select_mode)
        self.batch_pin_button.clicked.connect(self.pin_selected_sessions)
        self.batch_delete_button.clicked.connect(self.delete_selected_sessions)
        self.composer.send_requested.connect(self.send_message)
        self.service.started.connect(self._started)
        self.service.delta.connect(self._delta)
        self.service.finished.connect(self._finished)
        self.service.error.connect(self._error)
        self.service.stopped.connect(self._stopped)

    def toggle_sidebar(self) -> None:
        if self._compact_layout:
            if self.sidebar.isVisible():
                self.close_sidebar()
            else:
                self._show_overlay_sidebar()
            return
        self._sidebar_open = not self.sidebar.isVisible()
        self.sidebar.setVisible(self._sidebar_open)

    def close_sidebar(self) -> None:
        if not self._compact_layout:
            return
        self.sidebar.hide()
        self.sidebar_scrim.hide()

    def _show_overlay_sidebar(self) -> None:
        if not self._compact_layout:
            return
        self._position_overlay_sidebar()
        self.sidebar_scrim.show()
        self.sidebar_scrim.raise_()
        self.sidebar.show()
        self.sidebar.raise_()

    def _position_overlay_sidebar(self) -> None:
        rect = self.phone_shell.rect()
        drawer_width = min(286, max(220, int(rect.width() * 0.76)))
        self.sidebar.setFixedWidth(drawer_width)
        self.sidebar.setGeometry(0, 0, drawer_width, rect.height())
        actual_width = self.sidebar.width()
        self.sidebar_scrim.setGeometry(
            actual_width, 0, max(0, rect.width() - actual_width), rect.height()
        )

    def _update_responsive_layout(self) -> None:
        compact = self.width() < 880
        self.setProperty("compactLayout", compact)
        if compact != self._compact_layout:
            self._compact_layout = compact
            if compact:
                self.root_layout.removeWidget(self.sidebar)
                self.sidebar.setParent(self.phone_shell)
                self.sidebar.setProperty("overlayDrawer", True)
                self.sidebar.hide()
                self.sidebar_scrim.hide()
            else:
                self.sidebar_scrim.hide()
                self.sidebar.setFixedWidth(238)
                self.root_layout.insertWidget(0, self.sidebar)
                self.sidebar.setProperty("overlayDrawer", False)
                self.sidebar.show()
                self._sidebar_open = True
            self.sidebar.style().unpolish(self.sidebar)
            self.sidebar.style().polish(self.sidebar)
        if compact and self.sidebar.isVisible():
            self._position_overlay_sidebar()

    def _style(self) -> None:
        try:
            stylesheet = (Path(__file__).with_name("modern_styles.qss")).read_text(encoding="utf-8")
            self._bg_pixmap = self._resolve_bg_pixmap()
            self._bg_scaled = None
            self._set_background_surface_transparency(self._bg_pixmap is not None)
            if self._bg_pixmap is not None:
                overlay_theme = self._bg_theme or {"accent": self._base_accent, "dark": False}
                self.accent_color = overlay_theme["accent"]
                stylesheet += chat_themes.build_modern_custom_overlay_qss(
                    self.accent_color,
                    self.config.get("modern_chat_card_opacity", 84),
                )
            else:
                self.accent_color = self._base_accent
            self.setStyleSheet(stylesheet.replace("@ACCENT@", self.accent_color))
        except OSError:
            pass
        self._apply_header_avatar_style()
        for bubble in self._bubbles:
            self._apply_avatar_style(bubble.avatar, self.accent_color if bubble.role == "assistant" else "#2b75d6")
        self.update()

    def _set_background_surface_transparency(self, enabled: bool) -> None:
        """Prevent palette auto-fill from painting over a configured wallpaper."""
        self.phone_shell.setAutoFillBackground(not enabled)
        # These nested surfaces are already painted by modern_styles.qss. Auto
        # fill uses the native panel palette before QSS and caused the empty
        # page to become a grey band whenever a new conversation was opened.
        for widget in (
            self.scroll, self.scroll.viewport(), self.message_view,
            self.empty_page, self.timeline_host,
        ):
            widget.setAutoFillBackground(False)

    def _resolve_bg_pixmap(self):
        self._bg_value = str(self.config.get("modern_chat_background", "") or "").strip()
        try:
            opacity = int(self.config.get("modern_chat_background_opacity", 100))
        except (TypeError, ValueError):
            opacity = 100
        self._bg_opacity = max(10, min(100, opacity)) / 100.0
        fill = str(self.config.get("modern_chat_background_fill", "cover") or "cover")
        self._bg_fill = fill if fill in {"cover", "contain", "stretch"} else "cover"
        # 宽屏现代窗同样支持内置主题壁纸（与小手机一致），命中主题时套用其纱罩配色
        self._bg_theme = (
            chat_themes.get_theme(self._bg_value[8:])
            if self._bg_value.startswith("builtin:") else None
        )
        return resolve_bg_pixmap(self._bg_value)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._bg_pixmap is None:
            return super().paintEvent(event)
        target = self.phone_shell.geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(target), 20.0, 20.0)
        painter.setClipPath(clip)
        painter.fillPath(clip, QColor("#ffffff"))
        if self._bg_scaled is None or self._bg_scaled_size != target.size():
            self._bg_scaled = chat_themes.scale_background_pixmap(
                self._bg_pixmap, target.width(), target.height(), self._bg_fill,
            )
            self._bg_scaled_size = target.size()
        x = target.x() + (target.width() - self._bg_scaled.width()) // 2
        y = target.y() + (target.height() - self._bg_scaled.height()) // 2
        painter.setOpacity(self._bg_opacity)
        painter.drawPixmap(x, y, self._bg_scaled)
        painter.setOpacity(1.0)
        if self._bg_theme is not None:
            painter.fillPath(clip, QColor(*chat_themes.scrim_rgba(self._bg_theme)))
        painter.end()

    @staticmethod
    def _apply_session_palette(widget: QWidget) -> None:
        """Keep the session selector readable on light and dark host palettes."""
        palette = widget.palette()
        dark_text = QColor("#1f2937")
        disabled_text = QColor("#9ca3af")
        white = QColor("#ffffff")
        highlight = QColor("#e7f1ff")

        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            text = disabled_text if group == QPalette.ColorGroup.Disabled else dark_text
            palette.setColor(group, QPalette.ColorRole.WindowText, text)
            palette.setColor(group, QPalette.ColorRole.Text, text)
            palette.setColor(group, QPalette.ColorRole.ButtonText, text)
            palette.setColor(group, QPalette.ColorRole.Base, white)
            palette.setColor(group, QPalette.ColorRole.Button, white)
            palette.setColor(group, QPalette.ColorRole.Window, white)
            palette.setColor(group, QPalette.ColorRole.Highlight, highlight)
            palette.setColor(group, QPalette.ColorRole.HighlightedText, dark_text)

        widget.setPalette(palette)
        widget.setAutoFillBackground(True)

    def _apply_avatar_style(self, label: QLabel, color: str) -> None:
        label.setStyleSheet(f"background-color: {color}; color: #ffffff; border-radius: {label.width() // 2}px;")

    def _apply_header_avatar_style(self) -> None:
        self.avatar_label.setStyleSheet("background: transparent; border: none; padding: 0;")

    def _apply_character_theme(self) -> None:
        root = Path(__file__).resolve().parents[2] / "assets" / "characters"
        self._character_manifest = load_character_manifest(root, self.character_id)
        chat = self._character_manifest.get("chat", {})
        chat = chat if isinstance(chat, dict) else {}
        alias = self.config.character_alias(self.character_id)
        self.character_name = alias or str(self._character_manifest.get("name") or chat.get("name") or self.character_id)
        self.accent_color = _safe_color(chat.get("theme_color"))
        self._base_accent = self.accent_color
        self.brand_label.setText(f"{self.character_name} AI")
        self._update_header_avatar()
        self._apply_header_avatar_style()

    def _update_header_avatar(self) -> None:
        pet_window = self.pet_link.pet_window
        icon_pixmap = getattr(pet_window, "icon_pixmap", None)
        pixmap = icon_pixmap(34) if callable(icon_pixmap) else QPixmap()
        if pixmap is not None and not pixmap.isNull():
            self.avatar_label.setText("")
            self.avatar_label.setPixmap(
                pixmap.scaled(
                    30, 30,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            return
        self.avatar_label.clear()
        self.avatar_label.setText(_initial(self.character_id))

    def _load(self) -> None:
        self._clear_message_rows()
        for message in self.session.messages:
            self._add(message.role, message.content)
        self._set_empty_state(not bool(self.session.messages))
        self._bottom()

    def _clear_message_rows(self) -> None:
        while self.message_host_layout.count() > 1:
            item = self.message_host_layout.takeAt(0)
            self._delete_layout_item(item)
        self._bubbles.clear()
        self._bubble = None

    @staticmethod
    def _delete_layout_item(item) -> None:
        if item is None:
            return
        layout = item.layout()
        if layout is not None:
            ChatWindow._delete_layout(layout)
            return
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()

    @staticmethod
    def _delete_layout(layout) -> None:
        while layout.count():
            ChatWindow._delete_layout_item(layout.takeAt(0))

    def _set_empty_state(self, empty: bool) -> None:
        self.message_stack.setCurrentWidget(self.empty_page if empty else self.timeline_host)
        self.empty_state.setVisible(empty)
        QTimer.singleShot(0, self, self._update_conversation_height)

    def _add(self, role: str, text: str) -> MessageBubble:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        bubble = MessageBubble(role, text, self.character_id)
        if role == "user":
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        self.message_host_layout.insertLayout(self.message_host_layout.count() - 1, row)
        self._bubbles.append(bubble)
        self._set_empty_state(False)
        self._update_bubble_widths()
        QTimer.singleShot(0, self, self._update_conversation_height)
        return bubble

    def _update_conversation_height(self) -> None:
        """Center an empty composer; otherwise pin it below a top-down timeline."""
        if not hasattr(self, "scroll"):
            return
        empty = self.message_stack.currentWidget() is self.empty_page
        if empty:
            self.content_top_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            )
            self.content_bottom_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            )
            self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.scroll.setFixedHeight(150)
            self.chat_main_layout.setStretchFactor(self.scroll, 0)
        else:
            self.content_top_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            self.content_bottom_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            self.scroll.setMinimumHeight(0)
            self.scroll.setMaximumHeight(16777215)
            self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.chat_main_layout.setStretchFactor(self.scroll, 1)
        self.chat_main_layout.invalidate()
        self.message_host_layout.invalidate()

    def _update_bubble_widths(self) -> None:
        available = max(
            320,
            self.scroll.viewport().width() - 2 * self.message_horizontal_margin,
        )
        for bubble in self._bubbles:
            if bubble.role == "assistant":
                bubble.setMinimumWidth(available)
                bubble.setMaximumWidth(available)
            else:
                bubble.setMinimumWidth(0)
                bubble.setMaximumWidth(max(220, int(available * 0.66)))

    def _remove_bubble(self, bubble: MessageBubble | None) -> None:
        if bubble is None:
            return
        for index in range(0, self.message_host_layout.count() - 1):
            item = self.message_host_layout.itemAt(index)
            row = item.layout() if item else None
            if row is None:
                continue
            found = any(row.itemAt(i).widget() is bubble for i in range(row.count()))
            if found:
                self.message_host_layout.takeAt(index)
                self._delete_layout(row)
                break
        if bubble in self._bubbles:
            self._bubbles.remove(bubble)
        bubble.deleteLater()
        self._set_empty_state(not self._bubbles)

    def _refresh_sessions(self) -> None:
        sessions = self.store.list(self.character_id)
        if not sessions:
            self.session = self._new_session()
            sessions = [self.session]
        self.session_list.blockSignals(True)
        self.session_list.clear()
        def session_order(value):
            try:
                stamp = datetime.fromisoformat(value.updated_at).timestamp()
            except (TypeError, ValueError):
                stamp = 0.0
            return (not bool(getattr(value, "pinned", False)), -stamp)

        sessions.sort(key=session_order)
        selected = -1
        first_session_row = -1
        current_group = None
        for session in sessions:
            group = _session_group(session)
            if group != current_group:
                header = QListWidgetItem(group)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setData(Qt.ItemDataRole.UserRole, None)
                header.setSizeHint(QSize(1, 27))
                self.session_list.addItem(header)
                current_group = group
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            item.setToolTip(session.session_id)
            item.setSizeHint(QSize(1, 36))
            self.session_list.addItem(item)
            if first_session_row < 0:
                first_session_row = self.session_list.row(item)
            row = SessionListRow(session, self.session_list)
            row.action_requested.connect(self._session_action)
            row.selection_requested.connect(self.set_session_selected)
            row.set_selection_mode(
                self._multi_select_mode,
                session.session_id in self._selected_session_ids,
            )
            self.session_list.setItemWidget(item, row)
            if session.session_id == self.session.session_id:
                selected = self.session_list.row(item)
        if selected < 0:
            selected = first_session_row
            self.session = sessions[0]
        self.session_list.setCurrentRow(selected)
        self.session_list.blockSignals(False)
        self.title_label.setText(_short_title(self.session))
        self.session_list.setToolTip(f"当前会话：{self.session.session_id[:8]}")

    def _on_session_changed(self, index: int) -> None:
        if index < 0 or self._multi_select_mode:
            return
        item = self.session_list.item(index)
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self.select_session(str(item.data(Qt.ItemDataRole.UserRole)))

    def _on_session_item_clicked(self, item: QListWidgetItem) -> None:
        if not self._multi_select_mode:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id:
            session_id = str(session_id)
            self.set_session_selected(session_id, session_id not in self._selected_session_ids)

    def toggle_multi_select_mode(self) -> None:
        self.set_multi_select_mode(not self._multi_select_mode)

    def set_multi_select_mode(self, enabled: bool) -> None:
        self._multi_select_mode = bool(enabled)
        if not self._multi_select_mode:
            self._selected_session_ids.clear()
        self.batch_action_bar.setVisible(self._multi_select_mode)
        self.multi_select_button.setIcon(
            vector_widget_icon(
                self.multi_select_button,
                "remove" if self._multi_select_mode else "multi_select",
                16,
            )
        )
        self.multi_select_button.setToolTip(
            "退出批量管理" if self._multi_select_mode else "批量管理会话"
        )
        self._update_multi_select_caption()
        self._refresh_sessions()

    def set_session_selected(self, session_id: str, selected: bool) -> None:
        if not self._multi_select_mode:
            return
        if selected:
            self._selected_session_ids.add(str(session_id))
        else:
            self._selected_session_ids.discard(str(session_id))
        self._update_multi_select_caption()
        self._refresh_sessions()

    def _update_multi_select_caption(self) -> None:
        count = len(self._selected_session_ids)
        self.session_caption.setText(f"已选择 {count} 个对话" if self._multi_select_mode else "会话")
        enabled = count > 0
        self.batch_pin_button.setEnabled(enabled)
        self.batch_delete_button.setEnabled(enabled)

    def pin_selected_sessions(self) -> None:
        for session_id in tuple(self._selected_session_ids):
            session = self.store.load(session_id, self.character_id)
            if session is not None:
                session.pinned = True
                self.store.save(session)
        self.set_multi_select_mode(False)

    def delete_selected_sessions(self) -> None:
        self._delete_sessions(tuple(self._selected_session_ids))

    def _session_action(self, session_id: str, operation: str) -> None:
        session = self.store.load(session_id, self.character_id)
        if session is None:
            return
        if operation == "rename":
            title, accepted = QInputDialog.getText(
                self, "重命名会话", "会话名称", text=_short_title(session),
            )
            if not accepted:
                return
            session.custom_title = str(title).strip()[:60]
            self.store.save(session)
        elif operation == "pin":
            session.pinned = not bool(getattr(session, "pinned", False))
            self.store.save(session)
        elif operation == "delete":
            self._delete_sessions((session.session_id,))
            return
        self._refresh_sessions()

    def new_session(self) -> None:
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        # B8：切换会话前把旧会话（含生成中/停止的提问）入队保存，避免异步期间丢失
        self.store.save(self.session)
        self.session = self._new_session()
        self._clear_message_rows()
        self._set_empty_state(True)
        self._refresh_sessions()
        self._reset()

    def select_session(self, session_id: str) -> None:
        if not session_id or session_id == self.session.session_id:
            return
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        # B8：切换前保存当前会话最新状态（与 _stopped 的请求身份过滤互补）
        self.store.save(self.session)
        session = self.store.load(session_id, self.character_id)
        if session is None:
            self._refresh_sessions()
            return
        self.session = session
        self._load()
        self._refresh_sessions()
        self._reset()

    def delete_current_session(self) -> None:
        self._delete_sessions((self.session.session_id,))

    def _delete_sessions(self, session_ids) -> bool:
        sessions = [
            session
            for session_id in dict.fromkeys(str(value) for value in session_ids)
            if (session := self.store.load(session_id, self.character_id)) is not None
        ]
        if not sessions:
            return False
        dialog = DeleteConversationDialog(len(sessions), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        deleting_ids = {session.session_id for session in sessions}
        current_deleted = self.session.session_id in deleting_ids
        if current_deleted and self.service.busy:
            self._active_request_id = None
            self.service.stop()
        for session in sessions:
            self.store.delete(session)
        if current_deleted:
            remaining = self.store.list(self.character_id)
            self.session = remaining[0] if remaining else self._new_session()
            self._load()
        self._multi_select_mode = False
        self._selected_session_ids.clear()
        self.batch_action_bar.hide()
        self._update_multi_select_caption()
        self._refresh_sessions()
        return True

    def clear_session(self) -> None:
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        self.store.clear(self.session)
        self._clear_message_rows()
        self._set_empty_state(True)
        self._refresh_sessions()
        self._reset()

    def append_look_sync(self, user_text: str, reply: str) -> None:
        """「看看屏幕」的问答同步进当前会话，之后可在聊天历史里回看。
        正在生成回答时不插入，避免与在飞请求的流式输出交错。"""
        if self.service.busy:
            return
        self.session.messages.append(ChatMessage("user", user_text))
        self.session.messages.append(ChatMessage("assistant", reply))
        self.store.save(self.session)
        if self.isVisible():
            self._load()
            self._refresh_sessions()
            self._bottom()

    def refresh_settings(self) -> None:
        self.settings = self.config.chat_settings()
        self.provider_label.setText(self.settings.active_config.model)
        self.provider_label.setToolTip(self.settings.active_config.model)
        self._apply_character_theme()
        self._style()
        self._refresh_sessions()
        desired_pin = bool(self.config.get("chat_always_on_top", False))
        if desired_pin != bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint):
            self.set_chat_always_on_top(desired_pin)
        else:
            self.pin_button.setChecked(desired_pin)

    def switch_character(self, character_id: str) -> None:
        if not character_id or character_id == self.character_id:
            return
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        # B8：切换角色前保存旧角色会话（角色目录隔离，队列串行写不互踩）
        self.store.save(self.session)
        self.character_id = str(character_id)
        self._apply_character_theme()
        self.session = self._get_session()
        self._refresh_sessions()
        self._load()
        self._style()
        self._reset()

    def send_message(self) -> None:
        if self.service.busy:
            self.service.stop()
            return
        text = self.input.toPlainText().strip()
        if not text and not self.composer.attachment_paths:
            return
        attachment_names = [path.name for path in self.composer.attachment_paths]
        attachment_context = self.composer.attachment_prompt()
        image_payloads = self.composer.image_payloads()
        display_text = text or "请查看附件"
        if attachment_names:
            display_text += "\n\n附件：" + "、".join(attachment_names)
        request_text = text or "请分析这些附件。"
        if attachment_context:
            request_text += "\n\n" + attachment_context
        self.input.clear()
        self.composer.clear_attachments()
        self.session.messages.append(ChatMessage("user", display_text))
        self._add("user", display_text)
        self._last_user_text = request_text
        self._begin_generation(request_text, image_payloads=image_payloads)

    def retry_last(self) -> None:
        if self.service.busy:
            return
        text = self._last_user_text or next((m.content for m in reversed(self.session.messages) if m.role == "user"), "")
        if not text:
            return
        self._remove_bubble(self._bubble)
        image_payloads = None
        if isinstance(self._last_user_payload, list):
            image_payloads = [
                item for item in self._last_user_payload
                if isinstance(item, dict) and item.get("type") == "image_url"
            ]
        self._begin_generation(text, image_payloads=image_payloads)

    def _begin_generation(self, text: str, *, image_payloads: list[dict] | None = None) -> None:
        self._typewriter_timer.stop()
        self._pending_output = ""
        self._pending_finish_text = None
        self._stream_follow_output = True
        self._bubble = self._add("assistant", "")
        self._bubble.set_state("streaming")
        self._bubble.retry_requested.connect(self.retry_last)
        self._text = ""
        # B8：生成期间不落盘；整个交换在 _complete_finished 保存一次，
        # 停止/失败/切会话/关窗时强制 flush（提问不丢）。
        config = self.settings.active_config
        config.api_key = self.config.resolve_api_key(config)
        messages = self.prompt_builder.build_messages(self.settings, self.character_id, self.session.messages[:-1], text)
        if image_payloads:
            messages[-1]["content"] = [{"type": "text", "text": text}, *image_payloads]
        self._last_user_payload = messages[-1].get("content") if image_payloads else None
        self._active_request_id = self.service.send(messages, config)
        self._bottom()

    def _started(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        self.status.setText("思考中…")
        self.status_dot.setProperty("state", "busy")
        self.composer.set_busy(True)
        if self._bubble:
            self._bubble.set_state("streaming")
        self.pet_link.thinking()

    def _delta(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        if not text:
            return
        self._pending_output += str(text)
        if not self._typewriter_timer.isActive():
            self._typewriter_timer.start()
        self.status.setText("生成中…")

    def _typewriter_tick(self) -> None:
        if self._pending_output:
            # Large SSE chunks are common. Drain them in small adaptive pieces
            # so output remains readable without making long answers sluggish.
            count = max(1, min(10, (len(self._pending_output) + 39) // 40))
            self._text += self._pending_output[:count]
            self._pending_output = self._pending_output[count:]
            if self._bubble:
                self._bubble.set_content(self._text)
                self._bubble.set_state("streaming")
            self._update_conversation_height()
            self.pet_link.streaming(self._text)
            if self._stream_follow_output:
                self._bottom()
        if not self._pending_output and self._pending_finish_text is not None:
            final_text = self._pending_finish_text
            self._pending_finish_text = None
            self._typewriter_timer.stop()
            self._complete_finished(final_text)

    def _finished(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        text = str(text or "")
        if not text.strip():
            self._typewriter_timer.stop()
            self._pending_output = ""
            self._pending_finish_text = None
            self._error(request_id, "模型未返回任何内容，请稍后重试或检查模型配置。")
            return
        self._pending_finish_text = text
        # Reconcile the typewriter queue with the provider's authoritative
        # final response, including providers that emit no delta events.
        if text.startswith(self._text):
            self._pending_output = text[len(self._text):]
        else:
            # final 与已输出不一致（服务端截断/重写）：回到公共前缀处重打，
            # 避免整段重复显示
            common = 0
            limit = min(len(self._text), len(text))
            while common < limit and self._text[common] == text[common]:
                common += 1
            self._text = self._text[:common]
            self._pending_output = text[common:]
        if self._pending_output:
            if not self._typewriter_timer.isActive():
                self._typewriter_timer.start()
            return
        self._pending_finish_text = None
        self._complete_finished(text)

    def _complete_finished(self, text: str) -> None:
        if self._bubble:
            self._bubble.set_content(text)
            self._bubble.set_state("normal")
        self.session.messages.append(ChatMessage("assistant", text))
        # B8：流式结束保存一次（异步入队；同会话合并，GUI 线程不碰磁盘 I/O）
        self.store.save(self.session)
        self._refresh_sessions()
        self._reset()
        self.pet_link.success()
        if self._stream_follow_output:
            self._bottom()

    def _error(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        self._typewriter_timer.stop()
        self._pending_output = ""
        self._pending_finish_text = None
        if self._bubble:
            self._bubble.set_content("请求失败：" + str(text))
            self._bubble.set_state("error")
        # B8：失败时强制 flush——提问立即落盘（失败回复不保存）
        self.store.save(self.session)
        self._flush_forced('请求失败')
        self._reset()
        self.pet_link.error(text)
        self._bottom()

    def _stopped(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        self._typewriter_timer.stop()
        self._pending_output = ""
        self._pending_finish_text = None
        if self._bubble:
            if self._text:
                self._bubble.set_content(self._text)
                self._bubble.set_state("stopped")
            else:
                self._remove_bubble(self._bubble)
        # B8：停止时强制 flush——已输入内容立即落盘
        self.store.save(self.session)
        self._flush_forced('生成停止')
        self._reset()

    def _flush_forced(self, context: str) -> None:
        """强制 flush 落盘；失败必须被上层感知（记录日志，供排查与提示）。"""
        if not self.store.flush():
            _logger.error('%s强制落盘失败（用户数据可能未保存）: session=%s last_error=%s',
                          context, self.session.session_id, self.store.last_error)

    def _reset(self) -> None:
        # 停止打字机并丢弃未排空的输出：模型完成后若仍有 ~1 秒的逐字
        # 排空窗口，此时切换/新建/删除会话或换角色会把这轮回复继续
        # append 并保存进"新"会话（原会话丢回复、新会话多幻影消息）。
        self._typewriter_timer.stop()
        self._pending_output = ""
        self._pending_finish_text = None
        self._active_request_id = None
        self.status.setText("就绪")
        self.status_dot.setProperty("state", "ready")
        self.composer.set_busy(False)
        self._refresh_status_style()

    def _refresh_status_style(self) -> None:
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    def _is_near_bottom(self, threshold: int = 24) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - threshold

    def _on_scroll_value_changed(self, _value: int) -> None:
        """用户滚动位置决定是否继续跟随输出；上翻阅读时暂停自动滚底。"""
        self._stream_follow_output = self._is_near_bottom()

    def _bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, self, self._apply_bottom)
        # 切会话后布局更新可能晚于 singleShot(0)（内容高度尚未重算，
        # maximum 仍为 0）；再加一拍兜底，保证切回长会话时落在底部。
        QTimer.singleShot(80, self, self._apply_bottom)

    def _apply_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive_layout()
        margin = 20 if self._compact_layout else 76
        self.composer_card_layout.setContentsMargins(margin, 7, margin, 14 if self._compact_layout else 20)
        self.message_horizontal_margin = 22 if self._compact_layout else 76
        self.message_host_layout.setContentsMargins(
            self.message_horizontal_margin, 24, self.message_horizontal_margin, 24
        )
        self._update_bubble_widths()
        QTimer.singleShot(0, self, self._update_conversation_height)

    _EDGE_GRIP = 8

    def _edge_at(self, pos) -> set[str]:
        edges: set[str] = set()
        x, y = pos.x(), pos.y()
        if x < self._EDGE_GRIP:
            edges.add("left")
        elif x > self.width() - self._EDGE_GRIP:
            edges.add("right")
        if y < self._EDGE_GRIP:
            edges.add("top")
        elif y > self.height() - self._EDGE_GRIP:
            edges.add("bottom")
        return edges

    def _edge_cursor(self, edges: set[str]) -> Qt.CursorShape:
        if edges == {"left", "top"} or edges == {"right", "bottom"}:
            return Qt.CursorShape.SizeFDiagCursor
        if edges == {"right", "top"} or edges == {"left", "bottom"}:
            return Qt.CursorShape.SizeBDiagCursor
        if "left" in edges or "right" in edges:
            return Qt.CursorShape.SizeHorCursor
        if "top" in edges or "bottom" in edges:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def event(self, event) -> bool:  # noqa: N802
        # Hover 事件（WA_Hover）会持续投递到本窗口，即使光标位于未启用鼠标
        # 跟踪的子控件上方；用它刷新边缘缩放光标，避免进入窗口后光标卡死
        # 在缩放双箭头。位置始终是窗口本地坐标。
        if event.type() == QEvent.Type.HoverMove:
            self.setCursor(self._edge_cursor(self._edge_at(event.position())))
        elif event.type() == QEvent.Type.HoverLeave:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(event.position())
            if edges:
                self._resize_edges = edges
                self._resize_global_start = event.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
                self.setCursor(self._edge_cursor(edges))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_global_start is not None and self._resize_start_geometry is not None:
            start = self._resize_start_geometry
            delta = event.globalPosition().toPoint() - self._resize_global_start
            x, y, w, h = start.x(), start.y(), start.width(), start.height()
            if "left" in self._resize_edges:
                x = start.x() + delta.x()
                w = start.width() - delta.x()
            if "right" in self._resize_edges:
                w = start.width() + delta.x()
            if "top" in self._resize_edges:
                y = start.y() + delta.y()
                h = start.height() - delta.y()
            if "bottom" in self._resize_edges:
                h = start.height() + delta.y()
            w = min(max(w, self.minimumWidth()), self.maximumWidth())
            h = min(max(h, self.minimumHeight()), self.maximumHeight())
            # 尺寸被 clamp 时位置必须随尺寸回退：左/上边缘止步于对侧边缘，
            # 否则窗口会被推出屏幕（如左边缘右拖越过最小宽度时整体右移）
            if "left" in self._resize_edges:
                x = start.right() - w
            if "top" in self._resize_edges:
                y = start.bottom() - h
            self.setGeometry(QRect(x, y, w, h))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._resize_edges = set()
        self._resize_global_start = None
        self._resize_start_geometry = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def eventFilter(self, obj, event):
        scroll = getattr(self, "scroll", None)
        if scroll is not None and obj is scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self, self._update_bubble_widths)
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        """关闭=隐藏并复用窗口：先入队当前会话（含未提交提问），再停止生成，flush 落盘。

        停止前直接把当前会话入队：不依赖 worker 的 queued stopped 回调——该回调在
        退出事件循环停止后不会执行，生成中的提问可能从未入队而丢失。
        """
        self._typewriter_timer.stop()
        try:
            self.store.save(self.session)
        except Exception:
            _logger.exception('关闭窗口时保存会话失败')
        self.service.stop()
        self._active_request_id = None
        self._flush_forced('关闭窗口')
        self.set_pet_window(None)
        self.hide()
        event.ignore()
