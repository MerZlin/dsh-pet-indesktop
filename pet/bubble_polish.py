"""Theme-matched native speech surfaces; text layout and timers remain native."""

from __future__ import annotations
import json, re
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

_cache = {"path": None, "stamp": None, "theme": "light"}


def theme_for(bubble):
    config = getattr(bubble, "_dsh_theme_config", None)
    if config is not None:
        return config.get("bubble_ui_style", "light")
    try:
        from .language_ui import _manager

        if _manager is not None:
            path = Path(_manager.path)
        elif _cache["path"] is not None:
            path = _cache["path"]
        else:
            from .config import Config

            path = Path(Config().path)
        stamp = path.stat().st_mtime_ns
        if path != _cache["path"] or stamp != _cache["stamp"]:
            value = json.loads(path.read_text(encoding="utf-8"))
            _cache.update(path=path, stamp=stamp, theme=value.get("bubble_ui_style", "light"))
    except (OSError, ValueError):
        pass
    return _cache["theme"]


def palette(theme):
    from .ui_polish import colors

    c = colors("dark" if theme == "dark" else "light")
    if theme == "dark":
        c.update(top="#1d3049", bottom="#142237", bubbleBorder="#3d587c", shadow="#081424")
    elif theme == "glass":
        c.update(top="#f5faff", bottom="#eaf3ff", bubbleBorder="#bdd2ed", shadow="#5476a0")
    else:
        c.update(top="#ffffff", bottom="#f0f6ff", bubbleBorder="#c6d8ef", shadow="#5476a0")
    return c


def recolor(label, color):
    css = label.styleSheet()
    css = re.sub(r"(?<![-\w])color\s*:\s*[^;]+;", lambda _m: "color: " + color + ";", css)
    label.setStyleSheet(css)


def apply(bubble, force=False):
    from .overlay_layout import install

    install()
    if not getattr(bubble, "_dsh_raise_coordinated", False):
        bubble._dsh_raise_coordinated = True
        original_raise = bubble.raise_

        def raise_with_priority():
            original_raise()
            from .overlay_layout import sync

            sync()

        bubble.raise_ = raise_with_priority
    from .ui_preview import install_runtime

    install_runtime()
    theme = theme_for(bubble)
    key = (theme, bubble._style_id, bubble._text_scale)
    if not force and getattr(bubble, "_dsh_surface_key", None) == key:
        return
    bubble._dsh_surface_key = key
    bubble.setProperty("dshBubbleTheme", theme)
    c = palette(theme)
    bubble._preset = dict(bubble._preset, background=c["top"], border=c["bubbleBorder"], foreground=c["text"], shadow=c["shadow"])
    if bubble._preset.get("shape") != "breath_bubble":
        bubble._preset["radius"] = 18
    bubble._shadow_layers = ((4.0, 4, 1.5), (2.5, 7, 1.25), (1.0, 12, 1.0))
    recolor(bubble.label, c["text"])
    recolor(bubble._subtitle_label, c["muted"])
    recolor(bubble._page_indicator, c["blue"])
    if bubble._preset.get("shape") == "breath_bubble":
        bubble._water_fill = c["pill"]
    for button in bubble._interactive_buttons:
        button.setStyleSheet(
            "QPushButton { color: TEXT; background: FIELD; border: 1px solid BORDER; border-radius: 11px; padding: 4px 12px; font-size: 11px; } QPushButton:hover { background: HOVER; border-color: BLUE; } QPushButton:pressed { background: PILL; } QPushButton:focus { border-color: BLUE; }".replace(
                "TEXT", c["text"]
            )
            .replace("FIELD", c["field"])
            .replace("BORDER", c["border"])
            .replace("HOVER", c["hover"])
            .replace("BLUE", c["blue"])
            .replace("PILL", c["pill"])
        )
    for label in bubble._button_row.findChildren(type(bubble.label)):
        recolor(label, c["muted"] if label.objectName() == "pet-speech-branch-hint" else c["text"])
    bubble.update()


def paint(bubble):
    if bubble._preset.get("shape") == "breath_bubble":
        return False
    theme = bubble.property("dshBubbleTheme") or "light"
    c = palette(theme)
    painter = QPainter(bubble)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    bubble._paint_soft_shadow(painter, bubble._surface_path)
    rect = QRectF(bubble._surface_rect)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top = QColor(c["top"])
    bottom = QColor(c["bottom"])
    if theme == "glass":
        top.setAlpha(244)
        bottom.setAlpha(233)
    gradient.setColorAt(0, top)
    gradient.setColorAt(1, bottom)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawPath(bubble._surface_path)
    bubble._standard_image_rect = QRectF()
    if bubble._content_kind == "image" and not bubble._source_pixmap.isNull():
        image_rect = QRectF(bubble.label.geometry())
        clip = QPainterPath()
        radius = max(5.0, float(bubble._preset["radius"]) * 0.65)
        clip.addRoundedRect(image_rect, radius, radius)
        painter.save()
        painter.setClipPath(clip.intersected(bubble._surface_path))
        painter.drawPixmap(image_rect, bubble._source_pixmap, QRectF(bubble._source_pixmap.rect()))
        painter.restore()
        bubble._standard_image_rect = image_rect
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(c["bubbleBorder"]), 1.0))
    painter.drawPath(bubble._surface_path)
    painter.end()
    return True
