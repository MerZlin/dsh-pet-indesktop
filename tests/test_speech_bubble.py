# -*- coding: utf-8 -*-
"""Speech bubble unit tests."""
from __future__ import annotations

import time
from math import ceil
from PySide6.QtCore import QPropertyAnimation, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import pet.speech_bubble as speech_bubble_module
from pet.speech_bubble import (
    BUBBLE_TEXT_COLUMN,
    BUBBLE_TEXT_COLUMN_MAX,
    BUBBLE_TEXT_SLACK,
    PAGE_DWELL_MAX_MS,
    PAGE_DWELL_MIN_MS,
    PAGE_RETURN_PAUSE_MS,
    FlowLayout,
    PetSpeechBubble,
    bubble_column_for_text,
    bubble_label_size,
    bubble_max_lines,
    bubble_rect_for_anchor,
    elide_bubble_text,
    normalize_bubble_text,
    page_dots,
    page_dwell_ms,
    page_dwells_ms,
    paginate_bubble_text,
    truncate_bubble_text,
)


def _get_app():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout_ms: int = 5000, step_ms: int = 10) -> bool:
    """推进事件循环直到 predicate 成立，返回是否在预算内成立。

    禁止用固定 sleep 猜时序：动画的 ``finished`` 派发时刻随平台/负载漂移
    （macOS CI 上 1ms 动画也可能晚于固定窗口），只轮询目标状态并给宽预算。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(step_ms)
    return bool(predicate())


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


def test_bubble_column_for_text_grows_for_long_text():
    """自适应列宽：≤60 字保持 248px，之后逐步放宽、上限 360px。"""
    assert bubble_column_for_text("") == BUBBLE_TEXT_COLUMN
    assert bubble_column_for_text("字" * 60) == BUBBLE_TEXT_COLUMN

    short = bubble_column_for_text("字" * 61)
    mid = bubble_column_for_text("字" * 110)
    assert BUBBLE_TEXT_COLUMN < short < mid < BUBBLE_TEXT_COLUMN_MAX
    # 160 字到达上限，再长也不超过上限
    assert bubble_column_for_text("字" * 160) == BUBBLE_TEXT_COLUMN_MAX
    assert bubble_column_for_text("字" * 5000) == BUBBLE_TEXT_COLUMN_MAX

    # 长度口径与换行口径一致：Markdown 标记先规整掉再算字数
    assert bubble_column_for_text("**" + "字" * 60 + "**") == BUBBLE_TEXT_COLUMN


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


def test_truncate_bubble_text_limits_and_marks():
    """源头截断纯函数：只按字数硬截断 + 追加标记，未超长原样返回。"""
    assert truncate_bubble_text("", 5) == ""
    assert truncate_bubble_text(None, 5) == ""
    # 边界：恰好等于上限不截断
    assert truncate_bubble_text("字" * 5, 5) == "字" * 5
    assert truncate_bubble_text("字" * 4, 5) == "字" * 4
    # 超长：截到上限再追加标记（标记不计入上限）
    assert truncate_bubble_text("字" * 6, 5) == "字" * 5 + "…"
    assert (
        truncate_bubble_text("字" * 200, 150, "…（全文见聊天窗）")
        == "字" * 150 + "…（全文见聊天窗）"
    )
    # limit <= 0 视为不限长
    assert truncate_bubble_text("字" * 10, 0) == "字" * 10


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


def test_paginate_bubble_text_no_orphan_punctuation_page():
    # 避头尾（kinsoku）：闭标点不允许出现在行首。无禁则时，15 字 + "！"
    # 会让 "！" 被挤到第 4 行行首 → 第 2 页只剩一个 "！"（孤字页）。
    _get_app()
    font = QFont("Arial", 12)
    metrics = QFontMetrics(font)
    char = "测"
    line_w = metrics.horizontalAdvance(char * 5)

    text = char * 15 + "！"
    pages = paginate_bubble_text(metrics, text, line_w, max_lines=3)
    assert len(pages) == 2
    for page in pages:
        for line in page.split("\n"):
            assert not line.startswith("！")
    # 全文无损
    assert "\n".join(pages).replace("\n", "") == text


def test_paginate_bubble_text_rebalances_single_line_last_page():
    # 孤行控制：末页只剩 1 行时从前一页匀一行（3+1 → 2+2）
    _get_app()
    font = QFont("Arial", 12)
    metrics = QFontMetrics(font)
    char = "测"
    line_w = metrics.horizontalAdvance(char * 5)

    pages = paginate_bubble_text(metrics, char * 16, line_w, max_lines=3)
    assert len(pages) == 2
    assert [len(page.split("\n")) for page in pages] == [2, 2]
    assert "\n".join(pages).replace("\n", "") == char * 16


def test_paginate_bubble_text_rebalances_two_line_last_page():
    """孤行控制收紧到 ≤2 行：末页只剩 2 行时同样重平衡（3+2 → 2+3）。"""
    _get_app()
    font = QFont("Arial", 12)
    metrics = QFontMetrics(font)
    char = "测"
    line_w = metrics.horizontalAdvance(char * 5)

    # 40 字 = 8 行，3 行一页 → 3+3+2，末页 2 行也要从上一页匀一行 → 3+2+3
    pages = paginate_bubble_text(metrics, char * 40, line_w, max_lines=3)
    assert [len(page.split("\n")) for page in pages] == [3, 2, 3]
    assert "\n".join(pages).replace("\n", "") == char * 40
    for page in pages:
        assert len(page.split("\n")) <= 3

    # max_lines=6：8 行 = 6+2 → 5+3，末页不再是两行短尾
    pages_6 = paginate_bubble_text(metrics, char * 40, line_w, max_lines=6)
    assert [len(page.split("\n")) for page in pages_6] == [5, 3]
    assert "\n".join(pages_6).replace("\n", "") == char * 40


def test_page_dwell_ms_scales_with_length():
    # 短页钳到下限、满页（约 50 字）落在中段、超长页钳到上限
    assert page_dwell_ms("") == PAGE_DWELL_MIN_MS
    assert page_dwell_ms("短") == PAGE_DWELL_MIN_MS
    mid = page_dwell_ms("字" * 50)
    assert mid == 1200 + 50 * 60
    assert PAGE_DWELL_MIN_MS < mid < PAGE_DWELL_MAX_MS
    assert page_dwell_ms("字" * 500) == PAGE_DWELL_MAX_MS
    # 换行符不计入字数
    assert page_dwell_ms("字\n字") == page_dwell_ms("字字")


def test_page_dwells_ms_adds_return_pause():
    """多页停留表：末页多压一拍「回首页」停顿；单页/空页不加权。"""
    pages = ["字" * 50, "字" * 40, "字" * 30]
    dwells = page_dwells_ms(pages)
    assert len(dwells) == len(pages)
    assert dwells[0] == page_dwell_ms(pages[0])
    assert dwells[1] == page_dwell_ms(pages[1])
    assert dwells[-1] == page_dwell_ms(pages[-1]) + PAGE_RETURN_PAUSE_MS

    assert page_dwells_ms(["字" * 50]) == [page_dwell_ms("字" * 50)]
    assert page_dwells_ms([]) == []


def test_page_dots_rendering():
    assert page_dots(0, 1) == ""
    assert page_dots(0, 3) == "● ○ ○"
    assert page_dots(1, 3) == "○ ● ○"
    assert page_dots(2, 3) == "○ ○ ●"
    # 越界索引钳到最后一页
    assert page_dots(9, 2) == "○ ●"


def test_paged_bubble_uses_adaptive_dwells_and_dots():
    """多页气泡：逐页停留表与页数一致（末页含回首页停顿），页码为圆点。"""
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    text = "主人～DSH开始干活啦，人家会帮您盯着它的～" * 20
    bubble.show_text(text, QRect(0, 0, 120, 120), 40000)
    try:
        assert len(bubble._pages) > 1
        assert len(bubble._page_dwells) == len(bubble._pages)
        assert bubble._page_dwells == page_dwells_ms(bubble._pages)
        # 末页多压一拍「回首页」停顿，其余页按字数自适应
        assert bubble._page_dwells[0] == page_dwell_ms(bubble._pages[0])
        assert bubble._page_dwells[-1] == page_dwell_ms(bubble._pages[-1]) + PAGE_RETURN_PAUSE_MS
        assert bubble._page_indicator.text() == page_dots(0, len(bubble._pages))
    finally:
        bubble.dismiss()


def test_paged_bubble_flip_fades_and_updates_dots(monkeypatch):
    """翻页：淡出→换字→淡入走完后，文本/圆点页码更新且透明度复位。"""
    _get_app()
    # 动画时长压到 1ms，避免测试依赖真实动画时长
    monkeypatch.setattr(speech_bubble_module, "PAGE_FADE_OUT_MS", 1)
    monkeypatch.setattr(speech_bubble_module, "PAGE_FADE_IN_MS", 1)
    bubble = PetSpeechBubble(style_id="classic_top")
    text = "主人～DSH开始干活啦，人家会帮您盯着它的～" * 20
    bubble.show_text(text, QRect(0, 0, 120, 120), 40000)
    try:
        pages = list(bubble._pages)
        assert len(pages) > 1
        bubble._on_page_timeout()
        # 等淡出 → 换字 → 淡入走完：轮询目标状态 + 宽预算，不猜固定时长
        # （macOS CI 上 1ms 动画的 finished 派发可能晚于固定窗口 -> 曾确定性红）。
        assert _wait_until(lambda: bubble._page_fade is None), (
            "翻页动画未在预算内收尾："
            f"_page_fade={bubble._page_fade!r} "
            f"opacity={bubble._label_opacity.opacity() if bubble._label_opacity else None}")
        assert bubble.label.text() == pages[1]
        assert bubble._page_indicator.text() == page_dots(1, len(pages))
        assert bubble._label_opacity is not None
        assert bubble._label_opacity.opacity() == 1.0
    finally:
        bubble.dismiss()


def test_paged_bubble_reset_stops_fade_and_restores_opacity(monkeypatch):
    """翻页动画进行中换内容：动画被打断、透明度复位，不会留下隐形 label。"""
    _get_app()
    # 拉长动画，保证 _reset_paging 落在动画进行中
    monkeypatch.setattr(speech_bubble_module, "PAGE_FADE_OUT_MS", 60000)
    bubble = PetSpeechBubble(style_id="classic_top")
    text = "主人～DSH开始干活啦，人家会帮您盯着它的～" * 20
    bubble.show_text(text, QRect(0, 0, 120, 120), 40000)
    try:
        bubble._on_page_timeout()
        assert bubble._page_fade is not None  # 淡出进行中
        bubble._reset_paging()
        assert bubble._page_fade is None
        assert bubble._label_opacity.opacity() == 1.0
        assert bubble._pages == []
        assert not bubble._page_timer.isActive()
    finally:
        bubble.dismiss()


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
# 文本行必须完整落在 label 矩形内（行尾字被气泡切掉的回归）
# ============================================================================

# 用户截图里的原句（鲸鱼娘女仆模式的 start 台词）及其带空格变体：
# 换行按整型 horizontalAdvance 累加，而 label 宽度过去是用
# QFontMetrics.boundingRect(..., TextWordWrap) 二次排版量的，两者可以差一个字，
# label 比真实行窄时 QLabel（wordWrap=False）就把行尾那个字切在边界上。
CLIPPING_TEXTS = [
    "主人～DSH开始干活啦，人家会帮您盯着它的～",
    "主人～ DSH 开始干活啦，人家会帮您盯着它的～",
    "主人～DSH开始干活啦，人家会帮您盯 着它的～",
    "主人～DSH开始干活啦，人家会帮您盯着它 的～",
    "主人，DSH 正在读取 dsh-pet-indesktop/pet/speech_bubble.py，人家也帮您瞄两眼～",
]


def _rendered_metrics(bubble):
    bubble.label.ensurePolished()
    return QFontMetrics(bubble.label.font())


def _overflow_lines(bubble, lines) -> list[tuple[str, int, int]]:
    """返回 (行文本, 行宽度, label 宽度) 中放不进 label 的行。"""
    metrics = _rendered_metrics(bubble)
    width = bubble.label.width()
    return [
        (line, metrics.horizontalAdvance(line), width)
        for line in lines
        if metrics.horizontalAdvance(line) > width
    ]


def test_displayed_lines_fit_label_rect():
    """回归：真正绘制的每一行都必须放得进 label 矩形。

    过去 label 宽度由 boundingRect(TextWordWrap) 决定，而显示的是
    paginate_bubble_text 按整型 advance 逐字折出来的行；两套折行差一个字时，
    行尾最后一个字被 label 右边界切掉半个（截图里 '盯着它' 的 '它' 只剩一条边，
    下一行从 '的～' 开始）。
    """
    _get_app()
    for text in CLIPPING_TEXTS:
        bubble = PetSpeechBubble(style_id="classic_top")
        bubble.show_text(text, QRect(0, 0, 120, 120), 3200, sticky=True)
        assert bubble.label.wordWrap() is False
        assert _overflow_lines(bubble, bubble.label.text().split("\n")) == [], text
        bubble.dismiss()


def test_every_page_line_fits_label_width():
    """分页文本的每一页、每一行都必须放得进 label 矩形（宽度按所有页定死）。"""
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    # 长度取得足够大：任何平台的字体都会超过单页 6 行上限（不赌字体度量），
    # 保证这条用例在 Windows/Linux/macOS CI 上都真的走到分页分支。
    text = "主人～DSH开始干活啦，人家会帮您盯着它的～" * 20
    bubble.show_text(text, QRect(0, 0, 120, 120), 40000)
    assert bubble._pages, f"长文本应进入分页展示（{len(text)} 字）"
    assert len(bubble._pages) > 1

    for page in bubble._pages:
        assert _overflow_lines(bubble, page.split("\n")) == [], page


def test_long_text_bubble_uses_wider_column():
    """长文本走自适应列宽：label 比基础列宽更宽，但不超过 360px 上限。

    短文本（CLIPPING_TEXTS 那一组）仍走原有 248px 列宽，视觉不回归。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    text = "主人～DSH开始干活啦，人家会帮您盯着它的～" * 10
    bubble.show_text(text, QRect(0, 0, 120, 120), 40000)
    try:
        assert bubble.label.width() > BUBBLE_TEXT_COLUMN
        assert bubble.label.width() <= BUBBLE_TEXT_COLUMN_MAX
        assert bubble._pages
        for page in bubble._pages:
            assert _overflow_lines(bubble, page.split("\n")) == [], page
    finally:
        bubble.dismiss()

    # 短文本：列宽仍然是基础 248px（气泡整体宽度也随之不变）
    short = PetSpeechBubble(style_id="classic_top")
    short.show_text("短文本", QRect(0, 0, 120, 120), 3200, sticky=True)
    try:
        assert short.label.width() <= BUBBLE_TEXT_COLUMN
    finally:
        short.dismiss()


