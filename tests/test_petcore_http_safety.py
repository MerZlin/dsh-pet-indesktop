"""取数安全边界：协议白名单、内网拦截、重定向剥头、代理兜底与截断标记。

对应审计 AUD-03 / AUD-04 / AUD-13 / AUD-16 / AUD-18。
"""

from __future__ import annotations

import email.message
import time
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


@pytest.fixture(autouse=True)
def _fresh_transport():
    """每个用例都从「没有固定直连结论」开始：钉子、直连 opener 全是进程级状态。"""
    http.reset_transport()
    yield
    http.reset_transport()


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


def test_direct_pin_expires_so_proxy_is_probed_again(monkeypatch) -> None:
    """一次代理抖动不该把整个进程钉在直连上：钉子有期限，过期重新试代理。"""
    clock = {"now": 1000.0}
    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http, "_force_direct", True)
    monkeypatch.setattr(http, "_force_direct_until", clock["now"] + 10.0)
    attempts: list[bool] = []

    class _Opener:
        def __init__(self, use_proxy: bool) -> None:
            self.use_proxy = use_proxy

        def open(self, request, timeout=None, **kwargs):  # noqa: ANN001
            attempts.append(self.use_proxy)
            return "response"

    def build(use_proxy: bool, context=None):  # noqa: ANN001
        return _Opener(use_proxy)

    request = urllib.request.Request("http://example.com/x")
    assert http.urlopen(request, timeout=1.0, opener=build) == "response"
    assert attempts == [False]  # 钉子有效期内不再碰代理
    assert http.force_direct() is True

    clock["now"] += 11.0
    attempts.clear()
    assert http.urlopen(request, timeout=1.0, opener=build) == "response"
    assert attempts == [True]  # 过期后重新试代理（代理通就用代理）
    assert http.force_direct() is False


