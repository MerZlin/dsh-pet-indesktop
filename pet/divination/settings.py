# -*- coding: utf-8 -*-
"""设置页「互动 · 趣味」标签：每日一签（控件创建 / 行装配 / 保存）。

**为什么独立模块**：``modern_settings_dialog.py`` 的行数预算已无余量。本页控件与行
全部在此构建，对话框只做一处接线：``_write_config`` 里的
``settings_divination.save_divination_settings(self)``。

**为什么挂在「互动」域的页内标签而不是新开侧栏域**：侧栏域清单是外部契约
（``SETTINGS_DOMAIN_NAV``，多处用例与截图脚本按名称/索引依赖）。抽签与「天气」
同类——都是"用户主动拉一次、桌宠给一个结果"的互动面，也是本页第四个同级任务；
2026-10-17 之前它曾以一个只有一组的「趣味」一级域存在，按
``docs/SETTINGS-CHANGE-GATES.md`` 的准入（新一级域须"预计包含至少两个稳定设置组"）
判定不合格，已归位到「互动」。

**配置键（config.py 顶层平铺 4 个）**：``divination_enabled`` / ``divination_speak``
/ ``divination_hint``（三个都是**进程级**键，不在 ``PER_SLOT_SETTING_KEYS``：签是一
天一支的进程级状态，不逐宠）与运行时状态 ``divination_state``（**没有设置行**，本页
不读也不写它）。

**2026-10-16 起本页只有签，没有黄历**：她原话「不需要看黄历写宜忌了，我不是说了
忘掉之前的吗」。原先的「今日穿色」一节（当日日柱天干五行 → 宜穿/次吉/避）连同
`pet/almanac.py` 一起删掉了，别再往这里捡回来。
"""
from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ..settings_widgets import SettingRow, SettingsSection, ToggleSwitch


def create_divination_controls(dialog) -> None:
    """在对话框上创建本页控件（幂等；行由 build_divination_rows 装配）。"""
    if getattr(dialog, "divination_enabled_check", None) is not None:
        return
    cfg = dialog.config
    dialog.divination_enabled_check = ToggleSwitch(dialog)
    dialog.divination_enabled_check.setChecked(bool(cfg.get("divination_enabled", True)))

    dialog.divination_speak_check = ToggleSwitch(dialog)
    dialog.divination_speak_check.setChecked(bool(cfg.get("divination_speak", False)))

    dialog.divination_hint_check = ToggleSwitch(dialog)
    dialog.divination_hint_check.setChecked(bool(cfg.get("divination_hint", True)))


def _preview_today(dialog) -> None:
    """「试听今天的签」：走与右键同一条路径出声+出气泡。

    接法与语音报时的试听一致——设置页只碰**父级的公开钩子**（`on_divination_draw`
    由 `app.py` 接到 `AppShell.trigger_divination_draw`），不碰窗口私有成员；
    独立设置进程没有父级 AppShell 时退到本地合成播放。
    """
    parent = dialog.parent() if hasattr(dialog, "parent") else None
    cb = getattr(parent, "on_divination_draw", None)
    if callable(cb):
        cb()
        return
    if getattr(dialog, "standalone", False):
        import datetime as _d

        from .. import divination
        from ..settings_standalone import preview_voice_chime

        preview_voice_chime(dialog, divination.speech_text(
            divination.draw_for_day(_d.date.today())))


def build_divination_rows(dialog) -> list[SettingRow]:
    """本页设置行（objectName 前缀 `settingRow_divination_`，可被设置搜索索引）。"""
    create_divination_controls(dialog)
    preview_btn = QPushButton("试听今天的签", dialog)
    preview_btn.setToolTip("按当前配置立刻抽一次并出声（不动签号：还是今天那一支）")
    preview_btn.clicked.connect(lambda: _preview_today(dialog))
    return [
        SettingRow(
            "divination_enabled",
            "每日一签",
            "右键「抽支签」出当天的签：一天一支，当天再点显示同一支，次日 0 点换新；"
            "跨天一轮不重复（48 支走完再轮回）。签只跟日期有关，不涉及任何个人信息。",
            dialog.divination_enabled_check,
        ),
        SettingRow(
            "divination_speak",
            "抽签时念出来",
            "念一遍「抽签中...」起手，再接签题、四句签诗与解签"
            "（复用语音报时的音色与音频通道）。默认关闭。",
            dialog.divination_speak_check,
        ),
        SettingRow(
            "divination_preview",
            "试听今天的签",
            "按当前配置立刻走一遍抽签（含「抽签中...」与念签文）。不改签号："
            "还是今天那一支，只是让你先听听声音。",
            preview_btn,
        ),
        SettingRow(
            "divination_hint",
            "每天提醒一次",
            "当天还没抽签时，第一次点桌宠冒一行「今日的签还没看」。一天只提示一次，"
            "不会主动弹窗打扰。",
            dialog.divination_hint_check,
        ),
    ]


def build_divination_tab(dialog) -> QWidget:
    """「互动」域的第四个页内标签内容（由 ``settings_interaction`` 挂载）。"""
    content = QWidget(dialog)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)
    layout.addWidget(SettingsSection("抽签", build_divination_rows(dialog), content))
    layout.addStretch(1)
    return content


def save_divination_settings(dialog) -> None:
    """保存委托：三个开关写回 config（顶层扁平键，与 config.py 登记一致）。"""
    if getattr(dialog, "divination_enabled_check", None) is None:
        return
    dialog.config.set("divination_enabled", bool(dialog.divination_enabled_check.isChecked()))
    dialog.config.set("divination_speak", bool(dialog.divination_speak_check.isChecked()))
    dialog.config.set("divination_hint", bool(dialog.divination_hint_check.isChecked()))
