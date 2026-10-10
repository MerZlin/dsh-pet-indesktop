"""Independent vision editor. Only explicit Save/Confirm touches the secure vault."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from pet.credentials import CredentialError
from pet.settings_widgets import BrowserSpinBox, ModernSelect, SettingRow, SettingsSection, ToggleSwitch

from ..common.models import VisionProfile, VisionSettings
from .config import VisionConfigService


class ScreenSettingsPage(QWidget):
    saved = Signal()

    def __init__(self, service: VisionConfigService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("screenUnderstandingSettings")
        self.mode = ModernSelect(self, width=180)
        self.mode.addItem("手动看看屏幕", "manual")
        self.mode.addItem("自动识屏", "automatic")
        self.profiles = ModernSelect(self, width=180)
        self.profile_id = QLineEdit(self)
        self.url = QLineEdit(self)
        self.model = QLineEdit(self)
        self.path = QLineEdit("/v1/chat/completions", self)
        self.prompt = QPlainTextEdit(self)
        self.prompt.setMaximumHeight(120)
        self.key_edit = QLineEdit(self)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("首次配置请填写；留空保留当前配置的已有凭据")
        self.timeout = BrowserSpinBox(self)
        self.timeout.setRange(1, 600)
        self.tokens = BrowserSpinBox(self)
        self.tokens.setRange(1, 32768)
        self.temperature = QLineEdit("0.7", self)
        self.tls = ToggleSwitch(self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.save_button = QPushButton("保存独立视觉配置", self)
        if self.service.api is None:
            rows = [
                SettingRow("screen_status", "屏幕理解", "使用独立视觉配置；首次使用会准备默认参数，请在此处填写并保存凭据。", self.status, stacked=True),
                SettingRow("screen_mode", "配置用途", "自动与手动可分别配置，未配置的用途不会执行。", self.mode),
                SettingRow("screen_profiles", "绑定已有配置", "选择已有配置后点击保存；也可新建。共用配置的修改会影响两个用途。", self.profiles),
                SettingRow("screen_profile_id", "配置标识", "英文字母、数字、下划线或短横线；修改标识会创建新配置。", self.profile_id),
                SettingRow("screen_url", "视觉服务地址", "独立的 HTTP(S) 地址，不从聊天设置读取。", self.url, stacked=True),
                SettingRow("screen_model", "视觉模型", "填写服务提供的准确多模态模型标识。", self.model),
                SettingRow("screen_path", "请求路径", "OpenAI 兼容请求路径。", self.path),
                SettingRow("screen_key", "视觉 API Key", "只存入系统安全存储；失败则保持待配置，不保存明文。", self.key_edit, stacked=True),
                SettingRow("screen_prompt", "视觉提示词", "仅作用于屏幕理解，与聊天提示词独立。", self.prompt, stacked=True),
                SettingRow("screen_timeout", "超时（秒）", "保留视觉执行器已有超时下限和重试策略。", self.timeout),
                SettingRow("screen_temperature", "温度", "0 到 2。", self.temperature),
                SettingRow("screen_tokens", "输出 Token 预算", "保留视觉执行器已有预算下限与上限。", self.tokens),
                SettingRow("screen_tls", "验证 TLS 证书", "建议保持开启。", self.tls),
                SettingRow(
                    "screen_save", "保存与绑定", "显式保存当前用途；关闭设置不会保存这里未提交的草稿。保存不截图、不请求模型。", self.save_button, stacked=True
                ),
            ]
        else:
            # Do not construct retired connection rows: a parentless temporary
            # row would take its editor down when the list is replaced.
            rows = [
                SettingRow("screen_status", "屏幕理解", "识屏只在手动触发或原有自动策略允许时执行。", self.status, stacked=True),
                SettingRow("screen_mode", "请求类型", "手动与自动可分别设置提示词和生成参数，共用 API 设置。", self.mode),
                SettingRow("screen_prompt", "视觉提示词", "仅作用于屏幕理解，与聊天提示词独立。", self.prompt, stacked=True),
                SettingRow("screen_temperature", "温度", "0 到 2。", self.temperature),
                SettingRow("screen_tokens", "输出 Token 预算", "保留视觉执行器已有预算下限与上限。", self.tokens),
                SettingRow("screen_save", "保存识屏参数", "只保存提示词和生成参数；不会截图、请求模型或自动开启识屏。", self.save_button, stacked=True),
            ]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(SettingsSection("屏幕理解", rows, self))
        self.mode.currentIndexChanged.connect(self.reload)
        self.profiles.currentIndexChanged.connect(self._load_profile)
        self.save_button.clicked.connect(self._save)
        if self.service.api is not None:
            for field in (self.profiles, self.profile_id, self.url, self.model, self.path, self.key_edit, self.timeout, self.tls):
                field.hide()
            self.save_button.setText("保存识屏参数")
            link = QPushButton("打开 API 设置", self)
            link.clicked.connect(self.service.api.open_settings)
            layout.insertWidget(
                0, SettingsSection("模型与连接", [SettingRow("screen_api", "API 设置", "识屏优先尝试主 Key；需要时再填写视觉 Key。", link, stacked=True)], self)
            )
        self.reload()

    def reload(self, *_args):
        if self.service.api is not None:
            self._core_reload()
            return
        # No keyring access when opening/searching the page.
        self.profiles.blockSignals(True)
        self.profiles.clear()
        self.profiles.addItem("新建独立配置", "")
        try:
            stored_settings = self.service.settings()
            self._using_default_settings = not stored_settings.profiles
            self._settings = VisionSettings.default() if self._using_default_settings else stored_settings
            self._revision = self.service.revision()
            for profile_id in self._settings.profiles:
                self.profiles.addItem(profile_id, profile_id)
            bound_profile_id = self._settings.bindings.get(self.mode.currentData(), "")
            self.profiles.setCurrentData(bound_profile_id)
            bound_profile = self._settings.profiles.get(bound_profile_id)
            if self._using_default_settings:
                status = "未配置：已准备默认独立视觉配置，请填写 API Key 后保存。"
            elif bound_profile is None:
                status = "已有独立配置；当前用途尚未绑定配置，请选择并保存。"
            elif not bound_profile.credential_ref:
                status = "已有独立配置；当前用途尚未填写视觉 API Key，请在此处填写并保存。"
            else:
                status = "已有独立配置；凭据在执行时校验。"
            self.status.setText(status)
            self.save_button.setEnabled(True)
        except (ValueError, OSError, TypeError):
            self.status.setText("配置损坏：请先恢复配置备份，不会自动覆盖。")
            self.save_button.setEnabled(False)
        finally:
            self.profiles.blockSignals(False)
        self._load_profile()

    def _core_reload(self):
        mode = self.mode.currentData()
        self._revision = self.service.config.revision()
        business = self.service.business(mode)
        self.prompt.setPlainText(business["prompt"])
        self.temperature.setText(str(business["temperature"]))
        self.tokens.setValue(business["max_tokens"])
        self.key_edit.clear()
        purpose = "manual_look" if mode == "manual" else "analyze_frame"
        try:
            meta = self.service.api.metadata(purpose)
            self.profile_id.setText(meta.service_id)
            self.url.setText(meta.base_url)
            self.model.setText(meta.model)
            self.path.setText(meta.chat_path)
            self.timeout.setValue(meta.timeout)
            self.tls.setChecked(meta.verify_ssl)
            self.status.setText("API 已配置；此处只保存当前请求类型的识屏参数。")
        except (PermissionError, ValueError, OSError):
            for field in (self.profile_id, self.url, self.model, self.path):
                field.clear()
            self.status.setText("请先在 API 设置填写并保存主 Key；识屏参数可独立保存。")

    def _load_profile(self, *_args):
        settings = getattr(self, "_settings", None)
        profile = settings.profiles.get(self.profiles.currentData()) if settings else None
        self.profile_id.setText(profile.profile_id if profile else str(self.mode.currentData()))
        self.url.setText(profile.base_url if profile else "")
        self.model.setText(profile.model if profile else "")
        self.path.setText(profile.chat_path if profile else "/v1/chat/completions")
        self.prompt.setPlainText(profile.system_prompt if profile else "")
        self.timeout.setValue(int(profile.timeout) if profile else 60)
        self.tokens.setValue(profile.max_tokens if profile else 2048)
        self.temperature.setText(str(profile.temperature) if profile else "0.7")
        self.tls.setChecked(profile.verify_ssl if profile else True)
        self.key_edit.clear()

    def _error(self, exc):
        # Never show raw backend/URL exceptions: they may contain a secret.
        known = {
            "configuration_changed": "配置已被其他窗口修改，请重新打开设置。",
            "configuration_busy": "配置正在保存，请稍后重试。",
            "recovery_pending": "安全存储不可用，待恢复后重试清理。",
        }
        self.status.setText("保存失败：" + known.get(str(exc), "请检查参数及系统安全存储，当前用途保持原状态或待配置。"))

    def _save(self):
        try:
            if self.service.api is not None:
                self.service.save_business(
                    self.mode.currentData(),
                    self.prompt.toPlainText(),
                    float(self.temperature.text()),
                    int(self.tokens.value()),
                    expected_revision=self._revision,
                )
                self._core_reload()
                self.saved.emit()
                return
            profile = VisionProfile(
                self.profile_id.text().strip(),
                self.url.text().strip(),
                self.model.text().strip(),
                self.path.text().strip(),
                self.prompt.toPlainText(),
                float(self.timeout.value()),
                float(self.temperature.text()),
                int(self.tokens.value()),
                self.tls.isChecked(),
            )
            self.service.save_profile(profile, modes=[self.mode.currentData()], expected_revision=self._revision, secret=self.key_edit.text() or None)
            self.key_edit.clear()
            self.reload()
            self.status.setText("已保存。屏幕理解只使用这里的独立配置；留空凭据时保持待配置。")
            self.saved.emit()
        except (ValueError, OSError, CredentialError, TypeError) as exc:
            self.key_edit.clear()
            self._error(exc)
