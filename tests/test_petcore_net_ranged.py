"""分段下载引擎（纯逻辑）：切段、每段续传、装配、取消与进度。

引擎本身不碰 urllib；这里全部用回调替身，验证的是磁盘状态与调用序列。
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from petcore.market.errors import TransferCancelled
from petcore.net import ranged

PAYLOAD = bytes((index * 37 + 11) % 251 for index in range(64 * 1024))
TOTAL = len(PAYLOAD)


def _range_fetch(payload: bytes, log: list[tuple[int, int]], *, short: int = 0):
    """把 ``payload[start:stop]`` 写进 sink；``short`` 让最后一次少写一点。"""

    def fetch(start: int, stop: int, sink) -> None:
        log.append((start, stop))
        block = payload[start:stop]
        if short:
            block = block[:-short]
        sink(block)
        if short:
            raise ranged.SegmentationError("short read")

    return fetch


def test_should_segment_needs_several_connections_and_real_size() -> None:
    assert ranged.should_segment(ranged.MIN_SEGMENTED_BYTES, 4) is True
    assert ranged.should_segment(ranged.MIN_SEGMENTED_BYTES - 1, 4) is False
    assert ranged.should_segment(64 * 1024 * 1024, 1) is False
    assert ranged.should_segment(64 * 1024 * 1024, ranged.normalize_connections(99)) is True


def test_normalize_connections_clamps_to_the_supported_band() -> None:
    assert ranged.normalize_connections(0) == 1
    assert ranged.normalize_connections(-3) == 1
    assert ranged.normalize_connections("4") == 4
    assert ranged.normalize_connections(None) == 1
    assert ranged.normalize_connections(99) == ranged.MAX_CONNECTIONS
    assert ranged.normalize_connections(3.6) == 4


def test_plan_segments_covers_every_byte_exactly_once(tmp_path: Path) -> None:
    plan = ranged.plan_segments(TOTAL, tmp_path / "a.zip.part", segment_bytes=4096, connections=4)

    assert plan[0].start == 0
    assert plan[-1].stop == TOTAL
    for previous, current in zip(plan, plan[1:]):
        assert previous.stop == current.start
    assert sum(segment.length for segment in plan) == TOTAL
    assert [segment.index for segment in plan] == list(range(len(plan)))
    assert plan[0].range_header == f"bytes=0-{plan[0].length - 1}"
    assert plan[0].path.name.startswith("a.zip.part.seg")


def test_plan_segments_gives_each_connection_work_even_for_medium_files(tmp_path: Path) -> None:
    total = ranged.MIN_SEGMENTED_BYTES + 1024 * 1024
    plan = ranged.plan_segments(total, tmp_path / "a.part", connections=4)

    assert len(plan) >= 4


def test_plan_segments_handles_empty_and_negative_totals(tmp_path: Path) -> None:
    assert ranged.plan_segments(0, tmp_path / "a.part", connections=4) == ()
    assert ranged.plan_segments(-5, tmp_path / "a.part", connections=4) == ()


def test_engine_module_has_no_network_imports() -> None:
    """纯引擎：分段/续传/装配不许把 urllib/socket 带进来。"""
    tree = ast.parse(Path(ranged.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & {"urllib", "socket", "http", "requests", "aiohttp", "httpx"}, roots


def test_sidecar_roundtrip_and_mismatch(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"
    part.write_bytes(b"")
    ranged.write_state(
        part,
        size=TOTAL,
        sha256="a" * 64,
        segment_bytes=4096,
        connections=4,
        segments={0: ranged.SegmentState(bytes=10, url="https://mirror/a.zip")},
    )

    state = ranged.read_state(part, size=TOTAL, sha256="a" * 64)
    assert state is not None
    assert state.segment_bytes == 4096
    assert state.connections == 4
    assert state.segments[0] == ranged.SegmentState(bytes=10, url="https://mirror/a.zip")
    assert ranged.read_state(part, size=TOTAL, sha256="b" * 64) is None
    assert ranged.read_state(part, size=TOTAL + 1, sha256="a" * 64) is None
    assert ranged.read_state(tmp_path / "missing.part", size=TOTAL) is None

    raw = json.loads(ranged.sidecar_path(part).read_text(encoding="utf-8"))
    assert raw["size"] == TOTAL
    assert raw["segment_bytes"] == 4096
    assert raw["connections"] == 4


def test_clear_state_removes_the_sidecar_and_every_segment(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"
    part.write_bytes(b"x")
    for index in range(3):
        Path(f"{part}.seg{index}").write_bytes(b"y")
    ranged.write_state(
        part,
        size=TOTAL,
        sha256="",
        segment_bytes=4096,
        connections=2,
        segments={},
    )

    ranged.clear_state(part)

    assert part.read_bytes() == b"x", "清理状态不许动 .part 本身"
    assert not ranged.sidecar_path(part).exists()
    assert not list(tmp_path.glob("a.zip.part.seg*"))


def test_download_segmented_assembles_in_order_and_cleans_up(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"
    log: list[tuple[int, int]] = []
    progress: list[tuple[int, int]] = []

    result = ranged.download_segmented(
        part,
        total=TOTAL,
        sha256="c" * 64,
        connections=4,
        segment_bytes=4096,
        url="https://mirror/a.zip",
        fetch=_range_fetch(PAYLOAD, log),
        progress=lambda done, total: progress.append((done, total)),
    )

    assert part.read_bytes() == PAYLOAD
    assert result.bytes_written == TOTAL
    assert result.resumed_from == 0
    assert len(log) == result.segments >= 4
    assert sorted(start for start, _stop in log) == [segment.start for segment in ranged.plan_segments(TOTAL, part, segment_bytes=4096, connections=4)]
    assert not list(tmp_path.glob("*.seg*"))
    assert not ranged.sidecar_path(part).exists()
    assert progress[-1] == (TOTAL, TOTAL)
    assert all(previous[0] <= current[0] for previous, current in zip(progress, progress[1:]))


def test_download_segmented_resumes_each_segment_from_its_own_offset(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"
    plan = ranged.plan_segments(TOTAL, part, segment_bytes=4096, connections=4)
    # 前两段各留 1000 字节，其余为空；状态里记下同一来源。
    for segment in plan:
        Path(segment.path).write_bytes(PAYLOAD[segment.start : segment.start + 1000])
    ranged.write_state(
        part,
        size=TOTAL,
        sha256="c" * 64,
        segment_bytes=4096,
        connections=4,
        segments={segment.index: ranged.SegmentState(bytes=1000, url="https://mirror/a.zip") for segment in plan},
    )

    log: list[tuple[int, int]] = []
    result = ranged.download_segmented(
        part,
        total=TOTAL,
        sha256="c" * 64,
        segment_bytes=4096,
        connections=4,
        url="https://mirror/a.zip",
        fetch=_range_fetch(PAYLOAD, log),
    )

    assert part.read_bytes() == PAYLOAD
    assert result.resumed_from == 1000 * len(plan)
    assert sorted(start for start, _stop in log) == sorted(segment.start + 1000 for segment in plan)


def test_segment_from_another_source_is_downloaded_whole_again(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"
    plan = ranged.plan_segments(TOTAL, part, segment_bytes=4096, connections=4)
    first = plan[0]
    Path(first.path).write_bytes(b"z" * 1000)
    ranged.write_state(
        part,
        size=TOTAL,
        sha256="c" * 64,
        segment_bytes=4096,
        connections=4,
        segments={0: ranged.SegmentState(bytes=1000, url="https://mirror/other.zip")},
    )

    log: list[tuple[int, int]] = []
    result = ranged.download_segmented(
        part,
        total=TOTAL,
        sha256="c" * 64,
        segment_bytes=4096,
        connections=4,
        url="https://mirror/a.zip",
        fetch=_range_fetch(PAYLOAD, log),
    )

    assert (0, first.stop) in log, "换了来源的段必须整段重下"
    assert part.read_bytes() == PAYLOAD
    assert result.resumed_from == 0


def test_download_segmented_honours_cancellation_and_keeps_resumable_state(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"
    cancelled = threading.Event()
    written = {"bytes": 0}

    def fetch(start: int, stop: int, sink) -> None:
        for offset in range(start, stop, 512):
            if cancelled.is_set():
                raise TransferCancelled("download cancelled")
            block = PAYLOAD[offset : min(offset + 512, stop)]
            sink(block)
            written["bytes"] += len(block)
        if written["bytes"] >= 2 * 512 * 4:
            cancelled.set()

    with pytest.raises(TransferCancelled):
        ranged.download_segmented(
            part,
            total=TOTAL,
            sha256="c" * 64,
            connections=2,
            segment_bytes=4096,
            url="https://mirror/a.zip",
            fetch=fetch,
            cancel=cancelled.is_set,
        )

    assert not part.exists()
    state = ranged.read_state(part, size=TOTAL, sha256="c" * 64)
    assert state is not None
    assert any(item.bytes > 0 for item in state.segments)
    assert any(Path(segment.path).exists() for segment in ranged.plan_segments(TOTAL, part, segment_bytes=4096, connections=2))

    resumed = ranged.download_segmented(
        part,
        total=TOTAL,
        sha256="c" * 64,
        connections=2,
        segment_bytes=4096,
        url="https://mirror/a.zip",
        fetch=_range_fetch(PAYLOAD, []),
    )
    assert resumed.resumed_from > 0
    assert part.read_bytes() == PAYLOAD


def test_short_segment_is_reported_instead_of_assembled(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"

    with pytest.raises(ranged.SegmentationError):
        ranged.download_segmented(
            part,
            total=TOTAL,
            sha256="c" * 64,
            connections=2,
            segment_bytes=4096,
            url="https://mirror/a.zip",
            fetch=_range_fetch(PAYLOAD, [], short=16),
        )

    assert not part.exists()


def test_segment_errors_propagate_and_close_every_file(tmp_path: Path) -> None:
    part = tmp_path / "a.zip.part"

    def fetch(_start: int, _stop: int, _sink) -> None:
        raise OSError("connection reset")

    with pytest.raises(OSError):
        ranged.download_segmented(
            part,
            total=TOTAL,
            sha256="c" * 64,
            connections=4,
            segment_bytes=4096,
            url="https://mirror/a.zip",
            fetch=fetch,
        )

    assert not part.exists(), "失败时不许留下装配好的文件"
    # 允许留着已写的段文件（resume），但不许留半截 .part。
    assert not list(tmp_path.glob("*.part"))
    for segment in ranged.plan_segments(TOTAL, part, segment_bytes=4096, connections=4):
        if Path(segment.path).exists():
            Path(segment.path).unlink()
    ranged.clear_state(part)
