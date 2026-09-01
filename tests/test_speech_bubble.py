# -*- coding: utf-8 -*-
"""Speech bubble unit tests."""
from __future__ import annotations

from math import ceil
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from pet.speech_bubble import (
    FlowLayout,
    PetSpeechBubble,
    bubble_max_lines,
    elide_bubble_text,
    normalize_bubble_text,
    paginate_bubble_text,
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
    # 用 5 字符串的实际度量而非 5×单字符：部分平台（macOS）对 CJK 字形
    # 的 advance 累加有亚像素舍入，5×单字符可能略小于真实宽度，导致
    # 每行装不下 5 个字符、30 字符被意外省略。
    line_w = metrics.horizontalAdvance(char * 5)

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


def test_paginate_bubble_text_single_page():
    _get_app()
    font = QFont("Arial", 12)
    metrics = QFontMetrics(font)
    char = "测"
    line_w = metrics.horizontalAdvance(char * 5)

    # 一页装得下的文本 → 单页、无截断、无省略号
    pages = paginate_bubble_text(metrics, char * 15, line_w, max_lines=3)
    assert len(pages) == 1
    assert pages[0] == "\n".join([char * 5] * 3)
    assert "…" not in pages[0]

    # 空文本 → 空列表
    assert paginate_bubble_text(metrics, "", line_w) == []


def test_paginate_bubble_text_multiple_pages_keeps_full_content():
    _get_app()
    font = QFont("Arial", 12)
    metrics = QFontMetrics(font)
    char = "测"
    line_w = metrics.horizontalAdvance(char * 5)

    # 35 字符 = 7 行，3 行一页 → 3 页；每页不超过 max_lines 行
    text_35 = char * 35
    pages = paginate_bubble_text(metrics, text_35, line_w, max_lines=3)
    assert len(pages) == 3
    for page in pages:
        assert len(page.split("\n")) <= 3
    # 全文无损：拼接所有页去掉换行后 == 原始文本（对比 elide 的截断行为）
    assert "\n".join(pages).replace("\n", "") == text_35
    assert "…" not in "\n".join(pages)

    # 6 行恰好一页（bubble_max_lines 对长文本的 6 行上限）
    text_30 = char * 30
    pages_6 = paginate_bubble_text(metrics, text_30, line_w, max_lines=6)
    assert len(pages_6) == 1

    # 37 字符 = 8 行，6 行一页 → 2 页，全文仍在
    pages_long = paginate_bubble_text(metrics, char * 37, line_w, max_lines=6)
    assert len(pages_long) == 2
    assert "\n".join(pages_long).replace("\n", "") == char * 37


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


def test_sticky_bubble_no_auto_hide():
    """sticky=True 的气泡（审批）不启动自动隐藏定时器，一直停留直到 dismiss。"""
    _get_app()
    bubble = PetSpeechBubble()  # classic_top，避开 breath 复杂路径
    hidden_log = []
    bubble.hidden_signal.connect(lambda: hidden_log.append(1))

    bubble.show_text("有审批等你点", QRect(0, 0, 120, 120), 3200, sticky=True)
    assert bubble._hide_timer.isActive() is False, "sticky 气泡不应启动自动隐藏定时器"
    assert bubble.isVisible() is True

    # 普通气泡：有自动隐藏定时器
    bubble.show_text("普通气泡", QRect(0, 0, 120, 120), 3200, sticky=False)
    assert bubble._hide_timer.isActive() is True

    # dismiss 立即关闭并触发 hidden_signal
    bubble.dismiss()
    assert bubble.isVisible() is False
    assert len(hidden_log) == 1


def test_sticky_bubble_dismiss_emits_hidden_signal():
    """审批气泡 dismiss 后 hidden_signal 应发出（供上层判断不再恢复）。"""
    _get_app()
    bubble = PetSpeechBubble()
    hidden_log = []
    bubble.hidden_signal.connect(lambda: hidden_log.append(1))
    bubble.show_text("审批", QRect(0, 0, 120, 120), 3200, sticky=True)
    bubble.dismiss()
    assert len(hidden_log) == 1


# ============================================================================
# 交互气泡：选项按钮不撑宽气泡，文本区宽度自适应
# ============================================================================

def _fake_buttons(n: int):
    """构造 n 个按钮（label + no-op callback）。"""
    return [(f"选项 {i}", lambda: None) for i in range(n)]


def test_interactive_bubble_uses_flow_layout():
    """按钮行必须是 FlowLayout（自动换行，而不是横向一行撑宽）。"""
    bubble = PetSpeechBubble()
    assert isinstance(bubble._button_layout, FlowLayout)


def test_interactive_many_buttons_does_not_stretch_width():
    """很多选项时气泡宽度必须被文本宽度约束，不能被按钮总宽撑开。"""
    _get_app()
    bubble = PetSpeechBubble()
    text = "请选择下一步操作："
    # 单独显示文本得到基准宽度
    bubble.show_text(text, QRect(0, 0, 120, 120), 3200, sticky=True)
    text_width = bubble.width()

    # 带 8 个按钮的交互气泡：宽度不应显著超过纯文本气泡
    bubble.show_text(
        text, QRect(0, 0, 120, 120), 3200, sticky=True,
        buttons=_fake_buttons(8),
    )
    # 气泡宽度有上限（不应被 8 个按钮总宽横向撑开）；允许小幅放宽容纳按钮行
    assert bubble.width() <= max(text_width, 320) + 4, (
        f"交互气泡宽度 {bubble.width()} 不应远大于纯文本宽度 {text_width}"
    )


def test_interactive_label_width_follows_bubble():
    """文本 label 宽度应跟随气泡实际宽度（自适应，不固定 248）。"""
    _get_app()
    bubble = PetSpeechBubble()
    bubble.show_text(
        "短文本", QRect(0, 0, 120, 120), 3200, sticky=True,
        buttons=_fake_buttons(2),
    )
    assert bubble.label.width() > 0
    # label 在气泡内（含边距），不会超过气泡宽度
    assert bubble.label.width() <= bubble.width()


def test_flow_layout_has_height_for_width():
    """FlowLayout 必须实现 hasHeightForWidth / heightForWidth（换行高度计算的前提）。"""
    layout = FlowLayout()
    assert layout.hasHeightForWidth() is True
    assert layout.heightForWidth(120) >= 0

