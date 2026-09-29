"""Feature-owned automatic screen strategy editor (no request execution)."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from pet.settings_widgets import BrowserDoubleSpinBox, BrowserSpinBox, ModernSelect, SettingRow, SettingsSection, ToggleSwitch


class StrategySettings(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._build_proactive_controls()
        self.content = self._proactive_page_content()
        self.rows = self.content.findChildren(SettingRow)

    def save(self):
        from ..common.policy import PRESET_DEFAULTS

        pro_data = {}
        preset = self.pro_preset_select.currentData()
        # 非 custom 预设下改了数值 → 自动落为 custom，否则运行时会被预设覆盖（gemini 审查发现）
        if preset in PRESET_DEFAULTS:
            pv = PRESET_DEFAULTS[preset]
            if (
                self.pro_dwell_spin.value() != pv["dwell_seconds"]
                or abs(self._pro_cooldown_minutes() - pv["cooldown_minutes"]) > 1e-6
                or self.pro_cap_spin.value() != pv["daily_cap"]
            ):
                preset = "custom"
        pro_data.update(
            {
                "enabled": self.pro_enabled_check.isChecked(),
                "dry_run": self.pro_dryrun_check.isChecked(),
                "preset": preset,
                "dwell_seconds": self.pro_dwell_spin.value(),
                "cooldown_minutes": self._pro_cooldown_minutes(),
                "min_request_interval_seconds": self.pro_min_interval_spin.value(),
                "daily_cap": self.pro_cap_spin.value(),
                "require_idle": self.pro_idle_check.isChecked(),
                "min_idle_seconds": self.pro_idle_spin.value(),
                "allow_when_mouse_through": self.pro_through_check.isChecked(),
                "pre_cue": self.pro_precue_check.isChecked(),
                "whitelist": [x.strip() for x in self.pro_whitelist_edit.toPlainText().splitlines() if x.strip()],
            }
        )
        self.context.preferences.stage(pro_data)

    def _build_proactive_controls(self) -> None:
        """主动识屏页控件（仅 Windows 挂载，不依赖聊天能力）。"""
        from ..common.policy import effective_proactive_config

        pro = effective_proactive_config(self.context.preferences.read())

        self.pro_enabled_check = ToggleSwitch(self)
        self.pro_enabled_check.setChecked(bool(pro["enabled"]))
        self.pro_dryrun_check = ToggleSwitch(self)
        self.pro_dryrun_check.setChecked(bool(pro["dry_run"]))

        self.pro_preset_select = ModernSelect(self, width=160)
        for key, label in (
            ("balanced", "平衡（推荐）"),
            ("quiet", "安静"),
            ("active", "活跃"),
            ("custom", "自定义参数"),
        ):
            self.pro_preset_select.addItem(label, key)
        idx = {"quiet": 1, "balanced": 0, "active": 2, "custom": 3}.get(pro["preset"], 0)
        self.pro_preset_select.setCurrentIndex(idx)
        self.pro_preset_select.currentIndexChanged.connect(self._on_pro_preset_changed)

        self.pro_dwell_spin = BrowserSpinBox(self)
        self.pro_dwell_spin.setRange(15, 600)
        self.pro_dwell_spin.setValue(int(pro["dwell_seconds"]))

        self.pro_cooldown_spin = BrowserDoubleSpinBox(self)
        self.pro_cooldown_spin.setRange(0.5, 7200)
        self.pro_cooldown_spin.setDecimals(2)
        self.pro_cooldown_unit = ModernSelect(self, width=80)
        self.pro_cooldown_unit.addItem("分钟", "min")
        self.pro_cooldown_unit.addItem("秒", "sec")
        self._pro_set_cooldown_display(float(pro["cooldown_minutes"]))
        self.pro_cooldown_unit.currentIndexChanged.connect(self._on_pro_cooldown_unit_changed)

        self.pro_min_interval_spin = BrowserSpinBox(self)
        self.pro_min_interval_spin.setRange(30, 3600)
        self.pro_min_interval_spin.setValue(int(pro["min_request_interval_seconds"]))

        self.pro_cap_spin = BrowserSpinBox(self)
        self.pro_cap_spin.setRange(1, 9999)
        self.pro_cap_spin.setValue(int(pro["daily_cap"]))

        self.pro_idle_check = ToggleSwitch(self)
        self.pro_idle_check.setChecked(bool(pro["require_idle"]))
        self.pro_idle_spin = BrowserSpinBox(self)
        self.pro_idle_spin.setRange(5, 3600)
        raw_idle = (self.context.preferences.read() or {}).get("min_idle_seconds", 30)
        self.pro_idle_spin.setValue(int(raw_idle or 30))

        self.pro_through_check = ToggleSwitch(self)
        self.pro_through_check.setChecked(bool(pro["allow_when_mouse_through"]))
        self.pro_precue_check = ToggleSwitch(self)
        self.pro_precue_check.setChecked(bool(pro["pre_cue"]))

        self.pro_whitelist_edit = QPlainTextEdit(self)
        self.pro_whitelist_edit.setPlaceholderText("msedge.exe\ntitle:*会议*")
        self.pro_whitelist_edit.setPlainText("\n".join(str(x) for x in pro["whitelist"]))
        self.pro_whitelist_edit.setMinimumHeight(72)

        self.pro_add_btn = QPushButton("从当前前台窗口添加…", self)
        self.pro_add_btn.setProperty("variant", "ghost")
        self.pro_add_btn.clicked.connect(self._on_pro_add_foreground)
        self._pro_add_timer = QTimer(self)
        self._pro_add_timer.setSingleShot(True)
        self._pro_add_timer.timeout.connect(self._do_pro_add_foreground)

        self.pro_clear_mem_btn = QPushButton("清除陪伴记忆", self)
        self.pro_clear_mem_btn.setProperty("variant", "ghost")
        self.pro_clear_mem_btn.clicked.connect(self._on_pro_clear_memory)

    def _pro_set_cooldown_display(self, minutes: float) -> None:
        unit = "sec" if minutes < 1 else "min"
        self._pro_apply_cooldown_unit(unit, minutes)

    def _pro_apply_cooldown_unit(self, unit: str, minutes: float) -> None:
        self.pro_cooldown_unit.blockSignals(True)
        self.pro_cooldown_unit.setCurrentIndex(1 if unit == "sec" else 0)
        if unit == "sec":
            self.pro_cooldown_spin.setRange(30, 7200)
            self.pro_cooldown_spin.setDecimals(0)
            self.pro_cooldown_spin.setValue(min(7200, max(30, round(minutes * 60))))
        else:
            self.pro_cooldown_spin.setRange(0.5, 120)
            self.pro_cooldown_spin.setDecimals(2)
            self.pro_cooldown_spin.setValue(min(120.0, max(0.5, minutes)))
        self._pro_cooldown_last_unit = unit
        self.pro_cooldown_unit.blockSignals(False)

    def _on_pro_cooldown_unit_changed(self) -> None:
        old = getattr(self, "_pro_cooldown_last_unit", "min")
        v = float(self.pro_cooldown_spin.value())
        minutes = v / 60.0 if old == "sec" else v
        self._pro_apply_cooldown_unit(self.pro_cooldown_unit.currentData(), minutes)

    def _pro_cooldown_minutes(self) -> float:
        v = float(self.pro_cooldown_spin.value())
        return v / 60.0 if self.pro_cooldown_unit.currentData() == "sec" else v

    def _on_pro_preset_changed(self, _index: int) -> None:
        from ..common.policy import PRESET_DEFAULTS

        vals = PRESET_DEFAULTS.get(self.pro_preset_select.currentData())
        if vals:
            self.pro_dwell_spin.setValue(vals["dwell_seconds"])
            self._pro_set_cooldown_display(float(vals["cooldown_minutes"]))
            self.pro_cap_spin.setValue(vals["daily_cap"])

    def _on_pro_add_foreground(self) -> None:
        self.pro_add_btn.setEnabled(False)
        self.pro_add_btn.setText("请在 3 秒内切换到目标窗口…")
        self._pro_add_timer.start(3000)

    def _do_pro_add_foreground(self) -> None:
        self.pro_add_btn.setEnabled(True)
        self.pro_add_btn.setText("从当前前台窗口添加…")
        result = self.context.desktop.foreground_window()
        if result.status != "ok" or result.value is None:
            QMessageBox.information(self, "添加前台窗口", "未能检测到有效的前台窗口，请将目标软件置顶后再试。")
            return
        proc = result.value.process_name.strip()
        title = result.value.title.strip()
        box = QMessageBox(self)
        box.setWindowTitle("添加到白名单")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"检测到前台窗口：\n进程：{proc or '（未知）'}\n标题：{title or '（空）'}\n\n要按哪种方式关注它？")
        btn_proc = box.addButton("按软件（推荐）", QMessageBox.ButtonRole.AcceptRole)
        btn_title = box.addButton("按标题关键词", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        lines = [x.strip() for x in self.pro_whitelist_edit.toPlainText().splitlines() if x.strip()]
        if box.clickedButton() is btn_proc and proc and proc not in lines:
            lines.append(proc)
        elif box.clickedButton() is btn_title and title:
            rule = f"title:*{title}*"
            if rule not in lines:
                lines.append(rule)
        else:
            return
        self.pro_whitelist_edit.setPlainText("\n".join(lines))

    def _on_pro_clear_memory(self) -> None:
        try:
            self.context.memory.clear()
        except OSError:
            pass  # Preserve the original memory-clear error policy.
        QMessageBox.information(self, "陪伴记忆", "已清空主动识屏的短期陪伴记忆。")

    def _proactive_page_content(self) -> QWidget:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(
            SettingsSection(
                "总开关与节奏",
                [
                    SettingRow(
                        "proactive_enabled",
                        "开启主动识屏",
                        "她会偶尔看一眼你在用的软件并说句话。截图只在内存处理、不落盘、不写入会话。",
                        self.pro_enabled_check,
                    ),
                    SettingRow("proactive_dry_run", "dry-run 验证模式", "开启后满足条件只写日志、不调用模型、不消耗额度。", self.pro_dryrun_check),
                    SettingRow(
                        "proactive_preset",
                        "陪伴节奏预设",
                        "平衡 45s/5min/15次；安静 90s/10min/8次；活跃 20s/3min/25次（停留/冷却/每日上限）。",
                        self.pro_preset_select,
                    ),
                ],
                content,
            )
        )
        layout.addWidget(
            SettingsSection(
                "频率参数（自定义预设时生效）",
                [
                    SettingRow("proactive_dwell", "窗口停留门限（秒）", "同一前台窗口持续停留该时长才可能触发。", self.pro_dwell_spin),
                    SettingRow("proactive_cooldown", "关怀冷却间隔", "两次关怀的最短间隔，支持秒/分钟。", self._pro_cooldown_row()),
                    SettingRow("proactive_min_interval", "最小请求间隔（秒）", "免费模型的硬保护，不建议调太小。", self.pro_min_interval_spin),
                    SettingRow("proactive_daily_cap", "每日请求上限", "DeepSeek 视觉单次约 ¥0.003；上限 9999 约等于不限。", self.pro_cap_spin),
                ],
                content,
            )
        )
        layout.addWidget(
            SettingsSection(
                "触发条件",
                [
                    SettingRow("proactive_require_idle", "仅当我闲置时触发", "勾选后，敲键盘/动鼠标时不打扰。", self.pro_idle_check),
                    SettingRow("proactive_idle_seconds", "闲置判定秒数", "勾选上方后，闲置该秒数才触发。", self.pro_idle_spin),
                    SettingRow("proactive_through", "鼠标穿透时仍识屏", "桌宠处于鼠标穿透状态时是否继续工作。", self.pro_through_check),
                    SettingRow("proactive_pre_cue", "触发前先兆提示", "触发前先冒一句「让我看看……」。", self.pro_precue_check),
                ],
                content,
            )
        )
        layout.addWidget(
            SettingsSection(
                "白名单",
                [
                    SettingRow(
                        "proactive_whitelist",
                        "白名单（每行一条）",
                        "进程名（如 msedge.exe）= 关注这个软件；title:关键词 = 只关注标题含该词的窗口。留空 = 不识屏。",
                        self.pro_whitelist_edit,
                        stacked=True,
                    ),
                    SettingRow("proactive_whitelist_add", "快捷添加", "点击后 3 秒内切换到目标窗口，自动采样进程名/标题。", self.pro_add_btn),
                    SettingRow("proactive_memory_clear", "陪伴记忆", "只存进程名和活动分类（不落标题、不存截图），可随时清空。", self.pro_clear_mem_btn),
                ],
                content,
            )
        )
        layout.addStretch(1)
        return content

    def _pro_cooldown_row(self) -> QWidget:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(self.pro_cooldown_spin)
        h.addWidget(self.pro_cooldown_unit)
        return row
