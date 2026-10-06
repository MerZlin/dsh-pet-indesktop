"""Independent settings/menu surfaces and persisted native controls."""

from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QMenu, QScrollArea, QLabel

COPY = {
    "section": ("介面外觀", "Interface appearance", "界面外观"),
    "settings": ("設定視窗主題", "Settings window theme", "设置窗口主题"),
    "settings_hint": (
        "黑色、白色或玻璃；獨立於靈動島與右鍵選單。",
        "Black, white or glass; independent of the island and context menu.",
        "黑色、白色或玻璃；独立于灵动岛与右键菜单。",
    ),
    "bubble": ("氣泡主題", "Speech bubble theme", "气泡主题"),
    "bubble_hint": (
        "黑色、白色或玻璃；獨立保存，套用於歌詞與文字提醒。",
        "Black, white or glass; saved independently for lyrics and text notices.",
        "黑色、白色或玻璃；独立保存，应用于歌词与文字提醒。",
    ),
    "quota": ("Codex 用量主題", "Codex usage theme", "Codex 用量主题"),
    "quota_hint": (
        "獨立調整剩餘用量血條與趨勢圖的黑色、白色或玻璃外觀。",
        "Independent black, white or glass for quota bars and usage trends.",
        "独立调整剩余用量血条与趋势图的黑色、白色或玻璃外观。",
    ),
    "chat_window": ("對話視窗主題", "Chat window theme", "对话窗口主题"),
    "chat_window_hint": (
        "完整對話視窗的黑色、白色或玻璃外觀，獨立保存。",
        "Black, white or glass, saved independently for the full chat window.",
        "完整对话窗口的黑色、白色或玻璃外观，独立保存。",
    ),
    "quick_chat": ("快速對話主題", "Quick chat theme", "快速对话主题"),
    "quick_chat_hint": (
        "獨立調整快速對話浮窗；島內聊天沿用靈動島主題。",
        "Independent floating quick chat; embedded chat follows the island.",
        "独立调整快速对话浮窗；岛内聊天沿用灵动岛主题。",
    ),
    "menu": ("右鍵選單主題", "Context menu theme", "右键菜单主题"),
    "menu_hint": (
        "只調整右鍵肥魚的選單，不會改變設定視窗或靈動島。",
        "Changes the pet context menu only. Settings and island stay independent.",
        "只调整右键肥鱼的菜单，不会改变设置窗口或灵动岛。",
    ),
    "dark": ("黑色", "Black", "黑色"),
    "light": ("白色", "White", "白色"),
    "glass": ("玻璃", "Glass", "玻璃"),
    "work": ("Codex 工作狀態", "Codex work status", "Codex 工作状态"),
    "work_hint": (
        "靈動島顯示工作狀態；查看後收起完成與待操作通知。",
        "Show work status in the island. Viewed completion/input notices disappear.",
        "灵动岛显示工作状态；查看后收起完成与待操作通知。",
    ),
}


def text(key):
    from .language_ui import language

    return COPY[key][{"zh_TW": 0, "en": 1, "zh_CN": 2}.get(language(), 0)]


def settings_style(config):
    style = config.get("settings_ui_style")
    if style in ("dark", "light", "glass"):
        return style
    chosen = config.get("context_menu_appearance", {}).get("theme")
    if chosen in ("light", "dark"):
        return chosen
    return config.get("dynamic_island", {}).get("style", "dark")


def menu_style(config):
    style = config.get("menu_ui_style")
    if style in ("dark", "light", "glass"):
        return style
    original = config.get("context_menu_appearance", {}).get("theme", "system")
    if original in ("dark", "light"):
        return original
    from .settings_widgets import _system_dark

    return "dark" if _system_dark() else "light"


def surface_style(config, key):
    style = config.get(key)
    if style in ("dark", "light", "glass"):
        return style
    previous = config.get("dynamic_island", {}).get("style", "dark")
    return previous if previous in ("dark", "light") else "dark"


def quota_style(config):
    return surface_style(config, "quota_ui_style")


def quick_chat_style(config):
    return surface_style(config, "quick_chat_ui_style")


def chat_window_style(config):
    return surface_style(config, "chat_window_ui_style")


