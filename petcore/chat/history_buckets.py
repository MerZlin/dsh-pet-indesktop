"""会话侧栏的日期分节（零 Qt，纯函数）。

分区口径（与用户可见文案一致）：

- 今天 / 昨天；
- 本周：本周一 0 点起、且不是今天/昨天；
- 本月：本月 1 号 0 点起、且不是本周；
- 更早：其余（含 `updated_at <= 0` 的坏记录）。

每个分区内按 `updated_at` 倒序；空分区不返回。未来的时间戳（时钟回拨）
归入今天，避免出现「明天」这种分区。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

BUCKET_TODAY = "today"
BUCKET_YESTERDAY = "yesterday"
BUCKET_WEEK = "week"
BUCKET_MONTH = "month"
BUCKET_OLDER = "older"

BUCKET_LABELS = {
    BUCKET_TODAY: "今天",
    BUCKET_YESTERDAY: "昨天",
    BUCKET_WEEK: "本周",
    BUCKET_MONTH: "本月",
    BUCKET_OLDER: "更早",
}


@dataclass(frozen=True)
class HistoryBucket:
    key: str
    label: str
    sessions: tuple


def _session_day(session) -> date | None:
    try:
        stamp = float(getattr(session, "updated_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp).date()
    except (OverflowError, OSError, ValueError):
        return None


def bucket_key(session, now: datetime | None = None) -> str:
    """单个会话属于哪个分区；坏时间戳/零值归「更早」。"""
    current = now or datetime.now()
    day = _session_day(session)
    if day is None:
        return BUCKET_OLDER
    today = current.date()
    if day >= today:
        return BUCKET_TODAY
    if day == today - timedelta(days=1):
        return BUCKET_YESTERDAY
    week_start = today - timedelta(days=today.weekday())
    if day >= week_start:
        return BUCKET_WEEK
    if day.year == today.year and day.month == today.month:
        return BUCKET_MONTH
    return BUCKET_OLDER


def buckets(
    sessions: Iterable, now: datetime | None = None
) -> tuple[HistoryBucket, ...]:
    """按日期分区；空分区不返回，分区顺序固定。"""
    grouped: dict[str, list] = {
        BUCKET_TODAY: [],
        BUCKET_YESTERDAY: [],
        BUCKET_WEEK: [],
        BUCKET_MONTH: [],
        BUCKET_OLDER: [],
    }
    for session in sessions or ():
        grouped[bucket_key(session, now)].append(session)
    result: list[HistoryBucket] = []
    for key, items in grouped.items():
        if not items:
            continue
        items.sort(
            key=lambda item: float(getattr(item, "updated_at", 0.0) or 0.0),
            reverse=True,
        )
        result.append(HistoryBucket(key, BUCKET_LABELS[key], tuple(items)))
    return tuple(result)
