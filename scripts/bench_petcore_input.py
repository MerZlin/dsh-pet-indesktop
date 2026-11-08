"""Measure paint-to-input-region CPU cost, excluding native X server submission.

This portable microbenchmark is NOT a desktop acceptance or whole-app benchmark.
python scripts/bench_petcore_input.py --samples 1000 --out result.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 10:
        parser.error("--samples must be >=10")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import psutil
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication
    from petcore.config import Config
    from petcore.platform.input_region import RegionInputController, alpha_rectangles
    from petcore.ui.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    process = psutil.Process()
    results = []
    with tempfile.TemporaryDirectory(prefix="pc-input-") as directory:
        root = Path(directory)
        pet = PetWindow(Config(root), root, SimpleNamespace(characters=lambda: [], build_menu=lambda *_: None))
        source = QImage(str(ROOT / "assets/icon.ico"))
        assert not source.isNull()
        pet._image, pet._pixmap = source, QPixmap.fromImage(source)
        try:
            for size in (256, 512, 1024):
                pet.setFixedSize(size, size)
                alpha_rectangles(pet.input_image())  # warm NumPy/Qt allocations
                before = process.memory_info().rss
                cpu_start = time.process_time()
                durations = []
                count = 0
                for frame in range(args.samples):
                    pet._probe_angle = frame % 45
                    started = time.perf_counter_ns()
                    rectangles = alpha_rectangles(pet.input_image())
                    durations.append((time.perf_counter_ns() - started) / 1e6)
                    count += len(rectangles)
                results.append({"logical_size": size, "dpr": pet.devicePixelRatioF(), "samples": args.samples,
                                "mean_ms": statistics.mean(durations), "p95_ms": sorted(durations)[int(len(durations) * .95)],
                                "cpu_seconds": time.process_time() - cpu_start,
                                "rss_delta_bytes": process.memory_info().rss - before,
                                "mean_rectangles": count / args.samples})
            submissions = []
            backend = SimpleNamespace(apply=lambda *_: submissions.append(1), close=lambda: None)
            pet.show()
            app.processEvents()
            controller = RegionInputController(pet, backend=backend)
            controller.resume()
            initial = len(submissions)
            started = time.perf_counter_ns()
            for _ in range(10000):
                controller.refresh()
            cached = {"calls": 10000, "submissions": len(submissions) - initial,
                      "elapsed_ms": (time.perf_counter_ns() - started) / 1e6}
            controller.close()
        finally:
            pet.shutdown()
            pet.close()
    report = {"platform": platform.platform(), "python": sys.version, "qt": qVersion(), "qpa": app.platformName(),
              "samples": results, "cached": cached, "threads": process.num_threads(),
              "scope": "Qt paint + alpha rectangle generation; counter backend only; excludes XFixes/desktop compositor"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
