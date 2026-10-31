from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..media.images import (
    is_animated,
    load_pixmap,
    logical_size,
    logical_size_for,
    sanitize_dpr,
    scale_pixmap,
    screen_dpr,
)
from ..platform.win import apply_stream_window, apply_tool_window
from .animated_image import AnimatedImage
from .menu import active_menu, menu_open

#: 气泡内图片的等比缩放上限（宽与高都不超过它）。
MAX_IMAGE_SIDE = 320
_IMAGE_PAD = 8
BUBBLE_GAP = 6
SCREEN_MARGIN = 4

#: 文本列宽：短文本 248，超过 60 字逐步放宽到 360（主分支口径），再乘字号缩放。
BUBBLE_TEXT_COLUMN = 248
BUBBLE_TEXT_COLUMN_MAX = 360
BUBBLE_TEXT_GROWTH_CHARS = 60
BUBBLE_TEXT_GROWTH_SPAN = 100
#: label 的显式尺寸口径（主分支 `bubble_label_size`）：最短 96px，行宽余量 4px。
BUBBLE_TEXT_MIN_WIDTH = 96
BUBBLE_TEXT_SLACK = 4
#: 每页最多行数：短文本 3 行，长文本 6 行。
BUBBLE_MAX_LINES_SHORT = 3
BUBBLE_MAX_LINES_LONG = 6

#: 文本缩放系数（``bubble_text_scale`` 百分比 / 100）的支持区间。
TEXT_SCALE_MIN = 0.5
TEXT_SCALE_MAX = 3.0

_TEXT_FONT_PX = 13
_TEXT_SUBTITLE_FONT_PX = 11
_TEXT_PAGER_FONT_PX = 10
_TEXT_PADDING = (10, 14)
_TEXT_RADIUS = 12
_CAPTION_FONT_PX = 12
_CAPTION_PADDING = (5, 10)
_CAPTION_RADIUS = 10


def _px(value: float) -> int:
    return max(1, int(round(value)))


# ---------------------------------------------------------------- z 序原语
#: 只在真实 Windows 桌面上才动 Win32 z 序（offscreen 测试与其它平台一律跳过）。
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_NOOWNERZORDER = 0x0200
_GW_HWNDPREV = 3

_user32_lib = None
_user32_ready = False


def _user32():
    """真实 Windows 平台上的 ``user32``；offscreen 或其它平台返回 ``None``。

    offscreen 平台的 ``winId()`` 不是真 HWND，往上调 Win32 只会白跑一趟。
    """
    global _user32_lib, _user32_ready
    if _user32_ready:
        return _user32_lib
    if sys.platform != "win32":
        _user32_ready = True
        return None
    app = QGuiApplication.instance()
    if app is None or app.platformName() != "windows":
        return None  # 还没有真实 QApplication：先不下结论
    try:
        lib = ctypes.WinDLL("user32", use_last_error=True)
        lib.GetWindow.restype = ctypes.c_void_p
        lib.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        lib.SetWindowPos.restype = ctypes.c_int
        lib.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
    except (AttributeError, OSError):
        _user32_lib = None
    else:
        _user32_lib = lib
    _user32_ready = True
    return _user32_lib


def _hwnd(widget: QWidget | None) -> int | None:
    """顶层窗口句柄（控件没有窗口 / 底层对象已销毁时返回 ``None``）。"""
    if widget is None:
        return None
    try:
        return int(widget.winId())
    except RuntimeError:
        return None


def _window_above(hwnd: int | None) -> int | None:
    """``hwnd`` 正上方紧邻的那个窗口；它已经是该层最上面时返回 ``None``。"""
    user32 = _user32()
    if user32 is None or hwnd is None:
        return None
    return int(user32.GetWindow(ctypes.c_void_p(hwnd), _GW_HWNDPREV) or 0) or None


