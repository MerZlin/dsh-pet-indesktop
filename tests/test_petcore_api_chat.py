"""流式对话传输：SSE、非流式回退、取消、错误分类。"""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from petcore.api.chat import (
    ChatCancelled,
    ChatTruncated,
    build_payload,
    inject_web_unavailable_notice,
    stream_chat,
)
from petcore.contracts.api import ApiMetadata, ApiRequest, WebSearchOptions
from petcore.net import errors
from petcore.net import http as net_http
from petcore.net import websearch


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "sse"
    planner_mode = "ok"
    seen: list[dict] = []
    tool_calls = 0

    def log_message(self, *_args):  # noqa: A003 - 静音
        return

    def do_POST(self):  # noqa: N802 - HTTP 方法名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8"))
        type(self).seen.append(
            {"body": body, "auth": self.headers.get("Authorization", "")}
        )
        if body.get("tools"):
            if type(self).planner_mode == "500":
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if type(self).planner_mode == "badjson":
                data = b"not-json"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._planner()
            return
        if type(self).mode == "auth":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if type(self).mode == "rate":
            self.send_response(429)
            self.send_header("Retry-After", "3")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if type(self).mode == "json":
            payload = {
                "choices": [{"message": {"content": "一次性回答"}, "finish_reason": "stop"}]
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if type(self).mode == "reasoning":
            self._sse([])
            return
        if type(self).mode == "truncated":
            self._sse([], finish="length")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        chunks = ["你", "好", "！"]
        for chunk in chunks:
            payload = {"choices": [{"delta": {"content": chunk}}]}
            line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            self._write_chunk(line)
        done = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        self._write_chunk(f"data: {json.dumps(done)}\n\n".encode())
        self._write_chunk(b"data: [DONE]\n\n")
        self._write_chunk(b"")

    def _planner(self) -> None:
        """联网检索轮：第一次给一个 web_search 工具调用，之后直接说无需搜索。"""
        if type(self).tool_calls == 0:
            type(self).tool_calls += 1
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": json.dumps({"query": "deepseek"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            payload = {"choices": [{"message": {"content": "无需搜索"}}]}
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, chunks, finish: str = "stop") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            payload = {"choices": [{"delta": {"content": chunk}}]}
            self._write_chunk(
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            )
        thought = {"choices": [{"delta": {"reasoning_content": "先想一下。"}}]}
        self._write_chunk(
            f"data: {json.dumps(thought, ensure_ascii=False)}\n\n".encode("utf-8")
        )
        if finish == "stop":
            payload = {"choices": [{"delta": {"content": "答案"}}]}
            self._write_chunk(
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            )
        done = {"choices": [{"delta": {}, "finish_reason": finish}]}
        self._write_chunk(f"data: {json.dumps(done)}\n\n".encode())
        self._write_chunk(b"data: [DONE]\n\n")
        self._write_chunk(b"")

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # 取消时客户端会先关连接，服务端写失败是预期噪声。
        return


@pytest.fixture()
def server():
    _Handler.mode = "sse"
    _Handler.planner_mode = "ok"
    _Handler.seen = []
    _Handler.tool_calls = 0
    httpd = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch):
    """本文件全部打本机服务端：本机的代理/VPN 配置不许改变「发了几次请求」这类断言。

    尤其是 5xx 换路由：没有系统代理时「代理」与「直连」是同一条路，真机上
    不必也不能为此多发一次请求（``petcore/net/http.py::_has_other_route``）。
    """
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})


def _request(httpd, *, model: str = "deepseek-chat") -> ApiRequest:
    metadata = ApiMetadata(
        service_id="test",
        name="Test",
        base_url=f"http://127.0.0.1:{httpd.server_address[1]}",
        model=model,
    )
    return ApiRequest(metadata, "secret-key")


def test_stream_chat_collects_deltas_in_order(server) -> None:
    seen: list[str] = []
    result = stream_chat(
        _request(server),
        [{"role": "user", "content": "在吗"}],
        on_delta=seen.append,
        timeout=5,
    )
    assert result.text == "你好！"
    assert result.chunks == 3
    assert seen == ["你", "好", "！"]
    assert result.cancelled is False
    assert _Handler.seen[0]["auth"] == "Bearer secret-key"
    assert _Handler.seen[0]["body"]["stream"] is True


