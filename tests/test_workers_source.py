# -*- coding: utf-8 -*-
"""Bounded source and tailer tests for the Agent Link Worker."""

from __future__ import annotations

import json
from pathlib import Path

from pet.workers.agent_link_source import AgentLinkEventSource, ByteOffsetTailer


def _write_records(path: Path, count: int) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for index in range(count):
            stream.write(json.dumps({"event": "tool/call", "tool": f"tool-{index}"}) + "\n")


def test_byte_offset_tailer_max_lines_preserves_backlog(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    _write_records(events_file, 10)
    tailer = ByteOffsetTailer(events_file)
    tailer._initial_backfill_done = True

    first = tailer.read_new_lines(max_lines=3)
    second = tailer.read_new_lines(max_lines=3)
    rest = tailer.read_new_lines(max_lines=10)

    assert [json.loads(line)["tool"] for line in first + second + rest] == [f"tool-{index}" for index in range(10)]


def test_source_prime_discards_existing_backlog_but_keeps_following_records(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    _write_records(events_file, 2)
    source = AgentLinkEventSource(
        [{"agent_key": "dsh", "path": str(events_file), "kind": "file"}],
        max_events_per_poll=3,
    )

    source.prime()
    assert source.poll() == []

    with events_file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "tool/call", "tool": "new-tool"}) + "\n")

    batch = source.poll()
    assert [item["record"]["tool"] for item in batch] == ["new-tool"]


def test_source_poll_caps_batch_without_dropping_records(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    _write_records(events_file, 10)
    source = AgentLinkEventSource(
        [{"agent_key": "dsh", "path": str(events_file), "kind": "file"}],
        generation=7,
        max_events_per_poll=3,
    )
    next(iter(source._tailers.values()))._initial_backfill_done = True

    batches = []
    for _ in range(10):
        batch = source.poll()
        if not batch:
            break
        batches.append(batch)

    tools = [item["record"]["tool"] for batch in batches for item in batch]
    assert all(len(batch) <= 3 for batch in batches)
    assert tools == [f"tool-{index}" for index in range(10)]
    assert any(item["generation"] == 7 for batch in batches for item in batch)


def test_source_reports_sparse_backpressure_diagnostic(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    _write_records(events_file, 600)
    source = AgentLinkEventSource(
        [{"agent_key": "dsh", "path": str(events_file), "kind": "file"}],
        max_events_per_poll=3,
    )
    next(iter(source._tailers.values()))._initial_backfill_done = True

    while source.poll():
        pass

    diagnostics = source.drain_diagnostics()
    assert any(item["stage"] == "backpressure" for item in diagnostics)
