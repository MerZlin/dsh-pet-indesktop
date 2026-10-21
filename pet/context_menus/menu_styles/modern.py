# -*- coding: utf-8 -*-
"""Compact macOS project-menu appearance used exclusively by modern layout."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .common import SYSTEM_FONT_STACK, system_ui_font

#: 「菜单直角」开关（桌宠控制里那一项）的持久化位置。
_CORNER_SETTINGS_ORG = "dsh-pet"
_CORNER_SETTINGS_APP = "context-menu"

#: 圆角幅度在配置值基础上统一收 4px（12 → 8）。
RADIUS_TRIM = 4
#: 右下角不做圆角：系统投影偏右下，那一角本来就被投影撑着，带圆角反而显脏。
BR_CORNER_TRIM = 0


def _corner_settings() -> QSettings:
    return QSettings(_CORNER_SETTINGS_ORG, _CORNER_SETTINGS_APP)


def square_corners_enabled() -> bool:
    """菜单是否画直角（默认直角）。"""
    return bool(_corner_settings().value("square_corners", True, type=bool))


def set_square_corners(enabled: bool) -> None:
    settings = _corner_settings()
    settings.setValue("square_corners", bool(enabled))
    settings.sync()


def corner_radii(appearance: dict | None = None) -> tuple[int, int, int, int]:
    """返回 (左上, 右上, 右下, 左下) 四个角的半径。"""
    appearance = appearance or {}
    requested = appearance.get("corner_radius")
    radius = 12 if requested is None else max(0, min(18, int(requested)))
    if square_corners_enabled():
        return (0, 0, 0, 0)
    radius = max(0, radius - RADIUS_TRIM)
    return (radius, radius, 0, radius)


def _resolve_background(appearance: dict, dark: bool) -> tuple[int, int, int, int]:
    """解析菜单底色（含半透明 alpha）；QSS 与右下角补块共用这一份。"""
    raw = str(appearance.get("dark_background" if dark else "light_background")
              or ("#252525" if dark else "#ffffff"))
    translucent = bool(appearance.get("translucent", True))
    opacity = max(0.72, min(1.0, float(appearance.get("opacity") or 0.94)))
    if raw.startswith("#") and len(raw) == 7:
        red, green, blue = (int(raw[index:index + 2], 16) for index in (1, 3, 5))
        return (red, green, blue, round(opacity * 255) if translucent else 255)
    return (255, 255, 255, 255)


def _rounded_path(rect: QRectF, tl: float, tr: float, br: float, bl: float) -> QPainterPath:
    """四角半径各不相同的圆角矩形路径（QPainterPath 没有现成的四角 API）。"""
    path = QPainterPath()
    path.moveTo(rect.left() + tl, rect.top())
    path.lineTo(rect.right() - tr, rect.top())
    if tr > 0:
        path.arcTo(QRectF(rect.right() - 2 * tr, rect.top(), 2 * tr, 2 * tr), 90, -90)
    path.lineTo(rect.right(), rect.bottom() - br)
    if br > 0:
        path.arcTo(QRectF(rect.right() - 2 * br, rect.bottom() - 2 * br, 2 * br, 2 * br), 0, -90)
    path.lineTo(rect.left() + bl, rect.bottom())
    if bl > 0:
        path.arcTo(QRectF(rect.left(), rect.bottom() - 2 * bl, 2 * bl, 2 * bl), 270, -90)
    path.lineTo(rect.left(), rect.top() + tl)
    if tl > 0:
        path.arcTo(QRectF(rect.left(), rect.top(), 2 * tl, 2 * tl), 180, -90)
    path.closeSubpath()
    return path


def modern_menu_stylesheet(appearance: dict | None = None, *, dark: bool = False) -> str:
    appearance = appearance or {}
    density = str(appearance.get("density") or "standard")
    tl, tr, br, bl = corner_radii(appearance)
    red, green, blue, alpha = _resolve_background(appearance, dark)
    background = f"rgba({red}, {green}, {blue}, {alpha})"
    foreground = str(appearance.get("dark_foreground" if dark else "light_foreground") or ("#f3f3f3" if dark else "#171717"))
    selected = str(appearance.get("dark_hover" if dark else "light_hover") or ("#3a3a3a" if dark else "#eeeeee"))
    selected_text = "#ffffff" if dark else "#111111"
    separator = "#484848" if dark else "#e5e5e5"
    disabled = "#787878" if dark else "#9a9a9a"
    font_size = max(10, min(18, int(appearance.get("ui_font_size") or 13)))
    requested_font = str(appearance.get("ui_font") or "system").replace('"', "")
    font_stack = SYSTEM_FONT_STACK if requested_font == "system" else f'"{requested_font}"'
    vertical_padding = {"compact": 2, "standard": 3, "spacious": 5}.get(density, 3)
    separator_margin = {"compact": 3, "standard": 4, "spacious": 6}.get(density, 4)
    return f"""
