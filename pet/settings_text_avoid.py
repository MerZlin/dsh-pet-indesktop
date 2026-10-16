# -*- coding: utf-8 -*-
"""设置页「避字移动」控件 / 行装配 / 保存。

modern_settings_dialog.py 行数预算已无余量，本域的控件与行全部在本模块
构建（与 ``settings_file_interpret.py`` 同口径），对话框只做三处接线：
import、「桌宠」域里的分区装配（build_text_avoid_section，与「边缘探头」同款
的行工厂 + claim 归位）、保存委托（_write_config）。

**不新增侧栏域**：侧栏 10 个域是外部可见的契约（tests/test_menu_layout.py 与
tests/test_settings_interaction_tabs.py 锁着），避字移动属于「桌宠」域里
「动画与移动」的邻居，所以按既有 claim 机制归位，而不是另开一页。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from .config import _default_text_avoid_data
from .settings_widgets import (
    BrowserDoubleSpinBox,
    ModernSelect,
    SettingRow,
    SettingsSection,
    ToggleSwitch,
)

# 与 Config 归一化区间同源（config.py text_avoid 段）
_REFRESH_RANGE = (5.0, 300.0)
_RUN_SPEED_RANGE = (1.0, 3.0)
# 跑动速度的可选档位（与「播放速率」同款离散下拉；1x = 与平时一样快）
RUN_SPEED_CHOICES = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0)

# 本域设置行的 id（claim 归位与设置搜索索引都靠它，必须与 SettingRow 的 id 一致）
ROW_IDS = ("text_avoid", "text_avoid_cjk", "text_avoid_refresh",
           "text_avoid_react", "text_avoid_run_speed")


def _clamp(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(_REFRESH_RANGE[0], min(_REFRESH_RANGE[1], number))


def _clamp_run(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(_RUN_SPEED_RANGE[0], min(_RUN_SPEED_RANGE[1], number))


def create_text_avoid_controls(dialog) -> None:
    """在对话框上创建本域控件（幂等；行由 build_text_avoid_rows 装配）。"""
    if getattr(dialog, "text_avoid_enabled_check", None) is not None:
        return
    defaults = _default_text_avoid_data()
    cfg = dialog.config.get("text_avoid") or {}
    if not isinstance(cfg, dict):
        cfg = {}

    dialog.text_avoid_enabled_check = ToggleSwitch(dialog)
    dialog.text_avoid_enabled_check.setChecked(
        bool(cfg.get("enabled", defaults["enabled"]))
    )

    dialog.text_avoid_cjk_check = ToggleSwitch(dialog)
    dialog.text_avoid_cjk_check.setChecked(bool(cfg.get("cjk_only", defaults["cjk_only"])))

    dialog.text_avoid_refresh_spin = BrowserDoubleSpinBox(dialog)
    dialog.text_avoid_refresh_spin.setRange(*_REFRESH_RANGE)
    dialog.text_avoid_refresh_spin.setDecimals(0)
    dialog.text_avoid_refresh_spin.setSuffix(" 秒")
    dialog.text_avoid_refresh_spin.setValue(
        _clamp(cfg.get("refresh_seconds", defaults["refresh_seconds"]),
               defaults["refresh_seconds"])
    )

    dialog.text_avoid_react_check = ToggleSwitch(dialog)
    dialog.text_avoid_react_check.setChecked(
        bool(cfg.get("react_now", defaults["react_now"]))
    )

    dialog.text_avoid_run_speed_select = ModernSelect(dialog, width=112)
    current_run = _clamp_run(cfg.get("run_speed", defaults["run_speed"])) \
        if "run_speed" in cfg else defaults["run_speed"]
    choices = list(RUN_SPEED_CHOICES)
    if not any(abs(current_run - value) < 0.001 for value in choices):
        choices.append(current_run)
        choices.sort()
    for speed in choices:
        dialog.text_avoid_run_speed_select.addItem(f"{speed:g}x", speed)
    dialog.text_avoid_run_speed_select.setCurrentData(current_run)


def build_text_avoid_rows(dialog) -> list[SettingRow]:
    """本域的设置行（objectName 前缀 settingRow_text_avoid*，可被设置搜索索引）。"""
    return [
        SettingRow(
            "text_avoid",
            "避字移动",
            "桌宠自行选移动目标时，先看一眼屏幕上哪里有字，尽量落在不压住汉字的位置。"
            "识别走桌宠自带的本地文字识别（离线、断网可用，截图只在内存里、不落盘）。"
            "桌宠自己挡住的那块用 PrintWindow "
            "抓它底下那个窗口补回来——不改动窗口显示属性，截图/录屏照常能拍到桌宠。",
            dialog.text_avoid_enabled_check,
        ),
        SettingRow(
            "text_avoid_cjk",
            "只避开汉字",
            "开启时只把汉字当作要避开的字；关闭后英文和数字也算（遮住一整行英文同样难看）。",
            dialog.text_avoid_cjk_check,
        ),
        SettingRow(
            "text_avoid_refresh",
            "文字图刷新间隔",
            "同一张文字图至少隔多少秒才重新识别一次（5–300 秒，默认 10 秒）。识别是"
            "**按需**触发的：只在要挑落点、或要判断此刻压不压字时才刷（另外图再旧也有个"
            "两分钟的兜底），所以这个值调小通常也不会明显占资源；一次识别要起一个识别"
            "进程并加载模型，约 3~4 秒，故不建议调到最低。屏幕文字位置没变时会跳过这一轮。",
            dialog.text_avoid_refresh_spin,
        ),
        SettingRow(
            "text_avoid_react",
            "被压住就立刻走开",
            "发现自己此刻正压着字时，不等下一次自选移动，直接打断当前动画走开。"
            "关闭时只在下次要走的时候才顺便改道（不动正在播的动画）。",
            dialog.text_avoid_react_check,
        ),
        SettingRow(
            "text_avoid_run_speed",
            "避字跑动速度",
            "为避字而改道时，桌宠赶路的速度倍率（1x = 与平时一样）。"
            "动画与位移一起加快，所以是\"真的跑快了\"而不是滑步；普通漫游不受影响。",
            dialog.text_avoid_run_speed_select,
        ),
    ]


def build_text_avoid_section(dialog, parent: QWidget) -> SettingsSection:
    """行工厂 + 归位载体（与「边缘探头」同款）。

    行必须先在 ``_rebuild_domain_navigation`` 的 ``all_rows`` 快照之前作为对话框
    后代存在，``claim(...)`` 才找得到它们；随后它们在「桌宠」域里被重新装进新
    分区，本函数建的这个占位分区连同旧页一起销毁。
    """
    create_text_avoid_controls(dialog)
    return SettingsSection("避字移动", build_text_avoid_rows(dialog), parent)


def save_text_avoid_settings(dialog) -> None:
    """_write_config 委托：把本域控件写回 config（在既有 dict 上合并，保未来键）。"""
    if getattr(dialog, "text_avoid_enabled_check", None) is None:
        return
    data = dialog.config.get("text_avoid") or {}
    data = dict(data) if isinstance(data, dict) else {}
    data["enabled"] = bool(dialog.text_avoid_enabled_check.isChecked())
    data["cjk_only"] = bool(dialog.text_avoid_cjk_check.isChecked())
    data["refresh_seconds"] = float(dialog.text_avoid_refresh_spin.value())
    data["react_now"] = bool(dialog.text_avoid_react_check.isChecked())
    run = dialog.text_avoid_run_speed_select.currentData()
    data["run_speed"] = _clamp_run(run) if run is not None else 1.0
    dialog.config.set("text_avoid", data)
    dialog.config.save()
