"""素材包索引与下载引擎（纯网络层）。

索引格式（``schema`` 必须是 1）::

    {
      "schema": 1,
      "packs": [
        {
          "id": "blue-fish",
          "name": "蓝胖鱼",
          "version": "1.0.0",
          "filename": "blue-fish-1.0.0.zip",
          "size": 12345678,
          "sha256": "<64 位十六进制>",
          "character": "big_blue_fat_fish",
          "urls": ["https://github.com/..."],
          "links": {"homepage": "https://..."},
          "notes": "可选说明"
        }
      ]
    }

口径：

- 索引只存元数据，artifact 走**有序** ``urls``；每条都必须是 https 且落在
  ``hosts`` 白名单内（默认 :data:`DEFAULT_PACK_HOSTS`，即 GitHub 的下载/原始域）。
- 不合法条目**跳过**并汇总原因；全被跳过时抛 ``NetworkError(reason=protocol)``。
- 下载主路是 :func:`petcore.net.market.download_segmented`（先 Range 预检），
  服务端回 200 / 文件太小时对**同一个 URL** 回退单流一次。
- 不做索引磁盘缓存（调用方自己决定），不 import Qt，日志不写完整 URL。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from .. import __version__
from ..contracts.archive import BudgetExceeded
from ..market.errors import TransferCancelled
from . import errors, http, market, ranged
from .stream_download import download_stream, file_sha256

logger = logging.getLogger("petcore.net.packs")

#: 允许出现的下载主机（GitHub Release 与 raw 内容域）。
#: ``api.github.com`` 是「换签名直链」用的：本机实测 release 直链经 github.com 那一跳
#: 经常连不上（TCP 超时），而 api 域可达、302 到 CDN 后同一份包能跑到 3.2 MiB/s。
DEFAULT_PACK_HOSTS = (
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "raw.githubusercontent.com",
    # 分片兜底：jsDelivr 直接吃 GitHub 仓库（free、支持 Range，实测国内约 0.25 MiB/s）。
    "cdn.jsdelivr.net",
    "fastly.jsdelivr.net",
    "gcore.jsdelivr.net",
)
#: 索引本身的读取上限。
MAX_INDEX_BYTES = 4 * 1024 * 1024
SCHEMA = 1
USER_AGENT = f"dsh-pet-core/{__version__}"
PART_SUFFIX = ".part"

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,63}\Z")
_SHA256_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")
_FILENAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")
_DIRECTORY_NAMES = frozenset({".", ".."})


@dataclass(frozen=True)
class PackPart:
    """分片：`parts` 非空时按顺序下载并拼装成整包。

    为什么需要它：Gitee 免费仓库单文件上限 100 MB，126 MB 的素材包必须拆成两份。
    分片各自校验，拼装后仍核对整包的 ``size``/``sha256``。
    """

    filename: str
    size: int
    sha256: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class PackEntry:
    """索引里的一条素材包元数据。"""

    id: str
    name: str
    version: str
    filename: str
    size: int
    sha256: str
    character: str
    urls: tuple[str, ...]
    links: Mapping[str, str]
    notes: str
    parts: tuple[PackPart, ...] = ()
    #: 为真时先走分片（国内镜像），失败再回落到 ``urls`` 的单文件源。
    prefer_parts: bool = False


@dataclass(frozen=True)
class PackIndex:
    schema: int
    packs: tuple[PackEntry, ...]

    def by_id(self, pack_id: str) -> PackEntry | None:
        for entry in self.packs:
            if entry.id == pack_id:
                return entry
        return None


@dataclass(frozen=True)
class PackDownload:
    """一次下载的可核对结果。

    ``from_cache=True`` 表示目标文件已存在且大小/摘要都对（没发任何请求），
    此时 ``connections=0``、``resumed_from=0``、``url_host=""``。
    ``resumed_from`` 只在分段路径真正续传时非零（单流路径不报告偏移）。
    """

    path: Path
    size: int
    sha256: str
    from_cache: bool
    connections: int
    resumed_from: int
    url_host: str


def parse_index(
    payload: bytes | str | dict,
    *,
    hosts=DEFAULT_PACK_HOSTS,
) -> PackIndex:
    """解析并严格校验索引；不合法条目跳过，全被跳过时抛 ``protocol`` 错误。

    ``hosts`` 可注入（本地镜像/测试放宽白名单），校验本身仍要求 https。
    """
    raw = _load(payload)
    schema = raw.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SCHEMA:
        raise errors.NetworkError(
            f"不支持的素材包索引版本：{schema!r}", reason=errors.PROTOCOL
        )
    items = raw.get("packs")
    if not isinstance(items, list):
        raise errors.NetworkError("素材包索引缺少 packs 列表", reason=errors.PROTOCOL)

    allowed = tuple(str(host).strip().lower() for host in hosts)
    entries: list[PackEntry] = []
    skipped: list[str] = []
    for position, item in enumerate(items, start=1):
        entry, reason = _parse_entry(item, position, allowed)
        if entry is None:
            skipped.append(reason)
        else:
            entries.append(entry)
    if skipped:
        logger.warning(
            "素材包索引跳过 %d 条（共 %d 条）：%s",
            len(skipped),
            len(items),
            "；".join(skipped[:8]),
        )
        if not entries:
            raise errors.NetworkError(
                "素材包索引没有可用条目：" + "；".join(skipped[:5]),
                reason=errors.PROTOCOL,
            )
    return PackIndex(schema=schema, packs=tuple(entries))


def fetch_index(
    url: str,
    *,
    timeout: float = 15.0,
    hosts=DEFAULT_PACK_HOSTS,
) -> PackIndex:
    """取回并解析索引；``pollute=False`` 语义由 :func:`petcore.net.http.fetch_url` 保证。

    索引本身也放在 Release 资产里时（``github.com/.../releases/download/...``），
    先换成签名直链——和素材包走同一条「连得上的那一跳」。
    """
    target = resolve_github_asset(str(url), timeout=min(12.0, float(timeout)))
    status, _headers, body = http.fetch_url(
        target,
        headers=(("Accept", "application/json"), ("User-Agent", USER_AGENT)),
        timeout=timeout,
        max_bytes=MAX_INDEX_BYTES,
    )
    if status < 200 or status >= 300:
        raise errors.NetworkError(f"素材包索引返回 {status}", reason=errors.PROTOCOL)
    if getattr(body, "truncated", False):
        raise errors.NetworkError(
            f"素材包索引超过 {MAX_INDEX_BYTES} 字节上限", reason=errors.PROTOCOL
        )
    try:
        return parse_index(bytes(body), hosts=hosts)
    except errors.NetworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析失败统一收敛成协议错误
        raise errors.NetworkError(
            f"素材包索引解析失败：{type(exc).__name__}", reason=errors.PROTOCOL
        ) from exc


@dataclass(frozen=True, slots=True)
class _Source:
    """一次「下载一个文件并校验」的输入：单文件整包或一个分片。"""

    urls: tuple[str, ...]
    size: int
    sha256: str


def download_pack(
    entry: PackEntry,
    dest_dir,
    *,
    connections: int = 4,
    timeout: float = 20.0,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    max_bytes: int | None = None,
    policy: str = "auto",
) -> PackDownload:
    """下载到 ``<dest_dir>/<filename>``。

    两种形态：``entry.urls``（单文件整包）与 ``entry.parts``（分片，下完拼装）。
    ``policy`` 决定先走哪一套（失败总能换另一套）：

    - ``auto``：按索引意图（``prefer_parts``），默认 GitHub 单文件优先；
    - ``mirror``：先分片（国内镜像），再单文件；
    - ``github``：只用单文件源，完全不走分片。

    目标文件已存在且大小/摘要都对 → 直接返回（``from_cache=True``，不发请求）。
    某个地址失败换下一个；单文件与分片两套源互相兜底；全部失败抛 ``NetworkError``，
    消息里只有 ``主机名: 异常类型``（不写 URL 路径/查询串）。
    """
    if not _safe_filename(entry.filename):
        raise errors.NetworkError("素材包文件名不合法", reason=errors.PROTOCOL)
    dest_dir = Path(dest_dir)
    final_path = dest_dir / entry.filename
    expected_sha = str(entry.sha256 or "").strip().lower()

    cached = _cached(final_path, entry, expected_sha)
    if cached is not None:
        return cached

    singles = tuple(str(url) for url in entry.urls)
    if not singles and not entry.parts:
        raise errors.NetworkError("素材包没有可用的下载地址", reason=errors.PROTOCOL)
    dest_dir.mkdir(parents=True, exist_ok=True)
    report = _monotonic(progress)
    failures: list[str] = []

    chosen = str(policy or "auto").strip().lower()
    if chosen not in {"auto", "mirror", "github"}:
        chosen = "auto"
    plan: list[str] = []
    if singles:
        plan.append("single")
    if entry.parts and chosen != "github":
        plan.append("parts")
    if chosen == "mirror" or (chosen == "auto" and entry.prefer_parts):
        plan.reverse()

    for kind in plan:
        if cancel is not None and cancel():
            raise TransferCancelled("download cancelled")
        try:
            if kind == "single":
                used, resumed, host = _download_source(
                    _Source(singles, int(entry.size), expected_sha),
                    final_path,
                    connections=connections,
                    timeout=timeout,
                    progress=report,
                    cancel=cancel,
                    max_bytes=max_bytes,
                )
            else:
                used, resumed, host = _download_parts(
                    entry,
                    dest_dir,
                    connections=connections,
                    timeout=timeout,
                    report=report,
                    cancel=cancel,
                )
        except (TransferCancelled, BudgetExceeded):
            raise
        except errors.NetworkError as exc:
            failures.extend(str(exc).replace("素材包下载失败：", "").split("；")[:2])
            continue
        except Exception as exc:  # noqa: BLE001 - 换下一套源
            failures.append(f"{kind}: {type(exc).__name__}")
            continue
        return PackDownload(
            path=final_path,
            size=int(entry.size),
            sha256=expected_sha,
            from_cache=False,
            connections=int(used),
            resumed_from=int(resumed),
            url_host=host,
        )

    message = "素材包下载失败：" + "；".join(failures[:4]) if failures else "素材包下载失败"
    raise errors.NetworkError(message, reason=errors.NETWORK)


def _download_source(
    source: _Source,
    dest: Path,
    *,
    connections: int,
    timeout: float,
    progress: ProgressCallback | None,
    cancel: CancelCallback | None,
    max_bytes: int | None = None,
) -> tuple[int, int, str]:
    """把 ``source`` 下到 ``dest``（先分段，必要时回退单流一次）。

    返回 ``(实际连接数, 续传字节, 主机名)``；失败抛 ``NetworkError``（消息只含主机与异常类型）。
    """
    part_path = Path(f"{dest}{PART_SUFFIX}")
    failures: list[str] = []
    for url in source.urls:
        if cancel is not None and cancel():
            raise TransferCancelled("download cancelled")
        host = (urlsplit(url).hostname or "").lower()
        # github.com 那一跳在部分网络里连不上：先换成 api.github.com 给的签名直链。
        target_url = resolve_github_asset(url, timeout=min(12.0, float(timeout or 12.0)))
        try:
            outcome = market.download_segmented(
                target_url,
                part_path,
                expected_size=source.size,
                expected_sha256=source.sha256,
                timeout=timeout,
                connections=connections,
                progress=progress,
                cancel=cancel,
                max_bytes=max_bytes,
                state_url=url,
            )
        except market.RangesUnsupported:
            try:
                download_stream(
                    target_url,
                    dest,
                    expected_size=source.size,
                    expected_sha256=source.sha256,
                    timeout=timeout,
                    progress=progress,
                    cancel=cancel,
                    max_bytes=max_bytes,
                )
            except (TransferCancelled, BudgetExceeded):
                raise
            except Exception as exc:  # noqa: BLE001 - 换下一个地址
                logger.info(
                    "素材包单流下载失败：host=%s type=%s", host or "-", type(exc).__name__
                )
                failures.append(f"{host or '-'}: {type(exc).__name__}")
                continue
            # 单流成功后，上一次分段尝试留下的段文件/侧车就没有意义了。
            ranged.clear_state(part_path)
            return 1, 0, host
        except (TransferCancelled, BudgetExceeded):
            raise
        except Exception as exc:  # noqa: BLE001 - 换下一个地址
            logger.info("素材包分段下载失败：host=%s type=%s", host or "-", type(exc).__name__)
            failures.append(f"{host or '-'}: {type(exc).__name__}")
            continue

        # 分段引擎只保证长度，摘要由这里核对（跳转后的 CDN 域不落盘/不写日志）。
        try:
            digest = file_sha256(part_path)
        except OSError as exc:
            _unlink(part_path)
            logger.info("素材包摘要读取失败：host=%s type=%s", host or "-", type(exc).__name__)
            failures.append(f"{host or '-'}: {type(exc).__name__}")
            continue
        if source.sha256 and digest != source.sha256:
            _unlink(part_path)
            logger.info("素材包摘要不符：host=%s", host or "-")
            failures.append(f"{host or '-'}: DigestMismatch")
            continue
        try:
            os.replace(part_path, dest)
        except OSError as exc:
            raise errors.NetworkError("写入素材包失败", reason=errors.INTERNAL) from exc
        return int(outcome.connections), int(outcome.resumed_from), host

    raise errors.NetworkError(
        "素材包下载失败：" + "；".join(failures[:4]), reason=errors.NETWORK
    )


def _download_parts(
    entry: PackEntry,
    dest_dir: Path,
    *,
    connections: int,
    timeout: float,
    report: ProgressCallback,
    cancel: CancelCallback | None,
) -> tuple[int, int, str]:
    """逐片下载（已校验的分片跳过）→ 拼装 → 核对整包 → 删分片。"""
    total = int(entry.size)
    offset = 0
    used = 1
    resumed_total = 0
    host = ""
    for part in entry.parts:
        if cancel is not None and cancel():
            raise TransferCancelled("download cancelled")
        target = dest_dir / part.filename
        base = offset
        offset += int(part.size)
        if _file_matches(target, part.size, part.sha256):
            report(base + int(part.size), total)
            continue
        part_progress = _offset_progress(report, base, int(part.size), total)
        part_used, part_resumed, part_host = _download_source(
            _Source(part.urls, int(part.size), part.sha256),
            target,
            connections=connections,
            timeout=timeout,
            progress=part_progress,
            cancel=cancel,
        )
        used = max(used, part_used)
        resumed_total += part_resumed
        host = part_host or host

    assembled = Path(f"{dest_dir / entry.filename}{PART_SUFFIX}")
    try:
        with assembled.open("wb") as out:
            for part in entry.parts:
                with (dest_dir / part.filename).open("rb") as stream:
                    while True:
                        if cancel is not None and cancel():
                            raise TransferCancelled("download cancelled")
                        block = stream.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
    except TransferCancelled:
        _unlink(assembled)
        raise
    except OSError as exc:
        _unlink(assembled)
        raise errors.NetworkError("拼装素材包失败", reason=errors.INTERNAL) from exc

    try:
        size = assembled.stat().st_size
        digest = file_sha256(assembled)
    except OSError as exc:
        _unlink(assembled)
        raise errors.NetworkError("读取拼装结果失败", reason=errors.INTERNAL) from exc
    expected = str(entry.sha256 or "").lower()
    if size != int(entry.size) or (expected and digest != expected):
        _unlink(assembled)
        raise errors.NetworkError("素材包拼装后校验不符", reason=errors.PROTOCOL)
    try:
        os.replace(assembled, dest_dir / entry.filename)
    except OSError as exc:
        _unlink(assembled)
        raise errors.NetworkError("写入素材包失败", reason=errors.INTERNAL) from exc
    for part in entry.parts:
        _unlink(dest_dir / part.filename)
    report(total, total)
    return used, resumed_total, host


def _offset_progress(
    report: ProgressCallback, base: int, span: int, total: int
) -> ProgressCallback:
    """把「某个分片自己的进度」换算成整包进度。"""

    def wrapper(done: int, _reported_total: int) -> None:
        report(base + min(max(0, int(done)), span), total)

    return wrapper


def _file_matches(path: Path, size: int, sha256: str) -> bool:
    try:
        if path.stat().st_size != int(size):
            return False
        return not sha256 or file_sha256(path) == str(sha256).lower()
    except OSError:
        return False

# ------------------------------------------------------------------ 直链解析
#: ``https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>``
_RELEASE_PATH_RE = re.compile(
    r"\A/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)/?\Z"
)


def host_of(url: str) -> str:
    """取 URL 的主机名（小写）；解析不出来返回空串。

    UI 层要显示「哪个源失败了」，但那里不许 import `urllib`（架构守卫），
    所以把这一小段解析留在 net 里。
    """
    return (urlsplit(str(url)).hostname or "").lower()


def release_parts(url: str) -> tuple[str, str, str, str] | None:
    """从 Release 下载链接里取 ``(owner, repo, tag, asset)``；不是这种链接返回 None。"""
    parts = urlsplit(str(url))
    if (parts.scheme or "").lower() != "https":
        return None
    if (parts.hostname or "").lower() != "github.com":
        return None
    match = _RELEASE_PATH_RE.match(parts.path or "")
    if match is None:
        return None
    return tuple(urllib.parse.unquote(item) for item in match.groups())  # type: ignore[return-value]


def resolve_github_asset(url: str, *, timeout: float = 12.0) -> str:
    """把 github.com 的 Release 链接换成签名 CDN 直链；换不到就原样返回。

    **为什么需要这一步**：下载实际走的是
    ``github.com/.../releases/download/...`` → 302 → ``release-assets.githubusercontent.com``。
    在部分网络里第一跳（github.com）TCP 直接被吞，进度条就永远停在「正在连接…」；
    而 ``api.github.com`` 与 CDN 都可达。走 API 拿一次 302 的 Location，
    等于把「连不上的那一跳」换成「连得上的那一跳」，字节完全一样。

    签名地址有时效，所以每次下载前现取，不落盘（侧车只记索引里的原始 URL）。
    """
    parts = release_parts(url)
    if parts is None:
        return str(url)
    owner, repo, tag, asset_name = parts
    try:
        status, _headers, body = http.fetch_url(
            f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag)}",
            headers=(("Accept", "application/vnd.github+json"), ("User-Agent", USER_AGENT)),
            timeout=timeout,
            max_bytes=MAX_INDEX_BYTES,
        )
        if status != 200:
            return str(url)
        payload = json.loads(bytes(body).decode("utf-8"))
        assets = payload.get("assets") if isinstance(payload, dict) else None
        asset_id = next(
            (
                int(item["id"])
                for item in assets or ()
                if isinstance(item, dict) and str(item.get("name") or "") == asset_name
            ),
            None,
        )
        if asset_id is None:
            return str(url)
        request = http.build_request(
            f"https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}",
            headers=(
                ("Accept", "application/octet-stream"),
                ("Range", "bytes=0-0"),
                ("User-Agent", USER_AGENT),
            ),
        )
        # 让 urllib 跟完 302，只取最终地址：CDN 那边回 206、只发 1 个字节。
        response = http.urlopen(request, timeout=timeout, pollute=False)
        try:
            final = str(response.geturl() or "")
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - 关流失败不影响结论
                pass
    except Exception as exc:  # noqa: BLE001 - 解析失败一律回落原链接
        logger.info("素材包直链解析失败：host=api.github.com type=%s", type(exc).__name__)
        return str(url)
    target = urlsplit(final)
    if target.scheme != "https" or (target.hostname or "").lower() not in DEFAULT_PACK_HOSTS:
        logger.info("素材包直链解析结果不在白名单，回落原链接")
        return str(url)
    return final


def _cached(
    final_path: Path, entry: PackEntry, expected_sha: str
) -> PackDownload | None:
    """本地已有且大小/摘要都对：命中缓存，不发请求。"""
    if not expected_sha or not final_path.is_file():
        return None
    try:
        if final_path.stat().st_size != int(entry.size):
            return None
        if file_sha256(final_path) != expected_sha:
            return None
    except OSError:
        return None
    return PackDownload(
        path=final_path,
        size=int(entry.size),
        sha256=expected_sha,
        from_cache=True,
        connections=0,
        resumed_from=0,
        url_host="",
    )


def _monotonic(progress: ProgressCallback | None) -> ProgressCallback:
    """进度只能前进：一个地址失败换下一个（done 从 0 重新计）时不许倒退。"""
    if progress is None:
        return lambda _done, _total: None
    state = {"high": 0, "total": 0}

    def report(done: int, total: int) -> None:
        state["total"] = max(state["total"], int(total or 0))
        value = max(int(done), state["high"])
        state["high"] = value
        progress(value, state["total"])

    return report


def _safe_filename(name: object) -> bool:
    text = name if isinstance(name, str) else ""
    return bool(_FILENAME_RE.match(text)) and text not in _DIRECTORY_NAMES


def _load(payload: bytes | str | dict) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            text = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise errors.NetworkError(
                "素材包索引不是 UTF-8 文本", reason=errors.PROTOCOL
            ) from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise errors.NetworkError("素材包索引类型不支持", reason=errors.PROTOCOL)
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise errors.NetworkError(
            f"素材包索引不是合法 JSON：{type(exc).__name__}", reason=errors.PROTOCOL
        ) from exc
    if not isinstance(raw, dict):
        raise errors.NetworkError("素材包索引顶层必须是对象", reason=errors.PROTOCOL)
    return raw


def _parse_entry(
    item: object, position: int, hosts: tuple[str, ...]
) -> tuple[PackEntry | None, str]:
    if not isinstance(item, dict):
        return None, f"第 {position} 条不是对象"
    pack_id = item.get("id")
    if not isinstance(pack_id, str) or _ID_RE.match(pack_id) is None:
        return None, f"第 {position} 条 id 不合法"

    def reject(reason: str) -> tuple[None, str]:
        return None, f"{pack_id}: {reason}"

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return reject("name 为空")
    version = item.get("version")
    if not isinstance(version, str) or not version.strip():
        return reject("version 为空")
    filename = item.get("filename")
    if not _safe_filename(filename):
        return reject("filename 含非法字符或指向目录")

    size = item.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return reject("size 必须是正整数")

    sha256 = item.get("sha256")
    if not isinstance(sha256, str) or _SHA256_RE.match(sha256.strip()) is None:
        return reject("sha256 缺失或不是 64 位十六进制")

    raw_urls = item.get("urls")
    if not isinstance(raw_urls, (list, tuple)) or not raw_urls:
        return reject("urls 为空")
    urls: list[str] = []
    for raw_url in raw_urls:
        if not isinstance(raw_url, str) or not http.is_allowed_https_url(raw_url, hosts):
            return reject("urls 含非 https 或不在白名单内的地址")
        if raw_url not in urls:
            urls.append(raw_url)

    links = item.get("links")
    if links is None:
        links = {}
    if not isinstance(links, Mapping):
        return reject("links 不是对象")
    clean_links: dict[str, str] = {}
    for key, value in links.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return reject("links 含非字符串键值")
        clean_links[key] = value

    notes = item.get("notes")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        return reject("notes 不是文本")

    character = item.get("character")
    if character is None:
        character = ""
    if not isinstance(character, str):
        return reject("character 不是文本")

    parts, problem = _parse_parts(item.get("parts"), hosts=hosts)
    if problem:
        return reject(problem)
    if parts:
        total = sum(part.size for part in parts)
        if total != int(size):
            return reject(f"分片大小合计 {total} 与整包 {size} 不一致")
    prefer_parts = item.get("prefer_parts")
    if prefer_parts is None:
        prefer_parts = False
    if not isinstance(prefer_parts, bool):
        return reject("prefer_parts 必须是布尔")

    return (
        PackEntry(
            id=pack_id,
            name=name.strip(),
            version=version.strip(),
            filename=filename,
            size=int(size),
            sha256=sha256.strip().lower(),
            character=character.strip(),
            urls=tuple(urls),
            links=clean_links,
            notes=notes,
            parts=parts,
            prefer_parts=prefer_parts,
        ),
        "",
    )


def _parse_parts(
    raw: object, *, hosts: tuple[str, ...]
) -> tuple[tuple[PackPart, ...], str]:
    """解析 ``parts``；返回 ``(分片, 问题)``，问题非空时整条被拒。"""
    if raw is None:
        return (), ""
    if not isinstance(raw, (list, tuple)) or not raw:
        return (), "parts 必须是非空数组"
    parts: list[PackPart] = []
    seen: set[str] = set()
    for position, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            return (), f"分片 {position} 不是对象"
        filename = item.get("filename")
        if not _safe_filename(filename):
            return (), f"分片 {position} filename 不合法"
        if filename in seen:
            return (), f"分片 {position} filename 重复"
        seen.add(filename)
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return (), f"分片 {position} size 必须是正整数"
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or _SHA256_RE.match(sha256.strip()) is None:
            return (), f"分片 {position} sha256 缺失或不是 64 位十六进制"
        raw_urls = item.get("urls")
        if not isinstance(raw_urls, (list, tuple)) or not raw_urls:
            return (), f"分片 {position} urls 为空"
        urls: list[str] = []
        for raw_url in raw_urls:
            if not isinstance(raw_url, str) or not http.is_allowed_https_url(raw_url, hosts):
                return (), f"分片 {position} urls 含非 https 或不在白名单内的地址"
            if raw_url not in urls:
                urls.append(raw_url)
        parts.append(
            PackPart(
                filename=filename,
                size=int(size),
                sha256=sha256.strip().lower(),
                urls=tuple(urls),
            )
        )
    return tuple(parts), ""


def _unlink(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
