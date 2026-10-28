"""取数安全边界：协议白名单、内网拦截、重定向剥头、代理兜底与截断标记。

对应审计 AUD-03 / AUD-04 / AUD-13 / AUD-16 / AUD-18。
"""

from __future__ import annotations

import email.message
import urllib.error
import urllib.request

import pytest

from petcore.net import http
from petcore.net.errors import (
    PROTOCOL,
    HttpStatusError,
    NetworkError,
)
from petcore.net.http import (
    SafeRedirectHandler,
    fetch_url,
    is_private_host,
    validate_target,
)


class _Response:
    def __init__(self, *, status: int = 200, headers: dict | None = None, chunks: list[bytes] | None = None):
        self.status = status
        self.headers = headers or {}
        self._chunks = list(chunks or [b""])

    def read(self, amount: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        return None


class _Raiser:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def open(self, request, timeout=None, **kwargs):  # noqa: ANN001 - urllib seam
        raise self.exc


class _Returns:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def open(self, request, timeout=None, **kwargs):  # noqa: ANN001 - urllib seam
        return self.response


def test_fetch_url_rejects_file_scheme() -> None:
    with pytest.raises(NetworkError) as info:
        fetch_url("file:///C:/Windows/win.ini")
    assert info.value.reason == PROTOCOL


def test_fetch_url_rejects_private_targets_when_disallowed() -> None:
    for url in ("http://127.0.0.1:9/x", "http://10.1.2.3/y", "http://169.254.169.254/latest"):
        with pytest.raises(NetworkError):
            fetch_url(url, allow_private=False)


def test_validate_target_allows_public_by_default() -> None:
    validate_target("https://example.com/x")
    validate_target("http://93.184.216.34/x")


def test_private_host_detection_covers_literals_and_local_names() -> None:
    for host in ("127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1", "localhost", "printer.local"):
        assert is_private_host(host) is True, host
    assert is_private_host("93.184.216.34") is False


def test_redirect_handler_strips_sensitive_headers_across_origins() -> None:
    handler = SafeRedirectHandler()
    request = urllib.request.Request(
        "https://api.example.com/v1/chat",
        headers={"Authorization": "Bearer sk-secret", "X-Api-Key": "sk-secret", "Accept": "text/event-stream"},
    )
    redirected = handler.redirect_request(request, None, 302, "Found", email.message.Message(), "https://evil.example.net/x")
    assert redirected is not None
    headers = {str(key).lower() for key in redirected.headers}
    assert "authorization" not in headers
    assert "x-api-key" not in headers
    assert "accept" in headers


def test_redirect_handler_keeps_headers_on_same_origin() -> None:
    handler = SafeRedirectHandler()
    request = urllib.request.Request("https://api.example.com/a", headers={"Authorization": "Bearer sk"})
    redirected = handler.redirect_request(request, None, 302, "Found", email.message.Message(), "https://api.example.com/b")
    assert redirected is not None
    assert {str(key).lower() for key in redirected.headers} >= {"authorization"}


def test_redirect_handler_refuses_https_downgrade() -> None:
    handler = SafeRedirectHandler()
    request = urllib.request.Request("https://api.example.com/a")
    assert handler.redirect_request(request, None, 302, "Found", email.message.Message(), "http://api.example.com/a") is None


def test_urlopen_rebuilds_the_request_between_attempts() -> None:
    """ProxyHandler 会就地改写 Request；复用同一个对象等于再拨一次代理。"""
    seen: list[tuple[bool, str]] = []

    class _Opener:
        def __init__(self, use_proxy: bool) -> None:
            self.use_proxy = use_proxy

        def open(self, request, timeout=None, **kwargs):  # noqa: ANN001
            seen.append((self.use_proxy, request.host))
            if self.use_proxy:
                # 复刻 ProxyHandler.proxy_open 的就地改写
                request.host = "127.0.0.1:1"
                request.selector = request.full_url
                raise OSError("proxy unreachable")
            return "response"

    http.reset_transport()
    request = urllib.request.Request("http://example.com/x")
    assert http.urlopen(request, timeout=1.0, opener=lambda use_proxy, context=None: _Opener(use_proxy)) == "response"
    assert seen == [(True, "example.com"), (False, "example.com")]


def test_fetch_url_does_not_switch_route_when_retry_after_is_present() -> None:
    headers = email.message.Message()
    headers["Retry-After"] = "2"
    error = urllib.error.HTTPError("https://a.example/x", 500, "boom", headers, None)
    attempts: list[bool] = []

    def factory(use_proxy: bool, context=None):  # noqa: ANN001
        attempts.append(use_proxy)
        return _Raiser(error)

    with pytest.raises(HttpStatusError):
        fetch_url("https://a.example/x", timeout=1.0, opener=factory)
    assert attempts == [True]


def test_fetch_url_switches_route_on_plain_500() -> None:
    headers = email.message.Message()
    error = urllib.error.HTTPError("https://a.example/x", 500, "boom", headers, None)
    attempts: list[bool] = []

    def factory(use_proxy: bool, context=None):  # noqa: ANN001
        attempts.append(use_proxy)
        if use_proxy:
            return _Raiser(error)
        return _Returns(_Response(chunks=[b"ok"]))

    status, _headers, body = fetch_url("https://a.example/x", timeout=1.0, opener=factory)
    assert (status, bytes(body)) == (200, b"ok")
    assert attempts == [True, False]


def test_short_response_is_marked_truncated() -> None:
    def factory(use_proxy: bool, context=None):  # noqa: ANN001
        return _Returns(_Response(headers={"Content-Length": "1000"}, chunks=[b"x" * 20]))

    _status, _headers, body = fetch_url("https://a.example/x", timeout=1.0, opener=factory)
    assert len(body) == 20
    assert body.truncated is True
    assert body.declared_length == 1000


def test_max_bytes_truncation_is_marked() -> None:
    def factory(use_proxy: bool, context=None):  # noqa: ANN001
        return _Returns(_Response(chunks=[b"y" * 400]))

    _status, _headers, body = fetch_url("https://a.example/x", timeout=1.0, max_bytes=100, opener=factory)
    assert len(body) == 100
    assert body.truncated is True


def test_complete_response_is_not_marked_truncated() -> None:
    def factory(use_proxy: bool, context=None):  # noqa: ANN001
        return _Returns(_Response(headers={"Content-Length": "3"}, chunks=[b"abc"]))

    _status, _headers, body = fetch_url("https://a.example/x", timeout=1.0, opener=factory)
    assert body.truncated is False


#: 只有「同一台 API 主机」的调用点可以改写进程级「固定直连」结论
#: （它们的成败代表这台 API 能不能走代理）；其它域一律 `pollute=False`。
_ROUTE_OWNERS = frozenset(
    {
        "petcore/api/chat.py",
        "petcore/api/probe.py",
        "petcore/account/service.py",
    }
)


def test_only_api_hosts_may_pin_the_process_route() -> None:
    """`urlopen(...)` 的每个调用点要么是 API 主机，要么显式 `pollute=False`。

    背景：一次第三方 CDN/搜索请求的「代理失败→直连成功」若固化成进程结论，之后
    所有 API 请求都不再尝试代理——用户看到的是「AI 对话网络不可达」，而真正的原因
    早就不在这条链路上。加新调用点时这条守卫会逼你选一个路由语义。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "petcore"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root.parent).as_posix()
        if relative == "petcore/net/http.py":  # 定义处
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "urlopen":
                continue
            pollute = next(
                (kw.value for kw in node.keywords if kw.arg == "pollute"), None
            )
            none_pollute = isinstance(pollute, ast.Constant) and pollute.value is False
            if relative in _ROUTE_OWNERS or none_pollute:
                continue
            offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "这些 urlopen 调用会改写进程级『固定直连』结论，请传 pollute=False："
        + ", ".join(offenders)
    )
