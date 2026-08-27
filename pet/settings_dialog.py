from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import autostart as autostart_mod
from .config import (
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
)
from .proactive import PRESET_DEFAULTS, effective_proactive_config


class PetSettingsDialog(QDialog):
    """旧版菜单专用设置；保持原始表单结构和功能边界。"""

    settings_saved = Signal()

    def __init__(self, config, parent=None, enable_chat: bool = True):
        super().__init__(parent)
        self.config = config
        self.enable_chat = enable_chat
        self.setWindowTitle("桌宠设置")
        self.setMinimumWidth(460)
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # ============================================================ 基础设置
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
        self.capture_check: QCheckBox | None = None
        if sys.platform == "win32":
            self.capture_check = QCheckBox("直播捕获兼容模式（直播姬/OBS 窗口捕获可识别桌宠）")
            self.capture_check.setChecked(bool(config.get("stream_capture_mode", False)))
            self.capture_check.setToolTip(
                "直播姬/OBS 等软件的窗口捕获会过滤不占任务栏的工具窗口，"
                "导致列表里找不到桌宠。开启后桌宠变为普通窗口并显示标题，"
                "即可被捕获（代价：任务栏会显示桌宠图标）。"
            )
            form.addRow("直播捕获兼容", self.capture_check)
        root.addLayout(form)

        # ============================================================ 主动识屏 (仅 Windows + 有聊天能力)
        # 无 Chat 变体排除了 pet.chat，视觉链路不可用，不显示入口（避免可开但必失败）
        self.proactive_group: QGroupBox | None = None
        if sys.platform == "win32" and self.enable_chat:
            self._build_proactive_screen_ui(root)

        root.addWidget(QLabel("自言自语内容（每行一条，留空则恢复内置内容）："))
        self.texts_edit = QPlainTextEdit()
        texts = config.get("self_talk_texts", DEFAULT_SELF_TALK_TEXTS)
        self.texts_edit.setPlainText("\n".join(str(item) for item in texts))
        self.texts_edit.setMinimumHeight(100)
        root.addWidget(self.texts_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _build_proactive_screen_ui(self, root: QVBoxLayout) -> None:
        """构建主动识屏设置面板（手册 §7.1）。"""
        pro_cfg = effective_proactive_config(self.config.get("proactive_screen", {}))

        group = QGroupBox("主动识屏与陪伴（仅 Windows）")
        g_layout = QVBoxLayout(group)
        g_layout.setSpacing(8)

        # 总开关 + dry-run 开关
        h_switches = QHBoxLayout()
        self.pro_enabled_check = QCheckBox("开启主动识屏")
        self.pro_enabled_check.setChecked(bool(pro_cfg.get("enabled", False)))
        h_switches.addWidget(self.pro_enabled_check)

        self.pro_dryrun_check = QCheckBox("dry-run 验证模式（只打日志不调模型）")
        self.pro_dryrun_check.setChecked(bool(pro_cfg.get("dry_run", False)))
        self.pro_dryrun_check.setToolTip("开启后满足条件时只输出日志、不调用模型、不消耗每日额度。")
        h_switches.addWidget(self.pro_dryrun_check)
        g_layout.addLayout(h_switches)

        # 预设选择
        form_pro = QFormLayout()
        form_pro.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_pro.setVerticalSpacing(6)

        self.pro_preset_combo = QComboBox()
        self.pro_preset_combo.addItem("平衡（推荐：停留45s / 冷却5min / 每日15次）", "balanced")
        self.pro_preset_combo.addItem("安静（停留90s / 冷却10min / 每日8次）", "quiet")
        self.pro_preset_combo.addItem("活跃（停留20s / 冷却3min / 每日25次）", "active")
        self.pro_preset_combo.addItem("自定义参数", "custom")

        cur_preset = pro_cfg.get("preset", "balanced")
        idx = self.pro_preset_combo.findData(cur_preset)
        self.pro_preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.pro_preset_combo.currentIndexChanged.connect(self._on_proactive_preset_changed)
        form_pro.addRow("陪伴节奏预设", self.pro_preset_combo)

        # 自定义参数容器
        self.custom_params_widget = QWidget()
        custom_form = QFormLayout(self.custom_params_widget)
        custom_form.setContentsMargins(0, 0, 0, 0)
        custom_form.setVerticalSpacing(6)

        self.pro_dwell_spin = QSpinBox()
        self.pro_dwell_spin.setRange(15, 600)
        self.pro_dwell_spin.setSuffix(" 秒")
        self.pro_dwell_spin.setValue(int(pro_cfg.get("dwell_seconds", 45)))
        custom_form.addRow("窗口停留门限", self.pro_dwell_spin)

        # 冷却间隔：秒/分钟双单位切换（内部统一按分钟存，支持 30 秒 ~ 120 分钟）
        self.pro_cooldown_spin = QDoubleSpinBox()
        self.pro_cooldown_unit = QComboBox()
        self.pro_cooldown_unit.addItem("分钟", "min")
        self.pro_cooldown_unit.addItem("秒", "sec")
        h_cd = QHBoxLayout()
        h_cd.addWidget(self.pro_cooldown_spin)
        h_cd.addWidget(self.pro_cooldown_unit)
        self._set_cooldown_display(float(pro_cfg.get("cooldown_minutes", 5)))
        self.pro_cooldown_unit.currentIndexChanged.connect(self._on_cooldown_unit_changed)
        self.pro_cooldown_spin.setToolTip("两次关怀之间的最短间隔，30 秒 ~ 120 分钟自由调")
        custom_form.addRow("关怀冷却间隔", h_cd)

        self.pro_min_interval_spin = QSpinBox()
        self.pro_min_interval_spin.setRange(30, 3600)
        self.pro_min_interval_spin.setSuffix(" 秒")
        self.pro_min_interval_spin.setValue(int(pro_cfg.get("min_request_interval_seconds", 60)))
        self.pro_min_interval_spin.setToolTip("免费视觉模型的硬保护：两次模型请求的最小间隔，不建议调太小")
        custom_form.addRow("最小请求间隔", self.pro_min_interval_spin)

        self.pro_cap_spin = QSpinBox()
        self.pro_cap_spin.setRange(1, 9999)
        self.pro_cap_spin.setSuffix(" 次/天")
        self.pro_cap_spin.setValue(int(pro_cfg.get("daily_cap", 15)))
        self.pro_cap_spin.setToolTip("每天最多主动关怀几次（DeepSeek 视觉单次约 ¥0.003，15 次/天 ≈ ¥0.05；上限 9999 约等于不限）")
        custom_form.addRow("每日触发上限", self.pro_cap_spin)

        form_pro.addRow("", self.custom_params_widget)
        self.custom_params_widget.setVisible(cur_preset == "custom")

        # 闲置判定
        h_idle = QHBoxLayout()
        self.pro_require_idle_check = QCheckBox("仅当我闲置时触发（敲键盘/操作时不打扰）")
        self.pro_require_idle_check.setChecked(bool(pro_cfg.get("require_idle", False)))
        self.pro_idle_spin = QSpinBox()
        self.pro_idle_spin.setRange(5, 3600)
        self.pro_idle_spin.setSuffix(" 秒")
        self.pro_idle_spin.setValue(int(self.config.get("proactive_screen", {}).get("min_idle_seconds", 30) or 30))
        self.pro_idle_spin.setEnabled(self.pro_require_idle_check.isChecked())
        self.pro_require_idle_check.toggled.connect(self.pro_idle_spin.setEnabled)
        h_idle.addWidget(self.pro_require_idle_check)
        h_idle.addWidget(self.pro_idle_spin)
        form_pro.addRow("闲置守护", h_idle)

        # 选项复选框
        h_opts = QHBoxLayout()
        self.pro_through_check = QCheckBox("鼠标穿透时允许识屏")
        self.pro_through_check.setChecked(bool(pro_cfg.get("allow_when_mouse_through", True)))
        h_opts.addWidget(self.pro_through_check)

        self.pro_precue_check = QCheckBox("触发前先兆提示（“让我看看……”）")
        self.pro_precue_check.setChecked(bool(pro_cfg.get("pre_cue", True)))
        h_opts.addWidget(self.pro_precue_check)

        self.pro_free_check = QCheckBox("优先使用免费视觉模型（GLM-4.6V-Flash）")
        self.pro_free_check.setChecked(bool(pro_cfg.get("prefer_free_provider", True)))
        h_opts.addWidget(self.pro_free_check)
        form_pro.addRow("辅助开关", h_opts)

        g_layout.addLayout(form_pro)

        # 白名单编辑与一键添加
        wl_label = QLabel(
            "白名单（每行一条，留空 = 不识屏）：\n"
            "· 直接写进程名，如 msedge.exe —— 关注这个软件的所有窗口；\n"
            "· 写 title:关键词 —— 只关注标题包含该词的窗口（适合指定某个网页/文档）。"
        )
        wl_label.setWordWrap(True)
        g_layout.addWidget(wl_label)
        self.pro_whitelist_edit = QPlainTextEdit()
        wl = pro_cfg.get("whitelist", [])
        self.pro_whitelist_edit.setPlainText("\n".join(str(x) for x in wl if str(x).strip()))
        self.pro_whitelist_edit.setMinimumHeight(60)
        g_layout.addWidget(self.pro_whitelist_edit)

        h_add = QHBoxLayout()
        self.pro_add_btn = QPushButton("➕ 从当前前台窗口添加…")
        self.pro_add_btn.clicked.connect(self._on_add_foreground_to_whitelist)
        h_add.addWidget(self.pro_add_btn)

        btn_clear_mem = QPushButton("🗑 清除陪伴记忆")
        btn_clear_mem.clicked.connect(self._on_clear_proactive_memory)
        h_add.addWidget(btn_clear_mem)

        h_add.addStretch()
        g_layout.addLayout(h_add)

        self.proactive_group = group
        root.addWidget(group)

    def _on_proactive_preset_changed(self, index: int) -> None:
        preset = self.pro_preset_combo.currentData()
        self.custom_params_widget.setVisible(preset == "custom")
        if preset in PRESET_DEFAULTS:
            vals = PRESET_DEFAULTS[preset]
            self.pro_dwell_spin.setValue(vals["dwell_seconds"])
            self._set_cooldown_display(float(vals["cooldown_minutes"]))
            self.pro_cap_spin.setValue(vals["daily_cap"])

    def _apply_cooldown_unit(self, unit: str, minutes: float) -> None:
        """按指定单位设置冷却间隔的量程与显示值（不自动换单位）。"""
        self.pro_cooldown_unit.blockSignals(True)
        self.pro_cooldown_unit.setCurrentIndex(1 if unit == "sec" else 0)
        if unit == "sec":
            self.pro_cooldown_spin.setRange(30, 7200)
            self.pro_cooldown_spin.setDecimals(0)
            self.pro_cooldown_spin.setValue(min(7200, max(30, round(minutes * 60))))
        else:
            self.pro_cooldown_spin.setRange(0.5, 120)
            self.pro_cooldown_spin.setDecimals(2)
            self.pro_cooldown_spin.setSingleStep(0.5)
            self.pro_cooldown_spin.setValue(min(120.0, max(0.5, minutes)))
        self._cooldown_last_unit = unit
        self.pro_cooldown_unit.blockSignals(False)

    def _set_cooldown_display(self, minutes: float) -> None:
        """从存储值（分钟）初始化/预设时调用：不足 1 分钟用秒显示更直观。"""
        self._apply_cooldown_unit("sec" if minutes < 1 else "min", minutes)

    def _on_cooldown_unit_changed(self) -> None:
        """用户手动切换 秒/分钟：保持新单位，只换算显示值。
        注意：信号触发时 spin 里的数值仍是「旧单位」的，需按旧单位换算。"""
        new_unit = self.pro_cooldown_unit.currentData()
        old = getattr(self, "_cooldown_last_unit", "min")
        v = float(self.pro_cooldown_spin.value())
        minutes = v / 60.0 if old == "sec" else v
        self._apply_cooldown_unit(new_unit, minutes)

    def _cooldown_minutes_value(self) -> float:
        """当前 UI 上的冷却间隔（统一换算成分钟）。"""
        v = float(self.pro_cooldown_spin.value())
        if self.pro_cooldown_unit.currentData() == "sec":
            return v / 60.0
        return v

    def _on_add_foreground_to_whitelist(self) -> None:
        """从当前前台窗口读取进程与标题并添加到白名单框。

        点击时本设置对话框自己是前台窗口，立即采样会采到桌宠进程；
        因此先禁用按钮并提示用户在 3 秒内切换目标窗口，延迟后再采样。
        """
        self.pro_add_btn.setEnabled(False)
        self.pro_add_btn.setText("请在 3 秒内切换到目标窗口…")
        # 用对话框自己的 QTimer 而非全局 singleShot：对话框被关闭销毁时
        # 定时器随之销毁，不会回调到已删除的 C++ 对象
        if not hasattr(self, "_add_fg_timer"):
            self._add_fg_timer = QTimer(self)
            self._add_fg_timer.setSingleShot(True)
            self._add_fg_timer.timeout.connect(self._do_add_foreground_to_whitelist)
        self._add_fg_timer.start(3000)

    def _do_add_foreground_to_whitelist(self) -> None:
        """延迟 3 秒后的回调：恢复按钮状态并采样前台窗口，让用户选择规则类型。"""
        self.pro_add_btn.setEnabled(True)
        self.pro_add_btn.setText("➕ 从当前前台窗口添加…")

        from . import vision

        info = vision.foreground_window_info()
        if not info:
            QMessageBox.information(self, "添加前台窗口", "未能检测到有效的前台窗口，请将目标软件置顶后再试。")
            return

        proc = info.get("process", "").strip()
        title = info.get("title", "").strip()

        # 白名单规则两种写法（让用户明确选择，不再两行都塞）：
        # - 进程名（如 msedge.exe）：这个软件的所有窗口都会被关注——大多数情况选这个
        # - title: 规则：只匹配「标题包含该文字」的窗口，适合只关注某个网页/文档
        box = QMessageBox(self)
        box.setWindowTitle("添加到白名单")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"检测到前台窗口：\n进程：{proc or '（未知）'}\n标题：{title or '（空）'}\n\n"
            "要按哪种方式关注它？"
        )
        btn_proc = box.addButton("按软件（推荐）", QMessageBox.ButtonRole.AcceptRole)
        btn_title = box.addButton("按标题关键词", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()

        clicked = box.clickedButton()
        cur_text = self.pro_whitelist_edit.toPlainText().strip()
        lines = [line.strip() for line in cur_text.splitlines() if line.strip()]
        if clicked is btn_proc and proc:
            if proc not in lines:
                lines.append(proc)
        elif clicked is btn_title and title:
            rule = f"title:*{title}*"
            if rule not in lines:
                lines.append(rule)
        else:
            return  # 取消或采不到内容

        self.pro_whitelist_edit.setPlainText("\n".join(lines))
        # 说明气泡：告诉她两种规则的差别，避免误解
        if clicked is btn_proc:
            hint = f"已加入「{proc}」——她只会关注这个软件的窗口。"
        else:
            hint = "已加入标题规则——只关注标题包含该文字的窗口（可在上方文本框里改关键词）。"
        QMessageBox.information(self, "已加入白名单", hint)

    def _on_clear_proactive_memory(self) -> None:
        """清空本地主动识屏短期陪伴记忆。"""
        from .proactive import ProactiveMemory
        mem = ProactiveMemory(self.config.dir / "proactive_screen_memory.json")
        mem.clear()
        QMessageBox.information(self, "陪伴记忆", "已清空主动识屏的短期陪伴记忆。")

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
        if self.capture_check is not None:
            self.config.set("stream_capture_mode", self.capture_check.isChecked())

        # 保存主动识屏设置 (Windows)
        if sys.platform == "win32" and self.proactive_group is not None:
            wl_lines = [
                line.strip()
                for line in self.pro_whitelist_edit.toPlainText().splitlines()
                if line.strip()
            ]
            preset = self.pro_preset_combo.currentData()
            # 非 custom 预设下改了数值 → 自动落为 custom，否则运行时被预设覆盖（gemini 审查发现）
            if preset in PRESET_DEFAULTS:
                pv = PRESET_DEFAULTS[preset]
                if (self.pro_dwell_spin.value() != pv["dwell_seconds"]
                        or abs(self._cooldown_minutes_value() - pv["cooldown_minutes"]) > 1e-6
                        or self.pro_cap_spin.value() != pv["daily_cap"]):
                    preset = "custom"
            # 从现有配置复制，保留对话框未暴露的字段（min_request_interval_seconds、
            # change_threshold 等），避免保存时把它们冲掉。
            pro_data = dict(self.config.get("proactive_screen", {}) or {})
            pro_data.update({
                "enabled": self.pro_enabled_check.isChecked(),
                "dry_run": self.pro_dryrun_check.isChecked(),
                "preset": preset,
                "allow_when_mouse_through": self.pro_through_check.isChecked(),
                "whitelist": wl_lines,
                "dwell_seconds": self.pro_dwell_spin.value(),
                "require_idle": self.pro_require_idle_check.isChecked(),
                "min_idle_seconds": self.pro_idle_spin.value(),
                "cooldown_minutes": self._cooldown_minutes_value(),
                "min_request_interval_seconds": self.pro_min_interval_spin.value(),
                "daily_cap": self.pro_cap_spin.value(),
                "prefer_free_provider": self.pro_free_check.isChecked(),
                "pre_cue": self.pro_precue_check.isChecked(),
            })
            self.config.set("proactive_screen", pro_data)

        self.config.save()
        self.settings_saved.emit()
        self.accept()
