"""SSE 完整性与取消语义（审计 AUD-15 / AUD-17 / AUD-23 / AUD-24）。

用裸 socket 服务端逐字节控制响应，覆盖真实网关会出现、但普通 HTTP 夹具造不出来的
四种形状：断流、空体、错误帧、多行 `data:`，以及「静默不发的流」上的取消。
"""

from __future__ import annotations

import email.message
import http.client
import json
import socket
import struct
import threading
import time
import urllib.error

import pytest

from petcore.api.chat import stream_chat
from petcore.contracts.api import ApiMetadata, ApiRequest
from petcore.net import errors
from petcore.net import http as net_http


@pytest.fixture(autouse=True)
def _direct_only(monkeypatch):
    """本文件的裸 socket 服务端只监听本机：绕过系统代理，避免代理抖动干扰。

    同时把重试间隔压到 0：重试语义要测，但不该让每个用例多睡 0.6 秒。
    """

    def direct_urlopen(request, *, timeout, context=None, opener=None, routes=None, **kwargs):
        return net_http._opener(False, context).open(request, timeout=timeout, **kwargs)  # noqa: SLF001

    monkeypatch.setattr("petcore.api.chat.urlopen", direct_urlopen)
    monkeypatch.setattr("petcore.api.chat.RETRY_DELAY_S", 0.0)


def _reset_connection(conn: socket.socket) -> None:
    """不打招呼就断：SO_LINGER(0) 让 close 直接发 RST（真机的连接重置）。"""
    try:
        conn.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
    except OSError:
        pass


def _drain_request(conn: socket.socket) -> None:
    """读走完整请求（头 + Content-Length 指定的体）。

    只 ``recv`` 一次会在服务端关闭时把未读字节变成 RST，客户端读响应时会报
    「网络不可达」——这是本文件历史偶发失败的根因。
    """
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = conn.recv(4096)
        if not chunk:
            return
        buffer += chunk
    head, _, rest = buffer.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                length = 0
    remaining = length - len(rest)
    while remaining > 0:
        chunk = conn.recv(min(65536, remaining))
        if not chunk:
            return
        remaining -= len(chunk)


class _RawServer:

    def __init__(
        self,
        payload: bytes,
        *,
        stall_after: float | None = None,
        accepts: int = 1,
        failures: int = 0,
    ) -> None:
        self.payload = payload
        self.stall_after = stall_after
        self.accepts = max(1, int(accepts))
        #: 前 ``failures`` 个连接在发出任何字节之前被 RST（真机上的连接重置）。
        self.failures = max(0, int(failures))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.served = 0
        self.accepted = 0
        self.saw_eof_at = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for _ in range(self.accepts):
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            with conn:
                if self.accepted <= self.failures:
                    _reset_connection(conn)
                    continue
                try:
                    _drain_request(conn)
                    conn.sendall(self.payload)
                except OSError:
                    continue
                if self.stall_after is None:
                    continue
                deadline = time.monotonic() + self.stall_after
                conn.settimeout(0.05)
                while time.monotonic() < deadline:
                    try:
                        if not conn.recv(1024):
                            break
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                self.saw_eof_at = time.monotonic()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _request(port: int) -> ApiRequest:
    metadata = ApiMetadata(
        service_id="test",
        name="Test",
        base_url=f"http://127.0.0.1:{port}",
        model="deepseek-chat",
    )
    return ApiRequest(metadata, "secret-key")


