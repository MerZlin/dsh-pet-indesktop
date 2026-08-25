# -*- coding: utf-8 -*-
"""聊天背景裁切编辑器：拖拽移动 + 滚轮缩放选区，像选头像一样决定背景取哪块。

选区以归一化坐标 (x, y, w, h) 存进 config['chat_bg_crops'][背景标识]，
paintEvent 渲染时自定义选区优先于主题默认 focus（同一套 cover 渲染路径，不拉伸变形）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

# 选区纵横比与聊天窗默认比例一致（430/780）；窗口之后改尺寸时按 cover 逻辑对齐选区中心，不变形
VIEW_ASPECT = 430.0 / 780.0
MIN_COVER = 0.12  # 选区最小覆盖画面宽度比例


def clamp_box(x: float, y: float, w: float, art_ratio: float) -> tuple[float, float, float, float]:
    """把选区夹回画面内并保纵横比。art_ratio = 图宽/图高。返回 (x, y, w, h)。"""
    # 选区像素纵横比 = VIEW_ASPECT：(w*aw)/(h*ah) = VIEW_ASPECT → h = w*art_ratio/VIEW_ASPECT
    w = max(MIN_COVER, min(w, 1.0))
    h = w * art_ratio / VIEW_ASPECT
    if h > 1.0:
        h = 1.0
        w = h * VIEW_ASPECT / art_ratio
    x = min(max(x, 0.0), 1.0 - w)
    y = min(max(y, 0.0), 1.0 - h)
    return x, y, w, h


class CropCanvas(QWidget):
    """画布： contain 展示原图，选区外压暗，拖拽移动选区，滚轮缩放。"""

    box_changed = Signal(tuple)

    def __init__(self, pixmap: QPixmap, box: tuple[float, float, float, float], parent=None):
        super().__init__(parent)
        self._pix = pixmap
        self._box = box
        self._drag_start: QPointF | None = None
        self._box_start: tuple[float, float, float, float] | None = None
        self.setMinimumSize(360, 480)

    def box(self) -> tuple[float, float, float, float]:
        return self._box

    # ---- 坐标换算 ----
    def _disp_rect(self) -> QRectF:
        """原图在控件内 contain 显示的矩形。"""
        vw, vh = self.width(), self.height()
        aw, ah = self._pix.width(), self._pix.height()
        scale = min(vw / aw, vh / ah)
        w, h = aw * scale, ah * scale
        return QRectF((vw - w) / 2, (vh - h) / 2, w, h)

    def _box_rect(self) -> QRectF:
        d = self._disp_rect()
        x, y, w, h = self._box
        return QRectF(d.x() + x * d.width(), d.y() + y * d.height(),
                      w * d.width(), h * d.height())

    def _emit(self) -> None:
        self.box_changed.emit(self._box)
        self.update()

    # ---- 交互 ----
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._box_rect().contains(event.position()):
            self._drag_start = event.position()
            self._box_start = self._box
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is None or self._box_start is None:
            return
        d = self._disp_rect()
        dx = (event.position().x() - self._drag_start.x()) / d.width()
        dy = (event.position().y() - self._drag_start.y()) / d.height()
        x0, y0, w, h = self._box_start
        self._box = clamp_box(x0 + dx, y0 + dy, w, self._pix.width() / self._pix.height())
        self._emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start = None
        self._box_start = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.1 if event.angleDelta().y() < 0 else 1 / 1.1
        x, y, w, h = self._box
        cx, cy = x + w / 2, y + h / 2
        nw = w * factor
        nx, ny, nw, nh = clamp_box(cx - nw / 2, 0.0, nw, self._pix.width() / self._pix.height())
        # 保持中心（clamp 可能因贴边移动）
        nx = min(max(cx - nw / 2, 0.0), 1.0 - nw)
        ny = min(max(cy - nh / 2, 0.0), 1.0 - nh)
        self._box = (nx, ny, nw, nh)
        self._emit()
        event.accept()

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(24, 26, 32))
        d = self._disp_rect()
        p.drawPixmap(d, self._pix, QRectF(self._pix.rect()))
        # 选区外压暗
        box = self._box_rect()
        overlay = QPainterPath()
        overlay.addRect(QRectF(self.rect()))
        hole = QPainterPath()
        hole.addRect(box)
        p.fillPath(overlay.subtracted(hole), QColor(0, 0, 0, 150))
        # 选区框 + 三分线
        p.setPen(QPen(QColor(255, 255, 255, 235), 2))
        p.drawRect(box)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for t in (1 / 3, 2 / 3):
            p.drawLine(QPointF(box.x() + box.width() * t, box.y()),
                       QPointF(box.x() + box.width() * t, box.y() + box.height()))
            p.drawLine(QPointF(box.x(), box.y() + box.height() * t),
                       QPointF(box.x() + box.width(), box.y() + box.height() * t))
        p.end()


class CropDialog(QDialog):
    """背景裁切对话框。initial_box 为 None 时给居中默认选区。"""

    def __init__(self, pixmap: QPixmap, initial_box, parent=None):
        super().__init__(parent)
        self.setWindowTitle('裁切聊天背景')
        if initial_box is None:
            ar = pixmap.width() / pixmap.height()
            # 默认选区：尽量大的竖向取景（满高优先）
            w = min(0.9, 1.0 * VIEW_ASPECT / ar)
            initial_box = clamp_box((1 - w) / 2, 0.0, w, ar)
        self.canvas = CropCanvas(pixmap, initial_box, self)

        hint = QLabel('拖拽移动选区，滚轮缩放；保存后重新打开聊天窗生效')
        hint.setStyleSheet('color:#8a8175; font-size:11px;')
        hint.setWordWrap(True)
        reset = QPushButton('重置为主题默认')
        cancel = QPushButton('取消')
        save = QPushButton('保存选区')
        save.setDefault(True)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        reset.clicked.connect(self._on_reset)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        lay = QVBoxLayout(self)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(hint)
        lay.addLayout(row)
        self._reset_requested = False

    def _on_reset(self) -> None:
        self._reset_requested = True
        self.accept()

    def result_box(self):
        """(reset?, box)。reset=True 表示用户要回退主题默认取景。"""
        return self._reset_requested, self.canvas.box()
