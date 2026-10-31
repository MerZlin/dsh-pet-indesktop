"""artifact 下载编排：多 URL 回退、续传、大小与 SHA-256 校验。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..contracts.archive import ArchiveLimits
from ..contracts.http import TransferSpec
from ..net import ranged
from . import verify
from .errors import DownloadError, IntegrityError, TransferCancelled
from .model import MarketPackage, MarketVersion

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

#: 镜像竞速的预检超时：只测首字节，不值得等满整包的超时。
PROBE_TIMEOUT = 5.0

#: GitHub Release 下载会从 `github.com` 跳到这些 CDN 主机；逐跳校验靠这张显式名单。
GITHUB_ARTIFACT_HOSTS = (
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
)


class ArtifactFetcher(Protocol):
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
        ...


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    url: str
    sha256: str
    attempts: tuple[str, ...] = ()
    #: 本次传输沿用了多少已有字节（分段/单流续传），0 = 全新下载。
    resumed_from: int = 0
    #: 实际并行连接数（1 = 单流）。
    connections: int = 1


def download_dir(root: Path) -> Path:
    return Path(root) / "market" / "downloads"


def _part_path(directory: Path, package_id: str, version: str) -> Path:
    return directory / f"{package_id}-{version}.zip.part"


def _final_path(directory: Path, package_id: str, version: str) -> Path:
    return directory / f"{package_id}-{version}.zip"


def _state_path(directory: Path, package_id: str, version: str) -> Path:
    return directory / f"{package_id}-{version}.download.json"


def _write_state(path: Path, package: MarketPackage, version: MarketVersion) -> None:
    payload = {
        "package_id": package.id,
        "version": version.version,
        "size": version.size,
        "sha256": version.sha256.lower(),
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _state_matches(path: Path, package: MarketPackage, version: MarketVersion) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return payload == {
        "package_id": package.id,
        "version": version.version,
        "size": version.size,
        "sha256": version.sha256.lower(),
    }


def _discard_resume_state(part: Path, state: Path) -> None:
    for path in (part, state):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    ranged.clear_state(part)


def _preserve_staged_part(staging: Path, part: Path) -> None:
    candidates = sorted(staging.glob("*.part"))
    if not candidates:
        return
    source = candidates[0]
    try:
        os.replace(source, part)
    except OSError:
        try:
            shutil.copyfile(source, part)
        except OSError:
            return


def resumable_bytes(root: Path, package: MarketPackage, version: MarketVersion) -> int:
    """已下载、下次可续传的字节数（稳定的 ``.part`` 或分段状态）。

    只用于界面显示（进度行的「续传」）：分段状态的逐段字节与装配后的 ``.part``
    是两种形态，取当前存在的那一份，不重复累加。
    """
    directory = download_dir(root)
    part = _part_path(directory, package.id, version.version)
    try:
        if part.is_file():
            return int(part.stat().st_size)
    except OSError:
        pass
    state = ranged.read_state(part, size=version.size, sha256=version.sha256)
    if state is None:
        return 0
    total = 0
    for item in state.segments:
        total += max(0, int(item.bytes))
    return min(total, max(0, int(version.size)))


def _ranked_urls(
    fetcher: ArtifactFetcher,
    urls: list[str],
    *,
    mirror_policy: str,
    connections: int,
    timeout: float,
    cancel: CancelCallback | None,
    allow_local_file: bool,
) -> list[str]:
    """竞速策略下把最快响应的镜像排到最前面；失败/未知源保持索引顺序。"""
    if mirror_policy == "ordered" or len(urls) < 2:
        return list(urls)
    rank = getattr(fetcher, "rank_sources", None)
    if not callable(rank):
        return list(urls)
    if cancel is not None and cancel():
        raise TransferCancelled("download cancelled")
    schemes = ("https", "http", "file") if allow_local_file else ("https", "http")
    try:
        ranked = rank(
            tuple(urls),
            timeout=min(timeout, PROBE_TIMEOUT),
            cancel=cancel,
            allowed_hosts=GITHUB_ARTIFACT_HOSTS,
            allowed_schemes=schemes,
            prefer_range=connections > 1,
        )
    except TransferCancelled:
        raise
    except Exception:  # noqa: BLE001 - 预检失败只是退回索引顺序
        return list(urls)
    known = set(urls)
    ordered: list[str] = []
    for url in list(ranked) + list(urls):
        if url in known and url not in ordered:
            ordered.append(url)
    return ordered or list(urls)


def download_artifact(
    fetcher: ArtifactFetcher,
    root: Path,
    package: MarketPackage,
    version: MarketVersion,
    *,
    limits: ArchiveLimits | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    timeout: float = 15.0,
    local_file_root: Path | None = None,
    allow_local_file: bool = False,
    connections: int = 1,
    mirror_policy: str = "auto",
) -> DownloadResult:
    directory = download_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    part = _part_path(directory, package.id, version.version)
    final = _final_path(directory, package.id, version.version)
    state = _state_path(directory, package.id, version.version)
    attempts: list[str] = []
    connections = ranged.normalize_connections(connections)

    if part.exists() and not _state_matches(state, package, version):
        _discard_resume_state(part, state)
    if part.exists():
        _write_state(state, package, version)

    if final.is_file():
        try:
            if verify.sha256_file(final) == version.sha256 and final.stat().st_size == version.size:
                state.unlink(missing_ok=True)
                return DownloadResult(
                    path=final,
                    url="cache",
                    sha256=version.sha256,
                    connections=connections,
                )
        except OSError:
            pass
        try:
            final.unlink()
        except OSError:
            pass

    _write_state(state, package, version)
    urls = _ranked_urls(
        fetcher,
        list(version.urls),
        mirror_policy=str(mirror_policy or "auto"),
        connections=connections,
        timeout=timeout,
        cancel=cancel,
        allow_local_file=allow_local_file,
    )
    for url in urls:
        path: Path | None = None
        try:
            path, resumed_from, used_connections = _fetch_one(
                fetcher,
                url,
                part,
                directory,
                package,
                version,
                limits=limits,
                progress=progress,
                cancel=cancel,
                timeout=timeout,
                local_file_root=local_file_root,
                allow_local_file=allow_local_file,
                connections=connections,
            )
            size = path.stat().st_size
            if size != version.size:
                raise IntegrityError(f"大小不一致：期望 {version.size}，实际 {size}")
            digest = verify.sha256_file(path)
            if digest != version.sha256:
                raise IntegrityError("SHA-256 与索引不一致")
        except TransferCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - 记录后尝试下一个源
            attempts.append(f"{url}: {type(exc).__name__}: {exc}")
            if path is not None and path != part:
                # 分段分支直接落在最终文件名上；没通过校验的字节不许留成产物。
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, IntegrityError):
                _discard_resume_state(part, state)
            continue
        assert path is not None
        try:
            if path != final:
                os.replace(path, final)
        except OSError as exc:
            attempts.append(f"{url}: 写入下载目录失败：{exc}")
            continue
        try:
            state.unlink(missing_ok=True)
        except OSError:
            pass
        ranged.clear_state(part)
        return DownloadResult(
            path=final,
            url=url,
            sha256=version.sha256,
            attempts=tuple(attempts),
            resumed_from=resumed_from,
            connections=used_connections,
        )

    detail = "；".join(attempts) or "没有可用的下载地址"
    raise DownloadError(f"{package.id} {version.version} 下载失败：{detail}")


def _fetch_one(
    fetcher: ArtifactFetcher,
    url: str,
    part: Path,
    directory: Path,
    package: MarketPackage,
    version: MarketVersion,
    *,
    limits: ArchiveLimits | None,
    progress: ProgressCallback | None,
    cancel: CancelCallback | None,
    timeout: float,
    local_file_root: Path | None,
    allow_local_file: bool,
    connections: int,
) -> tuple[Path, int, int]:
    """返回 ``(落盘路径, 续传字节数, 连接数)``。

    优先走逐跳加固的 `fetch`（T02）；测试替身只实现 `download` 时保持旧路径。
    分段分支用稳定的 ``artifact_name`` 直写 ``<id>-<version>.zip.part``，不再把整包
    复制进 staging；单流分支保持原来的 staging 语义（字节级兼容）。
    """
    fetch = getattr(fetcher, "fetch", None)
    if callable(fetch):
        schemes = ("https", "http", "file") if allow_local_file else ("https", "http")
        segmented = ranged.should_segment(version.size, connections)
        # 传输层把 `<artifact>.part` 当下载中的临时文件；这里给最终名，
        # 它就会直接写我们那个稳定的 `<id>-<version>.zip.part`，不必再复制整包。
        artifact = part.name[: -len(".part")] if part.name.endswith(".part") else part.name
        spec = TransferSpec(
            urls=(url,),
            expected_size=version.size,
            expected_sha256=version.sha256,
            allowed_hosts=GITHUB_ARTIFACT_HOSTS,
            timeout=timeout,
            allowed_schemes=schemes,
            allow_local_file=allow_local_file,
            local_root=str(local_file_root) if local_file_root is not None else None,
            download_connections=connections,
            artifact_name=artifact if segmented else "",
        )
        if segmented:
            report = fetch(spec, directory, limits=limits, progress=progress, cancel=cancel)
            return Path(report.path), int(report.resumed_from), connections
        staging = directory / f".fetch-{package.id}-{version.version}"
        staging.mkdir(parents=True, exist_ok=True)
        # HttpFetcher uses the URL basename. Seed that transient name from the
        # stable package/version part so cancellation can resume without losing it.
        if part.is_file():
            name = Path(url.split("?", 1)[0].rstrip("/")).name or "artifact.zip"
            staged_part = staging / f"{name}.part"
            if not staged_part.exists():
                shutil.copyfile(part, staged_part)
        try:
            report = fetch(
                spec, staging, limits=limits, progress=progress, cancel=cancel
            )
            os.replace(report.path, part)
        except Exception:
            # Preserve only the resumable bytes; do not leave a Qt/network staging
            # tree that can be mistaken for an installable artifact.
            _preserve_staged_part(staging, part)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return part, int(report.resumed_from), 1
    fetcher.download(
        url,
        part,
        expected_size=version.size,
        timeout=timeout,
        progress=progress,
        cancel=cancel,
    )
    return part, 0, 1
