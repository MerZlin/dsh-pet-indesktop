"""市场索引与 artifact 的唯一 HTTP 实现。

架构约束：联网库只允许出现在 ``net/``。``market/`` 通过本模块注入的
``IndexFetcher`` / ``ArtifactFetcher`` 协议使用它，不直接 import urllib。
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..contracts.archive import BudgetExceeded
from ..net import errors
from ..net import http
from ..net import ranged
from ..market.errors import TransferCancelled
from ..market.source import IndexFetch, IndexFetchError

MAX_INDEX_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
USER_AGENT = f"dsh-pet-core/{__version__}"

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


def _decode_github_contents(body: bytes) -> bytes:
    try:
        raw: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return body
    if not isinstance(raw, dict):
        return body
    if raw.get("encoding") != "base64" or not isinstance(raw.get("content"), str):
        return body
    try:
        return base64.b64decode(raw["content"])
    except (ValueError, TypeError):
        return body


def _header(response, name: str) -> str:
    try:
        value = response.headers.get(name)
    except AttributeError:
        return ""
    return str(value or "")


def fetch_index(
    url: str,
    *,
    etag: str = "",
    timeout: float = 8.0,
    opener=None,
    cancel: CancelCallback | None = None,
) -> IndexFetch:
    """读取索引；304 返回 ``not_modified``，任何传输失败抛 ``IndexFetchError``。"""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(str(url), headers=headers)
    try:
        response = http.urlopen(request, timeout=timeout, opener=opener, pollute=False)
    except urllib.error.HTTPError as exc:
        if int(getattr(exc, "code", 0) or 0) == 304:
            return IndexFetch(body=b"", not_modified=True)
        raise IndexFetchError(f"HTTP {exc.code}", str(url)) from exc
    except Exception as exc:  # noqa: BLE001 - 稳定分类由 IndexFetchError 承担
        raise IndexFetchError(type(exc).__name__, str(url)) from exc

    try:
        chunks: list[bytes] = []
        total = 0
        with response:
            while True:
                if cancel is not None and cancel():
                    raise TransferCancelled("index fetch cancelled")
                block = response.read(min(CHUNK_BYTES, MAX_INDEX_BYTES + 1 - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
                if total > MAX_INDEX_BYTES:
                    break
            body = b"".join(chunks)
            new_etag = _header(response, "ETag")
    except TransferCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IndexFetchError(f"读取失败：{type(exc).__name__}", str(url)) from exc
    if len(body) > MAX_INDEX_BYTES:
        raise IndexFetchError(f"索引超过 {MAX_INDEX_BYTES} 字节上限", str(url))
    return IndexFetch(body=_decode_github_contents(body), etag=new_etag)


def fetch_index_file(url: str, *, cancel: CancelCallback | None = None) -> IndexFetch:
    """读取本地 `file://` 索引（离线验收/本地镜像）；不联网、不写缓存。"""
    try:
        parts = urllib.parse.urlsplit(str(url))
        path = Path(urllib.request.url2pathname(parts.path))
    except (ValueError, OSError) as exc:
        raise IndexFetchError(f"本地索引地址无效：{exc}", str(url)) from exc
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise IndexFetchError(f"读取失败：{type(exc).__name__}", str(url)) from exc
    if size > MAX_INDEX_BYTES:
        raise IndexFetchError(f"索引超过 {MAX_INDEX_BYTES} 字节上限", str(url))
    try:
        chunks: list[bytes] = []
        with path.open("rb") as stream:
            while True:
                if cancel is not None and cancel():
                    raise TransferCancelled("index fetch cancelled")
                block = stream.read(CHUNK_BYTES)
                if not block:
                    break
                chunks.append(block)
        body = b"".join(chunks)
    except TransferCancelled:
        raise
    except OSError as exc:
        raise IndexFetchError(f"读取失败：{type(exc).__name__}", str(url)) from exc
    return IndexFetch(body=body, etag=f"file:{size}:{int(path.stat().st_mtime)}")


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    """市场下载的逐跳策略：跳数上限、协议白名单与跨主机白名单（T02）。

    ``allowed_hosts`` 为空时只允许同主机跳转；GitHub Release 这类
    ``github.com → objects.githubusercontent.com`` 必须由调用方显式列出目标主机。
    ``download_to`` 不传 ``policy`` 时保持旧语义（由 urllib 自动跟随跳转）；
    ``fetch_artifact`` 一律传入由 :class:`~petcore.contracts.http.TransferSpec`
    转换出的策略。
    """

    max_redirects: int = 3
    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_redirects < 0:
            raise ValueError("max_redirects must be >= 0")
        if not self.allowed_schemes:
            raise ValueError("allowed_schemes must not be empty")


@dataclass(frozen=True, slots=True)
class DownloadOutcome:
    """一次下载的可核对结果；``resumed_from`` 只在真正续传时非零。"""

    url: str
    bytes_written: int
    resumed_from: int = 0
    redirects: int = 0


_CONTENT_RANGE_RE = re.compile(r"\Abytes\s+(\d+)-(\d+)/(\d+|\*)\Z", re.IGNORECASE)
_UNSATISFIED_RANGE_RE = re.compile(r"\Abytes\s+\*/(\d+)\Z", re.IGNORECASE)


def _parse_content_range(value: str) -> tuple[int | None, int | None, int | None]:
    """解析单段 ``Content-Range``；畸形、多段或越界返回 ``(None, None, None)``。"""
    match = _CONTENT_RANGE_RE.match(str(value or "").strip())
    if match is None:
        return (None, None, None)
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    if end < start or (total is not None and (total <= 0 or end >= total)):
        return (None, None, None)
    return (start, end, total)


def _parse_unsatisfied_total(value: str) -> int | None:
    match = _UNSATISFIED_RANGE_RE.match(str(value or "").strip())
    return int(match.group(1)) if match is not None else None


def _url_parts(url: str):
    parts = urllib.parse.urlsplit(str(url))
    if not parts.scheme:
        raise errors.NetworkError("URL 缺少协议", reason=errors.PROTOCOL)
    return parts


def _require_host(parts) -> None:
    if not parts.hostname:
        raise errors.NetworkError("URL 缺少主机名", reason=errors.PROTOCOL)
    if parts.username is not None or parts.password is not None:
        raise errors.NetworkError("URL 不允许包含用户凭据", reason=errors.PROTOCOL)


def _validate_initial(url: str, policy: TransportPolicy) -> None:
    parts = _url_parts(url)
    allowed = {str(item).lower() for item in policy.allowed_schemes}
    if parts.scheme.lower() not in allowed:
        raise errors.NetworkError(
            f"不支持的协议：{parts.scheme.lower()}", reason=errors.PROTOCOL
        )
    _require_host(parts)


def validate_redirect(
    current_url: str,
    target_url: str,
    policy: TransportPolicy,
    redirects: int,
) -> None:
    """校验第 ``redirects`` 次跳转。

    拒绝：超过 ``policy.max_redirects``、目标协议不在白名单、``https`` 降级到
    其它协议、以及跨主机跳转（主机名必须与当前/初始 URL 完全相同）。
    """
    if redirects > policy.max_redirects:
        raise errors.NetworkError(
            f"跳转超过 {policy.max_redirects} 次", reason=errors.PROTOCOL
        )
    target = _url_parts(target_url)
    allowed = {str(item).lower() for item in policy.allowed_schemes}
    scheme = target.scheme.lower()
    if scheme not in allowed:
        raise errors.NetworkError(
            f"跳转目标协议不被允许：{scheme}", reason=errors.PROTOCOL
        )
    _require_host(target)
    current = urllib.parse.urlsplit(str(current_url))
    if current.scheme.lower() == "https" and scheme != "https":
        raise errors.NetworkError("不允许从 https 降级", reason=errors.PROTOCOL)
    current_host = (current.hostname or "").lower()
    target_host = (target.hostname or "").lower()
    allowed_hosts = {str(host).strip().lower() for host in policy.allowed_hosts}
    if target_host != current_host and target_host not in allowed_hosts:
        raise errors.NetworkError(
            f"跨主机跳转被拒绝：{target_host or '(无主机)'}", reason=errors.PROTOCOL
        )


class _RedirectBlocked(urllib.error.HTTPError):
    """把 3xx 变成带目标地址的异常，由下载循环决定是否继续。"""

    def __init__(self, url: str, code: int, msg: str, headers, target: str):
        super().__init__(url, int(code), str(msg), headers, None)
        self.target = str(target or "")


class _BlockRedirects(urllib.request.HTTPRedirectHandler):
    """禁止 urllib 自动跟随；每个 3xx 响应先关闭再抛出。"""

    def _block(self, req, fp, code, msg, headers):
        target = ""
        try:
            if headers is not None:
                target = str(headers.get("Location") or "")
        except (AttributeError, TypeError):
            target = ""
        try:
            fp.close()
        except Exception:  # noqa: BLE001 - 关闭失败不改变拒绝结论
            pass
        raise _RedirectBlocked(req.full_url, code, msg, headers, target)

    http_error_301 = _block
    http_error_302 = _block
    http_error_303 = _block
    http_error_307 = _block
    http_error_308 = _block


def _redirect_blocking_opener(use_proxy: bool, context=None):
    handlers: list[Any] = []
    if not use_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(_BlockRedirects())
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def _close_quietly(resource) -> None:
    try:
        resource.close()
    except Exception:  # noqa: BLE001 - 关闭不得掩盖原始失败
        pass


def _discard_part(part_path: Path) -> None:
    try:
        part_path.unlink(missing_ok=True)
    except OSError:
        pass


def _translate_transport_error(exc: BaseException) -> errors.NetworkError:
    if isinstance(exc, errors.NetworkError):
        return exc
    if errors.classify_exception(exc) == errors.TIMEOUT:
        return errors.NetworkTimeout("连接超时")
    return errors.NetworkUnreachable(type(exc).__name__)


def _unsatisfied_is_complete(exc: urllib.error.HTTPError, existing: int, expected_size: int | None) -> bool:
    """416 只有在本地长度已等于预期、或 ``bytes */N`` 的 N 等于本地长度时算完成。"""
    try:
        header = _header(exc, "Content-Range")
    finally:
        _close_quietly(exc)
    if expected_size is not None and existing == expected_size:
        return True
    total = _parse_unsatisfied_total(header)
    return total is not None and total == existing


def _expected_total(response, status: int, done: int, cr_total: int | None, expected_size: int | None) -> int:
    total = int(expected_size or 0)
    if total:
        return total
    if cr_total is not None:
        return cr_total
    length = _header(response, "Content-Length")
    try:
        return done + int(length) if length else 0
    except ValueError:
        return 0


def download_to(
    url: str,
    part_path: Path,
    *,
    expected_size: int | None = None,
    timeout: float = 15.0,
    chunk_size: int = CHUNK_BYTES,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    opener=None,
    policy: TransportPolicy | None = None,
    max_bytes: int | None = None,
) -> int:
    """下载到 ``.part``；已有内容时按 206 区间续传。返回最终字节数。

    续传只在服务端 ``Content-Range`` 起点等于本地 ``.part`` 长度时追加；区间
    缺失、畸形或起点不符时丢弃 ``.part`` 并从 0 重下一次。416 只有在本地长度
    等于 ``expected_size``、或 ``Content-Range: bytes */N`` 的 N 等于本地长度
    时才算完成，否则删除 ``.part`` 重下一次（最多重试一次）。

    ``max_bytes`` 按**实际读到**的字节累计（含已有 ``.part``），越界立即抛
    :class:`~petcore.contracts.archive.BudgetExceeded` 并删除 ``.part``。

    取消时 ``.part`` **保留**（已写字节可用于下次续传），不截断、不删除。
    """
    return download_with_outcome(
        url,
        part_path,
        expected_size=expected_size,
        timeout=timeout,
        chunk_size=chunk_size,
        progress=progress,
        cancel=cancel,
        opener=opener,
        policy=policy,
        max_bytes=max_bytes,
    ).bytes_written


def download_with_outcome(
    url: str,
    part_path: Path,
    *,
    expected_size: int | None = None,
    timeout: float = 15.0,
    chunk_size: int = CHUNK_BYTES,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    opener=None,
    policy: TransportPolicy | None = None,
    max_bytes: int | None = None,
) -> DownloadOutcome:
    """:func:`download_to` 的完整结果版本（跳转数与真实续传偏移）。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    part_path = Path(part_path)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    existing = part_path.stat().st_size if part_path.is_file() else 0
    if max_bytes is not None and existing > max_bytes:
        _discard_part(part_path)
        raise BudgetExceeded("compressed", max_bytes, existing)

    current_url = str(url)
    if policy is not None:
        _validate_initial(current_url, policy)
    safe_opener = _redirect_blocking_opener if policy is not None else opener
    redirects = 0
    restarted = False

    while True:
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(current_url, headers=headers)
        try:
            response = http.urlopen(
                request, timeout=timeout, opener=safe_opener, pollute=False
            )
        except _RedirectBlocked as exc:
            _close_quietly(exc)  # 3xx 响应体已关闭；这里只清掉异常持有的引用
            if policy is None:
                raise errors.NetworkError(
                    "跳转未被处理", reason=errors.PROTOCOL
                ) from exc
            target = urllib.parse.urljoin(current_url, exc.target) if exc.target else ""
            redirects += 1
            if not target:
                raise errors.NetworkError("跳转缺少 Location", reason=errors.PROTOCOL) from exc
            validate_redirect(current_url, target, policy, redirects)
            current_url = target
            continue
        except urllib.error.HTTPError as exc:
            if int(getattr(exc, "code", 0) or 0) == 416 and existing > 0:
                if _unsatisfied_is_complete(exc, existing, expected_size):
                    return DownloadOutcome(
                        url=current_url,
                        bytes_written=existing,
                        resumed_from=existing,
                        redirects=redirects,
                    )
                if not restarted:
                    restarted = True
                    _discard_part(part_path)
                    existing = 0
                    continue
                raise errors.HttpStatusError(416) from None
            raise errors.http_error(exc) from exc
        except errors.NetworkError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate_transport_error(exc) from exc

        status = int(getattr(response, "status", 200) or 200)
        try:
            if 300 <= status < 400:
                raise errors.NetworkError(
                    f"未处理的跳转状态：{status}", reason=errors.PROTOCOL
                )
            mode = "wb"
            done = 0
            resumed_from = 0
            cr_total: int | None = None
            restart_needed = False
            if status == 206:
                start, end, total = _parse_content_range(_header(response, "Content-Range"))
                if existing > 0:
                    if (
                        start is None
                        or start != existing
                        or (end is not None and end < start)
                        or (expected_size is not None and total is not None and total != expected_size)
                        or (expected_size is not None and end is not None and end + 1 > expected_size)
                    ):
                        restart_needed = True
                    else:
                        mode = "ab"
                        done = existing
                        resumed_from = existing
                        cr_total = total
                elif start != 0:
                    restart_needed = True
                else:
                    cr_total = total
            if restart_needed and restarted:
                raise errors.NetworkError(
                    "服务端续传区间不匹配", reason=errors.PROTOCOL
                )
            if not restart_needed:
                total = _expected_total(response, status, done, cr_total, expected_size)
                with part_path.open(mode) as stream:
                    while True:
                        if cancel is not None and cancel():
                            raise TransferCancelled("download cancelled")
                        want = chunk_size
                        if max_bytes is not None:
                            want = max(1, min(chunk_size, max_bytes + 1 - done))
                        try:
                            block = response.read(want)
                        except Exception as exc:  # noqa: BLE001 - 读流失败统一分类
                            raise _translate_transport_error(exc) from exc
                        if not block:
                            break
                        if max_bytes is not None and done + len(block) > max_bytes:
                            raise BudgetExceeded(
                                "compressed", max_bytes, done + len(block)
                            )
                        stream.write(block)
                        done += len(block)
                        if progress is not None:
                            progress(done, total)
                return DownloadOutcome(
                    url=current_url,
                    bytes_written=done,
                    resumed_from=resumed_from,
                    redirects=redirects,
                )
        except BudgetExceeded:
            _discard_part(part_path)
            raise
        except (TransferCancelled, errors.NetworkError):
            raise
        except OSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise errors.NetworkUnreachable(type(exc).__name__) from exc
        finally:
            _close_quietly(response)

        restarted = True
        _discard_part(part_path)
        existing = 0


