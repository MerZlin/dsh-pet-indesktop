"""B 站只读取数：粉丝总数（免登录）与粉丝名单（需要 SESSDATA）。

安全边界：
- ``SESSDATA`` **只**放进请求头，绝不写日志、异常文本或返回值；
- 未登录/风控（``code`` -101/-352/-111）统一按 ``auth`` 分类，消息只写状态；
- 粉丝名单按页累计，超出单次预算返回 ``partial``，调用方据此**不覆盖**已有完整名单。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from .errors import AUTH, PROTOCOL, NetworkError, classify_exception
from .http import fetch_url

logger = logging.getLogger("petcore.net.bilibili")

STAT_API = "https://api.bilibili.com/x/relation/stat"
FANS_API = "https://api.bilibili.com/x/relation/fans"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_BYTES = 4_000_000
DEFAULT_PAGE_SIZE = 50
MAX_PAGES = 120

_AUTH_CODES = {-101, -111, -352}


def _get_json(
    url: str,
    *,
    params: tuple[tuple[str, str], ...],
    headers: tuple[tuple[str, str], ...] = (),
    timeout: float,
    prefer_direct: bool = True,
) -> dict:
    try:
        _status, _headers, body = fetch_url(
            url,
            headers=(("User-Agent", USER_AGENT), ("Referer", "https://space.bilibili.com/")) + headers,
            params=params,
            timeout=timeout,
            prefer_direct=prefer_direct,
            max_bytes=MAX_BYTES,
        )
    except NetworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - 传输层意外形态
        raise NetworkError(type(exc).__name__, reason=classify_exception(exc) or PROTOCOL) from exc
    if getattr(body, "truncated", False):
        raise NetworkError("B 站响应被截断", reason=PROTOCOL)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise NetworkError("B 站返回了无法解析的响应", reason=PROTOCOL) from None
    if not isinstance(payload, dict):
        raise NetworkError("B 站返回了无法解析的响应", reason=PROTOCOL)
    code = payload.get("code")
    if code not in (0, None):
        try:
            numeric = int(code)
        except (TypeError, ValueError):
            numeric = 0
        if numeric in _AUTH_CODES:
            raise NetworkError("B 站未登录或触发风控", reason=AUTH)
        raise NetworkError(f"B 站返回 {str(code)[:20]}", reason=PROTOCOL)
    return payload


def _clean_vmid(vmid: Any) -> int:
    try:
        value = int(vmid)
    except (TypeError, ValueError):
        raise NetworkError("B 站 UID 不合法", reason=PROTOCOL) from None
    if value <= 0:
        raise NetworkError("B 站 UID 不合法", reason=PROTOCOL)
    return value


def sessdata_value(value: Any) -> str:
    """接受裸 SESSDATA 或整段 Cookie；只取 SESSDATA 的值。

    带 ``=``/``;`` 但找不到 SESSDATA 的输入按 auth 拒绝——否则会把整段 Cookie
    当成 SESSDATA 发出去，用户只会看到无法解释的登录失败。
    """
    text = str(value or "").strip()
    if not text:
        raise NetworkError("B 站登录凭据未配置", reason=AUTH)
    found = ""
    for part in text.split(";"):
        key, separator, raw = part.strip().partition("=")
        if separator and key.strip().lower() == "sessdata":
            found = raw.strip()
            break
    if found:
        text = found
    elif "=" in text or ";" in text:
        raise NetworkError("B 站登录凭据里没有 SESSDATA", reason=AUTH)
    text = text.strip().strip('"').strip("'")
    if not text:
        raise NetworkError("B 站登录凭据未配置", reason=AUTH)
    return text


def fetch_fan_count(vmid: int, *, timeout: float = 8.0) -> int:
    """粉丝总数；``x/relation/stat`` 免登录，用来判断是否需要拉完整名单。"""
    uid = _clean_vmid(vmid)
    payload = _get_json(
        STAT_API,
        params=(("vmid", str(uid)),),
        timeout=timeout,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise NetworkError("B 站粉丝数响应格式不对", reason=PROTOCOL)
    if "follower" not in data:
        # 拿不到就是拿不到：写成 0 会让「总数变了」的判断永远成立。
        raise NetworkError("B 站粉丝数响应缺少 follower", reason=PROTOCOL)
    try:
        return max(0, int(data.get("follower")))
    except (TypeError, ValueError):
        raise NetworkError("B 站粉丝数响应格式不对", reason=PROTOCOL) from None


def fetch_fans(
    vmid: int,
    *,
    sessdata: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    timeout: float = 8.0,
    budget_s: float = 60.0,
    progress: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """粉丝名单（按 B 站返回顺序）。

    返回 ``{"names": [...], "total": n, "pages": p, "partial": bool}``；
    ``partial=True`` 表示预算/页数上限用完，调用方不得用它覆盖完整名单。
    """
    uid = _clean_vmid(vmid)
    cookie = sessdata_value(sessdata)
    size = max(1, min(DEFAULT_PAGE_SIZE, int(page_size)))
    limit = max(1, int(max_pages))
    deadline = clock() + max(1.0, float(budget_s))
    names: list[str] = []
    total = 0
    page = 1
    partial = False
    while page <= limit:
        if clock() >= deadline:
            partial = True
            break
        payload = _get_json(
            FANS_API,
            params=(
                ("vmid", str(uid)),
                ("pn", str(page)),
                ("ps", str(size)),
                ("order", "desc"),
            ),
            headers=(
                ("Cookie", f"SESSDATA={cookie}"),
                ("Referer", f"https://space.bilibili.com/{uid}/relation/fans"),
            ),
            timeout=min(float(timeout), max(1.0, deadline - clock())),
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise NetworkError("B 站粉丝响应格式不对", reason=PROTOCOL)
        try:
            total = max(total, int(data.get("total") or 0))
        except (TypeError, ValueError):
            pass
        items = data.get("list")
        if not isinstance(items, list):
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("uname") or "").strip()
            if name:
                names.append(name)
        total_pages = max(1, (total + size - 1) // size) if total else max(1, page)
        if progress is not None:
            try:
                progress(page, total_pages)
            except Exception:  # noqa: BLE001 - 进度回调失败不影响抓取
                logger.debug("bilibili progress callback failed", exc_info=True)
        if len(items) < size:
            break
        if page == limit:
            # 只有「页数/预算用完」才算半截；名单长度与 total 的差值可能来自
            # B 站隐私设置（部分粉丝不展示），不能因此永久拒绝落盘。
            partial = True
            break
        page += 1
    return {
        "names": names,
        "total": total,
        "pages": page,
        "partial": bool(partial),
    }
