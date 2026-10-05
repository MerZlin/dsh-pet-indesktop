"""Offline UI-only localization, shared by the pet and standalone settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QApplication, QGroupBox, QLabel, QLineEdit, QListWidget, QScrollArea, QTreeWidget, QWidget
import shiboken6

LANGUAGES = ("zh_CN", "zh_TW", "en")
DEFAULT_LANGUAGE = "zh_CN"
CONTENT_LABELS = {
    "bubble-body",
    "quick-chat-output",
    "session-row-title",
    "attachment-name",
    "attachment-meta",
    "imagePreviewPath",
    "quickLaunchName",
    "quickLaunchDetail",
}
_manager = None
_catalogs = None


def _load_catalogs():
    global _catalogs
    if _catalogs is None:
        directory = Path(__file__).with_name("codex_languages")
        _catalogs = {name: json.loads((directory / (name + ".json")).read_text(encoding="utf-8")) for name in ("en", "zh_TW", "zh_CN", "traditional_chars")}
    return _catalogs


def normalize_language(value):
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def language():
    return _manager.language if _manager is not None else DEFAULT_LANGUAGE


def text(value, locale=None):
    """Translate display copy, preserving editable text and all internal IDs."""
    if not isinstance(value, str) or not value:
        return value
    locale = normalize_language(locale or language())
    if value in ("简体中文", "繁體中文", "English"):
        return value
    catalogs = _load_catalogs()
    table = catalogs[locale]
    if value in table:
        return table[value]
    if locale == "zh_CN":
        return value.replace("小時", "小时").replace("分鐘", "分钟")
    if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|https?://)", value):
        return value
    # Full strings come first. Keep placeholders, rich text, and paths intact.
    if locale == "zh_TW":
        result = "".join(catalogs["traditional_chars"].get(char, char) for char in value)
        for before, after in (
            ("設置", "設定"),
            ("配置", "設定"),
            ("文件", "檔案"),
            ("鼠標", "滑鼠"),
            ("屏幕", "螢幕"),
            ("窗口", "視窗"),
            ("視頻", "影片"),
            ("保存", "儲存"),
            ("信息", "資訊"),
            ("默認", "預設"),
            ("搜索", "搜尋"),
            ("剪貼板", "剪貼簿"),
        ):
            result = result.replace(before, after)
        return result
    patterns = (
        (r"^(\d+(?:\.\d+)?)\s*(?:小時|小时)$", r"\1 hours"),
        (r"^(\d+)\s*分钟$", r"\1 minutes"),
        (r"^(\d+)\s*天$", r"\1 days"),
        (r"^(\d+)\s*张图片$", r"\1 images"),
        (r"^(\d+)\s*个快捷项$", r"\1 shortcuts"),
        (r"^切换到(.+)$", lambda match: "Switch to " + text(match[1], locale)),
        (r"^(展开|收起)(.+)$", lambda match: ("Expand " if match[1] == "展开" else "Collapse ") + text(match[2], locale)),
        (r"^(\d+/\d+ · )(.+)$", lambda match: match[1] + text(match[2], locale)),
        (r"^(.+?)\s*剩餘$", lambda match: text(match[1], locale) + " remaining"),
        (r"^更新 (.+) · 帳號共用額度$", r"Updated \1 · Shared account quota"),
        (r"^Codex 剩餘用量：(.*)$", lambda match: "Codex remaining quota: " + match[1]),
    )
    for pattern, replacement in patterns:
        if re.match(pattern, value):
            return re.sub(pattern, replacement, value)
    prefix = "留空则使用基础模式台词。可用参数："
    if value.startswith(prefix):
        remainder = value[len(prefix) :]
        for source in sorted(table, key=len, reverse=True):
            if re.search(r"[\u3400-\u9fff]", source):
                remainder = remainder.replace(source, table[source])
        remainder = remainder.replace("；其中", "; optional:").replace("（", " (").replace("）", ")").replace("、", ", ")
        return "Leave empty for default dialogue. Parameters: " + remainder
    suffix = "：这一类气泡的通过概率。"
    if suffix in value:
        category = value.split(suffix, 1)[0]
        return (
            text(category, locale)
            + ": Probability of showing this bubble. 0 = silent; 1 = always. Detection remains active. The context menu offers 0/1 shortcuts."
        )
    match = re.match(r"^裁切取景（(.+)）$", value)
    if match:
        return "Image crop (" + text(match[1], locale) + ")"
    if "\n" in value:
        return "\n".join(text(line, locale) for line in value.split("\n"))
    for prefix in ("配置未能写入磁盘，改动可能在重启后丢失。", "配置路径：", "JSON 模板无效：", "菜单布局未保存：", "无法写入系统剪贴板："):
        if value.startswith(prefix) and prefix in table:
            return table[prefix] + value[len(prefix) :]
    return value


def quota_row_label(row):
    label = row.get("label") or "Codex"
    minutes = row.get("windowDurationMins")
    if language() == "en" and isinstance(minutes, (int, float)):
        prefix = label.rsplit(" · ", 1)[0] + " · " if " · " in label else ""
        if minutes == 10080:
            label = "Weekly"
        elif minutes % 1440 == 0:
            label = "%g days" % (minutes / 1440)
        elif minutes % 60 == 0:
            label = "%g hours" % (minutes / 60)
        else:
            label = "%g minutes" % minutes
        label = prefix + label
    return text(str(label)) + (" remaining" if language() == "en" else "剩餘" if language() == "zh_TW" else "剩余")


class UiLanguageManager(QObject):
    def __init__(self, app, config):
        super().__init__(app)
        self.app = app
        self.path = Path(config.path)
        self.language = normalize_language(config.get("ui_language", DEFAULT_LANGUAGE))
        self._busy = False
        self._preview_owner = None
        self._stamp = None
        self._pending = False
        app.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def preview(self, locale, owner=None):
        self._preview_owner = owner
        locale = normalize_language(locale)
        if locale != self.language:
            self.language = locale
            self.translate_all()
            logging.info("DSH interface language changed: %s", locale)

    def refresh(self):
        if self._preview_owner is not None and not shiboken6.isValid(self._preview_owner):
            self._preview_owner = None
        if self._preview_owner is None:
            try:
                stamp = self.path.stat().st_mtime_ns
                if stamp != self._stamp:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    self._stamp = stamp
                    locale = normalize_language(data.get("ui_language"))
                    if locale != self.language:
                        self.language = locale
                        self.translate_all()
            except (OSError, ValueError):
                pass
        self.translate_all(visible_only=True)

    def eventFilter(self, watched, event):
        if not self._busy and isinstance(watched, QWidget):
            if event.type() == QEvent.Type.Show:
                self.translate_tree(watched)
            elif event.type() == QEvent.Type.LayoutRequest and not self._pending:
                self._pending = True
                QTimer.singleShot(0, self._flush)
        return False

    def _flush(self):
        self._pending = False
        self.translate_all(visible_only=True)

    def _property(self, obj, getter, setter, key):
        current = getter()
        if not isinstance(current, str) or not current:
            return
        source_key, rendered_key = "_dsh_source_" + key, "_dsh_rendered_" + key
        previous = obj.property(rendered_key)
        source = obj.property(source_key) if current == previous else current
        source = current if source is None else source
        translated = text(source, self.language)
        obj.setProperty(source_key, source)
        obj.setProperty(rendered_key, translated)
        if current != translated:
            setter(translated)

    def _list(self, widget):
        # Item labels in editors often ARE filenames or user input. Only the
        # settings navigation is safe to translate; its selection uses indexes.
        if widget.objectName() != "settingsSidebar":
            return
        for index in range(widget.count()):
            item = widget.item(index)
            role_source = int(Qt.ItemDataRole.UserRole) + 901
            role_rendered = role_source + 1
            current = item.text()
            source = item.data(role_source) if item.data(role_rendered) == current else current
            translated = text(source, self.language)
            item.setData(role_source, source)
            item.setData(role_rendered, translated)
            if current != translated:
                item.setText(translated)

    def _tree(self, widget):
        if widget.objectName() not in ("menuLayoutTree", "menuLayoutPreview"):
            return
        signal_blocker = QSignalBlocker(widget)

        def item_text(item, column):
            source_role = int(Qt.ItemDataRole.UserRole) + 903
            render_role = source_role + 1
            current = item.text(column)
            source = item.data(column, source_role) if item.data(column, render_role) == current else current
            translated = text(source, self.language)
            item.setData(column, source_role, source)
            item.setData(column, render_role, translated)
            if current != translated:
                item.setText(column, translated)

        for column in range(widget.columnCount()):
            item_text(widget.headerItem(), column)
        pending = [widget.topLevelItem(index) for index in range(widget.topLevelItemCount())]
        while pending:
            item = pending.pop()
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            # Preserve user aliases and submenu names; serialization uses the
            # original metadata, never translated text, for built-in actions.
            if not data.get("alias") and data.get("type") != "submenu":
                item_text(item, 0)
            for column in range(1, widget.columnCount()):
                item_text(item, column)
            pending.extend(item.child(index) for index in range(item.childCount()))
        signal_blocker.unblock()

    def translate_tree(self, root):
        if self._busy or not shiboken6.isValid(root):
            return
        self._busy = True
        try:
            widgets = [root, *root.findChildren(QWidget)]
            for widget in widgets:
                if not shiboken6.isValid(widget):
                    continue
                if widget.objectName() in CONTENT_LABELS:
                    continue
                for getter, setter, key in (
                    (widget.windowTitle, widget.setWindowTitle, "windowTitle"),
                    (widget.toolTip, widget.setToolTip, "toolTip"),
                    (widget.accessibleName, widget.setAccessibleName, "accessibleName"),
                    (widget.accessibleDescription, widget.setAccessibleDescription, "accessibleDescription"),
                ):
                    self._property(widget, getter, setter, key)
                if isinstance(widget, (QLabel, QAbstractButton)):
                    self._property(widget, widget.text, widget.setText, "text")
                if isinstance(widget, QLineEdit):
                    self._property(widget, widget.placeholderText, widget.setPlaceholderText, "placeholder")
                if isinstance(widget, QGroupBox):
                    self._property(widget, widget.title, widget.setTitle, "title")
                if isinstance(widget, QListWidget):
                    self._list(widget)
                if isinstance(widget, QTreeWidget):
                    self._tree(widget)
                if widget.objectName() == "sidebarPane":
                    widget.setFixedWidth((270 if widget.property("refinedSidebar") else 230) if self.language == "en" else 200)
                language_changed = widget.property("_dsh_painted_language") != self.language
                if widget.objectName() == "dynamic-island" and language_changed:
                    widget._content_cache_key = None
                    widget.update()
                if widget.objectName() == "codexQuotaBubble" and language_changed:
                    widget.update()
                widget.setProperty("_dsh_painted_language", self.language)
            for action in root.findChildren(QAction):
                self._property(action, action.text, action.setText, "text")
                self._property(action, action.toolTip, action.setToolTip, "toolTip")
        except RuntimeError:
            pass  # A deferred-delete window can disappear while being traversed.
        finally:
            self._busy = False

    def translate_all(self, visible_only=False):
        for widget in self.app.topLevelWidgets():
            if not visible_only or widget.isVisible():
                self.translate_tree(widget)


def install(config):
    global _manager
    app = QApplication.instance()
    if app is not None and _manager is None:
        _load_catalogs()
        _manager = UiLanguageManager(app, config)
        logging.info("DSH interface language loaded: %s", _manager.language)
    return _manager


def add_language_control(dialog):
    from .settings_widgets import ModernSelect, SettingRow, SettingsSection

    manager = install(dialog.config)
    selector = ModernSelect(dialog, width=180)
    selector.setObjectName("dshInterfaceLanguage")
    for label, value in (("简体中文", "zh_CN"), ("繁體中文", "zh_TW"), ("English", "en")):
        selector.addItem(label, value)
    selector.setCurrentData(normalize_language(dialog.config.get("ui_language", DEFAULT_LANGUAGE)))
    dialog.ui_language_select = selector
    row = SettingRow("ui_language", "界面语言", "选择界面语言；切换立即预览，保存后下次启动仍会使用。", selector)
    scroll = dialog.pages.widget(0).findChild(QScrollArea, "settingsScroll")
    content = scroll.widget()
    section = SettingsSection("语言", [row], content)
    content.layout().insertWidget(0, section)
    dialog._search_rows.append(row)
    selector.currentIndexChanged.connect(lambda _index: manager.preview(selector.currentData(), dialog))
    manager.translate_tree(dialog)


def settings_saved(dialog, succeeded):
    if succeeded and _manager is not None:
        _manager._preview_owner = None
        _manager.preview(dialog.ui_language_select.currentData())
        _manager._stamp = None
