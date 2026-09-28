# -*- coding: utf-8 -*-
"""免打扰（quiet mode）纯逻辑层：时长、暂存队列、结算文案、穿透判定。

本模块**不 import Qt、不碰 window 实例**——只回答四个问题：

1. 这次免打扰还要多久（``QuietState.left``）；
2. 这条提醒该不该被压住（``alert_survives_quiet``）；
3. 压住的东西放哪儿、最多放多少（``QuietState.hold`` / ``HELD_LIMIT``）；
4. 结束后怎么汇报（``held_summary``）。

展示层（``pet/window_alerts.py`` 的抑制闸门、``pet/quiet_service.py`` 的定时器与菜单）
只按这里的结论执行。这样"该不该闭嘴"的判定可以在没有 Qt 的环境里跑单测。

穿透口径（与 ``window_alerts.alert_survives_suppression`` 的"状态类"集合对齐）：
审批、提问、错误、控制结果、会话结束这些**需要人操作或代表 Agent 状态**的事件
一律穿透免打扰——把它们压掉会让 Agent 卡在等人确认的状态上，那是体验倒退，
不是"安静"。其余（自言自语、换歌、天气、余额、普通提醒）压住并暂存。
"""
from __future__ import annotations

import time

# 菜单里给的四档时长（分钟）；其余数值允许来自 config，但不进菜单
QUIET_OPTIONS: tuple[int, ...] = (15, 30, 60, 120)
DEFAULT_QUIET_MINUTES = 30
MIN_QUIET_MINUTES = 1
MAX_QUIET_MINUTES = 720

# 暂存上限：攒得再多也只留最近 N 条，结束汇报时不会连播到怀疑人生
HELD_LIMIT = 20

# 需要穿透免打扰的事件类型（Agent 生命周期 / 等人操作）
QUIET_SURVIVE_TYPES: frozenset[str] = frozenset({
    "approval", "question", "interaction",
    "approval/resolved", "question/resolved", "interaction/resolved",
    "control", "control-result", "bridge/control-result", "watchdog/control-result",
    "execution/failed", "agent/request-error", "lifecycle",
})

# 免打扰结束后汇报的固定尾句（没有暂存时用）
IDLE_END_LINE = "我一句话都没吭哦"


def _monotonic() -> float:
    """内部时钟统一走 monotonic：改系统时间不该影响"还要安静多久"。"""
    return time.monotonic()


def normalize_minutes(value, default: int = DEFAULT_QUIET_MINUTES) -> int:
    """把配置/菜单传来的时长夹到合法区间；非法值回落默认值。"""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(MIN_QUIET_MINUTES, min(MAX_QUIET_MINUTES, minutes))


def alert_survives_quiet(
    alert_type: str, *, sticky: bool = False, buttons=None, priority: int = 3,
) -> bool:
    """这条提醒要不要穿透免打扰。

    与设置窗口的抑制口径**刻意不同**：设置窗口只压"普通展示"，而免打扰还要
    压掉余额/完成提示这类"有信息但不紧急"的气泡；只有需要人操作或代表
    Agent 状态的事件才穿透（见模块文档）。
    """
    kind = str(alert_type or "").strip().lower()
    if kind in QUIET_SURVIVE_TYPES:
        return True
    # 带按钮的交互气泡（审批/提问/选择）即使 alertType 没登记也要穿透：
    # 用户点不到就等于 Agent 卡死。
    return bool(buttons)


def format_left(seconds) -> str:
    """剩余时间的人话格式：``还剩 42 分钟`` / ``还剩 25 秒``。"""
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        total = 0
    if total >= 60:
        return f"还剩 {total // 60} 分钟"
    return f"还剩 {total} 秒"


def held_summary(count: int, last_text: str = "") -> str:
    """免打扰结束时的汇报句（参考版同款口径，压过的条数说清楚）。"""
    if count <= 0:
        return IDLE_END_LINE
    tail = f"刚才攒了 {count} 条提醒"
    last = str(last_text or "").strip()
    if last:
        tail += f"，最后一条是「{last}」"
    return tail


class QuietState:
    """免打扰状态：只存"到什么时候"和暂存队列，不做任何展示。"""

    __slots__ = ("_until", "_held")

    def __init__(self) -> None:
        self._until: float = 0.0
        self._held: list[str] = []

    # ---------------------------------------------------------- 时长
    def start(self, minutes, *, now: float | None = None) -> int:
        """开始免打扰，返回实际生效的分钟数（已清空上一轮的暂存）。"""
        minutes = normalize_minutes(minutes)
        stamp = _monotonic() if now is None else float(now)
        self._until = stamp + minutes * 60.0
        self._held = []
        return minutes

    def stop(self, *, now: float | None = None) -> int:
        """结束免打扰，返回**剩余秒数**（已经到点时为 0；汇报文案据此选措辞）。"""
        left = self.left(now=now)
        self._until = 0.0
        return left

    def left(self, *, now: float | None = None) -> int:
        """还剩多少秒（0 = 没开）。"""
        stamp = _monotonic() if now is None else float(now)
        return max(0, int(round(self._until - stamp)))

    def active(self, *, now: float | None = None) -> bool:
        return self.left(now=now) > 0

    def expire_if_due(self, *, now: float | None = None) -> bool:
        """到点了就自己关掉并返回 True（定时器每秒调一次）。

        只有"已经开着且时间走完"才算到点：没开（``_until == 0``）和还没到点
        都返回 False——否则刚开的第一次 tick 就会把免打扰立刻结束掉。
        """
        if self._until <= 0:
            return False
        if self.active(now=now):
            return False
        self._until = 0.0
        return True

    # ---------------------------------------------------------- 暂存
    @property
    def held(self) -> list[str]:
        return self._held

    def held_count(self) -> int:
        return len(self._held)

    def hold(self, text: str) -> bool:
        """暂存一条被压住的提醒；空文本不记。返回是否真的记下了。"""
        line = str(text or "").strip()
        if not line:
            return False
        self._held.append(line)
        if len(self._held) > HELD_LIMIT:
            del self._held[:-HELD_LIMIT]
        return True

    def take_held(self) -> list[str]:
        """取走暂存（同时清空），供结束汇报使用。"""
        held, self._held = self._held, []
        return held

    def clear_held(self) -> None:
        self._held = []
