"""Measure bounded snapshot reads and hook subprocess costs using temporary data."""

import json, runpy, statistics, subprocess, tempfile, time, tracemalloc
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.agent_snapshot import SnapshotReader

bridge = runpy.run_path("integrations/dsh-pet-codex/scripts/bridge.py")
with tempfile.TemporaryDirectory(prefix="pet-bench-") as folder:
    home = Path(folder)
    root = home / "dsh-pet-bridge"
    for i in range(16):
        p = home / f"{i}.jsonl"
        p.touch()
        bridge["record"](dict(hook_event_name="UserPromptSubmit", session_id=str(i), turn_id="t", transcript_path=str(p)), root)
    with (home / "session_index.jsonl").open("w") as f:
        for i in range(16):
            f.write(json.dumps(dict(id=str(i), thread_name=f"session-{i}")) + "\n")
    reader = SnapshotReader(root / "state.json")
    reader.read()
    samples = []
    for _ in range(2000):
        begin = time.perf_counter_ns()
        reader.read()
        samples.append((time.perf_counter_ns() - begin) / 1000)
    print("16 active sessions unchanged poll, n=2000, us median/p95", round(statistics.median(samples), 2), round(sorted(samples)[1899], 2))
    tracemalloc.start()
    reader.read()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(2000):
        reader.read()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print("additional 2000 polls traced retained/peak bytes", current - before, peak - before)
    payload = json.dumps(dict(hook_event_name="PreToolUse", session_id="0", turn_id="t", tool_use_id="x", tool_name="Bash"))
    import os, sys

    samples = []
    for _ in range(30):
        begin = time.perf_counter_ns()
        subprocess.run(
            [sys.executable, "integrations/dsh-pet-codex/scripts/bridge.py"],
            input=payload,
            text=True,
            stdout=subprocess.DEVNULL,
            check=True,
            env={**os.environ, "DSH_PET_BRIDGE_DIR": str(root)},
        )
        samples.append((time.perf_counter_ns() - begin) / 1e6)
    print("hook subprocess n=30 ms median/p95", round(statistics.median(samples), 2), round(sorted(samples)[28], 2))
    print("snapshot bytes", (root / "state.json").stat().st_size)
