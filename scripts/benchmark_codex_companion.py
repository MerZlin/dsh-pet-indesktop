"""Measure bounded history and read-only work scans; never starts a model turn."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def measure(function, count=30):
    times = []
    cpu = time.process_time()
    for _ in range(count):
        start = time.perf_counter()
        function()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "n": count,
        "medianMs": round(statistics.median(times), 3),
        "p95Ms": round(sorted(times)[int(count * 0.95) - 1], 3),
        "cpuMsPerCall": round((time.process_time() - cpu) * 1000 / count, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import psutil
    from pet.codex_usage_history import UsageHistory
    from pet.codex_work_reader import CodexWorkReader

    process = psutil.Process()
    result = {"platform": sys.platform, "python": sys.version.split()[0], "cpuLogicalCores": psutil.cpu_count(), "modelTurns": 0}
    with tempfile.TemporaryDirectory(dir=args.output) as directory:
        history = UsageHistory(Path(directory) / "history.json")
        now = time.time()
        # Nine days of once-a-minute samples, with the normal two quota windows.
        history.samples = [
            {
                "t": now - (12960 - index) * 60,
                "rows": [
                    {"limitId": "codex", "window": "primary", "used": (index % 300) / 3, "reset": now + 3600, "minutes": 300},
                    {"limitId": "codex", "window": "secondary", "used": (index % 10080) / 100.8, "reset": now + 86400, "minutes": 10080},
                ],
            }
            for index in range(12960)
        ]
        result["historySave"] = measure(history.save, 20)
        result["historyFileBytes"] = history.path.stat().st_size
        result["historySamples"] = len(history.samples)
        result["fiveHourCalculation"] = measure(lambda: history.five_hour({"limitId": "codex", "window": "primary"}, now=now))
        result["weeklyCalculation"] = measure(lambda: history.daily({"limitId": "codex", "window": "secondary"}, now=now))
        result["historyLoad"] = measure(lambda: UsageHistory(history.path), 20)
    reader = CodexWorkReader()
    first = reader.read()
    if not first["ok"]:
        raise RuntimeError("This probe requires local Codex metadata and thread-history databases")
    result["localWorkRows"] = len(first["rows"])
    result["workScan"] = measure(reader.read, 100)
    warm = process.memory_info().rss
    result["workScanRepeated"] = measure(reader.read, 300)
    result["workScanRssChangeBytes"] = process.memory_info().rss - warm
    result["workCursorCount"] = len(reader.cursors)
    result["networkCallsForWorkStatus"] = 0
    (args.output / "benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
