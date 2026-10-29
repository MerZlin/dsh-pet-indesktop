"""带「系统代理不通就直连」兜底的 urlopen（移植自 `pet/http_util.py`）。

背景：Windows 的系统代理常被加速器/VPN 打开，进程退出后代理解析仍然生效，
`urllib.request.urlopen` 会照着死代理走 ⇒ 所有请求 `ConnectionRefused`。
这里的口径与主分支逐条一致：

- 先按系统代理试一次（尊重用户的加速器/VPN）；
- 传输失败时改直连重试，并把结论固化在进程内（有期限）；
- 4xx/429 与带 `Retry-After` 的答复不重试也不换路——那是服务端对这次请求的
  明确答复；
- **5xx 网关错误换一条路由再问一次**：代理/VPN 在链路正常时也会吐 502/503，
  只凭它判定「API 不可用」正是「网络不可达」误报的来源。
"""

from __future__ import annotations

import functools
import ipaddress
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit
from typing import Any, Callable

from .errors import (
    NETWORK,
    PROTOCOL,
    TIMEOUT,
    HttpStatusError,
    NetworkError,
    NetworkTimeout,
    classify_exception,
    http_error,
)

logger = logging.getLogger("petcore.net.http")

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 4_000_000
DEFAULT_FALLBACK_STATUSES = (500, 502, 504)
#: :func:`urlopen` 换路由重试的状态码。比 :func:`fetch_url` 多一个 503：代理/VPN
#: 网关在链路正常、API 健在时也会回 503，这一条答复不足以判定「这台 API 不可用」。
ROUTE_RETRY_STATUSES = (500, 502, 503, 504)
DEFAULT_ALLOWED_SCHEMES = ("http", "https")
DEFAULT_MAX_REDIRECTS = 3
_READ_CHUNK = 65_536
#: 跨源跳转时必须剥掉的敏感头（urllib 默认会把它们原样带过去）。
_SENSITIVE_HEADERS = ("authorization", "x-api-key", "api-key", "cookie", "proxy-authorization")
_LOCAL_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan", ".home.arpa")


class Body(bytes):
    """响应体，额外带「是否被截断」与声明的长度。

    仍是 ``bytes``（``json.loads``/切片/比较全部照旧），但调用方可以判定
    「读到的是不是完整响应」——否则半截 mp3 会被当作成功产物缓存起来。
    """

    truncated: bool
    declared_length: int | None

    def __new__(cls, data: bytes, truncated: bool = False, declared_length: int | None = None):
        instance = super().__new__(cls, data)
        instance.truncated = bool(truncated)
        instance.declared_length = declared_length
        return instance


def is_https_url(url: str) -> bool:
    parts = urlsplit(str(url))
    return parts.scheme.lower() == "https" and bool(parts.hostname)


def is_allowed_https_url(url: str, hosts) -> bool:
    parts = urlsplit(str(url))
    return parts.scheme.lower() == "https" and (parts.hostname or "").lower() in {
        str(host).lower() for host in hosts
    }


def _is_private_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address).strip().strip("[]").split("%")[0])
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


@functools.lru_cache(maxsize=256)
def is_private_host(host: str) -> bool:
    """目标主机是否落在本机/内网/链路本地：用于拦「模型被诱导读内网」。"""
    name = str(host or "").strip().strip("[]").lower()
    if not name:
        return True
    if name in _LOCAL_HOSTNAMES or name.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        return _is_private_address(name)
    try:
        infos = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except OSError:
        # 解析不了不是「本地地址」，交给真正的请求去失败并分类。
        return False
    return any(_is_private_address(str(info[4][0])) for info in infos if info[4])


