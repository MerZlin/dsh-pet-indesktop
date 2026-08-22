from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from .config import (
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
)


class PetSettingsDialog(QDialog):
    """桌宠动画节奏与自言自语设置；非模态，打开时桌宠仍可拖动。"""

    settings_saved = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("桌宠设置")
        self.setMinimumWidth(430)
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        intro = QLabel("调整动画节奏，并配置桌宠偶尔冒出的思考气泡。")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 3600.0)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSuffix(" 秒")
        self.gap_spin.setValue(float(config.get("animation_gap_seconds", 0.0)))
        self.gap_spin.setToolTip("非待机/非转向动画之间的等待时间；0 秒保持连续播放。")
        form.addRow("动作等待间隔", self.gap_spin)

        self.self_talk_check = QCheckBox("开启自言自语气泡")
        self.self_talk_check.setChecked(bool(config.get("self_talk_enabled", False)))
        form.addRow("气泡自言自语", self.self_talk_check)

        self.min_spin = QDoubleSpinBox()
        self.min_spin.setRange(5.0, 3600.0)
        self.min_spin.setSingleStep(1.0)
        self.min_spin.setDecimals(0)
        self.min_spin.setSuffix(" 秒")
        self.min_spin.setValue(float(config.get("self_talk_min_interval", DEFAULT_SELF_TALK_MIN_INTERVAL)))
        form.addRow("随机间隔最短", self.min_spin)

        self.max_spin = QDoubleSpinBox()
        self.max_spin.setRange(5.0, 3600.0)
        self.max_spin.setSingleStep(1.0)
        self.max_spin.setDecimals(0)
        self.max_spin.setSuffix(" 秒")
        self.max_spin.setValue(float(config.get("self_talk_max_interval", DEFAULT_SELF_TALK_MAX_INTERVAL)))
        form.addRow("随机间隔最长", self.max_spin)
        root.addLayout(form)

        root.addWidget(QLabel("自言自语内容（每行一条，留空则恢复内置内容）："))
        self.texts_edit = QPlainTextEdit()
        texts = config.get("self_talk_texts", DEFAULT_SELF_TALK_TEXTS)
        self.texts_edit.setPlainText("\n".join(str(item) for item in texts))
        self.texts_edit.setMinimumHeight(130)
        root.addWidget(self.texts_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _save(self) -> None:
        minimum = min(self.min_spin.value(), self.max_spin.value())
        maximum = max(self.min_spin.value(), self.max_spin.value())
        texts = [line.strip()[:120] for line in self.texts_edit.toPlainText().splitlines() if line.strip()]
        if not texts:
            texts = list(DEFAULT_SELF_TALK_TEXTS)
        self.config.set("animation_gap_seconds", self.gap_spin.value())
        self.config.set("self_talk_enabled", self.self_talk_check.isChecked())
        self.config.set("self_talk_min_interval", minimum)
        self.config.set("self_talk_max_interval", maximum)
        self.config.set("self_talk_texts", texts)
        self.config.save()
        self.settings_saved.emit()
        self.accept()