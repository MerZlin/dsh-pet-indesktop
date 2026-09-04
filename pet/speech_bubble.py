# -*- coding: utf-8 -*-
"""桌宠自言自语与聊天状态使用的轻量气泡。

macOS 焦点问题由气泡窗口自身解决：`WindowDoesNotAcceptFocus`、
`WA_ShowWithoutActivating` 和透明鼠标事件共同保证提示不会成为键盘或
鼠标目标。应用本身保持 Regular activation policy，Dock 图标因此可见。

注意：不要用“绕过 Qt show() 直接对原生窗口 orderFront”的做法——Qt
认为窗口未显示就不会触发绘制，气泡会“出现但看不见”。
"""
from __future__ import annotations

import logging
import re
import sys

log = logging.getLogger(__name__)
from math import ceil
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
    QPixmap, QTransform,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

_MAC = sys.platform == "darwin"
SELF_TALK_IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
}

# 交互按钮行里除 ``(label, callback)`` 按钮外的结构化行标记：
# - (SECTION_HEADER_LABEL, text) —— 分支/小节标题（独占一行、加粗）
# - (SECTION_HINT_LABEL, text)  —— 灰色提示行（独占一行）
# 由 agent_link 的多分支问题收集模式生成，SpeechBubble 负责渲染。
SECTION_HEADER_LABEL = "__pet_section_header__"
SECTION_HINT_LABEL = "__pet_section_hint__"


class FlowLayout(QLayout):
    """流式布局：子项超出可用宽度时自动换行（按钮行专用）。

    审批/选择题气泡的按钮行用 QHBoxLayout 时，选项一多就把气泡整个撑宽。
    FlowLayout 让按钮在气泡固定宽度内自动折行，气泡宽度封顶、不被拉长。
    """

    def __init__(self, parent=None, h_spacing: int = 6, v_spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayout.Item] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def __del__(self) -> None:
        while self.count():
            self.takeAt(0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            space_x = self._h_spacing if self._h_spacing >= 0 else item.widget().style().layoutSpacing(
                QSizePolicy.Policy.PushButton, QSizePolicy.Policy.PushButton,
                Qt.Orientation.Horizontal,
            )
            space_y = self._v_spacing if self._v_spacing >= 0 else item.widget().style().layoutSpacing(
                QSizePolicy.Policy.PushButton, QSizePolicy.Policy.PushButton,
                Qt.Orientation.Vertical,
            )
            next_x = x + hint.width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                # 放不下且本行已有内容：换行
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + hint.width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


# Each preset deliberately combines a distinct surface treatment with a preferred
# anchor.  They are not colour-only aliases of the legacy bubble.
BUBBLE_STYLE_PRESETS = {
    "classic_top": {
        "label": "经典暖黄 · 正上方", "placement": "top",
        "background": "#fffaf0", "border": "#efc261", "foreground": "#403725",
        "radius": 14, "shadow": "#6b542b",
    },
    "paper_left": {
        "label": "纸感卡片 · 左上方", "placement": "top_left",
        "background": "#ffffff", "border": "#dce1e7", "foreground": "#252a32",
        "radius": 11, "shadow": "#374151",
    },
    "glass_right": {
        "label": "深色玻璃 · 右上方", "placement": "top_right",
        "background": "#292d36", "border": "#4d5360", "foreground": "#f7f8fb",
        "radius": 18, "shadow": "#111318",
    },
    "soft_blue_top": {
        "label": "柔蓝对话 · 正上方", "placement": "top",
        "background": "#eef6ff", "border": "#a9c9ef", "foreground": "#24466f",
        "radius": 16, "shadow": "#315f91",
    },
    "breath_bubble": {
        "label": "吐气水泡 · 左上方", "placement": "top_left",
        "background": "#fbfeff", "border": "#0e5968", "foreground": "#23444d",
        "radius": 0, "shadow": "#0e5968", "shape": "breath_bubble",
    },
}


def breath_bubble_size_for_anchor(anchor_rect: QRect) -> QSize:
    """Scale the decorative water bubble with the pet's visible silhouette.

    The original 240 x 195 reference canvas looks oversized beside the smaller
    desktop-pet presets.  Keep its aspect ratio, but let the visible character
    width choose a bounded 168..216 px canvas.  Using the alpha-mask bounds
    (rather than the transparent video window) makes the result stable across
    the 320/461/544/640 px pet presets.
    """
    visible_width = max(1, int(anchor_rect.width()))
    width = max(168, min(216, int(round(visible_width * 0.82))))
    return QSize(width, int(width * 195 / 240 + 0.5))


def breath_bubble_size_for_scale(pet_scale: float) -> QSize:
    """Return stable, strictly increasing sizes for the supported pet scales."""
    scale = max(0.5, min(1.0, float(pet_scale)))
    width = int(round(120 + 96 * scale))
    return QSize(width, int(width * 195 / 240 + 0.5))


def list_self_talk_images(directory: str | Path) -> list[Path]:
    """List common image files directly inside a user-selected directory."""
    root = Path(str(directory or "")).expanduser()
    if not root.is_dir():
        return []
    try:
        return sorted(
            path for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in SELF_TALK_IMAGE_SUFFIXES
        )
    except OSError:
        return []


def normalize_bubble_text(text: str) -> str:
    """Convert model-flavoured Markdown into compact plain bubble text."""
    value = str(text or "").replace("```", " ")
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"(?m)^\s*[-*+]\s+", "", value)
    value = re.sub(r"[*_`]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def bubble_max_lines(text: str) -> int:
    """Return the max allowed lines for bubble text: 3 for short text, 6 for long."""
    return 3 if len(normalize_bubble_text(text)) <= 40 else 6


def elide_bubble_text(
    metrics: QFontMetrics,
    text: str,
    width: int,
    max_lines: int = 3,
) -> str:
    """Wrap text into a bounded number of lines and elide the remainder."""
    value = normalize_bubble_text(text)
    if not value:
        return ""
    lines: list[str] = []
    current = ""
    for index, char in enumerate(value):
        candidate = current + char
        if current and metrics.horizontalAdvance(candidate) > width:
            lines.append(current)
            if len(lines) >= max_lines:
                lines[-1] = metrics.elidedText(
                    current + value[index:], Qt.TextElideMode.ElideRight, width
                )
                return "\n".join(lines)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines[:max_lines])


