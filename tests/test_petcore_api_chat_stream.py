"""SSE 完整性与取消语义（审计 AUD-15 / AUD-17 / AUD-23 / AUD-24）。

用裸 socket 服务端逐字节控制响应，覆盖真实网关会出现、但普通 HTTP 夹具造不出来的
四种形状：断流、空体、错误帧、多行 `data:`，以及「静默不发的流」上的取消。
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from petcore.api.chat import stream_chat
from petcore.contracts.api import ApiMetadata, ApiRequest
from petcore.net import errors
from petcore.net import http as net_http


@pytest.fixture(autouse=True)
def _direct_only(monkeypatch):
    """本文件的裸 socket 服务端只监听本机：绕过系统代理，避免代理抖动干扰。"""

    def direct_urlopen(request, *, timeout, context=None, opener=None, **kwargs):
        return net_http._opener(False, context).open(request, timeout=timeout, **kwargs)  # noqa: SLF001

    monkeypatch.setattr("petcore.api.chat.urlopen", direct_urlopen)


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

    def __init__(self, payload: bytes, *, stall_after: float | None = None) -> None:
        self.payload = payload
        self.stall_after = stall_after
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.served = 0
        self.saw_eof_at = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            try:
                _drain_request(conn)
                conn.sendall(self.payload)
            except OSError:
                return
            if self.stall_after is None:
                return
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

    def factory(payload: bytes, *, stall_after: float | None = None) -> _RawServer:
        server = _RawServer(payload, stall_after=stall_after)
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
    server = server_factory(_truncated_chunked(body))
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


def test_transport_failure_before_any_delta_still_raises(server_factory) -> None:
    """一个字都没收到时不能假装成功：仍然抛带分类的 NetworkError。"""
    server = server_factory(_truncated_chunked(b""))
    with pytest.raises(errors.NetworkError):
        stream_chat(
            _request(server.port),
            [{"role": "user", "content": "hi"}],
            timeout=5.0,
        )
