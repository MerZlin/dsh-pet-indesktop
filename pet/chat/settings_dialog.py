from __future__ import annotations
import threading
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)
from .models import ChatSettings, ProviderConfig, SecretStore
from .providers import test_connection


class ChatSettingsDialog(QDialog):
    """AI 对话设置对话框。

    测试连接在 Python daemon 线程中执行真实请求，通过本对象的 Signal
    排队回主线程更新界面——不用 QThread，避免线程对象被提前销毁导致的
    "Destroyed while thread is still running" 崩溃（多次连续测试时偶发）。
    """
    _test_done = Signal(bool, str)
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.settings = config.chat_settings()
        p = self.settings.active_config
        self._test_thread = None
        self._test_done.connect(self._on_test_done)
        self.setWindowTitle('AI 对话设置')
        form = QFormLayout()
        self.name = QLineEdit(p.name)
        self.url = QLineEdit(p.base_url)
        self.model = QLineEdit(p.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.prompt = QPlainTextEdit(self.settings.default_system_prompt)
        self.prompt.setMinimumHeight(120)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setValue(int(p.timeout))
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0, 2)
        self.temp.setSingleStep(.1)
        self.temp.setValue(p.temperature)
        self.tokens = QSpinBox()
        self.tokens.setRange(1, 32768)
        self.tokens.setValue(p.max_tokens)
        self.skip_ssl = QCheckBox('跳过 SSL 证书验证（本地网关 / 自签名证书）')
        self.skip_ssl.setChecked(not p.verify_ssl)
        for label, w in [('Provider 名称', self.name), ('API 地址', self.url),
                         ('模型', self.model), ('API Key', self.key),
                         ('System Prompt', self.prompt), ('超时（秒）', self.timeout),
                         ('Temperature', self.temp), ('Max Tokens', self.tokens)]:
            form.addRow(label, w)
        form.addRow(self.skip_ssl)
        self.result = QLabel('')
        self.result.setWordWrap(True)
        self.test = QPushButton('测试连接')
        self.test.clicked.connect(self._run_test)
        save = QPushButton('保存')
        save.clicked.connect(self.save)
        buttons = QHBoxLayout()
        buttons.addWidget(self.test)
        buttons.addStretch(1)
        buttons.addWidget(save)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.result)
        layout.addLayout(buttons)

    def _provisional_config(self) -> ProviderConfig:
        """用表单当前值构造一份临时配置（不保存），供测试连接使用。"""
        p = self.settings.active_config
        return ProviderConfig(
            p.provider_id,
            self.name.text().strip() or p.name,
            self.url.text().strip(),
            p.chat_path,
            self.model.text().strip(),
            p.api_key_ref,
            self.key.text() or p.api_key,
            float(self.timeout.value()),
            float(self.temp.value()),
            int(self.tokens.value()),
            verify_ssl=not self.skip_ssl.isChecked(),
        )

    def _run_test(self):
        if self._test_thread is not None and self._test_thread.is_alive():
            return
        self.test.setEnabled(False)
        self.test.setText('测试中…')
        self.result.setText('')
        self.result.setStyleSheet('color: #666666;')
        self._test_thread = threading.Thread(
            target=self._run_test_worker,
            args=(self._provisional_config(),),
            daemon=True,
            name='pet-chat-connection-test',
        )
        self._test_thread.start()

    def _run_test_worker(self, provider_config: ProviderConfig):
        ok, message = test_connection(provider_config, timeout=10.0)
        self._test_done.emit(ok, message)

    def _on_test_done(self, ok: bool, message: str):
        self.test.setEnabled(True)
        self.test.setText('测试连接')
        self.result.setText(message)
        self.result.setStyleSheet('color: #16a34a;' if ok else 'color: #dc2626;')
        self._test_thread = None

    def save(self):
        p = self.settings.active_config
        p.name = self.name.text().strip() or p.name
        p.base_url = self.url.text().strip()
        p.model = self.model.text().strip()
        p.timeout = float(self.timeout.value())
        p.temperature = float(self.temp.value())
        p.max_tokens = int(self.tokens.value())
        p.verify_ssl = not self.skip_ssl.isChecked()
        key = self.key.text()
        if key:
            p.api_key_ref = p.api_key_ref or f'provider/{p.provider_id}'
            if not SecretStore().set(p.api_key_ref, key):
                p.api_key = key
        self.settings.default_system_prompt = self.prompt.toPlainText().strip()
        self.config.set_chat_settings(self.settings)
        self.config.save()
        self.accept()
