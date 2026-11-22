from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QPalette

LIGHT = {
    "page": "#fcfcfd",
    "sidebar": "#f7f7f8",
    "card": "#ffffff",
    "border": "#e2e4e8",
    "divider": "#eceef1",
    "selected": "#e3e5e8",
    "hover": "#f4f5f6",
    "accent": "#0a84ff",
    "accent_hover": "#0a78e6",
    "title": "#171717",
    "section": "#2b2b2b",
    "label": "#252525",
    "hint": "#777777",
    "status": "#777b80",
    "input_bg": "#ffffff",
    "input_border": "#d8dbe0",
    "disabled": "#9a9aa3",
}

DARK = {
    "page": "#202024",
    "sidebar": "#26262b",
    "card": "#2a2a30",
    "border": "#3a3a42",
    "divider": "#33333a",
    "selected": "#3a3a46",
    "hover": "#303036",
    "accent": "#0a84ff",
    "accent_hover": "#0a78e6",
    "title": "#f0f0f5",
    "section": "#d8d8e0",
    "label": "#e0e0e6",
    "hint": "#9a9aa3",
    "status": "#9a9aa3",
    "input_bg": "#242429",
    "input_border": "#3f3f48",
    "disabled": "#77777f",
}

_QSS = """
QDialog#settingsDialog {
    background: %(page)s;
}
QWidget#sidebarPanel {
    background: %(sidebar)s;
}
QWidget#settingsPage, QWidget#settingsPageContent, QStackedWidget#settingsPages {
    background: transparent;
}
QLineEdit#settingsSearch, QLineEdit#valueField, QLineEdit#apiField, QLineEdit#customMirrorPrefix {
    background: %(input_bg)s;
    border: 1px solid %(input_border)s;
    border-radius: 7px;
    min-height: 30px;
    padding: 0 8px;
    color: %(label)s;
}
QLineEdit#settingsSearch:focus, QLineEdit#customMirrorPrefix:focus {
    border: 1px solid %(accent)s;
}
QLineEdit#customMirrorPrefix:disabled {
    background: %(sidebar)s;
    color: %(hint)s;
}
QListWidget#settingsNav {
    background: transparent;
    border: none;
    outline: 0;
    font-size: %(base)spx;
}
QListWidget#settingsNav::item {
    color: %(label)s;
    min-height: %(nav_row)spx;
    padding: 0 10px 0 12px;
    border-left: 3px solid transparent;
    border-radius: 6px;
}
QListWidget#settingsNav::item:hover {
    background: %(hover)s;
}
QListWidget#settingsNav::item:selected {
    background: %(selected)s;
    border-left: 3px solid %(accent)s;
    color: %(title)s;
}
QListWidget#extensionsNav {
    background: transparent;
    border: none;
    outline: 0;
    font-size: %(base)spx;
}
QListWidget#extensionsNav::item {
    color: %(label)s;
    min-height: %(ext_nav_row)spx;
    padding: 0 10px 0 12px;
    border-left: 3px solid transparent;
    border-radius: 6px;
}
QListWidget#extensionsNav::item:hover {
    background: %(hover)s;
}
QListWidget#extensionsNav::item:selected {
    background: %(selected)s;
    border-left: 3px solid %(accent)s;
    color: %(title)s;
}
QListWidget#extensionsNav::item:disabled {
    color: %(section)s;
}
QWidget#extensionsBrowser, QStackedWidget#extensionsPages {
    background: transparent;
}
QLabel#pageTitle {
    font-size: %(page_title)spx;
    font-weight: 600;
    color: %(title)s;
}
QTreeWidget#menuEditorTree {
    background: %(input_bg)s;
    border: 1px solid %(input_border)s;
    border-radius: 8px;
    color: %(label)s;
    outline: 0;
}
QTreeWidget#menuEditorTree::item {
    min-height: 26px;
}
QTreeWidget#menuEditorTree::item:selected {
    background: %(selected)s;
    color: %(title)s;
}
QLabel#groupTitle {
    font-size: %(base_2)spx;
    font-weight: 700;
    color: %(title)s;
}
QLabel#sectionTitle {
    font-size: %(base)spx;
    font-weight: 600;
    color: %(section)s;
}
QLabel#subsectionTitle {
    font-size: %(base_1)spx;
    font-weight: 600;
    color: %(section)s;
}
QWidget[subSection="true"] {
    margin-left: 10px;
}
QLabel#settingLabel {
    font-size: %(base)spx;
    font-weight: 500;
    color: %(label)s;
}
QLabel#settingHint {
    font-size: %(base_1)spx;
    color: %(hint)s;
}
QLabel#searchStatus {
    font-size: %(base_2)spx;
    color: %(status)s;
}
QLabel#versionFooter {
    font-size: %(base_2)spx;
    color: %(status)s;
}
QLabel#screenInfo {
    font-size: %(base)spx;
    color: %(label)s;
}
QFrame#settingsCard {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 12px;
}
QPushButton#foldHeader {
    text-align: left;
    background: transparent;
    border: none;
    padding: 4px 2px;
    color: %(section)s;
    font-size: %(base)spx;
    font-weight: 600;
}
QPushButton#foldHeader:hover {
    color: %(accent)s;
}
QPushButton#foldHeader:focus {
    border: 1px solid %(accent)s;
    border-radius: 6px;
}
QLabel#fanName {
    color: %(label)s;
    font-size: %(base_1)spx;
    padding: 1px 4px;
}
QWidget#signaturePad {
    background: %(input_bg)s;
    border: 1px dashed %(input_border)s;
    border-radius: 10px;
}
QLabel#signaturePreview {
    background: transparent;
}
QListWidget#creditList {
    background: %(input_bg)s;
    border: 1px solid %(input_border)s;
    border-radius: 8px;
    color: %(label)s;
}
QFrame#cardDivider {
    background: %(divider)s;
    border: none;
    min-height: 1px;
    max-height: 1px;
}
QComboBox, QSpinBox {
    background: %(input_bg)s;
    border: 1px solid %(input_border)s;
    border-radius: 7px;
    min-height: 30px;
    padding: 0 8px;
    color: %(label)s;
}
QSpinBox:focus {
    border: 1px solid %(accent)s;
}
QSlider::groove:horizontal {
    height: 4px;
    background: %(input_border)s;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: %(accent)s;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: %(accent)s;
}
QSlider::handle:horizontal:hover {
    background: %(accent_hover)s;
}
QComboBox {
    padding-left: 10px;
    padding-right: 30px;
}
QComboBox::drop-down {
    width: 26px;
}
QComboBox QAbstractItemView {
    padding: 4px 0;
}
QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 6px 10px;
}

QComboBox:focus {
    border: 1px solid %(accent)s;
}
QComboBox QAbstractItemView {
    background: %(card)s;
    border: 1px solid %(border)s;
    color: %(label)s;
    selection-background-color: %(selected)s;
    selection-color: %(title)s;
}
QPushButton {
    background: %(card)s;
    border: 1px solid %(input_border)s;
    border-radius: 7px;
    min-height: 26px;
    padding: 0 14px;
    color: %(label)s;
}
QPushButton:hover {
    background: %(hover)s;
}
QPushButton:pressed {
    background: %(selected)s;
}
QPushButton:disabled {
    color: %(disabled)s;
}
QPushButton#primaryButton {
    background: %(accent)s;
    border-color: %(accent)s;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: %(accent_hover)s;
    border-color: %(accent_hover)s;
}
QPushButton#primaryButton:disabled {
    background: %(selected)s;
    border-color: %(border)s;
    color: %(disabled)s;
}
QListWidget#menuList, QListWidget#modsList, QListWidget#apiServiceList {
    background: %(input_bg)s;
    border: 1px solid %(input_border)s;
    border-radius: 7px;
    padding: 4px;
    color: %(label)s;
}
QListWidget#menuList::item, QListWidget#modsList::item, QListWidget#apiServiceList::item {
    padding: 6px 8px;
    border-radius: 5px;
}
QListWidget#menuList::item:selected, QListWidget#modsList::item:selected, QListWidget#apiServiceList::item:selected {
    background: %(selected)s;
    color: %(title)s;
}
QPlainTextEdit {
    background: %(input_bg)s;
    border: 1px solid %(input_border)s;
    border-radius: 7px;
    color: %(label)s;
}
QPlainTextEdit#apiLocalGuidePrompt {
    padding: 8px 10px;
}
QPlainTextEdit#apiLocalGuidePrompt:focus {
    border: 1px solid %(accent)s;
}
QCheckBox {
    color: %(label)s;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox::indicator:unchecked {
    border: 1px solid %(input_border)s;
    border-radius: 4px;
    background: %(input_bg)s;
}
QCheckBox::indicator:unchecked:hover {
    border-color: %(accent)s;
}
QCheckBox:disabled {
    color: %(disabled)s;
}
QWidget#settingsTaskTabBar {
    background: %(hover)s;
    border: none;
    border-radius: 8px;
}
QPushButton#settingsTaskTab {
    min-height: 32px;
    padding: 0 16px;
    font-size: %(base)spx;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: %(hint)s;
}
QPushButton#settingsTaskTab:hover {
    background: %(selected)s;
    color: %(title)s;
}
QPushButton#settingsTaskTab:checked {
    background: %(card)s;
    border-color: %(border)s;
    color: %(title)s;
    font-weight: 600;
}
QPushButton#settingsTaskTab:focus {
    border: 2px solid %(accent)s;
}
QScrollArea#settingsTaskTabScroll {
    background: transparent;
    border: none;
}
QScrollArea#settingsTaskTabScroll > QWidget > QWidget {
    background: transparent;
}
QToolButton#settingsTaskTabArrow {
    min-width: 20px;
    max-width: 20px;
    min-height: 32px;
    border: none;
    border-radius: 6px;
    background: %(hover)s;
    color: %(hint)s;
    font-size: 15px;
}
QToolButton#settingsTaskTabArrow:hover {
    background: %(selected)s;
    color: %(title)s;
}
"""


