# -*- coding: utf-8 -*-
"""今日汇总纯逻辑测试（不依赖 Qt）。

覆盖"有哪段报哪段、一段都没有时给固定兜底句"的口径，避免把查询失败
说成"今天没花钱"。
"""
from __future__ import annotations

import json
from datetime import datetime

from pet import daily_summary as summary


def test_format_duration_buckets():
    assert summary.format_duration(0) == "0 秒"
    assert summary.format_duration(59) == "59 秒"
    assert summary.format_duration(60) == "1 分"
    assert summary.format_duration(48 * 60) == "48 分"
    assert summary.format_duration(2 * 3600 + 10 * 60) == "2 小时 10 分"
    assert summary.format_duration(3 * 3600) == "3 小时"
    assert summary.format_duration("bad") == "0 秒"


def test_format_money_by_currency():
    assert summary.format_money(1.5) == "¥1.50"
    assert summary.format_money(2, "USD") == "$2.00"
    assert summary.format_money(3, "EUR") == "€3.00"
    assert summary.format_money(4, "JPY") == "JPY 4.00"
    assert summary.format_money("bad") == ""


def test_top_app_usage_threshold_and_order():
    book = {"vs code": 2 * 3600, "浏览器": 48 * 60, "记事本": 60}
    top = summary.top_app_usage(book, min_seconds=20 * 60, max_apps=2)
    assert top == [("vs code", 2 * 3600), ("浏览器", 48 * 60)]
    # 门槛以下不点名
    assert summary.top_app_usage(book, min_seconds=3600, max_apps=2) == [("vs code", 2 * 3600)]
    assert summary.top_app_usage({"记事本": 10}, min_seconds=60) == []
    assert summary.top_app_usage(None) == []


def test_build_summary_without_any_data_returns_fallback():
    assert summary.build_daily_summary() == summary.EMPTY_SUMMARY
    assert summary.build_daily_summary(app_times={"记事本": 5}) == summary.EMPTY_SUMMARY


def test_build_summary_combines_available_parts():
    text = summary.build_daily_summary(
        balance_used=1.32,
        app_times={"vs code": 2 * 3600 + 10 * 60, "浏览器": 48 * 60},
        min_seconds=20 * 60,
        max_apps=2,
    )
    assert text == "今天：余额用了 ¥1.32 · vs code 2 小时 10 分、浏览器 48 分"


def test_build_summary_prefers_balance_text_and_keeps_agent_cost():
    text = summary.build_daily_summary(
        balance_used=1.32,
        balance_text="余额 ¥98.10（现在空闲时段）",
        agent_cost_text="上一轮 ¥0.0039",
    )
    assert "余额用了 ¥1.32" in text
    assert "余额 ¥98.10（现在空闲时段）" in text
    assert text.endswith("上一轮 ¥0.0039")


def test_read_today_usage_handles_missing_stale_and_dirty(tmp_path):
    path = tmp_path / "usage.json"
    # 不存在 / 脏数据 → 0
    assert summary.read_today_usage(path) == 0.0
    path.write_text("not json", encoding="utf-8")
    assert summary.read_today_usage(path) == 0.0
    # 跨天 → 0
    path.write_text(json.dumps({"date": "2000-01-01", "todayUsage": 9.9}), encoding="utf-8")
    assert summary.read_today_usage(path) == 0.0
    # 当天 → 真值
    today = datetime.now().strftime("%Y-%m-%d")
    path.write_text(json.dumps({"date": today, "todayUsage": 1.25}), encoding="utf-8")
    assert summary.read_today_usage(path) == 1.25
