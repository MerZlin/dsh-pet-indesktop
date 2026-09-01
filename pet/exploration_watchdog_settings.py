# -*- coding: utf-8 -*-
"""Agent Exploration Loop Watchdog standalone settings page.

Used by the modern settings dialog as a sidebar page and can be opened
independently from the context-menu entry (右键 → Agent 联动 → 循环检测设置…).

Configuration keys live under the `agent_link` sub-dict:

    agent_link.exploration_watchdog_enabled
    agent_link.exploration_watchdog_mode
    agent_link.exploration_watchdog_warning_threshold
    agent_link.exploration_watchdog_control_threshold
    agent_link.exploration_watchdog_judge_timeout
    agent_link.exploration_watchdog_cooldown_steps
    agent_link.exploration_watchdog_early_grace_minutes
    agent_link.exploration_watchdog_long_run_minutes
    agent_link.exploration_watchdog_judge_model
    agent_link.exploration_watchdog_judge_provider
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .modern_settings_dialog import (
    BrowserSpinBox,
    ModernSelect,
    SettingRow,
    SettingsSection,
    ToggleSwitch,
)

log = logging.getLogger("dsh-pet-standalone")


class WatchdogSettingsPage(QWidget):
    """Self-contained settings page for Agent Exploration Loop Watchdog.

    Organised in four sections matching WD-27:
      1. 基础设置 (enable + mode)
      2. 风险评分 (warning / control thresholds, judge timeout)
      3. Think 风控 (cooldown, grace, long-run)
      4. Judge 配置 (model + provider)
    """

    settings_saved = Signal()

    def __init__(self, config, agent_link_cfg: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self._agent_cfg = dict(agent_link_cfg)

        # ---- 基础设置 ----
        self.enabled_check = ToggleSwitch(self)
        self.enabled_check.setChecked(bool(self._agent_cfg.get("exploration_watchdog_enabled", True)))
        mode = str(self._agent_cfg.get("exploration_watchdog_mode", "manual")).lower()
        self.mode_select = ModernSelect(self, width=132)
        self.mode_select.addItem("手动（弹窗提醒）", "manual")
        self.mode_select.addItem("自动（直接干预）", "auto")
        idx = self.mode_select.findData(mode)
        self.mode_select.setCurrentIndex(idx if idx >= 0 else 0)

        # ---- 风险评分 ----
        self.warning_spin = BrowserSpinBox(self)
        self.warning_spin.setRange(1, 20)
        self.warning_spin.setSuffix(" 分")
        self.warning_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_warning_threshold", 3)))
        self.control_spin = BrowserSpinBox(self)
        self.control_spin.setRange(1, 30)
        self.control_spin.setSuffix(" 分")
        self.control_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_control_threshold", 5)))
        self.judge_timeout_spin = BrowserSpinBox(self)
        self.judge_timeout_spin.setRange(1, 120)
        self.judge_timeout_spin.setSuffix(" 秒")
        self.judge_timeout_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_judge_timeout", 8)))

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

        # ---- Judge 配置 ----
        self.judge_model_edit = QLineEdit(self)
        self.judge_model_edit.setPlaceholderText("留空使用默认模型")
        self.judge_model_edit.setText(str(self._agent_cfg.get("exploration_watchdog_judge_model") or ""))
        self.judge_model_edit.setClearButtonEnabled(True)
        self.judge_provider_edit = QLineEdit(self)
        self.judge_provider_edit.setPlaceholderText("留空使用当前聊天 API")
        self.judge_provider_edit.setText(str(self._agent_cfg.get("exploration_watchdog_judge_provider") or ""))
        self.judge_provider_edit.setClearButtonEnabled(True)

        # ---- Layout ----
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        root.addWidget(SettingsSection("基础设置", [
            SettingRow("watchdog_enabled", "启用循环检测",
                        "识别重复的 Search/Read/Think 行为，防止 Agent 陷入无限探索循环。",
                        self.enabled_check),
            SettingRow("watchdog_mode", "运行模式",
                        "手动：触发时弹出确认弹窗；自动：直接执行干预策略。",
                        self.mode_select),
        ], self))

        root.addWidget(SettingsSection("风险评分", [
            SettingRow("warning_threshold", "Warning 阈值",
                        "风险分数达到此值时发出警告提醒。", self.warning_spin),
            SettingRow("control_threshold", "Control 阈值",
                        "风险分数达到此值时请求 Judge 介入（最高风险）。", self.control_spin),
            SettingRow("judge_timeout", "Judge 超时",
                        "向模型发送判定请求后等待的最长时间；超时将安全降级。", self.judge_timeout_spin),
        ], self))

        root.addWidget(SettingsSection("Think 风控", [
            SettingRow("cooldown_steps", "评估冷却步数",
                        "两次风险检测之间的最小步数间隔，防止高频误报。", self.cooldown_spin),
            SettingRow("early_grace_minutes", "启动宽限期",
                        "Agent 启动后的前 N 分钟提高风险阈值，允许更多探索空间。", self.grace_spin),
            SettingRow("long_run_minutes", "长运行降阈值",
                        "连续运行超过 N 分钟后，风险阈值自动降低 1，提高敏感度。", self.long_run_spin),
        ], self))

        # Judge 配置 — 把两个输入框放在同一个自定义 widget 里纵向排列
        judge_widget = QWidget()
        judge_layout = QVBoxLayout(judge_widget)
        judge_layout.setContentsMargins(0, 0, 0, 0)
        judge_layout.setSpacing(8)
        judge_layout.addWidget(self.judge_model_edit)
        judge_layout.addWidget(self.judge_provider_edit)
        root.addWidget(SettingsSection("Judge 配置", [
            SettingRow("judge_model", "Judge 模型",
                        "用于风险评估判定的模型标识；留空使用默认模型。", judge_widget, stacked=True),
        ], self))

        root.addStretch(1)

    def apply_to_config(self, agent_link_cfg: dict) -> dict:
        """Merge current values into the agent_link config dict.

        Returns the updated dict for the caller to persist.
        """
        updated = dict(agent_link_cfg)
        updated["exploration_watchdog_enabled"] = self.enabled_check.isChecked()
        updated["exploration_watchdog_mode"] = str(self.mode_select.currentData() or "manual")
        updated["exploration_watchdog_warning_threshold"] = self.warning_spin.value()
        updated["exploration_watchdog_control_threshold"] = self.control_spin.value()
        updated["exploration_watchdog_judge_timeout"] = self.judge_timeout_spin.value()
        updated["exploration_watchdog_cooldown_steps"] = self.cooldown_spin.value()
        updated["exploration_watchdog_early_grace_minutes"] = self.grace_spin.value()
        updated["exploration_watchdog_long_run_minutes"] = self.long_run_spin.value()
        judge_model = self.judge_model_edit.text().strip()
        updated["exploration_watchdog_judge_model"] = judge_model if judge_model else ""
        judge_provider = self.judge_provider_edit.text().strip()
        updated["exploration_watchdog_judge_provider"] = judge_provider if judge_provider else ""
        return updated

    def refresh_from_config(self, agent_link_cfg: dict) -> None:
        """Re-read values from the live config (e.g. after external change)."""
        self._agent_cfg = dict(agent_link_cfg)
        self.enabled_check.setChecked(bool(self._agent_cfg.get("exploration_watchdog_enabled", True)))
        mode = str(self._agent_cfg.get("exploration_watchdog_mode", "manual")).lower()
        idx = self.mode_select.findData(mode)
        self.mode_select.setCurrentIndex(idx if idx >= 0 else 0)
        self.warning_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_warning_threshold", 3)))
        self.control_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_control_threshold", 5)))
        self.judge_timeout_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_judge_timeout", 8)))
        self.cooldown_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_cooldown_steps", 3)))
        self.grace_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_early_grace_minutes", 5)))
        self.long_run_spin.setValue(int(self._agent_cfg.get("exploration_watchdog_long_run_minutes", 10)))
        self.judge_model_edit.setText(str(self._agent_cfg.get("exploration_watchdog_judge_model") or ""))
        self.judge_provider_edit.setText(str(self._agent_cfg.get("exploration_watchdog_judge_provider") or ""))


class WatchdogSettingsDialog(QWidget):
    """Standalone top-level dialog for the watchdog settings page.

    Used when opened from the context-menu entry:
        右键 → Agent 联动 → 循环检测设置…
    """

    settings_saved = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._saved_via_button = False
        self._positioned_away = False
        self.setWindowTitle("循环检测设置")
        self.resize(760, 520)
        self.setMinimumSize(640, 440)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setProperty("modernStyle", True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar with save button
        top_bar = QFrame(self)
        top_bar.setObjectName("watchdogTopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 10, 12, 10)
        top_layout.setSpacing(8)
        title_label = QLabel("循环检测设置", top_bar)
        title_label.setObjectName("pageTitle")
        top_layout.addWidget(title_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_layout.addStretch(1)
        self.save_button = QPushButton("保存并关闭", top_bar)
        self.save_button.setObjectName("saveAndExit")
        self.save_button.clicked.connect(self._do_save)
        top_layout.addWidget(self.save_button)
        root.addWidget(top_bar)

        # Content scroll area
        scroll = QScrollArea(self)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page = WatchdogSettingsPage(config, dict(config.get("agent_link", {})), scroll)
        scroll.setWidget(self.page)
        root.addWidget(scroll, 1)

        # Connect signals
        self.page.settings_saved.connect(self.settings_saved.emit)

    def _do_save(self) -> None:
        """Write current values to config, mark saved, and close."""
        try:
            self.config._load()
            agent_link = dict(self.config.get("agent_link", {}))
            updated = self.page.apply_to_config(agent_link)
            self.config.set("agent_link", updated)
            self.config.save()
            self._saved_via_button = True
            # Notify pet to hot-reload the watchdog if it exists
            if hasattr(self, "_pet_instance"):
                pet = self._pet_instance
                if hasattr(pet, "agent_link_manager"):
                    pet.agent_link_manager.apply_config()
            self.settings_saved.emit()
            self.close()
        except Exception:
            log.exception("保存循环检测设置失败")

    def set_pet_instance(self, pet) -> None:
        """Keep a reference to the pet for hot-reloading config."""
        self._pet_instance = pet

    def move_away_from_pet(self) -> None:
        """Position the dialog away from the pet window (called before show)."""
        if self._positioned_away:
            return
        self._positioned_away = True
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            self._move_away_from(parent.geometry())

    def _move_away_from(self, pet_geo: QRect) -> None:
        """Move the dialog to a position that doesn't intersect the pet."""
        size = self.size()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect()
        for rect in (
            QRect(pet_geo.right() + 12, pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left() - 12 - size.width(), pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.bottom() + 12, size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.top() - 12 - size.height(), size.width(), size.height()),
        ):
            if avail.contains(rect):
                self.move(rect.topLeft())
                return

    def refresh(self) -> None:
        """Re-read the latest config from disk."""
        self.page.refresh_from_config(dict(self.config.get("agent_link", {})))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Auto-save on close (X / Esc), matching the save-then-close button."""
        if not self._saved_via_button:
            try:
                self._do_save()
            except Exception:
                log.exception("关闭时保存循环检测设置失败")
        super().closeEvent(event)
