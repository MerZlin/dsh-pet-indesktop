# -*- coding: utf-8 -*-
"""余额气泡上屏缝：``_show_balance_payload`` 把金额短语整段交到真实气泡。

只替换动画入口（宿主没有素材播放器）。查询本身不在这条缝上，用例不发网络。
"""
from __future__ import annotations

import copy

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from pet import balance as balance_mod
from pet.app import _show_balance_payload
from pet.speech_bubble import PetSpeechBubble

_WORD_JOINER = "\u2060"
_INFO = {"is_available": True, "total": "12.34", "granted": "2.34", "topped_up": "10.00"}
_CHUNKS = ("余额 ¥12.34", "充值 ¥10.00", "赠送 ¥2.34")


def _app():
    return QApplication.instance() or QApplication([])


def _anchor() -> QRect:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(80, 80, 180, 240)
    area = screen.availableGeometry()
    return QRect(area.center().x() - 90, area.center().y(), 180, 240)


def _cfg() -> dict:
    return {
        "dialogue_mode": "custom",
        "dialogue_phrases": {
            "global": {"balance.result": ["口袋还剩 {text}"]},
            "agents": {},
        },
        "balance_tier_labels_mode": "default",
        "balance_tier_label_peak": "",
        "balance_tier_label_idle": "",
        "balance_tier_color_enabled": True,
    }


class _Host(QWidget):
    """真实 QWidget + PetSpeechBubble。动画只记名字，不碰素材。"""

    def __init__(self, style: str, scale: float) -> None:
        super().__init__()
        self.cfg = _cfg()
        self.bubble = PetSpeechBubble(style_id=style)
        self.bubble.set_text_scale(scale)
        self.anims: list[str] = []
        self.shown: list[str] = []

    def show_bubble(self, text, duration_ms=3200, subtitle=None, **_kwargs) -> bool:
        self.shown.append(str(text))
        self.bubble.show_text(
            str(text), _anchor(), int(duration_ms), subtitle=str(subtitle or ""),
        )
        _app().processEvents()
        return True

    def request_link_anim(self, name) -> None:
        self.anims.append(str(name))


def _visible_lines(bubble: PetSpeechBubble) -> list[str]:
    return bubble.label.text().replace(_WORD_JOINER, "").split("\n")


def _assert_fitting_chunks_intact(bubble: PetSpeechBubble) -> None:
    metrics = QFontMetrics(bubble.label.font())
    width = bubble.label.width()
    height = bubble.label.height()
    lines = _visible_lines(bubble)
    spacing = metrics.lineSpacing()
    assert spacing > 0 and lines
    # 行宽是换行预算，也是 QLabel 的裁切宽：每一行的墨迹都必须落在里面。
    # 经典气泡按行数定高，行槽整段在盒内。呼吸泡正文盒是画布余量，文字在盒里
    # 垂直居中；文字大小 3 倍时三行行高会高出这个余量，行槽仍须与盒子相交。
    block = len(lines) * spacing
    overflow = max(0, block - height)
    if bubble._preset.get("shape") != "breath_bubble":
        assert overflow <= 2, (block, height)
    top_clip = overflow / 2
    for index, line in enumerate(lines):
        assert metrics.horizontalAdvance(line) <= width, (line, width)
        top = index * spacing - top_clip
        bottom = top + spacing
        assert top < height + 2 and bottom > -2, (index, height, lines)
        if overflow <= 2:
            assert -2 <= top and bottom <= height + 2, (index, height, lines)
    visible = "\n".join(lines)
    flat = visible.replace("\n", "")
    for chunk in _CHUNKS:
        if chunk not in flat or metrics.horizontalAdvance(chunk) > width:
            continue
        start = flat.find(chunk)
        count = 0
        for char in visible:
            if char == "\n":
                assert not (start < count < start + len(chunk)), (chunk, lines)
                continue
            count += 1


def _assert_subtitle_budget(bubble: PetSpeechBubble, case: str) -> None:
    bubble.repaint()
    _app().processEvents()
    sub = bubble._subtitle_label
    assert not sub.isHidden(), case
    needed = sub.heightForWidth(max(1, sub.width()))
    assert sub.height() + 1 >= needed, case
    assert sub.maximumWidth() >= bubble.label.width(), case
    if bubble._preset.get("shape") == "breath_bubble":
        bubble._build_breath_bubble_geometry(bubble.rect())
        contour = int(bubble._surface_path.boundingRect().bottom())
        margins = bubble._layout.contentsMargins()
        inner = bubble.height() - margins.top() - margins.bottom()
        need = bubble.label.height() + bubble._layout.spacing() + sub.heightForWidth(
            max(1, bubble.label.width())
        )
        assert inner + 2 >= need, case
    else:
        contour = bubble._surface_rect.bottom()
    assert sub.y() + needed <= contour + 2, case


