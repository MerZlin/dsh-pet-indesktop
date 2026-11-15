"""气泡风格预设的数据层（零 Qt）。

原分支 ``pet/speech_bubble.py::BUBBLE_STYLE_PRESETS`` 的五条预设搬到 core：
这里只定义「是什么」——id、设置页文案、配色、圆角、首选落位、阴影层与形状名。
怎么画（QPainterPath / QSS 字符串）在 :mod:`petcore.ui.bubble_style`；配置归一与
设置页都只读本模块，``config.py`` 因此不需要依赖 Qt 或 ui 层。
"""

from __future__ import annotations

DEFAULT_BUBBLE_STYLE = "classic_top"

#: 每条预设都刻意把「表面处理」和「首选落位」绑在一起，不是只换颜色的别名。
BUBBLE_STYLE_PRESETS: dict[str, dict] = {
    "classic_top": {
        "label": "经典白 · 正上方",
        "placement": "top",
        # QColor 只认 #AARRGGBB / #RRGGBB，不认 CSS 的 rgba(...)。
        "background": "#f2ffffff",
        "border": "#b47896b4",
        "foreground": "#252a32",
        "radius": 12,
        "shape": "plain",
    },
    "paper_left": {
        "label": "纸感卡片 · 左上方",
        "placement": "top_left",
        "background": "#ffffff",
        "border": "#dce1e7",
        "foreground": "#252a32",
        "radius": 11,
        "shadow": "#374151",
    },
    "glass_right": {
        "label": "深色玻璃 · 右上方",
        "placement": "top_right",
        "background": "#292d36",
        "border": "#4d5360",
        "foreground": "#f7f8fb",
        "radius": 18,
        "shadow": "#111318",
    },
    "soft_blue_top": {
        "label": "柔蓝对话 · 正上方",
        "placement": "top",
        "background": "#eef6ff",
        "border": "#a9c9ef",
        "foreground": "#24466f",
        "radius": 16,
        "shadow": "#315f91",
    },
    "breath_bubble": {
        "label": "吐气水泡 · 左上方",
        "placement": "top_left",
        "background": "#fbfeff",
        "border": "#0e5968",
        "foreground": "#23444d",
        "radius": 0,
        "shadow": "#0e5968",
        "shape": "breath_bubble",
    },
}

#: 合法 id（顺序即设置页下拉顺序）。
STYLE_IDS = tuple(BUBBLE_STYLE_PRESETS)

#: 三层柔和阴影 ``(描边宽, alpha, 下移像素)``；与主分支逐值一致。
SHADOW_LAYERS = ((4.0, 6, 1.5), (2.5, 10, 1.25), (1.0, 18, 1.0))


def style_preset(style_id: str) -> dict:
    """取一条预设；未知 id 回默认（配置归一之外的第二道兜底）。"""
    return BUBBLE_STYLE_PRESETS.get(str(style_id or ""), BUBBLE_STYLE_PRESETS[DEFAULT_BUBBLE_STYLE])


def style_label(style_id: str) -> str:
    return str(style_preset(style_id)["label"])


__all__ = [
    "BUBBLE_STYLE_PRESETS",
    "DEFAULT_BUBBLE_STYLE",
    "SHADOW_LAYERS",
    "STYLE_IDS",
    "style_label",
    "style_preset",
]
