from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
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
from . import themes as chat_themes
from .service import ChatService
from .session_store import SessionStore


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_ACCENT = "#7b8fd9"



def _safe_color(value: object) -> str:
    value = str(value or "")
    return value if _COLOR_RE.fullmatch(value) else _DEFAULT_ACCENT


def _initial(character_id: str) -> str:
    text = str(character_id or "宠").strip()
    return text[:1].upper() or "宠"


def _short_title(session) -> str:
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
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
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


def resolve_bg_pixmap(config_value: str):
    """模块级背景解析（裁切编辑器共用）：builtin:<key> → 内置主题壁纸；否则按路径。
    空或文件不存在返回 None。"""
    value = (config_value or '').strip()
    if not value:
        return None
    if value.startswith('builtin:'):
        theme = chat_themes.get_theme(value[8:])
        if theme is None:
            return None
        name = theme['file']
        candidates = []
        meipass = getattr(sys, '_MEIPASS', None)  # PyInstaller 冻结环境的资源根（权威）
        if meipass:
            candidates.append(Path(meipass) / 'assets' / 'chat' / name)
        candidates.append(Path(__file__).resolve().parents[2] / 'assets' / 'chat' / name)
        base = next((c for c in candidates if c.is_file()), None)
        if base is None:
            return None
    else:
        base = Path(value)
        if not base.is_file():
            return None
    pix = QPixmap(str(base))
    return pix if not pix.isNull() else None


