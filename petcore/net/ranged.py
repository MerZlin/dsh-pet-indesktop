"""分段下载引擎：切段、每段续传、装配、取消与进度。

纯逻辑：不 import urllib/socket，也不碰 Qt。字节由调用方以 ``fetch(start, stop,
sink)`` 回调提供——它必须把 ``[start, stop)`` 交给 sink，并在 206 校验失败时抛错。
引擎只负责线程、续传偏移、段文件、装配与状态文件。

口径：

- 每段一个 ``<part>.seg<i>``，状态在 ``<part>.segments.json``：size / sha256 /
  segment_bytes / connections + 每段已写字节与**来源 URL**。
- **换了来源的段必须整段重下**：两个镜像的字节不许拼在同一个段里。
- 装配按段序追加，边追加边删段文件（峰值磁盘 ≈ 整包 + 一段）。
- 取消/失败保留段文件与状态供续传；成功才产出 ``.part``。
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
from glob import escape as _glob_escape
from pathlib import Path
from typing import Callable, Mapping

from ..market.errors import TransferCancelled

#: 单段的默认粒度上限（实际段长还受连接数约束，见 :func:`plan_segments`）。
SEGMENT_BYTES = 8 * 1024 * 1024
#: 低于这个大小一律走单流：切段的固定开销比省下来的时间还多。
MIN_SEGMENTED_BYTES = 4 * 1024 * 1024
MIN_CONNECTIONS = 1
MAX_CONNECTIONS = 8
DEFAULT_CONNECTIONS = 4
#: 读流/取消的检查粒度。
CHUNK_BYTES = 256 * 1024
SEGMENT_SUFFIX = ".seg"
SIDECAR_SUFFIX = ".segments.json"
_ASSEMBLY_BLOCK = 1024 * 1024

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]
#: ``fetch(start, stop, sink)``：把 ``[start, stop)`` 的字节写进 ``sink``。
FetchSegment = Callable[[int, int, Callable[[bytes], None]], None]


class SegmentationError(RuntimeError):
    """分段状态不自洽：段长度不符、段文件缺失、装配字节数不对。"""


def normalize_connections(value) -> int:
    """把配置/契约里的连接数钳到 1..8；不可解析按 1（旧单流）。"""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return MIN_CONNECTIONS
    return max(MIN_CONNECTIONS, min(MAX_CONNECTIONS, number))


def should_segment(size, connections) -> bool:
    """是否值得切段：至少 4 MiB 且连接数 > 1。"""
    try:
        total = int(size)
    except (TypeError, ValueError):
        return False
    return total >= MIN_SEGMENTED_BYTES and normalize_connections(connections) > 1


@dataclass(frozen=True, slots=True)
class Segment:
    index: int
    start: int
    stop: int  # 不含
    path: Path

    @property
    def length(self) -> int:
        return self.stop - self.start

    @property
    def range_header(self) -> str:
        return f"bytes={self.start}-{self.stop - 1}"


def segment_path(part, index: int) -> Path:
    return Path(f"{part}{SEGMENT_SUFFIX}{int(index)}")


def plan_segments(
    total,
    prefix,
    *,
    segment_bytes: int = SEGMENT_BYTES,
    connections: int = DEFAULT_CONNECTIONS,
) -> tuple[Segment, ...]:
    """把 ``total`` 切成按序排列的段。

    段长上限是 ``segment_bytes``，同时每连接至少分到一段（小文件也能并行）；
    这样 96 MiB / 4 连接是 12 段（4 个 worker 依次领），5 MiB / 4 连接是 4 段。
    """
    total = int(total)
    if total <= 0:
        return ()
    try:
        granularity = int(segment_bytes)
    except (TypeError, ValueError):
        granularity = SEGMENT_BYTES
    granularity = max(CHUNK_BYTES, granularity)
    workers = normalize_connections(connections)
    chunk = min(granularity, max(1, -(-total // workers)))
    count = -(-total // chunk)
    prefix = Path(prefix)
    segments: list[Segment] = []
    start = 0
    for index in range(count):
        stop = total if index == count - 1 else min(total, start + chunk)
        segments.append(
            Segment(
                index=index,
                start=start,
                stop=stop,
                path=Path(f"{prefix}{SEGMENT_SUFFIX}{index}"),
            )
        )
        start = stop
    return tuple(segments)


@dataclass(frozen=True, slots=True)
class SegmentState:
    bytes: int = 0
    url: str = ""


@dataclass(frozen=True, slots=True)
class ResumeState:
    size: int
    sha256: str
    segment_bytes: int
    connections: int
    segments: tuple[SegmentState, ...]

    def state_for(self, index: int) -> SegmentState | None:
        if 0 <= index < len(self.segments):
            return self.segments[index]
        return None


def sidecar_path(part) -> Path:
    return Path(f"{part}{SIDECAR_SUFFIX}")


def read_state(part, *, size, sha256: str = "") -> ResumeState | None:
    """读续传状态；大小/摘要不符或文件损坏返回 ``None``（当作没有状态）。"""
    path = sidecar_path(part)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        stored_size = int(raw.get("size"))
        segment_bytes = int(raw.get("segment_bytes"))
        connections = int(raw.get("connections"))
    except (TypeError, ValueError):
        return None
    if stored_size != int(size) or segment_bytes <= 0:
        return None
    stored_sha = str(raw.get("sha256") or "").lower()
    if sha256 and stored_sha and stored_sha != str(sha256).lower():
        return None
    entries = raw.get("segments")
    if not isinstance(entries, list):
        return None
    states: list[SegmentState] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        try:
            written = max(0, int(entry.get("bytes") or 0))
        except (TypeError, ValueError):
            written = 0
        states.append(SegmentState(bytes=written, url=str(entry.get("url") or "")))
    return ResumeState(
        size=stored_size,
        sha256=stored_sha,
        segment_bytes=segment_bytes,
        connections=normalize_connections(connections),
        segments=tuple(states),
    )


def write_state(
    part,
    *,
    size,
    sha256: str,
    segment_bytes: int,
    connections: int,
    segments: Mapping[int, SegmentState],
) -> None:
    """原子写侧车文件；**只存索引里的来源 URL，不存跳转后的签名地址**。"""
    count = (max(segments) + 1) if segments else 0
    entries = []
    for index in range(count):
        state = segments.get(index) or SegmentState()
        entries.append(
            {"bytes": max(0, int(state.bytes)), "url": str(state.url or "")}
        )
    payload = {
        "schema": 1,
        "size": int(size),
        "sha256": str(sha256 or "").lower(),
        "segment_bytes": int(segment_bytes),
        "connections": normalize_connections(connections),
        "segments": entries,
    }
    path = sidecar_path(part)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_state(part) -> None:
    """删掉侧车与所有段文件；不动 ``.part`` 本身。"""
    part = Path(part)
    candidates = [sidecar_path(part)]
    try:
        candidates.extend(part.parent.glob(f"{_glob_escape(part.name)}{SEGMENT_SUFFIX}*"))
    except OSError:
        pass
    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def assemble(part, segments) -> int:
    """按序追加段文件并逐段删除，返回写入字节数。"""
    part = Path(part)
    part.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with part.open("wb") as out:
            for segment in segments:
                source = Path(segment.path)
                remaining = segment.length
                with source.open("rb") as handle:
                    while remaining > 0:
                        block = handle.read(min(_ASSEMBLY_BLOCK, remaining))
                        if not block:
                            raise SegmentationError(
                                f"段 {segment.index} 字节不足：还差 {remaining}"
                            )
                        out.write(block)
                        written += len(block)
                        remaining -= len(block)
                source.unlink(missing_ok=True)
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return written


@dataclass(frozen=True, slots=True)
class SegmentedResult:
    bytes_written: int
    resumed_from: int = 0
    connections: int = 1
    segments: int = 0


def download_segmented(
    part,
    *,
    total,
    sha256: str = "",
    connections: int = DEFAULT_CONNECTIONS,
    segment_bytes: int = SEGMENT_BYTES,
    url: str = "",
    fetch: FetchSegment,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
) -> SegmentedResult:
    """并行下载 ``total`` 字节并装配到 ``part``。

    ``fetch`` 由调用方提供（负责真实 HTTP 与 206 校验）；``url`` 只用来记住
    「这一段来自哪个索引地址」——与状态里的来源不同就整段重下。
    """
    part = Path(part)
    total = int(total)
    if total <= 0:
        raise SegmentationError("total must be positive")
    connections = normalize_connections(connections)
    state = read_state(part, size=total, sha256=sha256)
    if state is not None:
        # 沿用产生段文件的计划，改配置不会让已下载的段失效。
        segment_bytes = state.segment_bytes
        connections = state.connections
    plan = plan_segments(
        total, part, segment_bytes=segment_bytes, connections=connections
    )
    if state is not None and len(state.segments) != len(plan):
        state = None
    part.parent.mkdir(parents=True, exist_ok=True)

    stored = (
        {index: item for index, item in enumerate(state.segments)}
        if state is not None
        else {}
    )
    have: dict[int, int] = {}
    for segment in plan:
        entry = stored.get(segment.index)
        path = Path(segment.path)
        try:
            on_disk = path.stat().st_size if path.is_file() else 0
        except OSError:
            on_disk = 0
        reusable = entry is not None and (not url or not entry.url or entry.url == url)
        written = min(on_disk, segment.length) if reusable else 0
        if not reusable or on_disk != written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            written = 0
        have[segment.index] = written
    resumed_from = sum(have.values())

    done = {"bytes": resumed_from}
    counted = threading.Lock()
    progress_lock = threading.Lock()
    abort = threading.Event()

    def cancelled() -> bool:
        if abort.is_set():
            return True
        return bool(cancel is not None and cancel())

    def sink_for(handle, segment: Segment, written: list[int]):
        def sink(block: bytes) -> None:
            if cancelled():
                raise TransferCancelled("download cancelled")
            if written[0] + len(block) > segment.length:
                raise SegmentationError(f"段 {segment.index} 超出计划长度")
            handle.write(block)
            written[0] += len(block)
            with counted:
                done["bytes"] += len(block)
                current = done["bytes"]
            if progress is not None:
                with progress_lock:
                    progress(current, total)

        return sink

    def run_segment(segment: Segment) -> None:
        if cancelled():
            raise TransferCancelled("download cancelled")
        existing = have[segment.index]
        path = Path(segment.path)
        written = [existing]
        with path.open("ab" if existing else "wb") as handle:
            if segment.stop - (segment.start + existing) > 0:
                fetch(segment.start + existing, segment.stop, sink_for(handle, segment, written))
        try:
            on_disk = path.stat().st_size
        except OSError:
            on_disk = 0
        if on_disk != segment.length:
            raise SegmentationError(
                f"段 {segment.index} 长度不符：期望 {segment.length}，实际 {on_disk}"
            )

    def worker(segment: Segment) -> None:
        try:
            run_segment(segment)
        except BaseException:
            # 让别的连接立刻收手，别在失败之后继续占带宽。
            abort.set()
            raise

    if progress is not None:
        with progress_lock:
            progress(resumed_from, total)

    futures = []
    error: BaseException | None = None
    if plan:
        pool = ThreadPoolExecutor(
            max_workers=max(1, min(connections, len(plan))),
            thread_name_prefix="market-segment",
        )
        try:
            futures = [pool.submit(worker, segment) for segment in plan]
            # 先等所有段收口（失败的那次只会让 abort 置位，别的 worker 自己收手）；
            # `shutdown` 必须放在收集之后，否则排队中的段会被 cancel 掉、装配缺段。
            for future in futures:
                try:
                    future.result()
                except CancelledError:
                    continue
                except BaseException as exc:  # noqa: BLE001 - 统一在状态落盘后抛
                    if error is None or (
                        isinstance(error, TransferCancelled)
                        and not isinstance(exc, TransferCancelled)
                    ):
                        error = exc
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    entries: dict[int, SegmentState] = {}
    for segment in plan:
        path = Path(segment.path)
        try:
            on_disk = path.stat().st_size if path.is_file() else 0
        except OSError:
            on_disk = 0
        previous = stored.get(segment.index)
        entries[segment.index] = SegmentState(
            bytes=min(on_disk, segment.length),
            url=url or (previous.url if previous is not None else ""),
        )
    try:
        write_state(
            part,
            size=total,
            sha256=sha256,
            segment_bytes=segment_bytes,
            connections=connections,
            segments=entries,
        )
    except OSError:
        pass

    if error is not None:
        raise error

    written = assemble(part, plan)
    if written != total:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise SegmentationError(f"装配字节数不符：期望 {total}，实际 {written}")
    clear_state(part)
    if progress is not None:
        with progress_lock:
            progress(total, total)
    return SegmentedResult(
        bytes_written=written,
        resumed_from=resumed_from,
        connections=max(1, min(connections, max(1, len(plan)))),
        segments=len(plan),
    )