def add_controls(dialog):
    from .settings_widgets import ModernSelect, SettingRow, SettingsSection, ToggleSwitch
    from .ui_polish import settings_theme

    if hasattr(dialog, "settings_style_select"):
        return
    selector = ModernSelect(dialog, width=180)
    selector.setObjectName("dshSettingsTheme")
    for key in ("dark", "light", "glass"):
        selector.addItem(text(key), key)
    selector.setCurrentData(settings_style(dialog.config))
    dialog.settings_style_select = selector
    row = SettingRow("settings_ui_style", text("settings"), text("settings_hint"), selector)
    bubble_selector = ModernSelect(dialog, width=180)
    bubble_selector.setObjectName("dshBubbleTheme")
    for key in ("dark", "light", "glass"):
        bubble_selector.addItem(text(key), key)
    bubble_selector.setCurrentData(dialog.config.get("bubble_ui_style", "light"))
    dialog.bubble_theme_select = bubble_selector
    bubble_row = SettingRow("bubble_ui_style", text("bubble"), text("bubble_hint"), bubble_selector)
    quota_selector = ModernSelect(dialog, width=180)
    quota_selector.setObjectName("dshQuotaTheme")
    quick_selector = ModernSelect(dialog, width=180)
    quick_selector.setObjectName("dshQuickChatTheme")
    for key in ("dark", "light", "glass"):
        quota_selector.addItem(text(key), key)
        quick_selector.addItem(text(key), key)
    quota_selector.setCurrentData(quota_style(dialog.config))
    dialog.quota_theme_select = quota_selector
    quick_selector.setCurrentData(quick_chat_style(dialog.config))
    dialog.quick_chat_theme_select = quick_selector
    quota_row = SettingRow("quota_ui_style", text("quota"), text("quota_hint"), quota_selector)
    quick_row = SettingRow("quick_chat_ui_style", text("quick_chat"), text("quick_chat_hint"), quick_selector)
    chat_selector = ModernSelect(dialog, width=180)
    chat_selector.setObjectName("dshChatWindowTheme")
    for key in ("dark", "light", "glass"):
        chat_selector.addItem(text(key), key)
    chat_selector.setCurrentData(chat_window_style(dialog.config))
    dialog.chat_window_theme_select = chat_selector
    chat_row = SettingRow("chat_window_ui_style", text("chat_window"), text("chat_window_hint"), chat_selector)
    section = SettingsSection(text("section"), [row, bubble_row, quota_row, quick_row, chat_row], dialog)
    scroll = dialog.pages.widget(0).findChild(QScrollArea, "settingsScroll")
    content = scroll.widget()
    content.layout().insertWidget(1, section)
    dialog._search_rows.extend([row, bubble_row, quota_row, quick_row, chat_row])
    menu_select = dialog.menu_theme_select
    menu_select.blockSignals(True)
    menu_select.clear()
    for key in ("dark", "light", "glass"):
        menu_select.addItem(text(key), key)
    menu_select.setCurrentData(menu_style(dialog.config))
    menu_select.blockSignals(False)
    menu_row = dialog.findChild(SettingRow, "settingRow_menu_theme")
    menu_row.label.setText(text("menu"))
    menu_row.hint_label.setText(text("menu_hint"))
    switch = ToggleSwitch(dialog)
    switch.setChecked(bool(dialog.config.get("codex_work_status_enabled", True)))
    dialog.codex_work_status_switch = switch
    status_row = SettingRow("codex_work_status_enabled", text("work"), text("work_hint"), switch)
    from .ytmusic import append_row

    island_row = dialog.findChild(SettingRow, "settingRow_dynamic_island_enabled")
    if island_row is not None:
        append_row(dialog, island_row.parentWidget(), status_row)
    else:
        section.card.layout().addWidget(status_row)
        dialog._search_rows.append(status_row)
    selector.currentIndexChanged.connect(lambda _index: settings_theme(dialog))
    # Dynamic strings retain their own source and are refreshed on language changes.
    dialog._dsh_theme_rows = (row, menu_row, status_row, section, bubble_row, quota_row, quick_row, chat_row)
    dialog.ui_language_select.currentIndexChanged.connect(lambda _index: translate_controls(dialog))
    translate_controls(dialog)
    settings_theme(dialog)
    from .ui_preview import attach

    attach(dialog)
    from .custom_avatar import add_controls as avatar_controls

    avatar_controls(dialog)


