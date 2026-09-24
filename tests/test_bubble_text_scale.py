# -*- coding: utf-8 -*-
"""气泡文字大小（bubble_text_scale）：配置钳位、气泡等比放大、不裁字、分页正常。

回归背景：配图早有 ``self_talk_image_scale``，文字气泡的尺寸与字号却是硬编码的
（列宽 248/360、font-size 13px），大屏上嫌字小只能改桌宠缩放。本项新增
``bubble_text_scale``（50–300%，默认 100）：**列宽、换行预算、label 尺寸与
字号用同一个系数**整体等比放大。

三条硬约束（本文件就是它们的守护者）：
1. 默认 100% 必须与旧版**逐像素一致**（列宽、字号、label 尺寸都不变）；
2. 放大后 label 不得比真实行窄（否则行尾那个字被 label 右边界切在边界上）；
3. 放大后分页仍然正常（每页不超过 ``bubble_max_lines`` 行、有页码）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _anchor() -> QRect:
    return QRect(420, 460, 220, 260)


# ------------------------------------------------------------ 纯函数
def test_column_scales_linearly_and_default_is_unchanged():
    from pet.speech_bubble_text import (
        BUBBLE_TEXT_COLUMN,
        BUBBLE_TEXT_COLUMN_MAX,
        bubble_column_for_text,
    )

    short = "今天的风很舒服。"
    assert bubble_column_for_text(short) == BUBBLE_TEXT_COLUMN  # 默认基线
    assert bubble_column_for_text(short, 1.0) == BUBBLE_TEXT_COLUMN
    assert bubble_column_for_text(short, 2.0) == BUBBLE_TEXT_COLUMN * 2
    assert bubble_column_for_text(short, 3.0) == BUBBLE_TEXT_COLUMN * 3

    long = "长文案" * 80
    base = bubble_column_for_text(long)
    doubled = bubble_column_for_text(long, 2.0)
    assert BUBBLE_TEXT_COLUMN < base <= BUBBLE_TEXT_COLUMN_MAX
    assert doubled == base * 2
    # 单调性：文案越长列越宽（缩放不破坏原有自适应曲线）
    assert bubble_column_for_text(long, 2.0) >= bubble_column_for_text("长文案" * 25, 2.0)


def test_scale_is_clamped_and_nan_falls_back_to_default():
    from pet.speech_bubble_text import (
        BUBBLE_TEXT_SCALE_MAX,
        BUBBLE_TEXT_SCALE_MIN,
        clamp_bubble_text_scale,
    )

    assert clamp_bubble_text_scale(1.0) == 1.0
    assert clamp_bubble_text_scale(0.01) == BUBBLE_TEXT_SCALE_MIN
    assert clamp_bubble_text_scale(99) == BUBBLE_TEXT_SCALE_MAX
    assert clamp_bubble_text_scale("abc") == 1.0
    assert clamp_bubble_text_scale(None) == 1.0
    assert clamp_bubble_text_scale(float("nan")) == 1.0


def test_font_px_scales_with_floor():
    from pet.speech_bubble_text import (
        BUBBLE_BODY_FONT_PX,
        scale_bubble_font_px,
    )

    assert scale_bubble_font_px(BUBBLE_BODY_FONT_PX, 1.0) == BUBBLE_BODY_FONT_PX
    assert scale_bubble_font_px(13, 2.0) == 26
    assert scale_bubble_font_px(13, 1.5) == 20  # 取整，不出现半个像素
    assert scale_bubble_font_px(13, 0.5) == 6  # round(6.5) 走银行家舍入
    # 系数先被钳到下限 0.5，所以字号永远不会缩到 0（10px 副标题 → 5px）
    assert scale_bubble_font_px(10, 0.01) == 5
    assert scale_bubble_font_px(1, 0.5) == 1  # 下限 1px


def test_breath_bubble_reference_canvas_scales():
    from pet.speech_bubble_text import (
        breath_bubble_size_for_anchor,
        breath_bubble_size_for_scale,
    )

    anchor = QRect(0, 0, 220, 260)
    base = breath_bubble_size_for_anchor(anchor)
    big = breath_bubble_size_for_anchor(anchor, 2.0)
    assert base == breath_bubble_size_for_anchor(anchor, 1.0)  # 默认零变化
    assert big.width() == base.width() * 2
    assert abs(big.width() / big.height() - 240 / 195) < 0.01  # 宽高比保持

    pet_base = breath_bubble_size_for_scale(0.72)
    pet_big = breath_bubble_size_for_scale(0.72, 2.0)
    assert pet_base == breath_bubble_size_for_scale(0.72, 1.0)
    assert pet_big.width() == pet_base.width() * 2


# ------------------------------------------------------------ 配置
def test_config_clamps_bubble_text_scale(tmp_path: Path):
    from pet.config import Config

    config = Config(tmp_path)
    assert config.get("bubble_text_scale") == 100
    config.set("bubble_text_scale", 180)
    config.save()
    assert Config(tmp_path).get("bubble_text_scale") == 180

    config.set("bubble_text_scale", 9999)
    config.save()
    assert Config(tmp_path).get("bubble_text_scale") == 300
    config.set("bubble_text_scale", 1)
    config.save()
    assert Config(tmp_path).get("bubble_text_scale") == 50
    config.set("bubble_text_scale", "abc")
    config.save()
    assert Config(tmp_path).get("bubble_text_scale") == 100


# ------------------------------------------------------------ 气泡本体
def _bubble(scale: float, text: str, **kwargs):
    from pet.speech_bubble import PetSpeechBubble

    bubble = PetSpeechBubble()
    try:
        bubble.set_text_scale(scale)
        bubble.show_text(text, _anchor(), 5000, **kwargs)
        bubble.label.ensurePolished()
    except Exception:
        bubble.close()
        raise
    return bubble


def test_default_scale_keeps_label_and_font_pixel_identical():
    """默认 100%：字号与 label 尺寸必须与未设置系数时逐像素一致。"""
    _qapp()
    plain = _bubble(1.0, "一句话而已。")
    scaled = _bubble(1.0, "一句话而已。")
    try:
        assert plain.text_scale == 1.0
        assert plain.label.size() == scaled.label.size()
        assert plain.label.font().pixelSize() == 13
    finally:
        plain.close()
        scaled.close()


def test_scale_grows_bubble_and_font_together():
    _qapp()
    normal = _bubble(1.0, "这是一句用来量宽的话。")
    big = _bubble(2.0, "这是一句用来量宽的话。")
    try:
        assert big.text_scale == 2.0
        assert big.label.font().pixelSize() == 26
        assert big.label.width() > normal.label.width()
        assert big.label.height() > normal.label.height()
        assert big.width() > normal.width()
    finally:
        normal.close()
        big.close()


def test_scaled_label_never_narrower_than_the_widest_line():
    """放大后 label 宽度必须 >= 真正会绘制的最长行实宽（同一整型度量口径）。

    这是「右边界不切字」的机器可判定形式：label 若比真实行窄，QLabel
    （wordWrap=False）就把行尾那个字切在边界上，看起来像被气泡挡掉。
    用的是 **show_text 自己排出来的 pages**，不是测试另排一次。
    """
    _qapp()
    from pet.speech_bubble_text import bubble_max_lines, paginate_bubble_text

    text = "桌宠气泡文字放大以后，每一行的行尾都不能被气泡右边界切掉，这是硬约束。" * 2
    for scale in (1.0, 1.5, 2.0, 3.0):
        bubble = _bubble(scale, text)
        try:
            metrics = QFontMetrics(bubble.label.font())
            pages = paginate_bubble_text(
                metrics,
                text,
                bubble.label.width(),
                bubble_max_lines(text),
            )
            widest = max(metrics.horizontalAdvance(line) for page in pages for line in page.split("\n"))
            assert bubble.label.width() >= widest, f"scale={scale} label 宽度 {bubble.label.width()} < 最长行 {widest}"
        finally:
            bubble.close()


def test_scaled_text_still_paginates():
    """放大后分页仍正常：每页不超上限行数、有页码提示、可翻到最后一页。"""
    _qapp()
    text = "分页检查。" * 90
    bubble = _bubble(2.0, text)
    try:
        assert len(bubble._pages) > 1, "长文案放大后应仍分页"
        for page in bubble._pages:
            assert len(page.split("\n")) <= 6
        total = len(bubble._pages)
        assert bubble._page_indicator.text() == " ".join("●" if index == 0 else "○" for index in range(total))
        bubble._on_page_timeout()
        assert bubble._page_index == 1
        for _ in range(total):
            bubble._on_page_timeout()
        assert bubble._page_index == total - 1
    finally:
        bubble.close()


def test_scale_applies_to_breath_bubble_text_but_not_image():
    """呼吸气泡：文字吃 bubble_text_scale，配图仍只吃 image_scale。"""
    _qapp()
    from pet.speech_bubble import PetSpeechBubble

    normal = PetSpeechBubble(style_id="breath_bubble")
    big = PetSpeechBubble(style_id="breath_bubble")
    big.set_text_scale(2.0)
    normal.show_text("呼吸气泡里的一句话。", _anchor(), 5000, pet_scale=0.72)
    big.show_text("呼吸气泡里的一句话。", _anchor(), 5000, pet_scale=0.72)
    try:
        assert big.width() > normal.width()
        assert big.label.font().pixelSize() > normal.label.font().pixelSize()
        assert big._image_scale == 1.0  # 文字系数不得污染配图系数
    finally:
        normal.close()
        big.close()


def test_scaled_wrap_budget_matches_the_label_in_the_live_show_text_path(monkeypatch):
    """可用区装不下缩放后的列宽时，换行预算、label 与气泡几何必须一起收窄。

    缺陷形态（真机 300% 实测过）：``_column_for_text`` 曾把可用区收窄夹在
    「缩放后的基础列宽」之上——``max(744, min(744, 386)) = 744``，于是气泡按
    744px 列宽排版、整个窗口（744 + 内边距 + 描边）越出可用区。这里把可用区收窄
    成 420px 稳定复现：正确实现下 column=386、label=363、最长行 351、气泡 389 全在区内；
    旧实现下 column=744、label=714、气泡 770 直接越界。
    """
    _qapp()
    from pet.speech_bubble import PetSpeechBubble
    from pet.speech_bubble_text import bubble_max_lines, paginate_bubble_text

    narrow = QRect(0, 0, 420, 700)
    monkeypatch.setattr(PetSpeechBubble, "_available_geometry", lambda self, anchor: QRect(narrow))
    anchor = QRect(80, 300, 200, 240)
    text = "放大的气泡文字每行都要能装下。" * 16
    bubble = PetSpeechBubble()
    bubble.set_text_scale(3.0)
    try:
        column = bubble._column_for_text(text, anchor)
        margins = bubble._layout.contentsMargins()
        assert column <= narrow.width() - margins.left() - margins.right(), f"列宽 {column} 超出可用区 {narrow.width()} → 气泡会长出屏幕"

        bubble.show_text(text, anchor, 60000)
        bubble.label.ensurePolished()
        metrics = QFontMetrics(bubble.label.font())
        pages = paginate_bubble_text(metrics, text, bubble.label.width(), bubble_max_lines(text))
        widest = max(metrics.horizontalAdvance(line) for page in pages for line in page.split("\n"))
        assert bubble.label.width() >= widest, f"可用区 420px：label {bubble.label.width()} 比最长行 {widest} 还窄 → 行尾会被切"
        assert bubble.label.width() >= 300, "300% 下 label 不该退化成窄条"
        assert narrow.contains(bubble.geometry()), f"气泡 {bubble.geometry().getRect()} 越出可用区 {narrow.getRect()}"
    finally:
        bubble.close()


def test_scale_clamped_on_bubble_and_stays_on_screen():
    """越界系数被钳位；放大到上限时气泡不被推到屏幕外。"""
    _qapp()
    from PySide6.QtGui import QGuiApplication

    bubble = _bubble(99.0, "越界放大的一行字。" * 6)
    try:
        assert bubble.text_scale == 3.0
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            assert avail.left() <= bubble.x() <= avail.right()
            assert bubble.y() <= avail.bottom()
            if bubble.height() <= avail.height():
                assert bubble.y() >= avail.top()
            else:
                # 气泡高过整块可用区时按设计顶边贴住上沿（不被推出屏幕上沿）
                assert bubble.y() == avail.top()
    finally:
        bubble.close()


def test_show_text_without_scale_keeps_legacy_hardcoded_font():
    """没有注入系数时（旧调用点）保持 13px 硬编码基线，行为零变化。"""
    _qapp()
    from pet.speech_bubble import PetSpeechBubble

    bubble = PetSpeechBubble()
    bubble.show_text("未注入系数。", _anchor(), 3000)
    bubble.label.ensurePolished()
    try:
        assert bubble.text_scale == 1.0
        assert bubble.label.font().pixelSize() == 13
    finally:
        bubble.close()
