"""Measure local todo-agent queue admission without making network requests."""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import tracemalloc
from datetime import datetime, timedelta
from types import SimpleNamespace

import psutil

import pet.chat.providers as chat_providers
from pet.todo_agent import TodoAgent, _format_schedule_context, parse_todo_response
from pet.todo_reminder import TODO_ITEMS_LIMIT


class _ProbeProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.condition = threading.Condition()

    def stream(self, *_args, **_kwargs):
        with self.condition:
            self.calls += 1
            self.condition.notify_all()
        return iter(())


class _ProbeConfig:
    def chat_settings(self):
        provider = SimpleNamespace(
            name="Probe",
            model="no-network",
            timeout=60.0,
            max_tokens=2048,
            api_key="",
        )
        return SimpleNamespace(active_config=provider)

    def resolve_api_key(self, _provider):
        return "probe-only"


class _AlreadyRunningWorker:
    def is_alive(self) -> bool:
        return True


def _thread_cpu_seconds(thread_id: int) -> float:
    thread = next(
        thread for thread in psutil.Process().threads() if thread.id == thread_id
    )
    return thread.user_time + thread.system_time


def benchmark(samples: int, idle_seconds: float) -> None:
    provider = _ProbeProvider()
    chat_providers.OpenAICompatibleProvider = lambda: provider
    agent = TodoAgent(_ProbeConfig(), lambda *_args: None)
    today = datetime.now().date()
    existing_todos = [
        {
            "kind": "once",
            "date": (today + timedelta(days=index % 14 + 1)).isoformat(),
            "time": f"{9 + index % 9:02d}:00",
            "enabled": True,
        }
        for index in range(TODO_ITEMS_LIMIT)
    ]
    latencies_ms = []

    for sample in range(samples):
        with provider.condition:
            expected_calls = provider.calls + 1
        started = time.perf_counter_ns()
        accepted = agent.submit(
            str(sample), "明天上午十点提交周报", existing_todos=existing_todos
        )
        latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        if not accepted:
            raise RuntimeError(f"request {sample} was not accepted")
        with provider.condition:
            consumed = provider.condition.wait_for(
                lambda: provider.calls >= expected_calls, timeout=2.0
            )
        if not consumed:
            raise TimeoutError(f"worker did not consume request {sample}")

    worker = agent._thread
    if worker is None or worker.native_id is None:
        raise RuntimeError("todo-agent worker did not start")
    cpu_before = _thread_cpu_seconds(worker.native_id)
    time.sleep(max(0.0, idle_seconds))
    cpu_after = _thread_cpu_seconds(worker.native_id)
    agent.shutdown()

    ordered = sorted(latencies_ms)
    p95_index = max(0, int(0.95 * len(ordered)) - 1)
    print(
        f"submit samples={samples} median_ms={statistics.median(ordered):.4f} "
        f"p95_ms={ordered[p95_index]:.4f} max_ms={max(ordered):.4f}"
    )
    print(
        f"idle seconds={idle_seconds:.1f} thread_cpu_delta_seconds="
        f"{cpu_after - cpu_before:.4f} network_calls=0"
    )

    queued_agent = TodoAgent(_ProbeConfig(), lambda *_args: None)
    queued_agent._thread = _AlreadyRunningWorker()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    queued = sum(
        queued_agent.submit(
            str(index), "x" * 8000, existing_todos=existing_todos
        )
        for index in range(queued_agent._queue.maxsize)
    )
    after = tracemalloc.take_snapshot()
    heap_delta = sum(
        stat.size_diff for stat in after.compare_to(before, "filename")
    )
    tracemalloc.stop()
    print(
        f"queue accepted={queued} queued_chars={queued * 8000} "
        f"snapshot_items={len(existing_todos)} heap_delta_bytes={heap_delta} "
        f"capacity={queued_agent._queue.maxsize}"
    )
    queued_agent._closed = True

    response = json.dumps({
        "todos": [{
            "title": "新事项",
            "kind": "once",
            "date": "",
            "date_is_explicit": False,
            "time": "",
            "time_is_explicit": False,
        }]
    })
    parse_latencies_ms = []
    for _sample in range(samples):
        started = time.perf_counter_ns()
        parsed = parse_todo_response(response, existing_todos=existing_todos)
        parse_latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        if not parsed:
            raise RuntimeError("missing-time parser did not find a slot")
    parse_ordered = sorted(parse_latencies_ms)
    print(
        f"missing-time parse samples={samples} snapshot_items={len(existing_todos)} "
        f"context_chars={len(_format_schedule_context(existing_todos, datetime.now()))} "
        f"median_ms={statistics.median(parse_ordered):.4f} "
        f"p95_ms={parse_ordered[p95_index]:.4f} max_ms={max(parse_ordered):.4f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--idle-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    benchmark(args.samples, args.idle_seconds)


if __name__ == "__main__":
    main()
