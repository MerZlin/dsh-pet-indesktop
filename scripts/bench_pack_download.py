"""素材包下载加速对比：同一文件分别用 1 / 4 连接各下一次。

PCL 式分段下载的收益取决于服务端是否给足带宽；只有实测数字能证明它在这个
网络、这个镜像上真的更快。每轮都删掉目标文件（``--keep`` 可保留），
默认跑 2 轮取中位数。

用法::

    python scripts/bench_pack_download.py --url <release 资产地址> \
        --size 132036744 --sha256 c6d17c49... --out .scratch/pack-bench.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _entry(url: str, size: int, sha256: str, filename: str):
    from petcore.net.packs import PackEntry

    return PackEntry(
        id="bench",
        name="bench",
        version="1",
        filename=filename,
        size=int(size),
        sha256=str(sha256).lower(),
        character="bench",
        urls=(url,),
        links={},
        notes="",
    )


def run_once(entry, dest: Path, connections: int, *, timeout: float) -> dict:
    from petcore.net import packs

    samples: list[tuple[float, int]] = []
    started = time.perf_counter()
    target = dest / entry.filename
    target.unlink(missing_ok=True)
    for suffix in (".part", ".part.json"):
        (dest / (entry.filename + suffix)).unlink(missing_ok=True)

    def progress(done: int, total: int) -> None:
        samples.append((time.perf_counter() - started, int(done)))

    result = packs.download_pack(
        entry,
        dest,
        connections=connections,
        timeout=timeout,
        progress=progress,
    )
    elapsed = time.perf_counter() - started
    size = result.path.stat().st_size
    # 峰值速度：相邻采样点之间 0.2 秒以上的窗口
    peak = 0.0
    for index in range(1, len(samples)):
        dt = samples[index][0] - samples[index - 1][0]
        if dt >= 0.2:
            peak = max(peak, (samples[index][1] - samples[index - 1][1]) / dt)
    target.unlink(missing_ok=True)
    return {
        "connections": int(result.connections or connections),
        "seconds": round(elapsed, 3),
        "bytes": int(size),
        "avg_mib_s": round(size / (1024 * 1024) / max(elapsed, 0.001), 2),
        "peak_mib_s": round(peak / (1024 * 1024), 2),
        "from_cache": bool(result.from_cache),
        "samples": len(samples),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_pack_download")
    parser.add_argument("--url", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--filename", default="bench-pack.zip")
    parser.add_argument("--out", type=Path, default=Path(".scratch/pack-bench.json"))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--connections", default="1,4")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dest", type=Path, default=Path(".scratch/pack-bench"))
    args = parser.parse_args(argv)

    entry = _entry(args.url, args.size, args.sha256, args.filename)
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    plan = [int(item) for item in str(args.connections).split(",") if item.strip()]
    report: dict[str, object] = {"url": args.url, "size": args.size, "rounds": args.rounds, "runs": {}}
    for connections in plan:
        runs = []
        for index in range(max(1, args.rounds)):
            print(f"[{connections} 连接] 第 {index + 1} 轮…", flush=True)
            try:
                run = run_once(entry, dest, connections, timeout=args.timeout)
            except Exception as exc:  # noqa: BLE001 - 脚本要如实报失败
                run = {"error": f"{type(exc).__name__}: {exc}"}
            print("  " + json.dumps(run, ensure_ascii=False), flush=True)
            runs.append(run)
        report["runs"][str(connections)] = runs
        good = [run for run in runs if "error" not in run]
        if good:
            report.setdefault("median", {})[str(connections)] = {
                "seconds": statistics.median(run["seconds"] for run in good),
                "avg_mib_s": statistics.median(run["avg_mib_s"] for run in good),
                "peak_mib_s": statistics.median(run["peak_mib_s"] for run in good),
            }
    medians = report.get("median", {})
    if "1" in medians and str(max(plan)) in medians:
        base = medians["1"]["avg_mib_s"]
        multi = medians[str(max(plan))]["avg_mib_s"]
        report["speedup"] = round(multi / base, 2) if base else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
