# -*- coding: utf-8 -*-
"""Agent Exploration Loop Watchdog settings page.

Used by the modern settings dialog as a sidebar page.

Configuration keys live under the `agent_link` sub-dict:

    agent_link.exploration_watchdog_enabled
    agent_link.exploration_watchdog_warning_threshold
    agent_link.exploration_watchdog_control_threshold
    agent_link.exploration_watchdog_cooldown_steps
    agent_link.exploration_watchdog_early_grace_minutes
    agent_link.exploration_watchdog_long_run_minutes
    agent_link.exploration_watchdog_long_think_seconds

同时承载「卡住检测」（stuck_detector）配置：

    agent_link.stuck_detect
    agent_link.stuck_worried_threshold
    agent_link.stuck_intervene_threshold
    agent_link.stuck_window_seconds
    agent_link.stuck_cooldown_seconds
    agent_link.stuck_reminder_text

以及「行为重复检测」（behavior_detector）配置：

    agent_link.pattern_detect
    agent_link.pattern_w6_control
    agent_link.pattern_w10_warn
    agent_link.pattern_w10_control
    agent_link.pattern_macro_w6_explore
    agent_link.pattern_macro_w6_action
    agent_link.pattern_macro_w10_explore
    agent_link.pattern_macro_w10_action
    agent_link.pattern_min_steps_between
    agent_link.pattern_cooldown_seconds
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .modern_settings_dialog import (
    BrowserSpinBox,
    SettingRow,
    SettingsSection,
    ToggleSwitch,
)

log = logging.getLogger("dsh-pet-standalone")


class WatchdogSettingsPage(QWidget):
    """Self-contained settings page for Agent Exploration Loop Watchdog.

    Organised in five sections:
      1. 基础设置 (enable toggle)
      2. 风险评分 (warning / control thresholds)
      3. Think 风控 (cooldown, grace, long-run)
      4. 卡住检测 (stuck_detector)
      5. 行为重复检测 (behavior_detector)
    """

    def __init__(self, config, agent_link_cfg: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self._agent_cfg = dict(agent_link_cfg)

        # ---- 基础设置 ----
        self.enabled_check = ToggleSwitch(self)
        self.enabled_check.setChecked(bool(self._agent_cfg.get("exploration_watchdog_enabled", True)))

        # ---- 风险评分 ----
        self.warning_spin = BrowserSpinBox(self)
        self.warning_spin.setRange(1, 20)
        self.warning_spin.setSuffix(" 分")
        self.warning_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_warning_threshold", 3)))
        self.control_spin = BrowserSpinBox(self)
        self.control_spin.setRange(1, 30)
        self.control_spin.setSuffix(" 分")
        self.control_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_control_threshold", 5)))

        # ---- Think 风控 ----
        self.cooldown_spin = BrowserSpinBox(self)
        self.cooldown_spin.setRange(1, 20)
        self.cooldown_spin.setSuffix(" 步")
        self.cooldown_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_cooldown_steps", 3)))
        self.grace_spin = BrowserSpinBox(self)
        self.grace_spin.setRange(1, 30)
        self.grace_spin.setSuffix(" 分钟")
        self.grace_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_early_grace_minutes", 5)))
        self.long_run_spin = BrowserSpinBox(self)
        self.long_run_spin.setRange(2, 240)
        self.long_run_spin.setSuffix(" 分钟")
        self.long_run_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_long_run_minutes", 10)))
        self.long_think_spin = BrowserSpinBox(self)
        self.long_think_spin.setRange(10, 1800)
        self.long_think_spin.setSuffix(" 秒")
        self.long_think_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_long_think_seconds", 120)))

        # ---- 卡住检测（stuck_detector：失败评分式人工介入建议，原仅右键菜单可配）----
        self.stuck_enabled_check = ToggleSwitch(self)
        self.stuck_enabled_check.setChecked(bool(self._agent_cfg.get("stuck_detect", True)))
        self.stuck_worried_spin = BrowserSpinBox(self)
        self.stuck_worried_spin.setRange(1, 20)
        self.stuck_worried_spin.setSuffix(" 分")
        self.stuck_worried_spin.setValue(int(self._agent_cfg.get("stuck_worried_threshold", 3)))
        self.stuck_intervene_spin = BrowserSpinBox(self)
        self.stuck_intervene_spin.setRange(2, 50)
        self.stuck_intervene_spin.setSuffix(" 分")
        self.stuck_intervene_spin.setValue(int(self._agent_cfg.get("stuck_intervene_threshold", 5)))
        self.stuck_window_spin = BrowserSpinBox(self)
        self.stuck_window_spin.setRange(10, 3600)
        self.stuck_window_spin.setSuffix(" 秒")
        self.stuck_window_spin.setValue(int(self._agent_cfg.get("stuck_window_seconds", 90)))
        self.stuck_cooldown_spin = BrowserSpinBox(self)
        self.stuck_cooldown_spin.setRange(10, 7200)
        self.stuck_cooldown_spin.setSuffix(" 秒")
        self.stuck_cooldown_spin.setValue(int(self._agent_cfg.get("stuck_cooldown_seconds", 300)))
        self.stuck_reminder_edit = QLineEdit(self)
        self.stuck_reminder_edit.setText(str(self._agent_cfg.get("stuck_reminder_text", "") or ""))
        self.stuck_reminder_edit.setPlaceholderText("留空使用默认提醒文案")

        # ---- 行为重复检测（behavior_detector：双窗口慢性循环 / 短时爆发 / 纯探索无产出）----
        self.pattern_enabled_check = ToggleSwitch(self)
        self.pattern_enabled_check.setChecked(bool(self._agent_cfg.get("pattern_detect", True)))
        self.pattern_w6_control_spin = BrowserSpinBox(self)
        self.pattern_w6_control_spin.setRange(1, 20)
        self.pattern_w6_control_spin.setSuffix(" 次")
        self.pattern_w6_control_spin.setValue(int(self._agent_cfg.get("pattern_w6_control", 3)))
        self.pattern_w10_warn_spin = BrowserSpinBox(self)
        self.pattern_w10_warn_spin.setRange(1, 20)
        self.pattern_w10_warn_spin.setSuffix(" 次")
        self.pattern_w10_warn_spin.setValue(int(self._agent_cfg.get("pattern_w10_warn", 3)))
        self.pattern_w10_control_spin = BrowserSpinBox(self)
        self.pattern_w10_control_spin.setRange(1, 30)
        self.pattern_w10_control_spin.setSuffix(" 次")
        self.pattern_w10_control_spin.setValue(int(self._agent_cfg.get("pattern_w10_control", 4)))
        self.pattern_macro_w6_explore_spin = BrowserSpinBox(self)
        self.pattern_macro_w6_explore_spin.setRange(1, 50)
        self.pattern_macro_w6_explore_spin.setSuffix(" 步")
        self.pattern_macro_w6_explore_spin.setValue(int(self._agent_cfg.get("pattern_macro_w6_explore", 5)))
        self.pattern_macro_w6_action_spin = BrowserSpinBox(self)
        self.pattern_macro_w6_action_spin.setRange(0, 50)
        self.pattern_macro_w6_action_spin.setSuffix(" 步")
        self.pattern_macro_w6_action_spin.setValue(int(self._agent_cfg.get("pattern_macro_w6_action", 0)))
        self.pattern_macro_w10_explore_spin = BrowserSpinBox(self)
        self.pattern_macro_w10_explore_spin.setRange(1, 50)
        self.pattern_macro_w10_explore_spin.setSuffix(" 步")
        self.pattern_macro_w10_explore_spin.setValue(int(self._agent_cfg.get("pattern_macro_w10_explore", 7)))
        self.pattern_macro_w10_action_spin = BrowserSpinBox(self)
        self.pattern_macro_w10_action_spin.setRange(0, 50)
        self.pattern_macro_w10_action_spin.setSuffix(" 步")
        self.pattern_macro_w10_action_spin.setValue(int(self._agent_cfg.get("pattern_macro_w10_action", 1)))
        self.pattern_min_steps_between_spin = BrowserSpinBox(self)
        self.pattern_min_steps_between_spin.setRange(1, 50)
        self.pattern_min_steps_between_spin.setSuffix(" 步")
        self.pattern_min_steps_between_spin.setValue(int(self._agent_cfg.get("pattern_min_steps_between", 3)))
        self.pattern_cooldown_seconds_spin = BrowserSpinBox(self)
        self.pattern_cooldown_seconds_spin.setRange(5, 3600)
        self.pattern_cooldown_seconds_spin.setSuffix(" 秒")
        self.pattern_cooldown_seconds_spin.setValue(int(self._agent_cfg.get("pattern_cooldown_seconds", 60)))

        # ---- Layout ----
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        root.addWidget(
            SettingsSection(
                "基础设置",
                [
                    SettingRow("watchdog_enabled", "启用循环检测", "识别重复的 Search/Read/Think 行为，防止 Agent 陷入无限探索循环。", self.enabled_check),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "风险评分",
                [
                    SettingRow("warning_threshold", "Warning 阈值", "风险分数达到此值时发出警告提醒。", self.warning_spin),
                    SettingRow("control_threshold", "Control 阈值", "风险分数达到此值时发出高级别警告提醒。", self.control_spin),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "Think 风控",
                [
                    SettingRow("cooldown_steps", "评估冷却步数", "两次风险检测之间的最小步数间隔，防止高频误报。", self.cooldown_spin),
                    SettingRow("early_grace_minutes", "启动宽限期", "Agent 启动后的前 N 分钟提高风险阈值，允许更多探索空间。", self.grace_spin),
                    SettingRow("long_run_minutes", "长运行降阈值", "连续运行超过 N 分钟后，风险阈值自动降低 1，提高敏感度。", self.long_run_spin),
                    SettingRow("long_think_seconds", "单次超长 Think", "单次思考超过该时长（秒）后直接提醒（不调整其他阈值）。", self.long_think_spin),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "卡住检测",
                [
                    SettingRow("stuck_detect", "启用卡住检测", "DSH 联动时识别工具失败/超时/反复重试等钻牛角尖行为，建议人工介入。", self.stuck_enabled_check),
                    SettingRow("stuck_worried_threshold", "担忧动画阈值", "卡住评分达到该分值时播放担忧动画。", self.stuck_worried_spin),
                    SettingRow("stuck_intervene_threshold", "介入提醒阈值", "卡住评分达到该分值时持续提醒人工介入。", self.stuck_intervene_spin),
                    SettingRow("stuck_window_seconds", "滑动窗口", "卡住评分统计的时间窗口。", self.stuck_window_spin),
                    SettingRow("stuck_cooldown_seconds", "提醒冷却", "两次介入提醒之间的最小间隔。", self.stuck_cooldown_spin),
                    SettingRow("stuck_reminder_text", "自定义提醒文案", "留空使用默认文案。", self.stuck_reminder_edit, stacked=True),
                ],
                self,
            )
        )

        root.addWidget(
            SettingsSection(
                "行为重复检测",
                [
                    SettingRow(
                        "pattern_detect", "启用行为重复检测", "DSH 联动时用 W6/W10 双窗口识别慢性循环、短时爆发与纯探索无产出。", self.pattern_enabled_check
                    ),
                    SettingRow("pattern_w6_control", "W6 同类重复（控制）", "短窗口内同一行为类别重复达到该次数即控制级提醒。", self.pattern_w6_control_spin),
                    SettingRow("pattern_w10_warn", "W10 同类重复（警告）", "长窗口内同一行为类别重复达到该次数即警告。", self.pattern_w10_warn_spin),
                    SettingRow(
                        "pattern_w10_control", "W10 同类重复（控制）", "长窗口内同一行为类别重复达到该次数即控制级提醒。", self.pattern_w10_control_spin
                    ),
                    SettingRow(
                        "pattern_macro_w6_explore",
                        "W6 大类探索步数",
                        "短窗口内探索类步骤数达到该值时参与「纯探索无产出」判定。",
                        self.pattern_macro_w6_explore_spin,
                    ),
                    SettingRow(
                        "pattern_macro_w6_action",
                        "W6 大类行动步数上限",
                        "短窗口内行动（编辑/运行/测试）步骤数不超过该值时才算无产出。",
                        self.pattern_macro_w6_action_spin,
                    ),
                    SettingRow(
                        "pattern_macro_w10_explore",
                        "W10 大类探索步数",
                        "长窗口内探索类步骤数达到该值时参与「纯探索无产出」判定。",
                        self.pattern_macro_w10_explore_spin,
                    ),
                    SettingRow(
                        "pattern_macro_w10_action", "W10 大类行动步数上限", "长窗口内行动步骤数不超过该值时才算无产出。", self.pattern_macro_w10_action_spin
                    ),
                    SettingRow(
                        "pattern_min_steps_between", "最小触发步数间隔", "两次行为重复提醒之间至少新增的 step 数。", self.pattern_min_steps_between_spin
                    ),
                    SettingRow("pattern_cooldown_seconds", "提醒冷却", "两次行为重复提醒之间的最小间隔。", self.pattern_cooldown_seconds_spin),
                ],
                self,
            )
        )

        root.addStretch(1)

    def apply_to_config(self, agent_link_cfg: dict) -> dict:
        """Merge current values into the agent_link config dict.

        Returns the updated dict for the caller to persist.
        """
        updated = dict(agent_link_cfg)
        updated["exploration_watchdog_enabled"] = self.enabled_check.isChecked()
        updated["exploration_watchdog_warning_threshold"] = self.warning_spin.value()
        updated["exploration_watchdog_control_threshold"] = self.control_spin.value()
        updated["exploration_watchdog_cooldown_steps"] = self.cooldown_spin.value()
        updated["exploration_watchdog_early_grace_minutes"] = self.grace_spin.value()
        updated["exploration_watchdog_long_run_minutes"] = self.long_run_spin.value()
        updated["exploration_watchdog_long_think_seconds"] = self.long_think_spin.value()
        updated["stuck_detect"] = self.stuck_enabled_check.isChecked()
        updated["stuck_worried_threshold"] = self.stuck_worried_spin.value()
        updated["stuck_intervene_threshold"] = self.stuck_intervene_spin.value()
        updated["stuck_window_seconds"] = self.stuck_window_spin.value()
        updated["stuck_cooldown_seconds"] = self.stuck_cooldown_spin.value()
        updated["stuck_reminder_text"] = self.stuck_reminder_edit.text().strip()
        updated["pattern_detect"] = self.pattern_enabled_check.isChecked()
        updated["pattern_w6_control"] = self.pattern_w6_control_spin.value()
        updated["pattern_w10_warn"] = self.pattern_w10_warn_spin.value()
        updated["pattern_w10_control"] = self.pattern_w10_control_spin.value()
        updated["pattern_macro_w6_explore"] = self.pattern_macro_w6_explore_spin.value()
        updated["pattern_macro_w6_action"] = self.pattern_macro_w6_action_spin.value()
        updated["pattern_macro_w10_explore"] = self.pattern_macro_w10_explore_spin.value()
        updated["pattern_macro_w10_action"] = self.pattern_macro_w10_action_spin.value()
        updated["pattern_min_steps_between"] = self.pattern_min_steps_between_spin.value()
        updated["pattern_cooldown_seconds"] = self.pattern_cooldown_seconds_spin.value()
        return updated
