"""流式对话传输：core 唯一的 chat HTTP/SSE 出口。

mod 只看到 :class:`petcore.contracts.api.ApiPort`；代理兜底、取消、超时和错误分类
都在这里，``petcore/chat/`` 与会话存储保持零网络依赖（架构守卫）。
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..contracts.api import ApiRequest, WebSearchOptions
from ..net import errors
from ..net import websearch
from ..net.http import urlopen
from .headers import extra_headers

logger = logging.getLogger("petcore.api.chat")

STREAM_DONE = "[DONE]"
DEFAULT_MODEL = "deepseek-chat"

#: 传输层抖动（断连/TLS 重置/DNS 抖动）最多再试几次、每次之间歇多久。
#: 只重试「一个字都还没吐」的失败：已经显示给用户的正文不能重来（会重复）。
STREAM_RETRY_ATTEMPTS = 3
OPEN_RETRY_ATTEMPTS = 2
RETRY_DELAY_S = 0.6
#: 同一条路由上重开一次连接的条件：只有这两类是「链路本身没通」；超时是慢、
#: 认证是配置问题，重试只会翻倍等待。
RETRYABLE_REASONS = (errors.NETWORK, errors.TLS)
#: 换**路由**再发一次的条件。响应不完整（``protocol``：IncompleteRead/
#: BadStatusLine/半截 SSE）与超时也算：代理缓冲/空闲掐断正是「小请求能通、
#: 长连接必死」的典型形状，换条路往往就好了。
STREAM_RETRY_REASONS = (
    errors.NETWORK,
    errors.TLS,
    errors.PROTOCOL,
    errors.TIMEOUT,
)
#: 正式回答的路由计划：默认（代理优先）→ 只直连 → 只走代理。
#: 代理把 SSE 卡死或掐断时，重发同一条路只会再失败一次。
STREAM_ROUTE_PLANS: tuple[tuple[bool, ...] | None, ...] = (None, (False,), (True,))


class _Progress:
    """已向 UI 吐出的内容计数：> 0 就绝不重试（重试会把同一段再打一遍）。"""

    __slots__ = ("content", "reasoning")

    def __init__(self) -> None:
        self.content = 0
        self.reasoning = 0

    @property
    def emitted(self) -> bool:
        return bool(self.content or self.reasoning)

#: 联网检索阶段最多几轮 planner / 每轮最多几次工具调用（同主分支）。
_MAX_RESEARCH_ROUNDS = 3
_MAX_CALLS_PER_ROUND = 4
_MAX_NOTES_CHARS = 24_000
#: 整次联网检索的墙钟预算：最坏 3 轮 × 60s + 12 次工具 × 20s 会把对话卡到 7 分钟。
_RESEARCH_BUDGET_SECONDS = 45.0

_PLANNER_PROMPT = (
    "你是联网检索助手，当前时间：{today}。请判断：回答用户最后一条消息，是否需要联网"
    "获取最新、实时或你不确定的事实信息（新闻、天气、价格、版本、赛事、政策、具体数据、"
    "某个人/产品/事件的现状等）。\n"
    "- 需要：调用 web_search 搜索，关键词要精炼，必要时换角度多搜几次；摘要信息不足时，"
    "可调用 fetch_url 阅读最相关的网页。\n"
    "- 不需要（闲聊、常识、写作、翻译、代码、情感陪伴等）：不要调用任何工具，直接回复"
    "「无需搜索」。\n"
    "你只负责收集资料，不要直接回答用户的问题。"
)
_CONTEXT_PROMPT = (
    "【联网搜索结果】（当前时间：{today}；以下资料是刚刚联网获取的）\n\n{notes}\n\n"
    "请结合以上资料回答用户最新的问题：优先采用资料中的最新信息；资料与你已有知识冲突时"
    "以资料为准；资料不足以回答时如实说明。如果引用了资料，在回答末尾用「参考来源」"
    "简短列出对应链接。"
)
_WEB_UNAVAILABLE_PROMPT = (
    "本次联网检索暂时不可用，请直接根据已有知识回答，并在回答开头简短说明"
    "『联网检索暂时不可用，以下内容未联网核实』。"
)
_WEB_UNAVAILABLE_STATUS = "联网检索暂时不可用，已改用纯 API 回答"
_WEB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "用搜索引擎联网搜索，获取最新/实时信息。返回若干条标题、链接和摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "精炼的搜索关键词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "读取指定网页的正文内容，用于搜索摘要不够详细时深入阅读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要读取的网页地址（http/https）"}
                },
                "required": ["url"],
            },
        },
    },
]


class ChatCancelled(RuntimeError):
    """调用方主动取消；不算错误，界面显示「已停止」。"""


class ChatTruncated(RuntimeError):
    """只输出思维链就撞上 max_tokens，没来得及给出回答（同主分支提示）。"""

    reason = errors.PROTOCOL


@dataclass(frozen=True, slots=True)
class ChatResult:
    """一次流式回答的结果。

    ``incomplete``/``end_reason`` 是**显式**的完整性信号：没有收到 ``[DONE]``、
    没有 ``finish_reason``、或响应体为空时，``end_reason`` 不是 ``done``，
    调用方必须提示用户，而不是把半条回答当成完整答复落盘（审计 AUD-15）。

    ``end_reason`` 取值：``done``（收到 ``[DONE]``/``finish_reason``）、``eof``
    （流提前结束）、``empty``（没有正文也没有思维链）、``cancelled``（用户停止）、
    ``interrupted``（已有正文后传输中断；``text`` 是保留的半条回答）。
    """

    text: str
    chunks: int = 0
    finish_reason: str = ""
    cancelled: bool = False
    reasoning: str = ""
    incomplete: bool = False
    end_reason: str = ""

    @property
    def usable(self) -> bool:
        """是否有可展示的内容（空回答不算）。"""
        return bool(self.text or self.reasoning)


def _sends_thinking_param(request: ApiRequest, mode: str) -> bool:
    """auto 模式只对 DeepSeek 官方地址发送 thinking，避免严格校验的服务报错。"""
    return mode != "auto" or "deepseek.com" in str(request.base_url or "").lower()


def thinking_payload(
    request: ApiRequest,
    mode: str = "auto",
    effort: str = "",
    *,
    force_disable: bool = False,
) -> dict[str, Any]:
    """DeepSeek V4 格式：``thinking={'type': enabled}`` + ``reasoning_effort``。"""
    mode = str(mode or "auto")
    if force_disable:
        return {"thinking": {"type": "disabled"}} if _sends_thinking_param(request, mode) else {}
    if mode == "enabled":
        level = str(effort or "high")
        if level not in {"low", "high", "max"}:
            level = "high"
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}
    if mode == "disabled":
        return {"thinking": {"type": "disabled"}}
    return {}


def _extract_delta(data: Mapping[str, Any]) -> tuple[str, str, str]:
    """(增量文本, 思维链增量, finish_reason)；兼容流式与一次性返回两种形状。"""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", "", ""
    first = choices[0] if isinstance(choices[0], Mapping) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), Mapping) else {}
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    text = delta.get("content") or message.get("content") or ""
    if not isinstance(text, str):
        text = ""
    # reasoning_content：DeepSeek/智谱等思考模型；reasoning：OpenRouter 等网关的同义字段。
    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = ""
    finish = first.get("finish_reason") or ""
    return text, reasoning, str(finish or "")


def build_payload(
    request: ApiRequest,
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str = "",
    temperature: float | None = None,
    stream: bool = True,
    thinking: str = "auto",
    reasoning_effort: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": str(model or request.metadata.model or DEFAULT_MODEL),
        "messages": [dict(item) for item in messages],
        "stream": bool(stream),
    }
    if temperature is not None:
        payload["temperature"] = max(0.0, min(2.0, float(temperature)))
    payload.update(thinking_payload(request, thinking, reasoning_effort))
    return payload


def _build_request(
    request: ApiRequest, payload: dict[str, Any], *, accept: str = "text/event-stream"
) -> urllib.request.Request:
    url = request.base_url.rstrip("/") + request.metadata.chat_path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = extra_headers(
        request.base_url,
        version=str(getattr(request.metadata, "version", "") or ""),
        headers=dict(getattr(request.metadata, "extra_headers", {}) or {}),
    )
    headers.update(
        {
            "Content-Type": "application/json",
            "Accept": accept,
        }
    )
    if request.api_key:
        headers["Authorization"] = f"Bearer {request.api_key}"
    return urllib.request.Request(url, data=body, headers=headers, method="POST")


def _context(request: ApiRequest):
    if request.metadata.verify_ssl:
        return ssl.create_default_context()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _open_response(
    http_request: urllib.request.Request,
    request: ApiRequest,
    *,
    timeout: float,
    opener=None,
    attempts: int = OPEN_RETRY_ATTEMPTS,
    routes: tuple[bool, ...] | None = None,
):
    """打开连接；传输层抖动时再试一次（``HTTPError`` 是服务器答复，永不重试）。

    秒级抖动（TLS 握手被掐、连接重置、DNS 抖动）在真机上是常态：以前一次失败
    就直接报「网络不可达」，用户只能再发一遍。这里替用户重发一次，仍然失败才抛。

    ``routes`` 原样透传给 :func:`petcore.net.http.urlopen`，是**本次调用**的路由
    计划（``(False,)`` = 只直连、``(True,)`` = 只走代理）；它不改写进程级
    「固定直连」结论——正式回答的换路重试不能替整个进程下结论。
    """
    last_exc: Exception | None = None
    tries = max(1, int(attempts))
    for index in range(tries):
        if index:
            time.sleep(RETRY_DELAY_S)
        try:
            return urlopen(
                http_request,
                timeout=max(1.0, float(timeout)),
                context=_context(request),
                opener=opener,
                routes=routes,
            )
        except errors.HttpStatusError:
            # 服务器有答复（4xx/5xx）：链路是通的，不重试、不换路，按状态码分类。
            raise
        except errors.NetworkError as exc:
            if exc.reason not in RETRYABLE_REASONS or index + 1 >= tries:
                raise
            last_exc = exc
        except urllib.error.HTTPError as exc:
            # 服务器有答复（4xx/5xx）：链路是通的，不重试、不换路，按状态码分类。
            raise errors.http_error(exc) from None
        except Exception as exc:  # noqa: BLE001 - 传输失败交给统一分类
            reason = errors.classify_exception(exc)
            if reason not in RETRYABLE_REASONS or index + 1 >= tries:
                if reason is None:
                    raise
                raise errors.NetworkError(reason=reason) from exc
            last_exc = exc
    reason = errors.classify_exception(last_exc) if last_exc is not None else None
    if reason is None:
        raise errors.NetworkError(reason=errors.NETWORK)
    raise errors.NetworkError(reason=reason) from last_exc


def _status(on_status, text: str) -> None:
    if on_status is not None:
        on_status(str(text))


def _cancelled(cancel) -> bool:
    return cancel is not None and bool(getattr(cancel, "is_set", lambda: False)())


def _today_text() -> str:
    return time.strftime("%Y-%m-%d")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, Mapping) and item.get("type") == "image_url":
                parts.append("[图片]")
        return "\n".join(parts)
    return str(content or "")


def _recent_dialogue(
    messages: Sequence[Mapping[str, Any]], limit: int = 6, max_chars: int = 2000
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") not in ("user", "assistant"):
            continue
        text = _text_of(message.get("content")).strip()
        if text:
            out.append({"role": message["role"], "content": text[-max_chars:]})
    out = out[-limit:]
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def inject_search_context(
    messages: Sequence[Mapping[str, Any]], notes: str
) -> list[dict[str, Any]]:
    """把检索资料并入首条 system 消息（无 system 时插到最前），不改动原列表。"""
    return _merge_system(messages, _CONTEXT_PROMPT.format(today=_today_text(), notes=notes))


def inject_web_unavailable_notice(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """告诉模型「这次没联网」，让它在**回答里**说清未核实（不改动原列表）。

    只靠状态行不够：状态行一闪而过，用户看到的仍是一段「像是刚查过」的答案。
    拼接口径与 :func:`inject_search_context` 一致。
    """
    return _merge_system(messages, _WEB_UNAVAILABLE_PROMPT)


def _merge_system(
    messages: Sequence[Mapping[str, Any]], block: str
) -> list[dict[str, Any]]:
    prepared = [dict(item) if isinstance(item, Mapping) else item for item in messages]
    if (
        prepared
        and isinstance(prepared[0], Mapping)
        and prepared[0].get("role") == "system"
        and isinstance(prepared[0].get("content"), str)
    ):
        prepared[0]["content"] = (
            str(prepared[0]["content"]).rstrip() + "\n\n" + block
        ).strip()
    else:
        prepared.insert(0, {"role": "system", "content": block})
    return prepared


def _post_json(
    request: ApiRequest,
    payload: dict[str, Any],
    *,
    timeout: float,
    opener=None,
) -> Mapping[str, Any]:
    """非流式 JSON 请求（联网检索的 planner 用）。"""
    http_request = _build_request(request, payload, accept="application/json")
    response = _open_response(http_request, request, timeout=timeout, opener=opener)
    try:
        raw = response.read(4 * 1024 * 1024)
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise errors.NetworkError(reason=errors.PROTOCOL) from None
    if not isinstance(data, Mapping):
        raise errors.NetworkError(reason=errors.PROTOCOL)
    return data


def _run_tool(
    name: str,
    args: Mapping[str, Any],
    *,
    options: WebSearchOptions,
    search_key: str,
    timeout: float,
    verify_ssl: bool,
    opener=None,
) -> tuple[str, bool, list[str]]:
    """执行一次工具调用 → (给模型的结果文本, 是否成功, 状态提示列表)。"""
    if name == "web_search":
        query = str(args.get("query", "")).strip()
        lines = [f"🔍 正在搜索：{query}"]
        try:
            results = websearch.search(
                query,
                engine=options.engine,
                api_key=search_key,
                limit=options.max_results,
                timeout=timeout,
                verify_ssl=verify_ssl,
                opener=opener,
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败不打断回答
            text = f"搜索「{query}」失败：{exc}"
            return text, False, lines + ["　" + text]
        return (
            websearch.format_results(query, results),
            bool(results),
            lines + [f"　找到 {len(results)} 条结果"],
        )
    if name == "fetch_url":
        url = str(args.get("url", "")).strip()
        lines = [f"📄 正在阅读：{url}"]
        try:
            text = websearch.fetch_page_text(
                url, timeout=timeout, verify_ssl=verify_ssl, opener=opener
            )
        except Exception as exc:  # noqa: BLE001
            text = f"读取网页 {url} 失败：{exc}"
            return text, False, lines + ["　" + text]
        return f"网页 {url} 的内容：\n{text}", True, lines
    return f"未知工具：{name}", False, []


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    """检索阶段的结果：资料、是否真的搜过、是否**失败**。

    ``failed`` 的口径是「这次没能拿到可信的联网资料」：planner 请求失败、
    任何一次工具调用失败、或整轮预算耗尽。它只决定要不要告诉模型「没联网」，
    **不是**错误——正式回答照发（``petcore/AGENTS.md``：检索是增强项）。
    """

    notes: str = ""
    searched: bool = False
    failed: bool = False


def research(
    request: ApiRequest,
    messages: Sequence[Mapping[str, Any]],
    *,
    options: WebSearchOptions,
    search_key: str = "",
    model: str = "",
    timeout: float = 60.0,
    on_status: Callable[[str], None] | None = None,
    cancel: Any | None = None,
    opener=None,
) -> ResearchOutcome:
    """检索阶段：非思考模式 + 工具调用，让模型决定搜什么；返回 :class:`ResearchOutcome`。

    单独走一轮非思考请求，而不是在正式回答里挂 tools：DeepSeek 思考模式带 tools 时
    要求回传历史所有轮次的 ``reasoning_content``（缺失即 400），而会话历史不保存
    思维链。任何检索失败都降级成状态行 + ``failed=True``，绝不阻断正式回答。
    """
    dialogue = _recent_dialogue(messages)
    if not dialogue:
        return ResearchOutcome()
    tools = list(_WEB_TOOLS) if options.fetch_pages else _WEB_TOOLS[:1]
    planner: list[dict[str, Any]] = [
        {"role": "system", "content": _PLANNER_PROMPT.format(today=_today_text())},
        *dialogue,
    ]
    notes: list[str] = []
    used = 0
    searched = False
    failed = False
    net_timeout = max(5.0, min(float(timeout), 20.0))
    deadline = time.monotonic() + _RESEARCH_BUDGET_SECONDS
    _status(on_status, "正在判断是否需要联网…")
    for round_no in range(_MAX_RESEARCH_ROUNDS):
        if _cancelled(cancel):
            return ResearchOutcome(_join_notes(notes), searched, failed)
        if time.monotonic() > deadline:
            _status(on_status, "联网检索超时，改用已有资料继续回答")
            failed = True
            break
        payload: dict[str, Any] = {
            "model": str(model or request.metadata.model or DEFAULT_MODEL),
            "messages": planner,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
            "max_tokens": 1024,
            "temperature": 0.3,
        }
        payload.update(thinking_payload(request, "auto", "", force_disable=True))
        try:
            data = _post_json(request, payload, timeout=net_timeout, opener=opener)
        except Exception as exc:  # noqa: BLE001 - 检索不可用不影响回答
            failed = True
            if _cancelled(cancel):
                return ResearchOutcome(_join_notes(notes), searched, failed)
            if round_no == 0:
                _status(on_status, f"联网检索不可用（{exc}），将直接回答")
                return ResearchOutcome(_join_notes(notes), searched, True)
            break
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, Mapping) else None
        if not isinstance(message, Mapping):
            failed = True
            if round_no == 0:
                _status(on_status, "联网检索返回了无法识别的响应，将直接回答")
                return ResearchOutcome(_join_notes(notes), searched, True)
            break
        calls = [
            call
            for call in (message.get("tool_calls") or [])
            if isinstance(call, Mapping) and isinstance(call.get("function"), Mapping)
        ][:_MAX_CALLS_PER_ROUND]
        if not calls:
            break
        searched = True
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": calls,
        }
        if message.get("reasoning_content"):
            assistant["reasoning_content"] = message["reasoning_content"]
        planner.append(assistant)
        for call in calls:
            if _cancelled(cancel):
                return ResearchOutcome(_join_notes(notes), searched, failed)
            if time.monotonic() > deadline:
                _status(on_status, "联网检索超时，改用已有资料继续回答")
                failed = True
                break
            function = call.get("function") or {}
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            text, ok, lines = _run_tool(
                str(function.get("name", "")),
                args if isinstance(args, Mapping) else {},
                options=options,
                search_key=search_key,
                timeout=net_timeout,
                verify_ssl=request.metadata.verify_ssl,
                opener=opener,
            )
            for line in lines:
                _status(on_status, line)
            if not ok:
                failed = True
            if ok and used < _MAX_NOTES_CHARS:
                note = text[: _MAX_NOTES_CHARS - used]
                notes.append(note)
                used += len(note)
            planner.append(
                {"role": "tool", "tool_call_id": str(call.get("id", "")), "content": text}
            )
    if not searched and not failed:
        _status(on_status, "无需联网，直接回答")
    return ResearchOutcome(_join_notes(notes), searched, failed)


def _join_notes(notes: Sequence[str]) -> str:
    return "\n\n---\n\n".join(notes)


def stream_chat(
    request: ApiRequest,
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str = "",
    temperature: float | None = None,
    timeout: float = 60.0,
    on_delta: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    thinking: str = "auto",
    reasoning_effort: str = "",
    web_search: WebSearchOptions | None = None,
    search_key: str = "",
    cancel: Any | None = None,
    opener=None,
) -> ChatResult:
    """POST ``chat_path`` 并把 SSE 增量逐段回调；返回完整文本。

    ``cancel`` 只要有 ``is_set()`` 即可（``threading.Event``）；每收到一行检查一次。
    失败统一抛 :class:`petcore.net.errors.NetworkError` 的子类，界面只认 ``reason``；
    例外是**已经收到正文之后**的传输中断：那时不抛错，而是返回
    ``end_reason="interrupted"`` 的半条结果（``text`` 是已收到的内容），
    一个字都没有才抛错。
    联网开启时先跑 :func:`research`（资料并入 system），再走正式回答。
    """
    conversation = [dict(item) if isinstance(item, Mapping) else item for item in messages]
    if web_search is not None and not _cancelled(cancel):
        # 联网检索是**增强项**，不是回答的前提：检索阶段的任何失败都必须退回
        # 纯 API 回答（主分支 `pet/chat/providers.py` 的 `_research` 同口径）。
        # `research()` 自己已经吞掉传输/HTTP/工具错误并标 ``failed``，这里再兜一层
        # 是给「非预期异常」（回调、解析、未来新增分支）用的——一次检索失败绝不能
        # 让用户拿到「本次回复未保存」。
        try:
            outcome = research(
                request,
                conversation,
                options=web_search,
                search_key=search_key,
                model=model,
                timeout=timeout,
                on_status=on_status,
                cancel=cancel,
                opener=opener,
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败降级，不冒泡给界面
            if _cancelled(cancel):
                return ChatResult(text="", cancelled=True)
            # 只记类型：异常文本可能带 URL/查询串。
            logger.warning("web search degraded to a plain answer: %s", type(exc).__name__)
            _status(on_status, "联网检索不可用，将直接回答")
            outcome = ResearchOutcome(failed=True)
        if _cancelled(cancel):
            return ChatResult(text="", cancelled=True)
        if outcome.notes:
            try:
                conversation = inject_search_context(conversation, outcome.notes)
            except Exception as exc:  # noqa: BLE001 - 资料拼接失败同样降级
                logger.warning(
                    "web search context injection failed: %s", type(exc).__name__
                )
                _status(on_status, "联网资料无法并入提问，将直接回答")
            else:
                _status(on_status, "已获取联网资料，正在组织回答…")
        elif outcome.failed:
            # 没资料但确实试过：让模型在回答里自己说清「未联网核实」。只在
            # 「一条资料都没拿到」时这么说；拿到了就不用给用户泼冷水。
            try:
                conversation = inject_web_unavailable_notice(conversation)
            except Exception as exc:  # noqa: BLE001 - 拼接失败也要照常回答
                logger.warning(
                    "web search notice injection failed: %s", type(exc).__name__
                )
            _status(on_status, _WEB_UNAVAILABLE_STATUS)

    payload = build_payload(
        request,
        conversation,
        model=model,
        temperature=temperature,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
    )
    progress = _Progress()
    attempt = 0
    while True:
        attempt += 1
        routes = STREAM_ROUTE_PLANS[min(attempt - 1, len(STREAM_ROUTE_PLANS) - 1)]
        try:
            return _stream_once(
                request,
                payload,
                timeout=timeout,
                on_delta=on_delta,
                on_reasoning=on_reasoning,
                on_status=on_status,
                cancel=cancel,
                opener=opener,
                progress=progress,
                routes=routes,
            )
        except errors.NetworkError as exc:
            if (
                attempt >= STREAM_RETRY_ATTEMPTS
                or _cancelled(cancel)
                or progress.emitted
                or not _stream_retryable(exc)
            ):
                raise
            # 一个字都没到用户眼前：这一次失败对用户等于「没发生」，换条路由重发。
            _status(on_status, "网络抖动，正在重试…")
            time.sleep(RETRY_DELAY_S)


def _stream_retryable(exc: errors.NetworkError) -> bool:
    """这次失败值不值得换一条路由再发一次。

    ``HttpStatusError`` 是服务器对这次请求的明确答复（401/429/HTTP 错误帧，
    含 500/502/504——:func:`petcore.net.http.urlopen` 已经在两条路由上问过了），
    换条路只会把同一句话再问一遍。
    """
    if isinstance(exc, errors.HttpStatusError):
        return False
    return exc.reason in STREAM_RETRY_REASONS


def _stream_once(
    request: ApiRequest,
    payload: dict[str, Any],
    *,
    timeout: float,
    on_delta: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None,
    on_status: Callable[[str], None] | None,
    cancel: Any | None,
    opener,
    progress: _Progress,
    routes: tuple[bool, ...] | None = None,
):
    """一次正式流式请求（打开 + 读流）；失败分类与半条回答的语义同 ``stream_chat``。"""
    http_request = _build_request(request, payload)
    response = _open_response(
        http_request,
        request,
        timeout=timeout,
        opener=opener,
        attempts=1,
        routes=routes,
    )

    parts: list[str] = []
    reasoning_parts: list[str] = []
    chunks = 0
    finish_reason = ""
    plain_lines: list[str] = []
    saw_sse = False
    saw_done = False
    cancelled = False

    def _apply(data_text: str) -> None:
        """处理一个完整 SSE 事件（多行 `data:` 已经按换行拼好）。"""
        nonlocal finish_reason, saw_done, chunks
        if data_text == STREAM_DONE:
            saw_done = True
            return
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            raise errors.NetworkError(reason=errors.PROTOCOL) from None
        if not isinstance(payload, Mapping):
            raise errors.NetworkError(reason=errors.PROTOCOL)
        _raise_for_error_frame(payload)
        delta, reasoning, finish = _extract_delta(payload)
        if finish:
            finish_reason = finish
        if reasoning:
            reasoning_parts.append(reasoning)
            progress.reasoning += 1
            if on_reasoning is not None:
                on_reasoning(reasoning)
        if delta:
            parts.append(delta)
            chunks += 1
            progress.content += 1
            if on_delta is not None:
                on_delta(delta)

    stop_watch = threading.Event()
    watcher = _start_cancel_watcher(response, cancel, stop_watch)
    stream_deadline = time.monotonic() + max(1.0, float(timeout))
    _set_read_timeout(response, SOCKET_POLL_SECONDS)
    interrupted: Exception | None = None
    try:
        event_lines: list[str] = []
        try:
            while True:
                if _cancelled(cancel):
                    cancelled = True
                    break
                try:
                    raw = _readline(response, cancel, stream_deadline)
                except ChatCancelled:
                    cancelled = True
                    break
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip()
                if not line.strip():
                    # SSE 事件以空行分隔：一个事件可以拆成多行 `data:`。
                    if event_lines:
                        saw_sse = True
                        _apply("\n".join(event_lines))
                        event_lines.clear()
                        if saw_done:
                            break
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    saw_sse = True
                    event_lines.append(line[5:].lstrip())
                    continue
                plain_lines.append(line.strip())
            if event_lines and not saw_done and not cancelled:
                _apply("\n".join(event_lines))
        except errors.NetworkError:
            raise
        except Exception as exc:  # noqa: BLE001 - 读流中断也归入稳定分类
            if _cancelled(cancel):
                cancelled = True
            elif parts:
                # 已经吐出的正文不能因为断流被抹掉：先记下，关掉响应后返回半条。
                interrupted = exc
            else:
                reason = errors.classify_exception(exc)
                if reason is None:
                    raise
                raise errors.NetworkError(reason=reason) from exc
    finally:
        stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=0.2)
        try:
            response.close()
        except Exception:
            pass

    if interrupted is not None:
        _status(on_status, f"回答被中断（{interrupted}），已保留前面的内容")
        return ChatResult(
            text="".join(parts),
            chunks=chunks,
            finish_reason=finish_reason,
            reasoning="".join(reasoning_parts),
            incomplete=True,
            end_reason="interrupted",
        )

    if not saw_sse and plain_lines:
        try:
            body = json.loads("\n".join(plain_lines))
        except json.JSONDecodeError:
            raise errors.NetworkError(reason=errors.PROTOCOL) from None
        if not isinstance(body, Mapping):
            raise errors.NetworkError(reason=errors.PROTOCOL)
        _raise_for_error_frame(body)
        text, reasoning, finish = _extract_delta(body)
        if reasoning:
            reasoning_parts.append(reasoning)
            progress.reasoning += 1
            if on_reasoning is not None:
                on_reasoning(reasoning)
        if text:
            parts.append(text)
            chunks += 1
            progress.content += 1
            if on_delta is not None:
                on_delta(text)
        if finish:
            finish_reason = finish
        # 一次性 JSON 响应没有 `[DONE]`：它本身就是完整答复。
        saw_done = True

    if (
        reasoning_parts
        and not parts
        and finish_reason == "length"
        and not cancelled
        and not _cancelled(cancel)
    ):
        raise ChatTruncated(
            "深度思考用完了「最大输出 Token」，还没来得及给出回答。"
            "请换一个更小的思考强度或在服务端调大输出上限。"
        )

    if cancelled or _cancelled(cancel):
        end_reason = "cancelled"
    elif saw_done or finish_reason:
        end_reason = "done"
    elif not parts and not reasoning_parts:
        end_reason = "empty"
    else:
        end_reason = "eof"

    return ChatResult(
        text="".join(parts),
        chunks=chunks,
        finish_reason=finish_reason,
        cancelled=cancelled,
        reasoning="".join(reasoning_parts),
        incomplete=end_reason in {"eof", "empty"},
        end_reason=end_reason,
    )


SOCKET_POLL_SECONDS = 0.5


def _set_read_timeout(response, seconds: float) -> bool:
    """把底层 socket 的读超时压短：否则一次阻塞读会一直挂到全局 timeout。"""
    try:
        sock = response.fp.raw._sock
    except Exception:  # noqa: BLE001 - 测试替身没有真实 socket
        return False
    try:
        sock.settimeout(float(seconds))
    except OSError:
        return False
    return True


def _readline(response, cancel, deadline: float) -> bytes:
    """读一行；反复超时只表示「暂时没数据」，取消与总预算在这里生效。"""
    while True:
        try:
            return response.readline()
        except errors.NetworkError:
            raise
        except TimeoutError:
            if _cancelled(cancel):
                raise ChatCancelled("已停止") from None
            if time.monotonic() > deadline:
                raise errors.NetworkTimeout("连接超时") from None
            continue


def _raise_for_error_frame(payload: Mapping[str, Any]) -> None:
    """HTTP 200 + SSE 错误帧：不能吞成「空回答」，要按状态码分类抛错。

    只取 ``code`` 做分类，**不回显** ``message``——响应体可能原样回显请求里的密钥。
    """
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return
    try:
        status = int(error.get("code"))
    except (TypeError, ValueError):
        status = 0
    if status:
        reason = errors.classify_http_status(status)
        if reason == errors.AUTH:
            raise errors.AuthError(status)
        if reason == errors.RATE_LIMIT:
            raise errors.RateLimitError(status)
        raise errors.HttpStatusError(status)
    raise errors.NetworkError(reason=errors.PROTOCOL)


def _start_cancel_watcher(response, cancel, stop: threading.Event):
    """取消要能打断阻塞读：只靠「两行之间检查」会让线程一直卡到 socket 超时。"""
    if cancel is None or not hasattr(cancel, "is_set"):
        return None

    def _watch() -> None:
        while not stop.wait(0.05):
            try:
                if not cancel.is_set():
                    continue
            except Exception:  # noqa: BLE001 - 取消对象坏了就退出监视
                return
            try:
                response.close()
            except Exception:
                pass
            return

    thread = threading.Thread(target=_watch, name="petcore-chat-cancel", daemon=True)
    thread.start()
    return thread