def _sse(body: bytes, *, status: int = 200, terminate: bool = True) -> bytes:
    head = (
        f"HTTP/1.1 {status} OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    # 分块编码必须以 0 长度块收尾；否则读取端会一直等到超时。
    return head + body + (b"0\r\n\r\n" if terminate else b"")


def _chunk(payload: bytes) -> bytes:
    return f"{len(payload):X}\r\n".encode() + payload + b"\r\n"


@pytest.fixture
def server_factory():
    servers: list[_RawServer] = []

    def factory(
        payload: bytes,
        *,
        stall_after: float | None = None,
        accepts: int = 1,
        failures: int = 0,
    ) -> _RawServer:
        server = _RawServer(
            payload, stall_after=stall_after, accepts=accepts, failures=failures
        )
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


def test_stream_without_done_is_flagged_incomplete(server_factory) -> None:
    body = _chunk(b'data: {"choices":[{"delta":{"content":"\xe5\x8d\x8a\xe6\x9d\xa1"}}]}\n\n')
    server = server_factory(_sse(body))
    result = stream_chat(_request(server.port), [{"role": "user", "content": "hi"}], timeout=5.0)
    assert result.text == "半条"
    assert result.incomplete is True
    assert result.end_reason == "eof"


def test_empty_body_is_flagged_incomplete(server_factory) -> None:
    server = server_factory(_sse(b""))
    result = stream_chat(_request(server.port), [{"role": "user", "content": "hi"}], timeout=5.0)
    assert result.text == ""
    assert result.incomplete is True
    assert result.end_reason == "empty"


def test_done_marks_the_stream_complete(server_factory) -> None:
    body = _chunk(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n') + _chunk(b"data: [DONE]\n\n")
    server = server_factory(_sse(body))
    result = stream_chat(_request(server.port), [{"role": "user", "content": "hi"}], timeout=5.0)
    assert result.text == "ok"
    assert result.incomplete is False
    assert result.end_reason == "done"


def test_multi_line_data_field_is_accepted(server_factory) -> None:
    payload = b'{"choices":[{"delta":{"content":"hello"}}]}'
    half = len(payload) // 2
    body = _chunk(b"data: " + payload[:half] + b"\ndata: " + payload[half:] + b"\n\n") + _chunk(b"data: [DONE]\n\n")
    server = server_factory(_sse(body))
    result = stream_chat(_request(server.port), [{"role": "user", "content": "hi"}], timeout=5.0)
    assert result.text == "hello"


def test_error_frame_is_raised_not_swallowed(server_factory) -> None:
    body = _chunk(b'data: {"error":{"code":401,"message":"invalid key"}}\n\n') + _chunk(b"data: [DONE]\n\n")
    server = server_factory(_sse(body))
    with pytest.raises(errors.NetworkError) as info:
        stream_chat(_request(server.port), [{"role": "user", "content": "hi"}], timeout=5.0)
    assert info.value.reason == errors.AUTH


def test_plain_json_body_is_complete(server_factory) -> None:
    payload = b'{"choices":[{"message":{"content":"whole"},"finish_reason":"stop"}]}'
    head = (
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    ).encode()
    server = server_factory(head + payload)
    result = stream_chat(_request(server.port), [{"role": "user", "content": "hi"}], timeout=5.0)
    assert result.text == "whole"
    assert result.incomplete is False


def test_cancel_interrupts_a_stalled_stream(server_factory) -> None:
    body = _chunk(b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n')
    # 不写终止块：连接保持打开但不再有数据，这才是「静默不发的流」。
    server = server_factory(_sse(body, terminate=False), stall_after=6.0)
    cancel = threading.Event()

    def _stop_later() -> None:
        time.sleep(0.4)
        cancel.set()

    threading.Thread(target=_stop_later, daemon=True).start()
    started = time.monotonic()
    result = stream_chat(
        _request(server.port),
        [{"role": "user", "content": "hi"}],
        timeout=30.0,
        cancel=cancel,
    )
    elapsed = time.monotonic() - started
    assert result.cancelled is True
    assert result.end_reason == "cancelled"
    assert elapsed < 3.0, f"取消耗时 {elapsed:.2f}s，说明阻塞读没被打断"


def _truncated_chunked(body: bytes) -> bytes:
    """声明比实际更长的分块：读端在块中途撞到 EOF（真实网关断流就是这样）。"""
    head = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    return head + f"{len(body) + 64:X}\r\n".encode() + body


def test_transport_failure_after_a_delta_keeps_what_was_received(server_factory) -> None:
    """已经吐出的正文不能因为断流被抹掉：返回半条 + interrupted，而不是抛错。"""
    body = b'data: {"choices":[{"delta":{"content":"\xe5\x8d\x8a\xe6\x9d\xa1"}}]}\n\n'
    server = server_factory(_truncated_chunked(body), accepts=2)
    statuses: list[str] = []
    result = stream_chat(
        _request(server.port),
        [{"role": "user", "content": "hi"}],
        on_status=statuses.append,
        timeout=5.0,
    )
    assert result.text == "半条"
    assert result.incomplete is True
    assert result.end_reason == "interrupted"
    assert result.cancelled is False
    assert any("已保留前面的内容" in line for line in statuses)
    # 已经给用户看过的正文绝不重发：服务端只该看到一次连接。
    assert server.accepted == 1


def test_transport_failure_before_any_delta_still_raises(server_factory) -> None:
    """一个字都没收到时不能假装成功：仍然抛带分类的 NetworkError。

    半截分块（服务端说了更长的长度就断）分类是 ``protocol``。它**可以换路由重发**
    （代理缓冲/空闲掐断正是这个形状），但三次都只拿到半截时仍然必须抛错，
    绝不当成一条完整回答交给用户。
    """
    server = server_factory(_truncated_chunked(b""), accepts=4)
    statuses: list[str] = []
    with pytest.raises(errors.NetworkError) as caught:
        stream_chat(
            _request(server.port),
            [{"role": "user", "content": "hi"}],
            on_status=statuses.append,
            timeout=5.0,
        )
    assert caught.value.reason == errors.PROTOCOL
    assert server.accepted == 3
    assert sum("重试" in line for line in statuses) == 2


def test_transient_reset_before_first_byte_is_retried(server_factory) -> None:
    """首连被 RST、次连正常：用户看到的是回答，不是「网络不可达」。"""
    body = _chunk(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n') + _chunk(
        b"data: [DONE]\n\n"
    )
    server = server_factory(_sse(body), accepts=2, failures=1)
    statuses: list[str] = []
    result = stream_chat(
        _request(server.port),
        [{"role": "user", "content": "hi"}],
        on_status=statuses.append,
        timeout=5.0,
    )
    assert result.text == "ok"
    assert result.end_reason == "done"
    assert server.accepted == 2
    assert any("重试" in line for line in statuses)


def test_two_failed_opens_end_in_network_error(server_factory) -> None:
    """每次换路由都连不上才报「网络不可达」：重试不是无限重试。"""
    server = server_factory(_sse(b""), accepts=4, failures=4)
    statuses: list[str] = []
    with pytest.raises(errors.NetworkError) as caught:
        stream_chat(
            _request(server.port),
            [{"role": "user", "content": "hi"}],
            on_status=statuses.append,
            timeout=5.0,
        )
    assert caught.value.reason == errors.NETWORK
    assert server.accepted == 3
    assert sum("重试" in line for line in statuses) == 2


# ---------------------------------------------------------------- 换路由重试


class _FakeStream:
    """``_stream_once`` 用到的最小响应替身：吐完 ``lines`` 再按需抛一次 ``error``。"""

    def __init__(
        self,
        lines: list[bytes] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self._lines = list(lines or [])
        self._error = error
        self.closed = False

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        return b""

    def close(self) -> None:
        self.closed = True


def _sse_text(text: str) -> list[bytes]:
    event = {"choices": [{"delta": {"content": text}}]}
    return [
        f"data: {json.dumps(event, ensure_ascii=False)}\n".encode(),
        b"\n",
        b"data: [DONE]\n",
        b"\n",
    ]


def _fake_urlopen(monkeypatch, responses):
    """按调用顺序返回响应，并记下每次收到的路由计划（``None`` = 默认计划）。"""
    calls: list[tuple[bool, ...] | None] = []

    def opener(request, *, timeout, context=None, opener=None, routes=None, **kwargs):
        calls.append(routes)
        index = min(len(calls), len(responses)) - 1
        response = responses[index]
        return response() if callable(response) else response

    monkeypatch.setattr("petcore.api.chat.urlopen", opener)
    return calls


def test_proxy_502_falls_back_to_direct_and_the_chat_succeeds(monkeypatch) -> None:
    """代理回 502、直连正常：整轮回答成功，且换路由真的发生过。

    这是用户报的「网络不可达」里最常见的一种：API 完全正常，只是系统代理/VPN
    的网关回了个 502。
    """
    headers = email.message.Message()
    seen: list[bool] = []

    class _Gateway502:
        def open(self, request, timeout=None, **kwargs):  # noqa: ANN001 - opener 接缝
            raise urllib.error.HTTPError(
                request.full_url, 502, "bad gateway", headers, None
            )

    class _DirectOk:
        def open(self, request, timeout=None, **kwargs):  # noqa: ANN001
            return _FakeStream(_sse_text("ok"))

    def factory(use_proxy: bool, context=None):  # noqa: ANN001 - OpenerFactory
        seen.append(use_proxy)
        return _Gateway502() if use_proxy else _DirectOk()

    monkeypatch.setattr("petcore.api.chat.urlopen", net_http.urlopen)
    result = stream_chat(
        _request(1),
        [{"role": "user", "content": "hi"}],
        timeout=5.0,
        opener=factory,
    )

    assert result.text == "ok"
    assert result.end_reason == "done"
    assert seen == [True, False], "代理 502 之后必须换直连再试，而不是直接报错"


def test_stream_break_before_content_retries_on_the_direct_route(monkeypatch) -> None:
    """代理 200 之后把流掐断：第二次**只走直连**，用户拿到的是回答而不是错误。"""
    calls = _fake_urlopen(
        monkeypatch,
        [
            lambda: _FakeStream(error=http.client.IncompleteRead(b"")),
            lambda: _FakeStream(_sse_text("ok")),
        ],
    )
    statuses: list[str] = []
    result = stream_chat(
        _request(1),
        [{"role": "user", "content": "hi"}],
        on_status=statuses.append,
        timeout=5.0,
    )

    assert result.text == "ok"
    assert result.end_reason == "done"
    assert calls == [None, (False,)], "第二次必须是「只直连」，不能重发同一条代理"
    assert any("重试" in line for line in statuses)


def test_incomplete_read_retries_direct_then_proxy(monkeypatch) -> None:
    """前两次断流、第三次成功：路由计划依次是 默认 → 只直连 → 只代理。"""
    calls = _fake_urlopen(
        monkeypatch,
        [
            lambda: _FakeStream(error=http.client.IncompleteRead(b"")),
            lambda: _FakeStream(error=http.client.BadStatusLine("")),
            lambda: _FakeStream(_sse_text("ok")),
        ],
    )
    result = stream_chat(_request(1), [{"role": "user", "content": "hi"}], timeout=5.0)

    assert result.text == "ok"
    assert calls == [None, (False,), (True,)]


def test_route_retries_are_capped_at_three_attempts(monkeypatch) -> None:
    """三次都断流就收手：不许无限重发，也不许把半截当成功。"""
    calls = _fake_urlopen(
        monkeypatch,
        [lambda: _FakeStream(error=http.client.IncompleteRead(b""))],
    )
    statuses: list[str] = []
    with pytest.raises(errors.NetworkError) as caught:
        stream_chat(
            _request(1),
            [{"role": "user", "content": "hi"}],
            on_status=statuses.append,
            timeout=5.0,
        )

    assert caught.value.reason == errors.PROTOCOL
    assert calls == [None, (False,), (True,)]
    assert sum("重试" in line for line in statuses) == 2


def test_gateway_status_error_is_not_retried(monkeypatch) -> None:
    """两条路由都答 5xx（``urlopen`` 已经问过了）之后不再重发。

    再发只会拿到同一句答复，却让用户多等三个往返。
    """
    headers = email.message.Message()
    calls: list[tuple[bool, ...] | None] = []

    def fake_urlopen(request, *, timeout, context=None, opener=None, routes=None, **kwargs):
        calls.append(routes)
        raise urllib.error.HTTPError(request.full_url, 502, "bad gateway", headers, None)

    monkeypatch.setattr("petcore.api.chat.urlopen", fake_urlopen)
    statuses: list[str] = []
    with pytest.raises(errors.NetworkError) as caught:
        stream_chat(
            _request(1),
            [{"role": "user", "content": "hi"}],
            on_status=statuses.append,
            timeout=5.0,
        )

    assert caught.value.reason == errors.NETWORK  # 502 → 网络不可达（既有分类）
    assert calls == [None]
    assert not any("重试" in line for line in statuses)


def test_no_retry_once_content_was_emitted(monkeypatch) -> None:
    """已经吐出的正文绝不重发（重发会把同一段再打一遍）。"""
    calls = _fake_urlopen(
        monkeypatch,
        [
            lambda: _FakeStream(
                _sse_text("半条")[:2], error=http.client.IncompleteRead(b"")
            ),
            lambda: _FakeStream(_sse_text("不该出现")),
        ],
    )
    result = stream_chat(_request(1), [{"role": "user", "content": "hi"}], timeout=5.0)

    assert result.text == "半条"
    assert result.end_reason == "interrupted"
    assert calls == [None]