def test_show_balance_payload_keeps_dict_and_formats_persona_text():
    _app()
    host = _Host("classic_top", 1.0)
    payload = {"text": balance_mod.format_balance(_INFO), "info": dict(_INFO)}
    original = copy.deepcopy(payload)
    try:
        _show_balance_payload(host, payload)
        _app().processEvents()
        assert payload == original
        assert _WORD_JOINER not in payload["text"]
        assert host.anims == ["余额-金袋叮当"]
        assert host.shown[0].replace(_WORD_JOINER, "").startswith("口袋还剩 ")
        assert "口袋还剩" in "".join(_visible_lines(host.bubble))
        _assert_fitting_chunks_intact(host.bubble)
        _assert_subtitle_budget(host.bubble, "dict")
        _show_balance_payload(host, payload)
        _app().processEvents()
        assert payload == original
        assert host.shown[0] == host.shown[1]
        assert host.anims == ["余额-金袋叮当", "余额-金袋叮当"]
        _assert_fitting_chunks_intact(host.bubble)
    finally:
        host.bubble.dismiss()
        host.close()


def test_show_balance_payload_accepts_legacy_string_without_animation():
    _app()
    host = _Host("classic_top", 1.0)
    text = "余额 ¥12.34（充值 ¥10.00 / 赠送 ¥2.34）"
    try:
        _show_balance_payload(host, text)
        _app().processEvents()
        assert host.anims == []
        visible = "".join(_visible_lines(host.bubble))
        assert visible.startswith("口袋还剩 ")
        assert text in visible.replace("\n", "")
        _assert_fitting_chunks_intact(host.bubble)
    finally:
        host.bubble.dismiss()
        host.close()


@pytest.mark.parametrize("style", ("classic_top", "breath_bubble"))
@pytest.mark.parametrize("scale", (0.5, 1.0, 1.5, 3.0))
def test_balance_bubble_lines_fit_at_text_scales(style, scale):
    _app()
    host = _Host(style, scale)
    payload = {"text": balance_mod.format_balance(_INFO), "info": dict(_INFO)}
    original = copy.deepcopy(payload)
    try:
        _show_balance_payload(host, payload)
        _app().processEvents()
        assert payload == original
        _assert_fitting_chunks_intact(host.bubble)
        _assert_subtitle_budget(host.bubble, f"{style} scale={scale}")
    finally:
        host.bubble.dismiss()
        host.close()


def test_elide_joined_tail_never_drops_tail_silently():
    """截断必须带省略号信号：整段放得下但加省略号超宽的分支，不许无声丢掉
    后续正文（PR #329 审查 F1——修复前该分支 return token，赠送金额整条消失）。"""
    from PySide6.QtGui import QFont, QFontMetrics

    from pet.balance import format_balance_display
    from pet.speech_bubble_text import _elide_joined_tail

    metrics = QFontMetrics(QFont("Microsoft YaHei UI", 13))
    text = format_balance_display("余额 ¥12.34（充值 ¥10.00 / 赠送 ¥2.34）")
    # 逐档收窄宽度扫一遍：任何截断结果都必须带 …（或根本放不下的极小宽度除外）
    full_advance = metrics.horizontalAdvance("余")
    for ratio in (0.95, 0.8, 0.6, 0.45, 0.3):
        width = int(metrics.horizontalAdvance(text) * ratio)
        if width < full_advance * 2:
            continue
        out = _elide_joined_tail(metrics, text, width)
        assert out == text or out.endswith("…"), f"ratio={ratio}: {out!r}"


def test_canvas_width_budget_ignores_word_joiners():
    """吐气水泡画布宽度档按可见字符数（去标记）——U+2060 连接符不可见，
    数进长度气泡会无端变宽（PR #329 审查 F2）。"""
    from pet.balance import format_balance_display
    from pet.speech_bubble_text import _budget_length, normalize_bubble_text

    plain = "余额 ¥12.34（充值 ¥10.00 / 赠送 ¥2.34）"
    marked = format_balance_display(plain)
    assert "⁠" in marked  # 确实有连接符
    assert _budget_length(normalize_bubble_text(marked)) == len(normalize_bubble_text(plain))
