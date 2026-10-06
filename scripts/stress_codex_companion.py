"""Run the affected timing families three times under measured CPU saturation."""

import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time


def burn(stop, ready):
    ready.set()
    value = 1
    while not stop.is_set():
        for _ in range(10000):
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tests", nargs="+", help="Affected timing-family test paths; defaults to the original companion gate")
    args = parser.parse_args()
    import psutil

    args.output.mkdir(parents=True, exist_ok=True)
    context = mp.get_context("spawn")
    stop = context.Event()
    workers, ready = [], []
    rounds = []
    cpu_samples = []
    try:
        for _ in range(psutil.cpu_count() or 2):
            event = context.Event()
            worker = context.Process(target=burn, args=(stop, event))
            worker.start()
            workers.append(worker)
            ready.append(event)
        assert all(event.wait(60) for event in ready), "Load workers did not initialize"
        root = Path(__file__).resolve().parents[1]
        for index in range(3):
            cpu_samples.append(psutil.cpu_percent(interval=0.5))
            command = [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "pytest",
                "-q",
                *(args.tests or ["tests/test_codex_companion.py", "tests/test_codex_work_reader.py", "tests/test_codex_bridge.py",
                                 "tests/test_settings_process_isolation.py", "tests/test_codex_autostart.py", "tests/test_autostart.py"]),
                "--basetemp",
                str(args.output / f"pytest-round-{index + 1}"),
            ]
            started = time.perf_counter()
            with (args.output / f"round-{index + 1}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    command,
                    cwd=root,
                    env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            rounds.append({"round": index + 1, "returncode": result.returncode, "seconds": round(time.perf_counter() - started, 2)})
            print(json.dumps(rounds[-1]), flush=True)
            if result.returncode:
                break  # Diagnose the first failed round; do not gamble on reruns.
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=15)
    value = {
        "workers": len(workers),
        "cpuPercentBeforeRounds": cpu_samples,
        "rounds": rounds,
        "allThreePassed": len(rounds) == 3 and all(row["returncode"] == 0 for row in rounds),
        "modelTurns": 0,
    }
    (args.output / "stress.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    print(json.dumps(value), flush=True)
    return 0 if value["allThreePassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