def _place_after(hwnd: int | None, above: int | None) -> bool:
    """把 ``hwnd`` 插到 ``above`` 正下方（``above`` 为空 = 该层最上面）。

    ``QWidget.stackUnder()`` 对顶层窗口是空操作（本机实测 z 序一点没动），只有
    Win32 的 ``SetWindowPos`` 真的改得动；``SWP_NOACTIVATE`` 保证不抢前台，
    ``SWP_NOOWNERZORDER`` 保证不会顺手把 owner 链重新抬一遍。
    """
    user32 = _user32()
    if user32 is None or hwnd is None:
        return False
    ok = user32.SetWindowPos(
        ctypes.c_void_p(hwnd),
        ctypes.c_void_p(above) if above else None,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER,
    )
    return bool(ok)


def _is_above(hwnd: int | None, other: int | None, limit: int = 256) -> bool:
    """``hwnd`` 是否压在 ``other`` 之上（从 ``other`` 往 z 序前方找 ``hwnd``）。"""
    user32 = _user32()
    if user32 is None or not hwnd or not other:
        return False
    current = other
    for _ in range(limit):
        current = int(user32.GetWindow(ctypes.c_void_p(current), _GW_HWNDPREV) or 0)
        if not current:
            return False
        if current == hwnd:
            return True
    return False


def _panel_margins(scale: float) -> tuple[int, int, int, int]:
    horizontal = _px(_TEXT_PADDING[1] * scale)
    vertical = _px(_TEXT_PADDING[0] * scale)
    return (horizontal, vertical, horizontal, vertical)


def _panel_qss(scale: float) -> str:
    return (
        "QWidget#bubblePanel{background:rgba(255,255,255,242);"
        "border:1px solid rgba(120,150,180,180);border-radius:%dpx;}"
        % _px(_TEXT_RADIUS * scale)
    )


def _text_qss(scale: float) -> str:
    return (
        "QLabel{background:transparent;color:#243040;font-size:%dpx;}"
        % _px(_TEXT_FONT_PX * scale)
    )


def _subtitle_qss(scale: float) -> str:
    return (
        "QLabel#bubbleSubtitle{background:transparent;color:rgba(36,48,64,190);"
        "font-size:%dpx;font-weight:600;}"
        % _px(_TEXT_SUBTITLE_FONT_PX * scale)
    )


def _pager_qss(scale: float) -> str:
    return (
        "QLabel#bubblePager{background:transparent;color:rgba(60,76,94,160);"
        "font-size:%dpx;}"
        % _px(_TEXT_PAGER_FONT_PX * scale)
    )


def _caption_qss(scale: float) -> str:
    return (
        "QLabel#bubbleCaption{background:rgba(255,255,255,242);color:#243040;"
        "border:1px solid rgba(120,150,180,180);border-radius:%dpx;"
        "padding:%dpx %dpx;font-size:%dpx;font-weight:600;}"
        % (
            _px(_CAPTION_RADIUS * scale),
            _px(_CAPTION_PADDING[0] * scale),
            _px(_CAPTION_PADDING[1] * scale),
            _px(_CAPTION_FONT_PX * scale),
        )
    )


_TEXT_QSS = _text_qss(1.0)

_IMAGE_QSS = (
    "QLabel#bubbleImage{background:rgba(255,255,255,242);"
    "border:1px solid rgba(120,150,180,180);border-radius:12px;}"
)

_CAPTION_QSS = _caption_qss(1.0)


def clamp_text_scale(scale: float) -> float:
    """把文本缩放系数钳进支持区间；非法值回退 1.0（与旧版逐像素一致）。"""
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return 1.0
    if value != value:  # NaN
        return 1.0
    return max(TEXT_SCALE_MIN, min(TEXT_SCALE_MAX, value))


def clamp_image_scale(scale: float) -> float:
    """图片缩放系数与文本同口径（0.5..3.0），非法值回退 1.0。"""
    return clamp_text_scale(scale)


