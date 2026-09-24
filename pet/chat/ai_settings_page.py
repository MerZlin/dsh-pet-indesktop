# -*- coding: utf-8 -*-
"""Modern settings AI 设置页 _AiSettingsPage。

从 modern_settings_dialog.py 纯机械搬移。现位于 pet/chat/ 下，原 `from .chat.xxx`
相对导入改为 `from .xxx`（级别变化）。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..settings_widgets import (
    ModernSelect,
    _line_edit,
    BrowserSpinBox,
    BrowserDoubleSpinBox,
    ToggleSwitch,
    ResourcePathPicker,
    ResponsiveActionRow,
    SettingsSection,
    SettingRow,
    SettingsCard,
)
from .themes import CHAT_UI_STYLE_LABELS, CHAT_UI_VIEW_ASPECT, theme_names
from .utils import _safe_emit


class _AiSettingsPage(QWidget):
    test_done = Signal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setObjectName("aiSettingsContent")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # No-chat bundles exclude pet.chat and never construct this optional page.
        from .models import ProviderConfig, SecretStore
        from .providers import test_connection

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
        # 系统通知键：构造期快照无条件回写会把打开期间外部（老聊天设置即存入口
        # settings_dialog.py:437）对该键的改动静默回滚，所以只在用户实际拨动过
        # （脏标记）时才写；toggled 连接晚于这里的 setChecked，构造期不会误标。
        self._system_notify_dirty = False
        self.system_notify_check.setChecked(bool(config.get("system_notifications_enabled", True)))
        self.chat_ui_style = ModernSelect(self, width=190)
        # 展示名与顺序（modern 在前 classic 在后）都取自 themes 的单一来源
        for style_id, style_label in CHAT_UI_STYLE_LABELS.items():
            self.chat_ui_style.addItem(style_label, style_id)
        self.chat_ui_style.setCurrentData(str(config.get("chat_ui_style", "modern")))
        self.vision_same = ToggleSwitch()
        self.vision_same.setChecked(bool(provider.vision_same_as_chat))
        self.vision_model = _line_edit(provider.vision_model)
        self.vision_url = _line_edit(provider.vision_base_url)
        self.vision_key = _line_edit(password=True)

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
        self.message_card_opacity.setValue(int(config.get("modern_chat_card_opacity", 84) or 84))
        self.background_fill = ModernSelect(self, width=160)
        self.background_fill.addItem("填充裁剪", "cover")
        self.background_fill.addItem("完整适应", "contain")
        self.background_fill.addItem("拉伸铺满", "stretch")
        # 裁切取景：编辑器结果按背景值记成编辑集（值为 None = 重置删除），save()
        # 时读磁盘最新配置后只合并编辑过的背景——整字典快照回写会把主设置窗打开
        # 期间外部（老聊天设置即存入口）对其他背景的裁切改动静默回滚。本页风格键
        # 的落盘粒度只到"按风格脏标记"（见下），裁切这一路才是按背景值合并。
        self._bg_crop_edits: dict[str, list | None] = {}
        # 背景风格键（两种对话风格各自的图片/不透明度/填充）同理：构造期快照无条件
        # 回写会把打开期间外部对另一风格的即存改动静默回滚，所以按风格脏标记只回写
        # 本窗口实际编辑过的风格；组内三键仍是整组回写，不承诺按键粒度。
        # _loading_background 挡住程序化赋值的误标。
        self._bg_edited_styles: set[str] = set()
        self._loading_background = False
        self.background_crop_btn = QPushButton("裁切取景…", self)
        self.background_crop_btn.clicked.connect(self._crop_background)
        self._populate_background_options(self._background_style)
        self.chat_ui_style.currentIndexChanged.connect(self._on_chat_ui_style_changed)
        self.background_select.currentIndexChanged.connect(self._on_background_edited)
        self.background_picker.edit.textChanged.connect(self._on_background_edited)
        # 路径变化要重新评估裁切按钮可用性：禁用后修好路径必须恢复可点
        self.background_picker.edit.textChanged.connect(self._update_background_visibility)
        self.background_opacity.valueChanged.connect(self._on_background_edited)
        self.background_fill.currentIndexChanged.connect(self._on_background_edited)
        # 填充方式改动要重估裁切行可见性：contain/stretch 下取景框不生效（themes.py 的 fill 语义）
        self.background_fill.currentIndexChanged.connect(self._update_background_visibility)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._run_test)
        self.test_result = QLabel("验证当前 Provider、API 地址和凭据是否可用。")
        self.test_result.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        provider_row = ResponsiveActionRow(
            self.provider_combo,
            [self.add_provider_btn, self.delete_provider_btn],
        )
        root.addWidget(
            SettingsSection(
                "API 列表",
                [
                    SettingRow(
                        "provider_list",
                        "API 列表",
                        "选择要编辑/切换的模型服务；保存后当前选中项会作为 active_provider 生效。",
                        provider_row,
                    ),
                ],
                self,
            )
        )
        root.addWidget(
            SettingsSection(
                "模型与连接",
                [
                    SettingRow("provider_name", "Provider 名称", "用于区分当前使用的模型服务。", self.name),
                    SettingRow("api_url", "API 地址", "OpenAI Chat Completions 兼容服务地址。", self.url),
                    SettingRow("model", "模型", "发送请求时使用的模型标识。", self.model),
                    SettingRow("api_key", "API Key", "凭据优先保存到系统钥匙串。", self.key),
                    SettingRow("system_prompt", "System Prompt", "定义桌宠对话时的身份、语气和行为。", self.prompt, stacked=True),
                    SettingRow("connection_test", "连接测试", self.test_result.text(), self.test_button),
                ],
                self,
            )
        )
        root.addWidget(
            SettingsSection(
                "系统通知",
                [
                    SettingRow(
                        "system_notifications_enabled",
                        "系统通知",
                        "对话完成 / 生成失败 / 需要授权时，即使切走窗口也会在桌面右下角提醒。",
                        self.system_notify_check,
                    ),
                ],
                self,
            )
        )
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
        # 用户拨动系统通知开关才置脏；连接晚于构造期 setChecked，程序化赋值不置脏。
        self.system_notify_check.toggled.connect(self._on_system_notify_edited)
        self._test_row = self.findChild(SettingRow, "settingRow_connection_test")
        if self._test_row is not None:
            self.test_result = self._test_row.hint_label
        root.addWidget(
            SettingsSection(
                "生成参数（高级）",
                [
                    SettingRow("timeout", "请求超时", "等待模型服务响应的最长时间。", self.timeout),
                    SettingRow("temperature", "Temperature", "数值越高，回答越随机。", self.temperature),
                    SettingRow("max_tokens", "最大输出 Token", "限制模型单次回复的最大长度。", self.tokens),
                    SettingRow("skip_ssl", "跳过 SSL 证书验证", "仅用于本地网关或自签名证书。", self.skip_ssl),
                ],
                self,
                advanced=True,
            )
        )
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.add_provider_btn.clicked.connect(self._add_provider)
        self.delete_provider_btn.clicked.connect(self._delete_provider)
        self._update_provider_buttons()
        root.addStretch(1)

    def appearance_rows(self) -> list[SettingRow]:
        """Controls visually owned by the Appearance page, persisted with AI settings."""
        rows = [
            SettingRow(
                "chat_ui_style",
                "对话窗口",
                "肥鱼版 DeepSeek 提供宽屏现代体验；肥鱼牌小手机保留紧凑经典体验。",
                self.chat_ui_style,
            ),
            SettingRow(
                "chat_background",
                "对话背景",
                "肥鱼版 DeepSeek 与肥鱼牌小手机均支持纯色、内置主题或自定义图片。",
                self.background_select,
            ),
            SettingRow("chat_background_file", "自定义背景图片", "支持常见图片格式，使用绝对路径。", self.background_picker),
            SettingRow("chat_background_opacity", "图片不透明度", "调节背景图可见强度；消息卡片会独立保证正文可读。", self.background_opacity),
            SettingRow("chat_background_fill", "填充方式", "选择裁剪铺满、完整显示或拉伸铺满窗口；仅「填充裁剪」支持自定义取景。", self.background_fill),
            SettingRow("chat_bg_crops", "裁切取景", "拖拽移动 + 滚轮缩放选区，决定背景取哪一块；不裁则按主题默认主体取景。", self.background_crop_btn),
            SettingRow(
                "modern_chat_card_opacity",
                "消息卡片不透明度",
                "调节肥鱼版 DeepSeek 消息卡片透出背景的程度。",
                self.message_card_opacity,
            ),
        ]
        self._background_file_row = rows[-5]
        self._background_detail_rows = rows[-4:-1]
        self._background_crop_row = self._background_detail_rows[-1]
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
        self._loading_background = True  # 程序化赋值不得误标脏
        try:
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
        finally:
            self._loading_background = False
        self._update_background_visibility()

    def _on_background_edited(self, *_args) -> None:
        """用户在当前风格上动了背景控件 → 该风格落盘脏标记（程序化赋值除外）。"""
        if not self._loading_background:
            self._bg_edited_styles.add(self._background_style)

    def _on_system_notify_edited(self, _checked: bool = False) -> None:
        """用户拨动系统通知开关 → 该键落盘脏标记（未拨动则保留外部即存值）。"""
        self._system_notify_dirty = True

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

    def _background_style_label(self) -> str:
        """当前对话窗口风格的展示名（裁切行标签、编辑器标题共用）。"""
        return CHAT_UI_STYLE_LABELS.get(self._background_style, self._background_style)

    def _background_fill_mode(self) -> str:
        return str(self.background_fill.currentData() or "cover")

    def _update_background_visibility(self, _index: int = -1) -> None:
        crop_row = getattr(self, "_background_crop_row", None)
        row = getattr(self, "_background_file_row", None)
        if row is not None:
            row.setVisible(self.background_select.currentData() == "custom")
            has_image = bool(self.background_select.currentData())
            for detail_row in getattr(self, "_background_detail_rows", []):
                detail_row.setVisible(has_image)
            # contain/stretch 下取景框被渲染路径整体忽略（见 themes.py 的 fill 语义），
            # 裁切入口一并隐藏，免得用户存下一个不生效的框。
            if crop_row is not None:
                crop_row.setVisible(has_image and self._background_fill_mode() == "cover")
            card = row.parentWidget()
            if isinstance(card, SettingsCard):
                card.refresh_separators()
        card_opacity_row = getattr(self, "_message_card_opacity_row", None)
        if card_opacity_row is not None:
            card_opacity_row.setVisible(self._background_style == "modern")
        if crop_row is not None:
            title = f"裁切取景（{self._background_style_label()}）"
            if crop_row.label.text() != title:
                crop_row.label.setText(title)
                crop_row.control.setAccessibleName(title)
        if getattr(self, "background_crop_btn", None) is not None:
            self.background_crop_btn.setText("裁切取景…")
            self.background_crop_btn.setEnabled(True)

    def _crop_background(self) -> None:
        """打开裁切取景编辑器：结果记入 _bg_crop_edits，save() 时按背景值合并落盘。"""
        from .crop_dialog import CropDialog
        from .themes import get_theme
        from .widgets import resolve_bg_pixmap

        self.config.reload()  # 打开期间外部可能即存改动：初始框必须读磁盘最新
        value = self._current_background_value()
        pix = resolve_bg_pixmap(value) if value else None
        if pix is None:
            self.background_crop_btn.setText("无可裁背景")
            self.background_crop_btn.setEnabled(False)
            return
        _unset = object()
        initial = self._bg_crop_edits.get(value, _unset)
        if initial is _unset:  # 会话编辑集里没有才读配置（上面已 reload 到磁盘最新）
            initial = (self.config.get("chat_bg_crops", {}) or {}).get(value)
        if initial is None and value.startswith("builtin:"):
            theme = get_theme(value[8:])
            initial = tuple(theme["focus"]) if theme else None
        aspect = CHAT_UI_VIEW_ASPECT.get(self._background_style, CHAT_UI_VIEW_ASPECT["classic"])
        dlg = CropDialog(pix, initial, self, self._background_style_label(), aspect)
        accepted = dlg.exec()
        reset, box = dlg.result_box() if accepted else (False, None)
        dlg.deleteLater()  # 延迟析构：对话框持有整张背景 QPixmap，不随使用次数累积
        if accepted:
            self._bg_crop_edits[value] = None if reset else [round(float(v), 4) for v in box]

    # ------------------------------------------------------------ API 列表管理
    @staticmethod
    def _provider_label(p) -> str:
        name = str(p.name or p.provider_id)
        model = str(p.model or "").strip()
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
        result = self._test_connection(provider, timeout=10.0)
        # 对话框可能在测试在飞时被关闭销毁（WA_DeleteOnClose）：直接
        # self.test_done.emit 会对已删 C++ 对象 RuntimeError。
        _safe_emit(self, "test_done", *result)

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
        # 按风格脏标记合并：只回写本窗口编辑过的风格，未触风格保持磁盘最新值
        # （宿主 save 前已 reload），打开期间外部对它们的即存改动不被快照回滚。
        # 粒度是风格而非按键：动过某风格即整组写回该风格的三个键。
        if "classic" in self._bg_edited_styles:
            self.config.set("chat_background", self._background_values["classic"])
            self.config.set("chat_background_opacity", self._background_display["classic"]["opacity"])
            self.config.set("chat_background_fill", self._background_display["classic"]["fill"])
        if "modern" in self._bg_edited_styles:
            self.config.set("modern_chat_background", self._background_values["modern"])
            self.config.set("modern_chat_background_opacity", self._background_display["modern"]["opacity"])
            self.config.set("modern_chat_background_fill", self._background_display["modern"]["fill"])
        if self._bg_crop_edits:
            # save() 跑在宿主 config.reload() 之后，这里读到的是磁盘最新：只覆盖
            # 本窗口编辑/重置过的键，外部对其他背景的即存改动原样保留。
            # 注意：此处绝不能再 reload()——本页 save 之前宿主已写入其它页的键，
            # reload 会把它们全部冲掉。
            crops = dict(self.config.get("chat_bg_crops", {}) or {})
            for key, box in self._bg_crop_edits.items():
                if box is None:
                    crops.pop(key, None)
                else:
                    crops[key] = box
            self.config.set("chat_bg_crops", crops)
            self._bg_crop_edits.clear()  # 落盘后编辑集失效：页面若复用，None 删除标记不得再次删外部新写入
        self.config.set("modern_chat_card_opacity", self.message_card_opacity.value())
        # 系统通知按脏标记合并：未拨动开关保持磁盘最新值（宿主 save 前已 reload），
        # 打开期间外部（老聊天设置即存）对该键的改动不被构造期快照回滚。
        if self._system_notify_dirty:
            self.config.set("system_notifications_enabled", self.system_notify_check.isChecked())
        self.config.set_chat_settings(self.settings)
