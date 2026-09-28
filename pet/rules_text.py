# -*- coding: utf-8 -*-
"""规则表 ↔ 表格文本的纯映射（**不 import Qt**）。

图形编辑器（``pet/rules_editor.py``）把三张规则的每一行摆成一组单元格；
本模块负责"规则 dict ↔ 单元格列表"的来回转换，顺带做校验：
转换失败返回 ``None``（编辑器据此拒绝保存并高亮那一行），不抛异常。

三张表：

- 定时提醒 ``timed_rules``：启用 / 类型 / 时间 / 星期 / 间隔 / 带汇总 / 台词
- 用久了 ``app_usage_rules``：启用 / 进程名 / 分钟 / 重复 / 台词
- 快捷键 ``hotkeys``：启用 / 按键 / 动作 / 台词

文本约定：台词一栏**每行一句**；星期写 ``周一,周三``（空 = 每天都算，
只有 ``weekly`` 用得上）；启用写 ``是/否``（也认 ``1/0``、``true/false``）。
"""
from __future__ import annotations

from . import hotkey_rules, timed_reminder

TIMED_COLUMNS = ("启用", "类型", "时间", "星期", "间隔", "带汇总", "台词")
USAGE_COLUMNS = ("启用", "进程名", "分钟", "重复", "台词")
HOTKEY_COLUMNS = ("启用", "按键", "动作", "台词")

TIMED_KINDS = (("daily", "每天"), ("weekly", "每周"), ("interval", "每隔"))
HOTKEY_ACTS = (("lines", "说一句"), ("weather", "看天气"), ("balance", "看余额"))

YES = "是"
NO = "否"


def flag_to_text(value) -> str:
    return YES if value else NO


def text_to_flag(value, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"是", "1", "true", "yes", "y", "on", "开", "启用"}:
        return True
    if text in {"否", "0", "false", "no", "n", "off", "关", "停用"}:
        return False
    return default


def lines_to_text(lines) -> str:
    return "\n".join(str(item).strip() for item in (lines or []) if str(item).strip())


def text_to_lines(text) -> list[str]:
    return [part.strip() for part in str(text or "").splitlines() if part.strip()]


def weekday_label(days) -> str:
    return "、".join(timed_reminder.WEEKDAY_NAMES[day] for day in timed_reminder.clean_days(days))


def label_to_days(text) -> list[int]:
    """``"周一,周三"`` → ``[0, 2]``；认不出的词直接忽略。"""
    # 顿号、逗号、空格都当分隔符：用户很自然会写"周一 周三"或"周一、周三"
    raw = str(text or "").replace("，", ",").replace("、", ",").replace(" ", ",")
    out: list[int] = []
    for token in raw.split(","):
        name = token.strip()
        if not name:
            continue
        if name in timed_reminder.WEEKDAY_NAMES:
            day = timed_reminder.WEEKDAY_NAMES.index(name)
            if day not in out:
                out.append(day)
        elif name.isdigit() and 0 <= int(name) <= 6:
            day = int(name)
            if day not in out:
                out.append(day)
    return sorted(out)


def _kind_to_text(when: str) -> str:
    for key, label in TIMED_KINDS:
        if key == when:
            return label
    return TIMED_KINDS[0][1]


def _text_to_kind(text) -> str:
    raw = str(text or "").strip().lower()
    for key, label in TIMED_KINDS:
        if raw in {key, label}:
            return key
    return "daily"


def _act_to_text(act: str) -> str:
    for key, label in HOTKEY_ACTS:
        if key == act:
            return label
    return HOTKEY_ACTS[0][1]


def _text_to_act(text) -> str:
    raw = str(text or "").strip().lower()
    for key, label in HOTKEY_ACTS:
        if raw in {key, label}:
            return key
    return "lines"


