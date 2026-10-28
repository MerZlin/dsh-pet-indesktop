"""联网搜索：搜索引擎适配 + 网页正文抓取（只用标准库）。

DeepSeek 等 OpenAI 兼容 API 本身不带联网能力：``api.chat`` 在正式回答前先让模型
（非思考模式、工具调用）决定要不要搜、搜什么，由本模块执行搜索/读网页，再把资料
作为上下文交给正式回答。

引擎（同主分支）：

- ``baidu``：免 Key，抓取百度网页结果（默认；国内直连可用）
- ``duckduckgo``：免 Key，抓取 html.duckduckgo.com（国内通常要代理）
- ``bocha`` / ``tavily``：需 API Key

两个免 Key 引擎互为兜底；频繁请求会被人机验证拦下，此时如实报错、不绕过。
密钥不经过本模块的配置对象：调用方（``api.chat`` / 端口）从钥匙串取好再传进来。
"""

from __future__ import annotations

import html as _html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import errors
from .http import urlopen, validate_target

__all__ = [
    "ENGINE_LABELS",
    "SearchError",
    "SearchResult",
    "fetch_page_text",
    "format_results",
    "html_to_text",
    "parse_baidu_html",
    "parse_duckduckgo_html",
    "search",
]

ENGINE_LABELS = {
    "baidu": "百度（免 Key）",
    "duckduckgo": "DuckDuckGo（免 Key）",
    "bocha": "博查 Bocha（需 API Key）",
    "tavily": "Tavily（需 API Key）",
}

#: 需要钥匙串里对应 Key 的引擎。
KEYED_ENGINES = frozenset({"bocha", "tavily"})

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_MAX_BYTES = 2 * 1024 * 1024
_FREE_FALLBACK = {"baidu": "duckduckgo", "duckduckgo": "baidu"}
_CAPTCHA_HINT = "，请稍后再试；需要稳定联网可在 AI 设置里改用博查 / Tavily"


class SearchError(RuntimeError):
    """搜索/读页失败；``reason`` 与 ``net.errors`` 同一套稳定分类。"""

    def __init__(self, message: str = "", *, reason: str = errors.NETWORK):
        self.reason = reason
        super().__init__(message or errors.REASON_TEXT.get(reason, reason))


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


# ---------------------------------------------------------------- HTTP 边界


def _context(verify_ssl: bool):
    context = ssl.create_default_context()
    if not verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _request(url: str, *, headers: dict | None = None, data: bytes | None = None):
    merged = dict(_BROWSER_HEADERS)
    merged.update(headers or {})
    return urllib.request.Request(url, data=data, headers=merged, method="POST" if data else "GET")


def _open(request, *, timeout: float, verify_ssl: bool, opener=None):
    try:
        return urlopen(
            request,
            timeout=max(1.0, float(timeout)),
            context=_context(verify_ssl),
            opener=opener,
            # 搜索/读页自己选路由：这一次的成功不代表整个进程该固定直连。
            pollute=False,
        )
    except urllib.error.HTTPError as exc:
        raise SearchError(
            f"HTTP {int(exc.code)}", reason=errors.classify_http_status(int(exc.code))
        ) from None
    except Exception as exc:  # noqa: BLE001 - 传输失败统一分类
        reason = errors.classify_exception(exc)
        if reason is None:
            raise
        raise SearchError(reason=reason) from None


def _decode(raw: bytes, content_type: str) -> tuple[str, str]:
    charset = ""
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    if not charset:
        match = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
        charset = match.group(1).decode("ascii", "ignore") if match else ""
    try:
        return raw.decode(charset or "utf-8", "replace"), content_type or ""
    except LookupError:
        return raw.decode("utf-8", "replace"), content_type or ""


def _get(url: str, *, timeout: float, verify_ssl: bool, opener=None):
    request = _request(url, headers={"Accept": _HTML_ACCEPT})
    response = _open(request, timeout=timeout, verify_ssl=verify_ssl, opener=opener)
    try:
        raw = response.read(_MAX_BYTES)
        content_type = str(response.headers.get("Content-Type") or "")
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响结果
            pass
    text, content_type = _decode(raw, content_type)
    return text, content_type


