"""联网搜索与读页：解析器、免 Key 兜底、缺 Key 的稳定错误、正文限长。"""

from __future__ import annotations

import urllib.error

import pytest

from petcore.net import errors
from petcore.net import http as net_http
from petcore.net.websearch import (
    SearchError,
    SearchResult,
    fetch_page_text,
    format_results,
    html_to_text,
    parse_baidu_html,
    parse_duckduckgo_html,
    search,
)

_BAIDU_PAGE = """
<div class="result c-container" mu="https://example.test/a">
  <h3><a href="https://example.test/a">第一<b>条</b></a></h3>
  <div class="c-abstract">摘要一</div>
</div>
<div class="result c-container" mu="https://www.baidu.com/s?wd=self">
  <h3>内部链接</h3>
</div>
<div class="result c-container" mu="https://example.test/b">
  <h3>第二条</h3>
  <div>摘要二</div>
</div>
"""

_DDG_PAGE = """
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fc">标题</a>
  <a class="result__snippet">摘要</a>
</div>
"""


# ---------------------------------------------------------------- 解析器


def test_parse_baidu_skips_internal_links_and_keeps_snippets() -> None:
    results = parse_baidu_html(_BAIDU_PAGE, 5)
    assert [item.url for item in results] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert results[0].title.replace(" ", "") == "第一条"
    assert "摘要一" in results[0].snippet


def test_parse_duckduckgo_unwraps_the_redirect() -> None:
    results = parse_duckduckgo_html(_DDG_PAGE, 5)
    assert results == [SearchResult("标题", "https://example.test/c", "摘要")]


def test_html_to_text_drops_scripts_and_keeps_blocks() -> None:
    page = (
        "<html><head><title>标题</title><style>x{}</style></head>"
        "<body><script>bad()</script><p>第一段</p><div>第二段</div></body></html>"
    )
    text = html_to_text(page)
    assert text.startswith("标题：标题")
    assert "第一段" in text and "第二段" in text
    assert "bad()" not in text and "x{}" not in text


def test_format_results_lists_links_and_snippets() -> None:
    rendered = format_results("q", [SearchResult("T", "https://e.test", "S")])
    assert "搜索「q」的结果" in rendered
    assert "https://e.test" in rendered and "摘要：S" in rendered
    assert "没有找到结果" in format_results("q", [])


# ---------------------------------------------------------------- 引擎分派


def test_keyed_engine_without_a_key_fails_before_any_request() -> None:
    with pytest.raises(SearchError) as caught:
        search("query", engine="bocha", api_key="")
    assert caught.value.reason == errors.AUTH
    assert "API Key" in str(caught.value)


def test_unknown_engine_is_a_protocol_error() -> None:
    with pytest.raises(SearchError) as caught:
        search("query", engine="nope")
    assert caught.value.reason == errors.PROTOCOL


def test_tavily_parses_results(monkeypatch) -> None:
    def fake_post(_url, _payload, _headers, **_kwargs):
        return {
            "results": [
                {"title": "标题", "url": "https://e.test/1", "content": "内容"},
                {"title": "第二条", "url": "https://e.test/2", "content": "内容二"},
            ]
        }

    monkeypatch.setattr("petcore.net.websearch._post_json", fake_post)
    results = search("q", engine="tavily", api_key="key", limit=1)
    assert results == [SearchResult("标题", "https://e.test/1", "内容")]


def test_free_engines_fall_back_to_each_other(monkeypatch) -> None:
    def fake_get(url, **_kwargs):
        if "baidu.com" in url:
            return "百度安全验证", "text/html"
        return _DDG_PAGE, "text/html"

    monkeypatch.setattr("petcore.net.websearch._get", fake_get)
    results = search("q", engine="baidu", limit=5)
    assert [item.url for item in results] == ["https://example.test/c"]


def test_free_engine_failure_names_both_attempts(monkeypatch) -> None:
    def fake_get(*_args, **_kwargs):
        raise SearchError("网络不可达", reason=errors.NETWORK)

    monkeypatch.setattr("petcore.net.websearch._get", fake_get)
    with pytest.raises(SearchError) as caught:
        search("q", engine="duckduckgo")
    assert "备用引擎也失败" in str(caught.value)


# ---------------------------------------------------------------- 读页


def test_fetch_page_text_is_truncated(monkeypatch) -> None:
    # 本用例只验证**截断**；目标校验（`validate_target`）另有专门用例。
    # 开发机上开了 fake-ip DNS 时 `e.test` 会被解析到 198.18.0.0/15（RFC 2544 保留段），
    # `is_private_host` 会依法拒绝，于是「截断」永远测不到——把校验收口到放行，
    # 让这条断言只对截断负责。
    monkeypatch.setattr("petcore.net.websearch.validate_target", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "petcore.net.websearch._get",
        lambda *_a, **_k: ("<html><title>T</title><p>" + "字" * 50 + "</p></html>", "text/html"),
    )
    text = fetch_page_text("https://e.test", max_chars=20)
    assert text.endswith("…（已截断）")
    assert len(text) <= 20 + len("…（已截断）")


def test_fetch_page_rejects_non_http_and_binary(monkeypatch) -> None:
    with pytest.raises(SearchError):
        fetch_page_text("file:///etc/passwd")

    monkeypatch.setattr(
        "petcore.net.websearch._get",
        lambda *_a, **_k: ("binary", "application/octet-stream"),
    )
    with pytest.raises(SearchError):
        fetch_page_text("https://e.test/file.bin")


# ---------------------------------------------------------------- 路由污染


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = {"Content-Type": "text/html"}

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def test_search_does_not_lock_the_process_to_direct(monkeypatch) -> None:
    """搜索自己选路由，不得改写进程级「固定直连」结论（否则正式回答不再试代理）。"""
    net_http.reset_transport()
    attempts: list[bool] = []

    def fake_opener(use_proxy, context=None):
        attempts.append(bool(use_proxy))

        class _Opener:
            def open(self, request, timeout=None, **_kwargs):
                if use_proxy:
                    raise urllib.error.URLError("proxy refused")
                return _FakeResponse(b"<html></html>")

        return _Opener()

    monkeypatch.setattr(net_http, "_opener", fake_opener)
    try:
        assert search("q", engine="baidu", timeout=5) == []
        assert attempts == [True, False]
        assert net_http.force_direct() is False

        attempts.clear()
        net_http.urlopen(net_http.build_request("https://example.test/"), timeout=5)
        assert attempts == [True, False]
    finally:
        net_http.reset_transport()
