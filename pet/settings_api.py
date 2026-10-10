"""Friendly API profiles; secrets remain in the system store, not UI configuration."""

from __future__ import annotations

import copy
import threading
from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .api_probe import ProbeResult
from .async_exit import application_exit_gate
from .credentials import CredentialError
from .settings_widgets import BrowserSpinBox, ModernSelect, ResponsiveActionRow, SettingRow, SettingsSection
from .simple_api import SimpleApiConfiguration, new_profile

MESSAGES = {
    "api_saved": "已保存，新请求立即生效，无需重启。",
    "configuration_changed": "配置已被其他窗口修改。草稿保留，请重新加载后再保存。",
    "credential_missing": "请填写 API Key。更改服务地址后需要重新输入对应的 Key。",
    "secure_storage_unavailable": "系统安全存储不可用，未保存；请恢复后重试。",
    "api_recovery_pending": "先前保存未完成，请恢复系统安全存储后重试。",
    "probe_text_ok": "文字连接成功，当前输入尚未保存；请点击保存。此测试不验证视觉或余额。",
    "probe_unauthorized": "连接被拒绝，请检查主 API Key 和模型权限。当前输入尚未保存。",
    "probe_http_error": "服务拒绝文字请求，请检查地址、模型和高级请求路径。当前输入尚未保存。",
    "probe_tls_error": "证书验证失败，请检查服务证书。当前输入尚未保存。",
    "probe_timeout": "请求超时，请检查网络、系统代理或服务状态后重试。当前输入尚未保存。",
    "probe_network_error": "网络连接失败，请检查地址、系统代理或 VPN。当前输入尚未保存。",
    "api_model_required": "请填写文字模型后再测试。",
    "api_operation_failed": "操作失败，请检查参数和安全存储；草稿保留。",
    "api_worker_start_failed": "测试或保存任务未能启动，请重试；草稿保留。",
}


class ApiJob(QObject):
    finished = Signal(bool, object)

    def __init__(self, cfg, action):
        app = QApplication.instance()
        super().__init__(app)
        self.done = threading.Event()
        self.gate = application_exit_gate(app)
        snapshot = SimpleNamespace(
            path=cfg.path, data=copy.deepcopy(cfg.data), instance_id=getattr(cfg, "instance_id", ""), runtime_layout=getattr(cfg, "runtime_layout", None)
        )

        def run():
            result = False, "api_operation_failed"
            try:
                result = True, action(snapshot)
            except (ValueError, OSError, CredentialError, PermissionError) as exc:
                reason = str(exc)
                result = False, reason if reason in MESSAGES else "api_operation_failed"
            except Exception:
                pass  # Backend exceptions may embed their secret arguments.
            finally:
                try:
                    self.finished.emit(*result)
                finally:
                    self.done.set()

        self.thread = threading.Thread(target=run, name="core-api-operation", daemon=False)
        self.token = self.gate.register("core.api", lambda: None, self.ready)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.collect)

    def start(self):
        try:
            self.thread.start()
        except Exception:
            self.gate.unregister(self.token)
            self.deleteLater()
            raise
        self.timer.start()

    def ready(self):
        return self.done.is_set() and not self.thread.is_alive()

    @Slot()
    def collect(self):
        if self.ready():
            self.timer.stop()
            self.gate.unregister(self.token)
            self.deleteLater()


