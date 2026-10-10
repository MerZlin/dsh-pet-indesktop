"""Core balance preferences; GUI never waits for the OS credential store."""

from __future__ import annotations

import copy
import threading
from types import SimpleNamespace

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .async_exit import application_exit_gate
from .balance_config import DEFAULT_ENDPOINT, OWNER, BalanceConfiguration
from .credentials import CredentialError
from .settings_widgets import SettingRow, SettingsSection


class BalanceSaveJob(QObject):
    finished = Signal(bool, str)

    def __init__(self, config, endpoint, secret, revision, *, backend=None):
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("application_required_for_balance_save")
        super().__init__(app)
        self._gate = application_exit_gate(app)
        self._done = threading.Event()
        # Bind to a detached data snapshot: background commits never mutate the
        # GUI-owned Config or draft. The file transaction remains CAS authority.
        snapshot = SimpleNamespace(
            path=config.path,
            data=copy.deepcopy(config.data),
            instance_id=config.instance_id,
            runtime_layout=SimpleNamespace(data_root_id=config.runtime_layout.data_root_id),
        )

        def run():
            result = False, "balance_save_failed"
            try:
                balance = BalanceConfiguration(snapshot, backend=backend)
                balance.save(endpoint, secret, expected_revision=revision)
                result = True, "balance_saved"
            except ValueError as exc:
                reason = str(exc)
                result = (
                    False,
                    reason if reason in {"configuration_changed", "balance_endpoint_invalid", "balance_secret_invalid"} else "balance_configuration_invalid",
                )
            except CredentialError:
                result = False, "secure_storage_unavailable"
            except Exception:
                # Never reflect backend errors: some backends include arguments.
                result = False, "balance_save_failed"
            finally:
                try:
                    self.finished.emit(*result)
                finally:
                    self._done.set()

        self._thread = threading.Thread(target=run, name="core-balance-save", daemon=False)
        self._token = self._gate.register(OWNER, lambda: None, self.ready)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._collect)

    def start(self):
        try:
            self._thread.start()
        except Exception:
            self._gate.unregister(self._token)
            self.deleteLater()
            raise
        self._timer.start()

    def ready(self):
        return self._done.is_set() and not self._thread.is_alive()

    @Slot()
    def _collect(self):
        if self.ready():
            self._timer.stop()
            self._gate.unregister(self._token)
            self.deleteLater()


class BalanceSettingsWidget(QWidget):
    """Only an explicit button accepts edits; opening this page reads no secrets."""

    def __init__(self, config, parent=None, *, backend=None):
        super().__init__(parent)
        self.setAccessibleName("余额凭据（Core 独立配置）")
        self.config, self.backend = config, backend
        self.busy = False
        self.pending_job = None
        self.balance = BalanceConfiguration(config, backend=backend)
        self.endpoint_edit = QLineEdit(self.balance.value.get("endpoint", DEFAULT_ENDPOINT), self)
        self.endpoint_edit.setMaxLength(2048)
        self.endpoint_edit.setAccessibleName("余额查询地址")
        self.secret_edit = QLineEdit(self)
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setMaxLength(16384)
        self.secret_edit.setAccessibleName("余额专用 API Key")
        self.secret_edit.setPlaceholderText("不显示已有密钥；留空保留同地址的凭据")
        self.save_button = QPushButton("保存余额凭据", self)
        self.save_button.setAccessibleName("确认保存余额凭据")
        self.save_button.setAutoDefault(False)
        self.status_label = QLabel("仅查询余额；不会自动使用 AI 包的密钥。", self)
        self.status_label.setAccessibleName("余额凭据保存状态")
        self.status_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.endpoint_edit)
        layout.addWidget(self.secret_edit)
        layout.addWidget(self.save_button)
        layout.addWidget(self.status_label)
        QWidget.setTabOrder(self.endpoint_edit, self.secret_edit)
        QWidget.setTabOrder(self.secret_edit, self.save_button)
        self.save_button.clicked.connect(self._save)

    @Slot()
    def _save(self):
        if self.busy:
            return
        self.pending_job = BalanceSaveJob(self.config, self.endpoint_edit.text(), self.secret_edit.text(), self.balance.revision, backend=self.backend)
        self.pending_job.finished.connect(self._on_saved)
        self.busy = True
        self.endpoint_edit.setReadOnly(True)
        self.secret_edit.setReadOnly(True)
        self.save_button.setEnabled(False)
        self.status_label.setText("正在保存至系统安全存储；不会请求模型或查询网络。")
        try:
            self.pending_job.start()
        except Exception:
            self._on_saved(False, "balance_worker_start_failed")

    @Slot(bool, str)
    def _on_saved(self, success, reason):
        self.busy = False
        self.endpoint_edit.setReadOnly(False)
        self.secret_edit.setReadOnly(False)
        self.save_button.setEnabled(True)
        if success:
            self.balance.reload()
            self.secret_edit.clear()
            self.status_label.setText("已保存余额配置；秘密仅留在系统安全存储。")
        else:
            self.status_label.setText(f"保存未完成：{reason}。草稿保留；请关闭重开以读取最新配置。")


def mount_balance_settings(dialog, layout, parent):
    from .feature_distribution import BUILTIN_AI

    dialog.balance_settings_widget = None
    if BUILTIN_AI:
        return
    from .settings_api import open_api_settings

    widget = QPushButton("打开 API 设置", parent)
    dialog.balance_settings_widget = widget
    widget.clicked.connect(lambda: open_api_settings(dialog.config))
    row = SettingRow("balance_credentials", "主 API", "余额直接使用主 API Key；服务需支持 DeepSeek 余额协议，不依赖 AI 包。", widget, stacked=True)
    layout.addWidget(SettingsSection("余额查询", [row], parent))
