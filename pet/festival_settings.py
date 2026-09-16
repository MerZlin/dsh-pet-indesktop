# -*- coding: utf-8 -*-
"""节日提醒设置页（现代设置对话框侧栏页）。

配置键（config.py 顶层平铺键，共 11 个）：
    festival_reminder_enabled / festival_reminder_cn /
    festival_reminder_solar_terms / festival_reminder_west /
    festival_reminder_mode / festival_reminder_count /
    festival_reminder_times / festival_reminder_show_quote /
    festival_reminder_speak / festival_custom_quotes_cn /
    festival_custom_quotes_west

语音播报（``festival_reminder_speak``）复用语音报时服务的音频通道，不新增音色/
语速/音调/音量键；同一分钟两者都到点时由报时让位（见 festival_service）。

风格对齐 pet/voice_chime_settings.py 与 pet/exploration_watchdog_settings.py：
自含 QWidget 页，提供 apply_to_config / refresh_from_config 与 settings_saved
信号，由 modern_settings_dialog.py 在 automation 域注册并参与 _write_config 保存。

**本页刻意不提供「试听/立即提醒」按钮**：立即提醒已由右键菜单「今日节日」
承担，而 modern_settings_dialog.py 的行数预算只剩个位数余量（见该文件顶部
预算注释），再挂一个信号回调会直接顶爆预算。

关于导入方向：本模块顶层 ``from .modern_settings_dialog import ...`` 看似
循环导入，实际安全——modern_settings_dialog 在 ``__init__`` 内**函数级**
import 本模块，模块级依赖是单向的（与 voice_chime_settings 同构）。
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

from .festival import (
    DEFAULT_COUNT,
    DEFAULT_MODE,
    DEFAULT_SHOW_QUOTE,
    DEFAULT_TIMES,
    MAX_COUNT,
    MIN_COUNT,
    MODE_KEYS,
    MODE_LABELS,
    clean_count,
    clean_mode,
)
from .modern_settings_dialog import (
    BrowserSpinBox,
    SettingRow,
    SettingsSection,
    ToggleSwitch,
)
from .settings_widgets import ModernSelect
from .voice_chime import clean_flag


class FestivalSettingsPage(QWidget):
    """自含节日提醒设置页。"""

    settings_saved = Signal()
    #: 用户点击「立即试听」时发出（无载荷）。回调由本页自行向上解析并调用；
    #: 保留信号是为了让外部（测试/宿主）也能观察到试听动作，与语音报时页对称。
    preview_requested = Signal()

    def __init__(self, config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config

        def flag(key: str, default: bool) -> bool:
            return clean_flag(self.config.get(key, default), default)

        # ---- 基础设置 ----
        self.enabled_check = ToggleSwitch(self)
        self.enabled_check.setChecked(flag("festival_reminder_enabled", False))

        self.cn_check = ToggleSwitch(self)
        self.cn_check.setChecked(flag("festival_reminder_cn", True))

        self.solar_terms_check = ToggleSwitch(self)
        self.solar_terms_check.setChecked(flag("festival_reminder_solar_terms", True))

        self.west_check = ToggleSwitch(self)
        self.west_check.setChecked(flag("festival_reminder_west", True))

        self.speak_check = ToggleSwitch(self)
        self.speak_check.setChecked(flag("festival_reminder_speak", False))

        # ---- 提醒时间 ----
        self.mode_select = ModernSelect(self, width=170)
        for key in MODE_KEYS:
            self.mode_select.addItem(MODE_LABELS[key], key)
        self.mode_select.setCurrentData(
            clean_mode(self.config.get("festival_reminder_mode", DEFAULT_MODE))
        )
        self.mode_select.currentIndexChanged.connect(self._refresh_mode_controls)

        self.count_spin = BrowserSpinBox(self)
        self.count_spin.setRange(MIN_COUNT, MAX_COUNT)
        self.count_spin.setValue(
            clean_count(self.config.get("festival_reminder_count", DEFAULT_COUNT))
        )
        self.count_spin.setToolTip("把提醒次数均匀铺在 09:00–21:00 之间")

        self.times_edit = QLineEdit(self)
        self.times_edit.setText(str(self.config.get("festival_reminder_times", DEFAULT_TIMES) or ""))
        self.times_edit.setPlaceholderText("如 09:00, 12:30, 20:00（逗号分隔）")

        # ---- 文案 ----
        self.quote_check = ToggleSwitch(self)
        self.quote_check.setChecked(flag("festival_reminder_show_quote", DEFAULT_SHOW_QUOTE))

        self.custom_cn_edit = QPlainTextEdit(self)
        self.custom_cn_edit.setMinimumSize(280, 84)
        self.custom_cn_edit.setMaximumHeight(180)
        self.custom_cn_edit.setPlaceholderText(
            "每行一条，追加到内置中文文案库（古诗词/名言），例如：\n"
            "今夜月明人尽望，不知秋思落谁家。"
        )
        self.custom_cn_edit.setPlainText(
            str(self.config.get("festival_custom_quotes_cn", "") or "")
        )

        self.custom_west_edit = QPlainTextEdit(self)
        self.custom_west_edit.setMinimumSize(280, 84)
        self.custom_west_edit.setMaximumHeight(180)
        self.custom_west_edit.setPlaceholderText(
            "One quote per line, appended to the built-in Western library.\n"
            "内置库只收录公有领域作品；其余（如影视/游戏台词）请自行在此添加。"
        )
        self.custom_west_edit.setPlainText(
            str(self.config.get("festival_custom_quotes_west", "") or "")
        )

        # ---- 试听 ----
        self.preview_btn = QPushButton("立即试听", self)
        self.preview_btn.setToolTip("按当前配置立即演示一次节日提醒（无需等到节日当天）")
        self.preview_btn.clicked.connect(self._on_preview_clicked)

        # ---- Layout ----
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        root.addWidget(
            SettingsSection(
                "基础设置",
                [
                    SettingRow(
                        "festival_reminder_enabled",
                        "启用节日提醒",
                        "命中节日/节气当天，按下方时间用桌宠气泡告知今天是什么日子，"
                        "并附一句与节日氛围匹配的文案。默认关闭。",
                        self.enabled_check,
                    ),
                    SettingRow(
                        "festival_reminder_cn",
                        "中国节日",
                        "春节、元宵、端午、七夕、中元、中秋、重阳、腊八、除夕、"
                        "元旦、劳动节、儿童节、国庆节等（含清明节）。",
                        self.cn_check,
                    ),
                    SettingRow(
                        "festival_reminder_solar_terms",
                        "24 节气",
                        "立春、雨水、惊蛰……冬至共 24 个节气，节气当天同样提醒。",
                        self.solar_terms_check,
                    ),
                    SettingRow(
                        "festival_reminder_west",
                        "西方节日",
                        "情人节、愚人节、复活节、母亲节、父亲节、万圣节、"
                        "平安夜、圣诞节。内置文案只取公有领域作品。",
                        self.west_check,
                    ),
                    SettingRow(
                        "festival_reminder_speak",
                        "语音播报",
                        "用语音念出节日提醒（音色/语速/音调/音量沿用「语音报时」的设置）。"
                        "与语音报时共用同一条音频通道，因此不会叠音；两者恰好同一分钟时"
                        "由报时让位。需要 edge-tts，缺失时只出气泡不出声。",
                        self.speak_check,
                    ),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "提醒时间",
                [
                    SettingRow(
                        "festival_reminder_mode",
                        "提醒方式",
                        "「按提醒次数」把下面的次数均匀铺在 09:00–21:00；"
                        "「自定义提醒时间」则完全按你填写的时刻提醒。",
                        self.mode_select,
                    ),
                    SettingRow(
                        "festival_reminder_count",
                        "提醒次数",
                        "每天提醒几次（1–6）。仅在「按提醒次数」模式生效。",
                        self.count_spin,
                    ),
                    SettingRow(
                        "festival_reminder_times",
                        "自定义提醒时间",
                        "仅在「自定义提醒时间」模式生效；HH:MM 逗号分隔，"
                        "如 09:00, 12:30, 20:00。留空则回落 09:00。",
                        self.times_edit,
                    ),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "文案",
                [
                    SettingRow(
                        "festival_reminder_show_quote",
                        "附带文案",
                        "提醒时在节日名称后附一句诗词/名言/经典引文（与节日氛围匹配）。"
                        "关闭后只报节日名称。",
                        self.quote_check,
                    ),
                    SettingRow(
                        "festival_custom_quotes_cn",
                        "自定义中文文案",
                        "每行一条，**追加**到内置中文库之后参与轮换；"
                        "留空则只用内置古诗词/名言库。",
                        self.custom_cn_edit,
                        stacked=True,
                    ),
                    SettingRow(
                        "festival_custom_quotes_west",
                        "自定义西文文案",
                        "每行一条，**追加**到内置西文库之后参与轮换。"
                        "内置库只收录公有领域作品（莎士比亚、狄更斯、KJV 圣经等），"
                        "影视/游戏台词等受版权保护的内容请自行在此添加。",
                        self.custom_west_edit,
                        stacked=True,
                    ),
                    SettingRow(
                        "festival_preview",
                        "立即试听",
                        "按当前配置立即演示一次，无需等到节日当天；"
                        "开启「语音播报」时会一并念出来，否则只显示气泡。"
                        "当天没有节日/节气时会直接说明。",
                        self.preview_btn,
                    ),
                ],
                self,
            )
        )

        self._refresh_mode_controls()

    # ------------------------------------------------------------ 交互
    def _on_preview_clicked(self) -> None:
        """试听：先落盘当前控件值，再透传给窗口的「今日节日」入口。

        **先 apply_to_config 再触发**：与语音报时试听同约定——服务端按最新配置
        组装文案与语音参数，否则试听的是上一次保存的设置。
        """
        self.apply_to_config()
        callback = self._resolve_preview_callback()
        if callback is not None:
            callback()
        self.preview_requested.emit()

    def _resolve_preview_callback(self):
        """向上查找窗口暴露的 ``on_festival_now`` 回调（找到即返回，找不到返回 None）。

        为什么不走「页面发信号 → modern_settings_dialog 处理」那条路（语音报时用的
        是那条）：该对话框的行数预算只剩个位数余量，再挂一个信号连接与处理方法会
        直接顶爆预算。放在本页则**零成本**（本文件不受行数预算约束）。

        用**有界向上遍历**而不是写死 ``parent().parent()``：SettingsRow 会被
        ``_rebuild_domain_navigation`` 重新 parent 到共享卡片域，写死层数将来会被
        一次无关的重构悄悄打断。找不到时静默忽略（仅设置界面，无副作用）。
        """
        widget = self.parentWidget()
        for _ in range(4):  # 页面 -> 对话框 -> 窗口/壳，留余量即可，不做无限上溯
            if widget is None:
                return None
            callback = getattr(widget, "on_festival_now", None)
            if callable(callback):
                return callback
            widget = widget.parentWidget()
        return None

    def _refresh_mode_controls(self) -> None:
        """按提醒方式启用/禁用「次数」与「自定义时间」控件，避免改了没生效的困惑。"""
        is_custom = self.mode_select.currentData() == "custom"
        self.count_spin.setEnabled(not is_custom)
        self.times_edit.setEnabled(is_custom)

    # ------------------------------------------------------------ 配置读写
    def apply_to_config(self) -> None:
        """把控件值合并写回 config（仅写节日提醒 10 键）。"""
        if self.config is None:
            return
        self.config.set("festival_reminder_enabled", self.enabled_check.isChecked())
        self.config.set("festival_reminder_cn", self.cn_check.isChecked())
        self.config.set("festival_reminder_solar_terms", self.solar_terms_check.isChecked())
        self.config.set("festival_reminder_west", self.west_check.isChecked())
        self.config.set("festival_reminder_mode", self.mode_select.currentData() or DEFAULT_MODE)
        self.config.set("festival_reminder_count", self.count_spin.value())
        self.config.set("festival_reminder_times", self.times_edit.text().strip())
        self.config.set("festival_reminder_show_quote", self.quote_check.isChecked())
        self.config.set("festival_reminder_speak", self.speak_check.isChecked())
        self.config.set("festival_custom_quotes_cn", self.custom_cn_edit.toPlainText().strip())
        self.config.set("festival_custom_quotes_west", self.custom_west_edit.toPlainText().strip())
        self.settings_saved.emit()

    def refresh_from_config(self) -> None:
        """用当前配置刷新控件（外部取消保存后回滚用）。"""
        def flag(key: str, default: bool) -> bool:
            return clean_flag(self.config.get(key, default), default)

        self.enabled_check.setChecked(flag("festival_reminder_enabled", False))
        self.cn_check.setChecked(flag("festival_reminder_cn", True))
        self.solar_terms_check.setChecked(flag("festival_reminder_solar_terms", True))
        self.west_check.setChecked(flag("festival_reminder_west", True))
        self.mode_select.setCurrentData(
            clean_mode(self.config.get("festival_reminder_mode", DEFAULT_MODE))
        )
        self.count_spin.setValue(
            clean_count(self.config.get("festival_reminder_count", DEFAULT_COUNT))
        )
        self.times_edit.setText(str(self.config.get("festival_reminder_times", DEFAULT_TIMES) or ""))
        self.quote_check.setChecked(flag("festival_reminder_show_quote", DEFAULT_SHOW_QUOTE))
        self.speak_check.setChecked(flag("festival_reminder_speak", False))
        self.custom_cn_edit.setPlainText(
            str(self.config.get("festival_custom_quotes_cn", "") or "")
        )
        self.custom_west_edit.setPlainText(
            str(self.config.get("festival_custom_quotes_west", "") or "")
        )
        self._refresh_mode_controls()