# ============================================================ 分段下载与镜像竞速

#: 镜像预检的默认超时（秒）：只换一个 head 回来，不该等满整包的超时。
PROBE_TIMEOUT = 5.0
#: 分段预检只请求 1 字节，用来确认 206 与总大小。
_PREFLIGHT_BYTES = 1


class RangesUnsupported(Exception):
    """服务端不用 Range（200）或文件太小：分段任务中止，调用方回退单流一次。"""


@dataclass(frozen=True, slots=True)
class SegmentedOutcome:
    url: str
    bytes_written: int
    resumed_from: int = 0
    redirects: int = 0
    connections: int = 1
    segments: int = 0


@dataclass(frozen=True, slots=True)
class SourceProbe:
    """一次 ``Range: bytes=0-0`` 预检的结果：够不够快、支不支持分段。"""

    url: str
    ok: bool
    ttfb: float
    status: int = 0
    total: int | None = None
    range_supported: bool = False
    final_url: str = ""
    redirects: int = 0
    error: str = ""


def _range_headers(start: int, stop: int) -> dict[str, str]:
    """分段请求头：显式 ``identity``，避免服务端压缩破坏区间语义。"""
    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Range": f"bytes={int(start)}-{int(stop) - 1}",
    }


def _range_get(
    url: str,
    start: int,
    stop: int,
    *,
    timeout: float,
    policy: TransportPolicy | None,
    cancel: CancelCallback | None = None,
):
    """发一次 Range 请求并手动逐跳跟随；返回 ``(response, final_url, redirects)``。"""
    headers = _range_headers(start, stop)
    current = str(url)
    redirects = 0
    safe = _redirect_blocking_opener if policy is not None else None
    while True:
        if cancel is not None and cancel():
            raise TransferCancelled("download cancelled")
        if policy is not None:
            _validate_initial(current, policy)
        request = urllib.request.Request(current, headers=dict(headers))
        try:
            response = http.urlopen(
                request, timeout=timeout, opener=safe, pollute=False
            )
        except _RedirectBlocked as exc:
            _close_quietly(exc)
            if policy is None:
                raise errors.NetworkError("跳转未被处理", reason=errors.PROTOCOL) from exc
            target = urllib.parse.urljoin(current, exc.target) if exc.target else ""
            redirects += 1
            if not target:
                raise errors.NetworkError("跳转缺少 Location", reason=errors.PROTOCOL) from exc
            validate_redirect(current, target, policy, redirects)
            current = target
            continue
        except urllib.error.HTTPError as exc:
            _close_quietly(exc)
            raise errors.http_error(exc) from exc
        except errors.NetworkError:
            raise
        except Exception as exc:  # noqa: BLE001 - 传输失败统一分类
            raise _translate_transport_error(exc) from exc
        return response, current, redirects


