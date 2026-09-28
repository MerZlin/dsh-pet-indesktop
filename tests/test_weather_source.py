# -*- coding: utf-8 -*-
"""天气数据源纯逻辑测试（离线：HTTP 出口全部注入假实现）。

覆盖：地名变体、中国天气网「伪 JSON」与实况解析、WMO 码表、城市列表清洗，
以及 ``fetch_weather`` 的「国内源优先 → 兜底源」编排与重试语义。
"""
from __future__ import annotations

from pet import weather_source as ws

CN_SEARCH_BODY = (
    '([{"ref":"101280501~guangdong~汕头~Shantou~shantou~101280501~'
    '广东~汕头~110.35~23.35~广东"}])'
)


def test_city_query_variants_strips_district_suffix():
    assert ws.city_query_variants("鹿城区") == ["鹿城区", "鹿城"]
    assert ws.city_query_variants("温州") == ["温州"]
    assert ws.city_query_variants("  汕头  ") == ["汕头"]
    assert ws.city_query_variants("") == []
    assert ws.city_query_variants(None) == []


def test_fallback_name_variants_adds_city_suffix():
    assert ws.fallback_name_variants("汕头") == ["汕头", "汕头市"]
    assert ws.fallback_name_variants("汕头市") == ["汕头市"]
    assert ws.fallback_name_variants("鹿城区") == ["鹿城区", "鹿城", "鹿城市"]


def test_clean_city_list_keeps_current_first_and_dedupes():
    got = ws.clean_city_list(["上海", "汕头", " 上海 ", "", None], current="汕头")
    assert got == ["汕头", "上海"]


def test_clean_city_list_caps_length():
    cities = [f"城市{i}" for i in range(ws.CITY_LIST_LIMIT + 5)]
    assert len(ws.clean_city_list(cities)) == ws.CITY_LIST_LIMIT


def test_clean_city_truncates_and_trims():
    assert ws.clean_city("  汕头 ") == "汕头"
    assert len(ws.clean_city("城" * 100)) == ws.CITY_MAX_LEN
    assert ws.clean_city(None) == ""


def test_parse_cn_city_search_reads_pseudo_json():
    assert ws.parse_cn_city_search(CN_SEARCH_BODY) == [("101280501", "汕头", "广东")]
    assert ws.parse_cn_city_search("") == []
    assert ws.parse_cn_city_search("(<html>") == []
    assert ws.parse_cn_city_search("([])") == []


def test_parse_cn_sk_ok_and_placeholder():
    assert ws.parse_cn_sk('var dataSK={"temp":"26","weather":"阴"}') == ("26", "阴")
    assert ws.parse_cn_sk('var dataSK={"temp":"999","weather":"阴"}') is None
    assert ws.parse_cn_sk("<html>网页无法访问</html>") is None
    assert ws.parse_cn_sk('var dataSK={"temp":26}') == ("26", "未知天气")
    assert ws.parse_cn_sk('var dataSK={"temp":"abc"}') is None


def test_wmo_zh_falls_back_to_unknown():
    assert ws.wmo_zh(0) == "晴"
    assert ws.wmo_zh("95") == "雷阵雨"
    assert ws.wmo_zh(None) == "未知天气"
    assert ws.wmo_zh(12345) == "未知天气"


def test_format_weather_line():
    assert ws.format_weather_line("汕头", "26", "阴") == "汕头今天 26°，天气阴"
    assert ws.format_weather_line("汕头", "26", "") == "汕头今天 26°，天气未知天气"


def _text_getter(routes: dict):
    """假 text_getter：按 URL 前缀命中返回 body；值为 Exception 时抛出。"""
    calls: list[tuple[str, dict]] = []

    def getter(url, params=None, timeout=6.0):
        calls.append((url, params or {}))
        for prefix, body in routes.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"未预期的 URL：{url}")

    getter.calls = calls
    return getter


def test_fetch_weather_prefers_cn_source():
    getter = _text_getter({
        ws.CN_WEATHER_SEARCH: CN_SEARCH_BODY,
        "https://d1.weather.com.cn/sk_2d/": 'var dataSK={"temp":"26","weather":"阴"}',
    })
    assert ws.fetch_weather("汕头", text_getter=getter) == ("26", "阴")
    assert getter.calls[0][1] == {"cityname": "汕头"}


def test_fetch_weather_retries_cn_once_then_succeeds():
    state = {"first": True}

    def flaky(url, params=None, timeout=6.0):
        if url.startswith(ws.CN_WEATHER_SEARCH):
            if state["first"]:
                state["first"] = False
                raise OSError("connection reset")
            return CN_SEARCH_BODY
        return 'var dataSK={"temp":"26","weather":"阴"}'

    assert ws.fetch_weather("汕头", text_getter=flaky) == ("26", "阴")


def test_fetch_weather_falls_back_to_open_meteo():
    getter = _text_getter({ws.CN_WEATHER_SEARCH: "([])"})

    def json_getter(url, params=None, timeout=6.0):
        if url == ws.OPEN_METEO_GEO:
            return {"results": [{"latitude": 23.35, "longitude": 116.68}]}
        return {"current": {"temperature_2m": 25.4, "weather_code": 61}}

    assert ws.fetch_weather("汕头", text_getter=getter, json_getter=json_getter) == ("25", "小雨")


def test_fetch_weather_returns_none_when_both_fail():
    def boom(*_args, **_kwargs):
        raise OSError("offline")

    assert ws.fetch_weather("汕头", text_getter=boom, json_getter=boom) is None
    assert ws.fetch_weather("", text_getter=boom) is None


def test_lookup_city_uses_cn_source():
    getter = _text_getter({ws.CN_WEATHER_SEARCH: CN_SEARCH_BODY})
    assert ws.lookup_city("汕头", text_getter=getter) == [("汕头（广东）", "汕头")]


def test_lookup_city_falls_back_to_open_meteo(monkeypatch):
    getter = _text_getter({ws.CN_WEATHER_SEARCH: "([])"})
    monkeypatch.setattr(
        ws, "_http_json",
        lambda url, params=None, timeout=6.0: {
            "results": [{"name": "Singapore", "admin1": "", "country": "Singapore"}]
        },
    )
    assert ws.lookup_city("Singapore", text_getter=getter) == [
        ("Singapore（Singapore）", "Singapore")
    ]


def test_lookup_city_returns_query_when_everything_fails(monkeypatch):
    getter = _text_getter({ws.CN_WEATHER_SEARCH: "([])"})
    monkeypatch.setattr(ws, "_http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert ws.lookup_city("某某地", text_getter=getter) == [("某某地", "某某地")]
