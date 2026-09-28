# -*- coding: utf-8 -*-
"""定时提醒的纯逻辑：规则清洗 + 到点判定（不 import Qt）。

三种规则形态（对齐参考实现 deepseek-dafeiyu-pet 的语义，但复用本仓库
``voice_chime_service`` 的"槽位去重"思路）：

- ``daily``：每天某个 ``HH:MM``；
- ``weekly``：某个 ``HH:MM`` 且今天在 ``days`` 里（0=周一 … 6=周日）；
- ``interval``：每隔 ``every`` 分钟（首次调度只盖戳、不立刻炸）。

去重键统一用 ``"YYYY-MM-DD HH:MM"``（interval 用时间戳），所以 20 秒 tick 在
同一分钟内跑三次也只触发一次；唤醒/改系统时间时**错过的直接丢弃、不补报**
（与报时"让位即消费"同口径，避免开机连播）。
"""
from __future__ import annotations

from datetime import datetime

TIMED_RULE_LIMIT = 20
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 内置一条：23:30 该睡了（带今日汇总，接在台词前面）
DEFAULT_TIMED_LINES = (
    "23:30 咯，早点睡嘛，明天的事明天再说",
    "都这个点啦？眼睛也要下班的哦",
)


def default_timed_rules() -> list[dict]:
    """内置规则（每次调用给一份新的，别让 config 与模块常量共用同一个 dict）。"""
    return [
        {
            "id": "builtin-2330", "when": "daily", "time": "23:30", "days": [],
            "every": 60, "on": True, "summary": True, "lines": list(DEFAULT_TIMED_LINES),
        }
    ]


def normalize_hhmm(value, default: str = "") -> str:
    """``"23:30"`` / ``"9:5"`` / ``"930"`` → ``"23:30"``；非法回默认值。"""
    text = str(value or "").strip()
    if not text:
        return default
    if ":" not in text and text.isdigit() and len(text) in (3, 4):
        text = f"{text[:-2]}:{text[-2:]}"
    parts = text.split(":")
    if len(parts) != 2:
        return default
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def clean_days(value) -> list[int]:
    """星期列表清洗：只留 0~6、去重、升序。"""
    out: list[int] = []
    for raw in value or []:
        try:
            day = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in out:
            out.append(day)
    return sorted(out)


def clean_lines(value) -> list[str]:
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def clean_timed_rules(value, *, default_factory=default_timed_rules) -> list[dict]:
    """规则表清洗：非法项丢弃、字段补默认；全非法或不是列表时回内置规则。

    台词为空 / 关闭的规则**保留**（用户在设置里关掉再打开，台词还在），
    但 ``due_rule_ids`` 不会让它说话。
    """
    if not isinstance(value, list):
        return default_factory()
    out: list[dict] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        when = str(raw.get("when") or "daily").strip().lower()
        if when not in {"daily", "weekly", "interval"}:
            when = "daily"
        try:
            every = max(1, int(raw.get("every") or 60))
        except (TypeError, ValueError):
            every = 60
        # daily/weekly 没写台词时补内置台词（它们靠时间点触发，空台词等于不响）；
        # interval 不补——"每隔 N 分钟"的规则不该被塞进"该睡了"这种定时台词。
        lines = clean_lines(raw.get("lines"))
        if not lines and when != "interval":
            lines = list(DEFAULT_TIMED_LINES)
        out.append({
            "id": str(raw.get("id") or f"rule-{index + 1}")[:48],
            "when": when,
            "time": normalize_hhmm(raw.get("time"), "23:30"),
            "days": clean_days(raw.get("days")),
            "every": every,
            "on": bool(raw.get("on", True)),
            "summary": bool(raw.get("summary", False)),
            "lines": lines,
        })
        if len(out) >= TIMED_RULE_LIMIT:
            break
    return out or default_factory()


def describe_rule(rule: dict) -> str:
    """规则的人话描述（菜单/设置页显示用）。"""
    when = str(rule.get("when") or "daily")
    if when == "interval":
        return f"每 {int(rule.get('every') or 60)} 分钟"
    time_text = normalize_hhmm(rule.get("time"), "--:--")
    if when == "weekly":
        days = clean_days(rule.get("days"))
        names = "、".join(WEEKDAY_NAMES[day] for day in days) or "未选星期"
        return f"{names} {time_text}"
    return f"每天 {time_text}"


def due_rule_ids(rules, state, now: datetime, *, stamp: float | None = None):
    """算出这一拍该触发的规则，并返回更新后的去重状态。

    ``state``：``{rule_id: 去重键}``；``stamp``：当前时间戳（interval 用，便于测试注入）。
    返回 ``(fired_rules, new_state)``——**fired 只含真正到点的**，被跳过的规则也会
    把去重键写回（不然同一分钟会被反复问）。
    """
    import time as _time

    stamp = _time.time() if stamp is None else float(stamp)
    hhmm = now.strftime("%H:%M")
    mark = f"{now.strftime('%Y-%m-%d')} {hhmm}"
    fired: list[dict] = []
    new_state = dict(state or {})
    for rule in rules or []:
        if not isinstance(rule, dict) or not rule.get("on", True):
            continue
        if not clean_lines(rule.get("lines")):
            continue
        rule_id = str(rule.get("id") or "")
        if not rule_id:
            continue
        when = str(rule.get("when") or "daily")
        if when == "interval":
            every = max(1, int(rule.get("every") or 60))
            last = new_state.get(rule_id)
            if last is None:
                new_state[rule_id] = f"{stamp:.0f}"      # 首次只盖戳，不立刻炸
                continue
            try:
                elapsed = stamp - float(last)
            except (TypeError, ValueError):
                new_state[rule_id] = f"{stamp:.0f}"
                continue
            if elapsed >= every * 60:
                new_state[rule_id] = f"{stamp:.0f}"
                fired.append(rule)
            continue
        if normalize_hhmm(rule.get("time")) != hhmm:
            continue
        if when == "weekly" and now.weekday() not in clean_days(rule.get("days")):
            continue
        if new_state.get(rule_id) == mark:
            continue
        new_state[rule_id] = mark
        fired.append(rule)
    return fired, new_state
