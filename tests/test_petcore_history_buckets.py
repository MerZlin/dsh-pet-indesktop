from __future__ import annotations

from datetime import datetime

from petcore.chat.history_buckets import (
    BUCKET_MONTH,
    BUCKET_OLDER,
    BUCKET_TODAY,
    BUCKET_WEEK,
    BUCKET_YESTERDAY,
    bucket_key,
    buckets,
)
from petcore.chat.session_store import ChatSession


def _at(year: int, month: int, day: int, hour: int = 10) -> float:
    return datetime(year, month, day, hour).timestamp()


def _session(name: str, stamp: float) -> ChatSession:
    return ChatSession(name, name, [], stamp)


NOW = datetime(2026, 10, 31, 15, 0)  # Saturday


def test_bucket_key_covers_calendar_boundaries() -> None:
    assert bucket_key(_session("a", _at(2026, 10, 31)), NOW) == BUCKET_TODAY
    assert bucket_key(_session("b", _at(2026, 10, 30)), NOW) == BUCKET_YESTERDAY
    assert bucket_key(_session("c", _at(2026, 10, 28)), NOW) == BUCKET_WEEK
    assert bucket_key(_session("d", _at(2026, 10, 5)), NOW) == BUCKET_MONTH
    assert bucket_key(_session("e", _at(2026, 9, 30)), NOW) == BUCKET_OLDER
    assert bucket_key(_session("f", 0.0), NOW) == BUCKET_OLDER
    # 时钟回拨：未来时间不能出现“明天”分区。
    assert bucket_key(_session("g", _at(2026, 11, 2)), NOW) == BUCKET_TODAY


def test_buckets_are_ordered_and_sorted_inside() -> None:
    rows = [
        _session("old", _at(2026, 9, 1)),
        _session("month", _at(2026, 10, 3)),
        _session("week-old", _at(2026, 10, 27)),
        _session("week-new", _at(2026, 10, 29)),
        _session("yesterday", _at(2026, 10, 30)),
        _session("today", _at(2026, 10, 31, 8)),
    ]
    result = buckets(rows, NOW)
    assert [bucket.key for bucket in result] == [
        BUCKET_TODAY,
        BUCKET_YESTERDAY,
        BUCKET_WEEK,
        BUCKET_MONTH,
        BUCKET_OLDER,
    ]
    week = next(bucket for bucket in result if bucket.key == BUCKET_WEEK)
    assert [session.session_id for session in week.sessions] == ["week-new", "week-old"]


def test_empty_buckets_are_omitted() -> None:
    result = buckets([_session("today", _at(2026, 10, 31))], NOW)
    assert len(result) == 1
    assert result[0].key == BUCKET_TODAY
    assert result[0].label == "今天"