# ------------------------------------------------------------------ 定时提醒
def timed_rule_to_row(rule: dict) -> list[str]:
    rule = rule or {}
    return [
        flag_to_text(rule.get("on", True)),
        _kind_to_text(str(rule.get("when") or "daily")),
        timed_reminder.normalize_hhmm(rule.get("time"), ""),
        weekday_label(rule.get("days")),
        # 间隔一栏只对"每隔"有意义；每天/每周留空，免得用户以为要填
        str(int(rule.get("every") or 60)) if str(rule.get("when")) == "interval" else "",
        flag_to_text(rule.get("summary", False)),
        lines_to_text(rule.get("lines")),
    ]


def timed_row_to_rule(cells, *, rule_id: str = "") -> dict | None:
    """单元格 → 规则；非法（时间/间隔写不对、类型不认识）返回 None。"""
    if not cells or len(cells) < len(TIMED_COLUMNS):
        return None
    on_text, kind_text, time_text, days_text, every_text, summary_text, lines_text = cells[:7]
    when = _text_to_kind(kind_text)
    time_value = timed_reminder.normalize_hhmm(time_text, "")
    every = 60
    if when == "interval":
        raw = str(every_text or "").strip()
        if not raw.isdigit() or int(raw) < 1:
            return None
        every = min(24 * 60, int(raw))
    elif when != "interval" and not time_value:
        return None                      # 每天/每周必须有合法时间
    lines = text_to_lines(lines_text)
    if when == "interval" and not lines:
        return None                      # 间隔型没台词等于不响，直接拒绝
    return {
        "id": str(rule_id or "")[:48],
        "when": when,
        "time": time_value or "23:30",
        "days": label_to_days(days_text) if when == "weekly" else [],
        "every": every,
        "on": text_to_flag(on_text, True),
        "summary": text_to_flag(summary_text, False),
        "lines": lines or list(timed_reminder.DEFAULT_TIMED_LINES),
    }


# ------------------------------------------------------------------ 用久了提醒
def usage_rule_to_row(exe: str, rule: dict) -> list[str]:
    rule = rule or {}
    return [
        flag_to_text(rule.get("on", True)),
        str(exe or ""),
        str(int(rule.get("minutes") or 60)),
        str(int(rule.get("repeat") or 0)),
        lines_to_text(rule.get("lines")),
    ]


def usage_row_to_rule(cells):
    """单元格 → ``(exe, rule)``；进程名为空或分钟非法返回 None。"""
    if not cells or len(cells) < len(USAGE_COLUMNS):
        return None
    on_text, exe_text, minutes_text, repeat_text, lines_text = cells[:5]
    exe = str(exe_text or "").strip().lower()
    if not exe:
        return None
    minutes_raw = str(minutes_text or "").strip()
    if not minutes_raw.isdigit() or int(minutes_raw) < 1:
        return None
    repeat_raw = str(repeat_text or "").strip() or "0"
    if not repeat_raw.isdigit():
        return None
    lines = text_to_lines(lines_text)
    if not lines:
        return None                      # 没台词就不用配了
    return exe, {
        "minutes": min(24 * 60, int(minutes_raw)),
        "repeat": min(24 * 60, int(repeat_raw)),
        "on": text_to_flag(on_text, True),
        "lines": lines,
    }


# ------------------------------------------------------------------ 全局快捷键
def hotkey_rule_to_row(rule: dict) -> list[str]:
    rule = rule or {}
    return [
        flag_to_text(rule.get("on", True)),
        hotkey_rules.normalize_seq(rule.get("seq")),
        _act_to_text(str(rule.get("act") or "lines")),
        lines_to_text(rule.get("lines")),
    ]


def hotkey_row_to_rule(cells, *, rule_id: str = "") -> dict | None:
    """单元格 → 规则；键位不合法（或落在保留名单）返回 None。"""
    if not cells or len(cells) < len(HOTKEY_COLUMNS):
        return None
    on_text, seq_text, act_text, lines_text = cells[:4]
    seq = hotkey_rules.normalize_seq(seq_text)
    if not hotkey_rules.hotkey_usable(seq):
        return None
    act = _text_to_act(act_text)
    lines = text_to_lines(lines_text)
    if act == "lines" and not lines:
        return None
    return {
        "id": str(rule_id or "")[:48],
        "seq": seq,
        "act": act,
        "on": text_to_flag(on_text, True),
        "lines": lines,
    }