def translate_controls(dialog):
    row, menu_row, status_row, section, bubble_row, quota_row, quick_row, chat_row = dialog._dsh_theme_rows
    for target, label, hint in (
        (row, "settings", "settings_hint"),
        (menu_row, "menu", "menu_hint"),
        (status_row, "work", "work_hint"),
        (bubble_row, "bubble", "bubble_hint"),
        (quota_row, "quota", "quota_hint"),
        (quick_row, "quick_chat", "quick_chat_hint"),
        (chat_row, "chat_window", "chat_window_hint"),
    ):
        target.label.setText(text(label))
        target.hint_label.setText(text(hint))
        for widget in (target.label, target.hint_label):
            widget.setProperty("_dsh_source_text", None)
            widget.setProperty("_dsh_rendered_text", None)
    heading = section.findChild(QLabel, "sectionTitle")
    if heading is not None:
        heading.setText(text("section"))
        heading.setProperty("_dsh_source_text", None)
        heading.setProperty("_dsh_rendered_text", None)
    for control in (
        dialog.settings_style_select,
        dialog.menu_theme_select,
        dialog.bubble_theme_select,
        dialog.quota_theme_select,
        dialog.quick_chat_theme_select,
        dialog.chat_window_theme_select,
    ):
        for index, (label, data) in enumerate(control._items):
            control._items[index] = (text(data), data)
        control.setText(control.currentText())
        control.update()


def save_controls(dialog, original):
    config = dialog.config
    old = config.__dict__.get("save")
    save = config.save

    def commit():
        config.set("settings_ui_style", dialog.settings_style_select.currentData())
        config.set("bubble_ui_style", dialog.bubble_theme_select.currentData())
        config.set("quota_ui_style", dialog.quota_theme_select.currentData())
        config.set("quick_chat_ui_style", dialog.quick_chat_theme_select.currentData())
        config.set("chat_window_ui_style", dialog.chat_window_theme_select.currentData())
        config.set("menu_ui_style", dialog.menu_theme_select.currentData())
        config.set("codex_work_status_enabled", dialog.codex_work_status_switch.isChecked())
        appearance = dict(config.get("context_menu_appearance", {}))
        appearance["theme"] = "light" if dialog.menu_theme_select.currentData() == "glass" else dialog.menu_theme_select.currentData()
        config.set("context_menu_appearance", appearance)
        return save()

    config.save = commit
    try:
        from .ui_preview import commit

        return commit(dialog, original)
    finally:
        if old is None:
            del config.save
        else:
            config.save = old


def menu_polish(root, pet):
    from .ui_polish import colors, tint_icon, apply_palette

    style = menu_style(pet.cfg)
    c = colors("light" if style == "glass" else style)
    dark = style == "dark"
    appearance = pet.cfg.get("context_menu_appearance", {})
    font = QFont(root.font())
    font.setPixelSize(int(appearance.get("ui_font_size") or 13))
    root.setFont(font)
    for menu in (root, *root.findChildren(QMenu)):
        menu.setProperty("modernDark", dark)
        menu.setProperty("dshMenuTheme", style)
        apply_palette(menu, c)
        background = "rgba(242,248,255,232)" if style == "glass" else c["surface"]
        menu.setStyleSheet(
            """QMenu { background: BG; color: TEXT; border: 1px solid BORDER; border-radius: 14px; padding: 7px; }
   QMenu::item { color: TEXT; min-height: 25px; padding: 6px 30px 6px 12px; margin: 1px 0; border-radius: 9px; }
   QMenu::item:selected { background: PILL; color: BLUE; }
   QMenu::item:disabled { color: MUTED; }
   QMenu::separator { height: 1px; background: BORDER; margin: 6px 9px; }
   QMenu::indicator { width: 0; height: 0; }
   """.replace("BG", background)
            .replace("TEXT", c["text"])
            .replace("BORDER", c["border"])
            .replace("PILL", c["pill"])
            .replace("BLUE", c["blue"])
            .replace("MUTED", c["muted"])
        )
        for action in menu.actions():
            if not action.icon().isNull():
                action.setIcon(tint_icon(action.icon(), c["muted"], __import__("PySide6.QtCore", fromlist=["QSize"]).QSize(18, 18)))
        # Qt's popup handle must retain transparency for rounded corners/acrylic.
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.update()
    root.setProperty("dshPolishedMenu", True)


def load_config(config):
    """Configuration is normalized by Config; only overlay reversible previews."""
    from .ui_preview import overlay_config

    overlay_config(config)
