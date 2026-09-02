from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QStyle,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .models import ChatMessage
from .pet_link import PetChatLink
from .prompt import PromptBuilder, load_character_manifest
from .service import ChatService
from .session_store import SessionStore
from ..context_menus.icons import vector_widget_icon
from . import themes as chat_themes


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_ACCENT = "#3994ff"


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
        return "新会话 · " + datetime.fromisoformat(session.created_at).strftime("%H:%M")
    except (TypeError, ValueError):
        return "新会话"


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
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.avatar = QLabel("你" if role == "user" else _initial(character_id))
        self.avatar.setObjectName("bubble-avatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(34, 34)
        self.avatar.setProperty("role", role)
        root.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        panel = QVBoxLayout()
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(5)
        self.meta = QLabel("你" if role == "user" else "桌宠")
        self.meta.setObjectName("bubble-meta")
        panel.addWidget(self.meta)

        self.body = QLabel()
        self.body.setObjectName("bubble-body")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setFont(QFont("Microsoft YaHei UI", 10))
        self.body.setText(content)
        panel.addWidget(self.body)

        self.status_label = QLabel()
        self.status_label.setObjectName("bubble-status")
        self.status_label.hide()
        panel.addWidget(self.status_label)

        self.retry_button = QPushButton("重试")
        self.retry_button.setObjectName("retry-button")
        self.retry_button.clicked.connect(self.retry_requested)
        self.retry_button.hide()
        panel.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        root.addLayout(panel, 1)

        if role == "user":
            root.setDirection(QHBoxLayout.Direction.RightToLeft)

    def set_content(self, text: str) -> None:
        self.body.setText(str(text))

    def set_state(self, state: str) -> None:
        self.state = state
        self.setProperty("state", state)
        if state == "streaming":
            self.status_label.setText("正在生成…")
            self.status_label.show()
            self.retry_button.hide()
        elif state == "error":
            self.status_label.setText("本次回复未保存")
            self.status_label.show()
            self.retry_button.show()
        elif state == "stopped":
            self.status_label.setText("已停止生成")
            self.status_label.show()
            self.retry_button.hide()
        else:
            self.status_label.hide()
            self.retry_button.hide()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class ChatComposer(QFrame):
    send_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat-composer")
        self._busy = False
        self._ime_composing = False  # 输入法组合中（防止回车误发送）
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(7)

        self.input = QPlainTextEdit()
        self.input.setObjectName("chat-input")
        self.input.setPlaceholderText("和桌宠说点什么…  Enter 发送，Shift+Enter 换行")
        self.input.setMinimumHeight(82)
        self.input.setMaximumHeight(150)
        self.input.installEventFilter(self)
        root.addWidget(self.input)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("内容会保存到当前角色的本地会话")
        self.hint.setObjectName("composer-hint")
        footer.addWidget(self.hint)
        footer.addStretch(1)
        self.send = QPushButton("发送")
        self.send.setObjectName("send-button")
        self.send.setMinimumWidth(92)
        self.send.clicked.connect(self.send_requested)
        footer.addWidget(self.send)
        self.grip = QSizeGrip(self)
        self.grip.setObjectName("composer-size-grip")
        footer.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(footer)
        self.input.textChanged.connect(self._update_enabled)
        self._update_enabled()

    def eventFilter(self, obj, event):
        # 输入法组合状态跟踪：组合中回车用于上屏候选，不应触发发送
        if event.type() == QEvent.Type.InputMethod:
            self._ime_composing = bool(event.preeditString())
            if event.commitString():
                self._ime_composing = False
        elif obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if getattr(self, "_ime_composing", False):
                    return False  # 交给输入法上屏候选
                self.send_requested.emit()
                return True
        return super().eventFilter(obj, event)

    def _update_enabled(self) -> None:
        self.send.setEnabled(self._busy or bool(self.input.toPlainText().strip()))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.send.setText("停止" if self._busy else "发送")
        self.send.setProperty("busy", self._busy)
        self._update_enabled()
        self.send.style().unpolish(self.send)
        self.send.style().polish(self.send)


class ChatWindow(QDialog):
    def __init__(self, config, character_id: str, parent=None, pet_window=None, notifier=None, auth_callback=None):
        super().__init__(parent)
        self.config = config
        self.character_id = str(character_id)
        self._system_notifier = notifier
        self._auth_callback = auth_callback
        self.setObjectName("chat-window")
        self.setWindowTitle("AI 对话")
        # 窗口级图标主题：浅色表面用深灰轮廓图标（rename 按钮等），
        # 避免深色系统 palette 白前景造成白底白图
        self.setProperty("menuStyle", "modern")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        # 手机式聊天窗尺寸范围：最小 380×620，最大 560×980
        self.setMinimumSize(380, 620)
        self.setMaximumSize(560, 980)
        self.resize(430, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 无边框圆角窗：窗口自身透明（QSS #chat-window 背景同步置 transparent），
        # 只显示圆角的 phone-shell，去掉窗外一圈方形背景（深色系统下是黑框）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

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
        if self.follow_pet and pet_window is not None and hasattr(pet_window, "add_position_listener"):
            pet_window.add_position_listener(self._on_pet_moved)
            self._follow_pet_window = pet_window

    def set_follow_pet(self, enabled: bool, persist: bool = True) -> None:
        self.follow_pet = bool(enabled)
        self.follow_button.blockSignals(True)
        self.follow_button.setChecked(self.follow_pet)
        self.follow_button.blockSignals(False)
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
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)
        self.phone_shell = QFrame(self)
        self.phone_shell.setObjectName("phone-shell")
        self.phone_shell.setAutoFillBackground(True)
        outer.addWidget(self.phone_shell)

        root = QVBoxLayout(self.phone_shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = ChatTitleBar(self.phone_shell)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(22, 14, 16, 12)
        title_layout.setSpacing(12)
        self.avatar_label = QLabel(_initial(self.character_id))
        self.avatar_label.setObjectName("avatar-label")
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setFixedSize(42, 42)
        title_layout.addWidget(self.avatar_label)
        title_text = QVBoxLayout()
        title_text.setSpacing(1)
        self.title_label = QLabel(f"{self.character_name} · AI 对话")
        self.title_label.setObjectName("title-label")
        self.subtitle_label = QLabel("陪伴式对话空间")
        self.subtitle_label.setObjectName("subtitle-label")
        title_text.addWidget(self.title_label)
        title_text.addWidget(self.subtitle_label)
        title_layout.addLayout(title_text)
        title_layout.addStretch(1)
        self.pin_button = QToolButton()
        self.pin_button.setObjectName("window-pin-button")
        self.pin_button.setIcon(vector_widget_icon(self.pin_button, "pin", 16))
        self.pin_button.setToolTip("窗口置顶")
        self.pin_button.setAccessibleName("切换聊天窗口置顶")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(bool(self.config.get("chat_always_on_top", False)))
        title_layout.addWidget(self.pin_button)
        self.minimize_button = QToolButton()
        self.minimize_button.setObjectName("window-minimize-button")
        self.minimize_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton))
        self.minimize_button.setToolTip("最小化")
        self.minimize_button.setAccessibleName("最小化聊天窗口")
        self.close_button = QToolButton()
        self.close_button.setObjectName("window-close-button")
        self.close_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        self.close_button.setToolTip("关闭")
        self.close_button.setAccessibleName("关闭聊天窗口")
        title_layout.addWidget(self.minimize_button)
        title_layout.addWidget(self.close_button)
        root.addWidget(self.title_bar)

        context = QFrame(self.phone_shell)
        context.setObjectName("chat-context-bar")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(16, 10, 16, 10)
        context_layout.setSpacing(7)

        context_top = QHBoxLayout()
        context_top.setContentsMargins(0, 0, 0, 0)
        context_top.setSpacing(7)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("status-dot")
        context_top.addWidget(self.status_dot)
        self.status = QLabel("就绪")
        self.status.setObjectName("status-label")
        context_top.addWidget(self.status)
        self.provider_label = QLabel(self.settings.active_config.name)
        self.provider_label.setObjectName("provider-label")
        self.provider = self.provider_label
        context_top.addWidget(self.provider_label)
        context_top.addStretch(1)
        self.follow_button = QToolButton()
        self.follow_button.setObjectName("follow-pet-button")
        self.follow_button.setText("\u8ddf\u968f\u684c\u5ba0")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(self.follow_pet)
        self.follow_button.setToolTip("\u804a\u5929\u7a97\u53e3\u8ddf\u968f\u684c\u5ba0\u79fb\u52a8")
        self.follow_button.setAccessibleName("\u804a\u5929\u7a97\u8ddf\u968f\u684c\u5ba0")
        context_top.addWidget(self.follow_button)
        context_layout.addLayout(context_top)

        context_bottom = QHBoxLayout()
        context_bottom.setContentsMargins(0, 0, 0, 0)
        context_bottom.setSpacing(6)
        self.session_caption = QLabel("会话")
        self.session_caption.setObjectName("context-caption")
        context_bottom.addWidget(self.session_caption)
        self.session_combo = QComboBox()
        self.session_combo.setObjectName("session-combo")
        session_view = self.session_combo.view()
        session_view.setObjectName("session-list")
        # Windows 上 QComboBox 弹出列表不完全跟随 QSS 配色，
        # 这里直接给控件和视图设置调色板，保证浅色主题下可读。
        self._apply_session_palette(self.session_combo)
        self._apply_session_palette(session_view)
        self.session_combo.setMinimumWidth(0)
        self.session_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        context_bottom.addWidget(self.session_combo, 1)
        self.new_session_button = QToolButton(self.phone_shell)
        self.new_session_button.setObjectName("new-session-button")
        self.new_session_button.setIcon(vector_widget_icon(self.new_session_button, "add", 15))
        self.new_session_button.setToolTip("新建会话")
        self.new_session_button.setAccessibleName("新建会话")
        self.rename_session_button = QToolButton(self.phone_shell)
        self.rename_session_button.setObjectName("rename-session-button")
        self.rename_session_button.setIcon(vector_widget_icon(self.rename_session_button, "rename", 15))
        self.rename_session_button.setToolTip("重命名当前会话")
        self.rename_session_button.setAccessibleName("重命名当前会话")
        self.delete_session_button = QToolButton(self.phone_shell)
        self.delete_session_button.setObjectName("delete-session-button")
        self.delete_session_button.setIcon(vector_widget_icon(self.delete_session_button, "remove", 15))
        self.delete_session_button.setToolTip("删除当前会话")
        self.delete_session_button.setAccessibleName("删除当前会话")
        self.clear_button = QToolButton(self.phone_shell)
        self.clear_button.setObjectName("clear-session-button")
        self.clear_button.setIcon(vector_widget_icon(self.clear_button, "clear", 15))
        self.clear_button.setToolTip("清空当前会话")
        self.clear_button.setAccessibleName("清空当前会话")
        context_bottom.addWidget(self.new_session_button)
        context_bottom.addWidget(self.rename_session_button)
        context_bottom.addWidget(self.delete_session_button)
        context_bottom.addWidget(self.clear_button)
        context_layout.addLayout(context_bottom)
        root.addWidget(context)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("message-scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.message_view = QWidget()
        self.message_stack = QStackedLayout(self.message_view)
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(28, 80, 28, 80)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state = QLabel("先从一句问候开始吧\n我会在这里陪你聊天、记录和思考")
        self.empty_state.setObjectName("empty-state")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)
        empty_layout.addWidget(self.empty_state)
        self.timeline_host = QWidget()
        self.message_host_layout = QVBoxLayout(self.timeline_host)
        self.message_host_layout.setContentsMargins(22, 24, 22, 24)
        self.message_host_layout.setSpacing(16)
        self.message_host_layout.addStretch(1)
        self.message_stack.addWidget(self.empty_page)
        self.message_stack.addWidget(self.timeline_host)
        self.scroll.setWidget(self.message_view)
        self.scroll.setAutoFillBackground(True)
        root.addWidget(self.scroll, 1)

        self.composer = ChatComposer(self)
        self.input = self.composer.input
        self.send = self.composer.send
        root.addWidget(self.composer)

        self.title = self.title_label
        self._set_empty_state(True)

    def _connect(self) -> None:
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button.clicked.connect(self.close)
        self.pin_button.clicked.connect(self.toggle_always_on_top)
        self.new_session_button.clicked.connect(self.new_session)
        self.rename_session_button.clicked.connect(self.rename_current_session)
        self.delete_session_button.clicked.connect(self.delete_current_session)
        self.clear_button.clicked.connect(self.clear_session)
        self.follow_button.toggled.connect(self.set_follow_pet)
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        self.composer.send_requested.connect(self.send_message)
        self.service.started.connect(self._started)
        self.service.delta.connect(self._delta)
        self.service.finished.connect(self._finished)
        self.service.error.connect(self._error)
        self.service.stopped.connect(self._stopped)

    def _style(self) -> None:
        try:
            stylesheet = (Path(__file__).with_name("legacy_styles.qss")).read_text(encoding="utf-8")
            self._bg_pixmap = self._resolve_bg_pixmap()
            self._bg_scaled = None
            self._set_background_surface_transparency(self._bg_pixmap is not None)
            if self._bg_pixmap is not None:
                overlay_theme = self._bg_theme or {"accent": self._base_accent, "dark": False}
                self.accent_color = overlay_theme["accent"]
                stylesheet += chat_themes.build_overlay_qss(overlay_theme)
            else:
                self.accent_color = self._base_accent
            self.setStyleSheet(stylesheet.replace("@ACCENT@", self.accent_color))
        except OSError:
            pass
        self._apply_avatar_style(self.avatar_label, self.accent_color)
        self._apply_session_palette(self.session_combo)
        self._apply_session_palette(self.session_combo.view())
        for bubble in self._bubbles:
            self._apply_avatar_style(bubble.avatar, self.accent_color if bubble.role == "assistant" else "#2b75d6")
        self.update()

    def _set_background_surface_transparency(self, enabled: bool) -> None:
        """Let the wallpaper show through the classic scroll viewport."""
        for widget in (
            self.phone_shell, self.scroll, self.scroll.viewport(), self.message_view,
            self.empty_page, self.timeline_host,
        ):
            widget.setAutoFillBackground(not enabled)

    def _resolve_bg_pixmap(self):
        self._bg_value = str(self.config.get("chat_background", "") or "").strip()
        try:
            opacity = int(self.config.get("chat_background_opacity", 100))
        except (TypeError, ValueError):
            opacity = 100
        self._bg_opacity = max(10, min(100, opacity)) / 100.0
        fill = str(self.config.get("chat_background_fill", "cover") or "cover")
        self._bg_fill = fill if fill in {"cover", "contain", "stretch"} else "cover"
        self._bg_theme = (
            chat_themes.get_theme(self._bg_value[8:])
            if self._bg_value.startswith("builtin:") else None
        )
        return chat_themes.resolve_background_pixmap(self._bg_value)

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

    def _apply_character_theme(self) -> None:
        root = Path(__file__).resolve().parents[2] / "assets" / "characters"
        self._character_manifest = load_character_manifest(root, self.character_id)
        chat = self._character_manifest.get("chat", {})
        chat = chat if isinstance(chat, dict) else {}
        self.character_name = str(self._character_manifest.get("name") or chat.get("name") or self.character_id)
        self.accent_color = _safe_color(chat.get("theme_color"))
        self._base_accent = self.accent_color
        self.title_label.setText(f"{self.character_name} · AI 对话")
        self.avatar_label.setText(_initial(self.character_id))
        self._apply_avatar_style(self.avatar_label, self.accent_color)

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

    def _add(self, role: str, text: str) -> MessageBubble:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        bubble = MessageBubble(role, text, self.character_id)
        self._apply_avatar_style(bubble.avatar, self.accent_color if role == "assistant" else "#2b75d6")
        if role == "user":
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        self.message_host_layout.insertLayout(max(0, self.message_host_layout.count() - 1), row)
        self._bubbles.append(bubble)
        self._set_empty_state(False)
        self._update_bubble_widths()
        return bubble

    def _update_bubble_widths(self) -> None:
        width = max(320, int(self.scroll.viewport().width() * 0.72))
        for bubble in self._bubbles:
            bubble.setMaximumWidth(width)

    def _remove_bubble(self, bubble: MessageBubble | None) -> None:
        if bubble is None:
            return
        for index in range(max(0, self.message_host_layout.count() - 1)):
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
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        selected = -1
        for index, session in enumerate(sessions):
            self.session_combo.addItem(_short_title(session), session.session_id)
            # 设置列表项前景色：让会话标题在浅色弹层中清晰可读
            self.session_combo.setItemData(index, QColor("#1f2937"), Qt.ItemDataRole.ForegroundRole)
            if session.session_id == self.session.session_id:
                selected = index
        if selected < 0:
            selected = 0
            self.session = sessions[0]
        self.session_combo.setCurrentIndex(selected)
        self.session_combo.blockSignals(False)
        self.session_combo.setToolTip(f"当前会话：{self.session.session_id[:8]}")

    def _on_session_changed(self, index: int) -> None:
        if index < 0:
            return
        self.select_session(str(self.session_combo.itemData(index)))

    def new_session(self) -> None:
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        self.session = self._new_session()
        self._clear_message_rows()
        self._set_empty_state(True)
        self._refresh_sessions()
        self._reset()

    def rename_current_session(self) -> None:
        """重命名当前会话（与新版窗口一致的交互：输入框预填当前标题）。"""
        title, accepted = QInputDialog.getText(
            self, "重命名会话", "会话名称", text=_short_title(self.session),
        )
        if not accepted:
            return
        self.session.custom_title = str(title).strip()[:60]
        self.store.save(self.session)
        self._refresh_sessions()

    def select_session(self, session_id: str) -> None:
        if not session_id or session_id == self.session.session_id:
            return
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        session = self.store.load(session_id, self.character_id)
        if session is None:
            self._refresh_sessions()
            return
        self.session = session
        self._load()
        self._refresh_sessions()
        self._reset()

    def delete_current_session(self) -> None:
        if self.service.busy:
            self._active_request_id = None
            self.service.stop()
        self.store.delete(self.session)
        sessions = self.store.list(self.character_id)
        self.session = sessions[0] if sessions else self._new_session()
        self._load()
        self._refresh_sessions()

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
        self.provider_label.setText(self.settings.active_config.name)
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
        if not text:
            return
        self.input.clear()
        self.session.messages.append(ChatMessage("user", text))
        self._add("user", text)
        self._last_user_text = text
        self._begin_generation(text)

    def retry_last(self) -> None:
        if self.service.busy:
            return
        text = self._last_user_text or next((m.content for m in reversed(self.session.messages) if m.role == "user"), "")
        if not text:
            return
        self._remove_bubble(self._bubble)
        self._begin_generation(text)

    def _begin_generation(self, text: str) -> None:
        self._bubble = self._add("assistant", "")
        self._bubble.set_state("streaming")
        self._bubble.retry_requested.connect(self.retry_last)
        self._text = ""
        self.store.save(self.session)
        config = self.settings.active_config
        config.api_key = self.config.resolve_api_key(config)
        messages = self.prompt_builder.build_messages(self.settings, self.character_id, self.session.messages[:-1], text)
        self._active_request_id = self.service.send(messages, config)
        self._bottom()

    def _focus_chat(self) -> None:
        """把聊天窗口带回前台，供系统通知点击后跳回页面处理。"""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _focus_auth_settings(self) -> None:
        if callable(self._auth_callback):
            self._auth_callback()
        else:
            self._focus_chat()

    def _show_system_notice(self, title: str, message: str, *, on_click=None) -> None:
        """仅在聊天窗口不是当前活动窗口时弹系统通知；点击默认跳回本窗口。"""
        if self._system_notifier is None or self.isActiveWindow():
            return
        try:
            self._system_notifier(title, message, on_click=on_click or self._focus_chat)
        except Exception:
            logging.getLogger(__name__).exception("系统通知发送失败")

    @staticmethod
    def _looks_like_authorization_error(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(
            token in lowered
            for token in ("401", "403", "unauthorized", "authentication", "api key",
                          "认证失败", "未授权", "授权")
        )

    def notify_authorization_required(self, message: str = "有一条需要授权或确认的请求，点击查看。") -> None:
        """供“需要授权/审批”类事件调用：切走窗口时弹系统通知并跳回聊天页。"""
        self._show_system_notice("需要授权", message)

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
        # 每次 delta 只更新文本；若用户停留在底部则跟随滚动
        follow_output = self._is_near_bottom()
        self._text += text
        if self._bubble:
            self._bubble.set_content(self._text)
            self._bubble.set_state("streaming")
        self.status.setText("生成中…")
        self.pet_link.streaming(self._text)
        if follow_output:
            self._bottom()

    def _finished(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        follow_output = self._is_near_bottom()
        if self._bubble:
            self._bubble.set_content(text)
            self._bubble.set_state("normal")
        self.session.messages.append(ChatMessage("assistant", text))
        self.store.save(self.session)
        self._refresh_sessions()
        self._reset()
        self.pet_link.success()
        self._show_system_notice("对话完成", "AI 已回复完成，点击查看。")
        if follow_output:
            self._bottom()

    def _error(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        if self._bubble:
            self._bubble.set_content("请求失败：" + str(text))
            self._bubble.set_state("error")
        self._reset()
        self.pet_link.error(text)
        if self._looks_like_authorization_error(text):
            self._show_system_notice(
                "需要授权",
                "模型服务返回认证失败，点击打开 AI 设置检查 API Key。",
                on_click=self._focus_auth_settings,
            )
        else:
            self._show_system_notice("生成失败", f"对话生成失败：{str(text)[:100]}")
        self._bottom()

    def _stopped(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        if self._bubble:
            if self._text:
                self._bubble.set_content(self._text)
                self._bubble.set_state("stopped")
            else:
                self._remove_bubble(self._bubble)
        self._reset()

    def _reset(self) -> None:
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

    def _bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # 切会话后布局更新可能晚于当前事件循环拍；加一拍兜底，
        # 保证切回长会话时落在底部（与新版窗口一致）。
        from PySide6.QtCore import QTimer
        QTimer.singleShot(80, self, lambda bar=bar: bar.setValue(bar.maximum()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_bubble_widths()

    def closeEvent(self, event) -> None:
        """关闭=隐藏并复用窗口：停止生成、解除桌宠位置监听，避免泄漏。"""
        self.service.stop()
        self.set_pet_window(None)
        self.hide()
        event.ignore()
