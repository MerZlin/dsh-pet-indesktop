# -*- coding: utf-8 -*-
"""语音报时设置页（现代设置对话框侧栏页）。

配置键（config.py 顶层平铺键）：
    voice_chime_enabled / voice_chime_schedule / voice_chime_custom_times /
    voice_chime_voice / voice_chime_rate / voice_chime_pitch / voice_chime_volume /
    voice_chime_show_bubble / voice_chime_show_quote /
    voice_chime_custom_quotes_zh / voice_chime_custom_quotes_en

台词/歌词按本地时间每 8 小时整体换一批（0-8 / 8-16 / 16-24 各对应库中一批，
跨周期切到新批次），同一周期内每次报时在批次内按顺序轮换取不同条目；自定义
台词非空时替换内置库参与同样的分批轮换，留空回退内置库。气泡报时文字以阿拉伯
数字展示（如“现在下午 15:45”），语音口播仍为中文数字。

风格对齐 pet/exploration_watchdog_settings.py：自含 QWidget 页，
提供 apply_to_config / refresh_from_config 与 settings_saved 信号，
由 modern_settings_dialog.py 注册为侧边栏「语音」总域下的「语音报时」分组并参与
_write_config 保存。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLineEdit,
    QPlainTextEdit,
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
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_SHOW_BUBBLE,
    DEFAULT_SHOW_QUOTE,
    DEFAULT_VOICE,
    DEFAULT_VOLUME,
    SCHEDULE_KEYS,
    SCHEDULE_LABELS,
    VOICE_OPTIONS,
    clean_pitch,
    clean_rate,
    clean_schedule,
    clean_voice,
    clean_volume,
)


def _sync_voice_select(select: ModernSelect, config) -> None:
    """按配置值刷新音色下拉：配置音色不在预设列表时追加“自定义”项并选中。"""
    current = clean_voice(config.get("voice_chime_voice", DEFAULT_VOICE))
    if select.findData(current) < 0:
        select.addItem(f"自定义：{current}", current)
    select.setCurrentData(current)


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
        self.voice_select = ModernSelect(self, width=230)
        for value, label in VOICE_OPTIONS:
            self.voice_select.addItem(label, value)
        _sync_voice_select(self.voice_select, self.config)

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

        self.bubble_check = ToggleSwitch(self)
        self.bubble_check.setChecked(bool(self.config.get("voice_chime_show_bubble", DEFAULT_SHOW_BUBBLE)))

        self.quote_check = ToggleSwitch(self)
        self.quote_check.setChecked(bool(self.config.get("voice_chime_show_quote", DEFAULT_SHOW_QUOTE)))

        # ---- 自定义台词/歌词（一行一条；留空回退内置库）----
        self.custom_zh_edit = QPlainTextEdit(self)
        self.custom_zh_edit.setMinimumSize(280, 84)
        self.custom_zh_edit.setMaximumHeight(180)
        self.custom_zh_edit.setPlaceholderText(
            "每行一条，例如：\n又是元气满满的一天～\n该起来喝口水、动一动啦。"
        )
        self.custom_zh_edit.setPlainText(str(self.config.get("voice_chime_custom_quotes_zh", "") or ""))

        self.custom_en_edit = QPlainTextEdit(self)
        self.custom_en_edit.setMinimumSize(280, 84)
        self.custom_en_edit.setMaximumHeight(180)
        self.custom_en_edit.setPlaceholderText(
            "One quote per line, e.g.\nTake a short break and stretch.\nYou've got this!"
        )
        self.custom_en_edit.setPlainText(str(self.config.get("voice_chime_custom_quotes_en", "") or ""))

        self.preview_btn = QPushButton("立即试听", self)
        self.preview_btn.setToolTip("按当前配置立即播报一句报时+台词（无需等待报时点）")
        self.preview_btn.clicked.connect(self._on_preview_clicked)

        # ---- Layout ----
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        root.addWidget(
            SettingsSection(
                "基础设置",
                [
                    SettingRow("voice_chime_enabled", "启用语音报时", "开启后按下方调度规则定时语音报时，并附台词/歌词（每 8 小时整体换一批，同周期内按序轮换）。", self.enabled_check),
                    SettingRow("voice_chime_schedule", "报时频率", "整点 / 每30分钟 / 每15分钟 / 每5分钟 / 每分钟 / 自定义时间点。", self.schedule_select),
                    SettingRow("voice_chime_custom_times", "自定义时间点", "仅「自定义时间点」模式生效；HH:MM 逗号分隔，如 08:30, 12:00。", self.custom_edit),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "语音",
                [
                    SettingRow(
                        "voice_chime_voice",
                        "音色",
                        "内置 20+ 款中英文音色（edge-tts 在线合成，无需 API Key）；配置值不在列表时自动追加“自定义”项。",
                        self.voice_select,
                    ),
                    SettingRow("voice_chime_rate", "语速", "语速偏移百分比：0 为正常，正数更快，负数更慢。", self.rate_spin),
                    SettingRow("voice_chime_pitch", "音调", "音调偏移（Hz）：0 为正常，正数更尖锐，负数更低沉。", self.pitch_spin),
                    SettingRow("voice_chime_volume", "音量", "报时播放音量（0-100）。", self.volume_spin),
                    SettingRow("voice_chime_show_bubble", "报时气泡", "报时时在桌宠头顶显示气泡文字（含台词/歌词）。", self.bubble_check),
                    SettingRow("voice_chime_show_quote", "台词/歌词", "报时时附带台词/歌词（每 8 小时整体换一批，同周期内每次报时按序取不同条目）；关闭后仅播报时间文本。", self.quote_check),
                    SettingRow("voice_chime_preview", "立即试听", "按当前配置立即播报一句“报时文本 + 台词/歌词”，无需等待报时点。", self.preview_btn),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "台词/歌词",
                [
                    SettingRow(
                        "voice_chime_custom_quotes_zh",
                        "自定义台词/歌词（中文）",
                        "每行一条，与内置中文库的关系：填了就整体替换内置库参与分批轮换"
                        "（每 8 小时整体换一批，同一周期内每次报时按序取不同条目）；"
                        "留空则自动回退内置中文台词库。中文音色报时时使用这里的内容。",
                        self.custom_zh_edit,
                        stacked=True,
                    ),
                    SettingRow(
                        "voice_chime_custom_quotes_en",
                        "自定义台词/歌词（英文）",
                        "每行一条，规则同上（每 8 小时整体换一批，同一周期内按序取不同条目）；"
                        "留空则自动回退内置英文台词库，与中文库各自独立分批轮换。"
                        "非中文音色报时时使用这里的内容。",
                        self.custom_en_edit,
                        stacked=True,
                    ),
                ],
                self,
            )
        )

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
        """把控件值合并写回 config（仅写语音报时 11 键）。"""
        if self.config is None:
            return
        self.config.set("voice_chime_enabled", self.enabled_check.isChecked())
        self.config.set("voice_chime_schedule", self.schedule_select.currentData() or "hourly")
        self.config.set("voice_chime_custom_times", self.custom_edit.text().strip())
        self.config.set("voice_chime_voice", clean_voice(self.voice_select.currentData() or DEFAULT_VOICE))
        self.config.set("voice_chime_rate", self.rate_spin.value())
        self.config.set("voice_chime_pitch", self.pitch_spin.value())
        self.config.set("voice_chime_volume", self.volume_spin.value())
        self.config.set("voice_chime_show_bubble", self.bubble_check.isChecked())
        self.config.set("voice_chime_show_quote", self.quote_check.isChecked())
        self.config.set("voice_chime_custom_quotes_zh", self.custom_zh_edit.toPlainText().strip())
        self.config.set("voice_chime_custom_quotes_en", self.custom_en_edit.toPlainText().strip())
        self.settings_saved.emit()

    def refresh_from_config(self) -> None:
        """用当前配置刷新控件（外部取消保存后回滚用）。"""
        self.enabled_check.setChecked(bool(self.config.get("voice_chime_enabled", True)))
        self.schedule_select.setCurrentData(clean_schedule(self.config.get("voice_chime_schedule", "hourly")))
        self.custom_edit.setText(str(self.config.get("voice_chime_custom_times", "") or ""))
        _sync_voice_select(self.voice_select, self.config)
        self.rate_spin.setValue(clean_rate(self.config.get("voice_chime_rate", DEFAULT_RATE)))
        self.pitch_spin.setValue(clean_pitch(self.config.get("voice_chime_pitch", DEFAULT_PITCH)))
        self.volume_spin.setValue(clean_volume(self.config.get("voice_chime_volume", DEFAULT_VOLUME)))
        self.bubble_check.setChecked(bool(self.config.get("voice_chime_show_bubble", DEFAULT_SHOW_BUBBLE)))
        self.quote_check.setChecked(bool(self.config.get("voice_chime_show_quote", DEFAULT_SHOW_QUOTE)))
        self.custom_zh_edit.setPlainText(str(self.config.get("voice_chime_custom_quotes_zh", "") or ""))
        self.custom_en_edit.setPlainText(str(self.config.get("voice_chime_custom_quotes_en", "") or ""))
        self._refresh_custom_enabled()