class ApiSettingsWidget(QWidget):
    saved = Signal()

    def __init__(self, cfg, parent=None, *, backend=None):
        super().__init__(parent)
        self.config, self.backend = cfg, backend
        self.store = SimpleApiConfiguration(cfg, backend=backend)
        self.api = self.store.api
        self.busy = self.dirty = self._loading = False
        self._after_save = None
        self._current = ""
        self.services_select = ModernSelect(self)
        self.add_button = QPushButton("新增", self)
        self.delete_button = QPushButton("移除", self)
        fields = ("name", "url", "model", "secret", "vision_secret", "vision_url", "vision_model", "path")
        for name in fields:
            edit = QLineEdit(self)
            edit.setMaxLength(16384 if "secret" in name else (2048 if name in {"url", "vision_url", "path"} else 256))
            setattr(self, name + "_edit", edit)
            edit.textChanged.connect(self._mark_dirty)
        self.name_edit.setMaxLength(128)
        for edit in (self.secret_edit, self.vision_secret_edit):
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vision_secret_edit.setAccessibleName("视觉 API Key（可选）")
        self.services_select.setAccessibleName("API 列表")
        self.vision_url_edit.setPlaceholderText("留空使用主 API 地址")
        self.vision_model_edit.setPlaceholderText("留空按主模型推导")
        self.clear_vision_button = QPushButton("清除视觉 Key", self)
        self.timeout = BrowserSpinBox(self)
        self.timeout.setRange(1, 300)
        self.tls = QCheckBox("验证 TLS 证书", self)
        self.timeout.valueChanged.connect(self._mark_dirty)
        self.tls.toggled.connect(self._mark_dirty)
        self.save_button = QPushButton("保存 API 设置", self)
        self.save_button.setAccessibleName("保存 API 设置")
        self.test_button = QPushButton("测试连接", self)
        self.test_button.setAccessibleName("测试连接")
        self.test_result = QLabel("尚未测试", self)
        self.test_result.setObjectName("settingLabel")
        self.test_result.setAccessibleName("API 连接测试结果")
        self.test_result.setTextFormat(Qt.TextFormat.PlainText)
        self.test_result.setWordWrap(True)
        result_font = self.test_result.font()
        result_font.setBold(True)
        self.test_result.setFont(result_font)
        self.test_note = QLabel(self)
        self.test_note.setObjectName("settingHint")
        self.test_note.setTextFormat(Qt.TextFormat.PlainText)
        self.test_note.setWordWrap(True)
        feedback = QWidget(self)
        feedback_layout = QVBoxLayout(feedback)
        feedback_layout.setContentsMargins(0, 0, 0, 0)
        feedback_layout.setSpacing(2)
        feedback_layout.addWidget(self.test_result)
        feedback_layout.addWidget(self.test_note)
        test_row = QWidget(self)
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.setSpacing(12)
        test_layout.addWidget(self.test_button, 0, Qt.AlignmentFlag.AlignTop)
        test_layout.addWidget(feedback, 1)

        self.reload_button = QPushButton("重新加载", self)
        self.status = QLabel("填写主 Key、测试并保存即可。识屏先尝试主 Key；不会自动开启识屏。", self)
        self.status.setObjectName("settingHint")
        self.status.setWordWrap(True)
        rows = [
            SettingRow(
                "api_profile",
                "API 列表",
                "聊天、文件解读和余额使用选中的 API。",
                ResponsiveActionRow(self.services_select, [self.add_button, self.delete_button]),
                stacked=True,
            ),
            SettingRow("api_name", "名称", "用来区分不同 API。", self.name_edit),
            SettingRow("api_url", "API 地址", "修改地址后请重新输入该服务的 Key。", self.url_edit, stacked=True),
            SettingRow("api_model", "模型", "沿用默认模型，或填写服务提供的模型名。", self.model_edit, stacked=True),
            SettingRow("api_key", "API Key", "已配置时无需重复输入；Key 只保存在系统安全存储。", self.secret_edit, stacked=True),
            SettingRow(
                "api_visual_key",
                "视觉 API Key（可选）",
                "不填时识屏先尝试主 Key；另一家视觉服务的地址和模型请在高级设置填写。",
                ResponsiveActionRow(self.vision_secret_edit, [self.clear_vision_button]),
                stacked=True,
            ),
            SettingRow("api_test", "测试连接", "仅测试当前输入的文字连接，可能产生少量费用；不会保存、截图或验证视觉与余额。", test_row, stacked=True),
        ]
        advanced = [
            SettingRow("api_visual_url", "视觉地址（可选）", "只有配置视觉 Key 才使用此地址；不会向其他服务发送主 Key。", self.vision_url_edit, stacked=True),
            SettingRow("api_visual_model", "视觉模型（可选）", "留空沿用原有模型推导。", self.vision_model_edit, stacked=True),
            SettingRow("api_path", "请求路径", "通常无需修改。", self.path_edit, stacked=True),
            SettingRow("api_timeout", "超时（秒）", "用于新的 API 请求。", self.timeout),
            SettingRow("api_tls", "连接安全", "建议保持证书验证开启。", self.tls),
        ]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(SettingsSection("模型与连接", rows, self))
        self.advanced_section = SettingsSection("高级设置", advanced, self, advanced=True)
        layout.addWidget(self.advanced_section)
        layout.addWidget(ResponsiveActionRow(self.save_button, [self.reload_button]))
        layout.addWidget(self.status)
        self.services_select.currentIndexChanged.connect(self._select_service)
        self.add_button.clicked.connect(self._add)
        self.delete_button.clicked.connect(self._delete)
        self.clear_vision_button.clicked.connect(self._clear_vision)
        self.save_button.clicked.connect(self._save)
        self.test_button.clicked.connect(self._test)
        self.reload_button.clicked.connect(self._discard)
        self._refresh()
        from .api_notifications import subscribe_api

        self._unsubscribe = subscribe_api(self.api.path, lambda: {"document": {}}, lambda _: self.api.revision(), lambda *_: self.refresh_committed())
        self.destroyed.connect(self._unsubscribe)

    def _mark_dirty(self, *_):
        if not self._loading:
            self.dirty = True
            self.test_result.setText("输入已更改，请重新测试。")
            self.test_note.clear()

    def refresh_committed(self):
        if not self.dirty and not self.busy:
            self._refresh()

    def _refresh(self):
        try:
            state = self.store.read()
            self.revision = self.store.revision()
        except (ValueError, OSError, TypeError):
            self.status.setText("配置暂时无法读取，请恢复后重新加载；不会覆盖原配置。")
            for control in self._draft_controls():
                control.setEnabled(control is self.reload_button)
            return
        for control in self._draft_controls():
            control.setEnabled(True)
        self.profiles = {p.service.service_id: p for p in state.profiles}
        self.keys, self.vision_keys, self.clear_vision = {}, {}, set()
        self._populate(state.active_id)
        self.dirty = False

    def _populate(self, pid):
        self.test_result.setText("尚未测试")
        self.test_note.clear()
        self.services_select.blockSignals(True)
        self.services_select.clear()
        for key, profile in self.profiles.items():
            self.services_select.addItem(profile.service.name, key)
        self.services_select.setCurrentData(pid)
        self.services_select.blockSignals(False)
        self._load_service(pid)

    def _load_service(self, pid):
        self._loading = True
        self._current = pid
        profile = self.profiles[pid]
        s = profile.service
        for name, value in (
            ("name", s.name),
            ("url", s.base_url),
            ("model", s.model),
            ("path", s.chat_path),
            ("vision_url", profile.vision_url),
            ("vision_model", profile.vision_model),
            ("secret", self.keys.get(pid, "")),
            ("vision_secret", self.vision_keys.get(pid, "")),
        ):
            getattr(self, name + "_edit").setText(value)
        self.secret_edit.setPlaceholderText("已配置，留空保持" if s.credential_ref else "请输入主 API Key")
        visual = profile.vision and profile.vision.credential_ref and pid not in self.clear_vision
        self.vision_secret_edit.setPlaceholderText("已配置，留空保持" if visual else "可选；留空尝试主 Key")
        self.tls.setChecked(s.verify_ssl)
        self.timeout.setValue(s.timeout)
        self._loading = False

    def _capture(self):
        pid = self._current
        if not pid:
            return
        previous = self.profiles[pid]
        service = replace(
            previous.service,
            name=self.name_edit.text().strip(),
            base_url=self.url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            chat_path=self.path_edit.text().strip(),
            timeout=self.timeout.value(),
            verify_ssl=self.tls.isChecked(),
        )
        self.profiles[pid] = replace(
            previous, service=service, vision_url=self.vision_url_edit.text().strip(), vision_model=self.vision_model_edit.text().strip()
        )
        self.keys[pid] = self.secret_edit.text()
        self.vision_keys[pid] = self.vision_secret_edit.text()
        if self.vision_keys[pid]:
            self.clear_vision.discard(pid)

    def _service(self):
        self._capture()
        return self.profiles[self._current].service.validate()

    def _select_service(self, *_):
        self._capture()
        self._load_service(self.services_select.currentData())
        self.dirty = True

    def _add(self):
        self._capture()
        profile = new_profile()
        self.profiles[profile.service.service_id] = profile
        self._populate(profile.service.service_id)
        self.dirty = True

    def _delete(self):
        if len(self.profiles) <= 1:
            self.status.setText("至少保留一项 API；可以直接修改当前项。")
            return
        self.profiles.pop(self._current)
        self._populate(next(iter(self.profiles)))
        self.dirty = True
        self.status.setText("已从列表移除，保存后生效。原有密钥不会自动删除。")

    def _clear_vision(self):
        self.vision_secret_edit.clear()
        self.clear_vision.add(self._current)
        self.vision_secret_edit.setPlaceholderText("保存后清除，恢复尝试主 Key")
        self.dirty = True

    def _draft_controls(self):
        return (
            self.services_select,
            self.add_button,
            self.delete_button,
            self.name_edit,
            self.url_edit,
            self.model_edit,
            self.secret_edit,
            self.vision_secret_edit,
            self.vision_url_edit,
            self.vision_model_edit,
            self.path_edit,
            self.timeout,
            self.tls,
            self.clear_vision_button,
            self.save_button,
            self.test_button,
            self.reload_button,
        )

    def _run(self, action, *, publish):
        if self.busy:
            return
        self.busy = True
        for control in self._draft_controls():
            control.setEnabled(False)
        self.pending_job = ApiJob(self.config, action)
        self._publish_on_finish = publish
        if not publish:
            self.test_result.setText("测试中…")
            self.test_note.setText("正在测试当前输入；不会保存设置。")
        self.pending_job.finished.connect(self._finished)
        try:
            self.pending_job.start()
        except Exception:
            self.pending_job = None
            self._finished(False, "api_worker_start_failed")

    @Slot(bool, object)
    def _finished(self, ok, result):
        reason = result.reason if isinstance(result, ProbeResult) else result
        self.busy = False
        for control in self._draft_controls():
            control.setEnabled(True)
        callback, self._after_save = self._after_save, None
        if ok and self._publish_on_finish:
            self._refresh()
            self.saved.emit()
        self.status.setText(MESSAGES.get(reason, MESSAGES["api_operation_failed"]))
        if not self._publish_on_finish:
            self._show_probe_result(ok, result)
        if ok and self._publish_on_finish and callback:
            callback()

    def _show_probe_result(self, ok, result):
        reason = result.reason if isinstance(result, ProbeResult) else result
        status = result.http_status if isinstance(result, ProbeResult) else None
        codes = {
            "probe_timeout": "TIMEOUT",
            "probe_network_error": "NETWORK_ERROR",
            "probe_tls_error": "TLS_ERROR",
            "credential_missing": "KEY_MISSING",
            "api_model_required": "MODEL_REQUIRED",
            "api_input_invalid": "INPUT_INVALID",
            "secure_storage_unavailable": "SECURE_STORAGE_UNAVAILABLE",
            "api_worker_start_failed": "TASK_START_FAILED",
        }
        code = f"HTTP {status}" if type(status) is int and 100 <= status <= 599 else codes.get(reason, "NO_HTTP_RESPONSE")
        label = "连接成功" if ok and reason == "probe_text_ok" else "连接失败"
        self.test_result.setText(f"{label} · {code}")
        self.test_note.setText(MESSAGES.get(reason, "请检查输入后重试；测试不会保存设置。"))

    def save_then(self, callback):
        if self.busy:
            self.status.setText("正在测试或保存，请等待完成后再关闭。")
        elif self.dirty:
            self._after_save = callback
            self._save()
        else:
            callback()

    def _save(self):
        self._capture()
        profiles, pid, revision = tuple(self.profiles.values()), self._current, self.revision
        keys, vision_keys, cleared = dict(self.keys), dict(self.vision_keys), set(self.clear_vision)

        def action(cfg):
            SimpleApiConfiguration(cfg, backend=self.backend).save(
                profiles, pid, expected_revision=revision, keys=keys, vision_keys=vision_keys, clear_vision=cleared
            )
            return "api_saved"

        self._run(action, publish=True)

    def _test(self):
        try:
            service, secret = self._service(), self.secret_edit.text()
            from .api_probe import probe_text

            self._run(lambda cfg: probe_text(cfg, service, secret, backend=self.backend), publish=False)
        except ValueError:
            self._show_probe_result(False, "api_input_invalid")
            self.status.setText("请检查名称、地址和模型；测试不会保存。")

    def _discard(self):
        if not self.dirty or QMessageBox.question(self, "重新加载 API", "丢弃未保存的输入并重新加载？") == QMessageBox.StandardButton.Yes:
            self._refresh()

    def closeEvent(self, event):
        self._unsubscribe()
        super().closeEvent(event)


