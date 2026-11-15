"""会话回收站：30 天内可恢复，永久删除前必须二次确认。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


def _stamp(value: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError, OSError):
        return "未知时间"


class TrashDialog(QDialog):
    """列出回收站会话，提供恢复 / 永久删除 / 清空。"""

    changed = Signal()

    def __init__(self, store, *, retention_days: int = 30, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.retention_days = max(0, int(retention_days or 0))
        self.setWindowTitle("回收站")
        self.setModal(True)
        self.resize(560, 430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        self.hint = QLabel(
            f"删除的会话先放在这里，保留 {self.retention_days} 天；恢复后回到原会话列表。",
            self,
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.list = QListWidget(self)
        self.list.setObjectName("chat-trash-list")
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        self.list.itemDoubleClicked.connect(lambda _item: self._restore())
        layout.addWidget(self.list, 1)

        self.empty_label = QLabel("回收站是空的。", self)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("chat-trash-empty")
        layout.addWidget(self.empty_label)

        buttons = QHBoxLayout()
        self.restore_button = QPushButton("恢复", self)
        self.restore_button.clicked.connect(self._restore)
        self.delete_button = QPushButton("永久删除", self)
        self.delete_button.clicked.connect(self._delete)
        self.empty_button = QPushButton("清空回收站", self)
        self.empty_button.clicked.connect(self._empty)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.empty_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.refresh()

    # ------------------------------------------------------------ 数据
    def refresh(self) -> None:
        self.list.clear()
        sessions = self.store.list_trashed()
        now = time.time()
        for session in sessions:
            elapsed_days = max(0, int((now - float(session.trashed_at or now)) / 86400))
            left = max(0, self.retention_days - elapsed_days) if self.retention_days else 0
            suffix = f"剩余 {left} 天" if self.retention_days else "不会自动清理"
            item = QListWidgetItem(
                f"{session.title or '未命名会话'}  ·  删除于 {_stamp(session.trashed_at)}  ·  {suffix}"
            )
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            self.list.addItem(item)
        has_items = self.list.count() > 0
        self.list.setVisible(has_items)
        self.empty_label.setVisible(not has_items)
        self.empty_button.setEnabled(has_items)
        self._sync_buttons()

    def _selected_id(self) -> str:
        item = self.list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _sync_buttons(self) -> None:
        selected = bool(self._selected_id())
        self.restore_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    # ------------------------------------------------------------ 动作
    def _restore(self) -> None:
        session_id = self._selected_id()
        if not session_id:
            return
        try:
            restored = self.store.restore(session_id)
        except OSError:
            QMessageBox.warning(self, "恢复失败", "会话文件写入失败，请检查磁盘权限。")
            return
        if restored is None:
            QMessageBox.warning(self, "恢复失败", "这个会话已经不存在了。")
        self.refresh()
        self.changed.emit()

    def _delete(self) -> None:
        session_id = self._selected_id()
        if not session_id:
            return
        item = self.list.currentItem()
        title = item.text().split("  ·  ")[0] if item is not None else "这个会话"
        answer = QMessageBox.question(
            self,
            "永久删除会话",
            f"确定永久删除「{title}」？删除后无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(session_id)
        except OSError:
            QMessageBox.warning(self, "删除失败", "会话文件删除失败，请检查磁盘权限。")
            return
        self.refresh()
        self.changed.emit()

    def _empty(self) -> None:
        count = self.list.count()
        if count <= 0:
            return
        answer = QMessageBox.question(
            self,
            "清空回收站",
            f"确定清空回收站？{count} 个会话将被永久删除，无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed = 0
        for row in range(count):
            item = self.list.item(row)
            session_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if not session_id:
                continue
            try:
                self.store.delete(session_id)
            except OSError:
                failed += 1
        self.refresh()
        self.changed.emit()
        if failed:
            QMessageBox.warning(
                self, "部分删除失败", f"有 {failed} 个会话没能删除，请检查磁盘权限。"
            )
