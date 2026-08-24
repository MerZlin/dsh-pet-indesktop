# -*- coding: utf-8 -*-
"""桌宠自言自语与聊天状态使用的轻量气泡。

macOS 焦点问题：气泡是定时器驱动显示的，而 `WA_ShowWithoutActivating`
在 macOS 上不生效（Qt 文档仅保证 X11/Windows），`show()` 会激活应用、
打断用户在其他应用中的输入。修复分两层（见 app.py 的
`_mac_set_accessory_activation`）：

1. 应用级：macOS 启动时把应用设为 accessory 激活策略——任何窗口
   （含气泡）出现都不会激活应用、不抢焦点；点击窗口仍可正常激活
   （聊天窗输入不受影响）。
2. 窗口级：气泡加 `WindowDoesNotAcceptFocus`，永不成为键盘焦点窗口。

注意：不要用“绕过 Qt show() 直接对原生窗口 orderFront”的做法——Qt
认为窗口未显示就不会触发绘制，气泡会“出现但看不见”。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

_MAC = sys.platform == "darwin"


class PetSpeechBubble(QFrame):
    """不依赖桌宠透明窗口的独立气泡，支持跨屏幕边界自动选位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pet-speech-bubble")
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if _MAC:
            # macOS：气泡永不接受键盘焦点，避免抢走正在输入应用的输入
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if _MAC:
            # 与主窗口一致：Tool 窗口置顶在 macOS 上需要该属性（QTBUG-38580）
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
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
        # 必须走 Qt show()：跳过它会不触发绘制，气泡“出现但看不见”
        self.show()
        if not _MAC:
            # 非 macOS：stays-on-top 不可靠时的兜底（macOS 由 accessory 策略
            # 保证不激活，raise_ 在这里会带来抢焦点风险，故跳过）
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
