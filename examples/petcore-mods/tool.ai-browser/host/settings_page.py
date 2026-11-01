"""设置页：默认站点、自定义站点与内核状态。

Qt 只出现在本文件与 ``web_pane.py``（runtime/sites/engine 三个模块保持无 Qt，
``tests/test_petcore_ai_browser_mod.py`` 会守住这条）。
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QPlainTextEdit

from petcore.ui.settings.base import SettingsPage, make_button

from . import engine, sites
from .runtime import SETTING_CUSTOM_SITES, SETTING_DEFAULT_SITE

KEYS = (SETTING_DEFAULT_SITE, SETTING_CUSTOM_SITES)


class AiBrowserPage(SettingsPage):
    title = "AI 网页"

    def __init__(self, host, parent=None, *, runtime=None):
        super().__init__(host, parent)
        self.runtime = runtime
        self._initial = {key: host.config.get(key) for key in KEYS}
        self._written = {key: host.config.get(key) for key in KEYS}

        self.group_heading("AI 网页")
        card = self.section("站点", level=3)

        self.default_site = QComboBox(self)
        card.add_row(
            "默认站点",
            self.default_site,
            "菜单「AI 网页」与标题栏按钮默认打开这里；工具栏里仍可随时切换。",
            expand=True,
        )

        self.custom = QPlainTextEdit(self)
        self.custom.setPlaceholderText("每行一个：名称 | URL\n例如：自建 | http://127.0.0.1:3000")
        self.custom.setFixedHeight(84)
        card.add_row(
            "自定义站点",
            self.custom,
            "只接受 http/https；非法行会被丢弃。允许本地地址（自己的 TTS/网页端）。",
            expand=True,
        )

        open_button = make_button("在对话窗打开", self, primary=True)
        open_button.clicked.connect(self._open_view)
        card.add_buttons([open_button])

        status = self.section("内核", level=3)
        self.engine_label = QLabel(self)
        self.engine_label.setWordWrap(True)
        status.add_row(
            "网页内核",
            self.engine_label,
            "网页由系统的 Microsoft Edge WebView2 渲染；安装包只负责带上 QtWebView。",
            expand=True,
        )

        self.add_note(
            "网页页显示在 AI 对话窗的标题栏「网页 AI」里，和本地 AI 对话互不干扰："
            "切回对话不会影响网页里的登录态与对话。"
        )
        self._load()
        self.default_site.currentIndexChanged.connect(self._changed)
        self.custom.textChanged.connect(self._changed)

    # ---- 状态
    def _sites(self) -> tuple[sites.Site, ...]:
        return sites.all_sites(self.custom.toPlainText() if hasattr(self, "custom") else "")

    def _rebuild_sites(self) -> None:
        current = str(self.default_site.currentData() or "")
        self.default_site.blockSignals(True)
        self.default_site.clear()
        for item in self._sites():
            self.default_site.addItem(f"{item.name}（{item.url}）", item.id)
        index = self.default_site.findData(current)
        self.default_site.setCurrentIndex(index if index >= 0 else 0)
        self.default_site.blockSignals(False)

    def _load(self) -> None:
        self.custom.blockSignals(True)
        self.custom.setPlainText(str(self.host.config.get(SETTING_CUSTOM_SITES, "") or ""))
        self.custom.blockSignals(False)
        self._rebuild_sites()
        wanted = sites.default_site(
            self.host.config.get(SETTING_DEFAULT_SITE, sites.DEFAULT_SITE_ID)
        )
        index = self.default_site.findData(wanted.id)
        if index >= 0:
            self.default_site.blockSignals(True)
            self.default_site.setCurrentIndex(index)
            self.default_site.blockSignals(False)
        self.engine_label.setText(self._engine_text())

    def _engine_text(self) -> str:
        reason = engine.missing_reason()
        if reason:
            return engine.missing_hint(reason)
        version = engine.runtime_version()
        return f"可用：QtWebView + Edge WebView2 {version}".strip()

    # ---- 动作
    def _open_view(self) -> None:
        if self.runtime is not None:
            self.runtime.open(None)

    # ---- 保存/取消
    def _changed(self, *_args) -> None:
        values = {
            SETTING_DEFAULT_SITE: self.default_site.currentData(),
            SETTING_CUSTOM_SITES: self.custom.toPlainText().strip(),
        }
        # 只写与上次写出不同的键：整组回写会把别处刚改的值按旧控件值翻回去。
        for key, value in values.items():
            if self._written.get(key) != value:
                self.host.config.set(key, value)
                self._written[key] = value
        self._rebuild_sites()
        self.changed.emit()

    def dirty(self) -> bool:
        return any(
            self.host.config.get(key) != value for key, value in self._initial.items()
        )

    def confirm_save(self) -> None:
        self._initial = {key: self.host.config.get(key) for key in KEYS}
        self._written = dict(self._initial)
        self._rebuild_sites()

    def discard_changes(self) -> None:
        for key, value in self._initial.items():
            self.host.config.set(key, value)
        self._written = dict(self._initial)
        self._load()
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "AI 网页",
            "网页对话",
            "内置浏览器",
            "deepseek",
            "豆包",
            "千问",
            "kimi",
            "文心",
            "元宝",
            "星火",
            "自定义站点",
        ]
