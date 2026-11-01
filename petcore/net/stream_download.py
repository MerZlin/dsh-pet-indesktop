"""单流下载：分段不可用（或不值得分段）时的回退路径。

零 Qt、零第三方依赖；HTTP 一律经 :mod:`petcore.net.http` 且 ``pollute=False``
——一次素材下载的成败不代表整台 API 该不该走代理。

口径（对齐 ``update/service.py`` 的成熟实现，但那条链不能改）：

- 先写 ``<final>.part``，大小与摘要都通过后 ``os.replace`` 成 ``final``；
- ``.part`` 已有内容时带 ``Range`` 续传，服务端回 200（不支持 Range）就从头写；
- 取消抛 :class:`TransferCancelled` 并**保留** ``.part``（下次接着下）；
- 大小/摘要不符删 ``.part`` **与** ``final``，抛 ``NetworkError(reason=protocol)``，
  消息里不带 URL（索引里的地址可能就是签名链接）；
- ``max_bytes`` 越界抛 :class:`BudgetExceeded` 并删 ``.part``；
- 分块 256 KiB，进度单调不减（回退到从头写时也不倒退）；
- 跳转由 ``net/http`` 负责，最终地址只用来填 ``StreamResult.redirected``，不写日志。
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import __version__
from ..contracts.archive import BudgetExceeded
from ..market.errors import TransferCancelled
from . import errors, http

#: 读流与取消检查的粒度。
CHUNK_BYTES = 256 * 1024
#: 校验摘要时的分块大小。
_HASH_BLOCK_BYTES = 1024 * 1024
USER_AGENT = f"dsh-pet-core/{__version__}"
PART_SUFFIX = ".part"

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

_CONTENT_RANGE_RE = re.compile(r"\Abytes\s+(\d+)-(\d+)/(\d+|\*)\Z", re.IGNORECASE)


@dataclass(frozen=True)
class StreamResult:
    """一次单流下载的结果；``redirected`` 是跳转后的最终地址（不写日志）。"""

    path: Path
    size: int
    sha256: str
    redirected: str


def file_sha256(path) -> str:
    """分块算文件摘要（1 MiB/块），供下载校验与缓存命中判定复用。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(_HASH_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_stream(
    url: str,
    final_path: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str = "",
    timeout: float = 20.0,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    max_bytes: int | None = None,
) -> StreamResult:
    """单流下载到 ``final_path``；``.part`` 可续传，成功后原子改名。

    ``expected_sha256`` 为空表示只校验大小。
    """
    http.validate_target(str(url))
    final_path = Path(final_path)
    part_path = Path(f"{final_path}{PART_SUFFIX}")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    expected_sha = str(expected_sha256 or "").strip().lower()
    if max_bytes is not None and int(max_bytes) <= 0:
        raise ValueError("max_bytes must be positive")
    if cancel is not None and cancel():
        raise TransferCancelled("download cancelled")
    report = _monotonic(progress)
    existing = part_path.stat().st_size if part_path.is_file() else 0
    if max_bytes is not None and existing > int(max_bytes):
        _unlink(part_path)
        raise BudgetExceeded("compressed", int(max_bytes), existing)
    restarted = False

    while True:
        try:
            response = _open(url, existing, timeout=timeout)
        except _ResumeRejected:
            # 416：本地片段可能已经下完（大小等于预期就只差校验），否则从头重下。
            if expected_size is not None and existing == int(expected_size):
                return _finalize(
                    part_path,
                    final_path,
                    expected_size=expected_size,
                    expected_sha256=expected_sha,
                    redirected=str(url),
                )
            if restarted:
                _unlink(part_path)
                raise errors.HttpStatusError(416) from None
            restarted = True
            _unlink(part_path)
            existing = 0
            continue
        status = int(getattr(response, "status", 200) or 200)
        final_url = _final_url(response, url)
        try:
            if 300 <= status < 400:
                raise errors.NetworkError(
                    f"未处理的跳转状态：{status}", reason=errors.PROTOCOL
                )
            mode = "wb"
            done = 0
            total = int(expected_size or 0)
            restart_needed = False
            if status == 206:
                start, end, cr_total = _parse_content_range(_header(response, "Content-Range"))
                if existing > 0:
                    if (
                        start is None
                        or start != existing
                        or (
                            expected_size is not None
                            and cr_total is not None
                            and cr_total != int(expected_size)
                        )
                        or (
                            expected_size is not None
                            and end is not None
                            and end + 1 > int(expected_size)
                        )
                    ):
                        restart_needed = True
                    else:
                        mode = "ab"
                        done = existing
                        total = total or int(cr_total or 0) or _declared_total(response, done)
                elif start != 0:
                    restart_needed = True
                else:
                    total = total or int(cr_total or 0) or _declared_total(response, 0)
            else:
                # 200 意味着服务端忽略了 Range：丢掉续传点，从头写。
                total = total or _declared_total(response, 0)
            if restart_needed and restarted:
                _unlink(part_path)
                _unlink(final_path)
                raise errors.NetworkError(
                    "服务端续传区间与本地片段不符", reason=errors.PROTOCOL
                )
            if not restart_needed:
                try:
                    with part_path.open(mode) as stream:
                        while True:
                            if cancel is not None and cancel():
                                raise TransferCancelled("download cancelled")
                            want = CHUNK_BYTES
                            if max_bytes is not None:
                                want = max(1, min(CHUNK_BYTES, int(max_bytes) + 1 - done))
                            try:
                                block = response.read(want)
                            except Exception as exc:  # noqa: BLE001 - 读流失败统一分类
                                raise _transport_error(exc) from exc
                            if not block:
                                break
                            if max_bytes is not None and done + len(block) > int(max_bytes):
                                raise BudgetExceeded(
                                    "compressed", int(max_bytes), done + len(block)
                                )
                            stream.write(block)
                            done += len(block)
                            report(done, total)
                except BudgetExceeded:
                    _unlink(part_path)
                    raise
                if total > 0 and done != total:
                    _unlink(part_path)
                    _unlink(final_path)
                    raise errors.NetworkError("下载提前结束", reason=errors.PROTOCOL)
                return _finalize(
                    part_path,
                    final_path,
                    expected_size=expected_size,
                    expected_sha256=expected_sha,
                    redirected=final_url,
                )
        finally:
            _close_quietly(response)
        # 服务端的续传区间不可用：清掉本地片段，从头再来一次。
        restarted = True
        _unlink(part_path)
        existing = 0


def _open(url: str, existing: int, *, timeout: float):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if existing > 0:
        headers["Range"] = f"bytes={int(existing)}-"
    request = urllib.request.Request(str(url), headers=headers)
    try:
        return http.urlopen(request, timeout=timeout, pollute=False)
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        if status == 416 and existing > 0:
            _close_quietly(exc)
            raise _ResumeRejected() from None
        raise errors.http_error(exc) from exc
    except errors.NetworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - 传输失败统一分类
        raise _transport_error(exc) from exc


class _ResumeRejected(Exception):
    """``416``：服务端认为本地片段已经到头（或比它声称的总长还大）。"""


def _finalize(
    part_path: Path,
    final_path: Path,
    *,
    expected_size: int | None,
    expected_sha256: str,
    redirected: str,
) -> StreamResult:
    try:
        size = part_path.stat().st_size
        digest = file_sha256(part_path)
    except OSError as exc:
        _unlink(part_path)
        _unlink(final_path)
        raise errors.NetworkError("下载文件不可读", reason=errors.PROTOCOL) from exc
    if (expected_size is not None and size != int(expected_size)) or (
        expected_sha256 and digest != expected_sha256
    ):
        _unlink(part_path)
        _unlink(final_path)
        raise errors.NetworkError("下载内容与预期不符（大小或摘要）", reason=errors.PROTOCOL)
    try:
        os.replace(part_path, final_path)
    except OSError as exc:
        raise errors.NetworkError("写入目标文件失败", reason=errors.INTERNAL) from exc
    return StreamResult(path=final_path, size=size, sha256=digest, redirected=redirected)


def _monotonic(progress: ProgressCallback | None) -> ProgressCallback:
    """进度只能前进：续传区间不可用而从头写时，done 不许倒退。"""
    if progress is None:
        return lambda _done, _total: None
    state = {"high": 0}

    def report(done: int, total: int) -> None:
        value = max(int(done), state["high"])
        state["high"] = value
        progress(value, int(total))

    return report


def _header(response, name: str) -> str:
    try:
        value = response.headers.get(name)
    except AttributeError:
        return ""
    return str(value or "")


def _declared_total(response, done: int) -> int:
    raw = _header(response, "Content-Length").strip()
    try:
        return done + int(raw) if raw else 0
    except ValueError:
        return 0


def _final_url(response, url: str) -> str:
    getter = getattr(response, "geturl", None)
    if not callable(getter):
        return str(url)
    try:
        return str(getter() or url)
    except Exception:  # noqa: BLE001 - 拿不到最终地址只影响诊断字段
        return str(url)


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


def _transport_error(exc: BaseException) -> errors.NetworkError:
    if isinstance(exc, errors.NetworkError):
        return exc
    reason = errors.classify_exception(exc) or errors.NETWORK
    if reason == errors.TIMEOUT:
        return errors.NetworkTimeout("连接超时")
    return errors.NetworkError(reason=reason)


def _unlink(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _close_quietly(resource) -> None:
    try:
        resource.close()
    except Exception:  # noqa: BLE001 - 关闭不得掩盖原始失败
        pass
