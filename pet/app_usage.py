# -*- coding: utf-8 -*-
"""用久了提醒的纯逻辑：规则清洗 + 连续使用时长状态机（不 import Qt）。

口径（对齐参考实现 deepseek-dafeiyu-pet 的实测经验）：

- **只在"人在用"时累计**：键鼠静了 ``FG_IDLE_FREEZE`` 秒就当人不在（看电影、挂机、
  人走开都不算），否则回来会被冤枉；
- **换应用就重新计时**：前台 exe 变了就重置累计与已触发次数；
- **首次 + 每 N 分钟重复**：``repeat=0`` 表示只提醒一次；``repeat=30`` 表示
  首次到点后再每 30 分钟补一句；
- 台词里可以用 ``{app}`` / ``{minutes}`` / ``{hm}``。

OS 交互（前台进程名、空闲秒数）在 ``pet/app_usage_service.py``；本模块只做判定，
因此能离线单测（见 ``tests/test_app_usage.py``）。
"""
from __future__ import annotations

FG_IDLE_FREEZE = 600          # 键鼠静了这么久（秒）就当人不在：不累计、不提醒
USAGE_RULE_LIMIT = 40

# 内置几条最常见的（用户可以在 config.json 的 app_usage_rules 里改/加/删）
DEFAULT_USAGE_RULES = {
    "steam.exe": {
        "minutes": 120, "repeat": 60, "on": True,
        "lines": ["{app} 都玩了 {hm} 啦，主人要不要起来走两步？"],
    },
    "wegame.exe": {
        "minutes": 120, "repeat": 60, "on": True,
        "lines": ["{app} 已经开了 {hm} 哦，人家替你看着时间呢～"],
    },
    "code.exe": {
        "minutes": 90, "repeat": 45, "on": True,
        "lines": ["主人已经在 {app} 里坐了 {hm} 啦，喝口水嘛～"],
    },
    "chrome.exe": {
        "minutes": 90, "repeat": 60, "on": True,
        "lines": ["{app} 开了 {hm} 咯，眼睛也要休息的哦"],
    },
}


def format_duration(seconds) -> str:
    """秒 → ``2 小时 10 分`` / ``48 分``。"""
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        total = 0
    if total >= 3600:
        hours, rest = divmod(total, 3600)
        minutes = rest // 60
        return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"
    if total >= 60:
        return f"{total // 60} 分"
    return f"{total} 秒"


def fill_vars(text: str, **values) -> str:
    """台词占位符替换：``{app}`` / ``{minutes}`` / ``{hm}``；未知占位符原样保留。"""
    out = str(text or "")
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def clean_rules(value, *, default_factory=lambda: dict(DEFAULT_USAGE_RULES)) -> dict:
    """规则表清洗：``{exe: {minutes, repeat, on, lines}}``；非法项丢弃、上限 40 条。"""
    if not isinstance(value, dict):
        return {exe: dict(rule) for exe, rule in default_factory().items()}
    out: dict[str, dict] = {}
    for exe, raw in value.items():
        name = str(exe or "").strip().lower()
        if not name or not isinstance(raw, dict):
            continue
        try:
            minutes = max(1, min(24 * 60, int(raw.get("minutes") or 60)))
        except (TypeError, ValueError):
            minutes = 60
        try:
            repeat = max(0, min(24 * 60, int(raw.get("repeat") or 0)))
        except (TypeError, ValueError):
            repeat = 0
        lines = [str(item).strip() for item in (raw.get("lines") or []) if str(item).strip()]
        out[name] = {
            "minutes": minutes, "repeat": repeat,
            "on": bool(raw.get("on", True)), "lines": lines,
        }
        if len(out) >= USAGE_RULE_LIMIT:
            break
    return out or {exe: dict(rule) for exe, rule in default_factory().items()}


class UsageTracker:
    """连续使用状态机（纯内存，喂假时间戳即可测）。

    ``feed(exe, now, idle_seconds)`` 每拍调一次，返回"这一刻该说的话"或 ``None``。
    """

    def __init__(self, rules: dict | None = None) -> None:
        self.rules = clean_rules(rules) if rules is not None else clean_rules(None)
        self.reset()

    def reset(self) -> None:
        self._exe = ""
        self._accum = 0.0
        self._clock: float | None = None
        self._fired = 0

    def set_rules(self, rules) -> None:
        self.rules = clean_rules(rules)

    def feed(self, exe, now: float, idle_seconds=None) -> str | None:
        """喂一拍；返回该说的台词（没到点 / 人不在 / 规则关着都返回 None）。"""
        name = str(exe or "").strip().lower()
        if not name or name != self._exe:
            # 换应用（或第一次）：重新计时，本轮一次都不欠
            self._exe = name
            self._accum = 0.0
            self._clock = now
            self._fired = 0
            return None
        # 注意：clock 可能是合法的 0.0，不能用 `or now` 兜底（那样首拍之后
        # 所有 gap 都会算成 0，永远到不了阈值——初版就是这个 bug）。
        base = self._clock if self._clock is not None else now
        gap = max(0.0, float(now) - float(base))
        self._clock = now
        if idle_seconds is not None:
            try:
                if float(idle_seconds) > FG_IDLE_FREEZE:
                    return None            # 人不在：不累计、也不提醒
            except (TypeError, ValueError):
                pass
        self._accum += gap
        rule = self.rules.get(name)
        if not isinstance(rule, dict) or not rule.get("on", True):
            return None
        lines = rule.get("lines") or []
        if not lines:
            return None
        minutes = int(rule.get("minutes") or 60)
        used = self._accum / 60.0
        if used < minutes:
            return None
        repeat = int(rule.get("repeat") or 0)
        due = 1 if repeat <= 0 else 1 + int((used - minutes) // repeat)
        if due <= self._fired:
            return None
        # 先记账再交话：全局开关关着时由服务层决定不冒泡，但计数照记——
        # 否则"关掉开关一段时间再打开"会一次性补出一串旧提醒。
        self._fired = due
        index = (due - 1) % len(lines)
        return fill_vars(lines[index], app=name, minutes=int(used),
                         hm=format_duration(used * 60))