def is_dark(app=None) -> bool:
    application = app or QGuiApplication.instance()
    if application is None:
        return False
    color = application.palette().color(QPalette.ColorRole.Window)
    return color.lightness() < 128


def nav_row_heights(font_size: int = 13) -> tuple[int, int]:
    """两侧导航的行高 token：``(主侧栏, 扩展管理导航)``。

    字号 13 时返回 ``(34, 32)``——与改动前逐像素一致（用户的「当前显示范围是
    下限」口径）；字号变大时行高跟着长，否则 18px 的行内文字会被 min-height
    切掉上下缘（`petcore/AGENTS.md` 菜单侧 `max(18, 字号+5)` 是同一类修正）。
    """
    try:
        base = max(10, min(18, int(font_size)))
    except (TypeError, ValueError):
        base = 13
    return max(34, base + 21), max(32, base + 19)


def stylesheet(dark: bool = False, font_size: int = 13) -> str:
    """设置页样式表；``font_size`` 是正文基准字号（10–18），其余字号按它推导。"""
    try:
        base = max(10, min(18, int(font_size)))
    except (TypeError, ValueError):
        base = 13
    nav_row, ext_nav_row = nav_row_heights(base)
    colors = dict(DARK if dark else LIGHT)
    colors.update(
        base=base,
        base_1=max(9, base - 1),
        base_2=max(9, base - 2),
        page_title=base + 9,
        nav_row=nav_row,
        ext_nav_row=ext_nav_row,
    )
    return _QSS % colors
