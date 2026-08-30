# -*- coding: utf-8 -*-
"""点击动画台词绑定编辑对话框。

每个点击动画可绑定多条专属自言自语台词；点击角色时优先播放当前动画
绑定的台词，未绑定时回退全局随机自言自语。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import catalog


def _discover_click_names(character_id: str) -> list[str]:
    """从素材目录发现当前角色的点击动画名；目录缺失时回退 catalog 常量。"""
    base = Path(__file__).resolve().parent.parent / "assets" / "characters"
    click_dir = base / str(character_id) / "videos" / "click"
    if click_dir.is_dir():
        names = sorted(p.stem for p in click_dir.glob("*.webm"))
        if names:
            return names
    return list(catalog.CLICKS)


class ClickTalkBindingsDialog(QDialog):
    """维护当前角色的 动画id → [台词...] 绑定。"""

    def __init__(self, config, click_names: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.character_id = str(config.get("character", catalog.DEFAULT_CHARACTER))
        self.click_names = list(click_names) if click_names else _discover_click_names(self.character_id)
        self.bindings = {
            str(action_id): list(texts)
            for action_id, texts in config.click_talk_bindings(self.character_id).items()
        }
        self._dirty = False

        self.setWindowTitle("点击动画台词绑定")
        self.resize(560, 420)
        self.setMinimumSize(480, 360)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "每个点击动画可绑定多条专属自言自语台词，每行一条。\n"
            "点击桌宠时会优先播放当前动画绑定的台词；未绑定时回退全局随机自言自语。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.addItems(self.click_names)
        self.list.setFixedWidth(200)
        body.addWidget(self.list)

        right = QVBoxLayout()
        right.addWidget(QLabel("绑定台词（每行一条）："))
        self.text_edit = QPlainTextEdit()
        right.addWidget(self.text_edit)
        body.addLayout(right)
        layout.addLayout(body)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("保存")
        cancel = QPushButton("取消")
        save.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        self.list.currentRowChanged.connect(self._load_selected)
        if self.click_names:
            self.list.setCurrentRow(0)

    def _load_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.click_names):
            self.text_edit.setPlainText("")
            return
        action_id = self.click_names[row]
        self.text_edit.setPlainText("\n".join(self.bindings.get(action_id, [])))

    def _save(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.click_names):
            action_id = self.click_names[row]
            texts = [line.strip() for line in self.text_edit.toPlainText().splitlines() if line.strip()]
            if texts:
                self.bindings[action_id] = texts[:50]
            else:
                self.bindings.pop(action_id, None)
        self.config.set_click_talk_bindings(self.character_id, self.bindings)
        self._dirty = True
        self.accept()

    def bindings_dict(self) -> dict:
        return self.bindings
