# -*- coding: utf-8 -*-
"""快速对话气泡（Quick Chat）。

点击桌宠弹出的头顶小气泡输入框；回车发送，AI 回复在气泡内流式显示，
与完整 AI 对话窗口共用同一会话历史（SessionStore / ChatService）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .speech_bubble import BUBBLE_STYLE_PRESETS

from .chat.models import ChatMessage
from .chat.prompt import PromptBuilder
from .chat.service import ChatService
from .chat.session_store import SessionStore

_PAGE_SIZE = 500


class QuickChatBubble(QFrame):
    def __init__(self, config, pet_window=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.pet_window = pet_window
        self.setObjectName("quick-chat-bubble")
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(320)
        self.setMaximumWidth(460)

        style_id = str(config.get("self_talk_bubble_style", "classic_top") or "classic_top")
        self._preset = BUBBLE_STYLE_PRESETS.get(style_id, BUBBLE_STYLE_PRESETS["classic_top"])
        self._tail_up = False

        self.character_id = str(config.get("character", "shenshen"))
        self.settings = config.chat_settings()
        self.prompt_builder = PromptBuilder(Path(__file__).resolve().parent.parent / "assets" / "characters")
        self.store = SessionStore(config.dir, getattr(config, "instance_id", ""))
        self.session = self._get_session()
        self.service = ChatService(parent=self)
        self._active_request_id: str | None = None
        self._reply_text = ""
        self._page = 0
        self._pages: list[str] = []
        self._deactivate_check_pending = False

        self._build()
        self._connect()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("快速对话")
        title.setObjectName("quick-chat-title")
        header.addWidget(title)
        header.addStretch(1)
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("quick-chat-hint")
        header.addWidget(self.hint_label)
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("quick-chat-close")
        self.close_btn.setFixedSize(24, 24)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        self.output = QLabel("")
        self.output.setObjectName("quick-chat-output")
        self.output.setWordWrap(True)
        self.output.setMinimumHeight(60)
        self.output.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.output_scroll = QScrollArea()
        self.output_scroll.setObjectName("quick-chat-output-scroll")
        self.output_scroll.setWidgetResizable(True)
        self.output_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.output_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.output_scroll.setMinimumHeight(60)
        self.output_scroll.setMaximumHeight(220)
        self.output_scroll.setWidget(self.output)
        layout.addWidget(self.output_scroll)

        self.page_widget = QWidget()
        self.page_row = QHBoxLayout(self.page_widget)
        self.page_row.setContentsMargins(0, 0, 0, 0)
        self.prev_btn = QPushButton("←")
        self.page_label = QLabel("")
        self.next_btn = QPushButton("→")
        for btn in (self.prev_btn, self.next_btn):
            btn.setObjectName("quick-chat-page")
            btn.setFixedSize(28, 24)
        self.open_chat_btn = QPushButton("去聊天窗")
        self.open_chat_btn.setObjectName("quick-chat-open")
        self.page_row.addWidget(self.prev_btn)
        self.page_row.addWidget(self.page_label)
        self.page_row.addWidget(self.next_btn)
        self.page_row.addStretch(1)
        self.page_row.addWidget(self.open_chat_btn)
        layout.addWidget(self.page_widget)
        self.page_widget.setVisible(False)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入消息，回车发送…")
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("quick-chat-send")
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_btn)
        layout.addLayout(input_row)

        bg = self._preset["background"]
        fg = self._preset["foreground"]
        border = self._preset["border"]
        self.setStyleSheet(f"""
            QLabel#quick-chat-title, QLabel#quick-chat-hint, QLabel#quick-chat-output,
            QLabel#quick-chat-page {{ background: transparent; color: {fg}; border: none; }}
            QScrollArea#quick-chat-output-scroll {{ background: transparent; border: none; }}
            QPushButton {{
                background: {border}; color: {fg}; border: none; border-radius: 8px;
                padding: 3px 8px;
            }}
            QPushButton:hover {{ background: {fg}; color: {bg}; }}
            QLineEdit {{
                background: {bg}; color: {fg}; border: 1px solid {border};
                border-radius: 8px; padding: 5px 8px;
            }}
        """)

    def _connect(self) -> None:
        self.close_btn.clicked.connect(self.close)
        self.send_btn.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.prev_btn.clicked.connect(lambda: self._show_page(self._page - 1))
        self.next_btn.clicked.connect(lambda: self._show_page(self._page + 1))
        self.open_chat_btn.clicked.connect(self._open_full_chat)
        self.service.started.connect(self._started)
        self.service.delta.connect(self._delta)
        self.service.finished.connect(self._finished)
        self.service.error.connect(self._error)
        self.service.stopped.connect(self._stopped)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(6, 8, -6, -8)
        radius = float(self._preset.get("radius", 14))
        body = QPainterPath()
        body.addRoundedRect(rect, radius, radius)
        tail = QPainterPath()
        tip_x = rect.center().x()
        if self._tail_up:
            tip = QPointF(tip_x, rect.top() - 6)
            tail.moveTo(QPointF(tip_x - 8, rect.top() + 2))
            tail.lineTo(tip)
            tail.lineTo(QPointF(tip_x + 8, rect.top() + 2))
        else:
            tip = QPointF(tip_x, rect.bottom() + 6)
            tail.moveTo(QPointF(tip_x - 8, rect.bottom() - 2))
            tail.lineTo(tip)
            tail.lineTo(QPointF(tip_x + 8, rect.bottom() - 2))
        tail.closeSubpath()
        surface = body.united(tail).simplified()

        # 柔和阴影
        shadow = QPainterPath(surface)
        shadow.translate(0, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._preset.get("shadow", "#6b542b")))
        painter.drawPath(shadow)

        # 气泡主体
        painter.setBrush(QColor(self._preset["background"]))
        painter.setPen(QPen(QColor(self._preset["border"]), 1))
        painter.drawPath(surface)
        painter.end()

    # ------------------------------------------------------------ 会话
    def _get_session(self):
        sessions = self.store.list(self.character_id)
        return sessions[0] if sessions else self._new_session()

    def refresh_session(self) -> None:
        """从磁盘重取最近会话（公开 seam；角色切换后外部刷新用，替代私访 _get_session）。"""
        self.session = self._get_session()

    def _new_session(self):
        session = self.store.create(
            self.character_id,
            self.settings.active_provider,
            self.prompt_builder.effective_system_prompt(self.settings, self.character_id),
        )
        self.store.save(session)
        return session

    def position_near_pet(self) -> None:
        pet = self.pet_window
        if pet is None or not hasattr(pet, "visible_content_rect"):
            return
        anchor = pet.visible_content_rect()
        screen = QGuiApplication.screenAt(anchor.center())
        available = screen.availableGeometry() if screen else QGuiApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        w = self.width()
        h = self.height()
        x = anchor.center().x() - w // 2
        y = anchor.top() - h - 8
        self._tail_up = False
        if y < available.top():
            y = anchor.bottom() + 8
            self._tail_up = True
        x = max(available.left() + 4, min(x, available.right() - w - 4))
        self.move(x, y)
        self.update()

    def show_for_pet(self, pet_window=None) -> None:
        if pet_window is not None:
            self.pet_window = pet_window
        self.position_near_pet()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    # ------------------------------------------------------------ 发送
    def _send(self) -> None:
        if self.service.busy:
            self.service.stop()
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        # 陈旧快照防护（DS-M7 → R3 P1 硬修）：原子「读-追加-提交」
        synced, _absorbed = self.store.append_message(
            self.session, ChatMessage("user", text)
        )
        if synced is None:
            self.session.messages.append(ChatMessage("user", text))
            self.store.save(self.session)
        else:
            self.session = synced
        self.output.setText("")
        self._reply_text = ""
        self._page = 0
        self._pages = []
        self.page_widget.setVisible(False)
        self.hint_label.setText("思考中…")
        config = self.settings.active_config
        config.api_key = self.config.resolve_api_key(config)
        messages = self.prompt_builder.build_messages(
            self.settings, self.character_id, self.session.messages[:-1], text
        )
        self._active_request_id = self.service.send(messages, config)

    def _started(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        self.hint_label.setText("生成中…")

    def _delta(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        self._reply_text += str(text)
        self._render_reply()

    def _finished(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        self._reply_text = str(text or "")
        synced, _absorbed = self.store.append_message(self.session, ChatMessage("assistant", self._reply_text))
        if synced is None:
            self.session.messages.append(ChatMessage("assistant", self._reply_text))
            self.store.save(self.session)
        else:
            self.session = synced
        self._active_request_id = None
        self.hint_label.setText("")
        self._render_reply()

    def _error(self, request_id: str, text: str) -> None:
        if request_id != self._active_request_id:
            return
        self._active_request_id = None
        self.hint_label.setText("")
        self._reply_text = f"请求失败：{text}"
        self._render_reply()

    def _stopped(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        self._active_request_id = None
        self.hint_label.setText("已停止")

    # ------------------------------------------------------------ 显示
    def _render_reply(self) -> None:
        text = self._reply_text
        if len(text) > _PAGE_SIZE:
            self._pages = [text[i:i + _PAGE_SIZE] for i in range(0, len(text), _PAGE_SIZE)]
            self._show_page(0)
            self.page_widget.setVisible(True)
            self.open_chat_btn.setVisible(True)
        else:
            self._pages = []
            self.page_widget.setVisible(False)
            self.output.setText(text)

    def _show_page(self, index: int) -> None:
        if not self._pages:
            return
        self._page = max(0, min(index, len(self._pages) - 1))
        self.output.setText(self._pages[self._page])
        self.page_label.setText(f"{self._page + 1}/{len(self._pages)}")
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < len(self._pages) - 1)

    def _open_full_chat(self) -> None:
        pet = self.pet_window
        if pet is not None and callable(getattr(pet, "on_open_chat", None)):
            pet.on_open_chat()
        elif hasattr(self, "open_chat_callback") and callable(self.open_chat_callback):
            self.open_chat_callback()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            # Cocoa 在另一个应用内窗口仍活动时首次 show/activate Tool 窗口，
            # 会产生一次过渡性的 WindowDeactivate；此时同步 close 会留下只
            # 有原生灰色底板的空白窗口。下一事件轮的 activeWindow 已稳定，
            # 可以区分这次过渡和用户真正切走焦点。
            if not self._deactivate_check_pending:
                self._deactivate_check_pending = True
                QTimer.singleShot(0, self, self._close_if_still_inactive)
        return super().event(event)

    def _close_if_still_inactive(self) -> None:
        self._deactivate_check_pending = False
        if self.isVisible() and QApplication.activeWindow() is not self:
            self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.service.busy:
            self.service.stop()
        super().closeEvent(event)
