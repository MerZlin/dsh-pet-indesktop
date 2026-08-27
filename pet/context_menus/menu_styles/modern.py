# -*- coding: utf-8 -*-
"""Compact macOS project-menu appearance used exclusively by modern layout."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .common import SYSTEM_FONT_STACK, system_ui_font

def modern_menu_stylesheet(appearance: dict | None = None, *, dark: bool = False) -> str:
    appearance = appearance or {}
    density = str(appearance.get("density") or "standard")
    radius = max(6, min(18, int(appearance.get("corner_radius") or 12)))
    vertical_padding = {"compact": 2, "standard": 3, "spacious": 5}.get(density, 3)
    separator_margin = {"compact": 3, "standard": 4, "spacious": 6}.get(density, 4)
    background = str(appearance.get("dark_background" if dark else "light_background") or ("#252525" if dark else "#ffffff"))
    foreground = str(appearance.get("dark_foreground" if dark else "light_foreground") or ("#f3f3f3" if dark else "#171717"))
    selected = str(appearance.get("dark_hover" if dark else "light_hover") or ("#3a3a3a" if dark else "#eeeeee"))
    selected_text = "#ffffff" if dark else "#111111"
    separator = "#484848" if dark else "#e5e5e5"
    disabled = "#787878" if dark else "#9a9a9a"
    font_size = max(10, min(18, int(appearance.get("ui_font_size") or 13)))
    requested_font = str(appearance.get("ui_font") or "system").replace('"', "")
    font_stack = SYSTEM_FONT_STACK if requested_font == "system" else f'"{requested_font}", {SYSTEM_FONT_STACK}'
    translucent = bool(appearance.get("translucent", True))
    opacity = max(0.72, min(1.0, float(appearance.get("opacity") or 0.94)))
    if translucent and background.startswith("#") and len(background) == 7:
        red, green, blue = (int(background[index:index + 2], 16) for index in (1, 3, 5))
        background = f"rgba({red}, {green}, {blue}, {round(opacity * 255)})"
    return f"""
QMenu {{
    background-color: {background};
    color: {foreground};
    border: none;
    border-radius: {radius}px;
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


MODERN_MENU_STYLESHEET = modern_menu_stylesheet()


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
    radius = max(6, min(18, int(appearance.get("corner_radius") or 12)))
    menu.setObjectName("modernContextMenu")
    menu.setProperty("menuStyle", "modern")
    menu.setProperty("modernTheme", theme)
    menu.setProperty("modernDensity", density)
    menu.setProperty("modernCornerRadius", radius)
    menu.setProperty("modernAppearance", appearance)
    menu.setProperty("modernDark", dark)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
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
    """Draw a stable one-physical-pixel border, including on Retina screens."""

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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#555555" if self.parent().property("modernDark") else "#d8d8d8"), 1.0 / dpr))
        radius = float(self.parent().property("modernCornerRadius") or 12)
        painter.drawRoundedRect(
            QRectF(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset),
            radius,
            radius,
        )


class ModernEnabledIndicator(QWidget):
    """Right-aligned status mark independent from the action's function icon."""

    def __init__(self, parent: QMenu) -> None:
        super().__init__(parent)
        self.setObjectName("modernEnabledIndicator")
        self.setProperty("statusAlignment", "right")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(16, 16)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1677e8"))
        painter.drawEllipse(QRectF(1.5, 1.5, 13.0, 13.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor("#ffffff"),
                1.55,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(QPointF(4.6, 8.1), QPointF(7.0, 10.4))
        painter.drawLine(QPointF(7.0, 10.4), QPointF(11.7, 5.5))


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


def _install_indicators_for_menu(menu: QMenu) -> None:
    overlays: list[tuple[object, ModernEnabledIndicator]] = []
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            _install_indicators_for_menu(submenu)
        if not action.isCheckable():
            continue
        indicator = ModernEnabledIndicator(menu)
        overlays.append((action, indicator))

        def sync(checked: bool, indicator=indicator) -> None:
            indicator.setVisible(bool(checked) and menu.isVisible())

        action.toggled.connect(sync)
        sync(action.isChecked())

    def relayout() -> None:
        if not menu.isVisible():
            for _action, indicator in overlays:
                indicator.hide()
            return
        for action, indicator in overlays:
            rect = menu.actionGeometry(action)
            indicator.move(max(0, menu.width() - 28), rect.center().y() - 8)
            indicator.setVisible(action.isChecked())
            indicator.raise_()

    def hide_all() -> None:
        for _action, indicator in overlays:
            indicator.hide()

    menu.aboutToShow.connect(
        lambda menu=menu: QTimer.singleShot(0, menu, relayout)
    )
    menu.aboutToHide.connect(hide_all)
    menu._modern_check_indicators = overlays  # retain wrappers and aid diagnostics
    menu._modern_relayout_indicators = relayout


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
