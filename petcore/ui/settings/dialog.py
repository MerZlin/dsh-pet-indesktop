from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ... import settings_reset
from ...config import ScopedConfig
from ..icons import MENU_ICON_SIZE, SIDEBAR_ICON_SIZE, icon as menu_icon
from ..placement import place_beside_pet
from .base import make_button, page_search_texts, reveal_text
from .page_about import AboutPage
from .page_api import ApiSettingsPage
from .page_extensions import ExtensionsPage
from .page_interaction import InteractionPage
from .page_pets import PetsPage
from .page_voice import VoicePage
from .pages_core import AppearancePage, GeneralPage, MenuPage
from .style import is_dark, nav_row_heights, stylesheet

logger = logging.getLogger("petcore.ui.settings")

_PAGE_ICONS = {
    "常规": "settings",
    "外观与交互": "size",
    "互动与物理": "pointer",
    "语音": "voice",
    "多宠": "spawn",
    "菜单": "application",
    "扩展管理": "application",
    "API 与账户": "network",
    "关于与诊断": "info",
}

_PAGE_KEYS = {
    "常规": "general",
    "外观与交互": "appearance",
    "互动与物理": "interaction",
    "语音": "voice",
    "多宠": "pets",
    "菜单": "menu",
    "扩展管理": "mods",
    "API 与账户": "api",
    "关于与诊断": "about",
}


def _page_icon(title: str) -> str:
    return _PAGE_ICONS.get(str(title), "application")


class _SettingsHostProxy:
    """Expose the host API while routing ``config`` to the selected pet."""

    def __init__(self, host, target_window=None):
        self._host = host
        self.window = target_window or getattr(host, "window", None)
        target_config = getattr(self.window, "config", None)
        self.config = ScopedConfig(host.config, target_config)

    def __getattr__(self, name):
        return getattr(self._host, name)


@dataclass
class _PageEntry:
    title: str
    page: QWidget
    keywords: tuple[str, ...]
    key: str = ""