QMenu {{
    background-color: {background};
    color: {foreground};
    border: none;
    border-radius: {tl}px;
    padding: 7px;
    font-family: {font_stack};
    font-size: {font_size}px;
    icon-size: 18px;
}}
QMenu::item {{
    min-height: 18px;
    padding: {vertical_padding}px 29px {vertical_padding}px 13px;
    margin: 0;
    border: none;
    border-radius: 9px;
}}
QMenu::item:selected {{
    background-color: {selected};
    color: {selected_text};
}}
QMenu::item:disabled {{ color: {disabled}; }}
QMenu::icon {{ left: 9px; }}
QMenu::indicator {{ width: 1px; height: 1px; image: none; }}
QMenu::separator {{
    height: 1px;
    background: {separator};
    margin: {separator_margin}px 4px;
}}
QMenu::right-arrow {{ width: 7px; height: 11px; margin-right: 10px; }}
QMenu::scroller {{ height: 20px; background: {background}; }}
"""


def apply_modern_menu_style(menu: QMenu, appearance: dict | None = None) -> None:
    appearance = dict(appearance or {})
    if not appearance and menu.parentWidget() is not None:
        parent = menu.parentWidget()
        appearance = {
            "theme": parent.property("modernTheme") or "system",
            "density": parent.property("modernDensity") or "standard",
            "corner_radius": parent.property("modernCornerRadius") or 12,
        }
    theme = str(appearance.get("theme") or "system")
    dark = theme == "dark" or (
        theme == "system" and menu.palette().color(QPalette.ColorRole.Window).lightness() < 128
    )
    density = str(appearance.get("density") or "standard")
    tl, tr, br, bl = corner_radii(appearance)
    menu.setObjectName("modernContextMenu")
    menu.setProperty("menuStyle", "modern")
    menu.setProperty("modernTheme", theme)
    menu.setProperty("modernDensity", density)
    menu.setProperty("modernCornerRadius", tl)
    menu.setProperty("modernBottomRightRadius", br)
    menu.setProperty("modernAppearance", appearance)
    menu.setProperty("modernDark", dark)
    red, green, blue, alpha = _resolve_background(appearance, dark)
    menu.setProperty("modernBackgroundColor", QColor(red, green, blue, alpha))
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    if sys.platform == "win32":
        # QMenu 是 Qt::Popup，窗口标志里没有 Qt::FramelessWindowHint；而 Windows QPA
        # 的 setWindowLayered() 只在「没有原生边框」（WS_CHILD 或 FramelessWindowHint）
        # 时才给带 alpha 的表面挂 WS_EX_LAYERED。少了它，圆角之外的透明像素会被当成
        # 不透明黑，菜单四角就是黑边。补上之后窗口才走逐像素 alpha，系统投影也才正常。
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    font_size = max(10, min(18, int(appearance.get("ui_font_size") or 13)))
    font = system_ui_font(font_size, QFont.Weight.Normal)
    requested_font = str(appearance.get("ui_font") or "system")
    if requested_font != "system":
        font.setFamily(requested_font)
    menu.setFont(font)
    menu.setStyleSheet(modern_menu_stylesheet(appearance, dark=dark))
    border = ModernHairlineBorder(menu)

    def sync_border() -> None:
        border.setGeometry(menu.rect())
        border.show()
        border.raise_()

    menu.aboutToShow.connect(
        lambda menu=menu: QTimer.singleShot(0, menu, sync_border)
    )
    menu._modern_hairline_border = border


class ModernHairlineBorder(QWidget):
    """一像素描边，并补上右下角那 2px 的差值。

    Qt 的 QSS 只吃统一的 border-radius（四角分开写不生效），所以底色仍按左上角
    半径画；右下角比它"更方"的那条月牙，由这里用同一份底色补齐，再沿四角路径
    描边。
    """

    def __init__(self, parent: QMenu) -> None:
        super().__init__(parent)
        self.setObjectName("modernHairlineBorder")
        self.setProperty("physicalPixelWidth", 1)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        dpr = max(1.0, self.devicePixelRatioF())
        inset = 0.5 / dpr
        menu = self.parent()
        corner = menu.property("modernCornerRadius")
        radius = 12.0 if corner is None else float(corner)
        bottom_right = menu.property("modernBottomRightRadius")
        br = radius if bottom_right is None else float(bottom_right)
        rect = QRectF(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset)
        target = _rounded_path(rect, radius, radius, br, radius)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if br < radius:
            base = QPainterPath()
            base.addRoundedRect(rect, radius, radius)
            fill = menu.property("modernBackgroundColor")
            if fill is not None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawPath(target.subtracted(base))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#555555" if menu.property("modernDark") else "#d8d8d8"), 1.0 / dpr))
        painter.drawPath(target)


class ModernCheckLayer(QWidget):
    """One menu-sized state layer; right padding reserves its fixed slot."""

    def __init__(self, parent: QMenu) -> None:
        super().__init__(parent)
        self.setObjectName("modernCheckLayer")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        menu = self.parentWidget()
        if not isinstance(menu, QMenu):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for action in menu.actions():
            if not action.isCheckable() or not action.isChecked() or not action.isVisible():
                continue
            rect = menu.actionGeometry(action)
            center = QPointF(self.width() - 21.0, rect.center().y())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1677e8"))
            painter.drawEllipse(QRectF(center.x() - 6.5, center.y() - 6.5, 13.0, 13.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 1.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(center.x() - 3.7, center.y()), QPointF(center.x() - 1.2, center.y() + 2.4))
            painter.drawLine(QPointF(center.x() - 1.2, center.y() + 2.4), QPointF(center.x() + 3.8, center.y() - 2.8))


def install_modern_check_indicators(menu: QMenu) -> None:
    """Reserve and repaint a stable right-side checked-state slot."""
    menu.setProperty("paintChecksOnRight", True)
    layer = ModernCheckLayer(menu)

    def sync_layer() -> None:
        layer.setGeometry(menu.rect())
        layer.show()
        layer.raise_()
        layer.update()

    menu.aboutToShow.connect(
        lambda menu=menu: QTimer.singleShot(0, menu, sync_layer)
    )
    menu.aboutToHide.connect(layer.hide)
    menu._modern_check_layer = layer
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            install_modern_check_indicators(submenu)
        if action.isCheckable():
            action.toggled.connect(lambda _checked, layer=layer: layer.update())