def paginate_bubble_text(
    metrics: QFontMetrics,
    text: str,
    width: int,
    max_lines: int = 3,
) -> list[str]:
    """Wrap text into pages of at most ``max_lines`` lines each — no elision.

    Unlike :func:`elide_bubble_text`, no content is ever cut: long text is
    split into several pages so the whole message can be shown by flipping
    pages.  Returns a list of page strings (each already contains ``\n``
    line breaks); a single-element list means one page suffices.
    """
    value = normalize_bubble_text(text)
    if not value:
        return []
    lines: list[str] = []
    current = ""
    for index, char in enumerate(value):
        candidate = current + char
        if current and metrics.horizontalAdvance(candidate) > width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return ["\n".join(lines)]
    return [
        "\n".join(lines[start : start + max_lines])
        for start in range(0, len(lines), max_lines)
    ]


def bubble_rect_for_anchor(
    anchor_rect: QRect,
    bubble_size: QSize,
    available: QRect,
    placement: str = "top",
    gap: int = 12,
) -> QRect:
    """Return an on-screen bubble rectangle that never covers the pet if space permits."""
    width, height = bubble_size.width(), bubble_size.height()
    centered_x = anchor_rect.center().x() - width // 2
    left_x = anchor_rect.left() - width + max(24, anchor_rect.width() // 3)
    right_x = anchor_rect.right() - max(24, anchor_rect.width() // 3)
    above_y = anchor_rect.top() - height - gap
    preferred = {
        "top_left": QPoint(left_x, above_y),
        "top_right": QPoint(right_x, above_y),
        "top": QPoint(centered_x, above_y),
    }.get(placement, QPoint(centered_x, above_y))
    candidates = [
        preferred,
        QPoint(centered_x, above_y),
        QPoint(left_x, above_y),
        QPoint(right_x, above_y),
        QPoint(anchor_rect.right() + gap, anchor_rect.center().y() - height // 2),
        QPoint(anchor_rect.left() - width - gap, anchor_rect.center().y() - height // 2),
        QPoint(centered_x, anchor_rect.bottom() + gap),
    ]
    for point in candidates:
        candidate = QRect(point, bubble_size)
        if available.contains(candidate) and not candidate.intersects(anchor_rect):
            return candidate

    # Clamp every fallback before scoring it. This keeps the bubble usable on a
    # small display while preferring a result with zero overlap.
    best = None
    best_overlap = None
    for point in candidates:
        x = min(max(point.x(), available.left()), available.right() - width + 1)
        y = min(max(point.y(), available.top()), available.bottom() - height + 1)
        candidate = QRect(QPoint(x, y), bubble_size)
        overlap = candidate.intersected(anchor_rect)
        area = max(0, overlap.width()) * max(0, overlap.height())
        if best is None or area < best_overlap:
            best, best_overlap = candidate, area
    return best or QRect(available.topLeft(), bubble_size)


class PetSpeechBubble(QFrame):
    """不依赖桌宠透明窗口的独立气泡，支持跨屏幕边界自动选位。"""

    clicked = Signal()

    # 气泡被隐藏（自动超时 / dismiss / 窗口隐藏）时发出，供上层在仍有
    # 待处理审批时自动恢复展示。
    hidden_signal = Signal()

    def __init__(self, parent=None, style_id: str = "classic_top"):
        super().__init__(parent)
        self._interactive = False
        self.setObjectName("pet-speech-bubble")
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # 气泡只是状态提示，在所有平台都不应该成为键盘焦点窗口。
        # WA_ShowWithoutActivating 在部分窗口系统上只是提示，而这个原生窗口
        # flag 才是防止定时 show() 抢走其他应用输入光标的硬约束。
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
        # Text is pre-wrapped with the actual font metrics so the organic safe
        # area has a deterministic three-line limit. Letting QLabel wrap again
        # can turn those three lines into four after stylesheet font polishing.
        self.label.setWordWrap(False)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setStyleSheet("background: transparent; border: none; padding: 0;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(13, 10, 13, 17)
        self._layout.addWidget(self.label)
        self._subtitle_label = QLabel(self)
        self._subtitle_label.setObjectName("pet-speech-subtitle")
        # The subtitle is user/LLM supplied (for example the watchdog's
        # recommendation).  Keep it inside the same text column as the main
        # message; QLabel otherwise reports the full unwrapped line as its
        # sizeHint and can stretch an interactive bubble across the chat UI.
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._subtitle_label.setMaximumWidth(248)
        self._subtitle_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._subtitle_label.hide()
        self._layout.addWidget(self._subtitle_label)
        self._page_indicator = QLabel(self)
        self._page_indicator.setObjectName("pet-page-indicator")
        self._page_indicator.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._page_indicator.hide()
        self._layout.addWidget(self._page_indicator, 0, Qt.AlignmentFlag.AlignRight)
        # 交互按钮行（审批「同意/拒绝」、问题「A/B/C」等）：默认隐藏，仅
        # show_text(buttons=...) 时出现。气泡平时对鼠标全透明，交互时临时关闭
        # 该属性让按钮可点，dismiss/隐藏时恢复穿透。
        # 用 FlowLayout 代替 QHBoxLayout，选项多时自动换行，不被撑宽。
        self._button_row = QWidget(self)
        self._button_row.setObjectName("pet-speech-buttons")
        self._button_layout = FlowLayout(self._button_row, h_spacing=6, v_spacing=6)
        self._button_layout.setContentsMargins(0, 4, 0, 0)
        self._button_row.hide()
        self._layout.addWidget(self._button_row)
        self._interactive_active = False
        self._interactive_buttons: list[QPushButton] = []
        # 长文本分页状态：页列表 + 当前页 + 自动翻页定时器。
        # 气泡对鼠标全透明（WA_TransparentForMouseEvents），无法靠点击翻页，
        # 因此采用「每页停留一小段后自动翻下一页」的方式保证全文可读完。
        self._pages: list[str] = []
        self._page_index = 0
        self._page_interval_ms = 0
        self._page_timer = QTimer(self)
        self._page_timer.setSingleShot(True)
        self._page_timer.timeout.connect(self._on_page_timeout)
        self._style_id = ""
        self._preset = BUBBLE_STYLE_PRESETS["classic_top"]
        self._anchor_rect = QRect()
        self._surface_rect = QRect()
        self._surface_path = QPainterPath()
        self._main_bubble_path = QPainterPath()
        self._breath_paths: list[QPainterPath] = []
        self._breath_image_rect = QRectF()
        self._breath_image_clip_path = QPainterPath()
        self._standard_image_rect = QRectF()
        self._water_fill = ""
        self._water_start_ratio = 0.0
        self._highlight_width_ratios = (0.0, 0.0)
        self._breath_scale = 1.0
        self._shadow_alpha = 18
        self._tail_base = (QPointF(), QPointF())
        self._tail_tip = QPointF()
        self._shadow_offset_y = 2
        # QPainter has no cheap cross-platform blur for a translucent tool
        # window. Several restrained outline layers produce a softer, more
        # stable shadow than the old single dark halo without a graphics effect.
        self._shadow_layers = (
            (4.0, 6, 1.5),
            (2.5, 10, 1.25),
            (1.0, 18, 1.0),
        )
        self._content_kind = "text"
        self._raw_text = ""
        self._source_pixmap = QPixmap()
        self._pet_scale: float | None = None
        self.set_style(style_id)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    @property
    def style_id(self) -> str:
        return self._style_id

    def set_style(self, style_id: str) -> None:
        self._style_id = style_id if style_id in BUBBLE_STYLE_PRESETS else "classic_top"
        self._preset = BUBBLE_STYLE_PRESETS[self._style_id]
        if self._preset.get("shape") == "breath_bubble":
            self._layout.setAlignment(self.label, Qt.AlignmentFlag.AlignCenter)
            self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self._water_fill = "#d9f2fb"
            self._shadow_alpha = 0
        else:
            self._layout.setContentsMargins(13, 10, 13, 17)
            self._layout.setAlignment(
                self.label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self._water_fill = ""
            self._shadow_alpha = 18
        self.label.setStyleSheet(
            "QLabel#pet-speech-label { background: transparent; border: none; padding: 0; "
            f"color: {self._preset['foreground']}; font-size: 13px; }}"
        )
        self._subtitle_label.setStyleSheet(
            "QLabel#pet-speech-subtitle { background: transparent; border: none; padding: 0; "
            f"color: {self._preset['foreground']}; font-size: 10px; }}"
        )
        self._page_indicator.setStyleSheet(
            "QLabel#pet-page-indicator { background: transparent; border: none; padding: 0; "
            f"color: {self._preset['foreground']}; font-size: 10px; }}"
        )
        self.update()

    def _configure_breath_content(
        self,
        anchor_rect: QRect,
        pet_scale: float | None,
    ) -> None:
        base_size = (
            breath_bubble_size_for_scale(pet_scale)
            if pet_scale is not None
            else breath_bubble_size_for_anchor(anchor_rect)
        )
        bubble_size = self._breath_size_for_content(base_size)
        self._breath_scale = bubble_size.width() / 240.0
        scale = self._breath_scale
        if self._content_kind == "image":
            self._layout.setContentsMargins(
                round(bubble_size.width() * 0.15),
                round(bubble_size.height() * 0.16),
                round(bubble_size.width() * 0.20),
                round(bubble_size.height() * 0.18),
            )
        else:
            self._layout.setContentsMargins(
                round(28 * scale), round(56 * scale),
                round(52 * scale), round(44 * scale),
            )
        margins = self._layout.contentsMargins()
        label_width = max(
            92, bubble_size.width() - margins.left() - margins.right()
        )
        label_height = max(
            42, bubble_size.height() - margins.top() - margins.bottom()
        )
        self.label.setFixedSize(label_width, label_height)
        self.setFixedSize(bubble_size)
        if self._content_kind == "image" and not self._source_pixmap.isNull():
            # Paint the image on the parent below water/outline/highlights.
            # QLabel children paint afterwards and used to cover those details.
            self.label.setText("")
            self.label.setPixmap(QPixmap())
            self.label.hide()
        else:
            self.label.show()
            self.label.setPixmap(QPixmap())
            self.label.setText(elide_bubble_text(
                QFontMetrics(self.label.font()),
                self._raw_text,
                label_width,
                bubble_max_lines(self._raw_text),
            ))

    def _breath_size_for_content(self, base_size: QSize) -> QSize:
        """Grow the reference canvas when content needs more safe-area space."""
        width = base_size.width()
        if self._content_kind == "image" and not self._source_pixmap.isNull():
            target = self._source_pixmap.size()
            target.scale(QSize(150, 122), Qt.AspectRatioMode.KeepAspectRatio)
            # Image safe area occupies 65% of the width and 66% of the height.
            width = max(
                width,
                ceil(target.width() / 0.65),
                ceil(target.height() / (0.66 * 195 / 240)),
            )
        elif self._content_kind == "text":
            length = len(normalize_bubble_text(self._raw_text))
            if length > 24:
                width += min(104, ceil((length - 24) / 8) * 14)
        width = max(base_size.width(), min(320, width))
        return QSize(width, int(width * 195 / 240 + 0.5))

    def set_interactive(self, on: bool) -> None:
        """开启后气泡可被鼠标点击（用于触发快速对话），默认全透明穿透。"""
        self._interactive = bool(on)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not self._interactive)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._interactive and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def show_text(
        self,
        text: str,
        anchor_rect: QRect,
        duration_ms: int = 3200,
        *,
        pet_scale: float | None = None,
        subtitle: str = "",
        sticky: bool = False,
        buttons: list[tuple[str, object]] | None = None,
    ) -> None:
        """显示文本气泡。

        ``sticky=True`` 时不启动自动隐藏定时器，气泡一直停留直到上层调用
        :meth:`dismiss`（用于「审批一直挂着直到审批结束」这类需要主动关闭的气泡）。
        审批文案短、无需分页，sticky 时强制单页展示。

        ``buttons`` 为 ``[(label, callback), ...]`` 时进入「交互气泡」模式：气泡内
        排一行可点按钮（审批同意/拒绝、问题 A/B/C），点击即回调并把决策交还上层
        （如回写 DSH）。交互气泡自动 sticky，且临时关闭鼠标穿透让按钮可点，
        收起/隐藏时恢复穿透。
        """
        text = str(text).strip()
        if not text:
            return
        interactive = bool(buttons)
        # 交互提醒（如循环检测的三按钮弹窗）必须保持按钮和回调绑定。
        # 动画/普通气泡偶尔会在之后尝试重绘同一窗口；若允许无按钮的
        # show_text 覆盖这里，视觉上文字还在，但按钮已被 teardown。
        if self._interactive_active and not interactive:
            return
        self._content_kind = "text"
        self._raw_text = text
        self._source_pixmap = QPixmap()
        self._pet_scale = pet_scale
        self._reset_paging()
        subtitle = str(subtitle or "").strip()
        if subtitle:
            self._subtitle_label.setText(subtitle)
            self._subtitle_label.show()
        else:
            self._subtitle_label.setText("")
            self._subtitle_label.hide()
        self.label.show()
        metrics = QFontMetrics(self.label.font())
        if interactive:
            # 交互气泡：不自动消失 + 显示按钮 + 临时关闭鼠标穿透
            self._setup_buttons(buttons)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self._interactive_active = True
        else:
            self._teardown_interactive()
        if self._preset.get("shape") == "breath_bubble" and not interactive:
            self._configure_breath_content(anchor_rect, pet_scale)
        else:
            # 长文本分页：每页不超过 bubble_max_lines 行，自动翻页直到全文展示完，
            # 底部显示「1/3」页码指示。总时长按页数扩展，保证每页可读完。
            pages = paginate_bubble_text(
                metrics, text, 248, bubble_max_lines(text)
            )
            display_text = pages[0] if pages else ""
            if len(pages) > 1 and not sticky and not interactive:
                per_page = max(2200, min(5000, duration_ms // len(pages)))
                total_ms = max(duration_ms, per_page * len(pages))
                self._pages = pages
                self._page_index = 0
                self._page_interval_ms = per_page
                self._page_indicator.setText(f"1/{len(pages)}")
                self._page_indicator.show()
                self._page_timer.start(per_page)
                duration_ms = total_ms
            else:
                self._reset_paging()
            self.label.setPixmap(QPixmap())
            self.label.setText(display_text)
            # 固定尺寸必须按所有页的最大测量值计算：后续页可能出现更宽的行，
            # 若只按第一页设置，翻页后 wordWrap=False 会把后续页文本裁掉。
            max_width = 0
            max_height = 0
            for page in pages:
                page_bounds = metrics.boundingRect(
                    QRect(0, 0, 248, 600), Qt.TextFlag.TextWordWrap, page
                )
                max_width = max(max_width, page_bounds.width())
                max_height = max(max_height, page_bounds.height())
            self.label.setFixedSize(
                max(96, min(248, max_width + 3)),
                max(20, max_height + 2),
            )
        self.adjustSize()
        self._place(anchor_rect)
        self.show()
        if not _MAC:
            self.raise_()
        if sticky or interactive:
            # 审批等需主动关闭的气泡：不启动自动隐藏，由上层 dismiss() 收尾
            self._hide_timer.stop()
        else:
            self._hide_timer.start(max(500, int(duration_ms)))

    def _setup_buttons(self, buttons: list[tuple[str, object]]) -> None:
        """清空旧按钮并按元素重建按钮行（仅交互气泡用）。

        元素除普通 ``(label, callback)`` 按钮外，还支持两类结构化行：
        - ``("__header__", text)``：分支标题行（独占一行、加粗）——多分支问题
          弹窗按分支分组展示的标题。
        - ``("__hint__", text)``：灰色提示行（独占一行）——例如自由文本问题
          「请到 DSH 界面输入文本回答」。
        """
        while self._button_layout.count():
            item = self._button_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._interactive_buttons = []
        for label, callback in buttons:
            if label == SECTION_HEADER_LABEL:
                header = QLabel(str(callback), self._button_row)
                header.setObjectName("pet-speech-branch-header")
                header.setWordWrap(True)
                header.setFixedWidth(220)
                header.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                header.setStyleSheet(
                    "QLabel { background: transparent; border: none;"
                    " font-weight:600; font-size:11px; color:#2b3a4a;"
                    " padding:2px 0 0 0; }"
                )
                self._button_layout.addWidget(header)
                continue
            if label == SECTION_HINT_LABEL:
                hint = QLabel(str(callback), self._button_row)
                hint.setObjectName("pet-speech-branch-hint")
                hint.setWordWrap(True)
                hint.setFixedWidth(220)
                hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                hint.setStyleSheet(
                    "QLabel { background: transparent; border: none;"
                    " font-size:10px; color:#7a8a9a; padding:1px 0 0 0; }"
                )
                self._button_layout.addWidget(hint)
                continue
            btn = QPushButton(str(label), self._button_row)
            btn.setObjectName("pet-speech-button")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(
                "QPushButton {"
                " background:#ffffff; border:1px solid #c9d4e0; border-radius:11px;"
                " padding:4px 12px; font-size:11px; color:#2b3a4a;"
                "}"
                "QPushButton:hover { background:#eef6ff; border-color:#8ab8e8; }"
                "QPushButton:pressed { background:#dcebfa; }"
            )
            btn.clicked.connect(lambda _checked=False, cb=callback: self._on_interactive_click(cb))
            self._interactive_buttons.append(btn)
            self._button_layout.addWidget(btn)
        self._button_row.show()

    def _on_interactive_click(self, callback) -> None:
        """交互按钮被点：先回调决策（上层清 _alert_current 并 dismiss），再隐藏收尾。

        时序很关键：若先 hide() 会触发 hidden_signal → 上层 _on_speech_bubble_hidden
        看到 _alert_current 还在会把审批气泡重新挂上，导致「点了同意/拒绝弹窗却不消失」。
        先回调让上层完成 hide_bubble()/dismiss()，再隐藏收尾。

        注意：callback() 内部通过 _resolve_interaction → resolve_alert →
        _speech_bubble.dismiss() 已经关闭了当前气泡，并通过 _on_speech_bubble_hidden
        → sticky restore 显示了下一个弹窗。此处不再调用 self.hide()——
        否则会二次隐藏已替换为下一条内容的气泡，导致其按钮被 deleteLater 清除、
        鼠标事件错乱（「第一个弹窗点击导致第二个弹窗也接收事件」）。
        """
        self._teardown_interactive()
        self._hide_timer.stop()
        try:
            callback()
        except Exception:
            log.exception("交互气泡回调异常")

    def _teardown_interactive(self) -> None:
        """退出交互态：恢复鼠标穿透、清空按钮行。隐藏/收起时都必须调用。"""
        if self._interactive_active:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._interactive_active = False
        while self._button_layout.count():
            item = self._button_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._interactive_buttons = []
        self._button_row.hide()

    def dismiss(self) -> None:
        """立即关闭当前气泡（停掉自动隐藏/翻页定时器）。供 sticky 气泡主动收尾。"""
        self._hide_timer.stop()
        self._page_timer.stop()
        self._reset_paging()
        self._teardown_interactive()
        self.hide()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """气泡被隐藏（超时 / dismiss / 父窗口隐藏）时通知上层。"""
        super().hideEvent(event)
        self._teardown_interactive()
        self.hidden_signal.emit()

    def _reset_paging(self) -> None:
        """停止自动翻页并隐藏页码指示（单页内容/图片/换内容时调用）。"""
        self._page_timer.stop()
        self._pages = []
        self._page_index = 0
        self._page_interval_ms = 0
        self._page_indicator.setText("")
        self._page_indicator.hide()

    def _on_page_timeout(self) -> None:
        """自动翻到下一页；最后一页展示完后由 _hide_timer 收尾隐藏。"""
        if not self._pages or self._page_index >= len(self._pages) - 1:
            self._page_timer.stop()
            return
        self._page_index += 1
        if self._content_kind == "text":
            self.label.setText(self._pages[self._page_index])
            self._page_indicator.setText(
                f"{self._page_index + 1}/{len(self._pages)}"
            )
            self._page_timer.start(self._page_interval_ms)

    def show_image(
        self,
        image_path: str | Path,
        anchor_rect: QRect,
        duration_ms: int = 3200,
        *,
        pet_scale: float | None = None,
    ) -> bool:
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            return False
        self._content_kind = "image"
        self._raw_text = ""
        self._source_pixmap = pixmap
        self._pet_scale = pet_scale
        self._reset_paging()
        self._subtitle_label.setText("")
        self._subtitle_label.hide()
        if self._preset.get("shape") == "breath_bubble":
            self._configure_breath_content(anchor_rect, pet_scale)
        else:
            target = pixmap.size()
            target.scale(QSize(220, 140), Qt.AspectRatioMode.KeepAspectRatio)
            target.setWidth(max(96, target.width()))
            target.setHeight(max(64, target.height()))
            self.label.setFixedSize(target)
            self.label.setText("")
            self.label.show()
            # Keep QLabel only as a layout placeholder. Parent painting uses
            # the original source, avoiding QLabel's DPR=1 pixmap normalization.
            self.label.setPixmap(QPixmap())
        self.adjustSize()
        self._place(anchor_rect)
        self.show()
        if not _MAC:
            self.raise_()
        self._hide_timer.start(max(500, int(duration_ms)))
        return True

    def reflow(self, anchor_rect: QRect, *, pet_scale: float | None = None) -> None:
        """Resize current content after a pet scale change, then reposition it."""
        if pet_scale is not None:
            self._pet_scale = float(pet_scale)
        if self._preset.get("shape") == "breath_bubble":
            self._configure_breath_content(anchor_rect, self._pet_scale)
            self.adjustSize()
        self._place(anchor_rect)

    def reposition(self, anchor_rect: QRect) -> None:
        if self.isVisible():
            self._place(anchor_rect)

    def _place(self, anchor_rect: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        # Image breath bubbles hide the QLabel because the parent paints the
        # clipped image below its decorations. Their sizeHint therefore only
        # contains layout margins; position the real fixed-size window instead.
        size = self.size()
        rect = bubble_rect_for_anchor(anchor_rect, size, avail, self._preset["placement"])
        if self._preset.get("shape") == "breath_bubble" and rect.bottom() < anchor_rect.top():
            # The reference canvas deliberately leaves transparent space below
            # the smallest detached bubble.  Position by the painted contour,
            # not by the transparent QWidget edge, otherwise that inset and the
            # generic 12 px placement gap add up to a visibly disconnected
            # thought bubble after adaptive down-scaling.
            self._build_breath_bubble_geometry(self.rect())
            visual_bottom = self._surface_path.boundingRect().bottom()
            visual_gap = 7
            top = int(round(anchor_rect.top() - visual_gap - visual_bottom))
            top = min(max(top, avail.top()), avail.bottom() - rect.height() + 1)
            rect.moveTop(top)
        self._anchor_rect = QRect(anchor_rect)
        self.move(rect.topLeft())
        self._update_surface_geometry(rect)

    def _update_surface_geometry(self, global_rect: QRect) -> None:
        local = self.rect()
        if self._preset.get("shape") == "breath_bubble":
            self._build_breath_bubble_geometry(local)
            self.update()
            return
        anchor_center = self.mapFromGlobal(self._anchor_rect.center())
        if global_rect.bottom() < self._anchor_rect.top():
            self._surface_rect = local.adjusted(4, 3, -4, -10)
            tip_x = min(max(anchor_center.x(), 20), local.width() - 20)
            self._tail_base = (
                QPointF(tip_x - 6, self._surface_rect.bottom() - 2),
                QPointF(tip_x + 6, self._surface_rect.bottom() - 2),
            )
            self._tail_tip = QPointF(tip_x, local.bottom() - 4)
        elif global_rect.left() > self._anchor_rect.right():
            self._surface_rect = local.adjusted(10, 3, -4, -4)
            tip_y = min(max(anchor_center.y(), 18), local.height() - 18)
            self._tail_base = (
                QPointF(self._surface_rect.left() + 2, tip_y - 6),
                QPointF(self._surface_rect.left() + 2, tip_y + 6),
            )
            self._tail_tip = QPointF(4, tip_y)
        elif global_rect.right() < self._anchor_rect.left():
            self._surface_rect = local.adjusted(4, 3, -10, -4)
            tip_y = min(max(anchor_center.y(), 18), local.height() - 18)
            self._tail_base = (
                QPointF(self._surface_rect.right() - 2, tip_y - 6),
                QPointF(self._surface_rect.right() - 2, tip_y + 6),
            )
            self._tail_tip = QPointF(local.right() - 4, tip_y)
        else:
            self._surface_rect = local.adjusted(4, 10, -4, -4)
            tip_x = min(max(anchor_center.x(), 20), local.width() - 20)
            self._tail_base = (
                QPointF(tip_x - 6, self._surface_rect.top() + 2),
                QPointF(tip_x + 6, self._surface_rect.top() + 2),
            )
            self._tail_tip = QPointF(tip_x, 4)
        rounded = QPainterPath()
        radius = float(self._preset["radius"])
        rounded.addRoundedRect(QRectF(self._surface_rect), radius, radius)
        self._main_bubble_path = QPainterPath(rounded)
        self._breath_paths = []
        tail = QPainterPath()
        tail.moveTo(self._tail_base[0])
        tail.lineTo(self._tail_tip)
        tail.lineTo(self._tail_base[1])
        tail.closeSubpath()
        self._surface_path = rounded.united(tail).simplified()
        self.update()

    def _build_breath_bubble_geometry(self, local: QRect) -> None:
        """Build the reference's organic bubble plus two detached breath bubbles."""
        sx = max(0.01, local.width() / 240.0)
        sy = max(0.01, local.height() / 195.0)
        self._breath_scale = min(sx, sy)
        rect = QRectF(5 * sx, 4 * sy, 193 * sx, 163 * sy)
        x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        main = QPainterPath()
        main.moveTo(x + width * 0.17, y + height * 0.06)
        main.cubicTo(
            x + width * 0.43, y - height * 0.03,
            x + width * 0.73, y + height * 0.01,
            x + width * 0.89, y + height * 0.20,
        )
        main.cubicTo(
            x + width * 1.01, y + height * 0.35,
            x + width * 1.01, y + height * 0.65,
            x + width * 0.86, y + height * 0.83,
        )
        main.cubicTo(
            x + width * 0.67, y + height * 1.02,
            x + width * 0.33, y + height * 1.02,
            x + width * 0.13, y + height * 0.84,
        )
        main.cubicTo(
            x - width * 0.02, y + height * 0.68,
            x - width * 0.04, y + height * 0.39,
            x + width * 0.07, y + height * 0.22,
        )
        main.cubicTo(
            x + width * 0.09, y + height * 0.16,
            x + width * 0.12, y + height * 0.10,
            x + width * 0.17, y + height * 0.06,
        )
        main.closeSubpath()

        # The two trailing bubbles intentionally use hand-drawn cubic contours,
        # not QPainterPath.addEllipse(), matching the reference's irregular rims.
        large_x, large_y, large_w, large_h = (
            local.width() - 71.0 * sx, local.height() - 58.0 * sy,
            31.0 * sx, 29.0 * sy,
        )
        large = QPainterPath()
        large.moveTo(large_x + large_w * 0.45, large_y)
        large.cubicTo(
            large_x + large_w * 0.72, large_y - large_h * 0.05,
            large_x + large_w * 1.02, large_y + large_h * 0.28,
            large_x + large_w * 0.94, large_y + large_h * 0.58,
        )
        large.cubicTo(
            large_x + large_w * 0.86, large_y + large_h * 0.91,
            large_x + large_w * 0.38, large_y + large_h * 1.04,
            large_x + large_w * 0.11, large_y + large_h * 0.78,
        )
        large.cubicTo(
            large_x - large_w * 0.07, large_y + large_h * 0.55,
            large_x + large_w * 0.12, large_y + large_h * 0.14,
            large_x + large_w * 0.45, large_y,
        )
        large.closeSubpath()

        small_x, small_y, small_w, small_h = (
            local.width() - 39.0 * sx, local.height() - 37.0 * sy,
            14.0 * sx, 13.0 * sy,
        )
        small = QPainterPath()
        small.moveTo(small_x + small_w * 0.40, small_y)
        small.cubicTo(
            small_x + small_w * 0.75, small_y - small_h * 0.03,
            small_x + small_w * 1.03, small_y + small_h * 0.31,
            small_x + small_w * 0.92, small_y + small_h * 0.62,
        )
        small.cubicTo(
            small_x + small_w * 0.78, small_y + small_h * 0.96,
            small_x + small_w * 0.34, small_y + small_h * 1.04,
            small_x + small_w * 0.08, small_y + small_h * 0.70,
        )
        small.cubicTo(
            small_x - small_w * 0.05, small_y + small_h * 0.42,
            small_x + small_w * 0.09, small_y + small_h * 0.10,
            small_x + small_w * 0.40, small_y,
        )
        small.closeSubpath()
        self._main_bubble_path = main
        self._breath_paths = [large, small]
        self._surface_path = QPainterPath(main)
        self._surface_path.addPath(large)
        self._surface_path.addPath(small)
        self._surface_rect = main.boundingRect().toRect()
        self._tail_base = (QPointF(), QPointF())
        self._tail_tip = QPointF()
        self._breath_image_rect = QRectF()
        self._breath_image_clip_path = QPainterPath()
        if self._content_kind == "image" and not self._source_pixmap.isNull():
            bounds = main.boundingRect()
            safe = bounds.adjusted(
                bounds.width() * 0.12,
                bounds.height() * 0.12,
                -bounds.width() * 0.18,
                -bounds.height() * 0.15,
            )
            target = self._source_pixmap.size()
            target.scale(safe.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            image_rect = QRectF(0, 0, target.width(), target.height())
            image_rect.moveCenter(safe.center())
            rounded = QPainterPath()
            image_radius = max(7.0, 12.0 * self._breath_scale)
            rounded.addRoundedRect(image_rect, image_radius, image_radius)
            self._breath_image_rect = image_rect
            self._breath_image_clip_path = rounded.intersected(main)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        background = QColor(self._preset["background"])
        border = QColor(self._preset["border"])
        if self._preset.get("shape") == "breath_bubble":
            self._paint_breath_bubble(painter, background, border)
            return
        self._paint_soft_shadow(painter, self._surface_path)
        painter.setBrush(background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(self._surface_path)
        self._standard_image_rect = QRectF()
        if self._content_kind == "image" and not self._source_pixmap.isNull():
            image_rect = QRectF(self.label.geometry())
            image_clip = QPainterPath()
            radius = max(5.0, float(self._preset["radius"]) * 0.65)
            image_clip.addRoundedRect(image_rect, radius, radius)
            painter.save()
            painter.setClipPath(image_clip.intersected(self._surface_path))
            painter.drawPixmap(
                image_rect,
                self._source_pixmap,
                QRectF(self._source_pixmap.rect()),
            )
            painter.restore()
            self._standard_image_rect = image_rect
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(self._surface_path)

    def _paint_soft_shadow(self, painter: QPainter, path: QPainterPath) -> None:
        base = QColor(self._preset["shadow"])
        for width, alpha, offset_y in self._shadow_layers:
            color = QColor(base)
            color.setAlpha(alpha)
            shadow_path = QTransform.fromTranslate(0, offset_y).map(path)
            painter.setBrush(color)
            painter.setPen(QPen(
                color, width, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
            ))
            painter.drawPath(shadow_path)

    def _paint_breath_bubble(self, painter: QPainter, background: QColor, border: QColor) -> None:
        self._paint_soft_shadow(painter, self._surface_path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawPath(self._main_bubble_path)

        bounds = self._main_bubble_path.boundingRect()
        if (
            self._content_kind == "image"
            and not self._source_pixmap.isNull()
            and not self._breath_image_clip_path.isEmpty()
        ):
            painter.save()
            painter.setClipPath(self._breath_image_clip_path)
            painter.drawPixmap(
                self._breath_image_rect,
                self._source_pixmap,
                QRectF(self._source_pixmap.rect()),
            )
            painter.restore()

        water = QPainterPath()
        self._water_start_ratio = 0.48
        water.moveTo(bounds.left() - 2, bounds.top() + bounds.height() * self._water_start_ratio)
        water.cubicTo(
            bounds.left() + bounds.width() * 0.13, bounds.top() + bounds.height() * 0.70,
            bounds.left() + bounds.width() * 0.30, bounds.top() + bounds.height() * 0.88,
            bounds.left() + bounds.width() * 0.52, bounds.top() + bounds.height() * 0.90,
        )
        water.cubicTo(
            bounds.left() + bounds.width() * 0.72, bounds.top() + bounds.height() * 0.92,
            bounds.left() + bounds.width() * 0.90, bounds.top() + bounds.height() * 0.82,
            bounds.right() + 2, bounds.top() + bounds.height() * 0.69,
        )
        water.lineTo(bounds.right() + 4, bounds.bottom() + 4)
        water.lineTo(bounds.left() - 4, bounds.bottom() + 4)
        water.closeSubpath()
        painter.save()
        painter.setClipPath(self._main_bubble_path)
        painter.fillPath(water, QColor(self._water_fill))
        painter.restore()

        outline_width = max(2.2, 3.2 * self._breath_scale)
        outline = QPen(border, outline_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._main_bubble_path)
        painter.setBrush(background)
        for breath in self._breath_paths:
            painter.drawPath(breath)

        highlight = QPainterPath()
        highlight_start = 0.70
        highlight_end = 0.85
        highlight.moveTo(bounds.left() + bounds.width() * highlight_start, bounds.top() + bounds.height() * 0.14)
        highlight.cubicTo(
            bounds.left() + bounds.width() * 0.76, bounds.top() + bounds.height() * 0.17,
            bounds.left() + bounds.width() * 0.82, bounds.top() + bounds.height() * 0.23,
            bounds.left() + bounds.width() * highlight_end, bounds.top() + bounds.height() * 0.27,
        )
        painter.setPen(QPen(border, max(2.0, 3.0 * self._breath_scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(highlight)
        glint = QPainterPath()
        glint_start = 0.875
        glint_end = 0.92
        glint.moveTo(bounds.left() + bounds.width() * glint_start, bounds.top() + bounds.height() * 0.30)
        glint.lineTo(bounds.left() + bounds.width() * glint_end, bounds.top() + bounds.height() * 0.35)
        painter.drawPath(glint)
        self._highlight_width_ratios = (
            highlight_end - highlight_start,
            glint_end - glint_start,
        )