def _reject_encoded(response) -> None:
    encoding = _header(response, "Content-Encoding").strip().lower()
    if encoding and encoding != "identity":
        raise errors.NetworkError(
            f"分段响应带了 Content-Encoding：{encoding}", reason=errors.PROTOCOL
        )


def _check_range_response(response, *, start: int, stop: int, total: int) -> None:
    """206 必须是「请求的区间、请求的总长」；200 表示服务端忽略了 Range。"""
    status = int(getattr(response, "status", 200) or 200)
    if status == 200:
        raise RangesUnsupported("服务端忽略了 Range")
    if status != 206:
        raise errors.NetworkError(f"分段请求返回 {status}", reason=errors.PROTOCOL)
    _reject_encoded(response)
    cr_start, cr_end, cr_total = _parse_content_range(_header(response, "Content-Range"))
    if cr_start is None or cr_end is None or cr_total is None:
        raise errors.NetworkError("Content-Range 缺失或畸形", reason=errors.PROTOCOL)
    if cr_start != start or cr_end != stop - 1 or cr_total != total:
        raise errors.NetworkError(
            "Content-Range 与请求区间不符", reason=errors.PROTOCOL
        )


def _preflight_range(
    url: str,
    *,
    timeout: float,
    policy: TransportPolicy | None,
    cancel: CancelCallback | None,
) -> tuple[int, str, int]:
    """``bytes=0-0`` 预检：确认 206、总大小与 Range 支持。"""
    response, final_url, redirects = _range_get(
        url, 0, _PREFLIGHT_BYTES, timeout=timeout, policy=policy, cancel=cancel
    )
    try:
        status = int(getattr(response, "status", 200) or 200)
        if status == 200:
            raise RangesUnsupported("服务端忽略了 Range")
        if status != 206:
            raise errors.NetworkError(f"预检返回 {status}", reason=errors.PROTOCOL)
        _reject_encoded(response)
        start, end, total = _parse_content_range(_header(response, "Content-Range"))
        if start != 0 or end != 0 or total is None or total <= 0:
            raise errors.NetworkError("预检 Content-Range 不符", reason=errors.PROTOCOL)
        return total, final_url, redirects
    finally:
        _close_quietly(response)


