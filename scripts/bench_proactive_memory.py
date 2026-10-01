# -*- coding: utf-8 -*-
"""主动记忆落盘的修复前后开销 A/B（issue #196 / #204 的性能证据）。

用法（仓库根目录）：
    python scripts/bench_proactive_memory.py [n]

对照物是「修复前实现」在本文件内逐行复刻的 ``_legacy_record()``（固定
``.json.tmp`` 名、无锁、无 fsync、无重试、失败静默），因此不需要回退代码即可
复测；两条路径跑在同一进程、同一目录、同一块盘上的同一批 n 次记录，并且各自
预写 20 条让文件到达 max_entries 的稳态大小。

度量：单次 record 的墙钟耗时（mean / p50 / p95 / max，毫秒）与进程 RSS 增长。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.proactive_memory import ProactiveMemory  # noqa: E402


def _legacy_record(path: Path, process: str, title: str, activity: str, max_entries: int = 20) -> None:
    """pet/proactive_memory.py 修复前 record() 的逐行复刻（基线对照）。"""
    entries: list[dict] = []
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                entries = raw["entries"]
        except (OSError, ValueError, TypeError):
            pass
    entries.insert(0, {"ts": time.time(), "process": process, "activity": activity})
    entries = entries[:max_entries]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass


def _measure(label: str, record, count: int) -> dict:
    samples: list[float] = []
    for index in range(count):
        start = time.perf_counter()
        record(index)
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    return {
        "label": label,
        "n": count,
        "mean": statistics.fmean(samples),
        "p50": samples[len(samples) // 2],
        "p95": samples[min(len(samples) - 1, int(len(samples) * 0.95))],
        "max": samples[-1],
    }


def _rss_mb() -> float:
    try:
        import psutil
    except Exception:
        return float("nan")
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _micro_fsync(base: Path):
    """开销归因①：只做「写小文件 + flush + fsync」（不含锁与 replace）。"""
    path = base / "fsync-probe.bin"
    payload = "x" * 2048

    def run(index: int) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    return run


def _micro_lock(memory: ProactiveMemory):
    """开销归因②：只做一次「拿锁 + 放锁」（不含任何落盘）。"""

    def run(index: int) -> None:
        with memory._locked():
            pass

    return run


def main(argv: list[str]) -> int:
    count = int(argv[1]) if len(argv) > 1 else 300
    print(f"环境: {sys.platform} / Python {sys.version.split()[0]} / 样本量 n={count}")
    with tempfile.TemporaryDirectory(prefix="bench-proactive-") as tmp:
        base = Path(tmp)
        legacy_path = base / "proactive_screen_memory.json"
        new_path = base / "proactive_screen_memory_fixed.json"
        memory = ProactiveMemory(new_path)
        for index in range(20):  # 各自预热到稳态（max_entries=20 的满文件）
            _legacy_record(legacy_path, "Code.exe", "t", f"warm-{index}")
            memory.record("Code.exe", "t", f"warm-{index}")

        rss_before = _rss_mb()
        rows = [
            _measure("修复前（无锁/固定 tmp/无 fsync）", lambda i: _legacy_record(legacy_path, "Code.exe", "t", f"act-{i}"), count),
            _measure("修复后（锁+唯一 tmp+fsync+重试）", lambda i: memory.record("Code.exe", "t", f"act-{i}"), count),
            _measure("  ├ 归因①：仅 fsync（2KB 文件）", _micro_fsync(base), count),
            _measure("  └ 归因②：仅抢锁+放锁", _micro_lock(memory), count),
        ]
        rss_after = _rss_mb()

        print(f"\n{'路径':<36}{'n':>5}{'mean':>10}{'p50':>10}{'p95':>10}{'max':>10}  (ms/次)")
        for row in rows:
            print(
                f"{row['label']:<36}{row['n']:>5}"
                f"{row['mean']:>10.3f}{row['p50']:>10.3f}{row['p95']:>10.3f}{row['max']:>10.3f}"
            )
        delta = rows[1]["mean"] - rows[0]["mean"]
        print(f"\n单次净增: {delta:+.3f} ms（{delta / rows[0]['mean'] * 100:+.0f}%）")
        print(f"RSS: {rss_before:.1f} MB → {rss_after:.1f} MB（{rss_after - rss_before:+.2f} MB / {2 * count} 次写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
