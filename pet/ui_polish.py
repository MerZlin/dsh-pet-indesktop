"""Shared local UI polish. No network access or preference mutations."""

from __future__ import annotations
import os
from PySide6.QtCore import QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QFrame

DARK = {
    "bg": "#101824",
    "surface": "#192639",
    "grid": "#2a3b53",
    "text": "#edf3fc",
    "muted": "#a4b4ca",
    "blue": "#78adff",
    "pill": "#243f61",
    "border": "#30435e",
    "field": "#121e30",
    "hover": "#24354e",
    "sidebar": "#131e2e",
    "track": "#2a3b53",
    "primary": "#397df0",
}
LIGHT = {
    "bg": "#f4f7fc",
    "surface": "#ffffff",
    "grid": "#dfe7f2",
    "text": "#20344f",
    "muted": "#627790",
    "blue": "#2d6fd4",
    "pill": "#e4efff",
    "border": "#ccd9eb",
    "field": "#f6f9fe",
    "hover": "#eaf1fc",
    "sidebar": "#edf3fc",
    "track": "#e2eaf6",
    "primary": "#3273dc",
}


def style_of(config):
    return "light" if config.get("dynamic_island", {}).get("style") == "light" else "dark"


def colors(style="dark"):
    return dict(LIGHT if style == "light" else DARK)


def ui_text(key):
    from .language_ui import language

    strings = {
        "trend_hint": ("點選額度查看趨勢", "Click a quota row for trends", "点击额度查看趋势"),
        "settings": ("桌寵設定", "Pet settings", "桌宠设置"),
        "account": ("使用 Codex 帳號", "Using your Codex account", "使用 Codex 账号"),
        "remaining": ("剩餘額度", "Quota remaining", "剩余额度"),
        "upcoming": ("待記錄", "Upcoming", "待记录"),
        "updated": ("更新", "Updated", "更新"),
        "now": ("現在", "Now", "现在"),
    }
    return strings[key][{"zh_TW": 0, "en": 1, "zh_CN": 2}.get(language(), 0)]


