"""对话窗配色的 WCAG 对比度工具（纯函数，便测试）。"""

from __future__ import annotations

from typing import Iterable


def _rgb(color: str) -> tuple[int, int, int]:
    text = str(color or "").strip().lstrip("#")
    if len(text) == 8:  # #AARRGGBB：忽略 alpha
        text = text[2:]
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ValueError(f"unsupported color: {color!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def relative_luminance(color: str) -> float:
    channels = []
    for value in _rgb(color):
        channel = value / 255.0
        channels.append(
            channel / 12.92
            if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x 对比度；4.5 是正文/按钮文字的及格线，3.0 是图标/大字。"""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def ensure_contrast(
    background: str,
    ink: str = "#ffffff",
    minimum: float = 4.5,
) -> str:
    """把背景向黑/白调节，直到与指定墨色达到对比度下限。

    用户可以把强调色调成任意 ``#RRGGBB``；白字压在浅色强调上会看不见，
    这里只调节**背景**，不改变用户选的墨色。
    """
    if contrast_ratio(ink, background) + 1e-9 >= minimum:
        return background
    red, green, blue = _rgb(background)
    target = (0, 0, 0) if relative_luminance(ink) > 0.5 else (255, 255, 255)
    for step in range(1, 21):
        factor = step / 20.0
        candidate = tuple(
            int(round(channel + (goal - channel) * factor))
            for channel, goal in zip((red, green, blue), target)
        )
        value = "#%02x%02x%02x" % candidate
        if contrast_ratio(ink, value) + 1e-9 >= minimum:
            return value
    return "#000000" if target == (0, 0, 0) else "#ffffff"


def readable_ink(
    background: str,
    *,
    light: str = "#ffffff",
    dark: str = "#16181d",
) -> str:
    """在浅/深两种墨色里选对比度更高的一个（避免白底白字、黑底黑字）。"""
    return light if contrast_ratio(light, background) >= contrast_ratio(dark, background) else dark


def audit_pairs(pairs: Iterable[tuple[str, str, float]], *, label: str = "") -> list[str]:
    """返回不达标的 ``前景/背景`` 说明；空列表 = 全部通过。"""
    problems: list[str] = []
    for foreground, background, minimum in pairs:
        ratio = contrast_ratio(foreground, background)
        if ratio + 1e-9 < minimum:
            prefix = f"{label}: " if label else ""
            problems.append(
                f"{prefix}{foreground} on {background} = {ratio:.2f} < {minimum:.1f}"
            )
    return problems


__all__ = [
    "audit_pairs",
    "contrast_ratio",
    "ensure_contrast",
    "readable_ink",
    "relative_luminance",
]
