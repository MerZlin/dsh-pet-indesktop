"""Bounded, owned Phase 4B performance and real CPU-load evidence.

No production hooks, state writes, package execution, user-data enumeration, or
process discovery/termination. Frozen idle samples use only successful generated
validation fixtures; stress children belong to this invocation and exit on Event.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import psutil

from scripts.validate_feature_management_delivery import OwnedProcess


def summarize(values):
    values = sorted(values)
    if not values:
        raise ValueError("empty measurement sample")
    return {"samples": len(values), "median": statistics.median(values), "p95": values[math.ceil(len(values) * 0.95) - 1], "min": values[0], "max": values[-1]}


def summarize_phases(phases):
    groups = defaultdict(list)
    for phase in phases:
        groups[phase["name"]].append(phase)
    result = {}
    for name, items in sorted(groups.items()):
        metrics = {
            key: summarize([item[key] for item in items]) for key in ("duration_ms", "cpu_ms", "rss_before", "rss_after", "threads_before", "threads_after")
        }
        metrics["rss_delta"] = summarize([item["rss_after"] - item["rss_before"] for item in items])
        metrics["errors"] = dict(Counter(item["error"] for item in items if item["error"]))
        metrics["io"] = {key: summarize([item["io"].get(key, 0) for item in items]) for key in sorted({key for item in items for key in item["io"]})}
        result[name] = metrics
    return result


def snapshot(process):
    cpu = process.cpu_times()
    return {"cpu_seconds": cpu.user + cpu.system, "rss": process.memory_info().rss, "threads": process.num_threads(), "io": process.io_counters()._asdict()}


def phase_report(matrices):
    phases = []
    for path in matrices:
        matrix = json.loads(path.read_text(encoding="utf-8"))
        assert matrix["passed"] is True, "failed acceptance is not performance evidence"
        for variant in matrix["variants"]:
            assert variant["passed"] and len(variant["rows"]) == 7
            for step in variant["steps"]:
                evidence = json.loads(Path(step["evidence"]).read_text(encoding="utf-8"))
                assert evidence["validation_only"] and evidence["production_bootstrap"] and evidence["error"] is None
                phases.extend(evidence.get("performance_phases", ()))
    return {
        "phases": summarize_phases(phases),
        "note": "Nested methods overlap; do not sum. Parent I/O counters are not all kernel syscalls. Observer calibration must be reported separately.",
    }


def idle_report(args, output):
    matrix_path = args.matrix[0].resolve()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert matrix["passed"] and len(matrix["variants"]) == 1
    variant = matrix["variants"][0]["variant"]
    artifacts = json.loads(args.artifacts.read_text(encoding="utf-8"))
    assert artifacts["validation_only"] is True
    installed_data = matrix_path.parent / variant / "data"
    assert installed_data.is_dir(), "generated installed fixture missing"
    cwd = output / "empty-runtime"
    cwd.mkdir()
    results = []
    for state, data in (("empty", output / "empty-data"), ("installed", installed_data)):
        for index in range(args.samples):
            evidence = output / f"{state}-{index}.json"
            command = [artifacts["cores"][variant], "--mode", "manager", "--action", "inspect", "--hold", "--data-dir", str(data), "--evidence", str(evidence)]
            child = OwnedProcess(command, cwd=cwd, output=output / f"{state}-{index}.log")
            try:
                child.ready()
                ready = json.loads(evidence.read_text(encoding="utf-8"))
                assert ready["error"] is None and not ready["loaded_feature_modules"], "management-only must not import feature"
                assert not ready["state"]["pending_transaction"]
                process = psutil.Process(child.process.pid)
                before = snapshot(process)
                started = time.perf_counter()
                # Measurement interval, not a guessed synchronization sleep.
                threading.Event().wait(args.seconds)
                assert child.process.poll() is None
                after = snapshot(process)
                elapsed = time.perf_counter() - started
                results.append(
                    {
                        "state": state,
                        "index": index,
                        "command": command,
                        "elapsed_seconds": elapsed,
                        "cpu_percent_one_core": 100 * (after["cpu_seconds"] - before["cpu_seconds"]) / elapsed,
                        "rss_before": before["rss"],
                        "rss_after": after["rss"],
                        "rss_delta": after["rss"] - before["rss"],
                        "threads_before": before["threads"],
                        "threads_after": after["threads"],
                        "io": {key: after["io"][key] - before["io"][key] for key in before["io"]},
                    }
                )
            finally:
                child.finish(natural_quit=True)
    return {"variant": variant, "idle_samples": results}


def burn_cpu(stop, ready):
    payload = b"owned phase4b cpu-load fixture" * 4096
    ready.put(os.getpid())
    while not stop.is_set():
        for _ in range(32):
            hashlib.sha256(payload).digest()


def stress_report(args, output):
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    ready = context.Queue()
    workers = [context.Process(target=burn_cpu, args=(stop, ready)) for _ in range(os.cpu_count() or 1)]
    sample_stop = threading.Event()
    cpu_samples = []
    passes = []

    def sample():
        while not sample_stop.is_set():
            cpu_samples.append((time.perf_counter(), psutil.cpu_percent(interval=1)))

    monitor = threading.Thread(target=sample, daemon=True)
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            ready.get(timeout=90)
        monitor.start()
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        for index in range(1, 4):
            command = [sys.executable, "-m", "pytest", "-q", *args.tests, "--basetemp", str(output / f"pytest-{index}")]
            started = time.perf_counter()
            with (output / f"pass-{index}.log").open("wb") as log:
                completed = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=1200, check=False)
            ended = time.perf_counter()
            samples = [value for when, value in cpu_samples if started <= when <= ended]
            passes.append(
                {"pass": index, "command": command, "returncode": completed.returncode, "seconds": ended - started, "cpu_percent": summarize(samples)}
            )
            print(json.dumps(passes[-1]), flush=True)
            if completed.returncode:
                raise RuntimeError(f"high-load pass {index} failed; read exact log before repair")
        return {"worker_count": len(workers), "passes": passes, "passed": True}
    finally:
        stop.set()
        sample_stop.set()
        if monitor.is_alive():
            monitor.join(timeout=3)
        for worker in workers:
            if worker.pid is not None:
                worker.join(timeout=20)
                assert not worker.is_alive(), "owned CPU fixture did not stop"
        ready.close()
        ready.join_thread()
        (output / "stress-progress.json").write_text(json.dumps({"worker_count": len(workers), "passes": passes}, indent=2), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("phases", "idle", "stress"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, nargs="+", default=[])
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--tests", nargs="+", default=[])
    args = parser.parse_args(argv)
    assert 1 <= args.samples <= 10 and 1 <= args.seconds <= 30, "bounded measurements required"
    assert args.mode != "stress" or args.tests, "explicit affected test scope required"
    assert args.mode == "stress" or args.matrix, "successful generated matrix required"
    assert args.mode != "idle" or args.artifacts, "validation artifacts required"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "psutil": psutil.__version__, "logical_cpus": os.cpu_count()},
        "mode": args.mode,
        "passed": False,
    }
    try:
        report["measurements"] = (
            phase_report(args.matrix) if args.mode == "phases" else idle_report(args, output) if args.mode == "idle" else stress_report(args, output)
        )
        report["passed"] = True
    except Exception as error:
        report["error"] = type(error).__name__ + ":" + str(error)
        raise
    finally:
        (output / "performance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
