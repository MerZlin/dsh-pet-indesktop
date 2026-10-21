# -*- coding: utf-8 -*-
"""节日提醒设置页（现代设置对话框侧栏页）。

配置键（config.py 顶层平铺键，共 13 个）：
    festival_reminder_enabled / festival_reminder_cn /
    festival_reminder_solar_terms / festival_reminder_west /
    festival_reminder_mode / festival_reminder_count /
    festival_reminder_times / festival_reminder_show_quote /
    festival_reminder_speak / festival_reminder_animation /
    festival_birthday / festival_custom_quotes_cn /
    festival_custom_quotes_west

语音播报（``festival_reminder_speak``）复用语音报时服务的音频通道，不新增音色/
语速/音调/音量键；同一分钟两者都到点时由报时让位（见 ``pet/festival_service.py``
的 ``should_speak_at``）。

**呈现整理（2026-10-13）**：常用项平铺在最上面，按心智分三段——何时提醒
（方式/次数/时间）、提醒哪些日子（中国节日/24 节气/西方节日）、怎么提醒（语音/
节日动画）；生日单列一组（它是个人日期）；少用项（自定义中文/西文文案、立即试听）
收进一个折叠头行（默认收起，可展开）。折叠的编排在 ``pet/festival_settings_panel``，
它只碰可见性与行序——配置键、读写、调度语义、动画/音效链路一行没动。

界面上**不出现**配置键名与实现细节，那些留在本文件、``pet/festival.py`` 与
``pet/festival_service.py`` 的注释里。例如：语音播报走的是报时服务的同一条音频
通道（``AppShell.ensure_audio_channel()``，底层是 ``edge-tts`` 合成、失败自动
回退）；节日动画的素材名形如「中秋赏月吃月饼」「端蛋糕送礼物」，当前角色素材池里
没有对应动作时静默跳过、只出气泡；「附带文案」的引文来自内置古诗词/名言库，
西文库只收录公有领域作品（莎士比亚、狄更斯、KJV 圣经等），影视/游戏台词等受
版权保护的内容由用户自行添加。

**从界面收起来的调度细节**（改调度时看这里）：次数模式的 1–6 次由
``festival.py`` 均匀铺在 09:00–21:00；时间模式按用户填写的 ``HH:MM``（逗号分隔，
非法值由 ``clean_custom_times`` 丢弃）、留空回落 09:00；生日（``festival_birthday``）
不受三个类别开关约束，但同样需要总开关开启；「立即试听」只演示一次、不写调度状态。

**生日是个人信息**：界面只提供输入框与一句说明（``festival_birthday``，``MM-DD``；
空 = 不提醒；2 月 29 日仅在闰年提醒）。仓库、测试与文档里**不得**出现任何真实
个人日期，测试一律用合成值。

风格对齐 pet/voice_chime_settings.py：
自含 QWidget 页，提供 apply_to_config（与 ``refresh_backend_rows`` 供对话框在
域构建后回调），由 modern_settings_dialog.py 在「语音」域注册并参与
``_write_config`` 保存。

**本页刻意不提供「试听/立即提醒」的宿主信号接线**：立即提醒已由右键菜单「今日
节日」承担（本页按钮向上解析窗口的 ``on_festival_now`` 回调），而
modern_settings_dialog.py 的行数预算只剩个位数余量（见该文件顶部预算注释），
再挂一个信号连接与处理方法会直接顶爆预算。

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

from pet.festival import (
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
from .settings_panel import (
    EXPAND_TEXT,
    FOLD_HINT,
    FOLD_KEY,
    FOLD_TITLE,
    FestivalFoldPanel,
    notice_row,
)
from ..modern_settings_dialog import (
    BrowserSpinBox,
    SettingRow,
    ToggleSwitch,
)
from ..settings_widgets import ModernSelect
from ..voice_chime import clean_flag


class FestivalSettingsPage(QWidget):
    """自含节日提醒设置页（常用项平铺，少用项折叠）。"""

    #: 用户点击「立即试听」时发出（无载荷）。回调由本页自行向上解析并调用；
    #: 保留信号是为了让外部（测试/宿主）也能观察到试听动作，与语音报时页对称。
    preview_requested = Signal()

    def __init__(self, config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config

        def flag(key: str, default: bool) -> bool:
            return clean_flag(self.config.get(key, default), default)

        # ---- 行的临时容器 ----
        # 本页不自己排版（行会被「语音」域搬进平卡），但**行从构造起就必须有父对象**：
        # 无父的 SettingRow 一旦被 setVisible 就是一个顶层窗口，用户会看到无标题小窗
        # 一闪（上游 #261 修的就是这个）。所以先收养进这个不排版的容器，域装配时
        # 再由 build_domain_page 把它们 reparent 进域卡片。
        self._holding = QWidget(self)
        self._holding.setObjectName("festivalRowsHolding")
        self._holding_layout = QVBoxLayout(self._holding)
        self._holding_layout.setContentsMargins(0, 0, 0, 0)
        self._holding.hide()

        # ---- 控件 ----
        # 顺序＝呈现顺序（域构建期平卡按这个次序排；折叠编排再按同一顺序摆正）
        self.enabled_check = ToggleSwitch(self)
        self.enabled_check.setChecked(flag("festival_reminder_enabled", False))

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

        self.cn_check = ToggleSwitch(self)
        self.cn_check.setChecked(flag("festival_reminder_cn", True))

        self.solar_terms_check = ToggleSwitch(self)
        self.solar_terms_check.setChecked(flag("festival_reminder_solar_terms", True))

        self.west_check = ToggleSwitch(self)
        self.west_check.setChecked(flag("festival_reminder_west", True))

        self.speak_check = ToggleSwitch(self)
        self.speak_check.setChecked(flag("festival_reminder_speak", False))

        self.animation_check = ToggleSwitch(self)
        self.animation_check.setChecked(flag("festival_reminder_animation", True))

        # 用户生日（MM-DD；空 = 不提醒）。宽容 10-24 / 10/24 / 10月24日 等写法；
        # 非法或不存在（02-30）由 clean_birthday 静默落空。
        # 界面只留输入与说明：真实日期不写进仓库（测试用合成值）。
        self.birthday_edit = QLineEdit(self)
        self.birthday_edit.setText(str(self.config.get("festival_birthday", "") or ""))
        self.birthday_edit.setPlaceholderText("如 10-24；留空 = 不提醒（2 月 29 日仅在闰年提醒）")

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

        self.preview_btn = QPushButton("立即试听", self)
        self.preview_btn.setToolTip("按当前配置立即演示一次节日提醒（无需等到节日当天）")
        self.preview_btn.clicked.connect(self._on_preview_clicked)

        # 少用/高级项的行：默认收起（坐在折叠头后面），由 _register_rows 登记
        self.custom_cn_row = SettingRow(
            "festival_custom_quotes_cn",
            "自定义中文文案",
            "每行一条；填了追加在内置库后。",
            self.custom_cn_edit,
            stacked=True,
        )
        self.custom_west_row = SettingRow(
            "festival_custom_quotes_west",
            "自定义西文文案",
            "每行一条；填了追加在内置库后。",
            self.custom_west_edit,
            stacked=True,
        )
        self.preview_row = SettingRow(
            "festival_preview",
            "立即试听",
            "马上按当前设置演示一次。",
            self.preview_btn,
        )

        # ---- 折叠编排 ----
        # 少用项（自定义文案 + 试听）收进一个折叠头行；常用项平铺。折叠只能落在行上
        # （行会被「语音」域搬进平卡，容器会散架），见 festival_settings_panel。
        self._festival_panel = FestivalFoldPanel()
        self.fold_toggle = QPushButton(EXPAND_TEXT, self)
        self.fold_row = notice_row(FOLD_KEY, FOLD_TITLE, FOLD_HINT, self.fold_toggle)
        self._festival_panel.set_header(self.fold_row, self.fold_toggle)

        # ---- 行与分组 ----
        # 行不挂在页面的容器里：它们会被「语音」域的平卡整块收走（容器会散架），
        # 所以分组标题与卡片由 festival_settings_panel.build_domain_page 建，本页只
        # 负责登记行序与文案（见该模块文档）。
        self._rows_by_group: list[tuple[str, list[SettingRow]]] = [
            (
                "何时提醒",
                [
                    SettingRow(
                        "festival_reminder_enabled",
                        "启用节日提醒",
                        "节日当天在桌宠头顶说一声。",
                        self.enabled_check,
                    ),
                    SettingRow(
                        "festival_reminder_mode",
                        "提醒方式",
                        "按次数平摊，或自己定时刻。",
                        self.mode_select,
                    ),
                    SettingRow(
                        "festival_reminder_count",
                        "每天提醒几次",
                        "每天 1–6 次，白天均匀响起。",
                        self.count_spin,
                    ),
                    SettingRow(
                        "festival_reminder_times",
                        "自定义提醒时间",
                        "逗号分隔；留空按 09:00。",
                        self.times_edit,
                    ),
                ],
            ),
            (
                "提醒哪些日子",
                [
                    SettingRow(
                        "festival_reminder_cn",
                        "中国节日",
                        "春节、端午、中秋、国庆等。",
                        self.cn_check,
                    ),
                    SettingRow(
                        "festival_reminder_solar_terms",
                        "24 节气",
                        "立春、雨水……冬至共 24 个。",
                        self.solar_terms_check,
                    ),
                    SettingRow(
                        "festival_reminder_west",
                        "西方节日",
                        "情人节、万圣节、圣诞节等。",
                        self.west_check,
                    ),
                ],
            ),
            (
                "怎么提醒",
                [
                    SettingRow(
                        "festival_reminder_speak",
                        "语音播报",
                        "念出声；音色等沿用语音报时。与报时共用一条音频通道，"
                        "同一分钟时由报时先念。免打扰期间不出声，气泡照旧。",
                        self.speak_check,
                    ),
                    SettingRow(
                        "festival_reminder_animation",
                        "节日动画",
                        "当天第一次顺带播一段节日动作。",
                        self.animation_check,
                    ),
                    SettingRow(
                        "festival_reminder_show_quote",
                        "附带文案",
                        "在节日后面附一句诗词或名言。",
                        self.quote_check,
                    ),
                ],
            ),
            (
                "你的生日",
                [
                    SettingRow(
                        "festival_birthday",
                        "我的生日",
                        "当天送上祝福；留空不提醒。",
                        self.birthday_edit,
                    ),
                ],
            ),
        ]

        self._register_rows()

        self._refresh_mode_controls()
        self._festival_panel.apply()

    # ------------------------------------------------------------ 折叠登记
    def _register_rows(self) -> None:
        """按呈现次序把行登记给折叠编排（平铺在前，折叠成员在后），并先收养它们。

        **必须先收养**：``SettingRow`` 在无父状态下被 ``setVisible`` 就是一个顶层窗口
        ——用户点设置时会看到一串无标题小窗闪一下（那正是上游 ``ffc80a6`` 修掉的
        ``#261``）。本页的行稍后会被「语音」域搬进平卡，但那是域装配阶段的事；在
        那之前它们也得挂在某个真实容器里。这里挂进本页的隐藏持有容器，域装配时
        再由 ``build_domain_page`` reparent 走。
        """
        for _title, rows in self._rows_by_group:
            for row in rows:
                self._holding_layout.addWidget(row)
                self._festival_panel.add_flat(row)
        for row in (self.custom_cn_row, self.custom_west_row, self.preview_row, self.fold_row):
            self._holding_layout.addWidget(row)
        for row in (self.custom_cn_row, self.custom_west_row, self.preview_row):
            self._festival_panel.add_folded(row)

    def build_domain_page(self, host: QWidget) -> QWidget:
        """建「语音」域里的节日提醒卡片（分组标题 + 行），交给对话框摆进域。

        为什么不走对话框侧的 ``page_content``：那一步现造容器，本页的四段分组标题
        会跟容器一起散架；页面自身不挂这些行（它们要被域平卡整块收走）。
        """
        return self._festival_panel.build_domain_page(host, self._rows_by_group)

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

    def refresh_backend_rows(self) -> None:
        """行被「语音」域搬进平卡后重新摆正行序与折叠（域构建完由对话框调用）。"""
        self._festival_panel.apply()

    # ------------------------------------------------------------ 配置读写
    def apply_to_config(self) -> None:
        """把控件值合并写回 config（仅写节日提醒 13 键）。"""
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
        self.config.set("festival_reminder_animation", self.animation_check.isChecked())
        self.config.set("festival_birthday", self.birthday_edit.text().strip())
        self.config.set("festival_custom_quotes_cn", self.custom_cn_edit.toPlainText().strip())
        self.config.set("festival_custom_quotes_west", self.custom_west_edit.toPlainText().strip())

    def refresh_from_config(self) -> None:
        """用当前配置刷新控件（外部取消保存后回滚用）。

        上游 #162 死代码清理删掉了它（上游那侧没有调用方），但本仓库的
        tests/test_festival.py 把它当作节日设置页的契约（改脏界面后回滚），故保留。
        同批被删的 ``settings_saved`` 信号在本仓库确实无人连接，未予恢复。
        """
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
        self.animation_check.setChecked(flag("festival_reminder_animation", True))
        self.birthday_edit.setText(str(self.config.get("festival_birthday", "") or ""))
        self.custom_cn_edit.setPlainText(
            str(self.config.get("festival_custom_quotes_cn", "") or "")
        )
        self.custom_west_edit.setPlainText(
            str(self.config.get("festival_custom_quotes_west", "") or "")
        )
        self._refresh_mode_controls()
