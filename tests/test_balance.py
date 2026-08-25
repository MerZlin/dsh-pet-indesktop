# -*- coding: utf-8 -*-
"""DeepSeek 余额查询模块测试。"""

import io
import json
import urllib.error

import pytest

from pet import balance
from pet.chat.models import ChatSession


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