def download_segmented(
    url: str,
    part_path: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str = "",
    timeout: float = 15.0,
    connections: int = ranged.DEFAULT_CONNECTIONS,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    policy: TransportPolicy | None = None,
    max_bytes: int | None = None,
) -> SegmentedOutcome:
    """多连接 Range 下载到 ``part_path``（先预检，再交给纯引擎装配）。

    - 200（服务端不支持 Range）或文件太小 → :class:`RangesUnsupported`，调用方
      回退单流**一次**，绝不把 200 的正文写进段文件；
    - 429/``Retry-After`` 等明确答复直接抛 :class:`~petcore.net.errors.HttpStatusError`，
      不换路重试；
    - 侧车只记 ``url``（索引地址），跳转后的签名 CDN 地址不落盘。
    """
    part_path = Path(part_path)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    if cancel is not None and cancel():
        raise TransferCancelled("download cancelled")
    total, transfer_url, redirects = _preflight_range(
        url, timeout=timeout, policy=policy, cancel=cancel
    )
    if expected_size is not None and int(expected_size) != total:
        raise errors.NetworkError(
            f"服务器大小与索引不一致：{total} != {expected_size}", reason=errors.PROTOCOL
        )
    if max_bytes is not None and total > int(max_bytes):
        raise BudgetExceeded("compressed", int(max_bytes), total)
    if total < ranged.MIN_SEGMENTED_BYTES:
        raise RangesUnsupported("artifact 太小，不值得切段")

    def fetch(start: int, stop: int, sink) -> None:
        response, _final, _redirects = _range_get(
            transfer_url, start, stop, timeout=timeout, policy=policy, cancel=cancel
        )
        try:
            _check_range_response(response, start=start, stop=stop, total=total)
            expected = stop - start
            read = 0
            while read < expected:
                if cancel is not None and cancel():
                    raise TransferCancelled("download cancelled")
                want = min(ranged.CHUNK_BYTES, expected - read)
                try:
                    block = response.read(want)
                except Exception as exc:  # noqa: BLE001 - 读流失败统一分类
                    raise _translate_transport_error(exc) from exc
                if not block:
                    break
                read += len(block)
                sink(block)
            if read != expected:
                raise errors.NetworkError("分段响应提前结束", reason=errors.PROTOCOL)
        finally:
            _close_quietly(response)

    result = ranged.download_segmented(
        part_path,
        total=total,
        sha256=expected_sha256,
        connections=connections,
        url=str(url),
        fetch=fetch,
        progress=progress,
        cancel=cancel,
    )
    return SegmentedOutcome(
        url=transfer_url,
        bytes_written=result.bytes_written,
        resumed_from=result.resumed_from,
        redirects=redirects,
        connections=result.connections,
        segments=result.segments,
    )


