"""气泡风格的绘制层：QSS 生成、表面路径、尾巴方向与柔和阴影。

数据（id/配色/圆角/首选落位）在 :mod:`petcore.contracts.bubble_styles`；这里只把
数据变成 Qt 能画的东西。几何函数保持纯函数，便于逐条钉住（尾巴方向、圆角、阴影）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QTransform

from ..contracts.bubble_styles import (
    DEFAULT_BUBBLE_STYLE,
    SHADOW_LAYERS,
    STYLE_IDS,
    style_preset,
)

#: 尾巴露在气泡主体外的深度与半宽（逻辑 px，随字号缩放）。
TAIL_DEPTH = 9.0
TAIL_HALF = 7.0
#: 气泡主体四周给尾巴/阴影留的透明边（整圈留，换落位时不用重排布局）。
TAIL_BAND = 10
#: 吐气水泡的有机轮廓在主体外还要呼吸空间（否则单行气泡被压成扁椭圆、字贴边）。
#: 左右不对称：轮廓居中会把文字推得偏右，左侧只留一条边、右侧留足圆弧。
BREATH_BAND_LEFT = 10
BREATH_BAND_RIGHT = 30
BREATH_BAND_Y = 16

#: 圆角矩形与三角尾巴的圆角采样（尾巴根部不额外加圆角）。
def _px(value: float) -> int:
    return max(1, int(round(value)))


def style_ids() -> tuple[str, ...]:
    return STYLE_IDS


def is_breath(style_id: str) -> bool:
    return style_preset(style_id).get("shape") == "breath_bubble"


def is_plain(style_id: str) -> bool:
    """默认「重写版」气泡：QSS 圆角矩形，没有尾巴/阴影/透明边。"""
    return style_preset(style_id).get("shape") == "plain"


def style_radius(style_id: str, scale: float = 1.0) -> int:
    radius = float(style_preset(style_id).get("radius") or 0)
    return _px(radius * scale) if radius > 0 else 0


def _rgba(color: str, alpha: int = 255) -> str:
    value = QColor(color)
    return f"rgba({value.red()},{value.green()},{value.blue()},{alpha})"


def panel_qss(style_id: str, scale: float = 1.0) -> str:
    """气泡主体统一留透明：表面只在 ``paintEvent`` 里画一次。

    plain 风格也走自绘而不是 QSS，避免从「有底色」切到「透明」时 Qt 缓存
    旧 palette，导致换风格后残留一块白底。
    """
    del style_id, scale
    return "QWidget#bubblePanel{background:transparent;border:none;}"


def text_qss(style_id: str, scale: float = 1.0, base_px: int = 13) -> str:
    preset = style_preset(style_id)
    return (
        "QLabel{background:transparent;color:%s;font-size:%dpx;}"
        % (preset["foreground"], _px(base_px * scale))
    )


def subtitle_qss(style_id: str, scale: float = 1.0, base_px: int = 11) -> str:
    preset = style_preset(style_id)
    return (
        "QLabel#bubbleSubtitle{background:transparent;color:%s;"
        "font-size:%dpx;font-weight:600;}"
        % (_rgba(preset["foreground"], 190), _px(base_px * scale))
    )


def pager_qss(style_id: str, scale: float = 1.0, base_px: int = 10) -> str:
    preset = style_preset(style_id)
    return (
        "QLabel#bubblePager{background:transparent;color:%s;font-size:%dpx;}"
        % (_rgba(preset["foreground"], 160), _px(base_px * scale))
    )


def caption_qss(style_id: str, scale: float = 1.0, base_px: int = 12) -> str:
    preset = style_preset(style_id)
    return (
        "QLabel#bubbleCaption{background:%s;color:%s;border:1px solid %s;"
        "border-radius:%dpx;padding:%dpx %dpx;font-size:%dpx;font-weight:600;}"
        % (
            _rgba(preset["background"], 242),
            preset["foreground"],
            _rgba(preset["border"], 180),
            _px(max(6, float(preset["radius"] or 10) * 0.65) * scale),
            _px(5 * scale),
            _px(10 * scale),
            _px(base_px * scale),
        )
    )


def image_qss(style_id: str, scale: float = 1.0) -> str:
    preset = style_preset(style_id)
    return (
        "QLabel#bubbleImage{background:%s;border:1px solid %s;border-radius:%dpx;}"
        % (
            _rgba(preset["background"], 242),
            _rgba(preset["border"], 180),
            _px(max(6, float(preset["radius"] or 12)) * scale),
        )
    )


def tail_side_for(anchor: QRect, bubble: QRect) -> str:
    """气泡贴着角色哪一侧：尾巴就长在对侧（朝向角色）。

    ``"none"`` = 气泡与锚点重叠（屏幕太挤时的兜底落点），不画尾巴。
    """
    if anchor.isNull() or bubble.isNull():
        return "none"
    if bubble.bottom() <= anchor.top():
        return "bottom"
    if bubble.top() >= anchor.bottom():
        return "top"
    if bubble.right() <= anchor.left():
        return "right"
    if bubble.left() >= anchor.right():
        return "left"
    return "none"


def tail_origin(
    anchor: QRect,
    bubble: QRect,
    *,
    radius: float = 0.0,
    tail_half: float = TAIL_HALF,
) -> tuple[str, float | None]:
    """尾巴长在哪条边、沿边的哪个位置（朝向角色的位置）。

    只按「气泡在角色的哪一侧」选边还不够：长气泡靠边时角色可能在气泡的
    斜下方，尾巴落在气泡正中就会和角色错开（实机反馈）。这里把尾巴尖沿边
    投影到角色中心并钳进圆角内，尾巴始终指向角色。
    """
    side = tail_side_for(anchor, bubble)
    if side == "none":
        return side, None
    if side in ("top", "bottom"):
        low = bubble.left() + radius + tail_half
        high = bubble.right() - radius - tail_half
        if high <= low:
            return side, float(bubble.center().x())
        return side, float(min(max(anchor.center().x(), low), high))
    low = bubble.top() + radius + tail_half
    high = bubble.bottom() - radius - tail_half
    if high <= low:
        return side, float(bubble.center().y())
    return side, float(min(max(anchor.center().y(), low), high))


def surface_path(
    rect: QRectF,
    radius: float,
    *,
    tail_side: str = "none",
    tail_depth: float = TAIL_DEPTH,
    tail_half: float = TAIL_HALF,
    tail_at: float | None = None,
) -> QPainterPath:
    """圆角矩形 + 朝向角色的三角尾巴（同一路径，填充/描边一次完成）。

    ``tail_at`` 是尾巴尖沿边的位置（``None`` = 边中点）；传 ``tail_origin()``
    的结果可让长气泡在靠边时仍然指向角色。
    """
    body = QPainterPath()
    radius = max(0.0, min(float(radius), min(rect.width(), rect.height()) / 2.0))
    if radius <= 0.0:
        body.addRect(rect)
    else:
        body.addRoundedRect(rect, radius, radius)
    if tail_side not in {"top", "bottom", "left", "right"}:
        return body
    cx = rect.center().x() if tail_at is None else float(tail_at)
    cy = rect.center().y() if tail_at is None else float(tail_at)
    if tail_side == "bottom":
        points = [
            QPointF(cx - tail_half, rect.bottom() - 1.0),
            QPointF(cx, rect.bottom() + tail_depth),
            QPointF(cx + tail_half, rect.bottom() - 1.0),
        ]
    elif tail_side == "top":
        points = [
            QPointF(cx + tail_half, rect.top() + 1.0),
            QPointF(cx, rect.top() - tail_depth),
            QPointF(cx - tail_half, rect.top() + 1.0),
        ]
    elif tail_side == "right":
        points = [
            QPointF(rect.right() - 1.0, cy + tail_half),
            QPointF(rect.right() + tail_depth, cy),
            QPointF(rect.right() - 1.0, cy - tail_half),
        ]
    else:
        points = [
            QPointF(rect.left() + 1.0, cy - tail_half),
            QPointF(rect.left() - tail_depth, cy),
            QPointF(rect.left() + 1.0, cy + tail_half),
        ]
    tail = QPainterPath()
    tail.addPolygon(points)
    tail.closeSubpath()
    return body.united(tail)


def breath_main_bubble_path(canvas: QSize) -> QPainterPath:
    """吐气水泡的有机轮廓（与主分支 ``breath_main_bubble_path`` 同一组控制点）。"""
    sx = max(0.01, canvas.width() / 240.0)
    sy = max(0.01, canvas.height() / 195.0)
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
    return main


def shadow_pen_layers(style_id: str):
    """三层柔和阴影：``(颜色, 描边宽, alpha, 下移)``，与主分支同值。"""
    shadow = style_preset(style_id).get("shadow")
    if not shadow:
        return []
    base = QColor(shadow)
    layers = []
    for width, alpha, offset_y in SHADOW_LAYERS:
        color = QColor(base)
        color.setAlpha(alpha)
        layers.append((color, width, offset_y))
    return layers


def shadow_path(path: QPainterPath, offset_y: float) -> QPainterPath:
    return QTransform.fromTranslate(0, offset_y).map(path)


def pen_for(color: QColor, width: float) -> QPen:
    return QPen(
        color,
        width,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )


__all__ = [
    "DEFAULT_BUBBLE_STYLE",
    "TAIL_BAND",
    "TAIL_DEPTH",
    "TAIL_HALF",
    "breath_main_bubble_path",
    "caption_qss",
    "image_qss",
    "is_breath",
    "pager_qss",
    "panel_qss",
    "pen_for",
    "shadow_path",
    "shadow_pen_layers",
    "style_ids",
    "style_radius",
    "subtitle_qss",
    "surface_path",
    "tail_origin",
    "tail_side_for",
    "text_qss",
]
