# -*- coding: utf-8 -*-
"""免打扰 / 今日汇总的设置页控件与行（控件与行定义都在本模块）。

**为什么单独一个模块**：``modern_settings_dialog.py`` 有行数预算
（``tests/test_architecture.py::MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET``），按
``docs/WINDOW_PY_SPLIT_GUIDE.md`` 的口径「新控件组优先拆出」——对话框里只留接线：
构造期建控件、域装配里取行、保存链里写回。

**接线契约**（与 ``settings_interaction`` / ``settings_file_interpret`` 同口径）：

- 控件在本模块创建并挂到 dialog（``quiet_*`` / ``summary_*`` 前缀）；
- 行由 ``build_quiet_rows()`` 返回，放进「自动化与联动」域的「免打扰与汇总」组；
  这些行不进 ``_rebuild_domain_navigation`` 开头的 ``all_rows`` 快照，因此不需要
  ``claim``，也不会被判成「待分类（开发期）」；
- ``objectName`` 由 ``SettingRow`` 统一置为 ``settingRow_<配置键>``，所以设置搜索
  索引、显隐联动与保存链照旧工作。
"""
from __future__ import annotations

from .daily_summary import DEFAULT_SUMMARY_MAX_APPS, DEFAULT_SUMMARY_MIN_SECONDS
from .quiet_mode import DEFAULT_QUIET_MINUTES
from .settings_widgets import BrowserSpinBox, SettingRow, ToggleSwitch

# 取值区间：与 pet/quiet_mode.normalize_minutes 的夹取口径一致
QUIET_MINUTES_RANGE = (1, 720)
SUMMARY_MINUTES_RANGE = (0, 24 * 60)
SUMMARY_MAX_APPS_RANGE = (1, 5)


def build_quiet_controls(dialog) -> None:
    """建控件（只在对话框构造期调用一次）。"""
    host = dialog
    host.quiet_default_minutes_spin = BrowserSpinBox(host)
    host.quiet_default_minutes_spin.setRange(*QUIET_MINUTES_RANGE)
    host.quiet_default_minutes_spin.setSuffix(" 分钟")
    host.quiet_default_minutes_spin.setValue(
        int(host.config.get("quiet_minutes_default", DEFAULT_QUIET_MINUTES) or DEFAULT_QUIET_MINUTES)
    )

    host.quiet_fullscreen_check = ToggleSwitch(host)
    host.quiet_fullscreen_check.setChecked(
        bool(host.config.get("quiet_also_on_fullscreen", True))
    )

    host.summary_min_minutes_spin = BrowserSpinBox(host)
    host.summary_min_minutes_spin.setRange(*SUMMARY_MINUTES_RANGE)
    host.summary_min_minutes_spin.setSuffix(" 分钟")
    host.summary_min_minutes_spin.setValue(
        max(0, int(host.config.get("summary_min_seconds", DEFAULT_SUMMARY_MIN_SECONDS) or 0)) // 60
    )

    host.summary_max_apps_spin = BrowserSpinBox(host)
    host.summary_max_apps_spin.setRange(*SUMMARY_MAX_APPS_RANGE)
    host.summary_max_apps_spin.setSuffix(" 个")
    host.summary_max_apps_spin.setValue(
        int(host.config.get("summary_max_apps", DEFAULT_SUMMARY_MAX_APPS) or DEFAULT_SUMMARY_MAX_APPS)
    )


def build_quiet_rows(dialog) -> list[SettingRow]:
    """「免打扰与汇总」组的行（顺序即渲染顺序）。"""
    return [
        SettingRow(
            "quiet_minutes_default",
            "免打扰默认时长",
            "右键菜单「免打扰」的默认时长；菜单里另有 15/30/60/120 四档可直接点。",
            dialog.quiet_default_minutes_spin,
        ),
        SettingRow(
            "quiet_also_on_fullscreen",
            "全屏时也算免打扰",
            "前台全屏（打游戏、看电影）期间同样压住普通气泡，提醒攒到退出全屏后一起汇报。",
            dialog.quiet_fullscreen_check,
        ),
        SettingRow(
            "summary_min_seconds",
            "汇总点名门槛",
            "「今日汇总」里应用时长至少达到这个值才点名（0 = 任何用量都点名）。",
            dialog.summary_min_minutes_spin,
        ),
        SettingRow(
            "summary_max_apps",
            "汇总点名条数",
            "「今日汇总」最多点名几个应用。",
            dialog.summary_max_apps_spin,
        ),
    ]


def apply_to_config(dialog) -> None:
    """把 4 个键写回配置（在对话框保存链里调用；未调用则保持原值）。"""
    dialog.config.set("quiet_minutes_default", int(dialog.quiet_default_minutes_spin.value()))
    dialog.config.set("quiet_also_on_fullscreen", bool(dialog.quiet_fullscreen_check.isChecked()))
    dialog.config.set("summary_min_seconds", int(dialog.summary_min_minutes_spin.value()) * 60)
    dialog.config.set("summary_max_apps", int(dialog.summary_max_apps_spin.value()))