def _post_json(url: str, payload: dict, headers: dict, *, timeout: float, verify_ssl: bool, opener=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = _request(
        url,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        data=body,
    )
    response = _open(request, timeout=timeout, verify_ssl=verify_ssl, opener=opener)
    try:
        raw = response.read(_MAX_BYTES)
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise SearchError("搜索服务返回了无效 JSON", reason=errors.PROTOCOL) from None
    if not isinstance(data, dict):
        raise SearchError("搜索服务返回了无效响应", reason=errors.PROTOCOL)
    return data


# ---------------------------------------------------------------- 解析器


def _clean_text(fragment: str) -> str:
    fragment = re.sub(
        r"<(script|style|noscript|svg)\b.*?</\1\s*>", " ", fragment, flags=re.S | re.I
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(_html.unescape(fragment).split())


def parse_duckduckgo_html(page: str, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for chunk in re.split(r'<div class="result results_links', page)[1:]:
        if "result--ad" in chunk[:300]:
            continue
        match = re.search(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S
        )
        if not match:
            continue
        href = _html.unescape(match.group(1))
        if href.startswith("//"):
            href = "https:" + href
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get(
            "uddg", [href]
        )[0]
        if "duckduckgo.com/y.js" in target:
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', chunk, re.S)
        results.append(
            SearchResult(
                _clean_text(match.group(2)),
                target,
                _clean_text(snippet.group(1)) if snippet else "",
            )
        )
        if len(results) >= limit:
            break
    return results


def parse_baidu_html(page: str, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen: set[str] = set()
    for chunk in re.split(r'(?=<div[^>]*class="result(?:-op)? c-container)', page)[1:]:
        mu = re.search(r'\bmu="([^"]*)"', chunk[:2000])
        heading = re.search(r"<h3[^>]*>(.*?)</h3>", chunk, re.S)
        if not mu or not heading:
            continue
        url = _html.unescape(mu.group(1)).strip()
        host = urllib.parse.urlparse(url).netloc
        if (
            not url.startswith("http")
            or "nourl" in url
            or url in seen
            or (host.endswith("baidu.com") and ("/s?" in url or "recommend" in host))
        ):
            continue
        title = _clean_text(heading.group(1))
        snippet = _clean_text(chunk[heading.end() : heading.end() + 8000])
        snippet = re.split(r'"\}|"clamp"|\{"', snippet)[0].strip()[:300]
        if not title:
            continue
        seen.add(url)
        results.append(SearchResult(title, url, snippet))
        if len(results) >= limit:
            break
    return results


def html_to_text(page: str) -> str:
    """粗粒度正文提取：去脚本/样式/导航，块级标签转换行。"""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    body = re.sub(
        r"<(script|style|noscript|svg|nav|footer|header|form|iframe)\b.*?</\1\s*>",
        " ",
        page,
        flags=re.S | re.I,
    )
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(
        r"<(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\b[^>]*>", "\n", body, flags=re.I
    )
    body = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [" ".join(line.split()) for line in body.splitlines()]
    text = "\n".join(line for line in lines if len(line) > 1)
    title = _clean_text(title_match.group(1)) if title_match else ""
    return (f"标题：{title}\n" if title else "") + text


# ---------------------------------------------------------------- 对外入口


def search(
    query: str,
    *,
    engine: str = "baidu",
    api_key: str = "",
    limit: int = 5,
    timeout: float = 15.0,
    verify_ssl: bool = True,
    opener=None,
) -> list[SearchResult]:
    """执行一次搜索；免 Key 引擎失败时自动换另一个兜底（同主分支）。"""
    query = " ".join(str(query or "").split())
    if not query:
        raise SearchError("搜索词为空", reason=errors.PROTOCOL)
    engine = str(engine or "baidu")
    if engine not in ENGINE_LABELS:
        raise SearchError(f"未知搜索引擎：{engine}", reason=errors.PROTOCOL)
    count = max(1, min(10, int(limit or 5)))
    options = {
        "limit": count,
        "timeout": timeout,
        "verify_ssl": verify_ssl,
        "api_key": api_key,
        "opener": opener,
    }
    if engine not in _FREE_FALLBACK:
        return _search_engine(engine, query, **options)
    try:
        return _search_engine(engine, query, **options)
    except SearchError as first:
        try:
            return _search_engine(_FREE_FALLBACK[engine], query, **options)
        except SearchError as second:
            raise SearchError(f"{first}；备用引擎也失败：{second}") from None


def _search_engine(
    engine: str,
    query: str,
    *,
    limit: int,
    timeout: float,
    verify_ssl: bool,
    api_key: str,
    opener=None,
) -> list[SearchResult]:
    if engine == "baidu":
        page, _ = _get(
            "https://www.baidu.com/s?ie=utf-8&wd=" + urllib.parse.quote(query),
            timeout=timeout,
            verify_ssl=verify_ssl,
            opener=opener,
        )
        if "wappass.baidu.com" in page[:5000] or "百度安全验证" in page[:5000]:
            raise SearchError("百度要求人机验证" + _CAPTCHA_HINT, reason=errors.PROTOCOL)
        return parse_baidu_html(page, limit)
    if engine in KEYED_ENGINES:
        if not api_key:
            raise SearchError(
                f"{ENGINE_LABELS[engine]} 未配置 API Key（设置 → AI 对话 → 联网搜索）",
                reason=errors.AUTH,
            )
        if engine == "bocha":
            data = _post_json(
                "https://api.bochaai.com/v1/web-search",
                {"query": query, "summary": True, "count": limit, "freshness": "noLimit"},
                {"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
                verify_ssl=verify_ssl,
                opener=opener,
            )
            pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
            return [
                SearchResult(
                    str(item.get("name", "")),
                    str(item.get("url", "")),
                    str(item.get("summary") or item.get("snippet") or "")[:600],
                )
                for item in pages[:limit]
                if isinstance(item, dict)
            ]
        data = _post_json(
            "https://api.tavily.com/search",
            {"query": query, "max_results": limit, "search_depth": "basic"},
            {"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            verify_ssl=verify_ssl,
            opener=opener,
        )
        return [
            SearchResult(
                str(item.get("title", "")),
                str(item.get("url", "")),
                str(item.get("content", ""))[:600],
            )
            for item in (data.get("results") or [])[:limit]
            if isinstance(item, dict)
        ]
    page, _ = _get(
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query),
        timeout=timeout,
        verify_ssl=verify_ssl,
        opener=opener,
    )
    if "anomaly-modal" in page or "bots use DuckDuckGo too" in page:
        raise SearchError(
            "DuckDuckGo 要求人机验证" + _CAPTCHA_HINT, reason=errors.PROTOCOL
        )
    return parse_duckduckgo_html(page, limit)


def fetch_page_text(
    url: str,
    *,
    timeout: float = 15.0,
    verify_ssl: bool = True,
    max_chars: int = 5000,
    opener=None,
) -> str:
    """读取网页正文（限长）；只支持 http/https，且拒绝本机与内网地址。

    这里的 URL 来自**模型生成的工具参数**，可以被网页内容诱导，所以必须比
    普通取数更严：`file://` 能直接读本机文件，`127.0.0.1`/`10.x` 能读内网服务
    与云元数据。
    """
    url = str(url or "").strip()
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise SearchError("只支持 http/https 网址", reason=errors.PROTOCOL)
    try:
        validate_target(url, allowed_schemes=("http", "https"), allow_private=False)
    except errors.NetworkError as exc:
        raise SearchError(str(exc), reason=errors.PROTOCOL) from None
    page, content_type = _get(
        url, timeout=timeout, verify_ssl=verify_ssl, opener=opener
    )
    lowered = content_type.lower()
    if lowered and "html" not in lowered and "text" not in lowered:
        raise SearchError(
            f"不支持的网页类型：{content_type.split(';')[0]}", reason=errors.PROTOCOL
        )
    text = html_to_text(page) if "<" in page[:2000] else page
    return text[:max_chars] + ("…（已截断）" if len(text) > max_chars else "")


def format_results(query: str, results: list[SearchResult]) -> str:
    if not results:
        return f"搜索「{query}」没有找到结果。"
    lines = [f"搜索「{query}」的结果："]
    for index, item in enumerate(results, 1):
        lines.append(
            f"[{index}] {item.title}\n链接：{item.url}\n摘要：{item.snippet or '（无）'}"
        )
    return "\n\n".join(lines)
