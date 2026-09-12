# -*- coding: utf-8 -*-
"""speech_bubble 纯函数区 — 气泡文本分页 / 定位 / 内容模型。

批6-2 从 pet/speech_bubble.py 整体迁出（纯搬移，逻辑/默认值零改动）：
- 文本规整与行数上限（normalize_bubble_text / bubble_max_lines）；
- 省略与分页（elide_bubble_text / paginate_bubble_text，换行带避头尾禁则）；
- 分页节奏与页码（page_dwell_ms / page_dots 与 PAGE_* 常量）；
- 定位与尺寸（bubble_rect_for_anchor / breath_bubble_size_for_anchor /
  breath_bubble_size_for_scale）；
- 自言自语图片清单（list_self_talk_images + SELF_TALK_IMAGE_SUFFIXES）。

依赖方向：speech_bubble -> speech_bubble_text，本模块不得反向 import pet.speech_bubble。
Qt 依赖面最小化：只导入纯函数实际使用的 Qt 类型。
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics

SELF_TALK_IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
}

# 气泡文本列的最大像素宽（气泡整体宽度上限的由来），以及 label 矩形相对
# 最长行的余量。换行预算必须是 ``列宽 - 余量``：整型 horizontalAdvance 累加
# 与绘制时的自然（分数）宽度有亚像素差，余量同时吸收这个差，行尾才不会切字。
BUBBLE_TEXT_COLUMN = 248
BUBBLE_TEXT_SLACK = 4


def bubble_wrap_width(column: int = BUBBLE_TEXT_COLUMN, slack: int = BUBBLE_TEXT_SLACK) -> int:
    """Text wrapping budget: always leaves ``slack`` px inside the column."""
    return max(1, int(column) - int(slack))


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


# 避头尾（kinsoku）行首禁则字符：闭标点不允许出现在行首。逐字换行时若
# 新行的首字符落在本集合内，就把上一行的末字符拉下来陪它——否则长句
# 末尾的 "！" 会独占一行，分页时变成一个标点符号撑起一整页（孤字页）。
# 开引号（" ' “ ‘）不在此列：它们出现在行首是合法的。
LINE_START_FORBIDDEN = frozenset(
    "，。、；：？！…·～）】》」』”’"
    ",.;:!?)]}"
)


def _wrap_bubble_lines(
    metrics: QFontMetrics, value: str, width: int
) -> list[str]:
    """Wrap ``value`` char-by-char into lines within ``width`` px (kinsoku-aware)."""
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if current and metrics.horizontalAdvance(candidate) > width:
            if char in LINE_START_FORBIDDEN and len(current) > 1:
                # 行首禁则：上一行末字符下沉，与闭标点同行，避免标点孤字行。
                lines.append(current[:-1])
                current = current[-1] + char
            else:
                lines.append(current)
                current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


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
    lines = _wrap_bubble_lines(metrics, value, width)
    if len(lines) > max_lines:
        remainder = "".join(lines[max_lines:])
        lines = lines[:max_lines]
        lines[-1] = metrics.elidedText(
            lines[-1] + remainder, Qt.TextElideMode.ElideRight, width
        )
    return "\n".join(lines)


def bubble_label_size(
    metrics: QFontMetrics,
    pages: list[str],
    column: int = BUBBLE_TEXT_COLUMN,
    slack: int = BUBBLE_TEXT_SLACK,
    min_width: int = 96,
    min_height: int = 20,
) -> QSize:
    """Return the label rect that holds **every line that will be painted**.

    必须用与换行相同的度量、并直接量真正的行宽，不能再用
    ``QFontMetrics.boundingRect(..., TextWordWrap)`` 二次排版来推宽度：
    那次排版按自然（分数）宽度断行，而 ``paginate_bubble_text`` 按整型
    ``horizontalAdvance`` 累加，两者可以差一个字；label 一旦比真实行窄，
    QLabel（wordWrap=False）就会把行尾那个字切在边界上，看起来像被气泡挡掉。

    宽度按所有页里最长的一行 + ``slack`` 计算（页间切换不再改 label 尺寸）；
    高度按行数 × ``lineSpacing`` 计算（所有页里行数最多的一页决定）。
    """
    lines = [line for page in pages for line in page.split("\n")] or [""]
    widest = max(metrics.horizontalAdvance(line) for line in lines)
    line_count = max((len(page.split("\n")) for page in pages), default=1)
    return QSize(
        max(min_width, min(column, widest + slack)),
        max(min_height, line_count * metrics.lineSpacing() + 2),
    )


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
    lines = _wrap_bubble_lines(metrics, value, width)
    if len(lines) <= max_lines:
        return ["\n".join(lines)]
    pages = [
        lines[start : start + max_lines]
        for start in range(0, len(lines), max_lines)
    ]
    if len(pages) >= 2 and len(pages[-1]) == 1 and len(pages[-2]) > 1:
        # 孤行控制：最后一页只剩一行时，从前一页匀一行过来（3+1 → 2+2），
        # 避免末页只有零星几个字、看起来像气泡被截断。
        pages[-2], pages[-1] = pages[-2][:-1], [pages[-2][-1]] + pages[-1]
    return ["\n".join(page) for page in pages]


# —— 分页节奏与页码 ——
# 每页停留时长按该页字数自适应：基础停留 + 每字阅读时长，钳制在
# [PAGE_DWELL_MIN, PAGE_DWELL_MAX]。满页 3 行约 45-60 字 → 约 3.9-4.8s；
# 稀疏短页（孤行重平衡后的两行短页）相应缩短，不再一刀切。
PAGE_DWELL_BASE_MS = 1200
PAGE_DWELL_PER_CHAR_MS = 60
PAGE_DWELL_MIN_MS = 2500
PAGE_DWELL_MAX_MS = 8000

# 翻页过渡：淡出略快、淡入略慢，视觉更顺。
PAGE_FADE_OUT_MS = 110
PAGE_FADE_IN_MS = 150


def page_dwell_ms(page_text: str) -> int:
    """一页气泡文本的建议停留时长（按字数自适应）。"""
    chars = len(str(page_text or "").replace("\n", ""))
    dwell = PAGE_DWELL_BASE_MS + chars * PAGE_DWELL_PER_CHAR_MS
    return max(PAGE_DWELL_MIN_MS, min(PAGE_DWELL_MAX_MS, dwell))


def page_dots(index: int, total: int) -> str:
    """页码圆点：当前页实心 ●、其余空心 ○；单页返回空串。"""
    if total <= 1:
        return ""
    index = max(0, min(int(index), total - 1))
    return " ".join("●" if i == index else "○" for i in range(total))


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
