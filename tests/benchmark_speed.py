# -*- coding: utf-8 -*-
"""
QMovie 播放速度实测 —— 判断 PLAYBACK_SPEED 补偿对当前素材是否合适。

原理：start() 后记录每帧到达的墙上时间，算平均帧间隔/帧率。
在真实 Windows（非 offscreen）运行最准；无窗口、不影响屏幕。

运行：python tests/benchmark_speed.py [动画名或文件名]
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QMovie  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.library import PLAYBACK_SPEED  # noqa: E402


def benchmark(path: str, speed: int, seconds: float = 3.0) -> None:
    app = QApplication.instance() or QApplication([])
    movie = QMovie(path)
    movie.setCacheMode(QMovie.CacheMode.CacheNone)
    movie.setSpeed(speed)

    timestamps: list[float] = []
    movie.frameChanged.connect(lambda _n: timestamps.append(time.monotonic()))
    movie.start()

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    movie.stop()

    if len(timestamps) < 3:
        print(f'speed={speed}: 帧数过少（{len(timestamps)}），无法计算')
        return
    intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    avg_ms = sum(intervals) / len(intervals) * 1000
    fps = 1000 / avg_ms
    print(f'speed={speed:3d}%  帧数={len(timestamps):3d}  平均帧间隔={avg_ms:5.1f}ms  实际帧率={fps:4.1f}fps  '
          f'(素材原生 {catalog.FRAME_MS}ms/{1000/catalog.FRAME_MS:.0f}fps)')


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else catalog.IDLE
    fname = catalog.ANIM_FILES.get(name, name)
    path = os.path.join(catalog.assets_dir(), fname)
    if not os.path.exists(path):
        print(f'素材不存在: {path}')
        return 1
    print(f'素材: {fname}')
    for speed in (100, 120, 150, 180):
        benchmark(path, speed)
    print(f'\n当前 library.PLAYBACK_SPEED = {PLAYBACK_SPEED}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
