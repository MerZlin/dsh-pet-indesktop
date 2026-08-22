from PySide6.QtWidgets import QDialog,QDoubleSpinBox,QFormLayout,QLineEdit,QPlainTextEdit,QPushButton,QSpinBox,QVBoxLayout
from .models import ChatSettings,SecretStore
class ChatSettingsDialog(QDialog):
    def __init__(self,config,parent=None):
        super().__init__(parent); self.config=config; self.settings=config.chat_settings(); p=self.settings.active_config; self.setWindowTitle('AI 对话设置'); form=QFormLayout(); self.name=QLineEdit(p.name); self.url=QLineEdit(p.base_url); self.model=QLineEdit(p.model); self.key=QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password); self.prompt=QPlainTextEdit(self.settings.default_system_prompt); self.prompt.setMinimumHeight(120); self.timeout=QSpinBox(); self.timeout.setRange(1,600); self.timeout.setValue(int(p.timeout)); self.temp=QDoubleSpinBox(); self.temp.setRange(0,2); self.temp.setSingleStep(.1); self.temp.setValue(p.temperature); self.tokens=QSpinBox(); self.tokens.setRange(1,32768); self.tokens.setValue(p.max_tokens)
        for label,w in [('Provider 名称',self.name),('API 地址',self.url),('模型',self.model),('API Key',self.key),('System Prompt',self.prompt),('超时（秒）',self.timeout),('Temperature',self.temp),('Max Tokens',self.tokens)]: form.addRow(label,w)
        self.test=QPushButton('测试连接'); self.test.clicked.connect(lambda:self.test.setText('请保存后发送消息测试')); save=QPushButton('保存'); save.clicked.connect(self.save); layout=QVBoxLayout(self); layout.addLayout(form); layout.addWidget(self.test); layout.addWidget(save)
    def save(self):
        p=self.settings.active_config; p.name=self.name.text().strip() or p.name; p.base_url=self.url.text().strip(); p.model=self.model.text().strip(); p.timeout=float(self.timeout.value()); p.temperature=float(self.temp.value()); p.max_tokens=int(self.tokens.value()); key=self.key.text()
        if key:
            p.api_key_ref=p.api_key_ref or f'provider/{p.provider_id}'
            if not SecretStore().set(p.api_key_ref,key): p.api_key=key
        self.settings.default_system_prompt=self.prompt.toPlainText().strip(); self.config.set_chat_settings(self.settings); self.config.save(); self.accept()