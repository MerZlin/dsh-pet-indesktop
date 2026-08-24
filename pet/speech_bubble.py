# -*- coding: utf-8 -*-
"""桌宠自言自语与聊天状态使用的轻量气泡。

macOS 焦点问题：气泡是定时器驱动显示的，而 `WA_ShowWithoutActivating`
在 macOS 上不生效（Qt 文档仅保证 X11/Windows），`show()` 会激活应用，
打断用户在其他应用中的输入。macOS 上改用原生 `orderFront:` 显示，
不激活应用、不抢焦点；Windows/Linux 保持原路径。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

_MAC = sys.platform == "darwin"


def _mac_native_available() -> bool:
    """仅真实 macOS Cocoa 平台才走原生 orderFront 路径。

    offscreen（CI 测试）等非 cocoa 平台下 winId() 不是真实 NSView，
    直接对其调 objc 会段错误（SIGSEGV 无法被 try/except 捕获）。
    """
    if not _MAC:
        return False
    try:
        return QGuiApplication.platformName() == "cocoa"
    except Exception:
        return False


def _mac_objc_msg(selector: bytes):
    """返回能对任意 NSObject 发 objc 消息的函数（selector 预注册）。"""
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_msgSend.restype = ctypes.c_void_p
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sel = objc.sel_registerName(selector)
    return lambda obj: objc.objc_msgSend(ctypes.c_void_p(obj), sel)


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
        # macOS orderFront 路径不走 Qt show()，用自有标志跟踪可见性
        self._mac_visible = False

    def show_text(self, text: str, anchor_rect: QRect, duration_ms: int = 3200) -> None:
        text = str(text).strip()
        if not text:
            return
        self.label.setText(text)
        self.label.adjustSize()
        self.adjustSize()
        self._place(anchor_rect)
        if _MAC:
            self._mac_show()
        else:
            self.show()
            self.raise_()  # 仅非 macOS：stays-on-top 不可靠时的兜底
        self._hide_timer.start(max(500, int(duration_ms)))

    def hide(self) -> None:  # noqa: N802 (Qt 命名)
        super().hide()
        self._mac_visible = False
        if _MAC:
            self._mac_order_out()

    def reposition(self, anchor_rect: QRect) -> None:
        visible = self._mac_visible if _MAC else self.isVisible()
        if visible:
            self._place(anchor_rect)

    def _mac_show(self) -> None:
        """macOS：orderFront: 显示气泡，不激活应用、不抢焦点。

        仅 cocoa 平台生效（offscreen 下直接回退 Qt show()，避免对假
        原生句柄调 objc 导致段错误）；失败时同样回退到 Qt show()。
        """
        if _mac_native_available():
            try:
                order_front = _mac_objc_msg(b"orderFront:")
                window = _mac_objc_msg(b"window")(int(self.winId()))
                if window:
                    order_front(window)
                    self._mac_visible = True
                    return
            except Exception:
                pass
        self.show()
        self._mac_visible = self.isVisible()

    def _mac_order_out(self) -> None:
        if not _mac_native_available():
            return
        try:
            order_out = _mac_objc_msg(b"orderOut:")
            window = _mac_objc_msg(b"window")(int(self.winId()))
            if window:
                order_out(window)
        except Exception:
            pass

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