def probe_source(
    url: str,
    *,
    timeout: float = PROBE_TIMEOUT,
    policy: TransportPolicy | None = None,
    cancel: CancelCallback | None = None,
) -> SourceProbe:
    """用 ``bytes=0-0`` 测一次首字节时间，并顺带确认 Range 支持与总大小。"""
    started = time.monotonic()
    try:
        response, final_url, redirects = _range_get(
            url, 0, _PREFLIGHT_BYTES, timeout=timeout, policy=policy, cancel=cancel
        )
    except TransferCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - 竞速里失败只是一条候选记录
        return SourceProbe(
            url=str(url),
            ok=False,
            ttfb=max(0.0, time.monotonic() - started),
            error=type(exc).__name__,
        )
    ttfb = max(0.0, time.monotonic() - started)
    try:
        status = int(getattr(response, "status", 200) or 200)
        start, end, total = _parse_content_range(_header(response, "Content-Range"))
        supported = (
            status == 206 and start == 0 and end == 0 and total is not None and total > 0
        )
        return SourceProbe(
            url=str(url),
            ok=status in (200, 206),
            ttfb=ttfb,
            status=status,
            total=total,
            range_supported=supported,
            final_url=final_url,
            redirects=redirects,
        )
    finally:
        _close_quietly(response)