def column_for_text(text: str, scale: float = 1.0) -> int:
    """文本列宽：短文本 248，超过 60 字逐步放宽到 360，再乘字号缩放。"""
    scale = clamp_text_scale(scale)
    length = len(str(text or ""))
    if length <= BUBBLE_TEXT_GROWTH_CHARS:
        grow = 0
    else:
        span = min(length - BUBBLE_TEXT_GROWTH_CHARS, BUBBLE_TEXT_GROWTH_SPAN)
        grow = (BUBBLE_TEXT_COLUMN_MAX - BUBBLE_TEXT_COLUMN) * span // BUBBLE_TEXT_GROWTH_SPAN
    return max(60, int(round((BUBBLE_TEXT_COLUMN + grow) * scale)))


def _wrap_lines(text: str, column_px: int, font) -> list[str]:
    """按字体实际宽度逐字折行；显式换行保留。"""
    metrics = QFontMetrics(font)
    column = max(20, int(column_px) - BUBBLE_TEXT_SLACK)
    lines: list[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        widths = {character: metrics.horizontalAdvance(character) for character in set(paragraph)}
        current = ""
        width = 0
        for character in paragraph:
            advance = widths[character]
            if current and width + advance > column:
                lines.append(current)
                current = character
                width = advance
            else:
                current += character
                width += advance
        lines.append(current)
    return lines


def paginate_text(text: str, *, column_px: int, font, max_lines: int | None = None) -> list[str]:
    """把长文本切成多页；短文本不超过 3 行，长文本每页 6 行。"""
    lines = _wrap_lines(text, column_px, font)
    limit = int(max_lines or 0)
    if limit <= 0:
        limit = (
            BUBBLE_MAX_LINES_SHORT
            if len(lines) <= BUBBLE_MAX_LINES_SHORT
            else BUBBLE_MAX_LINES_LONG
        )
    limit = max(1, limit)
    if len(lines) > BUBBLE_MAX_LINES_SHORT:
        limit = max(limit, BUBBLE_MAX_LINES_LONG)
    pages = [
        "\n".join(lines[index : index + limit])
        for index in range(0, len(lines), limit)
    ]
    pages = [page for page in pages if page.strip()] or [str(text or "")]
    # 末页只剩一行会被误读成“被截断”；从前一页借一行过来（主分支同口径）。
    if len(pages) > 1 and len(pages[-1].split("\n")) == 1:
        previous = pages[-2].split("\n")
        if len(previous) > 1:
            pages[-2] = "\n".join(previous[:-1])
            pages[-1] = previous[-1] + "\n" + pages[-1]
    return pages



def _scaled_pixmap(
    image, max_side: int = MAX_IMAGE_SIDE, dpr: float | None = None
) -> QPixmap | None:
    """把 ``QPixmap`` 或图片路径读成不超过 ``max_side`` 逻辑像素的 ``QPixmap``。

    解码尺寸乘设备像素比，避免 1.5 倍缩放的屏幕上「先缩到 320 再放大」发虚。
    """
    max_side = max(1, int(max_side))
    ratio = screen_dpr() if dpr is None else dpr
    if isinstance(image, QPixmap):
        if image.isNull():
            return None
        return scale_pixmap(image, max_side, ratio)
    return load_pixmap(Path(image), max_side, ratio)


class SpeechBubble(QWidget):
    #: 左键点在气泡上：桌宠用来打开「快速对话」（主分支同一条入口）。
    clicked = Signal()
    #: 气泡收起（自动到时 / 手动 hide 都算，仅「原本可见」时发一次）。
    #: 点击台词要排在余额气泡收起之后，窗口层靠它接续。
    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._content_kind = "text"
        self._anchor: QRect | None = None
        self._place_key: tuple | None = None
        #: `showEvent/hideEvent` 里的可见标志（进 hideEvent 时 Qt 已清 visible）。
        self._was_shown = False
        self._text_scale = 1.0
        self._stream_mode = False
        self._stream_keycolor = False
        self._key_color = QColor("#00FF00")
        self._host_on_top = True
        self._full_text = ""
        self._subtitle = ""
        self._pages: list[str] = []
        self._page_index = 0
        self._duration_ms = 4000

        self.label = QLabel("")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setWordWrap(False)
        self.label.setStyleSheet(_TEXT_QSS)

        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("bubbleSubtitle")
        self.subtitle_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(_subtitle_qss(1.0))
        self.subtitle_label.hide()

        self.pager = QLabel("")
        self.pager.setObjectName("bubblePager")
        self.pager.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.pager.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pager.setStyleSheet(_pager_qss(1.0))
        self.pager.hide()

        self.text_panel = QWidget(self)
        self.text_panel.setObjectName("bubblePanel")
        self.text_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.text_panel.setStyleSheet(_panel_qss(1.0))
        panel_layout = QVBoxLayout(self.text_panel)
        panel_layout.setContentsMargins(*_panel_margins(1.0))
        panel_layout.setSpacing(2)
        panel_layout.addWidget(self.subtitle_label)
        panel_layout.addWidget(self.label)
        panel_layout.addWidget(self.pager)

        self.caption_label = QLabel("")
        self.caption_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.caption_label.setObjectName("bubbleCaption")
        self.caption_label.setMaximumWidth(MAX_IMAGE_SIDE + 2 * _IMAGE_PAD)
        self.caption_label.setStyleSheet(_CAPTION_QSS)
        self.caption_label.hide()

        self.image_label = QLabel("")
        self.image_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.image_label.setObjectName("bubbleImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setContentsMargins(_IMAGE_PAD, _IMAGE_PAD, _IMAGE_PAD, _IMAGE_PAD)
        self.image_label.setStyleSheet(_IMAGE_QSS)
        self.image_label.hide()

        #: 动图控件（懒建，挂在 image_label 里）。静态图走 image_label 的
        #: pixmap，动图走 QMovie；两者互斥，切换时要停播并隐藏。
        self._animated_image: AnimatedImage | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(BUBBLE_GAP)
        layout.addWidget(self.caption_label)
        layout.addWidget(self.image_label)
        layout.addWidget(self.text_panel)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._apply_window_flags()

    # ---------------------------------------------------------------- 查询
    def content_kind(self) -> str:
        return self._content_kind

    def is_showing_image(self) -> bool:
        return self._content_kind == "image" and self.isVisible()

    @property
    def animated_image(self) -> AnimatedImage | None:
        """当前挂着的动图控件（没显示过动图时为 None）。"""
        return self._animated_image

    def anchor(self) -> QRect | None:
        return QRect(self._anchor) if self._anchor is not None else None

    def text_scale(self) -> float:
        """当前文本缩放系数（1.0 = 旧版默认字号）。"""
        return self._text_scale

    def set_text_scale(self, scale: float) -> None:
        """按系数缩放正文/副标题字号与内边距；文本在显示时立即重排。"""
        value = clamp_text_scale(scale)
        if value == self._text_scale:
            return
        self._text_scale = value
        self.label.setStyleSheet(_text_qss(value))
        self.subtitle_label.setStyleSheet(_subtitle_qss(value))
        self.pager.setStyleSheet(_pager_qss(value))
        self.text_panel.setStyleSheet(_panel_qss(value))
        if self.text_panel.layout() is not None:
            self.text_panel.layout().setContentsMargins(*_panel_margins(value))
        self.caption_label.setStyleSheet(_caption_qss(value))
        if self._content_kind == "text" and self._full_text:
            self._render_text()
            if self._anchor is not None:
                self._place(self._anchor)
            if self.isVisible():
                self._schedule_page()
            return
        if self._anchor is None or not self.isVisible():
            return
        self.adjustSize()
        self._place(self._anchor)

    # ---------------------------------------------------------------- 窗口样式
    def _window_flags(self):
        if self._stream_mode:
            return (
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._host_on_top:
            # 同一图层：桌宠置顶时气泡也必须置顶，否则图片气泡会被桌宠盖住
            # （文字气泡贴在头顶，肉眼看不出来）。
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _apply_window_flags(self) -> None:
        """重建旗标（setWindowFlags 会隐窗，可见时要原地恢复并摆回自己的层）。"""
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        # 构造期窗口还没 show，提前 winId() 会把宿主窗口带出 2px 边框；
        # 等可见（或 showEvent）再补原生样式。
        if was_visible:
            self._apply_native_style()
        if was_visible:
            if self._anchor is not None:
                self._place(self._anchor)
            self._present()

    def _apply_native_style(self) -> None:
        if os.name != "nt":
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if self._stream_mode:
            apply_stream_window(hwnd, keycolor=self._stream_keycolor)
        else:
            apply_tool_window(hwnd)

    # ---------------------------------------------------------------- 层级
    def _raises_by_design(self) -> bool:
        """这一层要不要主动抬到最前：宿主置顶或直播模式（两者都是置顶窗口）。"""
        return self._host_on_top or self._stream_mode

    def _present(self) -> None:
        """显示气泡，并按层级规则摆正 z 序。

        气泡是桌宠的**从属窗口**：``show()`` 会把整条 owner 链（气泡 + 桌宠）一起
        顶到普通窗口层的最上面，所以「去掉 ``raise_()``」并不够——本机实测只
        ``show()`` 一样会把桌宠抬到别的应用窗口之上。规则按优先级来：

        * 菜单会话期间：只要气泡或桌宠压在菜单上，就一起沉到菜单下方（菜单最高）；
        * 宿主置顶 / 直播模式：与旧行为一致，``raise_()`` 抬到最前；
        * 宿主不置顶：``show()`` 之后把桌宠塞回它原来的位置，气泡贴着桌宠下方，
          不抢别的应用窗口的前台。
        """
        menu = active_menu() if menu_open() else None
        if menu is not None:
            self.show()
            self._settle_below_menu(menu)
            return
        if self._raises_by_design():
            self.show()
            self.raise_()
            return
        anchor = _window_above(_hwnd(self.parentWidget()))
        self.show()
        self._settle_below_host(anchor)

    def _settle_below_host(self, anchor: int | None) -> None:
        """把桌宠塞回 ``anchor`` 下方，气泡贴着桌宠（``anchor`` 为空 = 该层最上面）。"""
        host = _hwnd(self.parentWidget())
        own = _hwnd(self)
        if host is None or own is None:
            return
        _place_after(host, anchor)
        _place_after(own, host)

    def _settle_below_menu(self, menu: QWidget) -> None:
        """菜单会话期间把压在菜单上的桌宠/气泡沉到菜单下方；本来就在下面则不动。"""
        menu_hwnd = _hwnd(menu)
        host = _hwnd(self.parentWidget())
        own = _hwnd(self)
        if menu_hwnd is None or own is None:
            return
        if not _is_above(own, menu_hwnd) and not _is_above(host, menu_hwnd):
            return
        if host is not None and _is_above(host, menu_hwnd):
            _place_after(host, menu_hwnd)
        _place_after(own, host if host is not None else menu_hwnd)

    def set_host_on_top(self, on: bool) -> None:
        """跟随宿主「窗口置顶」开关（同主分支 ``set_host_on_top``）。"""
        on = bool(on)
        if on == self._host_on_top:
            return
        self._host_on_top = on
        self._apply_window_flags()

    def host_on_top(self) -> bool:
        return self._host_on_top

    def set_stream_mode(
        self,
        enabled: bool,
        *,
        keycolor: bool = False,
        key_color: str = "#00FF00",
    ) -> None:
        """直播捕获兼容：气泡也必须是普通顶层窗口，否则捕获软件看不到它。

        色键模式下窗口改为不透明并铺底色；圆角外的区域交给 OBS 色度键抠掉。
        """
        enabled = bool(enabled)
        keycolor = bool(keycolor and enabled)
        color = QColor(str(key_color or "").strip())
        if not color.isValid():
            color = QColor("#00FF00")
        if (
            enabled == self._stream_mode
            and keycolor == self._stream_keycolor
            and color.name().lower() == self._key_color.name().lower()
        ):
            return
        self._stream_mode = enabled
        self._stream_keycolor = keycolor
        self._key_color = color
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, not keycolor
        )
        self._apply_window_flags()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._stream_mode and self._stream_keycolor:
            painter = QPainter(self)
            painter.fillRect(self.rect(), self._key_color)
            painter.end()
            return
        super().paintEvent(event)

    # ---------------------------------------------------------------- 文本
    def show_text(
        self,
        text: str,
        anchor: QRect,
        duration_ms: int = 4000,
        *,
        subtitle: str = "",
    ) -> None:
        text = str(text or "").strip()
        if not text:
            return
        self._content_kind = "text"
        self.caption_label.hide()
        self.caption_label.clear()
        self.image_label.hide()
        self.image_label.clear()
        self._stop_animated()
        self.text_panel.show()
        self._full_text = text
        self._subtitle = str(subtitle or "").strip()
        self._duration_ms = max(800, int(duration_ms))
        self._page_index = 0
        self._anchor = QRect(anchor)
        self._render_text()
        self._place(anchor)
        self._present()
        # 窗口显示后布局才最终生效：再量一次并重排，避免“永远慢一句”。
        self.adjustSize()
        self._place(anchor)
        self._schedule_page()

    def _column(self) -> int:
        column = column_for_text(self._full_text, self._text_scale)
        anchor = self._anchor
        if anchor is not None:
            screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                padding = 2 * _px(_TEXT_PADDING[1] * self._text_scale)
                column = min(column, max(80, area.width() - 2 * SCREEN_MARGIN - padding))
        return column

    def _text_font(self):
        self.label.ensurePolished()
        font = self.label.font()
        font.setPixelSize(_px(_TEXT_FONT_PX * self._text_scale))
        return font

    def _paginate(self) -> list[str]:
        return paginate_text(
            self._full_text,
            column_px=self._column(),
            font=self._text_font(),
        )

    def _render_text(self) -> None:
        self._pages = self._paginate()
        if not self._pages:
            return
        self._page_index = max(0, min(self._page_index, len(self._pages) - 1))
        page = self._pages[self._page_index]
        self.label.setWordWrap(False)
        self.label.setText(page)

        font = self._text_font()
        metrics = QFontMetrics(font)
        lines = page.split("\n") or [""]
        widest = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        width = max(
            BUBBLE_TEXT_MIN_WIDTH,
            min(self._column(), widest + BUBBLE_TEXT_SLACK),
        )

        self.subtitle_label.setText(self._subtitle)
        self.subtitle_label.setVisible(bool(self._subtitle))
        subtitle_height = 0
        if self._subtitle:
            self.subtitle_label.ensurePolished()
            subtitle_metrics = QFontMetrics(self.subtitle_label.font())
            subtitle_width = min(
                self._column(),
                subtitle_metrics.horizontalAdvance(self._subtitle) + BUBBLE_TEXT_SLACK,
            )
            width = max(width, BUBBLE_TEXT_MIN_WIDTH, subtitle_width)
            subtitle_height = self.subtitle_label.heightForWidth(width)
            if subtitle_height <= 0:
                subtitle_height = self.subtitle_label.sizeHint().height()
            self.subtitle_label.setFixedWidth(width)
            self.subtitle_label.setFixedHeight(max(1, subtitle_height))

        height = max(1, len(lines)) * metrics.lineSpacing() + 2
        self.label.setFixedSize(width, height)

        if len(self._pages) > 1:
            dots = " ".join(
                "●" if index == self._page_index else "○"
                for index in range(len(self._pages))
            )
            self.pager.setText(dots)
            self.pager.setFixedWidth(width)
            self.pager.show()
        else:
            self.pager.clear()
            self.pager.hide()

        layout = self.text_panel.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()

    def _schedule_page(self) -> None:
        total = max(1, sum(len(page) for page in self._pages))
        share = len(self._pages[self._page_index]) / total if self._pages else 1.0
        dwell = max(900, int(self._duration_ms * share))
        self._timer.start(dwell)

    def page_count(self) -> int:
        return len(self._pages)

    def page_index(self) -> int:
        return self._page_index

    def full_text(self) -> str:
        """当前气泡的完整正文（分页前），只读。"""
        return self._full_text

    def subtitle_text(self) -> str:
        return self._subtitle

    def next_page(self) -> bool:
        """翻到下一页；已是最后一页时返回 False（由滚轮决定是否关闭）。"""
        if self._page_index + 1 >= len(self._pages):
            return False
        self._page_index += 1
        self._render_text()
        if self._anchor is not None:
            self._place(self._anchor)
        self._schedule_page()
        return True

    def previous_page(self) -> bool:
        if self._page_index <= 0:
            return False
        self._page_index -= 1
        self._render_text()
        if self._anchor is not None:
            self._place(self._anchor)
        self._schedule_page()
        return True

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._content_kind != "text" or len(self._pages) <= 1:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        if delta < 0:
            if not self.next_page():
                self.hide()
        elif not self.previous_page():
            pass
        event.accept()

    # ---------------------------------------------------------------- 图片
    def show_image(
        self,
        image,
        anchor: QRect,
        caption: str = "",
        duration_ms: int = 4000,
        *,
        image_scale: float = 1.0,
        dpr: float | None = None,
    ) -> bool:
        scale = clamp_image_scale(image_scale)
        max_side = max(1, int(round(MAX_IMAGE_SIDE * scale)))
        ratio = sanitize_dpr(screen_dpr(self) if dpr is None else dpr)
        # 只有路径入参才可能是动图；QPixmap 重载保持纯静态的旧语义。
        animated_path: Path | None = None
        pixmap = None
        if not isinstance(image, QPixmap) and is_animated(image):
            size = logical_size_for(image, max_side)
            if size is None:
                return False
            animated_path = Path(image)
            width, height = size
        else:
            pixmap = _scaled_pixmap(image, max_side, dpr)
            if pixmap is None:
                return False
            width, height = logical_size(pixmap)
        self._content_kind = "image"
        self._full_text = ""
        self._subtitle = ""
        self._pages = []
        self.label.clear()
        self.text_panel.hide()
        caption = str(caption or "").strip()
        caption_width = max_side + 2 * _IMAGE_PAD
        self.caption_label.setWordWrap(True)
        self.caption_label.setFixedWidth(caption_width)
        self.caption_label.setText(caption)
        self.caption_label.setVisible(bool(caption))
        if caption:
            self.caption_label.ensurePolished()
            caption_height = self.caption_label.heightForWidth(caption_width)
            if caption_height <= 0:
                caption_height = self.caption_label.sizeHint().height()
            self.caption_label.setFixedHeight(max(1, caption_height))
        self.image_label.setFixedSize(
            width + 2 * _IMAGE_PAD, height + 2 * _IMAGE_PAD
        )
        if animated_path is not None:
            # 白色圆角底板仍是 image_label，动图作为它的子控件贴在 8px 内边距里。
            self.image_label.clear()
            animated = self._ensure_animated(animated_path, max_side, ratio)
            animated.move(_IMAGE_PAD, _IMAGE_PAD)
            animated.show()
            animated.start()
        else:
            self._stop_animated()
            self.image_label.setPixmap(pixmap)
        self.image_label.show()
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()
        self._place(anchor)
        self._present()
        self.adjustSize()
        self._place(anchor)
        self._timer.start(max(800, int(duration_ms)))
        return True

    def _ensure_animated(
        self, path: Path, max_side: int, dpr: float
    ) -> AnimatedImage:
        """动图控件懒建并按需换图/换上限（同一 `image_label` 里复用）。"""
        widget = self._animated_image
        if widget is None:
            widget = AnimatedImage(path, max_side, self.image_label, dpr=dpr)
            self._animated_image = widget
            return widget
        widget.stop()
        widget.set_max_side(max_side)
        widget.set_path(path)
        return widget

    def _stop_animated(self) -> None:
        """停播并隐藏动图：切文字/切静态图/气泡收起都要走这里。"""
        widget = self._animated_image
        if widget is None:
            return
        widget.stop()
        widget.hide()

    # ---------------------------------------------------------------- 跟随
    def reposition(self, anchor: QRect) -> None:
        """锚点或自身尺寸变化时重排；不可见时只记住锚点。"""
        self._anchor = QRect(anchor)
        if not self.isVisible():
            return
        key = (
            anchor.x(),
            anchor.y(),
            anchor.width(),
            anchor.height(),
            self.width(),
            self.height(),
        )
        if key == self._place_key:
            return
        self._place(anchor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton and self.isVisible():
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """收起时发一次 `closed`（自动到时、手动 hide、桌宠隐藏都走这里）。

        进 `hideEvent` 时 Qt 已经把 visible 标志清掉了，所以用 `showEvent` 记的
        自己的标志判断「原本是否可见」——否则每条都会发，或者一条都不发。
        """
        was_visible = self._was_shown
        # 收起后动图必须停播，否则定时器收起的气泡底下还在解码 GIF。
        self._stop_animated()
        super().hideEvent(event)
        self._was_shown = False
        if was_visible:
            self.closed.emit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._was_shown = True
        # 构造期 _apply_window_flags 不碰 winId（会带出 2px 宿主边框），
        # 真正显示后补一次直播模式的原生样式。
        if self._stream_mode:
            self._apply_native_style()
        # 气泡的 show/raise 会让 Qt 把宿主的 WindowDoesNotAcceptFocus 重新
        # 映射成 WS_EX_NOACTIVATE；直播模式要再清一次，否则 OBS 又拿不到激活态。
        host = self.parentWidget()
        sync = getattr(host, "_apply_native_style", None)
        if callable(sync):
            sync()

    def _place(self, anchor: QRect) -> None:
        """贴着角色锚点放：上居中 → 上偏左右 → 两侧 → 下方，且不压住角色。"""
        self._anchor = QRect(anchor)
        width = self.width()
        height = self.height()
        self._place_key = (
            anchor.x(),
            anchor.y(),
            anchor.width(),
            anchor.height(),
            width,
            height,
        )
        centered = anchor.center().x() - width // 2
        left_x = anchor.left() - width + max(24, anchor.width() // 3)
        right_x = anchor.right() - max(24, anchor.width() // 3)
        above = anchor.top() - height - BUBBLE_GAP
        below = anchor.bottom() + BUBBLE_GAP
        middle = anchor.center().y() - height // 2
        candidates = [
            (centered, above),
            (left_x, above),
            (right_x, above),
            (anchor.right() + BUBBLE_GAP, middle),
            (anchor.left() - width - BUBBLE_GAP, middle),
            (centered, below),
            (left_x, below),
            (right_x, below),
        ]
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if area is None:
            self.move(int(candidates[0][0]), int(candidates[0][1]))
            return
        for x, y in candidates:
            rect = QRect(int(x), int(y), width, height)
            if area.contains(rect) and not rect.intersects(anchor):
                self.move(rect.topLeft())
                return
        x = max(area.left() + SCREEN_MARGIN, min(area.right() - width - SCREEN_MARGIN, centered))
        y = above if above >= area.top() + SCREEN_MARGIN else min(
            area.bottom() - height - SCREEN_MARGIN, below
        )
        y = max(area.top() + SCREEN_MARGIN, min(y, area.bottom() - height - SCREEN_MARGIN))
        self.move(int(x), int(y))
