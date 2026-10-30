"""桌宠旁的「快速对话」气泡：点桌宠气泡后弹出的紧凑输入/回答窗。

窗口、输入、滚动、定位都在 Core；模型调用由 mod 经 ``QuickChatPort`` 驱动。
与完整聊天窗共用同一份会话（提交前 core 落 user，完成后落 assistant）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import tool_icon
from .markdown_view import markdown_to_html

FOLLOW_COALESCE_MS = 40
_MARGIN = 8


def _clamp_to_screen(rect: QRect, anchor: QRect) -> QRect:
    """把气泡钳进 ``anchor`` 所在屏幕的可用区（多屏时按鼠标侧优先）。"""
    screen = QGuiApplication.screenAt(anchor.center())
    available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)
    width = min(rect.width(), max(240, available.width() - 2 * _MARGIN))
    height = min(rect.height(), max(160, available.height() - 2 * _MARGIN))
    x = max(available.left() + _MARGIN, min(rect.x(), available.right() - width - _MARGIN))
    y = max(available.top() + _MARGIN, min(rect.y(), available.bottom() - height - _MARGIN))
    return QRect(x, y, width, height)


class QuickChatBubble(QWidget):
    """无边框小窗：标题 / 状态 / 回答 / 输入 / 去聊天窗。"""

    submitted = Signal(str)
    open_full_requested = Signal()
    closed = Signal()
    record_requested = Signal()
    record_stopped = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("quick-chat-window")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(380, 260)
        self._pet_window: QWidget | None = None
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(FOLLOW_COALESCE_MS)
        self._follow_timer.timeout.connect(self.position_near_pet)
        self._streaming = False
        self._text = ""
        self._build()

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.surface = QFrame(self)
        self.surface.setObjectName("quick-chat-surface")
        outer.addWidget(self.surface)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("快速对话", self.surface)
        title.setObjectName("quick-chat-title")
        header.addWidget(title)
        header.addStretch(1)
        self.hint = QLabel("", self.surface)
        self.hint.setObjectName("quick-chat-hint")
        header.addWidget(self.hint)
        self.close_button = QPushButton("×", self.surface)
        self.close_button.setObjectName("quick-chat-close")
        self.close_button.setFixedSize(24, 24)
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.output = QLabel("", self.surface)
        self.output.setObjectName("quick-chat-output")
        self.output.setWordWrap(True)
        self.output.setTextFormat(Qt.TextFormat.RichText)
        self.output.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.output.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll = QScrollArea(self.surface)
        self.scroll.setObjectName("quick-chat-scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(70)
        self.scroll.setMaximumHeight(200)
        self.scroll.setWidget(self.output)
        layout.addWidget(self.scroll)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.input = QLineEdit(self.surface)
        self.input.setObjectName("quick-chat-input")
        self.input.setPlaceholderText("和桌宠说点什么… Enter 发送")
        self.input.returnPressed.connect(self._submit)
        footer.addWidget(self.input, 1)
        self.record_button = QToolButton(self.surface)
        self.record_button.setObjectName("quick-chat-record")
        self.record_button.setIcon(tool_icon(self.record_button, "mic", 15))
        self.record_button.setToolTip("语音输入")
        self.record_button.setAccessibleName("语音输入")
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self._toggle_record)
        footer.addWidget(self.record_button)
        self.send_button = QPushButton("发送", self.surface)
        self.send_button.setObjectName("quick-chat-send")
        self.send_button.clicked.connect(self._submit)
        footer.addWidget(self.send_button)
        self.open_button = QPushButton("去聊天窗", self.surface)
        self.open_button.setObjectName("quick-chat-open")
        self.open_button.clicked.connect(self.open_full_requested)
        footer.addWidget(self.open_button)
        layout.addLayout(footer)

        self.surface.setStyleSheet(
            "QFrame#quick-chat-surface {"
            "  background: rgba(252, 252, 254, 248);"
            "  border: 1px solid rgba(0, 0, 0, 55);"
            "  border-radius: 12px;"
            "}"
            "QLabel#quick-chat-title { font-size: 13px; font-weight: 600; color: #1d2129; }"
            "QLabel#quick-chat-hint { font-size: 11px; color: #8a8f99; }"
            "QLabel#quick-chat-output { font-size: 13px; color: #1f2328; background: transparent; }"
            "QScrollArea#quick-chat-scroll { background: transparent; border: none; }"
            "QPushButton {"
            "  border: none; border-radius: 6px; padding: 4px 10px;"
            "  background: #eef0f3; color: #1d2129; font-size: 12px;"
            "}"
            "QPushButton:hover { background: #e2e5ea; }"
            "QPushButton#quick-chat-close { background: transparent; font-size: 16px; }"
            "QToolButton#quick-chat-record { border: none; border-radius: 6px; padding: 4px; background: transparent; }"
            "QToolButton#quick-chat-record:hover { background: #e2e5ea; }"
            "QToolButton#quick-chat-record[recording=\"true\"] { background: #d34f48; }"
            "QPushButton#quick-chat-send { background: #1677e8; color: #ffffff; font-weight: 600; }"
            "QPushButton#quick-chat-send:hover { background: #3d8ef0; }"
            "QPushButton#quick-chat-send:disabled { background: rgba(22, 119, 232, 70); }"
            "QLineEdit#quick-chat-input {"
            "  border: 1px solid #dfe2e7; border-radius: 7px; padding: 5px 8px;"
            "  background: #ffffff; color: #1d2129; font-size: 13px;"
            "}"
        )

    # ---------------------------------------------------------------- 状态
    def is_streaming(self) -> bool:
        return bool(self._streaming)

    def text(self) -> str:
        return self._text

    def begin(self) -> None:
        """清空上一轮回答，进入等待态。"""
        self._streaming = True
        self._text = ""
        self.output.clear()
        self.send_button.setEnabled(False)
        self.input.setEnabled(False)
        self.set_hint("正在想…")

    def append_token(self, delta: str) -> None:
        delta = str(delta or "")
        if not delta:
            return
        if not self._streaming:
            self.begin()
        self._text += delta
        self._render()
        self._scroll_bottom()

    def set_hint(self, text: str) -> None:
        self.hint.setText(str(text or ""))

    def finish(self, text: str = "") -> None:
        final = str(text) if str(text or "").strip() else self._text
        self._text = final
        self._streaming = False
        self.set_hint("")
        self.send_button.setEnabled(True)
        self.input.setEnabled(True)
        self._render()
        self._scroll_bottom()
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_error(self, message: str) -> None:
        self._streaming = False
        self._text = f"请求失败：{message}" if message else "请求失败"
        self.set_hint("")
        self.send_button.setEnabled(True)
        self.input.setEnabled(True)
        self.output.setText(f"<span style='color:#c0392b'>{self._escape(self._text)}</span>")

    def _render(self) -> None:
        self.output.setText(markdown_to_html(self._text) if self._text else "")
        self.output.adjustSize()

    @staticmethod
    def _escape(text: str) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _scroll_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------------------------------------------------------------- 定位/跟随
    def set_pet_window(self, pet_window: QWidget | None) -> None:
        if pet_window is self._pet_window:
            return
        if self._pet_window is not None:
            try:
                self._pet_window.pet_moved.disconnect(self._queue_follow)
            except (RuntimeError, TypeError):
                pass
        self._pet_window = pet_window
        if pet_window is not None:
            try:
                pet_window.pet_moved.connect(self._queue_follow)
            except (RuntimeError, TypeError, AttributeError):
                pass

    def _queue_follow(self) -> None:
        if self.isVisible() and not self._follow_timer.isActive():
            self._follow_timer.start()

    def position_near_pet(self) -> None:
        anchor = self._anchor_rect()
        if anchor is None:
            return
        size = self.sizeHint()
        width = max(340, min(420, size.width()))
        height = max(self.minimumSizeHint().height(), min(300, size.height()))
        x = anchor.x() + anchor.width() // 2 - width // 2
        y = anchor.y() - height - _MARGIN
        if y < 0:
            y = anchor.y() + anchor.height() + _MARGIN
        candidate = _clamp_to_screen(QRect(QPoint(x, y), QSize(width, height)), anchor)
        self.setGeometry(candidate)

    def _anchor_rect(self) -> QRect | None:
        window = self._pet_window
        if window is None:
            return None
        try:
            getter = getattr(window, "bubble_anchor_rect", None)
            if callable(getter):
                rect = getter()
                if isinstance(rect, QRect) and not rect.isEmpty():
                    return rect
            x, y, width, height = window.body_rect()
            return QRect(int(x), int(y), int(width), int(height))
        except (RuntimeError, TypeError, ValueError, AttributeError):
            return None

    def show_for_pet(self, pet_window: QWidget | None = None) -> None:
        self.set_pet_window(pet_window)
        self.position_near_pet()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    # ---------------------------------------------------------------- 事件
    def _toggle_record(self, checked: bool) -> None:
        if checked:
            self.record_requested.emit()
        else:
            self.record_stopped.emit()

    def set_recording(self, recording: bool, hint: str = "") -> None:
        self.record_button.blockSignals(True)
        self.record_button.setChecked(bool(recording))
        self.record_button.blockSignals(False)
        self.record_button.setProperty("recording", bool(recording))
        self.record_button.setIcon(
            tool_icon(
                self.record_button,
                "stop" if recording else "mic",
                15,
                dark=bool(recording),
            )
        )
        label = "停止录音" if recording else "语音输入"
        self.record_button.setToolTip(label)
        self.record_button.setAccessibleName(label)
        style = self.record_button.style()
        style.unpolish(self.record_button)
        style.polish(self.record_button)
        if hint:
            self.hint.setText(hint)

    def insert_transcript(self, text: str, *, auto_send: bool = False) -> None:
        value = str(text or "").strip()
        if not value:
            return
        self.input.setText(value)
        self.input.setFocus()
        if auto_send:
            self._submit()

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text or self._streaming:
            return
        self.input.clear()
        self.submitted.emit(text)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._follow_timer.stop()
        self.closed.emit()
        super().closeEvent(event)
