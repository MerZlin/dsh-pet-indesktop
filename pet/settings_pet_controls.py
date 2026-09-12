"""Pet-page control builders for the modern settings dialog (host-based).

ModernSettingsDialog retains a thin delegation method of the same name, so
existing callers and test patches keep working unchanged.
"""

from __future__ import annotations

import json
import sys

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import autostart as autostart_mod
from . import catalog
from .agent_link import AgentLinkManager
from .config import (
    DEFAULT_CONTEXT_MENU_APPEARANCE,
    DEFAULT_MENU_EASTER_EGG,
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    _float_or_default,
)
from .context_menus.icons import vector_widget_icon
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset
from .persona_phrases import PUBLIC_DIALOGUE_EVENTS, default_phrases, phrase_keys
from .persona_template import build_persona_template
from .report_gates import REPORT_GATE_DEFAULTS, REPORT_GATE_KEYS
from .settings_widgets import (
    AUDIO_NAME_FILTER,
    BrowserDoubleSpinBox,
    BrowserSpinBox,
    ClickSoundPackPicker,
    ColorPicker,
    ModernSelect,
    ProbabilitySlider,
    ResourcePathPicker,
    ResponsiveToggleActionRow,
    SettingRow,
    ToggleSwitch,
    _line_edit,
)
from .speech_bubble import BUBBLE_STYLE_PRESETS