def test_wide_bubble_column_clamped_to_narrow_capture_host():
    """直播捕获子模式：可用区=主窗矩形，长文本列宽必须收进主窗。

    列宽上限 360px + 左右内边距会让气泡比 320px 宽的主窗还宽，子控件越界
    会被裁掉；_column_for_text 按可用区收敛列宽后，气泡仍完整落在主窗内。
    """
    _get_app()
    host = QWidget()
    host.setGeometry(0, 0, 320, 320)
    bubble = PetSpeechBubble(style_id="classic_top")
    try:
        bubble.set_capture_compat(True, host)
        text = "主人～DSH开始干活啦，人家会帮您盯着它的～" * 10
        bubble.show_text(text, host.geometry(), 40000)
        assert bubble.width() <= host.width()
        assert host.rect().contains(bubble.geometry())
        for page in bubble._pages:
            assert _overflow_lines(bubble, page.split("\n")) == [], page
    finally:
        bubble.dismiss()
        bubble.set_capture_compat(False)
        host.close()
        bubble.deleteLater()
        host.deleteLater()
        _get_app().processEvents()


def test_bubble_label_stays_inside_painted_surface():
    """文本 label 必须落在气泡绘制区内（右侧留白足够，字不会被边框压住）。"""
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    bubble.show_text(
        "主人～DSH开始干活啦，人家会帮您盯着它的～",
        QRect(0, 0, 120, 120),
        3200,
        sticky=True,
    )
    surface = bubble._surface_rect
    assert bubble.label.geometry().left() >= surface.left()
    assert bubble.label.geometry().right() <= surface.right()
    assert bubble.label.geometry().top() >= surface.top()
    assert bubble.label.geometry().bottom() <= surface.bottom()


