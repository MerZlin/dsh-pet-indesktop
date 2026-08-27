from __future__ import annotations
import threading
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from .models import ChatSettings, ProviderConfig, SecretStore
from .providers import test_connection
from .themes import theme_names


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
        self.key.setPlaceholderText('留空表示不修改已保存的 Key')
        self.key_hint = QLabel(self._key_status(p))
        self.key_hint.setObjectName('key-hint')
        self.key_hint.setWordWrap(True)
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
        # 视觉模型（看看屏幕）：默认同聊天模型推导；取消勾选可手填（如免费 glm-4.6v-flash）
        self.vmodel = QLineEdit(p.vision_model)
        self.vmodel.setPlaceholderText('留空自动推导；免费视觉可用智谱 glm-4.6v-flash')
        self.vsame = QCheckBox('视觉模型同聊天模型（ds 文本模型自动换 vision-exp；GLM/Kimi 等多模态直接复用）')
        self.vsame.setChecked(p.vision_same_as_chat)
        self.vurl = QLineEdit(p.vision_base_url)
        self.vurl.setPlaceholderText('视觉 API 地址（留空复用聊天地址；GLM 填 https://open.bigmodel.cn/api/paas/v4）')
        self.vkey = QLineEdit()
        self.vkey.setEchoMode(QLineEdit.EchoMode.Password)
        self.vkey.setPlaceholderText('视觉 API Key（留空复用聊天 Key）')
        _vextra = [self.vmodel, self.vurl, self.vkey]
        def _vtoggle(c):
            for w in _vextra:
                w.setEnabled(not c)
        _vtoggle(p.vision_same_as_chat)
        self.vsame.toggled.connect(_vtoggle)
        # 聊天背景：纯色 / 内置主题 / 自定义图片 + 裁切取景
        self._bg_keys = [k for k, _ in theme_names()]
        self.bg_mode = QComboBox()
        self.bg_mode.addItems(['纯色（奶油）'] + [n for _, n in theme_names()] + ['自定义图片…'])
        cur = str(config.get('chat_background', '') or '')
        self.bg = QLineEdit(cur if cur and not cur.startswith('builtin:') else '')
        self.bg.setPlaceholderText('自定义图片路径，或点浏览选图')
        self.bg_btn = QPushButton('浏览…')
        self.bg_btn.clicked.connect(self._pick_bg)
        bg_row = QWidget()
        bg_lay = QHBoxLayout(bg_row)
        bg_lay.setContentsMargins(0, 0, 0, 0)
        bg_lay.addWidget(self.bg)
        bg_lay.addWidget(self.bg_btn)
        self.crop_btn = QPushButton('裁切取景…')
        self.crop_btn.clicked.connect(self._crop_bg)
        bgmode_row = QWidget()
        bgm_lay = QHBoxLayout(bgmode_row)
        bgm_lay.setContentsMargins(0, 0, 0, 0)
        bgm_lay.addWidget(self.bg_mode)
        bgm_lay.addWidget(self.crop_btn)
        theme_idx = self._bg_keys.index(cur[8:]) + 1 if cur.startswith('builtin:') and cur[8:] in self._bg_keys else None
        self.bg_mode.setCurrentIndex(0 if not cur else (theme_idx if theme_idx is not None else len(self._bg_keys) + 1))
        bg_row.setVisible(bool(cur) and theme_idx is None)
        self.bg_mode.currentIndexChanged.connect(lambda i: bg_row.setVisible(i == len(self._bg_keys) + 1))
        self.skip_ssl = QCheckBox('跳过 SSL 证书验证（开着代理/梯子、本地网关或自签名证书时勾选）')
        self.skip_ssl.setChecked(not p.verify_ssl)
        for label, w in [('Provider 名称', self.name), ('API 地址', self.url),
                         ('模型', self.model), ('', self.vsame), ('视觉模型', self.vmodel), ('视觉 API 地址', self.vurl), ('视觉 API Key', self.vkey),
                         ('API Key', self.key), ('', self.key_hint), ('System Prompt', self.prompt),
                         ('聊天背景', bgmode_row), ('', bg_row),
                         ('超时（秒）', self.timeout), ('Temperature', self.temp),
                         ('Max Tokens', self.tokens)]:
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

    def _pick_bg(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择聊天背景图', '', '图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self.bg.setText(path)

    def _crop_bg(self):
        from .crop_dialog import CropDialog
        from .themes import get_theme
        from .widgets import resolve_bg_pixmap
        i = self.bg_mode.currentIndex()
        value = '' if i == 0 else ('builtin:' + self._bg_keys[i - 1] if i <= len(self._bg_keys) else self.bg.text().strip())
        pix = resolve_bg_pixmap(value)
        if pix is None:
            self.crop_btn.setText('无可裁背景')
            return
        crops = dict(self.config.get('chat_bg_crops', {}) or {})
        initial = crops.get(value)
        if initial is None and value.startswith('builtin:'):
            t = get_theme(value[8:])
            initial = tuple(t['focus']) if t else None
        dlg = CropDialog(pix, initial, self)
        if dlg.exec():
            reset, box = dlg.result_box()
            if reset:
                crops.pop(value, None)
            else:
                crops[value] = [round(float(v), 4) for v in box]
            self.config.set('chat_bg_crops', crops)
            self.config.save()

    @staticmethod
    def _key_status(p) -> str:
        """当前是否已保存 API Key（提示用户留空不修改，无需每次重输）。"""
        saved = p.api_key or SecretStore().get(p.api_key_ref)
        if saved:
            return '已保存 API Key（留空保持不变，修改 System Prompt 无需重输）'
        return '尚未设置 API Key（填入后保存即生效）'

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
            # 表单未填时回退钥匙串：凭据默认存系统钥匙串，直接读 api_key 为空
            self.key.text() or p.api_key or SecretStore().get(p.api_key_ref),
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
        p.vision_model = self.vmodel.text().strip()
        p.vision_same_as_chat = self.vsame.isChecked()
        p.vision_base_url = self.vurl.text().strip()
        vkey = self.vkey.text()
        if vkey:
            p.vision_api_key_ref = f'provider/{p.provider_id}/vision'
            if not SecretStore().set(p.vision_api_key_ref, vkey):
                p.vision_api_key = vkey
        p.verify_ssl = not self.skip_ssl.isChecked()
        key = self.key.text()
        if key:
            p.api_key_ref = p.api_key_ref or f'provider/{p.provider_id}'
            if not SecretStore().set(p.api_key_ref, key):
                p.api_key = key
        i = self.bg_mode.currentIndex()
        bg_val = '' if i == 0 else ('builtin:' + self._bg_keys[i - 1] if i <= len(self._bg_keys) else self.bg.text().strip())
        self.config.set('chat_background', bg_val)
        self.settings.default_system_prompt = self.prompt.toPlainText().strip()
        self.config.set_chat_settings(self.settings)
        self.config.save()
        self.accept()
