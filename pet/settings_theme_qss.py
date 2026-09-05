# -*- coding: utf-8 -*-
"""Modern settings 主题 QSS 常量与组装。

从 modern_settings_dialog.py 纯机械搬移。_system_dark / BROWSER_CONTROL_STYLESHEET
在 settings_widgets 中定义，被 _settings_stylesheet 引用；_DARK_POPUP_OVERRIDE 亦属
settings_widgets（本模块未引用）。
"""
from __future__ import annotations

from .settings_widgets import _system_dark, BROWSER_CONTROL_STYLESHEET


_DARK_OVERRIDE = """
QDialog { background: #202024; color: #e4e4e9; }
QFrame#sidebarPane { background: #26262b; border-right: 1px solid #34343a; }
QStackedWidget { background: #202024; }
QLineEdit#settingsSearch { background: #2e2e35; color: #e4e4e9; }
QPushButton#saveAndExit { color: #e4e4e9; }
QPushButton#saveAndExit:hover { background: #33333c; }
QListWidget#settingsSidebar::item { color: #b8b8c0; }
QListWidget#settingsSidebar::item:hover { background: #2e2e36; color: #f0f0f5; }
QListWidget#settingsSidebar::item:selected { background: #3a3a46; color: #ffffff; }
QWidget#settingsTaskTabBar {
    background: #292930;
    border: none;
    border-radius: 8px;
}
QPushButton#settingsTaskTab {
    min-height: 26px;
    padding: 0 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #aaaab3;
}
QPushButton#settingsTaskTab:hover { background: #33333b; color: #f0f0f5; }
QPushButton#settingsTaskTab:checked {
    background: #41414b;
    border-color: #50505b;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#settingsTaskTab:focus { border: 2px solid #0a84ff; }
QLabel#pageTitle { color: #f0f0f5; }
QLabel#sectionTitle { color: #d8d8e0; }
QFrame#settingsCard { background: #2a2a30; border: 1px solid #3a3a42; }
QFrame#cardSeparator { background: #33333a; }
QLabel#settingLabel { color: #e0e0e6; }
QLabel#settingHint { color: #9a9aa3; }
QLabel#quickLaunchName { color: #e0e0e6; }
QLabel#quickLaunchDetail, QLabel#quickLaunchCount, QLabel#quickLaunchEmpty,
QLabel#menuLayoutEditorLabel, QLabel#menuLayoutPreviewLabel,
QLabel#menuLayoutEditorHint { color: #a8a8b0; }
QLabel#quickLaunchEmpty { background: #26262c; border-color: #3c3c44; }
QFrame#menuLayoutEditorPanel, QFrame#menuLayoutPreviewPanel {
    background: #26262c; border: 1px solid #3c3c44; border-radius: 10px;
}
QFrame#imagePreviewDrawer {
    background: #242429; border: none; border-left: 1px solid #44444d;
}
QScrollArea#imagePreviewScroll, QScrollArea#imagePreviewScroll > QWidget > QWidget,
QWidget#imageMasonryFlow { background: transparent; }
QLabel#imagePreviewTitle { color: #f0f0f5; font-size: 16px; font-weight: 600; }
QLabel#imagePreviewCount, QLabel#imagePreviewPath { color: #9999a2; }
QLabel#imagePreviewEmpty { color: #9999a2; }
QPushButton#imagePreviewClose { background: transparent; border: none; font-size: 20px; }
QPushButton#imagePreviewClose:hover { background: #393940; }
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #66666e; }
SettingRow[searchMatch="true"] { background: #2c3a4e; }
QListWidget#quickLaunchList { background: #26262c; border: 1px solid #3c3c44; }
QListWidget#quickLaunchList::item:selected { background: #3a3a46; color: #ffffff; }
QTreeWidget#menuLayoutTree, QTreeWidget#menuLayoutPreview {
    background: #2a2a30;
    border-color: #3a3a42;
}
QTreeWidget#menuLayoutTree::item:hover { background: #303036; }
QTreeWidget#menuLayoutTree::item:selected { background: #3a3a46; color: #ffffff; }
QTreeWidget#menuLayoutTree QHeaderView::section {
    background: #303036;
    border-bottom-color: #404048;
    color: #a8a8b0;
}
QLabel#menuLayoutPreviewLabel { color: #a8a8b0; }
QMenu { background: #2a2a30; color: #e4e4e9; border: 1px solid #45454f; }
QMenu::item:selected { background: #3a3a46; }
QPushButton { background: #3a3a42; border: 1px solid #4a4a54; color: #e4e4e9; }
QPushButton:hover { background: #44444e; }
QPushButton#advancedSectionToggle {
    min-height: 40px; padding: 0 38px 0 14px; text-align: left;
    background: #2a2a30; border: 1px solid #3a3a42; border-radius: 10px;
    color: #e4e4e9; font-size: 13px; font-weight: 600;
}
QPushButton#advancedSectionToggle:hover { background: #303036; border-color: #45454d; }
QPushButton#advancedSectionToggle:focus { border: 2px solid #0a84ff; }
QLabel#disclosureChevron { color: #a8a8b0; font-size: 18px; background: transparent; }
QToolButton { color: #e4e4e9; }
QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #e4e4e9; }
"""

