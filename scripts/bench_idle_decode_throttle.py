# -*- coding: utf-8 -*-
"""实机 CPU 对比（批11 可选加分）：闲置节流前后 ffmpeg 解码 CPU 占用。

对比同一 webm 在 set_decode_throttle(1)（节流前：消费 24fps、全速解码）
与 set_decode_throttle(2)（闲置降帧节流：消费 12fps、reader 背压阻塞，
ffmpeg 解码 ≈半帧率）下，测量窗口内 ffmpeg 子进程的 CPU 占用（进程
user+kernel 时间差分 / 墙钟时间，Windows GetProcessTimes，不依赖 psutil）。

播放结束自动重播，保证整个测量窗口内持续解码（idle 长播场景）。

用法（headless 桌面环境需先设 QT_QPA_PLATFORM=offscreen）：
    python scripts/bench_idle_decode_throttle.py [--seconds 8]
    python scripts/bench_idle_decode_throttle.py --webm <path> --seconds 5

输出两行 CPU 占比（满核百分比），并给出节流后的下降比例。
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet.webm_clip import WebMClip  # noqa: E402

DEFAULT_WEBM = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")


def _cpu_seconds(proc) -> float | None:
    """ffmpeg 子进程已消耗的 CPU 秒（user+kernel，Windows GetProcessTimes）。"""
    if proc is None or proc.poll() is not None:
        return None
    creation = wintypes.FILETIME()
    exit_t = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    ok = ctypes.windll.kernel32.GetProcessTimes(
        proc._handle,
        ctypes.byref(creation), ctypes.byref(exit_t),
        ctypes.byref(kernel), ctypes.byref(user),
    )
    if not ok:
        return None

    def _to_secs(ft) -> float:
        return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7

    return _to_secs(kernel) + _to_secs(user)


def measure(webm: Path, divisor: int, seconds: float) -> float:
    """播放 webm seconds 秒（节流 divisor），返回 ffmpeg 平均 CPU 占比。

    播完自动重播（idle 长播场景）：reader/ffmpeg 进程每轮换代，CPU 采样
    跨进程累计（换进程时清零差值基准，不把旧进程的累计 CPU 重复计入）。
    """
    app = QApplication.instance() or QApplication([])
    clip = WebMClip(webm)
    clip._ensure_meta()
    assert clip.start() is True
    clip.set_decode_throttle(divisor)
    deadline = time.monotonic() + 5.0
    while clip._reader_proc is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if clip._reader_proc is None:
        clip.cleanup()
        raise SystemExit("reader 未拉起 ffmpeg 进程，无法测量")

    total_cpu = 0.0
    last_proc = None
    last_cpu = None
    t0 = time.monotonic()
    wall_end = t0 + seconds
    while time.monotonic() < wall_end:
        app.processEvents()
        proc_now = clip._reader_proc
        if proc_now is not None and proc_now.poll() is None:
            now_cpu = _cpu_seconds(proc_now)
            if now_cpu is not None:
                if proc_now is last_proc and last_cpu is not None:
                    total_cpu += max(0.0, now_cpu - last_cpu)
                last_proc = proc_now
                last_cpu = now_cpu
        if not clip._running:
            clip.start()  # 播完重播：保持整个窗口持续解码
        time.sleep(0.005)
    wall = time.monotonic() - t0
    clip.stop()
    clip.cleanup()
    app.processEvents()
    return total_cpu / wall


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webm", type=Path, default=DEFAULT_WEBM)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    if not args.webm.exists():
        raise SystemExit(f"webm 不存在: {args.webm}")
    if ctypes.windll is None:  # pragma: no cover - 非 Windows 用 psutil 需另行实现
        raise SystemExit("本脚本用 Windows GetProcessTimes 采样，仅支持 Windows")

    off = measure(args.webm, divisor=1, seconds=args.seconds)
    on = measure(args.webm, divisor=2, seconds=args.seconds)
    print(f"节流前 (divisor=1, 全速解码): ffmpeg CPU ≈ {off * 100:.1f}%")
    print(f"节流后 (divisor=2, 半帧率解码): ffmpeg CPU ≈ {on * 100:.1f}%")
    if off > 0:
        print(f"下降比例: {(1 - on / off) * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
