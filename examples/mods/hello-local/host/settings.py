"""Settings owns its draft; refreshing other MODs must not replace this widget."""
from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget


class Settings(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.editor = QLineEdit(self)
        self.editor.setAccessibleName("示例消息")
        self.status = QLabel(self)
        from PySide6.QtCore import Qt
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("菜单命令发送的消息（不联网）", self))
        layout.addWidget(self.editor)
        layout.addWidget(self.status)
        self.discard_changes()

    def dirty(self):
        return self.editor.text() != self._saved

    def draft(self):
        return {"message": self.editor.text()}

    def confirm_save(self):
        try:
            self.context.configuration.commit_namespace(self.draft(), expected_revision=self._revision)
        except (OSError, ValueError, RuntimeError):
            self.status.setText("未保存：配置已在其他窗口变化。请复制草稿后点放弃修改以重新读取。")
            return False
        self._saved = self.editor.text()
        self._revision = self.context.configuration.revision()
        self.status.setText("已保存，新请求立即读取。")
        return True

    def discard_changes(self):
        self._revision = self.context.configuration.revision()
        self._saved = self.context.configuration.read_namespace().get("message", "你好！这是独立安装的 MOD。")
        self.editor.setText(self._saved)
        self.status.clear()
        return True

    def dispose(self):
        self.hide()
        self.deleteLater()
