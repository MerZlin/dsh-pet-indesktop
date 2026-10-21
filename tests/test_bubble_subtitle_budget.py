# -*- coding: utf-8 -*-
"""issue #216：余额气泡的峰谷副标题必须参与气泡高度预算。

病症（大 DPR 实机报告）：① 副标题跨到气泡轮廓外 ② 换行后第二行被窗口
底边裁掉 ③ 未着色灰字落在深色桌面上看不清（其实是文字画到了气泡轮廓外的
透明区，窗口底下就是桌面）。

判据用 ``QLabel.heightForWidth(w)``——它就是要绘制的行高，与真实绘制同源：
- 分配高度 < 需要高度 → 末行被自己的 label 裁掉（②③的可见后果）；
- 宽度上限 < 气泡真实内宽 → Qt 按高度定尺寸时用**未夹取**的宽度算行数、
  布局与绘制却按上限夹取，两者分叉必然多出一行（①的病根）；
- 文字底边越过气泡已绘制轮廓（标准泡 ``_surface_rect`` / 呼吸泡轮廓路径）→ ①。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pet.island import balance as balance_mod
from pet.speech_bubble import BUBBLE_TEXT_COLUMN, PetSpeechBubble

BEIJING = ZoneInfo("Asia/Shanghai")
# issue 截图里的余额正文形态：短正文（气泡内宽窄），峰谷副标题两行。
BALANCE_BODY = "余额情况：余额 ¥12.34"
PEAK_NOW = datetime(2026, 10, 8, 10, 30, tzinfo=BEIJING)   # 高峰 · 下一空闲 12:00

STANDARD_STYLES = ("classic_top", "paper_left", "glass_right", "soft_blue_top")


def _get_app():
    return QApplication.instance() or QApplication([])


def _anchor() -> QRect:
    screen = QGuiApplication.primaryScreen().availableGeometry()
    return QRect(screen.center().x() - 90, screen.center().y(), 180, 240)


def _show(style: str, scale: float, *, body: str = BALANCE_BODY) -> PetSpeechBubble:
    _get_app()
    bubble = PetSpeechBubble(style_id=style)
    bubble.set_text_scale(scale)
    bubble.show_text(
        body, _anchor(), 6000,
        subtitle=balance_mod.deepseek_pricing_hint_html(now=PEAK_NOW),
    )
    return bubble


def _contour_bottom(bubble: PetSpeechBubble) -> int:
    """气泡已绘制轮廓的底边（呼吸泡是贝塞尔轮廓，标准泡是圆角矩形）。"""
    if bubble._preset.get("shape") == "breath_bubble":
        bubble._build_breath_bubble_geometry(bubble.rect())
        return int(bubble._surface_path.boundingRect().bottom())
    return bubble._surface_rect.bottom()


def _subtitle_ink_outside_contour(bubble: PetSpeechBubble) -> int:
    """副标题墨迹里画在气泡轮廓外的像素数（设备像素）。

    做法：同一气泡渲染两次（有文字 / 清空两个 label 的文字、不动尺寸），
    差值像素 = 文字墨迹；只扫副标题所在的行带，逐个用 QPainterPath 判内外。
    """
    bubble._build_breath_bubble_geometry(bubble.rect())
    path = bubble._surface_path
    sub = bubble._subtitle_label
    need = sub.heightForWidth(max(1, sub.width()))

    bubble.repaint()
    with_text = bubble.grab().toImage()
    dpr = with_text.devicePixelRatio() or 1.0
    # 清空两个 label 的文字（不动尺寸）再渲染一次：与上一次的差值像素就是文字墨迹。
    bubble.label.setText("")
    sub.setText("")
    bubble.repaint()
    blank = bubble.grab().toImage()

    top = max(0, int((sub.y() - 2) * dpr))
    bottom = min(with_text.height(), int((sub.y() + need + 2) * dpr))
    outside = 0
    for y in range(top, bottom):
        for x in range(with_text.width()):
            if with_text.pixel(x, y) == blank.pixel(x, y):
                continue
            if not path.contains(QPointF(x / dpr, y / dpr)):
                outside += 1
    return outside


def _assert_subtitle_in_bubble(bubble: PetSpeechBubble, *, case: str) -> None:
    sub = bubble._subtitle_label
    assert not sub.isHidden(), f"{case}: 副标题应参与布局"
    needed = sub.heightForWidth(max(1, sub.width()))
    assert sub.height() + 1 >= needed, (
        f"{case}: 副标题被自己的 label 裁——分配 {sub.height()}px，"
        f"按 {sub.width()}px 宽排版需要 {needed}px（气泡 {bubble.width()}x{bubble.height()}）"
    )
    assert sub.maximumWidth() >= bubble.label.width(), (
        f"{case}: 副标题宽度上限 {sub.maximumWidth()}px 窄于气泡内宽 "
        f"{bubble.label.width()}px——定高口径与绘制口径分叉，副标题会多出一行"
    )
    assert sub.y() + needed <= _contour_bottom(bubble) + 2, (
        f"{case}: 副标题文字底边 {sub.y() + needed}px 越过气泡轮廓 "
        f"{_contour_bottom(bubble)}px（文字画到气泡外的透明区）"
    )


def _subtitle_text_column(bubble: PetSpeechBubble) -> tuple[float, float]:
    """副标题文本列的左右边界（气泡局部坐标，已扣掉安全内边距）。"""
    sub = bubble._subtitle_label
    margins = sub.contentsMargins()
    return (
        sub.x() + margins.left(),
        sub.x() + sub.width() - margins.right(),
    )


def _assert_subtitle_column_inside_contour(bubble: PetSpeechBubble, *, case: str) -> None:
    """副标题文本列必须落在主泡轮廓内（与字体度量无关的几何判据）。

    取文字底行那一行的主泡弦：轮廓在底部两角向内收，内盒底边比它上面任何一行
    都靠下，弦也最窄。文字列两端落在弦内，任何字体的墨迹都被 label 自身的矩形
    裁住，不可能画到轮廓外。旧口径左右用同一个固定内边距，而布局内边距相对
    轮廓是偏的（主泡矩形在画布内靠左），右端必然越出这条弦。

    两个端点各向列内收 0.5px 再判：轮廓是三次贝塞尔，端点正好压在弦边界上时
    浮点判内外不稳定，收半个像素不影响结论（旧口径的越界量是十几像素级）。
    """
    bubble._build_breath_bubble_geometry(bubble.rect())
    path = bubble._main_bubble_path
    sub = bubble._subtitle_label
    needed = sub.heightForWidth(max(1, sub.width()))
    # 文字底行：超长副标题被画布上限裁切时，墨迹只画到 label 自己的矩形下沿
    # （再往下的行根本不绘制），取两者较低值；正常情形下两者相等。
    row = float(sub.y() + min(needed, sub.height()))
    left, right = _subtitle_text_column(bubble)
    assert path.contains(QPointF(left + 0.5, row - 0.5)), (
        f"{case}: 副标题文本列左端 {left}px 不在文字底行（{row}px）的主泡轮廓内 "
        f"（画布 {bubble.width()}x{bubble.height()}）"
    )
    assert path.contains(QPointF(right - 0.5, row - 0.5)), (
        f"{case}: 副标题文本列右端 {right}px 不在文字底行（{row}px）的主泡轮廓内"
        f"（画布 {bubble.width()}x{bubble.height()}）——末行墨迹会落到轮廓外的透明区"
    )


@pytest.mark.parametrize("style", STANDARD_STYLES)
@pytest.mark.parametrize("scale", (1.5, 2.0, 3.0))
def test_balance_subtitle_stays_inside_scaled_standard_bubble(style, scale):
    """气泡文字放大后（大 DPR 实机上更常见），副标题仍须整行落在气泡内。

    回归：副标题宽度上限写死 248px 且不随「气泡文字大小」缩放，气泡变宽
    超过 248px 后 Qt 定高用未夹取的宽、布局用 248 夹取，副标题多出一行：
    文字底边越过轮廓线，末行被 label 裁掉。
    """
    bubble = _show(style, scale)
    try:
        _assert_subtitle_in_bubble(bubble, case=f"{style} scale={scale}")
    finally:
        bubble.dismiss()


@pytest.mark.parametrize("scale", (1.0, 1.5, 2.0))
def test_balance_subtitle_stays_inside_breath_bubble(scale):
    """吐气水泡的副标题：默认设置即可复现，必须也进高度预算。

    回归：呼吸泡画布高度只按正文算，正文盒先占满剩余空间，副标题只能吃
    边角——分配高度小于它要画的字高，末行被裁；被裁剩的半行还落在有机
    轮廓之外（水泡在底部两角向内收），深色桌面上就是看不见的灰字。
    """
    bubble = _show("breath_bubble", scale)
    try:
        _assert_subtitle_in_bubble(bubble, case=f"breath_bubble scale={scale}")
        # 结构性断言（与字体度量无关）：画布内高必须容得下正文盒 + 副标题
        margins = bubble._layout.contentsMargins()
        inner = bubble.height() - margins.top() - margins.bottom()
        sub = bubble._subtitle_label
        need = bubble.label.height() + bubble._layout.spacing() + sub.heightForWidth(
            max(1, bubble.label.width()))
        assert inner + 2 >= need, (
            f"breath_bubble scale={scale}: 画布内高 {inner}px 装不下正文盒 "
            f"{bubble.label.height()}px + 副标题 {sub.heightForWidth(bubble.label.width())}px"
        )
        # 几何判据先跑：下面数墨迹的那条会把两个 label 的文字清空（差值渲染），
        # 清空后副标题的 heightForWidth 不再是绘制时的口径。
        #
        # 墨迹数只反映**本机字体**：Windows 的 YaHei 恰好让末行比文本列窄，墨迹
        # 越不出轮廓；换字体（CI 的 Linux/Noto CJK 行高与字宽都不同）同一段文字
        # 末行更长，就落到主泡与副泡之间的透明缝里（CI 实测 1.5/2.0 各 32/49
        # 像素）。这条与字体度量无关：文本列两端必须落在「文字底行处的主泡弦」
        # 内——文字列在弦内时，任何字体的墨迹都被 label 自身的矩形裁住。
        _assert_subtitle_column_inside_contour(bubble, case=f"breath_bubble scale={scale}")
        # 副标题墨迹必须落在水泡轮廓内：轮廓在底部两角向内收，副标题末行
        # 首字会压到轮廓外沿——那段像素画在气泡外的透明区，深色桌面上就是
        # 看不见的灰字（issue #216 的①③）。这里直接数渲染墨迹（几何断言无法
        # 表达有机轮廓的边角）。
        outside = _subtitle_ink_outside_contour(bubble)
        assert outside <= 4, (
            f"breath_bubble scale={scale}: 副标题有 {outside} 个墨迹像素画在水泡轮廓外"
        )
    finally:
        bubble.dismiss()


def test_interactive_subtitle_never_widens_the_bubble_past_the_text_column():
    """交互（审批/确认）气泡的副标题不许把它撑宽——原有意图，回归面必须保住。

    file_interpret 的确认气泡带 33 字副标题 + 两个按钮；「气泡文字大小」放大后
    副标题的宽度上限仍取**未缩放**的基础文本列（与正文同口径），否则长副标题会把
    交互气泡撑到两倍宽。
    """
    _get_app()
    bubble = PetSpeechBubble(style_id="classic_top")
    bubble.set_text_scale(2.0)
    bubble.show_text(
        "要解读刚吃掉的 报告.pdf 吗？", _anchor(), 0,
        subtitle="内容将发送给当前配置的 AI 模型进行解读；文件本身不会被修改或上传",
        buttons=[("解读", lambda: None), ("不用了", lambda: None)],
    )
    try:
        assert bubble.width() <= BUBBLE_TEXT_COLUMN + 26 + 2, (
            f"交互气泡被副标题撑宽：{bubble.width()}px（文本列 {BUBBLE_TEXT_COLUMN}px）"
        )
        assert bubble._subtitle_label.maximumWidth() >= bubble.label.width()
    finally:
        bubble.dismiss()


def test_bubble_without_subtitle_geometry_is_unchanged():
    """无副标题的气泡尺寸不受本次改动影响（回归面收窄：只动带副标题的路径）。"""
    _get_app()
    screen = QGuiApplication.primaryScreen().availableGeometry()
    anchor = QRect(screen.center().x() - 90, screen.center().y(), 180, 240)
    plain = PetSpeechBubble(style_id="classic_top")
    plain.set_text_scale(1.0)
    plain.show_text(BALANCE_BODY, anchor, 6000)
    try:
        assert plain._subtitle_label.isHidden()
        margins = plain._layout.contentsMargins()
        assert plain.height() == plain.label.height() + margins.top() + margins.bottom()
    finally:
        plain.dismiss()