def probe_sources(
    urls,
    *,
    timeout: float = PROBE_TIMEOUT,
    policy: TransportPolicy | None = None,
    cancel: CancelCallback | None = None,
) -> tuple[SourceProbe, ...]:
    """并发预检多个镜像；返回顺序与输入一致（排序交给 :func:`rank_sources`）。"""
    urls = tuple(str(url) for url in urls)
    if not urls:
        return ()
    if len(urls) == 1:
        return (probe_source(urls[0], timeout=timeout, policy=policy, cancel=cancel),)
    with ThreadPoolExecutor(
        max_workers=min(ranged.MAX_CONNECTIONS, len(urls)),
        thread_name_prefix="market-probe",
    ) as pool:
        futures = [
            pool.submit(
                probe_source, url, timeout=timeout, policy=policy, cancel=cancel
            )
            for url in urls
        ]
        return tuple(future.result() for future in futures)


def rank_sources(
    probes,
    *,
    prefer_range: bool = True,
) -> tuple[str, ...]:
    """按 TTFB 排序可用源；不可用源保持索引顺序排在后面（稳定的回退顺序）。"""
    probes = tuple(probes)
    usable = [probe for probe in probes if probe.ok]
    if prefer_range:
        segmented = [probe for probe in usable if probe.range_supported]
        if segmented:
            usable = segmented
    ranked = sorted(usable, key=lambda probe: probe.ttfb)
    chosen: list[str] = []
    for probe in ranked:
        if probe.url not in chosen:
            chosen.append(probe.url)
    rest: list[str] = []
    for probe in probes:
        if probe.url not in chosen and probe.url not in rest:
            rest.append(probe.url)
    return tuple(chosen) + tuple(rest)


