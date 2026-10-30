# -*- coding: utf-8 -*-
"""「快速识屏」框选覆盖层：铺满目标屏、画冻结帧、拖框、松手出操作条。

视觉契约（"真截屏体验"就靠这几笔）：

- 底图 = 冻结帧（用户看到的是静止画面，不是身后的实时桌面）；
- **还没框选时不压暗**——用户要看着清晰的屏幕决定框哪（2026-10-14 实机反馈：
  全局压暗在明亮桌面上是一层雾，PixPin/豆包在框选前都不压暗）；
- 有选区后：非选区轻压暗 ``DIM_ALPHA`` 衬托选区（压暗是衬托，**不是遮内容**；
  HDR 屏的 SDR 感知提亮会把重压暗抬成白雾，所以只到 80 为止）；
- 选区描白色虚线框 + 左上角尺寸标签，松手后下方浮出动作条——
  交互对齐主流截图工具（框完松手 → 选做什么），不再要求"拖完再按 Enter"。

交互契约：

- 左键拖拽出选区，反向拖拽照常（归一化交给 ``normalize_drag``）；
- 松手且选区可用 → 选区钉住、动作条出现（提取文字 / 翻译 / 截图问答 /
  复制图像 / 取消）；在空白处重新按下 = 丢弃旧选区重框，动作条收起；
- ``Esc`` 全程取消（``selection_cancelled("esc")``），``Enter`` = 默认动作
  （提取文字）；动作条点「取消」= ``selection_cancelled("toolbar")``；
- **没有框住任何东西时动作条不出现、Enter 无效**：空选区进下游纯属浪费，
  在这里就挡住。比 ``MIN_SELECTION_SIDE`` 更小的拖拽**等于没框**——1~2 px 的
  抖动在旧口径下会换来"确认成功"，然后下游拿着几个像素去识别，用户看到的是
  "没有识别到文字"：一次完整会话白跑，还查不出为什么。所以门槛卡在交互层。
- **记住上次选区**：同一块屏（尺寸相同）上再次框选时，上次用过的那块预先
  画出来、动作条直接待命（"盯着同一个小区域反复截"是高频用法）。跨屏不共用
  ——尺寸变了旧坐标就没有意义；取消不算记住，没动过作的东西不该被当成默认值。

动作条的可用性：OCR/复制纯本地永远可点；翻译/问答在没有聊天配置的宿主上
置灰并写明原因（能力闸门由编排层经 ``capabilities`` 注入；引擎缺席这类事
只有真跑才知道，那时由动作结果如实报错）。

本片覆盖层是**独占**的：它铺满整屏并置顶，期间不处理"鼠标穿透到非选区"的
需求（那是后续片；先保证"框选期间点不到别的"这个可预期的行为）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from .reasons import ASK_UNAVAILABLE_REASON, TRANSLATION_UNAVAILABLE_REASON
from .session import SelectionRect, normalize_drag

log = logging.getLogger("dsh-pet-standalone")

__all__ = ["ACTION_ASK", "ACTION_COPY", "ACTION_OCR", "ACTION_TRANSLATE",
           "DIM_ALPHA", "MIN_SELECTION_SIDE", "SelectionMemory", "SelectionOverlay",
           "shared_selection_memory"]

#: 覆盖层动作条的动作 id（编排层 ``actions.py`` 按同一份字符串分发）。
ACTION_OCR = "ocr"
ACTION_TRANSLATE = "translate"
ACTION_ASK = "ask"
ACTION_COPY = "copy"

#: 非选区压暗强度（0-255）。80 的依据：选区内外一眼可分，底下内容照常可读
#: （用户要看着内容决定框哪）；HDR 桌面会对 SDR 内容做感知提亮，压得再重
#: 也会被抬回来一截，压太重反而只剩"雾"。
DIM_ALPHA = 80

#: 选区描边：白色虚线 + 深色实线垫底（亮/暗内容上都立得住）。
_BORDER_LIGHT = QColor(255, 255, 255, 235)
_BORDER_DARK = QColor(0, 0, 0, 130)

#: 尺寸标签与动作条的视觉令牌（深色 pill、白字——压在冻结帧的任意内容上都可读）。
_CHIP_BG = QColor(24, 26, 30, 215)
_CHIP_FG = QColor(255, 255, 255, 240)
_CHIP_RADIUS = 6.0
_BAR_GAP = 8           # 动作条与选区的间距（逻辑像素）
_LABEL_GAP = 6         # 尺寸标签与选区的间距

#: 选区的最小边长（逻辑像素）。8 的依据：截 8×8 一块进去，OCR 必然什么都读不出来
#: （连一个字符都放不下），所以它只可能是误触；而真正想框小图标/小按钮的用户，
#: 拖到 8 px 以上不需要刻意努力（4K 屏上 8 逻辑像素也远小于任何可读文本块）。
MIN_SELECTION_SIDE = 8


class SelectionMemory:
    """上次用过动作的选区（按屏幕尺寸分槽），跨覆盖层实例存活。

    覆盖层是**每次采集现造**的对象（``controller`` 的 ``overlay_factory``），
    所以"记住上次"这件事只能存在它外面。按逻辑宽高分槽而不是只存一份：多屏
    环境下两块尺寸不同的屏各自记各的，换屏时不会把另一块屏的坐标套过来。

    只提供"取/记/清"三件事，不碰 Qt（纯数据），因此可以在没有事件循环的地方
    直接单测。
    """

    def __init__(self) -> None:
        self._by_screen: dict[tuple[int, int], SelectionRect] = {}

    def get(self, size: tuple[int, int]) -> SelectionRect | None:
        """取该尺寸屏幕的上次选区（没记过返回 None）。返回副本，调用方改不脏内部。"""
        found = self._by_screen.get((int(size[0]), int(size[1])))
        if not isinstance(found, SelectionRect):
            return None
        return SelectionRect(found.x, found.y, found.width, found.height)

    def remember(self, size: tuple[int, int], selection: SelectionRect) -> None:
        if not isinstance(selection, SelectionRect) or selection.is_empty:
            return
        self._by_screen[(int(size[0]), int(size[1]))] = SelectionRect(
            selection.x, selection.y, selection.width, selection.height)

    def clear(self) -> None:
        self._by_screen.clear()


_SHARED_MEMORY = SelectionMemory()


def shared_selection_memory() -> SelectionMemory:
    """进程内共享的选区记忆（真实运行时的那一份；测试可注入自己的实例）。"""
    return _SHARED_MEMORY


class _ActionBar(QFrame):
    """选区动作条：松手后浮在选区下方的 pill（子控件，事件不落到覆盖层）。

    只负责展示与转发点击——动作是否可用、点了之后干什么，都不是这一层的事
    （可用性见模块 docstring，分发在 ``controller``/``actions``）。
    """

    triggered = Signal(str)   # 动作 id（含 "cancel"）

    _BUTTONS = (
        (ACTION_OCR, "提取文字", "edit"),
        (ACTION_TRANSLATE, "翻译", ""),
        (ACTION_ASK, "问问肥鱼", "chat"),
        (ACTION_COPY, "复制图像", "copy"),
        ("cancel", "取消", "exit"),
    )

    def __init__(self, parent: QWidget, capabilities: dict | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("screen-capture-action-bar")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        # 能力闸门（盲审 m10）：没配 AI 服务的宿主上，翻译/问答留位置、置灰、
        # 写明原因——点了才报错的旧行为是"能点但必炸"，不如不让点。
        caps = capabilities or {}
        disabled_reasons = {
            ACTION_TRANSLATE: TRANSLATION_UNAVAILABLE_REASON,
            ACTION_ASK: ASK_UNAVAILABLE_REASON,
        }
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        for action_id, label, icon_name in self._BUTTONS:
            button = QPushButton(label, self)
            button.setObjectName(f"screen-capture-action-{action_id}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon_name:
                from ..context_menus.icons import vector_widget_icon

                button.setIcon(vector_widget_icon(button, icon_name, 13))
            if action_id in disabled_reasons and caps.get(action_id) is False:
                button.setEnabled(False)
                button.setToolTip(disabled_reasons[action_id])
            button.clicked.connect(
                lambda _checked=False, aid=action_id: self.triggered.emit(aid))
            layout.addWidget(button)
        self.setStyleSheet(
            "#screen-capture-action-bar {"
            "  background: rgba(250, 250, 252, 245);"
            "  border: 1px solid rgba(0, 0, 0, 60);"
            "  border-radius: 8px;"
            "}"
            "#screen-capture-action-bar QPushButton {"
            "  background: transparent; border: none; border-radius: 5px;"
            "  color: #1d2129; font-size: 12px; padding: 4px 10px;"
            "}"
            "#screen-capture-action-bar QPushButton:hover {"
            "  background: rgba(0, 0, 0, 18);"
            "}"
            "#screen-capture-action-bar QPushButton:disabled {"
            "  color: rgba(29, 33, 41, 90);"
            "}"
        )


class SelectionOverlay(QWidget):
    """整屏框选层。

    ``frame`` 是冻结帧（裸设备像素，见 ``pixels.grab_screen_image``），
    ``screen`` 是目标 ``QScreen``；两者都可注入，offscreen 下用假对象即可测
    （本类只用它们的 ``geometry()`` / ``devicePixelRatio()``）。``memory`` 是
    "上次选区"的存放处（默认走进程内共享的那一份），测试注入自己的实例即可隔离。
    """

    action_requested = Signal(str, object)  # (动作 id, SelectionRect)
    selection_cancelled = Signal(str)       # 取消理由（"esc" / "toolbar"）

    def __init__(self, frame: QImage | None = None, screen=None, parent=None,
                 *, memory: SelectionMemory | None = None,
                 capabilities: dict | None = None) -> None:
        super().__init__(parent)
        self._frame = frame
        self._screen = screen if screen is not None else QGuiApplication.primaryScreen()
        self._memory = memory if memory is not None else _SHARED_MEMORY
        self._anchor: QPoint | None = None
        self._selection: SelectionRect | None = None
        self._dragging = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._apply_screen_geometry()
        self._action_bar = _ActionBar(self, capabilities)
        self._action_bar.triggered.connect(self._on_action_triggered)
        self._action_bar.hide()
        # 预选上次用过的那块（同尺寸屏幕才会命中，见 SelectionMemory）。刻意放在
        # 几何就位之后：校验"还在屏内"要用到本层自己的尺寸。命中即待选状态，
        # 动作条直接摆出来（不用先拖一下才能点）。
        self._selection = self._remembered()
        if self._usable(self._selection):
            self._show_action_bar()

    # ------------------------------------------------------------ 只读视图

    @property
    def screen_device_pixel_ratio(self) -> float:
        """目标屏的 DPR（裁剪时换算逻辑坐标用）。"""
        dpr = getattr(self._screen, "devicePixelRatio", None)
        return float(dpr()) if callable(dpr) else 1.0

    def current_selection(self) -> SelectionRect | None:
        return self._selection

    def action_bar(self) -> QWidget:
        """动作条（测试与定位探针用；产品代码不碰它）。"""
        return self._action_bar

    def _remembered(self) -> SelectionRect | None:
        """该尺寸屏幕上的上次选区；不在屏内（换分辨率/显示器后）就当没记过。

        跨屏共用同一个槽位的说明：键只有尺寸，所以两块**分辨率相同**的屏共用一份
        记忆。无害——覆盖层坐标是屏内局部坐标（``event.position()`` 相对本层），
        同一块面积在哪块屏上都成立；反倒是"按屏幕对象记"会在热插拔/换主屏后全部
        失效。越界（换分辨率后旧坐标落在屏外）则一律不作数，绝不偷偷挪到屏内。
        """
        size = self._screen_size()
        found = self._memory.get(size)
        if found is None or not self._usable(found):
            return None
        width, height = size
        if (found.x < 0 or found.y < 0
                or found.x + found.width > width or found.y + found.height > height):
            return None
        return found

    @staticmethod
    def _usable(selection: SelectionRect | None) -> bool:
        """能不能进下游：非空 **且** 两个方向都不小于 ``MIN_SELECTION_SIDE``。"""
        if not isinstance(selection, SelectionRect) or selection.is_empty:
            return False
        return (selection.width >= MIN_SELECTION_SIDE
                and selection.height >= MIN_SELECTION_SIDE)

    # ---------------------------------------------------------------- 生命周期

    def _apply_screen_geometry(self) -> None:
        if self._screen is None:
            return
        self.setGeometry(QRect(self._screen.geometry()))

    def _screen_size(self) -> tuple[int, int]:
        size = self.geometry().size()
        return size.width(), size.height()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        # macOS：覆盖层必须出现在**用户当前 Space**，否则 activateWindow 会把
        # accessory 应用切到它原来的桌面（权限失败时那正是一块空白桌面）。
        # 同时禁止失活自动隐藏；键鼠焦点仍由下面的 raise/activate 取得。
        try:
            from ..platform_mac import (
                _mac_set_hides_on_deactivate,
                _mac_set_window_all_spaces,
            )

            wid = int(self.winId())
            _mac_set_hides_on_deactivate(wid)
            _mac_set_window_all_spaces(wid)
        except Exception:
            log.debug("快速识屏：覆盖层 Space 行为设置失败", exc_info=True)
        # 键鼠都要吃：无激活窗口的覆盖层收不到 Esc/Enter。
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._dragging = False
        self._anchor = None
        super().closeEvent(event)

    # ---------------------------------------------------------------- 鼠标

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        # 空白处按下 = 丢弃旧选区重框（动作条收起；条是子控件，点它不会走到这里）。
        self._dragging = True
        self._anchor = QPoint(round(event.position().x()), round(event.position().y()))
        self._selection = None
        self._action_bar.hide()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._dragging or self._anchor is None:
            return
        self._selection = self._normalize(
            self._anchor.x(), self._anchor.y(),
            round(event.position().x()), round(event.position().y()))
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        if self._anchor is not None:
            self._selection = self._normalize(
                self._anchor.x(), self._anchor.y(),
                round(event.position().x()), round(event.position().y()))
        self._anchor = None
        # 拖到太小（含原地点击）等于**没框住东西**：把选区清成 None，让画面
        # 如实显示"还没选"，动作条与 Enter 自然也就无效。保留一个 1 px 的亮框
        # 反而在骗用户"选好了"。
        if not self._usable(self._selection):
            self._selection = None
        self.update()
        if self._usable(self._selection):
            self._show_action_bar()

    def _normalize(self, x0: int, y0: int, x1: int, y1: int) -> SelectionRect:
        """一次拖拽的起止点 → 钳制在屏内的选区（拖拽中途的预览也走它）。"""
        return normalize_drag(x0, y0, x1, y1, bounds=self._screen_size())

    # ---------------------------------------------------------------- 动作条

    def _show_action_bar(self) -> None:
        """把动作条摆到选区下缘（放不下就翻上缘），横向右对齐并钳在屏内。"""
        selection = self._selection
        if not self._usable(selection):
            return
        bar = self._action_bar
        bar.adjustSize()
        size = bar.sizeHint()
        if size.width() <= 0 or size.height() <= 0:
            size = QSize(320, 36)
        width, height = self._screen_size()
        if size.width() > width:   # 窄屏（含 offscreen 假屏）：条宽钳进屏内
            size = QSize(width, size.height())
        x = selection.x + selection.width - size.width()
        y = selection.y + selection.height + _BAR_GAP
        if y + size.height() > height:  # 下方放不下 → 翻到选区上缘内侧
            y = selection.y - size.height() - _BAR_GAP
        if y < 0:                        # 选区贴顶 → 放到选区内底部
            y = max(0, selection.y + selection.height - size.height() - _BAR_GAP)
        x = max(0, min(x, width - size.width()))
        bar.setGeometry(x, y, size.width(), size.height())
        bar.show()
        bar.raise_()

    def _on_action_triggered(self, action: str) -> None:
        if action == "cancel":
            self.selection_cancelled.emit("toolbar")
            return
        self._confirm_with(action)

    def _confirm_with(self, action: str) -> None:
        """确认一条动作：记住选区（用过的才配当默认值）→ 上报编排层。"""
        selection = self._selection
        if not self._usable(selection):
            log.debug("快速识屏：空选区或过小选区忽略动作 (%s)", action)
            return
        self._memory.remember(self._screen_size(), selection)
        self.action_requested.emit(action, selection)

    # ---------------------------------------------------------------- 键盘

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.selection_cancelled.emit("esc")
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Enter = 默认动作（提取文字）。空选区在这里挡住，与动作条同一条门槛。
            self._confirm_with(ACTION_OCR)
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
        # 四个边条拼出"除选区外"的遮罩：比 QPainterPath 相减少一次抗锯齿接缝，
        # 也保证选区内像素逐位不改（截图裁的是底图，肉眼看到的也必须是原样）。
        for strip in (
            QRect(area.x(), area.y(), area.width(), hole.y() - area.y()),
            QRect(area.x(), hole.bottom() + 1, area.width(),
                  area.bottom() - hole.bottom()),
            QRect(area.x(), hole.y(), hole.x() - area.x(), hole.height()),
            QRect(hole.right() + 1, hole.y(),
                  area.right() - hole.right(), hole.height()),
        ):
            if strip.width() > 0 and strip.height() > 0:
                painter.fillRect(strip, QColor(0, 0, 0, DIM_ALPHA))

        # 描边：深色实线垫底 + 白色虚线——亮色内容与压暗区两侧都看得清。
        painter.setPen(QPen(_BORDER_DARK, 3))
        painter.drawRect(hole.adjusted(0, 0, -1, -1))
        dash = QPen(_BORDER_LIGHT, 1.4, Qt.PenStyle.DashLine)
        painter.setPen(dash)
        painter.drawRect(hole.adjusted(0, 0, -1, -1))

        self._paint_size_label(painter, hole)
        painter.end()

    def _paint_hint(self, painter: QPainter, area: QRect) -> None:
        """无选区时的顶部居中提示条（拖拽开始即消失——那时 self._selection 已有值）。"""
        text = "拖拽框选 · Esc 取消"
        metrics = painter.fontMetrics()
        pad_x, pad_y = 14, 7
        chip_w = metrics.horizontalAdvance(text) + 2 * pad_x
        chip_h = metrics.height() + 2 * pad_y
        x = area.x() + (area.width() - chip_w) // 2
        y = area.y() + 64   # 顶中要让开灵动岛胶囊（它也在顶中，高约 44px）
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(float(x), float(y), float(chip_w), float(chip_h),
                            _CHIP_RADIUS, _CHIP_RADIUS)
        painter.fillPath(path, _CHIP_BG)
        painter.setPen(_CHIP_FG)
        painter.drawText(QRect(x, y, chip_w, chip_h), Qt.AlignmentFlag.AlignCenter, text)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _paint_size_label(self, painter: QPainter, hole: QRect) -> None:
        """选区左上方的尺寸标签（W × H）；贴顶时收进选区内侧。"""
        text = f"{hole.width()} × {hole.height()}"
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text)
        pad_x, pad_y = 8, 4
        chip_w = text_w + 2 * pad_x
        chip_h = metrics.height() + 2 * pad_y
        x = hole.left()
        y = hole.top() - chip_h - _LABEL_GAP
        if y < 0:
            y = hole.top() + _LABEL_GAP
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(float(x), float(y), float(chip_w), float(chip_h),
                            _CHIP_RADIUS, _CHIP_RADIUS)
        painter.fillPath(path, _CHIP_BG)
        painter.setPen(_CHIP_FG)
        painter.drawText(QRect(x, y, chip_w, chip_h), Qt.AlignmentFlag.AlignCenter, text)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