def _http_error(status: int, *, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.example/v1/chat", status, "boom", headers, None)


def _routing(routes: dict[bool, object]):
    """按路由返回「抛异常 / 返回响应」的 opener 工厂，并记下用过的路由。"""
    seen: list[bool] = []

    def factory(use_proxy: bool, context=None):  # noqa: ANN001 - OpenerFactory
        seen.append(use_proxy)
        outcome = routes[use_proxy]
        if isinstance(outcome, BaseException):
            return _Raiser(outcome)
        return _Returns(outcome)  # type: ignore[arg-type]

    return factory, seen


def test_urlopen_switches_route_on_502_from_the_proxy() -> None:
    """代理/VPN 网关回 502 时换直连再问一次：API 正常就不该报「网络不可达」。"""
    headers = email.message.Message()
    seen: list[bool] = []
    hosts: list[str] = []

    class _Gateway502:
        def open(self, request, timeout=None, **kwargs):  # noqa: ANN001 - opener 接缝
            seen.append(True)
            # 复刻 ProxyHandler.proxy_open 的就地改写：换路必须用重建的请求对象。
            hosts.append(request.host)
            request.host = "127.0.0.1:1"
            request.selector = request.full_url
            raise urllib.error.HTTPError(request.full_url, 502, "bad gateway", headers, None)

    class _DirectOk:
        def open(self, request, timeout=None, **kwargs):  # noqa: ANN001
            seen.append(False)
            hosts.append(request.host)
            return _Response(chunks=[b"ok"])

    def factory(use_proxy: bool, context=None):  # noqa: ANN001 - OpenerFactory
        return _Gateway502() if use_proxy else _DirectOk()

    request = urllib.request.Request("https://api.example/v1/chat")
    response = http.urlopen(request, timeout=1.0, opener=factory)

    assert isinstance(response, _Response)
    assert seen == [True, False]
    assert hosts == ["api.example", "api.example"], "换路必须用重建的请求，不能复用被改写的对象"
    assert request.host == "api.example", "原请求对象不许被就地改写"


def test_urlopen_raises_the_last_error_when_both_routes_answer_5xx() -> None:
    """两条路由都 5xx：抛**最后一条**的答复，不是第一条的。"""
    factory, seen = _routing({True: _http_error(502), False: _http_error(500)})
    with pytest.raises(urllib.error.HTTPError) as caught:
        http.urlopen(urllib.request.Request("https://api.example/v1/chat"), timeout=1.0, opener=factory)
    assert caught.value.code == 500
    assert seen == [True, False]


def test_urlopen_does_not_switch_route_when_retry_after_is_present() -> None:
    """带 ``Retry-After`` 的 502 是服务端明确要求等待：换路等于把限流当链路故障。"""
    factory, seen = _routing(
        {True: _http_error(502, retry_after="2"), False: _Response(chunks=[b"ok"])}
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        http.urlopen(urllib.request.Request("https://api.example/v1/chat"), timeout=1.0, opener=factory)
    assert caught.value.code == 502
    assert seen == [True]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_urlopen_does_not_switch_route_on_definitive_answers(status: int) -> None:
    """401/403/429 是服务端对这次请求的答复，换一条路只会得到同一句话。"""
    factory, seen = _routing({True: _http_error(status), False: _Response(chunks=[b"ok"])})
    with pytest.raises(urllib.error.HTTPError) as caught:
        http.urlopen(urllib.request.Request("https://api.example/v1/chat"), timeout=1.0, opener=factory)
    assert caught.value.code == status
    assert seen == [True]


def test_urlopen_route_plan_can_force_direct_only(monkeypatch) -> None:
    """显式路由计划只影响这一次调用：只直连成功不代表整个进程该固定直连。"""
    seen: list[bool] = []

    def factory(use_proxy: bool, context=None):  # noqa: ANN001
        seen.append(use_proxy)
        return _Returns(_Response(chunks=[b"ok"]))

    monkeypatch.setattr(http, "_opener", factory)
    response = http.urlopen(
        urllib.request.Request("https://api.example/v1/chat"),
        timeout=1.0,
        routes=(False,),
    )
    assert isinstance(response, _Response)
    assert seen == [False]
    assert http._force_direct is False  # noqa: SLF001 - 断言「没有写进程级结论」
    assert http._force_direct_until == 0.0  # noqa: SLF001


def test_a_new_pin_is_short_lived(monkeypatch) -> None:
    """钉子只活几十秒：一次抖动不该让进程此后一小时都不再试代理。"""
    clock = {"now": 1000.0}
    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    factory, seen = _routing({True: OSError("proxy down"), False: _Response(chunks=[b"ok"])})
    monkeypatch.setattr(http, "_opener", factory)

    http.urlopen(urllib.request.Request("https://api.example/v1/chat"), timeout=1.0)

    assert seen == [True, False]
    assert http.force_direct() is True
    assert http._force_direct_until - clock["now"] == http.DEFAULT_DIRECT_PIN_SECONDS  # noqa: SLF001
    assert 30.0 <= http.DEFAULT_DIRECT_PIN_SECONDS <= 60.0


def test_pinned_direct_failure_falls_back_to_the_proxy_and_clears_the_pin(monkeypatch) -> None:
    """钉着直连时直连失败：把代理还回去试一次，成功就解除钉子。"""
    monkeypatch.setattr(http, "_force_direct", True)
    monkeypatch.setattr(http, "_force_direct_until", time.monotonic() + 30.0)
    factory, seen = _routing({False: OSError("direct down"), True: _Response(chunks=[b"ok"])})
    monkeypatch.setattr(http, "_opener", factory)

    response = http.urlopen(urllib.request.Request("https://api.example/v1/chat"), timeout=1.0)

    assert isinstance(response, _Response)
    assert seen == [False, True]
    assert http.force_direct() is False


def test_pinned_failure_on_both_routes_clears_the_pin(monkeypatch) -> None:
    """直连和代理都不通：钉子（「直连能用」）的前提没了，下次重新按代理优先试。"""
    monkeypatch.setattr(http, "_force_direct", True)
    monkeypatch.setattr(http, "_force_direct_until", time.monotonic() + 30.0)
    factory, seen = _routing({False: OSError("direct down"), True: OSError("proxy down")})
    monkeypatch.setattr(http, "_opener", factory)

    with pytest.raises(OSError):
        http.urlopen(urllib.request.Request("https://api.example/v1/chat"), timeout=1.0)

    assert seen == [False, True]
    assert http.force_direct() is False


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
