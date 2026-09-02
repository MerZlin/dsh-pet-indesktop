# -*- coding: utf-8 -*-
"""Modern-inspired sidebar settings panel used by the modern context menu.

批 6-7：自定义控件库整体搬移至 settings_widgets.py（逐行搬移）；本文件保留
设置页构建与配置写回，import 控件库并 re-export（维持测试兼容），内联 QSS
抽至 pet/settings_styles*.qss（与 pet/chat/*.qss 同约定，运行时读取）。
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import autostart as autostart_mod
from . import catalog
from .agent_link import AgentLinkManager
from .click_sound import warm_click_sound_effects
from .config import (
    DEFAULT_CONTEXT_MENU_APPEARANCE,
    DEFAULT_MENU_EASTER_EGG,
    DEFAULT_QUICK_LAUNCH_APPS,
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    _float_or_default,
)
from .context_menus.icons import vector_widget_icon
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset, store_fun_asset
from .settings_widgets import (
    AUDIO_NAME_FILTER,
    BROWSER_CONTROL_STYLESHEET,
    BrowserDoubleSpinBox,
    BrowserSpinBox,
    ClickSoundPackPicker,
    ColorPicker,
    ColorSwatchButton,
    ModernSelect,
    QuickLaunchEditor,
    ResourcePathPicker,
    SettingRow,
    SettingsCard,
    SettingsSection,
    ToggleSwitch,
    _system_dark,
)
from .speech_bubble import BUBBLE_STYLE_PRESETS


def _system_font_families() -> tuple[str, ...]:
    """缓存系统字体族列表。

    macOS 上 QFontDatabase.families() 走 CoreText 枚举，首次调用可达数百 ms，
    设置窗口每次打开都重建实例，同步枚举会明显拖慢打开速度。
    """
    if _system_font_families._cache is None:
        _system_font_families._cache = tuple(QFontDatabase.families())
    return _system_font_families._cache


_system_font_families._cache = None


def _line_edit(text: str = "", *, password: bool = False, width: int = 240) -> QLineEdit:
    edit = QLineEdit(text)
    edit.setMinimumWidth(width)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class _AiSettingsPage(QWidget):
    test_done = Signal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        # No-chat bundles exclude pet.chat and never construct this optional page.
        from .chat.models import ProviderConfig, SecretStore
        from .chat.providers import test_connection

        self.config = config
        self._provider_config_type = ProviderConfig
        self._secret_store_type = SecretStore
        self._test_connection = test_connection
        self.settings = config.chat_settings()
        provider = self.settings.active_config
        self._test_thread = None
        self._provider_drafts: dict[str, dict] = {}
        self._deleted_provider_ids: set[str] = set()
        self._loading_provider = False
        self.test_done.connect(self._on_test_done)

        self.provider_combo = ModernSelect(self, width=230)
        self.add_provider_btn = QPushButton("添加", self)
        self.delete_provider_btn = QPushButton("删除", self)
        for pid, provider_item in self.settings.providers.items():
            self.provider_combo.addItem(self._provider_label(provider_item), pid)
        self.provider_combo.setCurrentData(self.settings.active_provider)

        self.name = _line_edit(provider.name)
        self.url = _line_edit(provider.base_url)
        self.model = _line_edit(provider.model)
        self.key = _line_edit(password=True)
        self.prompt = QPlainTextEdit(self.settings.default_system_prompt)
        self.prompt.setMinimumSize(240, 80)
        self.timeout = BrowserSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(provider.timeout))
        self.temperature = BrowserDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(provider.temperature)
        self.tokens = BrowserSpinBox()
        self.tokens.setRange(1, 32768)
        self.tokens.setValue(provider.max_tokens)
        self.skip_ssl = ToggleSwitch()
        self.skip_ssl.setChecked(not provider.verify_ssl)
        self.system_notify_check = ToggleSwitch()
        self.system_notify_check.setChecked(bool(config.get("system_notifications_enabled", True)))
        self.chat_ui_style = ModernSelect(self, width=190)
        self.chat_ui_style.addItem("肥鱼版 DeepSeek", "modern")
        self.chat_ui_style.addItem("肥鱼牌小手机", "classic")
        self.chat_ui_style.setCurrentData(str(config.get("chat_ui_style", "modern")))
        self.vision_same = ToggleSwitch()
        self.vision_same.setChecked(bool(provider.vision_same_as_chat))
        self.vision_model = _line_edit(provider.vision_model)
        self.vision_url = _line_edit(provider.vision_base_url)
        self.vision_key = _line_edit(password=True)

        from .chat.themes import theme_names
        self._background_themes = list(theme_names())
        self._background_values = {
            "classic": str(config.get("chat_background", "") or ""),
            "modern": str(config.get("modern_chat_background", "") or ""),
        }
        self._background_display = {
            "classic": {
                "opacity": int(config.get("chat_background_opacity", 100) or 100),
                "fill": str(config.get("chat_background_fill", "cover") or "cover"),
            },
            "modern": {
                "opacity": int(config.get("modern_chat_background_opacity", 100) or 100),
                "fill": str(config.get("modern_chat_background_fill", "cover") or "cover"),
            },
        }
        self._background_style = str(self.chat_ui_style.currentData() or "modern")
        self.background_select = ModernSelect(self, width=180)
        self.background_picker = ResourcePathPicker(
            "",
            parent=self,
        )
        self.background_opacity = BrowserSpinBox(self)
        self.background_opacity.setRange(10, 100)
        self.background_opacity.setSuffix(" %")
        self.message_card_opacity = BrowserSpinBox(self)
        self.message_card_opacity.setRange(10, 100)
        self.message_card_opacity.setSuffix(" %")
        self.message_card_opacity.setValue(
            int(config.get("modern_chat_card_opacity", 84) or 84)
        )
        self.background_fill = ModernSelect(self, width=160)
        self.background_fill.addItem("填充裁剪", "cover")
        self.background_fill.addItem("完整适应", "contain")
        self.background_fill.addItem("拉伸铺满", "stretch")
        self._populate_background_options(self._background_style)
        self.chat_ui_style.currentIndexChanged.connect(self._on_chat_ui_style_changed)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._run_test)
        self.test_result = QLabel("验证当前 Provider、API 地址和凭据是否可用。")
        self.test_result.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        provider_row = QWidget()
        provider_lay = QHBoxLayout(provider_row)
        provider_lay.setContentsMargins(0, 0, 0, 0)
        provider_lay.setSpacing(6)
        provider_lay.addWidget(self.provider_combo, 1)
        provider_lay.addWidget(self.add_provider_btn)
        provider_lay.addWidget(self.delete_provider_btn)
        root.addWidget(SettingsSection("API 列表", [
            SettingRow(
                "provider_list", "API 列表",
                "选择要编辑/切换的模型服务；保存后当前选中项会作为 active_provider 生效。",
                provider_row,
            ),
        ], self))
        root.addWidget(SettingsSection("模型与连接", [
            SettingRow("provider_name", "Provider 名称", "用于区分当前使用的模型服务。", self.name),
            SettingRow("api_url", "API 地址", "OpenAI Chat Completions 兼容服务地址。", self.url),
            SettingRow("model", "模型", "发送请求时使用的模型标识。", self.model),
            SettingRow("api_key", "API Key", "凭据优先保存到系统钥匙串。", self.key),
            SettingRow("system_prompt", "System Prompt", "定义桌宠对话时的身份、语气和行为。", self.prompt, stacked=True),
            SettingRow("connection_test", "连接测试", self.test_result.text(), self.test_button),
        ], self))
        root.addWidget(SettingsSection("系统通知", [
            SettingRow(
                "system_notifications_enabled", "系统通知",
                "对话完成 / 生成失败 / 需要授权时，即使切走窗口也会在桌面右下角提醒。",
                self.system_notify_check,
            ),
        ], self))
        vision_rows = [
            SettingRow("vision_same", "视觉模型复用聊天模型", "开启后自动选择兼容的视觉模型，用于“看看屏幕”。", self.vision_same),
            SettingRow("vision_model", "视觉模型", "关闭复用后使用的多模态模型标识；留空则自动推导。", self.vision_model),
            SettingRow("vision_url", "视觉 API 地址", "留空复用聊天服务地址。", self.vision_url),
            SettingRow("vision_key", "视觉 API Key", "留空复用聊天服务凭据。", self.vision_key),
        ]
        root.addWidget(SettingsSection("视觉能力", vision_rows, self))
        self._vision_override_rows = vision_rows[1:]
        self.vision_same.toggled.connect(self._update_vision_visibility)
        self._update_vision_visibility(self.vision_same.isChecked())
        self._test_row = self.findChild(SettingRow, "settingRow_connection_test")
        if self._test_row is not None:
            self.test_result = self._test_row.hint_label
        root.addWidget(SettingsSection("生成参数", [
            SettingRow("timeout", "请求超时", "等待模型服务响应的最长时间。", self.timeout),
            SettingRow("temperature", "Temperature", "数值越高，回答越随机。", self.temperature),
            SettingRow("max_tokens", "最大输出 Token", "限制模型单次回复的最大长度。", self.tokens),
            SettingRow("skip_ssl", "跳过 SSL 证书验证", "仅用于本地网关或自签名证书。", self.skip_ssl),
        ], self))
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.add_provider_btn.clicked.connect(self._add_provider)
        self.delete_provider_btn.clicked.connect(self._delete_provider)
        self._update_provider_buttons()
        root.addStretch(1)

    def appearance_rows(self) -> list[SettingRow]:
        """Controls visually owned by the Appearance page, persisted with AI settings."""
        rows = [
            SettingRow(
                "chat_ui_style", "对话窗口",
                "肥鱼版 DeepSeek 提供宽屏现代体验；肥鱼牌小手机保留紧凑经典体验。",
                self.chat_ui_style,
            ),
            SettingRow(
                "chat_background", "对话背景",
                "肥鱼版 DeepSeek 与肥鱼牌小手机均支持纯色、内置主题或自定义图片。",
                self.background_select,
            ),
            SettingRow("chat_background_file", "自定义背景图片", "支持常见图片格式，使用绝对路径。", self.background_picker),
            SettingRow("chat_background_opacity", "图片不透明度", "调节背景图可见强度；消息卡片会独立保证正文可读。", self.background_opacity),
            SettingRow("chat_background_fill", "填充方式", "选择裁剪铺满、完整显示或拉伸铺满窗口。", self.background_fill),
            SettingRow(
                "modern_chat_card_opacity", "消息卡片不透明度",
                "调节肥鱼版 DeepSeek 消息卡片透出背景的程度。",
                self.message_card_opacity,
            ),
        ]
        self._background_file_row = rows[-4]
        self._background_detail_rows = rows[-3:-1]
        self._message_card_opacity_row = rows[-1]
        self.background_select.currentIndexChanged.connect(self._update_background_visibility)
        self._update_background_visibility()
        return rows

    def _current_background_value(self) -> str:
        value = self.background_select.currentData()
        return self.background_picker.text() if value == "custom" else str(value or "")

    def _capture_background_value(self) -> None:
        self._background_values[self._background_style] = self._current_background_value()
        self._background_display[self._background_style] = {
            "opacity": self.background_opacity.value(),
            "fill": str(self.background_fill.currentData() or "cover"),
        }

    def _populate_background_options(self, style: str) -> None:
        self.background_select.clear()
        self.background_select.addItem("纯色背景", "")
        # 内置主题两种对话窗口风格都可用（肥鱼版 DeepSeek 与肥鱼牌小手机一致）
        for key, label in self._background_themes:
            self.background_select.addItem(label, f"builtin:{key}")
        self.background_select.addItem("自定义图片", "custom")
        value = str(self._background_values.get(style, "") or "")
        if value.startswith("builtin:") and self.background_select.findData(value) >= 0:
            self.background_select.setCurrentData(value)
            self.background_picker.setText("")
        elif value:
            self.background_select.setCurrentData("custom")
            self.background_picker.setText(value)
        else:
            self.background_select.setCurrentData("")
            self.background_picker.setText("")
        display = self._background_display.get(style, {})
        self.background_opacity.setValue(int(display.get("opacity", 100)))
        self.background_fill.setCurrentData(str(display.get("fill", "cover")))
        self._update_background_visibility()

    def _on_chat_ui_style_changed(self, _index: int = -1) -> None:
        self._capture_background_value()
        self._background_style = str(self.chat_ui_style.currentData() or "modern")
        self._populate_background_options(self._background_style)

    def _update_vision_visibility(self, reuse_chat_model: bool) -> None:
        for row in self._vision_override_rows:
            row.setVisible(not reuse_chat_model)
        card = self._vision_override_rows[0].parentWidget() if self._vision_override_rows else None
        if isinstance(card, SettingsCard):
            card.refresh_separators()

    def _update_background_visibility(self, _index: int = -1) -> None:
        row = getattr(self, "_background_file_row", None)
        if row is not None:
            row.setVisible(self.background_select.currentData() == "custom")
            has_image = bool(self.background_select.currentData())
            for detail_row in getattr(self, "_background_detail_rows", []):
                detail_row.setVisible(has_image)
            card = row.parentWidget()
            if isinstance(card, SettingsCard):
                card.refresh_separators()
        card_opacity_row = getattr(self, "_message_card_opacity_row", None)
        if card_opacity_row is not None:
            card_opacity_row.setVisible(self._background_style == "modern")

    # ------------------------------------------------------------ API 列表管理
    @staticmethod
    def _provider_label(p) -> str:
        name = str(p.name or p.provider_id)
        model = str(p.model or '').strip()
        return f"{name} · {model}" if model else name

    def _capture_current_draft(self) -> None:
        pid = self.settings.active_provider
        if not pid or pid not in self.settings.providers:
            return
        existing = self._provider_drafts.get(pid, {})
        key_text = self.key.text()
        vkey_text = self.vision_key.text()
        self._provider_drafts[pid] = {
            "name": self.name.text().strip(),
            "base_url": self.url.text().strip(),
            "model": self.model.text().strip(),
            # 输入框为空表示“不修改/不覆盖”，保留草稿里已录入但尚未保存的 Key；
            # 否则 _load_provider_ui() 清空输入框后会把草稿 Key 覆盖成空。
            "key": key_text if key_text else existing.get("key", ""),
            "timeout": float(self.timeout.value()),
            "temperature": float(self.temperature.value()),
            "max_tokens": int(self.tokens.value()),
            "vision_model": self.vision_model.text().strip(),
            "vision_same_as_chat": self.vision_same.isChecked(),
            "vision_base_url": self.vision_url.text().strip(),
            "vision_key": vkey_text if vkey_text else existing.get("vision_key", ""),
            "verify_ssl": not self.skip_ssl.isChecked(),
        }

    def _load_provider_ui(self, provider_id: str) -> None:
        p = self.settings.providers.get(provider_id)
        if p is None:
            return
        draft = self._provider_drafts.get(provider_id, {})
        self.settings.active_provider = provider_id
        self.name.setText(draft.get("name") if draft.get("name") is not None else p.name)
        self.url.setText(draft.get("base_url") if draft.get("base_url") is not None else p.base_url)
        self.model.setText(draft.get("model") if draft.get("model") is not None else p.model)
        self.key.clear()
        self.timeout.setValue(int(draft.get("timeout", p.timeout)))
        self.temperature.setValue(float(draft.get("temperature", p.temperature)))
        self.tokens.setValue(int(draft.get("max_tokens", p.max_tokens)))
        self.vision_model.setText(draft.get("vision_model", p.vision_model))
        self.vision_same.setChecked(bool(draft.get("vision_same_as_chat", p.vision_same_as_chat)))
        self.vision_url.setText(draft.get("vision_base_url", p.vision_base_url))
        self.vision_key.clear()
        self.skip_ssl.setChecked(not bool(draft.get("verify_ssl", p.verify_ssl)))
        self._update_provider_buttons()

    def _on_provider_changed(self, _index: int = -1) -> None:
        if self._loading_provider:
            return
        self._capture_current_draft()
        pid = self.provider_combo.currentData()
        if pid and pid in self.settings.providers:
            self._load_provider_ui(pid)

    def _new_provider_id(self) -> str:
        # 避免复用已删除的 provider_id：否则保存合并时新建项会被删除集合过滤掉。
        used = set(self.settings.providers) | set(self._deleted_provider_ids)
        i = len(self.settings.providers) + 1
        while f"api-{i}" in used:
            i += 1
        return f"api-{i}"

    def _add_provider(self) -> None:
        self._capture_current_draft()
        base = self.settings.active_config
        new_id = self._new_provider_id()
        new = self._provider_config_type(
            new_id,
            name=f"{base.name} 副本",
            base_url=base.base_url,
            chat_path=base.chat_path,
            model=base.model,
            api_key_ref=f"provider/{new_id}",
            timeout=base.timeout,
            temperature=base.temperature,
            max_tokens=base.max_tokens,
            vision_model=base.vision_model,
            vision_same_as_chat=base.vision_same_as_chat,
            vision_base_url=base.vision_base_url,
            vision_api_key_ref=f"provider/{new_id}/vision",
            verify_ssl=base.verify_ssl,
        )
        self.settings.providers[new_id] = new
        self._provider_drafts[new_id] = {
            "name": new.name,
            "base_url": new.base_url,
            "model": new.model,
            "key": "",
            "timeout": new.timeout,
            "temperature": new.temperature,
            "max_tokens": new.max_tokens,
            "vision_model": new.vision_model,
            "vision_same_as_chat": new.vision_same_as_chat,
            "vision_base_url": new.vision_base_url,
            "vision_key": "",
            "verify_ssl": new.verify_ssl,
        }
        self._loading_provider = True
        try:
            self.provider_combo.addItem(self._provider_label(new), new_id)
            self.provider_combo.setCurrentIndex(self.provider_combo.count() - 1)
        finally:
            self._loading_provider = False
        self.settings.active_provider = new_id
        self._load_provider_ui(new_id)
        self._update_provider_buttons()

    def _delete_provider(self) -> None:
        if len(self.settings.providers) <= 1:
            return
        pid = self.provider_combo.currentData()
        if not pid or pid not in self.settings.providers:
            return
        self.settings.providers.pop(pid, None)
        self._provider_drafts.pop(pid, None)
        self._deleted_provider_ids.add(pid)
        self._loading_provider = True
        try:
            self.provider_combo.clear()
            for p in self.settings.providers.values():
                self.provider_combo.addItem(self._provider_label(p), p.provider_id)
            self.settings.active_provider = next(iter(self.settings.providers))
            self.provider_combo.setCurrentData(self.settings.active_provider)
        finally:
            self._loading_provider = False
        self._load_provider_ui(self.settings.active_provider)
        self._update_provider_buttons()

    def _update_provider_buttons(self) -> None:
        self.delete_provider_btn.setEnabled(len(self.settings.providers) > 1)

    def _apply_draft_to_provider(self, provider_id: str) -> None:
        p = self.settings.providers.get(provider_id)
        draft = self._provider_drafts.get(provider_id)
        if p is None or not draft:
            return
        if draft.get("name"):
            p.name = draft["name"]
        p.base_url = draft.get("base_url") or p.base_url
        p.model = draft.get("model") or p.model
        p.timeout = float(draft.get("timeout", p.timeout))
        p.temperature = float(draft.get("temperature", p.temperature))
        p.max_tokens = int(draft.get("max_tokens", p.max_tokens))
        p.vision_model = draft.get("vision_model", p.vision_model)
        p.vision_same_as_chat = bool(draft.get("vision_same_as_chat", p.vision_same_as_chat))
        p.vision_base_url = draft.get("vision_base_url", p.vision_base_url)
        p.verify_ssl = bool(draft.get("verify_ssl", p.verify_ssl))
        key = str(draft.get("key") or "")
        if key:
            p.api_key_ref = p.api_key_ref or f"provider/{provider_id}"
            if not self._secret_store_type().set(p.api_key_ref, key):
                p.api_key = key
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")
        vkey = str(draft.get("vision_key") or "")
        if vkey:
            p.vision_api_key_ref = p.vision_api_key_ref or f"provider/{provider_id}/vision"
            if not self._secret_store_type().set(p.vision_api_key_ref, vkey):
                p.vision_api_key = vkey
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")

    def provisional_config(self):
        provider = self.settings.active_config
        return self._provider_config_type(
            provider.provider_id,
            self.name.text().strip() or provider.name,
            self.url.text().strip(),
            provider.chat_path,
            self.model.text().strip(),
            provider.api_key_ref,
            # 表单未填时回退钥匙串：凭据默认存系统钥匙串，直接读 api_key 为空
            self.key.text() or provider.api_key or self._secret_store_type().get(provider.api_key_ref),
            float(self.timeout.value()),
            float(self.temperature.value()),
            int(self.tokens.value()),
            verify_ssl=not self.skip_ssl.isChecked(),
        )

    def _run_test(self) -> None:
        if self._test_thread is not None and self._test_thread.is_alive():
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.test_result.setText("正在连接模型服务…")
        self._test_thread = threading.Thread(
            target=self._run_test_worker,
            args=(self.provisional_config(),),
            daemon=True,
            name="pet-modern-settings-connection-test",
        )
        self._test_thread.start()

    def _run_test_worker(self, provider) -> None:
        self.test_done.emit(*self._test_connection(provider, timeout=10.0))

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        self.test_result.setText(message)
        self.test_result.setStyleSheet("color: #16843b;" if ok else "color: #c9362b;")
        self._test_thread = None

    def save(self) -> None:
        # 保存前基于磁盘最新配置重取快照：另一个设置窗口（AI 设置/桌宠设置 AI 页）
        # 可能在本窗口打开期间改过 Provider 结构；先保留本窗口的 provider 草稿/
        # 新增/删除，再与磁盘快照合并，避免覆盖其它窗口新增的结构。
        old_settings = self.settings
        self._capture_current_draft()
        self.settings = self.config.chat_settings()
        for pid in list(self.settings.providers):
            if pid in self._deleted_provider_ids:
                self.settings.providers.pop(pid, None)
        for pid, provider in old_settings.providers.items():
            if pid not in self.settings.providers and pid not in self._deleted_provider_ids:
                self.settings.providers[pid] = provider
        active_pid = self.provider_combo.currentData()
        if active_pid in self.settings.providers:
            self.settings.active_provider = active_pid
        elif self.settings.providers:
            self.settings.active_provider = next(iter(self.settings.providers))
        for pid in list(self.settings.providers):
            self._apply_draft_to_provider(pid)
        self.settings.default_system_prompt = self.prompt.toPlainText().strip()
        self.config.set("chat_ui_style", self.chat_ui_style.currentData())
        self._capture_background_value()
        self.config.set("chat_background", self._background_values["classic"])
        self.config.set("modern_chat_background", self._background_values["modern"])
        self.config.set("chat_background_opacity", self._background_display["classic"]["opacity"])
        self.config.set("chat_background_fill", self._background_display["classic"]["fill"])
        self.config.set("modern_chat_background_opacity", self._background_display["modern"]["opacity"])
        self.config.set("modern_chat_background_fill", self._background_display["modern"]["fill"])
        self.config.set("modern_chat_card_opacity", self.message_card_opacity.value())
        self.config.set("system_notifications_enabled", self.system_notify_check.isChecked())
        self.config.set_chat_settings(self.settings)


class ModernSettingsDialog(QDialog):
    """Settings window matching Modern's sidebar and rounded-card hierarchy."""

    settings_saved = Signal()

    def __init__(self, config, parent=None, *, include_ai: bool = True):
        super().__init__(parent)
        self.config = config
        self.include_ai = bool(include_ai)
        self.ai_page = None
        self.setProperty("modernStyle", True)
        self.setProperty("menuStyle", "modern")
        self.setWindowTitle("桌宠设置")
        self.resize(800, 560)
        self.setMinimumSize(720, 500)
        self._positioned_away = False
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(13)
        self.setFont(font)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar_pane = QFrame(self)
        sidebar_pane.setObjectName("sidebarPane")
        sidebar_pane.setFixedWidth(188)
        sidebar_layout = QVBoxLayout(sidebar_pane)
        sidebar_layout.setContentsMargins(12, 16, 12, 12)
        sidebar_layout.setSpacing(9)
        self.save_exit_button = QPushButton("保存并退出", sidebar_pane)
        self.save_exit_button.setObjectName("saveAndExit")
        self.save_exit_button.setIcon(vector_widget_icon(self.save_exit_button, "back", 16))
        self.save_exit_button.clicked.connect(self._save)
        self.save_exit_button.setAutoDefault(False)
        self.save_exit_button.setDefault(False)
        sidebar_layout.addWidget(self.save_exit_button)
        self.search_edit = QLineEdit(sidebar_pane)
        self.search_edit.setObjectName("settingsSearch")
        self.search_edit.setPlaceholderText("搜索设置…")
        self.search_edit.addAction(
            vector_widget_icon(self, "search", 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.installEventFilter(self)
        sidebar_layout.addWidget(self.search_edit)
        self.search_status = QLabel("", sidebar_pane)
        self.search_status.setObjectName("searchStatus")
        self.search_status.setWordWrap(True)
        self.search_status.hide()
        sidebar_layout.addWidget(self.search_status)
        self.sidebar = QListWidget(sidebar_pane)
        self.sidebar.setObjectName("settingsSidebar")
        self.sidebar.setSpacing(2)
        sidebar_layout.addWidget(self.sidebar, 1)

        self.pages = QStackedWidget(self)
        body.addWidget(sidebar_pane)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        self._build_pet_controls()
        if include_ai:
            self.ai_page = _AiSettingsPage(config, self)

        general_content = QWidget()
        general_layout = QVBoxLayout(general_content)
        general_layout.setContentsMargins(0, 0, 0, 0)
        general_layout.setSpacing(18)
        autostart_desc = "登录系统后自动启动桌宠。" if not self.config.instance_id else "登录系统后自动启动桌宠。（仅主桌宠可设置）"
        launch_rows = [
            SettingRow("autostart", "开机自启", autostart_desc, self.autostart_check),
        ]
        if sys.platform == "darwin":
            launch_rows.append(SettingRow(
                "dock_icon", "显示 Dock 图标", "在 macOS Dock 中显示桌宠应用；关闭后仍可通过桌宠和托盘操作。",
                self.dock_icon_check,
            ))
        general_layout.addWidget(SettingsSection("应用启动", launch_rows, general_content))
        window_rows = [
            SettingRow("on_top", "窗口置顶", "始终将桌宠保持在其他窗口上方。", self.on_top_check),
        ]
        if sys.platform == "win32":
            window_rows.extend([
                SettingRow("auto_hide_fullscreen", "全屏时自动隐藏", "全屏游戏或视频期间自动隐藏桌宠。", self.auto_hide_fullscreen_check),
                SettingRow("cursor_hidden_passthrough", "光标隐藏时自动穿透", "Windows 光标隐藏后，桌宠自动穿透点击；光标出现立即恢复。适用于游戏，也可能影响自动隐藏光标的视频播放器。", self.cursor_hidden_passthrough_check),
                SettingRow("stream_capture", "直播捕获兼容", "让 OBS 等工具能够枚举并捕获桌宠窗口。", self.stream_capture_check),
            ])
        general_layout.addWidget(SettingsSection("窗口与系统", window_rows, general_content))
        if self.balance_refresh_spin is not None:
            general_layout.addWidget(SettingsSection("后台服务", [
                SettingRow("balance_refresh", "余额自动刷新", "设置后台刷新间隔；0 分钟表示关闭。", self.balance_refresh_spin),
                SettingRow("balance_tier_mode", "峰谷提示文案", "选择 DeepSeek 高峰/空闲提示的显示风格。", self.balance_tier_mode_select),
                SettingRow("balance_tier_peak", "高峰自定义文本", "仅“自定义”模式生效；留空回退默认“高峰”。", self.balance_tier_peak_edit, stacked=True),
                SettingRow("balance_tier_idle", "空闲自定义文本", "仅“自定义”模式生效；留空回退默认“空闲”。", self.balance_tier_idle_edit, stacked=True),
                SettingRow("balance_tier_color", "峰谷提示颜色", "开启后高峰显示红色、低谷显示绿色；关闭则使用普通气泡文字颜色。", self.balance_tier_color_check),
            ], general_content))
        general_layout.addStretch(1)
        self._add_page("常规", "settings", self._page_shell("常规", general_content))

        island_content = QWidget()
        island_layout = QVBoxLayout(island_content)
        island_layout.setContentsMargins(0, 0, 0, 0)
        island_layout.setSpacing(18)
        island_layout.addWidget(SettingsSection("灵动岛", [
            SettingRow("dynamic_island_enabled", "启用灵动岛", "显示独立胶囊小窗；桌宠隐藏后仍可常驻。", self.island_enabled_check),
            SettingRow("dynamic_island_icon", "显示图标", "在胶囊左侧显示角色图标。", self.island_icon_check),
            SettingRow("dynamic_island_name", "显示名称", "显示当前角色名称。", self.island_name_check),
            SettingRow("dynamic_island_info", "显示信息槽", "显示时间/余额/自定义短文本等信息。", self.island_info_check),
            SettingRow("dynamic_island_status", "显示状态灯", "显示右侧状态圆点。", self.island_status_check),
            SettingRow("dynamic_island_info_mode", "信息槽内容", "选择信息槽显示的内容；自定义文本在下方填写。", self.island_info_mode_select),
            SettingRow("dynamic_island_style", "背景风格", "黑色 / 白色 / 苹果式玻璃质感。", self.island_style_select),
            SettingRow("dynamic_island_icon", "图标", "选择灵动岛左侧显示的预制 emoji 图标。", self.island_icon_select),
            SettingRow("dynamic_island_custom_text", "自定义短文本", "信息槽选择“自定义短文本”时显示的内容。", self.island_custom_text_edit, stacked=True),
        ], island_content))
        island_layout.addStretch(1)
        self._add_page("灵动岛", "island", self._page_shell("灵动岛", island_content))

        behavior_content = QWidget()
        behavior_layout = QVBoxLayout(behavior_content)
        behavior_layout.setContentsMargins(0, 0, 0, 0)
        behavior_layout.setSpacing(16)
        behavior_layout.addWidget(SettingsSection("动画", [
            SettingRow("playback_speed", "播放速率", "控制所有桌宠动画的播放速度。", self.speed_select),
            SettingRow("animation_gap", "动作等待间隔", "非待机动作之间的休息时间；0 秒表示连续播放。", self.gap_spin),
            SettingRow("idle_low_fps", "闲置降帧", "一段时间不操作桌宠时，动画按半帧率呈现（24fps 素材 → 12fps 效果）；任何交互立即恢复全帧率。", self.idle_low_fps_check),
            SettingRow("no_move", "不移动", "暂停桌宠在桌面上的自动移动。", self.no_move_check),
            SettingRow("mouse_through", "鼠标穿透", "开启后桌宠不接收鼠标事件，点击穿透到下层窗口。", self.mouse_through_check),
            SettingRow("music_sing", "音乐自动唱歌", "检测到后台播放音乐时，自动播放唱歌动画。", self.music_sing_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("拖拽与弹射", [
            SettingRow("drag_physics", "拖动物理", "启用拖拽惯性、重力和边缘反弹。", self.drag_physics_check),
            SettingRow("throw_strength", "甩出力度", "控制桌宠被甩出或弹射发射时的最大速度限制。", self.throw_strength_select),
            SettingRow("slingshot_enabled", "弹弓弹射", "拖拽桌宠时点击右键进入蓄力瞄准，松开左键弹射飞出（Esc或右键取消）。", self.slingshot_check),
            SettingRow("lock_position", "锁定位置", "桌宠固定不动，无法拖动（点击互动仍有效）。", self.lock_position_check),
            SettingRow("shift_drag", "SHIFT+左键拖动", "开启后必须按住 SHIFT 再左键才能拖动桌宠。", self.shift_drag_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("多开碰撞", [
            SettingRow("collision_enabled", "碰撞开关", "多开桌宠之间发生碰撞物理互动。开启鼠标穿透的桌宠仍会参与碰撞，锁定位置的桌宠作为固定障碍。", self.collision_enabled_check),
            SettingRow("collision_restitution", "弹性系数", "碰撞反弹的能量保留程度（0~1.00，默认 0.82）。", self.collision_restitution_spin),
            SettingRow("collision_friction", "摩擦系数", "擦边碰撞时的切向摩擦阻力（0~0.30，默认 0.08）。", self.collision_friction_spin),
            SettingRow("collision_mass_scale", "质量倍率", "桌宠的基础质量加权倍率（0.5~2.0，默认 1.0）。", self.collision_mass_scale_spin),
            SettingRow("collision_impulse_cap", "冲量上限", "单次碰撞能施加的最大冲量上限（1000~12000，默认 9000）。", self.collision_impulse_cap_spin),
            SettingRow("collision_sound_enabled", "碰撞音效", "碰撞时播放音效反馈。", self.collision_sound_check),
            SettingRow("collision_sound_volume", "碰撞音量", "调整碰撞音效播放音量。", self.collision_sound_volume_spin),
        ], behavior_content))
        self.collision_policy_note = QLabel("碰撞参数由当前协调者桌宠的设置决定")
        self.collision_policy_note.setObjectName("settingHint")
        self.collision_policy_note.setWordWrap(True)
        self.collision_policy_note.setContentsMargins(14, 0, 14, 0)
        behavior_layout.addWidget(self.collision_policy_note)
        click_rows = [
            SettingRow("click_sound", "点击音效", "点击桌宠时播放轻量反馈音效。", self.click_sound_check),
            SettingRow("click_sound_pack", "音效音源", "选择预设音效包、自定义音频文件或文件夹随机播放。", self.click_sound_picker, stacked=True),
            SettingRow("click_sound_volume", "音效音量", "调整点击音效播放音量。", self.click_sound_volume_spin),
            SettingRow("click_sound_preview", "试听音效", "测试当前选择的点击音效。", self.click_sound_preview_btn),
            SettingRow("click_self_talk", "点击触发自言自语", "点击时随机显示一条自言自语内容。", self.click_self_talk_check),
        ]
        if self.click_balance_check is not None:
            click_rows.insert(4, SettingRow(
                "click_balance", "点击显示余额", "点击桌宠时查询并用气泡展示模型服务余额。",
                self.click_balance_check,
            ))
        behavior_layout.addWidget(SettingsSection("点击反馈", click_rows, behavior_content))
        behavior_layout.addWidget(SettingsSection("自言自语", [
            SettingRow("self_talk", "气泡自言自语", "让桌宠偶尔显示一条随机思考气泡。", self.self_talk_check),
            SettingRow("self_talk_duration", "显示时间", "每条文字或图片气泡保持显示的时间。", self.self_talk_duration_spin),
            SettingRow("self_talk_min", "最短间隔", "上一条气泡消失后，到下一条出现前的最短空闲时间。", self.min_spin),
            SettingRow("self_talk_max", "最长间隔", "上一条气泡消失后，到下一条出现前的最长空闲时间。", self.max_spin),
            SettingRow("self_talk_texts", "候选内容", "每行一条；留空时恢复内置文本。", self.texts_edit, stacked=True),
            SettingRow("self_talk_images", "图片目录", "从目录中的常见图片格式随机选择；默认使用内置彩蛋图片池，留空时只显示文本。", self.self_talk_image_dir_picker, stacked=True),
            SettingRow("self_talk_image_scale", "配图大小", "气泡里配图的显示尺寸（100% 为默认）。", self.self_talk_image_scale_spin),
            SettingRow("click_talk_bindings", "点击动画台词绑定", "为每个点击动画设置专属自言自语台词。", self.click_talk_bindings_btn),
        ], behavior_content))
        # Agent 联动：每个 Agent 一行自定义思考文案
        agent_thinking_rows = []
        for agent_key, edit in self.thinking_text_edits.items():
            agent_name = AgentLinkManager.AGENT_NAMES.get(agent_key, agent_key)
            default = AgentLinkManager._THINKING_DEFAULTS.get(agent_key, f"{agent_name} 正在深度烧烤……")
            agent_thinking_rows.append(
                SettingRow(f"agent_thinking_{agent_key}", f"{agent_name} 思考文案",
                           f"默认：{default}；支持 {{name}} 占位符；留空用默认。",
                           edit, stacked=True)
            )
        behavior_layout.addWidget(SettingsSection("Agent 联动 · 思考气泡文案", agent_thinking_rows, behavior_content))

        # Agent 联动：音效设置
        agent_sound_rows = [
            SettingRow("agent_sound_enabled", "Agent 音效联动", "当 Agent 开始工作、任务完成或发生错误时播放提示音。", self.agent_sound_check),
            SettingRow("agent_sound_start", "开始工作提示音", "Agent 进入工作状态时播放。", self.agent_sound_start_widget, stacked=True),
            SettingRow("agent_sound_done", "任务完成提示音", "Agent 完成任务时播放。", self.agent_sound_done_widget, stacked=True),
            SettingRow("agent_sound_error", "发生错误提示音", "Agent 出现错误异常时播放。", self.agent_sound_error_widget, stacked=True),
            SettingRow("agent_sound_volume", "音效音量", "调整 Agent 提示音音量。", self.agent_sound_volume_spin),
            SettingRow("agent_sound_cooldown", "冷却时间", "防止短时间内频繁触发音效；0 表示无时间冷却（仍单次去重）。", self.agent_sound_cooldown_spin),
        ]
        behavior_layout.addWidget(SettingsSection("Agent 联动 · 提示音效", agent_sound_rows, behavior_content))
        behavior_layout.addStretch(1)
        self._add_page("桌宠行为", "play", self._page_shell("桌宠行为", behavior_content))

        appearance_content = QWidget()
        appearance_layout = QVBoxLayout(appearance_content)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(16)
        appearance_layout.addWidget(SettingsSection("桌宠显示", [
            SettingRow("scale", "桌宠大小", "调整桌宠在桌面上的显示尺寸。", self.scale_combo),
            SettingRow("pet_opacity", "不透明度", "调整桌宠窗口的整体透明度；100% 为完全不透明。", self.pet_opacity_spin),
            SettingRow(
                "self_talk_bubble_style", "气泡方案",
                "选择气泡视觉与相对桌宠的位置；贴近屏幕边缘时自动换位。",
                self.bubble_style_select,
            ),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("菜单外观", [
            SettingRow("menu_theme", "颜色主题", "可跟随系统，或固定使用浅色/深色菜单。", self.menu_theme_select),
            SettingRow("menu_density", "菜单密度", "调整新版右键菜单的菜单项高度和分组留白。", self.menu_density_select),
            SettingRow("menu_radius", "圆角大小", "调整新版右键菜单和子菜单的外轮廓圆角。", self.menu_radius_select),
            SettingRow("menu_font", "UI 字体", "设置新版菜单使用的界面字体。", self.menu_font_select),
            SettingRow("menu_font_size", "UI 字号", "同步调整主菜单与多级菜单的字号。", self.menu_font_size_select),
            SettingRow("menu_translucent", "半透明菜单", "使用接近 Modern 的半透明浮层表面。", self.menu_translucent_check),
            SettingRow("menu_opacity", "表面不透明度", "调整菜单背景透出桌面内容的程度。", self.menu_opacity_spin),
        ], appearance_content))
        if self.ai_page is not None:
            appearance_layout.addWidget(SettingsSection("AI 对话外观", self.ai_page.appearance_rows(), appearance_content))
        appearance_layout.addWidget(SettingsSection("浅色主题", [
            SettingRow("light_background", "背景色", "浅色菜单的浮层背景。", self.light_background_picker),
            SettingRow("light_foreground", "文字色", "浅色菜单的主要文字与图标颜色。", self.light_foreground_picker),
            SettingRow("light_hover", "悬停色", "鼠标悬停菜单项时的背景。", self.light_hover_picker),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("深色主题", [
            SettingRow("dark_background", "背景色", "深色菜单的浮层背景。", self.dark_background_picker),
            SettingRow("dark_foreground", "文字色", "深色菜单的主要文字与图标颜色。", self.dark_foreground_picker),
            SettingRow("dark_hover", "悬停色", "鼠标悬停菜单项时的背景。", self.dark_hover_picker),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("彩蛋入口", [
            SettingRow("egg_enabled", "显示彩蛋", "控制新版菜单首行彩蛋入口是否显示。", self.egg_enabled_check),
            SettingRow("egg_title", "入口标题", "显示在圆形头像右侧的文字。", self.egg_title_edit),
            SettingRow("egg_hint", "右侧提示", "显示在鼠标指针图标后的短提示。", self.egg_hint_edit),
            SettingRow("egg_avatar", "头像图片", "使用绝对路径；支持常见图片格式。", self.egg_avatar_picker),
            SettingRow("egg_image_dir", "弹窗图片目录", "使用绝对路径；每次点击会随机选择一张图片。", self.egg_image_dir_picker),
        ], appearance_content))
        appearance_layout.addStretch(1)
        self._add_page("外观", "appearance", self._page_shell("外观", appearance_content))

        launcher_content = QWidget()
        launcher_layout = QVBoxLayout(launcher_content)
        launcher_layout.setContentsMargins(0, 0, 0, 0)
        launcher_layout.setSpacing(18)
        self.quick_launch_editor = QuickLaunchEditor(
            self.config.get("quick_launch_apps", DEFAULT_QUICK_LAUNCH_APPS),
            launcher_content,
        )
        launcher_layout.addWidget(SettingsSection("已配置应用", [
            SettingRow(
                "quick_launch_apps",
                "应用快捷启动",
                "这些应用将按图标和名称显示在新版右键菜单的“快捷启动”子菜单中。",
                self.quick_launch_editor,
                stacked=True,
            ),
        ], launcher_content))
        launcher_layout.addStretch(1)
        self._add_page("快捷启动", "application", self._page_shell("快捷启动", launcher_content))

        if sys.platform == "win32" and self.include_ai:
            self._add_page("主动识屏", "screen", self._page_shell("主动识屏", self._proactive_page_content()))

        if self.ai_page is not None:
            self._add_page("AI 设置", "chat", self._page_shell("AI 设置", self.ai_page))

        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self._search_rows = self.findChildren(SettingRow)
        self._search_matches: list[SettingRow] = []
        self._search_index = -1
        self.search_edit.textChanged.connect(self._search_settings)

        self.self_talk_check.toggled.connect(self._update_self_talk_controls)
        self.menu_translucent_check.toggled.connect(self._update_translucency_controls)
        self._update_self_talk_controls(self.self_talk_check.isChecked())
        self._update_translucency_controls(self.menu_translucent_check.isChecked())
        # 初始同步须在全部 SettingRow 构建完成后执行，否则 findChild 找不到行
        self._update_click_sound_controls(self.click_sound_check.isChecked())
        self._update_agent_sound_controls(self.agent_sound_check.isChecked())
        self._update_agent_sound_subcontrols()

        self.setStyleSheet(self._stylesheet())

    def _build_pet_controls(self) -> None:
        self.scale_combo = ModernSelect(self, width=132)
        current_scale = float(self.config.get("scale", catalog.DEFAULT_SCALE))
        scales = list(catalog.SCALE_STEPS)
        if not any(abs(current_scale - value) < 0.001 for value in scales):
            scales.append(current_scale)
            scales.sort()
        for scale in scales:
            self.scale_combo.addItem(f"{int(round(catalog.CANVAS_W * scale))} px", scale)
        self.scale_combo.setCurrentIndex(self.scale_combo.findData(current_scale))

        self.on_top_check = ToggleSwitch(self)
        self.on_top_check.setChecked(bool(self.config.get("on_top", True)))
        self.no_move_check = ToggleSwitch(self)
        self.no_move_check.setChecked(bool(self.config.get("no_move", False)))
        self.mouse_through_check = ToggleSwitch(self)
        self.mouse_through_check.setChecked(bool(self.config.get("mouse_through", False)))
        self.cursor_hidden_passthrough_check = ToggleSwitch(self)
        self.cursor_hidden_passthrough_check.setChecked(bool(self.config.get("cursor_hidden_passthrough", True)))
        self.drag_physics_check = ToggleSwitch(self)
        self.drag_physics_check.setChecked(bool(self.config.get("drag_physics", False)))

        # 甩出力度四档：gentle (轻柔) / standard (标准) / strong (强力) / crazy (疯狂)
        self.throw_strength_select = ModernSelect(self, width=132)
        self.throw_strength_select.addItem("轻柔", "gentle")
        self.throw_strength_select.addItem("标准", "standard")
        self.throw_strength_select.addItem("强力", "strong")
        self.throw_strength_select.addItem("疯狂", "crazy")
        current_strength = str(self.config.get("throw_strength", "standard") or "standard")
        self.throw_strength_select.setCurrentData(current_strength if current_strength in {"gentle", "standard", "strong", "crazy"} else "standard")

        # 弹弓弹射开关
        self.slingshot_check = ToggleSwitch(self)
        self.slingshot_check.setChecked(bool(self.config.get("slingshot_enabled", True)))

        # 多开碰撞设置
        self.collision_enabled_check = ToggleSwitch(self)
        self.collision_enabled_check.setChecked(bool(self.config.get("collision_enabled", True)))
        self.collision_restitution_spin = BrowserDoubleSpinBox(self)
        self.collision_restitution_spin.setRange(0.0, 1.0)
        self.collision_restitution_spin.setSingleStep(0.05)
        self.collision_restitution_spin.setDecimals(2)
        self.collision_restitution_spin.setValue(float(_float_or_default(self.config.get("collision_restitution", 0.82), 0.82, 0.0, 1.0)))
        self.collision_friction_spin = BrowserDoubleSpinBox(self)
        self.collision_friction_spin.setRange(0.0, 0.30)
        self.collision_friction_spin.setSingleStep(0.01)
        self.collision_friction_spin.setDecimals(2)
        self.collision_friction_spin.setValue(float(_float_or_default(self.config.get("collision_friction", 0.08), 0.08, 0.0, 0.30)))
        self.collision_mass_scale_spin = BrowserDoubleSpinBox(self)
        self.collision_mass_scale_spin.setRange(0.5, 2.0)
        self.collision_mass_scale_spin.setSingleStep(0.1)
        self.collision_mass_scale_spin.setDecimals(2)
        self.collision_mass_scale_spin.setValue(float(_float_or_default(self.config.get("collision_mass_scale", 1.0), 1.0, 0.5, 2.0)))
        self.collision_impulse_cap_spin = BrowserDoubleSpinBox(self)
        self.collision_impulse_cap_spin.setRange(1000.0, 12000.0)
        self.collision_impulse_cap_spin.setSingleStep(500.0)
        self.collision_impulse_cap_spin.setDecimals(0)
        self.collision_impulse_cap_spin.setValue(float(_float_or_default(self.config.get("collision_impulse_cap", 9000.0), 9000.0, 1000.0, 12000.0)))
        self.collision_sound_check = ToggleSwitch(self)
        self.collision_sound_check.setChecked(bool(self.config.get("collision_sound_enabled", True)))
        self.collision_sound_volume_spin = BrowserSpinBox(self)
        self.collision_sound_volume_spin.setRange(0, 100)
        self.collision_sound_volume_spin.setSuffix(" %")
        collision_sound_vol = float(self.config.get("collision_sound_volume", 0.70))
        self.collision_sound_volume_spin.setValue(int(round(collision_sound_vol * 100)))

        self.lock_position_check = ToggleSwitch(self)
        self.lock_position_check.setChecked(bool(self.config.get("lock_position", False)))
        self.shift_drag_check = ToggleSwitch(self)
        self.shift_drag_check.setChecked(bool(self.config.get("shift_drag", False)))
        self.pet_opacity_spin = BrowserSpinBox(self)
        self.pet_opacity_spin.setRange(10, 100)
        self.pet_opacity_spin.setSuffix(" %")
        self.pet_opacity_spin.setValue(int(_float_or_default(self.config.get("pet_opacity", 100), 100, 10, 100)))
        self.autostart_check = ToggleSwitch(self)
        self._autostart_initial = autostart_mod.is_enabled()
        self.autostart_check.setChecked(self._autostart_initial)
        if self.config.instance_id:
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅主桌宠可设置")
        self.dock_icon_check = None
        if sys.platform == "darwin":
            self.dock_icon_check = ToggleSwitch(self)
            self.dock_icon_check.setChecked(bool(self.config.get("show_dock_icon", True)))

        # 点击音效控件群
        self.click_sound_check = ToggleSwitch(self)
        self.click_sound_check.setChecked(bool(self.config.get("click_sound_enabled", True)))
        self.click_sound_picker = ClickSoundPackPicker(
            self.config.get("click_sound_pack"),
            parent=self,
        )
        self.click_sound_volume_spin = BrowserSpinBox(self)
        self.click_sound_volume_spin.setRange(0, 100)
        self.click_sound_volume_spin.setSuffix(" %")
        click_vol = float(self.config.get("click_sound_volume", 0.70))
        self.click_sound_volume_spin.setValue(int(round(click_vol * 100)))

        self.click_sound_preview_btn = QPushButton("试听", self)
        self.click_sound_preview_btn.setIcon(vector_widget_icon(self, "sound", 14))
        self.click_sound_preview_btn.setFixedWidth(72)
        self.click_sound_preview_btn.clicked.connect(self._preview_click_sound)

        self.click_sound_check.toggled.connect(self._update_click_sound_controls)
        # 音效开关即时生效：对话框的批量写回发生在关闭时，但声音开关是即时
        # 听觉反馈——用户关掉后期望立刻静音，而不是等关对话框。
        self.click_sound_check.toggled.connect(self._apply_click_sound_enabled_now)
        self.click_balance_check = None
        if self.include_ai:
            self.click_balance_check = ToggleSwitch(self)
            self.click_balance_check.setChecked(bool(self.config.get("click_show_balance", False)))
        self.click_self_talk_check = ToggleSwitch(self)
        self.click_self_talk_check.setChecked(bool(self.config.get("click_show_self_talk", False)))
        self.music_sing_check = ToggleSwitch(self)
        self.music_sing_check.setChecked(bool(self.config.get("music_sing_enabled", False)))
        self.balance_refresh_spin = None
        self.balance_tier_mode_select = None
        self.balance_tier_peak_edit = None
        self.balance_tier_idle_edit = None
        self.balance_tier_color_check = None
        if self.include_ai:
            self.balance_refresh_spin = BrowserSpinBox(self)
            self.balance_refresh_spin.setRange(0, 1440)
            self.balance_refresh_spin.setSuffix(" 分钟")
            self.balance_refresh_spin.setValue(int(self.config.get("balance_refresh_minutes", 0) or 0))
            self.balance_tier_mode_select = ModernSelect(self, width=180)
            self.balance_tier_mode_select.addItem("空闲 / 高峰（默认）", "default")
            self.balance_tier_mode_select.addItem("梁文谷 / 梁文峰", "liangwen")
            self.balance_tier_mode_select.addItem("自定义", "custom")
            self.balance_tier_mode_select.setCurrentData(
                str(self.config.get("balance_tier_labels_mode", "default") or "default")
            )
            self.balance_tier_peak_edit = QLineEdit(self)
            self.balance_tier_peak_edit.setPlaceholderText("高峰文本，例如：梁文峰")
            self.balance_tier_peak_edit.setText(str(self.config.get("balance_tier_label_peak", "") or ""))
            self.balance_tier_idle_edit = QLineEdit(self)
            self.balance_tier_idle_edit.setPlaceholderText("空闲文本，例如：梁文谷")
            self.balance_tier_idle_edit.setText(str(self.config.get("balance_tier_label_idle", "") or ""))
            self.balance_tier_color_check = ToggleSwitch(self)
            self.balance_tier_color_check.setChecked(bool(self.config.get("balance_tier_color_enabled", True)))
        self.auto_hide_fullscreen_check = None
        self.stream_capture_check = None
        if sys.platform == "win32":
            self.auto_hide_fullscreen_check = ToggleSwitch(self)
            self.auto_hide_fullscreen_check.setChecked(bool(self.config.get("auto_hide_fullscreen", True)))
            self.stream_capture_check = ToggleSwitch(self)
            self.stream_capture_check.setChecked(bool(self.config.get("stream_capture_mode", False)))

        self.speed_select = ModernSelect(self, width=112)
        current_speed = float(self.config.get("playback_speed", 1.0))
        speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        if not any(abs(current_speed - value) < 0.001 for value in speeds):
            speeds.append(current_speed)
            speeds.sort()
        for speed in speeds:
            self.speed_select.addItem(f"{speed:g}x", speed)
        self.speed_select.setCurrentData(current_speed)
        self.gap_spin = BrowserDoubleSpinBox(self)
        self.gap_spin.setRange(0.0, 3600.0)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSuffix(" 秒")
        self.gap_spin.setValue(float(self.config.get("animation_gap_seconds", 0.0)))

        self.self_talk_check = ToggleSwitch(self)
        self.self_talk_check.setChecked(bool(self.config.get("self_talk_enabled", False)))
        # 闲置降帧（灰度默认关）：长时间无交互且窗口可见时动画隔帧呈现
        self.idle_low_fps_check = ToggleSwitch(self)
        self.idle_low_fps_check.setChecked(bool(self.config.get("idle_low_fps_enabled", False)))
        self.self_talk_duration_spin = BrowserDoubleSpinBox(self)
        self.self_talk_duration_spin.setRange(1.0, 300.0)
        self.self_talk_duration_spin.setSingleStep(0.5)
        self.self_talk_duration_spin.setDecimals(1)
        self.self_talk_duration_spin.setSuffix(" 秒")
        self.self_talk_duration_spin.setValue(float(self.config.get(
            "self_talk_duration_seconds", DEFAULT_SELF_TALK_DURATION_SECONDS
        )))
        self.bubble_style_select = ModernSelect(self, width=172)
        for value, preset in BUBBLE_STYLE_PRESETS.items():
            self.bubble_style_select.addItem(str(preset["label"]), value)
        self.bubble_style_select.setCurrentData(
            str(self.config.get("self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self.min_spin = BrowserDoubleSpinBox(self)
        self.max_spin = BrowserDoubleSpinBox(self)
        for spin, value in (
            (self.min_spin, self.config.get("self_talk_min_interval", DEFAULT_SELF_TALK_MIN_INTERVAL)),
            (self.max_spin, self.config.get("self_talk_max_interval", DEFAULT_SELF_TALK_MAX_INTERVAL)),
        ):
            spin.setRange(5.0, 3600.0)
            spin.setDecimals(0)
            spin.setSuffix(" 秒")
            spin.setValue(float(value))
        self.texts_edit = QPlainTextEdit(self)
        self.texts_edit.setMinimumSize(240, 82)
        self.texts_edit.setMaximumHeight(170)
        texts = self.config.get("self_talk_texts", DEFAULT_SELF_TALK_TEXTS)
        self.texts_edit.setPlainText("\n".join(str(item) for item in texts))
        self.self_talk_image_dir_picker = ResourcePathPicker(
            str(self.config.get("self_talk_image_dir", "") or ""),
            directory=True,
            parent=self,
        )
        self.self_talk_image_scale_spin = BrowserSpinBox(self)
        self.self_talk_image_scale_spin.setRange(50, 300)
        self.self_talk_image_scale_spin.setSuffix(" %")
        self.self_talk_image_scale_spin.setValue(int(self.config.get("self_talk_image_scale", 100)))
        self.click_talk_bindings_btn = QPushButton("编辑…", self)
        self.click_talk_bindings_btn.setObjectName("clickTalkBindingsButton")
        self.click_talk_bindings_btn.clicked.connect(self._open_click_talk_bindings)

        # Agent 联动：每个 Agent 的自定义 thinking 气泡文案
        agent_link_cfg = self.config.get("agent_link", {})
        thinking_texts = agent_link_cfg.get("thinking_texts") or {}
        # 兼容旧的全局 thinking_text 字段
        legacy_text = str(agent_link_cfg.get("thinking_text", "") or "")
        self.thinking_text_edits: dict[str, QLineEdit] = {}
        for agent_key, agent_name in AgentLinkManager.AGENT_NAMES.items():
            edit = QLineEdit(self)
            default = AgentLinkManager._THINKING_DEFAULTS.get(agent_key, f"{agent_name} 正在深度烧烤……")
            edit.setPlaceholderText(default)
            text = str(thinking_texts.get(agent_key, "") or "")
            if not text and legacy_text:
                text = legacy_text
            edit.setText(text)
            edit.setClearButtonEnabled(True)
            self.thinking_text_edits[agent_key] = edit

        # Agent 联动：音效控件群
        self.agent_sound_check = ToggleSwitch(self)
        self.agent_sound_check.setChecked(bool(agent_link_cfg.get("sound_enabled", False)))

        # 辅助构建包含“开关+路径选择+试听”的组合控件
        def _build_agent_event_row(evt_key: str, default_builtin: str) -> tuple[QWidget, ToggleSwitch, ResourcePathPicker, QPushButton]:
            container = QWidget(self)
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            toggle = ToggleSwitch(container)
            toggle.setChecked(bool(agent_link_cfg.get(f"sound_{evt_key}_enabled", True)))
            path_val = str(agent_link_cfg.get(f"sound_{evt_key}_path") or default_builtin)
            picker = ResourcePathPicker(path_val, name_filter=AUDIO_NAME_FILTER, parent=container)
            preview_btn = QPushButton("试听", container)
            preview_btn.setIcon(vector_widget_icon(self, "sound", 14))
            preview_btn.setFixedWidth(72)
            preview_btn.clicked.connect(lambda _, k=evt_key: self._preview_agent_sound(k))

            layout.addWidget(toggle)
            layout.addWidget(picker, 1)
            layout.addWidget(preview_btn)
            return container, toggle, picker, preview_btn

        (self.agent_sound_start_widget, self.agent_sound_start_check,
         self.agent_sound_start_picker, self.agent_sound_start_preview) = _build_agent_event_row("start", "builtin:agent-start")

        (self.agent_sound_done_widget, self.agent_sound_done_check,
         self.agent_sound_done_picker, self.agent_sound_done_preview) = _build_agent_event_row("done", "builtin:agent-done")

        (self.agent_sound_error_widget, self.agent_sound_error_check,
         self.agent_sound_error_picker, self.agent_sound_error_preview) = _build_agent_event_row("error", "builtin:agent-error")

        self.agent_sound_volume_spin = BrowserSpinBox(self)
        self.agent_sound_volume_spin.setRange(0, 100)
        self.agent_sound_volume_spin.setSuffix(" %")
        agent_vol = float(agent_link_cfg.get("sound_volume", 0.65))
        self.agent_sound_volume_spin.setValue(int(round(agent_vol * 100)))

        self.agent_sound_cooldown_spin = BrowserDoubleSpinBox(self)
        self.agent_sound_cooldown_spin.setRange(0.0, 30.0)
        self.agent_sound_cooldown_spin.setSingleStep(0.5)
        self.agent_sound_cooldown_spin.setDecimals(1)
        self.agent_sound_cooldown_spin.setSuffix(" 秒")
        self.agent_sound_cooldown_spin.setValue(float(agent_link_cfg.get("sound_cooldown_seconds", 2.0)))

        self.agent_sound_check.toggled.connect(self._update_agent_sound_controls)
        self.agent_sound_check.toggled.connect(self._apply_agent_sound_enabled_now)
        self.agent_sound_start_check.toggled.connect(lambda: self._update_agent_sound_subcontrols())
        self.agent_sound_done_check.toggled.connect(lambda: self._update_agent_sound_subcontrols())
        self.agent_sound_error_check.toggled.connect(lambda: self._update_agent_sound_subcontrols())

        appearance = self.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
        self.menu_theme_select = ModernSelect(self, width=132)
        for label, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            self.menu_theme_select.addItem(label, value)
        self.menu_theme_select.setCurrentData(appearance.get("theme", "system"))
        self.menu_density_select = ModernSelect(self, width=132)
        for label, value in (("紧凑", "compact"), ("标准", "standard"), ("宽松", "spacious")):
            self.menu_density_select.addItem(label, value)
        self.menu_density_select.setCurrentData(appearance.get("density", "standard"))
        self.menu_radius_select = ModernSelect(self, width=112)
        for radius in (8, 12, 16, 18):
            self.menu_radius_select.addItem(f"{radius} px", radius)
        self.menu_radius_select.setCurrentData(int(appearance.get("corner_radius", 12)))
        self.menu_font_select = ModernSelect(self, width=172)
        self.menu_font_select.addItem("系统默认", "system")
        self._menu_fonts_populated = False
        current_font = str(appearance.get("ui_font") or "system")
        if current_font != "system":
            # 保留当前配置值无需枚举字体库，确保用户未展开选择器直接保存时
            # 不会把自定义字体静默重置为 system。
            self.menu_font_select.addItem(current_font, current_font)
        self.menu_font_select.setCurrentData(current_font)
        # Windows 字体较多时首次枚举可阻塞数秒。零延迟定时器仍会在
        # 设置窗口首帧绘制前运行，因此改为仅在用户真正展开字体选择器时加载。
        self.menu_font_select.aboutToShowPopup.connect(self._populate_menu_fonts)
        self.menu_font_size_select = ModernSelect(self, width=112)
        for size in range(10, 19):
            self.menu_font_size_select.addItem(f"{size} px", size)
        self.menu_font_size_select.setCurrentData(int(appearance.get("ui_font_size", 13)))
        self.menu_translucent_check = ToggleSwitch(self)
        self.menu_translucent_check.setChecked(bool(appearance.get("translucent", True)))
        self.menu_opacity_spin = BrowserDoubleSpinBox(self)
        self.menu_opacity_spin.setRange(0.72, 1.0)
        self.menu_opacity_spin.setSingleStep(0.02)
        self.menu_opacity_spin.setDecimals(2)
        self.menu_opacity_spin.setValue(float(appearance.get("opacity", 0.94)))

        def color_picker(key: str) -> ColorPicker:
            return ColorPicker(str(appearance.get(key) or DEFAULT_CONTEXT_MENU_APPEARANCE[key]), self)

        self.light_background_picker = color_picker("light_background")
        self.light_foreground_picker = color_picker("light_foreground")
        self.light_hover_picker = color_picker("light_hover")
        self.dark_background_picker = color_picker("dark_background")
        self.dark_foreground_picker = color_picker("dark_foreground")
        self.dark_hover_picker = color_picker("dark_hover")

        egg = self.config.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG)
        self.egg_enabled_check = ToggleSwitch(self)
        self.egg_enabled_check.setChecked(bool(egg.get("enabled", True)))
        self.egg_title_edit = _line_edit(str(egg.get("title") or "厉害了我的鲸"), width=240)
        self.egg_hint_edit = _line_edit(str(egg.get("hint") or "请点击"), width=160)
        avatar = resolve_fun_asset(egg.get("avatar"), oijingjing_image_path())
        image_dir = resolve_fun_asset(egg.get("image_dir"), oijingjing_image_path().parent)
        self.egg_avatar_picker = ResourcePathPicker(str(avatar.resolve()), parent=self)
        self.egg_image_dir_picker = ResourcePathPicker(str(image_dir.resolve()), directory=True, parent=self)

        # 灵动岛
        island_cfg = self.config.get("dynamic_island", {})
        if not isinstance(island_cfg, dict):
            island_cfg = {}
        self.island_enabled_check = ToggleSwitch(self)
        self.island_enabled_check.setChecked(bool(island_cfg.get("enabled", False)))
        self.island_icon_check = ToggleSwitch(self)
        self.island_icon_check.setChecked(bool(island_cfg.get("show_icon", True)))
        self.island_name_check = ToggleSwitch(self)
        self.island_name_check.setChecked(bool(island_cfg.get("show_name", True)))
        self.island_info_check = ToggleSwitch(self)
        self.island_info_check.setChecked(bool(island_cfg.get("show_info", True)))
        self.island_status_check = ToggleSwitch(self)
        self.island_status_check.setChecked(bool(island_cfg.get("show_status", True)))
        self.island_info_mode_select = ModernSelect(self, width=160)
        for label, value in (
            ("当前时间", "time"),
            ("余额峰谷", "balance_tier"),
            ("余额数值", "balance"),
            ("自定义短文本", "custom"),
        ):
            self.island_info_mode_select.addItem(label, value)
        self.island_info_mode_select.setCurrentData(str(island_cfg.get("info_mode") or "time"))
        self.island_style_select = ModernSelect(self, width=160)
        for label, value in (
            ("黑色", "dark"),
            ("白色", "light"),
            ("玻璃质感", "glass"),
        ):
            self.island_style_select.addItem(label, value)
        self.island_style_select.setCurrentData(str(island_cfg.get("style") or "dark"))
        self.island_icon_select = ModernSelect(self, width=160)
        for emoji in ("🐳", "🐟", "🐙", "🦭", "🐧", "🐱", "🐶", "🌟", "⚡", "❤️"):
            self.island_icon_select.addItem(emoji, emoji)
        self.island_icon_select.setCurrentData(str(island_cfg.get("icon") or "🐳"))
        self.island_custom_text_edit = _line_edit(str(island_cfg.get("custom_text") or ""), width=220)

    # ------------------------------------------------------------ 主动识屏
        if sys.platform == "win32" and self.include_ai:
            self._build_proactive_controls()
    def _build_proactive_controls(self) -> None:
        """主动识屏页控件（仅 Windows + 有聊天能力时挂载）。"""
        from .proactive import effective_proactive_config

        pro = effective_proactive_config(self.config.get("proactive_screen", {}))

        self.pro_enabled_check = ToggleSwitch(self)
        self.pro_enabled_check.setChecked(bool(pro["enabled"]))
        self.pro_dryrun_check = ToggleSwitch(self)
        self.pro_dryrun_check.setChecked(bool(pro["dry_run"]))

        self.pro_preset_select = ModernSelect(self, width=160)
        for key, label in (
            ("balanced", "平衡（推荐）"),
            ("quiet", "安静"),
            ("active", "活跃"),
            ("custom", "自定义参数"),
        ):
            self.pro_preset_select.addItem(label, key)
        idx = {"quiet": 1, "balanced": 0, "active": 2, "custom": 3}.get(pro["preset"], 0)
        self.pro_preset_select.setCurrentIndex(idx)
        self.pro_preset_select.currentIndexChanged.connect(self._on_pro_preset_changed)

        self.pro_dwell_spin = BrowserSpinBox(self)
        self.pro_dwell_spin.setRange(15, 600)
        self.pro_dwell_spin.setValue(int(pro["dwell_seconds"]))

        self.pro_cooldown_spin = BrowserDoubleSpinBox(self)
        self.pro_cooldown_spin.setRange(0.5, 7200)
        self.pro_cooldown_spin.setDecimals(2)
        self.pro_cooldown_unit = ModernSelect(self, width=80)
        self.pro_cooldown_unit.addItem("分钟", "min")
        self.pro_cooldown_unit.addItem("秒", "sec")
        self._pro_set_cooldown_display(float(pro["cooldown_minutes"]))
        self.pro_cooldown_unit.currentIndexChanged.connect(self._on_pro_cooldown_unit_changed)

        self.pro_min_interval_spin = BrowserSpinBox(self)
        self.pro_min_interval_spin.setRange(30, 3600)
        self.pro_min_interval_spin.setValue(int(pro["min_request_interval_seconds"]))

        self.pro_cap_spin = BrowserSpinBox(self)
        self.pro_cap_spin.setRange(1, 9999)
        self.pro_cap_spin.setValue(int(pro["daily_cap"]))

        self.pro_idle_check = ToggleSwitch(self)
        self.pro_idle_check.setChecked(bool(pro["require_idle"]))
        self.pro_idle_spin = BrowserSpinBox(self)
        self.pro_idle_spin.setRange(5, 3600)
        raw_idle = (self.config.get("proactive_screen", {}) or {}).get("min_idle_seconds", 30)
        self.pro_idle_spin.setValue(int(raw_idle or 30))

        self.pro_through_check = ToggleSwitch(self)
        self.pro_through_check.setChecked(bool(pro["allow_when_mouse_through"]))
        self.pro_precue_check = ToggleSwitch(self)
        self.pro_precue_check.setChecked(bool(pro["pre_cue"]))
        self.pro_free_check = ToggleSwitch(self)
        self.pro_free_check.setChecked(bool(pro["prefer_free_provider"]))

        self.pro_whitelist_edit = QPlainTextEdit(self)
        self.pro_whitelist_edit.setPlaceholderText("msedge.exe\ntitle:*会议*")
        self.pro_whitelist_edit.setPlainText("\n".join(str(x) for x in pro["whitelist"]))
        self.pro_whitelist_edit.setMinimumHeight(72)

        self.pro_add_btn = QPushButton("从当前前台窗口添加…", self)
        self.pro_add_btn.setProperty("variant", "ghost")
        self.pro_add_btn.clicked.connect(self._on_pro_add_foreground)
        self._pro_add_timer = QTimer(self)
        self._pro_add_timer.setSingleShot(True)
        self._pro_add_timer.timeout.connect(self._do_pro_add_foreground)

        self.pro_clear_mem_btn = QPushButton("清除陪伴记忆", self)
        self.pro_clear_mem_btn.setProperty("variant", "ghost")
        self.pro_clear_mem_btn.clicked.connect(self._on_pro_clear_memory)

    def _pro_set_cooldown_display(self, minutes: float) -> None:
        unit = "sec" if minutes < 1 else "min"
        self._pro_apply_cooldown_unit(unit, minutes)

    def _pro_apply_cooldown_unit(self, unit: str, minutes: float) -> None:
        self.pro_cooldown_unit.blockSignals(True)
        self.pro_cooldown_unit.setCurrentIndex(1 if unit == "sec" else 0)
        if unit == "sec":
            self.pro_cooldown_spin.setRange(30, 7200)
            self.pro_cooldown_spin.setDecimals(0)
            self.pro_cooldown_spin.setValue(min(7200, max(30, round(minutes * 60))))
        else:
            self.pro_cooldown_spin.setRange(0.5, 120)
            self.pro_cooldown_spin.setDecimals(2)
            self.pro_cooldown_spin.setValue(min(120.0, max(0.5, minutes)))
        self._pro_cooldown_last_unit = unit
        self.pro_cooldown_unit.blockSignals(False)

    def _on_pro_cooldown_unit_changed(self) -> None:
        old = getattr(self, "_pro_cooldown_last_unit", "min")
        v = float(self.pro_cooldown_spin.value())
        minutes = v / 60.0 if old == "sec" else v
        self._pro_apply_cooldown_unit(self.pro_cooldown_unit.currentData(), minutes)

    def _pro_cooldown_minutes(self) -> float:
        v = float(self.pro_cooldown_spin.value())
        return v / 60.0 if self.pro_cooldown_unit.currentData() == "sec" else v

    def _on_pro_preset_changed(self, _index: int) -> None:
        from .proactive import PRESET_DEFAULTS
        vals = PRESET_DEFAULTS.get(self.pro_preset_select.currentData())
        if vals:
            self.pro_dwell_spin.setValue(vals["dwell_seconds"])
            self._pro_set_cooldown_display(float(vals["cooldown_minutes"]))
            self.pro_cap_spin.setValue(vals["daily_cap"])

    def _on_pro_add_foreground(self) -> None:
        self.pro_add_btn.setEnabled(False)
        self.pro_add_btn.setText("请在 3 秒内切换到目标窗口…")
        self._pro_add_timer.start(3000)

    def _do_pro_add_foreground(self) -> None:
        self.pro_add_btn.setEnabled(True)
        self.pro_add_btn.setText("从当前前台窗口添加…")
        from . import vision
        info = vision.foreground_window_info()
        if not info:
            QMessageBox.information(self, "添加前台窗口", "未能检测到有效的前台窗口，请将目标软件置顶后再试。")
            return
        proc = str(info.get("process", "")).strip()
        title = str(info.get("title", "")).strip()
        box = QMessageBox(self)
        box.setWindowTitle("添加到白名单")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"检测到前台窗口：\n进程：{proc or '（未知）'}\n标题：{title or '（空）'}\n\n要按哪种方式关注它？"
        )
        btn_proc = box.addButton("按软件（推荐）", QMessageBox.ButtonRole.AcceptRole)
        btn_title = box.addButton("按标题关键词", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        lines = [x.strip() for x in self.pro_whitelist_edit.toPlainText().splitlines() if x.strip()]
        if box.clickedButton() is btn_proc and proc and proc not in lines:
            lines.append(proc)
        elif box.clickedButton() is btn_title and title:
            rule = f"title:*{title}*"
            if rule not in lines:
                lines.append(rule)
        else:
            return
        self.pro_whitelist_edit.setPlainText("\n".join(lines))

    def _on_pro_clear_memory(self) -> None:
        from .proactive import ProactiveMemory
        ProactiveMemory(self.config.dir / "proactive_screen_memory.json").clear()
        QMessageBox.information(self, "陪伴记忆", "已清空主动识屏的短期陪伴记忆。")

    def _proactive_page_content(self) -> QWidget:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(SettingsSection("总开关与节奏", [
            SettingRow("proactive_enabled", "开启主动识屏",
                       "她会偶尔看一眼你在用的软件并说句话。截图只在内存处理、不落盘、不写入会话。",
                       self.pro_enabled_check),
            SettingRow("proactive_dry_run", "dry-run 验证模式",
                       "开启后满足条件只写日志、不调用模型、不消耗额度。", self.pro_dryrun_check),
            SettingRow("proactive_preset", "陪伴节奏预设",
                       "平衡 45s/5min/15次；安静 90s/10min/8次；活跃 20s/3min/25次（停留/冷却/每日上限）。",
                       self.pro_preset_select),
        ], content))
        layout.addWidget(SettingsSection("频率参数（自定义预设时生效）", [
            SettingRow("proactive_dwell", "窗口停留门限（秒）", "同一前台窗口持续停留该时长才可能触发。",
                       self.pro_dwell_spin),
            SettingRow("proactive_cooldown", "关怀冷却间隔", "两次关怀的最短间隔，支持秒/分钟。",
                       self._pro_cooldown_row()),
            SettingRow("proactive_min_interval", "最小请求间隔（秒）", "免费模型的硬保护，不建议调太小。",
                       self.pro_min_interval_spin),
            SettingRow("proactive_daily_cap", "每日请求上限", "DeepSeek 视觉单次约 ¥0.003；上限 9999 约等于不限。",
                       self.pro_cap_spin),
        ], content))
        layout.addWidget(SettingsSection("触发条件", [
            SettingRow("proactive_require_idle", "仅当我闲置时触发", "勾选后，敲键盘/动鼠标时不打扰。",
                       self.pro_idle_check),
            SettingRow("proactive_idle_seconds", "闲置判定秒数", "勾选上方后，闲置该秒数才触发。",
                       self.pro_idle_spin),
            SettingRow("proactive_through", "鼠标穿透时仍识屏", "桌宠处于鼠标穿透状态时是否继续工作。",
                       self.pro_through_check),
            SettingRow("proactive_pre_cue", "触发前先兆提示", "触发前先冒一句「让我看看……」。",
                       self.pro_precue_check),
            SettingRow("proactive_free", "识屏优先用独立视觉配置", "开：服务商配了独立视觉端点（如免费的智谱 GLM-4.6V-Flash）时识屏走它；关：始终跟随聊天模型。",
                       self.pro_free_check),
        ], content))
        layout.addWidget(SettingsSection("白名单", [
            SettingRow("proactive_whitelist",
                       "白名单（每行一条）",
                       "进程名（如 msedge.exe）= 关注这个软件；title:关键词 = 只关注标题含该词的窗口。留空 = 不识屏。",
                       self.pro_whitelist_edit, stacked=True),
            SettingRow("proactive_whitelist_add", "快捷添加",
                       "点击后 3 秒内切换到目标窗口，自动采样进程名/标题。", self.pro_add_btn),
            SettingRow("proactive_memory_clear", "陪伴记忆",
                       "只存进程名和活动分类（不落标题、不存截图），可随时清空。",
                       self.pro_clear_mem_btn),
        ], content))
        layout.addStretch(1)
        return content

    def _pro_cooldown_row(self) -> QWidget:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(self.pro_cooldown_spin)
        h.addWidget(self.pro_cooldown_unit)
        return row

    def _update_self_talk_controls(self, enabled: bool) -> None:
        keys = (
            "self_talk_duration", "self_talk_min", "self_talk_max",
            "self_talk_texts", "self_talk_images", "click_self_talk",
            "click_talk_bindings",
        )
        controls = (
            self.self_talk_duration_spin, self.min_spin, self.max_spin,
            self.texts_edit, self.self_talk_image_dir_picker,
            self.click_self_talk_check, self.click_talk_bindings_btn,
        )
        for key, control in zip(keys, controls):
            control.setEnabled(bool(enabled))
            row = self.findChild(SettingRow, f"settingRow_{key}")
            if row is not None:
                row.setEnabled(bool(enabled))

    def _populate_menu_fonts(self) -> None:
        if shiboken6.isValid(self) is False or self._menu_fonts_populated:
            return
        self._menu_fonts_populated = True
        appearance = self.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
        for family in _system_font_families():
            if self.menu_font_select.findData(family) < 0:
                self.menu_font_select.addItem(family, family)
        current_font = str(appearance.get("ui_font") or "system")
        if self.menu_font_select.findData(current_font) < 0:
            self.menu_font_select.addItem(current_font, current_font)
        self.menu_font_select.setCurrentData(current_font)

    def _open_click_talk_bindings(self) -> None:
        from .click_talk_dialog import ClickTalkBindingsDialog

        click_names = None
        parent = self.parent()
        if parent is not None and hasattr(parent, "clicks") and parent.clicks:
            click_names = list(parent.clicks)
        dialog = ClickTalkBindingsDialog(self.config, click_names=click_names, parent=self)
        dialog.exec()

    def _preview_click_sound(self) -> None:
        """试听当前选择的点击音效配置（不保存配置）。"""
        from .click_sound import choose_sound, play_sound, resolve_click_sound_candidates

        pack = self.click_sound_picker.value()
        candidates = resolve_click_sound_candidates(pack, self.config.dir)
        sound_file = choose_sound(candidates)
        if sound_file:
            vol = float(self.click_sound_volume_spin.value()) / 100.0
            play_sound(sound_file, volume=vol)

    def _preview_agent_sound(self, event_name: str) -> None:
        """试听当前填写的 Agent 音效（不保存配置、不触发 Agent 业务逻辑）。"""
        from .click_sound import play_sound, resolve_builtin_sound

        picker = {
            "start": self.agent_sound_start_picker,
            "done": self.agent_sound_done_picker,
            "error": self.agent_sound_error_picker,
        }.get(event_name)
        if picker is None:
            return
        path_str = picker.text().strip()
        if not path_str:
            path_str = f"builtin:agent-{event_name}"

        target = None
        if path_str.startswith("builtin:"):
            target = resolve_builtin_sound(path_str)
        else:
            p = Path(path_str).expanduser()
            if p.is_file():
                target = p

        if target:
            vol = float(self.agent_sound_volume_spin.value()) / 100.0
            play_sound(target, volume=vol)

    def _update_click_sound_controls(self, enabled: bool) -> None:
        for row_key in ("click_sound_pack", "click_sound_volume", "click_sound_preview"):
            row = self.findChild(SettingRow, f"settingRow_{row_key}")
            if row is not None:
                row.setVisible(bool(enabled))
                card = row.parentWidget()
                if isinstance(card, SettingsCard):
                    card.refresh_separators()

    def _update_agent_sound_controls(self, enabled: bool) -> None:
        """Agent 音效总开关联动控制整组子项可见性/可用性。"""
        for row_key in ("agent_sound_start", "agent_sound_done", "agent_sound_error", "agent_sound_volume", "agent_sound_cooldown"):
            row = self.findChild(SettingRow, f"settingRow_{row_key}")
            if row is not None:
                row.setVisible(bool(enabled))
                card = row.parentWidget()
                if isinstance(card, SettingsCard):
                    card.refresh_separators()
        if enabled:
            self._update_agent_sound_subcontrols()

    def _update_agent_sound_subcontrols(self) -> None:
        """根据单事件独立开关启用/禁用路径选择器和试听按钮。"""
        self.agent_sound_start_picker.setEnabled(self.agent_sound_start_check.isChecked())
        self.agent_sound_start_preview.setEnabled(self.agent_sound_start_check.isChecked())

        self.agent_sound_done_picker.setEnabled(self.agent_sound_done_check.isChecked())
        self.agent_sound_done_preview.setEnabled(self.agent_sound_done_check.isChecked())

        self.agent_sound_error_picker.setEnabled(self.agent_sound_error_check.isChecked())
        self.agent_sound_error_preview.setEnabled(self.agent_sound_error_check.isChecked())

    def _update_translucency_controls(self, enabled: bool) -> None:
        self.menu_opacity_spin.setEnabled(bool(enabled))
        row = self.findChild(SettingRow, "settingRow_menu_opacity")
        if row is not None:
            row.setEnabled(bool(enabled))

    def _apply_agent_sound_enabled_now(self, checked: bool) -> None:
        """音效总开关即时生效，不等对话框关闭（合并写回，不动其他 agent_link 键）。"""
        agent_cfg = dict(self.config.get("agent_link", {}))
        agent_cfg["sound_enabled"] = bool(checked)
        self.config.set("agent_link", agent_cfg)
        self.config.save()

    def _apply_click_sound_enabled_now(self, checked: bool) -> None:
        """点击音效开关即时生效，不等对话框关闭。"""
        self.config.set("click_sound_enabled", bool(checked))
        self.config.save()

    def move_away_from_pet(self) -> None:
        """把窗口定位到不与桌宠相交的位置。

        在 show() 之前调用（_present_dialog 的 before_present），窗口首帧
        即落在最终位置，避免 Windows 上"先显示默认位置再跳走"的两段式。
        """
        if self._positioned_away:
            return
        self._positioned_away = True
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            self._move_away_from(parent.geometry())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().showEvent(event)
        # 兜底：未经 _present_dialog 直接 show 的路径仍要避让桌宠
        self.move_away_from_pet()

    def _move_away_from(self, pet_geo: QRect) -> None:
        """首次显示时把窗口移到不与桌宠相交的位置（右侧优先，再左侧/下方/上方）。"""
        size = self.size()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect()
        for rect in (
            QRect(pet_geo.right() + 12, pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left() - 12 - size.width(), pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.bottom() + 12, size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.top() - 12 - size.height(), size.width(), size.height()),
        ):
            if avail.contains(rect):
                self.move(rect.topLeft())
                return

    def _page_shell(self, title: str, content: QWidget) -> QWidget:
        page = QWidget(self.pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 26, 18)
        layout.setSpacing(12)
        heading = QLabel(title, page)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        scroll = QScrollArea(page)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        content.setMaximumWidth(960)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def _add_page(self, label: str, icon_name: str, page: QWidget) -> None:
        item = QListWidgetItem(vector_widget_icon(self, icon_name, 16), label)
        item.setSizeHint(QSize(0, 34))
        self.sidebar.addItem(item)
        self.pages.addWidget(page)

    def _clear_search_matches(self) -> None:
        for row in self._search_rows:
            if row.property("searchMatch"):
                row.setProperty("searchMatch", False)
                row.style().unpolish(row)
                row.style().polish(row)

    def _search_settings(self, query: str, *, advance: bool = False) -> None:
        query = query.strip().lower()
        self._clear_search_matches()
        if not query:
            self._search_matches = []
            self._search_index = -1
            self.search_status.hide()
            return
        matches = [
            row for row in self._search_rows
            if query in f"{row.label.text()} {row.hint_label.text()} {row.objectName()}".lower()
        ]
        if not matches:
            self._search_matches = []
            self._search_index = -1
            self.search_status.setText("未找到匹配的设置")
            self.search_status.show()
            return
        if matches != self._search_matches:
            self._search_matches = matches
            self._search_index = 0
        elif advance:
            self._search_index = (self._search_index + 1) % len(matches)
        row = matches[self._search_index]
        row.setProperty("searchMatch", True)
        row.style().unpolish(row)
        row.style().polish(row)
        page_index = 0
        for index in range(self.pages.count()):
            if self.pages.widget(index).isAncestorOf(row):
                page_index = index
                break
        self.sidebar.setCurrentRow(page_index)
        page = self.pages.widget(page_index)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        if scroll is not None:
            scroll.ensureWidgetVisible(row, 0, 24)
        self.search_status.setText(
            f"{self._search_index + 1}/{len(matches)} · {row.label.text()}"
        )
        self.search_status.show()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            watched is self.search_edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self._search_settings(self.search_edit.text(), advance=True)
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _stylesheet() -> str:
        return _settings_stylesheet()



    def _apply_autostart(self) -> None:
        """应用「开机自启」开关：仅在实际改动时写入系统登录项。

        保存按钮与直接关闭（X / Esc）共用，保证三条路径行为一致。
        """
        if self.autostart_check.isChecked() != self._autostart_initial:
            # set_enabled 返回 bool（enable()/disable()）；仅在明确失败时提示。
            ok = autostart_mod.set_enabled(self.autostart_check.isChecked())
            if ok is False:
                QMessageBox.warning(
                    self,
                    "开机自启设置失败",
                    "写入开机自启失败：可能被安全软件拦截。\n"
                    "可稍后在托盘菜单重试，或检查安全软件/系统优化工具的拦截记录。",
                )

    def _save(self) -> None:
        """「保存并退出」：写入配置并关闭对话框。"""
        self._saved_via_button = True
        self._write_config()
        self._apply_autostart()
        self.settings_saved.emit()
        self.accept()

    def _write_config(self) -> bool:
        """把当前控件值写入 config 并落盘（按钮与直接关闭共用）。

        保存前从磁盘重读：吸收外部对本对话框未暴露字段的改动。
        已知限制：已暴露字段仍是 last-writer-wins（对话框获胜）。
        返回是否成功落盘；失败时提示用户。
        """
        self.config.reload()
        minimum = min(self.min_spin.value(), self.max_spin.value())
        maximum = max(self.min_spin.value(), self.max_spin.value())
        texts = [line.strip()[:120] for line in self.texts_edit.toPlainText().splitlines() if line.strip()]
        self.config.set("scale", float(self.scale_combo.currentData()))
        self.config.set("on_top", self.on_top_check.isChecked())
        if self.dock_icon_check is not None:
            self.config.set("show_dock_icon", self.dock_icon_check.isChecked())
        self.config.set("no_move", self.no_move_check.isChecked())
        self.config.set("mouse_through", self.mouse_through_check.isChecked())
        self.config.set("drag_physics", self.drag_physics_check.isChecked())
        self.config.set("throw_strength", str(self.throw_strength_select.currentData() or "standard"))
        self.config.set("slingshot_enabled", self.slingshot_check.isChecked())
        self.config.set("collision_enabled", self.collision_enabled_check.isChecked())
        self.config.set("collision_restitution", self.collision_restitution_spin.value())
        self.config.set("collision_friction", self.collision_friction_spin.value())
        self.config.set("collision_mass_scale", self.collision_mass_scale_spin.value())
        self.config.set("collision_impulse_cap", self.collision_impulse_cap_spin.value())
        self.config.set("collision_sound_enabled", self.collision_sound_check.isChecked())
        self.config.set("collision_sound_volume", float(self.collision_sound_volume_spin.value()) / 100.0)
        self.config.set("lock_position", self.lock_position_check.isChecked())
        self.config.set("shift_drag", self.shift_drag_check.isChecked())
        self.config.set("pet_opacity", int(self.pet_opacity_spin.value()))
        self.config.set("click_sound_enabled", self.click_sound_check.isChecked())
        self.config.set("click_sound_pack", self.click_sound_picker.value())
        self.config.set("click_sound_volume", float(self.click_sound_volume_spin.value()) / 100.0)
        warm_click_sound_effects(
            self.config.get("click_sound_pack"),
            data_dir=self.config.dir,
        )
        existing_island = self.config.get("dynamic_island", {})
        if not isinstance(existing_island, dict):
            existing_island = {}
        self.config.set("dynamic_island", {
            "enabled": self.island_enabled_check.isChecked(),
            "show_icon": self.island_icon_check.isChecked(),
            "show_name": self.island_name_check.isChecked(),
            "show_info": self.island_info_check.isChecked(),
            "info_mode": str(self.island_info_mode_select.currentData() or "time"),
            "custom_text": self.island_custom_text_edit.text().strip(),
            "show_status": self.island_status_check.isChecked(),
            "style": str(self.island_style_select.currentData() or "dark"),
            "icon": str(self.island_icon_select.currentData() or "🐳"),
            "x": existing_island.get("x"),
            "y": existing_island.get("y"),
        })
        if self.click_balance_check is not None:
            self.config.set("click_show_balance", self.click_balance_check.isChecked())
        self.config.set("click_show_self_talk", self.click_self_talk_check.isChecked())
        self.config.set("music_sing_enabled", self.music_sing_check.isChecked())
        if self.balance_refresh_spin is not None:
            self.config.set("balance_refresh_minutes", int(self.balance_refresh_spin.value()))
            self.config.set(
                "balance_tier_labels_mode",
                str(self.balance_tier_mode_select.currentData() or "default"),
            )
            self.config.set("balance_tier_label_peak", self.balance_tier_peak_edit.text().strip())
            self.config.set("balance_tier_label_idle", self.balance_tier_idle_edit.text().strip())
            self.config.set("balance_tier_color_enabled", self.balance_tier_color_check.isChecked())
        if self.auto_hide_fullscreen_check is not None:
            self.config.set("auto_hide_fullscreen", self.auto_hide_fullscreen_check.isChecked())
        if self.cursor_hidden_passthrough_check is not None:
            self.config.set("cursor_hidden_passthrough", self.cursor_hidden_passthrough_check.isChecked())
        if self.stream_capture_check is not None:
            self.config.set("stream_capture_mode", self.stream_capture_check.isChecked())
        self.config.set("playback_speed", float(self.speed_select.currentData()))
        self.config.set("animation_gap_seconds", self.gap_spin.value())
        self.config.set("idle_low_fps_enabled", self.idle_low_fps_check.isChecked())
        self.config.set("self_talk_enabled", self.self_talk_check.isChecked())
        self.config.set("self_talk_bubble_style", self.bubble_style_select.currentData())
        self.config.set("self_talk_min_interval", minimum)
        self.config.set("self_talk_max_interval", maximum)
        self.config.set("self_talk_duration_seconds", self.self_talk_duration_spin.value())
        self.config.set("self_talk_texts", texts or list(DEFAULT_SELF_TALK_TEXTS))
        self.config.set("self_talk_image_dir", self.self_talk_image_dir_picker.text())
        self.config.set("self_talk_image_scale", self.self_talk_image_scale_spin.value())
        # Agent 联动：自定义 thinking 文案与音效（合并写回，不覆盖 agent_link 其他开关）
        agent_cfg = dict(self.config.get("agent_link", {}))
        agent_cfg["thinking_texts"] = {
            key: edit.text().strip()
            for key, edit in self.thinking_text_edits.items()
            if edit.text().strip()
        }
        agent_cfg.pop("thinking_text", None)  # 旧的全局字段已迁移到 thinking_texts

        # Agent 联动音效写回
        agent_cfg["sound_enabled"] = self.agent_sound_check.isChecked()
        agent_cfg["sound_start_enabled"] = self.agent_sound_start_check.isChecked()
        agent_cfg["sound_start_path"] = self.agent_sound_start_picker.text().strip() or "builtin:agent-start"
        agent_cfg["sound_done_enabled"] = self.agent_sound_done_check.isChecked()
        agent_cfg["sound_done_path"] = self.agent_sound_done_picker.text().strip() or "builtin:agent-done"
        agent_cfg["sound_error_enabled"] = self.agent_sound_error_check.isChecked()
        agent_cfg["sound_error_path"] = self.agent_sound_error_picker.text().strip() or "builtin:agent-error"
        agent_cfg["sound_volume"] = float(self.agent_sound_volume_spin.value()) / 100.0
        agent_cfg["sound_cooldown_seconds"] = float(self.agent_sound_cooldown_spin.value())

        self.config.set("agent_link", agent_cfg)
        self.config.set("context_menu_appearance", {
            "theme": self.menu_theme_select.currentData(),
            "density": self.menu_density_select.currentData(),
            "corner_radius": self.menu_radius_select.currentData(),
            "ui_font": self.menu_font_select.currentData(),
            "ui_font_size": self.menu_font_size_select.currentData(),
            "translucent": self.menu_translucent_check.isChecked(),
            "opacity": self.menu_opacity_spin.value(),
            "light_background": self.light_background_picker.text(),
            "light_foreground": self.light_foreground_picker.text(),
            "light_hover": self.light_hover_picker.text(),
            "dark_background": self.dark_background_picker.text(),
            "dark_foreground": self.dark_foreground_picker.text(),
            "dark_hover": self.dark_hover_picker.text(),
        })
        self.config.set("menu_easter_egg", {
            "enabled": self.egg_enabled_check.isChecked(),
            "title": self.egg_title_edit.text(),
            "hint": self.egg_hint_edit.text(),
            # 内置 assets 内的路径归一化回相对值，保持 portable（目录移动/自更新后仍可用）
            "avatar": store_fun_asset(self.egg_avatar_picker.text(), oijingjing_image_path()),
            "image_dir": store_fun_asset(self.egg_image_dir_picker.text(), oijingjing_image_path().parent),
        })
        self.config.set("quick_launch_apps", self.quick_launch_editor.apps())
        if self.ai_page is not None:
            self.ai_page.save()
        if sys.platform == "win32" and self.include_ai and hasattr(self, "pro_enabled_check"):
            from .proactive import PRESET_DEFAULTS
            pro_data = dict(self.config.get("proactive_screen", {}) or {})
            preset = self.pro_preset_select.currentData()
            # 非 custom 预设下改了数值 → 自动落为 custom，否则运行时会被预设覆盖（gemini 审查发现）
            if preset in PRESET_DEFAULTS:
                pv = PRESET_DEFAULTS[preset]
                if (self.pro_dwell_spin.value() != pv["dwell_seconds"]
                        or abs(self._pro_cooldown_minutes() - pv["cooldown_minutes"]) > 1e-6
                        or self.pro_cap_spin.value() != pv["daily_cap"]):
                    preset = "custom"
            pro_data.update({
                "enabled": self.pro_enabled_check.isChecked(),
                "dry_run": self.pro_dryrun_check.isChecked(),
                "preset": preset,
                "dwell_seconds": self.pro_dwell_spin.value(),
                "cooldown_minutes": self._pro_cooldown_minutes(),
                "min_request_interval_seconds": self.pro_min_interval_spin.value(),
                "daily_cap": self.pro_cap_spin.value(),
                "require_idle": self.pro_idle_check.isChecked(),
                "min_idle_seconds": self.pro_idle_spin.value(),
                "allow_when_mouse_through": self.pro_through_check.isChecked(),
                "pre_cue": self.pro_precue_check.isChecked(),
                "prefer_free_provider": self.pro_free_check.isChecked(),
                "whitelist": [x.strip() for x in self.pro_whitelist_edit.toPlainText().splitlines() if x.strip()],
            })
            self.config.set("proactive_screen", pro_data)
        self.config.set("autostart_wanted", self.autostart_check.isChecked())
        ok = self.config.save()
        if not ok:
            QMessageBox.warning(
                self,
                "保存失败",
                "配置未能写入磁盘，改动可能在重启后丢失。\n\n配置路径："
                + str(self.config.path),
            )
        return ok

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """直接关闭（X / Esc）时同样落盘，避免修改丢失。

        设置项都是即时型偏好，与右键菜单/托盘修改的写入时机保持一致；
        已走「保存并退出」则跳过（防重复写入）。
        """
        if not getattr(self, "_saved_via_button", False):
            try:
                self._write_config()
                self._apply_autostart()
            except Exception:
                logging.exception("关闭设置时保存配置失败")
        super().closeEvent(event)


def _load_qss(name: str) -> str:
    """读取随包分发的 QSS 资源（与 pet/chat/*.qss 同约定）。"""
    return (Path(__file__).with_name(name)).read_text(encoding="utf-8")


# 深色系统的覆盖段：追加在浅色 QSS 之后（后写规则优先）
_LIGHT_SETTINGS_STYLESHEET = _load_qss("settings_styles.qss")
_DARK_OVERRIDE = _load_qss("settings_styles_dark.qss")
_DARK_BROWSER_OVERRIDE = _load_qss("settings_styles_dark_browser.qss")


def _settings_stylesheet() -> str:
    """浅色基础 QSS + 显式控件文字色补丁；深色系统时追加深色覆盖段。"""
    light_patch = """
        QPushButton { color: #202020; }
        QToolButton { color: #202020; }
        QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #202020; }
    """
    base = _LIGHT_SETTINGS_STYLESHEET + light_patch
    if not _system_dark():
        return base + BROWSER_CONTROL_STYLESHEET
    return base + _DARK_OVERRIDE + BROWSER_CONTROL_STYLESHEET + _DARK_BROWSER_OVERRIDE
