# -*- coding: utf-8 -*-
"""歌词获取、LRC 解析与磁盘缓存。

按"匹配质量"排序依次尝试三个歌词源，命中即返回：

1. **QQ音乐** —— 中文曲库匹配最好，实测搜索首条即原唱。
2. **lrclib** —— 国际/日文曲目覆盖好，字段规范。
3. **网易云** —— 最后兜底；实测搜索常返回翻唱，故靠后。

三个源都是明文 HTTP 接口，无需加密，也不下载任何音频（单曲响应约 10 KB）。

**网络边界：歌词请求首选直连，直连坏掉时才回退一次系统代理。** 三个源都是公开
接口、其中两个还是国内域名，走系统代理只会多绕一段路；2026-09-22 实机事故里，
用户开着全局代理时三个源单次耗时 20~41 秒（全部超过 ``HTTP_TIMEOUT``），于是每首
未缓存曲目都以「0 行」收场，看起来就像歌词功能被改坏了（详见
``docs/PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md``）。

但反过来也有用户**只有代理能出网**：2026-10-16 汽水音乐那次，同一个 URL、同一分钟
直连恒 HTTP 500 而走系统代理 200 并真的取回歌词（详见
``docs/PR-REPORT-SODA-LYRICS-ROUTE-2026-10-16.md``）。所以直连失败且属于**传输失败
或 HTTP 500/502/504** 时，允许**回退一次**系统代理；401/403/404/429/503 这类
权限/不存在/限流答复不换路（见 :func:`_may_switch_to_proxy`）。两条路线共享
``fetch_lyrics`` 的同一份截止预算，不会把一次请求变成两倍超时。

缓存放数据目录下的 ``lyrics_cache/``，只存歌词文本，不存音频、不存收听历史；
条目数超过上限时按文件修改时间淘汰最旧的。用户可在设置页一键清空。

本模块的纯函数（``parse_lrc`` / 曲目匹配）不依赖 Qt，可独立单测。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# 缓存条目上限：按实测量级约 10 KB/首，2000 首 ≈ 21 MB。
CACHE_LIMIT = 2000
# 单个 HTTP 请求超时（秒）。歌词是锦上添花，宁可失败也不要长时间挂住后台线程。
HTTP_TIMEOUT = 8.0
# 缓存格式版本：解析逻辑变更时可据此失效旧缓存。
# v2：新增 instrumental 标记。v1 缓存把纯音乐的占位文案（"纯音乐，请欣赏"）
# 当成正常歌词存了下来，必须作废重取，否则会被当作有词曲目放起唱歌动画。
_CACHE_VERSION = 2

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 形如 [mm:ss.xx] / [mm:ss:xx] / [mm:ss]，可能一行多个。
_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 只有标签、没有时间戳的行，如 [ti:歌名] —— 按 LRC 规范跳过。
_TAG_RE = re.compile(r"^\[[a-zA-Z#]+:[^\]]*\]\s*$")
# [offset:±毫秒] 需要叠加到所有时间点上。
_OFFSET_RE = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)


@dataclass(frozen=True)
class LyricLine:
    """一行带时间戳的歌词。"""

    at: float
    text: str


@dataclass(frozen=True)
class Lyrics:
    """一次取词的结果。

    ``instrumental`` 表示**纯音乐**：曲目本身没有歌词（配乐 / OST / 演奏曲）。
    调用方据此不显示歌词、也不播唱歌动画——纯音乐没有可唱的句子。
    """

    lines: tuple[LyricLine, ...] = ()
    instrumental: bool = False

    def __bool__(self) -> bool:
        # 纯音乐（lines 为空、instrumental=True）也是**有效结果**：
        # 只认 lines 会让纯音乐缓存永远读不中，每次切回该曲都重打三个源。
        return bool(self.lines) or self.instrumental

    def __iter__(self):
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)


# ---------------------------------------------------------------- LRC 解析


def _parse_stamp(minutes: str, seconds: str, frac: str | None) -> float:
    """把 [mm:ss.xx] 的三段数字换算成秒。"""
    value = int(minutes) * 60 + int(seconds)
    if frac:
        # 两位是厘秒、三位是毫秒，一位按十分之一秒处理。
        value += int(frac) / (10 ** len(frac))
    return value


def parse_lrc(text: str) -> list[LyricLine]:
    """把 LRC 文本解析成按时间排序的歌词行。

    - 跳过不含时间戳的标签行（``[ti:]`` / ``[ar:]`` / ``[by:]`` 等）。
    - 一行有多个时间戳时（``[00:01][00:05]同一句``）展开成多条。
    - 应用 ``[offset:]`` 偏移。
    - 保留空文本行（间奏），调用方据此可让气泡保持上一句或清空。

    注意：**不**过滤"带时间戳但不是歌词"的元信息行（如 ``[00:05] 词：方文山``），
    这是产品上明确接受的行为。
    """
    offset_sec = 0.0
    match = _OFFSET_RE.search(text)
    if match:
        try:
            offset_sec = int(match.group(1)) / 1000.0
        except ValueError:
            offset_sec = 0.0

    lines: list[LyricLine] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        stamps = list(_TIME_RE.finditer(stripped))
        if not stamps:
            # 没有时间戳：可能是标签行，也可能是不规范的纯文本，两种都跳过。
            continue
        if _TAG_RE.match(stripped):
            continue
        # 时间戳之后的剩余内容即歌词文本。
        body = stripped[stamps[-1].end():].strip()
        for stamp in stamps:
            at = _parse_stamp(stamp.group(1), stamp.group(2), stamp.group(3)) + offset_sec
            lines.append(LyricLine(at=max(0.0, at), text=body))

    lines.sort(key=lambda item: item.at)
    return lines


# ---------------------------------------------------------------- 缓存


def cache_dir() -> Path:
    """歌词缓存目录（不存在时创建）。"""
    from ..config import APP_DIR_NAME, _default_base

    path = Path(_default_base()) / APP_DIR_NAME / "lyrics_cache"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.debug("创建歌词缓存目录失败: %s", path, exc_info=True)
    return path


def _cache_path(title: str, artist: str) -> Path:
    digest = hashlib.sha1(
        f"{artist.strip().lower()}|{title.strip().lower()}".encode("utf-8")
    ).hexdigest()
    return cache_dir() / f"{digest}.json"


def _read_cache(title: str, artist: str) -> Lyrics | None:
    path = _cache_path(title, artist)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("v") != _CACHE_VERSION:
            return None
        lines = tuple(
            LyricLine(at=float(item[0]), text=str(item[1]))
            for item in data.get("lines", [])
        )
        instrumental = bool(data.get("instrumental"))
        if not lines and not instrumental:
            return None
        return Lyrics(lines=lines, instrumental=instrumental)
    except Exception:
        log.debug("读取歌词缓存失败: %s", path, exc_info=True)
        return None


def _write_cache(title: str, artist: str, lyrics: Lyrics, limit: int | None = None) -> None:
    if not lyrics.lines and not lyrics.instrumental:
        return
    path = _cache_path(title, artist)
    try:
        path.write_text(
            json.dumps(
                {
                    "v": _CACHE_VERSION,
                    "title": title,
                    "artist": artist,
                    "fetched_at": time.time(),
                    "instrumental": bool(lyrics.instrumental),
                    "lines": [[line.at, line.text] for line in lyrics.lines],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        log.debug("写入歌词缓存失败: %s", path, exc_info=True)
        return
    _prune_cache(CACHE_LIMIT if limit is None else limit)


def _prune_cache(limit: int = CACHE_LIMIT) -> None:
    """条目超上限时，按修改时间淘汰最旧的若干条（LRU 近似）。

    ``limit`` 由调用方决定：生产路径传配置里的 ``music_lyric_cache_limit``
    （见 :func:`fetch_lyrics` 的 ``cache_limit``），不传则用模块默认。
    """
    try:
        entries = [p for p in cache_dir().glob("*.json") if p.is_file()]
        if len(entries) <= limit:
            return
        entries.sort(key=lambda p: p.stat().st_mtime)
        for stale in entries[: len(entries) - limit]:
            try:
                stale.unlink()
            except OSError:
                continue
    except Exception:
        log.debug("清理歌词缓存失败", exc_info=True)


# ---------------------------------------------------------------- 网络

# 「当前进程歌词请求走哪条路」每个进程只记一行（见 _note_route_once）。
_route_logged = False

# 直连失败时**允许**换到系统代理的 HTTP 状态：500 / 502 / 504。这三个是"这条
# 路本身出问题"的典型状态（服务端内部错误 / 上游网关错误 / 网关超时），换个出口
# 可能就通了。
# 其余状态一律不换路：401/403 是权限与风控、404 是不存在、429/503 是限流，
# 换条路重打只是把同一个答复再触发一次（也违背服务端 Retry-After 的请求）。
_FALLBACK_STATUSES = frozenset({500, 502, 504})

# 一次尝试的最小剩余预算（秒）。比这还少就不再发请求：注定来不及的那发只会
# 白占一个连接，不会带回任何结果。
_MIN_ATTEMPT_BUDGET = 0.25


def _host_of(url: str) -> str:
    """取 URL 的主机名，供日志定位（解析失败时回退成原串）。"""
    try:
        return urllib.parse.urlsplit(url).hostname or url
    except Exception:
        return url


def _build_opener():
    """歌词请求的**直连** opener（首选路线，见 :func:`_http_get_json`）。

    为什么首选直连、不用 ``urllib.request.urlopen``：后者会自动套用系统代理
    （Windows 注册表 / 环境变量，``urllib.request.getproxies()``）。用户开着
    全局模式 VPN 时，本机实测三个源单次耗时变成 41.3 / 22.1 / 20.4 秒，全部
    超过 ``HTTP_TIMEOUT``（8 秒），于是 ``fetch_lyrics`` 每次都在 9 秒死线处
    放弃、返回「0 行」——表现就是"歌词功能坏了"（2026-09-22 实机事故）。

    直连这三个公开接口实测 0.25~3.5 秒，与代理是否开启无关，行为可预期。

    构造开销实测 122us/次，故每次请求现建、不缓存：用户中途开关代理不会
    带出陈旧状态，也免掉跨线程共享 opener 的同步问题。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _system_proxies() -> dict:
    """本机的系统代理配置；``{}`` 表示没读到任何配置。

    只读注册表 / 环境变量，不发请求。返回值**可能带凭据**（``user:pass@host``），
    因此一律不进日志——日志里只写"直连 / 系统代理"这种路线名（见 :func:`_note_route_once`）。

    注意返回值里有**不是代理**的键（``no`` 是绕过名单），所以判断"有没有第二条路"
    要用 :func:`_fallback_proxy`，不要直接看这个字典非空。
    """
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return {}
    if not proxies:
        return {}
    return {str(key): str(value) for key, value in proxies.items() if value}


