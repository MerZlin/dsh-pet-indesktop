# -*- coding: utf-8 -*-
"""桌宠自言自语与聊天状态使用的轻量气泡。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class PetSpeechBubble(QFrame):
    """不依赖桌宠透明窗口的独立气泡，支持跨屏幕边界自动选位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pet-speech-bubble")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = QLabel(self)
        self.label.setObjectName("pet-speech-label")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setStyleSheet(
            "QLabel#pet-speech-label { color: #2f3a4a; font-size: 13px; "
            "background: #fffdf8; border: 1px solid #f0c86d; "
            "border-radius: 14px; padding: 8px 12px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, anchor_rect: QRect, duration_ms: int = 3200) -> None:
        text = str(text).strip()
        if not text:
            return
        self.label.setText(text)
        self.label.adjustSize()
        self.adjustSize()
        self._place(anchor_rect)
        self.show()
        self.raise_()
        self._hide_timer.start(max(500, int(duration_ms)))

    def reposition(self, anchor_rect: QRect) -> None:
        if self.isVisible():
            self._place(anchor_rect)

    def _place(self, anchor_rect: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        gap = 10
        size = self.sizeHint()
        # 气泡首选角色可见边界正上方，保证“思考内容”与角色身份直接关联；
        # 屏幕顶部空间不足时，再按右侧、左侧、下方回退，并由下面的 clamp 负责最终兜底。
        centered_x = anchor_rect.left() + (anchor_rect.width() - size.width()) // 2
        candidates = [
            QPoint(centered_x, anchor_rect.top() - size.height() - gap),
            QPoint(anchor_rect.right() + gap, anchor_rect.top() - size.height()),
            QPoint(anchor_rect.left() - size.width() - gap, anchor_rect.top() - size.height()),
            QPoint(centered_x, anchor_rect.bottom() + gap),
        ]
        chosen = candidates[-1]
        for point in candidates:
            candidate = QRect(point, size)
            if avail.contains(candidate):
                chosen = point
                break
        x = min(max(chosen.x(), avail.left()), avail.right() - size.width() + 1)
        y = min(max(chosen.y(), avail.top()), avail.bottom() - size.height() + 1)
        self.move(x, y)