_DARK_BROWSER_OVERRIDE = """
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #2e2e35; color: #e4e4e9; border: 1px solid #45454f;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover { border-color: #56565f; }
QSpinBox::up-button, QDoubleSpinBox::up-button { border-left: 1px solid #45454f; border-bottom: 1px solid #45454f; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #55555e; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #6a6a74; }
"""

def _settings_stylesheet(theme: str = "system") -> str:
    """浅色基础 QSS + 显式控件文字色补丁；深色系统时追加深色覆盖段。"""
    light_patch = """
        QPushButton { color: #202020; }
        QToolButton { color: #202020; }
        QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #202020; }
    """
    base = _LIGHT_SETTINGS_STYLESHEET + light_patch
    dark = theme == "dark" or (theme == "system" and _system_dark())
    if not dark:
        return base + BROWSER_CONTROL_STYLESHEET
    return base + _DARK_OVERRIDE + BROWSER_CONTROL_STYLESHEET + _DARK_BROWSER_OVERRIDE

_LIGHT_SETTINGS_STYLESHEET = """
QDialog {
    background: #fcfcfd;
    color: #202020;
    font-family: "SF Pro Text", ".AppleSystemUIFont", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#sidebarPane {
    background: #f7f7f8;
    border: none;
    border-right: 1px solid #e3e5e8;
}
QStackedWidget { background: #fcfcfd; }
QWidget#aiSettingsContent { background: transparent; }
QLineEdit#settingsSearch {
    min-height: 30px;
    padding: 0 8px;
    background: #f0f1f3;
    border: 1px solid transparent;
    border-radius: 15px;
    color: #202020;
}
QLineEdit#settingsSearch:focus {
    border: 2px solid #0a84ff;
    padding: 0 7px;
}
QPushButton#saveAndExit {
    min-height: 28px;
    padding: 2px 8px;
    text-align: left;
    background: transparent;
    border: none;
    border-radius: 8px;
    font-weight: 500;
}
QPushButton#saveAndExit:hover { background: #e9eaec; }
QLabel#searchStatus {
    padding: 0 5px;
    color: #777b80;
    font-size: 11px;
}
QListWidget#settingsSidebar {
    background: transparent;
    border: none;
    outline: none;
    font-size: 13px;
    font-weight: 500;
}
QListWidget#settingsSidebar::item {
    min-height: 26px;
    padding: 4px 10px;
    border-radius: 9px;
    color: #4e4e4e;
}
QListWidget#settingsSidebar::item:hover {
    background: #eceef1;
    color: #202020;
}
QWidget#settingsTaskTabBar {
    background: #f0f1f3;
    border: none;
    border-radius: 8px;
}
QPushButton#settingsTaskTab {
    min-height: 26px;
    padding: 0 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #60646a;
}
QPushButton#settingsTaskTab:hover { background: #e5e7ea; color: #202020; }
QPushButton#settingsTaskTab:checked {
    background: #ffffff;
    border-color: #d6d9de;
    color: #202020;
    font-weight: 600;
}
QPushButton#settingsTaskTab:focus { border: 2px solid #0a84ff; }
QListWidget#settingsSidebar::item:selected {
    background: #e3e5e8;
    color: #171717;
}
QLabel#pageTitle {
    font-size: 22px;
    font-weight: 600;
    color: #171717;
}
QLabel#sectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: #2b2b2b;
}
QFrame#settingsCard {
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 12px;
}
QFrame#cardSeparator {
    background: #eceef1;
    border: none;
    margin-left: 14px;
    margin-right: 14px;
}
QLabel#settingLabel {
    font-size: 13px;
    font-weight: 500;
    color: #252525;
}
QLabel#settingHint {
    font-size: 12px;
    font-weight: 400;
    color: #777777;
}
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #a6a8ac; }
SettingRow[searchMatch="true"] {
    background: #eaf3ff;
    border-radius: 8px;
}
QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget {
    background: transparent;
}
QLabel#quickLaunchCount, QLabel#menuLayoutEditorLabel, QLabel#menuLayoutPreviewLabel,
QLabel#menuLayoutEditorHint {
    color: #6f7378;
    font-size: 12px;
    font-weight: 500;
    padding-left: 2px;
}
QLabel#quickLaunchName { color: #252525; font-size: 13px; font-weight: 500; }
QLabel#quickLaunchDetail { color: #777777; font-size: 11px; }
QLabel#quickLaunchEmpty {
    color: #777777;
    background: #fbfbfb;
    border: 1px dashed #d9d9d9;
    border-radius: 8px;
}
QListWidget#quickLaunchList {
    background: #fbfbfb;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    outline: none;
    padding: 3px;
}
QListWidget#quickLaunchList::item {
    min-height: 30px;
    padding: 3px 7px;
    border-radius: 6px;
}
QListWidget#quickLaunchList::item:selected { background: #e8e8e8; color: #202020; }
QLabel#menuLayoutEditorHint { font-weight: 400; }
QFrame#menuLayoutEditorPanel, QFrame#menuLayoutPreviewPanel {
    background: #fbfbfc;
    border: 1px solid #e2e4e8;
    border-radius: 10px;
}
QFrame#imagePreviewDrawer {
    background: #ffffff;
    border: none;
    border-left: 1px solid #d9dce1;
}
QScrollArea#imagePreviewScroll, QScrollArea#imagePreviewScroll > QWidget > QWidget,
QWidget#imageMasonryFlow { background: transparent; }
QLabel#imagePreviewTitle { color: #202124; font-size: 16px; font-weight: 600; }
QLabel#imagePreviewCount, QLabel#imagePreviewPath { color: #777b80; font-size: 11px; }
QLabel#imagePreviewEmpty { color: #777b80; }
QPushButton#imagePreviewClose { background: transparent; border: none; font-size: 20px; }
QPushButton#imagePreviewClose:hover { background: #eceef1; }
QTreeWidget#menuLayoutTree, QTreeWidget#menuLayoutPreview {
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 10px;
    outline: none;
    padding: 4px;
}
QTreeWidget#menuLayoutTree::item, QTreeWidget#menuLayoutPreview::item {
    min-height: 28px;
    padding: 1px 5px;
    border-radius: 6px;
}
QTreeWidget#menuLayoutTree::item:hover { background: #f4f5f6; }
QTreeWidget#menuLayoutTree::item:selected {
    background: #e9eef5;
    color: #202020;
}
QTreeWidget#menuLayoutPreview::item:hover { background: transparent; }
QTreeWidget#menuLayoutTree QHeaderView::section {
    min-height: 28px;
    padding: 0 8px;
    background: #f7f7f8;
    border: none;
    border-bottom: 1px solid #e8e9ec;
    color: #6f7378;
    font-size: 12px;
    font-weight: 500;
}
QMenu {
    background: #ffffff;
    color: #202020;
    border: 1px solid #d7d9dd;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item { min-height: 26px; padding: 2px 24px 2px 10px; border-radius: 6px; }
QMenu::item:selected { background: #edf2f7; }
QMenu::item:disabled { color: #a6a8ac; }
QPushButton {
    min-height: 26px;
    padding: 1px 12px;
    background: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 7px;
    font-weight: 500;
}
QPushButton:hover { background: #f0f0f0; }
QPushButton:focus {
    border: 2px solid #0a84ff;
    padding: 0 11px;
}
QPushButton[settingsMenuButton="true"] { padding-right: 26px; }
QPushButton[settingsMenuButton="true"]:focus { padding-right: 25px; }
QPushButton#advancedSectionToggle {
    min-height: 40px;
    padding: 0 38px 0 14px;
    text-align: left;
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 10px;
    color: #252525;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#advancedSectionToggle:hover { background: #f7f7f8; border-color: #d7d9dd; }
QPushButton#advancedSectionToggle:focus { border: 2px solid #0a84ff; }
QLabel#disclosureChevron { color: #777b80; font-size: 18px; background: transparent; }
"""
