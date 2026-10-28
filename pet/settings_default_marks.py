# -*- coding: utf-8 -*-
"""设置页「已修改」标记 / 默认值小字 / 单项「改回默认」（设置页第三刀，2026-10-08）。

**为什么单独一个模块**：``modern_settings_dialog.py`` 的行数预算（``tests/
test_architecture.py``）没有余量，而本功能的主体是一张「设置行 → Config 键路径」
映射表加一组控件值适配器，与框体结构无关——按本仓库既有口径（settings_network /
settings_quiet / settings_music / settings_file_interpret / settings_reset_defaults
都是同一个原因拆出来的）落在这里，对话框只留三行接线。

口径（主代理裁决 + ``docs/SETTINGS-CHANGE-GATES.md``）：

- **默认值只有一个来源**：``Config._initial_data()``。本模块**一个默认值都不写**，
  只登记「哪一行对应哪个配置键」——``pet/config.py`` 改默认值不必动这里，也不会
  出现第二份会漂移的默认值表。
- **只认四类控件**（ToggleSwitch / ModernSelect / BrowserSpinBox / QLineEdit）：
  其余控件（QDoubleSpinBox、QComboBox、ColorPicker、ResourcePathPicker、复合控件等）
  整行不参与，既不显示标记也不报错。
- **没有可比默认值的行不显示标记**：逐服务商字段（api_url/model/api_key/…）、列表
  成员（island_network/cpu/mem/gpu 是 ``island_metrics`` 列表的成员）、UI 记忆
  （dialogue_scope）等，清单由 ``tests/test_settings_default_marks.py`` 的快照钉住。
- **单项重置只改控件值**：本模块不碰 ``Config``，也不新增任何写回路径——重置把控件
  设回默认值后，落盘时机跟着**该行自己的保存语义**走，与本模块无关：即时生效型开关
  （``click_sound_enabled`` / ``agent_link.sound_enabled`` 等，控件 ``toggled`` 直连
  宿主的 ``_apply_*_enabled_now``）改一下就写盘；其余行随设置窗关闭的既有
  ``_write_config`` 写回。
- 标记与按钮只做 ``setVisible``：依赖隐藏交给 Qt 自己传播（父行不可见时标记也不
  呈现，父行重新显示后标记跟着回来），不做额外的可见性记账。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QSpinBox, QWidget

from .config import Config
from .settings_widgets import ModernSelect, SettingRow, ToggleSwitch

#: 设置行键（``settingRow_`` 之后的部分）→ ``Config`` 默认值路径（点号分隔嵌套）。
#:
#: **本表只登记「哪一行对应哪个配置键」，一个默认值都不写**。行键与顶层配置键同名
#: 时不必列在这里（先查本表，再回落「行键就是顶层键」）。值写成 ``(路径, 倍数)``
#: 表示控件与配置不同量纲（配置存 0~1、控件按百分比显示）。
_ROW_DEFAULT_PATHS: dict[str, str | tuple[str, float]] = {
    "file_interpret_enabled": "file_interpret.enabled",
    # 主动识屏（嵌套 dict；preset 会在保存时按数值自动落为 custom）
    "proactive_enabled": "proactive_screen.enabled",
    "proactive_dry_run": "proactive_screen.dry_run",
    "proactive_preset": "proactive_screen.preset",
    "proactive_dwell": "proactive_screen.dwell_seconds",
    "proactive_min_interval": "proactive_screen.min_request_interval_seconds",
    "proactive_daily_cap": "proactive_screen.daily_cap",
    "proactive_require_idle": "proactive_screen.require_idle",
    "proactive_idle_seconds": "proactive_screen.min_idle_seconds",
    "proactive_through": "proactive_screen.allow_when_mouse_through",
    "proactive_pre_cue": "proactive_screen.pre_cue",
    "proactive_free": "proactive_screen.prefer_free_provider",
    # Agent 联动音效（嵌套 dict）
    "agent_sound_enabled": "agent_link.sound_enabled",
    "agent_sound_volume": ("agent_link.sound_volume", 100),
    "agent_cost": "agent_cost_enabled",
    # 余额
    "balance_refresh": "balance_refresh_minutes",
    "balance_tier_mode": "balance_tier_labels_mode",
    "balance_tier_peak": "balance_tier_label_peak",
    "balance_tier_idle": "balance_tier_label_idle",
    "balance_tier_color": "balance_tier_color_enabled",
    # 灵动岛（嵌套 dict）
    "dynamic_island_enabled": "dynamic_island.enabled",
    "dynamic_island_icon": "dynamic_island.show_icon",
    "dynamic_island_name": "dynamic_island.show_name",
    "dynamic_island_info": "dynamic_island.show_info",
    "dynamic_island_status": "dynamic_island.show_status",
    "dynamic_island_info_mode": "dynamic_island.info_mode",
    "dynamic_island_style": "dynamic_island.style",
    "dynamic_island_accent": "dynamic_island.accent",
    "dynamic_island_icon_value": "dynamic_island.icon",
    "dynamic_island_custom_text": "dynamic_island.custom_text",
    "dynamic_island_click_action": "dynamic_island.click_action",
    "dynamic_island_hidden_chat": "dynamic_island.hidden_chat",
    "dynamic_island_event_effects": "dynamic_island.event_effects",
    "dynamic_island_edge_dock": "dynamic_island.edge_dock",
    "dynamic_island_collision": "dynamic_island.collision_enabled",
    # 菜单外观 / 彩蛋（嵌套 dict）
    "menu_theme": "context_menu_appearance.theme",
    "menu_density": "context_menu_appearance.density",
    "menu_radius": "context_menu_appearance.corner_radius",
    "menu_font": "context_menu_appearance.ui_font",
    "menu_font_size": "context_menu_appearance.ui_font_size",
    "menu_translucent": "context_menu_appearance.translucent",
    "egg_enabled": "menu_easter_egg.enabled",
    "egg_title": "menu_easter_egg.title",
    "egg_hint": "menu_easter_egg.hint",
    "network_info_menu": "network_info_menu_enabled",
    # 互动
    "self_talk": "self_talk_enabled",
    "click_sound": "click_sound_enabled",
    "click_sound_volume": ("click_sound_volume", 100),
    "click_balance": "click_show_balance",
    "click_self_talk": "click_show_self_talk",
    "click_self_talk_speak": "self_talk_speak_enabled",
    "click_self_talk_precache": "self_talk_voice_precache_enabled",
    "golden_spin_click": "golden_spin_on_click",
    "idle_low_fps": "idle_low_fps_enabled",
    # 桌宠
    "edge_probe": "edge_probe_enabled",
    "collision_sound_volume": ("collision_sound_volume", 100),
    "music_sing": "music_sing_enabled",
    "music_lyric": "music_lyric_enabled",
    "music_browser_media": "music_browser_media_enabled",
    "foobar_lyrics": "foobar_lyrics_enabled",
    "lxmusic": "lxmusic_enabled",
    # 常规
    "autostart": "autostart_wanted",
    "stream_capture": "stream_capture_mode",
    "cross_screen": "allow_cross_screen",
    # AI 对话·联网搜索（全局 web_search 设置；默认值快照由 _config_default_data 注入）
    "web_search_enabled": "chat.web_search.enabled",
    "web_search_engine": "chat.web_search.engine",
    "web_search_max_results": "chat.web_search.max_results",
    "web_search_fetch_pages": "chat.web_search.fetch_pages",
    # macOS 专属行（darwin 分支才构造）：行键与配置键不同名，必须显式登记
    "dock_icon": "show_dock_icon",
}

#: 「控件不在这四类里」的哨兵：区分它与 ModernSelect 合法的空数据（``None``）。
NOT_A_SETTING_CONTROL = object()

_DEFAULT_CONFIG_DATA: dict | None = None


def _config_default_data() -> dict:
    """``Config`` 的出厂默认数据（默认值的唯一真相源）。

    ``Config._initial_data`` 不读 ``self``（``Config.__init__`` 与
    ``reset_to_defaults`` 共用它），所以这里用 ``__new__`` 取一份即可——不触发
    ``Config.__init__`` 的建目录 / 旧配置迁移 / keyring 迁移副作用：设置窗读一次
    默认值不该付这些代价。
    """
    global _DEFAULT_CONFIG_DATA
    if _DEFAULT_CONFIG_DATA is None:
        _DEFAULT_CONFIG_DATA = Config.__new__(Config)._initial_data()
    return _DEFAULT_CONFIG_DATA


def _control_value(control: QWidget):
    """四类受支持控件的当前值；其余控件返回 :data:`NOT_A_SETTING_CONTROL`。

    死控件守卫：模块停用/域重建后，注册表里的控件可能已随旧页销毁（C++
    已删、Python 壳还在）——摸它就是 RuntimeError（astra P1-1 崩溃现场）。
    按「不是设置控件」跳过：标记系统不该为死引用陪葬整个保存链。
    """
    import shiboken6

    if not shiboken6.isValid(control):
        return NOT_A_SETTING_CONTROL
    if isinstance(control, ToggleSwitch):
        return bool(control.isChecked())
    if isinstance(control, ModernSelect):
        return control.currentData()
    if isinstance(control, QSpinBox):  # BrowserSpinBox；QDoubleSpinBox 不在此列
        return control.value()
    if isinstance(control, QLineEdit):
        return control.text()
    return NOT_A_SETTING_CONTROL


def _apply_control_value(control: QWidget, value) -> None:
    """把配置口径的默认值写回控件（与用户手改走同一套控件接口）。"""
    if isinstance(control, ToggleSwitch):
        control.setChecked(bool(value))
    elif isinstance(control, ModernSelect):
        control.setCurrentData(value)
    elif isinstance(control, QSpinBox):
        control.setValue(int(round(float(value))))
    elif isinstance(control, QLineEdit):
        control.setText(str(value))


def _row_default_value(control: QWidget, row_key: str) -> tuple[bool, object]:
    """该行的配置默认值（按控件口径换算），返回值 ``(是否可比, 默认值)``。"""
    entry = _ROW_DEFAULT_PATHS.get(row_key)
    if entry is None:
        if row_key not in _config_default_data():
            return False, None
        entry = row_key
    path, factor = entry if isinstance(entry, tuple) else (entry, 1.0)
    node: object = _config_default_data()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    if factor != 1.0:
        try:
            node = round(float(node) * factor)
        except (TypeError, ValueError):
            return False, None
    return True, node


def _values_equal(current, default) -> bool:
    """控件值与该行默认值是否一致（数字跨 int/float 容忍，字符串严格比）。"""
    if current == default:
        return True
    try:
        return abs(float(current) - float(default)) < 1e-6
    except (TypeError, ValueError):
        return False


def _default_value_text(control: QWidget, value) -> str:
    """默认值的用户可读写法（开关=开/关，下拉=选项文案，数字带控件单位后缀）。"""
    if isinstance(control, ToggleSwitch):
        return "开" if value else "关"
    if isinstance(control, ModernSelect):
        index = control.findData(value)
        if index >= 0:
            return control.itemText(index)
    if isinstance(control, QLineEdit):
        return str(value or "").strip() or "（空）"
    if isinstance(control, QSpinBox):
        return f"{control.prefix()}{value}{control.suffix()}"
    return str(value)


def install(dialog) -> None:
    """给对话框里每一行接上「已修改」标记、默认值小字与单项重置入口。

    控件不是四类之一、或该行没有可比默认值时整行不参与。接完立刻刷新一次，所以
    打开设置时看到的就是当前配置相对出厂默认的真实差异。
    """
    dialog._default_mark_rows = []  # 宿主的行登记表：构建期接线，随后 refresh 复用
    for row in dialog.findChildren(SettingRow):
        control = row.control
        if _control_value(control) is NOT_A_SETTING_CONTROL:
            continue
        found, default = _row_default_value(
            control, row.objectName()[len("settingRow_"):]
        )
        if not found:
            continue
        dialog._default_mark_rows.append((row, control, default))
        row.default_reset_button.clicked.connect(
            lambda _checked=False, c=control, d=default: _reset_row(dialog, c, d)
        )
        if isinstance(control, ModernSelect):
            control.currentIndexChanged.connect(lambda *_: refresh(dialog))
        elif isinstance(control, QLineEdit):
            control.textChanged.connect(lambda *_: refresh(dialog))
        elif isinstance(control, ToggleSwitch):
            control.toggled.connect(lambda *_: refresh(dialog))
        else:  # BrowserSpinBox
            control.valueChanged.connect(lambda *_: refresh(dialog))
    refresh(dialog)


def refresh(dialog) -> None:
    """按当前控件值刷新全部行的「已修改」呈现（构建 / 改动 / 保存后共用）。

    ``install`` 之前被调用（``_write_config`` 之外还有域重建期）时静默跳过：
    此时还没有接线的行，无事可做。
    """
    for row, control, default in getattr(dialog, "_default_mark_rows", ()):
        # 行壳同样可能已死（旧页 deleteLater）：行与控件任一失效都跳过
        import shiboken6

        if not shiboken6.isValid(row):
            continue
        current = _control_value(control)
        modified = (
            current is not NOT_A_SETTING_CONTROL and not _values_equal(current, default)
        )
        row.set_default_state(
            _default_value_text(control, default) if modified else None,
            can_reset=shiboken6.isValid(control) and control.isEnabled(),
        )


def _reset_row(dialog, control: QWidget, default) -> None:
    """单项重置：只把控件值设回默认；落盘按该行自己的保存语义（即时生效型立即
    写盘，其余随设置窗关闭的 ``_write_config`` 写回）。"""
    _apply_control_value(control, default)
    refresh(dialog)