def validate_target(
    url: str,
    *,
    allowed_schemes: tuple[str, ...] = DEFAULT_ALLOWED_SCHEMES,
    allow_private: bool = True,
) -> None:
    """请求前的目标校验：协议白名单 + （可选）内网地址拦截。

    协议白名单是硬性的：``file://`` 走 urllib 的 FileHandler 能直接读本机文件，
    那会绕过响应体的所有大小/协议校验。
    """
    parts = urlsplit(str(url))
    scheme = (parts.scheme or "").lower()
    allowed = tuple(str(item).lower() for item in allowed_schemes)
    if scheme not in allowed:
        raise NetworkError(f"不支持的协议：{scheme or '（空）'}", reason=PROTOCOL)
    if allow_private:
        return
    if is_private_host(parts.hostname or ""):
        raise NetworkError("不允许访问本机或内网地址", reason=PROTOCOL)


def clone_request(request) -> urllib.request.Request:
    """按原请求重建一个 ``Request``。

    ``ProxyHandler``/``HTTPRedirectHandler`` 会**就地**改写 ``host``/``selector``，
    复用同一个对象重试等于再拨一次代理；每次换路由都必须重建。
    """
    if not isinstance(request, urllib.request.Request):
        return request
    headers = dict(request.header_items())
    return urllib.request.Request(
        request.full_url,
        data=request.data,
        headers=headers,
        method=request.get_method(),
        origin_req_host=getattr(request, "origin_req_host", None) or request.host,
        unverifiable=bool(getattr(request, "unverifiable", False)),
    )


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(str(url))
    return (parts.scheme or "").lower(), (parts.hostname or "").lower()


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向策略：限次、禁止 https→http 降级、跨源剥敏感头。

    默认的 ``HTTPRedirectHandler`` 会把 ``Authorization``/``X-Api-Key`` 原样带到
    新主机，还会跟最多 10 跳、允许跳到 ``ftp://``。
    """

    def __init__(self, max_redirects: int = DEFAULT_MAX_REDIRECTS) -> None:
        self.max_redirects = max(0, int(max_redirects))

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib hook
        if self.max_redirects <= 0:
            return None
        old_scheme, old_host = _origin(req.full_url)
        new_scheme, new_host = _origin(newurl)
        if new_scheme not in ("http", "https"):
            return None
        if old_scheme == "https" and new_scheme != "https":
            return None
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if (old_scheme, old_host) != (new_scheme, new_host):
            for key in list(new.headers):
                if str(key).lower() in _SENSITIVE_HEADERS:
                    del new.headers[key]
        return new

_force_direct = False
#: 「固定直连」结论的有效期终点（``time.monotonic()``）：一次代理抖动不该钉死整个进程。
_force_direct_until = 0.0
#: 钉子只活 45 秒：加速器/VPN 抖一下是常态，而重试代理的代价只是一次多出来的往返。
DEFAULT_DIRECT_PIN_SECONDS = 45.0
_direct_opener: urllib.request.OpenerDirector | None = None

OpenerFactory = Callable[[bool, Any], urllib.request.OpenerDirector]


def _opener(use_proxy: bool, context=None) -> urllib.request.OpenerDirector:
    """取 opener：``use_proxy=False`` 强制直连；``context`` 为 SSL 上下文。"""
    handlers: list[Any] = [SafeRedirectHandler()]
    if not use_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not use_proxy and context is None:
        global _direct_opener
        if _direct_opener is None:
            _direct_opener = urllib.request.build_opener(*handlers)
        return _direct_opener
    return urllib.request.build_opener(*handlers)


def force_direct() -> bool:
    """本进程此刻是否固定直连（诊断/测试用）。钉子过期即回到「重新试代理」。"""
    return bool(_force_direct) and time.monotonic() < _force_direct_until


def reset_transport() -> None:
    """复位传输层结论（测试隔离；网络环境变化后可显式重试代理）。"""
    global _force_direct, _force_direct_until, _direct_opener
    _force_direct = False
    _force_direct_until = 0.0
    _direct_opener = None


def urlopen(
    request,
    *,
    timeout: float,
    context=None,
    opener: OpenerFactory | None = None,
    pollute: bool = True,
    routes: tuple[bool, ...] | None = None,
    **kwargs,
):
    """同 :func:`urllib.request.urlopen`，但代理连不上时自动改直连重试一次。

    ``opener`` 是测试接缝：给定时用它构造 opener，且不固化进程级结论。
    ``pollute=False`` 给「自己决定路由」的调用方（搜索/读页）：这类请求的成功
    不代表整个进程可以固定直连，**不得**改写进程级「固定直连」结论。
    ``HTTPError`` 原样抛出；两种方式都失败时抛最后一次的传输异常。

    ``routes`` 是**本次调用**的路由计划：``True`` 走系统代理、``False`` 只直连，
    例如 ``(False,)`` = 只直连、``(True,)`` = 只走代理；``None`` 用默认计划
    （代理优先，传输失败改直连）。显式计划**不改写**进程级「固定直连」结论——
    那是「这台 API 能不能走代理」的进程级判断，一次按调用方要求的路由代表不了它。

    **5xx 换一条路由再问一次**：网关（尤其是代理/VPN）返回 500/502/503/504 时
    链路是通的、API 可能完全正常，拿它判定「网络不可达」会让用户去检查路由。
    4xx/429 与带 ``Retry-After`` 的答复不换路（那是服务端对这次请求的明确答复）。

    **每次尝试都重建请求对象**：``ProxyHandler`` 会把 ``req.host`` 改成代理地址、
    ``req.selector`` 改成绝对 URL，复用同一个对象「重试直连」等于再拨一次代理。

    **「固定直连」是有期限的**（``DEFAULT_DIRECT_PIN_SECONDS``）：加速器/VPN 抖动
    十秒不该让进程此后一辈子不再试代理——那会让「过一会儿又能用了」的网络变成
    永久「网络不可达」，只有重启才恢复。钉着直连时直连再失败会把代理还回去试一次；
    直连也没通就**清掉结论**，下一次调用重新按代理优先试。
    """
    global _force_direct, _force_direct_until
    pinned = force_direct()
    if _force_direct and not pinned:
        _force_direct = False
        _force_direct_until = 0.0
    if routes is not None:
        attempts = tuple(bool(item) for item in routes) or (False,)
    elif pinned:
        # 钉着直连：直连哪怕只抖一下，用户也已经多等了一个往返，把代理还给他。
        attempts = (False, True)
    else:
        attempts = (True, False)
    build = opener or _opener
    last_exc: Exception | None = None
    for index, use_proxy in enumerate(attempts):
        attempt = clone_request(request)
        try:
            response = build(use_proxy, context).open(attempt, timeout=timeout, **kwargs)
        except urllib.error.HTTPError as exc:
            # 还有别的路由、且这次答复可能是网关自己的错：换一条路再问一次。
            # 不换路的那几类（4xx/429/带 Retry-After）保持原样抛出：调用方
            # （`api/probe.py` 的状态码、`net/market.py` 的 304）认的就是 HTTPError。
            if (
                index + 1 < len(attempts)
                and _route_retryable(exc)
                and _has_other_route(request, opener)
            ):
                logger.info("HTTP %s：换路由重试一次", int(getattr(exc, "code", 0) or 0))
                _discard_response(exc)
                last_exc = exc
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - 传输失败：换下一种方式再试
            last_exc = exc
            continue
        if opener is None and pollute and routes is None:
            if use_proxy and _force_direct:
                # 代理又能用了：立刻解除「固定直连」，别让一次抖动钉死整个进程。
                _force_direct = False
                _force_direct_until = 0.0
                logger.info("系统代理已恢复：不再固定直连")
            elif not use_proxy and not _force_direct:
                _force_direct = True
                _force_direct_until = time.monotonic() + DEFAULT_DIRECT_PIN_SECONDS
                logger.info(
                    "网络请求改走直连：系统代理不可用（%s）",
                    type(last_exc).__name__ if last_exc is not None else "-",
                )
        return response
    # 每条路由都没通：钉子（「直连能用」）的前提已经不成立，下次重新试代理。
    if opener is None and pollute and _force_direct:
        _force_direct = False
        _force_direct_until = 0.0
        logger.info("直连也没通：清除「固定直连」结论，下次重新试代理")
    assert last_exc is not None
    raise last_exc


def _has_other_route(request, opener: OpenerFactory | None) -> bool:
    """这次 5xx 之后还有没有**另一条真不一样**的路。

    没有系统代理、或 ``no_proxy`` 把目标主机排除了，``ProxyHandler`` 会直接放行：
    「代理」与「直连」其实是同一条路，再问一次只会把同一个 5xx 拿回来（聊天请求
    还要多花一次额度）。给了自定义 ``opener`` 时按「有两条路」处理——那是调用方
    自己建模的路由，本模块无从（也不该）推断。
    """
    if opener is not None:
        return True
    try:
        proxies = {
            key: value
            for key, value in urllib.request.getproxies().items()
            if str(key).lower() != "no_proxy"
        }
    except Exception:  # noqa: BLE001 - 读不到代理配置就按「有代理」处理
        return True
    if not any(str(value or "").strip() for value in proxies.values()):
        return False
    try:
        host = str(getattr(request, "host", "") or "")
        return not host or not urllib.request.proxy_bypass(host)
    except Exception:  # noqa: BLE001 - 判断不了就多问一次（宁可多也不误判）
        return True


def _route_retryable(exc: urllib.error.HTTPError) -> bool:
    """这次 HTTPError 值不值得换一条路由再问一次。

    只有 5xx 网关错误值得：4xx/429 是服务端对这次请求的答复（换路只会把限流
    当链路故障），带 ``Retry-After`` 时更是服务端明确要求等待。
    """
    try:
        status = int(getattr(exc, "code", 0) or 0)
    except (TypeError, ValueError):
        return False
    if status not in ROUTE_RETRY_STATUSES:
        return False
    try:
        raw = exc.headers.get("Retry-After") if exc.headers is not None else None
    except (AttributeError, TypeError, ValueError):
        raw = None
    return not str(raw or "").strip()


def _discard_response(exc: BaseException) -> None:
    """放弃一个不打算交给调用方的响应：关掉它，别把连接晾在那儿。"""
    close = getattr(exc, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响换路
        pass



def build_request(
    url: str,
    *,
    method: str = "GET",
    headers: tuple[tuple[str, str], ...] = (),
    body: bytes = b"",
    params: tuple[tuple[str, str], ...] = (),
) -> urllib.request.Request:
    """构造一次请求；每次换路由都重新构造，避免复用被 OpenDirector 改过的对象。"""
    target = str(url)
    if params:
        query = urllib.parse.urlencode([(str(k), str(v)) for k, v in params])
        if query:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}{query}"
    data = body if body else None
    request = urllib.request.Request(target, data=data, method=str(method or "GET"))
    for key, value in headers:
        request.add_header(str(key), str(value))
    return request


def _build_opener(
    use_proxy: bool, context, max_redirects: int
) -> urllib.request.OpenerDirector:
    if max_redirects == DEFAULT_MAX_REDIRECTS:
        return _opener(use_proxy, context)
    handlers: list[Any] = [SafeRedirectHandler(max_redirects)]
    if not use_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def fetch_url(
    url: str,
    *,
    method: str = "GET",
    headers: tuple[tuple[str, str], ...] = (),
    body: bytes = b"",
    params: tuple[tuple[str, str], ...] = (),
    timeout: float = DEFAULT_TIMEOUT,
    prefer_direct: bool = False,
    direct_only: bool = False,
    retry_statuses: tuple[int, ...] = DEFAULT_FALLBACK_STATUSES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    context=None,
    opener: OpenerFactory | None = None,
    clock: Callable[[], float] = time.monotonic,
    allowed_schemes: tuple[str, ...] = DEFAULT_ALLOWED_SCHEMES,
    allow_private: bool = True,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
):
    """按显式路由取数，返回 ``(status, headers, body)``；``body`` 是 :class:`Body`。

    与 :func:`urlopen` 的区别：**不改写进程级「固定直连」结论**。天气这类
    「国内站直连、海外站走代理」的功能要求每次请求自己决定路由，任何一次失败
    都不该污染其它域（主分支 `pet/weather.py` 的口径）。

    路由顺序由 ``prefer_direct`` 决定，最多两次；两次共享同一个 ``timeout``
    预算。换路由条件只有两种：传输失败，或状态码在 ``retry_statuses`` 里
    且响应**没有** ``Retry-After``（服务端明确要求等待时换路等于把限流当链路故障）。
    ``direct_only=True`` 只走直连、失败也不切代理（本地模型用：不能把本地 Key
    与朗读文本发给系统代理）。``allow_private=False`` 用于「URL 来自模型/搜索页」
    的场景：本机与内网地址一律拒绝。
    """
    validate_target(url, allowed_schemes=allowed_schemes, allow_private=allow_private)
    deadline = clock() + max(0.05, float(timeout))
    if direct_only:
        order = (False,)
    else:
        order = (False, True) if prefer_direct else (True, False)
    if opener is not None:
        build: OpenerFactory = opener
    elif max_redirects == DEFAULT_MAX_REDIRECTS:
        build = _opener
    else:
        build = functools.partial(_build_opener, max_redirects=max_redirects)  # type: ignore[assignment]
    last: BaseException | None = None
    for index, use_proxy in enumerate(order):
        remaining = deadline - clock()
        if remaining <= 0:
            raise NetworkTimeout("连接超时")
        request = build_request(
            url, method=method, headers=headers, body=body, params=params
        )
        try:
            response = build(use_proxy, context).open(request, timeout=remaining)
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            converted = http_error(exc)
            if (
                index == 0
                and status in retry_statuses
                and getattr(converted, "retry_after", None) is None
            ):
                last = converted
                logger.info("HTTP %s：换路由重试一次", status)
                continue
            raise converted from None
        except Exception as exc:  # noqa: BLE001 - 传输失败：换下一种方式
            last = exc
            if index == 0:
                continue
            raise _transport_error(exc) from None
        try:
            headers_out = tuple(
                (str(key), str(value)) for key, value in (response.headers or {}).items()
            )
            status = int(getattr(response, "status", 0) or 0)
            declared = _content_length(response)
            body_bytes = _read_bounded(response, deadline, max_bytes, clock, declared)
        finally:
            try:
                response.close()
            except Exception:  # closing must not mask the original read failure
                pass
        return status, headers_out, body_bytes
    raise _transport_error(last) from None


def _content_length(response) -> int | None:
    try:
        raw = response.headers.get("Content-Length") if response.headers is not None else None
    except Exception:  # noqa: BLE001 - 头部对象不可解析就当没有声明
        return None
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _read_bounded(
    response, deadline: float, max_bytes: int, clock, declared: int | None = None
) -> Body:
    """读响应体：硬上限 + 共享 deadline；截断必须**说出来**，不能静默返回半截。"""
    limit = max(0, int(max_bytes))
    remaining = deadline - clock()
    if remaining <= 0:
        raise NetworkTimeout("连接超时")
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        want = min(_READ_CHUNK, limit + 1 - total)
        if want <= 0:
            break
        chunk = response.read(want)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if clock() > deadline:
            raise NetworkTimeout("连接超时")
    data = b"".join(chunks)
    truncated = len(data) > limit or (declared is not None and len(data) < declared)
    return Body(data[:limit], truncated=truncated, declared_length=declared)


def _transport_error(exc: BaseException | None) -> NetworkError:
    if exc is None:
        return NetworkError(reason=NETWORK)
    if isinstance(exc, HttpStatusError):
        return exc
    reason = classify_exception(exc) or NETWORK
    if reason == TIMEOUT:
        return NetworkTimeout("连接超时")
    return NetworkError(reason=reason)

