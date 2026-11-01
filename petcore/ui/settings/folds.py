"""设置页的可折叠分组（致谢页用）。

标题按钮带 ``▸/▾`` 与可访问名；内容仍是标准 :class:`SettingsCard`，所以搜索、
主题与字号规则不用另写一套。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from .base import SettingsCard


class CollapsibleSection(QWidget):
    toggled = Signal(bool)

    def __init__(self, title: str, *, expanded: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleSection")
        self._title = str(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.header = QPushButton(self)
        self.header.setObjectName("foldHeader")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setChecked(bool(expanded))
        layout.addWidget(self.header)

        self.body = SettingsCard(self)
        layout.addWidget(self.body)
        self.header.toggled.connect(self._on_toggled)
        self._apply(bool(expanded))

    def card(self) -> SettingsCard:
        return self.body

    def set_expanded(self, expanded: bool) -> None:
        checked = bool(expanded)
        if self.header.isChecked() != checked:
            self.header.setChecked(checked)
            return
        self._apply(checked)

    def is_expanded(self) -> bool:
        return bool(self.header.isChecked())

    def _on_toggled(self, checked: bool) -> None:
        self._apply(bool(checked))
        self.toggled.emit(bool(checked))

    def _apply(self, checked: bool) -> None:
        self.header.setText(f"{'▾' if checked else '▸'} {self._title}")
        self.header.setAccessibleName(f"{'折叠' if checked else '展开'} {self._title}")
        self.body.setVisible(bool(checked))
