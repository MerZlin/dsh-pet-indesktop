"""Compact agent status content, measured and painted with the same fonts."""
from math import ceil

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .speech_bubble_text import elide_bubble_text, normalize_bubble_text


class AgentBubbleContent(QWidget):
    def configure(self, title, status, body, font, color, width, max_lines, max_height):
        self.body_font = QFont(font)
        self.header_font = QFont(font)
        self.header_font.setPixelSize(max(9, round(font.pixelSize() * .85)))
        fm = QFontMetrics(self.body_font)
        hm = QFontMetrics(self.header_font)
        self.gap = max(8, round(font.pixelSize() * .65))
        self.line_height = ceil(fm.lineSpacing() * 1.3)
        self.body_top = hm.lineSpacing() + self.gap
        count = min(max_lines, max(0, (max_height - self.body_top - 4) // self.line_height))
        self.status = hm.elidedText(status, Qt.TextElideMode.ElideRight, width // 2)
        self.status_width = hm.horizontalAdvance(self.status)
        self.title = hm.elidedText(title, Qt.TextElideMode.ElideRight,
                                  max(1, width - self.status_width - self.gap))
        value = normalize_bubble_text(body)
        display = elide_bubble_text(fm, value, max(1, width - 2), count) if count else ''
        self.lines = display.split('\n') if display else []
        self.overflow = ''.join(self.lines) != value
        self.color = QColor(color)
        self.setFixedSize(width, self.body_top + len(self.lines) * self.line_height + 4)
        self.setAccessibleName(f'{title} · {status}')
        self.setAccessibleDescription(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.header_font)
        hm = QFontMetrics(self.header_font)
        muted = QColor(self.color)
        muted.setAlpha(190)
        painter.setPen(muted)
        painter.drawText(0, hm.ascent() + 1, self.title)
        painter.setPen(self.color)
        painter.drawText(self.width() - self.status_width, hm.ascent() + 1, self.status)
        painter.setFont(self.body_font)
        fm = QFontMetrics(self.body_font)
        for index, line in enumerate(self.lines):
            baseline = self.body_top + index * self.line_height + fm.ascent()
            painter.setPen(self.color)
            if self.overflow and index == len(self.lines) - 1 and line.endswith('…'):
                prefix = line[:-1]
                end = fm.horizontalAdvance(prefix)
                gradient = QLinearGradient(max(0, end - fm.height() * 2), 0, max(1, end), 0)
                gradient.setColorAt(0, self.color)
                faded = QColor(self.color)
                faded.setAlpha(55)
                gradient.setColorAt(1, faded)
                painter.setPen(QPen(QBrush(gradient), 1))
                painter.drawText(0, baseline, prefix)
                painter.setPen(self.color)
                painter.drawText(end, baseline, '…')
            else:
                # Draw pre-wrapped lines at the same baseline as the faded tail.
                # A font-height rectangle clips taller fallback CJK glyphs.
                painter.drawText(0, baseline, line)
