# -*- coding: utf-8 -*-
"""Speech bubble unit tests."""
from __future__ import annotations

from math import ceil
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from pet.speech_bubble import (
    PetSpeechBubble,
    bubble_max_lines,
    elide_bubble_text,
    normalize_bubble_text,
)


def _get_app():
    return QApplication.instance() or QApplication([])


def test_bubble_max_lines_threshold():
    # <= 40 chars -> 3 lines, > 40 chars -> 6 lines
    short_39 = "一" * 39
    boundary_40 = "一" * 40
    long_41 = "一" * 41
    long_100 = "一" * 100

    assert bubble_max_lines(short_39) == 3
    assert bubble_max_lines(boundary_40) == 3
    assert bubble_max_lines(long_41) == 6
    assert bubble_max_lines(long_100) == 6

    # Markdown normalization check
    md_text = "**" + "a" * 40 + "**"
    assert len(normalize_bubble_text(md_text)) == 40
    assert bubble_max_lines(md_text) == 3

    md_text_41 = "**" + "a" * 41 + "**"
    assert len(normalize_bubble_text(md_text_41)) == 41
    assert bubble_max_lines(md_text_41) == 6


def test_elide_bubble_text_max_lines_6():
    _get_app()
    font = QFont("Arial", 12)
    metrics = QFontMetrics(font)

    # Pick a character and calculate width per line
    char = "测"
    char_w = metrics.horizontalAdvance(char)
    # Line width fitting exactly 5 chars
    line_w = char_w * 5

    # 1. Text that fits in 6 lines (e.g. 5 * 6 = 30 chars) should not be truncated / no ellipsis
    text_30 = char * 30
    elided_30 = elide_bubble_text(metrics, text_30, line_w, max_lines=6)
    lines_30 = elided_30.split("\n")
    assert len(lines_30) == 6
    assert "…" not in elided_30
    assert "..." not in elided_30
    assert elided_30 == "\n".join([char * 5] * 6)

    # 2. Text that exceeds 6 lines (e.g. 35 chars = 7 lines) should be truncated to 6 lines and end with ellipsis
    text_35 = char * 35
    elided_35 = elide_bubble_text(metrics, text_35, line_w, max_lines=6)
    lines_35 = elided_35.split("\n")
    assert len(lines_35) == 6
    assert "…" in lines_35[-1] or "..." in lines_35[-1]


def test_breath_size_for_content_short_text_matches_legacy():
    _get_app()
    bubble = PetSpeechBubble(style_id="breath_bubble")
    base_size = QSize(200, 162)

    # Legacy formula:
    # length = len(normalize_bubble_text(self._raw_text))
    # if length > 24:
    #     width += min(48, ceil((length - 24) / 8) * 12)
    # width = max(base_size.width(), min(264, width))
    # return QSize(width, int(width * 195 / 240 + 0.5))

    # For text <= 24, width remains base_size.width() = 200, height = int(200 * 195 / 240 + 0.5) = 163
    test_cases = [
        "",
        "hello",
        "123456789012345678901234",  # 24 chars
    ]

    for text in test_cases:
        bubble._content_kind = "text"
        bubble._raw_text = text
        size = bubble._breath_size_for_content(base_size)
        # Expected from legacy formula for <= 24:
        expected_width = 200
        expected_height = int(expected_width * 195 / 240 + 0.5)  # 163
        assert size == QSize(expected_width, expected_height)

    # Test with different base size
    base_size_168 = QSize(168, 137)
    for text in test_cases:
        bubble._content_kind = "text"
        bubble._raw_text = text
        size = bubble._breath_size_for_content(base_size_168)
        expected_width = 168
        expected_height = int(expected_width * 195 / 240 + 0.5)
        assert size == QSize(expected_width, expected_height)