def build_pet_controls(host) -> None:
    from .modern_settings_dialog import dialogue_params_hint
    host.scale_combo = ModernSelect(host, width=132)
    current_scale = float(host.config.get("scale", catalog.DEFAULT_SCALE))
    scales = list(catalog.SCALE_STEPS)
    if not any(abs(current_scale - value) < 0.001 for value in scales):
        scales.append(current_scale)
        scales.sort()
    for scale in scales:
        host.scale_combo.addItem(f"{int(round(catalog.CANVAS_W * scale))} px", scale)
    host.scale_combo.setCurrentIndex(host.scale_combo.findData(current_scale))

    # 生小肥鱼尺寸策略：默认继承主肥鱼大小；关闭后使用 spawn_scale 独立选择。
    host.spawn_inherit_size_check = ToggleSwitch(host)
    host.spawn_inherit_size_check.setChecked(bool(host.config.get("spawn_inherit_size", True)))
    host.spawn_scale_combo = ModernSelect(host, width=132)
    current_spawn_scale = float(host.config.get("spawn_scale", catalog.DEFAULT_SCALE))
    spawn_scales = list(catalog.SCALE_STEPS)
    if not any(abs(current_spawn_scale - value) < 0.001 for value in spawn_scales):
        spawn_scales.append(current_spawn_scale)
        spawn_scales.sort()
    for scale in spawn_scales:
        host.spawn_scale_combo.addItem(f"{int(round(catalog.CANVAS_W * scale))} px", scale)
    host.spawn_scale_combo.setCurrentIndex(host.spawn_scale_combo.findData(current_spawn_scale))
    host.spawn_inherit_dynamic_island_check = ToggleSwitch(host)
    host.spawn_inherit_dynamic_island_check.setChecked(
        bool(host.config.get("spawn_inherit_dynamic_island", False))
    )
    host.clear_spawned_pets_btn = QPushButton("一键退出…", host)
    host.clear_spawned_pets_btn.clicked.connect(host._on_clear_spawned_pets)
    if host.config.instance_id:
        # 子肥鱼不能关闭主肥鱼进程，只允许主肥鱼执行退出操作。
        host.clear_spawned_pets_btn.setEnabled(False)
        host.clear_spawned_pets_btn.setToolTip("请在主肥鱼的设置里操作")

    host.on_top_check = ToggleSwitch(host)
    host.on_top_check.setChecked(bool(host.config.get("on_top", True)))
    host.no_move_check = ToggleSwitch(host)
    host.no_move_check.setChecked(bool(host.config.get("no_move", False)))
    host.mouse_through_check = ToggleSwitch(host)
    host.mouse_through_check.setChecked(bool(host.config.get("mouse_through", False)))
    # Windows 专属的光标隐藏穿透仅在 Windows 创建，避免非 Windows 未入布局时游离到窗口左上角。
    host.cursor_hidden_passthrough_check = None
    if sys.platform == "win32":
        host.cursor_hidden_passthrough_check = ToggleSwitch(host)
        host.cursor_hidden_passthrough_check.setChecked(bool(host.config.get("cursor_hidden_passthrough", True)))
    host.drag_physics_check = ToggleSwitch(host)
    host.drag_physics_check.setChecked(bool(host.config.get("drag_physics", False)))
    host.single_process_spawn_check = ToggleSwitch(host)
    host.single_process_spawn_check.setChecked(bool(host.config.get("experimental_single_process_spawn", False)))

    # 甩出力度四档：gentle (轻柔) / standard (标准) / strong (强力) / crazy (疯狂)
    host.throw_strength_select = ModernSelect(host, width=132)
    host.throw_strength_select.addItem("轻柔", "gentle")
    host.throw_strength_select.addItem("标准", "standard")
    host.throw_strength_select.addItem("强力", "strong")
    host.throw_strength_select.addItem("疯狂", "crazy")
    current_strength = str(host.config.get("throw_strength", "standard") or "standard")
    host.throw_strength_select.setCurrentData(current_strength if current_strength in {"gentle", "standard", "strong", "crazy"} else "standard")

    # 弹弓弹射开关
    host.slingshot_check = ToggleSwitch(host)
    host.slingshot_check.setChecked(bool(host.config.get("slingshot_enabled", True)))

    # 多开碰撞设置
    host.collision_enabled_check = ToggleSwitch(host)
    host.collision_enabled_check.setChecked(bool(host.config.get("collision_enabled", True)))
    host.collision_restitution_spin = BrowserDoubleSpinBox(host)
    host.collision_restitution_spin.setRange(0.0, 1.0)
    host.collision_restitution_spin.setSingleStep(0.05)
    host.collision_restitution_spin.setDecimals(2)
    host.collision_restitution_spin.setValue(float(_float_or_default(host.config.get("collision_restitution", 0.82), 0.82, 0.0, 1.0)))
    host.collision_friction_spin = BrowserDoubleSpinBox(host)
    host.collision_friction_spin.setRange(0.0, 0.30)
    host.collision_friction_spin.setSingleStep(0.01)
    host.collision_friction_spin.setDecimals(2)
    host.collision_friction_spin.setValue(float(_float_or_default(host.config.get("collision_friction", 0.08), 0.08, 0.0, 0.30)))
    host.collision_mass_scale_spin = BrowserDoubleSpinBox(host)
    host.collision_mass_scale_spin.setRange(0.5, 2.0)
    host.collision_mass_scale_spin.setSingleStep(0.1)
    host.collision_mass_scale_spin.setDecimals(2)
    host.collision_mass_scale_spin.setValue(float(_float_or_default(host.config.get("collision_mass_scale", 1.0), 1.0, 0.5, 2.0)))
    host.collision_impulse_cap_spin = BrowserDoubleSpinBox(host)
    host.collision_impulse_cap_spin.setRange(1000.0, 12000.0)
    host.collision_impulse_cap_spin.setSingleStep(500.0)
    host.collision_impulse_cap_spin.setDecimals(0)
    host.collision_impulse_cap_spin.setValue(float(_float_or_default(host.config.get("collision_impulse_cap", 9000.0), 9000.0, 1000.0, 12000.0)))
    host.collision_sound_check = ToggleSwitch(host)
    host.collision_sound_check.setChecked(bool(host.config.get("collision_sound_enabled", True)))
    host.collision_sound_volume_spin = BrowserSpinBox(host)
    host.collision_sound_volume_spin.setRange(0, 100)
    host.collision_sound_volume_spin.setSuffix(" %")
    collision_sound_vol = float(host.config.get("collision_sound_volume", 0.70))
    host.collision_sound_volume_spin.setValue(int(round(collision_sound_vol * 100)))

    host.lock_position_check = ToggleSwitch(host)
    host.lock_position_check.setChecked(bool(host.config.get("lock_position", False)))
    host.shift_drag_check = ToggleSwitch(host)
    host.shift_drag_check.setChecked(bool(host.config.get("shift_drag", False)))
    host.pet_opacity_spin = BrowserSpinBox(host)
    host.pet_opacity_spin.setRange(10, 100)
    host.pet_opacity_spin.setSuffix(" %")
    host.pet_opacity_spin.setValue(int(_float_or_default(host.config.get("pet_opacity", 100), 100, 10, 100)))
    host.autostart_check = ToggleSwitch(host)
    host._autostart_initial = autostart_mod.is_enabled()
    host.autostart_check.setChecked(host._autostart_initial)
    if host.config.instance_id:
        host.autostart_check.setEnabled(False)
        host.autostart_check.setToolTip("仅主桌宠可设置")
    host.dock_icon_check = None
    if sys.platform == "darwin":
        host.dock_icon_check = ToggleSwitch(host)
        host.dock_icon_check.setChecked(bool(host.config.get("show_dock_icon", True)))

    # 点击音效控件群
    host.click_sound_check = ToggleSwitch(host)
    host.click_sound_check.setChecked(bool(host.config.get("click_sound_enabled", True)))
    host.click_sound_picker = ClickSoundPackPicker(
        host.config.get("click_sound_pack"),
        parent=host,
    )
    host.click_sound_volume_spin = BrowserSpinBox(host)
    host.click_sound_volume_spin.setRange(0, 100)
    host.click_sound_volume_spin.setSuffix(" %")
    click_vol = float(host.config.get("click_sound_volume", 0.70))
    host.click_sound_volume_spin.setValue(int(round(click_vol * 100)))

    host.click_sound_preview_btn = QPushButton("试听", host)
    host.click_sound_preview_btn.setIcon(vector_widget_icon(host, "sound", 14))
    host.click_sound_preview_btn.setFixedWidth(72)
    host.click_sound_preview_btn.clicked.connect(host._preview_click_sound)

    host.click_sound_check.toggled.connect(host._update_click_sound_controls)
    # 音效开关即时生效：对话框的批量写回发生在关闭时，但声音开关是即时
    # 听觉反馈——用户关掉后期望立刻静音，而不是等关对话框。
    host.click_sound_check.toggled.connect(host._apply_click_sound_enabled_now)
    host.click_balance_check = None
    if host.include_ai:
        host.click_balance_check = ToggleSwitch(host)
        host.click_balance_check.setChecked(bool(host.config.get("click_show_balance", False)))
    host.click_self_talk_check = ToggleSwitch(host)
    host.click_self_talk_check.setChecked(bool(host.config.get("click_show_self_talk", False)))
    host.music_sing_check = ToggleSwitch(host)
    host.music_sing_check.setChecked(bool(host.config.get("music_sing_enabled", False)))
    host.golden_spin_click_check = ToggleSwitch(host)
    host.golden_spin_click_check.setChecked(bool(host.config.get("golden_spin_on_click", False)))
    host.golden_spin_direct_check = ToggleSwitch(host)
    host.golden_spin_direct_check.setChecked(bool(host.config.get("golden_spin_direct", False)))
    host.edge_probe_check = ToggleSwitch(host)
    host.edge_probe_check.setChecked(bool(host.config.get("edge_probe_enabled", False)))
    host.balance_refresh_spin = None
    host.balance_tier_mode_select = None
    host.balance_tier_peak_edit = None
    host.balance_tier_idle_edit = None
    host.balance_tier_color_check = None
    if host.include_ai:
        host.balance_refresh_spin = BrowserSpinBox(host)
        host.balance_refresh_spin.setRange(0, 1440)
        host.balance_refresh_spin.setSuffix(" 分钟")
        host.balance_refresh_spin.setValue(int(host.config.get("balance_refresh_minutes", 0) or 0))
        host.balance_tier_mode_select = ModernSelect(host, width=180)
        host.balance_tier_mode_select.addItem("空闲 / 高峰（默认）", "default")
        host.balance_tier_mode_select.addItem("梁文谷 / 梁文峰", "liangwen")
        host.balance_tier_mode_select.addItem("自定义", "custom")
        host.balance_tier_mode_select.setCurrentData(
            str(host.config.get("balance_tier_labels_mode", "default") or "default")
        )
        host.balance_tier_peak_edit = QLineEdit(host)
        host.balance_tier_peak_edit.setPlaceholderText("高峰文本，例如：梁文峰")
        host.balance_tier_peak_edit.setText(str(host.config.get("balance_tier_label_peak", "") or ""))
        host.balance_tier_idle_edit = QLineEdit(host)
        host.balance_tier_idle_edit.setPlaceholderText("空闲文本，例如：梁文谷")
        host.balance_tier_idle_edit.setText(str(host.config.get("balance_tier_label_idle", "") or ""))
        host.balance_tier_color_check = ToggleSwitch(host)
        host.balance_tier_color_check.setChecked(bool(host.config.get("balance_tier_color_enabled", True)))
    host.auto_hide_fullscreen_check = None
    host.stream_capture_check = None
    if sys.platform == "win32":
        host.auto_hide_fullscreen_check = ToggleSwitch(host)
        host.auto_hide_fullscreen_check.setChecked(bool(host.config.get("auto_hide_fullscreen", True)))
        host.stream_capture_check = ToggleSwitch(host)
        host.stream_capture_check.setChecked(bool(host.config.get("stream_capture_mode", False)))

    host.speed_select = ModernSelect(host, width=112)
    current_speed = float(host.config.get("playback_speed", 1.0))
    speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
    if not any(abs(current_speed - value) < 0.001 for value in speeds):
        speeds.append(current_speed)
        speeds.sort()
    for speed in speeds:
        host.speed_select.addItem(f"{speed:g}x", speed)
    host.speed_select.setCurrentData(current_speed)
    host.gap_spin = BrowserDoubleSpinBox(host)
    host.gap_spin.setRange(0.0, 3600.0)
    host.gap_spin.setSingleStep(0.5)
    host.gap_spin.setDecimals(1)
    host.gap_spin.setSuffix(" 秒")
    host.gap_spin.setValue(float(host.config.get("animation_gap_seconds", 0.0)))

    host.self_talk_check = ToggleSwitch(host)
    host.self_talk_check.setChecked(bool(host.config.get("self_talk_enabled", False)))
    host.idle_low_fps_check = ToggleSwitch(host)
    host.idle_low_fps_check.setChecked(bool(host.config.get("idle_low_fps_enabled", False)))
    host.self_talk_duration_spin = BrowserDoubleSpinBox(host)
    host.self_talk_duration_spin.setRange(1.0, 300.0)
    host.self_talk_duration_spin.setSingleStep(0.5)
    host.self_talk_duration_spin.setDecimals(1)
    host.self_talk_duration_spin.setSuffix(" 秒")
    host.self_talk_duration_spin.setValue(float(host.config.get(
        "self_talk_duration_seconds", DEFAULT_SELF_TALK_DURATION_SECONDS
    )))
    host.bubble_style_select = ModernSelect(host, width=172)
    for value, preset in BUBBLE_STYLE_PRESETS.items():
        host.bubble_style_select.addItem(str(preset["label"]), value)
    host.bubble_style_select.setCurrentData(
        str(host.config.get("self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE))
    )
    host.min_spin = BrowserDoubleSpinBox(host)
    host.max_spin = BrowserDoubleSpinBox(host)
    for spin, value in (
        (host.min_spin, host.config.get("self_talk_min_interval", DEFAULT_SELF_TALK_MIN_INTERVAL)),
        (host.max_spin, host.config.get("self_talk_max_interval", DEFAULT_SELF_TALK_MAX_INTERVAL)),
    ):
        spin.setRange(5.0, 3600.0)
        spin.setDecimals(0)
        spin.setSuffix(" 秒")
        spin.setValue(float(value))
    host.texts_edit = QPlainTextEdit(host)
    host.texts_edit.setMinimumSize(240, 82)
    host.texts_edit.setMaximumHeight(170)
    texts = host.config.get("self_talk_texts", DEFAULT_SELF_TALK_TEXTS)
    host.texts_edit.setPlainText("\n".join(str(item) for item in texts))
    host.self_talk_image_dir_picker = ResourcePathPicker(
        str(host.config.get("self_talk_image_dir", "") or ""),
        directory=True,
        image_preview=True,
        parent=host,
    )
    host.self_talk_image_scale_spin = BrowserSpinBox(host)
    host.self_talk_image_scale_spin.setRange(50, 300)
    host.self_talk_image_scale_spin.setSuffix(" %")
    host.self_talk_image_scale_spin.setValue(int(host.config.get("self_talk_image_scale", 100)))
    host.click_talk_bindings_btn = QPushButton("编辑…", host)
    host.click_talk_bindings_btn.setObjectName("clickTalkBindingsButton")
    host.click_talk_bindings_btn.clicked.connect(host._open_click_talk_bindings)

    # Agent 联动：agent_link 配置（思考文案编辑已移除；后续音效/专属层共用此引用）
    agent_link_cfg = host.config.get("agent_link", {})

    host.dialogue_mode_select = ModernSelect(host, width=190)
    for label, value in (("默认模式", "legacy"), ("鲸鱼娘女仆模式", "whale_maid"), ("自定义台词", "custom")):
        host.dialogue_mode_select.addItem(label, value)
    host.dialogue_mode_select.setCurrentData(str(host.config.get("dialogue_mode", "legacy") or "legacy"))
    # 统一预设：global 层是编辑区默认面（flat 旧结构 = global；双层取 global）
    configured_phrases = host.config.get("dialogue_phrases", {})
    if isinstance(configured_phrases, dict) and ("global" in configured_phrases or "agents" in configured_phrases):
        preset_global = configured_phrases.get("global")
        if not isinstance(preset_global, dict):
            preset_global = {}
        preset_agents = configured_phrases.get("agents")
        if not isinstance(preset_agents, dict):
            preset_agents = {}
    else:
        preset_global = configured_phrases if isinstance(configured_phrases, dict) else {}
        preset_agents = {}

    # 逐 Agent 覆盖层（delta）编辑缓冲：scope（""=global / agent_key）→ {事件: 文本}
    host.dialogue_scope_select = ModernSelect(host, width=190)
    host.dialogue_scope_select.addItem("默认（全局文案）", "")
    for agent_key, agent_name in AgentLinkManager.AGENT_NAMES.items():
        host.dialogue_scope_select.addItem(f"{agent_name} 专属文案", agent_key)
    # 自定义 Agent 也支持专属层（跟随 agent_link.custom_agents）
    for item in (agent_link_cfg.get("custom_agents") or []):
        if isinstance(item, dict) and str(item.get("key") or "").strip():
            host.dialogue_scope_select.addItem(
                f"{str(item.get('name') or item.get('key'))} 专属文案", str(item["key"]).strip())
    # 记住并恢复上次编辑层（设置页保存时写 dialogue_last_scope；未知值回落全局）
    initial_scope = str(host.config.get("dialogue_last_scope", "") or "")
    if host.dialogue_scope_select.findData(initial_scope) < 0:
        initial_scope = ""
    host.dialogue_scope_select.setCurrentData(initial_scope)
    host._dialogue_scope = initial_scope  # 当前编辑层（""=global）

    host.dialogue_phrase_edits: dict[str, QPlainTextEdit] = {}
    for key in phrase_keys():
        edit = QPlainTextEdit(host)
        raw_value = preset_global.get(key, "")
        if isinstance(raw_value, list):
            edit.setPlainText("\n".join(str(item) for item in raw_value if isinstance(item, str)))
        else:
            edit.setPlainText(str(raw_value or ""))
        edit.setMinimumHeight(48)
        edit.setMaximumHeight(120)
        hint = dialogue_params_hint(key)
        if hint:
            placeholder = "留空使用基础模式台词；本事件支持：" + hint
        else:
            placeholder = "留空使用基础模式台词；本事件无可替换参数"
        edit.setPlaceholderText(placeholder)
        host.dialogue_phrase_edits[key] = edit

    # 每个 scope 的编辑缓冲快照（切换时 flush/load）
    host._dialogue_scope_buffer: dict[str, dict[str, str]] = {}
    host._dialogue_scope_buffer[""] = {
        key: edit.toPlainText() for key, edit in host.dialogue_phrase_edits.items()
    }
    for agent_key, agent_events in preset_agents.items():
        if not isinstance(agent_events, dict):
            continue
        host._dialogue_scope_buffer[str(agent_key)] = {
            key: ("\n".join(str(i) for i in value) if isinstance(value, list) else str(value or ""))
            for key, value in agent_events.items()
        }
    if host._dialogue_scope:
        # 恢复上次编辑层：把该层缓冲载入编辑框（行可见性等对话框建完行后再收敛）
        _load_dialogue_scope_into_editors(host, host._dialogue_scope)
    host.dialogue_scope_select.currentIndexChanged.connect(host._on_dialogue_scope_changed)

    host.dialogue_template_import_edit = QPlainTextEdit(host)
    host.dialogue_template_import_edit.setObjectName("dialogueTemplateImportEdit")
    host.dialogue_template_import_edit.setPlaceholderText(
        "粘贴 persona-phrases/v1 JSON 模板到这里，然后点击“导入模板”"
    )
    host.dialogue_template_import_edit.setMinimumHeight(92)
    host.dialogue_template_import_edit.setMaximumHeight(180)
    host.dialogue_template_export_btn = QPushButton("一键复制模板", host)
    host.dialogue_template_export_btn.setObjectName("dialogueTemplateExportButton")
    host.dialogue_template_export_btn.clicked.connect(host._export_dialogue_template)
    host.dialogue_template_import_btn = QPushButton("导入模板", host)
    host.dialogue_template_import_btn.setObjectName("dialogueTemplateImportButton")
    host.dialogue_template_import_btn.clicked.connect(host._import_dialogue_template_json)
    host.dialogue_template_actions = QWidget(host)
    dialogue_template_actions_layout = QVBoxLayout(host.dialogue_template_actions)
    dialogue_template_actions_layout.setContentsMargins(0, 0, 0, 0)
    dialogue_template_actions_layout.setSpacing(6)
    dialogue_template_actions_layout.addWidget(host.dialogue_template_import_edit)
    dialogue_template_buttons = QHBoxLayout()
    dialogue_template_buttons.setContentsMargins(0, 0, 0, 0)
    dialogue_template_buttons.addWidget(host.dialogue_template_export_btn)
    dialogue_template_buttons.addWidget(host.dialogue_template_import_btn)
    dialogue_template_actions_layout.addLayout(dialogue_template_buttons)
    host.agent_sound_check = ToggleSwitch(host)
    host.agent_sound_check.setChecked(bool(agent_link_cfg.get("sound_enabled", False)))

    # 辅助构建包含“开关+路径选择+试听”的组合控件
    def _build_agent_event_row(evt_key: str, default_builtin: str) -> tuple[QWidget, ToggleSwitch, ResourcePathPicker, QPushButton]:
        toggle = ToggleSwitch(host)
        toggle.setChecked(bool(agent_link_cfg.get(f"sound_{evt_key}_enabled", True)))
        path_val = str(agent_link_cfg.get(f"sound_{evt_key}_path") or default_builtin)
        picker = ResourcePathPicker(path_val, name_filter=AUDIO_NAME_FILTER, parent=host)
        preview_btn = QPushButton("试听", host)
        preview_btn.setIcon(vector_widget_icon(host, "sound", 14))
        preview_btn.setFixedWidth(72)
        preview_btn.clicked.connect(lambda _, k=evt_key: host._preview_agent_sound(k))
        container = ResponsiveToggleActionRow(toggle, picker, preview_btn, host)
        return container, toggle, picker, preview_btn

    (host.agent_sound_start_widget, host.agent_sound_start_check,
     host.agent_sound_start_picker, host.agent_sound_start_preview) = _build_agent_event_row("start", "builtin:agent-start")

    (host.agent_sound_done_widget, host.agent_sound_done_check,
     host.agent_sound_done_picker, host.agent_sound_done_preview) = _build_agent_event_row("done", "builtin:agent-done")

    (host.agent_sound_error_widget, host.agent_sound_error_check,
     host.agent_sound_error_picker, host.agent_sound_error_preview) = _build_agent_event_row("error", "builtin:agent-error")

    host.agent_sound_volume_spin = BrowserSpinBox(host)
    host.agent_sound_volume_spin.setRange(0, 100)
    host.agent_sound_volume_spin.setSuffix(" %")
    agent_vol = float(agent_link_cfg.get("sound_volume", 0.65))
    host.agent_sound_volume_spin.setValue(int(round(agent_vol * 100)))

    host.agent_sound_cooldown_spin = BrowserDoubleSpinBox(host)
    host.agent_sound_cooldown_spin.setRange(0.0, 30.0)
    host.agent_sound_cooldown_spin.setSingleStep(0.5)
    host.agent_sound_cooldown_spin.setDecimals(1)
    host.agent_sound_cooldown_spin.setSuffix(" 秒")
    host.agent_sound_cooldown_spin.setValue(float(agent_link_cfg.get("sound_cooldown_seconds", 2.0)))

    # 事件气泡触发概率（0.00–1.00 滑块，无开关）：按事件聚合类别逐类调通过概率。
    # 0.00 = 该类完全不汇报（等同关闭），1.00 = 全部汇报。滑块是唯一控制项，
    # 右键菜单只给 0/1 两端快捷入口；键名即门名（见 pet/report_gates.py）。
    gates_cfg = agent_link_cfg.get("report_gates")
    if not isinstance(gates_cfg, dict):
        gates_cfg = {}
    host.report_gate_sliders = {}
    for gate in REPORT_GATE_KEYS:
        slider = ProbabilitySlider(
            host, value=float(gates_cfg.get(gate, REPORT_GATE_DEFAULTS[gate]))
        )
        slider.setObjectName(f"reportGateSlider_{gate}")
        host.report_gate_sliders[gate] = slider

    host.agent_sound_check.toggled.connect(host._update_agent_sound_controls)
    host.agent_sound_check.toggled.connect(host._apply_agent_sound_enabled_now)
    host.agent_sound_start_check.toggled.connect(lambda: host._update_agent_sound_subcontrols())
    host.agent_sound_done_check.toggled.connect(lambda: host._update_agent_sound_subcontrols())
    host.agent_sound_error_check.toggled.connect(lambda: host._update_agent_sound_subcontrols())

    # 待办提醒：偏好两键（条目在右键菜单「待办提醒」面板中管理）
    host.todo_reminder_check = ToggleSwitch(host)
    host.todo_reminder_check.setChecked(bool(host.config.get("todo_reminder_enabled", True)))
    host.todo_reminder_lead_spin = BrowserSpinBox(host)
    host.todo_reminder_lead_spin.setRange(0, 60)
    host.todo_reminder_lead_spin.setSuffix(" 分钟")
    host.todo_reminder_lead_spin.setValue(int(host.config.get("todo_reminder_lead_minutes", 5) or 0))

    appearance = host.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
    host.menu_theme_select = ModernSelect(host, width=132)
    for label, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
        host.menu_theme_select.addItem(label, value)
    host.menu_theme_select.setCurrentData(appearance.get("theme", "system"))
    host.menu_density_select = ModernSelect(host, width=132)
    for label, value in (("紧凑", "compact"), ("标准", "standard"), ("宽松", "spacious")):
        host.menu_density_select.addItem(label, value)
    host.menu_density_select.setCurrentData(appearance.get("density", "standard"))
    host.menu_radius_select = ModernSelect(host, width=112)
    for radius in (8, 12, 16, 18):
        host.menu_radius_select.addItem(f"{radius} px", radius)
    host.menu_radius_select.setCurrentData(int(appearance.get("corner_radius", 12)))
    host.menu_font_select = ModernSelect(host, width=172)
    host.menu_font_select.addItem("系统默认", "system")
    host._menu_fonts_populated = False
    current_font = str(appearance.get("ui_font") or "system")
    if current_font != "system":
        # 保留当前配置值无需枚举字体库，确保用户未展开选择器直接保存时
        # 不会把自定义字体静默重置为 system。
        host.menu_font_select.addItem(current_font, current_font)
    host.menu_font_select.setCurrentData(current_font)
    # Windows 字体较多时首次枚举可阻塞数秒。零延迟定时器仍会在
    # 设置窗口首帧绘制前运行，因此改为仅在用户真正展开字体选择器时加载。
    host.menu_font_select.aboutToShowPopup.connect(host._populate_menu_fonts)
    host.menu_font_size_select = ModernSelect(host, width=112)
    for size in range(10, 19):
        host.menu_font_size_select.addItem(f"{size} px", size)
    host.menu_font_size_select.setCurrentData(int(appearance.get("ui_font_size", 13)))
    host.menu_translucent_check = ToggleSwitch(host)
    host.menu_translucent_check.setChecked(bool(appearance.get("translucent", True)))
    host.menu_opacity_spin = BrowserDoubleSpinBox(host)
    host.menu_opacity_spin.setRange(0.72, 1.0)
    host.menu_opacity_spin.setSingleStep(0.02)
    host.menu_opacity_spin.setDecimals(2)
    host.menu_opacity_spin.setValue(float(appearance.get("opacity", 0.94)))

    def color_picker(key: str) -> ColorPicker:
        return ColorPicker(str(appearance.get(key) or DEFAULT_CONTEXT_MENU_APPEARANCE[key]), host)

    host.light_background_picker = color_picker("light_background")
    host.light_foreground_picker = color_picker("light_foreground")
    host.light_hover_picker = color_picker("light_hover")
    host.dark_background_picker = color_picker("dark_background")
    host.dark_foreground_picker = color_picker("dark_foreground")
    host.dark_hover_picker = color_picker("dark_hover")

    egg = host.config.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG)
    host.egg_enabled_check = ToggleSwitch(host)
    host.egg_enabled_check.setChecked(bool(egg.get("enabled", True)))
    host.egg_title_edit = _line_edit(str(egg.get("title") or "厉害了我的鲸"), width=240)
    host.egg_hint_edit = _line_edit(str(egg.get("hint") or "请点击"), width=160)
    avatar = resolve_fun_asset(egg.get("avatar"), oijingjing_image_path())
    image_dir = resolve_fun_asset(egg.get("image_dir"), oijingjing_image_path().parent)
    host.egg_avatar_picker = ResourcePathPicker(str(avatar.resolve()), parent=host)
    host.egg_image_dir_picker = ResourcePathPicker(
        str(image_dir.resolve()), directory=True, image_preview=True, parent=host,
    )

# ------------------------------------------------------------ 主动识屏
    if sys.platform == "win32" and host.include_ai:
        host._build_proactive_controls()


# ------------------------------------------------------------ 灵动岛联动控制器


def _update_island_controls(host, enabled: bool) -> None:
    host._set_setting_rows_visible((
        "dynamic_island_icon", "dynamic_island_name", "dynamic_island_info",
        "dynamic_island_status", "dynamic_island_info_mode",
        "dynamic_island_style", "dynamic_island_opacity", "dynamic_island_accent",
        "dynamic_island_icon_value",
        "dynamic_island_custom_text", "dynamic_island_click_action",
        "dynamic_island_event_effects", "dynamic_island_edge_dock",
        "dynamic_island_collision",
    ), enabled, dependency="island_enabled")
    _update_island_icon_controls(host, host.island_icon_check.isChecked())
    _update_island_info_controls(host, host.island_info_check.isChecked())


def _update_island_icon_controls(host, enabled: bool) -> None:
    host._set_setting_rows_visible(
        ("dynamic_island_icon_value",),
        enabled,
        dependency="island_show_icon",
    )


def _update_island_info_controls(host, enabled: bool) -> None:
    host._set_setting_rows_visible(
        ("dynamic_island_info_mode", "dynamic_island_custom_text"),
        enabled,
        dependency="island_show_info",
    )
    _update_island_custom_text(host)


def _update_island_custom_text(host, _index: int | None = None) -> None:
    host._set_setting_rows_visible(
        ("dynamic_island_custom_text",),
        host.island_info_mode_select.currentData() == "custom",
        dependency="island_info_mode",
    )


# ------------------------------------------------------------ 台词模板控制器
# 从 ModernSettingsDialog 外迁（host-based）：逻辑以 host 为参数驻留本模块，
# 对话框保留同名薄委托方法，兼容既有调用与测试 patch（tests 直接调
# dialog._import_dialogue_template_json / _current_dialogue_template 等）。


def _dialogue_flush_scope(host, scope: str | None = None) -> None:
    """把当前编辑区的文本快照写回 scope buffer（切换/保存前调用）。

    scope 缺省取内部追踪的当前层（host._dialogue_scope），而不是
    select.currentData()——切换信号触发时下拉已是新值，用它 flush 会把
    编辑内容误写进目标层。
    """
    if not hasattr(host, "dialogue_scope_select") or not hasattr(host, "dialogue_phrase_edits"):
        return
    scope = host._dialogue_scope if scope is None else scope
    host._dialogue_scope_buffer[str(scope)] = {
        key: edit.toPlainText() for key, edit in host.dialogue_phrase_edits.items()
    }


def _load_dialogue_scope_into_editors(host, scope: str) -> None:
    """把某层缓冲载入全部编辑框（未配置的事件留空 = 沿用 global/内置）。"""
    buf = host._dialogue_scope_buffer.get(scope) or {}
    for key, edit in host.dialogue_phrase_edits.items():
        edit.setPlainText(str(buf.get(key, "") or ""))


def _apply_dialogue_scope_rows(host) -> None:
    """Agent 专属层只显示该层可定制（Agent 路由）事件，公共事件行隐藏。

    global 层（""）显示全部事件。行尚未构建（对话框 __init__ 中途）时静默跳过。
    """
    scope = str(getattr(host, "_dialogue_scope", "") or "")
    if not hasattr(host, "dialogue_phrase_edits"):
        return
    show_all = scope == ""
    for key in host.dialogue_phrase_edits:
        row = host.findChild(SettingRow, f"settingRow_dialogue_{key}")
        if row is None:
            continue
        if show_all:
            row.setVisible(True)
            continue
        if key in PUBLIC_DIALOGUE_EVENTS:
            row.setVisible(False)


def _on_dialogue_scope_changed(host, index: int) -> None:
    """切换 global/某 Agent 专属文案编辑层：flush 当前层后载入目标层内容。"""
    if not hasattr(host, "dialogue_scope_select"):
        return
    _dialogue_flush_scope(host)
    target = str(host.dialogue_scope_select.currentData() or "")
    host._dialogue_scope = target
    _load_dialogue_scope_into_editors(host, target)
    _apply_dialogue_scope_rows(host)


def _dialogue_scope_values(host, scope: str) -> dict[str, list[str]]:
    """scope buffer 某层的非空事件 → list[str]（供保存/导出）。"""
    buf = host._dialogue_scope_buffer.get(scope) or {}
    return {
        key: [line.strip() for line in str(text).splitlines() if line.strip()]
        for key, text in buf.items()
        if str(text or "").strip()
    }


def _dialogue_phrase_values(host) -> dict[str, list[str]]:
    return _dialogue_scope_values(host, "")


def _current_dialogue_template(host) -> dict:
    # 导出 = 纯字段参考模板：phrases 一律留空（不携带当前已配置的台词），
    # 供 AI 依角色卡从零撰写；当前台词如需备份请直接复制编辑框内容。
    # agents：为「全部 Agent（含自定义）」各生成一层事件脚手架（值空 = 沿用
    # global/内置），让 AI 能逐个 Agent 单独配台词。
    agent_keys = [
        str(host.dialogue_scope_select.itemData(index) or "")
        for index in range(host.dialogue_scope_select.count())
    ]
    agent_keys = [key for key in agent_keys if key]
    return build_persona_template({
        "dialogue_mode": host.dialogue_mode_select.currentData() or "legacy",
        "dialogue_phrases": {},
    }, agent_keys=agent_keys or None)


def _export_dialogue_template(host) -> None:
    """Export the complete current template to the clipboard (no file dialog)."""
    try:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            raise RuntimeError("系统剪贴板不可用")
        clipboard.setText(json.dumps(_current_dialogue_template(host), ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:
        QMessageBox.warning(host, "导出失败", f"无法写入系统剪贴板：{exc}")
        return
    QMessageBox.information(
        host, "导出成功",
        "模板已复制到剪贴板：可直接粘贴给 AI 依角色卡改写，"
        "或粘贴回「导入模板」输入框一键导回。",
    )


def _import_dialogue_template(host) -> None:
    """导入默认台词模板：将所有预设台词填充到自定义编辑框。"""
    defaults = default_phrases()
    for key, edit in host.dialogue_phrase_edits.items():
        if key in defaults:
            edit.setPlainText(defaults[key])
    QMessageBox.information(host, "导入成功", "已导入全部默认台词模板。")


def _import_dialogue_template_json(host) -> None:
    """Import a complete persona template from the inline JSON editor."""
    raw = host.dialogue_template_import_edit.toPlainText().strip()
    if not raw:
        QMessageBox.warning(host, "导入失败", "请先粘贴 JSON 模板。")
        return
    try:
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("模板根节点必须是 JSON 对象")
        template_name = str(document.get("template", "") or "")
        if template_name and not template_name.startswith("persona-phrases/"):
            raise ValueError("不是兼容的 persona-phrases 模板")
        phrases = document.get("phrases")
        if not isinstance(phrases, dict):
            phrases = document.get("dialogue_phrases")
        if not isinstance(phrases, dict):
            raise ValueError("模板缺少 phrases 对象")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        QMessageBox.warning(host, "导入失败", f"JSON 模板无效：{exc}")
        return
    for key, edit in host.dialogue_phrase_edits.items():
        value = phrases.get(key, "")
        if isinstance(value, list):
            edit.setPlainText("\n".join(str(item) for item in value if isinstance(item, str)))
        elif value is not None:
            edit.setPlainText(str(value))
    # entries[].phrases 兜底：顶层 phrases 缺失/为空的 key 用 entries 补齐
    #（顶层有内容时以顶层为准，不被 entries 覆盖）。
    entries = document.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            edit = host.dialogue_phrase_edits.get(str(entry.get("key") or "").strip())
            if edit is None:
                continue
            current = phrases.get(entry["key"])
            has_top = (
                (isinstance(current, list) and any(isinstance(i, str) and i.strip() for i in current))
                or (isinstance(current, str) and current.strip())
            )
            if has_top or edit.toPlainText().strip():
                continue
            value = entry.get("phrases")
            if isinstance(value, list):
                text = "\n".join(str(item) for item in value if isinstance(item, str) and item.strip())
                if text:
                    edit.setPlainText(text)
    host.dialogue_mode_select.setCurrentData("custom")
    # agents delta（整体导入）：写入 scope buffer，供切换专属层编辑
    _dialogue_flush_scope(host)
    raw_agents = document.get("agents")
    if isinstance(raw_agents, dict):
        for agent_key, agent_events in raw_agents.items():
            if not isinstance(agent_events, dict):
                continue
            host._dialogue_scope_buffer[str(agent_key)] = {
                str(k): ("\n".join(str(i) for i in v) if isinstance(v, list) else str(v or ""))
                for k, v in agent_events.items()
            }
    host.dialogue_template_import_edit.clear()
    QMessageBox.information(host, "导入成功", "已导入全部弹窗内容模板；点击“保存并退出”后生效。")