class SettingsDialog(QDialog):
    def __init__(
        self, host, parent=None, initial_page: str | None = None, target_window=None
    ):
        super().__init__(parent)
        self.root_host = host
        self.target_window = target_window or getattr(host, "window", None)
        self.host = _SettingsHostProxy(host, self.target_window)
        self.config = self.host.config
        self._pages: list[_PageEntry] = []
        self._disposed = False
        self._placed = False
        self._snapshot = self.config.snapshot()
        window = self.target_window
        visible_check = getattr(window, "isVisible", None)
        self._initial_visible = bool(visible_check()) if callable(visible_check) else True
        self._before = {
            "character": str(self.config.get("character") or ""),
            "scale": float(self.config.get("scale") or 0.72),
            "always_on_top": bool(self.config.get("always_on_top")),
            "click_through": bool(self.config.get("click_through")),
        }
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(16)
        self._live_timer.timeout.connect(self._apply_live)
        self.setObjectName("settingsDialog")
        # 原生标题栏补上最小化/最大化；关闭按钮、非模态与保存语义保持不变。
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint
        )
        if window is None or int(getattr(window, "slot", 0)) == 0:
            self.setWindowTitle("设置 · 主宠")
        else:
            self.setWindowTitle(f"设置 · 子宠 {int(window.slot)}")
        self.resize(900, 620)
        self.setMinimumSize(720, 500)
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        palette = self.palette()
        accent = QColor("#0a84ff")
        palette.setColor(QPalette.ColorRole.Highlight, accent)
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        accent_role = getattr(QPalette.ColorRole, "Accent", None)
        if accent_role is not None:
            palette.setColor(accent_role, accent)
        self.setPalette(palette)
        self._build_ui()
        self._load_pages()
        self._fit_combo_widths()
        self._connect_live_preview()
        self._apply_filter("")
        if initial_page:
            self.select_page(initial_page)
        else:
            self.nav.setFocus()

    def refresh_style(self) -> None:
        """按当前界面字号重设样式表；字号/主题未变时是空操作。

        样式真的变了还要做两件收尾：主侧栏行高按字号重算（``nav_row_heights``
        与 QSS 用同一个公式，否则 18px 的标签会被 34px 的行切掉），以及通知每个
        页面重排自有几何（扩页导航宽/内容最小高都按字体度量算）。
        """
        key = (is_dark(), int(self.config.get("ui_font_size") or 13))
        if key == getattr(self, "_style_key", None):
            return
        self._style_key = key
        self.setStyleSheet(stylesheet(key[0], key[1]))
        self._apply_nav_row_height(key[1])
        self._reflow_pages()

    def _apply_nav_row_height(self, font_size: int) -> None:
        height = nav_row_heights(font_size)[0]
        nav = getattr(self, "nav", None)
        if nav is None:
            return
        for row in range(nav.count()):
            item = nav.item(row)
            if item is not None:
                item.setSizeHint(QSize(0, height))

    def _reflow_pages(self) -> None:
        for entry in getattr(self, "_pages", ()):  # 构造期还没有页面
            hook = getattr(entry.page, "reflow", None)
            if not callable(hook):
                continue
            try:
                hook()
            except Exception:
                logger.warning("reflow of %s failed", entry.title, exc_info=True)

    def _build_ui(self) -> None:
        self.refresh_style()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar = QWidget(self)
        sidebar.setObjectName("sidebarPanel")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 12)
        sidebar_layout.setSpacing(10)

        self.search = QLineEdit(sidebar)
        self.search.setObjectName("settingsSearch")
        self.search.setPlaceholderText("搜索设置")
        self.search.setAccessibleName("搜索设置")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        sidebar_layout.addWidget(self.search)

        self.nav = QListWidget(sidebar)
        self.nav.setObjectName("settingsNav")
        self.nav.setAccessibleName("设置页面")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # 18px 与主分支桌面版设置侧栏（`modern_settings_dialog.py:342`）一致；
        # 24px 在 42px 行高里会让图标比 16px 标签高出一截。
        self.nav.setIconSize(QSize(MENU_ICON_SIZE, MENU_ICON_SIZE))
        self.nav.setUniformItemSizes(True)
        self.nav.currentRowChanged.connect(self._select_row)
        sidebar_layout.addWidget(self.nav, 1)

        self.search_status = QLabel("", sidebar)
        self.search_status.setObjectName("searchStatus")
        self.search_status.setWordWrap(True)
        sidebar_layout.addWidget(self.search_status)

        # 重置是**命令**不是偏好（`docs/SETTINGS-CHANGE-GATES.md`）：不进页面列表，
        # 落在侧栏底部；主宠页两个按钮各自只动自己那一半键（见 settings_reset）。
        self.reset_pet_button = make_button("重置本宠设置…", sidebar)
        self.reset_pet_button.setObjectName("resetPetButton")
        self.reset_pet_button.setAccessibleName("重置本宠设置")
        self.reset_pet_button.setToolTip(
            "把这只桌宠的偏好恢复为出厂默认（保留身份与位置，不影响全局设置与 API 配置）"
        )
        self.reset_pet_button.clicked.connect(
            lambda: self._reset_scope(settings_reset.SCOPE_PET)
        )
        sidebar_layout.addWidget(self.reset_pet_button)

        self.reset_global_button = make_button("重置全局设置…", sidebar)
        self.reset_global_button.setObjectName("resetGlobalButton")
        self.reset_global_button.setAccessibleName("重置全局设置")
        self.reset_global_button.setToolTip(
            "把所有全局设置恢复为出厂默认（保留宠物位置与活跃成员，不影响本宠偏好）"
        )
        self.reset_global_button.clicked.connect(
            lambda: self._reset_scope(settings_reset.SCOPE_GLOBAL)
        )
        sidebar_layout.addWidget(self.reset_global_button)

        body.addWidget(sidebar)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.stack = QStackedWidget(content)
        self.stack.setObjectName("settingsPages")
        content_layout.addWidget(self.stack, 1)

        footer = QWidget(content)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(30, 8, 28, 16)
        footer_layout.setSpacing(8)
        self.version_label = QLabel(f"petcore v{__version__}", footer)
        self.version_label.setObjectName("versionFooter")
        footer_layout.addWidget(self.version_label)
        footer_layout.addStretch(1)
        self.cancel_button = make_button("取消", footer)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = make_button("保存", footer, primary=True)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.save_button)
        content_layout.addWidget(footer)

        body.addWidget(content, 1)
        root.addLayout(body)

    def _load_pages(self) -> None:
        pages: list[tuple[str, QWidget, tuple[str, ...], str]] = []
        for page in (
            GeneralPage(self.host),
            AppearancePage(self.host),
            InteractionPage(self.host),
            VoicePage(self.host),
            PetsPage(self.host),
            MenuPage(self.host),
            ExtensionsPage(self.host),
            ApiSettingsPage(self.host),
            AboutPage(self.host),
        ):
            title = str(getattr(page, "title", "") or "")
            pages.append((title, page, self._page_keywords(page), _PAGE_KEYS.get(title, "")))

        for title, page, keywords, key in pages:
            item = QListWidgetItem(title, self.nav)
            item.setData(Qt.ItemDataRole.UserRole, len(self._pages))
            # 行高与 QSS 同源（`nav_row_heights`）：字号 13 → 34px、字号 18 → 39px。
            # 旧的写法把 34 写死，字号一放大文字就被 min-height 切掉上下缘
            # （主分支 `pet/settings_widgets.py` 的侧栏 pane 也是同一个教训）。
            item.setSizeHint(QSize(0, self._nav_row_height()))
            item.setIcon(menu_icon(_page_icon(title), SIDEBAR_ICON_SIZE))
            self.stack.addWidget(page)
            self._pages.append(_PageEntry(title, page, keywords, key))

    def _nav_row_height(self) -> int:
        try:
            value = int(self.config.get("ui_font_size") or 13)
        except (TypeError, ValueError):
            value = 13
        return nav_row_heights(value)[0]

    def _fit_combo_widths(self) -> None:
        """按实际字体度量给下拉框留出箭头与左右安全区，避免末字被裁切。"""
        for combo in self.findChildren(QComboBox):
            metrics = combo.fontMetrics()
            widest = 0
            for index in range(combo.count()):
                widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
            # 箭头槽、边框和两侧 padding 均独立计入；不依赖平台默认 style。
            combo.setMinimumWidth(max(combo.minimumWidth(), widest + 58))

    def _page_keywords(self, page: QWidget) -> tuple[str, ...]:
        values: list[str] = []
        getter = getattr(page, "keywords", None)
        if callable(getter):
            try:
                values.extend(str(item) for item in (getter() or ()))
            except Exception:
                values = []
        # 行级文案（选项标题 + 说明）也进搜索口径：只搜页面标题等于搜不到功能。
        values.extend(page_search_texts(page))
        return tuple(dict.fromkeys(value for value in values if value))

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        first_visible = -1
        visible = 0
        for index, entry in enumerate(self._pages):
            item = self.nav.item(index)
            # 每次输入都重算：页面里的提示文案会随状态改写，构造期快照会过期。
            haystack = " ".join(
                (entry.title, *entry.keywords, *page_search_texts(entry.page))
            ).lower()
            matched = not needle or needle in haystack
            item.setHidden(not matched)
            if matched:
                visible += 1
                if first_visible < 0:
                    first_visible = index
        if not needle:
            self.search_status.setText("")
        elif visible:
            self.search_status.setText(f"匹配 {visible} 个页面")
        else:
            self.search_status.setText("没有匹配的设置页")
        if visible == 0:
            self.nav.setCurrentRow(-1)
            return
        current = self.nav.currentRow()
        if not 0 <= current < len(self._pages) or self.nav.item(current).isHidden():
            current = first_visible
            self.nav.setCurrentRow(current)
        if needle and 0 <= current < len(self._pages):
            reveal_text(self._pages[current].page, needle)

    def _select_row(self, row: int) -> None:
        if 0 <= row < len(self._pages):
            self.stack.setCurrentWidget(self._pages[row].page)

    def select_page(self, key: str) -> None:
        needle = str(key or "").strip().lower()
        if not needle:
            return
        if needle.startswith("extension:"):
            for index, entry in enumerate(self._pages):
                selector = getattr(entry.page, "select_tab", None)
                if callable(selector):
                    self.nav.setCurrentRow(index)
                    selector(needle)
                    return
        for index, entry in enumerate(self._pages):
            names = (entry.key, entry.title, *entry.keywords)
            if any(needle == str(name).lower() for name in names):
                self.nav.setCurrentRow(index)
                return
        for index, entry in enumerate(self._pages):
            if needle in entry.key.lower() or needle in entry.title.lower():
                self.nav.setCurrentRow(index)
                return

    def page_for_key(self, key: str):
        """Return a loaded page by its stable settings key for host callbacks."""
        needle = str(key or "").strip().lower()
        for entry in self._pages:
            if entry.key.lower() == needle:
                return entry.page
        return None

    def _connect_live_preview(self) -> None:
        for entry in self._pages:
            signal = getattr(entry.page, "changed", None)
            if signal is None:
                continue
            try:
                signal.connect(self._request_live_apply)
            except Exception:
                logger.warning("settings page %s has no usable changed signal", entry.title)

    def _request_live_apply(self, *_args) -> None:
        if self._disposed:
            return
        if not self._live_timer.isActive():
            self._live_timer.start()

    def _remember_before(self) -> None:
        self._before = {
            "character": str(self.config.get("character") or ""),
            "scale": float(self.config.get("scale") or 0.72),
            "always_on_top": bool(self.config.get("always_on_top")),
            "click_through": bool(self.config.get("click_through")),
        }

    def _apply_live(self) -> None:
        self._live_timer.stop()
        if self._disposed:
            return
        applier = getattr(self.host, "apply_live_settings", None)
        if callable(applier):
            try:
                applier()
            except Exception:
                logger.exception("live settings apply failed")
        else:
            self._apply_window_changes()
        self._remember_before()

    def _save_pages(self) -> bool:
        ok = True
        for entry in self._pages:
            page = entry.page
            dirty = getattr(page, "dirty", None)
            draft = getattr(page, "draft", None)
            confirm = getattr(page, "confirm_save", None)
            try:
                if callable(dirty) and not dirty():
                    continue
                if callable(draft):
                    draft()
                if callable(confirm) and confirm() is False:
                    ok = False
                    QMessageBox.warning(
                        self,
                        "保存设置",
                        f"「{entry.title}」没有保存：请按页面提示修正后再试。",
                    )
            except Exception:
                logger.exception("settings page %s failed to save", entry.title)
                QMessageBox.warning(self, "保存设置", f"「{entry.title}」保存失败，其他设置仍会保存。")
        return ok

    def _apply_window_changes(self) -> None:
        window = getattr(self.host, "window", None)
        if window is None:
            return

        def invoke(name: str, *args) -> None:
            callback = getattr(window, name, None)
            if not callable(callback):
                return
            try:
                callback(*args)
            except Exception:
                logger.exception("failed to call window.%s", name)

        character = str(self.config.get("character") or "")
        scale = float(self.config.get("scale") or 0.72)
        always_on_top = bool(self.config.get("always_on_top"))
        click_through = bool(self.config.get("click_through"))
        if character and character != self._before["character"]:
            invoke("set_character", character)
        if abs(scale - self._before["scale"]) > 1e-9:
            invoke("set_scale", scale)
        if always_on_top != self._before["always_on_top"]:
            invoke("toggle_always_on_top")
        if click_through != self._before["click_through"]:
            invoke("toggle_click_through")

    def _commit(self) -> bool:
        """保存并实时应用：保存页 → 同步子宠 → 落盘 → 实时应用。失败返回 False。"""
        self._live_timer.stop()
        if not self._save_pages():
            return False
        pets = getattr(self.host, "pets", None)
        if pets is not None:
            try:
                pets.sync_shared_settings()
            except Exception:
                logger.warning("failed to sync shared settings to pets", exc_info=True)
        try:
            self.config.save()
        except OSError as exc:
            QMessageBox.warning(self, "保存设置", f"无法保存配置：{exc}")
            return False
        self._apply_live()
        return True

    def accept(self) -> None:
        if not self._commit():
            return
        super().accept()

    # ------------------------------------------------------------ 重置（命令）

    def _confirm_reset(self, scope: str) -> bool:
        """破坏性动作的确认框：默认键与 Esc 都落「取消」（同主分支口径）。"""
        if scope == settings_reset.SCOPE_PET:
            title = "重置本宠设置"
            body = (
                "把这只桌宠的偏好恢复为出厂默认。\n\n"
                "会重置：气泡与字号缩放、动画权重、互动与物理、语音、点击台词、"
                "自言自语等按宠设置。\n"
                "会保留：宠物身份与位置、全局设置、API 密钥与 api.json。\n\n"
                "此操作不可撤销。"
            )
        else:
            title = "重置全局设置"
            body = (
                "把所有全局设置恢复为出厂默认。\n\n"
                "会重置：界面字号、多宠与菜单布局、灵动岛、AI 与联网、外观全局项等。\n"
                "会保留：每只宠的身份与位置、活跃成员表、各自的按宠偏好、"
                "API 密钥与 api.json。\n\n"
                "此操作不可撤销。"
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(body)
        confirm = box.addButton("重置", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def _reset_scope(self, scope: str) -> None:
        try:
            scope = settings_reset.normalize_scope(scope)
        except ValueError:
            return
        if not self._confirm_reset(scope):
            return
        try:
            changed = settings_reset.apply_reset(self.config, scope)
        except settings_reset.ResetError as exc:
            QMessageBox.warning(self, "重置设置", f"无法写入配置：{exc}")
            return
        logger.info("settings reset applied: scope=%s keys=%d", scope, len(changed))
        root = self.root_host
        if scope == settings_reset.SCOPE_GLOBAL:
            pets = getattr(root, "pets", None)
            sync = getattr(pets, "sync_shared_settings", None)
            if callable(sync):
                try:
                    sync()
                except Exception:  # noqa: BLE001 - 子宠同步失败不该掩盖重置结果
                    logger.warning("sync shared settings after reset failed", exc_info=True)
        apply_live = getattr(root, "apply_live_settings", None)
        if callable(apply_live):
            try:
                apply_live()
            except Exception:  # noqa: BLE001 - 实时应用失败仍要关闭窗口
                logger.warning("apply live settings after reset failed", exc_info=True)
        if scope == settings_reset.SCOPE_GLOBAL:
            reload_menu = getattr(root, "reload_menu", None)
            if callable(reload_menu):
                try:
                    reload_menu()
                except Exception:  # noqa: BLE001
                    logger.warning("reload menu after reset failed", exc_info=True)
        self._close_after_reset()

    def _close_after_reset(self) -> None:
        """重置已经落盘：关闭**不能**再走 ``_commit``（旧控件值会把默认值写回去）。"""
        self._live_timer.stop()
        self.done(QDialog.DialogCode.Accepted)

    def reject(self) -> None:
        self._live_timer.stop()
        self.config.restore(self._snapshot)
        for entry in self._pages:
            discarding = getattr(entry.page, "discard_changes", None)
            if not callable(discarding):
                continue
            try:
                discarding()
            except Exception:
                logger.warning("settings page %s failed to discard", entry.title, exc_info=True)
        window = getattr(self.host, "window", None)
        setter = getattr(window, "set_visible", None)
        if callable(setter):
            try:
                setter(self._initial_visible)
            except Exception:
                logger.warning("failed to restore pet visibility", exc_info=True)
        else:
            visible_check = getattr(window, "isVisible", None)
            if callable(visible_check) and bool(visible_check()) != self._initial_visible:
                window.toggle_hidden()
        self._live_timer.stop()
        self._apply_live()
        super().reject()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._placed:
            return
        self._placed = True
        place_beside_pet(self, getattr(self.host, "window", None))

    def keyPressEvent(self, event) -> None:
        """Enter = 保存并退出；只有多行文本框才允许 Enter 换行。"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.focusWidget()
            if not isinstance(focus, (QPlainTextEdit, QTextEdit)):
                self.accept()
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        """X 关闭也保存：只有提交失败才留在页面上，绝不静默丢弃改动。"""
        if not self._commit():
            event.ignore()
            return
        event.accept()
        # ``done`` 负责 dispose 释放内嵌页并让 host 清掉窗口引用（关闭不是 reject）。
        self.done(QDialog.DialogCode.Accepted)

    def done(self, result: int) -> None:
        super().done(result)
        self._dispose_pages()

    def _dispose_pages(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        for entry in self._pages:
            dispose = getattr(entry.page, "dispose", None)
            if not callable(dispose):
                continue
            try:
                dispose()
            except Exception:
                logger.warning("settings page %s failed to dispose", entry.title, exc_info=True)
