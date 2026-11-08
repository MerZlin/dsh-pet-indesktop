"""Taskbar recovery surface for desktops without a status-notifier host."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .settings.base import make_button


class RecoveryWindow(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setWindowTitle("dsh-pet core · 桌宠控制")
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        label = QLabel("当前桌面没有可用托盘。可从此窗口找回隐藏或穿透的桌宠。关闭窗口会最小化到任务栏。", self)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.restore_button = make_button("显示全部并恢复鼠标交互", self)
        self.restore_button.setAccessibleName("显示全部并恢复鼠标交互")
        self.restore_button.clicked.connect(self.restore)
        layout.addWidget(self.restore_button)
        for title, callback in (("桌宠设置", host.open_settings), ("退出应用", host.quit_app)):
            button = make_button(title, self)
            button.setAccessibleName(title)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def restore(self):
        self.host.set_all_click_through(False)
        self.host.set_all_visible(True)

    def closeEvent(self, event):
        # QApplication.quit first closes windows. Vetoing this event would also
        # veto the tray/menu Quit command, before aboutToQuit can clean up.
        if getattr(self.host, "_closing", False):
            event.accept()
            return
        event.ignore()
        self.showMinimized()