class ChatWindow(QDialog):
    def __init__(self, config, character_id: str, parent=None, pet_window=None):
        super().__init__(parent)
        self.config = config
        self.character_id = str(character_id)
        self.setObjectName("chat-window")
        self.setWindowTitle("AI 对话")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        # 手机式聊天窗尺寸范围：最小 380×620，最大 560×980
        self.setMinimumSize(380, 620)
        self.setMaximumSize(560, 980)
        self.resize(430, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 半透明窗口：外层 QDialog 透明，phone-shell 的圆角才是真轮廓；
        # 也是自定义背景图（paintEvent 圆角裁剪绘制）的前提
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.settings = config.chat_settings()
        self.prompt_builder = PromptBuilder(Path(__file__).resolve().parents[2] / "assets" / "characters")
        self.store = SessionStore(config.dir)
        self.session = self._get_session()
        self.service = ChatService(parent=self)
        self.pet_link = PetChatLink(pet_window)
        self._bubble: MessageBubble | None = None
        self._bubbles: list[MessageBubble] = []
        self._text = ""
        self._active_request_id: str | None = None
        self._last_user_text = ""
        self.accent_color = _DEFAULT_ACCENT
        self._base_accent = _DEFAULT_ACCENT  # 角色 manifest accent 应用前的基准
        self._bg_pixmap = None      # 聊天背景图（paintEvent 绘制）
        self._bg_theme = None       # 当前背景主题（themes.THEMES 条目）
        self._bg_value = ''         # 当前背景的 config 标识（自定义取景框的键）
        self._bg_scaled = None      # 按窗口尺寸缓存的缩放结果
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
        context_layout.setContentsMargins(16, 6, 16, 8)
        context_layout.setSpacing(7)

        # 单行工具栏：[状态点][状态][provider] [会话下拉(弹性)] [新建/删除/清空][跟随桌宠]
        context_bottom = QHBoxLayout()
        context_bottom.setContentsMargins(0, 0, 0, 0)
        context_bottom.setSpacing(6)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("status-dot")
        context_bottom.addWidget(self.status_dot)
        self.status = QLabel("就绪")
        self.status.setObjectName("status-label")
        context_bottom.addWidget(self.status)
        self.provider_label = QLabel(self.settings.active_config.name)
        self.provider_label.setObjectName("provider-label")
        self.provider = self.provider_label
        context_bottom.addWidget(self.provider_label)
        self.follow_button = QToolButton()
        self.follow_button.setObjectName("follow-pet-button")
        self.follow_button.setText("\u8ddf\u968f\u684c\u5ba0")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(self.follow_pet)
        self.follow_button.setToolTip("\u804a\u5929\u7a97\u53e3\u8ddf\u968f\u684c\u5ba0\u79fb\u52a8")
        self.follow_button.setAccessibleName("\u804a\u5929\u7a97\u8ddf\u968f\u684c\u5ba0")
        self.session_caption = QLabel("会话")
        self.session_caption.setObjectName("context-caption")
        self.session_caption.hide()  # 下拉框自明，隐藏"会话"文字让这排更干净
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
        self.new_session_button = QToolButton()
        self.new_session_button.setObjectName("new-session-button")
        self.new_session_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self.new_session_button.setToolTip("新建会话")
        self.new_session_button.setAccessibleName("新建会话")
        self.delete_session_button = QToolButton()
        self.delete_session_button.setObjectName("delete-session-button")
        self.delete_session_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.delete_session_button.setToolTip("删除当前会话")
        self.delete_session_button.setAccessibleName("删除当前会话")
        self.clear_button = QToolButton()
        self.clear_button.setObjectName("clear-session-button")
        self.clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        self.clear_button.setToolTip("清空当前会话")
        self.clear_button.setAccessibleName("清空当前会话")
        context_bottom.addWidget(self.new_session_button)
        context_bottom.addWidget(self.delete_session_button)
        context_bottom.addWidget(self.clear_button)
        context_bottom.addWidget(self.follow_button)
        context_layout.addLayout(context_bottom)
        root.addWidget(context)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("message-scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.message_view = QWidget()
        self.message_view.setObjectName("message-view")
        self.message_stack = QStackedLayout(self.message_view)
        self.empty_page = QWidget()
        self.empty_page.setObjectName("message-timeline")
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(28, 80, 28, 80)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state = QLabel("")  # 空状态留白：壁纸/纯色本身就是背景
        self.empty_state.setObjectName("empty-state")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)
        empty_layout.addWidget(self.empty_state)
        self.timeline_host = QWidget()
        self.timeline_host.setObjectName("message-timeline")
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
        self.new_session_button.clicked.connect(self.new_session)
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
            stylesheet = (Path(__file__).with_name("styles.qss")).read_text(encoding="utf-8")
            self._bg_pixmap = self._resolve_bg_pixmap()
            if self._bg_pixmap is not None:
                self._bg_scaled = None
                if self._bg_theme is not None:
                    self.accent_color = self._bg_theme['accent']  # 主题 accent 优先于角色 manifest
                    stylesheet += chat_themes.build_overlay_qss(self._bg_theme)
            else:
                self.accent_color = self._base_accent  # 无背景时回到底色
            self.setStyleSheet(stylesheet.replace("@ACCENT@", self.accent_color))
        except OSError:
            pass
        # 强调色可能随主题/背景切换而变，这里统一刷新头像与会话调色板，
        # 不放进 paintEvent（无背景时 paintEvent 直接返回会导致样式不刷新）
        self._apply_avatar_style(self.avatar_label, self.accent_color)
        self._apply_session_palette(self.session_combo)
        self._apply_session_palette(self.session_combo.view())
        for bubble in self._bubbles:
            self._apply_avatar_style(bubble.avatar, self.accent_color)

    def _resolve_bg_pixmap(self):
        '''解析聊天背景图；记录当前标识与主题。'''
        value = str(self.config.get('chat_background', '') or '').strip()
        self._bg_value = value
        self._bg_theme = chat_themes.get_theme(value[8:]) if value.startswith('builtin:') else None
        return resolve_bg_pixmap(value)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._bg_pixmap is None:
            return super().paintEvent(event)
        # 圆角裁剪绘制背景图 + 暖白纱罩（壁纸垫底，面板半透浮于其上）
        target = self.phone_shell.geometry()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(target), 26.0, 26.0)
        p.setClipPath(path)
        if self._bg_scaled is None or self._bg_scaled_size != target.size():
            self._bg_scaled = self._bg_pixmap.scaled(
                target.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self._bg_scaled_size = target.size()
        sw, sh = self._bg_scaled.width(), self._bg_scaled.height()
        # 主体感知裁剪：cover 满铺后平移，让取景框完整可见；框比窗口大则居中于主体。
        # 用户在裁切编辑器里自定义的取景框优先于主题默认 focus。
        crops = self.config.get('chat_bg_crops', {})
        custom = crops.get(self._bg_value) if isinstance(crops, dict) else None
        fx = None
        if isinstance(custom, (list, tuple)) and len(custom) == 4:
            try:
                fx, fy, fw, fh = (float(v) for v in custom)
            except (TypeError, ValueError):
                fx = None  # 手改坏的配置：回退主题默认取景
        if fx is None:
            fx, fy, fw, fh = (self._bg_theme or {}).get('focus', (0.25, 0.0, 0.5, 1.0))
        x = target.x() + target.width() / 2.0 - (fx + fw / 2.0) * sw
        y = target.y() + target.height() / 2.0 - (fy + fh / 2.0) * sh
        if fw * sw <= target.width():
            x = min(max(x, target.x() + target.width() - (fx + fw) * sw), target.x() - fx * sw)
        if fh * sh <= target.height():
            y = min(max(y, target.y() + target.height() - (fy + fh) * sh), target.y() - fy * sh)
        x = min(max(x, target.x() + target.width() - sw), float(target.x()))
        y = min(max(y, target.y() + target.height() - sh), float(target.y()))
        p.drawPixmap(int(round(x)), int(round(y)), self._bg_scaled)
        r, g, b, a = chat_themes.scrim_rgba(self._bg_theme or {})
        p.fillPath(path, QColor(r, g, b, a))
        p.end()

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
        self._base_accent = self.accent_color  # 记住角色底色，无背景时 _style 据此回退
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
        if role == "user":
            bubble.meta.hide()  # 头像已是"你"，不再重复显示
        else:
            bubble.meta.setText(self.character_name or "桌宠")
        self._apply_avatar_style(bubble.avatar, self.accent_color)
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

    def refresh_settings(self) -> None:
        self.settings = self.config.chat_settings()
        self.provider_label.setText(self.settings.active_config.name)
        self._apply_character_theme()
        self._style()
        self._refresh_sessions()

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

    def _started(self, request_id: str) -> None:
        if self._active_request_id and request_id != self._active_request_id:
            return
        self.status.setText("思考中…")
        self.status_dot.setProperty("state", "busy")
        self.composer.set_busy(True)
        if self._bubble:
            self._bubble.set_state("streaming")
        self.pet_link.thinking()

    def _delta(self, request_id: str, text: str) -> None:
        if self._active_request_id and request_id != self._active_request_id:
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
        if self._active_request_id and request_id != self._active_request_id:
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
        if follow_output:
            self._bottom()

    def _error(self, request_id: str, text: str) -> None:
        if self._active_request_id and request_id != self._active_request_id:
            return
        if self._bubble:
            self._bubble.set_content("请求失败：" + str(text))
            self._bubble.set_state("error")
        self._reset()
        self.pet_link.error(text)
        self._bottom()

    def _stopped(self, request_id: str) -> None:
        if self._active_request_id and request_id != self._active_request_id:
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_bubble_widths()

    def closeEvent(self, event) -> None:
        """关闭=隐藏并复用窗口：停止生成、解除桌宠位置监听，避免泄漏。"""
        self.service.stop()
        self.set_pet_window(None)
        self.hide()
        event.ignore()


