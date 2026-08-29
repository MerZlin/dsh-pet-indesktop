# -*- coding: utf-8 -*-
"""DeepSeek 余额查询模块测试。"""

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from pet import balance
from pet.chat.models import ChatSession

_BJ = timezone(timedelta(hours=8))


def _bj(hour: int, day=31, month=8, year=2026, weekday_override=None):
    """构造北京时间 datetime；默认 2026-08-31 是周一。"""
    return datetime(year, month, day, hour, tzinfo=_BJ)


def test_format_balance_variants():
    assert balance.format_balance({"total": "12.34", "granted": "2.34", "topped_up": "10.00"}) == \
        "余额 ¥12.34（充值 ¥10.00 / 赠送 ¥2.34）"
    assert balance.format_balance({"total": "5.00", "granted": "", "topped_up": "5.00"}) == \
        "余额 ¥5.00"
    assert balance.format_balance({"total": "", "granted": "", "topped_up": ""}) == "余额信息为空"


def test_fetch_balance_parses_response(monkeypatch):
    body = json.dumps({
        "is_available": True,
        "balance_infos": [{
            "currency": "CNY",
            "total_balance": "12.34",
            "granted_balance": "2.34",
            "topped_up_balance": "10.00",
        }],
    }).encode()

    def fake_urlopen(req, *args, **kwargs):
        # 校验端点与认证头
        assert req.full_url.endswith("/user/balance")
        assert req.get_header("Authorization") == "Bearer sk-test"
        return io.BytesIO(body)

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_urlopen)
    info = balance.fetch_balance("https://api.deepseek.com", "sk-test")
    assert info["total"] == "12.34"
    assert info["granted"] == "2.34"
    assert info["topped_up"] == "10.00"
    assert info["is_available"] is True


def test_fetch_balance_errors(monkeypatch):
    # 无 Key
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "")

    # HTTP 错误（如 401）
    def fake_http(req, *args, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_http)
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "sk-x")

    # 网络失败
    def fake_net(req, *args, **kwargs):
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_net)
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "sk-x")

    # 响应无 balance_infos
    def fake_empty(req, *args, **kwargs):
        return io.BytesIO(json.dumps({"is_available": True}).encode())

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_empty)
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "sk-x")


def test_balance_percent_and_event_index():
    # 余额 20 元 → 未消耗 0%；10 元 → 50%；0/负数 → 100%；非法 → None
    assert balance.balance_percent("20") == 0
    assert balance.balance_percent("10") == 50
    assert balance.balance_percent("0") == 100
    assert balance.balance_percent("-1") == 100
    assert balance.balance_percent("abc") is None
    assert balance.balance_percent("") is None

    # 档位：0..4 对应 [0,20) [20,40) [40,60) [60,80) [80,100)，100 单独第 5 档
    assert balance.balance_event_index(0) == 0
    assert balance.balance_event_index(19.9) == 0
    assert balance.balance_event_index(20) == 1
    assert balance.balance_event_index(59.9) == 2
    assert balance.balance_event_index(80) == 4
    assert balance.balance_event_index(100) == 5


def test_deepseek_pricing_tier():
    # 2026-08-31 是周一：9-12 / 14-18 高峰，其余空闲
    assert balance.deepseek_pricing_tier(_bj(10)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(11)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(13)) == "idle"
    assert balance.deepseek_pricing_tier(_bj(15)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(20)) == "idle"
    # 周六/周日全天空闲
    assert balance.deepseek_pricing_tier(_bj(10, day=29, month=8, year=2026)) == "idle"
    assert balance.deepseek_pricing_tier(_bj(15, day=29, month=8, year=2026)) == "idle"


def test_deepseek_pricing_hint_and_next_switch():
    hint_peak = balance.deepseek_pricing_hint(_bj(10))
    assert "当前高峰" in hint_peak
    assert "下一空闲 12:00" in hint_peak

    hint_idle_midday = balance.deepseek_pricing_hint(_bj(13))
    assert "当前空闲" in hint_idle_midday
    assert "下一高峰 14:00" in hint_idle_midday

    # 周末全天空闲，下一高峰为周一 09:00
    hint_weekend = balance.deepseek_pricing_hint(_bj(15, day=29, month=8, year=2026))
    assert "当前空闲" in hint_weekend
    assert "下一高峰" in hint_weekend


def test_friday_evening_next_peak_skips_weekend():
    # 2026-08-28 是周五，20:00 后下一高峰应为周一 09:00，而不是周六 09:00
    hint = balance.deepseek_pricing_hint(_bj(20, day=28, month=8, year=2026))
    assert "当前空闲" in hint
    assert "下一高峰 09:00" in hint
    next_tier, next_time = balance._next_pricing_switch(
        _bj(20, day=28, month=8, year=2026)
    )
    assert next_tier == "peak"
    assert next_time.weekday() == 0  # Monday
    assert next_time.hour == 9


def test_chat_session_title_roundtrip():
    session = ChatSession.create("cat", "provider", "prompt")
    assert session.title == ""
    session.title = "自定义备注"
    loaded = ChatSession.from_dict(session.to_dict())
    assert loaded.title == "自定义备注"
    # 旧数据无 title 字段 → 默认空串
    data = session.to_dict()
    data.pop("title")
    assert ChatSession.from_dict(data).title == ""
