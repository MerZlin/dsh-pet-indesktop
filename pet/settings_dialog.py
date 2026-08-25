from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from . import autostart as autostart_mod
from .config import (
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
)


class PetSettingsDialog(QDialog):
    """桌宠动画节奏与自言自语设置；非模态，打开时桌宠仍可拖动。"""

    settings_saved = Signal()

    def __init__(self, config, parent=None, enable_chat: bool = True):
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

        self.click_sound_check = QCheckBox("点击 Q 弹音效（可自定义声音：把 click.wav 放到数据目录 sounds/）")
        self.click_sound_check.setChecked(bool(config.get("click_sound_enabled", True)))
        form.addRow("音效", self.click_sound_check)

        # DeepSeek 余额相关仅 Chat 版显示（无 Chat 变体没有 API Key 可查）
        self.click_balance_check: QCheckBox | None = None
        self.balance_spin: QSpinBox | None = None
        if enable_chat:
            self.click_balance_check = QCheckBox("点击显示 DeepSeek 余额（与下方自言自语可同时勾选，自动排队显示）")
            self.click_balance_check.setChecked(bool(config.get("click_show_balance", False)))
            form.addRow("点击行为", self.click_balance_check)
            self.balance_spin = QSpinBox()
            self.balance_spin.setRange(0, 1440)
            self.balance_spin.setSuffix(" 分钟")
            self.balance_spin.setValue(int(config.get("balance_refresh_minutes", 0) or 0))
            self.balance_spin.setToolTip("0 表示关闭自动刷新，仅菜单手动查询")
            form.addRow("余额自动刷新", self.balance_spin)
        self.click_talk_check = QCheckBox("点击随机显示一条自定义自言自语")
        self.click_talk_check.setChecked(bool(config.get("click_show_self_talk", False)))
        form.addRow("", self.click_talk_check)

        # 开机自启 / 全屏自动隐藏（从主菜单移入设置）
        self.autostart_check = QCheckBox("开机自动启动桌宠")
        self.autostart_check.setChecked(autostart_mod.is_enabled())
        form.addRow("开机自启", self.autostart_check)
        self.auto_hide_check: QCheckBox | None = None
        if sys.platform == "win32":
            self.auto_hide_check = QCheckBox("前台程序全屏时自动隐藏桌宠（如全屏视频/游戏）")
            self.auto_hide_check.setChecked(bool(config.get("auto_hide_fullscreen", True)))
            form.addRow("全屏时自动隐藏", self.auto_hide_check)
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
        self.config.set("click_sound_enabled", self.click_sound_check.isChecked())
        if self.click_balance_check is not None:
            self.config.set("click_show_balance", self.click_balance_check.isChecked())
        self.config.set("click_show_self_talk", self.click_talk_check.isChecked())
        if self.balance_spin is not None:
            self.config.set("balance_refresh_minutes", int(self.balance_spin.value()))
        # 开机自启立即生效（写注册表/LaunchAgent plist），记录期望状态供启动自检
        autostart_ok = autostart_mod.set_enabled(self.autostart_check.isChecked())
        self.config.set("autostart_wanted", self.autostart_check.isChecked())
        if not autostart_ok:
            QMessageBox.warning(
                self, "开机自启",
                "写入开机自启失败：可能被安全软件拦截。\n"
                "可稍后在托盘菜单重试，或检查安全软件/系统优化工具的拦截记录。",
            )
        elif self.autostart_check.isChecked() and sys.platform == "darwin":
            QMessageBox.information(
                self, "开机自启",
                "已开启开机自启；如重启未生效，请到\n"
                "「系统设置 → 通用 → 登录项」中允许桌宠。",
            )
        if self.auto_hide_check is not None:
            self.config.set("auto_hide_fullscreen", self.auto_hide_check.isChecked())
        self.config.save()
        self.settings_saved.emit()
        self.accept()