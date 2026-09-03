# -*- coding: utf-8 -*-
"""speech_bubble 纯函数区 — 气泡文本分页 / 定位 / 内容模型。

批6-2 从 pet/speech_bubble.py 整体迁出（纯搬移，逻辑/默认值零改动）：
- 文本规整与行数上限（normalize_bubble_text / bubble_max_lines）；
- 省略与分页（elide_bubble_text / paginate_bubble_text）；
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