_PROXY_SCHEMES = ("http", "https")


def _configured_proxies(proxies: dict | None = None) -> dict:
    """系统配置里**能当代理用**的条目：``http`` / ``https``。

    ``getproxies()`` 还可能出现 ``no``（绕过名单，见 ``NO_PROXY``）与 ``all``/``ftp``
    这类条目，它们对本模块的 https 目标都不是代理——``ProxyHandler`` 按 URL 的 scheme
    装 ``*_open`` 处理器，没装到的协议就等于直连。

    ``proxies`` 可传入已读到的配置，避免失败路径上重复读注册表。
    """
    source = _system_proxies() if proxies is None else proxies
    return {
        str(key).lower(): str(value)
        for key, value in source.items()
        if str(key).lower() in _PROXY_SCHEMES and value
    }


def _fallback_proxy(url: str, proxies: dict | None = None) -> str | None:
    """这个 URL 走系统代理时**真正会用到的**代理；没有就返回 ``None``。

    判据全部沿用标准库语义（``ProxyHandler`` 怎么分派、``urllib.request.proxy_bypass``
    怎么判），不自建规则：

    1. 只有本 URL 实际协议（http/https）**有代理**才算有第二条路——``no`` 是绕过名单、
       ``ftp``/``all`` 对本 URL 无效（见 :func:`_configured_proxies`）。
    2. 主机命中 ``urllib.request.proxy_bypass`` 时，``ProxyHandler.proxy_open`` 会
       直接放行成直连——那不是"回退系统代理"，再打一次只是重复直连，不能算、也不能
       在日志里谎称成代理。
    """
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in _PROXY_SCHEMES:
        return None
    proxy = _configured_proxies(proxies).get(scheme)
    if not proxy:
        return None
    host = parts.hostname
    if host:
        try:
            if urllib.request.proxy_bypass(host):
                return None
        except Exception:
            return None
    return str(proxy)


