# -*- coding: utf-8 -*-
"""设置页「文件识别」域：拖文件解读的控件创建 / 行装配 / 保存。

modern_settings_dialog.py 行数预算已无余量，本域的控件与行全部在本模块
构建，对话框只做三处接线：控件安装（_build_file_interpret_controls）、
域导航挂页（_rebuild_domain_navigation）、保存委托（_write_config）。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .config import _default_file_interpret_data
from .settings_widgets import BrowserDoubleSpinBox, SettingRow, SettingsSection, ToggleSwitch

# 与 Config 归一化区间同源（config.py file_interpret 段）
_PROGRESS_INTERVAL_RANGE = (5.0, 120.0)
_PROGRESS_INTERVAL_DEFAULT = 15.0


def _clamp_interval(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _PROGRESS_INTERVAL_DEFAULT
    return max(_PROGRESS_INTERVAL_RANGE[0], min(_PROGRESS_INTERVAL_RANGE[1], number))


def create_file_interpret_controls(dialog) -> None:
    """在对话框上创建本域控件（幂等；行由 build_file_interpret_rows 装配）。"""
    if getattr(dialog, "file_interpret_enabled_check", None) is not None:
        return
    defaults = _default_file_interpret_data()
    cfg = dialog.config.get("file_interpret") or {}
    if not isinstance(cfg, dict):
        cfg = {}

    dialog.file_interpret_enabled_check = ToggleSwitch(dialog)
    dialog.file_interpret_enabled_check.setChecked(bool(cfg.get("enabled", defaults["enabled"])))

    dialog.file_interpret_interval_spin = BrowserDoubleSpinBox(dialog)
    dialog.file_interpret_interval_spin.setRange(*_PROGRESS_INTERVAL_RANGE)
    dialog.file_interpret_interval_spin.setDecimals(0)
    dialog.file_interpret_interval_spin.setSuffix(" 秒")
    dialog.file_interpret_interval_spin.setValue(_clamp_interval(cfg.get("progress_interval_seconds", defaults["progress_interval_seconds"])))


def build_file_interpret_rows(dialog) -> list[SettingRow]:
    """本域的设置行（objectName 前缀 settingRow_file_interpret_，可被设置搜索索引）。

    每次域导航重建时重新构造（控件对象复用，行壳随旧页 deleteLater），
    与 island/proactive 行的重建口径一致。
    """
    return [
        SettingRow(
            "file_interpret_enabled",
            "拖文件后询问解读",
            "桌宠吃掉文件后弹气泡询问是否交给 AI 解读；关闭则只播吃动画，不询问。",
            dialog.file_interpret_enabled_check,
        ),
        SettingRow(
            "file_interpret_progress_interval",
            "进度汇报间隔",
            "解读进行中每隔多少秒冒泡汇报一次进度（5–120 秒）。",
            dialog.file_interpret_interval_spin,
        ),
    ]


def build_file_interpret_page(dialog) -> QWidget:
    """「文件识别」域整页内容（_rebuild_domain_navigation 直接挂到域导航）。"""
    rows = build_file_interpret_rows(dialog)
    content = QWidget(dialog)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)
    layout.addWidget(SettingsSection("拖文件解读", rows, content))
    layout.addStretch(1)
    return content


def save_file_interpret_settings(dialog) -> None:
    """_write_config 委托：把本域控件写回 config（在既有 dict 上合并，保未来键）。"""
    if getattr(dialog, "file_interpret_enabled_check", None) is None:
        return
    data = dialog.config.get("file_interpret") or {}
    data = dict(data) if isinstance(data, dict) else {}
    data["enabled"] = bool(dialog.file_interpret_enabled_check.isChecked())
    data["progress_interval_seconds"] = float(dialog.file_interpret_interval_spin.value())
    dialog.config.set("file_interpret", data)
    dialog.config.save()
