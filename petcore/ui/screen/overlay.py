"""「截图提问」框选覆盖层：铺满目标屏、画冻结帧、拖框、松手出操作条。

视觉契约（同主分支）：

- 底图 = 冻结帧（用户看到的是静止画面，不是身后的实时桌面）；
- **还没框选时不压暗**——用户要看着清晰的屏幕决定框哪（全局压暗在明亮桌面上
  是一层雾，主流截图工具在框选前都不压暗）；
- 有选区后：非选区轻压暗 ``DIM_ALPHA`` 衬托选区（只到 80：HDR 屏的 SDR 感知
  提亮会把重压暗抬成白雾）；
- 选区描白色虚线框 + 深色实线垫底、左上角尺寸标签，松手后下方浮出操作条。

交互契约：

- 左键拖拽出选区（反向拖拽照常）；松手且两向都 ≥ ``MIN_SELECTION_SIDE`` 才
  算「框住了」，操作条出现；
- 空白处重新按下 = 丢弃旧选区重框；在已选区内按下不立刻清空（等双击确认），
  一旦开始拖动才重框；
- ``Enter`` 确认、``Esc`` 取消、操作条「问问肥鱼」= 确认、「取消」= 取消；
- 空选区/过小选区不确认也不出操作条——在这里挡住，避免下游拿着几个像素白跑。

覆盖层是**独占**的：铺满整屏并置顶，期间不处理「鼠标穿透到非选区」。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from ...contracts.screen import ACTION_ASK, ACTION_COPY, ACTION_OCR, ACTION_TRANSLATE
from ...platform import window_policy
from .selection import (
    MIN_SELECTION_SIDE,
    SelectionRect,
    normalize_drag,
    usable,
)

__all__ = ["DIM_ALPHA", "MIN_SELECTION_SIDE", "SelectionOverlay"]

#: 非选区压暗强度（0-255）。
DIM_ALPHA = 80

_BORDER_LIGHT = QColor(255, 255, 255, 235)
_BORDER_DARK = QColor(0, 0, 0, 130)
_CHIP_BG = QColor(24, 26, 30, 215)
_CHIP_FG = QColor(255, 255, 255, 240)
_CHIP_RADIUS = 6.0
_BAR_GAP = 8       # 操作条与选区的间距（逻辑像素）
_LABEL_GAP = 6     # 尺寸标签与选区的间距
_DRAG_SLOP = 3     # 已选区内按下后，移动超过它才算「重新框选」

_HINT = "拖拽框选要问的区域 · Enter 确认 · Esc 取消"


def _as_image(frame):
    """冻结帧只接受 QImage：QPixmap 会让 ``drawImage`` 抛 TypeError（整层灰屏）。"""
    if frame is None or isinstance(frame, QImage):
        return frame
    convert = getattr(frame, "toImage", None)
    return convert() if callable(convert) else frame


class _ActionBar(QFrame):
    """选区操作条：松手后浮在选区下方的 pill（子控件，事件不落到覆盖层）。"""

    triggered = Signal(str)  # ocr / translate / ask / copy / cancel

    #: (动作 id, 按钮文字, objectName 后缀)
    SPECS = (
        (ACTION_OCR, "提取文字", "ocr"),
        (ACTION_TRANSLATE, "翻译", "translate"),
        (ACTION_ASK, "问问肥鱼", "ask"),
        (ACTION_COPY, "复制图像", "copy"),
    )

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("screen-selection-bar")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        self.buttons: dict[str, QPushButton] = {}
        for action, label, suffix in self.SPECS:
            button = QPushButton(label, self)
            button.setObjectName(f"screen-selection-{suffix}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, a=action: self.triggered.emit(a))
            layout.addWidget(button)
            self.buttons[action] = button
        # 兼容旧调用方/测试的属性名。
        self.ocr_button = self.buttons[ACTION_OCR]
        self.translate_button = self.buttons[ACTION_TRANSLATE]
        self.ask_button = self.buttons[ACTION_ASK]
        self.copy_button = self.buttons[ACTION_COPY]
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("screen-selection-cancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(lambda: self.triggered.emit("cancel"))
        layout.addWidget(self.cancel_button)
        self.setStyleSheet(
            "#screen-selection-bar {"
            "  background: rgba(250, 250, 252, 250);"
            "  border: 1px solid rgba(0, 0, 0, 70);"
            "  border-radius: 8px;"
            "}"
            # 必须在 bar 上显式指定文字色：覆盖层是 Tool 窗口，深色系统主题下
            # 按钮会继承白色 ButtonText，落在浅色条上等于看不见。
            "#screen-selection-bar QPushButton {"
            "  border: none; border-radius: 5px; font-size: 12px; padding: 5px 10px;"
            "  color: #1d2129;"
            "}"
            "#screen-selection-bar QPushButton:hover { background: rgba(0, 0, 0, 18); }"
            "#screen-selection-bar QPushButton:disabled { color: #8a8f99; }"
            "QPushButton#screen-selection-ask {"
            "  background: #1677e8; color: #ffffff; font-weight: 600;"
            "}"
            "QPushButton#screen-selection-ask:hover { background: #3d8ef0; }"
            "QPushButton#screen-selection-ask:disabled {"
            "  background: rgba(22, 119, 232, 45); color: rgba(255, 255, 255, 210);"
            "}"
            "QPushButton#screen-selection-cancel {"
            "  background: transparent; color: #1d2129;"
            "}"
        )

    def apply_capabilities(
        self, capabilities: dict[str, bool] | None, reasons: dict[str, str] | None = None
    ) -> None:
        caps = dict(capabilities or {})
        notes = dict(reasons or {})
        for action, button in self.buttons.items():
            enabled = bool(caps.get(action, True))
            button.setEnabled(enabled)
            button.setToolTip("" if enabled else str(notes.get(action) or "当前不可用"))
        self.cancel_button.setEnabled(True)


class SelectionOverlay(QWidget):
    """整屏框选层。

    ``frame`` 是冻结帧（裸设备像素，见 ``platform.screenshot.grab_screen_image``），
    ``screen`` 是目标 ``QScreen``；两者都可注入，offscreen 下用假对象即可测
    （本类只用它们的 ``geometry()`` / ``devicePixelRatio()`` / ``name()``）。
    """

    accepted = Signal(str, object)  # (动作 id, SelectionRect)
    cancelled = Signal(str)         # 取消理由（"esc" / "toolbar"）

    def __init__(
        self,
        frame: QImage | None = None,
        screen=None,
        parent: QWidget | None = None,
        *,
        capabilities: dict[str, bool] | None = None,
        reasons: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame = _as_image(frame)
        self._screen = screen if screen is not None else QGuiApplication.primaryScreen()
        self._anchor: QPoint | None = None
        self._selection: SelectionRect | None = None
        self._dragging = False
        self._inside_press = False
        self._result_emitted = False
        self._capabilities = dict(capabilities or {})
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        window_policy.install(self, passive=False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._apply_screen_geometry()
        self._bar = _ActionBar(self)
        self._bar.triggered.connect(self._on_action)
        self._bar.apply_capabilities(self._capabilities, reasons)
        self._bar.hide()

    def action_enabled(self, action: str) -> bool:
        return bool(self._capabilities.get(action, True))

    def default_action(self) -> str:
        """Enter / 双击的默认动作：优先提取文字，其次问，最后复制。"""
        for action in (ACTION_OCR, ACTION_ASK, ACTION_COPY):
            if self.action_enabled(action):
                return action
        return ACTION_COPY

    # ------------------------------------------------------------ 只读视图

    @property
    def screen_device_pixel_ratio(self) -> float:
        """目标屏的 DPR（裁剪时换算逻辑坐标用）。"""
        getter = getattr(self._screen, "devicePixelRatio", None)
        try:
            return float(getter()) if callable(getter) else 1.0
        except Exception:
            return 1.0

    def current_selection(self) -> SelectionRect | None:
        return self._selection

    def action_bar(self) -> QWidget:
        """操作条（测试与探针用；产品代码不碰它）。"""
        return self._bar

    # ---------------------------------------------------------------- 生命周期

    def _apply_screen_geometry(self) -> None:
        if self._screen is None:
            return
        try:
            geometry = self._screen.geometry()
        except Exception:
            geometry = None
        if geometry is not None and not QRect(geometry).isEmpty():
            self.setGeometry(QRect(geometry))

    def _screen_size(self) -> tuple[int, int]:
        size = self.geometry().size()
        return size.width(), size.height()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        # Apply before activation: Cocoa otherwise may switch to the accessory
        # app's previous Space. Keep this interactive surface keyboard-capable.
        window_policy.apply(self, passive=False)
        # 键鼠都要吃：无激活窗口的覆盖层收不到 Esc/Enter。
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._dragging = False
        self._inside_press = False
        self._anchor = None
        # 被外部关掉（Alt+F4 / 应用退出）也必须给等待方一个结果，否则
        # select_region 会一直停在嵌套事件循环里。
        if not self._result_emitted:
            self._result_emitted = True
            self.cancelled.emit("closed")
        super().closeEvent(event)

    # ---------------------------------------------------------------- 鼠标

    @staticmethod
    def _point(event: QMouseEvent) -> QPoint:
        position = event.position()
        return QPoint(round(position.x()), round(position.y()))

    @staticmethod
    def _contains(selection: SelectionRect | None, point: QPoint) -> bool:
        if not usable(selection):
            return False
        return (
            selection.x <= point.x() < selection.x + selection.width
            and selection.y <= point.y() < selection.y + selection.height
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        point = self._point(event)
        self._anchor = point
        if self._contains(self._selection, point):
            # 已选区内按下 = 可能是双击确认；先不动选区，真拖动才重框。
            self._inside_press = True
            self._dragging = False
            return
        self._inside_press = False
        self._dragging = True
        self._selection = None
        self._bar.hide()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._anchor is None:
            return
        point = self._point(event)
        if self._inside_press:
            delta = point - self._anchor
            if abs(delta.x()) < _DRAG_SLOP and abs(delta.y()) < _DRAG_SLOP:
                return
            self._inside_press = False
            self._dragging = True
            self._bar.hide()
        if not self._dragging:
            return
        self._selection = self._normalize(
            self._anchor.x(), self._anchor.y(), point.x(), point.y()
        )
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._inside_press:
            # 已选区内单击（非拖动）：保持原选区与操作条。
            self._inside_press = False
            self._anchor = None
            return
        if not self._dragging:
            return
        self._dragging = False
        point = self._point(event)
        if self._anchor is not None:
            self._selection = self._normalize(
                self._anchor.x(), self._anchor.y(), point.x(), point.y()
            )
        self._anchor = None
        # 拖到太小（含原地点击）等于**没框住东西**：清成 None，让画面如实显示
        # 「还没选」——保留一个 1px 亮框只会骗用户「选好了」。
        if not usable(self._selection):
            self._selection = None
        self.update()
        if usable(self._selection):
            self._show_action_bar()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._contains(
            self._selection, self._point(event)
        ):
            self.confirm()
            return
        super().mouseDoubleClickEvent(event)

    def _normalize(self, x0: int, y0: int, x1: int, y1: int) -> SelectionRect:
        return normalize_drag(x0, y0, x1, y1, bounds=self._screen_size())

    # ---------------------------------------------------------------- 操作条

    def _show_action_bar(self) -> None:
        """把操作条摆到选区下缘（放不下就翻上缘），横向右对齐并钳在屏内。"""
        if not usable(self._selection):
            return
        bar = self._bar
        bar.adjustSize()
        size = bar.sizeHint()
        if size.width() <= 0 or size.height() <= 0:
            size = QSize(200, 34)
        width, height = self._screen_size()
        if size.width() > width:  # 窄屏（含 offscreen 假屏）：条宽钳进屏内
            size = QSize(width, size.height())
        selection = self._selection
        x = selection.x + selection.width - size.width()
        y = selection.y + selection.height + _BAR_GAP
        if y + size.height() > height:  # 下方放不下 → 翻到选区上缘外侧
            y = selection.y - size.height() - _BAR_GAP
        if y < 0:                       # 选区贴顶 → 放进选区内底部
            y = max(0, selection.y + selection.height - size.height() - _BAR_GAP)
        x = max(0, min(x, width - size.width()))
        bar.setGeometry(x, y, size.width(), size.height())
        bar.show()
        bar.raise_()

    def _on_action(self, action: str) -> None:
        if action == "cancel":
            self.cancel("toolbar")
            return
        if not self.action_enabled(action):
            return
        self.confirm(action)

    # ---------------------------------------------------------------- 确认/取消

    def select_rect(self, selection: SelectionRect) -> None:
        """程序化摆出一个选区（测试与注入方用；真实交互靠鼠标拖拽）。"""
        self._selection = selection if usable(selection) else None
        self.update()
        if usable(self._selection):
            self._show_action_bar()
        else:
            self._bar.hide()

    def confirm(self, action: str | None = None) -> bool:
        """确认当前选区（缺省动作按 ``default_action()``）；空/过小选区返回 False。"""
        if not usable(self._selection):
            return False
        chosen = str(action or self.default_action())
        if not self.action_enabled(chosen):
            return False
        self._result_emitted = True
        self.accepted.emit(chosen, self._selection)
        return True

    def cancel(self, reason: str = "cancel") -> None:
        self._dragging = False
        self._inside_press = False
        self._anchor = None
        self._result_emitted = True
        self.cancelled.emit(str(reason or "cancel"))

    # ---------------------------------------------------------------- 键盘

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel("esc")
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
            return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        area = self.rect()
        if self._frame is not None and not self._frame.isNull():
            painter.drawImage(area, self._frame)

        selection = self._selection
        if selection is None or selection.is_empty:
            # 无选区 = 不压暗（用户要看着清晰屏幕选区）；只留一条顶部提示。
            self._paint_hint(painter, area)
            painter.end()
            return

        hole = QRect(selection.x, selection.y, selection.width, selection.height)
        # 四个边条拼出「除选区外」的遮罩：比 QPainterPath 相减少一次抗锯齿接缝，
        # 也保证选区内像素逐位不改（截图裁的是底图，看到的也必须是原样）。
        for strip in (
            QRect(area.x(), area.y(), area.width(), hole.y() - area.y()),
            QRect(area.x(), hole.bottom() + 1, area.width(), area.bottom() - hole.bottom()),
            QRect(area.x(), hole.y(), hole.x() - area.x(), hole.height()),
            QRect(hole.right() + 1, hole.y(), area.right() - hole.right(), hole.height()),
        ):
            if strip.width() > 0 and strip.height() > 0:
                painter.fillRect(strip, QColor(0, 0, 0, DIM_ALPHA))

        painter.setPen(QPen(_BORDER_DARK, 3))
        painter.drawRect(hole.adjusted(0, 0, -1, -1))
        painter.setPen(QPen(_BORDER_LIGHT, 1.4, Qt.PenStyle.DashLine))
        painter.drawRect(hole.adjusted(0, 0, -1, -1))

        self._paint_size_label(painter, hole)
        painter.end()

    def _paint_hint(self, painter: QPainter, area: QRect) -> None:
        metrics = painter.fontMetrics()
        pad_x, pad_y = 14, 7
        chip_w = metrics.horizontalAdvance(_HINT) + 2 * pad_x
        chip_h = metrics.height() + 2 * pad_y
        x = area.x() + (area.width() - chip_w) // 2
        y = area.y() + 64
        self._paint_chip(painter, QRect(x, y, chip_w, chip_h), _HINT)

    def _paint_size_label(self, painter: QPainter, hole: QRect) -> None:
        text = f"{hole.width()} × {hole.height()}"
        metrics = painter.fontMetrics()
        pad_x, pad_y = 8, 4
        chip_w = metrics.horizontalAdvance(text) + 2 * pad_x
        chip_h = metrics.height() + 2 * pad_y
        x = hole.left()
        y = hole.top() - chip_h - _LABEL_GAP
        if y < 0:
            y = hole.top() + _LABEL_GAP
        self._paint_chip(painter, QRect(x, y, chip_w, chip_h), text)

    @staticmethod
    def _paint_chip(painter: QPainter, rect: QRect, text: str) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            float(rect.x()),
            float(rect.y()),
            float(rect.width()),
            float(rect.height()),
            _CHIP_RADIUS,
            _CHIP_RADIUS,
        )
        painter.fillPath(path, _CHIP_BG)
        painter.setPen(_CHIP_FG)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