def _note_route_once() -> None:
    """本机配了 http/https 代理时，每个进程记一行 INFO 说明路线策略。

    排查这类"听起来像功能坏了"的问题，第一眼就该看出网络走了哪条路：
    没这行 = 本机没有可用的系统代理配置，歌词请求只有直连一条路。
    **不回显代理地址/凭据**：日志常被整段贴进 issue 与报告，那是用户环境信息。
    """
    global _route_logged
    if _route_logged:
        return
    _route_logged = True
    if _configured_proxies():
        log.info(
            "歌词请求：检测到系统代理，首选直连；仅在直连传输失败或 HTTP 500/502/504 时回退系统代理一次"
        )


def _open_direct(request: urllib.request.Request, *, timeout: float):
    """用直连 opener 发请求（见 :func:`_build_opener`）。"""
    _note_route_once()
    return _build_opener().open(request, timeout=timeout)


def _open_via_system_proxy(request: urllib.request.Request, *, timeout: float, proxies: dict):
    """回退路线：跟随系统代理（``ProxyHandler(proxies)``，见 :func:`_http_get_json`）。"""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies)
    ).open(request, timeout=timeout)


def _may_switch_to_proxy(exc: BaseException) -> bool:
    """直连这次的失败，是否**值得**换到系统代理再试一次。

    只认两类：① **传输失败**（连不上、读超时、连接被重置、协议层坏掉——根本没
    拿到可用响应）；② HTTP **500 / 502 / 504**。其它 HTTP 状态是对方的明确答复
    （401/403 权限、404 不存在、429/503 限流），换条路重打只是再挨一次。

    另外：**只要响应带了 ``Retry-After`` 就不换路**——服务端明确要求"等一会儿再
    打"，立刻从另一条路再打同样违背该请求（三种取值形态都算：整数秒、HTTP 日期、
    连内容都解析不了的）。历史上这里只看状态码，日志却写着"不换路、不重试"，
    等于把 500/502/504 + Retry-After 的响应也换路重打了一遍。

    2026-10-16 实机事实正是①的极端形态：同一个 URL、同一分钟，直连恒 500 而代理
    200（`.scratch/soda-lyrics/REPORT.md` §4），单一固定路线让这类曲目 100% 0 行。
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code not in _FALLBACK_STATUSES:
            return False
        return _retry_after(exc) is None
    # URLError / TimeoutError / 连接重置都是 OSError 的子类；BadStatusLine 这类
    # 协议层异常走 HTTPException（不是 OSError），同属"没拿到可用响应"。
    return isinstance(exc, (OSError, http.client.HTTPException))


# ``Retry-After`` 头存在但值为空时，日志里用这个占位（"存在"就要阻止换路）。
_RETRY_AFTER_BLANK = "(空值)"


def _retry_after(exc: BaseException) -> str | None:
    """响应头里的 ``Retry-After``，规范化成可安全写日志的短串；没有就是 ``None``。

    三种形态都算**存在**（整数秒、HTTP 日期、以及解析不了的内容）——只要服务端给了
    这个头就是在要求等待，本模块据此**不换路、也不重试**（见
    :func:`_may_switch_to_proxy`）。值只留可打印字符并截断，避免把响应头里的
    任意内容原样写进日志。
    """
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    cleaned = "".join(ch for ch in str(raw).strip() if ch.isprintable())[:40]
    return cleaned or _RETRY_AFTER_BLANK


def _failure_detail(exc: BaseException) -> str:
    """失败原因的可安全落盘形态：异常类型 + HTTP 状态 + Retry-After。

    **不写异常文本**（``str(exc)``）：``URLError``/``InvalidURL`` 的消息里可能带
    内部代理地址甚至凭据（``<urlopen error ... proxy.internal:8080>``），而日志经常
    被整段外贴；异常类型 + 状态码已经够定位，需要细节时再看探针。
    """
    detail = type(exc).__name__
    status = getattr(exc, "code", None)
    if isinstance(status, int):
        detail += f" HTTP {status}"
    retry_after = _retry_after(exc)
    if retry_after is not None:
        detail += f"（服务端 Retry-After: {retry_after}，按该响应不换路、不重试）"
    return detail


def _attempt_timeout(deadline: float, *, now: float | None = None) -> float | None:
    """本次尝试可用的超时（秒）；剩余预算不足时返回 ``None``（不要发请求）。

    ``now`` 显式给出"此刻"，好让**首次**尝试用与预算同一时刻的读数（否则同一
    份预算下首次尝试会拿到 ``7.999…`` 而不是 ``HTTP_TIMEOUT`` 本身）。
    """
    left = deadline - (time.monotonic() if now is None else now)
    if left < _MIN_ATTEMPT_BUDGET:
        return None
    return min(HTTP_TIMEOUT, left)


def _decode_json_body(raw: bytes) -> dict | list:
    """响应字节 → JSON（部分接口返回 JSONP，剥掉外层包裹后再解析）。"""
    text = raw.decode("utf-8", "replace").strip()
    if not text.startswith(("{", "[")):
        start = text.find("(")
        end = text.rfind(")")
        if start != -1 and end > start:
            text = text[start + 1:end]
    return json.loads(text)


def _warn_request_failed(url: str, started: float, detail: str) -> None:
    """请求失败一律落在 INFO 可见的级别。

    取词失败时日志里只有「0 行」会被误当成"功能坏了"，这里把主机、耗时与
    异常类型留下（含超时 / 连接被拒 / 两条路各自的结局）。
    """
    log.warning("歌词请求失败 %s（%.2fs）: %s",
                _host_of(url), time.monotonic() - started, detail)


def _http_get_json(
    url: str, *, referer: str | None = None, deadline: float | None = None
) -> dict | list | None:
    """GET 一个 JSON 接口并解析；失败返回 ``None``（调用方按"这个源没命中"处理）。

    **路线**：首选直连（:func:`_build_opener`，2026-09-22 实测代理会把三源拖到
    20~41 秒）。直连失败且**同时**满足下列三条时，**回退一次**系统代理：

    1. 这个 URL 真的有一条代理路可走（:func:`_fallback_proxy`：本 URL 协议配了代理、
       且没命中系统代理绕过表——否则"回退"只是把同一条直连路再打一遍）；
    2. 失败属于传输失败或 HTTP 500/502/504，且响应没带 ``Retry-After``
       （见 :func:`_may_switch_to_proxy`）；
    3. 还剩够用的预算（见 :func:`_attempt_timeout`）。

    只回退一次、不做周期重试，也不在这里改任何全局 HTTP 行为——其它功能照旧
    各自跟随或绕开系统代理。日志只记异常类型 / HTTP 状态 / ``Retry-After``，
    不写异常文本（可能夹带内部代理地址或凭据）。

    ``deadline`` 是本次取词的**绝对**截止时刻（``time.monotonic()`` 口径），由
    :func:`fetch_lyrics` 算好后逐层透传：直连与回退**共享同一份预算**，各自只拿到
    剩余部分，因此一次请求（含回退）不会把 ``HTTP_TIMEOUT`` 翻倍成 2 倍超时。
    不传则本次请求自带一份 ``HTTP_TIMEOUT`` 预算（单测 / 直接调用口径）。
    """
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    started = time.monotonic()
    if deadline is None:
        deadline = started + HTTP_TIMEOUT

    timeout = _attempt_timeout(deadline, now=started)
    if timeout is None:
        log.warning("歌词请求跳过 %s：剩余预算不足 %.2fs，未发起请求",
                    _host_of(url), max(0.0, deadline - started))
        return None

    raw: bytes | None = None
    direct_error: BaseException | None = None
    try:
        with _open_direct(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:
        direct_error = exc

    if direct_error is not None:
        detail = f"直连 {_failure_detail(direct_error)}"
        proxies = _system_proxies()
        if (
            not _fallback_proxy(url, proxies)
            or not _may_switch_to_proxy(direct_error)
        ):
            _warn_request_failed(url, started, detail)
            return None
        remaining = deadline - time.monotonic()
        if remaining < _MIN_ATTEMPT_BUDGET:
            _warn_request_failed(
                url, started,
                f"{detail}；剩余预算 {max(0.0, remaining):.2f}s 不足以回退系统代理",
            )
            return None
        try:
            with _open_via_system_proxy(
                request, timeout=min(HTTP_TIMEOUT, remaining), proxies=proxies
            ) as response:
                raw = response.read()
        except Exception as proxy_error:
            _warn_request_failed(
                url, started,
                f"{detail}；系统代理 {_failure_detail(proxy_error)}",
            )
            return None
        log.info("歌词请求回退系统代理成功 %s（%.2fs）",
                 _host_of(url), time.monotonic() - started)

    try:
        return _decode_json_body(raw)
    except Exception as exc:
        _warn_request_failed(url, started, f"响应解析失败 {type(exc).__name__}")
        return None


# 多歌手分隔符（酷狗报「、」、歌词源多写「/」，feat./ft. 见英文曲库）：
# 两侧都是多歌手形态时归一逐段比对，否则整串子串必然丢词。
# feat/ft 必须带单词边界（\b），且切分发生在**去空白之前**——否则 swift/craft/
# daft punk 这类含 ft/feat 字母序列的单名会被拆碎（盲审 P1）。
_ARTIST_SEP_RE = re.compile(r"\s*(?:[、/／&×,，;；]|\bfeat\b\.?|\bft\b\.?)\s*", re.IGNORECASE)


def _name_matches(candidate: str, wanted: str) -> bool:
    """歌手名匹配：大小写不敏感，允许子串（"周杰伦" 命中 "周杰伦/Jay Chou"）；
    多歌手分隔符（、/／&×,; feat./ft.）在**两侧都出现**时归一逐段比对——
    实机样本：酷狗 SMTC 报「FLOW、GRANRODEO」而网易云歌词源写「FLOW/GRANRODEO」。"""
    a = re.sub(r"\s+", "", str(candidate or "").lower())
    b = re.sub(r"\s+", "", str(wanted or "").lower())
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # 在保留空白的小写串上切分（feat/ft 的词边界要靠空白/标点判定），切完去空白
    raw_a = [re.sub(r"\s+", "", p) for p in _ARTIST_SEP_RE.split(str(candidate or "").lower())]
    raw_b = [re.sub(r"\s+", "", p) for p in _ARTIST_SEP_RE.split(str(wanted or "").lower())]
    raw_a = [p for p in raw_a if p]
    raw_b = [p for p in raw_b if p]
    # 仅两侧均呈多歌手形态时增加逐段匹配
    if len(raw_a) < 2 or len(raw_b) < 2:
        return False
    # 逐段**相等**比对（去空白后）：子串比对会让 2-3 字符碎片几乎什么都命中
    # （盲审 P2："Craft/Lift" 的 "cra" 会被 "Soft、Raft" 的 "raf" 碎片带上）。
    return any(pa == pb for pa in raw_a for pb in raw_b)


def _as_lyrics(lines: list[LyricLine]) -> Lyrics | None:
    """把解析出的行包成 :class:`Lyrics`；空结果视为"这个源没命中"。"""
    if not lines:
        return None
    if _looks_instrumental(lines):
        return Lyrics(instrumental=True)
    return Lyrics(lines=tuple(lines))


# 各平台对"纯音乐"会给一句**占位文案**当歌词（不是空），必须识别出来，
# 否则会被当成唯一的歌词行显示、还会放起唱歌动画。
#
# 只匹配**整句占位语**，不匹配裸关键词：真歌词里完全可能出现"纯音乐"三个字
# （如「这首纯音乐真好听」），用关键词会把它们误判成纯音乐。
# 实测样本：QQ音乐 '此歌曲为没有填词的纯音乐，请您欣赏' / '纯音乐，请欣赏'。
_INSTRUMENTAL_PATTERNS = (
    "纯音乐，请欣赏",
    "纯音乐,请欣赏",
    "没有填词的纯音乐",
    "此歌曲为没有填词",
    "该歌曲为纯音乐",
    "暂无歌词",
    "无歌词",
    "instrumental",
    "no lyrics",
)


def _looks_instrumental(lines: list[LyricLine]) -> bool:
    """判断这些"歌词"是否只是纯音乐占位文案。

    仅在**整首**都像占位时才判定：真实歌曲里的某一行偶然提到"纯音乐"
    不该被误判成纯音乐。
    """
    texts = [str(line.text or "").strip().lower() for line in lines]
    texts = [t for t in texts if t]
    if not texts:
        return False
    # 占位文案只有一两句；真歌词不会这么少还全部命中关键词。
    if len(texts) > 3:
        return False
    return all(
        any(pattern in text for pattern in _INSTRUMENTAL_PATTERNS) for text in texts
    )


def _fetch_from_qq(title: str, artist: str, *, deadline: float | None = None) -> Lyrics | None:
    """QQ音乐：中文曲库匹配质量最好。"""
    query = urllib.parse.quote(f"{title} {artist}".strip())
    search = _http_get_json(
        "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
        f"?w={query}&format=json&n=10&p=1&cr=1&aggr=1",
        referer="https://y.qq.com/",
        deadline=deadline,
    )
    if not isinstance(search, dict):
        return None
    songs = ((search.get("data") or {}).get("song") or {}).get("list") or []
    songmid = None
    for song in songs:
        singers = "/".join(
            str(s.get("name") or "") for s in (song.get("singer") or [])
        )
        if _name_matches(singers, artist):
            songmid = song.get("songmid")
            break
    if not songmid:
        return None
    payload = _http_get_json(
        "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
        f"?songmid={songmid}&format=json&nobase64=1&g_tk=5381",
        referer="https://y.qq.com/",
        deadline=deadline,
    )
    if not isinstance(payload, dict):
        return None
    return _as_lyrics(parse_lrc(str(payload.get("lyric") or "")))


def _fetch_from_lrclib(title: str, artist: str, *, deadline: float | None = None) -> Lyrics | None:
    """lrclib：国际/日文曲目覆盖好，也是唯一会给「纯音乐」标记的源。"""
    params = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    payload = _http_get_json(f"https://lrclib.net/api/get?{params}", deadline=deadline)
    if not isinstance(payload, dict):
        return None
    synced = payload.get("syncedLyrics")
    if not synced:
        # 曲目存在但没词：lrclib 会显式给出 instrumental=True（配乐/OST）。
        if payload.get("instrumental"):
            return Lyrics(instrumental=True)
        return None
    return Lyrics(lines=tuple(parse_lrc(str(synced))))


def _fetch_from_netease(title: str, artist: str, *, deadline: float | None = None) -> Lyrics | None:
    """网易云：明文接口，作为最后兜底。"""
    query = urllib.parse.quote(f"{title} {artist}".strip())
    search = _http_get_json(
        "https://music.163.com/api/cloudsearch/pc"
        f"?s={query}&type=1&offset=0&limit=10",
        referer="https://music.163.com/",
        deadline=deadline,
    )
    if not isinstance(search, dict):
        return None
    songs = ((search.get("result") or {}).get("songs")) or []
    song_id = None
    for song in songs:
        # 歌手字段兼容：接口现在返回 ``ar``（旧字段 ``artists`` 已下线——
        # 2026-10 实测返回体里没有 ``artists``，读旧字段会让歌手恒为空串、
        # 本源 100% 静默失效；先 ar 后 artists 向前向后都兼容）。
        artists_field = song.get("ar") or song.get("artists") or []
        singers = "/".join(
            str(a.get("name") or "") for a in artists_field
        )
        if _name_matches(singers, artist):
            song_id = song.get("id")
            break
    if not song_id:
        if songs:
            # 「搜得到但无人匹配」是配置/字段问题的信号，不能继续静默——
            # 上次就是字段改名让本源死了一个月而日志里一条都没有
            log.warning(
                "网易云歌词：搜索到 %d 个候选但无人匹配歌手 %r（title=%r）",
                len(songs), artist, title)
        return None
    payload = _http_get_json(
        f"https://music.163.com/api/song/lyric?id={song_id}&lv=-1&kv=-1&tv=-1",
        referer="https://music.163.com/",
        deadline=deadline,
    )
    if not isinstance(payload, dict):
        return None
    return _as_lyrics(parse_lrc(str((payload.get("lrc") or {}).get("lyric") or "")))


# 按匹配质量排序（靠前优先）。三源**并发**发起，避免串行等待把几次网络
# 往返叠加成十几秒——实测 QQ 音乐单次就要 4~6 秒，串行时"QQ没命中 + 换
# lrclib"会直接翻倍。
_SOURCES = (
    ("qq", _fetch_from_qq),
    ("lrclib", _fetch_from_lrclib),
    ("netease", _fetch_from_netease),
)

# 高优先级源的优势窗口（秒）：这段时间内若靠前的源返回就采用它，保证匹配
# 质量；过期后接受任何已成功的结果，不让慢源拖住整次取词。
_PRIORITY_GRACE = 1.2


def fetch_lyrics(
    title: str, artist: str, *, use_cache: bool = True, cache_limit: int | None = None
) -> Lyrics | None:
    """取歌词：缓存 → 三源并发（QQ音乐 → lrclib → 网易云，按质量优先）。

    三个源同时发起请求，因此总耗时约等于**最慢的那个**而不是三者之和。
    靠前的源在优势窗口内返回即采用，保证匹配质量；窗口过期后接受任何已成功的
    结果，不让慢源拖住整次取词。

    ``cache_limit`` 是磁盘缓存条目上限（生产路径传配置的
    ``music_lyric_cache_limit``，见 ``music_lyric_controller``）；``None`` 用
    模块默认 :data:`CACHE_LIMIT`。调用方负责夹到合法区间。

    三个源共享**同一份**截止预算（``HTTP_TIMEOUT + 1.0``）：直连与可能的一次
    路线回退都从这份预算里扣，因此整次取词的用户可见延迟与改动前一致，不会
    因为多了一条路而翻倍。

    返回 :class:`Lyrics`；``instrumental=True`` 表示纯音乐（曲目存在但无词），
    调用方据此不显示歌词、也不播唱歌动画。全部失败时返回 ``None``，调用方应
    据此显示「暂无歌词」**并停止对该曲重试**，避免反复请求。
    """
    title = str(title or "").strip()
    artist = str(artist or "").strip()
    if not title and not artist:
        return None

    if use_cache:
        cached = _read_cache(title, artist)
        if cached:
            return cached

    executor = ThreadPoolExecutor(
        max_workers=len(_SOURCES), thread_name_prefix="lyric-fetch"
    )
    try:
        deadline = time.monotonic() + HTTP_TIMEOUT + 1.0
        # 把 future 与源名配成对，避免用 id() 反查这种脆弱做法。
        submitted = [
            (name, executor.submit(fetcher, title, artist, deadline=deadline))
            for name, fetcher in _SOURCES
        ]
        rank = {name: index for index, (name, _) in enumerate(_SOURCES)}
        found: dict[str, Lyrics] = {}
        grace_until = time.monotonic() + _PRIORITY_GRACE

        while submitted:
            now = time.monotonic()
            if now >= deadline:
                break
            horizon = grace_until if now < grace_until else deadline
            _, not_done = wait(
                [future for _, future in submitted],
                timeout=max(0.05, horizon - now),
                return_when=FIRST_COMPLETED,
            )
            still: list[tuple[str, object]] = []
            for name, future in submitted:
                if future in not_done:
                    still.append((name, future))
                    continue
                try:
                    lines = future.result()
                except Exception:
                    log.debug("歌词源 %s 异常", name, exc_info=True)
                    continue
                if lines is not None:
                    # 注意：空歌词但带 instrumental 标记也是有效结果，
                    # 不能用真值判断（Lyrics.__bool__ 只看 lines）。
                    found[name] = lines
            submitted = still

            # 优势窗口内只要靠前的源成功就别再等；窗口过后任何结果都收。
            if found and time.monotonic() >= grace_until:
                break

        best = _best_found(found, rank)
        if best is not None:
            _write_cache(title, artist, best, cache_limit)
        return best
    finally:
        # 不等剩余请求收尾：已经拿到结果，慢源在后台自然结束即可。
        executor.shutdown(wait=False)


def _best_found(
    found: dict[str, Lyrics], rank: dict[str, int]
) -> Lyrics | None:
    """在已成功的源里挑优先级最高的那个（rank 越小越优先）。"""
    if not found:
        return None
    name = min(found, key=lambda key: rank.get(key, len(rank)))
    return found[name]
