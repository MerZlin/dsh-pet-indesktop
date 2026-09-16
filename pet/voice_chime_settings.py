# -*- coding: utf-8 -*-
"""语音报时设置页（现代设置对话框侧栏页）。

配置键（config.py 顶层平铺键）：
    voice_chime_enabled / voice_chime_schedule / voice_chime_custom_times /
    voice_chime_voice / voice_chime_rate / voice_chime_pitch / voice_chime_volume

风格对齐 pet/exploration_watchdog_settings.py：自含 QWidget 页，
提供 apply_to_config / refresh_from_config 与 settings_saved 信号，
由 modern_settings_dialog.py 在 automation 域注册并参与 _write_config 保存。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .modern_settings_dialog import (
    BrowserSpinBox,
    SettingRow,
    SettingsSection,
    ToggleSwitch,
)
from .settings_widgets import ModernSelect
from .voice_chime import (
    COMMON_VOICES,
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_VOLUME,
    DEFAULT_VOICE,
    SCHEDULE_KEYS,
    SCHEDULE_LABELS,
    clean_pitch,
    clean_rate,
    clean_schedule,
    clean_voice,
    clean_volume,
)


class VoiceChimeSettingsPage(QWidget):
    """自含语音报时设置页。"""

    # 用户点击「试听」时发出（payload: 当前试听文案，空串表示按当前时间组装）
    preview_requested = Signal(str)
    settings_saved = Signal()

    def __init__(self, config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config

        # ---- 基础设置 ----
        self.enabled_check = ToggleSwitch(self)
        self.enabled_check.setChecked(bool(self.config.get("voice_chime_enabled", True)))

        # ---- 调度 ----
        self.schedule_select = ModernSelect(self, width=170)
        for key in SCHEDULE_KEYS:
            self.schedule_select.addItem(SCHEDULE_LABELS[key], key)
        self.schedule_select.setCurrentData(clean_schedule(self.config.get("voice_chime_schedule", "hourly")))

        self.custom_edit = QLineEdit(self)
        self.custom_edit.setText(str(self.config.get("voice_chime_custom_times", "") or ""))
        self.custom_edit.setPlaceholderText("如 08:30, 12:00, 23:59（逗号分隔）")
        self.schedule_select.currentIndexChanged.connect(self._refresh_custom_enabled)

        # ---- 语音 ----
        self.voice_edit = QLineEdit(self)
        self.voice_edit.setText(clean_voice(self.config.get("voice_chime_voice", DEFAULT_VOICE)))
        self.voice_edit.setPlaceholderText("edge-tts 音色名，如 " + DEFAULT_VOICE)
        # 常见音色列表挂在 tooltip 上（COMMON_VOICES 是纯数据层给的清单，
        # 提示文案承诺「可查看」就必须真能看到，不做无接线的装饰性文案）。
        self.voice_edit.setToolTip(
            "常见音色（可直接填名字）：\n" + "\n".join(f"· {v}" for v in COMMON_VOICES))

        # 速率/音调/音量一律走纯逻辑层清洗：config.json 被手改成非法值时
        # 回落默认值，绝不让设置页在构造期抛异常把用户挡在设置界面之外。
        self.rate_spin = BrowserSpinBox(self)
        self.rate_spin.setRange(-100, 100)
        self.rate_spin.setSuffix(" %")
        self.rate_spin.setValue(clean_rate(self.config.get("voice_chime_rate", DEFAULT_RATE)))
        self.rate_spin.setToolTip("语速偏移：0 为正常，正数更快，负数更慢")

        self.pitch_spin = BrowserSpinBox(self)
        self.pitch_spin.setRange(-50, 50)
        self.pitch_spin.setSuffix(" Hz")
        self.pitch_spin.setValue(clean_pitch(self.config.get("voice_chime_pitch", DEFAULT_PITCH)))
        self.pitch_spin.setToolTip("音调偏移：0 为正常，正数更尖锐，负数更低沉")

        self.volume_spin = BrowserSpinBox(self)
        self.volume_spin.setRange(0, 100)
        self.volume_spin.setSuffix(" %")
        self.volume_spin.setValue(clean_volume(self.config.get("voice_chime_volume", DEFAULT_VOLUME)))

        self.preview_btn = QPushButton("试听", self)
        self.preview_btn.setToolTip("按当前配置立即播报一句报时+台词")
        self.preview_btn.clicked.connect(self._on_preview_clicked)

        # ---- Layout ----
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        root.addWidget(SettingsSection("基础设置", [
            SettingRow("voice_chime_enabled", "启用语音报时",
                        "开启后按下方调度规则定时语音报时，并附随机台词/歌词。",
                        self.enabled_check),
            SettingRow("voice_chime_schedule", "报时频率",
                        "整点 / 每30分钟 / 每15分钟 / 每5分钟 / 每分钟 / 自定义时间点。",
                        self.schedule_select),
            SettingRow("voice_chime_custom_times", "自定义时间点",
                        "仅「自定义时间点」模式生效；HH:MM 逗号分隔，如 08:30, 12:00。",
                        self.custom_edit),
        ], self))

        root.addWidget(SettingsSection("语音", [
            SettingRow("voice_chime_voice", "音色",
                        "edge-tts 在线音色名（免费、无需 API Key）；鼠标悬停输入框可看常见音色列表。",
                        self.voice_edit),
            SettingRow("voice_chime_rate", "语速",
                        "语速偏移百分比：0 为正常，正数更快，负数更慢。",
                        self.rate_spin),
            SettingRow("voice_chime_pitch", "音调",
                        "音调偏移（Hz）：0 为正常，正数更尖锐，负数更低沉。",
                        self.pitch_spin),
            SettingRow("voice_chime_volume", "音量",
                        "报时播放音量（0-100）。",
                        self.volume_spin),
        ], self))

        tip = QHBoxLayout()
        tip.setContentsMargins(0, 0, 0, 0)
        tip.addStretch(1)
        tip.addWidget(self.preview_btn)
        root.addLayout(tip)

        self._refresh_custom_enabled()

    # ------------------------------------------------------------ 交互
    def _on_preview_clicked(self) -> None:
        # 先落盘当前控件值，再发试听信号（服务端按最新配置合成播放）
        self.apply_to_config()
        self.preview_requested.emit("")

    def _refresh_custom_enabled(self) -> None:
        is_custom = self.schedule_select.currentData() == "custom"
        self.custom_edit.setEnabled(is_custom)

    # ------------------------------------------------------------ 配置读写
    def apply_to_config(self) -> None:
        """把控件值合并写回 config（仅写语音报时 7 键）。"""
        if self.config is None:
            return
        self.config.set("voice_chime_enabled", self.enabled_check.isChecked())
        self.config.set("voice_chime_schedule", self.schedule_select.currentData() or "hourly")
        self.config.set("voice_chime_custom_times", self.custom_edit.text().strip())
        self.config.set("voice_chime_voice", clean_voice(self.voice_edit.text()))
        self.config.set("voice_chime_rate", self.rate_spin.value())
        self.config.set("voice_chime_pitch", self.pitch_spin.value())
        self.config.set("voice_chime_volume", self.volume_spin.value())
        self.settings_saved.emit()

    def refresh_from_config(self) -> None:
        """用当前配置刷新控件（外部取消保存后回滚用）。"""
        self.enabled_check.setChecked(bool(self.config.get("voice_chime_enabled", True)))
        self.schedule_select.setCurrentData(clean_schedule(self.config.get("voice_chime_schedule", "hourly")))
        self.custom_edit.setText(str(self.config.get("voice_chime_custom_times", "") or ""))
        self.voice_edit.setText(clean_voice(self.config.get("voice_chime_voice", DEFAULT_VOICE)))
        self.rate_spin.setValue(clean_rate(self.config.get("voice_chime_rate", DEFAULT_RATE)))
        self.pitch_spin.setValue(clean_pitch(self.config.get("voice_chime_pitch", DEFAULT_PITCH)))
        self.volume_spin.setValue(clean_volume(self.config.get("voice_chime_volume", DEFAULT_VOLUME)))
        self._refresh_custom_enabled()