def test_stream_chat_falls_back_to_one_shot_json(server) -> None:
    _Handler.mode = "json"
    result = stream_chat(_request(server), [{"role": "user", "content": "hi"}], timeout=5)
    assert result.text == "一次性回答"
    assert result.chunks == 1


def test_stream_chat_reports_auth_and_rate_limits(server) -> None:
    _Handler.mode = "auth"
    with pytest.raises(errors.NetworkError) as caught:
        stream_chat(_request(server), [{"role": "user", "content": "x"}], timeout=5)
    assert caught.value.reason == errors.AUTH

    _Handler.mode = "rate"
    with pytest.raises(errors.NetworkError) as caught:
        stream_chat(_request(server), [{"role": "user", "content": "x"}], timeout=5)
    assert caught.value.reason == errors.RATE_LIMIT


def test_stream_chat_stops_on_cancel(server) -> None:
    cancel = threading.Event()

    def on_delta(_delta: str) -> None:
        cancel.set()

    result = stream_chat(
        _request(server),
        [{"role": "user", "content": "x"}],
        on_delta=on_delta,
        cancel=cancel,
        timeout=5,
    )
    assert result.cancelled is True
    assert result.text == "你"


def test_build_payload_respects_model_and_temperature() -> None:
    request = _request(type("S", (), {"server_address": ("127.0.0.1", 1)})(), model="m1")
    payload = build_payload(
        request, [{"role": "user", "content": "hi"}], model="m2", temperature=9
    )
    assert payload["model"] == "m2"
    assert payload["temperature"] == 2.0
    assert payload["stream"] is True
    default = build_payload(request, [])
    assert default["model"] == "m1"


def test_chat_cancelled_is_not_a_network_error() -> None:
    assert issubclass(ChatCancelled, RuntimeError)


# ---------------------------------------------------------------- 深度思考


def test_thinking_payload_only_sends_for_explicit_modes(server) -> None:
    request = _request(server)
    auto = build_payload(request, [{"role": "user", "content": "hi"}])
    assert "thinking" not in auto

    enabled = build_payload(
        request, [], thinking="enabled", reasoning_effort="max"
    )
    assert enabled["thinking"] == {"type": "enabled"}
    assert enabled["reasoning_effort"] == "max"

    disabled = build_payload(request, [], thinking="disabled")
    assert disabled["thinking"] == {"type": "disabled"}


def test_reasoning_deltas_stay_out_of_the_answer(server) -> None:
    _Handler.mode = "reasoning"
    thoughts: list[str] = []
    result = stream_chat(
        _request(server),
        [{"role": "user", "content": "为什么"}],
        thinking="enabled",
        on_reasoning=thoughts.append,
        timeout=5,
    )
    assert thoughts == ["先想一下。"]
    assert result.reasoning == "先想一下。"
    assert result.text == "答案"
    assert _Handler.seen[0]["body"]["thinking"] == {"type": "enabled"}


def test_thinking_only_with_length_reports_truncation(server) -> None:
    _Handler.mode = "truncated"
    with pytest.raises(ChatTruncated):
        stream_chat(
            _request(server),
            [{"role": "user", "content": "长问题"}],
            thinking="enabled",
            timeout=5,
        )


# ---------------------------------------------------------------- 联网搜索


def _dialogue() -> list[dict]:
    return [
        {"role": "system", "content": "你是一只桌宠"},
        {"role": "user", "content": "DeepSeek 现在多少钱"},
    ]