def test_bubble_label_size_measures_painted_lines():
    """bubble_label_size 的宽度取“最长的那一行”，高度取“行数最多的一页”。

    用假的 metrics 固定度量，避免依赖平台字体：宽度必须是行宽 + 余量（而不是
    二次排版的结果），高度按所有页里最多行数算，翻页时 label 不会变。
    """
    class _StubMetrics:
        def __init__(self, per_char: int, spacing: int):
            self.per_char = per_char
            self.spacing = spacing

        def horizontalAdvance(self, text: str) -> int:
            return self.per_char * len(text)

        def lineSpacing(self) -> int:
            return self.spacing

    metrics = _StubMetrics(per_char=40, spacing=16)
    size = bubble_label_size(metrics, ["一二三\n四五", "六七"])
    assert size.width() == 3 * 40 + BUBBLE_TEXT_SLACK
    assert size.height() == 2 * 16 + 2

    # 宽度不超过内容列上限
    wide = bubble_label_size(_StubMetrics(per_char=20, spacing=16), ["一" * 30])
    assert wide.width() <= BUBBLE_TEXT_COLUMN

    # 短文本仍受最小尺寸约束
    tiny = bubble_label_size(metrics, [])
    assert tiny.width() == 96
    assert tiny.height() == 20


def test_bubble_rect_for_anchor_wide_bubble_stays_on_screen():
    """更宽的气泡（列宽上限 360 → 气泡约 386px）定位仍正确。

    正常屏幕：优先选到「避开桌宠」的位置；窄屏放不下时退化为贴边钳位，
    但结果仍必须完整落在可用区内（不允许气泡越出屏幕/主窗被裁）。
    """
    available = QRect(0, 0, 1920, 1080)
    anchor = QRect(900, 500, 120, 120)
    rect = bubble_rect_for_anchor(anchor, QSize(386, 220), available, "top")
    assert available.contains(rect)
    assert not rect.intersects(anchor)

    narrow = QRect(0, 0, 320, 300)
    packed_anchor = QRect(80, 80, 160, 160)
    clamped = bubble_rect_for_anchor(packed_anchor, QSize(280, 100), narrow, "top")
    assert narrow.contains(clamped)


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