def apply_palette(widget, c):
    pal = widget.palette()
    for role, key in (
        (QPalette.ColorRole.Window, "bg"),
        (QPalette.ColorRole.Base, "field"),
        (QPalette.ColorRole.AlternateBase, "surface"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "surface"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.Highlight, "primary"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
    ):
        pal.setColor(role, QColor(c[key]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    widget.setPalette(pal)


def titlebar(widget, dark=True):
    if os.name != "nt" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return
    try:
        import ctypes

        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(widget.winId())), 20, ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError, RuntimeError):
        pass


def tint_icon(icon, color, size):
    if icon.isNull():
        return icon
    image = icon.pixmap(size)
    painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(image.rect(), QColor(color))
    painter.end()
    return QIcon(image)


def common_qss(c):
    return (
        """
    QToolTip { color: TEXT; background: SURFACE; border: 1px solid BORDER; padding: 8px 10px; border-radius: 8px; }
    QScrollBar:vertical { width: 9px; background: transparent; margin: 3px 1px; }
    QScrollBar::handle:vertical { background: BORDER; border-radius: 4px; min-height: 30px; }
    QScrollBar::handle:vertical:hover { background: MUTED; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QScrollBar:horizontal { height: 9px; background: transparent; }
    QScrollBar::handle:horizontal { background: BORDER; border-radius: 4px; min-width: 30px; }
    QPushButton:focus, QToolButton:focus { border: 1px solid BLUE; }
    """.replace("TEXT", c["text"])
        .replace("SURFACE", c["surface"])
        .replace("BORDER", c["border"])
        .replace("MUTED", c["muted"])
        .replace("BLUE", c["blue"])
    )


def settings_theme(dialog):
    from .settings_theme_qss import _settings_stylesheet
    from .settings_widgets import ModernSelect, ToggleSwitch
    from .independent_ui import settings_style

    style = dialog.settings_style_select.currentData() if hasattr(dialog, "settings_style_select") else settings_style(dialog.config)
    c = colors("light" if style == "glass" else style)
    dark = style == "dark"
    dialog.setProperty("settingsDark", dark)
    dialog.setProperty("modernDark", dark)
    apply_palette(dialog, c)
    titlebar(dialog, dark)
    qss = """
    QDialog { background: BG; color: TEXT; font-family: "Segoe UI", "Microsoft JhengHei UI"; font-size: 13px; }
    QFrame#sidebarPane { background: SIDEBAR; border-right: 1px solid BORDER; }
    QStackedWidget { background: BG; }
    QLineEdit#settingsSearch { background: FIELD; color: TEXT; border: 1px solid BORDER; border-radius: 10px; min-height: 34px; padding: 0 10px; }
    QLineEdit#settingsSearch:focus { border: 1px solid BLUE; padding: 0 10px; }
    QPushButton#saveAndExit { min-height: 34px; background: PILL; color: BLUE; border: 1px solid BORDER; border-radius: 10px; font-weight: 600; padding: 0 12px; }
    QPushButton#saveAndExit:hover { background: HOVER; border-color: BLUE; }
    QListWidget#settingsSidebar { color: MUTED; font-size: 13px; }
    QListWidget#settingsSidebar::item { min-height: 32px; padding: 5px 12px; border-radius: 10px; color: MUTED; }
    QListWidget#settingsSidebar::item:hover { background: HOVER; color: TEXT; }
    QListWidget#settingsSidebar::item:selected { background: PILL; color: BLUE; font-weight: 600; }
    QLabel#pageTitle { font-size: 24px; font-weight: 600; color: TEXT; }
    QLabel#sectionTitle { font-size: 13px; font-weight: 600; color: MUTED; padding-top: 4px; }
    QFrame#settingsCard { background: SURFACE; border: 1px solid BORDER; border-radius: 14px; }
    QFrame#cardSeparator { background: BORDER; }
    QLabel#settingLabel { color: TEXT; font-size: 14px; font-weight: 500; }
    QLabel#settingHint, QLabel#searchStatus { color: MUTED; font-size: 12px; }
    QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: MUTED; }
    SettingRow[searchMatch="true"] { background: PILL; border-radius: 10px; }
    QWidget#settingsTaskTabBar { background: FIELD; border: 1px solid BORDER; border-radius: 10px; }
    QPushButton#settingsTaskTab { color: MUTED; min-height: 30px; background: transparent; border-radius: 8px; }
    QPushButton#settingsTaskTab:checked { background: PILL; color: BLUE; border: 1px solid BORDER; }
    QPushButton#advancedSectionToggle { background: SURFACE; color: TEXT; border-color: BORDER; border-radius: 12px; }
    QPushButton#advancedSectionToggle:hover { background: HOVER; }
    QPushButton { background: SURFACE; color: TEXT; border: 1px solid BORDER; border-radius: 8px; }
    QPushButton:hover { background: HOVER; border-color: BLUE; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QComboBox { background: FIELD; color: TEXT; border: 1px solid BORDER; border-radius: 8px; selection-background-color: PRIMARY; }
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: MUTED; }
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus { border-color: BLUE; }
    QCheckBox, QRadioButton, QGroupBox { color: TEXT; }
    QMenu { background: SURFACE; color: TEXT; border: 1px solid BORDER; border-radius: 10px; padding: 6px; }
    QMenu::item:selected { background: PILL; color: BLUE; border-radius: 6px; }
    """
    for token in sorted(c, key=len, reverse=True):
        qss = qss.replace(token.upper(), c[token])
    if style == "glass":
        qss += "QDialog { background: #eaf3fc; } QFrame#sidebarPane { background: #e3eef9; } QFrame#settingsCard { background: rgba(255,255,255,218); } QLineEdit#settingsSearch { background: rgba(255,255,255,190); }"
    dialog.setProperty("dshSettingsTheme", style)
    dialog.setStyleSheet(_settings_stylesheet("light" if style == "glass" else style) + qss + common_qss(c))
    for control in dialog.findChildren(ModernSelect):
        if control._popup is not None:
            control._popup.setStyleSheet(control.popupStyleSheet())
        control.update()
    for control in dialog.findChildren(ToggleSwitch):
        control.update()
    for button in (dialog.save_exit_button,):
        button.setIcon(tint_icon(button.icon(), c["blue"], button.iconSize()))
    for i in range(dialog.sidebar.count()):
        item = dialog.sidebar.item(i)
        item.setIcon(tint_icon(item.icon(), c["muted"], dialog.sidebar.iconSize()))


def settings_layout(dialog):
    from .settings_widgets import SettingRow

    dialog.resize(max(dialog.width(), 940), max(dialog.height(), 650))
    sidebar = dialog.findChild(QFrame, "sidebarPane")
    if sidebar is not None:
        sidebar.setProperty("refinedSidebar", True)
        sidebar.setFixedWidth(270 if dialog.config.get("ui_language") == "en" else 200)
    dialog.sidebar.setSpacing(3)
    for row in dialog.findChildren(SettingRow):
        row.layout().setContentsMargins(18, 13, 18, 13)
    settings_theme(dialog)


def chat_style(window):
    from pathlib import Path
    from .independent_ui import chat_window_style

    style = chat_window_style(window.config)
    c = colors("light" if style == "glass" else style)
    dark = style == "dark"
    window.setProperty("dshChatWindowTheme", style)
    window.setProperty("modernDark", dark)
    apply_palette(window, c)
    # The existing native sheet supplies layout/behavior selectors. Keep its
    # geometry rules, then set each reading surface explicitly below.
    from .chat import widgets

    base = Path(widgets.__file__).with_name("modern_styles.qss").read_text(encoding="utf-8")
    base = base.replace("@ACCENT@", c["primary"])
    overlay = "rgba(15,25,42,205)" if dark else "rgba(245,249,255,212)"
    surface = "rgba(25,38,57,235)" if dark else "rgba(255,255,255,238)"
    if style == "glass":
        overlay = "rgba(237,246,255,190)"
        surface = "rgba(255,255,255,212)"
    qss = """
    QDialog#chat-window { color: TEXT; font-family: "Segoe UI", "Microsoft JhengHei UI"; font-size: 14px; }
    QFrame#phone-shell { background: OVERLAY; border: 1px solid BORDER; border-radius: 18px; }
    QFrame#deepseek-sidebar { background: SIDEBAR; border-right: 1px solid BORDER; border-top-left-radius: 17px; border-bottom-left-radius: 17px; }
    QFrame#deepseek-sidebar[overlayDrawer="true"] { background: SIDEBAR; border-color: BORDER; }
    QFrame#chat-main { background: transparent; }
    QFrame#chat-main-header { background: SIDEBAR; border-bottom: 1px solid BORDER; min-height: 62px; border-top-right-radius: 17px; }
    QLabel#brand-label { color: BLUE; font-size: 17px; }
    QLabel#title-label { color: TEXT; font-size: 15px; }
    QLabel#subtitle-label, QLabel#provider-label, QLabel#status-label { color: MUTED; font-size: 11px; }
    QPushButton#new-conversation-button { background: PILL; color: BLUE; border: 1px solid BORDER; border-radius: 11px; min-height: 36px; max-height: 40px; }
    QPushButton#new-conversation-button:hover { background: HOVER; border-color: BLUE; }
    QLabel#session-section-title { color: MUTED; font-size: 12px; }
    QListWidget#session-list { color: TEXT; }
    QListWidget#session-list::item { min-height: 36px; border-radius: 10px; }
    QListWidget#session-list::item:disabled { color: MUTED; }
    QListWidget#session-list::item:hover { background: HOVER; }
    QListWidget#session-list::item:selected { background: PILL; color: BLUE; }
    QLabel#session-row-title { color: TEXT; }
    QFrame#sidebar-footer { background: transparent; border-top: 1px solid BORDER; }
    QToolButton#follow-pet-button, QToolButton#delete-session-button, QToolButton#clear-session-button { color: MUTED; min-height: 32px; }
    QToolButton:hover { background: HOVER; }
    QToolButton#window-close-button:hover { background: #653449; }
    QScrollArea#message-scroll, QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
    QWidget#message-view, QWidget#message-timeline { background: transparent; border: none; }
    QLabel#empty-state { color: MUTED; font-size: 15px; }
    QFrame#message-surface[role="assistant"] { background: CARD; border: 1px solid BORDER; border-radius: 14px; padding: 12px 14px; }
    QFrame#message-surface[role="user"] { background: PILL; border: 1px solid BORDER; border-radius: 14px; padding: 10px 14px; }
    QLabel#bubble-body { color: TEXT; font-size: 14px; }
    QLabel#bubble-meta, QLabel#bubble-status { color: MUTED; }
    QFrame#message-surface[state="error"] { background: #492b39; border: 1px solid #c67885; }
    QFrame#message-surface[state="error"] QLabel#bubble-body { color: #ffe1e7; }
    QFrame#message-bubble[state="stopped"] { background: transparent; }
    QFrame#message-tools QToolButton { color: MUTED; }
    QFrame#message-tools QToolButton:hover { background: HOVER; }
    QFrame#floating-composer { background: transparent; }
    QFrame#chat-composer { background: SURFACE; border: 1px solid BORDER; border-radius: 16px; }
    QPlainTextEdit#chat-input { color: TEXT; background: transparent; font-size: 14px; selection-background-color: PRIMARY; }
    QLabel#composer-hint { color: MUTED; font-size: 11px; }
    QToolButton#composer-attach-button { color: MUTED; }
    QToolButton#send-button { background: PRIMARY; color: #ffffff; }
    QToolButton#send-button:hover { background: #5595ff; }
    QToolButton#send-button:disabled { background: BORDER; }
    QToolButton#send-button[busy="true"] { background: #b57825; }
    QFrame#attachment-chip { background: FIELD; border-color: BORDER; }
    QLabel#attachment-name { color: TEXT; font-size: 11px; }
    QLabel#attachment-meta { color: MUTED; font-size: 10px; }
    """.replace("OVERLAY", overlay).replace("CARD", surface)
    for token in sorted(c, key=len, reverse=True):
        qss = qss.replace(token.upper(), c[token])
    if style == "glass":
        qss += 'QFrame#deepseek-sidebar { background: rgba(229,240,254,232); } QFrame#chat-main-header { background: rgba(241,248,255,230); } QFrame#chat-composer { background: rgba(247,251,255,226); } QFrame#message-surface[role="user"] { background: rgba(217,234,255,232); }'
    window.setStyleSheet(base + qss + common_qss(c))
    window.accent_color = c["primary"]
    polish_chat_icons(window, c)


def polish_chat_icons(widget, c=None):
    host = widget.window()
    if host.objectName() == "chat-window" and hasattr(host, "config"):
        from .independent_ui import chat_window_style

        style = chat_window_style(host.config)
        c = colors("light" if style == "glass" else style)
    c = c or colors("dark")
    for button in widget.findChildren(QAbstractButton):
        if not button.icon().isNull():
            color = "#ffffff" if button.objectName() == "send-button" else c["muted"]
            button.setIcon(tint_icon(button.icon(), color, button.iconSize()))


def quick_style(bubble):
    from .independent_ui import quick_chat_style

    style = quick_chat_style(bubble.config)
    c = colors("light" if style == "glass" else style)
    bubble.setProperty("dshQuickChatTheme", style)
    bubble._preset = dict(
        bubble._preset,
        background=("#dcf3f8fe" if style == "glass" else c["surface"]),
        foreground=c["text"],
        border=("#b8cfe9" if style == "glass" else c["border"]),
        shadow="#40101a2d",
        radius=18,
    )
    apply_palette(bubble, c)
    bubble.layout().setContentsMargins(20, 18, 20, 18)
    bubble.layout().setSpacing(12)
    bubble.title_label.setFont(QFont("Microsoft JhengHei UI", 11, QFont.Weight.DemiBold))
    bubble.output.setFont(QFont("Microsoft JhengHei UI", 10))
    bubble.input.setMinimumHeight(32)
    bubble.send_btn.setMinimumHeight(32)
    bubble.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    qss = """
    QLabel { color: TEXT; background: transparent; border: none; }
    QLabel#quick-chat-hint { color: MUTED; font-size: 12px; }
    QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
    QPushButton { background: PILL; color: TEXT; border: 1px solid BORDER; border-radius: 8px; padding: 3px 8px; }
    QPushButton:hover { background: HOVER; border-color: BLUE; }
    QPushButton#quick-chat-send { background: PRIMARY; color: #ffffff; padding: 4px 12px; border: none; }
    QPushButton#quick-chat-send:hover { background: #5595ff; }
    QPushButton#quick-chat-close { background: transparent; border: none; color: MUTED; font-size: 18px; padding: 0; }
    QPushButton#quick-chat-close:hover { background: HOVER; color: TEXT; }
    QLineEdit { background: FIELD; color: TEXT; border: 1px solid BORDER; border-radius: 10px; padding: 4px 10px; selection-background-color: PRIMARY; }
    QLineEdit:focus { border-color: BLUE; }
    """
    for token in sorted(c, key=len, reverse=True):
        qss = qss.replace(token.upper(), c[token])
    bubble.setStyleSheet(qss + common_qss(c))


def island_palette(island, original):
    if island._cfg.get("style", "dark") != "dark":
        return original
    opacity = island._opacity()
    gradient = QLinearGradient(0, 0, 0, island.height())
    gradient.setColorAt(0, QColor(28, 43, 64, round(248 * opacity)))
    gradient.setColorAt(1, QColor(16, 25, 40, round(246 * opacity)))
    return gradient, QColor(DARK["text"]), QColor(DARK["muted"])


def island_card(island):
    if not hasattr(island, "_card_box"):
        return
    c = colors("light" if island._cfg.get("style") in ("light", "glass") else "dark")
    island._card_box.layout().setContentsMargins(18, 9, 18, 15)
    island._card_box.layout().setSpacing(6)
    island._card_balance_label.setStyleSheet("color: %s; font-size: 14px; font-weight: 600;" % c["text"])
    for label in (island._card_tier_label, island._card_message_label):
        label.setStyleSheet("color: %s; font-size: 12px;" % c["muted"])
    island._card_message_label.setMaximumHeight(38)
    if str(island.config.get("chat", {}).get("active_provider", "")).startswith(("codex-gpt", "codex-dsh")):
        island._card_balance_label.setText("GPT · Codex")
        island._card_tier_label.setText(ui_text("account"))
    for label in (island._card_balance_label, island._card_tier_label):
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    island._card_tier_label.setVisible(bool(island._card_tier_label.text().strip()))
    island._card_message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for button in (island._card_toggle_btn, island._card_chat_btn, island._card_settings_btn):
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(30)
        button.setStyleSheet(
            "QPushButton { color: %s; background: %s; border: 1px solid %s; border-radius: 9px; padding: 0 10px; font-size: 12px; } QPushButton:hover { background: %s; border-color: %s; } QPushButton:pressed { background: %s; }"
            % (c["text"], c["field"], c["border"], c["hover"], c["blue"], c["pill"])
        )
    from .language_ui import language

    locale = language()
    island._card_toggle_btn.setText(
        ("Hide pet" if island._pet_visible else "Show pet")
        if locale == "en"
        else ("隱藏桌寵" if island._pet_visible else "顯示桌寵")
        if locale == "zh_TW"
        else ("隐藏桌宠" if island._pet_visible else "显示桌宠")
    )
    if not island._pet_visible:
        island._card_toggle_btn.setStyleSheet(
            "QPushButton { color: #ffffff; background: %s; border: 1px solid %s; border-radius: 9px; padding: 0 10px; font-size: 12px; } QPushButton:hover { background: %s; }"
            % (c["primary"], c["primary"], c["blue"])
        )
    island._card_chat_btn.setStyleSheet(island._card_chat_btn.styleSheet() + "QPushButton { color: %s; background: %s; }" % (c["blue"], c["pill"]))
    from .codex_work_status import refresh

    refresh(island)


def island_rest_size(island, size):
    extra = 0
    controller = getattr(island, "_codex_work_controller", None)
    if controller is not None:
        extra = max(0, controller.box.height() - 38)
    return QSize(size.width(), size.height() + 12 + extra) if island._mode == "expanded" else size


def paint_select(control):
    from .settings_widgets import _widget_dark, _draw_chevron

    c = colors("dark" if _widget_dark(control) else "light")
    painter = QPainter(control)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not control.isEnabled():
        painter.setOpacity(0.55)
    border = c["blue"] if control.hasFocus() else c["muted"] if control._hovered else c["border"]
    painter.setBrush(QColor(c["field"]))
    painter.setPen(QPen(QColor(border), 1.5 if control.hasFocus() else 1))
    painter.drawRoundedRect(QRectF(0.5, 0.5, control.width() - 1, control.height() - 1), 8, 8)
    painter.setPen(QColor(c["text"]))
    text = painter.fontMetrics().elidedText(control.currentText(), Qt.TextElideMode.ElideRight, control.width() - 34)
    painter.drawText(QRectF(10, 0, control.width() - 34, control.height()), Qt.AlignmentFlag.AlignVCenter, text)
    painter.end()
    _draw_chevron(control, control.height() / 2, down=True)


def settings_popup(widget, original):
    from .settings_widgets import _widget_dark

    c = colors("dark" if _widget_dark(widget) else "light")
    return original + (
        "QMenu#SettingsPopup { background: %s; color: %s; border-color: %s; } "
        "QMenu#SettingsPopup::item { color: %s; } "
        "QMenu#SettingsPopup::item:selected { background: %s; color: %s; }" % (c["surface"], c["text"], c["border"], c["text"], c["pill"], c["blue"])
    )
