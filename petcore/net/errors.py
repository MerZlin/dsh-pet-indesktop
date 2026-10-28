"""联网失败的稳定分类。

上层（设置页、气泡、mod 端口）只认这几个 reason 字符串，不解析异常文本：
``auth`` / ``rate_limit`` / ``network`` / ``timeout`` / ``tls`` / ``protocol`` /
``internal``。
服务器响应体一律不读、不回显——它可能原样回显请求里的密钥。

``tls`` 与 ``network`` 必须分开：证书校验失败（企业 MITM 代理、时钟偏差、自签证书）
说明链路是通的，报「网络不可达」只会让用户去重启路由。

``internal`` 也不是链路问题：**一次请求根本没发出去**（没有可用服务、服务 ID 对不上、
本地 payload 组装失败等）。它存在的唯一理由是「不许把内部错误谎报成网络不可达」——
那会把用户赶去重启路由，而且日志里什么都不留。
"""

from __future__ import annotations

import email.utils
import http.client
import ssl
import time
import urllib.error

AUTH = "auth"
RATE_LIMIT = "rate_limit"
NETWORK = "network"
TIMEOUT = "timeout"
TLS = "tls"
PROTOCOL = "protocol"
#: 请求没发出去/本地状态不自洽（没有服务、服务 ID 对不上），与链路无关。
INTERNAL = "internal"

REASON_TEXT = {
    AUTH: "密钥无效或没有权限",
    RATE_LIMIT: "请求过于频繁，稍后再试",
    NETWORK: "网络不可达",
    TIMEOUT: "连接超时",
    TLS: "证书校验失败",
    PROTOCOL: "服务端返回了无法识别的响应",
    INTERNAL: "内部错误（不是网络问题，详情见日志）",
}


class NetworkError(RuntimeError):
    """所有联网失败的基类；``reason`` 是稳定分类。"""

    reason = NETWORK

    def __init__(self, message: str = "", *, reason: str | None = None):
        if reason is not None:
            self.reason = reason
        super().__init__(message or REASON_TEXT.get(self.reason, self.reason))


class NetworkUnreachable(NetworkError):
    reason = NETWORK


class NetworkTimeout(NetworkError):
    reason = TIMEOUT


class HttpStatusError(NetworkError):
    """服务器有明确答复（4xx/5xx）。"""

    reason = PROTOCOL

    def __init__(self, status: int, *, retry_after: float | None = None):
        self.status = int(status)
        self.retry_after = retry_after
        super().__init__(
            f"HTTP {self.status}", reason=classify_http_status(self.status)
        )


class AuthError(HttpStatusError):
    reason = AUTH


class RateLimitError(HttpStatusError):
    reason = RATE_LIMIT


def classify_http_status(status: int) -> str:
    """状态码 → 稳定 reason。"""
    try:
        code = int(status)
    except (TypeError, ValueError):
        return PROTOCOL
    if code in (401, 403):
        return AUTH
    if code == 429:
        return RATE_LIMIT
    if code in (408, 425, 500, 502, 503, 504):
        return NETWORK
    return PROTOCOL


def _parse_retry_after(raw: object) -> float | None:
    """``Retry-After`` 两种合法形态：秒数，或 HTTP-date（RFC 7231）。"""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    try:
        return max(0.0, when.timestamp() - time.time())
    except (OverflowError, OSError, ValueError):
        return None


def http_error(exc: urllib.error.HTTPError) -> HttpStatusError:
    """把 ``HTTPError`` 收敛成带分类与 ``Retry-After`` 的异常。"""
    # Converting consumes the raw HTTPError, which owns an open response body.
    try:
        retry_after = None
        try:
            raw = exc.headers.get("Retry-After") if exc.headers is not None else None
            retry_after = _parse_retry_after(raw)
        except (TypeError, ValueError, AttributeError):
            retry_after = None
        status = int(getattr(exc, "code", 0) or 0)
        reason = classify_http_status(status)
        if reason == AUTH:
            return AuthError(status, retry_after=retry_after)
        if reason == RATE_LIMIT:
            return RateLimitError(status, retry_after=retry_after)
        return HttpStatusError(status, retry_after=retry_after)
    finally:
        try:
            exc.close()
        except Exception:
            pass


def _is_certificate_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, ssl.CertificateError):
        return True
    return False


def classify_exception(exc: BaseException) -> str | None:
    """传输异常 → 稳定 reason；无法识别返回 ``None``。"""
    if isinstance(exc, NetworkError):
        return exc.reason
    if isinstance(exc, urllib.error.HTTPError):
        return classify_http_status(exc.code)
    if isinstance(exc, urllib.error.URLError):
        if _is_certificate_error(getattr(exc, "reason", None)):
            return TLS
        return TIMEOUT if isinstance(exc.reason, TimeoutError) else NETWORK
    if _is_certificate_error(exc):
        return TLS
    if isinstance(exc, http.client.HTTPException):
        # IncompleteRead / BadStatusLine：链路通了但响应不完整/不合法。
        return PROTOCOL
    if isinstance(exc, TimeoutError):
        return TIMEOUT
    if isinstance(exc, ssl.SSLError):
        return NETWORK
    if isinstance(exc, OSError):
        return NETWORK
    return None


def describe(reason: str) -> str:
    return REASON_TEXT.get(str(reason), "未知错误")