def test_interactive_long_option_stays_inside_bubble():
    """选择/审批里的超长选项不能把按钮文字绘制到气泡外。"""
    _get_app()
    bubble = PetSpeechBubble()
    bubble.show_text(
        "请选择：",
        QRect(0, 0, 120, 120),
        3200,
        sticky=True,
        buttons=[("这是一个非常非常长的选择项文本，不能超出气泡边界", lambda: None)],
    )
    button = bubble._interactive_buttons[0]
    # 交互气泡的内容列最大宽度为 248，按钮还需留出两侧内边距。
    assert button.width() <= 220
    row = bubble._button_row
    # button 的坐标相对于按钮行；按钮行本身由气泡内容边距约束。
    assert row.geometry().left() >= 13
    assert row.geometry().right() <= bubble.width() - 13
    assert button.geometry().left() >= 0
    assert button.geometry().right() <= row.width()



def test_lyric_width_lock_reuses_first_line_column():
    """歌词锁宽：同首歌后续长句必须沿用第一句的列宽，不逐句改宽。

    回归：锁宽分支过去只拿 max(当句列宽, TITLE_FIRST_COLUMN) 做下限，
    长句仍会撑宽气泡（15 字→248px、80 字→264px），每换一句歌词气泡
    就横向伸缩一次，实机看起来一直在跳。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    anchor = QRect(0, 0, 120, 120)
    # 第一句（新歌，未锁宽）：记下这句的列宽
    bubble.show_text("短句歌词", anchor, 5000, subtitle="歌名", title_first=True, width_locked=False)
    first_width = bubble.label.width()
    # 同首歌后续长句（锁宽）：必须沿用第一句的宽度
    bubble.show_text("这是一句明显更长的歌词" * 6, anchor, 5000, subtitle="歌名", title_first=True, width_locked=True)
    assert bubble.label.width() == first_width, (
        f"锁宽后长句不应改宽：首句 {first_width}px vs 长句 {bubble.label.width()}px"
    )
    bubble.dismiss()


def test_lyric_height_lock_ratchet_keeps_top_edge_stable():
    """歌词锁高：换句后行数回落时气泡高度不许缩回去（顶边不跳）。

    回归：锁宽只锁了列宽，高度仍按当句行数算——长句（2 行）→短句（1 行）
    时气泡底边锚着鱼头顶不动、顶边往下掉一行，每换一句歌词气泡就上下
    跳一次。现在行数锁只单向往大涨，回落时保持已涨到的行数。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    anchor = QRect(0, 0, 120, 120)
    # 新歌首句（未锁宽）：一行短歌词
    bubble.show_text("短句歌词", anchor, 5000, subtitle="歌名", title_first=True, width_locked=False)
    one_line_height = bubble.label.height()
    # 换成长句（锁宽）：折成多行，高度涨上去
    bubble.show_text("这是一句明显更长的歌词" * 6, anchor, 5000, subtitle="歌名", title_first=True, width_locked=True)
    multi_line_height = bubble.label.height()
    assert multi_line_height > one_line_height
    # 再换回短句（锁宽）：高度必须保持涨到的值，不许缩回一行高
    bubble.show_text("短句歌词", anchor, 5000, subtitle="歌名", title_first=True, width_locked=True)
    assert bubble.label.height() == multi_line_height, (
        f"锁高后短句不应缩高：多行 {multi_line_height}px vs 短句 {bubble.label.height()}px"
    )
    bubble.dismiss()


