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

显示形式：自绘圆角气泡 + 底部小箭头（指向角色）+ 柔和阴影；
文字超出单页时自动分页，点击气泡翻页（页脚显示 1/3 · 点击翻页）。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

_MAC = sys.platform == "darwin"

# 分页参数：单页最大宽度/高度（逻辑像素）
_PAGE_MAX_WIDTH = 250
_PAGE_MAX_HEIGHT = 200
_ARROW_HEIGHT = 10  # 底部箭头尾巴高度
_BUBBLE_BG = QColor("#fffdf8")
_BUBBLE_BORDER = QColor("#f0c86d")


class PetSpeechBubble(QFrame):
    """不依赖桌宠透明窗口的独立气泡，支持跨屏幕边界自动选位与文字分页。"""

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
        # 鼠标穿透默认开启（与旧版一致，不干扰用户操作）；多页时可点击翻页，
        # show_text 会按页数动态切换（单页穿透、多页拦截）。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if _MAC:
            # 与主窗口一致：Tool 窗口置顶在 macOS 上需要该属性（QTBUG-38580）
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        # 注意：不能用 QGraphicsDropShadowEffect——气泡是 Windows 分层窗口，
        # effect 会让更新区域外扩出负坐标，UpdateLayeredWindowIndirect 失败刷屏；
        # 阴影由 paintEvent 自绘（下方偏移的半透明圆角矩形）。

        self.label = QLabel(self)
        self.label.setObjectName("pet-speech-label")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)  # 页脚灰色小字
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setStyleSheet(
            "QLabel#pet-speech-label { color: #2f3a4a; font-size: 13px; "
            "background: transparent; border: none; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12 + _ARROW_HEIGHT)
        layout.addWidget(self.label)

        self._pages: list[str] = []
        self._page = 0
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        """自绘圆角气泡 + 自绘阴影 + 底部箭头尾巴（指向角色）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        body_h = h - _ARROW_HEIGHT
        if w <= 2 or body_h <= 2:
            painter.end()
            return
        # 自绘阴影：主体右下偏移 2px 的半透明圆角矩形
        shadow = QPainterPath()
        shadow.addRoundedRect(2.5, 3.5, w - 1, body_h - 1, 14, 14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 26))
        painter.drawPath(shadow)
        # 圆角矩形主体
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, w - 1, body_h - 1, 14, 14)
        painter.setPen(QColor(_BUBBLE_BORDER))
        painter.setBrush(QColor(_BUBBLE_BG))
        painter.drawPath(path)
        # 底部中央小箭头
        size = 9
        x = w // 2
        arrow = QPainterPath()
        arrow.moveTo(x - size, body_h - 1)
        arrow.lineTo(x + size, body_h - 1)
        arrow.lineTo(x, h - 1)
        arrow.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_BUBBLE_BG))
        painter.drawPath(arrow)
        painter.end()

    # ------------------------------------------------------------ 文本
    def show_text(self, text: str, anchor_rect: QRect, duration_ms: int = 3200) -> None:
        text = str(text).strip()
        if not text:
            return
        self._pages = self._paginate(text)
        self._page = 0
        # 单页保持鼠标穿透（不挡用户点击）；多页才接收鼠标用于翻页
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            len(self._pages) <= 1,
        )
        self._anchor_rect = anchor_rect
        self._update_page_text()
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

    def _paginate(self, text: str) -> list[str]:
        """按固定宽度换行、固定高度分页；单页时原样返回。"""
        fm = QFontMetrics(self.label.font())
        width = _PAGE_MAX_WIDTH - 32  # 去掉左右 padding
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            current = ""
            for ch in paragraph:
                if fm.horizontalAdvance(current + ch) > width and current:
                    lines.append(current)
                    current = ch
                else:
                    current += ch
            lines.append(current)
        line_h = fm.lineSpacing()
        per_page = max(1, (_PAGE_MAX_HEIGHT - 24) // line_h)
        if len(lines) <= per_page:
            return ["\n".join(lines)]
        return ["\n".join(lines[i:i + per_page]) for i in range(0, len(lines), per_page)]

    def _update_page_text(self) -> None:
        text = self._pages[self._page]
        if len(self._pages) > 1:
            # 注意：span 里不能写 font-size（Qt rich text 的 px 转点大小会失败，
            # 触发 QFont::setPointSize(-1) 警告），只做颜色区分。
            text += (
                f'<br><span style="color:#b8a882;">'
                f'{self._page + 1}/{len(self._pages)} · 点击翻页</span>'
            )
        self.label.setText(text)
        self.label.adjustSize()
        self.adjustSize()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """点击气泡翻页（循环）；翻页后重置自动隐藏计时并重新定位。"""
        if len(self._pages) > 1:
            self._page = (self._page + 1) % len(self._pages)
            self._update_page_text()
            anchor = getattr(self, '_anchor_rect', None)
            if anchor is not None:
                self._place(anchor)
            self._hide_timer.start(3200)
            event.accept()
            return
        super().mousePressEvent(event)

    # ------------------------------------------------------------ 定位
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