def mount_api_settings(dialog, page):
    from .feature_distribution import BUILTIN_AI

    if BUILTIN_AI:
        return page  # Accepted legacy complete builds retain their original settings.
    if page is None:
        page = QWidget(dialog)
        QVBoxLayout(page).setContentsMargins(0, 0, 0, 0)
    dialog.api_settings_widget = ApiSettingsWidget(dialog.config, page)
    page.layout().insertWidget(0, dialog.api_settings_widget)
    return page


_OPEN = {}


def open_api_settings(cfg):
    key = str(cfg.path)
    previous = _OPEN.get(key)
    if previous is not None:
        previous.show()
        previous.raise_()
        previous.activateWindow()
        return
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame

    from .settings_theme_qss import _settings_stylesheet
    from .settings_widgets import _system_dark

    dialog = QDialog()
    dialog.setObjectName("coreApiSettings")
    dialog.setWindowTitle("API 设置 · 模型与连接")
    dialog.setMinimumSize(720, 500)
    dialog.resize(820, 700)
    theme = str(cfg.get("menu_theme", "system") or "system")
    dialog.setProperty("settingsDark", theme == "dark" or (theme == "system" and _system_dark()))
    dialog.setStyleSheet(_settings_stylesheet(theme))
    scroll = QScrollArea(dialog)
    scroll.setObjectName("settingsScroll")
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidgetResizable(True)
    widget = ApiSettingsWidget(cfg, scroll)
    scroll.setWidget(widget)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(30, 24, 28, 20)
    layout.addWidget(scroll)
    _OPEN[key] = dialog
    dialog.finished.connect(lambda *_: _OPEN.pop(key, None))
    dialog.finished.connect(dialog.deleteLater)
    dialog.show()
