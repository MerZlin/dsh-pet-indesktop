"""Independent vision editor. Only explicit Save/Confirm touches the secure vault."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from pet.credentials import CredentialError
from pet.settings_widgets import BrowserSpinBox, ModernSelect, SettingRow, SettingsSection, ToggleSwitch

from ..common.models import VisionProfile
from .config import VisionConfigService
from .migration import VisionMigration


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
        self.key_edit.setPlaceholderText("新配置必填；留空保留当前端点的已有凭据")
        self.timeout = BrowserSpinBox(self)
        self.timeout.setRange(1, 600)
        self.tokens = BrowserSpinBox(self)
        self.tokens.setRange(1, 32768)
        self.temperature = QLineEdit("0.7", self)
        self.tls = ToggleSwitch(self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.save_button = QPushButton("保存独立视觉配置", self)
        self.migrate_button = QPushButton("预览旧配置并确认迁移", self)
        self.recover_button = QPushButton("重试迁移清理", self)
        rows = [
            SettingRow("screen_status", "屏幕理解", "不依赖聊天开关；首次使用需确认迁移或独立配置。", self.status, stacked=True),
            SettingRow(
                "screen_migrate",
                "从原视觉能力迁移",
                "vision_same / vision_model / vision_url / vision_key：迁移后不再跟随聊天变化；不删除聊天凭据。",
                self.migrate_button,
                stacked=True,
            ),
            SettingRow("screen_recover", "中断恢复", "仅清理上次未使用的新增凭据，不覆盖已保存的配置。", self.recover_button),
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(SettingsSection("屏幕理解", rows, self))
        self.mode.currentIndexChanged.connect(self.reload)
        self.profiles.currentIndexChanged.connect(self._load_profile)
        self.save_button.clicked.connect(self._save)
        self.migrate_button.clicked.connect(self._migrate)
        self.recover_button.clicked.connect(self._recover)
        self.reload()

    def reload(self, *_args):
        # No keyring access when opening/searching the page.
        self.profiles.blockSignals(True)
        self.profiles.clear()
        self.profiles.addItem("新建独立配置", "")
        try:
            self._settings = self.service.settings()
            self._revision = self.service.revision()
            for profile_id in self._settings.profiles:
                self.profiles.addItem(profile_id, profile_id)
            self.profiles.setCurrentData(self._settings.bindings.get(self.mode.currentData(), ""))
            self.status.setText("已有独立配置；凭据在执行时校验。" if self._settings.profiles else "待配置：请迁移或新建。")
            self.migrate_button.setEnabled(not bool(self._settings.profiles))
            self.save_button.setEnabled(True)
        except (ValueError, OSError, TypeError):
            self.status.setText("配置损坏：请先恢复配置备份，不会自动覆盖。")
            self.save_button.setEnabled(False)
            self.migrate_button.setEnabled(False)
        finally:
            self.profiles.blockSignals(False)
        self._load_profile()

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
            "source_changed": "原配置已变化，请重新预览。",
            "configuration_changed": "配置已被其他窗口修改，请重新打开设置。",
            "configuration_busy": "配置正在保存，请稍后重试。",
            "recovery_pending": "安全存储不可用，待恢复后重试清理。",
        }
        self.status.setText("保存/迁移失败：" + known.get(str(exc), "请检查参数及系统安全存储，当前用途保持原状态或待配置。"))

    def _save(self):
        try:
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
            self.status.setText("已保存。缺少凭据的用途保持待配置，不会借用聊天 Key。")
            self.saved.emit()
        except (ValueError, OSError, CredentialError, TypeError) as exc:
            self.key_edit.clear()
            self._error(exc)

    def _migrate(self):
        try:
            migration = VisionMigration(self.service)
            preview = migration.preview()
            lines = ["将旧配置复制为独立视觉配置。聊天数据不删除，以后修改聊天不会影响识屏。"]
            for name, profile, state in (("自动", preview.automatic, preview.automatic_credential), ("手动", preview.manual, preview.manual_credential)):
                credential_state = {"available": "可复制", "missing": "未设置（迁移后待配置）", "unavailable": "安全存储不可用"}[state]
                lines.append(
                    f"{name}：{profile.base_url} · {profile.model}\n"
                    f"请求路径：{profile.chat_path}\n"
                    f"超时 {profile.timeout:g}s · 温度 {profile.temperature:g} · 最大输出 {profile.max_tokens}\n"
                    f"TLS 证书校验：{'开启' if profile.verify_ssl else '关闭'} · 凭据：{credential_state}\n"
                    f"提示词：{profile.system_prompt[:2000] or '（空）'}"
                )
            answer = QMessageBox.question(
                self, "确认视觉配置迁移", "\n\n".join(lines), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            migration.confirm(preview)
            self.reload()
            self.status.setText("迁移已确认；未找到凭据的配置需补填 Key。")
            self.saved.emit()
        except (ValueError, OSError, CredentialError, TypeError) as exc:
            self._error(exc)

    def _recover(self):
        try:
            self.service.recover()
            self.reload()
            self.status.setText("恢复检查完成，已有配置保持不变。")
        except (ValueError, OSError, CredentialError) as exc:
            self._error(exc)
