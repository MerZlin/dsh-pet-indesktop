# -*- coding: utf-8 -*-
"""「提醒与快捷键」设置组的行与控件（控件与行定义都在本模块）。

为什么单独一个模块：``modern_settings_dialog.py`` 有行数预算
（``tests/test_architecture.py::MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET``），按
``docs/WINDOW_PY_SPLIT_GUIDE.md`` 的口径「新控件组优先拆出」——对话框里只留
import、域装配一行与保存链一行。

四条都是**开关**；细则（规则表、键位、台词）住在 ``config.json`` 里：

- ``timed_rules``       定时提醒：每天 / 每周 / 每隔 N 分钟 + 台词（可带今日汇总）
- ``app_usage_rules``   用久了提醒：``{exe: {minutes, repeat, on, lines}}``
- ``hotkeys``           全局快捷键：[{seq: "Ctrl+Alt+1", act, lines}]
- ``mem_min_size_mb``   回收内存的最小进程尺寸（本页只给"不动前台"开关）
"""
from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from .settings_widgets import SettingRow, ToggleSwitch


def build_reminder_controls(dialog) -> None:
    """建控件（幂等：二次装配不重建，避免丢状态）。"""
    host = dialog
    for attr, key, default in (
        ("timed_on_check", "timed_on", True),
        ("app_usage_on_check", "app_usage_on", True),
        ("hotkeys_on_check", "hotkeys_on", True),
        ("mem_skip_foreground_check", "mem_skip_foreground", True),
    ):
        if getattr(host, attr, None) is None:
            control = ToggleSwitch(host)
            control.setChecked(bool(host.config.get(key, default)))
            setattr(host, attr, control)
    if getattr(host, "rules_editor_btn", None) is None:
        button = QPushButton("编辑规则…", host)
        button.clicked.connect(lambda _checked=False, dlg=host: _open_rules_editor(dlg))
        host.rules_editor_btn = button


def _open_rules_editor(dialog) -> None:
    """打开规则图形编辑器；窗口在的话（设置对话框的父就是桌宠窗口）保存后立刻生效。"""
    from . import rules_editor

    parent = dialog.parent()
    window = parent if hasattr(parent, "cfg") else None
    rules_editor.open_rules_editor(dialog.config, window=window, parent=dialog)


def build_toggle_rows(dialog) -> list[SettingRow]:
    """「提醒与快捷键」组的行（顺序即渲染顺序）。"""
    build_reminder_controls(dialog)
    return [
        SettingRow(
            "timed_on",
            "定时提醒",
            "到点说一句；规则写在 config.json 的 timed_rules 里"
            "（每天 HH:MM / 每周选几天 / 每隔 N 分钟，台词可配，可先报今日汇总）。",
            dialog.timed_on_check,
        ),
        SettingRow(
            "app_usage_on",
            "用久了提醒",
            "某个应用连续用满设定时长就说一句；规则在 config.json 的 app_usage_rules 里"
            "（键鼠静默超过 10 分钟不计时，换应用重新计时）。",
            dialog.app_usage_on_check,
        ),
        SettingRow(
            "hotkeys_on",
            "全局快捷键",
            "在哪个程序里按都管用；键位与动作在 config.json 的 hotkeys 里"
            "（默认 Ctrl+Alt+1/2/3，至少带 Ctrl；被别的程序占用的键会如实提示）。",
            dialog.hotkeys_on_check,
        ),
        SettingRow(
            "mem_skip_foreground",
            "回收内存不动前台程序",
            "右键菜单「回收内存」的开关：默认跳过你正在用的程序，"
            "免得收完还要重新读盘（更细的阈值在 config.json 的 mem_min_size_mb）。",
            dialog.mem_skip_foreground_check,
        ),
        SettingRow(
            "rules_editor",
            "编辑规则…",
            "打开图形编辑器：定时提醒（每天 / 每周 / 每隔）、用久了提醒（应用 + 时长 + 台词）、"
            "全局快捷键（按键 + 动作 + 台词）三张表，改完保存即刻生效，不用再手改 config.json。",
            dialog.rules_editor_btn,
        ),
    ]


def apply_to_config(dialog) -> None:
    """把 4 个开关写回配置（其余细则键不动）。"""
    dialog.config.set("timed_on", bool(dialog.timed_on_check.isChecked()))
    dialog.config.set("app_usage_on", bool(dialog.app_usage_on_check.isChecked()))
    dialog.config.set("hotkeys_on", bool(dialog.hotkeys_on_check.isChecked()))
    dialog.config.set("mem_skip_foreground", bool(dialog.mem_skip_foreground_check.isChecked()))