class HttpFetcher:
    """把模块函数包成 market 层使用的协议对象（便于测试替换）。"""

    def fetch_index(
        self,
        url: str,
        *,
        etag: str = "",
        timeout: float = 8.0,
        cancel: CancelCallback | None = None,
    ) -> IndexFetch:
        if str(url).lower().startswith("file://"):
            return fetch_index_file(url, cancel=cancel)
        return fetch_index(url, etag=etag, timeout=timeout, cancel=cancel)

    def fetch(self, spec, dest_dir, *, limits=None, progress=None, cancel=None):
        """逐跳加固的 artifact 下载（T02）；market 层优先使用这个入口。"""
        from .market_transport import fetch_artifact

        return fetch_artifact(
            spec, dest_dir, limits=limits, progress=progress, cancel=cancel
        )

    def rank_sources(
        self,
        urls,
        *,
        timeout: float = PROBE_TIMEOUT,
        cancel: CancelCallback | None = None,
        allowed_hosts: tuple[str, ...] = (),
        allowed_schemes: tuple[str, ...] = ("https", "http"),
        prefer_range: bool = True,
    ) -> tuple[str, ...]:
        """镜像竞速：按 TTFB 排序（失败/无 Range 的源排在后面，保持索引顺序）。"""
        urls = tuple(str(url) for url in urls)
        policy = TransportPolicy(
            max_redirects=3,
            allowed_schemes=tuple(allowed_schemes) or ("https",),
            allowed_hosts=tuple(allowed_hosts),
        )
        probes = probe_sources(urls, timeout=timeout, policy=policy, cancel=cancel)
        return rank_sources(probes, prefer_range=prefer_range)

    def download(
        self,
        url: str,
        part_path: Path,
        *,
        expected_size: int | None = None,
        timeout: float = 15.0,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> int:
        return download_to(
            url,
            part_path,
            expected_size=expected_size,
            timeout=timeout,
            progress=progress,
            cancel=cancel,
        )
