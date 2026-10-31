"""AI 对话扩展的设置页。

这是 **受信 UI**：设置页由 Core 托管，允许通过 ``host.config``（ScopedConfig）
读写；运行时代码（``runtime.py``）不碰 Qt，只经 ``HostPorts.config`` 读配置。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRegularExpression, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from petcore.ui.chat.themes import theme_names
from petcore.ui.settings.base import SettingsPage, make_button, make_checkbox

from .persona_presets import (
    CUSTOM_PERSONA,
    DEFAULT_PERSONA,
    PersonaPreset,
    PersonaStore,
    new_user_id,
)
from .prompts import DEFAULT_PROMPT

_CUSTOM_BACKGROUND = "__custom__"
_HEX = QRegularExpression(r"^#[0-9a-fA-F]{6}$")


class AiChatPage(SettingsPage):
    title = "AI 对话"

    _KEYS = (
        "ai_service",
        "ai_model",
        "ai_system_prompt",
        "ai_persona",
        "ai_vision_enabled",
        "ai_temperature",
        "ai_look_screen_auto_open",
        "ai_thinking",
        "ai_reasoning_effort",
        "ai_animation_enabled",
        "ai_animation_thinking",
        "ai_animation_min_seconds",
        "ai_voice_reply",
        "ai_memory_enabled",
        "ai_memory_inject",
        "ai_memory_max_entries",
        "ai_web_search",
        "ai_web_engine",
        "ai_web_results",
        "ai_web_fetch",
        "chat_background",
        "chat_accent",
        "chat_font_size",
        "chat_font_family",
        "chat_follow_pet",
        "chat_always_on_top",
        "ai_proactive_enabled",
        "ai_proactive_whitelist",
        "ai_proactive_dwell_seconds",
        "ai_proactive_change_threshold",
        "ai_proactive_daily_cap",
        "ai_proactive_cooldown_seconds",
        "ai_proactive_min_interval_seconds",
        "ai_proactive_require_idle",
        "ai_proactive_min_idle_seconds",
        "ai_proactive_dry_run",
        "ai_proactive_pre_cue",
        "ai_proactive_sync_chat",
    )

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.group_heading("AI 对话")
        self._initial = {key: self.config().get(key) for key in self._KEYS}
        self._background_value = str(self._initial.get("chat_background") or "")

        card = self.section("模型", level=3)
        self.service_combo = QComboBox(self)
        self.service_combo.addItem("自动（第一个服务）", "")
        try:
            for meta in self.api().services():
                self.service_combo.addItem(meta.name or meta.service_id, meta.service_id)
        except Exception:
            pass
        index = self.service_combo.findData(str(self._initial.get("ai_service") or ""))
        self.service_combo.setCurrentIndex(index if index >= 0 else 0)
        self.service_combo.currentIndexChanged.connect(self._on_changed)
        card.add_row("对话服务", self.service_combo, "留空自动用「API 与账户」里的第一个服务。")

        self.model_edit = QLineEdit(self)
        self.model_edit.setPlaceholderText("留空用服务里的默认模型")
        self.model_edit.setText(str(self._initial.get("ai_model") or ""))
        self.model_edit.editingFinished.connect(self._on_changed)
        card.add_row("模型覆盖", self.model_edit, "只影响对话；截图提问会优先用视觉模型。")

        self.temperature_spin = QDoubleSpinBox(self)
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setDecimals(1)
        try:
            self.temperature_spin.setValue(float(self._initial.get("ai_temperature") or 0.7))
        except (TypeError, ValueError):
            self.temperature_spin.setValue(0.7)
        self.temperature_spin.valueChanged.connect(self._on_changed)
        card.add_row("温度", self.temperature_spin, "越高越发散，0.7 适合日常聊天。")

        card2 = self.section("人格与视觉", level=3)
        self._persona_store = PersonaStore.for_data_root(self.config().root)
        self._personas: dict[str, PersonaPreset] = {}
        self._builtin_personas = {item.id for item in self._persona_store.bundled()}
        self.persona_combo = QComboBox(self)
        self.persona_combo.currentIndexChanged.connect(self._on_persona_changed)
        card2.add_row(
            "人设",
            self.persona_combo,
            "内置预设与用户人格卡都从这里选；选中后可直接改提示词，"
            "点「保存人格卡」立即写入用户目录（不受整窗取消影响；内置预设会生成同 id 覆盖文件）。",
        )
        self.persona_name = QLineEdit(self)
        self.persona_name.setMaxLength(32)
        self.persona_name.setPlaceholderText("人格名称（保存人格卡时使用）")
        card2.add_row(
            "人格名称",
            self.persona_name,
            "重命名内置预设会生成用户覆盖；「恢复内置」可撤销覆盖。",
        )
        self.persona_desc = QLabel("", self)
        self.persona_desc.setObjectName("settingHint")
        self.persona_desc.setWordWrap(True)
        card2.add_row("人设说明", self.persona_desc, "")

        persona_buttons = QWidget(self)
        persona_buttons_layout = QHBoxLayout(persona_buttons)
        persona_buttons_layout.setContentsMargins(0, 0, 0, 0)
        persona_buttons_layout.setSpacing(6)
        self.persona_new_button = make_button("新建", persona_buttons)
        self.persona_copy_button = make_button("复制当前", persona_buttons)
        self.persona_rename_button = make_button("重命名", persona_buttons)
        self.persona_save_button = make_button("保存人格卡", persona_buttons, primary=True)
        self.persona_reset_button = make_button("恢复内置", persona_buttons)
        self.persona_delete_button = make_button("删除", persona_buttons)
        self.persona_new_button.clicked.connect(self._new_persona)
        self.persona_copy_button.clicked.connect(self._copy_persona)
        self.persona_rename_button.clicked.connect(self._rename_persona)
        self.persona_save_button.clicked.connect(self._save_persona)
        self.persona_reset_button.clicked.connect(self._reset_persona)
        self.persona_delete_button.clicked.connect(self._delete_persona)
        for button in (
            self.persona_new_button,
            self.persona_copy_button,
            self.persona_rename_button,
            self.persona_save_button,
            self.persona_reset_button,
            self.persona_delete_button,
        ):
            persona_buttons_layout.addWidget(button)
        persona_buttons_layout.addStretch(1)
        card2.add_row(
            "人格卡",
            persona_buttons,
            "新建/复制/改名/保存/删除；包内预设不会被删，覆盖文件可随时恢复。",
            expand=True,
        )

        self.prompt_edit = QPlainTextEdit(self)
        self.prompt_edit.setPlaceholderText("留空使用默认人格")
        self.prompt_edit.textChanged.connect(self._on_changed)
        card2.add_row(
            "系统提示词",
            self.prompt_edit,
            "每次对话开头发给模型；支持 {name} 占位。编辑后点「保存人格卡」才写入用户卡。",
            expand=True,
        )
        persona = str(self._initial.get("ai_persona") or "").strip()
        if not persona:
            persona = (
                CUSTOM_PERSONA
                if str(self._initial.get("ai_system_prompt") or "").strip()
                else DEFAULT_PERSONA
            )
        self._reload_personas(persona)
        self._sync_persona_rows()

        self.vision_check = make_checkbox(self)
        self.vision_check.setChecked(bool(self._initial.get("ai_vision_enabled", True)))
        self.vision_check.toggled.connect(self._on_changed)
        card2.add_row(
            "允许截图提问",
            self.vision_check,
            "截图只读取当前屏幕并直接发给模型，不写入磁盘。",
        )

        self.auto_open_check = make_checkbox(self)
        self.auto_open_check.setChecked(
            bool(self._initial.get("ai_look_screen_auto_open", False))
        )
        self.auto_open_check.toggled.connect(self._on_changed)
        card2.add_row(
            "看看屏幕后自动打开对话",
            self.auto_open_check,
            "默认只把结果同步进对话记录并用气泡回答；勾选后才主动唤出对话窗口。",
        )

        card_anim = self.section("对话动作", level=3)
        self.anim_check = make_checkbox(self)
        self.anim_check.setChecked(
            bool(self._initial.get("ai_animation_enabled", True))
        )
        self.anim_check.toggled.connect(self._on_changed)
        card_anim.add_row(
            "回复时播放动作",
            self.anim_check,
            "按回复语义从当前角色的随机动作池挑一个动作；关闭后对话完全不自动切动作。",
        )

        self.anim_thinking_check = make_checkbox(self)
        self.anim_thinking_check.setChecked(
            bool(self._initial.get("ai_animation_thinking", True))
        )
        self.anim_thinking_check.toggled.connect(self._on_changed)
        card_anim.add_row(
            "生成时播放思考动作",
            self.anim_thinking_check,
            "收到第一个字时先播一个「思考」类动作；没有这类素材时不播。",
        )

        self.anim_min_spin = QDoubleSpinBox(self)
        self.anim_min_spin.setRange(0.5, 30.0)
        self.anim_min_spin.setSingleStep(0.5)
        self.anim_min_spin.setDecimals(1)
        self.anim_min_spin.setSuffix(" 秒")
        try:
            self.anim_min_spin.setValue(
                float(self._initial.get("ai_animation_min_seconds") or 2.0)
            )
        except (TypeError, ValueError):
            self.anim_min_spin.setValue(2.0)
        self.anim_min_spin.valueChanged.connect(self._on_changed)
        card_anim.add_row(
            "最短切换间隔",
            self.anim_min_spin,
            "两次对话动作之间至少间隔多久；回复完成时的语义动作可以打断思考动作。",
        )

        self.voice_reply_check = make_checkbox(self)
        self.voice_reply_check.setChecked(
            bool(self._initial.get("ai_voice_reply", False))
        )
        self.voice_reply_check.toggled.connect(self._on_changed)
        card_anim.add_row(
            "朗读回复",
            self.voice_reply_check,
            "回复完成后用「语音 → 主后端」朗读（去 Markdown、最多 500 字）。",
        )

        card_mem = self.section("长期记忆", level=3)
        self.memory_check = make_checkbox(self)
        self.memory_check.setChecked(
            bool(self._initial.get("ai_memory_enabled", True))
        )
        self.memory_check.toggled.connect(self._on_changed)
        card_mem.add_row(
            "启用长期记忆",
            self.memory_check,
            "用户说「记住…」时写入本机；每轮按问题检索 top-K 注入 system。",
        )
        self.memory_inject = QSpinBox(self)
        self.memory_inject.setRange(1, 8)
        self.memory_inject.setSuffix(" 条")
        self.memory_inject.setValue(int(self._initial.get("ai_memory_inject") or 3))
        self.memory_inject.valueChanged.connect(self._on_changed)
        card_mem.add_row(
            "每轮注入",
            self.memory_inject,
            "最多注入几条相关记忆；总预算 800 字。",
        )
        self.memory_max = QSpinBox(self)
        self.memory_max.setRange(50, 2000)
        self.memory_max.setSuffix(" 条")
        self.memory_max.setValue(
            int(self._initial.get("ai_memory_max_entries") or 500)
        )
        self.memory_max.valueChanged.connect(self._on_changed)
        card_mem.add_row(
            "每域上限",
            self.memory_max,
            "超出后按创建时间淘汰最旧条目；全局与当前角色各一份。",
        )
        memory_buttons = [
            make_button("打开记忆目录", self),
            make_button("清空当前角色记忆", self),
        ]
        memory_buttons[0].clicked.connect(self._open_memory_dir)
        memory_buttons[1].clicked.connect(self._clear_memory)
        card_mem.add_buttons(memory_buttons)
        self.memory_status = QLabel("", self)
        self.memory_status.setObjectName("settingHint")
        self.memory_status.setWordWrap(True)
        card_mem.add_widget(self.memory_status)
        self._refresh_memory_status()

        card_proactive = self.section("主动识屏", level=3)
        self.proactive_check = make_checkbox(self)
        self.proactive_check.setChecked(bool(self._initial.get("ai_proactive_enabled", False)))
        self.proactive_check.toggled.connect(self._on_changed)
        card_proactive.add_row(
            "启用主动识屏",
            self.proactive_check,
            "每 8 秒看一眼前台窗口；同一个窗口停留够久、画面变了才请求一次。"
            "答复只冒气泡，不主动打开对话窗，截图不写入磁盘。",
        )

        whitelist_box = QWidget(self)
        whitelist_layout = QVBoxLayout(whitelist_box)
        whitelist_layout.setContentsMargins(0, 0, 0, 0)
        whitelist_layout.setSpacing(6)
        self.whitelist_edit = QPlainTextEdit(whitelist_box)
        self.whitelist_edit.setPlaceholderText(
            "每行一条：进程名（如 chrome.exe）或 title:关键词（如 title:*报告*）"
        )
        self.whitelist_edit.setMaximumHeight(96)
        self.whitelist_edit.setPlainText(
            "\n".join(
                str(rule) for rule in (self._initial.get("ai_proactive_whitelist") or [])
            )
        )
        self.whitelist_edit.textChanged.connect(self._on_changed)
        whitelist_layout.addWidget(self.whitelist_edit)
        self.proactive_add_button = QPushButton("从当前前台窗口添加…", whitelist_box)
        self.proactive_add_button.clicked.connect(self._add_from_foreground)
        whitelist_layout.addWidget(self.proactive_add_button)
        card_proactive.add_row(
            "白名单",
            whitelist_box,
            "白名单为空时完全不工作（不是「全窗口都看」）。"
            "无前缀只匹配进程名；title: 前缀只匹配窗口标题，支持 * 与 ?。"
            "点下面的按钮后有 3 秒时间切到目标软件。",
            expand=True,
        )

        self.proactive_dwell_spin = QSpinBox(self)
        self.proactive_dwell_spin.setRange(15, 600)
        self.proactive_dwell_spin.setSuffix(" 秒")
        self.proactive_dwell_spin.setValue(
            int(self._initial.get("ai_proactive_dwell_seconds") or 45)
        )
        self.proactive_dwell_spin.valueChanged.connect(self._on_changed)
        card_proactive.add_row(
            "停留多久才看",
            self.proactive_dwell_spin,
            "同一个窗口连续停留超过这个时长才触发；默认 45 秒。",
        )

        self.proactive_threshold_spin = QSpinBox(self)
        self.proactive_threshold_spin.setRange(0, 32)
        self.proactive_threshold_spin.setValue(
            int(self._initial.get("ai_proactive_change_threshold") or 8)
        )
        self.proactive_threshold_spin.valueChanged.connect(self._on_changed)
        card_proactive.add_row(
            "画面变化阈值",
            self.proactive_threshold_spin,
            "64 位画面指纹的差异位数；小于阈值算「没变」，不发请求。0 = 每次都发。",
        )

        self.proactive_cap_spin = QSpinBox(self)
        self.proactive_cap_spin.setRange(1, 9999)
        self.proactive_cap_spin.setSuffix(" 次")
        self.proactive_cap_spin.setValue(int(self._initial.get("ai_proactive_daily_cap") or 15))
        self.proactive_cap_spin.valueChanged.connect(self._on_changed)
        card_proactive.add_row(
            "每日上限",
            self.proactive_cap_spin,
            "按真实请求计数，到了当天就不再请求（静默，不打扰）。",
        )

        self.proactive_cooldown_spin = QSpinBox(self)
        self.proactive_cooldown_spin.setRange(30, 7200)
        self.proactive_cooldown_spin.setSingleStep(30)
        self.proactive_cooldown_spin.setSuffix(" 秒")
        self.proactive_cooldown_spin.setValue(
            int(self._initial.get("ai_proactive_cooldown_seconds") or 300)
        )
        self.proactive_cooldown_spin.valueChanged.connect(self._on_changed)
        card_proactive.add_row(
            "两次请求的冷却",
            self.proactive_cooldown_spin,
            "一次主动识屏之后至少隔这么久才会再请求；默认 300 秒。",
        )

        self.proactive_interval_spin = QSpinBox(self)
        self.proactive_interval_spin.setRange(30, 3600)
        self.proactive_interval_spin.setSingleStep(10)
        self.proactive_interval_spin.setSuffix(" 秒")
        self.proactive_interval_spin.setValue(
            int(self._initial.get("ai_proactive_min_interval_seconds") or 60)
        )
        self.proactive_interval_spin.valueChanged.connect(self._on_changed)
        card_proactive.add_row(
            "最小请求间隔",
            self.proactive_interval_spin,
            "两次请求之间的硬下限（含失败），防止连续刷额度。",
        )

        self.proactive_idle_check = make_checkbox(self)
        self.proactive_idle_check.setChecked(
            bool(self._initial.get("ai_proactive_require_idle", False))
        )
        self.proactive_idle_check.toggled.connect(self._sync_proactive_visibility)
        self.proactive_idle_check.toggled.connect(self._on_changed)
        card_proactive.add_row(
            "只在我离开键盘时识屏",
            self.proactive_idle_check,
            "鼠标键盘有输入时不打扰你；默认关闭。",
        )

        self.proactive_idle_spin = QSpinBox(self)
        self.proactive_idle_spin.setRange(0, 3600)
        self.proactive_idle_spin.setSuffix(" 秒")
        self.proactive_idle_spin.setValue(
            int(self._initial.get("ai_proactive_min_idle_seconds") or 30)
        )
        self.proactive_idle_spin.valueChanged.connect(self._on_changed)
        self._proactive_idle_row = card_proactive.add_row(
            "闲置门槛",
            self.proactive_idle_spin,
            "键鼠空闲超过这个时长才算「离开」；关掉上一项时本项不生效。",
        )

        self.proactive_dry_check = make_checkbox(self)
        self.proactive_dry_check.setChecked(bool(self._initial.get("ai_proactive_dry_run", False)))
        self.proactive_dry_check.toggled.connect(self._on_changed)
        card_proactive.add_row(
            "只演练不请求",
            self.proactive_dry_check,
            "只写日志（进程名、窗口句柄、画面指纹），不发请求、不消耗额度。",
        )

        self.proactive_cue_check = make_checkbox(self)
        self.proactive_cue_check.setChecked(bool(self._initial.get("ai_proactive_pre_cue", True)))
        self.proactive_cue_check.toggled.connect(self._on_changed)
        card_proactive.add_row(
            "先冒「让我看看……」",
            self.proactive_cue_check,
            "请求前先给一句先兆，避免答复突然出现。",
        )

        self.proactive_sync_check = make_checkbox(self)
        self.proactive_sync_check.setChecked(bool(self._initial.get("ai_proactive_sync_chat", True)))
        self.proactive_sync_check.toggled.connect(self._on_changed)
        card_proactive.add_row(
            "同步进对话记录",
            self.proactive_sync_check,
            "答复同时写进该桌宠的会话（不会主动打开对话窗）。",
        )
        self._sync_proactive_visibility()

        card4 = self.section("深度思考", level=3)
        self.thinking_combo = QComboBox(self)
        self.thinking_combo.addItem("跟随模型默认", "auto")
        self.thinking_combo.addItem("开启深度思考", "enabled")
        self.thinking_combo.addItem("关闭深度思考", "disabled")
        index = self.thinking_combo.findData(str(self._initial.get("ai_thinking") or "auto"))
        self.thinking_combo.setCurrentIndex(index if index >= 0 else 0)
        self.thinking_combo.currentIndexChanged.connect(self._update_thinking_web_visibility)
        self.thinking_combo.currentIndexChanged.connect(self._on_changed)
        card4.add_row(
            "深度思考",
            self.thinking_combo,
            "开启后助手气泡会显示可折叠的思维链；跟随默认只对 DeepSeek 官方地址下发参数。",
        )

        self.effort_combo = QComboBox(self)
        self.effort_combo.addItem("低（更快）", "low")
        self.effort_combo.addItem("高（默认）", "high")
        self.effort_combo.addItem("最高（更慢）", "max")
        index = self.effort_combo.findData(
            str(self._initial.get("ai_reasoning_effort") or "high")
        )
        self.effort_combo.setCurrentIndex(index if index >= 0 else 1)
        self.effort_combo.currentIndexChanged.connect(self._on_changed)
        self._effort_row = card4.add_row(
            "思考强度",
            self.effort_combo,
            "只在「开启深度思考」时发送；强度越高越慢、越耗 Token。",
        )

        card5 = self.section("联网搜索", level=3)
        self.web_check = make_checkbox(self)
        self.web_check.setChecked(bool(self._initial.get("ai_web_search", False)))
        self.web_check.toggled.connect(self._update_thinking_web_visibility)
        self.web_check.toggled.connect(self._on_changed)
        card5.add_row(
            "允许联网搜索",
            self.web_check,
            "回答前先让模型判断要不要联网，需要时检索资料再作答。",
        )

        self.engine_combo = QComboBox(self)
        for engine, label in (
            ("baidu", "百度（免 Key）"),
            ("duckduckgo", "DuckDuckGo（免 Key，通常需代理）"),
            ("bocha", "博查 Bocha（需 API Key）"),
            ("tavily", "Tavily（需 API Key）"),
        ):
            self.engine_combo.addItem(label, engine)
        index = self.engine_combo.findData(
            str(self._initial.get("ai_web_engine") or "baidu")
        )
        self.engine_combo.setCurrentIndex(index if index >= 0 else 0)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self._engine_row = card5.add_row(
            "搜索引擎",
            self.engine_combo,
            "免 Key 的两个引擎互为兜底；遇到人机验证会如实提示，不会绕过。",
        )

        key_row = QWidget(self)
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        self.web_key_edit = QLineEdit(key_row)
        self.web_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.web_key_edit.editingFinished.connect(self._save_web_key)
        key_layout.addWidget(self.web_key_edit, 1)
        self.web_key_state = QLabel("", key_row)
        self.web_key_state.setObjectName("settingHint")
        key_layout.addWidget(self.web_key_state)
        self._web_key_row = card5.add_row(
            "搜索 API Key",
            key_row,
            "只存系统钥匙串；输入新值会覆盖旧值，留空不修改。",
        )

        self.web_results_spin = QSpinBox(self)
        self.web_results_spin.setRange(1, 10)
        self.web_results_spin.setSuffix(" 条")
        try:
            self.web_results_spin.setValue(int(self._initial.get("ai_web_results") or 5))
        except (TypeError, ValueError):
            self.web_results_spin.setValue(5)
        self.web_results_spin.valueChanged.connect(self._on_changed)
        self._web_results_row = card5.add_row(
            "每次搜索结果数",
            self.web_results_spin,
            "单次搜索交给模型的结果条数。",
        )

        self.web_fetch_check = make_checkbox(self)
        self.web_fetch_check.setChecked(bool(self._initial.get("ai_web_fetch", True)))
        self.web_fetch_check.toggled.connect(self._on_changed)
        self._web_fetch_row = card5.add_row(
            "允许阅读网页正文",
            self.web_fetch_check,
            "搜索摘要不够时允许模型打开网页读取正文；更准，但更慢。",
        )
        self._refresh_web_key_state()
        self._update_thinking_web_visibility()

        card3 = self.section("对话窗外观", level=3)
        self.background_combo = QComboBox(self)
        self._rebuild_background_options()
        self.background_combo.currentIndexChanged.connect(self._on_background_changed)
        card3.add_row(
            "背景",
            self.background_combo,
            "内置主题或本地图片；默认白色。改动会立即预览到已打开的对话窗。",
        )

        accent_row = QWidget(self)
        accent_layout = QHBoxLayout(accent_row)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(6)
        self.accent_edit = QLineEdit(accent_row)
        self.accent_edit.setPlaceholderText("#326de6（留空用角色默认色）")
        self.accent_edit.setText(str(self._initial.get("chat_accent") or ""))
        self.accent_edit.setMaximumWidth(150)
        self.accent_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"^#[0-9a-fA-F]{0,6}$|^$"), self.accent_edit
            )
        )
        self.accent_edit.editingFinished.connect(self._on_accent_finished)
        accent_layout.addWidget(self.accent_edit)
        self.accent_button = QPushButton("选择…", accent_row)
        self.accent_button.clicked.connect(self._pick_accent)
        accent_layout.addWidget(self.accent_button)
        self.accent_reset = QPushButton("默认", accent_row)
        self.accent_reset.clicked.connect(self._reset_accent)
        accent_layout.addWidget(self.accent_reset)
        accent_layout.addStretch(1)
        card3.add_row(
            "强调色",
            accent_row,
            "发送键、选中态与强调线；留空使用角色默认色，格式 #RRGGBB。",
        )

        self.default_font_check = make_checkbox(self)
        self.default_font_check.setChecked(
            not bool(str(self._initial.get("chat_font_family") or "").strip())
        )
        self.default_font_check.toggled.connect(self._sync_font_rows)
        self.default_font_check.toggled.connect(self._on_changed)
        self.font_combo = QFontComboBox(self)
        family = str(self._initial.get("chat_font_family") or "").strip()
        if family:
            self.font_combo.setCurrentFont(QFont(family))
        card3.add_row(
            "对话窗字体",
            self.font_combo,
            "只改对话窗；设置页、菜单、气泡仍跟随全局界面字号。",
        )
        card3.add_row(
            "使用系统字体",
            self.default_font_check,
            "勾选后忽略上面的字体选择，跟随系统 UI 字体。",
        )

        self.chat_font_size = QSpinBox(self)
        self.chat_font_size.setRange(0, 24)
        self.chat_font_size.setSuffix(" px")
        self.chat_font_size.setSpecialValueText("跟随界面字号")
        self.chat_font_size.setValue(
            int(self._initial.get("chat_font_size") or 0)
        )
        self.chat_font_size.valueChanged.connect(self._on_changed)
        card3.add_row(
            "对话窗字号",
            self.chat_font_size,
            "0 = 跟随「文字与快捷键 → 界面字号」；10–24 可单独放大/缩小对话窗。",
        )
        self._sync_font_rows()

        self.follow_check = make_checkbox(self)
        self.follow_check.setChecked(bool(self._initial.get("chat_follow_pet", False)))
        self.follow_check.toggled.connect(self._on_changed)
        card3.add_row(
            "跟随桌宠",
            self.follow_check,
            "打开后对话窗跟着发起它的桌宠移动，优先停在桌宠右侧，放不下时切到左侧。",
        )

        self.on_top_check = make_checkbox(self)
        self.on_top_check.setChecked(
            bool(self._initial.get("chat_always_on_top", False))
        )
        self.on_top_check.toggled.connect(self._on_changed)
        card3.add_row(
            "对话窗置顶",
            self.on_top_check,
            "与标题栏的图钉按钮是同一个开关。",
        )

        self.add_note(
            "对话内容按桌宠 + 角色分别保存在本机 sessions/ 目录；密钥只在系统钥匙串。"
            "截图提问是独立的框选窗口，图片只在内存中流转，问答历史随当次采集结束。"
        )

    # ---------------------------------------------------------------- 思考/联网控件
    def _keyed_engine(self) -> bool:
        return str(self.engine_combo.currentData() or "baidu") in {"bocha", "tavily"}

    def _refresh_web_key_state(self) -> None:
        engine = str(self.engine_combo.currentData() or "baidu")
        saved = False
        api = self.api()
        status = getattr(api, "web_search_key_status", None) if api is not None else None
        if callable(status):
            try:
                saved = bool((status() or {}).get(engine))
            except Exception:
                saved = False
        if self._keyed_engine():
            self.web_key_edit.setPlaceholderText(
                "已保存，留空不修改" if saved else "输入 API Key"
            )
            self.web_key_state.setText("已保存" if saved else "未配置")
        else:
            self.web_key_edit.setPlaceholderText("免 Key 引擎不需要")
            self.web_key_state.setText("")

    def _on_engine_changed(self, *_args) -> None:
        self._refresh_web_key_state()
        self._update_thinking_web_visibility()
        self._on_changed()

    def _save_web_key(self) -> None:
        text = self.web_key_edit.text().strip()
        if not text or not self._keyed_engine():
            return
        api = self.api()
        setter = getattr(api, "set_web_search_key", None) if api is not None else None
        if not callable(setter):
            return
        engine = str(self.engine_combo.currentData() or "baidu")
        try:
            setter(engine, text)
        except Exception:
            return
        self.web_key_edit.clear()
        self._refresh_web_key_state()
        available = getattr(api, "credentials_available", None)
        if callable(available):
            try:
                if not available():
                    self.web_key_state.setText("仅本进程（重启即丢）")
            except Exception:
                pass
        self.changed.emit()

    def _update_thinking_web_visibility(self, *_args) -> None:
        thinking_on = str(self.thinking_combo.currentData() or "auto") == "enabled"
        self._effort_row.setVisible(thinking_on)
        web_on = bool(self.web_check.isChecked())
        self._engine_row.setVisible(web_on)
        self._web_results_row.setVisible(web_on)
        self._web_fetch_row.setVisible(web_on)
        self._web_key_row.setVisible(web_on and self._keyed_engine())

    # ---------------------------------------------------------------- 外观控件
    def _rebuild_background_options(self) -> None:
        combo = self.background_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("默认（白色）", "")
        for key, label in theme_names():
            combo.addItem(f"内置 · {label}", f"builtin:{key}")
        value = str(self._background_value or "")
        if value and not value.startswith("builtin:"):
            combo.addItem(f"本地 · {Path(value).name}", value)
        combo.addItem("自定义图片…", _CUSTOM_BACKGROUND)
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _on_background_changed(self, _index: int) -> None:
        data = str(self.background_combo.currentData() or "")
        if data == _CUSTOM_BACKGROUND:
            path, _selected = QFileDialog.getOpenFileName(
                self,
                "选择对话窗背景",
                str(Path.home()),
                "图片 (*.png *.jpg *.jpeg *.webp *.bmp)",
            )
            if not path:
                self._rebuild_background_options()
                return
            data = str(Path(path).resolve())
            self._background_value = data
            self._rebuild_background_options()
        else:
            self._background_value = data
        self._on_changed()

    def _accent_value(self) -> str:
        text = self.accent_edit.text().strip()
        if not text:
            return ""
        if not text.startswith("#"):
            text = "#" + text
        return text.lower() if _HEX.match(text).hasMatch() else ""

    def _on_accent_finished(self) -> None:
        text = self.accent_edit.text().strip()
        if text and not self._accent_value():
            self.accent_edit.setText(str(self._initial.get("chat_accent") or ""))
            return
        self._on_changed()

    def _pick_accent(self) -> None:
        current = self._accent_value() or "#326de6"
        color = QColorDialog.getColor(QColor(current), self, "选择强调色")
        if not color.isValid():
            return
        self.accent_edit.setText(color.name())
        self._on_changed()

    def _reset_accent(self) -> None:
        self.accent_edit.clear()
        self._on_changed()

    # ---------------------------------------------------------------- 主动识屏
    def _sync_proactive_visibility(self, *_args) -> None:
        row = getattr(self, "_proactive_idle_row", None)
        if row is not None:
            row.setEnabled(bool(self.proactive_idle_check.isChecked()))

    def _foreground_window(self):
        features = getattr(self.host, "features", None)
        ports = getattr(features, "ports", None) if features is not None else None
        screen = getattr(ports, "screen", None) if ports is not None else None
        getter = getattr(screen, "foreground_window", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _add_from_foreground(self) -> None:
        self.proactive_add_button.setEnabled(False)
        self.proactive_add_button.setText("3 秒后采样…")
        QTimer.singleShot(3000, self._sample_foreground)

    def _sample_foreground(self) -> None:
        self.proactive_add_button.setEnabled(True)
        self.proactive_add_button.setText("从当前前台窗口添加…")
        info = self._foreground_window()
        process = str(getattr(info, "process", "") or "") if info is not None else ""
        title = str(getattr(info, "title", "") or "") if info is not None else ""
        if not process and not title:
            QMessageBox.information(
                self, "主动识屏", "没读到前台窗口。先切到目标软件，再点一次。"
            )
            return
        box = QMessageBox(self)
        box.setWindowTitle("主动识屏")
        box.setText("把当前前台窗口加入白名单：")
        box.setInformativeText(f"{process or '（未知进程）'} | {title or '（无标题）'}")
        by_process = (
            box.addButton("按软件", QMessageBox.ButtonRole.AcceptRole) if process else None
        )
        by_title = (
            box.addButton("按标题关键词", QMessageBox.ButtonRole.AcceptRole) if title else None
        )
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        rule = ""
        if by_process is not None and clicked is by_process:
            rule = process
        elif by_title is not None and clicked is by_title:
            keyword = title.strip()[:40]
            rule = f"title:*{keyword}*" if keyword else ""
        self._append_rule(rule)

    def _append_rule(self, rule: str) -> None:
        rule = str(rule or "").strip()
        if not rule:
            return
        existing = [
            line.strip()
            for line in self.whitelist_edit.toPlainText().splitlines()
            if line.strip()
        ]
        if rule in existing:
            return
        self.whitelist_edit.setPlainText("\n".join(existing + [rule]))

    # ---------------------------------------------------------------- 数据
    def api(self):
        features = getattr(self.host, "features", None)
        ports = getattr(features, "ports", None)
        return getattr(ports, "api", None) if ports is not None else None

    def config(self):
        return self.host.config

    def apply_preview(self) -> None:
        chat = getattr(self.host, "chat", None)
        apply = getattr(chat, "apply_appearance", None)
        if not callable(apply):
            return
        apply(
            background=self._background_value,
            accent=self._accent_value(),
            always_on_top=bool(self.on_top_check.isChecked()),
            follow=bool(self.follow_check.isChecked()),
        )

    def _on_changed(self, *_args) -> None:
        config = self.config()
        config.set("ai_service", str(self.service_combo.currentData() or ""))
        config.set("ai_model", self.model_edit.text().strip())
        persona_id = str(self.persona_combo.currentData() or "")
        if persona_id == CUSTOM_PERSONA:
            config.set("ai_system_prompt", self.prompt_edit.toPlainText().strip())
        config.set("ai_persona", persona_id)
        config.set("ai_vision_enabled", bool(self.vision_check.isChecked()))
        config.set("ai_temperature", float(self.temperature_spin.value()))
        config.set("ai_look_screen_auto_open", bool(self.auto_open_check.isChecked()))
        config.set("ai_thinking", str(self.thinking_combo.currentData() or "auto"))
        config.set(
            "ai_reasoning_effort", str(self.effort_combo.currentData() or "high")
        )
        config.set("ai_animation_enabled", bool(self.anim_check.isChecked()))
        config.set(
            "ai_animation_thinking", bool(self.anim_thinking_check.isChecked())
        )
        config.set("ai_animation_min_seconds", float(self.anim_min_spin.value()))
        config.set("ai_voice_reply", bool(self.voice_reply_check.isChecked()))
        config.set("ai_memory_enabled", bool(self.memory_check.isChecked()))
        config.set("ai_memory_inject", int(self.memory_inject.value()))
        config.set("ai_memory_max_entries", int(self.memory_max.value()))
        config.set("ai_web_search", bool(self.web_check.isChecked()))
        config.set("ai_web_engine", str(self.engine_combo.currentData() or "baidu"))
        config.set("ai_web_results", int(self.web_results_spin.value()))
        config.set("ai_web_fetch", bool(self.web_fetch_check.isChecked()))
        config.set("chat_background", self._background_value)
        config.set("chat_accent", self._accent_value())
        config.set(
            "chat_font_family",
            ""
            if self.default_font_check.isChecked()
            else self.font_combo.currentFont().family(),
        )
        config.set("chat_font_size", int(self.chat_font_size.value()))
        config.set("chat_follow_pet", bool(self.follow_check.isChecked()))
        config.set("chat_always_on_top", bool(self.on_top_check.isChecked()))
        config.set(
            "ai_proactive_enabled", bool(self.proactive_check.isChecked())
        )
        config.set(
            "ai_proactive_whitelist",
            [
                line.strip()
                for line in self.whitelist_edit.toPlainText().splitlines()
                if line.strip()
            ],
        )
        config.set(
            "ai_proactive_dwell_seconds", int(self.proactive_dwell_spin.value())
        )
        config.set(
            "ai_proactive_change_threshold", int(self.proactive_threshold_spin.value())
        )
        config.set("ai_proactive_daily_cap", int(self.proactive_cap_spin.value()))
        config.set(
            "ai_proactive_cooldown_seconds", int(self.proactive_cooldown_spin.value())
        )
        config.set(
            "ai_proactive_min_interval_seconds",
            int(self.proactive_interval_spin.value()),
        )
        config.set(
            "ai_proactive_require_idle", bool(self.proactive_idle_check.isChecked())
        )
        config.set(
            "ai_proactive_min_idle_seconds", int(self.proactive_idle_spin.value())
        )
        config.set("ai_proactive_dry_run", bool(self.proactive_dry_check.isChecked()))
        config.set("ai_proactive_pre_cue", bool(self.proactive_cue_check.isChecked()))
        config.set(
            "ai_proactive_sync_chat", bool(self.proactive_sync_check.isChecked())
        )
        self.apply_preview()
        self.changed.emit()

    def dirty(self) -> bool:
        config = self.config()
        return any(
            str(config.get(key)) != str(value)
            for key, value in self._initial.items()
        )

    def _snapshot(self) -> dict:
        return {key: self.config().get(key) for key in self._KEYS}

    def confirm_save(self) -> None:
        # 当前人格卡有未保存的提示词/名称编辑时，整窗保存也应落卡；
        # 否则“改内置提示词 → 直接点保存退出”会静默丢失。
        persona_id = str(self.persona_combo.currentData() or "")
        preset = self._persona()
        if persona_id not in ("", CUSTOM_PERSONA) and preset is not None:
            prompt = self.prompt_edit.toPlainText().strip()
            name = self.persona_name.text().strip() or preset.name
            if prompt != preset.prompt or name != preset.name:
                self._save_persona()
        self._on_changed()
        self._initial = self._snapshot()

    def discard_changes(self) -> None:
        self._initial = self._snapshot()
        self._background_value = str(self._initial.get("chat_background") or "")
        self._rebuild_background_options()
        self._sync_persona()
        self._sync_thinking_web()
        self._sync_chat_animation()
        self._sync_chat_font()
        self._sync_memory()
        self._sync_proactive()
        self.apply_preview()
        self.changed.emit()

    def _sync_chat_animation(self) -> None:
        config = self.config()
        for check, key, fallback in (
            (self.anim_check, "ai_animation_enabled", True),
            (self.anim_thinking_check, "ai_animation_thinking", True),
        ):
            check.blockSignals(True)
            check.setChecked(bool(config.get(key, fallback)))
            check.blockSignals(False)
        try:
            seconds = float(config.get("ai_animation_min_seconds") or 2.0)
        except (TypeError, ValueError):
            seconds = 2.0
        self.anim_min_spin.blockSignals(True)
        self.anim_min_spin.setValue(max(0.5, min(30.0, seconds)))
        self.anim_min_spin.blockSignals(False)
        self.voice_reply_check.blockSignals(True)
        self.voice_reply_check.setChecked(bool(config.get("ai_voice_reply", False)))
        self.voice_reply_check.blockSignals(False)

    def _persona(self, persona_id: str | None = None) -> PersonaPreset | None:
        key = str(persona_id or self.persona_combo.currentData() or "")
        return self._personas.get(key)

    def _reload_personas(self, select_id: str = "") -> None:
        self._personas = {
            item.id: item for item in self._persona_store.load_all()
        }
        key = str(select_id or "")
        self.persona_combo.blockSignals(True)
        self.persona_combo.clear()
        for preset in self._personas.values():
            self.persona_combo.addItem(preset.name, preset.id)
        self.persona_combo.addItem("自定义提示词", CUSTOM_PERSONA)
        index = self.persona_combo.findData(key)
        self.persona_combo.setCurrentIndex(index if index >= 0 else 0)
        self.persona_combo.blockSignals(False)
        self._sync_persona_rows()

    def _on_persona_changed(self, *_args) -> None:
        self._sync_persona_rows()
        self._on_changed()

    def _sync_persona_rows(self) -> None:
        persona_id = str(self.persona_combo.currentData() or "")
        preset = self._persona(persona_id)
        custom = persona_id == CUSTOM_PERSONA
        self.prompt_edit.blockSignals(True)
        if custom:
            self.prompt_edit.setPlainText(
                str(self.config().get("ai_system_prompt") or "")
            )
        elif preset is not None:
            self.prompt_edit.setPlainText(preset.prompt)
        else:
            self.prompt_edit.setPlainText("")
        self.prompt_edit.blockSignals(False)
        self.prompt_edit.setEnabled(True)
        self.persona_name.blockSignals(True)
        self.persona_name.setText("自定义提示词" if custom else (preset.name if preset else ""))
        self.persona_name.blockSignals(False)
        if custom:
            self.persona_desc.setText(
                "自定义提示词：下面文本框里的内容会发给模型（支持 {name}）；"
                "想保存成可复用的人格卡，点「新建」或「复制当前」。"
            )
        elif preset is not None:
            overridden = (
                preset.id in self._builtin_personas
                and self._persona_store.is_overridden(preset.id)
            )
            suffix = "（用户卡）" if preset.id not in self._builtin_personas else (
                "（已覆盖内置）" if overridden else "（内置）"
            )
            self.persona_desc.setText(f"{preset.description or '人设卡'} {suffix}")
        else:
            self.persona_desc.setText("")
        builtin_overridden = (
            persona_id in self._builtin_personas
            and self._persona_store.is_overridden(persona_id)
        )
        self.persona_copy_button.setEnabled(preset is not None)
        self.persona_rename_button.setEnabled(not custom)
        self.persona_save_button.setEnabled(not custom)
        self.persona_reset_button.setEnabled(builtin_overridden)
        self.persona_delete_button.setEnabled(
            bool(persona_id) and persona_id not in self._builtin_personas
        )

    def _sync_persona(self) -> None:
        config = self.config()
        persona = str(config.get("ai_persona") or "").strip()
        if not persona:
            persona = (
                CUSTOM_PERSONA
                if str(config.get("ai_system_prompt") or "").strip()
                else DEFAULT_PERSONA
            )
        self._reload_personas(persona)

    def _reload_select(self, persona_id: str) -> None:
        self._reload_personas(persona_id)
        self.config().set("ai_persona", persona_id)
        self._on_changed()
        self.changed.emit()

    def _new_persona(self) -> None:
        name, ok = QInputDialog.getText(self, "新建人格卡", "人格名称：")
        if not ok or not str(name or "").strip():
            return
        prompt = self.prompt_edit.toPlainText().strip() or DEFAULT_PROMPT
        preset = PersonaPreset(
            new_user_id(),
            str(name).strip(),
            "自定义人格卡",
            prompt,
            False,
        )
        try:
            saved = self._persona_store.save(preset)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._reload_select(saved.id)

    def _copy_persona(self) -> None:
        preset = self._persona()
        if preset is None:
            return
        copied = PersonaPreset(
            new_user_id(),
            f"{preset.name} 副本"[:32],
            preset.description,
            self.prompt_edit.toPlainText().strip() or preset.prompt,
            False,
        )
        try:
            saved = self._persona_store.save(copied)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._reload_select(saved.id)

    def _rename_persona(self) -> None:
        preset = self._persona()
        if preset is None:
            return
        name, ok = QInputDialog.getText(
            self, "重命名人格卡", "人格名称：", text=self.persona_name.text()
        )
        if not ok or not str(name or "").strip():
            return
        try:
            saved = self._persona_store.save(
                PersonaPreset(
                    preset.id,
                    str(name).strip(),
                    preset.description,
                    self.prompt_edit.toPlainText().strip() or preset.prompt,
                    False,
                )
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._reload_select(saved.id)

    def _save_persona(self) -> None:
        persona_id = str(self.persona_combo.currentData() or "")
        if persona_id == CUSTOM_PERSONA:
            self._on_changed()
            return
        preset = self._persona()
        if preset is None:
            return
        try:
            saved = self._persona_store.save(
                PersonaPreset(
                    preset.id,
                    self.persona_name.text().strip() or preset.name,
                    preset.description,
                    self.prompt_edit.toPlainText().strip(),
                    False,
                )
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._reload_select(saved.id)

    def _reset_persona(self) -> None:
        persona_id = str(self.persona_combo.currentData() or "")
        if persona_id not in self._builtin_personas:
            return
        answer = QMessageBox.question(
            self,
            "恢复内置人格",
            "删除这张卡的覆盖文件，恢复包内原始名称与提示词？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._persona_store.reset_builtin(persona_id)
        self._reload_select(persona_id)

    def _delete_persona(self) -> None:
        persona_id = str(self.persona_combo.currentData() or "")
        if not persona_id or persona_id in self._builtin_personas:
            return
        preset = self._persona(persona_id)
        answer = QMessageBox.question(
            self,
            "删除人格卡",
            f"确定删除「{preset.name if preset else persona_id}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._persona_store.delete(persona_id)
        self._reload_select(DEFAULT_PERSONA)

    def _memory_root(self):
        root = getattr(self.host, "root", None) or getattr(self.config(), "root", None)
        if not root:
            return None
        from petcore import paths

        return paths.feature_data_dir(Path(root), "ai.chat") / "memory"

    def _refresh_memory_status(self) -> None:
        from .memory import MemoryStore

        root = self._memory_root()
        if root is None:
            self.memory_status.setText("记忆目录不可用")
            return
        character = str(self.config().get("character") or "default")
        store = MemoryStore(root, character)
        self.memory_status.setText(
            f"当前角色 {store.count('character')} 条 · 全局 {store.count('global')} 条"
        )

    def _open_memory_dir(self) -> None:
        root = self._memory_root()
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def _clear_memory(self) -> None:
        from .memory import MemoryStore

        root = self._memory_root()
        if root is None:
            return
        character = str(self.config().get("character") or "default")
        removed = MemoryStore(root, character).clear("character")
        self.memory_status.setText(
            f"已清空当前角色记忆（{removed} 条）；全局记忆保留。"
        )

    def _sync_memory(self) -> None:
        config = self.config()
        self.memory_check.blockSignals(True)
        self.memory_check.setChecked(bool(config.get("ai_memory_enabled", True)))
        self.memory_check.blockSignals(False)
        for widget, key, fallback in (
            (self.memory_inject, "ai_memory_inject", 3),
            (self.memory_max, "ai_memory_max_entries", 500),
        ):
            try:
                value = int(config.get(key) or fallback)
            except (TypeError, ValueError):
                value = fallback
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._refresh_memory_status()

    def _sync_font_rows(self, *_args) -> None:
        self.font_combo.setEnabled(not self.default_font_check.isChecked())

    def _sync_chat_font(self) -> None:
        config = self.config()
        family = str(config.get("chat_font_family") or "").strip()
        self.default_font_check.blockSignals(True)
        self.default_font_check.setChecked(not family)
        self.default_font_check.blockSignals(False)
        if family:
            self.font_combo.blockSignals(True)
            self.font_combo.setCurrentFont(QFont(family))
            self.font_combo.blockSignals(False)
        try:
            size = int(config.get("chat_font_size") or 0)
        except (TypeError, ValueError):
            size = 0
        self.chat_font_size.blockSignals(True)
        self.chat_font_size.setValue(max(0, min(24, size)))
        self.chat_font_size.blockSignals(False)
        self._sync_font_rows()

    def _sync_thinking_web(self) -> None:
        """取消后把新控件拉回已恢复的配置（config 已由对话框整体还原）。"""
        config = self.config()
        widgets = (
            (self.thinking_combo, "ai_thinking", "auto"),
            (self.effort_combo, "ai_reasoning_effort", "high"),
            (self.engine_combo, "ai_web_engine", "baidu"),
        )
        for combo, key, fallback in widgets:
            value = str(config.get(key) or fallback)
            index = combo.findData(value)
            combo.blockSignals(True)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
        try:
            results = int(config.get("ai_web_results") or 5)
        except (TypeError, ValueError):
            results = 5
        for check, key, fallback in (
            (self.web_check, "ai_web_search", False),
            (self.web_fetch_check, "ai_web_fetch", True),
        ):
            check.blockSignals(True)
            check.setChecked(bool(config.get(key, fallback)))
            check.blockSignals(False)
        self.web_results_spin.blockSignals(True)
        self.web_results_spin.setValue(results)
        self.web_results_spin.blockSignals(False)
        self.web_key_edit.clear()
        self._refresh_web_key_state()
        self._update_thinking_web_visibility()

    def _sync_proactive(self) -> None:
        """取消后把主动识屏的控件拉回已恢复的配置。"""
        config = self.config()

        def value(key, fallback):
            current = config.get(key, fallback)
            return fallback if current is None else current

        for check, key, fallback in (
            (self.proactive_check, "ai_proactive_enabled", False),
            (self.proactive_idle_check, "ai_proactive_require_idle", False),
            (self.proactive_dry_check, "ai_proactive_dry_run", False),
            (self.proactive_cue_check, "ai_proactive_pre_cue", True),
            (self.proactive_sync_check, "ai_proactive_sync_chat", True),
        ):
            check.blockSignals(True)
            check.setChecked(bool(value(key, fallback)))
            check.blockSignals(False)
        for spin, key, fallback in (
            (self.proactive_dwell_spin, "ai_proactive_dwell_seconds", 45),
            (self.proactive_threshold_spin, "ai_proactive_change_threshold", 8),
            (self.proactive_cap_spin, "ai_proactive_daily_cap", 15),
            (self.proactive_cooldown_spin, "ai_proactive_cooldown_seconds", 300),
            (self.proactive_interval_spin, "ai_proactive_min_interval_seconds", 60),
            (self.proactive_idle_spin, "ai_proactive_min_idle_seconds", 30),
        ):
            try:
                number = int(value(key, fallback))
            except (TypeError, ValueError):
                number = int(fallback)
            spin.blockSignals(True)
            spin.setValue(number)
            spin.blockSignals(False)
        self.whitelist_edit.blockSignals(True)
        self.whitelist_edit.setPlainText(
            "\n".join(str(rule) for rule in (value("ai_proactive_whitelist", []) or []))
        )
        self.whitelist_edit.blockSignals(False)
        self._sync_proactive_visibility()

    def keywords(self) -> list[str]:
        return [
            "AI",
            "对话",
            "聊天",
            "模型",
            "提示词",
            "人设",
            "人格",
            "人格卡",
            "自定义人设",
            "字体",
            "字号",
            "对话窗字体",
            "长期记忆",
            "记忆",
            "温度",
            "截图",
            "视觉",
            "识屏",
            "看看屏幕",
            "截图提问",
            "自动打开",
            "深度思考",
            "思考",
            "推理",
            "思维链",
            "思考强度",
            "联网",
            "搜索",
            "搜索引擎",
            "百度",
            "博查",
            "Tavily",
            "API Key",
            "网页正文",
            "背景",
            "主题",
            "强调色",
            "颜色",
            "跟随",
            "置顶",
            "主动识屏",
            "识屏",
            "白名单",
            "停留",
            "去重",
            "画面指纹",
            "每日上限",
            "冷却",
            "最小间隔",
            "闲置",
            "演练",
            "dry-run",
            "chat",
        ]