def test_web_search_injects_notes_and_reports_progress(server, monkeypatch) -> None:
    monkeypatch.setattr(
        websearch,
        "search",
        lambda *_args, **_kwargs: [
            websearch.SearchResult("官方定价", "https://example.test/p", "每百万 token")
        ],
    )
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="bocha", max_results=2, fetch_pages=False),
        search_key="search-key",
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert any("正在搜索" in line for line in statuses)
    assert any("已获取联网资料" in line for line in statuses)

    planner = _Handler.seen[0]["body"]
    assert planner["stream"] is False
    assert planner["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in planner["tools"]] == ["web_search"]

    answer = _Handler.seen[-1]["body"]
    assert answer.get("tools") is None
    assert answer["messages"][0]["role"] == "system"
    assert "联网搜索结果" in answer["messages"][0]["content"]
    assert "官方定价" in answer["messages"][0]["content"]
    assert "你是一只桌宠" in answer["messages"][0]["content"]


def test_web_search_off_makes_no_extra_request(server, monkeypatch) -> None:
    searched: list[int] = []
    monkeypatch.setattr(
        websearch, "search", lambda *_a, **_k: searched.append(1) or []
    )
    stream_chat(_request(server), _dialogue(), timeout=5)

    assert len(_Handler.seen) == 1
    assert searched == []
    assert _Handler.seen[0]["body"].get("tools") is None


def test_search_failure_degrades_to_a_status_line(server, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise websearch.SearchError("未配置 API Key", reason=errors.AUTH)

    monkeypatch.setattr(websearch, "search", boom)
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="bocha"),
        search_key="",
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert any("失败" in line for line in statuses)


def test_tool_failure_falls_back_to_a_plain_answer_with_a_notice(server, monkeypatch) -> None:
    """工具调用失败：正式回答照发，并让模型在回答里说明「未联网核实」。"""

    def boom(*_args, **_kwargs):
        raise websearch.SearchError("连接被重置", reason=errors.NETWORK)

    monkeypatch.setattr(websearch, "search", boom)
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="bocha", fetch_pages=False),
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert result.incomplete is False
    assert any("联网检索暂时不可用" in line and "纯 API 回答" in line for line in statuses)
    answer = _Handler.seen[-1]["body"]
    assert answer.get("tools") is None
    system_text = answer["messages"][0]["content"]
    assert "联网检索暂时不可用" in system_text
    assert "未联网核实" in system_text
    # 一条资料都没拿到，就不能假装注入了「联网搜索结果」。
    assert "联网搜索结果" not in system_text


def test_research_budget_timeout_falls_back_with_a_notice(server, monkeypatch) -> None:
    """整轮预算耗尽也算「这次没联网」：回答必须带未核实说明，而不是静默照答。"""
    import petcore.api.chat as chat_mod

    monkeypatch.setattr(chat_mod, "_RESEARCH_BUDGET_SECONDS", -1.0)
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert any("联网检索超时" in line for line in statuses)
    assert any("联网检索暂时不可用" in line for line in statuses)
    # 超时不是「无需联网」：不许再冒一句相反的结论把用户绕晕。
    assert not any("无需联网" in line for line in statuses)
    assert "未联网核实" in _Handler.seen[-1]["body"]["messages"][0]["content"]


def test_web_unavailable_notice_creates_a_system_message_when_needed() -> None:
    out = inject_web_unavailable_notice([{"role": "user", "content": "hi"}])
    assert out[0]["role"] == "system"
    assert "未联网核实" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "hi"}


def test_planner_http_failure_still_answers_from_the_plain_api(server) -> None:
    """检索轮 500：整轮不能失败，要退回纯 API 回答（主分支同口径）。"""
    _Handler.planner_mode = "500"
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert result.incomplete is False
    assert any("联网检索不可用" in line and "直接回答" in line for line in statuses)
    assert any("联网检索暂时不可用" in line and "纯 API 回答" in line for line in statuses)
    # 正式回答照发，且**没有**注入任何检索资料；但要告诉模型这次没联网，
    # 让它在回答里说清「未联网核实」——否则用户会把模型记忆当成刚查到的实时信息。
    assert len(_Handler.seen) == 2
    answer = _Handler.seen[-1]["body"]
    assert answer.get("tools") is None
    assert answer["messages"][1:] == _dialogue()[1:]
    system_text = answer["messages"][0]["content"]
    assert answer["messages"][0]["role"] == "system"
    assert "你是一只桌宠" in system_text
    assert "联网检索暂时不可用" in system_text
    assert "未联网核实" in system_text
    assert "联网搜索结果" not in system_text


def test_planner_garbage_response_still_answers(server) -> None:
    _Handler.planner_mode = "badjson"
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        timeout=5,
    )
    assert result.text == "你好！"


def test_unexpected_research_error_degrades_to_a_plain_answer(server, monkeypatch) -> None:
    """非预期的检索异常（回调/解析/未来新增分支）也不许把整轮打成错误卡。"""

    def explode(*_args, **_kwargs):
        raise RuntimeError("unexpected failure with https://secret.example/?q=1")

    monkeypatch.setattr("petcore.api.chat.research", explode)
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert any("直接回答" in line for line in statuses)
    # 异常文本不进状态行（可能带 URL/查询串）。
    assert not any("secret.example" in line for line in statuses)


