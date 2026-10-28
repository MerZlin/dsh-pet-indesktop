"""设置页：唯一允许用 QtWidgets 的模块（文档 §1.9 / §4）。

开关、默认站点、额外白名单三项写 `host.config`（支持取消回滚），
「打开方式」一行是宿主能力的活证据：内嵌 Chromium / Edge 应用窗口 / 系统浏览器。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from petcore.ui.settings.base import SettingsPage, make_button, make_checkbox

from . import engine, external, sites
from .runtime import SETTING_DEFAULT_SITE, SETTING_ENABLED, SETTING_EXTRA_DOMAINS

KEYS = (SETTING_ENABLED, SETTING_DEFAULT_SITE, SETTING_EXTRA_DOMAINS)


class AiBrowserPage(SettingsPage):
    title = "AI 浏览器"

    def __init__(self, host, parent=None, *, runtime=None):
        super().__init__(host, parent)
        self.runtime = runtime
        self._initial = {key: host.config.get(key) for key in KEYS}
        self._written = {key: host.config.get(key) for key in KEYS}

        self.group_heading("AI 浏览器")
        card = self.section("开关与默认站点", level=3)

        self.enabled = make_checkbox(self)
        card.add_row(
            "启用 AI 浏览器",
            self.enabled,
            "关闭后菜单与命令只提示，不开任何窗口。",
            expand=True,
        )

        self.default_site = QComboBox(self)
        for site in sites.SITES:
            self.default_site.addItem(f"{site.name}（{site.url}）", site.id)
        card.add_row(
            "默认站点",
            self.default_site,
            "菜单「打开浏览器」从这里开始；工具栏仍可切换全部站点。",
            expand=True,
        )

        self.extra = QLineEdit(self)
        self.extra.setPlaceholderText("额外允许的域名，逗号分隔，例如 account.example.com")
        card.add_row(
            "额外白名单",
            self.extra,
            "只用于登录跳转这类已知域名；留空即严格只放行内置站点。",
            expand=True,
        )

        open_button = make_button("打开浏览器", self, primary=True)
        open_button.clicked.connect(self._open_browser)
        card.add_buttons([open_button])

        status = self.section("打开方式", level=3)
        self.engine_label = QLabel(self)
        self.engine_label.setWordWrap(True)
        status.add_row(
            "内核",
            self.engine_label,
            "扩展无法自带 Chromium：装到哪种宿主上，决定这里显示什么。",
            expand=True,
        )

        self.add_note(
            "白名单同时管两件事：菜单入口只列国模 AI 对话站；内嵌窗口的地址栏与"
            "主框架跳转也只放行这些域。"
        )
        self._load()
        self.enabled.toggled.connect(self._changed)
        self.default_site.currentIndexChanged.connect(self._changed)
        self.extra.textChanged.connect(self._changed)

    # ---- 状态
    def _load(self) -> None:
        for widget in (self.enabled, self.default_site, self.extra):
            widget.blockSignals(True)
        self.enabled.setChecked(bool(self.host.config.get(SETTING_ENABLED, True)))
        current = sites.default_site(self.host.config.get(SETTING_DEFAULT_SITE)).id
        index = self.default_site.findData(current)
        self.default_site.setCurrentIndex(index if index >= 0 else 0)
        self.extra.setText(str(self.host.config.get(SETTING_EXTRA_DOMAINS, "") or ""))
        for widget in (self.enabled, self.default_site, self.extra):
            widget.blockSignals(False)
        self.default_site.setEnabled(self.enabled.isChecked())
        self.extra.setEnabled(self.enabled.isChecked())
        self.engine_label.setText(self._engine_text())

    def _engine_text(self) -> str:
        if engine.engine_available():
            return "内嵌 Chromium（源码运行环境）：窗口内直接加载站点，白名单严格生效。"
        return (
            "此安装包未内置 Chromium：改用"
            + external.channel_label()
            + "打开。白名单只保证入口地址，窗口内跳转不受本扩展控制。"
        )

    # ---- 动作
    def _open_browser(self) -> None:
        if self.runtime is not None:
            self.runtime.open(None)
            return
        site = sites.default_site(self.host.config.get(SETTING_DEFAULT_SITE))
        if engine.engine_available():
            from .browser_window import open_browser

            open_browser(
                self._storage_dir(),
                extra_domains=sites.normalize_extra(
                    self.host.config.get(SETTING_EXTRA_DOMAINS, "")
                ),
                default_site_id=site.id,
            )
            return
        external.open_external(site.url)

    def _storage_dir(self) -> Path:
        if self.runtime is not None:
            return self.runtime.storage_dir()
        return Path(tempfile.gettempdir()) / "petcore-ai-browser"

    # ---- 保存/取消
    def _changed(self, *_args) -> None:
        values = {
            SETTING_ENABLED: self.enabled.isChecked(),
            SETTING_DEFAULT_SITE: self.default_site.currentData(),
            SETTING_EXTRA_DOMAINS: self.extra.text().strip(),
        }
        # 只写与上次写出不同的键：整组回写会把别处刚改的值按旧控件值翻回去。
        for key, value in values.items():
            if self._written.get(key) != value:
                self.host.config.set(key, value)
                self._written[key] = value
        self.default_site.setEnabled(self.enabled.isChecked())
        self.extra.setEnabled(self.enabled.isChecked())
        self.changed.emit()

    def dirty(self) -> bool:
        return any(
            self.host.config.get(key) != value for key, value in self._initial.items()
        )

    def confirm_save(self) -> None:
        self._initial = {key: self.host.config.get(key) for key in KEYS}
        self._written = dict(self._initial)

    def discard_changes(self) -> None:
        for key, value in self._initial.items():
            self.host.config.set(key, value)
        self._written = dict(self._initial)
        self._load()
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "AI 浏览器",
            "deepseek",
            "豆包",
            "千问",
            "kimi",
            "文心",
            "元宝",
            "星火",
        ]