def test_bubble_move_glides_after_first_show():
    """气泡二次定位走滑动动画而非瞬移（首次显示仍直接落位）。

    歌词气泡每秒重放时锚点有像素级修正，瞬移看起来"一帧帧跳"；
    改为 260ms 缓动滑动后呈现"被推动"的流畅感。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    anchor = QRect(100, 100, 120, 120)
    bubble.show_text("第一句歌词", anchor, 5000)
    first_pos = bubble.pos()
    assert bubble._pos_anim is None or bubble._pos_anim.state() == QPropertyAnimation.State.Stopped

    bubble.show_text("第二句歌词来了", anchor.translated(60, 0), 5000)
    anim = bubble._pos_anim
    assert anim is not None, "二次定位应走滑动动画"
    assert anim.state() == QPropertyAnimation.State.Running
    assert anim.startValue() == first_pos
    assert anim.endValue() != first_pos
    # 动画播完落点必须精确到达目标（无累积误差）
    anim.setCurrentTime(anim.duration())
    assert bubble.pos() == anim.endValue()
    bubble.dismiss()


def test_bubble_reposition_is_direct_not_animated():
    """鱼移动触发的跟随（reposition）必须直移，不走滑动动画。

    跟随场景每帧来新位置，走动画会不停重启导致气泡永远拖着延迟尾巴
    （实机用户反馈"拖动时气泡跟不上"）；只有歌词节拍这类离散修正
    才走滑动。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    bubble.show_text("一句歌词", QRect(100, 100, 120, 120), 5000)
    if bubble._pos_anim is not None:
        bubble._pos_anim.stop()
    before = bubble.pos()
    bubble.reposition(QRect(300, 100, 120, 120))
    assert bubble._pos_anim is None or bubble._pos_anim.state() == QPropertyAnimation.State.Stopped
    assert bubble.pos() != before, "reposition 应立即落位"
    bubble.dismiss()