def test_context_injection_failure_degrades_to_a_plain_answer(server, monkeypatch) -> None:
    monkeypatch.setattr(
        websearch,
        "search",
        lambda *_a, **_k: [websearch.SearchResult("官方定价", "https://e.test/p", "摘要")],
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("inject failed")

    monkeypatch.setattr("petcore.api.chat.inject_search_context", boom)
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="bocha"),
        on_status=statuses.append,
        timeout=5,
    )

    assert result.text == "你好！"
    assert any("无法并入提问" in line for line in statuses)
    assert _Handler.seen[-1]["body"]["messages"] == _dialogue()


def test_cancel_during_research_skips_the_answer(server, monkeypatch) -> None:
    cancel = threading.Event()

    def stop(*_args, **_kwargs):
        cancel.set()
        return []

    monkeypatch.setattr(websearch, "search", stop)
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        cancel=cancel,
        timeout=5,
    )

    assert result.cancelled is True
    assert result.text == ""
    assert all(item["body"].get("tools") for item in _Handler.seen)


def _direct_open(request, *, timeout, context=None, opener=None, routes=None, **kwargs):
    """本机服务端直连：绕过系统代理，让用例只观察它要断言的东西。"""
    return net_http._opener(False, context).open(request, timeout=timeout, **kwargs)  # noqa: SLF001


def test_planner_transport_failure_still_answers(server, monkeypatch) -> None:
    """检索轮传输失败只降级成状态行：正式回答照常发出并返回正文。"""
    import petcore.api.chat as chat_mod

    def flaky(request, *, timeout, context=None, opener=None, **kwargs):
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        if body.get("tools"):
            raise urllib.error.URLError("proxy down")
        return _direct_open(request, timeout=timeout, context=context, **kwargs)

    monkeypatch.setattr(chat_mod, "urlopen", flaky)
    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        on_status=statuses.append,
        timeout=20,
    )

    assert result.text == "你好！"
    assert any("联网检索不可用" in line for line in statuses)
    assert len(_Handler.seen) == 1
    assert _Handler.seen[0]["body"].get("tools") is None


def test_planner_uses_the_short_budget_and_stops_without_tools(server, monkeypatch) -> None:
    """planner 判定轮只吃 ≤20s 预算；不需要检索时整轮只有 planner + 回答两个请求。"""
    import petcore.api.chat as chat_mod

    _Handler.tool_calls = 99  # planner 直接回「无需搜索」，不给 tool_calls
    planned: list[float] = []
    real_post = chat_mod._post_json

    def spy(request, payload, *, timeout, opener=None):
        planned.append(float(timeout))
        return real_post(request, payload, timeout=timeout, opener=opener)

    monkeypatch.setattr(chat_mod, "_post_json", spy)
    monkeypatch.setattr(chat_mod, "urlopen", _direct_open)

    statuses: list[str] = []
    result = stream_chat(
        _request(server),
        _dialogue(),
        web_search=WebSearchOptions(engine="baidu"),
        on_status=statuses.append,
        timeout=60,
    )

    assert result.text == "你好！"
    assert len(_Handler.seen) == 2
    assert planned == [20.0]
    assert any("无需联网" in line for line in statuses)


def test_api_port_uses_the_configured_timeout(monkeypatch) -> None:
    """``api.json`` 里的超时对对话必须生效：以前无论写多少都恒 60 秒。"""
    from petcore.ui import ports as ui_ports

    captured: dict = {}

    def fake_stream(request, messages, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("petcore.api.chat.stream_chat", fake_stream)
    port = ui_ports.CoreApiPort(object(), object(), object())
    monkeypatch.setattr(
        port,
        "resolve",
        lambda service_id: ApiRequest(
            ApiMetadata(
                service_id=service_id,
                name="n",
                base_url="https://api.example",
                timeout=59,
            ),
            "secret",
        ),
    )

    assert port.stream_chat("api1", []) == "ok"
    assert captured["timeout"] == 59.0

    assert port.stream_chat("api1", [], timeout=12.0) == "ok"
    assert captured["timeout"] == 12.0