def test_bubble_tail_computed_for_target_position_not_current():
    """滑动期间算尾巴几何必须用目标落点坐标系，不是控件当前位置。

    回归（实审 P1-1）：_place 先起滑动动画再算 surface 几何，
    mapFromGlobal 用的是动画前的旧位置，尾巴永久指错（实测偏 108px）。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    anchor = QRect(500, 500, 120, 120)
    bubble.show_text("第一句歌词", anchor, 5000)
    if bubble._pos_anim is not None:
        bubble._pos_anim.stop()
    # 锚点右移 60px，再显示触发滑动
    bubble.show_text("第二句歌词", anchor.translated(60, 0), 5000)
    anim = bubble._pos_anim
    assert anim is not None
    anim.setCurrentTime(anim.duration())  # 直接到终点
    # 尾巴尖 x 应等于锚点中心在气泡局部坐标里的位置（夹在 20..w-20）
    expected = min(max(anchor.translated(60, 0).center().x() - bubble.pos().x(), 20),
                   bubble.width() - 20)
    assert abs(bubble._tail_tip.x() - expected) < 1.0, (
        f"尾巴应按目标落点算：期望 {expected}，实际 {bubble._tail_tip.x()}"
    )
    bubble.dismiss()


def test_bubble_reshow_after_dismiss_not_dragged_back():
    """dismiss 后再显示：残留动画不得把控件拽回旧落点（实审 P2-2）。"""
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    bubble.show_text("第一句", QRect(100, 100, 120, 120), 5000)
    bubble.show_text("第二句", QRect(500, 100, 120, 120), 5000)  # 起动画去 B
    bubble.dismiss()
    # 隐藏中残留动画仍在跑；重显示必须停表并直接落位
    bubble.show_text("第三句", QRect(100, 600, 120, 120), 5000)
    assert bubble._pos_anim.state() == QPropertyAnimation.State.Stopped
    from pet.speech_bubble_text import bubble_rect_for_anchor
    # 位置就是 C 的落点，不再回 B
    QTest.qWait(350)
    assert bubble._pos_anim.state() == QPropertyAnimation.State.Stopped
    bubble.dismiss()


# ---------------------------------------------------------------- F-PERF P2：跟随平移不空转重建/重绘
class RepaintCountingBubble(PetSpeechBubble):
    """真实气泡 + ``update()`` 计数（不是替身：几何/绘制全走产品实现）。

    跟随场景 30Hz 每拍一次 ``reposition``，若每次都重建 QPainterPath 并整窗
    重绘，气泡窗口每秒要画 30 遍；计数是本类唯一加法。
    """

    def __init__(self, **kwargs):
        # set_style 在 super().__init__ 内就会调一次 update()：计数字段必须先就位
        self.updates = 0
        super().__init__(**kwargs)

    def update(self, *args):  # noqa: D102 - 计数包装，转发真实实现
        self.updates += 1
        super().update(*args)


def _visible_bubble_with_text(text="跟随几何", anchor=None):
    bubble = RepaintCountingBubble(style_id="classic_top")
    bubble.show_text(text, anchor or QRect(300, 300, 120, 120), 5000)
    return bubble


def test_follow_translation_reuses_surface_path_without_repaint():
    """纯平移（锚点与窗口同步位移）：几何逐位不变 → 不重建路径、不重绘。"""
    _get_app()
    anchor = QRect(300, 300, 120, 120)
    bubble = _visible_bubble_with_text(anchor=anchor)
    path_before = bubble._surface_path
    main_before = bubble._main_bubble_path
    tail_before = (bubble._tail_base, bubble._tail_tip)
    local_before = bubble.rect()
    updates_before = bubble.updates
    pos_before = bubble.pos()

    bubble.reposition(anchor.translated(6, 3))

    assert bubble.rect() == local_before, "前提：窗口尺寸不变（纯平移）"
    assert bubble.pos() != pos_before, "气泡窗口必须真的跟着平移"
    assert bubble._surface_path is path_before, "纯平移不得重建表面路径"
    assert bubble._main_bubble_path is main_before
    assert bubble._tail_base == tail_before[0] and bubble._tail_tip == tail_before[1]
    assert bubble.updates == updates_before, (
        f"纯平移不得整窗重绘（update 次数 {updates_before} → {bubble.updates}）"
    )
    bubble.dismiss()


def test_follow_reposition_is_skipped_when_anchor_unchanged():
    """锚点整数矩形未变：原地不动的跟随拍不动窗、不重绘。"""
    _get_app()
    anchor = QRect(300, 300, 120, 120)
    bubble = _visible_bubble_with_text(anchor=anchor)
    bubble.reposition(anchor.translated(5, 0))
    updates_before = bubble.updates
    pos_before = bubble.pos()

    bubble.reposition(anchor.translated(5, 0))    # 同一锚点重复跟随拍

    assert bubble.pos() == pos_before
    assert bubble.updates == updates_before, "锚点未变不得重绘"
    bubble.dismiss()


def test_follow_rebuilds_and_repaints_when_geometry_changes():
    """几何真变（尺寸/相对位置变）仍必须重建路径 + 重绘。"""
    _get_app()
    anchor = QRect(300, 300, 120, 120)
    bubble = _visible_bubble_with_text(anchor=anchor)
    bubble.reposition(anchor.translated(4, 0))
    path_before = bubble._surface_path
    updates_before = bubble.updates

    # 尺寸变化（缩放路径：reflow 前先改内容尺寸）→ 局部矩形变 → 几何变
    bubble.resize(bubble.width() + 40, bubble.height() + 20)
    bubble.reposition(anchor.translated(4, 0))

    assert bubble._surface_path is not path_before, "尺寸变化必须重建表面路径"
    assert bubble._surface_path.boundingRect().width() > path_before.boundingRect().width()
    assert bubble.updates > updates_before, "几何变化必须重绘"
    bubble.dismiss()


def test_breath_bubble_follow_translation_skips_repaint():
    """吐气水泡形态同口径：纯平移不重建几何、不重绘。"""
    _get_app()
    anchor = QRect(320, 320, 120, 120)
    bubble = RepaintCountingBubble(style_id="breath_bubble")
    bubble.show_text("跟随几何", anchor, 5000)
    paths_before = (bubble._surface_path, bubble._main_bubble_path)
    updates_before = bubble.updates
    pos_before = bubble.pos()

    bubble.reposition(anchor.translated(5, 2))

    assert bubble.pos() != pos_before, "气泡窗口必须真的跟着平移"
    assert bubble._surface_path is paths_before[0]
    assert bubble._main_bubble_path is paths_before[1]
    assert bubble.updates == updates_before, "纯平移不得整窗重绘"
    bubble.dismiss()


def test_image_bubble_same_geometry_still_repaints_on_new_pixmap():
    """配图气泡：同锚点同尺寸换图仍必须重建 + 重绘（配图由父控件自绘）。

    几何签名把配图内容（``QPixmap.cacheKey()`` + label 几何）算在内就是为这条：
    纯文字由 QLabel 自绘，换图却要父控件 paintEvent 重画，漏签会让气泡永远停在
    旧图。
    """
    from PySide6.QtGui import QColor, QPixmap

    _get_app()
    anchor = QRect(300, 300, 120, 120)
    first = QPixmap(60, 40)
    first.fill(QColor("#336699"))
    second = QPixmap(60, 40)
    second.fill(QColor("#993366"))          # 同尺寸、不同内容
    assert first.cacheKey() != second.cacheKey()

    bubble = RepaintCountingBubble(style_id="classic_top")
    assert bubble.show_image("first.png", anchor, 5000, pet_scale=1.0, pixmap=first)
    path_before = bubble._surface_path
    local_before = bubble.rect()
    updates_before = bubble.updates

    assert bubble.show_image("second.png", anchor, 5000, pet_scale=1.0, pixmap=second)

    assert bubble.rect() == local_before, "前提：换图不改变气泡尺寸（同尺寸配图）"
    assert bubble._surface_path is not path_before, "换图必须重建表面路径"
    assert bubble.updates > updates_before, "换图必须重绘"
    bubble.dismiss()


def test_bubble_follows_host_on_top_flag():
    """宿主「桌面置顶」关闭时气泡不得再置顶——否则宠物被窗口挡住、
    气泡却浮在最上层（4.2.1 发布视频评论区反馈）。旗标变更后可见性必须保住。"""
    _get_app()
    bubble = PetSpeechBubble()
    assert bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint  # 默认置顶

    bubble.show()
    QTest.qWait(10)
    assert bubble.isVisible()

    bubble.set_host_on_top(False)
    assert not (bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert bubble.isVisible(), "setWindowFlags 隐窗后必须恢复显示"

    bubble.set_host_on_top(True)
    assert bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert bubble.isVisible()
    bubble.close()


def test_pet_window_on_top_propagates_to_bubble(tmp_path):
    """接线：PetWindow.set_on_top 必须同步气泡；初始 on_top=False 时气泡出生即不置顶。"""
    from pet.config import Config
    from tests.pet_window_fakes import make_pet_window

    _get_app()
    win = make_pet_window(tmp_path)
    assert win._speech_bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    win.set_on_top(False)
    assert not (win._speech_bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    win.set_on_top(True)
    assert win._speech_bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    win.close()

    cfg = Config(str(tmp_path / "cfg_off_top.json"))
    cfg.set("on_top", False)
    win2 = make_pet_window(tmp_path, runtime_id="off-top-pid2-deadbeef", config=cfg)
    assert not (win2._speech_bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    win2.close()


_WORD_JOINER = "\u2060"


def _body_metrics():
    _get_app()
    font = QFont()
    font.setPixelSize(13)
    return QFontMetrics(font)


def _visible(text: str) -> str:
    return str(text or "").replace(_WORD_JOINER, "")


def _newline_at(wrapped: str, chunk: str) -> int | None:
    """换行落在 ``chunk`` 内部时返回偏移；整段都在一行则返回 -1；找不到返回 None。"""
    visible = _visible(wrapped)
    flat = visible.replace("\n", "")
    start = flat.find(chunk)
    if start < 0:
        return None
    count = 0
    for char in visible:
        if char == "\n":
            if start < count < start + len(chunk):
                return count - start
            continue
        count += 1
    return -1


def _metric_widths(metrics: QFontMetrics, text: str) -> list[int]:
    """候选宽度全部来自真实字形 advance，不写死像素。"""
    widths = {metrics.horizontalAdvance(text[:index]) for index in range(1, len(text) + 1)}
    for count in range(1, 36):
        widths.add(metrics.horizontalAdvance("测" * count))
    return sorted(width for width in widths if width > 0)


def test_balance_chunks_stay_together_when_they_fit():
    """当前逐字换行会在「赠送」内部、以及「余额」和「¥5.99」之间断开。

    宽度取真实 advance。金额短语自己放得下时，整段必须留在同一行。
    """
    from pet.balance import format_balance_display

    metrics = _body_metrics()
    plain = "测" * 12 + "余额 ¥5.99（充值 ¥10.00 / 赠送 ¥2.34）"
    chunks = ("余额 ¥5.99", "充值 ¥10.00", "赠送 ¥2.34")
    gift_splits: list[int] = []
    balance_splits: list[int] = []
    for width in _metric_widths(metrics, plain):
        wrapped = "\n".join(paginate_bubble_text(metrics, plain, width, max_lines=8))
        if metrics.horizontalAdvance("赠送 ¥2.34") <= width and _newline_at(wrapped, "赠送") == 1:
            gift_splits.append(width)
        if metrics.horizontalAdvance("余额 ¥5.99") <= width and _newline_at(wrapped, "余额 ¥") in (2, 3):
            balance_splits.append(width)
    assert gift_splits, "应能从字体度量推出「赠送」被拆开的宽度"
    assert balance_splits, "应能从字体度量推出「余额」和金额被拆开的宽度"

    shown = format_balance_display(plain)
    assert _visible(shown) == plain
    for width in _metric_widths(metrics, plain):
        pages = paginate_bubble_text(metrics, shown, width, max_lines=8)
        wrapped = "\n".join(pages)
        assert _visible(wrapped).replace("\n", "") == plain
        for chunk in chunks:
            if metrics.horizontalAdvance(chunk) <= width:
                assert _newline_at(wrapped, chunk) == -1, (chunk, width, pages)
        glyph = max(metrics.horizontalAdvance(char) for char in plain)
        if width >= glyph:
            for line in _visible(wrapped).split("\n"):
                if line:
                    assert metrics.horizontalAdvance(line) <= width, (line, width)


def test_joined_runs_keep_pagination_elision_and_explicit_breaks():
    """连接段参与分页、省略、标点和强制换行；放得下的段不拆，字符顺序保留。"""
    from pet.speech_bubble_text import LINE_START_FORBIDDEN

    metrics = _body_metrics()
    gift = "赠送 ¥2.34"
    balance = "余额 ¥5.99"
    run = _WORD_JOINER.join(gift)
    balance_run = _WORD_JOINER.join(balance)
    width = metrics.horizontalAdvance(gift + "测")
    assert metrics.horizontalAdvance(gift) <= width

    text = "测" * 40 + run + "！" + balance_run
    pages = paginate_bubble_text(metrics, text, width, max_lines=3)
    wrapped = "\n".join(pages)
    visible = _visible(wrapped)
    assert visible.replace("\n", "") == "测" * 40 + gift + "！" + balance
    assert _newline_at(wrapped, gift) == -1
    assert _newline_at(wrapped, balance) == -1
    for page in pages:
        lines = [line for line in _visible(page).split("\n") if line]
        assert lines, page
        assert not (len(lines) == 1 and lines[0] in LINE_START_FORBIDDEN), page

    # 省略只留前几行。两个金额段都放在会被留下的位置，后面堆长尾巴迫使出现省略号。
    crowded = "测" * 4 + run + "！" + balance_run + "测" * 48
    kept = elide_bubble_text(metrics, crowded, width, max_lines=3)
    assert len(kept.split("\n")) <= 3
    assert _newline_at(kept, gift) == -1
    assert _newline_at(kept, balance) == -1
    assert ("…" in kept) or ("..." in kept)
    # 省略号贴着连接段：整段留下，或整段被省略，不能只剩「赠」。
    boundary = "测" * 10 + run + "！" + "测" * 24
    cut = elide_bubble_text(metrics, boundary, width, max_lines=2)
    assert len(cut.split("\n")) <= 2
    flat_cut = _visible(cut).replace("\n", "")
    assert ("赠" in flat_cut) == (gift in flat_cut)
    assert ("送" in flat_cut) == (gift in flat_cut)
    if gift in flat_cut:
        assert _newline_at(cut, gift) == -1

    broken = _WORD_JOINER.join("余额 ¥1.00") + "\n" + _WORD_JOINER.join("赠送 ¥2.00")
    wide = metrics.horizontalAdvance("余额 ¥1.00赠送 ¥2.00")
    paged = paginate_bubble_text(metrics, broken, wide, max_lines=3, keep_breaks=True)
    assert [line for line in _visible(paged[0]).split("\n")] == ["余额 ¥1.00", "赠送 ¥2.00"]

    overlong = "前" + _WORD_JOINER.join("余额 ¥1234567890123.99") + "后"
    narrow = max(metrics.horizontalAdvance(char) for char in _visible(overlong))
    fitted = paginate_bubble_text(metrics, overlong, narrow, max_lines=4)
    assert _visible("\n".join(fitted)).replace("\n", "") == _visible(overlong)
    for line in _visible("\n".join(fitted)).split("\n"):
        assert line
        assert metrics.horizontalAdvance(line) <= narrow
    tighter = max(1, metrics.horizontalAdvance("余") - 1)
    squeezed = paginate_bubble_text(metrics, overlong, tighter, max_lines=6)
    assert _visible("\n".join(squeezed)).replace("\n", "") == _visible(overlong)
    assert all(_visible(page).strip() for page in squeezed)


def test_overlong_joined_token_wraps_at_start_and_after_explicit_break():
    from pet.balance import format_balance_display

    metrics = _body_metrics()
    amount = "余额 ¥123456789012345678901234567890.99（充值 ¥10.00）"
    width = metrics.horizontalAdvance("余额 ¥1234")
    for prefix in ("", "前文\n"):
        text = prefix + format_balance_display(amount)
        pages = paginate_bubble_text(metrics, text, width, keep_breaks=True)
        lines = _visible("\n".join(pages)).split("\n")
        assert "".join(lines) == (prefix + amount).replace("\n", "")
        assert all(lines)
        assert all(metrics.horizontalAdvance(line) <= width for line in lines)


def test_word_joiner_does_not_change_budget_or_normalization():
    plain = "字" * 40
    marked = _WORD_JOINER.join(plain)
    assert _WORD_JOINER in normalize_bubble_text(marked)
    assert normalize_bubble_text(marked).replace(_WORD_JOINER, "") == plain
    assert normalize_bubble_text("余\u2060额\u2060 \u2060¥1.00") == "余\u2060额\u2060 \u2060¥1.00"
    assert bubble_max_lines(marked) == bubble_max_lines(plain) == 3
    column_text = "字" * 60
    assert bubble_column_for_text(_WORD_JOINER.join(column_text)) == bubble_column_for_text(column_text)
    dwell = "字" * 50
    assert page_dwell_ms(_WORD_JOINER.join(dwell)) == page_dwell_ms(dwell)
    assert page_dwell_ms("字\n字") == page_dwell_ms("字字")
